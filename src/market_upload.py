# -*- coding: utf-8 -*-
"""market_upload.py — 脚本市场 v2 批次3: 打包 + Gitee/GitHub OpenAPI 建分支→提交→发 PR。

设计依据 docs/marketplace-v2-design.md §2.5（OpenAPI 调用序列、错误处理、体积策略）。

对外接口:
    class UploadError(Exception)
    DEFAULT_REPO               = {"gitee": (owner, repo), "github": (owner, repo)}
    prepare_package(script_path, meta, images=None, out_path=None)
        -> (pkg_path, sha256, manifest)
    upload_index_patch(index_text, manifest, sha256, size, pkg_rel_path) -> str
    generate_pr_body(manifest) -> str
    validate_meta(meta, existing_ids=None) -> (errors, warnings)   # 上传向导前端预校验
    check_script_commands(script_path) -> list                      # 未注册命令名列表
    upload(provider, pkg_path, meta, *, owner=None, repo=None,
           base_branch="master", token=None, progress=None, cancel=None) -> dict
    upload_package = upload  # 语义别名

upload() 返回 dict: {"pr_url", "pr_number", "branch", "pkg_path"}
progress(stage:str, done:int, total:int)，stage ∈
    {"verify","branch","upload_pkg","upload_index","pull","done"}

安全规约（与 accounts 同款）:
    * token 仅来自 accounts.get_token(provider) 或显式传参；执行前校验，无则明确报错；
    * 请求/异常文本一律经 accounts._redact() 脱敏，绝不打印 token 明文；
    * 网络层 _http 为模块级可替换函数，便于自测整体注入 mock（不触发真实网络）。

可测结构:
    自测可直接 `market_upload._http = fake`（签名见 _http）以捕获请求序列；
    亦可 monkeypatch accounts.verify_token / accounts.get_token。
"""
import os
import json
import time
import base64

import accounts
import script_package
from utils import log1


class UploadError(Exception):
    """脚本市场上传异常（错误文本经脱敏，绝不含 token 明文）。"""
    pass


# ── 常量 ──
DEFAULT_REPO = {
    "gitee":  ("yohoten", "acrpa-marketplace"),
    "github": ("yohoten", "acrpa-marketplace"),
}

_API_GITEE = "https://gitee.com/api/v5"
_API_GITHUB = "https://api.github.com"

_TIMEOUT = 30
_USER_AGENT = "ACRPA/2.0"
_ONE_MB = 1024 * 1024

# 进度阶段（顺序即进度推进）
_STAGES = ("verify", "branch", "upload_pkg", "upload_index", "pull", "done")


# ══════════════════════════════════════════════════════════════════════
# HTTP 层（模块级可替换 → 自测注入 mock）
# ══════════════════════════════════════════════════════════════════════

class _Resp(object):
    """极简响应容器：统一 requests.Response 与 urllib 的差异。"""

    def __init__(self, status=0, data=None, headers=None, text=""):
        try:
            self.status = int(status or 0)
        except Exception:
            self.status = 0
        self.data = data
        self.headers = headers or {}
        self.text = text or ""


def _http(method, url, *, headers=None, params=None, json_body=None,
          timeout=_TIMEOUT):
    """统一 HTTP 入口（自测可整体替换以捕获/伪造请求序列）。

    requests 优先；缺依赖回退 urllib（仅支持 GET/POST/PUT/PATCH）。
    返回 _Resp(status, data, headers, text)。**不做** token 脱敏（调用方负责）。
    """
    hdrs = dict(headers or {})
    try:
        import requests
        r = requests.request(method, url, headers=hdrs, params=params,
                             json=json_body, timeout=timeout)
        data = None
        try:
            data = r.json()
        except Exception:
            data = None
        return _Resp(r.status_code, data, dict(getattr(r, "headers", {}) or {}),
                     getattr(r, "text", "") or "")
    except ImportError:
        pass

    import urllib.parse
    import urllib.request
    import urllib.error

    if params:
        qs = urllib.parse.urlencode(params)
        url = url + ("&" if "?" in url else "?") + qs
    body = None
    if json_body is not None:
        body = json.dumps(json_body).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            status = getattr(resp, "status", 0) or getattr(resp, "code", 0)
    except urllib.error.HTTPError as he:
        raw = he.read()
        status = he.code
    text = ""
    data = None
    try:
        text = raw.decode("utf-8", errors="replace")
        data = json.loads(text)
    except Exception:
        data = None
    return _Resp(status, data, {}, text)


