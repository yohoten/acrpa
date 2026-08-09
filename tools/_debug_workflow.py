# -*- coding: utf-8 -*-
"""工作流引擎新节点类型 (command/variable/loop/log) + 执行控制功能验证。

覆盖:
- T1: variable/log/wait 基础节点 + current_step 复位 + _step_results
- T2: enabled=False 禁用跳过 + comment 注释不影响执行
- T3: loop 节点固定次数循环
- T4: 外层 loop_count 循环
- T5: loop 节点条件循环 (条件为假立即结束)
- T6: command 节点未知命令容错
- T7: max_minutes 超时参数不报错
- T8: _normalize_step 新节点默认字段
"""
import os
import sys
import threading

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
sys.path.insert(0, SRC)

import state
state.running = False
state.quit2 = False
state.pause_event = threading.Event()
state.pause_event.set()
state.STOP_ON_ERROR = False

import workflow as wf_mod
from workflow import WorkflowEngine

_passed = [0]
_failed = [0]


def check(name, cond, detail=""):
    if cond:
        _passed[0] += 1
        print("  [PASS] " + name)
    else:
        _failed[0] += 1
        print("  [FAIL] " + name + ("  -> " + detail if detail else ""))


def collector():
    logs = []

    def _fake(msg, *a, **k):
        logs.append(str(msg))

    return logs, _fake


# ---------- T1: variable/log/wait + 状态跟踪 ----------
print("== T1 基础节点 (variable/log/wait) ==")
eng = WorkflowEngine()
wf = {"name": "T1", "loop_count": 1, "steps": [
    {"type": "variable", "var_name": "a", "var_value": "5"},
    {"type": "variable", "var_name": "b", "var_value": "${a}"},
    {"type": "log", "text": "a=${a}, b=${b}"},
    {"type": "wait", "seconds": 0.1},
]}
eng.run_workflow(wf, ".")
check("T1 变量 a=5", eng._variables.get("a") == 5, str(eng._variables))
check("T1 变量 b 解析 ${a} 为 5", eng._variables.get("b") == 5, str(eng._variables))
check("T1 结束后 current_step 复位 -1", eng.current_step == -1, str(eng.current_step))
check("T1 _step_results 全部 ok", all(v == "ok" for v in eng._step_results.values()),
      str(eng._step_results))

# ---------- T2: enabled=False 跳过 + comment 注释 ----------
print("== T2 禁用跳过 + 注释 ==")
eng2 = WorkflowEngine()
wf2 = {"name": "T2", "loop_count": 1, "steps": [
    {"type": "variable", "var_name": "skip", "var_value": "1", "enabled": False},
    {"type": "variable", "var_name": "ok", "var_value": "2", "comment": "带注释的节点"},
]}
eng2.run_workflow(wf2, ".")
check("T2 禁用节点未写入变量 skip", "skip" not in eng2._variables, str(eng2._variables))
check("T2 注释节点正常执行 ok=2", eng2._variables.get("ok") == 2, str(eng2._variables))
check("T2 禁用步骤标记 ok(无异常)", eng2._step_results.get(0) == "ok", str(eng2._step_results))

# ---------- T3: loop 节点固定次数 ----------
print("== T3 loop 节点固定次数 ==")
logs3, fake3 = collector()
wf_mod.log1 = fake3
eng3 = WorkflowEngine()
wf3 = {"name": "T3", "loop_count": 1, "steps": [
    {"type": "log", "text": "outer"},
    {"type": "loop", "times": "3", "steps": [
        {"type": "log", "text": "inner"},
    ]},
]}
eng3.run_workflow(wf3, ".")
n_inner = sum(1 for m in logs3 if m == "  [LOG] inner")
n_outer = sum(1 for m in logs3 if m == "  [LOG] outer")
check("T3 内层循环执行 3 次", n_inner == 3, "inner={}".format(n_inner))
check("T3 外层步骤执行 1 次", n_outer == 1, "outer={}".format(n_outer))

