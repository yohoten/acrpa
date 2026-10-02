# -*- coding: utf-8 -*-
"""ACRPA NetLink — 远程操控内核（Phase 2-2，纯标准库，Python 3.7 兼容）。

职责：
  * 处理 CMD_*：同步门控校验 → 立即回执 → 单 worker 队列串行执行；
  * 工作流 / 脚本双引擎分叉（state.pause_event 与 workflow._pause_event 互不相通）；
  * 首次远程操控被控端弹窗确认（自绘 Toplevel，超时可控）；
  * 远程 CMD_RUN_SCRIPT 路径安全校验（拒绝穿越）；
  * 每一条指令写审计日志（netlink.audit.AuditLog）。

设计约束：
  * 本模块不 import utils、不在顶层 import tkinter（需要时函数内懒 import + try/except），
    保证无 GUI 环境也能 `import netlink`；
  * 执行类操作（run）回到 Tk 主线程（root.after(0, fn)），无 GUI 时退化直接执行；
  * 纯状态标志操作（quit2 / pause_event）线程安全，worker 线程可直接调用；
  * 所有线程体与回调整体 try/except，异常只 _nl_log，绝不向上抛。
"""
import os
import queue
import threading
import time

try:
    import state
except Exception:          # 极端环境下 state 不可用 → 门控降级（放行），不崩
    state = None

from .connection import _nl_log
from .protocol import (
    make_msg,
    T_CMD_ACK, T_CMD_ERR,
    T_CMD_RUN, T_CMD_PAUSE, T_CMD_RESUME, T_CMD_STOP, T_CMD_RUN_SCRIPT,
)


# 本内核接受的指令集合（其余 CMD_* 一律回 unsupported command）
_CMD_SET = (T_CMD_RUN, T_CMD_PAUSE, T_CMD_RESUME, T_CMD_STOP, T_CMD_RUN_SCRIPT)
# 需首次操控确认的指令（PAUSE/RESUME 也纳入，保持一致）
_CONFIRM_CMDS = _CMD_SET

_CONFIRM_TIMEOUT = 30      # 确认弹窗超时（秒）

# ── 网页面板「有限控制」本地提交白名单（Phase4-2 批次1）─────────────────
# 只开放 run / stop；pause / resume 属「未开放」（面板层返回 action not allowed）。
_LOCAL_ACTIONS = {"run": T_CMD_RUN, "stop": T_CMD_STOP}

_LOCAL_DEFAULT_TIMEOUT = 8.0    # submit_local 等待终态上限（秒）


# ── 工作流引擎懒加载（本 Phase 的核心分叉点）─────────────────────────────
def _workflow_engine():
    """懒加载 workflow 模块；无 GUI/无该模块时返回 None，绝不抛。"""
    try:
        import workflow
        eng = getattr(workflow, "workflow_engine", None)
        return eng
    except Exception:
        return None


def _workflow_active(eng):
    """工作流是否正处于执行态：未停止 且 current_step >= 0。异常一律 False。"""
    try:
        return (eng is not None
                and not getattr(eng, "_stopped", True)
                and getattr(eng, "current_step", -1) >= 0)
    except Exception:
        return False


