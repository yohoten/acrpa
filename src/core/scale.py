# -*- coding: utf-8 -*-
"""core.scale — 等比缩放语义统一 (P3-2, 借鉴 maliang ``Canvas.ratios = size/init_size``)。

把「设计尺寸 → 缩放系数 → 实际尺寸」这一套换算收拢成只读纯函数, 让
脚本编辑器 (字号偏移) 与工作流流程图 (乘子缩放) 共用同一份 clamp/step/fit 与
scaled 换算, 消除两套并列实现。

约定 (与 core 包一致): 本模块不得 import tkinter / tkinter.font; 只做浮点运算。

maliang 语义对照
----------------
    maliang:  Canvas.ratios = Canvas.size / Canvas.init_size   (逐轴缩放系数)
    core.scale: ratio_of(actual, design) == actual / design     (同一件事)

    maliang:  widget 几何一律 ``design * ratios[axis]``
    core.scale: scaled(design, ratio) == design * ratio

两处消费方的「缩放系数」表达虽不同 (流程图 = 乘子 0.4–2.5; 编辑器 = 字号偏移
-4..12pt 落盘为 state.EDITOR_ZOOM), 换算仍走同一处, 渲染结果不变:
    · 流程图: 实际尺寸 = scaled(设计尺寸, zoom)
    · 编辑器: 实际字号 = editor_zoom_size(base, offset) == scaled(base,
               editor_zoom_ratio(base, offset)) 再钳制 [6, 40]
"""

__all__ = [
    "ratio_of", "scaled", "clamp_scale", "step_scale", "fit_scale",
    "editor_zoom_ratio", "editor_zoom_size",
]


def ratio_of(actual, design):
    """实际尺寸 / 设计尺寸 → 缩放系数 (maliang ``ratios = size/init_size``)。

    设计尺寸趋近 0 时回退 1.0 (无缩放), 避免除零。
    """
    d = float(design)
    return (float(actual) / d) if abs(d) > 1e-12 else 1.0


def scaled(design, ratio):
    """设计尺寸 × 缩放系数 → 实际尺寸 (等比缩放核心换算)。"""
    return float(design) * float(ratio)


def clamp_scale(scale, lo, hi):
    """缩放系数钳制到 [lo, hi] (lo > hi 时自动交换)。"""
    scale, lo, hi = float(scale), float(lo), float(hi)
    if lo > hi:
        lo, hi = hi, lo
    return max(lo, min(hi, scale))


def step_scale(scale, step, up=True):
    """按步进乘子缩放: ``up`` → ×step, 否则 ÷step。

    ``step`` 为放大乘子 (如 1.12 / 1.25), 反向步进等价于 ×(1/step)。
    """
    step = float(step)
    return float(scale) * (step if up else (1.0 / step))


def fit_scale(view_w, view_h, content_w, content_h, margin=0.0):
    """视口 / 内容 的最小等比缩放系数 (适应窗口)。

    边距 ``margin`` 从视口两侧各扣一次; 视口/内容均以 1.0 为下界, 避免除零与负值。
    """
    vw = max(float(view_w) - 2.0 * float(margin), 1.0)
    vh = max(float(view_h) - 2.0 * float(margin), 1.0)
    return min(vw / max(float(content_w), 1.0),
               vh / max(float(content_h), 1.0))


def editor_zoom_ratio(base_size, offset):
    """编辑器「字号偏移」→ 等比缩放系数: ``(base + offset) / base``。

    编辑器把倍率持久化为整数点偏移 ``state.EDITOR_ZOOM`` (0 = 跟随全局 ui_scale),
    这里把该偏移折算成与流程图同口径的缩放系数, 供 ``scaled`` 使用。
    """
    b = float(base_size)
    return (b + float(offset)) / b if abs(b) > 1e-12 else 1.0


def editor_zoom_size(base_size, offset, lo=6, hi=40):
    """编辑器实际字号 = ``scaled(base, editor_zoom_ratio(base, offset))`` 并钳制 [lo, hi]。

    与历史实现 ``clamp(base_size + offset, 6, 40)`` 逐位等价 (round 吸收浮点尾差)。
    """
    s = scaled(base_size, editor_zoom_ratio(base_size, offset))
    s = int(round(s))
    return max(int(lo), min(int(hi), s))
