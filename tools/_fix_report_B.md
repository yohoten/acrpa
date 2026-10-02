# 修复报告 B —— BUG-04：trusted 超时可能被 `sys.settrace(None)` / C 层阻塞绕过

- 子任务：修复-B（Orchestrator 委派）
- 工作区：`d:/CodingEmber/ACRPA`
- 涉及文件（仅此两个）：
  - [`src/py_sandbox.py`](../src/py_sandbox.py)
  - [`tools/_test_python_sandbox.py`](../tools/_test_python_sandbox.py)
- 环境：Windows + cmd.exe，解释器 `.venv\Scripts\python.exe`

---

## 1. 采用方案：**方案 B（进程内加固）**，未采用方案 A

### 1.1 为什么不采用方案 A（进程外强制超时）

方案 A（把 sandbox / trusted 的实际执行搬进独立子进程，由父进程按墙钟 `join/communicate(timeout)` 后 `terminate/kill`）在**本项目中不可行**，理由：

1. **公共 API 语义会被破坏**：`run(code, perm, timeout, api=..., ...)` 的 `api` 参数是一个**活体 `AcrpaAPI` 实例**（[`src/engine.py:1203`](../src/engine.py:1203)、[`src/engine.py:1311`](../src/engine.py:1311) 传入 `api=api`）。脚本中大量使用 `acrpa.click(...)` 等对宿主进程有真实副作用的调用。子进程无法安全地共享该活体对象，必须为每个方法实现 IPC 代理，属于对既有语义的**显著改变**——违反“最小且稳健的改动”要求。
2. **项目显式约束**：既有验收用例 [`tools/_test_python_sandbox.py:171`](../tools/_test_python_sandbox.py:171)（j3）断言 `"import multiprocessing" not in py_sandbox`，即项目有意为 sandbox / trusted 保留进程内执行、仅 full 走子进程。方案 A 与之一致性冲突。
3. full 档（[`src/py_sandbox.py:489`](../src/py_sandbox.py:489) `_exec_full`，subprocess + `kill`）本就已是方案 A 形态，仅适用于无需注入 `api` 的场景；trusted 与之定位不同。

结论：采用**方案 B**——在保留 `sys.settrace` 逐行检查作为兜底的前提下，叠加“追踪器保护 + 看门狗线程异步异常注入”两层加固，且**对外语义完全不变**。

---

## 2. 改动前后要点（文件:行）

### 2.1 [`src/py_sandbox.py`](../src/py_sandbox.py)

| 位置（改后行号） | 改动 |
| --- | --- |
| [`src/py_sandbox.py:9`](../src/py_sandbox.py:9) | 模块 docstring：更新超时机制说明（双层防护 + 残留限制）。 |
| [`src/py_sandbox.py:33`](../src/py_sandbox.py:33) | 新增 `import ctypes`（仅标准库，满足“纯标准库”约束）。 |
| [`src/py_sandbox.py:316`](../src/py_sandbox.py:316) | 新增常量 `_WATCHDOG_GRACE=0.3` / `_WATCHDOG_JOIN=2.0`。 |
| [`src/py_sandbox.py:321`](../src/py_sandbox.py:321) | 新增 `_block_trace_call()`：执行窗口内替代 `sys.settrace`/`sys.setprofile`，**屏蔽 `sys.settrace(None)` 关闭追踪的企图**。 |
| [`src/py_sandbox.py:331`](../src/py_sandbox.py:331) | 新增 `_async_raise()`：`ctypes.pythonapi.PyThreadState_SetAsyncExc` 向目标线程注入异常（含目标不唯一时回滚）。 |
| [`src/py_sandbox.py:353`](../src/py_sandbox.py:353) | 新增 `_start_watchdog()`：守护线程按墙钟计时，超时注入 `TimeoutError`；含 `_WATCHDOG_GRACE` 宽限避免与追踪器重复抛出。 |
| [`src/py_sandbox.py:383`](../src/py_sandbox.py:383) | 新增 `_stop_watchdog()`：幂等停止信号。 |
| [`src/py_sandbox.py:392`](../src/py_sandbox.py:392) | 重写 `_exec_inprocess()`：安装 tracer 前用 `_block_trace_call` 接管 `sys.settrace/setprofile`，并行启动看门狗；`finally` 中**依次**置 `exec_done` → 停看门狗 → 清 tracer → 恢复原函数。异常分支与返回结构（`timed_out` / `error`）保持原语义。 |

