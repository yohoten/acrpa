# -*- coding: utf-8 -*-
"""hotkey_win — 全局组合热键 (``RegisterHotKey`` + ``WM_HOTKEY``) 与热字串引擎。

设计要点 (对应开发计划 S1-04 卡 / DLL 篇 §5.1)
------------------------------------------------
1. **线程模型**: ``RegisterHotKey`` 与 ``GetMessage`` 必须在**同一线程** —— ``WM_HOTKEY``
   只会投递到注册该热键的线程消息队列。因此 :class:`HotkeyManager` 跑一个**专用 daemon
   线程**: 该线程创建隐藏消息窗口 → 安装 wndproc → 跑 ``GetMessageW`` 循环; 外部线程的
   ``register`` / ``unregister`` / ``rebuild`` **一律通过命令队列 + ``PostThreadMessageW``
   交给该线程执行**, 绝不在 Tk 主线程直接调用 ``RegisterHotKey``。
2. **回调只做投递**: wndproc 命中 ``WM_HOTKEY`` 后只调用注册的 callback (约定为
   ``root.after(0, fn)`` 风格), 下游代码零改动。
3. **失败必须可见**: ``RegisterHotKey`` 返回 0 时取 ``GetLastError()``;
   ``ERROR_HOTKEY_ALREADY_REGISTERED == 1409`` (已被其他程序占用) 单独给出可读文案,
   由调用方落回轮询并在设置界面提示。
4. **零依赖**: 全部 ``ctypes.WinDLL`` + 标准库, 不 import tkinter/engine/pyautogui
   (``import hotkey_win`` 零副作用, 不建窗口不起线程)。

公开 API
--------
``parse_spec(text) -> (mods, vk) | None``
    与 ``ACRPA._parse_hotkey_spec`` **语义完全等价** (mods 是 VK 码集合)。
``to_register_mods(mods) -> int``
    VK 码集合 → ``MOD_*`` 位标志。
``HotkeyManager`` / ``HotkeyManager.instance()``
    ``start()`` / ``register(action, spec, callback=None)`` / ``unregister(action)`` /
    ``rebuild(bindings, callbacks=None) -> {action: err}`` / ``registered_actions()`` /
    ``errors()`` / ``stop()`` / ``close()`` / ``post_hotkey(action)``。
``HotstringEngine`` / ``hotstring_engine()``
    ``start(bindings)`` / ``stop()`` / ``feed(vk, is_down, injected=False)`` /
    ``key_event_to_char(vk, shift=False)`` / ``build_inputs(backspaces, text)``。

已知限制 (UI 必须明示)
----------------------
* 热字串用 ``WH_KEYBOARD_LL`` 低层钩子, 对**以管理员身份运行的目标程序无效** (UIPI 隔离)。
* IME 组合态下不触发 (只支持纯 ASCII 触发词)。
"""

import collections
import ctypes
import os
import queue
import sys
import threading
import time

_IS_WIN = (os.name == "nt")

# ── Windows 常量 (全部系统 user32 明确定义, 不依赖第三方) ──
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
WM_USER = 0x0400
WM_HOTKEY_CMD = WM_USER + 0x51   # 内部: 唤醒消息线程处理命令队列

PM_NOREMOVE = 0x0000
PM_REMOVE = 0x0001
HWND_MESSAGE = -3
GWLP_WNDPROC = -4

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

ERROR_HOTKEY_ALREADY_REGISTERED = 1409

# 名称 → MOD_* 位标志 (卡要求 MOD_MAP 含 MOD_NOREPEAT)
MOD_MAP = {
    "ctrl": MOD_CONTROL, "control": MOD_CONTROL,
    "alt": MOD_ALT,
    "shift": MOD_SHIFT,
    "win": MOD_WIN,
    "norepeat": MOD_NOREPEAT,
}

# 名称 → 虚拟键码 (与 ACRPA._parse_hotkey_spec 的 mod_map 逐项相同)
MOD_VK = {
    "ctrl": 0x11, "control": 0x11,
    "alt": 0x12,
    "shift": 0x10,
    "win": 0x5B,
}

# 虚拟键码 → MOD_* 位标志 (parse_spec 返回 VK 集合, 注册时再转)
VK_TO_MOD_FLAG = {0x11: MOD_CONTROL, 0x12: MOD_ALT, 0x10: MOD_SHIFT, 0x5B: MOD_WIN}

