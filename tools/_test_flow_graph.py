#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""工作流 CFG 化「树 ⇄ 图」纯逻辑回归 (路线图 阶段二 第 9 项 A/B 期: src/flow_graph.py)。

用法:
    .venv\\Scripts\\python.exe -X utf8 tools\\_test_flow_graph.py

覆盖 (纯逻辑, 不建窗):
  ① 往返幂等      : demote(promote(x)) == x (字段等价, 允许 dict 顺序不同)
  ② 结构同构      : promote(demote(promote(x))) 与 promote(x) 同构 (to_dict 等价)
  ③ 序列化往返    : from_dict(to_dict(promote(x))).to_dict() == to_dict(promote(x))
  ④ 旧 JSON 兼容  : from_dict({name,steps}) (无 schema) == promote(x)
  ⑤ condition     : true/false 双出口; 分支锁定「单节点」; then/else 空串不建子节点
  ⑥ loop          : 存在 back_edge=True; 空容器不崩
  ⑦ 空输入        : 空 steps / 空容器 / 无 name 不崩
  ⑧ 纯逻辑守卫    : 不 import tkinter / workflow / engine; 输入不被就地修改

样例:
  · template/workflow001.json (含空 parallel/loop + command/variable/script);
  · 手工构造: condition(then/else 单节点 / 空串 / 仅 then)、loop(times 固定 / 条件表达式 +
    子 steps)、parallel(多子步骤)、enabled=False、comment、嵌套 loop(parallel(...))。

退出码: 0 = 全部通过 / 1 = 有失败。
"""
import copy
import io
import json
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


import flow_graph as fg  # noqa: E402


# ── 样例 ──────────────────────────────────────────────────────────────

def sample_template():
    with io.open(os.path.join(ROOT, "template", "workflow001.json"),
                 "r", encoding="utf-8") as f:
        return json.load(f)


def sample_rich():
    return {
        "name": "rich",
        "steps": [
            # 1) condition: then/else 均为非空单节点
            {"type": "condition", "if": "${x} > 1",
             "then": {"type": "script", "path": "a.xls"},
             "else": {"type": "wait", "seconds": 2, "enabled": False, "comment": "w"}},
            # 2) condition: then/else 为空字符串
            {"type": "condition", "if": "${y}", "then": "", "else": ""},
            # 3) condition: 仅 then (无 else 键)
            {"type": "condition", "if": "${z}", "then": {"type": "log", "text": "hi"}},
            # 4) 嵌套 loop(parallel(...)) + 禁用 command + comment
            {"type": "loop", "times": "3", "steps": [
                {"type": "parallel", "steps": [
                    {"type": "log", "text": "a"},
                    {"type": "variable", "var_name": "v", "var_value": "1"},
                ]},
                {"type": "command", "cmd": "点击",
                 "params": ["1", "2", "3", "4", "5", "6", "7", "8", "9"],
                 "enabled": False, "comment": "c"},
            ]},
            # 5) loop: 条件表达式 times + 空子 steps
            {"type": "loop", "times": "${i} < 5", "steps": []},
            {"type": "wait", "seconds": 1},
            {"type": "script", "path": "b.xls"},
        ],
    }


SAMPLES = [
    ("template/workflow001.json", sample_template),
    ("handcrafted rich", sample_rich),
]


def _no_reserved_leak(obj):
    """递归检查降级产物里不残留 __fg_* 保留键。"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(k, str) and k.startswith("__fg_"):
                return False
            if not _no_reserved_leak(v):
                return False
    elif isinstance(obj, list):
        for it in obj:
            if not _no_reserved_leak(it):
                return False
    return True


def _cond_nodes(g):
    return [n for n in g.nodes if n.type == "condition"]


def _loop_nodes(g):
    return [n for n in g.nodes if n.type == "loop"]


# ── ①② 幂等 + 同构 + ⑧ 不改输入 ───────────────────────────────────────

def t_roundtrip_and_isomorphism():
    print("\n── ① 往返幂等 / ② 结构同构 / ⑧ 不改输入 ──")
    for label, maker in SAMPLES:
        x = maker()
        before = copy.deepcopy(x)
        g = fg.promote(x)
        back = fg.demote(g)
        check(back == x, "[{}] demote(promote(x)) == x".format(label))
        check(x == before, "[{}] promote 未就地修改输入".format(label))
        check(_no_reserved_leak(back), "[{}] 降级产物无 __fg_* 保留键残留".format(label))
        g2 = fg.promote(back)
        check(fg.to_dict(g2) == fg.to_dict(g),
              "[{}] promote(demote(promote(x))) 与 promote(x) 同构".format(label),
              "nodes={} edges={}".format(len(g2.nodes), len(g2.edges)))


