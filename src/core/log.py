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
    global _tlog; _tlog = instance


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
    if _tlog:
        # ── Auto-detect level from tag if not explicitly provided ──
        if level is None:
            tag_lower = str(tag).lower() if tag else ""
            if tag_lower == "error":
                level = LOG_ERROR
            elif tag_lower == "warning":
                level = LOG_WARNING
            elif tag_lower == "info":
                level = LOG_INFO
            elif tag_lower == "debug":
                level = LOG_DEBUG
            else:
                level = LOG_INFO
        elif isinstance(level, str):
            level_map = {"DEBUG": LOG_DEBUG, "INFO": LOG_INFO,
                         "WARNING": LOG_WARNING, "ERROR": LOG_ERROR}
            level = level_map.get(level.upper(), LOG_INFO)

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
        _tlog.put(msg, tag, caller_file, caller_func, level)


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

    def put(self, msg, tag=None, caller_file="", caller_func="", level=LOG_INFO):
        """Add message to queue (thread-safe).

        Args:
            msg: Log message
            tag: Optional tkinter tag for GUI coloring
            caller_file: Source filename (auto-captured by log1)
            caller_func: Source function name (auto-captured by log1)
            level: Log level int (LOG_DEBUG=0, LOG_INFO=1, LOG_WARNING=2, LOG_ERROR=3)
        """
        _broadcast_log(msg, tag, level, caller_file, caller_func)
        self._q.put((msg, tag, caller_file, caller_func, level))

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
                msg, tag, caller_file, caller_func, level = self._q.get_nowait()
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
                self._file_buffer.append(
                    "[{}] {}: {}{}\n".format(timestamp, level_name, msg, caller_info))

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
            pattern = os.path.join(self._log_dir, "acrpa_*.log")
            for log_path in glob.glob(pattern):
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
