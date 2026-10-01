# -*- coding: utf-8 -*-
"""NetLink Phase1-1 冒烟自测 —— 不弹窗、不创建 Tk 窗口，全控制台 [OK]/[FAIL]。

运行:  python tools/_smoke_netlink.py
退出码: 0 = 全部通过；1 = 存在失败
"""
import os
import sys
import threading

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_SRC = os.path.join(_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

_results = []


def run(name, fn):
    """执行单个测试项，打印 [OK]/[FAIL]；返回 False 或抛异常均视为失败。"""
    try:
        ok = fn()
        if ok is False:
            raise AssertionError("returned False")
        _results.append((name, True))
        print("[OK]   " + name)
    except Exception as e:
        _results.append((name, False))
        print("[FAIL] " + name + " :: " + repr(e))


# ── 1. import netlink ──
def test_import():
    import netlink
    return netlink.PROTOCOL_VERSION == 1


# ── 2. 帧编解码（分多次、每次 1~5 字节喂入）──
def test_frame_roundtrip():
    import netlink
    framed = netlink.frame_message(netlink.make_msg("PING", {"a": 1}))
    r = netlink.FrameReader()
    got = []
    sizes = [1, 2, 3, 4, 5]
    i = 0
    k = 0
    while i < len(framed):
        n = sizes[k % len(sizes)]
        k += 1
        got.extend(r.feed(framed[i:i + n]))
        i += n
    if len(got) != 1:
        raise AssertionError("expected 1 payload, got {}".format(len(got)))
    msg = netlink.parse_msg(got[0])
    return msg["t"] == "PING" and msg["data"]["a"] == 1


# ── 3. 粘包：3 条帧一次 feed ──
def test_sticky():
    import netlink
    frames = b"".join(
        netlink.frame_message(netlink.make_msg("PING", {"i": i}))
        for i in range(3)
    )
    r = netlink.FrameReader()
    got = r.feed(frames)
    if len(got) != 3:
        raise AssertionError("expected 3 payloads, got {}".format(len(got)))
    return netlink.parse_msg(got[2])["data"]["i"] == 2


# ── 4. 异常帧：超限长度 / 零长度 → FrameError ──
def test_bad_frames():
    import struct
    from netlink.protocol import FrameError, FrameReader

    r = FrameReader()
    try:
        r.feed(struct.pack(">I", 99999999))
        raise AssertionError("oversize length did not raise FrameError")
    except FrameError:
        pass

    r2 = FrameReader()
    try:
        r2.feed(struct.pack(">I", 0))
        raise AssertionError("zero length did not raise FrameError")
    except FrameError:
        pass

    return True


# ── 5. 本地回环 Connection：互发 200 条 + 幂等 close ──
def test_loopback():
    import socket
    from netlink.connection import Connection

    a_sock, b_sock = socket.socketpair()
    n = 200
    recv_a = []
    recv_b = []
    ev_a = threading.Event()
    ev_b = threading.Event()
    lock = threading.Lock()

    def on_a(msg):
        with lock:
            recv_a.append(msg)
            if len(recv_a) >= n:
                ev_a.set()

    def on_b(msg):
        with lock:
            recv_b.append(msg)
            if len(recv_b) >= n:
                ev_b.set()

    ca = Connection(a_sock, ("local", 0), on_message=on_a, name="A")
    cb = Connection(b_sock, ("local", 0), on_message=on_b, name="B")
    ca.start()
    cb.start()
    for i in range(n):
        ca.send({"v": 1, "t": "PING", "id": "a{}".format(i), "ts": 1, "data": {"i": i}})
        cb.send({"v": 1, "t": "PING", "id": "b{}".format(i), "ts": 1, "data": {"i": i}})
    ev_a.wait(5.0)
    ev_b.wait(5.0)

    ok = len(recv_a) >= n and len(recv_b) >= n
    # close 可重复调用且不抛
    ca.close()
    ca.close()
    cb.close()
    cb.close()
    # 关闭后 send 必须立即返回 False（不抛 AttributeError）
    if ca.send({"v": 1, "t": "PING", "id": "x", "ts": 1, "data": {}}) is not False:
        raise AssertionError("send after close should return False")
    if not ok:
        raise AssertionError("loopback recv a={} b={}".format(len(recv_a), len(recv_b)))
    return True


# ── 6. NetBus：post/drain、异常回调隔离、unsubscribe ──
def test_bus():
    from netlink.bus import NetBus

    b = NetBus()
    b.post("state", {"x": 1})
    b.post("log", {"y": 2})
    b.post("sched", {"z": 3})
    items = b.drain()
    if len(items) != 3:
        raise AssertionError("drain expected 3, got {}".format(len(items)))

    good = []

    def bad(topic, payload):
        raise RuntimeError("boom")

    def good_fn(topic, payload):
        good.append((topic, payload))

    b.subscribe("state", bad)
    b.subscribe("state", good_fn)
    b.subscribe("state", good_fn)   # 重复订阅只登记一次
    b.publish("state", {"v": 9})
    if len(good) != 1:
        raise AssertionError("dup-subscribe/exception isolation failed: {}".format(len(good)))
    if len(b.drain()) != 1:         # publish 内部 post 一份
        raise AssertionError("publish should also enqueue one event")

    b.unsubscribe("state", good_fn)
    b.publish("state", {"v": 10})
    if len(good) != 1:
        raise AssertionError("unsubscribe failed")
    return True


# ── 7. 日志 sink：register / _broadcast_log / unregister ──
def test_log_sink():
    import tkinter  # noqa: F401 —— utils 顶层 import tkinter，先加载避免顺序问题
    import utils

    seen = []

    def spy(msg, tag, level, caller_file, caller_func):
        seen.append((msg, tag, level, caller_file, caller_func))

    utils.register_log_sink(spy)
    utils.register_log_sink(spy)   # 重复注册只登记一次
    utils._broadcast_log("hi", None, 1, "f.py", "fn")
    if len(seen) != 1 or seen[0] != ("hi", None, 1, "f.py", "fn"):
        raise AssertionError("sink args mismatch: {}".format(seen))

    utils.unregister_log_sink(spy)
    utils._broadcast_log("hi2", None, 1, "f.py", "fn")
    if len(seen) != 1:
        raise AssertionError("unregister failed")
    return True


# ── 8. state 配置键与默认值 ──
def test_state():
    import state
    if state.config.netlink_port != 19710:
        raise AssertionError("netlink_port != 19710: {}".format(state.config.netlink_port))
    if state.config.netlink_discovery_port != 19711:
        raise AssertionError("netlink_discovery_port != 19711")
    keys = dict((k, k) for k, _, _ in state._config_schema)
    if "netlink_enabled" not in keys:
        raise AssertionError("netlink_enabled missing from schema")
    if "netlink_static_peers" not in keys:
        raise AssertionError("netlink_static_peers missing from schema")
    return True


def main():
    # Phase2-1: 认证默认开启；本用例显式关闭以聚焦 Phase1 断言
    try:
        import state
        state.NETLINK_REQUIRE_AUTH = False
    except Exception:
        pass
    run("1. import netlink", test_import)
    run("2. frame roundtrip (1~5 bytes chunked)", test_frame_roundtrip)
    run("3. sticky packet (3 frames in one feed)", test_sticky)
    run("4. bad frames raise FrameError", test_bad_frames)
    run("5. loopback Connection x200 + idempotent close", test_loopback)
    run("6. NetBus post/subscribe/publish/drain", test_bus)
    run("7. log sink register/broadcast/unregister", test_log_sink)
    run("8. state netlink config", test_state)

    passed = sum(1 for _, ok in _results if ok)
    total = len(_results)
    print("-" * 52)
    print("PASS {}/{}".format(passed, total))
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
