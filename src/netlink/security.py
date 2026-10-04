# -*- coding: utf-8 -*-
"""ACRPA NetLink — 安全内核（Phase 2-1a：配对认证 + 权限分级 + 白名单持久化）。

设计要点（为什么这样做）：
  * PIN 绝不明文上网：PIN + salt 经 PBKDF2-HMAC-SHA256 派生 token，网络只传挑战/证明；
  * 敏感 token 只进 Windows 凭据库（state.cred_write），绝不落 config.json；
  * 全部后台/回调逻辑整体 try/except，异常只 _nl_log 或 bus.publish，绝不向上抛；
  * 本模块不 import utils（utils 顶层 import tkinter），日志走 connection._nl_log。

认证四步（PIN 不在线路上出现）：
  1. 控制端 → 被控端  AUTH{mode,node_id,fingerprint,name}                 （无 proof）
  2. 被控端 → 控制端  AUTH_CHALLENGE{nonce,need,salt(仅 need==pin),ts}
  3. 控制端 → 被控端  AUTH{nonce,proof,ts}   proof=HMAC(token,"nonce|node_id|ts")
  4. 被控端校验 → AUTH_OK{perm,server_node_id,server_name} / AUTH_FAIL{reason}
"""
import hashlib
import hmac
import secrets
import threading
import time

from .connection import _nl_log
from .protocol import make_msg, T_AUTH_CHALLENGE, T_AUTH_OK, T_AUTH_FAIL

try:
    import state as _state
except Exception:          # 极端环境下 state 不可用时降级（凭据库/持久化静默失效）
    _state = None


# 被控端在认证失败过载时向总线告警用的主题（与 node.TOPIC_STATUS 同名同值）
_TOPIC_STATUS = "netlink.status"


# ── 算法参数 ─────────────────────────────────────────────────────────────
PBKDF2_ROUNDS = 100000
TOKEN_LEN = 32          # bytes
SALT_LEN = 16           # bytes
NONCE_TTL = 60          # 秒
MAX_AUTH_FAILS = 5
PROOF_MAX_SKEW = 60     # 秒

# ── 配对认证防暴力（IP 维度 + 全局，独立于控制 PIN 的 _pin_*）──────────
# 阈值/退避风格与控制 PIN（MAX_PIN_FAILS/LOCK_BASE/LOCK_CAP）对齐。
MAX_AUTH_FAILS_PER_IP = 5       # 同一 IP 失败阈值，达到后按指数退避锁定
MAX_AUTH_FAILS_GLOBAL = 20      # 全局失败阈值（跨 IP，抵御换源分布式暴破）
AUTH_LOCK_BASE = 300            # 首次锁定秒数
AUTH_LOCK_CAP = 3600            # 锁定上限（1h）

VALID_PERMS = ("observe", "control", "script")
PERM_LABELS = {"observe": "仅观察", "control": "允许操控", "script": "允许接收脚本"}

_PERM_RANK = {"observe": 0, "control": 1, "script": 2}
_PERM_RANK_UNKNOWN = 99


# ── 基础密码学原语 ───────────────────────────────────────────────────────
def new_pin():
    """6 位数字字符串（用于人工核对的一次性配对码）。"""
    return "".join(secrets.choice("0123456789") for _ in range(6))


def new_salt():
    """配对 salt（hex，16 bytes）。"""
    return secrets.token_hex(SALT_LEN)


def new_nonce():
    """认证挑战 nonce（hex，16 bytes）。"""
    return secrets.token_hex(16)


def derive_token(pin, salt_hex):
    """PIN + salt → 32 字节 token（PBKDF2-HMAC-SHA256，10 万轮）。"""
    return hashlib.pbkdf2_hmac(
        "sha256", str(pin).encode("utf-8"),
        bytes.fromhex(str(salt_hex)), PBKDF2_ROUNDS, TOKEN_LEN)


def make_proof(token, nonce, node_id, ts):
    """HMAC-SHA256(token, "nonce|node_id|ts") 的 hexdigest。"""
    payload = "{0}|{1}|{2}".format(nonce, node_id, ts).encode("utf-8")
    return hmac.new(token, payload, hashlib.sha256).hexdigest()


