"""Theme, fonts, utilities, ThreadSafeLog, card factory.

P0 Optimization #5: Batched log writing to reduce I/O operations by 80%.
P1 Enhancement: Structured logging with LogLevel, daily rotation, and retention ( AutomationOperation).
"""
import queue, sys, time, os, glob, re, shutil
import tkinter
import tkinter.font
import state

# ── 领域层基础件 re-export (原实现已迁至 src/core/*, 见 docs/项目结构优化建议.md §3.1) ──
# 保持既有 `from utils import log1 / _safe_get / atomic_save / safe_filename` 调用点不变。
from core.log import (
    log1, set_tlog, get_tlog, register_log_sink, unregister_log_sink,
    _broadcast_log,
    ThreadSafeLog, LOG_DEBUG, LOG_INFO, LOG_WARNING, LOG_ERROR,
)
from core.util import _safe_get
from core.fs import atomic_save, atomic_write_bytes, safe_filename
from core.version import parse_version, compare_versions, version_tuple, version_gt
# 颜色运算真源已迁至 core.color (P1-1); 此处 re-export 保持既有
# `from utils import _darken / text_on / rel_luminance` 调用点不变。
from core.color import (  # noqa: E402
    darken as _darken, lighten, blend, transition, contrast,
    rel_luminance, text_on, hex_to_rgb, rgb_to_hex, rgb_to_hsl, hsl_to_rgb,
)
# 缓动控制器真源 (P2-1): ease() 与 core.animation.Animation 共表, 避免两套实现。
# Animation 供 show_toast 淡入淡出 (P2-3) 复用同一套 after 调度与 reduce_motion 降级。
from core.animation import CONTROLLERS as _EASE, out_cubic, Animation  # noqa: E402


def restyle_combobox_popdown(widget, colors=None):
    """重刷 ttk.Combobox 的下拉弹窗配色。

    下拉列表是原生 `tkinter.Listbox` (路径 `<combobox>.popdown.f.l`), 不参与 ttk 样式,
    只能单独 configure; 否则暗色主题下弹出层仍是系统白底黑字。
    """
    try:
        C = colors or _colors()
        path = widget.tk.call("ttk::combobox::PopdownWindow", str(widget))
        lb = widget.nametowidget(str(path) + ".f.l")
        lb.configure(background=C["bgc"], foreground=C["fgb"],
                     selectbackground=C["acl"], selectforeground=C["fgt"],
                     font=FONT_BODY)
        # §7: 下拉最大高 ≈240px (约 10 行), 长列表不再撑满屏幕遮挡下方控件;
        # 靠近屏幕底部时 Tk 原生 popdown 会自动向上翻转, 无需自绘。
        try:
            if int(lb.cget("height")) > 10:
                lb.configure(height=10)
        except Exception:
            pass
    except Exception:
        pass


# === Toast Notification System ===

# 复用单例提示条 (Toplevel 创建/销毁成本高, 提示又是高频反馈)
# P2-3: fade_in / fade_out 为淡入/淡出 Animation 句柄, 再次提示时 stop() 清残留定时器。
_toast = {"win": None, "frame": None, "label": None, "job": None,
          "fade_in": None, "fade_out": None}


