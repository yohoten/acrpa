"""Theme, fonts, utilities, ThreadSafeLog, card factory.

P0 Optimization #5: Batched log writing to reduce I/O operations by 80%.
P1 Enhancement: Structured logging with LogLevel, daily rotation, and retention ( AutomationOperation).
"""
import queue, time, os, glob, re
import tkinter
import tkinter.font
import state

# ── LogLevel constants ( ) ──
LOG_DEBUG = 0
LOG_INFO = 1
LOG_WARNING = 2
LOG_ERROR = 3

_LOG_LEVEL_NAMES = {
    LOG_DEBUG: "DEBUG",
    LOG_INFO: "INFO",
    LOG_WARNING: "WARNING",
    LOG_ERROR: "ERROR",
}


def _safe_get(val, default, converter=None):
    """Safely extract a typed value from an Excel cell."""
    if val is None: return default
    if isinstance(val, str):
        try:
            if converter: return converter(val)
        except ValueError: return default
        return val if converter is None else default
    try:
        if converter: return converter(val)
        return val
    except (ValueError, TypeError): return default


def _darken(hex_color, factor=0.15):
    """Darken a hex color by multiplying RGB channels."""
    try:
        h = hex_color.lstrip("#")
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        r, g, b = max(0, int(r*(1-factor))), max(0, int(g*(1-factor))), max(0, int(b*(1-factor)))
        return "#{:02x}{:02x}{:02x}".format(r, g, b)
    except Exception:
        return C["acl"]


# === Toast Notification System ===

def show_toast(root, message, msg_type="info", duration=2000):
    """
    Display a toast notification.
    
    Args:
        root: Tkinter root window reference
        message: Text to display
        msg_type: "info", "success", "warning", or "error"
        duration: Auto-dismiss time in ms (0 = no auto-dismiss)
    """
    # 主题化配色：C 会被 apply_theme() 重绑，故函数内实时取色，禁止模块级写死
    icon_map = {"info": "ℹ", "success": "✓", "warning": "⚠", "error": "✗"}
    bg_map = {"info": C["ac"], "success": C["sc"], "warning": C["wn"], "error": C["dg"]}
    # warning 的琥珀色在深浅两套配色下都偏亮，白字对比不足，改用深色文字；
    # info/success/error 三色饱和度足够，白字在两种主题下均清晰可读。
    if msg_type == "warning":
        fg_color = C["bg"] if state.DARK_MODE else C["fgt"]
    else:
        fg_color = "white"
    bg_color = bg_map.get(msg_type, bg_map["info"])
    icon = icon_map.get(msg_type, icon_map["info"])
    
    # Create toast window
    toast = tkinter.Toplevel(root)
    toast.overrideredirect(True)  # No window decorations
    toast.attributes("-topmost", True)
    toast.attributes("-alpha", 0.95)
    
    # Calculate position (center-top of main window)
    x = root.winfo_x() + root.winfo_width() // 2 - 120
    y = root.winfo_y() + 60
    toast.geometry("+{}+{}".format(x, y))
    
    # Style the toast
    toast.configure(bg=bg_color)
    
    # Content frame
    content = tkinter.Frame(toast, bg=bg_color)
    content.pack(padx=16, pady=10)
    
    # Icon and message
    label_text = "{}  {}".format(icon, message)
    label = tkinter.Label(content, text=label_text,
        font=FONT_BUTTON,
        fg=fg_color, bg=bg_color)
    label.pack()
    
    # Auto-dismiss
    if duration > 0:
        toast.after(duration, toast.destroy)
    
    return toast


# === Theme & colors ===

