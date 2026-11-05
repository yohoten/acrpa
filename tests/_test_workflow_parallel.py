#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""真并行分支隔离守卫 —— 作用域隔离 / outputs 确定性合并 / 失败汇总。

用法:
    .venv\\Scripts\\python.exe -X utf8 tests\\_test_workflow_parallel.py

覆盖:
  1. 分支变量作用域隔离: 各分支写同名变量互不可见, 只有 outputs 声明的键回写主作用域。
  2. outputs 按分支定义顺序确定性合并 (后定义覆盖先定义), 与线程完成顺序无关。
  3. 未声明 outputs 时向后兼容: 合并全部分支变量 (merge_all)。
  4. 分支失败按 STOP_ON_ERROR 语义汇总抛 WorkflowError。
  5. 源码断言: _run_parallel 使用 ThreadPoolExecutor + 隔离作用域, 无串行化锁。

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

import workflow  # noqa: E402

_PASS, _FAIL = [], []


def check(cond, msg, detail=""):
    (_PASS if cond else _FAIL).append(msg)
    tail = ("  ({})".format(detail) if detail else "")
    print("[{}] {}{}".format("OK  " if cond else "FAIL", msg, tail))


def _read(rel):
    with io.open(os.path.join(ROOT, rel), "r", encoding="utf-8") as f:
        return f.read()


# ── 1. 分支隔离 + outputs 确定性合并 ──────────────────────────────────
eng = workflow.WorkflowEngine()
eng._variables_main.clear()

# 每分支用 retry(max_attempts=1) 承载两个 variable 子步骤 (retry 顺序执行子步骤):
#   分支1: shared=b1, leak0=x
#   分支2: shared=b2, leak1=y
step = {
    "type": "parallel",
    "steps": [
        {"type": "retry", "max_attempts": 1, "delay": 0, "steps": [
            {"type": "variable", "var_name": "shared", "var_value": "b1"},
            {"type": "variable", "var_name": "leak0", "var_value": "x"},
        ]},
        {"type": "retry", "max_attempts": 1, "delay": 0, "steps": [
            {"type": "variable", "var_name": "shared", "var_value": "b2"},
            {"type": "variable", "var_name": "leak1", "var_value": "y"},
        ]},
    ],
    "outputs": ["shared"],
}
eng._run_parallel(step, ".")
check(eng._variables_main.get("shared") == "b2",
      "outputs 按分支定义顺序合并 (后定义 b2 覆盖 b1)",
      repr(eng._variables_main.get("shared")))
check("leak0" not in eng._variables_main and "leak1" not in eng._variables_main,
      "未声明为 outputs 的分支变量不泄漏到主作用域",
      repr(eng._variables_main))


# ── 2. merge_all (向后兼容: 未声明 outputs 合并全部) ──────────────────
eng2 = workflow.WorkflowEngine()
eng2._variables_main.clear()
step_all = {
    "type": "parallel",
    "steps": [
        {"type": "variable", "var_name": "a", "var_value": "1"},
        {"type": "variable", "var_name": "b", "var_value": "2"},
    ],
}
eng2._run_parallel(step_all, ".")
check(eng2._variables_main.get("a") == 1 and eng2._variables_main.get("b") == 2,
      "未声明 outputs 时合并全部分支变量 (旧行为)",
      repr(eng2._variables_main))


# ── 3. 失败汇总抛 WorkflowError ───────────────────────────────────────
eng3 = workflow.WorkflowEngine()
step_fail = {
    "type": "parallel",
    "steps": [
        {"type": "variable", "var_name": "ok", "var_value": "1"},
        {"type": "command", "cmd": "__no_such_cmd__", "params": []},
    ],
}
raised = False
try:
    eng3._run_parallel(step_fail, ".")
except workflow.WorkflowError as e:
    raised = True
    check("并行步骤" in str(e), "并行失败汇总抛 WorkflowError 且指明分支", str(e))
check(raised, "任一分支失败 → 汇总抛 WorkflowError")


# ── 4. 源码断言 (无串行化) ────────────────────────────────────────────
src = _read("src/workflow.py")
check("_with_isolated_scope" in src, "workflow.py 提供 _with_isolated_scope 隔离作用域")
check("ThreadPoolExecutor" in src and "as_completed" in src,
      "并行使用 ThreadPoolExecutor + as_completed")
check("def _run_parallel" in src and "branch_scopes" in src,
      "每分支独立 branch_scopes 作用域副本")
check("outputs" in src and "merge_all" in src,
      "支持显式 outputs 与 merge_all 向后兼容")

# ── 汇总 ─────────────────────────────────────────────────────────────
print("\n------------------------------------------------------------")
print("结果: 通过 {} / 失败 {}".format(len(_PASS), len(_FAIL)))
for f in _FAIL:
    print("  - {}".format(f))
sys.exit(1 if _FAIL else 0)
