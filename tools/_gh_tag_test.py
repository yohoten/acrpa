# -*- coding: utf-8 -*-
"""Definitive tag-name reservation test for v0.1.28-beta + full error capture."""
import json
import os
import ssl
import subprocess
import sys

import requests

REPO = "yohoten/acrpa"
API = "https://api.github.com"
TIMEOUT = 300
CA_BUNDLE_PATH = os.path.join("tools", "_win_ca_bundle.pem")
ORIG_SHA = "4e1c619c66ea3e4fa10161a501e8df6ac41de5df"


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


def main():
    verify = build_ca_bundle()
    h = {"Authorization": "Bearer " + get_token(), "Accept": "application/vnd.github+json",
         "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "acrpa-release-bot"}

    # main HEAD sha
    b = requests.get("%s/repos/%s/branches/main" % (API, REPO), headers=h, timeout=TIMEOUT, verify=verify)
    head_sha = b.json().get("commit", {}).get("sha") if b.status_code == 200 else None
    print("main HEAD:", head_sha)

    # A) create tag v0.1.28-beta at original sha
    a = requests.post("%s/repos/%s/git/refs" % (API, REPO), headers=h,
                      json={"ref": "refs/tags/v0.1.28-beta", "sha": ORIG_SHA},
                      timeout=TIMEOUT, verify=verify)
    print("A create v0.1.28-beta @orig:", a.status_code)
    print("   body:", a.text[:600])

    # B) create tag v0.1.28-beta at main HEAD
    bb = requests.post("%s/repos/%s/git/refs" % (API, REPO), headers=h,
                       json={"ref": "refs/tags/v0.1.28-beta", "sha": head_sha},
                       timeout=TIMEOUT, verify=verify)
    print("B create v0.1.28-beta @main:", bb.status_code)
    print("   body:", bb.text[:600])

    # C) name-specificity: similar-name tag should be allowed
    c = requests.post("%s/repos/%s/git/refs" % (API, REPO), headers=h,
                      json={"ref": "refs/tags/v0.1.28-beta.2", "sha": head_sha},
                      timeout=TIMEOUT, verify=verify)
    print("C create v0.1.28-beta.2 @main:", c.status_code)
    print("   body:", c.text[:400])
    if c.status_code == 201:
        requests.delete("%s/repos/%s/git/refs/tags/v0.1.28-beta.2" % (API, REPO),
                        headers=h, timeout=TIMEOUT, verify=verify)
        print("   cleanup v0.1.28-beta.2: ok")

    # D) release create with alternate tag (dry probe) - create then delete
    #    (not executed to avoid side effects unless needed)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