# ── ③ 序列化往返 ──────────────────────────────────────────────────────

def t_serialization_roundtrip():
    print("\n── ③ to_dict / from_dict 往返 ──")
    check(fg.GRAPH_SCHEMA == 2, "GRAPH_SCHEMA == 2")
    for label, maker in SAMPLES:
        g = fg.promote(maker())
        d = fg.to_dict(g)
        check(d.get("schema") == 2 and isinstance(d.get("graph"), dict),
              "[{}] to_dict 结构 = {{schema, name, graph{{nodes, edges}}}}".format(label),
              "keys={}".format(sorted(d.keys())))
        g2 = fg.from_dict(d)
        check(fg.to_dict(g2) == d, "[{}] from_dict(to_dict(g)) 等价".format(label))
        check(g.schema == 2, "[{}] Graph.schema == 2".format(label))


# ── ④ 旧 JSON 兼容 ────────────────────────────────────────────────────

def t_legacy_json():
    print("\n── ④ from_dict 兼容无 schema 旧 JSON ──")
    for label, maker in SAMPLES:
        x = maker()
        g_legacy = fg.from_dict(x)          # 旧格式 {name, steps}
        check(fg.to_dict(g_legacy) == fg.to_dict(fg.promote(x)),
              "[{}] from_dict(旧 {{name,steps}}) == promote(x)".format(label))
    # 裸 {nodes, edges}
    bare = fg.to_dict(fg.promote(sample_rich()))["graph"]
    g_bare = fg.from_dict({"name": "b", "nodes": bare["nodes"], "edges": bare["edges"]})
    check(len(g_bare.nodes) == len(bare["nodes"]), "from_dict 兼容裸 {nodes, edges}")


# ── ⑤ condition ───────────────────────────────────────────────────────

def t_condition():
    print("\n── ⑤ condition 双出口 / 单节点锁定 ──")
    g = fg.promote(sample_rich())
    conds = _cond_nodes(g)
    check(len(conds) == 3, "rich 样例含 3 个 condition 节点", "got={}".format(len(conds)))
    by_if = {}
    for n in conds:
        by_if[n.data.get("if")] = n

    # (1) then/else 非空单节点 → true/false 各 1 条出边
    n1 = by_if.get("${x} > 1")
    check(n1 is not None, "condition('${x} > 1') 已提升")
    if n1 is not None:
        outs = [e for e in g.edges if e.src == n1.id]
        ports = sorted(e.src_port for e in outs)
        check(ports == ["false", "true"],
              "非空 then/else → true/false 双出口", "ports={}".format(ports))
        labels = sorted(e.label for e in outs)
        check(labels == ["false", "true"], "双出口 label = true/false", "labels={}".format(labels))
        check(len(n1.branch_ids()) == 2, "then/else 各映射为单节点子图")
        # 分支子节点自身不含嵌套 (锁定单节点)
        for cid in n1.branch_ids():
            cn = g.node(cid)
            check(cn is not None and cn.child_ids() == [],
                  "condition 分支锁定为单节点 (无更深嵌套)", "child={}".format(cid))

    # (2) then/else 为空字符串 → 不建子节点, 也无比出边
    n2 = by_if.get("${y}")
    if n2 is not None:
        outs2 = [e for e in g.edges if e.src == n2.id]
        branch_outs = [e for e in outs2 if e.src_port in ("true", "false")]
        check(n2.branch_ids() == [], "then/else='' 不建分支子节点")
        check(branch_outs == [], "then/else='' 无 true/false 出边",
              "branch_outs={} total_outs={}".format(len(branch_outs), len(outs2)))
        check(sorted(e.src_port for e in outs2) == ["out"],
              "then/else='' 仅保留「继续流」顺序出边 (out)", "ports={}".format(
                  sorted(e.src_port for e in outs2)))
        check(n2.data.get(fg.K_THEN) == "" and n2.data.get(fg.K_ELSE) == "",
              "then/else='' 原样保留在 data 保留键")

    # (3) 仅 then → 只有 true 出边
    n3 = by_if.get("${z}")
    if n3 is not None:
        outs3 = sorted(e.src_port for e in g.edges if e.src == n3.id)
        check(outs3 == ["true"], "仅 then → 只有 true 出边", "ports={}".format(outs3))
        check(fg.K_ELSE not in n3.data, "无 else 键时不凭空补出 else")

    # 汇合边: 分支子节点连向下一个同级节点
    if n1 is not None:
        ends = set(n1.branch_ids())
        m = [e for e in g.edges if e.src in ends and e.src_port == "out"]
        check(len(m) == 2, "两分支各有 1 条汇合边 (指向同级后继)", "merge={}".format(len(m)))