**改动前**（原实现，依赖单层 trace）：

```python
# 原 src/py_sandbox.py:310-338（节选）
tracer = _make_tracer(deadline)
sys.settrace(tracer)          # ← 被测代码 sys.settrace(None) 即可关闭
try:
    exec(compiled, ns)
except TimeoutError as e:
    result["timed_out"] = True
    result["error"] = "TimeoutError: %s" % e
    ...
finally:
    sys.settrace(None)
```

**改动后**（双层防护）：

```python
# 新 src/py_sandbox.py:415-440（节选）
orig_settrace = sys.settrace
orig_setprofile = sys.setprofile
sys.settrace = _block_trace_call     # ← 屏蔽 settrace(None)
sys.setprofile = _block_trace_call
orig_settrace(tracer)                # 用真实函数装 tracer
watchdog_stop = _start_watchdog(deadline, ident, state)  # ← 墙钟看门狗
try:
    exec(compiled, ns)
except TimeoutError as e:
    result["timed_out"] = True
    result["error"] = "TimeoutError: %s" % (str(e) or "code timeout")
    ...
finally:
    state["exec_done"] = True
    _stop_watchdog(watchdog_stop)
    orig_settrace(None)
    sys.settrace = orig_settrace      # 恢复现场
    sys.setprofile = orig_setprofile
```

### 2.2 [`tools/_test_python_sandbox.py`](../tools/_test_python_sandbox.py)

| 位置（改后行号） | 改动 |
| --- | --- |
| [`tools/_test_python_sandbox.py:12`](../tools/_test_python_sandbox.py:12) | 新增 `import threading`（用于线程泄漏断言）。 |
| [`tools/_test_python_sandbox.py:445`](../tools/_test_python_sandbox.py:445) | 用**真实对抗性用例**替换原先只打印 `[WARN]` 的 §1.3 占位段（v0~v8）。 |

### 2.3 向后兼容性

- 公共 API（函数名 / 参数 / 返回 / 异常语义）**零变更**：`run`、`precheck`、`audit_*`、`make_record`、`cleanup_audit`、常量集均未改动签名与语义。
- 超时对外表现不变：仍 `result["timed_out"]=True`、`result["error"]` 以 `"TimeoutError: ..."` 开头；非超时异常仍按 `"<Type>: <msg>"` 返回；`ok/result/raw_result/elapsed/perm` 不变。
- 调用方 [`src/engine.py:1203`](../src/engine.py:1203) / [`src/engine.py:1311`](../src/engine.py:1311) 未受影响。

---

## 3. 对抗性用例结果（[`tools/_test_python_sandbox.py`](../tools/_test_python_sandbox.py) v0~v8）

| 用例 | 断言 | 结果 |
| --- | --- | --- |
| v0 | 源码含 `PyThreadState_SetAsyncExc`（看门狗存在） | [OK] |
| v0b | 源码含 `_block_trace_call`（追踪器保护存在） | [OK] |
| v1/v2 | `import sys; sys.settrace(None); while True: pass`（trusted, timeout=1s）→ `timed_out=True` 且 `elapsed=1.00s < 5s` | [OK] |
| v3/v4 | 纯 Python 死循环 `while True: pass`（trusted, timeout=1s）→ `timed_out=True` 且 `elapsed=1.00s < 5s` | [OK] |
| v5 | C 层阻塞 `import time; time.sleep(4)`（trusted, timeout=1s） | **[WARN] KNOWN LIMITATION**（见 §5） |
| v6 | 正常代码 `result = 6*7`（trusted）→ `ok=True, result='42'` | [OK] |
| v7 | 正常异常 `result = 1/0`（trusted）→ `ok=False, error` 含 `ZeroDivisionError` | [OK] |
| v8 | 执行结束后 `threading.active_count()` 未增长（无看门狗线程泄漏） | [OK] |

v5 实测输出（真实、未伪造）：

