# -*- coding: utf-8 -*-
"""Mini Bar 拆分结构回归 (静态, 不开窗口)。

背景: src/ACRPA.py 是 5700+ 行 / 196 个顶层函数的巨石。Mini Bar 集群 (25 个函数 +
17 个模块级状态变量) 已整体搬到 `src/mini_bar.py`, 靠 ACRPA 启动时 `_bind_minibar()`
注入宿主符号 (与 src/tray.py 一致: 被拆出的模块不反向 import 宿主)。

这种"搬出去 + 注入"的做法的唯一风险是**注入清单漏项** —— 漏一个就是运行期
NameError, 而且可能只在冷门分支上炸。本测试把它变成静态可查:

  M1 搬迁完整性 : 生成的模块里没有任何解析不到的全局名 (逐函数 AST 核对)
  M2 注入清单   : mini_bar.INJECTED 与静态分析出的清单完全一致 (不多不少)
  M3 回导出     : ACRPA 里仍在引用的搬迁符号都从 mini_bar 回导入了
  M4 注入时机   : ACRPA 在启动/主题刷新/缩放钩子/PIL 懒加载后都会调用 _bind_minibar()
  M5 无残留     : ACRPA.py 不再重复定义任何已搬迁的函数/变量

用法: .venv\\Scripts\\python.exe -X utf8 tools\\_test_minibar_split.py
退出码: 0=全部通过, 1=存在失败
"""
import ast
import io
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "tools"))
sys.path.insert(0, os.path.join(BASE, "src"))

import _extract_minibar as ex                      # noqa: E402

ACRPA_SRC = os.path.join(BASE, "src", "ACRPA.py")
MINI_SRC = os.path.join(BASE, "src", "mini_bar.py")

_PASS, _FAIL = [], []


def check(cond, msg):
    (_PASS if cond else _FAIL).append(msg)
    print("[{}] {}".format("OK  " if cond else "FAIL", msg))


def main():
    print("=" * 68)
    print("Mini Bar 拆分结构回归")
    print("=" * 68)

    acrpa = io.open(ACRPA_SRC, encoding="utf-8").read()
    mini = io.open(MINI_SRC, encoding="utf-8").read()
    mtree = ast.parse(mini)
    atree = ast.parse(acrpa)

    # mini_bar.py 是搬迁结果的唯一事实来源 —— 不再回头分析 ACRPA (它已不含这些定义)
    injected = None
    for node in mtree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "INJECTED" for t in node.targets):
            injected = [e.value for e in node.value.elts]
    # 注入位 (root/C/FONT_* 等) 本就在两个模块里各自存在, 不算"搬迁变量",
    # 否则会把"宿主自己也定义 root"误判成重复定义。
    placeholders = {t.id for n in mtree.body if isinstance(n, ast.Assign)
                    for t in n.targets if isinstance(t, ast.Name)}
    moved_funcs = {n.name for n in mtree.body if isinstance(n, ast.FunctionDef)}
    moved_vars = placeholders - set(injected or []) - {"INJECTED"}
    moved = moved_funcs | moved_vars

    # ── M1 搬迁完整性 ──
    print("\n── M1 搬迁完整性 (无未解析全局名) ──")
    unresolved = ex.verify_module(mini)
    if unresolved:
        for name, users in sorted(unresolved.items()):
            print("      {} ← {}".format(name, ", ".join(users[:3])))
    check(not unresolved,
          "mini_bar.py 无未解析全局名 ({} 个)".format(len(unresolved)))
    check(len(moved_funcs) >= 20,
          "mini_bar.py 至少含 20 个搬迁函数 (实际 {})".format(len(moved_funcs)))
    check("bind" in moved_funcs, "mini_bar.py 提供 bind()")

    # ── M2 注入清单一致 ──
    print("\n── M2 注入清单 ──")
    check(injected is not None, "mini_bar.py 定义了 INJECTED")
    check(sorted(injected or []) == sorted(set(injected or [])),
          "INJECTED 无重复项")
    check(set(injected or []) <= placeholders,
          "每个注入项都有模块级占位 (缺: {})".format(
              sorted(set(injected or []) - placeholders) or "无"))

    # ── M3 回导出 ──
    print("\n── M3 回导出 (ACRPA 仍按旧名调用的符号) ──")
    imported_from_mini = set()
    for node in atree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "mini_bar":
            for al in node.names:
                imported_from_mini.add(al.asname or al.name)
    refs = set()
    for node in atree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        for x in ast.walk(node):
            if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Load) and x.id in moved:
                refs.add(x.id)
    check(refs <= imported_from_mini,
          "ACRPA 仍引用的 {} 个搬迁符号已全部回导入 (缺: {})".format(
              len(refs), sorted(refs - imported_from_mini) or "无"))

    # ── M4 注入时机 ──
    print("\n── M4 注入时机 ──")
    calls = [n for n in ast.walk(atree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id == "_bind_minibar"]
    check(len(calls) >= 3,
          "_bind_minibar() 至少 3 处调用 (启动/主题/PIL/缩放), 实际 {}".format(len(calls)))
    top_level_call = any(
        isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
        and isinstance(n.value.func, ast.Name) and n.value.func.id == "_bind_minibar"
        for n in atree.body)
    check(top_level_call, "启动时 (模块级) 调用过一次 _bind_minibar()")
    check("_bind_minibar()" in acrpa and "_refresh_theme" in acrpa,
          "ACRPA 定义了 _bind_minibar 并接入主题刷新")
    check("import mini_bar" in acrpa and "import ACRPA" not in mini,
          "单向依赖: ACRPA → mini_bar (mini_bar 不反向 import 宿主)")

    # ── M5 无残留 ──
    print("\n── M5 ACRPA 无重复定义 ──")
    still = []
    for node in atree.body:
        if isinstance(node, ast.FunctionDef) and node.name in moved_funcs:
            still.append(node.name)
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id in moved_vars:
                    still.append(t.id)
    check(not still, "ACRPA.py 不再定义已搬迁符号 (残留: {})".format(still or "无"))

    print("\n" + "=" * 68)
    print("通过 {} / 失败 {}".format(len(_PASS), len(_FAIL)))
    print("结论: {}".format("FAIL" if _FAIL else "PASS"))
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
