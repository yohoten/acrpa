# -*- coding: utf-8 -*-
"""extensions.package — 扩展包安全解包 (仅复用 ``script_package`` 的安全原语)。

复用关系 (路线图 §12.3「层 3」/ §12.5 第 5 步)
--------------------------------------------
* ``script_package._safe_member``  —— 路径穿越 (zip-slip / 绝对路径 / 盘符 / ``..``);
* ``script_package._is_symlink``   —— 软链拒绝;
* ``script_package._check_limits`` —— 文件数 / 单文件 / 解压总体积 / 压缩比;
* ``script_package.compute_sha256``—— 分块哈希。

与 ``script_package.unpack`` 的差异
----------------------------------
**仅白名单前缀不同**: 脚本包为 ``{manifest.json, README.md, scripts/**, images/**}``;
扩展包为 ``{extension.json, pure/**, native/**, models/**, browsers/**}`` (§12.4 布局)。
其余安全语义 (sha256 前置 / 逐成员校验 / 限额 / 原子落地) 完全对齐。

依赖注入 / 零副作用
------------------
``script_package`` 经 ``utils`` 在 import 期即拉起 tkinter, 因此**不**在模块顶层 import;
改为函数内惰性 import (``_sp()``), 使 ``import extensions`` 保持无 tkinter/pyautogui 副作用
(对齐 ``capabilities`` 的既有契约, 见 ``tools/_test_import_side_effect_free.py``)。
"""
import os
import json
import shutil
import zipfile
import posixpath

from .manifest import EXT_MANIFEST_NAME, ExtensionError, manifest_errors

# 扩展包白名单前缀 (区别于脚本包)。
ALLOWED_PREFIXES = ("pure/", "native/", "models/", "browsers/")
_CHUNK = 1024 * 1024


def _sp():
    """惰性取 ``script_package`` (复用其安全原语; 不在本模块 import 期加载 tkinter)。"""
    import script_package
    return script_package


def _ext_whitelisted(name, is_dir=False):
    """成员是否命中扩展包白名单前缀。"""
    n = str(name).replace("\\", "/")
    if n == EXT_MANIFEST_NAME:
        return True
    if n.startswith(ALLOWED_PREFIXES):
        return True
    if is_dir and n.rstrip("/") in ("pure", "native", "models", "browsers"):
        return True
    return False


def read_manifest(pkg_path):
    """只读 ``extension.json`` (不改磁盘)。失败 raise ExtensionError。"""
    if not pkg_path or not os.path.exists(pkg_path):
        raise ExtensionError("扩展包文件不存在: {}".format(pkg_path))
    try:
        with zipfile.ZipFile(pkg_path, "r") as zf:
            if EXT_MANIFEST_NAME not in zf.namelist():
                raise ExtensionError("扩展包内缺少 {}".format(EXT_MANIFEST_NAME))
            raw = zf.read(EXT_MANIFEST_NAME)
    except ExtensionError:
        raise
    except zipfile.BadZipFile:
        raise ExtensionError("不是有效的 ZIP/扩展包: {}".format(pkg_path))
    except Exception as e:                                   # noqa: BLE001
        raise ExtensionError("读取扩展包失败: {}".format(e))
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception as e:                                   # noqa: BLE001
        raise ExtensionError("{} 解析失败: {}".format(EXT_MANIFEST_NAME, e))
    if not isinstance(data, dict):
        raise ExtensionError("{} 顶层必须是对象".format(EXT_MANIFEST_NAME))
    return data


