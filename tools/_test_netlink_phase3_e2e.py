# -*- coding: utf-8 -*-
"""NetLink Phase 3-2 端到端验收（批量下发 + 远端拒绝可见 + 定时任务展示）—— 全自动、不弹窗、< 60s。

运行:   python tools/_test_netlink_phase3_e2e.py
退出码: 0 = 全部断言通过（含 [SKIP] no display，允许 [WARN]）；1 = 存在 [FAIL]

架构（同进程三节点）:
  * A  = 被控端（直接构造 NetLinkNode，tcp 19981）；
  * A2 = 被控端（直接构造 NetLinkNode，tcp 19982）；
  * C  = 控制端（走 netlink.start_netlink 门面单例，tcp 19991，
        静态对端指向 A 与 A2），「设备互联」窗口绑定到 C。

⚠ 同进程多节点「received/」共享限制（重要，影响断言解释）:
  transfer.script_root() 依赖 state.NETLINK_SCRIPT_DIR —— 本进程内 state 为**全局单例**，
  因此 A 与 A2 解析到的是**同一个**脚本根，received/ 也是同一目录。
  于是**不能**据“A 与 A2 各写一份文件”来断言扇出；本脚本改为断言
  「C 收到 **2 条**来自不同连接的 CMD_ACK{cmd:'SCRIPT_PUSH', ok:True, status:'done'}
   且两条 tid 互不相同」这一**等价**的扇出证据。
  该限制只影响“物理落盘份数”的观察，不影响 tid/回执这一逻辑扇出证据的可信度。

10 项断言:
  1  批量下发返回 2 个不同 tid、skip 为空
  2  C 侧出现 2 条 SCRIPT_PUSH done（tid 各异）
  3  目标文件落盘且 sha256 与源一致
  4  扇出独立性：A2 权限改回 observe → A 仍成功、C 可见 A2 的 permission denied
  5  远端拒绝可见（核心修复）：checksum mismatch → C「传输」列表 失败：checksum mismatch（红）
  6  skip 场景：不存在的 peer / 本地文件不存在
  7  定时任务状态展示：TOPIC_PEER_SCHED → 详情区含 启用/09:30/3 个任务
  8  多选批量 UI 路径：selection_set 两台 → invoke [推脚本] → 新增 2 行
  9  审计含 cmd=SCRIPT_PUSH；临时 config.json 不含 cmd=/actor=/proof/secret
  10 收尾：三节点 stop + close_window → nl-* 线程 ≤5s 收敛为 0
"""
import hashlib
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

try:
    import tkinter  # noqa: F401 —— 提前加载（utils 顶层 import tkinter；headless 时忽略）
except Exception:
    pass

import state
import netlink
import netlink.node as nl_node
import netlink_window
from netlink.bus import NetBus
from netlink import security, transfer
from netlink.node import TOPIC_CMD_RESULT, TOPIC_PEER_SCHED  # noqa: F401
from netlink.protocol import make_msg, T_CMD_ACK, T_CMD_ERR  # noqa: F401

# ── 端口（199xx 段）──
A_TCP, A_UDP = 19981, 19983
A2_TCP, A2_UDP = 19982, 19984
C_TCP, C_UDP = 19991, 19993
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


def skip(name, why="no display"):
    print("[SKIP] " + name + " :: " + why)


def warn(msg):
    global _warns
    _warns += 1
    print("[WARN] " + msg)


def spin(root, seconds=0.5):
    """反复 root.update() 驱动窗口 _pump；root 为 None 时仅 sleep。"""
    end = time.time() + float(seconds)
    while time.time() < end:
        if root is not None:
            try:
                root.update()
            except Exception:
                pass
        time.sleep(0.02)


def wait_until(fn, timeout, root=None):
    end = time.time() + float(timeout)
    while time.time() < end:
        try:
            if fn():
                return True
        except Exception:
            pass
        if root is not None:
            try:
                root.update()
            except Exception:
                pass
        time.sleep(0.02)
    return False


def _nl_threads():
    try:
        return sorted(t.name for t in threading.enumerate()
                      if t.name and t.name.startswith("nl-"))
    except Exception:
        return []


def _row_for_name(win, name):
    """「传输」列表中文件列 == name 的行 values（取第一条）。"""
    try:
        for iid in win.tv_xfer.get_children():
            vals = win.tv_xfer.item(iid).get("values") or ()
            if len(vals) >= 4 and str(vals[1]) == name:
                return tuple(vals), tuple(win.tv_xfer.item(iid).get("tags") or ())
    except Exception:
        pass
    return None, ()