# 具名键 → 虚拟键码 (与 ACRPA._parse_hotkey_spec 的 named 表逐项相同)
NAMED_KEYS = {
    "space": 0x20, "enter": 0x0D, "esc": 0x1B, "tab": 0x09,
    "backspace": 0x08, "delete": 0x2E, "home": 0x24, "end": 0x23,
    "pageup": 0x21, "pagedown": 0x22, "insert": 0x2D,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73, "f5": 0x74,
    "f6": 0x75, "f7": 0x76, "f8": 0x77, "f9": 0x78, "f10": 0x79,
    "f11": 0x7A, "f12": 0x7B,
}

MSG_ERR_NON_WIN = "非 Windows 平台，热键注册不可用（已退回轮询）"


# ══════════════════════════════════════════════════════════════════════
# 键位解析 (与 ACRPA._parse_hotkey_spec 语义等价, 单测逐例证明)
# ══════════════════════════════════════════════════════════════════════
def parse_spec(text):
    """解析快捷键字符串 ``'ctrl+shift+q'`` → ``(mods:set, vk:int)``; 无效返回 ``None``。

    与 ``ACRPA._parse_hotkey_spec`` 逐分支相同 (含 ``"未设置"`` 视为空、无修饰键即无效、
    多个非修饰键以最后一个为准、非法段整体判无效)。mods 是**虚拟键码集合**, 不是 MOD_*
    位标志 —— 这样它同时兼容既有 ``GetAsyncKeyState`` 轮询路径。
    """
    if not text or text == "未设置":
        return None
    parts = [p.strip().lower() for p in str(text).split("+")]
    mods = set()
    key = None
    for p in parts:
        if p in MOD_VK:
            mods.add(MOD_VK[p])
        elif p in NAMED_KEYS:
            key = NAMED_KEYS[p]
        elif len(p) == 1 and p.isalnum():
            key = ord(p.upper())
        elif p:
            key = None
            break
    if not mods or key is None:
        return None
    return (mods, key)


def to_register_mods(mods):
    """虚拟键码集合 → ``RegisterHotKey`` 的 ``MOD_*`` 位标志 (未知码忽略)。"""
    flags = 0
    for m in (mods or ()):
        flags |= VK_TO_MOD_FLAG.get(m, 0)
    return flags


def error_text(err):
    """``GetLastError()`` → 可读中文 (1409 = 已被其他程序占用)。"""
    err = int(err or 0)
    if err == ERROR_HOTKEY_ALREADY_REGISTERED:
        return "已被其他程序占用 (ERROR_HOTKEY_ALREADY_REGISTERED=1409)，已退回轮询模式"
    if not err:
        return "注册失败 (无错误码)，已退回轮询模式"
    return "注册失败 (WinError={})，已退回轮询模式".format(err)


# ══════════════════════════════════════════════════════════════════════
# Win32 绑定 (非 Windows 时全部为 None, 各 API 走"失败开放"分支)
# ══════════════════════════════════════════════════════════════════════
class _MSG(ctypes.Structure):
    _fields_ = [("hwnd", ctypes.c_void_p), ("message", ctypes.c_uint),
                ("wParam", ctypes.c_size_t), ("lParam", ctypes.c_ssize_t),
                ("time", ctypes.c_uint32), ("pt", ctypes.c_long * 2)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort),
                ("dwFlags", ctypes.c_uint32), ("time", ctypes.c_uint32),
                ("dwExtraInfo", ctypes.c_size_t)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long),
                ("mouseData", ctypes.c_uint32), ("dwFlags", ctypes.c_uint32),
                ("time", ctypes.c_uint32), ("dwExtraInfo", ctypes.c_size_t)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", ctypes.c_uint32), ("wParamL", ctypes.c_ushort),
                ("wParamH", ctypes.c_ushort)]


class _INPUTUNION(ctypes.Union):
    # 三个成员必须齐全: SendInput 会校验 cbSize == sizeof(INPUT),
    # 只声明 ki 会让结构体变小 → 调用恒失败 (ERROR_INVALID_PARAMETER)。
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.c_uint32), ("u", _INPUTUNION)]


INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
VK_BACK = 0x08
LLKHF_INJECTED = 0x10

