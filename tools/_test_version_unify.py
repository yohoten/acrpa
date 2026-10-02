# -*- coding: utf-8 -*-
"""版本号统一（唯一权威来源）回归自测。

断言（对应「需求二：版本号统一」）：
- V1: version_info.get_version() / VERSION == 根 VERSION 文件首行（strip）
- V2: netlink 节点暴露的版本 == version_info.VERSION；且 node.py 静态源码
      不再直接 open(.../"VERSION")，改为复用 version_info
- V3: version_info._FALLBACK_VERSION == 当前版本（与根 VERSION 一致）
- V4: index.html / index.en.html 各自的单一版本源（ACRPA_RELEASE.version）存在，
      且页面内除单一源外不再出现当前版本字面量
- V5: tools/README.md 不再包含「修改 src/updater.py 中的 VERSION」这类过时指令

用法: .venv\\Scripts\\python.exe -X utf8 tools\\_test_version_unify.py
退出码: 0=全部通过, 1=存在失败
"""
import io
import os
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(BASE, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import version_info as vi                                    # noqa: E402

_passed = [0]
_failed = [0]
_warned = [0]


def ok(name):
    _passed[0] += 1
    print("[OK]   " + name)


def warn(name, detail=""):
    _warned[0] += 1
    print("[WARN] " + name + ("  -> " + detail if detail else ""))


def fail(name, detail=""):
    _failed[0] += 1
    print("[FAIL] " + name + ("  -> " + detail if detail else ""))


def check(name, cond, detail=""):
    if cond:
        ok(name)
    else:
        fail(name, detail)
    return bool(cond)


def read_text(path):
    with io.open(path, "r", encoding="utf-8") as f:
        return f.read()


def current_version():
    """根 VERSION 文件首行（strip，忽略空行与 # 注释）。"""
    path = os.path.join(BASE, "VERSION")
    lines = [l.strip() for l in read_text(path).splitlines()
             if l.strip() and not l.strip().startswith("#")]
    return lines[0] if lines else ""


# ── V1 权威来源一致性 ──
def test_v1_authoritative():
    print("── V1 权威来源一致性 ──")
    ver = current_version()
    check("根 VERSION 文件首行非空", bool(ver), "got empty")
    check("version_info.get_version() == 根 VERSION 首行",
          vi.get_version() == ver, "{} != {}".format(vi.get_version(), ver))
    check("version_info.VERSION == 根 VERSION 首行",
          vi.VERSION == ver, "{} != {}".format(vi.VERSION, ver))


# ── V2 netlink/node.py 收敛 ──
def test_v2_netlink():
    print("── V2 netlink/node.py 收敛 ──")
    src_path = os.path.join(SRC, "netlink", "node.py")
    src = read_text(src_path)
    # 静态检查：不再直接读取 VERSION 文件（无引号字面量），改为复用 version_info
    has_literal = ('"VERSION"' in src) or ("'VERSION'" in src)
    check('node.py 不再直接读取 "VERSION" 文件', not has_literal,
          "仍存在 VERSION 文件字面量读取")
    check("node.py 改为引用 version_info", "version_info" in src)
    check("node.py 不再直接 open VERSION（无 os.path.join(root, ...) 版本读取）",
          'os.path.join(root, "VERSION")' not in src)
    # 运行时：节点暴露版本 == version_info.VERSION
    try:
        from netlink.bus import NetBus
        from netlink.node import NetLinkNode
        node = NetLinkNode(root=None, bus=NetBus())
        got = node.info.get("version")
        check("NetLinkNode.info['version'] == version_info.VERSION",
              got == vi.VERSION, "{} != {}".format(got, vi.VERSION))
    except Exception as e:
        fail("构造 NetLinkNode 并比对版本", repr(e))


# ── V3 回退版本同步 ──
def test_v3_fallback():
    print("── V3 回退版本同步 ──")
    ver = current_version()
    fb = getattr(vi, "_FALLBACK_VERSION", None)
    check("_FALLBACK_VERSION == 当前版本", fb == ver,
          "{} != {}".format(fb, ver))


# ── V4 网页单一源 ──
def _html_single_source(path):
    """返回 (是否存在单一源, 单一源内字面量数, 全文出现次数, 版本值)。"""
    text = read_text(path)
    m = re.search(r"ACRPA_RELEASE\s*=\s*\{(.*?)\}", text, re.S)
    if not m:
        return (False, 0, 0, "")
    block = m.group(0)
    vmatch = re.search(r"version\s*:\s*[\"']([^\"']+)[\"']", block)
    ver = vmatch.group(1) if vmatch else ""
    total = text.count(ver) if ver else 0
    in_block = block.count(ver) if ver else 0
    return (True, in_block, total, ver)


def test_v4_html():
    print("── V4 网页单一源 ──")
    for fn in ("index.html", "index.en.html"):
        path = os.path.join(BASE, fn)
        has_src, in_block, total, ver = _html_single_source(path)
        check("{} 存在 ACRPA_RELEASE 单一源".format(fn), bool(has_src))
        check("{} 单一源 version 非空".format(fn), bool(ver))
        if ver:
            check("{} 除单一源外无其它版本字面量".format(fn),
                  total == in_block and in_block >= 1,
                  "版本 {} 全文 {} 次 / 单一源内 {} 次".format(ver, total, in_block))


# ── V5 tools/README.md 指令修正 ──
def test_v5_readme():
    print("── V5 tools/README.md 指令修正 ──")
    path = os.path.join(BASE, "tools", "README.md")
    text = read_text(path)
    stale = re.search(r"修改\s*`?src/updater\.py`?\s*中的\s*VERSION", text)
    check("README.md 不再含『修改 updater.py 中的 VERSION』",
          stale is None, "仍存在过时指令")
    check("README.md 指明唯一来源为根 VERSION 文件",
          "唯一来源" in text and "VERSION" in text
          and "version_info.py" in text)


def main():
    test_v1_authoritative()
    test_v2_netlink()
    test_v3_fallback()
    test_v4_html()
    test_v5_readme()
    print("")
    print("通过 {} / 失败 {} / 警告 {}".format(_passed[0], _failed[0], _warned[0]))
    if _failed[0]:
        print("结论: FAIL")
        return 1
    print("结论: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
