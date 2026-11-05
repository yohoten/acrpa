#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""core.hit 纯几何命中回归自测 (路线图 P3-3)。

用法:
    .venv\\Scripts\\python.exe -X utf8 tests\\_test_hit.py

覆盖:
  A  core.hit 纯几何语义: point_in_rect (含 pad / 边界闭区间 / 坏 rect 容错);
     detect_point (后绘制者优先) / RegionIndex (rebuild/query/len/boxes/bool)。
  B  纯度护栏: core.hit 零 tkinter (AST 实检 import 语句)。
  C  接线护栏: ui.flow_canvas.node_at 走几何 RegionIndex (render 灌 _node_regions);
     ui.workflow_view 用共享 `node` 标签 + detect_point 替代逐节点 tag_bind。

退出码: 0=全部通过 / 1=有失败。
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

_PASS, _FAIL = [], []


def check(cond, msg, detail=""):
    (_PASS if cond else _FAIL).append(msg)
    tail = ("  ({})".format(detail) if detail else "")
    print("[{}] {}{}".format("OK  " if cond else "FAIL", msg, tail))


def _read(rel):
    with io.open(os.path.join(ROOT, rel), "r", encoding="utf-8") as f:
        return f.read()


from core import hit  # noqa: E402


def t_geometry():
    print("\n── A. core.hit 纯几何语义 ──")
    check(hit.point_in_rect(5, 5, (0, 0, 10, 10)), "point_in_rect 内点命中")
    check(hit.point_in_rect(0, 0, (0, 0, 10, 10)), "point_in_rect 左/上边界闭区间命中")
    check(hit.point_in_rect(10, 10, (0, 0, 10, 10)), "point_in_rect 右/下边界闭区间命中")
    check(not hit.point_in_rect(11, 5, (0, 0, 10, 10)), "point_in_rect 外点未命中")
    check(hit.point_in_rect(11, 5, (0, 0, 10, 10), pad=2), "point_in_rect pad 外扩命中")
    check(not hit.point_in_rect(20, 5, (0, 0, 10, 10), pad=2), "point_in_rect pad 之外未命中")
    check(not hit.point_in_rect(5, 5, None), "point_in_rect 坏 rect 容错 (不抛)")

    regions = [("a", (0, 0, 10, 10)), ("b", (5, 5, 20, 20))]
    check(hit.detect_point(8, 8, regions) == "b", "detect_point 后绘制者优先 (重叠取 topmost)")
    check(hit.detect_point(2, 2, regions) == "a", "detect_point 只命中 a")
    check(hit.detect_point(30, 30, regions) is None, "detect_point 未命中 → None")
    check(hit.detect_point(30, 30, regions, pad=11) == "b", "detect_point pad 命中")

    idx = hit.RegionIndex(regions)
    check(len(idx) == 2, "RegionIndex.len")
    check(idx.query(8, 8) == "b", "RegionIndex.query topmost")
    check(idx.query(2, 2) == "a", "RegionIndex.query 单命中")
    check(idx.query(30, 30) is None, "RegionIndex.query 未命中")
    check(idx.boxes() == regions, "RegionIndex.boxes 只读副本")
    check(bool(idx) is True, "RegionIndex.bool 非空")
    idx.rebuild([])
    check(bool(idx) is False and len(idx) == 0 and idx.query(0, 0) is None,
          "RegionIndex.rebuild 清空后可安全查询")


def t_purity():
    print("\n── B. 纯度护栏 ──")

    def _imports_tkinter(node):
        if isinstance(node, ast.Import):
            return any(a.name.split(".")[0] == "tkinter" for a in node.names)
        if isinstance(node, ast.ImportFrom):
            return bool(node.module) and node.module.split(".")[0] == "tkinter"
        return False

    sc = _read("src/core/hit.py")
    has_tk = any(_imports_tkinter(n) for n in ast.walk(ast.parse(sc)))
    check(not has_tk, "core.hit 零 tkinter (AST 实检 import)")


def t_wiring():
    print("\n── C. 接线护栏 (源码文本) ──")
    fc = _read("src/ui/flow_canvas.py")
    check("_node_regions" in fc and "RegionIndex" in fc,
          "flow_canvas 引入 RegionIndex 几何命中")
    check("canvas._node_regions = RegionIndex" in fc,
          "flow_canvas.render 渲染后灌入 canvas._node_regions")
    check("idx.query(x, y, pad)" in fc,
          "flow_canvas.node_at 优先走 RegionIndex.query (回退 _tags_at)")

    wv = _read("src/ui/workflow_view.py")
    check("_wf_set_node_regions(" in wv and "detect_point(" in wv,
          "workflow_view 几何命中 (set_regions + detect_point)")
    check('tag_bind("node", "<Button-1>"' in wv and 'tag_bind("node", "<B1-Motion>"' in wv,
          "workflow_view 共享 `node` 标签一次性注册 (替代逐节点 tag_bind)")
    check("for i in range(len(roots)):" not in wv.split("_wf_set_node_regions")[0][-200:],
          "workflow_view 已移除 flow_canvas 路径逐节点 tag_bind 循环")


def main():
    print("=== core.hit 纯几何命中回归自测 (P3-3) ===")
    t_geometry()
    t_purity()
    t_wiring()
    print("\n" + "-" * 60)
    print("PASS={} FAIL={}".format(len(_PASS), len(_FAIL)))
    for m in _FAIL:
        print("[FAIL] {}".format(m))
    print("=== 结果: {} ===".format("FAIL" if _FAIL else "OK"))
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
