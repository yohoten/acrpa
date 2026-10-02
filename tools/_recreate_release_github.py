# -*- coding: utf-8 -*-
"""GitHub Release recreate helper for ACRPA v0.1.28-beta (API mode).

Phases:
  precheck : read-only checks; exits non-zero on failure, never deletes anything.
  run      : delete old release -> create new (prerelease=true) -> upload asset -> verify.

The GitHub PAT is obtained through `git credential fill` and NEVER printed.
"""
import argparse
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
RESULT_PATH = os.path.join("tools", "_recreate_github_result.json")
CA_BUNDLE_PATH = os.path.join("tools", "_win_ca_bundle.pem")
VERIFY = True


def build_ca_bundle():
    """Build a CA bundle from the Windows certificate store.

    The corporate/AV TLS interceptor is trusted by the OS but not by certifi,
    so we trust the same roots the OS does (no verify=False).
    """
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
    log("CA bundle built: %s (%d certs)" % (CA_BUNDLE_PATH, len(uniq)))
    return CA_BUNDLE_PATH


def log(msg):
    print(msg)
    sys.stdout.flush()


def get_token():
    """Obtain PAT via git credential fill. Returns token or None. Never prints it."""
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
    token = None
    for line in proc.stdout.splitlines():
        if line.startswith("password="):
            token = line[len("password="):].strip()
            break
    return token


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


def die(msg):
    log("FAIL: " + msg)
    sys.exit(1)


def precheck(token):
    """Return dict of facts; call die() on gate failure."""
    facts = {}

    # Gate A: asset
    if not os.path.isfile(ASSET_PATH):
        die("asset file missing: %s" % ASSET_PATH)
    size = os.path.getsize(ASSET_PATH)
    sha = sha256_file(ASSET_PATH)
    facts["asset_path"] = ASSET_PATH
    facts["asset_size"] = size
    facts["asset_sha256"] = sha
    log("PRECHECK asset: size=%d sha256=%s" % (size, sha))
    if size != EXPECT_SIZE:
        die("asset size mismatch: %d != %d" % (size, EXPECT_SIZE))
    if sha.lower() != EXPECT_SHA.lower():
        die("asset sha256 mismatch")
    log("PRECHECK asset: OK")

    # Gate B: body file
    if not os.path.isfile(BODY_PATH):
        die("body file missing: %s" % BODY_PATH)
    with open(BODY_PATH, "r", encoding="utf-8") as fh:
        body = fh.read()
    if not body.strip():
        die("body file empty")
    facts["body_path"] = BODY_PATH
    facts["body_len"] = len(body)
    log("PRECHECK body: OK (%d chars)" % len(body))

    # Gate C: token
    if not token:
        die("PAT not obtainable via git credential fill")
    log("PRECHECK token: OK (*** )")

    # Gate D: API reachable + existing release
    url = "%s/repos/%s/releases/tags/%s" % (API, REPO, TAG)
    try:
        resp = requests.get(url, headers=gh_headers(token), timeout=TIMEOUT, verify=VERIFY)
    except Exception as exc:  # noqa: BLE001
        die("GET existing release failed: %s" % type(exc).__name__)
    log("PRECHECK GET existing release: HTTP %d" % resp.status_code)
    if resp.status_code != 200:
        die("GET existing release returned HTTP %d" % resp.status_code)
    rel = resp.json()
    facts["old_id"] = rel.get("id")
    facts["old_name"] = rel.get("name")
    facts["old_prerelease"] = rel.get("prerelease")
    facts["old_immutable"] = rel.get("immutable")
    facts["old_html_url"] = rel.get("html_url")
    assets = rel.get("assets") or []
    facts["old_assets"] = [
        {
            "name": a.get("name"),
            "size": a.get("size"),
            "digest": a.get("digest"),
            "download_count": a.get("download_count"),
            "browser_download_url": a.get("browser_download_url"),
        }
        for a in assets
    ]
    log("PRECHECK old release: id=%s name=%r prerelease=%s immutable=%s"
        % (facts["old_id"], facts["old_name"], facts["old_prerelease"], facts["old_immutable"]))
    for a in facts["old_assets"]:
        log("PRECHECK old asset: name=%s size=%s digest=%s" % (a["name"], a["size"], a["digest"]))
    return facts, body


