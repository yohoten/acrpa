# -*- coding: utf-8 -*-
"""ACRPA NetLink — UDP 广播发现（纯标准库，Python 3.7 兼容）。

职责：
  * 周期向广播地址发送 ANNOUNCE（携带本机 node_id / 设备名 / TCP 端口）；
  * 接收同局域网其它节点的 ANNOUNCE / BYE，并回调 on_peer(info)；
  * 忽略自身 node_id 的广播包（避免自我发现）；
  * 支持静态对端（extra_peers），作为 UDP 广播不可靠时的兜底路径。

约束：本模块不 import utils（utils 顶层 import tkinter），日志统一走
connection._nl_log（文件 + 静默，不回投日志总线，避免回环）。
"""
import socket
import threading
import time

from .protocol import (
    DEFAULT_PORT, DEFAULT_DISCOVERY_PORT,
    encode_frame, frame_message, make_msg, parse_msg,
    T_ANNOUNCE, T_BYE,
)
from .connection import _nl_log


ANNOUNCE_INTERVAL = 5.0        # 广播周期（秒）
RECV_TIMEOUT = 1.0             # recvfrom 超时，保证线程可在 1s 内退出
BROADCAST_ADDR = "255.255.255.255"


def _primary_ip():
    """本机主 IPv4；失败返回空串。"""
    try:
        return socket.gethostbyname(socket.gethostname())
    except Exception:
        return ""


def _local_ipv4():
    """用 getaddrinfo 枚举本机 IPv4（排除回环）；失败返回空集合。"""
    ips = set()
    try:
        host = socket.gethostname()
        for info in socket.getaddrinfo(host, None):
            try:
                addr = info[4][0]
            except Exception:
                continue
            if "." in addr and not addr.startswith("127."):
                ips.add(addr)
    except Exception:
        pass
    return ips


def _broadcast_targets():
    """广播目标集合：255.255.255.255 + 各本地 IPv4 的 /24 定向广播地址（去重）。"""
    targets = set([BROADCAST_ADDR])
    for ip in _local_ipv4():
        parts = ip.split(".")
        if len(parts) == 4:
            targets.add("{}.{}.{}.255".format(parts[0], parts[1], parts[2]))
    return list(targets)


