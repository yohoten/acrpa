# -*- coding: utf-8 -*-
"""缺陷2探针: 证明 ui_bg_task.submit 在已 start 时是**异步**的。

即 `_editor_load_xls` (ACRPA.py:2558-2603) 在函数返回那一刻:
  · state._editor_rows 已被 2572 行置成 [] (旧值被丢弃);
  · 真正的 rows 还躺在工作线程/队列里 (2589-2594 的 _done 尚未执行)。
因此紧随其后的 main_run() 看到的是「空编辑器 + 已就位的 state.filename」。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

import ui.bg_task as bt


class FakeRoot(object):
    """只实现 after/after_cancel, 用来让 bg_task 进入「已启动」状态 (不跑 mainloop)。"""

    def __init__(self):
        self.jobs = []

    def after(self, ms, fn):
        self.jobs.append((ms, fn))
        return len(self.jobs)

    def after_cancel(self, jid):
        pass


def main():
    print("is_started(未 start) = {}".format(bt.is_started()))
    root = FakeRoot()
    bt.start(root, poll_ms=60)
    print("is_started(start 后) = {}".format(bt.is_started()))

    state = {"editor_rows": [], "filename": None}
    called = {"n": 0, "rows": None}

    def _load():
        # 模拟 script_io.load_script 的磁盘/解析耗时
        import time
        time.sleep(0.20)
        return ["row1", "row2", "row3"]

    def _done(rows):
        called["n"] += 1
        called["rows"] = rows
        state["editor_rows"] = rows

    # 复刻 _editor_load_xls: 先清空编辑器, 再 submit 后台加载
    state["editor_rows"] = []
    t0 = __import__("time").time()
    bt.submit(_load, _done, None, name="load-script")
    t1 = __import__("time").time()
    print("submit 返回耗时 = {:.2f} ms".format((t1 - t0) * 1000.0))
    print("submit 返回瞬间: state._editor_rows = {}  (空 → main_run 拿到 0 行)".format(
        state["editor_rows"]))
    print("submit 返回瞬间: _done 已执行次数 = {}".format(called["n"]))
    print("pending() = {}".format(bt.pending()))

    # 手动替代 Tk 主循环: 等回调入队后 drain
    import time
    for _ in range(40):
        time.sleep(0.02)
        if bt.pending() == 0:
            break
    bt.drain()
    print("drain 之后: _done 已执行次数 = {} rows = {}".format(called["n"], called["rows"]))
    bt.shutdown()
    print("=== 结论: submit 异步 → _editor_load_xls 返回时 rows 必为空 (缺陷2 前提成立)")


if __name__ == "__main__":
    main()
