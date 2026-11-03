# -*- coding: utf-8 -*-
"""重采样算法分级 (v5 §4.1 P1-10): `bg_image._pick_resample` 纯函数判定。

不创建 Tk 窗口、不解码真实图片 —— 只验证「按目标尺寸选算法」的规则表。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import ui.bg_image as bgi

PASS = FAIL = 0


def check(title, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS {}".format(title))
    else:
        FAIL += 1
        print("  FAIL {} {}".format(title, detail))


if not bgi._load_pil():
    print("  SKIP PIL 不可用, 无法验证算法常量")
    print("\n结果: PASS={} FAIL={}".format(PASS, FAIL))
    sys.exit(1 if FAIL else 0)

Image = bgi.Image
pick = bgi._pick_resample

print("── 1. 小图标 (目标长边 ≤64) → LANCZOS ──")
check("1a. 256×256 → 32×32 = LANCZOS",
      pick((256, 256), (32, 32)) == Image.LANCZOS, pick((256, 256), (32, 32)))
check("1b. 256×256 → 64×64 = LANCZOS",
      pick((256, 256), (64, 64)) == Image.LANCZOS, pick((256, 256), (64, 64)))
check("1c. 24×24 图标放大到 48×48 仍走小图标分支 (LANCZOS)",
      pick((24, 24), (48, 48)) == Image.LANCZOS, pick((24, 24), (48, 48)))

print("── 2. 大面积背景 (目标面积 ≥400k) → BOX ──")
check("2a. 256×256 → 1920×1080 = BOX",
      pick((256, 256), (1920, 1080)) == Image.BOX, pick((256, 256), (1920, 1080)))
check("2b. 256×256 → 800×600 (480k) = BOX",
      pick((256, 256), (800, 600)) == Image.BOX, pick((256, 256), (800, 600)))
check("2c. 400k 恰好等于阈值 → BOX",
      pick((256, 256), (800, 500)) == Image.BOX, pick((256, 256), (800, 500)))

print("── 3. 中等面积 (100k ≤ 面积 < 400k) → BILINEAR ──")
check("3a. 100×100 → 500×500 (250k) = BILINEAR",
      pick((100, 100), (500, 500)) == Image.BILINEAR, pick((100, 100), (500, 500)))
check("3b. 100×100 → 400×300 (120k) = BILINEAR",
      pick((100, 100), (400, 300)) == Image.BILINEAR, pick((100, 100), (400, 300)))
check("3c. 100×100 → 100×100 (10k, 不放不缩) = LANCZOS",
      pick((100, 100), (100, 100)) == Image.LANCZOS, pick((100, 100), (100, 100)))

print("── 4. 缩小类 (目标 < 源, 且不是小图标) → BOX ──")
check("4a. 2000×1000 → 800×400 = BOX",
      pick((2000, 1000), (800, 400)) == Image.BOX, pick((2000, 1000), (800, 400)))
check("4b. 1600×1200 → 1000×750 = BOX",
      pick((1600, 1200), (1000, 750)) == Image.BOX, pick((1600, 1200), (1000, 750)))

print("── 5. 小面积放大 (面积 <100k) → LANCZOS ──")
check("5a. 10×10 → 100×100 (10k) = LANCZOS",
      pick((10, 10), (100, 100)) == Image.LANCZOS, pick((10, 10), (100, 100)))

print("── 6. 非法尺寸 / 回退开关 ──")
check("6a. 零尺寸 → LANCZOS (不抛异常)",
      pick((0, 0), (100, 100)) == Image.LANCZOS, pick((0, 0), (100, 100)))
check("6b. None 尺寸 → LANCZOS (不抛异常)",
      pick(None, (100, 100)) == Image.LANCZOS, pick(None, (100, 100)))
_old = bgi.BG_RESAMPLE_AUTO
bgi.BG_RESAMPLE_AUTO = False
check("6c. BG_RESAMPLE_AUTO=False → 全部回 LANCZOS",
      pick((256, 256), (1920, 1080)) == Image.LANCZOS,
      pick((256, 256), (1920, 1080)))
bgi.BG_RESAMPLE_AUTO = _old

print("\n结果: PASS={} FAIL={}".format(PASS, FAIL))
sys.exit(1 if FAIL else 0)
