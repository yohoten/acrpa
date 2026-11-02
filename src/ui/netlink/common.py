# -*- coding: utf-8 -*-
"""设备互联窗口共享常量与模块级助手 (L1-A 拆分)。"""

import os
import socket
import time
import tkinter
from tkinter import ttk

import state
import utils
from utils import log1

# 项目根目录 (含 res/、userdata/)。common.py 位于 src/ui/netlink/，需向上 4 级。
APP_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
DEFAULT_TCP_PORT = 19710
MAX_LOG_LINES = 2000
TRIM_LOG_LINES = 500
PUMP_INTERVAL_MS = 200
_CMD_LABELS = {"CMD_RUN": "远程运行", "CMD_PAUSE": "远程暂停",
               "CMD_RESUME": "远程恢复", "CMD_STOP": "远程停止",
               "CMD_RUN_SCRIPT": "运行脚本"}
_CMD_VERB = {"run": "运行", "pause": "暂停", "resume": "恢复", "stop": "停止"}

def _nl():
    """懒加载 netlink 门面；失败返回 None。"""
    try:
        import netlink
        return netlink
    except Exception:
        return None

def _topics():
    """懒加载总线主题常量；失败返回 None (调用方需判空)。"""
    try:
        from netlink.node import (TOPIC_PEER, TOPIC_PEER_STATE, TOPIC_PEER_LOG,
                                  TOPIC_PEER_SCHED, TOPIC_STATUS, TOPIC_AUTH,
                                  TOPIC_CMD_RESULT)
        return {"peer": TOPIC_PEER, "state": TOPIC_PEER_STATE,
                "log": TOPIC_PEER_LOG, "sched": TOPIC_PEER_SCHED,
                "status": TOPIC_STATUS, "auth": TOPIC_AUTH,
                "cmd_result": TOPIC_CMD_RESULT}
    except Exception:
        return None
        return None

def _perm_labels():
    """权限 code → 中文标签；取不到时用内置兜底 (不抛)。"""
    try:
        from netlink.security import PERM_LABELS
        return dict(PERM_LABELS)
    except Exception:
        return {"observe": "仅观察", "control": "允许操控", "script": "允许接收脚本"}

def _perm_code(label):
    """中文标签 → 权限 code；未知返回 None。"""
    try:
        for code, lab in _perm_labels().items():
            if lab == label:
                return code
    except Exception:
        pass
    return None

def _perm_ok(perm):
    """perm 是否满足「允许操控」门槛（复用 netlink.security.perm_satisfies）。"""
    try:
        from netlink.security import perm_satisfies
        return bool(perm_satisfies(perm, "control"))
    except Exception:
        return str(perm or "") in ("control", "script")

def _cmd_types():
    """懒加载指令类型常量；失败返回 None。"""
    try:
        from netlink.protocol import (T_CMD_RUN, T_CMD_PAUSE, T_CMD_RESUME,
                                      T_CMD_STOP, T_CMD_ACK, T_CMD_ERR)
        return {"run": T_CMD_RUN, "pause": T_CMD_PAUSE, "resume": T_CMD_RESUME,
                "stop": T_CMD_STOP, "ack": T_CMD_ACK, "err": T_CMD_ERR}
    except Exception:
        return None

def _cmd_run_script_type():
    """懒加载 T_CMD_RUN_SCRIPT；失败返回 None。"""
    try:
        from netlink.protocol import T_CMD_RUN_SCRIPT
        return T_CMD_RUN_SCRIPT
    except Exception:
        return None

def _perm_script(perm):
    """perm 是否满足「允许接收脚本」门槛（复用 netlink.security.perm_satisfies）。"""
    try:
        from netlink.security import perm_satisfies
        return bool(perm_satisfies(perm, "script"))
    except Exception:
        return str(perm or "") == "script"

def _transfer():
    """懒加载 netlink.transfer 模块；失败返回 None。"""
    try:
        from netlink import transfer
        return transfer
    except Exception:
        return None

def _transfer_topic():
    """经门面取脚本分发进度主题；失败退回字面量（不硬编码在调用点）。"""
    try:
        nl = _nl()
        if nl is not None:
            return str(nl.get_transfer_topic())
    except Exception:
        pass
    return "netlink.transfer"

def _screenshot_topic():
    """经门面取远程截图事件主题；失败退回字面量（不硬编码在调用点）。"""
    try:
        nl = _nl()
        if nl is not None:
            return str(nl.get_screenshot_topic())
    except Exception:
        pass
    return "netlink.screenshot"

def _screen():
    """懒加载 netlink.screen 模块（仅用于取常量）；失败返回 None。"""
    try:
        from netlink import screen
        return screen
    except Exception:
        return None

def _safe_int(v, default=0):
    """宽松 int 转换（失败返回 default）。"""
    try:
        return int(v)
    except Exception:
        return default

def _script_root():
    """被控端脚本根目录（取不到返回空串）。"""
    try:
        tf = _transfer()
        return tf.script_root() if tf is not None else ""
    except Exception:
        return ""

def _fmt_size(n):
    """字节数 → 人类可读（失败返回 —）。"""
    try:
        n = int(n)
    except Exception:
        return "—"
    if n < 1024:
        return "{} B".format(n)
    if n < 1024 * 1024:
        return "{:.1f} KB".format(n / 1024.0)
    return "{:.1f} MB".format(n / (1024.0 * 1024.0))

def _fmt_mtime(ts):
    """时间戳 → 本地时间字符串（失败返回 —）。"""
    try:
        ts = int(ts)
        if ts > 0:
            return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
    except Exception:
        pass
    return "—"

