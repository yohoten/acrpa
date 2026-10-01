# -*- coding: utf-8 -*-
"""ACRPA NetLink — 脚本分发内核（Phase 3-1a，纯标准库，Python 3.7 兼容）。

职责：
  * 脚本根 / 接收目录解析（state.NETLINK_SCRIPT_DIR 或 <config 目录>/scripts）；
  * 远端脚本列表（list_scripts / resolve_script / is_valid_script_name）；
  * 分块传输内核：BLOB_BEGIN / BLOB_CHUNK / BLOB_END（JSON + base64，每块原始 64KB）；
  * 被控端落盘（IncomingStore）+ 控制端发送（OutgoingTransfer）+ sha256 完整性校验；
  * 进度发布到 TOPIC_TRANSFER（"netlink.transfer"）。

设计约束：
  * 不 import utils、不顶层 import GUI；日志走 connection._nl_log，绝不回投日志总线；
  * 所有线程均为 daemon，线程体与回调整体 try/except，异常绝不向上抛；
  * 帧一律 JSON（不引入二进制魔数帧），BLOB_CHUNK 用 base64 承载原始字节；
  * 本模块不 import node（避免循环），node 顶层 import 本模块是安全的。
"""
import base64
import hashlib
import os
import re
import threading
import time
import uuid

from .connection import _nl_log
from .protocol import (
    make_msg, T_CMD_ACK, T_CMD_ERR, T_CMD_RUN_SCRIPT,
    T_BLOB_BEGIN, T_BLOB_CHUNK, T_BLOB_END,
)
from .security import perm_satisfies

try:
    import state as _state
except Exception:          # 极端环境下 state 不可用时降级（路径退化为 cwd）
    _state = None


# ── 常量 ─────────────────────────────────────────────────────────────────
CHUNK_RAW = 64 * 1024                 # 每块原始字节数（base64 后约 85KB < MAX_FRAME_BYTES）
MAX_SCRIPT_BYTES = 32 * 1024 * 1024   # 单脚本上限 32MB
RECV_SUBDIR = "received"              # 接收子目录名
ALLOWED_EXT = (".xls", ".xlsx", ".json")

TOPIC_TRANSFER = "netlink.transfer"   # 进度/列表事件主题

# 合法脚本名：根下 "a.xls" 或接收目录 "received/b.xlsx"（仅此一种斜杠前缀）
_NAME_RE = re.compile(r"^(received/)?[^/\\:*?\"<>|]+\.(xls|xlsx|json)$")


# ── 路径工具 ─────────────────────────────────────────────────────────────
def script_root():
    """允许远程运行的脚本根目录。

    state.NETLINK_SCRIPT_DIR 非空则用它；否则 <dirname(state.CONFIG_PATH)>/scripts。
    """
    d = ""
    try:
        d = str(getattr(_state, "NETLINK_SCRIPT_DIR", "") or "").strip()
    except Exception:
        d = ""
    if not d:
        try:
            cfg = str(getattr(_state, "CONFIG_PATH", "") or "")
        except Exception:
            cfg = ""
        base = os.path.dirname(cfg) if cfg else os.getcwd()
        d = os.path.join(base, "scripts")
    return d


def recv_dir():
    """接收目录 script_root()/received（不存在则创建）。"""
    d = os.path.join(script_root(), RECV_SUBDIR)
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d


def sha256_file(path):
    """文件 sha256 hexdigest；读取失败返回 ""。"""
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            while True:
                b = f.read(1024 * 1024)
                if not b:
                    break
                h.update(b)
        return h.hexdigest()
    except Exception:
        return ""


def is_valid_script_name(name):
    """合法脚本名：^(received/)?<basename>.(xls|xlsx|json)$ 且不含 ".."。"""
    try:
        n = str(name or "")
        if (not n) or (".." in n):
            return False
        return _NAME_RE.fullmatch(n) is not None
    except Exception:
        return False


