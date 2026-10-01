# -*- coding: utf-8 -*-
"""NetLink Phase 3-1a 脚本分发内核专项自测 —— 全自动、不弹窗、总时长 < 60s。

运行:  python tools/_test_netlink_transfer.py
退出码: 0 = 全部断言通过（允许 [WARN]）；1 = 存在 [FAIL]

要点:
  * 工具函数（is_valid_script_name / resolve_script / sha256_file / list_scripts）；
  * 双节点 A(被控端)+B(控制端)，走真实配对 + perm=script，再跑 SCRIPT_LIST / 正常推送；
  * 手动原始 socket 精确构造 BLOB_BEGIN/CHUNK/END 触发各类失败分支；
  * 不写项目 config.json：state.CONFIG_PATH 指向临时文件，测后还原；
  * state.NETLINK_SCRIPT_DIR 指向临时目录（不污染项目目录）；
  * 端口 199xx 段（19980/19981/19990/19991），用完释放；清理 Windows 凭据库 token。

15 项断言:
  1  工具函数              2  list_scripts            3  SCRIPT_LIST 链路
  4  正常推送+sha256       5  分块数量/seq 递增        6  校验和不匹配
  7  大小超限              8  非法文件名               9  分块乱序
  10 重复 tid              11 传输中断清理             12 推完即运行(run=True)
  13 权限拒绝              14 审计 + TOPIC_TRANSFER     15 打包模块清单
"""
import base64
import hashlib
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import traceback

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
import netlink.node as nl_node
from netlink.bus import NetBus
from netlink.node import NetLinkNode, TOPIC_CMD_RESULT
from netlink import security, transfer
from netlink.protocol import (
    make_msg, frame_message, FrameReader, parse_msg,
    T_HELLO, T_AUTH, T_AUTH_OK, T_AUTH_FAIL, T_AUTH_CHALLENGE,
    T_CMD_ACK, T_CMD_ERR, T_BLOB_BEGIN, T_BLOB_CHUNK, T_BLOB_END,
)

# ── 端口（199xx 段）──
A_TCP, A_UDP = 19980, 19981
B_TCP, B_UDP = 19990, 19991
TOTAL_BUDGET = 60.0

# ── 原始控制端身份 ──
CLI_ID = "xfercli000000001"
CLI_NAME = "XferCli"
CLI_FP = security.make_fingerprint(CLI_ID, CLI_NAME)

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


def wait_until(fn, timeout):
    """带 deadline 的轮询；返回 (是否满足, 实测耗时秒)。"""
    t = time.time()
    end = t + float(timeout)
    while time.time() < end:
        try:
            if fn():
                return True, time.time() - t
        except Exception:
            pass
        time.sleep(0.02)
    return False, time.time() - t


# ── 原始 socket 客户端 ────────────────────────────────────────────────────
class RawClient(object):
    def __init__(self, port, host="127.0.0.1"):
        self.s = socket.create_connection((host, int(port)), timeout=3.0)
        self.reader = FrameReader()
        self.inbox = []

    def send(self, msg):
        try:
            self.s.sendall(frame_message(msg))
            return True
        except Exception:
            return False

    def recv(self, timeout=2.0):
        if self.inbox:
            return self.inbox.pop(0)
        deadline = time.time() + max(0.0, float(timeout))
        while time.time() < deadline:
            self.s.settimeout(max(0.05, deadline - time.time()))
            try:
                chunk = self.s.recv(65536)
            except socket.timeout:
                break
            except Exception:
                return None
            if not chunk:
                return None
            try:
                for p in self.reader.feed(chunk):
                    self.inbox.append(parse_msg(p))
            except Exception:
                return None
            if self.inbox:
                return self.inbox.pop(0)
        return None

    def wait_any(self, types, timeout=2.0):
        types = tuple(types)
        deadline = time.time() + float(timeout)
        while time.time() < deadline:
            m = self.recv(deadline - time.time())
            if m is None:
                continue
            if m.get("t") in types:
                return m
        return None

    def wait(self, t, timeout=2.0):
        return self.wait_any((t,), timeout)

    def close(self):
        try:
            self.s.close()
        except Exception:
            pass


