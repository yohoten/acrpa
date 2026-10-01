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
import json
import os
import shutil
import socket
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
from netlink.node import TOPIC_PEER_STATE, TOPIC_PEER_LOG

PORT_WEB = 19997
PORT_TCP = 19981
PORT_UDP = 19982
TOKEN_TARGET = "ACRPA/netlink/web-token"
TOTAL_BUDGET = 60.0

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
def _http(path, token=None, cookie=None, bearer=None, method="GET", timeout=5.0):
    url = "http://127.0.0.1:{}{}".format(PORT_WEB, path)
    if token is not None:
        sep = "&" if "?" in path else "?"
        url = url + sep + "token=" + urllib.parse.quote(token)
    req = urllib.request.Request(url, method=method)
    if cookie is not None:
        req.add_header("Cookie", "nl_token=" + cookie)
    if bearer is not None:
        req.add_header("Authorization", "Bearer " + bearer)
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
                 "running", "filename", "quit2", "quit3",
                 "NETLINK_REQUIRE_AUTH"):
        saved[name] = getattr(state, name, None)
    saved_exec = dict(getattr(state, "exec_state", {}) or {})
    try:
        saved_token = state.cred_read(TOKEN_TARGET)
    except Exception:
        saved_token = None

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
                          started_netlink, False)

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

    except Exception:
        print("[FAIL] 未捕获异常:")
        traceback.print_exc()
        _fails += 1
    finally:
        return finish(tmpdir, saved, saved_token, saved_exec,
                      started_netlink, True)


def finish(tmpdir, saved, saved_token, saved_exec, started_netlink, full):
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
        state.running = saved.get("running")
        state.filename = saved.get("filename")
        state.quit2 = saved.get("quit2")
        state.quit3 = saved.get("quit3")
        state.exec_state = saved_exec
        if saved_token:
            state.cred_write(TOKEN_TARGET, saved_token)
        else:
            state.cred_delete(TOKEN_TARGET)
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