def _toast_fps():
    """动效帧率 (fps) —— 帧间隔 motion_frame()≈16ms → 约 60fps。"""
    frame = motion_frame()
    return max(1, 1000 // max(1, int(frame)))


def _toast_cancel_anim():
    """停止淡入/淡出动画 + 取消自动隐藏 job (再次提示前调用, 清残留定时器)。"""
    win = _toast.get("win")
    job = _toast.get("job")
    if job is not None:
        try:
            if win is not None:
                win.after_cancel(job)
        except Exception:
            pass
        _toast["job"] = None
    for key in ("fade_in", "fade_out"):
        anim = _toast.get(key)
        if anim is not None:
            try:
                anim.stop()
            except Exception:
                pass
            _toast[key] = None


def _toast_apply(win, x, y0, p, a0, a1, dy0, dy1):
    """单帧应用: 透明度 a0→a1、y 位移 dy0→dy1 (以 y0 为基准, 进度 p∈[0,1])。"""
    try:
        p = float(p)
        a = a0 + (a1 - a0) * p
        win.attributes("-alpha", max(0.0, min(1.0, a)))
        win.geometry("+{}+{}".format(x, y0 + int(dy0 + (dy1 - dy0) * p)))
    except Exception:
        pass


def show_toast(root, message, msg_type="info", duration=2000):
    """
    Display a toast notification.

    Args:
        root: Tkinter root window reference
        message: Text to display
        msg_type: "info", "success", "warning", or "error"
        duration: Auto-dismiss time in ms (0 = no auto-dismiss)

    P2-3: 淡入 + 停留 + 淡出用 core.animation.Animation 驱动 (透明度 + 位移),
    帧间隔 motion_frame()、时长 motion_fast; reduce_motion() 时瞬显即隐 (跳过插值)。
    """
    # 主题化配色：C 会被 apply_theme() 重绑，故函数内实时取色，禁止模块级写死
    # §3.1 符号统一: 用 Segoe UI Symbol 单色码位 ⓘ/✔/✘/⚠ (全角粗体, 非彩色 emoji)
    icon_map = {"info": "ⓘ", "success": "✔", "warning": "⚠", "error": "✘"}
    bg_map = {"info": C["ac"], "success": C["sc"], "warning": C["wn"], "error": C["dg"]}
    bg_color = bg_map.get(msg_type, bg_map["info"])
    # 语义色跨主题明度翻转 (暗色 sc=#6CCB5F / wn=#FCE100 偏亮) → 字色按亮度自适应,
    # 不再写死白字 (暗色模式下白字在浅底上对比不足)。
    fg_color = text_on(bg_color)
    icon = icon_map.get(msg_type, icon_map["info"])

    _toast_cancel_anim()

    # 复用单例窗口: 每次提示都新建 Toplevel 会有明显的创建/销毁开销 (Windows 上
    # 数十 ms 且伴随一次映射闪烁), 而提示条是高频 UI 反馈 (保存/撤销/复制/校验)。
    # 这里保留一个隐藏的 Toplevel + Frame + Label, 再次提示只改文本/配色/位置。
    win = _toast["win"]
    try:
        alive = win is not None and bool(win.winfo_exists())
    except Exception:
        alive = False
    if not alive:
        win = tkinter.Toplevel(root)
        win.overrideredirect(True)          # No window decorations
        win.attributes("-topmost", True)
        win.attributes("-alpha", 0.0)
        content = tkinter.Frame(win)
        content.pack(padx=16, pady=10)
        label = tkinter.Label(content, font=FONT_BUTTON)
        label.pack()
        _toast.update({"win": win, "frame": content, "label": label, "job": None})
    else:
        content, label = _toast["frame"], _toast["label"]

    # Calculate position (center-top of main window)
    try:
        x = root.winfo_x() + root.winfo_width() // 2 - 120
        y0 = root.winfo_y() + 60
    except Exception:
        x, y0 = 0, 60

    win.configure(bg=bg_color)
    content.configure(bg=bg_color)
    label.configure(text="{}  {}".format(icon, message), fg=fg_color, bg=bg_color)
    try:
        win.deiconify()
        win.lift()
        # withdraw/deiconify 后部分 Tk 版本会丢失置顶与透明度, 重新声明一次
        win.attributes("-topmost", True)
    except Exception:
        pass

    # 淡入: 透明度 0→0.95 + 自上而下位移 12px→0
    fade_ms = motion("motion_fast")
    fps = _toast_fps()
    win.attributes("-alpha", 0.0)
    win.geometry("+{}+{}".format(x, y0 + 12))
    fade_in = Animation(
        fade_ms,
        lambda p: _toast_apply(win, x, y0, p, 0.0, 0.95, 12, 0),
        controller=out_cubic, fps=fps, root=win, reduce_motion=reduce_motion)
    fade_in.start()
    _toast["fade_in"] = fade_in

    # 停留 + 淡出: duration 后 透明度 0.95→0 + 上移 0→-8, 结束 withdraw (复用不销毁)
    if duration > 0:
        def _begin_fade_out(w=win, xx=x, yy=y0):
            def _done():
                try:
                    w.withdraw()
                except Exception:
                    pass
            fade_out = Animation(
                fade_ms,
                lambda p: _toast_apply(w, xx, yy, p, 0.95, 0.0, 0, -8),
                controller=out_cubic, fps=fps, root=w,
                reduce_motion=reduce_motion, end=_done)
            fade_out.start()
            _toast["fade_out"] = fade_out
        _toast["job"] = win.after(duration, _begin_fade_out)

    return win


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
# 日志徽标块 (INFO/SUCCESS/...) 专用: 同字族+同字号, 仅字重加粗 —— 等宽字体加粗
# 不改变字宽, 徽标块列宽因此保持对齐 (视觉稿 A5: 级别徽标作色块, 需字形有分量)。
FONT_LOG_BOLD = "ACRPA_LOG_BOLD"
FONT_SMALL  = "ACRPA_SMALL"
FONT_BUTTON = "ACRPA_BUTTON"
FONT_SMALL_BOLD = "ACRPA_SMALL_BOLD"
FONT_TINY       = "ACRPA_TINY"
FONT_ICON       = "ACRPA_ICON"
FONT_ICON_MD    = "ACRPA_ICON_MD"
FONT_ICON_LG    = "ACRPA_ICON_LG"
# 配对码/验证码这类「等宽大号数字」专用角色 (原为 netlink 内联 ("Consolas",28,"bold"))
FONT_PIN        = "ACRPA_PIN"

# 字体角色表: 角色名 -> (字族, 基准 pt, 字重) —— 全项目唯一字体定义源
_FONT_SPECS = {
    "ACRPA_TITLE":      ("Microsoft YaHei UI", 10, "bold"),
    "ACRPA_BODY":       ("Microsoft YaHei UI", 9,  "normal"),
    # 密集表格可读下限 (路线图 §5.5): 全部正文字号不得低于 9pt。
    # ACRPA_SMALL / SMALL_BOLD 原为 8pt, ACRPA_TINY 原为 7pt —— 均低于下限,
    # 本批次统一提到 9pt (与 BODY/BUTTON/LOG 等值)。ICON_* 为符号字形, 不受此下限约束。
    "ACRPA_SMALL":      ("Microsoft YaHei UI", 9,  "normal"),
    "ACRPA_SMALL_BOLD": ("Microsoft YaHei UI", 9,  "bold"),
    "ACRPA_TINY":       ("Microsoft YaHei UI", 9,  "normal"),
    "ACRPA_BUTTON":     ("Microsoft YaHei UI", 9,  "bold"),
    "ACRPA_LOG":        ("Consolas",           9,  "normal"),
    "ACRPA_LOG_BOLD":   ("Consolas",           9,  "bold"),
    "ACRPA_ICON":       ("Segoe UI Symbol",    9,  "normal"),
    "ACRPA_ICON_MD":    ("Segoe UI Symbol",   11,  "normal"),
    "ACRPA_ICON_LG":    ("Segoe UI Symbol",   12,  "normal"),
    "ACRPA_PIN":        ("Consolas",           28,  "bold"),
}

# ── UI 缩放 (pt 制) ──
UI_SCALE_DEFAULT = 1.0
UI_SCALE_MIN     = 0.8
UI_SCALE_MAX     = 1.5

_font_root = None   # init_fonts() 记录的 root, 供 dpi_factor()/set_ui_scale() 复用
# 命名字体句柄强引用: tkinter.font.Font 的 __del__ 会 font delete 自己创建的字体,
# 若不保留引用, 刚建好的命名字体立刻被回收删除 (实测 TclError: does not already exist)。
_font_cache = {}

# ── DPI 缓存 ──
# 为什么缓存: dpi_factor() 被 scaled() 间接调用, 而 scaled() 在控件构建里是高频
# 热路径 (res/ 全量约 900 处控件构造 × 每处若干 sp())。winfo_fpixels("1i") 是
# 一次 **Tcl 往返**, 不缓存等于把 DPI 查询放大成上千次解释器调用, 这是"启动慢/
# 布局慢"里最容易被忽略的一项。DPI 在进程内基本恒定, 缓存后只在 init_fonts()
# (root 建好) 与显式 refresh_dpi_cache() 时刷新。
_dpi_cache = {"root": None, "value": 1.0, "valid": False}


def refresh_dpi_cache(root=None):
    """重算 DPI 倍率缓存 (root 变更 / 跨屏移动 / 系统缩放变化后调用)。

    返回新的倍率; 无 root 时保持 1.0 且不置 valid (下次有 root 再算)。
    """
    r = root if root is not None else _font_root
    if r is None:
        _dpi_cache.update({"root": None, "value": 1.0, "valid": False})
        return 1.0
    try:
        v = max(0.5, min(4.0, float(r.winfo_fpixels("1i")) / 96.0))
    except Exception:
        v = 1.0
    _dpi_cache.update({"root": r, "value": v, "valid": True})
    return v


def dpi_factor():
    """屏幕 DPI 相对 96 的倍率; 需 root 存在 (winfo_fpixels("1i")/96), 结果缓存。

    无 root 或取值失败 -> 1.0。缓存根因见 _dpi_cache 注释: 未缓存时每次
    scaled() 都触发一次 Tcl 往返。DPI 变化用 refresh_dpi_cache() 显式刷新。
    """
    if _dpi_cache["valid"] and _dpi_cache["root"] is _font_root:
        return _dpi_cache["value"]
    return refresh_dpi_cache(_font_root)


# pt → px 的固定换算 (96 DPI 基准): Tk 的 `tk scaling` 语义即 "1pt = 多少 px"。
PT_PER_INCH = 72.0
PX_PER_INCH = 96.0


def apply_tk_scaling(root, fixed=None):
    """统一设置 `tk scaling`, 返回实际生效值 (px per pt)。

    auto (默认): scaling = fpixels("1i")/72 —— 即让 1pt 恒等于 1/72 英寸,
                 字体按 pt 书写, 由 Tk 换算, 视觉随系统 DPI 正确放大。
    fixed:       scaling 固定为 1.0 (1pt = 1px, 消除 Tk 内的 pt→px 换算),
                 此时命名字体必须改用**负像素字号**才能保持同样的视觉大小,
                 否则 150% DPI 下字会突然变小 (这是"直接 scaling=1.0"最常见的坑)。

    fixed 由 state.TK_SCALING_FIXED 控制 (默认 False = auto, 零行为变化)。
    """
    global _font_px_mode
    if not _dpi_cache["valid"] or _dpi_cache["root"] is not root:
        refresh_dpi_cache(root)
    if fixed is None:
        try:
            fixed = bool(getattr(state, "TK_SCALING_FIXED", False))
        except Exception:
            fixed = False
    try:
        if fixed:
            root.tk.call("tk", "scaling", 1.0)
            _font_px_mode = True
            return 1.0
        v = float(root.winfo_fpixels("1i")) / PT_PER_INCH
        root.tk.call("tk", "scaling", v)
        _font_px_mode = False
        return v
    except Exception:
        _font_px_mode = False
        return 1.0


# 是否处于「像素制字体」模式 (apply_tk_scaling 设置; init_fonts/set_ui_scale 读取)
_font_px_mode = False


def _font_size_value(base_size, scale):
    """基准 pt → 传给 Tk 的字号值: 像素制返回负像素, 否则返回 pt。"""
    pt = fit_pt(base_size, scale)
    if not _font_px_mode:
        return pt
    try:
        px = int(round(pt * (PX_PER_INCH / PT_PER_INCH) * dpi_factor()))
    except Exception:
        return pt
    return -max(6, px)


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
    # root 就绪 → 刷新 DPI 缓存 (一次性 Tcl 往返, 之后 scaled() 全走内存)。
    # 同一 root 已算过就不重复查询 (apply_tk_scaling 可能刚算过)。
    if not _dpi_cache["valid"] or _dpi_cache["root"] is not root:
        refresh_dpi_cache(root)
    for role, (family, size, weight) in _FONT_SPECS.items():
        pt = _font_size_value(size, scale)
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
            log1("命名字体改配失败: {} -> {} ({})".format(role, pt, e))


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
                                  exists=True).configure(size=_font_size_value(size, v))
            except Exception as e:
                # 不再静默吞掉: 缩放档位切换时的改配失败必须可见。
                log1("界面缩放字体改配失败: {} -> {} ({})".format(
                    role, _font_size_value(size, v), e))
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
    if fg_c is None:
        fg_c = C["fgb"]
    elif str(fg_c).strip().lower() in ("white", "#ffffff", "#fff"):
        # 字面白字在「浅语义底」(暗色主题 ac=#4CC2FF / wn=#FCE100) 上不可读 →
        # 按底色相对亮度自适应, 调用方无需再逐一改 fg。
        fg_c = _theme.text_on(bg_c)
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
            padx=sp("sp_sm"), pady=max(1, sp("sp_xs") // 2)).pack()
    except Exception:
        pass

# 悬停提示延迟 (ms, docs/UI优化方案-v3.md §5.2 D12): 全应用统一, 避免一碰就弹
# 又不会显得没反应; 数字不经 scaled() (时间不是像素)。
TOOLTIP_DELAY_MS = 400


def _cancel_tip_job(widget):
    """取消控件上待触发的 tooltip 定时器 (重复 <Enter>/离开时调用)。"""
    job = getattr(widget, "_tooltip_job", None)
    if job is not None:
        try:
            widget.after_cancel(job)
        except Exception:
            pass
        try:
            widget._tooltip_job = None
        except Exception:
            pass


def attach_tooltip(widget, text, delay=None):
    """为控件绑定悬停提示。text 可为字符串或返回字符串的可调用对象(动态提示)。

    delay: 悬停多久后弹出 (ms); None → `TOOLTIP_DELAY_MS` (400)。传 0 立即弹出。
    """
    getter = text if callable(text) else (lambda t=text: t)
    try:
        wait = TOOLTIP_DELAY_MS if delay is None else max(0, int(delay))
    except Exception:
        wait = TOOLTIP_DELAY_MS

    def _on_enter(event):
        _cancel_tip_job(widget)
        if wait <= 0:
            _show_tooltip(event, getter)
            return
        try:
            widget._tooltip_job = widget.after(
                wait, lambda: (_set_job_none(widget), _show_tooltip(event, getter)))
        except Exception:
            _show_tooltip(event, getter)

    def _on_leave(_event=None):
        _cancel_tip_job(widget)
        _hide_tooltip()

    widget.bind("<Enter>", _on_enter)
    widget.bind("<Leave>", _on_leave)


def _set_job_none(widget):
    try:
        widget._tooltip_job = None
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════════
# 可达性 (a11y) 助手 —— 路线图 §5.5「可达性补齐」
# ═══════════════════════════════════════════════════════════════════════
# 背景: 标题栏的 △ ⊟ ◑ ⚙ ? ⊕ 等图标是 `Label` + `<Button-1>`, 既不可 Tab
# 聚焦, 也没有 accessible name, 屏幕阅读器 / 纯键盘用户无法使用。
# Tk 没有跨平台标准无障碍接口 (Tk 8.6 无 `-name`/ARIA), 故做如下**文档化**约定:
#   1) 可访问名落到控件属性 `acrpa_accessible_name` (供测试/内省读取), 并在控件
#      尚无 `<Enter>` 绑定时复用 attach_tooltip 呈现在悬停提示里;
#   2) `bind_icon_activate()` 把「Label 图标按钮」一键变为可聚焦 + 可键盘激活。
# 约束: 绝不覆盖控件上既有 `<Enter>`/`<Leave>` (tooltip) 绑定。

def set_accessible_name(widget, text):
    """为控件登记可访问名 (Tk 无标准无障碍属性的文档化替代)。

    落地方式 (纯属性 + 复用 tooltip, 不引入新依赖):
      · `widget.acrpa_accessible_name = text`  —— 唯一权威读取点;
      · 仅当控件**尚未**绑定 `<Enter>` 时, 才用 attach_tooltip 呈现该名字,
        避免覆盖既有 tooltip 行为 (标题栏图标已各自绑定 <Enter>)。
    返回 text。失败静默 (无障碍属性缺失不应影响功能)。
    """
    try:
        widget.acrpa_accessible_name = text
    except Exception:
        pass
    try:
        if text and not widget.bind("<Enter>"):
            attach_tooltip(widget, text)
    except Exception:
        pass
    return text


def bind_icon_activate(widget, command, name=None):
    """把 Label 图标按钮变为**可键盘聚焦/激活** (路线图 §5.5 可达性补齐)。

    · takefocus=True + 1px 可见焦点环 (highlightcolor=C["focus"]);
    · `<Button-1>` / `<Return>` / `<space>` 触发同一 command (键盘等价点击);
    · name 非空时登记可访问名 (set_accessible_name)。
    调用方应移除此前对 `<Button-1>` 的直接绑定, 以免重复触发。
    不触碰 `<Enter>`/`<Leave>` (tooltip) 绑定。返回 widget。
    """
    try:
        widget.configure(takefocus=True, highlightthickness=1,
                         highlightbackground=themed("bg"),
                         highlightcolor=themed("focus"))
    except Exception:
        pass

    def _fire(_e=None):
        try:
            command()
        except Exception:
            pass
        return "break"

    for _seq in ("<Button-1>", "<Return>", "<space>"):
        try:
            widget.bind(_seq, _fire)
        except Exception:
            pass
    if name:
        set_accessible_name(widget, name)
    return widget


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
    # 设置页表单布局密度 (docs/设置窗口布局重构方案 §3.3):
    # 标签列最小宽 / 行距 / 分节间距 / 帮助文本折行宽 —— 供 cards/_layout.py 消费。
    # 独立 token, 不改 ctrl_h 全局值 (避免主窗口/市场/工作流连锁回归)。
    # help_wrap: 折行宽必须 < 内容列宽 (默认窗 680 − 导航 ~128 − 卡内留白), 否则
    # 最长的一条提示会把整张卡的 reqwidth 顶宽, 在默认窗宽下被横向裁掉。
    "form_label_w": 110, "row_gap": 8, "section_gap": 12, "help_wrap": 480,
    # 窗口尺寸 (设计 px, 路线图 §5.5): 主窗口默认几何 / 最小尺寸经 sp() 派生,
    # 使 set_ui_scale() 真正影响窗口尺寸 (而非只缩放字体)。
    "win_w": 1000, "win_h": 680, "win_min_w": 640, "win_min_h": 520,
    # 紧凑模式 (=Mini Bar 折叠前旧布局) 尺寸
    "win_compact_w": 500, "win_compact_h": 625,
    "win_compact_min_w": 450, "win_compact_min_h": 550,
    # 从窗口通用尺寸 (docs/UI优化方案-v3.md §2.3): 供 ui/window_shell 消费,
    # 使 set_ui_scale() 对设置/帮助/市场/互联等从窗口同样生效。
    "dlg_sm_w": 420, "dlg_sm_h": 320,
    "dlg_md_w": 560, "dlg_md_h": 480,
    "dlg_lg_w": 720, "dlg_lg_h": 600,
    "dlg_xl_w": 880, "dlg_xl_h": 680,
    "dlg_min_w": 360, "dlg_min_h": 240,
    # 滚动条厚度 (docs/UI优化方案-v3.md §5.2 D10): 垂直 8 / 横向内嵌工具条 6,
    # 一律由此派生, 禁写 width=6/8/16 裸数字 (ttk 默认 16px 是最离谱的一处)。
    "sb_w": 8, "sb_w_thin": 6,
    # ── 动效令牌 (P2-7): 时长单位 ms, 帧间隔 16ms≈60fps, 禁写裸 after(200) 等 ──
    # 时长不做 dpi/ui_scale 缩放 (时间是绝对量), 故单独成类, 不经 sp()。
    "motion_frame": 16, "motion_fast": 120, "motion_base": 200, "motion_slow": 320,
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


def motion(token="motion_base"):
    """动效时长 (ms, 绝对时间, 不做 dpi/ui_scale 缩放)。"""
    return int(TOKENS.get(token, TOKENS["motion_base"]))


def motion_frame():
    """动效每帧刷新间隔 (ms, 默认 16≈60fps)。"""
    return int(TOKENS.get("motion_frame", 16))


def reduce_motion():
    """「减少动效」开关 (无障碍 + 低配机器, P2-7)。

    真源 state.REDUCE_MOTION (暂未落地时为 False → 保留全动效); 消费方用它
    决定是否把插值动画退化为一步到位, 避免散落多份判断。
    """
    try:
        return bool(getattr(state, "REDUCE_MOTION", False))
    except Exception:
        return False


def ease(t, kind="out_cubic"):
    """轻量缓动插值 (纯函数, P2-1): 归一化进度 t∈[0,1] → 缓动后进度。

    kind: linear | out_quad | out_cubic (默认, 先快后慢) | in_out (先慢后快再慢)。
    真源 core.animation.CONTROLLERS, 与 Animation 控制器同表, 不引入第三方动画库。
    """
    try:
        t = max(0.0, min(1.0, float(t)))
    except Exception:
        t = 0.0
    return _EASE.get(kind, out_cubic)(t)


def icon_size(token="icon_md"):
    """图标边长 (默认 24 设计 px)。"""
    return sp(token)


# ═══════════════════════════════════════════════════════════════════════
# 窗口图标: 子窗口/对话框统一套「产品图标」(res/automation.ico)
#   · 此前 settings 等模块各自 `from utils import set_window_icon` 却取不到名字
#     (函数根本没实现), 异常又被静默吞掉, 于是设置页/弹窗一直显示 Tk 默认羽毛图标。
#   · 这里给出唯一实现: 路径解析 + iconbitmap(标题栏/Alt+Tab) + WM_SETICON(任务栏)。
# ═══════════════════════════════════════════════════════════════════════

def icon_path(name="automation.ico"):
    """产品图标绝对路径; 找不到返回 ""。

    与 ACRPA.py 的路径优先级一致: <根>/res/<name> 优先, 退回 <根>/<name>;
    冻结后「根」= exe 所在目录, 源码态 = 仓库根 (src 的上级)。
    """
    if getattr(sys, "frozen", False):
        base = os.path.dirname(os.path.abspath(sys.executable))
    else:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for candidate in (os.path.join(base, "res", name), os.path.join(base, name)):
        if os.path.exists(candidate):
            return candidate
    return ""


def set_window_icon(window, name="automation.ico"):
    """给 Toplevel/根窗口设置 ACRPA 产品图标, 返回是否成功。

    · iconbitmap → 标题栏左上角 / Alt+Tab;
    · WM_SETICON(ICON_SMALL/ICON_BIG) → 任务栏 (iconbitmap 不覆盖任务栏按钮);
    · 窗口首次 <Map> 后再补设一次: 任务栏按钮在窗口真正显示时才建立,
      只在创建期设置会被 Windows 用默认图标覆盖 (Win11 尤其明显)。

    纯 Windows API, 任何环境异常都静默返回 False —— 图标绝不该影响开窗。
    """
    path = icon_path(name)
    if not path:
        return False

    ok = False
    try:
        window.iconbitmap(path)
        ok = True
    except Exception:
        pass

    def _apply():
        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.WinDLL("user32", use_last_error=True)

            load = user32.LoadImageW
            load.restype = wintypes.HANDLE
            load.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
                             ctypes.c_int, ctypes.c_int, wintypes.UINT]
            send = user32.SendMessageW
            send.restype = wintypes.LPARAM
            send.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
            get_parent = user32.GetParent
            get_parent.restype = wintypes.HWND
            get_parent.argtypes = [wintypes.HWND]

            IMAGE_ICON = 1
            LR_LOADFROMFILE = 0x00000010
            WM_SETICON = 0x0080
            ICON_SMALL, ICON_BIG = 0, 1

            hicon = load(None, path, IMAGE_ICON, 0, 0, LR_LOADFROMFILE)
            if not hicon:
                return False
            hwnd = get_parent(wintypes.HWND(window.winfo_id()))
            if not hwnd:
                hwnd = window.winfo_id()
            send(hwnd, WM_SETICON, ICON_SMALL, hicon)
            send(hwnd, WM_SETICON, ICON_BIG, hicon)
            return True
        except Exception:
            return False

    ok = _apply() or ok
    try:
        if not getattr(window, "_acrpa_icon_map_bound", False):
            window._acrpa_icon_map_bound = True
            window.bind("<Map>", lambda _e: _apply(), add="+")
    except Exception:
        pass
    return ok


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


