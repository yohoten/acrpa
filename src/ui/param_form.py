# -*- coding: utf-8 -*-
"""ui.param_form — 命令 schema 驱动的参数表单 (纯 UI 组件)。

路线图「阶段二 · 第 8 项」: 让编辑器 / 工作流把命令参数从「固定 9 个无标签 Entry」
升级为 **由 `commands.schema(cmd_type)` 驱动**的具名控件 —— 类型 (string/number)、
枚举 (choices)、默认值/可选 (default/optional)、变长 (variadic) 直接体现在 UI 上。

对外接口
--------
    build_arg_form(parent, cmd_type, initial_args=None, *, colors, fonts, theme=None)
        -> (frame, get_args)
        get_args() -> list[str]      # 长度恒为 9, 空位为 ""

    args_to_schema_values(cmd_type, args)       -> list       # 9 位 args → 具名控件初值
    schema_values_to_args(cmd_type, values)     -> list[str]  # 反向, 补齐/裁剪到 9 位
    field_error(kind, choices, value)           -> str | None # 单字段校验 (空值放行)

渲染规则
--------
    kind == "string"   -> Entry
    kind == "number"   -> Entry + FocusOut 数值校验
                          (非法时标红并回退默认, **不阻断保存**)
    choices 非空        -> ttk.Combobox(state="readonly", values=choices)
    optional / default -> 标签后缀「（可选/默认X）」, 并取默认初值
    variadic           -> 「+ 追加」按钮, 收集为从该位起的连续多项

设计约束
--------
    · 只依赖 tkinter / ttk / commands; 色板 (colors)、字体 (fonts)、主题模块 (theme)
      一律由参数注入 —— **不 import ACRPA** (避免成环);
    · **零 GUI 副作用**: `import ui.param_form` 不建窗、不读屏幕;
    · 空值一律写 "" (与 `commands._clean` 同口径, **不写 "None"**);
    · 无 schema / 未知命令 / 无参数 → 退化为 9 个通用 Entry (等价既有 9 格表单)。
"""
import tkinter
from tkinter import ttk

import commands

__all__ = [
    "build_arg_form", "args_to_schema_values", "schema_values_to_args", "field_error",
]

# 参数位契约: 脚本表 9 列 / 工作流 step["params"] / ScriptData.args 恒 9 位。
_SLOTS = 9


# ══════════════════════════════════════════════════════════════════════
# 纯逻辑: 值归一 / 具名控件初值 ↔ 9 位 args (无 GUI, 可离线自测)
# ══════════════════════════════════════════════════════════════════════

def _clean(v):
    """单值归一 (与 `commands._clean` 同口径): None/空白/"None"/"null"/"-" → ""。"""
    if v is None:
        return ""
    s = str(v).strip()
    if s.lower() in ("none", "null", "-", ""):
        return ""
    return s


def _norm9(seq):
    """任意序列 → 长度恰为 9 的字符串列表 (越短补 "", 越长截断, None → "")。"""
    try:
        n = len(seq) if seq is not None else 0
    except Exception:
        n = 0
    return [(_clean(seq[i]) if i < n else "") for i in range(_SLOTS)]


def _variadic_index(ps):
    """首个变长参数在 schema 中的下标; 无则 -1。"""
    for i, p in enumerate(ps):
        if p.get("variadic"):
            return i
    return -1


def args_to_schema_values(cmd_type, args):
    """9 位 args → 具名控件初值 (list[str])。

    顺序与 `commands.schema(cmd_type)` 的参数一一对应; 变长参数项吸收「从该位起的
    连续多项」。无 schema / 未知命令 → 原样返回 9 位归一化值 (与回退表单一致)。
    """
    a = _norm9(args)
    ps = commands.schema(cmd_type)
    if not ps:
        return a
    vals, vi = [], _variadic_index(ps)
    for i, _p in enumerate(ps):
        if i >= _SLOTS:
            break
        if i == vi:
            vals.extend(a[i:])          # 变长: 从该位起连续多项
            break
        vals.append(a[i])
    return vals