def verify_proof(token, nonce, node_id, ts, proof_hex, now=None):
    """常数时间比较 proof；ts 与 now 偏差 > PROOF_MAX_SKEW 直接 False。"""
    try:
        if now is None:
            now = time.time()
        try:
            if abs(float(now) - float(ts)) > PROOF_MAX_SKEW:
                return False
        except Exception:
            return False
        expect = make_proof(token, nonce, node_id, ts)
        return hmac.compare_digest(expect, str(proof_hex or ""))
    except Exception:
        return False


def make_fingerprint(node_id, name="", machine_id=""):
    """设备指纹：sha256("node_id|name|machine_id") hexdigest 前 32 位。"""
    raw = "{0}|{1}|{2}".format(node_id, name, machine_id).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:32]


# ── 凭据库封装（token 只存 Windows 凭据库）──────────────────────────────
def peer_token_target(fingerprint):
    """某对端 token 的凭据库 target 名。"""
    return "ACRPA/netlink/peer/" + str(fingerprint or "")


def store_peer_token(fingerprint, token):
    """token(bytes) → hex 后写入凭据库；失败返回 False，不抛。"""
    try:
        if not fingerprint or not token:
            return False
        if _state is None:
            return False
        hexstr = token.hex() if isinstance(token, (bytes, bytearray)) else str(token)
        return bool(_state.cred_write(peer_token_target(fingerprint), hexstr))
    except Exception:
        return False


def load_peer_token(fingerprint):
    """读取该对端 token；不存在/失败返回 None，不抛。"""
    try:
        if not fingerprint or _state is None:
            return None
        hexstr = _state.cred_read(peer_token_target(fingerprint))
        if not hexstr:
            return None
        return bytes.fromhex(hexstr.strip())
    except Exception:
        return None


def forget_peer_token(fingerprint):
    """删除该对端 token。"""
    try:
        if fingerprint and _state is not None:
            _state.cred_delete(peer_token_target(fingerprint))
    except Exception:
        pass


# ═════════════════════════════════════════════════════════════════════════
# 网页面板「有限控制」控制 PIN（Phase4-2 批次1：内核 + PIN 存储）
#
# 与配对 PIN 的区别：
#   * 配对 PIN 是「一次性、短期、被控端现场展示给控制端」；
#   * 控制 PIN 是「本机所有者持有的长期第二因子」，用于解锁网页面板的
#     run/stop 有限控制 —— 因此必须可校验（不能只存短期窗口），
#     只能存「salt + PBKDF2 派生值」，绝不落 config.json / 绝不落日志。
#
# 存储格式（单字符串，UTF-8，写入 Windows 凭据库）：
#     pbkdf2$<salt_hex(32)>$<dkhash_hex(64)>
# 前缀预留算法演进（未来可 argon2$ / scrypt$）。
# ═════════════════════════════════════════════════════════════════════════
CONTROL_PIN_TARGET = "ACRPA/netlink/web-control-pin"

# 防暴力：与 MAX_AUTH_FAILS 对齐；锁定 300s 起，指数退避，上限 1h
MAX_PIN_FAILS = 5
LOCK_BASE = 300
LOCK_CAP = 3600

_PIN_LOCK = threading.RLock()
_pin_fails = 0
_pin_lock_until = 0.0
_pin_last_fail = 0.0

# 会话清空钩子预留：批次2 的 _ControlSessionStore.lock_all 在此接入；
# 「设置/重置/清除控制 PIN」即触发（防止旧会话继续持有已失效授权的控制权）。
_session_reset_hooks = []


def control_pin_target():
    """控制 PIN 的凭据库 target 名（稳定常量）。"""
    return CONTROL_PIN_TARGET


def _control_pin_audit(cmd, result, detail, actor="web-panel", remote=""):
    """写控制 PIN 相关审计（复用 AuditLog 单点落盘；失败静默，绝不抛）。"""
    try:
        global _control_pin_audit_log
        if _control_pin_audit_log is None:
            from .audit import AuditLog
            _control_pin_audit_log = AuditLog()
        _control_pin_audit_log.write(actor, cmd, {}, result, detail, remote)
    except Exception:
        pass


_control_pin_audit_log = None


# 配对认证审计单例（惰性创建；与 _control_pin_audit 并存，互不干扰）
_pair_audit_log = None


