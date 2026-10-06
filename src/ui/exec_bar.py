# -*- coding: utf-8 -*-
"""ui.exec_bar — 执行控制栏 (构建 / 行为 / 换肤)。

路线图 §3.1「阶段二 · 第 3 项」第 2 步: 把 ACRPA.py 中的「执行控制栏」整体抽出
(第 1 步的底部日志面板见 src/ui/log_dock.py)。

  · 构建段 (原 ACRPA.py build_app 内 "执行控制工具栏" 段):
    exec_bar / card_run / run_bar_outer / run_canvas / run_scrollbar / run_bar /
    run_overflow_hint / script_name_var / _script_switcher / loop_count_var /
    _loop_combo / exec_maxmin_var / exec_maxmin_sp / exec_stoponerror_var /
    btn_run / btn_pause / btn_step / btn_stop / btn_step_mode /
    btn_debug_exec / btn_vars_exec / btn_debug_mode;
  · 行为函数 (原 ACRPA.py): select_script / _screenshot_tool /
    _run_update_overflow / _run_on_bar_configure / _run_on_wheel /
    _shared_run / _shared_stop / _tip_with_hotkey / _recent_scripts /
    _update_recent_scripts / _on_script_switched / _add_recent_script /
    _on_loop_sel / _exec_params_commit / _update_exec_buttons;
  · 换肤回调: 原 ACRPA._refresh_theme 内「定点回填语义按钮」段的执行四键部分,
    现由本模块 `ui.theme.subscribe(refresh_theme)` 自注册 —— _refresh_theme 不再
    出现执行四键的具名回填 (按钮角色登记与角色回填语义不变)。

执行核心
--------
`main_run / stop_execution / autorun / toggle_pause / _step_once / _fmt_dur`
**不迁**: 仍留在 ACRPA.py, 经 build(..., deps=...) 注入本模块 (按钮 command 与
_shared_run/_shared_stop 的 Tab 分派回调)。

设计约束
--------
· **零 GUI 副作用**: `import ui.exec_bar` 不建窗、不建 Tk 对象 (仅 import tkinter);
· **不 import ACRPA**: 全部依赖经 `build(..., deps=...)` 显式注入 —— 避免成环
  (src/ 内既有约定: 模块间一律依赖注入, 无反向 import 宿主);
· **字体角色不复制定义**: `FONT_*` 由 build() 从注入值绑定, 本模块不自建
  `_FONT_SPECS` (唯一真源仍在 utils)。

deps 键 (build 的形参)
----------------------
    ui_theme          ui.theme 模块 (roled / get_role / subscribe)
    create_card       utils.create_card
    sp / ctrl_h       utils.sp / utils.ctrl_h (尺寸令牌)
    attach_tooltip    utils.attach_tooltip
    darken            utils._darken (按钮 hover 底色)
    state             state 模块 (MAX_EXECUTION_MINUTES / STOP_ON_ERROR / 热键名)
    log1 / show_toast utils.log1 / utils.show_toast
    root              主窗口 (simpledialog / toast 父窗口)
    notebook          Tab 容器 (运行/停止按当前激活 Tab 分派)
    app_root          APP_ROOT (脚本对话框 initialdir / recent.json 落盘)
    screenshot_dir    SCREENSHOT_DIR (截图落盘)
    get_rz / get_status_dot / get_status_text / get_edit_file_label
                      延迟取回宿主控件 (构建顺序晚于执行栏)
    editor_load_xls   _editor_load_xls (选择/切换脚本后载入编辑器)
    main_run / stop_execution / toggle_pause / step_once
                      utils... → 宿主执行核心 (经命令注入)
    run_workflow / stop_workflow
                      _wf_run / _wf_stop (工作流 Tab 分派)
    toggle_debug_mode / show_variables_window / toggle_step_mode
                      _toggle_debug_mode / _show_variables_window / _toggle_step_mode
    load_pil / get_pa / get_image_tk
                      _load_pil / _get_pa / 取当前 ImageTk 类 (区域截图)

对外接口
--------
    build(parent, *, notebook, colors, fonts, deps) -> handle
    refresh_theme(dark, colors, prev)            # ThemeBus 订阅回调
    set_state(status)                            # 等价原 _update_exec_buttons
    get_widgets() -> dict / 若干 get_*
    select_script / shared_run / shared_stop / tip_with_hotkey /
    update_recent_scripts / on_script_switched / add_recent_script /
    on_loop_sel / exec_params_commit / update_overflow / on_bar_configure /
    on_wheel / screenshot_tool
"""
import json
import os
import tkinter
from tkinter import filedialog
from tkinter import ttk

import i18n  # 路线图 §5.6: UI 字符串目录 (默认 zh; 文本只作显示, 不作状态/主题判定)