if _IS_WIN:
    try:
        _user32 = ctypes.WinDLL("user32", use_last_error=True)
        _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        _WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_void_p,
                                      ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t)
        _user32.CreateWindowExW.argtypes = [ctypes.c_uint32, ctypes.c_wchar_p,
                                            ctypes.c_wchar_p, ctypes.c_uint32,
                                            ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                            ctypes.c_int, ctypes.c_void_p,
                                            ctypes.c_void_p, ctypes.c_void_p,
                                            ctypes.c_void_p]
        _user32.CreateWindowExW.restype = ctypes.c_void_p
        _user32.DestroyWindow.argtypes = [ctypes.c_void_p]
        _user32.DestroyWindow.restype = ctypes.c_int
        _user32.DefWindowProcW.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                           ctypes.c_size_t, ctypes.c_ssize_t]
        _user32.DefWindowProcW.restype = ctypes.c_ssize_t
        _user32.GetMessageW.argtypes = [ctypes.POINTER(_MSG), ctypes.c_void_p,
                                        ctypes.c_uint, ctypes.c_uint]
        _user32.GetMessageW.restype = ctypes.c_int
        _user32.PeekMessageW.argtypes = [ctypes.POINTER(_MSG), ctypes.c_void_p,
                                         ctypes.c_uint, ctypes.c_uint, ctypes.c_uint]
        _user32.PeekMessageW.restype = ctypes.c_int
        _user32.TranslateMessage.argtypes = [ctypes.POINTER(_MSG)]
        _user32.DispatchMessageW.argtypes = [ctypes.POINTER(_MSG)]
        _user32.DispatchMessageW.restype = ctypes.c_ssize_t
        _user32.RegisterHotKey.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                           ctypes.c_uint32, ctypes.c_uint32]
        _user32.RegisterHotKey.restype = ctypes.c_int
        _user32.UnregisterHotKey.argtypes = [ctypes.c_void_p, ctypes.c_int]
        _user32.UnregisterHotKey.restype = ctypes.c_int
        _user32.PostThreadMessageW.argtypes = [ctypes.c_uint32, ctypes.c_uint,
                                               ctypes.c_size_t, ctypes.c_ssize_t]
        _user32.PostThreadMessageW.restype = ctypes.c_int
        _user32.SendInput.argtypes = [ctypes.c_uint, ctypes.POINTER(INPUT), ctypes.c_int]
        _user32.SendInput.restype = ctypes.c_uint
        _kernel32.GetCurrentThreadId.restype = ctypes.c_uint32
        # 32 位 Python 无 SetWindowLongPtrW → 回退 SetWindowLongW
        _set_long = getattr(_user32, "SetWindowLongPtrW", None) or _user32.SetWindowLongW
        _set_long.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
        _set_long.restype = ctypes.c_void_p
    except Exception:                                    # pragma: no cover - 平台兜底
        _user32 = None
        _kernel32 = None
        _WNDPROC = None
        _set_long = None
else:                                                    # pragma: no cover
    _user32 = None
    _kernel32 = None
    _WNDPROC = None
    _set_long = None


def is_supported():
    """当前平台是否支持 ``RegisterHotKey`` 后端 (非 Windows 恒 False)。"""
    return bool(_IS_WIN and _user32 is not None)


