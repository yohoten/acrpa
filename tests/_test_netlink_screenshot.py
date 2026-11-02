# -*- coding: utf-8 -*-
"""NetLink Phase 4-1 远程截图专项自测 —— 全自动、不弹窗、总时长 < 60s。

运行:  python tools/_test_netlink_screenshot.py
退出码: 0 = 全部断言通过（允许 [WARN]）；1 = 存在 [FAIL]

要点:
  * screen.capture() 直接调用（桌面会话/Pillow 缺失 → [WARN]，不假通过）；
  * max_width 夹取与生效 / 节流（MIN_INTERVAL）行为；
  * 双节点 A(被控端, tcp 19983)+C(控制端, tcp 19993, 静态对端→A)：
      先 perm=observe → 拒绝；改 control → 正常链路 + 审计；
  * UI 门控（[📷截图] 随权限变化）与截图查看窗口（PhotoImage 保活 / 降级路径）；
  * 连接缺失 → request_screenshot 返回 False；
  * 收尾：两节点停止 + close_window + nl-* 线程 ≤5s 收敛为 0。
  * 不写项目 config.json：state.CONFIG_PATH 指向临时文件，测后还原；
  * 端口 199xx 段，用完释放；凭据库 token 前后清理。

断言:
  1  capture() 直调         2  夹取与生效          3  节流
  4  权限门槛               5  正常链路            6  审计
  7  UI 门控                8  UI 查看窗口         9  连接缺失          10 线程收敛
"""
import base64
import io
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import traceback

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_SRC = os.path.join(_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

try:
    import tkinter  # noqa: F401 —— 提前加载（utils 顶层 import tkinter；headless 时忽略）
except Exception:
    tkinter = None

import state
import netlink
import netlink.node as nl_node
from netlink.bus import NetBus
from netlink.node import NetLinkNode, TOPIC_CMD_RESULT, TOPIC_SCREENSHOT
from netlink import security, screen, transfer
from netlink.protocol import (
    make_msg, frame_message, FrameReader, parse_msg,
    T_HELLO, T_AUTH, T_AUTH_OK, T_AUTH_FAIL, T_AUTH_CHALLENGE,
    T_CMD_ERR, T_SCREENSHOT_REQ, T_SCREENSHOT_DAT,
)

# ── 端口（199xx 段）──
A_TCP, A_UDP = 19983, 19984
C_TCP, C_UDP = 19993, 19994
TOTAL_BUDGET = 60.0

_ENV_UNSUPPORTED = ("pillow unavailable", "grab failed")

_fails = 0
_warns = 0
_skips = 0
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


def skip(msg):
    global _skips
    _skips += 1
    print("[SKIP] " + msg)


def wait_until(fn, timeout):
    """带 deadline 的轮询；返回 (是否满足, 实测耗时秒)。"""
    t = time.time()
    end = t + float(timeout)
    while time.time() < end:
        try:
            if fn():
                return True, time.time() - t
        except Exception:
            pass
        time.sleep(0.02)
    return False, time.time() - t


def _iter_widgets(widget):
    """递归遍历控件树。"""
    out = []
    try:
        for w in widget.winfo_children():
            out.append(w)
            out.extend(_iter_widgets(w))
    except Exception:
        pass
    return out


def _make_tiny_jpeg_b64():
    """用 PIL 造一张 4x4 JPEG（供无桌面会话时兜底生成 UI 事件载荷）。失败返回 ""。"""
    try:
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (4, 4), (10, 20, 30)).save(buf, format="JPEG", quality=60)
        return base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        return ""


