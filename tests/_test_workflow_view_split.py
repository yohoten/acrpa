#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""工作流 Tab 抽取回归自测 (路线图 §3.1 阶段二第 3 项第 3 步: `src/ui/workflow_view.py`)。

用法:
    .venv\\Scripts\\python.exe -X utf8 tools\\_test_workflow_view_split.py

覆盖:
  A  模块存在/导出  : src/ui/workflow_view.py 存在且导出 build / refresh_theme /
                     get_widgets 及行为函数 (_wf_run/_wf_stop/_wf_refresh_tree/
                     _wf_update_workflow_highlight/_wf_toggle_view/_wf_add_step/
                     _wf_edit_step/_wf_export_screenshot/_wf_render_flowchart 等);
                     且各 get_* 取回器齐全
  B  ACRPA 转发别名 : ACRPA.py 内保留 tab_workflow/wf_tree/wf_flow_canvas/wf_paned/
                     _wf_show_flow/_wf_view_btn/_wf_refresh_tree/_wf_run/_wf_stop 等
                     转发别名 (真源在 ui.workflow_view); 领域层 workflow 未迁
  C  去构建残留     : src/ACRPA.py 不再含 `tab_workflow = tkinter.Frame(` /
                     `wf_flow_canvas = tkinter.Canvas(` / `wf_tree = ttk.Treeview(` 等
                     原构建段控件创建; 全部只在新模块
  D  去重复/死代码  : ACRPA.py 不再有 `def _wf_` 顶层函数; `_wf_stop` 不再重复定义;
                     `_wf_scheduler_callback` (死引用) 已绝迹
  E  _FLOW_COLORS    : 流程图色板仅在新模块定义; ACRPA.py 无 _FLOW_COLORS / 无 #9CA3AF
  F  实机断言       : 真实 Tk 下 ACRPA.tab_workflow / ACRPA.wf_tree 非空;
                     ACRPA.wf_maxmin_var is ACRPA.exec_maxmin_var (同一 StringVar);
                     ACRPA.wf_tree is ui.workflow_view.get_wf_tree()

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

EXPORT_FUNCS = ("build", "refresh_theme", "get_widgets",
                "_wf_update_overflow", "_wf_on_wheel", "_wf_flow_on_wheel",
                "_wf_toggle_view", "_wf_refresh_tree", "_wf_run", "_wf_stop",
                "_wf_new", "_wf_open", "_wf_save", "_wf_add_step", "_wf_edit_step",
                "_wf_delete_step", "_wf_move_up", "_wf_move_down",
                "_wf_export_template", "_wf_export_screenshot", "_wf_context_menu",
                "_wf_open_variable_manager", "_wf_update_workflow_highlight",
                "_wf_render_flowchart", "_draw_arrow")

GETTER_FUNCS = ("get_tab_workflow", "get_wf_toolbar", "get_wf_toolbar_outer",
                "get_wf_canvas", "get_wf_scrollbar", "get_wf_loop_var",
                "get_wf_maxmin_var", "get_wf_name_var", "get_wf_tree",
                "get_wf_paned", "get_wf_flow_canvas", "get_wf_flow_frame",
                "get_show_flow", "get_view_btn", "get_flow_colors")


def t_module_and_exports():
    print("\n── A. 模块存在性 / 导出 ──")
    path = os.path.join(SRC, "ui", "workflow_view.py")
    check(os.path.exists(path), "src/ui/workflow_view.py 存在")
    if not os.path.exists(path):
        return
    try:
        tree = _parse("src/ui/workflow_view.py")
    except SyntaxError as e:
        check(False, "src/ui/workflow_view.py 语法错误: {}".format(e))
        return
    funcs = _top_funcs(tree)
    for fn in EXPORT_FUNCS:
        check(fn in funcs, "workflow_view 定义 def {}()".format(fn))
    missing_g = sorted(set(GETTER_FUNCS) - funcs)
    check(not missing_g, "workflow_view 各 get_* 取回器齐全", "缺失={}".format(missing_g))
    # 导入无副作用 (不建窗) 且导出可用
    try:
        import ui.workflow_view as wv
        for nm in ("build", "refresh_theme", "get_widgets", "run", "stop",
                   "refresh_tree", "update_highlight", "toggle_view"):
            check(hasattr(wv, nm), "import ui.workflow_view 后具备 {}".format(nm))
    except Exception as e:
        check(False, "import ui.workflow_view 失败: {}".format(e))


