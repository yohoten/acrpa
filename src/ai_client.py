"""
AI Client for ACRPA - Compatible with Python 3.7
Supports OpenAI-compatible APIs (DeepSeek, Qwen, etc.)
Uses requests library instead of openai SDK
"""
import json
import socket
import requests

import state
import ai_cost


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

            # 失败时把服务端的错误正文带出来 —— 只报 "400 Client Error" 等于没说。
            if response.status_code >= 400:
                detail = ""
                try:
                    body = response.json()
                    err = body.get("error")
                    if isinstance(err, dict):
                        detail = err.get("message") or ""
                    elif err:
                        detail = str(err)
                    if not detail:
                        detail = json.dumps(body, ensure_ascii=False)[:300]
                except Exception:
                    detail = (response.text or "")[:300]
                suffix = "（{}）".format(detail) if detail else ""
                if response.status_code == 401:
                    raise APIAuthError("API Key 无效或已过期" + suffix)
                if response.status_code == 429:
                    raise APIRateLimitError("请求频率超限，请稍后重试" + suffix)
                if response.status_code >= 500:
                    raise APIError("服务器错误: {} {}".format(response.status_code, suffix))
                raise APIError("请求被拒绝: HTTP {}{}".format(response.status_code, suffix))

            data = response.json()

            # token 记账 (脱敏落盘)
            ai_cost.record_usage(model, data.get("usage") or {})

            # Create a simple response object compatible with openai SDK
            class ChatCompletion:
                def __init__(self, data):
                    choice = (data.get("choices") or [{}])[0]
                    message = choice.get("message") or {}
                    # 推理模型 (deepseek-reasoner 一类) 会先产出 reasoning_content,
                    # 正文为空时只有它能说明"token 预算被推理吃光了"。
                    self.finish_reason = choice.get("finish_reason") or ""
                    self.reasoning_content = message.get("reasoning_content") or ""
                    self.usage = data.get("usage") or {}
                    self.model = data.get("model") or ""
                    self.choices = [
                        type('Choice', (), {
                            'message': type('Message', (), {
                                'content': message.get('content')
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

    def chat_completions_stream(self, model, messages, temperature=0.2,
                                max_tokens=1500, timeout=60, on_delta=None):
        """流式调用 chat completions (SSE)。

        Args:
            model / messages / temperature / max_tokens / timeout: 同 chat_completions
            on_delta: callable(chunk_text), 每收到一段增量正文回调一次 (用于 UI 流式展示)

        Returns:
            完整正文 str。异常时抛与 chat_completions 相同的 APIError 子类,
            调用方可降级回非流式路径。
        """
        url = f"{self.base_url}/chat/completions"

        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }

        try:
            response = self.session.post(url, json=payload, timeout=timeout,
                                         stream=True)
        except requests.exceptions.Timeout:
            raise APITimeoutError("请求超时，请检查网络连接")
        except requests.exceptions.ConnectionError:
            raise APIError("网络连接失败，请检查网络设置")
        except requests.exceptions.RequestException as e:
            raise APIError(f"API 请求失败: {str(e)}")

        # 失败时把服务端错误正文带出 (与 chat_completions 同口径)
        if response.status_code >= 400:
            detail = ""
            try:
                detail = (response.text or "")[:300]
            except Exception:
                pass
            suffix = "（{}）".format(detail) if detail else ""
            if response.status_code == 401:
                raise APIAuthError("API Key 无效或已过期" + suffix)
            if response.status_code == 429:
                raise APIRateLimitError("请求频率超限，请稍后重试" + suffix)
            if response.status_code >= 500:
                raise APIError("服务器错误: {} {}".format(response.status_code, suffix))
            raise APIError("请求被拒绝: HTTP {}{}".format(response.status_code, suffix))

        chunks = []
        try:
            for raw in response.iter_lines(decode_unicode=True):
                if not raw:
                    continue
                line = raw.strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                except Exception:
                    continue
                choice = (obj.get("choices") or [{}])[0]
                delta = choice.get("delta") or {}
                text = delta.get("content")
                if text:
                    chunks.append(text)
                    if on_delta:
                        try:
                            on_delta(text)
                        except Exception:
                            pass
                # 流式结束帧也可能携带 usage
                usage = obj.get("usage")
                if usage:
                    ai_cost.record_usage(model, usage)
        finally:
            try:
                response.close()
            except Exception:
                pass

        return "".join(chunks)


# ══════════════════════════════════════════════════════════════════════
# 脚本生成：调用 + 空正文重试 + 成因诊断
# ══════════════════════════════════════════════════════════════════════
# 背景（本机实测复现，非推测）：当前端点背后是**推理模型**，它"想"的时候会把整个
# max_tokens 预算烧在 reasoning_content 上。此时 HTTP 仍是 200，但
# message.content 为空 —— 调用方只看到"内容为空"，完全无从判断原因。
#
#   实测同一提示词（max_tokens → finish_reason / content / reasoning）：
#     500  → length /   0 字符 /  943 字符
#    2000  → length /   0 字符 / 5379 字符   ← 应用原先用的就是 2000
#    1000  → stop   / 593 字符 /    0 字符
#    8000  → stop   / 640 字符 /    0 字符
#
# 所以修法有两半：给足预算 + 空正文时自动放大预算重试一次，并把成因写进错误消息。
SCRIPT_MAX_TOKENS = 8000            # 推理模型需要余量；实测 2000 会被吃光
SCRIPT_RETRY_MULTIPLIER = 2


def _chat_with_backoff(client, model, messages, temperature, max_tokens, timeout,
                       max_attempts=3):
    """429/超时 指数退避重试; 其他异常直接抛出。"""
    import time as _time
    last = None
    for attempt in range(1, max_attempts + 1):
        try:
            return client.chat_completions(model=model, messages=messages,
                                           temperature=temperature,
                                           max_tokens=max_tokens, timeout=timeout)
        except (APIRateLimitError, APITimeoutError) as e:
            last = e
            if attempt >= max_attempts:
                break
            _time.sleep(ai_cost.backoff_delay(attempt))
    raise last


def empty_output_reason(response):
    """正文为空时的人类可读成因（finish_reason / 推理长度 / token 用量）。"""
    parts = []
    fr = getattr(response, "finish_reason", "") or ""
    if fr:
        parts.append("finish_reason={}".format(fr))
    reasoning = getattr(response, "reasoning_content", "") or ""
    if reasoning:
        parts.append("推理过程 {} 字符".format(len(reasoning)))
    usage = getattr(response, "usage", None) or {}
    if usage.get("completion_tokens"):
        detail = usage.get("completion_tokens_details") or {}
        if detail.get("reasoning_tokens"):
            parts.append("其中推理占用 {} tokens".format(detail["reasoning_tokens"]))
        parts.append("completion_tokens={}".format(usage["completion_tokens"]))
    return "；".join(parts)


def generate_script_content(client, messages, model, temperature=0.3,
                            max_tokens=None, timeout=120, on_retry=None,
                            on_delta=None):
    """请求脚本正文 → (content, response)。

    on_delta 提供时首轮走流式 (SSE) 逐段回调; 流式异常/空正文自动降级到
    非流式退避重试路径。正文为空且判定为"预算被推理吃光"时，自动放大预算
    重试一次；仍为空则抛 ValueError。
    """
    budget = int(max_tokens or SCRIPT_MAX_TOKENS)

    # 提示词/结果缓存: 相同请求命中直接返回 (节省 token 成本)
    key = ai_cost.cache_key(model, messages, temperature, budget)
    cached = ai_cost.cache_get(key)
    if cached:
        if on_delta:
            try:
                on_delta(cached)
            except Exception:
                pass
        return cached, None

    response = None
    if on_delta:
        # 首轮流式; 异常/空降级到非流式
        try:
            streamed = client.chat_completions_stream(
                model=model, messages=messages, temperature=temperature,
                max_tokens=budget, timeout=timeout, on_delta=on_delta)
            content = (streamed or "").strip()
            if content:
                ai_cost.cache_put(key, content)
                return content, None
        except Exception:
            pass
    if response is None:
        response = _chat_with_backoff(client, model, messages, temperature, budget, timeout)
    content = ((response.choices[0].message.content) or "").strip()
    if content:
        ai_cost.cache_put(key, content)
        return content, response

    reason = empty_output_reason(response)
    truncated = (getattr(response, "finish_reason", "") == "length"
                 or bool(getattr(response, "reasoning_content", "")))
    if truncated:
        bigger = budget * SCRIPT_RETRY_MULTIPLIER
        if on_retry:
            try:
                on_retry(reason, bigger)
            except Exception:
                pass
        response = _chat_with_backoff(client, model, messages, temperature, bigger,
                                      timeout + 60)
        content = ((response.choices[0].message.content) or "").strip()
        if content:
            ai_cost.cache_put(key, content)
            return content, response
        reason = "{}；放大到 {} tokens 重试后仍为空".format(reason, bigger) if reason \
            else "放大到 {} tokens 重试后仍为空".format(bigger)

    raise ValueError(
        "AI 没有返回正文{}。常见原因：推理模型把输出预算耗在了思考上，或提示词过长。"
        "请重试，或在「设置 → AI」里改用非推理模型 / 缩短输入。".format(
            "（{}）".format(reason) if reason else ""))


# ══════════════════════════════════════════════════════════════════════
# 自然语言 → 工作流 JSON (非 function-calling: 严格提示词 + 流式 + 静态检查兜底)
# ══════════════════════════════════════════════════════════════════════
WF_MAX_FIX_ROUNDS = 3

_WF_SYSTEM_PROMPT = (
    "你是 RPA 工作流设计器。只输出一个 JSON 对象，不要任何解释、前后缀或代码块标记。"
    "JSON 结构：{\"name\": \"...\", \"steps\": [...]}。"
    "step 支持 type: script/command/variable/log/wait/parallel/loop/retry/workflow/condition。"
    "字段约定："
    " script: {\"type\":\"script\",\"path\":\"...\"};"
    " command: {\"type\":\"command\",\"cmd\":\"...\",\"params\":[...]};"
    " variable: {\"type\":\"variable\",\"var_name\":\"...\",\"var_value\":\"...\"};"
    " log: {\"type\":\"log\",\"text\":\"...\"};"
    " wait: {\"type\":\"wait\",\"seconds\":N};"
    " parallel: {\"type\":\"parallel\",\"steps\":[...],\"outputs\":[...]};"
    " loop: {\"type\":\"loop\",\"times\":\"3\",\"steps\":[...]};"
    " retry: {\"type\":\"retry\",\"max_attempts\":3,\"delay\":1.0,\"steps\":[...]};"
    " workflow: {\"type\":\"workflow\",\"path\":\"sub.json\",\"inputs\":{},\"outputs\":[]};"
    " condition: {\"type\":\"condition\",\"if\":\"...\",\"then\":{...},\"else\":{...}}。"
)


def _parse_workflow_json(content):
    """从 AI 输出中提取并解析工作流 JSON dict (容错代码块标记)。"""
    import json as _json
    s = (content or "").strip()
    if not s:
        raise ValueError("内容为空")
    if s.startswith("```"):
        s = s.strip("`")
        if s.lower().startswith("json"):
            s = s[4:]
        s = s.strip()
    start = s.find("{")
    end = s.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("输出不含 JSON 对象")
    data = _json.loads(s[start:end + 1])
    if not isinstance(data, dict) or "steps" not in data:
        raise ValueError("JSON 缺少 steps 字段")
    data["steps"] = list(data.get("steps") or [])
    return data


def generate_workflow_from_text(client, text, model, temperature=0.3,
                                max_tokens=4000, timeout=120, on_delta=None):
    """自然语言 → 工作流 JSON, 用 flow_graph.analyze 静态检查兜底修正。

    Returns:
        (wf_data dict, issues list)。解析/校验失败抛 ValueError。
    """
    import flow_graph

    messages = [
        {"role": "system", "content": _WF_SYSTEM_PROMPT},
        {"role": "user", "content": "请生成工作流：" + (text or "")},
    ]

    def _non_stream():
        resp = _chat_with_backoff(client, model, messages, temperature,
                                  max_tokens, timeout)
        return ((resp.choices[0].message.content) or "").strip()

    data = None
    issues = []
    for round_no in range(WF_MAX_FIX_ROUNDS + 1):
        content = ""
        if round_no == 0:
            # 首轮优先流式展示; 流式异常降级非流式 (带退避重试)
            try:
                content = client.chat_completions_stream(
                    model=model, messages=messages, temperature=temperature,
                    max_tokens=max_tokens, timeout=timeout, on_delta=on_delta)
            except Exception:
                content = _non_stream()
        else:
            content = _non_stream()
        content = (content or "").strip()
        if not content:
            raise ValueError("AI 没有返回工作流内容")

        try:
            data = _parse_workflow_json(content)
        except Exception as e:
            if round_no >= WF_MAX_FIX_ROUNDS:
                raise ValueError("AI 输出解析失败: {}".format(e))
            messages.append({"role": "assistant", "content": content})
            messages.append({"role": "user", "content":
                "输出不是合法工作流 JSON: {}. 请只输出一个合法 JSON 对象。".format(e)})
            continue

        # 静态检查兜底: 9 项检查, 有 error 则回填修正 (有限轮)
        g = flow_graph.promote(data)
        issues = flow_graph.analyze(g)
        errors = [i for i in issues if i.get("level") == "error"]
        if not errors:
            return data, issues
        if round_no >= WF_MAX_FIX_ROUNDS:
            return data, issues
        err_text = "\n".join(i.get("message", "") for i in errors)
        messages.append({"role": "assistant", "content": content})
        messages.append({"role": "user", "content":
            "工作流存在以下静态问题，请修正后重新输出完整 JSON：\n" + err_text})

    return data, issues


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


# ── 是否已配置可用密钥的单一真源（带轻量缓存，避免热路径重复读取凭据库）──
_has_key_cache = None  # None=未缓存；True/False=已缓存结果


def _invalidate_key_cache():
    """使 has_ai_key 的缓存失效（密钥/提供商变更后调用）。"""
    global _has_key_cache
    _has_key_cache = None


def refresh_key_cache():
    """外部主动刷新 has_ai_key 缓存，下次调用将重新探测密钥可用性。"""
    _invalidate_key_cache()


def has_ai_key():
    """是否已配置可用 AI 密钥（与 resolve_api_key 同口径：凭据库优先，state.API_KEY 回退）。

    结果经模块级 _has_key_cache 缓存；set_provider_key/clear_provider_key 会自动失效，
    亦可调用 refresh_key_cache() 手动刷新。缓存目的在于：当守卫被反复评估时（例如
    执行引擎每次判定是否启用 AI 能力）避免重复读取 Windows 凭据库。
    """
    global _has_key_cache
    if _has_key_cache is None:
        _has_key_cache = bool(resolve_api_key())
    return _has_key_cache


def set_provider_key(pid, key):
    """把某提供商的密钥写入 Windows 凭据库；失败/空返回 False，不抛。"""
    if not pid or not key:
        return False
    try:
        ok = bool(state.cred_write(_key_target(pid), key))
    except Exception:
        return False
    if ok:
        _invalidate_key_cache()
    return ok


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
    _invalidate_key_cache()


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
