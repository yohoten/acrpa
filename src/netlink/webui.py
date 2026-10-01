# -*- coding: utf-8 -*-
"""ACRPA NetLink Phase 4-2 — 浏览器只读监控面板（纯标准库，Python 3.7 兼容）。

定位：给不装 ACRPA 的主管 / 手机 / 平板用一个浏览器即可查看本机与已连接设备的
运行状态、进度、定时任务与最近日志。**面板本身不含任何写操作**（运行 / 停止 /
推脚本一律不出现），仅提供只读 GET 接口。

设计约束（为什么这样做）：
  * 纯标准库（http.server / socketserver / hmac / secrets），不新增 pip 依赖；
  * netlink 包内禁止顶层 GUI import —— state 仅在函数内懒 import；
  * 通过 `node.bus.subscribe()` 订阅主题并把最新值存进内部快照字典，
    **绝不调用 `bus.drain()`**（否则会与设备互联窗口抢数据）；
  * 所有线程 daemon=True，线程体与每个请求整体 try/except，异常绝不向上抛；
  * 面板默认关闭（`netlink_web_enabled` 默认 False），启用即要求令牌；
    令牌只写 Windows 凭据库（`state.cred_write`），绝不落 config.json。

对外接口见 WebUI / get_token / rotate_token。
"""
import collections
import hmac
import http.server
import json
import os
import secrets
import socket
import threading
import urllib.parse

from .connection import _nl_log

# ── 主题常量（容错导入；缺失时回退字符串，保证本模块可独立导入）──
try:
    from .node import (TOPIC_PEER, TOPIC_PEER_STATE, TOPIC_PEER_LOG,
                       TOPIC_PEER_SCHED, TOPIC_STATUS, TOPIC_CMD_RESULT,
                       TOPIC_SCREENSHOT)
except Exception:  # pragma: no cover
    TOPIC_PEER = "netlink.peer"
    TOPIC_PEER_STATE = "netlink.peer_state"
    TOPIC_PEER_LOG = "netlink.peer_log"
    TOPIC_PEER_SCHED = "netlink.peer_sched"
    TOPIC_STATUS = "netlink.status"
    TOPIC_CMD_RESULT = "netlink.cmd_result"
    TOPIC_SCREENSHOT = "netlink.screenshot"
try:
    from .transfer import TOPIC_TRANSFER
except Exception:  # pragma: no cover
    TOPIC_TRANSFER = "netlink.transfer"


DEFAULT_PORT = 19712
TOKEN_TARGET = "ACRPA/netlink/web-token"   # 凭据库 key
COOKIE_NAME = "nl_token"

LOG_CAP = 300          # 每设备日志环形缓冲上限
MAX_N = 500            # /api/logs n 上限
DEFAULT_N = 200        # /api/logs n 默认值

_ALL_TOPICS = (TOPIC_PEER, TOPIC_PEER_STATE, TOPIC_PEER_LOG, TOPIC_PEER_SCHED,
               TOPIC_STATUS, TOPIC_CMD_RESULT, TOPIC_TRANSFER, TOPIC_SCREENSHOT)

_TOKEN_LOCK = threading.RLock()


# ══════════════════════════════════════════════════════════════════════════
# 令牌（存 Windows 凭据库；不存在则生成，绝不写 config.json）
# ══════════════════════════════════════════════════════════════════════════
def _state():
    """懒 import state（state 不顶层 import tkinter，此处仍保持懒加载习惯）。"""
    try:
        import state
        return state
    except Exception as e:
        _nl_log("webui: import state failed: {}".format(e), "WARN")
        return None


def _new_token():
    """生成一个高熵令牌（secrets 优先，失败回退 os.urandom）。"""
    try:
        return secrets.token_urlsafe(24)
    except Exception:
        try:
            return os.urandom(24).hex()
        except Exception:
            return "nl{}".format(id(object()))


def get_token():
    """从凭据库读取面板令牌；不存在则生成并写入，返回令牌字符串。"""
    st = _state()
    tok = ""
    try:
        if st is not None:
            tok = str(st.cred_read(TOKEN_TARGET) or "")
    except Exception:
        tok = ""
    if tok:
        return tok
    return rotate_token()