def _call(method, url, *, headers=None, params=None, json_body=None,
          token=None, retries=1):
    """带「网络异常重试 retries 次」的调用包装 → _Resp。

    传输层异常重试 1 次（设计 §2.5 体积/错误策略）；失败 raise UploadError（脱敏）。
    HTTP 非 2xx 在此**不**抛，交由 _raise_for 分类处理。
    """
    last = None
    for attempt in range(retries + 1):
        try:
            return _http(method, url, headers=headers, params=params,
                         json_body=json_body)
        except Exception as e:                      # 传输层异常（超时/连接）
            last = e
            if attempt < retries:
                time.sleep(0.5)
                continue
    raise UploadError("网络请求失败: {}".format(accounts._redact(last, token)))


# ══════════════════════════════════════════════════════════════════════
# 工具
# ══════════════════════════════════════════════════════════════════════

def _stage(progress, stage):
    """安全调用进度回调 progress(stage, done, total)。"""
    if not progress:
        return
    try:
        done = _STAGES.index(stage) + 1
    except ValueError:
        done = 0
    try:
        progress(stage, done, len(_STAGES))
    except Exception:
        pass


def _check_cancel(cancel):
    """cancel() 为 True 时抛出取消异常。"""
    try:
        if cancel and cancel():
            raise UploadError("已取消上传")
    except UploadError:
        raise
    except Exception:
        pass


def _b64_file(path):
    """文件内容 → 单行 base64（不含换行）。"""
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("ascii")


def _b64_str(text):
    """字符串 → base64（utf-8 源）。"""
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def _decode_content(d):
    """Contents API 的 content 字段（可能含换行）→ 解码文本；失败返回空串。"""
    c = (d or {}).get("content", "") or ""
    if not c:
        return ""
    try:
        return base64.b64decode(c).decode("utf-8", errors="replace")
    except Exception:
        return ""


def _extract_msg(resp):
    """从响应体抽取可读错误信息（message/error/errors 或原始文本）。"""
    d = getattr(resp, "data", None)
    if isinstance(d, dict):
        m = d.get("message") or d.get("error")
        errs = d.get("errors")
        if errs:
            m = "{} {}".format(m or "", errs)
        if m:
            return str(m)
    return str(getattr(resp, "text", "") or "")


def _raise_for(resp, provider, tok, action):
    """非 2xx → 分类报错（401 重登 / 403,404 仓库权限 / 其它），文本脱敏。"""
    status = getattr(resp, "status", 0)
    if 200 <= status < 300:
        return
    msg = _extract_msg(resp)
    if status == 401:
        hint = "，请重新登录（token 无效或已过期）"
    elif status in (403, 404):
        hint = "，请检查仓库名与访问权限（403 可能为限流）"
    elif status == 422:
        hint = "，参数被拒绝（分支/文件可能已存在）"
    else:
        hint = ""
    raise UploadError("{}失败 (HTTP {}){}: {}".format(
        action, status, hint, accounts._redact(msg, tok)))


def _make_branch(manifest):
    """由 manifest 生成 ASCII 安全的分支名 upload-<id>-<ver>-<ts>。"""
    raw = "upload-{}-{}-{}".format(
        manifest.get("id", "script"), manifest.get("version", "0.0"),
        time.strftime("%Y%m%d%H%M%S"))
    out = []
    for ch in raw:
        out.append(ch if (ch.isalnum() or ch in "-._") else "-")
    return "".join(out)[:60]


# ══════════════════════════════════════════════════════════════════════
# 打包 / 前端校验
# ══════════════════════════════════════════════════════════════════════

def prepare_package(script_path, meta, images=None, out_path=None):
    """打包本地脚本为 .acrpapkg 并校验 manifest。

    = script_package.pack(script_path, meta=meta, images=images, out_path=out_path)
      + validate_manifest。返回 (pkg_path, sha256, manifest)。
    """
    pkg_path, sha256 = script_package.pack(
        script_path, meta=meta, images=images, out_path=out_path)
    manifest = script_package.read_manifest(pkg_path)
    script_package.validate_manifest(manifest)
    return pkg_path, sha256, manifest


