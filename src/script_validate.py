# -*- coding: utf-8 -*-
"""script_validate — 脚本运行前静态校验（路线图 阶段二 · 第 7 项: dry-run）。

职责
----
对一份「已载入内存的脚本行列表」做**只读静态体检**, 返回结构化 Issue 列表。
供两处使用:

    * ``ACRPA.main_run`` 运行前体检 (默认只记日志, 不阻断 —— main_run 同时被
      定时任务/托盘/热键复用, 绝不能在此弹模态框);
    * ``ACRPA.validate_script_cb`` 「✓ 校验」按钮 → 问题列表窗口。

纯函数约束 (**务必保持**)
-------------------------
* 不执行任何命令;
* 不落盘、不弹窗、不写日志 (由调用方决定怎么呈现 Issue);
* 不 import tkinter / pyautogui / engine —— 只依赖 ``commands`` 与 ``os``,
  以便在无头环境 (CI) 里秒级导入与调用。

Issue 结构
----------
    {"row": <1-based int>, "level": "error"|"warning",
     "code": str, "message": str}

``row`` 为 1-based 行号; 脚本级问题 (空脚本 / 块配对) 用 ``row=0``。

校验集 (刻意保持"轻量", 不做 CFG / 不做嵌套语义 / 不做数据流)
-----------------------------------------------------------
1. 空脚本            → ``error/empty_script`` (row=0)
2. 未知命令          → ``error/unknown_command``
3. 参数不合法        → ``error/param`` (个数过多 / 非数字 / 枚举越界;
                       未填写一律放行 —— 与 ``commands.validate`` 同口径)
4. 块配对 (仅计数)   → ``warning/block_unbalanced`` (row=0)
5. 图片不存在 (仅路径判定, **不** 触碰 locate/DD/缓存) → ``warning/image_missing``

显式不做 (见路线图 §阶段二第 7 项「不做」清单): 幂等保护、命令级超时、
断点续跑、行号绝对化重构、``commands.validate``/``hints`` 语义变更、
``check_script_commands`` 返回契约变更、变量数据流分析。
"""
import os

import capabilities
import commands

__all__ = ["validate_script", "unknown_command_names", "IMAGE_COMMANDS"]

# xls 遗留格式的「占位行」（行1）— 载入器已跳过, 此处仍防御性跳过
_TITLE_PLACEHOLDER = "（标题行）"

# 以「第一个参数 = 图片名」的命令（纯路径判定图片是否存在）
IMAGE_COMMANDS = ("找图", "区域找图", "点图", "区域点图")

# 块结构命令（仅做计数配对, 不做嵌套语义）
_IF_OPEN, _IF_CLOSE = "如果", "结束如果"
_LOOP_OPEN, _LOOP_CLOSE = "循环开始", "循环结束"
_ELSE = "否则"

_IMAGE_EXT_HINT = (".png", ".bmp", ".jpg", ".jpeg", ".gif", ".webp")


# ── 行适配 (同时支持 ScriptData 对象与 xlrd 风格可索引行) ──────────────
def _cmd_of(row):
    """取行的命令名 (str); 无法识别返回 ""。"""
    cmd = getattr(row, "cmd_type", None)
    if cmd is not None:
        return "" if cmd is None else str(cmd).strip()
    try:
        cell = row[0]
    except Exception:
        return ""
    val = getattr(cell, "value", cell)
    return "" if val is None else str(val).strip()


def _args_of(row):
    """取行的参数列表 (list[str])。"""
    args = getattr(row, "args", None)
    if args is None:
        try:
            args = [getattr(c, "value", c) for c in list(row)[1:10]]
        except Exception:
            args = []
    out = []
    for a in (args or []):
        out.append("" if a is None else str(a))
    return out


def _blank(v):
    """未填写判定 (含字面量 "None", 与 script_io 归一化口径一致)。"""
    if v is None:
        return True
    s = str(v).strip()
    return s == "" or s.lower() == "none" or s == "-"


def _issue(row, level, code, message):
    return {"row": int(row), "level": level, "code": code, "message": message}


