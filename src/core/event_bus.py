# -*- coding: utf-8 -*-
"""core.event_bus —— 同步进程内事件总线 (方案文档 S3)。

目标: 消除宿主对视图模块的「直调扇出」(执行状态 / 工作流高亮等) —— 宿主
``publish(topic, **payload)``, 视图模块在 build 时 ``subscribe(topic, fn)``,
二者互不 import, 新增/移除订阅者无需改动宿主。

约束 (与 core.app_context 一致):
  * 仅依赖标准库, import 零副作用 (只创建一个空总线单例);
  * 同步派发 (publish 内直接调用订阅者), 保证派发顺序确定且无后台线程;
  * 线程安全 (RLock), 订阅者异常相互隔离 (单个订阅者抛错不影响其余订阅者)。

注意: 同步总线天然存在重入风险 —— 订阅者若在回调内对同一 topic 无条件再 publish,
会无限递归。约定订阅者不得在同一 topic 回调内无条件重发同一 topic。
"""
import threading
from collections import defaultdict


class EventBus:
    """进程内同步事件总线。"""

    def __init__(self):
        self._lock = threading.RLock()
        self._subs = defaultdict(list)

    def subscribe(self, topic, fn=None):
        """订阅 topic; 返回 fn (支持装饰器用法)。重复订阅同一 fn 幂等。

        支持两种用法::

            bus.subscribe("t", fn)   # 直接订阅
            @bus.subscribe("t")      # 装饰器
            def fn(**kw): ...
        """
        def _wrap(f):
            with self._lock:
                if f not in self._subs[topic]:
                    self._subs[topic].append(f)
            return f
        if fn is None:
            return _wrap
        return _wrap(fn)

    def unsubscribe(self, topic, fn):
        """退订 topic; 返回 fn (未订阅则静默)。"""
        with self._lock:
            try:
                self._subs[topic].remove(fn)
            except ValueError:
                pass
        return fn

    def publish(self, topic, **payload):
        """同步派发 topic 给所有订阅者, 返回成功调用数; 订阅者异常被隔离。

        先快照订阅者再逐个调用 (调用在锁外), 故订阅者回调内可安全 subscribe/
        unsubscribe, 但不得无条件重发同一 topic (避免递归)。
        """
        with self._lock:
            subs = list(self._subs.get(topic, []))
        called = 0
        for fn in subs:
            try:
                fn(**payload)
                called += 1
            except Exception:
                pass
        return called

    def subscribers(self, topic):
        """返回 topic 当前订阅者快照 (列表)。"""
        with self._lock:
            return list(self._subs.get(topic, []))

    def has(self, topic):
        """topic 是否已有订阅者。"""
        with self._lock:
            return bool(self._subs.get(topic))

    def clear(self):
        """清空全部订阅 (供测试/重建使用)。"""
        with self._lock:
            self._subs.clear()

    def __contains__(self, topic):
        return self.has(topic)


# 模块级单例 (import 零副作用: 仅一个空总线)
bus = EventBus()
