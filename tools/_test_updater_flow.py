# -*- coding: utf-8 -*-
"""更新链路回归自测（离线, 不发起任何真实网络请求）。

锁定的行为（对应本轮修复的缺陷）:

U1 附件挑选  : ACRPA.zip 优先 > 任意 .zip > 任意 .exe —— 只认 .zip 的旧实现会把
              "只发了便携 EXE 的 Release" 判成"没有下载地址"。
U2 校验和来源: Release API 的 asset digest 会被採纳为 sha256。
U3 跨版本升级: API 来源且无 digest 时, sha256 必须取自【远端清单里同版本】的值;
              绝不能回填本机 VERSION 的 sha256（旧实现如此, 导致升级必然
              "sha256 校验失败" —— 而下载明明成功）。
U4 无从取得时 : 宁可留空(不校验哈希), 也不拿本机旧包哈希去校验新包。
U5 包类型识别: zip / exe 由文件头判定; 下载落盘扩展名按直链推断。
U6 自更新助手: 脚本里必须同时具备 exe 与 zip 两条分支, 且 exe 分支会重写同级
              VERSION（否则程序自报旧版本、反复提示同一个更新）。

用法: .venv\\Scripts\\python.exe -X utf8 tools\\_test_updater_flow.py
退出码: 0=全部通过, 1=存在失败
"""
import io
import json
import os
import sys
import tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "src"))

import updater                                    # noqa: E402

_PASS = []
_FAIL = []

API_SRC = updater._RELEASE_API_SOURCES[0]
RAW_SRC = updater._RAW_MANIFEST_SOURCES[0]
NEW_VER = "0.1.30-beta"
DIGEST = "a" * 64
REMOTE_SHA = "b" * 64


def check(cond, msg):
    ( _PASS if cond else _FAIL).append(msg)
    print("[{}] {}".format("OK  " if cond else "FAIL", msg))


def _fake_http(mapping):
    """把 _http_get 换成查表实现, 保证测试完全离线。"""
    def _get(url, timeout=0):
        if url in mapping:
            return True, mapping[url], ""
        return False, "", "no fake response for {}".format(url)
    return _get


def _release_payload(tag, assets):
    return json.dumps({
        "tag_name": tag,
        "html_url": "https://example.invalid/releases/tag/" + tag,
        "body": "notes",
        "assets": assets,
    }, ensure_ascii=False)


def _asset(name, size=14707763, digest=None):
    a = {"name": name, "size": size,
         "browser_download_url": "https://example.invalid/dl/" + name}
    if digest:
        a["digest"] = digest
    return a


def _manifest_text(version, url, sha):
    return "{}\n{}\nsha256:{}\n".format(version, url, sha)


def with_fake(mapping):
    """上下文管理器: 临时替换 _http_get。"""
    class _Ctx:
        def __enter__(self):
            self._orig = updater._http_get
            updater._http_get = _fake_http(mapping)
            return self

        def __exit__(self, *exc):
            updater._http_get = self._orig
            return False
    return _Ctx()


# ── U1 附件挑选 ─────────────────────────────────────────────────────
def t_asset_pick():
    print("\n── U1 附件挑选优先级 ──")
    url, size, sha = updater._pick_release_asset([
        _asset("ACRPA-v0.1.30-beta.exe", 100, "sha256:" + DIGEST),
        _asset("ACRPA.zip", 200),
    ])
    check(url.endswith("ACRPA.zip") and size == 200,
          "ACRPA.zip 优先于 .exe (url={})".format(url.rsplit("/", 1)[-1]))

    url, size, sha = updater._pick_release_asset([
        _asset("说明.txt", 10),
        _asset("ACRPA-v0.1.30-beta.exe", 100, "sha256:" + DIGEST),
    ])
    check(url.endswith(".exe") and size == 100,
          "无 zip 时回退到 .exe 附件")
    check(sha == DIGEST, "exe 附件的 digest 被採纳为 sha256")

    url, size, sha = updater._pick_release_asset([_asset("random.bin", 1)])
    check(url == "" and size == 0 and sha == "", "无可用附件时返回空")

    url, size, sha = updater._pick_release_asset([
        {"name": "ACRPA.zip", "size": 5,
         "browser_download_url": "https://example.invalid/x", "digest": "sha256:zz"}])
    check(sha == "", "非法 digest 被丢弃 (不把脏值当校验和)")


# ── U2/U3/U4 校验和来源 ─────────────────────────────────────────────
def t_parse_api():
    print("\n── U2 Release API 解析 ──")
    payload = _release_payload("v" + NEW_VER,
                               [_asset("ACRPA-v0.1.30-beta.exe", 777,
                                       "sha256:" + DIGEST.upper())])
    info, why = updater._parse_release_api(payload, "fake")
    check(info is not None and info["latest"] == NEW_VER, "解析出 tag 版本号")
    check(info["sha256"] == DIGEST, "digest 转为小写 sha256")
    check(info["download_url"].endswith(".exe") and info["size"] == 777,
          "带出直链与体积")


