# -*- coding: utf-8 -*-
"""M3 修正测量: _log_apply_cbar 的**首次**（真正插入 ▌+tag）成本。

probe_perf.py 里的 3 次重复取中位，第 2/3 次因行首已有 ▌ 被幂等跳过，
中位数被拉低到 0.05 ms（只测到了"全跳过"路径）。这里每轮都换新的 Text，
保证每次都测到真正的首插成本。
"""
import os
import statistics
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

import tkinter as tk

from core.log import ThreadSafeLog
import ui.log_dock as ld


def one_round(root, n=400):
    text = tk.Text(root, height=20, width=100)
    text.pack()
    for t in ("info", "success", "warning", "error"):
        text.tag_configure(t)
    text.tag_configure("lzebra", background="#EEEEEE")
    for t in ("cbar_info", "cbar_success", "cbar_warning", "cbar_error"):
        text.tag_configure(t)
    ld._rz = text
    ld._cbar_state["done"] = 0
    tlog = ThreadSafeLog(text, None)
    for i in range(n):
        tlog.put("首测日志 {} 一些填充文本".format(i), "info")
    tlog.flush(root)
    total = int(float(text.index("end-1c")))
    t0 = time.perf_counter()
    ld._log_apply_cbar()
    dt = (time.perf_counter() - t0) * 1000.0
    done = ld._cbar_state["done"]
    text.destroy()
    return total, done, dt


def main():
    root = tk.Tk()
    root.withdraw()
    vals = []
    for _ in range(3):
        total, done, dt = one_round(root, 400)
        vals.append(dt)
        print("首轮 apply_cbar: 行数={} done={} 耗时={:.2f} ms".format(total, done, dt))
    print("首次 apply_cbar (400 行) 中位 = {:.2f} ms  单行 ≈ {:.4f} ms".format(
        statistics.median(vals), statistics.median(vals) / 400.0))
    root.destroy()


if __name__ == "__main__":
    main()
