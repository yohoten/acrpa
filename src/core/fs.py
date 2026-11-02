# -*- coding: utf-8 -*-
"""core.fs — 原子写盘与安全文件名 (纯 Python, 无 tkinter 依赖)。

从 ``src/utils.py`` 迁出 (docs/项目结构优化建议.md §3.1), 供 ``engine`` 等
领域层在无 tkinter 解释器里导入。零第三方依赖 (仅 os / re / shutil)。
"""
import os
import re
import shutil


# ═══════════════════════════════════════════════════════════════════════
# 原子写盘辅助 (P0-6 「脚本保存原子化」)
# ═══════════════════════════════════════════════════════════════════════
# 修法依据: docs/ACRPA-完善路线图.md §P0-6「复用 script_package 的原子写模式」。
# 设计约束:
#   1) 临时文件必须与目标**同目录/同卷** —— 否则 os.replace 可能跨卷失败;
#      这里直接用 `dest + ".tmp"` 而非 tempfile(避免落到 %TEMP% 造成跨卷)。
#   2) 任意时刻磁盘上必须存在一份可用副本: 先 writer 落 tmp, 再 copy2 出 .bak,
#      最后 os.replace 原子替换 —— 替换是内核级 rename, 不存在「半写」状态。
#   3) 失败不吞异常: 清理 tmp 后原样 raise, 由调用方决定是否提示用户。
# 本模块保持零第三方依赖 (仅 os / shutil)。

def atomic_save(dest_path, writer, backup=True):
    """原子地把内容保存到 dest_path。

    writer(tmp_path) 负责把内容完整写入 tmp_path(同目录临时文件)；
    写成功后: 若 dest_path 已存在且 backup=True, 先把原件复制为
    dest_path + ".bak", 再 os.replace(tmp_path, dest_path)。
    任一步失败: 删除临时文件并 raise, 保证 dest_path 从未被破坏
    (或保持旧内容)。返回 None。

    参数:
        dest_path: 目标文件路径, str 或 pathlib.Path (内部 os.fspath 归一)。
        writer:    可调用对象, 接收临时文件路径, 负责写入完整内容。
        backup:    True 时在覆盖前把旧文件复制为 "<dest>.bak"。

    异常:
        原样抛出 writer / shutil.copy2 / os.replace 的异常, 不吞掉。
    """
    dest = os.fspath(dest_path)
    tmp = dest + ".tmp"
    bak = dest + ".bak"

    # 残留临时文件 (上次崩溃遗留) 先清掉, 避免 writer 以追加语义污染结果
    if os.path.exists(tmp):
        try:
            os.remove(tmp)
        except OSError:
            pass

    try:
        writer(tmp)
        if backup and os.path.exists(dest):
            # 先落前像: 此刻 dest 仍是完好旧内容, 恒有可恢复副本
            shutil.copy2(dest, bak)
        os.replace(tmp, dest)
    except BaseException:
        # 尽力清理, 保证不留 .tmp; dest 未被 os.replace 触碰 → 保持旧内容
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        raise
    return None


def atomic_write_bytes(dest_path, data, backup=True):
    """便捷函数: 以二进制把 data 原子写入 dest_path。

    内部委托 atomic_save, 因此同样具备「同目录 tmp + .bak 前像 + os.replace
    原子替换 + 失败清理并抛出」的保证。data 需为 bytes 类对象。
    """
    def _writer(tmp):
        with open(tmp, "wb") as f:
            f.write(data)
    return atomic_save(dest_path, _writer, backup=backup)


# ═══════════════════════════════════════════════════════════════════════
# 安全文件名净化 (安全项 #6「浏览器下载文件名净化」)
# ═══════════════════════════════════════════════════════════════════════
# 背景: 浏览器下载的 download.suggested_filename 来自远端 Content-Disposition,
# 属**不可信输入**。直接 os.path.join(out_dir, name) 后 save_as 落盘, 可被
# `..\..\Startup\x.bat` / 绝对路径 / UNC 路径穿越目录, 覆盖下载目录之外的
# 任意可写文件。
# 修法依据: docs/ACRPA-完善路线图.md §7 #6「os.path.basename + 拒绝 .. / 绝对路径」。
# 设计约束: 纯函数、零第三方依赖、不触碰文件系统、不抛任何异常。

