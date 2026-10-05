# -*- coding: utf-8 -*-
"""flow_graph — 工作流「树 ⇄ 图」纯逻辑层 (路线图 阶段二 第 9 项 A 期)。

定位
----
执行引擎 (`src/workflow.py`) **保持树形递归不变**; 本模块把工作流定义 (树) 提升为
可编辑的**图模型** (节点 + 边), 供编辑视图 (C 期 `src/ui/flow_canvas.py`) 与后续
静态分析 (D–G 期) 使用。图 → 树 的降级只保证「提升而来的受限子图」可还原, 不追求
把任意 DAG 降级回 loop/condition 语法。

硬约束
------
· **纯逻辑**: 不 import `tkinter` / `workflow` / `engine`, `import flow_graph` 无副作用;
· **幂等契约**:
    demote(promote(x)) == x                          (字段等价, 允许 dict 顺序不同)
    promote(demote(promote(x))) 与 promote(x) 同构   (节点/边/类型/嵌套一致)
· **condition 分支锁定为「单节点」** —— 与 `workflow.py:593 _run_condition` 只执行单个
  branch 的行为一致; then/else 不接受 list。
· **loop 仅映射为「提升而来的受限子图」** —— 范围/回边语义, 不反向推断任意 DAG。
· 降级产物**保持 `steps` 顺序与容器嵌套** (`workflow.py` 用 `enumerate(steps)`, 顺序
  错位会导致执行可视化错位)。

字段口径 (真源 = `src/workflow.py:115 _normalize_step`)
-----------------------------------------------------
    script    {"type":"script","path":...}
    command   {"type":"command","cmd":...,"params":[9 项]}
    variable  {"type":"variable","var_name":...,"var_value":...}
    log       {"type":"log","text":...}
    wait      {"type":"wait","seconds":N}
    parallel  {"type":"parallel","steps":[...]}                 (list)
    loop      {"type":"loop","times":"3 或 ${x} < 5","steps":[...]} (list)
    condition {"type":"condition","if":...,"then":...,"else":...}  (then/else 单节点)
    通用字段  enabled (默认 True) / comment (默认 "")

嵌套承载 (Node.data 的三个保留键, demote 时自动剥离)
--------------------------------------------------
    __fg_children__   parallel / loop 的子节点 id 列表 (顺序 = 原始 steps 顺序)
    __fg_then__       condition 的 then: 原样值 (str 等) 或 {"__ref__": child_id}
    __fg_else__       condition 的 else: 同上

「原样值 vs 子节点」的判定: 非空 dict 视为**单节点子图** → 建子节点 + 出边; 其余
(空串 "" / 空 dict {} / 缺省) 原样保留在 data 里, 保证 `demote` 逐字还原。
"""
import copy

GRAPH_SCHEMA = 2

# 保留键 (仅存于 Node.data)
K_CHILDREN = "__fg_children__"
K_THEN = "__fg_then__"
K_ELSE = "__fg_else__"
REF_KEY = "__ref__"
_RESERVED = (K_CHILDREN, K_THEN, K_ELSE)

CONTAINER_TYPES = ("parallel", "loop")
# (step 键, data 保留键, condition 出边端口名)
_COND_BRANCHES = (("then", K_THEN, "true"), ("else", K_ELSE, "false"))

FALLBACK_TYPE = "script"


def _new_node_id(g):
    return "n{}".format(len(g.nodes))


def _new_edge_id(g):
    return "e{}".format(len(g.edges))


class Node(object):
    """图节点: 一个工作流步骤 (容器/condition 的嵌套经 data 保留键承载)。"""

    __slots__ = ("id", "type", "x", "y", "w", "h", "data", "comment", "enabled")

    def __init__(self, id, type=FALLBACK_TYPE, x=0.0, y=0.0, w=0.0, h=0.0,
                 data=None, comment="", enabled=True):
        self.id = str(id)
        self.type = type or FALLBACK_TYPE
        self.x = float(x or 0.0)
        self.y = float(y or 0.0)
        self.w = float(w or 0.0)
        self.h = float(h or 0.0)
        self.data = dict(data) if data else {}
        self.comment = comment or ""
        self.enabled = enabled

    # —— 结构查询 ——
    def own_fields(self):
        """data 去掉保留键 (深拷贝) —— 降级时的字段真源。"""
        return {k: copy.deepcopy(v) for k, v in self.data.items() if k not in _RESERVED}

    def body_ids(self):
        """容器子节点 id 列表 (parallel / loop)。"""
        return list(self.data.get(K_CHILDREN) or [])

    def branch_ids(self):
        """condition 的各分支子节点 id 列表 (仅含已建子节点的分支)。"""
        out = []
        for _key, rk, _port in _COND_BRANCHES:
            v = self.data.get(rk)
            if isinstance(v, dict) and REF_KEY in v:
                out.append(v[REF_KEY])
        return out

    def child_ids(self):
        """本节点的全部嵌套子节点 id (容器 + condition 分支)。"""
        return self.body_ids() + self.branch_ids()

    # —— 序列化 ——
    def to_dict(self):
        return {"id": self.id, "type": self.type,
                "x": self.x, "y": self.y, "w": self.w, "h": self.h,
                "data": copy.deepcopy(self.data),
                "comment": self.comment, "enabled": self.enabled}

    @classmethod
    def from_dict(cls, d):
        d = d or {}
        return cls(id=d.get("id", ""), type=d.get("type", FALLBACK_TYPE),
                   x=d.get("x", 0.0), y=d.get("y", 0.0),
                   w=d.get("w", 0.0), h=d.get("h", 0.0),
                   data=d.get("data") or {}, comment=d.get("comment", ""),
                   enabled=d.get("enabled", True))

    def __repr__(self):
        return "Node({!r}, {!r})".format(self.id, self.type)