# ── B. ACRPA 转发别名 + 构建期取回 ─────────────────────────────────────

ACRPA_ALIASES = (
    "from ui import workflow_view as ui_workflow_view",
    "_wf_update_workflow_highlight = ui_workflow_view.update_highlight",
    "_wf_on_wheel = ui_workflow_view.on_wheel",
    # 延迟构建 (v3 §5.2 P1): 需要控件树的入口改为「先 ensure 再转发」的包装函数
    "def _wf_ensure(",
    "def _wf_run(",
    "def _wf_stop(",
    "def _wf_toggle_view(",
    "def _wf_refresh_tree(",
    "_WF_LAZY = {",
    '_wfh = ui_workflow_view.get_widgets()',
    'wf_tree = _wfh["wf_tree"]',
    'wf_flow_canvas = _wfh["wf_flow_canvas"]',
    'wf_paned = _wfh["wf_paned"]',
    'wf_canvas = _wfh["wf_canvas"]',
    'wf_toolbar = _wfh["wf_toolbar"]',
    'wf_loop_var = _wfh["wf_loop_var"]',
    'wf_maxmin_var = _wfh["wf_maxmin_var"]',
    'wf_name_var = _wfh["wf_name_var"]',
    '_wf_show_flow = _wfh["_wf_show_flow"]',
    '_wf_view_btn = _wfh["_wf_view_btn"]',
)


def t_acrpa_aliases():
    print("\n── B. ACRPA 转发别名 / 构建期取回 ──")
    src = _read("src/ACRPA.py")
    miss = [a for a in ACRPA_ALIASES if a not in src]
    check(not miss, "ACRPA 保留工作流转发别名", "缺失={}".format(miss))
    # 领域层不迁
    check("from workflow import" in _read("src/ui/workflow_view.py"),
          "workflow_view 惰性引用领域层 workflow (不迁不改)")
    check("def parse_workflow" not in _read("src/ui/workflow_view.py")
          and "def run_workflow" not in _read("src/ui/workflow_view.py"),
          "领域层 parse_workflow/run_workflow 定义仍在 src/workflow.py (未迁)")


# ── C. 去构建残留 (原工作流构建段控件创建已迁出 ACRPA) ────────────────

BANNED_IN_ACRPA = (
    "wf_canvas = tkinter.Canvas(",
    "wf_flow_canvas = tkinter.Canvas(",
    "wf_tree = ttk.Treeview(",
    "wf_paned = tkinter.PanedWindow(",
    "wf_lib_tree = ttk.Treeview(",
    "_WF_LIB_ACTIONS = {",
    "wf_flow_canvas.configure(bg=",
)


def t_no_build_residue():
    print("\n── C. src/ACRPA.py 去构建残留 ──")
    a = _read("src/ACRPA.py")
    hits = [b for b in BANNED_IN_ACRPA if b in a]
    check(not hits, "ACRPA.py 不再含原工作流构建段控件创建", "残留={}".format(hits))
    # 延迟构建: 宿主只建「空占位 Tab」并 add, 控件树仍在 workflow_view 内
    check('tab_workflow = tkinter.Frame(notebook, bg=C["bg"])' in a
          and 'notebook.add(tab_workflow, text="  工作流  ")' in a,
          "ACRPA.py 仅创建空占位 Tab (延迟构建)")
    check("tab=tab_workflow" in a,
          "ACRPA.py 构建时把占位 Tab 交给 workflow_view 填充 (build(tab=...))")
    wv = _read("src/ui/workflow_view.py")
    for name in ("tab_workflow = tkinter.Frame(", "wf_flow_canvas = tkinter.Canvas(",
                 "wf_tree = ttk.Treeview(", "wf_paned = tkinter.PanedWindow(",
                 "_WF_LIB_ACTIONS = {"):
        check(name in wv, "workflow_view 创建 {}".format(name.split(" = ")[0]))


