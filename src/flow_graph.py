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


# ══════════════════════════════════════════════════════════════════════
# 静态分析 (路线图 §11.8, D–G 期 E 期) —— 全为纯函数: 输入 Graph, 输出数据
# ══════════════════════════════════════════════════════════════════════
#
# 分析建立在 promote() 的「树 → 图」表示之上 (loop 头部与循环体是两个 SCC,
# 出口边从 loop 头出发) —— 因此**不做**教科书式「循环无出口」判定 (会对合法
# 固定次数循环误报), 改为围绕 **loop 节点 / 条件分支 / 主包可达性** 的口径。

MAX_PATHS = 64          # 入口→出口简单路径数阈值 (超过提示拆分)
COMPLEXITY_WARN = 15    # 环路复杂度阈值

# 会修改变量的节点口径 (用于「条件循环体内是否推进」判定)
_VAR_CMDS = ("设置变量", "数学运算", "Python", "代码", "工作流变量")


def adjacency(g):
    """→ (fwd, rev, indeg, outdeg): 有向邻接与度 (含回边/自环; 仅计图中存在的节点)。"""
    idset = {n.id for n in g.nodes}
    fwd = {i: [] for i in idset}
    rev = {i: [] for i in idset}
    for e in g.edges:
        if e.src in idset and e.dst in idset:
            fwd[e.src].append(e.dst)
            rev[e.dst].append(e.src)
    indeg = {i: len(rev[i]) for i in idset}
    outdeg = {i: len(fwd[i]) for i in idset}
    return fwd, rev, indeg, outdeg


def entry_ids(g):
    """入口节点: 无入边者 (含孤立节点)。"""
    _f, _r, indeg, _o = adjacency(g)
    return [n.id for n in g.nodes if indeg[n.id] == 0]


def exit_ids(g):
    """出口(汇)节点: 无出边者。"""
    _f, _r, _i, outdeg = adjacency(g)
    return [n.id for n in g.nodes if outdeg[n.id] == 0]


def reachable_ids(g):
    """从入口集合 BFS 可达的节点 id 集合。"""
    fwd, _r, indeg, _o = adjacency(g)
    seen = set()
    stack = [n.id for n in g.nodes if indeg[n.id] == 0]
    while stack:
        u = stack.pop()
        if u in seen:
            continue
        seen.add(u)
        stack.extend(v for v in fwd[u] if v not in seen)
    return seen


def can_reach_exit(g):
    """能(反向 BFS)到达任一出口的节点 id 集合。"""
    _f, rev, _i, outdeg = adjacency(g)
    sinks = {n.id for n in g.nodes if outdeg[n.id] == 0}
    seen = set()
    stack = list(sinks)
    while stack:
        u = stack.pop()
        if u in seen:
            continue
        seen.add(u)
        stack.extend(v for v in rev[u] if v not in seen)
    return seen


def unreachable_ids(g):
    """从入口不可达的节点 (排除孤立节点, 后者单列)。"""
    seen = reachable_ids(g)
    iso = set(isolated_ids(g))
    return [n.id for n in g.nodes if n.id not in seen and n.id not in iso]


def isolated_ids(g):
    """孤立节点: 无入边且无出边。"""
    _f, _r, indeg, outdeg = adjacency(g)
    return [n.id for n in g.nodes if indeg[n.id] == 0 and outdeg[n.id] == 0]


def dead_branch_ids(g):
    """可达但无法到达任何出口的节点 (之后没有出口)。"""
    to_exit = can_reach_exit(g)
    return [n.id for n in g.nodes if n.id not in to_exit]


def sccs(g):
    """Tarjan (迭代实现, 防深递归) → 强连通分量列表 (每项为节点 id 列表)。"""
    fwd, _r, _i, _o = adjacency(g)
    index, low, on = {}, {}, set()
    stack, result, counter = [], [], [0]
    for root in [n.id for n in g.nodes]:
        if root in index:
            continue
        work = [(root, 0)]
        while work:
            v, pi = work[-1]
            if pi == 0:
                index[v] = low[v] = counter[0]
                counter[0] += 1
                stack.append(v)
                on.add(v)
            recurse = False
            succ = fwd.get(v, [])
            i = pi
            while i < len(succ):
                w = succ[i]
                if w not in index:
                    work[-1] = (v, i + 1)
                    work.append((w, 0))
                    recurse = True
                    break
                if w in on:
                    low[v] = min(low[v], index[w])
                i += 1
            if recurse:
                continue
            if low[v] == index[v]:
                comp = []
                while True:
                    w = stack.pop()
                    on.discard(w)
                    comp.append(w)
                    if w == v:
                        break
                result.append(comp)
            work.pop()
            if work:
                u = work[-1][0]
                low[u] = min(low[u], low[v])
    return result


