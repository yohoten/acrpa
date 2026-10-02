# -*- coding: utf-8 -*-
"""tools/_test_browser_download.py — 浏览器后端增强 P1-C「下载 / 上传」验收测试。

覆盖（无 playwright / 无真实浏览器亦可运行）：
  * match_download_name 纯函数：包含匹配；`/re/` 视为正则；空 pattern 恒真；非法正则 → False
  * _download_dir：参数 > browser_download_dir > CONFIG_PATH 同级 downloads
  * _download_target_path：overwrite=True 原样；overwrite=False 自动避让重名
  * session 下载队列：_on_download 去重；_pop_matching_download 命中取值 / 不消费不匹配项；
    _wait_download_state 命中即成功且不消费队列
  * 等待下载 facade（模拟下载对象，无浏览器）：落盘 + 绝对路径写回变量
  * 浏览器上传 facade（Fake locator，无浏览器）：单文件成功 / 缺文件 / 文件不存在
  * 命令注册断言：等待下载 / 浏览器上传 在 list_names()
  * handler 绑定断言（engine 已加载时）
  * state 新键默认值（P1 四项）
  * 真实浏览器下载落盘 / 上传设值 → SKIP 标注
  * AST 结构断言：match_download_name / _browser_wait_download / _browser_upload 存在
  * 模块级不存在 import playwright（懒加载契约）

只跑纯逻辑，不弹窗、不联网、不启动浏览器。退出码 0(全过/仅 WARN) / 1(存在 FAIL)。
输出行前缀: [OK] / [WARN] / [FAIL] / [SKIP]。
"""
import os
import ast
import sys
import shutil
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_FAILS = []
_WARNS = []


def ok(msg):
    print("[OK] " + msg)


def warn(msg):
    _WARNS.append(msg)
    print("[WARN] " + msg)


def skip(msg):
    print("[SKIP] " + msg)


def fail(msg):
    _FAILS.append(msg)
    print("[FAIL] " + msg)


def check(cond, msg):
    if cond:
        ok(msg)
    else:
        fail(msg)


def eq(actual, expected, msg):
    if actual == expected:
        ok("{} -> {!r}".format(msg, actual))
    else:
        fail("{}: 期望 {!r}，实际 {!r}".format(msg, expected, actual))


for _m in list(sys.modules):
    if _m == "playwright" or _m.startswith("playwright."):
        del sys.modules[_m]

import browser_backend as bb  # noqa: E402
import state  # noqa: E402

_pw_loaded = any(m == "playwright" or m.startswith("playwright.") for m in sys.modules)
check(not _pw_loaded, "import browser_backend 未触发 playwright 导入（懒加载契约）")

print("─" * 60)
# ══════════════════════════════════════════════════════════════════
# 1. match_download_name 纯函数
# ══════════════════════════════════════════════════════════════════
print("── match_download_name ──")
eq(bb.match_download_name("报表2025.xlsx", "报表"), True, "包含匹配命中")
eq(bb.match_download_name("报表2025.xlsx", "报表2024"), False, "包含匹配未命中")
eq(bb.match_download_name("a.txt", ""), True, "空 pattern 恒真")
eq(bb.match_download_name("a.txt", None), True, "None pattern 恒真")
eq(bb.match_download_name("", ""), True, "空文件名 + 空 pattern 恒真")
eq(bb.match_download_name("report_20250101.csv", "/report_\\d+/"), True, "/re/ 正则命中")
eq(bb.match_download_name("report_abc.csv", "/report_\\d+/"), False, "/re/ 正则未命中")
eq(bb.match_download_name("x.txt", "/[/"), False, "非法正则 → False（不抛异常）")
eq(bb.match_download_name(None, "x"), False, "None 文件名 + 非空 pattern → False")