def _colors():
    """Modern flat-design palette: brand-blue primary, dark-readable text."""
    # hlbg: 「当前行/变更项」高亮底色 (脚本树 running 行、变量树 changed 项);
    #       旧实现写死 #FEF3C7, 暗色下刺眼, 现纳入主题色表统一随主题切换。
    if state.DARK_MODE:
        return dict(
            bg="#0b1120", bgc="#1a2332", fgt="#f1f5f9", fgb="#cbd5e0",
            fgm="#9ca3af", ac="#60a5fa", ach="#3b82f6", acl="#1b3d6e",
            sc="#34d399", dg="#f87171", wn="#fbbf24", bd="#374051",
            logbg="#1a2332", logfg="#e2e8f0", ebg="#1a2332",
            err="#f87171", errbg="#3a2525", ok="#34d399",
            hover="#3b82f6", cardhover="#60a5fa", focus="#60a5fa",
            flowbg="#0f172a", hlbg="#4a3a12")
    return dict(
        bg="#f5f6f8", bgc="#ffffff", fgt="#1a202c", fgb="#2d3748",
        fgm="#718096", ac="#2563eb", ach="#1d4ed8", acl="#eff6ff",
        sc="#10b981", dg="#ef4444", wn="#f59e0b", bd="#e2e8f0",
        logbg="#fdfdfd", logfg="#2d3748", ebg="#ffffff",
        err="#ef4444", errbg="#fff0f0", ok="#10b981",
        hover="#1d4ed8", cardhover="#3b82f6", focus="#2563eb",
        flowbg="#f8fafc", hlbg="#FEF3C7")


C = _colors()


def themed(key, fallback=None):
    """安全读取当前主题色：C 会被 apply_theme() 重新绑定，故每次实时取。
    取不到时回退到 fallback（再取不到回退到 C.get('fgt')）。"""
    try:
        return C[key]
    except Exception:
        if fallback is not None:
            return fallback
        try:
            return C.get("fgt")
        except Exception:
            return None


# ── Font roles (Tk 命名字体) ──
# 注意: FONT_* 现为「命名字体角色名」字符串, 不再是 (family,size,weight) 元组。
# 字族/字号/字重由下方 _FONT_SPECS + ui_scale 统一落到 Tk 命名字体上, 任何
# widget 只需引用角色名; 切换缩放档位时仅 fontconfigure 名字, 不必重建 widget。
FONT_TITLE  = "ACRPA_TITLE"
FONT_BODY   = "ACRPA_BODY"
FONT_LOG    = "ACRPA_LOG"
FONT_SMALL  = "ACRPA_SMALL"
FONT_BUTTON = "ACRPA_BUTTON"
FONT_SMALL_BOLD = "ACRPA_SMALL_BOLD"
FONT_TINY       = "ACRPA_TINY"
FONT_ICON       = "ACRPA_ICON"
FONT_ICON_MD    = "ACRPA_ICON_MD"
FONT_ICON_LG    = "ACRPA_ICON_LG"

# 字体角色表: 角色名 -> (字族, 基准 pt, 字重) —— 全项目唯一字体定义源
_FONT_SPECS = {
    "ACRPA_TITLE":      ("Microsoft YaHei UI", 10, "bold"),
    "ACRPA_BODY":       ("Microsoft YaHei UI", 9,  "normal"),
    "ACRPA_SMALL":      ("Microsoft YaHei UI", 8,  "normal"),
    "ACRPA_SMALL_BOLD": ("Microsoft YaHei UI", 8,  "bold"),
    "ACRPA_TINY":       ("Microsoft YaHei UI", 7,  "normal"),
    "ACRPA_BUTTON":     ("Microsoft YaHei UI", 9,  "bold"),
    "ACRPA_LOG":        ("Consolas",           9,  "normal"),
    "ACRPA_ICON":       ("Segoe UI Symbol",    9,  "normal"),
    "ACRPA_ICON_MD":    ("Segoe UI Symbol",   11,  "normal"),
    "ACRPA_ICON_LG":    ("Segoe UI Symbol",   12,  "normal"),
}

# ── UI 缩放 (pt 制) ──
UI_SCALE_DEFAULT = 1.0
UI_SCALE_MIN     = 0.8
UI_SCALE_MAX     = 1.5

_font_root = None   # init_fonts() 记录的 root, 供 dpi_factor()/set_ui_scale() 复用
# 命名字体句柄强引用: tkinter.font.Font 的 __del__ 会 font delete 自己创建的字体,
# 若不保留引用, 刚建好的命名字体立刻被回收删除 (实测 TclError: does not already exist)。
_font_cache = {}


def dpi_factor():
    """屏幕 DPI 相对 96 的倍率; 需 root 存在 (winfo_fpixels("1i")/96)。

    无 root 或取值失败 -> 1.0。DPI 档位 (100%/150%/200% 的写死因子) 暂不固化:
    需在实机三档实测后再决定是否切换到像素制, 此处只提供可配置骨架。
    """
    r = _font_root
    if r is None:
        return 1.0
    try:
        return max(0.5, min(4.0, float(r.winfo_fpixels("1i")) / 96.0))
    except Exception:
        return 1.0


