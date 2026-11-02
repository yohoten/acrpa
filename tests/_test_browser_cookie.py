# -*- coding: utf-8 -*-
"""tools/_test_browser_cookie.py — 浏览器后端增强 P0「Cookie 序列化」验收测试。

覆盖（无 playwright / 无真实浏览器亦可运行）：
  * cookies_to_json / parse_cookies：JSON 列表往返一致
  * cookies_to_header / parse_cookies：header 串往返（name/value）
  * cookies_to_netscape / parse_cookies：Netscape 串往返（domain/path/secure/expires）
  * parse_cookies 多种输入（dict / list / "" / None / 非法串）
  * AST 结构断言：cookies_* / parse_cookies 存在
  * 降级断言：后端不可用时浏览器读取Cookie return False（PW_NOT_INSTALLED）

只跑纯逻辑，不弹窗、不联网、不启动浏览器。退出码 0(全过/仅 WARN) / 1(存在 FAIL)。
输出行前缀: [OK] / [WARN] / [FAIL]。
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


import browser_backend as bb  # noqa: E402

print("── cookies_to_json / parse_cookies 往返 ──")
_cookies = [
    {"name": "sid", "value": "abc123", "domain": "example.com", "path": "/",
     "secure": True, "expires": 1893456000},
    {"name": "lang", "value": "zh-CN", "domain": "example.com", "path": "/",
     "secure": False},
]
j = bb.cookies_to_json(_cookies)
check(isinstance(j, str) and j.startswith("["), "cookies_to_json → JSON 数组串")
_back = bb.parse_cookies(j)
eq(_back, _cookies, "JSON 往返一致")

print("─" * 60)
print("── cookies_to_header / parse_cookies ──")
h = bb.cookies_to_header(_cookies)
eq(h, "sid=abc123; lang=zh-CN", "cookies_to_header 格式")
_hp = bb.parse_cookies(h)
eq(_hp, [{"name": "sid", "value": "abc123"}, {"name": "lang", "value": "zh-CN"}],
   "header 往返（name/value）")

print("─" * 60)
print("── cookies_to_netscape / parse_cookies ──")
n = bb.cookies_to_netscape(_cookies)
check(n.startswith("# Netscape HTTP Cookie File"), "Netscape 头注释存在")
check("\t" in n, "Netscape 使用制表符分隔")
_np = bb.parse_cookies(n)
eq(_np, _cookies, "Netscape 往返一致（domain/path/secure/expires）")

print("─" * 60)
print("── parse_cookies 多种输入 ──")
eq(bb.parse_cookies(None), [], "None → []")
eq(bb.parse_cookies(""), [], "空串 → []")
eq(bb.parse_cookies("   "), [], "空白 → []")
eq(bb.parse_cookies({"name": "a", "value": "1"}), [{"name": "a", "value": "1"}],
   "dict → [dict]")
eq(bb.parse_cookies([{"name": "a", "value": "1"}]), [{"name": "a", "value": "1"}],
   "list → 原样")
eq(bb.parse_cookies("a=1; b=2"), [{"name": "a", "value": "1"}, {"name": "b", "value": "2"}],
   "header 字面量解析")
eq(bb.parse_cookies("not-a-cookie"), [], "无可解析内容 → []")

# JSON 往返对“含特殊字符值”的稳健性
_weird = [{"name": "k", "value": "a=b;c", "domain": ".x.com", "path": "/p"}]
eq(bb.parse_cookies(bb.cookies_to_json(_weird)), _weird, "JSON 特殊字符往返一致")

print("─" * 60)
print("── AST 结构断言 ──")
with open(os.path.join(SRC, "browser_backend.py"), "r", encoding="utf-8") as f:
    tree = ast.parse(f.read())
func_names = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
for fn in ["cookies_to_json", "cookies_to_header", "cookies_to_netscape",
           "parse_cookies", "cookies_get_all", "cookies_set",
           "_browser_cookie_get", "_browser_cookie_set"]:
    check(fn in func_names, "存在函数定义 {}".format(fn))

print("─" * 60)
print("── 降级断言（后端不可用 → return False） ──")


class _ArgsRow(object):
    def __init__(self, args):
        self.args = args


_orig_get_page = bb._get_page
_fake_vars = {}
_orig_ev = bb._engine_variables
bb._engine_variables = lambda: _fake_vars
try:
    bb._get_page = lambda: None
    _fake_vars.clear()
    r = bb._browser_cookie_get(_ArgsRow(["cookie_json", "json"]))
    check(r is False, "后端不可用 读取Cookie return False")
    eq(_fake_vars.get("browser_last_error"), "PW_NOT_INSTALLED", "读取Cookie → PW_NOT_INSTALLED")

    _fake_vars.clear()
    r = bb._browser_cookie_set(_ArgsRow(["cookie_json"]))
    check(r is False, "后端不可用 设置Cookie return False")
    eq(_fake_vars.get("browser_last_error"), "PW_NOT_INSTALLED", "设置Cookie → PW_NOT_INSTALLED")
finally:
    bb._get_page = _orig_get_page
    bb._engine_variables = _orig_ev

print("─" * 60)
if _FAILS:
    print("结果: FAIL ({} 项失败, {} 项警告)".format(len(_FAILS), len(_WARNS)))
    for m in _FAILS:
        print("  - " + m)
    sys.exit(1)
else:
    print("结果: PASS (0 失败, {} 项警告)".format(len(_WARNS)))
    sys.exit(0)