def _pair_audit(cmd, result, detail, remote=""):
    """写配对认证相关审计（复用 AuditLog 单点落盘；失败静默，绝不抛）。

    仅记录 reason / IP 等非敏感信息，绝不写入 PIN / proof / salt。
    """
    try:
        global _pair_audit_log
        if _pair_audit_log is None:
            from .audit import AuditLog
            _pair_audit_log = AuditLog()
        _pair_audit_log.write("netlink-peer", cmd, {}, result, detail, remote)
    except Exception:
        pass


def _load_control_pin_record():
    """读取并解析凭据库中的控制 PIN 记录；无/格式非法返回 None，不抛。"""
    try:
        if _state is None:
            return None
        raw = _state.cred_read(CONTROL_PIN_TARGET)
        if not raw:
            return None
        parts = str(raw).strip().split("$")
        if len(parts) != 3 or parts[0] != "pbkdf2":
            return None
        salt_hex, dk_hex = parts[1], parts[2]
        if not salt_hex or not dk_hex:
            return None
        return salt_hex, dk_hex
    except Exception:
        return None


def _is_valid_pin(pin):
    """PIN 规格 ^\\d{6}$（ASCII 六位数字，便于手机输入）。"""
    try:
        p = str(pin or "").strip()
        return len(p) == 6 and all(c in "0123456789" for c in p)
    except Exception:
        return False


def has_control_pin():
    """凭据库中是否存在合法的控制 PIN 记录。"""
    return _load_control_pin_record() is not None


def _pin_lock_state_now(now=None):
    """(locked, retry_after, fails) —— 内部查询，不产生副作用。"""
    now = time.time() if now is None else now
    with _PIN_LOCK:
        fails = int(_pin_fails)
        until = float(_pin_lock_until)
    if until > now:
        return True, int(until - now) + 1, fails
    return False, 0, fails


def control_pin_lock_state():
    """当前防暴力锁定状态：{"locked":bool,"retry_after":int,"fails":int}。

    供批次2 的 /api/control/status 与 unlock 的 429 Retry-After 使用。
    """
    try:
        locked, retry, fails = _pin_lock_state_now()
        return {"locked": bool(locked), "retry_after": int(retry), "fails": int(fails)}
    except Exception:
        return {"locked": False, "retry_after": 0, "fails": 0}


def _pin_fail(now=None, actor="web-panel", remote=""):
    """记一次失败：fails+1；达上限后按指数退避置锁定，并写审计。"""
    now = time.time() if now is None else now
    try:
        global _pin_fails, _pin_lock_until, _pin_last_fail
        with _PIN_LOCK:
            _pin_fails += 1
            _pin_last_fail = now
            fails = int(_pin_fails)
            retry = 0
            if fails >= MAX_PIN_FAILS:
                delay = min(LOCK_BASE * (2 ** (fails - MAX_PIN_FAILS)), LOCK_CAP)
                _pin_lock_until = now + delay
                retry = int(delay)
        _control_pin_audit("WEB_PIN_FAIL", "err",
                           "fails={}/{}".format(fails, MAX_PIN_FAILS),
                           actor=actor, remote=remote)
        if retry:
            _nl_log("control pin locked for {}s after {} fails".format(retry, fails),
                    "WARN")
    except Exception as e:
        _nl_log("control pin fail counter error: {}".format(e), "WARN")


def _pin_reset_fails():
    """清零失败计数与锁定（成功校验 / 重置 PIN / 测试复位）。"""
    try:
        global _pin_fails, _pin_lock_until, _pin_last_fail
        with _PIN_LOCK:
            _pin_fails = 0
            _pin_lock_until = 0.0
            _pin_last_fail = 0.0
    except Exception:
        pass


def reset_control_pin_guard():
    """清空防暴力失败计数与锁定（GUI「重置 PIN」或批处理/测试复位用）。"""
    _pin_reset_fails()
    return True


def register_control_session_reset_hook(fn):
    """注册「控制 PIN 变更 → 清空全部控制会话」回调（批次2 会话表接入点）。

    fn() 无参、无返回值；重复注册幂等；None/非可调用值忽略。
    """
    try:
        if fn is None or not callable(fn):
            return False
        with _PIN_LOCK:
            if fn not in _session_reset_hooks:
                _session_reset_hooks.append(fn)
        return True
    except Exception:
        return False


def _fire_control_session_reset(reason=""):
    """触发全部会话清空回调（钩子未接入时为空操作），异常只记日志。"""
    try:
        with _PIN_LOCK:
            hooks = list(_session_reset_hooks)
    except Exception:
        hooks = []
    for fn in hooks:
        try:
            fn()
        except Exception as e:
            _nl_log("control session reset hook error: {}".format(e), "WARN")


