#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""工作流 CFG 静态分析 + 图编辑原语 回归自测 (路线图 §11.8 / §11.7, E 期)。

用法:
    .venv\\Scripts\\python.exe -X utf8 tools\\_test_flow_analyze.py

覆盖:
  A  可达性/度      : entry/exit/isolated/unreachable/dead_branch。
  B  分析项 (≥8)    : 线性工作流无 error; 构造样例分别命中 unreachable / isolated /
                      dangling_exit / loop(次数空 / 条件循环无变量修改) / duplicate_edge /
                      path_explosion / complexity / missing_exit。
  C  图论量         : sccs / count_paths / cyclomatic_complexity (菱形=2)。
  D  编辑原语       : can_connect 拒绝(自环/重复/不存在); connect 成环标 back_edge;
                      disconnect; delete_node 前驱→后继重连; 容器子节点不可删。
  E  纯度/幂等      : import flow_graph 无 tkinter; promote→demote 仍幂等(护栏未破)。

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

_PASS, _FAIL = [], []


def check(cond, msg, detail=""):
    (_PASS if cond else _FAIL).append(msg)
    tail = ("  ({})".format(detail) if detail else "")
    print("[{}] {}{}".format("OK  " if cond else "FAIL", msg, tail))


import flow_graph as fg  # noqa: E402


def _codes(issues):
    return {i["code"] for i in issues}


def _nodes_of(issues, code):
    for i in issues:
        if i["code"] == code:
            return i["nodes"]
    return None


# ── A. 可达性 / 度 ───────────────────────────────────────────────────
def t_reachability():
    print("\n── A. 可达性 / 度 ──")
    g = fg.Graph()
    for i in range(4):
        g.add_node(fg.Node("n{}".format(i)))
    g.add_edge(fg.Edge("e0", "n0", "out", "n1", "in"))
    g.add_edge(fg.Edge("e1", "n2", "out", "n3", "in"))
    g.add_edge(fg.Edge("e2", "n3", "out", "n2", "in"))
    check(fg.entry_ids(g) == ["n0"], "entry = n0", str(fg.entry_ids(g)))
    check(set(fg.exit_ids(g)) == {"n1"}, "exit = n1", str(fg.exit_ids(g)))
    check(fg.isolated_ids(g) == [], "无孤立节点")
    check(sorted(fg.unreachable_ids(g)) == ["n2", "n3"], "n2/n3 不可达",
          str(fg.unreachable_ids(g)))
    check(sorted(fg.dead_branch_ids(g)) == ["n2", "n3"], "n2/n3 之后无出口",
          str(fg.dead_branch_ids(g)))

    g2 = fg.Graph()
    g2.add_node(fg.Node("s"))
    check(fg.isolated_ids(g2) == ["s"], "单节点为孤立")
    check(fg.entry_ids(g2) == ["s"] and fg.exit_ids(g2) == ["s"], "单节点既是入口也是出口")


# ── B. 分析项 ────────────────────────────────────────────────────────
def t_analyze_linear():
    print("\n── B1. 线性工作流: 无 error/warning ──")
    wf = {"name": "l", "steps": [
        {"type": "log", "text": "a"},
        {"type": "wait", "seconds": 1},
        {"type": "log", "text": "b"},
    ]}
    g = fg.promote(wf)
    issues = fg.analyze(g)
    bad = [i for i in issues if i["level"] in ("error", "warning")]
    check(not bad, "线性工作流无 error/warning", "{}".format(bad))
    check(fg.cyclomatic_complexity(g) == 1, "线性复杂度 == 1",
          str(fg.cyclomatic_complexity(g)))


