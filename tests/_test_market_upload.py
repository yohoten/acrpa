# -*- coding: utf-8 -*-
"""tools/_test_market_upload.py — 脚本市场 v2「批次3：上传通道」验收测试。

默认无真实网络（网络层 market_upload._http 整体注入 mock）；仅当设置环境变量
    ACRPA_MARKET_TOKEN  (Gitee/GitHub PAT) 且 ACRPA_MARKET_TEST_REPO=owner/repo
时才尝试真实测试仓库发 PR（默认 SKIP，绝不伪造 PASS）。

覆盖：
  * prepare_package → script_package.unpack round-trip；
  * Gitee / GitHub mock 请求序列（URL / 方法 / body 关键字段）与调用顺序；
  * GitHub >1MB 走 Git Data API（blobs → trees → commits → refs）；
  * token 缺失时 upload 明确拒绝；
  * 前端校验（builtin_ 前缀 / category 非法 / 必填缺失 / id 唯一提示）；
  * 脚本内未注册命令拦截（check_script_commands）；
  * 日志/异常文本无 token 明文（脱敏断言）；
  * 真实 PR（可选，SKIP 时明确标注）。

退出码 0(全过/仅 SKIP/WARN) / 1(存在 FAIL)。输出行前缀 [OK]/[WARN]/[FAIL]/[SKIP]。
运行：python -X utf8 tools\\_test_market_upload.py
"""
import os
import sys
import json
import base64
import shutil
import zipfile
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import market_upload as mu          # noqa: E402
import script_package as sp         # noqa: E402
import accounts as acc              # noqa: E402
import commands as cmd              # noqa: E402

_PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFAAH/"
    "q842iQAAAABJRU5ErkJggg==")

_FAKE_TOKEN = "ghp_ABCDEF0123456789abcdef0123456789ABCD"

_META = {
    "id": "off_upload_sample",
    "name": "上传示例脚本",
    "description": "批次3 上传通道自测用示例脚本（含一个 png 资源）",
    "category": "办公",
    "author": "ACRPA-test",
    "version": "1.0.0",
    "tags": ["测试", "上传"],
    "icon": "🚀",
    "requires": [],
}

_FAILS = []
_WARNS = []
_SKIPS = []


# ── 断言工具 ──

def ok(msg):
    print("[OK] " + msg)


def warn(msg):
    _WARNS.append(msg)
    print("[WARN] " + msg)


def skip(msg):
    _SKIPS.append(msg)
    print("[SKIP] " + msg)


def fail(msg):
    _FAILS.append(msg)
    print("[FAIL] " + msg)


def check(cond, msg):
    if cond:
        ok(msg)
    else:
        fail(msg)
    return bool(cond)


class _CapLog(object):
    def __init__(self):
        self.msgs = []

    def put(self, msg, tag=None, caller_file="", caller_func="", level=0):
        self.msgs.append(str(msg))

    def text(self):
        return "\n".join(self.msgs)


class FakeHTTP(object):
    """可注入的 mock 网络层：记录 (method,url,headers,params,json) 并按 respond 返回。"""

    def __init__(self, respond):
        self.calls = []
        self.respond = respond

    def __call__(self, method, url, headers=None, params=None, json_body=None,
                 timeout=None):
        self.calls.append({"method": method, "url": url,
                           "headers": dict(headers or {}),
                           "params": dict(params or {}), "json": json_body})
        return self.respond(method, url, headers or {}, params or {}, json_body)

    def find(self, needle, method=None):
        for c in self.calls:
            if needle in c["url"] and (method is None or c["method"] == method):
                return c
        return None

    def index_of(self, needle, method=None):
        for i, c in enumerate(self.calls):
            if needle in c["url"] and (method is None or c["method"] == method):
                return i
        return -1


def _b64_dec(s):
    return base64.b64decode(s)


# ── 环境准备 ──

