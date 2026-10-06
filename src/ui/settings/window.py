# -*- coding: utf-8 -*-
"""ui.settings.window — 设置窗口门面 (路线图 §3.1 阶段二第 4 项)。

把原 src/settings_window.py 的单函数 open_settings_window (≈2339 行) 拆为:

  · 本模块 : 窗口骨架 (左侧导航 / 右侧滚动画布 / 关闭 / Esc) + 统一即时保存框架
    (_auto_save / _do_save / _flash_saved / _flash_failed / _show_save_error /
     _reset_badge / _make_badge / _track_card_vars / _validate_number /
     _make_collapsible_card / _register_nav_card / _on_nav_click) +
     依赖注入 (init_ctx) + 主题刷新 (refresh_theme / on_theme_publish)。
  · cards/*.py : 11 张设置卡, 每卡导出
        build(parent, ctx) -> handles          # 构建该卡 UI, 返回句柄
        apply(ctx, handles) -> None            # 写回 state (等价原 _apply_<卡>)
    卡片 apply 由本模块逐卡注册进 _apply_map。

设计约束
--------
· **不 import ACRPA**: 全部依赖经 init_ctx 注入 (沿用原模块 docstring 约束)。
· ThemeBus 订阅由薄壳 src/settings_window.py 完成 (满足「settings_window.py 文本
  须含 _ui_theme.subscribe(_on_theme_publish)」的静态断言); 本模块只实现
  on_theme_publish / refresh_theme (供薄壳转发)。
· 卡片 Variable 经 build() 返回的 handles 显式传递, apply 只读 handles/ctx ——
  消除「定义位置在后」的隐式闭包延迟解析问题 (原缺陷 P1-1)。
· _do_save 顺序不变: _apply_map[key]() → state.save_config() → _flash_saved。
"""
import importlib
import os
import types
import tkinter
from tkinter import messagebox

import state
import utils
import scheduler as sched
from ui import theme as _ui_theme
from utils import (_btn, _darken, create_card, log1, show_toast, attach_tooltip,
                   FONT_SMALL_BOLD, FONT_ICON, FONT_ICON_MD, ctrl_h, sp)

__all__ = [
    "open_settings_window", "init_ctx", "refresh_theme", "on_theme_publish",
    "update_sched_next_label",
]

# ── 依赖注入 (由 ACRPA.py 启动时调用 init_ctx 填充) ──
root = None              # 主窗口
C = None                 # 颜色表 (主题切换时由 on_theme_publish 同步)
FONT_TITLE = FONT_BODY = FONT_SMALL = FONT_BUTTON = None
APP_ROOT = ""            # 程序根目录
_tlog = None             # ThreadSafeLog 实例 (导出日志)
_ensure_tray = None      # 托盘创建回调
_destroy_tray = None     # 托盘销毁回调
_toggle_fold = None      # Mini Bar 折叠切换回调
_main_run = None         # 运行脚本回调 (定时调度保存)
_win = None              # 当前打开的设置窗口
_prev_colors = None      # 上次刷新时的主题色表快照
_tip_win = None          # tooltip 窗口
_sched_next_label = None # 定时调度「下次执行」标签 (供主程序周期刷新)

# ── 统一即时保存框架 (控件改动 600ms 防抖后自动保存) ──
_debounce_after = {}     # card_key -> after id
_apply_map = {}          # card_key -> apply 函数 (保存时调用, 写 state)
_saved_badges = {}       # card_key -> 卡标题栏「自动保存」角标 Label
_last_error = {}         # card_key -> 最近一次保存失败原因

# ── P0 左侧导航栏 ──
_nav_cards = []          # [(key, card_widget), ...] — 注册顺序即显示顺序
_nav_labels = {}         # key -> tkinter.Label (导航项)
_nav_canvas = None       # 右侧滚动 Canvas
_nav_active_key = None   # 当前高亮的导航项 key

# 卡片模块名 (顺序即导航与 grid 行序): cards/*.py
_CARD_KEYS = ("exec", "ai", "sched", "record", "log", "system", "quick",
              "advanced", "netlink", "python", "market", "extensions")