# ══════════════════════════════════════════════════════════════════════
# HotkeyManager: 专用消息线程 + 隐藏消息窗口 + 注册表
# ══════════════════════════════════════════════════════════════════════
class HotkeyManager(object):
    """``RegisterHotKey`` 后端 (单例, 也可独立构造 —— 单测用它构造 1409 场景)。

    线程职责表::

        调用方线程 (Tk 主线程 / 任意线程)
            register/unregister/rebuild/stop → 投递命令 + 等回执 (不碰 user32 注册)
        "acrpa-hotkey" daemon 线程
            CreateWindowExW("STATIC", HWND_MESSAGE) → SetWindowLongPtrW(wndproc)
            GetMessageW 循环 → WM_HOTKEY → 查 id → 回调 (只做 root.after 投递)
            WM_HOTKEY_CMD → 执行命令队列 (真正的 RegisterHotKey/UnregisterHotKey)
    """

    _instance = None
    _instance_lock = threading.Lock()

    CMD_TIMEOUT = 3.0

    def __init__(self, dispatcher=None):
        self._lock = threading.RLock()
        self._dispatcher = dispatcher      # callable(action); 未注册 callback 时的兜底
        self._thread = None
        self._tid = 0
        self._hwnd = None
        self._wndproc_ref = None
        self._callbacks = {}               # action -> callable()
        self._registered = {}              # action -> (mods_flag, vk, hotkey_id)
        self._idmap = {}                   # hotkey_id -> action
        self._errors = {}                  # action -> err 文本 (最近一次 rebuild)
        self._next_id = 0xB000
        self._jobs = collections.deque()   # [(fn, Event, box)]
        self._ready = threading.Event()
        self._alive = False
        self._stopping = False
        self._thread_name = "acrpa-hotkey"

    # ── 单例 ──
    @classmethod
    def instance(cls):
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    @classmethod
    def existing_instance(cls):
        """返回已存在的单例 (不构造) —— 供 UI 只读查询, 避免顺手起线程。"""
        return cls._instance

    @classmethod
    def reset_instance(cls):
        """丢掉单例引用 (测试用; 不 stop 旧实例)。"""
        with cls._instance_lock:
            inst = cls._instance
            cls._instance = None
            return inst

    # ── 生命周期 ──
    def start(self, timeout=3.0):
        """启动消息线程 (幂等)。返回是否可用。"""
        if not is_supported():
            return False
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return True
            self._stopping = False
            self._ready.clear()
            t = threading.Thread(target=self._thread_main, name=self._thread_name,
                                 daemon=True)
            self._thread = t
        t.start()
        return bool(self._ready.wait(timeout)) and self._alive

    @property
    def is_running(self):
        with self._lock:
            return bool(self._alive and self._thread is not None
                        and self._thread.is_alive())

    @property
    def thread_id(self):
        return self._tid

    def stop(self, timeout=3.0):
        """干净停止: WM_QUIT → join → 清空注册表。可重复调用, 不抛异常。"""
        with self._lock:
            self._stopping = True
            t = self._thread
            tid = self._tid
        if is_supported() and tid:
            try:
                _user32.PostThreadMessageW(tid, WM_QUIT, 0, 0)
            except Exception:
                pass
        if t is not None and t.is_alive() and t is not threading.current_thread():
            try:
                t.join(timeout)
            except Exception:
                pass
        with self._lock:
            self._jobs.clear()
            self._registered.clear()
            self._idmap.clear()
            self._thread = None
            self._tid = 0
            self._hwnd = None
            self._alive = False
        return not (t is not None and t.is_alive())

    # close() 是 stop() 的别名 (卡要求 "stop()/close() 后线程已结束、注册项已清空")
    close = stop

    # ── 消息线程 ──
    def _thread_main(self):
        msg = _MSG()
        try:
            self._tid = int(_kernel32.GetCurrentThreadId())
            # 强制创建本线程的消息队列 (否则 PostThreadMessageW 返回 ERROR_INVALID_THREAD_ID)
            _user32.PeekMessageW(ctypes.byref(msg), None, WM_USER, WM_USER, PM_NOREMOVE)
            # 用系统预注册类 "STATIC" —— 避免 RegisterClassW 在 onefile 冻结环境下的
            # HINSTANCE 问题; HWND_MESSAGE 父窗口 = 消息专用隐形窗口。
            self._hwnd = _user32.CreateWindowExW(
                0, "STATIC", "ACRPAHotkeySink", 0, 0, 0, 0, 0,
                ctypes.c_void_p(HWND_MESSAGE), None, None, None)
            if self._hwnd:
                try:
                    self._wndproc_ref = _WNDPROC(self._wndproc)
                    _set_long(self._hwnd, GWLP_WNDPROC,
                              ctypes.cast(self._wndproc_ref, ctypes.c_void_p))
                except Exception:
                    self._wndproc_ref = None
            self._alive = True
        except Exception:
            self._alive = False
        finally:
            self._ready.set()
        if not self._alive:
            return
        try:
            while True:
                ret = _user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if ret == 0 or ret == -1:      # WM_QUIT / 错误
                    break
                if msg.message == WM_QUIT:
                    break
                if msg.message == WM_HOTKEY:
                    # RegisterHotKey(hwnd=...) 的 WM_HOTKEY 也走这里 (不 Dispatch, 免重复)
                    self._dispatch_id(int(msg.wParam or 0))
                    continue
                if msg.message == WM_HOTKEY_CMD:
                    self._drain_jobs()
                    continue
                _user32.TranslateMessage(ctypes.byref(msg))
                _user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            self._shutdown()

    def _shutdown(self):
        """线程退出前的清理 (仅本线程执行)。"""
        with self._lock:
            items = list(self._registered.values())
            self._registered.clear()
            self._idmap.clear()
            hwnd = self._hwnd
            self._hwnd = None
            self._alive = False
        for _mods, _vk, hid in items:
            try:
                _user32.UnregisterHotKey(hwnd, hid)
            except Exception:
                pass
        if hwnd:
            try:
                _user32.DestroyWindow(hwnd)
            except Exception:
                pass

    def _wndproc(self, hwnd, msg, wparam, lparam):
        """隐藏窗口窗口过程: 只处理 WM_HOTKEY, 其余交系统默认处理。"""
        try:
            if msg == WM_HOTKEY:
                self._dispatch_id(int(wparam or 0))
                return 0
        except Exception:
            pass
        return _user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _dispatch_id(self, hotkey_id):
        """id → action → 回调 (在本线程; 回调约定只做 ``root.after(0, ...)`` 投递)。"""
        with self._lock:
            action = self._idmap.get(hotkey_id)
            cb = self._callbacks.get(action) if action else None
            disp = self._dispatcher
        if not action:
            return
        try:
            if cb is not None:
                cb()
            elif disp is not None:
                disp(action)
        except Exception:
            pass

    def _drain_jobs(self):
        while True:
            try:
                fn, ev, box = self._jobs.popleft()
            except IndexError:
                return
            try:
                box["val"] = fn()
            except Exception as e:                        # noqa: BLE001
                box["err"] = e
            finally:
                ev.set()

    def _submit(self, fn, timeout=None):
        """把 fn 交给消息线程执行并等待 → ``(ok, value_or_err)``。"""
        if not is_supported():
            return False, MSG_ERR_NON_WIN
        if not self.start():
            return False, "热键消息线程启动失败"
        ev = threading.Event()
        box = {}
        job = (fn, ev, box)
        self._jobs.append(job)
        tid = self._tid
        if not tid or not _user32.PostThreadMessageW(tid, WM_HOTKEY_CMD, 0, 0):
            try:
                self._jobs.remove(job)
            except ValueError:
                pass
            err = ctypes.get_last_error()
            return False, "命令投递失败 (WinError={})".format(err)
        if not ev.wait(timeout or self.CMD_TIMEOUT):
            return False, "命令执行超时"
        if "err" in box:
            return False, str(box["err"])
        return True, box.get("val")

    # ── 注册 / 注销 ──
    def set_dispatcher(self, dispatcher):
        """设置兜底派发函数 ``dispatcher(action)`` (约定只做 root.after 投递)。"""
        with self._lock:
            self._dispatcher = dispatcher

    def register(self, action, spec, callback=None):
        """注册一个组合热键 → ``(ok, err_text)``。失败不抛, 返回可读原因。"""
        parsed = spec if isinstance(spec, tuple) else parse_spec(spec)
        if not parsed:
            return False, "无法解析的快捷键: {!r}".format(spec)
        mods, vk = parsed
        flags = to_register_mods(mods) | MOD_NOREPEAT

        def _do():
            # 同一 action 重复注册: 先注销旧的, 否则会和"自己"撞 1409
            with self._lock:
                old = self._registered.pop(action, None)
                if old:
                    self._idmap.pop(old[2], None)
                new_id = self._next_id
                self._next_id += 1
            if old:
                try:
                    _user32.UnregisterHotKey(self._hwnd, old[2])
                except Exception:
                    pass
            ok = _user32.RegisterHotKey(self._hwnd, new_id, flags, vk)
            if not ok:
                err = ctypes.get_last_error()
                with self._lock:
                    self._callbacks.pop(action, None)
                return (False, error_text(err))
            with self._lock:
                self._registered[action] = (flags, vk, new_id)
                self._idmap[new_id] = action
                if callback is not None:
                    self._callbacks[action] = callback
                self._errors.pop(action, None)
            return (True, "")

        ok, val = self._submit(_do)
        if not ok:
            return False, str(val)
        return val if isinstance(val, tuple) else (True, "")

    def unregister(self, action):
        """注销单个 action → bool (不存在也返回 True, 幂等)。"""
        def _do():
            with self._lock:
                old = self._registered.pop(action, None)
                self._callbacks.pop(action, None)
                if old:
                    self._idmap.pop(old[2], None)
            if old:
                _user32.UnregisterHotKey(self._hwnd, old[2])
            return True

        ok, val = self._submit(_do)
        return bool(ok and val)

    def unregister_all(self):
        """注销全部 (幂等; 线程未起时直接清表)。"""
        def _do():
            with self._lock:
                items = list(self._registered.values())
                self._registered.clear()
                self._idmap.clear()
                self._callbacks.clear()
            for _mods, _vk, hid in items:
                try:
                    _user32.UnregisterHotKey(self._hwnd, hid)
                except Exception:
                    pass
            return True

        ok, _val = self._submit(_do)
        if not ok:
            with self._lock:
                self._registered.clear()
                self._idmap.clear()
        return ok

    def rebuild(self, bindings, callbacks=None):
        """全量重建注册表 → ``{action: err}`` (**只含失败项**; 空 dict = 全部成功)。

        ``bindings`` = ``{action: spec_text}``; 解析失败的 spec 不注册 (由轮询兜住)。
        成功项可用 :meth:`registered_actions` 取到 —— 调用方据此让轮询跳过它们。
        """
        callbacks = callbacks or {}
        results = {}
        if not is_supported():
            for action in (bindings or {}):
                results[action] = MSG_ERR_NON_WIN
            self._errors = dict(results)
            return results
        self.unregister_all()
        for action, spec in (bindings or {}).items():
            ok, err = self.register(action, spec, callbacks.get(action))
            if not ok:
                results[action] = err
        with self._lock:
            self._errors = dict(results)
        return results

    # ── 查询 ──
    def registered_actions(self):
        with self._lock:
            return set(self._registered)

    def clear_local(self):
        """只清本进程内的注册表状态 (不触碰消息线程)。poll 回退用, 不启动任何线程。"""
        with self._lock:
            self._registered.clear()
            self._idmap.clear()
            self._errors = {}

    def spec_of(self, action):
        with self._lock:
            item = self._registered.get(action)
        if not item:
            return None
        return (item[0], item[1])

    def errors(self):
        with self._lock:
            return dict(self._errors)

    def post_hotkey(self, action, timeout=2.0):
        """测试钩子: 以 ``PostThreadMessageW(WM_HOTKEY)`` 注入一次命中 (不依赖真实按键)。"""
        if not is_supported():
            return False
        with self._lock:
            item = self._registered.get(action)
            tid = self._tid
        if not item or not tid:
            return False
        return bool(_user32.PostThreadMessageW(tid, WM_HOTKEY, item[2], 0))

    def send_hotkey(self, action):
        """测试钩子: 直接 ``SendMessageW`` 打到隐藏窗口 (证明 wndproc 路径可用)。"""
        if not is_supported():
            return False
        with self._lock:
            item = self._registered.get(action)
            hwnd = self._hwnd
        if not item or not hwnd:
            return False
        try:
            _user32.SendMessageW(hwnd, WM_HOTKEY, item[2], 0)
            return True
        except Exception:
            return False


