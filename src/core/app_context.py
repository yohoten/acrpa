"""AppContext: 统一依赖 / 服务容器 (方案文档阶段二第 4 项 S2)。

设计目标 (路线图 §9 S2):
  - 替代散落在各 ``ui/*_view.py`` 的模块级 ``_CTX`` dict 与 ACRPA 模块级转发别名,
    提供单一注册 / 读取入口;
  - 向后兼容: **先加只读访问器, 不删除旧键**; 各视图逐步切换, 最后才删旧键;
  - 线程安全: 构建期在主线程注册, 运行期由执行线程并发读取;
    ``get``/``has``/``__contains__`` 走 dict 单次读取 (GIL 下原子), 无需加锁;
    写操作 (register/update/clear/unregister) 用 RLock 保护。

用法:
    from core.app_context import ctx        # 模块级单例
    ctx.register("root", root)              # 返回 value, 可链式
    ctx.register_many(state=state, colors=C, fonts=FONTS)
    ctx.get("root")                         # -> root (缺省 None)
    ctx.get("root", fallback)               # -> 缺省回退
    ctx.snapshot()                          # -> 当前键值浅拷贝 (只读快照)
    ctx.has("root") / "root" in ctx / len(ctx)
    ctx.keys()                              # -> 键列表
    ctx.unregister("root") / ctx.clear()
"""

import threading

__all__ = ["AppContext", "ctx"]


class AppContext:
    """轻量线程安全的依赖 / 服务容器 (键 → 值)。"""

    __slots__ = ("_lock", "_data")

    def __init__(self):
        self._lock = threading.RLock()
        self._data = {}

    # ── 写 (加锁) ────────────────────────────────────────────────────
    def register(self, key, value):
        """登记一个依赖, 返回 value (便于链式 ``ctx.register(...)``)。"""
        with self._lock:
            self._data[key] = value
        return value

    def register_many(self, mapping=None, **kwargs):
        """批量登记; 返回 self (便于链式)。"""
        with self._lock:
            if mapping:
                self._data.update(mapping)
            if kwargs:
                self._data.update(kwargs)
        return self

    def update(self, mapping=None, **kwargs):
        return self.register_many(mapping, **kwargs)

    def unregister(self, key, default=None):
        """移除并返回旧值 (缺省 default)。"""
        with self._lock:
            return self._data.pop(key, default)

    def clear(self):
        with self._lock:
            self._data.clear()

    # ── 读 (无锁, dict 单次读取 GIL 下原子) ───────────────────────────
    def get(self, key, default=None):
        return self._data.get(key, default)

    def has(self, key):
        return key in self._data

    def keys(self):
        return list(self._data.keys())

    def snapshot(self):
        """返回当前键值的浅拷贝 (只读快照, 不暴露内部 dict)。"""
        with self._lock:
            return dict(self._data)

    def as_dict(self):
        return self.snapshot()

    # ── 协议 / 调试 ──────────────────────────────────────────────────
    def __contains__(self, key):
        return key in self._data

    def __len__(self):
        return len(self._data)

    def __getitem__(self, key):
        return self._data[key]

    def __iter__(self):
        with self._lock:
            return iter(list(self._data))

    def __repr__(self):
        with self._lock:
            return "<AppContext keys={}>".format(sorted(self._data.keys()))


# 模块级单例 (全局共享; import 时仅创建空容器, 零副作用)
ctx = AppContext()
