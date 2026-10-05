# -*- coding: utf-8 -*-
"""settings_window.py — 独立设置窗口 (薄壳 / 兼容面)。

路线图 §3.1 阶段二第 4 项: 原 2809 行单函数实现已拆分:
  · 门面 + 保存框架 + 导航/画布/关闭/Esc  → src/ui/settings/window.py
  · 11 张设置卡 (build/apply)              → src/ui/settings/cards/*.py

本文件退化为**薄壳**, 只做:
  1. re-export 兼容面: open_settings_window / init_ctx / refresh_theme /
     update_sched_next_label (供 ACRPA.py 与 tools 沿用 `settings_window.X`);
  2. ThemeBus 订阅 (保留 `_ui_theme.subscribe(_on_theme_publish)` 与
     `def refresh_theme(prev=None):` 字面量, 满足既有静态断言);
  3. 经 __getattr__ 动态转发其余属性 (_win / _prev_colors / _do_save /
     _apply_map / _last_error / _close / ...) 到 ui.settings.window。

依赖注入设计 (不变):
    不 import ACRPA (避免循环导入)，ACRPA.py 启动时调用 init_ctx() 注入
    root / C / FONT / 回调。主题切换时 ThemeBus 一次 publish 触发 _on_theme_publish,
    本模块同步色表并调用 refresh_theme() 递归刷新已打开窗口的颜色。
"""
import os, sys  # noqa: F401  (保留原模块导入面, 供既有静态断言/外部按名引用)

from ui import theme as _ui_theme
from ui import settings as _settings

__all__ = [
    "open_settings_window", "init_ctx", "refresh_theme",
    "update_sched_next_label",
]


def init_ctx(root_win=None, colors=None, fonts=None, app_root="", tlog=None,
             ensure_tray=None, destroy_tray=None, toggle_fold=None,
             main_run=None):
    """注入 ACRPA.py 提供的依赖 (模块启动时调用一次)。

    转调 ui.settings.init_ctx 后订阅 ThemeBus —— 主题切换时由 _refresh_theme
    一次 publish 触发本模块 _on_theme_publish, 不再被 ACRPA 具名调用。
    """
    _settings.init_ctx(root_win=root_win, colors=colors, fonts=fonts,
                       app_root=app_root, tlog=tlog, ensure_tray=ensure_tray,
                       destroy_tray=destroy_tray, toggle_fold=toggle_fold,
                       main_run=main_run)
    _ui_theme.subscribe(_on_theme_publish)


def _on_theme_publish(dark=None, colors=None, prev=None):
    """ThemeBus 订阅回调: 转发到 ui.settings.window.on_theme_publish。

    取代 ACRPA._refresh_theme 里的 `settings_window.C = C` + `refresh_theme(_prev)`
    两行手工扇出 (路线图 §5.2 修法③)。
    """
    return _settings.on_theme_publish(dark, colors, prev)


def refresh_theme(prev=None):
    """主题切换后递归刷新已打开设置窗口的颜色 (实现迁至 ui.settings.window)。

    prev: 旧主题色表快照 (由 ThemeBus 显式传入)。语义按钮回填以 prev∪C 判定。
    """
    return _settings.refresh_theme(prev)


def update_sched_next_label(text):
    """供主程序 _periodic 刷新: 设置窗口已打开时更新「下次执行」标签。"""
    return _settings.update_sched_next_label(text)


def open_settings_window():
    """打开独立设置窗口；若已打开则聚焦 (实现在 ui.settings.window)。"""
    return _settings.open_settings_window()


def __getattr__(name):
    """动态转发其余属性到 ui.settings 包 (进而到 window 模块)。

    使 `settings_window._win` / `_prev_colors` / `_do_save` / `_apply_map` /
    `_last_error` / `_close` 等始终取到 window 模块的**最新**值 (而非导入期快照)。
    """
    if name.startswith("__") and name.endswith("__"):
        raise AttributeError(name)
    try:
        return getattr(_settings, name)
    except AttributeError:
        raise AttributeError("module 'settings_window' has no attribute {!r}".format(name))