def store_control_pin(pin, actor="gui", remote=""):
    """设置/重置控制 PIN（salt + PBKDF2-10万轮）→ 凭据库；成功返回 True。

    成功后：清空防暴力计数 + 触发「清空全部控制会话」钩子 + 审计 WEB_PIN_SET。
    PIN 规格非法或凭据库写入失败 → False（不写审计，不抛）。
    """
    try:
        if _state is None:
            return False
        p = str(pin or "").strip()
        if not _is_valid_pin(p):
            _nl_log("control pin rejected: invalid format", "WARN")
            return False
        salt_hex = new_salt()
        dk_hex = derive_token(p, salt_hex).hex()
        blob = "pbkdf2${}${}".format(salt_hex, dk_hex)
        if not _state.cred_write(CONTROL_PIN_TARGET, blob):
            return False
        _pin_reset_fails()
        _control_pin_audit("WEB_PIN_SET", "ok", "action=set",
                           actor=actor, remote=remote)
        _fire_control_session_reset("pin set")
        return True
    except Exception as e:
        _nl_log("store control pin failed: {}".format(e), "WARN")
        return False


def verify_control_pin(pin, actor="web-panel", remote=""):
    """校验控制 PIN（常量时间比较）。含防暴力：锁定期间正确 PIN 亦拒绝。

    返回值单一 bool（与设计文档一致）；锁定态请用 control_pin_lock_state()。
    失败必写审计（WEB_PIN_FAIL）；锁定期间尝试写 WEB_UNLOCK result=rejected
    （避免「静默拒绝」造成审计盲区）。
    """
    try:
        now = time.time()
        locked, retry, _fails = _pin_lock_state_now(now)
        if locked:
            _control_pin_audit("WEB_UNLOCK", "rejected",
                               "locked retry_after={}".format(retry),
                               actor=actor, remote=remote)
            return False
        rec = _load_control_pin_record()
        if rec is None:
            _pin_fail(now, actor=actor, remote=remote)
            return False
        salt_hex, dk_hex = rec
        try:
            cand = derive_token(str(pin or ""), salt_hex).hex()
        except Exception:
            cand = ""
        if cand and hmac.compare_digest(cand, dk_hex):
            _pin_reset_fails()
            return True
        _pin_fail(now, actor=actor, remote=remote)
        return False
    except Exception as e:
        _nl_log("verify control pin failed: {}".format(e), "WARN")
        return False


def forget_control_pin(actor="gui", remote=""):
    """清除控制 PIN：删除凭据库记录 + 清锁定 + 触发会话清空钩子（不抛）。"""
    try:
        if _state is not None:
            _state.cred_delete(CONTROL_PIN_TARGET)
        _pin_reset_fails()
        _control_pin_audit("WEB_PIN_SET", "ok", "action=clear",
                           actor=actor, remote=remote)
        _fire_control_session_reset("pin cleared")
    except Exception as e:
        _nl_log("forget control pin failed: {}".format(e), "WARN")


# ── 权限映射 ─────────────────────────────────────────────────────────────
_PERM_MAP = {
    "PING": "observe", "PONG": "observe",
    "SUBSCRIBE": "observe", "UNSUBSCRIBE": "observe",
    "STATE_SYNC": "observe", "LOG_TAIL": "observe", "SCHED_STATUS": "observe",
    "CMD_RUN": "control", "CMD_PAUSE": "control", "CMD_RESUME": "control",
    "CMD_STOP": "control", "CMD_RUN_SCRIPT": "control",
    # Phase4-1：远程截图属操控类，observe 不允许（SCREENSHOT_DAT 为响应方向，不映射）
    "SCREENSHOT_REQ": "control",
    "SCRIPT_LIST_REQ": "script",
    "BLOB_BEGIN": "script", "BLOB_CHUNK": "script", "BLOB_END": "script",
}


def required_perm(msg_type):
    """某消息类型所需最低权限；未知类型返回 None。"""
    return _PERM_MAP.get(str(msg_type or ""))


def perm_satisfies(perm, need):
    """判断 perm 是否达到 need（need=None 视为放行）。"""
    if need is None:
        return True
    have = _PERM_RANK.get(str(perm or ""), -1)
    want = _PERM_RANK.get(str(need), _PERM_RANK_UNKNOWN)
    return have >= want


