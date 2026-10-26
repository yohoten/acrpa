# -*- coding: utf-8 -*-
"""打包体积门禁 (方案文档 §8.2: ACRPA.exe > 15 MB 即构建失败)。

用法::

    python tools/_check_volume.py                 # 校验 dist/ 下的产物
    python tools/_check_volume.py --path X.exe    # 校验指定文件
    python tools/_check_volume.py --max-mb 16     # 临时放宽上限

退出码: 0 = 通过 / 1 = 超限 / 2 = 未找到产物（CI 里通常按 0 或 1 判定，
"没产物" 不算体积问题，所以单独一码，便于区分「忘了打包」和「包太大」）。
"""
import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

DEFAULT_MAX_MB = 15.0


def check(path=None, max_mb=None, root=None):
    """→ (rc, text)。

    path 给定则只校验该文件; 否则按 ``build.exe_candidates()`` 找产物。
    """
    limit = DEFAULT_MAX_MB if max_mb is None else float(max_mb)
    if path:
        candidates = [path]
    else:
        try:
            import build as build_mod
            candidates = build_mod.exe_candidates()
        except Exception:
            base = root or ROOT
            candidates = [os.path.join(base, "dist", "ACRPA.exe"),
                          os.path.join(base, "dist", "ACRPA", "ACRPA.exe")]
    for cand in candidates:
        if not cand or not os.path.isfile(cand):
            continue
        size_mb = os.path.getsize(cand) / (1024.0 * 1024.0)
        text = "{}: {:.2f} MB (上限 {:.1f} MB)".format(
            os.path.basename(cand), size_mb, limit)
        return (1 if size_mb > limit else 0), text
    return 2, "未找到打包产物: {}".format(" | ".join(str(c) for c in candidates))


def main(argv=None):
    ap = argparse.ArgumentParser(description="ACRPA 打包体积门禁")
    ap.add_argument("--path", default=None, help="指定 exe 路径")
    ap.add_argument("--max-mb", type=float, default=DEFAULT_MAX_MB, help="体积上限(MB)")
    args = ap.parse_args(argv)
    rc, text = check(path=args.path, max_mb=args.max_mb)
    if rc == 0:
        print("[OK] 体积门禁通过: {}".format(text))
    elif rc == 1:
        print("[FAIL] 体积门禁未通过: {}".format(text))
    else:
        print("[SKIP] {}".format(text))
    return rc


if __name__ == "__main__":
    sys.exit(main())
