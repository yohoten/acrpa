# -*- coding: utf-8 -*-
"""tools/_test_browser_listen.py — 浏览器后端增强 P2-C「接口监听 / 抓包」验收测试。

覆盖（无 playwright / 无真实浏览器亦可运行）：
  * parse_resource_types：中文/英文同义、逗号/空白分隔、全部=不过滤
  * dedup_key：(method, url, status) 去重键
  * parse_url_pattern / match_url：包含匹配 / `/re/` 正则 / 空恒真
  * match_response：过滤列表
  * export_packets：JSON 导出可解析
  * _start_listen / _on_response：过滤 + 去重 + deque(maxlen) 丢最旧
  * _pop_matching_packet / _collect_packets：取值累计与超时（假页，无浏览器）
  * facade：开始监听 / 等待数据包 / 停止监听（含 INVALID_ARGUMENT / TIMEOUT / LISTEN_ERROR）
  * 命令注册断言：开始监听 / 等待数据包 / 停止监听
  * state 新键默认值（P2 四项）
  * AST 结构断言 + 模块级不存在 import playwright（懒加载契约）
  * 真实网络响应监听 → SKIP 标注

只跑纯逻辑，不弹窗、不联网、不启动浏览器。退出码 0(全过/仅 WARN) / 1(存在 FAIL)。
输出行前缀: [OK] / [WARN] / [FAIL] / [SKIP]。
"""
import os
import ast
import sys
import json

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_FAILS = []
_WARNS = []


def ok(msg):
    print("[OK] " + msg)


def warn(msg):
    _WARNS.append(msg)
    print("[WARN] " + msg)


def skip(msg):
    print("[SKIP] " + msg)


def fail(msg):
    _FAILS.append(msg)
    print("[FAIL] " + msg)


def check(cond, msg):
    if cond:
        ok(msg)
    else:
        fail(msg)


def eq(actual, expected, msg):
    if actual == expected:
        ok("{} -> {!r}".format(msg, actual))
    else:
        fail("{}: 期望 {!r}，实际 {!r}".format(msg, expected, actual))


for _m in list(sys.modules):
    if _m == "playwright" or _m.startswith("playwright."):
        del sys.modules[_m]

import browser_backend as bb  # noqa: E402
import state  # noqa: E402

_pw_loaded = any(m == "playwright" or m.startswith("playwright.") for m in sys.modules)
check(not _pw_loaded, "import browser_backend 未触发 playwright 导入（懒加载契约）")


class _ArgsRow(object):
    def __init__(self, args):
        self.args = list(args)


class _FakeReq(object):
    def __init__(self, method, rtype):
        self.method = method
        self.resource_type = rtype


class _FakeResp(object):
    def __init__(self, url, method="GET", status=200, rtype="xhr", headers=None):
        self.url = url
        self.status = status
        self.request = _FakeReq(method, rtype)
        self.headers = headers or {}


class _FakePage(object):
    def __init__(self):
        self.handlers = []
        self.context = None
        self._title = "t"
        self.url = "http://t/"

    def title(self):
        return self._title

    def on(self, evt, cb):
        self.handlers.append((evt, cb))

    def remove_listener(self, evt, cb):
        try:
            self.handlers.remove((evt, cb))
        except ValueError:
            pass

    def close(self):
        pass


class _BadPage(_FakePage):
    def on(self, evt, cb):
        raise RuntimeError("boom")


print("─" * 60)
# ══════════════════════════════════════════════════════════════════
# 1. 纯函数
# ══════════════════════════════════════════════════════════════════
print("── parse_resource_types ──")
eq(bb.parse_resource_types(""), frozenset(), "空 → 不过滤（空集合）")
eq(bb.parse_resource_types("全部"), frozenset(), "全部 → 不过滤")
eq(bb.parse_resource_types("all"), frozenset(), "all → 不过滤")
eq(bb.parse_resource_types("xhr"), frozenset(["xhr"]), "xhr")
eq(bb.parse_resource_types("XHR"), frozenset(["xhr"]), "XHR → 归一化小写")
eq(bb.parse_resource_types("接口"), frozenset(["xhr"]), "中文 '接口' → xhr")
eq(bb.parse_resource_types("xhr,fetch"), frozenset(["xhr", "fetch"]), "逗号分隔")
eq(bb.parse_resource_types("xhr fetch"), frozenset(["xhr", "fetch"]), "空白分隔")
eq(bb.parse_resource_types("文档,脚本"), frozenset(["document", "script"]), "中文多类型")

