# -*- coding: utf-8 -*-
"""Dump unique readable ASCII strings from a binary, C++ mangled symbols filtered out.

Usage: python tools/_inspect_strings.py <path-to-binary> [min-len]
"""
import re
import sys


def main(path, min_len=3):
    with open(path, "rb") as fh:
        data = fh.read()

    out = []
    seen = set()
    for m in re.finditer(rb"[\x20-\x7e]{%d,600}" % min_len, data):
        s = m.group().decode("latin1")
        # skip mangled C++ symbols / typeinfo / exception class names
        if any(t in s for t in ("??", "?$", "@@", "std::", "YAML::", "nlohmann", "?")):
            continue
        if s in seen:
            continue
        seen.add(s)
        out.append(s)
    for s in out:
        print(s)


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 3)