# ── 配对窗口（被控端短期 PIN）────────────────────────────────────────────
class PairWindow(object):
    """被控端短期配对窗口：生成 PIN+salt，TTL 到期自动失效。线程安全。"""

    def __init__(self, ttl=600):
        try:
            self._ttl = int(ttl) if ttl else 600
        except Exception:
            self._ttl = 600
        if self._ttl <= 0:
            self._ttl = 600
        self._lock = threading.RLock()
        self._pin = ""
        self._salt = ""
        self._expire = 0.0

    def open(self):
        """生成新 PIN+salt 并重置过期时间；返回 PIN(str)。"""
        with self._lock:
            self._pin = new_pin()
            self._salt = new_salt()
            self._expire = time.time() + self._ttl
            return self._pin

    def close(self):
        with self._lock:
            self._pin = ""
            self._salt = ""
            self._expire = 0.0

    @property
    def active(self):
        with self._lock:
            return bool(self._pin) and time.time() < self._expire

    @property
    def pin(self):
        with self._lock:
            return self._pin if (self._pin and time.time() < self._expire) else ""

    @property
    def salt(self):
        with self._lock:
            return self._salt if (self._salt and time.time() < self._expire) else ""

    def expires_in(self):
        """剩余有效秒数；<=0 表示已过期/未开启。"""
        with self._lock:
            if not self._pin:
                return 0
            return int(self._expire - time.time())


# ── nonce 库（防重放）────────────────────────────────────────────────────
class NonceStore(object):
    """单次有效的 nonce 存储，按连接隔离，NONCE_TTL 过期。线程安全。"""

    def __init__(self):
        self._lock = threading.RLock()
        self._items = {}   # {conn_key: (nonce, expire_ts)}

    def issue(self, conn_key):
        """为连接签发新 nonce 并返回（覆盖旧的未消费 nonce）。"""
        nonce = new_nonce()
        with self._lock:
            self._purge()
            self._items[conn_key] = (nonce, time.time() + NONCE_TTL)
        return nonce

    def consume(self, conn_key, nonce):
        """校验并消费 nonce；成功 True（一次性），重复/过期/不匹配 False。"""
        try:
            with self._lock:
                self._purge()
                item = self._items.get(conn_key)
                if not item:
                    return False
                got, exp = item
                if time.time() >= exp:
                    self._items.pop(conn_key, None)
                    return False
                if not hmac.compare_digest(str(got), str(nonce or "")):
                    return False
                self._items.pop(conn_key, None)
                return True
        except Exception:
            return False

    def forget(self, conn_key):
        with self._lock:
            self._items.pop(conn_key, None)

    def _purge(self):
        try:
            now = time.time()
            for k in list(self._items.keys()):
                if self._items[k][1] <= now:
                    self._items.pop(k, None)
        except Exception:
            pass


