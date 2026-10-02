# -*- coding: utf-8 -*-
"""tools/_test_code_command.py — 批次 2（文档 §1.2）「代码」命令统一走沙箱 验收测试。

覆盖：
  * 默认（legacy_code_command=False）下「代码」命令走 py_sandbox 执行内核：
    正常 .txt 可执行并回传 result；含 import / dunder 的 .txt 被 precheck 拒绝
    （稳定短语 "forbidden import" / "forbidden attribute: ..."）。
  * 弱路径校验修复：`../` 越权路径、base_dir 前缀同名目录（scripts vs scriptsX）
    均被拒（后者可实测旧 startswith 会放行）。
  * 失败语义：脚本运行期报错时 handler 返回 False，不抛异常。
  * legacy_code_command=True 时回退旧的 exec 路径（可执行被沙箱拒绝的 dunder 代码）。
  * PYTHON_DEFAULT_PERM=full 且未启用 PYTHON_FULL_ENABLED → "full disabled"。
  * 审计 source="file"；print 经 log 回调转发（对接批次 1）。
  * 静态断言：state 有 legacy_code_command 键；commands「代码」描述已更新；
    engine._python 调用点补了 log=log1。

只跑纯逻辑，不弹窗、不联网。退出码 0(全过/仅 WARN) / 1(存在 FAIL)。
输出行前缀: [OK] / [WARN] / [FAIL]。
"""
import os
import re
import sys
import json
import shutil
import tempfile

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


# ── import py_sandbox / engine / state ──
try:
    import py_sandbox
    ok("import py_sandbox OK")
except Exception as e:
    print("[FAIL] cannot import py_sandbox: %r" % (e,))
    sys.exit(1)

try:
    import state
    import engine
    ok("import engine/state OK (dynamic 代码-command tests enabled)")
except Exception as e:
    warn("import engine failed (%s) -> SKIP dynamic 代码-command tests"
         % type(e).__name__)
    engine = None


# ======================================================================
# 静态断言（不依赖 import engine 成功）
# ======================================================================
state_text = read_text(os.path.join(SRC, "state.py"))
engine_text = read_text(os.path.join(SRC, "engine.py"))
commands_text = read_text(os.path.join(SRC, "commands.py"))

check('("legacy_code_command", False' in state_text,
      "a1 state.py has legacy_code_command key (default False)")
check("os.path.commonpath" in engine_text, "a2 engine.py uses os.path.commonpath")
check(re.search(r"^\s*if not abs_path\.startswith", engine_text, re.M) is None,
      "a3 weak 'if not abs_path.startswith(...)' check removed")
check("def _exec_legacy(" in engine_text, "a4 engine.py has _exec_legacy helper")
check('audit="file"' in engine_text, "a5 engine.py 代码 passes audit=\"file\"")
check('audit="excel", row=self._current_row(), log=log1' in engine_text,
      "a6 engine._python run() call now injects log=log1")
check("def _exec(self, row, z):" in engine_text, "a7 _exec new signature present")
# 失败语义：_exec 内不再 raise
_exec_seg = engine_text.split("def _exec(self, row, z):", 1)[-1].split("def _exec_legacy(", 1)[0]
check(re.search(r"^\s*raise\b", _exec_seg, re.M) is None,
      "a8 no bare 'raise' statement left in _exec (return False semantics)")
_cmd_line = [ln for ln in commands_text.splitlines() if ln.strip().startswith('register("代码"')]
check(bool(_cmd_line) and "AST预检" in _cmd_line[0],
      "a9 commands.py 代码 description mentions AST precheck")


if engine is None:
    print("")
    print("SUMMARY: %d FAIL, %d WARN" % (len(_FAILS), len(_WARNS)))
    for m in _FAILS:
        print("  FAIL: " + m)
    sys.exit(1 if _FAILS else 0)


