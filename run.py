"""ACRPA 进程入口 (方案文档 §5 L5 #1/#2/#3/#4/#6/#7)。

本文件只做「判决 + 分流」，业务全部落在已有模块:

    run.py
      ├── 崩溃留痕 (crashlog.install)                     §L5 #4
      ├── 结构化启动日志 (crashlog.startup_mark)          §L5 #3
      ├── --rollback            → updater.rollback()      §L5 #4
      ├── --script / --workflow → cli.main()  (无头)      §L5 #7
      ├── --exec-task <id>      → single_instance IPC     §L5 #6
      └── 其余 (双击启动)        → 单实例判定 → app.main() §L5 #1

三处必须保持的既有契约 (被 tools/_test_cli_headless.py 与打包校验依赖):
  · 保留 ``import cli`` / ``cli.parse_args(`` / ``cli.main(`` 文本;
  · 保留 ``app.main()`` 的 GUI 启动路径;
  · 退出码语义 0/1/2 不变 (2 只会出现在用法错误上)。
"""
import os, sys
# 确保项目根目录在 path 中
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))


def cleanup_and_exit(exit_code=0):
    
    try:
        import state
        
        # 设置关闭标志
        state._closing = True
        state.quit2 = True
        state.pause_event.set()

        # L4 遗留: 退出路径显式关掉提醒浮窗 (避免残留置顶窗口)
        try:
            import reminder
            reminder.close_all()
        except Exception:
            pass

        # L5: 收掉单实例 IPC 隐藏窗口 (恢复原窗口过程)
        try:
            import single_instance
            single_instance.uninstall_receiver()
        except Exception:
            pass

        # 停止调度器（如果运行中）
        try:
            import scheduler as sched
            sched.stop_scheduler()
        except Exception:
            pass
        
        # 保存配置
        try:
            state.save_config()
        except Exception:
            pass
        
        # 强制刷新日志到磁盘
        try:
            from utils import _tlog
            if _tlog is not None and hasattr(_tlog, 'force_flush'):
                _tlog.force_flush()
        except Exception:
            pass
            
    except Exception as e:
        print(f"清理过程出错: {e}")
    
    sys.exit(exit_code)


def _mark(stage, **detail):
    """→ crashlog.startup_mark 的安全代理（日志模块不可用时不阻断启动）。"""
    try:
        import crashlog
        return crashlog.startup_mark(stage, **detail)
    except Exception:
        return ""


def _prepare_environment():
    """装配启动环境: 便携迁移 + 崩溃留痕 (必须早于任何重活)。"""
    try:
        import state
        moved = state.migrate_portable_data()
        if moved:
            print("[ACRPA] 便携模式: 已把 {} 个旧数据文件复制到 userdata/".format(moved))
    except Exception:
        pass
    try:
        import crashlog
        path = crashlog.install()
        _mark("entry", argv=len(sys.argv) - 1, portable=int(_is_portable()))
        return path
    except Exception:
        _mark("entry", argv=len(sys.argv) - 1)
        return ""


def _is_portable():
    try:
        import state
        return bool(state.is_portable())
    except Exception:
        return False