# Windows 保留设备名(不分大小写); "CON.txt" 形式同样被系统视为保留。
_WIN_RESERVED_NAMES = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + ["COM%d" % i for i in range(1, 10)]
    + ["LPT%d" % i for i in range(1, 10)]
)
# Windows 非法字符 + C0 控制字符 + DEL
_ILLEGAL_FN_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]')
# 驱动器前缀: "C:" / "c:" (盘符相对路径 "C:evil.bat" 亦被剥离)
_DRIVE_PREFIX_RE = re.compile(r'^[A-Za-z]:')
# 单段名长度上限(远低于 NTFS 255, 留出 "name (1).ext" 避让空间)
_MAX_FILENAME_LEN = 200


def _sanitize_segment(text):
    """把任意字符串压成**单段**文件名; 无有效内容时返回 ""(不抛异常)。"""
    s = str(text) if text is not None else ""
    # 1) 统一分隔符后取最后一段: 丢弃绝对路径前缀、'..'/'../' 父目录成分。
    #    这样 "..\..\evil.bat" -> "evil.bat", "/etc/passwd" -> "passwd"。
    s = s.replace("\\", "/").split("/")[-1]
    # 2) 丢弃驱动器前缀 (C: / c:)
    s = _DRIVE_PREFIX_RE.sub("", s)
    # 3) 先去首尾空白 —— 纯空白/制表符/换行输入在此即变为空, 走默认名分支
    s = s.strip()
    # 4) 替换非法字符(含 : * ? " < > |)与控制字符, 保持扩展名分隔点不动
    s = _ILLEGAL_FN_RE.sub("_", s)
    # 5) 去掉 Windows 不允许的**尾部点/空格**
    s = s.rstrip(". ")
    # 6) 纯点段('.' / '..')视为无内容
    if s in ("", ".", ".."):
        return ""
    # 7) Windows 保留设备名 -> 加 '_' 前缀改写(含 "CON.txt" 形式)
    if os.path.splitext(s)[0].upper() in _WIN_RESERVED_NAMES:
        s = "_" + s
    # 8) 超长截断(尽量保留扩展名)
    if len(s) > _MAX_FILENAME_LEN:
        base8, ext8 = os.path.splitext(s)
        keep = _MAX_FILENAME_LEN - len(ext8)
        s = (base8[:keep] + ext8) if keep > 0 else s[:_MAX_FILENAME_LEN]
    # 9) 兜底: 结果绝不含路径分隔符
    for sep in (os.sep, os.altsep):
        if sep and sep in s:
            s = s.replace(sep, "_")
    return s


def safe_filename(name, default="download"):
    """把可能来自远端的文件名净化成安全的**单段文件名**(不含任何目录成分)。

    规则:
      - 取 basename(同时把 '\\' 视为分隔符, 兼容 Windows 风格的远端名);
      - 丢弃 '.' 与 '..'、驱动器前缀(如 'C:')、盘符/绝对路径成分;
      - 移除/替换非法字符 [ \\ / : * ? " < > | ] 及控制字符;
      - 去除首尾空白与**尾部点/空格**(Windows 不允许);
      - 命中 Windows 保留设备名(CON/PRN/AUX/NUL/COM1-9/LPT1-9, 不分大小写)
        时加 '_' 前缀改写;
      - 结果为空则回退 default(同样经净化);
      - 绝不返回含 os.sep/os.altsep 的值。

    纯函数: 不触碰文件系统, 不抛异常。扩展名(如 .xls/.csv/.png)尽可能保留。
    """
    clean = _sanitize_segment(name)
    if clean:
        return clean
    fallback = _sanitize_segment(default)
    return fallback or "download"
