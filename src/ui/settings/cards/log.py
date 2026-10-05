# -*- coding: utf-8 -*-
"""ui.settings.cards.log — ▤ 日志卡 (原 settings_window 1415-1458 行段)。

导出:
    build(parent, ctx) -> handles
    apply(ctx, handles) -> None       # 等价原 _apply_log_settings
"""
import os
import tkinter
from tkinter import ttk, messagebox

import state
from utils import FONT_TINY

CARD_KEY = "log"
ROW = 4


def build(parent, ctx):
    C = ctx.C
    _btn = ctx.btn
    _log1 = ctx.log1
    _tlog = ctx.tlog
    APP_ROOT = ctx.APP_ROOT
    _validate_number = ctx.validate_number
    _make_collapsible_card = ctx.make_collapsible_card
    _make_badge = ctx.make_badge
    _register_nav_card = ctx.register_nav_card
    _track_card_vars = ctx.track_card_vars

    card, content, title, _ = _make_collapsible_card(parent, "日志", "▤")
    card.grid(row=ROW, column=0, sticky="ew", **ctx.PAD)
    _make_badge(CARD_KEY, title)
    _register_nav_card(CARD_KEY, card)

    def _export_log():
        import datetime
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        fp = os.path.join(APP_ROOT, "logs", "log_{}.txt".format(ts))
        _tlog.export(fp); _log1("日志已导出: {}".format(fp))
        messagebox.showinfo("导出成功", "日志已保存到:\n{}".format(fp))

    log_save_var = tkinter.BooleanVar(value=state.ENABLE_LOG_SAVING)
    log_save_cb = ttk.Checkbutton(content, text="自动保存日志到文件",
        variable=log_save_var)
    log_save_cb.grid(row=0, column=0, sticky="w", padx=8, pady=(2,1))

    log_level_frame = tkinter.Frame(content, bg=C["bgc"])
    log_level_frame.grid(row=1, column=0, sticky="ew", padx=8, pady=1)
    tkinter.Label(log_level_frame, text="记录级别", font=ctx.FONT_SMALL,
        fg=C["fgm"], bg=C["bgc"]).pack(side="left", padx=(0,6))
    log_level_var = tkinter.StringVar(value=str(state.LOG_LEVEL))
    log_level_combo = ttk.Combobox(log_level_frame, textvariable=log_level_var,
        values=("0=DEBUG", "1=INFO", "2=WARNING", "3=ERROR"),
        state="readonly", width=14)
    log_level_combo.pack(side="left")
    _export_log_btn = _btn(log_level_frame, "导出日志", _export_log, "ac", "white", tip="导出运行日志到文件")
    _export_log_btn.pack(side="right")

    log_retention_frame = tkinter.Frame(content, bg=C["bgc"])
    log_retention_frame.grid(row=2, column=0, sticky="ew", padx=8, pady=(1,1))
    tkinter.Label(log_retention_frame, text="保留天数", font=ctx.FONT_SMALL,
        fg=C["fgm"], bg=C["bgc"]).pack(side="left", padx=(0,6))
    log_retention_var = tkinter.StringVar(value=str(state.LOG_RETENTION_DAYS))
    log_retention_spin = _validate_number(tkinter.Spinbox(log_retention_frame, textvariable=log_retention_var,
        from_=1, to=90, width=5, font=ctx.FONT_SMALL,
        bg=C["ebg"], fg=C["fgb"], relief="solid", bd=1), log_retention_var,
        "日志保留天数", 7, int, 0, 90, card_key="log")
    log_retention_spin.pack(side="left")
    tkinter.Label(log_retention_frame, text="天 (0=永久保留)", font=FONT_TINY,
        fg=C["fgm"], bg=C["bgc"]).pack(side="left", padx=(4,0))

    # 日志保存路径提示
    _log_dir_display = os.path.join(APP_ROOT, "logs")
    _log_path_label = tkinter.Label(content,
        text="保存位置: {}".format(_log_dir_display),
        font=FONT_TINY, fg=C["fgm"], bg=C["bgc"])
    _log_path_label.grid(row=3, column=0, sticky="w", padx=8, pady=(0,6))

    # 绑定防抖保存 (日志卡)
    _track_card_vars("log", (log_save_var, log_level_var, log_retention_var))

    return {
        "card": card, "content": content, "title": title,
        "log_save_var": log_save_var, "log_level_var": log_level_var,
        "log_retention_var": log_retention_var,
    }


def apply(ctx, handles):
    """保存「日志」卡配置 (等价原 _apply_log_settings)。"""
    state.ENABLE_LOG_SAVING = handles["log_save_var"].get()
    try:
        state.LOG_LEVEL = int(handles["log_level_var"].get().split("=")[0])
    except (ValueError, IndexError):
        state.LOG_LEVEL = 1
    try:
        state.LOG_RETENTION_DAYS = int(handles["log_retention_var"].get())
    except ValueError:
        state.LOG_RETENTION_DAYS = 7
