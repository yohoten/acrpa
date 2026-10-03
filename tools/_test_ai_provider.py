# -*- coding: utf-8 -*-
"""ACRPA 自定义 AI 提供商 / 模型 专项自测 —— 全自动、不弹窗、不联网。

运行:  python tools/_test_ai_provider.py
退出码: 0 = 全部断言通过（允许 [WARN]）；1 = 存在 [FAIL]

覆盖:
  A. list_providers/get_provider（6 预设 + 自定义合并 + 同名覆盖）
  B. 推断/解析：resolve_provider / resolve_model / resolve_base_url
  C. 密钥隔离：set/get/clear_provider_key + config.json 绝无明文/敏感字段
  D. test_connection 两段式探测（monkeypatch 内部 HTTP 钩子，不发网）
  E. create_client_active / create_client 旧签名
  F. settings_window.py AI 卡 AST 断言
  G. 守护断言：NetLink / Mini Bar / utils.themed / devlink_btn
  H. 依赖探测（缺失仅 [WARN]，不假通过）

注: 不 import ACRPA / settings_window（本机可能缺 pyautogui/xlrd/pyperclip），
    对源代码仅做 AST/文本静态断言。
"""
import ast
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

def _install_requests_stub():
    """本机可能未安装 requests：注入最小 stub，使离线自测仍可导入 ai_client。

    （生产 requirements.txt 已含 requests；此处仅让自测在缺依赖时也可运行。
      test_connection 的 HTTP 全部走被 monkeypatch 的钩子，绝不真实联网。）
    """
    try:
        import requests  # noqa: F401
        return False
    except Exception:
        pass
    import types
    mod = types.ModuleType("requests")
    exc = types.ModuleType("requests.exceptions")

    class RequestException(IOError):
        pass

    class Timeout(RequestException):
        pass

    class ConnectionError(RequestException):  # noqa: A001
        pass

    exc.RequestException = RequestException
    exc.Timeout = Timeout
    exc.ConnectionError = ConnectionError

    class Session(object):
        def __init__(self):
            self.headers = {}

    def _no_net(*a, **k):
        raise RuntimeError("network disabled in self-test")

    mod.exceptions = exc
    mod.Session = Session
    mod.get = _no_net
    mod.post = _no_net
    sys.modules["requests"] = mod
    sys.modules["requests.exceptions"] = exc
    return True


_REQUESTS_STUBBED = _install_requests_stub()

import state          # noqa: E402
import ai_client      # noqa: E402

FAILS = []
WARNS = []


def _p(status, msg):
    print("[{}] {}".format(status, msg))


def ok(name, cond, detail=""):
    if cond:
        _p("OK", name)
        return True
    FAILS.append(name + ((" :: " + detail) if detail else ""))
    _p("FAIL", name + ((" :: " + detail) if detail else ""))
    return False


def warn(msg):
    WARNS.append(msg)
    _p("WARN", msg)


def _read(rel):
    with open(os.path.join(ROOT, rel), "r", encoding="utf-8") as f:
        return f.read()


def _parse(rel):
    return ast.parse(_read(rel), filename=rel)


class _Resp(object):
    """极简伪造响应对象（供 test_connection 的钩子替换）。"""

    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("no json body")
        return self._payload


# ══════════════════════════════════════════════════════════════════════════
# A. 提供商表
# ══════════════════════════════════════════════════════════════════════════

def test_list_providers_and_lookup():
    provs = ai_client.list_providers()
    ids = [p["id"] for p in provs]
    presets = ["deepseek", "qwen", "openai", "ollama", "custom", "anthropic"]
    ok("list_providers 含全部预设 id（6 个）", all(pid in ids for pid in presets),
       "ids={}".format(ids))

    dp = ai_client.get_provider("deepseek")
    ok("get_provider('deepseek') base_url 正确",
       bool(dp) and dp["base_url"] == "https://api.deepseek.com/v1", str(dp))

    ad = ai_client.get_provider("anthropic")
    ok("anthropic 预设 openai_compatible=False",
       bool(ad) and ad.get("openai_compatible") is False, str(ad))

    # 合并自定义
    state.AI_CUSTOM_PROVIDERS = [{
        "id": "my-llm", "name": "My LLM",
        "base_url": "http://127.0.0.1:8000/v1", "models": ["m1", "m2"]}]
    ids2 = [p["id"] for p in ai_client.list_providers()]
    ok("list_providers 合并自定义提供商", "my-llm" in ids2, "ids={}".format(ids2))
    mp = ai_client.get_provider("my-llm")
    ok("自定义提供商字段解析正确",
       bool(mp) and mp["base_url"] == "http://127.0.0.1:8000/v1"
       and mp["models"] == ["m1", "m2"] and mp.get("custom") is True, str(mp))

    # 同名 id 以自定义为准
    state.AI_CUSTOM_PROVIDERS = [{
        "id": "deepseek", "name": "MyDS",
        "base_url": "http://x.deepseek/v1", "models": ["d1"]}]
    dp2 = ai_client.get_provider("deepseek")
    ok("同名 id 以自定义为准（覆盖预设）",
       bool(dp2) and dp2["base_url"] == "http://x.deepseek/v1", str(dp2))

    # 自定义条目里的敏感字段应被剔除
    state.AI_CUSTOM_PROVIDERS = [{
        "id": "leaky", "name": "L", "base_url": "http://l/v1",
        "models": ["x"], "api_key": "SHOULD-NOT-LEAK", "token": "T"}]
    lp = ai_client.get_provider("leaky")
    ok("自定义条目敏感字段被剔除",
       bool(lp) and "api_key" not in lp and "token" not in lp, str(lp))
    state.AI_CUSTOM_PROVIDERS = []


