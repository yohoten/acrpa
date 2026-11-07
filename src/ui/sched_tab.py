# -*- coding: utf-8 -*-
"""ui.sched_tab — 「定时任务」Tab (顶部入口 + 左管理 + 右设置 + 底部日志)。

布局: 顶部 = 全局启用开关 + 下次执行预览 (轻量启停, 无卡顿)；
      中部 = 左右可伸缩 PanedWindow (左=任务管理, 右=定时设置)；
      底部 = 可折叠「执行记录」面板。

对外契约 (仿照 ui.workflow_view):
    build(parent, *, colors, fonts, deps=None, tab=None) -> dict
    get_widgets() -> dict
    refresh_theme(dark, colors, prev=None) -> None
    update_sched_next_label(text) -> None
    refresh_tasks() -> None
"""
import tkinter
from tkinter import ttk

import state
import scheduler as sched
import utils
from ui import theme as ui_theme
from ui.widgets.rounded_button import RoundedSwitch

# ── 模块级全局 (build 时注入) ──────────────────────────────
C = None
_FONTS = {}
_CTX = {}
_win = None
_tab = None
_left = None
_right = None
_paned = None
_vpaned = None
_top = None
_log_frame = None
_built = False

# 设置控件 / 变量
_sched_hour_var = None
_sched_minute_var = None
_sched_repeat_var = None
_sched_weekday_vars = None
_sched_poll_var = None
_sched_next_label = None
_weekday_frame = None
_switch_widget = None
_save_after = None       # 设置变更防抖 (→ _apply_schedule)
_save_cfg_after = None   # 启用开关防抖 (→ 仅写盘)
_log_collapsed = False
_log_toggle_btn = None

# 任务管理器句柄 (dialogs.build_sched_manager 返回值)
_manager = None
# 执行记录面板句柄 (dialogs.build_sched_log 返回值)
_log = None
# 任务统计标签 (右侧概览)
_stats_label = None

_WEEKDAYS = ["一", "二", "三", "四", "五", "六", "日"]
_REPEAT_LABELS = ("仅一次", "每天", "每周")
_REPEAT_KEYS = {"仅一次": "once", "每天": "daily", "每周": "weekly"}
_REPEAT_DISPLAY = {"once": "仅一次", "daily": "每天", "weekly": "每周"}


# ── 工具 ──────────────────────────────────────────────────
def _sp(key, default=8):
    """优先使用注入的 sp，否则回退 utils.sp。"""
    try:
        f = _CTX.get("sp")
        if f is not None:
            return f(key)
    except Exception:
        pass
    try:
        return utils.sp(key)
    except Exception:
        return default


def _log(msg, level="info"):
    log1 = _CTX.get("log1")
    if log1 is not None:
        try:
            log1(msg, level)
            return
        except Exception:
            pass
    try:
        utils.log1(msg, level)
    except Exception:
        pass


def _clamp_int(var, lo, hi, default):
    try:
        v = int(str(var.get()).strip())
    except Exception:
        v = default
    return max(lo, min(hi, v))


# ── 设置保存 (防抖 → 应用) ────────────────────────────────
def _schedule_save(*_args):
    global _save_after
    if _win is None:
        return
    if _save_after is not None:
        try:
            _win.after_cancel(_save_after)
        except Exception:
            pass
    _save_after = _win.after(600, _apply_schedule)


def _apply_schedule():
    global _save_after
    _save_after = None
    if _sched_hour_var is None:
        return
    try:
        enabled = bool(state._sched_enabled_var.get())
        hour = _clamp_int(_sched_hour_var, 0, 23, 9)
        minute = _clamp_int(_sched_minute_var, 0, 59, 0)
        mode = _REPEAT_KEYS.get(_sched_repeat_var.get(), "once")
        weekdays = [bool(v.get()) for v in _sched_weekday_vars]
        poll = _clamp_int(_sched_poll_var, 5, 600, 30)

        state.SCHED_HOUR = hour
        state.SCHED_MINUTE = minute
        state.SCHED_REPEAT_MODE = mode
        state.SCHED_WEEKDAYS = weekdays
        state.SCHED_POLL_INTERVAL = poll

        # 与 settings/cards/sched.py apply() 一致的重启流程
        state.SCHED_ENABLED = enabled
        sched.stop_scheduler()
        state.SCHED_ENABLED = enabled
        sched.calc_next_run()
        if enabled:
            sched.start_scheduler(_CTX.get("main_run"), state.save_config)
        state.save_config()

        _update_next_label()
        refresh_tasks()
    except Exception as e:
        _log("定时任务设置保存失败: %s" % (e,), "error")


