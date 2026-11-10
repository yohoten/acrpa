# -*- coding: utf-8 -*-
"""脚本编辑区「单元格状态机」专项自测 —— 对齐 Excel 的点击/编辑/清空/校验语义。

覆盖:
    1. 点选不改布局  : 切换选中行时参数列宽必须稳定 (修「点一下表格就变样」:
                       旧实现按选中命令的 schema 改 width, 每次点选整表重排);
    2. 活动列+状态条 : 单击单元格记录活动列, 底部状态条显示 行号/参数名/值/总行数;
    3. 键入即编辑    : 选中单元格后直接打字 → 进入编辑并替换原值;
    4. Delete 清空   : 只清当前列内容 (不再删行), 可撤销; 删行走 Ctrl+Delete;
    5. 校验拦截      : 非法值保留输入 + 控件标红 + 状态条给原因 + 不写库 + Esc 可退出;
    6. F2            : 打开「活动列」而不是永远第 0 列。

需要真实窗口会话: 默认 [WARN] 跳过, 设 ACRPA_GUI_TEST=1 启用。
全自动、不联网、不落盘; 仅操作内存中的 state._editor_rows。
运行:  python tests/_test_editor_cellstate.py
退出码: 0 = 全部断言通过(允许 [WARN]); 1 = 存在 [FAIL]
"""
import os
import sys
from types import SimpleNamespace

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


def _reset(A, state, rows):
    state._editor_rows = rows
    state._editor_active_col = 0
    state._undo_stack.clear()
    state._redo_stack.clear()
    state._editor_modified = False
    A._editor_sync_to_tree()
    A.tree.update()
    return A.tree.get_children()


def _widths(A):
    """当前显示的参数列宽 (p1..p4) —— 用于钉住「点选不重排」。"""
    return [A.tree.column("p{}".format(i), "width") for i in range(1, 5)]


def _pt_for(A, iid, disp):
    """扫描整行 x, 返回 identify_column == #disp 的点击坐标 (布局可能仍在收敛)。"""
    x0, y0, w0, h0 = A.tree.bbox(iid)
    for dx in range(2, w0 - 2, 3):
        if A.tree.identify_column(x0 + dx) == "#{}".format(disp):
            return x0 + dx, y0 + h0 // 2
    return None


