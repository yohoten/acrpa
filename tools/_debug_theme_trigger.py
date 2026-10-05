#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""save_config() → _refresh_theme() 触发收窄实测 (真实 Tk + monkeypatch 计数)。

用法:
    python -X utf8 tools/_debug_theme_trigger.py

目的 (本轮性能修复的证据):
  旧实现: state.save_config() 通知回调的 changed 恒含 dark_mode →
          ACRPA._on_config_changed 每次保存都调用 _refresh_theme() (整应用重绘)。
  新实现: 仅当 state.DARK_MODE 相对「已应用主题」_applied_dark_mode 真的变化时才刷新。

用例:
  T1 无关保存 (模拟拖动 Mini Bar 结束 state.MINI_BAR_POS 变更) → 增量应为 0
  T2 dark_mode 真变化后保存                                   → 增量应为 1
  T3 同值再保存 (未变化)                                      → 增量应为 0
  T4 真实 toggle_dark() 链路 (先刷新再保存)                   → 增量应为 1 (不重复刷新)

退出码: 0 = 全部符合预期 / 1 = 有不符合项。
若 ACRPA 无法实例化, 打印 [WARN] 并以退出码 2 区别于通过。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "src"))

FAILS = []


def _p(status, msg):
    print("[{}] {}".format(status, msg))


def _check(name, actual, expected):
    ok = (actual == expected)
    if not ok:
        FAILS.append("{}: got {}, expected {}".format(name, actual, expected))
    _p("OK" if ok else "FAIL", "{}: got {} / expected {}".format(name, actual, expected))


def main():
    print("=== save_config -> _refresh_theme 触发收窄实测 ===")
    try:
        import ACRPA
        import app
        app.build()   # 入口拆分后: import ACRPA 不再建窗, 需显式构建
        import state
    except Exception as e:
        _p("WARN", "ACRPA 导入失败 ({}), 无法实测".format(e))
        return 2

    try:
        state.CHECK_UPDATE = False
    except Exception:
        pass

    # 计数包装: _on_config_changed / toggle_dark 均通过全局名 _refresh_theme 调用 → 可计数
    calls = {"n": 0}
    _orig = ACRPA._refresh_theme

    def _counting():
        calls["n"] += 1
        _orig()

    ACRPA._refresh_theme = _counting
    _p("OK", "已包装 ACRPA._refresh_theme 计数 (真实重绘仍执行)")

    def _pump():
        """驱动主线程 after 队列 (线程安全调度用例需要)。"""
        try:
            ACRPA.root.update()
            ACRPA.root.update_idletasks()
        except Exception:
            pass

    _pump()

    # T1: 无关保存 (拖动 Mini Bar 结束 _mb_save_pos → state.MINI_BAR_POS 变更 → save_config)
    c0 = calls["n"]
    try:
        state.MINI_BAR_POS = "{}+{}".format(123, 45)
    except Exception:
        pass
    state.save_config()
    _pump()
    _check("T1 无关保存(拖动 Mini Bar) refresh 增量", calls["n"] - c0, 0)

    # T2: dark_mode 真变化 → 期望刷新一次
    c1 = calls["n"]
    state.DARK_MODE = not bool(state.DARK_MODE)
    state.save_config()
    _pump()
    _check("T2 dark_mode 真变化 refresh 增量", calls["n"] - c1, 1)

    # T3: 同值再保存 → 期望不刷新
    c2 = calls["n"]
    state.save_config()
    _pump()
    _check("T3 同值再保存 refresh 增量", calls["n"] - c2, 0)

    # T4: 真实 toggle_dark() 链路 (内部先 _refresh_theme 再 save_config) → 恰好一次
    c3 = calls["n"]
    try:
        ACRPA.toggle_dark()
    except Exception as e:
        _p("FAIL", "toggle_dark() 抛异常: {}".format(e))
        FAILS.append("toggle_dark 异常")
    _pump()
    _check("T4 toggle_dark() refresh 增量", calls["n"] - c3, 1)

    ACRPA._refresh_theme = _orig
    try:
        ACRPA.root.destroy()
    except Exception:
        pass

    print("=== 结果: {} ===".format(
        "FAIL ({})".format(len(FAILS)) if FAILS else "OK"))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
