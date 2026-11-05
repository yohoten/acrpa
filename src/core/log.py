# -*- coding: utf-8 -*-
"""core.log — 日志总线 (纯 Python, 无 tkinter 依赖)。

从 ``src/utils.py`` 迁出 (docs/项目结构优化建议.md §3.1 / §6.5), 目的:
让领域层 (``engine`` / ``workflow``) 在无 tkinter 的解释器里也能被导入,
解锁 CLI / 无头模式。

GUI 渲染所需的 Text 控件由外部注入 (``set_tlog``), 本模块绝不 import
tkinter。文件落盘走批量缓冲 + 每日轮转 + 保留天数清理 (P0#5 / P1)。

依赖: 标准库 (os / time / queue / glob) + ``state`` (state 本身无 tkinter)。
"""
import os
import time
import queue
import glob

import state


# ── 运行时回退开关 (v5 §3.4 L4 / §7.2) ──
def _env_flag(name, default=True):
    """环境变量开关: 缺失/空 → default; 0/false/no/off → False。"""
    try:
        v = os.environ.get(name)
    except Exception:
        return default
    if v is None or str(v).strip() == "":
        return default
    return str(v).strip().lower() not in ("0", "false", "no", "off")


# True : 行首色条字形 ▌ 由生产端拼进文本 → flush 的 1 次 insert 就带好字形,
#        ui.log_dock 不必再逐行 insert("▌") (400 行/批 ≈1200 次 Tcl → 0 次额外)。
# False: 恢复旧行为 —— 由 ui.log_dock._log_apply_cbar 逐行插入 ▌ 并 tag_add。
LOG_CBAR_INLINE = _env_flag("LOG_CBAR_INLINE", True)

# ── LogLevel constants ──
LOG_DEBUG = 0
LOG_INFO = 1
LOG_WARNING = 2
LOG_ERROR = 3

_LOG_LEVEL_NAMES = {
    LOG_DEBUG: "DEBUG",
    LOG_INFO: "INFO",
    LOG_WARNING: "WARNING",
    LOG_ERROR: "ERROR",
}

# ── 日志面板级别徽标 (唯一权威: docs/UI美化设计方案.md §2.5) ──
# 面板四级 = INFO / SUCCESS / WARNING / ERROR; 徽标文案**由 tag 判定**
# (log1(msg,"success") 的 int 级别实为 LOG_INFO, 但徽标须为 [SUCCESS]),
# tag 缺失/未知时回退到 level int。SUGGEST 不存在, 不得引入。
_LOG_BADGE_BY_TAG = {
    "info": "INFO",
    "success": "SUCCESS",
    "warning": "WARNING",
    "error": "ERROR",
}


def _log_badge(tag, level):
    """按 tag 决定日志级别徽标文案 (§2.5: tag 优先, 其次 level int)。"""
    key = str(tag).lower() if tag else ""
    if key in _LOG_BADGE_BY_TAG:
        return _LOG_BADGE_BY_TAG[key]
    return _LOG_LEVEL_NAMES.get(level, "INFO")


# === Logging bridge ===
_tlog = None

# ── 启动早期日志缓冲 (G2): log1 早于 set_tlog 调用时暂存, 日志系统就绪后回放 ──
# 现状缺口: `run.py` 在 `crashlog.install()`/`startup_mark()` 阶段就会经 crashlog._log1
# 调 `utils.log1`, 而此时 `_tlog` 还是 None → 这些「启动前日志」被 `if _tlog:` 静默丢弃。
# 这里改为暂存进有界缓冲, `set_tlog` 就绪时一次性回放, 保证启动早期异常/阶段日志不丢。
_EARLY_LOG = []                # [(msg, tag, level), ...]
_EARLY_LOG_MAX = 200           # 环形上限: 最多留 200 条 (防御: 无界会随长时间无 GUI 而膨胀)


def _normalize_level(level, tag):
    """level → int (LOG_DEBUG..LOG_ERROR); None 时按 tag 推断。与 log1 既有口径一致。"""
    if level is None:
        tag_lower = str(tag).lower() if tag else ""
        if tag_lower == "error":
            return LOG_ERROR
        if tag_lower == "warning":
            return LOG_WARNING
        if tag_lower == "debug":
            return LOG_DEBUG
        return LOG_INFO
    if isinstance(level, str):
        return {"DEBUG": LOG_DEBUG, "INFO": LOG_INFO,
                "WARNING": LOG_WARNING, "ERROR": LOG_ERROR}.get(level.upper(), LOG_INFO)
    try:
        return int(level)
    except (TypeError, ValueError):
        return LOG_INFO