# ======================================================================
# 夹具：临时脚本目录树
#   <root>/scripts/ok.txt            正常脚本
#   <root>/scripts/print.txt         print 转发
#   <root>/scripts/err.txt           运行期报错
#   <root>/scripts/imp.txt           import os（沙箱拒绝）
#   <root>/scripts/dun.txt           dunder 属性（沙箱拒绝；legacy 可执行）
#   <root>/scripts/sub.txt           §1.2 subclasses 逃逸链（沙箱拒绝）
#   <root>/evil.txt                  ../ 越权目标
#   <root>/scriptsX/x.txt            base_dir 前缀同名目录（旧 startswith 放行）
# ======================================================================
_root = tempfile.mkdtemp(prefix="acrpa_code_cmd_")
_scripts = os.path.join(_root, "scripts")
_scriptsX = os.path.join(_root, "scriptsX")
os.makedirs(_scripts, exist_ok=True)
os.makedirs(_scriptsX, exist_ok=True)


def _w(sub, name, content):
    p = os.path.join(sub, name + ".txt")
    with open(p, "w", encoding="utf-8") as f:
        f.write(content)
    return p


_w(_scripts, "ok", "result = 1 + 2")
_w(_scripts, "print", 'print("HELLO-FWD")')
_w(_scripts, "err", "result = 1 / 0")
_w(_scripts, "imp", "import os\nresult = 1")
_w(_scripts, "dun", "x = ().__class__.__name__\nresult = x")
_w(_scripts, "sub", "subs = ().__class__.__base__.__subclasses__()\nresult = len(subs)")
_w(_root, "evil", "result = 'pwned'")
_w(_scriptsX, "x", "result = 'sibling'")

# 稳定执行环境
state.quit2 = False
state.pause_event.set()
_prev_perm = getattr(state, "PYTHON_DEFAULT_PERM", "sandbox")
_prev_full = getattr(state, "PYTHON_FULL_ENABLED", False)
_prev_legacy = getattr(state, "LEGACY_CODE_COMMAND", False)
_prev_log1 = engine.log1
_seen = []


def _collector(msg, level="info"):
    _seen.append(str(msg))


engine.log1 = _collector  # 捕获 handler 内 log1 输出（含 print 转发）


class _Row(object):
    def __init__(self, name):
        self.args = [name]


def run_code(name, z=None):
    _seen[:] = []
    return engine.ExecutionEngine()._exec(_Row(name), z or _scripts)


def logs_contain(needle):
    return any(needle in m for m in _seen)


