# -*- coding: utf-8 -*-
"""ACRPA 无头 / CLI 入口 + JSON 报告 (路线图 §6.5)。

目标形态::

    python run.py --script <PATH> [--var K=V ...] [--json-report OUT.json] [--exit-code] [--quiet]

公开契约
--------
``parse_args(argv) -> dict | None``
    解析命令行。**未给 --script 时返回 None** —— 调用方 (run.py) 据此回退到 GUI 启动。
    绝不抛异常 / 不打印 / 不建窗: run.py 会在任何输入下安全地对它求值。

``main(argv, *, stdout=None) -> int``
    载入脚本 → 执行 → 打印人类可读进度 → (可选) 写 JSON 报告 → 返回退出码。

JSON 报告 schema (UTF-8, 2 空格缩进)::

    {
      "script": "<路径>", "started": "<ISO8601>", "finished": "<ISO8601>",
      "duration_ms": 0, "status": "success|failed",
      "total_rows": 0, "executed_rows": 0,
      "failed_row": null, "error": null,
      "rows": [{"index": 0, "cmd": "日志输出", "status": "ok|error|skipped", "message": ""}]
    }

键位约定
    · ``rows[].index``  : **0 基**源码行序 (与上面的 schema 示例一致);
    · ``failed_row``    : **1 基**行号 (与引擎 / 失败现场 JSON / 人类日志一致), 便于对照;
    · ``executed_rows`` : 实际执行过命令的**源码行数** (去重; 块标记行由引擎内部处理, 不计入);
    · ``rows[].status`` : ok = 该行命令成功 / error = 失败 / skipped = 未执行
                          (分支未走、或失败后按 STOP_ON_ERROR 被跳过)。

设计取舍 (为什么这样接)
------------------------
1. **导入零副作用 / 不碰 GUI**: 本模块顶层只 import 标准库; ``script_io`` / ``state``
   / ``engine`` 一律在 ``main()`` 内惰性导入 → ``import cli`` 既不加载 tkinter、更不建窗。
2. **变量表在哪**: ACRPA 的脚本变量存在 ``engine.variables`` (ExecutionEngine 实例上的
   dict); ``state`` 里只有调试用的 ``state.variables_watch``, **不是**运行时变量表 ——
   已按代码确认, 故 ``--var`` 写入 ``engine.variables``。取值规则与脚本内「设置变量」
   命令**同款**: 纯数值串转 int/float, 其余保留字符串 (这样 ``--var n=7`` 能直接参与
   数学运算/如果条件; 代价是 ``--var code=007`` 会变成 7 —— 需要保留字符串语义时请在
   脚本内用「字符串处理」覆盖)。
3. **执行主循环**: 复用 ``engine.execute_script(rows, script_dir, _nested=True)``。
   传 ``_nested=True`` 是必须的: ``execute_script`` 在「非嵌套」调用时会先
   ``self.variables.clear()``, 会把 --var 预置值抹掉; 嵌套调用跳过该重置。
   代价: 脚本级钩子 (``engine.register_script_hook`` 的 before/after) 不触发 ——
   无头 CLI 不加载 GUI 与插件, 钩子注册表通常为空, 实际无差别。也正因如此, 失败判定
   不依赖 ``engine._script_failed``, 而由下面第 4 条的逐行采集决定。
4. **逐行结果采集**: 执行期间临时包装 ``engine.execute`` (try/finally 还原) 采集每条命令的
   ok/error + message。块标记行 (如果/否则/结束如果/循环开始/循环结束/跳出循环) 由
   ``execute_script`` 内部处理、不经 ``execute``, 故按「是否已被访问」
   (``state.highlight_row``) 近似标为 ok/skipped。
5. **不做的事**: 不读也不写 config.json (用 schema 默认值, 如 retry=3 / STOP_ON_ERROR=True,
   保证 CI 确定性); 不启动调度器 / 托盘 / NetLink / 任何 Tk 窗口。
6. **部分 GUI 依赖命令在无头模式下不可用**: 找图/点图/输入/按键/窗口操作 等需要真实桌面
   会话; 浏览器 (playwright) / OCR 等可选后端未安装时按其能力声明判失败。这些不是本模块
   能兜底的 —— 本模块只保证「引擎主循环可在不创建任何 Tk 窗口的前提下运行」。

退出码
------
* ``0`` 成功 (或脚本失败但未给 --exit-code)
* ``1`` 脚本失败且给了 --exit-code
* ``2`` 用法错误 (未给 --script / 脚本不存在 / 载入失败 / --var 格式非法 / 报告写入失败)
"""
import json
import os
import sys
from datetime import datetime