# ── ⑥ loop ────────────────────────────────────────────────────────────

def t_loop():
    print("\n── ⑥ loop 范围 / 回边 ──")
    for label, maker in SAMPLES:
        g = fg.promote(maker())
        loops = _loop_nodes(g)
        backs = g.back_edges()
        check(len(loops) >= 1, "[{}] 含 loop 节点".format(label))
        check(len(backs) >= len(loops),
              "[{}] 每个 loop 至少 1 条 back_edge=True".format(label),
              "loops={} back_edges={}".format(len(loops), len(backs)))
        check(all(e.back_edge is True for e in backs), "[{}] back_edge 标志为 True".format(label))
    # 空 loop (workflow001) → 自回边
    g = fg.promote(sample_template())
    loops = _loop_nodes(g)
    selfloops = [e for e in g.back_edges() if e.src in [n.id for n in loops]
                 and e.dst == e.src]
    check(len(selfloops) >= 1, "空 steps 的 loop → 自身回边", "self={}".format(len(selfloops)))
    check(all(n.body_ids() == [] for n in loops), "workflow001 的 loop body 为空且不崩")
    # 嵌套 loop(parallel(...)): body 顺序 = 原始 steps 顺序
    g2 = fg.promote(sample_rich())
    outer = [n for n in _loop_nodes(g2) if n.data.get("times") == "3"]
    check(len(outer) == 1, "rich 含 times='3' 的 loop")
    if outer:
        kids = outer[0].body_ids()
        check(len(kids) == 2, "loop body 保留 2 个子节点 (顺序)", "kids={}".format(len(kids)))
        par = g2.node(kids[0])
        check(par is not None and par.type == "parallel", "loop 首子节点是 parallel (嵌套顺序不变)")
        check(par is not None and len(par.body_ids()) == 2, "嵌套 parallel 保留 2 个子步骤")


# ── ⑦ 空输入 ──────────────────────────────────────────────────────────

def t_empty():
    print("\n── ⑦ 空 steps / 空容器 / 无 name 不崩 ──")
    x = {"name": "e", "steps": []}
    g = fg.promote(x)
    check(g.nodes == [] and g.edges == [], "空 steps → 空图")
    check(fg.demote(g) == x, "空 steps 往返等价")
    check(fg.demote(fg.promote({})) == {"steps": []}, "空 dict → {'steps': []} (无 name 键)")
    check(fg.demote(fg.promote({"steps": []})) == {"steps": []}, "无 name 的旧 JSON 往返 (不补 name)")
    for bad in (None, [], "x", {"type": "parallel"}, {"type": "loop"}):
        try:
            gg = fg.promote(bad) if not isinstance(bad, dict) else fg.promote(bad)
            fg.demote(gg)
            ok = True
        except Exception as e:
            ok = False
            print("      bad={!r} → {!r}".format(bad, e))
        check(ok, "畸形输入 {!r} 不崩".format(bad))


# ── ⑧ 纯逻辑守卫 (静态) ───────────────────────────────────────────────

def t_pure_logic():
    print("\n── ⑧ 纯逻辑守卫 (无 tkinter / workflow / engine import) ──")
    src = _read("src/flow_graph.py")
    check("import tkinter" not in src and "from tkinter" not in src,
          "flow_graph 不 import tkinter (文档串提及不算)")
    check("import workflow" not in src and "from workflow" not in src,
          "flow_graph 不 import workflow")
    check("import engine" not in src and "from engine" not in src,
          "flow_graph 不 import engine")
    check("import flow_graph" in sys.modules or "flow_graph" in sys.modules,
          "flow_graph 已成功 import")
    check("tkinter" not in sys.modules, "本测试进程未加载 tkinter (纯逻辑)")


def main():
    print("=== flow_graph 树⇄图 纯逻辑回归 (路线图 阶段二 第 9 项 A/B 期) ===")
    print("解释器: {}".format(sys.executable))
    t_roundtrip_and_isomorphism()
    t_serialization_roundtrip()
    t_legacy_json()
    t_condition()
    t_loop()
    t_empty()
    t_pure_logic()
    print("\n=== 结果: {} 通过 / {} 失败 ===".format(len(_PASS), len(_FAIL)))
    if _FAIL:
        for m in _FAIL:
            print("  [FAIL] {}".format(m))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
