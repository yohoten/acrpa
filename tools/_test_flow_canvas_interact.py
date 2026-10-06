#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""工作流图连线交互 + 静态分析面板 回归自测 (路线图 §11.7 / §11.8, D–E 期收尾)。

用法:
    .venv\\Scripts\\python.exe -X utf8 tools\\_test_flow_canvas_interact.py

覆盖:
  A  静态导出 : ui.flow_canvas 暴露 bind_interactions/fit_to_view/port_at/node_at/edge_at;
               import flow_graph + ui.flow_canvas 不引入 tkinter (纯度护栏);
               flow_graph 暴露 analyze/connect/disconnect/delete_node/can_connect。
  B  静态接线 : src/ui/workflow_view.py 文本含 bind_interactions( / _flow_graph.analyze( /
               wf_flow_check_lbl / "show_ports": True。
  C  实机冒烟 : 建 Tk (withdraw) → promote 图 → render (show_ports=True) → 断言存在 port: 前缀
               标签; bind_interactions / fit_to_view 不抛异常。无 GUI 环境记 [WARN] (不判失败)。

退出码: 0=全部通过 / 1=有失败。
"""
import io
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

_PASS, _FAIL, _WARN = [], [], []


def check(cond, msg, detail=""):
    (_PASS if cond else _FAIL).append(msg)
    tail = ("  ({})".format(detail) if detail else "")
    print("[{}] {}{}".format("OK  " if cond else "FAIL", msg, tail))


def warn(msg):
    _WARN.append(msg)
    print("[WARN] {}".format(msg))


def _read(rel):
    with io.open(os.path.join(ROOT, rel), "r", encoding="utf-8") as f:
        return f.read()


import flow_graph as fg          # noqa: E402
from ui import flow_canvas as fc  # noqa: E402


# ── A. 静态导出 / 纯度 ───────────────────────────────────────────────
def t_static_exports():
    print("\n── A. 静态导出 / 纯度 ──")
    for name in ("bind_interactions", "fit_to_view", "port_at", "node_at", "edge_at"):
        check(hasattr(fc, name), "ui.flow_canvas 暴露 {}".format(name))
    for name in ("analyze", "connect", "disconnect", "delete_node", "can_connect"):
        check(hasattr(fg, name), "flow_graph 暴露 {}".format(name))
    check("tkinter" not in sys.modules,
          "import flow_graph + ui.flow_canvas 未引入 tkinter (纯度护栏)")


# ── B. 静态接线 (workflow_view 文本) ─────────────────────────────────
def t_static_wiring():
    print("\n── B. 静态接线 (workflow_view) ──")
    wv = _read("src/ui/workflow_view.py")
    check("bind_interactions(" in wv, "workflow_view 调用 bind_interactions(")
    check("_flow_graph.analyze(" in wv, "workflow_view 调用 _flow_graph.analyze(")
    check("wf_flow_check_lbl" in wv, "workflow_view 引用 wf_flow_check_lbl")
    check('"show_ports": True' in wv, 'workflow_view ctx 打开 "show_ports": True')


# ── C. 实机冒烟 (WARN 守卫) ──────────────────────────────────────────
def _has_port_tag(canvas):
    try:
        items = canvas.find_all()
    except Exception:
        return False
    for it in items:
        try:
            for t in canvas.gettags(it):
                if str(t).startswith("port:"):
                    return True
        except Exception:
            continue
    return False


def t_real_smoke():
    print("\n── C. 实机冒烟 (render + 交互) ──")
    try:
        import tkinter
    except Exception as e:
        warn("tkinter 不可用, 跳过实机冒烟: {}".format(e))
        return
    root = None
    try:
        root = tkinter.Tk()
        root.withdraw()
    except Exception as e:
        warn("无法创建 Tk root (无 GUI 环境), 跳过实机冒烟: {}".format(e))
        try:
            if root is not None:
                root.destroy()
        except Exception:
            pass
        return
    try:
        canvas = tkinter.Canvas(root, width=520, height=360)
        canvas.pack()

        wf = {"name": "t", "steps": [
            {"type": "log", "text": "a"},
            {"type": "wait", "seconds": 1},
            {"type": "log", "text": "b"},
        ]}
        g = fg.promote(wf)

        fonts = {"body": ("Segoe UI", 9), "small": ("Segoe UI", 8),
                 "small_bold": ("Segoe UI", 8, "bold")}
        colors = {"log": "#22C55E", "wait": "#9CA3AF"}
        ctx = {"show_ports": True, "fgm": "#888"}

        try:
            fc.render(canvas, g, colors=colors, fonts=fonts, ctx=ctx)
            check(True, "render(show_ports=True) 未抛异常")
        except Exception as e:
            check(False, "render(show_ports=True) 抛异常: {}".format(e))

        check(_has_port_tag(canvas), "画布存在 port: 前缀标签 (端口已渲染)")

        try:
            fc.bind_interactions(canvas, g, colors=colors, fonts=fonts, ctx=ctx)
            check(True, "bind_interactions 未抛异常")
        except Exception as e:
            check(False, "bind_interactions 抛异常: {}".format(e))

        try:
            fc.fit_to_view(canvas)
            check(True, "fit_to_view 未抛异常")
        except Exception as e:
            check(False, "fit_to_view 抛异常: {}".format(e))
    finally:
        try:
            root.destroy()
        except Exception:
            pass


def main():
    print("=== 工作流图连线交互 + 静态分析面板 回归自测 (路线图 §11.7/§11.8) ===")
    t_static_exports()
    t_static_wiring()
    t_real_smoke()

    print("\n" + "-" * 60)
    print("PASS={} FAIL={} WARN={}".format(len(_PASS), len(_FAIL), len(_WARN)))
    for m in _FAIL:
        print("[FAIL] {}".format(m))
    print("=== 结果: {} ===".format("FAIL" if _FAIL else "OK"))
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
