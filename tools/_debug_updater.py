# -*- coding: utf-8 -*-
"""更新机制回归测试 — 覆盖 v0.1.26 重构的检查/比较/下载/校验/版本来源。

覆盖:
- V*: 语义化版本比较（含预发布后缀、v 前缀、位数不齐、不可解析）
- M*: VERSION 清单解析（多直链、sha256、注释、非法首行、notes 行）
- C*: 下载直链候选（顺序、去重、Release 约定推导、两种 tag 写法、镜像兜底）
- F*: 下载产物校验（空包、HTML 错误页、体积不符、sha256 不符、合法包）
- D*: 下载流程（成功落盘、首个直链失败自动换源、全部失败可感知）
- P*: check() 结构化状态（network_error / ok / no_update、Release API 解析、
      镜像缓存落后时不吞掉真实新版本）
- A*: check_async 多路复用（检查在飞时第二个调用方也能拿到回调；已有结论直接复用）
- T*: 传输双栈（requests 因证书失败时 urllib 兜底；明确 404 不换栈）
- Z*: 包内 VERSION 读取（自更新前的版本一致性校验）
- R*: 冻结路径解析（打包后能读到真实版本号 —— 本次修复的核心缺陷）

用法: python tools/_debug_updater.py
退出码: 0=全部通过, 1=存在失败
"""
import hashlib
import os
import shutil
import sys
import tempfile
import threading
import time
import zipfile

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(BASE, "src")
sys.path.insert(0, SRC)

import updater                      # noqa: E402
import version_info as vi           # noqa: E402

_passed = [0]
_failed = [0]


def check(name, cond, detail=""):
    if cond:
        _passed[0] += 1
        print("  [PASS] " + name)
    else:
        _failed[0] += 1
        print("  [FAIL] " + name + ("  -> " + detail if detail else ""))


def cmp3(a, b):
    return updater.compare_versions(a, b)


def reset_updater_state():
    updater._result = None
    updater._status = None
    with updater._lock:
        del updater._pending_done[:]


# ══════════════════════════════════════════════════════════════════
print("[V] 语义化版本比较")
check("V1  0.1.25 < 0.1.26", cmp3("0.1.25", "0.1.26") == -1)
check("V2  0.1.26 > 0.1.25", cmp3("0.1.26", "0.1.25") == 1)
check("V3  相同版本 = 0", cmp3("0.1.26", "0.1.26") == 0)
# 旧实现用 int() 解析预发布后缀会抛异常并吞成"无更新", 这一组是本次修复的核心
check("V4  正式版 > 同号预发布", cmp3("0.1.26", "0.1.26-beta") == 1)
check("V5  预发布 < 同号正式版", cmp3("0.1.26-beta", "0.1.26") == -1)
check("V6  rc1 > beta", cmp3("0.1.26-rc1", "0.1.26-beta") == 1)
check("V7  位数不齐等价 (0.1 == 0.1.0)", cmp3("0.1", "0.1.0") == 0)
check("V8  v 前缀等价", cmp3("v0.1.26", "0.1.26") == 0)
check("V9  0.2 > 0.1.99", cmp3("0.2", "0.1.99") == 1)
check("V10 无分隔符后缀 (0.1.26beta) 可解析",
      cmp3("0.1.26", "0.1.26beta") == 1)
check("V11 pytest 风格后缀 (1.0.0rc.1 < 1.0.0)",
      cmp3("1.0.0rc.1", "1.0.0") == -1)
check("V12 不可解析不误报新版本", cmp3("abc", "0.1.26") == 0, str(cmp3("abc", "0.1.26")))
check("V13 旧接口 _compare_versions 兼容 (低→高 = True)",
      updater._compare_versions("0.1.24", "0.1.25") is True)
check("V14 旧接口 _compare_versions 兼容 (高→低 = False)",
      updater._compare_versions("0.1.25", "0.1.24") is False)
check("V15 旧接口对预发布后缀不再静默失败",
      updater._compare_versions("0.1.25", "0.1.26-beta") is True)