def save(obj):
    with open(RESULT_PATH, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
    log("RESULT written: %s" % RESULT_PATH)


def cmd_precheck(token):
    facts, _ = precheck(token)
    save({"phase": "precheck", "ok": True, "facts": facts})
    log("PRECHECK: ALL GATES PASSED")


def cmd_run(token):
    result = {"phase": "run", "ok": False, "steps": {}}

    facts, body = precheck(token)
    result["facts"] = facts
    old_id = facts["old_id"]
    name = facts.get("old_name") or DEFAULT_NAME

    # Step 2: delete old release
    del_url = "%s/repos/%s/releases/%s" % (API, REPO, old_id)
    log("DELETE %s" % del_url)
    try:
        dr = requests.delete(del_url, headers=gh_headers(token), timeout=TIMEOUT, verify=VERIFY)
    except Exception as exc:  # noqa: BLE001
        result["steps"]["delete"] = {"ok": False, "error": type(exc).__name__}
        save(result)
        die("DELETE raised %s; release NOT recreated" % type(exc).__name__)
    result["steps"]["delete"] = {"status": dr.status_code}
    log("DELETE status: %d" % dr.status_code)
    if dr.status_code != 204:
        die("DELETE expected 204, got %d; aborting before create" % dr.status_code)

    # Step 3: create new release (retry 1-2 times)
    create_url = "%s/repos/%s/releases" % (API, REPO)
    payload = {
        "tag_name": TAG,
        "target_commitish": "main",
        "name": name,
        "body": body,
        "draft": False,
        "prerelease": True,
    }
    created = None
    last_err = None
    for attempt in range(1, 4):
        try:
            cr = requests.post(create_url, headers=gh_headers(token), json=payload, timeout=TIMEOUT, verify=VERIFY)
        except Exception as exc:  # noqa: BLE001
            last_err = "exception:%s" % type(exc).__name__
            log("CREATE attempt %d exception: %s" % (attempt, type(exc).__name__))
            time.sleep(3 * attempt)
            continue
        log("CREATE attempt %d status: %d" % (attempt, cr.status_code))
        if cr.status_code in (200, 201):
            created = cr.json()
            break
        last_err = "http:%d body:%s" % (cr.status_code, cr.text[:300])
        time.sleep(3 * attempt)
    if created is None:
        result["steps"]["create"] = {"ok": False, "error": last_err}
        result["state"] = "DELETED_NOT_RECREATED"
        save(result)
        die("CREATE failed after retries; RISK STATE: old deleted, not recreated. err=%s" % last_err)

    new_id = created.get("id")
    result["steps"]["create"] = {
        "ok": True,
        "id": new_id,
        "html_url": created.get("html_url"),
        "prerelease": created.get("prerelease"),
        "draft": created.get("draft"),
        "name": created.get("name"),
    }
    log("CREATE ok: id=%s html_url=%s prerelease=%s"
        % (new_id, created.get("html_url"), created.get("prerelease")))

    # Step 4: upload asset (retry up to 3 with backoff)
    up_url = "%s/repos/%s/releases/%s/assets?name=%s" % (UPLOADS, REPO, new_id, ASSET_NAME)
    up_headers = {
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "acrpa-release-bot",
        "Content-Type": "application/octet-stream",
    }
    uploaded = None
    last_err = None
    with open(ASSET_PATH, "rb") as fh:
        data = fh.read()
    for attempt in range(1, 4):
        try:
            ur = requests.post(up_url, headers=up_headers, data=data, timeout=TIMEOUT, verify=VERIFY)
        except Exception as exc:  # noqa: BLE001
            last_err = "exception:%s" % type(exc).__name__
            log("UPLOAD attempt %d exception: %s" % (attempt, type(exc).__name__))
            time.sleep(5 * attempt)
            continue
        log("UPLOAD attempt %d status: %d" % (attempt, ur.status_code))
        if ur.status_code in (200, 201):
            uploaded = ur.json()
            break
        last_err = "http:%d body:%s" % (ur.status_code, ur.text[:300])
        time.sleep(5 * attempt)
    if uploaded is None:
        result["steps"]["upload"] = {"ok": False, "error": last_err}
        result["state"] = "CREATED_NO_ASSET"
        save(result)
        die("UPLOAD failed after retries; release created without asset. err=%s" % last_err)

    result["steps"]["upload"] = {
        "ok": True,
        "name": uploaded.get("name"),
        "size": uploaded.get("size"),
        "digest": uploaded.get("digest"),
        "browser_download_url": uploaded.get("browser_download_url"),
    }
    log("UPLOAD ok: name=%s size=%s digest=%s"
        % (uploaded.get("name"), uploaded.get("size"), uploaded.get("digest")))

    # Step 5: verify
    vr = requests.get("%s/repos/%s/releases/tags/%s" % (API, REPO, TAG),
                      headers=gh_headers(token), timeout=TIMEOUT, verify=VERIFY)
    verify = {"tag_get_status": vr.status_code}
    if vr.status_code == 200:
        vrel = vr.json()
        vassets = vrel.get("assets") or []
        verify["id"] = vrel.get("id")
        verify["prerelease"] = vrel.get("prerelease")
        verify["html_url"] = vrel.get("html_url")
        verify["assets"] = [
            {"name": a.get("name"), "size": a.get("size"), "digest": a.get("digest"),
             "browser_download_url": a.get("browser_download_url")}
            for a in vassets
        ]
        if vassets:
            dl = vassets[0].get("browser_download_url")
            try:
                head = requests.head(dl, allow_redirects=False, timeout=TIMEOUT, verify=VERIFY)
                verify["download_head_status"] = head.status_code
            except Exception as exc:  # noqa: BLE001
                verify["download_head_status"] = "exception:%s" % type(exc).__name__
    result["steps"]["verify"] = verify
    log("VERIFY tag GET: %d; prerelease=%s" % (vr.status_code, verify.get("prerelease")))
    log("VERIFY download HEAD: %s" % verify.get("download_head_status"))

    # other releases unaffected
    lr = requests.get("%s/repos/%s/releases?per_page=30" % (API, REPO),
                      headers=gh_headers(token), timeout=TIMEOUT, verify=VERIFY)
    others = []
    if lr.status_code == 200:
        for r in lr.json():
            others.append({"tag_name": r.get("tag_name"), "id": r.get("id"),
                           "prerelease": r.get("prerelease")})
    verify["release_list_status"] = lr.status_code
    verify["release_list"] = others

    ok = (
        verify.get("prerelease") is True
        and verify.get("assets")
        and verify["assets"][0].get("name") == ASSET_NAME
        and verify["assets"][0].get("size") == EXPECT_SIZE
        and (verify["assets"][0].get("digest") or "").lower() == ("sha256:" + EXPECT_SHA).lower()
        and verify.get("download_head_status") in (200, 302)
    )
    result["ok"] = bool(ok)
    result["state"] = "SUCCESS" if ok else "PARTIAL"
    save(result)
    log("RUN done: ok=%s state=%s" % (result["ok"], result["state"]))
    if not ok:
        sys.exit(2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", choices=["precheck", "run"], required=True)
    args = ap.parse_args()

    global VERIFY
    VERIFY = build_ca_bundle()

    token = get_token()
    if args.phase == "precheck":
        cmd_precheck(token)
    else:
        cmd_run(token)


if __name__ == "__main__":
    main()
