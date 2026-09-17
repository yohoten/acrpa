# -*- coding: utf-8 -*-
"""失败语义回归测试 — 验证阶段 0.1/0.2 的执行失败可感知与传播。

覆盖:
- E1: execute() 对未知命令返回 ok=False（不再静默成功）
- E2: execute() 对抛异常的 handler 记录 attempts 且最终 ok=False
- E3: execute() 成功时返回 ok=True / attempts=1
- E4: 找图超时抛 ImageNotFound（不再只 warning + return None）
- E5: 找图超时被 execute() 重试，最终 ok=False
- E6: stop_on_error=True 时脚本在失败行停止，后续命令不执行
- E7: stop_on_error=False 时脚本继续，但 _script_failed 置位
- E8: _exec_timings 记录真实成功标志（调试器耗时面板不再恒真）
- E9: 工作流脚本缺失 → 步骤标记 error
- E10: 工作流 command 节点未知命令 → 步骤标记 error
- E11: 工作流 stop_on_error=False 时失败步骤后继续执行

用法: python tools/_debug_error_semantics.py
退出码: 0=全部通过, 1=存在失败
"""
import os
import sys
import threading

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(BASE, "src")
sys.path.insert(0, SRC)

import state  # noqa: E402
state.running = False
state.quit2 = False
state.pause_event = threading.Event()
state.pause_event.set()
state.debug_mode = False

import commands  # noqa: E402
import engine as engine_mod  # noqa: E402
from engine import engine, ExecutionResult, ImageNotFound, RowAdapter  # noqa: E402
from scriptdata import ScriptData  # noqa: E402
import workflow as wf_mod  # noqa: E402
from workflow import WorkflowEngine, WorkflowError  # noqa: E402

_passed = [0]
_failed = [0]


def check(name, cond, detail=""):
    if cond:
        _passed[0] += 1
        print("  [PASS] " + name)
    else:
        _failed[0] += 1
        print("  [FAIL] " + name + ("  -> " + detail if detail else ""))


# ── 测试脚手架 ──
def _silence_logs():
    """屏蔽日志输出，保持测试结果可读。"""
    wf_mod.log1 = lambda *a, **k: None
    engine_mod.log1 = lambda *a, **k: None


def _stub_screenshot():
    """避免失败截图触碰真实桌面。"""
    class _FakePA(object):
        def screenshot(self, *a, **k):
            return None

    engine_mod.get_pyautogui = lambda: _FakePA()


_silence_logs()
_stub_screenshot()
engine.retry = 0
engine.retry_interval = 0
state.STOP_ON_ERROR = True


def _row(cmd, args=None):
    a = list(args or [])
    a += [""] * (9 - len(a))
    return ScriptData(cmd, a[:9])


# ── E1: 未知命令 ──
print("== E1-M3 execute() 结果语义 ==")
r1 = engine.execute(_row("__不存在的命令__"), os.getcwd())
check("E1 未知命令返回 ok=False", r1.ok is False, repr(r1.ok))
check("E1 未知命令 code=unknown_command", r1.code == "unknown_command", r1.code)
check("E1 返回值具备 bool 兼容(falsy)", not bool(r1), repr(bool(r1)))

# ── E2/E3: handler 抛异常与成功 ──
_attempt_calls = {"n": 0}


def _always_fail_handler(row, z):
    _attempt_calls["n"] += 1
    raise RuntimeError("模拟命令失败")


commands.register("测试失败命令", "仅用于回归测试", "无参数", _always_fail_handler)
engine.retry = 2
r2 = engine.execute(_row("测试失败命令"), os.getcwd())
check("E2 失败命令 ok=False", r2.ok is False, repr(r2.ok))
check("E2 code=command_failed", r2.code == "command_failed", r2.code)
check("E2 attempts=retry+1=3", r2.attempts == 3, str(r2.attempts))
check("E2 handler 实际被调用 3 次", _attempt_calls["n"] == 3, str(_attempt_calls["n"]))

engine.retry = 0
r3 = engine.execute(_row("等待", ["0"]), os.getcwd())
check("E3 成功命令 ok=True", r3.ok is True, repr(r3.ok))
check("E3 attempts=1", r3.attempts == 1, str(r3.attempts))

