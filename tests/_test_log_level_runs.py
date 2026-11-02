# -*- coding: utf-8 -*-
"""日志级别区间回归 (P0-1 / D1) —— 相邻同 tag 合并成跨行 range 后, 第 2..N 行不得丢级别。

缺陷链:
  1. `core/log.py` 的 ThreadSafeLog.flush 把**相邻同 tag** 的多条日志合并成
     **一个跨多行**的 tag range (spans 合并);
  2. 旧 `ui/log_dock.py:_log_level_runs` 每个 tag 只登记 `rngs[k]`(左端行号),
     区间其余行不登记 → 第 2..N 行丢级别;
  3. 后果: 色条回退 `cbar_info`; 级别过滤把第 2/3 条 error 一起隐藏。

断言 (本文件):
  A. 连续 3 条 error + 1 条 info → `_log_level_runs` 摊平出的 line_level 含
     第 1/2/3 行的 'error' 与第 4 行的 'info';
  B. 第 1/2/3 行行首色条 tag 均为 `cbar_error`;
  C. 级别过滤 = ERROR 时这 3 行**全部可见**(不被 lf_hide), 第 4 行 info 被隐藏。

需要真实 Tk Text (只建一个 hidden root, 不建主窗口):
无显示环境自动 SKIP 并返回 0 (便于进 CI)。
运行: python -u tests/_test_log_level_runs.py
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


def _row_tags(text, line_no, col=0):
    try:
        return list(text.tag_names("{}.{}".format(line_no, col)))
    except Exception:
        return []


def main():
    try:
        import tkinter as tk
    except Exception as e:                      # 无 tkinter → SKIP
        print("[SKIP] tkinter 不可用: {}".format(e))
        return 0
    try:
        root = tk.Tk()
    except Exception as e:                      # 无显示环境 → SKIP
        print("[SKIP] 无显示环境, 无法创建 Tk root: {}".format(e))
        return 0

    try:
        root.withdraw()
        text = tk.Text(root, height=10, width=60)
        text.pack()
        for _t in ("info", "success", "warning", "error",
                   "cbar_info", "cbar_success", "cbar_warning", "cbar_error"):
            text.tag_configure(_t)
        text.tag_configure("lzebra", background="#EEEEEE")
        text.tag_configure("lf_hide", elide=True)

        from core.log import ThreadSafeLog
        import ui.log_dock as ld

        # 只注入渲染上下文, 不调用 build() (不需要整套主界面)
        ld._rz = text
        ld._cbar_state["done"] = 0
        ld._logfilter["applied"] = 0
        ld._log_auto_scroll = tk.BooleanVar(master=root, value=False)
        ld._log_level_var = tk.StringVar(master=root, value="全部")

        # ── 生产端: 3 条连续 error 会被合并成一个跨 3 行的 tag range ──
        tlog = ThreadSafeLog(text, None)
        tlog.put("E1 第一条错误", "error")
        tlog.put("E2 第二条错误", "error")
        tlog.put("E3 第三条错误", "error")
        tlog.put("I1 一条信息", "info")
        tlog.flush(root)

        total = int(float(text.index("end-1c")))
        # Text 末尾恒有一个隐含空行 (最后一行以 \n 收尾), 故正文行数 = total - 1
        last = text.get("{}.0".format(total), "{}.end".format(total))
        body = total - 1 if last == "" else total
        check("A0. 共写入 4 行正文 (Text 末尾另有隐含空行)", body == 4, (total, repr(last)))

        ranges = text.tag_ranges("error")
        check("A0b. error tag 是 1 个跨多行 range (缺陷前提成立)",
              len(ranges) == 2, list(ranges))

        runs = ld._log_level_runs(1, total)
        line_level = {}
        for lv, a, b in runs:
            if lv:
                for n in range(a, b + 1):
                    line_level[n] = lv
        check("A1. 第 1 行级别 = error", line_level.get(1) == "error", line_level)
        check("A2. 第 2 行级别 = error (合并 range 第 2 行不丢)", line_level.get(2) == "error", line_level)
        check("A3. 第 3 行级别 = error (合并 range 第 3 行不丢)", line_level.get(3) == "error", line_level)
        check("A4. 第 4 行级别 = info", line_level.get(4) == "info", line_level)

        # ── 色条: 三行都必须是 cbar_error ──
        ld._log_apply_cbar()
        for _i in (1, 2, 3):
            _tags = _row_tags(text, _i)
            check("B{}. 第 {} 行色条 = cbar_error".format(_i, _i),
                  "cbar_error" in _tags, _tags)
        check("B4. 第 4 行色条 = cbar_info", "cbar_info" in _row_tags(text, 4),
              _row_tags(text, 4))

        # ── 过滤 ERROR: 3 条 error 全可见, info 被隐藏 ──
        ld._log_level_var.set("ERROR")
        ld._log_apply_filter(full=True)
        hidden = {i: ("lf_hide" in _row_tags(text, i)) for i in range(1, total + 1)}
        check("C1. ERROR 过滤下第 1 行可见", hidden.get(1) is False, hidden)
        check("C2. ERROR 过滤下第 2 行可见", hidden.get(2) is False, hidden)
        check("C3. ERROR 过滤下第 3 行可见", hidden.get(3) is False, hidden)
        check("C4. ERROR 过滤下第 4 行 (info) 被隐藏", hidden.get(4) is True, hidden)
    finally:
        try:
            root.destroy()
        except Exception:
            pass

    print("\n结果: PASS={} FAIL={}".format(_PASS, _FAIL))
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