# ── 白名单（持久化到 state.NETLINK_PEERS，只存非敏感字段）────────────────
class PeerStore(object):
    """已配对设备白名单。persist=False 时不读写 state（供测试用）。线程安全。"""

    FIELDS = ("node_id", "name", "fingerprint", "perm", "added_at", "last_seen")

    def __init__(self, persist=True):
        self._persist = bool(persist)
        self._lock = threading.RLock()
        self._items = []
        if self._persist:
            self._load()

    # ── 内部 ──
    def _load(self):
        try:
            raw = getattr(_state, "NETLINK_PEERS", None) if _state else None
            if not isinstance(raw, list):
                return
            for it in raw:
                if isinstance(it, dict) and it.get("fingerprint"):
                    self._items.append(self._norm(it))
        except Exception as e:
            _nl_log("peer_store load error: {}".format(e), "WARN")

    def _norm(self, it):
        now = int(time.time())
        perm = it.get("perm")
        if perm not in VALID_PERMS:
            perm = "observe"
        return {
            "node_id": str(it.get("node_id") or ""),
            "name": str(it.get("name") or ""),
            "fingerprint": str(it.get("fingerprint") or ""),
            "perm": perm,
            "added_at": int(it.get("added_at") or now),
            "last_seen": int(it.get("last_seen") or now),
        }

    def _save(self):
        if not self._persist or _state is None:
            return
        try:
            _state.NETLINK_PEERS = [dict(it) for it in self._items]
            _state.save_config()
        except Exception as e:
            _nl_log("peer_store save error: {}".format(e), "WARN")

    # ── 查询 ──
    def all(self):
        with self._lock:
            return [dict(it) for it in self._items]

    def get(self, fingerprint):
        fp = str(fingerprint or "")
        with self._lock:
            for it in self._items:
                if it["fingerprint"] == fp:
                    return dict(it)
        return None

    def get_by_node(self, node_id):
        nid = str(node_id or "")
        with self._lock:
            for it in self._items:
                if it["node_id"] == nid:
                    return dict(it)
        return None

    # ── 增删改 ──
    def add(self, node_id, name, fingerprint, perm="observe"):
        """新增或更新一条白名单；返回该条 dict。新条目 perm 取入参，旧条目保留原 perm。"""
        nid = str(node_id or "")
        nm = str(name or "")
        fp = str(fingerprint or "")
        pm = perm if perm in VALID_PERMS else "observe"
        now = int(time.time())
        with self._lock:
            found = None
            for it in self._items:
                if it["fingerprint"] == fp:
                    found = it
                    break
            if found is None:
                found = {"node_id": nid, "name": nm, "fingerprint": fp,
                         "perm": pm, "added_at": now, "last_seen": now}
                self._items.append(found)
            else:
                if nid:
                    found["node_id"] = nid
                if nm:
                    found["name"] = nm
                found["last_seen"] = now
            out = dict(found)
        self._save()
        return out

    def remove(self, fingerprint):
        fp = str(fingerprint or "")
        with self._lock:
            before = len(self._items)
            self._items = [it for it in self._items if it["fingerprint"] != fp]
            changed = len(self._items) != before
        if changed:
            self._save()
        return changed

    def set_perm(self, fingerprint, perm):
        if perm not in VALID_PERMS:
            return False
        fp = str(fingerprint or "")
        ok = False
        with self._lock:
            for it in self._items:
                if it["fingerprint"] == fp:
                    it["perm"] = perm
                    ok = True
                    break
        if ok:
            self._save()
        return ok

    def touch(self, fingerprint):
        """更新 last_seen（不落盘，避免高频写配置）。"""
        fp = str(fingerprint or "")
        now = int(time.time())
        with self._lock:
            for it in self._items:
                if it["fingerprint"] == fp:
                    it["last_seen"] = now
                    return

    def is_trusted(self, fingerprint):
        return self.get(fingerprint) is not None