def _debounced_save_config(*_args):
    """启用开关防抖写盘：仅持久化，不重启调度器、不重读设置。"""
    global _save_cfg_after
    if _win is None:
        return
    if _save_cfg_after is not None:
        try:
            _win.after_cancel(_save_cfg_after)
        except Exception:
            pass
    _save_cfg_after = _win.after(800, _flush_save_config)


def _flush_save_config():
    global _save_cfg_after
    _save_cfg_after = None
    try:
        state.save_config()
    except Exception as e:
        _log("定时任务配置写盘失败: %s" % (e,), "warn")


def _toggle_enabled(*_args):
    """轻量启停（入口开关）：仅切换调度器运行态 + 更新预览 + 防抖写盘。

    不再调用 _apply_schedule（避免每次开关都重启调度器参数 + 全量写盘），
    消除右侧启用按钮切换时的卡顿。
    """
    try:
        enabled = bool(state._sched_enabled_var.get())
        state.SCHED_ENABLED = enabled
        if enabled:
            sched.calc_next_run()
            sched.start_scheduler(_CTX.get("main_run"), state.save_config)
        else:
            sched.stop_scheduler()
        _update_next_label()
        _update_stats()
        _debounced_save_config()
    except Exception as e:
        _log("启用开关切换失败: %s" % (e,), "error")


# ── 对外刷新 ──────────────────────────────────────────────
def update_sched_next_label(text):
    global _sched_next_label
    if _sched_next_label is not None:
        try:
            _sched_next_label.configure(text=text or "--:--")
        except Exception:
            pass


def refresh_tasks():
    global _manager
    if _manager is not None:
        try:
            _manager["refresh"]()
        except Exception:
            pass
    _update_stats()


def _update_stats():
    global _stats_label
    if _stats_label is None:
        return
    try:
        tasks = state.SCHED_TASKS
        enabled = sum(1 for t in tasks if t.get("enabled"))
        text = "任务 {} / 启用 {}".format(len(tasks), enabled)
        _stats_label.configure(text=text)
    except Exception:
        pass


def _update_next_label():
    update_sched_next_label(state.SCHED_NEXT_RUN or "--:--")


# ── 入口 Header ───────────────────────────────────────────
def _build_header(parent):
    """顶部入口栏：全局启用开关 + 下次执行预览。

    把「启用定时」从右栏设置面板提升为独立入口（重设计入口），
    开关切换走轻量 `_toggle_enabled`，消除原右栏开关触发的全量重启卡顿。
    """
    global _sched_next_label, _switch_widget
    if getattr(state, "_sched_enabled_var", None) is None:
        state._sched_enabled_var = tkinter.BooleanVar(value=state.SCHED_ENABLED)

    f_body = _FONTS.get("small")
    f_title = _FONTS.get("button")
    f_next = _FONTS.get("small_bold") or f_body

    bar = tkinter.Frame(parent, bg=C["bgc"],
                        highlightbackground=C["bd"], highlightthickness=1)
    bar.pack(fill="x", padx=_sp("gap"), pady=(_sp("gap"), _sp("gap_tight")))

    # 左: 全局总开关 (入口)
    tkinter.Label(bar, text="启用定时", font=f_title, fg=C["fgm"],
                  bg=C["bgc"]).pack(side="left", padx=(_sp("gap"), _sp("gap_tight")))
    _switch_widget = RoundedSwitch(bar, variable=state._sched_enabled_var, colors=C)
    _switch_widget.pack(side="left")
    tkinter.Label(bar, text="单个任务可在左侧列表右键启停",
                  font=_FONTS.get("log") or f_body, fg=C["fgm"], bg=C["bgc"]).pack(
        side="left", padx=(_sp("gap_tight"), 0))

    # 右: 下次执行预览
    _sched_next_label = tkinter.Label(bar, text=state.SCHED_NEXT_RUN or "--:--",
                                      font=f_next, fg=C["ac"], bg=C["bgc"], anchor="e")
    _sched_next_label.pack(side="right", padx=(0, _sp("gap")))
    tkinter.Label(bar, text="下次执行", font=f_body, fg=C["fgb"],
                  bg=C["bgc"]).pack(side="right", padx=(_sp("gap_tight"), _sp("gap_tight")))


