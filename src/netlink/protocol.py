"""ACRPA NetLink 协议层 — 4 字节大端长度前缀帧 + JSON 信封（纯标准库）。

设计约定（后续子任务严格按此调用，命名不得擅自更改）：
  * 帧 = 4 字节大端无符号长度前缀 + payload(bytes)
  * 信封 = {"v":1, "t":类型, "id":消息id, "ts":int, "data":dict}
本模块只依赖标准库，禁止 import utils（避免 tkinter 与日志回环）。
"""
import json
import struct
import time
import uuid


PROTOCOL_VERSION = 1
DEFAULT_PORT = 19710
DEFAULT_DISCOVERY_PORT = 19711
MAX_FRAME_BYTES = 8 * 1024 * 1024      # 8MB 上限，超限直接断开该连接
HEADER_SIZE = 4                        # 4 字节大端无符号长度前缀


class FrameError(Exception):
    """帧编解码 / 协议信封校验失败。"""


def encode_frame(payload):
    """payload: bytes → 4 字节大端长度前缀 + payload。

    长度 <=0 或 > MAX_FRAME_BYTES 时抛 FrameError（调用方据此断连）。
    """
    if not isinstance(payload, (bytes, bytearray)):
        raise FrameError("payload must be bytes")
    payload = bytes(payload)
    n = len(payload)
    if n <= 0:
        raise FrameError("empty payload")
    if n > MAX_FRAME_BYTES:
        raise FrameError("frame too large: {}".format(n))
    return struct.pack(">I", n) + payload


def frame_message(msg):
    """dict → json.dumps(..., ensure_ascii=False).encode('utf-8') → encode_frame。"""
    raw = json.dumps(msg, ensure_ascii=False).encode("utf-8")
    return encode_frame(raw)


class FrameReader(object):
    """流式拆帧器：吃任意字节块，吐出凑齐的完整 payload。

    用法：
        r = FrameReader()
        for payload in r.feed(chunk_bytes):   # 返回本次凑齐的所有完整 payload(bytes)
            ...
        r.reset()                             # 连接断开时调用
    处理粘包 / 半包；长度前缀非法（<=0）或超过 MAX_FRAME_BYTES 时抛 FrameError。
    """

    def __init__(self):
        self._buf = bytearray()
        self._need = None  # 已解析出的待收长度，None 表示还没读到长度前缀

    def reset(self):
        """连接断开 / 出错后清空内部状态，可继续复用。"""
        self._buf = bytearray()
        self._need = None

    def feed(self, chunk):
        """喂入一块字节，返回本次凑齐的所有完整 payload(bytes) 列表。"""
        out = []
        if chunk:
            self._buf.extend(chunk)
        while True:
            if self._need is None:
                if len(self._buf) < HEADER_SIZE:
                    break
                n = struct.unpack(">I", bytes(self._buf[:HEADER_SIZE]))[0]
                if n <= 0 or n > MAX_FRAME_BYTES:
                    # 长度非法说明流已失步，丢弃缓冲并上报，由上层断连
                    self.reset()
                    raise FrameError("invalid frame length: {}".format(n))
                self._need = n
                del self._buf[:HEADER_SIZE]
            if len(self._buf) < self._need:
                break
            out.append(bytes(self._buf[:self._need]))
            del self._buf[:self._need]
            self._need = None
        return out


def make_msg(t, data=None, mid=None, ts=None):
    """统一信封 → dict。

    {"v":1, "t":类型, "id":消息id(默认自动生成 uuid4 hex),
     "ts":int(time.time()), "data":data or {}}
    """
    return {
        "v": PROTOCOL_VERSION,
        "t": t,
        "id": mid if mid else uuid.uuid4().hex,
        "ts": int(ts) if ts is not None else int(time.time()),
        "data": data if data is not None else {},
    }


