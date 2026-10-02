# -*- coding: utf-8 -*-
"""NetLink Phase 4-2 浏览器只读监控面板专项自测 —— 全自动、不弹窗、总时长 < 60s。

运行:  python tools/_test_netlink_webui.py
退出码: 0 = 全部断言通过（允许 [WARN]）；1 = 存在 [FAIL]

要点:
  * 客户端用 stdlib urllib.request；
  * state.CONFIG_PATH 指向临时文件，测后还原；端口用 199xx 段并释放；
  * 令牌测试隔离：测前保存并清空凭据库 ACRPA/netlink/web-token，测后还原/清理；
  * 数据联动：直接向总线 publish 一条 TOPIC_PEER_STATE，验证订阅快照生效；
  * 只读：POST → 405，未知路径 → 404，页面正文无写操作入口。

断言（13 项）:
  1  默认关闭        2  启用后运行态 + url 含 token     3  鉴权（无/错/对）
  4  Cookie 路径      5  Bearer 路径                     6  7 个只读 API
  7  只读边界        8  令牌不落 config.json            9  数据联动（订阅快照）
  10 rotate_token    11 停止与重绑 + start/stop 幂等     12 线程收敛
  13 stop_netlink 连带停 webui
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
    import tkinter  # noqa: F401 —— utils 顶层 import tkinter；headless 忽略
except Exception:
    tkinter = None

import state
import netlink
from netlink import security
from netlink.node import TOPIC_PEER_STATE, TOPIC_PEER_LOG

PORT_WEB = 19997
PORT_TCP = 19981
PORT_UDP = 19982
PORT_CTL = 19996          # 控制启用（HTTPS）专用面板端口
PORT_DEF = 19995          # 防御层/拒绝启动专用端口
TOKEN_TARGET = "ACRPA/netlink/web-token"
CTL_TARGET = "ACRPA/netlink/web-control-pin"
PIN = "123456"
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


def wait_until(fn, timeout):
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


# ── HTTP 客户端（stdlib only）────────────────────────────────────────────
def _http(path, token=None, cookie=None, bearer=None, method="GET",
          timeout=5.0, body=None, headers=None, port=None):
    port = PORT_WEB if port is None else port
    url = "http://127.0.0.1:{}{}".format(port, path)
    if token is not None:
        sep = "&" if "?" in path else "?"
        url = url + sep + "token=" + urllib.parse.quote(token)
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method)
    if cookie is not None:
        req.add_header("Cookie", "nl_token=" + cookie)
    if bearer is not None:
        req.add_header("Authorization", "Bearer " + bearer)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        r = urllib.request.urlopen(req, timeout=timeout)
        return r.getcode(), r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        try:
            body = e.read()
        except Exception:
            body = b""
        try:
            hdr = dict(e.headers)
        except Exception:
            hdr = {}
        return e.code, body, hdr
    except Exception as e:
        return 0, str(e).encode("utf-8"), {}


def _https(path, token=None, method="GET", body=None, headers=None,
           timeout=8.0, port=None):
    """HTTPS 客户端（自签证书 → 不校验）；返回 (code, body, headers)。"""
    port = PORT_CTL if port is None else port
    url = "https://127.0.0.1:{}{}".format(port, path)
    if token is not None:
        sep = "&" if "?" in path else "?"
        url = url + sep + "token=" + urllib.parse.quote(token)
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
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


def _can_connect(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.4)
    try:
        s.connect(("127.0.0.1", port))
        return True
    except Exception:
        return False
    finally:
        try:
            s.close()
        except Exception:
            pass


def _can_bind(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", port))
        return True
    except Exception:
        return False
    finally:
        try:
            s.close()
        except Exception:
            pass


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


def skip(name, why):
    global _skips
    _skips += 1
    print("[SKIP] " + name + " :: " + str(why))


class _Hooks(object):
    """假执行钩子：记录 run/stop 调用顺序（run 置 state.running=True 以模拟真实）。"""

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


def _find_openssl():
    """在 PATH 与常见安装位置（含 Git for Windows）查找 openssl 可执行文件。"""
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
    """自签证书三分支生成（cryptography → openssl → 空）。返回分支名或 ""。"""
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


def main():
    global _fails
    print("=" * 66)
    print("NetLink Phase 4-2 web panel self-test")
    print("=" * 66)

    # ── 环境隔离：备份 ──
    saved = {}
    for name in ("CONFIG_PATH", "NETLINK_ENABLED", "NETLINK_PORT",
                 "NETLINK_DISCOVERY_PORT", "NETLINK_AUTODISCOVER",
                 "NETLINK_DEVICE_NAME", "NETLINK_STATIC_PEERS",
                 "NETLINK_WEB_ENABLED", "NETLINK_WEB_PORT", "NETLINK_WEB_BIND",
                 "running", "recording", "filename", "has_script", "quit2", "quit3",
                 "NETLINK_REQUIRE_AUTH", "NETLINK_TLS_CERT", "NETLINK_TLS_KEY",
                 "NETLINK_WEB_CONTROL", "NETLINK_WEB_CONTROL_TTL",
                 "NETLINK_WEB_TLS", "NETLINK_WEB_CONFIRM_CONTROL",
                 "NETLINK_WEB_ALLOW_REMOTE_CONTROL",
                 "NETLINK_CONFIRM_CONTROL"):
        saved[name] = getattr(state, name, None)
    saved_exec = dict(getattr(state, "exec_state", {}) or {})
    try:
        saved_token = state.cred_read(TOKEN_TARGET)
    except Exception:
        saved_token = None
    try:
        saved_pin = state.cred_read(CTL_TARGET)
    except Exception:
        saved_pin = None
    try:
        security.reset_control_pin_guard()
    except Exception:
        pass

    tmpdir = tempfile.mkdtemp(prefix="acrpa_webtest_")
    state.CONFIG_PATH = os.path.join(tmpdir, "config.json")
    state.NETLINK_ENABLED = True
    state.NETLINK_PORT = PORT_TCP
    state.NETLINK_DISCOVERY_PORT = PORT_UDP
    state.NETLINK_AUTODISCOVER = False
    state.NETLINK_DEVICE_NAME = "WebTest"
    state.NETLINK_STATIC_PEERS = []
    state.NETLINK_REQUIRE_AUTH = True
    state.NETLINK_WEB_PORT = PORT_WEB
    state.NETLINK_WEB_BIND = "127.0.0.1"
    state.running = False
    try:
        state.exec_state = {"loop": 0, "total_loops": 0, "row": 0,
                            "total_rows": 0, "start_time": 0, "elapsed": 0}
    except Exception:
        pass

    # 令牌隔离：清空凭据库中的面板令牌
    try:
        state.cred_delete(TOKEN_TARGET)
    except Exception:
        pass

    started_netlink = False
    token = ""
    try:
        # ══════════════════ 1. 默认关闭 ══════════════════
        state.NETLINK_WEB_ENABLED = False
        r1 = False
        try:
            r1 = bool(netlink.start_webui())
        except Exception as e:
            r1 = "exc:{}".format(e)
        check("1a. 默认关闭: start_webui() 返回 False", r1 is False,
              "ret={!r}".format(r1))
        check("1b. 默认关闭: is_webui_running() 为 False",
              netlink.is_webui_running() is False)
        check("1c. 默认关闭: 127.0.0.1:{} 不可连".format(PORT_WEB),
              not _can_connect(PORT_WEB))

        # ══════════════════ 启动 netlink + 启用面板 ══════════════════
        ok_nl = False
        try:
            ok_nl = bool(netlink.start_netlink(root=None))
        except Exception as e:
            ok_nl = False
            print("   start_netlink exc: {!r}".format(e))
        started_netlink = ok_nl
        check("0. 被控/本机节点 start_netlink (tcp {})".format(PORT_TCP), ok_nl)

        if not ok_nl:
            print("[FAIL] 节点未就绪，跳过后续")
            return finish(tmpdir, saved, saved_token, saved_exec,
                          started_netlink, False, saved_pin)

        state.NETLINK_WEB_ENABLED = True
        try:
            ok_web = bool(netlink.start_webui())
        except Exception as e:
            ok_web = False
            print("   start_webui exc: {!r}".format(e))

        # ══════════════════ 2. 启用后运行态 + url ══════════════════
        check("2a. 启用后 start_webui() 返回 True", ok_web)
        check("2b. 启用后 is_webui_running() 为 True",
              netlink.is_webui_running() is True)
        url = ""
        try:
            url = netlink.webui_url() or ""
        except Exception:
            url = ""
        check("2c. webui_url() 含 token=", "token=" in url, "url={!r}".format(url))

        try:
            token = netlink.webui_token()
        except Exception:
            token = ""
        check("2d. webui_token() 返回非空令牌", bool(token) and len(token) >= 8,
              "len={}".format(len(token)))

        # ══════════════════ 3. 鉴权（无/错/对）══════════════════
        c3a, b3a, _ = _http("/")
        check("3a. 无令牌 GET / → 401", c3a == 401,
              "code={} body={!r}".format(c3a, b3a[:60]))
        c3b, _, _ = _http("/", token="0123456789abcdef0123456789abcdef")
        check("3b. 错误令牌 GET / → 401", c3b == 401, "code={}".format(c3b))
        c3c, b3c, h3c = _http("/", token=token)
        ctype = str(h3c.get("Content-Type") or "")
        check("3c. 正确 token → 200 且 Content-Type 含 text/html",
              c3c == 200 and "text/html" in ctype,
              "code={} ctype={!r}".format(c3c, ctype))
        check("3d. 页面正文含 ACRPA", b"ACRPA" in b3c)
        set_cookie = str(h3c.get("Set-Cookie") or "")
        check("3e. ?token 有效 → 响应 Set-Cookie 含 nl_token=",
              "nl_token=" in set_cookie,
              "set_cookie={!r}".format(set_cookie[:80]))

        # ══════════════════ 4. Cookie 路径 ══════════════════
        c4, _, _ = _http("/api/summary", cookie=token)
        check("4. 仅带 Cookie(无 query) GET /api/summary → 200", c4 == 200,
              "code={}".format(c4))

        # ══════════════════ 5. Bearer 路径 ══════════════════
        c5, b5, _ = _http("/api/peers", bearer=token)
        check("5. Bearer GET /api/peers → 200", c5 == 200, "code={}".format(c5))

        # ══════════════════ 6. 7 个只读 API ══════════════════
        apis = ["/api/summary", "/api/peers", "/api/state", "/api/logs",
                "/api/sched", "/api/status"]
        ok6 = True
        detail6 = []
        for p in apis:
            cc, bb, hh = _http(p, token=token)
            jj = _json(bb)
            ctype = str(hh.get("Content-Type") or "")
            good = (cc == 200 and isinstance(jj, dict)
                    and "application/json" in ctype)
            if not good:
                ok6 = False
                detail6.append("{}:code={},json={},ctype={!r}".format(
                    p, cc, isinstance(jj, dict), ctype))
        check("6a. summary/peers/state/logs/sched/status 均 200 且合法 JSON",
              ok6, "; ".join(detail6))

        node = None
        try:
            node = netlink.get_node()
        except Exception:
            node = None
        lp = []
        try:
            lp = node.list_peers() or [] if node is not None else []
        except Exception:
            lp = []
        j6 = _json(_http("/api/peers", token=token)[1]) or {}
        peers_arr = j6.get("peers")
        check("6b. /api/peers.peers 为数组且条数==node.list_peers()",
              isinstance(peers_arr, list) and len(peers_arr) == len(lp),
              "peers={} list_peers={}".format(
                  len(peers_arr) if isinstance(peers_arr, list) else None, len(lp)))

        # 造 600 行日志（full=True 先清空），验证 n 夹取 ≤500
        try:
            lines = [{"ts": "t{}".format(i), "tag": "L", "level": 1,
                      "msg": "line-{}".format(i), "file": "", "func": ""}
                     for i in range(600)]
            netlink.get_bus().publish(TOPIC_PEER_LOG,
                                      {"node_id": "webui-test-peer",
                                       "lines": lines, "seq": 600, "full": True})
        except Exception:
            pass
        j6c = _json(_http("/api/logs?node=webui-test-peer&n=99999", token=token)[1]) or {}
        nlines = len(j6c.get("lines") or [])
        check("6c. /api/logs?n=99999 被夹取（返回行数 ≤ 500）",
              nlines <= 500, "lines={}".format(nlines))

        # ══════════════════ 7. 只读边界 ══════════════════
        c7a, _, _ = _http("/api/state", token=token, method="POST")
        check("7a. POST /api/state → 405", c7a == 405, "code={}".format(c7a))
        c7b, _, _ = _http("/api/run", token=token)
        check("7b. GET /api/run → 404", c7b == 404, "code={}".format(c7b))
        page = _http("/", token=token)[1].decode("utf-8", "ignore")
        check("7c. 页面正文不含 CMD_RUN / CMD_STOP / push_script",
              ("CMD_RUN" not in page) and ("CMD_STOP" not in page)
              and ("push_script" not in page))

        # ══════════════════ 8. 令牌不落 config.json ══════════════════
        cfg_text = ""
        try:
            state.save_config()
            if os.path.exists(state.CONFIG_PATH):
                with open(state.CONFIG_PATH, "r", encoding="utf-8") as f:
                    cfg_text = f.read()
        except Exception as e:
            cfg_text = "<read error: {!r}>".format(e)
        check("8. config.json 不含令牌明文（令牌只在凭据库）",
              bool(token) and (token not in cfg_text),
              "token_in_cfg={}".format(token in cfg_text if token else None))

        # ══════════════════ 9. 数据联动（订阅快照）══════════════════
        state.running = True
        try:
            state.exec_state["row"] = 7
        except Exception:
            pass
        try:
            netlink.get_bus().publish(TOPIC_PEER_STATE, {
                "node_id": "webui-test-peer", "running": True, "paused": False,
                "recording": False, "script": "t.xls", "row": 7,
                "total_rows": 10, "loop": 0, "total_loops": 1,
                "elapsed": 1, "node": "WebTest", "ts": int(time.time())})
        except Exception as e:
            print("   publish exc: {!r}".format(e))

        def _row_seen():
            jj = _json(_http("/api/state", token=token)[1]) or {}
            peers = jj.get("peers") or {}
            ent = peers.get("webui-test-peer") or {}
            if int(ent.get("row") or 0) == 7:
                return True
            jj2 = _json(_http("/api/summary", token=token)[1]) or {}
            st = ((jj2.get("node") or {}).get("state") or {})
            return int(st.get("row") or 0) == 7

        ok9, dt9 = wait_until(_row_seen, 8.0)
        check("9. 订阅快照联动: ≤8s 内 /api/state 或 /api/summary 看到 row==7（{:.2f}s）"
              .format(dt9), ok9)

        # ══════════════════ 10. rotate_token ══════════════════
        old = token
        try:
            new = netlink.rotate_webui_token()
        except Exception as e:
            new = ""
            print("   rotate exc: {!r}".format(e))
        c10a, _, _ = _http("/api/status", token=old)
        c10b, _, _ = _http("/api/status", token=new)
        check("10. rotate_token(): 旧令牌 401 且新令牌 200",
              bool(new) and new != old and c10a == 401 and c10b == 200,
              "old_code={} new_code={} new_ok={}".format(
                  c10a, c10b, bool(new)))
        token = new

        # ══════════════════ 11. 停止与重绑 + 幂等 ══════════════════
        try:
            netlink.stop_webui()
        except Exception as e:
            print("   stop_webui exc: {!r}".format(e))
        ok11a, dt11 = wait_until(lambda: _can_bind(PORT_WEB), 5.0)
        check("11a. stop_webui() 后端口可再次 bind（{:.2f}s）".format(dt11), ok11a)

        s1 = bool(netlink.start_webui())
        s2 = bool(netlink.start_webui())
        check("11b. start_webui() 连续两次均 True（幂等）", s1 and s2,
              "s1={} s2={}".format(s1, s2))
        t1 = None
        t2 = None
        try:
            netlink.stop_webui()
            t1 = True
        except Exception as e:
            t1 = e
        try:
            netlink.stop_webui()
            t2 = True
        except Exception as e:
            t2 = e
        check("11c. stop_webui() 连续两次不抛异常（幂等）",
              t1 is True and t2 is True,
              "t1={!r} t2={!r}".format(t1, t2))

        # ══════════════════ 12. 线程收敛 ══════════════════
        def _web_threads():
            return [t for t in threading.enumerate()
                    if str(getattr(t, "name", "")) == "nl-web"]

        ok12, dt12 = wait_until(lambda: len(_web_threads()) == 0, 5.0)
        check("12. stop 后 nl-web 线程 ≤5s 消失（{:.2f}s）".format(dt12), ok12,
              "remain={}".format([t.name for t in _web_threads()]))

        # ══════════════════ 13. stop_netlink 连带停 webui ══════════════════
        s13 = bool(netlink.start_webui())
        r13 = bool(netlink.is_webui_running())
        try:
            netlink.stop_netlink()
            started_netlink = False
        except Exception as e:
            print("   stop_netlink exc: {!r}".format(e))
        after = bool(netlink.is_webui_running())
        ok13b, dt13 = wait_until(lambda: _can_bind(PORT_WEB), 5.0)
        check("13. stop_netlink() 连带停止 webui（running→False 且端口释放）",
              s13 and r13 and (after is False) and ok13b,
              "start={} running={} after={} rebind={}({:.2f}s)".format(
                  s13, r13, after, ok13b, dt13))

        # ══════════════════ 14. 新增控制 state 键（加性默认安全）══════════════════
        check("14a. netlink_web_control 默认 False",
              getattr(state, "NETLINK_WEB_CONTROL", None) is False,
              "got={!r}".format(getattr(state, "NETLINK_WEB_CONTROL", None)))
        check("14b. netlink_web_tls 默认 False",
              getattr(state, "NETLINK_WEB_TLS", None) is False)
        check("14c. netlink_web_control_ttl 默认 300",
              int(getattr(state, "NETLINK_WEB_CONTROL_TTL", 0)) == 300)
        check("14d. netlink_web_allow_remote_control 默认 False",
              getattr(state, "NETLINK_WEB_ALLOW_REMOTE_CONTROL", None) is False)
        try:
            state.save_config()
            cfgc = ""
            if os.path.exists(state.CONFIG_PATH):
                with open(state.CONFIG_PATH, "r", encoding="utf-8") as f:
                    cfgc = f.read()
            check("14e. config.json 含 5 个新控制键（加性）",
                  all(k in cfgc for k in (
                      "netlink_web_control", "netlink_web_control_ttl",
                      "netlink_web_tls", "netlink_web_confirm_control",
                      "netlink_web_allow_remote_control")),
                  "len={}".format(len(cfgc)))
        except Exception as e:
            check("14e. config.json 含 5 个新控制键（加性）", False, repr(e))

        # 重启节点 + 只读面板（控制关闭）用于控制端点断言
        state.NETLINK_WEB_CONTROL = False
        state.NETLINK_WEB_TLS = False
        state.NETLINK_WEB_ENABLED = True
        ok_nl2 = False
        try:
            ok_nl2 = bool(netlink.start_netlink(root=None))
        except Exception as e:
            print("   restart netlink exc: {!r}".format(e))
        started_netlink = ok_nl2
        ok_w2 = False
        try:
            ok_w2 = bool(netlink.start_webui())
        except Exception:
            ok_w2 = False
        check("14f. 重启节点 + 只读面板（控制关闭）", ok_nl2 and ok_w2)

        if ok_w2:
            token2 = ""
            try:
                token2 = netlink.webui_token()
            except Exception:
                token2 = ""
            url2 = ""
            try:
                url2 = netlink.webui_url() or ""
            except Exception:
                url2 = ""
            check("15. 控制关闭时 url() 仍以 http:// 开头（向后兼容）",
                  url2.startswith("http://"), "url={!r}".format(url2))

            c16, b16, _ = _http("/api/control/status", token=token2)
            j16 = _json(b16) or {}
            check("16. GET /api/control/status → 200 enabled=false unlocked=false tls=false",
                  c16 == 200 and j16.get("enabled") is False
                  and j16.get("unlocked") is False and j16.get("tls") is False
                  and j16.get("scheme") == "http",
                  "code={} body={}".format(c16, j16))

            c17a, _, _ = _http("/", token=token2, method="POST")
            c17b, _, _ = _http("/api/summary", token=token2, method="POST")
            c17c, _, _ = _http("/api/control/status", token=token2, method="POST")
            check("17. POST / 与 /api/summary 与 /api/control/status 仍 405（只读边界不回退）",
                  c17a == 405 and c17b == 405 and c17c == 405,
                  "root={} summary={} status={}".format(c17a, c17b, c17c))

            c18a, b18a, _ = _http("/api/control", token=token2, method="POST",
                                  body={"action": "run"})
            c18b, b18b, _ = _http("/api/control/unlock", token=token2, method="POST",
                                  body={"pin": PIN})
            c18c, b18c, _ = _http("/api/control/lock", token=token2, method="POST",
                                  body={})
            j18a = _json(b18a) or {}
            j18b = _json(b18b) or {}
            j18c = _json(b18c) or {}
            check("18. 控制关闭: control/unlock/lock → 404 control disabled",
                  c18a == 404 and c18b == 404 and c18c == 404
                  and j18a.get("error") == "control disabled"
                  and j18b.get("error") == "control disabled"
                  and j18c.get("error") == "control disabled",
                  "codes={}/{}/{}".format(c18a, c18b, c18c))

            c19, _, _ = _http("/api/control/unlock", method="POST",
                              body={"pin": PIN})
            check("19. 无令牌 POST /api/control/unlock → 401（L1 优先）",
                  c19 == 401, "code={}".format(c19))

        # ══════════════════ 20. 控制 PIN 存储（凭据库，不落 config）══════════════════
        try:
            security.forget_control_pin()
        except Exception:
            pass
        security.reset_control_pin_guard()
        check("20a. store_control_pin('123456') → True",
              security.store_control_pin(PIN) is True)
        raw = state.cred_read(CTL_TARGET) or ""
        check("20b. 凭据库值 'pbkdf2$salt$hash' 且不含明文 PIN",
              raw.startswith("pbkdf2$") and (PIN not in raw)
              and len(raw.split("$")) == 3, "raw={!r}".format(raw[:20]))
        state.save_config()
        cfg2 = ""
        if os.path.exists(state.CONFIG_PATH):
            with open(state.CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg2 = f.read()
        check("20c. config.json 不含 PIN 明文/哈希",
              (PIN not in cfg2) and ("pbkdf2" not in cfg2))
        check("20d. verify correct=True / wrong=False",
              security.verify_control_pin(PIN) is True
              and security.verify_control_pin("654321") is False)
        security.reset_control_pin_guard()

        # ══════════════════ 21. 防暴力锁定（security 层）══════════════════
        security.reset_control_pin_guard()
        for _i in range(security.MAX_PIN_FAILS):
            security.verify_control_pin("000000")
        st21 = security.control_pin_lock_state()
        check("21. 连续 {} 次错误 → locked 且 retry_after>0；正确 PIN 亦拒".format(
            security.MAX_PIN_FAILS),
              st21["locked"] is True and int(st21["retry_after"]) > 0
              and security.verify_control_pin(PIN) is False, "st={}".format(st21))
        security.reset_control_pin_guard()

        # ══════════════════ 22. 防御层：控制开+明文 → 403 plaintext rejected ══════════════════
        node2 = None
        try:
            node2 = netlink.get_node()
        except Exception:
            node2 = None
        if node2 is None:
            check("22. 防御层：控制开+明文 → 403 plaintext rejected", False, "no node")
        else:
            from netlink.webui import WebUI as _WebUI
            ui22 = _WebUI(node2, port=PORT_DEF, bind="127.0.0.1",
                          control=True, tls=False, cert="", key="")
            ui22._tls_wanted = False       # 白盒：强制明文以覆盖防御层
            ok22, why22 = ui22.start()
            try:
                tk22 = netlink.webui_token()
            except Exception:
                tk22 = ""
            c22, b22, _ = _http("/api/control/unlock", token=tk22,
                                method="POST", body={"pin": PIN}, port=PORT_DEF)
            j22 = _json(b22) or {}
            check("22. 防御层：控制开+明文 → 写端点 403 plaintext rejected",
                  ok22 is True and c22 == 403
                  and j22.get("error") == "plaintext rejected",
                  "ok={} code={} body={}".format(ok22, c22, j22))
            try:
                ui22.stop()
            except Exception:
                pass

        # ══════════════════ 23. 控制开+证书不可用 → start() 拒绝（不降级）══════════════════
        if node2 is None:
            check("23. 控制开+证书缺失 → start() = (False, 'cert missing')", False, "no node")
        else:
            from netlink.webui import WebUI as _WebUI2
            ui23 = _WebUI2(node2, port=PORT_DEF, bind="127.0.0.1",
                           control=True, tls=True, cert="", key="")
            res23 = ui23.start()
            if isinstance(res23, (tuple, list)):
                ok23 = bool(res23[0]) if len(res23) > 0 else False
                why23 = str(res23[1]) if len(res23) > 1 else ""
            else:
                ok23 = bool(res23)
                why23 = ""
            check("23a. 控制开+证书缺失 → start() = (False, 'cert missing')",
                  ok23 is False and why23 == "cert missing", "res={!r}".format(res23))
            check("23b. 端口未监听（绝不降级明文）",
                  not _can_connect(PORT_DEF), "port {}".format(PORT_DEF))

        # ══════════════════ 24. 控制启用 + 强制 HTTPS 全链路 ══════════════════
        cert = os.path.join(tmpdir, "panel.crt")
        key = os.path.join(tmpdir, "panel.key")
        how = _make_cert(cert, key)
        if not how or node2 is None:
            skip("24. 控制启用 HTTPS 全链路（unlock/CSRF/白名单/429/lock）",
                 "证书不可生成（无 cryptography/openssl）或无节点")
        else:
            print("   证书生成分支: {}".format(how))
            from netlink.webui import WebUI as _WebUI3
            hooks = _Hooks()
            try:
                node2.set_control_hooks(run=hooks.run, stop=hooks.stop)
            except Exception:
                pass
            state.NETLINK_WEB_CONFIRM_CONTROL = False
            state.NETLINK_CONFIRM_CONTROL = False
            state.recording = False
            state.running = False
            state.has_script = True
            security.store_control_pin(PIN)
            security.reset_control_pin_guard()
            ui24 = _WebUI3(node2, port=PORT_CTL, bind="127.0.0.1",
                           control=True, tls=True, cert=cert, key=key)
            ok24, why24 = ui24.start()
            check("24a. 控制面板 HTTPS 启动成功", ok24 is True,
                  "ok={} why={!r}".format(ok24, why24))
            if ok24:
                tk = ui24.token()
                cc, bb, _ = _https("/api/control/status", token=tk)
                jcc = _json(bb) or {}
                check("24b. HTTPS GET /api/control/status → 200 tls=true scheme=https",
                      cc == 200 and jcc.get("tls") is True
                      and jcc.get("scheme") == "https",
                      "code={} body={}".format(cc, jcc))
                pc, _, _ = _http("/api/control/status", token=tk, port=PORT_CTL)
                check("24c. 对 HTTPS 端口发明文 HTTP → 失败（不降级）",
                      pc == 0, "code={}".format(pc))
                c1, b1, _ = _https("/api/control/unlock", token=tk, method="POST",
                                   body={"pin": "000000"})
                check("24d. 错误 PIN → 401 pin invalid",
                      c1 == 401 and (_json(b1) or {}).get("error") == "pin invalid",
                      "code={}".format(c1))
                security.reset_control_pin_guard()
                for _i in range(security.MAX_PIN_FAILS):
                    _https("/api/control/unlock", token=tk, method="POST",
                           body={"pin": "999999"})
                lc, _, lh = _https("/api/control/unlock", token=tk, method="POST",
                                   body={"pin": "999999"})
                ra = str((lh or {}).get("Retry-After") or "")
                check("24e. 连续 {} 次错误 → 第 {} 次 429 + Retry-After>0".format(
                    security.MAX_PIN_FAILS, security.MAX_PIN_FAILS + 1),
                      lc == 429 and ra.isdigit() and int(ra) > 0,
                      "code={} retry={!r}".format(lc, ra))
                c2, _, _ = _https("/api/control/unlock", token=tk, method="POST",
                                  body={"pin": PIN})
                check("24f. 锁定期间正确 PIN 亦 429（token 正确也锁）",
                      c2 == 429, "code={}".format(c2))
                security.reset_control_pin_guard()
                c3, b3, h3 = _https("/api/control/unlock", token=tk, method="POST",
                                    body={"pin": PIN})
                j3 = _json(b3) or {}
                sc = str((h3 or {}).get("Set-Cookie") or "")
                csrf = str(j3.get("csrf") or "")
                sid = _cookie_from_hdr(h3, "nl_ctl")
                check("24g. 正确 PIN → 200 + csrf + nl_ctl(Secure/Strict/HttpOnly)",
                      c3 == 200 and bool(csrf) and bool(sid)
                      and "Secure" in sc and "SameSite=Strict" in sc
                      and "HttpOnly" in sc,
                      "code={} sc={!r}".format(c3, sc[:110]))
                c4, b4, _ = _https("/api/control/status", token=tk,
                                   headers={"Cookie": "nl_ctl=" + sid})
                j4 = _json(b4) or {}
                check("24h. status → unlocked=true 且 0<expires_in<=ttl",
                      c4 == 200 and j4.get("unlocked") is True
                      and 0 < int(j4.get("expires_in") or 0) <= 300,
                      "body={}".format(j4))
                c5, b5, _ = _https("/api/control", token=tk, method="POST",
                                   body={"action": "run"},
                                   headers={"Cookie": "nl_ctl=" + sid})
                check("24i. 无 CSRF → 403 csrf mismatch",
                      c5 == 403 and (_json(b5) or {}).get("error") == "csrf mismatch",
                      "code={}".format(c5))
                c6, _, _ = _https("/api/control", token=tk, method="POST",
                                  body={"action": "run"},
                                  headers={"Cookie": "nl_ctl=" + sid,
                                           "X-NL-CSRF": "wrong"})
                check("24j. 错误 CSRF → 403", c6 == 403, "code={}".format(c6))
                c7, b7, _ = _https("/api/control", token=tk, method="POST",
                                   body={"action": "pause", "csrf": csrf},
                                   headers={"Cookie": "nl_ctl=" + sid})
                check("24k. action=pause → 400 action not allowed",
                      c7 == 400 and (_json(b7) or {}).get("error") == "action not allowed",
                      "code={} body={}".format(c7, _json(b7)))
                c8, b8, _ = _https("/api/control", token=tk, method="POST",
                                   body={"action": "resume", "csrf": csrf},
                                   headers={"Cookie": "nl_ctl=" + sid})
                check("24k2. action=resume → 400 action not allowed", c8 == 400,
                      "code={}".format(c8))
                state.has_script = False
                c9, b9, _ = _https("/api/control", token=tk, method="POST",
                                   body={"action": "run", "csrf": csrf},
                                   headers={"Cookie": "nl_ctl=" + sid})
                check("24l. run 无脚本 → 409 no script selected",
                      c9 == 409 and (_json(b9) or {}).get("error") == "no script selected",
                      "code={} body={}".format(c9, _json(b9)))
                state.has_script = True
                hooks.reset()
                c10, b10, _ = _https("/api/control", token=tk, method="POST",
                                     body={"action": "run", "csrf": csrf},
                                     headers={"Cookie": "nl_ctl=" + sid})
                j10 = _json(b10) or {}
                check("24m. run（有脚本）→ 200 done 且经假钩子执行（loops=None）",
                      c10 == 200 and j10.get("status") == "done"
                      and len(hooks.run_calls) == 1 and hooks.run_calls[0] is None,
                      "code={} body={} calls={}".format(c10, j10, hooks.run_calls))
                c11, b11, _ = _https("/api/control", token=tk, method="POST",
                                     body={"action": "run", "csrf": csrf},
                                     headers={"Cookie": "nl_ctl=" + sid})
                check("24n. run 已运行 → 409 already running",
                      c11 == 409 and (_json(b11) or {}).get("error") == "already running",
                      "code={} body={}".format(c11, _json(b11)))
                c12, b12, _ = _https("/api/control", token=tk, method="POST",
                                     body={"action": "stop", "csrf": csrf},
                                     headers={"Cookie": "nl_ctl=" + sid})
                check("24o. stop → 200 且假钩子被调（串行）",
                      c12 == 200 and hooks.stop_calls >= 1,
                      "code={} stops={}".format(c12, hooks.stop_calls))
                c13, _, _ = _https("/api/control/lock", token=tk, method="POST",
                                   body={}, headers={"Cookie": "nl_ctl=" + sid})
                c14, b14, _ = _https("/api/control", token=tk, method="POST",
                                     body={"action": "run", "csrf": csrf},
                                     headers={"Cookie": "nl_ctl=" + sid})
                check("24p. lock → 200；随后 control → 403 control locked",
                      c13 == 200 and c14 == 403
                      and (_json(b14) or {}).get("error") == "control locked",
                      "lock={} after={}".format(c13, c14))
                try:
                    alines = list(ui24._audit.tail(500))
                except Exception:
                    alines = []
                check("24q. 审计含 WEB_UNLOCK/WEB_PIN_FAIL/WEB_LOCK/CMD_RUN/CMD_STOP + actor=web-panel|",
                      _has(alines, "cmd=WEB_UNLOCK")
                      and _has(alines, "cmd=WEB_PIN_FAIL")
                      and _has(alines, "cmd=WEB_LOCK")
                      and _has(alines, "cmd=CMD_RUN")
                      and _has(alines, "cmd=CMD_STOP")
                      and _has(alines, "actor=web-panel|"),
                      "n={}".format(len(alines)))
            try:
                ui24.stop()
            except Exception:
                pass

        # ══════════════════ 25. PWA 静态端点（免 token）+ 前端注入 ══════════════════
        tk_pwa = ""
        try:
            tk_pwa = netlink.webui_token()
        except Exception:
            tk_pwa = ""
        pwa_running = False
        try:
            pwa_running = bool(netlink.is_webui_running())
        except Exception:
            pwa_running = False
        if not pwa_running:
            try:
                state.NETLINK_WEB_CONTROL = False
                state.NETLINK_WEB_TLS = False
                state.NETLINK_WEB_BIND = "127.0.0.1"
                state.NETLINK_WEB_ENABLED = True
                pwa_running = bool(netlink.start_webui())
            except Exception:
                pwa_running = False
        if not pwa_running:
            check("25. PWA 静态端点", False, "只读面板未运行")
        else:
            cm, bm, hm = _http("/manifest.webmanifest")      # 免 token
            jm = _json(bm) or {}
            ctype_m = str((hm or {}).get("Content-Type") or "")
            check("25a. /manifest.webmanifest 免 token → 200 且 JSON 含 display/scope/icons",
                  cm == 200 and "manifest+json" in ctype_m
                  and jm.get("display") == "standalone" and jm.get("scope") == "/"
                  and isinstance(jm.get("icons"), list) and len(jm["icons"]) >= 2,
                  "code={} ctype={!r} keys={}".format(cm, ctype_m, sorted(jm.keys())))
            check("25b. manifest Cache-Control 含 no-cache",
                  "no-cache" in str((hm or {}).get("Cache-Control") or ""),
                  "cc={!r}".format((hm or {}).get("Cache-Control")))

            cs, bs, hs = _http("/sw.js")
            txt = bs.decode("utf-8", "ignore")
            check("25c. /sw.js 免 token → 200 含 CACHE_VERSION 与 install/activate/fetch（含 /api/ 判定）",
                  cs == 200 and "CACHE_VERSION" in txt
                  and "addEventListener('install'" in txt
                  and "addEventListener('activate'" in txt
                  and "addEventListener('fetch'" in txt
                  and "/api/" in txt,
                  "code={}".format(cs))
            check("25d. /sw.js 响应含 Service-Worker-Allowed: /",
                  str((hs or {}).get("Service-Worker-Allowed") or "") == "/",
                  "hdr={!r}".format((hs or {}).get("Service-Worker-Allowed")))

            co, bo, ho = _http("/offline.html")
            check("25e. /offline.html 免 token → 200 text/html 含 acrpa.snap.v1",
                  co == 200 and "text/html" in str((ho or {}).get("Content-Type") or "")
                  and b"acrpa.snap.v1" in bo,
                  "code={} ctype={!r}".format(co, (ho or {}).get("Content-Type")))

            ok_icons = True
            det_icons = []
            for pth in ("/icons/icon-192.png", "/icons/icon-512.png",
                        "/icons/apple-touch-icon.png"):
                ci, bi, hi = _http(pth)
                good = (ci == 200 and bi[:4] == b"\x89PNG"
                        and "image/png" in str((hi or {}).get("Content-Type") or "")
                        and "max-age" in str((hi or {}).get("Cache-Control") or ""))
                if not good:
                    ok_icons = False
                    det_icons.append("{}:code={} magic={} ctype={!r}".format(
                        pth, ci, bi[:4], (hi or {}).get("Content-Type")))
            check("25f. /icons/* 免 token → 200 PNG 魔数 + 长缓存", ok_icons,
                  "; ".join(det_icons))

            cf, bf, hf = _http("/favicon.ico")
            check("25g. /favicon.ico 免 token → 200 非空",
                  cf == 200 and len(bf) > 0,
                  "code={} len={} ctype={!r}".format(
                      cf, len(bf), (hf or {}).get("Content-Type")))

            ca, _, _ = _http("/api/summary")      # 免 token 取数据必须失败
            check("25h. 免 token GET /api/summary → 401（数据仍受令牌保护）",
                  ca == 401, "code={}".format(ca))

            cp, bp, _ = _http("/", token=tk_pwa)
            page_txt = bp.decode("utf-8", "ignore")
            check("25i. _PAGE 含 manifest link/theme-color/控制区/CSRF 头/离线快照键/SW 注册",
                  cp == 200 and 'rel="manifest"' in page_txt
                  and "theme-color" in page_txt
                  and 'id="ctlbar"' in page_txt
                  and "X-NL-CSRF" in page_txt
                  and "acrpa.snap.v1" in page_txt
                  and "serviceWorker" in page_txt,
                  "code={}".format(cp))
            check("25j. _PAGE 不含 CMD_RUN/CMD_STOP/push_script（白名单仅 run/stop）",
                  ("CMD_RUN" not in page_txt) and ("CMD_STOP" not in page_txt)
                  and ("push_script" not in page_txt))

    except Exception:
        print("[FAIL] 未捕获异常:")
        traceback.print_exc()
        _fails += 1
    finally:
        return finish(tmpdir, saved, saved_token, saved_exec,
                      started_netlink, True, saved_pin)


def finish(tmpdir, saved, saved_token, saved_exec, started_netlink, full,
           saved_pin=None):
    global _fails
    # ── 收尾：停面板 + 停节点 ──
    try:
        netlink.stop_webui()
    except Exception:
        pass
    try:
        netlink.stop_netlink()
    except Exception:
        pass

    # ── 还原环境 ──
    try:
        state.CONFIG_PATH = saved.get("CONFIG_PATH")
        state.NETLINK_ENABLED = saved.get("NETLINK_ENABLED")
        state.NETLINK_PORT = saved.get("NETLINK_PORT")
        state.NETLINK_DISCOVERY_PORT = saved.get("NETLINK_DISCOVERY_PORT")
        state.NETLINK_AUTODISCOVER = saved.get("NETLINK_AUTODISCOVER")
        state.NETLINK_DEVICE_NAME = saved.get("NETLINK_DEVICE_NAME")
        state.NETLINK_STATIC_PEERS = saved.get("NETLINK_STATIC_PEERS")
        state.NETLINK_WEB_ENABLED = saved.get("NETLINK_WEB_ENABLED")
        state.NETLINK_WEB_PORT = saved.get("NETLINK_WEB_PORT")
        state.NETLINK_WEB_BIND = saved.get("NETLINK_WEB_BIND")
        state.NETLINK_REQUIRE_AUTH = saved.get("NETLINK_REQUIRE_AUTH")
        state.NETLINK_TLS_CERT = saved.get("NETLINK_TLS_CERT")
        state.NETLINK_TLS_KEY = saved.get("NETLINK_TLS_KEY")
        state.NETLINK_WEB_CONTROL = saved.get("NETLINK_WEB_CONTROL")
        state.NETLINK_WEB_CONTROL_TTL = saved.get("NETLINK_WEB_CONTROL_TTL")
        state.NETLINK_WEB_TLS = saved.get("NETLINK_WEB_TLS")
        state.NETLINK_WEB_CONFIRM_CONTROL = saved.get("NETLINK_WEB_CONFIRM_CONTROL")
        state.NETLINK_WEB_ALLOW_REMOTE_CONTROL = saved.get(
            "NETLINK_WEB_ALLOW_REMOTE_CONTROL")
        state.NETLINK_CONFIRM_CONTROL = saved.get("NETLINK_CONFIRM_CONTROL")
        state.running = saved.get("running")
        state.recording = saved.get("recording")
        state.filename = saved.get("filename")
        state.has_script = saved.get("has_script")
        state.quit2 = saved.get("quit2")
        state.quit3 = saved.get("quit3")
        state.exec_state = saved_exec
        if saved_token:
            state.cred_write(TOKEN_TARGET, saved_token)
        else:
            state.cred_delete(TOKEN_TARGET)
        if saved_pin:
            state.cred_write(CTL_TARGET, saved_pin)
        else:
            state.cred_delete(CTL_TARGET)
    except Exception:
        pass
    try:
        shutil.rmtree(tmpdir, ignore_errors=True)
    except Exception:
        pass

    elapsed = time.time() - T0
    print("-" * 66)
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