# ── 执行记录折叠 ──────────────────────────────────────────
def _wire_log_toggle():
    """给「执行记录」工具栏追加折叠按钮，控制日志表体显隐。"""
    global _log_toggle_btn
    if _log is None:
        return
    toolbar = _log.get("toolbar")
    tree = _log.get("tree")
    if toolbar is None or tree is None:
        return
    f_body = _FONTS.get("small") or _FONTS.get("log")
    _log_toggle_btn = tkinter.Button(
        toolbar, text="收起 ▲", font=f_body, bg=C["bgc"], fg=C["fgb"],
        relief="flat", bd=1, cursor="hand2", padx=_sp("gap_tight"), pady=1,
        activebackground=C["acl"], activeforeground=C["fgb"],
        command=_toggle_log_panel)
    _log_toggle_btn.pack(side="right", padx=(0, _sp("gap")))


def _toggle_log_panel():
    """折叠/展开执行记录日志表体（始终保留工具栏与标题行）。"""
    global _log_collapsed, _log_toggle_btn
    if _log is None:
        return
    tree = _log.get("tree")
    if tree is None:
        return
    body = tree.master   # build_sched_log 中包裹 tree + 滚动条的 lf
    _log_collapsed = not _log_collapsed
    if _log_collapsed:
        body.grid_remove()
    else:
        body.grid()
        try:
            _log["refresh"]()
        except Exception:
            pass
    if _log_toggle_btn is not None:
        try:
            _log_toggle_btn.configure(text="展开 ▼" if _log_collapsed else "收起 ▲")
        except Exception:
            pass


