# -*- coding: utf-8 -*-
"""tools/_test_script_package.py — 脚本市场 v2「批次1：基础设施层」验收测试。

无 UI、默认无网络。仅覆盖本批次交付物：
    src/script_package.py (新)   src/marketplace.py (扩展)   src/accounts.py (新)
    src/utils.py (TOKENS 增量)   src/state.py (6 个非敏感键)

覆盖：pack/unpack round-trip、sha256 前置校验、zip-slip、软链、白名单、限额、
manifest 校验、marketplace 兼容解析与 install_package 图片拍平、凭据往返且不落
config.json、日志不含 token 明文。

退出码 0(全过/仅 WARN) / 1(存在 FAIL)。输出行前缀: [OK] / [WARN] / [FAIL]。
运行：python tools/_test_script_package.py
(cmd.exe 下中文乱码可先单独执行 `set PYTHONIOENCODING=utf-8`，勿用 && 串联。)
"""
import os
import sys
import base64
import json
import shutil
import tempfile
import zipfile
import hashlib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import script_package as sp          # noqa: E402
import marketplace as mp             # noqa: E402
import accounts as acc               # noqa: E402
import state                         # noqa: E402
import utils as u                    # noqa: E402

_FAILS = []
_WARNS = []

_PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFAAH/"
    "q842iQAAAABJRU5ErkJggg==")

_FAKE_TOKEN = "ghp_0123456789abcdef0123456789abcdef0123"

_META = {
    "id": "test_sample_pkg",
    "name": "测试示例包",
    "description": "批次1自测用示例脚本包（含一个 png 资源）",
    "category": "办公",
    "author": "ACRPA-test",
    "version": "1.0.0",
    "tags": ["测试", "示例"],
    "icon": "🧪",
    "requires": [],
}


# ── 测试工具 ──

def ok(msg):
    print("[OK] " + msg)


def warn(msg):
    _WARNS.append(msg)
    print("[WARN] " + msg)


def fail(msg):
    _FAILS.append(msg)
    print("[FAIL] " + msg)


def check(cond, msg):
    if cond:
        ok(msg)
    else:
        fail(msg)


def expect_pkg_error(fn, needle, label):
    """期望 fn() raise PackageError；needle 非空时要求异常文本含该子串。"""
    try:
        fn()
    except sp.PackageError as e:
        text = str(e)
        if needle and needle not in text:
            fail("{}: 已拒绝但原因 %r 未含 %r".format(label, text, needle))
        else:
            ok("{}: 已拒绝 → {}".format(label, text[:90]))
        return True
    except Exception as e:
        fail("{}: 抛出了非 PackageError: {!r}".format(label, e))
        return False
    fail("{}: 期望拒绝但未拒绝".format(label))
    return False


def write_zip(path, entries=None, symlinks=None):
    """entries: [(name, bytes)]；symlinks: [(name, target)] 以软链模式位写入。"""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in (entries or []):
            zf.writestr(name, data)
        for name, target in (symlinks or []):
            zi = zipfile.ZipInfo(name)
            zi.external_attr = (0xA1FF << 16)   # S_IFLNK | 0777
            zf.writestr(zi, target)


def _file_url(path):
    """本地文件 → file:// URL（包模式测试用，免网络）。"""
    try:
        import pathlib
        return pathlib.Path(os.path.abspath(path)).as_uri()
    except Exception:
        return "file:///" + os.path.abspath(path).replace("\\", "/")


def no_temp_left(parent):
    try:
        for n in os.listdir(parent):
            if n.startswith(".") and ".tmp-" in n:
                return False
    except Exception:
        return True
    return True


class _Resp(object):
    """极简假响应，用于 verify_token 分支测试。"""

    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload


class _CapLog(object):
    def __init__(self):
        self.msgs = []

    def put(self, msg, tag=None, caller_file="", caller_func="", level=0):
        self.msgs.append(str(msg))


# ══════════════════════════════════════════════════════════════════════
# 0. utils TOKENS / state 配置键
# ══════════════════════════════════════════════════════════════════════

