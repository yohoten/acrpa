"""ACRPA 更新检查 / 直链下载 / 便携版自更新 (零新增第三方依赖)。

与旧实现的差异 (逐条对应旧版的真实缺陷):

1. 多源降级 —— 旧版只有一个 Gitee raw 地址, 一旦该地址不可达就把异常吞成
   「无更新」。现按 GitHub Releases API → raw 清单镜像 (raw.githubusercontent
   → jsDelivr → Gitee) 依次尝试, 并记录实际生效的来源。
2. 版本比较 —— 旧版用 `int(x) for x in v.split(".")`, 遇到 "0.1.26-beta" 会抛
   异常并静默判定为无更新, 且 (0,1) 与 (0,1,0) 被判为不同版本。现支持 v 前缀、
   位数不齐、预发布后缀。
3. 失败可感知 —— 新增 check() 返回结构化 status, 区分
   ok / no_update / network_error / bad_manifest, 不再把网络故障伪装成「已是最新」。
4. 直链自动推导 —— 清单第二行留空时按 Release 约定生成直链, 发版只需改版本号。
   下载时按候选列表逐个尝试, 任一成功即止。
5. 完整性 —— 流式下载 + Content-Length 比对 + 容器魔术字节校验 (拒绝 HTML 错误页
   与半截包) + 可选 sha256 校验。
6. 自更新 —— 暂存到 <app_dir>/updates, 由独立 PowerShell 助手等待本进程退出后
   原子替换主程序并重启; 只替换主程序与 VERSION, 绝不触碰 config.json / logs /
   screenshots 等用户数据。
"""
import hashlib
import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import threading
import urllib.error
import urllib.request

import requests

from version_info import (
    VERSION,
    RELEASE_ASSET,
    get_download_url,
    get_download_urls,
    get_sha256,
    get_app_dir,
    release_asset_url,
    release_asset_urls,
    raw_mirror_urls,
)

_TIMEOUT = 8
_CHUNK = 64 * 1024
_USER_AGENT = "ACRPA-Updater/{}".format(VERSION)

# ── 清单来源: GitHub Releases API (首选) ──
# 优点是发版时只需在网页建 Release, 无需改任何文件; 直链与体积直接来自 API, 且无 CDN 缓存。
_RELEASE_API_SOURCES = (
    "https://api.github.com/repos/yohoten/acrpa/releases/latest",
)

# ── 清单来源: VERSION 纯文本镜像 (回退) ──
# 顺序按【实测可达性】而非"名义权威性"排:
#   · api.github.com / raw.githubusercontent.com 在部分网络 (尤其国内) 会被 TLS 阻断,
#     实测直接抛 SSLError, 因此放在能用的镜像之后;
#   · jsDelivr 镜像 GitHub main 分支且国内有节点, 实测 200, 作为第一回退;
#   · Gitee raw 实测可用但仓库另一条历史可能落后, 作为最后兜底 (只会少报, 不会误报)。
_RAW_MANIFEST_SOURCES = (
    "https://cdn.jsdelivr.net/gh/yohoten/acrpa@main/VERSION",
    "https://raw.githubusercontent.com/yohoten/acrpa/main/VERSION",
    "https://gitee.com/yohoten/acrpa/raw/master/VERSION",
)

# 兼容旧调用方 / 旧文档中的常量名
UPDATE_URL = _RAW_MANIFEST_SOURCES[0]

# 下载包合法性: ZIP 或 PE 可执行文件
_MAGIC = (b"PK\x03\x04", b"PK\x05\x06", b"MZ")

# 会话缓存: None=未检查, False=无更新, (ver, url)=有更新
_result = None
_status = None
_checking = False
_pending_done = []      # 检查进行中被追加进来的回调, 完成后统一通知
_lock = threading.Lock()


# ══════════════════════════════════════════════════════════════════════
# 版本比较
# ══════════════════════════════════════════════════════════════════════

