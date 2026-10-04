"""script_package.py — `.acrpapkg` 脚本包的打包 / 解包 / 校验 / 哈希。

包格式 = 标准 ZIP 归档 (zipfile, ZIP_DEFLATED)，布局见
docs/marketplace-v2-design.md §1：

    <id>-<version>.acrpapkg
    ├── manifest.json                     必需，位于根
    ├── README.md                         可选，投稿/使用说明
    ├── scripts/<entry>.xls               必需，脚本主体(二进制 Excel)
    └── images/*.png                      可选，脚本引用的图像资源

零第三方依赖：os / re / json / time / shutil / hashlib / zipfile / posixpath。

安全硬约束 (§1.5，unpack 必须全部满足，任一违反即整体拒绝)：
  1. sha256 前置校验 —— 不符则不打开 ZIP；
  2. 防 zip-slip —— 拒绝 `..` / 绝对路径 / 盘符 / 软链，规范化后断言落在目标根内；
  3. 体积/数量上限 —— 文件数、单文件、解压总体积、压缩比；
  4. 白名单前缀 —— 仅 {manifest.json, README.md, scripts/**, images/**}；
  5. 结构校验 —— manifest 可解析、版本受支持、必填齐全、entry/images 实际存在；
  6. 原子安装 —— 先解压到同级临时目录，全部通过后再替换到最终目录；
  7. 只读落盘 —— 不回写任何可执行代码路径，不执行包内任何内容。
"""
import os
import re
import json
import time
import shutil
import hashlib
import zipfile
import posixpath

import utils
from utils import log1


# ── 常量 ──
MANIFEST_NAME = "manifest.json"
README_NAME = "README.md"
PKG_EXT = ".acrpapkg"
SUPPORTED_MANIFEST_VERSIONS = (1,)
ALLOWED_PREFIXES = ("manifest.json", "README.md", "scripts/", "images/")
CATEGORIES = ("办公", "财务", "系统", "其他")
REQUIRES_VALUES = ("pywin32", "ocr", "playwright", "dd_driver")

MAX_FILE_COUNT = 256
MAX_UNPACK_BYTES = 64 * 1024 * 1024   # 解压后总字节上限
MAX_SINGLE_FILE = 64 * 1024 * 1024    # 单文件上限
MAX_COMPRESS_RATIO = 200              # 单条压缩比上限(且 >1MB 时判定疑似 zip bomb)

_CHUNK = 1024 * 1024
_ID_RE = re.compile(r"^[a-z][a-z0-9_]{2,49}$")
_VERSION_RE = re.compile(r"^\d+(\.\d+){1,2}$")
_DRIVE_RE = re.compile(r"^[A-Za-z]:")


class PackageError(Exception):
    """脚本包打包/解包/校验异常。"""
    pass


# ══════════════════════════════════════════════════════════════════════
# 哈希 / 版本工具
# ══════════════════════════════════════════════════════════════════════