# ── E4: 找图超时抛异常 ──
print("== E4-E5 找图超时语义 ==")
_orig_find_cached = engine._find_cached
_orig_image_timeout = state.IMAGE_TIMEOUT
state.IMAGE_TIMEOUT = 0.5
engine._find_cached = lambda *a, **k: None
_orig_chk = engine._chk
engine._chk = lambda: True

raised = None
try:
    engine._click(RowAdapter(_row("点图", ["not_exist.png", "0.96"])), os.getcwd())
except ImageNotFound as e:
    raised = e
check("E4 找图超时抛 ImageNotFound", raised is not None, "未抛异常")

engine.retry = 2
r5 = engine.execute(_row("点图", ["not_exist.png", "0.96"]), os.getcwd())
check("E5 找图超时被重试并最终失败", r5.ok is False and r5.attempts == 3,
      "ok={} attempts={}".format(r5.ok, r5.attempts))

engine.retry = 0
engine._find_cached = _orig_find_cached
engine._chk = _orig_chk
state.IMAGE_TIMEOUT = _orig_image_timeout

# ── E6/S1: stop_on_error=True 立即停止 ──
print("== E6-E7 stop_on_error 行为 ==")
engine.retry = 0
state.STOP_ON_ERROR = True
engine.variables.clear()
engine.execute_script([
    _row("测试失败命令", ["a"]),
    _row("设置变量", ["should_not_run", "1"]),
], os.getcwd())
check("E6 失败后后续命令未执行", "should_not_run" not in engine.variables,
      str(engine.variables))
check("E6 _script_failed 置位", engine._script_failed is True,
      repr(engine._script_failed))

# ── E7: stop_on_error=False 继续执行 ──
state.STOP_ON_ERROR = False
engine.variables.clear()
engine.execute_script([
    _row("测试失败命令", ["a"]),
    _row("设置变量", ["should_run", "1"]),
], os.getcwd())
check("E7 失败后仍继续执行", engine.variables.get("should_run") == 1,
      str(engine.variables))
check("E7 _script_failed 仍置位", engine._script_failed is True,
      repr(engine._script_failed))
state.STOP_ON_ERROR = True

# ── E8: _exec_timings 记录真实标志 ──
print("== E8 调试器耗时记录 ==")
state.debug_mode = True
state._exec_timings = {}
engine.variables.clear()
engine.execute_script([
    _row("设置变量", ["okvar", "1"]),
], os.getcwd())
t_ok = list(state._exec_timings.values())
check("E8 成功行 success=True",
      bool(t_ok) and t_ok[0].get("success") is True, str(t_ok[:1]))

state._exec_timings = {}
engine.execute_script([
    _row("测试失败命令", ["a"]),
], os.getcwd())
t_bad = list(state._exec_timings.values())
check("E8 失败行 success=False",
      bool(t_bad) and t_bad[0].get("success") is False, str(t_bad[:1]))
state.debug_mode = False

# ── E9: 工作流脚本缺失 → error ──
print("== E9-E11 工作流错误传播 ==")
state.STOP_ON_ERROR = True
logs, fake = [], (lambda msg, *a, **k: logs.append(str(msg)))
wf_mod.log1 = fake
eng9 = WorkflowEngine()
eng9.run_workflow({
    "name": "E9", "loop_count": 1, "steps": [
        {"type": "script", "path": "__not_exist__.xls"},
        {"type": "log", "text": "after"},
    ],
}, os.getcwd())
check("E9 缺失脚本步骤标记 error", eng9._step_results.get(0) == "error",
      str(eng9._step_results))
check("E9 stop_on_error=True 时后续步骤未执行",
      not any(m == "  [LOG] after" for m in logs), str(logs[-3:]))

# ── E10: command 节点未知命令 → error ──
logs10, fake10 = [], (lambda msg, *a, **k: logs10.append(str(msg)))
wf_mod.log1 = fake10
eng10 = WorkflowEngine()
eng10.run_workflow({
    "name": "E10", "loop_count": 1, "steps": [
        {"type": "command", "cmd": "__不存在的命令__", "params": ["1"]},
        {"type": "log", "text": "after"},
    ],
}, os.getcwd())
check("E10 未知命令节点标记 error", eng10._step_results.get(0) == "error",
      str(eng10._step_results))
