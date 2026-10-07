#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""工作流界面美化 P0 最小离屏断言 (_FLOW_STYLE / blend / 渲染乘子)。

用法:
    python -X utf8 tools\\_p0_flow_style_check.py

覆盖 (对应方案 §8 A1/A2/A3/A6/A7):
  A6  源码扫描: flow_canvas.py 的 #RRGGBB 字面量仅出现在回退常量/回退样式表
  blend 纯函数: 线性混色正确 + 非法输入兜底
  resolve_style: ctx["style"] 覆盖回退; 缺失时回退默认键齐全
  实机(可用时):
    A2 线宽 == sp(1) · 端口半径 == sp(4)
    A3 禁用节点填充 == blend(类型色, flowbg, 0.4)
    A1 ui_scale ∈ {1.0,1.25,1.5,2.0} 下 node_w(scale)/node_w(1.0) ≈ scale
       (含显式 1.0 vs 1.5 比值 ≈ 1.5)
    A7 ui_scale ∈ {1.0,1.25,1.5,2.0} × 深/浅两主题 = 8 组,
       逐组断言节点两行文本 bbox 不超节点矩形

「实机」= 离屏 tk.Canvas 上直接调用 ui.flow_canvas.render (不弹完整主窗口)。
几何 token 以 sp = lambda v: v * ui_scale 注入 (等价 utils.scaled 去掉恒定 dpi_factor);
字体按 ui_scale 线性放大 (镜像 utils.fit_pt: max(6, round(size*ui_scale))), 与方案 §8
A1/A7 (P0-1/P2-3) 口径一致 —— ui_scale 提升时「字号变大」必须由「节点矩形同步变大」承接。

