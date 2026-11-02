# -*- coding: utf-8 -*-
"""core.util — 通用纯函数 (无 tkinter 依赖)。

从 ``src/utils.py`` 迁出, 供 ``engine`` 等领域层在无 tkinter 解释器里导入。
"""


def _safe_get(val, default, converter=None):
    """Safely extract a typed value from an Excel cell."""
    if val is None: return default
    if isinstance(val, str):
        try:
            if converter: return converter(val)
        except ValueError: return default
        return val if converter is None else default
    try:
        if converter: return converter(val)
        return val
    except (ValueError, TypeError): return default
