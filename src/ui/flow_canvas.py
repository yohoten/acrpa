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

    render(canvas, graph, *, colors, fonts=None, ctx=None, zoom=1.0)
        -> dict(nodes/edges/width/height)
        zoom : 渲染期缩放乘子。zoom==1.0 时几何与设计空间逐位一致; zoom!=1.0 时
               布局几何 (节点/间距/margin) 按 zoom 整体等比缩放, 并经 layout() 把
               缩放后的坐标写回 graph.nodes (见 render docstring); 线宽/字号/端口
               半径按 zoom 线性补偿 (P0-4)。
        ctx  : 可含 "style"(结构色真源 _FLOW_STYLE) / "sp"(尺寸令牌) / "dark" 等。

    bind_zoom_pan(canvas, *, min_scale=0.4, max_scale=2.5, step=1.12, re_render=None)
        -> state dict (内部改「更新 zoom 状态 + 回调重绘」, 保留光标锚点语义)

    blend(color, other, ratio) -> "#RRGGBB"   (Tk canvas 无 alpha 的混色近似)
"""
import math
import os

# 节点几何 (口径与 ui.workflow_view 的 _NODE_W/_NODE_H/_NODE_GAP 一致)。
# 保留为**设计像素默认值** —— 实际尺寸由调用方经 ctx["sp"] 注入 sp(...) (P0-1),
# 直接调用 layout() 的既有路径 (无 sp) 仍得到这些字面量 (向后兼容)。
NODE_W = 200
NODE_H = 36
H_GAP = 46
V_GAP = 56
MARGIN = 28

# 缺省类型色 (类型色真源仍是 ui.workflow_view._FLOW_COLORS; 此处仅兜底)
FALLBACK_COLOR = "#6B7280"
# 兼容别名: 旧「整体变灰」常量 (P0-2 起不再用于覆盖类型色; 仅作最后兜底)
DISABLED_COLOR = "#94A3B8"

# ── 结构色回退真源 (P0-3) ────────────────────────────────────────────────
# 仅在 ctx["style"] 缺失时使用; 一旦调用方注入 ctx["style"] 即被逐键覆盖。
# 唯一真源在 ui.workflow_view._FLOW_STYLE —— 本模块「不自建色板」(见模块 docstring),
# 下面两张表只是**独立调用/回退**时的默认值 (A6: 字面量仅出现在默认回退值处)。
_FALLBACK_STYLE_LIGHT = {
    "shadow": "#CBD5E0", "flowbg": "#f8fafc", "disabled_mix": 0.4,
    "text_on_node": "#FFFFFF", "text_sub": "#E2E8F0",
    "grid_fine": "#EEEEEE", "grid_coarse": "#E1E1E1",
    "edge": "#94A3B8", "edge_sel": "#0078D4", "edge_reject": "#EF4444",
    "edge_back": "#8B5CF6", "walked": "#10B981", "terminal": "#94A3B8",
    "disabled_color": DISABLED_COLOR, "port_idle": None, "port_hover": "#0078D4",
}
_FALLBACK_STYLE_DARK = {
    "shadow": "#334155", "flowbg": "#0f172a", "disabled_mix": 0.4,
    "text_on_node": "#FFFFFF", "text_sub": "#CBD5E0",
    "grid_fine": "#333333", "grid_coarse": "#3A3A3A",
    "edge": "#A0A0A0", "edge_sel": "#4CC2FF", "edge_reject": "#FF99A4",
    "edge_back": "#8B5CF6", "walked": "#6CCB5F", "terminal": "#A0A0A0",
    "disabled_color": "#4A4A4A", "port_idle": None, "port_hover": "#4CC2FF",
}


def _is_color(value):
    """是否是 #RGB / #RRGGBB 形式的颜色串。"""
    return (isinstance(value, str) and value.startswith("#")
            and len(value) in (4, 7))


def blend(color, other, ratio):
    """线性混色 (纯函数, 不 import tkinter): result = color*(1-ratio) + other*ratio。

    · 输入/输出均为 #RRGGBB (接受 #RGB); ratio 自动裁剪到 [0, 1];
    · 非法输入回退返回 color 原值 (绝不抛异常)。
    Tk canvas 的 fill 不支持 alpha, 「40% 透明 / 淡化 30%」一律以本函数近似
    (P0-2 禁用态、P0-3 结构色)。
    """
    def _rgb(c):
        c = c.lstrip("#")
        if len(c) == 3:
            c = "".join(ch * 2 for ch in c)
        return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)

    try:
        r1, g1, b1 = _rgb(color)
        r2, g2, b2 = _rgb(other)
    except Exception:
        return color
    try:
        t = float(ratio)
    except Exception:
        t = 0.0
    t = max(0.0, min(1.0, t))
    r = int(round(r1 * (1.0 - t) + r2 * t))
    g = int(round(g1 * (1.0 - t) + g2 * t))
    b = int(round(b1 * (1.0 - t) + b2 * t))
    return "#{:02X}{:02X}{:02X}".format(r, g, b)


def resolve_style(ctx):
    """合并 ctx["style"] (真源) 与模块回退默认值 → 完整结构色表 (P0-3)。

    ctx["style"] 缺失/非法时整体回退 _FALLBACK_STYLE_*; 部分缺失键由回退补齐,
    保证返回 dict 内**所有键均存在** (调用方可直接下标取值)。
    """
    ctx = ctx or {}
    base = dict(_FALLBACK_STYLE_DARK if ctx.get("dark") else _FALLBACK_STYLE_LIGHT)
    st = ctx.get("style")
    if isinstance(st, dict):
        for k, v in st.items():
            if v is not None:
                base[k] = v
    return base


def _sp_of(ctx):
    """取 ctx["sp"] (尺寸令牌访问器); 缺失/非法回退恒等函数 (兼容独立调用)。"""
    sp = (ctx or {}).get("sp")
    return sp if callable(sp) else (lambda v: v)