class ControlExecutor(object):
    """把 CMD_* 指令落到被控端本机执行：门控校验 + 串行队列 + 确认 + 审计。"""

    def __init__(self, node):
        self._node = node
        self._hooks = {"run": None, "stop": None}
        self._q = queue.Queue()
        self._stop_evt = threading.Event()
        self._th = None
        self._started = False
        # 主线程动作泵：worker 只投递，主线程（root.after 注册的轮询）执行
        self._ui_q = queue.Queue()
        self._pump_on = False
        try:
            from .audit import AuditLog
            self._audit = AuditLog()
        except Exception:
            self._audit = None

    # ── 生命周期 ──────────────────────────────────────────────────────
    def start(self):
        """启动内部单 worker daemon 线程（幂等）。"""
        try:
            if self._started:
                return True
            self._stop_evt.clear()
            self._th = threading.Thread(target=self._worker, name="nl-control")
            self._th.daemon = True
            self._th.start()
            self._started = True
            self._ensure_pump()
            return True
        except Exception as e:
            _nl_log("control start failed: {}".format(e), "ERROR")
            return False

    def stop(self):
        """停止 worker（幂等）：置停止位 + 投哨兵 + join。"""
        try:
            self._stop_evt.set()
            try:
                self._q.put(None)
            except Exception:
                pass
            th = self._th
            if th is not None and th is not threading.current_thread():
                try:
                    th.join(1.5)
                except Exception:
                    pass
            self._th = None
            self._started = False
            self._pump_on = False
        except Exception as e:
            _nl_log("control stop error: {}".format(e), "WARN")

    # ── 主线程动作泵（必须在主线程注册；worker 不得直接调用 Tk）──────────
    def _ensure_pump(self):
        """在主线程注册 root.after 轮询泵（start() 于主线程调用）。"""
        try:
            if self._pump_on:
                return True
            root = self._root()
            if root is None:
                return False
            root.after(40, self._pump_ui)
            self._pump_on = True
            return True
        except Exception as e:
            _nl_log("control ensure pump failed: {}".format(e), "WARN")
            return False

    def _pump_ui(self):
        """主线程轮询：执行 worker 投递的动作，再自我续订。"""
        try:
            while True:
                try:
                    fn = self._ui_q.get_nowait()
                except queue.Empty:
                    break
                try:
                    fn()
                except Exception as e:
                    _nl_log("control ui action error: {}".format(e), "WARN")
        except Exception as e:
            _nl_log("control pump error: {}".format(e), "WARN")
        if not self._stop_evt.is_set():
            try:
                root = self._root()
                if root is not None:
                    root.after(40, self._pump_ui)
            except Exception:
                self._pump_on = False

    def _post(self, fn):
        """把动作投递到主线程泵；泵未就绪返回 False（调用方自行降级）。"""
        if not self._pump_on:
            return False
        try:
            self._ui_q.put(fn)
            return True
        except Exception:
            return False

    # ── 钩子 / 审计 ───────────────────────────────────────────────────
    def set_hooks(self, run=None, stop=None):
        """注入运行/停止钩子（run(loops=None) / stop()）。None 表示不改动该项。"""
        try:
            if run is not None:
                self._hooks["run"] = run
            if stop is not None:
                self._hooks["stop"] = stop
        except Exception:
            pass

    @property
    def audit(self):
        return self._audit

    # ── 环境访问（全部防御式，避免向上抛异常）─────────────────────────
    def _root(self):
        try:
            r = getattr(self._node, "root", None)
            if r is None:
                r = getattr(self._node, "_root", None)
            return r
        except Exception:
            return None

    def _conn_rec(self, conn):
        try:
            with self._node._lock:
                rec = self._node._conns.get(conn)
            return dict(rec) if rec else {}
        except Exception:
            return {}

    def _remote_str(self, conn):
        try:
            pa = getattr(conn, "peer_addr", None)
            if pa:
                return "{}:{}".format(pa[0], pa[1])
        except Exception:
            pass
        return ""

    def _fingerprint(self, conn):
        return str(self._conn_rec(conn).get("fingerprint") or "")

    def _actor(self, conn):
        """actor=<设备名|指纹前8位>（缺失则退化为可用部分）。"""
        try:
            rec = self._conn_rec(conn)
            name = str(rec.get("name") or "")
            fp = str(rec.get("fingerprint") or "")
            if not fp:
                nid = str(rec.get("node_id") or "")
                if nid:
                    try:
                        from .security import make_fingerprint
                        fp = make_fingerprint(nid, name)
                    except Exception:
                        fp = ""
            short = fp[:8]
            if name and short:
                return "{}|{}".format(name, short)
            return name or short or "unknown"
        except Exception:
            return "unknown"

    # ── 执行态访问 ────────────────────────────────────────────────────
    def _running(self):
        try:
            return bool(getattr(state, "running", False))
        except Exception:
            return False

    def _recording(self):
        try:
            return bool(getattr(state, "recording", False))
        except Exception:
            return False

    def _has_script(self):
        try:
            return bool(getattr(state, "has_script", False))
        except Exception:
            return False

    def _script_dir(self):
        """允许远程运行的脚本目录：NETLINK_SCRIPT_DIR 为空时取 <程序目录>/scripts。"""
        d = ""
        try:
            d = str(getattr(state, "NETLINK_SCRIPT_DIR", "") or "").strip()
        except Exception:
            d = ""
        if not d:
            try:
                cfg = str(getattr(state, "CONFIG_PATH", "") or "")
            except Exception:
                cfg = ""
            base = os.path.dirname(cfg) if cfg else os.getcwd()
            d = os.path.join(base, "scripts")
        return d

    def _set_script(self, abspath, dirpath):
        try:
            state.filename = abspath
            state.has_script = True
            state.script_dir = dirpath
        except Exception:
            pass

    # ── 审计 ──────────────────────────────────────────────────────────
    def _audit_write(self, actor, cmd, args, result, detail, remote):
        try:
            if self._audit is not None:
                self._audit.write(actor, cmd, args, result, detail, remote)
        except Exception:
            pass

    # ── 本地提交回复通道 ──────────────────────────────────────────────
    def _reply_fire(self, reply, status, detail, mode=""):
        """把终态回复给本地等待方（reply 为 None 时为空操作，绝不抛）。"""
        try:
            if reply is None:
                return
            reply(str(status or ""), str(detail or ""), str(mode or ""))
        except Exception as e:
            _nl_log("control local reply failed: {}".format(e), "WARN")

    def _reply_once(self, reply):
        """包装为「只生效一次」的回复回调，避免重复终态（超时后丢弃）。"""
        if reply is None:
            return None
        box = {"done": False}

        def _cb(status, detail, mode=""):
            if box["done"]:
                return
            box["done"] = True
            self._reply_fire(reply, status, detail, mode)

        return _cb

    # ── 回执发送 ──────────────────────────────────────────────────────
    def _err(self, conn, cmd, reason, actor, broadcast=True):
        """即时/终态错误回执（默认广播给所有已认证连接，多控制台状态一致）。"""
        try:
            msg = make_msg(T_CMD_ERR, {"cmd": cmd, "ok": False,
                                       "reason": str(reason or ""),
                                       "actor": str(actor or "")})
            if broadcast:
                self._node.broadcast(msg)
            else:
                self._node.send(conn, msg)
        except Exception as e:
            _nl_log("control send err failed: {}".format(e), "WARN")

    def _ack_accept(self, conn, cmd, actor, detail=""):
        """同步受理回执（仅回发起连接）。"""
        try:
            self._node.send(conn, make_msg(T_CMD_ACK, {
                "cmd": cmd, "ok": True, "status": "accepted",
                "detail": str(detail or ""), "actor": str(actor or ""),
                "mode": ""}))
        except Exception as e:
            _nl_log("control send ack failed: {}".format(e), "WARN")

    def _ack_terminal(self, cmd, status, detail, actor, mode=""):
        """终态 ACK（广播）：status ∈ {"done","failed"}，detail 携带 mode=...。"""
        try:
            d = "mode={} {}".format(mode or "", detail or "").strip()
            self._node.broadcast(make_msg(T_CMD_ACK, {
                "cmd": cmd, "ok": True, "status": status, "detail": d,
                "actor": str(actor or ""), "mode": mode or ""}))
        except Exception as e:
            _nl_log("control terminal ack failed: {}".format(e), "WARN")

    # ── 主入口：同步校验 + 立即回执 + 入队 ────────────────────────────
    def handle(self, conn, msg):
        """处理一条 CMD_*。同步完成门控校验与受理回执，执行落到 worker。"""
        try:
            if not isinstance(msg, dict):
                return
            t = msg.get("t")
            data = msg.get("data") or {}
            actor = self._actor(conn)
            remote = self._remote_str(conn)

            if t not in _CMD_SET:
                self._err(conn, t, "unsupported command", actor)
                self._audit_write(actor, t, data, "rejected", "unsupported command", remote)
                return

            loops = None
            # ── CMD_RUN_SCRIPT：同步路径校验 + 预置 state + loops ──
            if t == T_CMD_RUN_SCRIPT:
                name = str(data.get("name") or "")
                raw_loop = data.get("loop")
                try:
                    loops = int(raw_loop) if raw_loop not in (None, "", 0) else None
                except Exception:
                    loops = None
                # Phase3-1a：名称校验与路径解析复用 transfer（支持 received/<basename>，
                # 使分发落盘的脚本可被远程运行）。懒 import 避免循环依赖。
                try:
                    from . import transfer as _transfer
                except Exception:
                    _transfer = None
                valid_name = bool(_transfer is not None
                                  and _transfer.is_valid_script_name(name))
                if not valid_name:
                    self._err(conn, t, "invalid script name", actor)
                    self._audit_write(actor, t, {"name": name}, "rejected",
                                      "invalid script name", remote)
                    return
                abspath = _transfer.resolve_script(name)
                if (not abspath) or (not os.path.isfile(abspath)):
                    self._err(conn, t, "script not found", actor)
                    self._audit_write(actor, t, {"name": name}, "rejected",
                                      "script not found", remote)
                    return
                self._set_script(abspath, self._script_dir())

            # ── 门控校验（稳定英文 reason，便于测试断言）──
            ok, reason = self._precheck(t, data)
            if not ok:
                self._err(conn, t, reason, actor)
                self._audit_write(actor, t, data, "rejected", reason, remote)
                return

            # ── 受理：入队 + 立即回 accepted ──
            self._q.put((conn, t, dict(data), actor, loops, remote, None, False))
            self._ack_accept(conn, t, actor, "queued")
        except Exception as e:
            _nl_log("control handle error: {}".format(e), "ERROR")

    # ── 前置校验（handle 与 submit_local 共用，稳定英文 reason）────────
    def _precheck(self, t, data):
        """同步门控校验 → (ok:bool, reason:str)。

        reason 与既有远端路径**逐字一致**（already running / recording in progress /
        no script selected / not running），供远端与面板两条路径复用同一语义。
        CMD_STOP 幂等：无前置拒绝。
        """
        try:
            if t in (T_CMD_RUN, T_CMD_RUN_SCRIPT):
                if self._running():
                    return False, "already running"
                if self._recording():
                    return False, "recording in progress"
                if t == T_CMD_RUN and not self._has_script():
                    return False, "no script selected"
            elif t in (T_CMD_PAUSE, T_CMD_RESUME):
                if not self._running():
                    return False, "not running"
                if self._recording():
                    return False, "recording in progress"
            return True, ""
        except Exception as e:
            _nl_log("control precheck error: {}".format(e), "WARN")
            return False, "precheck error"

    # ── 本地面板提交入口（Phase4-2 批次1）────────────────────────────
    def submit_local(self, action, actor, remote, timeout=_LOCAL_DEFAULT_TIMEOUT):
        """把面板线程的本地写请求提交到既有单 worker，同步等待终态。

        action ∈ {"run","stop"}（其余 → action not allowed）；复用 `_do_run`/
        `_do_stop` 与其内部审计，**不复刻停止状态、不绕过审计、不双写审计**。

        返回 dict：{"ok":bool,"action":str,"status":"done|failed|rejected",
                    "detail":str,"mode":str}；不抛异常。
        执行仍在 Tk 主线程（worker → `_post` → `root.after` 泵），本方法只做
        「入队 + threading.Event 等待」，timeout 到期返回 rejected/execution timeout
        （HTTP 线程绝不被长期占用，也绝不触碰 Tk）。
        """
        res = {"ok": False, "action": "", "status": "rejected",
               "detail": "", "mode": ""}
        t = None
        data = {}
        try:
            act = str(action or "").strip().lower()
            res["action"] = act
            t = _LOCAL_ACTIONS.get(act)
            if t is None:
                res["detail"] = "action not allowed"
                self._audit_write(actor, act or "unknown", {}, "rejected",
                                  "action not allowed", remote)
                return res

            ok, reason = self._precheck(t, data)
            if not ok:
                res["detail"] = reason
                self._audit_write(actor, t, data, "rejected", reason, remote)
                return res

            evt = threading.Event()
            holder = {"res": None}
            fired = {"done": False}

            def reply(status, detail, mode=""):
                try:
                    if fired["done"]:
                        return
                    fired["done"] = True
                    holder["res"] = {"status": str(status or ""),
                                     "detail": str(detail or ""),
                                     "mode": str(mode or "")}
                finally:
                    evt.set()

            self._q.put((None, t, data, actor, None, remote, reply, True))
            try:
                wait_s = float(timeout)
            except Exception:
                wait_s = _LOCAL_DEFAULT_TIMEOUT
            if not evt.wait(max(0.1, wait_s)):
                res["detail"] = "execution timeout"
                self._audit_write(actor, t, data, "rejected",
                                  "execution timeout", remote)
                return res
            r = holder["res"] or {}
            status = str(r.get("status") or "failed")
            res["status"] = status
            res["detail"] = str(r.get("detail") or "")
            res["mode"] = str(r.get("mode") or "")
            res["ok"] = (status == "done")
            return res
        except Exception as e:
            _nl_log("control submit_local error: {}".format(e), "WARN")
            res["status"] = "rejected"
            res["detail"] = res["detail"] or "local submit error"
            return res

    # ── worker：全序串行处理 ──────────────────────────────────────────
    def _worker(self):
        try:
            while not self._stop_evt.is_set():
                try:
                    item = self._q.get(timeout=0.5)
                except queue.Empty:
                    continue
                if item is None:
                    break
                try:
                    self._process(item)
                except Exception as e:
                    _nl_log("control process error: {}".format(e), "WARN")
        except Exception as e:
            _nl_log("control worker fatal: {}".format(e), "ERROR")

    def _process(self, item):
        # 队列项：旧 6 元组 (conn,t,data,actor,loops,remote) 向后兼容；
        # 新 8 元组追加 (reply, local) —— local=True 即网页面板本地提交。
        try:
            conn, t, data, actor, loops, remote = item[:6]
            reply = item[6] if len(item) > 6 else None
            local = bool(item[7]) if len(item) > 7 else False
        except Exception:
            return
        # ── 首次操控确认门控（worker 发起，主线程弹窗；local 策略见 _confirm_gate）──
        ok, reason = self._confirm_gate(conn, t, actor, remote, local=local)
        if not ok:
            self._err(conn, t, reason, actor)
            self._audit_write(actor, t, data, "rejected", reason, remote)
            self._reply_fire(reply, "rejected", reason)
            return

        if t in (T_CMD_PAUSE, T_CMD_RESUME):
            self._do_pause_resume(conn, t, actor, data, remote)
        elif t == T_CMD_STOP:
            self._do_stop(conn, t, actor, data, remote, reply=reply)
        elif t in (T_CMD_RUN, T_CMD_RUN_SCRIPT):
            self._do_run(conn, t, actor, data, loops, remote, reply=reply)
        elif reply is not None:
            # 未匹配指令（理论上不可达）：确保本地等待方不被挂死
            self._reply_fire(reply, "rejected", "unsupported command")

    # ── 工作流分叉执行 ────────────────────────────────────────────────
    def _do_pause_resume(self, conn, t, actor, data, remote):
        eng = _workflow_engine()
        if _workflow_active(eng):
            mode = "workflow"
            try:
                if t == T_CMD_PAUSE:
                    eng.pause()
                else:
                    eng.resume()
            except Exception as e:
                self._err(conn, t, "workflow control failed: {}".format(e), actor)
                self._audit_write(actor, t, data, "err", str(e), remote)
                return
        else:
            mode = "script"
            try:
                if t == T_CMD_PAUSE:
                    state.pause_event.clear()
                else:
                    state.pause_event.set()
            except Exception as e:
                self._err(conn, t, "script control failed: {}".format(e), actor)
                self._audit_write(actor, t, data, "err", str(e), remote)
                return
        self._ack_terminal(t, "done", "done", actor, mode=mode)
        self._audit_write(actor, t, data, "ok", "mode={}".format(mode), remote)

    def _do_stop(self, conn, t, actor, data, remote, reply=None):
        reply = self._reply_once(reply)
        eng = _workflow_engine()
        wf = _workflow_active(eng)
        if wf:
            try:
                eng.stop()
            except Exception:
                pass
        # 脚本停止路径：无论是否工作流都执行（有 stop 钩子调钩子，否则复刻状态设置）
        hook = self._hooks.get("stop")
        if hook is not None:
            try:
                hook()
            except Exception as e:
                _nl_log("control stop hook error: {}".format(e), "WARN")
        else:
            try:
                state.quit3 = True
                state.quit2 = True
                state.running = False
                state.pause_event.set()
                state.exec_state["loop"] = 0
                state.exec_state["row"] = 0
            except Exception:
                pass
        mode = "workflow" if wf else "script"
        was_active = wf or self._running()
        detail = "done" if was_active else "not running"
        self._ack_terminal(t, "done", detail, actor, mode=mode)
        self._audit_write(actor, t, data, "ok",
                          "mode={} {}".format(mode, detail), remote)
        self._reply_fire(reply, "done", detail, mode)

    def _do_run(self, conn, t, actor, data, loops, remote, reply=None):
        mode = "script"
        hook = self._hooks.get("run")
        reply = self._reply_once(reply)

        def fn():
            try:
                if hook is None:
                    self._err(conn, t, "run hook not registered", actor)
                    self._audit_write(actor, t, data, "err",
                                      "run hook not registered", remote)
                    self._reply_fire(reply, "failed", "run hook not registered", mode)
                    return
                try:
                    hook(loops)
                except Exception as e:
                    self._ack_terminal(t, "failed", "run failed: {}".format(e),
                                       actor, mode=mode)
                    self._audit_write(actor, t, data, "err", str(e), remote)
                    self._reply_fire(reply, "failed", "run failed: {}".format(e), mode)
                    return
                self._ack_terminal(t, "done", "done", actor, mode=mode)
                self._audit_write(actor, t, data, "ok", "mode={}".format(mode), remote)
                self._reply_fire(reply, "done", "done", mode)
            except Exception as e:
                _nl_log("control run fn error: {}".format(e), "WARN")
                self._reply_fire(reply, "failed", "run error: {}".format(e), mode)

        # 优先投递到主线程泵执行；泵不可用时退化直接执行，保证 headless 可用
        if self._post(fn):
            return
        fn()

    # ── 首次操控确认门控 ──────────────────────────────────────────────
    def _confirm_gate(self, conn, cmd, actor, remote, local=False):
        """返回 (ok, reason)。关闭确认 / 已记住该设备 / 无 GUI 时按规则放行或拒绝。

        local=True（网页面板本地提交）：**PIN 解锁即视为本机所有者授权**，
        默认不再弹桌面确认（配置 `netlink_web_confirm_control` 默认 False）；
        置 True 时走与远端一致的 `_confirm_action` 弹窗（更严档）。
        本地路径**绝不**读取/写入 `NETLINK_CONFIRMED_PEERS`（那是远端设备指纹集合）。
        """
        try:
            if cmd not in _CONFIRM_CMDS:
                return True, ""
            if local:
                need_local = False
                try:
                    need_local = bool(getattr(state,
                                              "NETLINK_WEB_CONFIRM_CONTROL", False))
                except Exception:
                    need_local = False
                if not need_local:
                    return True, ""        # 本地面板默认直通（PIN 已授权）
                ok2, _remember, reason2 = self._confirm_action(
                    actor, cmd, remote=remote)
                if not ok2:
                    return False, (reason2 or "user rejected")
                return True, ""            # 本地不写入已确认设备集合
            need = True
            try:
                need = bool(getattr(state, "NETLINK_CONFIRM_CONTROL", True))
            except Exception:
                need = True
            if not need:
                return True, ""            # 直通（测试可用）
            fp = self._fingerprint(conn)
            try:
                confirmed = list(getattr(state, "NETLINK_CONFIRMED_PEERS", []) or [])
            except Exception:
                confirmed = []
            if fp and fp in confirmed:
                return True, ""
            ok, remember, reason = self._confirm(actor, cmd, remote=remote)
            if not ok:
                return False, (reason or "user rejected")
            if remember and fp:
                try:
                    if fp not in confirmed:
                        confirmed.append(fp)
                    state.NETLINK_CONFIRMED_PEERS = confirmed
                    state.save_config()
                except Exception:
                    pass
            return True, ""
        except Exception as e:
            _nl_log("control confirm gate error: {}".format(e), "WARN")
            return False, "confirm error"

    def _confirm_action(self, actor, cmd, timeout=_CONFIRM_TIMEOUT, remote=""):
        """确认动作的可替换入口（测试 mock 点）：默认委托 `_confirm`。

        `_confirm_gate` 只经此入口发起弹窗，便于自测断言「未弹窗」。
        """
        return self._confirm(actor, cmd, timeout=timeout, remote=remote)

    def _confirm(self, actor, cmd, timeout=_CONFIRM_TIMEOUT, remote=""):
        """主线程弹出自绘确认对话框；返回 (ok, remember, reason)。

        自绘 Toplevel（而非 messagebox）以确保超时可控：
          * grab_set() 独占 + 倒计时标签 + [允许]/[拒绝]/[允许并记住此设备] 三按钮；
        无 GUI（root is None）→ (False, False, "confirm unavailable")。
        """
        root = self._root()
        if root is None or not self._pump_on:
            return False, False, "confirm unavailable"
        result = {"ok": False, "remember": False, "done": False}
        evt = threading.Event()
        holder = {"dlg": None}

        def _settle(ok, remember):
            if result["done"]:
                return
            result["ok"] = bool(ok)
            result["remember"] = bool(remember)
            result["done"] = True
            try:
                dlg = holder.get("dlg")
                if dlg is not None:
                    try:
                        dlg.grab_release()
                    except Exception:
                        pass
                    try:
                        dlg.destroy()
                    except Exception:
                        pass
            except Exception:
                pass
            evt.set()

        def _show():
            try:
                import tkinter as tk
            except Exception:
                _settle(False, False)
                return
            try:
                dlg = tk.Toplevel(root)
                holder["dlg"] = dlg
                dlg.title("远程操控请求")
                try:
                    dlg.transient(root)
                except Exception:
                    pass
                try:
                    dlg.attributes("-topmost", True)
                except Exception:
                    pass
                body = ("设备: {}\n指令: {}\n来源: {}\n\n是否允许本次远程操控？").format(
                    actor or "unknown", cmd or "", remote or "unknown")
                tk.Label(dlg, text=body, justify="left",
                         padx=16, pady=12).pack()
                cd_var = tk.Label(dlg, text="")
                cd_var.pack()
                btns = tk.Frame(dlg)
                btns.pack(pady=8)
                tk.Button(btns, text="拒绝", width=10,
                          command=lambda: _settle(False, False)).pack(side="left", padx=4)
                tk.Button(btns, text="允许并记住此设备", width=16,
                          command=lambda: _settle(True, True)).pack(side="left", padx=4)
                tk.Button(btns, text="允许", width=10,
                          command=lambda: _settle(True, False)).pack(side="left", padx=4)
                try:
                    dlg.protocol("WM_DELETE_WINDOW", lambda: _settle(False, False))
                except Exception:
                    pass
                try:
                    dlg.grab_set()
                except Exception:
                    pass
                try:
                    dlg.focus_force()
                except Exception:
                    pass

                left = {"n": int(timeout)}

                def _tick():
                    if result["done"]:
                        return
                    if left["n"] <= 0:
                        _settle(False, False)
                        return
                    try:
                        cd_var.config(text="{} 秒后自动拒绝".format(left["n"]))
                    except Exception:
                        pass
                    left["n"] -= 1
                    try:
                        dlg.after(1000, _tick)
                    except Exception:
                        pass

                try:
                    dlg.after(0, _tick)
                except Exception:
                    pass
            except Exception:
                _settle(False, False)

        if not self._post(_show):
            return False, False, "confirm unavailable"

        if not evt.wait(timeout):
            # 超时：请主线程关闭对话框并把结果落定为拒绝
            self._post(lambda: _settle(False, False))
            return False, False, "confirm timeout"
        return result["ok"], result["remember"], ("" if result["ok"] else "user rejected")
