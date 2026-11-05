# -*- coding: utf-8 -*-
"""core.color — 颜色运算纯函数库 (P1-1, 借鉴 maliang `color/rgb.py`)。

统一收拢此前散落各处的颜色运算, 并补齐 maliang 的通用能力:
  · ``utils._darken``      → :func:`darken`     (乘系数变暗, 兼容原语义)
  · ``utils.rel_luminance``→ :func:`rel_luminance` (WCAG 相对亮度)
  · ``utils.text_on``      → :func:`text_on`    (按底色取可读前景)
  · ``flow_canvas.blend``  → :func:`blend`      (线性混色, 兼容原语义)
  · 补齐 maliang 语义: :func:`transition` (颜色过渡) / :func:`lighten` (提亮) /
    :func:`contrast` (反色) / :func:`hex_to_rgb` / :func:`rgb_to_hex` /
    :func:`rgb_to_hsl` / :func:`hsl_to_rgb` (P1-2 状态色派生备用)。

约定 (与本包 ``core/__init__.py`` 一致):
  * 纯函数、**不 import tkinter** —— 供 ``engine``/``workflow`` 在无 tkinter 解释器导入;
  * 输入输出均为 ``#RRGGBB`` (也接受 ``#RGB``); 非法输入**一律回退原值、绝不抛异常**。
"""

__all__ = (
    "hex_to_rgb", "rgb_to_hex",
    "blend", "transition", "darken", "lighten", "contrast",
    "rel_luminance", "text_on", "state_color",
    "rgb_to_hsl", "hsl_to_rgb",
)


def _parse(value):
    """``#RGB`` / ``#RRGGBB`` → ``(r, g, b)``; 无法解析返回 ``None`` (不抛异常)。"""
    if not isinstance(value, str):
        return None
    s = value.strip().lstrip("#")
    if len(s) == 3:
        s = "".join(ch * 2 for ch in s)
    if len(s) != 6:
        return None
    try:
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    except Exception:
        return None


def _fallback(color):
    """非法输入的回退值: 优先原值 (幂等), 否则中性黑。"""
    return color if isinstance(color, str) else "#000000"


def hex_to_rgb(value):
    """``#RGB``/``#RRGGBB`` → ``(r, g, b)``; 解析失败返回 ``None``。"""
    return _parse(value)


def rgb_to_hex(rgb):
    """``(r, g, b)`` → ``#RRGGBB`` (逐通道裁剪到 0..255)。"""
    try:
        r, g, b = rgb
        return "#{:02X}{:02X}{:02X}".format(
            max(0, min(255, int(round(r)))),
            max(0, min(255, int(round(g)))),
            max(0, min(255, int(round(b)))))
    except Exception:
        return "#000000"


def blend(color, other, ratio=0.5):
    """线性混色 (兼容 ``flow_canvas.blend``): result = color*(1-ratio) + other*ratio。

    Tk canvas 的 fill 不支持 alpha, 「40% 透明 / 淡化 30%」一律以本函数近似
    (P0-2 禁用态、P0-3 结构色)。ratio 自动裁剪到 [0, 1]; 非法输入回退 color 原值。
    """
    a, b = _parse(color), _parse(other)
    if a is None or b is None:
        return _fallback(color)
    try:
        t = max(0.0, min(1.0, float(ratio)))
    except Exception:
        t = 0.0
    return rgb_to_hex(tuple(a[i] * (1.0 - t) + b[i] * t for i in range(3)))


def transition(first, second, rate=0.5):
    """颜色过渡 (maliang ``rgb.transition`` 语义): first + (second-first)*rate。

    ``rate=0`` → first, ``rate=1`` → second; 等价于 ``blend(first, second, rate)``。
    """
    return blend(first, second, rate)


def darken(color, factor=0.15):
    """乘系数变暗 (兼容 ``utils._darken``): 每通道 ×(1-factor)。回退原值。"""
    a = _parse(color)
    if a is None:
        return _fallback(color)
    try:
        f = float(factor)
    except Exception:
        f = 0.15
    return rgb_to_hex(tuple(c * (1.0 - f) for c in a))


