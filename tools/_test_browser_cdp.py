# -*- coding: utf-8 -*-
"""tools/_test_browser_cdp.py — 浏览器后端增强 P2-B「CDP 接管已开浏览器」验收测试。

覆盖（无 playwright / 无真实浏览器亦可运行）：
  * parse_cdp_endpoint 纯函数：参数优先 / 回退配置 / 补 http:// / 去尾斜杠 / 空
  * plan_session_switch 纯函数：connect / reconnect / reuse 各分支
  * _connect_cdp 的 reuse 分支（已是 CDP 会话 → 不重复连接）
  * 失败短语：endpoint 空 → INVALID_ARGUMENT；连接失败 → CDP_CONNECT_FAILED（桩）
  * 无 playwright 时 → PW_NOT_INSTALLED
  * 命令注册断言：连接已开浏览器 / 接管浏览器 且别名绑定同一 handler
  * state 新键 browser_cdp_endpoint 默认值
  * AST 结构断言 + 模块级不存在 import playwright（懒加载契约）
  * 真实 CDP 连接（需本机 --remote-debugging-port 端点）→ SKIP 标注

只跑纯逻辑，不弹窗、不联网、不启动浏览器。退出码 0(全过/仅 WARN) / 1(存在 FAIL)。
输出行前缀: [OK] / [WARN] / [FAIL] / [SKIP]。
"""
import os
import ast
import sys

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
    """模拟 ScriptData：row.args = [...]（第 0 项即命令后第一个参数）。"""

    def __init__(self, args):
        self.args = list(args)


print("─" * 60)
# ══════════════════════════════════════════════════════════════════
# 1. parse_cdp_endpoint
# ══════════════════════════════════════════════════════════════════
print("── parse_cdp_endpoint ──")
eq(bb.parse_cdp_endpoint("http://127.0.0.1:9222", ""), "http://127.0.0.1:9222", "完整 http URL 原样")
eq(bb.parse_cdp_endpoint("127.0.0.1:9222", ""), "http://127.0.0.1:9222", "缺 scheme → 补 http://")
eq(bb.parse_cdp_endpoint("http://127.0.0.1:9222/", ""), "http://127.0.0.1:9222", "去尾斜杠")
eq(bb.parse_cdp_endpoint("", "http://localhost:9333"), "http://localhost:9333", "参数空 → 回退默认")
eq(bb.parse_cdp_endpoint("", ""), "", "两者皆空 → 空串")
eq(bb.parse_cdp_endpoint("  HTTP://x:1  ", ""), "HTTP://x:1", "去空白（保留 scheme）")
eq(bb.parse_cdp_endpoint("ws://127.0.0.1:9222", ""), "ws://127.0.0.1:9222", "ws scheme 保留")

print("─" * 60)
# ══════════════════════════════════════════════════════════════════
# 2. plan_session_switch
# ══════════════════════════════════════════════════════════════════
print("── plan_session_switch ──")
eq(bb.plan_session_switch("launch", False, True), "connect", "无会话 → connect")
eq(bb.plan_session_switch("launch", True, True), "reconnect", "有会话+关闭旧 → reconnect")
eq(bb.plan_session_switch("launch", True, False), "reuse", "有会话+不关旧 → reuse")
eq(bb.plan_session_switch("cdp", True, True), "reuse", "已是 cdp → reuse")
eq(bb.plan_session_switch("cdp", True, False), "reuse", "已是 cdp+不关旧 → reuse")
eq(bb.plan_session_switch("cdp", False, True), "connect", "cdp 无会话 → connect")

