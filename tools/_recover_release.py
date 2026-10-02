# -*- coding: utf-8 -*-
"""Recovery attempt: an immutable GitHub release blocks reusing its tag_name.

Sequence:
  1. record tag ref sha
  2. DELETE tag ref  (to allow re-creating the release with the same tag)
  3. POST release (tag_name=v0.1.28-beta, prerelease=true, name/body from docs)
  4. if created -> upload asset with retries -> verify
  5. if tag delete succeeded but create still fails -> restore the tag ref

Token comes from `git credential fill`; never printed.
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
TAG = "v0.1.28-beta"
ASSET_PATH = os.path.join("dist", "ACRPA v0.1.28-beta", "ACRPA v0.1.28-beta.exe")
ASSET_NAME = "ACRPA-v0.1.28-beta.exe"
BODY_PATH = os.path.join("docs", "releases", "v0.1.28-beta.md")
EXPECT_SIZE = 14680412
EXPECT_SHA = "f57f1687452affaf2acccb13b67cdf810e28e6ed319eaf1cf37994dd3e283790"
DEFAULT_NAME = "ACRPA v0.1.28-beta"
API = "https://api.github.com"
UPLOADS = "https://uploads.github.com"
TIMEOUT = 1800
RESULT_PATH = os.path.join("tools", "_recover_github_result.json")
CA_BUNDLE_PATH = os.path.join("tools", "_win_ca_bundle.pem")


def log(m):
    print(m)
    sys.stdout.flush()


def build_ca_bundle():
    if os.path.isfile(CA_BUNDLE_PATH) and os.path.getsize(CA_BUNDLE_PATH) > 0:
        return CA_BUNDLE_PATH
    pems = []
    for store in ("ROOT", "CA"):
        try:
            for cert, encoding, _t in ssl.enum_certificates(store):
                try:
                    if encoding == "x509_asn":
                        pems.append(ssl.DER_cert_to_PEM_cert(cert))
                except Exception:
                    pass
        except Exception:
            pass
    seen, uniq = set(), []
    for p in pems:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    with open(CA_BUNDLE_PATH, "w", encoding="ascii") as fh:
        fh.write("".join(uniq))
    return CA_BUNDLE_PATH


def get_token():
    proc = subprocess.run(["git", "credential", "fill"],
                          input="protocol=https\nhost=github.com\n\n",
                          capture_output=True, text=True, timeout=60)
    for line in proc.stdout.splitlines():
        if line.startswith("password="):
            return line[len("password="):].strip()
    return None


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for c in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(c)
    return h.hexdigest()


def main():
    verify = build_ca_bundle()
    token = get_token()
    h = {"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
         "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "acrpa-release-bot"}
    res = {"steps": {}}

    if not token:
        log("FAIL: no token")
        sys.exit(1)

    # asset sanity
    if os.path.getsize(ASSET_PATH) != EXPECT_SIZE or sha256_file(ASSET_PATH).lower() != EXPECT_SHA:
        log("FAIL: asset mismatch")
        sys.exit(1)
    with open(BODY_PATH, "r", encoding="utf-8") as fh:
        body = fh.read()

    # 1. tag ref
    rt = requests.get("%s/repos/%s/git/ref/tags/%s" % (API, REPO, TAG), headers=h,
                      timeout=TIMEOUT, verify=verify)
    res["steps"]["get_tag"] = {"status": rt.status_code}
    tag_sha = None
    if rt.status_code == 200:
        tag_sha = rt.json().get("object", {}).get("sha")
    res["steps"]["get_tag"]["sha"] = tag_sha
    log("GET tag ref: %d sha=%s" % (rt.status_code, tag_sha))

    # 2. delete tag ref
    deleted_tag = False
    if rt.status_code == 200:
        dt = requests.delete("%s/repos/%s/git/refs/tags/%s" % (API, REPO, TAG), headers=h,
                             timeout=TIMEOUT, verify=verify)
        res["steps"]["delete_tag"] = {"status": dt.status_code, "body": dt.text[:300]}
        log("DELETE tag ref: %d %s" % (dt.status_code, dt.text[:200]))
        deleted_tag = dt.status_code == 204
    else:
        res["steps"]["delete_tag"] = {"status": "skipped"}

    # 3. create release
    payload = {"tag_name": TAG, "target_commitish": "main",
               "name": DEFAULT_NAME, "body": body, "draft": False, "prerelease": True}
    created = None
    last_err = None
    for attempt in range(1, 3):
        cr = requests.post("%s/repos/%s/releases" % (API, REPO), headers=h, json=payload,
                           timeout=TIMEOUT, verify=verify)
        log("CREATE attempt %d: %d" % (attempt, cr.status_code))
        if cr.status_code in (200, 201):
            created = cr.json()
            break
        last_err = cr.text[:400]
        time.sleep(2)

    if created is None:
        res["steps"]["create"] = {"ok": False, "error": last_err}
        log("CREATE failed: %s" % last_err)
        # restore tag if we deleted it
        if deleted_tag and tag_sha:
            rr = requests.post("%s/repos/%s/git/refs" % (API, REPO), headers=h,
                               json={"ref": "refs/tags/" + TAG, "sha": tag_sha},
                               timeout=TIMEOUT, verify=verify)
            res["steps"]["restore_tag"] = {"status": rr.status_code, "body": rr.text[:300]}
            log("RESTORE tag ref: %d" % rr.status_code)
        res["state"] = "DELETED_NOT_RECREATED"
        with open(RESULT_PATH, "w", encoding="utf-8") as fh:
            json.dump(res, fh, ensure_ascii=False, indent=2)
        sys.exit(2)

    new_id = created.get("id")
    res["steps"]["create"] = {"ok": True, "id": new_id, "html_url": created.get("html_url"),
                              "prerelease": created.get("prerelease"), "name": created.get("name")}
    log("CREATE ok: id=%s html_url=%s prerelease=%s" % (new_id, created.get("html_url"),
                                                        created.get("prerelease")))

    # 4. upload asset
    up_url = "%s/repos/%s/releases/%s/assets?name=%s" % (UPLOADS, REPO, new_id, ASSET_NAME)
    uh = dict(h)
    uh["Content-Type"] = "application/octet-stream"
    with open(ASSET_PATH, "rb") as fh:
        data = fh.read()
    uploaded = None
    last_err = None
    for attempt in range(1, 4):
        try:
            ur = requests.post(up_url, headers=uh, data=data, timeout=TIMEOUT, verify=verify)
        except Exception as e:  # noqa: BLE001
            last_err = "exception:%s" % type(e).__name__
            log("UPLOAD attempt %d exception %s" % (attempt, type(e).__name__))
            time.sleep(5 * attempt)
            continue
        log("UPLOAD attempt %d: %d" % (attempt, ur.status_code))
        if ur.status_code in (200, 201):
            uploaded = ur.json()
            break
        last_err = ur.text[:300]
        time.sleep(5 * attempt)
    if uploaded is None:
        res["steps"]["upload"] = {"ok": False, "error": last_err}
        res["state"] = "CREATED_NO_ASSET"
        with open(RESULT_PATH, "w", encoding="utf-8") as fh:
            json.dump(res, fh, ensure_ascii=False, indent=2)
        log("UPLOAD failed: %s" % last_err)
        sys.exit(3)

    res["steps"]["upload"] = {"ok": True, "name": uploaded.get("name"), "size": uploaded.get("size"),
                              "digest": uploaded.get("digest"),
                              "browser_download_url": uploaded.get("browser_download_url")}
    log("UPLOAD ok: %s size=%s digest=%s" % (uploaded.get("name"), uploaded.get("size"),
                                             uploaded.get("digest")))

    # 5. verify
    vr = requests.get("%s/repos/%s/releases/tags/%s" % (API, REPO, TAG), headers=h,
                      timeout=TIMEOUT, verify=verify)
    ver = {"tag_get_status": vr.status_code}
    if vr.status_code == 200:
        d = vr.json()
        assets = d.get("assets") or []
        ver.update({"id": d.get("id"), "prerelease": d.get("prerelease"),
                    "immutable": d.get("immutable"), "html_url": d.get("html_url"),
                    "assets": [{"name": a.get("name"), "size": a.get("size"),
                                "digest": a.get("digest"),
                                "browser_download_url": a.get("browser_download_url")}
                               for a in assets]})
        if assets:
            hd = requests.head(assets[0].get("browser_download_url"), allow_redirects=False,
                               timeout=TIMEOUT, verify=verify)
            ver["download_head_status"] = hd.status_code
    lr = requests.get("%s/repos/%s/releases?per_page=30" % (API, REPO), headers=h,
                      timeout=TIMEOUT, verify=verify)
    ver["release_list"] = [{"tag_name": r.get("tag_name"), "id": r.get("id"),
                            "prerelease": r.get("prerelease")} for r in (lr.json() if lr.status_code == 200 else [])]
    res["steps"]["verify"] = ver
    log("VERIFY: prerelease=%s assets=%s head=%s" % (ver.get("prerelease"), ver.get("assets"),
                                                     ver.get("download_head_status")))

    ok = (ver.get("prerelease") is True and ver.get("assets")
          and ver["assets"][0].get("name") == ASSET_NAME
          and ver["assets"][0].get("size") == EXPECT_SIZE
          and (ver["assets"][0].get("digest") or "").lower() == ("sha256:" + EXPECT_SHA).lower()
          and ver.get("download_head_status") in (200, 302))
    res["ok"] = bool(ok)
    res["state"] = "SUCCESS" if ok else "PARTIAL"
    with open(RESULT_PATH, "w", encoding="utf-8") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=2)
    log("RUN: ok=%s state=%s" % (res["ok"], res["state"]))
    if not ok:
        sys.exit(4)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
