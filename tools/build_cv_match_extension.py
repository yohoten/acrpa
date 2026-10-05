#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""build_cv_match_extension — 打包首个上线扩展 ``cv.match`` (路线图 阶段二新增项③)。

用法::

    .venv\\Scripts\\python.exe tools\\build_cv_match_extension.py
    .venv\\Scripts\\python.exe tools\\build_cv_match_extension.py --update-index

功能
----
* 读 ``extensions_src/cv_match/`` → 回填 ``extension.json`` 的
  ``size`` / ``platform`` / ``arch`` / ``python_tag`` (按当前解释器);
* 打包为 ``addon-cv.match-<version>-<platform>-<arch>-<python_tag>.zip``
  (布局: ``extension.json`` + ``pure/**``, 仅白名单前缀);
* **确定性构建**: 固定时间戳 (1980-01-01) + 固定压缩级别 + 条目排序, 使**同一源码
  产出稳定 sha256** (便于把哈希固化进 ``src/extensions/index.py`` 白名单);
* 计算 sha256 (复用 ``script_package.compute_sha256``) 并打印包路径 + sha256;
* ``--update-index``: 把 sha256 写入 ``src/extensions/index.py`` 的 ``WHITELIST``
  (其余内容与格式不变); 缺省**只打印**。

说明: 本脚本名不以 ``_test_`` 开头, 不进 CI; 供开发者本地构图 / 固化白名单。
"""
import os
import re
import sys
import json
import shutil
import zipfile
import hashlib
import platform

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(BASE, "src")
EXT_SRC = os.path.join(BASE, "extensions_src", "cv_match")
INDEX_PY = os.path.join(SRC, "extensions", "index.py")
DEFAULT_OUT = os.path.join(BASE, "dist")

EXT_ID = "cv.match"
EXT_VERSION = "1.0.0"

# 确定性构建: 固定时间戳 (ZIP 最早可表示时间) 与压缩级别。
_FIXED_DATE = (1980, 1, 1, 0, 0, 0)
_FIXED_ATTR = (0o644 << 16)
_COMPRESS_LEVEL = 9


# ── 环境回填 ──────────────────────────────────────────────────────────
def _arch():
    """按当前解释器架构回填 (manifest 允许 amd64 / arm64 / any)。"""
    mach = (platform.machine() or "").lower()
    if mach in ("amd64", "x86_64"):
        return "amd64"
    if mach in ("arm64", "aarch64"):
        return "arm64"
    return "any"


def _python_tag():
    """按当前解释器回填 (cp<major><minor>)。"""
    return "cp{}{}".format(sys.version_info[0], sys.version_info[1])


def compute_sha256(path):
    """复用 ``script_package.compute_sha256``; 取用失败则本地兜底。"""
    if SRC not in sys.path:
        sys.path.insert(0, SRC)
    try:
        import script_package
        return script_package.compute_sha256(path)
    except Exception:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for block in iter(lambda: f.read(1024 * 1024), b""):
                h.update(block)
        return h.hexdigest()


# ── 收集 / 打包 ───────────────────────────────────────────────────────
def _collect_payload():
    """→ [(arcname, bytes)]: ``pure/**`` 下的全部文件 (排序, posix 相对路径)。"""
    pure_dir = os.path.join(EXT_SRC, "pure")
    if not os.path.isdir(pure_dir):
        raise RuntimeError("缺少扩展目录: {}".format(pure_dir))
    out = []
    for root, dirs, names in os.walk(pure_dir):
        dirs.sort()
        for n in sorted(names):
            full = os.path.join(root, n)
            rel = os.path.relpath(full, EXT_SRC).replace(os.sep, "/")
            with open(full, "rb") as f:
                out.append((rel, f.read()))
    out.sort(key=lambda t: t[0])
    return out


def _fill_manifest(manifest, size):
    m = dict(manifest)
    m["id"] = EXT_ID
    m["version"] = EXT_VERSION
    m["platform"] = "win"
    m["arch"] = _arch()
    m["python_tag"] = _python_tag()
    m["size"] = int(size)
    return m


def _write_zip(zip_path, entries):
    """确定性写 zip: 固定 date_time / external_attr / 压缩级别。"""
    if os.path.exists(zip_path):
        os.remove(zip_path)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED,
                         compresslevel=_COMPRESS_LEVEL) as zf:
        for arc, data in sorted(entries, key=lambda t: t[0]):
            zi = zipfile.ZipInfo(arc, date_time=_FIXED_DATE)
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = _FIXED_ATTR
            zi.create_system = 0
            zf.writestr(zi, data, compress_type=zipfile.ZIP_DEFLATED,
                        compresslevel=_COMPRESS_LEVEL)


def build(out_dir=None):
    """打包扩展 → (zip_path, sha256)。确定性: 同源码同解释器产出稳定 sha256。"""
    with open(os.path.join(EXT_SRC, "extension.json"), "r", encoding="utf-8") as f:
        manifest = json.load(f)

    payload = _collect_payload()
    size = sum(len(d) for _n, d in payload)
    m = _fill_manifest(manifest, size)
    raw = json.dumps(m, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")

    entries = [("extension.json", raw)] + payload
    out_dir = out_dir or DEFAULT_OUT
    os.makedirs(out_dir, exist_ok=True)
    name = "addon-{}-{}-{}-{}-{}.zip".format(
        EXT_ID, EXT_VERSION, m["platform"], m["arch"], m["python_tag"])
    zip_path = os.path.join(out_dir, name)
    _write_zip(zip_path, entries)
    return zip_path, compute_sha256(zip_path)


# ── 白名单固化 ────────────────────────────────────────────────────────
def _whitelist_block(sha):
    lines = [
        "WHITELIST = {",
        '    "%s": {' % EXT_ID,
        '        "%s": "%s",' % (EXT_VERSION, sha),
        "    },",
        "}",
    ]
    return "\n".join(lines)


def update_index(sha):
    """把 sha256 写入 index.py 的 WHITELIST (其余内容不变) → True/False(未变化)。"""
    with open(INDEX_PY, "r", encoding="utf-8") as f:
        text = f.read()
    m = re.search(r"^WHITELIST\s*=\s*\{", text, flags=re.M)
    if not m:
        raise RuntimeError("在 {} 中找不到 WHITELIST 定义".format(INDEX_PY))
    start = m.start()
    i = text.index("{", m.start())
    depth = 0
    j = i
    while j < len(text):
        c = text[j]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                break
        j += 1
    new_text = text[:start] + _whitelist_block(sha) + text[j + 1:]
    if new_text == text:
        return False
    tmp = INDEX_PY + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(new_text)
    os.replace(tmp, INDEX_PY)
    return True


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="打包 cv.match 扩展")
    ap.add_argument("--out-dir", default="", help="输出目录 (缺省 <repo>/dist)")
    ap.add_argument("--update-index", action="store_true",
                    help="把 sha256 写入 src/extensions/index.py 的 WHITELIST")
    args = ap.parse_args(argv)

    # 清掉上次输出目录, 保证确定性 (不含陈旧产物混淆)
    zip_path, sha = build(args.out_dir or None)
    print("包路径 : {}".format(zip_path))
    print("sha256 : {}".format(sha))
    print("体积   : {} bytes".format(os.path.getsize(zip_path)))
    if args.update_index:
        changed = update_index(sha)
        print("白名单 : {} ({})".format(
            INDEX_PY, "已更新" if changed else "无变化"))
    else:
        print("白名单 : (未更新; 加 --update-index 固化)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