# ── 认证状态机（被控端侧）────────────────────────────────────────────────
class AuthManager(object):
    """被控端认证状态机：挑战下发 + 证明校验 + 权限归属。线程安全。"""

    def __init__(self, node, peer_store, pair_window, require_auth=True):
        self._node = node
        self._peer_store = peer_store
        self._pair = pair_window
        self._require_auth = bool(require_auth)
        self._lock = threading.RLock()
        self._nonce = NonceStore()
        self._authed = {}    # {conn: fingerprint}
        self._pending = {}   # {conn: {"mode","node_id","fingerprint","name","salt","need"}}
        self._fails = {}     # {conn: count}  —— 既有 per-conn 维度（保持不变）
        # ── 防暴力（IP 维度 + 全局；全部由 self._lock 保护）──
        self._fails_by_ip = {}        # {ip: fail_count}
        self._lock_until_by_ip = {}   # {ip: unlock_ts}
        self._fails_global = 0        # 全局失败累计
        self._lock_until_global = 0.0 # 全局解锁时间戳

    # ── 配置 ──
    @property
    def require_auth(self):
        return self._require_auth

    def set_require_auth(self, value):
        self._require_auth = bool(value)

    # ── 查询 ──
    def authed(self, conn):
        """该连接是否已通过认证（关闭认证时一律视为已认证）。"""
        if not self._require_auth:
            return True
        with self._lock:
            return conn in self._authed

    def perm_of(self, conn):
        """该连接的权限：未认证→None；关闭认证→observe；已认证→实时白名单 perm。"""
        try:
            if not self._require_auth:
                return "observe"
            with self._lock:
                fp = self._authed.get(conn)
            if fp is None:
                return None
            rec = self._peer_store.get(fp)
            if rec and rec.get("perm") in VALID_PERMS:
                return rec["perm"]
            return "observe"
        except Exception:
            return None

    # ── 防暴力限流（IP 维度 + 全局；全部由 self._lock 保护）──
    @staticmethod
    def _peer_ip(conn):
        """安全取对端 IP；取不到返回 "?"（不抛）。"""
        try:
            pa = getattr(conn, "peer_addr", None)
            if pa and len(pa) >= 1 and pa[0]:
                return str(pa[0])
        except Exception:
            pass
        return "?"

    def _lock_state_now(self, ip=None, now=None):
        """(locked:bool, retry_after:int) —— 综合 IP 与全局锁定，无副作用。"""
        now = time.time() if now is None else now
        try:
            with self._lock:
                until = float(self._lock_until_global)
                if ip:
                    until = max(until, float(self._lock_until_by_ip.get(ip, 0.0)))
        except Exception:
            return False, 0
        if until > now:
            return True, int(until - now) + 1
        return False, 0

    def lock_state(self, ip=None):
        """当前锁定态快照：{"locked":bool,"retry_after":int}（供 UI / 测试）。"""
        try:
            locked, retry = self._lock_state_now(ip)
            return {"locked": bool(locked), "retry_after": int(retry)}
        except Exception:
            return {"locked": False, "retry_after": 0}

    def _note_fail(self, ip):
        """记一次失败：累加 IP 与全局计数；超阈值按指数退避置锁定（封顶）。"""
        try:
            now = time.time()
            with self._lock:
                if ip:
                    n = int(self._fails_by_ip.get(ip, 0)) + 1
                    self._fails_by_ip[ip] = n
                    if n >= MAX_AUTH_FAILS_PER_IP:
                        delay = min(
                            AUTH_LOCK_BASE * (2 ** (n - MAX_AUTH_FAILS_PER_IP)),
                            AUTH_LOCK_CAP)
                        self._lock_until_by_ip[ip] = now + delay
                self._fails_global += 1
                g = int(self._fails_global)
                if g >= MAX_AUTH_FAILS_GLOBAL:
                    delay = min(
                        AUTH_LOCK_BASE * (2 ** (g - MAX_AUTH_FAILS_GLOBAL)),
                        AUTH_LOCK_CAP)
                    self._lock_until_global = now + delay
        except Exception as e:
            _nl_log("auth fail counter error: {}".format(e), "WARN")

    def _note_success(self, ip):
        """成功配对后清零该 IP 的失败计数/锁定，并复位全局计数。

        复位全局计数是防误伤的必要补充：全局计数若只增不减，运行期累计
        达阈值后会把后续任意一次失败都判为「全局锁定」，形成永久性 DoS。
        """
        try:
            with self._lock:
                if ip:
                    self._fails_by_ip.pop(ip, None)
                    self._lock_until_by_ip.pop(ip, None)
                self._fails_global = 0
                self._lock_until_global = 0.0
        except Exception:
            pass

    def reset_rate_limit(self):
        """清空全部 IP/全局失败计数与锁定（重开配对窗口 / 管理解锁 / 测试复位）。"""
        try:
            with self._lock:
                self._fails_by_ip.clear()
                self._lock_until_by_ip.clear()
                self._fails_global = 0
                self._lock_until_global = 0.0
            return True
        except Exception:
            return False

    def _reject_if_locked(self, conn):
        """锁定期入口拦截：直接回 AUTH_FAIL，不下发 challenge、不消耗 nonce。"""
        ip = self._peer_ip(conn)
        locked, retry = self._lock_state_now(ip)
        if locked:
            self._fail(conn, "locked: too many attempts, retry_after={}s".format(retry))
            return True
        return False

    # ── 主入口 ──
    def on_auth(self, conn, msg):
        """处理 T_AUTH：无 proof → 发挑战；有 proof → 校验并回 OK/FAIL。"""
        try:
            data = msg.get("data") or {}
            if not self._require_auth:
                self._node.send(conn, _fail_msg("auth disabled"))
                return
            if data.get("proof"):
                self._verify(conn, data)
            else:
                self._challenge(conn, data)
        except Exception as e:
            _nl_log("auth on_auth error: {}".format(e), "ERROR")

    def _challenge(self, conn, data):
        if self._reject_if_locked(conn):
            return
        mode = str(data.get("mode") or "pair")
        node_id = str(data.get("node_id") or "")
        name = str(data.get("name") or "")
        fingerprint = str(data.get("fingerprint") or "")
        if mode == "resume":
            need = "token"
            salt = ""
        else:
            if not self._pair.active:
                self._node.send(conn, _fail_msg("pair window not open"))
                return
            need = "pin"
            salt = self._pair.salt
        nonce = self._nonce.issue(conn)
        with self._lock:
            self._pending[conn] = {"mode": mode, "node_id": node_id, "name": name,
                                   "fingerprint": fingerprint, "need": need,
                                   "salt": salt}
        self._node.send(conn, make_msg(T_AUTH_CHALLENGE, {
            "nonce": nonce, "need": need, "salt": salt, "ts": int(time.time())}))

    def _verify(self, conn, data):
        if self._reject_if_locked(conn):
            return
        nonce = str(data.get("nonce") or "")
        proof = str(data.get("proof") or "")
        ts = data.get("ts")
        with self._lock:
            pending = self._pending.get(conn) or {}
        mode = str(pending.get("mode") or "pair")
        node_id = str(pending.get("node_id") or "")
        name = str(pending.get("name") or "")
        fingerprint = str(pending.get("fingerprint") or "")
        salt = str(pending.get("salt") or "")

        # 1) 消费 nonce（防重放，先消费再判定，失败也消耗）
        nonce_ok = self._nonce.consume(conn, nonce)
        if not nonce_ok:
            self._fail(conn, "bad or reused nonce")
            return

        # 2) 确定用于校验的 token
        stored = None
        if mode == "resume" or (fingerprint and self._peer_store.is_trusted(fingerprint)):
            stored = load_peer_token(fingerprint)
        token = stored
        if token is None and mode != "resume":
            if self._pair.active and salt and self._pair.salt == salt:
                token = derive_token(self._pair.pin, self._pair.salt)
        if token is None:
            self._fail(conn, "no valid credential")
            return

        # 3) 校验证明
        if not verify_proof(token, nonce, node_id, ts, proof):
            self._fail(conn, "proof mismatch")
            return

        # 4) 通过：落白名单 + 存 token + 回 AUTH_OK
        rec = self._peer_store.add(node_id, name, fingerprint, "observe")
        perm = rec.get("perm") if rec.get("perm") in VALID_PERMS else "observe"
        secret_stored = store_peer_token(fingerprint, token)
        with self._lock:
            self._authed[conn] = fingerprint
            self._fails.pop(conn, None)
            self._pending.pop(conn, None)
        self._note_success(self._peer_ip(conn))
        info = self._node.info if self._node is not None else {}
        self._node.send(conn, make_msg(T_AUTH_OK, {
            "perm": perm, "server_node_id": info.get("node_id", ""),
            "server_name": info.get("name", ""),
            "secret_stored": bool(secret_stored)}))

    def _fail(self, conn, reason):
        """回 AUTH_FAIL；失败计数达上限则告警并断开连接。

        除保留既有 per-conn 计数外，叠加「IP 维度 + 全局」计数与锁定，
        并把该次失败写入审计（只记 reason / IP，绝不记 PIN / proof / salt）。
        """
        try:
            self._node.send(conn, _fail_msg(reason))
            ip = self._peer_ip(conn)
            with self._lock:
                n = self._fails.get(conn, 0) + 1
                self._fails[conn] = n
            self._note_fail(ip)
            locked, retry = self._lock_state_now(ip)
            _pair_audit("NETLINK_AUTH_FAIL", "err",
                        "reason={} locked={} retry_after={}".format(
                            str(reason or ""), bool(locked), int(retry)),
                        remote=ip)
            if n >= MAX_AUTH_FAILS:
                try:
                    self._node.bus.publish(_TOPIC_STATUS, {
                        "level": "warning",
                        "msg": "netlink: too many auth failures from {}".format(
                            getattr(conn, "peer_addr", ""))})
                except Exception:
                    pass
                try:
                    conn.close()
                except Exception:
                    pass
        except Exception as e:
            _nl_log("auth _fail error: {}".format(e), "WARN")

    def on_conn_closed(self, conn):
        with self._lock:
            self._authed.pop(conn, None)
            self._pending.pop(conn, None)
            self._fails.pop(conn, None)
        self._nonce.forget(conn)


def _fail_msg(reason):
    """构造 AUTH_FAIL 信封。"""
    return make_msg(T_AUTH_FAIL, {"reason": str(reason or "")})