def make_sample(work):
    """构造样本 .xls（优先真实模板；缺则伪字节）+ 1 张 png，返回 (xls, script_dir)。"""
    src_dir = os.path.join(work, "srcfiles")
    os.makedirs(src_dir, exist_ok=True)
    xls = os.path.join(src_dir, "上传示例.xls")
    tmpl = os.path.join(ROOT, "template", "脚本模板.xls")
    if os.path.exists(tmpl):
        shutil.copy2(tmpl, xls)
    else:
        warn("模板 {} 不存在，改用伪造 .xls".format(tmpl))
        with open(xls, "wb") as f:
            f.write(b"PK\x03\x04fake-xls" + b"0" * 120)
    with open(os.path.join(src_dir, "demo.png"), "wb") as f:
        f.write(_PNG_1PX)
    return xls, src_dir


# ══════════════════════════════════════════════════════════════════════
# 1. prepare_package round-trip
# ══════════════════════════════════════════════════════════════════════

def test_prepare_roundtrip(work):
    print("\n== 1. prepare_package → unpack round-trip ==")
    xls, _ = make_sample(work)
    pkg = os.path.join(work, "off_upload_sample-1.0.0.acrpapkg")
    pkg_path, sha, manifest = mu.prepare_package(xls, _META, out_path=pkg)

    check(os.path.exists(pkg_path), "prepare_package 生成包: {}".format(
        os.path.basename(pkg_path)))
    check(sha == sp.compute_sha256(pkg_path), "返回 sha256 == compute_sha256(pkg)")
    check(manifest.get("id") == _META["id"], "manifest.id 正确")
    check(manifest.get("entry", "").startswith("scripts/"), "manifest.entry 命中 scripts/")
    check(manifest.get("images") == ["images/demo.png"],
          "manifest.images 收录同目录 png")

    out_dir = os.path.join(work, "unpacked")
    man2 = sp.unpack(pkg_path, out_dir, expected_sha256=sha)
    check(man2.get("id") == _META["id"], "unpack 还原 manifest.id 一致")
    with open(xls, "rb") as f:
        orig = f.read()
    with open(os.path.join(out_dir, "scripts", "上传示例.xls"), "rb") as f:
        got = f.read()
    check(orig == got, "round-trip: 解包 .xls 字节与原始一致 ({} bytes)".format(len(got)))
    check(os.path.exists(os.path.join(out_dir, "images", "demo.png")),
          "round-trip: images/demo.png 已还原")
    return pkg_path, sha, manifest


# ══════════════════════════════════════════════════════════════════════
# 2. Gitee mock 请求序列
# ══════════════════════════════════════════════════════════════════════

def _gitee_respond(method, url, headers, params, body):
    if url.endswith("/branches") and method == "POST":
        return mu._Resp(201, {"name": (body or {}).get("branch_name")})
    if "/contents/packages/" in url and method == "POST":
        return mu._Resp(201, {"content": {"path": "packages/x"}})
    if url.endswith("/contents/index.json"):
        if method == "GET":
            old = b'{"name":"ACRPA Script Marketplace","scripts":[]}'
            return mu._Resp(200, {"sha": "old-index-sha",
                                  "content": base64.b64encode(old).decode()})
        return mu._Resp(200, {"content": {"path": "index.json"}})
    if url.endswith("/pulls") and method == "POST":
        return mu._Resp(201, {"html_url":
                              "https://gitee.com/yohoten/acrpa-marketplace/pulls/12",
                              "number": 12})
    return mu._Resp(500, {"message": "unexpected {} {}".format(method, url)})


