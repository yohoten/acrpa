#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""状态切换颜色渐变 P2-2 回归自测 (GradientItem 语义)。

用法:
    .venv\\Scripts\\python.exe -X utf8 tests\\_test_flow_canvas_gradient.py

覆盖:
  A 图元定位    : node_shape_item 命中节点主形状 (跳过 shadow 空 outline)。
  B 颜色渐变基元: animate_item_color 开动效时中间帧存在、终态=target;
                  reduce_motion 时一步到位且不调度 after (返回 None)。
  C 高亮步进渐变: animate_nodes_outline 开动效时旧/新节点 outline 中间帧存在、
                  终态正确迁移 (旧复原/新点亮); reduce_motion 瞬时对齐。
  D 脏重绘正交  : animate_nodes_outline 只 itemconfigure 受影响节点图元,
                  不新增/删除图元 (图元总数不变) —— 与 P2-7 脏矩形正交。

退出码: 0=全部通过 / 1=有失败。
"""
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


class FakeRoot:
    """鸭子类型 root: 记录 after 任务, 可逐条执行 (观察中间帧)。"""

    def __init__(self):
        self.queue = []       # [(tid, delay, func, args)]
        self.cancelled = set()
        self._next = 0

    def after(self, delay, func=None, *args):
        self._next += 1
        tid = "after#%d" % self._next
        self.queue.append((tid, delay, func, args))
        return tid

    def after_cancel(self, tid):
        self.cancelled.add(tid)

    def run_n(self, n):
        """按调度顺序执行 n 条未取消任务 (新调度者留待下次)。"""
        fired = 0
        while fired < n and self.queue:
            tid, delay, func, args = self.queue.pop(0)
            if tid in self.cancelled:
                continue
            if func is not None:
                func(*args)
            fired += 1
        return fired

    def flush(self):
        """执行队列中全部未取消任务。"""
        n = 0
        while self.queue:
            n += self.run_n(1)
        self.cancelled.clear()
        return n


import flow_graph as fg            # noqa: E402
from ui import flow_canvas as fc   # noqa: E402


def _mkctx(step, index_tags):
    return {"current_step": step, "step_results": {}, "sc": "#FF0000",
            "dg": "#DC2626", "index_tags": index_tags, "show_ports": False,
            "grid": False, "style": {"shadow": "#CCCCCC",
                                     "text_on_node": "#FFFFFF",
                                     "edge": "#9CA3AF", "edge_back": "#F59E0B",
                                     "terminal": "#6B7280", "flowbg": "#FFFFFF",
                                     "walked": "#FF0000", "edge_sel": "#2563EB",
                                     "edge_reject": "#DC2626"},
            "sp": lambda v: v}


def _shape_outline(canvas, nid):
    try:
        for it in canvas.find_withtag("fgnode_" + str(nid)):
            if canvas.type(it) in ("polygon", "rectangle"):
                o = canvas.itemcget(it, "outline") or ""
                if o:
                    return o
    except Exception:
        pass
    return None


def _make_scene():
    """建 Tk + Canvas + 3 节点线性图并全量渲染 (高亮 step0)。失败返回 (None,)*5。"""
    try:
        import tkinter
    except Exception as e:
        warn("tkinter 不可用, 跳过实机: {}".format(e))
        return None, None, None, None, None
    try:
        root = tkinter.Tk()
        root.withdraw()
    except Exception as e:
        warn("无法创建 Tk root (无 GUI), 跳过实机: {}".format(e))
        return None, None, None, None, None
    canvas = tkinter.Canvas(root, width=520, height=360)
    canvas.pack()
    wf = {"name": "grad", "steps": [
        {"type": "log", "text": "s{}".format(i)} for i in range(3)]}
    g = fg.promote(wf)
    roots = g.roots()
    ids = [n.id for n in roots]
    index_tags = {n.id: "node_{}".format(i) for i, n in enumerate(roots)}
    colors = {"log": "#22C55E"}
    fonts = {"body": ("Segoe UI", 9), "small": ("Segoe UI", 8),
             "small_bold": ("Segoe UI", 8, "bold")}
    fc.render(canvas, g, colors=colors, fonts=fonts, ctx=_mkctx(0, index_tags))
    return root, canvas, g, ids, index_tags


def _teardown(root):
    if root is not None:
        try:
            root.destroy()
        except Exception:
            pass


def t_shape_item():
    print("\n── A. node_shape_item 图元定位 ──")
    root, canvas, g, ids, index_tags = _make_scene()
    if canvas is None:
        return
    try:
        shape = fc.node_shape_item(canvas, ids[0])
        check(shape is not None, "node_shape_item 命中节点主形状")
        check(canvas.type(shape) in ("polygon", "rectangle"),
              "命中的是 polygon/rectangle", "type={}".format(canvas.type(shape)))
        check((canvas.itemcget(shape, "outline") or "") != "",
              "命中图元 outline 非空 (非 shadow)")
    finally:
        _teardown(root)


def t_item_color():
    print("\n── B. animate_item_color 颜色渐变基元 ──")
    root, canvas, g, ids, index_tags = _make_scene()
    if canvas is None:
        return
    try:
        # 建一个独立矩形图元做基元测试 (不干扰节点)
        item = canvas.create_rectangle(4, 4, 40, 20, outline="#000000")

        # B1: 开动效 → 中间帧存在 + 终态 = target
        fake = FakeRoot()
        anim = fc.animate_item_color(canvas, item, "outline", "#FF0000",
                                     duration=120, root=fake,
                                     reduce_motion=lambda: False)
        check(anim is not None, "开动效返回 Animation 实例")
        check(anim.active is True, "开动效 animation.active=True")
        check(fake.run_n(1) == 1, "推进 1 帧成功")
        mid = canvas.itemcget(item, "outline") or ""
        check(mid not in ("#000000", "#FF0000"),
              "开动效中间帧存在 (非起点/非终点)", "mid={}".format(mid))
        fake.flush()
        check((canvas.itemcget(item, "outline") or "") == "#FF0000",
              "开动效终态=target #FF0000")

        # B2: reduce_motion → 一步到位 + 不调度 after + 返回 None
        canvas.itemconfigure(item, outline="#000000")
        fake2 = FakeRoot()
        anim2 = fc.animate_item_color(canvas, item, "outline", "#00FF00",
                                      duration=120, root=fake2,
                                      reduce_motion=lambda: True)
        check(anim2 is None, "reduce_motion 一步到位返回 None")
        check((canvas.itemcget(item, "outline") or "") == "#00FF00",
              "reduce_motion 瞬时对齐 target")
        check(len(fake2.queue) == 0, "reduce_motion 未调度任何 after 任务")
    finally:
        _teardown(root)


def t_highlight_gradient():
    print("\n── C. animate_nodes_outline 高亮步进渐变 ──")
    root, canvas, g, ids, index_tags = _make_scene()
    if canvas is None:
        return
    try:
        # 起点: 全量渲染 (高亮 step0) → node0 描边=sc, node1 描边=类型色
        check(_shape_outline(canvas, ids[0]) == "#FF0000", "起点 node0 高亮(sc)")
        check(_shape_outline(canvas, ids[1]) == "#22C55E", "起点 node1 复原(类型色)")

        total_before = len(canvas.find_all())

        # C1: 开动效 → 中间帧存在 + 终态正确迁移
        fake = FakeRoot()
        anims = fc.animate_nodes_outline(
            canvas, g, [ids[0], ids[1]], colors={"log": "#22C55E"},
            ctx=_mkctx(1, index_tags), zoom=1.0, root=fake,
            reduce_motion=lambda: False)
        check(len(anims) == 2, "开动效返回 2 个 Animation (旧+新节点)")
        fake.run_n(1)
        mid0 = _shape_outline(canvas, ids[0]) or ""
        check(mid0 not in ("#FF0000", "#22C55E"),
              "旧节点中间帧存在 (非 sc/非类型色)", "mid0={}".format(mid0))
        fake.flush()
        check(_shape_outline(canvas, ids[0]) == "#22C55E", "终态 node0 复原(类型色)")
        check(_shape_outline(canvas, ids[1]) == "#FF0000", "终态 node1 点亮(sc)")

        # D: 只 itemconfigure 不增删图元
        check(len(canvas.find_all()) == total_before,
              "动画只 itemconfigure, 图元总数不变 (与脏重绘正交)")

        # C2: reduce_motion → 瞬时对齐
        anims2 = fc.animate_nodes_outline(
            canvas, g, [ids[0], ids[1]], colors={"log": "#22C55E"},
            ctx=_mkctx(0, index_tags), zoom=1.0,
            reduce_motion=lambda: True)
        check(anims2 == [], "reduce_motion 一步到位返回空列表")
        check(_shape_outline(canvas, ids[0]) == "#FF0000",
              "reduce_motion 瞬时: node0 点亮")
        check(_shape_outline(canvas, ids[1]) == "#22C55E",
              "reduce_motion 瞬时: node1 复原")
    finally:
        _teardown(root)


def main():
    print("=== 状态切换颜色渐变 P2-2: GradientItem 语义 ===")
    t_shape_item()
    t_item_color()
    t_highlight_gradient()

    print("\n" + "-" * 60)
    print("PASS={} FAIL={} WARN={}".format(len(_PASS), len(_FAIL), len(_WARN)))
    for m in _FAIL:
        print("[FAIL] {}".format(m))
    print("=== 结果: {} ===".format("FAIL" if _FAIL else "OK"))
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
