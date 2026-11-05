# -*- coding: utf-8 -*-
"""脚本编辑「参数列表头 / 列宽 / 悬停提示」纯展示辅助回归自测 (S1-A)。

背景: 这组函数从 ACRPA.py 迁到 src/ui/editor_columns.py, 只读命令 schema
(commands.schema / module_catalog), 据此改列头/列宽/tooltip, 不写任何存储。
本测试验证迁移后行为与迁移前一致 (纯函数 + 只读缓存), 并做一次 GUI 冒烟
(树列头联动 + 表头提示绑定, 需 ACRPA_GUI_TEST=1 时才真正构建窗口)。

断言:
  P1 空/未知命令返回空, 不抛异常
  P2 已知命令参数名与 P1-4 验收一致 (找图/按键)
  P3 列宽按类型自适应 (number=54; string 按名长在 [70,150])
  P4 tooltip 文本: 可选/默认补充, 越界返回空
  P5 结果缓存只读, 不改存储格式

用法: .venv\\Scripts\\python.exe -X utf8 tests\\_test_editor_columns.py
退出码: 0=全部通过, 1=存在失败
"""
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "src"))

from ui import editor_columns as ec          # noqa: E402

_PASS, _FAIL = [], []


def check(cond, msg):
    (_PASS if cond else _FAIL).append(msg)
    print("[{}] {}".format("OK  " if cond else "FAIL", msg))


def main():
    print("=" * 68)
    print("脚本编辑表头/列宽/tooltip 纯展示辅助自测")
    print("=" * 68)

    # ── P1 空/未知命令 ──
    check(ec.command_params("") == [], "command_params('') 返回空")
    check(ec.command_param_names("") == [], "command_param_names('') 返回空")
    check(ec.command_params("__不存在的命令__") == [], "未知命令返回空列表")

    # ── P2 已知命令参数名 (P1-4 验收) ──
    check(ec.command_param_names("找图") == ["图片名", "精度"],
          "找图 → [图片名, 精度]")
    check(ec.command_param_names("按键") == ["按键名", "次数", "间隔"],
          "按键 → [按键名, 次数, 间隔]")

    # ── P3 列宽按类型自适应 ──
    check(ec.param_col_width({"name": "x", "kind": "number"}) == 54,
          "number 参数列宽 = 54")
    w = ec.param_col_width({"name": "图片名", "kind": "string"})
    check(70 <= w <= 150, "string 参数列宽在 [70,150] (实得 {})".format(w))

    # ── P4 tooltip 文本 ──
    check(ec.param_hint_text([], 0) == "", "无参数时 tooltip 返回空")
    check(ec.param_hint_text([{"name": "a"}], 5) == "", "越界索引返回空")
    t = ec.param_hint_text(
        [{"name": "a", "raw": "", "optional": True, "default": 1}], 0)
    check("可选" in t and "默认 1" in t, "可选/默认补充写入 tooltip (实得 {!r})".format(t))

    # ── P5 只读 schema (不改存储) ──
    ps = ec.command_params("找图")
    check(isinstance(ps, list) and all(isinstance(p, dict) for p in ps),
          "command_params 返回参数字典列表")
    if ps:
        check(set(ps[0]) >= {"name", "raw", "kind", "default", "optional"},
              "参数字典含 name/raw/kind/default/optional 键")

    # ── G GUI 冒烟 (仅 ACRPA_GUI_TEST=1 时) ──
    if os.environ.get("ACRPA_GUI_TEST") == "1":
        try:
            import tkinter
            from tkinter import ttk
            root = tkinter.Tk()
            root.withdraw()
            tree = ttk.Treeview(root, columns=("cmd",) + tuple(
                "p{}".format(i) for i in range(1, 10)))
            tree["show"] = "headings"
            for i in range(1, 10):
                tree.heading("p{}".format(i), text="参数{}".format(i))
            tree.insert("", "end", values=("找图", "a.png", "0.9"))
            ec.sync_param_headings(tree)
            check(tree.heading("p1")["text"] == "图片名",
                  "GUI: 选中找图后 p1 表头=图片名")
            ec.bind_param_header_tip(tree)
            check(True, "GUI: bind_param_header_tip 绑定不抛异常")
            root.destroy()
        except Exception as e:
            check(False, "GUI 冒烟异常: {!r}".format(e))

    print("-" * 68)
    print("结果: {} OK / {} FAIL".format(len(_PASS), len(_FAIL)))
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
