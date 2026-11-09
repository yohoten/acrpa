"""临时脚本: 创建 Gitee v0.1.30 Release 并上传 dist 附件。

Token 从环境变量 GITEE_TOKEN 读取（不落盘、不打印）。
"""
import os
import sys

import requests

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API = "https://gitee.com/api/v5"
REPO = "yohoten/ACRPA"
TAG = "v0.1.30"
BODY_FILE = os.path.join(BASE, "docs", "releases", "v0.1.30.md")
ASSETS = [
    os.path.join(BASE, "dist", "ACRPA-v0.1.30.exe"),
    os.path.join(BASE, "dist", "ACRPA-v0.1.30-setup.exe"),
    os.path.join(BASE, "dist", "ACRPA-v0.1.30-portable.zip"),
]


def main():
    tok = (os.environ.get("GITEE_TOKEN") or "").strip()
    if not tok:
        print("no GITEE_TOKEN")
        return 1

    # 0. 校验 token
    r = requests.get("{}/repos/{}".format(API, REPO),
                     params={"access_token": tok}, timeout=30)
    print("auth check:", r.status_code)
    if r.status_code != 200:
        print(r.text[:200])
        return 1

    body = open(BODY_FILE, encoding="utf-8").read()
    rel = None
    r = requests.get("{}/repos/{}/releases/tags/{}".format(API, REPO, TAG),
                     params={"access_token": tok}, timeout=30)
    print("release by tag:", r.status_code)
    if r.status_code == 200 and isinstance(r.json() if r.text.strip() else None, dict):
        rel = r.json()
        print("exists id=", rel.get("id"))
    else:
        # Gitee 对「tag 无 Release」返回 200 + body null，按不存在处理

        payload = {
            "access_token": tok,
            "tag_name": TAG,
            "name": "ACRPA {}".format(TAG),
            "body": body,
            "target_commitish": "main",
            "prerelease": False,
        }
        r = requests.post("{}/repos/{}/releases".format(API, REPO),
                          data=payload, timeout=120)
        print("create:", r.status_code)
        if r.status_code not in (200, 201):
            print(r.text[:300])
            return 1
        rel = r.json()
        print("created id=", rel.get("id"))

    rid = rel.get("id")
    if not rid:
        print("no release id")
        return 1

    assets = rel.get("assets") or []
    existing = {a.get("name"): a.get("size") for a in assets}
    print("assets now:", list(existing.keys()))
    for path in ASSETS:
        name = os.path.basename(path)
        size = os.path.getsize(path)
        if existing.get(name) == size:
            print("[SKIP]", name)
            continue
        print("[..] upload {} ({:.1f} MB)".format(name, size / 1048576))
        with open(path, "rb") as fh:
            up = requests.post(
                "{}/repos/{}/releases/{}/attach_files".format(API, REPO, rid),
                params={"access_token": tok},
                files={"file": (name, fh, "application/octet-stream")},
                timeout=1800)
        print("   ->", up.status_code)
        if up.status_code not in (200, 201):
            print("   ", up.text[:300])
            return 1

    r = requests.get("{}/repos/{}/releases/tags/{}".format(API, REPO, TAG),
                     params={"access_token": tok}, timeout=30)
    print("\n=== 结果 ===", r.status_code)
    if r.status_code == 200:
        j = r.json()
        print("tag:", j.get("tag_name"), "name:", j.get("name"),
              "prerelease:", j.get("prerelease"))
        for a in (j.get("assets") or []):
            print("  asset:", a.get("name"), a.get("size"),
                  a.get("browser_download_url"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
