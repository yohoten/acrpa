#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""自然语言 → 工作流 JSON → 静态检查兜底守卫。

用法:
    .venv\\Scripts\\python.exe -X utf8 tests\\_test_nl_workflow.py

覆盖:
  1. _parse_workflow_json 解析裸 JSON / 代码块包裹 / 前后缀干扰 / 非法输入;
  2. generate_workflow_from_text 首轮流式生成 → flow_graph.analyze 9 项检查通过返回;
  3. 首轮产物带 error → 回填提示词进入修正轮 (有限轮次后返回 issues);
  4. 严格提示词约束 (仅输出 JSON, 不支持 function-calling)。

退出码: 0=全部通过 / 1=有失败。全部离线 (无网络)。
"""
import io
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import ai_client  # noqa: E402
import flow_graph  # noqa: E402

_PASS, _FAIL = [], []


def check(cond, msg, detail=""):
    (_PASS if cond else _FAIL).append(msg)
    tail = ("  ({})".format(detail) if detail else "")
    print("[{}] {}{}".format("OK  " if cond else "FAIL", msg, tail))


def _read(rel):
    with io.open(os.path.join(ROOT, rel), "r", encoding="utf-8") as f:
        return f.read()


# ── 1. _parse_workflow_json ──────────────────────────────────────────
GOOD = '{"name":"demo","steps":[{"type":"log","text":"hi"}]}'
p = ai_client._parse_workflow_json(GOOD)
check(p["name"] == "demo" and len(p["steps"]) == 1, "解析裸 JSON", str(p))

p2 = ai_client._parse_workflow_json('```json\n{}\n```'.format(GOOD))
check(p2["name"] == "demo", "解析 ```json 代码块包裹")

p3 = ai_client._parse_workflow_json('说明文字 {} 结尾'.format(GOOD))
check(p3["name"] == "demo", "剥离前后缀干扰文本")

for bad in ("", "no json here", "{}"):
    try:
        ai_client._parse_workflow_json(bad)
        check(False, "非法输入应抛 ValueError: {!r}".format(bad))
    except ValueError:
        check(True, "非法输入抛 ValueError: {!r}".format(bad[:20]))

# ── 2. 首轮流式生成 → 静态检查通过 ───────────────────────────────────
class _Resp:
    def __init__(self, content):
        self.choices = [_Choice(content)]


class _Choice:
    def __init__(self, content):
        self.message = _Msg(content)


class _Msg:
    def __init__(self, content):
        self.content = content


class _FakeClient:
    def __init__(self, contents):
        self._contents = list(contents)
        self.calls = 0

    def chat_completions_stream(self, **kwargs):
        self.calls += 1
        content = self._contents.pop(0) if self._contents else ""
        on_delta = kwargs.get("on_delta")
        if on_delta and content:
            on_delta(content)
        return content

    def chat_completions(self, **kwargs):
        self.calls += 1
        content = self._contents.pop(0) if self._contents else ""
        return _Resp(content)


GOOD_WF = ('{"name":"自动归档","steps":['
           '{"type":"script","path":"arch.xls"},'
           '{"type":"log","text":"done"}]}')
deltas = []
data, issues = ai_client.generate_workflow_from_text(
    _FakeClient([GOOD_WF]), "打开表格后记录日志", "deepseek-chat",
    on_delta=lambda t: deltas.append(t))
check(isinstance(data, dict) and len(data.get("steps", [])) == 2,
      "首轮流式生成合法工作流 (2 步)", str(data))
check(deltas and deltas[0] == GOOD_WF, "on_delta 收到流式正文", repr(deltas))
check(not any(i.get("level") == "error" for i in issues),
      "静态检查无 error 直接返回", str(issues))

# ── 3. 首轮输出非法 JSON → 回填提示词进入修正轮 ─────────────────────
# 首轮返回非 JSON 文本, 触发 _parse_workflow_json 抛错 → 修正轮给出合法工作流
BAD_WF = "抱歉，我先解释一下…（非 JSON 输出）"
client3 = _FakeClient([BAD_WF, GOOD_WF])
data3, issues3 = ai_client.generate_workflow_from_text(
    client3, "随便", "deepseek-chat")
check(client3.calls >= 2, "首轮非法 JSON 触发修正轮 (多次调用)", str(client3.calls))
check(len(data3.get("steps", [])) == 2, "修正轮后得到合法工作流", str(data3))

# ── 4. 严格提示词 + 源码断言 ─────────────────────────────────────────
src = _read("src/ai_client.py")
check("只输出一个 JSON 对象" in src, "严格提示词约束只输出 JSON")
check("def generate_workflow_from_text" in src, "提供自然语言→工作流生成入口")
check("flow_graph.analyze" in src, "用 flow_graph.analyze 静态检查兜底")
check("WF_MAX_FIX_ROUNDS = 3" in src, "修正轮次有限 (3)")

# ── 汇总 ─────────────────────────────────────────────────────────────
print("\n------------------------------------------------------------")
print("结果: 通过 {} / 失败 {}".format(len(_PASS), len(_FAIL)))
for f in _FAIL:
    print("  - {}".format(f))
sys.exit(1 if _FAIL else 0)