print("── dedup_key ──")
eq(bb.dedup_key({"method": "GET", "url": "u", "status": 200}), ("GET", "u", 200), "去重键")
eq(bb.dedup_key({}), ("", "", None), "空对象 → 默认键")

print("── parse_url_pattern / match_url ──")
eq(bb.parse_url_pattern(""), ("", "contains"), "空 pattern → 空/contains")
eq(bb.parse_url_pattern("/api/token"), ("/api/token", "contains"), "含斜杠但非 /re/ → contains")
eq(bb.parse_url_pattern("/api/\\d+/"), ("api/\\d+", "regex"), "/re/ → regex")
eq(bb.match_url("http://t/api/x", "api", "contains"), True, "包含匹配命中")
eq(bb.match_url("http://t/other", "api", "contains"), False, "包含匹配未命中")
eq(bb.match_url("http://t/api/123", "api/\\d+", "regex"), True, "正则命中")
eq(bb.match_url("http://t/api/x", "", "contains"), True, "空 value 恒真")
eq(bb.match_url("http://t/x", "[/", "regex"), False, "非法正则 → False（不抛）")

print("── match_response / export_packets ──")
_items = [{"url": "http://t/api/a", "status": 200},
          {"url": "http://t/api/b", "status": 200},
          {"url": "http://t/other", "status": 500}]
eq([i["url"] for i in bb.match_response(_items, "api")],
   ["http://t/api/a", "http://t/api/b"], "match_response 包含过滤")
eq([i["url"] for i in bb.match_response(_items, "/api/[ab]/")],
   ["http://t/api/a", "http://t/api/b"], "match_response /re/ 正则")
eq([i["url"] for i in bb.match_response(_items, "")], [i["url"] for i in _items], "空 pattern 全部命中")
try:
    _txt = bb.export_packets([{"url": "u", "status": 200}], "json")
    _back = json.loads(_txt)
    eq(_back[0]["url"], "u", "export_packets JSON 可解析")
except Exception as e:
    fail("export_packets 非合法 JSON: {}".format(e))

