# -*- coding: utf-8 -*-
"""ui.settings.cards._layout — 设置卡「表单化」纯布局工厂。

解决设置页「一行塞 3~5 个控件 + 3~5 个灰说明」导致的过于紧凑/难扫视问题。
提供三类原语 (均只做布局, 不碰 state / 不碰保存; 密度走 utils.TOKENS, 不硬编码):

    section(parent, ctx, title) -> Frame                卡内分节 (小标题 + hairline 分隔线)
    row(parent, ctx, label, builder, ...) -> Frame      表单行 (标签列 | 控件列 | 单位 |
                                                        需重启徽标 | 换行的帮助文本)
    help_line(parent, ctx, text, level=...) -> Frame    卡内整行提示 (FONT_TINY 灰)

契约:
    · row() 的 builder 是「以行内 Frame 为父」创建控件的回调: builder(fr) -> widget。
      widget 由 row() 负责 grid 进控件列 (col1 / 开关行占 col0+col1)。
    · 外层网格行号仍由各卡自行管理 (本工厂只负责「行内」三列布局)。
    · 颜色/字体全部取自 ctx (C / FONT_SMALL), 主题切换由 window.refresh_theme 递归 walk 覆盖。
"""
import tkinter

from utils import FONT_TINY, FONT_SMALL_BOLD
from ui.widgets.rounded_button import RoundedSwitch

__all__ = ["section", "row", "switch_row", "help_line"]


def _C(ctx):
    return ctx.C


def _sp(ctx, key):
    return ctx.sp(key)


def section(parent, ctx, title):
    """卡内分节: 小标题 + 1px 发丝分隔线。"""
    C = _C(ctx)
    fr = tkinter.Frame(parent, bg=C["bgc"])
    fr.columnconfigure(0, weight=1)
    tkinter.Label(fr, text=title, font=FONT_SMALL_BOLD,
        bg=C["bgc"], fg=C["fgt"], anchor="w").grid(
        row=0, column=0, sticky="ew")
    tkinter.Frame(fr, bg=C["bd"], height=_sp(ctx, "hairline")).grid(
        row=1, column=0, sticky="ew", pady=(_sp(ctx, "gap_tight"), _sp(ctx, "sp_sm")))
    return fr


