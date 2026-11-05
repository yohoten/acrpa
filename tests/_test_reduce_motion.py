#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""无障碍「减少动效」开关 P1-3 回归自测: state 字段 + reduce_motion() 消费 + 设置界面。

用法:
    .venv\\Scripts\\python.exe -X utf8 tests\\_test_reduce_motion.py

覆盖:
  A schema    : state._config_schema 新增 reduce_motion (默认 False, bool)。
  B 消费      : utils.reduce_motion() 实时反映 state.REDUCE_MOTION (切换即变)。
  C 设置界面  : ui/settings/cards/system.py 接入「减少动效（无障碍）」开关。

退出码: 0=全部通过 / 1=有失败。
"""
import io
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

_PASS, _FAIL, _WARN = [], [], []


def check(cond, msg, detail=""):
    (_PASS if cond else _FAIL).append(msg)
    tail = ("  ({})".format(detail) if detail else "")
    print("[{}] {}{}".format("OK  " if cond else "FAIL", msg, tail))


def warn(msg):
    _WARN.append(msg)
    print("[WARN] {}".format(msg))


def _read(rel):
    with io.open(os.path.join(ROOT, rel), "r", encoding="utf-8") as f:
        return f.read()


import state      # noqa: E402
import utils      # noqa: E402


def t_schema():
    print("\n── A. state 字段 ──")
    entry = None
    for k, d, t in state._config_schema:
        if k == "reduce_motion":
            entry = (k, d, t)
            break
    check(entry is not None, "state._config_schema 含 reduce_motion 字段")
    if entry is not None:
        _, d, t = entry
        check(d is False, "reduce_motion 默认 False (保留全动效)", "default={!r}".format(d))
        check(t is bool, "reduce_motion 类型 bool")
    check(hasattr(state, "REDUCE_MOTION"), "state.REDUCE_MOTION 全局变量已生成")


def t_consume():
    print("\n── B. reduce_motion() 消费 ──")
    old = getattr(state, "REDUCE_MOTION", False)
    try:
        state.REDUCE_MOTION = True
        check(utils.reduce_motion() is True, "REDUCE_MOTION=True → reduce_motion() True")
        state.REDUCE_MOTION = False
        check(utils.reduce_motion() is False, "REDUCE_MOTION=False → reduce_motion() False")
    finally:
        state.REDUCE_MOTION = old
    check(utils.reduce_motion() is bool(utils.reduce_motion()), "reduce_motion() 返回 bool")


def t_settings_ui():
    print("\n── C. 设置界面接入 ──")
    src = _read("src/ui/settings/cards/system.py")
    check("REDUCE_MOTION" in src, "system.py 引用 REDUCE_MOTION")
    check("减少动效" in src, "system.py 含「减少动效」开关文案")
    check("reduce_motion_var" in src, "system.py 声明 reduce_motion_var")
    check("_toggle_reduce_motion" in src, "system.py 定义 _toggle_reduce_motion 回调")


def main():
    print("=== 无障碍「减少动效」开关 P1-3: state 字段 + 消费 + 设置界面 ===")
    t_schema()
    t_consume()
    t_settings_ui()

    print("\n" + "-" * 60)
    print("PASS={} FAIL={} WARN={}".format(len(_PASS), len(_FAIL), len(_WARN)))
    for m in _FAIL:
        print("[FAIL] {}".format(m))
    print("=== 结果: {} ===".format("FAIL" if _FAIL else "OK"))
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