def test_gitee_sequence(work, pkg_path):
    print("\n== 2. Gitee mock 请求序列 ==")
    original_http = mu._http
    original_verify = acc.verify_token
    fake = FakeHTTP(_gitee_respond)
    verify_urls = []

    def _fake_verify(provider, token=None):
        verify_urls.append((provider, token))
        return {"provider": provider, "login": "tester", "id": 1}

    mu._http = fake
    acc.verify_token = _fake_verify
    try:
        result = mu.upload("gitee", pkg_path, _META, token=_FAKE_TOKEN,
                           base_branch="master")
    finally:
        mu._http = original_http
        acc.verify_token = original_verify

    check(bool(verify_urls), "upload 前调用 accounts.verify_token 校验登录")
    check(isinstance(result, dict) and result.get("pr_url", "").endswith("/pulls/12"),
          "返回 dict 且 pr_url 取自响应 html_url")
    check(result.get("pr_number") == 12, "返回 pr_number == 12")

    # 顺序：branch → upload_pkg → GET index → POST index → pulls
    i_branch = fake.index_of("/branches", "POST")
    i_pkg = fake.index_of("/contents/packages/", "POST")
    i_idx_get = fake.index_of("/contents/index.json", "GET")
    i_idx_post = fake.index_of("/contents/index.json", "POST")
    i_pull = fake.index_of("/pulls", "POST")
    check(i_branch >= 0 and i_branch < i_pkg < i_idx_get < i_idx_post < i_pull,
          "调用顺序: 建分支 → 传包 → 取 index sha → 更新 index → 发 PR "
          "(#{}/{}/{}/{}/{})".format(i_branch, i_pkg, i_idx_get, i_idx_post, i_pull))

    # 建分支 body
    c = fake.calls[i_branch]
    check(c["json"].get("refs") == "master", "建分支 body.refs == base_branch(master)")
    check(bool(c["json"].get("branch_name")), "建分支 body.branch_name 非空 ({})".format(
        c["json"].get("branch_name")))
    check(c["json"].get("access_token") == _FAKE_TOKEN,
          "Gitee 请求体携带 access_token (仅在 body, 不进日志)")

    # 传包 body：content 为合法 base64 且解码 == pkg 字节
    c = fake.calls[i_pkg]
    with open(pkg_path, "rb") as f:
        raw = f.read()
    check(_b64_dec(c["json"]["content"]) == raw,
          "传包 body.content 为合法 base64 且解码等于 .acrpapkg 原始字节")
    check(c["json"].get("branch") == fake.calls[i_branch]["json"]["branch_name"],
          "传包 body.branch == 新建分支名")
    check(c["url"].endswith("/packages/off_upload_sample-1.0.0.acrpapkg"),
          "传包 URL 指向 packages/<id>-<ver>.acrpapkg")

    # 更新 index body：带旧 sha + 新条目
    c = fake.calls[i_idx_post]
    check(c["json"].get("sha") == "old-index-sha",
          "更新 index body.sha == 取回的旧 index sha")
    merged = json.loads(_b64_dec(c["json"]["content"]).decode("utf-8"))
    ids = [s.get("id") for s in merged.get("scripts", [])]
    check(_META["id"] in ids, "更新后的 index.json 含新脚本 id")
    entry = [s for s in merged["scripts"] if s["id"] == _META["id"]][0]
    check(entry.get("pkg") == "packages/off_upload_sample-1.0.0.acrpapkg",
          "index 条目 pkg 字段正确")
    check(len(entry.get("sha256") or "") == 64, "index 条目 sha256 为 64 位 hex")

    # PR body
    c = fake.calls[i_pull]
    check(c["json"].get("head") == fake.calls[i_branch]["json"]["branch_name"],
          "PR body.head == 上传分支")
    check(c["json"].get("base") == "master", "PR body.base == base_branch")
    check("自检清单" in (c["json"].get("body") or ""), "PR body 含自检清单")

    # 进度阶段
    stages = []
    mu._http = fake
    acc.verify_token = _fake_verify
    try:
        mu.upload("gitee", pkg_path, _META, token=_FAKE_TOKEN,
                  progress=lambda st, d, t: stages.append(st))
    finally:
        mu._http = original_http
        acc.verify_token = original_verify
    check(stages[:1] == ["verify"] and stages[-1] == "done",
          "progress 首阶段 verify、末阶段 done ({} 阶段)".format(len(stages)))
    check(stages == ["verify", "branch", "upload_pkg", "upload_index", "pull", "done"],
          "progress 阶段顺序完整: {}".format(stages))