def current_ui_scale():
    """读取 state.UI_SCALE 并 clamp 到 [UI_SCALE_MIN, UI_SCALE_MAX]; 失败返回 1.0。"""
    try:
        v = float(getattr(state, "UI_SCALE", UI_SCALE_DEFAULT))
    except Exception:
        return UI_SCALE_DEFAULT
    return max(UI_SCALE_MIN, min(UI_SCALE_MAX, v))


def fit_pt(size, scale=None):
    """基准 pt -> 生效 pt: max(6, round(size * ui_scale))。不含 dpi_factor。

    Tk 命名字体在该平台下由 `tk scaling` 处理 pt->px, 故此处再乘 dpi_factor
    会双重放大; 实机 DPI 实测前一律不乘。
    """
    try:
        s = current_ui_scale() if scale is None else float(scale)
    except Exception:
        s = UI_SCALE_DEFAULT
    try:
        return max(6, int(round(float(size) * s)))
    except Exception:
        return 6


def scaled(value):
    """设计像素 -> 实际像素: round(value * dpi_factor() * ui_scale), 下限 1。"""
    try:
        return max(1, int(round(float(value) * dpi_factor() * current_ui_scale())))
    except Exception:
        return 1


def font(role):
    """返回可传给 Tk 的命名字体字符串; 未知角色回退 FONT_BODY。"""
    return role if role in _FONT_SPECS else FONT_BODY


def init_fonts(root):
    """创建/刷新全部命名字体。必须在 Tk() 之后、创建任何 widget 之前调用。

    幂等: 已存在的命名字体只 configure(family/size/weight), 不重复创建。
    root 为 None 或 Tk 不可用 -> 静默返回, 保证无 GUI 环境 import utils 不受影响。
    """
    global _font_root
    if root is None:
        return
    _font_root = root
    scale = current_ui_scale()
    for role, (family, size, weight) in _FONT_SPECS.items():
        pt = fit_pt(size, scale)
        f = None
        try:
            # 已存在: 仅改属性 (幂等; exists=True 包装既有命名, 不删底层字体)。
            # 不能用 nametofont(role, root=root): Python 3.9 签名为 nametofont(name),
            # 传 root= 会抛 TypeError 并被吞掉; Font(..., exists=True) 跨 3.8~3.10+ 等价。
            f = tkinter.font.Font(root=root, name=role, exists=True)
        except Exception:
            # 首次: 创建命名; 必须保留强引用并关闭自动删除, 否则初始化即被回收
            try:
                f = tkinter.font.Font(root=root, name=role, exists=False,
                                      family=family, size=pt, weight=weight)
                f.delete_font = False
                _font_cache[role] = f
                continue
            except Exception:
                continue
        try:
            f.configure(family=family, size=pt, weight=weight)
        except Exception as e:
            # 不再静默吞掉: 改配失败必须可见, 否则「字体未生效」会被掩盖。
            log1("命名字体改配失败: {} -> {}pt ({})".format(role, pt, e))


def set_ui_scale(scale):
    """clamp 到 [0.8,1.5] -> 写 state.UI_SCALE -> 立即 fontconfigure 全部角色。

    返回实际生效值。root 尚未建立时只改 state (下次 init_fonts 生效)。
    """
    try:
        v = float(scale)
    except Exception:
        v = UI_SCALE_DEFAULT
    v = max(UI_SCALE_MIN, min(UI_SCALE_MAX, v))
    try:
        state.UI_SCALE = v
    except Exception:
        pass
    if _font_root is not None:
        for role, (family, size, weight) in _FONT_SPECS.items():
            try:
                # 同 init_fonts: 3.9 不支持 nametofont(root=), 用 Font(exists=True)
                # 取命名句柄后改字号。
                tkinter.font.Font(root=_font_root, name=role,
                                  exists=True).configure(size=fit_pt(size, v))
            except Exception as e:
                # 不再静默吞掉: 缩放档位切换时的改配失败必须可见。
                log1("界面缩放字体改配失败: {} -> {}pt ({})".format(
                    role, fit_pt(size, v), e))
    return v

