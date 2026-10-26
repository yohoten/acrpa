# -*- coding: utf-8 -*-
"""net_ops — 网络/数据类动作的纯逻辑实现（L2 动作库扩充：HTTP 请求 / JSON 取值 /
Base64 编解码 / URL 编解码 / 哈希值）。

设计约束（务必保持）
--------------------
* **纯 stdlib**: ``urllib.request`` / ``json`` / ``base64`` / ``hashlib`` /
  ``urllib.parse``；不引入 ``requests``（见下方「为何用 urllib 而不是 requests」）；
* **import 期零副作用**: 不建连接、不读配置、不加载 GUI；
* **不抛异常**: 统一返回 ``(ok, message, value)``，解析失败给出中文可读错误；
* **安全底线（对齐方案文档 §8.6）**:
    - 只允许 ``http://`` / ``https://``（拒绝 ``file://`` 等本地协议）；
    - 默认只允许 ``https://``；``http://`` 仅**回环地址**（127.0.0.1/localhost/::1）
      与显式开启（``set_allow_http(True)`` 或环境变量 ``ACRPA_HTTP_ALLOW=1``）时可用；
    - 超时钳制在 1–120 秒，**不允许无限超时**；
    - 响应体上限 ``MAX_BODY``（4 MB），超限直接失败，防 OOM；
    - 拒绝跨协议重定向（https → http 降级），防 SSRF 绕过；
    - 不自动携带任何凭据（Cookie/Authorization 必须由调用方在请求头里显式给出）。

为何用 urllib 而不是 requests
-----------------------------
``requests`` 虽是既有依赖，但它的 ``Session`` 会自作主张合并环境代理、
自动跟随重定向、并把响应整体读进内存；本命令需要**逐项控制**重定向策略、
超时与响应体上限，``urllib.request`` 的 ``OpenerDirector`` 正好可以只挂
必要的 handler 实现白名单语义，且无需额外依赖即可打包。故本模块用
``urllib.request``，不新增任何 import 面。
"""
import base64
import hashlib
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

#: 响应体上限（4 MB，与方案文档 §8.6 一致）
MAX_BODY = 4 * 1024 * 1024
#: 超时上下限（秒）
MIN_TIMEOUT = 1.0
MAX_TIMEOUT = 120.0
DEFAULT_TIMEOUT = 15.0
#: 允许的方法
METHODS = ("GET", "POST", "PUT", "DELETE", "HEAD")
#: 是否允许非回环地址的明文 HTTP（默认关闭，需显式开启）
_ALLOW_HTTP = False
#: 回环主机名/地址
_LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "::1", "[::1]", "0.0.0.0")


def _clean(value):
    """归一化入参 → 去空白字符串；``None`` / ``empty`` / ``none`` 一律视为空。"""
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in ("none", "null", "-"):
        return ""
    return text


def _to_float(value, default):
    try:
        return float(_clean(value) or default)
    except Exception:
        return default


# ══════════════════════════════════════════════════════════════════════
# HTTP
# ══════════════════════════════════════════════════════════════════════
def allow_http():
    """当前是否允许非回环地址的明文 HTTP。"""
    if _ALLOW_HTTP:
        return True
    return _clean(os.environ.get("ACRPA_HTTP_ALLOW", "")).lower() in (
        "1", "true", "yes", "on")


def set_allow_http(flag):
    """显式开关「非回环 http://」（供设置界面 / 测试打开）。"""
    global _ALLOW_HTTP
    _ALLOW_HTTP = bool(flag)
    return _ALLOW_HTTP


def _check_url(url):
    """URL 白名单校验 → (ok, msg, 归一化 URL)。"""
    text = _clean(url)
    if not text:
        return False, "URL 为空：请先填写请求地址", ""
    try:
        parts = urllib.parse.urlsplit(text)
    except Exception as e:                 # noqa: BLE001
        return False, "URL 解析失败: {}".format(e), ""
    scheme = (parts.scheme or "").lower()
    if scheme not in ("http", "https"):
        return False, ("只允许 http:// 或 https://（收到 {!r}）；"
                       "本地协议/文件协议一律拒绝".format(parts.scheme)), ""
    host = (parts.hostname or "").lower()
    if not host:
        return False, "URL 缺少主机名: {}".format(text), ""
    if scheme == "http":
        if host not in _LOOPBACK_HOSTS and not allow_http():
            return False, ("默认只允许 https://；http://{} 需显式开启"
                           "（环境变量 ACRPA_HTTP_ALLOW=1）".format(host)), ""
    return True, "", text


