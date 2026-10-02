# -*- coding: utf-8 -*-
"""ACRPA NetLink — 节点编排（Phase 1-2，纯标准库，Python 3.7 兼容）。

NetLinkNode 把 Discovery / Server / Client / Agent 组装成一个可启停的节点，
并对上层（UI）暴露统一事件总线主题：

    TOPIC_PEER       设备上下线           {node_id, name, host, port, online, version}
    TOPIC_PEER_STATE 远端 STATE_SYNC      {node_id, <state_snapshot 字段>}
    TOPIC_PEER_LOG   远端 LOG_TAIL        {node_id, lines, seq, full}
    TOPIC_PEER_SCHED 远端 SCHED_STATUS    {node_id, enabled, next_run, task_count}
    TOPIC_STATUS     本模块自身提示        {"level": "info/warning/error", "msg": str}
    TOPIC_CMD_RESULT 指令回执（控制端）    {node_id, t, data}

本模块不 import utils，日志走 connection._nl_log。
"""
import hashlib
import os
import socket
import threading
import time

from .bus import NetBus
from .protocol import (
    DEFAULT_PORT, DEFAULT_DISCOVERY_PORT,
    make_msg,
    T_HELLO, T_AUTH, T_AUTH_OK, T_AUTH_FAIL, T_AUTH_CHALLENGE,
    T_PEER_ADDED, T_PEER_REMOVED,
    T_PING, T_PONG, T_SUBSCRIBE, T_UNSUBSCRIBE,
    T_STATE_SYNC, T_LOG_TAIL, T_SCHED_STATUS, T_CMD_ACK, T_CMD_ERR,
    T_TOPICS,
    T_SCRIPT_LIST_REQ, T_SCRIPT_LIST_RES,
    T_BLOB_BEGIN, T_BLOB_CHUNK, T_BLOB_END,
    T_SCREENSHOT_REQ, T_SCREENSHOT_DAT,
)
from .connection import _nl_log
from .discovery import Discovery
from .server import NetLinkServer
from .client import NetLinkClient
from .agent import Agent
from .control import ControlExecutor
from . import transfer
from . import screen
from . import tls
from .security import (
    PeerStore, PairWindow, AuthManager,
    make_fingerprint, load_peer_token, store_peer_token, forget_peer_token,
    derive_token, make_proof, required_perm, perm_satisfies,
)


# ── 总线主题常量（UI 层按这些名字订阅）─────────────────────────────────
TOPIC_PEER       = "netlink.peer"
TOPIC_PEER_STATE = "netlink.peer_state"
TOPIC_PEER_LOG   = "netlink.peer_log"
TOPIC_PEER_SCHED = "netlink.peer_sched"
TOPIC_STATUS     = "netlink.status"
TOPIC_AUTH       = "netlink.auth"
TOPIC_CMD_RESULT = "netlink.cmd_result"
TOPIC_SCREENSHOT = "netlink.screenshot"

# 认证前允许通过门槛的消息类型（其余消息未认证一律拒绝）
_ALLOW_WHEN_UNAUTH = (T_HELLO, T_AUTH, T_PING, T_PONG,
                      T_AUTH_CHALLENGE, T_AUTH_OK, T_AUTH_FAIL)

# 本节点能力位（Phase 1：只读，cmd/script 关闭）
CAPS = {"state": True, "log": True, "sched": True, "cmd": False, "script": False}

HEARTBEAT_INTERVAL = 10.0      # 心跳 PING 周期（秒）
STALE_TIMEOUT = 35.0           # 距上次收到数据超过该秒数视为掉线


# ── 工具函数 ──────────────────────────────────────────────────────────
def _safe_hostname():
    try:
        return socket.gethostname()
    except Exception:
        return "ACRPA"


def _local_host():
    """本机主 IPv4（失败返回空串）。"""
    try:
        return socket.gethostbyname(socket.gethostname())
    except Exception:
        return ""


def _machine_guid():
    """读取 Windows MachineGuid（非 Windows / 失败返回空串）。"""
    try:
        import winreg
        access = winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0)
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                             r"SOFTWARE\Microsoft\Cryptography", 0, access)
        try:
            val, _ = winreg.QueryValueEx(key, "MachineGuid")
        finally:
            try:
                winreg.CloseKey(key)
            except Exception:
                pass
        return str(val or "")
    except Exception:
        return ""


def make_node_id():
    """生成稳定 node_id：MachineGuid → sha1(hostname+用户名) → 随机。"""
    guid = _machine_guid()
    if guid:
        return hashlib.sha1(("guid:" + guid).encode("utf-8")).hexdigest()[:16]
    host = _safe_hostname()
    user = ""
    try:
        user = os.environ.get("USERNAME") or os.environ.get("USER") or ""
    except Exception:
        user = ""
    if host:
        return hashlib.sha1(("host:" + host + "|" + user).encode("utf-8")).hexdigest()[:16]
    try:
        import uuid
        return uuid.uuid4().hex[:16]
    except Exception:
        return "%016x" % (int(time.time() * 1000) & 0xFFFFFFFFFFFFFFFF)


def _import_state():
    try:
        import state
        return state
    except Exception as e:
        _nl_log("node: import state failed: {}".format(e), "WARN")
        return None


def _import_version_info():
    """懒 import version_info（版本号唯一事实来源）；失败返回 None。

    与 _import_state 同口径：netlink 作为应用内包加载时 sys.path 已含 src。
    集中在一处导入既避免顶层硬依赖，也便于新增回退逻辑。
    """
    try:
        import version_info
        return version_info
    except Exception as e:
        _nl_log("node: import version_info failed: {}".format(e), "WARN")
        return None


def _safe_int(v, default):
    try:
        return int(v)
    except Exception:
        return default


def _parse_static_peers(raw):
    """解析静态对端配置：支持 "host:port" / ["host", port] / {"host"/"ip", "port"}。"""
    out = []
    try:
        for item in (raw or []):
            host = None
            port = None
            if isinstance(item, str):
                if ":" in item:
                    h, _, p = item.rpartition(":")
                    host = h.strip()
                    port = _safe_int(p.strip(), None)
                else:
                    host = item.strip()
                    port = DEFAULT_PORT
            elif isinstance(item, dict):
                host = str(item.get("host") or item.get("ip") or "").strip()
                port = _safe_int(item.get("port"), None)
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                host = str(item[0]).strip()
                port = _safe_int(item[1], None)
            if host and port:
                out.append((host, int(port)))
    except Exception:
        pass
    return out