# ══════════════════════════════════════════════════════════════════════════
# B. 推断 / 解析
# ══════════════════════════════════════════════════════════════════════════

def test_inference():
    state.AI_PROVIDER = ""
    state.AI_BASE_URL = ""
    ok("推断 deepseek-chat → deepseek",
       ai_client.resolve_provider("deepseek-chat") == "deepseek")
    ok("推断 qwen-plus → qwen",
       ai_client.resolve_provider("qwen-plus") == "qwen")
    ok("推断 gpt-4o-mini → openai",
       ai_client.resolve_provider("gpt-4o-mini") == "openai")
    ok("推断 claude-3-5-sonnet-latest → anthropic",
       ai_client.resolve_provider("claude-3-5-sonnet-latest") == "anthropic")
    ok("llama3.1 + 空 base_url → custom",
       ai_client.resolve_provider("llama3.1") == "custom")
    ok("qwq 前缀 → qwen", ai_client.resolve_provider("qwq-32b") == "qwen")


def test_resolve_base_url():
    state.AI_PROVIDER = "ollama"
    state.AI_BASE_URL = ""
    b = ai_client.resolve_base_url()
    ok("AI_PROVIDER=ollama → resolve_base_url 含 11434", "11434" in b, b)

    state.AI_PROVIDER = ""
    state.AI_BASE_URL = "http://x/v1"
    ok("AI_BASE_URL 非空优先返回",
       ai_client.resolve_base_url() == "http://x/v1", ai_client.resolve_base_url())

    state.AI_BASE_URL = "https://api.deepseek.com/v1"
    ok("AI_BASE_URL 命中预设 → resolve_provider=deepseek",
       ai_client.resolve_provider("whatever") == "deepseek")

    state.AI_BASE_URL = "http://127.0.0.1:11434/v1"
    ok("base_url 含 11434 → resolve_provider=ollama",
       ai_client.resolve_provider("whatever") == "ollama")

    state.AI_BASE_URL = ""


def test_resolve_model():
    state.AI_PROVIDER = "deepseek"
    state.AI_MODEL = "custom-model"
    state.API_MODEL = "api-model"
    ok("resolve_model 一级：AI_MODEL 优先",
       ai_client.resolve_model() == "custom-model", ai_client.resolve_model())

    state.AI_MODEL = ""
    ok("resolve_model 二级：回退 API_MODEL",
       ai_client.resolve_model() == "api-model", ai_client.resolve_model())

    state.API_MODEL = ""
    ok("resolve_model 三级：回退预设首个模型",
       ai_client.resolve_model() == "deepseek-chat", ai_client.resolve_model())


# ══════════════════════════════════════════════════════════════════════════
# C. 密钥隔离
# ══════════════════════════════════════════════════════════════════════════

_ALLOWED_CUSTOM_KEYS = {"id", "name", "base_url", "models", "openai_compatible"}
_FORBIDDEN_FIELDS = ("key", "api_key", "token", "secret")


def _custom_entries_clean(text):
    try:
        data = json.loads(text)
    except Exception:
        return False
    for entry in (data.get("ai_custom_providers") or []):
        if not isinstance(entry, dict):
            return False
        for k in entry.keys():
            if k.lower() in _FORBIDDEN_FIELDS:
                return False
            if k not in _ALLOWED_CUSTOM_KEYS:
                return False
    return True


