# -*- coding: utf-8 -*-
"""bg_task 异常可见性 + 安全关闭回归 (P0-7 / P0-8)。

缺陷:
  D7 工作线程吞异常: payload 第三位恒 None, `on_error=None` 时完全静默;
  D8 `shutdown()` 只清队列, 在飞 worker 仍把结果 put 进无人消费的队列,
     且无 join / 无关闭标志 → 关窗后可能跑到失效控件上, 重启后旧回调还会执行。

断言 (本文件):
  A. `submit(fn_raises, None, None)` 后必须出现 tag="error" 的日志,
     内容含异常类型/信息与 ≥3 行 traceback;
  B. `start()` → `submit(sleep 0.5)` → 立即 `shutdown()`: 1.2 s 内返回、
     `pending()==0`、旧回调未执行;
  C. 重新 `start()` 后泵事件循环, 上一轮的旧回调仍不会被执行;
  D. 重启后新任务能正常回调。

不创建 Tk 窗口: 用 FakeRoot 提供 after/after_cancel, 手动泵事件循环。
运行: python -u tests/_test_bg_task_error.py
退出码: 0 = 全部通过 / 1 = 存在失败
"""
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_SRC = os.path.join(_ROOT, "src")
for _p in (_SRC, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import core.log as core_log          # noqa: E402
import ui.bg_task as bt              # noqa: E402

_PASS = 0
_FAIL = 0


def check(name, cond, detail=""):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print("[OK]   {}".format(name))
    else:
        _FAIL += 1
        print("[FAIL] {}  | {}".format(name, detail))


class _Sink(object):
    """log1 的落点替身: 只记录 (与 ThreadSafeLog.put 同签名), 不碰任何控件。"""

    def __init__(self):
        self.items = []

    def put(self, msg, tag=None, caller_file="", caller_func="", level=None):
        self.items.append({"msg": msg, "tag": tag, "level": level})


class FakeRoot(object):
    """最小 Tk 替身: after() 只登记回调, 由 pump() 手动驱动。"""

    def __init__(self):
        self.jobs = []
        self.seq = 0

    def after(self, ms, fn=None):
        if fn is None:
            fn, ms = ms, 0
        self.seq += 1
        self.jobs.append((self.seq, fn))
        return self.seq

    def after_cancel(self, job):
        self.jobs = [j for j in self.jobs if j[0] != job]

    def pump(self, rounds=10, wait=0.02):
        for _ in range(rounds):
            jobs, self.jobs = self.jobs, []
            for _sid, fn in jobs:
                fn()
            time.sleep(wait)


def _boom():
    raise RuntimeError("bg-boom")


def main():
    sink = _Sink()
    core_log.set_tlog(sink)

    # ── A. 异常必落日志 (on_error=None 也不静默) ──
    bt.shutdown()
    root = FakeRoot()
    bt.start(root, poll_ms=10)
    bt.submit(_boom, None, None, name="boom")
    deadline = time.time() + 3
    while bt.pending() and time.time() < deadline:
        root.pump(1)
    root.pump(2)

    errs = [i for i in sink.items if i["tag"] == "error"]
    check("A1. on_error=None 时异常仍落 error 日志", len(errs) >= 1, sink.items)
    msg = errs[0]["msg"] if errs else ""
    check("A2. 日志含异常类型与信息 (RuntimeError/bg-boom)",
          ("RuntimeError" in msg) and ("bg-boom" in msg), msg)
    lines = [ln for ln in msg.splitlines() if ln.strip()]
    check("A3. 日志含 traceback (≥3 行)", len(lines) >= 3, lines)
    check("A4. 日志标明任务名 (bg:boom)", "bg:boom" in msg, msg)
    check("A5. 异常任务处理后 pending 归零", bt.pending() == 0, bt.pending())

    # ── B. shutdown 安全性 (在飞任务不得污染下一轮) ──
    called = []

    def _slow():
        time.sleep(0.5)
        return "late"

    bt.shutdown()
    root = FakeRoot()
    bt.start(root, poll_ms=10)
    check("B0. 重新 start 成功", bt.is_started() is True)
    bt.submit(_slow, lambda v: called.append(v), name="slow")
    t0 = time.time()
    bt.shutdown()
    elapsed = time.time() - t0
    check("B1. shutdown 在 1.2 s 内返回", elapsed <= 1.2, elapsed)
    check("B2. shutdown 后 pending == 0", bt.pending() == 0, bt.pending())
    check("B3. 在飞任务的结果被丢弃 (回调未执行)", called == [], called)

    # ── C. 重启后旧回调仍不执行 ──
    root2 = FakeRoot()
    bt.start(root2, poll_ms=10)
    root2.pump(8, 0.05)
    check("C1. 重新 start 后旧回调不会被执行", called == [], called)

    # ── D. 新轮次功能正常 ──
    done = []
    bt.submit(lambda: 7, lambda v: done.append(v), name="round2")
    deadline = time.time() + 3
    while bt.pending() and time.time() < deadline:
        root2.pump(1)
    root2.pump(2)
    check("D1. 重启后新任务正常回调", done == [7], done)

    bt.shutdown()
    core_log.set_tlog(None)
    print("\n结果: PASS={} FAIL={}".format(_PASS, _FAIL))
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