def _parse_version(text):
    """解析版本号 → (数值元组, 是否预发布, 预发布键)；无法解析返回 None。"""
    raw = str(text or "").strip().lstrip("vV")
    if not raw:
        return None

    core, sep, pre = raw.partition("-")
    if not sep:
        # 兼容 0.1.26beta / 0.1.26_rc1 等写法
        i = 0
        while i < len(core) and (core[i].isdigit() or core[i] == "."):
            i += 1
        core, pre = core[:i], core[i:].lstrip("._-")
    else:
        pre = pre.lstrip("._-")

    parts = [p for p in core.split(".") if p != ""]
    if not parts:
        return None
    try:
        nums = tuple(int(p) for p in parts)
    except ValueError:
        return None

    # 预发布键: 数字段按数值比较, 字母段按字典序; 打标签避免混合类型比较报错
    key = tuple((0, int(p)) if p.isdigit() else (1, p.lower())
                for p in pre.replace("-", ".").replace("_", ".").split(".")
                if p)
    return nums, bool(pre), key


def compare_versions(current, latest):
    """比较两个版本号 → -1 (current 更旧) / 0 (相同) / 1 (current 更新)。

    无法解析任一侧时返回 0, 由调用方决定如何处理 (不擅自判定为新版本)。
    """
    a = _parse_version(current)
    b = _parse_version(latest)
    if a is None or b is None:
        return 0

    na, pb_a, key_a = a
    nb, pb_b, key_b = b
    width = max(len(na), len(nb))
    na = na + (0,) * (width - len(na))
    nb = nb + (0,) * (width - len(nb))

    if na != nb:
        return -1 if na < nb else 1
    # 数值相同: 正式版 > 预发布版 (1.0.0 > 1.0.0-rc1)
    if pb_a != pb_b:
        return 1 if pb_b else -1
    if key_a != key_b:
        return -1 if key_a < key_b else 1
    return 0


def _compare_versions(current, latest):
    """保留旧函数名与语义: latest > current 时返回 True。"""
    return compare_versions(current, latest) < 0


# ══════════════════════════════════════════════════════════════════════
# 清单解析
# ══════════════════════════════════════════════════════════════════════

def _http_get(url, timeout=_TIMEOUT):
    """GET 请求 → (ok, text, error)。

    双栈传输: 先 requests, 失败再用 urllib 重试。
    必要性 (实测, 非理论): 在启用 TLS 拦截的网络 (企业代理、安全软件) 中, 代理
    自签根证书装在 Windows 系统证书库, 而 requests 自带的是 certifi 包 —— 于是
    requests 对 api.github.com / raw.githubusercontent.com 抛
    SSLCertVerificationError, urllib (走系统证书库) 却返回 200。只用 requests
    的后果是: 恰好在最依赖内网的企业环境里, 两个【权威且无缓存】的来源永久不可达,
    只剩会缓存旧版本的 jsDelivr 可用 —— 表现为"永远提示已是最新", 且毫无错误提示。
    """
    headers = {"User-Agent": _USER_AGENT,
               "Accept": "application/json, text/plain, */*"}
    try:
        resp = requests.get(url, timeout=timeout, headers=headers)
        if resp.status_code != 200:
            # 服务端给出了明确应答 (404/403 等), 换传输栈重试没有意义
            return False, "", "HTTP {}".format(resp.status_code)
        return True, resp.text, ""
    except Exception as e:
        first = "{}: {}".format(type(e).__name__, e)

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            code = getattr(resp, "status", 200) or 200
            if code != 200:
                return False, "", "HTTP {}".format(code)
            return True, resp.read().decode("utf-8", "replace"), ""
    except Exception as e:
        return False, "", "{} | urllib {}: {}".format(first, type(e).__name__, e)


# ── 流式下载的双栈适配 ──
# requests 与 urllib 的响应对象接口不同, 这里统一成 read(n) + 上下文管理,
# 使下载主循环无需关心底层用的是哪一套 (以及是否能通过代理的自签证书)。

class _HttpStatus(Exception):
    """服务端给出了明确的非 200 —— 换传输栈重试没有意义, 只需记录状态码。"""

    def __init__(self, code):
        super(_HttpStatus, self).__init__("HTTP {}".format(code))
        self.code = code


