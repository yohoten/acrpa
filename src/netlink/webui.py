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
import hashlib
import hmac
import http.server
import io
import json
import os
import secrets
import socket
import threading
import time
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

# ── 控制会话 / CSRF（Phase4-2 批次2）──────────────────────────────────────
CTL_COOKIE = "nl_ctl"          # 控制会话 Cookie（HttpOnly; Secure; SameSite=Strict）
CSRF_HEADER = "X-NL-CSRF"      # CSRF 自定义头（令牌只存前端 JS 内存，不落盘）
DEFAULT_CONTROL_TTL = 300      # 控制会话默认 TTL（秒），滑动续期
MAX_SESSIONS = 8               # 控制会话表上限（超出淘汰最早创建者并审计）
MAX_SESSION_LIFE = 8 * 3600    # 控制会话绝对寿命上限（秒，超时强制重解锁）

# 网页面板有限控制白名单：仅 run / stop（pause/resume 属「未开放」）
_WEB_ACTIONS = ("run", "stop")

LOG_CAP = 300          # 每设备日志环形缓冲上限
MAX_N = 500            # /api/logs n 上限
DEFAULT_N = 200        # /api/logs n 默认值

_ALL_TOPICS = (TOPIC_PEER, TOPIC_PEER_STATE, TOPIC_PEER_LOG, TOPIC_PEER_SCHED,
               TOPIC_STATUS, TOPIC_CMD_RESULT, TOPIC_TRANSFER, TOPIC_SCREENSHOT)

_TOKEN_LOCK = threading.RLock()

# ── PWA（批次3）：manifest / Service Worker / 离线页 / 图标 ───────────────
CACHE_VERSION = "acrpa-nl-web-v1"   # 每次发版递增；SW activate 删除不匹配的旧缓存
MANIFEST_THEME = "#1e293b"
MANIFEST_BG = "#0f172a"

# 免 token 静态端点（仅返回无数据/无密钥资源；见设计文档 §3.7）。
# 值为 (Content-Type, Cache-Control)。
_STATIC_ASSETS = {
    "/manifest.webmanifest": ("application/manifest+json", "no-cache"),
    "/sw.js": ("application/javascript", "no-cache"),
    "/offline.html": ("text/html; charset=utf-8", "no-cache"),
    "/favicon.ico": ("image/x-icon", "public, max-age=86400"),
    "/icons/icon-192.png": ("image/png", "public, max-age=86400"),
    "/icons/icon-512.png": ("image/png", "public, max-age=86400"),
    "/icons/apple-touch-icon.png": ("image/png", "public, max-age=86400"),
}


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


def _safe_ttl(v):
    """控制会话 TTL 夹取到 [60, 3600]；非法/缺省回退 DEFAULT_CONTROL_TTL。"""
    try:
        n = int(v)
    except Exception:
        return DEFAULT_CONTROL_TTL
    if n <= 0:
        return DEFAULT_CONTROL_TTL
    if n < 60:
        n = 60
    if n > 3600:
        n = 3600
    return n


def _looks_like_pin(v):
    """PIN 规格 ^\\d{6}$（与 security._is_valid_pin 保持一致）。"""
    try:
        s = str(v or "").strip()
        return len(s) == 6 and all(c in "0123456789" for c in s)
    except Exception:
        return False


def _ctl_cookie(value, max_age):
    """构造 nl_ctl 控制会话 Cookie（HttpOnly; Secure; SameSite=Strict）。"""
    return "{}={}; HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age={}".format(
        CTL_COOKIE, value or "", int(max_age))


def _ua_hash(ua):
    """User-Agent 摘要（仅用于告警，不作硬门槛）；失败返回空串。"""
    try:
        return hashlib.sha256(str(ua or "").encode("utf-8")).hexdigest()[:12]
    except Exception:
        return ""


def _sec():
    """懒 import security（PIN 门禁 / 锁定状态）；失败返回 None。"""
    try:
        from . import security
        return security
    except Exception:
        return None


def _reject_code(detail):
    """把 submit_local 的 rejected detail 映射到 HTTP 状态码（稳定英文）。"""
    d = str(detail or "").strip().lower()
    if d == "execution timeout":
        return 504
    if d in ("already running", "recording in progress", "no script selected"):
        return 409
    if d == "action not allowed":
        return 400
    return 409


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
# PWA 资源：manifest / Service Worker / 离线页 / 图标（批次3）
#   设计约束：纯标准库 + 已打包 PIL（仅 Image.resize，不依赖被 exclude 的
#   ImageFont）；所有函数整体 try/except，失败只回退、绝不向上抛。
# ══════════════════════════════════════════════════════════════════════════
_MANIFEST = {
    "name": "ACRPA 监控面板",
    "short_name": "ACRPA",
    "description": "ACRPA NetLink 运行状态监控与有限控制面板",
    "lang": "zh-CN",
    "start_url": "/?src=pwa",
    "scope": "/",
    "display": "standalone",
    "orientation": "any",
    "theme_color": MANIFEST_THEME,
    "background_color": MANIFEST_BG,
    "icons": [
        {"src": "/icons/icon-192.png", "sizes": "192x192",
         "type": "image/png", "purpose": "any"},
        {"src": "/icons/icon-512.png", "sizes": "512x512",
         "type": "image/png", "purpose": "any"},
        {"src": "/icons/icon-512.png", "sizes": "512x512",
         "type": "image/png", "purpose": "maskable"},
    ],
}

try:
    _MANIFEST_JSON = json.dumps(_MANIFEST, ensure_ascii=False, indent=2)
except Exception:      # pragma: no cover
    _MANIFEST_JSON = "{}"


_SW_JS = """/* ACRPA NetLink web panel service worker (Phase4-2) */
var CACHE_VERSION = 'acrpa-nl-web-v1';
var PRECACHE = ['/manifest.webmanifest','/offline.html','/icons/icon-192.png',
  '/icons/icon-512.png','/icons/apple-touch-icon.png','/favicon.ico'];
self.addEventListener('install', function(e){
  e.waitUntil(
    caches.open(CACHE_VERSION)
      .then(function(c){ return c.addAll(PRECACHE); })
      .catch(function(){})
      .then(function(){ return self.skipWaiting(); })
  );
});
self.addEventListener('activate', function(e){
  e.waitUntil(
    caches.keys().then(function(keys){
      return Promise.all(keys.map(function(k){
        if (k !== CACHE_VERSION) { return caches.delete(k); }
      }));
    }).then(function(){ return self.clients.claim(); })
  );
});
function networkOnly(req){
  return fetch(req).catch(function(){
    return new Response(JSON.stringify({ok:false,error:'offline'}),
      {status:503, headers:{'Content-Type':'application/json; charset=utf-8'}});
  });
}
function navFirst(req){
  return fetch(req).catch(function(){
    return caches.match('/offline.html').then(function(r){
      return r || new Response('offline', {status:503});
    });
  });
}
function cacheFirst(req){
  return caches.match(req).then(function(hit){
    if (hit) {
      fetch(req).then(function(r){
        try { caches.open(CACHE_VERSION).then(function(c){ c.put(req, r.clone()); }); }
        catch (ex) {}
      }).catch(function(){});
      return hit;
    }
    return fetch(req).then(function(r){
      try { var cl = r.clone(); caches.open(CACHE_VERSION).then(function(c){ c.put(req, cl); }); }
      catch (ex) {}
      return r;
    }).catch(function(){ return caches.match('/offline.html'); });
  });
}
self.addEventListener('fetch', function(e){
  var req = e.request;
  if (req.method !== 'GET') { return; }
  var url;
  try { url = new URL(req.url); } catch (ex) { return; }
  if (url.origin !== location.origin) { return; }
  var p = url.pathname;
  if (p.indexOf('/api/') === 0) { e.respondWith(networkOnly(req)); return; }
  if (p === '/sw.js') { e.respondWith(networkOnly(req)); return; }
  if (req.mode === 'navigate') { e.respondWith(navFirst(req)); return; }
  e.respondWith(cacheFirst(req));
});
"""


