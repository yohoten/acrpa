# -*- coding: utf-8 -*-
"""Check legacy tag protection + raw rulesets (possible removable cause)."""
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


def build_ca_bundle():
    if os.path.isfile(CA_BUNDLE_PATH) and os.path.getsize(CA_BUNDLE_PATH) > 0:
        return CA_BUNDLE_PATH
    pems = []
    for store in ("ROOT", "CA"):
        try:
            for cert, encoding, _t in ssl.enum_certificates(store):
                try:
                    if encoding == "x509_asn":
                        pems.append(ssl.DER_cert_to_PEM_Cert(cert) if False else ssl.DER_cert_to_PEM_cert(cert))
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

    for path in ("tags/protection", "rulesets", "rules/branches/main"):
        r = requests.get("%s/repos/%s/%s" % (API, REPO, path), headers=h, timeout=TIMEOUT, verify=verify)
        print("GET %s -> %d" % (path, r.status_code))
        try:
            print("   ", json.dumps(r.json(), ensure_ascii=False)[:1200])
        except Exception:
            print("   ", r.text[:400])


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