def _scaled_font(font, zoom):
    """字体随缩放乘子 zoom 线性放大 (P0-4); 非法/缺省/zoom==1 原样返回。"""
    if not font or abs(zoom - 1.0) < 1e-9:
        return font
    try:
        if isinstance(font, (tuple, list)) and len(font) >= 2:
            size = float(font[1])
            scaled = max(1, int(round(abs(size) * zoom)))
            if size < 0:
                scaled = -scaled
            return (font[0], scaled) + tuple(font[2:])
    except Exception:
        pass
    return font

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


def _arrow_head(canvas, x, y, direction, color, tags="arrow", *, size=6):
    """在 (x, y) 画一个指向 direction 的箭头 (direction: 'down'/'up'/'left'/'right')。

    size: 箭头半宽 (设计 px, 已含 sp() 与 zoom 乘子, 由调用方传入; P0-4)。
    """
    s = size
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


def _draw_node(canvas, node, colors, fonts, ctx, *, zoom=1.0, sp=None, style=None):
    typ = node.type
    x, y, w, h = node.x, node.y, node.w, node.h
    cx, cy = x + w / 2.0, y + h / 2.0

    sp = sp or (lambda v: v)
    style = style or resolve_style(ctx)
    index = _node_index(ctx, node.id)
    base = colors.get(typ, FALLBACK_COLOR)

    # P0-2: 禁用态**保留类型色** —— 填充 = blend(类型色, flowbg, 0.4) (无 alpha 近似),
    # 禁止整体变灰 (移除旧的 DISABLED_COLOR 覆盖); 描边取混色, 文本第 2 行叠删除线。
    disabled = not node.enabled
    if disabled:
        flowbg = style.get("flowbg")
        if _is_color(flowbg):
            color = blend(base, flowbg, style.get("disabled_mix", 0.4))
        else:
            color = style.get("disabled_color") or base
    else:
        color = base

    darken = ctx.get("darken")
    outline = darken(color) if callable(darken) else color
    width = 2 * zoom
    if index is not None and ctx.get("current_step") == index:
        outline = ctx.get("sc") or outline
        width = 3 * zoom
    elif index is not None and (ctx.get("step_results") or {}).get(index) == "error":
        outline = ctx.get("dg") or outline
        width = 3 * zoom

    tags = ["node", _ROWS + node.id]
    itag = ctx.get("index_tags", {}).get(node.id)
    if itag:
        tags.append(itag)

    # 阴影 (P0-3: 走 ctx["style"], resolve_style 已补齐回退)
    shadow_id = canvas.create_rectangle(x + 2, y + 2, x + w + 2, y + h + 2,
                                        fill=style["shadow"], outline="", tags=("shadow",))

    text_on = style["text_on_node"]
    shape_id = None
    if typ == "condition":
        shape_id = canvas.create_polygon(
            cx, y - 2, x + w, cy, cx, y + h + 2, x, cy,
            fill=color, outline=outline, width=width, tags=tuple(tags))
    elif typ == "parallel":
        shape_id = canvas.create_rectangle(
            x, y, x + w, y + h, fill=color, outline=outline, width=width, tags=tuple(tags))
        canvas.create_rectangle(x + 4, y + 4, x + w - 4, y + h - 4,
                                outline=text_on, dash=(2, 2), tags=tuple(tags))
    elif typ == "loop":
        shape_id = canvas.create_rectangle(
            x, y, x + w, y + h, fill=color, outline=outline, width=width, tags=tuple(tags))
        canvas.create_rectangle(x + 3, y + 3, x + 10, y + h - 3,
                                fill=text_on, outline="", tags=tuple(tags))
    else:
        shape_id = _rounded_rect(canvas, x, y, x + w, y + h, max(1.0, sp(8) * zoom),
                                 fill=color, outline=outline, width=width, tags=tuple(tags))

    if shape_id is not None:
        canvas.tag_lower(shadow_id, shape_id)

    icon = ctx.get("icons", {}).get(typ) or _TYPE_ICONS.get(typ, "?")
    label = ctx.get("type_labels", {}).get(typ) or _TYPE_LABELS.get(typ, typ)
    line1 = "{} {} #{}".format(icon, label, (index + 1) if index is not None else "-")
    text = "{}\n{}".format(line1, _node_detail(node))
    text_id = canvas.create_text(cx, cy, text=text, fill=text_on, justify="center",
                                 anchor="center",
                                 font=_scaled_font(fonts.get("small_bold"), zoom),
                                 tags=tuple(tags))
    if disabled:
        # 第 2 行 (详情) 叠删除线: 穿过该行文本中线 (两行 bbox 的下 ~3/4 处)
        try:
            bbox = canvas.bbox(text_id)
            if bbox:
                mid = bbox[1] + (bbox[3] - bbox[1]) * 0.73
                canvas.create_line(bbox[0] + 2, mid, bbox[2] - 2, mid,
                                   fill=text_on, width=max(1.0, sp(1) * zoom),
                                   tags=tuple(tags))
        except Exception:
            pass
    if ctx.get("show_ports"):
        _draw_ports(canvas, node, base, outline, tags, zoom=zoom, sp=sp, style=style)


def _node_index(ctx, nid):
    """由调用方注入的 index_tags 反查顶层步骤序号 (仅供执行高亮/拖拽标签)。"""
    tag = (ctx.get("index_tags") or {}).get(nid)
    if not tag:
        return None
    digits = "".join(ch for ch in str(tag) if ch.isdigit())
    return int(digits) if digits else None


def _draw_forward_edge(canvas, e, s, d, color, fonts, ctx, *, zoom=1.0, sp=None, style=None):
    sp = sp or (lambda v: v)
    sx, sy = s.x + s.w / 2.0, s.y + s.h
    dx, dy = d.x + d.w / 2.0, d.y
    line_w = max(1.0, sp(1) * zoom)          # A2: zoom==1.0 时 == sp(1)
    canvas.create_line(sx, sy, dx, dy, fill=color, width=line_w,
                       tags=("arrow", "edge:" + str(e.id)))
    _arrow_head(canvas, dx, dy, "down", color, size=sp(6) * zoom)
    if e.label:
        mx, my = (sx + dx) / 2.0, (sy + dy) / 2.0
        canvas.create_text(mx + 8, my, text=e.label, fill=color, anchor="w",
                           font=_scaled_font(fonts.get("small"), zoom), tags=("arrow",))


