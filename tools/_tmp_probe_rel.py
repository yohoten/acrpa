"""临时探测: Gitee / AtomGit v0.1.30 Release 现状 (token 走环境变量)。"""
import json
import os

import requests

GITEE = (os.environ.get("GITEE_TOKEN") or "").strip()
ATOM = (os.environ.get("ATOMGIT_TOKEN") or "").strip()
TAG = "v0.1.30"


def show(tag_name, r):
    txt = (r.text or "").strip()
    print("  status:", r.status_code, "len:", len(txt))
    try:
        j = r.json()
    except Exception:
        print("  body:", txt[:300])
        return None
    if isinstance(j, dict):
        print("  keys:", sorted(j.keys())[:25])
        for k in ("id", "release_id", "tag_name", "name", "prerelease", "assets", "attach_files"):
            if k in j:
                v = j[k]
                if k in ("assets", "attach_files") and isinstance(v, list):
                    print("  {}: {} item(s)".format(k, len(v)))
                    for a in v[:6]:
                        if isinstance(a, dict):
                            print("     -", a.get("name"), a.get("size"),
                                  a.get("browser_download_url") or a.get("download_url"))
                else:
                    print("  {}: {!r}".format(k, v))
    else:
        print("  list len:", len(j))
        if j:
            print("  item0 keys:", sorted(j[0].keys())[:25])
            print("  item0 id:", j[0].get("id"), "tag:", j[0].get("tag_name"))
    return j


print("== Gitee auth ==")
r = requests.get("https://gitee.com/api/v5/repos/yohoten/ACRPA",
                 params={"access_token": GITEE}, timeout=30)
print("  repo:", r.status_code)
if r.status_code == 200:
    print("  full_name:", r.json().get("full_name"))
else:
    print("  ", (r.text or "")[:200])

print("== Gitee release by tag ==")
r = requests.get("https://gitee.com/api/v5/repos/yohoten/ACRPA/releases/tags/{}".format(TAG),
                 params={"access_token": GITEE}, timeout=30)
show(TAG, r)

print("== AtomGit auth ==")
r = requests.get("https://atomgit.com/api/v5/repos/yohoten/ACRPA",
                 params={"access_token": ATOM}, timeout=30)
print("  repo:", r.status_code)
if r.status_code == 200:
    print("  full_name:", r.json().get("full_name"))

print("== AtomGit release by tag ==")
r = requests.get("https://atomgit.com/api/v5/repos/yohoten/ACRPA/releases/tags/{}".format(TAG),
                 params={"access_token": ATOM}, timeout=30)
show(TAG, r)

print("== AtomGit release list ==")
r = requests.get("https://atomgit.com/api/v5/repos/yohoten/ACRPA/releases",
                 params={"access_token": ATOM, "per_page": 5}, timeout=30)
show("list", r)

print("== dist ==")
for n in sorted(os.listdir(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "dist"))):
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dist", n)
    if os.path.isfile(p) and n.lower().endswith((".exe", ".zip")):
        print("  ", n, os.path.getsize(p))
