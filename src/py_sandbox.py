"""py_sandbox.py — 自定义 Python 代码的受限执行沙箱（纯标准库）。

设计目标
--------
* 可被独立 import：本模块**不**导入 engine / pyautogui / requests / tkinter 等
  GUI/第三方库，便于单元测试与打包裁剪（唯一例外：cleanup_audit() 内惰性
  import state 仅用于读取日志保留天数，且 try/except 兜底）。
* 三级权限：sandbox（默认，最严）/ trusted（受信）/ full（独立子进程，默认禁用）。
* 执行前 AST 预检（precheck）+ 执行期超时：
    - sandbox / trusted：sys.settrace 行级时间检查（finally 中必定清理）；
    - full：subprocess timeout + kill（不使用 multiprocessing）。
* 审计：每次执行写一行 JSON 到 <程序目录>/logs/acrpa_py_YYYYMMDD.log，
  只记录 code_sha1 / 长度 / 首行预览，绝不写代码全文。

对外接口
--------
    PERM_SANDBOX / PERM_TRUSTED / PERM_FULL / VALID_PERMS
    FORBIDDEN_CALLS / FORBIDDEN_ATTR_PREFIX / SANDBOX_BUILTINS
    precheck(code, perm=PERM_SANDBOX) -> (bool, str)
    run(code, perm=PERM_SANDBOX, timeout=None, api=None, audit=None, row=0) -> dict
    audit_path() -> str
    audit_write(record) -> None
    make_record(code, perm, row, elapsed, ok, error, timed_out, source) -> dict
    cleanup_audit(days=None) -> int
"""
import ast
import base64
import builtins
import glob
import hashlib
import json
import os
import subprocess
import sys
import threading
import time

# ── 权限常量 ──
PERM_SANDBOX = "sandbox"
PERM_TRUSTED = "trusted"
PERM_FULL = "full"
VALID_PERMS = (PERM_SANDBOX, PERM_TRUSTED, PERM_FULL)

# ── 预检黑名单 ──
FORBIDDEN_CALLS = {"eval", "exec", "compile", "open", "input", "breakpoint",
                   "__import__", "globals", "locals", "vars", "getattr", "setattr",
                   "delattr", "memoryview", "classmethod", "staticmethod", "super"}
FORBIDDEN_ATTR_PREFIX = "__"

# trusted / full 仅拒绝的核心危险调用（其余放行）
_CORE_UNSAFE_CALLS = frozenset(("eval", "exec", "compile", "__import__"))

# sandbox 拒绝的语法结构（kind 用 type(node).__name__）
_FORBIDDEN_STMTS = (ast.With, ast.AsyncWith, ast.Global, ast.Nonlocal,
                    ast.Lambda, ast.ClassDef)

# ── 体积上限 ──
MAX_CODE_LEN = 20000
MAX_AST_NODES = 20000

# ── 超时 ──
DEFAULT_TIMEOUT = 30.0
_TRACE_STEP = 200          # 行级检查：每 N 个 line 事件比较一次时间
_RESULT_MAX_LEN = 2000
_PREVIEW_LEN = 80
_SHA1_LEN = 12

# ── sandbox 白名单内建（只读/无害）──
# 注意：绝不含 __import__/open/eval/exec/compile/globals/locals/getattr/setattr/
#       input/help/breakpoint 等危险或具逃逸能力的内建。
SANDBOX_BUILTINS = {
    "abs": abs, "all": all, "any": any, "bool": bool, "dict": dict,
    "divmod": divmod, "enumerate": enumerate, "filter": filter,
    "float": float, "format": format, "frozenset": frozenset, "int": int,
    "isinstance": isinstance, "issubclass": issubclass, "iter": iter,
    "len": len, "list": list, "map": map, "max": max, "min": min,
    "next": next, "pow": pow, "print": print, "range": range, "repr": repr,
    "reversed": reversed, "round": round, "set": set, "slice": slice,
    "sorted": sorted, "str": str, "sum": sum, "tuple": tuple, "type": type,
    "zip": zip,
    "True": True, "False": False, "None": None,
    "NotImplemented": NotImplemented,
    "Exception": Exception, "ValueError": ValueError, "TypeError": TypeError,
    "KeyError": KeyError, "IndexError": IndexError,
    "ZeroDivisionError": ZeroDivisionError, "ArithmeticError": ArithmeticError,
    "RuntimeError": RuntimeError, "StopIteration": StopIteration,
}