__all__ = [
    "build", "refresh_theme", "set_state", "get_widgets",
    "get_exec_bar", "get_card_run", "get_run_bar", "get_run_bar_outer",
    "get_run_canvas", "get_run_scrollbar", "get_run_overflow_hint",
    "get_script_name_var", "get_script_switcher", "get_loop_count_var",
    "get_loop_combo", "get_exec_maxmin_var", "get_exec_maxmin_sp",
    "get_exec_stoponerror_var", "get_btn_validate", "get_btn_run",
    "get_btn_pause", "get_btn_step",
    "get_btn_stop", "get_btn_step_mode", "get_btn_debug_exec",
    "get_btn_vars_exec", "get_btn_debug_mode",
    "select_script", "shared_run", "shared_stop", "tip_with_hotkey",
    "update_recent_scripts", "on_script_switched", "add_recent_script",
    "on_loop_sel", "exec_params_commit", "update_overflow", "on_bar_configure",
    "on_wheel", "screenshot_tool",
]

# ── 字体角色: 由 build() 从注入值绑定 (本模块不复制 utils._FONT_SPECS) ──
FONT_TITLE = None
FONT_BODY = None
FONT_SMALL = None
FONT_BUTTON = None

# 卡片内边距 (与宿主 build_app 的 PAD 同规格: {"padx":6,"pady":3})
_PAD = {"padx": 6, "pady": 3}

# ── 模块级控件引用 (build() 后可用; 宿主经 get_* 取回, 亦可直读) ──
_exec_bar = None            # root row1 常驻容器
_card_run = None            # 运行控制条卡片
_run_bar_outer = None       # 卡片内滚动区外框
_run_canvas = None          # 横向滚动画布
_run_scrollbar = None       # 横向滚动条
_run_bar = None             # 内容条 (Canvas 内 window)
_run_overflow_hint = None   # 溢出提示「⇄」
_script_name_var = None     # StringVar 当前脚本名
_script_switcher = None     # 最近脚本下拉
_loop_count_var = None      # StringVar 运行次数
_loop_combo = None          # 次数下拉
_exec_maxmin_var = None     # StringVar 最长执行分钟
_exec_maxmin_sp = None      # Spinbox 最长执行分钟
_exec_stoponerror_var = None  # BooleanVar 出错即停
_btn_validate = None        # ✓ 校验 (语义角色 "ac"; 运行前静态校验)
_btn_run = None             # ▶ 运行 (语义角色 "sc")
_btn_pause = None           # ⏸ 暂停/继续 (语义角色 "wn")
_btn_step = None            # ⏭ 单步 (语义角色 "ac")
_btn_stop = None            # ■ 停止 (语义角色 "dg")
_btn_step_mode = None       # = _btn_step (单步模式高亮)
_btn_debug_exec = None      # ⚑ 调试
_btn_vars_exec = None       # ☰ 变量
_btn_debug_mode = None      # = _btn_debug_exec (调试模式高亮)
_recent_scripts = []        # 最近使用脚本路径列表 (最长 10)

# ── 注入上下文 (build 填充) ──
_CTX = {
    "ui_theme": None, "create_card": None, "sp": None, "ctrl_h": None,
    "attach_tooltip": None, "darken": None, "state": None, "log1": None,
    "show_toast": None, "root": None, "notebook": None, "app_root": "",
    "screenshot_dir": "", "get_rz": None, "get_status_dot": None,
    "get_status_text": None, "get_edit_file_label": None, "editor_load_xls": None,
    "main_run": None, "stop_execution": None, "toggle_pause": None,
    "step_once": None, "run_workflow": None, "stop_workflow": None,
    "toggle_debug_mode": None, "show_variables_window": None,
    "toggle_step_mode": None, "load_pil": None, "get_pa": None,
    "get_image_tk": None, "colors": None,
}


def _bind_fonts(fonts):
    """把注入的字体角色绑定到模块级 FONT_* (唯一真源仍是 utils)。"""
    global FONT_TITLE, FONT_BODY, FONT_SMALL, FONT_BUTTON
    fonts = fonts or {}
    FONT_TITLE = fonts.get("title")
    FONT_BODY = fonts.get("body")
    FONT_SMALL = fonts.get("small")
    FONT_BUTTON = fonts.get("button")


def _pal():
    """当前主题色板 (build / refresh_theme 时更新)。"""
    c = _CTX.get("colors")
    return c if isinstance(c, dict) else {}


def _call(name, *args, **kw):
    """调用注入的回调 (未注入则安全 no-op)。"""
    fn = _CTX.get(name)
    if fn is None:
        return None
    try:
        return fn(*args, **kw)
    except Exception:
        return None


