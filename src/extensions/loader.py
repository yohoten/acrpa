# -*- coding: utf-8 -*-
"""extensions.loader — 扩展「提供者」加载 (路线图 阶段二新增项③)。

职责
----
把一个**已安装**扩展的 ``pure/`` 目录加入 ``sys.path`` (幂等) 并按 ``extension.json``
的 ``entry`` 惰性 ``import`` 出功能模块 (即「提供者」), 供 ``capabilities`` / ``engine``
消费。全部函数**不抛异常** (返回 ``None`` / ``(ok, reason)``), 与 ``extensions`` 顶层
「UI 友好」契约一致。

零副作用
--------
模块级仅 import 纯 stdlib (``os`` / ``sys`` / ``importlib``); 对 ``manager`` 的引用一律
函数内惰性 import —— 因此 ``import extensions`` 仍不加载 tkinter/pyautogui/script_package
(见 ``tools/_test_import_side_effect_free.py`` I7)。

对外 API
--------
    provider(ext_id)            -> module | None
    provider_self_check(ext_id) -> (ok: bool, reason: str)
"""
import os
import sys
import importlib

__all__ = ["provider", "provider_self_check"]

_not_installed = "扩展未安装或加载失败"


def _installed_manifest(ext_id):
    """→ (version, manifest_dict) ; 未安装 / 读失败 → (None, {})。"""
    from . import manager                            # 惰性 (避免 import 期环依赖)
    rec = manager.list_installed().get(str(ext_id))
    if not rec:
        return None, {}
    version = str(rec.get("version") or "")
    m = manager._read_installed_manifest(ext_id, version)
    return version, (m or {})


def provider(ext_id):
    """已安装 → 加载并返回其提供者模块; 未装 / 失败 → None (不抛)。"""
    try:
        ext_id = str(ext_id)
        version, m = _installed_manifest(ext_id)
        if not version or not m:
            return None
        entry = str(m.get("entry") or "").strip()
        if not entry:
            return None
        from . import manager
        pure_dir = os.path.join(manager.root(), ext_id, version, "pure")
        if not os.path.isdir(pure_dir):
            return None
        # 幂等: 同一目录只入栈一次
        if pure_dir not in sys.path:
            sys.path.insert(0, pure_dir)
        return importlib.import_module(entry)
    except Exception:                                # noqa: BLE001
        return None


def provider_self_check(ext_id):
    """懒加载提供者并调其 ``self_check()`` → (ok, reason); 任何异常 → (False, ...)。"""
    mod = provider(ext_id)
    if mod is None:
        return (False, _not_installed)
    fn = getattr(mod, "self_check", None)
    if not callable(fn):
        return (False, "扩展未提供 self_check()")
    try:
        r = fn()
    except Exception as e:                           # noqa: BLE001
        return (False, "self_check 异常: {}: {}".format(type(e).__name__, e))
    if isinstance(r, tuple) and len(r) >= 2:
        return (bool(r[0]), str(r[1]))
    if isinstance(r, bool):
        return (r, "")
    return (False, "self_check 返回值不可识别: {!r}".format(r))
