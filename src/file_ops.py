# -*- coding: utf-8 -*-
"""file_ops — 文件/目录族动作的纯逻辑实现（L2 动作库扩充，8 条命令的后端）。

设计约束（务必保持）
--------------------
* **纯 stdlib**: 仅 ``os`` / ``shutil`` / ``fnmatch`` / ``tempfile`` / ``time`` /
  ``ctypes``；不引入任何第三方依赖（回收站走 ``ctypes`` 调
  ``shell32.SHFileOperationW``，不需要 pywin32）；
* **import 期零副作用**: ``ctypes.windll`` 只在函数体内访问，因此本模块可被
  无头环境（CI / ``script_validate``）安全 import；
* **不抛异常**: 所有对外函数统一返回 ``(ok, message, value)``，错误消息为中文
  可读文本，由调用方（``commands.py`` 的 handler）决定写日志 / 写变量；
* **路径解析**: 相对路径一律按 ``script_dir``（脚本所在目录）解析，与引擎既有
  约定一致；**空路径直接失败**，绝不静默降级到当前目录或盘根。

对外接口
--------
    resolve(path, script_dir="")                  → 绝对路径（空 → ""）
    copy_path(src, dst, overwrite, script_dir)    → (ok, msg, 目标绝对路径)
    move_path(src, dst, overwrite, script_dir)    → (ok, msg, 目标绝对路径)
    delete_path(path, recursive, use_recycle, script_dir, confirm=None)
    make_dir(path, ignore_exists, script_dir)
    list_dir(path, pattern, sort_by, script_dir, dirs_first=True)
    read_text(path, encoding, script_dir)
    write_text(path, content, mode, encoding, script_dir)
    path_exists(path, script_dir)

返回值的 ``value`` 语义
----------------------
* 复制/移动 → 目标绝对路径（便于脚本用 ``目标变量名`` 落库）；
* 删除 → 被删除的绝对路径；
* 创建目录 → 目录绝对路径；
* 列目录 → 换行连接的名称串（与「设置变量」的字符串语义一致）；
* 读文件 → 文件内容（str）；
* 写文件 → 写入的绝对路径；
* 存在性 → ``"1"`` / ``"0"``（与 ``JudgeResult:=1/0`` 契约一致）。
"""
import fnmatch
import os
import shutil
import tempfile
import time

# ── 回收站常量（SHFileOperationW） ────────────────────────────────────
_FO_DELETE = 3
_FOF_SILENT = 0x0004
_FOF_NOCONFIRMATION = 0x0010
_FOF_ALLOWUNDO = 0x0040
_FOF_NOCONFIRMMKDIR = 0x0200
_FOF_NOERRORUI = 0x0400

#: 排序方式 → 内部键
_SORT_KEYS = ("名", "时间", "大小")

#: 支持的编码（auto = 先 utf-8 再 gbk，不做 BOM 猜测之外的推断）
_ENCODINGS = ("utf-8", "gbk", "auto")


def _clean(value):
    """归一化入参 → 去空白字符串；``None`` / ``empty`` / ``none`` 一律视为空。"""
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in ("none", "null", "-"):
        return ""
    return text


