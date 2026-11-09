"""AtomGit Release 附件上传: upload_url -> OBS 预签名 PUT -> 回调建附件。

Token 走环境变量 ATOMGIT_TOKEN, 不落盘不打印。
"""
import json
import os
import sys

import requests

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API = "https://atomgit.com/api/v5/repos/yohoten/ACRPA"
TAG = "v0.1.30"
ASSETS = [
    "dist/ACRPA-v0.1.30.exe",
    "dist/ACRPA-v0.1.30-setup.exe",
    "dist/ACRPA-v0.1.30-portable.zip",
]


def main():
    tok = (os.environ.get("ATOMGIT_TOKEN") or "").strip()
    if not tok:
        print("no ATOMGIT_TOKEN")
        return 1

    r = requests.get(API + "/releases/tags/{}".format(TAG),
                     params={"access_token": tok}, timeout=30)
    rel = r.json() if r.status_code == 200 else {}
    have = {}
    for a in (rel.get("assets") or []):
        n = a.get("name") or ""
        if n.lower().endswith((".exe", ".zip")):
            have[n] = a.get("size")
    print("existing:", have)

    for rel_path in ASSETS:
        path = os.path.join(BASE, rel_path)
        name = os.path.basename(path)
        size = os.path.getsize(path)
        if have.get(name) == size:
            print("[SKIP]", name)
            continue

        u = requests.get(API + "/releases/{}/upload_url".format(TAG),
                         params={"access_token": tok, "file_name": name},
                         timeout=60)
        print("[url]", name, u.status_code, flush=True)
        if u.status_code != 200:
            print("   ", (u.text or "")[:300])
            return 1
        info = u.json()
        url, headers = info["url"], info.get("headers") or {}
        print("   PUT {} ({:.1f} MB)".format(name, size / 1048576), flush=True)
        with open(path, "rb") as fh:
            p = requests.put(url, data=fh, headers=headers, timeout=3600)
        print("   ->", p.status_code, (p.text or "")[:200], flush=True)
        if p.status_code not in (200, 201, 204):
            return 1

    r = requests.get(API + "/releases/tags/{}".format(TAG),
                     params={"access_token": tok}, timeout=30)
    j = r.json()
    print("\n=== 结果 ===", r.status_code)
    for a in (j.get("assets") or []):
        print("  asset:", a.get("name"), a.get("size"),
              a.get("browser_download_url") or a.get("download_url"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