print("─" * 60)
# ══════════════════════════════════════════════════════════════════
# 3. _connect_cdp / 失败短语（离线）
# ══════════════════════════════════════════════════════════════════
print("── _connect_cdp / 失败短语 ──")
_orig_get_page = bb._get_page
_orig_pages = list(bb._pages)
_orig_page = bb._page
_orig_mode = bb._session_mode
_orig_active = bb._active_index
_orig_ev = bb._engine_variables
_fake_vars = {}
bb._engine_variables = lambda: _fake_vars
try:
    # 3.1 reuse：已是 cdp 会话（真实代码路径，不触 playwright）
    bb._session_mode = "cdp"
    bb._page = object()
    bb._pages[:] = [bb._page]
    _ok, _code, _d = bb._connect_cdp("http://127.0.0.1:9222", True)
    eq((_ok, _code), (True, "reuse"), "已是 cdp 会话 → (True, reuse)")

    # 3.2 endpoint 空 → facade INVALID_ARGUMENT（不触 playwright）
    bb._session_mode = "launch"
    bb._page = None
    bb._pages[:] = []
    state.BROWSER_CDP_ENDPOINT = ""
    _fake_vars.clear()
    _r = bb._browser_connect_cdp(_ArgsRow([]))
    check(_r is False, "空 endpoint → return False")
    eq(_fake_vars.get("browser_last_error"), "INVALID_ARGUMENT", "空 endpoint → INVALID_ARGUMENT")

    # 3.3 连接失败短语映射（桩替换 _connect_cdp，避免真实连接）
    _orig_connect = bb._connect_cdp
    bb._connect_cdp = lambda ep, close_old=True: (False, "CDP_CONNECT_FAILED",
                                                  "接管浏览器失败: ECONNREFUSED")
    try:
        _fake_vars.clear()
        _r = bb._browser_connect_cdp(_ArgsRow(["http://127.0.0.1:9222"]))
        check(_r is False, "连接失败 → return False")
        eq(_fake_vars.get("browser_last_error"), "CDP_CONNECT_FAILED", "连接失败 → CDP_CONNECT_FAILED")
    finally:
        bb._connect_cdp = _orig_connect

    # 3.4 无 playwright 时（真实 _connect_cdp）→ PW_NOT_INSTALLED
    if bb._check_playwright_available():
        skip("无 playwright 降级断言（本环境已安装 playwright）")
    else:
        bb._session_mode = "launch"
        bb._page = None
        bb._pages[:] = []
        _fake_vars.clear()
        _r = bb._browser_connect_cdp(_ArgsRow(["127.0.0.1:9222"]))
        check(_r is False, "无 playwright → return False")
        eq(_fake_vars.get("browser_last_error"), "PW_NOT_INSTALLED", "无 playwright → PW_NOT_INSTALLED")
finally:
    bb._get_page = _orig_get_page
    bb._engine_variables = _orig_ev
    bb._pages[:] = _orig_pages
    bb._page = _orig_page
    bb._session_mode = _orig_mode
    bb._active_index = _orig_active

print("─" * 60)
# ══════════════════════════════════════════════════════════════════
# 4. AST 结构断言
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
for fn in ["parse_cdp_endpoint", "plan_session_switch", "_connect_cdp", "_browser_connect_cdp"]:
    check(fn in func_names, "存在函数定义 {}".format(fn))

print("─" * 60)
# ══════════════════════════════════════════════════════════════════
# 5. commands 注册 / handler 绑定 / state 新键
# ══════════════════════════════════════════════════════════════════
print("── commands 注册 / handler 绑定 / state 新键 ──")
import commands  # noqa: E402

names = commands.list_names()
for n in ["连接已开浏览器", "接管浏览器"]:
    check(n in names, "注册表包含新命令 '{}'".format(n))

try:
    import engine  # noqa: E402
    _engine_ok = True
except Exception as e:  # pragma: no cover
    _engine_ok = False
    warn("无法 import engine，handler 绑定断言 SKIP: {}".format(e))
if _engine_ok:
    check(commands.get_handler("连接已开浏览器") is not None, "get_handler('连接已开浏览器') 非空")
    check(commands.get_handler("接管浏览器") is not None, "get_handler('接管浏览器') 非空")
    check(commands.get_handler("连接已开浏览器") is commands.get_handler("接管浏览器"),
          "别名绑定同一 handler")
else:
    skip("handler 绑定断言（engine 未加载）")

eq(getattr(state, "BROWSER_CDP_ENDPOINT", "<missing>"), "", "state.BROWSER_CDP_ENDPOINT 默认值")

print("─" * 60)
skip("真实 CDP 接管（需本机 chrome --remote-debugging-port=9222 且 playwright 已安装）—— 未在本环境执行")

print("─" * 60)
if _FAILS:
    print("结果: FAIL ({} 项失败, {} 项警告)".format(len(_FAILS), len(_WARNS)))
    for m in _FAILS:
        print("  - " + m)
    sys.exit(1)
else:
    print("结果: PASS (0 失败, {} 项警告)".format(len(_WARNS)))
    sys.exit(0)