class _SameSchemeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """只允许同协议重定向（拒绝 https → http 降级，防 SSRF 绕过）。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        old_scheme = urllib.parse.urlsplit(req.full_url).scheme.lower()
        new_scheme = urllib.parse.urlsplit(newurl).scheme.lower()
        if new_scheme != old_scheme:
            raise urllib.error.HTTPError(
                newurl, code,
                "拒绝跨协议重定向 ({} → {})".format(old_scheme, new_scheme),
                headers, fp)
        return urllib.request.HTTPRedirectHandler.redirect_request(
            self, req, fp, code, msg, headers, newurl)


def _build_opener():
    """构造 opener：禁用环境代理 + 只允许同协议重定向。

    * 传 ``ProxyHandler({})`` 实例 → ``build_opener`` 不再加默认代理 handler，
      行为可预期（不受系统/环境代理影响），也避免凭据经由未知代理外流；
    * 传 ``_SameSchemeRedirectHandler`` 实例 → 默认 ``HTTPRedirectHandler``
      被替换，跨协议重定向直接被拒。
    """
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}), _SameSchemeRedirectHandler())


def parse_headers(text):
    """请求头解析 → (headers dict, errors)。支持 JSON 对象或多行 ``k: v``。"""
    raw = _clean(text)
    if not raw:
        return {}, []
    if raw.startswith("{"):
        try:
            data = json.loads(raw)
        except Exception as e:             # noqa: BLE001
            return {}, ["请求头 JSON 解析失败: {}".format(e)]
        if not isinstance(data, dict):
            return {}, ["请求头 JSON 必须是对象"]
        return {str(k): str(v) for k, v in data.items()}, []
    out, errors = {}, []
    for line in raw.replace("\\n", "\n").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            errors.append("请求头格式应为 `键: 值`，收到: {!r}".format(line))
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        if not key:
            errors.append("请求头缺少键名: {!r}".format(line))
            continue
        out[key] = value.strip()
    return out, errors


def http_request(method="GET", url="", headers="", body="", timeout=DEFAULT_TIMEOUT,
                 save_path="", allow_http_plain=False):
    """发送 HTTP(S) 请求 → (ok, msg, {"status", "text", "reason", "saved"})。

    ``text`` 为按响应声明的 charset 解码后的响应体（上限 ``MAX_BODY``）。
    """
    verb = (_clean(method) or "GET").upper()
    if verb not in METHODS:
        return False, "不支持的方法: {!r}（可选 {}）".format(method, "/".join(METHODS)), None
    ok, msg, url_text = _check_url(url)
    if not ok:
        return False, msg, None
    if allow_http_plain:
        set_allow_http(True)

    seconds = _to_float(timeout, DEFAULT_TIMEOUT)
    seconds = max(MIN_TIMEOUT, min(MAX_TIMEOUT, seconds))

    hdrs, errors = parse_headers(headers)
    if errors:
        return False, "; ".join(errors), None

    payload = None
    body_text = "" if body is None else str(body)
    if verb in ("POST", "PUT", "DELETE") and body_text:
        payload = body_text.encode("utf-8")
        if not any(k.lower() == "content-type" for k in hdrs):
            stripped = body_text.lstrip()
            if stripped.startswith(("{", "[")):
                hdrs["Content-Type"] = "application/json; charset=utf-8"
            else:
                hdrs["Content-Type"] = "application/x-www-form-urlencoded; charset=utf-8"

    req = urllib.request.Request(url_text, data=payload, headers=hdrs, method=verb)
    try:
        opener = _build_opener()
        with opener.open(req, timeout=seconds) as resp:
            status = int(getattr(resp, "status", 0) or resp.getcode() or 0)
            reason = str(getattr(resp, "reason", "") or "")
            raw = resp.read(MAX_BODY + 1)
            charset = "utf-8"
            try:
                ctype = resp.headers.get("Content-Type", "") or ""
                match = re.search(r"charset=([\w\-]+)", ctype, re.I)
                if match:
                    charset = match.group(1)
            except Exception:
                pass
    except urllib.error.HTTPError as e:
        # 4xx/5xx 仍是"请求成功完成"，把状态码与响应体交给脚本判断
        try:
            raw = e.read(MAX_BODY + 1)
        except Exception:
            raw = b""
        status = int(getattr(e, "code", 0) or 0)
        reason = str(getattr(e, "reason", "") or "")
        charset = "utf-8"
    except urllib.error.URLError as e:
        return False, "请求失败: {}".format(getattr(e, "reason", e)), None
    except Exception as e:                 # noqa: BLE001
        return False, "请求失败: {}".format(e), None

    if len(raw) > MAX_BODY:
        return (False, "响应体超过上限 {} MB，已中止读取".format(MAX_BODY // (1024 * 1024)),
                {"status": status, "text": "", "reason": reason, "saved": ""})
    try:
        text = raw.decode(charset, "replace")
    except Exception:
        text = raw.decode("utf-8", "replace")

    saved = ""
    target = _clean(save_path)
    if target:
        try:
            parent = os.path.dirname(target)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(target, "w", encoding="utf-8", newline="") as fh:
                fh.write(text)
            saved = target
        except Exception as e:             # noqa: BLE001
            return False, "响应已收到但保存失败: {}".format(e), {
                "status": status, "text": text, "reason": reason, "saved": ""}

    detail = {"status": status, "text": text, "reason": reason, "saved": saved}
    summary = "HTTP {} {} ({} 字节)".format(status, reason, len(raw))
    if saved:
        summary += "，已保存到 {}".format(saved)
    return 200 <= status < 400, summary, detail


# ══════════════════════════════════════════════════════════════════════
# JSON 取值
# ══════════════════════════════════════════════════════════════════════
def parse_json_path(path):
    """路径表达式 → token 列表；支持 ``a.b`` / ``a[0]`` / ``a['k']`` / 前导 ``$``。"""
    text = _clean(path)
    if text.startswith("$"):
        text = text[1:]
    tokens = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch in ".[":
            i += 1
            continue
        if ch == "]":
            i += 1
            continue
        if ch in "'\"":
            end = text.find(ch, i + 1)
            if end < 0:
                raise ValueError("引号未闭合: {}".format(path))
            tokens.append(text[i + 1:end])
            i = end + 1
            continue
        j = i
        while j < len(text) and text[j] not in ".[]":
            j += 1
        token = text[i:j].strip()
        if token:
            tokens.append(int(token) if token.lstrip("-").isdigit() else token)
        i = j
    return tokens


def _walk(obj, tokens):
    """按 token 逐层取值；取不到抛 KeyError（消息人类可读）。"""
    cur = obj
    walked = []
    for token in tokens:
        walked.append(str(token))
        if isinstance(token, int):
            if not isinstance(cur, (list, tuple)):
                raise KeyError("路径 {} 处期望数组，实际是 {}{}".format(
                    ".".join(walked[:-1]) or "$", type(cur).__name__,
                    "（数组越界）" if not isinstance(cur, (list, tuple)) else ""))
            if token >= len(cur) or token < -len(cur):
                raise KeyError("数组下标越界: {}[{}] (长度 {})".format(
                    ".".join(walked[:-1]) or "$", token, len(cur)))
            cur = cur[token]
            continue
        if not isinstance(cur, dict):
            raise KeyError("路径 {} 处期望对象，实际是 {}".format(
                ".".join(walked[:-1]) or "$", type(cur).__name__))
        if token not in cur:
            raise KeyError("缺少键: {}".format(".".join(walked)))
        cur = cur[token]
    return cur


def json_get(content, path="", default=""):
    """从 JSON 文本按路径取值 → (ok, msg, value)。

    解析失败 / 路径不存在时返回 ``default``（不抛异常），消息里给出原因。
    """
    text = "" if content is None else str(content)
    if not text.strip():
        return False, "JSON 内容为空", default
    try:
        data = json.loads(text)
    except Exception as e:                 # noqa: BLE001
        return False, "JSON 解析失败: {}".format(e), default
    try:
        tokens = parse_json_path(path)
    except Exception as e:                 # noqa: BLE001
        return False, "路径表达式非法: {}".format(e), default
    if not tokens:
        return True, "路径为空 → 返回整份 JSON", json.dumps(data, ensure_ascii=False)
    try:
        value = _walk(data, tokens)
    except KeyError as e:
        return False, str(e.args[0] if e.args else e), default
    except Exception as e:                 # noqa: BLE001
        return False, "取值失败: {}".format(e), default
    if isinstance(value, (dict, list)):
        return True, "取值成功（{}）".format(type(value).__name__), json.dumps(
            value, ensure_ascii=False)
    if value is None:
        return True, "取值为 null → 返回默认值", default
    if isinstance(value, bool):
        return True, "取值成功", "true" if value else "false"
    return True, "取值成功", value


# ══════════════════════════════════════════════════════════════════════
# 编码工具
# ══════════════════════════════════════════════════════════════════════
def b64(text, mode="编码"):
    """Base64 编解码 → (ok, msg, 结果)。解码时容忍 ``-_`` 变体与缺失填充。"""
    raw = "" if text is None else str(text)
    act = _clean(mode) or "编码"
    if act == "编码":
        try:
            return True, "已编码", base64.b64encode(raw.encode("utf-8")).decode("ascii")
        except Exception as e:             # noqa: BLE001
            return False, "编码失败: {}".format(e), ""
    if act == "解码":
        cleaned = re.sub(r"\s+", "", raw).replace("-", "+").replace("_", "/")
        # 补齐 base64 填充
        pad = len(cleaned) % 4
        if pad:
            cleaned += "=" * (4 - pad)
        try:
            data = base64.b64decode(cleaned)
        except Exception as e:             # noqa: BLE001
            return False, "解码失败（不是合法 Base64）: {}".format(e), ""
        try:
            return True, "已解码", data.decode("utf-8")
        except Exception:
            return True, "已解码（非 UTF-8，按 UTF-8 替换非法字节）", \
                data.decode("utf-8", "replace")
    return False, "模式只能填 编码/解码（收到 {!r}）".format(mode), ""


def url_codec(text, mode="编码"):
    """URL 百分号编码/解码 → (ok, msg, 结果)。"""
    raw = "" if text is None else str(text)
    act = _clean(mode) or "编码"
    if act == "编码":
        try:
            return True, "已编码", urllib.parse.quote(raw, safe="")
        except Exception as e:             # noqa: BLE001
            return False, "编码失败: {}".format(e), ""
    if act == "解码":
        try:
            return True, "已解码", urllib.parse.unquote(raw)
        except Exception as e:             # noqa: BLE001
            return False, "解码失败: {}".format(e), ""
    return False, "模式只能填 编码/解码（收到 {!r}）".format(mode), ""


def hash_source(algo="sha256", source_type="文本", source=""):
    """MD5/SHA1/SHA256 → (ok, msg, 十六进制摘要)。

    ``source_type="文件"`` 时分块读取（8 KB），避免大文件一次性读进内存。
    """
    name = (_clean(algo) or "sha256").lower()
    if name in ("md5", "sha1", "sha-1", "sha256", "sha-256"):
        name = name.replace("-", "")
    else:
        return False, "算法只能填 md5/sha1/sha256（收到 {!r}）".format(algo), ""
    kind = _clean(source_type) or "文本"
    if kind not in ("文本", "文件"):
        return False, "源类型只能填 文本/文件（收到 {!r}）".format(source_type), ""
    try:
        digest = hashlib.new(name)
    except Exception as e:                 # noqa: BLE001
        return False, "初始化 {} 失败: {}".format(name, e), ""

    if kind == "文本":
        digest.update(("" if source is None else str(source)).encode("utf-8"))
        return True, "{} 摘要（文本）".format(name.upper()), digest.hexdigest()

    path = _clean(source)
    if not path:
        return False, "文件路径为空：请先填写要计算哈希的文件", ""
    if not os.path.isfile(path):
        return False, "文件不存在: {}".format(path), ""
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(8192), b""):
                digest.update(chunk)
    except Exception as e:                 # noqa: BLE001
        return False, "读取文件失败: {}".format(e), ""
    return True, "{} 摘要（文件）".format(name.upper()), digest.hexdigest()
