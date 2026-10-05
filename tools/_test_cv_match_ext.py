#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""首个上线扩展 ``cv.match`` 端到端自测 (路线图 阶段二新增项③)。

覆盖:
  X1 确定性构建   : build() 产出 zip; 两次构建 sha256 相同
  X2 白名单一致   : index.WHITELIST 收录 cv.match/1.0.0 且与构建 sha256 一致
  X3 安装         : 经白名单校验 install 成功 → 目录落地 + installed.json
  X4 能力 READY   : capabilities.refresh 后 state==READY 且 reason 含「扩展」;
                    engine._resolve_matcher() 返回非空 (非 None)
  X5 真实匹配     : provider.match() 命中且坐标正确 (±2px); 未命中返回 None;
                    非法文件 / 不存在路径返回 None (不抛)
  X6 卸载回退     : uninstall → refresh → _resolve_matcher() 回 None, reason 不再指向扩展
  X7 未安装自检   : provider_self_check(未安装) → (False, ...) 不抛异常

隔离: 全程在 tempfile.mkdtemp() 注入的 ACRPA_EXTENSIONS_ROOT 内进行, finally 清理;
      **绝不**污染真实 %LOCALAPPDATA%。

用法: .venv\\Scripts\\python.exe -X utf8 tools\\_test_cv_match_ext.py
退出码: 0 = 全部通过 / 1 = 有失败。
"""
import os
import sys
import zipfile
import shutil
import tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(BASE, "src")
TOOLS = os.path.join(BASE, "tools")
for p in (SRC, TOOLS):
    if p not in sys.path:
        sys.path.insert(0, p)

# 关键: 安装根指向临时目录, 避免污染真实 %LOCALAPPDATA%/ACRPA/extensions。
_TMPROOT = tempfile.mkdtemp(prefix="acrpa_cvmatch_test_")
os.environ["ACRPA_EXTENSIONS_ROOT"] = _TMPROOT

import capabilities                                     # noqa: E402
import extensions                                       # noqa: E402
import engine as engine_mod                             # noqa: E402
import build_cv_match_extension as builder             # noqa: E402
from capabilities import CapState                       # noqa: E402

_PASS, _FAIL = [], []


def check(cond, msg, detail=""):
    (_PASS if cond else _FAIL).append(msg)
    tail = ("  ({})".format(detail) if detail else "")
    print("[{}] {}{}".format("OK  " if cond else "FAIL", msg, tail))


# ── X1/X2 构建 + 白名单 ───────────────────────────────────────────────
def t_build_deterministic(tmp):
    print("\n── X1/X2 确定性构建 + 白名单一致 ──")
    z1, h1 = builder.build(out_dir=tmp)
    z2, h2 = builder.build(out_dir=tmp)
    check(os.path.isfile(z1) and z1.endswith(".zip"), "build() 产出 zip", z1)
    check(h1 == h2, "两次构建 sha256 相同 (确定性)", "{} vs {}".format(h1[:16], h2[:16]))
    check(builder.compute_sha256(z1) == h1, "compute_sha256 复算一致")

    # 包内清单须能过 manifest 校验
    with zipfile.ZipFile(z1, "r") as zf:
        names = zf.namelist()
        raw = zf.read("extension.json").decode("utf-8")
    import json as _json
    m = _json.loads(raw)
    errs = extensions.manifest.manifest_errors(m)
    check(not errs, "包内 extension.json 通过 manifest 校验", "; ".join(errs))
    check("extension.json" in names and "pure/cv_match_ext.py" in names,
          "包布局 = extension.json + pure/**", str(names))
    check(m.get("id") == "cv.match" and m.get("capabilities") == ["cv.match"],
          "清单 id/capabilities 正确")

    wl = extensions.index.sha256_for("cv.match", "1.0.0")
    check(wl is not None, "index.WHITELIST 已收录 cv.match/1.0.0")
    check(wl == h1, "白名单 sha256 与构建一致 (固化正确)")
    return z1, h1


# ── X3 安装 ───────────────────────────────────────────────────────────
def t_install(zip_path):
    print("\n── X3 安装 (经白名单校验) ──")
    ok, msg = extensions.install(zip_path)          # 默认走白名单强制校验
    check(ok is True, "install 返回 ok=True", msg)
    check("未签名" not in msg, "安装经白名单校验 (非未签名放行)", msg)
    dest = os.path.join(extensions.root(), "cv.match", "1.0.0")
    check(os.path.isfile(os.path.join(dest, "extension.json")), "extension.json 已落地")
    check(os.path.isfile(os.path.join(dest, "pure", "cv_match_ext.py")),
          "pure/cv_match_ext.py 已落地")
    check(extensions.is_installed("cv.match") is True, "installed.json 已登记")

    # 期望哈希不符 → 拒绝 (结构化)
    ok2, msg2 = extensions.install(zip_path, expected_sha256="0" * 64)
    check(ok2 is False and "sha256" in msg2, "错误 expected_sha256 → 拒绝", msg2)


# ── X4 能力 READY + matcher ───────────────────────────────────────────
def t_capability_ready():
    print("\n── X4 capabilities READY (由扩展提供) + matcher ──")
    capabilities.refresh("cv.match")
    st = capabilities.state("cv.match")
    check(st == CapState.READY, "state == READY", getattr(st, "value", st))
    reason = capabilities.require_reason("cv.match")
    check("扩展" in reason, "reason 含「扩展」", reason)

    engine_mod._reset_matcher_cache()
    m = engine_mod._resolve_matcher()
    check(callable(m), "engine._resolve_matcher() 返回非空 matcher")


# ── X5 真实匹配 ───────────────────────────────────────────────────────
def _make_bg():
    from PIL import Image
    bg = Image.new("RGB", (64, 64), (30, 30, 30))
    for yy in range(8):
        for xx in range(8):
            bg.putpixel((20 + xx, 20 + yy),
                        ((xx * 30) % 256, (yy * 30) % 256, 128))
    return bg


def t_real_match(tmp):
    print("\n── X5 真实匹配 (provider.match) ──")
    from PIL import Image
    mod = extensions.provider("cv.match")
    check(mod is not None and callable(getattr(mod, "match", None)),
          "provider('cv.match') 返回带 match() 的模块")
    if mod is None:
        return

    ok, why = mod.self_check()
    check(ok is True, "provider.self_check() 通过", why)

    bg = _make_bg()
    tpl = bg.crop((20, 20, 28, 28))                 # 8x8 模板, 左上角 (20,20)
    exp_x, exp_y = 20 + 8 // 2, 20 + 8 // 2         # (24, 24)

    res = mod.match(bg, tpl, confidence=0.9, grayscale=True)
    check(res is not None, "命中: 返回 (x, y, score)")
    if res:
        x, y, score = res
        check(abs(x - exp_x) <= 2 and abs(y - exp_y) <= 2,
              "坐标正确 ±2px", "got=({}, {}) exp=({}, {}) score={:.3f}".format(
                  x, y, exp_x, exp_y, score))
        check(score >= 0.9, "命中分值 >= 阈值", "{:.3f}".format(score))

    # 区域限定命中
    res_r = mod.match(bg, tpl, confidence=0.9, region=(10, 10, 40, 40))
    check(res_r is not None and abs(res_r[0] - exp_x) <= 2
          and abs(res_r[1] - exp_y) <= 2, "region 限定内命中且坐标正确",
          str(res_r))
    # 区域把目标排除 → None
    res_r2 = mod.match(bg, tpl, confidence=0.9, region=(0, 0, 15, 15))
    check(res_r2 is None, "region 排除目标 → None", str(res_r2))

    # 未命中 (结构与背景完全不同的噪声模板; 避免使用背景的仿射变换 ——
    # NCC 对亮度/对比度仿射不变, 那样的模板本就应判为命中) → None
    other = Image.new("RGB", (8, 8))
    for yy in range(8):
        for xx in range(8):
            k = (xx * 31 + yy * 57 + 11) % 251
            other.putpixel((xx, yy), (k, (k * 3) % 256, (k * 7) % 256))
    res_n = mod.match(bg, other, confidence=0.96)
    check(res_n is None, "未命中 → None", str(res_n))

    # 非法输入不抛异常
    check(mod.match(bg, os.path.join(tmp, "nope.png")) is None, "不存在路径 → None")
    bad = os.path.join(tmp, "bad.txt")
    with open(bad, "w", encoding="utf-8") as f:
        f.write("not an image")
    check(mod.match(bg, bad) is None, "非图片文件 → None")


# ── X6 卸载回退 ───────────────────────────────────────────────────────
def t_uninstall_fallback():
    print("\n── X6 卸载 → 回退内置路径 ──")
    ok, msg = extensions.uninstall("cv.match")
    check(ok is True, "uninstall 返回 ok=True", msg)
    check(extensions.is_installed("cv.match") is False, "installed.json 已移除")

    capabilities.refresh("cv.match")
    engine_mod._reset_matcher_cache()
    check(engine_mod._resolve_matcher() is None, "卸载后 _resolve_matcher() 为 None (回退内置)")
    reason = capabilities.require_reason("cv.match")
    check("由扩展 cv.match 提供" not in reason, "reason 不再指向扩展", reason)


# ── X7 未安装自检不抛 ─────────────────────────────────────────────────
def t_uninstalled_self_check():
    print("\n── X7 未安装扩展自检 (结构化, 不抛) ──")
    ok, reason = extensions.provider_self_check("cv.match")
    check(ok is False and isinstance(reason, str) and reason,
          "provider_self_check(未安装) → (False, reason)", reason)
    check(extensions.provider("cv.match") is None, "provider(未安装) → None")
    ok2, reason2 = extensions.provider_self_check("no.such.ext")
    check(ok2 is False and isinstance(reason2, str),
          "provider_self_check(不存在) → (False, reason)", reason2)


def main():
    print("=" * 68)
    print("cv.match 首个上线扩展 端到端自测 (阶段二新增项③)")
    print("临时 root: {}".format(_TMPROOT))
    print("=" * 68)
    tmp = tempfile.mkdtemp(prefix="acrpa_cvmatch_pkg_", dir=_TMPROOT)
    try:
        zip_path, _sha = t_build_deterministic(tmp)
        t_install(zip_path)
        t_capability_ready()
        t_real_match(tmp)
        t_uninstall_fallback()
        t_uninstalled_self_check()
    finally:
        try:
            extensions.uninstall("cv.match")
        except Exception:
            pass
        shutil.rmtree(_TMPROOT, ignore_errors=True)

    print()
    print("=" * 68)
    print("cv.match 端到端回归: 通过 {} 项, 失败 {} 项".format(len(_PASS), len(_FAIL)))
    print("=" * 68)
    if _FAIL:
        for m in _FAIL:
            print("  [FAIL] {}".format(m))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