class _RequestsStream(object):
    """把 requests 的流式响应适配成 urllib 风格 (read(n) + with)。"""

    def __init__(self, resp):
        self._resp = resp
        self._iter = resp.iter_content(_CHUNK)

    def read(self, n=-1):
        for chunk in self._iter:        # iter_content 对 keep-alive 会吐空块
            if chunk:
                return chunk
        return b""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        try:
            self._resp.close()
        except Exception:
            pass
        return False


def _open_requests_stream(url, timeout, headers):
    """requests 流式打开 → (handle, 声明体积)。"""
    resp = requests.get(url, timeout=timeout, stream=True, headers=headers)
    if resp.status_code != 200:
        raise _HttpStatus(resp.status_code)
    return _RequestsStream(resp), int(resp.headers.get("Content-Length") or 0)


def _open_urllib_stream(url, timeout, headers):
    """urllib 流式打开 (系统证书库, 能通过企业代理的自签证书)。"""
    req = urllib.request.Request(url, headers=headers)
    resp = urllib.request.urlopen(req, timeout=timeout)
    code = getattr(resp, "status", 200) or 200
    if code != 200:
        resp.close()
        raise _HttpStatus(code)
    return resp, int(resp.headers.get("Content-Length") or 0)


def _parse_release_api(payload, source):
    """解析 GitHub Releases API 响应 → info dict；失败返回 (None, 原因)。"""
    try:
        data = json.loads(payload)
    except Exception as e:
        return None, "JSON 解析失败: {}".format(e)
    if not isinstance(data, dict) or not data.get("tag_name"):
        return None, "响应缺少 tag_name"

    version = str(data["tag_name"]).strip().lstrip("vV")
    url, size = "", 0
    for asset in data.get("assets") or []:
        if asset.get("name") == RELEASE_ASSET:
            url = asset.get("browser_download_url", "")
            size = int(asset.get("size") or 0)
            break
    if not url:
        for asset in data.get("assets") or []:
            if str(asset.get("name", "")).lower().endswith(".zip"):
                url = asset.get("browser_download_url", "")
                size = int(asset.get("size") or 0)
                break

    return {
        "latest": version,
        "download_url": url,
        "size": size,
        "notes_url": data.get("html_url", ""),
        "notes": data.get("body", "") or "",
        "sha256": "",           # Release API 不提供校验和, 依赖清单行补充
        "source": source,
    }, ""


def _parse_manifest(text, source):
    """解析 VERSION 纯文本清单 → info dict；失败返回 (None, 原因)。"""
    lines = [l.strip() for l in (text or "").splitlines()
             if l.strip() and not l.strip().startswith("#")]
    if not lines:
        return None, "清单为空"
    if _parse_version(lines[0]) is None:
        return None, "首行不是合法版本号: {!r}".format(lines[0][:40])

    info = {
        "latest": lines[0].lstrip("vV"),
        "download_url": "",
        "download_urls": [],
        "size": 0,
        "notes_url": "",
        "notes": "",
        "sha256": "",
        "source": source,
    }
    for line in lines[1:]:
        if line.lower().startswith("sha256"):
            info["sha256"] = line.split(":", 1)[-1].split("=", 1)[-1].strip().lower()
        elif line.startswith("http://") or line.startswith("https://"):
            # 允许清单声明多条直链 (首个为 GitHub Release, 其后为国内镜像),
            # 下载时按声明顺序逐个尝试, 任一成功即止。
            info["download_urls"].append(line)
        elif line.lower().startswith("notes:"):
            info["notes_url"] = line.split(":", 1)[1].strip()
    if info["download_urls"]:
        info["download_url"] = info["download_urls"][0]
    return info, ""


def _empty_result(status="network_error", error="", source=""):
    return {
        "status": status,
        "has_update": False,
        "current": VERSION,
        "latest": "",
        "download_url": "",
        "sha256": "",
        "size": 0,
        "notes_url": "",
        "notes": "",
        "source": source,
        "error": error,
    }


# ══════════════════════════════════════════════════════════════════════
# 检查更新
# ══════════════════════════════════════════════════════════════════════

