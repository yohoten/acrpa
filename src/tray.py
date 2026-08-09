"""
Windows 系统托盘图标模块。

架构:
- 隐藏 tkinter Toplevel → 获取原生 HWND
- SetWindowLongPtrW(GWL_WNDPROC) 子类化窗口过程
- WNDPROC 仅记录事件到 list → root.after 轮询器安全消费
- 零后台线程，零 win32gui 依赖，纯 ctypes + tkinter

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
import tkinter


# ═══════════════════════════════════════════════════════════════════════
# Windows API 常量
# ═══════════════════════════════════════════════════════════════════════

WM_TRAYICON = 0x0400 + 1          # WM_USER + 1 — 托盘回调消息

WM_LBUTTONUP      = 0x0202        # 左键弹起（单击）
WM_LBUTTONDBLCLK  = 0x0203        # 左键双击
WM_RBUTTONUP      = 0x0205        # 右键弹起

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
        on_open_scheduler: 回调 — 菜单"定时执行设置"
    """

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

    # ── 公开 API ──────────────────────────────────────────────────

    @property
    def active(self) -> bool:
        """托盘图标是否已创建。"""
        return self._active

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
        self._nid.uFlags = NIF_TIP
        self._nid.szTip = tip_text
        ctypes.windll.shell32.Shell_NotifyIconW(
            NIM_MODIFY, ctypes.byref(self._nid))

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
        nid.szTip = "A/C RPA"
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
            self._dispatch(lparam)

        if self._running:
            self._after_id = self.root.after(POLL_INTERVAL, self._poll_events)

    def _dispatch(self, lparam):
        """分发托盘鼠标事件。

        支持: 左键单击 (WM_LBUTTONUP)、左键双击 (WM_LBUTTONDBLCLK)、
              右键弹起 (WM_RBUTTONUP)
        """
        if lparam in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
            if self.on_restore:
                self.on_restore()
        elif lparam == WM_RBUTTONUP:
            self._show_popup_menu()

    def _show_popup_menu(self):
        """弹出右键菜单 — 含运行/停止/设置/定时/自启动等全功能。"""
        menu = tkinter.Menu(self.root, tearoff=0,
                            font=("Microsoft YaHei UI", 9))

        import state

        # ── 状态行 ──
        try:
            if state.running:
                label = u"\u25cf 脚本运行中"
            elif state.recording:
                label = u"\u25cf 录制中"
            else:
                label = u"\u25cb 空闲"
        except Exception:
            label = "A/C RPA"
        menu.add_command(label=label, state="disabled")
        menu.add_separator()

        # ── 显示主窗口 ──
        menu.add_command(label=u"显示主窗口", command=self._on_restore)

        # ── 执行/停止 ──
        if state.running or state.recording:
            menu.add_command(label=u"取消执行", command=self._on_stop)
        else:
            menu.add_command(label=u"执行全部", command=self._on_run)
        menu.add_separator()

        # ── 设置 ──
        menu.add_command(label=u"设置", command=self._on_open_settings)
        menu.add_separator()

        # ── 定时执行 ──
        menu.add_command(label=u"定时执行设置", command=self._on_open_scheduler)
        sched_enabled = getattr(state, 'SCHED_ENABLED', False)
        sched_status = u"已启用" if sched_enabled else u"已禁用"
        menu.add_command(
            label=u"定时执行 [{}]".format(sched_status), state="disabled")

        # ── 开机自启动 ──
        auto_start = getattr(state, 'AUTO_START', False)
        auto_status = u"已启用" if auto_start else u"未启用"
        menu.add_command(
            label=u"开机自启动 [{}]".format(auto_status), state="disabled")

        menu.add_separator()
        menu.add_command(label=u"退出程序", command=self._on_quit)

        try:
            menu.tk_popup(self.root.winfo_pointerx(),
                          self.root.winfo_pointery())
        finally:
            menu.grab_release()

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