def _draw_back_edge(canvas, e, s, d, color, fonts, *, zoom=1.0, sp=None, style=None):
    sp = sp or (lambda v: v)
    line_w = max(1.0, sp(1) * zoom)
    dash = (max(1.0, sp(4) * zoom), max(1.0, sp(2) * zoom))
    if s.id == d.id:                            # 自环 (空 loop 的范围标记)
        x2, cy = s.x + s.w, s.y + s.h / 2.0
        canvas.create_line(x2, cy - 10 * zoom, x2 + 26 * zoom, cy, x2, cy + 10 * zoom,
                           fill=color, width=line_w, smooth=True, dash=dash,
                           arrow="last", tags=("arrow", "edge:" + str(e.id)))
        return
    sx, sy = s.x, s.y + s.h / 2.0               # 回边走左侧弓形
    dx, dy = d.x, d.y + d.h / 2.0
    bow = min(sx, dx) - sp(28) * zoom
    canvas.create_line(sx, sy, bow, (sy + dy) / 2.0, dx, dy,
                       fill=color, width=line_w, smooth=True, dash=dash,
                       arrow="last", tags=("arrow", "edge:" + str(e.id)))
    if e.label:
        canvas.create_text(bow - 4, (sy + dy) / 2.0, text=e.label, anchor="e",
                           fill=color, font=_scaled_font(fonts.get("small"), zoom),
                           tags=("arrow",))


def _draw_terminal(canvas, cx, cy, text, color, fonts, tags, *, zoom=1.0, sp=None, style=None):
    sp = sp or (lambda v: v)
    style = style or resolve_style({})
    w, h = sp(96) * zoom, sp(30) * zoom
    _rounded_rect(canvas, cx - w / 2.0, cy, cx + w / 2.0, cy + h, max(1.0, sp(15) * zoom),
                  fill=color, outline=color, tags=tags)
    canvas.create_text(cx, cy + h / 2.0, text=text, fill=style["text_on_node"],
                       font=_scaled_font(fonts.get("small_bold"), zoom), tags=tags)
    return cy, cy + h


def _draw_grid(canvas, x0, y0, x1, y1, style, sp, zoom, *, fine=16, coarse=4):
    """画布最底层网格 (P1-7): 细线 + 每 ``coarse`` 格一条加粗线。

    · 颜色取 ``ctx["style"]`` 的 ``grid_fine`` / ``grid_coarse`` (暗色主题已由
      `ui.workflow_view._refresh_flow_style` 取低对比取值) —— 本模块不自建色板;
    · 网格格距随 ``zoom`` 乘子一致 (设计像素 → 屏幕像素), 下限 6px 防止极小缩放下
      线数爆炸; 所有线带 ``grid`` 标签, 且**最先绘制** (位于所有图元之下)。
    """
    # 缺键回退到模块回退样式表 (resolve_style 已保证齐备; 此处再兜底, 且不引入
    # 越界字面量色 —— 见 P0 断言 A6「无越界字面量结构色」)。
    fine_color = style.get("grid_fine") or _FALLBACK_STYLE_LIGHT["grid_fine"]
    coarse_color = style.get("grid_coarse") or _FALLBACK_STYLE_LIGHT["grid_coarse"]
    try:
        step = max(6.0, float(sp(fine)) * float(zoom))
    except Exception:
        try:
            step = max(6.0, float(fine) * float(zoom))
        except Exception:
            return
    if step <= 0:
        return
    i = 0
    x = x0
    while x <= x1 + 0.5:
        col = coarse_color if (i % coarse == 0) else fine_color
        canvas.create_line(x, y0, x, y1, fill=col, width=1, tags=("grid",))
        x += step
        i += 1
    j = 0
    y = y0
    while y <= y1 + 0.5:
        col = coarse_color if (j % coarse == 0) else fine_color
        canvas.create_line(x0, y, x1, y, fill=col, width=1, tags=("grid",))
        y += step
        j += 1


# ══════════════════════════════════════════════════════════════════════
# 渲染入口
# ══════════════════════════════════════════════════════════════════════