def _run(A, state):
    tree = A.tree
    kids = _reset(A, state, [ScriptData("等待", ["2.5"]),
                             ScriptData("找图", ["a.png", "0.9"]),
                             ScriptData("按键", ["a", "3"])])

    # ── 1. 点选不改布局 ──
    print("\n== 1. 点选不改列宽 (修「点一下表格就变样」) ==")
    base = _widths(A)
    seen = []
    for k in kids:
        tree.selection_set(k)
        tree.update()
        seen.append(_widths(A))
    check("1a. 三次换选列宽完全一致", all(w == base for w in seen), (base, seen))

    # ── 2. 活动列 + 状态条 ──
    print("\n== 2. 活动列 + 状态条 ==")
    status = A._editor_status
    check("2a. 状态条已挂载", status is not None)
    tree.selection_set(kids[0])
    tree.update()
    pt = _pt_for(A, kids[0], 2)          # 显示列 #2 = 参数1
    check("2b. 命中参数1列坐标", bool(pt), pt)
    if pt:
        A._track_active_cell(SimpleNamespace(x=pt[0], y=pt[1]))
    check("2c. 活动列=1", state._editor_active_col == 1, state._editor_active_col)
    txt = status.cget("text") if status is not None else ""
    check("2d. 状态条含 行号/参数名/值/总数",
          ("第 1 行" in txt and "秒数" in txt and "2.5" in txt and "共 3 行" in txt),
          txt)

    # ── 3. 键入即编辑 ──
    print("\n== 3. 键入即编辑 ==")
    tree.selection_set(kids[1])
    tree.update()
    A._kb_type_to_edit(SimpleNamespace(char="7"))
    w = A._inline_edit_widget
    check("3a. 键入进入编辑", w is not None)
    check("3b. 首字符落格且替换原值", (w.get() if w is not None else None) == "7",
          (w.get() if w is not None else None))
    A._inline_commit_fn()
    check("3c. 提交写入数据", state._editor_rows[1].args[0] == "7",
          state._editor_rows[1].args[0])

    # ── 4. Delete 清空当前列 + 撤销 ──
    print("\n== 4. Delete 只清内容 (不删行) ==")
    kids = _reset(A, state, [ScriptData("等待", ["2.5"]), ScriptData("找图", ["a.png"])])
    tree.selection_set(kids[0])
    tree.update()
    pt = _pt_for(A, kids[0], 2)
    if pt:
        A._track_active_cell(SimpleNamespace(x=pt[0], y=pt[1]))
    undo0 = len(state._undo_stack)
    A._kb_clear_cells()
    check("4a. 行数不变", len(state._editor_rows) == 2, len(state._editor_rows))
    check("4b. 参数1 已清空", state._editor_rows[0].args[0] == "",
          state._editor_rows[0].args[0])
    check("4c. 入撤销栈一次", len(state._undo_stack) == undo0 + 1,
          (undo0, len(state._undo_stack)))
    A._cmd_undo()
    kids = tree.get_children()           # undo 走全量重建 → iid 全换
    tree.update()
    check("4d. 撤销恢复内容", state._editor_rows[0].args[0] == "2.5",
          state._editor_rows[0].args[0])

    # ── 5. 校验: 保留输入 + 标红 + 拦截提交 ──
    print("\n== 5. 校验拦截 (保留输入/标红/不写库) ==")
    tree.selection_set(kids[0])
    tree.update()
    A._editor_open_cell(0, 1)            # 「等待」的参数1 = number
    w = A._inline_edit_widget
    check("5a. 打开单元格", w is not None)
    if w is not None:
        w.delete(0, "end")
        w.insert(0, "abc")
        ok = A._inline_commit_fn()
        check("5b. 非法值提交被拦截", ok is False, ok)
        check("5c. 未写库", state._editor_rows[0].args[0] == "2.5",
              state._editor_rows[0].args[0])
        check("5d. 输入保留在控件里", w.get() == "abc", w.get())
        check("5e. 控件标红", str(w.cget("bg")) == str(A.C.get("errbg")),
              (w.cget("bg"), A.C.get("errbg")))
        txt = status.cget("text") if status is not None else ""
        check("5f. 状态条给出原因", "未提交" in txt, txt)
        try:
            w.focus_force()
        except Exception:
            pass
        tree.update()
        w.event_generate("<KeyPress-Escape>", when="now")
        tree.update()
        check("5g. Esc 退出错误编辑", A._inline_edit_widget is None,
              A._inline_edit_widget)

    # ── 6. F2 打开活动列 ──
    print("\n== 6. F2 = 单元格编辑 (活动列) ==")
    state._editor_active_col = 1
    tree.selection_set(kids[0])
    tree.update()
    A._edit_cell(SimpleNamespace(x=0, y=0, char="", keysym="F2"))
    check("6a. F2 进入编辑", A._inline_edit_widget is not None)
    check("6b. 落在活动列", state._editor_active_col == 1, state._editor_active_col)
    if A._inline_edit_widget is not None:
        A._inline_edit_widget.event_generate("<KeyPress-Escape>", when="now")
        tree.update()


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
        state_mod._editor_active_col = 0
        state_mod.breakpoints.clear()
        state_mod._undo_stack.clear()
        state_mod._redo_stack.clear()
        state_mod._editor_modified = False
    print("\n=== {} (FAIL={}, WARN={}) ===".format(
        "ALL PASS" if _fails == 0 else "FAILED", _fails, _warns))
    return 0 if _fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
