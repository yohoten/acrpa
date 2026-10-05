# -*- coding: utf-8 -*-
"""script_io — 脚本 I/O 统一分派层（路线图 阶段二 · 第 5 项）。

本模块是脚本读写（``.xls`` / ``.xlsx`` / ``.acrpas``）的**唯一实现**。原先分散
在多处、各自硬编码「跳过前 2 行 + 取第 0 列命令」的 xls 解析，均收敛到这里：

    * ``ACRPA._editor_load_xls``            （编辑器载入）
    * ``ACRPA`` 自动运行加载                （执行前重读）
    * ``snippets._load_xls_rows``            （片段文件）
    * ``market_upload.check_script_commands``（上传前命令校验）

**例外（有意保留，不合并）**：``workflow._load_xls_script`` 仍使用其原有的
xlrd 实现 —— ``tools/_test_python_sandbox.py`` 的 ``o1`` 断言要求
``src/workflow.py`` 保持 git 无 diff（安全回归护栏），任何改动都会使其失败。
因此工作流步骤脚本当前仍仅支持 ``.xls``/``.xlsx``，暂不支持 ``.acrpas``。

两种格式
--------
* ``.acrpas``：JSON 一等格式，schema 版本化（见 :data:`SCHEMA_VERSION`）：:

      {"schema": 1, "name": ..., "created": ..., "meta": {...},
       "rows": [{"cmd": ..., "args": [9 个字符串]}],
       "vars": {}, "images": []}

  其中 ``rows[i]`` 与 ``version_manager`` 的 ``{"cmd","args"}`` 结构完全兼容，
  可与之互相复用。

* ``.xls`` / ``.xlsx``：遗留位置式 Excel —— 行0=表头、行1=「（标题行）」占位、
  数据从行2起，第0列=命令名，第1..9列=参数1..9。**写仅支持 ``.xls``（xlwt）**；
  ``.xlsx`` 读依赖 xlrd（xlrd>=2.0 不支持 xlsx，此时保持原有报错行为）。

归一化
------
载入（两种格式）与写出时，均把参数值中的 ``None`` 与字面量字符串 ``"None"``
（路线图 §4.1 提到的存量脏值）统一归一为 ``""``；参数列表补齐/裁剪为恰好 9 个。

零第三方新依赖：仅 ``json`` / ``xlrd`` / ``xlwt`` / ``utils``（均为现有依赖，
且 xlrd/xlwt/utils 均按需惰性导入）。
"""
import os
import json
import uuid

# ── 常量 ──────────────────────────────────────────────────────────────
ACRPAS_EXT = ".acrpas"
SCHEMA_VERSION = 1

# 迁移函数表: {起始 schema 版本: doc -> doc}，逐级升到 SCHEMA_VERSION。
# 当前唯一版本即 1（恒等），留门供未来格式演进。
MIGRATIONS = {1: lambda doc: doc}

# xls 布局常量（与历史实现逐字对齐，勿轻易改动）
_HEADER_CMD = "命令类型"
_TITLE_PLACEHOLDER = "（标题行）"
_NUM_ARGS = 9


# ── 归一化工具 ────────────────────────────────────────────────────────
def _norm_arg(value):
    """把单个参数值归一为 9 参数约定下的字符串。

    ``None`` → ``""``；字面量 ``"None"`` → ``""``；其余 → ``str(value)``。
    """
    if value is None:
        return ""
    s = value if isinstance(value, str) else str(value)
    if s == "None":
        return ""
    return s


def _norm_cmd(value):
    """命令名归一为字符串（None → ""，其余 str）。命令名不做 "None" 清洗。"""
    if value is None:
        return ""
    return value if isinstance(value, str) else str(value)


def _norm_args(args):
    """把参数序列补齐/裁剪为恰好 9 个已归一字符串。"""
    out = []
    if args is not None:
        try:
            for v in args:
                out.append(_norm_arg(v))
        except TypeError:
            out = []
    while len(out) < _NUM_ARGS:
        out.append("")
    return out[:_NUM_ARGS]


# ── schema 迁移 ───────────────────────────────────────────────────────
def _migrate(doc):
    """按 ``schema`` 版本逐级迁移文档至 :data:`SCHEMA_VERSION`。"""
    if not isinstance(doc, dict):
        raise ValueError("acrpas 文档必须是 JSON 对象")
    ver = doc.get("schema", SCHEMA_VERSION)
    try:
        ver = int(ver)
    except Exception:
        ver = SCHEMA_VERSION
    if ver > SCHEMA_VERSION:
        raise ValueError(
            "acrpas schema {} 高于本程序支持的 {}，请升级 ACRPA".format(
                ver, SCHEMA_VERSION))
    while ver < SCHEMA_VERSION:
        fn = MIGRATIONS.get(ver)
        if fn is None:
            raise ValueError("缺少 schema {} 的迁移函数".format(ver))
        doc = fn(doc)
        ver += 1
    doc["schema"] = SCHEMA_VERSION
    return doc


