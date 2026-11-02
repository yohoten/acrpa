# -*- coding: utf-8 -*-
"""NetLinkWindow 方法组：sched (L1-A 拆分)。"""

import os
import socket
import time
import tkinter
from tkinter import ttk

import state
import utils
from utils import log1

from ui.netlink.common import (
    APP_ROOT, DEFAULT_TCP_PORT, MAX_LOG_LINES, TRIM_LOG_LINES, PUMP_INTERVAL_MS,
    _CMD_LABELS, _CMD_VERB,
    _nl, _topics, _perm_labels, _perm_code, _perm_ok, _cmd_types,
    _cmd_run_script_type, _perm_script, _transfer, _transfer_topic,
    _screenshot_topic, _screen, _safe_int, _script_root, _fmt_size, _fmt_mtime,
)

class SchedMixin(object):
    # _refresh_sched_label
    def _refresh_sched_label(self):
        """刷新选中设备详情区「定时任务」文本（无数据 → 定时任务：—）。"""
        try:
            self.lbl_sched.config(text=self._sched_text())
        except Exception:
            pass

    # _sched_text
    def _sched_text(self):
        """TOPIC_PEER_SCHED → 详情区文本。

        无数据 → "定时任务：—"；否则
        "定时任务：<启用|未启用> · 下次运行 <next_run 或 未安排/—> （N 个任务）"。
        """
        nid = self._sel
        if not nid:
            return "定时任务：—"
        s = self._scheds.get(str(nid))
        if not isinstance(s, dict):
            return "定时任务：—"
        enabled = bool(s.get("enabled"))
        next_run = str(s.get("next_run") or "")
        try:
            cnt = int(s.get("task_count") or 0)
        except Exception:
            cnt = 0
        if not next_run:
            nr = "未安排" if enabled else "—"
        else:
            nr = next_run
        return "定时任务：{} · 下次运行 {} （{} 个任务）".format(
            "启用" if enabled else "未启用", nr, cnt)