def _early_log_append(msg, tag, level):
    try:
        if len(_EARLY_LOG) >= _EARLY_LOG_MAX:
            _EARLY_LOG.pop(0)
        _EARLY_LOG.append((msg, tag, level))
    except Exception:
        pass


def _early_log_replay():
    """把启动早期缓冲回放到已就绪的 `_tlog`; 返回回放条数 (失败静默)。"""
    global _EARLY_LOG
    if not _EARLY_LOG or _tlog is None:
        return 0
    try:
        pending = _EARLY_LOG
        _EARLY_LOG = []
        n = 0
        for msg, tag, level in pending:
            try:
                _tlog.put(msg, tag, "", "", _normalize_level(level, tag))
                n += 1
            except Exception:
                pass
        return n
    except Exception:
        return 0

# === Log sink mirror (NetLink) ===
_log_sinks = []   # [(sink_fn,), ...] 由 netlink 注册


def register_log_sink(fn):
    """注册日志镜像回调 fn(msg, tag, level, caller_file, caller_func)。重复注册只登记一次。"""
    if fn not in _log_sinks:
        _log_sinks.append(fn)


def unregister_log_sink(fn):
    global _log_sinks
    _log_sinks = [f for f in _log_sinks if f is not fn]


def _broadcast_log(msg, tag, level, caller_file="", caller_func=""):
    """把一条日志镜像给所有 sink。总线未启动时零开销；任何 sink 抛错静默忽略。"""
    if not _log_sinks:
        return
    for fn in list(_log_sinks):
        try:
            fn(msg, tag, level, caller_file, caller_func)
        except Exception:
            pass


def set_tlog(instance):
    global _tlog
    _tlog = instance
    # G2: 日志总线就绪 → 回放启动早期缓冲 (不丢 run.py 阶段已产生的日志)
    if instance is not None:
        _early_log_replay()


def get_tlog():
    """返回当前日志总线实例 (可能为 None)。与 set_tlog 对称, 供测试/诊断读取。"""
    return _tlog


def log1(msg, tag=None, level=None):
    """Thread-safe logging with automatic caller info (file + function name).

    Args:
        msg: Log message text
        tag: Optional tkinter Text widget tag for GUI coloring
             Also auto-maps to level: "error"→ERROR, "warning"→WARNING, "info"→INFO, "debug"→DEBUG
        level: Log level override — "DEBUG"/"INFO"/"WARNING"/"ERROR" or int 0-3.
               If None, auto-detected from tag. Defaults to INFO.
    """
    if _tlog is None:
        # G2: 日志总线尚未就绪 (run.py 早期) → 暂存, set_tlog 时回放
        _early_log_append(msg, tag, level)
        return

    # ── Auto-detect level from tag if not explicitly provided ──
    level = _normalize_level(level, tag)

    # ── Capture caller's filename and function name ──
    import inspect
    caller_file = ""
    caller_func = ""
    try:
        frame = inspect.currentframe()
        # Go up 1 frame to get the actual caller of log1
        caller = frame.f_back
        if caller is not None:
            caller_file = os.path.basename(caller.f_code.co_filename)
            caller_func = caller.f_code.co_name
    except Exception:
        pass
    # run_id: 统一运行标识随日志一起入队, 落盘时写入文件行 (GUI 行不显示, 保持简洁)。
    try:
        from core import run_id as _run_id
        _rid = _run_id.current()
    except Exception:
        _rid = ""
    _tlog.put(msg, tag, caller_file, caller_func, level, _rid)


