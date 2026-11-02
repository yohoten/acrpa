# -*- coding: utf-8 -*-
"""bg_task 单元测试: 异步投递/回调顺序/异常通道/同步回退/关闭清理。

不创建 Tk 窗口: 用一个假 root (只提供 after/after_cancel) 手动驱动事件循环,
这样测试可在无 GUI 的 CI 环境运行。
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import ui.bg_task as bt

PASS = FAIL = 0


def check(title, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS {}".format(title))
    else:
        FAIL += 1
        print("  FAIL {} {}".format(title, detail))


class FakeRoot(object):
    """最小 Tk 替身: after() 只登记回调, 由测试用 pump() 手动跑。"""

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

    def pump(self, rounds=6):
        """按登记顺序执行一轮回调 (模拟事件循环)。"""
        for _ in range(rounds):
            jobs, self.jobs = self.jobs, []
            if not jobs:
                time.sleep(0.02)
                continue
            for _sid, fn in jobs:
                fn()


print("── 1. 未绑定时同步回退 (测试/无 GUI 环境零行为变化) ──")
bt.shutdown()
check("1a. is_started() 初始为 False", bt.is_started() is False)
_seen = []


def _ok_sync(x):
    _seen.append(("done", x))


_ret = bt.submit(lambda: 42, _ok_sync, name="sync")
check("1b. submit 返回 False 表示同步", _ret is False, _ret)
check("1c. 同步回调立即拿到结果", _seen == [("done", 42)], _seen)

_err = {}


def _bad():
    raise ValueError("boom")


bt.submit(_bad, None, lambda e: _err.setdefault("e", e), name="sync-err")
check("1d. 同步异常走 on_error", isinstance(_err.get("e"), ValueError), _err)

try:
    bt.submit(_bad, None, None, name="sync-raise")
    _raised = False
except ValueError:
    _raised = True
check("1e. 无 on_error 时同步重新抛出 (保持原语义)", _raised)


print("── 2. 异步投递与回调顺序 ──")
root = FakeRoot()
bt.start(root, poll_ms=10)
check("2a. start 后 is_started() 为 True", bt.is_started() is True)
check("2b. start 已排期一次轮询", len(root.jobs) == 1, root.jobs)

_order = []


def _slow():
    time.sleep(0.05)
    _order.append("work")
    return "payload"


bt.submit(_slow, lambda v: _order.append(("done", v)), name="async")
check("2c. 提交后 pending=1", bt.pending() == 1, bt.pending())
check("2d. 工作尚未完成时主线程不被阻塞", "work" not in _order, _order)
root.pump()                      # 进入轮询: 结果可能尚未就绪 → 再泵几轮
deadline = time.time() + 3
while bt.pending() and time.time() < deadline:
    root.pump(1)
    time.sleep(0.02)
check("2e. 回调在主线程轮询中执行", ("done", "payload") in _order, _order)
check("2f. 回调前先完成工作 (顺序正确)", _order[:1] == ["work"], _order)
check("2g. 回调后 pending 归零", bt.pending() == 0, bt.pending())

print("── 3. 异常通道与回调健壮性 ──")
_got = {}


def _raise():
    raise RuntimeError("kb")


bt.submit(_raise, None, lambda e: _got.setdefault("err", repr(e)), name="async-err")
deadline = time.time() + 3
while bt.pending() and time.time() < deadline:
    root.pump(1)
    time.sleep(0.02)
check("3a. 异步异常投递到 on_error", "kb" in _got.get("err", ""), _got)


def _boom_cb(_v):
    raise ValueError("callback-boom")


bt.submit(lambda: 1, _boom_cb, name="bad-cb")
deadline = time.time() + 3
while bt.pending() and time.time() < deadline:
    root.pump(1)
    time.sleep(0.02)
check("3b. 回调内部异常不打断轮询", bt.pending() == 0, bt.pending())
bt.submit(lambda: 2, lambda v: _got.setdefault("after", v), name="alive")
deadline = time.time() + 3
while bt.pending() and time.time() < deadline:
    root.pump(1)
    time.sleep(0.02)
check("3c. 坏回调之后轮询仍可正常投递", _got.get("after") == 2, _got)

print("── 4. drain / shutdown ──")
bt.shutdown()
check("4a. shutdown 后 is_started() 为 False", bt.is_started() is False)
check("4b. shutdown 后 pending 归零", bt.pending() == 0, bt.pending())
bt.submit(lambda: "x", lambda v: _got.setdefault("post", v), name="post-shutdown")
check("4c. shutdown 后 submit 退化为同步", _got.get("post") == "x", _got)
check("4d. drain 空队列返回 0", bt.drain() == 0)

print("\n结果: PASS={} FAIL={}".format(PASS, FAIL))
sys.exit(1 if FAIL else 0)
