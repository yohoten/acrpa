"""Theme, fonts, utilities, ThreadSafeLog, card factory.

P0 Optimization #5: Batched log writing to reduce I/O operations by 80%.
P1 Enhancement: Structured logging with LogLevel, daily rotation, and retention ( AutomationOperation).
"""
import queue, time, os, glob, re, shutil
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

# ── 日志面板级别徽标 (唯一权威: docs/UI美化设计方案.md §2.5) ──
# 面板四级 = INFO / SUCCESS / WARNING / ERROR; 徽标文案**由 tag 判定**
# (log1(msg,"success") 的 int 级别实为 LOG_INFO, 但徽标须为 [SUCCESS]),
# tag 缺失/未知时回退到 level int。SUGGEST 不存在, 不得引入。
_LOG_BADGE_BY_TAG = {
    "info": "INFO",
    "success": "SUCCESS",
    "warning": "WARNING",
    "error": "ERROR",
}


def _log_badge(tag, level):
    """按 tag 决定日志级别徽标文案 (§2.5: tag 优先, 其次 level int)。"""
    key = str(tag).lower() if tag else ""
    if key in _LOG_BADGE_BY_TAG:
        return _LOG_BADGE_BY_TAG[key]
    return _LOG_LEVEL_NAMES.get(level, "INFO")


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
    # §3.1 Emoji → Segoe UI Symbol 单色码位: ℹ→ⓘ, ✓→✔, ✗→✘, ⚠ 去 VS16 保留 ⚠
    icon_map = {"info": "ⓘ", "success": "✔", "warning": "⚠", "error": "✘"}
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
    """主题色板 (light/dark 两套) —— **兼容委托**。

    实现已于阶段二第 2 项 (`ui/theme.py` + `ThemeBus`) 迁至 `ui.theme.colors`,
    本函数保留为委托, 既有 `from utils import _colors` / `_colors()` 调用点
    (含 tools 自测与 utils.C 初始化) 无需改动。
    """
    from ui import theme
    return theme.colors()

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


# ── UI 缩放切换钩子 ──
# set_ui_scale() 只重配命名字体; 依赖像素尺寸重建的资源 (如 ACRPA 的选中左缘条
# PhotoImage, 高度按 row_h 构造) 必须随之重建 → 由使用方注册回调, 避免跨模块引用私有名。
_ui_scale_hooks = []


def register_ui_scale_hook(fn):
    """注册「缩放切换后」回调 (在 set_ui_scale 重配字体后调用)。重复注册只登记一次。"""
    if fn not in _ui_scale_hooks:
        _ui_scale_hooks.append(fn)


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
    # 缩放切换后通知已注册的资源重建回调 (如选中左缘条 PhotoImage), 失败不影响缩放本身
    for _fn in list(_ui_scale_hooks):
        try:
            _fn()
        except Exception:
            pass
    return v

PAD = {"padx":12,"pady":6}
PI  = {"padx":10,"pady":5}

# ── 滚动条样式统一常量 ──
SCROLLBAR_KW = {"width": 7, "relief": "flat", "bg": C["bd"], "troughcolor": C["logbg"]}


def create_card(parent):
    return tkinter.Frame(parent, bg=C["bgc"], bd=0,
        highlightbackground=C["bd"], highlightthickness=1)

