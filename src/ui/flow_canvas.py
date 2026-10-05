# -*- coding: utf-8 -*-
"""flow_canvas — 工作流「图」的可视化渲染 + 缩放/平移 (路线图 阶段二 第 9 项 C 期)。

职责
----
把 `src/flow_graph.py` 产出的图 (节点 + 边) 画到 `tkinter.Canvas` 上, 并挂载缩放/平移
交互。**只渲染, 不改数据语义** —— 布局坐标写回节点的 x/y/w/h (供 to_dict 持久化)。

布局 (§11.5 Sugiyama 简化版)
---------------------------
    去回边 (back_edge / 自环不参与分层) → 分层 (Kahn 最长路径, 带环兜底松弛) →
    层内重心排序 (上下交替扫 3 轮) → 坐标分配 (每层居中, 行内按序)。

形状语义
--------
    condition      菱形 (polygon)
    parallel       容器框 (虚线外框, 表示并行容器)
    loop           范围框 (左侧竖向范围带) + 回边 (back_edge 曲线)
    start / end    圆角矩形 (由渲染合成, 不属于图数据)
    其余          圆角矩形节点

节点内「结构化两行」文本: 第一行 = 图标 + 类型 + 序号; 第二行 = 详情 (含 comment / 禁用标记)。

缩放 / 平移 (`bind_zoom_pan`)
---------------------------
    Ctrl + 滚轮   以光标为锚点缩放 (0.4x–2.5x)
    Shift + 滚轮  水平滚动
    中键拖拽      平移 (canvas.scan_*)
    普通滚轮      语义保留给调用方 (`ui.workflow_view._wf_flow_on_wheel` 的垂直滚动)

配色真源
--------
`colors` 由调用方传入 (任务约束: `_FLOW_COLORS` 仍归 `ui.workflow_view`), 本模块**不自建**
图形色板, 仅在缺省类型上回退一个中性灰。

对外接口
--------
    layout(graph, **kw) -> (width, height)
    render(canvas, graph, *, colors, fonts=None, ctx=None) -> dict(nodes/edges/width/height)
    bind_zoom_pan(canvas, *, min_scale=0.4, max_scale=2.5, step=1.12) -> state dict
"""
import math
import os

# 节点几何 (口径与 ui.workflow_view 的 _NODE_W/_NODE_H/_NODE_GAP 一致)
NODE_W = 200
NODE_H = 36
H_GAP = 46
V_GAP = 56
MARGIN = 28

FALLBACK_COLOR = "#6B7280"
DISABLED_COLOR = "#94A3B8"

# 独立使用时的兜底标签/图标 (调用方可经 ctx 覆盖为 workflow_view 的真源)
_TYPE_LABELS = {"script": "- 脚本", "parallel": "|| 并行", "condition": "? 条件",
                "wait": "~ 等待", "loop": "↻ 循环", "command": "↯ 命令",
                "variable": "✚ 变量", "log": "✉ 日志"}
_TYPE_ICONS = {"script": "[>]", "parallel": "⫼", "condition": "◇", "wait": "⧗",
               "loop": "↻", "command": "↯", "variable": "✚", "log": "✉"}

_ROWS = "fgnode_"


# ══════════════════════════════════════════════════════════════════════
# 布局
# ══════════════════════════════════════════════════════════════════════

def _forward_adjacency(graph):
    """→ (fwd, rev): 去掉回边/自环后的邻接表 (仅含图中存在的节点)。"""
    idset = {n.id for n in graph.nodes}
    fwd = {i: [] for i in idset}
    rev = {i: [] for i in idset}
    for e in graph.edges:
        if e.back_edge or e.src == e.dst:
            continue
        if e.src in idset and e.dst in idset:
            fwd[e.src].append(e.dst)
            rev[e.dst].append(e.src)
    return fwd, rev


