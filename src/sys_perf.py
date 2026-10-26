# -*- coding: utf-8 -*-
"""sys_perf.py — 系统性能采样器（托盘悬停 tooltip 用）。

设计约束（方案文档 §5 L4 #2 / §8.3 / §8.4）
------------------------------------------
* **零新增第三方依赖**：只用 ``ctypes`` + 标准库（不用 psutil / pycaw / comtypes）；
* **不阻塞 UI 线程**：单次采集 < 5 ms（实测见 ``tools/_test_ui_l4.py``），
  内部还有最小采样间隔缓存，重复调用直接命中缓存；
* **任何 API 失败只降级不抛**：取不到就返回 ``None`` / ``0.0``，调用方拿到的
  永远是可用结构。

公开 API
--------
``cpu_percent()``    → 0–100 float（首次调用只建立基线，返回 0.0）
``memory()``         → ``{'percent','used_mb','total_mb'}`` 或 ``None``
``disk(drive='')``   → ``{'drive','free_bytes','total_bytes','free_gb','total_gb'}`` 或 ``None``
``network()``        → ``None``（见 ``NETWORK_NOTE``，本层已按方案降级）
``sample(drive='')`` → 合并快照 dict（含 ``ok`` 标记）
``format_tooltip(snap)`` → 单行中文 tooltip 文本
``reset()``          → 清空基线与缓存（测试用）

线程模型
--------
本模块**不创建任何线程**，全部函数为同步纯采集；由调用方（``tray.py``）在
``root.after`` 轮询里按 1–2 秒限流调用。内部只有两把轻量 ``Lock`` 保护基线/缓存，
不涉及 tkinter 对象。
"""
import ctypes
import ctypes.wintypes as wintypes
import os
import threading
import time

__all__ = [
    "cpu_percent", "memory", "disk", "network", "sample",
    "format_tooltip", "reset", "NETWORK_NOTE",
]

# 网络上下行已降级：`GetIfTable2` 的 MIB_IF_ROW2 是含嵌套联合体的巨型结构体
# （单行 > 1.3 KB），纯 ctypes 手写字段布局在 Windows 各版本间对齐风险高；
# 一旦偏移写错会读出垃圾数值污染 tooltip。方案文档 §5 L4 #2 明确允许该项降级，
# 故此版本 tooltip 只含 CPU / 内存 / 磁盘，不含网速。
NETWORK_NOTE = (
    "网络上下行已降级（未实现）：MIB_IF_ROW2 纯 ctypes 布局跨版本风险高，"
    "按方案文档 §5 L4 #2 允许的范围省略，tooltip 只显示 CPU/内存/磁盘。"
)

# 同一快照的最小复用间隔（秒）；CPU 需要至少两次采样的差分，故不能太小
MIN_INTERVAL_SEC = 0.5

# NOTIFYICONDATAW.szTip = 128 wchars —— 留出余量，避免被 Shell 静默截断
MAX_TIP_LEN = 120

_IS_WIN = (os.name == "nt")


# ══════════════════════════════════════════════════════════════════════
# Win32 结构体（全部在本模块内定义，不使用外部依赖）
# ══════════════════════════════════════════════════════════════════════

class _FILETIME(ctypes.Structure):
    """FILETIME —— GetSystemTimes 的入参/出参。"""
    _fields_ = [
        ("dwLowDateTime", wintypes.DWORD),
        ("dwHighDateTime", wintypes.DWORD),
    ]


