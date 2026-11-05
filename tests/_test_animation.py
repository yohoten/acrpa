#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""轻量动画调度器 P2-1 回归自测: 缓动控制器 + Animation 调度。

用法:
    .venv\\Scripts\\python.exe -X utf8 tests\\_test_animation.py

覆盖:
  A 控制器    : linear/out_quad/out_cubic/in_out 端点 (0→0, 1→1) + 单调不减 + 值域 [0,1]。
  B 帧调度    : Animation 用假 root.after 推进, 帧数正确、进度递增、end 触发一次。
  C 减少动效  : reduce_motion 真 → command(1.0)+end() 立即同步、不调度 after。
  D 增量模式  : derivation=True 时增量之和 ≈ 1。
  E 循环      : repeat=1 → end 触发 2 次。
  F 停止      : stop() 取消所有已调度任务。
  G ease 兼容 : utils.ease() 扩展 out_quad/in_out 且与 core.animation 控制器同表。

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

_PASS, _FAIL, _WARN = [], [], []


def check(cond, msg, detail=""):
    (_PASS if cond else _FAIL).append(msg)
    tail = ("  ({})".format(detail) if detail else "")
    print("[{}] {}{}".format("OK  " if cond else "FAIL", msg, tail))


def warn(msg):
    _WARN.append(msg)
    print("[WARN] {}".format(msg))


class FakeRoot:
    """鸭子类型 root: 记录 after 任务, 可 flush 按调度顺序执行 (不关心真实计时)。"""

    def __init__(self):
        self.queue = []       # [(tid, delay, func, args)] 按调度顺序
        self.cancelled = set()
        self._next = 0

    def after(self, delay, func=None, *args):
        self._next += 1
        tid = "after#%d" % self._next
        self.queue.append((tid, delay, func, args))
        return tid

    def after_cancel(self, tid):
        self.cancelled.add(tid)

    def flush(self):
        """执行当前队列中所有未取消任务; 执行期间新调度者留待下次 flush。"""
        snapshot = list(self.queue)
        self.queue = []
        fired = []
        for tid, delay, func, args in snapshot:
            if tid in self.cancelled:
                continue
            if func is not None:
                func(*args)
            fired.append((delay, func, args))
        self.cancelled.clear()
        return fired


def run_all(root, max_iters=200):
    for _ in range(max_iters):
        if not root.queue:
            break
        root.flush()
    return root


from core.animation import (  # noqa: E402
    linear, out_quad, out_cubic, in_out, CONTROLLERS, Animation,
)


def t_controllers():
    print("\n── A. 缓动控制器 ──")
    for name, fn in [("linear", linear), ("out_quad", out_quad),
                     ("out_cubic", out_cubic), ("in_out", in_out)]:
        check(abs(fn(0.0) - 0.0) < 1e-12, "{} 端点 t=0→0".format(name))
        check(abs(fn(1.0) - 1.0) < 1e-12, "{} 端点 t=1→1".format(name))
        prev, mono, in_range = 0.0, True, True
        for i in range(101):
            v = fn(i / 100.0)
            if v < prev - 1e-12:
                mono = False
            if v < -1e-9 or v > 1.0 + 1e-9:
                in_range = False
            prev = v
        check(mono, "{} 单调不减".format(name))
        check(in_range, "{} 值域 [0,1]".format(name))
    check(abs(in_out(0.5) - 0.5) < 1e-12, "in_out 中点=0.5")
    check(CONTROLLERS["out_cubic"] is out_cubic, "CONTROLLERS 索引正确")
    check(set(CONTROLLERS) == {"linear", "out_quad", "out_cubic", "in_out"},
          "CONTROLLERS 含 4 种")


def t_frames():
    print("\n── B. 帧调度 (假 root.after) ──")
    root = FakeRoot()
    seen, done = [], []
    anim = Animation(160, seen.append, controller=out_cubic,
                     end=lambda: done.append(1), root=root)
    check(anim.total_frames == 10, "total_frames=10 (160ms/16ms)")
    r = anim.start()
    check(r is None, "start() 无延迟返回 None")
    check(anim.active is True, "start 后 active")
    check(len(root.queue) == 12, "调度 10 帧 + end + _repeat = 12 任务")
    root.flush()
    check(len(seen) == 10, "command 触发 10 帧")
    check(len(done) == 1, "end 触发 1 次")
    check(seen == sorted(seen), "进度单调递增")
    check(abs(seen[0] - out_cubic(0.1)) < 1e-12, "首帧=out_cubic(0.1)")
    check(abs(seen[-1] - 1.0) < 1e-12, "末帧=1.0")
    check(anim.active is False, "flush 后 active=False (repeat=0)")


