# -*- coding: utf-8 -*-
"""ui.settings.cards.system — ▢ 系统卡 (原 settings_window 1463-1783 行段)。

自启 / 托盘 / 紧凑模式 / 帮助模态 / Mini Bar 定制 / 界面缩放 / 全局快捷键。

布局: 采用 cards/_layout.py 的「分节 + 表单行」——每组相关字段归为一个 section,
单行只放一个控件, 关键说明落在控件下方的 FONT_TINY help 行 (替代旧「行尾塞灰字」)。

导出:
    build(parent, ctx) -> handles
    apply(ctx, handles) -> None       # 等价原 _apply_system_settings (整卡统一收口)
"""
import os
import sys
import tkinter
from tkinter import ttk, messagebox

import state
import utils
from utils import FONT_TINY

from ui.settings.cards._layout import section, row, switch_row, help_line

CARD_KEY = "system"
ROW = 5


def build(parent, ctx):
    C = ctx.C
    sp = ctx.sp
    _flash_saved = ctx.flash_saved
    _log1 = ctx.log1
    _set_window_icon = ctx.set_window_icon
    _validate_number = ctx.validate_number
    _ensure_tray = ctx.ensure_tray
    _destroy_tray = ctx.destroy_tray
    _toggle_fold = ctx.toggle_fold
    APP_ROOT = ctx.APP_ROOT
    root = ctx.root
    _make_collapsible_card = ctx.make_collapsible_card
    _make_badge = ctx.make_badge
    _register_nav_card = ctx.register_nav_card
    _track_card_vars = ctx.track_card_vars

    card, content, title, _arrow = _make_collapsible_card(parent, "常规设置", "▢")
    card.grid(row=ROW, column=0, sticky="ew", **ctx.PAD)
    _make_badge(CARD_KEY, title)
    _register_nav_card(CARD_KEY, card)

    # 统一放置器: 分节/表单行依次进 content 的 grid 行, 行距走 token
    _r = 0

    def _add(w, pady=None):
        nonlocal _r
        w.grid(row=_r, column=0, sticky="ew",
               pady=((0, sp("row_gap")) if pady is None else pady))
        _r += 1
        return w

    def _sec(name, first=False):
        return _add(section(content, ctx, name),
                    pady=(sp("sp_sm") if first else sp("section_gap"), 0))

    # ── 分节: 启动与托盘 ──
    _sec("启动与托盘", first=True)

    auto_start_var = tkinter.BooleanVar(value=state.AUTO_START)

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
                    _log1("开机自启动需要安装 pywin32", "warning")
            else:
                if os.path.exists(shortcut):
                    os.remove(shortcut)
            _flash_saved("system")
        except Exception as e:
            _log1("开机自启动设置失败: {}".format(e), "error")

    _add(switch_row(content, ctx, "开机自启动", auto_start_var,
                    help="在 Windows 启动文件夹创建或移除快捷方式（需 pywin32）",
                    command=_toggle_auto_start))

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
            _log1("托盘切换异常: {}".format(e), "error")

    _add(switch_row(content, ctx, "关闭时最小化到系统托盘", tray_var,
                    help="关闭主窗口时隐藏到系统托盘，而不是退出程序",
                    command=_toggle_tray))

    # ── 分节: 界面与布局 ──
    _sec("界面与布局")

    compact_var = tkinter.BooleanVar(value=getattr(state, "COMPACT_MODE", False))
    _add(switch_row(content, ctx, "紧凑模式", compact_var,
                    help="回到 500×625 旧布局", restart=True))

    help_modal_var = tkinter.BooleanVar(value=getattr(state, "HELP_MODAL", False))

    def _toggle_help_modal():
        state.HELP_MODAL = help_modal_var.get()
        state.save_config()
        _flash_saved("system")

    _add(switch_row(content, ctx, "模态帮助窗口", help_modal_var,
                    help="开启后关闭帮助窗口才能继续操作；默认非模态",
                    command=_toggle_help_modal))

    reduce_motion_var = tkinter.BooleanVar(value=bool(getattr(state, "REDUCE_MOTION", False)))

    def _toggle_reduce_motion():
        state.REDUCE_MOTION = bool(reduce_motion_var.get())
        state.save_config()
        _flash_saved("system")

    # 统一使用 switch_row: 与同卡片其他开关一样左文右开关, 避免同类控件两种对齐
    _add(switch_row(content, ctx, "减少动效（无障碍）", reduce_motion_var,
                    help="关闭节点光晕 / 提示淡入等过渡动画，降低 GPU 负担",
                    command=_toggle_reduce_motion))

    _UI_SCALE_STEPS = ("0.8", "0.9", "1.0", "1.1", "1.2", "1.3", "1.5")
    ui_scale_var = tkinter.StringVar(value=str(float(getattr(state, "UI_SCALE", 1.0))))

    def _on_ui_scale_selected(event=None):
        """切换档位: 立即 set_ui_scale (刷新全部命名字体) + 提示需重启才完全生效。"""
        try:
            eff = utils.set_ui_scale(float(ui_scale_var.get()))
        except (TypeError, ValueError):
            eff = utils.current_ui_scale()
        ui_scale_var.set(str(eff))
        _log1("界面缩放已切换到 {} 档（部分界面需重启生效）".format(eff))
        try:
            messagebox.showinfo("界面缩放",
                "已切换到 {} 档。\n\n缩放仅影响字体与关键尺寸；\n"
                "主窗口几何与已创建控件的尺寸不会自动重排，建议重启程序后完全生效。".format(eff))
        except Exception:
            pass

    def _make_scale_combo(p):
        cbo = ttk.Combobox(p, textvariable=ui_scale_var, values=_UI_SCALE_STEPS,
                           state="readonly", width=5)
        cbo.bind("<<ComboboxSelected>>", _on_ui_scale_selected)
        return cbo

    _add(row(content, ctx, "界面缩放", _make_scale_combo,
        unit="0.8-1.5", help="缩放仅影响字体与关键尺寸；部分界面需重启生效", restart=True))

    # ── 分节: Mini Bar ──
    _sec("Mini Bar")

    mini_bar_var = tkinter.BooleanVar(value=state.MINI_BAR_ENABLED)

    def _toggle_mini_bar():
        state.MINI_BAR_ENABLED = mini_bar_var.get()
        state.save_config()
        if not state.MINI_BAR_ENABLED and state.folded:
            if _toggle_fold:
                _toggle_fold()
        _flash_saved("system")

    _add(switch_row(content, ctx, "启用 Mini Bar 折叠", mini_bar_var,
                    help="标题栏显示 ⊟ 按钮，可把主窗口折叠为 Mini Bar",
                    command=_toggle_mini_bar))

    mb_width_var = tkinter.StringVar(value=str(state.MINI_BAR_WIDTH))
    _add(row(content, ctx, "面板宽度", lambda p: _validate_number(
        tkinter.Spinbox(p, textvariable=mb_width_var, from_=380, to=900,
            increment=10, width=5, font=ctx.FONT_SMALL, bg=C["ebg"], fg=C["fgb"],
            relief="solid", bd=1),
        mb_width_var, "Mini Bar 宽度", 430, int, 380, 900, card_key="system"),
        unit="px (380-900)", indent=True))

    mb_height_var = tkinter.StringVar(value=str(state.MINI_BAR_HEIGHT))
    _add(row(content, ctx, "高度", lambda p: ttk.Combobox(
        p, textvariable=mb_height_var, values=(24, 28, 30, 34, 38, 42, 48),
        state="readonly", width=4),
        unit="px (24-48，步进 4)", indent=True))

    mb_opacity_var = tkinter.StringVar(value=str(state.MINI_BAR_OPACITY))
    _add(row(content, ctx, "透明度", lambda p: _validate_number(
        tkinter.Spinbox(p, textvariable=mb_opacity_var, from_=30, to=100,
            increment=5, width=4, font=ctx.FONT_SMALL, bg=C["ebg"], fg=C["fgb"],
            relief="solid", bd=1),
        mb_opacity_var, "Mini Bar 透明度", 80, int, 30, 100, card_key="system"),
        unit="% (30-100)", indent=True))

    # ── 分节: 全局快捷键 ──
    _sec("全局快捷键")

    # 快捷键录制弹窗 (复用原有逻辑)
    def _record_hotkey(var, key_name):
        dlg = tkinter.Toplevel(root)
        dlg.title("录制快捷键 - {}".format(key_name))
        utils.place_dialog(dlg, 320, 160, parent=root)
        dlg.transient(root); dlg.grab_set()
        dlg.configure(bg=C["bgc"])
        _set_window_icon(dlg)
        tkinter.Label(dlg, text="请按下快捷键组合...\n(支持 Ctrl/Alt/Shift + 字母/数字/F1-F12)",
            font=ctx.FONT_BODY, bg=C["bgc"], fg=C["fgb"]).pack(pady=(16, 8))

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

    # 快捷键注册表 [[功能, state_key, 默认值], ...]
    _HOTKEY_DEFS = [
        ("执行默认配置",   "HOTKEY_RUN",   "F5"),
        ("单步执行",       None,           "F10"),
        ("暂停/继续执行",  "HOTKEY_PAUSE", "F6"),
        ("取消执行",       "HOTKEY_STOP",  "Shift+F5"),
        ("强制关闭",       None,           "Alt+Shift+F4"),
        ("截图",           None,           "Ctrl+Shift+A"),
    ]

    # 快捷键表格
    hotkey_table = tkinter.Frame(content, bg=C["bgc"])
    hotkey_table.columnconfigure(1, weight=1)

    # 表头
    for ci, (txt, w) in enumerate([
        ("功能", 16), ("当前绑定", 16), ("操作", 12)
    ]):
        tkinter.Label(hotkey_table, text=txt, font=ctx.FONT_SMALL,
            fg=C["fgm"], bg=C["bgc"], anchor="w", padx=4).grid(
            row=0, column=ci, sticky="w", padx=(0, 2))

    # 分隔线
    tkinter.Frame(hotkey_table, bg=C["bd"], height=sp("hairline")).grid(
        row=1, column=0, columnspan=3, sticky="ew", pady=sp("hairline"))

    # 快捷键行
    hotkey_vars = {}  # 保存 var 引用供后续绑定
    for ri, (func_name, state_key, default_val) in enumerate(_HOTKEY_DEFS):
        r = ri + 2  # 跳过表头和分隔线
        curr_val = getattr(state, state_key, "") if state_key else default_val
        if not curr_val:
            curr_val = default_val

        tkinter.Label(hotkey_table, text=func_name, font=ctx.FONT_SMALL,
            fg=C["fgb"], bg=C["bgc"], anchor="w", padx=4).grid(
            row=r, column=0, sticky="w")

        var = tkinter.StringVar(value=curr_val)
        hotkey_vars[func_name] = (var, state_key)
        key_lbl = tkinter.Label(hotkey_table, textvariable=var,
            font=utils.FONT_SMALL_BOLD, bg=C["ac"], fg=utils.text_on(C["ac"]),
            padx=6, pady=2, relief="raised", bd=1)
        key_lbl.grid(row=r, column=1, sticky="w", padx=(0, 4))

        btn_frame = tkinter.Frame(hotkey_table, bg=C["bgc"])
        btn_frame.grid(row=r, column=2, sticky="w")
        rec_btn = tkinter.Label(btn_frame, text="录制", font=FONT_TINY,
            bg=C["sc"], fg=utils.text_on(C["sc"]), padx=4, pady=1, cursor="hand2",
            relief="raised", bd=1)
        rec_btn.pack(side="left", padx=1)
        rec_btn.bind("<Button-1>", lambda e, v=var, k=state_key:
            _record_hotkey(v, k or func_name))
        clr_btn = tkinter.Label(btn_frame, text="清除", font=FONT_TINY,
            bg=C["dg"], fg=utils.text_on(C["dg"]), padx=4, pady=1, cursor="hand2",
            relief="raised", bd=1)
        clr_btn.pack(side="left", padx=1)
        clr_btn.bind("<Button-1>", lambda e, v=var, k=state_key, d=default_val:
            (v.set(default_val), setattr(state, k.upper(), default_val) if k else None,
             state.save_config(), _flash_saved("system")))

    _add(hotkey_table)

    # ── S1-04: 热键后端状态 + 热字串限制 (注册失败必须可见, 不许静默) ──
    _hk_lines, _hk_bad = [], False
    try:
        import hotkey_win as _hkwin
        _backend = str(getattr(state, "HOTKEY_BACKEND", "poll") or "poll").lower()
        _mgr = _hkwin.HotkeyManager.existing_instance()
        _errs = dict(_mgr.errors()) if _mgr is not None else {}
        if _backend != "register":
            _hk_lines.append(
                "热键后端：轮询（兼容默认）。改为 register 可启用吞键式全局热键。")
        elif _errs:
            _hk_bad = True
            _hk_lines.append("热键注册失败（该条已退回轮询，功能仍可用）：")
            for _a in sorted(_errs):
                _hk_lines.append("   · {} → {}".format(_a, _errs[_a]))
        else:
            _hk_lines.append(
                "✔ 热键后端：RegisterHotKey（不轮询、目标程序收不到该组合键）")
        _hk_lines.append(_hkwin.HotstringEngine.LIMITS_TEXT)
    except Exception:
        _hk_lines, _hk_bad = [], False
    if _hk_lines:
        _add(help_line(content, ctx, "\n".join(_hk_lines),
                       level=("err" if _hk_bad else "info")))

    _add(help_line(content, ctx,
        "提示：支持 Ctrl/Alt/Shift/Win + 字母/数字/F1-F12；录制的快捷键在所有应用中全局生效"))

    # ── 分节: 安全 ──
    _sec("安全")

    danger_var = tkinter.BooleanVar(
        value=bool(getattr(state, "CONFIRM_DANGEROUS", True)))

    def _toggle_danger():
        state.CONFIRM_DANGEROUS = bool(danger_var.get())
        state.save_config()
        _flash_saved("system")

    _add(switch_row(content, ctx, "危险动作执行前二次确认", danger_var,
                    help="电源操作 / 永久删除 / 结束进程；无界面时一律拒绝",
                    command=_toggle_danger))

    danger_timeout_var = tkinter.StringVar(
        value=str(int(getattr(state, "CONFIRM_DANGEROUS_TIMEOUT_SEC", 15) or 15)))
    _add(row(content, ctx, "确认超时", lambda p: _validate_number(
        tkinter.Spinbox(p, textvariable=danger_timeout_var, from_=1, to=600,
            increment=5, width=5, font=ctx.FONT_SMALL, bg=C["ebg"], fg=C["fgb"],
            relief="solid", bd=1),
        danger_timeout_var, "危险动作确认超时", 15, int, 1, 600, card_key="system"),
        unit="秒（无响应视为拒绝）"))

    # 系统卡: 任一控件改动 → 600ms 防抖后走 apply 即时落盘
    _track_card_vars("system", (auto_start_var, tray_var, compact_var, mini_bar_var,
                                mb_width_var, mb_height_var, mb_opacity_var,
                                ui_scale_var, danger_var, danger_timeout_var))

    return {
        "card": card, "content": content, "title": title,
        "auto_start_var": auto_start_var, "tray_var": tray_var,
        "compact_var": compact_var, "help_modal_var": help_modal_var,
        "mini_bar_var": mini_bar_var, "mb_width_var": mb_width_var,
        "mb_height_var": mb_height_var, "mb_opacity_var": mb_opacity_var,
        "ui_scale_var": ui_scale_var, "hotkey_vars": hotkey_vars,
        "danger_var": danger_var, "danger_timeout_var": danger_timeout_var,
    }


