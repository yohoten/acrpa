# -*- coding: utf-8 -*-
"""_migrate_xls_to_acrpas — 存量 .xls 脚本批量转存为 .acrpas（非破坏性迁移）。

阶段二 · 第 5 项配套工具。**不联网、不修改/删除原始 .xls**：
以 script_io.load_script 读取旧 .xls，再以 script_io.save_script 另存为同名
``.acrpas``（与原文件同目录，扩展名替换）。原始 .xls 保持原样。

注意：工具名**不以 `_test_` 开头**，因此不会被 tools/run_tests.py 自动纳入 CI。

用法::

    python tools/_migrate_xls_to_acrpas.py                 # 迁移默认目录
    python tools/_migrate_xls_to_acrpas.py --dry-run       # 只报告，不写盘
    python tools/_migrate_xls_to_acrpas.py --dir some/dir  # 追加扫描目录（可重复）
    python tools/_migrate_xls_to_acrpas.py --overwrite     # 覆盖已存在的 .acrpas

默认扫描目录：``<repo>/template`` 与 ``<config 同级>/market_scripts``（存在才扫）。
退出码：0 = 无失败；1 = 有转换失败。
"""
import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_SRC = os.path.join(_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

try:  # utils 顶层 import tkinter；headless 环境忽略
    import tkinter  # noqa: F401
except Exception:
    pass

import script_io  # noqa: E402


def _market_install_dir():
    """市场安装根目录（与 marketplace.market_install_root 同口径）；失败返回 None。"""
    try:
        import state
        base = os.path.dirname(state.CONFIG_PATH)
        return os.path.join(base, "market_scripts")
    except Exception:
        return None


def _default_dirs():
    dirs = [os.path.join(_ROOT, "template")]
    mk = _market_install_dir()
    if mk:
        dirs.append(mk)
    return dirs


def _iter_xls(root):
    """递归列出 root 下的 .xls/.xlsx 绝对路径（不存在的目录返回 []）。"""
    out = []
    if not os.path.isdir(root):
        return out
    for dirpath, _dirs, files in os.walk(root):
        for fn in sorted(files):
            if fn.lower().endswith((".xls", ".xlsx")):
                out.append(os.path.join(dirpath, fn))
    return out


def _target_path(xls):
    return os.path.splitext(xls)[0] + script_io.ACRPAS_EXT


def _disp(path):
    """展示用路径：尽量相对仓库根；跨盘符等场景退回绝对路径。"""
    try:
        return os.path.relpath(path, _ROOT)
    except Exception:
        return path


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="把存量 .xls 脚本转存为 .acrpas（只读原文件，不联网）。")
    ap.add_argument("--dir", action="append", default=[],
                    help="追加扫描目录（可重复）；缺省用 template/ 与 market_scripts/")
    ap.add_argument("--dry-run", action="store_true",
                    help="只报告将要生成的文件，不写盘")
    ap.add_argument("--overwrite", action="store_true",
                    help="覆盖已存在的 .acrpas（默认跳过）")
    args = ap.parse_args(argv)

    dirs = args.dir or _default_dirs()
    targets = []
    for d in dirs:
        targets.extend(_iter_xls(d))

    scanned = len(targets)
    converted = 0
    skipped = 0
    failed = 0

    print("[migrate] 扫描目录: {}".format("; ".join(dirs)))
    print("[migrate] 模式: {}".format("dry-run（不写盘）" if args.dry_run else "写盘"))
    for xls in targets:
        dst = _target_path(xls)
        if os.path.exists(dst) and not args.overwrite:
            skipped += 1
            print("  skip  {}".format(_disp(xls)))
            continue
        try:
            rows = script_io.load_script(xls)
            if args.dry_run:
                converted += 1
                print("  plan  {}  ({} 行) -> {}".format(
                    _disp(xls), len(rows), os.path.basename(dst)))
            else:
                script_io.save_script(
                    dst, rows,
                    meta={"name": os.path.splitext(os.path.basename(xls))[0]})
                converted += 1
                print("  ok    {}  ({} 行) -> {}".format(
                    _disp(xls), len(rows), os.path.basename(dst)))
        except Exception as e:
            failed += 1
            print("  FAIL  {}  ({})".format(_disp(xls), e))

    print("[migrate] 扫描 {}，转换 {}，跳过 {}，失败 {}".format(
        scanned, converted, skipped, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
