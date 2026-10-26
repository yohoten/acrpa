# -*- coding: utf-8 -*-
"""运行历史持久化 (方案文档 §5 L5 #9) —— ``history.jsonl``。

一行一次运行，字段固定::

    {"ts":"2025-01-01T12:00:00","kind":"script|workflow|task","target":"…",
     "trigger":"cli|manual|sched","duration_ms":123,"status":"success|failed",
     "failed_row":null,"rows_total":42,"mode":"headless|gui","detail":"…"}

三条硬约束
----------
1. **落盘前必须脱敏**（§8.6）: 所有字符串过 ``interp.redact``（Bearer /
   ``token=`` / ``Authorization:`` / Cookie / 长 hex 串），因此 HTTP URL 里的
   ``?token=...``、Python 参数里的密钥都不会进历史文件。
2. **轮转/上限**: 单文件超过 ``state.HISTORY_MAX_MB``(默认 5 MB) 即改名归档为
   ``history-YYYYMMDD-HHMMSS.jsonl``，只保留 ``state.HISTORY_KEEP_FILES``(默认 5)
   份。写盘失败只记 warning，绝不影响主流程。
3. **零第三方依赖 + 不在导入期做 IO**: ``import history`` 无副作用，路径与阈值
   在调用时读取（便于测试用 :func:`configure` 指向临时目录）。

接线点（本轮）: ``cli.main()`` 的 script / workflow 运行、``--exec-task`` 转发、
以及 ``install_scheduler_hook()`` 包装的 ``scheduler._add_task_log``（调度器
模块本身不改，包装只旁路监听、不改变其行为）。
"""
import json
import os
import threading
import time

_LOCK = threading.RLock()

# 测试/嵌入方可覆盖 (None = 按 state 动态解析)
_PATH_OVERRIDE = None
_MAX_BYTES_OVERRIDE = None
_KEEP_OVERRIDE = None

DEFAULT_MAX_MB = 5
DEFAULT_KEEP = 5
DETAIL_MAX = 500            # detail 字段截断长度 (脱敏前先截断)


def configure(path=None, max_bytes=None, keep=None):
    """覆盖历史文件路径/阈值（测试用；传 None 恢复默认）。"""
    global _PATH_OVERRIDE, _MAX_BYTES_OVERRIDE, _KEEP_OVERRIDE
    with _LOCK:
        _PATH_OVERRIDE = path
        _MAX_BYTES_OVERRIDE = max_bytes
        _KEEP_OVERRIDE = keep


def history_file():
    """→ 历史文件绝对路径。

    优先级: :func:`configure` 覆盖 > ``ACRPA_HISTORY_PATH`` 环境变量 >
    ``state.history_path()``（便携模式自动指向 userdata/logs）。
    """
    if _PATH_OVERRIDE:
        return os.path.abspath(str(_PATH_OVERRIDE))
    env = os.environ.get("ACRPA_HISTORY_PATH")
    if env:
        return os.path.abspath(env)
    try:
        import state
        return state.history_path()
    except Exception:
        return os.path.join(os.getcwd(), "history.jsonl")


def _max_bytes():
    if _MAX_BYTES_OVERRIDE is not None:
        return max(0, int(_MAX_BYTES_OVERRIDE))
    try:
        import state
        mb = int(getattr(state, "HISTORY_MAX_MB", DEFAULT_MAX_MB) or DEFAULT_MAX_MB)
    except Exception:
        mb = DEFAULT_MAX_MB
    return max(0, mb) * 1024 * 1024


def _keep_files():
    if _KEEP_OVERRIDE is not None:
        return max(0, int(_KEEP_OVERRIDE))
    try:
        import state
        return max(0, int(getattr(state, "HISTORY_KEEP_FILES", DEFAULT_KEEP)
                          or DEFAULT_KEEP))
    except Exception:
        return DEFAULT_KEEP


# ══════════════════════════════════════════════════════════════════════
# 脱敏
# ══════════════════════════════════════════════════════════════════════

def sanitize(value, _depth=0):
    """递归脱敏: 字符串走 interp.redact，容器递归，其余原样。"""
    if _depth > 6:
        return "…"
    if isinstance(value, str):
        return _redact(value)
    if isinstance(value, (bytes, bytearray)):
        try:
            return _redact(bytes(value).decode("utf-8", "replace"))
        except Exception:
            return "<binary>"
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            key = str(k)
            out[_redact(key)[:64]] = sanitize(v, _depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        return [sanitize(v, _depth + 1) for v in value]
    return value


def _redact(text):
    """先截断再脱敏（避免对超长串做无谓正则）。"""
    try:
        s = str(text)
    except Exception:
        return ""
    if len(s) > DETAIL_MAX:
        s = s[:DETAIL_MAX] + "…"
    try:
        import interp
        return interp.redact(s)
    except Exception:
        return s


# ══════════════════════════════════════════════════════════════════════
# 写入
# ══════════════════════════════════════════════════════════════════════

def make_record(kind, target, trigger="", duration_ms=None, status="success",
                failed_row=None, rows_total=None, mode="", detail=""):
    """→ 已脱敏的记录 dict（不写盘）。"""
    rec = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "kind": _redact(kind)[:32],
        "target": _redact(target),
        "trigger": _redact(trigger)[:32],
        "duration_ms": None if duration_ms is None else int(duration_ms),
        "status": _redact(status)[:16],
        "failed_row": failed_row if isinstance(failed_row, int) else None,
        "rows_total": rows_total if isinstance(rows_total, int) else None,
        "mode": _redact(mode)[:16],
        "detail": _redact(detail),
    }
    return rec


