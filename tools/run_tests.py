# -*- coding: utf-8 -*-
"""ACRPA 测试运行器 —— 本地与 CI 的统一入口。

设计要点
--------
1. **独立子进程**: 每个脚本单独起进程并带超时。历史上这些脚本直接互相 import
   或被上一个脚本留下的全局状态污染, 一个卡死就拖垮整批。
2. **显式分类而非静默跳过**: `--safe`(CI 默认) 只跑不依赖 GUI 交互 / 浏览器 /
   真实网络的子集; 被排除的会打印 SKIP 与原因, 绝不悄悄不跑。
3. **单一事实来源**: 分类表就在下面的常量里, 新增脚本时若落进排除类会自动 SKIP,
   不会"看起来绿了其实没跑"。

用法:
    python tools/run_tests.py                 # 等价 --safe
    python tools/run_tests.py --all           # 全部 (本机全量回归)
    python tools/run_tests.py --only updater  # 只跑名字含 updater 的
    python tools/run_tests.py --list          # 只列分类, 不执行
    python tools/run_tests.py --json out.json # 结果落盘 (CI 归档)
退出码: 0=全部通过; 1=有失败; 2=有脚本超时
"""
import argparse
import io
import json
import os
import re
import subprocess
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(BASE, "tools")

# ── 分类规则 ────────────────────────────────────────────────────────
# 键 = 排除原因(也是 SKIP 时打印的说明), 值 = 判定函数
GUI_PREFIXES = ("_smoke_",)


def _why_not_safe(name):
    """返回排除原因; None 表示属于核心子集。

    只排除 GUI 冒烟: 它们要创建真实 Tk 窗口, 在 CI 的服务会话里通常起不来。
    其余脚本 (含 NetLink 的 2node/e2e/webui) 均为 localhost 自包含用例, 实测
    每个 1-14 秒跑完, 因此纳入 CI —— 这些恰恰是最有价值的回归网。
    """
    if name.startswith(GUI_PREFIXES):
        return "GUI 冒烟: 需要真实窗口会话 (本机全量回归时用 --all)"
    if name.startswith("_test_browser_"):
        return "浏览器: 需要 playwright 与 Chromium (未装时脚本会静默自跳过)"
    return None


def discover():
    """→ [(名字, 绝对路径)] 排序后的测试脚本列表。"""
    out = []
    for name in sorted(os.listdir(TOOLS)):
        if not name.endswith(".py"):
            continue
        if name.startswith("_test_") or name.startswith(GUI_PREFIXES):
            out.append((name, os.path.join(TOOLS, name)))
    return out


def run_one(path, timeout):
    """跑单个脚本 → dict(status, seconds, tail)。status ∈ pass/fail/timeout。"""
    started = time.time()
    try:
        p = subprocess.run(
            [sys.executable, "-X", "utf8", path],
            cwd=BASE, timeout=timeout,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        out = (p.stdout or b"").decode("utf-8", "replace")
        status = "pass" if p.returncode == 0 else "fail"
        code = p.returncode
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or b"").decode("utf-8", "replace") if e.stdout else ""
        status, code = "timeout", None
    return {
        "status": status,
        "returncode": code,
        "seconds": round(time.time() - started, 1),
        "tail": "\n".join(out.strip().splitlines()[-12:]),
    }


def main():
    ap = argparse.ArgumentParser(description="ACRPA 测试运行器")
    ap.add_argument("--all", action="store_true", help="运行全部脚本 (含 GUI/浏览器/网络)")
    ap.add_argument("--safe", action="store_true", help="只运行核心子集 (缺省)")
    ap.add_argument("--only", default="", help="只运行名字匹配该正则的脚本")
    ap.add_argument("--timeout", type=int, default=180, help="单个脚本超时秒数 (缺省 180)")
    ap.add_argument("--list", action="store_true", help="只列出分类, 不执行")
    ap.add_argument("--json", default="", help="把结果写入 JSON 文件")
    ap.add_argument("--verbose", action="store_true", help="失败时打印完整输出尾部")
    args = ap.parse_args()

    tests = discover()
    if args.only:
        rx = re.compile(args.only)
        tests = [(n, p) for n, p in tests if rx.search(n)]

    selected, skipped = [], []
    for name, path in tests:
        why = None if args.all else _why_not_safe(name)
        (skipped if why else selected).append((name, path, why))

    print("=" * 70)
    print("ACRPA 测试运行器 | 模式: {} | 选中 {} / 跳过 {} / 共 {}".format(
        "all" if args.all else "safe", len(selected), len(skipped), len(tests)))
    print("=" * 70)
    for name, _p, why in skipped:
        print("SKIP  {:<40} {}".format(name, why))
    if args.list:
        return 0
    if not selected:
        print("[WARN] 没有选中任何脚本")
        return 1

    results = {}
    failed = timed_out = 0
    for name, path, _ in selected:
        r = run_one(path, args.timeout)
        results[name] = r
        if r["status"] == "fail":
            failed += 1
        elif r["status"] == "timeout":
            timed_out += 1
        print("{:<5} {:<40} {:>6.1f}s".format(
            {"pass": "PASS", "fail": "FAIL", "timeout": "TMOUT"}[r["status"]],
            name, r["seconds"]))
        if r["status"] != "pass" and (args.verbose or r["status"] == "timeout"):
            print("      " + r["tail"].replace("\n", "\n      "))

    print("-" * 70)
    print("通过 {} / 失败 {} / 超时 {} (跳过 {})".format(
        len(selected) - failed - timed_out, failed, timed_out, len(skipped)))
    if failed:
        print("失败清单:")
        for name, r in results.items():
            if r["status"] == "fail":
                print("  - {} (rc={})".format(name, r["returncode"]))
                print("      " + r["tail"].replace("\n", "\n      "))

    if args.json:
        with io.open(args.json, "w", encoding="utf-8") as f:
            json.dump({"mode": "all" if args.all else "safe",
                       "skipped": [{"name": n, "reason": w} for n, _p, w in skipped],
                       "results": results}, f, ensure_ascii=False, indent=2)
        print("结果已写入: {}".format(os.path.relpath(args.json, BASE)))

    if failed:
        return 1
    if timed_out:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
