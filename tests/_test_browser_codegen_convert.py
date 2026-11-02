# -*- coding: utf-8 -*-
"""tools/_test_browser_codegen_convert.py — 浏览器后端增强 P2-A「codegen→DSL 录制转换器」验收测试。

覆盖（无 playwright / 无真实浏览器亦可运行）：
  * codegen_to_dsl 各映射：page.goto / click / fill / press(拆两行) / locator() /
    get_by_role / get_by_text / evaluate / wait_for_timeout / screenshot /
    keyboard.press / frame_locator(两行) / expect_download / add_cookies
  * 无法识别调用 → warnings，且不产出对应 row
  * 样板代码（import/with sync_playwright/赋值/close）→ 不告警
  * 空输入 / None / 非文本 / 畸形输入边界
  * strict=True → CodegenConvertError(code=CONVERT_ERROR)
  * recorder.convert_and_append 薄适配：追加 state.recorded_actions
  * 命令注册断言：P2 命令存在且既有命令逐字仍在
  * handler 绑定断言（engine 已加载时）
  * state P2 新键默认值
  * AST 结构断言 + 模块级不存在 import playwright（懒加载契约）
  * 真实 codegen CLI / 真实浏览器部分 → SKIP 标注

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

print("─" * 60)


def rows_of(code):
    return bb.codegen_to_dsl(code)[0]


# ══════════════════════════════════════════════════════════════════
# 1. 基础映射
# ══════════════════════════════════════════════════════════════════
print("── codegen_to_dsl 基础映射 ──")
eq(rows_of('page.goto("https://a.com")'),
   [{"cmd": "打开网页", "args": ["https://a.com"]}], "goto → 打开网页")
eq(rows_of('page.click("#login-btn")'),
   [{"cmd": "浏览器点击", "args": ["#login-btn"]}], "click → 浏览器点击")
eq(rows_of('page.locator(".item").click()'),
   [{"cmd": "浏览器点击", "args": [".item"]}], "locator().click() → 浏览器点击")
eq(rows_of("page.fill('input[name=q]', \"hello\")"),
   [{"cmd": "浏览器输入", "args": ["input[name=q]", "hello"]}], "fill → 浏览器输入")
eq(rows_of('page.press("#search", "Enter")'),
   [{"cmd": "浏览器点击", "args": ["#search"]}, {"cmd": "按键", "args": ["Enter"]}],
   "press → 浏览器点击 + 按键（拆两行）")
eq(rows_of('page.get_by_role("button", name="登录").click()'),
   [{"cmd": "浏览器点击", "args": ["role:button[name=登录]"]}], "get_by_role → role: DSL")
eq(rows_of('page.get_by_text("提交").click()'),
   [{"cmd": "浏览器点击", "args": ["text:提交"]}], "get_by_text → text: DSL")
eq(rows_of('page.evaluate("() => document.title")'),
   [{"cmd": "浏览器执行JS", "args": ["() => document.title", "", "否"]}], "evaluate → 浏览器执行JS")
eq(rows_of('page.wait_for_timeout(500)'),
   [{"cmd": "等待", "args": ["0.5"]}], "wait_for_timeout(500) → 等待 0.5")
eq(rows_of('page.wait_for_timeout(1000)'),
   [{"cmd": "等待", "args": ["1"]}], "wait_for_timeout(1000) → 等待 1")
eq(rows_of('page.keyboard.press("Enter")'),
   [{"cmd": "按键", "args": ["Enter"]}], "keyboard.press → 按键")
eq(rows_of('with page.expect_download() as download_info:'),
   [{"cmd": "等待下载", "args": ["", "", "", ""]}], "expect_download → 等待下载")
eq(rows_of('page.locator("#list >> tag:li").first().click()'),
   [{"cmd": "浏览器点击", "args": ["#list >> tag:li >> first:"]}],
   "链式 first() → DSL 步骤")

print("── frame_locator（两行） ──")
eq(rows_of('page.frame_locator("#pay").get_by_role("button", name="提交").click()'),
   [{"cmd": "切换框架", "args": ["#pay"]},
    {"cmd": "浏览器点击", "args": ["role:button[name=提交]"]}],
   "frame_locator 内联 → 切换框架 + 子定位")

print("── screenshot / add_cookies ──")
eq(rows_of('page.screenshot(path="D:/out/shot.png")'),
   [{"cmd": "浏览器截图", "args": ["shot", "page", "D:/out/shot.png"]}],
   "screenshot(path=) → 浏览器截图")
_ac = rows_of('context.add_cookies([{"name": "sid", "value": "v", "domain": ".a.com", "path": "/"}])')
eq(len(_ac), 1, "add_cookies 产出 1 行")
eq(_ac[0]["cmd"] if _ac else None, "浏览器设置Cookie", "add_cookies → 浏览器设置Cookie")
try:
    _parsed = json.loads(_ac[0]["args"][0])
    eq(_parsed[0]["name"], "sid", "add_cookies args[0] 为合法 JSON 列表")
except Exception as e:
    fail("add_cookies args[0] 非合法 JSON: {}".format(e))

print("─" * 60)
# ══════════════════════════════════════════════════════════════════
# 2. 警告 / 样板忽略 / 边界
# ══════════════════════════════════════════════════════════════════
print("── warnings / 样板 / 边界 ──")
_r, _w = bb.codegen_to_dsl('page.unknown_thing("x")')
eq(_r, [], "未知调用不产出 row")
eq(len(_w), 1, "未知调用进入 warnings")
check("unknown_thing" in _w[0], "warning 保留原始语句: {}".format(_w[0] if _w else ""))

_boiler = (
    "import re\n"
    "from playwright.sync_api import Playwright, sync_playwright, expect\n"
    "def run(playwright: Playwright) -> None:\n"
    "    browser = playwright.chromium.launch(headless=False)\n"
    "    context = browser.new_context()\n"
    "    page = context.new_page()\n"
    "    context.close()\n"
    "    browser.close()\n"
)
_r, _w = bb.codegen_to_dsl(_boiler)
eq(_r, [], "样板代码不产出 row")
eq(_w, [], "样板代码不产生 warning")

_r, _w = bb.codegen_to_dsl("")
eq((_r, _w), ([], []), "空输入 → ([], [])")
eq(bb.codegen_to_dsl(None), ([], []), "None 输入 → ([], [])")
_r, _w = bb.codegen_to_dsl(123)
eq((_r, _w), ([], ["123"]), "非文本输入 → 记为 warning")

_r, _w = bb.codegen_to_dsl("page.fill(")
eq(_r, [], "畸形输入不产出 row")
eq(len(_w), 1, "畸形输入进入 warnings")

print("── strict 模式 ──")
try:
    bb.codegen_to_dsl('page.mystery()', strict=True)
    fail("strict 应抛 CodegenConvertError")
except bb.CodegenConvertError as e:
    eq(e.code, "CONVERT_ERROR", "strict 抛出 CONVERT_ERROR")
except Exception as e:
    fail("strict 抛出非预期异常: {!r}".format(e))
_r, _w = bb.codegen_to_dsl('page.goto("https://a.com")', strict=True)
eq((_r, _w), ([{"cmd": "打开网页", "args": ["https://a.com"]}], []), "strict 干净输入正常返回")

print("─" * 60)
# ══════════════════════════════════════════════════════════════════
# 3. recorder 薄适配
# ══════════════════════════════════════════════════════════════════
print("── recorder.convert_and_append ──")
import recorder  # noqa: E402

_orig_actions = state.recorded_actions
try:
    state.recorded_actions = []
    added, wlist = recorder.convert_and_append('page.goto("https://a.com")\npage.click("#a")')
    eq(added, 2, "convert_and_append 追加 2 条")
    eq(len(state.recorded_actions), 2, "recorded_actions 长度 2")
    eq(state.recorded_actions[0].cmd_type, "打开网页", "第 1 条命令 = 打开网页")
    eq(state.recorded_actions[0].args[0], "https://a.com", "第 1 条参数 = URL")
    eq(state.recorded_actions[1].cmd_type, "浏览器点击", "第 2 条命令 = 浏览器点击")
    eq(wlist, [], "无未识别语句")

    state.recorded_actions = []
    added, wlist = recorder.convert_and_append('page.bad_call()')
    eq(added, 0, "未知调用追加 0 条")
    eq(len(wlist), 1, "未知调用产生 1 条 warning")

    # 便捷包装不抛异常
    state.recorded_actions = []
    added, wlist = recorder.record_browser_codegen('page.goto("https://b.com")')
    eq(added, 1, "record_browser_codegen 追加 1 条")
finally:
    state.recorded_actions = _orig_actions

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
for fn in ["codegen_to_dsl", "_codegen_statement", "_codegen_decompose", "_codegen_build_selector",
           "parse_cdp_endpoint", "plan_session_switch", "parse_resource_types", "dedup_key",
           "match_response", "match_url", "export_packets", "_start_listen", "_stop_listen",
           "_collect_packets", "_browser_connect_cdp", "_browser_start_listen",
           "_browser_wait_packets", "_browser_stop_listen", "_browser_record"]:
    check(fn in func_names, "存在函数定义 {}".format(fn))

print("─" * 60)
# ══════════════════════════════════════════════════════════════════
# 5. commands 注册 / handler 绑定 / state 新键
# ══════════════════════════════════════════════════════════════════
print("── commands 注册 / handler 绑定 / state 新键 ──")
import commands  # noqa: E402

names = commands.list_names()
_P2_NAMES = ["连接已开浏览器", "接管浏览器", "开始监听", "等待数据包", "停止监听", "启动浏览器录制"]
_PREV_NAMES = ["打开网页", "浏览器点击", "浏览器输入", "等待元素", "浏览器截图",
               "浏览器执行JS", "执行JS", "浏览器读取Cookie", "浏览器设置Cookie",
               "切换框架", "返回主框架", "新建标签页", "切换标签页", "关闭标签页",
               "等待下载", "浏览器上传"]
for n in _P2_NAMES:
    check(n in names, "注册表包含 P2 新命令 '{}'".format(n))
for n in _PREV_NAMES:
    check(n in names, "既有命令仍存在 '{}'".format(n))

try:
    import engine  # noqa: E402
    _engine_ok = True
except Exception as e:  # pragma: no cover
    _engine_ok = False
    warn("无法 import engine，handler 绑定断言 SKIP: {}".format(e))
if _engine_ok:
    for n in _P2_NAMES:
        check(commands.get_handler(n) is not None, "get_handler('{}') 非空".format(n))
    check(commands.get_handler("连接已开浏览器") is commands.get_handler("接管浏览器"),
          "连接已开浏览器 与 接管浏览器 绑定同一 handler")
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
skip("真实 playwright codegen CLI 录制（需 playwright CLI + 图形界面）—— 未在本环境执行")
skip("启动浏览器录制 成功路径（需 playwright CLI）—— 未在本环境执行")

print("─" * 60)
if _FAILS:
    print("结果: FAIL ({} 项失败, {} 项警告)".format(len(_FAILS), len(_WARNS)))
    for m in _FAILS:
        print("  - " + m)
    sys.exit(1)
else:
    print("结果: PASS (0 失败, {} 项警告)".format(len(_WARNS)))
    sys.exit(0)