def unknown_command_names(rows):
    """返回脚本里出现但未注册的命令名 (去重, 保持出现顺序)。

    与 ``market_upload.check_script_commands`` 的「未知命令」判定语义一致
    (truthy 名称 + 不在注册表 → 判未知)。此处以「行列表」为输入, 是该语义的
    公共纯函数版本 (``check_script_commands`` 仍以其既有 ``list[str]`` 契约从
    文件路径读取, 未改动)。
    """
    known = set(commands.list_names())
    seen = set()
    out = []
    for r in (rows or []):
        name = _cmd_of(r)
        if not name or name == _TITLE_PLACEHOLDER:
            continue
        if name in known or name in seen:
            continue
        seen.add(name)
        out.append(name)
    return out


def validate_script(rows, script_dir=""):
    """逐行静态校验, 返回 Issue 列表。

    不执行命令、不落盘、不弹窗、不写日志。
    ``script_dir`` 仅用于图片存在性判定 (``os.path.join(script_dir, 图片名)``)。
    """
    rows = list(rows or [])
    if not rows:
        return [_issue(0, "error", "empty_script", "脚本为空: 没有任何可执行的行")]

    known = set(commands.list_names())
    issues = []

    counts = {_IF_OPEN: 0, _IF_CLOSE: 0, _LOOP_OPEN: 0, _LOOP_CLOSE: 0, _ELSE: 0}

    for i, r in enumerate(rows, start=1):
        cmd = _cmd_of(r)
        # 跳过空行 / 遗留 xls 占位行(「（标题行）」)
        if not cmd or cmd == _TITLE_PLACEHOLDER:
            continue

        # 2. 命令存在性
        if cmd not in known:
            issues.append(_issue(i, "error", "unknown_command",
                                 "未知命令: {}".format(cmd)))
            continue

        if cmd in counts:
            counts[cmd] += 1

        args = _args_of(r)

        # 3. 参数校验 (复用 commands.validate; 未填写放行)
        try:
            errs, _normalized = commands.validate(cmd, args)
        except Exception:
            errs = []
        for e in errs:
            issues.append(_issue(i, "error", "param", e))

        # 4. 能力 (可选依赖) 缺失检查 (路线图 阶段二新增项①)
        #    纯查表 + 惰性探测 (capabilities 为纯 stdlib, 不加载 tkinter/pyautogui)。
        #    ("core",) 永不产生 Issue; 其余能力非 READY 时按 degraded 定级。
        for cap in commands.requires(cmd):
            if cap == "core":
                continue
            try:
                st = capabilities.state(cap)
            except Exception:
                st = capabilities.CapState.READY   # 失败开放
            if st == capabilities.CapState.READY:
                continue
            level = ("error" if capabilities.degraded(cap) in ("hard", "none")
                     else "warning")
            issues.append(_issue(
                i, level, "missing_capability",
                "第 {} 行 '{}' 需要扩展/能力 '{}', 当前 {}".format(
                    i, cmd, capabilities.label(cap), st.value)))

        # 5. 图片存在性 (纯路径判定, 绝不调用 locate/DD/缓存)
        if cmd in IMAGE_COMMANDS:
            name = (args[0] if args else "").strip()
            if not _blank(name):
                try:
                    path = os.path.join(script_dir or "", name)
                    exists = os.path.exists(path)
                except Exception:
                    exists = True  # 路径异常时不误报
                if not exists:
                    issues.append(_issue(
                        i, "warning", "image_missing",
                        "找不到图片文件: {} (相对脚本目录)".format(name)))

    # 4. 块配对 (仅计数; row=0)
    if counts[_IF_OPEN] != counts[_IF_CLOSE]:
        issues.append(_issue(
            0, "warning", "block_unbalanced",
            "「如果」({}) 与「结束如果」({}) 数量不匹配".format(
                counts[_IF_OPEN], counts[_IF_CLOSE])))
    if counts[_LOOP_OPEN] != counts[_LOOP_CLOSE]:
        issues.append(_issue(
            0, "warning", "block_unbalanced",
            "「循环开始」({}) 与「循环结束」({}) 数量不匹配".format(
                counts[_LOOP_OPEN], counts[_LOOP_CLOSE])))
    if counts[_ELSE] > counts[_IF_OPEN]:
        issues.append(_issue(
            0, "warning", "block_unbalanced",
            "「否则」({}) 多于「如果」({})".format(
                counts[_ELSE], counts[_IF_OPEN])))

    return issues
