# -*- coding: utf-8 -*-
"""P0-1 找图缓存 / P0-2 DD 内核驱动门禁 —— 回归自测（离线，不需要真实屏幕与驱动）。

对应路线图 §2 的两条已在源码复核过的缺陷：

P0-1 找图缓存
  · `_find_cached` 缓存命中时 `return (x, y)`（普通元组），而 docstring 与调用方都按
    pyautogui Point 的 `.x/.y` 取值 → **缓存命中必崩**（找图/区域找图/点图/区域点图
    在 5 秒 TTL 内重复搜索触发）。
  · 缓存键只用 `img_path`，忽略 confidence/region/grayscale → 区域限定搜索会被全屏
    命中污染（"明明限制了区域却点到别处"）。
  · `_image_cache` / `_CACHE_ORDER` 是无锁模块全局量，执行线程与 NetLink 远端执行
    可能并发读写。

P0-2 DD 内核驱动
  · `engine._write` 无条件 `get_dd_backend()`，而 `state.INPUT_MODE` / `USE_DD_DRIVER`
    在引擎里零读取 → 用户明确关闭的内核驱动依然加载并使用。
  · `_find_dd_dll` 把 `os.getcwd()` 纳入搜索路径 → DLL 植入面。
  · 加载前无任何完整性校验。

断言：
  C1 类型一致    : 缓存命中返回带 .x/.y 的对象，且不再触发真实找图
  C2 键含全部参数: confidence / region / grayscale 不同 → 不同缓存条目
  C3 真命中      : 同样的参数第二次不再调用 locateCenterOnScreen
  C4 TTL 过期    : 过期后重新找图
  C5 LRU 上限    : 条目数不超过 _CACHE_MAX
  C6 并发安全    : 多线程并发读写不抛异常、键不串
  D1 默认关闭    : 未启用时 _write 完全不碰 DD，走 PyAutoGUI
  D2 input_mode  : input_mode=dd 时才允许
  D3 开关优先    : use_dd_driver=True 时即使 input_mode=sendinput 也允许
  D4 命令模式    : 命令参数 mode=simulate 时仍不使用 DD（原语义不变）
  D5 后端门禁    : 未启用时 get_dd_backend 连 DLL 都不搜（不触发加载副作用）
  D6 搜索路径    : _find_dd_dll 不再包含 os.getcwd()
  D7 哈希固定    : dd_dll_sha256 不符时拒绝加载
  D8 静态预检    : 非 PE / 过小文件被拒绝
  D9 安装目录    : 非显式指定的安装目录外 DLL 被拒绝

用法: .venv\\Scripts\\python.exe -X utf8 tools\\_test_p0_cache_dd.py
退出码: 0=全部通过, 1=存在失败
"""
import os
import io
import re
import sys
import tempfile
import threading

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "src"))

import engine                                        # noqa: E402
import state                                         # noqa: E402
import dd_backend                                    # noqa: E402

_PASS, _FAIL = [], []


def check(cond, msg):
    (_PASS if cond else _FAIL).append(msg)
    print("[{}] {}".format("OK  " if cond else "FAIL", msg))


# ── 测试替身 ────────────────────────────────────────────────────────
class FakePoint:
    def __init__(self, x, y):
        self.x, self.y = x, y


class FakePA:
    """pyautogui 替身：记录调用，返回可预测坐标。"""

    def __init__(self):
        self.calls = []
        self.typed = []
        self.Point = FakePoint

    def locateCenterOnScreen(self, img, confidence=None, region=None, grayscale=None):
        self.calls.append((img, confidence, tuple(region) if region else None, grayscale))
        return FakePoint(100 + len(self.calls), 200 + len(self.calls))

    def typewrite(self, text, interval=None):
        self.typed.append(text)


class FakeDD:
    def __init__(self, ok=True):
        self.enabled = True
        self.ok = ok
        self.calls = []
        self.disabled_reason = ""

    def type_text(self, text, mode="auto"):
        self.calls.append((text, mode))
        return self.ok


class FakeRow:
    """模拟 engine 的行对象（同时提供 .args 与 row[i].value 两种取参形态）。"""

    class _Cell:
        def __init__(self, v):
            self.value = v

    def __init__(self, name, args):
        self.args = list(args)
        self.cells = [self._Cell(name)] + [self._Cell(a) for a in args]

    def __getitem__(self, i):
        return self.cells[i]


