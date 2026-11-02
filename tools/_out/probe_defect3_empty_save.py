# -*- coding: utf-8 -*-
"""缺陷3探针: 数据层 (script_io.save_script) 对「空 rows 覆盖已有文件」无守卫。

同时静态对照 UI 层守卫:
  ACRPA.py:3253-3262 _cmd_save  -> if not state._editor_rows: 弹「脚本为空，无法保存」并 return
  所以 Ctrl+S 路径有守卫; 本探针只证明**原语本身**是破坏性的, 供架构师判断
  其它调用点 (_persist_script / 版本快照 / 自动化脚本) 是否安全。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

from scriptdata import ScriptData
import script_io

OUT = os.path.join(ROOT, "tools", "_out", "_tmp_defect3")
os.makedirs(OUT, exist_ok=True)
fp = os.path.join(OUT, "five_rows.acrpas")

if os.path.exists(fp):
    os.remove(fp)

rows = [ScriptData(cmd_type="等待", args=[str(i)] + [""] * 8) for i in range(1, 6)]
script_io.save_script(fp, rows, meta={"name": "five_rows"})
before_rows = script_io.load_script(fp)
before_len = len(before_rows)
before_size = os.path.getsize(fp)
print("=== [1] 初始文件: {}".format(fp))
print("    before 行数 = {}  (内容: {})".format(
    before_len, [r.to_tuple() for r in before_rows]))
print("    before 字节数 = {}".format(before_size))

# 复刻 _persist_script(ACRPA.py:3186-3188) 的写盘原语, rows 传入「加载窗口期的空列表」
script_io.save_script(fp, [], meta={"name": "five_rows"})
after_rows = script_io.load_script(fp)
after_len = len(after_rows)
after_size = os.path.getsize(fp)
print("=== [2] 用空 rows 覆盖后")
print("    after 行数  = {}".format(after_len))
print("    after 字节数 = {}".format(after_size))
print("    文件原文     = {!r}".format(open(fp, "r", encoding="utf-8").read()[:400]))

print("=== [3] 判定")
print("    行数变化: {} → {}".format(before_len, after_len))
print("    是否被写成空脚本: {}".format(after_len == 0))
print("    备份文件: {}".format([n for n in os.listdir(OUT) if fp.split(os.sep)[-1] in n]))
