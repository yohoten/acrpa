"""accounts.py — 脚本市场账号 token 的安全存取与校验。

安全规约 (docs/marketplace-v2-design.md §2.4)：
  * token 只进 Windows 凭据库 (复用 state.cred_write/cred_read/cred_delete)；
  * 绝不写入 config.json，绝不写日志明文；
  * 异常/日志文本一律经 _redact() 脱敏 (token → ***)；
  * 凭据库不可用时 save_token 直接 raise AccountError，绝不回退明文文件。
"""
import re
import json

import state
from utils import log1


class AccountError(Exception):
    """市场账号操作异常。"""
    pass


PROVIDERS = ("gitee", "github")

_API = {
    "gitee":  {"verify_url": "https://gitee.com/api/v5/user"},
    "github": {"verify_url": "https://api.github.com/user"},
}

_CRED_PREFIX = "ACRPA/market"   # target = "ACRPA/market/<provider>_token"
_USER_AGENT = "ACRPA/2.0"
_TIMEOUT = 15

_HEXLIKE_RE = re.compile(r"[0-9a-zA-Z_\-]{24,}")


# ══════════════════════════════════════════════════════════════════════
# 内部工具
# ══════════════════════════════════════════════════════════════════════

def _cred_target(provider):
    """凭据库 target 名: ACRPA/market/<provider>_token。"""
    return "{}/{}_token".format(_CRED_PREFIX, provider)


def _norm_provider(provider):
    """归一化并校验 provider；非法 raise AccountError。"""
    p = str(provider or "").strip().lower()
    if p not in PROVIDERS:
        raise AccountError("不支持的账号提供商: {}".format(provider))
    return p


def _redact(text, token=None):
    """脱敏：先剔除已知 token 明文，再把长 token 样字符串替换为 ***。"""
    s = "" if text is None else str(text)
    if token:
        s = s.replace(str(token), "***")
    s = _HEXLIKE_RE.sub("***", s)
    return s


def _read_json(resp):
    """兼容 requests.Response 与 urllib 响应的 JSON 解析。"""
    try:
        return resp.json()
    except AttributeError:
        raw = resp.read()
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="ignore")
        return json.loads(raw)


def _http_get(url, headers=None, params=None, timeout=_TIMEOUT):
    """GET 请求：requests 优先，缺依赖时回退 urllib 标准库。"""
    hdrs = dict(headers or {})
    try:
        import requests
        return requests.get(url, headers=hdrs, params=params, timeout=timeout)
    except ImportError:
        pass
    import urllib.parse
    import urllib.request
    if params:
        qs = urllib.parse.urlencode(params)
        url = url + ("&" if "?" in url else "?") + qs
    req = urllib.request.Request(url, headers=hdrs)
    return urllib.request.urlopen(req, timeout=timeout)


def _status_of(resp):
    s = getattr(resp, "status_code", None)
    if s is None:
        s = getattr(resp, "status", 0)
    try:
        return int(s)
    except Exception:
        return 0


def _normalize_user(provider, data):
    """把 provider 差异归一为统一 user_info。"""
    if not isinstance(data, dict):
        raise AccountError("账号校验响应格式异常")
    login = data.get("login") or data.get("name") or ""
    return {
        "provider": provider,
        "login": login,
        "name": data.get("name") or login,
        "avatar_url": data.get("avatar_url") or "",
        "id": data.get("id") or "",
    }


# ══════════════════════════════════════════════════════════════════════
# 凭据存取 (只走 Windows 凭据库)
# ══════════════════════════════════════════════════════════════════════

def save_token(provider, token):
    """把 token 写入 Windows 凭据库；失败 raise AccountError (不回退明文)。"""
    provider = _norm_provider(provider)
    tok = str(token or "").strip()
    if not tok:
        raise AccountError("token 不能为空")
    if not state.cred_write(_cred_target(provider), tok):
        raise AccountError("凭据库写入失败 (Windows Credential Manager 不可用)")
    log1("市场账号: 已保存 {} token (Windows 凭据库)".format(provider))
    return True