def compute_sha256(path):
    """分块 (1MB) 计算文件 sha256，返回 64 位小写 hex。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(_CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def version_tuple(value):
    """语义化版本 → 数值元组 (major, minor, patch, ...)。

    P0-3: 委托 utils.version_tuple (唯一真源)。预发布后缀被正确剥离 ——
    "0.1.29-beta" → (0, 1, 29) (旧实现会因 int("29-beta") 抛异常退化成 (0, 0, 0))。
    无法解析 / 空输入返回 (), 且不抛异常。
    """
    return utils.version_tuple(value)


def version_gt(a, b):
    """版本 a > b 返回 True。

    P0-3: 委托 utils.version_gt (唯一真源)。任一侧不可解析时返回 False,
    不会把无法解析的输入误判为「远端/A 侧更新」。
    """
    return utils.version_gt(a, b)


def _utc_now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _app_version():
    """当前 ACRPA 版本 (min_app_version 比对的基准)；取不到返回空串(视为无下限)。"""
    try:
        from version_info import VERSION
        return str(VERSION)
    except Exception:
        return ""


# ══════════════════════════════════════════════════════════════════════
# manifest 构建 / 校验
# ══════════════════════════════════════════════════════════════════════

def build_manifest(script_path, meta, images=None,
                   manifest_version=1, app_min_version=""):
    """由 meta + 脚本文件路径生成规范化 manifest。

    meta 键: id/name/description/category/author/version/tags/icon/requires；
    缺字段用默认值补；不对合法性做最终裁决 (交 validate_manifest)。
    """
    meta = meta or {}
    script_name = os.path.basename(script_path or "")

    tags = meta.get("tags", [])
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    req = meta.get("requires", [])
    if isinstance(req, str):
        req = [t.strip() for t in req.split(",") if t.strip()]

    imgs = images or []
    img_rel = ["images/{}".format(os.path.basename(str(p))) for p in imgs]

    manifest = {
        "manifest_version": int(manifest_version or 1),
        "id": str(meta.get("id", "") or "").strip(),
        "name": str(meta.get("name", "") or ""),
        "description": str(meta.get("description", "") or ""),
        "category": str(meta.get("category", "") or ""),
        "author": str(meta.get("author", "") or ""),
        "version": str(meta.get("version", "") or ""),
        "tags": list(tags),
        "icon": str(meta.get("icon", "") or ""),
        "requires": list(req),
        "entry": "scripts/{}".format(script_name) if script_name else "",
        "images": img_rel,
        "readme": "",
        "created_at": _utc_now_iso(),
        "min_app_version": str(app_min_version or
                               meta.get("min_app_version", "") or ""),
        "script_sha256": "",
        "signature": None,
    }
    if script_path and os.path.exists(script_path):
        manifest["script_sha256"] = compute_sha256(script_path)
    return manifest


def manifest_errors(manifest):
    """返回问题字符串列表 (空=通过)。纯函数，便于单测。"""
    errors = []
    if not isinstance(manifest, dict):
        return ["manifest 不是 JSON 对象"]

    mv = manifest.get("manifest_version")
    if not isinstance(mv, int):
        errors.append("manifest_version 缺失或非整数")
    elif mv not in SUPPORTED_MANIFEST_VERSIONS:
        errors.append("不支持的 manifest_version={} (当前支持 {})，请升级 ACRPA".format(
            mv, "/".join(str(v) for v in SUPPORTED_MANIFEST_VERSIONS)))

    sid = manifest.get("id", "")
    if not sid or not isinstance(sid, str):
        errors.append("缺少必填字段 id")
    elif sid.startswith("builtin_"):
        errors.append("id 不得以 builtin_ 开头: {}".format(sid))
    elif not _ID_RE.match(sid):
        errors.append("id 格式非法 (应为 ^[a-z][a-z0-9_]{{2,49}}$): {}".format(sid))

    name = manifest.get("name", "")
    if not name:
        errors.append("缺少必填字段 name")
    elif len(str(name)) > 60:
        errors.append("name 过长 (>60 字符)")

    desc = manifest.get("description", "")
    if not desc:
        errors.append("缺少必填字段 description")
    elif len(str(desc)) > 300:
        errors.append("description 过长 (>300 字符)")

    cat = manifest.get("category", "")
    if cat not in CATEGORIES:
        errors.append("category 非法 (应为 {}): {}".format("/".join(CATEGORIES), cat))

    author = manifest.get("author", "")
    if not author:
        errors.append("缺少必填字段 author")
    elif len(str(author)) > 40:
        errors.append("author 过长 (>40 字符)")

    ver = manifest.get("version", "")
    if not ver or not _VERSION_RE.match(str(ver)):
        errors.append("version 非法 (应为 MAJOR.MINOR[.PATCH]): {}".format(ver))

    tags = manifest.get("tags", [])
    if tags and not isinstance(tags, list):
        errors.append("tags 应为字符串数组")
    elif isinstance(tags, list):
        if len(tags) > 8:
            errors.append("tags 数量超限 (>8)")
        for t in tags:
            if len(str(t)) > 16:
                errors.append("tag 过长 (>16): {}".format(t))

    req = manifest.get("requires", [])
    if req and not isinstance(req, list):
        errors.append("requires 应为字符串数组")
    elif isinstance(req, list):
        for r in req:
            if r not in REQUIRES_VALUES:
                errors.append("requires 取值非法: {}".format(r))

    entry = manifest.get("entry", "")
    if not entry:
        errors.append("缺少必填字段 entry")
    elif not str(entry).startswith("scripts/"):
        errors.append("entry 未命中 scripts/ 前缀: {}".format(entry))
    elif not str(entry).lower().endswith(".xls"):
        errors.append("entry 必须是 .xls: {}".format(entry))

    imgs = manifest.get("images", [])
    if imgs and not isinstance(imgs, list):
        errors.append("images 应为字符串数组")
    elif isinstance(imgs, list):
        for p in imgs:
            if not str(p).startswith("images/"):
                errors.append("image 未命中 images/ 前缀: {}".format(p))
            elif not str(p).lower().endswith(".png"):
                errors.append("image 必须是 .png: {}".format(p))

    readme = manifest.get("readme", "")
    if readme and str(readme) != README_NAME:
        errors.append("readme 只允许 {}: {}".format(README_NAME, readme))

    mav = manifest.get("min_app_version", "")
    if mav and not _VERSION_RE.match(str(mav)):
        errors.append("min_app_version 格式非法: {}".format(mav))

    return errors


def validate_manifest(manifest, app_version=None):
    """问题列表非空则 raise PackageError。app_version 非空时额外做 min_app_version 比对。"""
    errors = manifest_errors(manifest)
    if not errors and app_version and isinstance(manifest, dict):
        mav = manifest.get("min_app_version") or ""
        if mav and version_gt(mav, app_version):
            errors.append("该脚本需要 ACRPA >= {} (当前 {})，请升级".format(mav, app_version))
    if errors:
        raise PackageError("; ".join(errors))


# ══════════════════════════════════════════════════════════════════════
# 内部安全工具 (单测直接覆盖)
# ══════════════════════════════════════════════════════════════════════

def _is_within(base, target):
    """target 是否位于 base 目录之内 (两者均按绝对路径规范化)。"""
    try:
        base = os.path.normpath(os.path.abspath(base))
        target = os.path.normpath(os.path.abspath(target))
    except Exception:
        return False
    if target == base:
        return True
    return target.startswith(base + os.sep)


def _safe_member(name):
    """规范化成员名并做路径穿越校验；违规 raise PackageError。

    返回 posix 风格的规范化相对路径。
    """
    if not name:
        raise PackageError("包内存在空成员名")
    raw = str(name).replace("\\", "/")
    if raw.startswith("/"):
        raise PackageError("拒绝绝对路径成员: {}".format(name))
    if raw.startswith("//"):
        raise PackageError("拒绝 UNC 路径成员: {}".format(name))
    if _DRIVE_RE.match(raw):
        raise PackageError("拒绝含盘符的成员: {}".format(name))
    if ".." in raw.split("/"):
        raise PackageError("拒绝含 '..' 的成员: {}".format(name))
    norm = posixpath.normpath(raw)
    if norm in (".", "") or norm == ".." or norm.startswith("../"):
        raise PackageError("成员路径非法: {}".format(name))
    if posixpath.isabs(norm):
        raise PackageError("拒绝绝对路径成员: {}".format(name))
    return norm


def _is_symlink(info):
    """ZipInfo 外部属性高 16 位记录 Unix mode；S_IFLNK 位为 0xA000。"""
    try:
        return ((info.external_attr >> 16) & 0xA000) == 0xA000
    except Exception:
        return False


def _check_limits(infolist):
    """数量/单文件/解压总体积/压缩比 上限检查；违规 raise PackageError。"""
    if len(infolist) > MAX_FILE_COUNT:
        raise PackageError("包内条目数超限: {} > {}".format(
            len(infolist), MAX_FILE_COUNT))
    total = 0
    for info in infolist:
        try:
            size = int(getattr(info, "file_size", 0) or 0)
            csize = int(getattr(info, "compress_size", 0) or 0)
        except Exception:
            size, csize = 0, 0
        if size > MAX_SINGLE_FILE:
            raise PackageError("单文件超限: {} ({})".format(info.filename, size))
        total += size
        if total > MAX_UNPACK_BYTES:
            raise PackageError("解压后总体积超限 (> {})".format(MAX_UNPACK_BYTES))
        # 疑似 zip bomb: 体积可观且压缩比过高
        if size > 1024 * 1024:
            if csize <= 0:
                raise PackageError("疑似 zip bomb (压缩体积异常): {}".format(info.filename))
            if size / float(csize) > MAX_COMPRESS_RATIO:
                raise PackageError("疑似 zip bomb (压缩比过高): {}".format(info.filename))


def _whitelisted(name, is_dir=False):
    """成员是否命中白名单前缀 {manifest.json, README.md, scripts/**, images/**}。"""
    n = str(name).replace("\\", "/")
    if n in (MANIFEST_NAME, README_NAME):
        return True
    if n.startswith("scripts/") or n.startswith("images/"):
        return True
    if is_dir and n in ("scripts", "images"):
        return True
    return False


def _safe_remove(path):
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════
# 打包
# ══════════════════════════════════════════════════════════════════════

def find_local_images(script_path, names=None):
    """收集与 script_path 同目录的 .png；names 非空则只取指定名(自动补 .png)。"""
    if not script_path:
        return []
    d = os.path.dirname(os.path.abspath(script_path))
    if not d or not os.path.isdir(d):
        return []
    found = []
    if names:
        for n in names:
            base = os.path.basename(str(n))
            if not base.lower().endswith(".png"):
                base += ".png"
            p = os.path.join(d, base)
            if os.path.exists(p):
                found.append(p)
        return found
    try:
        for fn in sorted(os.listdir(d)):
            if fn.lower().endswith(".png"):
                found.append(os.path.join(d, fn))
    except Exception:
        return []
    return found


def pack(script_path, manifest=None, images=None, out_path=None, meta=None):
    """打包 → (pkg_path, sha256)。

    - manifest 为 None 时由 meta + script_path 构建；
    - images 为 None 时自动 find_local_images(script_path)；
    - out_path 为 None 时输出到 script_path 同目录 <id>-<version>.acrpapkg。
    """
    if not script_path or not os.path.exists(script_path):
        raise PackageError("脚本文件不存在: {}".format(script_path))
    script_path = os.path.abspath(script_path)
    script_name = os.path.basename(script_path)
    script_dir = os.path.dirname(script_path)
    meta = meta or {}

    imgs = images
    if imgs is None:
        imgs = find_local_images(script_path)
    imgs = [os.path.abspath(p) for p in (imgs or []) if p and os.path.exists(p)]

    if manifest is None:
        manifest = build_manifest(script_path, meta, images=imgs)
    else:
        manifest = dict(manifest)
    if not manifest.get("script_sha256"):
        manifest["script_sha256"] = compute_sha256(script_path)
    if not manifest.get("created_at"):
        manifest["created_at"] = _utc_now_iso()
    if not manifest.get("entry"):
        manifest["entry"] = "scripts/{}".format(script_name)
    if not manifest.get("images"):
        manifest["images"] = ["images/{}".format(os.path.basename(p)) for p in imgs]
    if "tags" not in manifest:
        manifest["tags"] = []
    if "requires" not in manifest:
        manifest["requires"] = []

    # README：meta.readme_path 优先，其次脚本同目录 README.md
    readme_src = meta.get("readme_path") or ""
    if not readme_src:
        cand = os.path.join(script_dir, README_NAME)
        if os.path.exists(cand):
            readme_src = cand
    if readme_src and os.path.exists(readme_src):
        manifest["readme"] = README_NAME
    elif not manifest.get("readme"):
        manifest["readme"] = ""

    validate_manifest(manifest)

    if out_path is None:
        out_path = os.path.join(script_dir, "{}-{}{}".format(
            manifest.get("id", "script"), manifest.get("version", "0.0"),
            PKG_EXT))
    out_path = os.path.abspath(out_path)
    out_dir = os.path.dirname(out_path)
    if out_dir and not os.path.isdir(out_dir):
        os.makedirs(out_dir, exist_ok=True)

    tmp = out_path + ".part"
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(MANIFEST_NAME,
                        json.dumps(manifest, ensure_ascii=False, indent=2))
            if manifest.get("readme") and readme_src and os.path.exists(readme_src):
                with open(readme_src, "rb") as f:
                    zf.writestr(README_NAME, f.read())
            zf.write(script_path, "scripts/{}".format(script_name))
            for p in imgs:
                zf.write(p, "images/{}".format(os.path.basename(p)))
        os.replace(tmp, out_path)
    except Exception as e:
        _safe_remove(tmp)
        if isinstance(e, PackageError):
            raise
        raise PackageError("打包失败: {}".format(e))

    return out_path, compute_sha256(out_path)


# ══════════════════════════════════════════════════════════════════════
# 解包 / 安装
# ══════════════════════════════════════════════════════════════════════

def read_manifest(pkg_path):
    """只读 manifest (不改磁盘)，用于上传前置校验/详情预览。"""
    if not pkg_path or not os.path.exists(pkg_path):
        raise PackageError("包文件不存在: {}".format(pkg_path))
    try:
        with zipfile.ZipFile(pkg_path, "r") as zf:
            if MANIFEST_NAME not in zf.namelist():
                raise PackageError("包内缺少 {}".format(MANIFEST_NAME))
            raw = zf.read(MANIFEST_NAME)
    except PackageError:
        raise
    except zipfile.BadZipFile:
        raise PackageError("不是有效的 ZIP/包文件: {}".format(pkg_path))
    except Exception as e:
        raise PackageError("读取包失败: {}".format(e))
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception as e:
        raise PackageError("manifest.json 解析失败: {}".format(e))
    if not isinstance(data, dict):
        raise PackageError("manifest.json 顶层必须是对象")
    return data


def unpack(pkg_path, dest_dir, expected_sha256=None):
    """安全解包到 dest_dir 并返回 manifest。

    执行 §1.5 全部安全约束；失败 raise PackageError 且不残留半成品。
    """
    if not pkg_path or not os.path.exists(pkg_path):
        raise PackageError("包文件不存在: {}".format(pkg_path))
    pkg_path = os.path.abspath(pkg_path)

    # (1) sha256 前置校验 —— 不符则绝不打开 ZIP
    if expected_sha256:
        got = compute_sha256(pkg_path)
        if got.lower() != str(expected_sha256).lower():
            raise PackageError("包 sha256 校验失败 (期望 {}... 实际 {}...)".format(
                str(expected_sha256)[:12], got[:12]))

    dest_dir = os.path.abspath(dest_dir)
    parent = os.path.dirname(dest_dir)
    try:
        os.makedirs(parent, exist_ok=True)
    except Exception as e:
        raise PackageError("无法创建目标父目录: {}".format(e))

    try:
        zf = zipfile.ZipFile(pkg_path, "r")
    except zipfile.BadZipFile:
        raise PackageError("不是有效的 ZIP/包文件: {}".format(pkg_path))
    except Exception as e:
        raise PackageError("打开包失败: {}".format(e))

    temp_dir = None
    try:
        infos = zf.infolist()

        # (2)(4) 逐成员：路径安全 + 白名单 + 软链
        files = []
        for info in infos:
            safe = _safe_member(info.filename)
            is_dir = info.filename.endswith("/") or info.is_dir()
            if not _whitelisted(safe, is_dir):
                raise PackageError("包内成员不在白名单前缀内: {}".format(info.filename))
            if is_dir:
                continue
            if _is_symlink(info):
                raise PackageError("拒绝符号链接成员: {}".format(info.filename))
            files.append((info, safe))

        # (3) 数量/体积/压缩比
        _check_limits([i for i, _ in files])

        names = set(n for _, n in files)

        # (5) 结构校验
        if MANIFEST_NAME not in names:
            raise PackageError("包内缺少 {}".format(MANIFEST_NAME))
        manifest = read_manifest(pkg_path)
        validate_manifest(manifest, app_version=_app_version())
        entry = manifest.get("entry", "")
        if entry not in names:
            raise PackageError("entry 在包内不存在: {}".format(entry))
        for p in manifest.get("images", []) or []:
            if p not in names:
                raise PackageError("images 声明的文件在包内不存在: {}".format(p))
        declared_sha = manifest.get("script_sha256") or ""

        # (6) 原子安装：先解压到同级临时目录
        temp_dir = os.path.join(
            parent, ".{}.tmp-{}".format(manifest.get("id") or
                                        os.path.basename(dest_dir) or "pkg",
                                        os.getpid()))
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)
        os.makedirs(temp_dir, exist_ok=True)

        total = 0
        for info, safe in files:
            target = os.path.join(temp_dir, *safe.split("/"))
            if not _is_within(temp_dir, target):
                raise PackageError("成员路径越界: {}".format(info.filename))
            tdir = os.path.dirname(target)
            if tdir and not os.path.isdir(tdir):
                os.makedirs(tdir, exist_ok=True)
            with zf.open(info, "r") as src, open(target, "wb") as dst:
                while True:
                    chunk = src.read(_CHUNK)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_UNPACK_BYTES:
                        raise PackageError("解压后总体积超限 (> {})".format(
                            MAX_UNPACK_BYTES))
                    dst.write(chunk)

        # 包内脚本内容复核
        if declared_sha:
            ep = os.path.join(temp_dir, *entry.split("/"))
            if compute_sha256(ep).lower() != str(declared_sha).lower():
                raise PackageError("脚本 sha256 与 manifest 声明不符")

        # 全部校验通过 → 原子替换最终目录
        if os.path.exists(dest_dir):
            if os.path.isdir(dest_dir):
                shutil.rmtree(dest_dir, ignore_errors=True)
            else:
                os.remove(dest_dir)
        os.replace(temp_dir, dest_dir)
        temp_dir = None
        log1("脚本包已解包: {} -> {}".format(manifest.get("id", ""), dest_dir))
        return manifest
    except PackageError:
        raise
    except Exception as e:
        raise PackageError("解包失败: {}".format(e))
    finally:
        try:
            zf.close()
        except Exception:
            pass
        if temp_dir and os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)


def install_package(pkg_path, dest_dir, expected_sha256=None,
                    flatten_images=True):
    """面向市场的高层安装。

    - unpack 到 dest_dir；
    - flatten_images=True 时把 images/ 下所有 .png 复制为 dest_dir/<name>.png，
      使 engine 的 `{script_dir}/{名}.png` 可直接命中；
    - 返回 manifest 追加 `_install` 键：
      {"script_path": <绝对路径>, "dir": <安装目录>, "files": [...]}。
    """
    manifest = unpack(pkg_path, dest_dir, expected_sha256=expected_sha256)
    dest_dir = os.path.abspath(dest_dir)

    entry_rel = manifest.get("entry", "")
    script_path = ""
    if entry_rel:
        script_path = os.path.join(dest_dir, *entry_rel.split("/"))
    script_dir = os.path.dirname(script_path) if script_path else dest_dir

    # 图片拍平：使 engine 的 "{script_dir}/{名}.png" 可直接命中 (资源与脚本同目录)。
    # 同时写入「脚本所在目录」与「安装根目录」两处，兼容两种 script_dir 口径
    # (entry 位于 scripts/ 子目录 vs 安装根目录)，任一口径下均可命中。
    img_dir = os.path.join(dest_dir, "images")
    if flatten_images and os.path.isdir(img_dir):
        targets = []
        for d in (dest_dir, script_dir):
            if d and d not in targets:
                targets.append(d)
        try:
            for fn in os.listdir(img_dir):
                if not fn.lower().endswith(".png"):
                    continue
                src = os.path.join(img_dir, fn)
                for d in targets:
                    shutil.copy2(src, os.path.join(d, fn))
        except Exception as e:
            raise PackageError("图片拍平失败: {}".format(e))

    files = []
    try:
        for root, _dirs, names in os.walk(dest_dir):
            for fn in names:
                files.append(os.path.join(root, fn))
    except Exception:
        pass

    manifest["_install"] = {
        "script_path": script_path,
        "dir": dest_dir,
        "files": files,
    }
    return manifest
