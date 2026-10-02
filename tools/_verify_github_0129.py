# -*- coding: utf-8 -*-
"""Read-only verification for the ACRPA v0.1.29-beta GitHub release.

Collects: repo immutability-related fields, the v0.1.29-beta release state,
asset list (expected empty), HEAD of the (missing) download URL, and the full
release list (to confirm v0.1.27.0 / v0.1.25.0 are untouched).

The GitHub PAT is obtained via `git credential fill` and NEVER printed.
No verify=False: CA bundle exported from the Windows certificate store.
"""
import json
import os
import ssl
import subprocess
import sys

import requests

REPO = "yohoten/acrpa"
TAG = "v0.1.29-beta"
DL_URL = ("https://github.com/yohoten/acrpa/releases/download/"
          "v0.1.29-beta/ACRPA-v0.1.29-beta.exe")

API = "https://api.github.com"
TIMEOUT = 600
RESULT_PATH = os.path.join("tools", "_verify_github_result_0129.json")
CA_BUNDLE_PATH = os.path.join("tools", "_win_ca_bundle.pem")


def log(msg):
    print(msg)
    sys.stdout.flush()


def build_ca_bundle():
    if os.path.isfile(CA_BUNDLE_PATH) and os.path.getsize(CA_BUNDLE_PATH) > 0:
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
    return CA_BUNDLE_PATH


def get_token():
    try:
        proc = subprocess.run(
            ["git", "credential", "fill"],
            input="protocol=https\nhost=github.com\n\n",
            capture_output=True, text=True, timeout=60,
        )
    except Exception as exc:  # noqa: BLE001
        log("TOKEN: git credential fill failed: %s" % type(exc).__name__)
        return None
    for line in proc.stdout.splitlines():
        if line.startswith("password="):
            return line[len("password="):].strip()
    return None


def gh_headers(token):
    return {
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "acrpa-release-bot",
    }


def main():
    verify = build_ca_bundle()
    token = get_token()
    if not token:
        log("FAIL: PAT not obtainable via git credential fill")
        sys.exit(1)
    log("TOKEN: OK (***)")

    result = {}

    # repo-level impo fields
    rr = requests.get("%s/repos/%s" % (API, REPO), headers=gh_headers(token),
                      timeout=TIMEOUT, verify=verify)
    log("GET repo: HTTP %d" % rr.status_code)
    repo_objs = {}
    if rr.status_code == 200:
        repo = rr.json()
        for k, v in repo.items():
            lk = k.lower()
            if "immu" in lk or "release" in lk or k in ("private", "visibility"):
                repo_objs[k] = v
        log("repo immutability/related fields: %s" % json.dumps(repo_objs, ensure_ascii=False))
    result["repo"] = {"status": rr.status_code, "fields": repo_objs}

    # release by tag
    vr = requests.get("%s/repos/%s/releases/tags/%s" % (API, REPO, TAG),
                      headers=gh_headers(token), timeout=TIMEOUT, verify=verify)
    log("GET release by tag: HTTP %d" % vr.status_code)
    rel = {}
    if vr.status_code == 200:
        v = vr.json()
        assets = v.get("assets") or []
        rel = {
            "id": v.get("id"),
            "name": v.get("name"),
            "tag_name": v.get("tag_name"),
            "draft": v.get("draft"),
            "prerelease": v.get("prerelease"),
            "immutable": v.get("immutable"),
            "html_url": v.get("html_url"),
            "target_commitish": v.get("target_commitish"),
            "asset_count": len(assets),
            "assets": [{"name": a.get("name"), "size": a.get("size"),
                        "digest": a.get("digest"),
                        "browser_download_url": a.get("browser_download_url")}
                       for a in assets],
        }
        log("release: id=%s draft=%s prerelease=%s immutable=%s assets=%d"
            % (rel["id"], rel["draft"], rel["prerelease"], rel["immutable"], rel["asset_count"]))
    result["release"] = {"status": vr.status_code, "data": rel}

    # HEAD the expected download URL (no asset -> expect 404)
    try:
        h = requests.head(DL_URL, allow_redirects=False, timeout=TIMEOUT, verify=verify)
        result["download_head"] = {"status": h.status_code, "location": h.headers.get("Location")}
    except Exception as exc:  # noqa: BLE001
        result["download_head"] = {"status": "exception:%s" % type(exc).__name__}
    log("HEAD download URL: %s" % result["download_head"])

    # full release list
    lr = requests.get("%s/repos/%s/releases?per_page=30" % (API, REPO),
                      headers=gh_headers(token), timeout=TIMEOUT, verify=verify)
    listing = []
    if lr.status_code == 200:
        for r in lr.json():
            listing.append({"tag_name": r.get("tag_name"), "id": r.get("id"),
                            "name": r.get("name"), "draft": r.get("draft"),
                            "prerelease": r.get("prerelease"),
                            "immutable": r.get("immutable")})
    result["release_list"] = {"status": lr.status_code, "items": listing}
    log("GET releases list: HTTP %d" % lr.status_code)
    for it in listing:
        log("  - %s id=%s draft=%s prerelease=%s immutable=%s"
            % (it["tag_name"], it["id"], it["draft"], it["prerelease"], it["immutable"]))

    with open(RESULT_PATH, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)
    log("RESULT written: %s" % RESULT_PATH)


if __name__ == "__main__":
    main()