def _probe_sources(sources, parser, current, timeout, errors):
    """按顺序探测来源, 返回最可信的一份清单 info (或 None)。

    这里刻意不做"首个成功即返回": 各镜像 (尤其 jsDelivr 的分支别名与 Gitee
    另一条历史分支) 会缓存旧版本。若首个成功的来源恰好落后, 真实存在的新版本
    就会被静默吞成"已是最新" —— 这是用户完全无从察觉的失败模式。因此:
      · 某个来源一旦报出"比本机新"的版本, 立即采纳并停止 (无需再看别的来源);
      · 若报出的都不比本机新, 则把所有来源探完, 取其中版本最高的一份。
    额外代价只落在"确实没有更新"这一支上, 且对象是几十字节的纯文本清单。
    """
    best = None
    for source in sources:
        ok, text, err = _http_get(source, timeout)
        if not ok:
            errors.append("{} → {}".format(source, err))
            continue
        info, why = parser(text, source)
        if info is None:
            errors.append("{} → {}".format(source, why))
            continue
        if compare_versions(current, info["latest"]) < 0:
            return info                     # 已确认有新版本, 早退
        if best is None or compare_versions(best["latest"], info["latest"]) < 0:
            best = info                     # 不比本机新, 先记下版本最高的一份
    return best


def check(timeout=_TIMEOUT):
    """检查更新 → 结构化结果 dict (status: ok/no_update/network_error/bad_manifest)。"""
    errors = []
    current = VERSION

    info = _probe_sources(_RELEASE_API_SOURCES, _parse_release_api,
                          current, timeout, errors)

    # Release API 是最权威来源, 但两种情况都真实存在: 清单已先行而 Release 尚未
    # 发布 (发版流程允许先改版本号再传包), 或 Release 已发布而镜像还落后。因此
    # 只要尚未确认"有新版本", 就把清单镜像也探一遍, 取版本更高的一方 —— 宁可多问
    # 一句, 也不能让落后的一方决定"没有更新"。
    if info is None or compare_versions(current, info["latest"]) >= 0:
        raw = _probe_sources(_RAW_MANIFEST_SOURCES, _parse_manifest,
                             current, timeout, errors)
        if raw is not None and (info is None or
                                compare_versions(info["latest"], raw["latest"]) < 0):
            info = raw

    if info is None:
        return _empty_result("network_error", " | ".join(errors[:3]))

    # 本地 VERSION 若显式配置了校验和, 即使来源是 Release API 也采纳
    if not info.get("sha256"):
        info["sha256"] = get_sha256()

    result = _empty_result("no_update", "", info["source"])
    result.update({
        "latest": info["latest"],
        "download_url": info["download_url"],
        "download_urls": list(info.get("download_urls") or []),
        "sha256": info["sha256"],
        "size": info["size"],
        "notes_url": info["notes_url"],
        "notes": info["notes"],
    })
    if compare_versions(VERSION, info["latest"]) < 0:
        result["status"] = "ok"
        result["has_update"] = True
    return result


def get_status():
    """返回最近一次 check 的结果 (None 表示尚未检查)。"""
    return _status


def get_result():
    """向后兼容旧接口: None / False / (version, url)。"""
    return _result


def check_async(callback=None, on_done=None, timeout=_TIMEOUT, force=False):
    """后台检查更新。

    callback(version, url)  仅在发现新版本时调用 (签名与旧版兼容)
    on_done(result)         无论结果如何都会调用一次, 便于 GUI 提示网络故障
    force=True              忽略既有结论强制重新请求

    多路复用: 已有检查在飞时把回调挂到队列, 一并复用本次结果 ——
    否则第二个调用方 (例如用户在检查进行中打开更新窗口) 会永远收不到回调。
    """
    global _checking
    with _lock:
        if _checking:
            _pending_done.append((on_done, callback))
            return
        if _status is not None and not force:
            res = _status
        else:
            res = None
            _checking = True

    if res is not None:
        _notify(res, on_done, callback)
        return

    def _run():
        global _checking, _result, _status
        try:
            res = check(timeout=timeout)
        except Exception as e:          # 兜底: 检查线程绝不允许把异常抛到调用方
            res = _empty_result("network_error", repr(e))

        _status = res
        _result = ((res["latest"], resolve_download_url(res))
                   if res.get("has_update") else False)

        with _lock:
            waiters = list(_pending_done)
            del _pending_done[:]
            _checking = False

        _notify(res, on_done, callback)
        for extra_done, extra_cb in waiters:
            _notify(res, extra_done, extra_cb)

    threading.Thread(target=_run, daemon=True, name="acrpa-update-check").start()


