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
