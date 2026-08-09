# -*- coding: utf-8 -*-
"""
engine 逻辑回归冒烟测试 — 验证 if/loop 块执行与变量作用域。

用法: python tools/_smoke_engine.py
退出码: 0=全部通过, 1=存在失败
"""
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, SRC)

import state  # noqa: E402
from engine import engine  # noqa: E402
from scriptdata import ScriptData  # noqa: E402

_FAILURES = []


def _check(name, cond, detail=""):
    if cond:
        print("  ✓ {}".format(name))
    else:
        print("  ✗ {}  {}".format(name, detail))
        _FAILURES.append(name)


# ── 测试1: IF 块内设置变量应保留 (回归: _handle_if 未传 _nested=True 导致 variables.clear) ──
def test_if_variable_scope():
    engine.variables.clear()
    rows = [
        ScriptData("设置变量", ["count", "1"]),
        ScriptData("如果", ["${count} == 1"]),
        ScriptData("设置变量", ["inside", "42"]),
        ScriptData("结束如果", [None] * 9),
    ]
    engine.execute_script(rows, os.getcwd())
    _check("IF 块内变量保留", engine.variables.get("inside") == 42,
           "inside={}".format(engine.variables.get("inside")))
    _check("外层变量不被清空", engine.variables.get("count") == 1,
           "count={}".format(engine.variables.get("count")))


# ── 测试2: 嵌套 IF (内层条件依赖外层变量) ──
def test_nested_if():
    engine.variables.clear()
    rows = [
        ScriptData("设置变量", ["x", "5"]),
        ScriptData("如果", ["${x} > 0"]),
        ScriptData("如果", ["${x} > 3"]),
        ScriptData("设置变量", ["depth", "2"]),
        ScriptData("结束如果", [None] * 9),
        ScriptData("结束如果", [None] * 9),
    ]
    engine.execute_script(rows, os.getcwd())
    _check("嵌套 IF 生效", engine.variables.get("depth") == 2,
           "depth={}".format(engine.variables.get("depth")))


# ── 测试3: 条件循环 (${count} < N 计数) ──
def test_conditional_loop():
    # 条件为假 → 循环体不应执行
    engine.variables.clear()
    rows_false = [
        ScriptData("设置变量", ["count", "5"]),
        ScriptData("循环开始", ["${count} < 3"]),
        ScriptData("设置变量", ["leaked", "1"]),
        ScriptData("循环结束", [None] * 9),
    ]
    engine.execute_script(rows_false, os.getcwd())
    _check("条件为假时循环体不执行", "leaked" not in engine.variables,
           "leaked={}".format(engine.variables.get("leaked")))
    # 条件为真 → 循环体应执行 3 次 (count 0→1→2→3)
    engine.variables.clear()
    rows_true = [
        ScriptData("设置变量", ["count", "0"]),
        ScriptData("循环开始", ["${count} < 3"]),
        ScriptData("数学运算", ["${count} + 1", "count"]),
        ScriptData("循环结束", [None] * 9),
    ]
    engine.execute_script(rows_true, os.getcwd())
    _check("条件为真时循环体执行3次", engine.variables.get("count") == 3,
           "count={}".format(engine.variables.get("count")))


# ── 测试4: 循环内嵌 IF (回归: _handle_loop 未处理块命令, if 条件被静默跳过) ──
def test_if_inside_loop():
    engine.variables.clear()
    engine.variables["flag"] = 0  # flag=0 → 循环体内 if 条件为假
    rows = [
        ScriptData("循环开始", ["2"]),
        ScriptData("如果", ["${flag} == 1"]),
        ScriptData("设置变量", ["hit", "1"]),  # 不应执行
        ScriptData("结束如果", [None] * 9),
        ScriptData("循环结束", [None] * 9),
    ]
    engine.execute_script(rows, os.getcwd())
    _check("循环内 IF 条件生效(假分支不执行)", "hit" not in engine.variables,
           "hit={}".format(engine.variables.get("hit")))


# ── 测试5: 循环内嵌 IF 真分支 (块体应执行 2 次) ──
def test_if_true_inside_loop():
    engine.variables.clear()
    rows = [
        ScriptData("循环开始", ["2"]),
        ScriptData("如果", ["1 == 1"]),
        ScriptData("设置变量", ["hit", "1"]),
        ScriptData("结束如果", [None] * 9),
        ScriptData("循环结束", [None] * 9),
    ]
    engine.execute_script(rows, os.getcwd())
    _check("循环内 IF 真分支执行", engine.variables.get("hit") == 1,
           "hit={}".format(engine.variables.get("hit")))


# ── 测试6: 循环内嵌循环 (嵌套循环块跳转, 修复前内层被静默跳过) ──
def test_nested_loop():
    engine.variables.clear()
    calls = {"n": 0}
    orig_execute = engine.execute

    def counting_execute(row, script_dir):
        if hasattr(row, 'cmd_type') and row.cmd_type == "设置变量":
            calls["n"] += 1
        return orig_execute(row, script_dir)

    engine.execute = counting_execute
    try:
        rows = [
            ScriptData("循环开始", ["2"]),
            ScriptData("循环开始", ["2"]),
            ScriptData("设置变量", ["deep", "1"]),
            ScriptData("循环结束", [None] * 9),
            ScriptData("循环结束", [None] * 9),
        ]
        engine.execute_script(rows, os.getcwd())
    finally:
        engine.execute = orig_execute
    # 外层2次 × 内层2次 = 4 次设置变量
    _check("嵌套循环执行(2×2=4次)", calls["n"] == 4, "n={}".format(calls["n"]))


# ── 测试7: 跳出循环后继续执行块后命令 ──
def test_break_loop():
    engine.variables.clear()
    rows = [
        ScriptData("循环开始", ["10"]),
        ScriptData("跳出循环", [None] * 9),
        ScriptData("循环结束", [None] * 9),
        ScriptData("设置变量", ["after", "1"]),
    ]
    engine.execute_script(rows, os.getcwd())
    _check("跳出循环后继续执行", engine.variables.get("after") == 1,
           "after={}".format(engine.variables.get("after")))


if __name__ == "__main__":
    print("engine 逻辑回归冒烟测试")
    print("-" * 40)
    test_if_variable_scope()
    test_nested_if()
    test_conditional_loop()
    test_if_inside_loop()
    test_if_true_inside_loop()
    test_nested_loop()
    test_break_loop()
    print("-" * 40)
    if _FAILURES:
        print("失败 {} 项: {}".format(len(_FAILURES), ", ".join(_FAILURES)))
        sys.exit(1)
    print("全部通过 ✓")
    sys.exit(0)
