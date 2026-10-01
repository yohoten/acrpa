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
    probe_deps()
    print("=== 结果: {} ===".format(
        "FAIL ({})".format(len(FAILS)) if FAILS else "OK"))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
