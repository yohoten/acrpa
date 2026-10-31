"""Action recorder — mouse/keyboard capture and ScriptData generation.

P0 Optimization #4: Reduced scan frequency from 30Hz to 20Hz (0.05s interval)
to lower CPU usage while maintaining responsive recording.

P2 Enhancement: Relative coordinate recording mode (  RecordingMode).
  - "绝对坐标": record screen absolute positions → "坐标" command
  - "相对坐标": record positions relative to active window → "窗口坐标" command

P3 Optimization: 录制改为事件驱动 — 优先使用 Windows 低级键盘/鼠标钩子
(WH_KEYBOARD_LL / WH_MOUSE_LL) + 消息泵，仅在真实输入事件发生时执行逻辑，
空闲时线程休眠 (CPU ≈ 0)。钩子安装失败时自动回退到原 20Hz 轮询模式。
"""
import time
import ctypes
import threading
import state
from scriptdata import ScriptData
from utils import log1

# Lazy imports for heavy libraries (P0 optimization #1)
_pyautogui = None

def get_pyautogui():
    """Lazy load pyautogui on first use"""
    global _pyautogui
    if _pyautogui is None:
        import pyautogui as pa
        _pyautogui = pa
    return _pyautogui


# GUI root reference (set by ACRPA.py after import)
_root = None
def set_root(r):
    global _root; _root = r


# ── 录制模式辅助 ──
def _get_recording_mode():
    """Return current recording mode: 'absolute' or 'relative'."""
    return getattr(state, 'RECORDING_MODE', 'absolute') if hasattr(state, 'RECORDING_MODE') else 'absolute'


def _get_active_window_title():
    """Get the title of the current foreground window."""
    try:
        import ctypes
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
        return buf.value
    except Exception:
        return ""


def _get_active_window_offset():
    """Get (left, top) offset of the foreground window for relative coordinate conversion.
    Returns (0, 0) on failure."""
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()

        class RECT(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                        ("right", ctypes.c_long), ("bottom", ctypes.c_long)]
        rect = RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        return (rect.left, rect.top)
    except Exception:
        return (0, 0)


def _record_click(x, y, button="left"):
    mode = _get_recording_mode()
    if mode == "relative":
        offset_x, offset_y = _get_active_window_offset()
        rel_x = x - offset_x
        rel_y = y - offset_y
        title = _get_active_window_title()
        sd = ScriptData("窗口坐标", [
            title if title else "当前窗口",
            str(rel_x), str(rel_y),
            "右" if button == "right" else "左", "1", "0.1", "", "", ""])
        state.recorded_actions.append(sd)
    else:
        sd = ScriptData("坐标", [str(x), str(y),
            "右" if button == "right" else "左", "1", "0.1", "", "", "", ""])
        state.recorded_actions.append(sd)


def _record_key(key):
    sd = ScriptData("按键", [key, "1", "0.1", "", "", "", "", "", ""])
    state.recorded_actions.append(sd)


def _record_hotkey(mods, key):
    """录制热键: mods 可为单个修饰键名, 也可为修饰键名列表 (按首次按下顺序)。

    写入 9 参数格式: 修饰键占参数1..N, 主键紧跟其后, 其余留空。
    (旧版把 "1"/"0.1" 写进参数3/4, 与「热键」多键语义冲突, 改为留空。)
    """
    if isinstance(mods, str):
        mods = [mods]
    keys = [str(m) for m in (mods or []) if m] + [str(key)]
    keys = keys[:4]
    sd = ScriptData("热键", keys + [""] * (9 - len(keys)))
    state.recorded_actions.append(sd)


def _record_wait(seconds):
    sd = ScriptData("等待", [str(seconds), "", "", "", "", "", "", "", ""])
    state.recorded_actions.append(sd)


def _record_scroll(delta):
    """录制滚轮操作（负=下滚, 正=上滚）"""
    sd = ScriptData("滚轮", [str(delta), "", "", "", "", "", "", "", ""])
    state.recorded_actions.append(sd)