# ══════════════════════════════════════════════════════════════════
print("[M] VERSION 清单解析")
_manifest = (
    "# 注释行应被忽略\n"
    "\n"
    "0.1.26\n"
    "https://github.com/yohoten/acrpa/releases/download/v0.1.26/ACRPA.zip\n"
    "https://gitee.com/yohoten/acrpa/raw/master/dist/ACRPA.zip\n"
    "sha256:" + "a" * 64 + "\n"
)
info, why = updater._parse_manifest(_manifest, "test")
check("M1  清单解析成功", info is not None, why)
check("M2  版本号取自首行", (info or {}).get("latest") == "0.1.26")
check("M3  多直链全部收集", len((info or {}).get("download_urls") or []) == 2,
      str((info or {}).get("download_urls")))
check("M4  download_url 取首条直链",
      (info or {}).get("download_url", "").startswith("https://github.com/"))
check("M5  sha256 行解析", (info or {}).get("sha256") == "a" * 64)
check("M6  注释与空行被忽略", (info or {}).get("latest") != "# 注释行应被忽略")

bad, why = updater._parse_manifest("not-a-version\nhttps://x/y.zip\n", "test")
check("M7  非法首行被拒", bad is None and bool(why), why)
empty, why = updater._parse_manifest("\n# only comments\n", "test")
check("M8  空清单被拒", empty is None and bool(why), why)

info2, _ = updater._parse_manifest(
    "0.1.27\n-\nnotes:https://example.com/notes\n", "test")
check("M9  第二行为占位符时不当作直链", info2["download_url"] == "")
check("M10 notes 行解析", info2["notes_url"] == "https://example.com/notes")

# ══════════════════════════════════════════════════════════════════
print("[C] 下载直链候选")
cands = updater.candidate_download_urls({
    "latest": "0.1.26",
    "download_urls": ["https://a.example/ACRPA.zip"],
    "download_url": "https://b.example/ACRPA.zip",
    "sha256": "a" * 64,
})
check("C1  候选非空", len(cands) > 0)
check("C2  远端清单直链优先", cands[0] == "https://a.example/ACRPA.zip", str(cands[:2]))
check("C3  候选无重复", len(cands) == len(set(cands)))
check("C4  含 Release 约定直链",
      vi.release_asset_url("0.1.26") in cands, str(cands))
check("C5  有 sha256 时含 raw 镜像兜底",
      any("raw.githubusercontent.com" in u for u in cands), str(cands))

# dist/ 副本是唯一不带版本信息的通道: 历史包连包内 VERSION 都没有, 无法复核,
# 此时若仍使用它, Release 未发布时会静默装上旧包 —— 没有校验和就必须放弃。
nogate = updater.candidate_download_urls({
    "latest": "0.1.26",
    "download_urls": ["https://a.example/ACRPA.zip"],
})
check("C5b 无 sha256 时放弃不带版本信息的 dist/ 兜底通道",
      not any("/dist/ACRPA.zip" in u for u in nogate), str(nogate))
check("C5c 无 sha256 时仍保留 Release 约定直链",
      vi.release_asset_url("0.1.26") in nogate, str(nogate))

bare = updater.candidate_download_urls({"latest": "0.1.26"})
check("C6  无显式直链时也能推导",
      vi.release_asset_url("0.1.26") in bare, str(bare))
check("C7  resolve_download_url 可落回 Release 约定",
      updater.resolve_download_url({"latest": "0.9.9"}).endswith("/v0.9.9/ACRPA.zip"),
      updater.resolve_download_url({"latest": "0.9.9"}))
# 仓库历史 tag 有 v0.1.25 与 v0.1.25.0 两种写法, 只推导一种必然在其中一种下 404
check("C8  推导直链同时覆盖三段与四段 tag 写法",
      vi.release_asset_urls("0.1.26") == [
          "https://github.com/yohoten/acrpa/releases/download/v0.1.26/ACRPA.zip",
          "https://github.com/yohoten/acrpa/releases/download/v0.1.26.0/ACRPA.zip",
      ], str(vi.release_asset_urls("0.1.26")))
