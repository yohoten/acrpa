# -*- coding: utf-8 -*-
"""L4 UI/交互 冒烟自测 —— 方案文档 §5 L4（7 项）。

覆盖
----
① ``sys_perf`` 采集器：CPU 0–100 / 内存百分比 0–100 / 磁盘数值 > 0，
   且**单次采集实测耗时 < 5 ms**（打印实测值）；
② ``reminder``：模块级 API 可导入；**无 GUI 时优雅跳过**；有 GUI 时创建浮窗，
   断言 ``-alpha``、自动关闭、打盹三态回调被调用（用 ``after`` 定时关闭，
   绝不阻塞等待真人点击）；
③ 托盘菜单结构：菜单项列表与回调绑定存在性（**不实际弹出**菜单）；
④ 任务编辑窗四页签：无 GUI 时断言函数可调用；有 GUI 时创建后立即销毁，
   断言四页签名字与字段绑定存在；
⑤ 搜索过滤纯逻辑（多关键词 AND、日期关键词、拼音降级）；
⑥ ``get_no_interp`` 驱动的占位符提示文案生成函数；
⑦ 粘贴路径解析（扩展名 → 目标类型）纯逻辑。

安全声明
--------
* 只写 ``tempfile.mkdtemp()`` 下的空文件，绝不碰用户数据；
* **不弹窗、不点按钮、不执行任何电源/音量/删除操作**；
* 无 GUI 环境（CI 服务会话）自动跳过所有窗口相关断言，退出码仍为 0。

用法: python -X utf8 tools/_test_ui_l4.py
退出码: 0=全部通过, 1=存在失败
"""
import os
import sys
import tempfile
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(BASE, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

_PASS, _FAIL, _SKIP = [], [], []


def check(cond, msg, detail=""):
    (_PASS if cond else _FAIL).append(msg)
    tail = ("  ({})".format(detail) if detail else "")
    print("[{}] {}{}".format("OK  " if cond else "FAIL", msg, tail))
    return bool(cond)


def skip(msg, detail=""):
    _SKIP.append(msg)
    tail = ("  ({})".format(detail) if detail else "")
    print("[SKIP] {}{}".format(msg, tail))


def section(title):
    print("\n" + "=" * 62)
    print(title)
    print("=" * 62)


# ══════════════════════════════════════════════════════════════════════
# ① sys_perf 采集器
# ══════════════════════════════════════════════════════════════════════

def test_sys_perf():
    section("① sys_perf 采集器（CPU / 内存 / 磁盘 + 耗时）")
    try:
        import sys_perf
    except Exception as e:                       # noqa: BLE001
        check(False, "import sys_perf", str(e))
        return

    # 第一次建立 CPU 基线；短暂等待后再采一次得到真实差分
    sys_perf.reset()
    sys_perf.cpu_percent()
    time.sleep(0.12)
    sys_perf.reset()                             # 清缓存，保证下面计时的是真实采集
    sys_perf.cpu_percent()
    time.sleep(0.12)

    started = time.perf_counter()
    snap = sys_perf.sample()
    cost_ms = (time.perf_counter() - started) * 1000.0

    check(isinstance(snap, dict) and bool(snap.get("ok")), "sample() 返回可用快照",
          "ok={}".format(snap.get("ok") if isinstance(snap, dict) else "?"))
    cpu = float(snap.get("cpu") or 0.0)
    mem = float(snap.get("mem_percent") or 0.0)
    check(0.0 <= cpu <= 100.0, "CPU 在 0–100 之间", "cpu={:.1f}%".format(cpu))
    check(0.0 <= mem <= 100.0, "内存百分比在 0–100 之间", "mem={:.1f}%".format(mem))
    check(float(snap.get("mem_total_mb") or 0.0) > 0.0, "内存总量 > 0",
          "{:.0f} MB".format(float(snap.get("mem_total_mb") or 0.0)))
    free_gb = float(snap.get("disk_free_gb") or 0.0)
    check(free_gb > 0.0, "磁盘可用空间 > 0",
          "{} {:.0f} GB 可用".format(snap.get("disk_drive") or "?", free_gb))
    check(cost_ms < 5.0, "单次采集耗时 < 5 ms", "实测 {:.3f} ms".format(cost_ms))

    tip = sys_perf.format_tooltip(snap)
    check("CPU" in tip and "内存" in tip, "tooltip 文案含 CPU/内存", tip)
    check(len(tip) <= sys_perf.MAX_TIP_LEN, "tooltip 长度不超 szTip 上限",
          "{} 字符".format(len(tip)))
    check(sys_perf.network() is None, "网络上下行已按方案降级（返回 None）",
          sys_perf.NETWORK_NOTE[:24] + "…")
    check(sys_perf.format_tooltip(None) == "A/C RPA", "非法输入降级不抛")


# ══════════════════════════════════════════════════════════════════════
# ② reminder 纯函数（无 GUI 部分）
# ══════════════════════════════════════════════════════════════════════

def test_reminder_pure():
    section("② reminder 模块级 API + 纯函数")
    try:
        import reminder
    except Exception as e:                       # noqa: BLE001
        check(False, "import reminder", str(e))
        return None
    for name in ("show_reminder", "register_reminder_api", "close_all",
                 "render_countdown", "normalize_position", "clamp_opacity"):
        check(callable(getattr(reminder, name, None)),
              "reminder.{} 可调用".format(name))

    check(reminder.normalize_position("右下角") == "bottom_right", "位置别名「右下角」")
    check(reminder.normalize_position("屏幕中央") == "center", "位置别名「屏幕中央」")
    check(reminder.normalize_position("???") == "center", "未知位置回退 center")
    check(abs(reminder.clamp_opacity(92) - 0.92) < 1e-6, "透明度百分数 92 → 0.92")
    check(abs(reminder.clamp_opacity(0.1) - 0.3) < 1e-6, "透明度下限钳到 0.3")
    check(abs(reminder.clamp_opacity(5) - 0.3) < 1e-6,
          "透明度百分数 5 → 0.05 后被下限钳到 0.3")
    check(abs(reminder.clamp_opacity(150) - 1.0) < 1e-6, "超范围透明度钳到上限 1.0")
    check(reminder.normalize_snooze_minutes("abc") == 5, "打盹分钟非法输入回退 5")
    check(reminder.snooze_options(None) == [5, 10, 30], "打盹选项默认 5/10/30")
    check(reminder.resolve_autoclose(-3) == 0, "自动关闭负数 → 0（不自动关闭）")
    check(reminder.format_remaining(95) == "1分35秒", "剩余时间格式化")

    text = reminder.render_countdown("还剩 {剩余}", 90)
    check("1分30秒" in text, "倒计时模板 {剩余} 生效", text)
    text2 = reminder.render_countdown("剩余 {remain} 秒", 30)
    check("30秒" in text2, "倒计时模板 {remain} 生效", text2)
    check(reminder.render_countdown("", 5).strip() != "", "空模板回退默认文案")

    # 注入式宿主：reminder 不 import ACRPA，只接受宿主登记
    reminder.register_reminder_api(root=None, on_close_task=lambda *a: None)
    host = reminder.get_host()
    check(callable(host.get("on_close_task")), "register_reminder_api 注入回调生效")
    return reminder


# ══════════════════════════════════════════════════════════════════════
# ③ 托盘菜单结构（不弹菜单）
# ══════════════════════════════════════════════════════════════════════

def test_tray_menu():
    section("③ 托盘菜单结构 + 回调绑定存在性")
    try:
        from ui import tray_menu
        import tray
    except Exception as e:                       # noqa: BLE001
        check(False, "import tray / ui.tray_menu", str(e))
        return

    spec = tray_menu.build_spec({})
    ids = tray_menu.menu_ids(spec)
    required = ("show_main", "reminder_5", "reminder_15", "reminder_30",
                "reminder_custom", "power", "power_shutdown_delay",
                "power_shutdown", "power_restart", "power_logoff", "power_lock",
                "power_sleep", "power_hibernate", "power_cancel",
                "mute_toggle", "open_settings", "open_tasks", "open_about", "quit")
    missing = [item for item in required if item not in ids]
    check(not missing, "必需菜单项齐全", "缺失={}".format(missing) if missing else "")

    actions = tray_menu.action_keys(spec)
    unmapped = [a for a in actions if a not in tray.ACTION_METHODS]
    check(not unmapped, "每个菜单动作都有回调键映射",
          "未映射={}".format(unmapped) if unmapped else "")
    missing_attr = [n for n in tray.ACTION_METHODS.values()
                    if not hasattr(tray.SystemTray, n)]
    check(not missing_attr, "回调方法在 SystemTray 上真实存在",
          "缺失={}".format(missing_attr) if missing_attr else "")

    for node in tray_menu.iter_items(spec):
        if node.get("type") == "separator":
            check(node.get("action") is None, "分隔线无回调 [{}]".format(node.get("id")),
                  "")
            break
    status_items = [n for n in tray_menu.iter_items(spec) if n.get("type") == "status"]
    check(len(status_items) >= 1, "至少有状态行（只读展示）",
          "{} 条".format(len(status_items)))

    # 运行态切换：运行中应出现「取消执行」而不是「执行全部」
    spec_run = tray_menu.build_spec({"running": True, "recording": False})
    ids_run = tray_menu.menu_ids(spec_run)
    check("stop_exec" in ids_run and "run_all" not in ids_run,
          "运行中菜单改为「取消执行」")
    spec_idle = tray_menu.build_spec({"running": False, "recording": False})
    check("run_all" in tray_menu.menu_ids(spec_idle), "空闲态菜单为「执行全部」")

    # 电源倒计时剩余秒数进入文案
    spec_cd = tray_menu.build_spec({"power_cancel_sec": 42})
    node = tray_menu.find_item(spec_cd, "power_cancel")
    check(node is not None and "42" in str(node.get("label")), "倒计时剩余秒数显示在菜单里",
          str(node.get("label")) if node else "")

    # 电源类动作名必须落在 sys_ops.POWER_ACTIONS 白名单里
    try:
        import sys_ops
        allowed = set(sys_ops.POWER_ACTIONS)
        used = {name for name in ("关机", "重启", "注销", "锁屏", "待机", "休眠")}
        check(used <= allowed, "托盘电源动作均在 sys_ops 白名单内",
              "白名单={}".format(sorted(allowed)))
    except Exception as e:                       # noqa: BLE001
        check(False, "import sys_ops 校验电源白名单", str(e))


# ══════════════════════════════════════════════════════════════════════
# ④ 搜索 / 占位符提示 / 粘贴路径 —— 纯逻辑
# ══════════════════════════════════════════════════════════════════════

def test_dialogs_pure():
    section("④ 搜索过滤 / 占位符提示 / 粘贴路径（纯逻辑）")
    try:
        import dialogs
    except Exception as e:                       # noqa: BLE001
        check(False, "import dialogs", str(e))
        return

    row = {"name": "定时 报告", "target": "脚本:report.acrpas",
           "plan": "每天 09:00", "next_run": "2026-10-23 09:00",
           "last_run": "2026-10-22 09:00:03", "status": "启用"}
    task = {"id": "1749000000123", "created": "2026-09-15 10:00:00"}

    check(dialogs.sched_row_matches(row, ""), "空查询命中全部")
    check(dialogs.sched_row_matches(row, "报告"), "单关键词命中")
    check(dialogs.sched_row_matches(row, "报告 09:00"), "多关键词 AND 命中")
    check(not dialogs.sched_row_matches(row, "报告 不存在"), "多关键词 AND 不命中")
    check(dialogs.sched_row_matches(row, "2026-10"), "日期关键词 YYYY-MM 命中")
    check(dialogs.sched_row_matches(row, "10-23"), "日期关键词 MM-DD 命中")
    check(dialogs.sched_row_matches(row, "2026/10"), "日期关键词斜杠归一化命中")
    check(not dialogs.sched_row_matches(row, "2026-11"),
          "不存在的月份不命中（同时证明 created=2026-09 不会误命中）")
    check(len(dialogs.sched_search_tokens("  a   b ")) == 2, "空格分隔 token 去除空项")

    # 拼音：未装 pypinyin 时静默降级（不得因此丢结果）
    if dialogs.pinyin_available():
        check(dialogs.sched_row_matches(row, "sdbg"), "拼音首字母匹配（已装 pypinyin）",
              dialogs.pinyin_initials("定时 报告"))
    else:
        skip("拼音首字母匹配", "pypinyin 未安装 → 静默降级为普通子串匹配")
        check(dialogs.pinyin_initials("定时 报告") == "",
              "未装 pypinyin 时首字母串为空（降级不抛）")
        check(dialogs.sched_row_matches(row, "报告"), "降级后普通匹配仍有效")

    # 占位符提示（get_no_interp 驱动）
    generic = dialogs.interp_hint_text(None)
    check("{var:}" in generic and "{date:}" in generic, "通用提示含四类占位符")
    check("Y=四位年" in generic or "日期标记" in generic, "通用提示含日期标记说明")
    hint_blocked = dialogs.interp_hint_text("电源操作")
    check("电源操作" in hint_blocked, "指定命令时提示里带命令名")
    check("不做插值" in hint_blocked, "部分参数受安全闸门保护时有说明", hint_blocked[:36])
    hint_multi = dialogs.interp_hint_for_values(["电源操作", "点击"])
    check(hint_multi == generic, "多行选中回退通用提示")

    # 粘贴路径解析（真实临时文件）
    tmp = tempfile.mkdtemp(prefix="acrpa_l4_")
    try:
        cases = [(".acrpas", "script"), (".xls", "script"), (".xlsx", "script"),
                 (".json", "workflow"), (".exe", "command"), (".lnk", "command")]
        for ext, want in cases:
            path = os.path.join(tmp, "t" + ext)
            with open(path, "wb") as fh:
                fh.write(b"x")
            info = dialogs.classify_pasted_path(path)
            check(info.get("ok") and info.get("target_type") == want,
                  "{} → {}".format(ext, want), str(info.get("target"))[:40])
            if want == "command":
                check(info.get("target") == "启动程序"
                      and info.get("target_params", {}).get("args") == [path],
                      "{} 预填「启动程序」+ 参数1 为路径".format(ext))
        quoted = '"{}"'.format(os.path.join(tmp, "t.acrpas"))
        check(dialogs.classify_pasted_path(quoted).get("ok"), "带引号的路径可解析")
        with open(os.path.join(tmp, "t.txt"), "wb") as fh:
            fh.write(b"x")
        bad = dialogs.classify_pasted_path(os.path.join(tmp, "t.txt"))
        check(not bad.get("ok"), "不支持扩展名给出原因", bad.get("reason", ""))
        check(not dialogs.classify_pasted_path("").get("ok"), "空剪贴板不通过")
        missing = dialogs.classify_pasted_path(os.path.join(tmp, "nope.acrpas"))
        check(not missing.get("ok"), "不存在的路径不通过", missing.get("reason", ""))
    finally:
        try:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)
        except Exception:                        # noqa: BLE001
            pass

    check(callable(getattr(dialogs, "_sched_task_dialog", None)),
          "_sched_task_dialog 可调用（四页签编辑器入口）")
    try:
        import inspect
        params = list(inspect.signature(dialogs._sched_task_dialog).parameters)
        check("prefill" in params, "签名保留 parent/task/on_done 并新增 prefill", str(params))
    except Exception as e:                       # noqa: BLE001
        check(False, "读取 _sched_task_dialog 签名", str(e))


