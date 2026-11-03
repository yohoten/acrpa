# -*- coding: utf-8 -*-
"""探针: 图片重采样耗时 + load_bg_photo 缓存命中 (v5 §4.1 / §4.2 / P1-10)。

输出
----
1. 1920×1080 目标下 LANCZOS / BOX / BILINEAR 各自耗时 (ms, 中位值);
2. 改造后 `bg_image.load_bg_photo` 首次 / 二次命中耗时 (ms);
3. `_pick_resample` 对 256×256 → 1920×1080 的实际选择 (应为 BOX)。

用法
----
    set PYTHONUTF8=1
    python -u tools/_out/_probe_img_resample.py > tools/_out/_probe_img_resample.txt 2>&1
"""
import os
import statistics
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
for _p in (os.path.join(_ROOT, "src"), _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import tkinter  # noqa: E402

import ui.bg_image as bgi  # noqa: E402

RES_DIR = os.path.join(_ROOT, "res")
NAME = "bg-ac.png"
TARGET = (1920, 1080)
REPEAT = 7

if not bgi._load_pil():
    print("PIL 不可用, 探针跳过")
    sys.exit(1)

from PIL import Image  # noqa: E402

path = bgi.resolve_icon_path(RES_DIR, NAME)
print("资源: {}".format(path))
src = Image.open(path).convert("RGBA")
print("源尺寸: {}×{}  目标: {}×{}".format(
    src.size[0], src.size[1], TARGET[0], TARGET[1]))

print("\n── 1. 单次缩放耗时 (ms, {} 次取中位) ──".format(REPEAT))
results = {}
for label, algo in (("LANCZOS", Image.LANCZOS),
                    ("BICUBIC", Image.BICUBIC),
                    ("BILINEAR", Image.BILINEAR),
                    ("BOX", Image.BOX)):
    samples = []
    for _ in range(REPEAT):
        t0 = time.perf_counter()
        src.resize(TARGET, algo)
        samples.append((time.perf_counter() - t0) * 1000.0)
    med = statistics.median(samples)
    results[label] = med
    print("  {:9s} {:8.2f} ms".format(label, med))

if results.get("LANCZOS"):
    print("  BOX / LANCZOS = {:.0f}% (越小越快)".format(
        results["BOX"] / results["LANCZOS"] * 100.0))

print("\n── 2. _pick_resample 实际选择 ──")
picked = bgi._pick_resample(src.size, TARGET)
name_of = {Image.LANCZOS: "LANCZOS", Image.BILINEAR: "BILINEAR",
           Image.BICUBIC: "BICUBIC", Image.BOX: "BOX", Image.NEAREST: "NEAREST"}
print("  256×256 → 1920×1080 选中: {}".format(name_of.get(picked, picked)))
print("  256×256 → 32×32   选中: {}".format(
    name_of.get(bgi._pick_resample(src.size, (32, 32)), "?")))

print("\n── 3. load_bg_photo 首次/二次耗时 (ms) —— 改造前 vs 改造后 ──")
root = tkinter.Tk()
root.withdraw()


def _measure(auto):
    """返回 (首次, 二次, 是否同一对象)。auto=False 等价改造前的纯 LANCZOS 行为。"""
    _old = bgi.BG_RESAMPLE_AUTO
    bgi.BG_RESAMPLE_AUTO = auto
    try:
        bgi.clear_cache()
        t0 = time.perf_counter()
        p1 = bgi.load_bg_photo(RES_DIR, NAME, alpha=0.4, bg_color="#FFFFFF",
                               size=TARGET, master=root)
        t_first = (time.perf_counter() - t0) * 1000.0
        t0 = time.perf_counter()
        p2 = bgi.load_bg_photo(RES_DIR, NAME, alpha=0.4, bg_color="#FFFFFF",
                               size=TARGET, master=root)
        t_second = (time.perf_counter() - t0) * 1000.0
        return t_first, t_second, (p1 is p2), (p1 is not None)
    finally:
        bgi.BG_RESAMPLE_AUTO = _old


before_first, before_second, _same_b, _ok_b = _measure(False)
after_first, after_second, same_a, ok_a = _measure(True)
print("  改造前(LANCZOS): 首次 {:8.2f} ms / 二次命中 {:8.2f} ms".format(
    before_first, before_second))
print("  改造后(自动分级): 首次 {:8.2f} ms / 二次命中 {:8.2f} ms  (同一对象={})".format(
    after_first, after_second, same_a))
if after_first > 0:
    print("  首次合成加速: {:.0f}% ({:.2f} → {:.2f} ms)".format(
        (1 - after_first / before_first) * 100.0, before_first, after_first))
if after_second > 0:
    print("  二次命中加速: {:.0f}×".format(after_first / after_second))
try:
    root.destroy()
except Exception:
    pass

print("\n结论: 算法分级生效 = {} ; 首次 {:.2f} ms (改造前 {:.2f} ms) / 二次命中 {:.2f} ms".format(
    name_of.get(picked) in ("BOX", "BILINEAR"), after_first, before_first, after_second))
sys.exit(0 if (ok_a and _ok_b) else 1)
