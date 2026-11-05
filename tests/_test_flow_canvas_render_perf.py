#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""流程图渲染 P2-7 回归自测: 动效 token 补齐 + 脏矩形局部重绘正确性/性能对比。

用法:
    .venv\\Scripts\\python.exe -X utf8 tests\\_test_flow_canvas_render_perf.py

覆盖:
  A  动效 token : utils.TOKENS 补齐 motion_frame/fast/base/slow, 且暴露
                  motion()/motion_frame()/reduce_motion()/ease() 纯访问器;
                  flow_canvas.render 支持 dirty 入参 (P2-7 脏矩形局部重绘入口)。
  B  脏重绘正确性 : 实机建 Tk → promote 40 节点线性图 → 全量渲染 (高亮 step0) →
                  dirty 局部切到 step1 → 断言画布图元总数与「同状态全量渲染」逐位一致
                  (无孤儿/无重复), 且高亮描边正确迁移 (旧节点复原, 新节点点亮)。
  C  性能对比    : 40 节点下全量 render 与单节点 dirty render 各测多轮取最小耗时,
                  dirty 应显著快于全量 (O(受影响节点) vs O(全图)); 无 GUI 环境 WARN 跳过。

退出码: 0=全部通过 / 1=有失败。
"""
import io
import os
import sys
import time

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


import inspect                     # noqa: E402
import utils                       # noqa: E402
import flow_graph as fg            # noqa: E402
from ui import flow_canvas as fc   # noqa: E402


# ── A. 动效 token 补齐 / dirty 入口 ──────────────────────────────────
def t_motion_tokens():
    print("\n── A. 动效 token 补齐 + dirty 入口 ──")
    for key in ("motion_frame", "motion_fast", "motion_base", "motion_slow"):
        check(key in utils.TOKENS, "utils.TOKENS 补齐 {}".format(key))
    check(utils.motion() == 200, "motion() 默认 motion_base=200ms")
    check(utils.motion("motion_fast") == 120, "motion('motion_fast')==120ms")
    check(utils.motion("motion_slow") == 320, "motion('motion_slow')==320ms")
    check(utils.motion_frame() == 16, "motion_frame()==16ms")
    check(isinstance(utils.reduce_motion(), bool), "reduce_motion() 返回 bool")
    check(abs(utils.ease(0.5) - 0.875) < 1e-9, "ease(0.5) out_cubic==0.875")
    check(utils.ease(0.5, "linear") == 0.5, "ease(0.5, 'linear')==0.5")
    check(utils.ease(0.0) == 0.0 and utils.ease(1.0) == 1.0, "ease 端点归一化")

    sig = inspect.signature(fc.render)
    check("dirty" in sig.parameters, "flow_canvas.render 支持 dirty 入参")
    check(hasattr(fc, "_as_ids") and hasattr(fc, "_render_dirty"),
          "flow_canvas 暴露 _as_ids/_render_dirty 辅助")
    # 标签增强: 阴影带节点标签、箭头/标签带 edge:<eid> —— 保证局部删除无孤儿图元
    src = _read("src/ui/flow_canvas.py")
    check('("shadow",) + tuple(tags)' in src, "阴影带 fgnode_<id> 标签 (可整组删除)")
    check('"edge:" + str(e.id)' in src, "箭头/边标签带 edge:<eid> 标签")


# ── B/C. 实机 (WARN 守卫) ────────────────────────────────────────────
def _shape_outline(canvas, nid):
    """节点 nid 的形状描边色 (跳过 shadow 的空 outline)。"""
    try:
        for it in canvas.find_withtag("fgnode_" + str(nid)):
            if canvas.type(it) in ("polygon", "rectangle"):
                o = canvas.itemcget(it, "outline") or ""
                if o:
                    return o
    except Exception:
        pass
    return None


def _node_item_count(canvas, nid):
    try:
        return len(canvas.find_withtag("fgnode_" + str(nid)))
    except Exception:
        return -1


def t_real_smoke():
    print("\n── B/C. 实机: 脏重绘正确性 + 性能对比 ──")
    try:
        import tkinter
    except Exception as e:
        warn("tkinter 不可用, 跳过实机: {}".format(e))
        return
    root = None
    try:
        root = tkinter.Tk()
        root.withdraw()
    except Exception as e:
        warn("无法创建 Tk root (无 GUI), 跳过实机: {}".format(e))
        return
    try:
        N = 40
        canvas = tkinter.Canvas(root, width=520, height=360)
        canvas.pack()

        wf = {"name": "perf", "steps": [
            {"type": "log", "text": "s{}".format(i)} for i in range(N)]}
        g = fg.promote(wf)
        roots = g.roots()
        ids = [n.id for n in roots]
        index_tags = {n.id: "node_{}".format(i) for i, n in enumerate(roots)}

        colors = {"log": "#22C55E"}
        fonts = {"body": ("Segoe UI", 9), "small": ("Segoe UI", 8),
                 "small_bold": ("Segoe UI", 8, "bold")}
        sp = lambda v: v

        def mkctx(step):
            return {"current_step": step, "step_results": {}, "sc": "#FF0000",
                    "dg": "#DC2626", "index_tags": index_tags, "show_ports": False,
                    "grid": False, "style": {"shadow": "#CCCCCC",
                                             "text_on_node": "#FFFFFF",
                                             "edge": "#9CA3AF", "edge_back": "#F59E0B",
                                             "terminal": "#6B7280", "flowbg": "#FFFFFF",
                                             "walked": "#FF0000", "edge_sel": "#2563EB",
                                             "edge_reject": "#DC2626"},
                    "sp": sp}

        # 1) 全量渲染 (高亮 step0) 作为脏重绘的起点
        fc.render(canvas, g, colors=colors, fonts=fonts, ctx=mkctx(0))
        check(True, "全量 render(40 节点) 未抛异常")

        # 2) 参考: 同状态 (高亮 step1) 的全量渲染图元总数
        total_full = len(canvas.find_all())
        fc.render(canvas, g, colors=colors, fonts=fonts, ctx=mkctx(1))
        total_ref = len(canvas.find_all())
        # 恢复起点
        fc.render(canvas, g, colors=colors, fonts=fonts, ctx=mkctx(0))

        # 3) 脏矩形局部重绘: 只重画 step0 + step1 (含其关联边)
        dirty = [ids[0], ids[1]]
        fc.render(canvas, g, colors=colors, fonts=fonts, ctx=mkctx(1), dirty=dirty)
        total_dirty = len(canvas.find_all())

        check(total_dirty == total_ref,
              "脏重绘图元总数与全量一致 (无孤儿/无重复)",
              "dirty={} full={}".format(total_dirty, total_ref))

        # 节点级图元计数不变 (每个 log 节点 = shadow+shape+text = 3)
        cnt0 = _node_item_count(canvas, ids[0])
        cnt1 = _node_item_count(canvas, ids[1])
        cnt2 = _node_item_count(canvas, ids[2])
        check(cnt0 == 3 and cnt1 == 3 and cnt2 == 3,
              "脏重绘后每节点图元计数不变 (shadow+shape+text=3)",
              "n0={} n1={} n2={}".format(cnt0, cnt1, cnt2))

        # 高亮迁移: step0 复原, step1 点亮
        check(_shape_outline(canvas, ids[1]) == "#FF0000",
              "脏重绘后 step1 描边点亮为 sc")
        check(_shape_outline(canvas, ids[0]) == "#22C55E",
              "脏重绘后 step0 描边复原为类型色")

        # 4) 性能对比: 全量 vs 单节点脏重绘 (各 5 轮取最小, 抗 GC 噪声)
        def _timeit(fn, rounds=5):
            best = None
            for _ in range(rounds):
                t0 = time.perf_counter()
                fn()
                dt = (time.perf_counter() - t0) * 1000.0
                best = dt if best is None else min(best, dt)
            return best

        full_ms = _timeit(lambda: fc.render(canvas, g, colors=colors, fonts=fonts,
                                            ctx=mkctx(1)))
        dirty_ms = _timeit(lambda: fc.render(canvas, g, colors=colors, fonts=fonts,
                                             ctx=mkctx(1), dirty=[ids[0]]))
        print("  [baseline] 全量 render ({} 节点) = {:.3f} ms".format(N, full_ms))
        print("  [dirty    ] 单节点局部重绘        = {:.3f} ms".format(dirty_ms))
        check(dirty_ms < full_ms,
              "单节点脏重绘快于全量 (O(受影响) vs O(全图))",
              "dirty={:.3f}ms full={:.3f}ms".format(dirty_ms, full_ms))
    finally:
        try:
            root.destroy()
        except Exception:
            pass


def main():
    print("=== 流程图渲染 P2-7: 动效 token + 脏矩形局部重绘 ===")
    t_motion_tokens()
    t_real_smoke()

    print("\n" + "-" * 60)
    print("PASS={} FAIL={} WARN={}".format(len(_PASS), len(_FAIL), len(_WARN)))
    for m in _FAIL:
        print("[FAIL] {}".format(m))
    print("=== 结果: {} ===".format("FAIL" if _FAIL else "OK"))
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