def test_key_isolation():
    state.AI_PROVIDER = "deepseek"
    state.AI_MODEL = ""
    state.API_MODEL = ""
    ok("set_provider_key 返回 True",
       ai_client.set_provider_key("deepseek", "sk-test-123") is True)
    ok("get_provider_key 读回一致",
       ai_client.get_provider_key("deepseek") == "sk-test-123")
    ok("resolve_api_key 命中凭据库",
       ai_client.resolve_api_key() == "sk-test-123", ai_client.resolve_api_key())

    # 写 config（含自定义提供商）→ 断言无明文/无敏感字段
    state.AI_CUSTOM_PROVIDERS = [{
        "id": "my-llm", "name": "My LLM",
        "base_url": "http://127.0.0.1:8000/v1", "models": ["m1"]}]
    state.save_config()
    with open(state.CONFIG_PATH, "r", encoding="utf-8") as f:
        text = f.read()
    ok("config.json 不含密钥明文 sk-test-123", "sk-test-123" not in text)
    ok("config.json 的 ai_custom_providers 内无 key/api_key/token/secret",
       _custom_entries_clean(text))
    ok("config.json 已写入 ai_provider 键", '"ai_provider"' in text)
    ok("config.json 已写入 ai_custom_providers 键", '"ai_custom_providers"' in text)

    # 删除凭据 → 回退 state.API_KEY
    state.API_KEY = "legacy-key"
    ai_client.clear_provider_key("deepseek")
    ok("clear_provider_key 后 resolve_api_key 回退 state.API_KEY",
       ai_client.resolve_api_key() == "legacy-key", ai_client.resolve_api_key())


# ══════════════════════════════════════════════════════════════════════════
# D. test_connection（不发网）
# ══════════════════════════════════════════════════════════════════════════

def test_test_connection():
    saved_get, saved_post = ai_client._http_get, ai_client._http_post
    calls = {"get": 0, "post": 0}
    try:
        # ① 200 → 成功
        calls["get"] = calls["post"] = 0
        ai_client._http_get = lambda url, headers=None, timeout=8: _Resp(200, {"data": [1, 2]})
        ai_client._http_post = lambda *a, **k: _Resp(200)
        r1 = ai_client.test_connection(provider="deepseek",
                                       base_url="https://api.deepseek.com/v1",
                                       model="deepseek-chat", api_key="k")
        ok("test_connection 200 → (True, ok...)", r1[0] is True and r1[1].startswith("ok"),
           str(r1))

        # ② 404 → 回退最小 chat/completions（第二次调用发生）
        calls["get"] = calls["post"] = 0

        def _g404(url, headers=None, timeout=8):
            calls["get"] += 1
            return _Resp(404)

        def _p200(url, json_body=None, headers=None, timeout=8):
            calls["post"] += 1
            return _Resp(200)

        ai_client._http_get = _g404
        ai_client._http_post = _p200
        r2 = ai_client.test_connection(provider="deepseek",
                                       base_url="https://api.deepseek.com/v1",
                                       model="deepseek-chat", api_key="k")
        ok("test_connection 404 → 回退 chat/completions 且成功",
           r2[0] is True and calls["get"] == 1 and calls["post"] == 1,
           "r2={} calls={}".format(r2, calls))

        # ③ socket.timeout → 超时错误
        def _g_timeout(url, headers=None, timeout=8):
            raise socket.timeout("timed out")

        ai_client._http_get = _g_timeout
        r3 = ai_client.test_connection(provider="deepseek",
                                       base_url="https://api.deepseek.com/v1",
                                       model="deepseek-chat", api_key="k")
        ok("test_connection 超时 → (False, 含'超时')",
           r3[0] is False and "超时" in r3[1], str(r3))

        # ④ 网络不可达
        def _g_conn(url, headers=None, timeout=8):
            raise ai_client.requests.exceptions.ConnectionError("no route")

        ai_client._http_get = _g_conn
        r4 = ai_client.test_connection(provider="deepseek",
                                       base_url="http://127.0.0.1:1/v1",
                                       model="deepseek-chat", api_key="k")
        ok("test_connection 连接错误 → (False, 含'网络不可达')",
           r4[0] is False and "网络不可达" in r4[1], str(r4))

        # ⑤ 401 → 密钥无效
        ai_client._http_get = lambda url, headers=None, timeout=8: _Resp(401)
        r5 = ai_client.test_connection(provider="deepseek",
                                       base_url="https://api.deepseek.com/v1",
                                       model="deepseek-chat", api_key="bad")
        ok("test_connection 401 → (False, 含'401')",
           r5[0] is False and "401" in r5[1], str(r5))

        # ⑥ anthropic → 直接返回不支持，且钩子未被调用
        calls["get"] = calls["post"] = 0

        def _g_should_not_call(url, headers=None, timeout=8):
            calls["get"] += 1
            return _Resp(200)

        ai_client._http_get = _g_should_not_call
        ai_client._http_post = _p200
        r6 = ai_client.test_connection(provider="anthropic",
                                       base_url="https://api.anthropic.com/v1",
                                       model="claude-3-5-sonnet-latest", api_key="k")
        ok("test_connection anthropic → (False, 含'不支持') 且不发请求",
           r6[0] is False and "不支持" in r6[1] and calls["get"] == 0 and calls["post"] == 0,
           "r6={} calls={}".format(r6, calls))
    finally:
        ai_client._http_get = saved_get
        ai_client._http_post = saved_post


