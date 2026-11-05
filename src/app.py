"""
ACRPA 薄入口: 构建应用并启动主循环。

设计要点 (路线图 §3.1 / 阶段二第 1 项):
  - `import ACRPA` 必须零副作用 (不建窗 / 不 after / 不起线程 / 不 makedirs / 不 load_config)。
    ACRPA.py 已退化为纯定义模块, 真正的构建收敛在 `ACRPA.build_app()` 内。
  - 本模块是唯一的「构建 + 启动」入口:
        run.py (打包入口)  ->  app.main()
        测试 / 工具         ->  import app; rt = app.build(); rt.root...
  - 因此测试可以在不创建任何 Tk 窗口的前提下 `import ACRPA` 覆盖 engine/commands/scriptdata。

L5 追加 (方案文档 §5 L5 #1/#3/#6/#9):
  - `build()` 在 ACRPA.build_app() 前后埋「结构化启动日志」的探针 (config /
    ui_build / log_ready / theme / tray / commands / scheduler / capabilities);
  - 构建完成后安装单实例 IPC 接收窗口 (`single_instance.install_receiver`)，
    并把 `--exec-task` 等启动前登记的动作补执行 (`dispatch_pending`);
  - 安装危险动作确认钩子 (`engine.set_danger_confirm_hook`) —— 只用一层
    Toplevel + 超时，绝不引入新依赖;
  - 包装 `scheduler._add_task_log` 旁路写入运行历史 (`history.install_scheduler_hook`)。

用法:
    # 启动完整应用 (等价于旧 `from ACRPA import root; root.mainloop()`)
    import app
    app.main()

    # 只为测试/内省构建一次 GUI, 拿到 root 后自行销毁
    import app
    rt = app.build()
    rt.root.after(500, rt.root.destroy)
    rt.root.mainloop()
"""
import os
import sys
import threading

# ── 保证 `src/` 与项目根都在 sys.path 中 (与 run.py 的策略保持一致) ──
_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC_DIR = os.path.join(_HERE, "src")
if os.path.isdir(_SRC_DIR):
    # app.py 位于项目根
    _PROJECT_ROOT = _HERE
else:
    # app.py 位于 src/ 内 (当前布局)
    _SRC_DIR = _HERE
    _PROJECT_ROOT = os.path.dirname(_HERE)