check("C9  候选列表含四段 tag 写法",
      any("/v0.1.26.0/ACRPA.zip" in u for u in cands), str(cands))
check("C10 已是四段版本时不重复补 .0",
      len(vi.release_asset_urls("0.1.25.0")) == 1,
      str(vi.release_asset_urls("0.1.25.0")))

# ══════════════════════════════════════════════════════════════════
print("[F] 下载产物校验")
tmp = tempfile.mkdtemp(prefix="acrpa_upd_test_")
try:
    empty_p = os.path.join(tmp, "empty.zip")
    open(empty_p, "wb").close()
    ok, why = updater._verify_file(empty_p)
    check("F1  空文件被拒", not ok, why)

    html_p = os.path.join(tmp, "html.zip")
    with open(html_p, "wb") as f:
        f.write(b"<!DOCTYPE html><html>404 Not Found</html>")
    ok, why = updater._verify_file(html_p)
    check("F2  HTML 错误页被拒", not ok and "有效" in why, why)

    good_p = os.path.join(tmp, "good.zip")
    with zipfile.ZipFile(good_p, "w") as z:
        z.writestr("ACRPA/VERSION", "0.1.26\n")
    digest = hashlib.sha256(open(good_p, "rb").read()).hexdigest()

    ok, why = updater._verify_file(good_p)
    check("F3  合法 ZIP 通过", ok, why)
    ok, why = updater._verify_file(good_p, expect_sha256=digest)
    check("F4  正确 sha256 通过", ok, why)
    ok, why = updater._verify_file(good_p, expect_sha256="b" * 64)
    check("F5  错误 sha256 被拒", not ok and "sha256" in why, why)
    ok, why = updater._verify_file(good_p, expect_size=1)
    check("F6  体积不符被拒", not ok and "体积" in why, why)
    ok, why = updater._verify_file(good_p, expect_size=os.path.getsize(good_p))
    check("F7  体积相符通过", ok, why)

    exe_p = os.path.join(tmp, "fake.exe")
    with open(exe_p, "wb") as f:
        f.write(b"MZ" + b"\x00" * 2048)
    ok, why = updater._verify_file(exe_p)
    check("F8  PE 可执行文件被接受", ok, why)

    check("F9  get_expected_version 可读包内 VERSION",
          updater.get_expected_version(good_p) == "0.1.26",
          updater.get_expected_version(good_p))
    check("F10 无 VERSION 的包返回空串",
          updater.get_expected_version(html_p) == "")

    check("F11 包内版本与目标一致时通过",
          updater._verify_package_version(good_p, "0.1.26")[0] is True)
    ok, why = updater._verify_package_version(good_p, "0.1.27")
    check("F12 包内版本与目标不一致时拒绝 (兜底镜像给旧包) ",
          not ok and "包内版本不符" in why, why)
    check("F13 目标版本为空时不做判定",
          updater._verify_package_version(good_p, "")[0] is True)
    check("F14 包内无 VERSION 时不做判定 (兼容旧发布包)",
          updater._verify_package_version(html_p, "0.1.26")[0] is True)

    # ══════════════════════════════════════════════════════════════
    print("[D] 下载流程")
    body = open(good_p, "rb").read()
    calls = []
    mode = {"all_fail": False}

    class _Resp(object):
        def __init__(self, data, status=200):
            self._data = data
            self.status_code = status
            self.headers = {"Content-Length": str(len(data))}
            self._done = False

        def iter_content(self, size):
            if not self._done:
                self._done = True
                yield self._data

    real_get = updater.requests.get

    def _fake_get(url, **kw):
        calls.append(url)
        if mode["all_fail"] or "dead.example" in url:
            return _Resp(b"", status=404)
        return _Resp(body)

    updater.requests.get = _fake_get
    try:
        out_dir = os.path.join(tmp, "dl")
        ok, res = updater.download_package(
            {"latest": "0.1.26",
             "download_urls": ["https://dead.example/ACRPA.zip",
                               "https://live.example/ACRPA.zip"]},
            dest_dir=out_dir)
        check("D1  首个直链 404 时自动换源", ok, str(res))
        check("D2  包已落盘且内容一致",
              ok and os.path.exists(res)
              and open(res, "rb").read() == body, str(res))
        check("D3  已按顺序尝试两个直链", len(calls) == 2, str(calls))
        check("D4  无残留 .part 文件",
              not any(n.endswith(".part") for n in os.listdir(out_dir)),
              str(os.listdir(out_dir)))

        calls[:] = []
        mode["all_fail"] = True
        ok, why = updater.download_package(
            {"latest": "0.1.26",
             "download_urls": ["https://dead.example/ACRPA.zip"]},
            dest_dir=os.path.join(tmp, "dl2"))
        mode["all_fail"] = False
        check("D5  全部源失败时返回失败而非静默", not ok and bool(why), str(why))
        check("D6  失败原因可读 (含 HTTP 状态)", "404" in why, why)
        check("D6b 候选列表全部被尝试过", len(calls) >= 2, str(len(calls)))

        calls[:] = []
        ok, why = updater.download_package(
            {"latest": "0.1.26", "download_urls": ["https://live.example/ACRPA.zip"],
             "sha256": "c" * 64},
            dest_dir=os.path.join(tmp, "dl3"))
        check("D7  sha256 不符时拒绝落盘", not ok and "sha256" in why, str(why))
        check("D8  校验失败后不留下成品包",
              not any(n.lower().endswith(".zip") for n in
                      (os.listdir(os.path.join(tmp, "dl3"))
                       if os.path.isdir(os.path.join(tmp, "dl3")) else [])),
              str(os.path.isdir(os.path.join(tmp, "dl3"))))

        # 兜底镜像给出旧包时必须换源, 而不是"下载成功但版本没变"
        stale_p = os.path.join(tmp, "stale.zip")
        with zipfile.ZipFile(stale_p, "w") as z:
            z.writestr("ACRPA/VERSION", "0.1.25\n")
        stale_body = open(stale_p, "rb").read()

        def _fake_stale(url, **kw):
            calls.append(url)
            return _Resp(stale_body if "stale.example" in url else body)

        updater.requests.get = _fake_stale
        calls[:] = []
        ok, res = updater.download_package(
            {"latest": "0.1.26",
             "download_urls": ["https://stale.example/ACRPA.zip",
                               "https://live.example/ACRPA.zip"]},
            dest_dir=os.path.join(tmp, "dl4"))
        check("D9  包内版本不符时换源而非接受旧包", ok, str(res))
        check("D10 最终落盘的是版本正确的包",
              ok and updater.get_expected_version(res) == "0.1.26",
              updater.get_expected_version(res) if ok else str(res))

        calls[:] = []

        def _fake_all_stale(url, **kw):
            calls.append(url)
            return _Resp(stale_body)

        updater.requests.get = _fake_all_stale
        ok, why = updater.download_package(
            {"latest": "0.1.26",
             "download_urls": ["https://stale.example/ACRPA.zip"]},
            dest_dir=os.path.join(tmp, "dl5"))
        check("D11 所有源都给出旧包时明确失败",
              not ok and "包内版本不符" in why, str(why))
        check("D12 每个候选源都被核查过 (未提前放弃)",
              len(calls) >= 2, str(len(calls)))
    finally:
        updater.requests.get = real_get