def _record_drag(x, y, dx, dy, duration=0.2):
    """录制拖拽操作：从(x,y)相对移动(dx,dy)"""
    mode = _get_recording_mode()
    if mode == "relative":
        offset_x, offset_y = _get_active_window_offset()
        rel_x = x - offset_x
        rel_y = y - offset_y
        title = _get_active_window_title()
        sd = ScriptData("拖拽", [str(rel_x + dx), str(rel_y + dy), str(duration), "", "", "", "", "", ""])
        state.recorded_actions.append(sd)
    else:
        x1, y1 = int(x + dx), int(y + dy)
        sd = ScriptData("拖拽", [str(x1), str(y1), str(duration), "", "", "", "", "", ""])
        state.recorded_actions.append(sd)


def _record_window_activate(title):
    """录制窗口激活"""
    sd = ScriptData("激活窗口", [title, "", "", "", "", "", "", "", ""])
    state.recorded_actions.append(sd)


def _record_screenshot(name=""):
    """录制截屏操作"""
    sd = ScriptData("截屏", [name if name else "rec", "", "", "", "", "", "", ""])
    state.recorded_actions.append(sd)


# ======================================================================
# P3: 事件驱动录制 — Windows 低级钩子 (键盘/鼠标) + 消息泵
# ======================================================================

# 回退轮询模式参数 (钩子安装失败时使用)
SCAN_INTERVAL = 0.05

# 常量
WH_KEYBOARD_LL = 13
WH_MOUSE_LL = 14
HC_ACTION = 0
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
WM_QUIT = 0x0012
PM_REMOVE = 0x0001
DRAG_THRESHOLD = 8  # 拖拽判定像素阈值


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", ctypes.c_uint32), ("scanCode", ctypes.c_uint32),
                ("flags", ctypes.c_uint32), ("time", ctypes.c_uint32),
                ("dwExtraInfo", ctypes.c_size_t)]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", POINT), ("mouseData", ctypes.c_uint32),
                ("flags", ctypes.c_uint32), ("time", ctypes.c_uint32),
                ("dwExtraInfo", ctypes.c_size_t)]


class MSG(ctypes.Structure):
    _fields_ = [("hwnd", ctypes.c_void_p), ("message", ctypes.c_uint),
                ("wParam", ctypes.c_size_t), ("lParam", ctypes.c_ssize_t),
                ("time", ctypes.c_uint32), ("pt", POINT)]


HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int,
                              ctypes.c_size_t, ctypes.c_ssize_t)

_user32 = ctypes.windll.user32
# 64 位下 HHOOK/LPARAM 是指针，必须显式声明 argtypes/restype 防止截断或溢出
_user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, ctypes.c_void_p, ctypes.c_uint32]
_user32.SetWindowsHookExW.restype = ctypes.c_void_p
_user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_size_t, ctypes.c_ssize_t]
_user32.CallNextHookEx.restype = ctypes.c_ssize_t
_user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
_user32.UnhookWindowsHookEx.restype = ctypes.c_int
_user32.PeekMessageW.argtypes = [ctypes.POINTER(MSG), ctypes.c_void_p,
                                 ctypes.c_uint, ctypes.c_uint, ctypes.c_uint]
_user32.PeekMessageW.restype = ctypes.c_int
_user32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
_user32.TranslateMessage.restype = ctypes.c_int
_user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
_user32.DispatchMessageW.restype = ctypes.c_ssize_t

# 钩子句柄与回调引用 (回调必须保持引用，否则被 GC 后回调崩溃)
_kbd_hook = None
_mouse_hook = None
_kbd_proc = None
_mouse_proc = None

# ── S1-04 最小扩展: 单个 WH_KEYBOARD_LL + 引用计数 + 常驻消费者 ──
# 动机: 热字串需要**常驻**键盘钩子, 而录制需要**期间**键盘钩子。若各装一套低级钩子,
# 两者会争抢同一条输入链 (重复回调/互相摘钩)。因此仍只装一个钩子, 用引用计数管理
# 生命周期, 用消费者列表把事件分发给录制之外的使用方 (见 acquire/release/add_consumer)。
_hook_lock = threading.RLock()      # 保护下面三个可变项 (RLock: 函数内可重入)
_kbd_refs = 0                       # 键盘钩子引用计数 (录制 1 + 每个常驻消费者 1)
_kbd_consumers = []                 # [fn(vk, is_down, injected)] 常驻消费者快照列表