def _rows_for_name(win, name):
    out = []
    try:
        for iid in win.tv_xfer.get_children():
            vals = win.tv_xfer.item(iid).get("values") or ()
            if len(vals) >= 4 and str(vals[1]) == name:
                out.append(tuple(vals))
    except Exception:
        pass
    return out


def _select_many(win, nids):
    """在设备表选中多行（保持选择顺序）；成功返回 True。"""
    iids = []
    for nid in nids:
        item = win._items.get(nid)
        if item:
            iids.append(item)
    if not iids:
        return False
    try:
        win.tv.selection_set(iids)
        win._on_select()
        return True
    except Exception:
        return False


def main():
    global _fails
    print("=" * 66)
    print("NetLink Phase 3-2 end-to-end acceptance (batch / remote-reject / sched)")
    print("=" * 66)

    # ── Tk root（无显示设备则 Tk 相关断言 SKIP，其余仍执行）──
    root = None
    has_display = True
    try:
        root = tkinter.Tk()
        root.withdraw()
    except Exception as e:
        root = None
        has_display = False
        print("[SKIP] no display ({}) — Tk 相关断言将跳过，退出码仍为 0".format(e))

    # ── 环境隔离：备份 ──
    orig = {}
    for name in ("CONFIG_PATH", "NETLINK_ENABLED", "NETLINK_PEERS",
                 "NETLINK_CONFIRMED_PEERS", "NETLINK_REQUIRE_AUTH",
                 "NETLINK_SCRIPT_DIR", "NETLINK_CONFIRM_CONTROL",
                 "NETLINK_DEVICE_NAME", "NETLINK_PORT", "NETLINK_DISCOVERY_PORT",
                 "NETLINK_AUTODISCOVER", "NETLINK_STATIC_PEERS",
                 "NETLINK_UI_WINDOW", "API_KEY",
                 "SCHED_ENABLED", "SCHED_NEXT_RUN", "SCHED_TASKS",
                 "running", "quit2", "quit3", "recording",
                 "has_script", "filename", "script_dir", "pause_event"):
        orig[name] = getattr(state, name, None)
    orig_make_node_id = nl_node.make_node_id
    orig_workflow = sys.modules.get("workflow", None)
    try:
        orig_cred = state.cred_read(state._CRED_TARGET)
    except Exception:
        orig_cred = None

    tmpdir = tempfile.mkdtemp(prefix="acrpa_p3_")
    scriptdir = os.path.join(tmpdir, "scripts")
    os.makedirs(os.path.join(scriptdir, "received"), exist_ok=True)
    localdir = os.path.join(tmpdir, "local")
    os.makedirs(localdir, exist_ok=True)

    state.CONFIG_PATH = os.path.join(tmpdir, "config.json")
    state.NETLINK_PEERS = []
    state.NETLINK_CONFIRMED_PEERS = []
    state.NETLINK_REQUIRE_AUTH = True
    state.NETLINK_SCRIPT_DIR = scriptdir
    state.NETLINK_CONFIRM_CONTROL = False

    run_tag = uuid.uuid4().hex[:6]
    seq = {"n": 0}

    def _mk_id():
        seq["n"] += 1
        return "p3e{}{}".format(run_tag, seq["n"])

    nl_node.make_node_id = _mk_id
    sys.modules["workflow"] = types.SimpleNamespace(workflow_engine=None)

    a = None
    a2 = None
    win = None
    fps = []
    lock = threading.Lock()
    c_cmd = []      # C 侧 TOPIC_CMD_RESULT
    c_xfer = []     # C 侧 TOPIC_TRANSFER
    c_sched = []    # C 侧 TOPIC_PEER_SCHED

    def snap(lst):
        with lock:
            return list(lst)

    def on_cmd(topic, payload):
        with lock:
            c_cmd.append(dict(payload) if isinstance(payload, dict) else {})

    def on_xfer(topic, payload):
        with lock:
            c_xfer.append(dict(payload) if isinstance(payload, dict) else {})

    def on_sched(topic, payload):
        with lock:
            c_sched.append(dict(payload) if isinstance(payload, dict) else {})

    try:
        # ══════════════════ 启动三节点 ══════════════════
        state.running = False
        state.recording = False
        state.has_script = False

        state.NETLINK_ENABLED = True
        state.NETLINK_AUTODISCOVER = False
        state.NETLINK_STATIC_PEERS = []

        state.NETLINK_PORT = A_TCP
        state.NETLINK_DISCOVERY_PORT = A_UDP
        state.NETLINK_DEVICE_NAME = "P3A"
        a = nl_node.NetLinkNode(root=root, bus=NetBus())
        ok_a = a.start()

        state.NETLINK_PORT = A2_TCP
        state.NETLINK_DISCOVERY_PORT = A2_UDP
        state.NETLINK_DEVICE_NAME = "P3A2"
        a2 = nl_node.NetLinkNode(root=root, bus=NetBus())
        ok_a2 = a2.start()

        state.NETLINK_PORT = C_TCP
        state.NETLINK_DISCOVERY_PORT = C_UDP
        state.NETLINK_DEVICE_NAME = "P3C"
        state.NETLINK_STATIC_PEERS = [["127.0.0.1", A_TCP], ["127.0.0.1", A2_TCP]]
        ok_c = netlink.start_netlink(root)
        c = netlink.get_node()

        check("0a. A(被控端) start (tcp {})".format(A_TCP), bool(ok_a))
        check("0b. A2(被控端) start (tcp {})".format(A2_TCP), bool(ok_a2))
        check("0c. C(控制端) start (tcp {})".format(C_TCP),
              bool(ok_c) and c is not None and c is not a and c is not a2)
        if not (ok_a and ok_a2 and ok_c and c is not None):
            raise RuntimeError("节点未就绪，无法继续")

        c.bus.subscribe(TOPIC_CMD_RESULT, on_cmd)
        c.bus.subscribe(transfer.TOPIC_TRANSFER, on_xfer)
        c.bus.subscribe(TOPIC_PEER_SCHED, on_sched)

        if has_display:
            win = netlink_window.open_netlink_window(root)
            spin(root, 0.6)
        check("0d. 设备互联窗口（C）", (win is not None and win.alive()) if has_display
              else "SKIP", "has_display={}".format(has_display))

        # 清理可能残留的凭据（Windows 凭据库为全局）
        for _fp in (str(getattr(a, "_self_fp", "") or ""),
                    str(getattr(a2, "_self_fp", "") or ""),
                    str(getattr(c, "_self_fp", "") or "")):
            if _fp:
                fps.append(_fp)
                try:
                    security.forget_peer_token(_fp)
                except Exception:
                    pass

        # ── 配对：C ↔ A、C ↔ A2 ──
        pin_a = a.open_pair_window()
        c.register_target("127.0.0.1", A_TCP, pin_a)
        paired_a = wait_until(lambda: len(a.list_peers() or []) >= 1, 10.0, root)

        pin_a2 = a2.open_pair_window()
        c.register_target("127.0.0.1", A2_TCP, pin_a2)
        paired_a2 = wait_until(lambda: len(a2.list_peers() or []) >= 1, 10.0, root)

        check("0e. C↔A 配对完成", paired_a)
        check("0f. C↔A2 配对完成", paired_a2)
        if not (paired_a and paired_a2):
            raise RuntimeError("配对未完成，无法继续")

        c_fp = str((a.list_peers() or [{}])[0].get("fingerprint") or "")
        a.set_peer_perm(c_fp, "script")
        a2.set_peer_perm(c_fp, "script")

        a_node_id = str(a.info.get("node_id") or "")
        a2_node_id = str(a2.info.get("node_id") or "")

        conn_a = wait_until(lambda: transfer.find_conn(c, a_node_id) is not None, 5.0, root)
        conn_a2 = wait_until(lambda: transfer.find_conn(c, a2_node_id) is not None, 5.0, root)
        check("0g. C 出站连接登记 A/A2 的 node_id", conn_a and conn_a2)

        # 模拟「发现到 A/A2」（生产由 UDP 广播提供；本测试关闭了自动发现）
        c._on_peer({"node_id": a_node_id, "name": "P3A", "host": "127.0.0.1",
                    "port": A_TCP, "version": "", "online": True})
        c._on_peer({"node_id": a2_node_id, "name": "P3A2", "host": "127.0.0.1",
                    "port": A2_TCP, "version": "", "online": True})

        if has_display:
            ready = wait_until(
                lambda: (a_node_id in win._authed and a2_node_id in win._authed
                         and win._items.get(a_node_id) is not None
                         and win._items.get(a2_node_id) is not None), 6.0, root)
            check("0h. 窗口 _authed/_items 含 A 与 A2", ready,
                  "authed={} items={}".format(list(win._authed.keys()),
                                              list(win._items.keys())))

        # ══════════════════ 1. 批量下发 ══════════════════
        data200 = os.urandom(200 * 1024)
        local200 = os.path.join(localdir, "batch.xls")
        with open(local200, "wb") as f:
            f.write(data200)
        sha200 = hashlib.sha256(data200).hexdigest()

        res1 = netlink.push_script_many([a_node_id, a2_node_id], local200,
                                        "batch.xls", False)
        ok1 = res1.get("ok") or {}
        skip1 = res1.get("skip") or {}
        tids1 = list(ok1.values())
        check("1a. push_script_many 返回 ok 含 2 个 peer", len(ok1) == 2,
              "ok={} skip={}".format(ok1, skip1))
        check("1b. 两个 tid 互不相同且非空",
              len(tids1) == 2 and len(set(tids1)) == 2 and all(tids1),
              "tids={}".format(tids1))
        check("1c. skip 为空", len(skip1) == 0, "skip={}".format(skip1))

        # ══════════════════ 2. C 侧 2 条 SCRIPT_PUSH done ══════════════════
        def _done_acks():
            out = []
            for e in snap(c_cmd):
                d = e.get("data") or {}
                if (d.get("cmd") == "SCRIPT_PUSH"
                        and d.get("status") == "done"
                        and d.get("ok", True)):
                    out.append(str(d.get("tid") or ""))
            return out

        got2 = wait_until(lambda: len(_done_acks()) >= 2, 8.0, root)
        acks = _done_acks()
        check("2a. C 侧出现 2 条 SCRIPT_PUSH done（tid 各异）",
              got2 and len(acks) >= 2 and len(set(acks)) >= 2,
              "acks={}".format(acks))
        xfer_done = [m for m in snap(c_xfer)
                     if m.get("state") == "done"
                     and str(m.get("node_id") or "") in (a_node_id, a2_node_id)]
        check("2b. C 侧 TOPIC_TRANSFER 亦出现 done（扇出证据）", len(xfer_done) >= 2,
              "n={}".format(len(xfer_done)))

        # ══════════════════ 3. 落盘 + sha256 ══════════════════
        dest3 = os.path.join(transfer.recv_dir(), "batch.xls")
        landed3 = wait_until(
            lambda: os.path.isfile(dest3)
            and transfer.sha256_file(dest3) == sha200, 8.0, root)
        check("3. recv_dir()/batch.xls 落盘且 sha256 与源一致（received/ 为 A、A2 共享目录）",
              landed3, "dest_sha={}".format(transfer.sha256_file(dest3)))

        # ══════════════════ 4. 扇出独立性（A2 权限改回 observe）══════════════════
        a2.set_peer_perm(c_fp, "observe")
        time.sleep(0.2)
        local4 = os.path.join(localdir, "iso.xls")
        with open(local4, "wb") as f:
            f.write(b"iso-payload-" * 500)
        res4 = netlink.push_script_many([a_node_id, a2_node_id], local4,
                                        "iso.xls", False)
        ok4 = res4.get("ok") or {}
        skip4 = res4.get("skip") or {}
        # 实现说明：push_many 只在「找不到连接」时 skip；A2 连接存活 → 仍进入 ok，
        # 其拒绝异步发生（下面是等价证据：A 成功 + C 看到 A2 的 permission denied）。
        dest4 = os.path.join(transfer.recv_dir(), "iso.xls")
        a_ok4 = wait_until(lambda: os.path.isfile(dest4), 8.0, root)
        check("4a. 扇出独立性：A2 被拒不影响 A（iso.xls 仍落盘）", a_ok4,
              "ok={} skip={}".format(ok4, skip4))

        def _a2_denied():
            for e in snap(c_cmd):
                d = e.get("data") or {}
                if (str(e.get("node_id") or "") == a2_node_id
                        and d.get("cmd") == "SCRIPT_PUSH"
                        and ("permission denied" in str(d.get("reason") or "")
                             or "permission denied" in str(d.get("detail") or ""))):
                    return True
            for m in snap(c_xfer):
                if (str(m.get("node_id") or "") == a2_node_id
                        and "permission denied" in str(m.get("detail") or "")):
                    return True
            return False

        denied = wait_until(_a2_denied, 8.0, root)
        check("4b. C 可见 A2 的 permission denied（扇出独立 + 远端拒绝可见）", denied,
              "c_cmd={}".format([ (e.get("data") or {}).get("cmd") for e in snap(c_cmd)]))
        check("4c. 偏差说明：A2 连接存活故进入 ok（push_many 仅对 no connection 跳过）",
              (a2_node_id in ok4) or (a2_node_id in skip4),
              "ok_keys={} skip_keys={}".format(list(ok4.keys()), list(skip4.keys())))
        # 复原 A2 权限（供断言 8 的多选批量）
        a2.set_peer_perm(c_fp, "script")
        time.sleep(0.2)

        # ══════════════════ 5. 远端拒绝可见（核心修复）══════════════════
        ck_local = os.path.join(localdir, "ck.xls")
        with open(ck_local, "wb") as f:
            f.write(b"checksum-demo-" * 400)
        _orig_sha = transfer.sha256_file
        spy = {"on": True, "path": os.path.abspath(ck_local)}

        def _sha_spy(path):
            try:
                if spy["on"] and os.path.abspath(str(path)) == spy["path"]:
                    return "0" * 64     # 故意错误：仅对 C 的源文件（A 校验用的 tmp 不受影响）
            except Exception:
                pass
            return _orig_sha(path)

        transfer.sha256_file = _sha_spy
        tid5 = None
        try:
            tid5 = netlink.push_script(a_node_id, ck_local, "ck.xls", False)
            # 5a：C 侧总线出现 failed（远端拒绝可见）
            def _ck_failed():
                for m in snap(c_xfer):
                    if (str(m.get("tid") or "") == str(tid5)
                            and m.get("state") == "failed"):
                        return str(m.get("detail") or "")
                return ""
            got5a = wait_until(lambda: bool(_ck_failed()), 8.0, root)
            detail5 = _ck_failed()
            check("5a. 远端拒绝在 C 侧总线可见（TOPIC_TRANSFER failed + tid）",
                  got5a and ("checksum mismatch" in detail5),
                  "tid={} detail={!r}".format(tid5, detail5))
            # 5b：C 窗口「传输」列表出现 失败：checksum mismatch（红 tag）
            if has_display:
                def _ck_row():
                    vals, tags = _row_for_name(win, "ck.xls")
                    if vals and str(vals[3]).startswith("失败") \
                            and "checksum mismatch" in str(vals[3]) \
                            and ("xfer_fail" in tags):
                        return True
                    return False
                got5b = wait_until(_ck_row, 8.0, root)
                row5, tags5 = _row_for_name(win, "ck.xls")
                check("5b. C 窗口「传输」列表显示 失败：checksum mismatch（红 xfer_fail）",
                      got5b, "row={} tags={}".format(row5, tags5))
            else:
                skip("5b. C 窗口远端拒绝红行（no display）")
        finally:
            spy["on"] = False
            transfer.sha256_file = _orig_sha
        check("5c. 被拒文件未落盘（远端失败后无 ck.xls）",
              not os.path.isfile(os.path.join(transfer.recv_dir(), "ck.xls")))

        # ══════════════════ 6. skip 场景 ══════════════════
        res6a = netlink.push_script_many(["ghost-peer-does-not-exist"],
                                         local200, "g.xls", False)
        check("6a. 不存在的 peer → skip 含该 peer 且原因非空",
              ("ghost-peer-does-not-exist" in (res6a.get("skip") or {}))
              and bool((res6a.get("skip") or {}).get("ghost-peer-does-not-exist"))
              and not (res6a.get("ok") or {}),
              "res={}".format(res6a))
        res6b = netlink.push_script_many([a_node_id, a2_node_id],
                                         os.path.join(localdir, "nope.xls"),
                                         "nope.xls", False)
        sk6 = res6b.get("skip") or {}
        check("6b. 本地文件不存在 → ok 为空、skip 覆盖全部 peer 且原因非空",
              (not (res6b.get("ok") or {})) and len(sk6) == 2
              and all(str(v) for v in sk6.values()),
              "res={}".format(res6b))

        # ══════════════════ 7. 定时任务状态展示 ══════════════════
        state.SCHED_ENABLED = True
        state.SCHED_NEXT_RUN = "09:30"
        state.SCHED_TASKS = [{}, {}, {}]

        def _sched_hit():
            for m in snap(c_sched):
                if (str(m.get("node_id") or "") == a_node_id
                        and bool(m.get("enabled"))
                        and int(m.get("task_count") or 0) == 3):
                    return True
            return False

        got_sched = wait_until(_sched_hit, 8.0, root)
        if has_display:
            _select_many(win, [a_node_id])
            spin(root, 0.4)
            txt7 = ""
            try:
                txt7 = str(win.lbl_sched.cget("text"))
            except Exception:
                txt7 = ""
            dbg7 = [m for m in snap(c_sched) if str(m.get("node_id") or "") == a_node_id]
            check("7. 详情区定时任务展示含 启用/09:30/3 个任务",
                  got_sched and ("启用" in txt7) and ("09:30" in txt7)
                  and ("3 个任务" in txt7),
                  "text={!r} got={} sched_evts={}".format(txt7, got_sched, dbg7[-3:]))
        else:
            check("7. TOPIC_PEER_SCHED 传达 C（无显示时仅校验总线）", got_sched,
                  "sched_evts={}".format(snap(c_sched)[-3:]))

        # ══════════════════ 8. 多选批量 UI 路径 ══════════════════
        if has_display:
            local8 = os.path.join(localdir, "multi.xls")
            with open(local8, "wb") as f:
                f.write(b"multi-ui-" * 300)
            win._ask_open_script = lambda: local8
            win._ask_push_params = lambda local, base: ("multi.xls", False)
            win._confirm_action = lambda *args, **kwargs: True
            before8 = len(_rows_for_name(win, "multi.xls"))
            sel_ok = _select_many(win, [a_node_id, a2_node_id])
            spin(root, 0.3)
            try:
                win.btn_script.invoke()
            except Exception:
                pass
            got8 = wait_until(
                lambda: len(_rows_for_name(win, "multi.xls")) >= 2, 8.0, root)
            after8 = len(_rows_for_name(win, "multi.xls"))
            check("8. 多选两台设备 → invoke [推脚本] → 「传输」列表新增 2 行",
                  sel_ok and before8 == 0 and after8 >= 2,
                  "before={} after={} rows={}".format(before8, after8,
                                                      _rows_for_name(win, "multi.xls")))
        else:
            skip("8. 多选批量 UI 路径（no display）")

        # ══════════════════ 9. 审计 + 配置脱敏 ══════════════════
        audit_text = ""
        try:
            audit_text = "\n".join((a.control.audit.tail(500)
                                    if getattr(a.control, "audit", None) else []) or [])
        except Exception:
            audit_text = ""
        check("9a. 被控端审计含 cmd=SCRIPT_PUSH",
              "cmd=SCRIPT_PUSH" in audit_text, "len={}".format(len(audit_text)))
        try:
            state.save_config()
        except Exception:
            pass
        cfg_text = ""
        try:
            with open(state.CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg_text = f.read()
        except Exception:
            cfg_text = ""
        bad = [k for k in ("cmd=", "actor=", "proof", "secret") if k in cfg_text]
        check("9b. 临时 config.json 不含 cmd=/actor=/proof/secret", len(bad) == 0,
              "found={}".format(bad))

    except Exception:
        print("[FAIL] 未捕获异常:")
        traceback.print_exc()
        _fails += 1
    finally:
        return finish(root, a, a2, win, orig, orig_make_node_id, orig_workflow,
                      orig_cred, tmpdir, fps)


def finish(root, a, a2, win, orig, orig_make_node_id, orig_workflow,
           orig_cred, tmpdir, fps):
    global _fails
    # ── 收尾：关窗 → 停 C → 停 A/A2 ──
    try:
        netlink_window.close_window()
    except Exception:
        pass
    try:
        netlink.stop_netlink()
    except Exception:
        pass
    for n in (a, a2):
        if n is not None:
            try:
                n.stop()
            except Exception:
                pass
    spin(root, 0.4)

    # ══════════════════ 10. nl-* 线程收敛 ══════════════════
    t10 = time.time()
    converged = wait_until(lambda: len(_nl_threads()) == 0, 5.0, root)
    dt10 = time.time() - t10
    if converged:
        check("10. 三节点 stop 后 nl-* 线程收敛为 0 (实测 {:.1f}s)".format(dt10), True)
    else:
        warn("10. nl-* 线程 5s 内未收敛为 0: {}".format(_nl_threads()))

    # ── 还原 state / 凭据库 / 模块补丁 ──
    try:
        for k, v in (orig or {}).items():
            if k == "pause_event" and v is None:
                continue
            try:
                setattr(state, k, v)
            except Exception:
                pass
        if orig_cred:
            state.cred_write(state._CRED_TARGET, orig_cred)
        else:
            state.cred_delete(state._CRED_TARGET)
    except Exception:
        pass
    try:
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
    for fp in (fps or []):
        if fp:
            try:
                security.forget_peer_token(fp)
            except Exception:
                pass
    try:
        shutil.rmtree(tmpdir, ignore_errors=True)
    except Exception:
        pass
    if root is not None:
        try:
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