def dialog_geometry(w, h, parent_rect=None, vroot=None):
    """**纯函数**: 计算对话框落位字符串 "WxH+X+Y" (可 headless 测试)。

    规则 (路线图 §5.5 待办 2 —— 消除硬编码 +x+y 屏幕偏移):
      · 有 parent_rect=(px,py,pw,ph) → 居中于父窗 (纵向略偏上 1/3);
        无 parent_rect           → 居中于 vroot;
      · 越界钳制: 结果完整落在虚拟屏内 (小屏 / 多显示器不溢出)。

    w/h 为**已缩放**的实际像素。vroot=(vx,vy,vw,vh), 缺省由 parent_rect 派生。
    不触碰 Tk, 不抛异常。
    """
    try:
        w = max(1, int(w)); h = max(1, int(h))
    except Exception:
        w, h = 1, 1
    if vroot is None:
        if parent_rect:
            try:
                px, py, pw, ph = (int(v) for v in parent_rect)
            except Exception:
                px = py = pw = ph = 0
            vroot = (0, 0, max(px + pw, w), max(py + ph, h))
        else:
            vroot = (0, 0, w, h)
    try:
        vx, vy, vw, vh = (int(v) for v in vroot)
    except Exception:
        vx, vy, vw, vh = 0, 0, w, h
    vw = max(vw, w); vh = max(vh, h)
    if parent_rect:
        try:
            px, py, pw, ph = (int(v) for v in parent_rect)
        except Exception:
            px = py = 0; pw = ph = 0
        x = px + (pw - w) // 2
        y = py + max(0, (ph - h) // 3)
    else:
        x = vx + (vw - w) // 2
        y = vy + max(0, (vh - h) // 3)
    x = max(vx, min(x, vx + vw - w))
    y = max(vy, min(y, vy + vh - h))
    return "{}x{}+{}+{}".format(w, h, x, y)


def place_dialog(win, w=None, h=None, parent=None):
    """通用对话框落位助手: 居中于父窗 + 虚拟屏越界钳制。

    替代全库硬编码的 `win.geometry("WxH+固定x+固定y")` (§5.5 待办 2):
    小屏 / 多显示器下原先的固定屏幕偏移会把窗口推出去。

    参数:
        win:    目标 Toplevel (或任何有 geometry/winfo_* 的窗口)。
        w, h:   设计像素尺寸 (内部经 scaled() 派生; None → 取当前请求尺寸)。
        parent: 居中所依据的父窗; 缺省用 win.master。
    返回实际写入的几何字符串 (失败时返回 "")。
    """
    try:
        if parent is None:
            parent = win.master
    except Exception:
        parent = None

    try:
        if w is None or h is None:
            win.update_idletasks()
            rw, rh = win.winfo_reqwidth(), win.winfo_reqheight()
            if w is None:
                w = rw
            if h is None:
                h = rh
    except Exception:
        w = w or 1
        h = h or 1
    try:
        w = scaled(w); h = scaled(h)
    except Exception:
        pass

    parent_rect = None
    try:
        if parent is not None and int(parent.winfo_width()) > 1:
            parent_rect = (parent.winfo_rootx(), parent.winfo_rooty(),
                           parent.winfo_width(), parent.winfo_height())
    except Exception:
        parent_rect = None

    try:
        probe = parent.winfo_toplevel() if parent is not None else win
        vroot = win_virtual_bounds(probe)
    except Exception:
        vroot = None

    geo = dialog_geometry(w, h, parent_rect, vroot)
    try:
        win.geometry(geo)
    except Exception:
        return ""
    return geo


# ── 版本号解析 / 比较 re-export (实现已迁至 src/core/version.py, 见 §3.1 / L2-A) ──
# parse_version / compare_versions / version_tuple / version_gt 均已在顶部 re-export,
# 既有 `from utils import ...` 与 `utils.XXX` 调用点无需改动。