PAD = {"padx":12,"pady":6}
PI  = {"padx":10,"pady":5}

# ── 滚动条样式统一常量 ──
SCROLLBAR_KW = {"width": 7, "relief": "flat", "bg": C["bd"], "troughcolor": C["logbg"]}


def create_card(parent):
    return tkinter.Frame(parent, bg=C["bgc"], bd=0,
        highlightbackground=C["bd"], highlightthickness=1)


def _btn(parent, text, cmd, bg_c=None, fg_c=None, tip=None):
    """Toolbar button factory with hover darken effect + optional tooltip.

    内边距走 scaled() 令牌, 随 dpi_factor * ui_scale 缩放, 避免大字号下串行。
    """
    if bg_c is None: bg_c = C["bgc"]
    if fg_c is None: fg_c = C["fgb"]
    if bg_c in ("white", "#ffffff", C["bgc"]):
        abg = C["acl"]
    else:
        abg = _darken(bg_c)
    btn = tkinter.Button(parent, text=text, font=FONT_SMALL, bg=bg_c, fg=fg_c,
        activebackground=abg, activeforeground=fg_c,
        relief="raised", bd=3, cursor="hand2",
        padx=scaled(10), pady=scaled(4), command=cmd)
    if tip:
        attach_tooltip(btn, tip)
    return btn


# ── Tooltip 体系 (统一悬停提示，供 ACRPA/settings_window/dialogs 复用) ──
_tip_win = None  # 当前 tooltip 窗口

def _hide_tooltip(event=None):
    """销毁当前 tooltip 窗口。"""
    global _tip_win
    if _tip_win:
        try:
            _tip_win.destroy()
        except Exception:
            pass
        _tip_win = None

def _show_tooltip(event, getter):
    """显示 tooltip (getter 返回文本字符串，可动态计算)。"""
    global _tip_win
    _hide_tooltip()
    txt = getter() if callable(getter) else getter
    if not txt:
        return
    try:
        _tip_win = tkinter.Toplevel(event.widget.winfo_toplevel())
        _tip_win.wm_overrideredirect(True)
        _tip_win.wm_geometry("+{}+{}".format(event.x_root + 12, event.y_root - 8))
        tkinter.Label(_tip_win, text=txt, font=FONT_SMALL,
            bg=C["bgc"], fg=C["fgb"], relief="solid", bd=1, padx=6, pady=2).pack()
    except Exception:
        pass

def attach_tooltip(widget, text):
    """为控件绑定悬停提示。text 可为字符串或返回字符串的可调用对象(动态提示)。"""
    getter = text if callable(text) else (lambda t=text: t)
    widget.bind("<Enter>", lambda e: _show_tooltip(e, getter))
    widget.bind("<Leave>", _hide_tooltip)