def _log1(msg, tag=None):
    """经注入的 utils.log1 记日志 (未注入则静默)。"""
    fn = _CTX.get("log1")
    if fn is None:
        return
    try:
        fn(msg, tag)
    except Exception:
        pass


def _toast(msg, kind="info", ms=2000):
    """经注入的 utils.show_toast 弹提示 (未注入则静默)。"""
    fn = _CTX.get("show_toast")
    if fn is None:
        return
    try:
        fn(_CTX.get("root"), msg, kind, ms)
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════
# 构建 (原 ACRPA.build_app 内「执行控制工具栏」段)
# ══════════════════════════════════════════════════════════════════════

def build(parent, *, notebook, colors, fonts, deps=None):
    """构建执行控制栏 (两 Tab 共用, root row1 常驻), 返回控件句柄 dict (= get_widgets())。

    parent  : 主窗口 (root)。
    notebook: Tab 容器 (运行/停止按当前激活 Tab 分派)。
    colors  : 当前主题色板 (utils.C / ui.theme.colors())。
    fonts   : {"title","body","small","button"} → 绑定模块级 FONT_*。
    deps    : 依赖注入字典 (键见模块 docstring)。
    """
    global _exec_bar, _card_run, _run_bar_outer, _run_canvas, _run_scrollbar, \
        _run_bar, _run_overflow_hint, _script_name_var, _script_switcher, \
        _loop_count_var, _loop_combo, _exec_maxmin_var, _exec_maxmin_sp, \
        _exec_stoponerror_var, _btn_validate, _btn_run, _btn_pause, _btn_step, \
        _btn_stop, _btn_step_mode, _btn_debug_exec, _btn_vars_exec, _btn_debug_mode

    _CTX.update(deps or {})
    _CTX["notebook"] = notebook
    _CTX["colors"] = colors
    _bind_fonts(fonts)

    ui_theme = _CTX.get("ui_theme")
    create_card = _CTX.get("create_card")
    sp = _CTX.get("sp")
    ctrl_h = _CTX.get("ctrl_h")
    darken = _CTX.get("darken")
    state = _CTX.get("state")
    attach_tooltip = _CTX.get("attach_tooltip")
    C = colors

    # ======================================================================
    # ② 执行控制工具栏 (常驻 · 两 Tab 共用) —— 由原「执行控制」Tab 降级而来
    #   运行控制条 → root row1 常驻;  运行日志 → 底部常驻面板 (见 ui/log_dock.py)
    # 设计: docs/底部常驻日志面板重构设计方案.md
    # ======================================================================
    exec_bar = tkinter.Frame(parent, bg=C["bg"])
    exec_bar.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 2))
    exec_bar.columnconfigure(0, weight=1)
    _exec_bar = exec_bar

    # ──────────────────────────────────────────────────────────────────────
    # ① 运行控制条 (Run Control Bar): 脚本 / 次数 / 四键同组 / 调试入口
    # ──────────────────────────────────────────────────────────────────────
    card_run = create_card(exec_bar)
    card_run.grid(row=0, column=0, sticky="ew", **_PAD)
    card_run.columnconfigure(0, weight=1)
    _card_run = card_run

    run_bar_outer = tkinter.Frame(card_run, bg=C["bgc"])
    run_bar_outer.grid(row=0, column=0, sticky="ew", padx=6, pady=4)
    run_bar_outer.columnconfigure(0, weight=1)
    _run_bar_outer = run_bar_outer

    run_canvas = tkinter.Canvas(run_bar_outer, bg=C["bgc"],
        height=ctrl_h("ctrl_h_lg"), highlightthickness=0, bd=0)
    run_scrollbar = tkinter.Scrollbar(run_bar_outer, orient="horizontal",
        command=run_canvas.xview, width=sp(6))
    run_bar = tkinter.Frame(run_canvas, bg=C["bgc"])
    _run_canvas = run_canvas
    _run_scrollbar = run_scrollbar
    _run_bar = run_bar

    # 溢出提示「⇄」: 内容宽度超过可视宽度时显示 (item 11)
    run_overflow_hint = tkinter.Label(run_bar_outer, text="⇄", font=FONT_SMALL,
        fg=C["fgm"], bg=C["bgc"])
    _run_overflow_hint = run_overflow_hint

    run_bar.bind("<Configure>", on_bar_configure)
    run_canvas.bind("<Configure>", lambda e: update_overflow())
    run_canvas.create_window((0, 0), window=run_bar, anchor="w")
    run_canvas.configure(xscrollcommand=run_scrollbar.set)
    run_canvas.grid(row=0, column=0, sticky="ew")
    run_scrollbar.grid(row=1, column=0, sticky="ew")

    # 脚本选择: 标签 + 最近脚本下拉 + 选择按钮
    tkinter.Label(run_bar, text=i18n.t("exec.label_script"), font=FONT_SMALL, fg=C["fgm"],
        bg=C["bgc"]).pack(side="left", padx=(2, 2))

    script_name_var = tkinter.StringVar(value=i18n.t("exec.no_file"))
    _script_name_var = script_name_var
    # 快速切换：下拉框 + 浏览按钮
    _script_switcher = ttk.Combobox(run_bar, textvariable=script_name_var,
        font=FONT_BODY, state="readonly", width=26)
    _script_switcher.pack(side="left", padx=(0, 4))

    _script_switcher.bind("<<ComboboxSelected>>", on_script_switched)
    update_recent_scripts()

    ui_theme.roled(tkinter.Button(run_bar, text=i18n.t("exec.label_choose"), font=FONT_BUTTON, bg=C["ac"], fg="white",
        activebackground=C["ach"], activeforeground="white", relief="flat", bd=1,
        cursor="hand2", padx=8, pady=2, command=select_script), "ac").pack(side="left", padx=(0, 4))

    # 次数: 可编辑 + 预设 + 自定义
    tkinter.Label(run_bar, text=i18n.t("exec.label_loops"), font=FONT_SMALL, fg=C["fgm"],
        bg=C["bgc"]).pack(side="left", padx=(6, 2))
    loop_count_var = tkinter.StringVar(value=i18n.t("exec.loop_infinite"))
    _loop_count_var = loop_count_var
    _loop_combo = ttk.Combobox(run_bar, textvariable=loop_count_var,
        values=(i18n.t("exec.loop_infinite"), '1', '5', '10', '50',
                i18n.t("exec.loop_custom")), width=8)
    _loop_combo.pack(side="left", padx=(0, 4))
    _loop_combo.bind("<<ComboboxSelected>>", on_loop_sel)

    # 执行参数就地化 (与工作流/设置共用同一口径: MAX_EXECUTION_MINUTES / STOP_ON_ERROR)
    tkinter.Label(run_bar, text=i18n.t("exec.label_maxmin"), font=FONT_SMALL, fg=C["fgm"],
        bg=C["bgc"]).pack(side="left", padx=(sp(4), sp(2)))
    exec_maxmin_var = tkinter.StringVar(value=str(state.MAX_EXECUTION_MINUTES))
    _exec_maxmin_var = exec_maxmin_var
    exec_maxmin_sp = tkinter.Spinbox(run_bar, textvariable=exec_maxmin_var, from_=0, to=1440,
        width=4, font=FONT_SMALL, bg=C["ebg"], fg=C["fgb"], relief="solid", bd=1)
    exec_maxmin_sp.pack(side="left", padx=(0, sp(2)))
    _exec_maxmin_sp = exec_maxmin_sp
    tkinter.Label(run_bar, text=i18n.t("exec.label_minute"), font=FONT_SMALL, fg=C["fgm"],
        bg=C["bgc"]).pack(side="left", padx=(0, sp(4)))
    exec_stoponerror_var = tkinter.BooleanVar(value=bool(state.STOP_ON_ERROR))
    _exec_stoponerror_var = exec_stoponerror_var
    tkinter.Checkbutton(run_bar, text=i18n.t("exec.stop_on_error"), variable=exec_stoponerror_var, font=FONT_SMALL,
        fg=C["fgm"], bg=C["bgc"], activebackground=C["bgc"], activeforeground=C["fgb"],
        selectcolor=C["ebg"], bd=0, highlightthickness=0,
        command=lambda: exec_params_commit()).pack(side="left", padx=(0, sp(4)))
    exec_maxmin_sp.bind("<FocusOut>", lambda e: exec_params_commit())
    exec_maxmin_sp.bind("<Return>", lambda e: exec_params_commit())

    tkinter.Frame(run_bar, bg=C["bd"], width=sp(2), height=sp(22)).pack(
        side="left", fill="y", padx=sp(6), pady=4)

    # ✓ 校验 (运行前 dry-run; 默认只读展示问题, 不执行脚本)
    # 置于四键同组之前: 与运行相关但不参与「运行/暂停/单步/停止」状态机
    btn_validate = ui_theme.roled(tkinter.Button(run_bar, text=i18n.t("exec.btn_validate"), font=FONT_BUTTON, bg=C["ac"], fg="black",
        activebackground=C["ach"], activeforeground="black", disabledforeground="black",
        relief="flat", bd=1,
        cursor="hand2", padx=sp(8), pady=sp(2), command=_call_proxy("validate_script_cb")), "ac")
    btn_validate.pack(side="left", padx=2)
    _btn_validate = btn_validate

    # 四键同组: ▶ 运行 / ⏸ 暂停 / ⏭ 单步 / ■ 停止
    # (尺寸走 utils.sp 令牌; 四键文字统一黑色, 与「运行」对齐)
    # 语义角色显式登记 (换肤按角色回填, 不再按旧颜色值反推语义组)
    btn_run = ui_theme.roled(tkinter.Button(run_bar, text=i18n.t("exec.btn_run"), font=FONT_BUTTON, bg=C["sc"], fg="black",
        activebackground=darken(C["sc"]), activeforeground="black", disabledforeground="black",
        relief="flat", bd=1,
        cursor="hand2", padx=sp(8), pady=sp(2), command=shared_run), "sc")
    btn_run.pack(side="left", padx=2)
    _btn_run = btn_run
    btn_pause = ui_theme.roled(tkinter.Button(run_bar, text="⏸ 暂停", font=FONT_BUTTON, bg=C["wn"], fg="black",
        activebackground=darken(C["wn"]), activeforeground="black", disabledforeground="black",
        relief="flat", bd=1,
        cursor="hand2", padx=sp(8), pady=sp(2), command=_CTX.get("toggle_pause")), "wn")
    btn_pause.pack(side="left", padx=2)
    _btn_pause = btn_pause

    btn_step = ui_theme.roled(tkinter.Button(run_bar, text=i18n.t("exec.btn_step"), font=FONT_BUTTON, bg=C["ac"], fg="black",
        activebackground=C["ach"], activeforeground="black", disabledforeground="black",
        relief="flat", bd=1,
        cursor="hand2", padx=sp(8), pady=sp(2), command=_CTX.get("step_once")), "ac")
    btn_step.pack(side="left", padx=2)
    _btn_step = btn_step
    btn_stop = ui_theme.roled(tkinter.Button(run_bar, text=i18n.t("exec.btn_stop"), font=FONT_BUTTON, bg=C["dg"], fg="black",
        activebackground=darken(C["dg"]), activeforeground="black", disabledforeground="black",
        relief="flat", bd=1,
        cursor="hand2", padx=sp(8), pady=sp(2), command=shared_stop), "dg")
    btn_stop.pack(side="left", padx=2)
    _btn_stop = btn_stop

    # 四键 tooltip (含用户配置快捷键); 单步右键 = 单步模式开关 (调试态内聚)
    try:
        attach_tooltip(btn_validate, i18n.t("exec.tip_validate"))
        attach_tooltip(btn_run, tip_with_hotkey("运行", "HOTKEY_RUN"))
        attach_tooltip(btn_pause, tip_with_hotkey("暂停/继续", "HOTKEY_PAUSE"))
        attach_tooltip(btn_stop, tip_with_hotkey("停止", "HOTKEY_STOP"))
        attach_tooltip(btn_step, "左键单步执行一步；右键切换「单步模式」(每行暂停)")
    except Exception:
        pass
    btn_step_mode = btn_step   # 供 _toggle_step_mode 高亮 (调试态归执行控制)
    _btn_step_mode = btn_step_mode
    btn_step.bind("<Button-3>", lambda e: _call("toggle_step_mode"))

    tkinter.Frame(run_bar, bg=C["bd"], width=sp(2), height=sp(22)).pack(
        side="left", fill="y", padx=sp(6), pady=4)

    # 调试 / 变量入口 (调试态统一归此; 脚本编辑工具栏已移除该组, 避免两处状态不同步)
    btn_debug_exec = tkinter.Button(run_bar, text=i18n.t("exec.btn_debug"), font=FONT_BUTTON, bg=C["bgc"], fg=C["fgb"],
        activebackground=C["acl"], activeforeground=C["fgb"], relief="flat", bd=1,
        cursor="hand2", padx=sp(8), pady=sp(2), command=_call_proxy("toggle_debug_mode"))
    btn_debug_exec.pack(side="left", padx=2)
    _btn_debug_exec = btn_debug_exec
    btn_vars_exec = tkinter.Button(run_bar, text=i18n.t("exec.btn_vars"), font=FONT_BUTTON, bg=C["bgc"], fg=C["fgb"],
        activebackground=C["acl"], activeforeground=C["fgb"], relief="flat", bd=1,
        cursor="hand2", padx=sp(8), pady=sp(2), command=_call_proxy("show_variables_window"))
    btn_vars_exec.pack(side="left", padx=2)
    _btn_vars_exec = btn_vars_exec
    btn_debug_mode = btn_debug_exec   # 供 _toggle_debug_mode 高亮
    _btn_debug_mode = btn_debug_mode
    try:
        attach_tooltip(btn_debug_exec, "调试模式 (断点/变量监视)")
        attach_tooltip(btn_vars_exec, "变量监视器")
    except Exception:
        pass

    # 换肤: 订阅 ThemeBus (幂等, 重复构建只登记一次) —— 取代原 _refresh_theme 的定点回填
    if ui_theme is not None:
        ui_theme.subscribe(refresh_theme)

    return get_widgets()