# ── 内存模型 ⇄ .acrpas 文档 ───────────────────────────────────────────
def _new_row_id():
    """生成一个非空的行标识（8 位十六进制 uuid），与 ScriptData.id 同构。"""
    return uuid.uuid4().hex[:8]


def rows_to_acrpas(rows, meta=None):
    """``list[ScriptData]`` → ``.acrpas`` 文档 dict。

    Args:
        rows: ``ScriptData`` 列表（``None`` 视为空）。
        meta: 可选 dict，识别顶层键 ``name`` / ``created`` / ``meta`` /
              ``vars`` / ``images`` 作为覆盖；其余键忽略。

    Returns:
        dict，``rows[i]`` 与 ``version_manager`` 的 ``{"cmd","args"}`` 兼容；
        额外附带可选字段 ``"id"``（稳定行标识，缺失时自动生成）。
    """
    doc = {
        "schema": SCHEMA_VERSION,
        "name": "",
        "created": "",
        "meta": {},
        "rows": [],
        "vars": {},
        "images": [],
    }
    if isinstance(meta, dict):
        if "name" in meta:
            doc["name"] = meta.get("name") or ""
        if "created" in meta:
            doc["created"] = meta.get("created") or ""
        if isinstance(meta.get("meta"), dict):
            doc["meta"] = dict(meta["meta"])
        if isinstance(meta.get("vars"), dict):
            doc["vars"] = dict(meta["vars"])
        if isinstance(meta.get("images"), (list, tuple)):
            doc["images"] = list(meta["images"])
    if not doc["created"]:
        import datetime
        doc["created"] = datetime.datetime.now().isoformat(timespec="seconds")
    for sd in rows or []:
        doc["rows"].append({
            "cmd": _norm_cmd(getattr(sd, "cmd_type", "")),
            "args": _norm_args(getattr(sd, "args", None)),
            # 稳定行标识: 可选字段 (schema 仍为 1, 向后兼容; 旧文档无此键)
            "id": getattr(sd, "id", None) or _new_row_id(),
        })
    return doc


def acrpas_to_rows(doc):
    """``.acrpas`` 文档 dict → ``list[ScriptData]``（含 schema 迁移 + 归一化）。

    兼容 ``args`` 缺省 / 长度不足（补齐 9）/ 具名参数（dict，按声明序取值）；
    并把字符串 ``"None"`` 归一为 ``""``。若 ``rows[i]`` 带 ``"id"`` 则原样保留，
    否则（旧文档 / 列表形式）由 ``ScriptData`` 自动生成新 id。
    """
    from scriptdata import ScriptData
    if not isinstance(doc, dict):
        raise ValueError("acrpas 文档必须是 JSON 对象")
    doc = _migrate(doc)
    rows = []
    for item in doc.get("rows") or []:
        rid = None
        if isinstance(item, dict):
            cmd = _norm_cmd(item.get("cmd", ""))
            args = item.get("args")
            if isinstance(args, dict):
                # 具名参数（路线图 §4.2 预留）：按声明序取值
                args = list(args.values())
            rid = item.get("id")
        elif isinstance(item, (list, tuple)):
            # 容错：允许 ["cmd", arg1, arg2, ...] 形式
            seq = list(item)
            cmd = _norm_cmd(seq[0]) if seq else ""
            args = seq[1:]
        else:
            cmd, args = "", None
        rows.append(ScriptData(cmd, _norm_args(args), id=rid))
    return rows


# ── 读 ────────────────────────────────────────────────────────────────
def load_acrpas(path):
    """读取 ``.acrpas``（UTF-8 JSON）→ ``list[ScriptData]``。"""
    with open(path, "r", encoding="utf-8") as f:
        doc = json.load(f)
    return acrpas_to_rows(doc)


def load_xls(path):
    """读取 ``.xls``/``.xlsx``（xlrd，跳过前 2 行）→ ``list[ScriptData]``。

    行为与历史 5 处实现等价：跳过空白行（``not row[0]``），参数补齐 9 个，
    额外把 ``"None"`` 归一为 ``""``。
    """
    import xlrd
    from scriptdata import ScriptData
    wb = xlrd.open_workbook(path)
    try:
        sheet = wb.sheet_by_index(0)
        rows = []
        for r in range(2, sheet.nrows):
            vals = sheet.row_values(r)
            if not vals or not vals[0]:
                continue
            cmd = _norm_cmd(vals[0])
            rows.append(ScriptData(cmd, _norm_args(vals[1:10])))
        return rows
    finally:
        try:
            wb.release_resources()
        except Exception:
            pass


