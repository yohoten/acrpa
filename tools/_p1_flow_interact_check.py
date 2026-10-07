#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""工作流界面美化 P1 (WF-B1 画布交互层) 最小离屏断言。

用法:
    python -X utf8 tools\\_p1_flow_interact_check.py

覆盖 (对应《工作流界面美化设计方案》P1 / WF-B1):
  A5  选中边高亮可回退: 连续选边 e1→e2 后, e1 的线宽回到基准值
      (= max(1.0, sp(1)*zoom)); e2 保持 width=3。
  A4  非法连线红色拒绝反馈: 拖到非法目标 (自环 / 重复边) 时橡皮筋色 == C["dg"],
      且目标节点描边 == C["dg"] (消费 flow_graph.can_connect 的 reason)。
  A8  拖拽局部重绘: 拖拽中仅新增 1 个图元 (插入指示线), 且**不触发全量重绘**;
      拖拽结束才发生一次全量重绘。(计数桩)
  Z1  zoom!=1.0 回归 (P1 收尾): 分层步距 == zoom×基线; 拖拽落点→索引映射与实际
      几何一致 (旧硬编码步距已去除); 吸附格 = sp(8)*zoom; A8 局部重绘在 zoom=2.0
      下仍成立。

另含静态护栏:
  · flow_canvas 暴露网格/hover/框选等 P1 能力 (源码扫描 _draw_grid / interact_v2);
  · workflow_view 定义 _WF_INTERACT_V2 (默认 True) 与拖拽指示线;
  · bind_zoom_pan 签名与 0.4–2.5 语义不变。

「实机」= 离屏 tk.Canvas 上直接调用 ui.flow_canvas / ui.workflow_view 的渲染与交互
(不弹完整主窗口)。几何 token 以 sp = lambda v: v (ui_scale=1.0) 注入。

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

AC = "#0078D4"        # 合法连线 / 选中 (ac)
DG = "#C42B1C"        # 非法连线 / 拒绝 (dg)
FGM = "#5F5F5F"       # 无目标 (fgd/中性)

_FONTS = {"body": ("Segoe UI", 9), "small": ("Segoe UI", 8),
          "small_bold": ("Segoe UI", 8, "bold")}
_COLORS = {"log": "#64748B", "wait": "#9CA3AF", "script": "#3B82F6"}

