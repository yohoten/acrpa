"""
AI Client for ACRPA - Compatible with Python 3.7
Supports OpenAI-compatible APIs (DeepSeek, Qwen, etc.)
Uses requests library instead of openai SDK
"""
import json
import socket
import requests

import state


# Custom exception classes for better error handling
class APIError(Exception):
    """Base class for API errors"""
    pass


class APIAuthError(APIError):
    """Authentication error (401 Unauthorized)"""
    pass


class APIRateLimitError(APIError):
    """Rate limit exceeded (429 Too Many Requests)"""
    pass


class APITimeoutError(APIError):
    """Request timeout"""
    pass


class AIClient:
    """Lightweight AI client using HTTP requests"""
    
    def __init__(self, api_key, base_url="https://api.deepseek.com"):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        })
    
    def chat_completions(self, model, messages, temperature=0.2, max_tokens=1500, timeout=60):
        """
        Call chat completions API
        
        Args:
            model: Model name (e.g., "deepseek-v4-flash")
            messages: List of message dicts
            temperature: Sampling temperature (0-1)
            max_tokens: Maximum tokens to generate
            timeout: Request timeout in seconds
            
        Returns:
            Response object with choices[0].message.content
        """
        url = f"{self.base_url}/chat/completions"
        
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens
        }
        
        try:
            response = self.session.post(url, json=payload, timeout=timeout)
            
            # Handle specific HTTP errors
            if response.status_code == 401:
                raise APIAuthError("API Key 无效或已过期")
            elif response.status_code == 429:
                raise APIRateLimitError("请求频率超限，请稍后重试")
            elif response.status_code >= 500:
                raise APIError(f"服务器错误: {response.status_code}")
            
            response.raise_for_status()
            data = response.json()
            
            # Create a simple response object compatible with openai SDK
            class ChatCompletion:
                def __init__(self, data):
                    self.choices = [
                        type('Choice', (), {
                            'message': type('Message', (), {
                                'content': data['choices'][0]['message']['content']
                            })()
                        })()
                    ]
            
            return ChatCompletion(data)
            
        except requests.exceptions.Timeout:
            raise APITimeoutError("请求超时，请检查网络连接")
        except requests.exceptions.ConnectionError:
            raise APIError("网络连接失败，请检查网络设置")
        except requests.exceptions.RequestException as e:
            raise APIError(f"API 请求失败: {str(e)}")
        except (KeyError, IndexError) as e:
            raise APIError(f"API 响应格式错误: {str(e)}")


# ── 统一模型注册表 ──
# 单一数据源：UI 模型下拉框 (list_models)、create_client / ai_enhance 的
# base_url 判断 (get_base_url) 全部基于此表，避免多处硬编码漂移。
# 新增模型只需在此追加一条即可。
MODEL_REGISTRY = {
    "deepseek-chat": {
        "base_url": "https://api.deepseek.com/v1",
        "vendor": "DeepSeek",
        "label": "DeepSeek Chat",
    },
    "deepseek-v4-flash": {
        "base_url": "https://api.deepseek.com/v1",
        "vendor": "DeepSeek",
        "label": "DeepSeek V4 Flash",
    },
    "deepseek-reasoner": {
        "base_url": "https://api.deepseek.com/v1",
        "vendor": "DeepSeek",
        "label": "DeepSeek Reasoner",
    },
    "qwen-plus": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "vendor": "Aliyun Qwen",
        "label": "Qwen Plus",
    },
    "qwen-max": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "vendor": "Aliyun Qwen",
        "label": "Qwen Max",
    },
    "gpt-3.5-turbo": {
        "base_url": "https://api.openai.com/v1",
        "vendor": "OpenAI",
        "label": "GPT-3.5 Turbo",
    },
    "gpt-4": {
        "base_url": "https://api.openai.com/v1",
        "vendor": "OpenAI",
        "label": "GPT-4",
    },
}


