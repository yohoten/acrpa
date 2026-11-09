"""轮询确认 Gitee / AtomGit v0.1.30 Release 附件是否齐备 (最多 6 分钟)。"""
import os
import time

import requests

GITEE = (os.environ.get("GITEE_TOKEN") or "").strip()
ATOM = (os.environ.get("ATOMGIT_TOKEN") or "").strip()
TAG = "v0.1.30"
WANT = {
    "ACRPA-v0.1.30.exe": 18893312,
    "ACRPA-v0.1.30-setup.exe": 17178154,
    "ACRPA-v0.1.30-portable.zip": 18618820,
}


def fetch(api):
    r = requests.get("{}/repos/{}/releases/tags/{}".format(api, "yohoten/ACRPA", TAG),
                     params={"access_token": (GITEE if "gitee" in api else ATOM)},
                     timeout=30)
    if r.status_code != 200:
        return None
    try:
        return r.json()
    except Exception:
        return None


def state(rel):
    got = {}
    for a in (rel or {}).get("assets") or []:
        got[a.get("name")] = a.get("size")
    ok = [n for n, s in WANT.items() if got.get(n) == s]
    return got, ok


deadline = time.time() + 360
while time.time() < deadline:
    g = fetch("https://gitee.com/api/v5")
    a = fetch("https://atomgit.com/api/v5")
    gs, gok = state(g)
    as_, aok = state(a)
    print("gitee: {}/3  atomgit: {}/3".format(len(gok), len(aok)), flush=True)
    if len(gok) == 3 and len(aok) == 3:
        break
    time.sleep(20)

print("\n=== Gitee ===")
for n, s in sorted(gs.items()):
    print("  ", n, s, "OK" if WANT.get(n) == s else "")
print("=== AtomGit ===")
for n, s in sorted(as_.items()):
    print("  ", n, s, "OK" if WANT.get(n) == s else "")
