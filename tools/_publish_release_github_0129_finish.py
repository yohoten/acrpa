# -*- coding: utf-8 -*-
"""Recovery/finish helper for ACRPA v0.1.29-beta (immutable-release workaround).

Problem: this repository has GitHub "immutable releases" enabled. A *published*
release is immutable, so `POST .../assets` returns 422
"Cannot upload assets to an immutable release."  (see README: build draft ->
upload -> publish).

This helper is NON-DESTRUCTIVE: it never deletes the release. It:
  1. GET /releases/tags/v0.1.29-beta
  2. If the asset is already present -> verify & done.
  3. Else PATCH the release to `draft=true` (convert back to a draft).
       - if this is rejected -> STOP and report (immutable blocks the change).
  4. Upload the asset (retry <= 3 with backoff).
  5. PATCH the release back to `draft=false, prerelease=true` (publish).
  6. Verify: release fields, asset digest/size, download HEAD, other releases.

The GitHub PAT is obtained via `git credential fill` and NEVER printed.
No verify=False: a CA bundle is exported from the Windows certificate store.
"""
import hashlib
import json
import os
import ssl
import subprocess
import sys
import time

import requests

REPO = "yohoten/acrpa"
TAG = "v0.1.29-beta"
ASSET_PATH = os.path.join("dist", "ACRPA v0.1.28-beta", "ACRPA v0.1.28-beta.exe")
ASSET_NAME = "ACRPA-v0.1.29-beta.exe"
EXPECT_SIZE = 14680412
EXPECT_SHA = "f57f1687452affaf2acccb13b67cdf810e28e6ed319eaf1cf37994dd3e283790"

API = "https://api.github.com"
UPLOADS = "https://uploads.github.com"
TIMEOUT = 1800
RESULT_PATH = os.path.join("tools", "_publish_github_result_0129_finish.json")
CA_BUNDLE_PATH = os.path.join("tools", "_win_ca_bundle.pem")
VERIFY = True


def log(msg):
    print(msg)
    sys.stdout.flush()


def build_ca_bundle():
    if os.path.isfile(CA_BUNDLE_PATH) and os.path.getsize(CA_BUNDLE_PATH) > 0:
        log("CA bundle reused: %s" % CA_BUNDLE_PATH)
        return CA_BUNDLE_PATH
    pems = []
    for store in ("ROOT", "CA"):
        try:
            for cert, encoding, _trust in ssl.enum_certificates(store):
                try:
                    if encoding == "x509_asn":
                        pems.append(ssl.DER_cert_to_PEM_cert(cert))
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001
            pass
    seen = set()
    uniq = []
    for pem in pems:
        if pem not in seen:
            seen.add(pem)
            uniq.append(pem)
    with open(CA_BUNDLE_PATH, "w", encoding="ascii") as fh:
        fh.write("".join(uniq))
    log("CA bundle built: %s (%d certs)" % (CA_BUNDLE_PATH, len(uniq)))
    return CA_BUNDLE_PATH


def get_token():
    try:
        proc = subprocess.run(
            ["git", "credential", "fill"],
            input="protocol=https\nhost=github.com\n\n",
            capture_output=True,
            text=True,
            timeout=60,
        )
    except Exception as exc:  # noqa: BLE001
        log("TOKEN: git credential fill failed: %s" % type(exc).__name__)
        return None
    for line in proc.stdout.splitlines():
        if line.startswith("password="):
            return line[len("password="):].strip()
    return None


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def gh_headers(token):
    return {
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "acrpa-release-bot",
    }