class Discovery(object):
    """UDP 广播发现器。on_peer(info) 在接收线程中调用，调用方自行保证线程安全。"""

    def __init__(self, port=DEFAULT_DISCOVERY_PORT, tcp_port=DEFAULT_PORT,
                 device_name="", node_id="", version="",
                 on_peer=None, enabled=True, extra_peers=None):
        """extra_peers: [("host", port), ...] 手动静态对端，启动时即回调一次。"""
        self.port = int(port or DEFAULT_DISCOVERY_PORT)
        self.tcp_port = int(tcp_port or DEFAULT_PORT)
        self.device_name = device_name or ""
        self.node_id = node_id or ""
        self.version = version or ""
        self._on_peer = on_peer
        self._enabled = bool(enabled)
        self._extra_peers = list(extra_peers or [])
        self._sock = None
        self._stop_evt = threading.Event()
        self._rx = None
        self._tx = None
        self._started = False

    @property
    def enabled(self):
        return self._enabled

    # ── 生命周期 ──────────────────────────────────────────────────────
    def start(self):
        """绑定 UDP 并启动收/发线程；失败记 _nl_log 并返回 False。"""
        try:
            if self._started:
                return True
            if not self._enabled:
                _nl_log("discovery disabled by config")
                return False
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            except Exception:
                pass
            sock.bind(("", self.port))
            sock.settimeout(RECV_TIMEOUT)
            self._sock = sock
            self._stop_evt.clear()
            self._rx = threading.Thread(target=self._recv_loop, name="nl-disc-rx")
            self._rx.daemon = True
            self._tx = threading.Thread(target=self._send_loop, name="nl-disc-tx")
            self._tx.daemon = True
            self._rx.start()
            self._tx.start()
            self._started = True
            _nl_log("discovery started udp={} tcp={} static={}".format(
                self.port, self.tcp_port, self._extra_peers))
            # 静态对端立即回调一次（不依赖 UDP 广播）
            self._emit_static_peers()
            return True
        except Exception as e:
            _nl_log("discovery start failed: {}".format(e), "ERROR")
            self._close_socket()
            return False

    def stop(self):
        """停止线程 + 关闭 socket；幂等，可在 1s 内退出。"""
        try:
            self._stop_evt.set()
            self._close_socket()
            for t in (self._rx, self._tx):
                if t is not None and t is not threading.current_thread():
                    try:
                        t.join(1.5)
                    except Exception:
                        pass
            self._rx = None
            self._tx = None
            self._started = False
        except Exception as e:
            _nl_log("discovery stop error: {}".format(e), "WARN")

    def _close_socket(self):
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

    # ── 对外发送 ──────────────────────────────────────────────────────
    def announce_now(self):
        """立即广播一次 ANNOUNCE。"""
        try:
            self._broadcast(self._payload(T_ANNOUNCE))
        except Exception as e:
            _nl_log("announce_now failed: {}".format(e), "WARN")

    def send_bye(self):
        """广播一次 BYE（退出通知）。"""
        try:
            self._broadcast(self._payload(T_BYE))
        except Exception as e:
            _nl_log("send_bye failed: {}".format(e), "WARN")

    def _payload(self, t):
        data = {
            "node_id": self.node_id,
            "name": self.device_name,
            "host": _primary_ip(),
            "port": self.tcp_port,
            "version": self.version,
            "ts": int(time.time()),
        }
        return encode_frame(frame_message(make_msg(t, data)))

    def _broadcast(self, payload):
        sock = self._sock
        if sock is None:
            return
        for addr in _broadcast_targets():
            try:
                sock.sendto(payload, (addr, self.port))
            except Exception:
                pass

    # ── 发送线程：每 5s 广播一次 ANNOUNCE ─────────────────────────────
    def _send_loop(self):
        try:
            while not self._stop_evt.is_set():
                try:
                    self.announce_now()
                except Exception as e:
                    _nl_log("announce loop error: {}".format(e), "WARN")
                self._stop_evt.wait(ANNOUNCE_INTERVAL)
        except Exception as e:
            _nl_log("discovery send loop fatal: {}".format(e), "ERROR")

    # ── 接收线程 ──────────────────────────────────────────────────────
    def _recv_loop(self):
        try:
            while not self._stop_evt.is_set():
                sock = self._sock
                if sock is None:
                    break
                try:
                    data, addr = sock.recvfrom(65535)
                except socket.timeout:
                    continue
                except Exception as e:
                    if self._stop_evt.is_set():
                        break
                    _nl_log("discovery recv error: {}".format(e), "WARN")
                    continue
                self._handle_datagram(data, addr)
        except Exception as e:
            _nl_log("discovery recv loop fatal: {}".format(e), "ERROR")

    def _handle_datagram(self, data, addr):
        try:
            msg = parse_msg(data)
        except Exception:
            return
        t = msg.get("t")
        if t not in (T_ANNOUNCE, T_BYE):
            return
        d = msg.get("data") or {}
        nid = str(d.get("node_id") or "")
        if nid and nid == self.node_id:
            return  # 忽略自身
        src_ip = ""
        try:
            src_ip = addr[0]
        except Exception:
            pass
        host = str(d.get("host") or "") or src_ip
        try:
            port = int(d.get("port") or self.tcp_port)
        except Exception:
            port = self.tcp_port
        info = {
            "node_id": nid,
            "name": str(d.get("name") or ""),
            "host": host,
            "port": port,
            "version": str(d.get("version") or ""),
            "ts": int(d.get("ts") or time.time()),
            "online": (t != T_BYE),
        }
        self._emit(info)

    # ── 静态对端 ──────────────────────────────────────────────────────
    def _emit_static_peers(self):
        for item in self._extra_peers:
            try:
                host = str(item[0])
                port = int(item[1])
            except Exception:
                continue
            self._emit({"node_id": "", "name": "", "host": host, "port": port,
                        "version": "", "ts": int(time.time()), "online": True})

    def _emit(self, info):
        cb = self._on_peer
        if cb is None:
            return
        try:
            cb(info)
        except Exception as e:
            _nl_log("discovery on_peer raised: {}".format(e), "ERROR")
