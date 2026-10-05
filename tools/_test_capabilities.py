# -*- coding: utf-8 -*-
"""能力四态注册表回归 (路线图 阶段二新增项①: `src/capabilities.py`)。

断言:
  C1 四态判定   : 假 probe 覆盖 READY / MISSING / BROKEN / DISABLED
  C2 失败开放   : probe 缺失 / 抛异常 / 未注册能力 → READY
  C3 幂等/缓存  : 同一能力重复 state() 只探一次; refresh() 后重探
  C4 线程安全   : 多线程并发 state() 不抛异常且结果一致
  C5 覆盖       : all_states() 覆盖首期 4 个能力
  C6 disable    : disable()→DISABLED; enable()→恢复重探
  C7 require_reason / label / degraded 辅助
  C8 纯 stdlib  : `import capabilities` 不加载 tkinter/pyautogui

用法: .venv\\Scripts\\python.exe -X utf8 tools\\_test_capabilities.py
退出码: 0=全部通过, 1=存在失败
"""
import os
import subprocess
import sys
import threading

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(BASE, "src")
sys.path.insert(0, SRC)

import capabilities                                     # noqa: E402
from capabilities import CapState, Capability, ProbeResult   # noqa: E402

_PASS, _FAIL = [], []


def check(cond, msg, detail=""):
    (_PASS if cond else _FAIL).append(msg)
    tail = ("  ({})".format(detail) if detail else "")
    print("[{}] {}{}".format("OK  " if cond else "FAIL", msg, tail))


def _boom():
    raise ValueError("boom")


def _reg(cap_id, probe, degraded="hard", label=None):
    capabilities.register(Capability(id=cap_id, label=label or cap_id,
                                     degraded=degraded, probe=probe))
    capabilities.enable(cap_id)          # 清 DISABLED + 清缓存
    capabilities.refresh(cap_id)


def main():
    print("=" * 68)
    print("capabilities 四态注册表自测")
    print("=" * 68)

    # ── C1/C2 四态 + 失败开放 ──
    print("\n── C1/C2 四态判定 + 失败开放 ──")
    _reg("t.ready", lambda: ProbeResult(CapState.READY, "ok"))
    _reg("t.missing", lambda: False)
    _reg("t.broken", lambda: ProbeResult(CapState.BROKEN, "有痕迹但自检失败"))
    _reg("t.disabled", lambda: True)
    _reg("t.passthru", lambda: True)              # 用于 disable 测试
    _reg("t.boom", _boom)
    _reg("t.noprobe", None)

    check(capabilities.state("t.ready") == CapState.READY, "probe 真 → READY")
    check(capabilities.state("t.missing") == CapState.MISSING, "probe 假 → MISSING")
    check(capabilities.state("t.broken") == CapState.BROKEN,
          "probe 返回 BROKEN → BROKEN")
    check(capabilities.state("t.boom") == CapState.READY,
          "probe 抛异常 → 失败开放 READY")
    check(capabilities.state("t.noprobe") == CapState.READY,
          "probe 缺失 → 失败开放 READY")
    check(capabilities.state("t.unregistered") == CapState.READY,
          "未注册能力 → 失败开放 READY")

    # ── C6 disable / enable ──
    print("\n── C6 用户显式关闭 ──")
    capabilities.disable("t.passthru")
    check(capabilities.state("t.passthru") == CapState.DISABLED,
          "disable() → DISABLED")
    capabilities.enable("t.passthru")
    check(capabilities.state("t.passthru") == CapState.READY,
          "enable() → 恢复重探为 READY")

    # ── C3 幂等 / 缓存 ──
    print("\n── C3 幂等 + 缓存 ──")
    calls = {"n": 0}

    def _counting():
        calls["n"] += 1
        return ProbeResult(CapState.READY, "counted")

    _reg("t.count", _counting)
    capabilities.state("t.count")
    capabilities.state("t.count")
    capabilities.state("t.count")
    check(calls["n"] == 1, "重复 state() 只探一次 (calls={})".format(calls["n"]))
    capabilities.refresh("t.count")
    capabilities.state("t.count")
    check(calls["n"] == 2, "refresh() 后重探 (calls={})".format(calls["n"]))

    # ── C4 线程安全 ──
    print("\n── C4 线程安全 ──")
    _reg("t.thread", lambda: ProbeResult(CapState.READY, "t"))
    results, errors = [], []

    def _worker():
        try:
            for _ in range(50):
                results.append(capabilities.state("t.thread"))
        except Exception as e:            # noqa: BLE001
            errors.append(repr(e))

    threads = [threading.Thread(target=_worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    check(not errors, "8 线程并发 state() 无异常", str(errors[:2]))
    check(results and all(r == CapState.READY for r in results),
          "并发结果一致且均为 READY ({} 次)".format(len(results)))

    # ── C5 all_states 覆盖 ──
    print("\n── C5 all_states 覆盖首期 4 能力 ──")
    st = capabilities.all_states()
    need = {"cv.match", "browser.playwright", "ocr.paddle_dll", "input.dd"}
    check(need <= set(st.keys()), "all_states() 含全部 4 个能力 (缺: {})".format(
        need - set(st.keys())))
    check(all(isinstance(v, CapState) for v in st.values()),
          "all_states() 值均为 CapState")

    # ── C7 辅助 ──
    print("\n── C7 label / degraded / require_reason ──")
    check(capabilities.label("cv.match").startswith("🖼"), "label('cv.match') 有图标")
    check(capabilities.degraded("cv.match") == "hard", "degraded('cv.match') == hard")
    check(capabilities.degraded("ocr.paddle_dll") == "soft",
          "degraded('ocr.paddle_dll') == soft")
    check(capabilities.degraded("t.unregistered") == "hard",
          "未注册能力 degraded 默认 hard")
    check("boom" in capabilities.require_reason("t.boom"),
          "require_reason 记录探测异常: {}".format(capabilities.require_reason("t.boom")))

    # ── C8 纯 stdlib: 子进程不加载 GUI ──
    print("\n── C8 import capabilities 不加载 GUI ──")
    code = ("import sys; sys.path.insert(0, {src!r}); "
            "import capabilities; "
            "print('TK=%s' % ('tkinter' in sys.modules)); "
            "print('PA=%s' % ('pyautogui' in sys.modules)); "
            "print('ENGINE=%s' % ('engine' in sys.modules))").format(src=SRC)
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    p = subprocess.run([sys.executable, "-X", "utf8", "-c", code], cwd=BASE,
                       env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       timeout=30)
    out = (p.stdout or b"").decode("utf-8", "replace")
    check(p.returncode == 0, "子进程 import capabilities 正常退出 (rc={})".format(
        p.returncode), (p.stderr or b"").decode("utf-8", "replace")[-200:])
    check("TK=False" in out, "import capabilities 未加载 tkinter", out.strip())
    check("PA=False" in out, "import capabilities 未加载 pyautogui", out.strip())
    check("ENGINE=False" in out, "import capabilities 未加载 engine", out.strip())

    print()
    print("=" * 68)
    print("capabilities 回归: 通过 {} 项, 失败 {} 项".format(len(_PASS), len(_FAIL)))
    print("=" * 68)
    if _FAIL:
        for m in _FAIL:
            print("  [FAIL] {}".format(m))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
