"""
Windows 系统托盘图标模块。

架构:
- 隐藏 tkinter Toplevel → 获取原生 HWND
- SetWindowLongPtrW(GWL_WNDPROC) 子类化窗口过程
- WNDPROC 仅记录事件到 list → root.after 轮询器安全消费
- 零后台线程，零 win32gui 依赖，纯 ctypes + tkinter

L4 升级（方案文档 §5 L4 #1/#2）:
- 右键菜单升级为**一级操作入口**：菜单结构由 ``ui/tray_menu.py`` 的纯数据规格
  产出（可单测），本模块只负责构建 ``tkinter.Menu`` 与分发回调；
- 电源类菜单项统一经 ``sys_ops.power_action(..., confirm=...)`` 二次确认，
  默认 60 秒延迟并可在菜单里「取消关机倒计时」；倒计时期间 tooltip 显示剩余秒数；
- 悬停 tooltip 显示实时性能（CPU / 内存 / 磁盘），采集器是 ``sys_perf.py``
  （纯 ctypes、单次 < 5 ms），刷新**限流**为每 ``tray_perf_interval_sec`` 秒最多一次，
  且只在「最近悬停过」时采集，常态零开销。

用法:
    from tray import SystemTray
    tray = SystemTray(root, icon_path="res/automation.ico",
                      on_restore=restore_callback, on_quit=quit_callback)
    tray.create()
    tray.destroy()
"""

import ctypes
import ctypes.wintypes
import os
import time
import tkinter
import utils


def _log_poll_error(key, msg, level="error", interval=60.0):
    """托盘轮询路径的**限流**日志（同一 key 60 s 一条，避免刷屏）。

    轮询器每 80 ms 跑一次，若某处持续失败，逐次记日志会把 acrpa_*.log 冲爆，
    因此统一走 ``crashlog.log_once``。crashlog 不可用时静默（绝不影响托盘）。
    """
    try:
        import crashlog
        crashlog.log_once(key, msg, level, interval)
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════════
# Windows API 常量
# ═══════════════════════════════════════════════════════════════════════

WM_TRAYICON = 0x0400 + 1          # WM_USER + 1 — 托盘回调消息
WM_COPYDATA = 0x004A              # L5: 单实例 IPC (二次启动转发 --exec-task)

WM_MOUSEMOVE      = 0x0200        # 鼠标移动（部分 Shell 会转发给托盘宿主）
WM_LBUTTONDOWN    = 0x0201        # 左键按下
WM_LBUTTONUP      = 0x0202        # 左键弹起（单击）
WM_LBUTTONDBLCLK  = 0x0203        # 左键双击
WM_RBUTTONDOWN    = 0x0204        # 右键按下
WM_RBUTTONUP      = 0x0205        # 右键弹起

NIN_SELECT        = 0x0400        # 托盘图标被选中（键盘/鼠标）
NIN_KEYSELECT     = 0x0401
NIN_BALLOONSHOW   = 0x0402
NIN_BALLOONHIDE   = 0x0403
NIN_POPUPOPEN     = 0x0406        # 悬停弹出 tooltip 前发送
NIN_POPUPCLOSE    = 0x0407

GWL_WNDPROC = -4                  # SetWindowLongPtr 子类化索引

# Shell_NotifyIcon 操作码
NIM_ADD    = 0x00000000
NIM_MODIFY = 0x00000001
NIM_DELETE = 0x00000002

# NOTIFYICONDATA 标志位
NIF_MESSAGE = 0x00000001
NIF_ICON    = 0x00000002
NIF_TIP     = 0x00000004
NIF_INFO    = 0x00000010

# 气泡图标类型
NIIF_INFO    = 0x00000001
NIIF_WARNING = 0x00000002
NIIF_ERROR   = 0x00000003
NIIF_NONE    = 0x00000000

# 图标加载
IDI_APPLICATION  = 32512
IMAGE_ICON       = 1
LR_LOADFROMFILE  = 0x00000010

# 轮询间隔 (ms)
POLL_INTERVAL = 80

# 托盘底座文案（性能/倒计时会追加在后面）
BASE_TIP = "A/C RPA"
# NOTIFYICONDATAW.szTip = 128 wchars
TIP_MAX_LEN = 120
# 「最近悬停」判定窗口的基准秒数（实际取 max(此值, perf_interval*3)）
HOVER_WINDOW_SEC = 6.0

