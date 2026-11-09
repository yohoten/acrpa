"""临时脚本: 给 Gitee v0.1.30 Release 上传 dist 产物 (token 走环境变量)。"""
import os
import sys

import requests

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API = "https://gitee.com/api/v5"
REPO = "yohoten/ACRPA"
TAG = "v0.1.30"
ASSETS = [
    "dist/ACRPA-v0.1.30.exe",
    "dist/ACRPA-v0.1.30-setup.exe",
    "dist/ACRPA-v0.1.30-portable.zip",
]


def main():
    tok = (os.environ.get("GITEE_TOKEN") or "").strip()
    if not tok:
        print("no GITEE_TOKEN")
        return 1

    r = requests.get("{}/repos/{}/releases/tags/{}".format(API, REPO, TAG),
                     params={"access_token": tok}, timeout=30)
    rel = r.json() if r.status_code == 200 and (r.text or "").strip() else None
    if not isinstance(rel, dict):
        print("release missing:", r.status_code, (r.text or "")[:200])
        return 1
    rid = rel.get("id")
    print("release id:", rid, "title:", rel.get("name"))
    have = {a.get("name"): a.get("size") for a in (rel.get("assets") or [])
            if a.get("size")}
    print("existing binary assets:", have)

    for rel_path in ASSETS:
        path = os.path.join(BASE, rel_path)
        name = os.path.basename(path)
        size = os.path.getsize(path)
        if have.get(name) == size:
            print("[SKIP]", name, size)
            continue
        print("[..] upload {} ({:.1f} MB)".format(name, size / 1048576), flush=True)
        with open(path, "rb") as fh:
            up = requests.post(
                "{}/repos/{}/releases/{}/attach_files".format(API, REPO, rid),
                params={"access_token": tok},
                files={"file": (name, fh, "application/octet-stream")},
                timeout=3600)
        print("   ->", up.status_code, (up.text or "")[:200], flush=True)
        if up.status_code not in (200, 201):
            return 1

    r = requests.get("{}/repos/{}/releases/tags/{}".format(API, REPO, TAG),
                     params={"access_token": tok}, timeout=30)
    j = r.json()
    print("\n=== 结果 ===", r.status_code)
    for a in (j.get("assets") or []):
        print("  asset:", a.get("name"), a.get("size"),
              a.get("browser_download_url"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