def _btn(parent, text, cmd, bg_c=None, fg_c=None, tip=None):
    """Toolbar button factory (经典扁平 + hover 底色/描边双变化) + optional tooltip.

    扁平化: 取消旧 `bd=3` 浮雕, 改 `bd=0` + 1px highlight 发丝描边
    (docs/UI美化设计方案.md §3.2 合法边框集合 {bd=0, bd=1, 1px highlightthickness});
    hover 不再仅 `_darken()` 变暗, 改「底色 caption_hover + 描边 border_strong」双变化 (§4.9.2)。
    内边距走 scaled() 令牌, 随 dpi_factor * ui_scale 缩放, 避免大字号下串行。

    bg_c 可传 **色板键名** (如 "ac"/"sc"/"dg"/"wn"): 一次完成「取当前主题色」+
    「登记语义角色」(ui.theme.set_role) —— 换肤时按角色回填, 不再靠文案/旧值推断
    (路线图 §5.2 修法②)。传字面颜色 ("white"/"#7C3AED") 则视为中性/品牌固定色。
    """
    from ui import theme as _theme
    if bg_c is None:
        bg_c, _role = C["bgc"], None
    else:
        bg_c, _role = _theme.resolve_bg(bg_c, C)
    if fg_c is None: fg_c = C["fgb"]
    # hover 底色分三类 (§4.9.2): 中性底 → caption_hover; 强调底 → ach;
    #   语义色底 (sc/dg/wn) → 自加深, 不得再用中性灰 hover 造成串色。
    _neutral = bg_c in ("white", "#ffffff", C["bgc"], C["surface_alt"])
    if _neutral:
        _hover_bg, _hover_bd = C["caption_hover"], C["border_strong"]
    elif bg_c in (C["ac"], C["ach"], C["hover"], C["focus"], C["cardhover"]):
        _hover_bg, _hover_bd = C["ach"], C["ach"]
    else:
        _hover_bg, _hover_bd = _darken(bg_c), _darken(bg_c)
    btn = tkinter.Button(parent, text=text, font=FONT_SMALL, bg=bg_c, fg=fg_c,
        activebackground=_hover_bg, activeforeground=fg_c,
        relief="flat", bd=0, highlightthickness=1,
        highlightbackground=C["bd"], highlightcolor=C["focus"],
        takefocus=True, cursor="hand2",
        padx=scaled(10), pady=scaled(4), command=cmd)
    if tip:
        attach_tooltip(btn, tip)
    # 语义角色登记: ThemeBus.publish 换肤时按角色回填底色, 不再靠文案/旧值反推
    if _role:
        try:
            _theme.set_role(btn, _role)
        except Exception:
            pass
    # hover: 「底色 + 描边」双变化 (非仅变暗); 必须后于 attach_tooltip 绑定
    # (其 <Enter>/<Leave> 未用 add, 会覆盖先绑的回调)。
    def _on_enter(_e):
        try:
            btn.configure(bg=_hover_bg, highlightbackground=_hover_bd)
        except Exception:
            pass
    def _on_leave(_e):
        try:
            btn.configure(bg=bg_c, highlightbackground=C["bd"])
        except Exception:
            pass
    btn.bind("<Enter>", _on_enter, add="+")
    btn.bind("<Leave>", _on_leave, add="+")
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
        # §4.8: tooltip = 经典浅黄 bg + 1px tooltip_border 描边 (统一走规范 token)
        tkinter.Label(_tip_win, text=txt, font=FONT_SMALL,
            bg=C["tooltip_bg"], fg=C["fgb"], relief="flat", bd=0,
            highlightthickness=1, highlightbackground=C["tooltip_border"],
            padx=6, pady=2).pack()
    except Exception:
        pass

def attach_tooltip(widget, text):
    """为控件绑定悬停提示。text 可为字符串或返回字符串的可调用对象(动态提示)。"""
    getter = text if callable(text) else (lambda t=text: t)
    widget.bind("<Enter>", lambda e: _show_tooltip(e, getter))
    widget.bind("<Leave>", _hide_tooltip)


def bind_sel_bold(tree_widget, font=None):
    """§4.5 降级: 对无 #0 列的 `show="headings"` 树, 选中行加粗 (FONT_SMALL_BOLD)。

    ttk.Treeview 无 per-row 边框/贴图能力 → 无法挂「选中左缘 2px ac 竖条」;
    按文档 §4.5 降级为「选中行整行 acl 底 + 加粗」(整行 acl 底由 apply_theme 的
    style.map 提供)。供 dialogs / netlink_window 等非 ACRPA 模块复用 (与
    ACRPA._bind_sel_bold 等价, 但不跨文件引用私有名)。
    """
    try:
        tree_widget.tag_configure("selbold", font=font or FONT_SMALL_BOLD)
    except Exception:
        pass

    def _on(_e=None):
        try:
            for it in tree_widget.get_children(""):
                tgs = tuple(t for t in tree_widget.item(it, "tags") if t != "selbold")
                tree_widget.item(it, tags=tgs)
            for it in tree_widget.selection():
                tgs = tree_widget.item(it, "tags")
                if "selbold" not in tgs:
                    tree_widget.item(it, tags=tuple(tgs) + ("selbold",))
        except Exception:
            pass

    try:
        tree_widget.bind("<<TreeviewSelect>>", _on, add=True)
    except Exception:
        pass


def apply_theme(root_widget, style):
    """应用当前主题到 ttk 样式与 root —— **兼容委托**。

    实现已于阶段二第 2 项迁至 `src/ui/theme.py:apply_theme`; 本函数保留为委托,
    既有 `utils.apply_theme(root, style)` 调用点无需改动。
    注意: 迁移后「重绑 utils.C」的行为保持不变 (由 ui.theme.apply_theme 完成)。
    """
    from ui import theme
    return theme.apply_theme(root_widget, style)

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
                # UI Enhancement: GUI 日志 = "[HH:MM:SS] [LEVEL] msg"。
                # §2.5: 四级 (INFO/SUCCESS/WARNING/ERROR) **均**带级别徽标, 且徽标**由 tag 判定**
                # (log1(msg,"success") 的 int 级别实为 LOG_INFO, 但徽标须为 [SUCCESS])。
                ts = time.strftime("%H:%M:%S")
                badge = _log_badge(tag, level)
                display_msg = "[{}] [{}] {}".format(ts, badge, msg)
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
    # 尺度基线 (docs/UI美化设计方案.md §3): ctrl_h 26→24 / ctrl_h_lg 30→28
    "ctrl_h": 24, "ctrl_h_sm": 22, "ctrl_h_lg": 28,
    "gap": 8, "gap_tight": 4, "card_pad": 10,
    "icon_sm": 16, "icon_md": 24, "icon_lg": 40,
    # 数据表行高唯一定值 22 (消除 22/24 冲突); 发丝线 / 焦点环 / sash (§3 / §6.1)
    "row_h": 22, "hairline": 1, "focus_w": 2, "sash": 4,
    # bar_h: 文档 §3 建议 10, 但该键当前被市场窗口「分类色条」宽度消费
    # (market_window.py:600 sp("bar_h")), 改动会影响既有布局几何 → 保持原值 4 (见结果偏差说明)。
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
    """控件高度 (默认 24 设计 px, docs/UI美化设计方案.md §3)。"""
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


