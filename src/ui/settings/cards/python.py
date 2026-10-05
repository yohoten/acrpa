# -*- coding: utf-8 -*-
"""ui.settings.cards.python — § Python 扩展卡 (原 settings_window 2515-2618 行段)。

导出:
    build(parent, ctx) -> handles
    apply(ctx, handles) -> None       # 等价原 _apply_python_settings
"""
import os
import tkinter
from tkinter import ttk

import state
from utils import FONT_TINY

CARD_KEY = "python"
ROW = 9


def build(parent, ctx):
    C = ctx.C
    _btn = ctx.btn
    APP_ROOT = ctx.APP_ROOT
    _validate_number = ctx.validate_number
    _make_collapsible_card = ctx.make_collapsible_card
    _make_badge = ctx.make_badge
    _register_nav_card = ctx.register_nav_card
    _track_card_vars = ctx.track_card_vars

    card, py_content, py_title, _ = _make_collapsible_card(parent, "Python 扩展", "§")
    card.grid(row=ROW, column=0, sticky="ew", **ctx.PAD)
    _make_badge(CARD_KEY, py_title)
    _register_nav_card(CARD_KEY, card)

    # 权限下拉：UI 标签 ↔ state 存储值映射（单一来源）
    _PY_PERM_LABEL = {
        "sandbox": "沙箱（受限，推荐）",
        "trusted": "受信（可用完整内建与 import）",
        "full":    "完全权限（独立进程，默认禁用）",
    }
    _PY_LABEL_PERM = {v: k for k, v in _PY_PERM_LABEL.items()}

    py_perm_frame = tkinter.Frame(py_content, bg=C["bgc"])
    py_perm_frame.grid(row=0, column=0, sticky="ew", padx=8, pady=(2, 1))
    tkinter.Label(py_perm_frame, text="默认权限", font=ctx.FONT_SMALL,
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
    tkinter.Label(py_to_frame, text="超时(秒)", font=ctx.FONT_SMALL,
        fg=C["fgm"], bg=C["bgc"]).pack(side="left", padx=(0, 6))
    py_timeout_var = tkinter.StringVar(value=str(getattr(state, "PYTHON_TIMEOUT", 30)))
    _validate_number(tkinter.Entry(py_to_frame, textvariable=py_timeout_var, width=6,
        font=ctx.FONT_SMALL, relief="solid", bd=1, bg=C["ebg"], fg=C["fgb"]),
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

    _btn(py_audit_frame, "打开日志目录", _py_open_log_dir, "ac", "white",
         tip="在文件管理器中打开 Python 审计日志目录").pack(side="right")

    tkinter.Label(py_content,
        text="提示：Python 命令受 AST 预检与超时约束；full 权限默认关闭（详见 docs/python扩展使用说明.md）",
        font=FONT_TINY, fg=C["fgm"], bg=C["bgc"]).grid(
        row=5, column=0, sticky="w", padx=10, pady=(0, 6))

    # 绑定防抖保存 (Python 扩展卡)
    _track_card_vars("python", (py_perm_var, py_full_var, py_timeout_var))

    return {
        "card": card, "content": py_content, "title": py_title,
        "py_perm_var": py_perm_var, "py_full_var": py_full_var,
        "py_timeout_var": py_timeout_var, "_PY_LABEL_PERM": _PY_LABEL_PERM,
    }


def apply(ctx, handles):
    """保存「Python 扩展」卡配置 (等价原 _apply_python_settings)。"""
    state.PYTHON_DEFAULT_PERM = handles["_PY_LABEL_PERM"].get(
        handles["py_perm_var"].get(), "sandbox")
    state.PYTHON_FULL_ENABLED = bool(handles["py_full_var"].get())
    try:
        v = int(handles["py_timeout_var"].get())
        if v < 1:
            v = 1
        if v > 600:
            v = 600
        state.PYTHON_TIMEOUT = v
    except (ValueError, TypeError):
        state.PYTHON_TIMEOUT = 30
        handles["py_timeout_var"].set("30")