def validate_meta(meta, existing_ids=None):
    """上传向导前端预校验（对齐市场 README 自检清单）。

    返回 (errors, warnings):
      * errors  非空 → 拒绝提交（builtin_ 前缀 / category 非法 / 必填缺失 / id 格式）；
      * warnings → 非阻断提示（id 已存在 → 该次上传将更新既有条目）。
    existing_ids: 远端既有脚本 id 集合（用于 id 唯一性提示），可为 None。
    """
    meta = meta or {}
    errors, warnings = [], []

    sid = str(meta.get("id", "") or "").strip()
    if not sid:
        errors.append("缺少必填字段 id")
    elif sid.startswith("builtin_"):
        errors.append("id 不得以 builtin_ 开头: {}".format(sid))
    elif not script_package._ID_RE.match(sid):
        errors.append("id 格式非法 (应为 ^[a-z][a-z0-9_]{{2,49}}$): {}".format(sid))
    elif existing_ids and sid in set(existing_ids):
        warnings.append("id 已存在，上传将更新该条目: {}".format(sid))

    if not str(meta.get("name", "") or "").strip():
        errors.append("缺少必填字段 name")
    if not str(meta.get("description", "") or "").strip():
        errors.append("缺少必填字段 description")
    cat = str(meta.get("category", "") or "").strip()
    if not cat:
        errors.append("缺少必填字段 category")
    elif cat not in script_package.CATEGORIES:
        errors.append("category 非法 (应为 {}): {}".format(
            "/".join(script_package.CATEGORIES), cat))
    if not str(meta.get("author", "") or "").strip():
        errors.append("缺少必填字段 author")
    ver = str(meta.get("version", "") or "").strip()
    if not ver:
        errors.append("缺少必填字段 version")
    elif not script_package._VERSION_RE.match(ver):
        errors.append("version 非法 (应为 MAJOR.MINOR[.PATCH]): {}".format(ver))

    return errors, warnings


def check_script_commands(script_path):
    """读取脚本（.xls/.xlsx/.acrpas）命令列，核验命令均已注册。

    解析统一委托 script_io.iter_commands（唯一实现）。返回未注册命令名列表
    （去重）；无法读取 / 缺依赖 → 返回 []（不阻断上传）。
    """
    try:
        import script_io
        from commands import list_names
    except Exception:
        return []
    if not script_path or not os.path.exists(script_path):
        return []
    try:
        names = script_io.iter_commands(script_path)
    except Exception:
        return []
    known = set(list_names())
    unknown = []
    for name in names:
        if name and name not in known and name not in unknown:
            unknown.append(name)
    return unknown


# ══════════════════════════════════════════════════════════════════════
# index.json 补丁 / PR 正文
# ══════════════════════════════════════════════════════════════════════

