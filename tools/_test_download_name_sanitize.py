# -*- coding: utf-8 -*-
"""tools/_test_download_name_sanitize.py — 安全项 #6「浏览器下载文件名净化」验收测试。

仅覆盖 src/utils.py 新增的 safe_filename（纯函数）。**不导入 playwright /
browser_backend**，因此可纳入 `tools/run_tests.py --safe`（CI）核心子集。

背景（docs/ACRPA-完善路线图.md §7 #6）:
    download.suggested_filename 来自远端 Content-Disposition（不可信），
    直接 os.path.join(out_dir, name) → save_as 可被 `..\\..\\Startup\\x.bat`
    或绝对路径穿越，写出下载目录、覆盖任意可写文件。

覆盖:
  1) 目录穿越 / 绝对路径 / UNC      → 单段、无分隔符、无 '..'
  2) Windows 保留设备名 (CON/NUL/com1，含大小写混合)
  3) 尾部点/空格 (Windows 不允许)   → 结果无尾部点/空格
  4) 非法字符 [< > : " | ? *]        → 合法单段名
  5) 空/None/纯空白                 → 回退默认名
  6) 正常名 (含中文)                → 保持不变
  7) out_dir 内保证: 净化结果 join 临时目录后 abspath 仍在 tmp 之内

退出码: 0(全过) / 1(存在 FAIL)。输出行前缀: [OK] / [FAIL]。
运行: python tools/_test_download_name_sanitize.py
"""
import os
import sys
import shutil
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import utils as u          # noqa: E402

_FAILS = []

# Windows 保留设备名（测试侧独立复制，避免与实现共用同一份错误）
_RESERVED = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + ["COM%d" % i for i in range(1, 10)]
    + ["LPT%d" % i for i in range(1, 10)]
)


# ── 测试工具 ──

def ok(msg):
    print("[OK] " + msg)


def fail(msg):
    _FAILS.append(msg)
    print("[FAIL] " + msg)


def check(cond, msg):
    if cond:
        ok(msg)
    else:
        fail(msg)


def _is_single_segment(out):
    """结果是否为合法单段名（无 '/', '\\', ':', 无控制字符）。"""
    if out is None or out == "":
        return False
    for ch in ("/", "\\", ":"):
        if ch in out:
            return False
    for ch in out:
        if ord(ch) < 0x20 or ord(ch) == 0x7f:
            return False
    return True


def _assert_clean(out, label):
    check(_is_single_segment(out), "%s: 结果为单段合法名 (%r)" % (label, out))
    check(".." not in out, "%s: 结果不含 '..' (%r)" % (label, out))
    check(out != "." and out != "..", "%s: 结果不是 '.'/'..' (%r)" % (label, out))


def _reserved_hit(out):
    base = os.path.splitext(out)[0]
    return base.upper() in _RESERVED


# ── 用例 1: 目录穿越 / 绝对路径 / UNC ──

def test_traversal():
    cases = [
        "..\\..\\evil.bat",
        "../../evil.bat",
        "/etc/passwd",
        "C:\\Windows\\x.txt",
        "\\\\server\\share\\x",
        "c:evil.bat",                 # 盘符相对路径
        "..\\..\\..\\Windows\\System32\\a.dll",
        "sub/dir/report.csv",
    ]
    for name in cases:
        out = u.safe_filename(name)
        _assert_clean(out, "穿越/绝对路径 %r" % name)
        check(out != name, "穿越/绝对路径 %r 被净化 (%r)" % (name, out))


# ── 用例 2: Windows 保留设备名 ──

def test_reserved():
    for name in ("CON", "NUL", "com1", "nul", "AuX", "lpt9", "PRN", "COM9"):
        out = u.safe_filename(name)
        _assert_clean(out, "保留名 %r" % name)
        check(not _reserved_hit(out),
              "保留名 %r 不再是裸保留名 (%r)" % (name, out))
        check(out.upper() != name.upper(),
              "保留名 %r 被改写 (%r)" % (name, out))
    # "CON.txt" 形式: 系统同样视为保留
    out = u.safe_filename("CON.txt")
    check(not _reserved_hit(out), "'CON.txt' 被改写为安全名 (%r)" % out)


# ── 用例 3: 尾部点 / 空格 ──

def test_trailing():
    for name, expect_base in (("a.", "a"), ("a ", "a"), ("report.xlsx ", "report.xlsx")):
        out = u.safe_filename(name)
        _assert_clean(out, "尾部点/空格 %r" % name)
        check(not out.endswith(".") and not out.endswith(" "),
              "尾部点/空格 %r 被去除 (%r)" % (name, out))
        check(out == expect_base,
              "尾部点/空格 %r 结果 == %r (得 %r)" % (name, expect_base, out))


# ── 用例 4: 非法字符 ──

def test_illegal_chars():
    out = u.safe_filename('a<b>c:d"e|f?g*h.txt')
    _assert_clean(out, "非法字符输入")
    check(out.endswith(".txt"),
          "非法字符输入保留扩展名 (%r)" % out)
    for ch in '<>:"|?*':
        check(ch not in out, "非法字符 %r 已被移除 (%r)" % (ch, out))


# ── 用例 5: 空 / None / 纯空白 → 默认名 ──

def test_empty():
    for name in ("", None, "   ", "\t\n", "..", ".", "...", "///"):
        out = u.safe_filename(name)
        _assert_clean(out, "空值 %r" % (name,))
        check(out == "download", "空值 %r 回退默认名 (%r)" % (name, out))
    # 自定义默认名
    out = u.safe_filename("", default="report.xls")
    check(out == "report.xls", "空值回退自定义默认名 (%r)" % out)
    # 恶意 default 也被净化
    out = u.safe_filename("", default="..\\..\\evil.bat")
    _assert_clean(out, "恶意 default")
    check(out == "evil.bat", "恶意 default 也被净化 (%r)" % out)


# ── 用例 6: 正常名保持不变 ──

def test_normal():
    for name in ("report.xlsx", "报表B.csv", "data_2024.xls"):
        out = u.safe_filename(name)
        check(out == name, "正常名 %r 保持不变 (%r)" % (name, out))
        _assert_clean(out, "正常名 %r" % name)


# ── 用例 7: out_dir 内保证 ──

def test_inside_outdir(tmp):
    """净化后的名字 join 到 tmp 后, 真实绝对路径必须仍在 tmp 之内。"""
    root = os.path.abspath(tmp)
    prefix = root + os.sep
    evil_names = [
        "..\\..\\evil.bat",
        "../../evil.bat",
        "/etc/passwd",
        "C:\\Windows\\x.txt",
        "\\\\server\\share\\x",
        "..\\..\\..\\..\\Startup\\x.bat",
    ]
    for name in evil_names:
        clean = u.safe_filename(name)
        fp = os.path.join(tmp, clean)
        ap = os.path.abspath(fp)
        check(ap.startswith(prefix) and ap != root,
              "净化后路径仍在 out_dir 内: %r -> %s" % (name, ap))
        # 对照组: 未净化的原始名确实会越界(证明测试有效)
        raw = os.path.abspath(os.path.join(tmp, str(name)))
        if not raw.startswith(prefix):
            ok("对照组确认原始名 %r 会越界 (%s)" % (name, raw))


def main():
    test_traversal()
    test_reserved()
    test_trailing()
    test_illegal_chars()
    test_empty()
    test_normal()

    tmp = tempfile.mkdtemp(prefix="acrpa_dlname_")
    try:
        test_inside_outdir(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("-" * 60)
    if _FAILS:
        print("[FAIL] 共 %d 项失败" % len(_FAILS))
        return 1
    print("[OK] 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