def resolve_script(name):
    """把 "a.xls" / "received/b.xls" 解析为绝对路径；不合法或不存在返回 None。"""
    try:
        if not is_valid_script_name(name):
            return None
        n = str(name)
        root = script_root()
        if n.startswith(RECV_SUBDIR + "/"):
            path = os.path.join(root, RECV_SUBDIR, n[len(RECV_SUBDIR) + 1:])
        else:
            path = os.path.join(root, n)
        path = os.path.abspath(path)
        if not os.path.isfile(path):
            return None
        return path
    except Exception:
        return None


def _scan_dir(d, dtype, prefix):
    """扫描单目录下合法脚本文件；返回 list[dict]。"""
    out = []
    try:
        if not os.path.isdir(d):
            return out
        for fn in os.listdir(d):
            full = os.path.join(d, fn)
            try:
                if not os.path.isfile(full):
                    continue
                if not fn.lower().endswith(ALLOWED_EXT):
                    continue
                st = os.stat(full)
                out.append({"name": prefix + fn, "size": int(st.st_size),
                            "mtime": int(st.st_mtime), "dir": dtype})
            except Exception:
                continue
    except Exception:
        pass
    return out


def list_scripts():
    """列出脚本根 + received 下的合法脚本；按 name 排序，最多 500 条。

    每条：{"name": <相对脚本根的名字>, "size": int, "mtime": int, "dir": "root"|"received"}
    """
    out = []
    try:
        root = script_root()
        out.extend(_scan_dir(root, "root", ""))
        out.extend(_scan_dir(os.path.join(root, RECV_SUBDIR), "received",
                             RECV_SUBDIR + "/"))
        out.sort(key=lambda x: x.get("name", ""))
        return out[:500]
    except Exception as e:
        _nl_log("transfer list_scripts error: {}".format(e), "WARN")
        return []


# ── 连接查找 / 发布 / actor ───────────────────────────────────────────────
def find_conn(node, peer_node_id):
    """按 node_id 找到该 peer 的存活连接；找不到返回 None。"""
    try:
        nid = str(peer_node_id or "")
        with node._lock:
            items = list(node._conns.items())
        for conn, rec in items:
            try:
                if conn is None or not conn.alive:
                    continue
                if str((rec or {}).get("node_id") or "") == nid:
                    return conn
            except Exception:
                continue
    except Exception:
        pass
    return None


def _pub(node, payload):
    """把进度/列表事件发到 TOPIC_TRANSFER（失败静默）。"""
    try:
        bus = getattr(node, "bus", None)
        if bus is not None:
            bus.publish(TOPIC_TRANSFER, payload)
    except Exception:
        pass


def _status(node, level, msg):
    """向 TOPIC_STATUS 发布告警（避免 import node 造成循环，直接用字符串主题）。"""
    try:
        bus = getattr(node, "bus", None)
        if bus is not None:
            bus.publish("netlink.status", {"level": level, "msg": msg})
    except Exception:
        pass


def _node_id_of(node, conn):
    try:
        return str(node._conn_node_id(conn) or "")
    except Exception:
        return ""


def _actor_of(node, conn):
    """优先复用 control 的 actor 计算；失败退化为 unknown。"""
    try:
        ctl = getattr(node, "control", None)
        if ctl is not None:
            return str(ctl._actor(conn) or "")
    except Exception:
        pass
    return "unknown"


def _local_actor(node):
    try:
        name = str(node.info.get("name") or "")
    except Exception:
        name = ""
    try:
        fp = str(getattr(node, "_self_fp", "") or "")[:8]
    except Exception:
        fp = ""
    if name and fp:
        return "{}|{}".format(name, fp)
    return name or fp or "unknown"