class NetLinkNode(object):
    """NetLink 节点：发现 + 连接 + 只读状态上报。"""

    def __init__(self, root=None, bus=None):
        self._root = root
        self._bus = bus if bus is not None else NetBus()
        self._lock = threading.RLock()
        self._node_id = make_node_id()
        self._name = _safe_hostname()
        self._port = DEFAULT_PORT
        self._discovery_port = DEFAULT_DISCOVERY_PORT
        self._host = _local_host()
        self._version = self._read_version()
        self._autodiscover = True
        self._static_peers = []
        self._discovery = None
        self._server = None
        self._client = None
        self._agent = None
        self._peers = {}                 # {node_id: info}
        self._conns = {}                 # {conn: {"node_id":.., "name":.., "hello_sent":bool}}
        self._hb = None
        self._stop_evt = threading.Event()
        self._started = False
        # ── Phase2-1a 安全内核 ──
        self._self_fp = make_fingerprint(self._node_id, self._name)
        _st = _import_state()
        try:
            _ttl = int(getattr(_st, "NETLINK_PIN_TTL", 600) or 600)
        except Exception:
            _ttl = 600
        try:
            _req = bool(getattr(_st, "NETLINK_REQUIRE_AUTH", True))
        except Exception:
            _req = True
        self._peer_store = PeerStore()
        self._pair = PairWindow(_ttl)
        self._auth = AuthManager(self, self._peer_store, self._pair,
                                 require_auth=_req)
        self._targets_pin = {}            # {(host, port): 待配对 PIN}
        self._client_auth_pending = {}    # {conn: {...}} 控制端认证上下文
        # ── Phase2-2 远程操控内核 ──
        self._control = ControlExecutor(self)
        # ── Phase3-1a 脚本分发接收内核 ──
        self._incoming = transfer.IncomingStore(self)

    # ── 只读属性 ──────────────────────────────────────────────────────
    @property
    def info(self):
        """本机信息：{"node_id","name","host","port","version"}。"""
        return {"node_id": self._node_id, "name": self._name,
                "host": self._host, "port": self._port, "version": self._version}

    @property
    def bus(self):
        return self._bus

    @property
    def peers(self):
        with self._lock:
            return dict(self._peers)

    # ── 生命周期 ──────────────────────────────────────────────────────
    def start(self):
        """读取 state 配置 → 建 Discovery/Server/Client/Agent → 全部 start。"""
        try:
            with self._lock:
                if self._started:
                    return True
            st = _import_state()
            self._load_config(st)

            # ── Phase4-3 TLS 可选档：启用但证书不可用 → 拒绝启动（绝不降级明文）──
            tls_ctx = None
            self._tls_ctx = None
            self._tls_reason = ""
            try:
                tls_enabled = (bool(getattr(st, "NETLINK_TLS", False))
                               if st is not None else False)
            except Exception:
                tls_enabled = False
            if tls_enabled:
                cert = str(getattr(st, "NETLINK_TLS_CERT", "") or "")
                key = str(getattr(st, "NETLINK_TLS_KEY", "") or "")
                tls_ctx, reason = tls.server_context(cert, key)
                if tls_ctx is None:
                    self._tls_reason = str(reason or "")
                    msg = "TLS 已启用但无法加载证书：{}（已拒绝启动互联）".format(
                        self._tls_reason)
                    _nl_log("node: {}".format(msg), "ERROR")
                    try:
                        self._bus.publish(TOPIC_STATUS, {"level": "error", "msg": msg})
                    except Exception:
                        pass
                    return False
                self._tls_ctx = tls_ctx

            ok = True
            self._client = NetLinkClient(self)

            self._server = NetLinkServer(self._port, self, host="", tls_ctx=tls_ctx)
            if not self._server.start():
                _nl_log("node: server bind failed on port {}".format(self._port), "ERROR")
                ok = False

            self._agent = Agent(self)
            self._agent.start()
            self._control.start()

            disc = None
            if self._autodiscover:
                disc = Discovery(port=self._discovery_port, tcp_port=self._port,
                                 device_name=self._name, node_id=self._node_id,
                                 version=self._version, on_peer=self._on_peer,
                                 enabled=True, extra_peers=self._static_peers)
                if not disc.start():
                    disc = None
            self._discovery = disc
            # 自动发现关闭 / 启动失败时，静态对端由节点自行兜底回调
            if self._discovery is None and self._static_peers:
                self._emit_static_peers()

            self._stop_evt.clear()
            self._hb = threading.Thread(target=self._heartbeat_loop, name="nl-hb")
            self._hb.daemon = True
            self._hb.start()

            with self._lock:
                self._started = True
            if ok:
                self._bus.publish(TOPIC_STATUS, {"level": "info",
                                                 "msg": "netlink started port={}".format(self._port)})
            else:
                self._bus.publish(TOPIC_STATUS, {"level": "error",
                                                 "msg": "netlink server bind failed"})
            return ok
        except Exception as e:
            _nl_log("node start error: {}".format(e), "ERROR")
            return False

    def stop(self):
        """幂等停止：send_bye + 逐个 stop + 清空连接与订阅表。"""
        try:
            with self._lock:
                was_started = self._started
                self._started = False
            self._stop_evt.set()

            disc = self._discovery
            if disc is not None:
                try:
                    disc.send_bye()
                except Exception:
                    pass
                try:
                    disc.stop()
                except Exception:
                    pass
            self._discovery = None

            hb = self._hb
            if hb is not None and hb is not threading.current_thread():
                try:
                    hb.join(1.5)
                except Exception:
                    pass
            self._hb = None

            if self._client is not None:
                try:
                    self._client.stop()
                except Exception:
                    pass
            if self._server is not None:
                try:
                    self._server.stop()
                except Exception:
                    pass
            if self._agent is not None:
                try:
                    self._agent.stop()
                except Exception:
                    pass
            if self._control is not None:
                try:
                    self._control.stop()
                except Exception:
                    pass

            self._client = None
            self._server = None
            self._agent = None
            with self._lock:
                self._conns = {}
                self._peers = {}
                self._client_auth_pending = {}
                self._targets_pin = {}
            if was_started:
                self._bus.publish(TOPIC_STATUS, {"level": "info", "msg": "netlink stopped"})
        except Exception as e:
            _nl_log("node stop error: {}".format(e), "ERROR")

    # ── 配置读取 ──────────────────────────────────────────────────────
    def _load_config(self, st):
        def g(name, default):
            if st is None:
                return default
            v = getattr(st, name, default)
            return default if v is None else v

        self._port = _safe_int(g("NETLINK_PORT", DEFAULT_PORT), DEFAULT_PORT) or DEFAULT_PORT
        self._discovery_port = _safe_int(g("NETLINK_DISCOVERY_PORT", DEFAULT_DISCOVERY_PORT),
                                         DEFAULT_DISCOVERY_PORT) or DEFAULT_DISCOVERY_PORT
        self._autodiscover = bool(g("NETLINK_AUTODISCOVER", True))
        name = str(g("NETLINK_DEVICE_NAME", "") or "").strip()
        self._name = name or _safe_hostname()
        self._static_peers = _parse_static_peers(g("NETLINK_STATIC_PEERS", []))
        self._host = _local_host()
        self._node_id = self._node_id or make_node_id()
        self._self_fp = make_fingerprint(self._node_id, self._name)
        # 刷新认证开关（测试常先改内存 state 再 start）
        try:
            self._auth.set_require_auth(bool(g("NETLINK_REQUIRE_AUTH", True)))
        except Exception:
            pass

    def _read_version(self):
        """当前应用版本号 —— 统一取自 version_info（读根 VERSION，唯一事实来源）。

        历史实现直接读取项目根目录下的 VERSION 文件，绕过了 version_info 的
        多级查找（EXE 同级 / _MEIPASS / 项目根）与冻结路径处理，打包环境下
        本节点可能读到空版本，与其它模块的版本口径出现分叉。此处改为复用
        version_info，改一处（根 VERSION 文件）即全局生效。
        极端情况下 version_info 不可导入时返回空串，由上层忽略版本字段。
        """
        vi = _import_version_info()
        if vi is None:
            return ""
        try:
            return str(vi.get_version() or "").strip()
        except Exception as e:
            _nl_log("node: version_info.get_version failed: {}".format(e), "WARN")
            return ""

    # ── Phase4-3 TLS 状态（供 UI 只读展示）─────────────────────────────
    def tls_status(self):
        """返回 {"enabled","cert","key","pins","ready","reason"}。

        ready：TLS 启用且服务端证书/私钥可加载；未启用时 ready=False。
        reason：未就绪时的稳定英文原因（cert missing / key missing /
        cert load failed / tls unavailable），就绪或未启用时为空串。
        """
        st = _import_state()
        enabled = False
        cert = ""
        key = ""
        pins = 0
        try:
            if st is not None:
                enabled = bool(getattr(st, "NETLINK_TLS", False))
                cert = str(getattr(st, "NETLINK_TLS_CERT", "") or "")
                key = str(getattr(st, "NETLINK_TLS_KEY", "") or "")
                pins = len(getattr(st, "NETLINK_TLS_PINS", []) or [])
        except Exception:
            pass
        ready = False
        reason = ""
        if enabled:
            ctx, rsn = tls.server_context(cert, key)
            ready = ctx is not None
            reason = "" if ready else str(rsn or "")
        return {"enabled": enabled, "cert": cert, "key": key,
                "pins": int(pins), "ready": bool(ready), "reason": reason}

    # ── 连接生命周期回调 ──────────────────────────────────────────────
    def on_conn_open(self, conn):
        """新连接建立：判定出入方向 → 登记 → 自动发送 HELLO（info+caps+fingerprint）。"""
        try:
            outbound = str(getattr(conn, "name", "") or "").startswith("out:")
            with self._lock:
                rec = self._conns.get(conn)
                if rec is None:
                    rec = {"node_id": "", "name": conn.name or "", "hello_sent": False,
                           "outbound": outbound, "fingerprint": "", "auth_started": False,
                           "subscribed": False, "authed": False}
                    self._conns[conn] = rec
                else:
                    # 只升不降：_handle_hello 可能已把 conn.name 改成对端设备名，
                    # 导致这里重算出的 outbound 从 True 误变 False（出站方向丢失）。
                    rec["outbound"] = bool(rec.get("outbound")) or outbound
                already = bool(rec.get("hello_sent"))
                if not already:
                    rec["hello_sent"] = True
            if not already:
                self.send(conn, make_msg(T_HELLO, self._hello_data()))
            _nl_log("node: conn opened {}".format(getattr(conn, "peer_addr", "")))
            # 对端身份未知，此处仅尝试（若已识别则续发 AUTH）
            self._maybe_client_auth(conn)
            # 缺陷 A：免认证连接建立/HELLO 交换后即自动订阅（需认证时此处不满足，等认证完成）
            self._ensure_subscribed(conn)
        except Exception as e:
            _nl_log("node on_conn_open error: {}".format(e), "ERROR")

    def on_conn_closed(self, conn):
        """连接断开：清理连接表 + agent 订阅 + 认证态 + 未完成的分发传输。"""
        try:
            with self._lock:
                self._conns.pop(conn, None)
                self._client_auth_pending.pop(conn, None)
            if self._auth is not None:
                self._auth.on_conn_closed(conn)
            if self._agent is not None:
                self._agent.unsubscribe_conn(conn)
            if self._incoming is not None:
                self._incoming.abort(conn)
        except Exception as e:
            _nl_log("node on_conn_closed error: {}".format(e), "ERROR")

    def _hello_data(self):
        d = dict(self.info)
        d["caps"] = dict(CAPS)
        d["fingerprint"] = make_fingerprint(self._node_id, self._name)
        return d

    # ── 消息统一入口 ──────────────────────────────────────────────────
    def handle_message(self, conn, msg):
        try:
            if not isinstance(msg, dict):
                return
            t = msg.get("t")
            data = msg.get("data") or {}
            # 本节点主动发起的出站连接（控制端→被控端）是「已登记对端」：
            # 其回执 / 状态推送直接消费，不再重复入站认证门槛。
            with self._lock:
                _crec = self._conns.get(conn)
            outbound = bool(_crec and _crec.get("outbound"))
            # 控制端收到指令回执（入站 CMD_ACK / CMD_ERR）→ 发布 TOPIC_CMD_RESULT
            if t in (T_CMD_ACK, T_CMD_ERR):
                self._on_cmd_result(conn, t, data)
                return
            # 认证前置门槛：开启认证且未通过时，仅放行握手/心跳类消息
            if (self._auth is not None and self._auth.require_auth
                    and not outbound
                    and t not in _ALLOW_WHEN_UNAUTH
                    and not self._auth.authed(conn)):
                self.send(conn, make_msg(T_AUTH_FAIL, {"reason": "auth required"}))
                return
            if t == T_HELLO:
                self._handle_hello(conn, data)
            elif t == T_AUTH:
                if self._auth is not None:
                    self._auth.on_auth(conn, msg)
                    # 缺陷 A：被控端入站连接认证完成后自动订阅远端状态
                    self._ensure_subscribed(conn)
            elif t in (T_AUTH_CHALLENGE, T_AUTH_OK, T_AUTH_FAIL):
                self._handle_client_auth(conn, msg)
            elif t in (T_PEER_ADDED, T_PEER_REMOVED):
                self._handle_peer_control(conn, t, data)
            elif t == T_PING:
                self.send(conn, make_msg(T_PONG, {"ts": int(time.time())}))
            elif t == T_PONG:
                pass
            elif t in (T_SUBSCRIBE, T_UNSUBSCRIBE):
                if self._agent is not None:
                    self._agent.on_remote_message(conn, msg)
            elif t in (T_STATE_SYNC, T_LOG_TAIL, T_SCHED_STATUS):
                self._on_remote_push(conn, t, data)
            elif isinstance(t, str) and t.startswith("CMD_") and t not in ("CMD_ACK", "CMD_ERR"):
                if self._agent is not None:
                    self._agent.on_remote_message(conn, msg)
                else:
                    self.send(conn, make_msg(T_CMD_ERR,
                                             {"cmd": t, "reason": "cmd not implemented in phase 2-1"}))
            elif t in (T_BLOB_BEGIN, T_BLOB_CHUNK, T_BLOB_END):
                # 本节点为被控端：接收分块（未认证已在上面门槛被 AUTH_FAIL 拦截）
                self._handle_blob(conn, t, data)
            elif t == T_SCRIPT_LIST_REQ:
                self._handle_script_list_req(conn, data)
            elif t == T_SCRIPT_LIST_RES:
                self._on_script_list_res(conn, data)
            elif t == T_SCREENSHOT_REQ:
                # 本节点为被控端：接受远程截图请求（防御性权限复检在方法内）
                self._handle_screenshot_req(conn, data)
            elif t == T_SCREENSHOT_DAT:
                # 本节点为控制端：收到远端截图响应 → 发布 TOPIC_SCREENSHOT
                self._on_screenshot_dat(conn, data)
            else:
                _nl_log("node: ignore message type {!r}".format(t))
        except Exception as e:
            _nl_log("node handle_message error: {}".format(e), "ERROR")

    def _handle_hello(self, conn, data):
        """记录对端 node_id/name/fingerprint；若本端尚未发过 HELLO 则回一条（避免乒乓）。"""
        try:
            node_id = str(data.get("node_id") or "")
            name = str(data.get("name") or "")
            fp = str(data.get("fingerprint") or "")
            reply = False
            with self._lock:
                rec = self._conns.get(conn)
                if rec is None:
                    rec = {"node_id": "", "name": "", "hello_sent": False,
                           "outbound": str(getattr(conn, "name", "") or "").startswith("out:"),
                           "fingerprint": "", "auth_started": False,
                           "subscribed": False, "authed": False}
                    self._conns[conn] = rec
                if node_id:
                    rec["node_id"] = node_id
                if name:
                    rec["name"] = name
                    try:
                        conn.name = name
                    except Exception:
                        pass
                if fp:
                    rec["fingerprint"] = fp
                if not rec.get("hello_sent"):
                    rec["hello_sent"] = True
                    reply = True
            if reply:
                self.send(conn, make_msg(T_HELLO, self._hello_data()))
            # 身份已知 → 出站连接可在 HELLO↔HELLO 之后续发 AUTH
            self._maybe_client_auth(conn)
            # 缺陷 A：HELLO 交换完成后尝试自动订阅（免认证时立即生效）
            self._ensure_subscribed(conn)
        except Exception as e:
            _nl_log("node handle_hello error: {}".format(e), "ERROR")

    # ── 控制端认证（发 AUTH / 处理挑战与结果）────────────────────────────
    def _maybe_client_auth(self, conn):
        """出站连接获知对端身份后按需发起认证（resume 优先，其次待配对 PIN）。"""
        try:
            if self._auth is None or not self._auth.require_auth:
                return
            with self._lock:
                rec = self._conns.get(conn)
                if rec is None or not rec.get("outbound"):
                    return
                if rec.get("auth_started"):
                    return
                node_id = rec.get("node_id") or ""
                if not node_id:
                    return                      # 身份未知，等 HELLO
                name = rec.get("name") or ""
                peer_fp = rec.get("fingerprint") or make_fingerprint(node_id, name)
                pa = getattr(conn, "peer_addr", None)
                try:
                    host, port = str(pa[0]), int(pa[1])
                except Exception:
                    host, port = "", 0
                token = load_peer_token(peer_fp)
                pin = self._targets_pin.get((host, port))
                if not token and not pin:
                    return                      # 等待用户在 UI 上发起配对
                mode = "resume" if token else "pair"
                rec["auth_started"] = True
            self._client_auth_pending[conn] = {
                "mode": mode, "pin": pin, "peer_fp": peer_fp,
                "node_id": node_id, "name": name, "host": host, "port": port,
            }
            self.send(conn, make_msg(T_AUTH, {
                "mode": mode, "node_id": self._node_id,
                "fingerprint": self._self_fp, "name": self._name}))
            _nl_log("node: AUTH({}) → {}:{}".format(mode, host, port))
        except Exception as e:
            _nl_log("node maybe_client_auth error: {}".format(e), "WARN")

    def _handle_client_auth(self, conn, msg):
        """控制端收到 AUTH_CHALLENGE / AUTH_OK / AUTH_FAIL 的处理。"""
        try:
            t = msg.get("t")
            data = msg.get("data") or {}
            if t == T_AUTH_CHALLENGE:
                self._on_auth_challenge(conn, data)
            elif t == T_AUTH_OK:
                self._on_auth_ok(conn, data)
            elif t == T_AUTH_FAIL:
                self._on_auth_fail(conn, data)
        except Exception as e:
            _nl_log("node handle_client_auth error: {}".format(e), "WARN")

    def _on_auth_challenge(self, conn, data):
        """应答挑战：取回/派生 token → 计算 proof → 回 AUTH{nonce,proof,ts}。"""
        nonce = str(data.get("nonce") or "")
        salt = str(data.get("salt") or "")
        need = str(data.get("need") or "")
        with self._lock:
            pending = self._client_auth_pending.get(conn) or {}
        mode = str(pending.get("mode") or "pair")
        token = None
        if mode == "resume" or need == "token":
            token = load_peer_token(pending.get("peer_fp") or "")
        if token is None:
            pin = pending.get("pin")
            if pin and salt:
                token = derive_token(pin, salt)
        if token is None:
            with self._lock:
                pending = self._client_auth_pending.pop(conn, None) or pending
            self.bus.publish(TOPIC_AUTH, self._auth_result(
                conn, False, "", "no credential for challenge", pending))
            return
        ts = int(time.time())
        proof = make_proof(token, nonce, self._node_id, ts)
        pending["token"] = token
        with self._lock:
            self._client_auth_pending[conn] = pending
        self.send(conn, make_msg(T_AUTH, {"nonce": nonce, "proof": proof, "ts": ts}))

    def _on_auth_ok(self, conn, data):
        """认证成功：记录结果 + 对称保存 token + 发 TOPIC_AUTH + 自动订阅。"""
        perm = str(data.get("perm") or "observe")
        with self._lock:
            pending = self._client_auth_pending.pop(conn, None) or {}
            rec = self._conns.get(conn)
            if rec is not None:
                # 出站连接（本机作为控制端）在此记录「本地认证完成」，供 perm_of 判定
                rec["authed"] = True
        token = pending.get("token")
        peer_fp = pending.get("peer_fp") or ""
        if token and peer_fp:
            try:
                store_peer_token(peer_fp, token)
            except Exception:
                pass
        self.bus.publish(TOPIC_AUTH, self._auth_result(conn, True, perm, "", pending))
        _nl_log("node: AUTH_OK from {}:{} perm={}".format(
            pending.get("host"), pending.get("port"), perm))
        # 缺陷 A：出站控制连接认证完成后自动订阅远端状态（幂等）
        self._ensure_subscribed(conn)

    def _on_auth_fail(self, conn, data):
        """认证失败：发 TOPIC_AUTH，供 UI 提示用户。"""
        reason = str(data.get("reason") or "")
        with self._lock:
            pending = self._client_auth_pending.pop(conn, None) or {}
        self.bus.publish(TOPIC_AUTH, self._auth_result(conn, False, "", reason, pending))
        _nl_log("node: AUTH_FAIL from {}:{} reason={}".format(
            pending.get("host"), pending.get("port"), reason), "WARN")

    def _auth_result(self, conn, ok, perm, reason, pending):
        """构造 TOPIC_AUTH 载荷。"""
        host = pending.get("host")
        port = pending.get("port")
        if host in (None, ""):
            pa = getattr(conn, "peer_addr", None)
            try:
                host, port = str(pa[0]), int(pa[1])
            except Exception:
                host, port = "", 0
        return {"stage": "result", "host": host, "port": port,
                "ok": bool(ok), "perm": str(perm or ""), "reason": str(reason or ""),
                "node_id": str(pending.get("node_id") or ""),
                "name": str(pending.get("name") or "")}

    def _handle_peer_control(self, conn, t, data):
        """处理 T_PEER_ADDED / T_PEER_REMOVED（被控端→控制端的配对通知）。"""
        try:
            node_id = str(data.get("node_id") or "") or self._conn_node_id(conn)
            pa = getattr(conn, "peer_addr", None)
            try:
                host, port = str(pa[0]), int(pa[1])
            except Exception:
                host, port = "", 0
            entry = {"node_id": node_id, "name": str(data.get("name") or ""),
                     "host": host, "port": port,
                     "version": str(data.get("version") or ""),
                     "online": (t == T_PEER_ADDED)}
            with self._lock:
                self._peers[node_id] = entry
            self._bus.publish(TOPIC_PEER, dict(entry))
            # 追加：权限变更通知 → 让控制端刷新权限列与操控按钮可用性
            if t == T_PEER_ADDED:
                self._bus.publish(TOPIC_AUTH, {
                    "stage": "perm", "ok": True, "perm": data.get("perm"),
                    "node_id": node_id, "name": entry.get("name") or ""})
        except Exception as e:
            _nl_log("node handle_peer_control error: {}".format(e), "WARN")

    def _on_cmd_result(self, conn, t, data):
        """控制端收到出站连接上的指令回执 → 发布 TOPIC_CMD_RESULT 供 UI 展示。

        Phase3-2 修复：被控端对 SCRIPT_PUSH 的「拒绝」此前只发 CMD_ACK(failed)/
        CMD_ERR，而这里只对 cmd 以 CMD_ 开头的回执发布事件，导致控制端「传输」
        列表看不到远端拒绝。现对 cmd == "SCRIPT_PUSH" 的终态（done/failed）追加
        一条 TOPIC_TRANSFER，使其归并到 OutgoingTransfer 已建立的传输行。
        （accepted 中间态忽略，避免与 OutgoingTransfer 自身进度事件重复。）
        """
        try:
            with self._lock:
                rec = self._conns.get(conn)
            if not (rec and rec.get("outbound")):
                return
            d = dict(data) if isinstance(data, dict) else {}
            payload = {"node_id": self._conn_node_id(conn), "t": t, "data": d}
            self._bus.publish(TOPIC_CMD_RESULT, payload)
            if str(d.get("cmd") or "") != "SCRIPT_PUSH":
                return
            if t == T_CMD_ERR or not d.get("ok", True):
                state = "failed"
            elif str(d.get("status") or "") == "done":
                state = "done"
            else:
                state = "sending"     # accepted 等中间态：交给 OutgoingTransfer 进度事件
            if state == "sending":
                return
            self._bus.publish(transfer.TOPIC_TRANSFER, {
                "node_id": self._conn_node_id(conn),
                "tid": str(d.get("tid") or ""),
                "name": str(d.get("name") or ""),
                "sent": 0, "total": 0, "state": state,
                "detail": str(d.get("reason") or d.get("detail") or "")})
        except Exception as e:
            _nl_log("node on_cmd_result error: {}".format(e), "WARN")

    def perm_of(self, conn):
        """该连接权限（供 agent 权限门槛消费）。

        缺陷 C：出站连接（本机作为控制端主动连出）的认证状态不进入被控端侧
        `AuthManager._authed`，`perm_of` 会返回 None → agent 侧会误判为
        「not authenticated」。对等架构下对端回推自身状态所需最低权限仅为
        observe，故对「已完成认证的出站连接」按 observe 放行；更高权限指令
        （CMD_*）仍按 observe 判定被正确拒绝，不影响被控端鉴权。
        """
        try:
            if self._auth is None:
                return "observe"
            perm = self._auth.perm_of(conn)
            if perm is not None:
                return perm
            with self._lock:
                rec = self._conns.get(conn)
            if rec and rec.get("outbound") and rec.get("authed"):
                return "observe"
            return perm
        except Exception:
            return None

    def _ensure_subscribed(self, conn):
        """连接进入「可订阅」状态后自动下发一次全 topic SUBSCRIBE（幂等）。

        缺陷 A：控制端此前从不主动 SUBSCRIBE，导致 STATE_SYNC / LOG_TAIL /
        SCHED_STATUS 永不推送，窗口「状态/进度/日志/定时任务」列恒为空。
        此处统一在「认证完成」或「免认证连接建立完成」后补订阅：
          * 出站连接（本机控制端连别人）：_on_auth_ok 记录 authed 后订阅；
          * 入站连接（被控端被别人连）：handle_message(T_AUTH) 认证完成后订阅；
          * 断线重连：新连接对象复用了新 rec（subscribed 初始 False），天然重订阅。
        同一连接重复调用只发一次（subscribed 标记，未认证前不消耗该标记）。
        """
        try:
            with self._lock:
                rec = self._conns.get(conn)
                if rec is None or rec.get("subscribed"):
                    return False
                outbound = bool(rec.get("outbound"))
                authed_local = bool(rec.get("authed"))
            if self._auth is not None and self._auth.require_auth:
                # 需认证：入站须 AuthManager.authed；出站以本地认证完成标记为准
                if not (self._auth.authed(conn) or (outbound and authed_local)):
                    return False
            with self._lock:
                rec = self._conns.get(conn)
                if rec is None or rec.get("subscribed"):
                    return False
                rec["subscribed"] = True
            sent = bool(self.send(conn, make_msg(T_SUBSCRIBE,
                                                 {"topics": list(T_TOPICS)})))
            if sent:
                _nl_log("node: auto SUBSCRIBE -> {}".format(
                    getattr(conn, "peer_addr", "")))
            return sent
        except Exception as e:
            _nl_log("node ensure_subscribed error: {}".format(e), "WARN")
            return False

    def _on_remote_push(self, conn, t, data):
        """远端推送（控制端视角）：补 node_id 后发布到总线。"""
        node_id = self._conn_node_id(conn)
        payload = dict(data) if isinstance(data, dict) else {}
        payload["node_id"] = node_id
        if t == T_STATE_SYNC:
            self._bus.publish(TOPIC_PEER_STATE, payload)
        elif t == T_LOG_TAIL:
            self._bus.publish(TOPIC_PEER_LOG, payload)
        elif t == T_SCHED_STATUS:
            self._bus.publish(TOPIC_PEER_SCHED, payload)

    def _conn_node_id(self, conn):
        with self._lock:
            rec = self._conns.get(conn)
        if rec and rec.get("node_id"):
            return rec["node_id"]
        pa = getattr(conn, "peer_addr", None)
        try:
            return "{}:{}".format(pa[0], pa[1])
        except Exception:
            return "unknown"

    def send(self, conn, msg_dict):
        """封装 conn.send，失败返回 False。"""
        try:
            if conn is None:
                return False
            return bool(conn.send(msg_dict))
        except Exception:
            return False

    # ── Phase2-2 远程操控接口 ──────────────────────────────────────────
    def set_control_hooks(self, run=None, stop=None):
        """注入被控端执行钩子（run(loops=None) / stop()），转调 ControlExecutor。"""
        try:
            if self._control is not None:
                self._control.set_hooks(run=run, stop=stop)
                return True
        except Exception as e:
            _nl_log("node set_control_hooks error: {}".format(e), "WARN")
        return False

    def broadcast(self, msg_dict, only_authed=True):
        """向所有（含出站的）已认证连接发送同一消息；返回发送成功数。"""
        count = 0
        try:
            with self._lock:
                conns = list(self._conns.keys())
            for conn in conns:
                try:
                    if (only_authed and self._auth is not None
                            and not self._auth.authed(conn)):
                        continue
                    if self.send(conn, msg_dict):
                        count += 1
                except Exception:
                    continue
        except Exception as e:
            _nl_log("node broadcast error: {}".format(e), "WARN")
        return count

    @property
    def control(self):
        return self._control

    # ── Phase3-1a 脚本分发接口（被控端接收 + 控制端列表/推送）─────────────
    def _handle_blob(self, conn, t, data):
        """把 BLOB_BEGIN/CHUNK/END 交给 IncomingStore（被控端视角）。"""
        try:
            inc = getattr(self, "_incoming", None)
            if inc is None:
                return
            if t == T_BLOB_BEGIN:
                inc.on_begin(conn, data)
            elif t == T_BLOB_CHUNK:
                inc.on_chunk(conn, data)
            elif t == T_BLOB_END:
                inc.on_end(conn, data)
        except Exception as e:
            _nl_log("node handle_blob error: {}".format(e), "ERROR")

    def _handle_script_list_req(self, conn, data):
        """回复 SCRIPT_LIST_RES（仅相对名，不泄露脚本根绝对路径）。"""
        try:
            need = required_perm(T_SCRIPT_LIST_REQ)
            perm = self.perm_of(conn)
            if perm is None:
                self.send(conn, make_msg(T_CMD_ERR, {
                    "cmd": T_SCRIPT_LIST_REQ, "reason": "not authenticated"}))
                return
            if need is not None and not perm_satisfies(perm, need):
                self.send(conn, make_msg(T_CMD_ERR, {
                    "cmd": T_SCRIPT_LIST_REQ,
                    "reason": "permission denied: need " + need}))
                return
            scripts = transfer.list_scripts()
            root = transfer.script_root() or ""
            dirname = os.path.basename(root.rstrip("\\/")) if root else ""
            self.send(conn, make_msg(T_SCRIPT_LIST_RES,
                                     {"scripts": scripts, "dir": dirname}))
        except Exception as e:
            _nl_log("node handle_script_list_req error: {}".format(e), "WARN")

    def _on_script_list_res(self, conn, data):
        """控制端收到远端脚本列表 → 发布 TOPIC_TRANSFER(state=list)。"""
        try:
            with self._lock:
                rec = self._conns.get(conn)
            if not (rec and rec.get("outbound")):
                return  # 仅控制端消费
            payload = {"node_id": self._conn_node_id(conn), "state": "list",
                       "scripts": data.get("scripts") or []}
            self._bus.publish(transfer.TOPIC_TRANSFER, payload)
        except Exception as e:
            _nl_log("node on_script_list_res error: {}".format(e), "WARN")

    # ── Phase4-1 远程截图（被控端应答 + 控制端请求/消费）───────────────────
    def _handle_screenshot_req(self, conn, data):
        """被控端：权限复检 → screen.capture → 回 SCREENSHOT_DAT + 同步写审计。

        审计复用 ControlExecutor 的 actor/remote/写入口；失败静默，不发网络。
        """
        try:
            perm = self.perm_of(conn)
            if not perm_satisfies(perm, "control"):
                self.send(conn, make_msg(T_CMD_ERR, {
                    "cmd": T_SCREENSHOT_REQ, "reason": "permission denied"}))
                return
            d = data if isinstance(data, dict) else {}
            max_width = d.get("max_width")
            quality = d.get("quality")
            try:
                result = screen.capture(max_width=max_width, quality=quality)
            except Exception as e:
                _nl_log("node screenshot capture error: {}".format(e), "WARN")
                result = {"ok": False, "ts": int(time.time()), "width": 0,
                          "height": 0, "format": "jpeg", "data_b64": "",
                          "bytes": 0, "error": "grab failed"}
            try:
                self.send(conn, make_msg(T_SCREENSHOT_DAT, dict(result)))
            except Exception as e:
                _nl_log("node screenshot send error: {}".format(e), "WARN")
            # 审计（同步、不发网络、失败静默）
            try:
                ok = bool(result.get("ok"))
                if ok:
                    detail = "jpg %dx%d %dB" % (
                        int(result.get("width") or 0),
                        int(result.get("height") or 0),
                        int(result.get("bytes") or 0))
                else:
                    detail = str(result.get("error") or "") or "error"
                ctl = getattr(self, "_control", None)
                if ctl is not None:
                    ctl._audit_write(ctl._actor(conn), "SCREENSHOT_REQ",
                                     {"max_width": max_width, "quality": quality},
                                     ("ok" if ok else "err"), detail,
                                     ctl._remote_str(conn))
            except Exception:
                pass
        except Exception as e:
            _nl_log("node handle_screenshot_req error: {}".format(e), "WARN")

    def _on_screenshot_dat(self, conn, data):
        """控制端：收到 SCREENSHOT_DAT → 发布 TOPIC_SCREENSHOT（补 node_id）。"""
        try:
            payload = {"node_id": self._conn_node_id(conn)}
            if isinstance(data, dict):
                payload.update(data)
            self._bus.publish(TOPIC_SCREENSHOT, payload)
        except Exception as e:
            _nl_log("node on_screenshot_dat error: {}".format(e), "WARN")

    def request_screenshot(self, peer_node_id, max_width=None, quality=None):
        """控制端：向该 peer 发送 SCREENSHOT_REQ；找不到连接返回 False。"""
        try:
            conn = transfer.find_conn(self, peer_node_id)
            if conn is None:
                return False
            d = {}
            if max_width is not None:
                d["max_width"] = max_width
            if quality is not None:
                d["quality"] = quality
            return self.send(conn, make_msg(T_SCREENSHOT_REQ, d))
        except Exception as e:
            _nl_log("node request_screenshot error: {}".format(e), "WARN")
            return False

    def list_remote_scripts(self, peer_node_id):
        """向该 peer 发 T_SCRIPT_LIST_REQ；找不到连接返回 False。"""
        try:
            conn = transfer.find_conn(self, peer_node_id)
            if conn is None:
                return False
            return self.send(conn, make_msg(T_SCRIPT_LIST_REQ, {}))
        except Exception as e:
            _nl_log("node list_remote_scripts error: {}".format(e), "WARN")
            return False

    def push_script(self, peer_node_id, local_path, remote_name=None, run=False):
        """推送本地脚本到该 peer；返回 tid 或 None。"""
        try:
            return transfer.push(self, peer_node_id, local_path, remote_name, run)
        except Exception as e:
            _nl_log("node push_script error: {}".format(e), "WARN")
            return None

    def push_script_many(self, peer_node_ids, local_path, remote_name=None,
                         run=False):
        """批量下发：对多个 peer 依次发起同一文件推送（Phase3-2）。

        返回 {"ok": {peer: tid}, "skip": {peer: <原因>}}；失败返回空映射。
        """
        try:
            return transfer.push_many(self, peer_node_ids, local_path,
                                      remote_name, run)
        except Exception as e:
            _nl_log("node push_script_many error: {}".format(e), "WARN")
            return {"ok": {}, "skip": {}}

    # ── Phase2-1b UI 接口：配对窗口 / 白名单 / 目标管理 ────────────────────
    def open_pair_window(self):
        """开启被控端配对窗口；返回 PIN(str) 或 None。"""
        try:
            return self._pair.open()
        except Exception as e:
            _nl_log("node open_pair_window error: {}".format(e), "WARN")
            return None

    def close_pair_window(self):
        """关闭配对窗口。"""
        try:
            self._pair.close()
        except Exception:
            pass

    def pair_window_status(self):
        """配对窗口状态：{"active":bool,"pin":str,"expires_in":int}。"""
        try:
            return {"active": bool(self._pair.active), "pin": self._pair.pin,
                    "expires_in": int(self._pair.expires_in())}
        except Exception:
            return {"active": False, "pin": "", "expires_in": 0}

    def list_peers(self):
        """白名单快照 list[dict]。"""
        try:
            return self._peer_store.all()
        except Exception:
            return []

    def snapshot(self):
        """本机快照：{"info":..., "state":{...}, "sched":{...}, "log":[...最近50条...]}。

        薄封装 self._agent 的 state_snapshot()/sched_snapshot()/log_snapshot()，
        供 Phase4-2 浏览器只读面板使用；任意失败返回空结构，绝不抛。
        """
        out = {"info": {}, "state": {}, "sched": {}, "log": []}
        try:
            out["info"] = dict(self.info)
        except Exception:
            out["info"] = {}
        agent = self._agent
        try:
            if agent is not None and hasattr(agent, "state_snapshot"):
                out["state"] = agent.state_snapshot() or {}
        except Exception:
            out["state"] = {}
        try:
            if agent is not None and hasattr(agent, "sched_snapshot"):
                out["sched"] = agent.sched_snapshot() or {}
        except Exception:
            out["sched"] = {}
        try:
            if agent is not None and hasattr(agent, "log_snapshot"):
                lines, _seq = agent.log_snapshot(0)
                out["log"] = list(lines or [])[-50:]
        except Exception:
            out["log"] = []
        return out

    def remove_peer(self, fingerprint):
        """删除白名单条目并清除其 token；返回 bool。"""
        try:
            ok = self._peer_store.remove(fingerprint)
            forget_peer_token(fingerprint)
            return bool(ok)
        except Exception:
            return False

    def set_peer_perm(self, fingerprint, perm):
        """设置某对端权限（observe/control/script）；返回 bool。

        成功后额外通知：① 持有该指纹的存活已认证连接（让控制端刷新权限）；
        ② 本地总线 TOPIC_AUTH(stage=result)（让被控端自身窗口即时刷新）。
        """
        try:
            ok = bool(self._peer_store.set_perm(fingerprint, perm))
        except Exception:
            return False
        if ok:
            try:
                self._notify_perm_change(fingerprint, perm)
            except Exception as e:
                _nl_log("node notify perm change error: {}".format(e), "WARN")
        return ok

    def _conn_fingerprint(self, conn):
        """该连接在 HELLO 中声明的对端指纹（未知返回空串）。"""
        try:
            with self._lock:
                rec = self._conns.get(conn)
            return str((rec or {}).get("fingerprint") or "")
        except Exception:
            return ""

    def _notify_perm_change(self, fingerprint, perm):
        """权限变更后：通知该对端的存活已认证连接，并刷新本地窗口。"""
        fp = str(fingerprint or "")
        if not fp:
            return False
        try:
            rec = self._peer_store.get(fp) or {}
        except Exception:
            rec = {}
        with self._lock:
            conns = list(self._conns.keys())
        sent = 0
        for conn in conns:
            try:
                if self._auth is not None and not self._auth.authed(conn):
                    continue
                if self._conn_fingerprint(conn) != fp:
                    continue
                if self.send(conn, make_msg(T_PEER_ADDED, {
                        "node_id": self._node_id, "name": self._name,
                        "fingerprint": fp, "perm": perm})):
                    sent += 1
            except Exception:
                continue
        try:
            self._bus.publish(TOPIC_AUTH, {
                "stage": "result", "ok": True, "perm": perm,
                "node_id": str(rec.get("node_id") or ""),
                "name": str(rec.get("name") or ""),
                "fingerprint": fp})
        except Exception:
            pass
        return sent > 0

    def register_target(self, host, port, pin=None):
        """控制端：登记静态对端并（可选）携带待配对 PIN，触发连接。"""
        try:
            h = str(host)
            p = int(port)
        except Exception:
            return
        try:
            with self._lock:
                if (h, p) not in self._static_peers:
                    self._static_peers.append((h, p))
                if pin is not None:
                    self._targets_pin[(h, p)] = str(pin)
            self._on_peer({"node_id": "{}:{}".format(h, p), "name": "",
                           "host": h, "port": p, "version": "", "online": True})
            if self._client is not None:
                try:
                    self._client.connect_to(h, p)
                except Exception:
                    pass
        except Exception as e:
            _nl_log("node register_target error: {}".format(e), "WARN")

    def forget_target(self, host, port):
        """控制端：移除静态对端并断开连接；返回 bool。"""
        try:
            key = (str(host), int(port))
        except Exception:
            return False
        try:
            self._targets_pin.pop(key, None)
            if self._client is not None:
                self._client.disconnect(key[0], key[1])
            with self._lock:
                self._static_peers = [sp for sp in self._static_peers if sp != key]
            return True
        except Exception as e:
            _nl_log("node forget_target error: {}".format(e), "WARN")
            return False

    @property
    def peer_store(self):
        return self._peer_store

    # ── 发现联动 ──────────────────────────────────────────────────────
    def _on_peer(self, info):
        """Discovery 回调：发布 TOPIC_PEER + 维护 peers 表 + 驱动 client 出站连接。"""
        try:
            if not isinstance(info, dict):
                return
            host = str(info.get("host") or "")
            port = _safe_int(info.get("port"), 0) or 0
            nid = str(info.get("node_id") or "") or "{}:{}".format(host, port)
            online = bool(info.get("online", True))
            entry = {"node_id": nid, "name": str(info.get("name") or ""),
                     "host": host, "port": port,
                     "version": str(info.get("version") or ""),
                     "online": online}
            with self._lock:
                self._peers[nid] = entry
                targets = [(p["host"], p["port"]) for p in self._peers.values()
                           if p.get("online") and p.get("host") and p.get("port")]
            self._bus.publish(TOPIC_PEER, dict(entry))
            if self._client is not None:
                self._client.set_targets(targets)
        except Exception as e:
            _nl_log("node _on_peer error: {}".format(e), "ERROR")

    def _emit_static_peers(self):
        for host, port in list(self._static_peers):
            self._on_peer({"node_id": "{}:{}".format(host, port), "name": "",
                           "host": host, "port": port, "version": "", "online": True})

    # ── 心跳线程 ──────────────────────────────────────────────────────
    def _heartbeat_loop(self):
        try:
            while not self._stop_evt.is_set():
                self._stop_evt.wait(HEARTBEAT_INTERVAL)
                if self._stop_evt.is_set():
                    break
                try:
                    self._heartbeat_tick()
                except Exception as e:
                    _nl_log("node heartbeat error: {}".format(e), "WARN")
        except Exception as e:
            _nl_log("node heartbeat fatal: {}".format(e), "ERROR")

    def _heartbeat_tick(self):
        with self._lock:
            conns = list(self._conns.keys())
        for conn in conns:
            try:
                if not conn.alive:
                    continue
                if conn.is_stale(STALE_TIMEOUT):
                    _nl_log("node: stale conn {}, closing".format(
                        getattr(conn, "peer_addr", "")), "WARN")
                    conn.close()
                    continue
                self.send(conn, make_msg(T_PING, {"ts": int(time.time())}))
            except Exception as e:
                _nl_log("node heartbeat conn error: {}".format(e), "WARN")
