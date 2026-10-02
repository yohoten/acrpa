# -*- coding: utf-8 -*-
"""Probe repo rulesets + token identity + test alternate tag creation (read-mostly)."""
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
    token = get_token()
    h = {"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
         "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "acrpa-release-bot"}

    u = requests.get("%s/user" % API, headers=h, timeout=TIMEOUT, verify=verify)
    print("GET /user:", u.status_code)
    if u.status_code == 200:
        d = u.json()
        print("  login=", d.get("login"))

    r = requests.get("%s/repos/%s/rulesets" % (API, REPO), headers=h, timeout=TIMEOUT, verify=verify)
    print("GET rulesets:", r.status_code)
    if r.status_code == 200:
        for rs in r.json():
            print("  ruleset id=%s name=%r target=%s enforcement=%s"
                  % (rs.get("id"), rs.get("name"), rs.get("target"), rs.get("enforcement")))
            rd = requests.get("%s/repos/%s/rulesets/%s" % (API, REPO, rs.get("id")),
                              headers=h, timeout=TIMEOUT, verify=verify)
            if rd.status_code == 200:
                det = rd.json()
                print("    conditions=", json.dumps(det.get("conditions"), ensure_ascii=False))
                print("    rules=", json.dumps([x.get("type") for x in (det.get("rules") or [])],
                                               ensure_ascii=False))
                print("    bypass_actors=", json.dumps(det.get("bypass_actors"), ensure_ascii=False))
    else:
        print("  body:", r.text[:300])

    # check repo meta
    rm = requests.get("%s/repos/%s" % (API, REPO), headers=h, timeout=TIMEOUT, verify=verify)
    if rm.status_code == 200:
        d = rm.json()
        print("repo: default_branch=", d.get("default_branch"),
              "permissions=", d.get("permissions"))

    # does the tag exist now?
    rt = requests.get("%s/repos/%s/git/ref/tags/v0.1.28-beta" % (API, REPO), headers=h,
                      timeout=TIMEOUT, verify=verify)
    print("GET tag v0.1.28-beta:", rt.status_code)

    # test: can we create a DIFFERENT tag ref?
    test_ref = "refs/tags/_probe_tmp_delete_me"
    ct = requests.post("%s/repos/%s/git/refs" % (API, REPO), headers=h,
                       json={"ref": test_ref, "sha": "4e1c619c66ea3e4fa10161a501e8df6ac41de5df"},
                       timeout=TIMEOUT, verify=verify)
    print("POST probe tag:", ct.status_code, ct.text[:250])
    if ct.status_code == 201:
        dl = requests.delete("%s/repos/%s/git/refs/tags/_probe_tmp_delete_me" % (API, REPO),
                             headers=h, timeout=TIMEOUT, verify=verify)
        print("  cleanup probe tag:", dl.status_code)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
