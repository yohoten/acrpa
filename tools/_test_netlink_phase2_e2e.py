# -*- coding: utf-8 -*-
"""NetLink Phase 2-3 端到端验收 —— 同进程双节点，全自动、不弹窗、总时长 < 60s。

运行:  python tools/_test_netlink_phase2_e2e.py
退出码: 0 = 全部断言通过（允许 [WARN] 不计失败）；1 = 存在 [FAIL]

场景:
  A = 被控端  tcp 19995  NETLINK_REQUIRE_AUTH=True  CONFIRM_CONTROL=False  静态对端关闭
  B = 控制端  tcp 19996  静态对端指向 A（走 netlink.start_netlink 门面单例，供窗口绑定）

覆盖 10 项断言:
  1. 自动订阅（缺陷 A 回归）：B 不做任何手工 SUBSCRIBE，≤5s 收到 TOPIC_PEER_STATE；
     触发 utils._broadcast_log 后 ≤5s 收到 TOPIC_PEER_LOG。（本轮最重要）
  2. 权限闭环：A.set_peer_perm(control/observe) → ≤2s B 收到 TOPIC_AUTH stage=perm。
  3. 指令闭环：B 发 CMD_RUN → B 收 accepted→done，A 的 fake_run 被调用，审计含 cmd=CMD_RUN。
  4. PAUSE/RESUME/STOP 闭环（脚本分叉）：pause_event / quit2 变化符合预期，B 均收终态。
  5. 工作流分叉（真实 workflow 模块）：PAUSE/RESUME 只动 eng._pause_event；STOP → _stopped。
     若 import workflow 失败 → [WARN] 并打印原因（不假通过）。
  6. 权限不足拒绝：perm=observe 发 CMD_STOP → B 收 CMD_ERR(reason 含 permission denied)，
     且 A 的 state.quit2 未被改变。
  7. 重连后重新订阅：强断 B→A → ≤8s 重连 → 不手工订阅 ≤5s 再次收到 TOPIC_PEER_STATE。
  8. UI 真实渲染：窗口设备表该行「状态」列非占位、「进度」列非 '—'。
  9. 审计与 config 隔离：审计文件含 cmd=/actor=/result=；临时 config.json 不含密钥/审计内容。
  10. 收尾：两节点 stop + 窗口 close → nl-* 线程 ≤5s 收敛为 0。

硬约束: 不弹窗、不阻塞、所有等待带 deadline、不写项目 config.json、端口 199xx 段用完释放、
        输出 [OK]/[WARN]/[FAIL]。
"""
import os
import shutil
import sys
import tempfile
import threading
import time
import traceback
import types
import uuid

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_SRC = os.path.join(_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import tkinter  # noqa: F401  —— utils 顶层 import tkinter，提前加载
import state
import utils

# ── 端口（199xx 段）──
A_TCP, A_UDP = 19995, 19993
B_TCP, B_UDP = 19996, 19994
TOTAL_BUDGET = 60.0

_fails = 0
_warns = 0
T0 = time.time()


def check(name, cond, detail=""):
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


def spin(root, seconds=0.4):
    end = time.time() + float(seconds)
    while time.time() < end:
        try:
            root.update()
        except Exception:
            pass
        time.sleep(0.02)


def wait_until(fn, timeout, root=None):
    """带 deadline 的轮询等待；返回 (是否满足, 实测耗时秒)。"""
    t = time.time()
    end = t + float(timeout)
    while time.time() < end:
        try:
            if fn():
                return True, time.time() - t
        except Exception:
            pass
        if root is not None:
            try:
                root.update()
            except Exception:
                pass
        time.sleep(0.02)
    return False, time.time() - t


def _nl_threads():
    try:
        return sorted(t.name for t in threading.enumerate()
                      if t.name and t.name.startswith("nl-"))
    except Exception:
        return []


class FakeHooks(object):
    """被控端假执行钩子：记录 run/stop 调用；stop 复刻 stop_execution 的状态设置。"""

    def __init__(self):
        self._lock = threading.Lock()
        self.run_calls = []
        self.stop_calls = 0
        self.sleep = 0.0

    def reset(self):
        with self._lock:
            self.run_calls = []
            self.stop_calls = 0

    def run(self, loops=None):
        with self._lock:
            self.run_calls.append(loops)
        try:
            if self.sleep:
                time.sleep(self.sleep)
        except Exception:
            pass

    def stop(self):
        with self._lock:
            self.stop_calls += 1
        try:
            state.quit3 = True
            state.quit2 = True
            state.running = False
            state.pause_event.set()
            state.exec_state["loop"] = 0
            state.exec_state["row"] = 0
        except Exception:
            pass


def main():
    global _fails
    # ── Tk root（无显示则 SKIP，退出码 0）──
    try:
        root = tkinter.Tk()
        root.withdraw()
    except Exception as e:
        print("[SKIP] no display ({})".format(e))
        return 0

    print("=" * 66)
    print("NetLink Phase 2-3 E2E acceptance (dual node, auto-subscribe focus)")
    print("=" * 66)

    # ── 环境隔离：备份 ──
    saved = {}
    for name in ("CONFIG_PATH", "NETLINK_PEERS", "NETLINK_CONFIRMED_PEERS",
                 "NETLINK_REQUIRE_AUTH", "NETLINK_CONFIRM_CONTROL",
                 "NETLINK_SCRIPT_DIR", "API_KEY", "running", "quit2", "quit3",
                 "recording", "has_script", "filename", "script_dir"):
        saved[name] = getattr(state, name, None)
    saved["exec_state"] = dict(getattr(state, "exec_state", {}) or {})
    saved["pause_event"] = getattr(state, "pause_event", None)
    orig_cred = None
    try:
        orig_cred = state.cred_read(state._CRED_TARGET)
    except Exception:
        orig_cred = None

    tmpdir = tempfile.mkdtemp(prefix="acrpa_phase2e2e_")
    state.CONFIG_PATH = os.path.join(tmpdir, "config.json")   # 审计目录随之隔离到 tmpdir/logs
    state.NETLINK_PEERS = []
    state.NETLINK_CONFIRMED_PEERS = []

    import netlink
    import netlink.node as nl_node
    import netlink_window
    from netlink import security
    from netlink.bus import NetBus
    from netlink.node import (TOPIC_PEER_STATE, TOPIC_PEER_LOG, TOPIC_AUTH,
                              TOPIC_CMD_RESULT)
    from netlink.protocol import (T_CMD_RUN, T_CMD_PAUSE, T_CMD_RESUME, T_CMD_STOP,
                                  T_CMD_ACK, T_CMD_ERR, T_SUBSCRIBE)

    orig_make_node_id = nl_node.make_node_id
    orig_workflow = sys.modules.get("workflow", None)
    run_tag = uuid.uuid4().hex[:6]
    _id_seq = {"n": 0}

    def _mk_id():
        _id_seq["n"] += 1
        return "p2{}{}".format(run_tag, _id_seq["n"])

    nl_node.make_node_id = _mk_id

    hooks = FakeHooks()
    a = None
    b = None
    win = None
    b_fp = ""
    a_node_id = ""
    forget_fps = []
    lock = threading.Lock()
    state_msgs = []
    log_msgs = []
    cmd_events = []

    def snap(lst):
        with lock:
            return list(lst)

    try:
        # ── 启动 A（被控端，直接构造节点）──
        state.NETLINK_ENABLED = True
        state.NETLINK_PORT = A_TCP
        state.NETLINK_DISCOVERY_PORT = A_UDP
        state.NETLINK_AUTODISCOVER = False
        state.NETLINK_DEVICE_NAME = "Phase2A"
        state.NETLINK_STATIC_PEERS = []
        state.NETLINK_REQUIRE_AUTH = True
        state.NETLINK_PIN_TTL = 600
        state.NETLINK_CONFIRM_CONTROL = False
        state.NETLINK_CONFIRMED_PEERS = []
        state.NETLINK_SCRIPT_DIR = ""
        state.running = False
        state.recording = False
        state.quit2 = False
        state.quit3 = False
        state.has_script = True
        state.pause_event.set()

        a = nl_node.NetLinkNode(root=None, bus=NetBus())
        ok_a = a.start()
        check("0. 被控端 A start (tcp {})".format(A_TCP), bool(ok_a))
        a.set_control_hooks(run=hooks.run, stop=hooks.stop)

        # ── 启动 B（控制端，门面单例，供窗口绑定）──
        state.NETLINK_PORT = B_TCP
        state.NETLINK_DISCOVERY_PORT = B_UDP
        state.NETLINK_DEVICE_NAME = "Phase2B"
        state.NETLINK_STATIC_PEERS = [["127.0.0.1", A_TCP]]
        state.NETLINK_AUTODISCOVER = False
        ok_b = netlink.start_netlink(root)
        check("0b. 控制端 B start (tcp {}, 静态对端→A)".format(B_TCP), bool(ok_b))
        b = netlink.get_node()
        check("0c. netlink.get_node() 可用且 != A", b is not None and b is not a)
        if a is None or b is None or not ok_a or not ok_b:
            print("[FAIL] 节点未就绪，后续断言无法继续")
            return finish(root, a, win, saved, orig_make_node_id, orig_workflow,
                          orig_cred, tmpdir, forget_fps)

        # ── 打开窗口（供断言 8）──
        win = netlink_window.open_netlink_window(root)
        spin(root, 0.5)

        # ── 总线收集器（回调路径，独立于窗口 drain）──
        def on_state(topic, payload):
            with lock:
                state_msgs.append(dict(payload) if isinstance(payload, dict) else {})

        def on_log(topic, payload):
            with lock:
                log_msgs.append(payload)

        def on_cmd(topic, payload):
            with lock:
                cmd_events.append(dict(payload) if isinstance(payload, dict) else {})

        try:
            b.bus.subscribe(TOPIC_PEER_STATE, on_state)
            b.bus.subscribe(TOPIC_PEER_LOG, on_log)
            b.bus.subscribe(TOPIC_CMD_RESULT, on_cmd)
        except Exception as e:
            warn("bus.subscribe 失败: {!r}".format(e))

        # ── 插桩：记录 B 实际「发出」的消息类型，用于证明自动订阅（缺陷 A 回归）──
        sent_types = []
        orig_bsend = b.send

        def _bsend(conn, msg_dict):
            try:
                sent_types.append((msg_dict or {}).get("t"))
            except Exception:
                pass
            return orig_bsend(conn, msg_dict)

        b.send = _bsend

        # ── 配对：A 开配对窗口 → B register_target(pin) ──
        for _fp in (str(getattr(a, "_self_fp", "") or ""),
                    str(getattr(b, "_self_fp", "") or "")):
            if _fp:
                forget_fps.append(_fp)
                try:
                    security.forget_peer_token(_fp)
                except Exception:
                    pass
        pin = a.open_pair_window()
        check("0d. A.open_pair_window() 返回 6 位 PIN",
              isinstance(pin, str) and len(pin) == 6 and pin.isdigit(),
              "pin={!r}".format(pin))
        b.register_target("127.0.0.1", A_TCP, pin)
        paired, dtp = wait_until(lambda: len(a.list_peers() or []) >= 1, 8.0, root)
        check("0e. 配对完成（A 白名单出现 B, {:.2f}s）".format(dtp), paired,
              "peers={}".format(len(a.list_peers() or [])))
        if not paired:
            print("[FAIL] 配对未完成，终止")
            return finish(root, a, win, saved, orig_make_node_id, orig_workflow,
                          orig_cred, tmpdir, forget_fps)
        b_fp = str((a.list_peers() or [{}])[0].get("fingerprint") or "")
        a_node_id = str(a.info.get("node_id") or "")

        # 模拟「发现到 A 的真实 node_id」（生产由 UDP 广播提供；本测试关闭自动发现）
        b._on_peer({"node_id": a_node_id, "name": "Phase2A", "host": "127.0.0.1",
                    "port": A_TCP, "version": "", "online": True})
        wait_until(lambda: win._items.get(a_node_id) is not None, 2.0, root)

        # ══════════════════ 1. 自动订阅（缺陷 A 回归，最重要）══════════════════
        state.running = True
        state.recording = False
        state.filename = "报表填报.xls"
        state.exec_state = {"loop": 1, "total_loops": 3, "row": 12, "total_rows": 240,
                            "start_time": time.time() - 30, "elapsed": 30}
        state.pause_event.set()

        got1a, dt1a = wait_until(lambda: any(
            (m.get("running") is True and m.get("row") == 12
             and m.get("total_rows") == 240) for m in snap(state_msgs)), 5.0, root)
        check("1a. 自动订阅：B 未手工 SUBSCRIBE，≤5s 收到 TOPIC_PEER_STATE"
              "（running/row/total_rows 正确，实测 {:.3f}s）".format(dt1a),
              got1a, "state_msgs={}".format(len(snap(state_msgs))))
        check("1a'. B 确实自动发出了 SUBSCRIBE（缺陷 A 修复生效）",
              T_SUBSCRIBE in sent_types,
              "sent_types={}".format([t for t in sent_types if t][:12]))

        with lock:
            del log_msgs[:]
        try:
            utils._broadcast_log("PHASE2-E2E-LOG-MARK", None, 1, "phase2e2e.py", "test")
        except Exception as e:
            warn("_broadcast_log 失败: {!r}".format(e))
        got1b, dt1b = wait_until(lambda: any(
            "PHASE2-E2E-LOG-MARK" in str(l.get("msg", ""))
            for m in snap(log_msgs) for l in (m.get("lines") or [])), 5.0, root)
        check("1b. 自动订阅：A 触发日志后 ≤5s B 收到 TOPIC_PEER_LOG"
              "（实测 {:.3f}s）".format(dt1b), got1b,
              "log_msgs={}".format(len(snap(log_msgs))))

        # ══════════════════ 2. 权限闭环 ══════════════════
        auth_msgs = []

        def on_auth(topic, payload):
            with lock:
                auth_msgs.append(dict(payload) if isinstance(payload, dict) else {})

        try:
            b.bus.subscribe(TOPIC_AUTH, on_auth)
        except Exception:
            pass

        with lock:
            del auth_msgs[:]
        a.set_peer_perm(b_fp, "control")
        got2a, dt2a = wait_until(lambda: any(
            (m.get("stage") == "perm" and m.get("perm") == "control")
            for m in snap(auth_msgs)), 2.0, root)
        check("2a. A.set_peer_perm(fp,'control') → ≤2s B 收到 TOPIC_AUTH"
              " stage=perm perm=control（{:.2f}s）".format(dt2a), got2a,
              "auth={}".format(snap(auth_msgs)[-3:]))

        with lock:
            del auth_msgs[:]
        a.set_peer_perm(b_fp, "observe")
        got2b, dt2b = wait_until(lambda: any(
            (m.get("stage") == "perm" and m.get("perm") == "observe")
            for m in snap(auth_msgs)), 2.0, root)
        check("2b. 改回 observe → ≤2s B 同样收到 stage=perm perm=observe"
              "（{:.2f}s）".format(dt2b), got2b)

        # ══════════════════ 3. 指令闭环（CMD_RUN）══════════════════
        state.NETLINK_CONFIRM_CONTROL = False
        state.running = False
        state.recording = False
        state.has_script = True
        state.filename = os.path.join(tmpdir, "demo.xls")
        hooks.reset()
        a.set_peer_perm(b_fp, "control")
        with lock:
            del cmd_events[:]
        sent3 = netlink.send_command(a_node_id, T_CMD_RUN, {})
        check("3a. netlink.send_command(CMD_RUN) 定位到 B→A 连接并发送", bool(sent3))

        def _statuses():
            return [str(((e.get("data") or {}).get("status")) or "")
                    for e in snap(cmd_events)
                    if (e.get("data") or {}).get("cmd") == T_CMD_RUN]

        got3, dt3 = wait_until(lambda: ("accepted" in _statuses()
                                        and "done" in _statuses()), 4.0, root)
        seq_ok = ("accepted" in _statuses() and "done" in _statuses()
                  and _statuses().index("accepted") < _statuses().index("done"))
        check("3b. B 收到 TOPIC_CMD_RESULT accepted → done（{}）".format(_statuses()),
              got3 and seq_ok)
        got3c, _ = wait_until(lambda: len(hooks.run_calls) >= 1, 2.0, root)
        check("3c. A 侧 fake_run 被调用 1 次", got3c and len(hooks.run_calls) == 1,
              "calls={}".format(len(hooks.run_calls)))
        audit_text = "\n".join(a.control.audit.tail(100) or [])
        check("3d. A 审计文件含 cmd=CMD_RUN", "cmd=CMD_RUN" in audit_text,
              "len={}".format(len(audit_text)))

        # ══════════════════ 4. PAUSE / RESUME / STOP 闭环（脚本分叉）══════════════════
        sys.modules["workflow"] = types.SimpleNamespace(workflow_engine=None)
        state.running = True
        state.recording = False
        state.pause_event.set()
        state.quit2 = False
        state.quit3 = False
        hooks.reset()
        a.set_peer_perm(b_fp, "control")
        with lock:
            del cmd_events[:]       # 清空起点：本条内 3 条指令的终态都保留到 4d 断言

        netlink.send_command(a_node_id, T_CMD_PAUSE, {})
        got4a, _ = wait_until(lambda: state.pause_event.is_set() is False, 3.0, root)
        check("4a. CMD_PAUSE → state.pause_event.is_set() is False", got4a,
              "set={}".format(state.pause_event.is_set()))

        netlink.send_command(a_node_id, T_CMD_RESUME, {})
        got4b, _ = wait_until(lambda: state.pause_event.is_set() is True, 3.0, root)
        check("4b. CMD_RESUME → state.pause_event 重新 set", got4b)

        netlink.send_command(a_node_id, T_CMD_STOP, {})
        got4c, _ = wait_until(lambda: state.quit2 is True, 3.0, root)
        check("4c. CMD_STOP（脚本分叉）→ state.quit2 is True 且 fake_stop 被调用",
              got4c and hooks.stop_calls >= 1,
              "quit2={} stop_calls={}".format(state.quit2, hooks.stop_calls))

        def _done_for(cmd):
            return any(str(((e.get("data") or {}).get("cmd")) or "") == cmd
                       and str(((e.get("data") or {}).get("status")) or "") == "done"
                       for e in snap(cmd_events))

        got4d, _ = wait_until(lambda: (_done_for(T_CMD_PAUSE)
                                       and _done_for(T_CMD_RESUME)
                                       and _done_for(T_CMD_STOP)), 2.0, root)
        check("4d. B 侧 PAUSE/RESUME/STOP 均收到终态 done", got4d,
              "events={}".format([((e.get("data") or {}).get("cmd"),
                                   (e.get("data") or {}).get("status"))
                                  for e in snap(cmd_events)][-8:]))

        # ══════════════════ 5. 工作流分叉（真实 workflow 模块）══════════════════
        wf_ok = False
        eng = None
        try:
            sys.modules.pop("workflow", None)
            import workflow as wf_real          # noqa: F401
            eng = wf_real.WorkflowEngine()
            wf_real.workflow_engine = eng
            eng._stopped = False
            eng._current_step = 0               # 伪造「正在执行」态，满足 _workflow_active
            wf_ok = True
        except Exception as e:
            warn("5. import workflow 失败（GUI/依赖缺失）：{!r} —— 工作流分叉断言记 WARN"
                 "（不假通过）".format(e))
        if wf_ok:
            state.running = True
            state.recording = False
            state.pause_event.set()
            state.quit2 = False
            hooks.reset()
            a.set_peer_perm(b_fp, "control")

            netlink.send_command(a_node_id, T_CMD_PAUSE, {})
            got5a, _ = wait_until(lambda: (eng._pause_event.is_set() is False
                                           and state.pause_event.is_set() is True),
                                  3.0, root)
            check("5a. workflow CMD_PAUSE → eng._pause_event 清除 且 state.pause_event 仍 set",
                  got5a, "eng={} state={}".format(eng._pause_event.is_set(),
                                                  state.pause_event.is_set()))

            netlink.send_command(a_node_id, T_CMD_RESUME, {})
            got5b, _ = wait_until(lambda: eng._pause_event.is_set() is True, 3.0, root)
            check("5b. workflow CMD_RESUME → eng._pause_event 恢复 set", got5b)

            netlink.send_command(a_node_id, T_CMD_STOP, {})
            got5c, _ = wait_until(lambda: eng._stopped is True, 3.0, root)
            check("5c. workflow CMD_STOP → eng._stopped is True", got5c)
        # 还原为脚本分叉，供后续断言
        sys.modules["workflow"] = types.SimpleNamespace(workflow_engine=None)

        # ══════════════════ 6. 权限不足拒绝 ══════════════════
        a.set_peer_perm(b_fp, "observe")
        state.quit2 = False
        state.running = True
        with lock:
            del cmd_events[:]
        netlink.send_command(a_node_id, T_CMD_STOP, {})
        got6, _ = wait_until(lambda: any(
            e.get("t") == T_CMD_ERR
            and "permission denied" in str(((e.get("data") or {}).get("reason")) or "")
            for e in snap(cmd_events)), 3.0, root)
        check("6a. perm=observe 发 CMD_STOP → B 收到 CMD_ERR 且 reason 含 'permission denied'",
              got6, "events={}".format([((e.get("data") or {}).get("reason")) for e in snap(cmd_events)][-3:]))
        spin(root, 0.3)
        check("6b. 权限不足时 A 的 state.quit2 未被改变",
              state.quit2 is False, "quit2={}".format(state.quit2))

        # ══════════════════ 7. 重连后重新订阅 ══════════════════
        a.set_peer_perm(b_fp, "control")
        old_conn = b._client.get("127.0.0.1", A_TCP)
        if old_conn is not None:
            try:
                old_conn.close()
            except Exception:
                pass
        back, dt7a = wait_until(lambda: (
            b._client.get("127.0.0.1", A_TCP) is not None
            and b._client.get("127.0.0.1", A_TCP).alive), 8.0, root)
        check("7a. 强断 B→A 后 ≤8s 重连成功（实测 {:.3f}s）".format(dt7a), back)
        new_conn = b._client.get("127.0.0.1", A_TCP)
        check("7a'. 重连后为全新连接对象（旧 conn 已被替换）",
              new_conn is not None and new_conn is not old_conn)

        got7b = False
        dt7b = -1.0
        if back:
            spin(root, 1.0)          # 等认证 + 自动订阅完成
            with lock:
                del state_msgs[:]
            state.exec_state = dict(state.exec_state)
            state.exec_state["row"] = 77
            got7b, dt7b = wait_until(lambda: any(m.get("row") == 77
                                                 for m in snap(state_msgs)), 5.0, root)
        check("7b. 重连后无需手工订阅，≤5s 再次收到 TOPIC_PEER_STATE"
              "（实测 {:.3f}s）".format(dt7b), got7b,
              "state_msgs={}".format(len(snap(state_msgs))))

        # ══════════════════ 8. UI 真实渲染 ══════════════════
        state.running = True
        state.recording = False
        state.pause_event.set()
        state.filename = "报表填报.xls"
        state.exec_state = {"loop": 2, "total_loops": 5, "row": 88, "total_rows": 240,
                            "start_time": time.time() - 60, "elapsed": 60}

        def _row_vals():
            item = win._items.get(a_node_id)
            if item is None:
                return None
            try:
                return tuple(win.tv.item(item).get("values") or ())
            except Exception:
                return None

        got8, _ = wait_until(lambda: (_row_vals() is not None
                                      and len(_row_vals()) >= 4
                                      and str(_row_vals()[1]) in ("● 运行", "⏸ 暂停", "● 录制中")
                                      and str(_row_vals()[3]) != "—"), 6.0, root)
        v = _row_vals() or ()
        check("8. 窗口设备表该行「状态」列非占位且「进度」列 != '—'",
              got8, "values={}".format(v))
        if got8:
            print("       [info] 状态列={!r} 进度列={!r}".format(
                v[1] if len(v) > 1 else None, v[3] if len(v) > 3 else None))

        # ══════════════════ 9. 审计与 config 隔离 ══════════════════
        apath = ""
        try:
            apath = a.control.audit.path
        except Exception:
            apath = ""
        atext = ""
        try:
            if apath and os.path.isfile(apath):
                with open(apath, "r", encoding="utf-8", errors="replace") as f:
                    atext = f.read()
        except Exception:
            atext = ""
        check("9a. logs/netlink_audit_YYYYMMDD.log 存在且含 cmd= / actor= / result=",
              bool(apath) and os.path.isfile(apath)
              and ("cmd=" in atext and "actor=" in atext and "result=" in atext),
              "path={!r} len={}".format(os.path.basename(apath or ""), len(atext)))

        cfg_text = ""
        try:
            if os.path.isfile(state.CONFIG_PATH):
                with open(state.CONFIG_PATH, "r", encoding="utf-8") as f:
                    cfg_text = f.read()
        except Exception:
            cfg_text = ""
        low = cfg_text.lower()
        leak = [k for k in ("cmd=", "actor=", "proof", "secret") if k in low]
        check("9b. 临时 config.json 不含 cmd= / actor= / proof / secret",
              cfg_text == "" or not leak,
              "leak={} cfg_len={}".format(leak, len(cfg_text)))

        # ══════════════════ 10. 收尾 + 线程收敛 ══════════════════
        try:
            netlink_window.close_window()
        except Exception:
            pass
        try:
            netlink.stop_netlink()
        except Exception:
            pass
        try:
            if a is not None:
                a.stop()
        except Exception:
            pass
        spin(root, 0.4)
        conv, dt10 = wait_until(lambda: len(_nl_threads()) == 0, 5.0, root)
        check("10. 两节点 stop + 窗口 close 后 nl-* 线程 ≤5s 收敛为 0"
              "（实测 {:.2f}s）".format(dt10), conv,
              "leaked={}".format(_nl_threads()))

    except Exception:
        print("[FAIL] 未捕获异常:")
        traceback.print_exc()
        _fails += 1
    finally:
        rc = finish(root, a, win, saved, orig_make_node_id, orig_workflow,
                    orig_cred, tmpdir, forget_fps)
        return rc


def finish(root, a, win, saved, orig_make_node_id, orig_workflow, orig_cred,
           tmpdir, forget_fps):
    global _fails
    # ── 收尾：关窗 → 停节点 ──
    try:
        import netlink_window
        netlink_window.close_window()
    except Exception:
        pass
    try:
        import netlink
        netlink.stop_netlink()
    except Exception:
        pass
    try:
        if a is not None:
            a.stop()
    except Exception:
        pass

    # ── 还原 state / 凭据库 / 模块补丁 ──
    try:
        state.CONFIG_PATH = saved.get("CONFIG_PATH")
        state.NETLINK_PEERS = saved.get("NETLINK_PEERS")
        state.NETLINK_CONFIRMED_PEERS = saved.get("NETLINK_CONFIRMED_PEERS")
        state.NETLINK_REQUIRE_AUTH = saved.get("NETLINK_REQUIRE_AUTH")
        state.NETLINK_CONFIRM_CONTROL = saved.get("NETLINK_CONFIRM_CONTROL")
        state.NETLINK_SCRIPT_DIR = saved.get("NETLINK_SCRIPT_DIR")
        state.API_KEY = saved.get("API_KEY")
        state.running = saved.get("running")
        state.quit2 = saved.get("quit2")
        state.quit3 = saved.get("quit3")
        state.recording = saved.get("recording")
        state.has_script = saved.get("has_script")
        state.filename = saved.get("filename")
        state.script_dir = saved.get("script_dir")
        if saved.get("exec_state") is not None:
            state.exec_state = saved.get("exec_state")
        if saved.get("pause_event") is not None:
            state.pause_event = saved.get("pause_event")
        if orig_cred:
            state.cred_write(state._CRED_TARGET, orig_cred)
        else:
            state.cred_delete(state._CRED_TARGET)
    except Exception:
        pass
    try:
        import netlink.node as nl_node
        nl_node.make_node_id = orig_make_node_id
    except Exception:
        pass
    try:
        if orig_workflow is None:
            sys.modules.pop("workflow", None)
        else:
            sys.modules["workflow"] = orig_workflow
    except Exception:
        pass
    for fp in (forget_fps or []):
        if fp:
            try:
                from netlink import security
                security.forget_peer_token(fp)
            except Exception:
                pass
    try:
        shutil.rmtree(tmpdir, ignore_errors=True)
    except Exception:
        pass
    try:
        if root is not None:
            root.destroy()
    except Exception:
        pass

    elapsed = time.time() - T0
    print("-" * 66)
    if elapsed >= TOTAL_BUDGET:
        _fails += 1
        print("[FAIL] 总耗时 {:.1f}s 超过 {}s 预算".format(elapsed, TOTAL_BUDGET))
    else:
        print("[OK]   总耗时 {:.1f}s (< {}s)".format(elapsed, TOTAL_BUDGET))
    print("warns={}".format(_warns))
    if _fails == 0:
        print("PASS (all assertions OK; warns={})".format(_warns))
        return 0
    print("FAIL ({} assertion(s) failed; warns={})".format(_fails, _warns))
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
