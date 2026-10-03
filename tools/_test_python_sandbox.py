# -*- coding: utf-8 -*-
"""tools/_test_python_sandbox.py — 独立可跑的 py_sandbox 验收测试。

* 只 import py_sandbox（GUI/第三方依赖缺失时，engine/commands 部分退化为静态 AST 断言）。
* 不弹窗、不联网。退出码 0(全过/仅 WARN) / 1(存在 FAIL)。
* 输出行前缀: [OK] / [WARN] / [FAIL]。
"""
import os
import sys
import json
import time
import threading
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

_FAILS = []
_WARNS = []


def ok(msg):
    print("[OK] " + msg)


def warn(msg):
    _WARNS.append(msg)
    print("[WARN] " + msg)


def fail(msg):
    _FAILS.append(msg)
    print("[FAIL] " + msg)


def check(cond, msg):
    if cond:
        ok(msg)
    else:
        fail(msg)


def read_text(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def expect_reject(code, perm, needle, label):
    try:
        okv, reason = py_sandbox.precheck(code, perm)
    except Exception as e:
        fail("%s: precheck raised %r" % (label, e))
        return
    if okv:
        fail("%s: expected reject, got allow" % label)
    elif needle and needle not in reason:
        fail("%s: reason %r missing %r" % (label, reason, needle))
    else:
        ok("%s: rejected (%s)" % (label, reason))


def expect_allow(code, perm, label):
    try:
        okv, reason = py_sandbox.precheck(code, perm)
    except Exception as e:
        fail("%s: precheck raised %r" % (label, e))
        return
    if okv:
        ok("%s: allowed" % label)
    else:
        fail("%s: expected allow, got reject (%s)" % (label, reason))


# ── import py_sandbox ──
try:
    import py_sandbox
    ok("import py_sandbox OK")
except Exception as e:
    print("[FAIL] cannot import py_sandbox: %r" % (e,))
    sys.exit(1)


# ======================================================================
# a. import 语句
# ======================================================================
okv, reason = py_sandbox.precheck("import os", "sandbox")
check((okv is False and "import" in reason),
      "a1 sandbox rejects 'import os' (%s)" % reason)
okv2, reason2 = py_sandbox.precheck("import os", "trusted")
check((okv2 is True and reason2 == ""), "a2 trusted allows 'import os'")

# ======================================================================
# b. 危险调用
# ======================================================================
expect_reject("open('x')", "sandbox", "forbidden call: open", "b1 open")
expect_reject("eval('1')", "sandbox", "forbidden call: eval", "b2 eval")
expect_reject("exec('1')", "sandbox", "forbidden call: exec", "b3 exec")
expect_reject("__import__('os')", "sandbox", "forbidden call: __import__", "b4 __import__")

# ======================================================================
# c. dunder 属性
# ======================================================================
expect_reject("().__class__.__bases__", "sandbox", "forbidden attribute", "c1 dunder attr")

# ======================================================================
# d. 语法结构
# ======================================================================
expect_allow("def f(): pass", "sandbox", "d1 def allowed")
expect_reject("class A: pass", "sandbox", "forbidden statement: ClassDef", "d2 class")
expect_reject("with x: pass", "sandbox", "forbidden statement: With", "d3 with")
expect_reject("lambda: 1", "sandbox", "forbidden statement: Lambda", "d4 lambda")

# ======================================================================
# e. 语法错误 / 体积超限
# ======================================================================
okv, reason = py_sandbox.precheck("x = (", "sandbox")
check((okv is False and reason == "syntax error"), "e1 syntax error")
okv, reason = py_sandbox.precheck("a" * 30000, "sandbox")
check((okv is False and reason == "code too large"), "e2 code too large")

# ======================================================================
# f. sandbox 执行
# ======================================================================
r = py_sandbox.run("result = 1 + 2", "sandbox")
check((r.get("ok") is True and r.get("result") == "3"),
      "f1 run sandbox result=3 (got %r)" % (r.get("result"),))

# ======================================================================
# g. sandbox 执行（内建） / 被预检拦截
# ======================================================================
r = py_sandbox.run("result = len([1,2])", "sandbox")
check(r.get("ok") is True, "g1 run len([1,2]) ok")
r = py_sandbox.run("result = open('nope')", "sandbox")
check(r.get("ok") is False, "g2 run open() blocked by precheck")

# ======================================================================
# h. 超时 + trace 清理
# ======================================================================
t0 = time.time()
r = py_sandbox.run("while True:\n    pass", "sandbox", timeout=1.0)
dt = time.time() - t0
check((r.get("ok") is False and r.get("timed_out") is True),
      "h1 sandbox timeout detected (timed_out=%r)" % (r.get("timed_out"),))
check(dt < 5.0, "h2 sandbox timeout elapsed=%.2fs (<5s)" % dt)
r = py_sandbox.run("result = 1", "sandbox")
check(r.get("ok") is True, "h3 trace cleared -> sandbox runs normally after timeout")

# ======================================================================
# i. trusted 允许 import
# ======================================================================
r = py_sandbox.run("import math\nresult = math.floor(3.7)", "trusted", timeout=5)
check((r.get("ok") is True and r.get("result") is not None and "3" in r.get("result")),
      "i1 trusted import math result=%r" % (r.get("result"),))

# ======================================================================
# j. full 用子进程
# ======================================================================
r = py_sandbox.run("result = 6*7", "full", timeout=10)
check((r.get("ok") is True and r.get("result") is not None and "42" in r.get("result")),
      "j1 full subprocess result=%r" % (r.get("result"),))
t0 = time.time()
r = py_sandbox.run("import time\ntime.sleep(30)", "full", timeout=2)
dt = time.time() - t0
check(r.get("timed_out") is True, "j2 full timeout -> timed_out=True")
check(dt < 8.0, "j2b full timeout elapsed=%.2fs (<8s)" % dt)

_src_text = read_text(os.path.join(SRC, "py_sandbox.py"))
check("import multiprocessing" not in _src_text, "j3 no 'import multiprocessing' in py_sandbox")
check("subprocess" in _src_text, "j4 subprocess used in py_sandbox")

# ======================================================================
# k. 审计
# ======================================================================
secret = "SECRET_" + ("x" * 200)
code_with_secret = "result = 1\n# " + secret
py_sandbox.run(code_with_secret, "sandbox", row=7)
apath = py_sandbox.audit_path()
exists = os.path.exists(apath)
check(exists, "k1 audit file exists: %s" % apath)
if exists:
    content = read_text(apath)
    lines = [ln for ln in content.splitlines() if ln.strip()]
    parsed = None
    if lines:
        try:
            parsed = json.loads(lines[-1])
        except Exception as e:
            fail("k2 last audit line not valid JSON: %r" % (e,))
    if parsed is not None:
        ok("k2 last audit line is valid JSON")
        check("code_sha1" in parsed, "k3 audit has code_sha1")
        check("perm" in parsed, "k4 audit has perm")
        check(secret not in content, "k5 audit does NOT contain full source secret")

# ======================================================================
# l. cleanup_audit 安全
# ======================================================================
logs_dir = os.path.dirname(apath)
try:
    os.makedirs(logs_dir, exist_ok=True)
except Exception:
    pass
fake = os.path.join(logs_dir, "acrpa_20990101.log")
try:
    with open(fake, "w", encoding="utf-8") as f:
        f.write("fake\n")
except Exception:
    fake = None
try:
    py_sandbox.cleanup_audit(0)
    py_sandbox.cleanup_audit(3650)
    ok("l1 cleanup_audit(0) / cleanup_audit(3650) did not raise")
except Exception as e:
    fail("l1 cleanup_audit raised %r" % (e,))
if fake:
    check(os.path.exists(fake), "l2 existing acrpa_*.log preserved (not deleted)")
    try:
        os.remove(fake)
    except Exception:
        pass

# ======================================================================
# m. SANDBOX_BUILTINS
# ======================================================================
bt = py_sandbox.SANDBOX_BUILTINS
must_absent = ["__import__", "open", "eval", "exec", "compile",
               "globals", "locals", "getattr", "setattr", "input"]
present = [k for k in must_absent if k in bt]
check(not present, "m1 dangerous builtins absent (extra=%r)" % (present,))
must_present = ["print", "len", "range", "sorted"]
missing = [k for k in must_present if k not in bt]
check(not missing, "m2 safe builtins present (missing=%r)" % (missing,))

# ======================================================================
# n. 静态 AST 断言（engine/commands/state/settings_window）
# ======================================================================
engine_text = read_text(os.path.join(SRC, "engine.py"))
commands_text = read_text(os.path.join(SRC, "commands.py"))
state_text = read_text(os.path.join(SRC, "state.py"))
settings_text = read_text(os.path.join(SRC, "settings_window.py"))

check("def _python(" in engine_text, "n1 engine.py has Python handler")
check("py_sandbox.precheck" in engine_text, "n2 engine.py calls py_sandbox.precheck")
check("py_sandbox.run" in engine_text, "n3 engine.py calls py_sandbox.run")
check('register("Python"' in commands_text, "n4 commands.py registers Python")
check('register("代码"' in commands_text, "n5 commands.py still registers 代码")
check('"python_default_perm"' in state_text, "n6 state.py has python_default_perm")
check('"python_full_enabled"' in state_text, "n7 state.py has python_full_enabled")
check('"python_timeout"' in state_text, "n8 state.py has python_timeout")
check("Python 扩展" in settings_text, "n9 settings_window.py has Python card")
check('_apply_map["python"]' in settings_text, "n10 settings_window has python apply_map")
check('_track_card_vars("python"' in settings_text, "n11 settings_window tracks python vars")

# ======================================================================
# o. 守护断言（未改动竞品模块）
# ======================================================================
# workflow.py 未被修改
wf_path = os.path.join(SRC, "workflow.py")
try:
    cp = subprocess.run(["git", "diff", "--stat", "--", "src/workflow.py"],
                        cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out = cp.stdout.decode("utf-8", "replace").strip()
    if cp.returncode == 0:
        check(out == "", "o1 src/workflow.py unchanged (git diff empty)")
    else:
        warn("o1 git diff failed (rc=%d); fallback anchor check" % cp.returncode)
        check("def _handler_workflow_run" in read_text(wf_path),
              "o1b workflow.py anchor present")
except Exception as e:
    warn("o1 git unavailable (%r); fallback anchor check" % (e,))
    try:
        check("def _handler_workflow_run" in read_text(wf_path),
              "o1b workflow.py anchor present")
    except Exception as e2:
        fail("o1c cannot read workflow.py: %r" % (e2,))

acrpa_text = read_text(os.path.join(SRC, "ACRPA.py"))
for token in ("open_devlink", "set_control_hooks", "start_netlink", "_nl_hook_run"):
    c = acrpa_text.count(token)
    if c >= 1:
        ok("o2 NetLink token present in ACRPA.py: %s (x%d)" % (token, c))
    else:
        # start_netlink 可能在 netlink 包内，本文件通过 netlink.start_netlink 间接调用
        warn("o2 NetLink token not found in ACRPA.py: %s" % token)

check(("_mb_animate_width" in acrpa_text or
       "_mb_animate_width" in read_text(os.path.join(SRC, "mini_bar.py")))
      and ("def _destroy_mini_bar" in read_text(os.path.join(SRC, "mini_bar.py")))
      and "after_cancel" in read_text(os.path.join(SRC, "mini_bar.py")),
      "o3 Mini Bar hooks still present (已抽到 src/mini_bar.py)")
check("def themed" in read_text(os.path.join(SRC, "utils.py")),
      "o4 utils.themed still present")
check("PROVIDER_PRESETS" in read_text(os.path.join(SRC, "ai_client.py")),
      "o5 ai_client.PROVIDER_PRESETS still present")
check(('devlink_btn' in acrpa_text and 'FONT_ICON_MD' in acrpa_text),
      "o6 devlink_btn uses the named icon font (globe emoji retired in v0.1.29-beta)")

# ======================================================================
# 旧插件兼容 (动态优先，无法 import engine 时静态断言)
# ======================================================================
engine = None
try:
    import engine as engine
    ok("import engine OK (dynamic handler signature tests enabled)")
except Exception as e:
    warn("import engine failed (%s) -> static AST assertion for handler adaptation"
         % type(e).__name__)

if engine is not None:
    try:
        def h2(row, script_dir):
            return "ok2"

        def h3(row, script_dir, api):
            return api

        a2 = engine._handler_arity(h2)
        a3 = engine._handler_arity(h3)
        check(a2 == 2, "p1 arity of 2-arg handler == 2 (got %r)" % a2)
        check(a3 == 3, "p2 arity of 3-arg handler == 3 (got %r)" % a3)
        try:
            res2 = engine._invoke_handler(h2, None, "")
            check(res2 == "ok2", "p3 2-arg handler invoked without api (got %r)" % (res2,))
        except TypeError as e:
            fail("p3 2-arg handler raised TypeError: %r" % (e,))
        except Exception as e:
            warn("p3 2-arg handler raised %r" % (e,))
        try:
            res3 = engine._invoke_handler(h3, None, "")
            ok("p4 3-arg handler invoked with api (type=%s)" % type(res3).__name__)
        except Exception as e:
            fail("p4 3-arg handler raised %r" % (e,))
    except Exception as e:
        fail("engine handler-adaptation dynamic test error: %r" % (e,))
else:
    check("_invoke_handler" in engine_text and "co_argcount" in engine_text,
          "p1s engine.py has inspect-based handler adaptation (static)")

# ======================================================================
# q. 依赖探测
# ======================================================================
for mod in ("pyautogui", "xlrd", "pyperclip"):
    try:
        __import__(mod)
        ok("q dependency present: %s" % mod)
    except Exception as e:
        warn("q dependency missing: %s (%r)" % (mod, e))

# ======================================================================
# r. §1.1 format 逃逸（文档 §1.1 复现，修复后应被 precheck 拒绝）
# ======================================================================
expect_reject('result = "{0.__class__.__base__}".format([])', "sandbox",
              "forbidden call: format", "r1 sandbox rejects str.format dunder read")
expect_reject('result = "{0._injected._python.__globals__[os].name}".format(acrpa)',
              "sandbox", "forbidden call: format",
              "r2 sandbox rejects format dunder leak chain")
expect_reject('x = "a__b"', "sandbox", "forbidden dunder in string",
              "r3 sandbox rejects string constant containing __")
expect_reject('x = "{0." + "__class__" + "}"', "sandbox", "forbidden dunder in string",
              "r3b sandbox rejects concatenated dunder string")
expect_reject('format("{0}", 1)', "sandbox", "forbidden call: format",
              "r4 sandbox rejects bare format() call")
check("format" not in py_sandbox.SANDBOX_BUILTINS,
      "r5 'format' removed from SANDBOX_BUILTINS")
check("format" in py_sandbox.FORBIDDEN_CALLS,
      "r6 'format' present in FORBIDDEN_CALLS")
expect_allow('result = "{0}".format(1)', "trusted", "r7 trusted still allows str.format")
expect_allow('x = "a__b"', "trusted", "r8 trusted still allows __ in string")
r = py_sandbox.run('result = "{0.__class__.__base__}".format([])', "sandbox")
check((r.get("ok") is False and "forbidden call: format" in (r.get("error") or "")),
      "r9 run() blocks format escape at precheck (error=%r)" % (r.get("error"),))

# ======================================================================
# s. §1.4 末尾表达式只执行一次（文档 §1.4 复现）
# ======================================================================
_seen = []


def _collector(msg, level="info"):
    _seen.append(str(msg))


_seen[:] = []
r = py_sandbox.run('print("MARKER-SIDE-EFFECT")', "sandbox", log=_collector)
_marks = [m for m in _seen if "MARKER-SIDE-EFFECT" in m]
check(len(_marks) == 1, "s1 doc repro: print marker occurs once (got %d)" % len(_marks))

_seen[:] = []
code_bump = ("def bump():\n"
             "    print('SIDE')\n"
             "    return 1\n"
             "bump()")
r = py_sandbox.run(code_bump, "sandbox", log=_collector)
_side = [m for m in _seen if "SIDE" in m]
check(len(_side) == 1, "s2 last-expr call executed once (got %d)" % len(_side))
check(r.get("result") == "1",
      "s3 last-expr value captured as result (got %r)" % (r.get("result"),))

r = py_sandbox.run("1 + 2", "sandbox")
check(r.get("result") == "3",
      "s4 bare last-expr becomes result (got %r)" % (r.get("result"),))
r = py_sandbox.run("result = 5\nresult + 1", "sandbox")
check(r.get("result") == "6",
      "s5 last-expr rewrites result (got %r)" % (r.get("result"),))
_ps = read_text(os.path.join(SRC, "py_sandbox.py"))
check("<acrpa-py-expr>" not in _ps,
      "s6 no second-eval path remains (_extract_result)")

# ======================================================================
# t. §1.5 print 转发 / full stdout 与 result 通道分离（文档 §1.5）
# ======================================================================
_seen[:] = []
r = py_sandbox.run('print("hello-log")', "sandbox", log=_collector)
check(any("hello-log" in m for m in _seen),
      "t1 in-process print forwarded to injected log callback")
check(r.get("ok") is True, "t2 print script ok=True")

_seen[:] = []
r = py_sandbox.run('print("hi")\nresult = 5', "full", timeout=15, log=_collector)
check(r.get("ok") is True, "t3 full run ok (error=%r)" % (r.get("error"),))
check(r.get("result") == "5",
      "t4 full result not polluted by stdout (got %r)" % (r.get("result"),))
check("hi" not in (r.get("result") or ""),
      "t4b full result carries no print output")
check(any("hi" in m for m in _seen),
      "t5 full user print forwarded to log channel")

_seen[:] = []
r = py_sandbox.run('result = 7*6', "full", timeout=15, log=_collector)
check(r.get("result") == "42",
      "t6 full result channel marker parsed (got %r)" % (r.get("result"),))
check(r.get("raw_result") == "42", "t7 additive raw_result present (full)")

# ======================================================================
# u. §1.2 假沙箱逃逸链（文档 §1.2 复现）—— sandbox precheck 应拒绝
#    原 repro: ().__class__.__base__.__subclasses__()
#    （旧「代码」命令无预检、可自由执行该链并定位 Popen → RCE；
#     现统一走沙箱，被 dunder 属性规则 forbidden attribute 拦截。）
# ======================================================================
expect_reject("subs = ().__class__.__base__.__subclasses__()", "sandbox",
              "forbidden attribute", "u1 §1.2 subclasses chain rejected (sandbox precheck)")
r = py_sandbox.run("subs = ().__class__.__base__.__subclasses__()", "sandbox")
check(r.get("ok") is False, "u2 §1.2 subclasses chain blocked by run() precheck")

# ======================================================================
# v. BUG-04 对抗性超时用例（trusted 超时不可被绕过）
#    方案 B（保留行级追踪器兜底 + 追踪器保护 + 看门狗线程异步注入）：
#    v1/v2: sys.settrace(None) 后死循环 → 必须被按时中断
#    v3/v4: 纯 Python 死循环 → 必须被按时中断
#    v5   : C 层阻塞 time.sleep → 方案 B 无法按时中断（KNOWN LIMITATION，WARN）
#    v6/v7: 正常返回 / 正常异常语义回归
#    v8   : 执行结束后无看门狗线程泄漏
# ======================================================================
_ps = read_text(os.path.join(SRC, "py_sandbox.py"))
check("PyThreadState_SetAsyncExc" in _ps,
      "v0 watchdog async-inject (PyThreadState_SetAsyncExc) present")
check("_block_trace_call" in _ps,
      "v0b trace-guard (_block_trace_call) present")

_base_threads = threading.active_count()

# v1/v2: 关闭追踪 + 死循环，必须在限定时间内被中断
t0 = time.time()
r = py_sandbox.run("import sys\nsys.settrace(None)\nwhile True:\n    pass",
                   "trusted", timeout=1.0)
dt1 = time.time() - t0
check(r.get("ok") is False and r.get("timed_out") is True,
      "v1 settrace(None)+deadloop timed_out (timed_out=%r)" % (r.get("timed_out"),))
check(dt1 < 5.0, "v2 settrace(None) bypass interrupted in time (%.2fs <5s)" % dt1)

# v3/v4: 纯 Python 死循环
t0 = time.time()
r = py_sandbox.run("while True:\n    pass", "trusted", timeout=1.0)
dt3 = time.time() - t0
check(r.get("ok") is False and r.get("timed_out") is True,
      "v3 pure-python deadloop timed_out (timed_out=%r)" % (r.get("timed_out"),))
check(dt3 < 5.0, "v4 pure-python deadloop interrupted in time (%.2fs <5s)" % dt3)

# v5: C 层阻塞（time.sleep）——方案 B 无法按时中断，如实标注 KNOWN LIMITATION
t0 = time.time()
r = py_sandbox.run("import time\ntime.sleep(4)\nresult = 1", "trusted", timeout=1.0)
dt5 = time.time() - t0
if dt5 < 3.0:
    # 若能在限定时间内中断（更强手段 / 未来方案 A），则视为通过
    check(r.get("timed_out") is True,
          "v5 C-level block interrupted in time (elapsed=%.2fs)" % dt5)
else:
    warn("v5 KNOWN LIMITATION (方案 B): C 层阻塞 time.sleep 无法按时中断，"
         "仅在阻塞返回后抛超时 (elapsed=%.2fs, timed_out=%r)"
         % (dt5, r.get("timed_out")))

# v6: 正常代码仍正确返回结果（回归）
r = py_sandbox.run("result = 6*7", "trusted", timeout=5)
check(r.get("ok") is True and r.get("result") == "42",
      "v6 trusted normal result preserved (got %r)" % (r.get("result"),))

# v7: 正常异常仍按原语义抛出（回归）
r = py_sandbox.run("result = 1 / 0", "trusted", timeout=5)
check(r.get("ok") is False and "ZeroDivisionError" in (r.get("error") or ""),
      "v7 trusted normal exception preserved (error=%r)" % (r.get("error"),))

# v8: 执行结束后无看门狗线程泄漏
time.sleep(0.5)
check(threading.active_count() <= _base_threads,
      "v8 no watchdog thread leak (active=%d base=%d)"
      % (threading.active_count(), _base_threads))

# ======================================================================
# 汇总
# ======================================================================
print("")
print("SUMMARY: %d FAIL, %d WARN" % (len(_FAILS), len(_WARNS)))
for m in _FAILS:
    print("  FAIL: " + m)
sys.exit(1 if _FAILS else 0)