finally:
    shutil.rmtree(tmp, ignore_errors=True)

# ══════════════════════════════════════════════════════════════════
print("[P] check() 结构化状态")
real_http = updater._http_get


def _patch_http(mapping):
    def _fake(url, timeout=8):
        for key, val in mapping.items():
            if key in url:
                return val
        return False, "", "connection refused"
    updater._http_get = _fake


try:
    reset_updater_state()
    _patch_http({})
    res = updater.check(timeout=1)
    check("P1  全源不可达 → network_error", res["status"] == "network_error",
          res["status"])
    check("P2  失败时 error 非空 (不再伪装成无更新)",
          bool(res.get("error")) and res["has_update"] is False, str(res.get("error"))[:60])

    reset_updater_state()
    _patch_http({"releases/latest": (
        True,
        '{"tag_name":"v0.1.99","html_url":"https://github.com/x/rel",'
        '"body":"notes","assets":[{"name":"ACRPA.zip",'
        '"browser_download_url":"https://dl.example/ACRPA.zip","size":12345}]}',
        "")})
    res = updater.check(timeout=1)
    check("P3  Release API 解析 → 有更新", res["status"] == "ok" and res["has_update"],
          str(res["status"]))
    check("P4  Release 直链与体积取自 assets",
          res["download_url"] == "https://dl.example/ACRPA.zip" and res["size"] == 12345,
          "{} / {}".format(res["download_url"], res["size"]))
    check("P5  Release 正文与页面链接被带出",
          res["notes"] == "notes" and res["notes_url"].endswith("/rel"))

    reset_updater_state()
    _patch_http({"/VERSION": (True, "{}\n".format(updater.VERSION), "")})
    res = updater.check(timeout=1)
    check("P6  远端版本与本机相同 → no_update",
          res["status"] == "no_update" and not res["has_update"], str(res["status"]))

    reset_updater_state()
    _patch_http({"raw.githubusercontent": (True, "0.999.0\n", ""),
                 "/VERSION": (True, "0.999.0\nhttps://x.example/a.zip\n", "")})
    res = updater.check(timeout=1)
    check("P7  远端版本更高 → ok", res["status"] == "ok" and res["has_update"],
          str(res["status"]))
    check("P8  记录实际生效的来源", bool(res["source"]), res["source"])

    reset_updater_state()
    _patch_http({"/VERSION": (True, "garbage-not-a-version\n", "")})
    res = updater.check(timeout=1)
    check("P9  清单损坏 → 不误判为有新版本",
          res["has_update"] is False and res["status"] in ("network_error", "no_update"),
          "{} / {}".format(res["status"], res.get("error", "")[:50]))

    # 反陈旧: 首个成功来源恰好缓存落后 (jsDelivr 分支别名实测会滞后), 不得把
    # 真实存在的新版本静默吞成"已是最新" —— 这是用户无从察觉的失败模式。
    reset_updater_state()
    _patch_http({
        "releases/latest": (False, "", "ssl error"),
        "cdn.jsdelivr.net": (True, "{}\n".format(updater.VERSION), ""),
        "raw.githubusercontent": (True, "0.999.0\nhttps://x.example/a.zip\n", ""),
    })
    res = updater.check(timeout=1)
    check("P10 首个镜像缓存落后时不吞掉真实新版本",
          res["status"] == "ok" and res["has_update"] and res["latest"] == "0.999.0",
          "{} / {}".format(res["status"], res["latest"]))
    check("P11 记录的是真正给出新版本的来源",
          "raw.githubusercontent" in (res["source"] or ""), str(res["source"]))

    # 发版流程允许"先改版本号再传包": Release 还没发布, 但清单已先行
    reset_updater_state()
    _patch_http({
        "releases/latest": (
            True,
            '{"tag_name":"v0.1.26","html_url":"https://github.com/x/rel","body":"n",'
            '"assets":[{"name":"ACRPA.zip",'
            '"browser_download_url":"https://dl.example/old.zip","size":1}]}', ""),
        "/VERSION": (True, "0.999.0\nhttps://x.example/a.zip\n", ""),
    })
    res = updater.check(timeout=1)
    check("P12 Release 落后而清单先行时仍报出更新",
          res["status"] == "ok" and res["latest"] == "0.999.0",
          "{} / {}".format(res["status"], res["latest"]))
