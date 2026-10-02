# -*- coding: utf-8 -*-
"""NetLink Phase4-2 批次2 —— 网页面板「有限控制」HTTP 端到端自测（真实节点 + 强制 HTTPS）。

运行:  python -X utf8 tools/_test_netlink_webui_control.py
退出码: 0 = 全部断言通过（允许 [WARN]/[SKIP]）；1 = 存在 [FAIL]

链路（对应设计文档 §8.3）：
  真实 NetLinkNode + node.set_control_hooks(run=fake,stop=fake) + 门面 start_webui()
  （state 配置 control=True / web_tls=True / 自签证书）→ urllib + ssl 客户端走通：
    status → unlock(PIN) → control(run) → control(stop) → lock
  并断言：CSRF、白名单、幂等/串行、绝不降级明文、审计、无桌面弹窗。

证书：优先 cryptography → 否则外部 openssl（含 Git for Windows 自带）→ 否则整体 [SKIP]。
隔离：临时 CONFIG_PATH、端口 199xx、凭据库 CONTROL_PIN_TARGET 备份/还原。
"""
import datetime
import json
import os
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_SRC = os.path.join(_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass

import state                                              # noqa: E402
import netlink                                            # noqa: E402
from netlink import security                              # noqa: E402
from netlink.webui import WebUI                           # noqa: E402

PORT_WEB = 19993
PORT_TCP = 19991
PORT_UDP = 19990
PORT_NARROW = 19989
PIN = "123456"
CTL_TARGET = "ACRPA/netlink/web-control-pin"
TOTAL_BUDGET = 90.0

_fails = 0
_warns = 0
_skips = 0
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


def skip(msg):
    global _skips
    _skips += 1
    print("[SKIP] " + msg)


# ── HTTP(S) 客户端 ─────────────────────────────────────────────────────────
def _plain(path, token=None, method="GET", body=None, headers=None,
           timeout=6.0, port=None):
    port = PORT_WEB if port is None else port
    url = "http://127.0.0.1:{}{}".format(port, path)
    if token is not None:
        sep = "&" if "?" in path else "?"
        url = url + sep + "token=" + urllib.parse.quote(token)
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        r = urllib.request.urlopen(req, timeout=timeout)
        return r.getcode(), r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        try:
            b = e.read()
        except Exception:
            b = b""
        try:
            h = dict(e.headers)
        except Exception:
            h = {}
        return e.code, b, h
    except Exception as e:
        return 0, str(e).encode("utf-8"), {}


def _https(path, token=None, method="GET", body=None, headers=None,
           timeout=8.0, port=None):
    port = PORT_WEB if port is None else port
    url = "https://127.0.0.1:{}{}".format(port, path)
    if token is not None:
        sep = "&" if "?" in path else "?"
        url = url + sep + "token=" + urllib.parse.quote(token)
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    ctx = ssl._create_unverified_context()
    try:
        r = urllib.request.urlopen(req, timeout=timeout, context=ctx)
        return r.getcode(), r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        try:
            b = e.read()
        except Exception:
            b = b""
        try:
            h = dict(e.headers)
        except Exception:
            h = {}
        return e.code, b, h
    except Exception as e:
        return 0, str(e).encode("utf-8"), {}


def _json(body):
    try:
        return json.loads(body.decode("utf-8"))
    except Exception:
        return None


def _cookie_from_hdr(hdr, name):
    sc = str((hdr or {}).get("Set-Cookie") or "")
    for part in sc.split(";"):
        k, _, v = part.strip().partition("=")
        if k == name:
            return v.strip()
    return ""


def _has(lines, *needles):
    for ln in lines:
        try:
            if all(nd in ln for nd in needles):
                return True
        except Exception:
            continue
    return False


# ── 证书生成 ───────────────────────────────────────────────────────────────
def _find_openssl():
    cands = []
    try:
        w = shutil.which("openssl")
        if w:
            cands.append(w)
    except Exception:
        pass
    cands += [
        r"C:\Program Files\Git\usr\bin\openssl.exe",
        r"C:\Program Files\Git\mingw64\bin\openssl.exe",
        r"C:\Program Files\OpenSSL-Win64\bin\openssl.exe",
        r"C:\Program Files (x86)\OpenSSL-Win32\bin\openssl.exe",
    ]
    for c in cands:
        try:
            if c and os.path.isfile(c):
                return c
        except Exception:
            pass
    return ""


def _make_cert(cert_path, key_path):
    try:
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, u"acrpa")])
        now = datetime.datetime.utcnow()
        crt = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
               .public_key(k.public_key())
               .serial_number(x509.random_serial_number())
               .not_valid_before(now - datetime.timedelta(days=1))
               .not_valid_after(now + datetime.timedelta(days=1))
               .sign(k, hashes.SHA256()))
        with open(key_path, "wb") as f:
            f.write(k.private_bytes(serialization.Encoding.PEM,
                                    serialization.PrivateFormat.TraditionalOpenSSL,
                                    serialization.NoEncryption()))
        with open(cert_path, "wb") as f:
            f.write(crt.public_bytes(serialization.Encoding.PEM))
        return "cryptography"
    except Exception:
        pass
    exe = _find_openssl()
    if exe:
        try:
            r = subprocess.run(
                [exe, "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                 "-keyout", key_path, "-out", cert_path, "-days", "1",
                 "-subj", "/CN=acrpa"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if (r.returncode == 0 and os.path.isfile(cert_path)
                    and os.path.isfile(key_path)):
                return "openssl:" + exe
        except Exception:
            pass
    return ""


# ── 假执行钩子 ─────────────────────────────────────────────────────────────
class FakeHooks(object):
    def __init__(self):
        self._l = threading.Lock()
        self.run_calls = []
        self.stop_calls = 0
        self.order = []

    def reset(self):
        with self._l:
            self.run_calls = []
            self.stop_calls = 0
            self.order = []

    def run(self, loops=None):
        with self._l:
            self.run_calls.append(loops)
            self.order.append("run")
        try:
            state.running = True
        except Exception:
            pass

    def stop(self):
        with self._l:
            self.stop_calls += 1
            self.order.append("stop")
        try:
            state.quit3 = True
            state.quit2 = True
            state.running = False
            state.pause_event.set()
        except Exception:
            pass


def main():
    global _fails
    print("=" * 70)
    print("NetLink Phase4-2 b2 -- web panel limited control E2E (real node + HTTPS)")
    print("=" * 70)

    # ── 环境隔离：备份 ──
    saved = {k: getattr(state, k, None) for k in (
        "CONFIG_PATH", "NETLINK_ENABLED", "NETLINK_PORT", "NETLINK_DISCOVERY_PORT",
        "NETLINK_AUTODISCOVER", "NETLINK_DEVICE_NAME", "NETLINK_STATIC_PEERS",
        "NETLINK_REQUIRE_AUTH", "NETLINK_WEB_ENABLED", "NETLINK_WEB_PORT",
        "NETLINK_WEB_BIND", "NETLINK_WEB_CONTROL", "NETLINK_WEB_CONTROL_TTL",
        "NETLINK_WEB_TLS", "NETLINK_WEB_CONFIRM_CONTROL",
        "NETLINK_WEB_ALLOW_REMOTE_CONTROL", "NETLINK_TLS_CERT", "NETLINK_TLS_KEY",
        "NETLINK_CONFIRM_CONTROL", "running", "recording", "has_script",
        "filename", "quit2", "quit3")}
    saved_exec = dict(getattr(state, "exec_state", {}) or {})
    try:
        saved_pin = state.cred_read(CTL_TARGET)
    except Exception:
        saved_pin = None
    try:
        security.reset_control_pin_guard()
    except Exception:
        pass

    tmpdir = tempfile.mkdtemp(prefix="acrpa_webctl_e2e_")
    cert = os.path.join(tmpdir, "panel.crt")
    key = os.path.join(tmpdir, "panel.key")

    started = False
    try:
        how = _make_cert(cert, key)
        if not how:
            skip("证书不可生成（无 cryptography / openssl）→ 控制 E2E 全链路 SKIP")
            return finish(tmpdir, saved, saved_pin, saved_exec, False)
        print("   证书生成分支: {}".format(how))

        # ── 隔离 state ──
        state.CONFIG_PATH = os.path.join(tmpdir, "config.json")
        state.NETLINK_ENABLED = True
        state.NETLINK_PORT = PORT_TCP
        state.NETLINK_DISCOVERY_PORT = PORT_UDP
        state.NETLINK_AUTODISCOVER = False
        state.NETLINK_DEVICE_NAME = "WebCtlE2E"
        state.NETLINK_STATIC_PEERS = []
        state.NETLINK_REQUIRE_AUTH = True
        state.NETLINK_WEB_ENABLED = True
        state.NETLINK_WEB_PORT = PORT_WEB
        state.NETLINK_WEB_BIND = "127.0.0.1"
        state.NETLINK_WEB_CONTROL = True
        state.NETLINK_WEB_CONTROL_TTL = 300
        state.NETLINK_WEB_TLS = True
        state.NETLINK_WEB_CONFIRM_CONTROL = False
        state.NETLINK_WEB_ALLOW_REMOTE_CONTROL = False
        state.NETLINK_TLS_CERT = cert
        state.NETLINK_TLS_KEY = key
        state.NETLINK_CONFIRM_CONTROL = True
        state.running = False
        state.recording = False
        state.has_script = False
        try:
            state.exec_state = {"loop": 0, "total_loops": 0, "row": 0,
                                "total_rows": 0, "start_time": 0, "elapsed": 0}
        except Exception:
            pass
        try:
            state.cred_delete("ACRPA/netlink/web-token")
        except Exception:
            pass
        security.store_control_pin(PIN)
        security.reset_control_pin_guard()

        # ── 启动节点 + 假钩子 ──
        ok_nl = bool(netlink.start_netlink(root=None))
        started = ok_nl
        check("0. 真实节点 start_netlink (tcp {})".format(PORT_TCP), ok_nl)
        if not ok_nl:
            return finish(tmpdir, saved, saved_pin, saved_exec, started)
        node = netlink.get_node()
        hooks = FakeHooks()
        check("0b. set_control_hooks(run/stop) 注入成功",
              bool(node.set_control_hooks(run=hooks.run, stop=hooks.stop)))
        confirm_calls = []

        def fake_confirm(actor, cmd, timeout=30, remote=""):
            confirm_calls.append((actor, cmd))
            return True, False, ""

        node.control._confirm_action = fake_confirm

        # ── 门面启动控制面板（强制 HTTPS）──
        ok_web = bool(netlink.start_webui())
        check("1a. start_webui() = True（控制+强制 TLS）", ok_web)
        check("1b. webui_last_error() 为空", netlink.webui_last_error() == "",
              "err={!r}".format(netlink.webui_last_error()))
        url = netlink.webui_url() or ""
        check("1c. webui_url() 以 https:// 开头", url.startswith("https://"),
              "url={!r}".format(url[:40]))
        check("1d. is_webui_running() = True", netlink.is_webui_running() is True)
        tk = netlink.webui_token()

        # 明文请求 → 失败（绝不降级）
        pc, _, _ = _plain("/api/control/status", token=tk)
        check("2a. 对 HTTPS 端口发明文 HTTP → 失败（不降级）", pc == 0,
              "code={}".format(pc))
        # TLS 握手 + status
        c1, b1, _ = _https("/api/control/status", token=tk)
        j1 = _json(b1) or {}
        check("2b. HTTPS status → 200 enabled/tls/scheme 正确",
              c1 == 200 and j1.get("enabled") is True and j1.get("tls") is True
              and j1.get("scheme") == "https" and j1.get("unlocked") is False,
              "code={} body={}".format(c1, j1))

        # ── unlock ──
        c2, _, _ = _https("/api/control/unlock", token=tk, method="POST",
                          body={"pin": "000000"})
        check("3a. 错误 PIN → 401", c2 == 401, "code={}".format(c2))
        security.reset_control_pin_guard()
        c3, b3, h3 = _https("/api/control/unlock", token=tk, method="POST",
                            body={"pin": PIN})
        j3 = _json(b3) or {}
        sc = str((h3 or {}).get("Set-Cookie") or "")
        csrf = str(j3.get("csrf") or "")
        sid = _cookie_from_hdr(h3, "nl_ctl")
        check("3b. 正确 PIN → 200 + csrf + nl_ctl(Secure/Strict/HttpOnly)",
              c3 == 200 and bool(csrf) and bool(sid) and "Secure" in sc
              and "SameSite=Strict" in sc and "HttpOnly" in sc,
              "code={} sc={!r}".format(c3, sc[:110]))
        ck = {"Cookie": "nl_ctl=" + sid}

        # ── control: run（无脚本 → 409；有脚本 → done）──
        c4, b4, _ = _https("/api/control", token=tk, method="POST",
                           body={"action": "run", "csrf": csrf}, headers=ck)
        check("4a. run 无脚本 → 409 no script selected",
              c4 == 409 and (_json(b4) or {}).get("error") == "no script selected",
              "code={} body={}".format(c4, _json(b4)))
        state.has_script = True
        hooks.reset()
        c5, b5, _ = _https("/api/control", token=tk, method="POST",
                           body={"action": "run", "csrf": csrf}, headers=ck)
        j5 = _json(b5) or {}
        check("4b. run → 200 done（假 run 钩子调用 1 次，loops=None）",
              c5 == 200 and j5.get("status") == "done"
              and len(hooks.run_calls) == 1 and hooks.run_calls[0] is None,
              "code={} body={} calls={}".format(c5, j5, hooks.run_calls))
        check("4c. 无 CSRF → 403 csrf mismatch",
              _https("/api/control", token=tk, method="POST",
                     body={"action": "run"}, headers=ck)[0] == 403)
        check("4d. 白名单外 pause → 400 action not allowed",
              _https("/api/control", token=tk, method="POST",
                     body={"action": "pause", "csrf": csrf}, headers=ck)[0] == 400)

        # ── 串行化：run(已运行 409) → stop(done) → run(done) ──
        hooks.reset()
        c40, _, _ = _https("/api/control", token=tk, method="POST",
                           body={"action": "run", "csrf": csrf}, headers=ck)   # 409
        c6, b6, _ = _https("/api/control", token=tk, method="POST",
                           body={"action": "stop", "csrf": csrf}, headers=ck)
        c7, b7, _ = _https("/api/control", token=tk, method="POST",
                           body={"action": "run", "csrf": csrf}, headers=ck)
        check("5a. 串行：run(409)/stop(200)/run(200) 且钩子顺序 stop,run",
              c40 == 409 and c6 == 200 and c7 == 200
              and hooks.order == ["stop", "run"],
              "order={} codes={}/{}/{}".format(hooks.order, c40, c6, c7))

        # ── lock 后 → 403 control locked ──
        c8, _, _ = _https("/api/control/lock", token=tk, method="POST",
                          body={}, headers=ck)
        c9, b9, _ = _https("/api/control", token=tk, method="POST",
                           body={"action": "stop", "csrf": csrf}, headers=ck)
        check("6a. lock → 200；随后 control → 403 control locked",
              c8 == 200 and c9 == 403
              and (_json(b9) or {}).get("error") == "control locked",
              "lock={} after={}".format(c8, c9))

        # ── 无桌面弹窗 ──
        check("7a. 本地面板控制默认不弹桌面确认（_confirm_action 未被调用）",
              len(confirm_calls) == 0, "calls={}".format(confirm_calls))
        check("7b. 无 Tk root（headless 直执行，且不动 GUI）",
              getattr(node, "root", None) is None)

        # ── 审计（由内核产出，actor=web-panel|）──
        try:
            lines = list(node.control.audit.tail(500))
        except Exception:
            lines = []
        check("8a. 审计含 CMD_RUN 且 actor=web-panel|",
              _has(lines, "cmd=CMD_RUN", "actor=web-panel|"), "n={}".format(len(lines)))
        check("8b. 审计含 CMD_STOP",
              _has(lines, "cmd=CMD_STOP"), "n={}".format(len(lines)))
        check("8c. 审计含 WEB_UNLOCK/WEB_LOCK/WEB_PIN_FAIL",
              _has(lines, "cmd=WEB_UNLOCK") and _has(lines, "cmd=WEB_LOCK")
              and _has(lines, "cmd=WEB_PIN_FAIL"), "n={}".format(len(lines)))

        # ── 绑定收窄（控制 + bind 0.0.0.0 → 127.0.0.1）──
        narrow = None
        try:
            narrow = WebUI(node, port=PORT_NARROW, bind="0.0.0.0", control=True,
                           tls=True, cert=cert, key=key)
            okn, whyn = narrow.start()
            check("9. 控制 + bind 0.0.0.0 → 自动收窄 127.0.0.1",
                  okn is True and narrow._bind == "127.0.0.1",
                  "ok={} bind={!r}".format(okn, getattr(narrow, "_bind", None)))
        except Exception as e:
            check("9. 控制 + bind 0.0.0.0 → 自动收窄 127.0.0.1", False, repr(e))
        finally:
            if narrow is not None:
                try:
                    narrow.stop()
                except Exception:
                    pass

    except Exception:
        print("[FAIL] 未捕获异常:")
        traceback.print_exc()
        _fails += 1
    finally:
        return finish(tmpdir, saved, saved_pin, saved_exec, started)


def finish(tmpdir, saved, saved_pin, saved_exec, started):
    global _fails
    try:
        netlink.stop_webui()
    except Exception:
        pass
    try:
        netlink.stop_netlink()
    except Exception:
        pass
    try:
        for k, v in saved.items():
            setattr(state, k, v)
        state.exec_state = saved_exec
        if saved_pin:
            state.cred_write(CTL_TARGET, saved_pin)
        else:
            state.cred_delete(CTL_TARGET)
        security.reset_control_pin_guard()
    except Exception:
        pass
    try:
        shutil.rmtree(tmpdir, ignore_errors=True)
    except Exception:
        pass

    elapsed = time.time() - T0
    print("-" * 70)
    if elapsed >= TOTAL_BUDGET:
        _fails += 1
        print("[FAIL] 总耗时 {:.1f}s 超过 {}s 预算".format(elapsed, TOTAL_BUDGET))
    else:
        print("[OK]   总耗时 {:.1f}s (< {}s)".format(elapsed, TOTAL_BUDGET))
    print("warns={} skips={}".format(_warns, _skips))
    if _fails == 0:
        print("PASS (all assertions OK; warns={}, skips={})".format(_warns, _skips))
        return 0
    print("FAIL ({} assertion(s) failed; warns={}, skips={})".format(
        _fails, _warns, _skips))
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