def _assign_layers(ids, fwd, rev):
    """Kahn 最长路径分层; 若仍有残留 (意外环) 则做有限轮松弛兜底。"""
    layer = {i: 0 for i in ids}
    indeg = {i: len(rev[i]) for i in ids}
    queue = [i for i in ids if indeg[i] == 0]
    seen = 0
    head = 0
    while head < len(queue):
        u = queue[head]
        head += 1
        seen += 1
        for v in fwd[u]:
            if layer[u] + 1 > layer[v]:
                layer[v] = layer[u] + 1
            indeg[v] -= 1
            if indeg[v] == 0:
                queue.append(v)
    if seen != len(ids):                       # 环 (未被标为回边): 兜底松弛
        for _ in range(len(ids)):
            changed = False
            for u in ids:
                for v in fwd[u]:
                    if layer[v] < layer[u] + 1:
                        layer[v] = layer[u] + 1
                        changed = True
            if not changed:
                break
    return layer


def _barycenter_order(layers, fwd, rev):
    """层内重心排序 (下扫 + 上扫, 交替 3 轮; 无邻居者保持原位)。"""
    order = {}
    for L in sorted(layers):
        for k, nid in enumerate(layers[L]):
            order[nid] = k

    def _sweep(down):
        seq = sorted(layers) if down else sorted(layers, reverse=True)
        for L in seq:
            others = rev if down else fwd
            fixed = order
            row = layers[L]

            def key(nid):
                ns = others.get(nid) or []
                if not ns:
                    return fixed.get(nid, 0)
                return sum(fixed.get(m, 0) for m in ns) / float(len(ns))

            row.sort(key=key)
            for k, nid in enumerate(row):
                order[nid] = k

    for r in range(3):
        _sweep(r % 2 == 0)


def layout(graph, *, node_w=NODE_W, node_h=NODE_H, h_gap=H_GAP, v_gap=V_GAP,
           margin=MARGIN):
    """分层布局: 写回 node.x/y/w/h, 返回画布 (width, height)。"""
    ids = [n.id for n in graph.nodes]
    if not ids:
        return float(2 * margin + node_w), float(2 * margin + node_h)

    fwd, rev = _forward_adjacency(graph)
    layer = _assign_layers(ids, fwd, rev)

    layers = {}
    for n in graph.nodes:                      # 初始层内顺序 = 节点创建顺序
        layers.setdefault(layer[n.id], []).append(n.id)
    _barycenter_order(layers, fwd, rev)

    row_w = {}
    for L, row in layers.items():
        row_w[L] = len(row) * node_w + max(0, len(row) - 1) * h_gap
    total_w = max(row_w.values()) if row_w else node_w

    for L in sorted(layers):
        x0 = margin + (total_w - row_w[L]) / 2.0
        y = margin + L * (node_h + v_gap)
        for k, nid in enumerate(layers[L]):
            node = graph.node(nid)
            if node is None:
                continue
            node.x = float(x0 + k * (node_w + h_gap))
            node.y = float(y)
            node.w = float(node_w)
            node.h = float(node_h)

    n_layers = len(layers)
    width = float(2 * margin + total_w)
    height = float(2 * margin + n_layers * node_h + max(0, n_layers - 1) * v_gap)
    return width, height


# ══════════════════════════════════════════════════════════════════════
# 绘制辅助
# ══════════════════════════════════════════════════════════════════════

def _rounded_rect(canvas, x1, y1, x2, y2, r=8, **kw):
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
           x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return canvas.create_polygon(pts, smooth=True, **kw)


def _arrow_head(canvas, x, y, direction, color, tags="arrow"):
    """在 (x, y) 画一个指向 direction 的箭头 (direction: 'down'/'up'/'left'/'right')。"""
    s = 6
    if direction == "down":
        pts = (x - s, y - 2 * s, x + s, y - 2 * s, x, y)
    elif direction == "up":
        pts = (x - s, y + 2 * s, x + s, y + 2 * s, x, y)
    elif direction == "left":
        pts = (x + 2 * s, y - s, x + 2 * s, y + s, x, y)
    else:  # right
        pts = (x - 2 * s, y - s, x - 2 * s, y + s, x, y)
    canvas.create_polygon(*pts, fill=color, outline=color, tags=tags)


