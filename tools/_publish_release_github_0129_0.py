# -*- coding: utf-8 -*-
"""一次性发布助手: 为 tag v0.1.29.0 建【草稿 Pre-release】-> 上传 exe -> 发布 -> 核验。

为何不复用 tools/publish_release.py:
    该工具把 prerelease 硬编码为 False。而客户端的首选更新来源是
    `GET /repos/{owner}/{repo}/releases/latest`(不含预发布)。若本版以正式版
    发布，处于 0.1.29-beta 的客户端会被判定"有新版本"(0.1.29.0 > 0.1.29-beta)，
    但 Release API 分支只会把本机 VERSION 里的 sha256 当期望值，与新包必然
    不符 -> 下载校验失败。因此本版必须以 **Pre-release** 发布，让更新走
    「原始清单(VERSION)」通道。

附件名必须与 VERSION 直链最后一段完全一致: ACRPA-v0.1.29-beta.exe
(磁盘上的构建产物名为 "ACRPA v0.1.29-beta.exe"，故先按附件名复制一份)。
"""
import hashlib
import json
import os
import shutil
import sys
import urllib.parse

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "tools"))
import publish_release as pr            # noqa: E402  复用取 token / api / curl 上传

REPO = "yohoten/acrpa"
TAG = "v0.1.29.0"
RELEASE_NAME = "ACRPA v0.1.29.0 (Pre-release)"
SRC = os.path.join(BASE, "dist", "ACRPA v0.1.29-beta", "ACRPA v0.1.29-beta.exe")
ASSET_NAME = "ACRPA-v0.1.29-beta.exe"
STAGE = os.path.join(BASE, "dist", ASSET_NAME)
BODY_PATH = os.path.join(BASE, "docs", "releases", "v0.1.29-beta.md")
RESULT = os.path.join(BASE, "tools", "_publish_release_github_0129_0_result.json")

CTA = "application/octet-stream"


def log(msg):
    print(msg)
    sys.stdout.flush()


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    result = {"repo": REPO, "tag": TAG, "asset_name": ASSET_NAME, "steps": {}}

    if not os.path.isfile(SRC):
        log("[FAIL] 找不到构建产物: {}".format(SRC))
        return 1
    shutil.copy2(SRC, STAGE)
    size = os.path.getsize(STAGE)
    digest = sha256_of(STAGE)
    result["asset"] = {"stage": STAGE, "size": size, "sha256": digest}
    log("[..] 附件已就位: {} ({} bytes, sha256 {})".format(ASSET_NAME, size, digest))

    with open(BODY_PATH, encoding="utf-8") as f:
        body = f.read()
    result["body_len"] = len(body)

    token, err = pr.git_token()
    if not token:
        log("[FAIL] 取不到 GitHub 凭据: {}".format(err))
        return 1
    log("[OK] 已从 git credential helper 取得 GitHub 凭据 (不打印)")

    # ── 1. 定位 / 创建草稿 Release ──
    ok, rel, why = pr.api("GET", "/repos/{}/releases/tags/{}".format(REPO, TAG), token)
    exists = bool(ok and isinstance(rel, dict) and rel.get("id"))
    if exists:
        result["steps"]["get"] = {"status": "exists", "id": rel.get("id"),
                                  "draft": rel.get("draft"),
                                  "prerelease": rel.get("prerelease"),
                                  "immutable": rel.get("immutable")}
        log("[OK] Release {} 已存在 (id={}, draft={}, prerelease={})".format(
            TAG, rel.get("id"), rel.get("draft"), rel.get("prerelease")))
        if not rel.get("draft"):
            have = {a.get("name") for a in (rel.get("assets") or [])}
            if ASSET_NAME in have:
                log("[OK] 附件已存在且已发布, 无需操作。")
                result["state"] = "ALREADY_PUBLISHED"
                _verify(token, rel, size, digest, result)
                return 0
            log("[FAIL] Release 已发布且缺少附件, 而本仓库 release 不可变, 无法补传。")
            result["state"] = "IMMUTABLE_NO_ASSET"
            return 1
        rel_id = rel["id"]
        # 草稿: 正文/预发布标志对齐
        okp, rel2, whyp = pr.api("PATCH", "/repos/{}/releases/{}".format(REPO, rel_id),
                                 token, payload={"name": RELEASE_NAME, "body": body,
                                                 "prerelease": True})
        if okp:
            rel = rel2
            log("[OK] 草稿正文/名称已对齐 (prerelease=True)")
        else:
            log("[WARN] 更新草稿失败: {}".format(whyp))
    else:
        okc, rel2, whyc = pr.api("POST", "/repos/{}/releases".format(REPO), token, payload={
            "tag_name": TAG,
            "name": RELEASE_NAME,
            "body": body,
            "draft": True,
            "prerelease": True,
        })
        if not okc:
            log("[FAIL] 建草稿 Release 失败: {}".format(whyc))
            result["steps"]["create"] = {"ok": False, "error": whyc}
            return 1
        rel = rel2
        rel_id = rel["id"]
        result["steps"]["create"] = {"ok": True, "id": rel_id,
                                     "draft": rel.get("draft"),
                                     "prerelease": rel.get("prerelease"),
                                     "html_url": rel.get("html_url")}
        log("[OK] 已建立草稿 Release {} (id={}, prerelease={})".format(
            TAG, rel_id, rel.get("prerelease")))

    # ── 2. 上传附件 ──
    have = {a.get("name"): (a.get("id"), a.get("size"), a.get("digest"))
            for a in (rel.get("assets") or [])}
    cur = have.get(ASSET_NAME)
    if cur and cur[1] == size:
        log("[OK] 附件已存在且体积一致 ({}), 跳过重传".format(size))
        result["steps"]["upload"] = {"ok": True, "skipped": True, "size": cur[1]}
    else:
        if cur:
            okd, _, whyd = pr.api("DELETE", "/repos/{}/releases/assets/{}".format(REPO, cur[0]),
                                  token)
            log("[{}] 删除同名旧附件: {}".format("OK" if okd else "WARN", whyd or ""))

        up = rel.get("upload_url") or \
            "https://uploads.github.com/repos/{}/releases/{}/assets".format(REPO, rel_id)
        up = up.split("{")[0]
        target = "{}?{}".format(up, urllib.parse.urlencode({"name": ASSET_NAME}))
        log("[..] 上传 {} ({:.2f} MB) …".format(ASSET_NAME, size / 1048576.0))

        ok1, res1, why1 = pr.api("POST", target, token, raw=open(STAGE, "rb").read(),
                                 ctype=CTA, timeout=3600, absolute=True)
        if not ok1:
            log("[..] urllib 通道未通({}), 改用 curl 通道 …".format(str(why1)[:160]))
            ok1, res1, why1 = pr.curl_upload(target, STAGE, token, CTA, 3600)
        if not ok1:
            log("[FAIL] 上传失败: {}".format(why1))
            result["steps"]["upload"] = {"ok": False, "error": str(why1)[:400]}
            _dump(result)
            return 1
        got = None
        if isinstance(res1, (bytes, bytearray)):
            try:
                res1 = json.loads(res1.decode("utf-8"))
            except Exception:
                res1 = None
        if isinstance(res1, dict):
            got = res1.get("size")
        log("[OK] 已上传 {} (服务端 size={} / 本地 {})".format(ASSET_NAME, got, size))
        result["steps"]["upload"] = {"ok": True, "server_size": got, "local_size": size}
        if got is not None and got != size:
            log("[FAIL] 服务端体积与本地不一致, 中止发布。")
            _dump(result)
            return 1

    # ── 3. 发布草稿 ──
    okp, rel3, whyp = pr.api("PATCH", "/repos/{}/releases/{}".format(REPO, rel_id), token,
                             payload={"draft": False})
    if not okp:
        log("[FAIL] 发布失败(附件已上传, 可人工发布): {}".format(whyp))
        result["steps"]["publish"] = {"ok": False, "error": str(whyp)[:400]}
        _dump(result)
        return 1
    log("[OK] 已发布 Release {} —— 直链现已可用".format(TAG))
    result["steps"]["publish"] = {"ok": True}

    # ── 4. 核验 ──
    _verify(token, rel3, size, digest, result)
    return 0


