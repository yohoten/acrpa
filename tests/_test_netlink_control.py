# -*- coding: utf-8 -*-
"""NetLink Phase2-2 远程操控内核专项自测 —— 全自动、总时长 < 45s。

运行:  python tools/_test_netlink_control.py
退出码: 0 = 全部断言通过（允许 [WARN]）；1 = 存在 [FAIL]

要点:
  * 隔离：备份并还原 state.CONFIG_PATH / NETLINK_PEERS / NETLINK_CONFIRMED_PEERS /
    API_KEY / 执行态 / 凭据库 / sys.modules["workflow"]；
  * 端口用 199xx 段（19980 / 19981），用完释放；
  * 通过 netlink.set_control_hooks(run=fake, stop=fake) 注入假钩子（不依赖真实 main_run）；
  * 真实 Tk root（withdraw）供 run 回主线程执行；无显示设备时退化直执行；
  * 用原始 socket 走完 HELLO + AUTH(pair) 建立已认证连接后发 CMD。

覆盖 15 项断言：权限门槛 / RUN 直通 / 无脚本 / 运行中 / 录制中 / PAUSE 未运行 /
脚本分叉 PAUSE-RESUME / 工作流分叉 / STOP 脚本 / RUN_SCRIPT 正常 / 路径穿越 /
串行化 / 审计 / 广播 / 首次确认弹窗。
"""
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import traceback
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_SRC = os.path.join(_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import tkinter  # noqa: F401  —— utils 顶层 import tkinter，提前加载
import state
import netlink
import netlink.node as nl_node
from netlink.bus import NetBus
from netlink import security
from netlink.protocol import (
    make_msg, frame_message, FrameReader, parse_msg,
    T_HELLO, T_AUTH, T_AUTH_OK, T_AUTH_FAIL, T_AUTH_CHALLENGE,
    T_CMD_ACK, T_CMD_ERR,
    T_CMD_RUN, T_CMD_PAUSE, T_CMD_RESUME, T_CMD_STOP, T_CMD_RUN_SCRIPT,
)

# ── 端口（199xx 段）──
TCP_PORT, UDP_PORT = 19980, 19981
TOTAL_BUDGET = 45.0

CLI1_ID, CLI1_NAME = "ctlcli0000000001", "CtlCli1"
CLI2_ID, CLI2_NAME = "ctlcli0000000002", "CtlCli2"
CLI1_FP = security.make_fingerprint(CLI1_ID, CLI1_NAME)
CLI2_FP = security.make_fingerprint(CLI2_ID, CLI2_NAME)

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


# ── 原始 socket 客户端 ────────────────────────────────────────────────────
class RawClient(object):
    def __init__(self, port, host="127.0.0.1"):
        self.s = socket.create_connection((host, int(port)), timeout=3.0)
        self.reader = FrameReader()
        self.inbox = []

    def send(self, msg):
        try:
            self.s.sendall(frame_message(msg))
            return True
        except Exception:
            return False

    def poll(self, timeout=0.02):
        """把可用字节读进 inbox（不弹出），供顺序消费。"""
        try:
            self.s.settimeout(max(0.001, float(timeout)))
            chunk = self.s.recv(65536)
        except socket.timeout:
            return
        except Exception:
            return
        if not chunk:
            return
        try:
            for p in self.reader.feed(chunk):
                self.inbox.append(parse_msg(p))
        except Exception:
            return

    def recv(self, timeout=2.0):
        if self.inbox:
            return self.inbox.pop(0)
        deadline = time.time() + max(0.0, float(timeout))
        while time.time() < deadline:
            self.poll(deadline - time.time())
            if self.inbox:
                return self.inbox.pop(0)
        return None

    def wait_any(self, types, timeout=2.0):
        types = tuple(types)
        deadline = time.time() + float(timeout)
        while time.time() < deadline:
            m = self.recv(deadline - time.time())
            if m is None:
                continue
            if m.get("t") in types:
                return m
        return None

    def wait(self, t, timeout=2.0):
        return self.wait_any((t,), timeout)

    def close(self):
        try:
            self.s.close()
        except Exception:
            pass


def connect(port):
    c = RawClient(port)
    c.wait(T_HELLO, 2.0)      # 吞掉被控端主动下发的 HELLO
    return c


def do_pair(c, pin, node_id, name, fp):
    """HELLO + AUTH(pair) 四步；返回 (有 proof 的) 最终响应 AUTH_OK/AUTH_FAIL。"""
    c.send(make_msg(T_HELLO, {"node_id": node_id, "name": name,
                              "version": "test", "caps": {}, "fingerprint": fp}))
    c.send(make_msg(T_AUTH, {"mode": "pair", "node_id": node_id,
                             "fingerprint": fp, "name": name}))
    ch = c.wait(T_AUTH_CHALLENGE, 3.0)
    d = (ch or {}).get("data") or {}
    nonce = str(d.get("nonce") or "")
    salt = str(d.get("salt") or "")
    token = security.derive_token(pin, salt)
    ts = int(time.time())
    proof = security.make_proof(token, nonce, node_id, ts)
    c.send(make_msg(T_AUTH, {"nonce": nonce, "proof": proof, "ts": ts}))
    return c.wait_any((T_AUTH_OK, T_AUTH_FAIL), 3.0)


# ── 假钩子 / 假工作流引擎 ─────────────────────────────────────────────────
class FakeHooks(object):
    """记录 run/stop 调用；run 内部维护并发计数器以验证串行化。"""

    def __init__(self):
        self._lock = threading.Lock()
        self.reset()
        self.sleep = 0.0

    def reset(self):
        with self._lock:
            self.run_calls = []
            self.stop_calls = 0
            self.active = 0
            self.max_active = 0

    def run(self, loops=None):
        with self._lock:
            self.active += 1
            if self.active > self.max_active:
                self.max_active = self.active
            self.run_calls.append(loops)
        try:
            if self.sleep:
                time.sleep(self.sleep)
        finally:
            with self._lock:
                self.active -= 1

    def stop(self):
        with self._lock:
            self.stop_calls += 1
        # 复刻 stop_execution() 的状态设置（生产 stop 钩子即 stop_execution）
        try:
            state.quit3 = True
            state.quit2 = True
            state.running = False
            state.pause_event.set()
            state.exec_state["loop"] = 0
            state.exec_state["row"] = 0
        except Exception:
            pass


class FakeEngine(object):
    """假工作流引擎：暴露 current_step / _stopped 及 pause/resume/stop。"""

    def __init__(self, current_step=-1, stopped=True):
        self._current_step = current_step
        self._stopped = stopped
        self.paused = False
        self.resumed = False
        self.stopped_called = False

    @property
    def current_step(self):
        return self._current_step

    def pause(self):
        self.paused = True

    def resume(self):
        self.resumed = True

    def stop(self):
        self.stopped_called = True
        self._stopped = True


def set_workflow(engine):
    """把假 workflow 模块注入 sys.modules，使 control._workflow_engine() 返回它。"""
    sys.modules["workflow"] = types.SimpleNamespace(workflow_engine=engine)


# ── 通用等待 / 泵 ─────────────────────────────────────────────────────────
def _pump(root, client=None):
    if root is not None:
        try:
            root.update()
        except Exception:
            pass
    if client is not None:
        client.poll(0.01)


def wait_until(fn, timeout, root=None, client=None):
    end = time.time() + timeout
    while time.time() < end:
        _pump(root, client)
        try:
            if fn():
                return True
        except Exception:
            pass
        time.sleep(0.01)
    return False


def drain(client, wait=0.25):
    end = time.time() + wait
    while time.time() < end:
        client.poll(0.02)
    client.inbox = []


def wait_any_cmd(client, cmd, timeout=4.0, root=None, include_accepted=False):
    """等待 cmd 的终态（ACK done/failed 或 ERR）；include_accepted 时也接受 accepted。"""
    end = time.time() + timeout
    while time.time() < end:
        _pump(root, client)
        while client.inbox:
            m = client.inbox.pop(0)
            d = m.get("data") or {}
            if d.get("cmd") != cmd:
                continue
            if m.get("t") == T_CMD_ERR:
                return m
            if m.get("t") == T_CMD_ACK:
                st = d.get("status")
                if st in ("done", "failed"):
                    return m
                if include_accepted and st == "accepted":
                    return m
        time.sleep(0.01)
    return None


def find_dialog(root, title="远程操控请求"):
    try:
        for w in root.winfo_children():
            try:
                if w.winfo_class() == "Toplevel" and w.title() == title:
                    return w
            except Exception:
                pass
    except Exception:
        pass
    return None


def find_button(widget, text):
    try:
        if widget.winfo_class() == "Button":
            try:
                if str(widget.cget("text")) == text:
                    return widget
            except Exception:
                pass
        for ch in widget.winfo_children():
            r = find_button(ch, text)
            if r is not None:
                return r
    except Exception:
        pass
    return None


def wait_reject_click(root, client, cmd, timeout=6.0):
    """泵事件循环并点击自绘对话框「拒绝」；返回匹配的 CMD_ERR。"""
    end = time.time() + timeout
    clicked = False
    while time.time() < end:
        try:
            root.update()
        except Exception:
            pass
        if not clicked:
            dlg = find_dialog(root)
            if dlg is not None:
                btn = find_button(dlg, "拒绝")
                if btn is not None:
                    try:
                        btn.invoke()
                        clicked = True
                    except Exception:
                        pass
        client.poll(0.02)
        while client.inbox:
            m = client.inbox.pop(0)
            d = m.get("data") or {}
            if m.get("t") == T_CMD_ERR and d.get("cmd") == cmd:
                return m
        time.sleep(0.01)
    return None


def reason_of(msg):
    return str(((msg or {}).get("data") or {}).get("reason") or "")


def detail_of(msg):
    return str(((msg or {}).get("data") or {}).get("detail") or "")


# ══════════════════════════════════════════════════════════════════════════
def main():
    global _fails
    print("=" * 64)
    print("NetLink Phase2-2 control kernel self-test")
    print("=" * 64)

    # ── Tk root（RUN 回主线程需要；无显示则退化直执行）──
    root = None
    try:
        root = tkinter.Tk()
        root.withdraw()
    except Exception as e:
        print("[WARN] no display ({}); run 将退化为主线程不可用直执行".format(e))
        root = None

    # ── 环境隔离 ──
    orig_config_path = state.CONFIG_PATH
    orig_peers = list(getattr(state, "NETLINK_PEERS", []) or [])
    orig_confirmed = list(getattr(state, "NETLINK_CONFIRMED_PEERS", []) or [])
    orig_api_key = getattr(state, "API_KEY", "")
    orig_confirm = getattr(state, "NETLINK_CONFIRM_CONTROL", True)
    orig_script_dir = getattr(state, "NETLINK_SCRIPT_DIR", "")
    orig_workflow = sys.modules.get("workflow", None)
    orig_cred = None
    try:
        orig_cred = state.cred_read(state._CRED_TARGET)
    except Exception:
        orig_cred = None
    saved = {}
    for name in ("running", "recording", "quit2", "quit3", "has_script",
                 "filename", "script_dir"):
        saved[name] = getattr(state, name, None)

    tmpdir = tempfile.mkdtemp(prefix="acrpa_ctl_test_")
    state.CONFIG_PATH = os.path.join(tmpdir, "config.json")
    state.NETLINK_PEERS = []
    state.NETLINK_CONFIRMED_PEERS = []

    hooks = FakeHooks()
    cli1 = cli2 = None
    node = None
    try:
        state.NETLINK_ENABLED = True
        state.NETLINK_PORT = TCP_PORT
        state.NETLINK_DISCOVERY_PORT = UDP_PORT
        state.NETLINK_AUTODISCOVER = False
        state.NETLINK_DEVICE_NAME = "CtlA"
        state.NETLINK_STATIC_PEERS = []
        state.NETLINK_REQUIRE_AUTH = True
        state.NETLINK_PIN_TTL = 600
        state.NETLINK_CONFIRM_CONTROL = True
        state.NETLINK_CONFIRMED_PEERS = []
        state.NETLINK_SCRIPT_DIR = ""
        nl_node.make_node_id = lambda: "ctlnode0000000001"
        ok0 = netlink.start_netlink(root)
        check("0. start node (tcp {})".format(TCP_PORT), bool(ok0))
        node = netlink.get_node()
        check("0b. netlink.get_node() 可用", node is not None)
        if node is None:
            return finish(root, node, cli1, cli2, orig_config_path, orig_peers,
                          orig_confirmed, orig_api_key, orig_confirm,
                          orig_script_dir, orig_workflow, orig_cred, saved, tmpdir)
        netlink.set_control_hooks(run=hooks.run, stop=hooks.stop)
        check("0c. 建立 ControlExecutor 并注入钩子",
              getattr(node, "control", None) is not None)

        pin = node.open_pair_window()
        check("0d. 打开配对窗口拿到 PIN",
              isinstance(pin, str) and len(pin) == 6, "pin={!r}".format(pin))

        # ── 建立两条已认证连接（控制端）──
        cli1 = connect(TCP_PORT)
        r1 = do_pair(cli1, pin, CLI1_ID, CLI1_NAME, CLI1_FP)
        check("0e. cli1 配对 AUTH_OK",
              r1 is not None and r1.get("t") == T_AUTH_OK,
              "got={}".format((r1 or {}).get("t")))
        cli2 = connect(TCP_PORT)
        r2 = do_pair(cli2, pin, CLI2_ID, CLI2_NAME, CLI2_FP)
        check("0f. cli2 配对 AUTH_OK",
              r2 is not None and r2.get("t") == T_AUTH_OK,
              "got={}".format((r2 or {}).get("t")))

        # ── 断言 1：权限门槛（observe + CMD_RUN → permission denied）──
        node.set_peer_perm(CLI1_FP, "observe")
        state.has_script = True
        state.filename = os.path.join(tmpdir, "x.xls")
        state.running = False
        state.recording = False
        drain(cli1)
        cli1.send(make_msg(T_CMD_RUN, {}))
        m1 = wait_any_cmd(cli1, T_CMD_RUN, 3.0, root)
        check("1. observe + CMD_RUN → permission denied",
              m1 is not None and m1.get("t") == T_CMD_ERR
              and "permission denied" in reason_of(m1),
              "reason={!r}".format(reason_of(m1)))
        node.set_peer_perm(CLI1_FP, "control")
        node.set_peer_perm(CLI2_FP, "control")

        # ── 断言 2：CMD_RUN 直通（确认关闭）──
        state.NETLINK_CONFIRM_CONTROL = False
        state.running = False
        state.recording = False
        state.has_script = True
        hooks.reset()
        drain(cli1)
        cli1.send(make_msg(T_CMD_RUN, {}))
        a2 = wait_any_cmd(cli1, T_CMD_RUN, 3.0, root, include_accepted=True)
        check("2a. 收到 CMD_ACK status=accepted",
              a2 is not None and a2.get("t") == T_CMD_ACK
              and (a2.get("data") or {}).get("status") == "accepted",
              "msg={}".format(a2))
        got_run = wait_until(lambda: len(hooks.run_calls) >= 1, 2.0, root, cli1)
        check("2b. fake_run <=2s 被调用 1 次",
              got_run and len(hooks.run_calls) == 1,
              "calls={}".format(len(hooks.run_calls)))
        t2 = wait_any_cmd(cli1, T_CMD_RUN, 3.0, root)
        check("2c. 收到广播终态 CMD_ACK status=done",
              t2 is not None and t2.get("t") == T_CMD_ACK
              and (t2.get("data") or {}).get("status") == "done",
              "msg={}".format(t2))

        # ── 断言 3：CMD_RUN 无脚本 ──
        state.has_script = False
        hooks.reset()
        drain(cli1)
        cli1.send(make_msg(T_CMD_RUN, {}))
        m3 = wait_any_cmd(cli1, T_CMD_RUN, 3.0, root)
        check("3a. 无脚本 → reason=='no script selected'",
              m3 is not None and reason_of(m3) == "no script selected",
              "reason={!r}".format(reason_of(m3)))
        check("3b. fake_run 未被调用", len(hooks.run_calls) == 0)
        state.has_script = True

        # ── 断言 4：CMD_RUN 运行中 ──
        state.running = True
        drain(cli1)
        cli1.send(make_msg(T_CMD_RUN, {}))
        m4 = wait_any_cmd(cli1, T_CMD_RUN, 3.0, root)
        check("4. 运行中 → reason=='already running'",
              m4 is not None and reason_of(m4) == "already running",
              "reason={!r}".format(reason_of(m4)))
        state.running = False

        # ── 断言 5：CMD_RUN 录制中 ──
        state.recording = True
        drain(cli1)
        cli1.send(make_msg(T_CMD_RUN, {}))
        m5 = wait_any_cmd(cli1, T_CMD_RUN, 3.0, root)
        check("5. 录制中 → reason=='recording in progress'",
              m5 is not None and reason_of(m5) == "recording in progress",
              "reason={!r}".format(reason_of(m5)))
        state.recording = False

        # ── 断言 6：CMD_PAUSE 未运行 ──
        state.running = False
        drain(cli1)
        cli1.send(make_msg(T_CMD_PAUSE, {}))
        m6 = wait_any_cmd(cli1, T_CMD_PAUSE, 3.0, root)
        check("6. 未运行 PAUSE → reason=='not running'",
              m6 is not None and reason_of(m6) == "not running",
              "reason={!r}".format(reason_of(m6)))

        # ── 断言 7：脚本分叉 PAUSE / RESUME ──
        set_workflow(None)          # 无工作流引擎 → 脚本模式
        state.running = True
        state.recording = False
        state.pause_event.set()
        drain(cli1)
        cli1.send(make_msg(T_CMD_PAUSE, {}))
        m7a = wait_any_cmd(cli1, T_CMD_PAUSE, 3.0, root)
        check("7a. PAUSE → CMD_ACK done 且 detail 含 mode=script",
              m7a is not None and m7a.get("t") == T_CMD_ACK
              and (m7a.get("data") or {}).get("status") == "done"
              and "mode=script" in detail_of(m7a), "msg={}".format(m7a))
        check("7b. 脚本模式 PAUSE → state.pause_event.is_set() is False",
              state.pause_event.is_set() is False)
        drain(cli1)
        cli1.send(make_msg(T_CMD_RESUME, {}))
        m7c = wait_any_cmd(cli1, T_CMD_RESUME, 3.0, root)
        check("7c. RESUME → is_set() is True",
              m7c is not None and state.pause_event.is_set() is True,
              "msg={}".format(m7c))

        # ── 断言 8：工作流分叉 ──
        fake = FakeEngine(current_step=0, stopped=False)
        set_workflow(fake)
        state.running = True
        state.pause_event.set()
        drain(cli1)
        cli1.send(make_msg(T_CMD_PAUSE, {}))
        m8a = wait_any_cmd(cli1, T_CMD_PAUSE, 3.0, root)
        check("8a. workflow PAUSE → fake.paused is True",
              fake.paused is True, "msg={}".format(m8a))
        check("8b. workflow PAUSE 未动 state.pause_event（仍为 True）",
              state.pause_event.is_set() is True)
        check("8c. workflow PAUSE detail 含 mode=workflow",
              m8a is not None and "mode=workflow" in detail_of(m8a),
              "detail={!r}".format(detail_of(m8a)))
        drain(cli1)
        cli1.send(make_msg(T_CMD_RESUME, {}))
        m8d = wait_any_cmd(cli1, T_CMD_RESUME, 3.0, root)
        check("8d. workflow RESUME → fake.resumed is True",
              fake.resumed is True, "msg={}".format(m8d))
        drain(cli1)
        cli1.send(make_msg(T_CMD_STOP, {}))
        m8e = wait_any_cmd(cli1, T_CMD_STOP, 3.0, root)
        check("8e. workflow STOP → fake.stopped_called is True",
              fake.stopped_called is True, "msg={}".format(m8e))

        # ── 断言 9：脚本模式 STOP ──
        set_workflow(None)
        state.quit2 = False
        state.running = True
        state.pause_event.set()
        hooks.reset()
        drain(cli1)
        cli1.send(make_msg(T_CMD_STOP, {}))
        m9 = wait_any_cmd(cli1, T_CMD_STOP, 3.0, root)
        check("9a. 脚本 STOP → state.quit2 is True", state.quit2 is True)
        check("9b. 脚本 STOP → state.pause_event.is_set() is True",
              state.pause_event.is_set() is True)
        check("9c. 脚本 STOP → fake_stop 被调用",
              hooks.stop_calls >= 1, "stop_calls={}".format(hooks.stop_calls))
        check("9d. 脚本 STOP detail 含 mode=script",
              m9 is not None and "mode=script" in detail_of(m9),
              "detail={!r}".format(detail_of(m9)))

        # ── 断言 10：CMD_RUN_SCRIPT 正常 ──
        sdir = os.path.join(tmpdir, "scripts")
        os.makedirs(sdir, exist_ok=True)
        demo = os.path.join(sdir, "demo.xls")
        with open(demo, "w", encoding="utf-8") as f:
            f.write("dummy")
        state.NETLINK_SCRIPT_DIR = sdir
        state.running = False
        state.recording = False
        state.has_script = False
        state.filename = None
        state.script_dir = None
        hooks.reset()
        drain(cli1)
        cli1.send(make_msg(T_CMD_RUN_SCRIPT, {"name": "demo.xls"}))
        a10 = wait_any_cmd(cli1, T_CMD_RUN_SCRIPT, 3.0, root, include_accepted=True)
        check("10a. RUN_SCRIPT 收到 accepted",
              a10 is not None and (a10.get("data") or {}).get("status") == "accepted",
              "msg={}".format(a10))
        wait_until(lambda: len(hooks.run_calls) >= 1, 2.0, root, cli1)
        check("10b. state.filename == 该绝对路径",
              state.filename == demo, "filename={!r}".format(state.filename))
        check("10c. state.script_dir == 该目录",
              state.script_dir == sdir, "script_dir={!r}".format(state.script_dir))
        check("10d. fake_run 被调用", len(hooks.run_calls) >= 1,
              "calls={}".format(len(hooks.run_calls)))

        # ── 断言 11：路径穿越 / 不存在 ──
        drain(cli1)
        cli1.send(make_msg(T_CMD_RUN_SCRIPT, {"name": "../../evil.xls"}))
        m11a = wait_any_cmd(cli1, T_CMD_RUN_SCRIPT, 3.0, root)
        check("11a. 穿越名 → reason=='invalid script name'",
              m11a is not None and reason_of(m11a) == "invalid script name",
              "reason={!r}".format(reason_of(m11a)))
        drain(cli1)
        cli1.send(make_msg(T_CMD_RUN_SCRIPT, {"name": "nope.xls"}))
        m11b = wait_any_cmd(cli1, T_CMD_RUN_SCRIPT, 3.0, root)
        check("11b. 不存在 → reason=='script not found'",
              m11b is not None and reason_of(m11b) == "script not found",
              "reason={!r}".format(reason_of(m11b)))

        # ── 断言 12：串行化（连发 RUN/PAUSE/RESUME）──
        hooks.reset()
        hooks.sleep = 0.3
        state.running = False
        state.recording = False
        state.has_script = True
        drain(cli1)
        cli1.send(make_msg(T_CMD_RUN, {}))
        cli1.send(make_msg(T_CMD_PAUSE, {}))
        cli1.send(make_msg(T_CMD_RESUME, {}))
        wait_until(lambda: len(hooks.run_calls) >= 1, 3.0, root, cli1)
        wait_until(lambda: hooks.max_active >= 1, 1.0, root, cli1)
        time.sleep(0.6)
        _pump(root, cli1)
        check("12a. 连发三条 → fake_run 只被调用 1 次",
              len(hooks.run_calls) == 1, "calls={}".format(len(hooks.run_calls)))
        check("12b. 假钩子并发计数器最大值为 1（无重入）",
              hooks.max_active == 1, "max_active={}".format(hooks.max_active))
        hooks.sleep = 0.0

        # ── 断言 13：审计日志 ──
        audit = getattr(node.control, "audit", None)
        check("13a. ControlExecutor.audit 可用", audit is not None)
        apath = ""
        if audit is not None:
            apath = audit.path
        check("13b. 审计文件存在且位于 logs 目录",
              bool(apath) and os.path.isfile(apath)
              and ("logs" in apath and "netlink_audit_" in os.path.basename(apath)),
              "path={!r}".format(apath))
        content = ""
        try:
            if apath and os.path.isfile(apath):
                with open(apath, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
        except Exception:
            content = ""
        check("13c. 审计含 cmd=CMD_RUN / result= / actor=",
              ("cmd=CMD_RUN" in content and "result=" in content
               and "actor=" in content),
              "len={}".format(len(content)))
        n_tail = 0
        try:
            n_tail = len(audit.tail(50)) if audit is not None else 0
        except Exception:
            n_tail = 0
        check("13d. audit.tail(50) 非空", n_tail > 0, "n={}".format(n_tail))
        cfg_text = ""
        try:
            if os.path.isfile(state.CONFIG_PATH):
                with open(state.CONFIG_PATH, "r", encoding="utf-8") as f:
                    cfg_text = f.read()
        except Exception:
            cfg_text = ""
        check("13e. config.json 不含审计内容（审计只写 logs 目录）",
              ("cmd=CMD_RUN" not in cfg_text) and ("actor=" not in cfg_text),
              "cfg_len={}".format(len(cfg_text)))

        # ── 断言 14：回执广播（两条已认证连接都收到）──
        state.running = False
        state.recording = False
        state.has_script = True
        hooks.reset()
        drain(cli1)
        drain(cli2)
        cli1.send(make_msg(T_CMD_RUN, {}))
        t14a = wait_any_cmd(cli1, T_CMD_RUN, 3.0, root)
        t14b = wait_any_cmd(cli2, T_CMD_RUN, 3.0, root)
        check("14a. 发起方 cli1 收到终态 CMD_ACK",
              t14a is not None and t14a.get("t") == T_CMD_ACK
              and (t14a.get("data") or {}).get("status") == "done",
              "msg={}".format(t14a))
        check("14b. 另一条已认证连接 cli2 也收到该指令终态（broadcast）",
              t14b is not None and t14b.get("t") == T_CMD_ACK
              and (t14b.get("data") or {}).get("cmd") == T_CMD_RUN,
              "msg={}".format(t14b))

        # ── 断言 15：首次操控确认弹窗 ──
        if root is None:
            warn("15. 无显示设备，确认弹窗无法自动化，跳过（WARN）")
        else:
            set_workflow(None)
            state.NETLINK_CONFIRM_CONTROL = True
            state.NETLINK_CONFIRMED_PEERS = []
            state.running = False
            state.recording = False
            state.has_script = True
            hooks.reset()
            drain(cli1)
            cli1.send(make_msg(T_CMD_RUN, {}))
            m15a = wait_reject_click(root, cli1, T_CMD_RUN, 6.0)
            r15 = reason_of(m15a)
            check("15a. 未确认设备 → 被拒绝（user rejected / confirm timeout）",
                  m15a is not None and r15 in ("user rejected", "confirm timeout"),
                  "reason={!r}".format(r15))
            check("15b. 被拒后指令未执行（fake_run 未调用）",
                  len(hooks.run_calls) == 0,
                  "calls={}".format(len(hooks.run_calls)))
            # 记住该设备后重发 → 直通执行
            state.NETLINK_CONFIRMED_PEERS = [CLI1_FP]
            hooks.reset()
            drain(cli1)
            cli1.send(make_msg(T_CMD_RUN, {}))
            m15c = wait_any_cmd(cli1, T_CMD_RUN, 4.0, root)
            check("15c. 已确认设备重发 → 直通执行（终态 done）",
                  m15c is not None and (m15c.get("data") or {}).get("status") == "done"
                  and len(hooks.run_calls) >= 1,
                  "msg={} calls={}".format(m15c, len(hooks.run_calls)))

    except Exception:
        print("[FAIL] 未捕获异常:")
        traceback.print_exc()
        _fails += 1
    finally:
        return finish(root, node, cli1, cli2, orig_config_path, orig_peers,
                      orig_confirmed, orig_api_key, orig_confirm,
                      orig_script_dir, orig_workflow, orig_cred, saved, tmpdir)


def finish(root, node, cli1, cli2, orig_config_path, orig_peers, orig_confirmed,
           orig_api_key, orig_confirm, orig_script_dir, orig_workflow,
           orig_cred, saved, tmpdir):
    global _fails
    for c in (cli1, cli2):
        try:
            if c is not None:
                c.close()
        except Exception:
            pass
    try:
        netlink.set_control_hooks(run=None, stop=None)
    except Exception:
        pass
    try:
        netlink.stop_netlink()
    except Exception:
        pass
    try:
        if node is not None:
            node.stop()
    except Exception:
        pass
    # 还原 state 与凭据库
    try:
        state.CONFIG_PATH = orig_config_path
        state.NETLINK_PEERS = orig_peers
        state.NETLINK_CONFIRMED_PEERS = orig_confirmed
        state.NETLINK_CONFIRM_CONTROL = orig_confirm
        state.NETLINK_SCRIPT_DIR = orig_script_dir
        state.API_KEY = orig_api_key
        for k, v in saved.items():
            setattr(state, k, v)
        if orig_cred:
            state.cred_write(state._CRED_TARGET, orig_cred)
        else:
            state.cred_delete(state._CRED_TARGET)
    except Exception:
        pass
    # 还原 sys.modules["workflow"]
    try:
        if orig_workflow is None:
            sys.modules.pop("workflow", None)
        else:
            sys.modules["workflow"] = orig_workflow
    except Exception:
        pass
    try:
        security.forget_peer_token(CLI1_FP)
        security.forget_peer_token(CLI2_FP)
    except Exception:
        pass
    try:
        shutil.rmtree(tmpdir, ignore_errors=True)
    except Exception:
        pass
    try:
        if root is not None:
            root.destroy()
    except Exception:
        pass

    elapsed = time.time() - T0
    print("-" * 64)
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