def schema_values_to_args(cmd_type, values):
    """具名控件值 → 9 位 args (补齐/裁剪到 9)。None/空白归一为 ""。"""
    a = [""] * _SLOTS
    try:
        vals = list(values) if values is not None else []
    except Exception:
        vals = []
    ps = commands.schema(cmd_type)
    if not ps:
        for i in range(_SLOTS):
            a[i] = _clean(vals[i]) if i < len(vals) else ""
        return a
    vi = _variadic_index(ps)
    k = 0
    for i, _p in enumerate(ps):
        if i >= _SLOTS:
            break
        if i == vi:
            for j in range(i, _SLOTS):
                a[j] = _clean(vals[k]) if k < len(vals) else ""
                k += 1
            return a
        a[i] = _clean(vals[k]) if k < len(vals) else ""
        k += 1
    return a


def field_error(kind, choices, value):
    """单字段校验 → 错误消息 / None (与 `commands.validate` 同口径, 空值放行)。

    只做能确定的检查: 数字写成了非数字 / 取值不在枚举内; 空值一律放行
    (脚本约定「不填的位置写 None」, 由各命令自行取默认值)。
    """
    s = _clean(value)
    if s == "":
        return None
    if kind == "number":
        try:
            float(s)
        except (TypeError, ValueError):
            return "不是数字"
    if choices and s not in choices:
        return "取值应为 {} 之一".format("/".join(choices))
    return None


# ══════════════════════════════════════════════════════════════════════
# 渲染: schema → 具名控件; 返回 (frame, get_args)
# ══════════════════════════════════════════════════════════════════════

def _label_text(p):
    """参数标签: 名称 + 可选/默认/变长后缀 (把 schema 语义直接摆在界面上)。"""
    t = p.get("name") or p.get("raw") or "参数"
    if p.get("variadic"):
        t += " (可多值)"
    if p.get("default") is not None:
        t += "（默认{}）".format(p["default"])
    elif p.get("optional"):
        t += "（可选）"
    return t


