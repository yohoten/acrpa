"""py_sandbox.py — 自定义 Python 代码的受限执行沙箱（纯标准库）。

设计目标
--------
* 可被独立 import：本模块**不**导入 engine / pyautogui / requests / tkinter 等
  GUI/第三方库，便于单元测试与打包裁剪（唯一例外：cleanup_audit() 内惰性
  import state 仅用于读取日志保留天数，且 try/except 兜底）。
* 三级权限：sandbox（默认，最严）/ trusted（受信）/ full（独立子进程，默认禁用）。
* 执行前 AST 预检（precheck）+ 执行期超时：
    - sandbox / trusted：sys.settrace 行级时间检查（finally 中必定清理）
      + 看门狗线程（墙钟计时，超时经 ctypes.PyThreadState_SetAsyncExc 向
      执行线程注入 TimeoutError）双层防护，并临时屏蔽 sys.settrace /
      sys.setprofile，使被测代码无法用 sys.settrace(None) 关闭超时检查
      （BUG-04）。残留限制：C 层阻塞（如 time.sleep）无法被按时中断，
      仅在其返回后抛出超时异常（详见 tools/_fix_report_B.md）。
    - full：subprocess timeout + kill（不使用 multiprocessing）。
* 审计：每次执行写一行 JSON 到 <程序目录>/logs/acrpa_py_YYYYMMDD.log，
  只记录 code_sha1 / 长度 / 首行预览，绝不写代码全文。

对外接口
--------
    PERM_SANDBOX / PERM_TRUSTED / PERM_FULL / VALID_PERMS
    FORBIDDEN_CALLS / FORBIDDEN_ATTR_CALLS / FORBIDDEN_ATTR_PREFIX / SANDBOX_BUILTINS
    precheck(code, perm=PERM_SANDBOX) -> (bool, str)
    run(code, perm=PERM_SANDBOX, timeout=None, api=None, audit=None, row=0, log=None) -> dict
    audit_path() -> str
    audit_write(record) -> None
    make_record(code, perm, row, elapsed, ok, error, timed_out, source) -> dict
    cleanup_audit(days=None) -> int
"""
import ast
import base64
import builtins
import ctypes
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
# §1.1：format / format_map 原先在 SANDBOX_BUILTINS 白名单里，可通过
#       "{0.__class__...}".format(x) 穿透读取任意 dunder 属性（AST 看不到
#       format 字符串内部的纯文本）。现移出白名单并加入拒绝名单。
FORBIDDEN_CALLS = {"eval", "exec", "compile", "open", "input", "breakpoint",
                   "__import__", "globals", "locals", "vars", "getattr", "setattr",
                   "delattr", "memoryview", "classmethod", "staticmethod", "super",
                   "format", "format_map"}