def _notify(res, on_done, callback):
    """安全回调: 单个回调异常不得影响其它回调与上层流程。"""
    if on_done:
        try:
            on_done(res)
        except Exception:
            pass
    if callback and res.get("has_update"):
        try:
            callback(res["latest"], resolve_download_url(res))
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════════════
# 下载直链
# ══════════════════════════════════════════════════════════════════════

def _url_matches_version(url, version):
    """判断直链是否指向指定版本。

    必要性: 本地 VERSION 文件第二行描述的是【当前已安装版本】的下载地址。若把它
    当作升级来源直接用, 用户在 0.1.26 收到 0.1.27 的更新提示后会重新下载 0.1.26,
    而且"下载成功" —— 静默降级/无效更新。因此本地清单直链只在版本号吻合时采纳。
    """
    if not url or not version:
        return True
    v = str(version).strip().lstrip("vV")
    if not v:
        return True
    return re.search(r"(?<![\d.])" + re.escape(v) + r"(?![\d])", url) is not None


def _acceptable_local_urls(version):
    """本地 VERSION 声明的直链中, 版本号与升级目标一致的那些。

    版本不吻合的一律丢弃 —— 见 _url_matches_version 的说明。
    """
    return [u for u in get_download_urls()
            if _url_matches_version(u, version)]


def resolve_download_url(info):
    """取首选直链: 远端清单声明 > 版本吻合的本地覆盖 > Release 约定推导。"""
    version = (info or {}).get("latest") or ""
    url = (info or {}).get("download_url") or ""
    if url:
        return url
    local = _acceptable_local_urls(version)
    if local:
        return local[0]
    return release_asset_url(version) if version else ""


def candidate_download_urls(info):
    """按优先级返回候选直链列表 (逐个尝试, 任一成功即止)。

    顺序: 远端清单声明的直链 (GitHub Release → 国内镜像) → 版本吻合的本地声明
    → Release 约定推导 → 仓库内 dist/ 副本镜像。

    最后一条 (dist/ 副本) 是唯一【不带版本信息】的通道: 它永远返回"仓库里最后
    提交的那份包", 且历史发布包可能连包内 VERSION 都没有 (实测 dist/ACRPA.zip
    即为此例), 于是"包内 VERSION 复核"也无从判定。此时若仍去用它, 用户在
    Release 尚未发布时会静默装上旧版 —— 表现为"更新成功"却版本没变, 比明确
    报错糟糕得多。因此: 没有可用的 sha256 时, 直接放弃这条通道, 让上层给出
    "请手动下载"的明确提示。
    """
    version = (info or {}).get("latest") or ""
    urls = list((info or {}).get("download_urls") or [])
    if (info or {}).get("download_url"):
        urls.append(info["download_url"])
    urls.extend(_acceptable_local_urls(version))
    if version:
        # 两种 tag 写法 (v0.1.26 / v0.1.26.0) 都作为候选, 避免因 tag 约定不一致 404
        urls.extend(release_asset_urls(version))
    if (info or {}).get("sha256") or get_sha256():
        urls.extend(raw_mirror_urls())

    unique = []
    for u in urls:
        if u and u not in unique:
            unique.append(u)
    return unique


# ══════════════════════════════════════════════════════════════════════
# 下载与校验
# ══════════════════════════════════════════════════════════════════════

def format_size(num):
    """字节数 → 人类可读字符串。"""
    try:
        n = float(num)
    except Exception:
        return "未知"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return "{:.1f} {}".format(n, unit)
        n /= 1024.0