try:
    # ── b. 默认走沙箱：正常脚本可执行 ──
    state.PYTHON_DEFAULT_PERM = "sandbox"
    state.PYTHON_FULL_ENABLED = False
    state.LEGACY_CODE_COMMAND = False

    rv = run_code("ok")
    check(rv is None, "b1 normal .txt executed -> returns None (got %r)" % (rv,))
    check(logs_contain("✅ 执行了脚本") and logs_contain("result=3"),
          "b2 success log carries result=3")

    # ── c. print 经 log 回调转发（批次 1 对接）──
    rv = run_code("print")
    check(rv is None, "c1 print .txt ok -> returns None")
    check(logs_contain("HELLO-FWD"), "c2 print forwarded via log=log1 callback")

    # ── d. 审计 source="file" ──
    apath = py_sandbox.audit_path()
    try:
        lines = [ln for ln in read_text(apath).splitlines() if ln.strip()]
        rec = json.loads(lines[-1]) if lines else {}
    except Exception as e:
        rec = {}
        warn("d audit read failed: %r" % (e,))
    check(rec.get("source") == "file",
          "d1 audit record source==\"file\" (got %r)" % (rec.get("source"),))

    # ── e. 沙箱 precheck 拒绝 import / dunder ──
    rv = run_code("imp")
    check(rv is False, "e1 import .txt rejected -> returns False (got %r)" % (rv,))
    check(logs_contain("forbidden import"), "e2 stable phrase 'forbidden import'")

    rv = run_code("dun")
    check(rv is False, "e3 dunder .txt rejected -> returns False (got %r)" % (rv,))
    check(logs_contain("forbidden attribute"), "e4 stable phrase 'forbidden attribute'")

    # ── e5/§1.2 文档原样复现：subclasses 逃逸链经「代码」命令被沙箱拒绝 ──
    #     旧 _exec 无预检、可自由执行 ().__class__.__base__.__subclasses__()
    #     （255 个活宿主类 → 可定位 Popen → RCE）；统一走沙箱后应被拦。
    rv = run_code("sub")
    check(rv is False, "e5 §1.2 subclasses escape rejected -> False (got %r)" % (rv,))
    check(logs_contain("forbidden attribute"), "e6 §1.2 stable phrase 'forbidden attribute'")

    # ── f. 失败语义：报错不抛异常、返回假值 ──
    raised = None
    rv = "UNSET"
    try:
        rv = run_code("err")
    except Exception as e:  # noqa
        raised = e
    check(raised is None, "f1 runtime error does NOT raise (raised=%r)" % (raised,))
    check(rv is False, "f2 runtime error returns False (got %r)" % (rv,))

    # ── g. 弱路径校验：../ 越权 ──
    rv = run_code("../evil")
    check(rv is False, "g1 '../evil' traversal rejected -> False (got %r)" % (rv,))
    check(logs_contain("安全错误"), "g2 traversal logs 安全错误")

    # ── h. 弱路径校验：base_dir 前缀同名目录 scripts vs scriptsX ──
    #     旧实现 abs_path.startswith(base_dir) 对该路径会放行（复现弱校验）。
    _abs = os.path.abspath(os.path.join(_scripts, "../scriptsX/x.txt"))
    _base = os.path.abspath(_scripts)
    _old_allows = _abs.startswith(_base)
    check(_old_allows is True,
          "h1 old startswith() would ALLOW sibling-prefix path (repro of weak check)")
    rv = run_code("../scriptsX/x")
    check(rv is False, "h2 sibling-prefix path rejected by commonpath (got %r)" % (rv,))
    check(logs_contain("安全错误"), "h3 sibling-prefix logs 安全错误")

    # ── i. legacy_code_command=True 回退旧 exec 路径 ──
    state.LEGACY_CODE_COMMAND = True
    rv = run_code("dun")
    check(rv is None,
          "i1 legacy=True executes sandbox-rejected dunder .txt -> None (got %r)" % (rv,))
    check(logs_contain("(legacy)"), "i2 legacy path taken (log '(legacy)')")
    state.LEGACY_CODE_COMMAND = False
    rv = run_code("dun")
    check(rv is False, "i3 legacy=False back to sandbox -> False (got %r)" % (rv,))

    # ── j. full 未启用 → "full disabled" ──
    state.PYTHON_DEFAULT_PERM = "full"
    state.PYTHON_FULL_ENABLED = False
    rv = run_code("ok")
    check(rv is False, "j1 full (disabled) rejected -> False (got %r)" % (rv,))
    check(logs_contain("full disabled"), "j2 stable phrase 'full disabled'")

    # ── k. 文件不存在 / 未指定文件名 ──
    rv = run_code("no_such_script")
    check(rv is False, "k1 missing file -> False (got %r)" % (rv,))
    check(logs_contain("脚本文件不存在"), "k2 missing-file friendly message kept")
    rv = engine.ExecutionEngine()._exec(_Row(""), _scripts)
    check(rv is False, "k3 empty name -> False (got %r)" % (rv,))

finally:
    # 恢复全局状态与临时目录
    state.PYTHON_DEFAULT_PERM = _prev_perm
    state.PYTHON_FULL_ENABLED = _prev_full
    state.LEGACY_CODE_COMMAND = _prev_legacy
    engine.log1 = _prev_log1
    shutil.rmtree(_root, ignore_errors=True)
    shutil.rmtree(_scriptsX, ignore_errors=True)


# ======================================================================
# 汇总
# ======================================================================
print("")
print("SUMMARY: %d FAIL, %d WARN" % (len(_FAILS), len(_WARNS)))
for m in _FAILS:
    print("  FAIL: " + m)
sys.exit(1 if _FAILS else 0)