class patched:
    """临时替换 engine 模块上的属性，退出时还原。"""

    def __init__(self, **kw):
        self.kw = kw
        self.old = {}

    def __enter__(self):
        for k, v in self.kw.items():
            self.old[k] = getattr(engine, k)
            setattr(engine, k, v)
        return self

    def __exit__(self, *exc):
        for k, v in self.old.items():
            setattr(engine, k, v)
        return False


def fresh_cache():
    engine._image_cache.clear()
    engine._CACHE_ORDER.clear()


# ── P0-1 ────────────────────────────────────────────────────────────
def t_cache_type():
    print("\n── C1 缓存命中返回类型 ──")
    pa = FakePA()
    fresh_cache()
    with patched(get_pyautogui=lambda: pa, _has_cv2=lambda: False):
        eng = engine.ExecutionEngine()
        first = eng._find_cached("/tmp/a.png", 0.96)
        second = eng._find_cached("/tmp/a.png", 0.96)
    check(hasattr(first, "x") and hasattr(first, "y"), "首次返回带 .x/.y")
    check(hasattr(second, "x") and hasattr(second, "y"),
          "缓存命中返回带 .x/.y 的对象 (回归: 旧实现返回 tuple → AttributeError)")
    check((second.x, second.y) == (first.x, first.y), "命中坐标与首次一致")
    check(len(pa.calls) == 1, "第二次未触发真实找图 (calls={})".format(len(pa.calls)))


def t_cache_key():
    print("\n── C2 缓存键必须含全部参数 ──")
    pa = FakePA()
    fresh_cache()
    with patched(get_pyautogui=lambda: pa, _has_cv2=lambda: False):
        eng = engine.ExecutionEngine()
        eng._find_cached("/tmp/b.png", 0.96, region=(0, 0, 100, 100), grayscale=True)
        eng._find_cached("/tmp/b.png", 0.96, region=(0, 0, 200, 200), grayscale=True)  # 区域不同
        eng._find_cached("/tmp/b.png", 0.80, region=(0, 0, 100, 100), grayscale=True)  # 精度不同
        eng._find_cached("/tmp/b.png", 0.96, region=(0, 0, 100, 100), grayscale=False)  # 灰度不同
        n_after_variants = len(pa.calls)
        eng._find_cached("/tmp/b.png", 0.96, region=(0, 0, 100, 100), grayscale=True)  # 应命中首条
    check(n_after_variants == 4,
          "四种不同参数各查一次 (calls={})".format(n_after_variants))
    check(len(pa.calls) == 4, "回到原参数组合时命中缓存, 不再查询")
    check(len(engine._image_cache) == 4, "缓存里是 4 条独立条目 (旧实现只有 1 条)")


def t_cache_ttl_and_lru():
    print("\n── C4/C5 TTL 与 LRU ──")
    pa = FakePA()
    fresh_cache()
    with patched(get_pyautogui=lambda: pa, _has_cv2=lambda: False):
        eng = engine.ExecutionEngine()
        eng._find_cached("/tmp/c.png", 0.9)
        eng._find_cached("/tmp/c.png", 0.9)
        check(len(pa.calls) == 1, "TTL 内命中缓存")
        old_ttl = engine._CACHE_TTL
        engine._CACHE_TTL = 0.0
        try:
            eng._find_cached("/tmp/c.png", 0.9)
            check(len(pa.calls) == 2, "TTL 过期后重新找图")
        finally:
            engine._CACHE_TTL = old_ttl

        fresh_cache()
        for i in range(engine._CACHE_MAX + 15):
            eng._find_cached("/tmp/m{}.png".format(i), 0.9)
        check(len(engine._image_cache) <= engine._CACHE_MAX,
              "LRU 上限生效 ({} ≤ {})".format(len(engine._image_cache), engine._CACHE_MAX))
        check(len(engine._CACHE_ORDER) == len(engine._image_cache),
              "LRU 表与缓存条目一一对应 (无重复键堆积)")