def _sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _verify_file(path, expect_sha256="", expect_size=0):
    """校验下载产物 → (ok, 原因)。拒绝空包 / HTML 错误页 / 校验和不符。"""
    if not os.path.exists(path):
        return False, "文件不存在"
    actual = os.path.getsize(path)
    if actual == 0:
        return False, "下载内容为空"
    if expect_size and actual != expect_size:
        return False, "体积不符: 期望 {} 实际 {}".format(
            format_size(expect_size), format_size(actual))

    with open(path, "rb") as f:
        head = f.read(4)
    if not any(head.startswith(m) for m in _MAGIC):
        return False, "不是有效的安装包 (疑似错误页或半截文件)"

    if expect_sha256:
        got = _sha256_of(path)
        if got.lower() != expect_sha256.lower():
            return False, "sha256 校验失败 (期望 {}... 实际 {}...)".format(
                expect_sha256[:12], got[:12])
    return True, ""


def download_package(info, dest_dir=None, progress=None, cancel=None,
                     timeout=30, filename=None):
    """下载更新包 → (ok, 路径或失败原因)。

    progress(downloaded, total)  下载进度回调 (total 可能为 0)
    cancel()                     返回 True 时中止下载
    """
    target_dir = dest_dir or os.path.join(get_app_dir(), "updates")
    try:
        os.makedirs(target_dir, exist_ok=True)
    except Exception as e:
        return False, "无法创建下载目录: {}".format(e)

    version = (info or {}).get("latest") or VERSION
    name = filename or "ACRPA_v{}.zip".format(version)
    dest = os.path.join(target_dir, name)
    part = dest + ".part"

    urls = candidate_download_urls(info)
    if not urls:
        return False, "没有可用的下载地址"

    errors = []
    for url in urls:
        ok, msg = _download_one(url, part, progress, cancel, timeout,
                                (info or {}).get("sha256", ""),
                                (info or {}).get("size", 0))
        if ok:
            # 包内版本校验: 不通过则视为该源失败并继续尝试下一个源
            vok, vmsg = _verify_package_version(part, version if filename is None else "")
            if not vok:
                errors.append("{} → {}".format(url.split("/")[-1] or url, vmsg))
                try:
                    os.remove(part)
                except Exception:
                    pass
                continue
            try:
                os.replace(part, dest)
            except Exception as e:
                return False, "落盘失败: {}".format(e)
            return True, dest
        errors.append("{} → {}".format(url.split("/")[-1] or url, msg))
        if cancel and cancel():
            break
        try:
            if os.path.exists(part):
                os.remove(part)
        except Exception:
            pass

    return False, " | ".join(errors[:3])


def _download_one(url, part_path, progress, cancel, timeout,
                  expect_sha256, expect_size):
    """单源流式下载 + 校验 → (ok, 原因)。

    传输同样双栈: requests 因证书问题失败时改用 urllib 再试一次; 若服务端明确
    应答非 200, 则不再换栈 (换栈解决不了 404)。
    """
    headers = {"User-Agent": _USER_AGENT}
    total = 0
    errors = []

    for label, opener in (("requests", _open_requests_stream),
                          ("urllib", _open_urllib_stream)):
        try:
            handle, total = opener(url, timeout, headers)
        except _HttpStatus as e:
            return False, str(e)        # 明确状态码, 换栈无意义
        except Exception as e:
            errors.append("{} {}".format(label, type(e).__name__))
            continue

        try:
            got = 0
            with handle, open(part_path, "wb") as f:
                while True:
                    if cancel and cancel():
                        return False, "已取消"
                    chunk = handle.read(_CHUNK)
                    if not chunk:
                        break
                    f.write(chunk)
                    got += len(chunk)
                    if progress:
                        try:
                            progress(got, total)
                        except Exception:
                            pass
        except Exception as e:
            errors.append("{} {}: {}".format(label, type(e).__name__, e))
            try:
                if os.path.exists(part_path):
                    os.remove(part_path)
            except Exception:
                pass
            continue

        return _verify_file(part_path, expect_sha256, expect_size or total)

    return False, " | ".join(errors[:2]) if errors else "无法建立连接"


# ══════════════════════════════════════════════════════════════════════
# 便携版自更新
# ══════════════════════════════════════════════════════════════════════

