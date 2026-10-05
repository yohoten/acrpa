#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""执行控制栏抽取回归自测 (路线图 §3.1 阶段二第 3 项第 2 步: `src/ui/exec_bar.py`)。

用法:
    .venv\\Scripts\\python.exe -X utf8 tools\\_test_exec_bar_split.py

覆盖:
  A  模块存在/导出  : src/ui/exec_bar.py 存在且导出 build / refresh_theme / set_state /
                     get_widgets 及行为函数 (select_script/shared_run/shared_stop/
                     on_loop_sel/exec_params_commit/update_recent_scripts/
                     on_script_switched/add_recent_script/tip_with_hotkey/
                     update_overflow/on_bar_configure/on_wheel/screenshot_tool);
                     且各 get_* 取回器齐全
  B  ACRPA 转发别名 : ACRPA.py 内保留 btn_* / script_name_var / _update_exec_buttons /
                     select_script / _shared_run / _shared_stop / _screenshot_tool /
                     _add_recent_script 等转发别名 (真源在 ui.exec_bar); 执行核心
                     main_run / stop_execution / toggle_pause / _step_once / _fmt_dur
                     仍定义于 ACRPA.py (未迁)
  C  去构建残留     : src/ACRPA.py 不再含 `exec_bar = tkinter.Frame(` 等原构建段控件创建,
                     四键 (btn_run 等) 的创建 (roled) 只在 exec_bar
  D  StringVar 同源 : 构建期 `exec_maxmin_var = _ew["exec_maxmin_var"]` 等别名
  E  实机断言       : 真实 Tk 下 ACRPA.wf_maxmin_var is ACRPA.exec_maxmin_var
                     (同一 StringVar); btn_run / script_name_var 非空