def t_cache_concurrency():
    print("\n── C6 并发安全 ──")
    pa = FakePA()
    fresh_cache()
    errors = []

    with patched(get_pyautogui=lambda: pa, _has_cv2=lambda: False):
        eng = engine.ExecutionEngine()

        def worker(n):
            try:
                for i in range(40):
                    eng._find_cached("/tmp/t{}_{}.png".format(n, i % 5), 0.9)
            except Exception as e:                    # noqa: BLE001
                errors.append("{}: {}".format(type(e).__name__, e))

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
    check(not errors, "8 线程 × 200 次并发调用无异常 {}".format(errors[:2] or ""))
    check(len(engine._CACHE_ORDER) == len(set(engine._CACHE_ORDER)),
          "并发后 LRU 表无重复键")


# ── P0-2 ────────────────────────────────────────────────────────────
def _run_write(mode_arg, use_dd, input_mode, dd_ok=True):
    """跑一次 _write → (dd 调用次数, 实际输入方式, dd 收到的 mode)。"""
    pa = FakePA()
    dd = FakeDD(ok=dd_ok)
    calls = {"n": 0}

    def _fake_get_dd():
        calls["n"] += 1
        return dd

    old_flag, old_mode = state.USE_DD_DRIVER, state.INPUT_MODE
    state.USE_DD_DRIVER, state.INPUT_MODE = use_dd, input_mode
    engine._dd_gate_hint_logged = False
    try:
        with patched(get_pyautogui=lambda: pa, get_dd_backend=_fake_get_dd):
            engine.ExecutionEngine()._write(
                FakeRow("写入", ["hello", "0.01", mode_arg]), "")
    finally:
        state.USE_DD_DRIVER, state.INPUT_MODE = old_flag, old_mode
    how = "dd" if dd.calls else ("pyautogui" if pa.typed else "none")
    return calls["n"], how, (dd.calls[0][1] if dd.calls else "")


def t_dd_gate():
    print("\n── D1~D4 引擎侧门禁 ──")
    n, how, _ = _run_write("auto", False, "sendinput")
    check(n == 0 and how == "pyautogui",
          "D1 未启用时完全不碰 DD, 走 PyAutoGUI (dd_calls={}, how={})".format(n, how))

    n, how, dd_mode = _run_write("auto", False, "dd")
    check(n == 1 and how == "dd", "D2 input_mode=dd 时允许使用 DD (how={})".format(how))

    n, how, _ = _run_write("direct", True, "sendinput")
    check(n == 1 and how == "dd", "D3 use_dd_driver=True 优先于 input_mode")

    n, how, _ = _run_write("simulate", True, "dd")
    check(n == 0 and how == "pyautogui",
          "D4 命令参数 mode=simulate 时不使用 DD (原语义不变)")

    # DD 存在但输入失败 → 必须回退, 不能静默丢字
    pa = FakePA()
    dd = FakeDD(ok=False)
    state.USE_DD_DRIVER, state.INPUT_MODE = True, "dd"
    engine._dd_gate_hint_logged = False
    try:
        with patched(get_pyautogui=lambda: pa, get_dd_backend=lambda: dd):
            engine.ExecutionEngine()._write(FakeRow("写入", ["hello", "0.01", "auto"]), "")
    finally:
        state.USE_DD_DRIVER, state.INPUT_MODE = False, "sendinput"
    check(pa.typed == ["hello"], "DD 输入失败时回退 PyAutoGUI (未丢字)")


