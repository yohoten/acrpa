# -*- coding: utf-8 -*-
"""脚本编辑区「选中左缘色条」增量同步自测。

覆盖:
    1. 单选/换选只重挂变化的行 (O(Δ))，不再全表遍历；
    2. 选择未变时零重挂；
    3. 换主题/DPI 重建贴图后 force=True 全表重挂，选中行仍是复合条；
    4. 全量重建树 / 清空树后缓存不残留旧 iid。

背景: 每行 ``tree.item(image=...)`` 是一次 Tcl 往返 (~10~20µs)。旧实现每次
<<TreeviewSelect>> 都对全表重挂 image，千行脚本下单次选择 10ms+，方向键连按与
单元格 Tab 步进都会被吃掉帧预算。本测试用「带 image= 的 item 调用次数」把
O(Δ) 这条不变量钉住。

需要真实窗口会话: 默认 [WARN] 跳过, 设 ACRPA_GUI_TEST=1 启用。
运行:  python tests/_test_editor_selbar.py
退出码: 0 = 全部断言通过(允许 [WARN]); 1 = 存在 [FAIL]
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_SRC = os.path.join(_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

try:  # utils 顶层 import tkinter; headless 时忽略
    import tkinter  # noqa: F401
except Exception:
    pass

from scriptdata import ScriptData  # noqa: E402

_fails = 0
_warns = 0


def check(name, cond, detail=""):
    global _fails
    if cond:
        print("[OK]   " + name)
    else:
        _fails += 1
        print("[FAIL] " + name + (("  | " + str(detail)) if detail else ""))


def warn(msg):
    global _warns
    _warns += 1
    print("[WARN] " + msg)


def _build():
    """构建主窗口; 失败返回 None (无显示环境)。"""
    try:
        import app
        app.build()
    except Exception as e:
        warn("GUI 构建不可用, 跳过: {!r}".format(e))
        return None
    try:
        import ACRPA
        return ACRPA
    except Exception as e:
        warn("ACRPA 导入失败, 跳过: {!r}".format(e))
        return None


def _rows(spec):
    """构造测试行 (参数不足 9 位补空, 与真实读入路径一致)。"""
    out = []
    for cmd, a in spec:
        sd = ScriptData(cmd, [str(a)] if a is not None else None)
        while len(sd.args) < 9:
            sd.args.append("")
        out.append(sd)
    return out


def _img(tree, iid):
    return tree.item(iid, "image")


def _run(A, state):
    tree = A.tree
    state._editor_rows = _rows([("等待", 1), ("找图", "a.png"), ("点击", 10),
                                ("输入", "hi"), ("坐标", 1), ("按键", "a")])
    A._editor_sync_to_tree()
    tree.update()
    kids = tree.get_children()
    check("0. 6 行已建树", len(kids) == 6, len(kids))

    # 统计「带 image= 的 tree.item 调用」= Tcl 往返次数
    orig_item = tree.item
    hits = {"n": 0}

    def counting_item(*a, **kw):
        if "image" in kw:
            hits["n"] += 1
        return orig_item(*a, **kw)

    tree.item = counting_item
    try:
        # ── 1. 单选: 1 次重挂 ──
        hits["n"] = 0
        tree.selection_set(kids[2])
        tree.update()
        check("1a. 单选只重挂 1 行 (O(Δ))", hits["n"] == 1, hits["n"])
        check("1b. 选中行有色条贴图", bool(_img(tree, kids[2])))
        check("1c. 缓存记录当前选中",
              A._SEL_BAR_PREV.get(tree) == {kids[2]}, A._SEL_BAR_PREV.get(tree))
        sel_img = _img(tree, kids[2])

        # ── 2. 换选: 旧+新 = 2 次 ──
        hits["n"] = 0
        tree.selection_set(kids[4])
        tree.update()
        check("2a. 换选只重挂 2 行 (旧+新)", hits["n"] == 2, hits["n"])
        check("2b. 旧选中恢复 4px 条", _img(tree, kids[2]) != sel_img,
              _img(tree, kids[2]))
        check("2c. 新选中为 6px 复合条", bool(_img(tree, kids[4])))

        # ── 3. 选择未变: 0 次 ──
        hits["n"] = 0
        A._sync_sel_bars(tree)
        check("3a. 选择未变时零重挂", hits["n"] == 0, hits["n"])

        # ── 4. 贴图重建 (换主题/DPI): force 全表重挂 ──
        A._build_sel_bar()
        hits["n"] = 0
        A._sync_sel_bars(tree, force=True)
        check("4a. force 全表重挂", hits["n"] == len(kids) + 1, hits["n"])
        check("4b. force 后选中行仍点亮", bool(_img(tree, kids[4])))
        check("4c. force 后未选中行恢复", _img(tree, kids[0]) != _img(tree, kids[4]))

        # ── 5. 全量重建树: iid 全换, 缓存不得残留旧 iid ──
        A._editor_sync_to_tree()
        tree.update()
        kids2 = tree.get_children()
        check("5a. 重建后行数不变", len(kids2) == len(kids), len(kids2))
        check("5b. 缓存无旧 iid",
              not (A._SEL_BAR_PREV.get(tree, set()) - set(kids2)),
              A._SEL_BAR_PREV.get(tree))

        # ── 6. 清空: 同步后缓存归空且不抛错 ──
        state._editor_rows = []
        A._editor_sync_to_tree()
        tree.update()
        A._sync_sel_bars(tree)
        check("6a. 清空后缓存为空", not A._SEL_BAR_PREV.get(tree, set()),
              A._SEL_BAR_PREV.get(tree))
    finally:
        tree.item = orig_item


def main():
    if os.environ.get("ACRPA_GUI_TEST") != "1":
        warn("未启用 (设置 ACRPA_GUI_TEST=1 运行; 需真实窗口会话)")
        print("\n=== SKIPPED (FAIL=0, WARN=1) ===")
        return 0
    A = _build()
    if A is None:
        print("\n=== SKIPPED (FAIL=0, WARN={}) ===".format(_warns))
        return 0
    import state as state_mod
    try:
        _run(A, state_mod)
    finally:
        state_mod._editor_rows = []
        state_mod.breakpoints.clear()
        state_mod._undo_stack.clear()
        state_mod._redo_stack.clear()
        state_mod._editor_modified = False
    print("\n=== {} (FAIL={}, WARN={}) ===".format(
        "ALL PASS" if _fails == 0 else "FAILED", _fails, _warns))
    return 0 if _fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
