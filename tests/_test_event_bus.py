#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""core.event_bus 单元测试 (方案文档 S3-a)。

覆盖: import 零副作用 / 订阅发布退订 / 重复订阅幂等 / 异常隔离 /
      无订阅者 no-op / 快照隔离 (派发中退订不影响本轮) / 线程并发 /
      清空 / 协议 (publish 返回调用数, subscribe 返回 fn)。
"""
import os
import sys
import threading

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from core.event_bus import EventBus

_PASS, _FAIL = [], []


def check(cond, msg, detail=""):
    (_PASS if cond else _FAIL).append(msg)
    print("[{}] {}{}".format("OK  " if cond else "FAIL", msg,
                             ("  ({})".format(detail) if detail else "")))


def main():
    print("=== core.event_bus 单元测试 (S3-a) ===")

    # import 零副作用: 单例 bus 初始为空
    from core.event_bus import bus
    check(isinstance(bus, EventBus), "单例 bus 为 EventBus 实例")
    check(not bus.has("t1"), "import 后总线为空 (零副作用)")

    b = EventBus()
    got = []

    # 订阅/发布
    b.subscribe("t1", lambda **kw: got.append(("a", kw)))
    n = b.publish("t1", x=1)
    check(n == 1 and got == [("a", {"x": 1})], "订阅后发布被调用", "n={}, got={}".format(n, got))

    # 多订阅者顺序确定
    b2 = EventBus()
    order = []
    b2.subscribe("s", lambda **k: order.append(1))
    b2.subscribe("s", lambda **k: order.append(2))
    b2.subscribe("s", lambda **k: order.append(3))
    b2.publish("s")
    check(order == [1, 2, 3], "多订阅者按订阅顺序派发", "order={}".format(order))

    # 重复订阅幂等
    b3 = EventBus()
    fn = lambda **k: None
    b3.subscribe("t", fn)
    b3.subscribe("t", fn)
    check(len(b3.subscribers("t")) == 1, "重复订阅幂等 (仅登记一次)")

    # 退订
    b3.unsubscribe("t", fn)
    check(len(b3.subscribers("t")) == 0, "退订后订阅者移除")
    b3.unsubscribe("t", fn)  # 再次退订应静默
    check(len(b3.subscribers("t")) == 0, "重复退订静默")

    # 异常隔离: 一个订阅者抛错不影响其余
    b4 = EventBus()
    hits = []
    def boom(**k):
        raise RuntimeError("boom")
    b4.subscribe("e", boom)
    b4.subscribe("e", lambda **k: hits.append(1))
    n = b4.publish("e")
    check(n == 1 and hits == [1], "订阅者异常被隔离, 其余仍被调用", "n={}, hits={}".format(n, hits))

    # 无订阅者 no-op
    check(b4.publish("nope") == 0, "无订阅者 publish 返回 0")

    # 快照隔离: 派发中退订不影响本轮
    b5 = EventBus()
    seen = []
    def a(**k):
        seen.append("a")
        b5.unsubscribe("t", bb)
    def bb(**k):
        seen.append("b")
    b5.subscribe("t", a)
    b5.subscribe("t", bb)
    b5.publish("t")
    check(seen == ["a", "b"], "派发中退订不影响本轮快照", "seen={}".format(seen))
    check(len(b5.subscribers("t")) == 1, "退订在派发后生效", "left={}".format(len(b5.subscribers("t"))))

    # subscribe 返回 fn (装饰器用法)
    b6 = EventBus()
    @b6.subscribe("dec")
    def _dec(**k):
        return None
    check(_dec in b6.subscribers("dec"), "subscribe 返回 fn 支持装饰器")

    # 线程并发: 4 读发布 + 4 写订阅, 校验无异常且派发计数一致
    b8 = EventBus()
    counter = {"n": 0}
    b8.subscribe("cc", lambda **k: counter.__setitem__("n", counter["n"] + 1))
    def reader2():
        for _ in range(500):
            b8.publish("cc")
    def writer2():
        for _ in range(500):
            b8.subscribe("cc", lambda **k: counter.__setitem__("n", counter["n"] + 1))
    ts = [threading.Thread(target=reader2) for _ in range(4)] + \
         [threading.Thread(target=writer2) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    check(counter["n"] >= 2000, "4 读 4 写并发无异常且派发计数 ≥ 2000", "n={}".format(counter["n"]))

    # 清空
    b8.clear()
    check(len(b8.subscribers("cc")) == 0, "clear 清空全部订阅")

    t_real_machine()

    print("\n=== 结果: {} (通过 {}, 失败 {}) ===".format(
        "FAIL" if _FAIL else "OK", len(_PASS), len(_FAIL)))
    return 1 if _FAIL else 0


def t_real_machine():
    """实机 (真实 Tk) 断言: 宿主 publish → 视图模块订阅接线 (S3-b/c)。"""
    print("\n── S3 实机: 事件总线接线 (exec_state / wf_highlight) ──")
    try:
        import tkinter
        probe = tkinter.Tk()
        probe.destroy()
    except Exception:
        print("[WARN] 无 GUI 环境, 跳过实机断言")
        return
    try:
        import app
        import ACRPA
        from core.event_bus import bus
    except Exception as e:
        print("[WARN] import 失败, 跳过实机断言: {}".format(e))
        return
    rt = None
    try:
        rt = app.build()
        import ui.exec_bar as eb
        import ui.workflow_view as wv

        # exec_state: 构建即订阅 (执行控制栏常驻)
        check(bus.subscribers("exec_state") == [eb.set_state],
              "exec_bar 订阅 exec_state (set_state)")
        # wf_highlight: 未构建工作流 Tab 前无订阅者
        check(not bus.has("wf_highlight"),
              "工作流 Tab 未构建时无 wf_highlight 订阅者")
        ACRPA._wf_ensure()
        check(bus.subscribers("wf_highlight") == [wv._wf_update_workflow_highlight],
              "workflow_view 懒构建后订阅 wf_highlight")

        # 派发可触发 (set_state 会改暂停键文案; 只断言调用计数, 行为已由 split 测试覆盖)
        check(bus.publish("exec_state", code=2) == 1, "publish exec_state 触发 1 个订阅者")
        check(bus.publish("wf_highlight") == 1, "publish wf_highlight 触发 1 个订阅者")

        # 无订阅者 topic no-op (未来新增视图可安全 publish)
        check(bus.publish("unused_topic") == 0, "未订阅 topic publish 返回 0")
    except Exception as e:
        check(False, "实机接线断言异常: {}".format(e))
    finally:
        try:
            if rt is not None:
                rt.root.destroy()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