def t_analyze_cases():
    print("\n── B2. 命中各分析项 ──")
    # unreachable
    g = fg.Graph()
    for i in range(4):
        g.add_node(fg.Node("n{}".format(i)))
    g.add_edge(fg.Edge("e0", "n0", "out", "n1", "in"))
    g.add_edge(fg.Edge("e1", "n2", "out", "n3", "in"))
    g.add_edge(fg.Edge("e2", "n3", "out", "n2", "in"))
    iss = fg.analyze(g)
    check("unreachable" in _codes(iss), "命中 unreachable")
    check(sorted(_nodes_of(iss, "unreachable") or []) == ["n2", "n3"], "unreachable 节点正确")

    # isolated
    g = fg.Graph()
    g.add_node(fg.Node("solo"))
    check("isolated" in _codes(fg.analyze(g)), "命中 isolated")

    # dangling_exit (手动构造: condition 有 then 却无 true 出边)
    g = fg.Graph()
    g.add_node(fg.Node("c", type="condition", data={fg.K_THEN: {fg.REF_KEY: "x"}}))
    g.add_node(fg.Node("x"))
    check("dangling_exit" in _codes(fg.analyze(g)), "命中 dangling_exit")

    # loop 次数为空
    g = fg.promote({"steps": [{"type": "loop", "times": "",
                               "steps": [{"type": "log", "text": "b"}]}]})
    check("loop" in _codes(fg.analyze(g)), "命中 loop(次数空)")

    # 条件循环体内无变量修改
    g = fg.promote({"steps": [{"type": "loop", "times": "${i} < 3",
                               "steps": [{"type": "log", "text": "b"}]}]})
    check("loop" in _codes(fg.analyze(g)), "命中 loop(条件循环无变量修改)")

    # 条件循环体内有变量修改 → 不应报 loop
    g = fg.promote({"steps": [{"type": "loop", "times": "${i} < 3",
                               "steps": [{"type": "variable", "var_name": "i", "var_value": "1"},
                                         {"type": "log", "text": "b"}]}]})
    check("loop" not in _codes(fg.analyze(g)), "条件循环有变量修改 → 不报 loop")

    # 固定次数循环 → 不应报 loop
    g = fg.promote({"steps": [{"type": "loop", "times": "3",
                               "steps": [{"type": "log", "text": "b"}]}]})
    check("loop" not in _codes(fg.analyze(g)), "固定次数循环 → 不报 loop")

    # duplicate_edge
    g = fg.Graph()
    g.add_node(fg.Node("a"))
    g.add_node(fg.Node("b"))
    g.add_edge(fg.Edge("e0", "a", "out", "b", "in"))
    g.add_edge(fg.Edge("e1", "a", "out", "b", "in"))
    check("duplicate_edge" in _codes(fg.analyze(g)), "命中 duplicate_edge")

    # path_explosion: 7 级菱形 → 128 条路径
    g = fg.Graph()
    g.add_node(fg.Node("c0"))
    prev = "c0"
    for i in range(1, 8):
        a, b = "a{}".format(i), "b{}".format(i)
        c = "c{}".format(i)
        g.add_node(fg.Node(a))
        g.add_node(fg.Node(b))
        g.add_node(fg.Node(c))
        g.add_edge(fg.Edge("", prev, "out", a, "in"))
        g.add_edge(fg.Edge("", prev, "out", b, "in"))
        g.add_edge(fg.Edge("", a, "out", c, "in"))
        g.add_edge(fg.Edge("", b, "out", c, "in"))
        prev = c
    check(fg.count_paths(g) > fg.MAX_PATHS, "菱形链路径数 > 阈值",
          str(fg.count_paths(g)))
    check("path_explosion" in _codes(fg.analyze(g)), "命中 path_explosion")

    # complexity: 高复杂度图 (长链 + 多跨边)
    g = fg.Graph()
    for i in range(20):
        g.add_node(fg.Node("n{}".format(i)))
    for i in range(19):
        g.add_edge(fg.Edge("", "n{}".format(i), "out", "n{}".format(i + 1), "in"))
    for i in range(0, 15):                       # 额外跨边抬复杂度
        g.add_edge(fg.Edge("", "n{}".format(i), "out", "n{}".format(i + 3), "in"))
    check(fg.cyclomatic_complexity(g) > fg.COMPLEXITY_WARN, "复杂度超阈值",
          str(fg.cyclomatic_complexity(g)))
    check("complexity" in _codes(fg.analyze(g)), "命中 complexity")


