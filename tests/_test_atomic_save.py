# -*- coding: utf-8 -*-
"""tools/_test_atomic_save.py — P0-6「脚本保存原子化」验收测试。

仅覆盖 src/utils.py 新增的 atomic_save / atomic_write_bytes。无 UI、无网络、
不依赖 tkinter 窗口（只 import 模块本身），用临时目录构造真实文件系统场景：

  1) 目标不存在   → 写入成功 / 内容正确 / 不产生 .bak / 无残留 .tmp
  2) 目标已存在   → .bak 内容 == 旧内容 / 目标 == 新内容 / 无残留 .tmp
  3) 失败注入     → 目标仍为旧内容(未被截断) / 无残留 .tmp / 异常向外抛出
  4) 残留 .tmp 清理、backup=False、pathlib.Path 入参
  5) atomic_write_bytes 等价用例（成功 / 覆盖+备份 / 失败注入）

退出码 0(全过) / 1(存在 FAIL)。输出行前缀: [OK] / [FAIL]。
运行：python tools/_test_atomic_save.py
(cmd.exe 下中文乱码可先单独执行 `set PYTHONIOENCODING=utf-8`，勿用 && 串联。)
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
try:
    import pathlib         # noqa: E402
except ImportError:        # pragma: no cover - py2 兜底
    pathlib = None

_FAILS = []
_OLD = b"OLD-CONTENT-second-gen"
_NEW = b"NEW-CONTENT-fresh"


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


def read(path):
    with open(path, "rb") as f:
        return f.read()


# ── 用例 1: 目标不存在 ──

def test_create(work):
    dest = os.path.join(work, "create.xls")
    res = u.atomic_save(dest, lambda tmp: open(tmp, "wb").write(_NEW))

    check(res is None, "atomic_save 目标不存在时返回 None")
    check(os.path.exists(dest), "atomic_save 目标不存在时创建了目标文件")
    check(read(dest) == _NEW, "atomic_save 写入内容与 writer 输出一致")
    check(not os.path.exists(dest + ".bak"),
          "atomic_save 目标原本不存在时不产生 .bak")
    check(not os.path.exists(dest + ".tmp"),
          "atomic_save 成功后无残留 .tmp")


# ── 用例 2: 目标已存在 → 覆盖 + 备份 ──

def test_backup(work):
    dest = os.path.join(work, "overwrite.xls")
    u.atomic_write_bytes(dest, _OLD, backup=False)   # 铺一个"旧文件"

    u.atomic_save(dest, lambda tmp: open(tmp, "wb").write(_NEW))
    check(read(dest) == _NEW, "atomic_save 覆盖后目标为新内容")
    check(os.path.exists(dest + ".bak"), "atomic_save 覆盖已存在目标时生成 .bak")
    check(read(dest + ".bak") == _OLD, "atomic_save 生成的 .bak 内容 == 覆盖前旧内容")
    check(not os.path.exists(dest + ".tmp"), "atomic_save 覆盖后无残留 .tmp")


# ── 用例 3: 失败注入 (writer 中途抛异常) ──

def test_failure_injection(work):
    dest = os.path.join(work, "inject.xls")
    u.atomic_write_bytes(dest, _OLD, backup=False)

    def _boom(tmp):
        # 先写一半, 再抛 —— 模拟磁盘满 / 文件被占用时的半截写入
        with open(tmp, "wb") as f:
            f.write(b"PARTIAL-HALF")
        raise RuntimeError("模拟写盘失败")

    raised = None
    try:
        u.atomic_save(dest, _boom)
    except RuntimeError as e:
        raised = e

    check(raised is not None, "atomic_save writer 异常时向外抛出 (未被吞掉)")
    check(str(raised or "") == "模拟写盘失败", "atomic_save 抛出的是 writer 原始异常")
    check(read(dest) == _OLD, "失败后目标内容仍为旧内容 (未被截断)")
    check(not os.path.exists(dest + ".tmp"), "失败后无残留 .tmp")
    check(not os.path.exists(dest + ".bak"),
          "失败后未生成 .bak (备份只发生在覆盖成功前一步)")


# ── 用例 4: 残留 tmp 清理 / backup=False / Path 入参 ──

def test_edge_cases(work):
    # 4.1 上一次崩溃遗留的 .tmp 必须先被清掉, 不能污染本次结果
    dest = os.path.join(work, "stale.xls")
    with open(dest + ".tmp", "wb") as f:
        f.write(b"STALE-JUNK")
    u.atomic_save(dest, lambda tmp: open(tmp, "wb").write(_NEW))
    check(read(dest) == _NEW, "存在残留 .tmp 时仍写出正确内容 (不被追加污染)")
    check(not os.path.exists(dest + ".tmp"), "残留 .tmp 已被清理")

    # 4.2 backup=False: 覆盖但不留 .bak
    dest2 = os.path.join(work, "nobak.xls")
    u.atomic_write_bytes(dest2, _OLD, backup=False)
    u.atomic_write_bytes(dest2, _NEW, backup=False)
    check(read(dest2) == _NEW, "backup=False 覆盖后内容正确")
    check(not os.path.exists(dest2 + ".bak"), "backup=False 不产生 .bak")

    # 4.3 pathlib.Path 入参 (str 归一)
    if pathlib is not None:
        p = pathlib.Path(work) / "pathlike.xls"
        u.atomic_write_bytes(p, _NEW)
        check(read(str(p)) == _NEW, "atomic_save 兼容 pathlib.Path 入参")
        check(not os.path.exists(str(p) + ".tmp"), "Path 入参无残留 .tmp")
    else:                                          # pragma: no cover
        ok("跳过 pathlib 用例 (环境无 pathlib)")


# ── 用例 5: atomic_write_bytes 基本等价 ──

def test_write_bytes(work):
    dest = os.path.join(work, "bytes.xls")

    res = u.atomic_write_bytes(dest, _OLD)
    check(res is None, "atomic_write_bytes 返回 None")
    check(read(dest) == _OLD, "atomic_write_bytes 首写内容正确")
    check(not os.path.exists(dest + ".bak"),
          "atomic_write_bytes 目标原本不存在时不产生 .bak")
    check(not os.path.exists(dest + ".tmp"), "atomic_write_bytes 首写后无残留 .tmp")

    u.atomic_write_bytes(dest, _NEW)          # 默认 backup=True
    check(read(dest) == _NEW, "atomic_write_bytes 覆盖后内容正确")
    check(read(dest + ".bak") == _OLD, "atomic_write_bytes 覆盖时 .bak == 旧内容")
    check(not os.path.exists(dest + ".tmp"), "atomic_write_bytes 覆盖后无残留 .tmp")


# ══════════════════════════════════════════════════════════════════════
# main
# ══════════════════════════════════════════════════════════════════════

def main():
    work = tempfile.mkdtemp(prefix="acrpa_atomic_test_")
    print("工作目录: {}".format(work))
    try:
        test_create(work)
        test_backup(work)
        test_failure_injection(work)
        test_edge_cases(work)
        test_write_bytes(work)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    print("\n" + "=" * 60)
    print("结果: {} 项 FAIL".format(len(_FAILS)))
    for m in _FAILS:
        print("  [FAIL] " + m)
    if _FAILS:
        return 1
    print("P0-6 原子写辅助验收点全部通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