def get_token(provider):
    """读取 token；不存在或凭据库不可用返回 None。"""
    provider = _norm_provider(provider)
    try:
        return state.cred_read(_cred_target(provider))
    except Exception:
        return None


def clear_token(provider):
    """删除 token (幂等)。"""
    provider = _norm_provider(provider)
    try:
        state.cred_delete(_cred_target(provider))
    except Exception:
        pass


def has_token(provider):
    """是否已保存 token (无网络)。"""
    try:
        return bool(get_token(provider))
    except Exception:
        return False


def list_logged_in():
    """无网络、仅凭据库探测，返回已存 token 的 provider 列表。"""
    out = []
    for p in PROVIDERS:
        try:
            if get_token(p):
                out.append(p)
        except Exception:
            continue
    return out


# ══════════════════════════════════════════════════════════════════════
# token 校验 / 登录
# ══════════════════════════════════════════════════════════════════════

def verify_token(provider, token=None):
    """校验 token 并返回 user_info (不持久化)。

    Gitee : GET verify_url?access_token=<t>  (Header 亦带 Authorization: token <t>)
    GitHub: GET verify_url                    (Header Authorization: token <t>)
    返回 {"provider","login","name","avatar_url","id"}。
    网络/401/403 等 → raise AccountError，错误信息经脱敏 (不含 token 明文)。
    """
    provider = _norm_provider(provider)
    tok = token if token is not None else get_token(provider)
    tok = str(tok or "").strip()
    if not tok:
        raise AccountError("未提供 {} token".format(provider))

    cfg = _API[provider]
    url = cfg["verify_url"]
    headers = {"User-Agent": _USER_AGENT, "Accept": "application/json"}
    params = None

    if provider == "gitee":
        params = {"access_token": tok}
        headers["Authorization"] = "token {}".format(tok)
    else:
        # 设计 §2.4：GitHub 使用 "Authorization: token <t>" (GitHub 同时接受 Bearer)
        headers["Authorization"] = "token {}".format(tok)

    try:
        resp = _http_get(url, headers=headers, params=params)
    except Exception as e:
        raise AccountError("网络请求失败: {}".format(_redact(e, tok)))

    status = _status_of(resp)
    if status == 401:
        raise AccountError("{} token 无效或已过期 (401)".format(provider))
    if status == 403:
        raise AccountError("{} 拒绝访问，可能触发限流或权限不足 (403)".format(provider))
    if status >= 400:
        raise AccountError("{} 校验失败 (HTTP {})".format(provider, status))

    try:
        data = _read_json(resp)
    except Exception as e:
        raise AccountError("账号校验响应解析失败: {}".format(_redact(e, tok)))

    return _normalize_user(provider, data)


def current_user(provider):
    """有 token 则返回 user_info，否则/网络异常返回 None。"""
    try:
        if not has_token(provider):
            return None
        return verify_token(provider)
    except Exception:
        return None


def login(provider, token):
    """校验 → 成功则保存到凭据库 → 返回 user_info。

    校验失败不会写入凭据库；写入失败 raise AccountError。
    """
    provider = _norm_provider(provider)
    info = verify_token(provider, token)
    save_token(provider, token)
    log1("市场账号: {} 登录成功 ({})".format(provider, info.get("login", "")))
    return info


def logout(provider):
    """清除 token；若当前 provider 匹配则一并清空展示用用户名并落盘。"""
    provider = _norm_provider(provider)
    clear_token(provider)
    try:
        if (getattr(state, "MARKET_PROVIDER", "") or "") == provider:
            state.MARKET_USERNAME = ""
            try:
                state.save_config()
            except Exception:
                pass
    except Exception:
        pass
    log1("市场账号: 已注销 {}".format(provider))
