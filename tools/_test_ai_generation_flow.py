# -*- coding: utf-8 -*-
"""AI 脚本生成链路回归（离线，不发真实请求）。

现场问题：用户点击「AI 生成脚本」得到

    生成失败
    发生未知错误：
    AI返回的内容为空或格式无效

本机实测根因（非推测）：当前端点背后是**推理模型**，它"想"的时候会把整个
max_tokens 预算烧在 reasoning_content 上，此时 HTTP 仍是 200 但正文为空。
同一提示词的实测数据：

    max_tokens=500  → finish_reason=length / content=  0 字符 / reasoning= 943 字符
    max_tokens=2000 → finish_reason=length / content=  0 字符 / reasoning=5379 字符  ← 应用原值
    max_tokens=1000 → finish_reason=stop   / content=593 字符 / reasoning=  0 字符
    max_tokens=8000 → finish_reason=stop   / content=640 字符 / reasoning=  0 字符

断言：
  G1 预算充足      : SCRIPT_MAX_TOKENS 足够大（防止有人改回 2000）
  G2 正常路径      : 有正文时不重试
  G3 空正文重试    : finish_reason=length / 有 reasoning → 放大预算重试一次并成功
  G4 重试仍为空    : 抛出 ValueError，且消息里带成因（不是"未知错误"）
  G5 非截断不重试  : 既无 length 也无 reasoning 的空正文不浪费第二次请求
  G6 成因描述      : empty_output_reason 汇总 finish_reason / 推理长度 / token 用量
  G7 HTTP 错误正文 : 400/401 等把服务端 error.message 带进异常消息
  G8 响应字段暴露  : ChatCompletion 暴露 finish_reason / reasoning_content / usage

用法: .venv\\Scripts\\python.exe -X utf8 tools\\_test_ai_generation_flow.py
退出码: 0=全部通过, 1=存在失败
"""
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "src"))

import ai_client as ac                              # noqa: E402

_PASS, _FAIL = [], []


def check(cond, msg):
    (_PASS if cond else _FAIL).append(msg)
    print("[{}] {}".format("OK  " if cond else "FAIL", msg))


class FakeMessage:
    def __init__(self, content):
        self.content = content


class FakeChoice:
    def __init__(self, content):
        self.message = FakeMessage(content)


class FakeResponse:
    """模拟 chat_completions 的返回值。"""

    def __init__(self, content="", finish_reason="stop", reasoning="", usage=None):
        self.choices = [FakeChoice(content)]
        self.finish_reason = finish_reason
        self.reasoning_content = reasoning
        self.usage = usage or {}


