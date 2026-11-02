# -*- coding: utf-8 -*-
"""script_io 专项自测 —— `.acrpas` 往返 / xls↔acrpas 等价 / 旧 xls 可读 / 原子写。

阶段二 · 第 5 项。全自动、不弹窗、不联网；用临时目录，跑完清理。

运行:  python tools/_test_script_io.py
退出码: 0 = 全部断言通过（允许 [WARN]）；1 = 存在 [FAIL]
"""
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

try:  # utils 顶层 import tkinter；headless 时忽略
    import tkinter  # noqa: F401
except Exception:
    pass

import script_io  # noqa: E402
from scriptdata import ScriptData  # noqa: E402

try:
    import xlrd  # noqa: F401
    import xlwt  # noqa: F401
    _HAVE_XLS = True
except Exception:
    _HAVE_XLS = False

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


def _sample_rows():
    return [
        ScriptData("等待", ["1", "", "", "", "", "", "", "", ""]),
        ScriptData("坐标", ["100", "200", "左", "1", "0.1", "", "", "", ""]),
        ScriptData("输入", ["hello", "", "", "", "", "", "", "", ""]),
    ]


def _boom(tmp_path):
    raise RuntimeError("boom")


def _run(tmp):
    rows = _sample_rows()

    # ── 1. .acrpas 往返（逐字段） ──
    print("\n== 1. .acrpas 往返 ==")
    p = os.path.join(tmp, "rt.acrpas")
    script_io.save_script(p, rows, meta={"name": "rt"})
    check("1a. .acrpas 文件已生成", os.path.exists(p))
    with open(p, "r", encoding="utf-8") as f:
        doc = json.load(f)
    check("1b. schema == SCHEMA_VERSION", doc.get("schema") == script_io.SCHEMA_VERSION,
          doc.get("schema"))
    check("1c. meta.name 写入", doc.get("name") == "rt", doc.get("name"))
    check("1d. rows 数量一致", len(doc.get("rows") or []) == len(rows))
    back = script_io.load_script(p)
    check("1e. 往返行数一致", len(back) == len(rows), len(back))
    same = [a.to_tuple() for a in rows] == [b.to_tuple() for b in back]
    check("1f. 往返逐字段相等 (to_tuple)", same)
    check("1g. 每行 args 恰好 9 个", all(len(r.args) == 9 for r in back))

    # ── 2. 归一化：字符串 "None" / args 缺省 / 超长裁剪 ──
    print("\n== 2. acrpas 归一化 ==")
    doc2 = {"schema": script_io.SCHEMA_VERSION, "rows": [
        {"cmd": "输入", "args": ["None", "x"]},   # 脏值 + 长度不足
        {"cmd": "等待"},                          # args 缺省
        {"cmd": "坐标", "args": ["1"] * 20},      # 超长 → 裁剪
    ]}
    rs = script_io.acrpas_to_rows(doc2)
    check("2a. 字符串 'None' 归一为 ''", rs[0].args[0] == "", repr(rs[0].args[0]))
    check("2b. 其余参数保留", rs[0].args[1] == "x", repr(rs[0].args[1]))
    check("2c. 长度补齐 9", len(rs[0].args) == 9, len(rs[0].args))
    check("2d. args 缺省 → 9 个空串", rs[1].args == [""] * 9, rs[1].args)
    check("2e. 超长裁剪为 9", len(rs[2].args) == 9, len(rs[2].args))

    # ── 3. xls ↔ acrpas 等价 + xls 结构不变 ──
    print("\n== 3. xls ↔ acrpas 等价 ==")
    if _HAVE_XLS:
        px = os.path.join(tmp, "s.xls")
        pa = os.path.join(tmp, "s.acrpas")
        script_io.save_script(px, rows)
        script_io.save_script(pa, rows)
        rx = script_io.load_script(px)
        ra = script_io.load_script(pa)
        check("3a. xls↔acrpas 逐行完全相等",
              [r.to_tuple() for r in rx] == [r.to_tuple() for r in ra])
        wb = xlrd.open_workbook(px)
        sh = wb.sheet_by_index(0)
        check("3b. 行0 表头 '命令类型'", sh.row_values(0)[0] == "命令类型",
              sh.row_values(0)[0])
        check("3c. 行1 '（标题行）' 占位", sh.row_values(1)[0] == "（标题行）",
              sh.row_values(1)[0])
        check("3d. 数据从行2起", sh.row_values(2)[0] == "等待", sh.row_values(2)[0])
        wb.release_resources()
    else:
        warn("缺 xlrd/xlwt，跳过 xls 等价/结构断言")

    # ── 4. 旧 xls 可读（真实模板 + 自建 fixture） ──
    print("\n== 4. 旧 .xls 可读 ==")
    tmpl = os.path.join(_ROOT, "template", "脚本模板.xls")
    if _HAVE_XLS and os.path.exists(tmpl):
        r = script_io.load_script(tmpl)
        check("4a. 真实模板可读 (行数>0)", len(r) > 0, len(r))
        check("4b. 首命令为非空字符串",
              isinstance(r[0].cmd_type, str) and r[0].cmd_type != "", r[0].cmd_type)
        check("4c. 每行 args=9", all(len(x.args) == 9 for x in r))
    else:
        warn("未找到 template/脚本模板.xls，跳过真实模板断言")
    if _HAVE_XLS:
        fx = os.path.join(tmp, "fixture.xls")
        wb = xlwt.Workbook()
        ws = wb.add_sheet("Sheet1")
        ws.write(0, 0, "命令类型")
        ws.write(1, 0, "（标题行）")
        ws.write(2, 0, "等待")
        ws.write(2, 1, "2.5")
        ws.write(3, 0, "坐标")
        ws.write(3, 1, "10")
        ws.write(3, 2, "20")
        wb.save(fx)
        r = script_io.load_script(fx)
        check("4d. 自建 xls fixture 行数==2", len(r) == 2, len(r))
        check("4e. fixture 首行 cmd/arg",
              r[0].cmd_type == "等待" and r[0].args[0] == "2.5",
              (r[0].cmd_type, r[0].args[0]))

    # ── 5. iter_commands 双格式 ──
    print("\n== 5. iter_commands 双格式 ==")
    pa2 = os.path.join(tmp, "cmds.acrpas")
    script_io.save_script(pa2, [ScriptData("等待", ["1"]),
                                ScriptData("坐标", ["1", "2"])])
    check("5a. iter_commands(.acrpas)", script_io.iter_commands(pa2) == ["等待", "坐标"],
          script_io.iter_commands(pa2))
    if _HAVE_XLS:
        px2 = os.path.join(tmp, "cmds.xls")
        wb = xlwt.Workbook()
        ws = wb.add_sheet("S")
        ws.write(0, 0, "命令类型")
        ws.write(1, 0, "标题")
        ws.write(2, 0, "等待")
        ws.write(3, 0, "坐标")
        wb.save(px2)
        check("5b. iter_commands(.xls)", script_io.iter_commands(px2) == ["等待", "坐标"],
              script_io.iter_commands(px2))
    else:
        warn("缺 xlrd，跳过 iter_commands(.xls)")

    # ── 6. 原子写：.bak 备份 / 失败不破坏 / 无 .tmp 残留 ──
    print("\n== 6. 原子写 ==")
    p6 = os.path.join(tmp, "atomic.acrpas")
    script_io.save_script(p6, rows)
    with open(p6, "rb") as f:
        v1 = f.read()
    script_io.save_script(p6, rows[:1])  # 覆盖写 → 触发 .bak
    check("6a. 覆盖写生成 .bak", os.path.exists(p6 + ".bak"))
    with open(p6 + ".bak", "rb") as f:
        bak = f.read()
    check("6b. .bak 为覆盖前内容", bak == v1)
    from utils import atomic_save
    with open(p6, "rb") as f:
        before = f.read()
    threw = False
    try:
        atomic_save(p6, _boom)
    except RuntimeError:
        threw = True
    check("6c. 写失败原样抛出", threw)
    with open(p6, "rb") as f:
        after = f.read()
    check("6d. 写失败不破坏原文件", before == after)
    check("6e. 无残留 .tmp", not os.path.exists(p6 + ".tmp"))

    # ── 7. schema 版本 + 迁移门 ──
    print("\n== 7. schema / 迁移门 ==")
    check("7a. SCHEMA_VERSION 为正整数",
          isinstance(script_io.SCHEMA_VERSION, int) and script_io.SCHEMA_VERSION >= 1,
          script_io.SCHEMA_VERSION)
    check("7b. MIGRATIONS 含当前版本门",
          script_io.SCHEMA_VERSION in script_io.MIGRATIONS)
    raised = False
    try:
        script_io.acrpas_to_rows({"schema": script_io.SCHEMA_VERSION + 1, "rows": []})
    except ValueError:
        raised = True
    check("7c. 高于支持版本被拒绝 (ValueError)", raised)

    # ── 8. 稳定行 id 往返 (阶段二第 6 项; 不删改以上既有断言) ──
    print("\n== 8. 稳定行 id 往返 ==")
    check("8a. SCHEMA_VERSION 仍为 1 (id 为可选字段, 未升版)",
          script_io.SCHEMA_VERSION == 1, script_io.SCHEMA_VERSION)
    ids = [r.id for r in rows]
    check("8b. 源行 id 非空且唯一",
          all(ids) and len(set(ids)) == len(ids), ids)
    pid = os.path.join(tmp, "ids.acrpas")
    script_io.save_script(pid, rows)
    with open(pid, "r", encoding="utf-8") as f:
        doci = json.load(f)
    check("8c. rows_to_acrpas 每行写出 id 字段",
          all(isinstance(r, dict) and r.get("id") for r in doci.get("rows", [])),
          doci.get("rows"))
    check("8d. 写出的 id 与源行一致",
          [r["id"] for r in doci.get("rows", [])] == ids,
          [r.get("id") for r in doci.get("rows", [])])
    backi = script_io.load_script(pid)
    check("8e. load 后逐行 id 一致",
          [r.id for r in backi] == ids, [r.id for r in backi])

    # 无 id 的旧文档 → 自动生成, 非空且唯一
    old = script_io.acrpas_to_rows({"schema": 1, "rows": [
        {"cmd": "等待", "args": ["1"]},
        {"cmd": "坐标", "args": ["1", "2"]},
    ]})
    oids = [r.id for r in old]
    check("8f. 旧文档(无 id) 读入 id 非空且唯一",
          all(oids) and len(set(oids)) == len(oids), oids)
    check("8g. 旧文档读入内容不变 (to_tuple)",
          [r.to_tuple() for r in old] ==
          [("等待", "1") + ("",) * 8, ("坐标", "1", "2") + ("",) * 7],
          [r.to_tuple() for r in old])
    if _HAVE_XLS:
        pxid = os.path.join(tmp, "idfree.xls")
        script_io.save_script(pxid, rows)
        rx = script_io.load_script(pxid)
        rids = [r.id for r in rx]
        check("8h. xls 路径不变: 读入生成非空唯一 id",
              all(rids) and len(set(rids)) == len(rids), rids)
    else:
        warn("缺 xlrd/xlwt，跳过 xls id 断言")


def main():
    tmp = tempfile.mkdtemp(prefix="acrpas_io_")
    try:
        _run(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\n=== {} (FAIL={}, WARN={}) ===".format(
        "ALL PASS" if _fails == 0 else "FAILED", _fails, _warns))
    return 0 if _fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
