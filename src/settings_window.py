"""
settings_window.py — 独立设置窗口 (从 ACRPA.py 设置 Tab 提取)

open_settings_window() 创建独立 Toplevel，包含全部设置卡片:
基础执行 / AI 增强 / 定时调度 / 日志 / 系统 / 快速操作 / 高级设置

统一即时保存模型: 所有设置控件改动即自动保存 (600ms 防抖)，
无需手动点 ✓；系统卡 (自启/托盘/Mini Bar) 保持即时生效。
卡片标题栏右侧角标反馈保存状态。

依赖注入设计:
    不 import ACRPA (避免循环导入)，ACRPA.py 启动时调用 init_ctx() 注入
    root / C / FONT / 回调。主题切换时 ACRPA._refresh_theme 会先同步
    settings_window.C 再调用 refresh_theme() 递归刷新已打开窗口的颜色。
"""
import os, sys
import threading
import tkinter
from tkinter import ttk, messagebox, filedialog

import state
import utils
import scheduler as sched
from utils import (_btn, _darken, create_card, log1, show_toast, attach_tooltip,
                   FONT_SMALL_BOLD, FONT_TINY, FONT_ICON)

# ── 依赖注入 (由 ACRPA.py 在启动时调用 init_ctx 填充) ──
root = None              # 主窗口
C = None                 # 颜色表 (主题切换时由 ACRPA._refresh_theme 同步)
FONT_TITLE = FONT_BODY = FONT_SMALL = FONT_BUTTON = None
APP_ROOT = ""            # 程序根目录
_tlog = None             # ThreadSafeLog 实例 (导出日志)
_ensure_tray = None      # 托盘创建回调
_destroy_tray = None     # 托盘销毁回调
_toggle_fold = None      # Mini Bar 折叠切换回调
_main_run = None         # 运行脚本回调 (定时调度保存)
_win = None              # 当前打开的设置窗口
_prev_colors = None      # 上次刷新时的主题色表快照 (语义色回填的兜底判定来源)
_tip_win = None          # tooltip 窗口
_sched_next_label = None # 定时调度「下次执行」标签 (打开设置窗口时注入，供主程序周期刷新)

# ── 统一即时保存框架 (控件改动 600ms 防抖后自动保存) ──
_debounce_after = {}     # card_key -> after id
_apply_map = {}          # card_key -> apply 函数 (保存时调用, 写 state)
_saved_badges = {}       # card_key -> 卡标题栏「自动保存」角标 Label

# ── P0 左侧导航栏 ──
_nav_cards = []          # [(key, icon, label, card_widget), ...] — 注册顺序即显示顺序
_nav_labels = {}         # key -> tkinter.Label (导航项)
_nav_canvas = None       # 右侧滚动 Canvas
_nav_active_key = None   # 当前高亮的导航项 key


def update_sched_next_label(text):
    """供主程序 _periodic 刷新: 设置窗口已打开时更新「下次执行」标签。"""
    global _sched_next_label
    if _sched_next_label is not None:
        try:
            _sched_next_label.config(text=text)
        except Exception:
            pass


def init_ctx(root_win=None, colors=None, fonts=None, app_root="", tlog=None,
             ensure_tray=None, destroy_tray=None, toggle_fold=None,
             main_run=None):
    """注入 ACRPA.py 提供的依赖 (模块启动时调用一次)。"""
    global root, C, FONT_TITLE, FONT_BODY, FONT_SMALL, FONT_BUTTON
    global APP_ROOT, _tlog, _ensure_tray, _destroy_tray, _toggle_fold, _main_run
    global _prev_colors
    root = root_win
    C = colors
    _prev_colors = dict(colors) if isinstance(colors, dict) else None
    if fonts:
        FONT_TITLE, FONT_BODY, FONT_SMALL, FONT_BUTTON = fonts
    APP_ROOT = app_root
    _tlog = tlog
    _ensure_tray = ensure_tray
    _destroy_tray = destroy_tray
    _toggle_fold = toggle_fold
    _main_run = main_run


def _set_window_icon(window):
    """Set the ACRPA icon on a child Toplevel window (including taskbar icon)."""
    ico_path = os.path.join(APP_ROOT, "res", "automation.ico")
    if not os.path.exists(ico_path):
        ico_path = os.path.join(APP_ROOT, "automation.ico")
    if not os.path.exists(ico_path):
        return
    try:
        import ctypes
        window.iconbitmap(ico_path)
        WM_SETICON = 0x0080
        ICON_SMALL = 0
        ICON_BIG = 1
        IMAGE_ICON = 1
        LR_LOADFROMFILE = 0x00000010
        hicon = ctypes.windll.user32.LoadImageW(
            None, ico_path, IMAGE_ICON, 0, 0, LR_LOADFROMFILE)
        if hicon:
            hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
            if not hwnd:
                hwnd = window.winfo_id()
            ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, hicon)
            ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, hicon)
    except Exception:
        pass


# 语义色分组 (与 ACRPA._SEMANTIC_GROUPS 同构): 组内任一键命中即归入该语义组。
_SEMANTIC_GROUPS = (("sc", ("sc", "ok")), ("dg", ("dg", "err")),
                    ("wn", ("wn",)), ("ac", ("ac", "hover", "focus")))


def _semantic_bg_for(cur_bg, prev):
    """语义按钮底色回填 (D2): 命中语义组返回当前主题色, 否则 None (中性按钮)。"""
    for primary, keys in _SEMANTIC_GROUPS:
        for k in keys:
            if prev and prev.get(k) == cur_bg:
                return C.get(primary)
            if C.get(k) == cur_bg:
                return C.get(primary)
    return None


def refresh_theme(prev=None):
    """主题切换后递归刷新已打开设置窗口的颜色 (供 ACRPA._refresh_theme 调用)。

    prev: 旧主题色表快照 (由 ACRPA._refresh_theme 显式传入)。语义按钮回填以
    prev∪C 判定 —— 旧实现依赖恒为 None 的 C["old_*"], 会把 ▶运行/⏸暂停 等
    语义按钮误判为中性按钮刷成灰 (与主窗口 D2 同源缺陷)。
    """
    global _prev_colors
    if _win is None or not _win.winfo_exists():
        return
    if not isinstance(prev, dict):
        prev = _prev_colors if isinstance(_prev_colors, dict) else {}

    def _walk(p, prev):
        for w in p.winfo_children():
            try:
                cls = w.winfo_class()
                if cls in ("Frame", "TFrame"):
                    ht = int(w.cget("highlightthickness"))
                    if ht > 0:
                        w.configure(bg=C["bgc"], highlightbackground=C["bd"])
                    else:
                        w.configure(bg=C["bg"])
                elif cls in ("Label", "TLabel"):
                    parent_bg = str(p.cget("bg"))
                    fg_color = C["fgm"]
                    txt = w.cget("text")
                    if txt and ("●" in txt or "运行" in txt or "就绪" in txt):
                        fg_color = C["ac"] if "就绪" in txt else (C["sc"] if "运行" in txt else C["fgm"])
                    w.configure(bg=parent_bg, fg=fg_color)
                elif cls == "Text":
                    w.configure(bg=C["logbg"], fg=C["logfg"],
                        insertbackground=C["fgt"],
                        selectbackground=C["acl"], selectforeground=C["fgt"])
                elif cls == "Scrollbar":
                    w.configure(bg=C["bd"], activebackground=C["fgm"], troughcolor=C["logbg"])
                elif cls == "Button":
                    # 语义按钮: 保留语义并更新为当前主题色; 中性按钮: 统一刷为卡片色
                    _new_bg = _semantic_bg_for(w.cget("bg"), prev)
                    if _new_bg is not None:
                        w.configure(bg=_new_bg)
                    else:
                        w.configure(bg=C["bgc"], fg=C["fgb"],
                            activebackground=C["acl"],
                            highlightbackground=C["bd"])
                elif cls == "Entry":
                    w.configure(bg=C["ebg"], fg=C["fgb"],
                        insertbackground=C["fgt"])
                elif cls == "Combobox":
                    w.configure(background=C["bgc"], fieldbackground=C["bgc"],
                        foreground=C["fgb"])
                elif cls == "Canvas":
                    # D5: 设置页滚动画布 (旧实现遗漏 → 暗色下仍为浅色)
                    w.configure(bg=C["bg"])
                elif cls == "Listbox":
                    # D5: 列表控件 (旧实现遗漏)
                    w.configure(bg=C["ebg"], fg=C["fgb"],
                        selectbackground=C["ac"], selectforeground="white")
                elif cls == "Spinbox":
                    # D5: 数值框 (旧实现遗漏)
                    w.configure(bg=C["ebg"], fg=C["fgb"],
                        buttonbackground=C["bgc"], insertbackground=C["fgt"])
                elif cls in ("Checkbutton", "Radiobutton"):
                    # D5: 勾选/单选框 (旧实现遗漏)
                    _pbg = str(p.cget("bg"))
                    w.configure(bg=_pbg, fg=C["fgb"],
                        activebackground=_pbg, activeforeground=C["fgt"],
                        selectcolor=C["bgc"], highlightbackground=C["bd"])
                elif cls == "Menu":
                    # D5: 下拉/右键菜单 (旧实现遗漏)
                    w.configure(bg=C["bgc"], fg=C["fgb"],
                        activebackground=C["ac"], activeforeground="white",
                        disabledforeground=C["fgm"])
            except Exception:
                pass
            _walk(w, prev)

    _walk(_win, prev)
    try:
        _win.update_idletasks()
    except Exception:
        pass
    # 记录本次主题快照, 供下次 (无显式 prev 时) 判定
    _prev_colors = dict(C) if isinstance(C, dict) else None


def _close():
    """关闭设置窗口并释放全局引用; 记忆窗口位置到 config (win_geometry)。"""
    global _win, _sched_next_label
    try:
        if _win is not None and _win.winfo_exists():
            state.WIN_GEOMETRY = _win.geometry()
            state.save_config()
    except Exception:
        pass
    try:
        _win.destroy()
    except Exception:
        pass
    _win = None
    _sched_next_label = None


# ======================================================================
# 统一即时保存框架
# ======================================================================

def _auto_save(card_key, debounce_ms=600):
    """防抖保存: 卡内任一设置变化后 600ms 无新变化 → 执行该卡 apply + save_config。"""
    if card_key in _debounce_after:
        try:
            _win.after_cancel(_debounce_after[card_key])
        except Exception:
            pass
    _debounce_after[card_key] = _win.after(debounce_ms, lambda: _do_save(card_key))


def _do_save(card_key):
    """执行该卡 apply 函数 → 保存配置 (触发 state.on_config_change 监听器) → 角标反馈。"""
    try:
        fn = _apply_map.get(card_key)
        if fn:
            fn()
        state.save_config()
        _flash_saved(card_key)
    except Exception as e:
        log1("保存设置失败: {}".format(e), "error")


def _flash_saved(card_key):
    """角标 1.2s 显示「✓ 已保存」后恢复「自动保存」。"""
    badge = _saved_badges.get(card_key)
    if badge is None or not badge.winfo_exists():
        return
    try:
        badge.configure(text="✓ 已保存", fg=C["sc"],
            font=FONT_SMALL_BOLD)
        _win.after(1200, lambda: _reset_badge(badge))
    except Exception:
        pass


def _reset_badge(badge):
    try:
        badge.configure(text="自动保存", fg=C["fgm"],
            font=FONT_SMALL)
    except Exception:
        pass


def _track_card_vars(card_key, vars_):
    """把卡内所有设置 var 绑定到防抖保存 (改动即自动保存)。"""
    for v in vars_:
        try:
            v.trace_add("write", lambda *_: _auto_save(card_key))
        except Exception:
            pass


def _make_badge(card_key, title_frame):
    """创建卡标题栏右侧「自动保存」角标 (供 _flash_saved 反馈)。"""
    badge = tkinter.Label(title_frame, text="自动保存",
        font=FONT_SMALL, bg=C["bgc"], fg=C["fgm"])
    badge.grid(row=0, column=1, sticky="e", padx=(4, 0))
    _saved_badges[card_key] = badge
    return badge


def _validate_number(entry, var, name, default, cast, min_v=None, max_v=None,
                     card_key=None):
    """数字输入 FocusOut 校验: 非法 → 浅红背景 + toast 提示 + 恢复默认; 合法 → 恢复背景。"""
    err_bg = C["errbg"]

    def _on_focus_out(event):
        raw = var.get().strip()
        ok = False
        try:
            v = cast(raw)
            if min_v is not None and v < min_v:
                raise ValueError
            if max_v is not None and v > max_v:
                raise ValueError
            ok = True
        except (ValueError, TypeError):
            pass
        if ok:
            entry.configure(bg=C["ebg"])
        else:
            entry.configure(bg=err_bg)
            rng = ""
            if min_v is not None:
                rng += "{}≤".format(min_v)
            if max_v is not None:
                rng += "≤{}".format(max_v)
            unit = "整数" if cast is int else "数字"
            show_toast(root, "{} 需为{}{}，已恢复默认 {}".format(
                name, rng, unit, default), "warning")
            var.set(str(default))
            if card_key:
                _auto_save(card_key)

    entry.bind("<FocusOut>", _on_focus_out)
    return entry


def _make_collapsible_card(parent, title, icon):
    """创建可折叠设置卡: 返回 (card, content_frame, title_frame, arrow_label)。

    点击标题栏或 ▼/▶ 箭头切换折叠; 内容全部放入 content_frame (row=1)。
    """
    card = create_card(parent)
    card.columnconfigure(0, weight=1)

    title_frame = tkinter.Frame(card, bg=C["bgc"])
    title_frame.grid(row=0, column=0, sticky="ew", padx=10, pady=(6, 2))
    title_frame.columnconfigure(0, weight=1)

    tkinter.Label(title_frame, text="{} {}".format(icon, title),
        font=FONT_TITLE,
        bg=C["bgc"], fg=C["fgt"]).grid(row=0, column=0, sticky="w")

    arrow = tkinter.Label(title_frame, text="▼", font=FONT_ICON,
        bg=C["bgc"], fg=C["fgm"], cursor="hand2")
    arrow.grid(row=0, column=2, sticky="e", padx=(4, 0))

    content = tkinter.Frame(card, bg=C["bgc"])
    content.grid(row=1, column=0, sticky="ew")
    content.columnconfigure(0, weight=1)

    def _toggle(event=None):
        if content.winfo_viewable():
            content.grid_remove()
            arrow.config(text="▶")
        else:
            content.grid()
            arrow.config(text="▼")

    title_frame.bind("<Button-1>", _toggle)
    arrow.bind("<Button-1>", _toggle)
    return card, content, title_frame, arrow

# ── P0 导航辅助 ──

_NAV_ITEMS = [
    # (key, icon, label) — 声明式导航注册
    ("exec",     "⚙️", "基础执行"),
    ("ai",       "🤖", "AI 增强"),
    ("sched",    "⏰", "定时调度"),
    ("record",   "📹", "录制设置"),
    ("log",      "📋", "日志"),
    ("system",   "💻", "系统"),
    ("quick",    "⚡", "快速操作"),
    ("advanced", "🔧", "高级设置"),
    ("netlink",  "🌐", "网络互联"),
    ("python",   "🐍", "Python 扩展"),
    ("market",   "🛒", "脚本市场"),
]


def _register_nav_card(key, card_widget):
    """注册一张设置卡到导航栏 (在卡创建后调用)。"""
    global _nav_cards
    _nav_cards.append((key, card_widget))


def _on_nav_click(key):
    """点击导航项：滚动右侧画布到对应卡片并高亮。"""
    global _nav_active_key, _nav_canvas
    if _nav_canvas is None:
        return

    # 更新导航高亮
    _nav_active_key = key
    for k, lbl in _nav_labels.items():
        try:
            if k == key:
                lbl.configure(bg=C["ac"], fg="white",
                    font=FONT_BUTTON)
            else:
                lbl.configure(bg=C["bg"], fg=C["fgm"],
                    font=FONT_BODY)
        except Exception:
            pass

    # 找到对应卡片并滚动到可见区域
    for k, card_w in _nav_cards:
        if k == key:
            try:
                _win.update_idletasks()
                card_y = card_w.winfo_y()
                inner_h = _nav_canvas.bbox("all")[3] if _nav_canvas.bbox("all") else 1
                if inner_h > 0:
                    fraction = max(0.0, min(1.0, (card_y - 10) / max(inner_h - 400, 1)))
                    _nav_canvas.yview_moveto(fraction)
            except Exception:
                pass
            break