def _node_detail(node):
    """节点详情行 (与原单列渲染口径一致)。"""
    d = node.data
    typ = node.type
    if typ == "script":
        detail = os.path.basename(d.get("path", "") or "")[:20] or "未选择"
    elif typ == "parallel":
        detail = "{} 子步骤".format(len(node.body_ids()))
    elif typ == "condition":
        detail = (d.get("if", "") or "条件")[:18]
    elif typ == "wait":
        detail = "{}s".format(d.get("seconds", 1))
    elif typ == "loop":
        detail = "{}".format(d.get("times", "1"))[:18]
    elif typ == "command":
        detail = "{}".format(d.get("cmd", ""))[:18]
    elif typ == "variable":
        detail = "{}={}".format(d.get("var_name", ""), d.get("var_value", ""))[:20]
    elif typ == "log":
        detail = "{}".format(d.get("text", ""))[:18]
    else:
        detail = str(d)[:20]
    if node.comment:
        detail = "{} #{}".format(detail, node.comment[:8])
    if not node.enabled:
        detail = "○ " + detail
    return detail


def _draw_node(canvas, node, colors, fonts, ctx):
    typ = node.type
    x, y, w, h = node.x, node.y, node.w, node.h
    cx, cy = x + w / 2.0, y + h / 2.0

    index = _node_index(ctx, node.id)
    color = colors.get(typ, FALLBACK_COLOR)
    if not node.enabled:
        color = DISABLED_COLOR

    darken = ctx.get("darken")
    outline = darken(color) if callable(darken) else color
    width = 2
    if index is not None and ctx.get("current_step") == index:
        outline = ctx.get("sc") or outline
        width = 3
    elif index is not None and (ctx.get("step_results") or {}).get(index) == "error":
        outline = ctx.get("dg") or outline
        width = 3

    tags = ["node", _ROWS + node.id]
    itag = ctx.get("index_tags", {}).get(node.id)
    if itag:
        tags.append(itag)

    # 阴影 (主题相关)
    shadow = "#334155" if ctx.get("dark") else "#CBD5E0"
    shadow_id = canvas.create_rectangle(x + 2, y + 2, x + w + 2, y + h + 2,
                                        fill=shadow, outline="", tags=("shadow",))

    shape_id = None
    if typ == "condition":
        shape_id = canvas.create_polygon(
            cx, y - 2, x + w, cy, cx, y + h + 2, x, cy,
            fill=color, outline=outline, width=width, tags=tuple(tags))
    elif typ == "parallel":
        shape_id = canvas.create_rectangle(
            x, y, x + w, y + h, fill=color, outline=outline, width=width, tags=tuple(tags))
        canvas.create_rectangle(x + 4, y + 4, x + w - 4, y + h - 4,
                                outline="#FFFFFF", dash=(2, 2), tags=tuple(tags))
    elif typ == "loop":
        shape_id = canvas.create_rectangle(
            x, y, x + w, y + h, fill=color, outline=outline, width=width, tags=tuple(tags))
        canvas.create_rectangle(x + 3, y + 3, x + 10, y + h - 3,
                                fill="#FFFFFF", outline="", tags=tuple(tags))
    else:
        shape_id = _rounded_rect(canvas, x, y, x + w, y + h, 8,
                                 fill=color, outline=outline, width=width, tags=tuple(tags))

    if shape_id is not None:
        canvas.tag_lower(shadow_id, shape_id)

    icon = ctx.get("icons", {}).get(typ) or _TYPE_ICONS.get(typ, "?")
    label = ctx.get("type_labels", {}).get(typ) or _TYPE_LABELS.get(typ, typ)
    line1 = "{} {} #{}".format(icon, label, (index + 1) if index is not None else "-")
    text = "{}\n{}".format(line1, _node_detail(node))
    canvas.create_text(cx, cy, text=text, fill="#FFFFFF", justify="center",
                       anchor="center", font=fonts.get("small_bold"), tags=tuple(tags))


def _node_index(ctx, nid):
    """由调用方注入的 index_tags 反查顶层步骤序号 (仅供执行高亮/拖拽标签)。"""
    tag = (ctx.get("index_tags") or {}).get(nid)
    if not tag:
        return None
    digits = "".join(ch for ch in str(tag) if ch.isdigit())
    return int(digits) if digits else None