# ======================================================================
# AST 预检
# ======================================================================
def precheck(code, perm=PERM_SANDBOX):
    """AST 预检。

    返回 (True, "") 或 (False, "<稳定英文原因>")。失败原因短语稳定，便于测试断言：
        "syntax error"
        "forbidden call: <name>"
        "forbidden attribute: <name>"
        "forbidden import"
        "forbidden statement: <kind>"
        "code too large"
    """
    try:
        if code is None:
            code = ""
        if not isinstance(code, str):
            code = str(code)
    except Exception:
        return (False, "syntax error")

    if len(code) > MAX_CODE_LEN:
        return (False, "code too large")

    try:
        tree = ast.parse(code)
    except SyntaxError:
        return (False, "syntax error")
    except Exception:
        return (False, "syntax error")

    node_count = 0
    for _ in ast.walk(tree):
        node_count += 1
    if node_count > MAX_AST_NODES:
        return (False, "code too large")

    sandbox = (perm == PERM_SANDBOX)
    for node in ast.walk(tree):
        if sandbox:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                return (False, "forbidden import")
            if isinstance(node, _FORBIDDEN_STMTS):
                return (False, "forbidden statement: %s" % type(node).__name__)
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                if sandbox and func.id in FORBIDDEN_CALLS:
                    return (False, "forbidden call: %s" % func.id)
                if (not sandbox) and func.id in _CORE_UNSAFE_CALLS:
                    return (False, "forbidden call: %s" % func.id)
        if sandbox and isinstance(node, ast.Attribute):
            attr = node.attr
            if isinstance(attr, str) and attr.startswith(FORBIDDEN_ATTR_PREFIX):
                return (False, "forbidden attribute: %s" % attr)
    return (True, "")


# ======================================================================
# 执行
# ======================================================================
def run(code, perm=PERM_SANDBOX, timeout=None, api=None, audit=None, row=0):
    """执行代码。返回 {ok,result,error,elapsed,perm,timed_out}，绝不抛异常。

    api    : 暴露给脚本的 acrpa 对象（AcrpaAPI 实例），可为 None。
    audit  : 审计来源标记：字符串（"excel"/"plugin"/"hook"/"console"）或可调用对象
             （接收 record dict）。字符串会写入审计记录的 source 字段。
    row    : 当前执行行号（写入审计）。
    """
    if perm not in VALID_PERMS:
        perm = PERM_SANDBOX
    if timeout is None:
        timeout = DEFAULT_TIMEOUT
    try:
        timeout = float(timeout)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT
    if timeout <= 0:
        timeout = DEFAULT_TIMEOUT

    t0 = time.time()
    result = {"ok": False, "result": None, "error": "", "elapsed": 0.0,
              "perm": perm, "timed_out": False}

    try:
        ok, reason = precheck(code, perm)
        if not ok:
            result["error"] = reason
        elif perm == PERM_FULL:
            _exec_full(code, timeout, result)
        else:
            _exec_inprocess(code, perm, timeout, api, result)
    except Exception as e:  # 兜底：任何未预期异常都必须被吞掉
        result["error"] = "%s: %s" % (type(e).__name__, e)

    result["elapsed"] = round(time.time() - t0, 4)
    _emit_audit(audit, code, perm, row, result)
    return result


def _real_builtins():
    return builtins


def _make_tracer(deadline, step=_TRACE_STEP):
    """构造 sys.settrace 用的行级时间检查 trace 函数。超时 raise TimeoutError。"""
    counter = [0]

    def _tracer(frame, event, arg):
        if event == "line":
            counter[0] += 1
            if counter[0] >= step:
                counter[0] = 0
                if time.time() > deadline:
                    raise TimeoutError("code timeout")
        return _tracer

    return _tracer


def _exec_inprocess(code, perm, timeout, api, result):
    """sandbox / trusted 在当前进程内执行（单一命名空间）。"""
    if perm == PERM_TRUSTED:
        ns = {"__builtins__": _real_builtins(), "acrpa": api, "result": None}
    else:
        ns = {"__builtins__": dict(SANDBOX_BUILTINS), "acrpa": api, "result": None}

    compiled = compile(code, "<acrpa-py>", "exec")
    deadline = time.time() + timeout
    tracer = _make_tracer(deadline)
    sys.settrace(tracer)
    try:
        exec(compiled, ns)
    except TimeoutError as e:
        result["timed_out"] = True
        result["error"] = "TimeoutError: %s" % e
        return
    except Exception as e:
        result["error"] = "%s: %s" % (type(e).__name__, e)
        return
    finally:
        # 关键：无论如何都要清除 trace，否则会拖慢整个进程后续执行。
        sys.settrace(None)

    result["ok"] = True
    result["result"] = _extract_result(ns, code)


def _extract_result(ns, code):
    """取 result 变量值；否则尝试求值最后一个表达式语句。返回 repr 截断字符串。"""
    val = ns.get("result")
    if val is None:
        try:
            tree = ast.parse(code)
            if tree.body and isinstance(tree.body[-1], ast.Expr):
                expr = ast.Expression(tree.body[-1].value)
                val = eval(compile(expr, "<acrpa-py-expr>", "eval"), ns)
        except Exception:
            val = None
    if val is None:
        return None
    try:
        text = repr(val)
    except Exception:
        text = "<unrepr>"
    if len(text) > _RESULT_MAX_LEN:
        text = text[:_RESULT_MAX_LEN] + "...(truncated)"
    return text


# full 权限：独立子进程执行。代码以 base64 经 stdin 传入（规避引号转义问题）。
_FULL_WRAPPER = (
    "import sys, base64\n"
    "src = base64.b64decode(sys.stdin.buffer.read()).decode('utf-8')\n"
    "ns = {'__name__': '__main__', 'result': None}\n"
    "exec(compile(src, '<acrpa-py-full>', 'exec'), ns)\n"
    "r = ns.get('result')\n"
    "if r is not None:\n"
    "    sys.stdout.write(repr(r))\n"
)