def lighten(color, factor=0.15):
    """向白混色提亮: 每通道向 ``#FFFFFF`` 混合 ``factor``。"""
    return blend(color, "#FFFFFF", factor)


def contrast(color):
    """反色 (maliang ``rgb.contrast``): 每通道取 255-v。回退原值。"""
    a = _parse(color)
    if a is None:
        return _fallback(color)
    return rgb_to_hex(tuple(255 - c for c in a))


def state_color(base, state, *, bg=None):
    """状态色派生 (P1-2): 由基础色统一派生交互态颜色, 替代逐处手写 ``darken``。

    state: normal | hover | active | pressed | down | disabled。
      · hover  → darken(base, 0.15)          (悬停微暗一格, 与既有语义按钮一致)
      · active/pressed/down → darken(base, 0.30)  (按下更深)
      · disabled → blend(base, bg, 0.618)    (黄金比例混到背景, maliang GOLDEN_RATIO 语义)
      · 其余/normal → base 原值
    ``bg`` 仅在 disabled 态使用; 缺省回退中性灰 ``#808080``。
    """
    if state == "hover":
        return darken(base, 0.15)
    if state in ("active", "pressed", "down"):
        return darken(base, 0.30)
    if state == "disabled":
        return blend(base, bg if bg else "#808080", 0.618)
    return base


def _srgb_to_lin(c):
    """sRGB 8bit 通道 → 线性光 (WCAG 定义)。"""
    c = c / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def rel_luminance(hex_color):
    """WCAG 相对亮度 (0=黑, 1=白); 解析失败返回 0.5 (中性)。"""
    rgb = _parse(hex_color)
    if rgb is None:
        return 0.5
    return (0.2126 * _srgb_to_lin(rgb[0]) + 0.7152 * _srgb_to_lin(rgb[1])
            + 0.0722 * _srgb_to_lin(rgb[2]))


def text_on(bg, light="#FFFFFF", dark="#1A1A1A"):
    """按背景相对亮度返回可读前景色 (WCAG 对比度更优者)。

    语义色跨主题会翻转明度 (亮色 ac=#0078D4 深底白字 / 暗色 ac=#4CC2FF 浅底黑字),
    因此语义底色上的文字色不能写死, 必须按底色计算。
    """
    return dark if rel_luminance(bg) > 0.45 else light


def rgb_to_hsl(rgb):
    """``(r, g, b)`` → ``(h, s, l)``, h∈[0,1] s∈[0,1] l∈[0,1]。"""
    r, g, b = (c / 255.0 for c in rgb)
    mx, mn = max(r, g, b), min(r, g, b)
    l = (mx + mn) / 2.0
    if mx == mn:
        return (0.0, 0.0, l)
    d = mx - mn
    s = d / (2.0 - mx - mn) if l > 0.5 else d / (mx + mn)
    if mx == r:
        h = (g - b) / d + (6.0 if g < b else 0.0)
    elif mx == g:
        h = (b - r) / d + 2.0
    else:
        h = (r - g) / d + 4.0
    return (h / 6.0, s, l)


def _hue2rgb(p, q, t):
    if t < 0.0:
        t += 1.0
    if t > 1.0:
        t -= 1.0
    if t < 1.0 / 6.0:
        return p + (q - p) * 6.0 * t
    if t < 1.0 / 2.0:
        return q
    if t < 2.0 / 3.0:
        return p + (q - p) * (2.0 / 3.0 - t) * 6.0
    return p


def hsl_to_rgb(hsl):
    """``(h, s, l)`` → ``(r, g, b)``, 输入 h/s/l∈[0,1]。"""
    h, s, l = hsl
    if s == 0.0:
        return (int(round(l * 255)),) * 3
    q = l * (1.0 + s) if l < 0.5 else l + s - l * s
    p = 2.0 * l - q
    return tuple(int(round(_hue2rgb(p, q, h + k) * 255))
                 for k in (1.0 / 3.0, 0.0, -1.0 / 3.0))
