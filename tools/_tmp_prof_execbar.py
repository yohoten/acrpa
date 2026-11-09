# -*- coding: utf-8 -*-
"""临时: profile exec_bar.build 找热点 — 用完删除。"""
import cProfile
import io
import os
import pstats
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import ui.exec_bar as eb

pr = cProfile.Profile()
pr.enable()
import app
app.build()
pr.disable()
print("total build done")

s = io.StringIO()
ps = pstats.Stats(pr, stream=s).sort_stats("cumulative").print_stats(20)
print(s.getvalue())
