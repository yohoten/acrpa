# -*- coding: utf-8 -*-
"""目录树盘点（含文件大小），用于外部依赖/模型核查。

用法:
    python -X utf8 tools/_dir_report.py "<目录>" [--deep]
"""
import os
import sys


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    root = argv[1]
    if not os.path.isdir(root):
        print("不是有效目录: {}".format(root))
        return 2
    deep = "--deep" in argv
    total = 0
    count = 0
    for cur, dirs, files in os.walk(root):
        dirs.sort()
        files.sort()
        rel = os.path.relpath(cur, root)
        depth = 0 if rel == "." else rel.count(os.sep) + 1
        if deep or depth <= 2:
            print("[DIR] {}".format(rel if rel != "." else "."))
            for f in files:
                p = os.path.join(cur, f)
                try:
                    sz = os.path.getsize(p)
                except OSError:
                    sz = -1
                print("      {:>13,}  {}".format(sz, f))
        for f in files:
            total += 1
            base = os.path.join(cur, f)
            try:
                count += os.path.getsize(base)
            except OSError:
                pass
    print("\n合计 {} 个文件, {:.1f} MB".format(total, count / 1048576.0))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