def _draw_forward_edge(canvas, e, s, d, color, fonts, ctx):
    sx, sy = s.x + s.w / 2.0, s.y + s.h
    dx, dy = d.x + d.w / 2.0, d.y
    canvas.create_line(sx, sy, dx, dy, fill=color, width=2, tags=("arrow",))
    _arrow_head(canvas, dx, dy, "down", color)
    if e.label:
        mx, my = (sx + dx) / 2.0, (sy + dy) / 2.0
        canvas.create_text(mx + 8, my, text=e.label, fill=color, anchor="w",
                           font=fonts.get("small"), tags=("arrow",))


def _draw_back_edge(canvas, e, s, d, color, fonts):
    if s.id == d.id:                            # 自环 (空 loop 的范围标记)
        x2, cy = s.x + s.w, s.y + s.h / 2.0
        canvas.create_line(x2, cy - 10, x2 + 26, cy, x2, cy + 10,
                           fill=color, width=2, smooth=True, dash=(4, 2),
                           arrow="last", tags=("arrow",))
        return
    sx, sy = s.x, s.y + s.h / 2.0               # 回边走左侧弓形
    dx, dy = d.x, d.y + d.h / 2.0
    bow = min(sx, dx) - 28
    canvas.create_line(sx, sy, bow, (sy + dy) / 2.0, dx, dy,
                       fill=color, width=2, smooth=True, dash=(4, 2),
                       arrow="last", tags=("arrow",))
    if e.label:
        canvas.create_text(bow - 4, (sy + dy) / 2.0, text=e.label, anchor="e",
                           fill=color, font=fonts.get("small"), tags=("arrow",))


def _draw_terminal(canvas, cx, cy, text, color, fonts, tags):
    w, h = 96, 30
    _rounded_rect(canvas, cx - w / 2.0, cy, cx + w / 2.0, cy + h, 15,
                  fill=color, outline=color, tags=tags)
    canvas.create_text(cx, cy + h / 2.0, text=text, fill="#FFFFFF",
                       font=fonts.get("small_bold"), tags=tags)
    return cy, cy + h


# ══════════════════════════════════════════════════════════════════════
# 渲染入口
# ══════════════════════════════════════════════════════════════════════

def render(canvas, graph, *, colors, fonts=None, ctx=None):
    """把 graph 画到 canvas 上, 返回统计 dict (nodes/edges/width/height)。"""
    colors = colors or {}
    fonts = fonts or {}
    ctx = ctx or {}
    canvas.delete("all")

    nodes = list(graph.nodes)
    if not nodes:
        canvas.create_text(200, 60, text="暂无步骤\n点击 [+ 脚本] 等按钮添加",
                           font=fonts.get("body"), fill=ctx.get("fgm", "#64748B"),
                           anchor="center", tags=("empty",))
        canvas.configure(scrollregion=(0, 0, 400, 150))
        return {"nodes": 0, "edges": 0, "width": 400, "height": 150}

    width, height = layout(graph)
    top = min(n.y for n in nodes)
    bottom = max(n.y + n.h for n in nodes)
    cx = width / 2.0
    start_y = top - NODE_H - 26
    end_y = bottom + 30

    fwd, _rev = _forward_adjacency(graph)
    roots = [n for n in graph.roots()]
    sinks = [n for n in nodes if not fwd.get(n.id)]

    # ── 边 (先画, 让节点覆盖在连线之上) ──
    for e in graph.edges:
        s, d = graph.node(e.src), graph.node(e.dst)
        if s is None or d is None:
            continue
        if e.back_edge:
            _draw_back_edge(canvas, e, s, d, colors.get("loop", "#8B5CF6"), fonts)
        else:
            _draw_forward_edge(canvas, e, s, d, ctx.get("fgm", "#94A3B8"), fonts, ctx)

    # ── start / end (渲染合成, 不写回图数据) ──
    term_color = ctx.get("fgm", "#94A3B8")
    _draw_terminal(canvas, cx, start_y, "START", term_color, fonts, ("terminal",))
    _draw_terminal(canvas, cx, end_y, "END", term_color, fonts, ("terminal",))
    for n in roots:
        x, y = n.x + n.w / 2.0, n.y
        canvas.create_line(cx, start_y + 30, x, y, fill=term_color, width=2,
                           arrow="last", tags=("arrow",))
    for n in sinks:
        x, y = n.x + n.w / 2.0, n.y + n.h
        canvas.create_line(x, y, cx, end_y, fill=term_color, width=2,
                           arrow="last", tags=("arrow",))

    # ── 节点 ──
    for n in nodes:
        _draw_node(canvas, n, colors, fonts, ctx)

    total_w = max(width, 400)
    total_h = end_y + 30 - start_y + 20
    canvas.configure(scrollregion=(0, start_y - 10, total_w, end_y + 40))
    return {"nodes": len(nodes), "edges": len(graph.edges),
            "width": total_w, "height": total_h}


