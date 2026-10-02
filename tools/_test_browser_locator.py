# -*- coding: utf-8 -*-
"""tools/_test_browser_locator.py — 浏览器后端增强 P0「locator 定位解析层」验收测试。

覆盖（无 playwright / 无真实浏览器亦可运行）：
  * parse_locator DSL：原生直通（#id/.cls/[a=b]/div > span/text=/xpath=/裸标签）
    + 各自定义前缀映射（css:/xpath:/text:/text^/text$/tag:/@/@@/role:/label:/
    placeholder:/testid:）
  * 边界：空串 / 纯空白 → INVALID_LOCATOR
  * _row_arg 双接口取参一致（row.args 与 xlrd 风格 row[i].value）+ 越界/空值默认
  * _resolve_vars：${x} 命中 / 缺失 / 无占位符
  * 结构断言（AST）：模块级不存在 import playwright（懒加载契约）；关键函数存在
  * import browser_backend 不触发 playwright 导入
  * commands 注册表含 4 个新命令名；既有 5 条命令注册逐字未变
  * state 新键默认值与设计文档一致
  * 降级断言：缺 playwright 时新命令 return False 且错误码正确（INVALID_ARGUMENT / PW_NOT_INSTALLED）

只跑纯逻辑，不弹窗、不联网、不启动浏览器。退出码 0(全过/仅 WARN) / 1(存在 FAIL)。
输出行前缀: [OK] / [WARN] / [FAIL]。
"""
import os
import re
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


# ══════════════════════════════════════════════════════════════════
# 0. import browser_backend 不得触发 playwright 导入
# ══════════════════════════════════════════════════════════════════
for _m in list(sys.modules):
    if _m == "playwright" or _m.startswith("playwright."):
        del sys.modules[_m]

import browser_backend as bb  # noqa: E402

_pw_loaded = any(m == "playwright" or m.startswith("playwright.") for m in sys.modules)
check(not _pw_loaded, "import browser_backend 未触发 playwright 导入（懒加载契约）")

print("─" * 60)

# ══════════════════════════════════════════════════════════════════
# 1. parse_locator — 原生直通 / 向后兼容
# ══════════════════════════════════════════════════════════════════
print("── parse_locator 原生直通（向后兼容）──")
for spec in ["#login-btn", ".result", "[data-x=1]", "div > span", "button", "div"]:
    p = bb.parse_locator(spec)
    eq(p.get("engine"), "playwright", "parse_locator({!r}).engine".format(spec))
    eq(p.get("raw"), spec, "parse_locator({!r}).raw 原样直通".format(spec))

for spec in ["text=登录", "xpath=//div", "css=#a .b", "role=button"]:
    p = bb.parse_locator(spec)
    eq(p.get("engine"), "playwright", "parse_locator({!r}).engine".format(spec))
    eq(p.get("raw"), spec, "parse_locator({!r}).raw 原样直通".format(spec))

print("─" * 60)
# ══════════════════════════════════════════════════════════════════
# 2. parse_locator — 自定义 DSL 前缀映射
# ══════════════════════════════════════════════════════════════════
print("── parse_locator 自定义 DSL ──")

p = bb.parse_locator("登录按钮")
eq(p.get("engine"), "get_by_text", "无前缀非CSS → get_by_text")
eq(p.get("text"), "登录按钮", "get_by_text.text")
eq(p.get("exact"), False, "get_by_text.exact=False（模糊）")

p = bb.parse_locator("css:#id > .cls")
eq(p.get("engine"), "playwright", "css: → playwright")
eq(p.get("raw"), "#id > .cls", "css: 剥离前缀")

p = bb.parse_locator('xpath://button[text()="登录"]')
eq(p.get("engine"), "playwright", "xpath: → playwright")
eq(p.get("raw"), 'xpath=//button[text()="登录"]', "xpath: 前缀补 xpath=")

