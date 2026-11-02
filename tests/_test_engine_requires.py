# -*- coding: utf-8 -*-
"""engine 执行前能力门禁回归 (路线图 阶段二新增项①)。

断言:
  E1 hard 缺失   : degraded=hard 且能力非 READY → 拦截返回 missing_capability
                   (handler 不执行, attempts=0)
  E2 soft 缺失   : degraded=soft 且能力非 READY → 只提示, **仍执行** handler
  E3 core 放行   : ("core",) → 永不拦截
  E4 READY 放行  : 能力 READY → 正常执行
  E5 失败开放    : 能力未注册 → 视为 READY, 放行
  E6 开关关闭    : CAPABILITY_ENFORCE=False → hard 缺失也放行 (测试/桩化环境 opt-out)

用假能力 + 假命令 (handler 不产生真实屏幕副作用), 不触碰真实输入/找图。

用法: .venv\\Scripts\\python.exe -X utf8 tools\\_test_engine_requires.py
退出码: 0=全部通过, 1=存在失败
"""
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(BASE, "src")
sys.path.insert(0, SRC)

import commands                                         # noqa: E402
import capabilities                                     # noqa: E402
import engine as engine_mod                             # noqa: E402
from engine import engine                               # noqa: E402
from scriptdata import ScriptData                       # noqa: E402

_PASS, _FAIL = [], []


def check(cond, msg, detail=""):
    (_PASS if cond else _FAIL).append(msg)
    tail = ("  ({})".format(detail) if detail else "")
    print("[{}] {}{}".format("OK  " if cond else "FAIL", msg, tail))


def _reg_cap(cap_id, degraded, state):
    capabilities.register(capabilities.Capability(
        id=cap_id, label="T {}".format(cap_id), degraded=degraded,
        probe=lambda s=state: capabilities.ProbeResult(s, "stub")))
    capabilities.enable(cap_id)
    capabilities.refresh(cap_id)


_CALLS = {"n": 0}


def _ok_handler(row, script_dir):
    _CALLS["n"] += 1
    return True


def _reg_cmd(name, requires):
    commands.register(name, "自测用", "无参数", _ok_handler, requires=requires)


def main():
    print("=" * 68)
    print("engine 能力门禁自测")
    print("=" * 68)

    _reg_cap("t.hard", "hard", capabilities.CapState.MISSING)
    _reg_cap("t.soft", "soft", capabilities.CapState.MISSING)
    _reg_cap("t.ready", "hard", capabilities.CapState.READY)
    _reg_cmd("__t_hard__", ("t.hard",))
    _reg_cmd("__t_soft__", ("t.soft",))
    _reg_cmd("__t_ready__", ("t.ready",))
    _reg_cmd("__t_core__", ("core",))
    _reg_cmd("__t_unknown__", ("t.not_registered",))   # 未注册能力 → 失败开放

    script_dir = os.getcwd()
    orig_enforce = engine_mod.CAPABILITY_ENFORCE

    try:
        # ── E1 hard 缺失 → 拦截 ──
        print("\n── E1 hard 缺失 → 拦截 ──")
        _CALLS["n"] = 0
        r = engine.execute(ScriptData("__t_hard__", []), script_dir)
        check(r.ok is False, "hard 缺失 ok=False", repr(r.ok))
        check(r.code == "missing_capability",
              "code == missing_capability", r.code)
        check(r.attempts == 0, "attempts == 0 (未进 handler)", repr(r.attempts))
        check(_CALLS["n"] == 0, "handler 未被调用", repr(_CALLS["n"]))

        # ── E2 soft 缺失 → 放行并提示 ──
        print("\n── E2 soft 缺失 → 放行 ──")
        _CALLS["n"] = 0
        r = engine.execute(ScriptData("__t_soft__", []), script_dir)
        check(r.ok is True, "soft 缺失 ok=True (仅提示)", repr(r.ok))
        check(_CALLS["n"] == 1, "handler 被调用一次", repr(_CALLS["n"]))

        # ── E3 core 放行 ──
        print("\n── E3 core 放行 ──")
        _CALLS["n"] = 0
        r = engine.execute(ScriptData("__t_core__", []), script_dir)
        check(r.ok is True and _CALLS["n"] == 1, "core 命令正常执行", repr(r.ok))

        # ── E4 READY 放行 ──
        print("\n── E4 READY 放行 ──")
        _CALLS["n"] = 0
        r = engine.execute(ScriptData("__t_ready__", []), script_dir)
        check(r.ok is True and _CALLS["n"] == 1, "READY 能力命令正常执行", repr(r.ok))

        # ── E5 失败开放 (未注册能力) ──
        print("\n── E5 失败开放 ──")
        _CALLS["n"] = 0
        r = engine.execute(ScriptData("__t_unknown__", []), script_dir)
        check(r.ok is True and _CALLS["n"] == 1,
              "未注册能力视为 READY, 放行", repr(r.ok))

        # ── E6 开关关闭 → 放行 ──
        print("\n── E6 CAPABILITY_ENFORCE=False opt-out ──")
        engine_mod.CAPABILITY_ENFORCE = False
        _CALLS["n"] = 0
        r = engine.execute(ScriptData("__t_hard__", []), script_dir)
        check(r.ok is True and _CALLS["n"] == 1,
              "关闭门禁后 hard 缺失也放行", repr(r.ok))
    finally:
        engine_mod.CAPABILITY_ENFORCE = orig_enforce
        for _n in ("__t_hard__", "__t_soft__", "__t_ready__", "__t_core__",
                   "__t_unknown__"):
            commands._registry[:] = [x for x in commands._registry if x[0] != _n]
            commands._schemas.pop(_n, None)
            commands._requires.pop(_n, None)

    print()
    print("=" * 68)
    print("engine_requires 回归: 通过 {} 项, 失败 {} 项".format(len(_PASS), len(_FAIL)))
    print("=" * 68)
    if _FAIL:
        for m in _FAIL:
            print("  [FAIL] {}".format(m))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
