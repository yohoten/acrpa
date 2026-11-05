#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""工作流撤销/重做守卫 —— 快照栈 + Ctrl+Z 接线。

用法:
    .venv\\Scripts\\python.exe -X utf8 tests\\_test_workflow_undo.py

覆盖:
  1. 有界快照栈 (_WF_UNDO/_WF_REDO/_WF_UNDO_MAX=100);
  2. _wf_snapshot_undo 深拷贝入 undo 栈并清空 redo;
  3. _wf_undo/_wf_redo 往返恢复 (不触碰 _wf_file);
  4. 工具栏按钮 + Ctrl+Z/Ctrl+Shift+Z/Ctrl+Y 快捷键接线。

退出码: 0=全部通过 / 1=有失败。无 GUI 时运行时断言打桩绕过刷新。
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

_PASS, _FAIL = [], []


def check(cond, msg, detail=""):
    (_PASS if cond else _FAIL).append(msg)
    tail = ("  ({})".format(detail) if detail else "")
    print("[{}] {}{}".format("OK  " if cond else "FAIL", msg, tail))


def _read(rel):
    with io.open(os.path.join(ROOT, rel), "r", encoding="utf-8") as f:
        return f.read()


# ── 1. 源码断言 ──────────────────────────────────────────────────────
src = _read("src/ui/workflow_view.py")
check("_WF_UNDO = []" in src and "_WF_REDO = []" in src, "定义 _WF_UNDO/_WF_REDO 快照栈")
check("_WF_UNDO_MAX = 100" in src, "快照栈上限 100")
for fn in ("_wf_snapshot_undo", "_wf_restore_snapshot", "_wf_undo", "_wf_redo"):
    check("def {}(".format(fn) in src, "定义 {}".format(fn))
check("_wf_snapshot_undo()" in src, "修改前调用 _wf_snapshot_undo 入栈")
check('bind("<Control-z>"' in src or "Control-z" in src, "绑定 Ctrl+Z")
check("Control-Shift-z" in src or "Control-Shift-Z" in src, "绑定 Ctrl+Shift+Z")
check("↶ 撤销" in src and "↷ 重做" in src, "工具栏提供撤销/重做按钮")

# ── 2. 运行时快照栈 (打桩绕过 UI 刷新) ────────────────────────────────
try:
    import ui.workflow_view as wv
except Exception as e:
    check(False, "import ui.workflow_view 失败: {}".format(e))
    wv = None

if wv is not None:
    # 打桩: 撤销/重做会调用 _wf_refresh_tree (需要已构建 UI), 这里替换为 no-op
    _orig_refresh = getattr(wv, "_wf_refresh_tree", None)
    _orig_toast = getattr(wv, "_toast", None)
    wv._wf_refresh_tree = lambda: None
    wv._toast = lambda *a, **k: None
    try:
        # 重置为确定初始态
        wv._wf_data = {"name": "A", "steps": [{"type": "log", "text": "one"}]}
        wv._WF_UNDO[:] = []
        wv._WF_REDO[:] = []

        # 快照 → 修改 → 撤销 → 重做
        wv._wf_snapshot_undo()
        check(len(wv._WF_UNDO) == 1 and not wv._WF_REDO,
              "快照入 undo 栈且清空 redo", "undo={} redo={}".format(len(wv._WF_UNDO), len(wv._WF_REDO)))
        snap0 = wv._WF_UNDO[-1]
        wv._wf_data["steps"].append({"type": "wait", "seconds": 1})
        wv._wf_data["name"] = "B"
        check(snap0["name"] == "A" and len(snap0["steps"]) == 1,
              "快照为深拷贝 (修改后快照不受影响)", str(snap0))

        wv._wf_undo()
        check(wv._wf_data["name"] == "A" and len(wv._wf_data["steps"]) == 1,
              "撤销恢复快照 (name/steps)", str(wv._wf_data))
        check(len(wv._WF_REDO) == 1, "撤销时当前态入 redo 栈")

        wv._wf_redo()
        check(wv._wf_data["name"] == "B" and len(wv._wf_data["steps"]) == 2,
              "重做恢复被撤销的修改", str(wv._wf_data))

        # 新操作清空 redo
        wv._wf_undo()
        wv._wf_snapshot_undo()
        check(not wv._WF_REDO, "新操作清空 redo 栈")
    finally:
        if _orig_refresh is not None:
            wv._wf_refresh_tree = _orig_refresh
        if _orig_toast is not None:
            wv._toast = _orig_toast

# ── 汇总 ─────────────────────────────────────────────────────────────
print("\n------------------------------------------------------------")
print("结果: 通过 {} / 失败 {}".format(len(_PASS), len(_FAIL)))
for f in _FAIL:
    print("  - {}".format(f))
sys.exit(1 if _FAIL else 0)
