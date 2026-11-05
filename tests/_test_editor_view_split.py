#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""脚本编辑 Tab 抽取回归自测 (阶段二第 3 项第 3 步 S1-B: `src/ui/editor_view.py`)。

用法:
    .venv\\Scripts\\python.exe -X utf8 tests\\_test_editor_view_split.py

覆盖:
  A  模块存在/导出  : src/ui/editor_view.py 存在且导出 build / refresh_theme /
                     get_widgets 及 get_* 取回器齐全; 导入无副作用 (不建窗)
  B  ACRPA 接线     : ACRPA 导入 ui.editor_view + ui_editor_view.build(...) +
                     构建期 `tree = _ew["tree"]` 等别名取回; 编辑行为函数
                     (_preconfig_cmd_tags / _apply_editor_zoom / _build_sel_bar /
                     _bind_sel_bar / _bind_row_hover / _init_toolbar_buttons)
                     仍定义于 ACRPA.py (未迁, 作为构建期行为补调)
  C  去构建残留     : ACRPA.py 不再含原「TAB 1: 编辑」控件创建段
                     (tab_edit/toolbar/tree/legend_frame/_idle_bg_label/
                     _empty_hint/_editor_font 的 tkinter 创建)
  D  get_widgets 同源: ACRPA.tree is ui_editor_view.get_tree() 等 (真实 Tk)

退出码: 0 = 全部通过 / 1 = 有失败。无 GUI 环境的实机段记 [WARN] (不假通过)。
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

EXPORT_FUNCS = ("build", "refresh_theme", "get_widgets")

GETTER_FUNCS = ("get_tab_edit", "get_toolbar", "get_edit_file_label",
                "get_tb_scroll", "get_toolbar_canvas", "get_toolbar_scrollbar",
                "get_toolbar_inner", "get_toolbar_overflow_hint",
                "get_tree_frame", "get_tree", "get_tsy", "get_tsx",
                "get_legend_frame", "get_legend_inner", "get_editor_font")


def t_module_and_exports():
    print("\n── A. 模块存在性 / 导出 ──")
    path = os.path.join(SRC, "ui", "editor_view.py")
    check(os.path.exists(path), "src/ui/editor_view.py 存在")
    if not os.path.exists(path):
        return
    try:
        tree = _parse("src/ui/editor_view.py")
    except SyntaxError as e:
        check(False, "src/ui/editor_view.py 语法错误: {}".format(e))
        return
    funcs = _top_funcs(tree)
    for fn in EXPORT_FUNCS:
        check(fn in funcs, "editor_view 定义 def {}()".format(fn))
    missing_g = sorted(set(GETTER_FUNCS) - funcs)
    check(not missing_g, "editor_view 各 get_* 取回器齐全", "缺失={}".format(missing_g))
    try:
        import ui.editor_view as ev
        for nm in ("build", "refresh_theme", "get_widgets", "get_tree"):
            check(hasattr(ev, nm), "import ui.editor_view 后具备 {}".format(nm))
    except Exception as e:
        check(False, "import ui.editor_view 失败: {}".format(e))


# ── B. ACRPA 接线 + 编辑行为未迁 ─────────────────────────────────────

# 编辑行为函数: 仍定义于 ACRPA.py (构建期行为补调, 未迁 editor_view)
CORE_FUNCS = ("_preconfig_cmd_tags", "_apply_editor_zoom", "_build_sel_bar",
              "_bind_sel_bar", "_bind_row_hover", "_init_toolbar_buttons",
              "_sync_idle_bg", "_sync_empty_hint", "_idle_bg_on_configure",
              "_idle_bg_retheme", "_editor_zoom_step", "_editor_zoom_wheel",
              "_editor_zoom_reset", "_tree_columns_on_configure")


