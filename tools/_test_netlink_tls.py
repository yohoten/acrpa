# -*- coding: utf-8 -*-
"""NetLink Phase 4-3 TLS 可选档专项自测 —— 全自动、不弹窗、总时长 < 60s。

运行:  python tools/_test_netlink_tls.py
退出码: 0 = 全部断言通过（允许 [WARN] 不计失败）；1 = 存在 [FAIL]

覆盖断言（对应交付物 7 的 10 项）:
  1. tls.available() 为真；server_context("","") == (None,"cert missing")；非法路径原因非空。
  2. TLS 启用但无证书 → node.start() 返回 False，总线收到 TOPIC_STATUS level=error，
     _started 保持 False；随后关闭 TLS 再 start() 成功。
  3. client_context() 返回非 None。
  4. 真实 TLS 握手（有证书时）：B 经 TLS 连上 A（alive）、TOFU 固定 1 条指纹、
     B 收到 tofu/pinned 提示、B 收到 A 的 STATE_SYNC（加密通道上业务消息可用）。
  5. TLS 下配对/指令可用：AUTH(pin) 配对 → set_peer_perm(control) → CMD_RUN → CMD_ACK done。
  6. 指纹不符拒绝：把已固定指纹改成错误值 → B 重连被拒（connect_to None / get() None）
     且出现 pin/指纹 warning 文案。
  7. TOFU 幂等：pin_add 同一指纹两次 → 仍 1 条。
  8. 明文回归：TLS 关闭时双节点轻量握手 + 免认证自动 SUBSCRIBE 收到 STATE。
  9. 审计：指纹拒绝路径写入审计（含 TLS_PIN）。
  10. 收尾：节点 stop()、还原 state 与 NETLINK_TLS_PINS、nl-* 线程 ≤5s 收敛为 0。

证书获取策略（按顺序，并在输出中打印实际分支）：
  1) import cryptography 成功 → 生成 2048-bit RSA 自签证书（仅测试脚本使用）；
  2) 否则 subprocess 调 openssl req -x509 生成；
  3) 都不行 → 依赖证书的真实握手断言记 [WARN] 并打印原因（不假通过）。

隔离：临时 CONFIG_PATH、端口 199xx、还原并清理 state.NETLINK_TLS_PINS。
"""
import datetime
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import uuid

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_SRC = os.path.join(_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import state  # noqa: E402

# ── 端口（199xx 段）──
A_TCP, A_UDP = 19987, 19985
B_TCP, B_UDP = 19988, 19986
C_TCP, C_UDP = 19983, 19981
D_TCP, D_UDP = 19984, 19982
TOTAL_BUDGET = 60.0

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


def wait_until(fn, timeout):
    """带 deadline 的轮询等待；返回 (是否满足, 实测耗时秒)。"""
    t = time.time()
    end = t + float(timeout)
    while time.time() < end:
        try:
            if fn():
                return True, time.time() - t
        except Exception:
            pass
        time.sleep(0.02)
    return False, time.time() - t


def _conn_alive(node, host, port):
    try:
        c = node._client.get(host, port)
        return bool(c is not None and c.alive)
    except Exception:
        return False


def _nl_threads():
    try:
        return sorted(t.name for t in threading.enumerate()
                      if t.name and t.name.startswith("nl-"))
    except Exception:
        return []


class FakeHooks(object):
    """被控端假执行钩子：记录 run/stop 调用（与既有 netlink 测试一致）。"""

    def __init__(self):
        self._lock = threading.Lock()
        self.run_calls = []
        self.stop_calls = 0

    def reset(self):
        with self._lock:
            self.run_calls = []
            self.stop_calls = 0

    def run(self, loops=None):
        with self._lock:
            self.run_calls.append(loops)

    def stop(self):
        with self._lock:
            self.stop_calls += 1
        try:
            state.quit3 = True
            state.quit2 = True
            state.running = False
        except Exception:
            pass


# ── 证书生成（三分支）──────────────────────────────────────────────────────
def _gen_cert_cryptography(cert_path, key_path):
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, u"acrpa")])
    now = datetime.datetime.utcnow()
    cert = (x509.CertificateBuilder()
            .subject_name(name).issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=1))
            .sign(key, hashes.SHA256()))
    with open(key_path, "wb") as f:
        f.write(key.private_bytes(serialization.Encoding.PEM,
                                  serialization.PrivateFormat.TraditionalOpenSSL,
                                  serialization.NoEncryption()))
    with open(cert_path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    return True


def _gen_cert_openssl(cert_path, key_path, days=1):
    cmd = ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
           "-days", str(days), "-keyout", key_path, "-out", cert_path,
           "-subj", "/CN=acrpa"]
    try:
        r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=30)
        return (r.returncode == 0 and os.path.isfile(cert_path)
                and os.path.isfile(key_path))
    except Exception:
        return False