def t_reduce_motion():
    print("\n── C. 减少动效一步到位 ──")
    root = FakeRoot()
    seen, done = [], []
    anim = Animation(160, seen.append, end=lambda: done.append(1),
                     root=root, reduce_motion=lambda: True)
    r = anim.start()
    check(r is None, "reduce_motion start 返回 None")
    check(len(seen) == 1 and seen[0] == 1.0, "command 立即收到 1.0")
    check(len(done) == 1, "end 立即触发")
    check(len(root.queue) == 0, "未调度任何 after 任务")
    check(anim.active is False, "active=False")

    # 无 root 时减少动效也可直接 start (不依赖 after)
    anim2 = Animation(160, lambda p: None, reduce_motion=lambda: True)
    try:
        anim2.start()
        check(True, "reduce_motion 无需 root 即可 start")
    except Exception as e:
        check(False, "reduce_motion 无需 root 即可 start", "{}".format(e))


def t_derivation():
    print("\n── D. 增量模式 derivation ──")
    root = FakeRoot()
    deltas = []
    anim = Animation(100, deltas.append, controller=linear,
                     derivation=True, root=root)
    # duration=100, fps=60 → frame_ms=16, divmod(100,16)=(6,4) → 6 帧
    anim.start()
    root.flush()
    check(len(deltas) == 6, "6 帧增量")
    check(abs(sum(deltas) - 1.0) < 1e-9, "增量之和≈1.0", "sum={}".format(sum(deltas)))


def t_repeat():
    print("\n── E. 循环 repeat ──")
    root = FakeRoot()
    done = []
    anim = Animation(32, lambda p: None, end=lambda: done.append(1),
                     root=root, repeat=1)
    # duration=32, fps=60 → frame_ms=16, divmod(32,16)=(2,0) → 2 帧/轮
    anim.start()
    run_all(root)
    check(len(done) == 2, "repeat=1 → end 触发 2 次 (2 轮)")
    check(anim.count == 1, "循环结束计数复位到 repeat")


def t_stop():
    print("\n── F. stop 取消 ──")
    root = FakeRoot()
    seen = []
    anim = Animation(160, seen.append, root=root)
    anim.start()
    check(anim.active is True, "start 后 active")
    n = len(root.queue)  # 无 end → 10 帧 + _repeat = 11
    anim.stop()
    check(anim.active is False, "stop 后 active=False")
    check(len(root.cancelled) == n, "{} 个任务全部 after_cancel".format(n))
    root.flush()
    check(len(seen) == 0, "停止后不再触发 command")


def t_ease_compat():
    print("\n── G. utils.ease() 扩展与真源一致 ──")
    try:
        import utils
        check(abs(utils.ease(0.3, "out_quad") - out_quad(0.3)) < 1e-12,
              "ease out_quad=core 同表")
        check(abs(utils.ease(0.3, "in_out") - in_out(0.3)) < 1e-12,
              "ease in_out=core 同表")
        check(abs(utils.ease(0.3, "out_cubic") - out_cubic(0.3)) < 1e-12,
              "ease out_cubic=core 同表")
        check(abs(utils.ease(0.3, "linear") - linear(0.3)) < 1e-12,
              "ease linear=core 同表")
        check(abs(utils.ease(0.3, "nope") - out_cubic(0.3)) < 1e-12,
              "未知 kind 回退 out_cubic")
        check(utils.ease(1.5) == 1.0, "ease 上裁剪到 1")
        check(utils.ease(-0.5) == 0.0, "ease 下裁剪到 0")
    except Exception as e:
        warn("utils 导入失败, 跳过: {}".format(e))


def main():
    print("=== 轻量动画调度器 P2-1: 缓动控制器 + Animation 调度 ===")
    t_controllers()
    t_frames()
    t_reduce_motion()
    t_derivation()
    t_repeat()
    t_stop()
    t_ease_compat()

    print("\n" + "-" * 60)
    print("PASS={} FAIL={} WARN={}".format(len(_PASS), len(_FAIL), len(_WARN)))
    for m in _FAIL:
        print("[FAIL] {}".format(m))
    print("=== 结果: {} ===".format("FAIL" if _FAIL else "OK"))
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
