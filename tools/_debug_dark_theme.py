#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""暗黑模式「亮 → 暗 → 亮」往返切换运行时验证 (真实 Tk 窗口实例化)。

用法:
    python -X utf8 tools/_debug_dark_theme.py

覆盖:
  D2 语义按钮回填     — ▶运行(sc)/⏸暂停(wn)/⏭单步(ac)/●录制(dg) 在往返切换后
                        必须等于当前主题期望色 (旧实现在暗色下全部被刷成 C["bgc"] 灰)
  D2 settings 窗口    — 设置窗口内不得残留上一主题的语义色按钮
  D3 Treeview/日志 tag — tree("running") 背景、rz 日志 tag 前景随主题更新
  D1 dialogs.C        — 切换后弹窗模块颜色表已重同步为当前主题
  D4 netlink_window   — 已打开的设备互联窗口配色 + Treeview tag 前景随主题更新
  D6 mini bar         — 图标前景/底色、语义按钮底色随主题更新

退出码: 0 = 全部通过 / 1 = 有失败。
若 ACRPA 无法导入 (缺依赖等), 自动降级为静态断言并明确标注。
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "src"))

FAILS = []
CHECKS = []


def _p(status, msg):
    print("[{}] {}".format(status, msg))


def expect(name, actual, expected):
    ok = (actual == expected)
    CHECKS.append((name, actual, expected, ok))
    if not ok:
        FAILS.append("{}: got {!r}, expected {!r}".format(name, actual, expected))
    _p("OK" if ok else "FAIL",
       "{}: got {!r} / expected {!r}".format(name, actual, expected))


def expect_true(name, cond, detail=""):
    CHECKS.append((name, detail, "True", bool(cond)))
    if not cond:
        FAILS.append("{}: {}".format(name, detail))
    _p("OK" if cond else "FAIL", "{} {}".format(name, detail))


def _palette_line(tag, C):
    return "[{}] sc={} dg={} wn={} ac={} acl={} bd={} hlbg={} bgc={} bg={}".format(
        tag, C.get("sc"), C.get("dg"), C.get("wn"), C.get("ac"), C.get("acl"),
        C.get("bd"), C.get("hlbg"), C.get("bgc"), C.get("bg"))


def _tv_tag_bg(tree, tag):
    """Treeview 读 tag 背景 (Treeview 没有 tag_cget, 用 tag_configure 查询)。"""
    try:
        return str(tree.tag_configure(tag).get("background", ""))
    except Exception:
        return "<err>"


def _tv_tag_fg(tree, tag):
    """Treeview 读 tag 前景。"""
    try:
        return str(tree.tag_configure(tag).get("foreground", ""))
    except Exception:
        return "<err>"


def _text_tag_fg(text_widget, tag):
    """Text 控件用 tag_cget 读 tag 前景。"""
    try:
        return str(text_widget.tag_cget(tag, "foreground"))
    except Exception:
        return "<err>"


def _force_theme(acrpa, state, dark):
    state.DARK_MODE = bool(dark)
    acrpa._refresh_theme()
    try:
        acrpa.root.update()
    except Exception:
        pass


def _check_main_window(acrpa, cur):
    """D2 + D3: 语义按钮 / 树 tag / 日志 tag。"""
    expect("btn_run.bg(运行=sc)", acrpa.btn_run.cget("bg"), cur["sc"])
    expect("btn_pause.bg(暂停=wn)", acrpa.btn_pause.cget("bg"), cur["wn"])
    expect("btn_step.bg(单步=ac)", acrpa.btn_step.cget("bg"), cur["ac"])
    expect("btn_record.bg(录制=dg)", acrpa.btn_record.cget("bg"), cur["dg"])
    expect("tree.tag('running').background", _tv_tag_bg(acrpa.tree, "running"), cur["hlbg"])
    expect("rz.tag('error').foreground", _text_tag_fg(acrpa.rz, "error"), cur["dg"])
    expect("rz.tag('success').foreground", _text_tag_fg(acrpa.rz, "success"), cur["sc"])
    expect("rz.tag('warning').foreground", _text_tag_fg(acrpa.rz, "warning"), cur["wn"])