def test_tokens():
    print("\n== 0. utils TOKENS 与访问器 ==")
    # 设计 §2.7 的 TOKENS 以 sp_* 族承载间距（无裸键 "sp"），此处按其真值校验
    check(any(k.startswith("sp_") for k in u.TOKENS), "TOKENS 含 sp_* 间距族")
    for k in ("radius", "ctrl_h", "gap", "card_pad", "icon_md", "bar_h"):
        check(k in u.TOKENS, "TOKENS 含 '{}'".format(k))
    check(u.sp("sp_md") == u.scaled(12), "sp('sp_md') == scaled(12)")
    check(u.radius() == u.scaled(6), "radius() == scaled(6)")
    # docs/UI美化设计方案.md §3 把 ctrl_h 由 26 收到 24 (ctrl_h_lg 30→28)
    check(u.ctrl_h() == u.scaled(24), "ctrl_h() == scaled(24)")
    check(u.gap() == u.scaled(8), "gap() == scaled(8)")
    check(u.icon_size("icon_lg") == u.scaled(40), "icon_size('icon_lg') == scaled(40)")
    check(u.tk_px(10) == u.scaled(10), "tk_px(10) == scaled(10)")
    check(u.sp(7) == u.scaled(7), "sp(数字) 走 scaled")
    check(u.sp("no_such_key") == 1, "sp(未知键) 退化为下限 1（不抛异常）")

    old = getattr(state, "UI_SCALE", 1.0)
    try:
        u.set_ui_scale(0.8)
        v08 = u.sp("sp_lg")
        u.set_ui_scale(1.5)
        v15 = u.sp("sp_lg")
    finally:
        u.set_ui_scale(old)
    check(v15 > v08, "sp() 随 ui_scale 增大 (0.8→{}, 1.5→{})".format(v08, v15))

    # 既有符号零改动
    check(isinstance(u.PAD, dict) and u.PAD["padx"] == 12 and u.PAD["pady"] == 6,
          "既有 PAD 未被改动")
    check(isinstance(u.PI, dict) and u.PI["padx"] == 10 and u.PI["pady"] == 5,
          "既有 PI 未被改动")
    check(u.fit_pt(10) > 0 and callable(u.themed), "既有 fit_pt/themed 仍可用")


def test_state_schema():
    print("\n== 0b. state 新增 6 个非敏感配置键 ==")
    keys = [k for k, _, _ in state._config_schema]
    expect = ["market_provider", "market_username", "market_auto_check_update",
              "market_install_dir", "market_last_category", "market_index_cache_ttl"]
    for k in expect:
        check(k in keys, "_config_schema 含 '{}'".format(k))
    check(not any(("token" in k) for k in keys),
          "config schema 内不存在任何 *token* 键（敏感项不入 config.json）")


# ══════════════════════════════════════════════════════════════════════
# 1. pack / unpack round-trip
# ══════════════════════════════════════════════════════════════════════

