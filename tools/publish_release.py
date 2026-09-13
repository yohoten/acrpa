"""ACRPA 发布助手 —— 建 GitHub Release 并上传发布包 (走 git 已存凭据, 无需另配 token)。

用法 (项目根目录运行):
    python tools/publish_release.py                  # 建草稿 Release v<版本号>, 上传 dist/ACRPA.zip, 发布
    python tools/publish_release.py --exe            # 同时上传独立 EXE (沿用历史命名 ACRPA.vX.Y.Z.exe)
    python tools/publish_release.py --asset 路径     # 指定要上传的文件
    python tools/publish_release.py --recreate       # 重建已存在的 Release (删除后按草稿重来, tag 保留)
    python tools/publish_release.py --keep-draft     # 传完先不发布, 留作草稿人工确认
    python tools/publish_release.py --dry-run        # 只打印将要执行的动作, 不发请求
    python tools/publish_release.py --repo a/b       # 覆盖仓库 (缺省取 version_info.GITHUB_REPO)

为什么需要它:
    VERSION 首行声明的直链形如
        https://github.com/<owner>/<repo>/releases/download/v<ver>/ACRPA.zip
    这条 URL 只在「存在 tag v<ver> 的 Release, 且其中有一个恰好叫 ACRPA.zip 的附件」
    时才有效。tag 名、Release 是否存在、附件名三者任一不符就是 404 —— 而客户端会把
    “直链 404” 与“网络故障”区分开, 表现为明确的下载失败。手工点网页最容易漏的正是
    附件名 (上传时会被自动改名成 ACRPA-1.zip 之类)。

必须先建草稿 (本仓库实测):
    仓库启用了 release 不可变。已发布的 Release 既不能改正文也不能再加附件, 直接
    POST 附件会得到 422 "Cannot upload assets to an immutable release."。正确流程是
    先建 draft → 上传附件 → 最后 PATCH draft=false 发布。本工具默认走这条路径。

凭据: 复用 git 的 credential helper (本机为 wincred)。取不到时给出明确指引, 不静默失败。

网络 (实测本机):
  · 环境里有一个本地代理 (HTTP_PROXY=127.0.0.1:…)。小请求走它没问题, 但 13MB 的附件
    上传会被它回 "Tunnel connection failed: 502 Bad Gateway" —— 故上传优先直连。
  · 上行带宽实测仅 0.07-0.28 MB/s (0.5MB/2.4s、2MB/7.2s、5MB/66.9s), 因此上传超时
    默认给到 3600 秒。给成 60 秒那种常规值必然在写出阶段超时, 表现为
    "The write operation timed out", 容易被误判成认证或权限问题。
"""
import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VERSION_FILE = os.path.join(BASE, "VERSION")
RELEASES_DIR = os.path.join(BASE, "docs", "releases")
DIST = os.path.join(BASE, "dist")
API = "https://api.github.com"
UA = "ACRPA-publish-release"

sys.path.insert(0, os.path.join(BASE, "src"))
try:
    import version_info as vi           # noqa: E402
    DEFAULT_REPO = vi.GITHUB_REPO
except Exception:
    DEFAULT_REPO = "yohoten/acrpa"