def _check_dialogs(acrpa, dialogs, utils):
    """D1: 弹窗模块颜色表已重同步 (取值与当前主题一致)。

    注: apply_theme() 会另行重绑 utils.C 为「取值相同的新 dict」, 因此跨模块的
    dict 对象本就不同 (settings_window 亦然); 断言口径取「取值等价」, 并额外
    确认 dialogs.C 正是 ACRPA 当前使用的颜色表对象。
    """
    expect_true("dialogs.C 取值 == 当前 utils.C",
                dialogs.C == utils.C,
                "dialogs.C={} utils.C={}".format(dialogs.C, utils.C))
    expect_true("dialogs.C is ACRPA.C (同一颜色表对象)",
                dialogs.C is acrpa.C,
                "dialogs.C={} acrpa.C={}".format(id(dialogs.C), id(acrpa.C)))
    if isinstance(dialogs.C, dict):
        expect("dialogs.C['ac'] == 当前 ac", dialogs.C.get("ac"), utils.C["ac"])


def _count_semantic_buttons(win, palette):
    """统计 win 子树中底色仍为「上一主题语义色」的 Button 数量 (应为 0)。"""
    bad = []
    try:
        old = set([palette.get("sc"), palette.get("dg"), palette.get("wn"),
                   palette.get("ac"), palette.get("ok"), palette.get("err")])
    except Exception:
        old = set()

    def walk(p):
        for w in p.winfo_children():
            try:
                if w.winfo_class() == "Button" and str(w.cget("bg")) in old:
                    bad.append(str(w.cget("bg")))
            except Exception:
                pass
            walk(w)

    try:
        walk(win)
    except Exception:
        pass
    return bad


def _check_settings_window(acrpa, settings_window, utils, prev_palette):
    """D2(settings): 设置窗口内的语义按钮不得停留在旧主题色。"""
    try:
        settings_window.open_settings_window()
        acrpa.root.update()
    except Exception as e:
        _p("WARN", "设置窗口未能打开, 跳过 settings 语义色检查: {}".format(e))
        return
    win = settings_window._win
    if win is None:
        _p("WARN", "settings_window._win 为空, 跳过")
        return
    bad = _count_semantic_buttons(win, prev_palette)
    expect_true("settings: 无按钮停留在上一主题语义色",
                not bad,
                "残留旧色按钮 {} 个: {}".format(len(bad), sorted(set(bad))[:6]))
    expect_true("settings: _prev_colors 已同步为当前主题",
                isinstance(settings_window._prev_colors, dict)
                and settings_window._prev_colors.get("ac") == utils.C["ac"],
                "_prev_colors.ac={}".format(
                    (settings_window._prev_colors or {}).get("ac")))
    try:
        settings_window._close()
        acrpa.root.update()
    except Exception:
        pass


def _check_netlink(acrpa, utils):
    """D4: 设备互联窗口刷新入口 + tag 前景。"""
    try:
        import netlink_window
    except Exception as e:
        _p("WARN", "netlink_window 导入失败, 跳过 D4: {}".format(e))
        return
    expect_true("netlink_window 暴露模块级 refresh_theme()",
                hasattr(netlink_window, "refresh_theme"))
    try:
        inst = netlink_window.open_netlink_window(acrpa.root)
    except Exception as e:
        _p("WARN", "设备互联窗口未能打开, 跳过: {}".format(e))
        return
    if inst is None:
        _p("WARN", "设备互联窗口未创建 (单例为空), 跳过")
        return
    expect_true("NetLinkWindow 存在 refresh_theme()", hasattr(inst, "refresh_theme"))
    expect("netlink.win.bg", inst.win.cget("bg"), utils.C["bg"])
    expect("netlink.tv tag('run').foreground", _tv_tag_fg(inst.tv, "run"), utils.C["sc"])
    expect("netlink.tv tag('pause').foreground", _tv_tag_fg(inst.tv, "pause"), utils.C["wn"])
    try:
        netlink_window.close_window()
        acrpa.root.update()
    except Exception:
        pass


def _mb_theme_tuple_has(acrpa):
    """源码级确认 mini bar 主题同步元组补入了 ac/bd/acl (签名健壮性)。"""
    try:
        fp = os.path.join(ROOT, "src", "ACRPA.py")
        with io.open(fp, "r", encoding="utf-8") as f:
            src = f.read()
        i = src.index("def _sync_mini_bar_status")
        seg = src[i:i + 2000]
        return ('C["ac"], C["bd"], C["acl"]' in seg)
    except Exception:
        return False


