# -*- coding: utf-8 -*-
"""NetLink Phase1-3 窗口冒烟自测 —— 用一个 withdraw 的 Tk root，
反复开关「设备互联」窗口 (open_netlink_window / close_window)。

运行:   python tools/_smoke_netlink_window.py
退出码: 0 = 通过；或 0 = 无显示设备([SKIP] no display)；1 = 存在失败

要点:
  * 不依赖 netlink 已启动 (未启动时窗口仍须能正常打开/关闭/刷新);
  * import netlink_window 阶段不得创建任何窗口;
  * 全程仅用 root.update() 驱动事件循环, 不进入 mainloop。
"""
import os
import sys
import tempfile
import traceback

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(os.path.dirname(_HERE), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

_results = []


def check(name, cond, detail=""):
    if cond:
        _results.append((name, True))
        print("[OK]   " + name)
    else:
        _results.append((name, False))
        print("[FAIL] " + name + (" :: " + detail if detail else ""))


def main():
    # ── 建 root (无显示设备则 SKIP) ──
    try:
        import tkinter
        root = tkinter.Tk()
        root.withdraw()
    except Exception as e:
        print("[SKIP] no display ({})".format(e))
        return 0

    try:
        import netlink
        import netlink_window
        import state

        # 静默所有模态对话框（本脚本不进入 mainloop，模态框会阻塞）
        try:
            from tkinter import messagebox as _mb
            _mb.showinfo = lambda *a, **k: "ok"
            _mb.showwarning = lambda *a, **k: "ok"
            _mb.showerror = lambda *a, **k: "ok"
            _mb.askyesno = lambda *a, **k: True
            _mb.askstring = lambda *a, **k: None
        except Exception:
            pass

        # import 期零副作用: 不得创建窗口
        check("import 期无窗口", netlink_window.is_open() is False,
              "is_open() 应初始为 False")

        # 记录初始 netlink 运行态 (本脚本从不主动启动)
        started = False
        try:
            started = bool(netlink.is_running())
        except Exception:
            started = False
        check("本脚本未启动 netlink", not started)

        # 反复开关 3 次
        for i in range(1, 4):
            w = netlink_window.open_netlink_window(root)
            for _ in range(6):
                root.update()
            check("第{}次 open 成功".format(i),
                  w is not None and netlink_window.is_open())
            netlink_window.close_window()
            for _ in range(4):
                root.update()
            check("第{}次 close 成功".format(i), netlink_window.is_open() is False)

        # close_window 幂等
        netlink_window.close_window()
        check("close_window 幂等", netlink_window.is_open() is False)

        # ── Phase4-2 批次3：网页面板对话框（控制开关 / PIN / TLS 状态）冒烟 ──
        # 隔离：临时 config + 备份可能被触碰的 state 键（避免污染真实 config.json）
        _saved_cfg = getattr(state, "CONFIG_PATH", None)
        _keys = ("NETLINK_WEB_CONTROL", "NETLINK_WEB_TLS",
                 "NETLINK_WEB_ALLOW_REMOTE_CONTROL")
        _saved = dict((k, getattr(state, k, None)) for k in _keys)
        _tmp = tempfile.mkdtemp(prefix="acrpa_websmoke_")
        try:
            state.CONFIG_PATH = os.path.join(_tmp, "config.json")
        except Exception:
            pass
        try:
            w = netlink_window.open_netlink_window(root)
            for _ in range(6):
                root.update()
            check("网页面板: 窗口已打开", w is not None
                  and netlink_window.is_open())
            dlg = None
            try:
                dlg = w._on_web_panel() if w is not None else None
            except Exception:
                traceback.print_exc()
                dlg = None
            for _ in range(4):
                root.update()
            check("网页面板: _on_web_panel() 返回对话框且不抛异常", dlg is not None)
            check("网页面板: 控制开关变量已构造",
                  getattr(w, "_web_control_var", None) is not None)
            check("网页面板: TLS/证书状态标签已构造",
                  getattr(w, "_web_tls_lbl", None) is not None)
            check("网页面板: [设置/重置控制 PIN] 按钮已构造",
                  getattr(w, "_web_pin_btn", None) is not None)
            check("网页面板: 允许局域网控制变量已构造",
                  getattr(w, "_web_allow_remote_var", None) is not None)
            _err = ""
            try:
                w._web_refresh()
                w._on_web_control_toggle()
                w._on_web_allow_remote_toggle()
                for _ in range(2):
                    root.update()
            except Exception as e:      # noqa: BLE001
                _err = repr(e)
            check("网页面板: 控制开关 / 局域网开关联动不抛异常", _err == "", _err)
            _pin_ok = False
            try:
                _pin_ok = isinstance(netlink.has_webui_control_pin(), bool) \
                    and isinstance(netlink.webui_control_status(), dict)
            except Exception as e:      # noqa: BLE001
                _pin_ok = False
                print("   门面接口异常: {!r}".format(e))
            check("网页面板: 门面接口可调用（has pin / control status）", _pin_ok)
            netlink_window.close_window()
            for _ in range(4):
                root.update()
            check("网页面板: 关闭窗口后 is_open() 为 False",
                  netlink_window.is_open() is False)
        finally:
            for _k, _v in _saved.items():
                try:
                    setattr(state, _k, _v)
                except Exception:
                    pass
            try:
                state.CONFIG_PATH = _saved_cfg
            except Exception:
                pass
            try:
                import shutil
                shutil.rmtree(_tmp, ignore_errors=True)
            except Exception:
                pass
        return 0
    except Exception:
        print("[FAIL] unexpected exception:")
        traceback.print_exc()
        return 1
    finally:
        try:
            root.destroy()
        except Exception:
            pass


if __name__ == "__main__":
    _code = main()
    _fails = [n for n, ok in _results if not ok]
    if _fails or _code != 0:
        print("[FAIL] {} 项失败".format(len(_fails)))
        sys.exit(1)
    print("[OK]   all window smoke checks passed")
    sys.exit(0)
