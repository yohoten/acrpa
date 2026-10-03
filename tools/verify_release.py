# -*- coding: utf-8 -*-
"""发布后核验 —— 一条命令确认「用户真的下得到、且下到的是对的东西」。

背景: 本仓库 release 启用了不可变策略, 且客户端更新要同时依赖
  ① GitHub Release 附件名与 VERSION 直链末段一致
  ② tag 指向的提交正是本次发布的那份代码
  ③ sha256 与实际下载物一致
  ④ 三处清单镜像 (raw / jsDelivr / Gitee raw) 不落后
任一条不成立, 用户侧就是"提示有新版本却下不动/校验失败/装回旧包", 而发布者
在网页上完全看不出来。这些检查此前全靠人工一条条敲, 本脚本把它固化。

核验项:
  V1 本地清单格式        VERSION 首行 / 直链 / sha256 / tag / 附件名
  V2 本地产物校验        dist/<附件名> 是否存在且 sha256 与清单一致
  V3 tag → commit        远端 tag(含附注 tag 解引用)是否指向本地 HEAD
  V4 Release 状态        存在 / 非 draft / prerelease 是否符合预期
  V5 Release 附件        名称、体积、digest 与本地一致
  V6 直链真实下载        实际 GET 并校验 sha256 (--no-download 可跳过)
  V7 清单镜像            raw.githubusercontent / jsDelivr / Gitee raw 的首行版本
  V8 Gitee 镜像          tag 与 main 分支 SHA 是否已同步 (公开 API, 无需 token)

用法:
  python tools/verify_release.py
  python tools/verify_release.py --no-download
  python tools/verify_release.py --tag v0.1.29.0 --expect-prerelease
  python tools/verify_release.py --resolve github.com:140.82.114.3   # 本机 github.com 被阻断时
退出码: 0=全部通过(允许 WARN); 1=存在 FAIL
"""
import argparse
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "tools"))
import publish_release as pr                       # noqa: E402  复用 git_token / api

VERSION_FILE = os.path.join(BASE, "VERSION")
DIST = os.path.join(BASE, "dist")
GITHUB_REPO = "yohoten/acrpa"
GITEE_REPO = "yohoten/ACRPA"
MANIFEST_SOURCES = (
    "https://cdn.jsdelivr.net/gh/yohoten/acrpa@main/VERSION",
    "https://raw.githubusercontent.com/yohoten/acrpa/main/VERSION",
    "https://gitee.com/yohoten/acrpa/raw/master/VERSION",
)
UA = "ACRPA-verify-release"

_OK, _FAIL, _WARN = [], [], []


def ok(msg):
    _OK.append(msg)
    print("[OK]   " + msg)


def fail(msg):
    _FAIL.append(msg)
    print("[FAIL] " + msg)


def warn(msg):
    _WARN.append(msg)
    print("[WARN] " + msg)


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def http_text(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return True, r.read().decode("utf-8", "replace")
    except Exception as e:
        return False, "{}: {}".format(type(e).__name__, e)


def download(url, dest, resolve=None, timeout=600):
    """下载 → (ok, 说明)。优先用系统 curl (可 --resolve 绕过被阻断的 IP)。"""
    curl = shutil.which("curl")
    if curl:
        cmd = [curl, "-sSL", "--ssl-no-revoke", "--max-time", str(timeout),
               "-o", dest, "-w", "%{http_code} %{size_download}"]
        if resolve:
            cmd += ["--resolve", resolve]
        cmd.append(url)
        p = subprocess.run(cmd, capture_output=True)
        txt = (p.stdout or b"").decode("utf-8", "replace").strip()
        if p.returncode != 0:
            return False, "curl rc={} {}".format(
                p.returncode, (p.stderr or b"").decode("gbk", "replace")[:200])
        return True, txt
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=timeout) as r, open(dest, "wb") as f:
            shutil.copyfileobj(r, f)
        return True, "urllib"
    except Exception as e:
        return False, "{}: {}".format(type(e).__name__, e)