# 会把「图标上发生过鼠标交互」记进悬停时间戳的消息
_HOVER_MSGS = frozenset({
    WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP, WM_LBUTTONDBLCLK,
    WM_RBUTTONDOWN, WM_RBUTTONUP, NIN_SELECT, NIN_KEYSELECT,
    NIN_POPUPOPEN, NIN_BALLOONSHOW,
})


# ═══════════════════════════════════════════════════════════════════════
# 菜单回调键 → SystemTray 方法名（单一真源，供 ui.tray_menu 自检与单测）
# ═══════════════════════════════════════════════════════════════════════

ACTION_METHODS = {
    "restore":              "_on_restore",
    "run":                  "_on_run",
    "stop":                 "_on_stop",
    "reminder_5":           "_reminder_5",
    "reminder_15":          "_reminder_15",
    "reminder_30":          "_reminder_30",
    "reminder_custom":      "_reminder_custom",
    "power_shutdown_delay": "_power_shutdown_delay",
    "power_shutdown":       "_power_shutdown",
    "power_restart":        "_power_restart",
    "power_logoff":         "_power_logoff",
    "power_lock":           "_power_lock",
    "power_sleep":          "_power_sleep",
    "power_hibernate":      "_power_hibernate",
    "power_cancel":         "_power_cancel",
    "mute_toggle":          "_mute_toggle",
    "settings":             "_on_open_settings",
    "tasks":                "_on_open_scheduler",
    "about":                "_about",
    "netlink_toggle":       "_toggle_netlink",
    "quit":                 "_on_quit",
}


# ═══════════════════════════════════════════════════════════════════════
# ctypes 结构体
# ═══════════════════════════════════════════════════════════════════════

class NOTIFYICONDATAW(ctypes.Structure):
    """Shell_NotifyIconW 参数结构体。"""
    _fields_ = [
        ("cbSize",           ctypes.wintypes.DWORD),
        ("hWnd",             ctypes.wintypes.HWND),
        ("uID",              ctypes.wintypes.UINT),
        ("uFlags",           ctypes.wintypes.UINT),
        ("uCallbackMessage", ctypes.wintypes.UINT),
        ("hIcon",            ctypes.wintypes.HICON),
        ("szTip",            ctypes.c_wchar * 128),
        ("dwState",          ctypes.wintypes.DWORD),
        ("dwStateMask",      ctypes.wintypes.DWORD),
        ("szInfo",           ctypes.c_wchar * 256),
        ("uVersion",         ctypes.wintypes.UINT),
        ("szInfoTitle",      ctypes.c_wchar * 64),
        ("dwInfoFlags",      ctypes.wintypes.DWORD),
        ("guidItem",         ctypes.c_wchar * 40),
        ("hBalloonIcon",     ctypes.wintypes.HICON),
    ]


# WNDPROC 函数签名: LRESULT CALLBACK WndProc(HWND, UINT, WPARAM, LPARAM)
_WNDPROC_TYPE = ctypes.WINFUNCTYPE(
    ctypes.c_longlong,             # LRESULT (64-bit)
    ctypes.wintypes.HWND,          # hWnd
    ctypes.wintypes.UINT,          # uMsg
    ctypes.wintypes.WPARAM,        # wParam
    ctypes.wintypes.LPARAM,        # lParam
)


# ═══════════════════════════════════════════════════════════════════════
# SystemTray
# ═══════════════════════════════════════════════════════════════════════