def _call_proxy(name):
    """生成惰性命令 (延迟解析注入的回调, 避免构建期 command 绑定到 None)。"""
    return lambda: _call(name)


# ══════════════════════════════════════════════════════════════════════
# 换肤回调 (原 ACRPA._refresh_theme 内「定点回填语义按钮」段的执行四键部分)
# ══════════════════════════════════════════════════════════════════════

def refresh_theme(dark=None, colors=None, prev=None):
    """ThemeBus 订阅回调 (签名与 publish 派发契约一致): 执行四键按语义角色回填。"""
    C = colors if isinstance(colors, dict) else _CTX.get("colors")
    if not isinstance(C, dict):
        return
    _CTX["colors"] = C
    ui_theme = _CTX.get("ui_theme")
    for w in (_btn_validate, _btn_run, _btn_pause, _btn_step, _btn_stop):
        if w is None:
            continue
        try:
            role = ui_theme.get_role(w) if ui_theme is not None else None
            if role:
                w.configure(bg=C.get(role, C["bgc"]))
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════════════
# 行为函数 (原 ACRPA.py; 执行核心经 _CTX 注入)
# ══════════════════════════════════════════════════════════════════════

def tip_with_hotkey(label, attr):
    """生成「标签 (快捷键: X)」提示; 未配置快捷键时只显示标签。"""
    state = _CTX.get("state")
    hk = getattr(state, attr, "") or "" if state is not None else ""
    return "{} (快捷键: {})".format(label, hk) if hk else label