# ── 被控端：接收并落盘 ────────────────────────────────────────────────────
class IncomingStore(object):
    """被控端：接收 BLOB_BEGIN/CHUNK/END 并落盘。线程安全。"""

    def __init__(self, node):
        self._node = node
        self._lock = threading.RLock()
        self._active = {}     # {conn: state_dict}
        self._tids = set()    # 进行中的 tid 集合

    # ── 工具 ──
    def _audit(self, actor, cmd, args, result, detail):
        try:
            ctl = getattr(self._node, "control", None)
            audit = getattr(ctl, "audit", None) if ctl is not None else None
            if audit is not None:
                audit.write(actor, cmd, args, result=result, detail=detail)
        except Exception:
            pass

    def _ack(self, conn, status, detail, actor, tid=None):
        """CMD_ACK：accepted / done / failed（Phase3-2 起统一携带 tid）。"""
        try:
            self._node.send(conn, make_msg(T_CMD_ACK, {
                "cmd": "SCRIPT_PUSH", "ok": (status != "failed"),
                "status": status, "detail": str(detail or ""),
                "actor": str(actor or ""), "mode": "",
                "tid": str(tid or "")}))
        except Exception:
            pass

    def _err(self, conn, reason, actor, tid=None):
        """T_CMD_ERR 回发起方（稳定英文 reason；Phase3-2 起携带 tid）。"""
        try:
            self._node.send(conn, make_msg(T_CMD_ERR, {
                "cmd": "SCRIPT_PUSH", "ok": False,
                "reason": str(reason or ""), "actor": str(actor or ""),
                "tid": str(tid or "")}))
        except Exception:
            pass

    def _drop_state(self, conn, tid=None):
        """取出并清理某连接的活动状态（关闭句柄 + 删临时文件）。返回 state 或 None。"""
        st = None
        with self._lock:
            st = self._active.pop(conn, None)
            if st is not None:
                self._tids.discard(st.get("tid"))
            if tid:
                self._tids.discard(tid)
        if st is not None:
            try:
                st["fh"].close()
            except Exception:
                pass
            try:
                os.remove(st["tmp"])
            except Exception:
                pass
        return st

    def _fail(self, conn, reason, tid=None):
        """失败路径：清理临时文件 + 发 CMD_ACK(failed) + CMD_ERR + 发布 TOPIC_TRANSFER。

        Phase3-2：两条回执都补上 tid，使控制端能把「远端拒绝」归并到既有传输行。
        """
        try:
            st = self._drop_state(conn, tid)
            actor = _actor_of(self._node, conn)
            name = st.get("name", "") if st else ""
            sent = int(st.get("received", 0)) if st else 0
            total = int(st.get("size", 0)) if st else 0
            use_tid = str((st.get("tid") if st else tid) or "")
            self._publish(conn, use_tid, name, sent, total, "failed", reason)
            self._ack(conn, "failed", reason, actor, use_tid)
            self._err(conn, reason, actor, use_tid)
        except Exception as e:
            _nl_log("transfer fail path error: {}".format(e), "WARN")

    def _publish(self, conn, tid, name, sent, total, state, detail):
        _pub(self._node, {"node_id": _node_id_of(self._node, conn),
                          "tid": str(tid or ""), "name": str(name or ""),
                          "sent": int(sent), "total": int(total),
                          "state": state, "detail": str(detail or "")})

    # ── 协议入口 ──
    def on_begin(self, conn, data):
        """校验 → 建临时文件 → 记 state → 回 CMD_ACK accepted。"""
        try:
            # 1) 权限防御（正常已由 node 前置门槛拦截）
            try:
                perm = self._node.perm_of(conn)
            except Exception:
                perm = None
            if not perm_satisfies(perm, "script"):
                self._fail(conn, "permission denied", str((data or {}).get("tid") or ""))
                return

            tid = str((data or {}).get("tid") or "")
            name = str((data or {}).get("name") or "")
            sha = str((data or {}).get("sha256") or "")
            run = bool((data or {}).get("run"))
            actor = _actor_of(self._node, conn)

            # 2) tid 缺失/重复
            with self._lock:
                dup = (not tid) or (tid in self._tids)
            if dup:
                self._fail(conn, "duplicate transfer", tid)
                return

            # 3) 名称合法
            if not is_valid_script_name(name):
                self._fail(conn, "invalid script name", tid)
                return

            # 4) 大小合法
            try:
                size = int((data or {}).get("size"))
            except Exception:
                size = 0
            if size <= 0 or size > MAX_SCRIPT_BYTES:
                self._fail(conn, "file too large", tid)
                return

            # 5) 建临时文件
            tmp = os.path.join(recv_dir(), ".tmp_{}.part".format(tid))
            try:
                fh = open(tmp, "wb")
            except Exception:
                self._fail(conn, "tmp write failed", tid)
                return

            st = {"tid": tid, "name": name, "size": size, "sha256": sha,
                  "run": run, "seq": 0, "received": 0, "tmp": tmp, "fh": fh,
                  "actor": actor}
            with self._lock:
                # 同一连接重复 BEGIN（不同 tid）：先清理旧的，避免句柄泄漏
                old = self._active.pop(conn, None)
                if old is not None:
                    try:
                        old["fh"].close()
                    except Exception:
                        pass
                    try:
                        os.remove(old["tmp"])
                    except Exception:
                        pass
                    self._tids.discard(old.get("tid"))
                self._active[conn] = st
                self._tids.add(tid)

            self._ack(conn, "accepted", "transfer {}".format(tid), actor, tid)
            self._publish(conn, tid, name, 0, size, "sending", "")
        except Exception as e:
            _nl_log("transfer on_begin error: {}".format(e), "WARN")

    def on_chunk(self, conn, data):
        """base64 解码追加；seq 必须递增且连续，否则失败。"""
        try:
            with self._lock:
                st = self._active.get(conn)
            if st is None:
                return  # 无进行中的传输 → 忽略
            tid = str((data or {}).get("tid") or "")
            if tid != st["tid"]:
                self._fail(conn, "chunk out of order", tid)
                return
            try:
                seq = int((data or {}).get("seq"))
            except Exception:
                seq = -1
            if seq != st["seq"]:
                self._fail(conn, "chunk out of order", tid)
                return
            try:
                raw = base64.b64decode(str((data or {}).get("data_b64") or ""),
                                       validate=True)
            except Exception:
                self._fail(conn, "bad chunk", tid)
                return
            try:
                st["fh"].write(raw)
                st["fh"].flush()
            except Exception:
                self._fail(conn, "tmp write failed", tid)
                return
            st["seq"] += 1
            st["received"] += len(raw)
            self._publish(conn, tid, st["name"], st["received"], st["size"],
                          "sending", "")
        except Exception as e:
            _nl_log("transfer on_chunk error: {}".format(e), "WARN")

    def on_end(self, conn, data):
        """校验 sha256 + 大小 → 移入 recv_dir → CMD_ACK done / 失败。"""
        try:
            with self._lock:
                st = self._active.get(conn)
            if st is None:
                return
            tid = str((data or {}).get("tid") or "")
            if tid != st["tid"]:
                self._fail(conn, "chunk out of order", tid)
                return
            try:
                st["fh"].close()
            except Exception:
                pass

            if st["received"] != st["size"]:
                self._fail(conn, "size mismatch", tid)
                return
            end_sha = str((data or {}).get("sha256") or "")
            got_sha = sha256_file(st["tmp"])
            if (not got_sha) or (got_sha != st["sha256"]) or (end_sha != st["sha256"]):
                self._fail(conn, "checksum mismatch", tid)
                return

            fname = os.path.basename(st["name"])
            dest = os.path.join(recv_dir(), fname)
            try:
                existed = os.path.isfile(dest)
                os.replace(st["tmp"], dest)
            except Exception:
                self._fail(conn, "tmp write failed", tid)
                return

            # 落盘成功：从活动表移除（临时文件已被 os.replace 消费，不可再删）
            with self._lock:
                self._active.pop(conn, None)
                self._tids.discard(tid)

            detail = "received/" + fname
            if existed:
                detail = detail + " (overwritten)"
            actor = st.get("actor") or _actor_of(self._node, conn)
            self._audit(actor, "SCRIPT_PUSH", {"name": fname, "size": st["size"]},
                        "ok", detail)
            self._ack(conn, "done", detail, actor, tid)
            self._publish(conn, tid, fname, st["received"], st["size"], "done", detail)
            if st.get("run"):
                self._run_received(conn, "received/" + fname)
        except Exception as e:
            _nl_log("transfer on_end error: {}".format(e), "WARN")

    def _run_received(self, conn, rel_name):
        """run=True 时复用既有 CMD_RUN_SCRIPT 路径执行落盘脚本。"""
        try:
            ctl = getattr(self._node, "control", None)
            if ctl is None:
                return
            ctl.handle(conn, make_msg(T_CMD_RUN_SCRIPT,
                                      {"name": rel_name, "loop": None}))
        except Exception as e:
            _nl_log("transfer run received error: {}".format(e), "WARN")

    def abort(self, conn):
        """连接断开时清理临时文件与状态（不留残 file）。"""
        try:
            st = self._drop_state(conn)
            if st is None:
                return
            actor = st.get("actor") or _actor_of(self._node, conn)
            self._audit(actor, "SCRIPT_PUSH", {"name": st.get("name", "")},
                        "err", "aborted")
            self._publish(conn, st.get("tid"), st.get("name"), st.get("received", 0),
                          st.get("size", 0), "failed", "aborted")
        except Exception as e:
            _nl_log("transfer abort error: {}".format(e), "WARN")


