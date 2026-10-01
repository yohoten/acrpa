# -*- coding: utf-8 -*-
"""ACRPA NetLink — 远程操控审计日志（纯标准库，Python 3.7 兼容）。

职责：
  * 把每一条远程操控指令落盘为单行 UTF-8 文本，供事后追责与合规审计；
  * 按天轮转（netlink_audit_YYYYMMDD.log）+ 删除超期文件（保留天数可配）；
  * 绝不 import utils（utils 顶层 import tkinter），也不占用日志总线，避免回环；
  * 所有写入整体 try/except，失败静默，绝不影响主流程。

行格式（单行、UTF-8、ensure_ascii=False）：
  [2026-01-01 14:22:01] actor=<设备名|指纹前8位> remote=<ip:port> \
      cmd=<CMD_RUN> args=<json> result=<ok|err|rejected> detail=<文本>
"""
import glob
import json
import os
import threading
import time

try:
    import state as _state
except Exception:          # 极端环境下 state 不可用时降级（路径退化为 cwd/logs）
    _state = None


_MAX_ARGS = 400            # args JSON 序列化后最大字符数，超出截断


class AuditLog(object):
    """线程安全的远程操控审计日志（每日轮转 + 超期清理）。"""

    def __init__(self, log_dir=None, retention_days=0):
        self._lock = threading.Lock()
        self._log_dir = log_dir or self._default_dir()
        try:
            os.makedirs(self._log_dir, exist_ok=True)
        except Exception:
            pass
        try:
            self._retention = int(retention_days)
        except Exception:
            self._retention = 0
        if self._retention <= 0:
            try:
                self._retention = int(getattr(_state, "LOG_RETENTION_DAYS", 7) or 7)
            except Exception:
                self._retention = 7
        self._last_date = ""
        self._cleanup()

    # ── 路径 ──────────────────────────────────────────────────────────
    @staticmethod
    def _default_dir():
        """默认日志目录：<程序目录(含 config.json)>/logs。"""
        try:
            cfg = str(getattr(_state, "CONFIG_PATH", "") or "")
            base = os.path.dirname(cfg) if cfg else os.getcwd()
        except Exception:
            base = os.getcwd()
        return os.path.join(base, "logs")

    def _file_for(self, day=None):
        d = day or time.strftime("%Y%m%d")
        return os.path.join(self._log_dir, "netlink_audit_{}.log".format(d))

    @property
    def path(self):
        """当前审计文件绝对路径。"""
        return os.path.abspath(self._file_for())

    # ── 写入 ──────────────────────────────────────────────────────────
    def write(self, actor, cmd, args=None, result="ok", detail="", remote=""):
        """追加一行审计记录；线程安全，绝不抛。"""
        try:
            try:
                if args is None:
                    args = {}
                ajson = json.dumps(args, ensure_ascii=False, sort_keys=True)
            except Exception:
                ajson = "{}"
            if len(ajson) > _MAX_ARGS:
                ajson = ajson[:_MAX_ARGS] + "..."
            line = ("[{0}] actor={1} remote={2} cmd={3} args={4} "
                    "result={5} detail={6}\n").format(
                        time.strftime("%Y-%m-%d %H:%M:%S"),
                        str(actor or ""), str(remote or ""), str(cmd or ""),
                        ajson, str(result or ""), str(detail or ""))
            day = time.strftime("%Y%m%d")
            with self._lock:
                if day != self._last_date:
                    try:
                        os.makedirs(self._log_dir, exist_ok=True)
                    except Exception:
                        pass
                    self._last_date = day
                    self._cleanup()
                with open(self._file_for(day), "a", encoding="utf-8") as f:
                    f.write(line)
        except Exception:
            pass

    # ── 读取（供 UI / 测试）──────────────────────────────────────────
    def tail(self, n=200):
        """返回最近 n 行（按写入顺序）；文件不存在/读失败返回 []。"""
        try:
            n = int(n)
        except Exception:
            n = 200
        if n <= 0:
            return []
        try:
            path = self._file_for()
            if not os.path.isfile(path):
                return []
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.read().splitlines()
            return lines[-n:]
        except Exception:
            return []

    # ── 超期清理（自行 glob，不依赖 utils）────────────────────────────
    def _cleanup(self):
        if self._retention <= 0:
            return
        try:
            cutoff = time.time() - (self._retention * 86400)
            pattern = os.path.join(self._log_dir, "netlink_audit_*.log")
            for p in glob.glob(pattern):
                try:
                    if os.path.getmtime(p) < cutoff:
                        os.remove(p)
                except Exception:
                    pass
        except Exception:
            pass