# ══════════════════════════════════════════════════════════════════════
# 热字串: 按键 → 字符 (纯函数) / 缓冲命中 (纯逻辑) / 展开注入
# ══════════════════════════════════════════════════════════════════════
_UNSHIFTED_OEM = {
    0xBA: ";", 0xBB: "=", 0xBC: ",", 0xBD: "-", 0xBE: ".", 0xBF: "/",
    0xC0: "`", 0xDB: "[", 0xDC: "\\", 0xDD: "]", 0xDE: "'",
}
_SHIFTED_OEM = {
    0xBA: ":", 0xBB: "+", 0xBC: "<", 0xBD: "_", 0xBE: ">", 0xBF: "?",
    0xC0: "~", 0xDB: "{", 0xDC: "|", 0xDD: "}", 0xDE: '"',
}
_SHIFTED_DIGITS = ")!@#$%^&*("

TRIGGER_TERMINATORS = (" ", "\n", "\t")


def key_event_to_char(vk, shift=False):
    """(虚拟键码, shift) → 单字符; 非可见键/不可映射返回 ``None``。

    **纯函数** (无 IO/无状态), 供 ``HotstringEngine.feed`` 与单测共用。
    """
    try:
        vk = int(vk)
    except Exception:
        return None
    if 0x41 <= vk <= 0x5A:                       # A-Z
        ch = chr(vk)
        return ch if shift else ch.lower()
    if 0x30 <= vk <= 0x39:                       # 0-9
        return _SHIFTED_DIGITS[vk - 0x30] if shift else chr(vk)
    if 0x60 <= vk <= 0x69:                       # 小键盘 0-9
        return chr(0x30 + (vk - 0x60))
    if vk == 0x20:
        return " "
    if vk == 0x0D:
        return "\n"
    if vk == 0x09:
        return "\t"
    if vk in _UNSHIFTED_OEM:
        return _SHIFTED_OEM[vk] if shift else _UNSHIFTED_OEM[vk]
    return None


