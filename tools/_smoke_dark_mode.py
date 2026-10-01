#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""暗黑模式修复 + 互联按钮去字 静态自测 (不弹窗, 退出码 0/1, 输出 [OK]/[WARN]/[FAIL])。

用法:
    python tools/_smoke_dark_mode.py

覆盖:
  A. src/utils.py          — 存在 def themed( ; show_toast 体不再出现固定色字面量,
                             改用主题化 bg_map (C["ac"]/C["sc"]/C["wn"]/C["dg"])
  B. dialogs.py/scheduler.py/ACRPA.py
                           — 占位符/语义色不再硬编码 #9CA3AF(/#10B981);
                             三文件各自含 themed( 调用, 合计 ≥ 3
  C. src/ACRPA.py          — 日志 Text 存在 insertbackground= ;
                             devlink_btn text="🌐" 且 font 字号 11 ; "🌐 互联" 已消失
  D. src/ACRPA.py          — NetLink 集成块未被改动 (各 1 次)
  E. src/ACRPA.py          — Mini Bar 守护: _mb_animate_width 存在 /
                             _destroy_mini_bar 含 after_cancel
  F. 依赖探测: pyautogui/xlrd/pyperclip 缺失 → [WARN] (不假通过)

注: 纯静态断言, 不 import ACRPA (本机缺依赖), 不创建任何窗口。
"""
import ast
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAILS = []

# Toast 原先写死的四种固定色 (本任务要求从 show_toast 体内清除)
_FIXED_HEX = ("#2563eb", "#10b981", "#f59e0b", "#ef4444")


def _p(status, msg):
    print("[{}] {}".format(status, msg))


def _read(rel):
    with open(os.path.join(ROOT, rel), "r", encoding="utf-8") as f:
        return f.read()


def _parse(rel):
    return ast.parse(_read(rel), filename=rel)


def _find_func(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def _sub_kinds(node, name_id):
    """收集 node 内所有 Name(name_id)[<str>] 的下标字符串集合。"""
    keys = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Subscript) and isinstance(sub.value, ast.Name) \
                and sub.value.id == name_id and isinstance(sub.slice, ast.Constant) \
                and isinstance(sub.slice.value, str):
            keys.add(sub.slice.value)
    return keys


def check_utils():
    """A. utils.py: themed 护栏 + Toast 主题化。"""
    src = _read("src/utils.py")
    tree = _parse("src/utils.py")

    if _find_func(tree, "themed") is not None:
        _p("OK", "utils.py 存在 def themed(")
    else:
        FAILS.append("utils.py 缺 def themed(")
        _p("FAIL", "utils.py 缺 def themed(")

    toast = _find_func(tree, "show_toast")
    if toast is None:
        FAILS.append("utils.py 缺 show_toast")
        _p("FAIL", "utils.py 缺 show_toast")
        return

    # A2. show_toast 体内不得再出现固定色字面量
    bad = []
    for n in ast.walk(toast):
        if isinstance(n, ast.Constant) and isinstance(n.value, str) \
                and n.value.strip().lower() in _FIXED_HEX:
            bad.append(n.value)
    if bad:
        FAILS.append("show_toast 仍含固定色字面量: {}".format(bad))
        _p("FAIL", "show_toast 仍含固定色字面量: {}".format(bad))
    else:
        _p("OK", "show_toast 已无固定色字面量")

    # A3. 存在 bg_map 变量
    has_bg_map = any(
        isinstance(n, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "bg_map" for t in n.targets)
        for n in ast.walk(toast))
    if has_bg_map:
        _p("OK", "show_toast 使用主题化 bg_map")
    else:
        FAILS.append("show_toast 缺 bg_map 主题化映射")
        _p("FAIL", "show_toast 缺 bg_map 主题化映射")

    # A4. bg_map 引用 C["ac"]/C["sc"]/C["wn"]/C["dg"]
    need = {"ac", "sc", "wn", "dg"}
    hit = _sub_kinds(toast, "C").intersection(need)
    if hit == need:
        _p("OK", "show_toast 引用主题色 C['ac'/'sc'/'wn'/'dg']")
    else:
        FAILS.append("show_toast 缺主题色引用: {}".format(sorted(need - hit)))
        _p("FAIL", "show_toast 缺主题色引用: {}".format(sorted(need - hit)))

    # A5. 模块内存在 themed 定义体 (读取 C[key] 且含 fallback 回退)
    tsrc = None
    for name in ("src/utils.py",):
        tsrc = _read(name)
    if "def themed(" in tsrc:
        _p("OK", "utils.py themed 定义位置就绪")
    else:
        FAILS.append("utils.py themed 定义缺失")
        _p("FAIL", "utils.py themed 定义缺失")


def _themed_count(rel):
    return _read(rel).count("themed(")


def check_hardcoded_colors():
    """B. 三文件硬编码色清扫 + themed 使用。"""
    # B1. dialogs.py 完全清除 #9CA3AF
    d_src = _read("src/dialogs.py")
    if "#9CA3AF" not in d_src and "#9ca3af" not in d_src:
        _p("OK", "dialogs.py 已无 #9CA3AF (占位符改 themed('fgm'))")
    else:
        FAILS.append("dialogs.py 仍含 #9CA3AF")
        _p("FAIL", "dialogs.py 仍含 #9CA3AF")

    # B2. scheduler.py 完全清除 #9CA3AF / #10B981
    s_src = _read("src/scheduler.py")
    s_hits = [h for h in ("#9CA3AF", "#9ca3af", "#10B981", "#10b981") if h in s_src]
    if not s_hits:
        _p("OK", "scheduler.py 已无 #9CA3AF/#10B981 (改 themed('fgm'/'sc'))")
    else:
        FAILS.append("scheduler.py 仍含硬编码: {}".format(s_hits))
        _p("FAIL", "scheduler.py 仍含硬编码: {}".format(s_hits))

    # B3. ACRPA.py: #9CA3AF 仅允许保留在 _FLOW_COLORS 图形色板内
    a_src = _read("src/ACRPA.py")
    i0 = a_src.find("_FLOW_COLORS = {")
    i1 = a_src.find("}", i0) if i0 != -1 else -1
    stray = None
    pos = a_src.find("#9CA3AF")
    while pos != -1:
        if not (i0 != -1 and i0 < pos < i1):
            stray = pos
            break
        pos = a_src.find("#9CA3AF", pos + 1)
    if stray is None:
        _p("OK", "ACRPA.py 的 #9CA3AF 仅保留在 _FLOW_COLORS 图形色板内 (刻意固定)")
    else:
        FAILS.append("ACRPA.py 在 _FLOW_COLORS 之外仍含 #9CA3AF")
        _p("FAIL", "ACRPA.py 在 _FLOW_COLORS 之外仍含 #9CA3AF")

    # B4. 三文件各自含 themed( , 合计 ≥ 3
    c_d = _themed_count("src/dialogs.py")
    c_s = _themed_count("src/scheduler.py")
    c_a = _themed_count("src/ACRPA.py")
    total = c_d + c_s + c_a
    detail = "dialogs={} scheduler={} ACRPA={}".format(c_d, c_s, c_a)
    if c_d >= 1 and c_s >= 1 and c_a >= 1 and total >= 3:
        _p("OK", "themed( 使用分布合格 ({}, 合计 {})".format(detail, total))
    else:
        FAILS.append("themed( 使用不足 ({}, 合计 {})".format(detail, total))
        _p("FAIL", "themed( 使用不足 ({}, 合计 {})".format(detail, total))


def check_acrpa_cursor_and_button():
    """C. 日志 Text 光标 + 互联按钮去字。"""
    a_src = _read("src/ACRPA.py")
    tree = _parse("src/ACRPA.py")

    # C1. 存在 insertbackground=
    if "insertbackground=" in a_src:
        _p("OK", "ACRPA.py 存在 insertbackground= (光标色随主题)")
    else:
        FAILS.append("ACRPA.py 缺 insertbackground=")
        _p("FAIL", "ACRPA.py 缺 insertbackground=")

    # C2. devlink_btn: text="🌐", font 字号 11
    dev_ok = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "devlink_btn" for t in node.targets):
            call = node.value
            if not isinstance(call, ast.Call):
                continue
            kw = {k.arg: k.value for k in call.keywords}
            text_v = getattr(kw.get("text"), "value", None)
            font_v = kw.get("font")
            size = None
            if isinstance(font_v, ast.Tuple) and len(font_v.elts) >= 2 \
                    and isinstance(font_v.elts[1], ast.Constant):
                size = font_v.elts[1].value
            if text_v == "🌐" and size == 11:
                dev_ok = True
    if dev_ok:
        _p("OK", "devlink_btn text='🌐' 且 font 字号 11")
    else:
        FAILS.append("devlink_btn 未满足 text='🌐' + 字号 11")
        _p("FAIL", "devlink_btn 未满足 text='🌐' + 字号 11")

    # C3. 旧文案已消失
    if "🌐 互联" not in a_src:
        _p("OK", "ACRPA.py 已无 '🌐 互联' 旧文案")
    else:
        FAILS.append("ACRPA.py 仍含 '🌐 互联'")
        _p("FAIL", "ACRPA.py 仍含 '🌐 互联'")


def check_netlink_untouched():
    """D. NetLink 集成块未被改动 (各 1 次)。"""
    tree = _parse("src/ACRPA.py")
    counts = {"open_devlink": 0, "set_control_hooks": 0,
              "start_netlink": 0, "_nl_hook_run": 0}
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef):
            if n.name == "open_devlink":
                counts["open_devlink"] += 1
            elif n.name == "_nl_hook_run":
                counts["_nl_hook_run"] += 1
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
            if n.func.attr in ("set_control_hooks", "start_netlink"):
                counts[n.func.attr] += 1
    ok = True
    for k, v in counts.items():
        if v == 1:
            _p("OK", "NetLink 块 '{}' 出现 1 次".format(k))
        else:
            ok = False
            FAILS.append("NetLink 块 '{}' 出现 {} 次 (期望 1)".format(k, v))
            _p("FAIL", "NetLink 块 '{}' 出现 {} 次 (期望 1)".format(k, v))
    if ok:
        _p("OK", "NetLink 集成块未见改动")


def check_mini_bar_guards():
    """E. Mini Bar 守护断言。"""
    tree = _parse("src/ACRPA.py")

    if _find_func(tree, "_mb_animate_width") is not None:
        _p("OK", "存在 _mb_animate_width (Mini Bar 守护)")
    else:
        FAILS.append("缺 _mb_animate_width")
        _p("FAIL", "缺 _mb_animate_width")

    node = _find_func(tree, "_destroy_mini_bar")
    has_cancel = False
    if node is not None:
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) \
                    and sub.func.attr == "after_cancel":
                has_cancel = True
    if has_cancel:
        _p("OK", "_destroy_mini_bar 含 after_cancel (定时器可取消)")
    else:
        FAILS.append("_destroy_mini_bar 缺 after_cancel")
        _p("FAIL", "_destroy_mini_bar 缺 after_cancel")

def check_theme_refresh_regressions():
    """G. 暗黑模式「全应用色彩缺失」修复回归断言 (防回退护栏)。

    G1. ACRPA._refresh_theme 内同步 dialogs.C (D1 弹窗沿用旧配色)
    G2. 语义色判定不再依赖未初始化的 C["old_*"] (D2 语义按钮被刷灰主因)
    G3. mini bar 主题同步元组补入 ac/bd/acl (签名健壮性)
    G4. 不再有写死的 #FEF3C7 直接用于 tag_configure (D3 → 统一走 C["hlbg"])
    G5. _refresh_theme 内重新 tag_configure 日志区与脚本树 (D3 tag 前景不刷新)
    G6. settings_window.refresh_theme 接受旧快照 prev 参数 (D2 settings 同源缺陷)
    G7. netlink_window 提供 refresh_theme 且 ACRPA 调用之 (D4)
    G8. utils._colors 提供 hlbg 主题键 (D3 主题化高亮底色)
    G9. _walk 覆盖 Menu/Checkbutton/Radiobutton (D5 覆盖不全)
    """
    a_src = _read("src/ACRPA.py")
    a_tree = _parse("src/ACRPA.py")
    rt = _find_func(a_tree, "_refresh_theme")
    seg = ast.get_source_segment(a_src, rt) if rt is not None else None
    if seg is None:
        FAILS.append("G: 未找到 ACRPA._refresh_theme")
        _p("FAIL", "G: 未找到 ACRPA._refresh_theme")
        return

    # G1. dialogs 颜色表重同步
    if "dialogs.C = C" in seg:
        _p("OK", "G1 _refresh_theme 同步 dialogs.C (D1)")
    else:
        FAILS.append("G1 _refresh_theme 未同步 dialogs.C")
        _p("FAIL", "G1 _refresh_theme 未同步 dialogs.C (D1)")

    # G2. 不得再读取/写入 C["old_*"]
    old_names = ("old_sc", "old_dg", "old_wn", "old_ac")
    hits = [n for n in old_names if n in seg]
    if hits:
        FAILS.append("G2 _refresh_theme 仍引用 C['old_*']: {}".format(hits))
        _p("FAIL", "G2 _refresh_theme 仍引用 C['old_*']: {}".format(hits))
    else:
        _p("OK", "G2 语义色判定改用旧快照, 无 C['old_*'] 残留 (D2)")

    # G2b. 存在旧快照变量 (局部变量 _prev)
    if "_prev" in seg:
        _p("OK", "G2b _refresh_theme 使用旧主题色快照 _prev")
    else:
        FAILS.append("G2b _refresh_theme 缺旧主题色快照 _prev")
        _p("FAIL", "G2b _refresh_theme 缺旧主题色快照 _prev")

    # G2c. AST 顺序断言: 旧快照 _prev = dict(C) 的行号必须 < C = _colors() 的行号。
    # 防止「快照被移动到重绑之后」——那样 _prev 等于新主题, 语义色判定失效, D2 静默复发。
    prev_line = None
    colors_line = None
    for sub in ast.walk(rt):
        if not isinstance(sub, ast.Assign):
            continue
        for t in sub.targets:
            if isinstance(t, ast.Name) and t.id == "_prev":
                prev_line = sub.lineno
            if isinstance(t, ast.Name) and t.id == "C" \
                    and isinstance(sub.value, ast.Call) \
                    and isinstance(sub.value.func, ast.Name) \
                    and sub.value.func.id == "_colors":
                colors_line = sub.lineno
    if prev_line is not None and colors_line is not None and prev_line < colors_line:
        _p("OK", "G2c _prev 快照行号 < C=_colors() 重绑行号 ({} < {})".format(
            prev_line, colors_line))
    else:
        FAILS.append("G2c _prev 快照须先于 C=_colors() 重绑 (prev={}, colors={})".format(
            prev_line, colors_line))
        _p("FAIL", "G2c _prev 快照须先于 C=_colors() 重绑 (prev={}, colors={})".format(
            prev_line, colors_line))

    # G3. mini bar 主题同步元组含 ac/bd/acl
    mb = _find_func(a_tree, "_sync_mini_bar_status")
    mb_seg = ast.get_source_segment(a_src, mb) if mb is not None else ""
    if 'C["ac"], C["bd"], C["acl"]' in (mb_seg or ""):
        _p("OK", "G3 mini bar 主题同步元组含 ac/bd/acl")
    else:
        FAILS.append("G3 mini bar 主题同步元组缺 ac/bd/acl")
        _p("FAIL", "G3 mini bar 主题同步元组缺 ac/bd/acl")

    # G4. ACRPA.py 内不得再有写死的 #FEF3C7
    if 'background="#FEF3C7"' not in a_src:
        _p("OK", "G4 ACRPA.py 无写死 #FEF3C7 颜色值 (改用 C['hlbg'] ; 注释提及不计)")
    else:
        FAILS.append("G4 ACRPA.py 仍将 #FEF3C7 作为颜色值使用")
        _p("FAIL", "G4 ACRPA.py 仍将 #FEF3C7 作为颜色值使用")

    # G5. _refresh_theme 内重刷日志区 / 脚本树 tag
    has_log_tag = 'tag_configure("error"' in seg and 'tag_configure("success"' in seg
    has_tree_tag = 'tag_configure("running"' in seg
    if has_log_tag and has_tree_tag:
        _p("OK", "G5 _refresh_theme 重刷日志区 + 脚本树 tag (D3)")
    else:
        FAILS.append("G5 _refresh_theme 缺 tag 重刷 (log={} tree={})".format(
            has_log_tag, has_tree_tag))
        _p("FAIL", "G5 _refresh_theme 缺 tag 重刷 (log={} tree={})".format(
            has_log_tag, has_tree_tag))

    # G6. settings_window.refresh_theme 接受 prev
    s_src = _read("src/settings_window.py")
    if "def refresh_theme(prev=None):" in s_src:
        _p("OK", "G6 settings_window.refresh_theme 接受 prev 旧快照 (D2)")
    else:
        FAILS.append("G6 settings_window.refresh_theme 未接受 prev 旧快照")
        _p("FAIL", "G6 settings_window.refresh_theme 未接受 prev 旧快照")

    # G7. netlink_window 主题刷新入口 + ACRPA 调用
    n_src = _read("src/netlink_window.py")
    if "def refresh_theme" in n_src and "netlink_window.refresh_theme()" in seg:
        _p("OK", "G7 netlink_window.refresh_theme 存在且被 _refresh_theme 调用 (D4)")
    else:
        FAILS.append("G7 netlink_window 主题刷新入口缺失或未接入")
        _p("FAIL", "G7 netlink_window 主题刷新入口缺失或未接入")

    # G8. utils._colors 提供 hlbg
    u_src = _read("src/utils.py")
    if "hlbg=" in u_src.replace(" ", ""):
        _p("OK", "G8 utils._colors 提供 hlbg 主题键")
    else:
        FAILS.append("G8 utils._colors 缺 hlbg 主题键")
        _p("FAIL", "G8 utils._colors 缺 hlbg 主题键")

    # G9. _walk 覆盖 Menu / Checkbutton / Radiobutton (主窗 + settings)
    if '"Menu"' in seg and '"Checkbutton", "Radiobutton"' in seg:
        _p("OK", "G9 ACRPA._walk 覆盖 Menu/Checkbutton/Radiobutton (D5)")
    else:
        FAILS.append("G9 ACRPA._walk 覆盖不全 (Menu/Checkbutton/Radiobutton)")
        _p("FAIL", "G9 ACRPA._walk 覆盖不全 (Menu/Checkbutton/Radiobutton)")
    if '"Menu"' in s_src and '"Canvas"' in s_src and '"Listbox"' in s_src \
            and '"Spinbox"' in s_src:
        _p("OK", "G9b settings._walk 覆盖 Menu/Canvas/Listbox/Spinbox (D5)")
    else:
        FAILS.append("G9b settings._walk 覆盖不全")
        _p("FAIL", "G9b settings._walk 覆盖不全")


def probe_deps():
    """F. 依赖探测: 打印真实 import 结果, 缺失仅 [WARN] (不假通过)。"""
    code = "import pyautogui, xlrd, pyperclip"
    try:
        proc = subprocess.run([sys.executable, "-c", code],
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              cwd=ROOT)
        rc = proc.returncode
        tail = (proc.stdout or b"").decode("utf-8", "ignore").strip()
    except Exception as e:
        rc, tail = 1, str(e)
    if rc == 0:
        _p("OK", "依赖探测: python -c \"{}\" 通过 (rc=0)".format(code))
    else:
        _p("WARN", "依赖探测: python -c \"{}\" 失败 (rc={}) :: {}".format(
            code, rc, tail.splitlines()[-1] if tail else ""))
    return rc

def main():
    print("=== ACRPA 暗黑模式修复 + 互联按钮自测 (静态断言) ===")
    check_utils()
    check_hardcoded_colors()
    check_acrpa_cursor_and_button()
    check_netlink_untouched()
    check_mini_bar_guards()
    check_theme_refresh_regressions()
    probe_deps()
    print("=== 结果: {} ===".format(
        "FAIL ({})".format(len(FAILS)) if FAILS else "OK"))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