# ── 控制端：分块发送 ──────────────────────────────────────────────────────
class OutgoingTransfer(object):
    """控制端：把一个本地文件分块发给某个连接。"""

    def __init__(self, node, conn, local_path, remote_name=None, run=False):
        self._node = node
        self._conn = conn
        self._local = str(local_path or "")
        self._name = str(remote_name or "") or os.path.basename(self._local)
        self._run = bool(run)
        self._tid = uuid.uuid4().hex[:16]

    @property
    def tid(self):
        return self._tid

    def start(self):
        """起一个 daemon 线程执行发送；不阻塞调用方。"""
        try:
            th = threading.Thread(target=self._run_send,
                                  name="nl-transfer-{}".format(self._tid))
            th.daemon = True
            th.start()
        except Exception as e:
            _nl_log("transfer start thread error: {}".format(e), "WARN")

    def _publish(self, state, sent, total, detail=""):
        _pub(self._node, {"node_id": _node_id_of(self._node, self._conn),
                          "tid": self._tid, "name": self._name,
                          "sent": int(sent), "total": int(total),
                          "state": state, "detail": str(detail or "")})

    def _run_send(self):
        try:
            conn = self._conn
            local = self._local
            if (not local) or (not os.path.isfile(local)):
                self._publish("failed", 0, 0, "local file missing")
                return
            try:
                size = int(os.path.getsize(local))
            except Exception:
                size = 0
            if size <= 0 or size > MAX_SCRIPT_BYTES:
                self._publish("failed", 0, max(size, 0), "file too large")
                return
            sha = sha256_file(local)
            if not sha:
                self._publish("failed", 0, size, "local file missing")
                return

            actor = _local_actor(self._node)

            # ── BLOB_BEGIN ──
            ok = conn.send(make_msg(T_BLOB_BEGIN, {
                "tid": self._tid, "name": self._name, "size": size,
                "sha256": sha, "run": self._run, "actor": actor}))
            if not ok:
                self._publish("failed", 0, size, "send failed")
                _status(self._node, "warning",
                        "transfer {}: send failed".format(self._tid))
                return

            # ── BLOB_CHUNK ──
            sent = 0
            seq = 0
            fails = 0
            try:
                with open(local, "rb") as f:
                    while True:
                        raw = f.read(CHUNK_RAW)
                        if not raw:
                            break
                        b64 = base64.b64encode(raw).decode("ascii")
                        okc = conn.send(make_msg(T_BLOB_CHUNK, {
                            "tid": self._tid, "seq": seq, "data_b64": b64}))
                        if not okc:
                            fails += 1
                            if fails >= 2:
                                self._publish("failed", sent, size, "send failed")
                                _status(self._node, "warning",
                                        "transfer {}: send queue full".format(self._tid))
                                return
                        else:
                            fails = 0
                        seq += 1
                        sent += len(raw)
                        self._publish("sending", sent, size)
                        time.sleep(0.005)
            except Exception as e:
                self._publish("failed", sent, size, "local file missing")
                _nl_log("transfer read error: {}".format(e), "WARN")
                return

            # ── BLOB_END ──
            oke = conn.send(make_msg(T_BLOB_END, {
                "tid": self._tid, "seq_total": seq, "sha256": sha}))
            if not oke:
                self._publish("failed", sent, size, "send failed")
                _status(self._node, "warning",
                        "transfer {}: send failed".format(self._tid))
                return
            self._publish("done", sent, size)
        except Exception as e:
            _nl_log("transfer run_send fatal: {}".format(e), "ERROR")
            try:
                self._publish("failed", 0, 0, "internal error")
            except Exception:
                pass


