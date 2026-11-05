#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""工作流版本历史守卫 —— 保存 / 恢复 / 差异对比。

用法:
    .venv\\Scripts\\python.exe -X utf8 tests\\_test_workflow_version.py

覆盖:
  1. workflow_versions 表建表 (无脚本版本表冲突);
  2. save_workflow_version 自增版本号 + 内容往返;
  3. get_workflow_history 降序返回 (version_num, timestamp, step_count, comment);
  4. restore_workflow_version 恢复 dict / 不存在返回 None;
  5. diff_workflow_versions 产出人类可读差异 (新增/删除/变更/无差异)。

退出码: 0=全部通过 / 1=有失败。
"""
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

import version_manager  # noqa: E402

_PASS, _FAIL = [], []


def check(cond, msg, detail=""):
    (_PASS if cond else _FAIL).append(msg)
    tail = ("  ({})".format(detail) if detail else "")
    print("[{}] {}{}".format("OK  " if cond else "FAIL", msg, tail))


WF_A = {"name": "demo", "steps": [
    {"type": "script", "path": "a.xls"},
    {"type": "log", "text": "hello"},
]}
WF_B = {"name": "demo", "steps": [
    {"type": "script", "path": "a.xls"},
    {"type": "log", "text": "hello"},
    {"type": "wait", "seconds": 5},
]}

with tempfile.TemporaryDirectory(prefix="acrpa_wfver_") as d:
    db = os.path.join(d, "versions.db")
    vm = version_manager.VersionManager(db_path=db)
    wf_path = os.path.join(d, "workflow.json")

    # ── 保存 + 自增 ──
    v1 = vm.save_workflow_version(wf_path, WF_A, "初始")
    v2 = vm.save_workflow_version(wf_path, WF_B, "加等待")
    check(v1 == 1 and v2 == 2, "版本号自增 (v1/v2)", "{}/{}".format(v1, v2))

    # ── 历史 (降序) ──
    hist = vm.get_workflow_history(wf_path)
    check(len(hist) == 2, "历史含 2 个版本", str(len(hist)))
    check(hist[0][0] == 2 and hist[0][3] == "加等待",
          "历史降序且保留 comment", repr(hist[0]))
    check(hist[1][2] == 2, "step_count 记录正确 (v1=2 步)", repr(hist[1]))

    # ── 恢复 ──
    r1 = vm.restore_workflow_version(wf_path, 1)
    check(isinstance(r1, dict) and len(r1.get("steps", [])) == 2,
          "restore v1 还原 2 步工作流", str(r1))
    check(vm.restore_workflow_version(wf_path, 99) is None,
          "恢复不存在版本返回 None")

    # ── diff ──
    d = vm.diff_workflow_versions(wf_path, 1, 2)
    check("wait 5s" in d and "+ [步骤3]" in d,
          "diff 标出新增步骤", d)
    d_same = vm.diff_workflow_versions(wf_path, 1, 1)
    check("无差异" in d_same, "同版本 diff 返回无差异", d_same)

    # ── 空路径防御 ──
    check(vm.save_workflow_version("", WF_A) == 0, "空路径保存返回 0 (不落库)")
    check(vm.get_workflow_history("") == [], "空路径历史返回空列表")

# ── 汇总 ─────────────────────────────────────────────────────────────
print("\n------------------------------------------------------------")
print("结果: 通过 {} / 失败 {}".format(len(_PASS), len(_FAIL)))
for f in _FAIL:
    print("  - {}".format(f))
sys.exit(1 if _FAIL else 0)