# ══════════════════════════════════════════════════════════════════════════
# E. 客户端工厂
# ══════════════════════════════════════════════════════════════════════════

def test_client_factory():
    state.AI_PROVIDER = "deepseek"
    state.AI_MODEL = "deepseek-chat"
    state.AI_BASE_URL = "https://api.deepseek.com/v1"
    state.API_KEY = ""
    ai_client.set_provider_key("deepseek", "sk-test-123")
    try:
        cli = ai_client.create_client_active()
        ok("create_client_active 构造 AIClient 且 base_url 正确",
           isinstance(cli, ai_client.AIClient)
           and cli.base_url == "https://api.deepseek.com/v1",
           "base_url={}".format(getattr(cli, "base_url", None)))

        old = ai_client.create_client("sk-old", "deepseek-chat")
        ok("create_client 旧签名仍可用",
           isinstance(old, ai_client.AIClient)
           and old.base_url == "https://api.deepseek.com/v1",
           "base_url={}".format(getattr(old, "base_url", None)))

        # base_url 为空时回退 get_base_url(model)（与 create_client 一致）
        state.AI_PROVIDER = "custom"
        state.AI_BASE_URL = ""
        state.AI_MODEL = "deepseek-chat"
        cli2 = ai_client.create_client_active()
        ok("create_client_active base_url 为空回退 get_base_url(model)",
           cli2.base_url == ai_client.get_base_url("deepseek-chat"),
           "base_url={}".format(cli2.base_url))
    except Exception as e:  # noqa: BLE001
        ok("create_client_active/create_client 不抛异常", False, repr(e))
    finally:
        ai_client.clear_provider_key("deepseek")


# ══════════════════════════════════════════════════════════════════════════
# F. settings_window.py AI 卡 AST / 文本断言
# ══════════════════════════════════════════════════════════════════════════

def test_settings_window_ast():
    src = _read("src/settings_window.py")
    ok("AI 卡含 提供商 Combobox",
       "_prov_combo = ttk.Combobox(" in src and "提供商" in src)
    ok("AI 卡含 模型 Entry",
       "api_model_entry = tkinter.Entry(" in src)
    ok("AI 卡含 BaseURL Entry",
       "api_base_entry = tkinter.Entry(" in src)
    ok("AI 卡含 [测试连接] 按钮", 'text="测试连接"' in src)
    ok("AI 卡含 [添加自定义提供商] 按钮", 'text="添加自定义提供商"' in src)
    ok("_apply_map[\"ai\"] 仍注册",
       '_apply_map["ai"] = _apply_ai_settings' in src)
    ok('_track_card_vars("ai" 存在', '_track_card_vars("ai"' in src)
    ok("_apply_ai_settings 调用 set_provider_key",
       "set_provider_key" in src and "def _apply_ai_settings" in src)


# ══════════════════════════════════════════════════════════════════════════
# G. 守护断言
# ══════════════════════════════════════════════════════════════════════════

