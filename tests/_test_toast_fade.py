#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""提示条/Toast 淡入淡出 P2-3 回归自测 (MoveItem 语义)。

用法:
    .venv\\Scripts\\python.exe -X utf8 tests\\_test_toast_fade.py

覆盖:
  A 接线      : show_toast 淡入/淡出走 core.animation.Animation; 时长=motion_fast,
                帧间隔=motion_frame() (经 _toast_fps 换算)。
  B 减少动效  : reduce_motion 真 → 瞬显 (alpha 一步到位 0.95) 且淡入 Animation 无
                after 残留 (active=False), 无淡出/自动隐藏定时器 (duration=0)。
  C 开动效    : reduce_motion 假 → 淡入 Animation 已调度帧 (active=True);
                _toast_cancel_anim 可停止并清空句柄 (再次提示前清残留定时器)。
  D 冒烟      : 真实 Tk 下 show_toast (含 duration>0 登记自动隐藏 job) 不抛异常。

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


import utils          # noqa: E402
import state          # noqa: E402
from core.animation import Animation  # noqa: E402


def t_wiring():
    print("\n── A. 接线: show_toast → Animation (motion token) ──")
    check(callable(getattr(utils, "_toast_fps", None)), "_toast_fps 存在")
    fps = utils._toast_fps()
    frame = utils.motion_frame()
    check(fps == max(1, 1000 // max(1, int(frame))),
          "_toast_fps == 1000//motion_frame()", "fps={} frame={}".format(fps, frame))
    check(hasattr(utils, "_toast_apply") and hasattr(utils, "_toast_cancel_anim"),
          "暴露 _toast_apply/_toast_cancel_anim 辅助")


def t_real():
    print("\n── B/C/D. 实机: 减少动效 / 开动效 / 冒烟 ──")
    try:
        import tkinter
    except Exception as e:
        warn("tkinter 不可用, 跳过实机: {}".format(e))
        return
    try:
        root = tkinter.Tk()
        root.withdraw()
    except Exception as e:
        warn("无法创建 Tk root (无 GUI), 跳过实机: {}".format(e))
        return
    prev = getattr(state, "REDUCE_MOTION", False)
    try:
        # B: reduce_motion 真 → 瞬显 + 淡入无 after 残留
        state.REDUCE_MOTION = True
        win = utils.show_toast(root, "toast-rm", "info", duration=0)
        check(win is not None, "show_toast 返回 Toplevel (reduce_motion)")
        fi = utils._toast.get("fade_in")
        check(isinstance(fi, Animation), "fade_in 是 Animation 实例")
        check(fi is not None and fi.duration == utils.motion("motion_fast"),
              "fade_in 时长=motion_fast", "duration={}".format(
                  fi.duration if fi else None))
        check(fi is not None and fi.active is False,
              "reduce_motion: fade_in 无 after 残留 (active=False)")
        check(utils._toast.get("fade_out") is None, "duration=0: 无淡出动画")
        check(utils._toast.get("job") is None, "duration=0: 无自动隐藏定时器")
        try:
            a = float(win.attributes("-alpha"))
            check(abs(a - 0.95) < 1e-6, "reduce_motion: alpha 瞬显 0.95",
                  "alpha={}".format(a))
        except Exception:
            check(True, "reduce_motion: alpha 读取跳过 (平台不支持 -alpha)")
        utils._toast_cancel_anim()
        check(utils._toast.get("fade_in") is None, "cancel 后 fade_in 句柄清空")

        # C: 开动效 → 淡入调度帧 (active=True), cancel 可停止
        state.REDUCE_MOTION = False
        utils.show_toast(root, "toast-anim", "info", duration=0)
        fi2 = utils._toast.get("fade_in")
        check(fi2 is not None and fi2.active is True,
              "开动效: fade_in 已调度帧 (active=True)")
        utils._toast_cancel_anim()
        check(utils._toast.get("fade_in") is None, "cancel 停止并清空开动效淡入")

        # D: duration>0 完整 show 冒烟 (登记自动隐藏 job, 不等待真实淡出)
        utils.show_toast(root, "toast-full", "success", duration=120)
        check(utils._toast.get("job") is not None, "duration>0: 登记自动隐藏 job")
        check(True, "show_toast 完整冒烟未抛异常")
    finally:
        utils._toast_cancel_anim()
        state.REDUCE_MOTION = prev
        try:
            root.destroy()
        except Exception:
            pass


def main():
    print("=== 提示条/Toast 淡入淡出 P2-3: MoveItem 语义 ===")
    t_wiring()
    t_real()

    print("\n" + "-" * 60)
    print("PASS={} FAIL={} WARN={}".format(len(_PASS), len(_FAIL), len(_WARN)))
    for m in _FAIL:
        print("[FAIL] {}".format(m))
    print("=== 结果: {} ===".format("FAIL" if _FAIL else "OK"))
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