def _to_str(value):
    """把返回值里的 path 等对象统一成 str（str 直接返回，其余转字符串）。"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return str(value)
    except Exception:
        return ""


# ── 路径解析 ─────────────────────────────────────────────────────────
def resolve(path, script_dir=""):
    """相对路径按 ``script_dir`` 解析 → 绝对路径；路径为空返回 ``""``。

    支持 ``%VAR%`` / ``~`` 展开（自动化脚本里出现环境变量是常见需求）。
    """
    text = _clean(path)
    if not text:
        return ""
    try:
        text = os.path.expandvars(os.path.expanduser(text))
    except Exception:
        pass
    try:
        if not os.path.isabs(text) and _clean(script_dir):
            text = os.path.join(_clean(script_dir), text)
        return os.path.abspath(text)
    except Exception:
        return text


def _require(path, what="路径"):
    """校验路径非空 → (绝对路径, 错误消息)。"""
    text = _clean(path)
    if not text:
        return "", "{}为空：请先填写{}".format(what, what)
    return text, ""


def _ensure_parent(path):
    """目标文件的父目录不存在时创建（复制/移动/写入都需要）。"""
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)


def _is_dir_target(dst):
    """目标是否为「已存在的目录」——此时按 ``basename(src)`` 拼进去。"""
    try:
        return os.path.isdir(dst)
    except Exception:
        return False


# ── 回收站 ───────────────────────────────────────────────────────────
def _recycle_supported():
    """当前平台是否支持送回收站（仅 Windows）。"""
    return os.name == "nt"


def _shfileop_delete(path):
    """ctypes 调 ``SHFileOperationW`` 把单个路径送回收站 → (ok, msg)。

    不引入 pywin32；结构体与常量按 Win32 SDK 定义手写。任何失败都返回
    ``(False, 原因)``，由调用方决定是否回退永久删除。
    """
    if not _recycle_supported():
        return False, "非 Windows 平台不支持回收站"
    try:
        import ctypes
        from ctypes import wintypes

        class _SHFILEOPSTRUCTW(ctypes.Structure):
            _fields_ = [
                ("hwnd", wintypes.HWND),
                ("wFunc", wintypes.UINT),
                ("pFrom", wintypes.LPCWSTR),
                ("pTo", wintypes.LPCWSTR),
                ("fFlags", ctypes.c_ushort),        # FILEOP_FLAGS = WORD
                ("fAnyOperationsAborted", wintypes.BOOL),
                ("hNameMappings", ctypes.c_void_p),
                ("lpszProgressTitle", wintypes.LPCWSTR),
            ]

        op = _SHFILEOPSTRUCTW()
        op.hwnd = None
        op.wFunc = _FO_DELETE
        # pFrom 需要双 \0 结尾：ctypes 会自动补一个，再加一个即为双结尾
        op.pFrom = path + "\0"
        op.pTo = None
        op.fFlags = (_FOF_ALLOWUNDO | _FOF_NOCONFIRMATION | _FOF_SILENT
                     | _FOF_NOERRORUI | _FOF_NOCONFIRMMKDIR)

        func = ctypes.windll.shell32.SHFileOperationW
        func.argtypes = [ctypes.POINTER(_SHFILEOPSTRUCTW)]
        func.restype = ctypes.c_int
        rc = int(func(ctypes.byref(op)))
        if rc != 0:
            return False, "SHFileOperationW 返回错误码 {}".format(rc)
        if op.fAnyOperationsAborted:
            return False, "回收站操作被中止"
        return True, "已移入回收站"
    except Exception as e:                 # noqa: BLE001 — 纯逻辑模块不抛
        return False, "调用回收站失败: {}".format(e)


def _permanent_delete(path, recursive):
    """永久删除（目录需递归）→ (ok, msg)。"""
    try:
        if os.path.isdir(path) and not os.path.islink(path):
            if not recursive:
                # 目录必须递归：非递归直接拒绝，避免"删空目录"这类隐蔽行为
                try:
                    if os.listdir(path):
                        return False, "目录非空，需开启『递归』才能删除: {}".format(path)
                except OSError as e:
                    return False, "读取目录失败: {}".format(e)
                os.rmdir(path)
                return True, "已删除空目录 {}".format(path)
            shutil.rmtree(path)
            return True, "已永久删除目录 {}".format(path)
        os.remove(path)
        return True, "已永久删除文件 {}".format(path)
    except Exception as e:                 # noqa: BLE001
        return False, "永久删除失败: {}".format(e)


# ── 复制 / 移动 ──────────────────────────────────────────────────────
def _dest_path(src, dst):
    """目标为已存在目录时拼 ``basename(src)``，否则原样返回。"""
    if _is_dir_target(dst):
        return os.path.join(dst, os.path.basename(src))
    return dst


def copy_path(src, dst, overwrite=True, script_dir=""):
    """复制文件或目录 → (ok, msg, 目标绝对路径)。

    ``overwrite=False`` 且目标已存在时失败（不做静默覆盖）。
    """
    src_abs = resolve(src, script_dir)
    dst_raw = resolve(dst, script_dir)
    if not src_abs or not dst_raw:
        return False, "源路径与目标路径都必须填写", ""
    if not os.path.exists(src_abs):
        return False, "源不存在: {}".format(src_abs), ""
    target = _dest_path(src_abs, dst_raw)
    if os.path.exists(target):
        if not overwrite:
            return False, "目标已存在且不允许覆盖: {}".format(target), ""
        if os.path.isdir(target) and not os.path.isdir(src_abs):
            return False, "目标同名目录已存在，无法覆盖: {}".format(target), ""
    try:
        _ensure_parent(target)
        if os.path.isdir(src_abs):
            shutil.copytree(src_abs, target,
                            dirs_exist_ok=bool(overwrite))
        else:
            shutil.copy2(src_abs, target)
        return True, "已复制: {} → {}".format(src_abs, target), target
    except Exception as e:                 # noqa: BLE001
        return False, "复制失败: {}".format(e), ""


def move_path(src, dst, overwrite=True, script_dir=""):
    """移动文件或目录（同盘即重命名）→ (ok, msg, 目标绝对路径)。

    跨盘时 ``shutil.move`` 会自动退化为 copy + remove。
    """
    src_abs = resolve(src, script_dir)
    dst_raw = resolve(dst, script_dir)
    if not src_abs or not dst_raw:
        return False, "源路径与目标路径都必须填写", ""
    if not os.path.exists(src_abs):
        return False, "源不存在: {}".format(src_abs), ""
    target = _dest_path(src_abs, dst_raw)
    if os.path.abspath(target) == os.path.abspath(src_abs):
        return False, "源与目标相同，无需移动: {}".format(src_abs), ""
    if os.path.exists(target):
        if not overwrite:
            return False, "目标已存在且不允许覆盖: {}".format(target), ""
        if os.path.isdir(target) and os.path.isdir(src_abs):
            # 目录合并不做静默覆盖：语义模糊，直接要求用户明确处理
            return False, "目标目录已存在，拒绝合并: {}".format(target), ""
        try:
            if os.path.isdir(target):
                shutil.rmtree(target)
            else:
                os.remove(target)
        except Exception as e:             # noqa: BLE001
            return False, "覆盖目标失败: {}".format(e), ""
    try:
        _ensure_parent(target)
        shutil.move(src_abs, target)
        return True, "已移动: {} → {}".format(src_abs, target), target
    except Exception as e:                 # noqa: BLE001
        return False, "移动失败: {}".format(e), ""


# ── 删除 ─────────────────────────────────────────────────────────────
def delete_path(path, recursive=False, use_recycle=True, script_dir="",
                confirm=None):
    """删除文件/目录 → (ok, msg, 删除的绝对路径)。

    安全语义（对齐方案文档 §8.6）：
      ① **默认送回收站**（可恢复）；回收站失败才回退永久删除，并在消息中说明；
      ② 永久删除（``use_recycle=False``）**必须**经 ``confirm`` 二次确认，
         无确认回调时直接拒绝执行；
      ③ 禁止通配：入参只能是单个具体路径（不做 ``glob`` 展开）。
    """
    abs_path = resolve(path, script_dir)
    if not abs_path:
        return False, "路径为空：请先填写要删除的路径", ""
    if any(ch in os.path.basename(abs_path) for ch in ("*", "?")):
        return False, "删除命令不接受通配符，请给出具体路径: {}".format(abs_path), ""
    if not os.path.exists(abs_path):
        return False, "路径不存在: {}".format(abs_path), ""
    # 拒绝删除盘根 / 用户主目录 / 系统目录本身（防止"删整个盘"这类事故）
    norm = os.path.abspath(abs_path)
    drive, tail = os.path.splitdrive(norm)
    if tail in ("", "\\", "/"):
        return False, "拒绝删除盘根目录: {}".format(norm), ""
    for guard in (os.environ.get("SystemRoot", ""), os.environ.get("ProgramFiles", ""),
                  os.environ.get("USERPROFILE", "")):
        guard_abs = _clean(guard)
        if guard_abs and os.path.abspath(guard_abs) == norm:
            return False, "拒绝删除系统/用户主目录本身: {}".format(norm), ""

    if use_recycle:
        ok, msg = _shfileop_delete(norm)
        if ok:
            return True, msg, norm
        # 回退永久删除：仍需递归开关，且明确写进消息里
        ok2, msg2 = _permanent_delete(norm, recursive)
        if ok2:
            return True, "回收站不可用({})；已回退{}".format(msg, msg2), norm
        return False, "删除失败（回收站: {}；永久删除: {}）".format(msg, msg2), ""

    # 永久删除：二次确认闸门
    if not callable(confirm):
        return (False, "永久删除需二次确认：当前无确认界面，"
                       "请改用『送回收站=是』或显式提供确认", "")
    try:
        approved = bool(confirm("即将永久删除（不可恢复）：\n{}\n递归={}".format(
            norm, "是" if recursive else "否")))
    except Exception as e:                 # noqa: BLE001
        return False, "二次确认失败: {}".format(e), ""
    if not approved:
        return False, "用户取消删除", ""
    ok, msg = _permanent_delete(norm, recursive)
    return ok, msg, (norm if ok else "")


# ── 目录 ─────────────────────────────────────────────────────────────
def make_dir(path, ignore_exists=True, script_dir=""):
    """创建目录（支持多级）→ (ok, msg, 目录绝对路径)。"""
    abs_path = resolve(path, script_dir)
    if not abs_path:
        return False, "路径为空：请先填写要创建的目录", ""
    if os.path.isdir(abs_path):
        if ignore_exists:
            return True, "目录已存在（已忽略）: {}".format(abs_path), abs_path
        return False, "目录已存在: {}".format(abs_path), abs_path
    if os.path.exists(abs_path):
        return False, "同名文件已存在，无法创建目录: {}".format(abs_path), ""
    try:
        os.makedirs(abs_path, exist_ok=True)
        return True, "已创建目录: {}".format(abs_path), abs_path
    except Exception as e:                 # noqa: BLE001
        return False, "创建目录失败: {}".format(e), ""


def _sort_key(entry, by):
    """列出目录的排序键；取不到时退化为 0，保证不会因个别条目抛错。"""
    if by == "时间":
        try:
            return entry.stat().st_mtime
        except Exception:
            return 0
    if by == "大小":
        try:
            return entry.stat().st_size
        except Exception:
            return 0
    return 0                                      # 名：由调用方按 name 排序


def list_dir(path, pattern="", sort_by="名", script_dir="", dirs_first=True):
    """列出目录内容 → (ok, msg, 换行连接的名称串)。"""
    abs_path = resolve(path, script_dir)
    if not abs_path:
        return False, "路径为空：请先填写要列出的目录", ""
    if not os.path.isdir(abs_path):
        return False, "目录不存在: {}".format(abs_path), ""
    by = _clean(sort_by) or "名"
    if by not in _SORT_KEYS:
        by = "名"
    pat = _clean(pattern)
    try:
        entries = []
        for entry in os.scandir(abs_path):
            name = entry.name
            if pat and not fnmatch.fnmatch(name, pat):
                continue
            entries.append(entry)
    except Exception as e:                 # noqa: BLE001
        return False, "列出目录失败: {}".format(e), ""

    if by == "名":
        entries.sort(key=lambda e: e.name.lower())
    else:
        # 时间/大小默认倒序（最新/最大的在前），与资源管理器习惯一致
        entries.sort(key=lambda e: _sort_key(e, by), reverse=True)
    if dirs_first:
        try:
            entries.sort(key=lambda e: 0 if e.is_dir() else 1)
        except Exception:
            pass

    names = []
    for entry in entries:
        try:
            names.append(entry.name + ("\\" if entry.is_dir() else ""))
        except Exception:
            names.append(entry.name)
    text = "\n".join(names)
    return True, "列出 {} 项（{}）".format(len(names), abs_path), text


# ── 文本读写 ─────────────────────────────────────────────────────────
def _decode(data, encoding):
    """按 encoding 解码 → (text, 实际编码)；``auto`` 先 utf-8 再 gbk。"""
    enc = (_clean(encoding) or "auto").lower()
    if enc in ("utf8", "utf-8"):
        return data.decode("utf-8"), "utf-8"
    if enc in ("gbk", "gb18030"):
        try:
            return data.decode(enc), enc
        except Exception:
            return data.decode("gb18030", "replace"), "gb18030"
    # auto
    for cand in ("utf-8", "gbk"):
        try:
            return data.decode(cand), cand
        except Exception:
            continue
    return data.decode("utf-8", "replace"), "utf-8(替换非法字节)"


def read_text(path, encoding="auto", script_dir="", max_bytes=32 * 1024 * 1024):
    """读取文本文件 → (ok, msg, 内容)。默认上限 32 MB，避免超大文件拖垮内存。"""
    abs_path = resolve(path, script_dir)
    if not abs_path:
        return False, "路径为空：请先填写要读取的文件", ""
    if os.path.isdir(abs_path):
        return False, "目标是目录，不是文件: {}".format(abs_path), ""
    try:
        size = os.path.getsize(abs_path)
    except Exception as e:                 # noqa: BLE001
        return False, "读取文件失败: {}".format(e), ""
    if size > max_bytes:
        return False, "文件过大（{} 字节 > 上限 {} 字节）".format(size, max_bytes), ""
    try:
        with open(abs_path, "rb") as fh:
            data = fh.read()
    except Exception as e:                 # noqa: BLE001
        return False, "读取文件失败: {}".format(e), ""
    try:
        text, used = _decode(data, encoding)
    except Exception as e:                 # noqa: BLE001
        return False, "解码失败（编码={}）: {}".format(encoding, e), ""
    return True, "已读取 {} 字节（编码 {}）".format(len(data), used), text


def _atomic_write(abs_path, text, encoding):
    """原子写：同目录临时文件 + ``os.replace``（复用 script_package 已验证的模式）。"""
    directory = os.path.dirname(abs_path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".acrpa_", suffix=".tmp", dir=directory)
    os.close(fd)
    try:
        with open(tmp, "w", encoding=encoding, newline="") as fh:
            fh.write(text)
        os.replace(tmp, abs_path)
    finally:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass


def write_text(path, content, mode="覆盖", encoding="utf-8", script_dir=""):
    """写入文本文件（原子写）→ (ok, msg, 写入的绝对路径)。"""
    abs_path = resolve(path, script_dir)
    if not abs_path:
        return False, "路径为空：请先填写要写入的文件", ""
    if os.path.isdir(abs_path):
        return False, "目标是目录，不是文件: {}".format(abs_path), ""
    text = "" if content is None else str(content)
    enc = (_clean(encoding) or "utf-8").lower()
    if enc in ("auto",):
        enc = "utf-8"
    if enc == "utf8":
        enc = "utf-8"
    append = _clean(mode) == "追加"
    try:
        if append and os.path.exists(abs_path):
            with open(abs_path, "rb") as fh:
                old, used = _decode(fh.read(), enc)
            _atomic_write(abs_path, old + text, used.replace("(替换非法字节)", ""))
        else:
            _atomic_write(abs_path, text, enc)
        return True, "已{}写入 {} 字节: {}".format(
            "追加" if append else "覆盖", len(text.encode(enc, "replace")), abs_path
        ), abs_path
    except Exception as e:                 # noqa: BLE001
        return False, "写入文件失败: {}".format(e), ""


# ── 存在性 ───────────────────────────────────────────────────────────
def path_exists(path, script_dir=""):
    """文件/目录是否存在 → (ok, msg, "1"/"0")。路径为空视为参数错误（不返回 0）。"""
    abs_path = resolve(path, script_dir)
    if not abs_path:
        return False, "路径为空：请先填写要检查的路径", ""
    try:
        found = os.path.exists(abs_path)
    except Exception as e:                 # noqa: BLE001
        return False, "检查路径失败: {}".format(e), ""
    return True, ("存在: " if found else "不存在: ") + abs_path, ("1" if found else "0")