check("E10 后续步骤未执行",
      not any(m == "  [LOG] after" for m in logs10), str(logs10[-3:]))

# ── E11: stop_on_error=False 时工作流继续 ──
state.STOP_ON_ERROR = False
logs11, fake11 = [], (lambda msg, *a, **k: logs11.append(str(msg)))
wf_mod.log1 = fake11
eng11 = WorkflowEngine()
eng11.run_workflow({
    "name": "E11", "loop_count": 1, "steps": [
        {"type": "command", "cmd": "__不存在的命令__", "params": ["1"]},
        {"type": "log", "text": "after"},
    ],
}, os.getcwd())
check("E11 失败后继续执行后续步骤",
      any(m == "  [LOG] after" for m in logs11), str(logs11[-3:]))
check("E11 失败步骤仍标记 error", eng11._step_results.get(0) == "error",
      str(eng11._step_results))
state.STOP_ON_ERROR = True

# ── E12-E14: 循环体内失败传播 (验证者发现的缺陷: break 曾只退出内层循环) ──
print("== E12-E14 循环体内失败传播 ==")
engine.retry = 0
_loop_calls = {"n": 0}


def _counting_fail_handler(row, z):
    _loop_calls["n"] += 1
    raise RuntimeError("模拟循环体内失败")


commands.register("测试循环失败命令", "仅用于回归测试", "无参数", _counting_fail_handler)

state.STOP_ON_ERROR = True
_loop_calls["n"] = 0
engine.variables.clear()
engine.execute_script([
    _row("循环开始", ["5"]),
    _row("测试循环失败命令", ["a"]),
    _row("循环结束", [None] * 9),
    _row("设置变量", ["after_loop", "1"]),
], os.getcwd())
check("E12 循环体内失败仅执行 1 次(不再重跑 5 次)", _loop_calls["n"] == 1,
      "calls={}".format(_loop_calls["n"]))
check("E12 失败后循环外命令未执行", "after_loop" not in engine.variables,
      str(engine.variables))

_loop_calls["n"] = 0
engine.variables.clear()
engine.execute_script([
    _row("循环开始", ["5"]),
    _row("如果", ["1 == 1"]),
    _row("测试循环失败命令", ["a"]),
    _row("结束如果", [None] * 9),
    _row("循环结束", [None] * 9),
    _row("设置变量", ["after_if_loop", "1"]),
], os.getcwd())
check("E13 循环内 IF 块失败仅执行 1 次", _loop_calls["n"] == 1,
      "calls={}".format(_loop_calls["n"]))
check("E13 失败后未继续执行", "after_if_loop" not in engine.variables,
      str(engine.variables))

state.STOP_ON_ERROR = False
_loop_calls["n"] = 0
engine.variables.clear()
engine.execute_script([
    _row("循环开始", ["3"]),
    _row("测试循环失败命令", ["a"]),
    _row("循环结束", [None] * 9),
    _row("设置变量", ["after_loop2", "1"]),
], os.getcwd())
check("E14 stop_on_error=False 时循环跑满 3 次", _loop_calls["n"] == 3,
      "calls={}".format(_loop_calls["n"]))
check("E14 循环后命令仍执行", engine.variables.get("after_loop2") == 1,
      str(engine.variables))
state.STOP_ON_ERROR = True

# ── E15: 块结构标记兼容性 (已注册但无 handler，不应误判为失败) ──
r15 = engine.execute(_row("循环结束", [None] * 9), os.getcwd())
check("E15 无 handler 的块标记不判失败", r15.ok is True, repr(r15.ok))
check("E15 block marker code=no_handler", r15.code == "no_handler", r15.code)
r15b = engine.execute(_row("如果", ["1"]), os.getcwd())
check("E15 '如果' 标记同样不判失败", r15b.ok is True, repr(r15b.ok))

# ── 清理 ──
for _n in ("测试失败命令", "测试循环失败命令"):
    commands._registry[:] = [r for r in commands._registry if r[0] != _n]

print()
print("=" * 50)
print("失败语义回归: 通过 {} 项, 失败 {} 项".format(_passed[0], _failed[0]))
print("=" * 50)
sys.exit(1 if _failed[0] else 0)
