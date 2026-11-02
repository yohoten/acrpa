# -*- coding: utf-8 -*-
"""NetLink Phase1-4 端到端验收 —— 同机双节点，走静态对端链路，全自动。

运行:  python tools/_test_netlink_e2e.py
退出码: 0 = 全部断言通过（允许的 [WARN] 不计失败）；1 = 存在 [FAIL]

硬约束（与 Phase1-4 任务一致）:
  * 不弹窗、不阻塞；任何等待循环均带 deadline；总时长 < 60s；
  * 不写 config.json（只改 state.* 内存字面量）；
  * 端口用 199xx 段，用完即释放；
  * 输出 [OK]/[WARN]/[FAIL]。

覆盖 10 项断言:
  1. 状态链路真实性（row/total_rows/loop/script）
  2. 暂停态上报（paused True/False）
  3. 日志链路（真走 utils.log1 + ThreadSafeLog，含退化兜底）
  4. SUBSCRIBE 后立即推送（<2s）
  5. 断线重连（<=6s）且重订阅后再次收到 STATE
  6. CMD_* 被拒绝（CMD_ERR + reason 非空 + 不改 state.quit2）
  7. 静态对端两种写法（"host:port" 字符串 / ["host", port] 序列）
  8. 端口占用降级（start() 返回 False，不逃逸异常、不拖死其它线程）
  9. 幂等 stop + 可重启
 10. 资源泄漏检查（nl-* 线程收敛，允许 WARN）
"""
import os
import socket
import sys
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_SRC = os.path.join(_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import tkinter  # noqa: F401  —— utils 顶层 import tkinter，提前加载
import state
import utils

# ── 端口：A/B 主链路；C1/C2 静态对端两种写法；OCC 端口占用 ──
A_TCP, A_UDP = 19910, 19911
B_TCP, B_UDP = 19920, 19921
C1_TCP, C1_UDP = 19940, 19941
C2_TCP, C2_UDP = 19950, 19951
OCC_TCP, OCC_UDP = 19930, 19931

TOTAL_BUDGET = 60.0

_fails = 0
_warns = 0
T0 = time.time()


def check(name, cond, detail=""):
    """[OK]/[FAIL] 打印 + 计数。"""
    global _fails
    if cond:
        print("[OK]   " + name)
        return True
    _fails += 1
    print("[FAIL] " + name + (" :: " + detail if detail else ""))
    return False


def warn(msg):
    global _warns
    _warns += 1
    print("[WARN] " + msg)


def wait_until(fn, timeout):
    """带 deadline 的轮询等待；fn() 抛异常视为 False。"""
    dl = time.time() + timeout
    while time.time() < dl:
        try:
            if fn():
                return True
        except Exception:
            pass
        time.sleep(0.05)
    return False


def set_cfg(tcp, udp, name, static_peers, autodiscover=True):
    """只改内存字面量，绝不落盘 config.json。"""
    state.NETLINK_ENABLED = True
    state.NETLINK_PORT = tcp
    state.NETLINK_DISCOVERY_PORT = udp
    state.NETLINK_AUTODISCOVER = bool(autodiscover)
    state.NETLINK_DEVICE_NAME = name
    state.NETLINK_STATIC_PEERS = static_peers


class _CaptureConn(object):
    """桩连接：仅用于在 A 侧直接 node.handle_message，捕获回发报文。"""

    def __init__(self):
        self.sent = []
        self.peer_addr = ("127.0.0.1", 0)
        self.name = "capture"

    def send(self, msg_dict):
        try:
            self.sent.append(msg_dict)
        except Exception:
            pass
        return True


def _nl_thread_names():
    try:
        return sorted(t.name for t in threading.enumerate()
                      if t.name and t.name.startswith("nl-"))
    except Exception:
        return []


def main():
    global _fails
    print("=" * 64)
    print("NetLink Phase1-4 E2E acceptance (same-host dual node)")
    print("=" * 64)

    # Phase2-1: 认证默认开启；本用例显式关闭以聚焦 Phase1 断言
    state.NETLINK_REQUIRE_AUTH = False

    # ── Tk root（供 ThreadSafeLog 真链路使用，withdraw 不弹窗）──
    root = None
    try:
        root = tkinter.Tk()
        root.withdraw()
    except Exception as e:
        print("[WARN] no display ({}); log real-chain will fall back".format(e))
        root = None

    import netlink
    import netlink.node as nl_node
    from netlink.bus import NetBus
    from netlink.node import NetLinkNode, TOPIC_PEER_STATE, TOPIC_PEER_LOG
    from netlink.protocol import T_CMD_STOP, T_CMD_ERR

    lock = threading.Lock()
    state_msgs = []
    log_msgs = []

    def on_state(topic, payload):
        with lock:
            state_msgs.append(dict(payload))

    def on_log(topic, payload):
        with lock:
            log_msgs.append(payload)

    def snap(lst):
        with lock:
            return list(lst)

    def clear(lst):
        with lock:
            del lst[:]

    A = B = None
    try:
        # ══════════════════════════════════════════════════════════════
        # 启动 A (19910/19911) 与 B (19920/19921)，走静态对端
        # ══════════════════════════════════════════════════════════════
        set_cfg(A_TCP, A_UDP, "E2E-A", [["127.0.0.1", B_TCP]])
        nl_node.make_node_id = lambda: "e2ea000000000000"
        A = NetLinkNode(root=None, bus=NetBus())
        okA = A.start()

        set_cfg(B_TCP, B_UDP, "E2E-B", [["127.0.0.1", A_TCP]])
        nl_node.make_node_id = lambda: "e2eb000000000000"
        busB = NetBus()
        busB.subscribe(TOPIC_PEER_STATE, on_state)
        busB.subscribe(TOPIC_PEER_LOG, on_log)
        B = NetLinkNode(root=None, bus=busB)
        okB = B.start()

        check("0a. node A start (tcp {}/udp {})".format(A_TCP, A_UDP), bool(okA))
        check("0b. node B start (tcp {}/udp {})".format(B_TCP, B_UDP), bool(okB))
        if not (okA and okB):
            print("[FAIL] 节点启动失败，后续断言无法继续")
            return 1

        # B 的出站连接（B -> A）建立（静态对端，重连线程最长 3s）
        conn_ok = wait_until(
            lambda: (B._client.get("127.0.0.1", A_TCP) is not None
                     and B._client.get("127.0.0.1", A_TCP).alive), 8.0)
        check("0c. B 静态对端连上 A (client alive)", conn_ok)
        if not conn_ok:
            print("[FAIL] 静态对端未建立连接，终止")
            return 1
        connBA = B._client.get("127.0.0.1", A_TCP)

        # ══════════════════════════════════════════════════════════════
        # 预置 A 侧内存执行状态（两者同进程共享 state，但 B 的 bus 只由
        # A 推送的 STATE_SYNC 驱动，故断言仍反映“链路真实性”）
        # ══════════════════════════════════════════════════════════════
        state.running = True
        state.filename = "报表填报.xls"
        state.exec_state = {"loop": 2, "total_loops": 5, "row": 34,
                            "total_rows": 120, "start_time": time.time() - 60,
                            "elapsed": 60}
        state.pause_event.set()

        # ── 断言 1：状态链路真实性 ──────────────────────────────────
        clear(state_msgs)
        sent = connBA.send(netlink.make_msg(
            "SUBSCRIBE", {"topics": ["state", "log", "sched"]}))
        got1 = wait_until(lambda: any(
            m.get("row") == 34 and m.get("total_rows") == 120
            and m.get("loop") == 2 and m.get("script") == "报表填报.xls"
            for m in snap(state_msgs)), 3.0)
        detail1 = ""
        if not got1:
            sample = snap(state_msgs)[-1] if snap(state_msgs) else {}
            detail1 = "sent={} last={}".format(sent, sample)
        check("1. B 收到 STATE 且 row==34/total_rows==120/loop==2/script 正确",
              got1, detail1)

        # ── 断言 2：暂停态上报 ──────────────────────────────────────
        clear(state_msgs)
        state.pause_event.clear()
        got_p = wait_until(lambda: any(m.get("paused") is True
                                       for m in snap(state_msgs)), 3.0)
        check("2a. clear() -> paused==True", got_p,
              "state_msgs={}".format(len(snap(state_msgs))))

        clear(state_msgs)
        state.pause_event.set()
        got_r = wait_until(lambda: any(m.get("paused") is False
                                       and m.get("running") is True
                                       for m in snap(state_msgs)), 3.0)
        check("2b. set() -> paused==False（恢复）", got_r,
              "state_msgs={}".format(len(snap(state_msgs))))

        # ── 断言 3：日志链路（真遍历 log1）──────────────────────────
        clear(log_msgs)
        tlog = None
        real_chain = False
        chain_note = ""
        try:
            if root is None:
                raise RuntimeError("no Tk root")
            txt = tkinter.Text(root)
            tlog = utils.ThreadSafeLog(txt)
            utils.set_tlog(tlog)
            utils.log1("E2E-行34-找图成功", "info")
            try:
                tlog.flush(root)  # 真链路按实际实现调用（sink 在 put 内触发）
            except Exception as fe:
                chain_note = "flush raised: {}".format(repr(fe))
            real_chain = True
        except Exception as e:
            chain_note = "real chain failed: {}".format(repr(e))
        if not real_chain:
            print("[WARN] log1 真链路不可用（{}），退化为 _broadcast_log".format(chain_note))
            try:
                utils._broadcast_log("E2E-行34-找图成功", "info", 1, "e2e.py", "test")
            except Exception as e:
                print("[FAIL] 退化路径也失败: {}".format(repr(e)))

        got3 = wait_until(lambda: any(
            "E2E-行34-找图成功" in str(l.get("msg", ""))
            for m in snap(log_msgs) for l in (m.get("lines") or [])), 3.0)
        check("3a. B 收到含 'E2E-行34-找图成功' 的 LOG_TAIL (<=3s)", got3,
              "real_chain={} note={} log_msgs={}".format(real_chain, chain_note,
                                                         len(snap(log_msgs))))
        # 断言 lines[0] 四键
        keys_ok = False
        for m in snap(log_msgs):
            lines = m.get("lines") or []
            if lines and isinstance(lines[0], dict):
                if set(["ts", "tag", "level", "msg"]).issubset(set(lines[0].keys())):
                    keys_ok = True
                    break
        check("3b. lines[0] 具备 ts/tag/level/msg 四键", keys_ok,
              "log_msgs={}".format(len(snap(log_msgs))))
        print("      [info] log real-chain used = {}".format(real_chain))

        # ── 断言 4：SUBSCRIBE 后立即推送（<2s）─────────────────────
        clear(state_msgs)
        t4 = time.time()
        connBA.send(netlink.make_msg("SUBSCRIBE", {"topics": ["state"]}))
        got4 = wait_until(lambda: len(snap(state_msgs)) > 0, 2.0)
        dt4 = time.time() - t4
        check("4. SUBSCRIBE 后立即推送 (<2s, 实测 {:.3f}s)".format(dt4),
              got4 and dt4 < 2.0, "dt={:.3f}".format(dt4))

        # ── 断言 5：断线重连（<=6s）+ 重订阅 ───────────────────────
        old = B._client.get("127.0.0.1", A_TCP)
        if old is not None:
            old.close()  # 强杀 B->A 连接
        t5 = time.time()
        back = wait_until(lambda: (B._client.get("127.0.0.1", A_TCP) is not None
                                   and B._client.get("127.0.0.1", A_TCP).alive), 6.0)
        dt5 = time.time() - t5
        check("5a. 掉线后 <=6s 重连成功 (实测 {:.3f}s)".format(dt5),
              back, "dt={:.3f}".format(dt5))

        got5b = False
        if back:
            clear(state_msgs)
            c2 = B._client.get("127.0.0.1", A_TCP)
            c2.send(netlink.make_msg("SUBSCRIBE", {"topics": ["state"]}))
            got5b = wait_until(lambda: len(snap(state_msgs)) > 0, 3.0)
        check("5b. 重连后重新 SUBSCRIBE 再次收到 STATE", got5b)

        # ── 断言 6：CMD_* 被拒绝（只读阶段不得误动本地执行）────────
        before_quit2 = getattr(state, "quit2", False)
        # (a) 直接入 A 的消息入口（确定性捕获 CMD_ERR）
        cap = _CaptureConn()
        A.handle_message(cap, netlink.make_msg(T_CMD_STOP))
        errs = [m for m in cap.sent if m.get("t") == T_CMD_ERR]
        reason = ""
        if errs:
            reason = str((errs[0].get("data") or {}).get("reason") or "")
        check("6a. A 对 T_CMD_STOP 回 CMD_ERR 且 reason 非空",
              bool(errs) and bool(reason), "errs={} reason={!r}".format(len(errs), reason))
        # (b) 也走一次真实链路（B 作为控制端向 A 发），验证不崩、不改 quit2
        try:
            c3 = B._client.get("127.0.0.1", A_TCP)
            if c3 is not None:
                c3.send(netlink.make_msg(T_CMD_STOP))
        except Exception as e:
            warn("real-path CMD send raised: {}".format(repr(e)))
        time.sleep(0.3)
        check("6b. state.quit2 未被置 True（本地执行未受影响）",
              getattr(state, "quit2", False) == before_quit2
              and not getattr(state, "quit2", False),
              "quit2={}".format(getattr(state, "quit2", False)))

        # ── 断言 7：静态对端两种写法 ────────────────────────────────
        def spin_and_probe(tcp, udp, name, static_value, node_id):
            set_cfg(tcp, udp, name, static_value, autodiscover=False)
            nl_node.make_node_id = (lambda _nid=node_id: _nid)
            n = NetLinkNode(root=None, bus=NetBus())
            ok = n.start()
            connected = False
            dt = 0.0
            if ok:
                t = time.time()
                connected = wait_until(lambda: (
                    n._client.get("127.0.0.1", B_TCP) is not None
                    and n._client.get("127.0.0.1", B_TCP).alive), 6.0)
                dt = time.time() - t
            try:
                n.stop()
            except Exception:
                pass
            return ok, connected, dt

        ok7a, conn7a, dt7a = spin_and_probe(
            C1_TCP, C1_UDP, "E2E-C1", ["127.0.0.1:{}".format(B_TCP)],
            "e2ec100000000000")
        check("7a. 字符串 '127.0.0.1:{}' 静态对端可连 (dt={:.3f}s)".format(B_TCP, dt7a),
              ok7a and conn7a, "ok={} conn={}".format(ok7a, conn7a))

        ok7b, conn7b, dt7b = spin_and_probe(
            C2_TCP, C2_UDP, "E2E-C2", [["127.0.0.1", B_TCP]],
            "e2ec200000000000")
        check("7b. 序列 ['127.0.0.1', {}] 静态对端可连 (dt={:.3f}s)".format(B_TCP, dt7b),
              ok7b and conn7b, "ok={} conn={}".format(ok7b, conn7b))
        if conn7a != conn7b:
            print("[info] 两种写法结果不一致：str={} seq={}".format(conn7a, conn7b))

        # ── 断言 8：端口占用降级 ────────────────────────────────────
        occ = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        occ_ok = True
        try:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                occ.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            occ.bind(("", OCC_TCP))
            occ.listen(8)
        except Exception as e:
            occ_ok = False
            warn("无法占用端口 {}：{}".format(OCC_TCP, repr(e)))
        if occ_ok:
            set_cfg(OCC_TCP, OCC_UDP, "E2E-Occ", [], autodiscover=False)
            nl_node.make_node_id = lambda: "e2eocc000000000"
            nocc = NetLinkNode(root=None, bus=NetBus())
            raised = None
            ok8 = None
            try:
                ok8 = nocc.start()
            except Exception as e:
                raised = e
            check("8a. 端口被占用时 start() 返回 False（无异常逃逸）",
                  raised is None and ok8 is False,
                  "raised={} ret={}".format(repr(raised), ok8))
            # 其它线程不被拖死
            alive_a = (A._server is not None and A._server._th is not None
                       and A._server._th.is_alive())
            alive_b = (B._server is not None and B._server._th is not None
                       and B._server._th.is_alive())
            check("8b. 已启动节点 A/B 的 accept 线程仍存活", bool(alive_a and alive_b),
                  "A={} B={}".format(alive_a, alive_b))
            try:
                nocc.stop()
            except Exception:
                pass
            try:
                occ.close()
            except Exception:
                pass
            time.sleep(0.3)
            # 释放后重新启动成功（新节点，使用同一端口）
            set_cfg(OCC_TCP, OCC_UDP, "E2E-Occ2", [], autodiscover=False)
            nl_node.make_node_id = lambda: "e2eocc200000000"
            n2 = NetLinkNode(root=None, bus=NetBus())
            ok8c = bool(n2.start())
            check("8c. 释放端口后重新启动成功", ok8c)
            try:
                n2.stop()
            except Exception:
                pass
        else:
            try:
                occ.close()
            except Exception:
                pass

        # ── 断言 9：幂等 stop + 可重启 ──────────────────────────────
        idem_ok = True
        try:
            A.stop()
            A.stop()
            B.stop()
            B.stop()
        except Exception as e:
            idem_ok = False
            warn("stop() 幂等抛异常：{}".format(repr(e)))
        check("9a. 两节点 stop() 各调 2 次不抛异常", idem_ok)

        restart_ok = True
        try:
            set_cfg(A_TCP, A_UDP, "E2E-A", [["127.0.0.1", B_TCP]])
            nl_node.make_node_id = lambda: "e2ea000000000000"
            rA = A.start()
            set_cfg(B_TCP, B_UDP, "E2E-B", [["127.0.0.1", A_TCP]])
            nl_node.make_node_id = lambda: "e2eb000000000000"
            rB = B.start()
            restart_ok = bool(rA and rB)
            A.stop()
            B.stop()
        except Exception as e:
            restart_ok = False
            warn("restart 抛异常：{}".format(repr(e)))
        check("9b. start()→stop() 各一轮成功", restart_ok)

        # ── 断言 10：资源泄漏检查 ───────────────────────────────────
        # 收尾：确保全部停止
        for n in (A, B):
            try:
                n.stop()
            except Exception:
                pass
        leaked = _nl_thread_names()
        if leaked:
            converged = wait_until(lambda: len(_nl_thread_names()) == 0, 5.0)
            leaked = _nl_thread_names()
        else:
            converged = True
        if converged and not leaked:
            check("10. 全部 stop() 后 nl-* 线程收敛为 0", True)
        else:
            warn("残留 nl-* 线程 {}（WARN，非 FAIL）".format(leaked))

    finally:
        # 复位全局 tlog 与执行态，避免污染
        try:
            utils.set_tlog(None)
        except Exception:
            pass
        try:
            if A is not None:
                A.stop()
        except Exception:
            pass
        try:
            if B is not None:
                B.stop()
        except Exception:
            pass
        try:
            if root is not None:
                root.destroy()
        except Exception:
            pass

    elapsed = time.time() - T0
    print("-" * 64)
    if elapsed >= TOTAL_BUDGET:
        _fails += 1
        print("[FAIL] 总耗时 {:.1f}s 超过 {}s 预算".format(elapsed, TOTAL_BUDGET))
    else:
        print("[OK]   总耗时 {:.1f}s (< {}s)".format(elapsed, TOTAL_BUDGET))
    if _fails == 0:
        print("PASS (all assertions OK; warns={})".format(_warns))
        return 0
    print("FAIL ({} assertion(s) failed; warns={})".format(_fails, _warns))
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(1)