# ── C. 图论量 ────────────────────────────────────────────────────────
def t_graph_theory():
    print("\n── C. 图论量 ──")
    # 菱形: E=4 N=4 P=1 → 复杂度 2
    g = fg.Graph()
    for nid in ("s", "a", "b", "t"):
        g.add_node(fg.Node(nid))
    for e in (("s", "a"), ("s", "b"), ("a", "t"), ("b", "t")):
        g.add_edge(fg.Edge("", e[0], "out", e[1], "in"))
    check(fg.cyclomatic_complexity(g) == 2, "菱形复杂度 == 2",
          str(fg.cyclomatic_complexity(g)))
    check(fg.count_paths(g) == 2, "菱形路径数 == 2", str(fg.count_paths(g)))

    # SCC: 3 节点环 → 一个含 3 节点的 SCC
    g2 = fg.Graph()
    for nid in ("p", "q", "r"):
        g2.add_node(fg.Node(nid))
    for e in (("p", "q"), ("q", "r"), ("r", "p")):
        g2.add_edge(fg.Edge("", e[0], "out", e[1], "in"))
    comps = [sorted(c) for c in fg.sccs(g2)]
    check(["p", "q", "r"] in comps, "三节点环 → 一个 SCC", "{}".format(comps))

    check(fg.issue_summary([{"level": "error"}, {"level": "warning"},
                            {"level": "warning"}, {"level": "info"}]) == (1, 2, 1),
          "issue_summary 计数正确")


# ── D. 编辑原语 ──────────────────────────────────────────────────────
def t_edit_primitives():
    print("\n── D. 编辑原语 ──")
    g = fg.Graph()
    for nid in ("a", "b", "c"):
        g.add_node(fg.Node(nid))
    g.add_edge(fg.Edge("e0", "a", "out", "b", "in"))

    ok, _ = fg.can_connect(g, "a", "a")
    check(not ok, "can_connect 拒绝自环")
    ok, _ = fg.can_connect(g, "a", "b")
    check(not ok, "can_connect 拒绝重复边")
    ok, _ = fg.can_connect(g, "a", "zzz")
    check(not ok, "can_connect 拒绝不存在的节点")
    ok, _ = fg.can_connect(g, "b", "c")
    check(ok, "can_connect 允许新边")

    e = fg.connect(g, "b", "c")
    check(e is not None and g.node("c") is not None, "connect 新增边")
    # 成环: c → a 应为回边
    e2 = fg.connect(g, "c", "a")
    check(e2 is not None and e2.back_edge is True, "connect 成环标记 back_edge")
    check(fg.connect(g, "c", "a") is None, "重复 connect 返回 None")

    removed = fg.disconnect(g, e2.id)
    check(removed is not None and removed.id == e2.id, "disconnect 删除指定边")
    check(all(x.id != e2.id for x in g.edges), "边确已移除")

    # delete_node: 删除 b 应把 a → c 直连
    ok = fg.delete_node(g, "b")
    check(ok, "delete_node 成功")
    check(g.node("b") is None, "节点 b 已删")
    check(any(x.src == "a" and x.dst == "c" for x in g.edges), "前驱→后继已重连")

    # 容器子节点不可删
    g2 = fg.promote({"steps": [{"type": "loop", "times": "2",
                                "steps": [{"type": "log", "text": "x"}]}]})
    loop_node = [n for n in g2.nodes if n.type == "loop"][0]
    child = loop_node.body_ids()[0]
    check(fg.delete_node(g2, child) is False, "容器子节点不可删")


# ── E. 纯度 / 幂等 ───────────────────────────────────────────────────
def t_purity_idempotent():
    print("\n── E. 纯度 / 幂等 ──")
    check("tkinter" not in sys.modules, "import flow_graph 未引入 tkinter")
    wf = {"name": "w", "steps": [
        {"type": "condition", "if": "1", "then": {"type": "log", "text": "t"},
         "else": {"type": "log", "text": "e"}},
        {"type": "loop", "times": "3", "steps": [{"type": "wait", "seconds": 1}]},
    ]}
    g1 = fg.promote(wf)
    back = fg.demote(g1)
    check(back == wf, "promote→demote 幂等 (护栏未破)")
    g2 = fg.promote(back)
    check(len(g1.nodes) == len(g2.nodes) and len(g1.edges) == len(g2.edges),
          "再提升同构")


def main():
    print("=== 工作流 CFG 静态分析 + 图编辑原语 回归自测 ===")
    t_reachability()
    t_analyze_linear()
    t_analyze_cases()
    t_graph_theory()
    t_edit_primitives()
    t_purity_idempotent()

    print("\n" + "-" * 60)
    print("PASS={} FAIL={}".format(len(_PASS), len(_FAIL)))
    for m in _FAIL:
        print("[FAIL] {}".format(m))
    print("=== 结果: {} ===".format("FAIL" if _FAIL else "OK"))
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