# 录制状态 (事件驱动模式共享)
_hook_state = {
    "lbtn_down": False,      # 左键是否按下 (拖拽起点)
    "drag_start": None,      # (x, y) 按下位置
    "mod_active": [],        # 当前按下的修饰键名列表 (按首次按下顺序, 如 ["ctrl","shift"])
    "mod_vks": [],           # 当前按下的修饰键 vk 列表 (与 mod_active 同序)
    "prev_keys": {},         # vk → bool 按键去重 (自动重复过滤)
    "last_action_time": 0.0, # 上一次动作时间 (间隔 >0.5s 插入等待)
}

# 按键名称映射 (与轮询模式共用)
VK_NAMES = {
    0x08: "Backspace", 0x09: "Tab", 0x0D: "Enter", 0x1B: "Esc",
    0x20: "Space", 0x21: "PageUp", 0x22: "PageDown",
    0x23: "End", 0x24: "Home",
    0x25: "Left", 0x26: "Up", 0x27: "Right", 0x28: "Down",
    0x2D: "Insert", 0x2E: "Delete",
    0x70: "F1", 0x71: "F2", 0x72: "F3", 0x73: "F4",
    0x74: "F5", 0x75: "F6", 0x76: "F7", 0x77: "F8",
    0x78: "F9", 0x79: "F10", 0x7A: "F11", 0x7B: "F12",
    0xA0: "LShift", 0xA1: "RShift", 0xA2: "LCtrl", 0xA3: "RCtrl",
    0xA4: "LAlt", 0xA5: "RAlt",
}
MOD_VK = {0xA0: "shift", 0xA1: "shift", 0xA2: "ctrl", 0xA3: "ctrl",
          0xA4: "alt", 0xA5: "alt"}
MOD_VK_SET = set(MOD_VK)

# 扩展按键扫描范围 (仅回退轮询模式使用)
_SCAN_RANGES = list(range(0x08, 0x5B)) + list(range(0x60, 0x70)) + list(range(0x70, 0x7C))


def vk_to_name(vk):
    if vk in VK_NAMES: return VK_NAMES[vk]
    if 0x30 <= vk <= 0x39: return chr(vk)
    if 0x41 <= vk <= 0x5A: return chr(vk)
    if 0x60 <= vk <= 0x6F: return "NumPad{}".format(vk - 0x60)
    return None


def _reset_hook_state():
    """录制开始时重置钩子状态。"""
    st = _hook_state
    st["lbtn_down"] = False
    st["drag_start"] = None
    st["mod_active"] = []
    st["mod_vks"] = []
    st["prev_keys"] = {}
    st["last_action_time"] = time.time()


def _on_key_event(vk, is_down):
    """处理键盘钩子事件 (按下/抬起)。"""
    st = _hook_state
    if vk in MOD_VK_SET:
        # 修饰键: 按下累加 (保留首次按下顺序), 抬起移除; 支持多修饰键
        name = MOD_VK[vk]
        if is_down and not st["prev_keys"].get(vk):
            if vk not in st["mod_vks"]:
                st["mod_vks"].append(vk)
            if name not in st["mod_active"]:
                st["mod_active"].append(name)
        elif not is_down:
            if vk in st["mod_vks"]:
                st["mod_vks"].remove(vk)
            # LShift/RShift 同名: 另一个仍按住时不清除
            if not any(MOD_VK[x] == name for x in st["mod_vks"]):
                if name in st["mod_active"]:
                    st["mod_active"].remove(name)
        st["prev_keys"][vk] = is_down
        return
    if not is_down:
        st["prev_keys"][vk] = False
        return
    if st["prev_keys"].get(vk):
        return  # 按住自动重复，忽略
    st["prev_keys"][vk] = True
    name = vk_to_name(vk)
    if not name:
        return
    gap = time.time() - st["last_action_time"]
    if gap > 0.5: _record_wait(round(gap, 1))
    if st["mod_active"]:
        _record_hotkey(list(st["mod_active"]), name)
    else:
        _record_key(name)
    st["last_action_time"] = time.time()


