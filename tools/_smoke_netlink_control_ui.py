# -*- coding: utf-8 -*-
"""NetLink Phase2-2b 远程操控 UI 集成自测 —— 全自动、无弹窗、总时长 < 50s。

运行:   python tools/_smoke_netlink_control_ui.py
退出码: 0 = 全部断言通过（含 [SKIP] no display，允许 [WARN]）；1 = 存在 [FAIL]

设计要点:
  * 真实 Tk root (withdraw) + **同进程双节点**（A=被控端 tcp 19980，B=控制端 tcp 19990）；
    A 直接构造 NetLinkNode（独立 NetBus），B 走 netlink.start_netlink（门面单例）以便
    「设备互联」窗口绑定到 B；
  * state 为进程全局、A/B 共享，故所有执行态在使用前显式重置；
  * 仅用 root.update() 驱动事件循环（不进入 mainloop），同时驱动窗口 _pump 与
    两侧 ControlExecutor 的主线程泵；
  * 二次确认对话框（本地 messagebox.askyesno）在测试内以
    `win._confirm_action = lambda *a, **k: True` 替换绕过，避免阻塞；
  * 隔离: state.CONFIG_PATH 指向临时文件；备份/还原 NETLINK_PEERS /
    NETLINK_CONFIRMED_PEERS / API_KEY / NETLINK_SCRIPT_DIR / running / quit2 /
    recording / has_script / filename / pause_event、凭据库、nl_node.make_node_id、
    sys.modules["workflow"]；
  * 端口用 199xx 段（19980/19981、19990/19991），用完即释放。

断言顺序（第 9 条提前到第 6 条之后执行，以保证在权限回收前判定「未运行态」）:
  1/2/3/4/5/6/9/7/8/10/11。
"""
import os
import shutil
import sys
import tempfile
import threading
import time
import traceback
import types
import uuid

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_SRC = os.path.join(_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import tkinter
import state
import netlink
import netlink.node as nl_node
import netlink_window
from netlink.bus import NetBus
from netlink import security
from netlink.node import TOPIC_AUTH, TOPIC_CMD_RESULT
from netlink.protocol import make_msg, T_SUBSCRIBE

# ── 端口（199xx 段）──
A_TCP, A_UDP = 19980, 19981
B_TCP, B_UDP = 19990, 19991
TOTAL_BUDGET = 50.0

_fails = 0
_warns = 0
T0 = time.time()


def check(name, cond, detail=""):
    global _fails
    if cond:
        print("[OK]   " + name)
        return True
    _fails += 1
    print("[FAIL] " + name + (" :: " + detail if detail else ""))
    return False


def warn(msg):
    global _warns
    _warns += 1
    print("[WARN] " + msg)


def spin(root, seconds=0.5):
    """在给定秒数内反复 root.update() 驱动 _pump 与两侧控制泵。"""
    end = time.time() + float(seconds)
    while time.time() < end:
        try:
            root.update()
        except Exception:
            pass
        time.sleep(0.02)


def wait_until(fn, timeout, root):
    end = time.time() + float(timeout)
    while time.time() < end:
        try:
            if fn():
                return True
        except Exception:
            pass
        try:
            root.update()
        except Exception:
            pass
        time.sleep(0.02)
    return False


def _nl_threads():
    try:
        return sorted(t.name for t in threading.enumerate()
                      if t.name and t.name.startswith("nl-"))
    except Exception:
        return []


class FakeHooks(object):
    """记录 run/stop 调用；run 内部短暂阻塞以便观察 accepted→done 时序。"""

    def __init__(self):
        self._lock = threading.Lock()
        self.run_calls = []
        self.stop_calls = 0
        self.sleep = 0.25

    def run(self, loops=None):
        with self._lock:
            self.run_calls.append(loops)
        try:
            if self.sleep:
                time.sleep(self.sleep)
        except Exception:
            pass

    def stop(self):
        with self._lock:
            self.stop_calls += 1
        # 复刻 stop_execution() 的状态设置
        try:
            state.quit2 = True
            state.running = False
            state.pause_event.set()
        except Exception:
            pass


def _btn_state(btn):
    try:
        return str(btn.cget("state"))
    except Exception:
        return "?"


def _btn_text(btn):
    try:
        return str(btn.cget("text"))
    except Exception:
        return ""


def _select(win, nid):
    """在设备表选中 node_id 对应的行；成功返回 True。"""
    try:
        item = win._items.get(nid)
        if item is None:
            return False
        win.tv.selection_set(item)
        win._on_select()
        return True
    except Exception:
        return False


def _cmd_rows_with(win, label):
    """「远程指令」列表中指令列 == label 的行 values 列表。"""
    out = []
    try:
        for iid in win.tv_cmd.get_children():
            vals = win.tv_cmd.item(iid).get("values") or ()
            if len(vals) >= 4 and str(vals[2]) == label:
                out.append(tuple(vals))
    except Exception:
        pass
    return out


def main():
    # ── Tk root（无显示设备则 SKIP，退出码 0）──
    try:
        root = tkinter.Tk()
        root.withdraw()
    except Exception as e:
        print("[SKIP] no display ({})".format(e))
        return 0

    print("=" * 66)
    print("NetLink Phase2-2b remote-control UI integration smoke test")
    print("=" * 66)

    # ── 环境隔离：备份 ──
    orig = {
        "CONFIG_PATH": state.CONFIG_PATH,
        "NETLINK_PEERS": list(getattr(state, "NETLINK_PEERS", []) or []),
        "NETLINK_CONFIRMED_PEERS": list(
            getattr(state, "NETLINK_CONFIRMED_PEERS", []) or []),
        "API_KEY": getattr(state, "API_KEY", ""),
        "NETLINK_SCRIPT_DIR": getattr(state, "NETLINK_SCRIPT_DIR", ""),
        "NETLINK_REQUIRE_AUTH": getattr(state, "NETLINK_REQUIRE_AUTH", True),
        "NETLINK_CONFIRM_CONTROL": getattr(state, "NETLINK_CONFIRM_CONTROL", True),
        "running": getattr(state, "running", None),
        "quit2": getattr(state, "quit2", None),
        "recording": getattr(state, "recording", None),
        "has_script": getattr(state, "has_script", None),
        "filename": getattr(state, "filename", None),
        "pause_event": getattr(state, "pause_event", None),
    }
    orig_make_node_id = nl_node.make_node_id
    orig_workflow = sys.modules.get("workflow", None)
    orig_cred = None
    try:
        orig_cred = state.cred_read(state._CRED_TARGET)
    except Exception:
        orig_cred = None

    tmpdir = tempfile.mkdtemp(prefix="acrpa_ctlui_")
    state.CONFIG_PATH = os.path.join(tmpdir, "config.json")
    state.NETLINK_PEERS = []
    state.NETLINK_CONFIRMED_PEERS = []

    # 唯一 node_id 生成器（A/B 同进程需不同身份；含随机 tag 避免与历史运行/真实数据
    # 的「已记住凭据」撞车 —— 否则会走 resume 而非 pair，导致配对失败）
    id_seq = {"n": 0}
    run_tag = uuid.uuid4().hex[:6]

    def _mk_id():
        id_seq["n"] += 1
        return "nlu{}{}".format(run_tag, id_seq["n"])

    nl_node.make_node_id = _mk_id

    # 强制脚本分叉（无工作流引擎）
    sys.modules["workflow"] = types.SimpleNamespace(workflow_engine=None)

    hooks = FakeHooks()
    a = None
    win = None
    b_fp = ""
    fps = []
    rec_perm = []
    rec_cmd = []
    try:
        # ── A（被控端）：直接构造节点 ──
        state.NETLINK_ENABLED = True
        state.NETLINK_PORT = A_TCP
        state.NETLINK_DISCOVERY_PORT = A_UDP
        state.NETLINK_AUTODISCOVER = False
        state.NETLINK_DEVICE_NAME = "NetLinkA"
        state.NETLINK_STATIC_PEERS = []
        state.NETLINK_REQUIRE_AUTH = True
        state.NETLINK_PIN_TTL = 600
        state.NETLINK_CONFIRM_CONTROL = False
        state.NETLINK_CONFIRMED_PEERS = []
        state.NETLINK_SCRIPT_DIR = ""
        state.running = False
        state.recording = False
        state.quit2 = False
        state.has_script = True

        a = nl_node.NetLinkNode(root=root, bus=NetBus())
        ok_a = a.start()
        check("0. A(被控端) start (tcp {})".format(A_TCP), bool(ok_a))
        a.set_control_hooks(run=hooks.run, stop=hooks.stop)

        # ── B（控制端）：门面单例 + 窗口 ──
        state.NETLINK_PORT = B_TCP
        state.NETLINK_DISCOVERY_PORT = B_UDP
        state.NETLINK_DEVICE_NAME = "NetLinkB"
        state.NETLINK_STATIC_PEERS = []
        state.NETLINK_AUTODISCOVER = False
        ok_b = netlink.start_netlink(root)
        check("0b. B(控制端) start (tcp {})".format(B_TCP), bool(ok_b))
        b = netlink.get_node()
        check("0c. netlink.get_node() 可用", b is not None and b is not a)

        win = netlink_window.open_netlink_window(root)
        spin(root, 0.8)
        check("0d. open_netlink_window 成功", win is not None
              and netlink_window.is_open() and win.alive())

        if a is None or b is None or win is None or not win.alive():
            raise RuntimeError("环境未就绪，无法继续")

        # 总线记录器（捕获时序）
        def on_auth(topic, payload):
            try:
                if isinstance(payload, dict):
                    rec_perm.append(str(payload.get("stage") or ""))
            except Exception:
                pass

        def on_cmd(topic, payload):
            try:
                d = (payload or {}).get("data") or {}
                rec_cmd.append(str(d.get("status") or ""))
            except Exception:
                pass

        try:
            b.bus.subscribe(TOPIC_AUTH, on_auth)
            b.bus.subscribe(TOPIC_CMD_RESULT, on_cmd)
        except Exception:
            pass

        # ── 配对：A 开配对窗口 → B register_target(pin) ──
        # 先清理可能残留的凭据（Windows 凭据库为全局；否则会走 resume 而非 pair）
        a_self_fp = str(getattr(a, "_self_fp", "") or "")
        b_self_fp = str(getattr(b, "_self_fp", "") or "")
        for _fp in (a_self_fp, b_self_fp):
            if _fp:
                fps.append(_fp)
                try:
                    security.forget_peer_token(_fp)
                except Exception:
                    pass
        pin = a.open_pair_window()
        check("0e. A.open_pair_window() 返回 6 位 PIN",
              isinstance(pin, str) and len(pin) == 6 and pin.isdigit(),
              "pin={!r}".format(pin))
        b.register_target("127.0.0.1", A_TCP, pin)
        a_node_id = str(a.info.get("node_id") or "")
        paired = wait_until(lambda: (len(a.list_peers() or []) >= 1
                                     and a_node_id in win._authed), 8.0, root)
        check("0f. 配对完成（AUTH_OK，A 白名单有 B / B 窗口 _authed 有 A）",
              paired, "peers={} authed={}".format(len(a.list_peers() or []),
                                                  list(win._authed.keys())))
        if not paired:
            raise RuntimeError("配对未完成，无法继续")
        b_fp = str((a.list_peers() or [{}])[0].get("fingerprint") or "")

        # 模拟「发现到 A 的真实 node_id」（生产环境由 UDP 广播提供；本测试关闭了自动发现）
        b._on_peer({"node_id": a_node_id, "name": "NetLinkA", "host": "127.0.0.1",
                    "port": A_TCP, "version": "", "online": True})
        wait_until(lambda: win._items.get(a_node_id) is not None, 2.0, root)

        # ── 工具：让 B 订阅 A 的 state（生产由控制端订阅；本测试手工发起并容错重发）──
        def subscribe_state():
            conn = None
            for c, rec in list(b._conns.items()):
                try:
                    if rec.get("outbound") and str(rec.get("node_id")) == a_node_id:
                        conn = c
                except Exception:
                    pass
            if conn is None:
                return False
            try:
                return bool(b.send(conn, make_msg(T_SUBSCRIBE, {"topics": ["state"]})))
            except Exception:
                return False

        def ensure_state(pred, tries=5):
            for _ in range(tries):
                subscribe_state()
                if wait_until(pred, 1.5, root):
                    return True
            return False

        # ══════════════ 1. 配对后（observe）→ 远程运行 disabled ══════════════
        _select(win, a_node_id)
        spin(root, 0.5)
        perm_txt = win._perm_text(a_node_id)
        check("1. 配对后(perm=observe) [▶远程运行] disabled（权限不足）",
              _btn_state(win.btn_run) == "disabled" and perm_txt == "仅观察",
              "state={} perm={!r}".format(_btn_state(win.btn_run), perm_txt))

        # ══════════════ 2. 授权 control → B 收到 stage=perm 且按钮可用 ══════════════
        a.set_peer_perm(b_fp, "control")
        state.running = True          # [⏹停止] 仅在设备运行/暂停时可用（见启用规则）
        state.recording = False
        state.pause_event.set()
        ensure_state(lambda: bool((win._states.get(a_node_id) or {}).get("running")))
        got_perm = wait_until(lambda: ("perm" in rec_perm
                                       and _btn_state(win.btn_run) == "normal"
                                       and _btn_state(win.btn_stop) == "normal"),
                              3.0, root)
        check("2. A.set_peer_perm(control) → B 收到 TOPIC_AUTH stage=perm 且"
              " [▶远程运行]/[⏹停止] 可用",
              got_perm, "stages={} run={} stop={}".format(
                  rec_perm, _btn_state(win.btn_run), _btn_state(win.btn_stop)))
        # ══════════════ 3. 点 [▶远程运行] → fake_run 被调用（绕过确认弹窗）══════════════
        win._confirm_action = lambda *args, **kwargs: True   # patch 二次确认
        state.has_script = True
        state.filename = os.path.join(tmpdir, "demo.xls")
        state.running = False
        state.recording = False
        state.quit2 = False
        _select(win, a_node_id)
        spin(root, 0.4)
        pre_ok = _btn_state(win.btn_run) == "normal"
        win.btn_run.invoke()
        got_run = wait_until(lambda: len(hooks.run_calls) >= 1, 2.0, root)
        check("3. 点[▶远程运行]（patched 确认）→ ≤2s fake_run 被调用 1 次",
              pre_ok and got_run and len(hooks.run_calls) == 1,
              "pre={} calls={}".format(pre_ok, len(hooks.run_calls)))

        # ══════════════ 4. 远程指令列表：已受理 → 成功（同一行）══════════════
        terminal = wait_until(lambda: "done" in rec_cmd, 3.0, root)
        spin(root, 0.4)
        rows = _cmd_rows_with(win, "远程运行")
        seq_ok = ("accepted" in rec_cmd and "done" in rec_cmd
                  and rec_cmd.index("accepted") < rec_cmd.index("done"))
        row_ok = (len(rows) == 1 and str(rows[0][3]).startswith("成功"))
        check("4. 「远程指令」出现该指令：已受理 → 成功（合并为同一行）",
              terminal and seq_ok and row_ok,
              "statuses={} rows={}".format(rec_cmd, rows))
        check("4b. 底部状态行摘要含「远程运行 成功」",
              "远程运行 成功" in str(win._cmd_summary),
              "summary={!r}".format(win._cmd_summary))

        # ══════════════ 5. 暂停 / 恢复（同一按钮切换）══════════════
        state.running = True
        state.recording = False
        state.pause_event.set()
        running_seen = ensure_state(
            lambda: bool((win._states.get(a_node_id) or {}).get("running")))
        _select(win, a_node_id)
        spin(root, 0.4)
        pause_ready = wait_until(lambda: _btn_state(win.btn_pause) == "normal",
                                 2.0, root)
        win.btn_pause.invoke()
        paused_ok = wait_until(lambda: state.pause_event.is_set() is False,
                               2.0, root)
        text_ok = False
        for _ in range(5):
            if "恢复" in _btn_text(win.btn_pause):
                text_ok = True
                break
            subscribe_state()
            spin(root, 0.5)
        check("5a. 点[⏸暂停] → state.pause_event.is_set() is False",
              running_seen and pause_ready and paused_ok,
              "seen={} ready={} set={}".format(running_seen, pause_ready,
                                               state.pause_event.is_set()))
        check("5b. 暂停后按钮文案变为 [▶ 恢复]", text_ok,
              "text={!r}".format(_btn_text(win.btn_pause)))
        win.btn_pause.invoke()
        resume_ok = wait_until(lambda: state.pause_event.is_set() is True,
                              2.0, root)
        check("5c. 再点[▶恢复] → state.pause_event 重新 set", resume_ok,
              "set={}".format(state.pause_event.is_set()))

        # ══════════════ 6. 点 [⏹停止] → fake_stop + quit2 ══════════════
        state.quit2 = False
        state.running = True
        state.pause_event.set()
        ensure_state(lambda: bool((win._states.get(a_node_id) or {}).get("running")))
        _select(win, a_node_id)
        spin(root, 0.4)
        stop_ready = wait_until(lambda: _btn_state(win.btn_stop) == "normal",
                                2.0, root)
        win.btn_stop.invoke()
        stop_ok = wait_until(lambda: (hooks.stop_calls >= 1
                                      and state.quit2 is True), 2.5, root)
        check("6. 点[⏹停止] → fake_stop 被调用且 state.quit2 is True",
              stop_ready and stop_ok,
              "ready={} stop_calls={} quit2={}".format(
                  stop_ready, hooks.stop_calls, state.quit2))

        # ══════════════ 9. 未运行态 → 暂停/停止 disabled ══════════════
        state.running = False
        state.pause_event.set()
        state.recording = False
        ensure_state(lambda: (win._states.get(a_node_id) or {}).get("running") is False)
        _select(win, a_node_id)
        spin(root, 0.5)
        check("9. 未运行态(running=False) → [⏸暂停]/[⏹停止] disabled",
              _btn_state(win.btn_pause) == "disabled"
              and _btn_state(win.btn_stop) == "disabled",
              "pause={} stop={}".format(_btn_state(win.btn_pause),
                                        _btn_state(win.btn_stop)))

        # ══════════════ 7. 权限回收 observe → 操控按钮重新 disabled ══════════════
        a.set_peer_perm(b_fp, "observe")
        revoked = wait_until(lambda: (_btn_state(win.btn_run) == "disabled"
                                      and _btn_state(win.btn_stop) == "disabled"),
                             2.0, root)
        check("7. A.set_peer_perm(observe) → B 操控按钮重新 disabled",
              revoked, "run={} stop={}".format(_btn_state(win.btn_run),
                                               _btn_state(win.btn_stop)))

        # ══════════════ 8. 审计：A 端记录 + B 窗口审计入口 ══════════════
        tail = []
        try:
            tail = a.control.audit.tail(50) or []
        except Exception:
            tail = []
        joined = "\n".join(str(x) for x in tail)
        check("8a. A.control.audit.tail(50) 非空且含 cmd=CMD_RUN",
              len(tail) > 0 and "cmd=CMD_RUN" in joined,
              "n={} has_cmd={}".format(len(tail), "cmd=CMD_RUN" in joined))

        win.btn_audit.invoke()
        spin(root, 0.5)
        dlg = win._audit_dlg
        exists = False
        try:
            exists = dlg is not None and bool(dlg.winfo_exists())
        except Exception:
            exists = False
        txt = ""
        try:
            if win._audit_text is not None:
                txt = win._audit_text.get("1.0", "end")
        except Exception:
            txt = ""
        check("8b. B 窗口 [📜审计日志] 弹窗存在且其 Text 内容非空",
              exists and bool(txt.strip()),
              "exists={} len={}".format(exists, len(txt.strip())))
        # 归属结论：control.py 只在「执行侧」写审计；B 的 AuditLog 与 A 共用同一
        # 审计目录（同一 CONFIG_PATH → 同一 logs/），故默认读到的内容与 A 相同。
        if "cmd=CMD_RUN" in txt:
            print("[OK]   8c. B 侧审计文本亦含 cmd=CMD_RUN"
                  "（因 A/B 共用同一 CONFIG_PATH 下的审计文件）")
        else:
            warn("8c. B 侧审计文本不含 cmd=CMD_RUN —— 结论：审计仅记录执行侧"
                 "（control.py 只写被控端），B 自身执行侧无记录")
        try:
            win._close_audit_dialog()
        except Exception:
            pass

        # ══════════════ 10. 未认证设备 → 权限列=未认证 且四按钮 disabled ══════════════
        ghost_id = "nlghost000000001"
        try:
            win._peers[ghost_id] = {"name": "Ghost", "host": "", "port": 0,
                                    "version": "", "online": True}
        except Exception:
            pass
        wait_until(lambda: win._items.get(ghost_id) is not None, 2.0, root)
        _select(win, ghost_id)
        spin(root, 0.5)
        ghost_perm = win._perm_text(ghost_id)
        four_disabled = all(_btn_state(x) == "disabled"
                            for x in (win.btn_run, win.btn_pause,
                                      win.btn_stop, win.btn_script))
        check("10. 未认证设备权限列=未认证 且四个按钮 disabled",
              ghost_perm == "未认证" and four_disabled,
              "perm={!r} four_disabled={}".format(ghost_perm, four_disabled))

    except Exception:
        print("[FAIL] unexpected exception:")
        traceback.print_exc()
        _fails += 1
    finally:
        rc = finish(root, a, win, orig, orig_make_node_id, orig_workflow,
                    orig_cred, tmpdir, fps)
        return rc


def finish(root, a, win, orig, orig_make_node_id, orig_workflow, orig_cred,
           tmpdir, fps):
    global _fails
    # ── 收尾：关窗 → 停两节点 ──
    try:
        netlink_window.close_window()
    except Exception:
        pass
    try:
        netlink.stop_netlink()
    except Exception:
        pass
    try:
        if a is not None:
            a.stop()
    except Exception:
        pass
    spin(root, 0.4)

    # ══════════════ 11. nl-* 线程收敛（不满足记 WARN）══════════════
    t11 = time.time()
    converged = wait_until(lambda: len(_nl_threads()) == 0, 5.0, root)
    dt11 = time.time() - t11
    if converged:
        check("11. 两节点 stop 后 nl-* 线程收敛为 0 (实测 {:.1f}s)".format(dt11), True)
    else:
        warn("11. nl-* 线程 5s 内未收敛为 0: {}".format(_nl_threads()))

    # ── 还原 state / 凭据库 / 模块补丁 ──
    try:
        state.CONFIG_PATH = orig["CONFIG_PATH"]
        state.NETLINK_PEERS = orig["NETLINK_PEERS"]
        state.NETLINK_CONFIRMED_PEERS = orig["NETLINK_CONFIRMED_PEERS"]
        state.API_KEY = orig["API_KEY"]
        state.NETLINK_SCRIPT_DIR = orig["NETLINK_SCRIPT_DIR"]
        state.NETLINK_REQUIRE_AUTH = orig["NETLINK_REQUIRE_AUTH"]
        state.NETLINK_CONFIRM_CONTROL = orig["NETLINK_CONFIRM_CONTROL"]
        state.running = orig["running"]
        state.quit2 = orig["quit2"]
        state.recording = orig["recording"]
        state.has_script = orig["has_script"]
        state.filename = orig["filename"]
        if orig["pause_event"] is not None:
            state.pause_event = orig["pause_event"]
        if orig_cred:
            state.cred_write(state._CRED_TARGET, orig_cred)
        else:
            state.cred_delete(state._CRED_TARGET)
    except Exception:
        pass
    try:
        nl_node.make_node_id = orig_make_node_id
    except Exception:
        pass
    try:
        if orig_workflow is None:
            sys.modules.pop("workflow", None)
        else:
            sys.modules["workflow"] = orig_workflow
    except Exception:
        pass
    for fp in (fps or []):
        if fp:
            try:
                security.forget_peer_token(fp)
            except Exception:
                pass
    try:
        shutil.rmtree(tmpdir, ignore_errors=True)
    except Exception:
        pass
    try:
        root.destroy()
    except Exception:
        pass

    elapsed = time.time() - T0
    print("-" * 66)
    if elapsed >= TOTAL_BUDGET:
        _fails += 1
        print("[FAIL] 总耗时 {:.1f}s 超过 {}s 预算".format(elapsed, TOTAL_BUDGET))
    else:
        print("[OK]   总耗时 {:.1f}s (< {}s)".format(elapsed, TOTAL_BUDGET))
    print("warns={}".format(_warns))
    if _fails == 0:
        print("PASS (all assertions OK)")
        return 0
    print("FAIL ({} assertion(s) failed)".format(_fails))
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