# ── D. 去重复定义 / 死代码 ────────────────────────────────────────────

def t_no_dup_deadcode():
    print("\n── D. 去重复定义 / 死引用清理 ──")
    a = _read("src/ACRPA.py")
    funcs = _top_funcs(_parse("src/ACRPA.py"))
    wf_funcs = sorted(n for n in funcs if n.startswith("_wf_"))
    # 延迟构建只允许「确保已构建 + 转发」的薄包装, 不允许再出现构建/业务实现
    allowed = {"_wf_ensure", "_wf_run", "_wf_stop", "_wf_toggle_view", "_wf_refresh_tree"}
    check(set(wf_funcs) == allowed,
          "ACRPA.py 的 _wf_* 顶层函数仅剩延迟构建薄包装",
          "实际={} 期望={}".format(wf_funcs, sorted(allowed)))
    check(a.count("def _wf_stop") == 1,
          "ACRPA.py 唯一 _wf_stop 定义 (延迟构建包装)", "count={}".format(a.count("def _wf_stop")))
    # 死引用 _wf_scheduler_callback: 定义与死调用 (_wf_update_status/_wf_update_flowchart/
    # _wf_update_log) 均已清除 (注释中提到该名不算)。
    check("def _wf_scheduler_callback" not in a
          and "_wf_update_status(" not in a and "_wf_update_flowchart(" not in a
          and "_wf_update_log(" not in a,
          "死引用 _wf_scheduler_callback 及其死调用已从 ACRPA.py 清除")
    check("def _wf_scheduler_callback" not in _read("src/ui/workflow_view.py"),
          "死引用 _wf_scheduler_callback 未迁入 workflow_view")
    # _add_sub/_del_sub 是 parallel/loop 两个互斥分支内的嵌套函数 (非同一作用域重复) ——
    # 迁移后应各出现 2 次 (两分支), 语义保留。
    wv = _read("src/ui/workflow_view.py")
    check(wv.count("def _add_sub(") == 2 and wv.count("def _del_sub(") == 2,
          "并行/循环两分支各自的内嵌 _add_sub/_del_sub 保留 (非缺陷)",
          "add={} del={}".format(wv.count("def _add_sub("), wv.count("def _del_sub(")))


# ── E. _FLOW_COLORS 仅在新模块 ────────────────────────────────────────

def t_flow_colors():
    print("\n── E. _FLOW_COLORS 归属 ──")
    a = _read("src/ACRPA.py")
    wv = _read("src/ui/workflow_view.py")
    check("_FLOW_COLORS" not in a, "ACRPA.py 不再出现 _FLOW_COLORS")
    check("#9CA3AF" not in a, "ACRPA.py 不再出现流程图硬编码 #9CA3AF")
    check("_FLOW_COLORS = {" in wv, "workflow_view 定义 _FLOW_COLORS 图形色板")
    check("#9CA3AF" in wv, "workflow_view 的 _FLOW_COLORS 含 #9CA3AF (wait 色, 刻意固定)")


# ── G. 延迟构建 (§5.2 P1) ─────────────────────────────────────────────

