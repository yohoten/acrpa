#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""AppContext 容器单测 (方案文档阶段二第 4 项 S2-a)。

用法:
    .venv\\Scripts\\python.exe -X utf8 tests\\_test_app_context.py

覆盖:
  A  导入零副作用    : import core.app_context 不建窗 / 不起线程 / 不写盘
  B  基础读写        : register/get/默认值/register_many/update/has/in/len/keys
  C  快照隔离        : snapshot 浅拷贝, 改快照不回写容器
  D  删除/清空       : unregister 返回值 / clear 后为空
  E  线程安全        : 多线程并发 get 与 register 不抛异常且最终一致
  F  协议/调试       : __getitem__ / __iter__ / __repr__ / __len__

退出码: 0 = 全部通过 / 1 = 有失败。
"""
import os
import sys
import threading

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

_PASS, _FAIL = [], []


def check(cond, msg, detail=""):
    (_PASS if cond else _FAIL).append(msg)
    print("[{}] {}{}".format("OK  " if cond else "FAIL", msg,
                             ("  ({})".format(detail) if detail else "")))


def t_import_side_effect():
    print("\n── A. 导入零副作用 ──")
    import core.app_context as mod
    check(hasattr(mod, "AppContext"), "导出 AppContext 类")
    check(hasattr(mod, "ctx"), "导出模块级单例 ctx")
    check(isinstance(mod.ctx, mod.AppContext), "ctx 是 AppContext 实例")
    check(len(mod.ctx) == 0, "导入后 ctx 为空 (无副作用)")


def t_basic():
    print("\n── B. 基础读写 ──")
    from core.app_context import AppContext
    c = AppContext()
    check(c.get("missing") is None, "get 缺省返回 None")
    check(c.get("missing", 42) == 42, "get 支持默认值")
    check(c.register("root", "ROOT") == "ROOT", "register 返回 value (可链式)")
    check(c.get("root") == "ROOT", "get 取回已登记值")
    c.register_many(a=1, b=2)
    check(c.get("a") == 1 and c.get("b") == 2, "register_many(**kwargs)")
    c.update({"c": 3})
    check(c.get("c") == 3, "update(mapping)")
    check(c.has("root") and ("root" in c), "has / __contains__")
    check(len(c) == 4, "len 计数")
    check(set(c.keys()) == {"root", "a", "b", "c"}, "keys 返回键列表")


def t_snapshot():
    print("\n── C. 快照隔离 ──")
    from core.app_context import AppContext
    c = AppContext()
    holder = {"v": 1}
    c.register("holder", holder)
    snap = c.snapshot()
    check(snap["holder"] is holder, "snapshot 浅拷贝 (值对象同引用)")
    snap["extra"] = "x"
    check("extra" not in c, "改快照不回写容器 (不暴露内部 dict)")
    holder["v"] = 2
    check(c.get("holder")["v"] == 2, "浅拷贝语义: 值对象可变引用仍共享")


def t_remove():
    print("\n── D. 删除 / 清空 ──")
    from core.app_context import AppContext
    c = AppContext()
    c.register("k", "v")
    check(c.unregister("k") == "v", "unregister 返回旧值")
    check(c.unregister("k", "dflt") == "dflt", "unregister 缺省回退")
    c.register_many(x=1, y=2)
    c.clear()
    check(len(c) == 0, "clear 后为空")


def t_thread_safety():
    print("\n── E. 线程安全 (并发读写) ──")
    from core.app_context import AppContext
    c = AppContext()
    errors = []

    def _writer(n):
        try:
            for i in range(2000):
                c.register("w{}".format(n), i)
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    def _reader():
        try:
            for _ in range(5000):
                c.get("w0")
                c.snapshot()
                c.keys()
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    ts = [threading.Thread(target=_writer, args=(i,)) for i in range(4)]
    rs = [threading.Thread(target=_reader) for _ in range(4)]
    for t in ts + rs:
        t.start()
    for t in ts + rs:
        t.join()
    check(not errors, "并发读写无异常", "errors={}".format(errors))
    check(c.has("w0") and c.has("w3"), "写线程最终一致")


def t_protocol():
    print("\n── F. 协议 / 调试 ──")
    from core.app_context import AppContext
    c = AppContext()
    c.register("k", "v")
    check(c["k"] == "v", "__getitem__ 取值")
    check(list(iter(c)) == ["k"], "__iter__ 迭代键")
    check("k" in repr(c), "__repr__ 含键名")


def main():
    print("=== AppContext 容器单测 (S2-a) ===")
    t_import_side_effect()
    t_basic()
    t_snapshot()
    t_remove()
    t_thread_safety()
    t_protocol()
    print("\n=== 结果: {} (通过 {}, 失败 {}) ===".format(
        "FAIL" if _FAIL else "OK", len(_PASS), len(_FAIL)))
    if _FAIL:
        for m in _FAIL:
            print("  - {}".format(m))
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