def build_inputs(backspaces, text, base_vk=VK_BACK):
    """构造 ``SendInput`` 事件序列 (纯函数): 先 N 个回删, 再逐 UTF-16 码元注入文本。

    返回 ``[("vk", vk), ("unicode", code_unit), ...]``; 只描述, 不调用 SendInput,
    因此可在无键鼠环境下断言 "回删字符数正确"。
    """
    events = []
    for _i in range(max(0, int(backspaces or 0))):
        events.append(("vk", base_vk))
    for ch in (text or ""):
        try:
            data = ch.encode("utf-16-le")
        except Exception:
            continue
        for i in range(0, len(data), 2):
            events.append(("unicode", data[i] | (data[i + 1] << 8)))
    return events


def _send_events(events, hold=0.0):
    """把 :func:`build_inputs` 的序列交给 ``SendInput`` (每事件 keydown+keyup)。"""
    if not is_supported() or not events:
        return 0
    arr = (INPUT * (len(events) * 2))()
    n = 0
    for kind, code in events:
        for up in (False, True):
            inp = arr[n]
            inp.type = INPUT_KEYBOARD
            if kind == "unicode":
                inp.u.ki.wVk = 0
                inp.u.ki.wScan = int(code) & 0xFFFF
                flags = KEYEVENTF_UNICODE
            else:
                inp.u.ki.wVk = int(code) & 0xFF
                inp.u.ki.wScan = 0
                flags = 0
            if up:
                flags |= KEYEVENTF_KEYUP
            inp.u.ki.dwFlags = flags
            n += 1
    sent = int(_user32.SendInput(n, arr, ctypes.sizeof(INPUT)) or 0)
    if hold:
        time.sleep(hold)
    return sent


