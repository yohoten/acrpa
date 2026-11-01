# -*- coding: utf-8 -*-
"""脚本编辑区「单元格操作」专项自测 —— Excel 式行/单元格行为回归。

覆盖:
    1. 单元格步进    : _editor_cell_step 的 Tab 折行 / 上下移 / 边界返回 None;
    2. 上移/下移     : 边界时为纯 no-op(不动数据、不入撤销栈、不标已修改);
                       断点随行内容移动; 选中随行移动; 多行同时移动;
    3. 删除行        : 空选择时不入撤销栈; 删除后选中落到相邻行;
    4. 复制粘贴      : Ctrl+C 按视觉顺序复制; Ctrl+V 插到最靠下的选中行之后,
                       并选中新粘贴行;
    5. 复制行 Ctrl+D : 多行(升序选择)时不再索引漂移复制错行;
    6. TSV 纯逻辑    : 行与文本互转、CRLF 归一、保留中间空行、超 10 列截断;
    7. 剪贴板互通    : Ctrl+C 把行写成系统剪贴板 TSV; Ctrl+V 优先解析系统
                       剪贴板表格; 单元格多行/制表符文本自动拆行拆列;
                       短 args 行写入不越界。

需要真实窗口会话: 默认 [WARN] 跳过, 设 ACRPA_GUI_TEST=1 启用。
全自动、不联网、不修改真实脚本文件; 仅操作内存中的 state._editor_rows。
运行:  .venv\\Scripts\\python.exe tools\\_test_editor_cells.py
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


def _cmds(state):
    return [sd.cmd_type for sd in state._editor_rows]


def _reset(A, state, spec):
    """重置编辑器为指定行, 清空选择/断点/撤销栈/脏标记。"""
    state._editor_rows = _rows(spec)
    state.breakpoints.clear()
    state._undo_stack.clear()
    state._redo_stack.clear()
    state._editor_modified = False
    state._editor_clipboard = []
    A._editor_sync_to_tree()
    A.tree.selection_remove(*A.tree.selection())
    A.tree.update_idletasks()


def _select(A, indices):
    """按 0-based 索引依次选中(保留调用顺序, 便于构造乱序选择)。"""
    children = A.tree.get_children()
    A.tree.selection_remove(*A.tree.selection())
    for i in indices:
        if 0 <= i < len(children):
            A.tree.selection_add(children[i])
    return list(A.tree.selection())


def _sel_cmds(A, state):
    return [state._editor_rows[A.tree.index(it)].cmd_type for it in A.tree.selection()]


# ── 1. 单元格步进 ────────────────────────────────────────────────────
def t_cell_step(A, state):
    print("\n== 1. _editor_cell_step 折行/边界 ==")
    _reset(A, state, [("A", 1), ("B", 2), ("C", 3)])
    step = A._editor_cell_step
    check("1a. Tab 右移一格", step(0, 0, 0, 1) == (0, 1), step(0, 0, 0, 1))
    check("1b. 末列 Tab → 下一行首列", step(0, 9, 0, 1) == (1, 0), step(0, 9, 0, 1))
    check("1c. 首列 Shift+Tab → 上一行末列", step(1, 0, 0, -1) == (0, 9), step(1, 0, 0, -1))
    check("1d. 首行 Shift+Tab → 边界 None", step(0, 0, 0, -1) is None, step(0, 0, 0, -1))
    check("1e. Enter 下移保持同列", step(0, 3, 1, 0) == (1, 3), step(0, 3, 1, 0))
    check("1f. 末行 ↓ → 边界 None", step(2, 3, 1, 0) is None, step(2, 3, 1, 0))
    check("1g. 首行 ↑ → 边界 None", step(0, 3, -1, 0) is None, step(0, 3, -1, 0))
    _reset(A, state, [("A", 1)])
    check("1h. 单元格末列且只有一行 → 边界 None", step(0, 9, 0, 1) is None)


# ── 2. 上移 / 下移 ───────────────────────────────────────────────────
def t_move(A, state):
    print("\n== 2. 上移/下移: 边界 no-op · 断点跟随 · 选中保持 ==")
    _reset(A, state, [("A", 1), ("B", 2), ("C", 3)])

    # 首行上移 = no-op, 不得污染撤销栈/脏标记
    _select(A, [0])
    A._cmd_move_up()
    check("2a. 首行上移不改数据", _cmds(state) == ["A", "B", "C"], _cmds(state))
    check("2b. 首行上移不入撤销栈", len(state._undo_stack) == 0,
          len(state._undo_stack))
    check("2c. 首行上移不标已修改", state._editor_modified is False,
          state._editor_modified)

    # 末行下移 = no-op
    _select(A, [2])
    A._cmd_move_down()
    check("2d. 末行下移不改数据", _cmds(state) == ["A", "B", "C"], _cmds(state))
    check("2e. 末行下移不入撤销栈", len(state._undo_stack) == 0,
          len(state._undo_stack))

    # 中间行上移: 断点跟随 B, 选中跟随 B
    _reset(A, state, [("A", 1), ("B", 2), ("C", 3)])
    state.breakpoints.add(2)          # 1-based 第 2 行 = B
    _select(A, [1])
    A._cmd_move_up()
    check("2f. 上移后顺序 B,A,C", _cmds(state) == ["B", "A", "C"], _cmds(state))
    check("2g. 断点跟随内容到第 1 行", state.breakpoints == {1},
          state.breakpoints)
    check("2h. 上移后选中仍是 B", _sel_cmds(A, state) == ["B"],
          _sel_cmds(A, state))

    # 下移: 断点跟随 + 选中保持
    _reset(A, state, [("A", 1), ("B", 2), ("C", 3)])
    state.breakpoints.add(2)          # B
    _select(A, [1])
    A._cmd_move_down()
    check("2i. 下移后顺序 A,C,B", _cmds(state) == ["A", "C", "B"], _cmds(state))
    check("2j. 下移后断点跟随到第 3 行", state.breakpoints == {3},
          state.breakpoints)
    check("2k. 下移后选中仍是 B", _sel_cmds(A, state) == ["B"],
          _sel_cmds(A, state))

    # 多行同时上移 (升序选择 1,2)
    _reset(A, state, [("A", 1), ("B", 2), ("C", 3)])
    _select(A, [1, 2])
    A._cmd_move_up()
    check("2l. 多行上移顺序 B,C,A", _cmds(state) == ["B", "C", "A"], _cmds(state))
    check("2m. 多行上移后选中仍为 B,C", _sel_cmds(A, state) == ["B", "C"],
          _sel_cmds(A, state))


# ── 3. 删除行 ────────────────────────────────────────────────────────
def t_delete(A, state):
    print("\n== 3. 删除行: 空选择不污染撤销栈 · 选中落到相邻行 ==")
    _reset(A, state, [("A", 1), ("B", 2), ("C", 3)])
    A._cmd_del_row()
    check("3a. 空选择删除不改数据", _cmds(state) == ["A", "B", "C"], _cmds(state))
    check("3b. 空选择删除不入撤销栈", len(state._undo_stack) == 0,
          len(state._undo_stack))
    check("3c. 空选择删除不标已修改", state._editor_modified is False,
          state._editor_modified)

    _reset(A, state, [("A", 1), ("B", 2), ("C", 3)])
    _select(A, [1])
    A._cmd_del_row()
    check("3d. 删除 B 后剩余 A,C", _cmds(state) == ["A", "C"], _cmds(state))
    check("3e. 删除后选中落到相邻行 C", _sel_cmds(A, state) == ["C"],
          _sel_cmds(A, state))

    _reset(A, state, [("A", 1), ("B", 2)])
    _select(A, [1])
    A._cmd_del_row()
    check("3f. 删末行后选中落到上一行 A", _sel_cmds(A, state) == ["A"],
          _sel_cmds(A, state))


# ── 4. 复制 / 粘贴 ───────────────────────────────────────────────────
def _set_clip(A, text):
    """写系统剪贴板; 无剪贴板环境返回 False。"""
    try:
        A.root.clipboard_clear()
        A.root.clipboard_append(text)
        return True
    except Exception:
        return False


def t_copy_paste(A, state):
    print("\n== 4. Ctrl+C / Ctrl+V: 视觉顺序 · 插入位置 · 粘贴后选中 ==")
    _reset(A, state, [("A", 1), ("B", 2), ("C", 3)])
    _select(A, [2, 0])                 # 乱序选择: C 先, A 后
    A._kb_copy()
    check("4a. 复制按视觉顺序 (A,C)",
          [sd.cmd_type for sd in state._editor_clipboard] == ["A", "C"],
          [sd.cmd_type for sd in state._editor_clipboard])

    _reset(A, state, [("A", 1), ("B", 2), ("C", 3)])
    _set_clip(A, "")                   # 清空系统剪贴板 → 回退内部行剪贴板
    state._editor_clipboard = [ScriptData("X", ["9"])]
    _select(A, [2, 0])                 # 乱序选择: sel[-1] 是第 0 行
    A._kb_paste()
    check("4b. 粘贴到最靠下选中行之后", _cmds(state) == ["A", "B", "C", "X"],
          _cmds(state))
    check("4c. 粘贴后选中新行", _sel_cmds(A, state) == ["X"],
          _sel_cmds(A, state))

    _reset(A, state, [("A", 1)])
    _set_clip(A, "")
    state._editor_clipboard = []
    A._kb_paste()
    check("4d. 空剪贴板粘贴不入撤销栈", len(state._undo_stack) == 0,
          len(state._undo_stack))


# ── 5. Ctrl+D 复制行 ─────────────────────────────────────────────────
def t_duplicate(A, state):
    print("\n== 5. Ctrl+D: 多行不索引漂移 ==")
    _reset(A, state, [("A", 1), ("B", 2), ("C", 3)])
    _select(A, [0, 1])                 # 升序多选: 原实现会把 A 复制两次
    A._kb_duplicate_row()
    check("5a. 升序多行复制结果 A,A,B,B,C",
          _cmds(state) == ["A", "A", "B", "B", "C"], _cmds(state))
    check("5b. 复制后选择保持在原行 A,B", _sel_cmds(A, state) == ["A", "B"],
          _sel_cmds(A, state))


# ── 6. TSV 纯逻辑 (行 ↔ 系统剪贴板文本) ───────────────────────────────
def t_tsv_pure(A, state):
    print("\n== 6. TSV 纯逻辑: 行与文本互转 · CRLF · 空行 · 截断 ==")
    rows = _rows([("A", 1), ("B", None)])
    tsv = A._rows_to_tsv(rows)
    check("6a. 每行 10 列 (9 个制表符)", all(ln.count("\t") == 9 for ln in tsv.split("\n")),
          [ln for ln in tsv.split("\n")])
    check("6b. TSV 行首字段为命令", tsv.split("\n")[0].split("\t")[:2] == ["A", "1"],
          tsv.split("\n")[0])

    back, has_tab = A._tsv_to_rows(tsv)
    check("6c. TSV 往返保持内容与行数",
          [sd.to_tuple() for sd in back] == [sd.to_tuple() for sd in rows],
          [sd.to_tuple() for sd in back])
    check("6d. 含制表符时 has_tab=True", has_tab is True, has_tab)

    crlf, _ = A._tsv_to_rows("A\t1\r\nB\t2\r\n")
    check("6e. CRLF 归一且去掉结尾空行",
          [sd.cmd_type for sd in crlf] == ["A", "B"], [sd.cmd_type for sd in crlf])
    check("6f. 参数按列落位", crlf[1].args[:2] == ["2", ""], crlf[1].args[:3])

    keep, _ = A._tsv_to_rows("A\n\nB\n")
    check("6g. 保留中间空行 (3 行)", [sd.cmd_type for sd in keep] == ["A", "", "B"],
          [sd.cmd_type for sd in keep])

    many, _ = A._tsv_to_rows("C" + "\t" + "\t".join(str(i) for i in range(1, 15)))
    check("6h. 超过 10 列截断", len(many[0].args) == 9
          and many[0].args[8] == "9", many[0].args)

    single, has_tab2 = A._tsv_to_rows("仅命令\n第二行")
    check("6i. 无制表符时按命令行处理",
          [sd.cmd_type for sd in single] == ["仅命令", "第二行"] and has_tab2 is False,
          ([sd.cmd_type for sd in single], has_tab2))
    check("6j. 空文本 → 空结果", A._tsv_to_rows("") == ([], False))


# ── 7. 系统剪贴板互通 + 单元格多行粘贴拆行 ───────────────────────────
def t_clipboard_interop(A, state):
    print("\n== 7. 系统剪贴板互通 · 多行粘贴拆行 ==")
    if not _set_clip(A, "A\t1\nB\t2\n"):
        warn("系统剪贴板不可用, 跳过本节")
        return

    # 7a/7b: Ctrl+C 把行写成 TSV 到系统剪贴板
    _reset(A, state, [("A", 1), ("B", 2)])
    _select(A, [0, 1])
    A._kb_copy()
    try:
        got = A.root.clipboard_get()
    except Exception:
        got = ""
    check("7a. Ctrl+C 写入系统剪贴板 (TSV)",
          got.split("\n")[0].split("\t")[:2] == ["A", "1"], repr(got[:40]))
    check("7b. 系统剪贴板含 2 行", len([ln for ln in got.split("\n")]) == 2,
          repr(got))

    # 7c/7d: Ctrl+V 优先解析系统剪贴板表格
    _reset(A, state, [("Z", 9)])
    if not _set_clip(A, "X\t7\nY\t8\n"):
        warn("系统剪贴板写入失败, 跳过")
        return
    _select(A, [0])
    A._kb_paste()
    check("7c. 从系统剪贴板粘贴 2 行", _cmds(state) == ["Z", "X", "Y"], _cmds(state))
    check("7d. 粘贴保留参数列", [sd.args[0] for sd in state._editor_rows] ==
          ["9", "7", "8"], [sd.args[0] for sd in state._editor_rows])

    # 7e/7f: 单元格内多行/制表符文本 → 拆行拆列写入
    _reset(A, state, [("A", 1), ("B", 2)])
    n = A._editor_paste_table(0, 1, "p1\np2\tq2\np3")
    check("7e. 多行文本拆成 3 行写入", n == 3, n)
    check("7f. 制表符拆列落位 (第 0 行参数1/参数2)",
          state._editor_rows[0].args[0] == "p1"
          and state._editor_rows[1].args[0] == "p2"
          and state._editor_rows[1].args[1] == "q2"
          and state._editor_rows[2].args[0] == "p3",
          [(sd.args[0], sd.args[1]) for sd in state._editor_rows])

    # 7g: 起点接近末尾时自动追加空行
    _reset(A, state, [("A", 1)])
    n2 = A._editor_paste_table(0, 0, "C1\nC2\nC3")
    check("7g. 行不足时自动追加", n2 == 3 and _cmds(state) == ["C1", "C2", "C3"],
          _cmds(state))
    check("7h. 追加行 id 非空且唯一",
          len({sd.id for sd in state._editor_rows}) == 3,
          [sd.id for sd in state._editor_rows])

    # 7i: 行 args 不足 9 位 (短列表构造) 时写入单元格不得越界
    _reset(A, state, [])
    state._editor_rows = [ScriptData("S", ["only"])]      # args 长度 = 1
    ok = True
    try:
        A._editor_paste_table(0, 3, "v3")
    except Exception as e:
        ok = False
        detail = repr(e)
    else:
        detail = state._editor_rows[0].args
    check("7i. 短 args 行写入第 3 列不越界", ok and len(state._editor_rows[0].args) == 9
          and state._editor_rows[0].args[2] == "v3", detail)


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
        t_cell_step(A, state_mod)
        t_move(A, state_mod)
        t_delete(A, state_mod)
        t_copy_paste(A, state_mod)
        t_duplicate(A, state_mod)
        t_tsv_pure(A, state_mod)
        t_clipboard_interop(A, state_mod)
    finally:
        # 不落盘: 清掉本次测试写入的内存状态
        state_mod._editor_rows = []
        state_mod.breakpoints.clear()
        state_mod._undo_stack.clear()
        state_mod._redo_stack.clear()
        state_mod._editor_clipboard = []
        state_mod._editor_modified = False
    print("\n=== {} (FAIL={}, WARN={}) ===".format(
        "ALL PASS" if _fails == 0 else "FAILED", _fails, _warns))
    return 0 if _fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