def update_overflow(event=None):
    """内容宽度超过可视宽度时显示溢出提示「⇄」 (item 11)。"""
    sp = _CTX.get("sp")
    try:
        bb = _run_canvas.bbox("all")
        need = bb[2] if bb else 0
        if need > _run_canvas.winfo_width() + sp(2):
            _run_overflow_hint.grid(row=0, column=1, sticky="e", padx=(sp(2), 0))
        else:
            _run_overflow_hint.grid_remove()
    except Exception:
        pass


def on_bar_configure(event=None):
    """内容条尺寸变化 → 更新滚动区域与溢出提示。"""
    try:
        _run_canvas.configure(scrollregion=_run_canvas.bbox("all"))
    except Exception:
        pass
    update_overflow()


def on_wheel(event):
    """内容条鼠标滚轮 → 横向滚动。"""
    if event.delta > 0:
        _run_canvas.xview_scroll(-1, "units")
    else:
        _run_canvas.xview_scroll(1, "units")


def shared_run(event=None):
    """▶ 运行 上下文分派: 按当前激活 Tab 决定运行脚本还是工作流。

    Tab 顺序: 0=脚本编辑, 1=工作流 (执行控制已升级为常驻工具栏, 不占 Tab)
    """
    notebook = _CTX.get("notebook")
    try:
        idx = notebook.index(notebook.select())
    except Exception:
        idx = 0
    if idx == 1:
        try:
            _call("run_workflow")
        except Exception as e:
            _log1("工作流启动失败: {}".format(e), "error")
    else:
        _call("main_run")