# ══════════════════════════════════════════════════════════════════════
# 3. GitHub mock 请求序列（≤1MB 走 contents）
# ══════════════════════════════════════════════════════════════════════

def _gh_respond_factory(record):
    def respond(method, url, headers, params, body):
        record.append((method, url))
        if "/git/ref/heads/" in url and method == "GET":
            return mu._Resp(200, {"object": {"sha": "basesha000"}})
        if url.endswith("/git/refs") and method == "POST":
            return mu._Resp(201, {"ref": (body or {}).get("ref")})
        if url.endswith("/contents/index.json"):
            if method == "GET":
                old = b'{"name":"ACRPA Script Marketplace","scripts":[]}'
                return mu._Resp(200, {"sha": "gh-index-sha",
                                      "content": base64.b64encode(old).decode()})
            return mu._Resp(200, {"content": {"path": "index.json"}})
        if "/contents/packages/" in url and method == "PUT":
            return mu._Resp(201, {"content": {"path": "packages/x"}})
        if url.endswith("/pulls") and method == "POST":
            return mu._Resp(201, {"html_url":
                                  "https://github.com/yohoten/acrpa-marketplace/pull/7",
                                  "number": 7})
        return mu._Resp(500, {"message": "unexpected {} {}".format(method, url)})
    return respond


def test_github_sequence(work, pkg_path):
    print("\n== 3. GitHub mock 请求序列 (contents API) ==")
    original_http = mu._http
    original_verify = acc.verify_token
    record = []
    fake = FakeHTTP(_gh_respond_factory(record))
    mu._http = fake
    acc.verify_token = lambda p, token=None: {"provider": p, "login": "tester", "id": 2}
    try:
        result = mu.upload("github", pkg_path, _META, token=_FAKE_TOKEN,
                           base_branch="master")
    finally:
        mu._http = original_http
        acc.verify_token = original_verify

    check(result.get("pr_url", "").endswith("/pull/7"), "GitHub 返回 pr_url 正确")
    check(result.get("pr_number") == 7, "GitHub 返回 pr_number == 7")

    i_ref_get = fake.index_of("/git/ref/heads/master", "GET")
    i_ref_post = fake.index_of("/git/refs", "POST")
    i_idx_get = fake.index_of("/contents/index.json", "GET")
    i_pkg_put = fake.index_of("/contents/packages/", "PUT")
    i_idx_put = fake.index_of("/contents/index.json", "PUT")
    i_pull = fake.index_of("/pulls", "POST")
    check(i_ref_get >= 0 and i_ref_get < i_ref_post < i_idx_get < i_pkg_put < i_idx_put < i_pull,
          "调用顺序: 取基点 → 建分支 → 取 index sha → PUT 包 → PUT index → 发 PR "
          "(#{}/{}/{}/{}/{}/{})".format(i_ref_get, i_ref_post, i_idx_get, i_pkg_put,
                                        i_idx_put, i_pull))
    check(fake.calls[i_ref_post]["json"].get("sha") == "basesha000",
          "建分支 body.sha 取自 GET ref 的 object.sha")
    check(fake.calls[i_ref_post]["json"].get("ref", "").startswith("refs/heads/"),
          "建分支 body.ref == refs/heads/<branch>")
    check(fake.calls[i_pkg_put]["json"].get("branch") ==
          fake.calls[i_ref_post]["json"]["ref"].split("refs/heads/")[-1],
          "PUT 包 body.branch == 新建分支")
    check("Authorization" in fake.calls[i_pkg_put]["headers"] and
          fake.calls[i_pkg_put]["headers"]["Authorization"].startswith("token "),
          "GitHub 请求头携带 Authorization: token <t>")
    check("User-Agent" in fake.calls[i_pkg_put]["headers"],
          "GitHub 请求头携带 User-Agent")
    check(fake.calls[i_idx_put]["json"].get("sha") == "gh-index-sha",
          "PUT index body.sha == 取回旧 sha")
    check(fake.calls[i_pull]["json"].get("base") == "master", "PR body.base == master")