def update_sched_next_label(text):
    """供主程序 _periodic 刷新: 设置窗口已打开时更新「下次执行」标签。"""
    global _sched_next_label
    if _sched_next_label is not None:
        try:
            _sched_next_label.config(text=text)
        except Exception:
            pass


def _register_sched_label(lbl):
    """登记「下次执行」标签 (sched 卡建好后回调本模块)。"""
    global _sched_next_label
    _sched_next_label = lbl


def init_ctx(root_win=None, colors=None, fonts=None, app_root="", tlog=None,
             ensure_tray=None, destroy_tray=None, toggle_fold=None,
             main_run=None):
    """注入 ACRPA.py 提供的依赖 (模块启动时调用一次)。

    注: ThemeBus 订阅由薄壳 src/settings_window.py 的 init_ctx 完成 (避免重复订阅)。
    """
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


def on_theme_publish(dark=None, colors=None, prev=None):
    """ThemeBus 派发目标 (由薄壳 _on_theme_publish 转发): 同步色表 + 重刷已开窗口。"""
    global C
    if isinstance(colors, dict):
        C = colors
    refresh_theme(prev)


def refresh_theme(prev=None):
    """主题切换后递归刷新已打开设置窗口的颜色。

    prev: 旧主题色表快照。语义按钮回填以 prev∪C 判定 —— 旧实现依赖恒为 None 的
    C["old_*"], 会把 ▶运行/⏸暂停 等语义按钮误判为中性按钮刷成灰 (与主窗口 D2 同源缺陷)。
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
                    # 语义角色由控件创建时显式登记 (ui.theme.set_role); 未登记 → fgm。
                    fg_color = C.get(_ui_theme.get_role(w) or "", C["fgm"])
                    w.configure(bg=parent_bg, fg=fg_color)
                elif cls == "Text":
                    w.configure(bg=C["logbg"], fg=C["logfg"],
                        insertbackground=C["fgt"],
                        selectbackground=C["acl"], selectforeground=C["fgt"])
                elif cls == "Scrollbar":
                    w.configure(bg=C["bd"], activebackground=C["fgm"], troughcolor=C["logbg"])
                elif cls == "Button":
                    _role = _ui_theme.get_role(w)
                    if _role:
                        w.configure(bg=C.get(_role, C["bgc"]))
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
                    w.configure(bg=C["bg"])
                elif cls == "Listbox":
                    w.configure(bg=C["ebg"], fg=C["fgb"],
                        selectbackground=C["acl"], selectforeground=C["fgt"])
                elif cls == "Spinbox":
                    w.configure(bg=C["ebg"], fg=C["fgb"],
                        buttonbackground=C["bgc"], insertbackground=C["fgt"])
                elif cls in ("Checkbutton", "Radiobutton"):
                    _pbg = str(p.cget("bg"))
                    w.configure(bg=_pbg, fg=C["fgb"],
                        activebackground=_pbg, activeforeground=C["fgt"],
                        selectcolor=C["bgc"], highlightbackground=C["bd"])
                elif cls == "Menu":
                    w.configure(bg=C["bgc"], fg=C["fgb"],
                        activebackground=C["acl"], activeforeground=C["fgt"],
                        disabledforeground=C["fgm"])
            except Exception:
                pass
            _walk(w, prev)

    _walk(_win, prev)
    try:
        _win.update_idletasks()
    except Exception:
        pass
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
    """执行该卡 apply → 保存配置 → 角标反馈; 失败必须可见 (角标可点击重试)。"""
    try:
        fn = _apply_map.get(card_key)
        if fn:
            fn()
        state.save_config()
        _last_error.pop(card_key, None)
        _flash_saved(card_key)
    except Exception as e:
        _last_error[card_key] = "{}: {}".format(type(e).__name__, e)
        log1("保存设置失败 [{}]: {}".format(card_key, e), "error")
        _flash_failed(card_key)


def _flash_saved(card_key):
    """角标 1.2s 显示「✔ 已保存」后恢复「自动保存」。"""
    badge = _saved_badges.get(card_key)
    if badge is None or not badge.winfo_exists():
        return
    try:
        badge.configure(text="✔ 已保存", fg=C["sc"], font=FONT_SMALL_BOLD)
        try:
            badge.unbind("<Button-1>")
        except Exception:
            pass
        _win.after(1200, lambda: _reset_badge(badge))
    except Exception:
        pass


def _flash_failed(card_key):
    """角标切「⚠ 保存失败 (点击查看)」并保持, 直到该卡保存成功。"""
    badge = _saved_badges.get(card_key)
    if badge is None or not badge.winfo_exists():
        return
    try:
        badge.configure(text="⚠ 保存失败 (点击查看)", fg=C["dg"],
            font=FONT_SMALL_BOLD)
        badge.bind("<Button-1>", lambda _e, k=card_key: _show_save_error(k))
    except Exception:
        pass


def _show_save_error(card_key):
    """查看某卡最近一次保存失败原因, 可一键重试。"""
    reason = _last_error.get(card_key) or "未知原因"
    if messagebox.askretrycancel("保存失败",
            "这组设置未能保存：\n\n{}\n\n点「重试」立即重试；点「取消」可在修改后自动重试。".format(reason)):
        _do_save(card_key)


def _reset_badge(badge):
    try:
        badge.configure(text="自动保存", fg=C["fgm"], font=FONT_SMALL)
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
    badge.grid(row=0, column=2, sticky="e", padx=(sp("gap_tight"), 0))
    _saved_badges[card_key] = badge
    return badge


def _validate_number(entry, var, name, default, cast, min_v=None, max_v=None,
                     card_key=None):
    """数字输入 FocusOut 校验: 非法 → 浅红背景 + toast + 恢复默认; 合法 → 恢复背景。"""
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
    """创建可折叠设置卡: 返回 (card, content_frame, title_frame, arrow_label)。"""
    card = create_card(parent)
    card.columnconfigure(0, weight=1)

    title_frame = tkinter.Frame(card, bg=C["bgc"])
    title_frame.grid(row=0, column=0, sticky="ew",
        padx=sp("card_pad"), pady=(sp("sp_xs"), sp("sp_xs")))
    title_frame.columnconfigure(1, weight=1)

    # 图标用统一图标字体 (Segoe UI Symbol 单色字形), 与标题正文分离 (§3.1/§3.2)
    tkinter.Label(title_frame, text=icon, font=FONT_ICON_MD,
        bg=C["bgc"], fg=C["fgt"]).grid(row=0, column=0, sticky="w",
        padx=(0, sp("gap_tight")))

    tkinter.Label(title_frame, text=title, font=FONT_TITLE,
        bg=C["bgc"], fg=C["fgt"]).grid(row=0, column=1, sticky="w")

    arrow = tkinter.Label(title_frame, text="▼", font=FONT_ICON,
        bg=C["bgc"], fg=C["fgm"], cursor="hand2")
    arrow.grid(row=0, column=3, sticky="e", padx=(sp("gap_tight"), 0))

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
    ("exec",     "⚙", "基础执行"),
    ("ai",       "✦", "AI 增强"),
    ("sched",    "⏱", "定时调度"),
    ("record",   "◉", "录制设置"),
    ("log",      "▤", "日志"),
    ("system",   "▢", "系统"),
    ("quick",    "↯", "快速操作"),
    ("advanced", "⚒", "高级设置"),
    ("netlink",  "⊕", "网络互联"),
    ("python",   "§", "Python 扩展"),
    ("market",   "⛁", "脚本市场"),
    ("extensions", "⧉", "扩展"),
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

    _nav_active_key = key
    for k, lbl in _nav_labels.items():
        try:
            if k == key:
                lbl.configure(bg=C["acl"], fg=C["fgt"], font=FONT_BUTTON)
            else:
                lbl.configure(bg=C["bg"], fg=C["fgm"], font=FONT_BODY)
        except Exception:
            pass

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
    # 声明自管换肤: 通用 walk (ACRPA._walk / dialogs.refresh_theme) 不再跨入本窗口,
    # 换肤由薄壳订阅回调 _on_theme_publish → on_theme_publish 负责 (阶段二第 2/4 项)。
    _ui_theme.claim_window(_win, "settings_window")
    _win.title("设置 — ACRPA")
    # 记忆上次位置/尺寸 (config.json win_geometry): 非空且经虚拟屏越界校验
    # (utils.geometry_in_screen) 通过才应用; 空或越界一律回退居中 680x680。
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
        utils.place_dialog(_win, 680, 680)
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
    tkinter.Frame(nav_panel, bg=C["bd"], height=sp("hairline")).grid(
        row=1, column=0, sticky="ew", padx=sp("gap_tight"), pady=(0, sp("sp_xs")))

    _nav_items_frame = tkinter.Frame(nav_panel, bg=C["bg"])
    _nav_items_frame.grid(row=2, column=0, sticky="nsew")
    _nav_items_frame.columnconfigure(0, weight=1)
    nav_panel.rowconfigure(2, weight=1)

    _nav_row_idx = 0
    for key, icon, label_text in _NAV_ITEMS:
        _nav_items_frame.rowconfigure(_nav_row_idx, minsize=ctrl_h("ctrl_h_lg"))
        nav_lbl = tkinter.Label(_nav_items_frame,
            text="  {}  {}".format(icon, label_text),
            font=FONT_BODY, bg=C["bg"], fg=C["fgm"],
            anchor="w", padx=sp("sp_sm"), pady=sp("sp_xs"), cursor="hand2",
            takefocus=True, highlightthickness=sp("focus_w"),
            highlightbackground=C["bg"], highlightcolor=C["focus"])
        nav_lbl.grid(row=_nav_row_idx, column=0, sticky="ew",
            padx=sp("gap_tight"), pady=sp("hairline"))
        nav_lbl.bind("<Button-1>", lambda e, k=key: _on_nav_click(k))
        nav_lbl.bind("<Enter>", lambda e, l=nav_lbl, k=key:
            l.configure(bg=C["row_hover"]) if _nav_active_key != k else None)
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

    # 滚轮悬停导航栏时也滚动内容区 (提升导航栏可用性)
    nav_panel.bind("<MouseWheel>", _on_wheel)
    _nav_items_frame.bind("<MouseWheel>", _on_wheel)

    PAD = {"padx": sp("sp_sm"), "pady": sp("sp_xs")}

    # ── 依赖注入上下文 (显式携带全部依赖与工厂; 卡片据此建卡/回写 state) ──
    ctx = types.SimpleNamespace(
        C=C,
        FONT_TITLE=FONT_TITLE, FONT_BODY=FONT_BODY,
        FONT_SMALL=FONT_SMALL, FONT_BUTTON=FONT_BUTTON,
        APP_ROOT=APP_ROOT, root=root, win=_win, state=state, PAD=PAD,
        make_collapsible_card=_make_collapsible_card,
        make_badge=_make_badge,
        register_nav_card=_register_nav_card,
        validate_number=_validate_number,
        track_card_vars=_track_card_vars,
        auto_save=_auto_save,
        flash_saved=_flash_saved,
        set_window_icon=_set_window_icon,
        set_sched_label=_register_sched_label,
        btn=_btn, create_card=create_card, show_toast=show_toast, log1=log1,
        darken=_darken, attach_tooltip=attach_tooltip, sp=sp, ctrl_h=ctrl_h,
        ui_theme=_ui_theme,
        sched=sched,
        tlog=_tlog, ensure_tray=_ensure_tray, destroy_tray=_destroy_tray,
        toggle_fold=_toggle_fold, main_run=_main_run,
        # quick 卡「恢复默认」需关闭并重开窗口 (等价原 _close() + root.after(...))
        close_window=_close, reopen_window=open_settings_window,
    )

    # ── 逐卡构建 + 注册 apply (顺序即 grid 行序; quick 无 apply) ──
    handles = {}
    for key in _CARD_KEYS:
        mod = importlib.import_module("ui.settings.cards." + key)
        h = mod.build(_inner, ctx)
        handles[key] = h
        if key != "quick":
            _apply_map[key] = (lambda m=mod, hh=h: m.apply(ctx, hh))

    # 窗口级滚轮绑定 + 关闭快捷键
    _win.bind("<Escape>", lambda e: _close())
    _win.lift()
    _win.focus_force()