def test_guards():
    a_src = _read("src/ACRPA.py")
    tree = _parse("src/ACRPA.py")

    counts = {"open_devlink": 0, "set_control_hooks": 0,
              "start_netlink": 0, "_nl_hook_run": 0}
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef):
            if n.name == "open_devlink":
                counts["open_devlink"] += 1
            elif n.name == "_nl_hook_run":
                counts["_nl_hook_run"] += 1
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
            if n.func.attr in ("set_control_hooks", "start_netlink"):
                counts[n.func.attr] += 1
    for k, v in counts.items():
        ok("NetLink 守护 '{}' 出现 1 次".format(k), v == 1, "count={}".format(v))

    has_mb = any(isinstance(n, ast.FunctionDef) and n.name == "_mb_animate_width"
                 for n in ast.walk(tree))
    ok("Mini Bar 守护 _mb_animate_width 存在", has_mb)

    dest = None
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef) and n.name == "_destroy_mini_bar":
            dest = n
    has_cancel = False
    if dest is not None:
        for sub in ast.walk(dest):
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) \
                    and sub.func.attr == "after_cancel":
                has_cancel = True
    ok("_destroy_mini_bar 含 after_cancel", has_cancel)

    ok("utils.py 的 def themed( 仍在", "def themed(" in _read("src/utils.py"))

    dev_ok = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "devlink_btn"
                for t in node.targets):
            call = node.value
            if not isinstance(call, ast.Call):
                continue
            kw = {k.arg: k.value for k in call.keywords}
            text_v = getattr(kw.get("text"), "value", None)
            font_v = kw.get("font")
            # v0.1.29-beta「UI 美化」起: 图标改由命名图标字体承载 (FONT_ICON_MD),
            # 断言形态而非某个具体字形, 免得换个图标就假红。
            if isinstance(text_v, str) and text_v.strip() and isinstance(font_v, ast.Name):
                dev_ok = True
    ok("devlink_btn 为图标按钮 (text 非空 + 命名字体角色)", dev_ok)
    ok("ACRPA.py 已无『地球图标+互联』旧文案", "🌐 互联" not in a_src)


# ══════════════════════════════════════════════════════════════════════════
# H. 依赖探测
# ══════════════════════════════════════════════════════════════════════════

def probe_deps():
    code = "import pyautogui, xlrd, pyperclip"
    try:
        proc = subprocess.run([sys.executable, "-c", code],
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              cwd=ROOT)
        rc = proc.returncode
        tail = (proc.stdout or b"").decode("utf-8", "ignore").strip()
    except Exception as e:  # noqa: BLE001
        rc, tail = 1, str(e)
    if rc == 0:
        _p("OK", "依赖探测: pyautogui/xlrd/pyperclip 齐备")
    else:
        warn("依赖探测: python -c \"{}\" 失败 (rc={}) :: {}".format(
            code, rc, tail.splitlines()[-1] if tail else ""))
    if _REQUESTS_STUBBED:
        warn("依赖探测: 本机缺 requests，已注入离线 stub 运行自测（生产 requirements.txt 含 requests）")
    else:
        _p("OK", "依赖探测: requests 已安装（走真实 requests，HTTP 仍被 monkeypatch）")
    return rc


# ══════════════════════════════════════════════════════════════════════════
def main():
    print("=" * 64)
    print("ACRPA AI 提供商/模型自定义 自测 (不弹窗 / 不联网)")
    print("=" * 64)

    # ── 环境隔离：不写项目 config.json；备份并还原凭据库/内存状态 ──
    orig_config_path = state.CONFIG_PATH
    orig_api_key = getattr(state, "API_KEY", "")
    orig_provider = getattr(state, "AI_PROVIDER", "")
    orig_model = getattr(state, "AI_MODEL", "")
    orig_base = getattr(state, "AI_BASE_URL", "")
    orig_custom = list(getattr(state, "AI_CUSTOM_PROVIDERS", []) or [])
    orig_cred_api = state.cred_read(state._CRED_TARGET)
    orig_cred_ds = state.cred_read("ACRPA/ai_key/deepseek")
    tmpdir = tempfile.mkdtemp(prefix="acrpa_ai_test_")
    state.CONFIG_PATH = os.path.join(tmpdir, "config.json")
    try:
        test_list_providers_and_lookup()
        test_inference()
        test_resolve_base_url()
        test_resolve_model()
        test_key_isolation()
        test_test_connection()
        test_client_factory()
        test_settings_window_ast()
        test_guards()
    finally:
        try:
            state.CONFIG_PATH = orig_config_path
            state.API_KEY = orig_api_key
            state.AI_PROVIDER = orig_provider
            state.AI_MODEL = orig_model
            state.AI_BASE_URL = orig_base
            state.AI_CUSTOM_PROVIDERS = orig_custom
            if orig_cred_api:
                state.cred_write(state._CRED_TARGET, orig_cred_api)
            else:
                state.cred_delete(state._CRED_TARGET)
            if orig_cred_ds:
                state.cred_write("ACRPA/ai_key/deepseek", orig_cred_ds)
            else:
                state.cred_delete("ACRPA/ai_key/deepseek")
        except Exception:
            pass
        shutil.rmtree(tmpdir, ignore_errors=True)

    probe_deps()
    print("-" * 64)
    print("warns={}".format(len(WARNS)))
    if FAILS:
        print("FAIL ({} assertion(s) failed)".format(len(FAILS)))
        return 1
    print("PASS (all assertions OK)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