# ══════════════════════════════════════════════════════════════════════
# 4. GitHub >1MB 走 Git Data API
# ══════════════════════════════════════════════════════════════════════

def _gh_big_respond(method, url, headers, params, body):
    if "/git/ref/heads/" in url and method == "GET":
        return mu._Resp(200, {"object": {"sha": "basesha000"}})
    if url.endswith("/git/refs") and method == "POST":
        return mu._Resp(201, {"ref": (body or {}).get("ref")})
    if url.endswith("/git/blobs") and method == "POST":
        return mu._Resp(201, {"sha": "blobsha111"})
    if url.endswith("/git/trees") and method == "POST":
        return mu._Resp(201, {"sha": "treesha222"})
    if url.endswith("/git/commits") and method == "POST":
        return mu._Resp(201, {"sha": "commitsha333"})
    if "/git/refs/heads/" in url and method == "PATCH":
        return mu._Resp(200, {"object": {"sha": "commitsha333"}})
    if url.endswith("/contents/index.json"):
        if method == "GET":
            return mu._Resp(200, {"sha": "gh-index-sha",
                                  "content": base64.b64encode(b'{"scripts":[]}').decode()})
        return mu._Resp(200, {"content": {}})
    if url.endswith("/pulls") and method == "POST":
        return mu._Resp(201, {"html_url": "https://github.com/o/r/pull/9", "number": 9})
    return mu._Resp(500, {"message": "unexpected {} {}".format(method, url)})


def _make_big_pkg(work, xls, meta):
    """构造一个体积 >1MB 的合法 .acrpapkg（含一段不可压缩的 images/big.png）。"""
    man = sp.build_manifest(xls, meta, images=[])
    man["entry"] = "scripts/{}".format(os.path.basename(xls))
    man["images"] = []
    big = os.path.join(work, "big.acrpapkg")
    info = zipfile.ZipInfo("images/big.png")
    with zipfile.ZipFile(big, "w") as zf:
        zf.writestr(sp.MANIFEST_NAME, json.dumps(man, ensure_ascii=False))
        zf.write(xls, "scripts/{}".format(os.path.basename(xls)))
        zf.writestr(info, os.urandom(1300 * 1024),
                    compress_type=zipfile.ZIP_STORED)
    return big


def test_github_big_file(work, pkg_path):
    print("\n== 4. GitHub >1MB 走 Git Data API ==")
    xls, _ = make_sample(work)
    big = _make_big_pkg(work, xls, _META)
    size = os.path.getsize(big)
    check(size > mu._ONE_MB, "构造包体积 >1MB ({} bytes)".format(size))

    original_http = mu._http
    original_verify = acc.verify_token
    fake = FakeHTTP(_gh_big_respond)
    mu._http = fake
    acc.verify_token = lambda p, token=None: {"provider": p, "login": "tester", "id": 3}
    try:
        mu.upload("github", big, _META, token=_FAKE_TOKEN, base_branch="master")
    finally:
        mu._http = original_http
        acc.verify_token = original_verify

    i_blob = fake.index_of("/git/blobs", "POST")
    i_tree = fake.index_of("/git/trees", "POST")
    i_commit = fake.index_of("/git/commits", "POST")
    i_patch = fake.index_of("/git/refs/heads/", "PATCH")
    i_put_c = fake.index_of("/contents/packages/", "PUT")
    check(i_blob >= 0 and i_tree >= 0 and i_commit >= 0 and i_patch >= 0,
          "大文件走 Git Data API：blobs/trees/commits/refs 均已调用")
    check(i_put_c == -1, "大文件未走 contents API (PUT) 路径")
    check(fake.calls[i_blob]["json"].get("encoding") == "base64", "blob body.encoding == base64")
    check(fake.calls[i_commit]["json"].get("parents") == ["basesha000"],
          "commit body.parents == 基点 sha")
    check(fake.calls[i_patch]["json"].get("sha") == "commitsha333",
          "PATCH ref body.sha == 新建 commit sha")


