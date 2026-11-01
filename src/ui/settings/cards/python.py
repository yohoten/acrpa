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

from ui.settings.cards._layout import section, row, help_line

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
    sp = ctx.sp

    card, py_content, py_title, _ = _make_collapsible_card(parent, "Python 扩展", "§")
    card.grid(row=ROW, column=0, sticky="ew", **ctx.PAD)
    _make_badge(CARD_KEY, py_title)
    _register_nav_card(CARD_KEY, card)

    _r = 0

    def _add(w, pady=None):
        nonlocal _r
        w.grid(row=_r, column=0, sticky="ew",
               pady=(sp("row_gap") if pady is None else pady))
        _r += 1
        return w

    def _sec(name, first=False):
        return _add(section(py_content, ctx, name),
                    pady=(sp("sp_sm") if first else sp("section_gap"), 0))

    # 权限下拉：UI 标签 ↔ state 存储值映射（单一来源）
    _PY_PERM_LABEL = {
        "sandbox": "沙箱（受限，推荐）",
        "trusted": "受信（可用完整内建与 import）",
        "full":    "完全权限（独立进程，默认禁用）",
    }
    _PY_LABEL_PERM = {v: k for k, v in _PY_PERM_LABEL.items()}

    _sec("权限与安全", first=True)
    _py_init_perm = str(getattr(state, "PYTHON_DEFAULT_PERM", "sandbox"))
    if _py_init_perm not in _PY_PERM_LABEL:
        _py_init_perm = "sandbox"
    py_perm_var = tkinter.StringVar(value=_PY_PERM_LABEL[_py_init_perm])
    _add(row(py_content, ctx, "默认权限", lambda p: ttk.Combobox(
        p, textvariable=py_perm_var,
        values=tuple(_PY_PERM_LABEL[k] for k in ("sandbox", "trusted", "full")),
        state="readonly", width=26)))

    py_full_var = tkinter.BooleanVar(value=bool(getattr(state, "PYTHON_FULL_ENABLED", False)))
    _add(row(py_content, ctx, "", lambda p: ttk.Checkbutton(
        p, text="允许 full 权限（独立进程执行任意代码）", variable=py_full_var)))

    py_full_warn = _add(help_line(py_content, ctx,
        "⚠ 完全权限可绕过沙箱执行任意代码与命令，仅在完全信任的脚本上启用", level="err"))

    def _py_toggle_full_warn(*_):
        try:
            if py_full_var.get():
                py_full_warn.grid()
            else:
                py_full_warn.grid_remove()
        except Exception:
            pass

    py_full_var.trace_add("write", _py_toggle_full_warn)
    _py_toggle_full_warn()

    py_timeout_var = tkinter.StringVar(value=str(getattr(state, "PYTHON_TIMEOUT", 30)))
    _add(row(py_content, ctx, "超时", lambda p: _validate_number(
        tkinter.Entry(p, textvariable=py_timeout_var, width=6,
            font=ctx.FONT_SMALL, relief="solid", bd=1, bg=C["ebg"], fg=C["fgb"]),
        py_timeout_var, "Python 超时", 30, int, 1, 600, card_key="python"),
        unit="秒 (1-600)"))

    # 审计日志路径 (只读) + 打开日志目录
    _sec("审计日志")
    try:
        import py_sandbox as _py_sandbox
        _py_audit_path = _py_sandbox.audit_path()
    except Exception:
        _py_audit_path = os.path.join(APP_ROOT, "logs", "acrpa_py_YYYYMMDD.log")

    def _py_open_log_dir():
        try:
            os.startfile(os.path.dirname(_py_audit_path))
        except Exception:
            pass

    def _mk_audit(p):
        f = tkinter.Frame(p, bg=C["bgc"])
        tkinter.Label(f, text=_py_audit_path, font=FONT_TINY,
            fg=C["fgm"], bg=C["bgc"]).pack(side="left", padx=(0, 8))
        _btn(f, "打开日志目录", _py_open_log_dir, "ac", "white",
             tip="在文件管理器中打开 Python 审计日志目录").pack(side="left")
        return f

    _add(row(py_content, ctx, "审计日志", _mk_audit))

    _add(help_line(py_content, ctx,
        "提示：Python 命令受 AST 预检与超时约束；full 权限默认关闭"
        "（详见 docs/python扩展使用说明.md）"))

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