def apply(ctx, handles):
    """「系统」整卡统一收口: 写回本卡全部 state 键并做数值夹取/吸附。

    修复 D-1/D-2 —— 旧 _apply_mb_settings 从未被调用, 导致 Mini Bar 宽度/高度/
    透明度只是安慰剂 (改了不生效)。
    """
    state.AUTO_START = handles["auto_start_var"].get()
    state.MINIMIZE_TO_TRAY = handles["tray_var"].get()
    state.COMPACT_MODE = handles["compact_var"].get()
    state.MINI_BAR_ENABLED = handles["mini_bar_var"].get()
    try:
        state.HELP_MODAL = handles["help_modal_var"].get()
    except Exception:
        pass
    try:
        w = int(handles["mb_width_var"].get())
    except (TypeError, ValueError):
        w = state.MINI_BAR_WIDTH
    state.MINI_BAR_WIDTH = max(380, min(900, w))
    try:
        h = int(handles["mb_height_var"].get())
    except (TypeError, ValueError):
        h = state.MINI_BAR_HEIGHT
    # 夹取到 [24,48] 并吸附到 4 的倍数
    state.MINI_BAR_HEIGHT = int(round(max(24, min(48, h)) / 4.0) * 4)
    try:
        op = int(handles["mb_opacity_var"].get())
    except (TypeError, ValueError):
        op = state.MINI_BAR_OPACITY
    state.MINI_BAR_OPACITY = max(30, min(100, op))
    # 界面缩放档位持久化 (字体已在切换回调里 fontconfigure 过, 此处仅落盘)
    try:
        state.UI_SCALE = max(0.8, min(1.5, float(handles["ui_scale_var"].get())))
    except (TypeError, ValueError):
        state.UI_SCALE = 1.0
    # L5 #6: 危险动作确认开关 + 超时（缺键用默认值，向后兼容旧 handles）
    try:
        state.CONFIRM_DANGEROUS = bool(handles["danger_var"].get())
    except Exception:
        pass
    try:
        t = int(handles["danger_timeout_var"].get())
        state.CONFIRM_DANGEROUS_TIMEOUT_SEC = max(1, min(600, t))
    except Exception:
        pass
    # 本卡内由独立控件写入的快捷键键 (显式经 handles 传递, 无隐式闭包顺序)
    try:
        for _name, (_v, _sk) in handles["hotkey_vars"].items():
            if _sk:
                setattr(state, _sk.upper(), _v.get())
    except Exception:
        pass