"""
AI Client for ACRPA - Compatible with Python 3.7
Supports OpenAI-compatible APIs (DeepSeek, Qwen, etc.)
Uses requests library instead of openai SDK
"""
import json
import requests


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