class SystemTray:
    """Windows 系统托盘图标管理器。

    Args:
        root:         tkinter 根窗口 (tkinter.Tk)
        icon_path:    .ico 文件路径，None 使用默认图标
        on_restore:   回调 — 双击托盘图标 / 菜单"显示主窗口"
        on_quit:      回调 — 菜单"退出程序"
        on_run:       回调 — 菜单"执行全部"
        on_stop:      回调 — 菜单"取消执行"
        on_open_settings: 回调 — 菜单"设置"
        on_open_scheduler: 回调 — 菜单"任务管理"
    """

    # 菜单回调键 → 方法名（类属性，便于单测断言「绑定齐全」而不建窗）
    ACTION_METHODS = ACTION_METHODS

    def __init__(self, root: tkinter.Tk, icon_path: str = None,
                 on_restore=None, on_quit=None,
                 on_run=None, on_stop=None,
                 on_open_settings=None, on_open_scheduler=None):
        self.root = root
        self.icon_path = icon_path
        self.on_restore = on_restore
        self.on_quit = on_quit
        self.on_run = on_run
        self.on_stop = on_stop
        self.on_open_settings = on_open_settings
        self.on_open_scheduler = on_open_scheduler

        # ── 修正 API 返回类型（64-bit 指针）──
        _fix_api_signatures()

        self._msg_win = None           # 隐藏 Toplevel
        self._hwnd = None              # 原生 HWND
        self._hicon = None             # 图标句柄
        self._nid = NOTIFYICONDATAW()  # 托盘数据
        self._active = False           # 托盘是否活跃
        self._running = False          # 轮询器是否运行
        self._after_id = None          # root.after ID
        self._orig_wndproc = None      # 原始 WNDPROC 地址
        self._wndproc_ref = None       # Python WNDPROC 引用 (防 GC)
        self._events = []              # 事件队列: WNDPROC → poller

        # ── L4: 悬停性能 tooltip ──
        self._perf_enabled = True
        self._perf_interval = 2.0
        self._perf_next_at = 0.0
        self._last_tip_at = 0.0
        self._hover_ts = 0.0
        self._tip_text = BASE_TIP

        # ── L4: 电源倒计时状态 ──
        self._power_deadline = 0.0     # 0 = 无倒计时
        self._power_action = ""

        self._load_perf_config()

    # ── 公开 API ──────────────────────────────────────────────────

    @property
    def active(self) -> bool:
        """托盘图标是否已创建。"""
        return self._active

    @property
    def tip_text(self) -> str:
        """当前 tooltip 文案（调试/单测用）。"""
        return self._tip_text

    def create(self) -> bool:
        """创建托盘图标。已创建则直接返回 True。"""
        if self._active:
            return True

        try:
            self._create_msg_window()
            self._load_icon()
            self._subclass_window()
            if not self._add_tray_icon():
                self._cleanup_partial()
                return False
            self._start_poller()
            self._active = True
            self._load_perf_config()
            self._refresh_tip(force=True)
            return True
        except Exception:
            self._cleanup_partial()
            return False

    def destroy(self):
        """移除托盘图标并释放所有资源。幂等。"""
        if self._active:
            self._delete_tray_icon()
            self._active = False

        self._running = False
        if self._after_id is not None:
            try:
                self.root.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

        self._power_deadline = 0.0
        self._power_action = ""
        self._unsubclass_window()
        self._destroy_msg_window()
        self._destroy_icon_handle()

    def show_balloon(self, title: str, message: str,
                     icon_type: int = NIIF_INFO):
        """在托盘图标上弹出气泡通知。

        Args:
            title:     标题
            message:   正文
            icon_type: NIIF_INFO / NIIF_WARNING / NIIF_ERROR / NIIF_NONE
        """
        if not self._active:
            return
        self._nid.uFlags = NIF_INFO
        self._nid.szInfoTitle = title
        self._nid.szInfo = message
        self._nid.dwInfoFlags = icon_type
        ctypes.windll.shell32.Shell_NotifyIconW(
            NIM_MODIFY, ctypes.byref(self._nid))
        # 恢复标准 flags
        self._nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        self._nid.szInfoTitle = ""
        self._nid.szInfo = ""

    def update_tip(self, tip_text: str):
        """更新托盘图标悬停提示文字。"""
        if not self._active:
            return
        text = str(tip_text or BASE_TIP)[:TIP_MAX_LEN]
        self._nid.uFlags = NIF_TIP
        self._nid.szTip = text
        try:
            ctypes.windll.shell32.Shell_NotifyIconW(
                NIM_MODIFY, ctypes.byref(self._nid))
        finally:
            # 恢复标准 flags：否则后续 NIM_MODIFY 会丢掉图标/回调消息
            self._nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        self._tip_text = text

    def action_keys(self):
        """→ 本类支持的全部菜单回调键（升序，供 ui.tray_menu 自检）。"""
        return sorted(self.ACTION_METHODS.keys())

    # ── 内部: 窗口管理 ──────────────────────────────────────────

    def _create_msg_window(self):
        """创建隐藏 tkinter Toplevel 并获取原生 HWND。"""
        self._msg_win = tkinter.Toplevel(self.root)
        self._msg_win.withdraw()

        # 设置图标（防止任务栏闪现默认图标）
        if self.icon_path and os.path.exists(self.icon_path):
            try:
                # tkinter 标准图标设置
                self._msg_win.iconbitmap(self.icon_path)

                # WM_SETICON: 确保任务栏也使用正确图标
                WM_SETICON = 0x0080
                ICON_SMALL = 0
                ICON_BIG = 1
                IMAGE_ICON = 1
                LR_LOADFROMFILE = 0x00000010

                hicon = ctypes.windll.user32.LoadImageW(
                    None, self.icon_path, IMAGE_ICON, 0, 0, LR_LOADFROMFILE)

                if hicon:
                    tk_id = self._msg_win.winfo_id()
                    hwnd = ctypes.windll.user32.GetParent(tk_id)
                    if not hwnd:
                        hwnd = tk_id

                    ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, hicon)
                    ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, hicon)
            except Exception:
                pass

        self._msg_win.update_idletasks()
        tk_id = self._msg_win.winfo_id()
        self._hwnd = ctypes.windll.user32.GetParent(tk_id)
        if not self._hwnd:
            self._hwnd = tk_id

    def _destroy_msg_window(self):
        """销毁隐藏消息窗口。"""
        if self._msg_win:
            try:
                self._msg_win.destroy()
            except Exception:
                pass
            self._msg_win = None
            self._hwnd = None

    # ── 内部: 图标 ──────────────────────────────────────────────

    def _load_icon(self):
        """加载 .ico 文件为 HICON，失败则用系统默认图标。"""
        if self.icon_path and os.path.exists(self.icon_path):
            self._hicon = ctypes.windll.user32.LoadImageW(
                0, self.icon_path, IMAGE_ICON, 0, 0, LR_LOADFROMFILE)
        if not self._hicon:
            self._hicon = ctypes.windll.user32.LoadIconW(
                0, IDI_APPLICATION)

    def _destroy_icon_handle(self):
        """释放图标资源。"""
        if self._hicon:
            try:
                ctypes.windll.user32.DestroyIcon(self._hicon)
            except Exception:
                pass
            self._hicon = None

    # ── 内部: 子类化 ────────────────────────────────────────────

    def _subclass_window(self):
        """子类化 Toplevel 窗口过程，拦截 WM_TRAYICON。"""
        if not self._hwnd:
            return

        tray = self  # 闭包引用

        @_WNDPROC_TYPE
        def _wnd_proc(hwnd, msg, wparam, lparam):
            if msg == WM_TRAYICON:
                tray._events.append(lparam)
                return 0
            if msg == WM_COPYDATA:
                # L5: 单实例 IPC —— 解析/校验/入队全部在 single_instance 内完成，
                # 执行经 root.after 回流主线程（**绝不**在此处直接跑任务）。
                # 返回值同步给发送方 (0=已投递 / 1=任务不存在 / 2=载荷非法)。
                try:
                    import single_instance
                    return ctypes.c_longlong(
                        single_instance.handle_copydata(lparam, hwnd))
                except Exception:
                    return ctypes.c_longlong(2)
            return ctypes.windll.user32.CallWindowProcW(
                tray._orig_wndproc, hwnd, msg, wparam, lparam)

        self._wndproc_ref = _wnd_proc
        self._orig_wndproc = ctypes.windll.user32.SetWindowLongPtrW(
            self._hwnd, GWL_WNDPROC, _wnd_proc)

    def _unsubclass_window(self):
        """恢复原始窗口过程。"""
        if self._hwnd and self._orig_wndproc:
            try:
                ctypes.windll.user32.SetWindowLongPtrW(
                    self._hwnd, GWL_WNDPROC, self._orig_wndproc)
            except Exception:
                pass
        self._orig_wndproc = None
        self._wndproc_ref = None

    # ── 内部: Shell_NotifyIcon ──────────────────────────────────

    def _add_tray_icon(self) -> bool:
        """调用 Shell_NotifyIcon(NIM_ADD)。"""
        if not self._hwnd:
            return False
        nid = self._nid
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self._hwnd
        nid.uID = 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_TRAYICON
        nid.hIcon = self._hicon or 0
        nid.szTip = BASE_TIP
        return bool(ctypes.windll.shell32.Shell_NotifyIconW(
            NIM_ADD, ctypes.byref(nid)))

    def _delete_tray_icon(self):
        """调用 Shell_NotifyIcon(NIM_DELETE)。"""
        try:
            ctypes.windll.shell32.Shell_NotifyIconW(
                NIM_DELETE, ctypes.byref(self._nid))
        except Exception:
            pass

    # ── 内部: 事件轮询 ──────────────────────────────────────────

    def _start_poller(self):
        """启动 root.after 事件轮询器。"""
        self._running = True
        self._poll_events()

    def _poll_events(self):
        """轮询事件队列（在正常 tkinter 上下文中执行）。"""
        if not self._running:
            return

        # 批量消费事件
        events = self._events
        self._events = []

        for lparam in events:
            try:
                self._dispatch(lparam)
            except Exception as e:          # noqa: BLE001
                _log_poll_error("tray.dispatch",
                                "托盘事件分发失败: {}".format(e))

        # L4: 悬停性能 tooltip / 电源倒计时（限流在同一处完成）
        try:
            self._poll_perf()
        except Exception as e:              # noqa: BLE001
            _log_poll_error("tray.perf", "托盘性能轮询失败: {}".format(e))

        if self._running:
            try:
                self._after_id = self.root.after(POLL_INTERVAL, self._poll_events)
            except Exception as e:          # noqa: BLE001
                _log_poll_error("tray.after", "托盘轮询重排失败: {}".format(e))
                self._after_id = None
                self._running = False

    def _dispatch(self, lparam):
        """分发托盘鼠标事件。

        支持: 左键单击 (WM_LBUTTONUP)、左键双击 (WM_LBUTTONDBLCLK)、
              右键弹起 (WM_RBUTTONUP)；其余消息只更新「最近悬停」时间戳。
        """
        try:
            if lparam in _HOVER_MSGS:
                self._hover_ts = time.time()
        except Exception:
            pass
        if lparam in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
            if self.on_restore:
                self.on_restore()
        elif lparam == WM_RBUTTONUP:
            self._show_popup_menu()
        elif lparam == NIN_SELECT:
            # 键盘选中（Win+Alt 场景）也给一次性能刷新
            self._refresh_tip(force=True)

    # ── L4: 悬停实时性能 ────────────────────────────────────────

    def _load_perf_config(self):
        """从 state 读取性能 tooltip 开关与刷新间隔（缺键用默认值）。"""
        enabled, interval = True, 2.0
        try:
            import state
            enabled = bool(getattr(state, "TRAY_PERF_ENABLED", True))
            interval = float(getattr(state, "TRAY_PERF_INTERVAL_SEC", 2.0) or 2.0)
        except Exception:
            pass
        self._perf_enabled = enabled
        # 钳制到 1–5 秒：既要「实时感」，又不能常态开销
        self._perf_interval = max(1.0, min(5.0, interval))

    def _hover_recent(self, now=None):
        """→ 最近是否在托盘图标上悬停/交互过。"""
        now = time.time() if now is None else now
        window = max(HOVER_WINDOW_SEC, self._perf_interval * 3.0)
        try:
            return (now - self._hover_ts) <= window
        except Exception:
            return False

    def _poll_perf(self):
        """轮询内的轻量钩子：电源倒计时到期 + 悬停时刷新 tooltip。

        只在「最近悬停过」且距上次刷新 ≥ ``_perf_interval`` 时采集，
        因此常态（无人悬停）CPU 占用 ≈ 0。
        """
        if not self._active:
            return
        now = time.time()
        if self._power_deadline and now >= self._power_deadline:
            self._power_deadline = 0.0
            self._power_action = ""
            self._refresh_tip(force=True)
            return
        if now < self._perf_next_at:
            return
        if not self._hover_recent(now):
            self._perf_next_at = now + self._perf_interval
            return
        self._perf_next_at = now + self._perf_interval
        self._refresh_tip(force=True)

    def _refresh_tip(self, force=False):
        """重算并写回 tooltip：``A/C RPA — CPU: 12% 内存: 48% 磁盘 C: 120GB 可用``。"""
        if not self._active:
            return
        now = time.time()
        if not force and (now - self._last_tip_at) < self._perf_interval:
            return
        self._last_tip_at = now
        parts = [BASE_TIP]
        parts = [BASE_TIP]
        if self._perf_enabled:
            try:
                import sys_perf
                text = sys_perf.format_tooltip(sys_perf.sample())
                if text and text != BASE_TIP:
                    parts.append(text)
            except Exception as e:          # noqa: BLE001
                _log_poll_error("tray.perf_sample",
                                "性能采样失败 (tooltip 退回基础文案): {}".format(e))
        if self._power_deadline:
            left = int(round(self._power_deadline - now))
            if left > 0:
                parts.append("{} {} 秒后".format(self._power_action or "关机", left))
        try:
            self.update_tip(" — ".join(parts))
        except Exception as e:              # noqa: BLE001
            _log_poll_error("tray.update_tip", "更新托盘提示失败: {}".format(e))
    def power_remaining(self):
        """→ 电源倒计时剩余秒数（无倒计时为 0）。"""
        if not self._power_deadline:
            return 0
        return max(0, int(round(self._power_deadline - time.time())))

    # ── L4: 菜单构建 / 分发 ─────────────────────────────────────

    def _menu_ctx(self):
        """收集菜单规格需要的运行时上下文（全部带安全默认）。"""
        ctx = {"running": False, "recording": False,
               "power_delay_sec": 60, "power_cancel_sec": None,
               "sched_enabled": False, "auto_start": False,
               "netlink_enabled": False}
        try:
            import state
            ctx["running"] = bool(getattr(state, "running", False))
            ctx["recording"] = bool(getattr(state, "recording", False))
            ctx["power_delay_sec"] = int(getattr(state, "TRAY_POWER_DELAY_SEC", 60) or 0)
            ctx["sched_enabled"] = bool(getattr(state, "SCHED_ENABLED", False))
            ctx["auto_start"] = bool(getattr(state, "AUTO_START", False))
            ctx["netlink_enabled"] = bool(getattr(state, "NETLINK_ENABLED", False))
        except Exception:
            pass
        left = self.power_remaining()
        ctx["power_cancel_sec"] = left or None
        return ctx

    def build_menu_spec(self):
        """→ 菜单规格（委托 ``ui.tray_menu.build_spec``，失败回退空列表）。"""
        try:
            from ui import tray_menu
            return tray_menu.build_spec(self._menu_ctx())
        except Exception:
            return []

    def _fill_menu(self, menu, spec):
        """按规格递归填充 ``tkinter.Menu``。"""
        for node in spec or []:
            if not isinstance(node, dict):
                continue
            kind = node.get("type")
            label = str(node.get("label") or "")
            action = node.get("action")
            if kind == "separator":
                menu.add_separator()
                continue
            if kind == "status":
                menu.add_command(label=label, state="disabled")
                continue
            if kind == "submenu":
                sub = tkinter.Menu(menu, tearoff=0, font=utils.FONT_BODY)
                self._fill_menu(sub, node.get("children") or [])
                menu.add_cascade(label=label, menu=sub)
                continue
            if kind == "check":
                var = tkinter.BooleanVar(value=bool(node.get("checked")))
                menu.add_checkbutton(
                    label=label, variable=var,
                    command=lambda a=action: self._dispatch_action(a))
                continue
            state_flag = "normal"
            if str(node.get("id")) == "power_cancel" and not self.power_remaining():
                state_flag = "disabled"
            menu.add_command(label=label, state=state_flag,
                             command=lambda a=action: self._dispatch_action(a))

    def _show_popup_menu(self):
        """弹出右键菜单 —— 一级操作入口（结构见 ui/tray_menu.py）。"""
        try:
            menu = tkinter.Menu(self.root, tearoff=0, font=utils.FONT_BODY)
        except Exception:
            menu = tkinter.Menu(self.root, tearoff=0)
        try:
            self._fill_menu(menu, self.build_menu_spec())
        except Exception:
            menu.add_command(label="显示主窗口", command=self._on_restore)
            menu.add_separator()
            menu.add_command(label="退出程序", command=self._on_quit)
        try:
            menu.tk_popup(self.root.winfo_pointerx(),
                          self.root.winfo_pointery())
        finally:
            try:
                menu.grab_release()
            except Exception:
                pass

    def _dispatch_action(self, action):
        """菜单回调键 → 本类方法（未知键静默忽略，异常只记日志）。"""
        name = self.ACTION_METHODS.get(str(action or ""))
        fn = getattr(self, name, None) if name else None
        if not callable(fn):
            return
        try:
            fn()
        except Exception as e:                # noqa: BLE001
            try:
                utils.log1("托盘菜单动作失败 [{}]: {}".format(action, e), "error")
            except Exception:
                pass

    def _toast(self, message, kind="info"):
        """主窗 toast（失败退回日志）。"""
        try:
            utils.show_toast(self.root, str(message), kind)
        except Exception:
            try:
                utils.log1(str(message), "info")
            except Exception:
                pass

    # ── L4: 快速提醒 ────────────────────────────────────────────

    def _schedule_reminder(self, minutes):
        """N 分钟后弹提醒浮窗（用 root.after，不建线程）。"""
        try:
            minutes = max(1, min(1440, int(minutes)))
        except (TypeError, ValueError):
            return
        try:
            self.root.after(minutes * 60 * 1000,
                            lambda m=minutes: self._fire_reminder(m))
            self._toast("已设置提醒：{} 分钟后".format(minutes), "success")
        except Exception as e:                # noqa: BLE001
            try:
                utils.log1("设置托盘提醒失败: {}".format(e), "error")
            except Exception:
                pass

    def _fire_reminder(self, minutes):
        """到点弹提醒浮窗（注入式 API：reminder 不 import 本模块/ACRPA）。"""
        try:
            import reminder
            reminder.show_reminder(
                title="提醒",
                body="{} 分钟已到".format(minutes),
                parent=self.root)
        except Exception as e:                # noqa: BLE001
            try:
                utils.log1("弹出提醒失败: {}".format(e), "error")
            except Exception:
                pass

    def _reminder_5(self):
        self._schedule_reminder(5)

    def _reminder_15(self):
        self._schedule_reminder(15)

    def _reminder_30(self):
        self._schedule_reminder(30)

    def _reminder_custom(self):
        """自定义分钟（简对话框；取消/非法输入静默忽略）。"""
        try:
            from tkinter import simpledialog
            minutes = simpledialog.askinteger(
                "自定义提醒", "多少分钟后提醒？", parent=self.root,
                minvalue=1, maxvalue=1440)
        except Exception:                     # noqa: BLE001
            minutes = None
        if minutes:
            self._schedule_reminder(minutes)

    # ── L4: 电源（必须经 sys_ops.power_action + confirm）────────

    def _confirm_power(self, message):
        """电源操作二次确认（在主线程执行，返回 bool）。"""
        try:
            from tkinter import messagebox
            return bool(messagebox.askyesno(
                "确认电源操作", str(message), parent=self.root))
        except Exception:                     # noqa: BLE001
            return False

    def _power(self, action, delay=None):
        """统一电源入口：默认延迟取 ``state.TRAY_POWER_DELAY_SEC``（60 秒）。"""
        try:
            import state
            import sys_ops
        except Exception as e:                # noqa: BLE001
            self._toast("电源操作不可用: {}".format(e), "error")
            return
        if delay is None:
            try:
                delay = int(getattr(state, "TRAY_POWER_DELAY_SEC", 60) or 0)
            except Exception:
                delay = 60
        ok, msg, detail = sys_ops.power_action(
            action, delay=delay, confirm=self._confirm_power)
        if ok:
            seconds = 0
            try:
                seconds = int((detail or {}).get("delay") or 0)
            except Exception:
                seconds = 0
            if seconds > 0 and action in ("关机", "重启"):
                self._power_deadline = time.time() + seconds
                self._power_action = action
            try:
                utils.log1("托盘电源: {}".format(msg), "info")
            except Exception:
                pass
            self._toast(msg, "warning" if seconds > 0 else "info")
        else:
            try:
                utils.log1("托盘电源未执行: {}".format(msg), "warning")
            except Exception:
                pass
            self._toast(msg, "warning")
        self._refresh_tip(force=True)

    def _power_shutdown_delay(self):
        self._power("关机")

    def _power_shutdown(self):
        self._power("关机", delay=0)

    def _power_restart(self):
        self._power("重启")

    def _power_logoff(self):
        self._power("注销", delay=0)

    def _power_lock(self):
        self._power("锁屏", delay=0)

    def _power_sleep(self):
        self._power("待机", delay=0)

    def _power_hibernate(self):
        self._power("休眠", delay=0)

    def _power_cancel(self):
        """取消已排定的关机/重启倒计时。"""
        try:
            import sys_ops
            ok, msg, _detail = sys_ops.cancel_power()
        except Exception as e:                # noqa: BLE001
            ok, msg = False, "取消失败: {}".format(e)
        if ok:
            self._power_deadline = 0.0
            self._power_action = ""
        self._toast(msg, "success" if ok else "warning")
        self._refresh_tip(force=True)

    # ── L4: 其它一级入口 ────────────────────────────────────────

    def _mute_toggle(self):
        try:
            import sys_ops
            ok, msg, _level = sys_ops.volume("静音切换")
        except Exception as e:                # noqa: BLE001
            ok, msg = False, "静音切换失败: {}".format(e)
        self._toast(msg, "success" if ok else "warning")

    def _toggle_netlink(self):
        """允许远程连接（失败静默，不影响其它菜单项）。"""
        try:
            import netlink
            import state
            state.NETLINK_ENABLED = not getattr(state, "NETLINK_ENABLED", False)
            state.save_config()
            if state.NETLINK_ENABLED:
                netlink.start_netlink(self.root)
            else:
                netlink.stop_netlink()
        except Exception:                     # noqa: BLE001
            pass

    def _about(self):
        """关于对话框（版本号取自 version_info，单一真源）。"""
        version = ""
        try:
            import version_info
            version = str(getattr(version_info, "VERSION", "") or "")
        except Exception:
            version = ""
        text = ("ACRPA 自动化助手" + ("\n版本: {}".format(version) if version else "") +
                "\n\n托盘菜单：快速提醒 / 电源操作 / 静音切换 / 任务管理。\n"
                "悬停托盘图标可查看 CPU / 内存 / 磁盘实时状态。\n"
                "提示：文件拖放依赖第三方组件（未引入），"
                "可直接在主窗或任务管理窗按 Ctrl+V 粘贴路径来新建任务。")
        try:
            from tkinter import messagebox
            messagebox.showinfo("关于 ACRPA", text, parent=self.root)
        except Exception:                     # noqa: BLE001
            self._toast("ACRPA 自动化助手")

    # ── 内部: 回调 & 清理 ───────────────────────────────────────

    def _on_restore(self):
        if self.on_restore:
            self.on_restore()

    def _on_quit(self):
        self.destroy()
        if self.on_quit:
            self.on_quit()

    def _on_run(self):
        if self.on_run:
            self.on_run()

    def _on_stop(self):
        if self.on_stop:
            self.on_stop()

    def _on_open_settings(self):
        if self.on_open_settings:
            self.on_open_settings()

    def _on_open_scheduler(self):
        if self.on_open_scheduler:
            self.on_open_scheduler()

    def _cleanup_partial(self):
        """create() 失败时清理已分配资源。"""
        self._delete_tray_icon()
        self._unsubclass_window()
        self._destroy_msg_window()
        self._destroy_icon_handle()


# ═══════════════════════════════════════════════════════════════════════
# 模块级辅助
# ═══════════════════════════════════════════════════════════════════════

def _fix_api_signatures():
    """修正 ctypes API 签名（64-bit 指针正确处理）。

    仅在首次 SystemTray 实例化时调用一次。
    """
    if getattr(_fix_api_signatures, '_done', False):
        return
    _fix_api_signatures._done = True

    user32 = ctypes.windll.user32
    user32.SetWindowLongPtrW.restype = ctypes.c_void_p
    user32.CallWindowProcW.restype = ctypes.c_longlong
    user32.CallWindowProcW.argtypes = [
        ctypes.c_void_p,             # WNDPROC (64-bit pointer)
        ctypes.wintypes.HWND,
        ctypes.wintypes.UINT,
        ctypes.wintypes.WPARAM,
        ctypes.wintypes.LPARAM,
    ]