class Edge(object):
    """图边: src_port ∈ {out, body, true, false, back}。back_edge=True 表示回边。"""

    __slots__ = ("id", "src", "src_port", "dst", "dst_port", "label", "back_edge")

    def __init__(self, id="", src="", src_port="out", dst="", dst_port="in",
                 label="", back_edge=False):
        self.id = str(id)
        self.src = str(src)
        self.src_port = src_port or "out"
        self.dst = str(dst)
        self.dst_port = dst_port or "in"
        self.label = label or ""
        self.back_edge = bool(back_edge)

    def to_dict(self):
        return {"id": self.id, "src": self.src, "src_port": self.src_port,
                "dst": self.dst, "dst_port": self.dst_port,
                "label": self.label, "back_edge": self.back_edge}

    @classmethod
    def from_dict(cls, d):
        d = d or {}
        return cls(id=d.get("id", ""), src=d.get("src", ""),
                   src_port=d.get("src_port", "out"), dst=d.get("dst", ""),
                   dst_port=d.get("dst_port", "in"), label=d.get("label", ""),
                   back_edge=d.get("back_edge", False))

    def __repr__(self):
        return "Edge({!r}->{!r} via {!r})".format(self.src, self.dst, self.src_port)


class Graph(object):
    """工作流图: 节点 + 边 + schema。`schema` 为版本号 (GRAPH_SCHEMA)。"""

    def __init__(self, name=None, nodes=None, edges=None, schema=GRAPH_SCHEMA):
        self.name = name
        self.nodes = list(nodes or [])
        self.edges = list(edges or [])
        self.schema = schema

    # —— 构建 ——
    def add_node(self, node):
        self.nodes.append(node)
        return node

    def add_edge(self, edge):
        self.edges.append(edge)
        return edge

    # —— 查询 ——
    def node(self, nid):
        for n in self.nodes:
            if n.id == nid:
                return n
        return None

    def child_ids(self):
        s = set()
        for n in self.nodes:
            s.update(n.child_ids())
        return s

    def roots(self):
        """顶层节点 (未被任何容器/分支容纳), 顺序 = 原始 steps 顺序。"""
        child = self.child_ids()
        return [n for n in self.nodes if n.id not in child]

    def back_edges(self):
        return [e for e in self.edges if e.back_edge]

    # —— 序列化 (graph 视图: 写入坐标 + 边) ——
    def to_dict(self):
        return {"schema": GRAPH_SCHEMA, "name": self.name,
                "graph": {"nodes": [n.to_dict() for n in self.nodes],
                          "edges": [e.to_dict() for e in self.edges]}}

    @classmethod
    def from_dict(cls, d):
        d = d or {}
        g = cls(name=d.get("name"), schema=d.get("schema", GRAPH_SCHEMA))
        g.nodes = [Node.from_dict(n) for n in (d.get("nodes") or [])]
        g.edges = [Edge.from_dict(e) for e in (d.get("edges") or [])]
        return g

    def __repr__(self):
        return "Graph(name={!r}, nodes={}, edges={})".format(
            self.name, len(self.nodes), len(self.edges))


# ══════════════════════════════════════════════════════════════════════
# 树 → 图 (promote)
# ══════════════════════════════════════════════════════════════════════

def _is_branch_subgraph(v):
    """非空 dict → 视为单节点子图 (与 _run_condition 的单节点语义一致)。"""
    return isinstance(v, dict) and bool(v)