# 自更新助手脚本 (纯 ASCII, 全部路径经环境变量注入, 规避批处理中文编码问题)
_APPLY_PS1 = r"""# ACRPA 自更新助手 — 由主程序生成, 运行完自动删除
$ErrorActionPreference = 'Stop'
$zip     = $env:ACRPA_ZIP
$stage   = $env:ACRPA_STAGE
$target  = $env:ACRPA_TARGET
$exeName = $env:ACRPA_EXENAME
$expect  = $env:ACRPA_EXPECTVER
$logPath = $env:ACRPA_LOG
$waitPid = 0
[void][int]::TryParse($env:ACRPA_PID, [ref]$waitPid)

function Write-Log([string]$m) {
    try { "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $m" | Out-File -FilePath $logPath -Append -Encoding UTF8 } catch {}
}

try {
    Write-Log "apply_update: waiting for pid $waitPid to exit"
    $deadline = (Get-Date).AddSeconds(180)
    while ((Get-Date) -lt $deadline) {
        if (-not (Get-Process -Id $waitPid -ErrorAction SilentlyContinue)) { break }
        Start-Sleep -Milliseconds 500
    }

    if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
    New-Item -ItemType Directory -Path $stage -Force | Out-Null
    Write-Log "extracting $zip"
    [System.IO.Compression.ZipFile]::ExtractToDirectory($zip, $stage)

    $exe = Get-ChildItem -LiteralPath $stage -Recurse -File -Filter '*.exe' |
           Where-Object { $_.Length -gt 1MB } |
           Sort-Object Length -Descending | Select-Object -First 1
    if (-not $exe) { throw 'package contains no executable over 1MB' }
    $root = $exe.Directory.FullName
    Write-Log "package root: $root"

    if ($expect) {
        $vf = Join-Path $root 'VERSION'
        if (Test-Path -LiteralPath $vf) {
            $got = (Get-Content -LiteralPath $vf -TotalCount 1).Trim()
            if ($got -ne $expect) { throw "version mismatch: package=$got expected=$expect" }
            Write-Log "version verified: $got"
        } else {
            # 包内未携带 VERSION: 不阻断 (下载阶段已完成 sha256/体积校验),
            # 但留痕便于事后定位; 发布包建议由 tools/make_release.py 生成。
            Write-Log "WARN: package has no VERSION, version check skipped"
        }
    }

    # 先落到 .new 再原子替换, 避免半截文件顶掉可运行的主程序
    $dest = Join-Path $target $exeName
    $tmp  = Join-Path $target ($exeName + '.new')
    Copy-Item -LiteralPath $exe.FullName -Destination $tmp -Force
    if ((Get-Item -LiteralPath $tmp).Length -lt 1MB) { throw 'staged exe too small' }
    Move-Item -LiteralPath $tmp -Destination $dest -Force
    Write-Log "main program replaced"

    # 只同步 VERSION, 其余资源 (模板/说明) 请下载完整包, 避免误删用户数据
    $src_ver = Join-Path $root 'VERSION'
    if (Test-Path -LiteralPath $src_ver) {
        Copy-Item -LiteralPath $src_ver -Destination (Join-Path $target 'VERSION') -Force
        Write-Log "VERSION synced"
    }

    Start-Process -FilePath $dest
    Write-Log "restarted: $dest"
    Remove-Item -LiteralPath $zip -Force -ErrorAction SilentlyContinue
} catch {
    Write-Log ("FAILED: " + $_.Exception.Message)
} finally {
    Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $MyInvocation.MyCommand.Path -Force -ErrorAction SilentlyContinue
}
"""


def can_self_update():
    """自更新仅对打包后的便携版 EXE 有意义 → (bool, 原因)。"""
    if not getattr(sys, "frozen", False):
        return False, "当前为源码运行模式, 自更新仅在打包后的 EXE 中可用"
    exe = os.path.abspath(sys.executable)
    if not os.path.exists(exe):
        return False, "找不到当前可执行文件"
    if not os.access(os.path.dirname(exe), os.W_OK):
        return False, "程序目录不可写 (可能安装在受保护路径), 请手动替换"
    return True, ""