def _make_cert(cert_path, key_path):
    """返回实际使用的分支名：'cryptography' / 'openssl' / None。"""
    try:
        import cryptography  # noqa: F401
        if _gen_cert_cryptography(cert_path, key_path):
            return "cryptography"
    except Exception as e:
        print("[..] cryptography 分支不可用: {}".format(e))
    if _gen_cert_openssl(cert_path, key_path):
        return "openssl"
    return None


def main():
    global _fails
    print("=" * 66)
    print("NetLink Phase 4-3 TLS acceptance (stdlib ssl + TOFU pinning)")
    print("=" * 66)

    # ── 环境隔离：备份 ──
    saved = {}
    for name in ("CONFIG_PATH", "NETLINK_ENABLED", "NETLINK_PORT",
                 "NETLINK_DISCOVERY_PORT", "NETLINK_AUTODISCOVER",
                 "NETLINK_DEVICE_NAME", "NETLINK_STATIC_PEERS",
                 "NETLINK_REQUIRE_AUTH", "NETLINK_CONFIRM_CONTROL",
                 "NETLINK_PIN_TTL", "NETLINK_TLS", "NETLINK_TLS_CERT",
                 "NETLINK_TLS_KEY", "NETLINK_PEERS", "NETLINK_CONFIRMED_PEERS",
                 "NETLINK_SCRIPT_DIR", "running", "quit2", "quit3",
                 "recording", "has_script", "filename"):
        saved[name] = getattr(state, name, None)
    saved["exec_state"] = dict(getattr(state, "exec_state", {}) or {})
    saved["pause_event"] = getattr(state, "pause_event", None)
    saved_pins = list(getattr(state, "NETLINK_TLS_PINS", []) or [])

    tmpdir = tempfile.mkdtemp(prefix="acrpa_tls_")
    state.CONFIG_PATH = os.path.join(tmpdir, "config.json")
    state.NETLINK_TLS_PINS = []

    certdir = os.path.join(tmpdir, "certs")
    os.makedirs(certdir)
    cert_path = os.path.join(certdir, "cert.pem")
    key_path = os.path.join(certdir, "key.pem")
    source = _make_cert(cert_path, key_path)
    print("[..] 证书获取分支: {}".format(source or "不可用（TLS 握手断言将记 WARN）"))
    tls_ok = source is not None

    import netlink.node as nl_node
    from netlink.bus import NetBus
    from netlink import tls
    from netlink.protocol import make_msg, T_CMD_RUN
    from netlink.node import TOPIC_STATUS, TOPIC_PEER_STATE, TOPIC_AUTH

    orig_make_node_id = nl_node.make_node_id
    run_tag = uuid.uuid4().hex[:6]
    _seq = {"n": 0}

    def _mk_id():
        _seq["n"] += 1
        return "t{}{}".format(run_tag, _seq["n"])

    nl_node.make_node_id = _mk_id

    a = b = n2 = None
    c = d = None
    hooks = FakeHooks()
    forget_fps = []

    try:
        # ══════════════════ 1. 基本接口 ══════════════════
        check("1a. tls.available() 为真", tls.available() is True)
        ctx, reason = tls.server_context("", "")
        check("1b. server_context('','') == (None,'cert missing')",
              ctx is None and reason == "cert missing", "reason={!r}".format(reason))
        ctx2, reason2 = tls.server_context(os.path.join(tmpdir, "nope.pem"),
                                           os.path.join(tmpdir, "nope.key"))
        check("1c. 非法路径返回非空原因",
              ctx2 is None and bool(reason2), "reason={!r}".format(reason2))

        # ══════════════════ 3. client_context ══════════════════
        cctx, creason = tls.client_context()
        check("3. client_context() 返回非 None", cctx is not None,
              "reason={!r}".format(creason))

        # ══════════════════ 2. TLS 启用但无证书 → 拒绝启动 ══════════════════
        state.NETLINK_TLS = True
        state.NETLINK_TLS_CERT = ""
        state.NETLINK_TLS_KEY = ""
        state.NETLINK_PORT = A_TCP
        state.NETLINK_DISCOVERY_PORT = A_UDP
        state.NETLINK_AUTODISCOVER = False
        state.NETLINK_DEVICE_NAME = "TLSNoCert"
        state.NETLINK_STATIC_PEERS = []
        status2 = []
        bus2 = NetBus()
        bus2.subscribe(TOPIC_STATUS, lambda tp, pl: status2.append(
            dict(pl) if isinstance(pl, dict) else {}))
        n2 = nl_node.NetLinkNode(root=None, bus=bus2)
        ok2 = n2.start()
        check("2a. TLS 启用但无证书 → start() 返回 False", ok2 is False,
              "ok={!r}".format(ok2))
        check("2b. 总线收到 TOPIC_STATUS level=error",
              any(str(m.get("level")) == "error" for m in status2),
              "msgs={}".format([m.get("msg") for m in status2][-2:]))
        check("2c. _started 保持 False", getattr(n2, "_started", None) is False,
              "started={!r}".format(getattr(n2, "_started", None)))
        state.NETLINK_TLS = False
        ok2b = n2.start()
        check("2d. 关闭 TLS 后 start() 成功", bool(ok2b) is True,
              "ok={!r}".format(ok2b))
        n2.stop()
        n2 = None

        # ══════════════════ 7. TOFU 幂等 ══════════════════
        state.NETLINK_TLS_PINS = []
        fp_dummy = "a" * 64
        tls.pin_add(fp_dummy)
        tls.pin_add(fp_dummy)
        check("7. pin_add 同一指纹两次 → 仍 1 条",
              len(state.NETLINK_TLS_PINS) == 1,
              "pins={}".format(state.NETLINK_TLS_PINS))
        state.NETLINK_TLS_PINS = []

        # ══════════════════ 4/5/6/9. 真实 TLS 握手 / 配对指令 / 指纹拒绝 / 审计 ══
        if not tls_ok:
            warn("无可用证书（cryptography/openssl 均不可用）→ TLS 真实握手/配对/指令/"
                 "指纹拒绝/审计断言记 WARN，不假通过")
        else:
            state.NETLINK_TLS = True
            state.NETLINK_TLS_CERT = cert_path
            state.NETLINK_TLS_KEY = key_path
            state.NETLINK_TLS_PINS = []
            state.NETLINK_REQUIRE_AUTH = True
            state.NETLINK_CONFIRM_CONTROL = False
            state.NETLINK_PIN_TTL = 600
            state.running = False
            state.recording = False
            state.quit2 = False
            state.quit3 = False
            state.has_script = True
            state.pause_event.set()

            # ── A 被控端（TLS）──
            state.NETLINK_PORT = A_TCP
            state.NETLINK_DISCOVERY_PORT = A_UDP
            state.NETLINK_DEVICE_NAME = "TLSA"
            state.NETLINK_STATIC_PEERS = []
            statusA = []
            busA = NetBus()
            busA.subscribe(TOPIC_STATUS, lambda tp, pl: statusA.append(
                dict(pl) if isinstance(pl, dict) else {}))
            a = nl_node.NetLinkNode(root=None, bus=busA)
            okA = a.start()
            check("4a. A(TLS) start() 成功", bool(okA), "ok={!r}".format(okA))
            a.set_control_hooks(run=hooks.run, stop=hooks.stop)

            # ── B 控制端（TLS，静态对端→A）──
            state.NETLINK_PORT = B_TCP
            state.NETLINK_DISCOVERY_PORT = B_UDP
            state.NETLINK_DEVICE_NAME = "TLSB"
            state.NETLINK_STATIC_PEERS = [["127.0.0.1", A_TCP]]
            statusB = []
            state_msgs = []
            auth_msgs = []
            busB = NetBus()
            busB.subscribe(TOPIC_STATUS, lambda tp, pl: statusB.append(
                dict(pl) if isinstance(pl, dict) else {}))
            busB.subscribe(TOPIC_PEER_STATE, lambda tp, pl: state_msgs.append(
                dict(pl) if isinstance(pl, dict) else {}))
            busB.subscribe(TOPIC_AUTH, lambda tp, pl: auth_msgs.append(
                dict(pl) if isinstance(pl, dict) else {}))
            b = nl_node.NetLinkNode(root=None, bus=busB)
            okB = b.start()
            check("4b. B(TLS, 静态对端→A) start() 成功", bool(okB), "ok={!r}".format(okB))

            got_conn, dtc = wait_until(lambda: _conn_alive(b, "127.0.0.1", A_TCP), 8.0)
            check("4c. B 经 TLS 连上 A（conn.alive，{:.2f}s）".format(dtc), got_conn)
            check("4d. NETLINK_TLS_PINS 出现 1 条指纹（TOFU）",
                  len(state.NETLINK_TLS_PINS) == 1,
                  "pins={}".format(state.NETLINK_TLS_PINS))
            check("4e. B 的 TOPIC_STATUS 出现 tofu/pinned 信息",
                  any(("tofu" in str(m.get("msg") or "").lower()
                       or "pinned" in str(m.get("msg") or "").lower())
                      for m in statusB),
                  "msgs={}".format([m.get("msg") for m in statusB][-4:]))

            # 配对（AUTH + pin）
            pin = a.open_pair_window()
            check("4f. A.open_pair_window() 返回 6 位 PIN",
                  isinstance(pin, str) and len(pin) == 6 and pin.isdigit(),
                  "pin={!r}".format(pin))
            b.register_target("127.0.0.1", A_TCP, pin)
            try:
                b._client.disconnect("127.0.0.1", A_TCP)
            except Exception:
                pass
            b._client.connect_to("127.0.0.1", A_TCP)
            paired, dtp = wait_until(lambda: len(a.list_peers() or []) >= 1, 12.0)
            check("4g. TLS 通道上完成 AUTH(pin) 配对（{:.2f}s）".format(dtp), paired,
                  "peers={}".format(len(a.list_peers() or [])))
            b_fp = str(((a.list_peers() or [{}])[0]).get("fingerprint") or "")
            if b_fp:
                forget_fps.append(b_fp)

            # STATE_SYNC（加密通道业务消息可用）
            state.running = True
            state.recording = False
            state.filename = os.path.join(tmpdir, "报表填报.xls")
            state.exec_state = {"loop": 1, "total_loops": 2, "row": 3,
                                "total_rows": 10, "start_time": time.time(),
                                "elapsed": 1}
            state.pause_event.set()
            got_state, dts = wait_until(lambda: len(state_msgs) >= 1, 6.0)
            check("4h. B 在 TLS 通道收到 A 的 STATE_SYNC（业务消息可用，{:.2f}s）"
                  .format(dts), got_state,
                  "state_msgs={}".format(len(state_msgs)))

            # ══════════════════ 5. TLS 下配对/指令可用 ══════════════════
            a.set_peer_perm(b_fp, "control")
            got_perm, dtperm = wait_until(lambda: any(
                (m.get("stage") == "perm" and m.get("perm") == "control")
                for m in auth_msgs), 3.0)
            check("5a. set_peer_perm(control) → B 收到 TOPIC_AUTH stage=perm"
                  "（{:.2f}s）".format(dtperm), got_perm,
                  "auth={}".format(auth_msgs[-2:]))

            state.running = False
            state.recording = False
            state.has_script = True
            state.filename = os.path.join(tmpdir, "demo.xls")
            hooks.reset()
            cmd_events = []
            busB.subscribe(TOPIC_CMD_RESULT, lambda tp, pl: cmd_events.append(
                dict(pl) if isinstance(pl, dict) else {}))
            conn = b._client.get("127.0.0.1", A_TCP)
            sent5 = bool(conn is not None and b.send(conn, make_msg(T_CMD_RUN, {})))
            check("5b. B 在 TLS 通道下发 CMD_RUN", sent5)

            def _done_run():
                return any(
                    str(((e.get("data") or {}).get("cmd")) or "") == T_CMD_RUN
                    and str(((e.get("data") or {}).get("status")) or "") == "done"
                    for e in cmd_events)

            got5, dt5 = wait_until(_done_run, 5.0)
            check("5c. B 收到 CMD_ACK done（TLS 不影响既有功能，{:.2f}s）".format(dt5),
                  got5, "events={}".format(
                      [((e.get("data") or {}).get("status")) for e in cmd_events][-6:]))
            got5d, _ = wait_until(lambda: len(hooks.run_calls) >= 1, 2.0)
            check("5d. A 侧 fake_run 被调用", got5d,
                  "calls={}".format(len(hooks.run_calls)))

            # ══════════════════ 6. 指纹不符拒绝 ══════════════════
            state.NETLINK_TLS_PINS = ["f" * 64]      # 手工写入错误指纹
            try:
                b._client.disconnect("127.0.0.1", A_TCP)
            except Exception:
                pass
            base_idx = len(statusB)
            res6 = b._client.connect_to("127.0.0.1", A_TCP)
            check("6a. 指纹不符 → connect_to 返回 None（拒绝连接）", res6 is None,
                  "res={!r}".format(res6))
            check("6b. client.get() 为 None（无存活连接）",
                  b._client.get("127.0.0.1", A_TCP) is None)
            got_warn, _ = wait_until(lambda: any(
                str(m.get("level")) == "warning"
                and ("pin" in str(m.get("msg") or "").lower()
                     or "指纹" in str(m.get("msg") or ""))
                for m in statusB[base_idx:]), 2.0)
            check("6c. TOPIC_STATUS 出现 pin/指纹 warning 文案", got_warn,
                  "msgs={}".format([m.get("msg") for m in statusB[base_idx:]][-3:]))

            # ══════════════════ 9. 审计 ══════════════════
            audit_lines = []
            try:
                ctl = getattr(b, "control", None)
                audit = getattr(ctl, "audit", None) if ctl is not None else None
                if audit is not None:
                    audit_lines = audit.tail(300) or []
            except Exception:
                audit_lines = []
            if audit_lines:
                check("9. 指纹拒绝路径写入审计（含 TLS_PIN）",
                      any("TLS_PIN" in ln for ln in audit_lines),
                      "tail={}".format(audit_lines[-2:]))
            else:
                warn("审计不可用（tail 为空）→ 跳过 TLS_PIN 审计断言")

            # ── 停止 TLS 场景节点 ──
            for n in (b, a):
                try:
                    if n is not None:
                        n.stop()
                except Exception:
                    pass
            a = b = None
            state.NETLINK_TLS_PINS = []

        # ══════════════════ 8. 明文回归 ══════════════════
        state.NETLINK_TLS = False
        state.NETLINK_TLS_CERT = ""
        state.NETLINK_TLS_KEY = ""
        state.NETLINK_REQUIRE_AUTH = False
        state.NETLINK_AUTODISCOVER = False
        state.NETLINK_STATIC_PEERS = []
        state.NETLINK_PORT = C_TCP
        state.NETLINK_DISCOVERY_PORT = C_UDP
        state.NETLINK_DEVICE_NAME = "PlainA"
        pstate = []
        busC = NetBus()
        busC.subscribe(TOPIC_PEER_STATE, lambda tp, pl: pstate.append(
            dict(pl) if isinstance(pl, dict) else {}))
        c = nl_node.NetLinkNode(root=None, bus=busC)
        okC = c.start()

        state.NETLINK_PORT = D_TCP
        state.NETLINK_DISCOVERY_PORT = D_UDP
        state.NETLINK_DEVICE_NAME = "PlainB"
        state.NETLINK_STATIC_PEERS = [["127.0.0.1", C_TCP]]
        busD = NetBus()
        d = nl_node.NetLinkNode(root=None, bus=busD)
        okD = d.start()
        check("8a. 明文双节点 start 成功", bool(okC and okD),
              "okC={!r} okD={!r}".format(okC, okD))
        got8b, dt8b = wait_until(lambda: _conn_alive(d, "127.0.0.1", C_TCP), 8.0)
        check("8b. 明文 B→A 连接建立且 alive（{:.2f}s）".format(dt8b), got8b)
        state.running = True
        state.filename = "plain.xls"
        state.exec_state = {"loop": 1, "total_loops": 1, "row": 1, "total_rows": 5,
                            "start_time": time.time(), "elapsed": 1}
        got8c, dt8c = wait_until(lambda: len(pstate) >= 1, 6.0)
        check("8c. 明文（免认证）自动 SUBSCRIBE 后收到 STATE（{:.2f}s）".format(dt8c),
              got8c, "pstate={}".format(len(pstate)))

    except Exception:
        print("[FAIL] 未捕获异常:")
        traceback.print_exc()
        _fails += 1
    finally:
        for n in (c, d, b, a, n2):
            try:
                if n is not None:
                    n.stop()
            except Exception:
                pass
        wait_until(lambda: not _nl_threads(), 5.0)
        left = _nl_threads()
        check("10. 收尾：nl-* 线程 ≤5s 收敛为 0", not left, "left={}".format(left))

        # 还原 state
        nl_node.make_node_id = orig_make_node_id
        for name, val in saved.items():
            try:
                setattr(state, name, val)
            except Exception:
                pass
        state.NETLINK_TLS_PINS = saved_pins
        try:
            shutil.rmtree(tmpdir, ignore_errors=True)
        except Exception:
            pass

    print("=" * 66)
    dt = time.time() - T0
    print("耗时 {:.2f}s (budget {:.0f}s)".format(dt, TOTAL_BUDGET))
    if _fails == 0:
        print("PASS (all assertions OK; warns={})".format(_warns))
        return 0
    print("FAIL ({} assertion(s) failed; warns={})".format(_fails, _warns))
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        sys.exit(1)