def apply_theme(root_widget, style):
    """Refresh colors dict and re-apply all ttk + widget styles."""
    global C
    C = _colors()
    root_widget.configure(bg=C["bg"])

    # === Notebook / Tabs - compact ===
    style.configure("TNotebook", background=C["bg"], borderwidth=0)
    style.configure("TNotebook.Tab", font=FONT_BODY, padding=(20,6),
                    background=C["bd"], foreground=C["fgm"])
    style.map("TNotebook.Tab",
              font=[("selected", FONT_BUTTON)],
              background=[("selected", C["bgc"]), ("active", C["acl"])],
              foreground=[("selected", C["ac"])])

    # === Treeview (脚本编辑器) 主题化 ===
    style.configure("Treeview", background=C["bgc"], fieldbackground=C["bgc"],
                    foreground=C["fgb"], rowheight=22, borderwidth=0)
    style.map("Treeview",
              background=[("selected", C["acl"])],
              foreground=[("selected", C["fgt"])])
    style.configure("Treeview.Heading", background=C["bgc"], foreground=C["fgm"],
                    font=FONT_BUTTON, relief="flat")
    style.map("Treeview.Heading", background=[("active", C["acl"])])

    # === Cards ===
    style.configure("Card.TFrame", background=C["bgc"], relief="solid", borderwidth=1)
    style.configure("MainBg.TFrame", background=C["bg"])

    # === Labels ===
    style.configure("Title.TLabel", background=C["bg"], foreground=C["fgt"], font=FONT_TITLE)
    style.configure("Body.TLabel", background=C["bgc"], foreground=C["fgb"], font=FONT_BODY)
    style.configure("Muted.TLabel", background=C["bgc"], foreground=C["fgm"], font=FONT_SMALL)
    style.configure("Status.TLabel", background=C["bg"], foreground=C["fgm"], font=FONT_SMALL)

    # === Combobox - compact ===
    style.configure("TCombobox", fieldbackground=C["bgc"], background=C["bgc"],
                    foreground=C["fgb"], font=FONT_BODY, arrowsize=12)
    style.map("TCombobox", fieldbackground=[("readonly", C["bgc"])],
              background=[("readonly", C["bgc"])])

    # ─ Buttons (ttk) - compact ===
    style.configure("Action.TButton", background=C["ac"], foreground="white",
                    borderwidth=0, focuscolor="none", font=FONT_BUTTON, padding=(10,4))
    style.map("Action.TButton",
              background=[("active", C["ach"]), ("disabled", C["fgm"])])
    style.configure("Success.TButton", background=C["sc"], foreground="white",
                    borderwidth=0, focuscolor="none", font=FONT_BUTTON, padding=(10,4))
    style.map("Success.TButton", background=[("active", _darken(C["sc"]))])
    style.configure("Danger.TButton", background=C["dg"], foreground="white",
                    borderwidth=0, focuscolor="none", font=FONT_BUTTON, padding=(10,4))
    style.map("Danger.TButton", background=[("active", _darken(C["dg"]))])
    style.configure("Warning.TButton", background=C["wn"], foreground="white",
                    borderwidth=0, focuscolor="none", font=FONT_BUTTON, padding=(10,4))
    style.map("Warning.TButton", background=[("active", _darken(C["wn"]))])

    # === Treeview / Table - compact ===
    style.configure("Treeview", font=FONT_BODY, rowheight=24,
                    background=C["bgc"], foreground=C["fgb"],
                    fieldbackground=C["bgc"], borderwidth=0)
    style.configure("Treeview.Heading",
                    font=FONT_SMALL_BOLD,
                    background=C["ac"], foreground="white", relief="flat", borderwidth=0)
    style.map("Treeview",
              background=[("selected", C["acl"])],
              foreground=[("selected", C["fgt"])])

    # === Progressbar - compact ===
    style.configure("Horizontal.TProgressbar", background=C["sc"],
                    troughcolor=C["bg"], borderwidth=0, thickness=5)
    style.configure("Success.Horizontal.TProgressbar", background=C["sc"],
                    troughcolor=C["acl"], borderwidth=0, thickness=5)


# === Logging bridge ===
_tlog = None

# === Log sink mirror (NetLink) ===
_log_sinks = []   # [(sink_fn,), ...] 由 netlink 注册


def register_log_sink(fn):
    """注册日志镜像回调 fn(msg, tag, level, caller_file, caller_func)。重复注册只登记一次。"""
    if fn not in _log_sinks:
        _log_sinks.append(fn)


def unregister_log_sink(fn):
    global _log_sinks
    _log_sinks = [f for f in _log_sinks if f is not fn]


def _broadcast_log(msg, tag, level, caller_file="", caller_func=""):
    """把一条日志镜像给所有 sink。总线未启动时零开销；任何 sink 抛错静默忽略。"""
    if not _log_sinks:
        return
    for fn in list(_log_sinks):
        try:
            fn(msg, tag, level, caller_file, caller_func)
        except Exception:
            pass

def set_tlog(instance):
    global _tlog; _tlog = instance