def connect(port):
    c = RawClient(port)
    c.wait(T_HELLO, 2.0)     # 吞掉被控端主动下发的 HELLO
    return c


def do_pair(c, pin):
    """走 1~3 步配对；返回 (resp_msg, token)。"""
    c.send(make_msg(T_AUTH, {"mode": "pair", "node_id": CLI_ID,
                             "fingerprint": CLI_FP, "name": CLI_NAME}))
    ch = c.wait(T_AUTH_CHALLENGE, 2.0)
    if ch is None:
        return None, None
    d = ch.get("data") or {}
    nonce = str(d.get("nonce") or "")
    salt = str(d.get("salt") or "")
    token = security.derive_token(pin, salt)
    ts = int(time.time())
    proof = security.make_proof(token, nonce, CLI_ID, ts)
    c.send(make_msg(T_AUTH, {"nonce": nonce, "proof": proof, "ts": ts}))
    resp = c.wait_any((T_AUTH_OK, T_AUTH_FAIL), 2.0)
    return resp, token


# ── 临时脚本根工具 ────────────────────────────────────────────────────────
def _recv_parts():
    """recv_dir 下残留的 .tmp_*.part 列表。"""
    d = transfer.recv_dir()
    try:
        return [f for f in os.listdir(d)
                if f.startswith(".tmp_") and f.endswith(".part")]
    except Exception:
        return []


def new_tid():
    return os.urandom(8).hex()


