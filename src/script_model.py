# -*- coding: utf-8 -*-
"""script_model — ``state._editor_rows`` 的薄适配层（路线图 阶段二 · 第 6 项）。

定位
----
``state._editor_rows`` (``list[ScriptData]``) **仍是编辑器唯一真源**；本模块仅做
两件不改变数据所有权的事：

    1. **行身份**：以 ``ScriptData.id`` 作为稳定行标识，提供
       :meth:`ScriptTableModel.id_of` / :meth:`index_of`，让调用方在重排
       （上移/下移/增删）后仍能追踪「同一行」；
    2. **行级失效通知**：:meth:`invalidate_row`（单行数据变更）/ :meth:`invalidate_all`
       （结构变更）只负责把「哪一行 / 是否是结构变更」广播给订阅者，由订阅者
       决定「就地刷新单行」还是「全量重建」。

约束
----
* ``import script_model`` 无副作用：**不** import ``ACRPA``、**不** import
  ``tkinter``、仅依赖标准库（导入期不建窗）。
* 订阅者回调的异常彼此隔离：单个回调抛错不影响主流程，也不影响其它订阅者。

注意
----
``rows`` 属性返回**传入列表本身**（非拷贝）。编辑器多处会整体重绑定
``state._editor_rows``（undo/redo/载入），届时调用方需用新的列表重建 model。
"""


class ScriptTableModel:
    """``list[ScriptData]`` 的薄适配 + 行级失效广播。

    兼容策略：``state._editor_rows`` 仍为真源；本类不拥有、不复制数据。
    """

    def __init__(self, rows=None):
        # 直接持有传入列表引用（真源）；None 视为空列表
        self._rows = rows if rows is not None else []
        self._listeners = []

    # ── 数据访问 ──────────────────────────────────────────────────────
    @property
    def rows(self):
        """当前行列表（真源引用）。"""
        return self._rows

    def id_of(self, index):
        """返回第 ``index`` 行的稳定 id；越界返回 ``None``。"""
        if index is None or index < 0 or index >= len(self._rows):
            return None
        return getattr(self._rows[index], "id", None)

    def index_of(self, rid):
        """按稳定 id 查找行下标；未找到返回 ``-1``。"""
        if rid is None:
            return -1
        for i, sd in enumerate(self._rows):
            if getattr(sd, "id", None) == rid:
                return i
        return -1

    # ── 订阅 / 退订 ──────────────────────────────────────────────────
    def on_row_invalidated(self, cb):
        """订阅行失效通知；``cb(index_or_None, reason)``。重复订阅同一 cb 只保留一次。"""
        if cb is None:
            return
        if cb not in self._listeners:
            self._listeners.append(cb)

    def off_row_invalidated(self, cb):
        """退订行失效通知。"""
        self._listeners = [c for c in self._listeners if c is not cb]

    # ── 通知入口 ─────────────────────────────────────────────────────
    def invalidate_row(self, index, reason="update"):
        """单行变更：广播 ``cb(index, reason)``；index 越界则不广播。"""
        if index is None or index < 0 or index >= len(self._rows):
            return
        self._emit(index, reason)

    def invalidate_all(self, reason="structure"):
        """结构变更：广播 ``cb(None, reason)``。"""
        self._emit(None, reason)

    # ── 内部 ─────────────────────────────────────────────────────────
    def _emit(self, index, reason):
        """逐个通知订阅者，单个回调异常被隔离（不影响主流程与其它订阅者）。"""
        for cb in list(self._listeners):
            try:
                cb(index, reason)
            except Exception:
                # 隔离：记录堆栈但不向上抛，保证主流程与其它订阅者继续
                try:
                    import traceback
                    traceback.print_exc()
                except Exception:
                    pass


__all__ = ["ScriptTableModel"]