p = bb.parse_locator("text:登录")
eq(p.get("engine"), "get_by_text", "text: → get_by_text")
eq(p.get("text"), "登录", "text: 文本")
eq(p.get("exact"), False, "text: exact=False")

p = bb.parse_locator("text^登 录")
eq(p.get("engine"), "get_by_text", "text^ → get_by_text")
eq(p.get("regex"), "^" + re.escape("登 录"), "text^ regex ^...")

p = bb.parse_locator("text$完成")
eq(p.get("engine"), "get_by_text", "text$ → get_by_text")
eq(p.get("regex"), re.escape("完成") + "$", "text$ regex ...$")

p = bb.parse_locator("tag:button")
eq(p.get("engine"), "playwright", "tag: 单标签 → playwright")
eq(p.get("raw"), "button", "tag:button raw")

p = bb.parse_locator("tag:input@type=submit")
eq(p.get("engine"), "playwright", "tag: 叠加属性 → playwright")
eq(p.get("raw"), "input[type=submit]", "tag:input@type=submit raw")

p = bb.parse_locator("@data-id")
eq(p.get("raw"), "[data-id]", "@attr 存在")

p = bb.parse_locator("@name=user")
eq(p.get("raw"), "[name=user]", "@attr=val 等值")

p = bb.parse_locator("tag:button@@text:提交")
eq(p.get("engine"), "playwright", "@@ 组合 → playwright")
eq(p.get("raw"), "button", "@@ 基串 raw")
eq(p.get("has_text"), "提交", "@@ 追加 has_text")

p = bb.parse_locator("role:button[name=登录]")
eq(p.get("engine"), "get_by_role", "role: → get_by_role")
eq(p.get("role"), "button", "role: 角色名")
eq(p.get("name"), "登录", "role: name")

p = bb.parse_locator("label:用户名")
eq(p.get("engine"), "get_by_label", "label: → get_by_label")
eq(p.get("text"), "用户名", "label: 文本")

p = bb.parse_locator("placeholder:请输入手机号")
eq(p.get("engine"), "get_by_placeholder", "placeholder: → get_by_placeholder")
eq(p.get("text"), "请输入手机号", "placeholder: 文本")

p = bb.parse_locator("testid:submit-btn")
eq(p.get("engine"), "get_by_test_id", "testid: → get_by_test_id")
eq(p.get("test_id"), "submit-btn", "testid: 标识")

print("─" * 60)
# ══════════════════════════════════════════════════════════════════
# 3. parse_locator — 边界
# ══════════════════════════════════════════════════════════════════
print("── parse_locator 边界 ──")
for bad in ["", "   ", None]:
    p = bb.parse_locator(bad)
    eq(p.get("engine"), "error", "空/空白 {!r} → error".format(bad))
    eq(p.get("code"), "INVALID_LOCATOR", "空/空白 {!r} → INVALID_LOCATOR".format(bad))

for bad in ["text:", "tag:", "role:", "label:", "placeholder:", "testid:", "css:", "xpath:"]:
    p = bb.parse_locator(bad)
    eq(p.get("engine"), "error", "残缺前缀 {!r} → error".format(bad))

print("─" * 60)
# ══════════════════════════════════════════════════════════════════
# 4. _row_arg — 双接口取参
# ══════════════════════════════════════════════════════════════════
print("── _row_arg 双接口 ──")


class _Cell(object):
    __slots__ = ("value",)

    def __init__(self, v):
        self.value = v


class _XRow(object):
    """模拟 xlrd 风格 row（含命令名，参数自索引 1 起）。"""

    def __init__(self, values):
        self.cells = [_Cell(v) for v in values]

    def __getitem__(self, i):
        return self.cells[i] if i < len(self.cells) else _Cell(None)


class _ArgsRow(object):
    """模拟 ScriptData / _Row-like（.args 为“命令名后的参数”）。"""

    def __init__(self, args):
        self.args = args


arow = _ArgsRow(["a", "b", ""])
xrow = _XRow(["cmd", "a", "b", None])