def _on_mouse_event(wParam, x, y):
    """处理鼠标钩子事件 (左键按下/抬起 → 点击或拖拽，右键按下 → 点击)。"""
    st = _hook_state
    now = time.time()
    if wParam == WM_LBUTTONDOWN:
        st["lbtn_down"] = True
        st["drag_start"] = (x, y)
    elif wParam == WM_LBUTTONUP:
        if not st["lbtn_down"]:
            return
        st["lbtn_down"] = False
        sx, sy = st["drag_start"] if st["drag_start"] else (x, y)
        dx = x - sx; dy = y - sy
        gap = now - st["last_action_time"]
        if gap > 0.5: _record_wait(round(gap, 1))
        if abs(dx) > DRAG_THRESHOLD or abs(dy) > DRAG_THRESHOLD:
            _record_drag(sx, sy, dx, dy)
        else:
            _record_click(sx, sy, "left")
        st["last_action_time"] = now
    elif wParam == WM_RBUTTONDOWN:
        gap = now - st["last_action_time"]
        if gap > 0.5: _record_wait(round(gap, 1))
        _record_click(x, y, "right")
        st["last_action_time"] = now


def _kbd_callback(nCode, wParam, lParam):
    """键盘低级钩子回调 (仅在真实按键事件时被系统调用)。

    S1-04 扩展: 除录制外, 还把事件分发给常驻消费者 (热字串)。
    回调内**不得做任何 IO/日志** —— 超时会被系统摘钩, 拖慢全局输入。
    """
    if nCode == HC_ACTION and wParam in (WM_KEYDOWN, WM_SYSKEYDOWN,
                                         WM_KEYUP, WM_SYSKEYUP):
        is_down = wParam in (WM_KEYDOWN, WM_SYSKEYDOWN)
        vk = 0
        injected = False
        try:
            info = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            vk = int(info.vkCode)
            injected = bool(int(getattr(info, "flags", 0)) & 0x10)  # LLKHF_INJECTED
        except Exception:
            vk = 0
        # 录制路径: 仅录制中才走 (钩子可能由热字串常驻持有, 此时绝不能记动作)
        if getattr(state, "recording", False):
            try:
                _on_key_event(vk, is_down)
            except Exception:
                pass
        # 常驻消费者路径 (热字串): 零 IO
        if _kbd_consumers:
            for fn in tuple(_kbd_consumers):
                try:
                    fn(vk, is_down, injected)
                except Exception:
                    pass
    return _user32.CallNextHookEx(_kbd_hook, nCode, wParam, lParam)


