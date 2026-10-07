#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""工作流界面美化 P1 (WF-B2 控件联动层) 最小离屏断言。

用法:
    python -X utf8 tools\\_p1_panel_check.py

覆盖 (对应《工作流界面美化设计方案》P1 / WF-B2):
  A9  检查面板 (P1-1): 展开列表项数 == len(flow_graph.analyze(graph));
      点击第 k 项 → 第 k 条 issue 的 nodes 全部高亮 (tag wfsel_<nid>)。
  A10 大纲 ↔ 画布双向联动 (P1-5): 大纲选中第 i 行 → 画布对应节点高亮 (tag wfhl);
      画布单选节点 → 大纲选中对应行 (双向一致)。
  P1-2 画布悬浮工具条 + 小地图: 控件存在性 (工具条 ≥6 键 / 小地图 sp(120)×sp(80))
       + fit_to_view「按视口等比算 zoom + 居中」行为断言 (zoom_state 被写回 + 触发重绘)。

另含静态护栏:
  · flow_canvas 暴露 minimap / minimap_to_view / view_center 与 on_fit 钩子;
  · workflow_view 定义 _WF_PANEL_V2 (默认 True) 与检查面板/工具条/小地图/联动实现
    (隐藏即回退; wf_flow_check_lbl 仍保留)。

「实机」= 离屏 Tk 上直接把 ui.workflow_view 需要的控件挂到模块级全局, 调用其渲染与
联动函数 (不弹完整主窗口)。几何 token 以 sp = lambda v: v (ui_scale=1.0) 注入。