# ══════════════════════════════════════════════════════════════════════════
def main():
    global _fails
    print("=" * 66)
    print("NetLink Phase 4-1 remote screenshot self-test")
    print("=" * 66)

    # ── 环境隔离：备份 ──
    saved = {}
    for name in ("CONFIG_PATH", "NETLINK_PEERS", "NETLINK_REQUIRE_AUTH",
                 "NETLINK_SCRIPT_DIR", "NETLINK_CONFIRM_CONTROL",
                 "NETLINK_CONFIRMED_PEERS", "API_KEY", "running", "quit2",
                 "quit3", "recording", "has_script", "filename", "script_dir",
                 "NETLINK_ENABLED"):
        saved[name] = getattr(state, name, None)
    orig_cred = None
    try:
        orig_cred = state.cred_read(state._CRED_TARGET)
    except Exception:
        orig_cred = None

    tmpdir = tempfile.mkdtemp(prefix="acrpa_shot_test_")
    state.CONFIG_PATH = os.path.join(tmpdir, "config.json")
    state.NETLINK_PEERS = []
    state.NETLINK_CONFIRMED_PEERS = []
    state.NETLINK_REQUIRE_AUTH = True
    state.NETLINK_SCRIPT_DIR = os.path.join(tmpdir, "scripts")
    state.NETLINK_CONFIRM_CONTROL = False
    state.running = False
    state.recording = False

    orig_make_node_id = nl_node.make_node_id
    run_tag = os.urandom(4).hex()
    seq = {"n": 0}

    def _mk_id():
        seq["n"] += 1
        return "p4{}{}".format(run_tag, seq["n"])

    nl_node.make_node_id = _mk_id

    a = None
    c = None
    root = None
    win = None
    forget_fps = []
    lock = threading.Lock()
    cmd_events = []
    shot_events = []
    a_node_id = ""

    def snap(lst):
        with lock:
            return list(lst)

    try:
        # ══════════════════ 1. capture() 直调 ══════════════════
        r1 = screen.capture()
        if r1.get("ok"):
            raw = b""
            decoded_ok = False
            try:
                raw = base64.b64decode(r1.get("data_b64") or "")
                from PIL import Image
                im = Image.open(io.BytesIO(raw))
                im.load()
                decoded_ok = True
            except Exception:
                decoded_ok = False
            check("1a. capture() ok=True, format=jpeg, width<=640, height>0, bytes>0",
                  r1.get("format") == "jpeg" and int(r1.get("width") or 0) <= 640
                  and int(r1.get("height") or 0) > 0 and int(r1.get("bytes") or 0) > 0,
                  "r={}".format({k: r1.get(k) for k in ("ok", "width", "height",
                                                        "bytes", "format")}))
            check("1b. data_b64 可 base64 解码且可被 PIL.Image.open 打开",
                  bool(r1.get("data_b64")) and len(raw) == int(r1.get("bytes") or -1)
                  and decoded_ok,
                  "raw_len={} bytes={}".format(len(raw), r1.get("bytes")))
        else:
            err = str(r1.get("error") or "")
            if err in _ENV_UNSUPPORTED:
                warn("1. capture() 环境不支持：error={!r}（记 WARN，不假通过）".format(err))
            else:
                check("1. capture() 返回未知失败（非环境问题，视为实现错误）", False,
                      "error={!r} result={}".format(err, r1))

        # ══════════════════ 2. max_width 夹取与生效 ══════════════════
        time.sleep(screen.MIN_INTERVAL + 0.25)
        r2a = screen.capture(max_width=200)
        time.sleep(screen.MIN_INTERVAL + 0.25)
        r2b = screen.capture(max_width=99999)
        time.sleep(screen.MIN_INTERVAL + 0.25)
        r2c = screen.capture(quality=999)
        check("2a. capture(max_width=200) → width<=200",
              int(r2a.get("width") or 0) <= 200,
              "width={} error={!r}".format(r2a.get("width"), r2a.get("error")))
        check("2b. capture(max_width=99999) → width<=1280（夹取上限）",
              int(r2b.get("width") or 0) <= screen.MAX_WIDTH_LIMIT,
              "width={} error={!r}".format(r2b.get("width"), r2b.get("error")))
        check("2c. capture(quality=999) 不报错（返回 dict，非 rate limited）",
              isinstance(r2c, dict) and r2c.get("error") != "rate limited",
              "res={}".format(r2c))

        # ══════════════════ 3. 节流 ══════════════════
        time.sleep(screen.MIN_INTERVAL + 0.25)
        _first = screen.capture()
        _second = screen.capture()
        check("3a. 连续两次 capture() → 第二次 error == 'rate limited'",
              (not _second.get("ok")) and _second.get("error") == "rate limited",
              "first_ok={} second={!r}".format(_first.get("ok"), _second.get("error")))
        time.sleep(screen.MIN_INTERVAL + 0.2)
        _third = screen.capture()
        check("3b. sleep(MIN_INTERVAL+0.2) 后再调 → 不再 rate limited",
              _third.get("error") != "rate limited",
              "third={!r}".format(_third.get("error")))

        # ══════════════════ 启动节点 + 配对 ══════════════════
        state.NETLINK_ENABLED = True
        state.NETLINK_PORT = A_TCP
        state.NETLINK_DISCOVERY_PORT = A_UDP
        state.NETLINK_AUTODISCOVER = False
        state.NETLINK_DEVICE_NAME = "ShotA"
        state.NETLINK_STATIC_PEERS = []
        state.NETLINK_REQUIRE_AUTH = True

        a = NetLinkNode(root=None, bus=NetBus())
        ok_a = a.start()
        check("0. 被控端 A start (tcp {})".format(A_TCP), bool(ok_a))

        state.NETLINK_PORT = C_TCP
        state.NETLINK_DISCOVERY_PORT = C_UDP
        state.NETLINK_DEVICE_NAME = "ShotC"
        state.NETLINK_STATIC_PEERS = [["127.0.0.1", A_TCP]]

        c = NetLinkNode(root=None, bus=NetBus())
        ok_c = c.start()
        check("0b. 控制端 C start (tcp {}, 静态对端→A)".format(C_TCP), bool(ok_c))

        if not ok_a or not ok_c:
            print("[FAIL] 节点未就绪，终止")
            return finish(tmpdir, a, c, root, win, saved, orig_make_node_id,
                          orig_cred, forget_fps)

        # 让门面指向控制端 C（供 netlink.request_screenshot / UI 使用）
        netlink._bus = c.bus
        netlink._node = c

        def on_cmd(topic, payload):
            with lock:
                cmd_events.append(dict(payload) if isinstance(payload, dict) else {})

        def on_shot(topic, payload):
            with lock:
                shot_events.append(dict(payload) if isinstance(payload, dict) else {})

        c.bus.subscribe(TOPIC_CMD_RESULT, on_cmd)
        c.bus.subscribe(TOPIC_SCREENSHOT, on_shot)

        for _fp in (str(getattr(a, "_self_fp", "") or ""),
                    str(getattr(c, "_self_fp", "") or "")):
            if _fp:
                forget_fps.append(_fp)
                try:
                    security.forget_peer_token(_fp)
                except Exception:
                    pass

        pin = a.open_pair_window()
        c.register_target("127.0.0.1", A_TCP, pin)
        paired, dtp = wait_until(lambda: len(a.list_peers() or []) >= 1, 8.0)
        check("0c. 配对完成（A 白名单出现 C, {:.2f}s）".format(dtp), paired)
        if not paired:
            print("[FAIL] 配对未完成，终止")
            return finish(tmpdir, a, c, root, win, saved, orig_make_node_id,
                          orig_cred, forget_fps)

        c_fp = str((a.list_peers() or [{}])[0].get("fingerprint") or "")
        a_node_id = str(a.info.get("node_id") or "")
        a.set_peer_perm(c_fp, "observe")
        wait_until(lambda: transfer.find_conn(c, a_node_id) is not None, 3.0)

        # ══════════════════ 4. 权限门槛（perm=observe → 拒绝）══════════════════
        with lock:
            cmd_events[:] = []
        sent4 = False
        try:
            sent4 = bool(netlink.request_screenshot(a_node_id))
        except Exception:
            sent4 = False
        check("4a. perm=observe 时 C.request_screenshot 发出（连接存在）", sent4)

        def _denied():
            for e in snap(cmd_events):
                d = e.get("data") or {}
                if "permission denied" in str(d.get("reason") or ""):
                    return True
            return False

        ok4, dt4 = wait_until(_denied, 3.0)
        reason4 = ""
        for e in snap(cmd_events):
            d = e.get("data") or {}
            if "permission denied" in str(d.get("reason") or ""):
                reason4 = str(d.get("reason") or "")
        check("4b. observe → C 收到 CMD_ERR reason 含 'permission denied'（{:.2f}s）".format(dt4),
              ok4, "reason={!r}".format(reason4))

        audit_before = "\n".join((a.control.audit.tail(200) if a.control.audit else []) or [])
        check("4c. A 未写成功审计（无 cmd=SCREENSHOT_REQ result=ok）",
              not ("cmd=SCREENSHOT_REQ" in audit_before
                   and "result=ok" in audit_before),
              "audit_len={}".format(len(audit_before)))

        # ══════════════════ 5. 正常链路（perm=control）══════════════════
        a.set_peer_perm(c_fp, "control")
        time.sleep(screen.MIN_INTERVAL + 0.2)
        with lock:
            shot_events[:] = []
        t5 = time.time()
        sent5 = bool(netlink.request_screenshot(a_node_id))
        ok5, dt5 = wait_until(
            lambda: any(bool(m.get("ok")) and str(m.get("data_b64") or "")
                        for m in snap(shot_events)), 3.0)
        elapsed5 = time.time() - t5
        got5 = None
        for m in snap(shot_events):
            if bool(m.get("ok")) and str(m.get("data_b64") or ""):
                got5 = m
                break
        check("5a. control → C.request_screenshot 发出", sent5)
        check("5b. ≤3s C 的 TOPIC_SCREENSHOT 收到 ok=True（实测 {:.2f}s）".format(elapsed5),
              ok5 and elapsed5 <= 3.0, "elapsed={:.2f}s".format(elapsed5))
        check("5c. 载荷含 data_b64/width/height",
              bool(got5) and str(got5.get("data_b64") or "")
              and int(got5.get("width") or 0) > 0 and int(got5.get("height") or 0) > 0,
              "payload_keys={}".format(list((got5 or {}).keys())))

        # ══════════════════ 6. 审计 ══════════════════
        audit_text = "\n".join((a.control.audit.tail(200) if a.control.audit else []) or [])
        check("6. A 审计含 cmd=SCREENSHOT_REQ 且 result=ok",
              ("cmd=SCREENSHOT_REQ" in audit_text) and ("result=ok" in audit_text),
              "len={}".format(len(audit_text)))

        # ══════════════════ 7/8. UI 门控 + 查看窗口 ══════════════════
        ui_ok = False
        if tkinter is None:
            skip("7/8. no display (tkinter import failed)")
        else:
            try:
                root = tkinter.Tk()
                root.withdraw()
                ui_ok = True
            except Exception as e:
                print("[SKIP] no display ({})".format(e))
                skip("7/8. no display")

        if ui_ok:
            import netlink_window
            win = netlink_window.open_netlink_window(root)
            if win is None:
                skip("7/8. 无法创建 netlink_window")
            else:
                win._running = True
                win._sel = a_node_id
                win._peers[a_node_id] = {"name": "ShotA", "host": "127.0.0.1",
                                         "port": A_TCP, "version": "", "online": True}
                win._authed[a_node_id] = "observe"
                win._update_control_buttons()
                st_obs = str(win.btn_shot.cget("state"))
                check("7a. observe 时 [📷截图] disabled", st_obs == "disabled",
                      "state={!r}".format(st_obs))

                win._authed[a_node_id] = "control"

                def _enabled():
                    try:
                        for _ in range(3):
                            root.update()
                    except Exception:
                        pass
                    return str(win.btn_shot.cget("state")) == "normal"

                ok7, dt7 = wait_until(_enabled, 2.0)
                check("7b. 改 control 后 ≤2s [📷截图] 变可用（{:.2f}s）".format(dt7), ok7,
                      "state={!r}".format(str(win.btn_shot.cget("state"))))

                # ── 8. 查看窗口 ──
                time.sleep(screen.MIN_INTERVAL + 0.25)
                r8 = screen.capture()
                data8 = str(r8.get("data_b64") or "") if r8.get("ok") else ""
                w8 = int(r8.get("width") or 0) if r8.get("ok") else 0
                h8 = int(r8.get("height") or 0) if r8.get("ok") else 0
                b8 = int(r8.get("bytes") or 0) if r8.get("ok") else 0
                if not data8:
                    data8 = _make_tiny_jpeg_b64()
                    w8 = w8 or 4
                    h8 = h8 or 4
                    b8 = b8 or 0
                if not data8:
                    skip("8. 无法生成测试用 JPEG（无 Pillow/无桌面会话）")
                else:
                    payload8 = {"node_id": a_node_id, "ok": True,
                                "ts": int(time.time()), "width": w8, "height": h8,
                                "format": "jpeg", "data_b64": data8, "bytes": b8,
                                "error": ""}
                    try:
                        c.bus.publish(netlink.get_screenshot_topic(), payload8)
                    except Exception:
                        pass

                    def _toplevel_ready():
                        try:
                            for _ in range(3):
                                root.update()
                        except Exception:
                            pass
                        return win._shot_dlg_alive() and win._shot_dlg is not None

                    ok8, dt8 = wait_until(_toplevel_ready, 2.0)
                    # 递归枚举控件树中的 Toplevel，断言标题含 "远程截图"
                    # （截图窗口是设备互联窗口的子窗口，不在 root 直接子级）
                    titled = None
                    try:
                        for w in _iter_widgets(root):
                            if isinstance(w, tkinter.Toplevel):
                                if "远程截图" in (w.title() or ""):
                                    titled = w
                                    break
                    except Exception:
                        titled = None
                    check("8a. 存在标题含 '远程截图' 的 Toplevel（{:.2f}s）".format(dt8),
                          titled is not None and ok8,
                          "titled={} alive={}".format(titled is not None,
                                                      win._shot_dlg_alive()))

                    img_set = False
                    path_text = False
                    target = titled if titled is not None else win._shot_dlg
                    for lbl in _iter_widgets(target) if target is not None else []:
                        try:
                            if lbl.cget("image"):
                                img_set = True
                            t = str(lbl.cget("text") or "")
                            if ("已保存到" in t) or ("acrpa_shot_" in t):
                                path_text = True
                        except Exception:
                            pass
                    check("8b. 窗口内含已设置 image 的 Label 或降级路径文本",
                          img_set or path_text,
                          "img_set={} path_text={}".format(img_set, path_text))
                    if img_set:
                        check("8c. PhotoImage 引用已保活（win._shot_img 非空）",
                              win._shot_img is not None)

        # ══════════════════ 9. 连接缺失 ══════════════════
        miss = True
        try:
            miss = (netlink.request_screenshot("不存在的peer") is False)
        except Exception:
            miss = False
        check("9. request_screenshot('不存在的peer') 返回 False", miss)

    except Exception:
        print("[FAIL] 未捕获异常:")
        traceback.print_exc()
        _fails += 1
    finally:
        return finish(tmpdir, a, c, root, win, saved, orig_make_node_id,
                      orig_cred, forget_fps)