finally:
    updater._http_get = real_http

# ══════════════════════════════════════════════════════════════════
print("[A] check_async 多路复用")
real_http = updater._http_get
try:
    reset_updater_state()
    hits = []

    def _slow_http(url, timeout=8):
        hits.append(url)
        time.sleep(0.25)
        if "api.github.com" in url:
            return True, ('{"tag_name":"v0.999.0","html_url":"https://x/rel",'
                          '"body":"b","assets":[{"name":"ACRPA.zip",'
                          '"browser_download_url":"https://dl.example/ACRPA.zip",'
                          '"size":9}]}'), ""
        return True, "0.999.0\nhttps://x.example/a.zip\n", ""

    updater._http_get = _slow_http
    got = []
    done_evt = threading.Event()

    def _done_a(res):
        got.append(("a", (res or {}).get("status")))
        if len(got) >= 2:
            done_evt.set()

    def _done_b(res):
        got.append(("b", (res or {}).get("status")))
        if len(got) >= 2:
            done_evt.set()

    updater.check_async(on_done=_done_a)
    time.sleep(0.05)
    updater.check_async(on_done=_done_b)      # 检查在飞 → 挂到待通知队列
    done_evt.wait(6)

    names = sorted(n for n, _ in got)
    check("A1  并发调用两个回调都收到结果", names == ["a", "b"], str(got))
    check("A2  两个回调拿到同类结果", len(set(s for _, s in got)) == 1, str(got))
    check("A3  只发起一次网络请求 (复用而非重复)", len(hits) == 1, str(hits))

    # 已有结论且未 force → 立即回调, 不再发请求
    hits[:] = []
    got2 = []
    updater.check_async(on_done=lambda r: got2.append(r))
    time.sleep(0.1)
    check("A4  已有结论时同步复用, 不再发请求", len(hits) == 0, str(hits))
    check("A5  复用回调仍被调用", len(got2) == 1, str(len(got2)))