def log1(msg, tag=None, level=None):
    """Thread-safe logging with automatic caller info (file + function name).

    Args:
        msg: Log message text
        tag: Optional tkinter Text widget tag for GUI coloring
             Also auto-maps to level: "error"→ERROR, "warning"→WARNING, "info"→INFO, "debug"→DEBUG
        level: Log level override — "DEBUG"/"INFO"/"WARNING"/"ERROR" or int 0-3.
               If None, auto-detected from tag. Defaults to INFO.
    """
    if _tlog:
        # ── Auto-detect level from tag if not explicitly provided ──
        if level is None:
            tag_lower = str(tag).lower() if tag else ""
            if tag_lower == "error":
                level = LOG_ERROR
            elif tag_lower == "warning":
                level = LOG_WARNING
            elif tag_lower == "info":
                level = LOG_INFO
            elif tag_lower == "debug":
                level = LOG_DEBUG
            else:
                level = LOG_INFO
        elif isinstance(level, str):
            level_map = {"DEBUG": LOG_DEBUG, "INFO": LOG_INFO,
                         "WARNING": LOG_WARNING, "ERROR": LOG_ERROR}
            level = level_map.get(level.upper(), LOG_INFO)

        # ── Capture caller's filename and function name ──
        import inspect
        caller_file = ""
        caller_func = ""
        try:
            frame = inspect.currentframe()
            # Go up 1 frame to get the actual caller of log1
            caller = frame.f_back
            if caller is not None:
                caller_file = os.path.basename(caller.f_code.co_filename)
                caller_func = caller.f_code.co_name
        except Exception:
            pass
        _tlog.put(msg, tag, caller_file, caller_func, level)


