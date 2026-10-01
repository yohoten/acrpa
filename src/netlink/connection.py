"""ACRPA NetLink 单条 TCP 连接抽象 — 读/写分离双线程（纯标准库）。

设计要点（为什么这样做）：
  * 读、写各用一条 daemon 线程，避免 sendall 阻塞拖垮接收；
  * 队列满时丢最旧一条而非阻塞，保证网络层永不拖慢本地自动化；
  * 所有线程体整体 try/except，任何异常都不向上传播；
  * 本模块禁止 import utils（utils 顶层 import tkinter），日志走私有 _nl_log。
"""
import os
import queue
import socket
import threading
import time

from .protocol import FrameError, FrameReader, frame_message, parse_msg


# ── 模块私有日志：写文件 + 静默。绝不 import utils / 绝不回投日志总线 ──
_LOG_LOCK = threading.Lock()


def _nl_log_file():
    """日志文件路径：<项目根>/logs/netlink.log（frozen 或权限异常时静默降级）。"""
    base = os.path.dirname(os.path.abspath(__file__))            # src/netlink
    root = os.path.dirname(os.path.dirname(base))                # 项目根
    log_dir = os.path.join(root, "logs")
    try:
        os.makedirs(log_dir, exist_ok=True)
    except Exception:
        pass
    return os.path.join(log_dir, "netlink.log")


def _nl_log(msg, level="INFO"):
    """NetLink 内部日志：写文件，失败静默。用于排查网络层问题，不回投总线避免回环。"""
    try:
        line = "{0} [{1}] {2}\n".format(time.strftime("%Y-%m-%d %H:%M:%S"), level, msg)
        with _LOG_LOCK:
            with open(_nl_log_file(), "a", encoding="utf-8") as f:
                f.write(line)
    except Exception:
        pass


class Connection(object):
    """单条 TCP 连接：读线程回调 on_message，写线程消费发送队列。"""

    SEND_QUEUE_MAX = 512     # 发送队列上限，满则丢最旧
    RECV_CHUNK = 65536

    def __init__(self, sock, peer_addr, on_message=None, on_close=None, name=""):
        """on_message(msg_dict) 在读线程中调用（调用方自行保证线程安全）；
        on_close(err_or_none) 在连接结束时调用一次（去重，仅调一次）。
        """
        self._sock = sock
        self.peer_addr = peer_addr
        self.name = name
        self._on_message = on_message
        self._on_close = on_close
        self._send_q = queue.Queue(maxsize=self.SEND_QUEUE_MAX)
        self._reader = FrameReader()
        self._closed = False
        self._close_lock = threading.Lock()
        self._close_cb_fired = False
        self._rt = None
        self._wt = None
        self._alive = False
        self._last_recv_ts = time.time()

    # ── 只读属性 ──
    @property
    def alive(self):
        return self._alive and not self._closed

    @property
    def last_recv_ts(self):
        return self._last_recv_ts

    def is_stale(self, timeout=35.0):
        """上层心跳判定：距上次收到数据超过 timeout 秒视为掉线。"""
        try:
            return (time.time() - self._last_recv_ts) > timeout
        except Exception:
            return True

    def start(self):
        """置 TCP_NODELAY、启动读线程 + 写线程（均 daemon）。"""
        try:
            self._sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except Exception:
            pass
        self._alive = True
        self._rt = threading.Thread(target=self._read_loop,
                                    name="nl-read-{}".format(self.name or id(self)))
        self._rt.daemon = True
        self._wt = threading.Thread(target=self._write_loop,
                                    name="nl-write-{}".format(self.name or id(self)))
        self._wt.daemon = True
        self._rt.start()
        self._wt.start()

    # ── 读线程 ──
    def _read_loop(self):
        err = None
        try:
            while not self._closed:
                try:
                    chunk = self._sock.recv(self.RECV_CHUNK)
                except Exception as e:
                    err = e
                    break
                if not chunk:
                    break  # 对端正常关闭
                self._last_recv_ts = time.time()
                try:
                    payloads = self._reader.feed(chunk)
                except FrameError as fe:
                    # 长度非法/超限 → 流已失步，断连
                    _nl_log("frame error from {0}: {1}".format(self.peer_addr, fe), "WARN")
                    err = fe
                    break
                for payload in payloads:
                    self._dispatch(payload)
        except Exception as e:
            err = e
        finally:
            self._alive = False
            self.close(err)

    def _dispatch(self, payload):
        """单帧派发；parse_msg 出错只丢该帧，不断连。"""
        try:
            msg = parse_msg(payload)
        except FrameError as fe:
            _nl_log("bad frame from {0}: {1}".format(self.peer_addr, fe), "WARN")
            return
        except Exception as e:
            _nl_log("parse error from {0}: {1}".format(self.peer_addr, e), "WARN")
            return
        cb = self._on_message
        if cb is not None:
            try:
                cb(msg)
            except Exception as e:
                _nl_log("on_message raised: {}".format(e), "ERROR")

    # ── 写线程 ──
    def _write_loop(self):
        err = None
        try:
            while True:
                try:
                    payload = self._send_q.get(timeout=0.5)
                except queue.Empty:
                    if self._closed:
                        break
                    continue
                if payload is None:      # 关闭哨兵
                    break
                if self._closed:
                    break
                try:
                    self._sock.sendall(payload)
                except Exception as e:
                    err = e
                    break
        except Exception as e:
            err = e
        finally:
            if err is not None:
                self._alive = False
                self.close(err)

    # ── 发送 API（线程安全，绝不阻塞、绝不抛异常）──
    def send(self, msg_dict):
        """编码并线程安全入发送队列；队列满丢最旧一条并返回 False。"""
        try:
            if self._closed or not self._alive:
                return False
            return self.send_raw(frame_message(msg_dict))
        except Exception:
            return False

    def send_raw(self, payload_bytes):
        """直接入队一个已编码好的 payload（Phase3 blob 用）。"""
        try:
            if self._closed or not self._alive:
                return False
            try:
                self._send_q.put_nowait(payload_bytes)
                return True
            except queue.Full:
                # 队列满：丢最旧一条腾位置，再塞入新的一条，返回 False 让上层知晓溢出
                try:
                    self._send_q.get_nowait()
                except queue.Empty:
                    pass
                try:
                    self._send_q.put_nowait(payload_bytes)
                except queue.Full:
                    pass
                return False
        except Exception:
            return False

    # ── 关闭 ──
    def close(self, err=None):
        """幂等关闭：shutdown 套接字、唤醒队列、join 线程（超时 1s）、回调 on_close。"""
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
            self._alive = False
        # 唤醒写线程（队列满时先丢一条）
        try:
            self._send_q.put_nowait(None)
        except queue.Full:
            try:
                self._send_q.get_nowait()
            except Exception:
                pass
            try:
                self._send_q.put_nowait(None)
            except Exception:
                pass
        except Exception:
            pass
        # 关套接字（唤醒阻塞中的 recv）
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass
        try:
            self._sock.close()
        except Exception:
            pass
        # join 两条线程（跳过当前线程，避免自杀式 join）
        cur = threading.current_thread()
        for t in (self._rt, self._wt):
            if t is not None and t is not cur:
                try:
                    t.join(1.0)
                except Exception:
                    pass
        # 回调 on_close 一次
        with self._close_lock:
            if not self._close_cb_fired:
                self._close_cb_fired = True
                cb = self._on_close
            else:
                cb = None
        if cb is not None:
            try:
                cb(err)
            except Exception:
                pass
