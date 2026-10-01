# -*- coding: utf-8 -*-
"""ACRPA NetLink — 出站连接池 + 保活重连（纯标准库，Python 3.7 兼容）。

职责（仅连接建立与保活，不发业务消息）：
  * 维护 {(host, port): Connection} 出站连接表（加锁）；
  * set_targets(targets) 设定"期望保活"的对端集合；
  * 一个重连线程每 3 秒检查一次，对缺失或 not alive 的目标静默重连；
  * 新连接建立后回调 node.on_conn_open（由 node 决定是否发 HELLO）。

本模块不 import utils，日志走 connection._nl_log。
"""
import socket
import threading

from .connection import Connection, _nl_log
from . import tls


RECONNECT_INTERVAL = 3.0
CONNECT_TIMEOUT = 3.0

# 总线主题字面量（避免顶层 import node 造成循环依赖）
_TOPIC_STATUS = "netlink.status"


class NetLinkClient(object):
    """出站连接管理器。node: NetLinkNode。"""

    def __init__(self, node):
        self._node = node
        self._conns = {}                 # {(host, port): Connection}
        self._lock = threading.RLock()
        self._targets = []               # [(host, port), ...]
        self._stop_evt = threading.Event()
        self._th = None
        self._started = False
        # 构造即启动保活线程（targets 为空时不产生任何连接）
        try:
            self._stop_evt.clear()
            self._th = threading.Thread(target=self._reconnect_loop, name="nl-client")
            self._th.daemon = True
            self._th.start()
            self._started = True
        except Exception as e:
            _nl_log("client thread start failed: {}".format(e), "ERROR")

    # ── 目标集合 ──────────────────────────────────────────────────────
    def set_targets(self, targets):
        """设置期望保活的对端集合：[("host", port), ...]。"""
        try:
            clean = []
            seen = set()
            for item in (targets or []):
                try:
                    host = str(item[0])
                    port = int(item[1])
                except Exception:
                    continue
                if not host or not port:
                    continue
                key = (host, port)
                if key in seen:
                    continue
                seen.add(key)
                clean.append(key)
            with self._lock:
                self._targets = clean
        except Exception as e:
            _nl_log("client set_targets error: {}".format(e), "WARN")

    # ── Phase4-3 TLS 辅助 ─────────────────────────────────────────────
    def _tls_setup(self):
        """读 state 现况，返回 (enabled, ctx)。

        TLS 启用但上下文构造失败时 ctx 为 None（调用方必须拒绝明文连接）。
        """
        try:
            import state
            enabled = bool(getattr(state, "NETLINK_TLS", False))
        except Exception:
            enabled = False
        if not enabled:
            return False, None
        try:
            ctx, _reason = tls.client_context()
        except Exception as e:
            _nl_log("client: client_context failed: {}".format(e), "WARN")
            ctx = None
        return True, ctx

    def _publish_tls_status(self, level, msg):
        """向总线发布 TOPIC_STATUS（失败静默）。"""
        try:
            bus = getattr(self._node, "bus", None)
            if bus is not None:
                bus.publish(_TOPIC_STATUS, {"level": str(level or "info"),
                                            "msg": str(msg or "")})
        except Exception:
            pass

    def _audit_tls_pin(self, host, port, fp, status):
        """指纹不符拒绝时写审计（审计不可用则跳过）。"""
        try:
            ctl = getattr(self._node, "control", None)
            audit = getattr(ctl, "audit", None) if ctl is not None else None
            if audit is None:
                return
            audit.write("system", "TLS_PIN",
                        {"fp": str(fp or "")[:16], "host": str(host),
                         "port": int(port)},
                        result="rejected",
                        detail=str(status or "pin mismatch"),
                        remote="{}:{}".format(host, port))
        except Exception:
            pass

    # ── 连接建立 ──────────────────────────────────────────────────────
    def connect_to(self, host, port):
        """建立（或复用）到 (host, port) 的出站连接；失败返回 None。"""
        try:
            key = (str(host), int(port))
        except Exception:
            return None
        with self._lock:
            conn = self._conns.get(key)
            if conn is not None and conn.alive:
                return conn
        # 连接建立放在锁外，避免阻塞其它调用
        try:
            sock = socket.create_connection(key, timeout=CONNECT_TIMEOUT)
        except Exception as e:
            _nl_log("connect_to {} failed: {}".format(key, e))
            return None
        # ── Phase4-3 TLS：可选加密 + 证书指纹固定（失败绝不降级为明文）──
        try:
            enabled, ctx = self._tls_setup()
            if enabled:
                if ctx is None:
                    _nl_log("connect_to {}: TLS enabled but context unavailable;"
                            " refuse plaintext".format(key), "ERROR")
                    try:
                        sock.close()
                    except Exception:
                        pass
                    return None
                ssock, reason = tls.wrap_client(sock, ctx, server_hostname=None)
                if ssock is None:
                    _nl_log("connect_to {}: TLS handshake failed: {}".format(key, reason))
                    try:
                        sock.close()
                    except Exception:
                        pass
                    return None
                fp = tls.cert_fingerprint(ssock)
                ok, pin_status = tls.pin_verify_or_reject(fp)
                if not ok:
                    try:
                        ssock.close()
                    except Exception:
                        pass
                    self._publish_tls_status("warning", (
                        "对端证书指纹不符（pin mismatch），已拒绝连接: {}:{}"
                    ).format(key[0], key[1]))
                    self._audit_tls_pin(key[0], key[1], fp, pin_status)
                    _nl_log("connect_to {}: TLS pin mismatch: {}".format(
                        key, (fp or "")[:16]), "WARN")
                    return None
                sock = ssock
                self._publish_tls_status("info", "对端证书指纹：{}（{}）".format(
                    (fp or "")[:16], pin_status))
        except Exception as e:
            _nl_log("connect_to {}: TLS setup error: {}".format(key, e), "WARN")
            try:
                sock.close()
            except Exception:
                pass
            return None
        box = {}

        def _on_msg(m):
            c = box.get("c")
            if c is not None:
                self._node.handle_message(c, m)

        def _on_close(err):
            c = box.get("c")
            if c is not None:
                self._remove(key, c)
                self._node.on_conn_closed(c)

        conn = Connection(sock, key, on_message=_on_msg, on_close=_on_close,
                          name="out:{}".format(key[0]))
        box["c"] = conn
        with self._lock:
            old = self._conns.get(key)
            self._conns[key] = conn
        if old is not None and old is not conn:
            try:
                old.close()
            except Exception:
                pass
        conn.start()
        _nl_log("client connected to {}".format(key))
        # 通知节点新连接建立（用于自动发送 HELLO）
        try:
            self._node.on_conn_open(conn)
        except Exception as e:
            _nl_log("on_conn_open raised: {}".format(e), "ERROR")
        return conn

    def get(self, host, port):
        """取当前连接（不建立），无则返回 None。"""
        try:
            key = (str(host), int(port))
        except Exception:
            return None
        with self._lock:
            return self._conns.get(key)

    def disconnect(self, host, port):
        """主动断开一条出站连接。"""
        try:
            key = (str(host), int(port))
        except Exception:
            return
        with self._lock:
            conn = self._conns.pop(key, None)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    def stop(self):
        """断开全部出站连接并停止重连线程；幂等。"""
        try:
            self._stop_evt.set()
            th = self._th
            if th is not None and th is not threading.current_thread():
                try:
                    th.join(1.5)
                except Exception:
                    pass
            self._th = None
            with self._lock:
                conns = list(self._conns.values())
                self._conns = {}
                self._targets = []
            for conn in conns:
                try:
                    conn.close()
                except Exception:
                    pass
            self._started = False
        except Exception as e:
            _nl_log("client stop error: {}".format(e), "WARN")

    # ── 重连线程 ──────────────────────────────────────────────────────
    def _reconnect_loop(self):
        try:
            while not self._stop_evt.is_set():
                try:
                    self._check_targets()
                except Exception as e:
                    _nl_log("client reconnect check error: {}".format(e), "WARN")
                self._stop_evt.wait(RECONNECT_INTERVAL)
        except Exception as e:
            _nl_log("client reconnect loop fatal: {}".format(e), "ERROR")

    def _check_targets(self):
        with self._lock:
            targets = list(self._targets)
        for host, port in targets:
            if self._stop_evt.is_set():
                return
            key = (host, port)
            with self._lock:
                conn = self._conns.get(key)
            if conn is not None and conn.alive:
                continue
            if conn is not None:
                self._remove(key, conn)
            self.connect_to(host, port)

    def _remove(self, key, conn):
        with self._lock:
            cur = self._conns.get(key)
            if cur is conn:
                self._conns.pop(key, None)
