# -*- coding: utf-8 -*-
"""ACRPA NetLink — 只读状态采集 Agent（Phase 1，纯标准库，Python 3.7 兼容）。

本阶段只做只读采集与订阅推送，不实现任何控制命令（CMD_*）：
  * STATE_SYNC  : 运行状态快照（running/paused/row/loop/...）；
  * LOG_TAIL    : 日志镜像（200 条环形缓冲，seq 递增，支持 full 追发历史）；
  * SCHED_STATUS: 计划任务摘要（enabled/next_run/task_count）。

数据源仅限 state 模块的大写全局量与 exec_state（不 import engine/workflow/scheduler）。
本模块不 import utils 于顶层；日志 sink 在 start() 内懒加载（utils 顶层 import tkinter）。
"""
import os
import threading
import time

import state

from .protocol import (
    make_msg,
    T_SUBSCRIBE, T_UNSUBSCRIBE, T_PING,
    T_STATE_SYNC, T_LOG_TAIL, T_SCHED_STATUS,
    T_CMD_ACK, T_CMD_ERR, T_TOPICS,
)
from .connection import _nl_log
from .security import required_perm, perm_satisfies


SAMPLE_INTERVAL = 0.5          # 采样周期（秒）
FULL_PUSH_INTERVAL = 5.0       # STATE_SYNC 兜底全量推送周期（秒）
LOG_RING = 200                 # 日志环形缓冲长度
MSG_MAX = 2000                 # 单条日志最大字符数（超出截断）


