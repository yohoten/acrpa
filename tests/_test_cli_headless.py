#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""CLI / 无头模式 + JSON 报告 回归自测 (路线图 §6.5)。

被验证的实现: ``src/cli.py`` (parse_args / main) 与 ``run.py`` 的接线分支。

覆盖:
  A  纯解析   : parse_args 各分支 —— 无 --script → None; --script / --var(累积) /
                --json-report / --exit-code / --quiet; ``--opt=value`` 形式;
                未知开关 (含 --headless) 被忽略; --script 缺值 → None。
  B  纯净度   : 子进程 ``import cli`` 后 sys.modules 中无 tkinter (且未建窗),
                并导出 parse_args/main (与 _test_import_side_effect_free 同款做法)。
  C  端到端   : tempfile 造真实 .acrpas 脚本 (复用 script_io 写入 / cli 读回),
                仅用无头安全命令 (设置变量 / 数学运算 / 等待 0):
                  C1 成功   : status=success, failed_row/error=null, 报告存在且为合法 JSON,
                             逐行 ok, 退出码 0; 并验证 --var 预置变量对脚本可见。
                  C2 失败   : 未注册命令 → status=failed, failed_row=2, error 非空;
                             --exit-code → 1; 未给 --exit-code → 0。
                  C3 用法错 : 脚本不存在 → 2; 缺 --script → 2; --var 非法 → 2。
  D  静态接线 : run.py 文本含 ``cli.main(`` 与 ``parse_args(``, 且仍保留 GUI 启动路径
                (app.main() 且会进 Tk 主循环)。

不需要显示器 / 不建任何 Tk 窗口 (B 用子进程断言, C 全程 stdout 重定向到 StringIO)。

用法: .venv\\Scripts\\python.exe -X utf8 tools\\_test_cli_headless.py
退出码: 0=全部通过 / 1=有失败。
"""
import io
import json
import os
import subprocess
import sys
import tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

_PASS, _FAIL, _WARN = [], [], []


def check(cond, msg, detail=""):
    (_PASS if cond else _FAIL).append(msg)
    tail = ("  ({})".format(detail) if detail else "")
    print("[{}] {}{}".format("OK  " if cond else "FAIL", msg, tail))


def warn(msg, detail=""):
    _WARN.append(msg)
    print("[WARN] {}{}".format(msg, "  ({})".format(detail) if detail else ""))


import cli  # noqa: E402  (A/B/D 的被测对象; 导入必须零副作用)


# ── A. 纯解析 ────────────────────────────────────────────────────────
def t_parse():
    print("\n── A. parse_args 各分支 ──")
    check(cli.parse_args([]) is None, "空 argv → None (回退 GUI)")
    check(cli.parse_args(["--quiet"]) is None, "只有 --quiet (无 --script) → None")
    check(cli.parse_args(["--exit-code"]) is None, "只有 --exit-code (无 --script) → None")
    check(cli.parse_args(["--script"]) is None, "--script 缺值 → None (视为未给脚本)")

    o = cli.parse_args(["--script", "a.acrpas"])
    check(o is not None and o["script"] == "a.acrpas", "--script 取值正确", repr(o))
    check(o is not None and o["exit_code"] is False and o["quiet"] is False
          and o["vars"] == [] and o["json_report"] is None, "未给的开关保持默认")

    o = cli.parse_args(["--script=t.acrpas", "--var", "a=1", "--var", "b=2",
                        "--var=c=3", "--exit-code", "--quiet"])
    check(o is not None and o["script"] == "t.acrpas", "--script=值 形式", str(o))
    check(o is not None and o["vars"] == ["a=1", "b=2", "c=3"],
          "--var 累积 (含 --var=K=V 形式)", str(o and o["vars"]))
    check(o is not None and o["exit_code"] is True and o["quiet"] is True,
          "--exit-code / --quiet 正确置位")

    o = cli.parse_args(["--script", "s.acrpas", "--json-report", "r.json"])
    check(o is not None and o["json_report"] == "r.json",
          "--json-report 取值", str(o and o["json_report"]))

    o = cli.parse_args(["--script", "s.acrpas", "--json-report=out.json",
                        "--headless", "--unknown-flag", "stray"])
    check(o is not None and o["json_report"] == "out.json",
          "--json-report=值 + 未知开关被忽略 (含 --headless)")

    # --var 取值规则: 与脚本内「设置变量」命令同款 (数值转 int/float, 其余保留字符串)
    vals, bad = cli._parse_vars(["a=1", "b=2.5", "c=x", "d=007", "e="])
    check(bad is None and vals == {"a": 1, "b": 2.5, "c": "x", "d": 7, "e": ""},
          "--var 取值: 数值转 int/float, 其余保留字符串", str(vals))
    _, bad2 = cli._parse_vars(["novalue"])
    check(bad2 == "novalue", "--var 非法项 (缺 '=') 被报出", repr(bad2))
    _, bad3 = cli._parse_vars(["=1"])
    check(bad3 == "=1", "--var 非法项 (空变量名) 被报出", repr(bad3))


# ── B. 纯净度 ────────────────────────────────────────────────────────
def _child(code, timeout=60):
    """子进程执行一段 python → (rc|None(超时), stdout, stderr)。"""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        p = subprocess.run([sys.executable, "-X", "utf8", "-c", code],
                           cwd=ROOT, env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, "", "TIMEOUT after {}s".format(timeout)
    return (p.returncode,
            (p.stdout or b"").decode("utf-8", "replace"),
            (p.stderr or b"").decode("utf-8", "replace"))


def t_purity():
    print("\n── B. import cli 纯净度 (不加载 tkinter / 不建窗) ──")
    code = ("import sys; sys.path.insert(0, {src!r}); "
            "import cli; "
            "print('TK=%s' % ('tkinter' in sys.modules)); "
            "print('API=%s' % bool(hasattr(cli, 'parse_args') and hasattr(cli, 'main'))); "
            "print('NO_TK_ATTR=%s' % (not any(hasattr(cli, n) for n in ('root', 'win'))))"
            ).format(src=SRC)
    rc, out, err = _child(code)
    check(rc == 0, "子进程 import cli 正常退出 (rc={})".format(rc), (err or "").strip()[-200:])
    check("TK=False" in out, "import cli 后 sys.modules 无 tkinter", out.strip().replace("\n", " "))
    check("API=True" in out, "cli 导出 parse_args / main")
    check("NO_TK_ATTR=True" in out, "cli 模块上无 root/win 之类的窗口对象")


# ── C. 端到端 ────────────────────────────────────────────────────────
def _make_script(dirpath, name, rows):
    """用现有 script_io 写 .acrpas (复用既有实现, 不新写解析器)。"""
    import script_io
    path = os.path.join(dirpath, name)
    script_io.save_acrpas(path, rows)
    return path


def _load_report(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def t_e2e_success():
    print("\n── C1. 端到端: 成功 (无头安全命令) ──")
    import engine as engine_mod
    from scriptdata import ScriptData

    with tempfile.TemporaryDirectory(prefix="acrpa_cli_ok_") as d:
        script = _make_script(d, "ok.acrpas", [
            ScriptData("设置变量", ["a", "1"]),
            ScriptData("数学运算", ["${seed} + ${a}", "b"]),   # seed 由 --var 预置
            ScriptData("等待", ["0"]),
        ])
        report_path = os.path.join(d, "report.json")
        out = io.StringIO()
        rc = cli.main(["--script", script, "--var", "seed=7",
                       "--json-report", report_path, "--exit-code"], stdout=out)

        check(rc == 0, "成功脚本 + --exit-code → 退出码 0", repr(rc))
        check(os.path.isfile(report_path), "JSON 报告文件已生成")
        try:
            rep = _load_report(report_path)
        except Exception as e:
            rep = {}
            warn("报告不是合法 JSON", "{}: {}".format(type(e).__name__, e))

        check(rep.get("status") == "success", "status == success", str(rep.get("status")))
        check(rep.get("failed_row") is None and rep.get("error") is None,
              "failed_row / error 均为 null", repr((rep.get("failed_row"), rep.get("error"))))
        check(rep.get("total_rows") == 3, "total_rows == 3", repr(rep.get("total_rows")))
        check(rep.get("executed_rows") == 3, "executed_rows == 3", repr(rep.get("executed_rows")))
        check(isinstance(rep.get("duration_ms"), int) and rep["duration_ms"] >= 0,
              "duration_ms 为整数", repr(rep.get("duration_ms")))
        check(bool(rep.get("started")) and bool(rep.get("finished")),
              "started / finished 非空", "{} / {}".format(rep.get("started"), rep.get("finished")))
        check(os.path.abspath(script) == rep.get("script"), "script 为脚本绝对路径",
              str(rep.get("script")))

        rows = rep.get("rows") or []
        check(len(rows) == 3, "rows 逐行 3 条", repr(len(rows)))
        check([r.get("status") for r in rows] == ["ok", "ok", "ok"],
              "逐行状态全 ok", str([r.get("status") for r in rows]))
        check(rows and rows[0].get("index") == 0, "rows[].index 为 0 基",
              repr(rows[0].get("index") if rows else None))
        check(rows and rows[1].get("cmd") == "数学运算", "rows[].cmd 为命令名",
              repr(rows[1].get("cmd") if len(rows) > 1 else None))
        check(all("message" in r for r in rows), "每条 rows 都有 message 字段")

        # --var 真的写进了引擎变量表 (${seed} + ${a} == 7 + 1 == 8)
        check(engine_mod.engine.variables.get("b") == 8,
              "--var 预置变量对脚本可见 (${seed}+${a} == 8)",
              repr(engine_mod.engine.variables.get("b")))
        check("success" in out.getvalue(), "stdout 含人类可读结果行",
              (out.getvalue().strip().splitlines() or [""])[-1])


def t_e2e_failure():
    print("\n── C2. 端到端: 失败 (未注册命令) ──")
    import engine as engine_mod
    from scriptdata import ScriptData

    with tempfile.TemporaryDirectory(prefix="acrpa_cli_bad_") as d:
        script = _make_script(d, "bad.acrpas", [
            ScriptData("设置变量", ["a", "1"]),
            ScriptData("__该命令不存在__", ["x"]),
        ])
        report_path = os.path.join(d, "report.json")

        # 失败现场留存会往 screenshots/ 落 JSON+PNG, 对自测无价值 → 临时关闭
        # (engine.FAILURE_CONTEXT_ENABLED 就是为自测/性能场景提供的开关)
        orig = engine_mod.FAILURE_CONTEXT_ENABLED
        engine_mod.FAILURE_CONTEXT_ENABLED = False
        try:
            out1 = io.StringIO()
            rc1 = cli.main(["--script", script, "--exit-code",
                            "--json-report", report_path], stdout=out1)
            check(rc1 == 1, "失败脚本 + --exit-code → 退出码 1", repr(rc1))
            try:
                rep = _load_report(report_path)
            except Exception as e:
                rep = {}
                warn("失败用例报告不是合法 JSON", "{}: {}".format(type(e).__name__, e))
            check(rep.get("status") == "failed", "status == failed", str(rep.get("status")))
            check(rep.get("failed_row") == 2, "failed_row == 2 (1 基行号)",
                  repr(rep.get("failed_row")))
            check(rep.get("error"), "error 非空", repr(rep.get("error")))
            rows = rep.get("rows") or []
            check([r.get("status") for r in rows] == ["ok", "error"],
                  "逐行状态 [ok, error]", str([r.get("status") for r in rows]))
            check(rep.get("executed_rows") == 2, "executed_rows == 2",
                  repr(rep.get("executed_rows")))
            check(rep.get("total_rows") == 2, "total_rows == 2", repr(rep.get("total_rows")))

            out2 = io.StringIO()
            rc2 = cli.main(["--script", script], stdout=out2)   # 无 --exit-code
            check(rc2 == 0, "失败脚本但未给 --exit-code → 退出码 0", repr(rc2))
        finally:
            engine_mod.FAILURE_CONTEXT_ENABLED = orig


def t_usage_errors():
    print("\n── C3. 用法错误 → 退出码 2 ──")
    missing = os.path.join(tempfile.gettempdir(), "acrpa_不存在_script.acrpas")
    check(not os.path.isfile(missing), "用例前置: 该路径确实不存在")
    rc = cli.main(["--script", missing, "--exit-code"], stdout=io.StringIO())
    check(rc == 2, "脚本不存在 → 2", repr(rc))
    check(cli.main([], stdout=io.StringIO()) == 2, "缺 --script → 2")

    with tempfile.TemporaryDirectory(prefix="acrpa_cli_use_") as d:
        script = _make_script(d, "ok.acrpas", [])
        rc = cli.main(["--script", script, "--var", "novalue"], stdout=io.StringIO())
        check(rc == 2, "--var 非法 (缺 K=V) → 2", repr(rc))
        rc = cli.main(["--script", script, "--quiet"], stdout=io.StringIO())
        check(rc == 0, "空脚本 + --quiet → 0 (无输出) ", repr(rc))


# ── D. 静态接线 ──────────────────────────────────────────────────────
def t_wiring():
    print("\n── D. run.py 静态接线 ──")
    with open(os.path.join(ROOT, "run.py"), "r", encoding="utf-8") as f:
        txt = f.read()
    check("cli.main(" in txt, "run.py 调用了 cli.main(")
    check("parse_args(" in txt, "run.py 调用了 cli.parse_args(")
    check("import cli" in txt, "run.py 导入 cli")
    # GUI 回退路径必须保留 (阶段二后 run.py 经 app.main() 进主循环)
    with open(os.path.join(SRC, "app.py"), "r", encoding="utf-8") as f:
        app_txt = f.read()
    check("app.main()" in txt, "run.py 保留 GUI 启动路径 (app.main())")
    check("mainloop" in txt or "mainloop" in app_txt,
          "GUI 启动路径仍进 Tk 主循环 (mainloop)")
    # 去注释后比较真实代码位置 (注释里也会提到 app.main(), 直接 index 会误判)
    code = "\n".join(ln for ln in txt.splitlines() if not ln.lstrip().startswith("#"))
    check(code.index("cli.main(") < code.index("app.main()"),
          "CLI 分支位于 GUI 启动之前 (去注释后比较)")


def main():
    print("=" * 68)
    print("ACRPA CLI / 无头模式 + JSON 报告 回归自测 (路线图 §6.5)")
    print("=" * 68)
    t_parse()
    t_purity()
    t_e2e_success()
    t_e2e_failure()
    t_usage_errors()
    t_wiring()

    print("\n" + "-" * 60)
    print("PASS={} FAIL={} WARN={}".format(len(_PASS), len(_FAIL), len(_WARN)))
    for m in _FAIL:
        print("  [FAIL] {}".format(m))
    for m in _WARN:
        print("  [WARN] {}".format(m))
    print("=== 结果: {} ===".format("FAIL" if _FAIL else "OK"))
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
