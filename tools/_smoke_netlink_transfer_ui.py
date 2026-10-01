# -*- coding: utf-8 -*-
"""NetLink Phase 3-1b 脚本分发 UI 集成自测 —— 全自动、无弹窗、总时长 < 60s。

运行:   python tools/_smoke_netlink_transfer_ui.py
退出码: 0 = 全部断言通过（含 [SKIP] no display，允许 [WARN]）；1 = 存在 [FAIL]

设计要点:
  * 真实 Tk root (withdraw) + **同进程双节点**：
      A = 被控端（直接构造 NetLinkNode，tcp 19985，独立 NetBus；
                    NETLINK_REQUIRE_AUTH=True / NETLINK_CONFIRM_CONTROL=False，
                    关闭自动发现）；
      B = 控制端（走 netlink.start_netlink 门面单例，tcp 19995，静态对端→A），
          「设备互联」窗口绑定到 B；
  * 配对：A.open_pair_window() 取 PIN → B.register_target(pin) → A.set_peer_perm(B_fp, "script")；
  * **文件选择与自绘对话框的注入方式**：`_on_push_script` 的交互被拆成两个可替换方法
    —— `win._ask_open_script()`（返回本地路径）与
    `win._ask_push_params(local, base)`（返回 (remote_name, run)）；
    测试直接替换这两个方法注入结果，避免真实文件框/自绘窗口阻塞（见文末偏离说明）。
  * **二次确认的绕过方式**：`_on_rs_run` 走既有 `win._confirm_action(title, msg)`，
    测试以 `win._confirm_action = lambda *a, **k: True` 替换（与 _smoke_netlink_control_ui 同法）。
  * 仅用 root.update() 驱动事件循环（不进入 mainloop），同时驱动窗口 _pump 与 A 侧控制泵；
  * 隔离: state.CONFIG_PATH 指向临时文件；NETLINK_SCRIPT_DIR 指向临时目录；
    备份/还原 NETLINK_PEERS / NETLINK_CONFIRMED_PEERS / API_KEY / NETLINK_SCRIPT_DIR /
    running / quit2 / recording / has_script / filename / pause_event；清理凭据库 token；
    补丁 nl_node.make_node_id、sys.modules["workflow"]；
  * 端口 199xx 段（19985/19986、19995/19996），用完即释放。

断言:
  1  配对后(observe) [推脚本]/[📂 远端脚本] 均 disabled
  2  set_peer_perm(script) 后 ≤2s 两者均可用
  3  推送 200KB 假 .xls → A 落盘且 sha256 一致；B「传输」行 传输中 → 成功
  4  进度列出现一次 sent/total (百分比%)
  5  远端脚本列表：对话框存在，含 脚本目录/已接收 两个文件且位置正确
  6  远程运行 received 条目 → fake_run 被调用且 state.filename 指向该文件
  7  失败路径：perm 改回 observe → 按钮 disabled（提示）；直推不存在文件 → 传输行 失败：（红）
  8  收尾：两节点 stop + close_window → nl-* 线程 ≤5s 收敛为 0
"""
import hashlib
import os
import re
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
from netlink import security, transfer
from netlink.node import TOPIC_AUTH
from netlink.protocol import T_CMD_RUN_SCRIPT  # noqa: F401 (存在性)

# ── 端口（199xx 段）──
A_TCP, A_UDP = 19985, 19986
B_TCP, B_UDP = 19995, 19996
TOTAL_BUDGET = 60.0

_fails = 0
_warns = 0
T0 = time.time()

_PROG_RE = re.compile(r"^\d+/\d+ \(\d+%\)$")


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
    """在给定秒数内反复 root.update() 驱动 _pump 与 A 侧控制泵。"""
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


def _btn_state(btn):
    try:
        return str(btn.cget("state"))
    except Exception:
        return "?"


def _select(win, nid):
    """在设备表选中 node_id 对应行；成功返回 True。"""
    try:
        item = win._items.get(nid)
        if item is None:
            return False
        win.tv.selection_set(item)
        win._on_select()
        return True
    except Exception:
        return False


def _xfer_row(win, name):
    """「传输」列表中文件列 == name 的行 values；不存在返回 None。"""
    try:
        for iid in win.tv_xfer.get_children():
            vals = win.tv_xfer.item(iid).get("values") or ()
            if len(vals) >= 4 and str(vals[1]) == name:
                return tuple(vals)
    except Exception:
        pass
    return None


def _xfer_tags(win, name):
    try:
        for iid in win.tv_xfer.get_children():
            vals = win.tv_xfer.item(iid).get("values") or ()
            if len(vals) >= 4 and str(vals[1]) == name:
                return tuple(win.tv_xfer.item(iid).get("tags") or ())
    except Exception:
        pass
    return ()