# ── 便捷入口 ─────────────────────────────────────────────────────────────
def push(node, peer_node_id, local_path, remote_name=None, run=False):
    """找到该 peer 的存活已认证连接 → 建 OutgoingTransfer 并 start()；返回 tid。

    找不到连接返回 None。
    """
    try:
        conn = find_conn(node, peer_node_id)
        if conn is None:
            return None
        ot = OutgoingTransfer(node, conn, local_path, remote_name, run)
        ot.start()
        return ot.tid
    except Exception as e:
        _nl_log("transfer push error: {}".format(e), "WARN")
        return None


def check_local_source(local_path, remote_name=None):
    """本地文件预校验（批量下发先短路用）；通过返回 ""，否则返回英文原因。"""
    try:
        local = str(local_path or "")
        if (not local) or (not os.path.isfile(local)):
            return "local file missing"
        name = str(remote_name or "") or os.path.basename(local)
        if (not is_valid_script_name(name)) or (".." in name):
            return "invalid script name"
        if os.path.splitext(name)[1].lower() not in ALLOWED_EXT:
            return "invalid script name"
        try:
            size = int(os.path.getsize(local))
        except Exception:
            size = 0
        if size <= 0:
            return "local file empty"
        if size > MAX_SCRIPT_BYTES:
            return "file too large"
        return ""
    except Exception:
        return "local file missing"