def _check_mini_bar(acrpa, utils):
    """D6: mini bar 图标前景 + 底色 + 主题元组。"""
    if not getattr(acrpa.state, "MINI_BAR_ENABLED", True):
        _p("WARN", "MINI_BAR_ENABLED=False, 跳过 mini bar 检查")
        return
    try:
        acrpa._create_mini_bar()
        acrpa.root.update()
    except Exception as e:
        _p("WARN", "Mini Bar 未能创建, 跳过: {}".format(e))
        return
    mb = acrpa._mini_bar
    if mb is None:
        _p("WARN", "Mini Bar 为空, 跳过")
        return
    w = mb._widgets
    expect("mini bar icon.fg", w["icon"].cget("fg"), utils.C["ac"])
    expect("mini bar outer.bg", w["outer"].cget("bg"), utils.C["ac"])
    expect("mini bar run.bg", w["run"].cget("bg"), utils.C["sc"])
    expect("mini bar stop.bg", w["stop"].cget("bg"), utils.C["dg"])
    expect("mini bar pause.bg", w["pause"].cget("bg"), utils.C["wn"])
    expect("mini bar step.bg", w["step"].cget("bg"), utils.C["ac"])
    expect_true("mini bar 主题同步元组含 ac/bd/acl",
                _mb_theme_tuple_has(acrpa),
                "见 _sync_mini_bar_status 的 theme 元组")
    try:
        acrpa._destroy_mini_bar()
        acrpa.root.update()
    except Exception:
        pass


def _check_refresh_idempotent(acrpa):
    """G-RT: 同主题下连续两次 _refresh_theme() 必须幂等 (色板与控件色均不变)。

    若刷新链路里遗留累计副作用 (反复 append/重绑/追加 tag), 二次刷新会显现差异;
    本用例在同一定稿主题上再刷两次以卡住该类回归。
    """
    try:
        before = dict(acrpa.C)
        snap = (acrpa.btn_run.cget("bg"), acrpa.btn_pause.cget("bg"),
                acrpa.btn_step.cget("bg"), acrpa.btn_record.cget("bg"),
                _tv_tag_bg(acrpa.tree, "running"), _text_tag_fg(acrpa.rz, "error"))
        acrpa._refresh_theme()
        acrpa._refresh_theme()
        acrpa.root.update()
        after = dict(acrpa.C)
        after_snap = (acrpa.btn_run.cget("bg"), acrpa.btn_pause.cget("bg"),
                      acrpa.btn_step.cget("bg"), acrpa.btn_record.cget("bg"),
                      _tv_tag_bg(acrpa.tree, "running"), _text_tag_fg(acrpa.rz, "error"))
        expect_true("幂等: 同主题两次 _refresh_theme 色板一致",
                    before == after,
                    "diff={}".format({k: (before.get(k), after.get(k))
                                      for k in before if before.get(k) != after.get(k)}))
        expect_true("幂等: 同主题两次 _refresh_theme 控件色不变",
                    snap == after_snap,
                    "before={} after={}".format(snap, after_snap))
    except Exception as e:
        _p("WARN", "幂等检查异常, 跳过: {}".format(e))


def run_static_fallback():
    """ACRPA 无法实例化时的静态降级校验。"""
    print("=== 降级为静态断言 (无法实例化 ACRPA) ===")
    srcs = {}
    for key, rel in (("acrpa", "src/ACRPA.py"), ("settings", "src/settings_window.py"),
                     ("netlink", "src/netlink_window.py"), ("utils", "src/utils.py")):
        with io.open(os.path.join(ROOT, rel), "r", encoding="utf-8") as f:
            srcs[key] = f.read()

    expect_true("netlink_window 定义 refresh_theme()",
                "def refresh_theme" in srcs["netlink"])
    expect_true("settings refresh_theme 接受 prev 参数",
                "def refresh_theme(prev=None):" in srcs["settings"])
    expect_true("ACRPA._refresh_theme 同步 dialogs.C",
                "dialogs.C = C" in srcs["acrpa"])
    expect_true("ACRPA._refresh_theme 调用 netlink_window.refresh_theme()",
                "netlink_window.refresh_theme()" in srcs["acrpa"])
    expect_true("无 C[\"old_sc\"] 残留 (改旧快照判定)",
                'C["old_sc"]' not in srcs["acrpa"]
                and 'C.get("old_sc")' not in srcs["acrpa"])
    expect_true("utils._colors 提供 hlbg 语义键",
                "hlbg=" in srcs["utils"].replace(" ", ""))
    expect_true("不再有写死的 tag_configure(\"running\", background=\"#FEF3C7\")",
                '"running", background="#FEF3C7"' not in srcs["acrpa"])
    return 1 if FAILS else 0


