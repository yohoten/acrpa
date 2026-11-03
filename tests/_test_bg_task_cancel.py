# -*- coding: utf-8 -*-
"""bg_task v2 (v5 §2.2 P1-3): CancelToken / submit_chunked / 有界队列背压。

不创建 Tk 窗口: 用假 root (只提供 after/after_cancel) 手动驱动事件循环,
因此可在无 GUI 的 CI 环境运行 (与 tests/_test_bg_task.py 同一套约定)。
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

    def pump(self, rounds=4):
        for _ in range(rounds):
            jobs, self.jobs = self.jobs, []
            if not jobs:
                time.sleep(0.01)
                continue
            for _sid, fn in jobs:
                try:
                    fn()
                except Exception:
                    pass


def _pump_until(pred, root, timeout=8.0):
    """驱动假事件循环直到 pred() 为真或超时。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        root.pump(1)
        time.sleep(0.01)
    return bool(pred())


bt.shutdown()
root = FakeRoot()
bt.start(root, poll_ms=10)

print("── 1. CancelToken: 取消后回调不再执行 ──")
tok = bt.CancelToken()
fired = []


def _slow():
    time.sleep(0.05)
    return "payload-cancelled"


bt.submit(_slow, lambda v: fired.append(v), None, name="cancel-me", token=tok)
tok.cancel()
_ok = _pump_until(lambda: bt.pending() == 0, root, 5.0)
check("1a. 取消后 pending 归零 (不泄漏计数)", _ok and bt.pending() == 0, bt.pending())
check("1b. 取消后 on_done 未被调用", fired == [], fired)
check("1c. is_cancelled() 为真", tok.is_cancelled() is True)

tok2 = bt.CancelToken()
fired2 = []
bt.submit(lambda: "payload-ok", lambda v: fired2.append(v), None,
          name="not-cancelled", token=tok2)
_ok = _pump_until(lambda: fired2 == ["payload-ok"], root, 5.0)
check("1d. 未取消 → on_done 正常执行 (对照组)", _ok and fired2 == ["payload-ok"], fired2)

print("── 2. submit_chunked: 分块回主线程且顺序正确 ──")
items = list(range(1000))
chunks = []
_ret = bt.submit_chunked(items, lambda c: list(c), lambda p: chunks.append(p),
                         chunk_size=200, name="chunked")
check("2a. 返回 True 表示异步提交", _ret is True, _ret)
_pump_until(lambda: len(chunks) >= 5 and bt.pending() == 0, root, 10.0)
check("2b. 1000 项按 200 切片 = 5 块", len(chunks) == 5, len(chunks))
_flat = [x for p in chunks for x in p]
check("2c. 分块拼接后与原序列完全一致 (顺序正确)", _flat == items, _flat[:10])
check("2d. 每块长度 ≤ chunk_size", all(len(p) <= 200 for p in chunks),
      [len(p) for p in chunks])

print("── 3. 有界队列背压: low 队列满时不阻塞主线程 ──")
# 主线程故意不 drain, 让 300 个 low 结果把有界队列 (256) 压满
worst_ms = 0.0
for i in range(300):
    _t0 = time.perf_counter()
    bt.submit((lambda n: (lambda: n))(i), (lambda v: None), None,
              name="low{}".format(i), priority=bt.PRIO_LOW)
    _dt = (time.perf_counter() - _t0) * 1000.0
    if _dt > worst_ms:
        worst_ms = _dt
time.sleep(0.5)      # 给 4 个 IO worker 时间把结果全部入队
check("3a. 单次 submit 主线程耗时 ≤50ms", worst_ms <= 50.0, round(worst_ms, 2))
check("3b. low 队列长度不超过 256", bt.pending_len() <= 256, bt.pending_len())
check("3c. 背压生效 (发生丢弃计数)", bt.dropped() > 0, bt.dropped())

# 收尾: drain 干净 + shutdown 不残留
_pump_until(lambda: bt.pending() == 0, root, 10.0)
bt.shutdown()
check("3d. shutdown 后 pending 归零", bt.pending() == 0, bt.pending())
check("3e. shutdown 后 is_started() 为 False", bt.is_started() is False)

print("\n结果: PASS={} FAIL={}".format(PASS, FAIL))
sys.exit(1 if FAIL else 0)