def unpack(pkg_path, dest_dir, expected_sha256=None):
    """安全解包扩展包到 dest_dir 并返回 manifest。

    执行与 ``script_package.unpack`` 等价的安全约束 (扩展白名单), 失败 raise
    ExtensionError 且不残留半成品; 成功则「先解到同级临时目录 → os.replace 原子落地」。
    """
    if not pkg_path or not os.path.exists(pkg_path):
        raise ExtensionError("扩展包文件不存在: {}".format(pkg_path))
    pkg_path = os.path.abspath(pkg_path)
    sp = _sp()

    # (1) sha256 前置校验 —— 不符则绝不打开 ZIP
    if expected_sha256:
        try:
            got = sp.compute_sha256(pkg_path)
        except Exception as e:                              # noqa: BLE001
            raise ExtensionError("计算扩展包 sha256 失败: {}".format(e))
        if got.lower() != str(expected_sha256).lower():
            raise ExtensionError("扩展包 sha256 校验失败 (期望 {}... 实际 {}...)".format(
                str(expected_sha256)[:12], got[:12]))

    dest_dir = os.path.abspath(dest_dir)
    parent = os.path.dirname(dest_dir)
    try:
        os.makedirs(parent, exist_ok=True)
    except Exception as e:                                  # noqa: BLE001
        raise ExtensionError("无法创建目标父目录: {}".format(e))

    try:
        zf = zipfile.ZipFile(pkg_path, "r")
    except zipfile.BadZipFile:
        raise ExtensionError("不是有效的 ZIP/扩展包: {}".format(pkg_path))
    except Exception as e:                                  # noqa: BLE001
        raise ExtensionError("打开扩展包失败: {}".format(e))

    temp_dir = None
    try:
        infos = zf.infolist()

        # (2)(4) 逐成员: 路径安全 + 扩展白名单 + 软链
        files = []
        for info in infos:
            try:
                safe = sp._safe_member(info.filename)
            except sp.PackageError as e:
                raise ExtensionError(str(e))
            is_dir = info.filename.endswith("/") or info.is_dir()
            if not _ext_whitelisted(safe, is_dir):
                raise ExtensionError("扩展包成员不在白名单前缀内: {}".format(info.filename))
            if is_dir:
                continue
            if sp._is_symlink(info):
                raise ExtensionError("拒绝符号链接成员: {}".format(info.filename))
            files.append((info, safe))

        # (3) 数量/体积/压缩比
        try:
            sp._check_limits([i for i, _ in files])
        except sp.PackageError as e:
            raise ExtensionError(str(e))

        names = set(n for _, n in files)

        # (5) 结构校验
        if EXT_MANIFEST_NAME not in names:
            raise ExtensionError("扩展包内缺少 {}".format(EXT_MANIFEST_NAME))
        manifest = read_manifest(pkg_path)
        errs = manifest_errors(manifest)
        if errs:
            raise ExtensionError("; ".join(errs))

        # (6) 原子落地: 先解到同级临时目录
        import random
        temp_dir = os.path.join(
            parent, ".{}.tmp-{}-{:x}".format(
                manifest.get("id") or os.path.basename(dest_dir) or "ext",
                os.getpid(), random.getrandbits(32)))
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)
        os.makedirs(temp_dir, exist_ok=True)

        for info, safe in files:
            target = os.path.join(temp_dir, *safe.split("/"))
            if not sp._is_within(temp_dir, target):
                raise ExtensionError("成员路径越界: {}".format(info.filename))
            tdir = os.path.dirname(target)
            if tdir and not os.path.isdir(tdir):
                os.makedirs(tdir, exist_ok=True)
            with zf.open(info, "r") as src, open(target, "wb") as dst:
                while True:
                    chunk = src.read(_CHUNK)
                    if not chunk:
                        break
                    dst.write(chunk)

        # 全部校验通过 → 原子替换最终目录
        if os.path.exists(dest_dir):
            if os.path.isdir(dest_dir):
                shutil.rmtree(dest_dir, ignore_errors=True)
            else:
                os.remove(dest_dir)
        os.replace(temp_dir, dest_dir)
        temp_dir = None
        return manifest
    except ExtensionError:
        raise
    except Exception as e:                                  # noqa: BLE001
        raise ExtensionError("解包失败: {}".format(e))
    finally:
        try:
            zf.close()
        except Exception:                                   # noqa: BLE001
            pass
        if temp_dir and os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)