def _exec_full(code, timeout, result):
    """full 权限：subprocess 独立进程，超时 kill。不使用 multiprocessing。"""
    try:
        payload = base64.b64encode(code.encode("utf-8", "replace"))
    except Exception as e:
        result["error"] = "%s: %s" % (type(e).__name__, e)
        return

    try:
        proc = subprocess.Popen(
            [sys.executable, "-c", _FULL_WRAPPER],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=_program_dir())
    except Exception as e:
        result["error"] = "%s: %s" % (type(e).__name__, e)
        return

    timed_out = False
    out = b""
    err = b""
    try:
        out, err = proc.communicate(payload, timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            proc.kill()
        except Exception:
            pass
        try:
            out, err = proc.communicate(timeout=5)
        except Exception:
            out, err = b"", b""
    except Exception as e:
        try:
            proc.kill()
        except Exception:
            pass
        result["error"] = "%s: %s" % (type(e).__name__, e)
        return

    if timed_out:
        result["timed_out"] = True
        result["error"] = "TimeoutError: code timeout"
        return

    txt_out = (out or b"").decode("utf-8", "replace").strip()
    txt_err = (err or b"").decode("utf-8", "replace").strip()
    if proc.returncode == 0:
        result["ok"] = True
        result["result"] = txt_out if txt_out else None
    else:
        msg = txt_err or ("exit code %s" % proc.returncode)
        result["ok"] = False
        result["error"] = ("RuntimeError: " + msg)[:400]


# ======================================================================
# 审计
# ======================================================================
def _program_dir():
    """程序目录（与 ACRPA.py 的 APP_ROOT 规则一致）。"""
    try:
        if getattr(sys, "frozen", False):
            return os.path.dirname(os.path.abspath(sys.executable))
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    except Exception:
        return os.getcwd()


def audit_path():
    """审计日志路径：<程序目录>/logs/acrpa_py_YYYYMMDD.log"""
    return os.path.join(_program_dir(), "logs",
                        "acrpa_py_%s.log" % time.strftime("%Y%m%d"))


_audit_lock = threading.Lock()


def audit_write(record):
    """线程安全地追加一行 JSON 审计记录（ensure_ascii=False）。失败静默。"""
    try:
        if not isinstance(record, dict):
            return
        rec = dict(record)
        if "ts" not in rec:
            rec["ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
        line = json.dumps(rec, ensure_ascii=False)
        path = audit_path()
        d = os.path.dirname(path)
        if d and not os.path.isdir(d):
            os.makedirs(d)
        with _audit_lock:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception:
        pass


def make_record(code, perm, row, elapsed, ok, error, timed_out, source):
    """构造审计记录：只保留 sha1 前 12 位 + 长度 + 首行预览，不写代码全文。"""
    try:
        code_str = code if isinstance(code, str) else str(code or "")
    except Exception:
        code_str = ""
    sha = hashlib.sha1(code_str.encode("utf-8", "replace")).hexdigest()[:_SHA1_LEN]
    first_line = code_str.splitlines()[0] if code_str else ""
    return {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "perm": perm,
        "row": row,
        "code_sha1": sha,
        "code_len": len(code_str),
        "elapsed": round(float(elapsed), 4),
        "ok": bool(ok),
        "error": (error or "")[:_RESULT_MAX_LEN],
        "timed_out": bool(timed_out),
        "source": source or "unknown",
        "code_preview": first_line[:_PREVIEW_LEN],
    }


def _emit_audit(audit, code, perm, row, result):
    try:
        source = audit if isinstance(audit, str) else "unknown"
        record = make_record(code, perm, row, result["elapsed"], result["ok"],
                             result["error"], result["timed_out"], source)
        if callable(audit):
            try:
                audit(record)
                return
            except Exception:
                pass
        audit_write(record)
    except Exception:
        pass


def cleanup_audit(days=None):
    """删除超过保留期的 acrpa_py_*.log。

    days 缺省取 state.LOG_RETENTION_DAYS（try/except 兜底 7）；days<=0 视为永久保留。
    **只**匹配 acrpa_py_*.log，绝不触碰既有的 acrpa_*.log。返回删除数量。
    """
    count = 0
    try:
        if days is None:
            try:
                import state
                days = getattr(state, "LOG_RETENTION_DAYS", 7)
            except Exception:
                days = 7
        try:
            days = int(days)
        except (TypeError, ValueError):
            days = 7
        if days <= 0:
            return 0
        cutoff = time.time() - days * 86400
        log_dir = os.path.join(_program_dir(), "logs")
        pattern = os.path.join(log_dir, "acrpa_py_*.log")
        for path in glob.glob(pattern):
            try:
                if os.path.getmtime(path) < cutoff:
                    os.remove(path)
                    count += 1
            except Exception:
                pass
    except Exception:
        pass
    return count
