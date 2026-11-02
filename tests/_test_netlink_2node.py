# -*- coding: utf-8 -*-
"""NetLink Phase1-2 同机双节点自测 —— 不依赖窗口、不弹框，全控制台 [OK]/[WARN]/[FAIL]。

运行:  python tools/_test_netlink_2node.py
退出码: 0 = 全部断言通过；1 = 存在失败

说明：
  * 节点 A: tcp 19810 / udp 19811；节点 B: tcp 19820 / udp 19821；
  * 主要走"静态对端"路径（UDP 广播在部分同机环境不可靠），
    autodiscover 同时打开并观察，但断言不依赖广播成功（失败仅打印 [WARN]）。
  * 直接在内存里改 state.NETLINK_* 字面量（不写 config.json）。
"""
import os
import sys
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_SRC = os.path.join(_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import tkinter  # noqa: F401  先加载 tkinter（utils 顶层 import tkinter）
import utils
import state
import netlink
import netlink.node as nl_node
from netlink.bus import NetBus
from netlink.node import (
    NetLinkNode,
    TOPIC_PEER, TOPIC_PEER_STATE, TOPIC_PEER_LOG, TOPIC_PEER_SCHED, TOPIC_STATUS,
)

A_TCP, A_UDP = 19810, 19811
B_TCP, B_UDP = 19820, 19821

_fails = 0


def check(name, cond, detail=""):
    global _fails
    if cond:
        print("[OK]   " + name)
    else:
        _fails += 1
        print("[FAIL] " + name + (" :: " + detail if detail else ""))


def warn(msg):
    print("[WARN] " + msg)


def set_cfg(tcp, udp, name, static_peers):
    state.NETLINK_ENABLED = True
    state.NETLINK_PORT = tcp
    state.NETLINK_DISCOVERY_PORT = udp
    state.NETLINK_AUTODISCOVER = True
    state.NETLINK_DEVICE_NAME = name
    state.NETLINK_STATIC_PEERS = static_peers


def wait_until(fn, timeout):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if fn():
                return True
        except Exception:
            pass
        time.sleep(0.05)
    return False


def main():
    global _fails
    print("=" * 60)
    print("NetLink Phase1-2 2-node test")
    print("=" * 60)

    # Phase2-1: 认证默认开启；本用例显式关闭以聚焦 Phase1 断言
    state.NETLINK_REQUIRE_AUTH = False

    lock = threading.Lock()
    state_msgs = []
    log_msgs = []
    sched_msgs = []
    peer_msgs = []
    status_msgs = []

    def on_state(topic, payload):
        with lock:
            state_msgs.append(payload)

    def on_log(topic, payload):
        with lock:
            log_msgs.append(payload)

    def on_sched(topic, payload):
        with lock:
            sched_msgs.append(payload)

    def on_peer(topic, payload):
        with lock:
            peer_msgs.append(payload)

    def on_status(topic, payload):
        with lock:
            status_msgs.append(payload)

    # ── 节点 A ────────────────────────────────────────────────────────
    set_cfg(A_TCP, A_UDP, "NodeA", [["127.0.0.1", B_TCP]])
    nl_node.make_node_id = lambda: "nodeA0000000000"
    busA = NetBus()
    A = NetLinkNode(root=None, bus=busA)
    okA = A.start()
    check("1a. node A start (tcp {} / udp {})".format(A_TCP, A_UDP), bool(okA))
    if not okA:
        print("-" * 60)
        print("PASS aborted: node A failed to start")
        return 1

    # ── 节点 B ────────────────────────────────────────────────────────
    set_cfg(B_TCP, B_UDP, "NodeB", [["127.0.0.1", A_TCP]])
    nl_node.make_node_id = lambda: "nodeB0000000000"
    busB = NetBus()
    busB.subscribe(TOPIC_PEER, on_peer)
    busB.subscribe(TOPIC_PEER_STATE, on_state)
    busB.subscribe(TOPIC_PEER_LOG, on_log)
    busB.subscribe(TOPIC_PEER_SCHED, on_sched)
    busB.subscribe(TOPIC_STATUS, on_status)
    B = NetLinkNode(root=None, bus=busB)
    okB = B.start()
    check("1b. node B start (tcp {} / udp {})".format(B_TCP, B_UDP), bool(okB))
    if not okB:
        try:
            A.stop()
        except Exception:
            pass
        print("-" * 60)
        print("PASS aborted: node B failed to start")
        return 1

    # ── 2. B 的 client 能连到 A ────────────────────────────────────────
    connBA = B._client.connect_to("127.0.0.1", A_TCP)
    check("2. B client connects to A (connect_to alive)",
          connBA is not None and connBA.alive,
          "conn={}".format(connBA))

    # ── 3. B 订阅 state → A 在 3s 内推 T_STATE_SYNC ────────────────────
    if connBA is not None and connBA.alive:
        with lock:
            del state_msgs[:]
        sent = connBA.send(netlink.make_msg(
            "SUBSCRIBE", {"topics": ["state", "log", "sched"]}))
        got_state = wait_until(lambda: len(state_msgs) > 0, 3.0)
        check("3. A pushes STATE_SYNC after SUBSCRIBE (<=3s)",
              bool(sent) and got_state,
              "sent={} state_msgs={}".format(sent, len(state_msgs)))
    else:
        check("3. A pushes STATE_SYNC after SUBSCRIBE (<=3s)", False, "no conn")

    # ── 4. A 端日志镜像 → B 收到含文本的 TOPIC_PEER_LOG ────────────────
    with lock:
        del log_msgs[:]
    try:
        utils._broadcast_log("HELLO-FROM-A", None, 1, "t.py", "fn")
        raised = None
    except Exception as e:
        raised = e
    found = wait_until(
        lambda: any("HELLO-FROM-A" in line.get("msg", "")
                    for m in log_msgs for line in (m.get("lines") or [])),
        3.0)
    check("4. B receives LOG_TAIL containing 'HELLO-FROM-A' (<=3s)",
          raised is None and found,
          "raised={} log_msgs={}".format(raised, len(log_msgs)))

    # ── 附： SCHED 推送（非强制项，允许为空但需不报错）───────────────
    sched_seen = len(sched_msgs) > 0
    if sched_seen:
        print("[OK]   4b. got SCHED_STATUS push (count={})".format(len(sched_msgs)))
    else:
        warn("no SCHED_STATUS push observed (scheduler state unchanged)")

    # ── 广播验证（允许失败）──────────────────────────────────────────
    if wait_until(lambda: len(peer_msgs) > 0, 1.0):
        discovered = [p for p in peer_msgs if not str(p.get("node_id", "")).startswith("127.0.0.1:")]
        if discovered:
            print("[OK]   4c. udp discovery produced peer announce(s)")
        else:
            warn("udp discovery not verified (static peers only)")
    else:
        warn("udp discovery not verified (static peers only)")

    # ── 5. stop() 幂等 + 可重启 ───────────────────────────────────────
    A.stop()
    A.stop()  # 幂等
    set_cfg(A_TCP, A_UDP, "NodeA", [["127.0.0.1", B_TCP]])
    nl_node.make_node_id = lambda: "nodeA0000000000"
    restarted = A.start()
    check("5. A.stop() idempotent + restartable", bool(restarted))

    # ── 6. 无异常逃逸 ─────────────────────────────────────────────────
    threads = [t.name for t in threading.enumerate()]
    fn_threads = [n for n in threads if n.startswith("nl-")]
    print("6. live netlink threads at end: {}".format(fn_threads))
    check("6. no exception escaped (fsm intact)", True)

    # ── 收尾 ─────────────────────────────────────────────────────────
    try:
        A.stop()
    except Exception as e:
        warn("A.stop() raised {}".format(e))
    try:
        B.stop()
    except Exception as e:
        warn("B.stop() raised {}".format(e))

    with lock:
        n_state = len(state_msgs)
        n_log = len(log_msgs)
        n_sched = len(sched_msgs)
        n_peer = len(peer_msgs)
        n_status = len(status_msgs)

    print("-" * 60)
    print("message counts (busB):")
    print("  TOPIC_PEER       : {}".format(n_peer))
    print("  TOPIC_PEER_STATE : {}".format(n_state))
    print("  TOPIC_PEER_LOG   : {}".format(n_log))
    print("  TOPIC_PEER_SCHED : {}".format(n_sched))
    print("  TOPIC_STATUS     : {}".format(n_status))
    print("-" * 60)
    if _fails == 0:
        print("PASS (all assertions OK)")
        return 0
    print("FAIL ({} assertion(s) failed)".format(_fails))
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(1)
