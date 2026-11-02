# -*- coding: utf-8 -*-
"""性能基线探针 M1-M4 (每个指标测 3 次取中位数, 单位 ms).

M1 表格行 key 全量计算 (_tree_row_key = _row_text_for + sd.to_tuple)
M2 ttk.Treeview 插入/清空 9 列
M3 ThreadSafeLog.flush + _log_apply_cbar (400 行一批; 稳态 6000 行)
M4 PIL open/resize(LANCZOS/BOX/BILINEAR) + ui.bg_image.load_bg_photo 首次/二次
"""
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
from core.log import ThreadSafeLog
import ui.log_dock as ld

RES = os.path.join(ROOT, "res")
SIZES = (500, 2000, 5000)


def med(fn, repeat=3):
    ts = []
    for _ in range(repeat):
        t0 = time.perf_counter()
        fn()
        ts.append((time.perf_counter() - t0) * 1000.0)
    return statistics.median(ts), ts


def make_rows(n):
    return [ScriptData(cmd_type="等待",
                       args=[str(i + 1)] + ["" for _ in range(8)]) for i in range(n)]


# ═══════════════ M1 ═══════════════
def m1():
    print("### M1 表格行 key 全量计算 (_tree_row_key x n)")
    ACRPA.state = types.SimpleNamespace(breakpoints=set())
    ACRPA.engine = types.SimpleNamespace(conditional_breakpoints={})
    out = {}
    for n in SIZES:
        rows = make_rows(n)

        def run():
            for i, sd in enumerate(rows):
                ACRPA._tree_row_key(sd, i)

        m, ts = med(run)
        out[n] = m
        print("    n={:<5d} 中位={:8.2f} ms  单次={}  单行={:.4f} ms".format(
            n, m, ["{:.2f}".format(x) for x in ts], m / max(1, n)))
    return out


# ═══════════════ M2 ═══════════════
def m2():
    print("### M2 ttk.Treeview 插入/删除 n 行 (9 列)")
    out = {}
    root = tk.Tk()
    root.withdraw()
    cols = ("cmd",) + tuple("p{}".format(i) for i in range(1, 10))
    for n in SIZES:
        tree = ttk.Treeview(root, columns=cols, show="tree headings")
        vals = ("等待",) + tuple("" for _ in range(9))

        def do_insert():
            for i in range(n):
                tree.insert("", "end", text="{:02d}".format(i + 1), values=vals,
                            tags=())

        m_ins, ts_ins = med(do_insert)
        tree.update_idletasks()

        def do_delete():
            tree.delete(*tree.get_children())

        # 删之前先保证有 n 行
        do_insert()
        tree.update_idletasks()
        m_del, ts_del = med(do_delete)
        print("    n={:<5d} insert 中位={:8.2f} ms (单次 {})  delete 中位={:7.2f} ms".format(
            n, m_ins, ["{:.1f}".format(x) for x in ts_ins], m_del))
        tree.destroy()
        out[n] = (m_ins, m_del)
    root.destroy()
    return out


# ═══════════════ M3 ═══════════════
def m3():
    print("### M3 日志 flush(400 行) + _log_apply_cbar")
    out = {}
    root = tk.Tk()
    root.withdraw()
    text = tk.Text(root, height=20, width=100)
    text.pack()
    for t in ("info", "success", "warning", "error"):
        text.tag_configure(t)
    text.tag_configure("lzebra", background="#EEEEEE")
    for t in ("cbar_info", "cbar_success", "cbar_warning", "cbar_error"):
        text.tag_configure(t)
    ld._rz = text
    ld._cbar_state["done"] = 0
    import state as st
    st.LOG_MAX_LINES = 6000
    tlog = ThreadSafeLog(text, None)

    def flush_batch():
        for i in range(400):
            tlog.put("批内日志 {} 一些填充文本".format(i), "info")
        tlog.flush(root)

    m_flush, ts = med(flush_batch)
    ld._cbar_state["done"] = 0
    m_cbar, ts2 = med(ld._log_apply_cbar)
    print("    空面板 400 行: flush 中位={:.2f} ms  首次 apply_cbar 中位={:.2f} ms".format(
        m_flush, m_cbar))

    # 稳态: 先灌到 6000 行
    lines = int(float(text.index("end-1c")))
    while lines < 6000:
        for i in range(400):
            tlog.put("预热日志 {} 一些填充文本".format(i), "info")
        tlog.flush(root)
        ld._log_apply_cbar()
        lines = int(float(text.index("end-1c")))
    print("    稳态行数 = {}".format(lines))

    def steady_cycle():
        for i in range(400):
            tlog.put("稳态新增 {} 一些填充文本".format(i), "info")
        tlog.flush(root)
        ld._log_trim()
        ld._log_apply_cbar()

    m_steady, ts3 = med(steady_cycle)
    print("    稳态(6000 行) 一批 400 行 flush+trim+cbar 中位={:.2f} ms 单次={}".format(
        m_steady, ["{:.1f}".format(x) for x in ts3]))

    def steady_flush_only():
        for i in range(400):
            tlog.put("稳态A {} 一些填充文本".format(i), "info")
        tlog.flush(root)

    m_sf, _ = med(steady_flush_only)
    def steady_cbar_only():
        ld._log_apply_cbar()
    m_sc, _ = med(steady_cbar_only)
    print("    稳态拆解: flush 段中位={:.2f} ms  cbar 增量段中位={:.2f} ms".format(m_sf, m_sc))
    out["flush"] = m_flush
    out["cbar_empty"] = m_cbar
    out["steady_cycle"] = m_steady
    out["steady_flush"] = m_sf
    out["steady_cbar"] = m_sc
    text.destroy()
    root.destroy()
    return out