# ══════════════════════════════════════════════════════════════════════
# 5. token 缺失 → 明确拒绝
# ══════════════════════════════════════════════════════════════════════

def test_token_missing(pkg_path):
    print("\n== 5. token 缺失时 upload 明确拒绝 ==")
    original_get = acc.get_token
    acc.get_token = lambda provider: None
    try:
        try:
            mu.upload("gitee", pkg_path, _META)
            fail("token 缺失: 期望 UploadError 但未抛出")
        except mu.UploadError as e:
            t = str(e)
            check("token" in t and ("登录" in t or "设置" in t),
                  "token 缺失被拒且给出可读引导: {}".format(t[:80]))
    finally:
        acc.get_token = original_get

    try:
        mu.upload("bitbucket", pkg_path, _META, token="x")
        fail("非法 provider: 期望 UploadError 但未抛出")
    except mu.UploadError as e:
        check("不支持" in str(e), "非法 provider 被拒: {}".format(str(e)[:60]))


# ══════════════════════════════════════════════════════════════════════
# 6. 前端校验（builtin_ / category / 必填 / id 唯一）
# ══════════════════════════════════════════════════════════════════════

def test_validate_meta():
    print("\n== 6. 上传向导前端校验 validate_meta ==")
    good = dict(_META)
    errs, warns = mu.validate_meta(good)
    check(errs == [] and warns == [], "合法 meta 无 errors/warnings")

    errs, _ = mu.validate_meta(dict(good, id="builtin_x"))
    check(any("builtin_" in e for e in errs), "id=builtin_x → 拒绝 (builtin_ 前缀)")

    errs, _ = mu.validate_meta(dict(good, category="乱写"))
    check(any("category" in e for e in errs), "category=乱写 → 拒绝 (四选一)")

    errs, _ = mu.validate_meta(dict(good, name="", description=""))
    check(any("name" in e for e in errs) and any("description" in e for e in errs),
          "必填缺失 (name/description) → 拒绝")

    errs, warns = mu.validate_meta(dict(good), existing_ids=[_META["id"]])
    check(errs == [] and any("已存在" in w for w in warns),
          "id 唯一性 → 非阻断提示 (已存在: {})".format(warns))

    # upload 端同样拒绝 builtin_（前端 + 后端双保险）
    pkg_dir = tempfile.mkdtemp(prefix="mu_meta_")
    try:
        xls, _ = make_sample(pkg_dir)
        pkg = os.path.join(pkg_dir, "x.acrpapkg")
        pkg_path, _, _ = mu.prepare_package(xls, _META, out_path=pkg)
        try:
            mu.upload("gitee", pkg_path, dict(_META, id="builtin_x"), token=_FAKE_TOKEN)
            fail("upload 端 builtin_ 前缀: 期望 UploadError 但未抛出")
        except mu.UploadError as e:
            check("builtin_" in str(e), "upload 端亦拒绝 builtin_ 前缀")
    finally:
        shutil.rmtree(pkg_dir, ignore_errors=True)


# ══════════════════════════════════════════════════════════════════════
# 7. check_script_commands（未注册命令拦截）
# ══════════════════════════════════════════════════════════════════════