# sandbox 下拒绝的「属性调用」名（形如 "<str>".format(...) / x.format_map(...)）。
FORBIDDEN_ATTR_CALLS = frozenset(("format", "format_map"))
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
#       input/help/breakpoint 等危险或具逃逸能力的内建，也不含 format
#       （§1.1：可经 format 字符串读取 dunder，见 FORBIDDEN_ATTR_CALLS）。
SANDBOX_BUILTINS = {
    "abs": abs, "all": all, "any": any, "bool": bool, "dict": dict,
    "divmod": divmod, "enumerate": enumerate, "filter": filter,
    "float": float, "frozenset": frozenset, "int": int,
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
        "forbidden dunder in string"
        "code too large"

    §1.1：sandbox 档额外拒绝「含 __ 的字符串常量」（含 f-string 字面量片段），
    以及 str.format / str.format_map 属性调用；trusted / full 语义不变。
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
            # §1.1：拒绝任何含 "__" 的字符串常量（含 f-string 字面量片段）。
            # format 字符串内部的 {0.__class__} 是纯文本、AST 不可见，故必须按
            # 字面量文本拦截；拼接绕过（"{0." + "__class__" + "}"）亦被此规则覆盖。
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if "__" in node.value:
                    return (False, "forbidden dunder in string")
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                if sandbox and func.id in FORBIDDEN_CALLS:
                    return (False, "forbidden call: %s" % func.id)
                if (not sandbox) and func.id in _CORE_UNSAFE_CALLS:
                    return (False, "forbidden call: %s" % func.id)
            elif sandbox and isinstance(func, ast.Attribute):
                # §1.1：拒绝 "<str>".format(...) / x.format_map(...) 属性调用。
                if func.attr in FORBIDDEN_ATTR_CALLS:
                    return (False, "forbidden call: %s" % func.attr)
        if sandbox and isinstance(node, ast.Attribute):
            attr = node.attr
            if isinstance(attr, str) and attr.startswith(FORBIDDEN_ATTR_PREFIX):
                return (False, "forbidden attribute: %s" % attr)
    return (True, "")


# ======================================================================
# 执行
# ======================================================================
def run(code, perm=PERM_SANDBOX, timeout=None, api=None, audit=None, row=0, log=None):
    """执行代码。返回 {ok,result,raw_result,error,elapsed,perm,timed_out}，绝不抛异常。

    api    : 暴露给脚本的 acrpa 对象（AcrpaAPI 实例），可为 None。
    audit  : 审计来源标记：字符串（"excel"/"plugin"/"hook"/"console"）或可调用对象
             （接收 record dict）。字符串会写入审计记录的 source 字段。
    row    : 当前执行行号（写入审计）。
    log    : §1.5 可注入日志回调 log(msg, level="info")，用于转发脚本 print /
             子进程日志；缺省依次退化 utils.log1 → sys.stderr → 安全 no-op
             （py_sandbox 不硬依赖 GUI）。

    返回字典保持既有字段语义；raw_result 为加性字段（P1 §2.1 写回变量用，本批次
    不强制消费）。
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
    result = {"ok": False, "result": None, "raw_result": None, "error": "",
              "elapsed": 0.0, "perm": perm, "timed_out": False}

    try:
        ok, reason = precheck(code, perm)
        if not ok:
            result["error"] = reason
        elif perm == PERM_FULL:
            _exec_full(code, timeout, result, log)
        else:
            _exec_inprocess(code, perm, timeout, api, result, log)
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


def _rewrite_last_expr(tree):
    """§1.4：把最后一条 ast.Expr 语句改写为 `result = <expr>`（只执行一次）。

    原先 `_extract_result` 会在末行表达式为 None 时重新 eval 一次，导致同一语句
    执行两次（末行是 `acrpa.click(...)` / `x.append(1)` 时即真实的双击 / 重复输入）。
    改写后单次执行，值直接落入 `result`。
    """
    try:
        body = tree.body
        if body and isinstance(body[-1], ast.Expr):
            last = body[-1]
            assign = ast.Assign(targets=[ast.Name(id="result", ctx=ast.Store())],
                                value=last.value)
            ast.copy_location(assign, last)
            ast.fix_missing_locations(assign)
            body[-1] = assign
    except Exception:
        pass
    return tree


def _compile_units(code):
    """解析源码 + §1.4 末行改写，返回可 exec 的 code object。"""
    return compile(_rewrite_last_expr(ast.parse(code)), "<acrpa-py>", "exec")


def _default_log(msg, level="info"):
    """print 转发兜底通道：ACRPA 日志面板（utils.log1）→ sys.stderr → 安全 no-op。

    惰性 import utils 以避免 py_sandbox 硬依赖 GUI；任何失败都不得影响脚本执行。
    """
    try:
        import utils
        fn = getattr(utils, "log1", None)
        if callable(fn):
            fn(msg, level)
            return
    except Exception:
        pass
    try:
        if sys.stderr is not None:
            sys.stderr.write(str(msg) + "\n")
    except Exception:
        pass


def _resolve_log(log):
    """可注入日志回调；缺省退化为 _default_log（不硬依赖 GUI）。"""
    return log if callable(log) else _default_log


def _make_print(log):
    """构造转发到 ACRPA 日志的 print 包装（§1.5）。

    保留 sep/end 语义；显式 file 参数时仍写该流；打包版 sys.stdout=None 时
    print 不再静默丢失。
    """
    def _print(*args, sep=" ", end="\n", file=None, flush=False):
        try:
            if file is not None:
                print(*args, sep=sep, end=end, file=file, flush=flush)
                return
            text = sep.join([str(a) for a in args])
            if end and end != "\n":
                text += end
            log("[py] " + text, "info")
        except Exception:
            pass

    return _print


# ── BUG-04：超时加固（追踪器保护 + 看门狗线程异步异常注入）──
_WATCHDOG_GRACE = 0.3      # 看门狗在行级追踪器之后额外等待的宽限秒数
_WATCHDOG_JOIN = 2.0       # 注入后等待执行线程自然退出的上限秒数


def _block_trace_call(tracefunc=None):
    """执行期间替代 sys.settrace / sys.setprofile 的占位实现。

    BUG-04：被测代码可调用 sys.settrace(None) 关闭逐行时间检查，从而绕过
    超时。此处对执行窗口内的一切外部调用一律忽略（既不清理也不替换），
    使内层 tracer 始终生效；窗口结束后在 finally 中恢复原函数。
    """
    return None


def _async_raise(ident, exc_type):
    """向 ident 线程异步注入 exc_type（Windows 下唯一可行的强制中断手段）。

    Python 无跨平台取消线程 API；ctypes 调用 PyThreadState_SetAsyncExc 可在
    目标线程下一字节码边界抛出异常（纯 Python 死循环即刻生效）。返回影响
    的线程数，0 表示失败（已吞异常，绝不抛出）。
    """
    try:
        res = ctypes.pythonapi.PyThreadState_SetAsyncExc(
            ctypes.c_ulong(ident), ctypes.py_object(exc_type))
    except Exception:
        return 0
    if res > 1:
        # 目标不唯一：立即回滚，避免误伤其它线程。
        try:
            ctypes.pythonapi.PyThreadState_SetAsyncExc(
                ctypes.c_ulong(ident), None)
        except Exception:
            pass
    return res


def _start_watchdog(deadline, ident, state):
    """启动守护线程，按墙钟时间监测执行线程耗时；超时则注入 TimeoutError。

    与行级追踪器（sys.settrace）互补：后者按「每 _TRACE_STEP 个 line 事件」
    检查时间，可被 sys.settrace(None) 关闭；本看门狗基于墙钟，不受其影响，
    对纯 Python 死循环可强制中断。返回 stop 事件用于协同收尾并避免线程泄漏。
    残留限制：C 层阻塞（如 time.sleep）期间注入的异常无法即时生效，仅在其
    返回后于下一字节码边界抛出。
    """
    stop = threading.Event()

    def _watch():
        # 先等到 deadline，再给行级追踪器留一小段优先响应窗口，避免二者
        # 同时触发造成重复抛异常。
        delay = max(0.0, deadline - time.time()) + _WATCHDOG_GRACE
        if stop.wait(delay):
            return
        if state.get("exec_done") or stop.is_set():
            return
        # 追踪器可能已被绕过（BUG-04）：向执行线程注入超时异常。
        _async_raise(ident, TimeoutError)
        # 等待执行线程自然退出（纯 Python 循环会立即抛出）；C 层阻塞期间
        # 该 wait 超时后线程自行退出（daemon），不产生常驻泄漏。
        stop.wait(_WATCHDOG_JOIN)

    th = threading.Thread(target=_watch, name="acrpa-py-watchdog", daemon=True)
    th.start()
    return stop


def _stop_watchdog(stop):
    """通知看门狗线程停止（幂等、失败静默）。"""
    try:
        if stop is not None:
            stop.set()
    except Exception:
        pass


def _exec_inprocess(code, perm, timeout, api, result, log=None):
    """sandbox / trusted 在当前进程内执行（单一命名空间）。

    BUG-04 加固：保留 sys.settrace 行级检查作为兜底，同时叠加
    (1) 追踪器保护——执行期间屏蔽 sys.settrace / sys.setprofile，使被测代码
        无法用 sys.settrace(None) 关闭超时检查；
    (2) 看门狗线程——按墙钟时间在 deadline 后经 PyThreadState_SetAsyncExc
        注入 TimeoutError，覆盖纯 Python 死循环。
    对外语义不变：超时仍抛 TimeoutError 并置 timed_out=True。
    """
    if perm == PERM_TRUSTED:
        ns = {"__builtins__": _real_builtins(), "acrpa": api, "result": None}
    else:
        ns = {"__builtins__": dict(SANDBOX_BUILTINS), "acrpa": api, "result": None}
    # §1.5：print 转发到 ACRPA 日志（修复打包版 windowed 模式静默丢失）。
    ns["print"] = _make_print(_resolve_log(log))

    compiled = _compile_units(code)
    deadline = time.time() + timeout
    tracer = _make_tracer(deadline)
    ident = threading.get_ident()
    state = {"exec_done": False}

    # BUG-04：捕获真实 settrace/setprofile，执行窗口内用占位实现接管其对外
    # 可见属性，屏蔽被测代码关闭追踪的企图；窗口结束后必定恢复。
    orig_settrace = sys.settrace
    orig_setprofile = sys.setprofile
    sys.settrace = _block_trace_call
    sys.setprofile = _block_trace_call

    orig_settrace(tracer)
    watchdog_stop = _start_watchdog(deadline, ident, state)
    try:
        exec(compiled, ns)
    except TimeoutError as e:
        result["timed_out"] = True
        result["error"] = "TimeoutError: %s" % (str(e) or "code timeout")
        return
    except Exception as e:
        result["error"] = "%s: %s" % (type(e).__name__, e)
        return
    finally:
        # 关键：先置 exec_done 并停看门狗，再清除 trace 并恢复原函数，
        # 避免看门狗误注入到窗口之外，也避免追踪拖慢进程后续执行。
        state["exec_done"] = True
        _stop_watchdog(watchdog_stop)
        orig_settrace(None)
        sys.settrace = orig_settrace
        sys.setprofile = orig_setprofile

    result["ok"] = True
    result["raw_result"] = ns.get("result")
    result["result"] = _extract_result(ns)


def _extract_result(ns):
    """§1.4：只读取命名空间中的 result 变量（不再对末行重新 eval）。

    返回 repr 截断字符串；无结果返回 None。
    """
    val = ns.get("result") if isinstance(ns, dict) else None
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
# §1.4：子进程内同样做「末行 Expr → result=」改写（杜绝二次执行）。
# §1.5：用户 print 重定向到 stderr（日志通道），结果带标记单独走原 stdout，
#       result 不再被用户输出淹没。
_RESULT_MARKER = "ACRPA-RESULT:"
_FULL_WRAPPER = (
    "import sys, base64, ast\n"
    "src = base64.b64decode(sys.stdin.buffer.read()).decode('utf-8')\n"
    "tree = ast.parse(src)\n"
    "if tree.body and isinstance(tree.body[-1], ast.Expr):\n"
    "    _last = tree.body[-1]\n"
    "    tree.body[-1] = ast.Assign(targets=[ast.Name(id='result', "
    "ctx=ast.Store())], value=_last.value)\n"
    "    ast.fix_missing_locations(tree)\n"
    "_real_out = sys.stdout\n"
    "sys.stdout = sys.stderr\n"
    "ns = {'__name__': '__main__', 'result': None}\n"
    "exec(compile(tree, '<acrpa-py-full>', 'exec'), ns)\n"
    "r = ns.get('result')\n"
    "if r is not None:\n"
    "    _real_out.write('" + _RESULT_MARKER + "' + repr(r))\n"
    "_real_out.flush()\n"
)


def _exec_full(code, timeout, result, log=None):
    """full 权限：subprocess 独立进程，超时 kill。不使用 multiprocessing。

    §1.5：日志通道（用户 print → stderr，转发进 ACRPA 日志）与结果通道
    （result → 带标记的 stdout）分离。
    """
    log_fn = _resolve_log(log)
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

    txt_out = (out or b"").decode("utf-8", "replace")
    txt_err = (err or b"").decode("utf-8", "replace")
    if proc.returncode == 0:
        result["ok"] = True
        # §1.5：result 只认带标记的结果通道，stdout 其余内容不再充当 result。
        out_txt = txt_out.strip()
        if out_txt.startswith(_RESULT_MARKER):
            val = out_txt[len(_RESULT_MARKER):]
            result["result"] = val if val else None
            result["raw_result"] = val if val else None
        log_txt = txt_err.strip()
        if log_txt:
            for ln in log_txt.splitlines():
                try:
                    log_fn("[py] " + ln, "info")
                except Exception:
                    pass
    else:
        msg = txt_err.strip() or ("exit code %s" % proc.returncode)
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