退出码: 0=无失败 / 1=有失败。
无 GUI 环境 / 某组无法测量文本 bbox 时记 [WARN] (明确标注, 不计入 PASS, 也不判失败)。
"""
import io
import os
import re
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

# ── A1/A7 覆盖矩阵 (方案 §8) ─────────────────────────────────────────
SCALES = (1.0, 1.25, 1.5, 2.0)        # ui_scale 四档
DARKS = (False, True)                  # 浅 / 深 两主题
RATIO_TOL = 0.05                       # A1 node_w 比值容差
FIT_TOL = 1.5                          # A7 文本越界容差 (px, 允许亚像素/描边误差)


def check(cond, msg):
    print("[{}] {}".format("OK  " if cond else "FAIL", msg))
    if not cond:
        _FAIL.append(msg)


def warn(msg):
    print("[WARN] {}".format(msg))
    _WARN.append(msg)


# ── A6. 源码扫描 (纯文本) ────────────────────────────────────────────
def t_a6_source_scan():
    print("\n── A6. 字面量结构色扫描 ──")
    path = os.path.join(SRC, "ui", "flow_canvas.py")
    with io.open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    start = next((i for i, l in enumerate(lines, 1) if "_FALLBACK_STYLE_LIGHT" in l), 0)
    dstart = next((i for i, l in enumerate(lines, 1) if "_FALLBACK_STYLE_DARK" in l), 0)
    dend = next((i for i in range(dstart, len(lines) + 1)
                 if lines[i - 1].strip() == "}"), dstart)
    bad = []
    for i, ln in enumerate(lines, 1):
        if not re.search(r"#[0-9A-Fa-f]{6}", ln):
            continue
        s = ln.strip()
        if s.startswith("#"):
            continue                                  # 注释
        if start and dstart and (start <= i <= dend):
            continue                                  # 回退样式表区间
        if s.startswith("FALLBACK_COLOR") or s.startswith("DISABLED_COLOR"):
            continue                                  # 回退常量
        bad.append((i, s))
    check(not bad, "A6: 无越界字面量结构色 (越界={})".format(bad))


# ── blend / resolve_style (纯函数, 不建窗) ────────────────────────────
def t_pure():
    print("\n── blend / resolve_style 纯函数 ──")
    from ui import flow_canvas as fc
    check("tkinter" not in sys.modules, "import flow_canvas 未引入 tkinter (纯度护栏)")
    check(fc.blend("#000000", "#FFFFFF", 0.5) == "#808080", "blend 黑白 50% == #808080")
    check(fc.blend("#FF0000", "#0000FF", 0.4) == "#990066", "blend 红蓝 40% == #990066")
    check(fc.blend("#336699", "#FFFFFF", 0.0) == "#336699", "blend ratio=0 == 原值")
    check(fc.blend("not-a-color", "#FFFFFF", 0.4) == "not-a-color", "blend 非法输入兜底原值")
    keys = ("shadow", "text_on_node", "text_sub", "edge", "edge_sel", "edge_reject",
            "edge_back", "walked", "terminal", "disabled_color", "port_hover")
    s0 = fc.resolve_style({})
    check(all(k in s0 for k in keys), "resolve_style({}) 回退键齐全")
    check(fc.resolve_style({"style": {"edge": "#123456"}})["edge"] == "#123456",
          "ctx['style'] 覆盖回退 (edge)")
    check(fc.resolve_style({"dark": True})["shadow"] == "#334155", "dark 回退 shadow")


# ── 实机 (可用时) ────────────────────────────────────────────────────
# 浅/深两套结构色 (对应 ui.workflow_view._FLOW_STYLE 的两种主题取值口径)
STYLE_LIGHT = {
    "flowbg": "#f8fafc", "disabled_mix": 0.4, "shadow": "#E1E1E1",
    "text_on_node": "#FFFFFF", "text_sub": "#E2E8F0",
    "edge": "#5F5F5F", "edge_sel": "#0078D4", "edge_reject": "#C42B1C",
    "edge_back": "#8B5CF6", "walked": "#107C10", "terminal": "#5F5F5F",
    "disabled_color": "#94A3B8", "port_hover": "#0078D4",
}
STYLE_DARK = {
    "flowbg": "#0f172a", "disabled_mix": 0.4, "shadow": "#334155",
    "text_on_node": "#FFFFFF", "text_sub": "#CBD5E0",
    "edge": "#A0A0A0", "edge_sel": "#4CC2FF", "edge_reject": "#FF99A4",
    "edge_back": "#8B5CF6", "walked": "#6CCB5F", "terminal": "#A0A0A0",
    "disabled_color": "#4A4A4A", "port_hover": "#4CC2FF",
}

# 节点类型色 (真源 ui.workflow_view._FLOW_COLORS 的抽样)
FLOW_COLORS = {"log": "#64748B", "wait": "#9CA3AF", "script": "#3B82F6"}

# 断言用工作流: 含启用/禁用/长详情三类节点, 覆盖 A2/A3/A7
WF = {"name": "t", "steps": [
    {"type": "log", "text": "a"},
    {"type": "wait", "seconds": 1, "enabled": False},
    {"type": "script", "path": "C:\\very\\long\\dir\\abcdefghijklmnopqrstuvwxyz.py"},
]}

_BASE_FONT = {"body": 9, "small": 8, "small_bold": 8}


def _sp_of(scale):
    """尺寸令牌: 设计 px → 设计 px * ui_scale (等价 utils.scaled 去掉恒定 dpi_factor)。"""
    return (lambda v: float(v) * float(scale))


def _fonts_of(scale):
    """字体随 ui_scale 线性放大 (镜像 utils.fit_pt: max(6, round(size*ui_scale)))。"""
    def px(base):
        return max(6, int(round(base * float(scale))))
    return {"body": ("Segoe UI", px(_BASE_FONT["body"])),
            "small": ("Segoe UI", px(_BASE_FONT["small"])),
            "small_bold": ("Segoe UI", px(_BASE_FONT["small_bold"]), "bold")}


def _ctx_of(scale, dark):
    return {"dark": bool(dark), "style": (STYLE_DARK if dark else STYLE_LIGHT),
            "sp": _sp_of(scale), "type_labels": {}, "icons": {},
            "index_tags": {}, "show_ports": True}


def _node_shapes(cv):
    out = []
    for it in cv.find_all():
        tags = [str(t) for t in cv.gettags(it)]
        if any(t.startswith("fgnode_") for t in tags) and cv.type(it) in ("polygon", "rectangle"):
            out.append(it)
    return out


def _node_id_of(cv, item):
    for t in cv.gettags(item):
        t = str(t)
        if t.startswith("fgnode_"):
            return t[len("fgnode_"):]
    return None


def _node_rect_map(cv):
    """→ {node_id: (x1, y1, x2, y2)}: 取每个节点面积最大的图元作为节点矩形。"""
    rects = {}
    for it in _node_shapes(cv):
        nid = _node_id_of(cv, it)
        if nid is None:
            continue
        co = cv.coords(it)
        xs, ys = co[0::2], co[1::2]
        box = (min(xs), min(ys), max(xs), max(ys))
        old = rects.get(nid)
        if old is None or ((box[2] - box[0]) * (box[3] - box[1])
                           > (old[2] - old[0]) * (old[3] - old[1])):
            rects[nid] = box
    return rects


def _node_widths(cv):
    """所有节点矩形的宽度集合 (A1: 期望 == 200 * ui_scale)。"""
    return sorted({round(r[2] - r[0], 2) for r in _node_rect_map(cv).values()})


def _text_overflow(cv, tol=FIT_TOL):
    """→ (measured, bad): 逐节点文本 bbox 与其所在节点矩形比对。

    bad 项 = (nid, text_w, text_h, rect_w, rect_h, (左越, 右越, 上越, 下越))。
    measured == 0 表示本组一个文本 bbox 都没量到 (应记 WARN, 不得当 PASS)。
    """
    rects = _node_rect_map(cv)
    measured, bad = 0, []
    for it in cv.find_all():
        if cv.type(it) != "text":
            continue
        nid = _node_id_of(cv, it)
        if nid is None or nid not in rects:
            continue
        bb = cv.bbox(it)
        if not bb:
            continue
        measured += 1
        x1, y1, x2, y2 = rects[nid]
        over = (x1 - bb[0], bb[2] - x2, y1 - bb[1], bb[3] - y2)   # >0 = 越界
        if max(over) > tol:
            bad.append((nid, round(bb[2] - bb[0], 1), round(bb[3] - bb[1], 1),
                        round(x2 - x1, 1), round(y2 - y1, 1),
                        tuple(round(o, 1) for o in over)))
    return measured, bad


def t_real_smoke():
    print("\n── 实机断言 (render) ──")
    try:
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
    except Exception as e:
        warn("无 GUI 环境, 跳过实机断言 (A1/A2/A3/A7 全部未验证): {}".format(e))
        return
    try:
        import flow_graph as fg
        from ui import flow_canvas as fc

        def _render(scale, dark):
            cv = tk.Canvas(root, width=1120, height=840)
            fc.render(cv, fg.promote(WF), colors=FLOW_COLORS, fonts=_fonts_of(scale),
                      ctx=_ctx_of(scale, dark), zoom=1.0)
            return cv

        # ── A2 (ui_scale=1.0, 浅色): 连线线宽 == sp(1); 端口半径 == sp(4) ──
        cv = _render(1.0, False)
        widths = set()
        for it in cv.find_all():
            if cv.type(it) == "line" and "arrow" in [str(t) for t in cv.gettags(it)]:
                widths.add(round(float(cv.itemcget(it, "width")), 3))
        check(widths == {1.0}, "A2: zoom==1.0 连线线宽 == sp(1)=1.0 ({})".format(sorted(widths)))
        radii = set()
        for it in cv.find_all():
            if "port" in [str(t) for t in cv.gettags(it)]:
                x1, y1, x2, y2 = cv.coords(it)
                radii.add(round((x2 - x1) / 2.0, 3))
        check(radii == {4.0}, "A2: 端口半径 == sp(4)=4.0 ({})".format(sorted(radii)))

        # ── A3: 禁用节点填充 == blend(类型色, flowbg, 0.4) 且 != #94A3B8 ──
        expect = fc.blend("#9CA3AF", STYLE_LIGHT["flowbg"], 0.4)
        fills = [cv.itemcget(it, "fill") for it in _node_shapes(cv)]
        check(expect in fills, "A3: 禁用节点填充 == blend(类型色,flowbg,0.4)={}".format(expect))
        check("#94A3B8" not in fills, "A3: 禁用节点未再整体变灰 (#94A3B8 不出现)")
        cv.destroy()

        # ── A1: node_w 随 ui_scale 线性 (4 档) + 显式 1.0 vs 1.5 ≈ 1.5 ──
        print("\n  [A1] node_w 乘子线性 (ui_scale {} 档)".format(list(SCALES)))
        a1_w = {}
        for scale in SCALES:
            cvx = _render(scale, False)
            ws = _node_widths(cvx)
            cvx.destroy()
            if not ws:
                warn("A1: ui_scale={:.2f} 未量到节点矩形 (无法验证)".format(scale))
                continue
            a1_w[scale] = min(ws)
            print("       ui_scale={:<5} node_w={:<8} (实测集合={})".format(
                scale, a1_w[scale], ws))
        base = a1_w.get(1.0)
        check(base is not None and abs(base - 200.0) < 0.01,
              "A1: ui_scale=1.0 下 node_w == 200 设计 px ({})".format(base))
        if base is None:
            warn("A1: 缺 ui_scale=1.0 基准, 比值断言无法进行")
        else:
            for scale in sorted(a1_w):
                ratio = a1_w[scale] / base
                check(abs(ratio - scale) <= RATIO_TOL,
                      "A1: node_w({:.2f})/node_w(1.0) = {:.4f} ≈ {:.2f} (±{})".format(
                          scale, ratio, scale, RATIO_TOL))
            if 1.5 in a1_w:
                r15 = a1_w[1.5] / base
                check(abs(r15 - 1.5) <= RATIO_TOL,
                      "A1(§8硬性): ui_scale=1.0 vs 1.5 下 node_w 比 = {:.4f} ≈ 1.5 "
                      "({} -> {})".format(r15, base, a1_w[1.5]))
            else:
                warn("A1: 缺 ui_scale=1.5 档, 无法显式断言 1.0 vs 1.5 比值")

        # ── A7: ui_scale × 深/浅 = 8 组, 逐组断言文本不越节点矩形 ──
        print("\n  [A7] 文本 bbox vs 节点矩形 ({} 组)".format(len(SCALES) * len(DARKS)))
        a7_ok, a7_warn = 0, 0
        for scale in SCALES:
            for dark in DARKS:
                cvx = _render(scale, dark)
                measured, bad = _text_overflow(cvx)
                cvx.destroy()
                tag = "A7: ui_scale={:<5} {} ".format(
                    scale, "深色" if dark else "浅色")
                if measured == 0:
                    a7_warn += 1
                    warn(tag + "无法测量文本 bbox (无文本项/度量不可用), 记 WARN 非 PASS")
                elif bad:
                    check(False, tag + "文本越界 {}".format(bad))
                else:
                    a7_ok += 1
                    check(True, tag + "两行文本 bbox 未超节点矩形 (measured={})".format(measured))
        cov = a7_ok + a7_warn
        check(cov == len(SCALES) * len(DARKS),
              "A7: 组合覆盖 {}/{} (PASS={}, WARN={})".format(
                  cov, len(SCALES) * len(DARKS), a7_ok, a7_warn))
        root.destroy()
    except Exception as e:
        warn("实机断言异常 (记 WARN): {}".format(e))
        try:
            root.destroy()
        except Exception:
            pass


def main():
    print("=== 工作流 P0 (_FLOW_STYLE/blend/渲染乘子) 离屏断言 ===")
    t_a6_source_scan()
    t_pure()
    t_real_smoke()
    print("\n" + "-" * 56)
    print("FAIL={}  WARN={}  {}".format(len(_FAIL), len(_WARN),
                                       "OK" if not _FAIL else "FAIL"))
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