def count_paths(g, limit=MAX_PATHS):
    """入口→出口的简单路径数 (DFS 去环; 超过 limit 提前返回 limit+1)。"""
    fwd, _r, indeg, _o = adjacency(g)
    starts = [n.id for n in g.nodes if indeg[n.id] == 0]
    if not starts:
        return 0
    total = [0]

    def dfs(u, seen):
        if total[0] > limit:
            return
        nxt = fwd.get(u) or []
        if not nxt:
            total[0] += 1
            return
        for v in nxt:
            if v in seen:
                continue
            dfs(v, seen | {v})

    for s in starts:
        dfs(s, {s})
        if total[0] > limit:
            return limit + 1
    return total[0]


def _components(g):
    """无向连通分量数 (用于环路复杂度 P)。"""
    idset = {n.id for n in g.nodes}
    adj = {i: set() for i in idset}
    for e in g.edges:
        if e.src in idset and e.dst in idset:
            adj[e.src].add(e.dst)
            adj[e.dst].add(e.src)
    seen, comp = set(), 0
    for i in idset:
        if i in seen:
            continue
        comp += 1
        stack = [i]
        while stack:
            u = stack.pop()
            if u in seen:
                continue
            seen.add(u)
            stack.extend(adj[u] - seen)
    return comp


def cyclomatic_complexity(g):
    """环路复杂度 E − N + 2P (P=无向连通分量数, 空图为 0)。"""
    n, e = len(g.nodes), len(g.edges)
    if n == 0:
        return 0
    return e - n + 2 * max(1, _components(g))


def _node_modifies_vars(node):
    if node is None:
        return False
    if node.type == "variable":
        return True
    if node.type == "command" and node.data.get("cmd") in _VAR_CMDS:
        return True
    return False


def loop_issues(g):
    """循环相关检查: 次数为空 / 条件循环体内未修改变量。返回 [(nid, message)]。"""
    out = []
    for n in g.nodes:
        if n.type != "loop":
            continue
        t = str(n.data.get("times", "") or "").strip()
        if not t:
            out.append((n.id, "循环次数为空（可能不执行或死循环）"))
        elif not t.isdigit():
            body = n.body_ids()
            if not any(_node_modifies_vars(g.node(b)) for b in body):
                out.append((n.id, "条件循环体内未修改变量（可能死循环）"))
    return out


def duplicate_edge_ids(g):
    """重复边 (src, src_port, dst, dst_port 相同) 的边 id 列表 (保留首条之外者)。"""
    seen, dup = set(), []
    for e in g.edges:
        key = (e.src, e.src_port, e.dst, e.dst_port)
        if key in seen:
            dup.append(e.id)
        else:
            seen.add(key)
    return dup


def analyze(g):
    """→ 问题列表 ``[{code, level, nodes, message}]``; level ∈ error/warning/info。

    覆盖 (≥8): 不可达 / 孤立 / 悬挂出口 / 缺少终止节点 / 路径爆炸 / 环路复杂度 /
    循环次数 / 无出口分支 / 重复边。全为纯函数, 易测 (§11.11 E 期)。
    """
    issues = []
    if not g.nodes:
        return issues

    unre = unreachable_ids(g)
    if unre:
        issues.append({"code": "unreachable", "level": "error", "nodes": unre,
                       "message": "{} 个步骤不可达（从入口无法到达）".format(len(unre))})

    iso = isolated_ids(g)
    if iso:
        issues.append({"code": "isolated", "level": "warning", "nodes": iso,
                       "message": "{} 个孤立步骤（无入边也无出边）".format(len(iso))})

    # 悬挂出口: condition 存在某分支却无对应端口出边
    dangling = set()
    for n in g.nodes:
        if n.type != "condition":
            continue
        ports = {e.src_port for e in g.edges if e.src == n.id}
        if (K_THEN in n.data) and ("true" not in ports):
            dangling.add(n.id)
        if (K_ELSE in n.data) and ("false" not in ports):
            dangling.add(n.id)
    if dangling:
        issues.append({"code": "dangling_exit", "level": "warning",
                       "nodes": sorted(dangling), "message": "条件分支缺少出口边"})

    dead = dead_branch_ids(g)
    if dead:
        issues.append({"code": "dead_branch", "level": "warning", "nodes": dead,
                       "message": "{} 个步骤之后没有出口".format(len(dead))})

    for nid, msg in loop_issues(g):
        issues.append({"code": "loop", "level": "warning", "nodes": [nid],
                       "message": msg})

    dup = duplicate_edge_ids(g)
    if dup:
        issues.append({"code": "duplicate_edge", "level": "info", "nodes": [],
                       "message": "{} 条重复连线".format(len(dup))})

    if not [n for n in g.nodes if not (adjacency(g)[0].get(n.id))]:
        issues.append({"code": "missing_exit", "level": "info", "nodes": [],
                       "message": "没有终止节点（所有节点都有后继）"})

    if count_paths(g) > MAX_PATHS:
        issues.append({"code": "path_explosion", "level": "warning", "nodes": [],
                       "message": "入口→出口路径数 > {}，建议拆分".format(MAX_PATHS)})

    cx = cyclomatic_complexity(g)
    if cx > COMPLEXITY_WARN:
        issues.append({"code": "complexity", "level": "info", "nodes": [],
                       "message": "环路复杂度 {} > {}".format(cx, COMPLEXITY_WARN)})

    return issues