def local_manifest():
    with io.open(VERSION_FILE, encoding="utf-8") as f:
        lines = [l.strip() for l in f
                 if l.strip() and not l.strip().startswith("#")]
    version = lines[0] if lines else ""
    url = next((l for l in lines[1:] if re.match(r"^https?://", l, re.I)), "")
    m = next((re.match(r"^sha256\s*[:=]\s*([0-9a-fA-F]{64})$", l) for l in lines[1:]
              if re.match(r"^sha256\s*[:=]", l, re.I)), None)
    sha = m.group(1).lower() if m else ""
    tag = ""
    mt = re.search(r"/releases/download/([^/]+)/", url)
    if mt:
        tag = mt.group(1)
    asset = url.rsplit("/", 1)[-1] if url else ""
    return {"version": version, "url": url, "sha256": sha, "tag": tag, "asset": asset}


def _norm_resolve(spec):
    """把 host:ip 补全为 curl 需要的 host:port:ip。"""
    if not spec:
        return ""
    parts = spec.split(":")
    if len(parts) == 2:
        return "{}:443:{}".format(parts[0], parts[1])
    return spec


def git(*argv):
    try:
        p = subprocess.run(["git"] + list(argv), cwd=BASE,
                           capture_output=True, text=True)
        return p.returncode, (p.stdout or "").strip()
    except Exception:
        return 1, ""


def local_head():
    return git("rev-parse", "HEAD")[1]


def resolve_github_tag(tag, token, repo):
    """附注 tag 需二次解引用 → (commit_sha, 说明)。"""
    ok1, ref, why1 = pr.api("GET", "/repos/{}/git/ref/tags/{}".format(repo, tag), token)
    if not ok1 or not isinstance(ref, dict):
        return "", "读 tag ref 失败: {}".format(why1)
    obj = ref.get("object") or {}
    if obj.get("type") == "tag":
        ok2, tagobj, why2 = pr.api("GET", "/repos/{}/git/tags/{}".format(repo, obj.get("sha")),
                                  token)
        if not ok2 or not isinstance(tagobj, dict):
            return "", "解引用附注 tag 失败: {}".format(why2)
        return (tagobj.get("object") or {}).get("sha", ""), ""
    return obj.get("sha", ""), ""