def shared_stop(event=None):
    """■ 停止 上下文分派: 按当前激活 Tab 决定停止脚本还是工作流。"""
    notebook = _CTX.get("notebook")
    try:
        idx = notebook.index(notebook.select())
    except Exception:
        idx = 0
    if idx == 1:
        try:
            _call("stop_workflow")
        except Exception as e:
            _log1("工作流停止失败: {}".format(e), "error")
    else:
        _call("stop_execution")


def update_recent_scripts():
    """更新快速切换下拉列表。"""
    global _recent_scripts
    try:
        recent_file = os.path.join(_CTX.get("app_root", ""), "recent.json")
        if os.path.exists(recent_file):
            with open(recent_file, "r", encoding="utf-8") as f:
                _recent_scripts = json.load(f)[:10]
    except Exception:
        _recent_scripts = []
    scripts = [os.path.basename(s) for s in _recent_scripts] if _recent_scripts else [i18n.t("exec.no_file")]
    try:
        _script_switcher["values"] = scripts
    except Exception:
        pass


def on_script_switched(event=None):
    """快速切换到最近使用的脚本。"""
    state = _CTX.get("state")
    idx = _script_switcher.current()
    if idx >= 0 and idx < len(_recent_scripts):
        fp = _recent_scripts[idx]
        if os.path.exists(fp):
            state.filename = fp
            state.has_script = True
            state.script_dir = os.path.dirname(fp)
            _script_name_var.set(os.path.basename(fp))
            lab = _call("get_edit_file_label")
            if lab is not None:
                try:
                    lab.config(text=os.path.basename(fp))
                except Exception:
                    pass
            _call("editor_load_xls", fp)
            _toast("已切换: {}".format(os.path.basename(fp)), "info")


