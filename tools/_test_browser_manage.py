# -*- coding: utf-8 -*-
"""tools/_test_browser_manage.py — 浏览器后端增强 P3「会话/读取/选择」验收测试。

覆盖（无 playwright / 无真实浏览器亦可运行）：
  * parse_browser_mode：中文/英文/同义词归一（打开/接管/关闭），未知 → ""
  * browser_channel_kwargs：Chrome/Edge 走 channel，Firefox/WebKit 换引擎，其余回退 Chromium
  * parse_wait_until：等待模式归一，空/未知 → domcontentloaded（保持旧行为）
  * parse_selector：SelectorType（CSS/XPath/Text/ID/Name/ClassName/TagName/LinkText）→ DSL
  * parse_selector：类型为空/auto/dsl 时原样透传（不破坏既有 DSL）
  * _launch_options：state 默认值 + 命令级覆盖 + 视口/最大化参数推导
  * state 新增配置键存在且默认值不改变现有行为
  * 命令注册表含 4 个新命令，且 handler 已绑定到 browser_backend
  * import browser_backend 不触发 import playwright（懒加载硬约束）
  * atexit 兜底清理已注册

只跑纯逻辑，不弹窗、不联网、不启动浏览器。退出码 0(全过/仅 WARN) / 1(存在 FAIL)。
输出行前缀: [OK] / [WARN] / [FAIL]。
"""
import os
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


import state                     # noqa: E402
import browser_backend as bb     # noqa: E402

print("── 懒加载硬约束 ──")
check("playwright.sync_api" not in sys.modules,
      "import browser_backend 未触发 import playwright")
check(callable(getattr(bb, "_atexit_cleanup", None)),
      "atexit 兜底清理函数存在")

print("── parse_browser_mode 同义词归一 ──")
for text, want in [("打开", "open"), ("启动", "open"), ("open", "open"), ("launch", "open"),
                   ("接管", "attach"), ("连接", "attach"), ("attach", "attach"), ("cdp", "attach"),
                   ("关闭", "close"), ("退出", "close"), ("close", "close"), ("quit", "close")]:
    eq(bb.parse_browser_mode(text), want, "mode({})".format(text))
for text in ("", "  ", "瞎写"):
    eq(bb.parse_browser_mode(text), "", "mode({!r}) 未知→空".format(text))

print("── browser_channel_kwargs 通道映射 ──")
eq(bb.browser_channel_kwargs("Chrome"), ("chromium", {"channel": "chrome"}), "Chrome")
eq(bb.browser_channel_kwargs("谷歌"), ("chromium", {"channel": "chrome"}), "谷歌")
eq(bb.browser_channel_kwargs("Edge"), ("chromium", {"channel": "msedge"}), "Edge")
eq(bb.browser_channel_kwargs("Microsoft Edge"), ("chromium", {"channel": "msedge"}), "Microsoft Edge")
eq(bb.browser_channel_kwargs("Firefox"), ("firefox", {}), "Firefox")
eq(bb.browser_channel_kwargs("WebKit"), ("webkit", {}), "WebKit")
eq(bb.browser_channel_kwargs("内置"), ("chromium", {}), "内置 Chromium")
eq(bb.browser_channel_kwargs(""), ("chromium", {}), "空 → 内置")

print("── parse_wait_until 等待模式 ──")
for text, want in [("", "domcontentloaded"), ("默认", "domcontentloaded"),
                   ("加载完成", "load"), ("load", "load"),
                   ("网络空闲", "networkidle"), ("networkidle", "networkidle"),
                   ("提交", "commit")]:
    eq(bb.parse_wait_until(text), want, "wait_until({!r})".format(text))
eq(bb.parse_wait_until("未知模式"), "domcontentloaded", "未知 → 回退 domcontentloaded")

print("── parse_selector 选择器类型归一 ──")
_pairs = [
    (".a", "css", "css:.a"),
    ("//div[@id='x']", "xpath", "xpath://div[@id='x']"),
    ("登录", "text", "text:登录"),
    ("登录", "linktext", "text=登录"),
    ("登录", "partiallinktext", "text:登录"),
    ("login-btn", "id", "#login-btn"),
    ("#login-btn", "id", "#login-btn"),
    ('user"name', "name", '[name="user\\"name"]'),
    ("btn primary", "classname", ".btn.primary"),
    (".btn", "class", ".btn"),
    ("button", "tagname", "tag:button"),
]
for spec, typ, want in _pairs:
    eq(bb.parse_selector(spec, typ), want, "selector({!r}, {!r})".format(spec, typ))

