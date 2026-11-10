# -*- coding: utf-8 -*-
"""脚本编辑「参数列表头 / 列宽 / 悬停提示」纯展示辅助 (S1-A 切片)。

从 ACRPA.py 迁出的只读展示逻辑: 只读取命令 schema (commands.schema / module_catalog),
据此动态改写表格列头、提供列宽估算与表头悬停说明。**不写任何存储格式**,
脚本 JSON / 工作流 JSON 零变化, 兼容旧文件。

列宽策略: `param_col_width` 只提供「按类型估算」的纯函数 (供自适应列宽/校验复用),
`sync_param_headings` **绝不改 width** —— 按选中行改列宽会让每次点选都重排整表,
双击编辑的第二次点击落到别的列。

设计 (与 workflow_view / exec_bar 范式一致): 本模块不 import ACRPA;
`sync_param_headings(tree)` / `bind_param_header_tip(tree)` 均以 `tree` 为显式入参,
消除对宿主闭包/全局控件的依赖。宿主 ACRPA.py 在构建段调用这两个函数完成绑定。
"""

import utils

__all__ = [
    "command_params", "command_param_names", "param_col_width",
    "param_hint_text", "sync_param_headings", "reset_param_headings",
    "bind_param_header_tip", "get_header_params",
]

# ── 缓存 (命令名 → 参数名列表 / 参数字典列表) + 当前选中行参数 (供表头 tooltip/列宽) ──
_PARAM_HEADING_CACHE = {}
_PARAM_INFO_CACHE = {}          # 命令名 → 参数字典列表 (含 name/raw/kind/choices/default/optional)
_HEADER_PARAMS = []             # 当前选中命令的参数字典列表 (供表头 tooltip)
_HEADING_KEY = None             # 上次写入表头的 (cmd, multi): 重复点选直接跳过


def command_params(cmd):
    """命令名 → schema 参数字典列表 (含 name/raw/kind/choices/default/optional)。

    主源 commands.schema (带 kind/choices, 供列宽自适应与单元格校验), 缺失时回退
    module_catalog (无 kind/choices, 校验按 string 放行); 数据只读, 不改任何存储格式。
    """
    name = str(cmd or "").strip()
    if not name:
        return []
    if name in _PARAM_INFO_CACHE:
        return _PARAM_INFO_CACHE[name]
    params = []
    try:
        import commands as _cmds
        for p in _cmds.schema(name):
            if isinstance(p, dict):
                params.append({
                    "name": str(p.get("name") or ""),
                    "raw": str(p.get("raw") or ""),
                    "kind": str(p.get("kind") or "string"),
                    "choices": tuple(p.get("choices") or ()),
                    "default": p.get("default"),
                    "optional": bool(p.get("optional")),
                })
    except Exception:
        params = []
    if not params:
        try:
            from ui import module_catalog as _mc
            item = _mc.get_command(name)
            if isinstance(item, dict):
                for p in (item.get("params") or ()):
                    if isinstance(p, dict):
                        params.append({
                            "name": str(p.get("name") or ""),
                            "raw": str(p.get("raw") or ""),
                            "kind": "string",
                            "choices": (),
                            "default": p.get("default"),
                            "optional": bool(p.get("optional")),
                        })
        except Exception:
            params = []
    _PARAM_INFO_CACHE[name] = params
    return params


def command_param_names(cmd):
    """命令名 → schema 参数名列表 (只读 schema, 失败返回 [])。"""
    name = str(cmd or "").strip()
    if not name:
        return []
    if name in _PARAM_HEADING_CACHE:
        return _PARAM_HEADING_CACHE[name]
    names = [p["name"] for p in command_params(name)]
    _PARAM_HEADING_CACHE[name] = names
    return names


def param_col_width(p):
    """按参数类型估算列宽 (设计 px, 未缩放): 数值窄、文本/路径宽 (P1-4 后续 a)。

    纯函数: kind == "number" → 54 (坐标/次数/精度/灰度等); 否则按
    「名长 × 每字符 ≈ 16px」在 [70, 150] 内。缺 kind 时回退名长启发式。
    """
    name = str(p.get("name") or "")
    if p.get("kind") == "number":
        return 54
    w = 70 + min(len(name) * 16, 80)
    return int(w)


