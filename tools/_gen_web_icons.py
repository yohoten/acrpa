# -*- coding: utf-8 -*-
"""PWA 图标生成器（构建/开发期工具，Phase4-2 批次3）。

从 `res/icon.png` 生成 PWA 所需的 192 / 512 图标：
    res/icon-192.png
    res/icon-512.png

背景：浏览器「添加到主屏」要求 manifest 声明 192 与 512 图标；本仓库源图
`res/icon.png` 为 128×128，需**上采样**（LANCZOS）。若日后提供更大源图
（≥512），本脚本会直接下采样，质量更好。

运行:
    python -X utf8 tools/_gen_web_icons.py
    python -X utf8 tools/_gen_web_icons.py --source res/icon.png --force

退出码: 0 = 成功；1 = 失败（PIL 缺失 / 源图缺失等）。
注意: 仅使用 PIL.Image（resize/save），不依赖被 spec exclude 的 ImageFont。
"""
import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_DEFAULT_SRC = os.path.join(_ROOT, "res", "icon.png")
_RES_DIR = os.path.join(_ROOT, "res")
_SIZES = (192, 512)


def _verify_png(path, expect):
    """PNG 魔数 + 尺寸校验；返回 (ok, detail)。"""
    try:
        with open(path, "rb") as f:
            head = f.read(8)
        if head[:4] != b"\x89PNG":
            return False, "not png"
        from PIL import Image
        im = Image.open(path)
        if tuple(im.size) != (expect, expect):
            return False, "size {}".format(im.size)
        return True, "{}x{}".format(im.size[0], im.size[1])
    except Exception as e:
        return False, "verify error: {!r}".format(e)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Generate PWA 192/512 icons")
    ap.add_argument("--source", default=_DEFAULT_SRC, help="源图（默认 res/icon.png）")
    ap.add_argument("--outdir", default=_RES_DIR, help="输出目录（默认 res/）")
    ap.add_argument("--force", action="store_true",
                    help="已存在且尺寸正确时也重新生成")
    args = ap.parse_args(argv)

    src = os.path.abspath(args.source)
    outdir = os.path.abspath(args.outdir)

    if not os.path.isfile(src):
        print("[FAIL] 源图不存在: {}".format(src))
        return 1
    if not os.path.isdir(outdir):
        try:
            os.makedirs(outdir)
        except Exception as e:
            print("[FAIL] 无法创建输出目录 {}: {!r}".format(outdir, e))
            return 1

    try:
        from PIL import Image
    except Exception as e:
        print("[FAIL] 需要 Pillow（PIL）: {!r}".format(e))
        return 1

    try:
        image = Image.open(src)
        mode = image.mode
        size0 = tuple(image.size)
    except Exception as e:
        print("[FAIL] 打开源图失败: {!r}".format(e))
        return 1
    print("[OK]   源图: {} ({} {}, mode={})".format(
        src, size0[0], size0[1], mode))

    rc = 0
    for n in _SIZES:
        dst = os.path.join(outdir, "icon-{}.png".format(n))
        if not args.force:
            ok, _d = _verify_png(dst, n)
            if ok:
                print("[SKIP] {} 已存在且尺寸正确".format(dst))
                continue
        try:
            im = image.convert("RGBA")
            im = im.resize((n, n), Image.LANCZOS)
            im.save(dst, format="PNG")
        except Exception as e:
            print("[FAIL] 生成 {} 失败: {!r}".format(dst, e))
            rc = 1
            continue
        ok, detail = _verify_png(dst, n)
        if ok:
            up = " (上采样)" if n > size0[0] else ""
            print("[OK]   {} -> {}{}".format(dst, detail, up))
        else:
            print("[FAIL] {} 校验失败: {}".format(dst, detail))
            rc = 1

    print("-" * 60)
    print("PASS" if rc == 0 else "FAIL")
    return rc


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(1)