class ThreadSafeLog:
    """
    Thread-safe logging with batched file writes (P0 optimization #5).
    
    Batches logs to reduce I/O operations by ~80%.
    Writes when buffer reaches BATCH_SIZE or FLUSH_INTERVAL seconds.
    
    P1 Enhancement: Structured logging with LogLevel filtering, daily rotation,
    and automatic old log cleanup (  + LogSavePath).
    """
    # P0 Optimization #5: Batch configuration
    BATCH_SIZE = 10          # Write after accumulating N messages
    FLUSH_INTERVAL = 1.0     # Or write every N seconds
    
    def __init__(self, widget, log_dir=None):
        self._q = queue.Queue()
        self._w = widget
        self._buffer = []       # In-memory buffer for export
        
        # P0 Optimization #5: File write batching
        self._file_buffer = []
        self._last_flush = time.time()
        
        # P1 Enhancement: Daily log rotation
        self._log_dir = log_dir
        self._log_file = None
        self._current_date = ""  # Track date for daily rotation
        if log_dir:
            self._rotate_log_file()
            self._cleanup_old_logs()

    def put(self, msg, tag=None, caller_file="", caller_func="", level=LOG_INFO):
        """Add message to queue (thread-safe).
        
        Args:
            msg: Log message
            tag: Optional tkinter tag for GUI coloring
            caller_file: Source filename (auto-captured by log1)
            caller_func: Source function name (auto-captured by log1)
            level: Log level int (LOG_DEBUG=0, LOG_INFO=1, LOG_WARNING=2, LOG_ERROR=3)
        """
        _broadcast_log(msg, tag, level, caller_file, caller_func)
        self._q.put((msg, tag, caller_file, caller_func, level))

    def flush(self, root_widget):
        """Flush queue to GUI widget and batched file write with LogLevel filtering."""
        # P1 Enhancement: Check daily rotation on each flush cycle
        self._check_daily_rotation()

        while not self._q.empty():
            try:
                msg, tag, caller_file, caller_func, level = self._q.get_nowait()

                # Add to in-memory buffer for export
                self._buffer.append((msg, tag))

                # Update GUI widget immediately (always show all levels in GUI)
                s = self._w.index("end-1c")
                # UI Enhancement: GUI 日志加时间戳 (与文件格式对齐)，WARNING+ 带级别前缀
                level_name = _LOG_LEVEL_NAMES.get(level, "INFO")
                ts = time.strftime("%H:%M:%S")
                display_msg = "[{}] {}".format(ts, msg) if level < LOG_WARNING else \
                              "[{}] [{}] {}".format(ts, level_name, msg)
                self._w.insert("end", "{}\n".format(display_msg))
                if tag: self._w.tag_add(tag, s, "end-1c")
                self._w.update(); self._w.see(tkinter.END)

                # P1 Enhancement: File write with LogLevel filtering + structured format
                if self._log_file and self._check_log_saving_enabled():
                    # Check LogLevel filter
                    if level < self._get_min_log_level():
                        continue

                    # Structured format: [2026-07-20 11:13:48] INFO: message [file:function]
                    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
                    level_name = _LOG_LEVEL_NAMES.get(level, "INFO")
                    caller_info = ""
                    if caller_file and caller_func:
                        caller_info = " [{}:{}]".format(caller_file, caller_func)
                    elif caller_file:
                        caller_info = " [{}]".format(caller_file)
                    self._file_buffer.append(
                        "[{}] {}: {}{}\n".format(timestamp, level_name, msg, caller_info))

                    # Check if we should flush to disk
                    now = time.time()
                    if (len(self._file_buffer) >= self.BATCH_SIZE or
                        now - self._last_flush >= self.FLUSH_INTERVAL):
                        self._flush_to_disk()

            except queue.Empty: break

    def _flush_to_disk(self):
        """Write buffered logs to disk (batched I/O)."""
        if not self._file_buffer or not self._log_file:
            return

        try:
            # Ensure log directory exists
            log_dir = os.path.dirname(self._log_file)
            if log_dir and not os.path.exists(log_dir):
                os.makedirs(log_dir, exist_ok=True)

            # Append all buffered messages at once
            with open(self._log_file, "a", encoding="utf-8") as f:
                f.writelines(self._file_buffer)

            # Clear buffer and update timestamp
            self._file_buffer.clear()
            self._last_flush = time.time()
        except Exception:
            pass  # Silently ignore file write errors

    # ── P1 Enhancement: Daily log rotation & retention ──

    def _get_log_filename(self):
        """Get daily log filename: acrpa_YYYYMMDD.log."""
        return os.path.join(self._log_dir,
            "acrpa_{}.log".format(time.strftime("%Y%m%d")))

    def _rotate_log_file(self):
        """Switch to today's log file (create if not exists)."""
        if not self._log_dir:
            return
        new_file = self._get_log_filename()
        today_str = time.strftime("%Y%m%d")
        if self._log_file != new_file or self._current_date != today_str:
            # Flush any pending writes to old file before switching
            # 注意: 不能调 force_flush() (会再次触发 _check_daily_rotation → _rotate_log_file 无限递归)
            self._flush_to_disk()
            self._log_file = new_file
            self._current_date = today_str

    def _check_daily_rotation(self):
        """Check if date has changed and rotate log file if needed."""
        today_str = time.strftime("%Y%m%d")
        if self._current_date != today_str:
            self._rotate_log_file()
            self._cleanup_old_logs()

    def _cleanup_old_logs(self):
        """Delete log files older than LOG_RETENTION_DAYS."""
        if not self._log_dir:
            return
        try:
            retention_days = getattr(state, 'LOG_RETENTION_DAYS', 7)
            if retention_days <= 0:
                return  # 0 or negative means keep forever

            cutoff = time.time() - (retention_days * 86400)
            pattern = os.path.join(self._log_dir, "acrpa_*.log")
            for log_path in glob.glob(pattern):
                try:
                    if os.path.getmtime(log_path) < cutoff:
                        os.remove(log_path)
                except Exception:
                    pass
        except Exception:
            pass  # Cleanup failure is non-critical

    def _check_log_saving_enabled(self):
        """Check if log file saving is enabled in config."""
        try:
            return getattr(state, 'ENABLE_LOG_SAVING', True)
        except Exception:
            return True  # Default to enabled if state unavailable

    def _get_min_log_level(self):
        """Get the minimum log level to write to file from config."""
        try:
            return getattr(state, 'LOG_LEVEL', LOG_INFO)
        except Exception:
            return LOG_INFO

    def force_flush(self):
        """Force immediate flush of all pending logs to disk."""
        self._check_daily_rotation()
        self._flush_to_disk()

    def export(self, filepath):
        """Export all logged messages to a file."""
        # First flush any pending writes
        self.force_flush()
        
        # Then export the in-memory buffer
        with open(filepath, "w", encoding="utf-8") as f:
            for msg, tag in self._buffer:
                f.write(msg + "\n")

    def clear_buffer(self): 
        """Clear in-memory buffer (but keep file logs)."""
        self._buffer = []
        self._file_buffer = []
        self._last_flush = time.time()