class Agent(object):
    """只读采集 Agent：注册日志 sink + 500ms 采样，向订阅连接推送快照。"""

    def __init__(self, node):
        self._node = node
        self._subs = {}            # {conn: set(topics)}
        self._sent_full = {}       # {conn: bool} 是否已推过 full 历史
        self._subs_lock = threading.RLock()
        self._stop_evt = threading.Event()
        self._th = None
        self._started = False
        # 日志环形缓冲
        self._log_buf = []         # [line_dict, ...]（含内部字段 _seq）
        self._log_lock = threading.Lock()
        self._log_seq = 0
        self._last_pushed_log_seq = 0
        self._sink = None
        # 上次快照
        self._last_state_key = None
        self._last_state_full_ts = 0.0
        self._last_sched = None

    # ── 生命周期 ──────────────────────────────────────────────────────
    def start(self):
        """注册日志 sink 并启动 500ms 采样线程。"""
        try:
            if self._started:
                return True
            self._register_sink()
            self._stop_evt.clear()
            self._th = threading.Thread(target=self._sample_loop, name="nl-agent")
            self._th.daemon = True
            self._th.start()
            self._started = True
            return True
        except Exception as e:
            _nl_log("agent start failed: {}".format(e), "ERROR")
            return False

    def stop(self):
        """注销 sink + 停止采样线程；幂等。"""
        try:
            self._stop_evt.set()
            th = self._th
            if th is not None and th is not threading.current_thread():
                try:
                    th.join(1.5)
                except Exception:
                    pass
            self._th = None
            self._unregister_sink()
            with self._subs_lock:
                self._subs = {}
                self._sent_full = {}
            self._started = False
        except Exception as e:
            _nl_log("agent stop error: {}".format(e), "WARN")

    # ── 日志 sink（懒加载 utils）────────────────────────────────────────
    def _register_sink(self):
        try:
            import utils
        except Exception as e:
            _nl_log("agent: utils import failed, log mirror disabled: {}".format(e), "WARN")
            return

        def sink(msg, tag, level, caller_file, caller_func):
            try:
                self._on_log(msg, tag, level, caller_file, caller_func)
            except Exception as e:
                _nl_log("agent log sink error: {}".format(e), "WARN")

        self._sink = sink
        try:
            utils.register_log_sink(sink)
        except Exception as e:
            _nl_log("register_log_sink failed: {}".format(e), "WARN")

    def _unregister_sink(self):
        sink = self._sink
        self._sink = None
        if sink is None:
            return
        try:
            import utils
            utils.unregister_log_sink(sink)
        except Exception:
            pass

    def _on_log(self, msg, tag, level, caller_file, caller_func):
        """sink 回调：截断超长文本 → 追加环形缓冲 → seq 自增。"""
        try:
            text = "" if msg is None else str(msg)
            if len(text) > MSG_MAX:
                text = text[:MSG_MAX] + "..."
            try:
                lvl = int(level)
            except Exception:
                lvl = 1
            line = {
                "ts": time.strftime("%H:%M:%S"),
                "tag": (str(tag) if tag is not None else ""),
                "level": lvl,
                "msg": text,
                "file": (str(caller_file) if caller_file else ""),
                "func": (str(caller_func) if caller_func else ""),
                "_seq": 0,
            }
            with self._log_lock:
                self._log_seq += 1
                line["_seq"] = self._log_seq
                self._log_buf.append(line)
                if len(self._log_buf) > LOG_RING:
                    del self._log_buf[0:len(self._log_buf) - LOG_RING]
        except Exception as e:
            _nl_log("agent log buffer error: {}".format(e), "WARN")

    # ── 快照 ──────────────────────────────────────────────────────────
    def state_snapshot(self):
        """运行状态快照（整体 try/except，缺字段用默认）。"""
        out = {"running": False, "paused": False, "recording": False,
               "script": "", "row": 0, "total_rows": 0, "loop": 0,
               "total_loops": 0, "elapsed": 0, "node": "", "ts": int(time.time())}
        try:
            es = getattr(state, "exec_state", None) or {}
            running = bool(getattr(state, "running", False))
            recording = bool(getattr(state, "recording", False))
            try:
                pe = getattr(state, "pause_event", None)
                paused = bool(running and (pe is not None) and (not pe.is_set()))
            except Exception:
                paused = False
            fn = getattr(state, "filename", None)
            script = os.path.basename(fn) if fn else ""
            row = int(es.get("row", 0) or 0)
            total_rows = int(es.get("total_rows", 0) or 0)
            loop = int(es.get("loop", 0) or 0)
            total_loops = int(es.get("total_loops", 0) or 0)
            if running:
                st = es.get("start_time", 0) or 0
                elapsed = int(time.time() - st) if st else int(es.get("elapsed", 0) or 0)
            else:
                elapsed = int(es.get("elapsed", 0) or 0)
            node_name = ""
            try:
                node_name = self._node.info.get("name", "") if self._node else ""
            except Exception:
                node_name = ""
            out.update({"running": running, "paused": paused,
                        "recording": recording, "script": script,
                        "row": row, "total_rows": total_rows,
                        "loop": loop, "total_loops": total_loops,
                        "elapsed": elapsed, "node": node_name,
                        "ts": int(time.time())})
        except Exception as e:
            _nl_log("state_snapshot error: {}".format(e), "WARN")
        return out

    def log_snapshot(self, since_seq=0):
        """返回 (lines_list, new_seq)，仅包含 seq > since_seq 的行。"""
        try:
            with self._log_lock:
                seq = self._log_seq
                raw = [ln for ln in self._log_buf if ln.get("_seq", 0) > since_seq]
            lines = [{"ts": ln["ts"], "tag": ln["tag"], "level": ln["level"],
                      "msg": ln["msg"], "file": ln["file"], "func": ln["func"]}
                     for ln in raw]
            return lines, seq
        except Exception as e:
            _nl_log("log_snapshot error: {}".format(e), "WARN")
            return [], self._log_seq

    def sched_snapshot(self):
        """计划任务摘要：{enabled, next_run, task_count}。"""
        out = {"enabled": False, "next_run": "", "task_count": 0}
        try:
            enabled = bool(getattr(state, "SCHED_ENABLED", False))
            next_run = getattr(state, "SCHED_NEXT_RUN", "") or ""
            tasks = getattr(state, "SCHED_TASKS", None) or []
            try:
                count = len(tasks)
            except Exception:
                count = 0
            out = {"enabled": enabled, "next_run": str(next_run), "task_count": count}
        except Exception as e:
            _nl_log("sched_snapshot error: {}".format(e), "WARN")
        return out

    # ── 远端消息（权限门槛 + 订阅/心跳/命令）─────────────────────────────
    def on_remote_message(self, conn, msg):
        """权限门槛：先按 required_perm 校验，再分派 SUBSCRIBE / CMD_* 等。"""
        try:
            if not isinstance(msg, dict):
                return
            t = msg.get("t")
            data = msg.get("data") or {}
            # ── 权限门槛 ──
            try:
                perm = self._node.perm_of(conn)
            except Exception:
                perm = None
            if perm is None:
                self._node.send(conn, make_msg(T_CMD_ERR,
                                               {"cmd": t, "reason": "not authenticated"}))
                return
            need = required_perm(t)
            if need is not None and not perm_satisfies(perm, need):
                self._node.send(conn, make_msg(T_CMD_ERR,
                                               {"cmd": t, "reason": "permission denied: need " + need}))
                return
            # ── 分派 ──
            if t == T_SUBSCRIBE:
                self._handle_subscribe(conn, data)
            elif t == T_UNSUBSCRIBE:
                self._handle_unsubscribe(conn, data)
            elif t == T_PING:
                self._node.send(conn, make_msg("PONG", {"ts": int(time.time())}))
            elif t == "HELLO":
                pass  # HELLO 由 node 处理
            elif isinstance(t, str) and t.startswith("CMD_") and t not in ("CMD_ACK", "CMD_ERR"):
                ctl = getattr(self._node, "control", None)
                if ctl is not None:
                    ctl.handle(conn, msg)
                else:
                    self._reject_cmd(conn, t)
        except Exception as e:
            _nl_log("agent on_remote_message error: {}".format(e), "ERROR")

    def _reject_cmd(self, conn, t):
        """兜底：操控内核不可用时统一回 T_CMD_ERR（正常路径由 control.handle 落点）。"""
        try:
            self._node.send(conn, make_msg(T_CMD_ERR,
                                           {"cmd": t, "reason": "control unavailable"}))
        except Exception as e:
            _nl_log("reject cmd error: {}".format(e), "WARN")

    def _handle_subscribe(self, conn, data):
        topics = data.get("topics")
        if not isinstance(topics, (list, tuple)):
            topics = []
        valid = []
        for tp in topics:
            name = str(tp)
            if name in T_TOPICS and name not in valid:
                valid.append(name)
        with self._subs_lock:
            cur = self._subs.get(conn)
            if cur is None:
                cur = set()
                self._subs[conn] = cur
            cur.update(valid)
            self._sent_full.setdefault(conn, False)
        # 立即推送一次初始快照，避免等待下一个采样周期
        if "state" in valid:
            self._send_state_to(conn)
        if "sched" in valid:
            self._send_sched_to(conn)
        if "log" in valid:
            self._send_log_full(conn)
        self._node.send(conn, make_msg(T_CMD_ACK,
                                       {"cmd": "SUBSCRIBE", "ok": True, "topics": valid}))

    def _handle_unsubscribe(self, conn, data):
        topics = data.get("topics")
        if not isinstance(topics, (list, tuple)):
            topics = list(T_TOPICS)
        with self._subs_lock:
            cur = self._subs.get(conn)
            if cur is not None:
                for tp in topics:
                    cur.discard(str(tp))
        self._node.send(conn, make_msg(T_CMD_ACK, {"cmd": "UNSUBSCRIBE", "ok": True}))

    def unsubscribe_conn(self, conn):
        """连接断开时清理该连接的订阅与状态。"""
        try:
            with self._subs_lock:
                self._subs.pop(conn, None)
                self._sent_full.pop(conn, None)
        except Exception:
            pass

    # ── 采样线程 ──────────────────────────────────────────────────────
    def _sample_loop(self):
        try:
            while not self._stop_evt.is_set():
                try:
                    self._tick()
                except Exception as e:
                    _nl_log("agent tick error: {}".format(e), "WARN")
                self._stop_evt.wait(SAMPLE_INTERVAL)
        except Exception as e:
            _nl_log("agent sample loop fatal: {}".format(e), "ERROR")

    def _tick(self):
        now = time.time()
        snap = self.state_snapshot()
        key = (snap.get("running"), snap.get("paused"), snap.get("recording"),
               snap.get("script"), snap.get("row"), snap.get("loop"))
        push_state = False
        if key != self._last_state_key:
            self._last_state_key = key
            push_state = True
        if (now - self._last_state_full_ts) >= FULL_PUSH_INTERVAL:
            self._last_state_full_ts = now
            push_state = True
        if push_state:
            for conn in self._subs_for("state"):
                self._node.send(conn, make_msg(T_STATE_SYNC, dict(snap)))

        sched = self.sched_snapshot()
        if sched != self._last_sched:
            self._last_sched = sched
            for conn in self._subs_for("sched"):
                self._node.send(conn, make_msg(T_SCHED_STATUS, dict(sched)))

        self._push_log()

    def _subs_for(self, topic):
        out = []
        try:
            with self._subs_lock:
                for conn, topics in self._subs.items():
                    if topic in topics:
                        out.append(conn)
        except Exception:
            pass
        return out

    def _push_log(self):
        with self._log_lock:
            seq = self._log_seq
        if seq <= self._last_pushed_log_seq:
            return
        targets = self._subs_for("log")
        if not targets:
            self._last_pushed_log_seq = seq
            return
        new_lines, cur_seq = self.log_snapshot(self._last_pushed_log_seq)
        self._last_pushed_log_seq = cur_seq
        for conn in targets:
            with self._subs_lock:
                sent_full = self._sent_full.get(conn, False)
            if not sent_full:
                self._send_log_full(conn)
                continue
            if not new_lines:
                continue
            data = {"lines": new_lines, "seq": cur_seq, "full": False}
            self._node.send(conn, make_msg(T_LOG_TAIL, data))

    def _send_state_to(self, conn):
        try:
            self._node.send(conn, make_msg(T_STATE_SYNC, self.state_snapshot()))
        except Exception as e:
            _nl_log("send state to conn error: {}".format(e), "WARN")

    def _send_sched_to(self, conn):
        try:
            self._node.send(conn, make_msg(T_SCHED_STATUS, self.sched_snapshot()))
        except Exception as e:
            _nl_log("send sched to conn error: {}".format(e), "WARN")

    def _send_log_full(self, conn):
        try:
            lines, seq = self.log_snapshot(0)
            ok = self._node.send(conn, make_msg(T_LOG_TAIL,
                                                {"lines": lines, "seq": seq, "full": True}))
            with self._subs_lock:
                self._sent_full[conn] = True
            if seq > self._last_pushed_log_seq:
                self._last_pushed_log_seq = seq
        except Exception as e:
            _nl_log("send log full error: {}".format(e), "WARN")