_OFFLINE_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ACRPA 监控面板（离线）</title>
<meta name="theme-color" content="#1e293b">
<style>
 body{font-family:-apple-system,"Segoe UI",Roboto,"Microsoft YaHei",sans-serif;margin:0;background:#0f172a;color:#e2e8f0;}
 header{padding:12px 16px;background:#1e293b;}
 h1{font-size:16px;margin:0 0 4px;}
 .sub{font-size:12px;color:#94a3b8;}
 .wrap{padding:12px 16px;}
 table{width:100%;border-collapse:collapse;font-size:13px;}
 th,td{text-align:left;padding:6px 8px;border-bottom:1px solid #1e293b;}
 th{color:#94a3b8;font-weight:600;white-space:nowrap;}
 #banner{background:#7f1d1d;color:#fff;padding:8px 16px;font-size:13px;}
 button{font-size:13px;padding:5px 12px;border-radius:4px;border:1px solid #334155;background:#1e293b;color:#e2e8f0;cursor:pointer;margin-bottom:10px;}
 .empty{color:#64748b;padding:10px 8px;}
 .off{color:#64748b;}
 .run{color:#34d399;}
</style>
</head>
<body>
<div id="banner">⚠ 离线模式：显示最后快照（<span id="ago">—</span>）</div>
<header>
  <h1>ACRPA 监控面板（离线）</h1>
  <div class="sub" id="selfline">本机：— ｜ 连接设备：0</div>
</header>
<div class="wrap">
  <button id="retry" type="button">重试</button>
  <table>
    <thead><tr>
      <th>设备</th><th>状态</th><th>脚本</th><th>进度</th><th>循环</th><th>已运行</th>
    </tr></thead>
    <tbody id="tbody"></tbody>
  </table>
</div>
<script>
function pad2(n){ return (n < 10 ? '0' : '') + n; }
function fmtDur(s){
  s = Number(s || 0); if (isNaN(s) || s < 0) { s = 0; }
  var h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60;
  return (h > 0 ? h + ':' : '') + pad2(m) + ':' + pad2(x);
}
function stateLabel(st){
  if (!st) { return '—'; }
  if (st.running) { return st.paused ? '已暂停' : '运行中'; }
  return '空闲';
}
function addCell(tr, text, cls){
  var td = document.createElement('td');
  td.textContent = (text === null || text === undefined) ? '' : String(text);
  if (cls) { td.className = cls; }
  tr.appendChild(td);
}
function agoText(ts){
  try {
    var d = Math.floor(Date.now() / 1000) - Number(ts || 0);
    if (isNaN(d) || d < 0) { d = 0; }
    if (d < 60) { return d + ' 秒前'; }
    if (d < 3600) { return Math.floor(d / 60) + ' 分钟前'; }
    return Math.floor(d / 3600) + ' 小时前';
  } catch (e) { return '未知'; }
}
function render(){
  var snap = null;
  try { snap = JSON.parse(localStorage.getItem('acrpa.snap.v1') || 'null'); } catch (e) { snap = null; }
  var tb = document.getElementById('tbody');
  tb.textContent = '';
  if (!snap) {
    var tr0 = document.createElement('tr');
    var td0 = document.createElement('td');
    td0.className = 'empty';
    td0.colSpan = 6;
    td0.textContent = '暂无离线快照（需至少在线成功加载一次）';
    tr0.appendChild(td0);
    tb.appendChild(tr0);
    return;
  }
  document.getElementById('ago').textContent = agoText(snap.ts);
  var sum = snap.summary || {};
  var info = (sum.node || {}).info || {};
  var cnt = sum.counts || {};
  document.getElementById('selfline').textContent =
    '本机：' + (info.name || '—') + ' ｜ 连接设备：' + (cnt.online || 0) + '/' + (cnt.total || 0);
  var peers = snap.peers || [];
  if (!peers.length) {
    var tr1 = document.createElement('tr');
    var td1 = document.createElement('td');
    td1.className = 'empty';
    td1.colSpan = 6;
    td1.textContent = '暂无已连接设备';
    tr1.appendChild(td1);
    tb.appendChild(tr1);
    return;
  }
  peers.forEach(function(p){
    var st = p.state || {};
    var tr = document.createElement('tr');
    addCell(tr, p.name || p.node_id || '设备');
    addCell(tr, stateLabel(st), st.running ? 'run' : 'off');
    addCell(tr, st.script || '—');
    addCell(tr, (st.total_rows ? (st.row || 0) + '/' + st.total_rows : (st.row || 0)));
    addCell(tr, (st.total_loops ? (st.loop || 0) + '/' + st.total_loops : (st.loop || 0)));
    addCell(tr, fmtDur(st.elapsed));
    tb.appendChild(tr);
  });
}
var rb = document.getElementById('retry');
if (rb) { rb.addEventListener('click', function(){ location.replace('/'); }); }
render();
</script>
</body>
</html>
"""


_RES_DIR_CACHE = None
_ICON_CACHE = {}
_ICON_LOCK = threading.RLock()


def _res_dir():
    """定位 res 目录（源码运行 / PyInstaller 打包 / 当前工作目录三种情形）。"""
    global _RES_DIR_CACHE
    if _RES_DIR_CACHE is not None:
        return _RES_DIR_CACHE
    cands = []
    try:
        base = os.path.dirname(os.path.abspath(__file__))   # src/netlink
        cands.append(os.path.join(os.path.dirname(os.path.dirname(base)), "res"))
    except Exception:
        pass
    try:
        import sys
        mp = getattr(sys, "_MEIPASS", "")
        if mp:
            cands.append(os.path.join(mp, "res"))
    except Exception:
        pass
    try:
        cands.append(os.path.join(os.getcwd(), "res"))
    except Exception:
        pass
    for c in cands:
        try:
            if c and os.path.isdir(c):
                _RES_DIR_CACHE = c
                return c
        except Exception:
            pass
    _RES_DIR_CACHE = cands[0] if cands else "res"
    return _RES_DIR_CACHE


def _asset_bytes(name):
    """读取 res/<name> 的原始字节；缺失/失败返回 b''。"""
    try:
        p = os.path.join(_res_dir(), str(name))
        with open(p, "rb") as f:
            return f.read()
    except Exception:
        return b""


def _scale_icon(src_name, size):
    """用 PIL 把 res/<src_name> 缩放到 size×size PNG；失败返回 b''（不抛）。"""
    try:
        from PIL import Image
    except Exception:
        return b""
    try:
        p = os.path.join(_res_dir(), str(src_name))
        im = Image.open(p).convert("RGBA")
        im = im.resize((int(size), int(size)), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        return b""


def icon_bytes(name, size=0):
    """返回图标字节（带内存缓存）。

    优先级：res/<name> 原文 → PIL 从 res/icon.png 缩放 → res/icon.png 原文 → b''。
    **绝不抛异常**；打包环境 PIL 可用（仅用 Image.resize）。
    """
    key = (str(name), int(size or 0))
    with _ICON_LOCK:
        hit = _ICON_CACHE.get(key)
    if hit is not None:
        return hit
    data = _asset_bytes(name)
    if not data and size:
        data = _scale_icon("icon.png", int(size))
    if not data:
        data = _asset_bytes("icon.png")
    if data is None:
        data = b""
    with _ICON_LOCK:
        _ICON_CACHE[key] = data
    return data


# ══════════════════════════════════════════════════════════════════════════
# 单页 HTML（内联 CSS/JS；无外部资源、无 CDN；JS 端 textContent 赋值防 XSS）
# ══════════════════════════════════════════════════════════════════════════
_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ACRPA 监控面板</title>
<link rel="manifest" href="/manifest.webmanifest">
<link rel="icon" href="/favicon.ico">
<link rel="apple-touch-icon" href="/icons/apple-touch-icon.png">
<meta name="theme-color" content="#1e293b">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="ACRPA">
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
 #ctlbar{display:none;background:#111c33;border:1px solid #1e293b;border-radius:6px;padding:10px 12px;margin-bottom:12px;}
 .ctlrow{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:6px;}
 .ctlrow:last-child{margin-bottom:0;}
 #ctlbar button{font-size:13px;padding:5px 12px;border-radius:4px;border:1px solid #334155;background:#1e293b;color:#e2e8f0;cursor:pointer;}
 #ctlbar button:disabled{opacity:.45;cursor:default;}
 #ctlbar input{font-size:14px;padding:5px 8px;width:150px;border-radius:4px;border:1px solid #334155;background:#020617;color:#e2e8f0;letter-spacing:2px;}
 #pwa{margin-bottom:10px;}
 #pwa button{font-size:12px;padding:5px 10px;border-radius:4px;border:1px solid #334155;background:#1e293b;color:#e2e8f0;cursor:pointer;}
</style>
</head>
<body>
<div id="banner">连接已断开</div>
<header>
  <h1>ACRPA 只读监控面板</h1>
  <div class="sub" id="selfline">本机：— ｜ 节点：— ｜ 连接设备：0</div>
</header>
<div class="wrap">
  <div id="pwa">
    <button id="installbtn" type="button" style="display:none;">安装到主屏</button>
    <span class="sub" id="pwainfo" style="display:none;"></span>
  </div>
  <div id="ctlbar">
    <div class="ctlrow">
      <span id="ctlstate" class="off">控制：—</span>
      <span class="sub" id="ctlmeta"></span>
    </div>
    <div class="ctlrow" id="ctlpinrow">
      <input id="ctlpinin" type="password" inputmode="numeric" autocomplete="off" maxlength="6" placeholder="6 位控制 PIN">
      <button id="ctlunlock" type="button">解锁</button>
      <button id="ctllock" type="button" disabled>锁定</button>
    </div>
    <div class="ctlrow">
      <button id="ctlrun" type="button" disabled>运行当前脚本</button>
      <button id="ctlstop" type="button" disabled>停止</button>
    </div>
    <div class="ctlrow"><span id="ctlmsg" class="sub" aria-live="polite"></span></div>
  </div>
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
var ctlCsrf = "";
var ctlEnabled = false;
var ctlUnlocked = false;
var ctlExpires = 0;
var ctlRunning = false;
var ctlHasScript = false;
var ctlPinLocked = false;
var ctlSeen = false;
var ctlTick = 0;
var deferredPrompt = null;
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
  var st = n.state || {};
  var cnt = (sum && sum.counts) || {};
  var rt = st.running ? (st.paused ? '已暂停' : '运行中') : '空闲';
  document.getElementById('selfline').textContent =
    '本机：' + (info.name || '—') + ' ｜ 运行：' + rt +
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
// ── 控制区（有限控制：仅 run / stop；CSRF 只存 JS 内存，不落盘）──
function actLabel(a){
  if (a === 'run') { return '运行'; }
  if (a === 'stop') { return '停止'; }
  return a;
}
function ctlMsg(t, cls){
  var el = document.getElementById('ctlmsg');
  if (!el) { return; }
  el.textContent = (t === null || t === undefined) ? '' : String(t);
  el.className = cls || 'sub';
}
function ctlButtons(){
  var run = document.getElementById('ctlrun');
  var stop = document.getElementById('ctlstop');
  var lk = document.getElementById('ctllock');
  var canRun = ctlEnabled && ctlUnlocked && !ctlRunning && ctlHasScript;
  var canStop = ctlEnabled && ctlUnlocked && ctlRunning;
  if (run) { run.disabled = !canRun; run.setAttribute('aria-disabled', canRun ? 'false' : 'true'); }
  if (stop) { stop.disabled = !canStop; stop.setAttribute('aria-disabled', canStop ? 'false' : 'true'); }
  if (lk) { lk.disabled = !ctlUnlocked; lk.setAttribute('aria-disabled', ctlUnlocked ? 'false' : 'true'); }
}
function ctlApplyStatus(j){
  try {
    ctlEnabled = !!(j && j.enabled);
    var bar = document.getElementById('ctlbar');
    if (bar) { bar.style.display = ctlEnabled ? 'block' : 'none'; }
    if (!ctlEnabled) { ctlButtons(); return; }
    ctlUnlocked = !!(j.unlocked);
    ctlExpires = Number(j.expires_in || 0);
    ctlPinLocked = !!(j.pin_locked);
    ctlRunning = !!(j.running);
    ctlHasScript = !!(j.has_script);
    var st = document.getElementById('ctlstate');
    if (st) {
      if (ctlPinLocked) {
        st.textContent = '控制：PIN 已锁定（重试 ' + String(j.retry_after || 0) + 's）';
        st.className = 'off';
      } else if (ctlUnlocked) {
        st.textContent = '控制：已解锁（剩余 ' + fmtDur(ctlExpires) + '）';
        st.className = 'run';
      } else {
        st.textContent = '控制：已锁定（需 PIN 解锁）';
        st.className = 'off';
      }
    }
    var meta = document.getElementById('ctlmeta');
    if (meta) {
      meta.textContent = (j.scheme === 'https' ? 'HTTPS' : 'HTTP') +
        ' ｜ 会话 TTL ' + String(j.ttl || 0) + 's' +
        ' ｜ 运行：' + (ctlRunning ? '运行中' : '空闲');
    }
    ctlButtons();
  } catch (e) {}
}
async function ctlRefreshStatus(){
  try { ctlApplyStatus(await jget('/api/control/status')); } catch (e) {}
}
async function ctlPost(path, body, useCsrf){
  var hdr = {'Content-Type': 'application/json'};
  if (useCsrf && ctlCsrf) { hdr['X-NL-CSRF'] = ctlCsrf; }
  var r = await fetch(path, {method: 'POST', cache: 'no-store',
    credentials: 'same-origin', headers: hdr, body: JSON.stringify(body || {})});
  var j = null;
  try { j = await r.json(); } catch (e) { j = null; }
  return {code: r.status, body: j};
}
async function ctlUnlock(){
  var el = document.getElementById('ctlpinin');
  var pin = (el && el.value ? String(el.value) : '').trim();
  if (!/^[0-9]{6}$/.test(pin)) { ctlMsg('请输入 6 位数字 PIN', 'off'); return; }
  ctlMsg('解锁中…');
  var res = await ctlPost('/api/control/unlock', {pin: pin}, false);
  var b = res.body || {};
  if (res.code === 200 && b.ok) {
    ctlCsrf = String(b.csrf || '');
    if (el) { el.value = ''; }
    ctlMsg('已解锁', 'run');
  } else if (res.code === 429) {
    ctlMsg('PIN 已锁定，请稍后重试', 'off');
  } else {
    ctlMsg('解锁失败：' + String(b.error || ('HTTP ' + res.code)), 'off');
  }
  await ctlRefreshStatus();
}
async function ctlLock(){
  ctlMsg('已锁定', 'off');
  try { await ctlPost('/api/control/lock', {}, false); } catch (e) {}
  ctlCsrf = '';
  await ctlRefreshStatus();
}
async function ctlAct(action){
  ctlMsg(actLabel(action) + '执行中…');
  var res = await ctlPost('/api/control', {action: action}, true);
  var b = res.body || {};
  if (b.status === 'done') {
    ctlMsg(actLabel(action) + '完成：' + String(b.detail || 'done'), 'run');
  } else if (b.status === 'failed') {
    ctlMsg(actLabel(action) + '失败：' + String(b.detail || ''), 'off');
  } else {
    ctlMsg(actLabel(action) + '被拒：' + String(b.error || b.detail || ('HTTP ' + res.code)), 'off');
  }
  await ctlRefreshStatus();
}
function saveSnap(sum, peers, sch){
  try {
    var snap = {ts: Math.floor(Date.now() / 1000),
      summary: {node: (sum && sum.node) || {}, counts: (sum && sum.counts) || {}},
      peers: (peers && peers.peers) || [], sched: (sch && sch.peers) || {}};
    var s = JSON.stringify(snap);
    if (s.length > 200000) { snap.peers = []; s = JSON.stringify(snap); }
    localStorage.setItem('acrpa.snap.v1', s);
  } catch (e) {}
}
// ── PWA（安装按钮 / 非安全上下文提示）──
function pwaHint(show, text){
  var el = document.getElementById('pwainfo');
  if (!el) { return; }
  el.textContent = text || '';
  el.style.display = show ? 'inline' : 'none';
}
function pwaInit(){
  var b = document.getElementById('installbtn');
  if (b) {
    b.addEventListener('click', function(){
      if (!deferredPrompt) { return; }
      try { deferredPrompt.prompt(); } catch (e) {}
      deferredPrompt = null;
      b.style.display = 'none';
    });
  }
  if (!window.isSecureContext) {
    if (b) { b.style.display = 'none'; }
    var host = location.hostname || '';
    if (host !== 'localhost' && host !== '127.0.0.1') {
      pwaHint(true, '安装与离线需通过 HTTPS 访问（请在设置中启用面板 TLS）');
    }
  }
}
function ctlInit(){
  var u = document.getElementById('ctlunlock');
  var l = document.getElementById('ctllock');
  var r = document.getElementById('ctlrun');
  var s = document.getElementById('ctlstop');
  var p = document.getElementById('ctlpinin');
  if (u) { u.addEventListener('click', function(){ ctlUnlock(); }); }
  if (l) { l.addEventListener('click', function(){ ctlLock(); }); }
  if (r) { r.addEventListener('click', function(){ ctlAct('run'); }); }
  if (s) { s.addEventListener('click', function(){ ctlAct('stop'); }); }
  if (p) { p.addEventListener('keydown', function(ev){ if (ev.key === 'Enter') { ctlUnlock(); } }); }
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
    saveSnap(sum, peers, sch);
    ctlTick = ctlTick + 1;
    if (!ctlSeen) { ctlSeen = true; await ctlRefreshStatus(); }
    else if (ctlEnabled) { if ((ctlTick % 2) === 0) { await ctlRefreshStatus(); } }
    else if ((ctlTick % 15) === 0) { await ctlRefreshStatus(); }
  } catch (e) {
    setBanner(true);
  }
}
window.addEventListener('beforeinstallprompt', function(e){
  try { e.preventDefault(); } catch (x) {}
  deferredPrompt = e;
  var b = document.getElementById('installbtn');
  if (b) { b.style.display = 'inline-block'; }
});
window.addEventListener('appinstalled', function(){
  deferredPrompt = null;
  var b = document.getElementById('installbtn');
  if (b) { b.style.display = 'none'; }
});
ctlInit();
pwaInit();
refresh();
setInterval(refresh, 2000);
setInterval(function(){
  if (ctlUnlocked && ctlExpires > 0) {
    ctlExpires = ctlExpires - 1;
    if (ctlExpires <= 0) { ctlUnlocked = false; ctlExpires = 0; ctlButtons(); }
    var st = document.getElementById('ctlstate');
    if (st && ctlUnlocked) { st.textContent = '控制：已解锁（剩余 ' + fmtDur(ctlExpires) + '）'; }
  }
}, 1000);
if ('serviceWorker' in navigator && window.isSecureContext) {
  window.addEventListener('load', function(){
    try { navigator.serviceWorker.register('/sw.js').catch(function(){}); } catch (e) {}
  });
}
</script>
</body>
</html>
"""


# ══════════════════════════════════════════════════════════════════════════
# 控制会话表（进程内、不落盘；滑动续期 + 绝对寿命 + 容量淘汰）
# ══════════════════════════════════════════════════════════════════════════
class _ControlSessionStore(object):
    """网页面板控制会话表：sid -> {csrf, created, exp, remote, ua_hash, last}。

    规则（对应设计文档 §1.2）：
      * TTL 滑动续期：通过 CSRF 校验的控制写请求顺延 exp（上限 created+MAX_SESSION_LIFE）；
      * 绝对上限 MAX_SESSION_LIFE（8h）到点强制失效；
      * 容量 MAX_SESSIONS（8）：超出淘汰最早 created 的会话并审计 reason=evicted；
      * 不绑定 IP 为硬门槛：IP 变化只审计 WEB_SESSION_IP_CHANGED 后放行；
      * **仅内存态**：面板重启即全部失效（可接受且期望）。
    """

    def __init__(self, ttl=DEFAULT_CONTROL_TTL, max_sessions=MAX_SESSIONS,
                 max_life=MAX_SESSION_LIFE, audit=None):
        self._ttl = _safe_ttl(ttl)
        try:
            self._max = max(1, int(max_sessions))
        except Exception:
            self._max = MAX_SESSIONS
        try:
            self._max_life = max(60, int(max_life))
        except Exception:
            self._max_life = MAX_SESSION_LIFE
        self._audit = audit
        self._lock = threading.RLock()
        self._sessions = {}

    def _event(self, cmd, result, detail):
        try:
            if self._audit is not None:
                self._audit(cmd, result, detail)
        except Exception:
            pass

    def _prune(self, now):
        dead = []
        for sid, s in self._sessions.items():
            try:
                if now >= float(s.get("exp") or 0) or \
                        now >= float(s.get("created") or 0) + self._max_life:
                    dead.append(sid)
            except Exception:
                dead.append(sid)
        for sid in dead:
            self._sessions.pop(sid, None)

    def create(self, remote="", ua=""):
        """新建会话 → (sid, csrf, expires_in)；超出容量时淘汰最早者。"""
        with self._lock:
            now = time.time()
            self._prune(now)
            while len(self._sessions) >= self._max and self._sessions:
                try:
                    oldest = min(self._sessions.items(),
                                 key=lambda kv: kv[1].get("created") or 0)[0]
                except Exception:
                    oldest = next(iter(self._sessions))
                self._sessions.pop(oldest, None)
                self._event("WEB_LOCK", "ok", "reason=evicted")
            sid = secrets.token_urlsafe(32)
            csrf = secrets.token_urlsafe(32)
            self._sessions[sid] = {
                "csrf": csrf, "created": now, "exp": now + self._ttl,
                "remote": str(remote or ""), "ua_hash": _ua_hash(ua),
                "last": None,
            }
            return sid, csrf, self._ttl

    def get(self, sid, touch=False, remote=""):
        """取会话（可选滑动续期）；不存在/过期返回 None。"""
        if not sid:
            return None
        with self._lock:
            now = time.time()
            self._prune(now)
            s = self._sessions.get(sid)
            if s is None:
                return None
            if remote and s.get("remote") and remote != s.get("remote"):
                self._event("WEB_SESSION_IP_CHANGED", "warn",
                            "old={} new={}".format(s.get("remote"), remote))
            if touch:
                new_exp = min(now + self._ttl,
                              float(s.get("created") or now) + self._max_life)
                if new_exp > float(s.get("exp") or 0):
                    s["exp"] = new_exp
            return dict(s)

    def touch(self, sid, remote=""):
        """滑动续期（仅成功通过全部校验的控制写请求调用）。"""
        return self.get(sid, touch=True, remote=remote)

    def set_last(self, sid, action, result):
        """记录最近一次控制动作（供 /api/control/status 展示）。"""
        with self._lock:
            s = self._sessions.get(sid)
            if s is not None:
                s["last"] = {"action": str(action or ""),
                             "result": str(result or ""),
                             "ts": int(time.time())}

    def lock(self, sid, reason="user", remote=""):
        """失效单个会话；sid 缺失/不存在时为空操作（幂等）。"""
        with self._lock:
            if sid:
                self._sessions.pop(sid, None)
        return True

    def lock_all(self, reason=""):
        """清空全部会话（PIN 变更/清除钩子 + 面板停用）。返回 True。"""
        with self._lock:
            self._sessions.clear()
        return True

    def count(self):
        with self._lock:
            return len(self._sessions)


# ══════════════════════════════════════════════════════════════════════════
# HTTP 服务
# ══════════════════════════════════════════════════════════════════════════
class _Server(http.server.ThreadingHTTPServer):
    """面板 HTTP 服务：每连接一线程（daemon），端口可复用。

    `ssl_ctx`：控制/HTTPS 时由 WebUI.start() 注入；None 表示明文。
    TLS 握手在 `_Handler.setup()` 内逐连接进行（不阻塞 accept 主循环）。
    """

    daemon_threads = True
    allow_reuse_address = True
    _server_address_ok = False
    ssl_ctx = None

    def __init__(self, addr, handler, webui):
        self.webui = webui
        http.server.ThreadingHTTPServer.__init__(self, addr, handler)
        self._server_address_ok = True


class _Handler(http.server.BaseHTTPRequestHandler):
    """路由处理器：GET 只读 + （控制启用时）POST 有限控制；令牌三形式鉴权。

    TLS 落地点：`setup()` 内逐连接 wrap_socket（握手发生在每连接工作线程，
    不阻塞 serve_forever 的 accept 主循环，避免半开握手 DoS）。
    """

    server_version = "ACRPA"      # 不暴露 Python 版本
    sys_version = ""
    protocol_version = "HTTP/1.1"

    # ── TLS：逐连接握手包裹 ────────────────────────────────────────────
    def setup(self):
        self._tls_failed = False
        ctx = getattr(self.server, "ssl_ctx", None)
        if ctx is not None:
            try:
                self.request = ctx.wrap_socket(
                    self.request, server_side=True,
                    do_handshake_on_connect=True)
            except Exception:
                # 握手失败：丢弃该连接，不影响 accept 主循环与其它连接
                try:
                    self.request.close()
                except Exception:
                    pass
                self._tls_failed = True
                return
        http.server.BaseHTTPRequestHandler.setup(self)

    def handle(self):
        if getattr(self, "_tls_failed", False):
            return
        http.server.BaseHTTPRequestHandler.handle(self)

    def finish(self):
        if getattr(self, "_tls_failed", False):
            try:
                self.request.close()
            except Exception:
                pass
            return
        try:
            http.server.BaseHTTPRequestHandler.finish(self)
        except Exception:
            pass

    def _is_https(self):
        """当前连接是否为已建立 TLS 的 SSLSocket（明文拒绝的第二层防御）。"""
        if getattr(self.server, "ssl_ctx", None) is None:
            return False
        try:
            import ssl
            return isinstance(self.request, ssl.SSLSocket)
        except Exception:
            return False

    # ── 免 token 静态资源（PWA：manifest / sw / offline / icons / favicon）──
    def _serve_static(self, ui, path):
        """服务 PWA 静态资源（无数据/无密钥）；路径不在白名单 → 404。"""
        meta = _STATIC_ASSETS.get(path)
        if meta is None:
            self._send_text(404, "not found")
            return
        ctype, cache = meta
        extra = (("Service-Worker-Allowed", "/"),) if path == "/sw.js" else None
        data = None
        try:
            data = ui.static_asset(path)
        except Exception as e:
            _nl_log("webui static asset error: {}".format(e), "WARN")
            data = None
        if data is None:
            data = b""
        self._send(200, data, ctype, extra=extra, cache=cache)

    # ── 请求日志：只落文件，不打印控制台 ──
    def log_message(self, fmt, *args):
        try:
            _nl_log("webui: {} {}".format(self.address_string(), fmt % args))
        except Exception:
            pass

    # ── 响应工具 ──
    def _send(self, code, body, ctype, set_cookie=None, extra=None, cache=None):
        try:
            data = body if isinstance(body, bytes) else str(body).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", cache or "no-store")
            if set_cookie:
                self.send_header(
                    "Set-Cookie",
                    "{}={}; HttpOnly; SameSite=Lax; Path=/".format(
                        COOKIE_NAME, set_cookie))
            for k, v in (extra or ()):
                try:
                    self.send_header(k, v)
                except Exception:
                    pass
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:
            _nl_log("webui send error: {}".format(e), "WARN")

    def _send_text(self, code, text, set_cookie=None):
        self._send(code, text, "text/plain; charset=utf-8", set_cookie)

    def _send_json(self, obj, set_cookie=None, code=200, extra=None):
        try:
            body = json.dumps(obj, ensure_ascii=False)
        except Exception:
            body = "{}"
        self._send(code, body, "application/json; charset=utf-8",
                   set_cookie, extra)

    def _send_html(self, html, set_cookie=None):
        self._send(200, html, "text/html; charset=utf-8", set_cookie)

    def _send_err(self, code, error, detail="", extra=None):
        """控制端点统一错误体：{"ok":false,"error":...,"detail":...}。"""
        self._send_json({"ok": False, "error": str(error or ""),
                         "detail": str(detail or error or "")},
                        code=code, extra=extra)

    # ── 令牌/Cookie 来源 ──
    def _cookie_value(self, name):
        try:
            raw = self.headers.get("Cookie") or ""
        except Exception:
            return ""
        for part in raw.split(";"):
            k, _, v = part.strip().partition("=")
            if k == name:
                return v.strip()
        return ""

    def _cookie_token(self):
        return self._cookie_value(COOKIE_NAME)

    def _bearer_token(self):
        try:
            h = self.headers.get("Authorization") or ""
        except Exception:
            return ""
        if h[:7].lower() == "bearer ":
            return h[7:].strip()
        return ""

    def _auth(self, params):
        """L1 令牌鉴权（query → Cookie → Bearer）；返回 (authed, set_cookie)。"""
        ui = getattr(self.server, "webui", None)
        expected = ui.token() if ui is not None else ""
        query_tok = (params.get("token") or [""])[0]
        if query_tok and _const_eq(query_tok, expected):
            return True, query_tok          # 有效 query 令牌 → 落 Cookie
        cookie_tok = self._cookie_token()
        if cookie_tok and _const_eq(cookie_tok, expected):
            return True, None
        bearer_tok = self._bearer_token()
        if bearer_tok and _const_eq(bearer_tok, expected):
            return True, None
        return False, None

    # ── 路由（GET）──
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

        # 免 token 静态端点（PWA 资源：无数据/无密钥）——浏览器获取 manifest /
        # 注册 SW 时默认不带凭据，若强制 token 会导致安装与注册失败（§3.7）。
        if path in _STATIC_ASSETS:
            self._serve_static(ui, path)
            return

        authed, set_cookie = self._auth(params)
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
        elif path == "/api/control/status":
            sid = self._cookie_value(CTL_COOKIE)
            self._send_json(ui.api_control_status(sid), set_cookie)
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

    # ── 路由（POST）：控制端点分派；非控制端点仍 405（只读边界不回退）──
    _CTL_POST_PATHS = ("/api/control", "/api/control/unlock",
                       "/api/control/lock")

    def _read_json(self):
        """读取 ≤4KB 的 JSON 请求体；失败返回 (None, reason)。"""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except Exception:
            length = 0
        if length < 0:
            return None, "bad request"
        if length > 4096:
            return None, "payload too large"
        try:
            raw = self.rfile.read(length) if length else b""
        except Exception:
            return None, "bad request"
        if not raw:
            return {}, ""
        try:
            obj = json.loads(raw.decode("utf-8"))
        except Exception:
            return None, "bad request"
        if not isinstance(obj, dict):
            return None, "bad request"
        return obj, ""

    def _handle_post(self):
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

        authed, _sc = self._auth(params)
        if not authed:
            self._send_text(401, "unauthorized")
            return

        if path not in self._CTL_POST_PATHS:
            # 只读边界不回退：非控制端点的写请求一律 405
            self._send_text(405, "method not allowed")
            return

        if not ui.control_enabled():
            self._send_err(404, "control disabled")
            return
        if not self._is_https():
            # 防御层：控制开启却收到明文写请求（理论上不可达）
            ui.audit_reject("plaintext", self)
            self._send_err(403, "plaintext rejected")
            return

        if path == "/api/control/unlock":
            self._post_unlock(ui)
        elif path == "/api/control/lock":
            self._post_lock(ui)
        else:
            self._post_control(ui)

    def _post_unlock(self, ui):
        body, why = self._read_json()
        if body is None:
            self._send_err(400, "bad request", why)
            return
        pin = body.get("pin")
        if not _looks_like_pin(pin):
            self._send_err(400, "bad request", "pin must be 6 digits")
            return
        code, obj, extra = ui.api_control_unlock(self, str(pin))
        self._send_json(obj, code=code, extra=extra)

    def _post_lock(self, ui):
        # 排空请求体（保持 HTTP/1.1 keep-alive 同步；lock 本身不需要 body）
        try:
            self._read_json()
        except Exception:
            pass
        sid = self._cookie_value(CTL_COOKIE)
        code, obj, extra = ui.api_control_lock(self, sid)
        self._send_json(obj, code=code, extra=extra)

    def _post_control(self, ui):
        body, why = self._read_json()
        if body is None:
            self._send_err(400, "bad request", why)
            return
        try:
            csrf_hdr = self.headers.get(CSRF_HEADER) or ""
        except Exception:
            csrf_hdr = ""
        sid = self._cookie_value(CTL_COOKIE)
        code, obj, extra = ui.api_control_action(
            self, sid, body.get("action"), csrf_hdr or body.get("csrf"))
        self._send_json(obj, code=code, extra=extra)

    def do_POST(self):
        try:
            self._handle_post()
        except Exception as e:
            _nl_log("webui post error: {}".format(e), "ERROR")
            try:
                self._send_text(500, "internal error")
            except Exception:
                pass

    def _method_not_allowed(self):
        try:
            self._send_text(405, "method not allowed")
        except Exception:
            pass

    do_PUT = _method_not_allowed
    do_DELETE = _method_not_allowed
    do_PATCH = _method_not_allowed
    do_HEAD = _method_not_allowed


# ══════════════════════════════════════════════════════════════════════════
# WebUI
# ══════════════════════════════════════════════════════════════════════════
class WebUI(object):
    """浏览器只读监控面板：bus 快照 + ThreadingHTTPServer + 令牌鉴权。"""

    def __init__(self, node, port=DEFAULT_PORT, bind="0.0.0.0",
                 control=False, control_ttl=None, tls=False,
                 confirm_control=False, allow_remote_control=False,
                 cert="", key=""):
        self._node = node
        self._port = _safe_port(port)
        self._bind = str(bind or "0.0.0.0").strip() or "0.0.0.0"

        # ── 控制（Phase4-2 批次2）：默认全关 → 行为与原先完全一致 ──
        self._control = bool(control)
        self._control_ttl = _safe_ttl(control_ttl)
        self._tls_wanted = bool(tls) or self._control   # 控制开启即强制 TLS
        self._confirm_control = bool(confirm_control)
        self._allow_remote_control = bool(allow_remote_control)
        self._cert = str(cert or "")
        self._key = str(key or "")
        self._tls_on = False

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

        try:
            from .audit import AuditLog
            self._audit = AuditLog()
        except Exception:
            self._audit = None
        self._sessions = _ControlSessionStore(ttl=self._control_ttl,
                                              audit=self._audit_event)
        # PIN 设置/重置/清除 → 清空全部控制会话（防旧授权残留）
        try:
            sec = _sec()
            if sec is not None:
                sec.register_control_session_reset_hook(self._sessions.lock_all)
        except Exception:
            pass

        self._subscribe()

    # ── 控制辅助 ──────────────────────────────────────────────────────
    def _audit_event(self, cmd, result, detail, actor="web-panel", remote=""):
        """写网页面板审计（复用 AuditLog 单点落盘；失败静默）。"""
        try:
            if self._audit is not None:
                self._audit.write(actor, cmd, {}, result, detail, remote)
        except Exception:
            pass

    def _ip_of(self, handler):
        try:
            return str(handler.client_address[0] or "")
        except Exception:
            return ""

    def _remote_of(self, handler):
        try:
            ca = handler.client_address
            return "{}:{}".format(ca[0], ca[1])
        except Exception:
            return ""

    def _ua_of(self, handler):
        try:
            return str(handler.headers.get("User-Agent") or "")
        except Exception:
            return ""

    def control_enabled(self):
        """面板有限控制是否启用。"""
        return bool(self._control)

    def audit_reject(self, reason, handler):
        """写一条 WEB_REJECT（reason ∈ plaintext/whitelist/csrf/control locked）。"""
        ip = self._ip_of(handler)
        self._audit_event("WEB_REJECT", "rejected", str(reason),
                          actor="web-panel|{}".format(ip),
                          remote=self._remote_of(handler))

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
        """绑定并启动服务；返回 (ok:bool, reason:str)。

        * 已启动 → (True, "")；
        * 控制开启（或显式要求 TLS）而证书不可用 → **(False, <稳定英文原因>)**，
          **绝不降级明文**；
        * 绑定失败 → (False, "start failed: ...")。
        向后兼容：门面同时接受 bool 与 (ok, reason) 两种返回（见 netlink/__init__）。
        """
        try:
            with self._lock:
                if self._running and self._srv is not None:
                    return True, ""
            ctx = None
            if self._tls_wanted:
                cert, key = self._cert, self._key
                if not cert or not key:
                    st = _state()
                    if st is not None:
                        cert = cert or str(getattr(st, "NETLINK_TLS_CERT", "") or "")
                        key = key or str(getattr(st, "NETLINK_TLS_KEY", "") or "")
                try:
                    from . import tls as _tls
                    ctx, why = _tls.server_context(cert, key)
                except Exception as e:
                    _nl_log("webui tls init failed: {}".format(e), "ERROR")
                    ctx, why = None, "tls unavailable"
                if ctx is None:
                    reason = str(why or "tls unavailable")
                    _nl_log("webui: control requires TLS; refusing ({})"
                            .format(reason), "ERROR")
                    self._publish_status(
                        "error", "面板启用有限控制需要 TLS，但证书不可用：{}"
                        .format(reason))
                    return False, reason
            bind = self._bind
            if self._control and bind in ("0.0.0.0", ""):
                # 绑定收窄：局域网暴露「运行/停止」风险高，默认收窄到本机
                if not self._allow_remote_control:
                    _nl_log("webui: control enabled with bind 0.0.0.0 -> "
                            "narrowed to 127.0.0.1 (set "
                            "netlink_web_allow_remote_control to allow LAN)")
                    bind = "127.0.0.1"
            srv = _Server((bind, self._port), _Handler, self)
            try:
                srv.ssl_ctx = ctx
            except Exception:
                pass
            th = threading.Thread(target=self._serve, name="nl-web")
            th.daemon = True
            self._subscribe()
            with self._lock:
                self._bind = bind
                self._tls_on = ctx is not None
                self._srv = srv
                self._thread = th
                self._running = True
            th.start()
            _nl_log("webui: started on {}:{} tls={}".format(
                bind, self._port, self._tls_on))
            return True, ""
        except Exception as e:
            _nl_log("webui start failed: {}".format(e), "ERROR")
            try:
                with self._lock:
                    self._srv = None
                    self._thread = None
                    self._running = False
            except Exception:
                pass
            return False, "start failed: {}".format(e)

    def _publish_status(self, level, msg):
        """向总线发布一条状态（供 GUI 展示失败原因）；失败静默。"""
        try:
            bus = self._bus()
            if bus is not None:
                bus.publish(TOPIC_STATUS, {"level": str(level),
                                           "msg": str(msg),
                                           "ts": int(time.time())})
        except Exception:
            pass

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
        """访问地址：<scheme>://<host>:<port>/?token=xxxx（控制/HTTPS 时为 https）。"""
        bind = self._bind
        if bind in ("127.0.0.1", "localhost"):
            host = "127.0.0.1"
        else:
            host = self._primary_ipv4() or "127.0.0.1"
        scheme = "https" if self._tls_on else "http"
        return "{}://{}:{}/?token={}".format(
            scheme, host, self._port, self.token())

    def _primary_ipv4(self):
        try:
            return _local_ipv4()
        except Exception:
            return ""

    def render_page(self):
        return _PAGE

    # ── PWA 静态资源（免 token；仅无数据/无密钥资源）────────────────────
    def static_asset(self, path):
        """返回静态资源字节；未知路径返回 None（不抛）。"""
        p = str(path or "")
        if p == "/manifest.webmanifest":
            try:
                return _MANIFEST_JSON.encode("utf-8")
            except Exception:
                return b"{}"
        if p == "/sw.js":
            return _SW_JS.encode("utf-8")
        if p == "/offline.html":
            return _OFFLINE_HTML.encode("utf-8")
        if p == "/favicon.ico":
            return icon_bytes("automation.ico")
        if p == "/icons/icon-192.png":
            return icon_bytes("icon-192.png", 192)
        if p == "/icons/icon-512.png":
            return icon_bytes("icon-512.png", 512)
        if p == "/icons/apple-touch-icon.png":
            return icon_bytes("apple-touch-icon.png", 180)
        return None

    def manifest_json(self):
        """manifest.webmanifest 文本（供测试/GUI 提示复用）。"""
        return _MANIFEST_JSON

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

    # ══════════════════════════════════════════════════════════════════════
    # 有限控制端点（Phase4-2 批次2）
    # 三层门槛：L1 token（handler 已过）→ L3 HTTPS → L2 会话/CSRF/白名单。
    # ══════════════════════════════════════════════════════════════════════
    def api_control_status(self, sid=None):
        """GET /api/control/status：不需要解锁，供前端发现 enabled 并显示状态。"""
        try:
            sec = _sec()
            lock = sec.control_pin_lock_state() if sec is not None else {}
        except Exception:
            lock = {}
        tls = bool(self._tls_on)
        out = {"enabled": bool(self._control),
               "tls": tls,
               "scheme": "https" if tls else "http",
               "ttl": int(self._control_ttl),
               "unlocked": False,
               "expires_in": 0,
               "csrf_required": True,
               "last_action": None,
               "pin_locked": bool(lock.get("locked")),
               "retry_after": int(lock.get("retry_after") or 0),
               "bind": str(self._bind),
               "remote_control_allowed": bool(self._allow_remote_control),
               "running": False, "has_script": False}
        try:
            st = _state()
            if st is not None:
                out["running"] = bool(getattr(st, "running", False))
                out["has_script"] = bool(getattr(st, "has_script", False))
        except Exception:
            pass
        if self._control and sid:
            s = self._sessions.get(sid)
            if s is not None:
                out["unlocked"] = True
                try:
                    out["expires_in"] = max(
                        0, int(float(s.get("exp") or 0) - time.time()))
                except Exception:
                    out["expires_in"] = 0
                out["last_action"] = s.get("last")
        return out

    def api_control_unlock(self, handler, pin):
        """POST /api/control/unlock：PIN 校验 → 建会话 + 返回 CSRF。

        返回 (code, body, extra_headers)。含防暴力 429 + Retry-After。
        """
        ip = self._ip_of(handler)
        remote = self._remote_of(handler)
        actor = "web-panel|{}".format(ip)
        sec = _sec()
        if sec is None:
            return 500, {"ok": False, "error": "control unavailable",
                         "detail": ""}, []
        try:
            lock = sec.control_pin_lock_state()
        except Exception:
            lock = {}
        if lock.get("locked"):
            retry = int(lock.get("retry_after") or 0)
            self._audit_event("WEB_UNLOCK", "rejected",
                              "locked retry_after={}".format(retry),
                              actor=actor, remote=remote)
            return 429, {"ok": False, "error": "pin locked",
                         "detail": "locked retry_after={}".format(retry)}, \
                [("Retry-After", str(retry))]
        ok = False
        try:
            ok = bool(sec.verify_control_pin(str(pin), actor=actor,
                                             remote=remote))
        except Exception:
            ok = False
        if not ok:
            # verify_control_pin 已写 WEB_PIN_FAIL 审计（不重复写）
            return 401, {"ok": False, "error": "pin invalid", "detail": ""}, []
        sid, csrf, exp_in = self._sessions.create(remote=remote,
                                                  ua=self._ua_of(handler))
        self._audit_event("WEB_UNLOCK", "ok",
                          "session={} ttl={}".format(sid[:8], exp_in),
                          actor=actor, remote=remote)
        body = {"ok": True, "csrf": csrf, "expires_in": int(exp_in)}
        return 200, body, [("Set-Cookie", _ctl_cookie(sid, exp_in))]

    def api_control_lock(self, handler, sid):
        """POST /api/control/lock：失效当前会话（幂等）。返回 200。"""
        ip = self._ip_of(handler)
        remote = self._remote_of(handler)
        actor = "web-panel|{}".format(ip)
        self._sessions.lock(sid, reason="user", remote=remote)
        self._audit_event("WEB_LOCK", "ok", "reason=user",
                          actor=actor, remote=remote)
        return 200, {"ok": True, "locked": True}, \
            [("Set-Cookie", _ctl_cookie("", 0))]

    def api_control_action(self, handler, sid, action, csrf_token):
        """POST /api/control：会话 → CSRF → 白名单 → submit_local 执行。

        返回 (code, body, extra_headers)；body 结构
        {"ok":bool,"action":str,"status":str,"detail":str,"mode":str}。
        """
        ip = self._ip_of(handler)
        remote = self._remote_of(handler)
        actor = "web-panel|{}".format(ip)
        s = self._sessions.get(sid) if sid else None
        if s is None:
            self._audit_event("WEB_REJECT", "rejected", "control locked",
                              actor=actor, remote=remote)
            return 403, {"ok": False, "error": "control locked",
                         "detail": ""}, []
        if not _const_eq(csrf_token, s.get("csrf")):
            self._audit_event("WEB_REJECT", "rejected", "csrf",
                              actor=actor, remote=remote)
            return 403, {"ok": False, "error": "csrf mismatch",
                         "detail": ""}, []
        act = str(action or "").strip().lower()
        if act in ("pause", "resume"):
            self._audit_event("WEB_REJECT", "rejected",
                              "whitelist action={}".format(act),
                              actor=actor, remote=remote)
            return 400, {"ok": False, "error": "action not allowed",
                         "detail": "{} not exposed".format(act)}, []
        if act not in _WEB_ACTIONS:
            self._audit_event("WEB_REJECT", "rejected",
                              "whitelist action={}".format(act or "?"),
                              actor=actor, remote=remote)
            return 400, {"ok": False, "error": "action not allowed",
                         "detail": ""}, []
        ce = None
        try:
            ce = getattr(self._node, "control", None)
        except Exception:
            ce = None
        if ce is None:
            return 500, {"ok": False, "error": "control unavailable",
                         "detail": ""}, []
        try:
            res = ce.submit_local(act, actor, remote)
        except Exception as e:
            _nl_log("webui control submit failed: {}".format(e), "WARN")
            res = {"ok": False, "action": act, "status": "rejected",
                   "detail": "local submit error", "mode": ""}
        status = str(res.get("status") or "rejected")
        detail = str(res.get("detail") or "")
        mode = str(res.get("mode") or "")
        try:
            self._sessions.set_last(sid, act, status or detail)
        except Exception:
            pass
        body = {"ok": bool(res.get("ok")), "action": act,
                "status": status, "detail": detail, "mode": mode}
        if status == "done":
            # 仅「成功通过全部校验（含预检）并执行完成」的写请求滑动续期；
            # 白名单外 / 预检失败 / 执行失败 均不续期（避免被用于「保活」）。
            self._sessions.touch(sid)
            return 200, body, []
        if status == "failed":
            body["error"] = "failed"
            return 200, body, []
        # rejected → 依据 detail 映射 HTTP 状态码；错误体含稳定 reason（doc §3.0）
        body["ok"] = False
        body["error"] = detail or "rejected"
        return _reject_code(detail), body, []