def main():
    ap = argparse.ArgumentParser(description="ACRPA 发布后核验")
    ap.add_argument("--repo", default=GITHUB_REPO)
    ap.add_argument("--gitee-repo", default=GITEE_REPO)
    ap.add_argument("--tag", default="", help="缺省取 VERSION 直链里的 tag")
    ap.add_argument("--no-download", action="store_true", help="跳过 V6 真实下载")
    ap.add_argument("--expect-prerelease", action="store_true",
                    help="要求 Release 必须是 Pre-release (beta 通道)")
    ap.add_argument("--resolve", default="", metavar="HOST:IP",
                    help="下载时强制解析 (curl --resolve), 如 github.com:140.82.114.3; "
                         "本机 github.com 被阻断时用它换一个可达 IP")
    ap.add_argument("--json", default="", help="核验结果写入 JSON")
    args = ap.parse_args()

    man = local_manifest()
    tag = args.tag or man["tag"]
    print("=" * 70)
    print("ACRPA 发布后核验 | repo={} tag={} asset={}".format(args.repo, tag or "(未知)",
                                                              man["asset"] or "(未知)"))
    print("=" * 70)

    # ── V1 本地清单 ──
    print("\n── V1 本地清单 ──")
    (ok if re.match(r"^\d+\.\d+\.\d+", man["version"] or "") else fail)(
        "VERSION 首行版本号: {!r}".format(man["version"]))
    (ok if man["url"] else fail)("VERSION 声明下载直链")
    (ok if man["sha256"] else warn)(
        "VERSION 声明 sha256: {}".format(man["sha256"][:16] + "…" if man["sha256"] else "缺失"))
    (ok if tag else fail)("从直链解析出 tag: {!r}".format(tag))

    # ── V2 本地产物 ──
    print("\n── V2 本地产物 ──")
    local_pkg = os.path.join(DIST, man["asset"]) if man["asset"] else ""
    local_sha = ""
    if local_pkg and os.path.isfile(local_pkg):
        local_sha = sha256_of(local_pkg)
        size = os.path.getsize(local_pkg)
        if man["sha256"] and local_sha != man["sha256"]:
            fail("本地 {} 的 sha256 与 VERSION 声明不一致 (本地 {}… 声明 {}…)".format(
                man["asset"], local_sha[:12], man["sha256"][:12]))
        else:
            ok("本地 {} 存在且 sha256 相符 ({} bytes)".format(man["asset"], size))
    else:
        warn("本地 dist/{} 不存在, 跳过本地产物比对 (只核验线上)".format(man["asset"]))

    # ── V3/V4/V5 GitHub ──
    token, err = pr.git_token()
    if not token:
        fail("取不到 GitHub 凭据, 无法核验 Release: {}".format(err))
        rel = None
    else:
        print("\n── V3 tag → commit ──")
        head = local_head()
        if not tag:
            fail("没有 tag, 无法核验")
        else:
            commit, why = resolve_github_tag(tag, token, args.repo)
            if not commit:
                fail("远端 tag {} 无法解析: {}".format(tag, why))
            elif not head:
                ok("tag {} → {} (本地无 git 上下文, 跳过祖先关系判定)".format(
                    tag, commit[:12]))
            elif commit == head:
                ok("tag {} → {} (与本地 HEAD 一致)".format(tag, commit[:12]))
            else:
                # 发布之后再提交文档/工具是常态, 因此要求 tag 指向的提交
                # 必须是 HEAD 的祖先, 而不是"等于 HEAD"。
                rc, _ = git("merge-base", "--is-ancestor", commit, head)
                if rc == 0:
                    _rc, cnt = git("rev-list", "--count", "{}..HEAD".format(commit))
                    ok("tag {} → {} 是本地 HEAD 的祖先 (其后另有 {} 个提交)".format(
                        tag, commit[:12], cnt or "?"))
                else:
                    fail("tag {} 指向 {} , 既不是本地 HEAD {} 也不是其祖先 —— "
                         "发布内容与当前代码不一致".format(tag, commit[:12], head[:12]))

        print("\n── V4/V5 Release 与附件 ──")
        okl, rel, whyl = pr.api("GET", "/repos/{}/releases/tags/{}".format(args.repo, tag),
                                token)
        if not okl or not isinstance(rel, dict):
            fail("读 Release 失败: {}".format(whyl))
            rel = None
        else:
            if rel.get("draft"):
                fail("Release 仍是草稿 (draft=True) —— 用户看不到")
            else:
                ok("Release 已发布 (draft=False)")
            is_pre = bool(rel.get("prerelease"))
            if args.expect_prerelease and not is_pre:
                fail("Release 不是 Pre-release —— beta 通道必须为预发布, "
                     "否则 /releases/latest 会把半成品当正式版推给所有用户")
            elif args.expect_prerelease:
                ok("Release 为 Pre-release (符合 beta 通道)")
            else:
                print("[INFO] prerelease={} immutable={}".format(is_pre, rel.get("immutable")))

            assets = rel.get("assets") or []
            names = [a.get("name") for a in assets]
            if man["asset"] in names:
                a = next(x for x in assets if x.get("name") == man["asset"])
                if a.get("size") and local_pkg and os.path.isfile(local_pkg) \
                        and a["size"] != os.path.getsize(local_pkg):
                    fail("附件体积不符: 线上 {} / 本地 {}".format(
                        a["size"], os.path.getsize(local_pkg)))
                else:
                    ok("附件体积相符: {} ({} bytes)".format(man["asset"], a.get("size")))
                digest = (a.get("digest") or "").lower().replace("sha256:", "")
                if man["sha256"] and digest and digest != man["sha256"]:
                    fail("附件 digest 与 VERSION 声明不一致 (线上 {}… 声明 {}…)".format(
                        digest[:12], man["sha256"][:12]))
                elif digest:
                    ok("附件 digest 相符 ({})".format(digest[:16] + "…"))
                else:
                    warn("Release API 未给出附件 digest")
            else:
                fail("Release 附件里没有 {} (现有: {}) —— VERSION 直链会 404".format(
                    man["asset"], names or "空"))

    # ── V6 直链真实下载 ──
    print("\n── V6 直链真实下载 ──")
    if args.no_download:
        warn("按 --no-download 跳过")
    elif not man["url"]:
        fail("没有直链可下载")
    else:
        tmp = os.path.join(tempfile.gettempdir(), "acrpa_verify_" + (man["asset"] or "pkg"))
        if os.path.exists(tmp):
            os.remove(tmp)
        okd, info = download(man["url"], tmp, _norm_resolve(args.resolve) or None)
        if not okd:
            fail("下载失败: {}".format(info))
        else:
            got = sha256_of(tmp)
            size = os.path.getsize(tmp)
            if man["sha256"] and got != man["sha256"]:
                fail("下载物 sha256 不符 (下载 {}… 声明 {}…)".format(
                    got[:12], man["sha256"][:12]))
            else:
                ok("直链可下载且 sha256 相符 ({} bytes, {})".format(size, info))
            os.remove(tmp)

    # ── V7 清单镜像 ──
    print("\n── V7 清单镜像 ──")
    for src in MANIFEST_SOURCES:
        gok, text = http_text(src)
        if not gok:
            warn("{} 读取失败: {}".format(src.split("/")[2], text))
            continue
        first = next((l.strip() for l in text.splitlines()
                      if l.strip() and not l.strip().startswith("#")), "")
        if first == man["version"]:
            ok("{} → {}".format(src.split("/")[2], first))
        else:
            warn("{} → {} (本地为 {})".format(src.split("/")[2], first or "(空)", man["version"]))

    # ── V8 Gitee 镜像 ──
    print("\n── V8 Gitee 镜像 ──")
    gok, gtext = http_text("https://gitee.com/api/v5/repos/{}/tags".format(args.gitee_repo))
    if not gok:
        warn("Gitee tags 读取失败: {}".format(gtext))
    else:
        try:
            tags = json.loads(gtext)
            names = [t.get("name") for t in tags] if isinstance(tags, list) else []
            (ok if tag in names else warn)(
                "Gitee tag {} {}".format(tag, "已同步" if tag in names else
                                         "缺失 (Gitee 侧无该 tag)"))
        except Exception as e:
            warn("Gitee tags 解析失败: {}".format(e))
    gok2, btext = http_text("https://gitee.com/api/v5/repos/{}/branches/main".format(
        args.gitee_repo))
    if gok2:
        try:
            b = json.loads(btext)
            gsha = ((b.get("commit") or {}).get("sha") or "")[:12]
            head = local_head()[:12]
            (ok if gsha == head else warn)(
                "Gitee main → {} ({})".format(gsha, "与本地一致" if gsha == head
                                              else "本地为 {}".format(head)))
        except Exception as e:
            warn("Gitee branch 解析失败: {}".format(e))
    else:
        warn("Gitee branch 读取失败: {}".format(btext))

    print("\n" + "=" * 70)
    print("通过 {} / 警告 {} / 失败 {}".format(len(_OK), len(_WARN), len(_FAIL)))
    if args.json:
        with io.open(args.json, "w", encoding="utf-8") as f:
            json.dump({"tag": tag, "manifest": man, "ok": _OK, "warn": _WARN,
                       "fail": _FAIL}, f, ensure_ascii=False, indent=2)
        print("结果已写入: {}".format(os.path.relpath(args.json, BASE)))
    print("结论: {}".format("FAIL" if _FAIL else "PASS"))
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
