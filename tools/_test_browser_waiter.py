# -*- coding: utf-8 -*-
"""tools/_test_browser_waiter.py — 浏览器后端增强 P0「waiter 等待层」验收测试。

覆盖（无 playwright / 无真实浏览器亦可运行）：
  * parse_wait_state：中文/英文/同义词归一（出现/可见/visible → visible 等）
  * parse_wait_state：未知 / 空 → ""（由调用方回退/报错）
  * WAIT_STATES 枚举内容与设计文档一致
  * wait_for 纯逻辑分支（page 缺失 / plan 无效 / 预留状态 / 未知状态）无需浏览器
  * AST 结构断言：parse_wait_state / wait_for / WAIT_STATES 存在
  * 等待元素 facade 参数解析：旧 2/3 参形态默认语义（出现/消失）不破坏

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

print("── parse_wait_state 同义词归一 ──")
_map = [
    ("出现", "visible"), ("可见", "visible"), ("显示", "visible"), ("visible", "visible"),
    ("消失", "hidden"), ("隐藏", "hidden"), ("hidden", "hidden"),
    ("存在", "attached"), ("附加", "attached"), ("attached", "attached"),
    ("移除", "detached"), ("删除", "detached"), ("detached", "detached"),
    ("可点击", "clickable"), ("clickable", "clickable"),
    ("可用", "enabled"), ("enabled", "enabled"),
    ("URL变动", "url"), ("网址变动", "url"), ("url", "url"),
    ("标题变动", "title"), ("title", "title"),
    ("下载开始", "download"), ("download", "download"),
    ("导航", "navigation"), ("navigation", "navigation"),
]
for raw, want in _map:
    eq(bb.parse_wait_state(raw), want, "parse_wait_state({!r})".format(raw))

# 大小写不敏感（英文）
eq(bb.parse_wait_state("VISIBLE"), "visible", "英文大小写不敏感")

print("─" * 60)
print("── parse_wait_state 边界（未知/空 → ""） ──")
for raw in ["", "   ", "不存在的状态", "？？？"]:
    eq(bb.parse_wait_state(raw), "", "parse_wait_state({!r}) → ''".format(raw))
eq(bb.parse_wait_state(None), "", "parse_wait_state(None) → ''")

print("─" * 60)
print("── WAIT_STATES 枚举 ──")
_eq_states = ("visible", "hidden", "attached", "detached",
              "clickable", "enabled", "url", "title", "download", "navigation")
eq(tuple(bb.WAIT_STATES), _eq_states, "WAIT_STATES 与设计文档一致")

print("─" * 60)
print("── wait_for 纯逻辑分支（无需浏览器） ──")

# page 缺失 → PW_NOT_INSTALLED
r = bb.wait_for(None, {"engine": "playwright", "raw": "#x"}, state="visible")
eq(r[0], False, "wait_for(page=None).ok")
eq(r[1], "PW_NOT_INSTALLED", "wait_for(page=None).code")

# plan 无效 → INVALID_LOCATOR（page 传非 None 占位对象，走 plan 校验分支）
_dummy_page = object()
r = bb.wait_for(_dummy_page, {"engine": "error", "code": "INVALID_LOCATOR", "message": "bad"},
                state="visible")
eq(r[0], False, "wait_for(error plan).ok")
eq(r[1], "INVALID_LOCATOR", "wait_for(error plan).code")

# 预留状态 download/navigation → INVALID_ARGUMENT
for st in ("download", "navigation"):
    r = bb.wait_for(_dummy_page, {"engine": "playwright", "raw": "#x"}, state=st)
    eq(r[0], False, "wait_for(state={}).ok".format(st))
    eq(r[1], "INVALID_ARGUMENT", "wait_for(state={}).code（P0 预留）".format(st))

# 未知状态 → INVALID_ARGUMENT
r = bb.wait_for(_dummy_page, {"engine": "playwright", "raw": "#x"}, state="bogus")
eq(r[0], False, "wait_for(unknown state).ok")
eq(r[1], "INVALID_ARGUMENT", "wait_for(unknown state).code")

print("─" * 60)
print("── 等待元素 facade 参数解析（纯逻辑，无浏览器） ──")


class _Cell(object):
    __slots__ = ("value",)

    def __init__(self, v):
        self.value = v


class _XRow(object):
    def __init__(self, values):
        self.cells = [_Cell(v) for v in values]

    def __getitem__(self, i):
        return self.cells[i] if i < len(self.cells) else _Cell(None)


class _ArgsRow(object):
    def __init__(self, args):
        self.args = args


# 用 monkeypatch 把 _get_page 置 None，验证旧/新参数形态都能进入正确分支
_orig_get_page = bb._get_page
_fake_vars = {}
_orig_ev = bb._engine_variables
bb._engine_variables = lambda: _fake_vars
try:
    # 旧 2 参形态: 等待元素, .result, 10  (状态缺省 → visible)
    _fake_vars.clear()
    bb._get_page = lambda: None
    bb._browser_wait_element(_ArgsRow([".result", "10"]))
    # 后端不可用会走 PW_NOT_INSTALLED（参数解析无异常即视为通过）
    eq(_fake_vars.get("browser_last_error"), "PW_NOT_INSTALLED",
       "旧 2 参形态进入参数解析（后端不可用时 PW_NOT_INSTALLED）")

    # 缺选择器 → INVALID_ARGUMENT（先于后端检查）
    _fake_vars.clear()
    bb._browser_wait_element(_ArgsRow(["", "10", "出现"]))
    eq(_fake_vars.get("browser_last_error"), "INVALID_ARGUMENT", "缺选择器 → INVALID_ARGUMENT")
finally:
    bb._get_page = _orig_get_page
    bb._engine_variables = _orig_ev

print("─" * 60)
print("── AST 结构断言 ──")
with open(os.path.join(SRC, "browser_backend.py"), "r", encoding="utf-8") as f:
    tree = ast.parse(f.read())
func_names = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
for fn in ["parse_wait_state", "wait_for", "_browser_wait_element"]:
    check(fn in func_names, "存在函数定义 {}".format(fn))
const_names = {t.id for n in tree.body if isinstance(n, ast.Assign)
               for t in n.targets if isinstance(t, ast.Name)}
check("WAIT_STATES" in const_names, "模块级常量 WAIT_STATES 存在")

print("─" * 60)
if _FAILS:
    print("结果: FAIL ({} 项失败, {} 项警告)".format(len(_FAILS), len(_WARNS)))
    for m in _FAILS:
        print("  - " + m)
    sys.exit(1)
else:
    print("结果: PASS (0 失败, {} 项警告)".format(len(_WARNS)))
    sys.exit(0)