# ══════════════════════════════════════════════════════════════════════
# 缩放 / 平移
# ══════════════════════════════════════════════════════════════════════

def _scrollregion(canvas):
    try:
        parts = str(canvas.cget("scrollregion")).split()
        if len(parts) == 4:
            return [float(p) for p in parts]
    except Exception:
        pass
    return None


def _refresh_scrollregion(canvas, pad=20):
    try:
        bb = canvas.bbox("all")
        if bb:
            canvas.configure(scrollregion=(bb[0] - pad, bb[1] - pad,
                                           bb[2] + pad, bb[3] + pad))
    except Exception:
        pass


def _anchor_view(canvas, ax, ay, wx, wy):
    """让画布坐标 (ax, ay) 落在控件坐标 (wx, wy) —— 缩放以光标为锚点。"""
    sr = _scrollregion(canvas)
    if not sr:
        return
    x0, y0, x1, y1 = sr
    tot_w = max(x1 - x0, 1.0)
    tot_h = max(y1 - y0, 1.0)
    fx = (ax - wx - x0) / tot_w
    fy = (ay - wy - y0) / tot_h
    try:
        canvas.xview_moveto(max(0.0, min(1.0, fx)))
        canvas.yview_moveto(max(0.0, min(1.0, fy)))
    except Exception:
        pass


def bind_zoom_pan(canvas, *, min_scale=0.4, max_scale=2.5, step=1.12):
    """挂载 Ctrl+滚轮缩放 / Shift+滚轮水平 / 中键拖拽平移; 返回状态 dict。

    普通 <MouseWheel> 不在此处绑定 —— 垂直滚动语义仍由调用方
    (`_wf_flow_on_wheel`) 保留, 本函数只增加更具体的修饰键分支。
    """
    st = {"scale": 1.0, "min_scale": min_scale, "max_scale": max_scale,
          "panning": False}

    def _clamp(v):
        return max(min_scale, min(max_scale, v))

    def _on_zoom(event):
        delta = getattr(event, "delta", 0)
        if not delta:
            return "break"
        new = _clamp(st["scale"] * (step if delta > 0 else 1.0 / step))
        if abs(new - st["scale"]) < 1e-9:
            return "break"
        try:
            ax = canvas.canvasx(event.x)
            ay = canvas.canvasy(event.y)
            ratio = new / st["scale"]
            canvas.scale("all", ax, ay, ratio, ratio)
            st["scale"] = new
            _refresh_scrollregion(canvas)
            _anchor_view(canvas, ax, ay, event.x, event.y)
        except Exception:
            pass
        return "break"

    def _on_shift_wheel(event):
        delta = getattr(event, "delta", 0)
        if not delta:
            return "break"
        try:
            canvas.xview_scroll(-1 if delta > 0 else 1, "units")
        except Exception:
            pass
        return "break"

    def _pan_start(event):
        st["panning"] = True
        try:
            canvas.scan_mark(event.x, event.y)
        except Exception:
            pass
        return "break"

    def _pan_move(event):
        if not st["panning"]:
            return "break"
        try:
            canvas.scan_dragto(event.x, event.y, gain=1)
        except Exception:
            pass
        return "break"

    def _pan_end(event):
        st["panning"] = False
        return "break"

    canvas.bind("<Control-MouseWheel>", _on_zoom, add="+")
    canvas.bind("<Shift-MouseWheel>", _on_shift_wheel, add="+")
    canvas.bind("<Button-2>", _pan_start, add="+")
    canvas.bind("<B2-Motion>", _pan_move, add="+")
    canvas.bind("<ButtonRelease-2>", _pan_end, add="+")
    st["handlers"] = (_on_zoom, _on_shift_wheel, _pan_start, _pan_move, _pan_end)
    return st