def rotate_token():
    """重新生成令牌（写凭据库）并返回新值；凭据库不可用时仍返回内存令牌。"""
    with _TOKEN_LOCK:
        tok = _new_token()
        st = _state()
        try:
            if st is not None:
                st.cred_write(TOKEN_TARGET, tok)
        except Exception:
            pass
        return tok


# ══════════════════════════════════════════════════════════════════════════
# 小工具
# ══════════════════════════════════════════════════════════════════════════
def _const_eq(a, b):
    """常量时间比较，避免时序侧信道；异常一律视为不等。"""
    try:
        return hmac.compare_digest(str(a or ""), str(b or ""))
    except Exception:
        return False


def _clamp_n(v):
    """把 /api/logs 的 n 夹取到 [1, MAX_N]；非法值回退默认。"""
    try:
        n = int(v)
    except Exception:
        return DEFAULT_N
    if n < 1:
        n = 1
    if n > MAX_N:
        n = MAX_N
    return n


def _safe_port(v):
    try:
        p = int(v)
    except Exception:
        return DEFAULT_PORT
    if p < 1 or p > 65535:
        return DEFAULT_PORT
    return p


def _local_ipv4():
    """本机主 IPv4（不真正发包的 UDP connect 技巧）；失败回退 hostname 解析。"""
    s = None
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.2)
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        pass
    finally:
        try:
            if s is not None:
                s.close()
        except Exception:
            pass
    try:
        return socket.gethostbyname(socket.gethostname())
    except Exception:
        return ""


