# -*- coding: utf-8 -*-
"""ui.widgets — 自绘控件包 (P3-1 起, 借鉴 maliang 自绘控件)。

目前仅含 rounded_button (Canvas 自绘圆角按钮/开关)。新控件按需在此扩展,
一律走「自绘 Canvas + state_color 派生 + Animation 动效 + _acrpa_redraw 换肤钩子」。
"""

from ui.widgets.rounded_button import (  # noqa: F401
    RoundedButton,
    RoundedSwitch,
    rounded_polygon,
)

__all__ = ["RoundedButton", "RoundedSwitch", "rounded_polygon"]