def _verify(token, rel, size, digest, result):
    ok, cur, why = pr.api("GET", "/repos/{}/releases/tags/{}".format(REPO, TAG), token)
    if not ok:
        log("[WARN] 复核读取失败: {}".format(why))
        return
    assets = cur.get("assets") or []
    a = assets[0] if assets else {}
    log("\n=== Release 复核 ===")
    log("tag_name   : {}".format(cur.get("tag_name")))
    log("name       : {}".format(cur.get("name")))
    log("draft      : {}   prerelease: {}".format(cur.get("draft"), cur.get("prerelease")))
    log("immutable  : {}".format(cur.get("immutable")))
    log("html_url   : {}".format(cur.get("html_url")))
    for x in assets:
        log("asset      : {} | size={} | digest={}".format(
            x.get("name"), x.get("size"), x.get("digest")))
        log("download   : {}".format(x.get("browser_download_url")))
    ok_digest = (a.get("digest") or "").lower() == "sha256:" + digest or \
                (a.get("digest") or "").lower() == digest
    result["state"] = "PUBLISHED"
    result["verify"] = {
        "tag_name": cur.get("tag_name"),
        "draft": cur.get("draft"),
        "prerelease": cur.get("prerelease"),
        "immutable": cur.get("immutable"),
        "html_url": cur.get("html_url"),
        "assets": [{"name": x.get("name"), "size": x.get("size"),
                    "digest": x.get("digest"),
                    "url": x.get("browser_download_url")} for x in assets],
        "size_match": bool(assets) and a.get("size") == size,
        "digest_match": ok_digest,
    }
    log("size_match : {}".format(result["verify"]["size_match"]))
    log("digest_match: {}".format(result["verify"]["digest_match"]))
    _dump(result)


def _dump(result):
    with open(RESULT, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    log("结果已写入: {}".format(os.path.relpath(RESULT, BASE)))


if __name__ == "__main__":
    sys.exit(main())