print("─" * 60)
# ══════════════════════════════════════════════════════════════════
# 2. _download_dir / _download_target_path
# ══════════════════════════════════════════════════════════════════
print("── _download_dir / _download_target_path ──")
tmp = tempfile.mkdtemp(prefix="acrpa_dl_")
try:
    eq(bb._download_dir(tmp), tmp, "参数优先")
    _orig_state_dir = getattr(state, "BROWSER_DOWNLOAD_DIR", "")
    state.BROWSER_DOWNLOAD_DIR = tmp
    try:
        eq(bb._download_dir(""), tmp, "回退 browser_download_dir")
    finally:
        state.BROWSER_DOWNLOAD_DIR = _orig_state_dir
    _default_dir = bb._download_dir("")
    check(_default_dir.endswith("downloads"), "默认目录以 downloads 结尾: {}".format(_default_dir))

    fp = bb._download_target_path(tmp, "report.xlsx", True)
    eq(fp, os.path.join(tmp, "report.xlsx"), "overwrite=True 原样路径")

    open(os.path.join(tmp, "report.xlsx"), "w", encoding="utf-8").close()
    eq(bb._download_target_path(tmp, "report.xlsx", False),
       os.path.join(tmp, "report_1.xlsx"), "overwrite=False 避让为 _1")
    eq(bb._download_target_path(tmp, "report.xlsx", True),
       os.path.join(tmp, "report.xlsx"), "overwrite=True 仍原样")

    print("─" * 60)
    # ══════════════════════════════════════════════════════════════════
    # 3. session 下载队列（去重 / 匹配 / 不消费）
    # ══════════════════════════════════════════════════════════════════
    print("── session 下载队列 ──")


    class _FakeDownload(object):
        def __init__(self, name):
            self.suggested_filename = name
            self.saved_to = None

        def save_as(self, path):
            self.saved_to = path
            with open(path, "w", encoding="utf-8") as f:
                f.write("data")


    bb._download_queue[:] = []
    d1 = _FakeDownload("报表A.csv")
    d2 = _FakeDownload("readme.txt")
    bb._on_download(d1)
    bb._on_download(d1)  # 去重
    bb._on_download(d2)
    eq(len(bb._download_queue), 2, "_on_download 去重（同对象仅入队 1 次）")

    popped = bb._pop_matching_download("readme")
    eq(popped is d2, True, "_pop_matching_download 命中 readme")
    eq(len(bb._download_queue), 1, "命中项已被取出")

    bb._download_queue[:] = [d1]
    okk, code, _d = bb._wait_download_state(300, "报表")
    eq(okk, True, "_wait_download_state 命中即成功")
    eq(len(bb._download_queue), 1, "_wait_download_state 不消费队列（保留给落盘）")

    okk, code, _d = bb._wait_download_state(100, "不存在")
    eq(okk, False, "_wait_download_state 未命中 → 失败")
    eq(code, "TIMEOUT", "未命中 → TIMEOUT")

    print("─" * 60)
    # ══════════════════════════════════════════════════════════════════
    # 4. 等待下载 facade（模拟下载对象，无浏览器）
    # ══════════════════════════════════════════════════════════════════
    print("── 等待下载 facade（模拟队列，无浏览器） ──")


    class _FakePage(object):
        def __init__(self):
            self.context = None
            self.url = "about:blank"
            self._loc = _FakeLocator()

        def title(self):
            return "t"

        def on(self, evt, cb):
            pass

        def locator(self, sel):
            return self._loc

        def wait_for_event(self, evt, timeout=0):
            raise RuntimeError("no download event")

        def close(self):
            pass


    class _FakeLocator(object):
        def __init__(self):
            self.files = None

        def set_input_files(self, files, timeout=None):
            self.files = files


    class _ArgsRow(object):
        def __init__(self, args):
            self.args = args


    _orig_get_page = bb._get_page
    _orig_pages = list(bb._pages)
    _orig_active = bb._active_index
    _orig_page = bb._page
    _orig_ev = bb._engine_variables
    _orig_overwrite = getattr(state, "BROWSER_DOWNLOAD_OVERWRITE", True)
    _fake_vars = {}
    bb._get_page = lambda: None
    bb._engine_variables = lambda: _fake_vars
    bb._pages[:] = []
    bb._hooked_pages.clear()
    bb._active_index = -1
    bb._page = None
    try:
        fpage = _FakePage()
        bb._register_page(fpage)

        # 4.1 命中队列中的下载 → 落盘 + 写回变量
        bb._download_queue[:] = []
        dl = _FakeDownload("报表B.csv")
        bb._on_download(dl)
        _fake_vars.clear()
        r = bb._browser_wait_download(_ArgsRow([tmp, "报表", "5", "dl_path"]))
        check(r is None, "等待下载 成功 return None")
        eq(_fake_vars.get("dl_path"), os.path.abspath(os.path.join(tmp, "报表B.csv")),
           "绝对路径写回变量")
        check(os.path.exists(os.path.join(tmp, "报表B.csv")), "下载文件已落盘")
        eq(_fake_vars.get("browser_last_error"), "", "成功清空错误码")

        # 4.2 队列空 + wait_for_event 抛错 → DOWNLOAD_TIMEOUT
        bb._download_queue[:] = []
        _fake_vars.clear()
        r = bb._browser_wait_download(_ArgsRow([tmp, "", "0.1"]))
        check(r is False, "等待下载 超时 return False")
        eq(_fake_vars.get("browser_last_error"), "DOWNLOAD_TIMEOUT", "超时 → DOWNLOAD_TIMEOUT")

        # 4.3 浏览器上传：单文件成功
        upfile = os.path.join(tmp, "a.pdf")
        open(upfile, "w", encoding="utf-8").close()
        _fake_vars.clear()
        r = bb._browser_upload(_ArgsRow(["tag:input@type=file", upfile]))
        check(r is None, "浏览器上传 单文件 return None")
        eq(fpage._loc.files, upfile, "set_input_files 收到单文件路径")

        # 4.4 浏览器上传：缺文件路径 → INVALID_ARGUMENT
        _fake_vars.clear()
        r = bb._browser_upload(_ArgsRow(["#f"]))
        check(r is False, "浏览器上传 缺文件 return False")
        eq(_fake_vars.get("browser_last_error"), "INVALID_ARGUMENT", "缺文件 → INVALID_ARGUMENT")

        # 4.5 浏览器上传：文件不存在 → INVALID_ARGUMENT
        _fake_vars.clear()
        r = bb._browser_upload(_ArgsRow(["#f", os.path.join(tmp, "nope.pdf")]))
        check(r is False, "浏览器上传 文件不存在 return False")
        eq(_fake_vars.get("browser_last_error"), "INVALID_ARGUMENT", "文件不存在 → INVALID_ARGUMENT")

        # 4.6 浏览器上传：缺定位 → INVALID_ARGUMENT
        _fake_vars.clear()
        r = bb._browser_upload(_ArgsRow([""]))
        check(r is False, "浏览器上传 缺定位 return False")
        eq(_fake_vars.get("browser_last_error"), "INVALID_ARGUMENT", "缺定位 → INVALID_ARGUMENT")
    finally:
        bb._get_page = _orig_get_page
        bb._engine_variables = _orig_ev
        bb._pages[:] = _orig_pages
        bb._active_index = _orig_active
        bb._page = _orig_page
        bb._hooked_pages.clear()
        bb._download_queue[:] = []
        state.BROWSER_DOWNLOAD_OVERWRITE = _orig_overwrite

    skip("真实浏览器下载落盘（需 playwright + Chromium）—— 未在本环境执行")
    skip("真实浏览器上传设值（需 playwright + Chromium）—— 未在本环境执行")
    skip("等待元素 状态=download 真实命中（需 playwright）—— 未在本环境执行")

    print("─" * 60)
    # ══════════════════════════════════════════════════════════════════
    # 5. AST 结构断言
    # ══════════════════════════════════════════════════════════════════
    print("── AST 结构断言 ──")
    with open(os.path.join(SRC, "browser_backend.py"), "r", encoding="utf-8") as f:
        tree = ast.parse(f.read())
    module_level_imports = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            for a in node.names:
                module_level_imports.append(a.name)
        elif isinstance(node, ast.ImportFrom):
            module_level_imports.append(node.module or "")
    check(not any(n == "playwright" or n.startswith("playwright.") for n in module_level_imports),
          "模块级不存在 import playwright（AST）")

    func_names = {n.name for n in tree.body
                  if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    for fn in ["match_download_name", "_download_dir", "_download_target_path",
               "_on_download", "_pop_matching_download", "_wait_download_state",
               "_browser_wait_download", "_browser_upload"]:
        check(fn in func_names, "存在函数定义 {}".format(fn))

    print("─" * 60)
    # ══════════════════════════════════════════════════════════════════
    # 6. commands 注册 / handler 绑定 / state 新键
    # ══════════════════════════════════════════════════════════════════
    print("── commands 注册 / handler 绑定 / state 新键 ──")
    import commands  # noqa: E402

    names = commands.list_names()
    for n in ["等待下载", "浏览器上传"]:
        check(n in names, "注册表包含新命令 '{}'".format(n))

    try:
        import engine  # noqa: E402
        _engine_ok = True
    except Exception as e:  # pragma: no cover
        _engine_ok = False
        warn("无法 import engine，handler 绑定断言 SKIP: {}".format(e))
    if _engine_ok:
        for n in ["等待下载", "浏览器上传"]:
            check(commands.get_handler(n) is not None, "get_handler('{}') 非空".format(n))
    else:
        skip("handler 绑定断言（engine 未加载）")

    _expect = {
        "browser_download_dir": "",
        "browser_download_timeout": 15.0,
        "browser_download_overwrite": True,
        "browser_screenshot_dir": "",
    }
    for k, v in _expect.items():
        eq(getattr(state, k.upper(), "<missing>"), v, "state.{} 默认值".format(k.upper()))
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("─" * 60)
if _FAILS:
    print("结果: FAIL ({} 项失败, {} 项警告)".format(len(_FAILS), len(_WARNS)))
    for m in _FAILS:
        print("  - " + m)
    sys.exit(1)
else:
    print("结果: PASS (0 失败, {} 项警告)".format(len(_WARNS)))
    sys.exit(0)
