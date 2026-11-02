# -*- coding: utf-8 -*-
"""缺陷1探针: ThreadSafeLog 相邻同 tag 合并 → _log_level_runs 只取区间左端 → 丢级别。

复现链:
  1. core/log.py:226-231 相邻同 tag 的 span 合并成**一个** tag range;
  2. ui/log_dock.py:552-558 每个 tag 只取 rngs[k] (左端行号), 区间其余行不登记;
  3. 结果: 连续 3 条 error 只有第 1 行有级别 → 色条错色 (回退 cbar_info) +
     级别过滤把第 2/3 条 error 一起隐藏。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

import tkinter as tk

from core.log import ThreadSafeLog
import ui.log_dock as ld


def line_tags(text, i):
    try:
        return [n for n in text.tag_names("{}.0".format(i)) if n]
    except Exception:
        return []


def main():
    root = tk.Tk()
    root.withdraw()
    text = tk.Text(root, height=10, width=60)
    text.pack()
    for t in ("info", "success", "warning", "error"):
        text.tag_configure(t)

    ld._rz = text
    ld._cbar_state["done"] = 0

    tlog = ThreadSafeLog(text, None)
    tlog.put("E1 第一条错误", "error")
    tlog.put("E2 第二条错误", "error")
    tlog.put("E3 第三条错误", "error")
    tlog.put("I1 一条信息", "info")
    tlog.flush(root)

    total = int(float(text.index("end-1c")))
    print("=== [1] flush 后 Text 行数 = {}".format(total))
    for i in range(1, total + 1):
        print("    行{} 内容={!r}".format(
            i, text.get("{}.0".format(i), "{}.end".format(i))))

    print("=== [2] 每个级别 tag 的实际区间 (注意 error 是 1 个跨 3 行的 range)")
    for lv in ld._LOG_LEVEL_TAGS:
        print("    tag_ranges({!r}) = {}".format(lv, text.tag_ranges(lv)))

    print("=== [3] _log_level_runs(1, {}) 返回的 runs".format(total))
    runs = ld._log_level_runs(1, total)
    print("    runs = {}".format(runs))
    line_level = {}
    for lv, a, b in runs:
        if lv:
            for n in range(a, b + 1):
                line_level[n] = lv
    print("=== [4] 由 runs 摊平出的 line_level 字典 (即 _log_apply_cbar 的输入)")
    print("    line_level = {}".format(line_level))
    for i in range(1, total + 1):
        print("    行{} → 级别={!r}".format(i, line_level.get(i, "")))

    print("=== [5] _log_apply_cbar() 后每行的色条 tag (error 行应带 cbar_error)")
    ld._log_apply_cbar()
    for i in range(1, total + 1):
        print("    行{} 首字符={!r} tags={}".format(
            i, text.get("{}.0".format(i), "{}.1".format(i)), line_tags(text, i)))

    print("=== [6] 级别过滤 = ERROR 时哪些行被 lf_hide 隐藏 (期望: 3 条 error 全显示)")
    ld._log_level_var = tk.StringVar(master=root, value="ERROR")
    ld._log_auto_scroll = tk.BooleanVar(master=root, value=True)
    ld._log_apply_filter(full=True)
    for i in range(1, total + 1):
        hidden = "lf_hide" in text.tag_names("{}.0".format(i))
        print("    行{} 隐藏={} 内容={!r}".format(
            i, hidden, text.get("{}.0".format(i), "{}.end".format(i))))

    root.destroy()
    print("=== DONE")


if __name__ == "__main__":
    main()