def get_model_info(model):
    """返回模型在注册表中的条目；未注册时按关键词兜底推断。"""
    info = MODEL_REGISTRY.get(model)
    if info:
        return info
    m = model.lower()
    if "qwen" in m:
        return {"base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "vendor": "Aliyun Qwen", "label": model}
    if "claude" in m:
        return {"base_url": "https://api.anthropic.com/v1",
                "vendor": "Anthropic", "label": model}
    if "gpt" in m or "o1" in m or "o3" in m:
        return {"base_url": "https://api.openai.com/v1",
                "vendor": "OpenAI", "label": model}
    # 兜底默认 DeepSeek
    return {"base_url": "https://api.deepseek.com/v1",
            "vendor": "DeepSeek", "label": model}

def get_base_url(model):
    """根据模型返回对应的 API base_url（统一数据源）。"""
    return get_model_info(model)["base_url"]


def list_models():
    """返回注册表中的全部模型名（供设置 UI 下拉框使用）。"""
    return list(MODEL_REGISTRY.keys())


def create_client(api_key, model="deepseek-chat"):
    """
    Factory function to create appropriate client based on model
    
    Args:
        api_key: API key
        model: Model name
        
    Returns:
        AIClient instance
    """
    base_url = get_base_url(model)
    return AIClient(api_key, base_url)


# ══════════════════════════════════════════════════════════════════════════
# AI 提供商 / 模型 自定义解析层（PR-4）
#   * 预设提供商 + 用户自定义提供商（state.AI_CUSTOM_PROVIDERS）
#   * 密钥一律走 Windows 凭据库 target "ACRPA/ai_key/<pid>"，绝不落 config.json
#   * 向后兼容：AI_PROVIDER/AI_MODEL/AI_BASE_URL 为空时回退到旧 api_key/api_model
# ══════════════════════════════════════════════════════════════════════════

_KEY_TARGET_PREFIX = "ACRPA/ai_key/"

PROVIDER_PRESETS = {
    "deepseek": {
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "models": ["deepseek-chat", "deepseek-reasoner"],
        "key_target": _KEY_TARGET_PREFIX,
        "openai_compatible": True,
    },
    "qwen": {
        "label": "通义千问(阿里云兼容模式)",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "models": ["qwen-plus", "qwen-turbo", "qwen-max"],
        "key_target": _KEY_TARGET_PREFIX,
        "openai_compatible": True,
    },
    "openai": {
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "models": ["gpt-4o-mini", "gpt-4o", "gpt-4.1-mini"],
        "key_target": _KEY_TARGET_PREFIX,
        "openai_compatible": True,
    },
    "ollama": {
        "label": "Ollama(本地)",
        "base_url": "http://127.0.0.1:11434/v1",
        "models": ["llama3.1", "qwen2.5"],
        "key_target": _KEY_TARGET_PREFIX,
        "openai_compatible": True,
    },
    "custom": {
        "label": "自定义",
        "base_url": "",
        "models": [],
        "key_target": _KEY_TARGET_PREFIX,
        "openai_compatible": True,
    },
    "anthropic": {
        "label": "Anthropic",
        "base_url": "https://api.anthropic.com/v1",
        "models": ["claude-3-5-sonnet-latest"],
        "key_target": _KEY_TARGET_PREFIX,
        "openai_compatible": False,
    },
}


def _key_target(pid):
    """凭据库 target 规则：ACRPA/ai_key/<pid>。"""
    return _KEY_TARGET_PREFIX + str(pid or "")


def _custom_list():
    """读取 state.AI_CUSTOM_PROVIDERS（容错：非 list 视为空）。"""
    try:
        raw = getattr(state, "AI_CUSTOM_PROVIDERS", None)
        return list(raw) if isinstance(raw, list) else []
    except Exception:
        return []


def _norm_custom(entry):
    """把一条自定义提供商规整为标准条目（只保留白名单字段，剔除敏感字段）。"""
    if not isinstance(entry, dict):
        return None
    cid = (entry.get("id") or "").strip()
    if not cid:
        return None
    models = entry.get("models")
    if isinstance(models, str):
        models = [m.strip() for m in models.split(",") if m.strip()]
    elif isinstance(models, list):
        models = [str(m).strip() for m in models if str(m).strip()]
    else:
        models = []
    return {
        "id": cid,
        "label": (entry.get("name") or cid),
        "base_url": (entry.get("base_url") or "").strip(),
        "models": models,
        "key_target": _KEY_TARGET_PREFIX,
        "openai_compatible": bool(entry.get("openai_compatible", True)),
        "custom": True,
    }


def list_providers():
    """返回全部提供商（预设在前、自定义追加；同名 id 以自定义为准）。"""
    merged = {}
    order = []
    for pid, info in PROVIDER_PRESETS.items():
        d = dict(info)
        d["id"] = pid
        d["custom"] = False
        merged[pid] = d
        order.append(pid)
    for entry in _custom_list():
        c = _norm_custom(entry)
        if not c:
            continue
        if c["id"] not in merged:
            order.append(c["id"])
        merged[c["id"]] = c
    return [merged[pid] for pid in order if pid in merged]


def get_provider(pid):
    """按 id 取提供商（含自定义）；不存在返回 None。"""
    if not pid:
        return None
    for p in list_providers():
        if p["id"] == pid:
            return p
    return None


def get_base_url_for(provider):
    """返回指定提供商的 base_url（含自定义）；未知返回 ""。"""
    p = get_provider(provider)
    return p.get("base_url", "") if p else ""


def _infer_provider_from_model(model):
    """按模型名前缀推断提供商 id；无法推断返回 ""。"""
    m = (model or "").strip().lower()
    if m.startswith("deepseek"):
        return "deepseek"
    if m.startswith("qwen") or m.startswith("qwq"):
        return "qwen"
    if m.startswith("gpt") or m.startswith("o1") or m.startswith("o3"):
        return "openai"
    if m.startswith("claude"):
        return "anthropic"
    return ""


def resolve_provider(model=None):
    """解析当前生效的提供商 id。

    优先级：AI_PROVIDER → (AI_BASE_URL 命中预设/自定义) → 模型名前缀推断
    （deepseek*/qwen*/qwq*/gpt*/o1*/o3*/claude*）；base_url 含 11434 或指向
    localhost/127.0.0.1 → ollama；其余 → "custom"。
    """
    pid = (getattr(state, "AI_PROVIDER", "") or "").strip()
    if pid:
        return pid
    base = (getattr(state, "AI_BASE_URL", "") or "").strip().rstrip("/")
    if base:
        for p in list_providers():
            pb = (p.get("base_url") or "").strip().rstrip("/")
            if pb and pb == base:
                return p["id"]
    if model is None:
        m = (getattr(state, "AI_MODEL", "") or "").strip()
        if not m:
            m = (getattr(state, "API_MODEL", "") or "").strip()
    else:
        m = model
    inferred = _infer_provider_from_model(m)
    if inferred:
        return inferred
    lb = base.lower()
    if "11434" in base or "localhost" in lb or "127.0.0.1" in base:
        return "ollama"
    return "custom"


def resolve_model():
    """解析当前生效的模型名：AI_MODEL → API_MODEL → 预设首个模型。"""
    m = (getattr(state, "AI_MODEL", "") or "").strip()
    if m:
        return m
    m = (getattr(state, "API_MODEL", "") or "").strip()
    if m:
        return m
    p = get_provider(resolve_provider())
    if p and p.get("models"):
        return p["models"][0]
    return "deepseek-chat"


def resolve_base_url():
    """解析当前生效的 base_url：AI_BASE_URL → 预设 base_url → ""（用 SDK 默认）。"""
    b = (getattr(state, "AI_BASE_URL", "") or "").strip()
    if b:
        return b
    p = get_provider(resolve_provider())
    if p and p.get("base_url"):
        return p["base_url"]
    return ""


def resolve_api_key():
    """解析当前生效的 API Key：凭据库(ACRPA/ai_key/<pid>) → state.API_KEY。"""
    pid = resolve_provider()
    if pid:
        k = get_provider_key(pid)
        if k:
            return k
    return getattr(state, "API_KEY", "") or ""


def set_provider_key(pid, key):
    """把某提供商的密钥写入 Windows 凭据库；失败/空返回 False，不抛。"""
    if not pid or not key:
        return False
    try:
        return bool(state.cred_write(_key_target(pid), key))
    except Exception:
        return False


def get_provider_key(pid):
    """读取某提供商已保存的密钥；不存在/失败返回 None，不抛。"""
    if not pid:
        return None
    try:
        return state.cred_read(_key_target(pid))
    except Exception:
        return None


def clear_provider_key(pid):
    """删除某提供商在凭据库中的密钥；不抛。"""
    if not pid:
        return
    try:
        state.cred_delete(_key_target(pid))
    except Exception:
        pass


def create_client_active():
    """按当前配置（provider/model/base_url/key）创建客户端。

    请求体与 create_client 一致，保证对既有调用方零破坏：
      key   → resolve_api_key()
      model → resolve_model()
      url   → resolve_base_url()（为空则回退 get_base_url(model)，与 create_client 一致）
    """
    model = resolve_model()
    base_url = resolve_base_url() or get_base_url(model)
    return AIClient(resolve_api_key(), base_url)


# ── 可被测试 monkeypatch 的 HTTP 钩子（默认走 requests）──
def _http_get(url, headers=None, timeout=8):
    """GET 钩子（测试可替换，避免真实发网）。"""
    return requests.get(url, headers=headers or {}, timeout=timeout)


def _http_post(url, json_body=None, headers=None, timeout=8):
    """POST 钩子（测试可替换，避免真实发网）。"""
    return requests.post(url, json=json_body, headers=headers or {}, timeout=timeout)


def _models_summary(resp):
    """从 /models 响应里提取人类可读的模型信息。"""
    try:
        data = resp.json()
        items = data.get("data") if isinstance(data, dict) else None
        if isinstance(items, list):
            return "models 可访问（{} 个）".format(len(items))
    except Exception:
        pass
    return "models 可访问"


def test_connection(provider=None, base_url=None, model=None, api_key=None,
                    timeout=8):
    """测试 AI 连接是否可用，返回 (ok: bool, message: str)，绝不抛。

    两段式探测：
      1) GET {base_url}/models（带 Authorization: Bearer <key>）；
      2) 若返回 404/405/501 → 回退发最小 chat/completions 请求。
    openai_compatible=False 的提供商（如 anthropic）直接返回不支持，不发请求。
    """
    try:
        pid = provider or resolve_provider()
        p = get_provider(pid)
        if p and not p.get("openai_compatible", True):
            return (False, "该提供商不使用 OpenAI 兼容接口，本工具暂不支持")
        if not base_url:
            base_url = (p.get("base_url") if p else "") or resolve_base_url()
        if not base_url:
            return (False, "未配置 BaseURL")
        base_url = base_url.strip().rstrip("/")
        if not model:
            model = resolve_model()
        if not api_key:
            api_key = (get_provider_key(pid) if pid else None) or resolve_api_key() or ""
        headers = {"Authorization": "Bearer " + api_key} if api_key else {}

        # ── 第一段：GET /models ──
        try:
            resp = _http_get(base_url + "/models", headers=headers, timeout=timeout)
        except (socket.timeout, requests.exceptions.Timeout):
            return (False, "超时：{} 秒内无响应".format(timeout))
        except (requests.exceptions.ConnectionError, OSError):
            return (False, "网络不可达：{}".format(base_url))
        except Exception as e:
            return (False, "请求失败：{}".format(e))

        status = getattr(resp, "status_code", None)
        if status == 200:
            return (True, "ok: " + _models_summary(resp))
        if status in (401, 403):
            return (False, "401 密钥无效")
        if status not in (404, 405, 501):
            return (False, "接口返回 HTTP {}".format(status))

        # ── 第二段：回退最小 chat/completions ──
        body = {
            "model": model,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 1,
        }
        try:
            resp2 = _http_post(base_url + "/chat/completions", json_body=body,
                               headers=headers, timeout=timeout)
        except (socket.timeout, requests.exceptions.Timeout):
            return (False, "超时：{} 秒内无响应".format(timeout))
        except (requests.exceptions.ConnectionError, OSError):
            return (False, "网络不可达：{}".format(base_url))
        except Exception as e:
            return (False, "请求失败：{}".format(e))

        st2 = getattr(resp2, "status_code", None)
        if st2 == 200:
            return (True, "ok: chat/completions 可用（模型 {}）".format(model))
        if st2 in (401, 403):
            return (False, "401 密钥无效")
        if st2 == 404:
            return (False, "404 接口不存在：{}".format(base_url))
        return (False, "接口返回 HTTP {}".format(st2))
    except Exception as e:
        return (False, "测试失败：{}".format(e))