class FakeClient:
    """记录每次调用的 max_tokens，并按脚本返回响应。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def chat_completions(self, model, messages, temperature=0.2,
                         max_tokens=1500, timeout=60):
        self.calls.append(max_tokens)
        if not self.responses:
            raise AssertionError("FakeClient 响应已被取空（多调了一次？）")
        return self.responses.pop(0)


class StubSession:
    """替换 AIClient.session，用于测 HTTP 层。"""

    def __init__(self, status_code, payload):
        self.status_code = status_code
        self.payload = payload
        self.headers = {}

    def post(self, url, json=None, timeout=None):
        outer = self

        class R:
            status_code = outer.status_code
            reason = "Bad Request"
            text = ""

            def json(self):
                return outer.payload

        return R()


def t_budget_guard():
    print("\n── G1 预算充足 ──")
    check(ac.SCRIPT_MAX_TOKENS >= 4000,
          "SCRIPT_MAX_TOKENS = {} (≥4000, 推理模型需要余量)".format(ac.SCRIPT_MAX_TOKENS))
    check(ac.SCRIPT_RETRY_MULTIPLIER >= 2,
          "SCRIPT_RETRY_MULTIPLIER = {}".format(ac.SCRIPT_RETRY_MULTIPLIER))


def t_normal():
    print("\n── G2 正常路径 ──")
    c = FakeClient([FakeResponse("操作,参数1\n按键,down")])
    content, resp = ac.generate_script_content(c, [{"role": "user", "content": "x"}], "m")
    check(content.startswith("操作"), "返回正文: {!r}".format(content[:16]))
    check(c.calls == [ac.SCRIPT_MAX_TOKENS],
          "只调用一次且用默认预算 {}".format(c.calls))


def t_retry_on_truncation():
    print("\n── G3 空正文 → 放大预算重试 ──")
    c = FakeClient([
        FakeResponse("", finish_reason="length", reasoning="R" * 500),
        FakeResponse("操作,参数1\n按键,down", finish_reason="stop"),
    ])
    notes = []
    content, resp = ac.generate_script_content(
        c, [{"role": "user", "content": "x"}], "m",
        on_retry=lambda reason, bigger: notes.append((reason, bigger)))
    check(content.startswith("操作"), "重试后拿到正文")
    check(c.calls == [ac.SCRIPT_MAX_TOKENS, ac.SCRIPT_MAX_TOKENS * 2],
          "第二次调用使用了放大后的预算 {}".format(c.calls))
    check(notes and "length" in notes[0][0] and "推理过程" in notes[0][0],
          "on_retry 收到成因: {}".format(notes[0][0] if notes else "无"))

    # finish_reason=stop 但有 reasoning_content 也算"思考占用了预算"
    c2 = FakeClient([FakeResponse("", finish_reason="stop", reasoning="R" * 10),
                     FakeResponse("操作,参数1\n按键,down")])
    content2, _ = ac.generate_script_content(c2, [{"role": "user", "content": "x"}], "m")
    check(content2 and len(c2.calls) == 2, "仅有 reasoning 时同样重试")


def t_retry_exhausted():
    print("\n── G4 重试后仍为空 → 明确报错 ──")
    c = FakeClient([
        FakeResponse("", finish_reason="length", reasoning="R" * 800,
                     usage={"completion_tokens": 2000,
                            "completion_tokens_details": {"reasoning_tokens": 1780}}),
        FakeResponse("", finish_reason="length", reasoning="R" * 1600),
    ])
    try:
        ac.generate_script_content(c, [{"role": "user", "content": "x"}], "m")
        check(False, "应当抛出 ValueError")
    except ValueError as e:
        msg = str(e)
        check("finish_reason=length" in msg, "错误消息含 finish_reason")
        check("推理过程" in msg and "1780" in msg, "错误消息含推理规模/token 用量")
        check("重试后仍为空" in msg, "错误消息说明已重试过")
        check("未知错误" not in msg, "不再是含糊的『未知错误』")
        print("      消息: {}".format(msg[:120] + "…"))


def t_no_retry_when_not_truncated():
    print("\n── G5 非截断空正文不重试 ──")
    c = FakeClient([FakeResponse("", finish_reason="stop", reasoning="")])
    try:
        ac.generate_script_content(c, [{"role": "user", "content": "x"}], "m")
        check(False, "应当抛出 ValueError")
    except ValueError:
        check(len(c.calls) == 1, "只调用一次, 不浪费第二次请求 (calls={})".format(c.calls))


def t_reason_text():
    print("\n── G6 成因描述 ──")
    r = FakeResponse("", finish_reason="length", reasoning="R" * 42,
                     usage={"completion_tokens": 2000,
                            "completion_tokens_details": {"reasoning_tokens": 1500}})
    txt = ac.empty_output_reason(r)
    check("finish_reason=length" in txt and "42 字符" in txt
          and "1500 tokens" in txt and "completion_tokens=2000" in txt,
          "成因: {}".format(txt))
    check(ac.empty_output_reason(FakeResponse("", "")) == "",
          "完全无信息时返回空串 (不编造)")


def t_http_error_body():
    print("\n── G7 HTTP 错误带出服务端正文 ──")
    client = ac.AIClient("k", "https://example.invalid/v1")
    client.session = StubSession(400, {"error": {"message": "Model Not Exist"}})
    try:
        client.chat_completions("bad-model", [{"role": "user", "content": "x"}])
        check(False, "应当抛 APIError")
    except ac.APIError as e:
        check("Model Not Exist" in str(e), "400 错误消息含服务端原文: {}".format(str(e)[:80]))

    client.session = StubSession(401, {"error": {"message": "Authentication Fails"}})
    try:
        client.chat_completions("m", [{"role": "user", "content": "x"}])
        check(False, "应当抛 APIAuthError")
    except ac.APIAuthError as e:
        check("Authentication Fails" in str(e), "401 归为认证失败并带原文")


def t_response_fields():
    print("\n── G8 响应字段暴露 ──")
    client = ac.AIClient("k", "https://example.invalid/v1")
    client.session = StubSession(200, {
        "model": "m1",
        "choices": [{"finish_reason": "length",
                     "message": {"content": "", "reasoning_content": "R" * 7}}],
        "usage": {"completion_tokens": 10},
    })
    resp = client.chat_completions("m1", [{"role": "user", "content": "x"}])
    check(resp.finish_reason == "length", "finish_reason 暴露")
    check(resp.reasoning_content == "R" * 7, "reasoning_content 暴露")
    check(resp.usage.get("completion_tokens") == 10, "usage 暴露")
    check(resp.choices[0].message.content == "", "choices[0].message.content 兼容旧用法")


def main():
    print("=" * 68)
    print("AI 脚本生成链路回归 (离线)")
    print("=" * 68)
    t_budget_guard()
    t_normal()
    t_retry_on_truncation()
    t_retry_exhausted()
    t_no_retry_when_not_truncated()
    t_reason_text()
    t_http_error_body()
    t_response_fields()
    print("\n" + "=" * 68)
    print("通过 {} / 失败 {}".format(len(_PASS), len(_FAIL)))
    print("结论: {}".format("FAIL" if _FAIL else "PASS"))
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
