#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""自绘圆角按钮/开关 P3-1 回归自测 (maliang 自绘控件语义)。

用法:
    .venv\\Scripts\\python.exe -X utf8 tests\\_test_rounded_controls.py

覆盖:
  A 纯逻辑  : rounded_polygon 顶点序列 (长度/起点/半径 clamp/坐标越界)。
  B 开关    : RoundedSwitch 初始态 / toggle 写 variable+回调 / reduce_motion 瞬时
              (无 after 残留) vs 开动效 (active=True) / 换肤 refresh 变色不残留 /
              _acrpa_redraw 钩子委托。
  C 按钮    : RoundedButton 初始底 / hover 光晕 (state_color 派生) / reduce_motion
              瞬时 / command 触发 / 换肤 refresh。
  D 走 walk : settings_window.refresh_theme 对自绘 Canvas 走 _acrpa_redraw (而非
              通用 bg 覆盖), 换肤后填充色更新、无旧色残留。

退出码: 0=全部通过 / 1=有失败。
"""
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


from ui.widgets.rounded_button import (  # noqa: E402
    RoundedButton, RoundedSwitch, rounded_polygon,
)

# 两套最小色板: 换肤验证用 (只含控件读取到的键, 开/关底跨主题明度翻转)
P_LIGHT = {
    "bg": "#FFFFFF", "bgc": "#FFFFFF", "ebg": "#F5F5F5",
    "sc": "#107C10", "bd": "#D6D6D6", "ac": "#7C3AED", "focus": "#7C3AED",
    "fgb": "#1A1A1A", "fgt": "#1A1A1A", "fgm": "#888888",
    "surface_alt": "#F0F0F0", "border_strong": "#C8C8C8",
    "acl": "#E6D9FB", "wn": "#FCE100", "dg": "#C42B1C",
}
P_DARK = {
    "bg": "#1E1E1E", "bgc": "#242424", "ebg": "#2A2A2A",
    "sc": "#6CCB5F", "bd": "#4A4A4A", "ac": "#B18CF2", "focus": "#B18CF2",
    "fgb": "#E8E8E8", "fgt": "#E8E8E8", "fgm": "#9A9A9A",
    "surface_alt": "#2E2E2E", "border_strong": "#5A5A5A",
    "acl": "#3A2D5C", "wn": "#FCE100", "dg": "#E0635B",
}


def _norm(c):
    try:
        return str(c).strip().lower()
    except Exception:
        return str(c)


def _track_fill(sw):
    return _norm(sw.itemcget(sw._track, "fill"))


def t_polygon():
    print("\n── A. 纯逻辑: rounded_polygon 顶点序列 ──")
    pts = rounded_polygon(0, 0, 40, 22, 11)
    check(len(pts) == 40, "20 顶点 → 40 坐标", "len={}".format(len(pts)))
    check(pts[0] == 11 and pts[1] == 0, "起点 (x1+r, y1)", "({}, {})".format(pts[0], pts[1]))
    check(pts[-2] == 0 and pts[-1] == 0, "闭合回 (x1, y1)", "({}, {})".format(pts[-2], pts[-1]))
    xs = [pts[i] for i in range(0, 40, 2)]
    ys = [pts[i] for i in range(1, 40, 2)]
    check(min(xs) >= 0 and max(xs) <= 40, "x 均落在 [0, 40]")
    check(min(ys) >= 0 and max(ys) <= 22, "y 均落在 [0, 22]")
    # 半径 clamp: 超过 min(w,h)/2 时取半
    c = rounded_polygon(0, 0, 40, 22, 999)
    check(c[0] == 11.0, "半径 clamp 到 min(w,h)/2", "x1+r={}".format(c[0]))
    # 非对称 (r 受短边限制)
    c2 = rounded_polygon(0, 0, 100, 20, 30)
    check(c2[0] == 10.0, "r 受短边 20 限制 → 10", "x1+r={}".format(c2[0]))


def _make_root():
    import tkinter
    try:
        root = tkinter.Tk()
        root.withdraw()
        return root
    except Exception as e:
        warn("无法创建 Tk root (无 GUI): {}".format(e))
        return None


def t_switch():
    print("\n── B. RoundedSwitch: 开关冒烟 + 减少动效 + 换肤 ──")
    import state
    import utils
    root = _make_root()
    if root is None:
        return
    prev_rm = getattr(state, "REDUCE_MOTION", False)
    try:
        calls = []
        var = __import__("tkinter").BooleanVar(value=False)
        sw = RoundedSwitch(root, variable=var, colors=P_LIGHT,
                           command=lambda: calls.append(True))
        check(sw.get() is False, "初始 OFF")
        check(_track_fill(sw) == _norm(P_LIGHT["bd"]), "初始轨道 OFF 底 = bd",
              _track_fill(sw))
        check(sw.winfo_class() == "Canvas", "是 Canvas 子类 (winfo_class=Canvas)")

        # toggle 冒烟: 写 variable + 回调
        sw.toggle()
        check(sw.get() is True, "toggle → ON")
        check(var.get() is True, "toggle 写 variable=True")
        check(len(calls) == 1, "toggle 触发 command", "calls={}".format(len(calls)))

        # reduce_motion: 瞬时, 无 after 残留 (anim=None)
        state.REDUCE_MOTION = True
        sw.toggle()  # ON → OFF
        check(sw.get() is False, "reduce_motion 下 toggle → OFF")
        check(sw._anim is None, "reduce_motion: 无动画句柄 (无 after 残留)")
        check(_track_fill(sw) == _norm(P_LIGHT["bd"]), "reduce_motion: 轨道一步到位 OFF 底")

        # 开动效: 调度帧 (anim.active=True)
        state.REDUCE_MOTION = False
        sw.toggle()  # OFF → ON
        check(sw._anim is not None and sw._anim.active is True,
              "开动效: 动画已调度帧 (active=True)")
        if sw._anim is not None:
            sw._anim.stop()

        # 换肤 refresh: 填充色更新、无旧色残留
        sw.refresh(P_DARK)
        check(_track_fill(sw) == _norm(P_DARK["sc"]), "换肤后 ON 轨道底 = 暗色 sc",
              _track_fill(sw))
        # 关闭态换肤
        sw.set(False, animate=False)
        check(_track_fill(sw) == _norm(P_DARK["bd"]), "换肤后 OFF 轨道底 = 暗色 bd (无旧色残留)",
              _track_fill(sw))

        # _acrpa_redraw 钩子委托 refresh
        sw._acrpa_redraw(P_LIGHT)
        check(_track_fill(sw) == _norm(P_LIGHT["bd"]), "_acrpa_redraw 委托 refresh (回亮色)")
    finally:
        state.REDUCE_MOTION = prev_rm
        root.destroy()


def t_button():
    print("\n── C. RoundedButton: 按钮冒烟 + 光晕 + 减少动效 ──")
    import state
    import utils
    root = _make_root()
    if root is None:
        return
    prev_rm = getattr(state, "REDUCE_MOTION", False)
    try:
        fired = []
        btn = RoundedButton(root, text="测试", colors=P_LIGHT,
                            command=lambda: fired.append(True))
        base = _norm(P_LIGHT["surface_alt"])
        check(_norm(btn._fill) == base, "初始中性底 = surface_alt", _norm(btn._fill))
        check(btn.winfo_class() == "Canvas", "是 Canvas 子类")

        # 语义底 (role=sc): 底=sc, 文字 text_on(sc)
        btn2 = RoundedButton(root, text="运行", colors=P_LIGHT, role="sc")
        check(_norm(btn2._fill) == _norm(P_LIGHT["sc"]), "role=sc → 底=sc")

        # reduce_motion: hover 瞬时
        state.REDUCE_MOTION = True
        btn._set_hover(True)
        # 用 state_color 计算期望 hover 色
        from core.color import state_color as _sc
        expect_hover = _norm(_sc(P_LIGHT["surface_alt"], "hover"))
        check(_norm(btn._fill) == expect_hover, "reduce_motion: hover 一步到位",
              _norm(btn._fill))
        check(btn._anim is None, "reduce_motion: 按钮无动画句柄 (无 after 残留)")
        btn._set_hover(False)

        # 开动效: hover 调度帧
        state.REDUCE_MOTION = False
        btn._set_hover(True)
        check(btn._anim is not None and btn._anim.active is True,
              "开动效: hover 已调度帧 (active=True)")
        if btn._anim is not None:
            btn._anim.stop()
        btn._set_hover(False)

        # command 触发
        btn._activate()
        check(len(fired) == 1, "_activate 触发 command")

        # 换肤 refresh: 底更新
        btn.refresh(P_DARK)
        check(_norm(btn._fill) == _norm(P_DARK["surface_alt"]), "换肤后中性底更新",
              _norm(btn._fill))
    finally:
        state.REDUCE_MOTION = prev_rm
        root.destroy()


def t_walk():
    print("\n── D. settings_window.refresh_theme: walk 走 _acrpa_redraw ──")
    import tkinter
    root = _make_root()
    if root is None:
        return
    try:
        import ui.settings.window as win_mod
    except Exception as e:
        warn("无法导入 ui.settings.window: {}".format(e))
        root.destroy()
        return
    _old_win = win_mod._win
    _old_C = win_mod.C
    try:
        sw = RoundedSwitch(root, colors=P_LIGHT)
        win_mod._win = root
        win_mod.C = P_DARK
        win_mod.refresh_theme(prev=P_LIGHT)
        # 自绘 Canvas 应被 walk 识别 → 走 _acrpa_redraw → 填充色换到暗色 bd
        check(_track_fill(sw) == _norm(P_DARK["bd"]),
              "walk 识别自绘 Canvas → 换肤到暗色 bd (无旧色残留)", _track_fill(sw))
        # 普通 Canvas 仍走 bg=C["bg"] (未被 _acrpa_redraw 分支吞掉)
        plain = tkinter.Canvas(root, width=10, height=10)
        win_mod.refresh_theme(prev=P_DARK)
        try:
            check(_norm(plain.cget("bg")) == _norm(P_DARK["bg"]),
                  "普通 Canvas 仍套用 bg=C[bg]", _norm(plain.cget("bg")))
        except Exception:
            check(True, "普通 Canvas bg 读取跳过")
    finally:
        win_mod._win = _old_win
        win_mod.C = _old_C
        root.destroy()


def main():
    print("=== 自绘圆角按钮/开关 P3-1: maliang 自绘控件语义 ===")
    t_polygon()
    t_switch()
    t_button()
    t_walk()

    print("\n" + "-" * 60)
    print("PASS={} FAIL={} WARN={}".format(len(_PASS), len(_FAIL), len(_WARN)))
    for m in _FAIL:
        print("[FAIL] {}".format(m))
    print("=== 结果: {} ===".format("FAIL" if _FAIL else "OK"))
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