class HotstringEngine(object):
    """ASCII 热字串: 缓冲最近 N 字符 → 命中触发词 → 回删 + 注入展开文本。

    * **默认关闭**: ``bindings`` 为空 → :meth:`start` 直接返回 False, **不安装任何钩子**。
    * 触发词只支持纯 ASCII, 长度 2~20, 以空格/回车/Tab 结尾触发。
    * 钩子回调 (:meth:`_on_key`) **只做数组缓冲与入队, 零 IO/零日志** (超时会被系统摘钩);
      真正的 ``SendInput`` 在独立 worker 线程执行。
    * 复用 ``recorder`` 的既有 ``WH_KEYBOARD_LL`` 钩子与消息泵范式
      (``recorder.acquire_kbd_hook/add_kbd_consumer/run_hook_pump``), **不另起第二套钩子**。
    """

    BUF_MAX = 32
    TRIGGER_MIN = 2
    TRIGGER_MAX = 20

    def __init__(self, bindings=None, script_runner=None):
        self._lock = threading.RLock()
        self._bindings = []            # [{trigger, expand, kind, script_path}]
        self._buf = []
        self._shift = False
        self._ctrl = False
        self._alt = False
        self._win = False
        self._running = False
        self._hook_installed = False
        self._thread = None
        self._worker = None
        self._queue = queue.Queue()
        self._script_runner = script_runner
        self._expansions = 0
        self.load_bindings(bindings or [])

    # ── 配置 ──
    def load_bindings(self, raw):
        """规范化 ``state.HOTSTRINGS`` 列表 → ``[{trigger, expand, kind, script_path}]``。

        非法项 (非 ASCII / 长度越界 / 空展开) 一律丢弃 —— 不抛异常。
        """
        out = []
        for item in (raw or []):
            if not isinstance(item, dict):
                continue
            trigger = str(item.get("trigger") or "")
            expand = item.get("expand")
            expand = "" if expand is None else str(expand)
            kind = str(item.get("kind") or "text")
            script_path = str(item.get("script_path") or "")
            if not self._valid_trigger(trigger):
                continue
            if kind == "text" and not expand:
                continue
            if kind == "script" and not script_path:
                continue
            if kind not in ("text", "script"):
                continue
            out.append({"trigger": trigger, "expand": expand,
                        "kind": kind, "script_path": script_path})
        with self._lock:
            self._bindings = out
        return list(out)

    @classmethod
    def _valid_trigger(cls, trigger):
        if not trigger or not (cls.TRIGGER_MIN <= len(trigger) <= cls.TRIGGER_MAX):
            return False
        try:
            trigger.encode("ascii")
        except Exception:
            return False
        return True

    def bindings(self):
        with self._lock:
            return list(self._bindings)

    @property
    def is_running(self):
        return bool(self._running)

    @property
    def expansions(self):
        return self._expansions

    def set_script_runner(self, fn):
        self._script_runner = fn

    # ── 纯逻辑: 缓冲与命中 ──
    def reset(self):
        with self._lock:
            self._buf = []

    def feed(self, vk, is_down, injected=False):
        """吃一个键盘事件 → 命中则返回 ``{trigger, expand, kind, script_path, backspaces}``。

        **纯逻辑** (无 IO/无日志), 可在无键鼠环境下单测。``injected=True`` (注入事件)
        一律忽略, 避免自己 ``SendInput`` 出来的字符又触发一次。
        """
        if injected:
            return None
        if vk in (0x10, 0xA0, 0xA1):
            self._shift = bool(is_down)
            return None
        if vk in (0xA2, 0xA3):
            self._ctrl = bool(is_down)
            self._buf = []
            return None
        if vk in (0xA4, 0xA5):
            self._alt = bool(is_down)
            self._buf = []
            return None
        if vk in (0x5B, 0x5C):
            self._win = bool(is_down)
            self._buf = []
            return None
        if not is_down:
            return None
        if self._ctrl or self._alt or self._win:
            self._buf = []
            return None
        if vk == 0x08:                                # Backspace: 退一格缓冲
            if self._buf:
                self._buf.pop()
            return None
        ch = key_event_to_char(vk, self._shift)
        if ch is None:
            self._buf = []
            return None
        self._buf.append(ch)
        if len(self._buf) > self.BUF_MAX:
            del self._buf[:-self.BUF_MAX]
        if ch not in TRIGGER_TERMINATORS:
            return None
        typed = "".join(self._buf[:-1])                # 去掉刚输入的终止符
        for b in list(self._bindings):
            trig = b["trigger"]
            if len(typed) >= len(trig) and typed.endswith(trig):
                self._buf = []
                hit = dict(b)
                hit["backspaces"] = len(trig)
                return hit
        return None

    # ── 钩子生命周期 (复用 recorder 的单套 WH_KEYBOARD_LL) ──
    def start(self, bindings=None):
        """启动。``bindings`` 为空 (= 默认关闭) → 不安装钩子, 返回 False。"""
        if bindings is not None:
            self.load_bindings(bindings)
        if not self.bindings():
            return False
        if not _IS_WIN:
            return False
        with self._lock:
            if self._running:
                return True
            self._running = True
            self._thread = threading.Thread(target=self._run, name="acrpa-hotstring",
                                            daemon=True)
            self._worker = threading.Thread(target=self._expand_worker,
                                            name="acrpa-hotstring-expand", daemon=True)
        self._thread.start()
        self._worker.start()
        return True

    def _run(self):
        """钩子宿主线程: 装钩子 → 注册消费者 → 跑消息泵 (与 recorder 同款范式)。"""
        recorder = None
        try:
            import recorder as recorder  # 延迟 import: 保持 import hotkey_win 零副作用
        except Exception:
            recorder = None
        try:
            if recorder is None or not recorder.acquire_kbd_hook():
                self._running = False
                return
            self._hook_installed = True
            recorder.add_kbd_consumer(self._on_key)
            recorder.run_hook_pump(lambda: not self._running)
        finally:
            if recorder is not None:
                try:
                    recorder.remove_kbd_consumer(self._on_key)
                except Exception:
                    pass
            if self._hook_installed:
                try:
                    recorder.release_kbd_hook()
                except Exception:
                    pass
                self._hook_installed = False
            self._running = False

    def _on_key(self, vk, is_down, injected=False):
        """钩子回调 —— **零 IO、零日志、零锁等待**: 只做缓冲 + 入队。"""
        if not self._running:
            return
        try:
            hit = self.feed(vk, is_down, injected)
        except Exception:
            return
        if hit is None:
            return
        try:
            self._queue.put_nowait(hit)
        except Exception:
            pass

    def _expand_worker(self):
        while self._running or not self._queue.empty():
            try:
                hit = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                self.expand(hit)
            except Exception:
                pass

    def expand(self, hit):
        """执行一次展开: 回删触发词 → 注入文本 / 调脚本运行器。返回是否发出事件。"""
        if not isinstance(hit, dict):
            return False
        n = int(hit.get("backspaces") or 0)
        self._expansions += 1
        if hit.get("kind") == "script":
            _send_events(build_inputs(n, ""))
            runner = self._script_runner
            if runner is not None:
                runner(hit.get("script_path") or "")
            return True
        events = build_inputs(n, hit.get("expand") or "")
        _send_events(events)
        return bool(events)

    def stop(self, timeout=2.0):
        """停止并卸载消费者 (钩子引用计数归零时由 recorder 真正 Unhook)。幂等。"""
        with self._lock:
            self._running = False
            t, w = self._thread, self._worker
        if w is not None and w.is_alive() and w is not threading.current_thread():
            try:
                w.join(timeout)
            except Exception:
                pass
        if t is not None and t.is_alive() and t is not threading.current_thread():
            try:
                t.join(timeout)
            except Exception:
                pass
        with self._lock:
            self._thread = None
            self._worker = None
            self._hook_installed = False
            self._buf = []
            self._queue = queue.Queue()
        return not (t is not None and t.is_alive())

    close = stop

    # ── 已知限制 (UI 明示) ──
    LIMITS_TEXT = ("热字串限制：对以管理员运行的目标程序无效（UIPI 隔离）；"
                   "输入法组合态下不触发；仅支持纯 ASCII 触发词（2~20 字符，"
                   "以空格/回车/Tab 结尾展开）")


_engine = None
_engine_lock = threading.Lock()


def hotstring_engine():
    """模块级单例 (延迟创建, 未 ``start`` 时不存在任何钩子)。"""
    global _engine
    with _engine_lock:
        if _engine is None:
            _engine = HotstringEngine()
        return _engine


def reset_hotstring_engine():
    """丢掉单例引用 (测试用; 不 stop 旧实例)。"""
    global _engine
    with _engine_lock:
        inst = _engine
        _engine = None
        return inst