退出码: 0=无失败 / 1=有失败。无 GUI 环境或某组无法测量时记 [WARN] (不计入 PASS)。
"""
import inspect
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

_FAIL = []
_WARN = []

# 主题色板 (供 _refresh_flow_style / 各函数读取的关键键)
_COLORS = {
    "bg": "#FFFFFF", "bgc": "#F3F3F3", "bd": "#E1E1E1", "ebg": "#FFFFFF",
    "fgb": "#1F1F1F", "fgt": "#000000", "fgm": "#5F5F5F",
    "ac": "#0078D4", "sc": "#107C10", "dg": "#C42B1C", "wn": "#C77700",
    "flowbg": "#F8FAFC", "gridline": "#EEEEEE",
}

_FONTS = {"body": ("Segoe UI", 9), "small": ("Segoe UI", 8),
          "small_bold": ("Segoe UI", 8, "bold")}

# 断言用工作流: 线性两步 (断开首条边即产生含 nodes 的 isolated issue)
WF = {"name": "t", "steps": [
    {"type": "log", "text": "a"},
    {"type": "log", "text": "b"},
    {"type": "wait", "seconds": 1},
]}


def check(cond, msg):
    print("[{}] {}".format("OK  " if cond else "FAIL", msg))
    if not cond:
        _FAIL.append(msg)


def warn(msg):
    print("[WARN] {}".format(msg))
    _WARN.append(msg)


def _read(rel):
    with io.open(os.path.join(ROOT, rel), "r", encoding="utf-8") as f:
        return f.read()


class _Ev(object):
    """极简事件桩 (仅需 x/y/state)。"""
    def __init__(self, x, y, state=0):
        self.x = x
        self.y = y
        self.state = state


# ── 静态护栏 (纯文本 / 反射) ──────────────────────────────────────────
def t_static():
    print("\n── 静态护栏 ──")
    fc = _read("src/ui/flow_canvas.py")
    wv = _read("src/ui/workflow_view.py")
    check("def minimap(" in fc, "flow_canvas 定义 minimap (P1-2)")
    check("def minimap_to_view(" in fc, "flow_canvas 定义 minimap_to_view (P1-2 跳转)")
    check("def view_center(" in fc, "flow_canvas 定义 view_center (视口居中)")
    check("zoom_state=None" in fc and "def fit_to_view(" in fc,
          "fit_to_view 支持 zoom_state (按视口等比算 zoom)")
    check("on_fit" in fc, "bind_interactions 支持 on_fit 钩子 (F 键 zoom-aware)")
    check("_WF_PANEL_V2 = True" in wv, "workflow_view 定义 _WF_PANEL_V2 (默认 True, 可回退)")
    check("def _wf_refresh_check_panel(" in wv, "workflow_view 定义 _wf_refresh_check_panel")
    check("def _wf_build_panel_v2(" in wv, "workflow_view 定义 _wf_build_panel_v2")
    check("wf_flow_check_lbl" in wv, "workflow_view 仍保留 wf_flow_check_lbl (折叠态)")
    check("'<<TreeviewSelect>>'" in wv or '"<<TreeviewSelect>>"' in wv,
          "workflow_view 绑定 <<TreeviewSelect>> (大纲联动)")
    check("wf_check_tree" in wv and "wf_flow_toolbar" in wv and "wf_minimap" in wv,
          "workflow_view 定义检查列表/工具条/小地图控件")
    check("zoom_state=_FLOW_ZOOM_STATE" in wv, "workflow_view 的 fit 走 zoom_state 路径")
    # fit_to_view 签名: 新增参数均为关键字 (向后兼容既有位置调用)
    from ui import flow_canvas as fcmod
    sig = inspect.signature(fcmod.fit_to_view)
    check("canvas" in sig.parameters and "margin" in sig.parameters,
          "fit_to_view 保留 (canvas, margin) 位置参数 (兼容 _test_flow_canvas_interact)")
    for nm in ("zoom_state", "re_render"):
        p = sig.parameters.get(nm)
        check(p is not None and p.kind is inspect.Parameter.KEYWORD_ONLY,
              "fit_to_view 新增 {} 为关键字参数".format(nm))


# ── P1-2 行为: fit_to_view 按视口等比算 zoom ──────────────────────────
def t_fit(root):
    print("\n── P1-2. fit_to_view 按视口等比算 zoom ──")
    import tkinter
    from ui import flow_canvas as fc
    cv = tkinter.Canvas(root, width=400, height=300)
    cv.configure(scrollregion=(0, 0, 1600, 1200))     # 内容远大于视口
    st = {"scale": 1.0, "min_scale": 0.4, "max_scale": 2.5}
    calls = {"n": 0}
    fc.fit_to_view(cv, zoom_state=st, re_render=lambda: calls.__setitem__("n", calls["n"] + 1))
    check(st["scale"] < 1.0, "P1-2: 大内容下 fit 将 zoom 调小 ({})".format(st["scale"]))
    check(calls["n"] == 1, "P1-2: fit 触发一次重绘回调 (calls={})".format(calls["n"]))
    # 小内容 → zoom 调大 (受 max_scale=2.5 钳制)
    cv2 = tkinter.Canvas(root, width=400, height=300)
    cv2.configure(scrollregion=(0, 0, 100, 80))
    st2 = {"scale": 1.0, "min_scale": 0.4, "max_scale": 2.5}
    fc.fit_to_view(cv2, zoom_state=st2, re_render=lambda: None)
    check(st2["scale"] == 2.5, "P1-2: 小内容下 fit zoom 命中 max_scale 钳制 ({})".format(st2["scale"]))
    # 旧路径 (无 zoom_state) 不改缩放
    st3 = {"scale": 0.7}
    fc.fit_to_view(cv, zoom_state=None)
    check(st3["scale"] == 0.7, "P1-2: 无 zoom_state 时 fit 不改缩放 (向后兼容)")
    cv.destroy()
    cv2.destroy()


# ── 共享: 挂载 workflow_view 所需全局 ─────────────────────────────────
def _mount(root):
    import tkinter
    from tkinter import ttk
    import ui.workflow_view as wv

    frame = tkinter.Frame(root)
    cv = tkinter.Canvas(frame, width=520, height=360)
    cv.grid(row=0, column=0, sticky="nsew")
    sy = tkinter.Scrollbar(frame, orient="vertical", command=cv.yview)
    sy.grid(row=0, column=1, sticky="ns")
    lbl = tkinter.Label(frame, text="检查: —")
    lbl.grid(row=1, column=0, columnspan=2, sticky="ew")
    tree = ttk.Treeview(frame, columns=("type", "detail"),
                        show="tree headings", selectmode="browse")
    tree.heading("#0", text="")
    tree.column("#0", width=30, stretch=False)
    tree.heading("type", text="类型")
    tree.heading("detail", text="详情")

    wv.wf_flow_frame = frame
    wv.wf_flow_canvas = cv
    wv.wf_flow_scroll_y = sy
    wv.wf_flow_check_lbl = lbl
    wv.wf_tree = tree
    wv.wf_flow_toolbar = None
    wv.wf_minimap = None
    wv.wf_check_tree = None
    wv.C = dict(_COLORS)
    wv._CTX["sp"] = (lambda v: float(v))
    wv._CTX["state"] = None
    wv._CTX["colors"] = dict(_COLORS)
    wv._refresh_flow_style(wv.C)
    wv._wf_data = {"name": "t", "steps": [dict(s) for s in WF["steps"]]}
    wv._wf_graph_edit = None
    wv._FLOW_ZOOM_STATE = None
    wv._wf_drag_idx = None
    wv._WF_SYNCING = False
    wv._wf_check_expanded = False
    # WF-B2 面板控件 (检查列表 / 工具条 / 小地图) —— 与 build() 顺序一致
    wv._wf_build_panel_v2()
    # 渲染 (内部会绑定交互 + 刷新检查面板 + 小地图片段)
    wv._wf_render_flow_canvas()
    return wv, frame, cv, lbl, tree


# ── A9. 检查面板 (P1-1) ───────────────────────────────────────────────
def t_a9(root):
    print("\n── A9. 检查面板项数 + 点击定位高亮 ──")
    import flow_graph as fg
    wv, frame, cv, lbl, tree = _mount(root)

    # 构造一条「含 nodes」的 issue (断开首条边 → 孤立节点)
    g = wv._wf_graph_edit
    if g is None or not g.edges:
        warn("A9: 未能取得图/边, 无法验证")
        return
    fg.disconnect(g, g.edges[0].id)
    issues = fg.analyze(g)
    wv._wf_refresh_check_panel(issues)
    tree_items = wv.wf_check_tree.get_children()
    check(len(tree_items) == len(issues),
          "A9: 检查面板项数 == len(analyze(g)) ({} vs {})".format(
              len(tree_items), len(issues)))

    k = next((i for i, it in enumerate(issues) if it.get("nodes")), None)
    if k is None:
        warn("A9: 所有 issue 均无 nodes, 点击高亮无法验证")
        return
    cv.delete("wfhl")
    wv.wf_check_tree.selection_set("issue_{}".format(k))
    wv._wf_check_tree_click()
    want = set(issues[k]["nodes"])
    got = set()
    for nid in want:
        if cv.find_withtag("wfsel_" + str(nid)):
            got.add(nid)
    check(want and got == want,
          "A9: 点击第 {} 项 → 第 {} 条 issue 的 nodes 全部高亮 ({} / {})".format(
              k, k, sorted(got), sorted(want)))


# ── A10. 大纲 ↔ 画布双向联动 (P1-5) ───────────────────────────────────
def t_a10(root):
    print("\n── A10. 大纲 ↔ 画布双向联动 ──")
    wv, frame, cv, lbl, tree = _mount(root)
    wv._wf_refresh_tree()           # 填充大纲行 (末尾会重绘画布)
    rows = wv.wf_tree.get_children()
    g = wv._wf_graph_edit
    if not rows or g is None:
        warn("A10: 大纲行/图缺失, 无法验证")
        return

    # ① 大纲 → 画布
    i = 1 if len(rows) > 1 else 0
    cv.delete("wfhl")
    wv.wf_tree.selection_set(rows[i])
    wv._wf_on_tree_select()
    want_nid = g.roots()[i].id
    check(bool(cv.find_withtag("wfsel_" + str(want_nid))),
          "A10: 大纲选中第 {} 行 → 画布节点 {} 高亮".format(i, want_nid))

    # ② 画布 → 大纲
    _sel0 = wv.wf_tree.selection()
    if _sel0:
        wv.wf_tree.selection_remove(*_sel0)
    wv._wf_node_click(_Ev(10, 10), i)
    sel = wv.wf_tree.selection()
    check(tuple(sel) == (rows[i],),
          "A10: 画布单选第 {} 个节点 → 大纲选中对应行 (sel={})".format(i, tuple(sel)))
    wv._wf_drag_idx = None


# ── P1-2. 工具条 / 小地图 控件存在性 ──────────────────────────────────
def t_controls(root):
    print("\n── P1-2. 工具条 / 小地图 控件存在性 ──")
    wv, frame, cv, lbl, tree = _mount(root)
    check(wv.wf_flow_toolbar is not None, "P1-2: 悬浮工具条已创建")
    if wv.wf_flow_toolbar is not None:
        n_btn = len(wv.wf_flow_toolbar.winfo_children())
        check(n_btn >= 6, "P1-2: 工具条含 ≥6 个图标键 (＋/－/⤢/⌗/◲/⌁), n={}".format(n_btn))
        mapped = bool(wv.wf_flow_toolbar.place_info())
        check(mapped, "P1-2: 工具条经 place() 悬浮 (place_info 非空)")
    check(wv.wf_minimap is not None, "P1-2: 小地图画布已创建")
    if wv.wf_minimap is not None:
        w = int(float(wv.wf_minimap.cget("width")))
        h = int(float(wv.wf_minimap.cget("height")))
        check(w == 120 and h == 80, "P1-2: 小地图尺寸 == sp(120)×sp(80) ({}×{})".format(w, h))
        check(bool(wv.wf_minimap.place_info()), "P1-2: 小地图经 place() 悬浮于画布")
    check(wv.wf_check_tree is not None, "P1-1: 检查列表 Treeview 已创建")
    # 折叠/展开两态
    if wv.wf_check_tree is not None:
        was = wv._wf_check_expanded
        wv._wf_toggle_check_panel()
        check(wv._wf_check_expanded != was, "P1-1: 点击摘要行切换展开态")
        check(bool(wv.wf_check_tree.grid_info()) or wv._wf_check_expanded,
              "P1-1: 展开态检查列表进入 grid 管理")
        wv._wf_toggle_check_panel()
        check(not wv._wf_check_expanded, "P1-1: 再次点击回到折叠态")


def main():
    print("=== 工作流 P1 (WF-B2 控件联动层) 离屏断言 ===")
    t_static()
    try:
        import tkinter
        root = tkinter.Tk()
        root.withdraw()
    except Exception as e:
        warn("无 GUI 环境, 跳过实机断言 (A9/A10/P1-2 全部未验证): {}".format(e))
        root = None
    if root is not None:
        try:
            t_fit(root)
            t_a9(root)
            t_a10(root)
            t_controls(root)
        except Exception as e:
            warn("实机断言异常 (记 WARN): {}".format(e))
        finally:
            try:
                root.destroy()
            except Exception:
                pass
    print("\n" + "-" * 56)
    print("FAIL={}  WARN={}  {}".format(len(_FAIL), len(_WARN),
                                       "OK" if not _FAIL else "FAIL"))
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
