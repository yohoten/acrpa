# -*- coding: utf-8 -*-
"""tools/_test_browser_frame_tab.py — 浏览器后端增强 P1-B「iframe + 标签页」验收测试。

覆盖（无 playwright / 无真实浏览器亦可运行）：
  * parse_frame_selector 纯函数：main/MAIN/主文档/顶层/top → ("main", None)；
    纯数字 → ("index", n)；其余 → ("locator", spec)
  * parse_tab_selector 纯函数：纯数字 → ("index", n)；其余 → ("match", text)
  * session 句柄管理纯逻辑（Fake page，不启动浏览器）：
      - _register_page 追加 _pages / 置活动 / 挂载 download 监听（去重）
      - _active_top_page / _switch_tab（索引 / 标题 / URL 匹配 / 未命中）
      - _close_tab（关活动页 → 回退最后剩余页；清空 → _page=None）
      - _switch_frame("main") 复位；_switch_frame 非法/越界 → FRAME_NOT_FOUND
  * 命令注册断言：切换框架/返回主框架/新建标签页/切换标签页/关闭标签页 在 list_names()
  * handler 绑定断言（engine 已加载时）：上述命令 handler 非 None
  * 需真实浏览器进入/返回 iframe、标签页新建/切换/关闭 → SKIP 标注
  * AST 结构断言：parse_frame_selector / parse_tab_selector / _switch_frame / get_top_page 存在
  * 模块级不存在 import playwright（懒加载契约）

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

_pw_loaded = any(m == "playwright" or m.startswith("playwright.") for m in sys.modules)
check(not _pw_loaded, "import browser_backend 未触发 playwright 导入（懒加载契约）")

print("─" * 60)
# ══════════════════════════════════════════════════════════════════
# 1. parse_frame_selector / parse_tab_selector（纯函数）
# ══════════════════════════════════════════════════════════════════
print("── parse_frame_selector ──")
for raw in ["main", "MAIN", "Main", "主文档", "顶层", "top", "TOP"]:
    eq(bb.parse_frame_selector(raw), ("main", None), "parse_frame_selector({!r})".format(raw))
eq(bb.parse_frame_selector("2"), ("index", 2), "纯数字 → index")
eq(bb.parse_frame_selector("0"), ("index", 0), "0 → index")
eq(bb.parse_frame_selector("#payment-iframe"), ("locator", "#payment-iframe"), "DSL → locator")
eq(bb.parse_frame_selector("tag:iframe@name=pay"), ("locator", "tag:iframe@name=pay"),
   "tag: DSL → locator")
eq(bb.parse_frame_selector(None), ("locator", ""), "None → locator('')")
eq(bb.parse_frame_selector("  main  "), ("main", None), "含空白 main")

print("── parse_tab_selector ──")
eq(bb.parse_tab_selector("0"), ("index", 0), "纯数字 0 → index")
eq(bb.parse_tab_selector("12"), ("index", 12), "纯数字 12 → index")
eq(bb.parse_tab_selector("报表"), ("match", "报表"), "标题 → match")
eq(bb.parse_tab_selector("https://example.com/report"), ("match", "https://example.com/report"),
   "URL → match")
eq(bb.parse_tab_selector("-1"), ("match", "-1"), "负号非纯数字 → match")
eq(bb.parse_tab_selector(None), ("match", ""), "None → match('')")

print("─" * 60)
# ══════════════════════════════════════════════════════════════════
# 2. session 句柄管理纯逻辑（Fake page）
# ══════════════════════════════════════════════════════════════════
print("── session 句柄管理（Fake page，无浏览器） ──")


class _FakePage(object):
    """最小 page 替身：仅实现句柄管理所需接口。"""

    def __init__(self, title="", url="about:blank"):
        self._title = title
        self.url = url
        self.closed = False
        self.handlers = []
        self.context = None
        self.frames = []

    def title(self):
        return self._title

    def on(self, evt, cb):
        self.handlers.append((evt, cb))

    def close(self):
        self.closed = True


_orig_get_page = bb._get_page
_orig_pages = list(bb._pages)
_orig_active = bb._active_index
_orig_page = bb._page
_orig_frame = bb._frame
_orig_hooked = set(bb._hooked_pages)
bb._get_page = lambda: None  # 阻止真实启动
bb._pages[:] = []
bb._hooked_pages.clear()
bb._active_index = -1
bb._page = None
bb._frame = None
try:
    p0 = _FakePage("首页", "http://a.test/home")
    p1 = _FakePage("报表", "http://a.test/report")

    bb._register_page(p0)
    eq(len(bb._pages), 1, "_register_page 追加第 1 页")
    eq(bb._active_top_page() is p0, True, "第 1 页成为活动页")
    eq(bb._active_index, 0, "_active_index=0")

    bb._register_page(p1)
    eq(len(bb._pages), 2, "追加第 2 页")
    eq(bb._active_top_page() is p1, True, "第 2 页成为活动页")

    # download 监听挂载（去重）
    eq(("download", bb._on_download) in p0.handlers, True, "p0 挂载 download 监听")
    bb._register_page(p0)  # 重复登记不应重复挂载/重复入 _pages
    eq(len(bb._pages), 2, "重复 _register_page 不新增页")
    eq(sum(1 for h in p0.handlers if h[0] == "download"), 1, "download 监听去重（仅 1 次）")

    # _switch_tab：索引
    okk, _c, _d = bb._switch_tab("0")
    eq(okk, True, "_switch_tab('0') 成功")
    eq(bb._active_top_page() is p0, True, "活动页切到索引 0")
    # 标题匹配
    okk, _c, _d = bb._switch_tab("报表")
    eq(okk, True, "_switch_tab('报表') 成功")
    eq(bb._active_top_page() is p1, True, "标题匹配切到报表页")
    # URL 匹配
    okk, _c, _d = bb._switch_tab("http://a.test/home")
    eq(okk, True, "_switch_tab(URL) 成功")
    eq(bb._active_top_page() is p0, True, "URL 匹配切到首页")
    # 未命中
    okk, code, _d = bb._switch_tab("不存在的标题")
    eq(okk, False, "_switch_tab(未命中) 失败")
    eq(code, "TAB_NOT_FOUND", "未命中 → TAB_NOT_FOUND")
    okk, code, _d = bb._switch_tab("9")
    eq(okk, False, "_switch_tab(越界索引) 失败")
    eq(code, "TAB_NOT_FOUND", "越界索引 → TAB_NOT_FOUND")

    # _close_tab：关活动页（p0 为活动）→ 回退最后剩余页
    okk, _c, _d = bb._close_tab("")
    eq(okk, True, "_close_tab('') 成功")
    eq(p0.closed, True, "活动页 p0 已关闭")
    eq(len(bb._pages), 1, "剩余 1 页")
    eq(bb._active_top_page() is p1, True, "回退到最后一个剩余页 p1")

    # 关最后一页 → 空
    okk, _c, _d = bb._close_tab("0")
    eq(okk, True, "_close_tab('0') 成功")
    eq(len(bb._pages), 0, "_pages 清空")
    eq(bb._page, None, "无剩余页 _page=None")
    eq(bb._active_index, -1, "_active_index=-1")
    okk, code, _d = bb._close_tab("")
    eq(okk, False, "空列表关页失败")
    eq(code, "TAB_NOT_FOUND", "空列表关页 → TAB_NOT_FOUND")

    # _switch_frame：main 复位（含 Fake page，无浏览器）
    bb._register_page(p0)
    bb._frame = object()  # 模拟已进入某 frame
    okk, _c, _d = bb._switch_frame("main")
    eq(okk, True, "_switch_frame('main') 成功")
    eq(bb._frame, None, "main → _frame=None")

    # 越界索引 frame → FRAME_NOT_FOUND
    okk, code, _d = bb._switch_frame("99")
    eq(okk, False, "_switch_frame(越界索引) 失败")
    eq(code, "FRAME_NOT_FOUND", "越界索引 → FRAME_NOT_FOUND")

    # DSL frame 定位（Fake page 无 locator）→ FRAME_NOT_FOUND（不冒泡异常）
    okk, code, _d = bb._switch_frame("#no-iframe")
    eq(okk, False, "_switch_frame(无效 DSL) 失败")
    eq(code, "FRAME_NOT_FOUND", "定位失败 → FRAME_NOT_FOUND")
finally:
    bb._get_page = _orig_get_page
    bb._pages[:] = _orig_pages
    bb._active_index = _orig_active
    bb._page = _orig_page
    bb._frame = _orig_frame
    bb._hooked_pages.clear()
    bb._hooked_pages.update(_orig_hooked)

skip("真实浏览器进入/返回 iframe（需 playwright + Chromium）—— 未在本环境执行")
skip("真实浏览器标签页新建/切换/关闭（需 playwright + Chromium）—— 未在本环境执行")

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
for fn in ["parse_frame_selector", "parse_tab_selector", "_switch_frame", "get_top_page",
           "get_active_page", "_register_page", "_switch_tab", "_close_tab",
           "_browser_switch_frame", "_browser_main_frame", "_browser_new_tab",
           "_browser_switch_tab", "_browser_close_tab"]:
    check(fn in func_names, "存在函数定义 {}".format(fn))

print("─" * 60)
# ══════════════════════════════════════════════════════════════════
# 4. commands 注册 + handler 绑定
# ══════════════════════════════════════════════════════════════════
print("── commands 注册 / handler 绑定 ──")
import commands  # noqa: E402

names = commands.list_names()
_p1_frame_tab = ["切换框架", "返回主框架", "新建标签页", "切换标签页", "关闭标签页"]
for n in _p1_frame_tab:
    check(n in names, "注册表包含新命令 '{}'".format(n))

try:
    import engine  # noqa: E402
    _engine_ok = True
except Exception as e:  # pragma: no cover
    _engine_ok = False
    warn("无法 import engine，handler 绑定断言 SKIP: {}".format(e))

if _engine_ok:
    for n in _p1_frame_tab:
        check(commands.get_handler(n) is not None, "get_handler('{}') 非空".format(n))
else:
    skip("handler 绑定断言（engine 未加载）")

print("─" * 60)
if _FAILS:
    print("结果: FAIL ({} 项失败, {} 项警告)".format(len(_FAILS), len(_WARNS)))
    for m in _FAILS:
        print("  - " + m)
    sys.exit(1)
else:
    print("结果: PASS (0 失败, {} 项警告)".format(len(_WARNS)))
    sys.exit(0)