# ---------- T4: 外层 loop_count ----------
print("== T4 外层 loop_count ==")
logs4, fake4 = collector()
wf_mod.log1 = fake4
eng4 = WorkflowEngine()
wf4 = {"name": "T4", "loop_count": 3, "steps": [
    {"type": "log", "text": "step"},
]}
eng4.run_workflow(wf4, ".")
n4 = sum(1 for m in logs4 if m == "  [LOG] step")
check("T4 外层循环执行 3 次", n4 == 3, "step={}".format(n4))

# ---------- T5: loop 条件循环 (条件为假立即结束) ----------
print("== T5 loop 条件循环 ==")
logs5, fake5 = collector()
wf_mod.log1 = fake5
eng5 = WorkflowEngine()
eng5._variables["x"] = 5
wf5 = {"name": "T5", "loop_count": 1, "steps": [
    {"type": "log", "text": "before"},
    {"type": "loop", "times": "${x} < 3", "steps": [
        {"type": "log", "text": "cond"},
    ]},
    {"type": "log", "text": "after"},
]}
eng5.run_workflow(wf5, ".")
n_cond = sum(1 for m in logs5 if m == "  [LOG] cond")
n_before = sum(1 for m in logs5 if m == "  [LOG] before")
n_after = sum(1 for m in logs5 if m == "  [LOG] after")
check("T5 条件为假时内层执行 0 次", n_cond == 0, "cond={}".format(n_cond))
check("T5 前后步骤正常执行", n_before == 1 and n_after == 1,
      "before={}, after={}".format(n_before, n_after))

# ---------- T6: command 节点未知命令容错 ----------
print("== T6 command 节点容错 ==")
logs6, fake6 = collector()
wf_mod.log1 = fake6
eng6 = WorkflowEngine()
wf6 = {"name": "T6", "loop_count": 1, "steps": [
    {"type": "command", "cmd": "不存在的命令_xyz", "params": ["1", "2", "3"]},
    {"type": "log", "text": "done"},
]}
eng6.run_workflow(wf6, ".")
check("T6 未知命令容错不中断 (done 已执行)",
      any(m == "  [LOG] done" for m in logs6), str(logs6[-3:]))
check("T6 未知命令步骤标记 ok", eng6._step_results.get(0) == "ok", str(eng6._step_results))

# ---------- T7: max_minutes 超时参数 ----------
print("== T7 max_minutes 参数 ==")
eng7 = WorkflowEngine()
wf7 = {"name": "T7", "loop_count": 2, "max_minutes": 1, "steps": [
    {"type": "log", "text": "t"},
]}
eng7.run_workflow(wf7, ".")
check("T7 max_minutes 运行完成并复位", eng7.current_step == -1, str(eng7.current_step))

# ---------- T8: _normalize_step 默认字段 ----------
print("== T8 _normalize_step 默认字段 ==")
ns = wf_mod._normalize_step({"type": "command", "cmd": "坐标"})
check("T8 command 默认 params 为 9 个空串",
      isinstance(ns.get("params"), list) and len(ns["params"]) == 9, str(ns.get("params")))
ns2 = wf_mod._normalize_step({"type": "variable", "var_name": "v"})
check("T8 variable 默认 var_value 空串", ns2.get("var_value") == "", str(ns2))
ns3 = wf_mod._normalize_step({"type": "loop", "steps": [{"path": "a.xls"}]})
check("T8 loop 默认 times=1", ns3.get("times") == "1", str(ns3.get("times")))
ns4 = wf_mod._normalize_step("a.xls")
check("T8 字符串简写→script 节点",
      ns4 == {"type": "script", "path": "a.xls"}, str(ns4))

print()
print("=" * 50)
print("工作流引擎验证: 通过 {} 项, 失败 {} 项".format(_passed[0], _failed[0]))
print("=" * 50)
sys.exit(1 if _failed[0] else 0)
