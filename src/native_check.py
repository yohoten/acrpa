# -*- coding: utf-8 -*-
"""native_check — 原生文件（PE / DLL 依赖 / 路径编码）的**零依赖静态**检查。

为什么需要它
------------
能力探测曾出现"假就绪"：只要 ``PaddleOCR.dll`` / ``dd63330.dll`` 文件在盘上就判
READY，但第一次真正调用必然失败（缺 4 个原生依赖、缺模型目录、路径含中文、DLL 是
32 位而主程序是 64 位…）。本模块把"**静态可判定**的那部分"收敛成一组纯函数，
供 ``capabilities`` 的 probe 使用，让"探测结论 ⇔ 第一次调用不会因缺依赖失败"。

设计约束（务必保持）
--------------------
* **纯 stdlib**：模块级仅 import ``io`` / ``os`` / ``struct``；不 import 任何
  ACRPA 内部模块，不 import 第三方库；
* **import 零副作用**：模块级只有常量与函数定义 —— 不读文件、不建对象、不缓存，
  全部工作发生在函数体内（``tools/_test_import_side_effect_free.py`` 必须仍通过）；
* **绝不 LoadLibrary**：本模块只做 ``open(..., 'rb')`` 读 PE 头 + ``os.path`` 存在性
  检查。真正 ``LoadLibrary`` 会 ①触发杀软启发式（实测可把进程直接打死）②执行
  DllMain 副作用 ③在内核驱动场景下安装系统服务。静态检查已足够回答
  "这个 DLL 在本进程里能不能被加载"；
* **失败开放 (fail-open)**：任何失败都返回 ``None`` 或 ``(False, reason)``，
  **绝不抛异常** —— 探测自身故障不得误伤命令执行。

公开 API
--------
    pe_machine(path)                -> int | None
    is_x64(path)                    -> (bool, str)
    dll_loadable(path_or_dir, deps) -> (bool, str)
    dir_ascii_only(path)            -> (bool, str)

    # 辅助（供 probe 复用，避免各处重复实现同一判断）
    missing_files(dirpath, names)   -> list[str]
    dir_has_files(dirpath, names)   -> bool
    machine_name(machine)           -> str
"""
import io
import os
import struct

#: PE 头 ``IMAGE_FILE_HEADER.Machine`` 取值
PE_MACHINE_X86 = 0x014C      # i386
PE_MACHINE_X64 = 0x8664      # AMD64 / x86-64
PE_MACHINE_ARM = 0x01C0
PE_MACHINE_ARM64 = 0xAA64

_MACHINE_NAMES = {
    PE_MACHINE_X86: "x86 (32 位)",
    PE_MACHINE_X64: "x64 (64 位)",
    PE_MACHINE_ARM: "ARM (32 位)",
    PE_MACHINE_ARM64: "ARM64",
}

#: ``e_lfanew`` 的合理上限（正常 PE 不会超过 1 MB，防止畸形文件让我们 seek 到天边）
_MAX_E_LFANEW = 0x100000


# ══════════════════════════════════════════════════════════════════════
# PE 头
# ══════════════════════════════════════════════════════════════════════

def machine_name(machine):
    """``Machine`` 数值 → 人类可读名称（未知值给十六进制）。"""
    if machine is None:
        return "未知"
    try:
        return _MACHINE_NAMES.get(int(machine),
                                  "未知构架 (Machine=0x{:X})".format(int(machine)))
    except Exception:                     # noqa: BLE001 — 纯展示函数, 不抛
        return "未知"


def pe_machine(path):
    """读取 PE 头里 ``IMAGE_FILE_HEADER.Machine``（uint16）。

    读法：``0x3C`` 处 4 字节小端 = ``e_lfanew``（PE 签名偏移）→ 该处应为
    ``b"PE\\0\\0"`` → ``+4`` 处 2 字节小端即 ``Machine``。

    :returns: Machine int；非 PE / 读失败 / 路径非文件 → ``None``（不抛异常）
    """
    try:
        with io.open(path, "rb") as f:
            head = f.read(0x40)
            if len(head) < 0x40 or head[:2] != b"MZ":
                return None
            e_lfanew = struct.unpack("<I", head[0x3C:0x40])[0]
            if e_lfanew < 0x40 or e_lfanew > _MAX_E_LFANEW:
                return None
            f.seek(e_lfanew)
            if f.read(4) != b"PE\0\0":
                return None
            raw = f.read(2)
            if len(raw) < 2:
                return None
            return struct.unpack("<H", raw)[0]
    except Exception:                     # noqa: BLE001 — 失败开放
        return None