# ═══════════════════════════════════════════════════════════════════════
# 共享尺寸令牌 (设计 px) — 供脚本市场窗口与现有组件复用
# (docs/marketplace-v2-design.md §2.7)
#
# 尺度口径 (须统一)：
#   * 字体走 fit_pt()  —— 不含 dpi_factor (Tk 的 `tk scaling` 已处理 pt→px)；
#   * sp()  走 scaled() —— 含 dpi_factor * ui_scale。
#   留白若与文字行高强相关，优先用 gap/ctrl_h (走 scaled) 保持一致；
#   命中尺寸/图标用 icon_size/tk_px (走 scaled)。
#
# 纯新增：不修改 scaled/fit_pt/PAD/PI/create_card/_btn/themed 的任何现有语义。
# ═══════════════════════════════════════════════════════════════════════

TOKENS = {
    "sp_xs": 4, "sp_sm": 8, "sp_md": 12, "sp_lg": 16, "sp_xl": 24,
    "radius": 6, "radius_sm": 4, "radius_lg": 10,
    "ctrl_h": 26, "ctrl_h_sm": 22, "ctrl_h_lg": 30,
    "gap": 8, "gap_tight": 4, "card_pad": 10,
    "icon_sm": 16, "icon_md": 24, "icon_lg": 40,
    "card_min_h": 84, "bar_h": 4,
}


def tk_px(value):
    """设计 px → 实际 px = scaled(value) (含 dpi_factor * ui_scale)。"""
    return scaled(value)


def sp(key_or_px):
    """间距/尺寸访问器：键名命中 TOKENS → scaled(TOKENS[key])；数字 → scaled(n)。

    未知键名按 0 处理 (scaled 下限 1)，不抛异常，便于 UI 侧容错迭代。
    """
    if isinstance(key_or_px, str):
        return scaled(TOKENS.get(key_or_px, 0))
    return scaled(key_or_px)


def radius(token="radius"):
    """圆角半径 (默认 6 设计 px)。"""
    return sp(token)


def ctrl_h(token="ctrl_h"):
    """控件高度 (默认 26 设计 px)。"""
    return sp(token)


def gap(token="gap"):
    """通用间距 (默认 8 设计 px)。"""
    return sp(token)


def icon_size(token="icon_md"):
    """图标边长 (默认 24 设计 px)。"""
    return sp(token)


# ═══════════════════════════════════════════════════════════════════════
# 窗口几何工具 (主窗口 / 设置窗口复用): 虚拟屏取值 + 越界校验 + 居中派生
# 抽取自 ACRPA._main_win_bounds / _main_geometry_in_screen / _main_center_geometry,
# 行为完全等价, 供主窗口启动几何与设置窗口记忆几何共用同一套判定。
# ═══════════════════════════════════════════════════════════════════════

def win_virtual_bounds(root):
    """返回虚拟屏 (vx, vy, vw, vh); 不支持虚拟屏时回退主屏 (0, 0, 屏宽, 屏高)。"""
    try:
        vx, vy = root.winfo_vrootx(), root.winfo_vrooty()
        vw, vh = root.winfo_vrootwidth(), root.winfo_vrootheight()
        if vw > 0 and vh > 0:
            return vx, vy, vw, vh
    except Exception:
        pass
    return 0, 0, root.winfo_screenwidth(), root.winfo_screenheight()


def geometry_in_screen(root, geo):
    """校验 "WxH+X+Y" 是否落在当前虚拟屏内 (至少标题栏与 ≥40px 可见)。

    geo 为空/格式不符/尺寸过小 (w<200 或 h<150) 一律返回 False。
    """
    m = re.match(r"^(\d+)x(\d+)([+-]\d+)([+-]\d+)$", geo or "")
    if not m:
        return False
    w, h, x, y = (int(m.group(i)) for i in range(1, 5))
    if w < 200 or h < 150:
        return False
    vx, vy, vw, vh = win_virtual_bounds(root)
    if (x + w) < (vx + 40) or x > (vx + vw - 40):
        return False
    if (y + 40) < vy or y > (vy + vh - 40):
        return False
    return True


def center_geometry(root, w, h):
    """把 w×h 居中到虚拟屏 (纵向略偏上 1/3), 并按虚拟屏夹取上限。"""
    vx, vy, vw, vh = win_virtual_bounds(root)
    w = min(w, max(200, vw - 40)); h = min(h, max(150, vh - 40))
    x = vx + max(0, (vw - w) // 2)
    y = vy + max(0, (vh - h) // 3)
    return "{}x{}+{}+{}".format(w, h, x, y)
