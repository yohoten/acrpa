# -*- coding: utf-8 -*-
"""extensions.manifest — 扩展清单 (``extension.json``) 校验 (纯函数)。

设计约束 (务必保持)
------------------
* **纯 stdlib**: 模块级仅 import ``re``; 不 import ``utils`` (后者会拉起 tkinter,
  破坏 ``import extensions`` 无副作用契约)。
* **纯函数 / 无副作用**: ``manifest_errors`` 只读入参并返回问题列表, 不触盘、不建目录。
* 风格对齐 ``script_package.manifest_errors`` (返回问题字符串列表, 空=通过), 便于单测。

``extension.json`` 最小字段 (路线图 §12.4)::

    {"schema": 1, "id": "cv.match", "name": "OpenCV 图像匹配", "version": "1.0.0",
     "capabilities": ["cv.match"], "platform": "win", "arch": "amd64",
     "python_tag": "cp312", "size": 12345, "min_app_version": "0.2.0"}
"""
import re

EXT_MANIFEST_NAME = "extension.json"
SUPPORTED_SCHEMA = (1,)

# id 允许点分 (如 "cv.match")；每段小写字母开头。
_ID_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")
_CAP_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")
_VERSION_RE = re.compile(r"^\d+(\.\d+){1,2}$")

PLATFORMS = ("win", "any")
ARCHS = ("amd64", "arm64", "any")


class ExtensionError(Exception):
    """扩展清单/包校验异常 (manifest 与 package 共用)。"""
    pass


def _vtuple(value):
    """语义化版本 → 数值元组; 不可解析返回 ()。不抛异常。"""
    try:
        return tuple(int(x) for x in str(value).split("."))
    except Exception:
        return ()


def version_gt(a, b):
    """版本 a > b 返回 True; 任一侧不可解析返回 False。"""
    ta, tb = _vtuple(a), _vtuple(b)
    if not ta or not tb:
        return False
    n = max(len(ta), len(tb))
    ta = ta + (0,) * (n - len(ta))
    tb = tb + (0,) * (n - len(tb))
    return ta > tb


def manifest_errors(manifest):
    """返回问题字符串列表 (空=通过)。纯函数, 便于单测。"""
    errors = []
    if not isinstance(manifest, dict):
        return ["extension.json 不是 JSON 对象"]

    schema = manifest.get("schema")
    if not isinstance(schema, int):
        errors.append("schema 缺失或非整数")
    elif schema not in SUPPORTED_SCHEMA:
        errors.append("不支持的 schema={} (当前支持 {})".format(
            schema, "/".join(str(v) for v in SUPPORTED_SCHEMA)))

    sid = manifest.get("id", "")
    if not sid or not isinstance(sid, str):
        errors.append("缺少必填字段 id")
    elif not _ID_RE.match(sid):
        errors.append("id 格式非法 (应为点分段小写标识): {}".format(sid))

    name = manifest.get("name", "")
    if not name:
        errors.append("缺少必填字段 name")
    elif len(str(name)) > 80:
        errors.append("name 过长 (>80 字符)")

    ver = manifest.get("version", "")
    if not ver or not _VERSION_RE.match(str(ver)):
        errors.append("version 非法 (应为 MAJOR.MINOR[.PATCH]): {}".format(ver))

    caps = manifest.get("capabilities", [])
    if not isinstance(caps, list):
        errors.append("capabilities 应为字符串数组")
    elif not caps:
        errors.append("capabilities 不能为空 (扩展至少声明一个能力)")
    else:
        for c in caps:
            if not isinstance(c, str) or not _CAP_RE.match(c):
                errors.append("capability 非法: {!r}".format(c))

    plat = manifest.get("platform", "")
    if plat not in PLATFORMS:
        errors.append("platform 非法 (应为 {}): {}".format("/".join(PLATFORMS), plat))

    arch = manifest.get("arch", "")
    if arch not in ARCHS:
        errors.append("arch 非法 (应为 {}): {}".format("/".join(ARCHS), arch))

    tag = manifest.get("python_tag", "")
    if not isinstance(tag, str) or not tag:
        errors.append("python_tag 缺失或非字符串")

    size = manifest.get("size", None)
    if not isinstance(size, int) or isinstance(size, bool) or size < 0:
        errors.append("size 应为非负整数: {!r}".format(size))

    mav = manifest.get("min_app_version", "")
    if mav and not _VERSION_RE.match(str(mav)):
        errors.append("min_app_version 格式非法: {}".format(mav))

    return errors


def validate(manifest, app_version=None):
    """问题列表非空则 raise ExtensionError; app_version 非空时额外做 min_app_version 比对。"""
    errors = manifest_errors(manifest)
    if not errors and app_version and isinstance(manifest, dict):
        mav = manifest.get("min_app_version") or ""
        if mav and version_gt(mav, app_version):
            errors.append("该扩展需要 ACRPA >= {} (当前 {}), 请升级".format(
                mav, app_version))
    if errors:
        raise ExtensionError("; ".join(errors))