def _build_generic(frame, initial_args, bg, ebg, fg, fgt, font):
    """回退: 9 个通用 Entry (等价既有「固定 9 格」表单)。返回 get_args()。"""
    vals = _norm9(initial_args)
    vars_ = []
    grid = tkinter.Frame(frame, bg=bg)
    grid.pack(padx=6, pady=4)
    for i in range(_SLOTS):
        v = tkinter.StringVar(value=vals[i])
        vars_.append(v)
        tkinter.Label(grid, text="p{}".format(i + 1), bg=bg, fg=fg,
                      font=font).grid(row=i // 3, column=(i % 3) * 2,
                                      padx=(4, 0), pady=2)
        tkinter.Entry(grid, textvariable=v, width=10, font=font, bg=ebg, fg=fg,
                      relief="solid", bd=1, insertbackground=fgt).grid(
            row=i // 3, column=(i % 3) * 2 + 1, padx=2, pady=2)

    def get_args():
        out = [_clean(v.get()) for v in vars_]
        while len(out) < _SLOTS:
            out.append("")
        return out[:_SLOTS]

    return get_args


def build_arg_form(parent, cmd_type, initial_args=None, *, colors, fonts, theme=None):
    """依据 `commands.schema(cmd_type)` 渲染参数控件。

    返回 ``(frame, get_args)``; ``get_args() -> list[str]`` **长度恒为 9** (空位置 "")。
    无 schema / 未知命令 / 无参数 → 退化为 9 个通用 Entry (等价现状)。

    colors/fonts 由调用方注入 (不 import ACRPA); theme 为 ``ui.theme`` 模块 (可空),
    仅用于解析强调色, 使控件与当前主题一致。
    """
    C = colors or {}
    F = fonts or {}
    f_body = F.get("body") or F.get("log")
    f_small = F.get("small") or f_body
    f_button = F.get("button") or f_body
    f_log = F.get("log") or f_body

    bg = C.get("bgc", "#FFFFFF")
    fg = C.get("fgb", "#333333")
    ebg = C.get("ebg", "#FFFFFF")
    fgt = C.get("fgt", "#000000")
    errbg = C.get("errbg", "#fff0f0")
    accent = C.get("ac", "#0078D4")
    if theme is not None:
        try:
            accent = theme.resolve_bg("ac", C)[0]
        except Exception:
            accent = C.get("ac", "#0078D4")

    frame = tkinter.Frame(parent, bg=bg)

    ps = commands.schema(cmd_type)
    if not ps:
        return frame, _build_generic(frame, initial_args, bg, ebg, fg, fgt, f_log)

    vals = args_to_schema_values(cmd_type, initial_args)
    fields = []                    # list[callable -> list[str]]: 每字段贡献若干连续参数位

    def _entry_get(v):
        return _clean(v.get())

    def _attach_number_check(entry, var, p):
        """数字字段 FocusOut 校验: 非法 → 标红 + 回退默认 (不阻断保存)。"""
        default = p.get("default")
        default = "" if default is None else str(default)

        def _on_focus_out(event=None):
            s = str(var.get()).strip()
            if s == "":
                try:
                    entry.configure(bg=ebg)
                except Exception:
                    pass
                return
            try:
                float(s)
                entry.configure(bg=ebg)
            except (TypeError, ValueError):
                try:
                    entry.configure(bg=errbg)
                except Exception:
                    pass
                var.set(default)

        entry.bind("<FocusOut>", _on_focus_out)

    def _add_scalar(p, row, init):
        tkinter.Label(frame, text=_label_text(p), bg=bg, fg=fg, font=f_small,
                      anchor="w").grid(row=row, column=0, sticky="w",
                                       padx=(6, 4), pady=2)
        v = tkinter.StringVar(value=init)
        choices = tuple(p.get("choices") or ())
        if choices:
            w = ttk.Combobox(frame, textvariable=v, values=choices,
                             state="readonly", width=14, font=f_body)
            if init not in choices:
                dflt = p.get("default")
                v.set(dflt if dflt in choices else "")
            w.grid(row=row, column=1, sticky="w", padx=4, pady=2)
        else:
            w = tkinter.Entry(frame, textvariable=v, width=18, font=f_log,
                              bg=ebg, fg=fg, relief="solid", bd=1,
                              insertbackground=fgt)
            w.grid(row=row, column=1, sticky="w", padx=4, pady=2)
            if p.get("kind") == "number":
                _attach_number_check(w, v, p)
        fields.append(lambda vv=v: [_entry_get(vv)])

    def _add_variadic(p, row, init_list):
        entries = []
        holder = tkinter.Frame(frame, bg=bg)
        holder.grid(row=row, column=0, columnspan=2, sticky="w",
                    padx=6, pady=2)

        def _add_one(val):
            r = tkinter.Frame(holder, bg=bg)
            r.pack(anchor="w", pady=1)
            tkinter.Label(r, text=_label_text(p), bg=bg, fg=fg, font=f_small,
                          width=18, anchor="w").pack(side="left")
            v = tkinter.StringVar(value=_clean(val))
            e = tkinter.Entry(r, textvariable=v, width=18, font=f_log,
                              bg=ebg, fg=fg, relief="solid", bd=1,
                              insertbackground=fgt)
            e.pack(side="left", padx=4)
            if p.get("kind") == "number":
                _attach_number_check(e, v, p)
            entries.append(v)

        seed = list(init_list) if init_list else [""]
        for val in seed:
            _add_one(val)
        tkinter.Button(holder, text="+ 追加", font=f_button, bg=accent, fg="white",
                       relief="flat", bd=0, padx=6, pady=1, cursor="hand2",
                       command=lambda: _add_one("")).pack(anchor="w", pady=2)

        def _collect(vs=entries):
            out = [_clean(v.get()) for v in vs]
            return out or [""]

        fields.append(_collect)

    vi = _variadic_index(ps)
    row = 0
    for i, p in enumerate(ps):
        if i >= _SLOTS:
            break
        if i == vi:
            init_list = list(vals[i:]) if i < len(vals) else [""]
            while len(init_list) > 1 and init_list[-1] == "":
                init_list.pop()
            _add_variadic(p, row, init_list)
            row += 1
            break
        init = vals[i] if i < len(vals) else ""
        if init == "" and p.get("default") is not None:
            init = str(p["default"])
        _add_scalar(p, row, init)
        row += 1

    def get_args():
        flat = []
        for g in fields:
            flat.extend(g())
        flat = flat[:_SLOTS]
        while len(flat) < _SLOTS:
            flat.append("")
        return flat

    return frame, get_args