class _MEMORYSTATUSEX(ctypes.Structure):
    """MEMORYSTATUSEX —— GlobalMemoryStatusEx 的出参。"""
    _fields_ = [
        ("dwLength", wintypes.DWORD),
        ("dwMemoryLoad", wintypes.DWORD),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


class _ULARGE_INTEGER(ctypes.Structure):
    """ULARGE_INTEGER（只用 LowPart/HighPart 两字段，避免联合体对齐问题）。"""
    _fields_ = [
        ("LowPart", wintypes.DWORD),
        ("HighPart", wintypes.DWORD),
    ]


def _ft_int(ft):
    """FILETIME → 64 位整数。"""
    return (int(ft.dwHighDateTime) << 32) | int(ft.dwLowDateTime)


def _ul_int(ul):
    """ULARGE_INTEGER → 64 位整数。"""
    return (int(ul.HighPart) << 32) | int(ul.LowPart)


# ══════════════════════════════════════════════════════════════════════
# CPU（GetSystemTimes 差分）
# ══════════════════════════════════════════════════════════════════════

_cpu_lock = threading.Lock()
# ts=0 表示尚无基线；idle/busy 单位是 100 ns 计数
_cpu_state = {"ts": 0.0, "idle": 0, "busy": 0}


def cpu_percent():
    """→ 全系统 CPU 占用 0–100（float）。

    首次调用（或 ``reset()`` 之后）只建立基线，返回 ``0.0``；
    两次调用之间隔得太近（< 1 ms）时返回上一次的计算结果而不更新基线，
    避免除零/抖动。
    """
    if not _IS_WIN:
        return 0.0
    try:
        idle = _FILETIME()
        kernel = _FILETIME()
        user = _FILETIME()
        if not ctypes.windll.kernel32.GetSystemTimes(
                ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
            return 0.0
        idle_v = _ft_int(idle)
        # kernel 时间**包含** idle 时间，所以 busy = (kernel - idle) + user
        busy_v = (_ft_int(kernel) - idle_v) + _ft_int(user)
    except Exception:                       # noqa: BLE001 —— 失败只降级
        return 0.0

    with _cpu_lock:
        prev = _cpu_state
        if not prev["ts"]:
            prev.update(ts=time.time(), idle=idle_v, busy=busy_v)
            return 0.0
        d_busy = busy_v - prev["busy"]
        d_idle = idle_v - prev["idle"]
        prev.update(ts=time.time(), idle=idle_v, busy=busy_v)
        total = d_busy + d_idle
        if total <= 0:                      # 采样窗口太短 / 计数器回绕
            return 0.0
        pct = 100.0 * float(d_busy) / float(total)
        return round(max(0.0, min(100.0, pct)), 1)


# ══════════════════════════════════════════════════════════════════════
# 内存（GlobalMemoryStatusEx）
# ══════════════════════════════════════════════════════════════════════

def memory():
    """→ ``{'percent','used_mb','total_mb'}``；失败返回 ``None``。"""
    if not _IS_WIN:
        return None
    try:
        st = _MEMORYSTATUSEX()
        st.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
            return None
        total = int(st.ullTotalPhys)
        avail = int(st.ullAvailPhys)
        if total <= 0:
            return None
        used = max(0, total - avail)
        return {
            "percent": round(max(0.0, min(100.0, 100.0 * used / float(total))), 1),
            "used_mb": round(used / 1048576.0, 1),
            "total_mb": round(total / 1048576.0, 1),
        }
    except Exception:                       # noqa: BLE001
        return None


# ══════════════════════════════════════════════════════════════════════
# 磁盘（GetDiskFreeSpaceExW）
# ══════════════════════════════════════════════════════════════════════

def default_drive():
    """→ 默认盘符（形如 ``C:``）；优先 SystemDrive。"""
    try:
        drive = (os.environ.get("SystemDrive") or "").strip().upper()
        if drive:
            return drive
    except Exception:                       # noqa: BLE001
        pass
    try:
        head = os.path.splitdrive(os.path.abspath(os.sep))[0].upper()
        if head:
            return head
    except Exception:                       # noqa: BLE001
        pass
    return "C:"


def _norm_drive(drive):
    """盘符归一化：``C`` / ``C:`` / ``C:\\`` → ``C:``。"""
    text = str(drive or "").strip().rstrip("\\/")
    if len(text) == 1 and text.isalpha():
        return text.upper() + ":"
    return text or default_drive()


def disk(drive=""):
    """→ ``{'drive','free_bytes','total_bytes','free_gb','total_gb'}``；失败返回 ``None``。"""
    if not _IS_WIN:
        return None
    name = _norm_drive(drive)
    try:
        free = _ULARGE_INTEGER()
        total = _ULARGE_INTEGER()
        total_free = _ULARGE_INTEGER()
        ok = ctypes.windll.kernel32.GetDiskFreeSpaceExW(
            ctypes.c_wchar_p(name + "\\"),
            ctypes.byref(free), ctypes.byref(total), ctypes.byref(total_free))
        if not ok:
            return None
        free_b = _ul_int(free)
        total_b = _ul_int(total)
        if total_b <= 0:
            return None
        return {
            "drive": name,
            "free_bytes": free_b,
            "total_bytes": total_b,
            "free_gb": round(free_b / 1073741824.0, 1),
            "total_gb": round(total_b / 1073741824.0, 1),
        }
    except Exception:                       # noqa: BLE001
        return None


# ══════════════════════════════════════════════════════════════════════
# 网络（降级占位 —— 保留 API 形状，便于后续无痛补实现）
# ══════════════════════════════════════════════════════════════════════

def network():
    """→ ``None``（本版本不采集网络上下行，原因见模块常量 ``NETWORK_NOTE``）。"""
    return None


# ══════════════════════════════════════════════════════════════════════
# 合并快照 + 缓存
# ══════════════════════════════════════════════════════════════════════

_cache_lock = threading.Lock()
_cache = {"ts": 0.0, "snap": None}


def sample(drive="", max_age=MIN_INTERVAL_SEC):
    """→ 一次合并快照 dict（带缓存，重复调用在 ``max_age`` 内直接复用）。

    返回字段：``ok`` / ``cpu`` / ``mem_percent`` / ``mem_used_mb`` /
    ``mem_total_mb`` / ``disk_drive`` / ``disk_free_gb`` / ``disk_total_gb`` /
    ``ts``。任何子项失败只令对应字段为 0 / 空串，``ok`` 表示内存读取成功。
    """
    now = time.time()
    with _cache_lock:
        cached = _cache.get("snap")
        if cached is not None:
            try:
                if 0 <= (now - float(_cache.get("ts") or 0.0)) <= float(max_age):
                    return dict(cached)
            except Exception:               # noqa: BLE001
                pass

    mem = memory()
    dsk = disk(drive)
    cpu = cpu_percent()
    snap = {
        "ok": mem is not None,
        "ts": now,
        "cpu": cpu,
        "mem_percent": (mem or {}).get("percent", 0.0),
        "mem_used_mb": (mem or {}).get("used_mb", 0.0),
        "mem_total_mb": (mem or {}).get("total_mb", 0.0),
        "disk_drive": (dsk or {}).get("drive", ""),
        "disk_free_gb": (dsk or {}).get("free_gb", 0.0),
        "disk_total_gb": (dsk or {}).get("total_gb", 0.0),
    }
    with _cache_lock:
        _cache["ts"] = now
        _cache["snap"] = dict(snap)
    return dict(snap)


def format_tooltip(snap, max_len=MAX_TIP_LEN):
    """快照 → 单行中文 tooltip，例：``CPU: 12%  内存: 48%  磁盘 C: 120GB 可用``。

    任何缺失项自动省略；全部缺失时回退 ``A/C RPA``。绝不抛异常。
    """
    try:
        if not isinstance(snap, dict):
            return "A/C RPA"
        parts = []
        if snap.get("mem_total_mb"):
            parts.append("CPU: {:.0f}%".format(float(snap.get("cpu") or 0.0)))
            parts.append("内存: {:.0f}%".format(float(snap.get("mem_percent") or 0.0)))
        elif snap.get("cpu"):
            parts.append("CPU: {:.0f}%".format(float(snap.get("cpu") or 0.0)))
        drive = str(snap.get("disk_drive") or "").rstrip(":")
        if drive and float(snap.get("disk_free_gb") or 0.0) > 0:
            parts.append("磁盘 {}: {:.0f}GB 可用".format(
                drive, float(snap.get("disk_free_gb") or 0.0)))
        text = "  ".join(parts).strip()
        if not text:
            return "A/C RPA"
        limit = int(max_len or MAX_TIP_LEN)
        return text[:limit]
    except Exception:                       # noqa: BLE001
        return "A/C RPA"


def reset():
    """清空 CPU 基线与快照缓存（测试/重连托盘图标时使用）。"""
    with _cpu_lock:
        _cpu_state.update(ts=0.0, idle=0, busy=0)
    with _cache_lock:
        _cache["ts"] = 0.0
        _cache["snap"] = None
