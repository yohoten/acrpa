# -*- coding: utf-8 -*-
"""日志色条 Tcl 收敛 (v5 §3.4 L4 / P1-6): ▌ 内联 + 批量 tag_add + 导出 strip。

不创建 Tk 窗口: 用最小 Text 替身 (FakeText) 计数 `tag_add` / `insert` 调用次数,
因此可在无 GUI 的 CI 环境运行。
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import ui.log_dock as ld


PASS = FAIL = 0


def check(title, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS {}".format(title))
    else:
        FAIL += 1
        print("  FAIL {} {}".format(title, detail))


class FakeText(object):
    """最小 Text 替身: 只实现 _log_apply_cbar / 导出用到的接口。

    行模型与 Tk 一致: `lines[-1]` 恒为隐式末尾空行, `index("end-1c")` 返回
    "<行数>.0", `get("N.0","end-1c")` 返回第 N 行起的剩余全文。
    """

    def __init__(self, lines=None):
        self.lines = list(lines or [])
        if not self.lines or self.lines[-1] != "":
            self.lines.append("")
        self.tag_add_calls = 0
        self.insert_calls = 0
        self.tags = {}

    def index(self, spec):
        if str(spec) == "end-1c":
            return "{}.0".format(len(self.lines))
        raise ValueError(str(spec))

    def get(self, a, b):
        try:
            start = int(str(a).split(".")[0])
        except Exception:
            start = 1
        if str(b) != "end-1c":
            raise ValueError(str(b))
        return "\n".join(self.lines[start - 1:])

    def insert(self, idx, s):
        self.insert_calls += 1
        n = int(str(idx).split(".")[0])
        self.lines[n - 1] = str(s) + self.lines[n - 1]

    def tag_add(self, tag, *rngs):
        self.tag_add_calls += 1
        self.tags.setdefault(tag, []).extend(rngs)

    def tag_remove(self, tag, *a):
        self.tags.pop(tag, None)

    def tag_ranges(self, tag):
        return []

    def tag_names(self, idx=None):
        return ()


print("── 1. LOG_CBAR_INLINE 生效 (生产端已内联 ▌) ──")
check("1a. log_dock 与 core.log 的开关同源为 True", ld.LOG_CBAR_INLINE is True,
      ld.LOG_CBAR_INLINE)

print("── 2. 400 行一批: tag_add ≤2 次, 0 次逐行 insert ──")
_rows = ["▌[12:00:{:02d}] [INFO] line {}".format(i % 60, i) for i in range(1, 401)]
fake = FakeText(_rows)
ld._rz = fake
ld._cbar_state["done"] = 0
ld._log_apply_cbar()

check("2a. 单批 400 行 tag_add 次数 ≤2", fake.tag_add_calls <= 2, fake.tag_add_calls)
check("2b. 未发生逐行 insert(▌) (0 次)", fake.insert_calls == 0, fake.insert_calls)
check("2c. 游标推进到 400", ld._cbar_state["done"] == 400, ld._cbar_state["done"])
check("2d. 400 行行首仍为 ▌",
      all(fake.lines[i].startswith("▌") for i in range(400)),
      [l[:6] for l in fake.lines[:3]])
check("2e. cbar_info 区间覆盖 400 行 (800 个索引)",
      len(fake.tags.get("cbar_info", [])) == 800,
      len(fake.tags.get("cbar_info", [])))
check("2f. lzebra 区间覆盖偶数行 (200 行 = 400 个索引)",
      len(fake.tags.get("lzebra", [])) == 400,
      len(fake.tags.get("lzebra", [])))

print("── 3. 幂等: 二次调用不再改变任何东西 ──")
_calls_before = fake.tag_add_calls
ld._log_apply_cbar()
check("3a. 二次调用不再 tag_add (游标已到 400)", fake.tag_add_calls == _calls_before,
      fake.tag_add_calls)

print("── 4. 导出文本 strip 掉 ▌ ──")
_tmp = os.path.join(tempfile.gettempdir(), "_acrpa_cbar_export_test.txt")
try:
    _n = ld._log_export_write(_tmp, fake.lines[:-1], set())
    with open(_tmp, "r", encoding="utf-8") as f:
        _txt = f.read()
    check("4a. 导出行数 = 400", _n == 400, _n)
    check("4b. 导出文本不含 ▌", "▌" not in _txt, _txt[:30])
    _first_expect = _rows[0][1:]          # 去掉行首 ▌
    _last_expect = _rows[-1][1:]
    check("4c. 首行内容与去掉 ▌ 后一致",
          _txt.split("\n")[0] == _first_expect, _txt.split("\n")[0])
    check("4d. 末行内容与去掉 ▌ 后一致",
          _txt.rstrip("\n").split("\n")[-1] == _last_expect,
          _txt.rstrip("\n").split("\n")[-1])
finally:
    try:
        os.remove(_tmp)
    except Exception:
        pass

print("── 5. 过滤隐藏行在导出时被跳过 ──")
try:
    _n = ld._log_export_write(_tmp, fake.lines[:-1], {2, 3})
    with open(_tmp, "r", encoding="utf-8") as f:
        _txt = f.read()
    check("5a. 隐藏 2 行后导出 398 行", _n == 398, _n)
    check("5b. 第 2 行内容未被导出", "line 2\n" not in _txt, _txt[:60])
finally:
    try:
        os.remove(_tmp)
    except Exception:
        pass

print("\n结果: PASS={} FAIL={}".format(PASS, FAIL))
sys.exit(1 if FAIL else 0)
