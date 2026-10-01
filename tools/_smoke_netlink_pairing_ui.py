# -*- coding: utf-8 -*-
"""NetLink Phase2-1b 配对与权限 UI 集成自测 —— 全自动、无人工交互、总时长 < 40s。

运行:   python tools/_smoke_netlink_pairing_ui.py
退出码: 0 = 全部断言通过（含 [SKIP] no display / 允许 [WARN]）；1 = 存在 [FAIL]

要点:
  * 真实 Tk root (withdraw) + 真实 netlink 节点（tcp 19970），仅用 root.update()
    驱动事件循环（不进入 mainloop），验证 NetLinkWindow._pump 的主线程消费路径；
  * 用原始 socket 手工走完 HELLO → AUTH(pair) → AUTH_CHALLENGE → AUTH(proof) → AUTH_OK，
    帧编解码复用 netlink.protocol，密码学复用 netlink.security；
  * 不写项目 config.json：state.CONFIG_PATH 指向临时文件，测后还原；
    state.NETLINK_PEERS / API_KEY / 凭据库均备份并还原；
  * 端口用 199xx 段（19970 / 19971），用完释放。
"""
import os
import shutil
import socket
import sys
import tempfile
import time
import traceback

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_SRC = os.path.join(_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

# ── 端口（199xx 段）──
TCP_PORT = 19970
UDP_PORT = 19971

# ── 控制端（原始 socket）身份 ──
CLI_ID = "pairuicli0000001"
CLI_NAME = "PairUiCli"

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


def spin(root, seconds=0.6):
    """在给定秒数内反复 root.update() 驱动 _pump。"""
    end = time.time() + float(seconds)
    while time.time() < end:
        try:
            root.update()
        except Exception:
            pass
        time.sleep(0.02)


def _all_text(widget, out=None):
    """递归收集 widget 树中的 text / Toplevel title（用于断言 PIN 可见）。"""
    if out is None:
        out = []
    try:
        if "text" in widget.keys():
            t = widget.cget("text")
            if t:
                out.append(str(t))
    except Exception:
        pass
    try:
        if widget.winfo_class() == "Toplevel":
            ti = widget.title()
            if ti:
                out.append(str(ti))
    except Exception:
        pass
    try:
        for ch in widget.winfo_children():
            _all_text(ch, out)
    except Exception:
        pass
    return out


def _tree_rows(tv):
    """Treeview 全部行 → [(values...)]。"""
    rows = []
    try:
        for iid in tv.get_children():
            rows.append(tuple(tv.item(iid).get("values") or ()))
    except Exception:
        pass
    return rows


def _row_by_fp(rows, fp):
    for r in rows:
        if len(r) >= 2 and str(r[1]) == str(fp):
            return r
    return None


# ── 原始 socket 客户端 ────────────────────────────────────────────────────
class RawClient(object):
    def __init__(self, port, host="127.0.0.1"):
        from netlink.protocol import FrameReader
        self.s = socket.create_connection((host, int(port)), timeout=3.0)
        self.reader = FrameReader()
        self.inbox = []

    def send(self, msg):
        from netlink.protocol import frame_message
        try:
            self.s.sendall(frame_message(msg))
            return True
        except Exception:
            return False

    def recv(self, timeout=2.0):
        from netlink.protocol import parse_msg
        if self.inbox:
            return self.inbox.pop(0)
        deadline = time.time() + max(0.0, float(timeout))
        while time.time() < deadline:
            self.s.settimeout(max(0.05, deadline - time.time()))
            try:
                chunk = self.s.recv(65536)
            except socket.timeout:
                break
            except Exception:
                return None
            if not chunk:
                return None
            try:
                for p in self.reader.feed(chunk):
                    self.inbox.append(parse_msg(p))
            except Exception:
                return None
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


# ══════════════════════════════════════════════════════════════════════════
def main():
    # ── 建 root (无显示设备则 SKIP，退出码 0) ──
    try:
        import tkinter
        root = tkinter.Tk()
        root.withdraw()
    except Exception as e:
        print("[SKIP] no display ({})".format(e))
        return 0

    import state
    import netlink
    import netlink_window
    from netlink import security
    from netlink.protocol import (make_msg, T_HELLO, T_AUTH, T_AUTH_OK,
                                  T_AUTH_FAIL, T_AUTH_CHALLENGE)

    # ── 环境隔离：不写项目 config.json；备份并还原内存态/凭据库 ──
    orig_config_path = state.CONFIG_PATH
    orig_peers = list(getattr(state, "NETLINK_PEERS", []) or [])
    orig_api_key = getattr(state, "API_KEY", "")
    orig_cred = None
    try:
        orig_cred = state.cred_read(state._CRED_TARGET)
    except Exception:
        orig_cred = None
    tmpdir = tempfile.mkdtemp(prefix="acrpa_pairui_")
    state.CONFIG_PATH = os.path.join(tmpdir, "config.json")
    state.NETLINK_PEERS = []

    fp = security.make_fingerprint(CLI_ID, CLI_NAME)
    node = None
    win = None
    cli = None
    try:
        print("=" * 64)
        print("NetLink Phase2-1b pairing/permission UI integration smoke test")
        print("=" * 64)

        # ── 启动单节点（被控端）：tcp 19970, 关闭自动发现, 无静态对端, require_auth ──
        state.NETLINK_ENABLED = True
        state.NETLINK_PORT = TCP_PORT
        state.NETLINK_DISCOVERY_PORT = UDP_PORT
        state.NETLINK_AUTODISCOVER = False
        state.NETLINK_DEVICE_NAME = "PairUiA"
        state.NETLINK_STATIC_PEERS = []
        state.NETLINK_REQUIRE_AUTH = True
        state.NETLINK_PIN_TTL = 600

        ok0 = netlink.start_netlink(root)
        check("0. start node (tcp {})".format(TCP_PORT), bool(ok0))
        node = netlink.get_node()
        check("0b. netlink.get_node() 可用", node is not None)
        if node is None:
            print("[FAIL] 节点不可用，后续 UI 断言无法继续")
            return finish(root, node, win, cli, orig_config_path, orig_peers,
                          orig_api_key, orig_cred, tmpdir, fp)

        # ── 打开「设备互联」窗口 ──
        win = netlink_window.open_netlink_window(root)
        spin(root, 0.8)
        check("1. open_netlink_window 成功", win is not None
              and netlink_window.is_open())
        check("1b. 窗口 alive()", bool(win is not None and win.alive()))
        if win is None or not win.alive():
            print("[FAIL] 窗口未就绪，后续 UI 断言无法继续")
            return finish(root, node, win, cli, orig_config_path, orig_peers,
                          orig_api_key, orig_cred, tmpdir, fp)

        # ── 3. 配对码 UI ──
        pin = node.open_pair_window()
        check("2. open_pair_window 返回 6 位数字 PIN",
              isinstance(pin, str) and len(pin) == 6 and pin.isdigit(),
              "pin={!r}".format(pin))
        spin(root, 0.8)
        st = node.pair_window_status()
        check("2b. pair_window_status()['active'] is True",
              bool(st.get("active")) is True, "st={}".format(st))
        joined = "".join(_all_text(win.win)).replace(" ", "")
        check("2c. 遍历窗口 widget 文本可找到 PIN（去空格后）",
              bool(pin) and pin in joined, "pin={!r}".format(pin))

        # 打开配对码对话框，再次断言 PIN 可见（对话框为 win.win 的子 Toplevel）
        dlg = win._open_pair_dialog()
        spin(root, 0.5)
        try:
            dlg_ok = dlg is not None and bool(dlg.winfo_exists())
        except Exception:
            dlg_ok = False
        check("2d. 配对码对话框已创建", dlg_ok)
        joined2 = "".join(_all_text(win.win)).replace(" ", "")
        check("2e. 对话框中可找到 PIN", bool(pin) and pin in joined2,
              "pin={!r}".format(pin))

        # ── 4. 配对成功路径（原始 socket 走完 AUTH 四步）──
        cli = RawClient(TCP_PORT)
        hello = cli.wait(T_HELLO, 3.0)
        check("3. 收到被控端 HELLO", hello is not None)
        cli.send(make_msg(T_AUTH, {"mode": "pair", "node_id": CLI_ID,
                                   "fingerprint": fp, "name": CLI_NAME}))
        ch = cli.wait(T_AUTH_CHALLENGE, 3.0)
        cd = (ch or {}).get("data") or {}
        nonce = str(cd.get("nonce") or "")
        salt = str(cd.get("salt") or "")
        check("3b. 收到 AUTH_CHALLENGE(含 nonce+salt)",
              ch is not None and bool(nonce) and bool(salt))
        token = security.derive_token(pin, salt)
        ts = int(time.time())
        proof = security.make_proof(token, nonce, CLI_ID, ts)
        cli.send(make_msg(T_AUTH, {"nonce": nonce, "proof": proof, "ts": ts}))
        resp = cli.wait_any((T_AUTH_OK, T_AUTH_FAIL), 3.0)
        check("3c. AUTH 四步后收到 AUTH_OK",
              resp is not None and resp.get("t") == T_AUTH_OK,
              "got={}".format((resp or {}).get("t")))
        check("3d. AUTH_OK.perm == observe",
              ((resp or {}).get("data") or {}).get("perm") == "observe",
              "data={}".format((resp or {}).get("data")))

        # 刷新后「已配对设备」区应出现该设备，权限显示 仅观察
        spin(root, 0.9)
        rows4 = _tree_rows(win.tv_paired)
        row4 = _row_by_fp(rows4, fp)
        check("4. 「已配对设备」区出现该设备", row4 is not None,
              "rows={}".format(rows4))
        check("4b. 该行权限显示 仅观察",
              row4 is not None and len(row4) >= 3 and str(row4[2]) == "仅观察",
              "row={}".format(row4))

        # ── 5. 权限变更即时生效 ──
        okp = node.set_peer_perm(fp, "control")
        check("5. set_peer_perm(control) 返回 True", bool(okp))
        spin(root, 0.9)
        rows5 = _tree_rows(win.tv_paired)
        row5 = _row_by_fp(rows5, fp)
        check("5b. 该行权限即时变为 允许操控",
              row5 is not None and len(row5) >= 3 and str(row5[2]) == "允许操控",
              "row={}".format(row5))

        # ── 6. 移除（直接调 node.remove_peer，不触发二次确认对话框）──
        okr = node.remove_peer(fp)
        check("6. remove_peer 返回 True", bool(okr))
        spin(root, 0.9)
        rows6 = _tree_rows(win.tv_paired)
        check("6b. 该设备已从「已配对设备」区消失",
              _row_by_fp(rows6, fp) is None, "rows={}".format(rows6))
        check("6c. node.list_peers() 为空", list(node.list_peers() or []) == [])

        # ── 7. 未启动容错（防 _pump 刷屏死循环）──
        t7 = time.time()
        netlink.stop_netlink()
        spin(root, 0.8)
        elapsed7 = time.time() - t7
        check("7. stop 后窗口仍存活(alive)", bool(win.alive())
              and netlink_window.is_open())
        check("7b. stop 后刷新阶段耗时 < 2s", elapsed7 < 2.0,
              "elapsed={:.2f}s".format(elapsed7))

    except Exception:
        print("[FAIL] unexpected exception:")
        traceback.print_exc()
        _fails += 1
    finally:
        return finish(root, node, win, cli, orig_config_path, orig_peers,
                      orig_api_key, orig_cred, tmpdir, fp)


def finish(root, node, win, cli, orig_config_path, orig_peers, orig_api_key,
           orig_cred, tmpdir, fp):
    """收尾：关窗口/停节点/还原 state 与凭据库/清理临时文件；返回退出码。"""
    global _fails
    try:
        if cli is not None:
            cli.close()
    except Exception:
        pass
    try:
        import netlink_window
        netlink_window.close_window()
    except Exception:
        pass
    try:
        spin(root, 0.2)
    except Exception:
        pass
    try:
        import netlink
        netlink.stop_netlink()
    except Exception:
        pass
    try:
        if node is not None:
            node.stop()
    except Exception:
        pass
    # 还原内存态与凭据库
    try:
        import state
        state.CONFIG_PATH = orig_config_path
        state.NETLINK_PEERS = orig_peers
        state.API_KEY = orig_api_key
        if orig_cred:
            state.cred_write(state._CRED_TARGET, orig_cred)
        else:
            state.cred_delete(state._CRED_TARGET)
    except Exception:
        pass
    try:
        from netlink import security
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
    print("-" * 64)
    print("elapsed {:.2f}s, warns={}".format(elapsed, _warns))
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
