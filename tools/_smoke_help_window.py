# -*- coding: utf-8 -*-
"""_smoke_help_window.py — 帮助系统渲染层冒烟（阶段 1-2）。

覆盖（[OK] / [WARN] / [FAIL]，退出码 0 成功 / 1 失败）：
  A1  窗口可开（Toplevel 建窗不抛异常）
  A2  章节齐全（≥13 且各章正文非空）
  A3  命令数一致（渲染命令集合 == commands.list_all() 名称集合）
  A4  分组全覆盖（每条命令均归属某分组）
  A5  搜索命中（关键字返回预期章节/命令）
  A6  主题跟随（refresh_theme 不报错且控件配色变化）
  A7  缩放跟随（utils.scaled 生效，渲染层同源）
  A8  几何记忆 + 越界回退（复用 utils.geometry_in_screen）
  A9  离线可用（不触发外部网络；外链打开被 monkeypatch 拦截）
  A11 向后兼容（dialogs.show_help_dialog() 委托成功）

用法：
    .venv\\Scripts\\python.exe -X utf8 tools\\_smoke_help_window.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
for _p in (ROOT, SRC):
    if _p not in sys.path:
        sys.path.insert(0, _p)

OUTDIR = os.path.join(ROOT, "tools", "_smoke_result_phase2")
os.makedirs(OUTDIR, exist_ok=True)
REPORT = os.path.join(OUTDIR, "_smoke_help_window.out")

_lines = []
FAILS = 0
WARNS = 0


def emit(msg=""):
    print(msg)
    _lines.append(msg)


def _ok(msg):
    emit("[OK]   " + msg)


def _warn(msg):
    global WARNS
    WARNS += 1
    emit("[WARN] " + msg)


def _fail(msg):
    global FAILS
    FAILS += 1
    emit("[FAIL] " + msg)


def check(cond, ok_msg, fail_msg):
    if cond:
        _ok(ok_msg)
    else:
        _fail(fail_msg)
    return bool(cond)


def main():
    emit("=== 帮助系统渲染层冒烟 (_smoke_help_window.py) ===")
    try:
        import tkinter
        import state
        import utils
        import commands
        import help_window
        import help_content
    except Exception as e:
        emit("[FAIL] 关键模块导入失败: {!r}".format(e))
        _write_report()
        return 1

    # 阻止 smoke 写真实 config.json
    _orig_save = state.save_config
    state.save_config = lambda *a, **k: None

    try:
        root = tkinter.Tk()
    except Exception as e:
        emit("[FAIL] 无法创建 Tk root: {!r}".format(e))
        state.save_config = _orig_save
        _write_report()
        return 1
    root.withdraw()
    try:
        utils.init_fonts(root)
    except Exception as e:
        _warn("utils.init_fonts 失败(命名字体可能缺省): {!r}".format(e))

    lightC = dict(utils.C)

    # ── A1 窗口可开 ──
    win = None
    inst = None
    try:
        win = help_window.open_help_window(root, lightC)
        inst = help_window.get_instance()
        root.update_idletasks()
        root.update()
        check(win is not None and help_window.is_open() and inst is not None
              and inst.alive(),
              "A1 帮助窗口可开（Toplevel 存活）",
              "A1 帮助窗口未成功创建: win={!r} inst={!r}".format(win, inst))
    except Exception as e:
        _fail("A1 打开帮助窗口抛异常: {!r}".format(e))

    if inst is None:
        emit("A1 未获得实例, 终止后续断言")
        _cleanup(root, state, _orig_save)
        _write_report()
        return 1

    # ── A2 章节齐全 ──
    try:
        secs = inst._sections
        if inst._fallback_only:
            _warn("A2 内容层降级为仅命令速查（未加载 res/help）")
        n = len(secs)
        check(n >= 13, "A2 章节数 {} (>=13)".format(n),
              "A2 章节数不足: {} (<13)".format(n))
        empty = [s.id for s in secs if not (s.body or "").strip()] \
            if not inst._fallback_only else []
        check(not empty, "A2 全部章节正文非空",
              "A2 正文为空的章节: {}".format(empty))
    except Exception as e:
        _fail("A2 章节校验异常: {!r}".format(e))

    # ── A3 命令数一致 ──
    try:
        registry = set(commands.list_names())
        rendered = set(e.name for e in inst._cmd_entries)
        check(registry == rendered,
              "A3 命令集合一致（注册表 {} == 渲染 {}）".format(
                  len(registry), len(rendered)),
              "A3 命令集合不一致: 缺={} 多={}".format(
                  sorted(registry - rendered), sorted(rendered - registry)))
    except Exception as e:
        _fail("A3 命令一致性校验异常: {!r}".format(e))

    # ── A4 分组全覆盖 ──
    try:
        grouped = set()
        for _t, es in inst._command_groups:
            for e in es:
                grouped.add(e.name)
        registry = set(commands.list_names())
        missing = registry - grouped
        check(not missing, "A4 分组全覆盖（{} 条命令均归属某分组）".format(
                  len(grouped)),
              "A4 未归属任何分组的命令: {}".format(sorted(missing)))
    except Exception as e:
        _fail("A4 分组覆盖校验异常: {!r}".format(e))

    # ── A5 搜索命中 ──
    try:
        hits = inst.search_hits("循环")
        check("循环开始" in hits["commands"],
              "A5 搜索 '循环' 命中命令: {}".format(
                  [c for c in hits["commands"] if "循环" in c][:3]),
              "A5 搜索 '循环' 未命中预期命令: {}".format(hits["commands"][:5]))
        hits2 = inst.search_hits("netlink")
        sec_ok = ("netlink" in hits2["sections"]) or bool(hits2["commands"])
        check(sec_ok,
              "A5 搜索 'netlink' 命中章节 {} / 命令 {}".format(
                  hits2["sections"], hits2["commands"][:3]),
              "A5 搜索 'netlink' 无任何命中")
    except Exception as e:
        _fail("A5 搜索校验异常: {!r}".format(e))

    # ── A6 主题跟随 ──
    try:
        before_txt = inst.text.cget("bg")
        before_nav = inst.nav.cget("bg")
        try:
            state.DARK_MODE = True
            darkC = dict(utils._colors())
        finally:
            state.DARK_MODE = False
        help_window.refresh_theme(root, darkC)
        root.update_idletasks()
        after_txt = inst.text.cget("bg")
        after_nav = inst.nav.cget("bg")
        check(str(after_txt) == str(darkC["logbg"])
              and str(after_nav) == str(darkC["bgc"])
              and (after_txt != before_txt or after_nav != before_nav),
              "A6 主题跟随: text.bg {} -> {} (nav {} -> {})".format(
                  before_txt, after_txt, before_nav, after_nav),
              "A6 主题未生效: text {} -> {} 期望 {}".format(
                  before_txt, after_txt, darkC["logbg"]))
        # 复原浅色
        help_window.refresh_theme(root, lightC)
        root.update_idletasks()
    except Exception as e:
        _fail("A6 主题刷新异常: {!r}".format(e))

    # ── A7 缩放跟随 ──
    try:
        check(help_window.scaled is utils.scaled,
              "A7 渲染层使用 utils.scaled（同源令牌）",
              "A7 渲染层未复用 utils.scaled")
        s0 = utils.current_ui_scale()
        v1 = utils.scaled(100)
        utils.set_ui_scale(1.5)
        v2 = utils.scaled(100)
        utils.set_ui_scale(s0)
        check(v2 > v1, "A7 缩放生效: scaled(100) {} -> {}（scale 1.0->1.5）".format(
                  v1, v2),
              "A7 缩放未生效: {} -> {}".format(v1, v2))
    except Exception as e:
        _fail("A7 缩放校验异常: {!r}".format(e))

    # ── A8 几何记忆 + 越界回退 ──
    try:
        # 1) 预置合法几何 -> 恢复使用
        valid = utils.center_geometry(root, 820, 560)
        state.HELP_GEOMETRY = valid
        state.HELP_MAXIMIZED = False
        inst._on_close()          # 关闭（触发写回, save_config 已 stub）
        win2 = help_window.open_help_window(root, lightC)
        root.update_idletasks()
        root.update()
        inst2 = help_window.get_instance()
        g2 = inst2.win.geometry()
        check(utils.geometry_in_screen(root, g2),
              "A8 记忆几何恢复: {} 落在虚拟屏内".format(g2),
              "A8 记忆几何越界: {} (预置 {})".format(g2, valid))
        saved = getattr(state, "HELP_GEOMETRY", "")
        check(bool(saved) and utils.geometry_in_screen(root, saved),
              "A8 关闭写回几何: {}".format(saved),
              "A8 关闭未写回有效几何: {!r}".format(saved))
        # 2) 越界几何 -> 回退居中
        inst2._on_close()
        state.HELP_GEOMETRY = "900x700+99999+99999"
        help_window.open_help_window(root, lightC)
        root.update_idletasks()
        root.update()
        inst3 = help_window.get_instance()
        g3 = inst3.win.geometry()
        check(utils.geometry_in_screen(root, g3),
              "A8 越界回退居中: {} 落在虚拟屏内".format(g3),
              "A8 越界未回退, 几何 {} 仍在屏外".format(g3))
        inst = inst3
    except Exception as e:
        _fail("A8 几何校验异常: {!r}".format(e))

    # ── A9 离线可用（链接拦截，不触发真实网络/外链） ──
    try:
        import webbrowser
        calls = {"startfile": [], "web": []}
        _orig_start = getattr(os, "startfile", None)
        _orig_web = webbrowser.open

        def _fake_start(path, *a, **k):
            calls["startfile"].append(path)

        def _fake_web(url, *a, **k):
            calls["web"].append(url)
            return True

        try:
            os.startfile = _fake_start
            webbrowser.open = _fake_web
            ok_doc = inst._open_link("docs/netlink-总览与部署指南.md")
            ok_url = inst._open_link("https://gitee.com/yohoten/acrpa")
            ok_bad = inst._open_link("docs/__no_such_doc__.md")
        finally:
            if _orig_start is not None:
                os.startfile = _orig_start
            webbrowser.open = _orig_web
        check(ok_doc and any("netlink" in str(p) for p in calls["startfile"]),
              "A9 本地 docs 链接走 os.startfile 并命中: {}".format(
                  [os.path.basename(str(p)) for p in calls["startfile"]]),
              "A9 本地链接未按预期打开: {}".format(calls["startfile"]))
        check(ok_url and calls["web"],
              "A9 http 链接走浏览器（被拦截）: {}".format(calls["web"]),
              "A9 http 链接未走浏览器: {}".format(calls["web"]))
        check(ok_bad is False,
              "A9 失效链接返回 False 且给出可见提示（非静默崩溃）",
              "A9 失效链接未正确处理（返回 {}）".format(ok_bad))
    except Exception as e:
        _fail("A9 离线链接校验异常: {!r}".format(e))

    # ── A11 向后兼容：dialogs.show_help_dialog 委托 ──
    try:
        import dialogs
        dialogs.init_ctx(root, lightC,
                         (utils.FONT_TITLE, utils.FONT_BODY,
                          utils.FONT_SMALL, utils.FONT_BUTTON))
        has_legacy = hasattr(dialogs, "_show_help_dialog_legacy")
        ret = dialogs.show_help_dialog()
        root.update_idletasks()
        check(has_legacy and help_window.is_open() and ret is not None,
              "A11 show_help_dialog() 委托成功（legacy fallback 保留）",
              "A11 show_help_dialog() 兼容失败: legacy={} open={} ret={!r}".format(
                  has_legacy, help_window.is_open(), ret))
    except Exception as e:
        _fail("A11 兼容入口校验异常: {!r}".format(e))

    _cleanup(root, state, _orig_save)
    emit("-" * 64)
    emit("FAIL={} | WARN={}".format(FAILS, WARNS))
    emit("结论: {}".format("PASS" if FAILS == 0 else "FAIL"))
    _write_report()
    return 0 if FAILS == 0 else 1


def _cleanup(root, state, orig_save):
    try:
        inst = None
        import help_window
        inst = help_window.get_instance()
        if inst is not None and inst.alive():
            inst._on_close()
    except Exception:
        pass
    try:
        state.save_config = orig_save
    except Exception:
        pass
    try:
        root.destroy()
    except Exception:
        pass


def _write_report():
    try:
        with open(REPORT, "w", encoding="utf-8") as f:
            f.write("\n".join(_lines) + "\n")
    except Exception:
        pass


if __name__ == "__main__":
    sys.exit(main())
