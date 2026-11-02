# -*- coding: utf-8 -*-
"""P0-3 临时验收脚本 (scratch): 空 rows 守卫 + 显式 allow_empty 放行。

断言:
  1. save_script(fp, []) 抛 ValueError;
  2. 抛异常后原文件行数不变 (5 行);
  3. save_script(fp2, [], allow_empty=True) 允许写入 0 行 (新建空脚本另存路径)。
"""
import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
_SRC = os.path.join(_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import script_io  # noqa: E402
from scriptdata import ScriptData  # noqa: E402

rows = [ScriptData("等待", [str(i + 1)]) for i in range(5)]
tmp = tempfile.mkdtemp(prefix="acrpa_p03_")
fp = os.path.join(tmp, "five_rows.acrpas")
script_io.save_script(fp, rows, meta={"name": "five_rows"})
before = len(script_io.load_script(fp))
print("1) 初始落盘行数 = {}".format(before))

raised = False
try:
    script_io.save_script(fp, [])
except ValueError as e:
    raised = True
    print("2) save_script(fp, []) 抛 ValueError: {}".format(e))
after = len(script_io.load_script(fp))
print("3) 拒绝后磁盘行数 = {} (期望仍为 {})".format(after, before))

fp2 = os.path.join(tmp, "empty_ok.acrpas")
script_io.save_script(fp2, [], meta={"name": "empty_ok"}, allow_empty=True)
after2 = len(script_io.load_script(fp2))
print("4) allow_empty=True 写入空脚本行数 = {} (期望 0)".format(after2))

ok = raised and after == before and after2 == 0
print("VERDICT = {}".format("PASS" if ok else "FAIL"))
sys.exit(0 if ok else 1)
