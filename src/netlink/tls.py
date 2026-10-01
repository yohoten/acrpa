# -*- coding: utf-8 -*-
"""ACRPA NetLink — TLS 可选档（stdlib ssl + 证书指纹固定，Python 3.7 兼容）。

设计要点（为什么这样做）：
  * 只用标准库 ssl，绝不引入 cryptography / cffi（打包体积与兼容性）；
  * 自签证书无法做链验证 → 客户端 use verify_mode=CERT_NONE + check_hostname=False，
    改用 sha256 证书指纹「TOFU（首次信任）」固定；
  * 指纹不符必须拒绝连接（绝不静默降级为明文）；
  * 本模块不 import utils、不顶层 import GUI；日志走 connection._nl_log；
  * 所有函数整体 try/except，异常绝不向上抛，失败返回「稳定英文原因」；
  * 顶层不 import ssl（headless/frozen 环境安全），函数内按需 import。

稳定英文原因（UI / 测试依赖，勿随意改动）：
  "tls unavailable" / "cert missing" / "key missing" / "cert load failed"
  / "server handshake failed" / "client handshake failed"
"""
import hashlib
import os
import threading

from .connection import _nl_log


# 可选：指纹也可只存 state.NETLINK_TLS_PINS（本项目实际只用 state）；
# 该前缀保留给「按对端分片存 Windows 凭据库」的后续扩展。
PIN_TARGET_PREFIX = "ACRPA/netlink/tls-pin/"

# 原因短语（供调用方/UI 复用，保持稳定）
R_TLS_UNAVAILABLE = "tls unavailable"
R_CERT_MISSING = "cert missing"
R_KEY_MISSING = "key missing"
R_CERT_LOAD_FAILED = "cert load failed"
R_SERVER_HANDSHAKE_FAILED = "server handshake failed"
R_CLIENT_HANDSHAKE_FAILED = "client handshake failed"

_PIN_LOCK = threading.Lock()


def _import_state():
    """懒 import state；失败返回 None（不抛）。"""
    try:
        import state
        return state
    except Exception as e:
        _nl_log("tls: import state failed: {}".format(e), "WARN")
        return None


def _norm(fp):
    """指纹规范化：去空白 + 小写。"""
    return str(fp or "").strip().lower()


def available():
    """ssl 模块是否可用（Windows 上恒 True）；任何异常返回 False。"""
    try:
        import ssl  # noqa: F401
        return True
    except Exception as e:
        _nl_log("tls: ssl unavailable: {}".format(e), "WARN")
        return False


# ── 上下文构造 ─────────────────────────────────────────────────────────────
def server_context(cert_path, key_path):
    """构造服务端 SSLContext。

    返回 (SSLContext, None) 或 (None, <稳定英文原因>)；失败原因 ∈
    {"cert missing", "key missing", "cert load failed", "tls unavailable"}。
    """
    try:
        import ssl
    except Exception as e:
        _nl_log("tls: server_context ssl import failed: {}".format(e), "WARN")
        return None, R_TLS_UNAVAILABLE
    try:
        cert = str(cert_path or "")
        key = str(key_path or "")
        if not cert or not os.path.isfile(cert):
            return None, R_CERT_MISSING
        if not key or not os.path.isfile(key):
            return None, R_KEY_MISSING
        try:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        except Exception as e:
            _nl_log("tls: SSLContext(TLS_SERVER) failed: {}".format(e), "WARN")
            return None, R_TLS_UNAVAILABLE
        try:
            ctx.load_cert_chain(certfile=cert, keyfile=key)
        except Exception as e:
            _nl_log("tls: load_cert_chain failed: {}".format(e), "WARN")
            return None, R_CERT_LOAD_FAILED
        # 服务端不请求客户端证书（自签场景用指纹固定替代链验证）
        try:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        except Exception:
            pass
        return ctx, None
    except Exception as e:
        _nl_log("tls: server_context error: {}".format(e), "WARN")
        return None, R_CERT_LOAD_FAILED


def client_context():
    """构造宽松客户端 SSLContext（自签无法链验证，改指纹固定）。

    返回 (SSLContext, None) 或 (None, <稳定英文原因>)。
    """
    try:
        import ssl
    except Exception as e:
        _nl_log("tls: client_context ssl import failed: {}".format(e), "WARN")
        return None, R_TLS_UNAVAILABLE
    try:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        # 顺序敏感：必须先把 check_hostname 置 False，才能把 verify_mode 置 CERT_NONE
        try:
            ctx.check_hostname = False
        except Exception:
            pass
        try:
            ctx.verify_mode = ssl.CERT_NONE
        except Exception:
            pass
        return ctx, None
    except Exception as e:
        _nl_log("tls: client_context error: {}".format(e), "WARN")
        return None, R_TLS_UNAVAILABLE