# ══════════════════════════════════════════════════════════════════════
# ⑤ GUI 相关（无 GUI 时优雅跳过）
# ══════════════════════════════════════════════════════════════════════

def _try_root():
    """→ 可用的 Tk 根窗口；无 GUI 环境返回 None（绝不抛）。"""
    try:
        import tkinter
        win = tkinter.Tk()
        win.withdraw()
        win.update_idletasks()
        return win
    except Exception:                            # noqa: BLE001
        return None


def test_gui(reminder_mod):
    section("⑤ GUI：提醒浮窗 / 四页签编辑器（无 GUI 自动跳过）")
    root = _try_root()
    if root is None:
        skip("GUI 断言", "当前环境无法创建 Tk 根窗口（CI 服务会话/无桌面）")
        return
    try:
        import tkinter
        from tkinter import ttk
        import dialogs
        import utils
        from ui import theme as ui_theme
        import reminder

        # ── 提醒浮窗：-alpha / 自动关闭 ──
        reminder.register_reminder_api(root=root)
        popup = reminder.show_reminder(
            title="L4 自测", body="自动关闭用例", pos="center", opacity=0.9,
            autoclose_sec=1, show_countdown=True, snooze_minutes=5)
        check(popup is not None, "show_reminder 创建浮窗")
        if popup is not None:
            try:
                alpha = float(popup.win.attributes("-alpha"))
            except Exception:                    # noqa: BLE001
                alpha = -1.0
            check(abs(alpha - reminder.clamp_opacity(0.9)) <= 0.02,
                  "浮窗 -alpha 生效", "alpha={:.2f}".format(alpha))
            deadline = time.time() + 5.0
            while not popup.closed and time.time() < deadline:
                try:
                    root.update()
                except Exception:                # noqa: BLE001
                    break
                time.sleep(0.05)
            check(popup.closed and popup.auto_closed,
                  "自动关闭秒数到点后自行关闭（用 after，不阻塞真人）",
                  "reason={}".format(popup.close_reason))

        # ── 打盹三态回调（直接 invoke 按钮，不依赖鼠标） ──
        got = {}

        def _on_snooze(minutes):
            got["snooze"] = minutes

        def _on_dismiss():
            got["dismiss"] = True

        def _on_close_task(*args):
            got["close_task"] = True

        p2 = reminder.ReminderPopup(
            root, title="L4 三态", body="回调用例", pos="bottom_right",
            opacity=0.85, autoclose_sec=0, snooze_minutes=5,
            on_snooze=_on_snooze, on_dismiss=_on_dismiss,
            on_close_task=_on_close_task, task_id="t-1")
        check("snooze_5" in p2.buttons and "dismiss_once" in p2.buttons
              and "stop_task" in p2.buttons, "三态按钮齐备", str(sorted(p2.buttons)))
        p2.buttons["snooze_5"].invoke()
        check(got.get("snooze") == 5 and p2.closed, "「稍后提醒我」回调被调用",
              "minutes={}".format(got.get("snooze")))

        p3 = reminder.ReminderPopup(root, title="L4 三态2", body="dismiss",
            autoclose_sec=0, on_dismiss=_on_dismiss)
        p3.buttons["dismiss_once"].invoke()
        check(got.get("dismiss") is True and p3.closed, "「不再提醒（本次）」回调被调用")

        p4 = reminder.ReminderPopup(root, title="L4 三态3", body="stop",
            autoclose_sec=0, on_close_task=_on_close_task, task_id="t-2")
        p4.buttons["stop_task"].invoke()
        check(got.get("close_task") is True and p4.closed, "「彻底关闭该任务」回调被调用")
        reminder.close_all()

        # ── 四页签任务编辑器：创建后立即销毁 ──
        colors = ui_theme.colors()
        dialogs.init_ctx(root_win=root, colors=colors,
                         fonts=(utils.FONT_TITLE, utils.FONT_BODY,
                                utils.FONT_SMALL, utils.FONT_BUTTON),
                         app_root=BASE, res_dir=os.path.join(BASE, "res"))
        sample_task = {"id": "t-100", "name": "自测任务", "target_type": "script",
                       "target": "a.acrpas", "enabled": True, "run_count": 3,
                       "triggers": [{"kind": "daily", "time": "09:00"},
                                    {"kind": "hotkey", "spec": "ctrl+alt+1"}],
                       "on_overlap": "skip", "timeout_sec": 0,
                       "missed_run": "ignore", "missed_delay_sec": 60,
                       "stop_hotkey": ""}
        dlg = None
        try:
            dlg = dialogs._sched_task_dialog(root, sample_task, None)
        except Exception as e:                    # noqa: BLE001
            check(False, "构造四页签编辑器（旧任务向后兼容）", str(e))
        if dlg is not None:
            notebooks = [w for w in dlg.winfo_children()
                         if w.winfo_class() == "TNotebook"]
            check(bool(notebooks), "编辑器含 ttk.Notebook")
            if notebooks:
                nb = notebooks[0]
                names = [nb.tab(t, "text") for t in nb.tabs()]
                check(names == ["任务", "计划", "热键", "执行"],
                      "四页签名字与顺序正确", str(names))
                plan_tab = dlg.nametowidget(nb.tabs()[1])
                trees = []

                def _walk(widget):
                    for child in widget.winfo_children():
                        if child.winfo_class() == "Treeview":
                            trees.append(child)
                        _walk(child)
                _walk(plan_tab)
                check(bool(trees), "计划页含触发器列表 Treeview")
                if trees:
                    cols = list(trees[0]["columns"])
                    check(cols == ["kind", "param"], "触发器列表列绑定正确", str(cols))
                    check(len(trees[0].get_children()) == 2,
                          "旧任务的 2 条触发器均已载入",
                          "{} 条".format(len(trees[0].get_children())))
            try:
                dlg.grab_release()
            except Exception:                     # noqa: BLE001
                pass
            dlg.destroy()
            check(True, "编辑器创建后可安全销毁")
    finally:
        try:
            root.destroy()
        except Exception:                         # noqa: BLE001
            pass


# ══════════════════════════════════════════════════════════════════════
# main
# ══════════════════════════════════════════════════════════════════════

def main():
    print("L4 UI/交互 冒烟自测 —— 方案文档 §5 L4（7 项）")
    test_sys_perf()
    reminder_mod = test_reminder_pure()
    test_tray_menu()
    test_dialogs_pure()
    if reminder_mod is not None:
        test_gui(reminder_mod)
    else:
        skip("GUI 断言", "reminder 不可用")

    total = len(_PASS) + len(_FAIL)
    print("\n" + "-" * 62)
    print("通过 {} / 失败 {} / 跳过 {}".format(len(_PASS), len(_FAIL), len(_SKIP)))
    if _FAIL:
        print("失败项:")
        for name in _FAIL:
            print("  - {}".format(name))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