# ══════════════════════════════════════════════════════════════════════════
# 单页 HTML（内联 CSS/JS；无外部资源、无 CDN；JS 端 textContent 赋值防 XSS）
# ══════════════════════════════════════════════════════════════════════════
_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ACRPA 监控面板</title>
<style>
 body{font-family:-apple-system,"Segoe UI",Roboto,"Microsoft YaHei",sans-serif;margin:0;background:#0f172a;color:#e2e8f0;}
 header{padding:12px 16px;background:#1e293b;}
 h1{font-size:16px;margin:0 0 4px;}
 .sub{font-size:12px;color:#94a3b8;}
 .wrap{padding:12px 16px;}
 table{width:100%;border-collapse:collapse;font-size:13px;}
 th,td{text-align:left;padding:6px 8px;border-bottom:1px solid #1e293b;vertical-align:top;}
 th{color:#94a3b8;font-weight:600;white-space:nowrap;}
 tr.row{cursor:pointer;}
 tr.row:hover{background:#1e293b;}
 .off{color:#64748b;}
 .run{color:#34d399;}
 pre{white-space:pre-wrap;word-break:break-all;background:#020617;color:#cbd5e1;padding:8px;font-size:12px;margin:4px 0 0;max-height:320px;overflow:auto;}
 #banner{background:#7f1d1d;color:#fff;padding:6px 16px;font-size:13px;display:none;}
 .empty{color:#64748b;padding:10px 8px;}
</style>
</head>
<body>
<div id="banner">连接已断开</div>
<header>
  <h1>ACRPA 只读监控面板</h1>
  <div class="sub" id="selfline">本机：— ｜ 节点：— ｜ 连接设备：0</div>
</header>
<div class="wrap">
  <table>
    <thead><tr>
      <th>设备</th><th>状态</th><th>脚本</th><th>进度</th><th>循环</th>
      <th>已运行</th><th>定时下次</th><th>权限</th>
    </tr></thead>
    <tbody id="tbody"></tbody>
  </table>
  <div id="logbox" style="margin-top:12px;display:none;">
    <div class="sub" id="logtitle">日志</div>
    <pre id="loglines"></pre>
  </div>
</div>
<script>
var expanded = null;
var schedMap = {};
function pad2(n){ return (n < 10 ? '0' : '') + n; }
function fmtDur(s){
  s = Number(s || 0); if (isNaN(s) || s < 0) { s = 0; }
  var h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60;
  return (h > 0 ? h + ':' : '') + pad2(m) + ':' + pad2(x);
}
function permLabel(p){
  if (p === 'control') return '允许操控';
  if (p === 'script') return '允许接收脚本';
  if (p === 'observe') return '仅观察';
  return '—';
}
function stateLabel(st){
  if (!st) { return '—'; }
  if (st.running) { return st.paused ? '已暂停' : '运行中'; }
  return '空闲';
}
async function jget(url){
  var r = await fetch(url, {cache: 'no-store', credentials: 'same-origin'});
  if (!r.ok) { throw new Error('HTTP ' + r.status); }
  return r.json();
}
function setBanner(show){
  document.getElementById('banner').style.display = show ? 'block' : 'none';
}
function addCell(tr, text, cls){
  var td = document.createElement('td');
  td.textContent = (text === null || text === undefined) ? '' : String(text);
  if (cls) { td.className = cls; }
  tr.appendChild(td);
}
function renderSelf(sum){
  var n = (sum && sum.node) || {};
  var info = n.info || {};
  var cnt = (sum && sum.counts) || {};
  document.getElementById('selfline').textContent =
    '本机：' + (info.name || '—') + ' ｜ 节点：' + (info.node_id || '—') +
    ' ｜ 连接设备：' + (cnt.online || 0) + '/' + (cnt.total || 0);
}
function render(peers){
  var tb = document.getElementById('tbody');
  tb.textContent = '';
  if (!peers || !peers.length) {
    var tr0 = document.createElement('tr');
    var td0 = document.createElement('td');
    td0.className = 'empty';
    td0.colSpan = 8;
    td0.textContent = '暂无已连接设备';
    tr0.appendChild(td0);
    tb.appendChild(tr0);
    return;
  }
  peers.forEach(function(p){
    var st = p.state || {};
    var sch = schedMap[p.node_id] || {};
    var tr = document.createElement('tr');
    tr.className = 'row';
    addCell(tr, p.name || p.node_id || '设备');
    addCell(tr, stateLabel(st), st.running ? 'run' : 'off');
    addCell(tr, st.script || '—');
    addCell(tr, (st.total_rows ? (st.row || 0) + '/' + st.total_rows : (st.row || 0)));
    addCell(tr, (st.total_loops ? (st.loop || 0) + '/' + st.total_loops : (st.loop || 0)));
    addCell(tr, fmtDur(st.elapsed));
    addCell(tr, sch.next_run || '—');
    addCell(tr, permLabel(p.perm));
    tr.onclick = function(){ toggleLog(p.node_id); };
    tb.appendChild(tr);
  });
}
async function toggleLog(nid){
  var box = document.getElementById('logbox');
  if (!nid) { return; }
  if (expanded === nid) { expanded = null; box.style.display = 'none'; return; }
  expanded = nid;
  box.style.display = 'block';
  document.getElementById('logtitle').textContent = '日志：' + nid;
  var pre = document.getElementById('loglines');
  pre.textContent = '加载中…';
  try {
    var d = await jget('/api/logs?node=' + encodeURIComponent(nid) + '&n=200');
    var lines = d.lines || [];
    var txt = lines.map(function(l){
      return (l.ts || '') + ' ' + (l.tag ? ('[' + l.tag + '] ') : '') + (l.msg || '');
    }).join('\\n');
    pre.textContent = txt || '(无日志)';
  } catch (e) {
    pre.textContent = '(读取失败)';
  }
}
async function refresh(){
  try {
    var sum = await jget('/api/summary');
    var peers = await jget('/api/peers');
    var sch = await jget('/api/sched');
    schedMap = sch.peers || {};
    renderSelf(sum);
    render(peers.peers || []);
    setBanner(false);
  } catch (e) {
    setBanner(true);
  }
}
refresh();
setInterval(refresh, 2000);
</script>
</body>
</html>
"""


# ══════════════════════════════════════════════════════════════════════════
# HTTP 服务
# ══════════════════════════════════════════════════════════════════════════
class _Server(http.server.ThreadingHTTPServer):
    """面板 HTTP 服务：每连接一线程（daemon），端口可复用。"""

    daemon_threads = True
    allow_reuse_address = True
    _server_address_ok = False

    def __init__(self, addr, handler, webui):
        self.webui = webui
        http.server.ThreadingHTTPServer.__init__(self, addr, handler)
        self._server_address_ok = True


class _Handler(http.server.BaseHTTPRequestHandler):
    """只读路由处理器：全部 GET；令牌鉴权三形式；失败 401。"""

    server_version = "ACRPA"      # 不暴露 Python 版本
    sys_version = ""
    protocol_version = "HTTP/1.1"

    # ── 请求日志：只落文件，不打印控制台 ──
    def log_message(self, fmt, *args):
        try:
            _nl_log("webui: {} {}".format(self.address_string(), fmt % args))
        except Exception:
            pass

    # ── 响应工具 ──
    def _send(self, code, body, ctype, set_cookie=None):
        try:
            data = body if isinstance(body, bytes) else str(body).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            if set_cookie:
                self.send_header(
                    "Set-Cookie",
                    "{}={}; HttpOnly; SameSite=Lax; Path=/".format(
                        COOKIE_NAME, set_cookie))
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:
            _nl_log("webui send error: {}".format(e), "WARN")

    def _send_text(self, code, text, set_cookie=None):
        self._send(code, text, "text/plain; charset=utf-8", set_cookie)

    def _send_json(self, obj, set_cookie=None):
        try:
            body = json.dumps(obj, ensure_ascii=False)
        except Exception:
            body = "{}"
        self._send(200, body, "application/json; charset=utf-8", set_cookie)

    def _send_html(self, html, set_cookie=None):
        self._send(200, html, "text/html; charset=utf-8", set_cookie)

    # ── 令牌来源 ──
    def _cookie_token(self):
        try:
            raw = self.headers.get("Cookie") or ""
        except Exception:
            return ""
        for part in raw.split(";"):
            k, _, v = part.strip().partition("=")
            if k == COOKIE_NAME:
                return v.strip()
        return ""

    def _bearer_token(self):
        try:
            h = self.headers.get("Authorization") or ""
        except Exception:
            return ""
        if h[:7].lower() == "bearer ":
            return h[7:].strip()
        return ""

    # ── 路由 ──
    def _handle(self):
        ui = getattr(self.server, "webui", None)
        if ui is None:
            self._send_text(500, "panel unavailable")
            return
        try:
            parsed = urllib.parse.urlsplit(self.path or "/")
        except Exception:
            self._send_text(400, "bad request")
            return
        path = parsed.path or "/"
        try:
            params = urllib.parse.parse_qs(parsed.query or "")
        except Exception:
            params = {}

        query_tok = (params.get("token") or [""])[0]
        cookie_tok = self._cookie_token()
        bearer_tok = self._bearer_token()
        expected = ui.token()

        authed = False
        set_cookie = None
        if query_tok and _const_eq(query_tok, expected):
            authed = True
            set_cookie = query_tok          # 有效 query 令牌 → 落 Cookie
        elif cookie_tok and _const_eq(cookie_tok, expected):
            authed = True
        elif bearer_tok and _const_eq(bearer_tok, expected):
            authed = True
        if not authed:
            self._send_text(401, "unauthorized")
            return

        if path == "/":
            self._send_html(ui.render_page(), set_cookie)
        elif path == "/api/summary":
            self._send_json(ui.api_summary(), set_cookie)
        elif path == "/api/peers":
            self._send_json(ui.api_peers(), set_cookie)
        elif path == "/api/state":
            self._send_json(ui.api_state(), set_cookie)
        elif path == "/api/logs":
            node_id = (params.get("node") or [""])[0]
            n = _clamp_n((params.get("n") or [DEFAULT_N])[0])
            self._send_json(ui.api_logs(node_id, n), set_cookie)
        elif path == "/api/sched":
            self._send_json(ui.api_sched(), set_cookie)
        elif path == "/api/status":
            self._send_json(ui.api_status(), set_cookie)
        else:
            self._send_text(404, "not found")

    def do_GET(self):
        try:
            self._handle()
        except Exception as e:
            _nl_log("webui handler error: {}".format(e), "ERROR")
            try:
                self._send_text(500, "internal error")
            except Exception:
                pass

    def _method_not_allowed(self):
        try:
            self._send_text(405, "method not allowed")
        except Exception:
            pass

    do_POST = _method_not_allowed
    do_PUT = _method_not_allowed
    do_DELETE = _method_not_allowed
    do_PATCH = _method_not_allowed
    do_HEAD = _method_not_allowed


# ══════════════════════════════════════════════════════════════════════════
# WebUI
# ══════════════════════════════════════════════════════════════════════════
class WebUI(object):
    """浏览器只读监控面板：bus 快照 + ThreadingHTTPServer + 令牌鉴权。"""

    def __init__(self, node, port=DEFAULT_PORT, bind="0.0.0.0"):
        self._node = node
        self._port = _safe_port(port)
        self._bind = str(bind or "0.0.0.0").strip() or "0.0.0.0"

        self._lock = threading.RLock()
        # ── 内部快照（订阅总线所得，绝不 drain）──
        self._peers = {}                 # node_id -> info
        self._states = {}                # node_id -> state payload（最新）
        self._logs = {}                  # node_id -> deque(maxlen=LOG_CAP)
        self._scheds = {}                # node_id -> sched payload
        self._status = None              # 最新一条 TOPIC_STATUS
        self._cmd_results = collections.deque(maxlen=50)
        self._transfers = collections.deque(maxlen=50)

        self._srv = None
        self._thread = None
        self._running = False
        self._token = ""

        self._subscribe()

    # ── 总线订阅 ──────────────────────────────────────────────────────
    def _bus(self):
        try:
            return self._node.bus if self._node is not None else None
        except Exception:
            return None

    def _subscribe(self):
        bus = self._bus()
        if bus is None:
            return
        for topic in _ALL_TOPICS:
            try:
                bus.subscribe(topic, self._on_event)
            except Exception:
                pass

    def _on_event(self, topic, payload):
        """总线回调（网络/Agent 线程）：仅更新内部快照，整体 try/except。"""
        try:
            if not isinstance(payload, dict):
                return
            if topic == TOPIC_PEER:
                nid = str(payload.get("node_id") or "")
                if nid:
                    with self._lock:
                        self._peers[nid] = dict(payload)
            elif topic == TOPIC_PEER_STATE:
                nid = str(payload.get("node_id") or "")
                if nid:
                    with self._lock:
                        self._states[nid] = dict(payload)
            elif topic == TOPIC_PEER_LOG:
                self._on_log(payload)
            elif topic == TOPIC_PEER_SCHED:
                nid = str(payload.get("node_id") or "")
                if nid:
                    with self._lock:
                        self._scheds[nid] = dict(payload)
            elif topic == TOPIC_STATUS:
                with self._lock:
                    self._status = dict(payload)
            elif topic == TOPIC_CMD_RESULT:
                with self._lock:
                    self._cmd_results.append(dict(payload))
            elif topic == TOPIC_TRANSFER:
                with self._lock:
                    self._transfers.append(dict(payload))
            # TOPIC_SCREENSHOT：面板本轮不展示截图，仅保持订阅关系
        except Exception as e:
            _nl_log("webui on_event error: {}".format(e), "WARN")

    def _on_log(self, payload):
        nid = str(payload.get("node_id") or "")
        if not nid:
            return
        lines = payload.get("lines") or []
        full = bool(payload.get("full"))
        with self._lock:
            dq = self._logs.get(nid)
            if dq is None:
                dq = collections.deque(maxlen=LOG_CAP)
                self._logs[nid] = dq
            if full:
                dq.clear()
            for ln in lines:
                dq.append(ln)

    # ── 生命周期 ──────────────────────────────────────────────────────
    def start(self):
        """绑定并启动服务；绑定失败返回 False，已启动返回 True。"""
        try:
            with self._lock:
                if self._running and self._srv is not None:
                    return True
            srv = _Server((self._bind, self._port), _Handler, self)
            th = threading.Thread(target=self._serve, name="nl-web")
            th.daemon = True
            self._subscribe()
            with self._lock:
                self._srv = srv
                self._thread = th
                self._running = True
            th.start()
            _nl_log("webui: started on {}:{}".format(self._bind, self._port))
            return True
        except Exception as e:
            _nl_log("webui start failed: {}".format(e), "ERROR")
            try:
                with self._lock:
                    self._srv = None
                    self._thread = None
                    self._running = False
            except Exception:
                pass
            return False

    def _serve(self):
        try:
            with self._lock:
                srv = self._srv
            if srv is None:
                return
            srv.serve_forever(poll_interval=0.5)
        except Exception as e:
            _nl_log("webui serve error: {}".format(e), "ERROR")

    def stop(self):
        """幂等停止：shutdown + server_close + 退订；端口可再次绑定。"""
        try:
            with self._lock:
                srv = self._srv
                th = self._thread
                self._srv = None
                self._thread = None
                self._running = False
            if srv is not None:
                try:
                    srv.shutdown()
                except Exception:
                    pass
                try:
                    srv.server_close()
                except Exception:
                    pass
            if th is not None and th is not threading.current_thread():
                try:
                    th.join(3.0)
                except Exception:
                    pass
            bus = self._bus()
            if bus is not None:
                try:
                    bus.unsubscribe_all(self._on_event)
                except Exception:
                    pass
            _nl_log("webui: stopped")
        except Exception as e:
            _nl_log("webui stop error: {}".format(e), "WARN")

    @property
    def running(self):
        with self._lock:
            return bool(self._running and self._srv is not None)

    # ── 对外信息 ──────────────────────────────────────────────────────
    def token(self):
        """当前令牌（不存在则生成并写入凭据库）。"""
        with self._lock:
            if not self._token:
                self._token = get_token()
            return self._token

    def rotate_token(self):
        """重新生成令牌（写凭据库）并返回新值。"""
        new = rotate_token()
        with self._lock:
            self._token = new
        return new

    def url(self):
        """访问地址：http://<host>:<port>/?token=xxxx。"""
        bind = self._bind
        if bind in ("127.0.0.1", "localhost"):
            host = "127.0.0.1"
        else:
            host = self._primary_ipv4() or "127.0.0.1"
        return "http://{}:{}/?token={}".format(host, self._port, self.token())

    def _primary_ipv4(self):
        try:
            return _local_ipv4()
        except Exception:
            return ""

    def render_page(self):
        return _PAGE

    # ── 数据访问（全部返回可 json 序列化的 dict；内部已加锁）──
    def _self_snapshot(self):
        try:
            if self._node is not None and hasattr(self._node, "snapshot"):
                snap = self._node.snapshot()
                if isinstance(snap, dict):
                    return snap
        except Exception:
            pass
        return {"info": {}, "state": {}, "sched": {}, "log": []}

    def _pairs(self):
        try:
            if self._node is not None and hasattr(self._node, "list_peers"):
                return list(self._node.list_peers() or [])
        except Exception:
            pass
        return []

    def api_summary(self):
        with self._lock:
            peers = list(self._peers.values())
        total = len(peers)
        online = sum(1 for p in peers if p.get("online", True))
        return {"node": self._self_snapshot(),
                "counts": {"total": total, "online": online}}

    def api_peers(self):
        pairs = self._pairs()
        perm_by_nid = {}
        for it in pairs:
            if not isinstance(it, dict):
                continue
            nid = str(it.get("node_id") or "")
            if nid:
                perm_by_nid[nid] = str(it.get("perm") or "")
        with self._lock:
            live = {k: dict(v) for k, v in self._peers.items()}
            states = {k: dict(v) for k, v in self._states.items()}
        out = []
        seen = set()
        for nid, info in live.items():
            entry = dict(info)
            entry["state"] = states.get(nid)
            entry["perm"] = perm_by_nid.get(nid)
            out.append(entry)
            seen.add(nid)
        # 白名单中仍未出现在实时 peers 的条目也纳入（权限已知）
        for it in pairs:
            if not isinstance(it, dict):
                continue
            nid = str(it.get("node_id") or "")
            if not nid or nid in seen:
                continue
            out.append({"node_id": nid, "name": str(it.get("name") or ""),
                        "host": "", "port": 0, "version": "", "online": False,
                        "state": states.get(nid),
                        "perm": str(it.get("perm") or "")})
            seen.add(nid)
        return {"peers": out}

    def api_state(self):
        with self._lock:
            states = {k: dict(v) for k, v in self._states.items()}
        return {"node": self._self_snapshot(), "peers": states}

    def api_logs(self, node_id=None, n=DEFAULT_N):
        n = _clamp_n(n)
        nid = str(node_id or "")
        if not nid:
            snap = self._self_snapshot()
            self_nid = str((snap.get("info") or {}).get("node_id") or "")
            lines = list(snap.get("log") or [])[-n:]
            return {"node_id": self_nid, "lines": lines}
        with self._lock:
            dq = self._logs.get(nid)
            lines = list(dq)[-n:] if dq is not None else []
        return {"node_id": nid, "lines": lines}

    def api_sched(self):
        snap = self._self_snapshot()
        with self._lock:
            scheds = {k: dict(v) for k, v in self._scheds.items()}
        return {"node": snap.get("sched") or {}, "peers": scheds}

    def api_status(self):
        with self._lock:
            status = dict(self._status) if isinstance(self._status, dict) else None
            cmds = [dict(x) for x in self._cmd_results]
            xfers = [dict(x) for x in self._transfers]
        return {"status": status, "cmd_results": cmds, "transfers": xfers}