def is_x64(path):
    """该 PE 是否为 64 位 x64 且能被 64 位 ACRPA 加载 → ``(ok, reason)``。

    三分支：x64 → ``(True, …)``；32 位 → ``(False, 含"32 位"的原因)``；
    非 PE → ``(False, "不是有效的 PE")``。
    """
    m = pe_machine(path)
    if m is None:
        return False, "不是有效的 PE 文件 (读不到 Machine): {}".format(path)
    if m == PE_MACHINE_X64:
        return True, "x64 原生模块 (Machine=0x{:X})".format(m)
    if m == PE_MACHINE_X86:
        return (False,
                "32 位 DLL (Machine=0x14C)，64 位 ACRPA 无法加载 "
                "(会报 WinError 193: 不是有效的 Win32 应用程序)")
    return (False,
            "非 x64 原生模块 ({}, Machine=0x{:X})，64 位 ACRPA 无法加载".format(
                machine_name(m), m))


# ══════════════════════════════════════════════════════════════════════
# 依赖清单（静态存在性）
# ══════════════════════════════════════════════════════════════════════

def missing_files(dirpath, names):
    """``names`` 里在 ``dirpath`` 下**不存在**的文件名列表（保序）。

    目录不存在时视为全部缺失。**只查存在性，不打开任何文件。**
    """
    out = []
    try:
        base = str(dirpath or "")
        for name in (names or ()):
            if not base or not os.path.isfile(os.path.join(base, name)):
                out.append(name)
    except Exception:                     # noqa: BLE001 — 失败开放: 视为全缺
        return list(names or ())
    return out


def dir_has_files(dirpath, names):
    """``names`` 是否全部存在于 ``dirpath``（空清单视为 True）。"""
    return not missing_files(dirpath, names)


def dll_loadable(path_or_dir, deps=()):
    """静态判断"这个 DLL 及其同目录依赖是否齐全" → ``(ok, reason)``。

    **绝不真正加载 DLL**（无 ``LoadLibrary`` / ``WinDLL`` / ``CDLL`` 调用）：
    Windows 的加载器找不到依赖时返回的是 ``WinError 126 (找不到指定的模块)``，
    不带任何文件名提示，用户无从下手；这里直接列出缺哪个文件。

    :param path_or_dir: DLL 自身路径，或其所在目录
    :param deps: 同目录下必须存在的依赖文件名序列
    :returns: 缺失 → ``(False, "缺少依赖文件: a.dll, b.dll")``；
              齐全 → ``(True, "依赖齐全 (N 项)")``；异常 → ``(False, "检查异常: …")``
    """
    try:
        p = str(path_or_dir or "")
        if not p:
            return False, "未提供 DLL 路径或目录"
        if os.path.isdir(p):
            dll_dir = os.path.abspath(p)
        elif os.path.isfile(p):
            dll_dir = os.path.dirname(os.path.abspath(p))
        else:
            return False, "路径不存在: {}".format(p)
        lacked = missing_files(dll_dir, deps)
        if lacked:
            return False, "缺少依赖文件: {}".format(", ".join(lacked))
        return True, "依赖齐全 ({} 项){}".format(
            len(list(deps or ())),
            (": " + ", ".join(deps)) if deps else "")
    except Exception as e:                # noqa: BLE001 — 失败开放
        return False, "检查异常: {}".format(e)


# ══════════════════════════════════════════════════════════════════════
# 路径编码（PaddleOCR.dll 内部按 ANSI 找模型文件）
# ══════════════════════════════════════════════════════════════════════

def dir_ascii_only(path):
    """路径是否**纯 ASCII** → ``(ok, reason)``。

    ``PaddleOCR.dll`` 内部按 ANSI 路径查找 ``inference.json`` / ``.pdiparams``，
    含中文/全角字符的目录实测会报 ``can not found model file in ...``
    （见 ``docs/paddle-ocr-dll-使用说明.md``「⚠ 路径必须是纯 ASCII」）。
    """
    try:
        p = str(path or "")
        if not p:
            return False, "路径为空"
        ap = os.path.abspath(p)
        bad = sorted({ch for ch in ap if ord(ch) > 127})
        if bad:
            return (False,
                    "路径含非 ASCII 字符 {} —— PaddleOCR.dll 内部按 ANSI 查找模型文件, "
                    "会报 'can not find model file in ...'; 请改用纯英文/数字路径: {}".format(
                        "".join(bad), ap))
        return True, "路径为纯 ASCII: {}".format(ap)
    except Exception as e:                # noqa: BLE001 — 失败开放
        return False, "检查异常: {}".format(e)
