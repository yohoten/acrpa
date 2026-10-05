#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""扩展管理器回归自测 (路线图 阶段二新增项②: ``src/extensions/``)。

用法:
    .venv\\Scripts\\python.exe -X utf8 tools\\_test_extensions.py

覆盖 (无网络; 全部在注入的临时 root 内进行, 不污染真实 %LOCALAPPDATA%):
  E1 install 成功      : 正确落地到注入 root、写 installed.json、自检触发能力重探
  E2 uninstall 往返    : 目录消失 + installed.json 更新
  E3 回滚 + quarantine : 自检失败 (能力 BROKEN) → 不写 installed.json、产物入 quarantine/
  E4 sha256            : 期望哈希不符 → 拒绝; 正确哈希 → 通过
  E5 zip-slip/软链/白名单: 危险成员一律拒绝 (复用 script_package 安全原语)
  E6 enable/disable    : 更新 installed.json.enabled 且提示「下次启动生效」
  E7 repair            : 未给包 → 重跑自检; 已装扩展 self-check 通过
  E8 结构化返回        : 各类异常 (路径不存在 / 非 zip / 坏清单) 均返回值而非抛出
  E9 manifest 纯函数   : extension.json 校验 (缺字段 / 非法 id 等)

退出码: 0 = 全部通过 / 1 = 有失败。
"""
import os
import sys
import json
import shutil
import hashlib
import tempfile
import zipfile

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(BASE, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

# 关键: 安装根指向临时目录, 避免污染真实 %LOCALAPPDATA%/ACRPA/extensions。
_TMPROOT = tempfile.mkdtemp(prefix="acrpa_ext_test_")
os.environ["ACRPA_EXTENSIONS_ROOT"] = _TMPROOT

import capabilities                                        # noqa: E402
import extensions                                          # noqa: E402
from extensions import manifest as ext_manifest            # noqa: E402
from extensions import package as ext_package              # noqa: E402
from capabilities import Capability, CapState, ProbeResult  # noqa: E402

_PASS, _FAIL = [], []


def check(cond, msg, detail=""):
    (_PASS if cond else _FAIL).append(msg)
    tail = ("  ({})".format(detail) if detail else "")
    print("[{}] {}{}".format("OK  " if cond else "FAIL", msg, tail))


# ── 测试工具 ──────────────────────────────────────────────────────────

def _manifest(ext_id, version, caps, size=12345, **kw):
    m = {
        "schema": 1, "id": ext_id, "name": "测试扩展 " + ext_id,
        "version": version, "capabilities": list(caps),
        "platform": "win", "arch": "amd64", "python_tag": "cp312",
        "size": size, "min_app_version": "",
    }
    m.update(kw)
    return m


def write_ext_zip(path, manifest, extra_entries=None, symlinks=None):
    """手写扩展包: extension.json + pure/placeholder.py (+ 额外成员/软链)。

    manifest=None → 不写 extension.json (测缺清单); 字符串 → 原样写 (测坏 JSON)。
    """
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        if manifest is not None:
            raw = (manifest if isinstance(manifest, str)
                   else json.dumps(manifest, ensure_ascii=False, indent=2))
            zf.writestr("extension.json", raw)
        zf.writestr("pure/placeholder.py", "# placeholder\n")
        for name, data in (extra_entries or []):
            zf.writestr(name, data)
        for name, target in (symlinks or []):
            zi = zipfile.ZipInfo(name)
            zi.external_attr = (0xA1FF << 16)   # S_IFLNK | 0777
            zf.writestr(zi, target)


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _reg(cap_id, probe, ext_id=None, label=None):
    capabilities.register(Capability(id=cap_id, label=label or cap_id,
                                     ext_id=ext_id, degraded="soft", probe=probe))
    capabilities.enable(cap_id)
    capabilities.refresh(cap_id)


def _expect_ext_error(fn, needle, label):
    """期望 fn() raise extensions.ExtensionError; needle 非空要求异常文本含该子串。"""
    try:
        fn()
    except extensions.ExtensionError as e:
        text = str(e)
        if needle and needle not in text:
            check(False, "{}: 已拒绝但原因未含 {!r}".format(label, needle), text[:90])
        else:
            check(True, "{}: 已拒绝 → {}".format(label, text[:80]))
        return
    except Exception as e:                                  # noqa: BLE001
        check(False, "{}: 抛出了非 ExtensionError: {!r}".format(label, e))
        return
    check(False, "{}: 期望拒绝但未拒绝".format(label))


# ── 测试用例 ──────────────────────────────────────────────────────────

def t_e1_install_ok(tmp):
    print("\n── E1 install 成功 (落地 / installed.json / 自检重探) ──")
    calls = {"n": 0}

    def _probe_ready():
        calls["n"] += 1
        return ProbeResult(CapState.READY, "测试能力就绪")

    _reg("t.ext.ready", _probe_ready, ext_id="t.ready.ext")
    capabilities.state("t.ext.ready")
    calls["n"] = 0

    pkg = os.path.join(tmp, "ready.zip")
    write_ext_zip(pkg, _manifest("t.ready.ext", "1.0.0", ["t.ext.ready"]))
    ok, msg = extensions.install(pkg)
    check(ok is True, "install 返回 ok=True", msg)
    check(isinstance(msg, str) and msg, "返回消息为非空字符串")

    dest = os.path.join(extensions.root(), "t.ready.ext", "1.0.0")
    check(os.path.isfile(os.path.join(dest, "extension.json")),
          "extension.json 已落地到 <root>/<id>/<version>/")
    check(os.path.isfile(os.path.join(dest, "pure", "placeholder.py")),
          "pure/placeholder.py 已落地 (白名单成员)")
    check(calls["n"] >= 1, "安装期自检触发了能力重探 (probe calls={})".format(calls["n"]))

    inst = extensions.list_installed()
    rec = inst.get("t.ready.ext")
    check(isinstance(rec, dict) and rec.get("version") == "1.0.0",
          "installed.json 记录 version", str(rec))
    check(rec.get("enabled") is True, "installed.json 记录 enabled=True")
    check(extensions.is_installed("t.ready.ext") is True, "is_installed 为 True")
    check(capabilities.state("t.ext.ready") == CapState.READY,
          "安装后能力 state 为 READY")

    # 无残留 .tmp-*
    leftovers = [n for n in os.listdir(os.path.join(extensions.root(), "t.ready.ext"))
                 if ".tmp-" in n]
    check(not leftovers, "原子落地后无 .tmp-* 残留", str(leftovers))


def t_e2_uninstall(tmp):
    print("\n── E2 uninstall 往返 ──")
    ok, msg = extensions.uninstall("t.ready.ext")
    check(ok is True, "uninstall 返回 ok=True", msg)
    check(not os.path.exists(os.path.join(extensions.root(), "t.ready.ext")),
          "扩展目录已删除")
    check("t.ready.ext" not in extensions.list_installed(),
          "installed.json 已移除该扩展")
    check(extensions.is_installed("t.ready.ext") is False, "is_installed 为 False")
    ok2, msg2 = extensions.uninstall("t.ready.ext")
    check(ok2 is False, "重复 uninstall 返回 ok=False (结构化)", msg2)


def t_e3_rollback_quarantine(tmp):
    print("\n── E3 自检失败 → 回滚 + quarantine ──")
    _reg("t.ext.broken", lambda: ProbeResult(CapState.BROKEN, "缺依赖 dll"),
         ext_id="t.broken.ext")
    pkg = os.path.join(tmp, "broken.zip")
    write_ext_zip(pkg, _manifest("t.broken.ext", "1.0.0", ["t.ext.broken"]))
    ok, msg = extensions.install(pkg)
    check(ok is False, "install 返回 ok=False", msg)
    check("自检" in msg, "失败原因含「自检」", msg)
    check(not os.path.exists(os.path.join(extensions.root(), "t.broken.ext")),
          "目标目录已回滚 (不存在)")
    check("t.broken.ext" not in extensions.list_installed(),
          "installed.json 未写入失败扩展")
    qdir = extensions.manager.quarantine_dir()
    q_items = os.listdir(qdir) if os.path.isdir(qdir) else []
    check(any("t.broken.ext" in n for n in q_items),
          "产物已移入 quarantine/", str(q_items))


def t_e4_sha256(tmp):
    print("\n── E4 sha256 强制校验 ──")
    _reg("t.ext.sha", lambda: ProbeResult(CapState.READY, "ok"), ext_id="t.sha.ext")
    pkg = os.path.join(tmp, "sha.zip")
    write_ext_zip(pkg, _manifest("t.sha.ext", "1.0.0", ["t.ext.sha"]))
    real = sha256_of(pkg)

    ok, msg = extensions.install(pkg, expected_sha256="0" * 64)
    check(ok is False and "sha256" in msg, "期望哈希不符 → 拒绝", msg)
    check(not os.path.exists(os.path.join(extensions.root(), "t.sha.ext")),
          "sha256 不符时未落地任何目录")

    ok2, msg2 = extensions.install(pkg, expected_sha256=real)
    check(ok2 is True, "正确 sha256 → 安装成功", msg2)
    extensions.uninstall("t.sha.ext")


def t_e5_member_rejections(tmp):
    print("\n── E5 zip-slip / 软链 / 白名单外成员 ──")
    d = os.path.join(tmp, "reject")
    os.makedirs(d, exist_ok=True)

    # zip-slip
    z1 = os.path.join(tmp, "slip.zip")
    write_ext_zip(z1, _manifest("t.slip.ext", "1.0.0", ["cv.match"]),
                  extra_entries=[("../evil.py", b"x")])
    _expect_ext_error(lambda: ext_package.unpack(z1, os.path.join(d, "slip")),
                      None, "zip-slip 成员")

    # 软链
    z2 = os.path.join(tmp, "link.zip")
    write_ext_zip(z2, _manifest("t.link.ext", "1.0.0", ["cv.match"]),
                  symlinks=[("pure/link.py", "real.py")])
    _expect_ext_error(lambda: ext_package.unpack(z2, os.path.join(d, "link")),
                      "符号链接", "软链成员")

    # 白名单外前缀 (scripts/ 属脚本包白名单, 扩展包不允许)
    z3 = os.path.join(tmp, "wl.zip")
    write_ext_zip(z3, _manifest("t.wl.ext", "1.0.0", ["cv.match"]),
                  extra_entries=[("scripts/run.xls", b"y")])
    _expect_ext_error(lambda: ext_package.unpack(z3, os.path.join(d, "wl")),
                      "白名单", "白名单外成员")

    # 绝对路径
    z4 = os.path.join(tmp, "abs.zip")
    write_ext_zip(z4, _manifest("t.abs.ext", "1.0.0", ["cv.match"]),
                  extra_entries=[("C:/evil.py", b"z")])
    _expect_ext_error(lambda: ext_package.unpack(z4, os.path.join(d, "abs")),
                      None, "含盘符的绝对路径成员")


def t_e6_enable_disable(tmp):
    print("\n── E6 enable / disable (下次启动生效) ──")
    _reg("t.ext.toggle", lambda: ProbeResult(CapState.READY, "ok"),
         ext_id="t.toggle.ext")
    pkg = os.path.join(tmp, "toggle.zip")
    write_ext_zip(pkg, _manifest("t.toggle.ext", "1.0.0", ["t.ext.toggle"]))
    ok, msg = extensions.install(pkg)
    check(ok is True, "install toggle 扩展成功", msg)

    ok, msg = extensions.disable("t.toggle.ext")
    check(ok is True, "disable 返回 ok=True", msg)
    check("下次启动" in msg, "disable 提示「下次启动生效」", msg)
    check(extensions.list_installed()["t.toggle.ext"].get("enabled") is False,
          "installed.json.enabled 更新为 False")
    check(capabilities.state("t.ext.toggle") == CapState.DISABLED,
          "capabilities 该能力为 DISABLED")

    ok, msg = extensions.enable("t.toggle.ext")
    check(ok is True, "enable 返回 ok=True", msg)
    check("下次启动" in msg, "enable 提示「下次启动生效」", msg)
    check(extensions.list_installed()["t.toggle.ext"].get("enabled") is True,
          "installed.json.enabled 更新为 True")
    check(capabilities.state("t.ext.toggle") == CapState.READY,
          "capabilities 该能力恢复 READY")

    extensions.uninstall("t.toggle.ext")


def t_e7_repair(tmp):
    print("\n── E7 repair 路径 ──")
    _reg("t.ext.repair", lambda: ProbeResult(CapState.READY, "ok"),
         ext_id="t.repair.ext")
    pkg = os.path.join(tmp, "repair.zip")
    write_ext_zip(pkg, _manifest("t.repair.ext", "1.0.0", ["t.ext.repair"]))
    ok, msg = extensions.install(pkg)
    check(ok is True, "install repair 扩展成功", msg)

    # (a) 不带包 → 重跑自检 (应通过)
    ok, msg = extensions.repair("t.repair.ext")
    check(ok is True, "repair (no pkg) 自检通过", msg)

    # (b) 带包 → 重跑解包 + 自检
    ok, msg = extensions.repair("t.repair.ext", pkg_path=pkg)
    check(ok is True, "repair (with pkg) 重跑解包+自检通过", msg)

    # (c) 未安装 → 结构化失败
    ok, msg = extensions.repair("nope.ext")
    check(ok is False, "repair 未安装扩展 → ok=False", msg)

    extensions.uninstall("t.repair.ext")


def t_e8_structured_returns(tmp):
    print("\n── E8 异常不抛出, 返回结构化结果 ──")
    ok, msg = extensions.install(os.path.join(tmp, "does-not-exist.zip"))
    check(ok is False and isinstance(msg, str), "install 路径不存在 → (False, msg)", msg)

    notzip = os.path.join(tmp, "notzip.txt")
    with open(notzip, "wb") as f:
        f.write(b"this is not a zip")
    ok, msg = extensions.install(notzip)
    check(ok is False and isinstance(msg, str), "install 非 zip → (False, msg)", msg)

    # 缺 extension.json
    z = os.path.join(tmp, "nomanifest.zip")
    write_ext_zip(z, None)
    ok, msg = extensions.install(z)
    check(ok is False, "install 缺 extension.json → (False, msg)", msg)

    # 坏 JSON
    z2 = os.path.join(tmp, "badjson.zip")
    write_ext_zip(z2, "{ this is not json")
    ok, msg = extensions.install(z2)
    check(ok is False, "install 坏 JSON → (False, msg)", msg)

    # 清单校验失败 (version 非法)
    z3 = os.path.join(tmp, "badmanifest.zip")
    write_ext_zip(z3, _manifest("t.bad.ext", "not-a-version", ["cv.match"]))
    ok, msg = extensions.install(z3)
    check(ok is False and "校验" in msg, "install 清单非法 → (False, msg)", msg)


def t_e9_manifest_pure():
    print("\n── E9 manifest 校验纯函数 ──")
    check(len(ext_manifest.manifest_errors(_manifest("t.ok.ext", "1.0.0", ["cv.match"]))) == 0,
          "合法清单 → 无错误")
    check(len(ext_manifest.manifest_errors({})) > 0, "空 dict → 有错误")
    bad_id = _manifest("Bad.Id", "1.0.0", ["cv.match"])
    check(any("id" in e for e in ext_manifest.manifest_errors(bad_id)),
          "非法 id → 报错")
    no_cap = _manifest("t.nocap.ext", "1.0.0", [])
    check(any("capabilities" in e for e in ext_manifest.manifest_errors(no_cap)),
          "空 capabilities → 报错")
    check(ext_manifest.version_gt("1.0.1", "1.0.0") is True, "version_gt(1.0.1,1.0.0)")
    check(ext_manifest.version_gt("bad", "1.0.0") is False, "version_gt 不可解析 → False")


def main():
    print("=" * 68)
    print("extensions 扩展管理器回归自测 (阶段二新增项②)")
    print("临时 root: {}".format(_TMPROOT))
    print("=" * 68)
    tmp = tempfile.mkdtemp(prefix="acrpa_ext_pkg_", dir=_TMPROOT)
    try:
        t_e1_install_ok(tmp)
        t_e2_uninstall(tmp)
        t_e3_rollback_quarantine(tmp)
        t_e4_sha256(tmp)
        t_e5_member_rejections(tmp)
        t_e6_enable_disable(tmp)
        t_e7_repair(tmp)
        t_e8_structured_returns(tmp)
        t_e9_manifest_pure()
    finally:
        shutil.rmtree(_TMPROOT, ignore_errors=True)

    print()
    print("=" * 68)
    print("extensions 回归: 通过 {} 项, 失败 {} 项".format(len(_PASS), len(_FAIL)))
    print("=" * 68)
    if _FAIL:
        for m in _FAIL:
            print("  [FAIL] {}".format(m))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