def render(canvas, graph, *, colors, fonts=None, ctx=None, zoom=1.0):
    """把 graph 画到 canvas 上, 返回统计 dict (nodes/edges/width/height)。

    zoom: 渲染期缩放乘子 (P0-4 / P1-2 / WF-B2)。
      · zoom==1.0: 几何与设计空间**逐位一致** (沿用 P0 基线, A1/A2/A7 不回归);
      · zoom!=1.0: 布局几何 (node_w/h、h_gap、v_gap、margin) 按 zoom **整体等比缩放**,
        并经 layout() 把缩放后的 x/y/w/h **写回** graph.nodes —— 故渲染后 graph 坐标为
        缩放坐标 (fit_to_view/缩放按钮因此视觉生效); 线宽/字号/端口半径/箭头等仍按
        zoom 线性补偿。
    """
    colors = colors or {}
    fonts = fonts or {}
    ctx = ctx or {}
    try:
        zoom = float(zoom)
    except Exception:
        zoom = 1.0
    if not (zoom > 0):
        zoom = 1.0
    style = resolve_style(ctx)                  # P0-3: 结构色真源 (含回退)
    sp = _sp_of(ctx)                            # P0-1: 尺寸令牌 (缺失→恒等)
    canvas.delete("all")

    nodes = list(graph.nodes)
    if not nodes:
        canvas.create_text(200, 60, text="暂无步骤\n点击 [+ 脚本] 等按钮添加",
                           font=fonts.get("body"),
                           fill=ctx.get("fgm") or style["terminal"],
                           anchor="center", tags=("empty",))
        canvas.configure(scrollregion=(0, 0, 400, 150))
        return {"nodes": 0, "edges": 0, "width": 400, "height": 150}

    # P0-1: 布局几何经 ctx["sp"] 注入 (sp 缺失时恒等 → 退回设计像素默认值)。
    # P1-2: 布局几何再乘 zoom 乘子 —— 令缩放对节点/间距/坐标**整体等比**生效
    # (zoom==1.0 时与 P0 完全一致 → 既有 A1/A2/A7 断言不变); 若 zoom 只补偿
    # 线宽/字号, 则 fit_to_view/缩放按钮在视觉上无效。
    node_w, node_h = sp(NODE_W) * zoom, sp(NODE_H) * zoom
    width, height = layout(graph, node_w=node_w, node_h=node_h,
                           h_gap=sp(H_GAP) * zoom, v_gap=sp(V_GAP) * zoom,
                           margin=sp(MARGIN) * zoom)
    top = min(n.y for n in nodes)
    bottom = max(n.y + n.h for n in nodes)
    cx = width / 2.0
    start_y = top - node_h - sp(26)
    end_y = bottom + sp(30)

    # P1-7: 最底层网格 (先画 → 位于边/节点之下); 颜色取 ctx["style"] 的 grid_*。
    # ctx["grid"] 默认 True (由宿主经 _WF_INTERACT_V2 控制, 便于回滚)。
    if ctx.get("grid", True):
        _draw_grid(canvas, 0.0, start_y - sp(30), max(width, 400.0),
                   end_y + sp(40), style, sp, zoom)

    fwd, _rev = _forward_adjacency(graph)
    roots = [n for n in graph.roots()]
    sinks = [n for n in nodes if not fwd.get(n.id)]

    # 结构色 (P0-3: 取 ctx["style"], ctx 内 fgm 等仅作次选)
    edge_color = style["edge"]
    back_color = style["edge_back"]
    term_color = style["terminal"]

    # ── 边 (先画, 让节点覆盖在连线之上) ──
    for e in graph.edges:
        s, d = graph.node(e.src), graph.node(e.dst)
        if s is None or d is None:
            continue
        if e.back_edge:
            _draw_back_edge(canvas, e, s, d, back_color, fonts,
                            zoom=zoom, sp=sp, style=style)
        else:
            _draw_forward_edge(canvas, e, s, d, edge_color, fonts, ctx,
                               zoom=zoom, sp=sp, style=style)

    # ── start / end (渲染合成, 不写回图数据) ──
    _draw_terminal(canvas, cx, start_y, "START", term_color, fonts, ("terminal",),
                   zoom=zoom, sp=sp, style=style)
    _draw_terminal(canvas, cx, end_y, "END", term_color, fonts, ("terminal",),
                   zoom=zoom, sp=sp, style=style)
    term_line_w = max(1.0, sp(1) * zoom)
    for n in roots:
        x, y = n.x + n.w / 2.0, n.y
        canvas.create_line(cx, start_y + sp(30) * zoom, x, y, fill=term_color,
                           width=term_line_w, arrow="last", tags=("arrow",))
    for n in sinks:
        x, y = n.x + n.w / 2.0, n.y + n.h
        canvas.create_line(x, y, cx, end_y, fill=term_color,
                           width=term_line_w, arrow="last", tags=("arrow",))

    # ── 节点 ──
    for n in nodes:
        _draw_node(canvas, n, colors, fonts, ctx, zoom=zoom, sp=sp, style=style)

    total_w = max(width, 400)
    total_h = end_y + sp(30) - start_y + sp(20)
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


def bind_zoom_pan(canvas, *, min_scale=0.4, max_scale=2.5, step=1.12, re_render=None):
    """挂载 Ctrl+滚轮缩放 / Shift+滚轮水平 / 中键拖拽平移; 返回状态 dict。

    普通 <MouseWheel> 不在此处绑定 —— 垂直滚动语义仍由调用方
    (`_wf_flow_on_wheel`) 保留, 本函数只增加更具体的修饰键分支。

    P0-4: 缩放语义由 `canvas.scale` **坐标变换** 改为 **渲染期乘子** —— 更新
    st["scale"] 后经 `re_render` 触发重绘 (布局坐标不变; 线宽/字号/端口半径随
    乘子补偿)。`re_render` 缺省 (None) 时保留旧 `canvas.scale` 行为 (向后兼容)。
    0.4–2.5 范围与「光标锚点」语义不变。
    """
    st = {"scale": 1.0, "min_scale": min_scale, "max_scale": max_scale,
          "panning": False, "re_render": re_render}

    def _clamp(v):
        return max(min_scale, min(max_scale, v))

    def _on_zoom(event):
        delta = getattr(event, "delta", 0)
        if not delta:
            return "break"
        prev = st["scale"]
        new = _clamp(prev * (step if delta > 0 else 1.0 / step))
        if abs(new - prev) < 1e-9:
            return "break"
        try:
            ax = canvas.canvasx(event.x)
            ay = canvas.canvasy(event.y)
        except Exception:
            ax = ay = None
        st["scale"] = new
        rr = st.get("re_render") or re_render
        if callable(rr):
            # 渲染期乘子: 布局坐标不变, 重绘按新 zoom 补偿线宽/字号/端口半径
            try:
                rr()
            except Exception:
                pass
        else:                                   # 无重绘回调 → 旧行为 (坐标缩放)
            try:
                canvas.scale("all", ax, ay, new / prev, new / prev)
            except Exception:
                pass
        _refresh_scrollregion(canvas)
        if ax is not None:
            _anchor_view(canvas, ax, ay, event.x, event.y)
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


# ══════════════════════════════════════════════════════════════════════
# 端口 / 连线交互 (路线图 §11.7, D 期)
# ══════════════════════════════════════════════════════════════════════
#
# 端口: 节点边缘圆点, 标签 ``port`` / ``port:<nid>:<port>``; 渲染受 ``ctx["show_ports"]``
# 控制。边: 每条连线带 ``edge:<eid>`` 标签供命中测试。
# 交互**只操作内存图** —— 按 §11.4 的工程判断, 执行仍走树执行器, 图编辑为视图级。