def parse_msg(payload):
    """bytes → dict；JSON 非法 / 缺 t / v 不兼容 → 抛 FrameError。"""
    try:
        text = bytes(payload).decode("utf-8") if isinstance(payload, (bytes, bytearray)) else payload
        msg = json.loads(text)
    except Exception as e:
        raise FrameError("bad json: {}".format(e))
    if not isinstance(msg, dict):
        raise FrameError("message is not a dict")
    if "t" not in msg or not msg.get("t"):
        raise FrameError("missing message type 't'")
    v = msg.get("v", PROTOCOL_VERSION)
    if v != PROTOCOL_VERSION:
        raise FrameError("incompatible version: {}".format(v))
    if not isinstance(msg.get("data"), dict):
        msg["data"] = {}
    return msg


# ── 消息类型常量（值即协议中的 t，供其它模块 import 使用）──
T_ANNOUNCE        = "ANNOUNCE"        # UDP 广播发现
T_BYE             = "BYE"             # 节点退出广播
T_HELLO           = "HELLO"           # 握手：设备名/版本/能力位/node_id
T_AUTH            = "AUTH"            # 认证请求（Phase2 使用，此处只定义常量）
T_AUTH_OK         = "AUTH_OK"
T_AUTH_FAIL       = "AUTH_FAIL"
T_AUTH_CHALLENGE  = "AUTH_CHALLENGE"  # 服务端→客户端：下发 nonce(+配对 salt)
T_PEER_ADDED      = "PEER_ADDED"      # 被控端→控制端：配对成功通知(可含 perm)
T_PEER_REMOVED    = "PEER_REMOVED"
T_PING            = "PING"
T_PONG            = "PONG"
T_SUBSCRIBE       = "SUBSCRIBE"       # {"topics":["state","log","sched"]}
T_UNSUBSCRIBE     = "UNSUBSCRIBE"
T_STATE_SYNC      = "STATE_SYNC"
T_LOG_TAIL        = "LOG_TAIL"
T_SCHED_STATUS    = "SCHED_STATUS"
T_CMD_RUN         = "CMD_RUN"
T_CMD_PAUSE       = "CMD_PAUSE"
T_CMD_RESUME      = "CMD_RESUME"
T_CMD_STOP        = "CMD_STOP"
T_CMD_RUN_SCRIPT  = "CMD_RUN_SCRIPT"
T_CMD_ACK         = "CMD_ACK"
T_CMD_ERR         = "CMD_ERR"
T_SCRIPT_LIST_REQ = "SCRIPT_LIST_REQ"
T_SCRIPT_LIST_RES = "SCRIPT_LIST_RES"
T_BLOB_BEGIN      = "BLOB_BEGIN"
T_BLOB_CHUNK      = "BLOB_CHUNK"
T_BLOB_END        = "BLOB_END"
T_SCREENSHOT_REQ  = "SCREENSHOT_REQ"
T_SCREENSHOT_DAT  = "SCREENSHOT_DAT"

T_NAMES = (
    T_ANNOUNCE, T_BYE, T_HELLO, T_AUTH, T_AUTH_OK, T_AUTH_FAIL,
    T_AUTH_CHALLENGE, T_PEER_ADDED, T_PEER_REMOVED,
    T_PING, T_PONG, T_SUBSCRIBE, T_UNSUBSCRIBE,
    T_STATE_SYNC, T_LOG_TAIL, T_SCHED_STATUS,
    T_CMD_RUN, T_CMD_PAUSE, T_CMD_RESUME, T_CMD_STOP, T_CMD_RUN_SCRIPT,
    T_CMD_ACK, T_CMD_ERR, T_SCRIPT_LIST_REQ, T_SCRIPT_LIST_RES,
    T_BLOB_BEGIN, T_BLOB_CHUNK, T_BLOB_END,
    T_SCREENSHOT_REQ, T_SCREENSHOT_DAT,
)

# 订阅主题白名单（SUBSCRIBE 校验用）
T_TOPICS = ("state", "log", "sched")