eq(bb._row_arg(arow, 0, "d"), "a", ".args[0]")
eq(bb._row_arg(arow, 1, "d"), "b", ".args[1]")
eq(bb._row_arg(arow, 2, "d"), "d", ".args 空值→default")
eq(bb._row_arg(arow, 9, "d"), "d", ".args 越界→default")

eq(bb._row_arg(xrow, 0, "d"), "a", "xlrd row[1].value")
eq(bb._row_arg(xrow, 1, "d"), "b", "xlrd row[2].value")
eq(bb._row_arg(xrow, 2, "d"), "d", "xlrd None→default")
eq(bb._row_arg(xrow, 9, "d"), "d", "xlrd 越界→default")

eq(bb._row_arg(_ArgsRow(["a", "b"]), 0, "d"), bb._row_arg(_XRow(["c", "a", "b"]), 0, "d"),
   "两接口取参一致")

print("─" * 60)
# ══════════════════════════════════════════════════════════════════
# 5. _resolve_vars
# ══════════════════════════════════════════════════════════════════
print("── _resolve_vars ──")
_orig_ev = bb._engine_variables
bb._engine_variables = lambda: {"x": "1"}
try:
    eq(bb._resolve_vars("a${x}b"), "a1b", "a${x}b (x=1)")
    eq(bb._resolve_vars("a${miss}b"), "ab", "缺失变量→空串")
    eq(bb._resolve_vars("plain"), "plain", "无占位符原样")
finally:
    bb._engine_variables = _orig_ev

print("─" * 60)
# ══════════════════════════════════════════════════════════════════
# 6. AST / 结构断言
# ══════════════════════════════════════════════════════════════════
print("── AST 结构断言 ──")
src_path = os.path.join(SRC, "browser_backend.py")
with open(src_path, "r", encoding="utf-8") as f:
    source = f.read()
tree = ast.parse(source)

module_level_imports = []
for node in tree.body:
    if isinstance(node, ast.Import):
        for a in node.names:
            module_level_imports.append(a.name)
    elif isinstance(node, ast.ImportFrom):
        module_level_imports.append(node.module or "")

check(not any(n == "playwright" or n.startswith("playwright.") for n in module_level_imports),
      "模块级不存在 import playwright（AST）")