def finish(tmpdir, a, c, root, win, saved, orig_make_node_id, orig_cred, forget_fps):
    global _fails
    # ── 收尾：关窗 + 停节点（顺序：先 UI 后节点）──
    try:
        if win is not None and root is not None:
            try:
                root.update()
            except Exception:
                pass
    except Exception:
        pass
    try:
        import netlink_window
        netlink_window.close_window()
    except Exception:
        pass
    for n in (c, a):
        if n is not None:
            try:
                n.stop()
            except Exception:
                pass

    # 10. nl-* 线程 ≤5s 收敛为 0
    def _nl_threads():
        return [t for t in threading.enumerate()
                if str(getattr(t, "name", "")).startswith("nl-")]

    ok10, dt10 = wait_until(lambda: len(_nl_threads()) == 0, 5.0)
    check("10. nl-* 线程 ≤5s 收敛为 0（{:.2f}s）".format(dt10), ok10,
          "remain={}".format([t.name for t in _nl_threads()]))

    # ── 还原环境 ──
    try:
        state.CONFIG_PATH = saved.get("CONFIG_PATH")
        state.NETLINK_PEERS = saved.get("NETLINK_PEERS")
        state.NETLINK_REQUIRE_AUTH = saved.get("NETLINK_REQUIRE_AUTH")
        state.NETLINK_SCRIPT_DIR = saved.get("NETLINK_SCRIPT_DIR")
        state.NETLINK_CONFIRM_CONTROL = saved.get("NETLINK_CONFIRM_CONTROL")
        state.NETLINK_CONFIRMED_PEERS = saved.get("NETLINK_CONFIRMED_PEERS")
        state.API_KEY = saved.get("API_KEY")
        state.running = saved.get("running")
        state.quit2 = saved.get("quit2")
        state.quit3 = saved.get("quit3")
        state.recording = saved.get("recording")
        state.has_script = saved.get("has_script")
        state.filename = saved.get("filename")
        state.script_dir = saved.get("script_dir")
        state.NETLINK_ENABLED = saved.get("NETLINK_ENABLED")
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
        netlink._bus = None
        netlink._node = None
    except Exception:
        pass
    for fp in (forget_fps or []):
        if fp:
            try:
                security.forget_peer_token(fp)
            except Exception:
                pass
    try:
        if root is not None:
            root.destroy()
    except Exception:
        pass
    try:
        shutil.rmtree(tmpdir, ignore_errors=True)
    except Exception:
        pass

    elapsed = time.time() - T0
    print("-" * 66)
    if elapsed >= TOTAL_BUDGET:
        _fails += 1
        print("[FAIL] 总耗时 {:.1f}s 超过 {}s 预算".format(elapsed, TOTAL_BUDGET))
    else:
        print("[OK]   总耗时 {:.1f}s (< {}s)".format(elapsed, TOTAL_BUDGET))
    print("warns={} skips={}".format(_warns, _skips))
    if _fails == 0:
        print("PASS (all assertions OK; warns={}, skips={})".format(_warns, _skips))
        return 0
    print("FAIL ({} assertion(s) failed; warns={}, skips={})".format(
        _fails, _warns, _skips))
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
