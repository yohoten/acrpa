"""探测 AtomGit upload_url 返回结构与后续提交方式。"""
import json
import os

import requests

TOK = (os.environ.get("ATOMGIT_TOKEN") or "").strip()
API = "https://atomgit.com/api/v5/repos/yohoten/ACRPA"
TAG = "v0.1.30"

r = requests.get(API + "/releases/{}/upload_url".format(TAG),
                 params={"access_token": TOK, "file_name": "probe.txt"}, timeout=30)
print("upload_url:", r.status_code)
print(r.text[:1200])

# 若返回 url, 试探 PUT 一个小文件是否被接受 (仅探测, 之后尝试删除)
try:
    j = r.json()
except Exception:
    j = {}
print("\nkeys:", sorted(j.keys()))
for k, v in j.items():
    s = str(v)
    print(" ", k, "=", s[:120])