def record(kind, target, trigger="", duration_ms=None, status="success",
           failed_row=None, rows_total=None, mode="", detail=""):
    """追加一行历史 → 记录 dict（写盘失败返回记录本身，不抛）。"""
    rec = make_record(kind, target, trigger, duration_ms, status,
                      failed_row, rows_total, mode, detail)
    try:
        append_record(rec)
    except Exception:
        pass
    return rec


def append_record(rec):
    """把**已脱敏**的记录写盘（内部 API；外部请用 :func:`record`）。"""
    line = json.dumps(sanitize(rec), ensure_ascii=False, separators=(",", ":"))
    path = history_file()
    with _LOCK:
        parent = os.path.dirname(path)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent, exist_ok=True)
        rotate_if_needed(path)
        with open(path, "a", encoding="utf-8", newline="\n") as f:
            f.write(line + "\n")
    return True


# ══════════════════════════════════════════════════════════════════════
# 轮转 / 上限
# ══════════════════════════════════════════════════════════════════════

def rotated_files(path=None):
    """→ 已有的归档文件列表（按时间升序）。"""
    base = os.path.basename(path or history_file())
    stem = base[:-6] if base.endswith(".jsonl") else base
    folder = os.path.dirname(path or history_file()) or "."
    try:
        names = [n for n in os.listdir(folder)
                 if n.startswith(stem + "-") and n.endswith(".jsonl")]
    except Exception:
        return []
    names.sort()
    return [os.path.join(folder, n) for n in names]


def rotate_if_needed(path=None, force=False):
    """超过上限则归档并清理旧归档 → True 表示发生了轮转。"""
    path = path or history_file()
    limit = _max_bytes()
    try:
        if not os.path.isfile(path):
            return False
        size = os.path.getsize(path)
        if not force and (limit <= 0 or size <= limit):
            return False
        stamp = time.strftime("%Y%m%d-%H%M%S")
        target = os.path.join(os.path.dirname(path) or ".",
                              "{}-{}.jsonl".format(_stem(path), stamp))
        n = 1
        while os.path.exists(target):
            target = os.path.join(os.path.dirname(path) or ".",
                                  "{}-{}-{}.jsonl".format(_stem(path), stamp, n))
            n += 1
        os.replace(path, target)
        _prune(path)
        return True
    except Exception:
        return False


def _stem(path):
    base = os.path.basename(path)
    return base[:-6] if base.endswith(".jsonl") else base


def _prune(path):
    keep = _keep_files()
    files = rotated_files(path)
    for old in files[:max(0, len(files) - keep)]:
        try:
            os.remove(old)
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════════════
# 读取（供任务管理窗展示「上次结果」）
# ══════════════════════════════════════════════════════════════════════

def read_recent(limit=50, path=None):
    """→ 最近 ``limit`` 条记录（新的在前）；文件不存在返回 []。"""
    path = path or history_file()
    if limit is None or limit <= 0:
        return []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except Exception:
        return []
    out = []
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
        if len(out) >= limit:
            break
    return out


def last_result(kind=None, target=None, path=None):
    """→ 指定 kind/target 最近一条记录（任务列表「上次结果」用）。"""
    for rec in read_recent(limit=200, path=path):
        if kind is not None and rec.get("kind") != kind:
            continue
        if target is not None and rec.get("target") != target:
            continue
        return rec
    return None


# ══════════════════════════════════════════════════════════════════════
# 调度器旁路监听（不改 scheduler.py: 只包装其日志函数）
# ══════════════════════════════════════════════════════════════════════

_SCHED_HOOKED = False


def install_scheduler_hook():
    """包装 ``scheduler._add_task_log``，把每次任务运行结果同步进历史。

    必须在 GUI 启动后调用（scheduler 默认值/task 库此时已就绪）。幂等。
    包装是**旁路**的: 先调原函数，再异步写历史；原函数异常照旧上抛。
    """
    global _SCHED_HOOKED
    if _SCHED_HOOKED:
        return False
    try:
        import scheduler
    except Exception:
        return False
    original = getattr(scheduler, "_add_task_log", None)
    if not callable(original):
        return False

    def _wrapped(task_name, script_name, status, trigger="定时",
                 log_type="info", folder="", reason=""):
        result = original(task_name, script_name, status, trigger,
                          log_type, folder, reason)
        try:
            text = str(status or "")
            if "运行中" in text:
                return result          # 只记终态
            failed = any(k in text for k in ("失败", "错误", "超时", "中止"))
            record(kind="task",
                   target=script_name or task_name or "",
                   trigger=trigger or "sched",
                   duration_ms=None,
                   status="failed" if failed else "success",
                   failed_row=None,
                   rows_total=None,
                   mode="gui",
                   detail=text if reason is None else "{} {}".format(text, reason))
        except Exception:
            pass
        return result

    try:
        scheduler._add_task_log = _wrapped
        _SCHED_HOOKED = True
        return True
    except Exception:
        return False
