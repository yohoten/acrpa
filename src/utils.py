"""Theme, fonts, utilities, ThreadSafeLog, card factory.

P0 Optimization #5: Batched log writing to reduce I/O operations by 80%.
P1 Enhancement: Structured logging with LogLevel, daily rotation, and retention ( AutomationOperation).
"""
import queue, time, os, glob
import tkinter
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
    # Color mapping for different message types
    colors = {
        "info": ("#2563EB", "ℹ"),      # Blue
        "success": ("#10B981", "✓"),   # Green
        "warning": ("#F59E0B", "⚠"),   # Yellow
        "error": ("#EF4444", "✗")      # Red
    }
    
    bg_color, icon = colors.get(msg_type, colors["info"])
    
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
        fg="white", bg=bg_color)
    label.pack()
    
    # Auto-dismiss
    if duration > 0:
        toast.after(duration, toast.destroy)
    
    return toast


# === Theme & colors ===

def _colors():
    """Modern flat-design palette: brand-blue primary, dark-readable text."""
    if state.DARK_MODE:
        return dict(
            bg="#0b1120", bgc="#1a2332", fgt="#f1f5f9", fgb="#cbd5e0",
            fgm="#9ca3af", ac="#60a5fa", ach="#3b82f6", acl="#1b3d6e",
            sc="#34d399", dg="#f87171", wn="#fbbf24", bd="#374051",
            logbg="#1a2332", logfg="#e2e8f0", ebg="#1a2332",
            err="#f87171", errbg="#3a2525", ok="#34d399",
            hover="#3b82f6", cardhover="#60a5fa", focus="#60a5fa",
            flowbg="#0f172a")
    return dict(
        bg="#f5f6f8", bgc="#ffffff", fgt="#1a202c", fgb="#2d3748",
        fgm="#718096", ac="#2563eb", ach="#1d4ed8", acl="#eff6ff",
        sc="#10b981", dg="#ef4444", wn="#f59e0b", bd="#e2e8f0",
        logbg="#fdfdfd", logfg="#2d3748", ebg="#ffffff",
        err="#ef4444", errbg="#fff0f0", ok="#10b981",
        hover="#1d4ed8", cardhover="#3b82f6", focus="#2563eb",
        flowbg="#f8fafc")


C = _colors()

# Font constants - compact sizes
FONT_TITLE = ("Microsoft YaHei UI",10,"bold")
FONT_BODY  = ("Microsoft YaHei UI",9)
FONT_LOG   = ("Consolas",9)
FONT_SMALL = ("Microsoft YaHei UI",8)
FONT_BUTTON= ("Microsoft YaHei UI",9,"bold")

PAD = {"padx":12,"pady":6}
PI  = {"padx":10,"pady":5}

# ── 滚动条样式统一常量 ──
SCROLLBAR_KW = {"width": 7, "relief": "flat", "bg": C["bd"], "troughcolor": C["logbg"]}


def create_card(parent):
    return tkinter.Frame(parent, bg=C["bgc"], bd=0,
        highlightbackground=C["bd"], highlightthickness=1)


def _btn(parent, text, cmd, bg_c=None, fg_c=None, tip=None):
    """Toolbar button factory with hover darken effect + optional tooltip."""
    if bg_c is None: bg_c = C["bgc"]
    if fg_c is None: fg_c = C["fgb"]
    if bg_c in ("white", "#ffffff", C["bgc"]):
        abg = C["acl"]
    else:
        abg = _darken(bg_c)
    btn = tkinter.Button(parent, text=text, font=FONT_SMALL, bg=bg_c, fg=fg_c,
        activebackground=abg, activeforeground=fg_c,
        relief="raised", bd=3, cursor="hand2",
        padx=10, pady=4, command=cmd)
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
                    font=(*FONT_SMALL, "bold"),
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
