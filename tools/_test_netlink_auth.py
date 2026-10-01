# -*- coding: utf-8 -*-
"""NetLink Phase2-1a 安全内核专项自测 —— 全自动、不弹窗、总时长 < 45s。

运行:  python tools/_test_netlink_auth.py
退出码: 0 = 全部断言通过（允许 [WARN]）；1 = 存在 [FAIL]

要点:
  * 用原始 socket + protocol.frame_message / FrameReader 手写协议驱动被控端，
    便于精确构造 / 重放报文；
  * 不写项目 config.json：把 state.CONFIG_PATH 指向临时文件，测后还原；
  * 端口用 199xx 段（19960 / 19970），用完释放；
  * 覆盖 12 项断言：密码学单元 / 错误 PIN / 正确 PIN / 重放 / 重连免 PIN /
    白名单落地 / config 无密钥 / 权限门槛 / 失败锁定 / 关闭认证兼容 /
    配对码过期 / PeerStore 增删改。
"""
import json
import os
import re
import shutil
import socket
import sys
import tempfile
import time
import traceback

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_SRC = os.path.join(_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import tkinter  # noqa: F401  —— utils 顶层 import tkinter，提前加载
import state
import netlink.node as nl_node
from netlink.bus import NetBus
from netlink.node import NetLinkNode
from netlink import security
from netlink.protocol import (
    make_msg, frame_message, FrameReader, parse_msg,
    T_HELLO, T_AUTH, T_AUTH_OK, T_AUTH_FAIL, T_AUTH_CHALLENGE,
    T_SUBSCRIBE, T_CMD_ACK, T_CMD_ERR, T_STATE_SYNC, T_CMD_STOP,
)

# ── 端口（199xx 段）──
A_TCP, A_UDP = 19960, 19961
A2_TCP, A2_UDP = 19970, 19971

# ── 控制端（原始 socket）身份 ──
CLI_ID = "authcli000000001"
CLI_NAME = "AuthCli"
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

    def closed(self, timeout=2.0):
        """对端是否已主动关闭（recv 返回 b'' 或抛错）。"""
        deadline = time.time() + float(timeout)
        while time.time() < deadline:
            self.s.settimeout(max(0.05, deadline - time.time()))
            try:
                chunk = self.s.recv(65536)
            except socket.timeout:
                continue
            except Exception:
                return True
            if chunk == b"":
                return True
        return False

    def close(self):
        try:
            self.s.close()
        except Exception:
            pass


def connect(port):
    """建立原始连接并吞掉被控端主动下发的 HELLO。"""
    c = RawClient(port)
    c.wait(T_HELLO, 2.0)
    return c


def do_pair(c, pin, wrong=False):
    """走 1~3 步配对；返回 (resp_msg, step3_msg, token)。"""
    c.send(make_msg(T_AUTH, {"mode": "pair", "node_id": CLI_ID,
                             "fingerprint": CLI_FP, "name": CLI_NAME}))
    ch = c.wait(T_AUTH_CHALLENGE, 2.0)
    if ch is None:
        return None, None, None
    d = ch.get("data") or {}
    nonce = str(d.get("nonce") or "")
    salt = str(d.get("salt") or "")
    use_pin = (pin + "0") if wrong else pin
    token = security.derive_token(use_pin, salt)
    ts = int(time.time())
    proof = security.make_proof(token, nonce, CLI_ID, ts)
    step3 = make_msg(T_AUTH, {"nonce": nonce, "proof": proof, "ts": ts})
    c.send(step3)
    resp = c.wait_any((T_AUTH_OK, T_AUTH_FAIL), 2.0)
    return resp, step3, token


# ── 独立单元：密码学 / PairWindow / PeerStore ─────────────────────────────
def unit_crypto():
    now = int(time.time())
    salt = security.new_salt()
    tok1 = security.derive_token("123456", salt)
    tok2 = security.derive_token("123456", salt)
    check("1a. derive_token 同输入同输出(len==TOKEN_LEN)",
          tok1 == tok2 and len(tok1) == security.TOKEN_LEN,
          "len={}".format(len(tok1)))
    nonce = security.new_nonce()
    pr = security.make_proof(tok1, nonce, "NODE1", now)
    check("1b. verify_proof 正确通过",
          security.verify_proof(tok1, nonce, "NODE1", now, pr, now=now) is True)
    bad = ("0" if pr[0] != "0" else "1") + pr[1:]
    check("1c. 篡改 proof 1 字符 → False",
          security.verify_proof(tok1, nonce, "NODE1", now, bad, now=now) is False)
    pr_old = security.make_proof(tok1, nonce, "NODE1", now - 120)
    check("1d. ts 偏差 120s → False",
          security.verify_proof(tok1, nonce, "NODE1", now - 120, pr_old, now=now) is False)
    pr_30 = security.make_proof(tok1, nonce, "NODE1", now - 30)
    check("1e. ts 偏差 30s → True",
          security.verify_proof(tok1, nonce, "NODE1", now - 30, pr_30, now=now) is True)


def unit_pair_window():
    pw = security.PairWindow(ttl=1)
    p = pw.open()
    check("11a. PairWindow.open → 6 位数字 PIN",
          isinstance(p, str) and len(p) == 6 and p.isdigit(), "pin={!r}".format(p))
    check("11b. PairWindow.active True", pw.active is True)
    time.sleep(1.2)
    check("11c. TTL 到期 → active False", pw.active is False)
    check("11d. TTL 到期 → expires_in <= 0", pw.expires_in() <= 0,
          "in={}".format(pw.expires_in()))


def unit_peer_store():
    saved = list(getattr(state, "NETLINK_PEERS", []) or [])
    ps = security.PeerStore(persist=False)
    r = ps.add("n1", "N1", "fp1", "observe")
    check("12a. add 返回完整字段",
          isinstance(r, dict) and all(
              k in r for k in ("node_id", "name", "fingerprint",
                               "perm", "added_at", "last_seen")),
          "r={}".format(r))
    check("12b. get_by_node 命中",
          (ps.get_by_node("n1") or {}).get("fingerprint") == "fp1")
    check("12c. is_trusted True", ps.is_trusted("fp1") is True)
    check("12d. set_perm control 生效",
          ps.set_perm("fp1", "control") is True
          and (ps.get("fp1") or {}).get("perm") == "control")
    check("12e. set_perm 非法权限 → False", ps.set_perm("fp1", "bogus") is False)
    check("12f. remove 返回 True", ps.remove("fp1") is True)
    check("12g. remove 后 is_trusted False", ps.is_trusted("fp1") is False)
    check("12h. persist=False 不写 state.NETLINK_PEERS",
          list(getattr(state, "NETLINK_PEERS", []) or []) == saved)


# ── config.json 扫描 ──────────────────────────────────────────────────────
def scan_config_text():
    path = state.CONFIG_PATH
    if not os.path.exists(path):
        return True, "no config file written"
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except Exception as e:
        return False, "read failed: {}".format(e)
    low = text.lower()
    for kw in ("proof", "token", "pin", "secret"):
        if ('"%s"' % kw) in low:
            return False, "found sensitive key/word: {}".format(kw)
    runs = re.findall(r"[0-9a-fA-F]{32,}", text)
    allowed = set()
    try:
        data = json.loads(text)
    except Exception:
        data = None

    def collect(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k == "fingerprint" and isinstance(v, str):
                    allowed.add(v)
                else:
                    collect(v)
        elif isinstance(o, list):
            for x in o:
                collect(x)

    collect(data)
    for r in runs:
        if r not in allowed:
            return False, "unexpected long hex run: {}".format(r[:16])
    return True, "clean"


# ══════════════════════════════════════════════════════════════════════════
def main():
    global _fails
    print("=" * 64)
    print("NetLink Phase2-1a security kernel self-test")
    print("=" * 64)

    # ── 环境隔离：不写项目 config.json；备份并还原凭据库/内存状态 ──
    orig_config_path = state.CONFIG_PATH
    orig_peers = list(getattr(state, "NETLINK_PEERS", []) or [])
    orig_api_key = getattr(state, "API_KEY", "")
    orig_cred = state.cred_read(state._CRED_TARGET)
    tmpdir = tempfile.mkdtemp(prefix="acrpa_auth_test_")
    state.CONFIG_PATH = os.path.join(tmpdir, "config.json")
    state.NETLINK_PEERS = []
    state.NETLINK_REQUIRE_AUTH = True
    state.NETLINK_PIN_TTL = 600

    A = None
    A2 = None
    try:
        # ── 独立单元（不依赖节点）──
        unit_crypto()          # 断言 1
        unit_pair_window()     # 断言 11
        unit_peer_store()      # 断言 12

        # ── 起始被控端 A（require_auth=True）──
        state.NETLINK_ENABLED = True
        state.NETLINK_PORT = A_TCP
        state.NETLINK_DISCOVERY_PORT = A_UDP
        state.NETLINK_AUTODISCOVER = False
        state.NETLINK_DEVICE_NAME = "AuthA"
        state.NETLINK_STATIC_PEERS = []
        nl_node.make_node_id = lambda: "authnodeA0000001"
        A = NetLinkNode(root=None, bus=NetBus())
        okA = A.start()
        check("0. 被控端 A start (tcp {})".format(A_TCP), bool(okA))
        if not okA:
            print("[FAIL] A 启动失败，认证链路断言无法继续")
            return finish(orig_config_path, orig_peers, orig_api_key,
                          orig_cred, tmpdir, A, A2)

        pin = A.open_pair_window()
        check("0b. 打开配对窗口拿到 PIN",
              isinstance(pin, str) and len(pin) == 6, "pin={!r}".format(pin))

        # ── 断言 2：错误 PIN 配对 ──
        c2 = connect(A_TCP)
        resp2, _, _ = do_pair(c2, pin, wrong=True)
        check("2a. 错误 PIN → T_AUTH_FAIL",
              resp2 is not None and resp2.get("t") == T_AUTH_FAIL,
              "resp={}".format((resp2 or {}).get("t")))
        c2.send(make_msg(T_SUBSCRIBE, {"topics": ["state"]}))
        rej = c2.wait_any((T_AUTH_FAIL, T_STATE_SYNC), 2.0)
        check("2b. 未认证 SUBSCRIBE → T_AUTH_FAIL（而非 STATE_SYNC）",
              rej is not None and rej.get("t") == T_AUTH_FAIL,
              "got={}".format((rej or {}).get("t")))
        c2.close()

        # ── 断言 3：正确 PIN 配对 + 订阅推送 ──
        c3 = connect(A_TCP)
        resp3, step3, token3 = do_pair(c3, pin)
        d3 = (resp3 or {}).get("data") or {}
        check("3a. 正确 PIN → T_AUTH_OK",
              resp3 is not None and resp3.get("t") == T_AUTH_OK,
              "resp={}".format((resp3 or {}).get("t")))
        check("3b. AUTH_OK.perm == observe", d3.get("perm") == "observe",
              "perm={!r}".format(d3.get("perm")))
        check("3c. AUTH_OK 带 secret_stored 字段", "secret_stored" in d3,
              "keys={}".format(list(d3.keys())))
        c3.send(make_msg(T_SUBSCRIBE, {"topics": ["state"]}))
        # 注意：被控端先推 STATE_SYNC 再回 CMD_ACK，故一次性收集两条，避免丢弃
        got_ack = False
        got_sync = False
        dl3 = time.time() + 3.0
        while time.time() < dl3 and not (got_ack and got_sync):
            m = c3.recv(dl3 - time.time())
            if m is None:
                continue
            if m.get("t") == T_CMD_ACK:
                got_ack = True
            elif m.get("t") == T_STATE_SYNC:
                got_sync = True
        check("3d. 订阅 T_SUBSCRIBE → T_CMD_ACK", got_ack)
        check("3e. 订阅后 <=3s 收到 T_STATE_SYNC", got_sync)

        # ── 断言 4：重放拒绝 ──
        if step3 is not None:
            c3.send(step3)
            rp = c3.wait(T_AUTH_FAIL, 2.0)
            check("4a. 同连接重放同一条 AUTH → T_AUTH_FAIL",
                  rp is not None and rp.get("t") == T_AUTH_FAIL,
                  "got={}".format((rp or {}).get("t")))
        # 新连接重放同一条 step3
        c4 = connect(A_TCP)
        if step3 is not None:
            c4.send(step3)
            rp4 = c4.wait(T_AUTH_FAIL, 2.0)
            check("4b. 新连接重放 → T_AUTH_FAIL",
                  rp4 is not None and rp4.get("t") == T_AUTH_FAIL,
                  "got={}".format((rp4 or {}).get("t")))
        c4.close()
        c3.close()

        # ── 断言 6：白名单落地 ──
        peers = A.list_peers()
        rec6 = None
        for p in peers:
            if p.get("fingerprint") == CLI_FP:
                rec6 = p
                break
        check("6a. A.list_peers() 含该 peer", rec6 is not None,
              "peers={}".format(peers))
        if rec6 is not None:
            check("6b. peer 字段完整(node_id/name/fingerprint/perm/added_at/last_seen)",
                  all(k in rec6 for k in ("node_id", "name", "fingerprint",
                                          "perm", "added_at", "last_seen")),
                  "rec={}".format(rec6))
            check("6c. peer.perm == observe", rec6.get("perm") == "observe",
                  "perm={!r}".format(rec6.get("perm")))
        else:
            check("6b. peer 字段完整(node_id/name/fingerprint/perm/added_at/last_seen)", False, "no rec")
            check("6c. peer.perm == observe", False, "no rec")

        # ── 断言 7：config.json 无密钥 ──
        ok7, detail7 = scan_config_text()
        check("7. config 无 proof/token/pin/secret 及超长 hex", ok7, detail7)

        # ── 断言 5：重连免 PIN（resume）──
        c5 = connect(A_TCP)
        c5.send(make_msg(T_HELLO, {"node_id": CLI_ID, "name": CLI_NAME,
                                   "version": "test", "caps": {}, "fingerprint": CLI_FP}))
        tok5 = security.load_peer_token(CLI_FP)
        check("5a. 凭据库已存该 peer token", tok5 is not None)
        if tok5 is None:
            tok5 = token3
        c5.send(make_msg(T_AUTH, {"mode": "resume", "node_id": CLI_ID,
                                  "fingerprint": CLI_FP, "name": CLI_NAME}))
        ch5 = c5.wait(T_AUTH_CHALLENGE, 2.0)
        d5 = (ch5 or {}).get("data") or {}
        nonce5 = str(d5.get("nonce") or "")
        ts5 = int(time.time())
        proof5 = security.make_proof(tok5, nonce5, CLI_ID, ts5)
        c5.send(make_msg(T_AUTH, {"nonce": nonce5, "proof": proof5, "ts": ts5}))
        r5 = c5.wait_any((T_AUTH_OK, T_AUTH_FAIL), 2.0)
        check("5b. resume → T_AUTH_OK（不需要 PIN）",
              r5 is not None and r5.get("t") == T_AUTH_OK,
              "got={}".format((r5 or {}).get("t")))

        # ── 断言 8：权限门槛 ──
        state.quit2 = False
        A.set_peer_perm(CLI_FP, "observe")
        c5.send(make_msg(T_CMD_STOP))
        e1 = c5.wait(T_CMD_ERR, 2.0)
        r1 = str(((e1 or {}).get("data") or {}).get("reason") or "")
        check("8a. observe + CMD_STOP → permission denied",
              e1 is not None and "permission denied" in r1, "reason={!r}".format(r1))
        check("8b. state.quit2 未被置 True", getattr(state, "quit2", False) is False)
        # Phase2-2: control 权限下 CMD_STOP 已由操控内核受理（不再 not implemented）。
        # 关闭首次操控确认门控（无 GUI 时弹窗不可用），断言被受理执行。
        state.NETLINK_CONFIRM_CONTROL = False
        A.set_peer_perm(CLI_FP, "control")
        c5.send(make_msg(T_CMD_STOP))
        a2 = c5.wait(T_CMD_ACK, 2.0)
        d2 = (a2 or {}).get("data") or {}
        check("8c. control + CMD_STOP → 已受理执行 (Phase2-2)",
              a2 is not None and d2.get("cmd") == "CMD_STOP",
              "ack={}".format(a2))
        c5.close()

        # ── 断言 9：失败锁定（5 次错误 PIN → 连接被关闭）──
        c9 = connect(A_TCP)
        saw_disconnect = False
        for _ in range(5):
            c9.send(make_msg(T_AUTH, {"mode": "pair", "node_id": CLI_ID,
                                      "fingerprint": CLI_FP, "name": CLI_NAME}))
            ch = c9.wait(T_AUTH_CHALLENGE, 2.0)
            dd = (ch or {}).get("data") or {}
            nn = str(dd.get("nonce") or "")
            ss = str(dd.get("salt") or "")
            tk = security.derive_token(pin + "0", ss)
            tt = int(time.time())
            pf = security.make_proof(tk, nn, CLI_ID, tt)
            c9.send(make_msg(T_AUTH, {"nonce": nn, "proof": pf, "ts": tt}))
            rr = c9.wait_any((T_AUTH_FAIL, T_AUTH_OK), 2.0)
            if rr is None or rr.get("t") != T_AUTH_FAIL:
                break
        saw_disconnect = c9.closed(2.0)
        check("9. 连续 5 次错误 PIN → 连接被 A 主动关闭", saw_disconnect)
        c9.close()

        # ── 断言 10：require_auth=False 兼容 ──
        state.NETLINK_REQUIRE_AUTH = False
        state.NETLINK_PORT = A2_TCP
        state.NETLINK_DISCOVERY_PORT = A2_UDP
        state.NETLINK_AUTODISCOVER = False
        state.NETLINK_DEVICE_NAME = "AuthA2"
        state.NETLINK_STATIC_PEERS = []
        nl_node.make_node_id = lambda: "authnodeA0000002"
        A2 = NetLinkNode(root=None, bus=NetBus())
        okA2 = A2.start()
        check("10a. 被控端 A2 start(require_auth=False)", bool(okA2))
        if okA2:
            c10 = connect(A2_TCP)
            c10.send(make_msg(T_SUBSCRIBE, {"topics": ["state"]}))
            sy10 = c10.wait(T_STATE_SYNC, 3.0)
            check("10b. 关闭认证后免配对直接收到 STATE_SYNC", sy10 is not None)
            c10.close()

    except Exception:
        print("[FAIL] 未捕获异常:")
        traceback.print_exc()
        _fails += 1
    finally:
        return finish(orig_config_path, orig_peers, orig_api_key,
                      orig_cred, tmpdir, A, A2)


def finish(orig_config_path, orig_peers, orig_api_key, orig_cred, tmpdir, A, A2):
    """收尾：停节点 + 还原 state/凭据库 + 清理临时文件；返回退出码。"""
    global _fails
    for n in (A2, A):
        if n is not None:
            try:
                n.stop()
            except Exception:
                pass
    # 还原内存与凭据库
    try:
        state.CONFIG_PATH = orig_config_path
        state.NETLINK_PEERS = orig_peers
        state.API_KEY = orig_api_key
        if orig_cred:
            state.cred_write(state._CRED_TARGET, orig_cred)
        else:
            state.cred_delete(state._CRED_TARGET)
    except Exception:
        pass
    try:
        security.forget_peer_token(CLI_FP)
    except Exception:
        pass
    try:
        shutil.rmtree(tmpdir, ignore_errors=True)
    except Exception:
        pass

    elapsed = time.time() - T0
    print("-" * 64)
    print("elapsed {:.2f}s, warns={}".format(elapsed, _warns))
    if _fails == 0:
        print("PASS (all assertions OK)")
        return 0
    print("FAIL ({} assertion(s) failed)".format(_fails))
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
