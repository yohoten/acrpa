#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""run_id 统一可观测性守卫 —— 一次运行一个 id, 贯穿日志/history/fail_context/CLI 报告。

用法:
    .venv\\Scripts\\python.exe -X utf8 tests\\_test_run_id.py

覆盖:
  1. core.run_id 语义   : new_run/current/reset/ensure_run 的生成、复用与清空。
  2. history 字段       : make_record 写入 run_id; 旧行无 run_id 仍可读 (向后兼容)。
  3. 四通道源码已接入   : build.py 显式声明 core.run_id (惰性 import 静态分析不可见);
                          core.log / history / engine / cli 均已注入 run_id。

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

from core import run_id  # noqa: E402

_PASS, _FAIL = [], []


def check(cond, msg, detail=""):
    (_PASS if cond else _FAIL).append(msg)
    tail = ("  ({})".format(detail) if detail else "")
    print("[{}] {}{}".format("OK  " if cond else "FAIL", msg, tail))


def _read(rel):
    with io.open(os.path.join(ROOT, rel), "r", encoding="utf-8") as f:
        return f.read()


# ── 1. core.run_id 语义 ──────────────────────────────────────────────
r1 = run_id.new_run()
check(bool(r1) and len(r1) == 16, "new_run 生成 16 位十六进制 run_id", r1)
check(run_id.current() == r1, "current() 返回当前 run_id", run_id.current())
r2 = run_id.new_run()
check(bool(r2) and r2 != r1, "new_run 每次生成全新 id", "{} vs {}".format(r2, r1))
run_id.reset()
check(run_id.current() == "", "reset() 清空后 current() 返回空串", repr(run_id.current()))
r3 = run_id.ensure_run()
check(run_id.ensure_run() == r3, "ensure_run() 已有 id 时复用", r3)
run_id.reset()
r4 = run_id.ensure_run()
check(bool(r4) and r4 != r3, "ensure_run() 无 id 时新建", r4)
run_id.reset()

# ── 2. history 字段 + 向后兼容 ───────────────────────────────────────
import history  # noqa: E402

run_id.new_run()
_rid = run_id.current()
rec = history.make_record("script", "a.xls")
check(rec.get("run_id") == _rid, "history.make_record 写入 run_id 字段", _rid)
run_id.reset()

with tempfile.TemporaryDirectory(prefix="acrpa_runid_") as d:
    p = os.path.join(d, "history.jsonl")
    with io.open(p, "w", encoding="utf-8") as f:
        f.write(json.dumps({"ts": "2026-01-01T00:00:00", "kind": "script",
                            "target": "x.xls", "status": "ok"}) + "\n")
    try:
        history.configure(path=p, max_bytes=None, keep=None)
        recent = history.read_recent(limit=5)
        check(len(recent) >= 1 and "status" in recent[0]
              and recent[0].get("run_id", "") == "",
              "旧历史行 (无 run_id) 仍可读回, 向后兼容", str(recent[0]))
    finally:
        history.configure(path=None, max_bytes=None, keep=None)

# ── 3. 四通道源码已接入 ──────────────────────────────────────────────
build_txt = _read("build.py")
check('"core.run_id"' in build_txt,
      "build.py::_PROJECT_MODULES 显式声明 core.run_id (惰性 import 不可静态发现)")

log_txt = _read("src/core/log.py")
check("run_id" in log_txt and "def put(self, msg, tag=None" in log_txt,
      "core.log: put 接收 run_id 参数 (文件行写入 [run:...])")

hist_txt = _read("src/history.py")
check('"run_id": _rid' in hist_txt, "history.make_record 注入 run_id")

eng_txt = _read("src/engine.py")
check('"run_id": rid' in eng_txt, "engine 失败现场 payload 注入 run_id")

cli_txt = _read("src/cli.py")
check(cli_txt.count('"run_id": rid') >= 2, "cli 脚本/工作流两处报告注入 run_id")

# ── 汇总 ─────────────────────────────────────────────────────────────
print("\n------------------------------------------------------------")
print("结果: 通过 {} / 失败 {}".format(len(_PASS), len(_FAIL)))
for f in _FAIL:
    print("  - {}".format(f))
sys.exit(1 if _FAIL else 0)
