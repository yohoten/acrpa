# -*- coding: utf-8 -*-
"""设备互联窗口 - 薄壳 re-export。

L1-A 拆分后，真正的实现位于 `ui/netlink/` 包（window.py 门面 + 7 个方法组 mixin）。
本模块仅为兼容宿主 (ACRPA.py) 与既有 smoke 脚本的导入面，不包含业务逻辑。
保留 `def refresh_theme` 字面量以满足既有静态断言 (G7)。
"""
from __future__ import annotations

from ui.netlink import window as _window

NetLinkWindow = _window.NetLinkWindow
open_netlink_window = _window.open_netlink_window
is_open = _window.is_open
close_window = _window.close_window


def refresh_theme(prev=None):
    """主题切换时重刷窗口配色（转发到 ui.netlink.window）。"""
    _window.refresh_theme()


def __getattr__(name):
    """未显式 re-export 的名称，回退到 ui.netlink 包（薄壳动态转发）。"""
    if name.startswith("__") and name.endswith("__"):
        raise AttributeError(name)
    import ui.netlink as _pkg
    try:
        return getattr(_pkg, name)
    except AttributeError:
        raise AttributeError(
            "module 'netlink_window' has no attribute {!r}".format(name)
        ) from None


__all__ = [
    "NetLinkWindow",
    "open_netlink_window",
    "is_open",
    "refresh_theme",
    "close_window",
]