finally:
    updater._http_get = real_http
    reset_updater_state()

# ══════════════════════════════════════════════════════════════════
print("[T] 传输双栈 (requests 证书失败 → urllib 兜底)")
real_get = updater.requests.get
real_urlopen = updater.urllib.request.urlopen
tmp = tempfile.mkdtemp(prefix="acrpa_transport_")


class _UrlResp(object):
    """urllib 风格的最小响应壳。"""

    status = 200

    def __init__(self, data):
        self._data = data
        self.headers = {"Content-Length": str(len(data))}

    def read(self, n=-1):
        out, self._data = self._data, b""
        return out

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def close(self):
        pass


class _Resp404(object):
    status_code = 404
    headers = {}


def _req_ssl_fail(*a, **kw):
    raise updater.requests.exceptions.SSLError("certificate verify failed")


try:
    updater.requests.get = _req_ssl_fail
    updater.urllib.request.urlopen = lambda req, timeout=None: _UrlResp(b"0.1.26\n")
    ok, text, err = updater._http_get("https://api.github.com/x", timeout=1)
    check("T1  requests 证书失败时 urllib 兜底成功",
          ok and text.strip() == "0.1.26", "{} / {}".format(ok, err))
    check("T2  兜底成功时不残留错误信息", err == "", err)

    # 服务端明确应答 404 时不应再换栈 (换栈解决不了 404, 只会拖慢并污染原因)
    hit = {"urllib": 0}

    def _count_urlopen(req, timeout=None):
        hit["urllib"] += 1
        return _UrlResp(b"should-not-happen")

    updater.requests.get = lambda *a, **kw: _Resp404()
    updater.urllib.request.urlopen = _count_urlopen
    ok, text, err = updater._http_get("https://x.example/missing", timeout=1)
    check("T3  明确 404 不换栈重试",
          (not ok) and "404" in err and hit["urllib"] == 0,
          "{} / {} / urllib={}".format(ok, err, hit["urllib"]))

    # 下载环节同样双栈: requests 抛证书错时必须改用 urllib 完成下载
    src_zip = os.path.join(tmp, "src.zip")
    with zipfile.ZipFile(src_zip, "w") as z:
        z.writestr("ACRPA/VERSION", "0.1.26\n")
    payload = open(src_zip, "rb").read()

    updater.requests.get = _req_ssl_fail
    updater.urllib.request.urlopen = lambda req, timeout=None: _UrlResp(payload)
    part = os.path.join(tmp, "pkg.part")
    okd, why = updater._download_one(
        "https://raw.githubusercontent.com/x/ACRPA.zip", part,
        None, None, 1, "", len(payload))
    check("T4  下载在 requests 失败时改用 urllib 完成", okd, str(why))
    check("T5  兜底下载内容与源一致",
          okd and open(part, "rb").read() == payload,
          "size={}".format(os.path.getsize(part) if os.path.exists(part) else -1))
