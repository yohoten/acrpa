# -*- coding: utf-8 -*-
"""ui.settings.cards.sched — ⏱ 定时调度卡 (原 settings_window 1239-1331 行段)。

导出:
    build(parent, ctx) -> handles
    apply(ctx, handles) -> None       # 等价原 _apply_sched_settings
"""
import tkinter
from tkinter import ttk

import state
import scheduler as sched
from utils import FONT_TINY

CARD_KEY = "sched"
ROW = 2


def build(parent, ctx):
    C = ctx.C
    _btn = ctx.btn
    _log1 = ctx.log1
    _make_collapsible_card = ctx.make_collapsible_card
    _make_badge = ctx.make_badge
    _register_nav_card = ctx.register_nav_card
    _track_card_vars = ctx.track_card_vars
    root = ctx.root

    card, content, title, _ = _make_collapsible_card(parent, "定时调度", "⏱")
    card.grid(row=ROW, column=0, sticky="ew", **ctx.PAD)
    _make_badge(CARD_KEY, title)
    _register_nav_card(CARD_KEY, card)

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
    enable_frame = tkinter.Frame(content, bg=C["bgc"])
    enable_frame.grid(row=0, column=0, sticky="ew", padx=8, pady=(2,2))

    ttk.Checkbutton(enable_frame, text="启用定时",
        variable=state._sched_enabled_var).pack(side="left", padx=(0,8))

    # 定时调度说明
    sched_desc_frame = tkinter.Frame(content, bg=C["bgc"])
    sched_desc_frame.grid(row=4, column=0, sticky="ew", padx=8, pady=(0, 2))
    tkinter.Label(sched_desc_frame, text="启用后按设定时间自动执行当前脚本", font=FONT_TINY,
        fg=C["fgm"], bg=C["bgc"]).pack(anchor="w")

    tkinter.Label(enable_frame, text="时刻:", font=ctx.FONT_BODY,
        fg=C["fgb"], bg=C["bgc"]).pack(side="left", padx=(0,4))
    tf = tkinter.Frame(enable_frame, bg=C["bgc"]); tf.pack(side="left")
    ttk.Combobox(tf, textvariable=_sched_hour_var, values=["{:02d}".format(i) for i in range(24)],
        state="readonly", width=4).pack(side="left")
    tkinter.Label(tf, text=":", font=ctx.FONT_BODY,
        fg=C["fgb"], bg=C["bgc"]).pack(side="left", padx=2)
    ttk.Combobox(tf, textvariable=_sched_minute_var, values=["{:02d}".format(i) for i in range(60)],
        state="readonly", width=4).pack(side="left")

    # Repeat + weekdays
    repeat_frame = tkinter.Frame(content, bg=C["bgc"])
    repeat_frame.grid(row=1, column=0, sticky="ew", padx=8, pady=(0,2))

    tkinter.Label(repeat_frame, text="重复:", font=ctx.FONT_BODY,
        fg=C["fgb"], bg=C["bgc"]).pack(side="left", padx=(0,4))
    _rep_combo = ttk.Combobox(repeat_frame, textvariable=_sched_repeat_var,
        values=("仅一次", "每天", "每周"), state="readonly", width=8)
    _rep_combo.pack(side="left", padx=(0,8))

    _wf = tkinter.Frame(repeat_frame, bg=C["bgc"]); _wf.pack(side="left")
    tkinter.Label(_wf, text="星期:", font=ctx.FONT_BODY,
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
    next_frame = tkinter.Frame(content, bg=C["bgc"])
    next_frame.grid(row=2, column=0, sticky="ew", padx=8, pady=(0,2))

    tkinter.Label(next_frame, text="下次执行:", font=ctx.FONT_BODY,
        fg=C["fgb"], bg=C["bgc"]).pack(side="left", padx=(0,6))
    _sched_next_label = tkinter.Label(next_frame,
        text=state.SCHED_NEXT_RUN if state.SCHED_NEXT_RUN else "--:--",
        font=ctx.FONT_BODY, fg=C["ac"], bg=C["bgc"])
    _sched_next_label.pack(side="left")
    # 登记到窗口 (供 ACRPA._periodic 经 update_sched_next_label 刷新) + 注入调度器
    ctx.set_sched_label(_sched_next_label)
    try:
        sched.set_gui_refs(root, state._sched_enabled_var, _sched_next_label)
    except Exception:
        pass

    # === 管理计划任务按钮 ──
    sched_mgr_frame = tkinter.Frame(content, bg=C["bgc"])
    sched_mgr_frame.grid(row=3, column=0, sticky="ew", padx=8, pady=(2, 6))
    from dialogs import open_sched_manager
    _btn(sched_mgr_frame, "[+] 管理计划任务", open_sched_manager, "ac", "white", tip="打开计划任务管理窗口").pack(side="left", padx=(0, 4))
    tkinter.Label(sched_mgr_frame, text="(多任务管理 · 执行日志)",
        font=ctx.FONT_SMALL, bg=C["bgc"], fg=C["fgm"]).pack(side="left")

    # 绑定防抖保存 (定时调度卡: 含重复模式切换/星期勾选/启用开关)
    _track_card_vars("sched", (state._sched_enabled_var, _sched_hour_var,
        _sched_minute_var, _sched_repeat_var) + tuple(_sched_weekday_vars))

    return {
        "card": card, "content": content, "title": title,
        "_sched_hour_var": _sched_hour_var, "_sched_minute_var": _sched_minute_var,
        "_sched_repeat_var": _sched_repeat_var, "_sched_weekday_vars": _sched_weekday_vars,
    }


def apply(ctx, handles):
    """保存「定时调度」卡配置并安全重启调度器 (等价原 _apply_sched_settings)。"""
    _log1 = ctx.log1
    _main_run = ctx.main_run

    state.SCHED_ENABLED = state._sched_enabled_var.get()
    try:
        state.SCHED_HOUR = int(handles["_sched_hour_var"].get())
    except (ValueError, TypeError):
        state.SCHED_HOUR = 9
    try:
        state.SCHED_MINUTE = int(handles["_sched_minute_var"].get())
    except (ValueError, TypeError):
        state.SCHED_MINUTE = 0
    mode_map = {"仅一次": "once", "每天": "daily", "每周": "weekly"}
    state.SCHED_REPEAT_MODE = mode_map.get(handles["_sched_repeat_var"].get(), "once")
    state.SCHED_WEEKDAYS = [v.get() for v in handles["_sched_weekday_vars"]]
    sched_enabled = state.SCHED_ENABLED
    try:
        sched.stop_scheduler()
        state.SCHED_ENABLED = sched_enabled  # restore (stop_scheduler sets it False)
        sched.calc_next_run()
        if sched_enabled:
            sched.start_scheduler(_main_run, state.save_config)
    except Exception as e:
        _log1("定时调度保存失败: {}".format(e), "error")