def row(parent, ctx, label, builder, help=None, unit=None, restart=False, indent=False):
    """标准表单行 (builder 模式)。

    label   : 左侧标签文本 (传 "" / None 表示开关行, 控件直接顶到最左)。
    builder : builder(fr) -> widget, 以行内 Frame 为父创建控件 (不自行 grid)。
    help    : 关键说明, 换行置于控件下方 (FONT_TINY 灰, 替代旧「行尾塞灰字」)。
    unit    : 单位/范围, 置于控件右 (FONT_TINY 灰, 如 "px (380–900)")。
    restart : True 时在行尾加 ⏱ (U+23F1)「需重启生效」徽标, 并汇总到状态条计数。
    indent  : True 时整行左侧缩进 (联动子行, 相对父开关/父选项)。

    返回行容器 (indent 时返回外包缩进容器, 否则返回行 Frame), 由调用方 grid 到内容区。
    """
    C = _C(ctx)

    container = parent
    if indent:
        # 联动子行: 先造外包容器, 行 Frame 以其为父 → 左缩进不影响标签列对齐
        container = tkinter.Frame(parent, bg=C["bgc"])
        container.columnconfigure(0, weight=1)

    fr = tkinter.Frame(container, bg=C["bgc"])
    fr.columnconfigure(0, minsize=_sp(ctx, "form_label_w"))
    fr.columnconfigure(1, weight=1)
    if indent:
        fr.grid(row=0, column=0, sticky="ew", padx=(_sp(ctx, "sp_xl"), 0))

    # builder 的父容器必须是「控件格」: Tk 的 grid 只能把控件放进它自己的 parent,
    # 事后再 grid 到别的 Frame 是无效的 (会静默留在 fr 里)。
    field = tkinter.Frame(fr, bg=C["bgc"])
    widget = builder(field)

    # 控件与「单位 / 重启徽标」必须同处一格: 若把单位放到独立的 weight=1 列,
    # 输入框会被拉伸到整行宽、单位被推到最右, 数字框尤其失真。
    expandable = True
    try:
        expandable = widget.winfo_class() in ("Entry", "TEntry", "Text", "Frame")
    except Exception:
        pass
    # 带单位的行都是短数值输入, 不能拉满整行, 否则单位和输入框会被拉开一大截
    if unit:
        expandable = False

    if label:
        # §7: 标签右对齐 → 冒号列对齐 (原左对齐导致长标签把控件列推歪)
        tkinter.Label(fr, text=label, font=ctx.FONT_SMALL,
            bg=C["bgc"], fg=C["fgm"], anchor="e").grid(
            row=0, column=0, sticky="e", padx=(0, _sp(ctx, "sp_sm")))
        field.grid(row=0, column=1, sticky="ew")
        if expandable:
            field.columnconfigure(0, weight=1)
        widget.grid(row=0, column=0, sticky="ew" if expandable else "w")
        tail_col = 1
    else:
        # 开关/复选框行: 控件自带文案, 直接占据标签列 + 控件列
        field.grid(row=0, column=0, columnspan=2, sticky="ew")
        if expandable:
            field.columnconfigure(0, weight=1)
        # 单选项不拉满整行, 否则空白区也成了可点击区域
        widget.grid(row=0, column=0, sticky="ew" if expandable else "w")
        tail_col = 1
    tail = field
    help_span = 2

    if unit:
        tkinter.Label(tail, text=unit, font=FONT_TINY,
            bg=C["bgc"], fg=C["fgm"]).grid(row=0, column=tail_col, sticky="w",
            padx=(_sp(ctx, "sp_sm"), 0))
        tail_col += 1
    if restart:
        tkinter.Label(tail, text="⏱ 需重启", font=FONT_TINY,
            bg=C["bgc"], fg=C["wn"]).grid(row=0, column=tail_col, sticky="w",
            padx=(_sp(ctx, "sp_sm"), 0))
        tail_col += 1
        # 汇总到窗口底部状态栏「需重启项数」
        _note = getattr(ctx, "note_restart", None)
        if _note:
            try:
                _note()
            except Exception:
                pass

    if help:
        help_label = tkinter.Label(fr, text=help, font=FONT_TINY,
            bg=C["bgc"], fg=C["fgm"], anchor="w", justify="left",
            wraplength=_sp(ctx, "help_wrap"))
        help_label.grid(row=1, column=0, columnspan=help_span, sticky="w",
            pady=(_sp(ctx, "gap_tight"), 0))

        def fit_help(event, label=help_label):
            available = max(180, int(event.width) - _sp(ctx, "sp_sm"))
            if int(label.cget("wraplength")) != available:
                label.configure(wraplength=available)
        fr.bind("<Configure>", fit_help)

    return container if indent else fr


def switch_row(parent, ctx, text, variable, help="", command=None, restart=False):
    """即时启停设置行：左侧名称与说明，右侧自绘 Switch。"""
    C = _C(ctx)

    def build_switch(row_parent):
        frame = tkinter.Frame(row_parent, bg=C["bgc"])
        frame.columnconfigure(0, weight=1)
        tkinter.Label(frame, text=text, font=ctx.FONT_SMALL,
                      bg=C["bgc"], fg=C["fgb"], anchor="w").grid(
            row=0, column=0, sticky="w", padx=(0, _sp(ctx, "sp_lg")))
        RoundedSwitch(frame, variable=variable, command=command, colors=C).grid(
            row=0, column=1, sticky="e")
        return frame

    return row(parent, ctx, "", build_switch, help=help, restart=restart)


def help_line(parent, ctx, text, level="info"):
    """卡内整行提示行 (机制/副作用说明, 不参与保存)。level: info(dim) / warn(黄) / err(红)。"""
    C = _C(ctx)
    fg = {"info": C["fgm"], "warn": C["wn"], "err": C["dg"]}.get(level, C["fgm"])
    fr = tkinter.Frame(parent, bg=C["bgc"])
    fr.columnconfigure(0, weight=1)
    label = tkinter.Label(fr, text=text, font=FONT_TINY, bg=C["bgc"], fg=fg,
        anchor="w", justify="left", wraplength=_sp(ctx, "help_wrap"))
    label.grid(row=0, column=0, sticky="w")

    def fit_text(event):
        available = max(180, int(event.width) - _sp(ctx, "sp_sm"))
        if int(label.cget("wraplength")) != available:
            label.configure(wraplength=available)
    fr.bind("<Configure>", fit_text)
    return fr