_USAGE = ("用法: python run.py --script <PATH> [--var K=V ...] "
          "[--json-report OUT.json] [--exit-code] [--quiet]")

# 引擎内部处理、不经 execute() 的块结构标记 (与 engine.execute_script 的分支一致)
_BLOCK_CMDS = frozenset(["如果", "否则", "结束如果", "循环开始", "循环结束", "跳出循环"])

# 需要取值的选项
_VALUE_OPTS = ("--script", "--var", "--json-report")


# ═══════════════════════════════════════════════════════════════════════
# 参数解析
# ═══════════════════════════════════════════════════════════════════════

def parse_args(argv):
    """解析 CLI 参数 → ``dict``; 未给 ``--script`` 时返回 ``None``。

    返回: ``{"script","vars","json_report","exit_code","quiet"}``。

    约定:
      · 支持 ``--opt value`` 与 ``--opt=value`` 两种写法;
      · ``--var`` 可重复出现 (累积, 重复键以后者为准);
      · 未知开关 (含路线图提到的 ``--headless``) 一律忽略 —— 保持对 GUI 参数与
        未来扩展的容忍, 也避免 argparse 在这里抛 SystemExit 影响 run.py 回退分支;
      · 缺值 (如末尾孤立的 ``--script``) 视为「未给脚本」→ 返回 None。

    副作用: 无 (不打印 / 不导入 engine / 不建窗)。
    """
    opts = {"script": None, "vars": [], "json_report": None,
            "exit_code": False, "quiet": False}

    args = [str(a) for a in (argv or [])]
    i, n = 0, len(args)
    while i < n:
        arg = args[i]
        key, sep, inline = arg.partition("=")
        if key in _VALUE_OPTS:
            if sep:
                value = inline
            elif i + 1 < n:
                i += 1
                value = args[i]
            else:
                value = ""          # 缺值 → 交给 main 判为用法错误
            if key == "--script":
                opts["script"] = value
            elif key == "--var":
                opts["vars"].append(value)
            else:
                opts["json_report"] = value
        elif arg == "--exit-code":
            opts["exit_code"] = True
        elif arg in ("--quiet", "-q"):
            opts["quiet"] = True
        i += 1

    if not opts["script"]:
        return None
    return opts


def _coerce_value(text):
    """把取值串转成变量值 —— 规则与脚本内「设置变量」命令 (engine._set_variable) 一致。

    纯数值串 → int / float; 其余 (含空串 / "True" / "0x10") 原样保留字符串。
    保持一致的意义: 脚本里 设置变量 n 7 与 CLI 里 --var n=7 得到同一种值,
    后续 数学运算 / 如果 的行为不会因来源不同而不同。
    """
    s = str(text)
    try:
        if "." in s:
            return float(s)
        return int(s)
    except (ValueError, TypeError):
        return s


def _parse_vars(items):
    """``["K=V", ...]`` → ``({K: V}, 首个非法项 | None)``。

    值经 :func:`_coerce_value` 归一 (与脚本内「设置变量」同款规则);
    重复键后者覆盖前者。
    """
    out = {}
    for it in items:
        key, sep, value = str(it).partition("=")
        key = key.strip()
        if not sep or not key:
            return out, str(it)
        out[key] = _coerce_value(value)
    return out, None


# ═══════════════════════════════════════════════════════════════════════
# 逐行执行迹 (临时包装 engine.execute 采集, 不改引擎)
# ═══════════════════════════════════════════════════════════════════════