def _rs_rows(win):
    """远端脚本对话框的行 [{name,size,mtime,place,iid}]。"""
    out = []
    tv = win._rs_tree
    if tv is None:
        return out
    try:
        for iid in tv.get_children():
            vals = tv.item(iid).get("values") or ()
            if len(vals) >= 4:
                out.append({"name": str(vals[0]), "size": str(vals[1]),
                            "mtime": str(vals[2]), "place": str(vals[3]),
                            "iid": iid})
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
    print("NetLink Phase 3-1b script-distribution UI integration smoke test")
    print("=" * 66)

    # ── 环境隔离：备份 ──
    orig = {}
    for name in ("CONFIG_PATH", "NETLINK_PEERS", "NETLINK_CONFIRMED_PEERS",
                 "API_KEY", "NETLINK_SCRIPT_DIR", "NETLINK_REQUIRE_AUTH",
                 "NETLINK_CONFIRM_CONTROL", "running", "quit2", "recording",
                 "has_script", "filename", "pause_event", "script_dir"):
        orig[name] = getattr(state, name, None)
    orig_make_node_id = nl_node.make_node_id
    orig_workflow = sys.modules.get("workflow", None)
    try:
        orig_cred = state.cred_read(state._CRED_TARGET)
    except Exception:
        orig_cred = None

    tmpdir = tempfile.mkdtemp(prefix="acrpa_xferui_")
    scriptdir = os.path.join(tmpdir, "scripts")
    os.makedirs(os.path.join(scriptdir, "received"), exist_ok=True)
    localdir = os.path.join(tmpdir, "local")
    os.makedirs(localdir, exist_ok=True)

    state.CONFIG_PATH = os.path.join(tmpdir, "config.json")
    state.NETLINK_PEERS = []
    state.NETLINK_CONFIRMED_PEERS = []
    state.NETLINK_SCRIPT_DIR = scriptdir

    id_seq = {"n": 0}
    run_tag = uuid.uuid4().hex[:6]

    def _mk_id():
        id_seq["n"] += 1
        return "nlf{}{}".format(run_tag, id_seq["n"])

    nl_node.make_node_id = _mk_id
    sys.modules["workflow"] = types.SimpleNamespace(workflow_engine=None)

    hooks = {"run_calls": 0, "filename": None}

    def fake_run(loops=None):
        hooks["run_calls"] += 1
        try:
            hooks["filename"] = state.filename
        except Exception:
            pass

    def fake_stop():
        pass

    a = None
    win = None
    b_fp = ""
    fps = []
    try:
        # ── A（被控端）：直接构造节点 ──
        state.NETLINK_ENABLED = True
        state.NETLINK_PORT = A_TCP
        state.NETLINK_DISCOVERY_PORT = A_UDP
        state.NETLINK_AUTODISCOVER = False
        state.NETLINK_DEVICE_NAME = "XferUIA"
        state.NETLINK_STATIC_PEERS = []
        state.NETLINK_REQUIRE_AUTH = True
        state.NETLINK_CONFIRM_CONTROL = False
        state.NETLINK_CONFIRMED_PEERS = []
        state.running = False
        state.quit2 = False
        state.recording = False
        state.has_script = False

        a = nl_node.NetLinkNode(root=root, bus=NetBus())
        ok_a = a.start()
        check("0. A(被控端) start (tcp {})".format(A_TCP), bool(ok_a))
        a.set_control_hooks(run=fake_run, stop=fake_stop)

        # ── B（控制端）：门面单例 + 窗口 ──
        state.NETLINK_PORT = B_TCP
        state.NETLINK_DISCOVERY_PORT = B_UDP
        state.NETLINK_DEVICE_NAME = "XferUIB"
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

        # 清理可能残留的凭据（Windows 凭据库为全局）
        a_self_fp = str(getattr(a, "_self_fp", "") or "")
        b_self_fp = str(getattr(b, "_self_fp", "") or "")
        for _fp in (a_self_fp, b_self_fp):
            if _fp:
                fps.append(_fp)
                try:
                    security.forget_peer_token(_fp)
                except Exception:
                    pass

        # ── 配对 ──
        pin = a.open_pair_window()
        check("0e. A.open_pair_window() 返回 6 位 PIN",
              isinstance(pin, str) and len(pin) == 6 and pin.isdigit(),
              "pin={!r}".format(pin))
        b.register_target("127.0.0.1", A_TCP, pin)
        a_node_id = str(a.info.get("node_id") or "")
        paired = wait_until(lambda: (len(a.list_peers() or []) >= 1
                                     and a_node_id in win._authed), 8.0, root)
        check("0f. 配对完成（A 白名单有 B / B 窗口 _authed 有 A）",
              paired, "peers={} authed={}".format(len(a.list_peers() or []),
                                                  list(win._authed.keys())))
        if not paired:
            raise RuntimeError("配对未完成，无法继续")
        b_fp = str((a.list_peers() or [{}])[0].get("fingerprint") or "")

        # 模拟「发现到 A 的真实 node_id」（生产由 UDP 广播提供；本测试关闭了自动发现）
        b._on_peer({"node_id": a_node_id, "name": "XferUIA", "host": "127.0.0.1",
                    "port": A_TCP, "version": "", "online": True})
        wait_until(lambda: win._items.get(a_node_id) is not None, 2.0, root)

        # ══════════════ 1. 配对后（observe）→ 两按钮 disabled ══════════════
        _select(win, a_node_id)
        spin(root, 0.6)
        s1 = (_btn_state(win.btn_script) == "disabled"
              and _btn_state(win.btn_remote_scripts) == "disabled")
        check("1. 配对后(perm=observe) [推脚本]/[📂 远端脚本] 均 disabled",
              s1, "script={} rs={}".format(_btn_state(win.btn_script),
                                           _btn_state(win.btn_remote_scripts)))

        # ══════════════ 2. 授权 script → ≤2s 两按钮可用 ══════════════
        a.set_peer_perm(b_fp, "script")
        ready = wait_until(lambda: (_btn_state(win.btn_script) == "normal"
                                    and _btn_state(win.btn_remote_scripts) == "normal"),
                           2.0, root)
        check("2. A.set_peer_perm(script) → ≤2s [推脚本]/[📂 远端脚本] 均可用",
              ready, "script={} rs={}".format(_btn_state(win.btn_script),
                                              _btn_state(win.btn_remote_scripts)))

        # 注入: 进度列插桩（记录写入 tree 的 prog 文本，供断言 4）
        prog_seen = []
        orig_upsert_row = win._xfer_upsert_row

        def _spy_upsert_row(tid, dev, name, stxt, tag, prog=None):
            try:
                if prog:
                    prog_seen.append(str(prog))
            except Exception:
                pass
            return orig_upsert_row(tid, dev, name, stxt, tag, prog)

        win._xfer_upsert_row = _spy_upsert_row

        # ══════════════ 3. 推送 200KB 假 .xls（注入交互）══════════════
        data200 = os.urandom(200 * 1024)
        local200 = os.path.join(localdir, "pushed.xls")
        with open(local200, "wb") as f:
            f.write(data200)
        sha200 = hashlib.sha256(data200).hexdigest()
        # 注入: 文件选择 + 自绘对话框（run=False，避免连带自动运行影响时序）
        win._ask_open_script = lambda: local200
        win._ask_push_params = lambda local, base: ("pushed.xls", False)
        _select(win, a_node_id)
        spin(root, 0.3)
        win.btn_script.invoke()
        dest = os.path.join(transfer.recv_dir(), "pushed.xls")
        landed = wait_until(
            lambda: os.path.isfile(dest) and transfer.sha256_file(dest) == sha200,
            8.0, root)
        check("3a. A recv_dir/pushed.xls 落盘且 sha256 与源文件一致",
              landed, "dest_sha={}".format(transfer.sha256_file(dest)))
        # 传输列表：该行出现并 成功
        ok3b = wait_until(
            lambda: (_xfer_row(win, "pushed.xls") is not None
                     and str((_xfer_row(win, "pushed.xls") or ("", "", "", ""))[3]).startswith("成功")),
            5.0, root)
        row3 = _xfer_row(win, "pushed.xls")
        check("3b. B「传输」列表出现该行且状态由 传输中 → 成功",
              ok3b, "row={}".format(row3))
        check("3c. 底部状态行摘要含「推送 pushed.xls 成功」",
              "推送 pushed.xls 成功" in str(win._xfer_summary),
              "summary={!r}".format(win._xfer_summary))

        # ══════════════ 4. 进度列出现 sent/total (百分比%) ══════════════
        matched = [p for p in list(prog_seen) if _PROG_RE.match(p)]
        check("4. sending 阶段进度列至少出现一次 n/total (%) 形式",
              len(matched) >= 1, "prog_seen={}".format(prog_seen[:8]))

        # ══════════════ 5. 远端脚本列表（脚本目录 + 已接收）══════════════
        with open(os.path.join(scriptdir, "root1.xls"), "wb") as f:
            f.write(b"root-payload")
        with open(os.path.join(scriptdir, "received", "recv1.xls"), "wb") as f:
            f.write(b"recv-payload")
        sent_list = False
        try:
            sent_list = bool(netlink.list_remote_scripts(a_node_id))
        except Exception:
            sent_list = False
        got5 = wait_until(lambda: (win._rs_dlg is not None
                                   and win._rs_tree is not None
                                   and len(_rs_rows(win)) >= 2), 5.0, root)
        rows5 = dict((r["name"], r["place"]) for r in _rs_rows(win))
        check("5a. 远端脚本对话框存在且请求发送成功",
              sent_list and got5, "sent={} rows={}".format(sent_list,
                                                           list(rows5.items())))
        check("5b. 含 root1.xls(脚本目录) 与 received/recv1.xls(已接收)",
              rows5.get("root1.xls") == "脚本目录"
              and rows5.get("received/recv1.xls") == "已接收",
              "rows={}".format(list(rows5.items())))

        # ══════════════ 6. 远程运行 received 条目 ══════════════
        target_iid = None
        for r in _rs_rows(win):
            if r["name"] == "received/recv1.xls":
                target_iid = r["iid"]
        win._confirm_action = lambda *args, **kwargs: True   # 绕过二次确认
        ok6a = False
        if target_iid is not None:
            try:
                win._rs_tree.selection_set(target_iid)
                win._rs_btn_run.invoke()
                ok6a = True
            except Exception:
                ok6a = False
        run_ok = wait_until(lambda: hooks["run_calls"] >= 1, 5.0, root)
        exp = os.path.abspath(os.path.join(scriptdir, "received", "recv1.xls"))
        fn_ok = (hooks["filename"] is not None
                 and os.path.abspath(str(hooks["filename"])) == exp)
        check("6. 对话框内选中 received 条目点[▶远程运行选中] → fake_run 被调用且"
              " state.filename 指向该文件",
              ok6a and run_ok and fn_ok,
              "sel={} calls={} filename={!r} expect={!r}".format(
                  ok6a, hooks["run_calls"], hooks["filename"], exp))

        # ══════════════ 7. 失败路径 ══════════════
        # 7a: perm 改回 observe → 按钮 disabled（状态行/按钮给出提示）
        a.set_peer_perm(b_fp, "observe")
        rev = wait_until(lambda: (_btn_state(win.btn_script) == "disabled"
                                  and _btn_state(win.btn_remote_scripts) == "disabled"),
                         2.0, root)
        check("7a. perm 改回 observe → [推脚本]/[📂 远端脚本] 重新 disabled",
              rev, "script={} rs={}".format(_btn_state(win.btn_script),
                                            _btn_state(win.btn_remote_scripts)))
        # 7b: 直推「不存在的本地文件」→ 传输行 失败：（红）
        tid7 = None
        try:
            tid7 = netlink.push_script(a_node_id,
                                       os.path.join(localdir, "nope.xls"),
                                       "nope.xls")
        except Exception:
            tid7 = None
        fail_ok = wait_until(
            lambda: (_xfer_row(win, "nope.xls") is not None
                     and str((_xfer_row(win, "nope.xls") or ("", "", "", ""))[3]).startswith("失败")),
            5.0, root)
        row7 = _xfer_row(win, "nope.xls")
        tags7 = _xfer_tags(win, "nope.xls")
        check("7b. 失败传输在「传输」列表显示 失败：…（红：xfer_fail）",
              bool(tid7) and fail_ok and ("xfer_fail" in tags7),
              "tid={} row={} tags={}".format(tid7, row7, tags7))

    except Exception:
        print("[FAIL] unexpected exception:")
        traceback.print_exc()
        _fails += 1
    finally:
        return finish(root, a, win, orig, orig_make_node_id, orig_workflow,
                      orig_cred, tmpdir, fps)


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

    # ══════════════ 8. nl-* 线程收敛 ══════════════
    t8 = time.time()
    converged = wait_until(lambda: len(_nl_threads()) == 0, 5.0, root)
    dt8 = time.time() - t8
    if converged:
        check("8. 两节点 stop 后 nl-* 线程收敛为 0 (实测 {:.1f}s)".format(dt8), True)
    else:
        warn("8. nl-* 线程 5s 内未收敛为 0: {}".format(_nl_threads()))

    # ── 还原 state / 凭据库 / 模块补丁 ──
    try:
        for k, v in orig.items():
            if k == "pause_event" and v is None:
                continue
            try:
                setattr(state, k, v)
            except Exception:
                pass
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
        print("PASS (all assertions OK; warns={})".format(_warns))
        return 0
    print("FAIL ({} assertion(s) failed; warns={})".format(_fails, _warns))
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