print("─" * 60)
# ══════════════════════════════════════════════════════════════════
# 2. session 队列：过滤 / 去重 / 上限 / 取值（假页，无浏览器）
# ══════════════════════════════════════════════════════════════════
print("── session 监听队列 ──")
_orig_get_page = bb._get_page
_orig_pages = list(bb._pages)
_orig_page = bb._page
_orig_active = bb._active_index
_orig_mode = bb._session_mode
_orig_ev = bb._engine_variables
_fake_vars = {}
bb._engine_variables = lambda: _fake_vars
try:
    page = _FakePage()
    bb._get_page = lambda: page
    bb._pages[:] = []
    bb._active_index = -1
    bb._page = None
    bb._session_mode = "launch"

    _ok, _c, _d = bb._start_listen("/api", "", 2)
    check(_ok, "_start_listen 成功")
    eq(bb._listen_queue.maxlen, 2, "队列 maxlen = 参数上限")
    check(any(h[0] == "response" for h in page.handlers), "已注册 response 监听")

    bb._on_response(_FakeResp("http://t/api/a", status=200))
    bb._on_response(_FakeResp("http://t/api/b", status=200))
    eq(len(bb._listen_queue), 2, "命中入队 2 条")
    bb._on_response(_FakeResp("http://t/api/a", status=200))
    eq(len(bb._listen_queue), 2, "去重 (method,url,status) 生效")
    bb._on_response(_FakeResp("http://t/api/a", status=500))
    eq(len(bb._listen_queue), 2, "同 url 不同 status → 视为不同键（仍入队但受上限约束）")
    bb._on_response(_FakeResp("http://t/api/c", status=200))
    eq(len(bb._listen_queue), 2, "超上限丢弃最旧")
    eq([it["url"] for it in bb._listen_queue], ["http://t/api/a", "http://t/api/c"],
       "队列保留最新 2 条")

    # 资源类型过滤
    bb._start_listen("", "xhr", 200)
    bb._on_response(_FakeResp("http://t/doc", rtype="document"))
    bb._on_response(_FakeResp("http://t/api/y", rtype="xhr"))
    eq([it["url"] for it in bb._listen_queue], ["http://t/api/y"], "资源类型过滤：非 xhr 被排除")

    # URL 正则过滤
    bb._start_listen("/api/\\d+/token/", "", 200)
    bb._on_response(_FakeResp("http://t/api/123/token/x"))
    bb._on_response(_FakeResp("http://t/nope"))
    eq(len(bb._listen_queue), 1, "URL 正则过滤生效")

    # 取值 / 超时
    bb._start_listen("/api", "", 200)
    bb._on_response(_FakeResp("http://t/api/1"))
    bb._on_response(_FakeResp("http://t/api/2"))
    _ok, _items, _d = bb._collect_packets("/api", 2, 5)
    eq((_ok, len(_items)), (True, 2), "_collect_packets 取回 2 条")
    eq(len(bb._listen_queue), 0, "取值后队列清空")
    _ok, _items, _d = bb._collect_packets("/api", 1, 0)
    eq(_ok, False, "无数据 + 超时 → ok=False")
    eq(_d, "TIMEOUT", "超时 detail=TIMEOUT")
    eq(bb._pop_matching_packet("/api"), None, "空队列取包 → None")

    # 停止监听：移除回调 + 清空队列
    _ok, _c, _d = bb._start_listen("/api", "", 200)
    bb._on_response(_FakeResp("http://t/api/z"))
    eq(len(bb._listen_queue), 1, "重新监听后入队")
    bb._stop_listen()
    eq(len(bb._listen_queue), 0, "停止监听清空队列")
    eq(bb._listen_handler, None, "停止监听移除 handler")
    check(not any(h[0] == "response" for h in page.handlers), "停止监听已 remove_listener")

    print("── facade：开始监听 / 等待数据包 / 停止监听 ──")
    # 未监听 → INVALID_ARGUMENT
    bb._stop_listen()
    _fake_vars.clear()
    _r = bb._browser_wait_packets(_ArgsRow(["/api"]))
    check(_r is False, "未监听 → return False")
    eq(_fake_vars.get("browser_last_error"), "INVALID_ARGUMENT", "未监听 → INVALID_ARGUMENT")

    # 成功路径：写回变量（JSON 字符串）
    _fake_vars.clear()
    _r = bb._browser_start_listen(_ArgsRow(["/api", "xhr", "50"]))
    check(_r is None, "开始监听 return None")
    bb._on_response(_FakeResp("http://t/api/token", status=200))
    _r = bb._browser_wait_packets(_ArgsRow(["/api", "1", "0", "pkt_var"]))
    check(_r is None, "等待数据包 return None")
    _txt = _fake_vars.get("pkt_var")
    check(_txt is not None, "等待数据包写回变量 pkt_var")
    try:
        _arr = json.loads(_txt)
        eq(_arr[0]["url"], "http://t/api/token", "写回内容为命中包的 JSON 列表")
    except Exception as e:
        fail("写回变量非合法 JSON: {}".format(e))

    # 超时 → TIMEOUT
    _fake_vars.clear()
    bb._start_listen("/never", "", 10)
    _r = bb._browser_wait_packets(_ArgsRow(["/never", "1", "0", "x"]))
    check(_r is False, "无命中 → return False")
    eq(_fake_vars.get("browser_last_error"), "TIMEOUT", "无命中 → TIMEOUT")

    # 停止监听幂等成功
    _r = bb._browser_stop_listen(_ArgsRow([]))
    check(_r is None, "停止监听 return None")

    # LISTEN_ERROR：page.on 抛错
    bad = _BadPage()
    bb._get_page = lambda: bad
    bb._pages[:] = []
    bb._page = None
    _fake_vars.clear()
    _r = bb._browser_start_listen(_ArgsRow(["/api"]))
    check(_r is False, "注册回调失败 → return False")
    eq(_fake_vars.get("browser_last_error"), "LISTEN_ERROR", "注册回调失败 → LISTEN_ERROR")

    # 无浏览器 → PW_NOT_INSTALLED
    bb._get_page = lambda: None
    bb._pages[:] = []
    bb._page = None
    _fake_vars.clear()
    _r = bb._browser_start_listen(_ArgsRow(["/api"]))
    check(_r is False, "无浏览器 → return False")
    eq(_fake_vars.get("browser_last_error"), "PW_NOT_INSTALLED", "无浏览器 → PW_NOT_INSTALLED")