def read_version():
    with open(VERSION_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                return line
    raise SystemExit("[FAIL] VERSION 首行读不到有效版本号")


def manifest_tag():
    """Release tag 取自 VERSION 的直链行。

    不能想当然按 v<版本号> 拼。本仓库 release 不可变: 一个 tag 被已发布的 Release 用过
    之后就不能再用于新 Release —— 即便那个 Release 已被删除, 也会报
    "tag_name was used by an immutable release" (实测)。历史线上本就有 v0.1.25.0 这类
    四段写法。直链是客户端真正会用的 URL, 故 tag 必须以它为准。
    """
    try:
        with open(VERSION_FILE, encoding="utf-8") as f:
            text = f.read()
    except Exception:
        return ""
    m = re.search(r"/releases/download/([^/]+)/", text)
    return m.group(1) if m else ""


def read_body(tag, version):
    """Release 正文本地草稿 —— 先找 <tag>.md, 再找 v<版本号>.md; 都没有则退化为一句说明。"""
    for name in ("{}.md".format(tag), "v{}.md".format(version)):
        path = os.path.join(RELEASES_DIR, name)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return f.read(), path
    return "ACRPA {}\n".format(tag), None


def git_token(host="github.com"):
    """从 git credential helper 取凭据 (优先 password, 兼容 GitHub PAT / OAuth token)。"""
    try:
        p = subprocess.run(
            ["git", "credential", "fill"],
            input="protocol=https\nhost={}\n\n".format(host),
            capture_output=True, text=True, timeout=30)
    except Exception as e:
        return "", "调用 git credential 失败: {}".format(e)
    for line in (p.stdout or "").splitlines():
        if line.startswith("password="):
            return line[len("password="):].strip(), ""
    return "", "git 未存有 {} 的凭据 (可先 git push 一次以写入)" .format(host)


def _opener(direct):
    """direct=True 时忽略 HTTP(S)_PROXY 环境变量, 直连。"""
    if direct:
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return urllib.request.build_opener()


def api(method, path, token, payload=None, timeout=60, raw=None, ctype=None,
        absolute=False, retries=2):
    """调用 GitHub API → (ok, 解析后的 dict/list 或 bytes, 错误说明)。

    通道选择 (实测本机踩坑): 环境里有一个本地代理 (HTTP_PROXY=127.0.0.1:...),
    小的 JSON 请求走它没问题, 但上传 13MB 附件时它回 "Tunnel connection failed:
    502 Bad Gateway" —— 代理对大体量 POST 不适用。因此上传一律先试直连, 失败再回退
    代理; 普通请求仍按环境变量走。两种通道各重试 retries 次, 覆盖偶发超时。
    """
    url = path if absolute else API + path
    data = None
    headers = {"User-Agent": UA, "Accept": "application/vnd.github+json",
               "Authorization": "token {}".format(token)}
    if raw is not None:
        data = raw
        headers["Content-Type"] = ctype or "application/octet-stream"
    elif payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    modes = [True, False] if raw is not None else [False, True]
    errors = []
    for direct in modes:
        op = _opener(direct)
        for _ in range(max(1, retries)):
            req = urllib.request.Request(url, data=data, headers=headers, method=method)
            try:
                with op.open(req, timeout=timeout) as r:
                    body = r.read()
                    if (ctype or "").startswith("application/octet"):
                        return True, body, ""
                    try:
                        return True, json.loads(body.decode("utf-8")), ""
                    except Exception:
                        return True, body, ""
            except urllib.error.HTTPError as e:
                detail = ""
                try:
                    detail = e.read().decode("utf-8", "replace")[:300]
                except Exception:
                    pass
                errors.append("{} 通道 HTTP {} {} {}".format(
                    "直连" if direct else "代理", e.code, e.reason, detail))
                # 4xx 是请求本身的问题, 换通道/重试都无意义
                if 400 <= e.code < 500:
                    return False, None, " | ".join(errors)
            except Exception as e:
                errors.append("{} 通道 {}: {}".format(
                    "直连" if direct else "代理", type(e).__name__, e))
    return False, None, " | ".join(errors)


def curl_upload(url, path, token, ctype, timeout):
    """用系统 curl 上传附件 → (ok, 响应, 错误)。

    为什么需要这条备用通道 (均为本机实测):
      · schannel 默认检查证书吊销。在 TLS 拦截的网络里这一步必然失败
        (CRYPT_E_NO_REVOCATION_CHECK), 与 git 需要 GIT_SSL_NO_VERIFY 同源。
        curl 的 --ssl-no-revoke 关掉该检查后通道即通 (实测 0.7s 返回)。
      · Python urllib 上传 13MB 时会被本机代理回 "Tunnel connection failed:
        502 Bad Gateway"; curl 的分块发送方式则正常。
    凭据经 600 权限的临时配置文件传入, 不出现在命令行参数里。
    """
    import tempfile

    cfg = os.path.join(tempfile.gettempdir(), "acrpa_publish_curl.cfg")
    abs_path = os.path.abspath(path).replace("\\", "/")
    body = ('url = "{url}"\n'
            'request = "POST"\n'
            'header = "Authorization: token {tok}"\n'
            'header = "Content-Type: {ctype}"\n'
            'header = "User-Agent: {ua}"\n'
            'data-binary = "@{path}"\n'
            'connect-timeout = 30\n'
            'max-time = {timeout}\n'
            'ssl-no-revoke\n'
            'silent\n'
            'show-error\n').format(url=url, tok=token, ctype=ctype, ua=UA,
                                   path=abs_path, timeout=timeout)
    try:
        with open(cfg, "w", encoding="utf-8") as f:
            f.write(body)
        try:
            os.chmod(cfg, 0o600)
        except Exception:
            pass
        p = subprocess.run(["curl", "--config", cfg, "-w", "\n%{http_code}"],
                           capture_output=True, timeout=timeout + 120)
    except Exception as e:
        return False, None, "curl 调用失败: {}".format(e)
    finally:
        try:
            os.remove(cfg)
        except Exception:
            pass

    out = (p.stdout or b"").decode("utf-8", "replace")
    err = (p.stderr or b"").decode("gbk", "replace").strip()
    if p.returncode != 0:
        return False, None, "curl rc={} {}".format(p.returncode, err[-300:])
    text, _, code = out.rpartition("\n")
    if not code.strip().startswith("2"):
        return False, None, "curl HTTP {} {}".format(code.strip(), text[-300:])
    try:
        return True, json.loads(text), ""
    except Exception:
        return True, text, ""


def _report(repo, token, tag, name):
    """打印 latest release 与附件清单, 便于一眼核对直链是否可解析。"""
    okl, latest, whyl = api("GET", "/repos/{}/releases/latest".format(repo), token)
    print("\n=== 结果 ===")
    if okl and isinstance(latest, dict):
        print("latest release : {} ({}), draft={} prerelease={}".format(
            latest.get("tag_name"), latest.get("name"),
            latest.get("draft"), latest.get("prerelease")))
        for a in latest.get("assets", []):
            print("  asset: {:<24} {:>10}  {}".format(
                a.get("name"), a.get("size"), a.get("browser_download_url")))
    else:
        print("[WARN] 读 latest 失败: {}".format(whyl))
    print("\n直链 (VERSION 首行): https://github.com/{}/releases/download/{}/{}".format(
        repo, tag, name))
    print("下一步: 清 CDN 缓存 → python tools/make_release.py --no-zip --purge-cdn main")


def main():
    ap = argparse.ArgumentParser(description="ACRPA 发布助手")
    ap.add_argument("--repo", default=DEFAULT_REPO, help="owner/repo (缺省 {})".format(DEFAULT_REPO))
    ap.add_argument("--asset", help="要上传的发布包 (缺省 dist/ACRPA.zip)")
    ap.add_argument("--exe", action="store_true", help="同时上传独立 EXE")
    ap.add_argument("--dry-run", action="store_true", help="只打印动作")
    ap.add_argument("--recreate", action="store_true",
                    help="已存在同名已发布 Release 时删除重建 (tag 与提交保留)")
    ap.add_argument("--keep-draft", action="store_true",
                    help="传完不发布, 留作草稿 (便于人工核对后再手动发布)")
    ap.add_argument("--transport", choices=["auto", "urllib", "curl"], default="auto",
                    help="上传通道: auto=urllib 失败后自动改用 curl (缺省)")
    ap.add_argument("--timeout", type=int, default=3600,
                    help="上传超时秒数 (缺省 3600; 实测上行约 0.1-0.3 MB/s, "
                         "13MB 包需要数分钟, 超时给小了必然半途失败)")
    args = ap.parse_args()

    version = read_version()
    tag = manifest_tag() or "v{}".format(version)
    asset = args.asset or os.path.join(DIST, "ACRPA.zip")
    body, body_path = read_body(tag, version)

    if not os.path.exists(asset):
        print("[FAIL] 找不到发布包 {}, 请先运行 tools/make_release.py".format(asset),
              file=sys.stderr)
        return 1

    size = os.path.getsize(asset)
    name = os.path.basename(asset)
    print("仓库   : {}".format(args.repo))
    print("tag    : {}".format(tag))
    print("发布包 : {} ({:.1f} MB)".format(name, size / (1024 * 1024)))
    print("正文   : {}".format(os.path.relpath(body_path, BASE) if body_path
                              else "(无本地正文, 用占位说明)"))

    # 附件名必须与 VERSION 首行直链的最后一段完全一致, 否则那条直链就是 404。
    if name != "ACRPA.zip":
        print("[WARN] 附件名为 {!r}, 而 VERSION 的直链指向 ACRPA.zip。".format(name),
              file=sys.stderr)
        print("       客户端会按 ACRPA.zip 拼直链, 名字不符即 404。", file=sys.stderr)

    token, err = git_token()
    if not token:
        print("[FAIL] 取不到 GitHub 凭据: {}".format(err), file=sys.stderr)
        print("       本工具复用 git 的 credential helper, 请先确认能 "
              "`git push github gh-merge:main`。", file=sys.stderr)
        return 1

    if args.dry_run:
        print("\n[dry-run] 将执行:")
        print("  1. GET  /repos/{}/releases/tags/{}".format(args.repo, tag))
        print("  2. 不存在则 POST 建【草稿】Release; 已存在且已发布则{}".format(
            "删除重建 (--recreate)" if args.recreate else "报错提示需要 --recreate"))
        print("  3. 上传 {} (同名附件先删除, 否则会被改名为 ACRPA-1.zip 而使直链 404)".format(name))
        if args.exe:
            print("  4. 上传独立 EXE")
        print("  {}. PATCH 草稿 → 已发布{}".format(
            5 if args.exe else 4, " (已指定 --keep-draft, 跳过)" if args.keep_draft else ""))
        print("\n  注: 本仓库启用了 release 不可变, 附件必须在【草稿阶段】上传。")
        return 0

    # 1. 定位/建立 Release (必须先草稿, 见模块 docstring)
    ok, rel, why = api("GET", "/repos/{}/releases/tags/{}".format(args.repo, tag), token)
    exists = bool(ok and isinstance(rel, dict) and rel.get("id"))

    need_names = {name}
    want_exe = os.path.join(DIST, "ACRPA v{}.exe".format(version))
    if args.exe and os.path.exists(want_exe):
        need_names.add("ACRPA.v{}.exe".format(version))

    if exists and not rel.get("draft"):
        have = {a.get("name") for a in (rel.get("assets") or [])}
        if need_names <= have:
            print("[OK] Release {} 已发布且附件齐全, 无需重传。".format(tag))
            print("     (本仓库 release 不可变, 已发布的 Release 无法再改, 故直接跳过)")
            _report(args.repo, token, tag, name)
            return 0
        if not args.recreate:
            print("[FAIL] Release {} 已发布, 无法再添加附件。".format(tag), file=sys.stderr)
            print("       本仓库启用了 release 不可变 —— 已发布的 Release 既不能改正文也不能"
                  "加附件, POST 附件会得到 422。", file=sys.stderr)
            print("       缺少: {}".format(sorted(need_names - have)), file=sys.stderr)
            print("       请加 --recreate 删除后按草稿重建 (tag 与已推送的提交不受影响)。",
                  file=sys.stderr)
            return 1
        okd, _, whyd = api("DELETE", "/repos/{}/releases/{}".format(args.repo, rel["id"]), token)
        if not okd:
            print("[FAIL] 删除旧 Release 失败: {}".format(whyd), file=sys.stderr)
            return 1
        print("[OK] 已删除已发布 Release {} (tag 保留), 将以草稿重建".format(tag))
        exists = False

    if exists:
        okp, rel2, whyp = api("PATCH", "/repos/{}/releases/{}".format(args.repo, rel["id"]),
                              token, payload={"name": "ACRPA {}".format(tag), "body": body})
        if okp:
            rel = rel2
            print("[OK] 草稿 Release {} 已存在, 正文已更新 (id={})".format(tag, rel["id"]))
        else:
            print("[WARN] 更新草稿正文失败: {}".format(whyp))
    else:
        okc, rel2, whyc = api("POST", "/repos/{}/releases".format(args.repo), token,
                              payload={"tag_name": tag, "name": "ACRPA {}".format(tag),
                                       "body": body, "draft": True, "prerelease": False})
        if not okc:
            print("[FAIL] 建草稿 Release 失败: {}".format(whyc), file=sys.stderr)
            return 1
        rel = rel2
        print("[OK] 已建立草稿 Release {} (id={})".format(tag, rel["id"]))

    rel_id = rel["id"]
    upload_url = rel.get("upload_url") or \
        "{}/repos/{}/releases/{}/assets".format(API, args.repo, rel_id)
    # 去掉 upload_url 里的 {?name,label} 模板
    upload_url = upload_url.split("{")[0]

    # 2. 上传 (同名先删, 否则 GitHub 会改名成 ACRPA-1.zip, 直链随即 404)
    targets = [(asset, name)]
    if args.exe:
        exe = os.path.join(DIST, "ACRPA v{}.exe".format(version))
        if os.path.exists(exe):
            targets.append((exe, "ACRPA.v{}.exe".format(version)))
        else:
            print("[WARN] 未找到 {} , 跳过独立 EXE".format(exe))

    existing = {a.get("name"): (a.get("id"), a.get("size"))
                for a in (rel.get("assets") or [])}
    for path, aname in targets:
        cur = existing.get(aname)
        if cur and cur[1] == os.path.getsize(path):
            # 重跑时不必重传: 本机上行仅 0.1-0.3 MB/s, 一个 13MB 附件要数分钟。
            print("[OK] {} 已存在且体积一致 ({}), 跳过重传".format(aname, cur[1]))
            continue
        if cur:
            okd, _, whyd = api("DELETE",
                               "/repos/{}/releases/assets/{}".format(args.repo, cur[0]), token)
            if okd:
                print("[OK] 已删除同名旧附件 {} (体积 {} 不符)".format(aname, cur[1]))
            else:
                print("[WARN] 删除旧附件失败: {}".format(whyd))

        raw = open(path, "rb").read()
        ctype = "application/zip" if path.lower().endswith(".zip") else "application/octet-stream"
        query = urllib.parse.urlencode({"name": aname})
        target = "{}?{}".format(upload_url, query)
        print("[..] 上传 {} ({:.1f} MB) …".format(aname, len(raw) / (1024 * 1024)))

        oku, res, whyu = (False, None, "已跳过")
        if args.transport != "curl":
            oku, res, whyu = api("POST", target, token, raw=raw, ctype=ctype,
                                 timeout=args.timeout)
        if not oku:
            if args.transport == "auto":
                print("[..] urllib 通道未通, 改用 curl 通道 …")
                print("     urllib: {}".format(str(whyu)[:200]))
            if args.transport != "urllib":
                oku, res, whyu = curl_upload(target, path, token, ctype, args.timeout)
        if not oku:
            print("[FAIL] 上传 {} 失败: {}".format(aname, whyu), file=sys.stderr)
            return 1
        got = (res or {}).get("size")
        print("[OK] 已上传 {} (服务端 size={} / 本地 {})".format(aname, got, len(raw)))
        if got is not None and got != len(raw):
            print("[WARN] 服务端体积与本地不一致!", file=sys.stderr)

    # 3. 发布草稿 (附件必须在草稿阶段传完, 之后 Release 即不可变)
    if args.keep_draft:
        print("\n[OK] 已按 --keep-draft 保留为草稿, 未发布。")
        print("     人工确认后发布: 在网页上点 Publish release, 或重跑本工具 (不带 --keep-draft)。")
        _report(args.repo, token, tag, name)
        return 0

    okp, rel3, whyp = api("PATCH", "/repos/{}/releases/{}".format(args.repo, rel_id), token,
                          payload={"draft": False})
    if not okp:
        print("[FAIL] 发布会话失败 (附件已上传, 仍可人工发布): {}".format(whyp),
              file=sys.stderr)
        return 1
    print("[OK] 已发布 Release {} —— 直链现已可用".format(tag))

    # 4. 回报结果
    _report(args.repo, token, tag, name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
