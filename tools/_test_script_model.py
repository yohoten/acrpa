# -*- coding: utf-8 -*-
"""script_model / 行 ID 专项自测 —— 路线图 阶段二 · 第 6 项。

覆盖:
    1. id 稳定性     : 列表内移动(上/下交换)后各行 id 不变; 删除后其余 id 不变;
    2. id 往返       : save_script(.acrpas) → load_script 后 rows[i].id 一致;
                       无 id 的旧文档读入后每行 id 非空且唯一;
    3. 克隆产生新 id : _clone_snippet_rows 等价路径产生新 id, 与原行不同;
    4. 模型通知      : on_row_invalidated/index_of/id_of; 订阅者异常隔离;
    5. to_tuple 契约 : 仍为 10 项 (回归护栏, id 不进列)。
可选(默认跳过, 设 ACRPA_GUI_TEST=1 启用, 需真实窗口):
    6. 实机等价      : _update_row_inplace 与 _editor_sync_to_tree 的
                       tree values/text 逐行等价。

全自动、不弹窗、不联网; 用临时目录, 跑完清理。
运行:  .venv\\Scripts\\python.exe tools\\_test_script_model.py
退出码: 0 = 全部断言通过(允许 [WARN]); 1 = 存在 [FAIL]
"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile

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
import script_io  # noqa: E402
from script_model import ScriptTableModel  # noqa: E402

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


# ── 1. id 稳定性 ─────────────────────────────────────────────────────
def t_id_stable():
    print("\n== 1. id 稳定性 (移动/删除) ==")
    rows = [ScriptData("等待", ["1"]),
            ScriptData("坐标", ["2"]),
            ScriptData("按键", ["3"])]
    ids = [r.id for r in rows]
    check("1a. 每行 id 非空字符串",
          all(isinstance(i, str) and i for i in ids), ids)
    check("1b. 行内 id 唯一", len(set(ids)) == len(ids), ids)

    # 交换 0<->1 (等价于上移/下移一次)
    rows[0], rows[1] = rows[1], rows[0]
    check("1c. 交换后各行 id 保持 (随对象移动)",
          [r.id for r in rows] == [ids[1], ids[0], ids[2]],
          [r.id for r in rows])

    # 删除中间一行
    del rows[1]  # 移除了原 rows[0] (id=ids[0])
    check("1d. 删除后其余 id 不变",
          [r.id for r in rows] == [ids[1], ids[2]],
          [r.id for r in rows])


# ── 2. id 往返 (.acrpas) ─────────────────────────────────────────────
def t_id_roundtrip(tmp):
    print("\n== 2. id 往返 (.acrpas) ==")
    rows = [ScriptData("等待", ["1"]),
            ScriptData("坐标", ["10", "20"]),
            ScriptData("输入", ["hello"])]
    ids = [r.id for r in rows]
    p = os.path.join(tmp, "ids.acrpas")
    script_io.save_script(p, rows, meta={"name": "ids"})

    with open(p, "r", encoding="utf-8") as f:
        doc = json.load(f)
    check("2a. 文档 rows[i] 均含 id 字段",
          all(isinstance(r, dict) and r.get("id") for r in doc.get("rows", [])),
          doc.get("rows"))
    check("2b. SCHEMA_VERSION 未升版 (仍为 1)",
          script_io.SCHEMA_VERSION == 1, script_io.SCHEMA_VERSION)
    check("2c. 文档 schema 字段 == 1", doc.get("schema") == 1, doc.get("schema"))

    back = script_io.load_script(p)
    check("2d. 往返行数一致", len(back) == len(rows), len(back))
    check("2e. 往返保留逐行 id",
          [r.id for r in back] == ids, [r.id for r in back])

    # 无 id 的旧文档 → 自动生成, 非空且唯一
    old_doc = {"schema": 1, "rows": [
        {"cmd": "等待", "args": ["1"]},
        {"cmd": "坐标", "args": ["1", "2"]},
        {"cmd": "输入", "args": []},
    ]}
    old = script_io.acrpas_to_rows(old_doc)
    oids = [r.id for r in old]
    check("2f. 旧文档(in-memory) 每行 id 非空",
          all(isinstance(i, str) and i for i in oids), oids)
    check("2g. 旧文档(in-memory) id 唯一",
          len(set(oids)) == len(oids), oids)

    p2 = os.path.join(tmp, "old.acrpas")
    with open(p2, "w", encoding="utf-8") as f:
        json.dump(old_doc, f, ensure_ascii=False)
    old2 = script_io.load_script(p2)
    o2 = [r.id for r in old2]
    check("2h. 旧文档(文件) 读入 id 非空且唯一",
          all(o2) and len(set(o2)) == len(o2), o2)


# ── 3. 克隆产生新 id ─────────────────────────────────────────────────
def t_clone_new_ids():
    print("\n== 3. 克隆产生新 id ==")
    rows = [ScriptData("等待", ["1"]),
            ScriptData("坐标", ["2"])]
    ids = [r.id for r in rows]
    # _clone_snippet_rows 等价路径 (ACRPA._clone_snippet_rows 逐字相同)
    clones = [ScriptData(r.cmd_type, list(r.args)) for r in rows]
    cids = [c.id for c in clones]
    check("3a. 克隆 id 均非空", all(cids), cids)
    check("3b. 克隆 id 与原行逐行不同",
          all(c != o for c, o in zip(cids, ids)), (cids, ids))
    check("3c. 克隆之间 id 唯一", len(set(cids)) == len(cids), cids)
    check("3d. 克隆内容保持 (to_tuple)",
          [c.to_tuple() for c in clones] == [r.to_tuple() for r in rows])


# ── 4. 模型通知 ──────────────────────────────────────────────────────
def t_model_notifications():
    print("\n== 4. ScriptTableModel 通知/索引 ==")
    rows = [ScriptData("等待", ["1"]),
            ScriptData("坐标", ["2"])]
    m = ScriptTableModel(rows)
    check("4a. rows 为真源引用 (非拷贝)", m.rows is rows)
    check("4b. id_of(0) 正确", m.id_of(0) == rows[0].id)
    check("4c. id_of 越界返回 None", m.id_of(5) is None and m.id_of(-1) is None)
    check("4d. index_of 命中", m.index_of(rows[1].id) == 1)
    check("4e. index_of 未命中/None → -1",
          m.index_of("no-such-id") == -1 and m.index_of(None) == -1)

    calls = []

    def cb(index, reason):
        calls.append((index, reason))

    m.on_row_invalidated(cb)
    m.invalidate_row(1)
    check("4f. invalidate_row 触发订阅 (默认 reason=update)",
          calls == [(1, "update")], calls)
    m.invalidate_row(1, reason="edit")
    check("4g. reason 透传", calls[-1] == (1, "edit"), calls[-1])

    calls.clear()
    m.invalidate_all()
    check("4h. invalidate_all 广播 (index=None)",
          calls == [(None, "structure")], calls)

    calls.clear()
    m.invalidate_row(9)
    check("4i. 越界 invalidate_row 不广播", calls == [], calls)

    # 订阅者异常隔离: bad 抛错不影响 good, 主流程不抛出
    order = []

    def bad(index, reason):
        order.append("bad")
        raise RuntimeError("boom-subscriber")

    def good(index, reason):
        order.append("good")

    m.off_row_invalidated(cb)
    m.on_row_invalidated(bad)
    m.on_row_invalidated(good)
    with contextlib.redirect_stderr(io.StringIO()):  # 抑制模型打印的堆栈
        try:
            m.invalidate_row(0)
            raised = False
        except Exception:
            raised = True
    check("4j. 主流程不因订阅者异常抛出", not raised)
    check("4k. 单个订阅者异常不影响其它订阅者",
          order == ["bad", "good"], order)

    order.clear()
    m.off_row_invalidated(bad)
    m.invalidate_row(0)
    check("4l. off_row_invalidated 后不再回调", order == ["good"], order)


# ── 5. to_tuple 契约回归 ─────────────────────────────────────────────
def t_to_tuple_contract():
    print("\n== 5. to_tuple 10 列契约 (回归护栏) ==")
    sd = ScriptData("等待", ["1", "2", "3", "4", "5", "6", "7", "8", "9"])
    check("5a. to_tuple 长度 == 10", len(sd.to_tuple()) == 10, len(sd.to_tuple()))
    check("5b. id 不在 to_tuple 中", sd.id not in sd.to_tuple(), sd.id)
    sd2 = ScriptData.from_xlrd_row_values(["等待", "2"] + [""] * 8)
    check("5c. from_xlrd_row_values 仍 ok 且生成非空 id",
          sd2.cmd_type == "等待" and len(sd2.args) == 9 and bool(sd2.id),
          (sd2.cmd_type, len(sd2.args), sd2.id))


# ── 6. (可选) 实机等价 ───────────────────────────────────────────────
def t_gui_equivalence():
    print("\n== 6. (可选) _update_row_inplace vs 全量重建 实机等价 ==")
    if os.environ.get("ACRPA_GUI_TEST") != "1":
        warn("跳过实机等价 (设 ACRPA_GUI_TEST=1 启用, 需真实窗口会话)")
        return
    try:
        import ACRPA
        import app
        app.build()
    except Exception as e:  # 无显示/构建失败 → 记 WARN, 不假通过
        warn("GUI 构建不可用, 跳过: {!r}".format(e))
        return

    import state as state_mod
    tv = ACRPA.tree
    rows = [ScriptData("等待", ["1"]),
            ScriptData("坐标", ["10", "20"]),
            ScriptData("按键", ["a"])]
    state_mod._editor_rows = rows
    state_mod.breakpoints.clear()
    ACRPA._editor_sync_to_tree()
    tv.update_idletasks()

    # 单行就地刷新 (第 2 行数据变更)
    rows[1].args[0] = "999"
    ACRPA._update_row_inplace(1)
    inplace = [(tv.item(iid, "text"), tv.item(iid, "values"))
               for iid in tv.get_children()]

    # 全量重建得到期望
    ACRPA._editor_sync_to_tree()
    rebuild = [(tv.item(iid, "text"), tv.item(iid, "values"))
               for iid in tv.get_children()]

    check("6a. 行数一致", len(inplace) == len(rebuild),
          (len(inplace), len(rebuild)))
    check("6b. 就地刷新 == 全量重建 (text/values)", inplace == rebuild,
          (inplace, rebuild))
    check("6c. 就地刷新后第 2 行值已更新",
          "999" in tuple(inplace[1][1]), inplace[1])

    # 断点 toggle 就地刷新等价
    state_mod.breakpoints.add(2)
    ACRPA._update_row_inplace(1)
    txt_inplace = [tv.item(iid, "text") for iid in tv.get_children()]
    ACRPA._editor_sync_to_tree()
    txt_rebuild = [tv.item(iid, "text") for iid in tv.get_children()]
    check("6d. 断点标记就地 == 全量重建 (text)", txt_inplace == txt_rebuild,
          (txt_inplace, txt_rebuild))


def main():
    tmp = tempfile.mkdtemp(prefix="acrpa_model_")
    try:
        t_id_stable()
        t_id_roundtrip(tmp)
        t_clone_new_ids()
        t_model_notifications()
        t_to_tuple_contract()
        t_gui_equivalence()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\n=== {} (FAIL={}, WARN={}) ===".format(
        "ALL PASS" if _fails == 0 else "FAILED", _fails, _warns))
    return 0 if _fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