PORT_R = 4


def _draw_ports(canvas, node, fill, outline, tags, *, zoom=1.0, sp=None, style=None):
    """节点端口 (圆点): in 在上缘; out/body 在下缘; condition 为 true/false 双出口。

    P0-4: 半径 = sp(PORT_R) * zoom (zoom==1.0 时 == sp(4))。
    """
    sp = sp or (lambda v: v)
    r = max(1.0, sp(PORT_R) * zoom)
    x, y, w, h = node.x, node.y, node.w, node.h
    cx = x + w / 2.0
    ports = [("in", cx, y)]
    if node.type == "condition":
        ports += [("true", x + w * 0.3, y + h), ("false", x + w * 0.7, y + h)]
    elif node.type in ("parallel", "loop"):
        ports.append(("body", cx, y + h))
    else:
        ports.append(("out", cx, y + h))
    for name, px, py in ports:
        canvas.create_oval(px - r, py - r, px + r, py + r,
                           fill=fill, outline=outline, width=max(1.0, sp(1) * zoom),
                           tags=tuple(tags) + ("port", "port:%s:%s" % (node.id, name)))


def _tags_at(canvas, x, y, prefix, pad=6):
    """光标附近首个带 prefix 标签对象的「去前缀值」(后绘制者优先)。"""
    try:
        items = canvas.find_overlapping(x - pad, y - pad, x + pad, y + pad)
    except Exception:
        return None
    for it in reversed(items):
        try:
            for t in canvas.gettags(it):
                if t.startswith(prefix):
                    return t[len(prefix):]
        except Exception:
            continue
    return None


def port_at(canvas, x, y, pad=8):
    """→ (node_id, port) 或 (None, None)。"""
    val = _tags_at(canvas, x, y, "port:", pad)
    if not val or ":" not in val:
        return None, None
    nid, port = val.split(":", 1)
    return nid, port


def node_at(canvas, x, y, pad=2):
    """→ 光标下节点 id 或 None。"""
    return _tags_at(canvas, x, y, "fgnode_", pad)


def edge_at(canvas, x, y, pad=5):
    """→ 光标下边 id 或 None。"""
    return _tags_at(canvas, x, y, "edge:", pad)


def _viewport(canvas):
    """→ (可视宽, 可视高) 像素; 未映射 (winfo<=1) 时回退 cget("width"/"height")。"""
    w = h = 0.0
    try:
        w = float(canvas.winfo_width())
        h = float(canvas.winfo_height())
    except Exception:
        pass
    if w <= 1.0:
        try:
            w = float(canvas.cget("width") or 0)
        except Exception:
            w = 0.0
    if h <= 1.0:
        try:
            h = float(canvas.cget("height") or 0)
        except Exception:
            h = 0.0
    return (w if w > 1.0 else 400.0), (h if h > 1.0 else 300.0)


def view_center(canvas, cx, cy):
    """把画布坐标 (cx, cy) 居中到当前视口 (等比/定位共用的滚动原语)。"""
    sr = _scrollregion(canvas)
    if not sr:
        return
    x0, y0, x1, y1 = sr
    tw = max(x1 - x0, 1.0)
    th = max(y1 - y0, 1.0)
    vw, vh = _viewport(canvas)
    fx = (cx - vw / 2.0 - x0) / tw
    fy = (cy - vh / 2.0 - y0) / th
    try:
        canvas.xview_moveto(max(0.0, min(1.0, fx)))
        canvas.yview_moveto(max(0.0, min(1.0, fy)))
    except Exception:
        pass


def fit_to_view(canvas, margin=24, *, zoom_state=None, re_render=None,
                min_scale=0.4, max_scale=2.5):
    """适应窗口 (P1-2 升级: 按视口等比算 zoom + 居中)。

    · 传入 ``zoom_state`` (dict, 含 ``scale``) 且 ``re_render`` 可调用时: 按
      「视口 / 内容」比值算出目标 zoom, 写回 ``zoom_state["scale"]`` 后触发重绘,
      再把内容居中 (等比 + 居中);
    · 缺省 (None / 不可调用) 时保留旧行为 —— 仅把 scrollregion 左上角附近
      移入视图, **不改缩放** (兼容 `_test_flow_canvas_interact` 的既有调用)。
    """
    if isinstance(zoom_state, dict) and callable(re_render):
        sr = _scrollregion(canvas)
        if not sr:
            return
        vw, vh = _viewport(canvas)
        try:
            z0 = float(zoom_state.get("scale", 1.0))
        except Exception:
            z0 = 1.0
        sw = max(sr[2] - sr[0], 1.0)
        sh = max(sr[3] - sr[1], 1.0)
        z = z0 * min(max(vw - 2.0 * margin, 1.0) / sw,
                     max(vh - 2.0 * margin, 1.0) / sh)
        z = max(min_scale, min(max_scale, z))
        if abs(z - z0) > 1e-6:
            zoom_state["scale"] = z
            try:
                re_render()
            except Exception:
                pass
        sr = _scrollregion(canvas) or sr
        view_center(canvas, (sr[0] + sr[2]) / 2.0, (sr[1] + sr[3]) / 2.0)
        return
    # 旧行为: 不改缩放 (避免与 zoom 状态失配)
    sr = _scrollregion(canvas)
    if not sr:
        return
    x0, y0, x1, y1 = sr
    w = max(x1 - x0, 1.0)
    h = max(y1 - y0, 1.0)
    try:
        canvas.xview_moveto(max(0.0, (x0 + margin) / w))
        canvas.yview_moveto(max(0.0, (y0 + margin) / h))
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════
# 小地图 (P1-2 / WF-B2): 等比缩略全部节点 + 视口框
# ══════════════════════════════════════════════════════════════════════

