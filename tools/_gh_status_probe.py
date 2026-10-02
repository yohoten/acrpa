# -*- coding: utf-8 -*-
"""Read-only status probe: current release state + tag state on GitHub."""
import json
import os
import ssl
import subprocess
import sys

import requests

REPO = "yohoten/acrpa"
TAG = "v0.1.28-beta"
API = "https://api.github.com"
TIMEOUT = 300
CA_BUNDLE_PATH = os.path.join("tools", "_win_ca_bundle.pem")


def build_ca_bundle():
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
    for pem in pems:
        if pem not in seen:
            seen.add(pem)
            uniq.append(pem)
    with open(CA_BUNDLE_PATH, "w", encoding="ascii") as fh:
        fh.write("".join(uniq))
    return CA_BUNDLE_PATH


def get_token():
    proc = subprocess.run(
        ["git", "credential", "fill"],
        input="protocol=https\nhost=github.com\n\n",
        capture_output=True, text=True, timeout=60,
    )
    for line in proc.stdout.splitlines():
        if line.startswith("password="):
            return line[len("password="):].strip()
    return None


def main():
    verify = build_ca_bundle()
    token = get_token()
    h = {
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "acrpa-release-bot",
    }

    r = requests.get("%s/repos/%s/releases/tags/%s" % (API, REPO, TAG),
                     headers=h, timeout=TIMEOUT, verify=verify)
    print("GET release by tag: HTTP", r.status_code)
    if r.status_code == 200:
        d = r.json()
        print("  id=", d.get("id"), "name=", repr(d.get("name")),
              "prerelease=", d.get("prerelease"), "immutable=", d.get("immutable"))
        print("  assets=", [(a.get("name"), a.get("size"), a.get("digest")) for a in (d.get("assets") or [])])

    r2 = requests.get("%s/repos/%s/releases?per_page=30" % (API, REPO),
                      headers=h, timeout=TIMEOUT, verify=verify)
    print("GET releases list: HTTP", r2.status_code)
    if r2.status_code == 200:
        for rel in r2.json():
            print("  -", rel.get("tag_name"), "id=", rel.get("id"),
                  "prerelease=", rel.get("prerelease"), "draft=", rel.get("draft"))

    rt = requests.get("%s/repos/%s/git/ref/tags/%s" % (API, REPO, TAG),
                      headers=h, timeout=TIMEOUT, verify=verify)
    print("GET tag ref: HTTP", rt.status_code)
    if rt.status_code == 200:
        print("  ref=", rt.json().get("ref"), "object=", rt.json().get("object", {}).get("sha"))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