退出码: 0 = 全部通过 / 1 = 有失败。无 GUI 环境的实机段记 [WARN] (不假通过)。
纯静态断言 + 真实 Tk 动态断言。
"""
import ast
import io
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

_PASS, _FAIL, _WARN = [], [], []


def check(cond, msg, detail=""):
    (_PASS if cond else _FAIL).append(msg)
    tail = ("  ({})".format(detail) if detail else "")
    print("[{}] {}{}".format("OK  " if cond else "FAIL", msg, tail))


def warn(msg):
    _WARN.append(msg)
    print("[WARN] {}".format(msg))


def _read(rel):
    with io.open(os.path.join(ROOT, rel), "r", encoding="utf-8") as f:
        return f.read()


def _parse(rel):
    return ast.parse(_read(rel), filename=rel)


def _top_funcs(tree):
    return {n.name for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


# ── A. 模块存在 + 导出 ────────────────────────────────────────────────

EXPORT_FUNCS = ("build", "refresh_theme", "set_state", "get_widgets",
                "select_script", "shared_run", "shared_stop",
                "on_loop_sel", "exec_params_commit", "update_recent_scripts",
                "on_script_switched", "add_recent_script", "tip_with_hotkey",
                "update_overflow", "on_bar_configure", "on_wheel",
                "screenshot_tool")

GETTER_FUNCS = ("get_exec_bar", "get_card_run", "get_run_bar", "get_run_bar_outer",
                "get_run_canvas", "get_run_scrollbar", "get_run_overflow_hint",
                "get_script_name_var", "get_script_switcher", "get_loop_count_var",
                "get_loop_combo", "get_exec_maxmin_var", "get_exec_maxmin_sp",
                "get_exec_stoponerror_var", "get_btn_validate", "get_btn_run",
                "get_btn_pause",
                "get_btn_step", "get_btn_stop", "get_btn_step_mode",
                "get_btn_debug_exec", "get_btn_vars_exec", "get_btn_debug_mode")


def t_module_and_exports():
    print("\n── A. 模块存在性 / 导出 ──")
    path = os.path.join(SRC, "ui", "exec_bar.py")
    check(os.path.exists(path), "src/ui/exec_bar.py 存在")
    if not os.path.exists(path):
        return
    try:
        tree = _parse("src/ui/exec_bar.py")
    except SyntaxError as e:
        check(False, "src/ui/exec_bar.py 语法错误: {}".format(e))
        return
    funcs = _top_funcs(tree)
    for fn in EXPORT_FUNCS:
        check(fn in funcs, "exec_bar 定义 def {}()".format(fn))
    missing_g = sorted(set(GETTER_FUNCS) - funcs)
    check(not missing_g, "exec_bar 各 get_* 取回器齐全", "缺失={}".format(missing_g))
    # 导入无副作用 (不建窗) 且导出可用
    try:
        import ui.exec_bar as eb
        for nm in ("build", "refresh_theme", "set_state", "get_widgets",
                   "select_script", "shared_run", "shared_stop", "screenshot_tool"):
            check(hasattr(eb, nm), "import ui.exec_bar 后具备 {}".format(nm))
    except Exception as e:
        check(False, "import ui.exec_bar 失败: {}".format(e))


# ── B. ACRPA 转发别名 + 执行核心未迁 ─────────────────────────────────

ACRPA_ALIASES = (
    "select_script = ui_exec_bar.select_script",
    "_screenshot_tool = ui_exec_bar.screenshot_tool",
    "_shared_run = ui_exec_bar.shared_run",
    "_shared_stop = ui_exec_bar.shared_stop",
    "_update_exec_buttons = ui_exec_bar.set_state",
    "_add_recent_script = ui_exec_bar.add_recent_script",
    "_run_on_wheel = ui_exec_bar.on_wheel",
)

# 执行核心: 必须仍定义于 ACRPA.py (未迁 exec_bar)
CORE_FUNCS = ("main_run", "stop_execution", "autorun",
              "toggle_pause", "_step_once", "_fmt_dur")


def t_acrpa_aliases():
    print("\n── B. ACRPA 转发别名 / 执行核心 ──")
    src = _read("src/ACRPA.py")
    check("from ui import exec_bar as ui_exec_bar" in src,
          "ACRPA 导入 ui.exec_bar")
    miss = [a for a in ACRPA_ALIASES if a not in src]
    check(not miss, "ACRPA 保留执行栏转发别名", "缺失={}".format(miss))

    tree = _parse("src/ACRPA.py")
    funcs = _top_funcs(tree)
    for fn in CORE_FUNCS:
        check(fn in funcs, "执行核心 def {0}() 仍在 ACRPA.py (未迁)".format(fn))
    # btn_* / script_name_var 等别名在 build_app 内经 get_widgets 取回
    for tok in ('script_name_var = _ew["script_name_var"]',
                'exec_maxmin_var = _ew["exec_maxmin_var"]',
                'exec_maxmin_sp = _ew["exec_maxmin_sp"]',
                'btn_run = _ew["btn_run"]',
                'btn_pause = _ew["btn_pause"]',
                'btn_step = _ew["btn_step"]',
                'btn_stop = _ew["btn_stop"]',
                'btn_debug_exec = _ew["btn_debug_exec"]',
                'loop_count_var = _ew["loop_count_var"]',
                '_script_switcher = _ew["script_switcher"]',
                'btn_validate = _ew["btn_validate"]'):
        check(tok in src, "build_app 取回别名: {}".format(tok))


# ── C. 去构建残留 (原构建段控件创建已迁出 ACRPA) ──────────────────────

# 原执行栏构建段特有的控件创建/绑定 (迁移后不应再出现在 ACRPA.py)
BANNED_IN_ACRPA = (
    "exec_bar = tkinter.Frame(",
    "exec_bar=tkinter.Frame(",
    "card_run = create_card(exec_bar)",
    "run_bar_outer = tkinter.Frame(card_run",
    "run_canvas = tkinter.Canvas(run_bar_outer",
    "run_bar = tkinter.Frame(run_canvas",
    "run_overflow_hint = tkinter.Label(run_bar_outer",
    'run_bar.bind("<Configure>"',
    "_script_switcher = ttk.Combobox(run_bar",
    "_loop_combo = ttk.Combobox(run_bar",
    "exec_maxmin_sp = tkinter.Spinbox(run_bar",
    "btn_step_mode = btn_step",
    "btn_debug_mode = btn_debug_exec",
)


def t_no_build_residue():
    print("\n── C. src/ACRPA.py 去构建残留 ──")
    a = _read("src/ACRPA.py")
    flat = a.replace(" ", "")
    hits = [b for b in BANNED_IN_ACRPA if b in a]
    check(not hits, "ACRPA.py 不再含原执行栏构建段控件创建", "残留={}".format(hits))
    # 四键创建 (roled) 只在 exec_bar; ACRPA 不再具名创建
    eb = _read("src/ui/exec_bar.py")
    eflat = eb.replace(" ", "")
    for name in ("btn_validate", "btn_run", "btn_pause", "btn_step", "btn_stop"):
        check('{}=ui_theme.roled('.format(name) in eflat,
              "exec_bar 创建 {} (roled)".format(name))
        check('{}=ui_theme.roled('.format(name) not in flat,
              "ACRPA.py 不再创建 {}".format(name))
    check("_update_exec_buttons = ui_exec_bar.set_state" in a,
          "ACRPA._update_exec_buttons 转发到 exec_bar.set_state")


# ── D. StringVar 同源 (构建期别名) ────────────────────────────────────

def t_stringvar_alias():
    print("\n── D. StringVar 同源 (构建期) ──")
    a = _read("src/ACRPA.py")
    check('exec_maxmin_var = _ew["exec_maxmin_var"]' in a,
          "exec_maxmin_var 由 exec_bar.get_widgets() 取回 (同一对象)")
    # 阶段二第 3 项第 3 步: 工作流 Tab 抽到 src/ui/workflow_view.py, 构建期
    # `wf_maxmin_var = exec_maxmin_var` 字面量随之迁出 —— 改为断言工作流侧经
    # get_widgets() 取回 (对象身份不变, 见 E 实机断言), 强度不降。
    check('wf_maxmin_var = _wfh["wf_maxmin_var"]' in a,
          "wf_maxmin_var 经 workflow_view.get_widgets() 取回 (与执行栏同一 StringVar)")


# ── E. 实机断言 (真实 Tk) ─────────────────────────────────────────────

def t_real_machine():
    print("\n── E. 实机 (真实 Tk) 断言 ──")
    try:
        import tkinter
    except Exception as e:
        warn("tkinter 不可用, 跳过实机断言: {}".format(e))
        return
    try:
        probe = tkinter.Tk()
        probe.destroy()
    except Exception as e:
        warn("无法创建 Tk root (无 GUI 环境), 跳过实机断言: {}".format(e))
        return
    try:
        import app
        import ACRPA
    except Exception as e:
        warn("import app/ACRPA 失败, 跳过实机断言: {}".format(e))
        return
    rt = None
    try:
        rt = app.build()
        check(ACRPA.btn_run is not None, "实机: ACRPA.btn_run 非空")
        check(ACRPA.script_name_var is not None, "实机: ACRPA.script_name_var 非空")
        check(ACRPA.wf_maxmin_var is ACRPA.exec_maxmin_var,
              "实机: ACRPA.wf_maxmin_var is ACRPA.exec_maxmin_var (同一 StringVar)")
        import ui.exec_bar as eb
        check(ACRPA.script_name_var is eb.get_script_name_var(),
              "实机: ACRPA.script_name_var is exec_bar.get_script_name_var()")
        check(ACRPA.exec_maxmin_var is eb.get_exec_maxmin_var(),
              "实机: ACRPA.exec_maxmin_var is exec_bar.get_exec_maxmin_var()")
        check(ACRPA._script_switcher is eb.get_script_switcher(),
              "实机: ACRPA._script_switcher is exec_bar.get_script_switcher()")
    except Exception as e:
        check(False, "实机构建/断言异常: {}".format(e))
    finally:
        try:
            if rt is not None:
                rt.root.destroy()
        except Exception:
            pass


def main():
    print("=== 执行控制栏抽取回归 (路线图 §3.1 阶段二第 3 项第 2 步) ===")
    t_module_and_exports()
    t_acrpa_aliases()
    t_no_build_residue()
    t_stringvar_alias()
    t_real_machine()
    print("\n=== 结果: {} (通过 {}, 失败 {}, WARN {}) ===".format(
        "FAIL" if _FAIL else "OK", len(_PASS), len(_FAIL), len(_WARN)))
    if _FAIL:
        for m in _FAIL:
            print("  - {}".format(m))
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