def minimap(mcv, graph, *, colors=None, ctx=None, width=None, height=None, view=None):
    """把 graph 等比缩略绘到小地图画布 ``mcv``; 返回映射 dict (供反算跳转)。

    · 节点坐标取 ``graph.nodes`` 的 ``n.x/y/w/h`` (``layout()`` 已写回);
    · 节点色取 ``colors`` (类型色真源仍在调用方), 禁用节点混色淡化 (风格与主画布一致);
    · ``view`` = (x0, y0, x1, y1) 内容坐标矩形 → 以 1px 视口框表示;
    · 返回 ``{"ox","oy","s"}`` (原点偏移 + 缩放比), 供 `minimap_to_view` 反算。
    """
    colors = colors or {}
    ctx = ctx or {}
    style = resolve_style(ctx)
    try:
        mcv.delete("all")
    except Exception:
        return None
    nodes = list(getattr(graph, "nodes", []) or [])
    if not nodes:
        return None
    if width is None:
        try:
            width = float(mcv.winfo_width())
        except Exception:
            width = 120.0
    if height is None:
        try:
            height = float(mcv.winfo_height())
        except Exception:
            height = 80.0
    width = max(float(width), 20.0)
    height = max(float(height), 20.0)
    xs = [n.x for n in nodes]
    ys = [n.y for n in nodes]
    xe = [n.x + n.w for n in nodes]
    ye = [n.y + n.h for n in nodes]
    cw = max(max(xe) - min(xs), 1.0)
    ch = max(max(ye) - min(ys), 1.0)
    pad = 4.0
    s = min((width - 2.0 * pad) / cw, (height - 2.0 * pad) / ch)
    if s <= 0:
        s = 1.0
    ox = pad + ((width - 2.0 * pad) - cw * s) / 2.0 - min(xs) * s
    oy = pad + ((height - 2.0 * pad) - ch * s) / 2.0 - min(ys) * s
    flowbg = style.get("flowbg")
    for n in nodes:
        col = colors.get(n.type) or FALLBACK_COLOR
        if not n.enabled and _is_color(flowbg):
            col = blend(col, flowbg, style.get("disabled_mix", 0.4))
        x1, y1 = ox + n.x * s, oy + n.y * s
        x2, y2 = ox + (n.x + n.w) * s, oy + (n.y + n.h) * s
        try:
            mcv.create_rectangle(x1, y1, max(x1 + 1.0, x2), max(y1 + 1.0, y2),
                                 fill=col, outline="", tags=("mini",))
        except Exception:
            pass
    if view and len(view) == 4:
        vx0, vy0, vx1, vy1 = [float(v) for v in view]
        try:
            mcv.create_rectangle(ox + vx0 * s, oy + vy0 * s,
                                 ox + vx1 * s, oy + vy1 * s,
                                 outline=style["edge_sel"],      # 结构色真源 (不自建)
                                 width=1, tags=("miniview",))
        except Exception:
            pass
    return {"ox": ox, "oy": oy, "s": s}


def minimap_to_view(canvas, mapping, mx, my):
    """小地图坐标 (mx, my) → 主画布对应内容点居中 (点击/拖框跳转)。"""
    if not isinstance(mapping, dict):
        return
    try:
        s = float(mapping.get("s") or 1.0)
        if s <= 0:
            return
        cx = (float(mx) - float(mapping.get("ox") or 0.0)) / s
        cy = (float(my) - float(mapping.get("oy") or 0.0)) / s
    except Exception:
        return
    view_center(canvas, cx, cy)