# ═══════════════ M4 ═══════════════
def m4():
    print("### M4 图片处理")
    from PIL import Image, ImageTk
    import ui.bg_image as bg
    out = {}
    p = os.path.join(RES, "bg-ac.png")
    print("    源图 {} 存在={} size={}".format(p, os.path.isfile(p),
                                                Image.open(p).size))
    m_open, ts = med(lambda: Image.open(p))
    print("    Image.open 中位={:.2f} ms 单次={}".format(
        m_open, ["{:.2f}".format(x) for x in ts]))
    src = Image.open(p).convert("RGBA")
    for name, filt in (("LANCZOS", Image.LANCZOS), ("BOX", Image.BOX),
                       ("BILINEAR", Image.BILINEAR), ("NEAREST", Image.NEAREST)):
        m, ts = med(lambda f=filt: src.resize((1920, 1080), f))
        out[name] = m
        print("    resize 1920x1080 {} 中位={:.2f} ms 单次={}".format(
            name, m, ["{:.2f}".format(x) for x in ts]))

    root = tk.Tk()
    root.withdraw()
    print("    --- ui.bg_image.load_bg_photo(res, size=(1920,1080)) 缓存效果 ---")
    t0 = time.perf_counter()
    ph1 = bg.load_bg_photo(RES, "bg-ac.png", alpha=0.4, bg_color="#FFFFFF",
                           size=(1920, 1080), master=root)
    t1 = time.perf_counter()
    ph2 = bg.load_bg_photo(RES, "bg-ac.png", alpha=0.4, bg_color="#FFFFFF",
                           size=(1920, 1080), master=root)
    t2 = time.perf_counter()
    print("    首次 = {:.2f} ms  (返回 {})".format((t1 - t0) * 1000.0, type(ph1)))
    print("    二次 = {:.2f} ms  (同一对象={})".format((t2 - t1) * 1000.0, ph1 is ph2))
    print("    last_error = {!r}".format(bg.get_last_error()))
    bg.clear_cache()
    t3 = time.perf_counter()
    ph3 = bg.load_bg_photo(RES, "bg-ac.png", alpha=0.4, bg_color="#FFFFFF",
                           size=(1920, 1080), master=root)
    t4 = time.perf_counter()
    print("    clear_cache() 后 = {:.2f} ms".format((t4 - t3) * 1000.0))
    root.destroy()
    return out


def main():
    print("=" * 70)
    r1 = m1()
    print("")
    r2 = m2()
    print("")
    r3 = m3()
    print("")
    r4 = m4()
    print("")
    print("### 汇总(ms, 3 次中位)")
    print("M1 key: {}".format({k: round(v, 2) for k, v in r1.items()}))
    print("M2 ins/del: {}".format({k: (round(v[0], 2), round(v[1], 2)) for k, v in r2.items()}))
    print("M3: {}".format({k: round(v, 2) for k, v in r3.items()}))
    print("M4: {}".format({k: round(v, 2) for k, v in r4.items()}))
    print("=== DONE")


if __name__ == "__main__":
    main()