```
[WARN] v5 KNOWN LIMITATION (方案 B): C 层阻塞 time.sleep 无法按时中断，
       仅在阻塞返回后抛超时 (elapsed=4.01s, timed_out=True)
```

即：注入在 `deadline+宽限` 时已发出，但 `time.sleep` 期间无法即时生效，直到阻塞返回后的下一字节码边界才抛出 `TimeoutError`（`timed_out` 最终为 `True`，但**耗时 ≈ sleep 全程**）。

---

## 4. 自验证命令（cmd.exe 逐条执行，`-X utf8`）

| # | 命令 | 退出码 | 结论 |
| --- | --- | --- | --- |
| 1 | `.venv\Scripts\python.exe -X utf8 tools\_test_python_sandbox.py` | **0** | `SUMMARY: 0 FAIL, 1 WARN`（唯一 WARN 为 v5 已知限制） |
| 2 | `.venv\Scripts\python.exe -X utf8 tools\_test_code_command.py` | **0** | `SUMMARY: 0 FAIL, 0 WARN` |
| 3 | `.venv\Scripts\python.exe -X utf8 tools\_debug_check.py` | **0** | `syntax_failed=0 import_failed=0`（106 文件语法 0 失败；18/18 模块导入成功） |
| 4 | `.venv\Scripts\python.exe -X utf8 tools\_debug_functional.py` | **0** | `全部通过 ✓` |

关键输出（命令 1 对抗性段）：

```
[OK] v0 watchdog async-inject (PyThreadState_SetAsyncExc) present
[OK] v1 settrace(None)+deadloop timed_out (timed_out=True)
[OK] v2 settrace(None) bypass interrupted in time (1.00s <5s)
[OK] v3 pure-python deadloop timed_out (timed_out=True)
[OK] v4 pure-python deadloop interrupted in time (1.00s <5s)
[WARN] v5 KNOWN LIMITATION (方案 B): C 层阻塞 time.sleep 无法按时中断，仅在阻塞返回后抛超时 (elapsed=4.01s, timed_out=True)
[OK] v6 trusted normal result preserved (got '42')
[OK] v7 trusted normal exception preserved (error='ZeroDivisionError: division by zero')
[OK] v8 no watchdog thread leak (active=1 base=1)
SUMMARY: 0 FAIL, 1 WARN
```

---

## 5. 残留限制（如实标注）

1. **C 层阻塞无法按时中断（方案 B 固有）**：被测脚本阻塞在 `time.sleep(huge)`、原生扩展 / DLL 调用等**不返回字节码边界**的 C 调用期间，`PyThreadState_SetAsyncExc` 注入的异常**不会即时生效**，仅在阻塞返回后的下一字节码边界才抛出。因此超时**能被最终检测**（`timed_out=True`），但**中断时刻会被推迟到阻塞结束**。
   - 该限制已由 v5 捕获为明确的 `[WARN] KNOWN LIMITATION`，**未伪造为 PASS/FAIL**。
   - 要彻底消除该限制需采用方案 A（进程外强制 `kill`）；因 §1.1 的语义约束本批次不采用，属后续批次可评估项（可考虑“常驻 worker 子进程 + Job Object”等，但需先解决 `api` 活体对象注入问题）。
2. **`sys.settrace` 屏蔽的全局性**：执行窗口内 `sys.settrace`/`sys.setprofile` 属性被临时接管，窗口极短且 `finally` 必定恢复；对同进程其它线程在该窗口内主动调用 `sys.settrace` 的行为会被忽略（本项目脚本执行路径无此用法）。
3. **多线程逃逸不在本批次范围**：被测脚本若自行 `threading.Thread` 启动后台死循环，看门狗仅针对当前执行线程注入；线程相关的强制回收不在 BUG-04 修复目标内。
4. **追踪器 vs 看门狗的协同**：为免二者在同一超时时点重复抛出，看门狗在 `deadline` 之后额外等待 `_WATCHDOG_GRACE=0.3s` 并检查 `exec_done`/`stop`；纯 Python 场景由行级追踪器优先命中（实测 1.00s ≈ timeout），看门狗作为其被绕过时的兜底。