# ═══════════════════════════════════════════════════════════════════════
# 版本号解析 / 比较 —— 唯一权威实现 (P0-3「版本比较统一」)
# src/updater.py 与 src/script_package.py 均委托此处, 消除两处重复实现。
# 约束: 本模块保持零第三方依赖, 且不得反向 import updater / script_package
#       (utils 是共享低层模块, updater 依赖 requests 且可能反向引用)。
# ═══════════════════════════════════════════════════════════════════════

def parse_version(value):
    """解析版本号 → (数值元组, 是否预发布, 预发布键)；无法解析返回 None。

    兼容写法: "v1.2.3" / "0.1.26-beta" / "0.1.26beta" / "0.1.26_rc1" /
    "1.0.0-rc.1"。预发布键按段生成 —— 数字段按数值、字母段按字典序,
    用 (0, int) / (1, str) 打标签, 避免跨类型比较抛异常。
    """
    raw = str(value or "").strip().lstrip("vV")
    if not raw:
        return None

    core, sep, pre = raw.partition("-")
    if not sep:
        # 兼容 0.1.26beta / 0.1.26_rc1 等写法
        i = 0
        while i < len(core) and (core[i].isdigit() or core[i] == "."):
            i += 1
        core, pre = core[:i], core[i:].lstrip("._-")
    else:
        pre = pre.lstrip("._-")

    parts = [p for p in core.split(".") if p != ""]
    if not parts:
        return None
    try:
        nums = tuple(int(p) for p in parts)
    except ValueError:
        return None

    key = tuple((0, int(p)) if p.isdigit() else (1, p.lower())
                for p in pre.replace("-", ".").replace("_", ".").split(".")
                if p)
    return nums, bool(pre), key


def compare_versions(current, latest):
    """比较两个版本号 → -1 (current 更旧) / 0 (相同) / 1 (current 更新)。

    无法解析任一侧时返回 0, 由调用方决定如何处理 (不擅自判定为新版本)。
    """
    a = parse_version(current)
    b = parse_version(latest)
    if a is None or b is None:
        return 0

    na, pb_a, key_a = a
    nb, pb_b, key_b = b
    width = max(len(na), len(nb))
    na = na + (0,) * (width - len(na))
    nb = nb + (0,) * (width - len(nb))

    if na != nb:
        return -1 if na < nb else 1
    # 数值相同: 正式版 > 预发布版 (1.0.0 > 1.0.0-rc1)
    if pb_a != pb_b:
        return 1 if pb_b else -1
    if key_a != key_b:
        return -1 if key_a < key_b else 1
    return 0


def version_tuple(value):
    """语义化版本 → 数值元组 (major, minor, patch, ...)。

    成功解析时返回真实数值部分 (预发布后缀被正确剥离):
    "0.1.29-beta" → (0, 1, 29)。无法解析 / 空输入返回 () 且不抛异常。
    """
    parsed = parse_version(value)
    if parsed is None:
        return ()
    return parsed[0]


def version_gt(a, b):
    """版本 a > b 返回 True。

    任一侧不可解析时 compare_versions 返回 0, 因此返回 False ——
    即不把不可解析的输入误判为「远端有新版本」。
    """
    return compare_versions(a, b) > 0


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
# 说明: 本模块已被测试 tools/_test_download_name_sanitize.py 直接覆盖(无重依赖)。

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

    示例:
        safe_filename("..\\..\\evil.bat")        -> "evil.bat"
        safe_filename("C:\\Windows\\x.txt")      -> "x.txt"
        safe_filename("/etc/passwd")             -> "passwd"
        safe_filename("CON")                     -> "_CON"
        safe_filename("report.xlsx")             -> "report.xlsx"  (不变)
    """
    clean = _sanitize_segment(name)
    if clean:
        return clean
    fallback = _sanitize_segment(default)
    return fallback or "download"
