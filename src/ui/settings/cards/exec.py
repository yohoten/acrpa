# -*- coding: utf-8 -*-
"""ui.settings.cards.exec — ⚙ 基础执行卡 (原 settings_window open_settings_window 内
767-890 行段)。

导出:
    build(parent, ctx) -> handles     # 构建卡片, 返回句柄 (含该卡 Variables)
    apply(ctx, handles) -> None       # 等价原 _apply_exec_settings, 写回 state
"""
import tkinter
from tkinter import ttk, messagebox

import state
from utils import FONT_TINY, FONT_ICON

CARD_KEY = "exec"
ROW = 0


def build(parent, ctx):
    C = ctx.C
    # 工厂显式绑定 (等价原闭包; 便于静态断言按 _track_card_vars("exec", ...) 匹配)
    _make_collapsible_card = ctx.make_collapsible_card
    _make_badge = ctx.make_badge
    _register_nav_card = ctx.register_nav_card
    _validate_number = ctx.validate_number
    _track_card_vars = ctx.track_card_vars

    card, content, title, _ = _make_collapsible_card(parent, "基础执行", "⚙")
    card.grid(row=ROW, column=0, sticky="ew", **ctx.PAD)
    _make_badge(CARD_KEY, title)
    _register_nav_card(CARD_KEY, card)

    # === Retry settings row ──
    retry_frame = tkinter.Frame(content, bg=C["bgc"])
    retry_frame.grid(row=0, column=0, sticky="ew", padx=8, pady=2)

    tkinter.Label(retry_frame, text="重试:", font=ctx.FONT_BODY, fg=C["fgb"],
        bg=C["bgc"]).pack(side="left", padx=(0,4))
    retry_var = tkinter.StringVar(value=str(state.RETRY_MAX))
    retry_entry = _validate_number(tkinter.Entry(retry_frame, textvariable=retry_var, width=6,
        font=ctx.FONT_BODY, relief="solid", bd=1,
        bg=C["ebg"], fg=C["fgb"]), retry_var, "重试次数", 3, int, 0, card_key="exec")
    retry_entry.pack(side="left", padx=(0,6))

    tkinter.Label(retry_frame, text="间隔:", font=ctx.FONT_BODY, fg=C["fgb"],
        bg=C["bgc"]).pack(side="left", padx=(0,4))
    interval_var = tkinter.StringVar(value=str(state.RETRY_INTERVAL))
    interval_entry = _validate_number(tkinter.Entry(retry_frame, textvariable=interval_var, width=6,
        font=ctx.FONT_BODY, relief="solid", bd=1,
        bg=C["ebg"], fg=C["fgb"]), interval_var, "重试间隔", 1.0, float, 0, card_key="exec")
    interval_entry.pack(side="left", padx=(0,6))

    tkinter.Label(retry_frame, text="识图超时:", font=ctx.FONT_BODY, fg=C["fgb"],
        bg=C["bgc"]).pack(side="left", padx=(0,4))
    timeout_var = tkinter.StringVar(value=str(state.IMAGE_TIMEOUT))
    timeout_entry = _validate_number(tkinter.Entry(retry_frame, textvariable=timeout_var, width=6,
        font=ctx.FONT_BODY, relief="solid", bd=1,
        bg=C["ebg"], fg=C["fgb"]), timeout_var, "识图超时", 5.0, float, 0, card_key="exec")
    timeout_entry.pack(side="left")

    # 识图超时说明
    tkinter.Label(retry_frame, text="图片识别等待时间，超时自动跳过", font=FONT_TINY,
        fg=C["fgm"], bg=C["bgc"]).pack(side="left", padx=(4, 0))

    # === 执行模式选择 (P1-6: Radiobutton 替代 DD 复选框) ──
    mode_frame = tkinter.Frame(content, bg=C["bgc"])
    mode_frame.grid(row=1, column=0, sticky="ew", padx=8, pady=(2, 2))

    tkinter.Label(mode_frame, text="执行模式:", font=ctx.FONT_BODY,
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
    dd_info_btn = tkinter.Label(mode_frame, text="  ⓘ DD 驱动详情", font=FONT_TINY,
        fg=C["ac"], bg=C["bgc"], cursor="hand2")
    dd_info_btn.grid(row=_mode_row, column=0, sticky="w", pady=(2, 4))

    def _show_dd_info(event):
        messagebox.showinfo("DD 驱动信息",
            "DD 驱动说明:\n\n"
            "✔ 优势:\n"
            "• 输入速度提升 3-5 倍\n"
            "• 支持后台窗口操作\n"
            "• 难以被反自动化检测\n\n"
            "⚠ 注意:\n"
            "• 需要以管理员身份运行\n"
            "• 仅支持 Windows 系统\n"
            "• 加载失败会自动回退到 PyAutoGUI")
    dd_info_btn.bind("<Button-1>", _show_dd_info)

    # === Hint + App protection ──
    hint_frame = tkinter.Frame(content, bg=C["bgc"])
    hint_frame.grid(row=3, column=0, sticky="ew", padx=8, pady=(0,4))

    tkinter.Label(hint_frame, text="(默认5秒，超时自动跳过)", font=ctx.FONT_SMALL,
        fg=C["fgm"], bg=C["bgc"]).pack(side="left")

    app_protect_var = tkinter.BooleanVar(value=state.APP_PROTECT)
    ttk.Checkbutton(hint_frame, text="关闭时确认", variable=app_protect_var).pack(side="right")
    check_update_var = tkinter.BooleanVar(value=state.CHECK_UPDATE)
    ttk.Checkbutton(hint_frame, text="检查更新", variable=check_update_var).pack(side="right", padx=(8,0))

    # Fail-safe setting
    failsafe_frame = tkinter.Frame(content, bg=C["bgc"])
    failsafe_frame.grid(row=2, column=0, sticky="ew", padx=10, pady=(4, 4))

    tkinter.Label(failsafe_frame, text="安全设置:", font=ctx.FONT_BODY,
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

    failsafe_info_btn = tkinter.Label(failsafe_frame, text="ⓘ", font=FONT_ICON,
        fg=C["ac"], bg=C["bgc"], cursor="hand2")
    failsafe_info_btn.pack(side="left")
    failsafe_info_btn.bind("<Button-1>", _show_failsafe_info)

    # 绑定防抖保存 (基础执行卡)
    _track_card_vars("exec", (retry_var, interval_var, timeout_var,
        input_mode_var, app_protect_var, check_update_var, failsafe_var))

    return {
        "card": card, "content": content, "title": title,
        "retry_var": retry_var, "interval_var": interval_var,
        "timeout_var": timeout_var, "input_mode_var": input_mode_var,
        "app_protect_var": app_protect_var, "check_update_var": check_update_var,
        "failsafe_var": failsafe_var,
    }


def apply(ctx, handles):
    """保存「基础执行」卡配置 (等价原 _apply_exec_settings)。"""
    try:
        state.RETRY_MAX = int(handles["retry_var"].get())
    except ValueError:
        state.RETRY_MAX = 3; handles["retry_var"].set("3")
    try:
        state.RETRY_INTERVAL = float(handles["interval_var"].get())
    except ValueError:
        state.RETRY_INTERVAL = 1.0; handles["interval_var"].set("1.0")
    try:
        state.IMAGE_TIMEOUT = float(handles["timeout_var"].get())
    except ValueError:
        state.IMAGE_TIMEOUT = 5.0; handles["timeout_var"].set("5.0")
    state.APP_PROTECT = handles["app_protect_var"].get()
    state.CHECK_UPDATE = handles["check_update_var"].get()
    state.INPUT_MODE = handles["input_mode_var"].get()
    state.USE_DD_DRIVER = (handles["input_mode_var"].get() == "dd")
    state.FAILSAFE = handles["failsafe_var"].get()
    # 注: engine.retry / retry_interval 由配置变更监听器自动同步 (ACRPA._on_config_changed)