def push_many(node, peer_node_ids, local_path, remote_name=None, run=False):
    """对多个 peer 依次发起同一文件的推送（Phase3-2 批量下发）。

    返回 {"ok": {peer_node_id: tid, ...}, "skip": {peer_node_id: <原因>, ...}}：
      * peer 找不到连接        → skip[peer] = "no connection"；
      * 本地文件缺失/超限/扩展名非法 → 直接返回 {"ok": {}, "skip": {每个 peer: <原因>}}；
      * 逐个调用 push()，任一失败不影响其它（串行，避免同时灌满多个发送队列）。

    调用方不阻塞：每个 peer 的 OutgoingTransfer 各自在自己的 daemon 线程里发送。
    """
    ok_map = {}
    skip_map = {}
    try:
        peers = []
        for p in (peer_node_ids or []):
            s = str(p or "")
            if s and (s not in peers):
                peers.append(s)
        if not peers:
            return {"ok": ok_map, "skip": skip_map}
        # 本地源预校验：一次失败 → 全部 peer 统一 skip（不重复读盘）
        reason = check_local_source(local_path, remote_name)
        if reason:
            for p in peers:
                skip_map[p] = reason
            return {"ok": ok_map, "skip": skip_map}
        # 串行逐个发起（任一失败不影响其它）
        for p in peers:
            tid = push(node, p, local_path, remote_name, run)
            if tid:
                ok_map[p] = tid
            else:
                skip_map[p] = "no connection"
    except Exception as e:
        _nl_log("transfer push_many error: {}".format(e), "WARN")
    return {"ok": ok_map, "skip": skip_map}