def _handle_exec_task(task_id):
    """``--exec-task <id>`` → 退出码 (None 表示「无实例，接着启动 GUI 再执行」)。

    语义对齐 zTasker ``-exec:<ID>``:
      · 已有实例 → 通过 ``WM_COPYDATA`` 转发，**不新开进程**，本进程退出码 0；
      · 无实例   → 抢到单实例锁后启动 GUI，并在主窗就绪后执行该任务一次。
    """
    try:
        import single_instance as si
    except Exception as e:
        print("[ACRPA] 单实例模块不可用: {}".format(e))
        return 2

    task_id = str(task_id or "").strip()
    if not task_id:
        print("[ACRPA] 用法错误: --exec-task 需要任务 ID")
        return 2

    def _forward():
        try:
            payload = si.build_payload(si.ACTION_EXEC_TASK, {"task_id": task_id})
        except Exception as e:
            return si.RC_REJECTED, str(e)
        return si.send_payload(payload)

    if si.find_receiver():
        rc, detail = _forward()
        _mark("single_instance", mode="forward", rc=rc)
        if rc == si.RC_OK:
            print("[ACRPA] 已把任务 {} 转交给运行中的实例执行".format(task_id))
            return 0
        if rc == si.RC_NOT_FOUND:
            print("[ACRPA] 任务不存在: {}".format(task_id))
            return 1
        if rc == si.RC_REJECTED:
            print("[ACRPA] 转发被拒绝 (载荷校验失败): {}".format(detail))
            return 2
        print("[ACRPA] 转发失败: {} → 改为启动新实例执行".format(detail))

    # 无实例 (或转发失败): 抢锁 → 登记待执行 → 走 GUI 启动
    if not si.acquire():
        rc, detail = _forward()          # 竞态: 刚刚有人抢先启动
        _mark("single_instance", mode="forward-retry", rc=rc)
        if rc == si.RC_OK:
            return 0
        if rc == si.RC_NOT_FOUND:
            print("[ACRPA] 任务不存在: {}".format(task_id))
            return 1
        return 2
    si.set_pending(si.ACTION_EXEC_TASK, {"task_id": task_id})
    _mark("single_instance", mode="primary-pending")
    return None


def _start_gui():
    """构建并进入 Tk 主循环 (既有行为不变)。"""
    _mark("main_loop")
    try:
        import app
        app.main()
    except KeyboardInterrupt:
        print("\n程序已退出")
        cleanup_and_exit(0)
    except Exception as e:
        print(f"\n程序异常: {e}")
        import traceback
        traceback.print_exc()
        cleanup_and_exit(1)


if __name__ == "__main__":
    _prepare_environment()

    missing = []
    for mod in ["pyautogui", "xlrd", "pyperclip", "PIL"]:
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)

    if missing:
        print("缺少依赖库,请运行:")
        print("  pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple")
        print(f"\n缺失: {', '.join(missing)}")
        cleanup_and_exit(1)

    # ── CLI / 无头模式 (路线图 §6.5, L5 #7 扩充) ──────────────────────
    # 给出 --script / --workflow / --rollback 时由 cli 接管 (不建任何 Tk 窗口、
    # 跑完即退出)；--exec-task 需要 GUI + 单实例 IPC，先在此单独处理。
    # 未给任何动作开关、或 cli 解析/导入失败 → 一律落回下面的 GUI 路径，
    # 即现有行为完全不变。
    #
    # 取舍 (L5 #1 要求说明): **无头 CLI 不被单实例互斥阻塞** ——
    # `--script` / `--workflow` 是纯计算型任务 (CI/cron 常要求并发多份)，
    # 若被互斥挡住会让自动化链路互相排队甚至失败；而 GUI 与调度器共享
    # state.running 单例，才必须单实例。故: 无头路径不抢锁，可与 GUI 共存，
    # 并在日志里明确记录「headless」模式，便于事后分辨是谁跑了什么。
    argv = sys.argv[1:]
    try:
        import cli
        _opts = cli.parse_args(argv)
        if _opts is not None:
            if _opts.get("script") is None and _opts.get("exec_task"):
                _rc = _handle_exec_task(_opts.get("exec_task"))
                if _rc is not None:
                    sys.exit(_rc)
                # 走到这里 = 无实例，已登记待执行 → 继续启动 GUI
            else:
                _mark("single_instance", mode="headless")
                sys.exit(cli.main(argv))
    except SystemExit:
        raise          # cli 的正常退出码原样向上抛
    except Exception:
        pass           # cli 不可用/解析异常 → 静默回退 GUI

    # ── GUI 路径: 单实例互斥 (方案文档 §5 L5 #1) ──────────────────────
    try:
        import single_instance as si
        if not si.acquire():
            rc, detail = si.notify_restore()
            _mark("single_instance", mode="secondary", rc=rc)
            if rc == si.RC_OK:
                print("[ACRPA] 已在运行，已唤起已有窗口")
            else:
                print("[ACRPA] 已在运行（唤起失败: {}）".format(detail))
            sys.exit(0)
        _mark("single_instance", mode="primary")
    except SystemExit:
        raise
    except Exception:
        pass           # 单实例不可用 → 按单实例失效的旧行为继续启动

    _start_gui()
