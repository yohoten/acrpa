# -*- coding: utf-8 -*-
"""extensions.manager — 扩展生命周期: install / uninstall / enable / disable / repair。

安装布局 (路线图 §12.4)
----------------------
根目录优先取环境变量 ``ACRPA_EXTENSIONS_ROOT`` (便于测试 / 避免污染真实用户目录),
否则 ``%LOCALAPPDATA%/ACRPA/extensions``::

    <root>/
    ├── installed.json                 # 已安装登记表 (id → version/enabled/installed_at/size)
    ├── quarantine/                    # 自检失败件的隔离区 (不删, 便于排查)
    └── <ext_id>/<version>/
        ├── extension.json / pure/ / native/ / models/ / browsers/

安装链路 (§12.5)
---------------
① (若给 sha256, 或白名单命中) **强制 sha256 校验**;
② 安全解包 (zip-slip / 软链 / 限额 / 扩展白名单 → ``extensions.package.unpack``);
③ **原子落地** (package.unpack 内: 同级 ``.tmp-*`` → ``os.replace``);
④ **安装期自检**: 逐声明能力 ``capabilities.refresh`` → ``state``, 非 READY 即整体回滚;
⑤ 写 ``installed.json``。

统一约定
--------
* 所有对外函数**不抛异常**, 一律返回 ``(ok: bool, message: str)`` (UI 友好);
* ``enable/disable`` 无法热插拔 (§12.5): 返回文案含「下次启动生效」。

零副作用: 模块级仅 import 纯 stdlib 与纯 ``capabilities``; ``script_package`` 经
``extensions.package`` 惰性加载, 故 ``import extensions`` 不加载 tkinter/pyautogui。
"""
import os
import json
import time
import shutil
import random

import capabilities

from . import index, manifest as _manifest, package
from .manifest import ExtensionError, EXT_MANIFEST_NAME

ENV_ROOT = "ACRPA_EXTENSIONS_ROOT"
INSTALLED_NAME = "installed.json"
QUARANTINE_NAME = "quarantine"

RESTART_NOTE = "（下次启动生效）"


# ══════════════════════════════════════════════════════════════════════
# 根目录 / installed.json 读写
# ══════════════════════════════════════════════════════════════════════

def root():
    """→ 扩展安装根目录 (环境变量可注入, 否则 %LOCALAPPDATA%/ACRPA/extensions)。"""
    env = os.environ.get(ENV_ROOT)
    if env:
        return os.path.abspath(env)
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "ACRPA", "extensions")


def installed_json_path():
    return os.path.join(root(), INSTALLED_NAME)


def quarantine_dir():
    return os.path.join(root(), QUARANTINE_NAME)


def _ensure_root():
    try:
        os.makedirs(root(), exist_ok=True)
    except Exception as e:                                  # noqa: BLE001
        raise ExtensionError("无法创建扩展根目录: {}".format(e))


def _load_installed():
    """读 installed.json → dict; 缺失/损坏返回 {} (不抛异常)。"""
    p = installed_json_path()
    if not os.path.exists(p):
        return {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:                                       # noqa: BLE001
        return {}


def _save_installed(data):
    """原子写 installed.json (.part → os.replace)。"""
    _ensure_root()
    p = installed_json_path()
    tmp = p + ".part"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, p)
    except Exception as e:                                  # noqa: BLE001
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:                                   # noqa: BLE001
            pass
        raise ExtensionError("写入 {} 失败: {}".format(INSTALLED_NAME, e))


def _now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ══════════════════════════════════════════════════════════════════════
# 查询
# ══════════════════════════════════════════════════════════════════════

def list_installed():
    """→ {ext_id: {version, enabled, installed_at, size}} 快照 (只读, 不建目录)。"""
    return dict(_load_installed())


def is_installed(ext_id):
    """该扩展是否登记在 installed.json。"""
    return str(ext_id) in _load_installed()


