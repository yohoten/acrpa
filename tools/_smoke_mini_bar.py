#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Mini Bar 优化 静态自测脚本 (不弹窗, 退出码 0/1, 输出 [OK]/[WARN]/[FAIL])。

用法:
    python tools/_smoke_mini_bar.py            # 仅静态 AST 断言
    python tools/_smoke_mini_bar.py --launch   # 额外做启动存活冒烟 (会短暂开窗)

覆盖:
  A. src/state.py         — _config_schema 含 mini_bar_height / mini_bar_pos
  B. src/settings_window.py — _apply_map 注册 "system" / _apply_system_settings /
                              _track_card_vars("system", ...)
  C. src/ACRPA.py          — Mini Bar 函数无字面量 430/560 / 有 _mb_animate_width /
                              有 after_cancel / _sync_mini_bar_status 签名含主题色
  D. src/ACRPA.py          — NetLink 集成块未被改动 (open_devlink / set_control_hooks /
                              netlink.start_netlink 各 1 次)
"""
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAILS = []


def _p(status, msg):
    print("[{}] {}".format(status, msg))


def _read(rel):
    with open(os.path.join(ROOT, rel), "r", encoding="utf-8") as f:
        return f.read()


def _parse(rel):
    return ast.parse(_read(rel), filename=rel)


# Mini Bar 已抽到 src/mini_bar.py (拆分第一阶段): 针对它的静态断言必须同时看两个文件,
# 否则"函数搬走了"会被误判成"函数没了"。
UI_SOURCES = ("src/ACRPA.py", "src/mini_bar.py")


def _read_ui():
    return "\n".join(_read(r) for r in UI_SOURCES
                     if os.path.exists(os.path.join(ROOT, r)))


def _parse_ui():
    return ast.parse(_read_ui(), filename="<ACRPA.py+mini_bar.py>")


def _iter_funcs(tree):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node


_MB_FUNCS = ("_create_mini_bar", "_destroy_mini_bar", "_sync_mini_bar_status",
             "_mb_set_icon", "_mb_target_size", "_mb_clamp_pos", "_mb_save_pos",
             "_mb_set_size", "_mb_animate_width", "_mb_relayout", "_mb_dot_color",
             "_mb_set_dot", "_mb_breathe_tick", "_mb_blink_tick", "_mb_flash_alert",
             "_mb_start_dot_anim", "_mb_schedule_idle_check", "_mb_idle_check",
             "_mb_apply_form", "_mb_touch")


def check_state():
    """A. state.py schema 追加两键。"""
    tree = _parse("src/state.py")
    keys = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "_config_schema":
                    if isinstance(node.value, ast.List):
                        for el in node.value.elts:
                            if isinstance(el, ast.Tuple) and el.elts:
                                first = el.elts[0]
                                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                                    keys.add(first.value)
    for k in ("mini_bar_height", "mini_bar_pos"):
        if k in keys:
            _p("OK", "state._config_schema 含 '{}'".format(k))
        else:
            FAILS.append("state._config_schema 缺 '{}'".format(k))
            _p("FAIL", "state._config_schema 缺 '{}'".format(k))


def check_settings():
    """B. settings_window.py 系统卡统一收口。"""
    tree = _parse("src/settings_window.py")
    funcs = {n.name for n in _iter_funcs(tree)}

    if "_apply_system_settings" in funcs:
        _p("OK", "settings_window 存在 _apply_system_settings")
    else:
        FAILS.append("缺 _apply_system_settings")
        _p("FAIL", "settings_window 缺 _apply_system_settings")

    # _apply_map["system"] = _apply_system_settings
    mapped = False
    tracked = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Subscript):
                    if (isinstance(t.value, ast.Name) and t.value.id == "_apply_map"
                            and isinstance(t.slice, ast.Constant)
                            and t.slice.value == "system"):
                        mapped = True
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name) and f.id == "_track_card_vars":
                if node.args and isinstance(node.args[0], ast.Constant) \
                        and node.args[0].value == "system":
                    tracked = True
    if mapped:
        _p("OK", '_apply_map 注册了 "system"')
    else:
        FAILS.append('_apply_map 未注册 "system"')
        _p("FAIL", '_apply_map 未注册 "system"')
    if tracked:
        _p("OK", '_track_card_vars("system", ...) 已注册')
    else:
        FAILS.append('未注册 _track_card_vars("system", ...)')
        _p("FAIL", '未注册 _track_card_vars("system", ...)')


def check_mini_bar():
    """C. Mini Bar 静态断言 (源码分布在 ACRPA.py 与 mini_bar.py 两处)。"""
    src = _read_ui()
    tree = _parse_ui()
    funcs = {n.name: n for n in _iter_funcs(tree)}

    # C1. Mini Bar 相关函数中不得出现字面量 430 / 560
    bad = []
    for name, node in funcs.items():
        if name not in _MB_FUNCS:
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Constant) and isinstance(sub.value, int) \
                    and not isinstance(sub.value, bool) and sub.value in (430, 560):
                bad.append("{}:{}".format(name, sub.value))
    if bad:
        FAILS.append("Mini Bar 函数仍有字面量 430/560: {}".format(", ".join(bad)))
        _p("FAIL", "Mini Bar 函数仍有字面量 430/560: {}".format(", ".join(bad)))
    else:
        _p("OK", "Mini Bar 函数无字面量 430/560")

    # C2. 存在 _mb_animate_width
    if "_mb_animate_width" in funcs:
        _p("OK", "存在 _mb_animate_width")
    else:
        FAILS.append("缺 _mb_animate_width")
        _p("FAIL", "缺 _mb_animate_width")

    # C3. 存在 after_cancel 调用
    has_cancel = any(
        isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and n.func.attr == "after_cancel" for n in ast.walk(tree))
    if has_cancel:
        _p("OK", "存在 after_cancel 调用 (定时器可取消)")
    else:
        FAILS.append("缺 after_cancel 调用")
        _p("FAIL", "缺 after_cancel 调用")

    # C4. _sync_mini_bar_status 签名比较含 C["..."] 主题色
    node = funcs.get("_sync_mini_bar_status")
    theme_hits = []
    if node is not None:
        for sub in ast.walk(node):
            if isinstance(sub, ast.Subscript) and isinstance(sub.value, ast.Name) \
                    and sub.value.id == "C" and isinstance(sub.slice, ast.Constant) \
                    and isinstance(sub.slice.value, str):
                theme_hits.append(sub.slice.value)
    need = {"bgc", "sc", "fgm", "dg", "wn"}
    hit = need.intersection(theme_hits)
    if hit == need:
        _p("OK", "_sync_mini_bar_status 签名含主题色 C[...] {}".format(sorted(hit)))
    else:
        FAILS.append("_sync_mini_bar_status 缺主题色引用: {}".format(sorted(need - hit)))
        _p("FAIL", "_sync_mini_bar_status 缺主题色引用: {}".format(sorted(need - hit)))


def check_netlink_untouched():
    """D. NetLink 集成块未被改动 (各 1 次)。"""
    tree = _parse("src/ACRPA.py")
    counts = {"open_devlink": 0, "set_control_hooks": 0, "start_netlink": 0}
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef) and n.name == "open_devlink":
            counts["open_devlink"] += 1
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


def probe_deps():
    """依赖探测: 返回缺失模块列表。"""
    missing = []
    for mod in ("pyautogui", "xlrd", "pyperclip"):
        try:
            __import__(mod)
        except Exception:
            missing.append(mod)
    if missing:
        _p("WARN", "解释器缺少依赖: {} (启动冒烟将跳过)".format(", ".join(missing)))
    else:
        _p("OK", "依赖探测通过 (pyautogui/xlrd/pyperclip)")
    return missing


def launch_smoke():
    """启动存活冒烟: subprocess 跑 run.py, 10s 后仍存活则视为无导入/构建期异常。"""
    import subprocess
    import time
    cmd = [sys.executable, os.path.join(ROOT, "run.py")]
    try:
        proc = subprocess.Popen(cmd, cwd=ROOT,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    except Exception as e:
        _p("FAIL", "启动 run.py 失败: {}".format(e))
        FAILS.append("启动 run.py 失败: {}".format(e))
        return
    time.sleep(10)
    alive = proc.poll() is None
    out = b""
    if not alive:
        try:
            out = proc.stdout.read() or b""
        except Exception:
            pass
    try:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
    except Exception:
        pass
    if alive:
        _p("OK", "启动存活冒烟: run.py 存活 10s (无导入/构建期异常)")
    else:
        tail = out.decode("utf-8", "ignore")[-600:]
        FAILS.append("run.py 提前退出:\n{}".format(tail))
        _p("FAIL", "启动存活冒烟: run.py 提前退出:\n{}".format(tail))


def main():
    do_launch = "--launch" in sys.argv[1:]
    print("=== ACRPA Mini Bar 自测 (静态 AST 断言) ===")
    check_state()
    check_settings()
    check_mini_bar()
    check_netlink_untouched()
    missing = probe_deps()
    if do_launch:
        if missing:
            _p("WARN", "依赖缺失, 启动存活冒烟跳过: {}".format(", ".join(missing)))
        else:
            launch_smoke()
    print("=== 结果: {} ===".format("FAIL ({})".format(len(FAILS)) if FAILS else "OK"))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