def t_dd_backend_gate():
    print("\n── D5/D6 后端侧门禁与搜索路径 ──")
    searched = []
    orig_find = dd_backend.DDBackend._find_dd_dll
    dd_backend.DDBackend._find_dd_dll = lambda self: (searched.append(1), None)[1]
    old_flag, old_mode = state.USE_DD_DRIVER, state.INPUT_MODE
    state.USE_DD_DRIVER, state.INPUT_MODE = False, "sendinput"
    try:
        dd_backend.reset_dd_backend()
        be = dd_backend.get_dd_backend()
        check(be.enabled is False, "未启用时后端 enabled=False")
        check(not searched,
              "未启用时连 DLL 搜索都不做 (内核驱动加载有副作用, 不能被顺手触发)")
        check(bool(be.disabled_reason), "给出了禁用原因: {}".format(be.disabled_reason))
    finally:
        dd_backend.DDBackend._find_dd_dll = orig_find
        dd_backend.reset_dd_backend()
        state.USE_DD_DRIVER, state.INPUT_MODE = old_flag, old_mode

    src = io.open(os.path.join(BASE, "src", "dd_backend.py"), encoding="utf-8").read()
    # 用 AST 判定"是否真的调用了 getcwd", 而不是子串匹配 ——
    # 注释里提到 os.getcwd() 不该被判失败（本测试第一版就栽在这上面）。
    import ast as _ast
    fn = next((n for n in _ast.walk(_ast.parse(src))
               if isinstance(n, _ast.FunctionDef) and n.name == "_find_dd_dll"), None)
    check(fn is not None, "找到 _find_dd_dll")
    getcwd_calls = [n for n in _ast.walk(fn) if isinstance(n, _ast.Call)
                    and isinstance(n.func, _ast.Attribute) and n.func.attr == "getcwd"]
    check(not getcwd_calls,
          "D6 _find_dd_dll 不再把当前工作目录纳入搜索 (防 DLL 植入)")
    check("lib" in src and "dd_driver" in src, "仍保留安装目录内的 lib/dd_driver")


def t_dd_preflight():
    print("\n── D7/D8/D9 加载前静态预检 ──")
    be = dd_backend.DDBackend(allow_load=False)
    tmp = tempfile.mkdtemp(prefix="acrpa_dd_")
    good = os.path.join(tmp, "good.dll")
    with open(good, "wb") as f:
        f.write(b"MZ" + b"\x00" * 8192)
    bad = os.path.join(tmp, "bad.dll")
    with open(bad, "wb") as f:
        f.write(b"NOTPE" + b"\x00" * 8192)
    small = os.path.join(tmp, "small.dll")
    with open(small, "wb") as f:
        f.write(b"MZ" + b"\x00" * 10)

    ok, why = be._preflight(good, explicit=True)
    check(ok, "合法 PE + 显式路径 → 通过 (说明: {})".format(why))

    ok, why = be._preflight(bad, explicit=True)
    check(not ok and "PE" in why, "D8 非 PE 文件被拒: {}".format(why))

    ok, why = be._preflight(small, explicit=True)
    check(not ok and "过小" in why, "D8 过小文件被拒: {}".format(why))

    ok, why = be._preflight(good, explicit=False)
    check(not ok and "安装目录" in why, "D9 安装目录外 + 非显式指定 → 拒绝: {}".format(why))

    old = getattr(state, "DD_DLL_SHA256", "")
    try:
        state.DD_DLL_SHA256 = "0" * 64
        ok, why = be._preflight(good, explicit=True)
        check(not ok and "sha256" in why, "D7 哈希不符时拒绝加载: {}".format(why))
        import hashlib
        state.DD_DLL_SHA256 = hashlib.sha256(open(good, "rb").read()).hexdigest()
        ok, why = be._preflight(good, explicit=True)
        check(ok and "sha256" in why, "D7 哈希匹配时放行: {}".format(why))
    finally:
        state.DD_DLL_SHA256 = old


def t_dd_search_paths_source():
    print("\n── D6b 引擎侧调用点 ──")
    src = io.open(os.path.join(BASE, "src", "engine.py"), encoding="utf-8").read()
    seg = src[src.index("def _write"):]
    seg = seg[:seg.index("def _wait")] if "def _wait" in seg else seg
    check("dd_allowed()" in seg, "_write 内部先过 dd_allowed() 门禁")
    check(re.search(r"if mode in \[\"auto\", \"direct\"\]", seg) is not None,
          "DD 仅在 auto/direct 模式下参与")
    check("_dd_gate_hint_logged" in src, "未启用提示只打一次 (不刷屏)")


def main():
    print("=" * 70)
    print("P0-1 找图缓存 / P0-2 DD 驱动门禁 回归")
    print("=" * 70)
    t_cache_type()
    t_cache_key()
    t_cache_ttl_and_lru()
    t_cache_concurrency()
    t_dd_gate()
    t_dd_backend_gate()
    t_dd_preflight()
    t_dd_search_paths_source()
    print("\n" + "=" * 70)
    print("通过 {} / 失败 {}".format(len(_PASS), len(_FAIL)))
    print("结论: {}".format("FAIL" if _FAIL else "PASS"))
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