# ══════════════════════════════════════════════════════════════════════════
def main():
    global _fails
    print("=" * 66)
    print("NetLink Phase 3-1a script transfer kernel self-test")
    print("=" * 66)

    # ── 环境隔离：备份 ──
    saved = {}
    for name in ("CONFIG_PATH", "NETLINK_PEERS", "NETLINK_REQUIRE_AUTH",
                 "NETLINK_SCRIPT_DIR", "NETLINK_CONFIRM_CONTROL",
                 "NETLINK_CONFIRMED_PEERS", "API_KEY", "running", "quit2",
                 "quit3", "recording", "has_script", "filename", "script_dir"):
        saved[name] = getattr(state, name, None)
    orig_cred = None
    try:
        orig_cred = state.cred_read(state._CRED_TARGET)
    except Exception:
        orig_cred = None

    tmpdir = tempfile.mkdtemp(prefix="acrpa_xfer_test_")
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

    orig_make_node_id = nl_node.make_node_id
    run_tag = os.urandom(4).hex()
    seq = {"n": 0}

    def _mk_id():
        seq["n"] += 1
        return "p3a{}{}".format(run_tag, seq["n"])

    nl_node.make_node_id = _mk_id

    a = None
    b = None
    forget_fps = []
    lock = threading.Lock()
    cmd_events = []
    xfer_a = []
    xfer_b = []
    chunk_count = {"n": 0, "seqs": []}

    def snap(lst):
        with lock:
            return list(lst)

    try:
        # ══════════════════ 1. 工具函数 ══════════════════
        pos = [("a.xls", True), ("received/b.xlsx", True), ("data.json", True)]
        neg = [("../a.xls", False), ("a/b.xls", False), ("a.exe", False),
               ("received/", False), ("a.xls/json", False)]
        ok1 = all(transfer.is_valid_script_name(n) == v for n, v in pos + neg)
        check("1a. is_valid_script_name 正/反例（含 a/b.xls 为反例）", ok1,
              "pos={} neg={}".format(
                  [transfer.is_valid_script_name(n) for n, _ in pos],
                  [transfer.is_valid_script_name(n) for n, _ in neg]))
        check("1b. resolve_script 不存在文件 → None",
              transfer.resolve_script("no_such_file.xls") is None)
        blob = b"\x00\x01binary\xff" * 1000
        fp1 = os.path.join(tmpdir, "hash.bin")
        with open(fp1, "wb") as f:
            f.write(blob)
        check("1c. sha256_file 与 hashlib 一致",
              transfer.sha256_file(fp1) == hashlib.sha256(blob).hexdigest())

        # ══════════════════ 2. list_scripts() ══════════════════
        with open(os.path.join(scriptdir, "x.xls"), "wb") as f:
            f.write(b"root-x")
        with open(os.path.join(scriptdir, "received", "y.xls"), "wb") as f:
            f.write(b"recv-y")
        listed = transfer.list_scripts()
        names = [it.get("name") for it in listed]
        dirs = dict((it.get("name"), it.get("dir")) for it in listed)
        has_meta = all(("size" in it and "mtime" in it) for it in listed)
        check("2. list_scripts 返回 2 条(x.xls/root, received/y.xls/received) 且含 size/mtime",
              len(listed) == 2 and names == ["received/y.xls", "x.xls"]
              and dirs.get("x.xls") == "root"
              and dirs.get("received/y.xls") == "received" and has_meta,
              "listed={}".format(listed))

        # ══════════════════ 启动节点 + 配对 + perm=script ══════════════════
        state.NETLINK_ENABLED = True
        state.NETLINK_PORT = A_TCP
        state.NETLINK_DISCOVERY_PORT = A_UDP
        state.NETLINK_AUTODISCOVER = False
        state.NETLINK_DEVICE_NAME = "XferA"
        state.NETLINK_STATIC_PEERS = []
        state.running = False
        state.recording = False

        a = NetLinkNode(root=None, bus=NetBus())
        ok_a = a.start()
        check("0. 被控端 A start (tcp {})".format(A_TCP), bool(ok_a))

        state.NETLINK_PORT = B_TCP
        state.NETLINK_DISCOVERY_PORT = B_UDP
        state.NETLINK_DEVICE_NAME = "XferB"
        state.NETLINK_STATIC_PEERS = [["127.0.0.1", A_TCP]]
        b = NetLinkNode(root=None, bus=NetBus())
        ok_b = b.start()
        check("0b. 控制端 B start (tcp {}, 静态对端→A)".format(B_TCP), bool(ok_b))

        if not ok_a or not ok_b:
            print("[FAIL] 节点未就绪，终止")
            return finish(tmpdir, a, b, saved, orig_make_node_id,
                          orig_cred, forget_fps)

        # 收集器
        def on_cmd(topic, payload):
            with lock:
                cmd_events.append(dict(payload) if isinstance(payload, dict) else {})

        def on_xa(topic, payload):
            with lock:
                xfer_a.append(dict(payload) if isinstance(payload, dict) else {})

        def on_xb(topic, payload):
            with lock:
                xfer_b.append(dict(payload) if isinstance(payload, dict) else {})

        b.bus.subscribe(TOPIC_CMD_RESULT, on_cmd)
        a.bus.subscribe("netlink.transfer", on_xa)
        b.bus.subscribe("netlink.transfer", on_xb)

        # 插桩：统计 A 收到的 BLOB_CHUNK 数量 / seq
        orig_on_chunk = a._incoming.on_chunk

        def spy_on_chunk(conn, data):
            try:
                with lock:
                    chunk_count["n"] += 1
                    chunk_count["seqs"].append((data or {}).get("seq"))
            except Exception:
                pass
            return orig_on_chunk(conn, data)

        a._incoming.on_chunk = spy_on_chunk

        for _fp in (str(getattr(a, "_self_fp", "") or ""),
                    str(getattr(b, "_self_fp", "") or ""), CLI_FP):
            if _fp:
                forget_fps.append(_fp)
                try:
                    security.forget_peer_token(_fp)
                except Exception:
                    pass

        pin = a.open_pair_window()
        b.register_target("127.0.0.1", A_TCP, pin)
        paired, dtp = wait_until(lambda: len(a.list_peers() or []) >= 1, 8.0)
        check("0c. 配对完成（A 白名单出现 B, {:.2f}s）".format(dtp), paired)
        if not paired:
            print("[FAIL] 配对未完成，终止")
            return finish(tmpdir, a, b, saved, orig_make_node_id,
                          orig_cred, forget_fps)
        b_fp = str((a.list_peers() or [{}])[0].get("fingerprint") or "")
        a_node_id = str(a.info.get("node_id") or "")
        a.set_peer_perm(b_fp, "script")
        # 等 B 侧出站连接登记 A 的 node_id
        wait_until(lambda: transfer.find_conn(b, a_node_id) is not None, 3.0)

        # ══════════════════ 3. SCRIPT_LIST 链路 ══════════════════
        sent3 = b.list_remote_scripts(a_node_id)
        check("3a. B.list_remote_scripts(A_node_id) 发送成功", bool(sent3))
        ok3, dt3 = wait_until(
            lambda: any(m.get("state") == "list" for m in snap(xfer_b)), 3.0)
        got_list = None
        for m in snap(xfer_b):
            if m.get("state") == "list":
                got_list = m.get("scripts")
        check("3b. B 的 TOPIC_TRANSFER 收到 state=list 且 scripts 含 2 条（{:.2f}s）".format(dt3),
              ok3 and isinstance(got_list, list) and len(got_list) == 2,
              "scripts={}".format(got_list))

        # ══════════════════ 4. 正常推送（200KB）+ 5. 分块正确性 ══════════════════
        data200 = os.urandom(200 * 1024)
        local200 = os.path.join(localdir, "pushed.xls")
        with open(local200, "wb") as f:
            f.write(data200)
        sha200 = hashlib.sha256(data200).hexdigest()

        with lock:
            chunk_count["n"] = 0
            chunk_count["seqs"] = []
        tid4 = b.push_script(a_node_id, local200, remote_name="pushed.xls")
        check("4a. push_script 返回非空 tid", isinstance(tid4, str) and len(tid4) > 0,
              "tid={!r}".format(tid4))

        dest = os.path.join(transfer.recv_dir(), "pushed.xls")
        ok4, dt4 = wait_until(
            lambda: os.path.isfile(dest) and transfer.sha256_file(dest) == sha200, 8.0)
        check("4b. A recv_dir/pushed.xls 落盘且内容逐字节一致(sha256 相同, {:.2f}s)".format(dt4),
              ok4, "dest_sha={}".format(transfer.sha256_file(dest)))

        ok4c, _ = wait_until(lambda: any(
            (e.get("data") or {}).get("cmd") == "SCRIPT_PUSH"
            and (e.get("data") or {}).get("status") == "done"
            for e in snap(cmd_events)), 3.0)
        detail4 = ""
        for e in snap(cmd_events):
            d = e.get("data") or {}
            if d.get("cmd") == "SCRIPT_PUSH" and d.get("status") == "done":
                detail4 = str(d.get("detail") or "")
        check("4c. B 收到 CMD_ACK status=done 且 detail 含 received/pushed.xls",
              ok4c and "received/pushed.xls" in detail4, "detail={!r}".format(detail4))

        expect_chunks = (len(data200) + transfer.CHUNK_RAW - 1) // transfer.CHUNK_RAW
        with lock:
            n_chunks = chunk_count["n"]
            seqs = list(chunk_count["seqs"])
        check("5a. A 收到 BLOB_CHUNK 数量 == ceil(size/CHUNK_RAW) = {}".format(expect_chunks),
              n_chunks == expect_chunks, "got={} expect={}".format(n_chunks, expect_chunks))
        check("5b. 分块 seq 严格递增 0..N-1",
              seqs == list(range(len(seqs))) and len(seqs) == expect_chunks,
              "seqs={}".format(seqs))

        # ══════════════════ 14. 审计 + TOPIC_TRANSFER ══════════════════
        states_a = [m.get("state") for m in snap(xfer_a)]
        check("14a. A 发布 TOPIC_TRANSFER 含 sending 与 done",
              ("sending" in states_a) and ("done" in states_a),
              "states={}".format(states_a[:12]))
        audit_text = "\n".join((a.control.audit.tail(200) if a.control.audit else []) or [])
        check("14b. A 审计文件含 cmd=SCRIPT_PUSH 与 result=",
              ("cmd=SCRIPT_PUSH" in audit_text) and ("result=" in audit_text),
              "len={}".format(len(audit_text)))

        # ══════════════════ 原始控制端（脚本权限）══════════════════
        rc = connect(A_TCP)
        pin2 = a.open_pair_window()
        resp_r, _tok = do_pair(rc, pin2)
        check("R0. 原始控制端配对成功(AUTH_OK)",
              resp_r is not None and resp_r.get("t") == T_AUTH_OK,
              "resp={}".format((resp_r or {}).get("t")))
        a.set_peer_perm(CLI_FP, "script")
        content = b"hello-xfer-payload"
        sha_content = hashlib.sha256(content).hexdigest()
        wrong_sha = "deadbeef" * 8

        # ══════════════════ 6. 校验和不匹配 ══════════════════
        tid6 = new_tid()
        rc.send(make_msg(T_BLOB_BEGIN, {"tid": tid6, "name": "ok.xls",
                                        "size": len(content), "sha256": wrong_sha,
                                        "run": False, "actor": "raw"}))
        rc.send(make_msg(T_BLOB_CHUNK, {"tid": tid6, "seq": 0,
                                        "data_b64": base64.b64encode(content).decode("ascii")}))
        rc.send(make_msg(T_BLOB_END, {"tid": tid6, "seq_total": 1, "sha256": wrong_sha}))
        e6 = rc.wait(T_CMD_ERR, 3.0)
        r6 = str(((e6 or {}).get("data") or {}).get("reason") or "")
        check("6a. 校验和不匹配 → CMD_ERR reason=checksum mismatch",
              e6 is not None and r6 == "checksum mismatch", "reason={!r}".format(r6))
        check("6b. 无目标文件且无 .tmp_*.part 残留",
              (not os.path.isfile(os.path.join(transfer.recv_dir(), "ok.xls")))
              and (not _recv_parts()), "parts={}".format(_recv_parts()))

        # ══════════════════ 7. 大小超限 ══════════════════
        tid7 = new_tid()
        rc.send(make_msg(T_BLOB_BEGIN, {"tid": tid7, "name": "big.xls",
                                        "size": transfer.MAX_SCRIPT_BYTES + 1,
                                        "sha256": "0" * 64, "run": False, "actor": "raw"}))
        e7 = rc.wait(T_CMD_ERR, 3.0)
        r7 = str(((e7 or {}).get("data") or {}).get("reason") or "")
        check("7. 声明大小超限 → CMD_ERR reason=file too large",
              e7 is not None and r7 == "file too large", "reason={!r}".format(r7))

        # ══════════════════ 8. 非法文件名 ══════════════════
        tid8 = new_tid()
        rc.send(make_msg(T_BLOB_BEGIN, {"tid": tid8, "name": "../../evil.xls",
                                        "size": 1, "sha256": "0" * 64,
                                        "run": False, "actor": "raw"}))
        e8 = rc.wait(T_CMD_ERR, 3.0)
        r8 = str(((e8 or {}).get("data") or {}).get("reason") or "")
        challenge = (e8 is not None and r8 == "invalid script name")
        tid8b = new_tid()
        rc.send(make_msg(T_BLOB_BEGIN, {"tid": tid8b, "name": "a.exe",
                                        "size": 1, "sha256": "0" * 64,
                                        "run": False, "actor": "raw"}))
        e8b = rc.wait(T_CMD_ERR, 3.0)
        r8b = str(((e8b or {}).get("data") or {}).get("reason") or "")
        check("8. 非法文件名(../../evil.xls 与 a.exe) → invalid script name",
              challenge and e8b is not None and r8b == "invalid script name",
              "r1={!r} r2={!r}".format(r8, r8b))

        # ══════════════════ 9. 分块乱序 ══════════════════
        tid9 = new_tid()
        rc.send(make_msg(T_BLOB_BEGIN, {"tid": tid9, "name": "ok9.xls",
                                        "size": len(content), "sha256": sha_content,
                                        "run": False, "actor": "raw"}))
        rc.send(make_msg(T_BLOB_CHUNK, {"tid": tid9, "seq": 1,
                                        "data_b64": base64.b64encode(content).decode("ascii")}))
        e9 = rc.wait(T_CMD_ERR, 3.0)
        r9 = str(((e9 or {}).get("data") or {}).get("reason") or "")
        check("9. 分块乱序(先 seq=1) → chunk out of order",
              e9 is not None and r9 == "chunk out of order", "reason={!r}".format(r9))

        # ══════════════════ 10. 重复 tid ══════════════════
        tid10 = new_tid()
        beg10 = make_msg(T_BLOB_BEGIN, {"tid": tid10, "name": "dup.xls",
                                        "size": len(content), "sha256": sha_content,
                                        "run": False, "actor": "raw"})
        rc.send(beg10)
        rc.send(beg10)
        e10 = rc.wait(T_CMD_ERR, 3.0)
        r10 = str(((e10 or {}).get("data") or {}).get("reason") or "")
        check("10. 同 tid 连发两次 BLOB_BEGIN → duplicate transfer",
              e10 is not None and r10 == "duplicate transfer", "reason={!r}".format(r10))

        # ══════════════════ 13. 权限拒绝 ══════════════════
        a.set_peer_perm(CLI_FP, "observe")
        tid13 = new_tid()
        rc.send(make_msg(T_BLOB_BEGIN, {"tid": tid13, "name": "denied.xls",
                                        "size": len(content), "sha256": sha_content,
                                        "run": False, "actor": "raw"}))
        e13 = rc.wait(T_CMD_ERR, 3.0)
        r13 = str(((e13 or {}).get("data") or {}).get("reason") or "")
        check("13a. perm=observe → CMD_ERR reason 含 permission denied",
              e13 is not None and "permission denied" in r13, "reason={!r}".format(r13))
        check("13b. 权限拒绝后 recv_dir 无新增文件/无 .part 残留",
              (not os.path.isfile(os.path.join(transfer.recv_dir(), "denied.xls")))
              and (not _recv_parts()), "parts={}".format(_recv_parts()))
        a.set_peer_perm(CLI_FP, "script")     # 复原，供断言 11

        # ══════════════════ 11. 传输中断清理 ══════════════════
        tid11 = new_tid()
        rc.send(make_msg(T_BLOB_BEGIN, {"tid": tid11, "name": "abort.xls",
                                        "size": len(content), "sha256": sha_content,
                                        "run": False, "actor": "raw"}))
        rc.send(make_msg(T_BLOB_CHUNK, {"tid": tid11, "seq": 0,
                                        "data_b64": base64.b64encode(content).decode("ascii")}))
        time.sleep(0.3)
        check("11a. 中断前确实存在 .tmp_*.part（证明已建临时文件）",
              bool(_recv_parts()), "parts={}".format(_recv_parts()))
        rc.close()
        ok11, dt11 = wait_until(lambda: not _recv_parts(), 3.0)
        check("11b. conn.close() 后 ≤3s 无 .tmp_*.part 残留（{:.2f}s）".format(dt11), ok11,
              "parts={}".format(_recv_parts()))

        # ══════════════════ 12. 推完即运行（run=True）══════════════════
        run_seen = {"calls": 0, "filename": None}

        def fake_run(loops=None):
            run_seen["calls"] += 1
            try:
                run_seen["filename"] = state.filename
            except Exception:
                pass

        def fake_stop():
            pass

        a.set_control_hooks(run=fake_run, stop=fake_stop)
        state.NETLINK_CONFIRM_CONTROL = False
        state.running = False
        state.recording = False
        state.has_script = False
        local12 = os.path.join(localdir, "autorun.xls")
        with open(local12, "wb") as f:
            f.write(b"autorun-payload-" * 100)
        tid12 = b.push_script(a_node_id, local12, remote_name="autorun.xls", run=True)
        check("12a. push_script(run=True) 返回 tid", isinstance(tid12, str) and tid12)
        ok12, dt12 = wait_until(lambda: run_seen["calls"] >= 1, 5.0)
        check("12b. run=True 后 ≤3s fake_run 被调用（{:.2f}s）".format(dt12), ok12,
              "calls={}".format(run_seen["calls"]))
        dest12 = os.path.abspath(os.path.join(transfer.recv_dir(), "autorun.xls"))
        check("12c. state.filename 指向 recv_dir/autorun.xls",
              run_seen["filename"] is not None
              and os.path.abspath(str(run_seen["filename"])) == dest12,
              "filename={!r} expect={!r}".format(run_seen["filename"], dest12))

        # ══════════════════ 15. 打包模块清单 ══════════════════
        vpath = os.path.join(_ROOT, "tools", "_verify_netlink_package.py")
        vtext = ""
        try:
            with open(vpath, "r", encoding="utf-8") as f:
                vtext = f.read()
        except Exception:
            vtext = ""
        check("15a. _verify_netlink_package.py NL_MODULES 含 netlink.transfer",
              '"netlink.transfer"' in vtext or "'netlink.transfer'" in vtext)
        mods = []
        try:
            seg = vtext.split("NL_MODULES = [", 1)[1].split("]", 1)[0]
            for tok in seg.replace("\n", " ").split(","):
                tok = tok.strip().strip('"').strip("'")
                if tok:
                    mods.append(tok)
        except Exception:
            mods = []
        check("15b. NL_MODULES 交叉校验覆盖 17 项",
              len(mods) == 17 and "netlink.transfer" in mods,
              "count={} mods={}".format(len(mods), mods))

    except Exception:
        print("[FAIL] 未捕获异常:")
        traceback.print_exc()
        _fails += 1
    finally:
        return finish(tmpdir, a, b, saved, orig_make_node_id, orig_cred, forget_fps)