def _mouse_callback(nCode, wParam, lParam):
    """鼠标低级钩子回调 (仅在真实鼠标事件时被系统调用)。"""
    if nCode == HC_ACTION and wParam in (WM_LBUTTONDOWN, WM_LBUTTONUP, WM_RBUTTONDOWN):
        try:
            info = ctypes.cast(lParam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
            _on_mouse_event(wParam, int(info.pt.x), int(info.pt.y))
        except Exception:
            pass
    return _user32.CallNextHookEx(_mouse_hook, nCode, wParam, lParam)


def acquire_kbd_hook():
    """S1-04: 申请低级键盘钩子 (引用计数)。全库**只装一个** WH_KEYBOARD_LL。

    返回是否可用。调用方线程必须自己跑消息泵 (录制 → _message_pump,
    常驻消费者 → run_hook_pump), 否则钩子回调无人分发。
    """
    global _kbd_hook, _kbd_proc, _kbd_refs
    with _hook_lock:
        _kbd_refs += 1
        if _kbd_hook:
            return True
        _kbd_proc = HOOKPROC(_kbd_callback)
        _kbd_hook = _user32.SetWindowsHookExW(WH_KEYBOARD_LL, _kbd_proc, None, 0)
        if not _kbd_hook:
            _kbd_proc = None
            _kbd_refs = max(0, _kbd_refs - 1)
            return False
        return True


def release_kbd_hook():
    """S1-04: 归还键盘钩子引用; 计数归零才真正 Unhook (幂等, 不抛)。"""
    global _kbd_hook, _kbd_proc, _kbd_refs
    with _hook_lock:
        if _kbd_refs > 0:
            _kbd_refs -= 1
        if _kbd_refs == 0 and _kbd_hook:
            try:
                _user32.UnhookWindowsHookEx(_kbd_hook)
            except Exception:
                pass
            _kbd_hook = None
            _kbd_proc = None


def add_kbd_consumer(fn):
    """S1-04: 注册键盘常驻消费者 ``fn(vk, is_down, injected)``。重复注册幂等。

    ⚠ fn 在**系统输入回调线程**里执行: 禁止任何 IO/日志/阻塞, 只做内存缓冲。
    """
    with _hook_lock:
        if fn not in _kbd_consumers:
            _kbd_consumers.append(fn)


def remove_kbd_consumer(fn):
    """S1-04: 注销键盘常驻消费者 (不存在也安全)。"""
    with _hook_lock:
        try:
            _kbd_consumers.remove(fn)
        except ValueError:
            pass


def run_hook_pump(should_stop):
    """S1-04: 与录制同款的消息泵 (PeekMessage+DispatchMessage), 供常驻宿主线程使用。

    与 _message_pump 的差别只有停止条件由调用方给出 (录制看 record_stop/quit2/_closing)。
    """
    msg = MSG()
    while not should_stop():
        if _user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
            if msg.message == WM_QUIT:
                break
            _user32.TranslateMessage(ctypes.byref(msg))
            _user32.DispatchMessageW(ctypes.byref(msg))
            continue
        time.sleep(0.01)


def _install_hooks():
    """安装键盘/鼠标低级钩子。任一成功即视为可用。"""
    global _mouse_hook, _mouse_proc
    ok_kbd = acquire_kbd_hook()
    with _hook_lock:
        if _mouse_hook is None:
            _mouse_proc = HOOKPROC(_mouse_callback)
            _mouse_hook = _user32.SetWindowsHookExW(WH_MOUSE_LL, _mouse_proc, None, 0)
            if not _mouse_hook:
                _mouse_proc = None
    return bool(ok_kbd or _mouse_hook)


def _uninstall_hooks():
    """卸载钩子并释放回调引用 (键盘钩子按引用计数归还, 不硬摘别人在用的钩子)。"""
    global _mouse_hook, _mouse_proc
    with _hook_lock:
        if _mouse_hook:
            try:
                _user32.UnhookWindowsHookEx(_mouse_hook)
            except Exception:
                pass
            _mouse_hook = None
        _mouse_proc = None
    release_kbd_hook()


def _message_pump():
    """消息泵: 有消息立即分发 (事件驱动)，空闲休眠 10ms (CPU ≈ 0)。
    循环中检查 record_stop / quit2 / _closing 以实现停止响应。"""
    msg = MSG()
    while not state.record_stop and not state.quit2 and not getattr(state, "_closing", False):
        if _user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
            if msg.message == WM_QUIT:
                break
            _user32.TranslateMessage(ctypes.byref(msg))
            _user32.DispatchMessageW(ctypes.byref(msg))
            continue
        time.sleep(0.01)


# ======================================================================
# 回退轮询模式 (钩子不可用时的原 20Hz 实现)
# ======================================================================

def _polling_loop():
    """原轮询录制逻辑 (20Hz GetAsyncKeyState 扫描)，事件驱动不可用时的回退。"""
    user32 = _user32

    prev_lbtn = False; prev_rbtn = False
    prev_keys = {}
    mod_active = []          # 按下的修饰键 vk 列表 (按首次按下顺序, 支持多修饰键)
    last_action_time = time.time()
    drag_active = False
    drag_start_x = 0; drag_start_y = 0

    while not state.record_stop:
        if state.quit2: break
        time.sleep(SCAN_INTERVAL)
        now = time.time()

        lbtn = user32.GetAsyncKeyState(0x01) & 0x8000
        rbtn = user32.GetAsyncKeyState(0x02) & 0x8000

        if lbtn and not prev_lbtn:
            pa = get_pyautogui()
            drag_start_x, drag_start_y = pa.position()
            drag_active = True
        if drag_active and (not lbtn):
            pa = get_pyautogui()
            ex, ey = pa.position()
            dx = ex - drag_start_x; dy = ey - drag_start_y
            if abs(dx) > DRAG_THRESHOLD or abs(dy) > DRAG_THRESHOLD:
                gap = now - last_action_time
                if gap > 0.5: _record_wait(round(gap, 1))
                _record_drag(drag_start_x, drag_start_y, dx, dy)
                last_action_time = now
            elif not rbtn and not prev_rbtn:
                gap = now - last_action_time
                if gap > 0.5: _record_wait(round(gap, 1))
                _record_click(drag_start_x, drag_start_y, "left")
                last_action_time = now
            drag_active = False

        if rbtn and not prev_rbtn:
            gap = now - last_action_time
            if gap > 0.5: _record_wait(round(gap, 1))
            pa = get_pyautogui()
            x, y = pa.position()
            _record_click(x, y, "right")
            last_action_time = now
        prev_lbtn = lbtn; prev_rbtn = rbtn

        # 修饰键校准: 已抬起的逐个移除 (保留仍按住的, 顺序不变)
        for mvk in list(mod_active):
            if not (user32.GetAsyncKeyState(mvk) & 0x8000):
                mod_active.remove(mvk)

        for vk in _SCAN_RANGES:
            if vk in MOD_VK_SET: continue
            state_ = user32.GetAsyncKeyState(vk) & 0x8000
            was_down = prev_keys.get(vk, False)
            if state_ and not was_down:
                name = vk_to_name(vk)
                if name:
                    gap = now - last_action_time
                    if gap > 0.5: _record_wait(round(gap, 1))
                    if mod_active:
                        _record_hotkey([MOD_VK[v] for v in mod_active], name)
                    else:
                        _record_key(name)
                    last_action_time = now
            prev_keys[vk] = state_

        for vk in MOD_VK_SET:
            state_ = user32.GetAsyncKeyState(vk) & 0x8000
            was_down = prev_keys.get(vk, False)
            if state_ and not was_down and vk not in mod_active:
                mod_active.append(vk)
            prev_keys[vk] = state_


def recorder_thread():
    """录制入口: 优先事件驱动 (低级钩子 + 消息泵)，失败时回退轮询。

    两种模式对外行为一致: 点击/右键/拖拽/按键/热键/等待 录制，
    通过 state.record_stop 停止 (ACRPA.py 各停止入口均可触发)。
    """
    try:
        state.recording = True
        state.record_stop = False
        state.recorded_actions = []

        mode = _get_recording_mode()
        mode_label = "相对坐标(窗口)" if mode == "relative" else "绝对坐标"
        log1("录制开始 [{}] — 按 Ctrl+Shift+Q 或点击停止录制结束 (支持: 点击/按键/拖拽/热键)".format(mode_label))

        try:
            _reset_hook_state()
            if _install_hooks():
                _message_pump()
            else:
                log1("低级钩子安装失败，回退到轮询模式")
                _polling_loop()
        finally:
            _uninstall_hooks()

        state.recording = False
        log1("录制结束，共 {} 个动作".format(len(state.recorded_actions)))
        # Auto-fill editor on main thread (Tkinter not thread-safe)
        if _root and hasattr(state, '_on_recording_done'):
            _root.after(0, state._on_recording_done)
    except Exception as e:
        log1("录制线程异常: {}".format(e), "error")
        state.recording = False
        try:
            _uninstall_hooks()
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════════
# 浏览器录制薄适配（P2-A）—— codegen 文本 → DSL → ScriptData → recorded_actions
# ═══════════════════════════════════════════════════════════════════════
# 说明: 转换器本体 codegen_to_dsl 位于 browser_backend（纯函数、无 playwright 依赖）；
#       本节仅做“取文本 → 转换 → 追加 state.recorded_actions”的薄适配。
#       不改动任何既有键鼠录制函数、不改动 GUI/窗口代码。
def convert_and_append(code_text, strict=False):
    """codegen 文本 → rows → ScriptData → 追加到 state.recorded_actions。

    返回 ``(added, warnings)``：added=追加条数；warnings=未识别语句清单。
    ``strict=True`` 时若存在未识别语句，由转换器抛 CodegenConvertError。
    """
    from browser_backend import codegen_to_dsl  # 延迟 import（无 playwright 依赖）
    rows, warnings = codegen_to_dsl(code_text, strict=strict)
    added = 0
    for r in (rows or []):
        cmd = r.get("cmd") or ""
        if not cmd:
            continue
        sd = ScriptData(cmd, list(r.get("args") or []))
        state.recorded_actions.append(sd)
        added += 1
    return (added, warnings)


def record_browser_codegen(code_text, strict=False):
    """便捷包装：转换并记录日志（供菜单/外部入口调用）。返回 ``(added, warnings)``。"""
    try:
        added, warnings = convert_and_append(code_text, strict=strict)
    except Exception as e:
        log1("浏览器录制转换失败: {}".format(e), "error")
        return (0, [])
    log1("浏览器录制: 追加 {} 条动作, {} 条未识别".format(added, len(warnings or [])))
    for w in (warnings or []):
        log1("  未识别: {}".format(w), "warning")
    return (added, warnings)