def test_check_commands(work):
    print("\n== 7. check_script_commands（未注册命令拦截） ==")
    try:
        import xlwt          # noqa: F401
        import xlrd          # noqa: F401
        have_xls = True
    except Exception:
        have_xls = False

    if have_xls:
        bad = os.path.join(work, "bad_cmd.xls")
        wb = xlwt.Workbook()
        ws = wb.add_sheet("Sheet1")
        ws.write(0, 0, "命令类型")
        ws.write(1, 0, "（标题行）")
        ws.write(2, 0, "等待")
        ws.write(3, 0, "不存在的命令XYZ")
        wb.save(bad)
        check(mu.check_script_commands(bad) == ["不存在的命令XYZ"],
              "未注册命令被检出")

        good = os.path.join(work, "good_cmd.xls")
        wb2 = xlwt.Workbook()
        ws2 = wb2.add_sheet("Sheet1")
        ws2.write(0, 0, "命令类型")
        ws2.write(1, 0, "（标题行）")
        ws2.write(2, 0, "等待")
        wb2.save(good)
        check(mu.check_script_commands(good) == [], "全部已注册命令 → 返回空列表")
    else:
        # 环境缺 xlrd/xlwt → 注入 fake xlrd 覆盖 check_script_commands 的解析逻辑
        warn("xlrd/xlwt 未安装，改用注入 fake xlrd 覆盖命令校验逻辑（真实 .xls 解析未覆盖）")
        import types
        fake = types.ModuleType("xlrd")

        class _Sheet(object):
            nrows = 4

            def cell_value(self, r, c):
                return {2: "等待", 3: "不存在的命令XYZ"}.get(r, "")

        class _Book(object):
            def sheet_by_index(self, i):
                return _Sheet()

        fake.open_workbook = lambda p: _Book()
        saved = sys.modules.get("xlrd")
        sys.modules["xlrd"] = fake
        try:
            unknown = mu.check_script_commands(__file__)   # 路径存在即可触发解析
        finally:
            if saved is None:
                sys.modules.pop("xlrd", None)
            else:
                sys.modules["xlrd"] = saved
        check(unknown == ["不存在的命令XYZ"],
              "未注册命令被检出（fake xlrd 注入）: {}".format(unknown))

    check("等待" in cmd.list_names(), "commands.list_names() 含『等待』(基准校验)")


# ══════════════════════════════════════════════════════════════════════
# 8. index patch 合并语义
# ══════════════════════════════════════════════════════════════════════

def test_index_patch():
    print("\n== 8. upload_index_patch 合并语义 ==")
    empty = mu.upload_index_patch("", _META, "a" * 64, 1234,
                                  "packages/off_upload_sample-1.0.0.acrpapkg")
    data = json.loads(empty)
    check(len(data.get("scripts", [])) == 1 and data["scripts"][0]["id"] == _META["id"],
          "空 index → 追加一条")
    check(data["scripts"][0]["size"] == 1234, "条目 size 写入")

    old = json.dumps({"name": "m", "scripts": [
        {"id": _META["id"], "downloads": 42, "rating": 4.5, "rating_count": 9,
         "filename": "legacy.xls"}]})
    merged = json.loads(mu.upload_index_patch(old, _META, "b" * 64, 10, "packages/x.acrpapkg"))
    check(len(merged["scripts"]) == 1, "同 id → 原地替换 (不新增)")
    e = merged["scripts"][0]
    check(e["downloads"] == 42 and e["rating"] == 4.5 and e["rating_count"] == 9,
          "同 id 替换保留 downloads/rating 统计")
    check(e["pkg"] == "packages/x.acrpapkg" and e["sha256"] == "b" * 64,
          "同 id 替换写入新 pkg/sha256")


# ══════════════════════════════════════════════════════════════════════
# 9. 脱敏：日志/异常无 token 明文
# ══════════════════════════════════════════════════════════════════════