def param_hint_text(params, i):
    """第 i 个参数的悬停说明: 原文 (含默认提示) + 可选/默认补充。"""
    if not params or i < 0 or i >= len(params):
        return ""
    p = params[i]
    raw = str(p.get("raw") or "").strip()
    name = str(p.get("name") or "").strip()
    seg = raw or name
    if not seg:
        return ""
    extra = []
    if p.get("optional"):
        extra.append("可选")
    default = p.get("default")
    if default not in (None, "") and "默认" not in raw:
        extra.append("默认 {}".format(default))
    return seg if not extra else "{}  ({})".format(seg, ", ".join(extra))


def sync_param_headings(tree):
    """选中某行时把「参数1..9」换成该命令 schema 字段名 —— **只改文字, 不改列宽**。

    列宽必须与选中行无关: 早先这里按当前命令的 schema 改 ``width``, 每次点选都会
    让整表重排 (实测 116px → 267/202/213px)。双击编辑的第一次点击就把列挪走,
    第二次点击落到别的列 —— 这正是「点一下表格就变样、不好编辑」的根因。
    列宽改由用户拖拽 / 双击分隔线自适应 (见 ``autofit_column``)。

    多选时列语义不唯一 → 回退通用「参数N」; 参数信息存入 _HEADER_PARAMS 供 tooltip。
    同一命令重复点选直接返回, 省掉 9 次 heading 的 Tcl 往返。
    """
    global _HEADER_PARAMS, _HEADING_KEY
    try:
        sel = tree.selection()
        multi = len(sel) > 1
        cmd = tree.set(sel[0], "cmd") if (sel and not multi) else ""
        params = command_params(cmd)
        _HEADER_PARAMS = params
        key = (cmd, multi)
        if key == _HEADING_KEY:
            return
        _HEADING_KEY = key
        for i in range(1, 10):
            p = params[i - 1] if i - 1 < len(params) else None
            nm = (p["name"] if p and p.get("name") else "参数{}".format(i))
            tree.heading("p{}".format(i), text=nm)
    except Exception:
        pass


def reset_param_headings():
    """外部结构变更 (清空/重建) 后允许下一次 sync 重写表头 (清掉缓存键)。"""
    global _HEADING_KEY
    _HEADING_KEY = None


def bind_param_header_tip(tree):
    """表头悬停提示 (P1-4 后续 b): 鼠标停在参数列表头上时显示该参数的说明/默认值。

    复用 utils 的 tooltip 窗口; <Motion> 高频触发, 仅在「所在列变化」时重算/重绘,
    避免刷屏。依赖 _HEADER_PARAMS (由 sync_param_headings 维护)。
    """
    _state = {"last": None}

    def _region_col(event):
        try:
            if tree.identify_region(event.x, event.y) != "heading":
                return None
            col = tree.identify_column(event.x)   # 形如 "#2"
            name = tree.column(col, "id")         # "cmd" / "p1".."p9"
            if not name or name == "cmd" or not name.startswith("p"):
                return None
            return int(name[1:]) - 1
        except Exception:
            return None

    def _on_motion(event):
        idx = _region_col(event)
        if idx is None:
            if _state["last"] is not None:
                _state["last"] = None
                try:
                    utils._cancel_tip_job(tree)
                    utils._hide_tooltip()
                except Exception:
                    pass
            return
        if _state["last"] == idx:
            return
        _state["last"] = idx
        text = param_hint_text(_HEADER_PARAMS, idx)
        if not text:
            return

        def _show():
            try:
                utils._show_tooltip(event, lambda: text)
            except Exception:
                pass
        try:
            utils._cancel_tip_job(tree)
            tree._tooltip_job = tree.after(utils.TOOLTIP_DELAY_MS, _show)
        except Exception:
            pass

    tree.bind("<Motion>", _on_motion, add=True)
    tree.bind("<Leave>",
              lambda _e: (_state.update(last=None),
                          utils._cancel_tip_job(tree), utils._hide_tooltip()),
              add=True)


def get_header_params():
    """当前选中命令的参数字典列表 (供测试/诊断读取; 正常由 sync_param_headings 维护)。"""
    return _HEADER_PARAMS
