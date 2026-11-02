# -*- coding: utf-8 -*-
"""滚轮递归绑定回归 (P0-5 / D4) —— 幂等早退不得跳过整棵子树。

缺陷: 旧 `_bind_scroll_recursive` 在函数开头 `if getattr(parent,"_scroll_handler",
None) is handler: return`, 一旦父控件已绑过就直接返回, **不再递归子树** →
之后新增的子控件永久漏绑 `<MouseWheel>` (折叠/展开工具栏、动态加按钮后滚轮失效)。

断言 (本文件):
  A. 首次调用: 3 层嵌套容器与最深处的老 Label 都已绑定;
  B. **新增一个 Label 后再调一次** → 该 Label 已绑定 (P0-4/5 核心);
  C. 重复调用不叠加绑定 (绑定脚本与首次完全一致);
  D. 第 4 层深处新增控件也能绑上;
  E. 一次滚轮只触发一次回调 (若当前环境能投递事件; 否则打印 SKIP 不判失败)。

需要真实 Tk (会建一个小窗口); 无显示环境自动 SKIP 并返回 0。
运行: python -u tests/_test_scroll_bind_recursive.py
退出码: 0 = 全部通过 (或 SKIP) / 1 = 存在失败
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_SRC = os.path.join(_ROOT, "src")
for _p in (_SRC, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

_PASS = 0
_FAIL = 0


def check(name, cond, detail=""):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print("[OK]   {}".format(name))
    else:
        _FAIL += 1
        print("[FAIL] {}  | {}".format(name, detail))


def main():
    try:
        import tkinter as tk
    except Exception as e:
        print("[SKIP] tkinter 不可用: {}".format(e))
        return 0
    try:
        root = tk.Tk()
    except Exception as e:
        print("[SKIP] 无显示环境, 无法创建 Tk root: {}".format(e))
        return 0

    try:
        root.geometry("320x220")
        outer = tk.Frame(root)
        outer.pack()
        mid = tk.Frame(outer)
        mid.pack()
        inner = tk.Frame(mid)
        inner.pack()
        old = tk.Label(inner, text="old", width=8)
        old.pack()
        root.update()

        import ACRPA                              # noqa: E402 (重, 放到建窗之后)

        hits = {"n": 0}

        def handler(_e=None):
            hits["n"] += 1

        # ── A. 首次调用 ──
        ACRPA._bind_scroll_recursive(outer, handler)
        check("A1. 首次调用: outer 已绑定滚轮", bool(outer.bind("<MouseWheel>")))
        check("A2. 首次调用: 中间层 mid 已绑定滚轮", bool(mid.bind("<MouseWheel>")))
        check("A3. 首次调用: 最深处 old Label 已绑定滚轮",
              bool(old.bind("<MouseWheel>")))
        first_script = outer.bind("<MouseWheel>")

        # ── B. 二次调用: 新增子控件必须被绑上 (缺陷点) ──
        new = tk.Label(mid, text="new", width=8)
        new.pack()
        root.update()                             # 让新控件真正映射, 否则合成事件投递不到
        ACRPA._bind_scroll_recursive(outer, handler)
        check("B1. 二次调用: 新增 Label 已绑定滚轮 (P0 核心断言)",
              bool(new.bind("<MouseWheel>")), new.bind("<MouseWheel>"))
        check("B2. 二次调用: 老控件绑定仍在", bool(old.bind("<MouseWheel>")))
        check("B3. 二次调用不叠加绑定 (绑定脚本与首次一致)",
              outer.bind("<MouseWheel>") == first_script,
              (first_script, outer.bind("<MouseWheel>")))

        # ── C. 三次调用: 第 4 层新增控件也要绑上 ──
        deep = tk.Frame(inner)
        deep.pack()
        deep_lbl = tk.Label(deep, text="deep", width=8)
        deep_lbl.pack()
        ACRPA._bind_scroll_recursive(outer, handler)
        check("C1. 三次调用: 第 4 层新增控件已绑定滚轮",
              bool(deep_lbl.bind("<MouseWheel>")))

        # ── D. 一次滚轮只触发一次 (重复绑定会让回调翻倍) ──
        hits["n"] = 0
        new.event_generate("<MouseWheel>", delta=120)
        root.update()
        n_new = hits["n"]
        hits["n"] = 0
        old.event_generate("<MouseWheel>", delta=120)
        root.update()
        n_old = hits["n"]
        if n_new == 0 and n_old == 0:
            print("[SKIP] D 段: 当前环境不投递合成 <MouseWheel> 事件, 跳过回调计数断言")
        else:
            check("D1. 新增控件一次滚轮只触发 1 次回调", n_new == 1, n_new)
            check("D2. 老控件一次滚轮只触发 1 次回调", n_old == 1, n_old)
    finally:
        try:
            root.destroy()
        except Exception:
            pass

    print("\n结果: PASS={} FAIL={}".format(_PASS, _FAIL))
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