# ── socket 包裹（握手阻塞，调用方需先设 timeout）─────────────────────────────
def wrap_server(sock, ctx):
    """服务端握手包裹；返回 (SSLSocket, None) 或 (None, <稳定英文原因>)。"""
    try:
        if sock is None or ctx is None:
            return None, R_TLS_UNAVAILABLE
        return ctx.wrap_socket(sock, server_side=True), None
    except Exception as e:
        _nl_log("tls: server handshake failed: {}".format(e), "WARN")
        return None, R_SERVER_HANDSHAKE_FAILED


def wrap_client(sock, ctx, server_hostname=None):
    """客户端握手包裹；返回 (SSLSocket, None) 或 (None, <稳定英文原因>)。"""
    try:
        if sock is None or ctx is None:
            return None, R_TLS_UNAVAILABLE
        return ctx.wrap_socket(sock, server_hostname=server_hostname), None
    except Exception as e:
        _nl_log("tls: client handshake failed: {}".format(e), "WARN")
        return None, R_CLIENT_HANDSHAKE_FAILED


# ── 指纹 ───────────────────────────────────────────────────────────────────
def cert_fingerprint(ssl_sock):
    """对端证书 sha256 指纹（hex）；无证书/失败返回空串。"""
    try:
        der = ssl_sock.getpeercert(binary_form=True)
        if not der:
            return ""
        return hashlib.sha256(der).hexdigest()
    except Exception as e:
        _nl_log("tls: cert_fingerprint failed: {}".format(e), "WARN")
        return ""


# ── 指纹固定（TOFU）────────────────────────────────────────────────────────
def _pins_list():
    """当前已固定指纹快照 list[str]（state 不可用返回空表）。"""
    st = _import_state()
    if st is None:
        return []
    try:
        pins = getattr(st, "NETLINK_TLS_PINS", None)
        if not isinstance(pins, list):
            return []
        return pins
    except Exception:
        return []


def pin_known(fingerprint):
    """该指纹是否已固定在 state.NETLINK_TLS_PINS 中。"""
    fp = _norm(fingerprint)
    if not fp:
        return False
    try:
        return fp in [_norm(x) for x in _pins_list()]
    except Exception:
        return False


def pin_add(fingerprint):
    """追加指纹到 state.NETLINK_TLS_PINS 并 save_config（去重）；返回 bool。"""
    fp = _norm(fingerprint)
    if not fp:
        return False
    try:
        st = _import_state()
        with _PIN_LOCK:
            if st is None:
                return False
            pins = getattr(st, "NETLINK_TLS_PINS", None)
            pins = list(pins) if isinstance(pins, list) else []
            if fp in [_norm(x) for x in pins]:
                return True
            pins.append(fp)
            st.NETLINK_TLS_PINS = pins
            try:
                st.save_config()
            except Exception:
                pass
        return True
    except Exception as e:
        _nl_log("tls: pin_add failed: {}".format(e), "WARN")
        return False


def pin_check_or_tofu(fingerprint):
    """已固定 → (True,"pinned")；未固定 → 采纳并固定，返回 (True,"tofu")。

    本项目采用 TOFU（首次信任）；本函数不做「不符即拒绝」，仅供明确需要
    「仅记录首次信任」的调用点使用（如只读监控）。连接建立请用
    pin_verify_or_reject 以获得「已固定但不符即拒绝」的强保证。
    """
    fp = _norm(fingerprint)
    if not fp:
        return False, "empty fingerprint"
    if pin_known(fp):
        return True, "pinned"
    pin_add(fp)
    return True, "tofu"


def pin_verify_or_reject(fingerprint):
    """连接建立用强校验：

      已固定且相符 → (True,"ok")；已固定但不符 → (False,"pin mismatch")；
      未固定（列表为空）→ (True,"tofu") 并 pin_add。
    """
    fp = _norm(fingerprint)
    if not fp:
        return False, "pin mismatch"
    if pin_known(fp):
        return True, "ok"
    # 列表非空但本指纹不在其中 → 指纹不符，拒绝（绝不降级为信任新指纹）
    if _pins_list():
        return False, "pin mismatch"
    pin_add(fp)
    return True, "tofu"