def _read_installed_manifest(ext_id, version):
    """读已安装目录内的 extension.json → manifest dict; 失败返回 {}。"""
    p = os.path.join(root(), str(ext_id), str(version), EXT_MANIFEST_NAME)
    if not os.path.exists(p):
        return {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:                                       # noqa: BLE001
        return {}


def caps_of_extension(ext_id):
    """→ 已安装扩展声明的能力 id 列表 (读其 extension.json; 缺失返回 [])。"""
    rec = _load_installed().get(str(ext_id))
    if not rec:
        return []
    m = _read_installed_manifest(ext_id, rec.get("version", ""))
    caps = m.get("capabilities") or []
    return [str(c) for c in caps] if isinstance(caps, list) else []


# ══════════════════════════════════════════════════════════════════════
# 自检 / 回滚
# ══════════════════════════════════════════════════════════════════════

def _self_check(m):
    """逐声明能力重探; 全部 READY → (True, ""); 否则 (False, 原因)。

    「未注册能力」失败开放为 READY (与 ``capabilities`` 契约一致)。
    """
    caps = m.get("capabilities") or []
    for cap_id in caps:
        try:
            capabilities.refresh(cap_id)
            st = capabilities.state(cap_id)
        except Exception as e:                              # noqa: BLE001
            return False, "能力 {} 自检异常: {}".format(cap_id, e)
        if st != capabilities.CapState.READY:
            return False, "能力 {} 自检为 {} ({})".format(
                cap_id, getattr(st, "value", st), capabilities.require_reason(cap_id))
    return True, ""


def _quarantine(dest_dir, ext_id, version):
    """把产物移入 quarantine/ (不删); 清理空的 ext_id 父目录。失败静默。"""
    try:
        qdir = quarantine_dir()
        os.makedirs(qdir, exist_ok=True)
        tag = "{}-{}".format(ext_id, version)
        target = os.path.join(qdir, "{}-{:x}".format(tag, random.getrandbits(24)))
        if os.path.exists(dest_dir):
            shutil.move(dest_dir, target)
    except Exception:                                       # noqa: BLE001
        # 退而求其次: 直接删除, 保证不残留半成品
        try:
            shutil.rmtree(dest_dir, ignore_errors=True)
        except Exception:                                   # noqa: BLE001
            pass
    # 清理空的 <root>/<ext_id>
    try:
        parent = os.path.dirname(dest_dir)
        if os.path.isdir(parent) and not os.listdir(parent):
            os.rmdir(parent)
    except Exception:                                       # noqa: BLE001
        pass


# ══════════════════════════════════════════════════════════════════════
# 安装
# ══════════════════════════════════════════════════════════════════════

def install(pkg_path, *, expected_sha256=None, allow_unsigned=False):
    """安装扩展包 → (ok, message)。

    - ``expected_sha256`` 非空 → 强制校验 (不符拒绝);
    - ``allow_unsigned=True`` → 忽略内置白名单 (仅用 expected_sha256 校验);
    - 默认 (allow_unsigned=False) → 期望哈希 = expected_sha256 或白名单内建值;
      两者皆无 (白名单占位为空, 见 index.py) 时按「未签名」放行。
    """
    try:
        if not pkg_path or not os.path.exists(pkg_path):
            return False, "扩展包不存在: {}".format(pkg_path)

        try:
            m = package.read_manifest(pkg_path)
        except ExtensionError as e:
            return False, "清单无效: {}".format(e)

        errs = _manifest.manifest_errors(m)
        if errs:
            return False, "扩展清单校验失败: " + "; ".join(errs)

        ext_id = str(m.get("id"))
        version = str(m.get("version"))

        # ① sha256 (强制/白名单)
        if allow_unsigned:
            expected = expected_sha256
        else:
            expected = expected_sha256 or index.sha256_for(ext_id, version)
        if expected:
            try:
                import hashlib
                h = hashlib.sha256()
                with open(pkg_path, "rb") as f:
                    for block in iter(lambda: f.read(1024 * 1024), b""):
                        h.update(block)
                got = h.hexdigest()
            except Exception as e:                          # noqa: BLE001
                return False, "计算 sha256 失败: {}".format(e)
            if got.lower() != str(expected).lower():
                return False, "sha256 校验失败 (期望 {}... 实际 {}...)".format(
                    str(expected)[:12], got[:12])

        _ensure_root()
        dest = os.path.join(root(), ext_id, version)

        # ②③ 安全解包 + 原子落地
        try:
            package.unpack(pkg_path, dest, expected_sha256=expected)
        except ExtensionError as e:
            return False, "解包失败: {}".format(e)

        # ④ 安装期自检
        ok, why = _self_check(m)
        if not ok:
            _quarantine(dest, ext_id, version)
            return False, "安装自检失败, 已回滚并隔离: {}".format(why)

        # ⑤ 写 installed.json
        data = _load_installed()
        data[ext_id] = {
            "version": version,
            "enabled": True,
            "installed_at": _now_iso(),
            "size": int(m.get("size") or 0),
        }
        try:
            _save_installed(data)
        except ExtensionError as e:
            _quarantine(dest, ext_id, version)
            return False, "登记失败, 已回滚并隔离: {}".format(e)

        # 刷新能力缓存 (自检已探过, 此处保证是当前态)
        for c in (m.get("capabilities") or []):
            try:
                capabilities.refresh(c)
            except Exception:                               # noqa: BLE001
                pass

        note = "" if (expected or allow_unsigned) else " [未签名]"
        return True, "扩展 {} {} 安装成功{}{}".format(ext_id, version, note, RESTART_NOTE)
    except Exception as e:                                  # noqa: BLE001
        return False, "安装失败: {}: {}".format(type(e).__name__, e)


# ══════════════════════════════════════════════════════════════════════
# 卸载 / 启用 / 禁用 / 修复
# ══════════════════════════════════════════════════════════════════════

def uninstall(ext_id):
    """卸载扩展 (删目录 + 更新 installed.json + 刷新能力) → (ok, message)。"""
    try:
        ext_id = str(ext_id)
        data = _load_installed()
        rec = data.get(ext_id)
        if not rec:
            return False, "未安装扩展: {}".format(ext_id)
        caps = caps_of_extension(ext_id)

        d = os.path.join(root(), ext_id)
        try:
            shutil.rmtree(d, ignore_errors=True)
        except Exception as e:                              # noqa: BLE001
            return False, "删除目录失败: {}".format(e)

        data.pop(ext_id, None)
        _save_installed(data)
        for c in caps:
            try:
                capabilities.refresh(c)
            except Exception:                               # noqa: BLE001
                pass
        return True, "已卸载扩展 {}{}".format(ext_id, RESTART_NOTE)
    except Exception as e:                                  # noqa: BLE001
        return False, "卸载失败: {}: {}".format(type(e).__name__, e)


def _set_enabled(ext_id, enabled):
    """内部: 更新 installed.json 的 enabled + 同步 capabilities.enable/disable。"""
    ext_id = str(ext_id)
    data = _load_installed()
    rec = data.get(ext_id)
    if not rec:
        return False, "未安装扩展: {}".format(ext_id)
    rec["enabled"] = bool(enabled)
    data[ext_id] = rec
    _save_installed(data)
    for c in caps_of_extension(ext_id):
        try:
            if enabled:
                capabilities.enable(c)
            else:
                capabilities.disable(c)
        except Exception:                                   # noqa: BLE001
            pass
    verb = "启用" if enabled else "禁用"
    return True, "已{}扩展 {}{}".format(verb, ext_id, RESTART_NOTE)


def enable(ext_id):
    """启用扩展 (§12.5: 无法热插拔, 下次启动生效)。"""
    try:
        return _set_enabled(ext_id, True)
    except Exception as e:                                  # noqa: BLE001
        return False, "启用失败: {}: {}".format(type(e).__name__, e)


def disable(ext_id):
    """禁用扩展 (§12.5: 无法热插拔, 下次启动生效)。"""
    try:
        return _set_enabled(ext_id, False)
    except Exception as e:                                  # noqa: BLE001
        return False, "禁用失败: {}: {}".format(type(e).__name__, e)


def repair(ext_id, pkg_path=None, *, expected_sha256=None, allow_unsigned=True):
    """修复扩展。

    - 给了 ``pkg_path`` → 重跑「解包→自检」(经 ``install``), 失败回滚/隔离;
    - 未给 → 对已安装内容重跑自检; 失败则隔离并移出 installed.json。
    """
    try:
        ext_id = str(ext_id)
        data = _load_installed()
        rec = data.get(ext_id)
        if not rec:
            return False, "未安装扩展: {}".format(ext_id)
        version = rec.get("version", "")

        if pkg_path:
            was_enabled = bool(rec.get("enabled", True))
            ok, msg = install(pkg_path, expected_sha256=expected_sha256,
                              allow_unsigned=allow_unsigned)
            if not ok:
                return False, "修复失败: {}".format(msg)
            if not was_enabled:
                _set_enabled(ext_id, False)
            return True, "修复完成: {}".format(msg)

        m = _read_installed_manifest(ext_id, version)
        if not m:
            return False, "修复失败: 找不到已安装清单 {}".format(EXT_MANIFEST_NAME)
        ok, why = _self_check(m)
        if ok:
            return True, "修复完成: 扩展 {} 自检通过{}".format(ext_id, RESTART_NOTE)
        # 自检失败: 隔离 + 移出登记
        dest = os.path.join(root(), ext_id, version)
        _quarantine(dest, ext_id, version)
        data.pop(ext_id, None)
        _save_installed(data)
        return False, "修复失败, 已隔离: {}".format(why)
    except Exception as e:                                  # noqa: BLE001
        return False, "修复失败: {}: {}".format(type(e).__name__, e)