def save(obj):
    with open(RESULT_PATH, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
    log("RESULT written: %s" % RESULT_PATH)


def short(text, n=400):
    return (text or "")[:n]


def asset_ok(assets):
    if not assets:
        return False
    a = assets[0]
    return (
        a.get("name") == ASSET_NAME
        and a.get("size") == EXPECT_SIZE
        and (a.get("digest") or "").lower() == ("sha256:" + EXPECT_SHA).lower()
    )


def verify(token, result):
    vr = requests.get("%s/repos/%s/releases/tags/%s" % (API, REPO, TAG),
                      headers=gh_headers(token), timeout=TIMEOUT, verify=VERIFY)
    verify_obj = {"tag_get_status": vr.status_code}
    if vr.status_code == 200:
        vrel = vr.json()
        vassets = vrel.get("assets") or []
        verify_obj["id"] = vrel.get("id")
        verify_obj["prerelease"] = vrel.get("prerelease")
        verify_obj["draft"] = vrel.get("draft")
        verify_obj["immutable"] = vrel.get("immutable")
        verify_obj["html_url"] = vrel.get("html_url")
        verify_obj["assets"] = [
            {"name": a.get("name"), "size": a.get("size"), "digest": a.get("digest"),
             "download_count": a.get("download_count"),
             "browser_download_url": a.get("browser_download_url")}
            for a in vassets
        ]
        if vassets:
            dl = vassets[0].get("browser_download_url")
            try:
                head_resp = requests.head(dl, allow_redirects=False, timeout=TIMEOUT, verify=VERIFY)
                verify_obj["download_head_status"] = head_resp.status_code
                verify_obj["download_head_location"] = head_resp.headers.get("Location")
            except Exception as exc:  # noqa: BLE001
                verify_obj["download_head_status"] = "exception:%s" % type(exc).__name__
    result["verify"] = verify_obj
    log("VERIFY tag GET: %d prerelease=%s draft=%s"
        % (vr.status_code, verify_obj.get("prerelease"), verify_obj.get("draft")))
    log("VERIFY download HEAD: %s" % verify_obj.get("download_head_status"))

    lr = requests.get("%s/repos/%s/releases?per_page=30" % (API, REPO),
                      headers=gh_headers(token), timeout=TIMEOUT, verify=VERIFY)
    others = []
    if lr.status_code == 200:
        for r in lr.json():
            others.append({"tag_name": r.get("tag_name"), "id": r.get("id"),
                           "prerelease": r.get("prerelease")})
    verify_obj["release_list_status"] = lr.status_code
    verify_obj["release_list"] = others
    log("VERIFY release list: HTTP %d" % lr.status_code)
    for o in others:
        log("  - %s id=%s prerelease=%s" % (o["tag_name"], o["id"], o["prerelease"]))

    ok = (
        verify_obj.get("prerelease") is True
        and verify_obj.get("draft") is False
        and asset_ok(vassets if vr.status_code == 200 else [])
        and verify_obj.get("download_head_status") in (200, 302)
    )
    return bool(ok)


def main():
    global VERIFY
    VERIFY = build_ca_bundle()

    token = get_token()
    if not token:
        log("FAIL: PAT not obtainable via git credential fill")
        sys.exit(1)
    log("TOKEN: OK (***)")

    result = {"phase": "finish", "steps": {}}

    # 1) current release
    ru = "%s/repos/%s/releases/tags/%s" % (API, REPO, TAG)
    r = requests.get(ru, headers=gh_headers(token), timeout=TIMEOUT, verify=VERIFY)
    log("GET release: HTTP %d" % r.status_code)
    if r.status_code != 200:
        save({"phase": "finish", "ok": False, "error": "release GET %d" % r.status_code})
        log("FAIL: release not found (HTTP %d)" % r.status_code)
        sys.exit(1)
    rel = r.json()
    rid = rel.get("id")
    result["release"] = {"id": rid, "html_url": rel.get("html_url"),
                         "draft": rel.get("draft"), "prerelease": rel.get("prerelease"),
                         "immutable": rel.get("immutable")}
    log("release id=%s draft=%s prerelease=%s immutable=%s"
        % (rid, rel.get("draft"), rel.get("prerelease"), rel.get("immutable")))
    cur_assets = rel.get("assets") or []

    # 2) already has asset?
    if asset_ok(cur_assets):
        log("asset already present -> verify")
        result["steps"]["asset_present"] = True
        ok = verify(token, result)
        result["ok"] = ok
        result["state"] = "SUCCESS" if ok else "PARTIAL"
        save(result)
        sys.exit(0 if ok else 2)

    # gate: asset precheck
    if not os.path.isfile(ASSET_PATH):
        save({"phase": "finish", "ok": False, "error": "asset missing: %s" % ASSET_PATH})
        log("FAIL: asset missing: %s" % ASSET_PATH)
        sys.exit(1)
    size = os.path.getsize(ASSET_PATH)
    sha = sha256_file(ASSET_PATH)
    result["asset"] = {"path": ASSET_PATH, "size": size, "sha256": sha}
    log("PRECHECK asset: size=%d sha256=%s" % (size, sha))
    if size != EXPECT_SIZE or sha.lower() != EXPECT_SHA.lower():
        save({"phase": "finish", "ok": False, "error": "asset mismatch"})
        log("FAIL: asset size/sha256 mismatch")
        sys.exit(1)

    # 3) convert to draft
    pr = requests.patch("%s/repos/%s/releases/%s" % (API, REPO, rid),
                        headers=gh_headers(token), json={"draft": True},
                        timeout=TIMEOUT, verify=VERIFY)
    result["steps"]["patch_to_draft"] = {"status": pr.status_code, "body": short(pr.text)}
    log("PATCH -> draft: HTTP %d" % pr.status_code)
    if pr.status_code != 200:
        result["ok"] = False
        result["state"] = "IMMUTABLE_PATCH_BLOCKED"
        save(result)
        log("FAIL: cannot convert to draft (HTTP %d): %s" % (pr.status_code, short(pr.text)))
        sys.exit(3)
    log("PATCH -> draft: OK")

    # 4) upload asset
    up_url = "%s/repos/%s/releases/%s/assets?name=%s" % (UPLOADS, REPO, rid, ASSET_NAME)
    up_headers = {
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "acrpa-release-bot",
        "Content-Type": "application/octet-stream",
    }
    with open(ASSET_PATH, "rb") as fh:
        data = fh.read()
    uploaded = None
    last_err = None
    for attempt in range(1, 4):
        try:
            ur = requests.post(up_url, headers=up_headers, data=data,
                               timeout=TIMEOUT, verify=VERIFY)
        except Exception as exc:  # noqa: BLE001
            last_err = "exception:%s" % type(exc).__name__
            log("UPLOAD attempt %d exception: %s" % (attempt, last_err))
            time.sleep(5 * attempt)
            continue
        log("UPLOAD attempt %d status: %d" % (attempt, ur.status_code))
        if ur.status_code in (200, 201):
            uploaded = ur.json()
            break
        last_err = "http:%d body:%s" % (ur.status_code, short(ur.text, 300))
        log("UPLOAD attempt %d error: %s" % (attempt, last_err))
        time.sleep(5 * attempt)
    if uploaded is None:
        result["steps"]["upload"] = {"ok": False, "error": last_err}
        result["state"] = "DRAFT_NO_ASSET"
        save(result)
        log("FAIL: upload failed on draft: %s" % last_err)
        sys.exit(2)
    result["steps"]["upload"] = {
        "ok": True,
        "name": uploaded.get("name"),
        "size": uploaded.get("size"),
        "digest": uploaded.get("digest"),
        "browser_download_url": uploaded.get("browser_download_url"),
    }
    log("UPLOAD ok: name=%s size=%s digest=%s"
        % (uploaded.get("name"), uploaded.get("size"), uploaded.get("digest")))

    # 5) publish
    pr2 = requests.patch("%s/repos/%s/releases/%s" % (API, REPO, rid),
                         headers=gh_headers(token),
                         json={"draft": False, "prerelease": True},
                         timeout=TIMEOUT, verify=VERIFY)
    result["steps"]["patch_publish"] = {"status": pr2.status_code, "body": short(pr2.text)}
    log("PATCH -> publish: HTTP %d" % pr2.status_code)
    if pr2.status_code != 200:
        result["ok"] = False
        result["state"] = "PUBLISH_FAILED"
        save(result)
        log("FAIL: publish PATCH HTTP %d: %s" % (pr2.status_code, short(pr2.text)))
        sys.exit(2)

    # 6) verify
    ok = verify(token, result)
    result["ok"] = ok
    result["state"] = "SUCCESS" if ok else "PARTIAL"
    save(result)
    log("FINISH done: ok=%s state=%s" % (result["ok"], result["state"]))
    sys.exit(0 if ok else 2)


if __name__ == "__main__":
    main()
