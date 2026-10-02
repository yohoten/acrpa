# -*- coding: utf-8 -*-
"""GitHub Release recreate helper (round 2) for ACRPA v0.1.28-beta.

Context: the previous round deleted both the old Release and its tag; recreation
was blocked by "immutable release" tag retention. The user reports immutability
is now disabled, so this round retries.

Flow (single `all` phase, with an internal gate that aborts before creating
the Release when the tag ref cannot be created):

  1. status : GET release/tag (expect 404) + GET commits/main (HEAD sha)
  2. gate   : POST /git/refs {refs/tags/v0.1.28-beta, sha=HEAD}
              - 201/200 -> continue
              - 422     -> STOP (do not create release)
  3. create : POST /releases (prerelease=true, body=docs full text)
  4. upload : POST uploads.../assets?name=ACRPA-v0.1.28-beta.exe (retry<=3)
  5. verify : GET release + HEAD browser_download_url + list releases

The GitHub PAT is obtained via `git credential fill` and NEVER printed.
No verify=False: a CA bundle is exported from the Windows cert store.
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
RELEASE_NAME = "ACRPA v0.1.28-beta (Pre-release)"

API = "https://api.github.com"
UPLOADS = "https://uploads.github.com"
TIMEOUT = 1800
RESULT_PATH = os.path.join("tools", "_recreate_github_result2.json")
CA_BUNDLE_PATH = os.path.join("tools", "_win_ca_bundle.pem")
VERIFY = True


def log(msg):
    print(msg)
    sys.stdout.flush()


def build_ca_bundle():
    """Build a CA bundle from the Windows certificate store (no verify=False)."""
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


def cmd_status(token):
    result = {"phase": "status", "steps": {}}
    # release
    ru = "%s/repos/%s/releases/tags/%s" % (API, REPO, TAG)
    r = requests.get(ru, headers=gh_headers(token), timeout=TIMEOUT, verify=VERIFY)
    result["steps"]["release_get"] = {"status": r.status_code}
    log("STATUS GET release: HTTP %d" % r.status_code)
    # tag ref
    tu = "%s/repos/%s/git/ref/tags/%s" % (API, REPO, TAG)
    t = requests.get(tu, headers=gh_headers(token), timeout=TIMEOUT, verify=VERIFY)
    result["steps"]["tag_get"] = {"status": t.status_code}
    log("STATUS GET tag ref: HTTP %d" % t.status_code)
    # head
    cu = "%s/repos/%s/commits/main" % (API, REPO)
    c = requests.get(cu, headers=gh_headers(token), timeout=TIMEOUT, verify=VERIFY)
    head = None
    if c.status_code == 200:
        head = c.json().get("sha")
    result["steps"]["head"] = {"status": c.status_code, "sha": head}
    log("STATUS GET commits/main: HTTP %d sha=%s" % (c.status_code, head))
    result["ok"] = True
    save(result)


def cmd_gate(token):
    result = {"phase": "gate", "steps": {}}
    cu = "%s/repos/%s/commits/main" % (API, REPO)
    c = requests.get(cu, headers=gh_headers(token), timeout=TIMEOUT, verify=VERIFY)
    if c.status_code != 200:
        result["ok"] = False
        result["steps"]["head"] = {"status": c.status_code}
        save(result)
        log("GATE: cannot get HEAD sha (HTTP %d)" % c.status_code)
        sys.exit(1)
    head = c.json().get("sha")
    result["steps"]["head"] = {"status": 200, "sha": head}
    log("GATE HEAD sha: %s" % head)

    body = {"ref": "refs/tags/%s" % TAG, "sha": head}
    ru = "%s/repos/%s/git/refs" % (API, REPO)
    r = requests.post(ru, headers=gh_headers(token), json=body, timeout=TIMEOUT, verify=VERIFY)
    result["steps"]["create_ref"] = {
        "status": r.status_code,
        "body": short(r.text),
    }
    log("GATE POST /git/refs: HTTP %d" % r.status_code)
    log("GATE body: %s" % short(r.text))

    if r.status_code in (200, 201):
        result["ok"] = True
        result["gate"] = "PASS"
        if r.status_code == 201:
            result["steps"]["create_ref"]["ref_sha"] = (r.json() or {}).get("object", {}).get("sha")
        save(result)
        log("GATE: PASS (tag ref creatable)")
        sys.exit(0)
    if r.status_code == 422:
        result["ok"] = False
        result["gate"] = "FAIL_422_IMMUTABLE"
        save(result)
        log("GATE: FAIL 422 (tag name still restricted / immutable retention remains)")
        sys.exit(3)
    result["ok"] = False
    result["gate"] = "FAIL_HTTP_%d" % r.status_code
    save(result)
    log("GATE: FAIL unexpected HTTP %d" % r.status_code)
    sys.exit(1)


def cmd_run(token):
    result = {"phase": "run", "steps": {}}

    # Precheck: asset
    if not os.path.isfile(ASSET_PATH):
        save({"phase": "run", "ok": False, "error": "asset missing: %s" % ASSET_PATH})
        log("FAIL: asset missing: %s" % ASSET_PATH)
        sys.exit(1)
    size = os.path.getsize(ASSET_PATH)
    sha = sha256_file(ASSET_PATH)
    result["asset"] = {"path": ASSET_PATH, "size": size, "sha256": sha}
    log("PRECHECK asset: size=%d sha256=%s" % (size, sha))
    if size != EXPECT_SIZE or sha.lower() != EXPECT_SHA.lower():
        save({"phase": "run", "ok": False, "error": "asset mismatch"})
        log("FAIL: asset size/sha256 mismatch")
        sys.exit(1)

    # Precheck: body
    with open(BODY_PATH, "r", encoding="utf-8") as fh:
        body = fh.read()
    if not body.strip():
        save({"phase": "run", "ok": False, "error": "body empty"})
        log("FAIL: body empty")
        sys.exit(1)
    result["body_len"] = len(body)
    log("PRECHECK body: OK (%d chars)" % len(body))

    # Gate: ensure tag ref creatable / exists
    cu = "%s/repos/%s/commits/main" % (API, REPO)
    c = requests.get(cu, headers=gh_headers(token), timeout=TIMEOUT, verify=VERIFY)
    if c.status_code != 200:
        save({"phase": "run", "ok": False, "error": "head_fetch_%d" % c.status_code})
        log("FAIL: HEAD fetch HTTP %d" % c.status_code)
        sys.exit(1)
    head = c.json().get("sha")
    result["head_sha"] = head
    gu = "%s/repos/%s/git/refs" % (API, REPO)
    gr = requests.post(gu, headers=gh_headers(token),
                       json={"ref": "refs/tags/%s" % TAG, "sha": head},
                       timeout=TIMEOUT, verify=VERIFY)
    result["steps"]["gate_ref"] = {"status": gr.status_code, "body": short(gr.text)}
    log("GATE POST /git/refs: HTTP %d" % gr.status_code)
    log("GATE body: %s" % short(gr.text))
    if gr.status_code not in (200, 201):
        if gr.status_code == 422:
            result["ok"] = False
            result["gate"] = "FAIL_422_IMMUTABLE"
            save(result)
            log("GATE: FAIL 422 -> STOP (do not create release)")
            sys.exit(3)
        # create may already exist (422) is handled above; other errors -> stop
        result["ok"] = False
        result["gate"] = "FAIL_HTTP_%d" % gr.status_code
        save(result)
        log("GATE: FAIL HTTP %d -> STOP" % gr.status_code)
        sys.exit(1)
    result["gate"] = "PASS"
    log("GATE: PASS")

    # Create release
    create_url = "%s/repos/%s/releases" % (API, REPO)
    payload = {
        "tag_name": TAG,
        "target_commitish": "main",
        "name": RELEASE_NAME,
        "body": body,
        "draft": False,
        "prerelease": True,
    }
    created = None
    last_err = None
    for attempt in range(1, 4):
        try:
            cr = requests.post(create_url, headers=gh_headers(token), json=payload,
                               timeout=TIMEOUT, verify=VERIFY)
        except Exception as exc:  # noqa: BLE001
            last_err = "exception:%s" % type(exc).__name__
            log("CREATE attempt %d exception: %s" % (attempt, last_err))
            time.sleep(3 * attempt)
            continue
        log("CREATE attempt %d status: %d" % (attempt, cr.status_code))
        if cr.status_code in (200, 201):
            created = cr.json()
            break
        last_err = "http:%d body:%s" % (cr.status_code, short(cr.text, 300))
        log("CREATE attempt %d error: %s" % (attempt, last_err))
        if cr.status_code == 422:
            # immutable still in effect -> stop, do not retry blindly
            result["steps"]["create"] = {"ok": False, "error": last_err}
            result["state"] = "CREATE_422"
            save(result)
            log("CREATE 422 -> STOP")
            sys.exit(3)
        time.sleep(3 * attempt)
    if created is None:
        result["steps"]["create"] = {"ok": False, "error": last_err}
        result["state"] = "CREATE_FAILED"
        save(result)
        log("FAIL: create failed: %s" % last_err)
        sys.exit(1)

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

    # Upload asset
    up_url = "%s/repos/%s/releases/%s/assets?name=%s" % (UPLOADS, REPO, new_id, ASSET_NAME)
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
            ur = requests.post(up_url, headers=up_headers, data=data, timeout=TIMEOUT, verify=VERIFY)
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
        result["state"] = "CREATED_NO_ASSET"
        save(result)
        log("FAIL: upload failed: %s (release kept, NOT deleted)" % last_err)
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

    # Verify
    vr = requests.get("%s/repos/%s/releases/tags/%s" % (API, REPO, TAG),
                      headers=gh_headers(token), timeout=TIMEOUT, verify=VERIFY)
    verify = {"tag_get_status": vr.status_code}
    if vr.status_code == 200:
        vrel = vr.json()
        vassets = vrel.get("assets") or []
        verify["id"] = vrel.get("id")
        verify["prerelease"] = vrel.get("prerelease")
        verify["draft"] = vrel.get("draft")
        verify["immutable"] = vrel.get("immutable")
        verify["html_url"] = vrel.get("html_url")
        verify["assets"] = [
            {"name": a.get("name"), "size": a.get("size"), "digest": a.get("digest"),
             "browser_download_url": a.get("browser_download_url")}
            for a in vassets
        ]
        if vassets:
            dl = vassets[0].get("browser_download_url")
            try:
                head_resp = requests.head(dl, allow_redirects=False, timeout=TIMEOUT, verify=VERIFY)
                verify["download_head_status"] = head_resp.status_code
            except Exception as exc:  # noqa: BLE001
                verify["download_head_status"] = "exception:%s" % type(exc).__name__
    result["steps"]["verify"] = verify
    log("VERIFY tag GET: %d prerelease=%s" % (vr.status_code, verify.get("prerelease")))
    log("VERIFY download HEAD: %s" % verify.get("download_head_status"))

    lr = requests.get("%s/repos/%s/releases?per_page=30" % (API, REPO),
                      headers=gh_headers(token), timeout=TIMEOUT, verify=VERIFY)
    others = []
    if lr.status_code == 200:
        for r in lr.json():
            others.append({"tag_name": r.get("tag_name"), "id": r.get("id"),
                           "prerelease": r.get("prerelease")})
    verify["release_list_status"] = lr.status_code
    verify["release_list"] = others
    log("VERIFY release list: HTTP %d" % lr.status_code)
    for o in others:
        log("  - %s id=%s prerelease=%s" % (o["tag_name"], o["id"], o["prerelease"]))

    ok = (
        verify.get("prerelease") is True
        and bool(verify.get("assets"))
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
    ap.add_argument("--phase", choices=["status", "gate", "run"], required=True)
    args = ap.parse_args()

    global VERIFY
    VERIFY = build_ca_bundle()

    token = get_token()
    if not token:
        log("FAIL: PAT not obtainable via git credential fill")
        sys.exit(1)
    log("TOKEN: OK (***)")

    if args.phase == "status":
        cmd_status(token)
    elif args.phase == "gate":
        cmd_gate(token)
    else:
        cmd_run(token)


if __name__ == "__main__":
    main()
