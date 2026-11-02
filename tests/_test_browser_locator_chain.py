# -*- coding: utf-8 -*-
"""tools/_test_browser_locator_chain.py — 浏览器后端增强 P1-A「定位 DSL 链式/相对定位」验收测试。

覆盖（无 playwright / 无真实浏览器亦可运行）：
  * parse_locator 链式 `>>`：子代定位 / nth / first / last / parent / next / prev /
    filter / has
  * 索引糖：first: → nth:0；last: → nth:-1；nth:N/index:N → nth:N（0 基，可负）
  * `@@` 与 `>>` 组合：A@@B>>C ≡ (A@@B)>>C（@@ 优先级更高）
  * 非法边界 → INVALID_LOCATOR：无基串 nth:2 / parent: / child: / first:、
    `#a >>`（空步骤）、`>> div`（缺基串）、`#a >> nth:x`（非整数）、
    `#a >> filter:`（空文本）、`tag:div >> has:nth:2`（递归非法）
  * 兼容回归：`#a > div`（单个 `>`）、`div > span` 仍走 CSS 直通（R3 不误伤）
  * P0 既有 DSL 回归：text:/tag:/@@/role:/label:/placeholder:/testid: 语义不变
  * AST 结构断言：存在函数定义 _parse_chain / _parse_step / build_locator / _apply_chain_step
  * 结构断言：模块级不存在 import playwright（懒加载契约）

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
# 1. 链式 `>>` 基本解析
# ══════════════════════════════════════════════════════════════════
print("── parse_locator 链式 `>>` ──")

p = bb.parse_locator("#a >> div")
eq(p.get("engine"), "playwright", "#a >> div 基串 engine")
eq(p.get("raw"), "#a", "#a >> div 基串 raw")
eq(p.get("chain"), [{"op": "child", "plan": {"engine": "playwright", "raw": "div"}}],
   "#a >> div → chain=[child(div)]")

p = bb.parse_locator("tag:li >> nth:2")
eq(p.get("raw"), "li", "tag:li >> nth:2 基串 raw")
eq(p.get("chain"), [{"op": "nth", "index": 2}], "tag:li >> nth:2 → nth:2")

p = bb.parse_locator("tag:li >> index:3")
eq(p.get("chain"), [{"op": "nth", "index": 3}], "index:N 等价 nth:N")

p = bb.parse_locator("#child >> parent:")
eq(p.get("chain"), [{"op": "parent"}], "#child >> parent: → parent")

p = bb.parse_locator("#a >> next:")
eq(p.get("chain"), [{"op": "next"}], "#a >> next: → next")

p = bb.parse_locator("#a >> prev:")
eq(p.get("chain"), [{"op": "prev"}], "#a >> prev: → prev")

p = bb.parse_locator("#form >> child:input")
eq(p.get("chain"), [{"op": "child", "plan": {"engine": "playwright", "raw": "input"}}],
   "#form >> child:input → child(input)")

p = bb.parse_locator("tag:li >> filter:完成")
eq(p.get("chain"), [{"op": "filter", "text": "完成"}], "tag:li >> filter:完成")

p = bb.parse_locator("tag:div >> has:tag:a")
eq(p.get("chain"), [{"op": "has", "plan": {"engine": "playwright", "raw": "a"}}],
   "tag:div >> has:tag:a → has(a)")

# 非算子步骤默认按“子代定位”（等价 child:），与文档示例一致
p = bb.parse_locator("#a >> 某段文字")
eq(p.get("chain"),
   [{"op": "child", "plan": {"engine": "get_by_text", "text": "某段文字", "exact": False}}],
   "非算子步骤 → 子代文本定位（get_by_text）")

print("─" * 60)
# ══════════════════════════════════════════════════════════════════
# 2. 索引糖 first: / last: 与多级链
# ══════════════════════════════════════════════════════════════════
print("── 索引糖 / 多级链 ──")

p = bb.parse_locator("tag:li >> first:")
eq(p.get("chain"), [{"op": "nth", "index": 0}], "first: → nth:0")

p = bb.parse_locator("tag:li >> last:")
eq(p.get("chain"), [{"op": "nth", "index": -1}], "last: → nth:-1")

p = bb.parse_locator("#table >> tag:tr >> nth:1")
eq(p.get("raw"), "#table", "#table >> tag:tr >> nth:1 基串 raw")
eq(p.get("chain"),
   [{"op": "child", "plan": {"engine": "playwright", "raw": "tr"}},
    {"op": "nth", "index": 1}],
   "#table >> tag:tr >> nth:1 两级 chain")

p = bb.parse_locator("#table >> tag:tr >> first:")
eq(p.get("chain"),
   [{"op": "child", "plan": {"engine": "playwright", "raw": "tr"}},
    {"op": "nth", "index": 0}],
   "文档示例 #table >> tag:tr >> first:")

print("─" * 60)
# ══════════════════════════════════════════════════════════════════
# 3. `@@` 与 `>>` 组合（@@ 优先级高于 >>）
# ══════════════════════════════════════════════════════════════════
print("── `@@` + `>>` 组合 ──")

p = bb.parse_locator("tag:button@@text:提交 >> nth:0")
eq(p.get("engine"), "playwright", "A@@B>>C 基串 engine")
eq(p.get("raw"), "button", "A@@B>>C 基串 raw（@@ 已先求值）")
eq(p.get("has_text"), "提交", "A@@B>>C 保留 @@ 的 has_text")
eq(p.get("chain"), [{"op": "nth", "index": 0}], "A@@B>>C chain=[nth:0]")

print("─" * 60)
# ══════════════════════════════════════════════════════════════════
# 4. 非法边界 → INVALID_LOCATOR
# ══════════════════════════════════════════════════════════════════
print("── 链式非法边界 → INVALID_LOCATOR ──")

for bad in ["nth:2", "index:1", "first:", "last:", "parent:", "next:", "prev:",
            "child:", "child:input", "filter:x", "has:div"]:
    p = bb.parse_locator(bad)
    eq(p.get("engine"), "error", "独立步骤 {!r} → error".format(bad))
    eq(p.get("code"), "INVALID_LOCATOR", "独立步骤 {!r} → INVALID_LOCATOR".format(bad))

for bad in ["#a >> ", "#a >>", " >> div", ">>div"]:
    p = bb.parse_locator(bad)
    eq(p.get("engine"), "error", "残缺链式 {!r} → error".format(bad))
    eq(p.get("code"), "INVALID_LOCATOR", "残缺链式 {!r} → INVALID_LOCATOR".format(bad))

for bad in ["#a >> nth:x", "#a >> nth:", "#a >> nth:1.5", "#a >> filter:",
            "#a >> has:", "#a >> child:", "tag:div >> has:nth:2"]:
    p = bb.parse_locator(bad)
    eq(p.get("engine"), "error", "非法步骤 {!r} → error".format(bad))
    eq(p.get("code"), "INVALID_LOCATOR", "非法步骤 {!r} → INVALID_LOCATOR".format(bad))

print("─" * 60)
# ══════════════════════════════════════════════════════════════════
# 5. 兼容回归：单个 `>` 仍走 CSS 直通（R3 不误伤）
# ══════════════════════════════════════════════════════════════════
print("── 单 `>` CSS 直通回归 ──")

for spec in ["#a > div", "div > span", "#a>div", "ul > li > a"]:
    p = bb.parse_locator(spec)
    eq(p.get("engine"), "playwright", "{!r} → playwright".format(spec))
    eq(p.get("raw"), spec, "{!r} raw 原样直通".format(spec))
    check("chain" not in p, "{!r} 无 chain（非链式）".format(spec))

print("─" * 60)
# ══════════════════════════════════════════════════════════════════
# 6. P0 既有 DSL 回归（语义不变）
# ══════════════════════════════════════════════════════════════════
print("── P0 DSL 回归 ──")
p = bb.parse_locator("text:登录")
eq(p.get("engine"), "get_by_text", "text: 不变")
eq(p.get("text"), "登录", "text: 文本不变")

p = bb.parse_locator("tag:input@type=submit")
eq(p.get("raw"), "input[type=submit]", "tag:@attr 不变")

p = bb.parse_locator("tag:button@@text:提交")
eq(p.get("raw"), "button", "@@ 基串不变")
eq(p.get("has_text"), "提交", "@@ has_text 不变")
check("chain" not in p, "@@ 单独使用无 chain")

p = bb.parse_locator("role:button[name=登录]")
eq(p.get("engine"), "get_by_role", "role: 不变")
eq(p.get("name"), "登录", "role: name 不变")

for spec, eng in [("label:用户名", "get_by_label"),
                  ("placeholder:请输入手机号", "get_by_placeholder"),
                  ("testid:submit-btn", "get_by_test_id")]:
    eq(bb.parse_locator(spec).get("engine"), eng, "{!r} engine 不变".format(spec))

eq(bb.parse_locator("").get("code"), "INVALID_LOCATOR", "空串仍 INVALID_LOCATOR")

print("─" * 60)
# ══════════════════════════════════════════════════════════════════
# 7. AST 结构断言
# ══════════════════════════════════════════════════════════════════
print("── AST 结构断言 ──")
src_path = os.path.join(SRC, "browser_backend.py")
with open(src_path, "r", encoding="utf-8") as f:
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
for fn in ["parse_locator", "build_locator", "_build_base_locator", "_parse_chain",
           "_parse_step", "_looks_chain_step", "_apply_chain_step"]:
    check(fn in func_names, "存在函数定义 {}".format(fn))

print("─" * 60)
if _FAILS:
    print("结果: FAIL ({} 项失败, {} 项警告)".format(len(_FAILS), len(_WARNS)))
    for m in _FAILS:
        print("  - " + m)
    sys.exit(1)
else:
    print("结果: PASS (0 失败, {} 项警告)".format(len(_WARNS)))
    sys.exit(0)