for _p in (_SRC_DIR, _PROJECT_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# 纯定义模块: 此 import 不产生任何副作用
import ACRPA


# ═══════════════════════════════════════════════════════════════════════
# L5 #3: 结构化启动日志 —— 在既有函数的边界埋探针 (不改 ACRPA.build_app)
# ═══════════════════════════════════════════════════════════════════════

def _mark(stage, **detail):
    """→ crashlog.startup_mark 安全代理。"""
    try:
        import crashlog
        return crashlog.startup_mark(stage, **detail)
    except Exception:
        return ""


def _wrap_once(mod, attr, stage, detail_fn=None):
    """把 ``mod.attr`` 包一层「首次调用后记一条启动阶段」。幂等, 失败静默。

    只在**首次**调用时打点: 探针是永久 monkey-patch, 而 apply_theme /
    load_config 等在运行期会被再次调用 (换肤/重载配置), 若每次都打点,
    cost 会变成「距上一次打点的间隔」(分钟~小时级), 产生
    `stage=theme cost=939771ms` 这类误导性启动日志。
    """
    try:
        original = getattr(mod, attr, None)
        if original is None or getattr(original, "_acrpa_boot_probe", False):
            return False
        marked = [False]

        def _probe(*a, **kw):
            result = original(*a, **kw)
            if not marked[0]:
                marked[0] = True
                _mark(stage, **(detail_fn(result) if detail_fn else {}))
            return result

        _probe._acrpa_boot_probe = True
        _probe._acrpa_original = original
        setattr(mod, attr, _probe)
        return True
    except Exception:
        return False


def _install_boot_probes():
    """在 build_app 之前埋探针: 配置加载 / 日志就绪 / 主题 / 托盘。"""
    try:
        import state
        _wrap_once(state, "load_config", "config")
    except Exception:
        pass
    try:
        import utils
        _wrap_once(utils, "set_tlog", "log_ready")
    except Exception:
        pass
    try:
        from ui import theme as _theme
        _wrap_once(_theme, "apply_theme", "theme")
    except Exception:
        pass
    try:
        from tray import SystemTray
        _wrap_once(SystemTray, "create", "tray")
    except Exception:
        pass


def _mark_post_build():
    """构建完成后补记: 命令注册表 / 调度器 / 能力探测。"""
    try:
        import commands
        _mark("commands", total=len(commands.list_names()))
    except Exception:
        _mark("commands", total=-1)
    try:
        import scheduler
        active = bool(scheduler.is_multi_active()) if hasattr(
            scheduler, "is_multi_active") else False
        _mark("scheduler", active=int(active))
    except Exception:
        _mark("scheduler", active=-1)
    try:
        import capabilities
        total = -1
        fn = getattr(capabilities, "all_states", None)
        if callable(fn):                      # all_states(): {cap_id: CapState}
            try:
                data = fn()
                total = len(data) if isinstance(data, dict) else -1
            except Exception:
                total = -1
        _mark("capabilities", total=total)
    except Exception:
        _mark("capabilities", total=-1)


# ═══════════════════════════════════════════════════════════════════════
# L5 #6: 危险动作确认钩子 (engine 侧在调用 handler 前询问)
# ═══════════════════════════════════════════════════════════════════════

def _show_danger_dialog(root, cmd, detail, timeout):
    """在主线程弹一个带超时的确认框 → True/False。"""
    import tkinter
    from tkinter import ttk

    result = {"ok": False}
    try:
        dlg = tkinter.Toplevel(root)
        dlg.title("危险操作确认")
        dlg.transient(root)
        dlg.resizable(False, False)
        try:
            dlg.attributes("-topmost", True)
        except Exception:
            pass
        # 取主窗口当前主题色板 (暗色下不再弹系统浅色框)
        C = getattr(ACRPA, "C", {}) or {}
        _bgc = C.get("bgc", "#ffffff")
        _fgb = C.get("fgb", "#000000")
        _fgm = C.get("fgm", "#666666")
        dlg.configure(bg=_bgc)
        body = tkinter.Frame(dlg, padx=16, pady=12, bg=_bgc)
        body.pack(fill="both", expand=True)
        tkinter.Label(body, text="即将执行高危命令：{}".format(cmd),
                      font=getattr(utils, "FONT_TITLE", "ACRPA_TITLE"),
                      bg=_bgc, fg=_fgb, anchor="w").pack(fill="x")
        tkinter.Label(body, text=str(detail or "")[:400], justify="left",
                      wraplength=420, anchor="w",
                      bg=_bgc, fg=_fgb).pack(fill="x", pady=(6, 4))
        # 警示文字: 仅前景色随主题 (wn), 底色仍随容器
        _warn_lbl = tkinter.Label(body, text="{} 秒内未确认将视为拒绝".format(int(timeout)),
                      fg=C.get("wn", "#b45309"), bg=_bgc, anchor="w")
        try:
            from ui import theme as _ui_theme
            _ui_theme.set_fg_token(_warn_lbl, "wn")
        except Exception:
            pass
        _warn_lbl.pack(fill="x", pady=(0, 8))
        btns = tkinter.Frame(body, bg=_bgc)
        btns.pack(fill="x")

        def _done(ok):
            result["ok"] = bool(ok)
            try:
                dlg.grab_release()
            except Exception:
                pass
            try:
                dlg.destroy()
            except Exception:
                pass

        ttk.Button(btns, text="拒绝", command=lambda: _done(False)).pack(side="right", padx=(6, 0))
        ttk.Button(btns, text="允许执行", command=lambda: _done(True)).pack(side="right")
        try:
            dlg.protocol("WM_DELETE_WINDOW", lambda: _done(False))
            dlg.grab_set()
        except Exception:
            pass
        try:
            dlg.after(max(1, int(timeout)) * 1000, lambda: _done(False))
        except Exception:
            pass
        dlg.update_idletasks()
        try:
            root.wait_window(dlg)
        except Exception:
            pass
    except Exception:
        return False
    return bool(result["ok"])


def _ask_danger(cmd, detail, timeout=15.0):
    """确认回调: 主线程直接弹窗; 后台线程经 ``root.after`` 回流后等待。"""
    try:
        import tkinter
        root = getattr(tkinter, "_default_root", None)
    except Exception:
        root = None
    if root is None:
        return False
    if threading.current_thread() is threading.main_thread():
        return _show_danger_dialog(root, cmd, detail, timeout)
    box = {"ok": False}
    done = threading.Event()

    def _fire():
        try:
            box["ok"] = _show_danger_dialog(root, cmd, detail, timeout)
        finally:
            done.set()

    try:
        root.after(0, _fire)
    except Exception:
        return False
    done.wait(max(1.0, float(timeout)) + 5.0)
    return bool(box["ok"])


def install_danger_confirm_hook():
    """把确认回调注入 engine（无 GUI 时 engine 会直接拒绝，不静默放行）。"""
    try:
        import engine
        fn = getattr(engine, "set_danger_confirm_hook", None)
        if callable(fn):
            fn(_ask_danger)
            return True
    except Exception:
        pass
    return False


# ═══════════════════════════════════════════════════════════════════════
# 构建 / 启动
# ═══════════════════════════════════════════════════════════════════════

def build():
    """构建应用: 创建 root、装配全部 GUI 控件与依赖。

    所有原先挂在 ACRPA 模块级的副作用 (建窗 / 路径 / 主题 / 绑定 / 定时器 /
    NetLink 启动等) 都收敛在 `ACRPA.build_app()` 中, 这里显式触发一次。

    返回 ACRPA 模块本身 (也可经模块级名 `app.ACRPA` 访问), 便于:
        rt = app.build(); rt.root.destroy()
    """
    _install_boot_probes()
    _mark("ui_build")
    ACRPA.build_app()
    # v3 §4.4: 构建结束单独埋点, 使「骨架装配」与「首帧/可交互」可分别度量
    _mark("ui_build_done")
    _finish_build(ACRPA)
    return ACRPA


def _register_app_context(rt):
    """把构建产物的核心服务登记到 AppContext 单例 (方案文档 S2)。

    纯增量: 只写容器, 不改任何既有读取路径; 各视图后续经 deps 注入 ctx
    后再逐步切换。失败一律静默降级 (不影响已建好的窗口)。
    """
    try:
        from core.app_context import ctx
        root = getattr(rt, "root", None)
        ctx.register("root", root)
        ctx.register("acrpa", rt)
        try:
            import state as _st
            ctx.register("state", _st)
        except Exception:
            pass
        colors = getattr(rt, "C", None)
        if colors is not None:
            ctx.register("colors", colors)
    except Exception:
        pass


def _finish_build(rt):
    """构建完成后的 L5 接线（全部失败可降级，不影响已建好的窗口）。"""
    _mark_post_build()
    root = getattr(rt, "root", None)
    # ④ AppContext 初始化快照: 登记 root/state/acrpa/colors (S2 增量, 无读取方)
    _register_app_context(rt)
    # ① Tk 回调异常留痕: 按钮/事件处理中的异常不再只打 stderr (打包版无控制台)
    try:
        import crashlog
        crashlog.install_tk_hook(root)
    except Exception:
        pass
    # ① 单实例 IPC 接收窗口 + 启动前登记的动作
    hwnd = 0
    try:
        import single_instance
        hwnd = single_instance.install_receiver(root)
        _mark("ipc", hwnd=1 if hwnd else 0)
    except Exception:
        pass
    try:
        import single_instance
        single_instance.dispatch_pending(root)
    except Exception:
        pass
    # ② 危险动作确认钩子
    install_danger_confirm_hook()
    # ③ 运行历史: 旁路监听调度器任务完成
    try:
        import history
        history.install_scheduler_hook()
    except Exception:
        pass


def main():
    """构建应用并进入 Tk 主循环 (run.py 打包入口调用)。"""
    build()
    # v3 §4.4: 首帧 / 可交互两个标记, 用于「启动性能」前后对比
    # (ui_first_frame = 第一次 idle 完成; ui_ready = 主循环已能处理输入)
    try:
        root = ACRPA.root
        root.after_idle(lambda: _mark("ui_first_frame"))
        root.after(120, lambda: _mark("ui_ready"))
    except Exception:
        pass
    _mark("main_loop")
    ACRPA.root.mainloop()


if __name__ == "__main__":
    main()