def apply_update(zip_path, restart=True):
    """启动自更新助手: 等待本进程退出 → 替换主程序与 VERSION → 重启。

    调用方在收到 (True, _) 后应尽快退出进程, 否则助手会等待到超时。
    """
    ok, why = can_self_update()
    if not ok:
        return False, why
    if not os.path.exists(zip_path):
        return False, "更新包不存在: {}".format(zip_path)

    app_dir = get_app_dir()
    updates_dir = os.path.join(app_dir, "updates")
    try:
        os.makedirs(updates_dir, exist_ok=True)
    except Exception as e:
        return False, "无法创建更新目录: {}".format(e)

    ps1 = os.path.join(updates_dir, "apply_update.ps1")
    try:
        with open(ps1, "w", encoding="utf-8") as f:
            f.write(_APPLY_PS1)
    except Exception as e:
        return False, "无法写入更新助手: {}".format(e)

    env = dict(os.environ)
    env.update({
        "ACRPA_ZIP": zip_path,
        "ACRPA_STAGE": os.path.join(updates_dir, "stage"),
        "ACRPA_TARGET": app_dir,
        "ACRPA_EXENAME": os.path.basename(sys.executable),
        "ACRPA_EXPECTVER": (get_expected_version(zip_path) or ""),
        "ACRPA_PID": str(os.getpid()),
        "ACRPA_LOG": os.path.join(updates_dir, "apply_update.log"),
    })

    flags = 0
    detached = getattr(subprocess, "DETACHED_PROCESS", 0)
    new_group = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    if detached or new_group:
        flags = detached | new_group

    try:
        subprocess.Popen(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", ps1],
            env=env, close_fds=True, creationflags=flags,
            cwd=updates_dir,
        )
    except Exception as e:
        return False, "无法启动更新助手: {}".format(e)

    return True, ""


def get_expected_version(zip_path):
    """离线读取包内 VERSION 首行 (仅取 zip 根或一级子目录下的 VERSION)。"""
    try:
        import zipfile
        with zipfile.ZipFile(zip_path) as z:
            for name in z.namelist():
                if name.upper() == "VERSION" or name.upper().endswith("/VERSION"):
                    if name.count("/") <= 1:
                        with z.open(name) as f:
                            return f.readline().decode("utf-8", "replace").strip()
    except Exception:
        pass
    return ""


def _verify_package_version(path, expected):
    """包内 VERSION 与目标版本不一致时拒绝 → (ok, 原因)。

    必要场景: 兜底镜像 (仓库内 dist/ACRPA.zip) 不带版本号, 永远返回"最后提交的
    那份包"。如果发版时忘了同步更新它, 用户会被提示升级到 0.1.27、拿到手的却是
    0.1.26 的包, 而且"下载成功"。包内 VERSION 是唯一能离线判定版本的东西, 因此
    由 tools/make_release.py 强制放入包根。
    """
    if not expected:
        return True, ""
    got = get_expected_version(path)
    if not got:
        # 包内无版本信息 (旧版发布包): 无法判定, 放行但由调用方留痕
        return True, ""
    if compare_versions(got, expected) != 0:
        return False, "包内版本不符: 包内={} 目标={}".format(got, expected)
    return True, ""


def read_log(updates_dir=None):
    """读取自更新日志尾部 (供 GUI 展示失败原因)。"""
    path = os.path.join(updates_dir or os.path.join(get_app_dir(), "updates"),
                        "apply_update.log")
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()[-2000:]
    except Exception:
        return ""


def cleanup_updates(keep_latest=True):
    """清理 updates 目录中的旧包与残留 .part 文件。"""
    updates_dir = os.path.join(get_app_dir(), "updates")
    if not os.path.isdir(updates_dir):
        return 0
    removed = 0
    zips = []
    try:
        for name in os.listdir(updates_dir):
            path = os.path.join(updates_dir, name)
            if name.endswith(".part") or name == "stage":
                if os.path.isdir(path):
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    os.remove(path)
                removed += 1
            elif name.lower().endswith(".zip"):
                zips.append(path)
        if keep_latest and len(zips) > 1:
            zips.sort(key=lambda p: os.path.getmtime(p), reverse=True)
            for path in zips[1:]:
                os.remove(path)
                removed += 1
    except Exception:
        pass
    return removed
