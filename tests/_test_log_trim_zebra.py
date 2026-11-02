# -*- coding: utf-8 -*-
"""日志裁剪斑马纹回归 (P0-6 / D6) —— 奇数 excess 裁剪后 zebra 不得反转。

缺陷:
  1. `_log_trim` 裁掉的行数可能是奇数 → 剩余行整体左移一位, zebra 奇偶反转,
     之后追加新行会出现**连续两行同样式**;
  2. 滞回门槛 `limit + max(200, limit//10)` 在 `LOG_MAX_LINES<200` 时永不触发,
     小上限配置事实上不裁剪。

断言 (本文件, 220 行/221 行两种奇偶各跑一遍, LOG_MAX_LINES=10):
  A. 滞回门槛可达 → `_log_trim()` 返回值 != 0, 裁剪后行数 = 上限;
  B. 裁剪后 zebra 落在偶数行 (与裁剪前奇偶口径一致);
  C. 裁剪后不存在连续两行同 zebra;
  D. 再追加 3 行 → 仍按偶数行、仍无连续两行同样式。

注意: Tk Text 末尾恒有一个「隐含空行」(最后一行以换行收尾), 该行没有内容也
不会被上 zebra, 故所有期望值都只对**有内容的行**计算。

需要真实 Tk Text (只建 hidden root); 无显示环境自动 SKIP 并返回 0。
运行: python -u tests/_test_log_trim_zebra.py
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


def _scan(text):
    """→ (total, rows), rows = [(行号, 是否有 zebra)] 仅含有内容的行。

    lzebra 加在「行首 ▌ 之后」, 故按 {}.1 列查询 (与 log_dock 一致)。
    """
    total = int(float(text.index("end-1c")))
    rows = []
    for i in range(1, total + 1):
        content = text.get("{}.0".format(i), "{}.end".format(i))
        if not content:
            continue                      # 末尾隐含空行: 无内容也不上 zebra
        rows.append((i, "lzebra" in text.tag_names("{}.1".format(i))))
    return total, rows


def _dups(rows):
    """返回「连续两行 zebra 相同」的行号对。"""
    return [(a[0], b[0]) for a, b in zip(rows, rows[1:]) if a[1] == b[1]]


def _want_even(rows):
    return [i for i, _z in rows if i % 2 == 0]


def _run_case(tk, n_puts, limit):
    import state as st
    from core.log import ThreadSafeLog
    import ui.log_dock as ld

    tag = "[{}行/上限{}] ".format(n_puts, limit)
    root = tk.Tk()
    root.withdraw()
    try:
        text = tk.Text(root, height=8, width=40)
        text.pack()
        for _t in ("info", "success", "warning", "error"):
            text.tag_configure(_t)
        for _t in ("cbar_info", "cbar_success", "cbar_warning", "cbar_error"):
            text.tag_configure(_t)
        text.tag_configure("lzebra", background="#EEEEEE")
        text.tag_configure("lf_hide", elide=True)

        ld._rz = text
        ld._cbar_state["done"] = 0
        ld._logfilter["applied"] = 0
        st.LOG_MAX_LINES = limit

        tlog = ThreadSafeLog(text, None)
        for n in range(n_puts):
            tlog.put("第{}行".format(n + 1), "info")
        # GUI_BATCH_MAX=400 → 分片 flush 直到队列空
        while True:
            before = int(float(text.index("end-1c")))
            tlog.flush(root)
            if int(float(text.index("end-1c"))) == before:
                break

        ld._log_apply_cbar()
        total0, rows0 = _scan(text)
        check(tag + "A0. flush 后行数 = {} 次 put + 1 隐含空行".format(n_puts),
              total0 == n_puts + 1, total0)
        check(tag + "A0b. 裁剪前 zebra 已在偶数行 (基线口径, 且非空)",
              [i for i, z in rows0 if z] == _want_even(rows0)
              and len([i for i, z in rows0 if z]) > 0,
              [i for i, z in rows0 if z])

        excess = ld._log_trim()
        after_total, rows = _scan(text)
        check(tag + "A1. 滞回门槛可达: _log_trim 真的裁了 (小上限不失效)",
              excess != 0, excess)
        check(tag + "A2. excess 奇偶与「总数-上限」一致 (奇数场景已覆盖)",
              excess == total0 - limit, (excess, total0 - limit))
        check(tag + "A3. 裁剪后行数 = 上限 {}".format(limit),
              after_total == limit, after_total)

        zebra = [i for i, z in rows if z]
        want = _want_even(rows)
        check(tag + "B1. 裁剪后 zebra 落在偶数行 (未反转)",
              zebra == want, (zebra, want))
        check(tag + "B2. 裁剪后无连续两行同样式",
              _dups(rows) == [], _dups(rows))

        # 追加 3 行, 检查新旧交界
        for n in range(3):
            tlog.put("新增{}".format(n + 1), "info")
        tlog.flush(root)
        ld._log_apply_cbar()
        _total2, rows2 = _scan(text)
        zebra2 = [i for i, z in rows2 if z]
        want2 = _want_even(rows2)
        check(tag + "D1. 追加后 zebra 仍按偶数行",
              zebra2 == want2, (zebra2, want2))
        check(tag + "D2. 追加后无连续两行同样式",
              _dups(rows2) == [], _dups(rows2))
    finally:
        try:
            root.destroy()
        except Exception:
            pass


def main():
    try:
        import tkinter as tk
    except Exception as e:
        print("[SKIP] tkinter 不可用: {}".format(e))
        return 0
    try:
        tk.Tk().destroy()
    except Exception as e:
        print("[SKIP] 无显示环境, 无法创建 Tk root: {}".format(e))
        return 0

    for n_puts in (220, 221):        # 隐含空行使实际总行数 = n_puts+1:
        _run_case(tk, n_puts, 10)    # 220 → 221-10=211 奇数 / 221 → 222-10=212 偶数

    print("\n结果: PASS={} FAIL={}".format(_PASS, _FAIL))
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
