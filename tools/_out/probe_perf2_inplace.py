# -*- coding: utf-8 -*-
"""补充基线: 单行就地刷新 vs 全量 key 计算的真实单价 (5000 行规模)。"""
import os
import statistics
import sys
import time
import types

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

import tkinter as tk
from tkinter import ttk

import ACRPA
from scriptdata import ScriptData

ACRPA.state = types.SimpleNamespace(breakpoints=set())
ACRPA.engine = types.SimpleNamespace(conditional_breakpoints={})


def med(fn, repeat=3):
    ts = []
    for _ in range(repeat):
        t0 = time.perf_counter()
        fn()
        ts.append((time.perf_counter() - t0) * 1000.0)
    return statistics.median(ts)


def main():
    n = 5000
    rows = [ScriptData(cmd_type="等待", args=[str(i + 1)] + [""] * 8) for i in range(n)]
    root = tk.Tk()
    root.withdraw()
    cols = ("cmd",) + tuple("p{}".format(i) for i in range(1, 10))
    tree = ttk.Treeview(root, columns=cols, show="tree headings")
    iids = []
    for i, sd in enumerate(rows):
        iids.append(tree.insert("", "end", text=sd.to_tuple()[0][:2], values=sd.to_tuple()))
    tree.update_idletasks()
    print("树内行数 = {}".format(len(tree.get_children())))

    print("_tree_row_key 单次 = {:.4f} ms".format(med(lambda: ACRPA._tree_row_key(rows[0], 0), repeat=50)))
    print("tree.get_children() (5000 行) = {:.3f} ms".format(
        med(lambda: tree.get_children(), repeat=20)))

    mid = iids[n // 2]
    key = ACRPA._tree_row_key(rows[n // 2], n // 2)

    def one_item():
        tree.item(mid, text=key[0], values=key[1], tags=())

    print("tree.item() 单行就地刷新 (含 get_children 一次) = {:.3f} ms".format(
        med(one_item, repeat=20)))

    def full_keys():
        for i, sd in enumerate(rows):
            ACRPA._tree_row_key(sd, i)

    print("全量 new_keys 计算 (={} 行) = {:.3f} ms".format(n, med(full_keys)))
    root.destroy()


if __name__ == "__main__":
    main()
