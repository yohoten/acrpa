"""
AI 调用成本记账 + 提示词/结果缓存 + 失败重试退避。

标准库优先, 不引入第三方依赖。数据落盘到 userdata JSON, 脱敏后仅存数量/模型/时间
(绝不落盘 API Key 与提示词正文)。
"""
import hashlib
import json
import os
import random
import threading
import time

_LOCK = threading.Lock()


# ── 落盘位置 ──────────────────────────────────────────────────────────
def _userdata_dir():
    import state
    root = getattr(state, "APP_ROOT", "") or ""
    if root:
        d = os.path.join(root, "userdata")
    else:
        d = os.path.join(os.path.expanduser("~"), ".acrpa", "userdata")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d


def _cost_file():
    return os.path.join(_userdata_dir(), "ai_cost.json")


def _cache_file():
    return os.path.join(_userdata_dir(), "ai_cache.json")


def _load(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, type(default)) else default
    except Exception:
        return default


def _save(path, data):
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        pass


# ── token 记账 ────────────────────────────────────────────────────────
def record_usage(model, usage, kind="chat"):
    """按 usage 累计并持久化 (脱敏: 仅存数量/模型/时间)。"""
    if not usage:
        return
    try:
        pt = int(usage.get("prompt_tokens") or 0)
        ct = int(usage.get("completion_tokens") or 0)
    except Exception:
        return
    rec = {
        "model": model or "",
        "prompt_tokens": pt,
        "completion_tokens": ct,
        "kind": kind,
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    with _LOCK:
        data = _load(_cost_file(), [])
        data.append(rec)
        if len(data) > 500:
            data = data[-500:]
        _save(_cost_file(), data)


def cost_summary():
    """返回累计用量摘要 dict (calls/prompt_tokens/completion_tokens/by_model)。"""
    data = _load(_cost_file(), [])
    total_p = sum(int(r.get("prompt_tokens") or 0) for r in data)
    total_c = sum(int(r.get("completion_tokens") or 0) for r in data)
    by_model = {}
    for r in data:
        m = r.get("model", "unknown")
        by_model[m] = by_model.get(m, 0) + 1
    return {"calls": len(data), "prompt_tokens": total_p,
            "completion_tokens": total_c, "by_model": by_model}


# ── 提示词 / 结果缓存 ──────────────────────────────────────────────────
def cache_key(model, messages, temperature=0.2, max_tokens=1500, **extra):
    """消息 + 模型 + 参数哈希为键 (不落地明文)。"""
    h = hashlib.sha256()
    h.update((model or "").encode("utf-8"))
    h.update(json.dumps(messages, ensure_ascii=False, sort_keys=True).encode("utf-8"))
    h.update("|t={}|m={}".format(temperature, max_tokens).encode("utf-8"))
    for k in sorted(extra.keys()):
        h.update("|{}={}".format(k, extra[k]).encode("utf-8"))
    return h.hexdigest()


def cache_get(key):
    data = _load(_cache_file(), {})
    return data.get(key)


def cache_put(key, content):
    with _LOCK:
        data = _load(_cache_file(), {})
        data[key] = content
        if len(data) > 200:
            for old in list(data.keys())[:-200]:
                data.pop(old, None)
        _save(_cache_file(), data)


def cache_clear():
    _save(_cache_file(), {})


# ── 失败重试退避 ──────────────────────────────────────────────────────
def backoff_delay(attempt, base=1.0, cap=30.0):
    """指数退避 + 抖动 (429/超时重试用), 抖动不突破 cap 上限。"""
    exp = min(cap, base * (2 ** max(0, attempt - 1)))
    return min(cap, exp + random.uniform(0, 0.3 * exp))
