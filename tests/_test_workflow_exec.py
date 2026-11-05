#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""工作流新步骤语义守卫 —— retry / rollback / 子工作流引用 / 循环引用防护。

用法:
    .venv\\Scripts\\python.exe -X utf8 tests\\_test_workflow_exec.py

覆盖:
  1. _normalize_step  : retry / workflow / on_error_rollback 的默认值与规范化。
  2. retry            : 子步骤失败重试, 超限抛 WorkflowError。
  3. rollback         : 任一步骤失败时执行 on_error_rollback 后再向上抛错。
  4. workflow (子引用): 目标解析 / 循环引用检测 / 深度上限。
  5. flow_graph       : retry 视为容器, workflow 视为叶节点 (不误报/不崩溃)。

退出码: 0=全部通过 / 1=有失败。
"""
import os
import sys
import tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import workflow  # noqa: E402
import flow_graph  # noqa: E402

_PASS, _FAIL = [], []


def check(cond, msg, detail=""):
    (_PASS if cond else _FAIL).append(msg)
    tail = ("  ({})".format(detail) if detail else "")
    print("[{}] {}{}".format("OK  " if cond else "FAIL", msg, tail))


# ── 1. _normalize_step ───────────────────────────────────────────────
n_retry = workflow._normalize_step({"type": "retry", "steps": [{"type": "log", "text": "x"}]})
check(n_retry.get("max_attempts") == 3, "retry 默认 max_attempts=3", n_retry.get("max_attempts"))
check(n_retry.get("delay") == 1.0, "retry 默认 delay=1.0", n_retry.get("delay"))
check(len(n_retry.get("steps", [])) == 1 and n_retry["steps"][0]["type"] == "log",
      "retry 子步骤被递归规范化")

n_wf = workflow._normalize_step({"type": "workflow", "path": "sub.json"})
check(n_wf.get("inputs") == {} and n_wf.get("outputs") == [],
      "workflow 默认 inputs={} / outputs=[]")

n_scr = workflow._normalize_step(
    {"type": "script", "path": "a.xls",
     "on_error_rollback": [{"type": "log", "text": "undo"}]})
check(len(n_scr.get("on_error_rollback", [])) == 1
      and n_scr["on_error_rollback"][0]["type"] == "log",
      "on_error_rollback 子步骤被规范化")

n_par = workflow._normalize_step({"type": "parallel", "steps": []})
check("outputs" in n_par, "parallel 规范化后带 outputs 字段 (默认 None 向后兼容)")


# ── 2. retry 重试语义 ────────────────────────────────────────────────
eng = workflow.WorkflowEngine()
failing_retry = {"type": "retry", "max_attempts": 3, "delay": 0,
                 "steps": [{"type": "command", "cmd": "__no_such_cmd__", "params": []}]}
raised = False
try:
    eng._run_retry(failing_retry, ".")
except workflow.WorkflowError:
    raised = True
check(raised, "retry 子步骤持续失败, 超过 max_attempts 抛 WorkflowError")


# ── 3. rollback 失败补偿 ──────────────────────────────────────────────
eng2 = workflow.WorkflowEngine()
eng2._variables_main.clear()
step_with_rb = {
    "type": "command", "cmd": "__no_such_cmd__", "params": [],
    "on_error_rollback": [{"type": "variable", "var_name": "rolled", "var_value": "yes"}],
}
try:
    eng2._execute_step(step_with_rb, ".")
except workflow.WorkflowError:
    pass
check(eng2._variables_main.get("rolled") == "yes",
      "失败时先执行 on_error_rollback 再抛错 (补偿变量已写入)",
      repr(eng2._variables_main))


# ── 4. 子工作流引用: 解析 / 循环引用 / 深度 ───────────────────────────
eng3 = workflow.WorkflowEngine()
try:
    eng3._resolve_workflow_target({"type": "workflow", "path": ""}, ".")
    check(False, "空路径应抛错")
except workflow.WorkflowError:
    check(True, "空路径抛 WorkflowError")

try:
    eng3._resolve_workflow_target({"type": "workflow", "path": "missing_sub.json"}, ".")
    check(False, "不存在的子工作流应抛错")
except workflow.WorkflowError:
    check(True, "不存在的子工作流抛 WorkflowError")

with tempfile.TemporaryDirectory(prefix="acrpa_wfref_") as d:
    sub_path = os.path.join(d, "sub.json")
    with open(sub_path, "w", encoding="utf-8") as f:
        f.write('{"name":"sub","steps":[]}')
    # 深度上限
    eng4 = workflow.WorkflowEngine()
    eng4._wf_stack = ["/fake/{}".format(i) for i in range(workflow._WF_MAX_DEPTH)]
    try:
        eng4._run_workflow_ref({"type": "workflow", "path": sub_path}, d)
        check(False, "超过深度上限应抛错")
    except workflow.WorkflowError as e:
        check("嵌套过深" in str(e), "子工作流嵌套过深抛 WorkflowError", str(e))
    # 循环引用检测
    eng5 = workflow.WorkflowEngine()
    eng5._wf_stack = [os.path.abspath(sub_path)]
    try:
        eng5._run_workflow_ref({"type": "workflow", "path": sub_path}, d)
        check(False, "循环引用应抛错")
    except workflow.WorkflowError as e:
        check("循环引用" in str(e), "子工作流循环引用抛 WorkflowError", str(e))


# ── 5. flow_graph 兼容新步骤类型 ─────────────────────────────────────
g = flow_graph.promote({"name": "w", "steps": [
    {"type": "retry", "max_attempts": 3, "delay": 1,
     "steps": [{"type": "log", "text": "a"}]},
    {"type": "workflow", "path": "sub.json", "inputs": {}, "outputs": []},
    {"type": "script", "path": "x.xls",
     "on_error_rollback": [{"type": "log", "text": "rb"}]},
]})
issues = flow_graph.analyze(g)
check(not any(i.get("level") == "error" for i in issues),
      "flow_graph.analyze 对新步骤类型不误报 error", str(issues))
retry_nodes = [n for n in g.nodes if n.type == "retry"]
check(len(retry_nodes) == 1 and len(retry_nodes[0].body_ids()) == 1,
      "retry 在 flow_graph 中作为容器 (子步骤成为 body 子节点)")

# 往返: promote → demote 不丢字段
wf_def = {"name": "w", "steps": [
    {"type": "retry", "max_attempts": 5, "delay": 2.0,
     "steps": [{"type": "wait", "seconds": 1}]},
    {"type": "workflow", "path": "sub.json", "inputs": {"a": "1"}, "outputs": ["b"]},
]}
rt = flow_graph.demote(flow_graph.promote(wf_def))
check(rt["steps"][0]["max_attempts"] == 5 and rt["steps"][0]["delay"] == 2.0,
      "promote/demote 往返保留 retry 参数", repr(rt["steps"][0]))
check(rt["steps"][1]["path"] == "sub.json" and rt["steps"][1]["outputs"] == ["b"],
      "promote/demote 往返保留 workflow 参数", repr(rt["steps"][1]))


# ── 汇总 ─────────────────────────────────────────────────────────────
print("\n------------------------------------------------------------")
print("结果: 通过 {} / 失败 {}".format(len(_PASS), len(_FAIL)))
for f in _FAIL:
    print("  - {}".format(f))
sys.exit(1 if _FAIL else 0)
