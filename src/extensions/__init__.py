# -*- coding: utf-8 -*-
"""extensions — 可选依赖扩展管理器 (路线图 阶段二新增项②)。

顶层 API (供设置窗口「扩展」卡与后续命令消费方调用; 均不抛异常, 返回 (ok, msg))::

    list_installed()    -> {ext_id: {version, enabled, installed_at, size}}
    is_installed(id)    -> bool
    install(pkg_path, *, expected_sha256=None, allow_unsigned=False) -> (ok, msg)
    uninstall(ext_id)   -> (ok, msg)
    enable(ext_id)      -> (ok, msg)   # §12.5 无法热插拔 → 下次启动生效
    disable(ext_id)     -> (ok, msg)
    repair(ext_id, pkg_path=None) -> (ok, msg)

子模块
------
    manifest.py  extension.json 校验 (纯函数)
    package.py   扩展包安全解包 (复用 script_package 安全原语; 扩展白名单)
    manager.py   生命周期 (install / uninstall / enable / disable / repair)
    index.py     内置白名单 id → {version: sha256} (§12.6 信任模型)
    loader.py    提供者加载 provider() / provider_self_check() (阶段二新增项③)

顶层 API 另含 (阶段二新增项③)::

    provider(ext_id)             -> module | None
    provider_self_check(ext_id)  -> (ok, reason)

设计约束
--------
**纯逻辑 / 无 GUI / import 无副作用**: 模块级仅 import 纯 stdlib 与纯 ``capabilities``;
``script_package`` (会经 utils 拉起 tkinter) 一律惰性加载 —— 因此 ``import extensions``
不加载 tkinter/pyautogui, 与 ``capabilities`` 保持同一契约。
"""
from . import manifest, package, index, manager, loader
from .manifest import ExtensionError

__all__ = [
    "list_installed", "is_installed", "install", "uninstall",
    "enable", "disable", "repair", "root", "caps_of_extension",
    "provider", "provider_self_check",
    "manifest", "package", "index", "manager", "loader", "ExtensionError",
]

# ── 顶层转发 (门面) ──
list_installed = manager.list_installed
is_installed = manager.is_installed
install = manager.install
uninstall = manager.uninstall
enable = manager.enable
disable = manager.disable
repair = manager.repair
root = manager.root
caps_of_extension = manager.caps_of_extension
provider = loader.provider
provider_self_check = loader.provider_self_check
