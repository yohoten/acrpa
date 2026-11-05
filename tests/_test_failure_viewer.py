#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""失败现场查看器守卫 —— 扫描 / 脱敏 / 排序 / 只读数据面。

用法:
    .venv\\Scripts\\python.exe -X utf8 tests\\_test_failure_viewer.py

覆盖:
  1. engine.failure_context_dir / scan_failure_contexts 的目录解析与扫描排序;
  2. 损坏文件 / 非 fail_context 前缀文件静默跳过;
  3. 变量脱敏 (interp.redact) 对 dict/list/str 安全返回 str;
  4. dialogs 暴露 open_failure_viewer 入口 (UI 面源码断言, 不做真实 GUI)。

退出码: 0=全部通过 / 1=有失败。
"""
import io
import json
import os
import sys
import tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import engine  # noqa: E402
import interp  # noqa: E402

_PASS, _FAIL = [], []


def check(cond, msg, detail=""):
    (_PASS if cond else _FAIL).append(msg)
    tail = ("  ({})".format(detail) if detail else "")
    print("[{}] {}{}".format("OK  " if cond else "FAIL", msg, tail))


def _read(rel):
    with io.open(os.path.join(ROOT, rel), "r", encoding="utf-8") as f:
        return f.read()


# ── 1. 目录解析 ──────────────────────────────────────────────────────
d0 = engine.failure_context_dir()
check(isinstance(d0, str), "failure_context_dir() 返回 str", repr(d0))
check(engine.scan_failure_contexts("__nonexistent_dir__") == [],
      "目录不存在时扫描返回空列表")

# ── 2. 扫描 / 排序 / 容错 ────────────────────────────────────────────
with tempfile.TemporaryDirectory(prefix="acrpa_failview_") as d:
    def _w(fn, obj):
        with io.open(os.path.join(d, fn), "w", encoding="utf-8") as f:
            f.write(obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False))

    _w("fail_context_20260101.json",
       {"time": "2026-01-01T10:00:00", "run_id": "old", "script": "a.xls",
        "row": 3, "cmd": "点击", "message": "没找到", "vars": {"x": 1}})
    _w("fail_context_20260102.json",
       {"time": "2026-01-02T10:00:00", "run_id": "new", "script": "b.xls",
        "row": 9, "cmd": "等待", "message": "超时",
        "vars": {"x": 2, "pwd": "secret123"}})
    _w("notes.txt", "not a failure context")
    _w("fail_context_bad.json", "{ not valid json")

    items = engine.scan_failure_contexts(d)
    check(len(items) == 2, "仅合法 fail_context_*.json 被扫描到", str(len(items)))
    check(items[0]["time"] == "2026-01-02T10:00:00" and items[0]["run_id"] == "new",
          "按时间倒序, 最新在前", items[0].get("time"))
    check(items[0]["_file"].endswith("fail_context_20260102.json"),
          "每条注入 _file 指向源文件", items[0]["_file"])
    check(items[1]["vars"]["x"] == 1, "旧条目变量可读", items[1].get("vars"))

# ── 3. 变量脱敏 ──────────────────────────────────────────────────────
check(isinstance(interp.redact({"pwd": "secret123"}), str),
      "interp.redact 对 dict 安全返回 str", interp.redact({"pwd": "secret123"}))
check(isinstance(interp.redact([1, "a"]), str), "interp.redact 对 list 安全返回 str")
check("secret123" not in interp.redact("pwd=secret123"),
      "interp.redact 掩盖密钥值", interp.redact("pwd=secret123"))

# ── 4. UI 入口源码断言 ───────────────────────────────────────────────
dtext = _read("src/dialogs.py")
check("def open_failure_viewer(" in dtext, "dialogs 暴露 open_failure_viewer 入口")
atext = _read("src/ACRPA.py")
check("open_failure_viewer" in atext, "主界面已接线失败现场查看入口")

# ── 汇总 ─────────────────────────────────────────────────────────────
print("\n------------------------------------------------------------")
print("结果: 通过 {} / 失败 {}".format(len(_PASS), len(_FAIL)))
for f in _FAIL:
    print("  - {}".format(f))
sys.exit(1 if _FAIL else 0)