finally:
    updater.requests.get = real_get
    updater.urllib.request.urlopen = real_urlopen
    shutil.rmtree(tmp, ignore_errors=True)

# ══════════════════════════════════════════════════════════════════
print("[R] 冻结路径解析 (打包后版本号来源)")
tmp = tempfile.mkdtemp(prefix="acrpa_frozen_")
try:
    exe_dir = os.path.join(tmp, "app")
    os.makedirs(exe_dir)
    with open(os.path.join(exe_dir, "VERSION"), "w", encoding="utf-8") as f:
        f.write("9.9.9\nhttps://exe.example/a.zip\n")

    saved = (getattr(sys, "frozen", None), sys.executable,
             getattr(sys, "_MEIPASS", None))
    saved_frozen = sys.__dict__.get("frozen")
    try:
        sys.frozen = True
        sys.executable = os.path.join(exe_dir, "ACRPA v9.9.9.exe")
        sys._MEIPASS = os.path.join(tmp, "meipass")

        roots = vi._candidate_roots()
        check("R1  EXE 同级目录优先于内嵌副本与项目根",
              roots[0] == exe_dir, str(roots[:2]))
        check("R2  内嵌副本 (_MEIPASS) 进入候选",
              os.path.join(tmp, "meipass") in roots, str(roots))
        check("R3  冻结时优先读到 EXE 旁 VERSION (不再回退内置值)",
              vi.get_version() == "9.9.9", vi.get_version())
        check("R4  冻结时直链也来自 EXE 旁 VERSION",
              vi.get_download_url() == "https://exe.example/a.zip",
              vi.get_download_url())
        check("R5  冻结时应用目录 = EXE 所在目录",
              vi.get_app_dir() == exe_dir, vi.get_app_dir())
        check("R6  清单路径可诊断",
              vi.get_manifest_path() == os.path.join(exe_dir, "VERSION"),
              str(vi.get_manifest_path()))
    finally:
        sys.executable = saved[1]
        if saved_frozen is None:
            sys.__dict__.pop("frozen", None)
        else:
            sys.frozen = saved_frozen
        if saved[2] is None:
            sys.__dict__.pop("_MEIPASS", None)
        else:
            sys._MEIPASS = saved[2]

    check("R7  还原后回到源码运行 (读项目根 VERSION)",
          vi.get_version() == vi._FALLBACK_VERSION or
          vi.get_version() == open(os.path.join(BASE, "VERSION"),
                                   encoding="utf-8").readline().strip(),
          vi.get_version())
    check("R8  源码模式应用目录 = 项目根", vi.get_app_dir() == BASE, vi.get_app_dir())
    check("R9  版本一致性: 项目 VERSION == version_info.VERSION",
          vi.get_version() == vi.VERSION, "{} / {}".format(vi.get_version(), vi.VERSION))
finally:
    shutil.rmtree(tmp, ignore_errors=True)

# ══════════════════════════════════════════════════════════════════
print("[S] 自更新前置条件")
ok, why = updater.can_self_update()
check("S1  源码模式拒绝自更新并给出原因", (not ok) and bool(why), str(why))
check("S2  拒绝时不抛异常", isinstance(ok, bool))
check("S3  apply_update 在源码模式返回失败而非崩溃",
      updater.apply_update(os.path.join(BASE, "VERSION"))[0] is False)
check("S4  format_size 可读", updater.format_size(13 * 1024 * 1024) == "13.0 MB",
      updater.format_size(13 * 1024 * 1024))
check("S5  format_size 容忍非法输入", updater.format_size(None) == "未知",
      updater.format_size(None))

print("\n" + "=" * 56)
if _failed[0]:
    print("FAILED ({})  通过 {} / 共 {}".format(_failed[0], _passed[0],
                                             _passed[0] + _failed[0]))
    sys.exit(1)
print("ALL PASSED  ({} 项)".format(_passed[0]))
sys.exit(0)
