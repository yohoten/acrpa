# -*- coding: utf-8 -*-
"""ui.settings — 设置窗口拆分包 (路线图 §3.1 阶段二第 4 项)。

把原 src/settings_window.py 的 2200+ 行单函数 open_settings_window 拆为:

  · window.py    门面: open_settings_window 骨架 + 左侧导航 / 右侧滚动画布 /
                 关闭 / Esc + 统一即时保存框架 (_auto_save/_do_save/_flash_*/
                 _make_badge/_track_card_vars/_validate_number/_make_collapsible_card/
                 _register_nav_card ...) + 依赖注入 (init_ctx) + 主题刷新。
  · cards/*.py   11 张设置卡: 每卡导出 build(parent, ctx) -> handles /
                 apply(ctx, handles) -> None。

src/settings_window.py 退化为薄壳: 从本包 re-export 兼容面 (open_settings_window /
init_ctx / refresh_theme / update_sched_next_label) + 保留 ThemeBus 订阅
(`_ui_theme.subscribe(_on_theme_publish)`) 与动态转发 (__getattr__)。

依赖注入: 不 import ACRPA (沿用原模块 docstring 约束); 全部依赖经 init_ctx 注入。
"""
from ui.settings import window as _window
from ui.settings.window import (
    open_settings_window, init_ctx, refresh_theme, update_sched_next_label,
)

__all__ = ["open_settings_window", "init_ctx", "refresh_theme",
           "update_sched_next_label"]


def __getattr__(name):
    """把 _win / _prev_colors / _do_save / _apply_map / _last_error / _close 等
    动态转发到 window 模块 (包级 __getattr__, PEP 562), 使
    `ui.settings._win` 等始终取到最新值。
    """
    return getattr(_window, name)