def t_acrpa_wiring():
    print("\n── B. ACRPA 接线 / 编辑行为未迁 ──")
    src = _read("src/ACRPA.py")
    check("from ui import editor_view as ui_editor_view" in src,
          "ACRPA 导入 ui.editor_view")
    check("ui_editor_view.build(" in src, "ACRPA 调用 ui_editor_view.build(...)")
    # 构建期别名取回
    for tok in ('tab_edit = _ew["tab_edit"]', 'tree = _ew["tree"]',
                'toolbar = _ew["toolbar"]', 'toolbar_canvas = _ew["toolbar_canvas"]',
                'toolbar_inner = _ew["toolbar_inner"]',
                'tree_frame = _ew["tree_frame"]',
                'legend_frame = _ew["legend_frame"]',
                '_editor_font = _ew["editor_font"]'):
        check(tok in src, "build_app 取回别名: {}".format(tok))

    tree = _parse("src/ACRPA.py")
    funcs = _top_funcs(tree)
    for fn in CORE_FUNCS:
        check(fn in funcs, "编辑行为 def {0}() 仍在 ACRPA.py (未迁)".format(fn))


# ── C. 去构建残留 (原构建段控件创建已迁出 ACRPA) ──────────────────────

BANNED_IN_ACRPA = (
    "tab_edit = tkinter.Frame(notebook",
    "tab_edit=tkinter.Frame(notebook",
    "toolbar = tkinter.Frame(tab_edit",
    "toolbar=tkinter.Frame(tab_edit",
    "edit_file_label = tkinter.Label(toolbar",
    "toolbar_canvas = tkinter.Canvas(tb_scroll",
    "toolbar_inner = tkinter.Frame(toolbar_canvas",
    "tree_frame = tkinter.Frame(tab_edit",
    "tree = ttk.Treeview(tree_frame",
    "tree=ttk.Treeview(tree_frame",
    "_idle_bg_label = tkinter.Label(tab_edit",
    "_empty_hint = tkinter.Label(tab_edit",
    "_editor_font = tkinter.font.Font(root=root",
    "legend_frame = tkinter.Frame(tab_edit",
)


def t_no_build_residue():
    print("\n── C. src/ACRPA.py 去构建残留 ──")
    a = _read("src/ACRPA.py")
    hits = [b for b in BANNED_IN_ACRPA if b in a]
    check(not hits, "ACRPA.py 不再含原编辑 Tab 控件创建", "残留={}".format(hits))
    ev = _read("src/ui/editor_view.py")
    for tok in ("tab_edit = tkinter.Frame(parent", "tree = ttk.Treeview(tree_frame",
                "_editor_font = tkinter.font.Font("):
        check(tok in ev, "editor_view 创建控件: {}".format(tok))


# ── D. 实机断言 (真实 Tk) ─────────────────────────────────────────────

def t_real_machine():
    print("\n── D. 实机 (真实 Tk) 断言 ──")
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
        import ui.editor_view as ev
    except Exception as e:
        warn("import app/ACRPA 失败, 跳过实机断言: {}".format(e))
        return
    rt = None
    try:
        rt = app.build()
        check(ACRPA.tree is not None, "实机: ACRPA.tree 非空")
        check(ACRPA.tree is ev.get_tree(), "实机: ACRPA.tree is editor_view.get_tree()")
        check(ACRPA.tab_edit is ev.get_tab_edit(), "实机: ACRPA.tab_edit is get_tab_edit()")
        check(ACRPA.toolbar_canvas is ev.get_toolbar_canvas(),
              "实机: ACRPA.toolbar_canvas is get_toolbar_canvas()")
        check(ACRPA._editor_font is ev.get_editor_font(),
              "实机: ACRPA._editor_font is get_editor_font()")
        check(ACRPA._IDLE_BG.get("label") is not None,
              "实机: _IDLE_BG['label'] 由 editor_view 回填")
        check(ACRPA._EMPTY_HINT.get("label") is not None,
              "实机: _EMPTY_HINT['label'] 由 editor_view 回填")
    except Exception as e:
        check(False, "实机构建/断言异常: {}".format(e))
    finally:
        try:
            if rt is not None:
                rt.root.destroy()
        except Exception:
            pass


def main():
    print("=== 脚本编辑 Tab 抽取回归 (阶段二第 3 项第 3 步 S1-B) ===")
    t_module_and_exports()
    t_acrpa_wiring()
    t_no_build_residue()
    t_real_machine()
    print("\n=== 结果: {} (通过 {}, 失败 {}, WARN {}) ===".format(
        "FAIL" if _FAIL else "OK", len(_PASS), len(_FAIL), len(_WARN)))
    if _FAIL:
        for m in _FAIL:
            print("  - {}".format(m))
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