class _RowTrace:
    """执行迹: 包装 ``engine.execute`` 采集逐行结果, 并实时打印进度。

    包装是可逆的 (``uninstall``)，且只读地观察返回值 —— 不改变引擎语义:
    handler 抛出的异常 (含 AbortSignal) 原样上抛, 不伪造记录。
    """

    def __init__(self, rows, out=None, quiet=False):
        self._row_no = {}
        for i, row in enumerate(rows, 1):
            self._row_no[id(row)] = i
        self._out = out
        self._quiet = quiet
        self._orig = None
        # 按执行顺序: {"index": 1基, "cmd": str, "ok": bool, "message": str}
        self.records = []

    def install(self, eng):
        self._orig = eng.execute
        eng.execute = self._call

    def uninstall(self, eng):
        if self._orig is None:
            return
        try:
            del eng.execute          # 移除实例属性 → 恢复类上的原方法
        except Exception:
            try:
                eng.execute = self._orig
            except Exception:
                pass
        self._orig = None

    def _call(self, row, script_dir):
        res = self._orig(row, script_dir)

        ok = bool(getattr(res, "ok", True))
        message = str(getattr(res, "message", "") or "")
        code = str(getattr(res, "code", "") or "")
        cmd = getattr(row, "cmd_type", None)
        if cmd is None:
            try:
                cmd = str(row[0].value)
            except Exception:
                cmd = ""
        cmd = str(cmd)

        idx = self._row_no.get(id(row))
        if idx is not None:
            self.records.append({"index": idx, "cmd": cmd, "ok": ok,
                                 "message": message or ("" if ok else code)})
            if not self._quiet:
                _say(self._out, "  [ACRPA CLI] 行 {:<4} {:<12} {}".format(
                    idx, cmd, "OK" if ok else "FAIL  {}".format(message or code)))
        elif not self._quiet:
            _say(self._out, "  [ACRPA CLI] {:<12} {}".format(
                cmd, "OK" if ok else "FAIL  {}".format(message or code)))
        return res


def _build_rows_report(rows, records, last_visited):
    """→ ``(rows_json, executed_rows, failed_row, error)``。

    · 同一条源码行被执行多次 (循环体) 时: 只要出现过失败就以失败为准, 否则保留最新一次;
    · 块标记行按「是否已被访问」近似标记 (引擎对块结构不走 ``execute``, 无法逐行取证)。
    """
    by_index = {}
    for rec in records:
        cur = by_index.get(rec["index"])
        if cur is None or (cur["ok"] and not rec["ok"]):
            by_index[rec["index"]] = rec

    rows_json = []
    failed_row = None
    error = None
    for i, row in enumerate(rows, 1):
        cmd = str(getattr(row, "cmd_type", "") or "")
        rec = by_index.get(i)
        if rec is not None:
            status = "ok" if rec["ok"] else "error"
            message = rec["message"]
        elif cmd in _BLOCK_CMDS and i <= last_visited:
            status, message = "ok", ""
        else:
            status, message = "skipped", ""
        rows_json.append({"index": i - 1, "cmd": cmd,
                          "status": status, "message": message})
        if status == "error" and failed_row is None:
            failed_row, error = i, (message or "命令执行失败")
    return rows_json, len(by_index), failed_row, error


# ═══════════════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════════════