def bind_interactions(canvas, graph, *, colors=None, fonts=None, ctx=None,
                      re_render=None, get_zoom_state=None, on_graph_changed=None,
                      on_select_edge=None, toast=None, on_fit=None):
    """D 期连线交互 + P1 (WF-B1) 交互增强。

    既有 (D 期): 端口拖拽建边 / 边选中与 Delete 删除 / F 适应窗口 / Ctrl+0 重置缩放 /
    Ctrl+Z·Ctrl+Y 撤销重做。仅操作内存图 (视图级编辑), 变更前压栈快照。

    P1 增强 (受 ``ctx["interact_v2"]`` 开关保护, 默认 True; 新增绑定一律 ``add="+"``):
      · N2 节点 hover: 描边提亮 + 内缩 1px 的高亮环 (canvas 级 <Motion>/<Leave>);
      · N4 非法连线拒绝反馈: 橡皮筋实时判色 (ac/dg/中性) + 释放时目标节点 dg 闪烁
        + toast(reason)（reason 取自 flow_graph.can_connect; 不再静默);
      · N5 矩形框选: 空白左键拖 (与端口橡皮筋分流)。
      · A5 选中边可回退: 切换选中边时把旧边线宽恢复基准 (消除 width=3 残留)。

    返回状态 dict (另附 select_edge/hover/reject/press/motion/release/
    base_edge_width/nodes_in_rect 处理函数, 供宿主与离屏自测驱动, 不影响既有契约)。
    """
    import flow_graph as _fg
    colors = colors or {}
    ctx = ctx or {}
    style = resolve_style(ctx)                  # P0-3: 结构色真源 (含回退)
    sp = _sp_of(ctx)
    interact_v2 = bool(ctx.get("interact_v2", True))

    def _zoom_now():
        z = 1.0
        if callable(get_zoom_state):
            try:
                zs = get_zoom_state()
                if isinstance(zs, dict):
                    z = float(zs.get("scale", 1.0))
            except Exception:
                z = 1.0
        return z

    def _base_edge_width():
        """选中高亮的**基准线宽** (口径同渲染 :442/:454 = max(1.0, sp(1)*zoom))。"""
        try:
            return max(1.0, float(sp(1)) * _zoom_now())
        except Exception:
            return 1.0

    rubber_color = ctx.get("ac") or style["edge_sel"]                 # 合法 (ac)
    reject_color = ctx.get("dg") or style["edge_reject"]             # 非法 (dg)
    idle_color = ctx.get("fgd") or ctx.get("fgm") or style["edge"]    # 无目标 (fgd)
    toast_fn = toast or ctx.get("toast")

    st = {"selected_edge": None, "rubber": None, "src": None,
          "undos": [], "redos": [], "hover": None, "hover_items": {},
          "marquee": None, "marquee_origin": None, "selected_nodes": set()}

    def _notify(msg):
        if not callable(toast_fn) or not msg:
            return
        try:
            toast_fn(msg)
        except Exception:
            pass

    def _snapshot():
        return graph.to_dict()

    def _restore(snap):
        g2 = _fg.Graph.from_dict(snap)
        graph.nodes[:] = g2.nodes
        graph.edges[:] = g2.edges

    def _push():
        st["undos"].append(_snapshot())
        if len(st["undos"]) > 50:
            st["undos"].pop(0)
        st["redos"].clear()

    def _redraw():
        if callable(re_render):
            try:
                re_render(graph)
            except TypeError:
                re_render()
            except Exception:
                pass

    def _edge_items(eid):
        try:
            return list(canvas.find_withtag("edge:" + str(eid))) if eid else []
        except Exception:
            return []

    def _highlight(eid):
        for it in _edge_items(eid):
            try:
                canvas.itemconfigure(it, width=3)
            except Exception:
                pass

    def _restore_edge_width(eid):
        bw = _base_edge_width()
        for it in _edge_items(eid):
            try:
                canvas.itemconfigure(it, width=bw)
            except Exception:
                pass

    def _select_edge(eid):
        # P1-6/A5: 切换时把「旧边」线宽恢复基准 —— 消除 _highlight 置 width=3 无恢复
        old = st.get("selected_edge")
        if interact_v2 and old and old != eid:
            _restore_edge_width(old)
        st["selected_edge"] = eid
        if eid:
            _highlight(eid)
        if callable(on_select_edge):
            try:
                on_select_edge(eid)
            except Exception:
                pass

    # ── N2 节点 hover: 提亮描边 + 内缩 1px 的高亮环 (可逆) ──
    def _node_box(nid):
        n = graph.node(nid)
        if n is None:
            return None
        return (n.x, n.y, n.x + n.w, n.y + n.h)

    def _clear_hover_ring(nid):
        hid = st["hover_items"].pop(nid, None)
        if hid is not None:
            try:
                canvas.delete(hid)
            except Exception:
                pass

    def _set_hover(nid):
        if not interact_v2 or st.get("hover") == nid:
            return
        old = st.get("hover")
        if old is not None:
            _clear_hover_ring(old)
        st["hover"] = nid
        if not nid:
            return
        box = _node_box(nid)
        if box is None:
            return
        inset = max(1.0, float(sp(1)) * _zoom_now())   # 内缩 1px
        try:
            hid = canvas.create_rectangle(
                box[0] + inset, box[1] + inset, box[2] - inset, box[3] - inset,
                outline=rubber_color, width=max(1.0, float(sp(2)) * _zoom_now()),
                tags=("hover", "fhover_" + str(nid)))
            canvas.tag_raise(hid)
            st["hover_items"][nid] = hid
        except Exception:
            pass

    # ── P1-3/A4 橡皮筋判色 ──
    def _rubber_color_for(x, y):
        src, src_port = st.get("src") or (None, None)
        dst = node_at(canvas, x, y)
        if not src or not dst:
            return idle_color
        try:
            ok, _reason = _fg.can_connect(graph, src, dst, src_port or "out", "in")
        except Exception:
            ok = False
        return rubber_color if ok else reject_color

    # ── P1-3/N4 拒绝反馈: 目标节点 dg 描边闪烁 + toast(reason) ──
    def _flash_node(nid):
        if not nid:
            return
        saved = []
        try:
            for it in canvas.find_withtag(_ROWS + str(nid)):
                if canvas.type(it) in ("polygon", "rectangle"):
                    try:
                        saved.append((it, canvas.itemcget(it, "outline")))
                    except Exception:
                        pass
            for it, _o in saved:
                canvas.itemconfigure(it, outline=reject_color)
        except Exception:
            return

        def _restore_outline():
            for it, o in saved:
                try:
                    canvas.itemconfigure(it, outline=o)
                except Exception:
                    pass

        try:
            canvas.after(320, _restore_outline)        # 闪烁后自动恢复
        except Exception:
            _restore_outline()

    def _reject(nid, reason):
        _flash_node(nid)
        _notify(reason or "无法建立连线")

    def _undo(event=None):
        if not st["undos"]:
            return "break"
        st["redos"].append(_snapshot())
        _restore(st["undos"].pop())
        _redraw()
        return "break"

    def _redo(event=None):
        if not st["redos"]:
            return "break"
        st["undos"].append(_snapshot())
        _restore(st["redos"].pop())
        _redraw()
        return "break"

    def _on_press(event):
        nid, port = port_at(canvas, event.x, event.y)
        if nid:
            st["src"] = (nid, port)
            st["rubber"] = canvas.create_line(
                event.x, event.y, event.x, event.y,
                fill=rubber_color, dash=(4, 3), width=2, tags=("rubber",))
            return "break"
        eid = edge_at(canvas, event.x, event.y)
        _select_edge(eid)
        return "break" if eid else None

    def _on_motion(event):
        if st["rubber"] is None:
            return None
        coords = canvas.coords(st["rubber"])
        coords[2:] = [event.x, event.y]
        canvas.coords(st["rubber"], *coords)
        if interact_v2:
            try:
                canvas.itemconfigure(st["rubber"],
                                     fill=_rubber_color_for(event.x, event.y))
            except Exception:
                pass
        return "break"

    def _changed():
        if callable(on_graph_changed):
            try:
                on_graph_changed(graph)
            except Exception:
                pass

    def _on_release(event):
        if st["rubber"] is None:
            return None
        try:
            canvas.delete(st["rubber"])
        except Exception:
            pass
        st["rubber"] = None
        src, src_port = st.get("src") or (None, None)
        st["src"] = None
        if not src:
            return "break"
        dst = node_at(canvas, event.x, event.y)
        if not dst:
            return "break"
        try:
            ok, reason = _fg.can_connect(graph, src, dst, src_port or "out", "in")
        except Exception:
            ok, reason = True, ""
        if not ok:
            if interact_v2:
                _reject(dst, reason)           # 消费 can_connect 的 reason (勿再静默)
            return "break"
        _push()
        e = _fg.connect(graph, src, dst, src_port or "out", "in")
        if e is None:
            st["undos"].pop()
            if interact_v2:
                _reject(dst, reason)
            return "break"
        _select_edge(e.id)
        _changed()
        _redraw()
        return "break"

    def _del_edge(event=None):
        eid = st["selected_edge"]
        if not eid:
            return "break"
        _push()
        if _fg.disconnect(graph, eid) is None:
            st["undos"].pop()
            return "break"
        st["selected_edge"] = None
        _changed()
        _redraw()
        return "break"

    def _reset_zoom(event=None):
        zs = get_zoom_state() if callable(get_zoom_state) else None
        if isinstance(zs, dict):
            zs["scale"] = 1.0
        _redraw()
        return "break"

    # ── N5 矩形框选: 仅空白处左键拖 (与端口橡皮筋 / 边选中分流) ──
    def _nodes_in_rect(x0, y0, x1, y1):
        if x1 < x0:
            x0, x1 = x1, x0
        if y1 < y0:
            y0, y1 = y1, y0
        sel = set()
        for n in graph.nodes:
            if (n.x >= x0 and n.y >= y0 and n.x + n.w <= x1 and n.y + n.h <= y1):
                sel.add(n.id)
        return sel

    def _on_marquee_press(event):
        if not interact_v2:
            return None
        try:
            if port_at(canvas, event.x, event.y)[0]:
                return None
            if edge_at(canvas, event.x, event.y):
                return None
            if node_at(canvas, event.x, event.y):
                return None           # 节点上按下 → 交给节点拖拽 (tag_bind)
        except Exception:
            pass
        st["marquee_origin"] = (event.x, event.y)
        if st.get("marquee") is not None:
            try:
                canvas.delete(st["marquee"])
            except Exception:
                pass
        try:
            st["marquee"] = canvas.create_rectangle(
                event.x, event.y, event.x, event.y, outline=rubber_color,
                dash=(3, 2), width=max(1.0, float(sp(1)) * _zoom_now()),
                tags=("marquee", "fcmarquee"))
        except Exception:
            st["marquee"] = None
        return "break"

    def _on_marquee_motion(event):
        if not interact_v2 or st.get("marquee") is None:
            return None
        o = st.get("marquee_origin") or (event.x, event.y)
        try:
            canvas.coords(st["marquee"], o[0], o[1], event.x, event.y)
        except Exception:
            pass
        return "break"

    def _on_marquee_release(event):
        if not interact_v2 or st.get("marquee") is None:
            return None
        try:
            canvas.delete(st["marquee"])
        except Exception:
            pass
        st["marquee"] = None
        o = st.get("marquee_origin") or (event.x, event.y)
        sel = _nodes_in_rect(o[0], o[1], event.x, event.y)
        st["selected_nodes"] = sel
        for nid in sel:
            box = _node_box(nid)
            if box is None:
                continue
            try:
                canvas.create_rectangle(box[0] - 2, box[1] - 2, box[2] + 2, box[3] + 2,
                                        outline=rubber_color, dash=(2, 2), width=1,
                                        tags=("nodeselect",))
            except Exception:
                pass
        return "break"

    # ── N2 hover 监听 (canvas 级 <Motion>/<Leave>) ──
    def _on_hover(event):
        if not interact_v2:
            return None
        try:
            nid = node_at(canvas, event.x, event.y)
        except Exception:
            nid = None
        _set_hover(nid)
        return None

    def _on_leave(event):
        if interact_v2:
            _set_hover(None)
        return None

    def _fit_event(_e=None):
        """F 适应窗口: 优先走调用方注入的 on_fit (P1-2 zoom-aware), 否则旧行为。"""
        if callable(on_fit):
            try:
                on_fit()
            except Exception:
                pass
        else:
            fit_to_view(canvas)
        return "break"

    canvas.bind("<Button-1>", _on_press)
    canvas.bind("<B1-Motion>", _on_motion)
    canvas.bind("<ButtonRelease-1>", _on_release)
    canvas.bind("<Delete>", _del_edge)
    canvas.bind("<f>", _fit_event)
    canvas.bind("<F>", _fit_event)
    canvas.bind("<Control-Key-0>", _reset_zoom)
    canvas.bind("<Control-z>", _undo)
    canvas.bind("<Control-y>", _redo)

    if interact_v2:
        # 新增绑定一律 add="+": 不覆盖既有绑定; _on_press 命中端口/边返回 "break"
        # 会自然中止后续附加绑定 → 框选仅发生在空白处。
        canvas.bind("<Button-1>", _on_marquee_press, add="+")
        canvas.bind("<B1-Motion>", _on_marquee_motion, add="+")
        canvas.bind("<ButtonRelease-1>", _on_marquee_release, add="+")
        for _seq, _fn in (("<Motion>", _on_hover), ("<Leave>", _on_leave)):
            try:
                canvas.unbind(_seq)        # <Motion> 无既有绑定, 先清避免重复挂载
            except Exception:
                pass
            canvas.bind(_seq, _fn, add="+")

    st["handlers"] = (_on_press, _on_motion, _on_release, _del_edge, _undo, _redo)
    # 处理函数下沉 (供宿主与离屏自测驱动; 不破坏既有契约)
    st["select_edge"] = _select_edge
    st["base_edge_width"] = _base_edge_width
    st["hover"] = _set_hover
    st["reject"] = _reject
    st["press"] = _on_press
    st["motion"] = _on_motion
    st["release"] = _on_release
    st["nodes_in_rect"] = _nodes_in_rect
    return st