class ThreadSafeLog:
    """
    Thread-safe logging with batched file writes (P0 optimization #5).

    Batches logs to reduce I/O operations by ~80%.
    Writes when buffer reaches BATCH_SIZE or FLUSH_INTERVAL seconds.

    P1 Enhancement: Structured logging with LogLevel filtering, daily rotation,
    and automatic old log cleanup (  + LogSavePath).
    """
    # P0 Optimization #5: Batch configuration
    BATCH_SIZE = 10          # Write after accumulating N messages
    FLUSH_INTERVAL = 1.0     # Or write every N seconds

    # UI 侧批量渲染 (v3 §4.1): 单次 flush 最多推给控件的条数。
    # 超出部分留在队列里给下一个 tick, 保证一次 flush 的重绘成本有上界。
    GUI_BATCH_MAX = 400

    def __init__(self, widget, log_dir=None):
        self._q = queue.Queue()
        self._w = widget
        self._buffer = []       # In-memory buffer for export

        # P0 Optimization #5: File write batching
        self._file_buffer = []
        self._last_flush = time.time()

        # P1 Enhancement: Daily log rotation
        self._log_dir = log_dir
        self._log_file = None
        self._current_date = ""  # Track date for daily rotation
        if log_dir:
            self._rotate_log_file()
            self._cleanup_old_logs()

    def put(self, msg, tag=None, caller_file="", caller_func="", level=LOG_INFO,
            run_id=""):
        """Add message to queue (thread-safe).

        Args:
            msg: Log message
            tag: Optional tkinter tag for GUI coloring
            caller_file: Source filename (auto-captured by log1)
            caller_func: Source function name (auto-captured by log1)
            level: Log level int (LOG_DEBUG=0, LOG_INFO=1, LOG_WARNING=2, LOG_ERROR=3)
            run_id: 本次运行的统一标识 (仅写入文件行, 不进入 GUI 行)
        """
        _broadcast_log(msg, tag, level, caller_file, caller_func)
        self._q.put((msg, tag, caller_file, caller_func, level, run_id))

    def has_pending(self):
        """GUI 队列里是否还有待渲染的日志行 (v5 §3.5 G3 的事件驱动判据)。

        宿主 `flush_and_apply` 用它做「队列空 → 本轮 0 次 Tcl 调用」的短路;
        返回 True 时按保守处理 (宁可多刷一次, 也不丢日志)。
        """
        try:
            return not self._q.empty()
        except Exception:
            return True

    def flush(self, root_widget):
        """Flush queue to GUI widget and batched file write with LogLevel filtering.

        v3 §4.1: GUI 侧改为「整批拼接 + 一次 insert + 一次 see/update_idletasks」。
        逐条 update() 会在刷屏时做成 N 次强制同步重绘, 是日志卡顿的根因。
        消息顺序/级别过滤/徽标/落盘时机与改造前完全一致。
        """
        # P1 Enhancement: Check daily rotation on each flush cycle
        self._check_daily_rotation()

        # 控件可能已随窗口销毁 (关窗后仍有日志涌入): 只停 GUI 侧, 文件侧照常
        gui_ok = True
        try:
            if self._w is None or not self._w.winfo_exists():
                gui_ok = False
        except Exception:
            gui_ok = False

        parts = []          # GUI 文本片段
        spans = []          # [(tag, start, end)] 相对整批文本的字符偏移
        offset = 0
        drain = 0

        while drain < self.GUI_BATCH_MAX:
            try:
                msg, tag, caller_file, caller_func, level, run_id = self._q.get_nowait()
            except queue.Empty:
                break
            drain += 1

            # Add to in-memory buffer for export
            self._buffer.append((msg, tag))

            # ── GUI 渲染: 只累积, 循环外一次性提交 ──
            # UI Enhancement: GUI 日志 = "[HH:MM:SS] [LEVEL] msg"。
            # §2.5: 四级 (INFO/SUCCESS/WARNING/ERROR) **均**带级别徽标, 且徽标**由 tag 判定**
            # (log1(msg,"success") 的 int 级别实为 LOG_INFO, 但徽标须为 [SUCCESS])。
            if gui_ok:
                ts = time.strftime("%H:%M:%S")
                badge = _log_badge(tag, level)
                line = "[{}] [{}] {}\n".format(ts, badge, msg)
                if LOG_CBAR_INLINE:
                    # L4: 色条字形并入生产文本。级别 tag 的区间仍从行首开始
                    # (覆盖 ▌), 而 cbar_* tag 后配置、优先级更高 → ▌ 显示色条色,
                    # 文本其余部分仍是级别色, 视觉与旧版逐像素一致。
                    # 副作用: 导出/复制路径需 strip("▌") —— 见 ui/log_dock.py。
                    line = "▌" + line
                parts.append(line)
                if tag:
                    # 相邻同 tag 合并成一段, 减少 tag_add 次数
                    if spans and spans[-1][0] == tag and spans[-1][2] == offset:
                        spans[-1][2] = offset + len(line)
                    else:
                        spans.append([tag, offset, offset + len(line)])
                offset += len(line)

            # P1 Enhancement: File write with LogLevel filtering + structured format
            if self._log_file and self._check_log_saving_enabled():
                # Check LogLevel filter
                if level < self._get_min_log_level():
                    continue

                # Structured format: [2026-07-20 11:13:48] INFO: message [file:function]
                timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
                level_name = _LOG_LEVEL_NAMES.get(level, "INFO")
                caller_info = ""
                if caller_file and caller_func:
                    caller_info = " [{}:{}]".format(caller_file, caller_func)
                elif caller_file:
                    caller_info = " [{}]".format(caller_file)
                run_info = " [run:{}]".format(run_id) if run_id else ""
                self._file_buffer.append(
                    "[{}] {}: {}{}{}\n".format(timestamp, level_name, msg,
                                                run_info, caller_info))

                # Check if we should flush to disk
                now = time.time()
                if (len(self._file_buffer) >= self.BATCH_SIZE or
                    now - self._last_flush >= self.FLUSH_INTERVAL):
                    self._flush_to_disk()

        # ── 整批提交 (一次 insert + 一次见底/刷新) ──
        if gui_ok and parts:
            try:
                anchor = self._w.index("end-1c")
                self._w.insert("end", "".join(parts))
                for tag, s, e in spans:
                    self._w.tag_add(tag, "{}+{}c".format(anchor, s),
                                    "{}+{}c".format(anchor, e))
                self._w.see("end")
                self._w.update_idletasks()
            except Exception:
                pass

    def _flush_to_disk(self):
        """Write buffered logs to disk (batched I/O)."""
        if not self._file_buffer or not self._log_file:
            return

        try:
            # Ensure log directory exists
            log_dir = os.path.dirname(self._log_file)
            if log_dir and not os.path.exists(log_dir):
                os.makedirs(log_dir, exist_ok=True)

            # Append all buffered messages at once
            with open(self._log_file, "a", encoding="utf-8") as f:
                f.writelines(self._file_buffer)

            # Clear buffer and update timestamp
            self._file_buffer.clear()
            self._last_flush = time.time()
        except Exception:
            pass  # Silently ignore file write errors

    # ── P1 Enhancement: Daily log rotation & retention ──

    def _get_log_filename(self):
        """Get daily log filename: acrpa_YYYYMMDD.log."""
        return os.path.join(self._log_dir,
            "acrpa_{}.log".format(time.strftime("%Y%m%d")))

    def _rotate_log_file(self):
        """Switch to today's log file (create if not exists)."""
        if not self._log_dir:
            return
        new_file = self._get_log_filename()
        today_str = time.strftime("%Y%m%d")
        if self._log_file != new_file or self._current_date != today_str:
            # Flush any pending writes to old file before switching
            # 注意: 不能调 force_flush() (会再次触发 _check_daily_rotation → _rotate_log_file 无限递归)
            self._flush_to_disk()
            self._log_file = new_file
            self._current_date = today_str

    def _check_daily_rotation(self):
        """Check if date has changed and rotate log file if needed."""
        today_str = time.strftime("%Y%m%d")
        if self._current_date != today_str:
            self._rotate_log_file()
            self._cleanup_old_logs()

    def _cleanup_old_logs(self):
        """Delete log files older than LOG_RETENTION_DAYS."""
        if not self._log_dir:
            return
        try:
            retention_days = getattr(state, 'LOG_RETENTION_DAYS', 7)
            if retention_days <= 0:
                return  # 0 or negative means keep forever

            cutoff = time.time() - (retention_days * 86400)
            # 两类都清理: 主日志 acrpa_*.log + 崩溃日志 crash_*.log (history.jsonl 不动)
            patterns = ("acrpa_*.log", "crash_*.log")
            for pattern in patterns:
                for log_path in glob.glob(os.path.join(self._log_dir, pattern)):
                    try:
                        if os.path.getmtime(log_path) < cutoff:
                            os.remove(log_path)
                    except Exception:
                        pass
        except Exception:
            pass  # Cleanup failure is non-critical

    def _check_log_saving_enabled(self):
        """Check if log file saving is enabled in config."""
        try:
            return getattr(state, 'ENABLE_LOG_SAVING', True)
        except Exception:
            return True  # Default to enabled if state unavailable

    def _get_min_log_level(self):
        """Get the minimum log level to write to file from config."""
        try:
            return getattr(state, 'LOG_LEVEL', LOG_INFO)
        except Exception:
            return LOG_INFO

    def force_flush(self):
        """Force immediate flush of all pending logs to disk."""
        self._check_daily_rotation()
        self._flush_to_disk()

    def export(self, filepath):
        """Export all logged messages to a file."""
        # First flush any pending writes
        self.force_flush()

        # Then export the in-memory buffer
        with open(filepath, "w", encoding="utf-8") as f:
            for msg, tag in self._buffer:
                f.write(msg + "\n")

    def clear_buffer(self):
        """Clear in-memory buffer (but keep file logs)."""
        self._buffer = []
        self._file_buffer = []
        self._last_flush = time.time()