def issue_summary(issues):
    """→ (error, warning, info) 计数三元组。"""
    e = sum(1 for i in issues if i.get("level") == "error")
    w = sum(1 for i in issues if i.get("level") == "warning")
    n = sum(1 for i in issues if i.get("level") == "info")
    return e, w, n


# ══════════════════════════════════════════════════════════════════════
# 图编辑原语 (§11.7 连线交互的纯逻辑层)
# ══════════════════════════════════════════════════════════════════════

def _reaches(g, a, b, max_hops=None):
    """a 能否沿出边到达 b (含 a==b 时按有向可达, 不含零跳)。"""
    fwd, _r, _i, _o = adjacency(g)
    seen = set()
    stack = [a]
    hops = 0
    while stack:
        if max_hops is not None and hops > max_hops:
            return False
        u = stack.pop()
        for v in fwd.get(u, []):
            if v == b:
                return True
            if v not in seen:
                seen.add(v)
                stack.append(v)
        hops += 1
    return False


def can_connect(g, src, dst, src_port="out", dst_port="in"):
    """→ (ok, reason)。拒绝: 节点不存在 / 自环 / 重复边。成环(回边)允许。"""
    if g.node(src) is None or g.node(dst) is None:
        return False, "节点不存在"
    if src == dst:
        return False, "不能连接到自身"
    for e in g.edges:
        if (e.src, e.src_port, e.dst, e.dst_port) == (src, src_port, dst, dst_port):
            return False, "该连线已存在"
    return True, ""


def connect(g, src, dst, src_port="out", dst_port="in", label=""):
    """建边 (重复/非法返回 None); 若新边成环则标记 back_edge。返回新 Edge。"""
    ok, _reason = can_connect(g, src, dst, src_port, dst_port)
    if not ok:
        return None
    back = _reaches(g, dst, src)
    e = Edge(_new_edge_id(g), src=src, src_port=src_port,
             dst=dst, dst_port=dst_port, label=label, back_edge=back)
    g.add_edge(e)
    return e


def disconnect(g, edge_id):
    """按 id 删除边; 返回被删 Edge 或 None。"""
    for i, e in enumerate(g.edges):
        if e.id == edge_id:
            return g.edges.pop(i)
    return None


def delete_node(g, nid):
    """删除顶层节点并把前驱 → 后继直连 (§11.7 Delete)。

    容器/条件分支的子节点不可删 (返回 False)。返回 True 表示已删除。
    """
    node = g.node(nid)
    if node is None or nid in g.child_ids():
        return False
    preds = [e.src for e in g.edges if e.dst == nid and e.src != nid]
    succs = [e.dst for e in g.edges if e.src == nid and e.dst != nid]
    g.edges = [e for e in g.edges if e.src != nid and e.dst != nid]
    g.nodes = [n for n in g.nodes if n.id != nid]
    for p in preds:
        for s in succs:
            if g.node(p) is not None and g.node(s) is not None:
                connect(g, p, s, "out", "in")
    return True


def to_graph(g):
    """图视图序列化 (含坐标) —— 供会话内缓存/回放。"""
    return g.to_dict()["graph"]
