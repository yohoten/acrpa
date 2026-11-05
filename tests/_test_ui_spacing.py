#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""UI 防回归自测 — C5 硬编码色白名单断言 + 间距 lint (UI 美化方案 v4 §8.3 P2-3 / §5.2 C5)。

用法:
    .venv\\Scripts\\python.exe -X utf8 tests\\_test_ui_spacing.py

覆盖 (无 GUI 依赖, 可进 CI):
  C5  硬编码色白名单
      客户端 ``src/**.py`` 禁止出现非注释 ``#RRGGBB`` 字面量, 唯一例外是
      ``ui/theme.py`` (单一色板真源) 与 ``netlink/webui.py`` (浏览器 HTML/CSS)。
      其余已人工审查的文件按「当前非注释字面量集合」登记进 ``COLOR_WHITELIST``,
      出现**新增**字面量即失败 (防回归: 只允许递减, 不允许增长)。
      登记说明: 白名单内几乎全是 ``C.get(key, "#fallback")`` / ``utils.themed(key, "#fallback")``
      的防御性回退默认值、自包含回退色板, 以及画布专用色族 (workflow_view)。
  D1  间距 lint
      ``dialogs.py`` 出现 ``pad[xy]=数字`` 即硬失败 (P1-4 已收口为 sp()/gap());
      其余文件仅统计报告, 不判失败 (由 §7.3 分期推进, 不属本次 C5 门禁)。

退出码: 0 = 全部通过 / 1 = 有失败。
"""
import os
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

_PASS, _FAIL = [], []

HEX = re.compile(r"#[0-9a-fA-F]{6}\b")
SPACING = re.compile(r"pad[xy]=\d+")

# C5 例外: 单一色板真源 / 浏览器 HTML (§5.2 C4 / §10.5)
C5_EXCLUDE = frozenset({"ui/theme.py", "netlink/webui.py"})

# C5 白名单: 已人工审查、允许保留非注释 #RRGGBB 的文件 → 锁定的字面量集合。
# 出现集合外的**新**字面量 → 失败; 删除字面量 → 允许 (可顺带把键删掉)。
COLOR_WHITELIST = {
    "ACRPA.py": frozenset({"#FFFFFF"}),
    "app.py": frozenset({"#000000", "#666666", "#B45309", "#FFFFFF"}),
    "core/color.py": frozenset({"#000000", "#1A1A1A", "#808080", "#FFFFFF"}),  # P1-1: 中性默认色常量
    "reminder.py": frozenset({"#0078D4", "#1F1F1F", "#333333", "#5F5F5F",
                              "#C42B1C", "#E1E1E1", "#FFFFFF"}),
    "utils.py": frozenset({"#1A1A1A", "#7C3AED", "#FFFFFF"}),
    "ui/bg_image.py": frozenset({"#FFFFFF"}),
    "ui/command_palette.py": frozenset({"#000000", "#0078D4", "#333333",
                                        "#5F5F5F", "#CCE4F7", "#E1E1E1", "#FFFFFF"}),
    "ui/netlink/remote.py": frozenset({"#718096"}),
    "ui/netlink/window.py": frozenset({"#0078D4", "#10B981", "#22C55E",
                                       "#718096", "#EF4444", "#F59E0B"}),
    "ui/param_form.py": frozenset({"#000000", "#0078D4", "#333333",
                                   "#FFF0F0", "#FFFFFF"}),
    "ui/window_shell.py": frozenset({"#0078D4", "#DC2626", "#E8E8E8", "#F5F5F5"}),
    "ui/workflow_view.py": frozenset({"#0078D4", "#0EA5E9", "#10B981", "#3B82F6",
                                      "#4A4A4A", "#64748B", "#6B7280", "#888888",
                                      "#8B5CF6", "#94A3B8", "#9CA3AF", "#CCE4F7",
                                      "#E1E1E1", "#E2E8F0", "#EEEEEE", "#EF4444",
                                      "#F59E0B", "#F8FAFC", "#FFFFFF"}),
}


def check(cond, msg, detail=""):
    (_PASS if cond else _FAIL).append(msg)
    tail = ("  ({})".format(detail) if detail else "")
    print("[{}] {}{}".format("OK  " if cond else "FAIL", msg, tail))


def strip_comments(line):
    """去掉注释后返回代码部分 (跟踪单/双引号, 字符串内的 # 保留)。"""
    out = []
    in_s = None
    i, n = 0, len(line)
    while i < n:
        c = line[i]
        if in_s:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(line[i + 1])
                i += 2
                continue
            if c == in_s:
                in_s = None
            i += 1
        else:
            if c in ("'", '"'):
                in_s = c
                out.append(c)
            elif c == "#":
                break
            else:
                out.append(c)
            i += 1
    return "".join(out)


def iter_py():
    for dirpath, _dirs, files in os.walk(SRC):
        for fn in files:
            if fn.endswith(".py"):
                yield os.path.join(dirpath, fn)


def t_color_whitelist():
    print("\n── C5 硬编码色白名单 ──")
    problems = []
    missing_note = []
    for path in iter_py():
        rel = os.path.relpath(path, SRC).replace("\\", "/")
        if rel in C5_EXCLUDE:
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                lines = fh.readlines()
        except Exception:
            continue
        found = set()
        for line in lines:
            code = strip_comments(line)
            if not code.strip():
                continue
            found.update(m.upper() for m in HEX.findall(code))
        if not found:
            if rel in COLOR_WHITELIST:
                missing_note.append(rel)  # 已清零, 白名单键可删 (不判失败)
            continue
        if rel not in COLOR_WHITELIST:
            problems.append("{} 非白名单文件新增硬编码色: {}".format(
                rel, ", ".join(sorted(found))))
            continue
        extra = found - COLOR_WHITELIST[rel]
        if extra:
            problems.append("{} 出现白名单外新字面量: {}".format(
                rel, ", ".join(sorted(extra))))
    check(not problems, "C5: src/**.py 无新增硬编码色 (除 theme.py/webui.py)",
          "; ".join(problems) if problems else "0 处新增")
    if missing_note:
        print("  (info) 已清零、白名单键可删除: {}".format(", ".join(missing_note)))


def t_spacing_lint():
    print("\n── D1 间距 lint ──")
    # 硬门禁: dialogs.py (P1-4 收口)
    dlg = os.path.join(SRC, "dialogs.py")
    dlg_hits = []
    if os.path.exists(dlg):
        with open(dlg, "r", encoding="utf-8", errors="replace") as fh:
            for idx, line in enumerate(fh, 1):
                if SPACING.search(line):
                    dlg_hits.append("L{} {}".format(idx, line.strip()))
    check(not dlg_hits, "D1: dialogs.py 无 pad[xy]=数字 (P1-4 收口为 sp()/gap())",
          "; ".join(dlg_hits) if dlg_hits else "0 处")

    # 其余文件: 仅报告 (§7.3 分期, 不判失败)
    report = []
    for path in iter_py():
        rel = os.path.relpath(path, SRC).replace("\\", "/")
        if rel == "dialogs.py":
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                n = sum(1 for line in fh if SPACING.search(line))
        except Exception:
            continue
        if n:
            report.append((rel, n))
    total = sum(n for _, n in report)
    print("  (report) 其余文件 pad[xy]=数字 共 {} 处, 分布在 {} 个文件 (不判失败)".format(
        total, len(report)))
    for rel, n in sorted(report, key=lambda x: -x[1]):
        print("    {:>4}  {}".format(n, rel))


def main():
    print("=== UI 防回归自测 (v4 §8.3 P2-3: C5 硬编码色 + 间距 lint) ===")
    t_color_whitelist()
    t_spacing_lint()
    print("\n=== 结果: {} (通过 {}, 失败 {}) ===".format(
        "FAIL" if _FAIL else "OK", len(_PASS), len(_FAIL)))
    if _FAIL:
        for m in _FAIL:
            print("  - {}".format(m))
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