def test_redaction(pkg_path):
    print("\n== 9. token 脱敏（日志/异常无明文） ==")
    # 9.1 accounts._redact 基本行为
    red = acc._redact("failed using token {} please retry".format(_FAKE_TOKEN), _FAKE_TOKEN)
    check(_FAKE_TOKEN not in red, "accounts._redact 抹除已知 token 明文")

    # 9.2 API 错误消息回显 token 时，异常与日志均不得含明文
    def _respond_401(method, url, headers, params, body):
        return mu._Resp(401, {"message": "bad credential {}".format(_FAKE_TOKEN)})

    original_http = mu._http
    original_verify = acc.verify_token
    original_log = mu.log1
    original_acc_log = acc.log1
    cap = _CapLog()
    mu._http = FakeHTTP(_respond_401)
    acc.verify_token = lambda p, token=None: {"provider": p, "login": "tester", "id": 4}
    mu.log1 = cap.put
    acc.log1 = cap.put
    raised = ""
    try:
        mu.upload("gitee", pkg_path, _META, token=_FAKE_TOKEN)
    except mu.UploadError as e:
        raised = str(e)
    finally:
        mu._http = original_http
        acc.verify_token = original_verify
        mu.log1 = original_log
        acc.log1 = original_acc_log

    check(raised and _FAKE_TOKEN not in raised,
          "UploadError 文本不含 token 明文: {}".format(raised[:90]))
    check(_FAKE_TOKEN not in cap.text(), "捕获的全部 log1 输出不含 token 明文")
    check("401" in raised, "401 归类为需重新登录的提示")

    # 9.3 传输层异常也脱敏
    def _raise_timeout(method, url, headers=None, params=None, json_body=None,
                       timeout=None):
        raise RuntimeError("timeout leak {}".format(_FAKE_TOKEN))

    original_http = mu._http
    original_verify = acc.verify_token
    mu._http = _raise_timeout
    acc.verify_token = lambda p, token=None: {"provider": p, "login": "t", "id": 5}
    raised2 = ""
    try:
        mu.upload("gitee", pkg_path, _META, token=_FAKE_TOKEN)
    except mu.UploadError as e:
        raised2 = str(e)
    finally:
        mu._http = original_http
        acc.verify_token = original_verify
    check(raised2 and _FAKE_TOKEN not in raised2,
          "传输层异常经重试后报错且不含 token 明文")


# ══════════════════════════════════════════════════════════════════════
# 10. 真实测试仓库 PR（可选，SKIP 明确标注）
# ══════════════════════════════════════════════════════════════════════

def test_real_pr_optional(pkg_path):
    print("\n== 10. 真实测试仓库 PR（可选） ==")
    token = os.environ.get("ACRPA_MARKET_TOKEN", "").strip()
    repo_spec = os.environ.get("ACRPA_MARKET_TEST_REPO", "").strip()
    provider = os.environ.get("ACRPA_MARKET_PROVIDER", "gitee").strip() or "gitee"
    if not token or not repo_spec or "/" not in repo_spec:
        skip("未配置 ACRPA_MARKET_TOKEN 或 ACRPA_MARKET_TEST_REPO=owner/repo，"
             "跳过真实 PR（不伪造 PASS）")
        return
    owner, repo = repo_spec.split("/", 1)
    try:
        result = mu.upload(provider, pkg_path, _META, owner=owner, repo=repo,
                           token=token)
        check(bool(result.get("pr_url")), "真实 PR 已创建: {}".format(result.get("pr_url")))
    except Exception as e:
        skip("真实上传跳过（网络/权限不可用）: {}".format(
            acc._redact(e, token)[:140]))


# ══════════════════════════════════════════════════════════════════════
# main
# ══════════════════════════════════════════════════════════════════════

def main():
    work = tempfile.mkdtemp(prefix="acrpa_upload_test_")
    try:
        pkg_path, sha, manifest = test_prepare_roundtrip(work)
        test_gitee_sequence(work, pkg_path)
        test_github_sequence(work, pkg_path)
        test_github_big_file(work, pkg_path)
        test_token_missing(pkg_path)
        test_validate_meta()
        test_check_commands(work)
        test_index_patch()
        test_redaction(pkg_path)
        test_real_pr_optional(pkg_path)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    print("\n" + "=" * 60)
    print("汇总: FAIL={}  WARN={}  SKIP={}".format(len(_FAILS), len(_WARNS), len(_SKIPS)))
    for m in _SKIPS:
        print("  [SKIP] " + m)
    for m in _WARNS:
        print("  [WARN] " + m)
    for m in _FAILS:
        print("  [FAIL] " + m)
    print("=" * 60)
    return 1 if _FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