# ── 设置面板 ──────────────────────────────────────────────
def _build_settings(parent):
    global _sched_hour_var, _sched_minute_var, _sched_repeat_var
    global _sched_weekday_vars, _sched_poll_var
    global _weekday_frame, _stats_label

    if getattr(state, "_sched_enabled_var", None) is None:
        state._sched_enabled_var = tkinter.BooleanVar(value=state.SCHED_ENABLED)

    _sched_hour_var = tkinter.StringVar(value="{:02d}".format(state.SCHED_HOUR))
    _sched_minute_var = tkinter.StringVar(value="{:02d}".format(state.SCHED_MINUTE))
    _sched_repeat_var = tkinter.StringVar(
        value=_REPEAT_DISPLAY.get(state.SCHED_REPEAT_MODE, "仅一次"))
    _sched_weekday_vars = [tkinter.BooleanVar(value=v) for v in state.SCHED_WEEKDAYS]
    _sched_poll_var = tkinter.StringVar(value=str(state.SCHED_POLL_INTERVAL))

    f_body = _FONTS.get("small")
    f_title = _FONTS.get("button")

    # 卡片
    card = tkinter.Frame(parent, bg=C["bgc"],
                         highlightbackground=C["bd"], highlightthickness=1)
    card.pack(fill="both", expand=True)

    header = tkinter.Frame(card, bg=C["bgc"])
    header.pack(fill="x", padx=_sp("gap"), pady=(_sp("gap"), _sp("gap_tight")))
    tkinter.Label(header, text="定时设置", font=f_title, fg=C["fgm"],
                  bg=C["bgc"]).pack(side="left")
    _stats_label = tkinter.Label(header, text="", font=f_body, fg=C["ac"],
                                 bg=C["bgc"])
    _stats_label.pack(side="right")

    body = tkinter.Frame(card, bg=C["bgc"])
    body.pack(fill="both", expand=True, padx=_sp("gap"), pady=(0, _sp("gap")))

    row = 0

    def _label(text, parent_w, col=0):
        tkinter.Label(parent_w, text=text, font=f_body, fg=C["fgb"],
                      bg=C["bgc"], anchor="w").grid(
            row=row, column=col, sticky="w", padx=(0, _sp("gap_tight")),
            pady=_sp("gap_tight"))

    # 时刻
    _label("时刻", body)
    time_row = tkinter.Frame(body, bg=C["bgc"])
    time_row.grid(row=row, column=1, sticky="w")
    hh = ttk.Combobox(time_row, textvariable=_sched_hour_var, width=4,
                      font=f_body, state="readonly",
                      values=["{:02d}".format(i) for i in range(24)])
    hh.pack(side="left")
    tkinter.Label(time_row, text=":", font=f_body, fg=C["fgm"],
                  bg=C["bgc"]).pack(side="left", padx=2)
    mm = ttk.Combobox(time_row, textvariable=_sched_minute_var, width=4,
                      font=f_body, state="readonly",
                      values=["{:02d}".format(i) for i in range(60)])
    mm.pack(side="left")
    hh.bind("<<ComboboxSelected>>", _schedule_save)
    mm.bind("<<ComboboxSelected>>", _schedule_save)
    row += 1

    # 重复
    _label("重复", body)
    rep = ttk.Combobox(body, textvariable=_sched_repeat_var, width=10,
                       font=f_body, state="readonly", values=_REPEAT_LABELS)
    rep.grid(row=row, column=1, sticky="w")
    rep.bind("<<ComboboxSelected>>", _on_repeat_change)
    row += 1

    # 星期 (每周时显示)
    _label("星期", body)
    _weekday_frame = tkinter.Frame(body, bg=C["bgc"])
    _weekday_frame.grid(row=row, column=1, sticky="w")
    for i, (label, var) in enumerate(zip(_WEEKDAYS, _sched_weekday_vars)):
        cb = ttk.Checkbutton(_weekday_frame, text=label, variable=var,
                             command=_schedule_save)
        cb.grid(row=i // 4, column=i % 4, sticky="w", padx=(0, _sp("gap_tight")))
    row += 1

    # 轮询间隔
    _label("轮询间隔", body)
    poll_row = tkinter.Frame(body, bg=C["bgc"])
    poll_row.grid(row=row, column=1, sticky="w")
    ttk.Spinbox(poll_row, from_=5, to=600, increment=5,
                textvariable=_sched_poll_var, width=8, font=f_body).pack(side="left")
    tkinter.Label(poll_row, text="秒", font=f_body, fg=C["fgm"],
                  bg=C["bgc"]).pack(side="left", padx=4)
    _sched_poll_var.trace_add("write", _schedule_save)
    row += 1

    # 说明
    tkinter.Label(body, text="修改后自动保存并重启调度", font=_FONTS.get("log") or f_body,
                  fg=C["fgm"], bg=C["bgc"], anchor="w").grid(
        row=row, column=0, columnspan=2, sticky="w", pady=(_sp("gap_tight"), 0))

    # 启用开关变化 → 轻量启停（不再触发全量 _apply_schedule）
    state._sched_enabled_var.trace_add("write", _toggle_enabled)

    # 仅同步星期行显隐，不触发保存（避免构建时无谓重启调度器 + 写盘）
    _sync_weekday_visibility()


def _sync_weekday_visibility():
    global _weekday_frame
    if _weekday_frame is None:
        return
    show = _sched_repeat_var.get() == "每周"
    if show:
        _weekday_frame.grid()
    else:
        _weekday_frame.grid_remove()


def _on_repeat_change(*_args):
    _sync_weekday_visibility()
    _schedule_save()


# ── 构建 / 换肤 ───────────────────────────────────────────
def build(parent, *, colors, fonts, deps=None, tab=None):
    global C, _FONTS, _CTX, _win, _tab, _left, _right, _built, _manager
    global _paned, _log_frame, _log
    C = colors
    _FONTS = fonts or {}
    _CTX = deps or {}
    _win = _CTX.get("root") or _CTX.get("app_root")
    _tab = tab or parent
    _built = True

    # 布局: 顶部 = 全局启用入口 + 下次执行预览；
    #        中部 = 左右可伸缩 PanedWindow (左=任务管理, 右=定时设置)；
    #        底部 = 可折叠「执行记录」面板。
    _tab.columnconfigure(0, weight=1)
    _tab.rowconfigure(0, weight=0)   # 顶部入口
    _tab.rowconfigure(1, weight=1)   # 主区 (Paned)
    _tab.rowconfigure(2, weight=0)   # 日志

    # 顶部: 全局启用开关 + 下次执行预览 (新入口)
    _header_frame = tkinter.Frame(_tab, bg=C["bg"])
    _header_frame.grid(row=0, column=0, sticky="ew")
    _build_header(_header_frame)

    _paned = tkinter.PanedWindow(
        _tab, orient="horizontal", bg=C["bd"],
        sashwidth=utils.sp("sb_w"), sashrelief="flat",
        opaqueresize=False)
    _paned.grid(row=1, column=0, sticky="nsew",
                padx=_sp("gap"), pady=(0, _sp("gap_tight")))

    _left = tkinter.Frame(_paned, bg=C["bgc"])
    _right = tkinter.Frame(_paned, bg=C["bgc"])

    # 左区: 计划任务管理 (懒导入避免 import 顺序问题)
    from dialogs import build_sched_manager
    _manager = build_sched_manager(_left, win_parent=_win, show_log_button=False)

    # 右区: 设置面板
    _build_settings(_right)

    # 右区给一个较宽初始尺寸；用户可拖动分隔条自由伸缩。
    _paned.add(_left, minsize=380, stretch="always")
    _paned.add(_right, minsize=300, stretch="always", width=380)
    # §5.4 分栏 sash 记忆: 拖拽结束写盘; _balance_paned 会优先恢复缓存
    try:
        _paned.bind("<ButtonRelease-1>", _on_paned_release)
    except Exception:
        pass
    # 初始按 55% / 45% 分配，右栏更宽更易读。
    _tab.after_idle(lambda: _balance_paned())

    # 下区: 执行记录（由原「查看日志」改造为内嵌面板，可折叠）
    from dialogs import build_sched_log
    _log_frame = tkinter.Frame(_tab, bg=C["bgc"],
                               highlightbackground=C["bd"], highlightthickness=1)
    _log_frame.grid(row=2, column=0, sticky="nsew",
                    padx=_sp("gap"), pady=(0, _sp("gap")))
    _log = build_sched_log(_log_frame)
    _wire_log_toggle()

    # 注入 GUI 引用 (下次执行标签 + 任务树，供调度器联动刷新)
    try:
        sched.set_gui_refs(_win, state._sched_enabled_var,
                           _sched_next_label, task_tree=_manager["tree"],
                           task_tree_refresh=refresh_tasks)
    except Exception as e:
        _log("注入调度器 GUI 引用失败: %s" % (e,), "warn")

    # 主题订阅 (仅首次)
    try:
        ui_theme.subscribe(refresh_theme)
    except Exception:
        pass

    _update_stats()
    return get_widgets()


def _on_paned_release(_event=None):
    """拖拽 sash 后读回位置并单键写盘 (§5.4)。"""
    try:
        if _paned is None:
            return
        state.set_layout_sash("sched", "main", state.sash_pos(_paned, 0), persist=True)
    except Exception:
        pass


def _balance_paned():
    """分配左右栏初始宽度（优先恢复缓存 sashpos, 否则按 52% 比例; 仅生效一次）。"""
    global _paned, _left, _right
    try:
        if _paned is None or _left is None or _right is None:
            return
        if not _paned.winfo_exists():
            return
        total = _paned.winfo_width()
        if total > 100:
            cached = None
            try:
                cached = state.get_layout_sash().get("sched", {}).get("main")
            except Exception:
                cached = None
            if cached is not None:
                left_w = max(380, min(int(cached), total - 300))
            else:
                left_w = max(380, int(total * 0.52))
            _paned.paneconfigure(_left, width=left_w)
            _paned.paneconfigure(_right, width=max(300, total - left_w - 8))
    except Exception:
        pass


def get_widgets():
    return {
        "tab": _tab, "left": _left, "right": _right,
        "manager": _manager, "next_label": _sched_next_label,
        "paned": _paned, "log": _log, "log_frame": _log_frame,
        "log_toggle_btn": _log_toggle_btn,
    }


def refresh_theme(dark, colors, prev=None):
    global C
    C = colors
    if not _built or _tab is None:
        return
    # 右区设置面板: 由主窗口 _walk 统一换肤; 此处只需刷新自绘开关与任务表 tag 色。
    if _switch_widget is not None:
        try:
            _switch_widget.refresh(colors)
        except Exception:
            pass
    if _manager is not None:
        try:
            tree = _manager["tree"]
            tree.tag_configure("en", foreground=C["fgb"])
            tree.tag_configure("dis", foreground=C["fgm"])
            tree.tag_configure("exp", foreground=C["wn"])
        except Exception:
            pass
    # 执行记录表 tag 色同步主题
    if _log is not None:
        try:
            ltree = _log["tree"]
            ltree.tag_configure("ok", foreground=C["sc"])
            ltree.tag_configure("fail", foreground=C["dg"])
            ltree.tag_configure("info", foreground=C["ac"])
        except Exception:
            pass
    # 任务统计标签前景色
    if _stats_label is not None:
        try:
            _stats_label.configure(fg=C["ac"], bg=C["bgc"])
        except Exception:
            pass