def main(argv, *, stdout=None):
    """执行脚本 → 打印进度 → 写 JSON 报告 → 返回退出码 (0/1/2)。

    ``stdout`` 缺省为 ``sys.stdout``; 测试可传 ``io.StringIO()`` 以捕获输出。
    """
    out = sys.stdout if stdout is None else stdout
    if stdout is None:
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass

    opts = parse_args(argv)
    if opts is None:
        _say(out, "[ACRPA CLI] 用法错误: 缺少 --script")
        _say(out, _USAGE)
        return 2

    script = opts["script"]
    if not os.path.isfile(script):
        _say(out, "[ACRPA CLI] 用法错误: 脚本不存在: {}".format(script))
        return 2

    variables, bad_var = _parse_vars(opts["vars"])
    if bad_var is not None:
        _say(out, "[ACRPA CLI] 用法错误: --var 需要 K=V 形式, 非法项: {!r}".format(bad_var))
        return 2

    quiet = opts["quiet"]
    started = datetime.now()

    # ── 惰性导入: 保证 `import cli` 零副作用 (不加载 tkinter / engine) ──
    import script_io
    import state
    import engine as engine_mod
    eng = engine_mod.engine

    try:
        rows = script_io.load_script(script)      # .acrpas → JSON; .xls/.xlsx → xlrd (复用既有载入路径)
    except Exception as e:
        _say(out, "[ACRPA CLI] 脚本载入失败: {}: {}".format(type(e).__name__, e))
        return 2

    script_abs = os.path.abspath(script)
    script_dir = os.path.dirname(script_abs)

    # 运行态重置 (只碰 state 的公开运行时标志, 不读 config)
    try:
        state.quit2 = False
        state.filename = script_abs
        state.exec_state["total_rows"] = len(rows)
        state.exec_state["row"] = 0
    except Exception:
        pass

    # --var → engine.variables; 先清空以保证同一进程内重复调用互不污染
    try:
        eng.variables.clear()
    except Exception:
        pass
    if variables:
        eng.variables.update(variables)

    if not quiet:
        _say(out, "[ACRPA CLI] 脚本: {} ({} 行)".format(script_abs, len(rows)))
        if variables:
            _say(out, "[ACRPA CLI] 预置变量: {}".format(
                ", ".join("{}={}".format(k, variables[k]) for k in sorted(variables))))

    trace = _RowTrace(rows, out=out, quiet=quiet)
    fatal = None
    trace.install(eng)
    try:
        eng.execute_script(rows, script_dir, _nested=True)
    except Exception as e:                     # 引擎级异常 → 记为失败, 不让调用方崩溃
        fatal = "{}: {}".format(type(e).__name__, e)
    finally:
        trace.uninstall(eng)

    finished = datetime.now()

    last_visited = 0
    try:
        last_visited = int(getattr(state, "highlight_row", 0) or 0)
    except Exception:
        pass

    rows_json, executed_rows, failed_row, error = _build_rows_report(
        rows, trace.records, last_visited)
    if fatal is not None and failed_row is None:
        error = fatal
    failed = failed_row is not None or fatal is not None

    report = {
        "script": script_abs,
        "started": started.isoformat(timespec="seconds"),
        "finished": finished.isoformat(timespec="seconds"),
        "duration_ms": int((finished - started).total_seconds() * 1000),
        "status": "failed" if failed else "success",
        "total_rows": len(rows),
        "executed_rows": executed_rows,
        "failed_row": failed_row,
        "error": error,
        "rows": rows_json,
    }

    if not quiet:
        _say(out, "[ACRPA CLI] 结果: {} | 总行数 {} | 实际执行 {} 行 | 用时 {} ms".format(
            report["status"], report["total_rows"],
            report["executed_rows"], report["duration_ms"]))
        if failed:
            _say(out, "[ACRPA CLI] 失败行: {}  错误: {}".format(
                failed_row if failed_row is not None else "-", error or ""))

    text = json.dumps(report, ensure_ascii=False, indent=2)
    if opts["json_report"]:
        try:
            _write_report(opts["json_report"], text)
        except Exception as e:
            _say(out, "[ACRPA CLI] 报告写入失败 ({}): {}: {}".format(
                opts["json_report"], type(e).__name__, e))
            return 2
        if not quiet:
            _say(out, "[ACRPA CLI] JSON 报告: {}".format(os.path.abspath(opts["json_report"])))
    elif not quiet:
        _say(out, text)          # 未给 --json-report: 同一份报告打到 stdout (便于管道捕获)

    if failed and opts["exit_code"]:
        return 1
    return 0


def _write_report(path, text):
    """UTF-8 原子写入 JSON 报告 (临时文件 + os.replace), 失败抛异常由 main 转退出码 2。"""
    path = os.path.abspath(str(path))
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)


def _say(out, msg):
    """向 ``out`` 写一行; 任何 IO/编码异常都不得影响退出码判定。"""
    if out is None:
        return
    try:
        out.write(msg + "\n")
        flush = getattr(out, "flush", None)
        if flush is not None:
            flush()
    except Exception:
        pass


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
