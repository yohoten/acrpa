# -*- coding: utf-8 -*-
"""ACRPA NetLink — TCP 入站监听（纯标准库，Python 3.7 兼容）。

职责：
  * 绑定 TCP 端口、accept 循环（daemon 线程）；
  * 每条入站连接包装为 Connection，回调统一转给 NetLinkNode；
  * 维护存活连接列表（加锁），断开时移除。

本模块不 import utils，日志走 connection._nl_log。
"""
import socket
import threading

from .connection import Connection, _nl_log
from . import tls


class NetLinkServer(object):
    """TCP 监听服务。node: NetLinkNode。

    tls_ctx：可选 SSLContext（Phase4-3）。非 None 时每条入站连接先做 TLS 握手，
    成功才交给 Connection；握手失败只关闭该连接，不影响 accept 循环。
    """

    def __init__(self, port, node, host="", tls_ctx=None):
        self.port = int(port)
        self._node = node
        self._host = host or ""
        self._sock = None
        self._stop_evt = threading.Event()
        self._th = None
        self._conns = []
        self._lock = threading.Lock()
        self._started = False
        self._tls_ctx = tls_ctx

    @property
    def conns(self):
        """存活连接快照 list[Connection]。"""
        with self._lock:
            return list(self._conns)

    # ── 生命周期 ──────────────────────────────────────────────────────
    def start(self):
        """绑定端口并启动 accept 线程；端口占用/绑定失败返回 False。"""
        try:
            if self._started:
                return True
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((self._host, self.port))
            sock.listen(64)
            sock.settimeout(1.0)
            self._sock = sock
            self._stop_evt.clear()
            self._th = threading.Thread(target=self._accept_loop, name="nl-accept")
            self._th.daemon = True
            self._th.start()
            self._started = True
            _nl_log("server listening on {}:{}".format(self._host or "0.0.0.0", self.port))
            return True
        except Exception as e:
            _nl_log("server start failed on port {}: {}".format(self.port, e), "ERROR")
            self._close_listen()
            return False

    def stop(self):
        """关闭监听 socket 与全部连接；幂等。"""
        try:
            self._stop_evt.set()
            self._close_listen()
            th = self._th
            if th is not None and th is not threading.current_thread():
                try:
                    th.join(1.5)
                except Exception:
                    pass
            self._th = None
            for conn in self.conns:
                try:
                    conn.close()
                except Exception:
                    pass
            with self._lock:
                self._conns = []
            self._started = False
        except Exception as e:
            _nl_log("server stop error: {}".format(e), "WARN")

    def _close_listen(self):
        try:
            sock = self._sock
            self._sock = None
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass
        except Exception:
            pass

    # ── accept 线程 ───────────────────────────────────────────────────
    def _accept_loop(self):
        try:
            while not self._stop_evt.is_set():
                sock = self._sock
                if sock is None:
                    break
                try:
                    csock, addr = sock.accept()
                except socket.timeout:
                    continue
                except Exception as e:
                    if self._stop_evt.is_set():
                        break
                    _nl_log("accept error: {}".format(e), "WARN")
                    continue
                self._handle_accept(csock, addr)
        except Exception as e:
            _nl_log("accept loop fatal: {}".format(e), "ERROR")

    def _handle_accept(self, csock, addr):
        try:
            ip = ""
            try:
                ip = addr[0]
            except Exception:
                pass
            # ── Phase4-3 TLS：入站连接先握手，失败仅关该连接（不影响 accept 循环）──
            if self._tls_ctx is not None:
                wrapped = None
                reason = ""
                try:
                    try:
                        csock.settimeout(10)
                    except Exception:
                        pass
                    wrapped, reason = tls.wrap_server(csock, self._tls_ctx)
                except Exception as e:
                    wrapped = None
                    reason = str(e)
                if wrapped is None:
                    _nl_log("server: TLS handshake failed from {}: {}".format(ip, reason), "WARN")
                    try:
                        csock.close()
                    except Exception:
                        pass
                    return
                csock = wrapped
                try:
                    csock.settimeout(None)
                except Exception:
                    pass
            holder = []

            def _on_msg(m):
                conn = holder[0] if holder else None
                if conn is not None:
                    self._node.handle_message(conn, m)

            def _on_close(err):
                conn = holder[0] if holder else None
                if conn is not None:
                    self._remove_conn(conn)
                    self._node.on_conn_closed(conn)

            conn = Connection(csock, addr, on_message=_on_msg, on_close=_on_close,
                              name="in:{}".format(ip))
            holder.append(conn)
            with self._lock:
                self._conns.append(conn)
            conn.start()
            # 通知节点新连接建立（用于自动发送 HELLO）
            try:
                self._node.on_conn_open(conn)
            except Exception as e:
                _nl_log("on_conn_open raised: {}".format(e), "ERROR")
        except Exception as e:
            _nl_log("handle_accept error: {}".format(e), "ERROR")
            try:
                csock.close()
            except Exception:
                pass

    def _remove_conn(self, conn):
        with self._lock:
            try:
                self._conns.remove(conn)
            except ValueError:
                pass