def t_check_no_local_sha():
    print("\n── U3/U4 check(): 不得回填本机 sha256 ──")
    local_sha = updater.get_sha256()
    check(bool(local_sha), "本机 VERSION 确实配置了 sha256 (前提成立)")

    # 场景 A: API 有 digest → 直接采用, 不需要清单
    with with_fake({API_SRC: _release_payload("v" + NEW_VER, [
            _asset("ACRPA-v0.1.30-beta.exe", 777, "sha256:" + DIGEST)])}):
        res = updater.check(timeout=1)
    check(res["has_update"] and res["latest"] == NEW_VER, "A: 识别出新版本")
    check(res["sha256"] == DIGEST, "A: 采用 Release 附件 digest")
    check(res["sha256"] != local_sha, "A: 未回填本机 sha256")

    # 场景 B: API 无 digest, 远端清单有同版本 sha256 → 用清单的
    remote_url = "https://example.invalid/dl/ACRPA_v{}.zip".format(NEW_VER)
    with with_fake({
        API_SRC: _release_payload("v" + NEW_VER, [_asset("ACRPA.zip", 888)]),
        RAW_SRC: _manifest_text(NEW_VER, remote_url, REMOTE_SHA),
    }):
        res = updater.check(timeout=1)
    check(res["sha256"] == REMOTE_SHA, "B: 采用远端清单中同版本的 sha256")
    check(res["sha256"] != local_sha, "B: 未回填本机 sha256")

    # 场景 C: 谁都给不出 sha256 → 保持为空, 绝不退回本机值 (核心回归)
    with with_fake({
        API_SRC: _release_payload("v" + NEW_VER, [_asset("ACRPA.zip", 888)]),
        RAW_SRC: _manifest_text("0.1.29-beta",
                                "https://example.invalid/dl/old.zip", "c" * 64),
    }):
        res = updater.check(timeout=1)
    check(res["latest"] == NEW_VER, "C: 仍报出新版本")
    check(res["sha256"] == "",
          "C: 取不到目标版本校验和时留空 (旧实现会塞入本机 sha256)")
    check(res["sha256"] != local_sha, "C: 未回填本机 sha256")


# ── U5 包类型与扩展名 ───────────────────────────────────────────────
def t_package_kind():
    print("\n── U5 包类型与扩展名 ──")
    tmp = tempfile.mkdtemp(prefix="acrpa_test_")
    zip_path = os.path.join(tmp, "pkg.zip")
    exe_path = os.path.join(tmp, "pkg.exe")
    bad_path = os.path.join(tmp, "pkg.txt")
    with open(zip_path, "wb") as f:
        f.write(b"PK\x03\x04rest")
    with open(exe_path, "wb") as f:
        f.write(b"MZ\x90\x00rest")
    with open(bad_path, "wb") as f:
        f.write(b"not a package")

    check(updater._package_kind(zip_path) == "zip", "zip 包按文件头识别")
    check(updater._package_kind(exe_path) == "exe", "exe 包按文件头识别")
    check(updater._package_kind(bad_path) == "", "无法识别的包返回空")
    check(updater._package_kind(os.path.join(tmp, "missing")) == "",
          "文件不存在时返回空 (不抛异常)")

    cases = {
        "https://x/dl/ACRPA-v0.1.29-beta.exe": ".exe",
        "https://x/dl/ACRPA.zip?token=abc": ".zip",
        "https://x/dl/ACRPA_v0.1.29.zip#frag": ".zip",
        "https://x/dl/unknown": ".zip",
    }
    for url, want in cases.items():
        check(updater._package_ext(url) == want,
              "_package_ext({}) == {}".format(url.rsplit("/", 1)[-1], want))


# ── U6 自更新助手脚本 ───────────────────────────────────────────────
def t_apply_script():
    print("\n── U6 自更新助手脚本分支 ──")
    ps = updater._APPLY_PS1
    check("$kind -eq 'exe'" in ps, "存在 exe 分支")
    check("ExtractToDirectory" in ps, "保留 zip 解压分支")
    check("Write-Manifest" in ps and "UTF8Encoding($false)" in ps,
          "exe 分支会重写无 BOM 的 VERSION (BOM 会让版本号比对失败)")
    check("ACRPA_PKG" in ps and "ACRPA_ZIP" not in ps,
          "助手改用 ACRPA_PKG (zip 专属命名已移除)")
    check("Replace-Main" in ps, "主程序替换收敛为单一函数 (含 .new 原子替换)")


def main():
    print("=" * 66)
    print("ACRPA 更新链路回归自测 (离线)")
    print("=" * 66)
    t_asset_pick()
    t_parse_api()
    t_check_no_local_sha()
    t_package_kind()
    t_apply_script()
    print("\n" + "=" * 66)
    print("通过 {} / 失败 {}".format(len(_PASS), len(_FAIL)))
    if _FAIL:
        for m in _FAIL:
            print("  FAIL: {}".format(m))
    print("结论: {}".format("FAIL" if _FAIL else "PASS"))
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