def test_roundtrip(work):
    print("\n== 1. pack → unpack round-trip ==")
    src_dir = os.path.join(work, "srcfiles")
    os.makedirs(src_dir, exist_ok=True)
    sample = os.path.join(src_dir, "脚本模板.xls")
    tmpl = os.path.join(ROOT, "template", "脚本模板.xls")
    if not os.path.exists(tmpl):
        warn("样本 {} 不存在，改用伪造 .xls".format(tmpl))
        with open(sample, "wb") as f:
            f.write(b"PK\x03\x04fake-xls-bytes" + b"0" * 200)
    else:
        shutil.copy2(tmpl, sample)
    png = os.path.join(src_dir, "demo.png")
    with open(png, "wb") as f:
        f.write(_PNG_1PX)
    with open(os.path.join(src_dir, "README.md"), "w", encoding="utf-8") as f:
        f.write("# 测试包\n\n批次1 round-trip 用 README。\n")

    pkg = os.path.join(work, "test_sample_pkg-1.0.0.acrpapkg")
    pkg_path, digest = sp.pack(sample, meta=_META, out_path=pkg)

    check(os.path.exists(pkg_path), "pack() 生成包文件: {}".format(os.path.basename(pkg_path)))
    with open(pkg_path, "rb") as f:
        indep = hashlib.sha256(f.read()).hexdigest()
    check(digest == indep, "pack() 返回 sha256 == 独立计算值")
    check(digest == sp.compute_sha256(pkg_path), "compute_sha256(pkg) == pack 返回值")

    man = sp.read_manifest(pkg_path)
    check(man.get("id") == _META["id"], "manifest.id 正确")
    check(man.get("entry") == "scripts/脚本模板.xls", "manifest.entry == scripts/脚本模板.xls")
    check(man.get("images") == ["images/demo.png"], "manifest.images == ['images/demo.png']")
    check(man.get("readme") == "README.md", "manifest.readme == README.md")
    check(len(man.get("script_sha256") or "") == 64, "manifest.script_sha256 为 64 位 hex")

    out_dir = os.path.join(work, "unpacked")
    man2 = sp.unpack(pkg_path, out_dir, expected_sha256=digest)
    check(man2.get("id") == _META["id"], "unpack 返回 manifest.id 一致")

    with open(sample, "rb") as f:
        orig = f.read()
    with open(os.path.join(out_dir, "scripts", "脚本模板.xls"), "rb") as f:
        got = f.read()
    check(orig == got, "解包后 .xls 字节与原始一致 ({} bytes)".format(len(got)))
    check(os.path.exists(os.path.join(out_dir, "images", "demo.png")), "images/demo.png 已还原")
    check(os.path.exists(os.path.join(out_dir, "README.md")), "README.md 已还原")
    check(os.path.exists(os.path.join(out_dir, "manifest.json")), "manifest.json 已还原")
    check(no_temp_left(work), "round-trip 后无残留临时目录")

    # install_package：图片拍平到脚本同目录与安装根目录
    inst = os.path.join(work, "installed")
    man3 = sp.install_package(pkg_path, inst, expected_sha256=digest)
    inst_info = man3.get("_install") or {}
    check(os.path.exists(os.path.join(inst, "demo.png")),
          "install_package: 图片已拍平到安装根目录")
    check(os.path.exists(os.path.join(inst, "scripts", "demo.png")),
          "install_package: 图片已拍平到脚本同目录 (engine {script_dir}/{名}.png 可命中)")
    check(bool(inst_info.get("script_path")) and os.path.exists(inst_info["script_path"]),
          "install_package: _install.script_path 指向存在的 .xls")
    check(len(inst_info.get("files") or []) >= 4, "install_package: _install.files 清单非空")

    return pkg_path, digest


# ══════════════════════════════════════════════════════════════════════
# 2. sha256 前置校验
# ══════════════════════════════════════════════════════════════════════

