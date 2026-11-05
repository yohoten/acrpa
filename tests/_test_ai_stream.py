#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""AI 流式 / 成本记账 / 缓存 / 退避守卫。

用法:
    .venv\\Scripts\\python.exe -X utf8 tests\\_test_ai_stream.py

覆盖:
  1. chat_completions_stream 逐段解析 SSE (data: 行 / [DONE] / usage 记账);
  2. ai_cost.cache_key 确定性 + cache_put/cache_get 往返 + cache_clear;
  3. ai_cost.backoff_delay 指数退避 + 抖动有界;
  4. ai_cost.record_usage 脱敏累计 (仅数量/模型/时间, 不含密钥与提示词);
  5. generate_script_content 命中缓存直接返回 (不调 API)。

退出码: 0=全部通过 / 1=有失败。
"""
import os
import sys
import tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import ai_client  # noqa: E402
import ai_cost  # noqa: E402

_PASS, _FAIL = [], []


def check(cond, msg, detail=""):
    (_PASS if cond else _FAIL).append(msg)
    tail = ("  ({})".format(detail) if detail else "")
    print("[{}] {}{}".format("OK  " if cond else "FAIL", msg, tail))


# ── 1. SSE 流式解析 ──────────────────────────────────────────────────
class _FakeResp:
    status_code = 200
    text = ""

    def __init__(self, lines):
        self._lines = lines

    def iter_lines(self, decode_unicode=True):
        for line in self._lines:
            yield line

    def close(self):
        pass


def _build_client(lines):
    c = ai_client.AIClient("test-key")
    c.session.post = lambda *a, **k: _FakeResp(lines)
    return c


deltas = []
client = _build_client([
    'data: {"choices":[{"delta":{"content":"Hello"}}]}',
    'data: {"choices":[{"delta":{"content":" world"}}]}',
    'data: {"choices":[{"delta":{"content":"!"}}],"usage":{"prompt_tokens":12,"completion_tokens":3}}',
    'data: [DONE]',
])
out = client.chat_completions_stream("m", [{"role": "user", "content": "hi"}],
                                     on_delta=lambda t: deltas.append(t))
check(out == "Hello world!", "SSE 流式聚合完整正文", repr(out))
check(deltas == ["Hello", " world", "!"], "on_delta 逐段回调", repr(deltas))

# 非 data: 行 / 坏 JSON 行被跳过
deltas2 = []
client2 = _build_client([
    ': keep-alive',
    'data: {bad json',
    'data: {"choices":[{"delta":{"content":"ok"}}]}',
    'data: [DONE]',
])
out2 = client2.chat_completions_stream("m", [{"role": "user", "content": "hi"}],
                                       on_delta=lambda t: deltas2.append(t))
check(out2 == "ok" and deltas2 == ["ok"], "跳过非 data/坏 JSON 行", repr(out2))


# ── 2. 缓存键 / 往返 / 清空 ───────────────────────────────────────────
k1 = ai_cost.cache_key("m", [{"role": "user", "content": "hi"}], 0.3, 4000)
k2 = ai_cost.cache_key("m", [{"role": "user", "content": "hi"}], 0.3, 4000)
k3 = ai_cost.cache_key("m", [{"role": "user", "content": "bye"}], 0.3, 4000)
check(k1 == k2 and k1 != k3, "cache_key 确定性 + 对消息敏感", "{}/{}".format(k1[:8], k3[:8]))

with tempfile.TemporaryDirectory(prefix="acrpa_aicost_") as d:
    _orig_cost = ai_cost._cost_file
    _orig_cache = ai_cost._cache_file
    ai_cost._cost_file = lambda: os.path.join(d, "cost.json")
    ai_cost._cache_file = lambda: os.path.join(d, "cache.json")
    try:
        ai_cost.cache_clear()
        check(ai_cost.cache_get(k1) is None, "清空后缓存未命中")
        ai_cost.cache_put(k1, "cached-result")
        check(ai_cost.cache_get(k1) == "cached-result", "cache_put/get 往返命中")

        # ── 3. token 记账 ──
        ai_cost.record_usage("deepseek-chat",
                             {"prompt_tokens": 100, "completion_tokens": 50})
        ai_cost.record_usage("deepseek-chat",
                             {"prompt_tokens": 20, "completion_tokens": 10})
        s = ai_cost.cost_summary()
        check(s["calls"] == 2, "记账调用数累计", str(s["calls"]))
        check(s["prompt_tokens"] == 120 and s["completion_tokens"] == 60,
              "token 用量累计", "p={} c={}".format(s["prompt_tokens"], s["completion_tokens"]))
        raw = ""
        with open(ai_cost._cost_file(), "r", encoding="utf-8") as f:
            raw = f.read()
        check("api_key" not in raw.lower() and "secret" not in raw.lower(),
              "记账落盘脱敏 (不含密钥/提示词)")

        # ── 4. generate_script_content 缓存命中不调 API ──
        called = {"n": 0}
        class _NoopClient:
            def chat_completions(self, *a, **k):
                called["n"] += 1
                raise AssertionError("不应调用 API")
            def chat_completions_stream(self, *a, **k):
                called["n"] += 1
                raise AssertionError("不应调用 API")
        content, _resp = ai_client.generate_script_content(
            _NoopClient(), [{"role": "user", "content": "hi"}], "m",
            max_tokens=4000)
        check(content == "cached-result" and called["n"] == 0,
              "命中缓存直接返回, 不调 API", repr(content))
    finally:
        ai_cost._cost_file = _orig_cost
        ai_cost._cache_file = _orig_cache


# ── 5. 退避 ──────────────────────────────────────────────────────────
import random
random.seed(0)
d1 = ai_cost.backoff_delay(1, base=1.0)
d2 = ai_cost.backoff_delay(2, base=1.0)
d3 = ai_cost.backoff_delay(3, base=1.0)
check(d1 >= 1.0 and d2 >= 2.0 and d3 >= 4.0,
      "退避随 attempt 指数增长", "{:.2f}/{:.2f}/{:.2f}".format(d1, d2, d3))
check(ai_cost.backoff_delay(99, base=1.0, cap=30.0) <= 30.0,
      "退避有 cap 上限")

# ── 汇总 ─────────────────────────────────────────────────────────────
print("\n------------------------------------------------------------")
print("结果: 通过 {} / 失败 {}".format(len(_PASS), len(_FAIL)))
for f in _FAIL:
    print("  - {}".format(f))
sys.exit(1 if _FAIL else 0)