def t_lazy_build():
    print("\n── G. 工作流 Tab 延迟构建 ──")
    a = _read("src/ACRPA.py")
    wv = _read("src/ui/workflow_view.py")
    check("_WF_LAZY" in a and '"ensure"' in a,
          "ACRPA 定义 _WF_LAZY 守卫 (built/ok/ensure)")
    check("def _build_workflow_tab(" in a,
          "ACRPA 定义 _build_workflow_tab 延迟构建函数")
    check('if _WF_LAZY["built"]:' in a,
          "延迟构建幂等 (_WF_LAZY['built'] 守卫)")
    check('notebook.select() == str(tab_workflow)' in a
          and '"<<NotebookTabChanged>>"' in a,
          "首帧只建空 Tab, 切到工作流 Tab 才触发构建")
    check('_WF_LAZY["ensure"] = _build_workflow_tab' in a,
          "把构建入口注入 _WF_LAZY 供 _wf_ensure() 调用")
    check('if _WF_LAZY["ok"]:' in a,
          "_periodic 的工作流高亮按「已构建」守卫 (未构建时跳过)")
    check("def _wf_ensure(" in a and "_wf_ensure()" in a,
          "绕过 Tab 切换的入口 (run/stop/toggle/refresh) 先 ensure")
    check("tab=None" in wv and "if tab is not None:" in wv,
          "workflow_view.build 支持 host 传入占位 Tab (tab=...)")
    check("_bind_scroll_recursive(wf_toolbar, _wf_on_wheel)" in a,
          "构建后补绑工具栏滚轮 (原 _setup_scroll_bindings 的 wf 分支)")


# ── F. 实机断言 (真实 Tk) ─────────────────────────────────────────────

def t_real_machine():
    print("\n── F. 实机 (真实 Tk) 断言 ──")
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
        check(ACRPA.tab_workflow is not None, "实机: ACRPA.tab_workflow 非空 (空占位 Tab)")
        # ── 延迟构建: 启动后未切 Tab 时控件树应尚未创建 ──
        check(ACRPA.wf_tree is None,
              "实机: 启动后 wf_tree 仍为 None (工作流 Tab 未构建)")
        check(ACRPA.wf_flow_canvas is None,
              "实机: 启动后 wf_flow_canvas 仍为 None (延迟构建生效)")
        # ── 触发构建 (等价于用户切到该 Tab / IPC 入口 ensure) ──
        check(ACRPA._wf_ensure() is True, "实机: _wf_ensure() 成功构建工作流 Tab")
        check(ACRPA.wf_tree is not None, "实机: 构建后 wf_tree 非空")
        check(ACRPA.wf_flow_canvas is not None, "实机: 构建后 wf_flow_canvas 非空")
        check(ACRPA.wf_maxmin_var is ACRPA.exec_maxmin_var,
              "实机: ACRPA.wf_maxmin_var is ACRPA.exec_maxmin_var (同一 StringVar)")
        import ui.workflow_view as wv
        check(ACRPA.wf_tree is wv.get_wf_tree(),
              "实机: ACRPA.wf_tree is workflow_view.get_wf_tree()")
        check(ACRPA.tab_workflow is wv.get_tab_workflow(),
              "实机: ACRPA.tab_workflow is workflow_view.get_tab_workflow()")
        check(ACRPA.wf_maxmin_var is wv.get_wf_maxmin_var(),
              "实机: ACRPA.wf_maxmin_var is workflow_view.get_wf_maxmin_var()")
        # 幂等: 再次 ensure 不重建 (同一控件对象)
        _t1 = ACRPA.wf_tree
        ACRPA._wf_ensure()
        check(ACRPA.wf_tree is _t1, "实机: 重复 ensure 不重复构建 (幂等)")
    except Exception as e:
        check(False, "实机构建/断言异常: {}".format(e))
    finally:
        try:
            if rt is not None:
                rt.root.destroy()
        except Exception:
            pass


def main():
    print("=== 工作流 Tab 抽取回归 (路线图 §3.1 阶段二第 3 项第 3 步) ===")
    t_module_and_exports()
    t_acrpa_aliases()
    t_no_build_residue()
    t_no_dup_deadcode()
    t_flow_colors()
    t_lazy_build()
    t_real_machine()
    print("\n=== 结果: {} (通过 {}, 失败 {}, WARN {}) ===".format(
        "FAIL" if _FAIL else "OK", len(_PASS), len(_FAIL), len(_WARN)))
    if _FAIL:
        for m in _FAIL:
            print("  - {}".format(m))
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