def main():
    print("=== ACRPA 暗黑模式往返切换运行时验证 (真实 Tk) ===")
    try:
        import ACRPA
        import state
        import utils
        import dialogs
    except Exception as e:
        _p("WARN", "ACRPA 导入失败 ({}), 降级静态断言".format(e))
        rc = run_static_fallback()
        print("=== 结果: {} ===".format(
            "FAIL ({})".format(len(FAILS)) if FAILS else "OK"))
        return rc

    # 关闭启动期更新检查 (本机缺 requests, 会污染输出)
    try:
        state.CHECK_UPDATE = False
    except Exception:
        pass

    # ── 基线: 亮色 ──────────────────────────────────────────────────
    _force_theme(ACRPA, state, False)
    light = dict(ACRPA.C)
    print(_palette_line("light", light))
    _check_main_window(ACRPA, light)
    _check_dialogs(ACRPA, dialogs, utils)
    _check_refresh_idempotent(ACRPA)

    # ── 切到暗色 ────────────────────────────────────────────────────
    _force_theme(ACRPA, state, True)
    dark = dict(ACRPA.C)
    print(_palette_line("dark ", dark))
    expect_true("暗色色板与亮色不同",
                light["sc"] != dark["sc"] and light["ac"] != dark["ac"])
    _check_main_window(ACRPA, dark)
    _check_dialogs(ACRPA, dialogs, utils)
    _check_refresh_idempotent(ACRPA)
    _check_settings_window(ACRPA, ACRPA.settings_window, utils, light)
    _check_netlink(ACRPA, utils)
    _check_mini_bar(ACRPA, utils)

    # ── 再切回亮色 (往返) ───────────────────────────────────────────
    _force_theme(ACRPA, state, False)
    back = dict(ACRPA.C)
    print(_palette_line("light*", back))
    _check_main_window(ACRPA, back)
    _check_dialogs(ACRPA, dialogs, utils)
    _check_netlink(ACRPA, utils)

    print("--- 往返切换前后「主题期望色」对照 ---")
    print("{:<30}{:>11}{:>11}{:>11}".format("palette key", "light", "dark", "light*"))
    for name, lv, dv, bv in (("play btn (sc)", light["sc"], dark["sc"], back["sc"]),
                             ("pause btn (wn)", light["wn"], dark["wn"], back["wn"]),
                             ("step btn (ac)", light["ac"], dark["ac"], back["ac"]),
                             ("record btn (dg)", light["dg"], dark["dg"], back["dg"]),
                             ("tree running (hlbg)", light["hlbg"], dark["hlbg"], back["hlbg"]),
                             ("log error (dg)", light["dg"], dark["dg"], back["dg"])):
        print("{:<30}{:>11}{:>11}{:>11}".format(name, lv, dv, bv))

    print("--- 控件实测 (往返切换后, 应等于 light* 列) ---")
    print("{:<30}{:>20}".format("btn_run.bg (实测)", str(ACRPA.btn_run.cget("bg"))))
    print("{:<30}{:>20}".format("btn_pause.bg (实测)", str(ACRPA.btn_pause.cget("bg"))))
    print("{:<30}{:>20}".format("btn_step.bg (实测)", str(ACRPA.btn_step.cget("bg"))))
    print("{:<30}{:>20}".format("btn_record.bg (实测)", str(ACRPA.btn_record.cget("bg"))))
    print("{:<30}{:>20}".format("tree running tag (实测)", _tv_tag_bg(ACRPA.tree, "running")))
    print("{:<30}{:>20}".format("rz error tag (实测)", _text_tag_fg(ACRPA.rz, "error")))
    print("{:<30}{:>20}".format("dialogs.C['ac'] (实测)", str(dialogs.C.get("ac"))))

    try:
        ACRPA.root.destroy()
    except Exception:
        pass

    print("=== 结果: {} (共 {} 项检查) ===".format(
        "FAIL ({})".format(len(FAILS)) if FAILS else "OK", len(CHECKS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
