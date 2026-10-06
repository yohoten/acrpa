# -*- coding: utf-8 -*-
"""ui.settings.cards.record — ◉ 录制设置卡 (原 settings_window 1333-1410 行段)。

导出:
    build(parent, ctx) -> handles
    apply(ctx, handles) -> None       # 等价原 _apply_record_settings
"""
import tkinter
from tkinter import ttk

import state
from utils import FONT_TINY, place_dialog

CARD_KEY = "record"
ROW = 3

# 录制模式下拉选项 ↔ state 存储值映射（单一来源，UI 与保存共用）
_REC_MODE_MAP = {"绝对坐标": "absolute", "相对窗口": "relative"}


def build(parent, ctx):
    C = ctx.C
    _btn = ctx.btn
    _flash_saved = ctx.flash_saved
    _set_window_icon = ctx.set_window_icon
    _make_collapsible_card = ctx.make_collapsible_card
    _make_badge = ctx.make_badge
    _register_nav_card = ctx.register_nav_card
    _track_card_vars = ctx.track_card_vars
    root = ctx.root

    card, content, title, _ = _make_collapsible_card(parent, "录制设置", "◉")
    card.grid(row=ROW, column=0, sticky="ew", **ctx.PAD)
    _make_badge(CARD_KEY, title)
    _register_nav_card(CARD_KEY, card)

    # 录制模式行
    rec_mode_frame = tkinter.Frame(content, bg=C["bgc"])
    rec_mode_frame.grid(row=0, column=0, sticky="ew", padx=8, pady=(2, 2))
    tkinter.Label(rec_mode_frame, text="坐标模式:", font=ctx.FONT_BODY,
        fg=C["fgb"], bg=C["bgc"]).pack(side="left", padx=(0, 8))
    _rec_mode_label = {v: k for k, v in _REC_MODE_MAP.items()}
    recording_mode_var = tkinter.StringVar(value=_rec_mode_label.get(state.RECORDING_MODE, "绝对坐标"))
    ttk.Combobox(rec_mode_frame, textvariable=recording_mode_var,
        values=("绝对坐标", "相对窗口"), state="readonly", width=8).pack(side="left")

    # 录制模式说明
    rec_mode_desc = tkinter.Frame(content, bg=C["bgc"])
    rec_mode_desc.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 4))
    tkinter.Label(rec_mode_desc, text="绝对坐标：以屏幕左上角为原点，不同分辨率下可能偏移",
        font=FONT_TINY, fg=C["fgm"], bg=C["bgc"]).pack(anchor="w")
    tkinter.Label(rec_mode_desc, text="相对窗口：以激活窗口左上角为原点，窗口移动/缩放后仍准确",
        font=FONT_TINY, fg=C["fgm"], bg=C["bgc"]).pack(anchor="w")

    # 停止录制快捷键行
    rec_hotkey_frame = tkinter.Frame(content, bg=C["bgc"])
    rec_hotkey_frame.grid(row=2, column=0, sticky="ew", padx=8, pady=(2, 6))
    tkinter.Label(rec_hotkey_frame, text="停止录制:", font=ctx.FONT_BODY,
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
        place_dialog(dlg, 320, 160, parent=root)
        dlg.transient(root); dlg.grab_set()
        dlg.configure(bg=C["bgc"])
        _set_window_icon(dlg)
        tkinter.Label(dlg, text="请按下快捷键组合...", font=ctx.FONT_BODY,
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
    _btn(rec_hotkey_frame, "录制", _record_rec_stop, "sc", "white",
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

    return {
        "card": card, "content": content, "title": title,
        "recording_mode_var": recording_mode_var,
        "rec_stop_hk_var": rec_stop_hk_var,
    }


def apply(ctx, handles):
    """保存「录制设置」卡配置 (等价原 _apply_record_settings)。"""
    state.RECORDING_MODE = _REC_MODE_MAP.get(handles["recording_mode_var"].get(), "absolute")
    state.RECORDING_STOP_HOTKEY = handles["rec_stop_hk_var"].get()