def test_sha256(work, pkg_path, digest):
    print("\n== 2. sha256 前置校验 ==")
    expect_pkg_error(
        lambda: sp.unpack(pkg_path, os.path.join(work, "bad_sha"), expected_sha256="0" * 64),
        "sha256 校验失败", "expected_sha256 不符")

    tampered = os.path.join(work, "tampered.acrpapkg")
    with open(pkg_path, "rb") as f:
        blob = bytearray(f.read())
    blob[len(blob) // 2] ^= 0xFF      # 篡改中间一个字节
    with open(tampered, "wb") as f:
        f.write(bytes(blob))
    expect_pkg_error(
        lambda: sp.unpack(tampered, os.path.join(work, "tampered_out"),
                          expected_sha256=digest),
        "sha256 校验失败", "篡改一字节后 unpack")
    check(not os.path.exists(os.path.join(work, "tampered_out")),
          "sha256 不符时未产生任何解包目录")
    check(no_temp_left(work), "sha256 拒绝后无残留临时目录")


# ══════════════════════════════════════════════════════════════════════
# 3. zip-slip / 软链 / 白名单
# ══════════════════════════════════════════════════════════════════════

def test_zip_slip(work):
    print("\n== 3. zip-slip / 软链 / 白名单 ==")
    cases = [
        ("dotdot", [("../evil.txt", b"pwn")], ".."),
        ("abs", [("/abs/evil.xls", b"pwn")], "绝对路径"),
        ("drive", [("C:\\evil.xls", b"pwn")], "盘符"),
        ("sandwich", [("a/../../evil.xls", b"pwn")], ".."),
    ]
    for label, entries, needle in cases:
        zp = os.path.join(work, "slip_{}.zip".format(label))
        write_zip(zp, entries=entries)
        expect_pkg_error(lambda p=zp, l=label: sp.unpack(p, os.path.join(work, "slip_out_" + l)),
                         needle, "zip-slip {}".format(label))
    # 越界文件确实未被写入目标根之外（dest_dir 的父目录 = work）
    check(not os.path.exists(os.path.join(work, "evil.txt")),
          "zip-slip: 目标目录外无 evil.txt 落盘")
    check(not os.path.exists(os.path.join(os.path.dirname(work), "evil.txt")),
          "zip-slip: 更上层目录亦无 evil.txt 落盘")
    check(no_temp_left(work), "zip-slip 拒绝后无残留临时目录")

    # 符号链接成员
    zp = os.path.join(work, "slip_symlink.zip")
    write_zip(zp, entries=[("manifest.json", b"{}")],
              symlinks=[("scripts/link.xls", "/etc/passwd")])
    expect_pkg_error(lambda: sp.unpack(zp, os.path.join(work, "slip_link")),
                     "符号链接", "符号链接成员")

    # 白名单越界
    zp = os.path.join(work, "slip_white.zip")
    write_zip(zp, entries=[("manifest.json", b"{}"), ("bin/x.exe", b"MZ")])
    expect_pkg_error(lambda: sp.unpack(zp, os.path.join(work, "slip_white")),
                     "白名单", "白名单外成员 bin/x.exe")


# ══════════════════════════════════════════════════════════════════════
# 4. 限额：文件数 / 解压体积 / 单文件 / zip bomb
# ══════════════════════════════════════════════════════════════════════

def test_limits(work):
    print("\n== 4. 限额 (文件数/体积/单文件/压缩比) ==")
    # 文件数：真实构造 260 个成员 (> MAX_FILE_COUNT=256)
    zp = os.path.join(work, "limit_count.zip")
    many = [("scripts/f{:03d}.xls".format(i), b"x") for i in range(260)]
    write_zip(zp, entries=many)
    expect_pkg_error(lambda: sp.unpack(zp, os.path.join(work, "limit_count")),
                     "条目数超限", "文件数超限 (260 > 256)")

    # 解压总体积：缩小阈值后触发
    old_total = sp.MAX_UNPACK_BYTES
    try:
        sp.MAX_UNPACK_BYTES = 1024
        zp = os.path.join(work, "limit_bytes.zip")
        write_zip(zp, entries=[("scripts/a.xls", b"A" * 2048)])
        expect_pkg_error(lambda: sp.unpack(zp, os.path.join(work, "limit_bytes")),
                         "总体积超限", "解压总体积超限 (阈值缩至 1024B)")
    finally:
        sp.MAX_UNPACK_BYTES = old_total

    # 单文件：缩小阈值后触发
    old_single = sp.MAX_SINGLE_FILE
    try:
        sp.MAX_SINGLE_FILE = 8
        zp = os.path.join(work, "limit_single.zip")
        write_zip(zp, entries=[("scripts/a.xls", b"A" * 100)])
        expect_pkg_error(lambda: sp.unpack(zp, os.path.join(work, "limit_single")),
                         "单文件超限", "单文件超限 (阈值缩至 8B)")
    finally:
        sp.MAX_SINGLE_FILE = old_single

    # zip bomb：>1MB 且压缩比过高
    zp = os.path.join(work, "limit_bomb.zip")
    write_zip(zp, entries=[("scripts/bomb.xls", b"\x00" * (2 * 1024 * 1024))])
    expect_pkg_error(lambda: sp.unpack(zp, os.path.join(work, "limit_bomb")),
                     "zip bomb", "疑似 zip bomb (2MB 全零高压缩比)")

    check(no_temp_left(work), "限额拒绝后无残留临时目录")


# ══════════════════════════════════════════════════════════════════════
# 5. manifest 校验
# ══════════════════════════════════════════════════════════════════════

def test_manifest(work):
    print("\n== 5. manifest 校验 ==")
    sample = os.path.join(work, "srcfiles", "脚本模板.xls")
    base = sp.build_manifest(sample, _META, images=[])
    check(sp.manifest_errors(base) == [], "合法 manifest: manifest_errors 为空")
    sp.validate_manifest(base, app_version="0.1.26")
    ok("合法 manifest: validate_manifest 通过")

    def mut(**kv):
        m = dict(base)
        m.update(kv)
        return m

    cases = [
        ("缺 id", {k: v for k, v in base.items() if k != "id"}, "id"),
        ("非法 category", mut(category="乱写"), "category"),
        ("builtin_ 前缀 id", mut(id="builtin_x"), "builtin_"),
        ("非法 version", mut(version="v1"), "version"),
        ("entry 前缀非法", mut(entry="other/a.xls"), "scripts/"),
        ("manifest_version=2", mut(manifest_version=2), "manifest_version"),
    ]
    for label, man, needle in cases:
        errs = sp.manifest_errors(man)
        hit = any(needle in e for e in errs)
        check(hit, "manifest_errors 捕获「{}」".format(label))
        expect_pkg_error(lambda m=man: sp.validate_manifest(m), needle,
                         "validate_manifest 拒绝「{}」".format(label))

    # min_app_version 高于当前版本 → 拒绝并提示升级
    expect_pkg_error(
        lambda: sp.validate_manifest(mut(min_app_version="99.0.0"), app_version="0.1.26"),
        "请升级", "min_app_version 99.0.0 > 当前 0.1.26 被拒")

    # entry 在包内不存在
    man = sp.build_manifest(sample, _META, images=[])
    man["entry"] = "scripts/missing.xls"
    zp = os.path.join(work, "no_entry.acrpapkg")
    write_zip(zp, entries=[("manifest.json", json.dumps(man, ensure_ascii=False).encode("utf-8")),
                           ("scripts/脚本模板.xls", b"x")])
    expect_pkg_error(lambda: sp.unpack(zp, os.path.join(work, "no_entry")),
                     "entry 在包内不存在", "entry 不在包内")

    # manifest_version=2 在 unpack 路径也被拒
    man2 = sp.build_manifest(sample, _META, images=[])
    man2["manifest_version"] = 2
    zp2 = os.path.join(work, "mv2.acrpapkg")
    write_zip(zp2, entries=[("manifest.json", json.dumps(man2, ensure_ascii=False).encode("utf-8")),
                            ("scripts/脚本模板.xls", b"x")])
    expect_pkg_error(lambda: sp.unpack(zp2, os.path.join(work, "mv2")),
                     "manifest_version", "unpack 拒绝 manifest_version=2")

    # images 声明但包内缺失
    man3 = sp.build_manifest(sample, _META, images=[])
    man3["images"] = ["images/nope.png"]
    zp3 = os.path.join(work, "no_img.acrpapkg")
    write_zip(zp3, entries=[("manifest.json", json.dumps(man3, ensure_ascii=False).encode("utf-8")),
                            ("scripts/脚本模板.xls", b"x")])
    expect_pkg_error(lambda: sp.unpack(zp3, os.path.join(work, "no_img")),
                     "images 声明的文件在包内不存在", "images 声明缺失文件")


# ══════════════════════════════════════════════════════════════════════
# 6. marketplace 扩展（离线）
# ══════════════════════════════════════════════════════════════════════

def test_marketplace(work, pkg_path, digest):
    print("\n== 6. marketplace 扩展 (离线) ==")
    legacy = mp.ScriptInfo({"id": "legacy_x", "name": "旧条目",
                            "filename": "legacy_x", "version": "1.2"})
    check(legacy.is_package is False, "旧 filename-only 条目 is_package=False")
    check(mp.resolve_download_url(legacy) ==
          mp.MARKETPLACE_REPO + "/scripts/legacy_x.xls",
          "旧模式下载直链与原实现完全一致（零回归）")
    check(legacy.to_dict().get("filename") == "legacy_x" and "pkg" in legacy.to_dict(),
          "to_dict 兼容输出原字段并补 v2 键")

    v2 = mp.ScriptInfo({"id": "pkg_x", "name": "包条目", "version": "2.0.0",
                        "pkg": "packages/pkg_x-2.0.0.acrpapkg",
                        "sha256": "ab" * 32, "size": 1234,
                        "min_app_version": "0.1.26"})
    check(v2.is_package is True, "v2 条目 is_package=True")
    check(mp.resolve_download_url(v2) ==
          mp.MARKETPLACE_PKG_DIR + "/pkg_x-2.0.0.acrpapkg",
          "包模式直链 == MARKETPLACE_PKG_DIR/<pkg>")
    check(mp.ScriptInfo.from_dict({"id": "y"}).id == "y", "from_dict 与 __init__ 对称")
    check(mp.ScriptInfo.from_dict({}).is_package is False, "from_dict(空) 容错")

    # check_update
    saved_cache = mp._index_cache
    try:
        mp._index_cache = [legacy, v2]
        check(mp.check_update("pkg_x", "1.0.0") is True, "check_update: 远端 2.0.0 > 本地 1.0.0")
        check(mp.check_update("pkg_x", "2.0.0") is False, "check_update: 版本相等 → False")
        check(mp.check_update("legacy_x", "1.0") is True, "check_update: 旧条目 1.2 > 1.0")
        check(mp.check_update("nope", "1.0") is False, "check_update: 未知 id → False")
    finally:
        mp._index_cache = saved_cache

    # install_package: <dest_root>/<id> + 图片拍平
    root = os.path.join(work, "market_scripts")
    man = mp.install_package(pkg_path, root, expected_sha256=digest)
    dest = os.path.join(root, _META["id"])
    check(man.get("id") == _META["id"], "marketplace.install_package 返回 manifest.id")
    check(os.path.exists(os.path.join(dest, "demo.png")), "安装落点 <dest_root>/<id>/ 且图片已拍平")
    sp_path = (man.get("_install") or {}).get("script_path")
    check(bool(sp_path) and os.path.exists(sp_path), "返回 .xls 绝对路径存在")
    check(os.path.dirname(sp_path) == os.path.join(dest, "scripts"),
          "脚本落点: <dest_root>/<id>/scripts/<entry>.xls")

    # 包模式缺 sha256 → 拒绝（不静默降级；直接验证内部实现，不触网）
    nosha = mp.ScriptInfo({"id": "nosha", "name": "n", "version": "1.0",
                           "pkg": "packages/nosha-1.0.acrpapkg"})
    try:
        mp._download_and_install_package(nosha, work)
        fail("包模式缺 sha256 未拒绝")
    except mp.MarketplaceError as e:
        check("sha256" in str(e), "包模式缺 sha256 → 拒绝 ({})".format(str(e)[:60]))

    # 直链优先级：url > pkg > filename
    check(mp.resolve_download_url(mp.ScriptInfo({"url": "http://u/x"})) == "http://u/x",
          "resolve_download_url: url 优先")
    check(mp.resolve_download_url(mp.ScriptInfo({})) == "",
          "resolve_download_url: 无 url/pkg/filename → 空串")

    # 包模式端到端：file:// 直链 → 下载 → sha256 校验 → 解包安装 → 返回 .xls
    pkg_info = mp.ScriptInfo({
        "id": _META["id"], "name": "包模式端到端", "version": "1.0.0",
        "pkg": "packages/test_sample_pkg-1.0.0.acrpapkg",
        "sha256": digest, "size": os.path.getsize(pkg_path),
        "url": _file_url(pkg_path),
    })
    dl_dir = os.path.join(work, "dl")
    saved2 = mp._index_cache
    try:
        mp._index_cache = [pkg_info]
        got = mp.download_script(_META["id"], dl_dir)
    finally:
        mp._index_cache = saved2
    check(bool(got) and os.path.exists(got), "download_script 包模式 → 返回存在的 .xls 路径")
    check(os.path.dirname(got) ==
          os.path.join(dl_dir, "market_scripts", _META["id"], "scripts"),
          "包模式安装落点 <save_dir>/market_scripts/<id>/scripts/")
    check(os.path.exists(os.path.join(os.path.dirname(got), "demo.png")),
          "包模式: 图片与脚本同目录 → engine {script_dir}/{名}.png 可命中")

    # 包模式 sha256 不符 → 拒绝
    bad_info = mp.ScriptInfo({"id": _META["id"], "name": "坏包", "version": "1.0.0",
                              "pkg": "packages/x.acrpapkg", "sha256": "0" * 64,
                              "url": _file_url(pkg_path)})
    saved3 = mp._index_cache
    try:
        mp._index_cache = [bad_info]
        try:
            mp.download_script(_META["id"], os.path.join(work, "dl_bad"))
            fail("包模式 sha256 不符未拒绝")
        except mp.MarketplaceError as e:
            check("sha256" in str(e), "包模式 sha256 不符 → 拒绝 ({})".format(str(e)[:60]))
    finally:
        mp._index_cache = saved3

    # 旧 builtin 生成路径零回归
    saved4 = mp._index_cache
    fp = None
    try:
        mp._index_cache = None
        fp = mp.download_script("builtin_1", os.path.join(work, "builtin"))
    except Exception as e:
        warn("builtin 生成跳过 (可能缺 xlwt): {!r}".format(e))
    finally:
        mp._index_cache = saved4
    if fp:
        check(os.path.exists(fp), "builtin 脚本生成路径零回归")


# ══════════════════════════════════════════════════════════════════════
# 7. 凭据往返 + 不落 config.json + 日志脱敏
# ══════════════════════════════════════════════════════════════════════

def test_accounts(work):
    print("\n== 7. accounts 凭据 / 脱敏 / config.json ==")
    cap = _CapLog()
    old_tlog = u._tlog
    u._tlog = cap
    saved_ok = False
    try:
        acc.save_token("gitee", _FAKE_TOKEN)
        saved_ok = True
    except acc.AccountError as e:
        warn("Windows 凭据库不可用，跳过凭据往返: {}".format(e))
    finally:
        pass

    if saved_ok:
        check(acc.get_token("gitee") == _FAKE_TOKEN, "cred 往返: get_token == 写入值")
        check(acc.has_token("gitee") is True, "has_token == True")
        check("gitee" in acc.list_logged_in(), "list_logged_in 含 gitee")
        check(acc._cred_target("gitee") == "ACRPA/market/gitee_token",
              "凭据 target == ACRPA/market/gitee_token")
        acc.clear_token("gitee")
        check(acc.get_token("gitee") is None, "clear_token 后 get_token == None")
        check(acc.has_token("gitee") is False, "clear_token 后 has_token == False")
        acc.clear_token("gitee")
        ok("clear_token 幂等（重复调用不抛异常）")

    # 非法 provider
    try:
        acc.get_token("gitlab")
        fail("非法 provider 未拒绝")
    except acc.AccountError:
        ok("非法 provider → AccountError")

    # verify_token 分支（monkeypatch 网络）
    orig_get = acc._http_get
    try:
        acc._http_get = lambda *a, **k: _Resp(401)
        try:
            acc.verify_token("gitee", _FAKE_TOKEN)
            fail("401 未抛 AccountError")
        except acc.AccountError as e:
            check("401" in str(e) and _FAKE_TOKEN not in str(e),
                  "verify_token 401 → AccountError 且不含 token 明文")

        acc._http_get = lambda *a, **k: _Resp(200, {"login": "yohoten", "id": 7,
                                                    "avatar_url": "http://x/a.png"})
        info = acc.verify_token("gitee", _FAKE_TOKEN)
        check(info.get("login") == "yohoten" and info.get("provider") == "gitee",
              "verify_token 200 → 归一化 user_info")

        def boom(*a, **k):
            raise RuntimeError("conn failed token={}".format(_FAKE_TOKEN))
        acc._http_get = boom
        try:
            acc.verify_token("github", _FAKE_TOKEN)
            fail("网络异常未抛 AccountError")
        except acc.AccountError as e:
            check(_FAKE_TOKEN not in str(e) and "***" in str(e),
                  "verify_token 网络异常 → 已脱敏({})".format(str(e)[:80]))
    finally:
        acc._http_get = orig_get

    # 无 token
    try:
        acc.verify_token("gitee", "")
        fail("空 token 未拒绝")
    except acc.AccountError:
        ok("空 token → AccountError")

    # 日志脱敏：整个账号流程日志中不得出现 token 明文
    joined = "\n".join(cap.msgs)
    check(_FAKE_TOKEN not in joined,
          "日志脱敏: {} 条日志中均无 token 明文".format(len(cap.msgs)))

    # config.json 不落 token
    orig_path = state.CONFIG_PATH
    orig_cw, orig_cr, orig_cd = state._cred_write, state._cred_read, state._cred_delete
    try:
        state._cred_write = lambda *a, **k: True      # 避免动到真实 api_key 凭据
        state._cred_read = lambda *a, **k: ""
        state._cred_delete = lambda *a, **k: None
        cfg = os.path.join(work, "config.json")
        state.CONFIG_PATH = cfg
        state.MARKET_PROVIDER = "gitee"
        state.MARKET_USERNAME = "tester"
        state.API_KEY = ""
        state.save_config()
        text = open(cfg, "r", encoding="utf-8").read()
        check(_FAKE_TOKEN not in text, "config.json 不含 token 明文")
        check("_token" not in text, "config.json 无任何 *_token 键")
        okv = True
        for k in ("market_provider", "market_username", "market_auto_check_update",
                  "market_install_dir", "market_last_category", "market_index_cache_ttl"):
            if '"{}"'.format(k) not in text:
                okv = False
                fail("config.json 缺失键 {}".format(k))
        if okv:
            ok("config.json 含全部 6 个市场非敏感键")

        # load_config 缺键用默认值
        with open(cfg, "w", encoding="utf-8") as f:
            json.dump({"dark_mode": True}, f)
        state.MARKET_PROVIDER = "github"
        state.MARKET_INDEX_CACHE_TTL = 123
        state.load_config()
        check(state.MARKET_PROVIDER == "gitee", "load_config: 缺键用默认 market_provider=gitee")
        check(state.MARKET_INDEX_CACHE_TTL == 3600,
              "load_config: 缺键用默认 market_index_cache_ttl=3600")
        check(state.MARKET_LAST_CATEGORY == "全部", "load_config: 缺键用默认 market_last_category")
    finally:
        state.CONFIG_PATH = orig_path
        state._cred_write, state._cred_read, state._cred_delete = orig_cw, orig_cr, orig_cd
        u._tlog = old_tlog


# ══════════════════════════════════════════════════════════════════════
# main
# ══════════════════════════════════════════════════════════════════════

def main():
    work = tempfile.mkdtemp(prefix="acrpa_pkg_test_")
    print("工作目录: {}".format(work))
    try:
        test_tokens()
        test_state_schema()
        pkg_path, digest = test_roundtrip(work)
        test_sha256(work, pkg_path, digest)
        test_zip_slip(work)
        test_limits(work)
        test_manifest(work)
        test_marketplace(work, pkg_path, digest)
        test_accounts(work)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    print("\n" + "=" * 60)
    print("结果: {} 项通过, {} 项 FAIL, {} 项 WARN".format(
        "全部" if not _FAILS else "部分", len(_FAILS), len(_WARNS)))
    for m in _FAILS:
        print("  [FAIL] " + m)
    for m in _WARNS:
        print("  [WARN] " + m)
    if _FAILS:
        return 1
    print("所有批次1基础设施验收点通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
