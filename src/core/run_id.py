# -*- coding: utf-8 -*-
"""core.run_id — 一次运行的统一标识 (贯穿日志 / history.jsonl / fail_context / CLI 报告)。

背景
----
脚本运行、工作流运行、调度任务、CLI 无头四条路径此前各自记录日志/历史/失败现场,
互无关联。本模块提供单一 ``run_id``, 由四条「顶层运行入口」在开始执行时调用
:func:`new_run` 生成并写入 contextvars 上下文; 下游通道经 :func:`current` 读取。

设计要点
--------
1. **contextvars 承载**: 线程安全; ``ThreadPoolExecutor.submit`` 会把提交时的上下文
   复制给工作线程, 因此并行分支自动继承父 run_id (一个顶层 run 内所有分支同 id)。
2. **模块级回退**: 保留 ``_LAST_RUN_ID`` 兜底。极少数在 ``new_run`` 之前就已创建、
   之后才执行的线程 (无法继承上下文) 经 ``current()`` 也能取到最近一次 run_id,
   保证四条通道「尽力一致」而非静默丢字段。
3. **嵌套执行不新建**: 子工作流引用、脚本内「运行工作流」命令属于同一顶层运行,
   不应再 ``new_run``, 直接继承父 id。
"""
import contextvars
import uuid

_ctx = contextvars.ContextVar("acrpa_run_id", default="")
_LAST_RUN_ID = ""


def new_run():
    """开始一次新运行: 生成全新 run_id 写入当前上下文, 返回该 id。

    仅在顶层运行入口调用 (GUI 运行 / CLI / 调度 / 单实例工作流)。
    """
    global _LAST_RUN_ID
    rid = uuid.uuid4().hex[:16]
    try:
        _ctx.set(rid)
    except Exception:
        pass
    _LAST_RUN_ID = rid
    return rid


def ensure_run():
    """返回当前 run_id; 尚未开始一次运行时新建一个 (防御性入口, 供领域层兜底)。"""
    rid = current()
    if rid:
        return rid
    return new_run()


def current():
    """→ 当前上下文 run_id (空则回退模块级最近一次, 再则空串)。"""
    try:
        rid = _ctx.get()
    except Exception:
        rid = ""
    return rid or _LAST_RUN_ID


def reset():
    """清空当前上下文与回退值 (一次运行结束后调用, 避免跨运行串味)。"""
    global _LAST_RUN_ID
    try:
        _ctx.set("")
    except Exception:
        pass
    _LAST_RUN_ID = ""