def add_recent_script(filepath):
    """添加脚本到最近使用列表。"""
    global _recent_scripts
    if filepath in _recent_scripts:
        _recent_scripts.remove(filepath)
    _recent_scripts.insert(0, filepath)
    _recent_scripts = _recent_scripts[:10]
    try:
        with open(os.path.join(_CTX.get("app_root", ""), "recent.json"), "w", encoding="utf-8") as f:
            json.dump(_recent_scripts, f)
    except Exception:
        pass
    update_recent_scripts()


def select_script():
    """打开文件对话框选择一个脚本文件（.xls/.xlsx/.acrpas）。"""
    state = _CTX.get("state")
    state.filename = filedialog.askopenfilename(
        title="选择一个脚本文件", filetypes=[('xls', '*.xls'), ('ACRPA 脚本', '*.acrpas')],
        initialdir=_CTX.get("app_root", ""))
    if state.filename and os.path.getsize(state.filename):
        state.has_script = True
        _script_name_var.set(os.path.basename(state.filename))
        lab = _call("get_edit_file_label")
        if lab is not None:
            try:
                lab.config(text=os.path.basename(state.filename))
            except Exception:
                pass
        state.script_dir = os.path.dirname(state.filename)
        rz = _call("get_rz")
        if rz is not None:
            try:
                rz.delete(0.0, "end")
            except Exception:
                pass
        _log1("脚本资源存放目录：{}".format(state.script_dir))
        C = _pal()
        sdot = _call("get_status_dot")
        if sdot is not None:
            try:
                sdot.config(text=i18n.t("status.loaded_dot"), fg=C.get("sc"))
            except Exception:
                pass
        stxt = _call("get_status_text")
        if stxt is not None:
            try:
                stxt.config(text=i18n.t("exec.loaded_text"), fg=C.get("fgb"))
            except Exception:
                pass
        _call("editor_load_xls", state.filename)
        add_recent_script(state.filename)
    else:
        state.has_script = False


def on_loop_sel(event=None):
    """次数下拉: 选中「自定义…」时弹出输入框。"""
    if _loop_count_var.get() == i18n.t("exec.loop_custom"):
        try:
            from tkinter import simpledialog
            n = simpledialog.askinteger(i18n.t("exec.dlg_loops_title"), i18n.t("exec.dlg_loops_prompt"),
                parent=_CTX.get("root"), minvalue=1, maxvalue=1000000)
            _loop_count_var.set(i18n.t("exec.loop_infinite") if n is None else str(n))
        except Exception:
            _loop_count_var.set(i18n.t("exec.loop_infinite"))


def exec_params_commit():
    """就地执行参数写回 state (与设置/工作流同源, 无口径差异)。"""
    state = _CTX.get("state")
    try:
        _exec_maxmin_var.set(str(max(0, int(float(_exec_maxmin_var.get())))))
    except Exception:
        _exec_maxmin_var.set("0")
    try:
        state.MAX_EXECUTION_MINUTES = int(_exec_maxmin_var.get())
        state.STOP_ON_ERROR = bool(_exec_stoponerror_var.get())
        state.save_config()
    except Exception:
        pass


def set_state(code):
    """执行控制四键的文案/配色切换 (0=就绪 1=已暂停 2=运行中 3=已停止)。

    ⚠ 不再禁用任何按钮 —— 四键始终可点, 各命令 (main_run/toggle_pause/
      _step_once/stop_execution) 内部自行守卫非法时机, 避免非运行态下
      暂停/单步/停止点不动。此处仅切换「暂停 ⇄ 继续」文案与底色。
    """
    C = _pal()
    # 防御性恢复可点击 (万一被外部置为 disabled)
    for _b in (_btn_run, _btn_pause, _btn_step, _btn_stop):
        try:
            _b.config(state="normal")
        except Exception:
            pass
    try:
        if code == 1:      # 已暂停
            _btn_pause.config(text=i18n.t("exec.btn_resume"), bg=C.get("ac"))
        else:              # 就绪 / 运行中 / 已停止
            _btn_pause.config(text="⏸ 暂停", bg=C.get("wn"))
    except Exception:
        pass