finally:
    bb._get_page = _orig_get_page
    bb._engine_variables = _orig_ev
    bb._pages[:] = _orig_pages
    bb._page = _orig_page
    bb._active_index = _orig_active
    bb._session_mode = _orig_mode
    bb._stop_listen()

print("─" * 60)
# ══════════════════════════════════════════════════════════════════
# 3. AST 结构断言
# ══════════════════════════════════════════════════════════════════
print("── AST 结构断言 ──")
with open(os.path.join(SRC, "browser_backend.py"), "r", encoding="utf-8") as f:
    tree = ast.parse(f.read())
module_level_imports = []
for node in tree.body:
    if isinstance(node, ast.Import):
        for a in node.names:
            module_level_imports.append(a.name)
    elif isinstance(node, ast.ImportFrom):
        module_level_imports.append(node.module or "")
check(not any(n == "playwright" or n.startswith("playwright.") for n in module_level_imports),
      "模块级不存在 import playwright（AST）")
func_names = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
for fn in ["parse_resource_types", "dedup_key", "parse_url_pattern", "match_url",
           "match_response", "export_packets", "_on_response", "_start_listen",
           "_stop_listen", "_pop_matching_packet", "_collect_packets",
           "_browser_start_listen", "_browser_wait_packets", "_browser_stop_listen"]:
    check(fn in func_names, "存在函数定义 {}".format(fn))

print("─" * 60)
# ══════════════════════════════════════════════════════════════════
# 4. commands 注册 / handler 绑定 / state 新键
# ══════════════════════════════════════════════════════════════════
print("── commands 注册 / handler 绑定 / state 新键 ──")
import commands  # noqa: E402

names = commands.list_names()
for n in ["开始监听", "等待数据包", "停止监听"]:
    check(n in names, "注册表包含新命令 '{}'".format(n))

try:
    import engine  # noqa: E402
    _engine_ok = True
except Exception as e:  # pragma: no cover
    _engine_ok = False
    warn("无法 import engine，handler 绑定断言 SKIP: {}".format(e))
if _engine_ok:
    for n in ["开始监听", "等待数据包", "停止监听"]:
        check(commands.get_handler(n) is not None, "get_handler('{}') 非空".format(n))
else:
    skip("handler 绑定断言（engine 未加载）")

_expect = {
    "browser_cdp_endpoint": "",
    "browser_listen_max": 200,
    "browser_listen_default_timeout": 15.0,
    "browser_user_agent": "",
}
for k, v in _expect.items():
    eq(getattr(state, k.upper(), "<missing>"), v, "state.{} 默认值".format(k.upper()))

print("─" * 60)
skip("真实网络响应监听（需 playwright + Chromium 真实请求）—— 未在本环境执行")

print("─" * 60)
if _FAILS:
    print("结果: FAIL ({} 项失败, {} 项警告)".format(len(_FAILS), len(_WARNS)))
    for m in _FAILS:
        print("  - " + m)
    sys.exit(1)
else:
    print("结果: PASS (0 失败, {} 项警告)".format(len(_WARNS)))
    sys.exit(0)
