"""bump_version 逻辑回归测试 (临时) — 不修改真实 VERSION 文件。"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import bump_version as b

FAILURES = []

def check(name, cond):
    print("  {} {}".format("✓" if cond else "✗", name))
    if not cond:
        FAILURES.append(name)

print("[bump() 递增逻辑]")
cases = [
    ("0.1.23",    "patch", "0.1.24"),
    ("0.1.23beta", "patch", "0.1.24beta"),
    ("0.1.23-beta", "patch", "0.1.24-beta"),
    ("0.1.23rc1",  "patch", "0.1.24rc1"),
    ("0.1.24",    "minor", "0.2.0"),
    ("0.2.0",     "major", "1.0.0"),
    ("1.2.3",     "patch", "1.2.4"),
]
for cur, part, expect in cases:
    got = b.bump(cur, part)
    check("bump({}, {}) = {} (期望 {})".format(cur, part, got, expect), got == expect)

print("[版本格式校验]")
valid = [
    ("0.1.23", True), ("0.1.23beta", True), ("0.1.23-beta", True),
    ("0.1.23b", True), ("1.0.0rc.1", True), ("0.1.2.3", True),
    ("abc", False), ("", False), ("0.1", False),
]
for v, exp in valid:
    got = bool(b._VERSION_RE.match(v))
    check("格式 {} -> {}".format(v, got), got == exp)

print("\n" + "=" * 50)
if FAILURES:
    print("FAILED ({}): {}".format(len(FAILURES), ", ".join(FAILURES)))
else:
    print("全部通过 ✓")
sys.exit(1 if FAILURES else 0)