def screenshot_tool():
    """区域截图: 全屏半透明遮罩上拖拽选区后保存到截图目录。"""
    _call("load_pil")
    root = _CTX.get("root")
    try:
        root.withdraw()
    except Exception:
        pass
    import time
    time.sleep(0.3)
    ss = _call("get_pa").screenshot()
    overlay = tkinter.Toplevel()
    overlay.attributes("-fullscreen", True)
    overlay.attributes("-alpha", 0.4)
    overlay.attributes("-topmost", True)
    overlay.configure(bg="black")
    overlay.config(cursor="cross")

    canvas = tkinter.Canvas(overlay, bg="black", highlightthickness=0)
    canvas.pack(fill="both", expand=True)
    ss_img = _call("get_image_tk").PhotoImage(ss)
    canvas.create_image(0, 0, image=ss_img, anchor="nw")
    canvas.ss_img = ss_img
    rect = None
    start_x = [0]
    start_y = [0]

    def on_press(event):
        start_x[0] = event.x
        start_y[0] = event.y

    def on_drag(event):
        nonlocal rect
        if rect:
            canvas.delete(rect)
        rect = canvas.create_rectangle(start_x[0], start_y[0], event.x, event.y,
            outline="white", width=2)

    def on_release(event):
        nonlocal rect
        overlay.destroy()
        try:
            root.deiconify()
        except Exception:
            pass
        x1 = min(start_x[0], event.x)
        y1 = min(start_y[0], event.y)
        x2 = max(start_x[0], event.x)
        y2 = max(start_y[0], event.y)
        width = x2 - x1
        height = y2 - y1
        # 确保截图区域有效（至少 1x1 像素）
        if width <= 0 or height <= 0:
            _log1("截图区域无效，请重新选择")
            return
        ss = _call("get_pa").screenshot(region=(x1, y1, width, height))
        import datetime
        ts = datetime.datetime.now().strftime("%m%d_%H%M%S")
        fp = os.path.join(_CTX.get("screenshot_dir", ""), "shot_{}.png".format(ts))
        ss.save(fp)
        _log1("截图已保存: {}".format(fp))
        _toast("截图已保存", "success")

    overlay.bind("<Button-1>", on_press)
    overlay.bind("<B1-Motion>", on_drag)
    overlay.bind("<ButtonRelease-1>", on_release)
    overlay.bind("<Escape>", lambda e: (overlay.destroy(), _safe_deiconify(root)))
    overlay.mainloop()


def _safe_deiconify(root):
    try:
        root.deiconify()
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════
# 控件取回 (宿主转发别名 / tools 直读)
# ══════════════════════════════════════════════════════════════════════

def get_exec_bar():
    return _exec_bar


def get_card_run():
    return _card_run


def get_run_bar():
    return _run_bar


def get_run_bar_outer():
    return _run_bar_outer


def get_run_canvas():
    return _run_canvas


def get_run_scrollbar():
    return _run_scrollbar


def get_run_overflow_hint():
    return _run_overflow_hint


def get_script_name_var():
    return _script_name_var


def get_script_switcher():
    return _script_switcher


def get_loop_count_var():
    return _loop_count_var


def get_loop_combo():
    return _loop_combo


def get_exec_maxmin_var():
    return _exec_maxmin_var


def get_exec_maxmin_sp():
    return _exec_maxmin_sp


def get_exec_stoponerror_var():
    return _exec_stoponerror_var


def get_btn_run():
    return _btn_run


def get_btn_pause():
    return _btn_pause


def get_btn_step():
    return _btn_step


def get_btn_stop():
    return _btn_stop


def get_btn_validate():
    return _btn_validate


def get_btn_step_mode():
    return _btn_step_mode


def get_btn_debug_exec():
    return _btn_debug_exec


def get_btn_vars_exec():
    return _btn_vars_exec


def get_btn_debug_mode():
    return _btn_debug_mode


def get_widgets():
    """取回全部控件句柄 (与构建返回值一致)。"""
    return {
        "exec_bar": _exec_bar, "card_run": _card_run,
        "run_bar_outer": _run_bar_outer, "run_canvas": _run_canvas,
        "run_scrollbar": _run_scrollbar, "run_bar": _run_bar,
        "run_overflow_hint": _run_overflow_hint,
        "script_name_var": _script_name_var, "script_switcher": _script_switcher,
        "loop_count_var": _loop_count_var, "loop_combo": _loop_combo,
        "exec_maxmin_var": _exec_maxmin_var, "exec_maxmin_sp": _exec_maxmin_sp,
        "exec_stoponerror_var": _exec_stoponerror_var,
        "btn_validate": _btn_validate,
        "btn_run": _btn_run, "btn_pause": _btn_pause, "btn_step": _btn_step,
        "btn_stop": _btn_stop, "btn_step_mode": _btn_step_mode,
        "btn_debug_exec": _btn_debug_exec, "btn_vars_exec": _btn_vars_exec,
        "btn_debug_mode": _btn_debug_mode,
    }