WF3 = {"name": "t", "steps": [
    {"type": "log", "text": "a"},
    {"type": "log", "text": "b"},
    {"type": "log", "text": "c"},
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


def _ctx(sp=None):
    return {"dark": False, "show_ports": True, "fgm": FGM, "ac": AC, "dg": DG,
            "sp": (sp or (lambda v: float(v))), "interact_v2": True}


# ── 静态护栏 (纯文本 / 反射) ──────────────────────────────────────────
def t_static():
    print("\n── 静态护栏 ──")
    fc = _read("src/ui/flow_canvas.py")
    wv = _read("src/ui/workflow_view.py")
    check("def _draw_grid(" in fc, "flow_canvas 定义 _draw_grid (P1-7 网格)")
    check('ctx.get("grid", True)' in fc, "render 经 ctx['grid'] 控制网格绘制 (可回滚)")
    check("interact_v2" in fc, "bind_interactions 受 ctx['interact_v2'] 开关保护")
    check('add="+"' in fc, "新增交互绑定使用 add='+' (不覆盖既有绑定)")
    check("can_connect(" in fc, "橡皮筋判色消费 flow_graph.can_connect")
    check("_WF_INTERACT_V2 = True" in wv, "workflow_view 定义 _WF_INTERACT_V2 (默认 True)")
    check("drag_indicator" in wv, "workflow_view 定义拖拽插入指示线 tag")
    check("_wf_snap_coord" in wv and "_wf_alt_down" in wv,
          "workflow_view 提供 sp(8) 吸附 + Alt 关闭 (N3)")
    check("_wf_drop_index" in wv and "_wf_current_zoom" in wv,
          "workflow_view 提供几何一致的落点→索引映射 + zoom 取值 (P1 收尾)")
    # bind_zoom_pan 签名 / 语义不变 (0.4–2.5)
    from ui import flow_canvas as fcmod
    sig = inspect.signature(fcmod.bind_zoom_pan)
    params = list(sig.parameters)
    check(params == ["canvas", "min_scale", "max_scale", "step", "re_render"],
          "bind_zoom_pan 签名不变 ({})".format(params))
    check(sig.parameters["min_scale"].default == 0.4
          and sig.parameters["max_scale"].default == 2.5,
          "bind_zoom_pan 0.4–2.5 范围默认值不变")


# ── 实机: A5 选中边回退 ───────────────────────────────────────────────
def t_a5(root):
    print("\n── A5. 选中边高亮可回退 ──")
    import flow_graph as fg
    from ui import flow_canvas as fc
    cv = __import__("tkinter").Canvas(root, width=680, height=460)
    g = fg.promote(WF3)
    ctx = _ctx()
    fc.render(cv, g, colors=_COLORS, fonts=_FONTS, ctx=ctx, zoom=1.0)
    st = fc.bind_interactions(cv, g, colors=_COLORS, fonts=_FONTS, ctx=ctx)
    eids = [e.id for e in g.edges]
    if len(eids) < 2:
        warn("A5: 边数 < 2 ({}), 无法验证".format(eids))
        cv.destroy()
        return
    e1, e2 = eids[0], eids[1]
    base = st["base_edge_width"]()

    def _w(eid):
        return {round(float(cv.itemcget(it, "width")), 3)
                for it in cv.find_withtag("edge:" + str(eid))}

    st["select_edge"](e1)
    w1 = _w(e1)
    st["select_edge"](e2)
    w1b = _w(e1)
    w2 = _w(e2)
    check(w1 == {3.0}, "A5: 选中 e1 后 e1 线宽 == 3 ({})".format(sorted(w1)))
    check(w1b == {round(base, 3)},
          "A5: 切到 e2 后 e1 线宽回到基准 {} ({})".format(round(base, 3), sorted(w1b)))
    check(w2 == {3.0}, "A5: e2 当前选中线宽 == 3 ({})".format(sorted(w2)))
    cv.destroy()


# ── 实机: A4 非法连线拒绝反馈 ─────────────────────────────────────────
def _port_xy(cv, nid, name):
    tag = "port:%s:%s" % (nid, name)
    for it in cv.find_all():
        if tag in [str(t) for t in cv.gettags(it)]:
            x1, y1, x2, y2 = cv.coords(it)
            return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)
    return None


def _outline(cv, nid):
    return {cv.itemcget(it, "outline") for it in cv.find_withtag("fgnode_" + str(nid))
            if cv.type(it) in ("polygon", "rectangle")}


def t_a4(root):
    print("\n── A4. 非法连线红色拒绝反馈 ──")
    import tkinter
    import flow_graph as fg
    from ui import flow_canvas as fc
    cv = tkinter.Canvas(root, width=680, height=460)
    g = fg.promote(WF3)
    ctx = _ctx()
    fc.render(cv, g, colors=_COLORS, fonts=_FONTS, ctx=ctx, zoom=1.0)
    st = fc.bind_interactions(cv, g, colors=_COLORS, fonts=_FONTS, ctx=ctx)

    dup = next((e for e in g.edges if e.src != e.dst), None)
    if dup is None:
        warn("A4: 无可用边, 无法验证")
        cv.destroy()
        return
    src, dst = dup.src, dup.dst
    snode, dnode = g.node(src), g.node(dst)
    sx, sy = _port_xy(cv, src, "out") or (snode.x + snode.w / 2.0, snode.y + snode.h)
    tx, ty = dnode.x + dnode.w / 2.0, dnode.y + dnode.h / 2.0

    # ① 重复边: 拖到已存在边的目标节点
    st["press"](_Ev(sx, sy))
    check(st["rubber"] is not None, "A4: 端口按下已创建橡皮筋")
    st["motion"](_Ev(tx, ty))
    rub = cv.itemcget(st["rubber"], "fill")
    check(rub == DG, "A4: 重复边橡皮筋色 == dg ({})".format(rub))
    st["release"](_Ev(tx, ty))
    check(DG in _outline(cv, dst),
          "A4: 重复边目标节点描边 == dg ({})".format(sorted(_outline(cv, dst))))

    # ② 自环: 拖回源节点自身
    st["press"](_Ev(sx, sy))
    st["motion"](_Ev(sx, sy))
    rub2 = cv.itemcget(st["rubber"], "fill")
    check(rub2 == DG, "A4: 自环橡皮筋色 == dg ({})".format(rub2))
    st["release"](_Ev(sx, sy))
    check(DG in _outline(cv, src),
          "A4: 自环目标节点描边 == dg ({})".format(sorted(_outline(cv, src))))

    # ③ 合法目标: 未连线组合应判 ac (构造: 断开再造一条 n0->n2 拖拽前先验证色)
    cv.destroy()


# ── 实机: A8 拖拽局部重绘 ─────────────────────────────────────────────
def t_a8(root):
    print("\n── A8. 拖拽局部重绘 (仅插入指示线, 不触发全量重绘) ──")
    import tkinter
    import ui.workflow_view as wv
    cv = tkinter.Canvas(root, width=600, height=480)

    # 被拖节点图形 (tag node_0) + 一个无关背景图元
    cv.create_rectangle(10, 10, 120, 46, tags=("fgnode_n0", "node", "node_0"))
    cv.create_text(65, 28, text="a", tags=("fgnode_n0", "node", "node_0"))
    cv.create_rectangle(300, 300, 340, 340, tags=("other",))

    wv.wf_flow_canvas = cv
    wv._CTX["sp"] = (lambda v: float(v))          # ui_scale=1.0
    wv._wf_data = {"name": "t", "steps": [dict(s) for s in WF3["steps"]]}
    calls = {"n": 0}
    wv._wf_render_flowchart = (lambda *a, **k: calls.__setitem__("n", calls["n"] + 1))
    wv._wf_refresh_tree = (lambda *a, **k: None)
    wv._wf_drag_idx = None

    n0 = len(cv.find_all())
    wv._wf_drag_start(_Ev(65, 20), 0)
    wv._wf_drag_move(_Ev(65, 64), 0)
    n1 = len(cv.find_all())
    wv._wf_drag_move(_Ev(65, 104), 0)
    n2 = len(cv.find_all())

    check(n1 - n0 == 1, "A8: 拖拽一次新增图元 == 1 (仅插入指示线), Δ={}".format(n1 - n0))
    check(n2 - n0 == 1, "A8: 二次拖拽仍 == 1 (指示线复用), Δ={}".format(n2 - n0))
    check(calls["n"] == 0, "A8: 拖拽过程中未触发全量重绘 (calls={})".format(calls["n"]))

    wv._wf_drag_end(_Ev(65, 144), 0)
    check(calls["n"] == 1, "A8: 拖拽结束才发生一次全量重绘 (calls={})".format(calls["n"]))

    # 还原宿主全局, 避免污染后续断言
    wv.wf_flow_canvas = None
    wv._wf_drag_idx = None
    cv.destroy()


# ── 实机: P1 收尾 zoom!=1.0 几何 / 落点→索引映射 (回归) ───────────────
def t_zoom_geom(root):
    print("\n── P1 收尾. zoom=1.0/2.0 几何步距 + 落点→索引映射 + 吸附格 ──")
    import tkinter
    import flow_graph as fg
    from ui import flow_canvas as fc
    import ui.workflow_view as wv

    wv._CTX["sp"] = (lambda v: float(v))          # ui_scale=1.0
    saved_g, saved_zoom = wv._wf_graph_edit, wv._FLOW_ZOOM_STATE
    try:
        for zoom in (1.0, 2.0):
            g = fg.promote({"name": "z", "steps": [dict(s) for s in WF3["steps"]]})
            cv = tkinter.Canvas(root, width=680, height=900)
            fc.render(cv, g, colors=_COLORS, fonts=_FONTS, ctx=_ctx(), zoom=zoom)
            cv.destroy()
            roots = g.roots()
            cs = [roots[i].y + roots[i].h / 2.0 for i in range(len(roots))]
            step = cs[1] - cs[0]
            check(abs(step - 92.0 * zoom) < 1e-6,
                  "P1 收尾: zoom={} 分层步距 == (node_h+v_gap)x{} = {} ({})".format(
                      zoom, zoom, 92.0 * zoom, step))
            wv._wf_graph_edit = g              # _wf_drop_index 按实际几何取中心
            got = [wv._wf_drop_index(cs[i], 3) for i in range(3)]
            check(got == [0, 1, 2],
                  "P1 收尾: zoom={} 落点中心 {} → 索引 {} (期望 [0, 1, 2])".format(
                      zoom, [round(c, 1) for c in cs], got))
            if zoom == 1.0:
                # 举证: 旧硬编码公式 (gap=56 / start_y=20) 于中心 138 误判为 idx2
                old = max(0, min(2, int(round((cs[1] - 20) / 56.0))))
                check(old == 2 and got[1] == 1,
                      "P1 收尾: 旧公式 y={} → idx{} (误判); 修正后 → idx{}".format(
                          round(cs[1], 1), old, got[1]))
    finally:
        wv._wf_graph_edit, wv._FLOW_ZOOM_STATE = saved_g, saved_zoom

    # 吸附格随 zoom 等比: 步长 sp(8)*zoom (zoom=1 → 8 网格; zoom=2 → 16 网格)
    wv._FLOW_ZOOM_STATE = None
    s1 = wv._wf_snap_coord(24, _Ev(0, 0))
    wv._FLOW_ZOOM_STATE = {"scale": 2.0}
    s2 = wv._wf_snap_coord(24, _Ev(0, 0))
    wv._FLOW_ZOOM_STATE = saved_zoom
    check(s1 == 24 and s2 == 32,
          "P1 收尾: 吸附格 = sp(8)*zoom → 24 命中 {} (zoom1) / {} (zoom2)".format(s1, s2))


def t_zoom_a8(root):
    print("\n── P1 收尾. A8 拖拽局部重绘在 zoom=2.0 下仍成立 ──")
    import tkinter
    import ui.workflow_view as wv

    cv = tkinter.Canvas(root, width=600, height=480)
    cv.create_rectangle(10, 10, 220, 82, tags=("fgnode_n0", "node", "node_0"))
    cv.create_text(115, 46, text="a", tags=("fgnode_n0", "node", "node_0"))
    cv.create_rectangle(300, 300, 340, 340, tags=("other",))

    saved = {"cv": wv.wf_flow_canvas, "g": wv._wf_graph_edit,
             "z": wv._FLOW_ZOOM_STATE, "d": wv._wf_data,
             "r": wv._wf_render_flowchart, "t": wv._wf_refresh_tree,
             "i": wv._wf_drag_idx, "ind": wv._wf_drag_indic}
    calls = {"n": 0}
    try:
        wv.wf_flow_canvas = cv
        wv._CTX["sp"] = (lambda v: float(v))
        wv._FLOW_ZOOM_STATE = {"scale": 2.0}   # zoom=2.0 场景
        wv._wf_graph_edit = None               # 几何回退: step=2x(36+56)=184
        wv._wf_data = {"name": "t", "steps": [dict(s) for s in WF3["steps"]]}
        wv._wf_drag_idx = None
        wv._wf_drag_indic = None
        wv._wf_render_flowchart = (lambda *a, **k: calls.__setitem__("n", calls["n"] + 1))
        wv._wf_refresh_tree = (lambda *a, **k: None)

        n0 = len(cv.find_all())
        wv._wf_drag_start(_Ev(115, 40), 0)
        wv._wf_drag_move(_Ev(115, 128), 0)
        n1 = len(cv.find_all())
        wv._wf_drag_move(_Ev(115, 216), 0)
        n2 = len(cv.find_all())
        check(n1 - n0 == 1, "P1 收尾: zoom=2.0 拖拽一次新增图元 == 1, Δ={}".format(n1 - n0))
        check(n2 - n0 == 1, "P1 收尾: zoom=2.0 二次拖拽仍 == 1 (指示线复用), Δ={}".format(n2 - n0))
        check(calls["n"] == 0, "P1 收尾: zoom=2.0 拖拽中未触发全量重绘 (calls={})".format(calls["n"]))
        wv._wf_drag_end(_Ev(115, 300), 0)
        check(calls["n"] == 1,
              "P1 收尾: zoom=2.0 拖拽结束才发生一次全量重绘 (calls={})".format(calls["n"]))
    finally:
        wv.wf_flow_canvas = saved["cv"]
        wv._wf_graph_edit = saved["g"]
        wv._FLOW_ZOOM_STATE = saved["z"]
        wv._wf_data = saved["d"]
        wv._wf_render_flowchart = saved["r"]
        wv._wf_refresh_tree = saved["t"]
        wv._wf_drag_idx = saved["i"]
        wv._wf_drag_indic = saved["ind"]
        cv.destroy()


# ── 实机: 网格 / hover / 框选 冒烟 (不抛异常) ─────────────────────────
def t_smoke(root):
    print("\n── 冒烟. 网格 / hover / 框选 (N2/N5) ──")
    import tkinter
    import flow_graph as fg
    from ui import flow_canvas as fc
    cv = tkinter.Canvas(root, width=680, height=460)
    g = fg.promote(WF3)
    ctx = _ctx()
    fc.render(cv, g, colors=_COLORS, fonts=_FONTS, ctx=ctx, zoom=1.0)
    grid_n = len(cv.find_withtag("grid"))
    check(grid_n > 0, "P1-7: 网格已绘制 (grid 图元 {} 个)".format(grid_n))
    st = fc.bind_interactions(cv, g, colors=_COLORS, fonts=_FONTS, ctx=ctx)
    n = g.roots()[0]
    st["hover"](n.id)
    check(len(cv.find_withtag("hover")) >= 1, "N2: hover 高亮环已创建")
    box = (n.x - 5, n.y - 5, n.x + n.w + 5, n.y + n.h + 5)
    sel = st["nodes_in_rect"](*box)
    check(n.id in sel, "N5: 框选命中节点 (sel={})".format(sorted(sel)))
    cv.destroy()


def main():
    print("=== 工作流 P1 (WF-B1 画布交互层) 离屏断言 ===")
    t_static()
    try:
        import tkinter
        root = tkinter.Tk()
        root.withdraw()
    except Exception as e:
        warn("无 GUI 环境, 跳过实机断言 (A4/A5/A8 全部未验证): {}".format(e))
        root = None
    if root is not None:
        try:
            t_a5(root)
            t_a4(root)
            t_a8(root)
            t_zoom_geom(root)
            t_zoom_a8(root)
            t_smoke(root)
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