def load_xls_rows(path):
    """读取 ``.xls`` 原始数据行（跳过前 2 行）→ ``list[list]``。

    每行形如 ``[cmd, arg1..arg9]``（共 10 项，均已归一化）。供需要原始行、
    而非 ``ScriptData`` 的调用方使用。
    """
    import xlrd
    wb = xlrd.open_workbook(path)
    try:
        sheet = wb.sheet_by_index(0)
        out = []
        for r in range(2, sheet.nrows):
            vals = sheet.row_values(r)
            if not vals or not vals[0]:
                continue
            out.append([_norm_cmd(vals[0])] + _norm_args(vals[1:10]))
        return out
    finally:
        try:
            wb.release_resources()
        except Exception:
            pass


def load_script(path):
    """按扩展名分派读取脚本 → ``list[ScriptData]``。

    ``.acrpas`` → JSON；其它（``.xls``/``.xlsx``）→ xlrd。
    """
    if not path:
        raise ValueError("脚本路径为空")
    ext = os.path.splitext(str(path))[1].lower()
    if ext == ACRPAS_EXT:
        return load_acrpas(path)
    return load_xls(path)


def iter_commands(path):
    """只读返回脚本的命令名列表（不含参数），供市场校验等使用。

    ``.acrpas`` → 每行 ``cmd``；其它 → xlrd 第 0 列（行0/行1 跳过）。
    读取失败原样抛出，由调用方决定是否降级为空列表。
    """
    if not path:
        return []
    ext = os.path.splitext(str(path))[1].lower()
    if ext == ACRPAS_EXT:
        out = []
        for sd in load_acrpas(path):
            if sd.cmd_type:
                out.append(sd.cmd_type)
        return out
    import xlrd
    wb = xlrd.open_workbook(path)
    try:
        sheet = wb.sheet_by_index(0)
        out = []
        for r in range(2, sheet.nrows):
            name = str(sheet.cell_value(r, 0)).strip()
            if name:
                out.append(name)
        return out
    finally:
        try:
            wb.release_resources()
        except Exception:
            pass


# ── 写（原子） ────────────────────────────────────────────────────────
def save_acrpas(path, rows, meta=None):
    """原子写入 ``.acrpas``（UTF-8 JSON，2 空格缩进）。失败不破坏原文件。"""
    from utils import atomic_save
    doc = rows_to_acrpas(rows, meta=meta)
    text = json.dumps(doc, ensure_ascii=False, indent=2)

    def _writer(tmp):
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)

    return atomic_save(path, _writer, backup=True)


def save_xls(path, rows):
    """原子写入 ``.xls``（xlwt，格式与历史 ``_cmd_save`` 完全一致）。

    行0=``命令类型`` + ``参数1..9``；行1=``（标题行）`` 占位；数据从行2起。
    """
    from utils import atomic_save
    import xlwt
    wb = xlwt.Workbook()
    ws = wb.add_sheet("Sheet1")
    ws.write(0, 0, _HEADER_CMD)
    for j in range(1, _NUM_ARGS + 1):
        ws.write(0, j, "参数{}".format(j))
    ws.write(1, 0, _TITLE_PLACEHOLDER)
    for i, sd in enumerate(rows or [], start=2):
        ws.write(i, 0, _norm_cmd(getattr(sd, "cmd_type", "")))
        args = _norm_args(getattr(sd, "args", None))
        for j in range(_NUM_ARGS):
            ws.write(i, j + 1, args[j])
    return atomic_save(path, lambda tmp: wb.save(tmp), backup=True)


def save_script(path, rows, meta=None):
    """按扩展名分派原子写入脚本。

    ``.acrpas`` → JSON；其它（``.xls``）→ xls（xlwt）。失败不破坏原文件
    （``utils.atomic_save``：同目录 ``.tmp`` + ``.bak`` 前像 + ``os.replace``）。
    """
    if not path:
        raise ValueError("脚本路径为空")
    ext = os.path.splitext(str(path))[1].lower()
    if ext == ACRPAS_EXT:
        return save_acrpas(path, rows, meta=meta)
    return save_xls(path, rows)


__all__ = [
    "ACRPAS_EXT", "SCHEMA_VERSION", "MIGRATIONS",
    "rows_to_acrpas", "acrpas_to_rows",
    "load_acrpas", "load_xls", "load_xls_rows", "load_script", "iter_commands",
    "save_acrpas", "save_xls", "save_script",
]