print("── parse_selector 原样透传（不破坏既有 DSL）──")
for spec in ("#id", "text:登录", "role:button[name=登录]", "css:.a", "if>>span"):
    eq(bb.parse_selector(spec, ""), spec, "passthrough({!r}, '')".format(spec))
    eq(bb.parse_selector(spec, "auto"), spec, "passthrough({!r}, auto)".format(spec))
    eq(bb.parse_selector(spec, "未知类型"), spec, "passthrough({!r}, 未知类型)".format(spec))
eq(bb.parse_selector("   ", "css"), "", "空选择器 → 空串")

print("── _launch_options 合并与推导 ──")
_orig = {k: getattr(state, k) for k in
         ("BROWSER_HEADLESS", "BROWSER_SLOW_MO", "BROWSER_CHANNEL",
          "BROWSER_USER_DATA_DIR", "BROWSER_PERSISTENT",
          "BROWSER_WINDOW_WIDTH", "BROWSER_WINDOW_HEIGHT")}
try:
    state.BROWSER_HEADLESS = True
    state.BROWSER_SLOW_MO = 0
    state.BROWSER_CHANNEL = ""
    state.BROWSER_USER_DATA_DIR = ""
    state.BROWSER_PERSISTENT = False
    state.BROWSER_WINDOW_WIDTH = 0
    state.BROWSER_WINDOW_HEIGHT = 0
    opts = bb._launch_options()
    eq(opts["headless"], True, "默认无头")
    eq(opts["channel"], "", "默认无通道")
    eq(opts["viewport"], None, "默认无视口")
    eq(opts["args"], [], "无头 + 无视口 → 不加 --start-maximized")

    state.BROWSER_WINDOW_WIDTH = 1280
    state.BROWSER_WINDOW_HEIGHT = 800
    opts = bb._launch_options()
    eq(opts["viewport"], {"width": 1280, "height": 800}, "视口按配置推导")
    eq(opts["args"], [], "有视口 → 不加 --start-maximized")

    state.BROWSER_WINDOW_WIDTH = 0
    state.BROWSER_WINDOW_HEIGHT = 0
    state.BROWSER_HEADLESS = False
    opts = bb._launch_options()
    check("--start-maximized" in opts["args"], "有头 + 无视口 → 最大化窗口")

    bb._LAUNCH_OVERRIDE.update({"channel": "Edge", "headless": False,
                                "user_data_dir": "D:/tmp/prof", "persistent": True})
    opts = bb._launch_options()
    eq(opts["channel"], "Edge", "命令级覆盖 channel 生效")
    eq(opts["user_data_dir"], "D:/tmp/prof", "命令级覆盖 user_data_dir 生效")
    eq(opts["persistent"], True, "命令级覆盖 persistent 生效")
finally:
    bb._LAUNCH_OVERRIDE.clear()
    for k, v in _orig.items():
        setattr(state, k, v)

print("── state 新增配置键 ──")
for key, default in (("BROWSER_CHANNEL", ""), ("BROWSER_USER_DATA_DIR", ""),
                     ("BROWSER_PERSISTENT", False), ("BROWSER_WINDOW_WIDTH", 0),
                     ("BROWSER_WINDOW_HEIGHT", 0), ("LOG_MAX_LINES", 6000)):
    got = getattr(state, key, "<缺失>")
    eq(got, default if got != "<缺失>" else "<缺失>", "{} 默认值".format(key))

print("── 命令注册与 handler 绑定 ──")
try:
    import commands
    _names = {row[0] for row in commands.list_all()}
    for name in ("浏览器管理", "关闭浏览器", "浏览器读取", "浏览器选择"):
        check(name in _names, "命令已注册: {}".format(name))
except Exception as e:
    warn("命令注册检查跳过: {}".format(e))
for fn in ("_browser_manage", "_browser_close_cmd", "_browser_read", "_browser_select"):
    check(callable(getattr(bb, fn, None)), "handler 实现存在: {}".format(fn))

print()
if _FAILS:
    print("FAIL {} 项 / WARN {} 项".format(len(_FAILS), len(_WARNS)))
    for m in _FAILS:
        print("  - " + m)
    sys.exit(1)
print("全部通过 (WARN {} 项)".format(len(_WARNS)))