def upload_index_patch(index_text, manifest, sha256, size, pkg_rel_path):
    """把/更新一条 v2 条目合并进 index.json 文本，返回新文本。

    同 id → 原地替换（保留 downloads/rating/rating_count 统计）；否则追加。
    空/非法 index_text → 以最小骨架起头。
    """
    data = None
    txt = (index_text or "").strip()
    if txt:
        try:
            data = json.loads(txt)
        except Exception:
            data = None
    if not isinstance(data, dict):
        data = {"name": "ACRPA Script Marketplace", "updated": "", "scripts": []}

    scripts = data.get("scripts")
    if not isinstance(scripts, list):
        scripts = []

    sid = manifest.get("id", "")
    new_entry = {
        "id": sid,
        "name": manifest.get("name", ""),
        "description": manifest.get("description", ""),
        "category": manifest.get("category", ""),
        "author": manifest.get("author", ""),
        "version": manifest.get("version", ""),
        "downloads": 0, "rating": 0.0, "rating_count": 0,
        "tags": list(manifest.get("tags", []) or []),
        "icon": manifest.get("icon", ""),
        "preview": "",
        "requires": list(manifest.get("requires", []) or []),
        "pkg": str(pkg_rel_path).replace("\\", "/"),
        "sha256": sha256,
        "size": int(size or 0),
        "url": "",
        "min_app_version": manifest.get("min_app_version", ""),
    }

    replaced = False
    for i, s in enumerate(scripts):
        if isinstance(s, dict) and s.get("id") == sid:
            for k in ("downloads", "rating", "rating_count", "preview", "filename"):
                if k in s:
                    new_entry[k] = s[k]
            scripts[i] = new_entry
            replaced = True
            break
    if not replaced:
        scripts.append(new_entry)

    data["scripts"] = scripts
    data.setdefault("name", "ACRPA Script Marketplace")
    data["updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return json.dumps(data, ensure_ascii=False, indent=2)


def generate_pr_body(manifest):
    """生成 PR 正文：脚本简介 + 自检清单（对齐市场 README）。"""
    tags = ", ".join(manifest.get("tags", []) or []) or "—"
    req = ", ".join(manifest.get("requires", []) or []) or "无"
    return (
        "## 新增脚本投稿\n\n"
        "**名称**: {name}\n\n"
        "**简介**: {desc}\n\n"
        "**分类**: {cat} ｜ **版本**: {ver} ｜ **作者**: {author}\n\n"
        "**标签**: {tags}\n\n"
        "**依赖**: {req}\n\n"
        "### 自检清单\n"
        "- [x] id 不以 `builtin_` 开头（`{id}`）\n"
        "- [x] category 为四选一（办公/财务/系统/其他）\n"
        "- [x] 脚本文件真实存在且为 .xls\n"
        "- [x] index.json 条目已同步（pkg / sha256 / size）\n"
        "- [x] 脚本内命令均已注册（commands.list_names）\n"
    ).format(name=manifest.get("name", ""), desc=manifest.get("description", ""),
             cat=manifest.get("category", ""), ver=manifest.get("version", ""),
             author=manifest.get("author", ""), tags=tags, req=req,
             id=manifest.get("id", ""))


# ══════════════════════════════════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════════════════════════════════

def _resolve_manifest(pkg_path, meta):
    """以包内 manifest 为准，用 meta 的展示字段覆盖，再校验合法性。"""
    manifest = script_package.read_manifest(pkg_path)
    meta = meta or {}
    for k in ("id", "name", "description", "category", "author", "version",
              "icon", "min_app_version"):
        v = meta.get(k)
        if v not in (None, ""):
            manifest[k] = v
    for k in ("tags", "requires"):
        v = meta.get(k)
        if v not in (None, "", []):
            manifest[k] = v
    errors = script_package.manifest_errors(manifest)
    if errors:
        raise UploadError("manifest 校验失败: " + "; ".join(errors))
    return manifest


def upload(provider, pkg_path, meta, *, owner=None, repo=None,
           base_branch="master", token=None, progress=None, cancel=None):
    """主入口：建分支 → 提交包 → 更新 index.json → 发 PR。

    Args:
        provider    : "gitee" / "github"
        pkg_path    : 本地 .acrpapkg 路径
        meta        : 展示字段 dict（id/name/description/category/author/version/tags/
                      icon/requires/min_app_version）；包内 manifest 为准，meta 覆盖。
        owner/repo  : 目标仓库；缺省取 DEFAULT_REPO[provider]
        base_branch : 目标基线分支（默认 master）
        token       : 显式 token；缺省 accounts.get_token(provider)
        progress    : progress(stage, done, total)
        cancel      : cancel() -> bool，True 时中止

    Returns:
        {"pr_url", "pr_number", "branch", "pkg_path"}

    Raises:
        UploadError（token 缺失 / manifest 非法 / 网络或 API 失败；文本脱敏）
    """
    provider = str(provider or "").strip().lower()
    if provider not in accounts.PROVIDERS:
        raise UploadError("不支持的账号提供商: {}".format(provider))
    if not pkg_path or not os.path.exists(pkg_path):
        raise UploadError("包文件不存在: {}".format(pkg_path))

    manifest = _resolve_manifest(pkg_path, meta)

    # token：显式 > 凭据库；均无 → 明确报错引导登录
    tok = token if token else accounts.get_token(provider)
    tok = str(tok or "").strip()
    if not tok:
        raise UploadError(
            "未配置 {} token，请先在「设置 → 脚本市场账号」或市场窗口登录后再上传".format(provider))

    if not owner or not repo:
        d_owner, d_repo = DEFAULT_REPO.get(provider, ("", ""))
        owner = owner or d_owner
        repo = repo or d_repo
    if not owner or not repo:
        raise UploadError("未指定上传仓库 (owner/repo)")

    pkg_rel = "packages/{}-{}{}".format(
        manifest.get("id", "script"), manifest.get("version", "0.0"),
        script_package.PKG_EXT)
    sha256 = script_package.compute_sha256(pkg_path)
    size = os.path.getsize(pkg_path)
    branch = _make_branch(manifest)

    # 1. 校验登录
    _stage(progress, "verify")
    _check_cancel(cancel)
    try:
        accounts.verify_token(provider, tok)
    except Exception as e:
        raise UploadError("账号校验失败: {}".format(accounts._redact(e, tok)))

    log1("市场上传: provider={} repo={}/{} branch={} pkg={}".format(
        provider, owner, repo, branch, os.path.basename(pkg_path)))

    if provider == "gitee":
        return _gitee_upload(provider, owner, repo, base_branch, branch, tok,
                             pkg_path, pkg_rel, manifest, sha256, size,
                             progress, cancel)
    return _github_upload(provider, owner, repo, base_branch, branch, tok,
                          pkg_path, pkg_rel, manifest, sha256, size,
                          progress, cancel)


def _gitee_upload(provider, owner, repo, base_branch, branch, tok, pkg_path,
                  pkg_rel, manifest, sha256, size, progress, cancel):
    """Gitee：POST /branches → POST /contents/{pkg} → GET/POST /contents/index.json → POST /pulls。"""
    base = "{}/repos/{}/{}".format(_API_GITEE, owner, repo)

    # 2. 建分支
    _stage(progress, "branch")
    _check_cancel(cancel)
    resp = _call("POST", base + "/branches", token=tok, json_body={
        "access_token": tok, "refs": base_branch, "branch_name": branch})
    _raise_for(resp, provider, tok, "创建分支")

    # 3. 提交包
    _stage(progress, "upload_pkg")
    _check_cancel(cancel)
    resp = _call("POST", base + "/contents/" + pkg_rel, token=tok, json_body={
        "access_token": tok, "content": _b64_file(pkg_path),
        "message": "add: {} v{}".format(manifest.get("id"), manifest.get("version")),
        "branch": branch})
    _raise_for(resp, provider, tok, "提交脚本包")

    # 4. 更新 index.json（先取旧 sha）
    _stage(progress, "upload_index")
    _check_cancel(cancel)
    idx_sha, idx_text = _gitee_get_file(base, "index.json", base_branch, tok)
    new_index = upload_index_patch(idx_text, manifest, sha256, size, pkg_rel)
    body = {"access_token": tok, "content": _b64_str(new_index),
            "message": "chore: index update {}".format(manifest.get("id")),
            "branch": branch}
    if idx_sha:
        body["sha"] = idx_sha
    resp = _call("POST", base + "/contents/index.json", token=tok, json_body=body)
    _raise_for(resp, provider, tok, "更新 index.json")

    # 5. 发 PR
    _stage(progress, "pull")
    _check_cancel(cancel)
    resp = _call("POST", base + "/pulls", token=tok, json_body={
        "access_token": tok, "title": "[script] {}".format(manifest.get("name")),
        "head": branch, "base": base_branch, "body": generate_pr_body(manifest)})
    _raise_for(resp, provider, tok, "创建 PR")

    pr = resp.data if isinstance(resp.data, dict) else {}
    _stage(progress, "done")
    return {"pr_url": pr.get("html_url", ""),
            "pr_number": pr.get("number") if pr.get("number") is not None else pr.get("id"),
            "branch": branch, "pkg_path": pkg_path}


def _gitee_get_file(base, path, ref, tok):
    """GFET /contents/{path}?ref=<ref> → (sha, 文本)；404 → ("", "")。"""
    resp = _call("GET", base + "/contents/" + path, token=tok,
                 params={"access_token": tok, "ref": ref})
    if resp.status == 404:
        return "", ""
    _raise_for(resp, "gitee", tok, "读取 " + path)
    d = resp.data if isinstance(resp.data, dict) else {}
    return d.get("sha", ""), _decode_content(d)


def _gh_headers(tok):
    return {"Authorization": "token {}".format(tok),
            "Accept": "application/vnd.github+json",
            "User-Agent": _USER_AGENT}


def _github_upload(provider, owner, repo, base_branch, branch, tok, pkg_path,
                   pkg_rel, manifest, sha256, size, progress, cancel):
    """GitHub：GET ref → POST refs → 提交包(contents 或 Git Data) → PUT index.json → POST pulls。"""
    base = "{}/repos/{}/{}".format(_API_GITHUB, owner, repo)
    hdrs = _gh_headers(tok)
    msg = "add: {} v{}".format(manifest.get("id"), manifest.get("version"))

    # 2. 取基点 + 建分支
    _stage(progress, "branch")
    _check_cancel(cancel)
    resp = _call("GET", base + "/git/ref/heads/" + base_branch,
                 headers=hdrs, token=tok)
    _raise_for(resp, provider, tok, "读取基点")
    base_sha = ((resp.data or {}).get("object") or {}).get("sha", "")

    resp = _call("POST", base + "/git/refs", headers=hdrs, token=tok,
                 json_body={"ref": "refs/heads/" + branch, "sha": base_sha})
    _raise_for(resp, provider, tok, "创建分支")

    # 先取 index.json 旧 sha（更新需要）
    idx_sha, idx_text = _github_get_file(base, "index.json", base_branch, hdrs, tok)

    # 3. 提交包：≤1MB 走 contents；>1MB 走 Git Data API
    _stage(progress, "upload_pkg")
    _check_cancel(cancel)
    if size <= _ONE_MB:
        resp = _call("PUT", base + "/contents/" + pkg_rel, headers=hdrs, token=tok,
                     json_body={"message": msg, "content": _b64_file(pkg_path),
                                "branch": branch})
        _raise_for(resp, provider, tok, "提交脚本包")
    else:
        resp = _call("POST", base + "/git/blobs", headers=hdrs, token=tok,
                     json_body={"content": _b64_file(pkg_path), "encoding": "base64"})
        _raise_for(resp, provider, tok, "上传 blob")
        blob_sha = (resp.data or {}).get("sha", "")

        resp = _call("POST", base + "/git/trees", headers=hdrs, token=tok,
                     json_body={"tree": [{"path": pkg_rel, "mode": "100644",
                                          "type": "blob", "sha": blob_sha}]})
        _raise_for(resp, provider, tok, "创建 tree")
        tree_sha = (resp.data or {}).get("sha", "")

        resp = _call("POST", base + "/git/commits", headers=hdrs, token=tok,
                     json_body={"message": msg, "tree": tree_sha,
                                "parents": [base_sha]})
        _raise_for(resp, provider, tok, "创建 commit")
        commit_sha = (resp.data or {}).get("sha", "")

        resp = _call("PATCH", base + "/git/refs/heads/" + branch, headers=hdrs,
                     token=tok, json_body={"sha": commit_sha})
        _raise_for(resp, provider, tok, "更新分支引用")

    # 4. 更新 index.json
    _stage(progress, "upload_index")
    _check_cancel(cancel)
    new_index = upload_index_patch(idx_text, manifest, sha256, size, pkg_rel)
    body = {"message": "chore: index update {}".format(manifest.get("id")),
            "content": _b64_str(new_index), "branch": branch}
    if idx_sha:
        body["sha"] = idx_sha
    resp = _call("PUT", base + "/contents/index.json", headers=hdrs, token=tok,
                 json_body=body)
    _raise_for(resp, provider, tok, "更新 index.json")

    # 5. 发 PR
    _stage(progress, "pull")
    _check_cancel(cancel)
    resp = _call("POST", base + "/pulls", headers=hdrs, token=tok, json_body={
        "title": "[script] {}".format(manifest.get("name")),
        "head": branch, "base": base_branch, "body": generate_pr_body(manifest)})
    _raise_for(resp, provider, tok, "创建 PR")

    pr = resp.data if isinstance(resp.data, dict) else {}
    _stage(progress, "done")
    return {"pr_url": pr.get("html_url", ""), "pr_number": pr.get("number"),
            "branch": branch, "pkg_path": pkg_path}


def _github_get_file(base, path, ref, hdrs, tok):
    """GET /contents/{path}?ref=<ref> → (sha, 文本)；404 → ("", "")。"""
    resp = _call("GET", base + "/contents/" + path, headers=hdrs, token=tok,
                 params={"ref": ref})
    if resp.status == 404:
        return "", ""
    _raise_for(resp, "github", tok, "读取 " + path)
    d = resp.data if isinstance(resp.data, dict) else {}
    return d.get("sha", ""), _decode_content(d)


# 语义别名（设计 §2.5）
upload_package = upload


__all__ = [
    "UploadError", "DEFAULT_REPO", "prepare_package", "upload_index_patch",
    "generate_pr_body", "validate_meta", "check_script_commands",
    "upload", "upload_package",
]
