# -*- coding: utf-8 -*-
"""任务4反证探针:
  A. _bind_scroll_recursive (ACRPA.py:624-639) 的幂等提前 return 是否会跳过新增子树;
  B. _log_trim (log_dock.py:380-404) 裁剪后 lzebra 奇偶是否错位 (出现连续两行同样式)。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

import tkinter as tk

import ACRPA
from core.log import ThreadSafeLog
import ui.log_dock as ld
import state as st


# ══════════════════ A. 滚动绑定 ══════════════════
def probe_scroll_bind():
    print("########## A. _bind_scroll_recursive 漏绑新增子树 ##########")
    root = tk.Tk()
    root.withdraw()
    outer = tk.Frame(root)
    outer.pack()
    mid = tk.Frame(outer)
    mid.pack()
    inner = tk.Frame(mid)
    inner.pack()
    old = tk.Label(inner, text="old", width=8)
    old.pack()

    def handler(_e=None):
        pass

    ACRPA._bind_scroll_recursive(outer, handler)
    print("第一次调用后 老 Label.bind('<MouseWheel>') = {!r}".format(old.bind("<MouseWheel>")))
    print("             outer.bind                = {!r}".format(outer.bind("<MouseWheel>")))

    new = tk.Label(mid, text="new", width=8)
    new.pack()

    ACRPA._bind_scroll_recursive(outer, handler)
    print("第二次调用后 新增 Label.bind('<MouseWheel>') = {!r}".format(new.bind("<MouseWheel>")))
    print("             outer._scroll_handler is handler = {}".format(
        getattr(outer, "_scroll_handler", None) is handler))
    print("             mid._scroll_handler   is handler = {}".format(
        getattr(mid, "_scroll_handler", None) is handler))
    verdict = not bool(new.bind("<MouseWheel>"))
    print("=> 新增子树被跳过(缺陷成立)? {}".format(verdict))

    # 对照: 换成新 handler (模拟重建场景) 时是否恢复绑定
    def handler2(_e=None):
        pass
    ACRPA._bind_scroll_recursive(outer, handler2)
    print("改绑新 handler 后 新增 Label.bind = {!r}".format(new.bind("<MouseWheel>")))
    root.destroy()


# ══════════════════ B. 日志裁剪 zebra 错位 ══════════════════
def _zebra_rows(text):
    """注意: lzebra 加在「行首 ▌ 之后」(log_dock.py:613-614), 故须查 {}.1 而非 {}.0。"""
    total = int(float(text.index("end-1c")))
    out = []
    for i in range(1, total + 1):
        names = text.tag_names("{}.1".format(i))
        out.append((i, "lzebra" in names,
                    text.get("{}.0".format(i), "{}.1".format(i)) == "▌"))
    return out


def _report_dups(rows, label):
    dups = []
    for a, b in zip(rows, rows[1:]):
        if a[1] == b[1]:
            dups.append((a[0], b[0], a[1]))
    print("  {} 连续两行同样式(zebra相同)的对数 = {} 前 5 对 = {}".format(
        label, len(dups), dups[:5]))
    print("  {} zebra 位置(行号) = {}".format(label, [r[0] for r in rows if r[1]]))


def probe_zebra(n_lines, limit, tag_scene):
    print("")
    print("########## B-{} 灌 {} 行, LOG_MAX_LINES={} ##########".format(
        tag_scene, n_lines, limit))
    root = tk.Tk()
    root.withdraw()
    text = tk.Text(root, height=8, width=40)
    text.pack()
    for t in ("info", "success", "warning", "error"):
        text.tag_configure(t)
    text.tag_configure("lzebra", background="#EEEEEE")
    for t in ("cbar_info", "cbar_success", "cbar_warning", "cbar_error"):
        text.tag_configure(t)
    text.tag_configure("lf_hide", elide=True)

    ld._rz = text
    ld._cbar_state["done"] = 0
    ld._logfilter["applied"] = 0
    st.LOG_MAX_LINES = limit

    tlog = ThreadSafeLog(text, None)
    for n in range(n_lines):
        tlog.put("第{}行".format(n + 1), "info")
    # GUI_BATCH_MAX=400, 分片 flush 直到队列空
    while True:
        before = int(float(text.index("end-1c")))
        tlog.flush(root)
        if int(float(text.index("end-1c"))) == before:
            break
    total = int(float(text.index("end-1c")))
    ld._log_apply_cbar()
    print("  flush 后行数 = {} (上限 {}, 滞回门槛 = {})".format(
        total, limit, limit + max(200, limit // 10)))
    rows_before = _zebra_rows(text)
    _report_dups(rows_before, "裁剪前")

    excess = ld._log_trim()
    rows_after = _zebra_rows(text)
    print("  _log_trim() 返回 excess = {}  裁剪后行数 = {}".format(excess, len(rows_after)))
    print("  _cbar_state['done'] = {}".format(ld._cbar_state["done"]))
    print("  裁剪后逐行 (行号, 有lzebra, 首字符是▌):")
    for r in rows_after:
        print("      {}".format(r))
    _report_dups(rows_after, "裁剪后")
    # 裁剪后再来一批新行, 看新旧边界是否出现同样式相邻
    for n in range(3):
        tlog.put("新增{}".format(n + 1), "info")
    tlog.flush(root)
    ld._log_apply_cbar()
    rows_mix = _zebra_rows(text)
    print("  追加 3 行后逐行:")
    for r in rows_mix:
        print("      {}".format(r))
    _report_dups(rows_mix, "追加后")
    root.destroy()


def main():
    probe_scroll_bind()
    probe_zebra(20, 10, "20行上限10")
    probe_zebra(221, 10, "221行上限10")
    probe_zebra(220, 10, "220行上限10奇数裁剪")
    print("=== DONE")


if __name__ == "__main__":
    main()
