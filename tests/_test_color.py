#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""颜色运算库 P1-1 回归自测: core.color 收拢 + 兼容 re-export。

用法:
    .venv\\Scripts\\python.exe -X utf8 tests\\_test_color.py

覆盖:
  A 解析/序列化 : hex_to_rgb / rgb_to_hex (#RGB/#RRGGBB 往返, 非法回退)。
  B 混色/过渡   : blend / transition (线性插值, ratio 裁剪, 非法回退原值)。
  C 明暗/反色   : darken / lighten / contrast。
  D 亮度/前景   : rel_luminance / text_on (WCAG)。
  E HSL         : rgb_to_hsl / hsl_to_rgb 往返 (P1-2 状态色派生备用)。
  F 兼容        : utils._darken / text_on / rel_luminance 与 flow_canvas.blend
                  仍可用且真源指向 core.color (re-export 不回退)。

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


import core.color as color  # noqa: E402


def t_parse():
    print("\n── A. hex_to_rgb / rgb_to_hex ──")
    check(color.hex_to_rgb("#000000") == (0, 0, 0), "hex_to_rgb #000000")
    check(color.hex_to_rgb("#FFFFFF") == (255, 255, 255), "hex_to_rgb #FFFFFF")
    check(color.hex_to_rgb("#FFF") == (255, 255, 255), "hex_to_rgb #FFF 三码展开")
    check(color.hex_to_rgb("#ff0000") == (255, 0, 0), "hex_to_rgb 大小写不敏感")
    check(color.hex_to_rgb("#GGGGGG") is None, "非法 hex 返回 None")
    check(color.hex_to_rgb(None) is None, "非字符串返回 None")
    check(color.rgb_to_hex((255, 0, 0)) == "#FF0000", "rgb_to_hex 大写输出")
    check(color.rgb_to_hex((300, -5, 127)) == "#FF007F", "rgb_to_hex 裁剪到 0..255")
    check(color.rgb_to_hex(color.hex_to_rgb("#3A7BD5")) == "#3A7BD5", "往返一致")


def t_blend_transition():
    print("\n── B. blend / transition ──")
    check(color.blend("#000000", "#FFFFFF", 0.5) == "#808080", "blend 中点=#808080")
    check(color.blend("#FFFFFF", "#000000", 0.0) == "#FFFFFF", "blend rate=0→原色")
    check(color.blend("#FFFFFF", "#000000", 1.0) == "#000000", "blend rate=1→目标色")
    check(color.blend("#000000", "#FFFFFF", 5.0) == "#FFFFFF", "blend ratio 上裁剪")
    check(color.blend("#000000", "#FFFFFF", -1.0) == "#000000", "blend ratio 下裁剪")
    check(color.blend("#GGGGGG", "#FFFFFF", 0.5) == "#GGGGGG", "blend 非法回退原值")
    check(color.transition("#000000", "#FFFFFF", 0.5) == "#808080", "transition 与 blend 同口径")
    check(color.transition("#000000", "#FFFFFF", 0.0) == "#000000", "transition rate=0")
    check(color.transition("#000000", "#FFFFFF", 1.0) == "#FFFFFF", "transition rate=1")


def t_darken_lighten_contrast():
    print("\n── C. darken / lighten / contrast ──")
    check(color.darken("#FFFFFF", 0.5) == "#808080", "darken 0.5=#808080")
    check(color.darken("#FFFFFF", 0.0) == "#FFFFFF", "darken factor=0→原色")
    check(color.darken("#000000") == "#000000", "darken 黑不变")
    check(color.darken("#GGGGGG") == "#GGGGGG", "darken 非法回退原值")
    check(color.lighten("#000000", 0.5) == "#808080", "lighten 0.5=#808080")
    check(color.lighten("#FFFFFF", 0.5) == "#FFFFFF", "lighten 白不变")
    check(color.contrast("#000000") == "#FFFFFF", "contrast 黑→白")
    check(color.contrast("#FFFFFF") == "#000000", "contrast 白→黑")
    check(color.contrast("#GGGGGG") == "#GGGGGG", "contrast 非法回退原值")


def t_luminance_text_on():
    print("\n── D. rel_luminance / text_on ──")
    check(abs(color.rel_luminance("#000000") - 0.0) < 1e-9, "rel_luminance 黑=0")
    check(abs(color.rel_luminance("#FFFFFF") - 1.0) < 1e-9, "rel_luminance 白=1")
    check(color.rel_luminance("#GGGGGG") == 0.5, "rel_luminance 非法=0.5 中性")
    check(color.text_on("#000000") == "#FFFFFF", "text_on 黑底→白字")
    check(color.text_on("#FFFFFF") == "#1A1A1A", "text_on 白底→深字")
    check(color.text_on("#0078D4") == "#FFFFFF", "text_on 亮语义色 ac→白字")
    check(color.text_on("#4CC2FF") == "#1A1A1A", "text_on 暗主题浅底→深字")


def t_hsl():
    print("\n── E. rgb_to_hsl / hsl_to_rgb ──")
    for rgb in [(255, 0, 0), (0, 255, 0), (0, 0, 255), (128, 128, 128),
                (255, 255, 255), (0, 0, 0), (200, 120, 45)]:
        h, s, l = color.rgb_to_hsl(rgb)
        back = color.hsl_to_rgb((h, s, l))
        ok = all(abs(back[i] - rgb[i]) <= 2 for i in range(3))
        check(ok, "hsl 往返 {}".format(rgb), "back={}".format(back))


def t_state_color():
    print("\n── G. state_color 状态色派生 (P1-2) ──")
    base = "#16A34A"
    check(color.state_color(base, "normal") == base, "normal=原值")
    check(color.state_color(base, "hover") == color.darken(base, 0.15), "hover=darken(base,0.15)")
    check(color.state_color(base, "active") == color.darken(base, 0.30), "active=darken(base,0.30)")
    check(color.state_color(base, "pressed") == color.darken(base, 0.30), "pressed=darken(base,0.30)")
    check(color.state_color(base, "disabled", bg="#FFFFFF") == color.blend(base, "#FFFFFF", 0.618),
          "disabled=blend(base,bg,0.618) 黄金比例")


def t_compat():
    print("\n── F. 兼容 re-export ──")
    try:
        import utils
        check(utils._darken is color.darken, "utils._darken 真源=core.color.darken")
        check(utils.rel_luminance is color.rel_luminance, "utils.rel_luminance 真源一致")
        check(utils.text_on is color.text_on, "utils.text_on 真源一致")
        check(utils._darken("#FFFFFF", 0.5) == "#808080", "utils._darken 行为正确")
    except Exception as e:
        warn("utils 导入失败, 跳过兼容: {}".format(e))
    try:
        from ui.flow_canvas import blend as fc_blend
        check(fc_blend is color.blend, "flow_canvas.blend 真源=core.color.blend")
        check(fc_blend("#000000", "#FFFFFF", 0.5) == "#808080", "flow_canvas.blend 行为正确")
    except Exception as e:
        warn("flow_canvas 导入失败, 跳过兼容: {}".format(e))


def main():
    print("=== 颜色运算库 P1-1: core.color 收拢 + 兼容 re-export ===")
    t_parse()
    t_blend_transition()
    t_darken_lighten_contrast()
    t_luminance_text_on()
    t_hsl()
    t_state_color()
    t_compat()

    print("\n" + "-" * 60)
    print("PASS={} FAIL={} WARN={}".format(len(_PASS), len(_FAIL), len(_WARN)))
    for m in _FAIL:
        print("[FAIL] {}".format(m))
    print("=== 结果: {} ===".format("FAIL" if _FAIL else "OK"))
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