def _promote_step(g, step):
    """把一个步骤 (含其嵌套) 提升为节点, 返回节点 id。"""
    if isinstance(step, str):
        step = {"type": FALLBACK_TYPE, "path": step}
    elif not isinstance(step, dict):
        step = {"type": FALLBACK_TYPE, "path": str(step)}
    step = copy.deepcopy(step)
    typ = step.get("type") or FALLBACK_TYPE

    nid = _new_node_id(g)
    node = Node(id=nid, type=typ,
                comment=step.get("comment", "") or "",
                enabled=step.get("enabled", True))
    own = {}
    for k, v in step.items():
        if typ in CONTAINER_TYPES and k == "steps":
            continue                       # 子步骤 → 子节点 (见下)
        if typ == "condition" and k in ("then", "else"):
            continue                       # 分支统一由下方处理 (子图 / 原样值)
        own[k] = copy.deepcopy(v)
    node.data = own
    g.add_node(node)

    # ── 容器: parallel / loop ──
    if typ in CONTAINER_TYPES:
        if "steps" in step:
            kids = _promote_steps(g, step.get("steps") or [])
            own[K_CHILDREN] = kids
            if kids:
                g.add_edge(Edge(_new_edge_id(g), src=nid, src_port="body",
                                dst=kids[0], dst_port="in"))
            if typ == "loop":
                head = kids[0] if kids else nid
                tail = kids[-1] if kids else nid
                g.add_edge(Edge(_new_edge_id(g), src=tail, src_port="back",
                                dst=head, dst_port="in",
                                label="loop", back_edge=True))
        elif typ == "loop":
            # loop 无 steps: 自身回边 (保证 loop 必有 back_edge=True)
            g.add_edge(Edge(_new_edge_id(g), src=nid, src_port="back",
                            dst=nid, dst_port="in",
                            label="loop", back_edge=True))

    # ── 条件: 菱形节点 + true/false 双出口 ──
    elif typ == "condition":
        for key, rk, port in _COND_BRANCHES:
            if key not in step:
                continue
            val = step.get(key)
            if _is_branch_subgraph(val):
                cid = _promote_step(g, val)
                own[rk] = {REF_KEY: cid}
                g.add_edge(Edge(_new_edge_id(g), src=nid, src_port=port,
                                dst=cid, dst_port="in", label=port))
            else:
                own[rk] = copy.deepcopy(val)   # "" / {} / 其他 → 原样保留

    return nid


def _promote_steps(g, steps):
    """同级步骤: 逐节点提升, 并在相邻者之间连顺序边。

    condition 的「汇合边」: 从各分支子节点的出口连向下一个同级节点 (跳过菱形本身),
    避免出现「菱形 → 下一节点」这条错误的直连。
    """
    ids = [_promote_step(g, s) for s in (steps or [])]
    for i in range(len(ids) - 1):
        a, b = ids[i], ids[i + 1]
        na = g.node(a)
        branches = na.branch_ids() if (na is not None and na.type == "condition") else []
        if branches:
            for cid in branches:
                g.add_edge(Edge(_new_edge_id(g), src=cid, src_port="out",
                                dst=b, dst_port="in"))
        else:
            g.add_edge(Edge(_new_edge_id(g), src=a, src_port="out",
                            dst=b, dst_port="in"))
    return ids


def promote(workflow_json):
    """树 (工作流 dict) → 图。输入不被修改 (内部深拷贝)。

    name 缺失时 Graph.name 为 None, 以便 demote 时不凭空补出 "name" 键。
    """
    wf = workflow_json if isinstance(workflow_json, dict) else {}
    g = Graph(name=(wf.get("name") if "name" in wf else None), schema=GRAPH_SCHEMA)
    _promote_steps(g, wf.get("steps") or [])
    return g


# ══════════════════════════════════════════════════════════════════════
# 图 → 树 (demote)
# ══════════════════════════════════════════════════════════════════════

def _demote_node(g, nid):
    """把一个节点 (含其嵌套) 降级回步骤 dict。"""
    node = g.node(nid)
    if node is None:
        return {}
    step = node.own_fields()
    typ = node.type
    if typ in CONTAINER_TYPES:
        if K_CHILDREN in node.data:
            step["steps"] = [_demote_node(g, c) for c in node.body_ids()]
    elif typ == "condition":
        for key, rk, _port in _COND_BRANCHES:
            if rk not in node.data:
                continue
            val = node.data.get(rk)
            if isinstance(val, dict) and REF_KEY in val:
                step[key] = _demote_node(g, val[REF_KEY])
            else:
                step[key] = copy.deepcopy(val)
    return step


def demote(g):
    """图 → 工作流 dict, 剥离坐标与边 (仅保留 name + steps 结构)。

    name 为 None (原输入无 "name" 键) 时不写 "name"。
    """
    out = {}
    if g.name is not None:
        out["name"] = g.name
    out["steps"] = [_demote_node(g, n.id) for n in g.roots()]
    return out


# ══════════════════════════════════════════════════════════════════════
# 序列化 (graph 视图)
# ══════════════════════════════════════════════════════════════════════

def to_dict(g):
    """Graph → {"schema":2,"name":...,"graph":{"nodes":[...],"edges":[...]}}。"""
    return g.to_dict()


def from_dict(d):
    """dict → Graph。

    新格式 (含 "graph") 直接还原; 旧格式 ({name, steps} 或裸 {nodes, edges})
    兼容: {name,steps} 走 promote(), 保证无 schema 的旧 JSON 也能读。
    """
    d = d if isinstance(d, dict) else {}
    if isinstance(d.get("graph"), dict):
        g = Graph(name=d.get("name"), schema=d.get("schema", GRAPH_SCHEMA))
        g.nodes = [Node.from_dict(n) for n in (d["graph"].get("nodes") or [])]
        g.edges = [Edge.from_dict(e) for e in (d["graph"].get("edges") or [])]
        return g
    if "nodes" in d and "edges" in d:
        return Graph.from_dict(d)
    return promote(d)