def finish(tmpdir, a, b, saved, orig_make_node_id, orig_cred, forget_fps):
    global _fails
    for n in (b, a):
        if n is not None:
            try:
                n.stop()
            except Exception:
                pass
    try:
        state.CONFIG_PATH = saved.get("CONFIG_PATH")
        state.NETLINK_PEERS = saved.get("NETLINK_PEERS")
        state.NETLINK_REQUIRE_AUTH = saved.get("NETLINK_REQUIRE_AUTH")
        state.NETLINK_SCRIPT_DIR = saved.get("NETLINK_SCRIPT_DIR")
        state.NETLINK_CONFIRM_CONTROL = saved.get("NETLINK_CONFIRM_CONTROL")
        state.NETLINK_CONFIRMED_PEERS = saved.get("NETLINK_CONFIRMED_PEERS")
        state.API_KEY = saved.get("API_KEY")
        state.running = saved.get("running")
        state.quit2 = saved.get("quit2")
        state.quit3 = saved.get("quit3")
        state.recording = saved.get("recording")
        state.has_script = saved.get("has_script")
        state.filename = saved.get("filename")
        state.script_dir = saved.get("script_dir")
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
    for fp in (forget_fps or []):
        if fp:
            try:
                security.forget_peer_token(fp)
            except Exception:
                pass
    try:
        shutil.rmtree(tmpdir, ignore_errors=True)
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