def open_settings_window():
    """打开独立设置窗口；若已打开则聚焦。"""
    global _win, _sched_next_label, _nav_canvas, _nav_cards, _nav_labels, _nav_active_key
    global _prev_colors
    if _win is not None and _win.winfo_exists():
        _win.lift()
        _win.focus_force()
        return
    # 本窗口将以当前 C 建窗: 记录快照, 保证下次主题切换的语义色判定基准正确
    _prev_colors = dict(C) if isinstance(C, dict) else None
    _nav_cards = []
    _nav_labels = {}
    _nav_active_key = None
    _win = tkinter.Toplevel(root)
    _win.title("设置 — ACRPA")
    # 记忆上次位置/尺寸 (config.json win_geometry): 非空且经虚拟屏越界校验
    # (utils.geometry_in_screen) 通过才应用; 空或越界一律回退居中 680x680,
    # 避免显示器拔插/缩放变化后设置窗口落到屏幕外无法操作。
    try:
        saved_geo = getattr(state, "WIN_GEOMETRY", "") or ""
        if saved_geo and utils.geometry_in_screen(_win, saved_geo):
            _win.geometry(saved_geo)
        else:
            rx, ry = root.winfo_x(), root.winfo_y()
            rw = max(root.winfo_width(), 680); rh = max(root.winfo_height(), 680)
            sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
            x = min(max(rx + (rw - 680) // 2, 0), max(sw - 680, 0))
            y = min(max(ry + (rh - 680) // 2, 0), max(sh - 680, 0))
            _win.geometry("680x680+{}+{}".format(x, y))
    except Exception:
        _win.geometry("680x680+450+60")
    _win.minsize(580, 500)
    _win.configure(bg=C["bg"])
    _set_window_icon(_win)
    _win.columnconfigure(0, weight=0)  # 导航栏 (固定宽度)
    _win.columnconfigure(1, weight=1)  # 内容区 (可伸缩)
    _win.rowconfigure(0, weight=1)
    _win.protocol("WM_DELETE_WINDOW", _close)

    # ── 左侧导航栏 ──
    nav_panel = tkinter.Frame(_win, bg=C["bg"], width=140)
    nav_panel.grid(row=0, column=0, sticky="ns")
    nav_panel.grid_propagate(False)
    nav_panel.columnconfigure(0, weight=1)

    tkinter.Label(nav_panel, text="设置导航", font=FONT_TITLE,
        bg=C["bg"], fg=C["fgt"]).grid(row=0, column=0, sticky="ew",
        padx=8, pady=(10, 6))
    tkinter.Frame(nav_panel, bg=C["bd"], height=1).grid(
        row=1, column=0, sticky="ew", padx=6, pady=(0, 6))

    _nav_items_frame = tkinter.Frame(nav_panel, bg=C["bg"])
    _nav_items_frame.grid(row=2, column=0, sticky="nsew")
    _nav_items_frame.columnconfigure(0, weight=1)
    nav_panel.rowconfigure(2, weight=1)

    # 预创建导航项
    _nav_row_idx = 0
    for key, icon, label_text in _NAV_ITEMS:
        nav_lbl = tkinter.Label(_nav_items_frame,
            text="  {}  {}".format(icon, label_text),
            font=FONT_BODY, bg=C["bg"], fg=C["fgm"],
            anchor="w", padx=8, pady=5, cursor="hand2")
        nav_lbl.grid(row=_nav_row_idx, column=0, sticky="ew", padx=4, pady=1)
        nav_lbl.bind("<Button-1>", lambda e, k=key: _on_nav_click(k))
        nav_lbl.bind("<Enter>", lambda e, l=nav_lbl, k=key:
            l.configure(bg=C["acl"]) if _nav_active_key != k else None)
        nav_lbl.bind("<Leave>", lambda e, l=nav_lbl, k=key:
            l.configure(bg=C["bg"]) if _nav_active_key != k else None)
        _nav_labels[key] = nav_lbl
        _nav_row_idx += 1

    # ── 右侧内容区 (滚动) ──
    _nav_canvas = tkinter.Canvas(_win, bg=C["bg"], highlightthickness=0, bd=0)
    _scrollbar = tkinter.Scrollbar(_win, orient="vertical", width=8,
        relief="flat", bg=C["bd"], troughcolor=C["bg"], command=_nav_canvas.yview)
    _inner = tkinter.Frame(_nav_canvas, bg=C["bg"])
    _inner.columnconfigure(0, weight=1)

    _inner.bind("<Configure>",
        lambda e: _nav_canvas.configure(scrollregion=_nav_canvas.bbox("all")))

    def _on_canvas_configure(event):
        _nav_canvas.itemconfig("inner", width=event.width)

    _nav_canvas.create_window((0, 0), window=_inner, anchor="nw", tags="inner")
    _nav_canvas.configure(yscrollcommand=_scrollbar.set)
    _nav_canvas.bind("<Configure>", _on_canvas_configure, add="+")

    _nav_canvas.grid(row=0, column=1, sticky="nsew")
    _scrollbar.grid(row=0, column=2, sticky="ns")

    def _on_wheel(event):
        _nav_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
    _win.bind("<MouseWheel>", _on_wheel)
    _inner.bind("<MouseWheel>", _on_wheel)

    # ── 滚轮悬停导航栏时也滚动内容区 (提升导航栏可用性) ──
    nav_panel.bind("<MouseWheel>", _on_wheel)
    _nav_items_frame.bind("<MouseWheel>", _on_wheel)

    PAD = {"padx": 8, "pady": 5}
    PAD = {"padx": 8, "pady": 5}

    # ── 分卡保存 handlers (即时保存框架调用, 保留原逻辑) ──
    def _apply_exec_settings():
        """保存「基础执行」卡配置。"""
        try:
            state.RETRY_MAX = int(retry_var.get())
        except ValueError:
            state.RETRY_MAX = 3; retry_var.set("3")
        try:
            state.RETRY_INTERVAL = float(interval_var.get())
        except ValueError:
            state.RETRY_INTERVAL = 1.0; interval_var.set("1.0")
        try:
            state.IMAGE_TIMEOUT = float(timeout_var.get())
        except ValueError:
            state.IMAGE_TIMEOUT = 5.0; timeout_var.set("5.0")
        state.APP_PROTECT = app_protect_var.get()
        state.CHECK_UPDATE = check_update_var.get()
        state.INPUT_MODE = input_mode_var.get()
        state.USE_DD_DRIVER = (input_mode_var.get() == "dd")
        state.FAILSAFE = failsafe_var.get()
        # 注: engine.retry / retry_interval 由配置变更监听器自动同步 (见 ACRPA._on_config_changed)

    def _apply_ai_settings():
        """保存「AI 增强」卡配置，并按需启停异常检测器。

        提供商/模型/BaseURL 写入 state.AI_*；密钥走 Windows 凭据库
        ACRPA/ai_key/<pid>（绝不落 config.json）并清空 Entry 显示值。
        """
        pid = _label_to_id.get(_prov_var.get(), "") or (getattr(state, "AI_PROVIDER", "") or "")
        state.AI_PROVIDER = pid
        state.AI_MODEL = api_model_var.get().strip()
        state.AI_BASE_URL = api_base_var.get().strip()
        key = api_key_var.get().strip()
        if key:
            if pid:
                if _aic is not None:
                    try:
                        _aic.set_provider_key(pid, key)
                    except Exception:
                        pass
            else:
                # provider 为空 → 回退旧路径（save_config 会写入凭据库 ACRPA/api_key）
                state.API_KEY = key
            # 清空 Entry 显示值，避免明文常驻 UI／被误存（仅在有输入时清一次，防止反复触发）
            try:
                api_key_var.set("")
            except Exception:
                pass
        # api_model 同步策略：仅当选自预设（非 custom）、且 api_model 仍是旧默认值
        # "deepseek-v4-flash" 时，才把新模型名同步进 api_model（详见汇报说明）。
        if (state.AI_MODEL and state.AI_MODEL != state.API_MODEL
                and state.API_MODEL == "deepseek-v4-flash"
                and pid and pid != "custom"):
            state.API_MODEL = state.AI_MODEL
        state.AI_SMART_RETRY = ai_smart_retry_var.get()
        state.AI_ANOMALY_DETECT = ai_anomaly_var.get()
        if state.AI_ANOMALY_DETECT and (state.API_KEY or pid):
            try:
                from ai_enhance import anomaly_detector
                anomaly_detector.enable()
            except Exception:
                pass
        else:
            try:
                from ai_enhance import anomaly_detector
                anomaly_detector.disable()
            except Exception:
                pass

    def _apply_log_settings():
        """保存「日志」卡配置。"""
        state.ENABLE_LOG_SAVING = log_save_var.get()
        try:
            state.LOG_LEVEL = int(log_level_var.get().split("=")[0])
        except (ValueError, IndexError):
            state.LOG_LEVEL = 1
        try:
            state.LOG_RETENTION_DAYS = int(log_retention_var.get())
        except ValueError:
            state.LOG_RETENTION_DAYS = 7

    def _apply_sched_settings():
        """保存「定时调度」卡配置并安全重启调度器。"""
        state.SCHED_ENABLED = state._sched_enabled_var.get()
        try:
            state.SCHED_HOUR = int(_sched_hour_var.get())
        except (ValueError, TypeError):
            state.SCHED_HOUR = 9
        try:
            state.SCHED_MINUTE = int(_sched_minute_var.get())
        except (ValueError, TypeError):
            state.SCHED_MINUTE = 0
        mode_map = {"仅一次": "once", "每天": "daily", "每周": "weekly"}
        state.SCHED_REPEAT_MODE = mode_map.get(_sched_repeat_var.get(), "once")
        state.SCHED_WEEKDAYS = [v.get() for v in _sched_weekday_vars]
        sched_enabled = state.SCHED_ENABLED
        try:
            sched.stop_scheduler()
            state.SCHED_ENABLED = sched_enabled  # restore (stop_scheduler sets it False)
            sched.calc_next_run()
            if sched_enabled:
                sched.start_scheduler(_main_run, state.save_config)
        except Exception as e:
            log1("定时调度保存失败: {}".format(e), "error")

    # 高级设置下拉选项 ↔ state 存储值映射（单一来源，UI 与保存共用）
    _REC_MODE_MAP = {"绝对坐标": "absolute", "相对窗口": "relative"}
    _OCR_BACKEND_MAP = {"自动": "auto", "PaddleOCR": "paddle", "Windows OCR": "winrt", "Tesseract": "tesseract"}

    def _apply_advanced_settings():
        """保存「高级设置」卡配置（运行时由对应模块按需读取）。"""
        try:
            state.MAX_EXECUTION_MINUTES = int(max_minutes_var.get())
        except ValueError:
            state.MAX_EXECUTION_MINUTES = int(max_minutes_var.get())
            state.BOUND_WINDOW_TITLE = bound_window_var.get().strip()
            state.STOP_ON_ERROR = stop_on_error_var.get()
            state.OCR_PREFERRED_BACKEND = _OCR_BACKEND_MAP.get(ocr_backend_var.get(), "auto")
        state.OCR_PADDLE_DIR = ocr_paddle_var.get().strip()
        state.BROWSER_HEADLESS = browser_headless_var.get()
        try:
            state.BROWSER_SLOW_MO = int(browser_slowmo_var.get())
        except ValueError:
            state.BROWSER_SLOW_MO = 0; browser_slowmo_var.set("0")
        try:
            state.SCHED_POLL_INTERVAL = int(sched_poll_var.get())
        except ValueError:
            state.SCHED_POLL_INTERVAL = 30; sched_poll_var.set("30")
        state.DD_DLL_PATH = dd_dll_path_var.get().strip()

    def _export_log():
        import datetime
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        fp = os.path.join(APP_ROOT, "logs", "log_{}.txt".format(ts))
        _tlog.export(fp); log1("日志已导出: {}".format(fp))
        messagebox.showinfo("导出成功", "日志已保存到:\n{}".format(fp))

    # 注册即时保存映射
    _apply_map["exec"] = _apply_exec_settings
    _apply_map["ai"] = _apply_ai_settings
    _apply_map["log"] = _apply_log_settings
    _apply_map["sched"] = _apply_sched_settings
    _apply_map["advanced"] = _apply_advanced_settings

    # ======================================================================
    # ⚙️ 基础执行 card
    # ======================================================================
    card_exec, exec_content, exec_title, _ = _make_collapsible_card(_inner, "基础执行", "⚙️")
    card_exec.grid(row=0, column=0, sticky="ew", **PAD)
    _make_badge("exec", exec_title)
    _register_nav_card("exec", card_exec)

    # === Retry settings row ──
    retry_frame = tkinter.Frame(exec_content, bg=C["bgc"])
    retry_frame.grid(row=0, column=0, sticky="ew", padx=8, pady=2)

    tkinter.Label(retry_frame, text="重试:", font=FONT_BODY, fg=C["fgb"],
        bg=C["bgc"]).pack(side="left", padx=(0,4))
    retry_var = tkinter.StringVar(value=str(state.RETRY_MAX))
    retry_entry = _validate_number(tkinter.Entry(retry_frame, textvariable=retry_var, width=6,
        font=FONT_BODY, relief="solid", bd=1,
        bg=C["ebg"], fg=C["fgb"]), retry_var, "重试次数", 3, int, 0, card_key="exec")
    retry_entry.pack(side="left", padx=(0,6))

    tkinter.Label(retry_frame, text="间隔:", font=FONT_BODY, fg=C["fgb"],
        bg=C["bgc"]).pack(side="left", padx=(0,4))
    interval_var = tkinter.StringVar(value=str(state.RETRY_INTERVAL))
    interval_entry = _validate_number(tkinter.Entry(retry_frame, textvariable=interval_var, width=6,
        font=FONT_BODY, relief="solid", bd=1,
        bg=C["ebg"], fg=C["fgb"]), interval_var, "重试间隔", 1.0, float, 0, card_key="exec")
    interval_entry.pack(side="left", padx=(0,6))

    tkinter.Label(retry_frame, text="识图超时:", font=FONT_BODY, fg=C["fgb"],
        bg=C["bgc"]).pack(side="left", padx=(0,4))
    timeout_var = tkinter.StringVar(value=str(state.IMAGE_TIMEOUT))
    timeout_entry = _validate_number(tkinter.Entry(retry_frame, textvariable=timeout_var, width=6,
        font=FONT_BODY, relief="solid", bd=1,
        bg=C["ebg"], fg=C["fgb"]), timeout_var, "识图超时", 5.0, float, 0, card_key="exec")
    timeout_entry.pack(side="left")

    # 识图超时说明
    tkinter.Label(retry_frame, text="图片识别等待时间，超时自动跳过", font=FONT_TINY,
        fg=C["fgm"], bg=C["bgc"]).pack(side="left", padx=(4, 0))

    # === 执行模式选择 (P1-6: Radiobutton 替代 DD 复选框) ──
    mode_frame = tkinter.Frame(exec_content, bg=C["bgc"])
    mode_frame.grid(row=1, column=0, sticky="ew", padx=8, pady=(2, 2))

    tkinter.Label(mode_frame, text="执行模式:", font=FONT_BODY,
        fg=C["fgb"], bg=C["bgc"]).grid(row=0, column=0, sticky="w", padx=(0, 8), pady=(0, 4))

    _INPUT_MODE_LABELS = {
        "sendinput":  "前台模式 (SendInput) — 兼容性好，需窗口在前台",
        "dd":         "驱动键鼠 (DD) — 高性能、支持后台，需管理员权限",
        "sendmessage": "后台模式 (SendMessage) — 可后台操作，兼容性较差",
    }
    input_mode_var = tkinter.StringVar(value=getattr(state, 'INPUT_MODE', 'sendinput'))
    _mode_row = 1
    for mode_key, mode_label in _INPUT_MODE_LABELS.items():
        rb = ttk.Radiobutton(mode_frame, text=mode_label, variable=input_mode_var,
            value=mode_key)
        rb.grid(row=_mode_row, column=0, sticky="w", padx=(0, 8), pady=1)
        _mode_row += 1

    # DD 驱动信息按钮
    dd_info_btn = tkinter.Label(mode_frame, text="  ℹ️ DD 驱动详情", font=FONT_TINY,
        fg=C["ac"], bg=C["bgc"], cursor="hand2")
    dd_info_btn.grid(row=_mode_row, column=0, sticky="w", pady=(2, 4))

    def _show_dd_info(event):
        messagebox.showinfo("DD 驱动信息",
            "DD 驱动说明:\n\n"
            "✅ 优势:\n"
            "• 输入速度提升 3-5 倍\n"
            "• 支持后台窗口操作\n"
            "• 难以被反自动化检测\n\n"
            "⚠️ 注意:\n"
            "• 需要以管理员身份运行\n"
            "• 仅支持 Windows 系统\n"
            "• 加载失败会自动回退到 PyAutoGUI")
    dd_info_btn.bind("<Button-1>", _show_dd_info)

    # === Hint + App protection ──
    hint_frame = tkinter.Frame(exec_content, bg=C["bgc"])
    hint_frame.grid(row=3, column=0, sticky="ew", padx=8, pady=(0,4))

    tkinter.Label(hint_frame, text="(默认5秒，超时自动跳过)", font=FONT_SMALL,
        fg=C["fgm"], bg=C["bgc"]).pack(side="left")

    app_protect_var = tkinter.BooleanVar(value=state.APP_PROTECT)
    ttk.Checkbutton(hint_frame, text="关闭时确认", variable=app_protect_var).pack(side="right")
    check_update_var = tkinter.BooleanVar(value=state.CHECK_UPDATE)
    ttk.Checkbutton(hint_frame, text="检查更新", variable=check_update_var).pack(side="right", padx=(8,0))

    # Fail-safe setting
    failsafe_frame = tkinter.Frame(exec_content, bg=C["bgc"])
    failsafe_frame.grid(row=2, column=0, sticky="ew", padx=10, pady=(4, 4))

    tkinter.Label(failsafe_frame, text="安全设置:", font=FONT_BODY,
        fg=C["fgb"], bg=C["bgc"]).pack(side="left", padx=(0, 6))

    failsafe_var = tkinter.BooleanVar(value=getattr(state, 'FAILSAFE', True))
    failsafe_cb = ttk.Checkbutton(failsafe_frame, text="启用 PyAutoGUI 故障保护 (鼠标移角落停止)",
        variable=failsafe_var)
    failsafe_cb.pack(side="left", padx=(0, 8))

    def _show_failsafe_info(event):
        info = (
            "PyAutoGUI 故障保护说明:\n\n"
            "启用: 当鼠标移动到屏幕四个角落时，自动停止脚本执行\n"
            "  - 优点: 提供紧急停止机制，防止失控\n"
            "  - 缺点: 可能意外触发，影响自动化流程\n\n"
            "禁用: 关闭角落检测，脚本不会因鼠标位置而停止\n"
            "  - 优点: 适合长时间运行的RPA任务\n"
            "  - 缺点: 需要使用停止按钮或快捷键来终止\n\n"
            "建议: RPA自动化场景可禁用，调试时可启用"
        )
        messagebox.showinfo("故障保护说明", info)

    failsafe_info_btn = tkinter.Label(failsafe_frame, text="ℹ️", font=FONT_ICON,
        fg=C["ac"], bg=C["bgc"], cursor="hand2")
    failsafe_info_btn.pack(side="left")
    failsafe_info_btn.bind("<Button-1>", _show_failsafe_info)

    # 绑定防抖保存 (基础执行卡)
    _track_card_vars("exec", (retry_var, interval_var, timeout_var,
        input_mode_var, app_protect_var, check_update_var, failsafe_var))

    # ======================================================================
    # 🤖 AI 增强 card
    # ======================================================================
    card_ai, ai_content, ai_title, _ = _make_collapsible_card(_inner, "AI 增强", "🤖")
    card_ai.grid(row=1, column=0, sticky="ew", **PAD)
    _make_badge("ai", ai_title)
    _register_nav_card("ai", card_ai)

    ai_frame = tkinter.Frame(ai_content, bg=C["bgc"])
    ai_frame.grid(row=0, column=0, sticky="ew", padx=8, pady=(4, 2))

    tkinter.Label(ai_frame, text="AI增强:", font=FONT_BODY,
        fg=C["fgb"], bg=C["bgc"]).pack(side="left", padx=(0, 6))

    ai_smart_retry_var = tkinter.BooleanVar(value=state.AI_SMART_RETRY)
    ai_smart_cb = ttk.Checkbutton(ai_frame, text="智能重试 (失败时AI分析调整)",
        variable=ai_smart_retry_var)
    ai_smart_cb.pack(side="left", padx=(0, 8))

    # AI 智能重试说明
    ai_desc_frame = tkinter.Frame(ai_content, bg=C["bgc"])
    ai_desc_frame.grid(row=2, column=0, sticky="ew", padx=8, pady=(0, 2))
    tkinter.Label(ai_desc_frame, text="失败时 AI 分析截图并给出修正建议（需配置 API Key）", font=FONT_TINY,
        fg=C["fgm"], bg=C["bgc"]).pack(anchor="w")

    ai_anomaly_var = tkinter.BooleanVar(value=state.AI_ANOMALY_DETECT)
    ai_anomaly_cb = ttk.Checkbutton(ai_frame, text="异常检测 (AI监控执行)",
        variable=ai_anomaly_var)
    ai_anomaly_cb.pack(side="left", padx=(0, 8))

    def _show_ai_info(event):
        info = (
            "AI 增强功能说明:\n\n"
            "智能重试: 命令失败时AI自动分析截图，给出修正建议并重试\n"
            "  - 仅在使用图像识别类命令失败时生效\n"
            "  - 需要配置 API Key\n\n"
            "异常检测: AI实时监控执行过程，检测异常模式并告警\n"
            "  - 每10条命令自动检测一次\n"
            "  - 不需要手动干预，后台静默运行\n\n"
            "[AI] 调试按钮在「执行控制」页面底部"
        )
        messagebox.showinfo("AI 增强功能", info)

    ai_info_btn = tkinter.Label(ai_frame, text="ℹ️", font=FONT_ICON,
        fg=C["ac"], bg=C["bgc"], cursor="hand2")
    ai_info_btn.pack(side="left")
    ai_info_btn.bind("<Button-1>", _show_ai_info)

    # === API config row (自定义 AI 提供商 / 模型 / BaseURL / Key  PR-4) ──
    api_frame = tkinter.Frame(ai_content, bg=C["bgc"])
    api_frame.grid(row=1, column=0, sticky="ew", padx=8, pady=(2,6))
    api_frame.columnconfigure(0, weight=0); api_frame.columnconfigure(1, weight=1)

    try:
        import ai_client as _aic
    except Exception:
        _aic = None

    _prov_by_id = {}
    _label_to_id = {}
    _prov_labels = []
    for _p in (_aic.list_providers() if _aic is not None else []):
        _prov_by_id[_p["id"]] = _p
        _label_to_id[_p["label"]] = _p["id"]
        _prov_labels.append(_p["label"])

    _init_pid = (getattr(state, "AI_PROVIDER", "") or "").strip()
    if _init_pid not in _prov_by_id and _aic is not None:
        try:
            _init_pid = _aic.resolve_provider()
        except Exception:
            _init_pid = ""
    if _init_pid not in _prov_by_id and _prov_labels:
        _init_pid = _label_to_id[_prov_labels[0]]
    _init_p = _prov_by_id.get(_init_pid, {})

    # 提供商
    tkinter.Label(api_frame, text="提供商", font=FONT_BODY,
        fg=C["fgb"], bg=C["bgc"]).grid(row=0, column=0, sticky="w", padx=(0,6), pady=2)
    _prov_var = tkinter.StringVar(
        value=_init_p.get("label", _prov_labels[0] if _prov_labels else ""))
    _prov_combo = ttk.Combobox(api_frame, textvariable=_prov_var, width=26,
        values=_prov_labels, state="readonly")
    _prov_combo.grid(row=0, column=1, sticky="ew", pady=2)

    # 模型（可编辑）
    tkinter.Label(api_frame, text="模型", font=FONT_BODY,
        fg=C["fgb"], bg=C["bgc"]).grid(row=1, column=0, sticky="w", padx=(0,6), pady=2)
    try:
        _init_model = _aic.resolve_model() if _aic is not None else state.API_MODEL
    except Exception:
        _init_model = state.API_MODEL
    api_model_var = tkinter.StringVar(value=_init_model)
    api_model_entry = tkinter.Entry(api_frame, textvariable=api_model_var, width=28,
        font=FONT_SMALL, relief="solid", bd=1, bg=C["ebg"], fg=C["fgb"])
    api_model_entry.grid(row=1, column=1, sticky="ew", pady=2)

    # BaseURL
    tkinter.Label(api_frame, text="BaseURL", font=FONT_BODY,
        fg=C["fgb"], bg=C["bgc"]).grid(row=2, column=0, sticky="w", padx=(0,6), pady=2)
    _init_base = (getattr(state, "AI_BASE_URL", "") or "").strip() or _init_p.get("base_url", "")
    api_base_var = tkinter.StringVar(value=_init_base)
    api_base_entry = tkinter.Entry(api_frame, textvariable=api_base_var, width=28,
        font=FONT_SMALL, relief="solid", bd=1, bg=C["ebg"], fg=C["fgb"])
    api_base_entry.grid(row=2, column=1, sticky="ew", pady=2)

    # API Key（掩码显示）
    tkinter.Label(api_frame, text="API Key", font=FONT_BODY,
        fg=C["fgb"], bg=C["bgc"]).grid(row=3, column=0, sticky="w", padx=(0,6), pady=2)
    try:
        _init_key = (_aic.get_provider_key(_init_pid) if _aic is not None else None) \
            or getattr(state, "API_KEY", "")
    except Exception:
        _init_key = getattr(state, "API_KEY", "")
    api_key_var = tkinter.StringVar(value=_init_key)
    api_key_entry = tkinter.Entry(api_frame, textvariable=api_key_var, width=22,
        font=FONT_SMALL, show="*", relief="solid", bd=1, bg=C["ebg"], fg=C["fgb"])
    api_key_entry.grid(row=3, column=1, sticky="ew", pady=2)

    # 显示/隐藏切换 (眼睛图标)
    def _toggle_api_key_visibility():
        if api_key_entry.cget("show") == "*":
            api_key_entry.config(show="")
            api_eye_btn.config(text="◉")
        else:
            api_key_entry.config(show="*")
            api_eye_btn.config(text="○")
    api_eye_btn = tkinter.Label(api_frame, text="○", font=FONT_ICON,
        bg=C["bgc"], fg=C["fgm"], cursor="hand2")
    api_eye_btn.grid(row=3, column=2, padx=(4, 0))
    api_eye_btn.bind("<Button-1>", lambda e: _toggle_api_key_visibility())
    attach_tooltip(api_eye_btn, "显示/隐藏 API Key")

    # 测试连接 + 结果标签
    ai_test_result = tkinter.Label(api_frame, text="", font=FONT_SMALL,
        fg=C["fgm"], bg=C["bgc"], anchor="w")

    def _on_test_connection():
        try:
            from utils import themed
            ok_color, bad_color = themed("sc"), themed("dg")
        except Exception:
            ok_color, bad_color = C["sc"], C["dg"]
        pid = _label_to_id.get(_prov_var.get(), "")
        ai_test_result.config(text="测试中…（最长 8 秒）", fg=C["fgm"])
        try:
            ai_test_result.update_idletasks()
        except Exception:
            pass
        if _aic is None:
            ai_test_result.config(text="AI 客户端不可用", fg=bad_color)
            return
        try:
            ok, msg = _aic.test_connection(
                provider=pid,
                base_url=api_base_var.get().strip(),
                model=api_model_var.get().strip(),
                api_key=api_key_var.get().strip(),
                timeout=8)
        except Exception as e:
            ok, msg = False, "测试失败：{}".format(e)
        ai_test_result.config(text=msg, fg=(ok_color if ok else bad_color))
        try:
            log1("[AI 测试连接] {}: {}".format("成功" if ok else "失败", msg))
        except Exception:
            pass

    ai_test_btn = tkinter.Button(api_frame, text="测试连接", font=FONT_SMALL,
        bg=C["bgc"], fg=C["fgb"], relief="raised", bd=1, padx=8, pady=1,
        cursor="hand2", activebackground=_darken(C["bgc"]), activeforeground=C["fgb"],
        highlightbackground=C["bd"], highlightthickness=1, command=_on_test_connection)
    ai_test_btn.grid(row=4, column=0, sticky="w", pady=(2, 0))
    ai_test_result.grid(row=4, column=1, columnspan=2, sticky="ew", pady=(2, 0))

    # 添加自定义提供商（自绘 Toplevel）
    def _open_custom_provider_dialog():
        dlg = tkinter.Toplevel(_win)
        dlg.title("自定义 AI 提供商")
        dlg.configure(bg=C["bgc"])
        try:
            dlg.transient(_win)
            dlg.grab_set()
        except Exception:
            pass
        _set_window_icon(dlg)

        frm = tkinter.Frame(dlg, bg=C["bgc"])
        frm.pack(fill="both", expand=True, padx=12, pady=10)
        frm.columnconfigure(1, weight=1)

        v_id = tkinter.StringVar()
        v_name = tkinter.StringVar()
        v_url = tkinter.StringVar()
        v_models = tkinter.StringVar()
        for _i, (_lbl, _var) in enumerate((
                ("提供商 ID", v_id), ("显示名", v_name),
                ("BaseURL", v_url), ("模型列表(逗号分隔)", v_models))):
            tkinter.Label(frm, text=_lbl, font=FONT_BODY, fg=C["fgb"],
                bg=C["bgc"]).grid(row=_i, column=0, sticky="w", padx=(0, 6), pady=3)
            tkinter.Entry(frm, textvariable=_var, font=FONT_SMALL, width=34,
                relief="solid", bd=1, bg=C["ebg"], fg=C["fgb"]).grid(
                row=_i, column=1, sticky="ew", pady=3)

        tkinter.Label(frm, text="已添加的自定义提供商：", font=FONT_BODY,
            fg=C["fgb"], bg=C["bgc"]).grid(row=4, column=0, columnspan=2,
            sticky="w", pady=(8, 2))
        list_frame = tkinter.Frame(frm, bg=C["bgc"])
        list_frame.grid(row=5, column=0, columnspan=2, sticky="ew")
        list_frame.columnconfigure(0, weight=1)

        def _refresh_options():
            """重建主窗口提供商下拉选项（含删/加自定义后同步）。"""
            del _prov_labels[:]
            _prov_by_id.clear()
            _label_to_id.clear()
            for _p in (_aic.list_providers() if _aic is not None else []):
                _prov_by_id[_p["id"]] = _p
                _label_to_id[_p["label"]] = _p["id"]
                _prov_labels.append(_p["label"])
            try:
                _prov_combo.config(values=_prov_labels)
            except Exception:
                pass

        def _select_provider(pid):
            p = _prov_by_id.get(pid)
            if not p:
                return
            _prov_var.set(p["label"])
            try:
                _on_provider_change()
            except Exception:
                pass

        def _refresh_list():
            for w in list_frame.winfo_children():
                w.destroy()
            rows = [c for c in _prov_by_id.values() if c.get("custom")]
            if not rows:
                tkinter.Label(list_frame, text="（暂无）", font=FONT_SMALL,
                    fg=C["fgm"], bg=C["bgc"]).grid(row=0, column=0, sticky="w")
                return
            for _i, c in enumerate(rows):
                tkinter.Label(list_frame, text="{}（{}）".format(c["id"], c["label"]),
                    font=FONT_SMALL, fg=C["fgb"], bg=C["bgc"]).grid(
                    row=_i, column=0, sticky="w", pady=1)
                tkinter.Button(list_frame, text="删除", font=FONT_SMALL,
                    bg=C["bgc"], fg=C["fgb"], relief="raised", bd=1, padx=6, pady=0,
                    cursor="hand2", command=lambda _pid=c["id"]: _remove(_pid)).grid(
                    row=_i, column=1, sticky="e", padx=(6, 0), pady=1)

        def _remove(pid):
            try:
                state.AI_CUSTOM_PROVIDERS = [
                    c for c in (getattr(state, "AI_CUSTOM_PROVIDERS", None) or [])
                    if c.get("id") != pid]
                state.save_config()
            except Exception as e:
                log1("删除自定义提供商失败: {}".format(e), "error")
            _refresh_options()
            _refresh_list()

        def _do_add():
            cid = v_id.get().strip()
            name = v_name.get().strip() or cid
            url = v_url.get().strip()
            models = [m.strip() for m in v_models.get().split(",") if m.strip()]
            if not cid or not url:
                messagebox.showwarning("提示", "提供商 ID 与 BaseURL 为必填", parent=dlg)
                return
            try:
                lst = [c for c in (getattr(state, "AI_CUSTOM_PROVIDERS", None) or [])
                       if c.get("id") != cid]
                lst.append({"id": cid, "name": name, "base_url": url, "models": models})
                state.AI_CUSTOM_PROVIDERS = lst
                state.save_config()
            except Exception as e:
                log1("添加自定义提供商失败: {}".format(e), "error")
                return
            _refresh_options()
            _refresh_list()
            _select_provider(cid)

        btn_row = tkinter.Frame(frm, bg=C["bgc"])
        btn_row.grid(row=6, column=0, columnspan=2, sticky="e", pady=(10, 0))
        tkinter.Button(btn_row, text="添加/更新", font=FONT_SMALL, bg=C["sc"],
            fg="white", relief="raised", bd=1, padx=12, pady=3, cursor="hand2",
            activebackground=_darken(C["sc"]), activeforeground="white",
            command=_do_add).pack(side="left", padx=(0, 6))
        tkinter.Button(btn_row, text="关闭", font=FONT_SMALL, bg=C["bgc"],
            fg=C["fgb"], relief="raised", bd=1, padx=12, pady=3, cursor="hand2",
            activebackground=_darken(C["bgc"]), activeforeground=C["fgb"],
            highlightbackground=C["bd"], highlightthickness=1,
            command=dlg.destroy).pack(side="left")

        _refresh_options()
        _refresh_list()

    ai_custom_btn = tkinter.Button(api_frame, text="添加自定义提供商", font=FONT_SMALL,
        bg=C["bgc"], fg=C["fgb"], relief="raised", bd=1, padx=8, pady=1,
        cursor="hand2", activebackground=_darken(C["bgc"]), activeforeground=C["fgb"],
        highlightbackground=C["bd"], highlightthickness=1,
        command=_open_custom_provider_dialog)
    ai_custom_btn.grid(row=5, column=0, columnspan=3, sticky="w", pady=(4, 0))

    # 切换提供商：自动带出 BaseURL/模型（不覆盖用户手填）+ 载入已存密钥
    def _on_provider_change(event=None):
        pid = _label_to_id.get(_prov_var.get(), "")
        prev_pid = getattr(_on_provider_change, "_prev", None)
        new_p = _prov_by_id.get(pid, {})
        prev_p = _prov_by_id.get(prev_pid, {}) if prev_pid else {}
        cur_base = api_base_var.get().strip()
        prev_base = (prev_p.get("base_url") or "").strip()
        new_base = (new_p.get("base_url") or "").strip()
        if (not cur_base) or (prev_base and cur_base == prev_base):
            api_base_var.set(new_base)
        prev_models = prev_p.get("models") or []
        prev_first = prev_models[0] if prev_models else ""
        new_models = new_p.get("models") or []
        new_first = new_models[0] if new_models else ""
        cur_model = api_model_var.get().strip()
        if (not cur_model) or (prev_first and cur_model == prev_first):
            if new_first:
                api_model_var.set(new_first)
        try:
            api_key_var.set((_aic.get_provider_key(pid) if _aic is not None else None) or "")
        except Exception:
            pass
        try:
            ai_test_result.config(text="", fg=C["fgm"])
        except Exception:
            pass
        _on_provider_change._prev = pid

    _on_provider_change._prev = _init_pid
    _prov_combo.bind("<<ComboboxSelected>>", _on_provider_change)

    # 绑定防抖保存 (AI 增强卡)
    _track_card_vars("ai", (api_key_var, api_model_var, api_base_var, _prov_var,
        ai_smart_retry_var, ai_anomaly_var))

    # ======================================================================
    # ⏰ 定时调度 card
    # ======================================================================
    card_sched, sched_content, sched_title, _ = _make_collapsible_card(_inner, "定时调度", "⏰")
    card_sched.grid(row=2, column=0, sticky="ew", **PAD)
    _make_badge("sched", sched_title)
    _register_nav_card("sched", card_sched)

    # UI vars for scheduler (复用 ACRPA 启动时创建的变量，避免调度器线程引用失效)
    if state._sched_enabled_var is None:
        state._sched_enabled_var = tkinter.BooleanVar(value=state.SCHED_ENABLED)
    _sched_hour_var = tkinter.StringVar(value="{:02d}".format(state.SCHED_HOUR))
    _sched_minute_var = tkinter.StringVar(value="{:02d}".format(state.SCHED_MINUTE))
    _repeat_labels = {"once": "仅一次", "daily": "每天", "weekly": "每周"}
    _sched_repeat_var = tkinter.StringVar(value=_repeat_labels.get(state.SCHED_REPEAT_MODE, "仅一次"))
    _sched_weekday_labels = ["一", "二", "三", "四", "五", "六", "七"]
    _sched_weekday_vars = [tkinter.BooleanVar(value=v) for v in state.SCHED_WEEKDAYS]

    # Enable + time
    enable_frame = tkinter.Frame(sched_content, bg=C["bgc"])
    enable_frame.grid(row=0, column=0, sticky="ew", padx=8, pady=(2,2))

    ttk.Checkbutton(enable_frame, text="启用定时",
        variable=state._sched_enabled_var).pack(side="left", padx=(0,8))

    # 定时调度说明
    sched_desc_frame = tkinter.Frame(sched_content, bg=C["bgc"])
    sched_desc_frame.grid(row=4, column=0, sticky="ew", padx=8, pady=(0, 2))
    tkinter.Label(sched_desc_frame, text="启用后按设定时间自动执行当前脚本", font=FONT_TINY,
        fg=C["fgm"], bg=C["bgc"]).pack(anchor="w")

    tkinter.Label(enable_frame, text="时刻:", font=FONT_BODY,
        fg=C["fgb"], bg=C["bgc"]).pack(side="left", padx=(0,4))
    tf = tkinter.Frame(enable_frame, bg=C["bgc"]); tf.pack(side="left")
    ttk.Combobox(tf, textvariable=_sched_hour_var, values=["{:02d}".format(i) for i in range(24)],
        state="readonly", width=4).pack(side="left")
    tkinter.Label(tf, text=":", font=FONT_BODY,
        fg=C["fgb"], bg=C["bgc"]).pack(side="left", padx=2)
    ttk.Combobox(tf, textvariable=_sched_minute_var, values=["{:02d}".format(i) for i in range(60)],
        state="readonly", width=4).pack(side="left")

    # Repeat + weekdays
    repeat_frame = tkinter.Frame(sched_content, bg=C["bgc"])
    repeat_frame.grid(row=1, column=0, sticky="ew", padx=8, pady=(0,2))

    tkinter.Label(repeat_frame, text="重复:", font=FONT_BODY,
        fg=C["fgb"], bg=C["bgc"]).pack(side="left", padx=(0,4))
    _rep_combo = ttk.Combobox(repeat_frame, textvariable=_sched_repeat_var,
        values=("仅一次", "每天", "每周"), state="readonly", width=8)
    _rep_combo.pack(side="left", padx=(0,8))

    _wf = tkinter.Frame(repeat_frame, bg=C["bgc"]); _wf.pack(side="left")
    tkinter.Label(_wf, text="星期:", font=FONT_BODY,
        fg=C["fgb"], bg=C["bgc"]).pack(side="left", padx=(0,4))
    for i, lt in enumerate(_sched_weekday_labels):
        ttk.Checkbutton(_wf, text=lt, variable=_sched_weekday_vars[i]).pack(side="left", padx=1)

    def _sched_toggle_weekdays(*_):
        if _sched_repeat_var.get() == "每周":
            _wf.pack(side="left")
        else:
            _wf.pack_forget()
    _sched_repeat_var.trace_add("write", _sched_toggle_weekdays)
    _sched_toggle_weekdays()

    # Next run
    next_frame = tkinter.Frame(sched_content, bg=C["bgc"])
    next_frame.grid(row=2, column=0, sticky="ew", padx=8, pady=(0,2))

    tkinter.Label(next_frame, text="下次执行:", font=FONT_BODY,
        fg=C["fgb"], bg=C["bgc"]).pack(side="left", padx=(0,6))
    _sched_next_label = tkinter.Label(next_frame,
        text=state.SCHED_NEXT_RUN if state.SCHED_NEXT_RUN else "--:--",
        font=FONT_BODY, fg=C["ac"], bg=C["bgc"])
    _sched_next_label.pack(side="left")
    # 注入下一次执行标签到调度器 (仅一次模式完成后自动刷新 UI / 取消勾选)
    try:
        sched.set_gui_refs(root, state._sched_enabled_var, _sched_next_label)
    except Exception:
        pass

    # === 管理计划任务按钮 ──
    sched_mgr_frame = tkinter.Frame(sched_content, bg=C["bgc"])
    sched_mgr_frame.grid(row=3, column=0, sticky="ew", padx=8, pady=(2, 6))
    from dialogs import open_sched_manager
    _btn(sched_mgr_frame, "[+] 管理计划任务", open_sched_manager, C["ac"], "white", tip="打开计划任务管理窗口").pack(side="left", padx=(0, 4))
    tkinter.Label(sched_mgr_frame, text="(多任务管理 · 执行日志)",
        font=FONT_SMALL, bg=C["bgc"], fg=C["fgm"]).pack(side="left")

    # 绑定防抖保存 (定时调度卡: 含重复模式切换/星期勾选/启用开关)
    _track_card_vars("sched", (state._sched_enabled_var, _sched_hour_var,
        _sched_minute_var, _sched_repeat_var) + tuple(_sched_weekday_vars))

    # ======================================================================
    # 📹 录制设置 card (P1-4)
    # ======================================================================
    card_record, rec_content, rec_title, _ = _make_collapsible_card(_inner, "录制设置", "📹")
    card_record.grid(row=3, column=0, sticky="ew", **PAD)
    _make_badge("record", rec_title)
    _register_nav_card("record", card_record)

    # 录制模式行
    rec_mode_frame = tkinter.Frame(rec_content, bg=C["bgc"])
    rec_mode_frame.grid(row=0, column=0, sticky="ew", padx=8, pady=(2, 2))
    tkinter.Label(rec_mode_frame, text="坐标模式:", font=FONT_BODY,
        fg=C["fgb"], bg=C["bgc"]).pack(side="left", padx=(0, 8))
    _rec_mode_label = {v: k for k, v in _REC_MODE_MAP.items()}
    recording_mode_var = tkinter.StringVar(value=_rec_mode_label.get(state.RECORDING_MODE, "绝对坐标"))
    ttk.Combobox(rec_mode_frame, textvariable=recording_mode_var,
        values=("绝对坐标", "相对窗口"), state="readonly", width=8).pack(side="left")

    # 录制模式说明
    rec_mode_desc = tkinter.Frame(rec_content, bg=C["bgc"])
    rec_mode_desc.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 4))
    tkinter.Label(rec_mode_desc, text="绝对坐标：以屏幕左上角为原点，不同分辨率下可能偏移",
        font=FONT_TINY, fg=C["fgm"], bg=C["bgc"]).pack(anchor="w")
    tkinter.Label(rec_mode_desc, text="相对窗口：以激活窗口左上角为原点，窗口移动/缩放后仍准确",
        font=FONT_TINY, fg=C["fgm"], bg=C["bgc"]).pack(anchor="w")

    # 停止录制快捷键行
    rec_hotkey_frame = tkinter.Frame(rec_content, bg=C["bgc"])
    rec_hotkey_frame.grid(row=2, column=0, sticky="ew", padx=8, pady=(2, 6))
    tkinter.Label(rec_hotkey_frame, text="停止录制:", font=FONT_BODY,
        fg=C["fgb"], bg=C["bgc"]).pack(side="left", padx=(0, 8))
    rec_stop_hk_var = tkinter.StringVar(value=getattr(state, 'RECORDING_STOP_HOTKEY', 'Ctrl+Alt+F12'))
    rec_stop_hk_lbl = tkinter.Label(rec_hotkey_frame, textvariable=rec_stop_hk_var,
        font=("Consolas", 8, "bold"), bg=C["ac"], fg="white",
        padx=6, pady=2, relief="raised", bd=1)
    rec_stop_hk_lbl.pack(side="left", padx=(0, 6))
    # 录制按钮
    def _record_rec_stop():
        dlg = tkinter.Toplevel(root)
        dlg.title("录制停止录制快捷键")
        dlg.geometry("320x160+500+300")
        dlg.transient(root); dlg.grab_set()
        dlg.configure(bg=C["bgc"])
        _set_window_icon(dlg)
        tkinter.Label(dlg, text="请按下快捷键组合...", font=FONT_BODY,
            bg=C["bgc"], fg=C["fgb"]).pack(pady=(16, 8))
        def _on_key(event):
            parts = []
            if event.state & 0x4: parts.append("Ctrl")
            if event.state & 0x20000: parts.append("Alt")
            if event.state & 0x1: parts.append("Shift")
            key = event.keysym
            if key not in ("Control_L", "Control_R", "Alt_L", "Alt_R", "Shift_L", "Shift_R"):
                parts.append(key.upper())
                combo = "+".join(parts)
                rec_stop_hk_var.set(combo)
                state.RECORDING_STOP_HOTKEY = combo
                state.save_config()
                dlg.destroy()
                _flash_saved("record")
        dlg.bind("<KeyPress>", _on_key)
        dlg.bind("<Escape>", lambda e: dlg.destroy())
        dlg.focus_set()
    _btn(rec_hotkey_frame, "录制", _record_rec_stop, C["sc"], "white",
        tip="录制停止录制的快捷键组合").pack(side="left", padx=(0, 4))
    def _reset_rec_stop():
        rec_stop_hk_var.set("Ctrl+Alt+F12")
        state.RECORDING_STOP_HOTKEY = "Ctrl+Alt+F12"
        state.save_config()
        _flash_saved("record")
    _btn(rec_hotkey_frame, "恢复默认", _reset_rec_stop, C["bgc"], C["fgb"],
        tip="恢复默认快捷键").pack(side="left")

    # 绑定防抖保存
    _track_card_vars("record", (recording_mode_var, rec_stop_hk_var))

    # ── 录制设置 apply ──
    def _apply_record_settings():
        state.RECORDING_MODE = _REC_MODE_MAP.get(recording_mode_var.get(), "absolute")
        state.RECORDING_STOP_HOTKEY = rec_stop_hk_var.get()
    _apply_map["record"] = _apply_record_settings

    # ======================================================================
    # 📋 日志 card
    # ======================================================================
    card_log_cfg, log_content, log_title, _ = _make_collapsible_card(_inner, "日志", "📋")
    card_log_cfg.grid(row=4, column=0, sticky="ew", **PAD)
    _make_badge("log", log_title)
    _register_nav_card("log", card_log_cfg)

    log_save_var = tkinter.BooleanVar(value=state.ENABLE_LOG_SAVING)
    log_save_cb = ttk.Checkbutton(log_content, text="自动保存日志到文件",
        variable=log_save_var)
    log_save_cb.grid(row=0, column=0, sticky="w", padx=8, pady=(2,1))

    log_level_frame = tkinter.Frame(log_content, bg=C["bgc"])
    log_level_frame.grid(row=1, column=0, sticky="ew", padx=8, pady=1)
    tkinter.Label(log_level_frame, text="记录级别", font=FONT_SMALL,
        fg=C["fgm"], bg=C["bgc"]).pack(side="left", padx=(0,6))
    log_level_var = tkinter.StringVar(value=str(state.LOG_LEVEL))
    log_level_combo = ttk.Combobox(log_level_frame, textvariable=log_level_var,
        values=("0=DEBUG", "1=INFO", "2=WARNING", "3=ERROR"),
        state="readonly", width=14)
    log_level_combo.pack(side="left")
    _export_log_btn = _btn(log_level_frame, "导出日志", _export_log, C["ac"], "white", tip="导出运行日志到文件")
    _export_log_btn.pack(side="right")

    log_retention_frame = tkinter.Frame(log_content, bg=C["bgc"])
    log_retention_frame.grid(row=2, column=0, sticky="ew", padx=8, pady=(1,1))
    tkinter.Label(log_retention_frame, text="保留天数", font=FONT_SMALL,
        fg=C["fgm"], bg=C["bgc"]).pack(side="left", padx=(0,6))
    log_retention_var = tkinter.StringVar(value=str(state.LOG_RETENTION_DAYS))
    log_retention_spin = _validate_number(tkinter.Spinbox(log_retention_frame, textvariable=log_retention_var,
        from_=1, to=90, width=5, font=FONT_SMALL,
        bg=C["ebg"], fg=C["fgb"], relief="solid", bd=1), log_retention_var,
        "日志保留天数", 7, int, 0, 90, card_key="log")
    log_retention_spin.pack(side="left")
    tkinter.Label(log_retention_frame, text="天 (0=永久保留)", font=FONT_TINY,
        fg=C["fgm"], bg=C["bgc"]).pack(side="left", padx=(4,0))

    # 日志保存路径提示
    _log_dir_display = os.path.join(APP_ROOT, "logs")
    _log_path_label = tkinter.Label(log_content,
        text="保存位置: {}".format(_log_dir_display),
        font=FONT_TINY, fg=C["fgm"], bg=C["bgc"])
    _log_path_label.grid(row=3, column=0, sticky="w", padx=8, pady=(0,6))

    # 绑定防抖保存 (日志卡)
    _track_card_vars("log", (log_save_var, log_level_var, log_retention_var))

    # ======================================================================
    # 💻 系统 card
    # ======================================================================
    card_system, sys_content, sys_title, _ = _make_collapsible_card(_inner, "系统", "💻")
    card_system.grid(row=5, column=0, sticky="ew", **PAD)
    _make_badge("system", sys_title)
    _register_nav_card("system", card_system)

    # 开机自启动
    auto_start_var = tkinter.BooleanVar(value=state.AUTO_START)
    auto_start_frame = tkinter.Frame(sys_content, bg=C["bgc"])
    auto_start_frame.grid(row=0, column=0, sticky="ew", padx=8, pady=2)

    def _toggle_auto_start():
        state.AUTO_START = auto_start_var.get()
        state.save_config()
        try:
            startup_dir = os.path.join(os.getenv("APPDATA"),
                r"Microsoft\Windows\Start Menu\Programs\Startup")
            shortcut = os.path.join(startup_dir, "ACRPA.lnk")
            if state.AUTO_START:
                # 创建快捷方式
                try:
                    import pythoncom
                    from win32com.client import Dispatch
                    pythoncom.CoInitialize()
                    shell = Dispatch("WScript.Shell")
                    wscript = shell.CreateShortcut(shortcut)
                    if getattr(sys, "frozen", False):
                        wscript.TargetPath = sys.executable
                        wscript.WorkingDirectory = os.path.dirname(sys.executable)
                    else:
                        wscript.TargetPath = sys.executable
                        wscript.Arguments = os.path.join(APP_ROOT, "run.py")
                        wscript.WorkingDirectory = APP_ROOT
                    wscript.Save()
                    pythoncom.CoUninitialize()
                except ImportError:
                    log1("开机自启动需要安装 pywin32", "warning")
            else:
                if os.path.exists(shortcut):
                    os.remove(shortcut)
            _flash_saved("system")
        except Exception as e:
            log1("开机自启动设置失败: {}".format(e), "error")

    ttk.Checkbutton(auto_start_frame, text="开机自启动", variable=auto_start_var,
        command=_toggle_auto_start).pack(side="left", padx=(0, 16))

    # 最小化到系统托盘
    tray_var = tkinter.BooleanVar(value=state.MINIMIZE_TO_TRAY)
    def _toggle_tray():
        try:
            state.MINIMIZE_TO_TRAY = tray_var.get()
            state.save_config()
            if state.MINIMIZE_TO_TRAY:
                if _ensure_tray:
                    _ensure_tray()
            else:
                if _destroy_tray:
                    _destroy_tray()
                try:
                    root.deiconify()
                except Exception:
                    pass
            _flash_saved("system")
        except Exception as e:
            log1("托盘切换异常: {}".format(e), "error")

    ttk.Checkbutton(auto_start_frame, text="关闭时最小化到系统托盘", variable=tray_var,
        command=_toggle_tray).pack(side="left")

    # 紧凑模式: 勾选回到 500x625 旧布局 (重启生效), 照顾小屏/便携习惯用户
    compact_frame = tkinter.Frame(sys_content, bg=C["bgc"])
    compact_frame.grid(row=4, column=0, sticky="ew", padx=8, pady=(2, 0))
    compact_var = tkinter.BooleanVar(value=getattr(state, "COMPACT_MODE", False))
    ttk.Checkbutton(compact_frame, text="紧凑模式（小屏/便携，重启生效）",
        variable=compact_var).pack(side="left")
    tkinter.Label(compact_frame, text="回到 500×625 旧布局", font=FONT_TINY,
        fg=C["fgm"], bg=C["bgc"]).pack(side="left", padx=(6, 0))

    # 帮助窗口模态开关 (阶段1-2): 默认非模态(可边看边操作); 勾选恢复 grab_set 独占输入
    help_modal_var = tkinter.BooleanVar(value=getattr(state, "HELP_MODAL", False))
    def _toggle_help_modal():
        state.HELP_MODAL = help_modal_var.get()
        state.save_config()
        _flash_saved("system")
    ttk.Checkbutton(compact_frame, text="帮助窗口使用模态",
        variable=help_modal_var, command=_toggle_help_modal).pack(
            side="left", padx=(16, 0))
    tkinter.Label(compact_frame, text="默认非模态", font=FONT_TINY,
        fg=C["fgm"], bg=C["bgc"]).pack(side="left", padx=(6, 0))

    # Mini Bar 折叠模式
    mini_bar_frame = tkinter.Frame(sys_content, bg=C["bgc"])
    mini_bar_frame.grid(row=1, column=0, sticky="ew", padx=8, pady=2)

    mini_bar_var = tkinter.BooleanVar(value=state.MINI_BAR_ENABLED)
    def _toggle_mini_bar():
        state.MINI_BAR_ENABLED = mini_bar_var.get()
        state.save_config()
        if not state.MINI_BAR_ENABLED and state.folded:
            # 禁用 Mini Bar 时如果当前处于折叠状态，则展开
            if _toggle_fold:
                _toggle_fold()
        _flash_saved("system")

    ttk.Checkbutton(mini_bar_frame, text="启用 Mini Bar 折叠（标题栏 ⊟ 按钮）",
        variable=mini_bar_var, command=_toggle_mini_bar).pack(side="left")

    # Mini Bar 定制 (P2-8): 宽度 + 高度 + 透明度
    mb_custom_frame = tkinter.Frame(sys_content, bg=C["bgc"])
    mb_custom_frame.grid(row=2, column=0, sticky="ew", padx=8, pady=(2, 4))
    tkinter.Label(mb_custom_frame, text="面板宽度:", font=FONT_SMALL,
        fg=C["fgm"], bg=C["bgc"]).pack(side="left", padx=(0, 4))
    mb_width_var = tkinter.StringVar(value=str(state.MINI_BAR_WIDTH))
    mb_width_spin = _validate_number(tkinter.Spinbox(mb_custom_frame, textvariable=mb_width_var,
        from_=380, to=900, increment=10, width=5, font=FONT_SMALL,
        bg=C["ebg"], fg=C["fgb"], relief="solid", bd=1), mb_width_var,
        "Mini Bar 宽度", 430, int, 380, 900, card_key="system")
    mb_width_spin.pack(side="left", padx=(0, 4))
    tkinter.Label(mb_custom_frame, text="px (380-900)", font=FONT_SMALL,
        fg=C["fgm"], bg=C["bgc"]).pack(side="left", padx=(0, 10))
    tkinter.Label(mb_custom_frame, text="高度:", font=FONT_SMALL,
        fg=C["fgm"], bg=C["bgc"]).pack(side="left", padx=(0, 4))
    mb_height_var = tkinter.StringVar(value=str(state.MINI_BAR_HEIGHT))
    mb_height_combo = ttk.Combobox(mb_custom_frame, textvariable=mb_height_var,
        values=(24, 28, 30, 34, 38, 42, 48), state="readonly", width=4)
    mb_height_combo.pack(side="left", padx=(0, 4))
    tkinter.Label(mb_custom_frame, text="px 高度（24–48，步进 4）", font=FONT_SMALL,
        fg=C["fgm"], bg=C["bgc"]).pack(side="left", padx=(0, 10))
    tkinter.Label(mb_custom_frame, text="透明度:", font=FONT_SMALL,
        fg=C["fgm"], bg=C["bgc"]).pack(side="left", padx=(0, 4))
    mb_opacity_var = tkinter.StringVar(value=str(state.MINI_BAR_OPACITY))
    mb_opacity_spin = _validate_number(tkinter.Spinbox(mb_custom_frame, textvariable=mb_opacity_var,
        from_=30, to=100, increment=5, width=4, font=FONT_SMALL,
        bg=C["ebg"], fg=C["fgb"], relief="solid", bd=1), mb_opacity_var,
        "Mini Bar 透明度", 80, int, 30, 100, card_key="system")
    mb_opacity_spin.pack(side="left", padx=(0, 4))
    tkinter.Label(mb_custom_frame, text="% (30-100)", font=FONT_SMALL,
        fg=C["fgm"], bg=C["bgc"]).pack(side="left")

    # ── 界面缩放 (pt 制) ── 并入本卡: 走 _apply_system_settings + _track_card_vars
    ui_scale_frame = tkinter.Frame(sys_content, bg=C["bgc"])
    ui_scale_frame.grid(row=6, column=0, sticky="ew", padx=8, pady=(2, 6))
    tkinter.Label(ui_scale_frame, text="界面缩放", font=FONT_SMALL,
        fg=C["fgm"], bg=C["bgc"]).pack(side="left", padx=(0, 4))
    _UI_SCALE_STEPS = ("0.8", "0.9", "1.0", "1.1", "1.2", "1.3", "1.5")
    ui_scale_var = tkinter.StringVar(value=str(float(getattr(state, "UI_SCALE", 1.0))))
    ui_scale_combo = ttk.Combobox(ui_scale_frame, textvariable=ui_scale_var,
        values=_UI_SCALE_STEPS, state="readonly", width=5)
    ui_scale_combo.pack(side="left", padx=(0, 8))

    def _on_ui_scale_selected(event=None):
        """切换档位: 立即 set_ui_scale (刷新全部命名字体) + 提示需重启才完全生效。"""
        try:
            eff = utils.set_ui_scale(float(ui_scale_var.get()))
        except (TypeError, ValueError):
            eff = utils.current_ui_scale()
        ui_scale_var.set(str(eff))
        log1("界面缩放已切换到 {} 档（部分界面需重启生效）".format(eff))
        try:
            messagebox.showinfo("界面缩放",
                "已切换到 {} 档。\n\n缩放仅影响字体与关键尺寸；\n"
                "主窗口几何与已创建控件的尺寸不会自动重排，建议重启程序后完全生效。".format(eff))
        except Exception:
            pass

    ui_scale_combo.bind("<<ComboboxSelected>>", _on_ui_scale_selected)
    tkinter.Label(ui_scale_frame, text="缩放仅影响字体与关键尺寸；调整后建议重启程序。",
        font=FONT_TINY, fg=C["fgm"], bg=C["bgc"]).pack(side="left")

    # 「系统」整卡统一收口: 一次写回本卡全部 state 键并做数值夹取/吸附。
    # 修复 D-1/D-2 —— 旧 _apply_mb_settings 从未被调用, 导致 Mini Bar
    # 宽度/高度/透明度只是安慰剂 (改了不生效)。
    def _apply_system_settings():
        state.AUTO_START = auto_start_var.get()
        state.MINIMIZE_TO_TRAY = tray_var.get()
        state.COMPACT_MODE = compact_var.get()
        state.MINI_BAR_ENABLED = mini_bar_var.get()
        try:
            state.HELP_MODAL = help_modal_var.get()
        except Exception:
            pass
        try:
            w = int(mb_width_var.get())
        except (TypeError, ValueError):
            w = state.MINI_BAR_WIDTH
        state.MINI_BAR_WIDTH = max(380, min(900, w))
        try:
            h = int(mb_height_var.get())
        except (TypeError, ValueError):
            h = state.MINI_BAR_HEIGHT
        # 夹取到 [24,48] 并吸附到 4 的倍数
        state.MINI_BAR_HEIGHT = int(round(max(24, min(48, h)) / 4.0) * 4)
        try:
            op = int(mb_opacity_var.get())
        except (TypeError, ValueError):
            op = state.MINI_BAR_OPACITY
        state.MINI_BAR_OPACITY = max(30, min(100, op))
        # 界面缩放档位持久化 (字体已在切换回调里 fontconfigure 过, 此处仅落盘)
        try:
            state.UI_SCALE = max(0.8, min(1.5, float(ui_scale_var.get())))
        except (TypeError, ValueError):
            state.UI_SCALE = 1.0
        # 本卡内由独立控件写入的快捷键键 (hotkey_vars 稍后构建, 调用时才求值)
        try:
            for _name, (_v, _sk) in hotkey_vars.items():
                if _sk:
                    setattr(state, _sk.upper(), _v.get())
        except Exception:
            pass

    _apply_map["system"] = _apply_system_settings

    # 快捷键列表视图 (P0-3)
    hotkey_title_frame = tkinter.Frame(sys_content, bg=C["bgc"])
    hotkey_title_frame.grid(row=3, column=0, sticky="ew", padx=8, pady=(4, 2))
    tkinter.Label(hotkey_title_frame, text="全局快捷键", font=FONT_BODY,
        fg=C["fgb"], bg=C["bgc"]).pack(side="left")

    # 快捷键注册表 [[功能, state_key, 默认值], ...]
    _HOTKEY_DEFS = [
        ("执行默认配置",   "HOTKEY_RUN",   "F5"),
        ("单步执行",       None,           "F10"),
        ("暂停/继续执行",  "HOTKEY_PAUSE", "F6"),
        ("取消执行",       "HOTKEY_STOP",  "Shift+F5"),
        ("强制关闭",       None,           "Alt+Shift+F4"),
        ("截图",           None,           "Ctrl+Shift+A"),
    ]

    # 快捷键录制弹窗 (复用原有逻辑)
    def _record_hotkey(var, key_name):
        dlg = tkinter.Toplevel(root)
        dlg.title("录制快捷键 - {}".format(key_name))
        dlg.geometry("320x160+500+300")
        dlg.transient(root); dlg.grab_set()
        dlg.configure(bg=C["bgc"])
        _set_window_icon(dlg)
        tkinter.Label(dlg, text="请按下快捷键组合...\n(支持 Ctrl/Alt/Shift + 字母/数字/F1-F12)",
            font=FONT_BODY, bg=C["bgc"], fg=C["fgb"]).pack(pady=(16, 8))

        def _on_key(event):
            parts = []
            if event.state & 0x4: parts.append("Ctrl")
            if event.state & 0x20000: parts.append("Alt")
            if event.state & 0x1: parts.append("Shift")
            key = event.keysym
            if key not in ("Control_L", "Control_R", "Alt_L", "Alt_R",
                           "Shift_L", "Shift_R"):
                parts.append(key.upper())
                combo = "+".join(parts)
                var.set(combo)
                if key_name:
                    setattr(state, key_name.upper(), combo)
                state.save_config()
                dlg.destroy()
                _flash_saved("system")
        dlg.bind("<KeyPress>", _on_key)
        dlg.bind("<Escape>", lambda e: dlg.destroy())
        dlg.focus_set()

    # 快捷键表格
    hotkey_table = tkinter.Frame(sys_content, bg=C["bgc"])
    hotkey_table.grid(row=4, column=0, sticky="ew", padx=8, pady=(0, 2))
    hotkey_table.columnconfigure(1, weight=1)

    # 表头
    for ci, (txt, w) in enumerate([
        ("功能", 16), ("当前绑定", 16), ("操作", 12)
    ]):
        tkinter.Label(hotkey_table, text=txt, font=FONT_SMALL,
            fg=C["fgm"], bg=C["bgc"], anchor="w", padx=4).grid(
            row=0, column=ci, sticky="w", padx=(0, 2))

    # 分隔线
    tkinter.Frame(hotkey_table, bg=C["bd"], height=1).grid(
        row=1, column=0, columnspan=3, sticky="ew", pady=2)

    # 快捷键行
    hotkey_vars = {}  # 保存 var 引用供后续绑定
    for ri, (func_name, state_key, default_val) in enumerate(_HOTKEY_DEFS):
        r = ri + 2  # 跳过表头和分隔线
        curr_val = getattr(state, state_key, "") if state_key else default_val
        if not curr_val:
            curr_val = default_val

        tkinter.Label(hotkey_table, text=func_name, font=FONT_SMALL,
            fg=C["fgb"], bg=C["bgc"], anchor="w", padx=4).grid(
            row=r, column=0, sticky="w")

        var = tkinter.StringVar(value=curr_val)
        hotkey_vars[func_name] = (var, state_key)
        key_lbl = tkinter.Label(hotkey_table, textvariable=var,
            font=("Consolas", 8, "bold"), bg=C["ac"], fg="white",
            padx=6, pady=2, relief="raised", bd=1)
        key_lbl.grid(row=r, column=1, sticky="w", padx=(0, 4))

        btn_frame = tkinter.Frame(hotkey_table, bg=C["bgc"])
        btn_frame.grid(row=r, column=2, sticky="w")
        rec_btn = tkinter.Label(btn_frame, text="录制", font=FONT_TINY,
            bg=C["sc"], fg="white", padx=4, pady=1, cursor="hand2",
            relief="raised", bd=1)
        rec_btn.pack(side="left", padx=1)
        rec_btn.bind("<Button-1>", lambda e, v=var, k=state_key:
            _record_hotkey(v, k or func_name))
        clr_btn = tkinter.Label(btn_frame, text="清除", font=FONT_TINY,
            bg=C["dg"], fg="white", padx=4, pady=1, cursor="hand2",
            relief="raised", bd=1)
        clr_btn.pack(side="left", padx=1)
        clr_btn.bind("<Button-1>", lambda e, v=var, k=state_key, d=default_val:
            (v.set(default_val), setattr(state, k.upper(), default_val) if k else None,
             state.save_config(), _flash_saved("system")))

    # 底部提示
    tkinter.Label(sys_content,
        text="提示：支持 Ctrl/Alt/Shift/Win + 字母/数字/F1-F12；录制的快捷键在所有应用中全局生效",
        font=FONT_TINY, fg=C["fgm"], bg=C["bgc"]).grid(
        row=5, column=0, sticky="w", padx=8, pady=(0, 6))

    # 系统卡: 任一控件改动 → 600ms 防抖后走 _apply_system_settings 即时落盘
    _track_card_vars("system", (auto_start_var, tray_var, compact_var, mini_bar_var,
                                mb_width_var, mb_height_var, mb_opacity_var,
                                ui_scale_var))

    # ======================================================================
    # ⚡ 快速操作 card (P2-7/P2-9)
    # ======================================================================
    card_quick, quick_content, quick_title, _ = _make_collapsible_card(_inner, "快速操作", "⚡")
    card_quick.grid(row=6, column=0, sticky="ew", **PAD)
    _register_nav_card("quick", card_quick)
    quick_content.columnconfigure(0, weight=1)

    # ── 关于 ACRPA (P2-7) — 版本号从 VERSION 文件统一获取 (每次打开实时读取) ──
    from version_info import get_version, get_manifest_path
    about_frame = tkinter.Frame(quick_content, bg=C["bgc"])
    about_frame.grid(row=0, column=0, columnspan=2, sticky="ew", padx=8, pady=(4, 4))

    about_head = tkinter.Frame(about_frame, bg=C["bgc"])
    about_head.pack(fill="x")
    tkinter.Label(about_head, text="ACRPA v{}".format(get_version()), font=FONT_TITLE,
        fg=C["ac"], bg=C["bgc"]).pack(side="left")

    def _open_update():
        try:
            import dialogs
            dialogs.show_update_dialog(force_check=True)
        except Exception as e:
            messagebox.showerror("检查更新", "无法打开更新窗口:\n{}".format(e))

    _btn(about_head, "⇪ 检查更新", _open_update,
         tip="检查 GitHub 上的最新版本并直接下载 (支持自动重启更新)"
         ).pack(side="right")

    tkinter.Label(about_frame, text="自动化工作流工具 — 脚本编辑 | 执行控制 | 动作录制 | 模板共创",
        font=FONT_TINY, fg=C["fgm"], bg=C["bgc"]).pack(anchor="w")
    tkinter.Label(about_frame, text="仅供学习研究使用，使用者自行承担风险",
        font=FONT_TINY, fg=C["dg"], bg=C["bgc"]).pack(anchor="w")
    # 版本来源路径: 打包版与源码版的 VERSION 位置不同, 排查"版本号不对"时先看这里
    tkinter.Label(about_frame, text="版本来源: {}".format(get_manifest_path() or "(内置回退值)"),
        font=FONT_TINY, fg=C["fgm"], bg=C["bgc"],
        wraplength=520, justify="left").pack(anchor="w")

    # ── 分级重置 (P2-9) ──
    def _reset_defaults(all_settings=False):
        confirm_msg = "确定要恢复所有设置为默认值吗？\n此操作不可撤销。" if all_settings else \
            "确定要恢复本页设置为默认值吗？"
        if messagebox.askyesno("确认", confirm_msg):
            defaults = {k: d for k, d, _ in state._config_schema}
            for key, val in defaults.items():
                setattr(state, key.upper(), val)
            state.save_config()
            log1("已恢复默认设置")
            _close()
            root.after(200, open_settings_window)
            show_toast(root, "已恢复默认设置并应用", "success")

    reset_btn_frame = tkinter.Frame(quick_content, bg=C["bgc"])
    reset_btn_frame.grid(row=1, column=0, columnspan=2, sticky="ew", padx=4, pady=(2, 2))
    _btn(reset_btn_frame, "恢复全部默认", lambda: _reset_defaults(all_settings=True),
        C["dg"], "white", tip="恢复所有设置为默认值").pack(side="left", padx=(0, 4))
    _btn(reset_btn_frame, "恢复本页默认", lambda: _reset_defaults(all_settings=False),
        C["wn"], "white", tip="恢复当前页设置为默认值").pack(side="left")

    # ======================================================================
    # 🔧 高级设置 card (可折叠: 执行 / 录制 / OCR / 浏览器 / 定时 / DD)
    # ======================================================================
    card_advanced, adv_content, adv_title, _ = _make_collapsible_card(_inner, "高级设置", "🔧")
    card_advanced.grid(row=7, column=0, sticky="ew", **PAD)
    _make_badge("advanced", adv_title)
    _register_nav_card("advanced", card_advanced)

    # === 执行增强行：最大执行时间 + 绑定窗口 ===
    adv_exec_frame = tkinter.Frame(adv_content, bg=C["bgc"])
    adv_exec_frame.grid(row=0, column=0, sticky="ew", padx=8, pady=2)
    max_minutes_var = tkinter.StringVar(value=str(state.MAX_EXECUTION_MINUTES))
    tkinter.Label(adv_exec_frame, text="最大执行:", font=FONT_BODY,
        fg=C["fgb"], bg=C["bgc"]).pack(side="left", padx=(0,4))
    max_minutes_spin = _validate_number(tkinter.Spinbox(adv_exec_frame, textvariable=max_minutes_var, from_=0, to=1440, width=5,
        font=FONT_BODY, bg=C["ebg"], fg=C["fgb"],
        relief="solid", bd=1), max_minutes_var, "最大执行时间", 0, int, 0, 1440, card_key="advanced")
    max_minutes_spin.pack(side="left", padx=(0,4))
    tkinter.Label(adv_exec_frame, text="分钟 (0=不限)", font=FONT_SMALL,
        fg=C["fgm"], bg=C["bgc"]).pack(side="left", padx=(0,12))
    tkinter.Label(adv_exec_frame, text="到达时限后自动停止", font=FONT_TINY,
        fg=C["fgm"], bg=C["bgc"]).pack(side="left", padx=(0, 12))
    bound_window_var = tkinter.StringVar(value=state.BOUND_WINDOW_TITLE)
    tkinter.Label(adv_exec_frame, text="绑定窗口:", font=FONT_BODY,
        fg=C["fgb"], bg=C["bgc"]).pack(side="left", padx=(0,4))
    tkinter.Entry(adv_exec_frame, textvariable=bound_window_var, width=16,
        font=FONT_BODY, bg=C["ebg"], fg=C["fgb"],
        relief="solid", bd=1).pack(side="left")

    # === 出错处理行 ===
    adv_opt_frame = tkinter.Frame(adv_content, bg=C["bgc"])
    adv_opt_frame.grid(row=1, column=0, sticky="ew", padx=8, pady=2)
    stop_on_error_var = tkinter.BooleanVar(value=state.STOP_ON_ERROR)
    ttk.Checkbutton(adv_opt_frame, text="出错立即停止", variable=stop_on_error_var).pack(side="left")

    # === OCR 后端行 (P1-5 增强) ===
    adv_ocr_frame = tkinter.Frame(adv_content, bg=C["bgc"])
    adv_ocr_frame.grid(row=2, column=0, sticky="ew", padx=8, pady=2)
    tkinter.Label(adv_ocr_frame, text="OCR后端:", font=FONT_BODY,
        fg=C["fgb"], bg=C["bgc"]).pack(side="left", padx=(0,4))
    _backend_label = {v: k for k, v in _OCR_BACKEND_MAP.items()}
    ocr_backend_var = tkinter.StringVar(value=_backend_label.get(state.OCR_PREFERRED_BACKEND, "自动"))
    ttk.Combobox(adv_ocr_frame, textvariable=ocr_backend_var,
        values=("自动", "PaddleOCR", "Windows OCR", "Tesseract"),
        state="readonly", width=11).pack(side="left", padx=(0,8))
    tkinter.Label(adv_ocr_frame, text="Paddle模型目录:", font=FONT_BODY,
        fg=C["fgb"], bg=C["bgc"]).pack(side="left", padx=(0,4))
    ocr_paddle_var = tkinter.StringVar(value=state.OCR_PADDLE_DIR)
    tkinter.Entry(adv_ocr_frame, textvariable=ocr_paddle_var, width=22,
        font=FONT_SMALL, bg=C["ebg"], fg=C["fgb"],
        relief="solid", bd=1).pack(side="left", padx=(0,4))
    _ocr_browse_btn = _btn(adv_ocr_frame, "浏览", lambda: ocr_paddle_var.set(filedialog.askdirectory(
        title="选择 PaddleOCR 模型目录", initialdir=APP_ROOT) or ocr_paddle_var.get()),
        C["ac"], "white", tip="选择 PaddleOCR 模型目录")
    _ocr_browse_btn.pack(side="left")

    # OCR 增强 (P1-5): 状态 + 预热 + 线程数 + 重载
    ocr_status_frame = tkinter.Frame(adv_content, bg=C["bgc"])
    ocr_status_frame.grid(row=3, column=0, sticky="ew", padx=8, pady=2)
    ocr_status_var = tkinter.StringVar(value="未初始化")
    tkinter.Label(ocr_status_frame, text="OCR 状态:", font=FONT_SMALL,
        fg=C["fgm"], bg=C["bgc"]).pack(side="left", padx=(0, 6))
    tkinter.Label(ocr_status_frame, textvariable=ocr_status_var, font=FONT_SMALL,
        fg=C["sc"], bg=C["bgc"]).pack(side="left", padx=(0, 8))
    def _reload_ocr():
        try:
            from ocr_backend import get_ocr_engine
            get_ocr_engine(force_reload=True)
            ocr_status_var.set("已重载")
            log1("OCR 引擎已重载")
            show_toast(root, "OCR 引擎已重载", "success")
        except Exception as e:
            ocr_status_var.set("重载失败")
            log1("OCR 重载失败: {}".format(e), "error")
    _btn(ocr_status_frame, "应用并重载", _reload_ocr, C["ac"], "white",
        tip="重新加载 OCR 引擎").pack(side="left", padx=(0, 8))

    # OCR 预热 + 线程数
    ocr_preload_var = tkinter.BooleanVar(value=getattr(state, 'OCR_PRELOAD', False))
    ttk.Checkbutton(ocr_status_frame, text="启动时预热", variable=ocr_preload_var).pack(side="left", padx=(0, 12))
    tkinter.Label(ocr_status_frame, text="CPU线程:", font=FONT_SMALL,
        fg=C["fgm"], bg=C["bgc"]).pack(side="left", padx=(0, 4))
    ocr_threads_var = tkinter.StringVar(value=str(getattr(state, 'OCR_THREADS', 8)))
    ocr_threads_spin = _validate_number(tkinter.Spinbox(ocr_status_frame, textvariable=ocr_threads_var,
        from_=1, to=32, width=4, font=FONT_SMALL,
        bg=C["ebg"], fg=C["fgb"], relief="solid", bd=1), ocr_threads_var,
        "OCR 线程数", 8, int, 1, 32, card_key="advanced")
    ocr_threads_spin.pack(side="left")

    # OCR 状态自动检测
    def _detect_ocr_status():
        try:
            from ocr_backend import get_ocr_engine
            engine = get_ocr_engine()
            if engine:
                backend_name = getattr(engine, 'backend_name', '自动')
                ocr_status_var.set("已就绪 ({})".format(backend_name))
            else:
                ocr_status_var.set("未初始化")
        except Exception:
            ocr_status_var.set("未安装")
    _win.after(500, _detect_ocr_status)
    def _apply_ocr_extra():
        state.OCR_PRELOAD = ocr_preload_var.get()
        try: state.OCR_THREADS = int(ocr_threads_var.get())
        except ValueError: state.OCR_THREADS = 8

    # === 浏览器行 ===
    adv_br_frame = tkinter.Frame(adv_content, bg=C["bgc"])
    adv_br_frame.grid(row=4, column=0, sticky="ew", padx=8, pady=2)
    browser_headless_var = tkinter.BooleanVar(value=state.BROWSER_HEADLESS)
    ttk.Checkbutton(adv_br_frame, text="浏览器无头模式", variable=browser_headless_var).pack(side="left", padx=(0,16))
    tkinter.Label(adv_br_frame, text="慢放:", font=FONT_BODY,
        fg=C["fgb"], bg=C["bgc"]).pack(side="left", padx=(0,4))
    browser_slowmo_var = tkinter.StringVar(value=str(state.BROWSER_SLOW_MO))
    browser_slowmo_spin = _validate_number(tkinter.Spinbox(adv_br_frame, textvariable=browser_slowmo_var, from_=0, to=5000,
        increment=50, width=6, font=FONT_BODY,
        bg=C["ebg"], fg=C["fgb"], relief="solid", bd=1), browser_slowmo_var,
        "浏览器慢放", 0, int, 0, 5000, card_key="advanced")
    browser_slowmo_spin.pack(side="left", padx=(0,4))
    tkinter.Label(adv_br_frame, text="ms (0=最快)", font=FONT_SMALL,
        fg=C["fgm"], bg=C["bgc"]).pack(side="left")

    # === 定时轮询 + DD DLL 行 ===
    adv_sys_frame = tkinter.Frame(adv_content, bg=C["bgc"])
    adv_sys_frame.grid(row=5, column=0, sticky="ew", padx=8, pady=(2,2))
    tkinter.Label(adv_sys_frame, text="调度轮询:", font=FONT_BODY,
        fg=C["fgb"], bg=C["bgc"]).pack(side="left", padx=(0,4))
    sched_poll_var = tkinter.StringVar(value=str(state.SCHED_POLL_INTERVAL))
    sched_poll_spin = _validate_number(tkinter.Spinbox(adv_sys_frame, textvariable=sched_poll_var, from_=5, to=600,
        increment=5, width=5, font=FONT_BODY,
        bg=C["ebg"], fg=C["fgb"], relief="solid", bd=1), sched_poll_var,
        "调度轮询间隔", 30, int, 5, 600, card_key="advanced")
    sched_poll_spin.pack(side="left", padx=(0,4))
    tkinter.Label(adv_sys_frame, text="秒", font=FONT_SMALL,
        fg=C["fgm"], bg=C["bgc"]).pack(side="left", padx=(0,12))
    tkinter.Label(adv_sys_frame, text="DD DLL路径:", font=FONT_BODY,
        fg=C["fgb"], bg=C["bgc"]).pack(side="left", padx=(0,4))
    dd_dll_path_var = tkinter.StringVar(value=state.DD_DLL_PATH)
    tkinter.Entry(adv_sys_frame, textvariable=dd_dll_path_var, width=20,
        font=FONT_SMALL, bg=C["ebg"], fg=C["fgb"],
        relief="solid", bd=1).pack(side="left", padx=(0,4))
    _dd_browse_btn = _btn(adv_sys_frame, "浏览", lambda: dd_dll_path_var.set(filedialog.askopenfilename(
        title="选择 DD 驱动 DLL", filetypes=[("DLL", "*.dll"), ("All", "*.*")],
        initialdir=APP_ROOT) or dd_dll_path_var.get()),
        C["ac"], "white", tip="选择 DD 驱动 DLL 文件")
    _dd_browse_btn.pack(side="left")

    tkinter.Label(adv_content,
        text="提示：部分高级设置（浏览器/OCR/DD 后端、绑定窗口）保存后将在下次启动或执行对应功能时生效",
        font=FONT_SMALL, bg=C["bgc"], fg=C["fgm"]).grid(
        row=6, column=0, sticky="w", padx=10, pady=(0,6))

    # 绑定防抖保存 (高级设置卡)
    _track_card_vars("advanced", (max_minutes_var, bound_window_var, stop_on_error_var,
        ocr_backend_var, ocr_paddle_var, ocr_preload_var, ocr_threads_var,
        browser_headless_var, browser_slowmo_var, sched_poll_var, dd_dll_path_var))

    # ======================================================================
    # 🌐 网络互联 card (NetLink 多设备互联)
    # ======================================================================
    card_netlink, nl_content, nl_title, _ = _make_collapsible_card(_inner, "网络互联", "🌐")
    card_netlink.grid(row=8, column=0, sticky="ew", **PAD)
    _make_badge("netlink", nl_title)
    _register_nav_card("netlink", card_netlink)

    # 启用开关 + 自动发现
    nl_switch_frame = tkinter.Frame(nl_content, bg=C["bgc"])
    nl_switch_frame.grid(row=0, column=0, sticky="ew", padx=8, pady=(2, 1))
    nl_enabled_var = tkinter.BooleanVar(value=getattr(state, "NETLINK_ENABLED", False))
    ttk.Checkbutton(nl_switch_frame, text="启用设备互联",
        variable=nl_enabled_var).pack(side="left", padx=(0, 16))
    nl_auto_var = tkinter.BooleanVar(value=getattr(state, "NETLINK_AUTODISCOVER", True))
    ttk.Checkbutton(nl_switch_frame, text="UDP 自动发现",
        variable=nl_auto_var).pack(side="left")

    # TCP 端口 / 发现端口 / 设备名
    nl_addr_frame = tkinter.Frame(nl_content, bg=C["bgc"])
    nl_addr_frame.grid(row=1, column=0, sticky="ew", padx=8, pady=1)
    tkinter.Label(nl_addr_frame, text="TCP端口", font=FONT_SMALL,
        fg=C["fgm"], bg=C["bgc"]).pack(side="left", padx=(0, 4))
    nl_port_var = tkinter.StringVar(value=str(getattr(state, "NETLINK_PORT", 19710)))
    _validate_number(tkinter.Entry(nl_addr_frame, textvariable=nl_port_var, width=7,
        font=FONT_BODY, relief="solid", bd=1, bg=C["ebg"], fg=C["fgb"]),
        nl_port_var, "互联 TCP 端口", 19710, int, 1, 65535, card_key="netlink").pack(
        side="left", padx=(0, 10))
    tkinter.Label(nl_addr_frame, text="发现端口", font=FONT_SMALL,
        fg=C["fgm"], bg=C["bgc"]).pack(side="left", padx=(0, 4))
    nl_dport_var = tkinter.StringVar(value=str(getattr(state, "NETLINK_DISCOVERY_PORT", 19711)))
    _validate_number(tkinter.Entry(nl_addr_frame, textvariable=nl_dport_var, width=7,
        font=FONT_BODY, relief="solid", bd=1, bg=C["ebg"], fg=C["fgb"]),
        nl_dport_var, "互联发现端口", 19711, int, 1, 65535, card_key="netlink").pack(
        side="left", padx=(0, 10))
    tkinter.Label(nl_addr_frame, text="设备名", font=FONT_SMALL,
        fg=C["fgm"], bg=C["bgc"]).pack(side="left", padx=(0, 4))
    nl_name_var = tkinter.StringVar(value=getattr(state, "NETLINK_DEVICE_NAME", "") or "")
    tkinter.Entry(nl_addr_frame, textvariable=nl_name_var, width=16,
        font=FONT_BODY, relief="solid", bd=1, bg=C["ebg"], fg=C["fgb"]).pack(side="left")

    # 权限级别
    nl_perm_frame = tkinter.Frame(nl_content, bg=C["bgc"])
    nl_perm_frame.grid(row=2, column=0, sticky="ew", padx=8, pady=1)
    tkinter.Label(nl_perm_frame, text="权限级别", font=FONT_SMALL,
        fg=C["fgm"], bg=C["bgc"]).pack(side="left", padx=(0, 4))
    _NL_PERM_MAP = {"仅观察": "observe", "允许操控": "control", "允许接收脚本": "script"}
    _nl_perm_label = {v: k for k, v in _NL_PERM_MAP.items()}
    nl_perm_var = tkinter.StringVar(
        value=_nl_perm_label.get(getattr(state, "NETLINK_PERM_LEVEL", "observe"), "仅观察"))
    ttk.Combobox(nl_perm_frame, textvariable=nl_perm_var,
        values=("仅观察", "允许操控", "允许接收脚本"),
        state="readonly", width=14).pack(side="left", padx=(0, 12))

    # 静态对端 (只读显示 + 清空; 明细在「设备互联」窗口维护)
    nl_peers_frame = tkinter.Frame(nl_content, bg=C["bgc"])
    nl_peers_frame.grid(row=3, column=0, sticky="ew", padx=8, pady=(1, 6))
    _nl_peers_count = len(getattr(state, "NETLINK_STATIC_PEERS", []) or [])
    tkinter.Label(nl_peers_frame,
        text="静态对端: {} 个 (在「设备互联」窗口维护)".format(_nl_peers_count),
        font=FONT_SMALL, fg=C["fgm"], bg=C["bgc"]).pack(side="left", padx=(0, 8))

    def _clear_static_peers():
        try:
            state.NETLINK_STATIC_PEERS = []
            state.save_config()
            _flash_saved("netlink")
            show_toast(root, "已清空静态对端", "info", 1500)
        except Exception as e:
            log1("清空静态对端失败: {}".format(e), "warning")

    _btn(nl_peers_frame, "清空", _clear_static_peers, C["dg"], "white",
        tip="清空手动添加的静态对端列表 (不影响自动发现)").pack(side="left")

    # 配对认证开关 + 关闭风险红字提示 (仅关闭时显示)
    nl_auth_frame = tkinter.Frame(nl_content, bg=C["bgc"])
    nl_auth_frame.grid(row=4, column=0, sticky="ew", padx=8, pady=(1, 1))
    nl_require_auth_var = tkinter.BooleanVar(
        value=bool(getattr(state, "NETLINK_REQUIRE_AUTH", True)))
    ttk.Checkbutton(nl_auth_frame,
        text="启用配对认证（关闭后同网段任意设备可只读查看）",
        variable=nl_require_auth_var).pack(side="left")
    nl_auth_warn = tkinter.Label(nl_content,
        text="⚠ 关闭认证后，同网段任何设备都可读取本机运行状态与日志",
        font=FONT_SMALL, fg=C["err"], bg=C["bgc"])
    nl_auth_warn.grid(row=5, column=0, sticky="w", padx=24, pady=(0, 2))

    def _toggle_auth_warn():
        try:
            if nl_require_auth_var.get():
                nl_auth_warn.grid_remove()
            else:
                nl_auth_warn.grid()
        except Exception:
            pass

    try:
        nl_require_auth_var.trace_add("write", lambda *_: _toggle_auth_warn())
    except Exception:
        pass
    _toggle_auth_warn()

    # 配对码有效期 (秒)
    nl_ttl_frame = tkinter.Frame(nl_content, bg=C["bgc"])
    nl_ttl_frame.grid(row=6, column=0, sticky="ew", padx=8, pady=(1, 6))
    tkinter.Label(nl_ttl_frame, text="配对码有效期(秒)", font=FONT_SMALL,
        fg=C["fgm"], bg=C["bgc"]).pack(side="left", padx=(0, 4))
    nl_ttl_var = tkinter.StringVar(value=str(getattr(state, "NETLINK_PIN_TTL", 600)))
    _validate_number(tkinter.Entry(nl_ttl_frame, textvariable=nl_ttl_var, width=7,
        font=FONT_BODY, relief="solid", bd=1, bg=C["ebg"], fg=C["fgb"]),
        nl_ttl_var, "配对码有效期", 600, int, 10, 86400, card_key="netlink").pack(
        side="left")

    # 浏览器只读监控面板 (Phase4-2: 默认关闭, 启用即要求令牌)
    nl_web_frame = tkinter.Frame(nl_content, bg=C["bgc"])
    nl_web_frame.grid(row=7, column=0, sticky="ew", padx=8, pady=(1, 1))
    nl_web_enabled_var = tkinter.BooleanVar(
        value=bool(getattr(state, "NETLINK_WEB_ENABLED", False)))
    ttk.Checkbutton(nl_web_frame, text="启用浏览器只读监控面板",
        variable=nl_web_enabled_var).pack(side="left", padx=(0, 12))
    tkinter.Label(nl_web_frame, text="端口", font=FONT_SMALL,
        fg=C["fgm"], bg=C["bgc"]).pack(side="left", padx=(0, 4))
    nl_web_port_var = tkinter.StringVar(
        value=str(getattr(state, "NETLINK_WEB_PORT", 19712)))
    _validate_number(tkinter.Entry(nl_web_frame, textvariable=nl_web_port_var, width=7,
        font=FONT_BODY, relief="solid", bd=1, bg=C["ebg"], fg=C["fgb"]),
        nl_web_port_var, "面板端口", 19712, int, 1, 65535, card_key="netlink").pack(
        side="left", padx=(0, 10))
    tkinter.Label(nl_web_frame, text="监听范围", font=FONT_SMALL,
        fg=C["fgm"], bg=C["bgc"]).pack(side="left", padx=(0, 4))
    _NL_WEB_BIND_MAP = {"所有网卡（同网段可访问）": "0.0.0.0", "仅本机": "127.0.0.1"}
    _nl_web_bind_label = {v: k for k, v in _NL_WEB_BIND_MAP.items()}
    nl_web_bind_var = tkinter.StringVar(
        value=_nl_web_bind_label.get(
            str(getattr(state, "NETLINK_WEB_BIND", "0.0.0.0")),
            "所有网卡（同网段可访问）"))
    ttk.Combobox(nl_web_frame, textvariable=nl_web_bind_var,
        values=("所有网卡（同网段可访问）", "仅本机"),
        state="readonly", width=18).pack(side="left")

    nl_web_warn = tkinter.Label(nl_content,
        text="⚠ 面板会暴露本机运行状态与操作日志，请仅在可信内网使用",
        font=FONT_SMALL, fg=C["wn"], bg=C["bgc"])
    nl_web_warn.grid(row=8, column=0, sticky="w", padx=24, pady=(0, 2))

    def _toggle_web_warn():
        try:
            if nl_web_enabled_var.get():
                nl_web_warn.grid()
            else:
                nl_web_warn.grid_remove()
        except Exception:
            pass

    try:
        nl_web_enabled_var.trace_add("write", lambda *_: _toggle_web_warn())
    except Exception:
        pass
    _toggle_web_warn()

    # ── TLS 可选档 (Phase4-3: 自签证书 + 指纹固定) ──
    nl_tls_frame = tkinter.Frame(nl_content, bg=C["bgc"])
    nl_tls_frame.grid(row=9, column=0, sticky="ew", padx=8, pady=(1, 1))
    nl_tls_var = tkinter.BooleanVar(value=bool(getattr(state, "NETLINK_TLS", False)))
    ttk.Checkbutton(nl_tls_frame, text="启用 TLS 加密（需提供自签证书 PEM）",
        variable=nl_tls_var).pack(side="left")

    nl_tls_cert_frame = tkinter.Frame(nl_content, bg=C["bgc"])
    nl_tls_cert_frame.grid(row=10, column=0, sticky="ew", padx=8, pady=1)
    tkinter.Label(nl_tls_cert_frame, text="证书PEM", font=FONT_SMALL,
        fg=C["fgm"], bg=C["bgc"]).pack(side="left", padx=(0, 4))
    nl_tls_cert_var = tkinter.StringVar(
        value=str(getattr(state, "NETLINK_TLS_CERT", "") or ""))
    tkinter.Entry(nl_tls_cert_frame, textvariable=nl_tls_cert_var, width=22,
        font=FONT_SMALL, bg=C["ebg"], fg=C["fgb"], relief="solid", bd=1).pack(
        side="left", padx=(0, 4))

    def _browse_tls_cert():
        try:
            p = filedialog.askopenfilename(
                title="选择服务端证书 PEM",
                filetypes=[("证书 PEM", "*.pem *.crt *.key"), ("All", "*.*")])
            if p:
                nl_tls_cert_var.set(p)
        except Exception:
            pass

    _btn(nl_tls_cert_frame, "浏览…", _browse_tls_cert, C["ac"], "white",
         tip="选择服务端证书 PEM").pack(side="left", padx=(0, 8))
    tkinter.Label(nl_tls_cert_frame, text="私钥PEM", font=FONT_SMALL,
        fg=C["fgm"], bg=C["bgc"]).pack(side="left", padx=(0, 4))
    nl_tls_key_var = tkinter.StringVar(
        value=str(getattr(state, "NETLINK_TLS_KEY", "") or ""))
    tkinter.Entry(nl_tls_cert_frame, textvariable=nl_tls_key_var, width=22,
        font=FONT_SMALL, bg=C["ebg"], fg=C["fgb"], relief="solid", bd=1).pack(
        side="left", padx=(0, 4))

    def _browse_tls_key():
        try:
            p = filedialog.askopenfilename(
                title="选择服务端私钥 PEM",
                filetypes=[("私钥 PEM", "*.pem *.crt *.key"), ("All", "*.*")])
            if p:
                nl_tls_key_var.set(p)
        except Exception:
            pass

    _btn(nl_tls_cert_frame, "浏览…", _browse_tls_key, C["ac"], "white",
         tip="选择服务端私钥 PEM").pack(side="left")

    nl_tls_warn = tkinter.Label(nl_content,
        text="⚠ 启用 TLS 后必须同时配置证书与私钥，否则设备互联将拒绝启动",
        font=FONT_SMALL, fg=C["wn"], bg=C["bgc"])
    nl_tls_warn.grid(row=11, column=0, sticky="w", padx=24, pady=(0, 2))

    def _toggle_tls_warn():
        try:
            if nl_tls_var.get():
                nl_tls_warn.grid()
            else:
                nl_tls_warn.grid_remove()
        except Exception:
            pass

    try:
        nl_tls_var.trace_add("write", lambda *_: _toggle_tls_warn())
    except Exception:
        pass
    _toggle_tls_warn()

    # 已固定指纹 (只读展示 + 清空; TOFU 首次信任记录)
    nl_pins_frame = tkinter.Frame(nl_content, bg=C["bgc"])
    nl_pins_frame.grid(row=12, column=0, sticky="ew", padx=8, pady=(1, 6))
    _nl_pins_count = len(getattr(state, "NETLINK_TLS_PINS", []) or [])
    tkinter.Label(nl_pins_frame,
        text="已固定证书指纹: {} 个".format(_nl_pins_count),
        font=FONT_SMALL, fg=C["fgm"], bg=C["bgc"]).pack(side="left", padx=(0, 8))

    def _clear_tls_pins():
        try:
            state.NETLINK_TLS_PINS = []
            state.save_config()
            _flash_saved("netlink")
            show_toast(root, "已清空已固定指纹", "info", 1500)
        except Exception as e:
            log1("清空已固定指纹失败: {}".format(e), "warning")

    _btn(nl_pins_frame, "清空指纹", _clear_tls_pins, C["dg"], "white",
         tip="清空已固定的对端证书指纹（下次连接将重新 TOFU 首次信任）").pack(side="left")

    # ── 网页面板「有限控制」(Phase4-2 演进；默认全关 = 行为完全不变) ──
    nl_web_ctl_frame = tkinter.Frame(nl_content, bg=C["bgc"])
    nl_web_ctl_frame.grid(row=13, column=0, sticky="ew", padx=8, pady=(1, 1))
    nl_web_control_var = tkinter.BooleanVar(
        value=bool(getattr(state, "NETLINK_WEB_CONTROL", False)))
    ttk.Checkbutton(nl_web_ctl_frame,
        text="启用网页面板有限控制（run/stop，强制 HTTPS）",
        variable=nl_web_control_var).pack(side="left", padx=(0, 12))
    nl_web_tls_var = tkinter.BooleanVar(
        value=bool(getattr(state, "NETLINK_WEB_TLS", False)))
    ttk.Checkbutton(nl_web_ctl_frame,
        text="面板启用 HTTPS（控制必需）",
        variable=nl_web_tls_var).pack(side="left")

    nl_web_ctl2 = tkinter.Frame(nl_content, bg=C["bgc"])
    nl_web_ctl2.grid(row=14, column=0, sticky="ew", padx=8, pady=(1, 1))
    tkinter.Label(nl_web_ctl2, text="控制会话 TTL(秒)", font=FONT_SMALL,
        fg=C["fgm"], bg=C["bgc"]).pack(side="left", padx=(0, 4))
    nl_web_control_ttl_var = tkinter.StringVar(
        value=str(getattr(state, "NETLINK_WEB_CONTROL_TTL", 300)))
    _validate_number(tkinter.Entry(nl_web_ctl2, textvariable=nl_web_control_ttl_var,
        width=6, font=FONT_BODY, relief="solid", bd=1, bg=C["ebg"], fg=C["fgb"]),
        nl_web_control_ttl_var, "控制会话 TTL", 300, int, 60, 3600,
        card_key="netlink").pack(side="left", padx=(0, 10))
    nl_web_confirm_control_var = tkinter.BooleanVar(
        value=bool(getattr(state, "NETLINK_WEB_CONFIRM_CONTROL", False)))
    ttk.Checkbutton(nl_web_ctl2, text="本地面板控制也需桌面确认",
        variable=nl_web_confirm_control_var).pack(side="left", padx=(0, 10))
    nl_web_allow_remote_var = tkinter.BooleanVar(
        value=bool(getattr(state, "NETLINK_WEB_ALLOW_REMOTE_CONTROL", False)))
    ttk.Checkbutton(nl_web_ctl2, text="允许局域网控制(高危)",
        variable=nl_web_allow_remote_var).pack(side="left")

    nl_web_ctl_warn = tkinter.Label(nl_content,
        text="⚠ 控制 PIN 在「设备互联 → 网页面板」中设置/重置；控制强制 HTTPS，"
             "需先配置证书与私钥 PEM（否则面板拒绝启用）",
        font=FONT_SMALL, fg=C["wn"], bg=C["bgc"])
    nl_web_ctl_warn.grid(row=15, column=0, sticky="w", padx=24, pady=(0, 6))

    def _toggle_web_ctl_warn():
        try:
            if nl_web_control_var.get():
                nl_web_ctl_warn.grid()
            else:
                nl_web_ctl_warn.grid_remove()
        except Exception:
            pass

    try:
        nl_web_control_var.trace_add("write", lambda *_: _toggle_web_ctl_warn())
    except Exception:
        pass
    _toggle_web_ctl_warn()

    # ── 网络互联 apply (走既有保存路径: _apply_map → _do_save → state.save_config) ──
    def _apply_netlink_settings():
        state.NETLINK_ENABLED = nl_enabled_var.get()
        try:
            state.NETLINK_PORT = int(nl_port_var.get())
        except (ValueError, TypeError):
            state.NETLINK_PORT = 19710; nl_port_var.set("19710")
        state.NETLINK_DEVICE_NAME = nl_name_var.get().strip()
        state.NETLINK_AUTODISCOVER = nl_auto_var.get()
        try:
            state.NETLINK_DISCOVERY_PORT = int(nl_dport_var.get())
        except (ValueError, TypeError):
            state.NETLINK_DISCOVERY_PORT = 19711; nl_dport_var.set("19711")
        state.NETLINK_PERM_LEVEL = _NL_PERM_MAP.get(nl_perm_var.get(), "observe")
        state.NETLINK_REQUIRE_AUTH = bool(nl_require_auth_var.get())
        try:
            state.NETLINK_PIN_TTL = int(nl_ttl_var.get())
        except (ValueError, TypeError):
            state.NETLINK_PIN_TTL = 600; nl_ttl_var.set("600")
        # Phase4-2 浏览器只读监控面板
        state.NETLINK_WEB_ENABLED = bool(nl_web_enabled_var.get())
        try:
            state.NETLINK_WEB_PORT = int(nl_web_port_var.get())
        except (ValueError, TypeError):
            state.NETLINK_WEB_PORT = 19712; nl_web_port_var.set("19712")
        state.NETLINK_WEB_BIND = _NL_WEB_BIND_MAP.get(nl_web_bind_var.get(), "0.0.0.0")
        # Phase4-3 TLS 可选档
        state.NETLINK_TLS = bool(nl_tls_var.get())
        state.NETLINK_TLS_CERT = nl_tls_cert_var.get().strip()
        state.NETLINK_TLS_KEY = nl_tls_key_var.get().strip()
        # Phase4-2 网页面板有限控制 (默认全关)
        state.NETLINK_WEB_CONTROL = bool(nl_web_control_var.get())
        try:
            state.NETLINK_WEB_CONTROL_TTL = int(nl_web_control_ttl_var.get())
        except (ValueError, TypeError):
            state.NETLINK_WEB_CONTROL_TTL = 300
            nl_web_control_ttl_var.set("300")
        # 联动强制：控制开启 → 面板 TLS 必须为真（绝不回落明文）
        state.NETLINK_WEB_TLS = bool(nl_web_tls_var.get()) or state.NETLINK_WEB_CONTROL
        state.NETLINK_WEB_CONFIRM_CONTROL = bool(nl_web_confirm_control_var.get())
        state.NETLINK_WEB_ALLOW_REMOTE_CONTROL = bool(nl_web_allow_remote_var.get())
        # 开关变更即时生效 (节点重启; 全部 try/except, 失败不影响设置保存)
        try:
            import netlink
            if state.NETLINK_ENABLED:
                netlink.start_netlink(root)
                if state.NETLINK_WEB_ENABLED:
                    # 控制/TLS 变更需重启面板才生效 → 先停后起
                    try:
                        netlink.stop_webui()
                    except Exception:
                        pass
                    if not netlink.start_webui():
                        why = ""
                        try:
                            why = str(netlink.webui_last_error() or "")
                        except Exception:
                            why = ""
                        log1("网页面板启动失败: {}".format(why or "unknown"),
                             "warning")
                        try:
                            show_toast(root, "网页面板启动失败：{}".format(
                                why or "证书/端口"), "warning", 3000)
                        except Exception:
                            pass
                else:
                    netlink.stop_webui()
            else:
                netlink.stop_netlink()
        except Exception as e:
            log1("NetLink 开关同步失败: {}".format(e), "warning")
    _apply_map["netlink"] = _apply_netlink_settings

    # 绑定防抖保存 (网络互联卡)
    _track_card_vars("netlink", (nl_enabled_var, nl_port_var, nl_name_var,
        nl_auto_var, nl_dport_var, nl_perm_var, nl_require_auth_var, nl_ttl_var,
        nl_web_enabled_var, nl_web_port_var, nl_web_bind_var,
        nl_tls_var, nl_tls_cert_var, nl_tls_key_var,
        nl_web_control_var, nl_web_tls_var, nl_web_control_ttl_var,
        nl_web_confirm_control_var, nl_web_allow_remote_var))

    # ======================================================================
    # 🐍 Python 扩展 card
    # ======================================================================
    card_python, py_content, py_title, _ = _make_collapsible_card(_inner, "Python 扩展", "🐍")
    card_python.grid(row=9, column=0, sticky="ew", **PAD)
    _make_badge("python", py_title)
    _register_nav_card("python", card_python)

    # 权限下拉：UI 标签 ↔ state 存储值映射（单一来源）
    _PY_PERM_LABEL = {
        "sandbox": "沙箱（受限，推荐）",
        "trusted": "受信（可用完整内建与 import）",
        "full":    "完全权限（独立进程，默认禁用）",
    }
    _PY_LABEL_PERM = {v: k for k, v in _PY_PERM_LABEL.items()}

    py_perm_frame = tkinter.Frame(py_content, bg=C["bgc"])
    py_perm_frame.grid(row=0, column=0, sticky="ew", padx=8, pady=(2, 1))
    tkinter.Label(py_perm_frame, text="默认权限", font=FONT_SMALL,
        fg=C["fgm"], bg=C["bgc"]).pack(side="left", padx=(0, 6))
    _py_init_perm = str(getattr(state, "PYTHON_DEFAULT_PERM", "sandbox"))
    if _py_init_perm not in _PY_PERM_LABEL:
        _py_init_perm = "sandbox"
    py_perm_var = tkinter.StringVar(value=_PY_PERM_LABEL[_py_init_perm])
    _py_perm_combo = ttk.Combobox(py_perm_frame, textvariable=py_perm_var,
        values=tuple(_PY_PERM_LABEL[k] for k in ("sandbox", "trusted", "full")),
        state="readonly", width=32)
    _py_perm_combo.pack(side="left")

    # 允许 full 权限 + 风险红字（随勾选显隐）
    py_full_frame = tkinter.Frame(py_content, bg=C["bgc"])
    py_full_frame.grid(row=1, column=0, sticky="ew", padx=8, pady=(1, 0))
    py_full_var = tkinter.BooleanVar(value=bool(getattr(state, "PYTHON_FULL_ENABLED", False)))
    ttk.Checkbutton(py_full_frame, text="允许 full 权限（独立进程执行任意代码）",
        variable=py_full_var).pack(side="left")

    py_full_warn = tkinter.Label(py_content,
        text="⚠ 完全权限可绕过沙箱执行任意代码与命令，仅在完全信任的脚本上启用",
        font=FONT_TINY, fg=C["err"], bg=C["bgc"])

    def _py_toggle_full_warn(*_):
        try:
            if py_full_var.get():
                py_full_warn.grid(row=2, column=0, sticky="w", padx=10, pady=(0, 1))
            else:
                py_full_warn.grid_remove()
        except Exception:
            pass

    py_full_var.trace_add("write", _py_toggle_full_warn)
    _py_toggle_full_warn()

    # 超时(秒)
    py_to_frame = tkinter.Frame(py_content, bg=C["bgc"])
    py_to_frame.grid(row=3, column=0, sticky="ew", padx=8, pady=1)
    tkinter.Label(py_to_frame, text="超时(秒)", font=FONT_SMALL,
        fg=C["fgm"], bg=C["bgc"]).pack(side="left", padx=(0, 6))
    py_timeout_var = tkinter.StringVar(value=str(getattr(state, "PYTHON_TIMEOUT", 30)))
    _validate_number(tkinter.Entry(py_to_frame, textvariable=py_timeout_var, width=6,
        font=FONT_SMALL, relief="solid", bd=1, bg=C["ebg"], fg=C["fgb"]),
        py_timeout_var, "Python 超时", 30, int, 1, 600, card_key="python").pack(side="left")
    tkinter.Label(py_to_frame, text="秒 (1-600)", font=FONT_TINY,
        fg=C["fgm"], bg=C["bgc"]).pack(side="left", padx=(4, 0))

    # 审计日志路径 (只读) + 打开日志目录
    try:
        import py_sandbox as _py_sandbox
        _py_audit_path = _py_sandbox.audit_path()
    except Exception:
        _py_audit_path = os.path.join(APP_ROOT, "logs", "acrpa_py_YYYYMMDD.log")
    py_audit_frame = tkinter.Frame(py_content, bg=C["bgc"])
    py_audit_frame.grid(row=4, column=0, sticky="ew", padx=8, pady=(1, 6))
    tkinter.Label(py_audit_frame, text="审计日志: {}".format(_py_audit_path),
        font=FONT_TINY, fg=C["fgm"], bg=C["bgc"]).pack(side="left")

    def _py_open_log_dir():
        try:
            os.startfile(os.path.dirname(_py_audit_path))
        except Exception:
            pass

    _btn(py_audit_frame, "打开日志目录", _py_open_log_dir, C["ac"], "white",
         tip="在文件管理器中打开 Python 审计日志目录").pack(side="right")

    tkinter.Label(py_content,
        text="提示：Python 命令受 AST 预检与超时约束；full 权限默认关闭（详见 docs/python扩展使用说明.md）",
        font=FONT_TINY, fg=C["fgm"], bg=C["bgc"]).grid(
        row=5, column=0, sticky="w", padx=10, pady=(0, 6))

    def _apply_python_settings():
        """保存「Python 扩展」卡配置。"""
        state.PYTHON_DEFAULT_PERM = _PY_LABEL_PERM.get(py_perm_var.get(), "sandbox")
        state.PYTHON_FULL_ENABLED = bool(py_full_var.get())
        try:
            v = int(py_timeout_var.get())
            if v < 1:
                v = 1
            if v > 600:
                v = 600
            state.PYTHON_TIMEOUT = v
        except (ValueError, TypeError):
            state.PYTHON_TIMEOUT = 30
            py_timeout_var.set("30")
    _apply_map["python"] = _apply_python_settings

    # 绑定防抖保存 (Python 扩展卡)
    _track_card_vars("python", (py_perm_var, py_full_var, py_timeout_var))

    # ======================================================================
    # 🛒 脚本市场 账号卡 (批次3 v2 — 账号状态 / 登录 / 注销 / 市场选项)
    # ======================================================================
    card_market, mk_content, mk_title, _ = _make_collapsible_card(_inner, "脚本市场", "🛒")
    card_market.grid(row=10, column=0, sticky="ew", **PAD)
    _make_badge("market", mk_title)
    _register_nav_card("market", card_market)

    import accounts as _accounts

    _mk_status = {}

    def _mk_refresh():
        """无网络刷新两 provider 登录状态 (仅凭据库探测)；同时刷新当前账户。"""
        for _prov in ("gitee", "github"):
            _lbl = _mk_status.get(_prov)
            if _lbl is None:
                continue
            try:
                _has = _accounts.has_token(_prov)
            except Exception:
                _has = False
            try:
                if _has:
                    _lbl.configure(text="● 已登录", fg=C["sc"])
                else:
                    _lbl.configure(text="○ 未登录", fg=C["fgm"])
            except Exception:
                pass
        try:
            _name = getattr(state, "MARKET_USERNAME", "") or ""
            _p = (getattr(state, "MARKET_PROVIDER", "") or "").lower()
            if _name:
                mk_cur_lbl.configure(text="当前账户: {} ({})".format(_name, _p or "-"))
            else:
                _logged = _accounts.list_logged_in()
                mk_cur_lbl.configure(text="当前账户: {}".format(
                    _logged[0] if _logged else "(未登录)"))
        except Exception:
            pass

    def _mk_open_token(prov):
        """内联 PAT 输入弹窗；Token 仅经 accounts.login → 凭据库，绝不明文落盘。"""
        dlg = tkinter.Toplevel(_win)
        dlg.title("登录 {}".format(prov))
        dlg.geometry("440x190+520+320")
        dlg.transient(_win)
        dlg.configure(bg=C["bgc"])
        try:
            dlg.grab_set()
        except Exception:
            pass
        _set_window_icon(dlg)
        tkinter.Label(dlg, text="Personal Access Token ({})".format(prov),
            font=FONT_BODY, bg=C["bgc"], fg=C["fgt"]).pack(pady=(14, 4))
        tkinter.Label(dlg, text="仅存本机 Windows 凭据库，不上传、不写入配置文件。",
            font=FONT_TINY, bg=C["bgc"], fg=C["fgm"]).pack()
        var = tkinter.StringVar()
        ent = tkinter.Entry(dlg, textvariable=var, show="*", width=46,
            font=FONT_BODY, relief="solid", bd=1, bg=C["ebg"], fg=C["fgb"])
        ent.pack(pady=8)
        ent.focus_set()

        def _submit():
            token = var.get().strip()
            if not token:
                messagebox.showwarning("提示", "请输入 Token", parent=dlg)
                return
            t = token
            dlg.destroy()
            _mk_login(prov, t)

        btns = tkinter.Frame(dlg, bg=C["bgc"])
        btns.pack(pady=(0, 12))
        _btn(btns, "确定", _submit).pack(side="left", padx=(0, 6))
        _btn(btns, "取消", dlg.destroy).pack(side="left")
        ent.bind("<Return>", lambda e: _submit())

    def _mk_login(prov, token):
        """后台线程校验并保存 token；回主线程刷新状态。"""
        def work():
            try:
                info = _accounts.login(prov, token)
            except Exception as e:
                err = str(e)

                def _err():
                    try:
                        messagebox.showerror("登录失败", err, parent=_win)
                    except Exception:
                        pass
                    _mk_refresh()
                try:
                    _win.after(0, _err)
                except Exception:
                    pass
                return

            def _ok():
                try:
                    state.MARKET_PROVIDER = prov
                    state.MARKET_USERNAME = info.get("login", "") or info.get("name", "")
                    state.save_config()
                except Exception:
                    pass
                try:
                    show_toast(root, "已登录 {}: {}".format(
                        prov, info.get("login", "")), "success")
                except Exception:
                    pass
                _mk_refresh()
            try:
                _win.after(0, _ok)
            except Exception:
                pass

        threading.Thread(target=work, daemon=True).start()

    def _mk_logout(prov):
        try:
            _accounts.logout(prov)
        except Exception as e:
            log1("注销失败: {}".format(e), "warning")
        _mk_refresh()
        try:
            show_toast(root, "已注销 {}".format(prov), "info")
        except Exception:
            pass

    mk_rows = tkinter.Frame(mk_content, bg=C["bgc"])
    mk_rows.grid(row=0, column=0, sticky="ew", padx=8, pady=(2, 1))
    mk_rows.columnconfigure(1, weight=1)
    for _i, (_prov, _label) in enumerate((("gitee", "Gitee"), ("github", "GitHub"))):
        tkinter.Label(mk_rows, text=_label, font=FONT_BODY, bg=C["bgc"],
            fg=C["fgb"], width=8, anchor="w").grid(row=_i, column=0, sticky="w", pady=1)
        _st = tkinter.Label(mk_rows, text="", font=FONT_SMALL, bg=C["bgc"], fg=C["fgm"])
        _st.grid(row=_i, column=1, sticky="w")
        _mk_status[_prov] = _st
        _btns = tkinter.Frame(mk_rows, bg=C["bgc"])
        _btns.grid(row=_i, column=2, sticky="e")
        _btn(_btns, "登录", lambda p=_prov: _mk_open_token(p),
             C["sc"], "white", tip="输入 PAT 登录（仅存本机凭据库）").pack(
            side="left", padx=(0, 4))
        _btn(_btns, "注销", lambda p=_prov: _mk_logout(p),
             C["dg"], "white", tip="清除本机保存的 token").pack(side="left")

    mk_cur_lbl = tkinter.Label(mk_content, text="", font=FONT_SMALL,
        bg=C["bgc"], fg=C["ac"])
    mk_cur_lbl.grid(row=1, column=0, sticky="w", padx=8, pady=(2, 2))

    mk_opt = tkinter.Frame(mk_content, bg=C["bgc"])
    mk_opt.grid(row=2, column=0, sticky="ew", padx=8, pady=(1, 1))
    market_auto_check_var = tkinter.BooleanVar(
        value=bool(getattr(state, "MARKET_AUTO_CHECK_UPDATE", True)))
    ttk.Checkbutton(mk_opt, text="打开市场时后台查更新",
        variable=market_auto_check_var).pack(side="left", padx=(0, 12))
    tkinter.Label(mk_opt, text="索引缓存(秒)", font=FONT_SMALL,
        fg=C["fgm"], bg=C["bgc"]).pack(side="left", padx=(0, 4))
    market_ttl_var = tkinter.StringVar(
        value=str(getattr(state, "MARKET_INDEX_CACHE_TTL", 3600)))
    _validate_number(tkinter.Entry(mk_opt, textvariable=market_ttl_var, width=7,
        font=FONT_BODY, relief="solid", bd=1, bg=C["ebg"], fg=C["fgb"]),
        market_ttl_var, "索引缓存秒数", 3600, int, 1, 86400,
        card_key="market").pack(side="left")

    tkinter.Label(mk_content,
        text="Token 仅存 Windows 凭据库；登录/上传均不写入 config.json。",
        font=FONT_TINY, fg=C["fgm"], bg=C["bgc"]).grid(
        row=3, column=0, sticky="w", padx=8, pady=(0, 6))

    def _apply_market_settings():
        """保存「脚本市场」卡非敏感配置 (账号 token 走凭据库，不在此)。"""
        state.MARKET_AUTO_CHECK_UPDATE = bool(market_auto_check_var.get())
        try:
            v = int(market_ttl_var.get())
            v = 1 if v < 1 else (86400 if v > 86400 else v)
            state.MARKET_INDEX_CACHE_TTL = v
        except (ValueError, TypeError):
            state.MARKET_INDEX_CACHE_TTL = 3600
            market_ttl_var.set("3600")
    _apply_map["market"] = _apply_market_settings

    _track_card_vars("market", (market_auto_check_var, market_ttl_var))
    _mk_refresh()

    # 窗口级滚轮绑定 + 关闭快捷键
    _win.bind("<Escape>", lambda e: _close())
    _win.lift()
    _win.focus_force()