inner_pw = False
for fn in [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
    for node in ast.walk(fn):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name == "playwright" or a.name.startswith("playwright."):
                    inner_pw = True
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").startswith("playwright"):
                inner_pw = True
check(inner_pw, "playwright import 确实位于函数体内（延迟导入）")

func_names = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
for fn in ["parse_locator", "build_locator", "parse_wait_state", "wait_for",
           "_run_js", "cookies_get_all", "cookies_set", "cookies_to_json",
           "parse_cookies", "_row_arg", "_resolve_vars", "_fail", "_clear_last_error",
           "_browser_exec_js", "_browser_cookie_get", "_browser_cookie_set",
           "_browser_navigate", "_browser_click", "_browser_input",
           "_browser_wait_element", "_browser_screenshot"]:
    check(fn in func_names, "存在函数定义 {}".format(fn))

print("─" * 60)
# ══════════════════════════════════════════════════════════════════
# 7. commands 注册（新命令 + 既有 5 条逐字未变）
# ══════════════════════════════════════════════════════════════════
print("── commands 注册断言 ──")
import commands  # noqa: E402

names = commands.list_names()
for n in ["浏览器执行JS", "执行JS", "浏览器读取Cookie", "浏览器设置Cookie"]:
    check(n in names, "注册表包含新命令 '{}'".format(n))

_expected_5 = [
    ("打开网页", "浏览器打开指定URL", "网址"),
    ("浏览器点击", "点击页面元素(CSS选择器或text=)", "选择器"),
    ("浏览器输入", "在输入框中填入文本", "选择器, 文本"),
    ("等待元素", "等待页面元素出现或消失", "选择器, 超时秒数, 出现/消失"),
    ("浏览器截图", "截取页面或元素截图", "名称, 目标(page或选择器)"),
]
_reg_by_name = {n: (n, d, p) for (n, d, p, _h) in commands.list_all()}
for name, desc, params in _expected_5:
    check(_reg_by_name.get(name) == (name, desc, params),
          "既有命令注册逐字未变: {}".format(name))

# handler 绑定（需 engine 加载）
try:
    import engine  # noqa: E402
    _engine_ok = True
except Exception as e:  # pragma: no cover
    _engine_ok = False
    warn("无法 import engine，handler 绑定断言 SKIP: {}".format(e))

if _engine_ok:
    for n in ["浏览器执行JS", "执行JS", "浏览器读取Cookie", "浏览器设置Cookie"]:
        check(commands.get_handler(n) is not None, "get_handler('{}') 非空".format(n))
else:
    skip("handler 绑定断言（engine 未加载）")

print("─" * 60)
# ══════════════════════════════════════════════════════════════════
# 8. state 新键默认值
# ══════════════════════════════════════════════════════════════════
print("── state 新键默认值 ──")
import state  # noqa: E402

_expect_keys = {
    "browser_wait_timeout": 15.0,
    "browser_poll_interval": 0.2,
    "browser_full_page_screenshot": False,
    "browser_js_timeout": 15.0,
    "browser_retry": 1,
    "browser_retry_interval": 0.5,
    "browser_silent": False,
}
for k, v in _expect_keys.items():
    eq(getattr(state, k.upper(), "<missing>"), v, "state.{}".format(k.upper()))

print("─" * 60)
# ══════════════════════════════════════════════════════════════════
# 9. 降级断言（缺 playwright / 缺参 → return False + 错误码）
# ══════════════════════════════════════════════════════════════════
print("── 降级 / 失败短路断言 ──")
_fake_vars = {}
_orig_ev2 = bb._engine_variables
_orig_get_page = bb._get_page
bb._engine_variables = lambda: _fake_vars
try:
    # 9.1 缺参 → INVALID_ARGUMENT
    _fake_vars.clear()
    r = bb._browser_exec_js(_ArgsRow(["", "", ""]))
    check(r is False, "浏览器执行JS 缺参 return False")
    eq(_fake_vars.get("browser_last_error"), "INVALID_ARGUMENT", "缺参 → INVALID_ARGUMENT")

    # 9.2 后端不可用 → PW_NOT_INSTALLED（monkeypatch _get_page 为 None，避免真启动浏览器）
    bb._get_page = lambda: None
    _fake_vars.clear()
    r = bb._browser_exec_js(_ArgsRow(["1+1", "out", "否"]))
    check(r is False, "浏览器执行JS 后端不可用 return False")
    eq(_fake_vars.get("browser_last_error"), "PW_NOT_INSTALLED", "后端不可用 → PW_NOT_INSTALLED")

    _fake_vars.clear()
    r = bb._browser_cookie_get(_ArgsRow([]))
    check(r is False, "浏览器读取Cookie 后端不可用 return False")
    eq(_fake_vars.get("browser_last_error"), "PW_NOT_INSTALLED", "读Cookie → PW_NOT_INSTALLED")

    _fake_vars.clear()
    r = bb._browser_cookie_set(_ArgsRow(["", ""]))
    check(r is False, "浏览器设置Cookie 缺来源 return False")
    eq(_fake_vars.get("browser_last_error"), "INVALID_ARGUMENT", "设置Cookie 缺来源 → INVALID_ARGUMENT")
finally:
    bb._engine_variables = _orig_ev2
    bb._get_page = _orig_get_page

print("─" * 60)
if _FAILS:
    print("结果: FAIL ({} 项失败, {} 项警告)".format(len(_FAILS), len(_WARNS)))
    for m in _FAILS:
        print("  - " + m)
    sys.exit(1)
else:
    print("结果: PASS (0 失败, {} 项警告)".format(len(_WARNS)))
    sys.exit(0)
