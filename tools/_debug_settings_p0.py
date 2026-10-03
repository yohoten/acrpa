# -*- coding: utf-8 -*-
"""设置窗口 P0 修复的离屏冒烟测试（不依赖 ACRPA 主程序）

覆盖:
  P0-1 「高级设置」卡 apply 必须把控件值真正写回 state
        （旧实现: 三项被写在 except 分支 → 永不落盘; 非法值 → 连带跳过后续赋值）
  P0-2 OCR 状态/重载不再引用不存在的 ocr_backend.get_ocr_engine
  P0-3 apply 抛异常时必须记录失败原因（角标可点击查看/重试）
  P0-4 + 新增 UI: 推理参数/授权串/调用原型三个字段参与落盘

用法:
    .venv\\Scripts\\python.exe -X utf8 tools\\_debug_settings_p0.py
退出码: 0=全部通过, 1=存在失败项
"""
import os
import re
import sys
import time
import traceback

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_SRC = os.path.join(_ROOT, "src")
sys.path.insert(0, _SRC)

import tkinter  # noqa: E402

import state  # noqa: E402
import settings_window as sw  # noqa: E402

#: 高级设置卡 apply 应当落盘的键（控件值 → state）
ADV_KEYS = (
    "MAX_EXECUTION_MINUTES", "BOUND_WINDOW_TITLE", "STOP_ON_ERROR",
    "OCR_PREFERRED_BACKEND", "OCR_PADDLE_DIR", "OCR_PRELOAD", "OCR_THREADS",
    "BROWSER_HEADLESS", "BROWSER_SLOW_MO", "SCHED_POLL_INTERVAL", "DD_DLL_PATH",
    "PADDLE_DLL_ENABLED", "PADDLE_DLL_DIR", "PADDLE_DLL_MODEL_DIR",
    "PADDLE_DLL_PROTO_INIT", "PADDLE_DLL_PROTO_DETECT",
    "PADDLE_DLL_CONFIG", "PADDLE_DLL_LICENSE",
)


def _scan_colors():
    """从 settings_window 源码扫描所有 C["key"] 用法，凑出完整颜色键集。"""
    with open(os.path.join(_SRC, "settings_window.py"), encoding="utf-8") as fh:
        src = fh.read()
    keys = sorted(set(re.findall(r'C\["([A-Za-z0-9_]+)"\]', src)))
    base = {
        "bgc": "#f4f4f4", "fgb": "#111111", "fgm": "#666666", "ebg": "#ffffff",
        "ac": "#2a6df4", "sc": "#2e9e5b", "dg": "#c0504d", "wn": "#d99a2b",
        "err": "#c0504d", "errbg": "#ffe3e3",
    }
    return {k: base.get(k, "#dddddd") for k in keys}


def main():
    results = []

    def check(name, cond, extra=""):
        results.append((name, bool(cond), extra))

    root = tkinter.Tk()
    root.withdraw()
    cb_errors = []
    root.report_callback_exception = (
        lambda exc, val, tb: cb_errors.append("".join(traceback.format_exception(exc, val, tb))))

    # 计划调度卡引用的变量通常由主程序创建，这里兜底
    if not hasattr(state, "_sched_enabled_var"):
        state._sched_enabled_var = tkinter.BooleanVar(
            value=bool(getattr(state, "SCHED_ENABLED", False)))

    sw.init_ctx(root_win=root, colors=_scan_colors(),
                fonts=(("Segoe UI", 10), ("Segoe UI", 9), ("Segoe UI", 8), ("Segoe UI", 9)),
                app_root=_ROOT)

    cfg_path = getattr(state, "CONFIG_PATH", os.path.join(_ROOT, "config.json"))
    backup = None
    if os.path.exists(cfg_path):
        with open(cfg_path, "rb") as fh:
            backup = fh.read()

    try:
        sw.open_settings_window()
        for _ in range(6):
            root.update()
            root.update_idletasks()
            time.sleep(0.05)

        # ── P0-1: 打开窗口时控件值 == state 值（快照），投毒后再保存应被还原 ──
        expected = {}
        for k in ADV_KEYS:
            if hasattr(state, k):
                expected[k] = getattr(state, k)
        check("高级设置卡字段齐全", len(expected) == len(ADV_KEYS),
              "缺少: {}".format([k for k in ADV_KEYS if k not in expected]))

        for k in expected:
            cur = getattr(state, k)
            if isinstance(cur, bool):
                setattr(state, k, not cur)
            elif isinstance(cur, int):
                setattr(state, k, 99999 if cur != 99999 else 0)
            else:
                setattr(state, k, "__POISON__")

        sw._do_save("advanced")
        for k, v in expected.items():
            now = getattr(state, k)
            check("P0-1 落盘 {}".format(k), now == v,
                  "期望 {!r} 实际 {!r}".format(v, now))
        check("P0-1 保存无错误", not sw._last_error.get("advanced"),
              str(sw._last_error.get("advanced", "")))

        # 新增 UI 字段确实被绑定（旧实现根本没有这些控件）
        check("P0-4 新 UI 生效: proto_detect",
              getattr(state, "PADDLE_DLL_PROTO_DETECT", "") == expected.get("PADDLE_DLL_PROTO_DETECT"))
        check("P0-4 新 UI 生效: license 字段存在",
              "paddle_dll_license_var" in open(os.path.join(_SRC, "settings_window.py"),
                                               encoding="utf-8").read())

        # ── P0-3: apply 抛异常 → 记录失败原因（供角标点击查看/重试） ──
        def _boom():
            raise RuntimeError("boom-for-test")
        sw._apply_map["__test_fail__"] = _boom
        sw._do_save("__test_fail__")
        check("P0-3 失败被记录", bool(sw._last_error.get("__test_fail__")),
              str(sw._last_error.get("__test_fail__")))
        sw._apply_map.pop("__test_fail__", None)
        sw._last_error.pop("__test_fail__", None)

        # ── P0-2: 旧接口已清除；新接口存在 ──
        with open(os.path.join(_SRC, "settings_window.py"), encoding="utf-8") as fh:
            src_now = fh.read()
        # 注释里可以提到旧函数名, 但不能再有 import/调用
        check("P0-2 源码已无 get_ocr_engine 调用",
              "from ocr_backend import get_ocr_engine" not in src_now
              and "get_ocr_engine(" not in src_now)
        import ocr_backend
        check("P0-2 ocr_get_backend_info 存在", hasattr(ocr_backend, "ocr_get_backend_info"))
        check("P0-2 ocr_reset_backend 存在", hasattr(ocr_backend, "ocr_reset_backend"))

        # ── P0-4: 高级设置卡内不得出现 grid 单元格冲突（改行号最容易踩） ──
        def _find_by_text(win, needle):
            stack = [win]
            while stack:
                w = stack.pop()
                try:
                    if needle in str(w.cget("text")):
                        return w
                except Exception:
                    pass
                try:
                    stack.extend(w.winfo_children())
                except Exception:
                    pass
            return None

        node = _find_by_text(sw._win, "推理参数:")
        check("P0-4 定位到高级设置卡", node is not None)
        if node is not None:
            adv_content = node.master.master
            cells = {}
            for k in adv_content.winfo_children():
                try:
                    gi = k.grid_info()
                except Exception:
                    continue
                if not gi or int(gi.get("columnspan", 1) or 1) != 1:
                    continue
                key = (int(gi.get("row", 0)), int(gi.get("column", 0)))
                cells.setdefault(key, []).append(str(k))
            dup = {k: v for k, v in cells.items() if len(v) > 1}
            rows = sorted(k[0] for k in cells)
            check("P0-4 高级卡无单元格冲突", not dup, str(dup))
            check("P0-4 高级卡行号连续(0..N-1)",
                  rows == list(range(len(rows))), str(rows))

        # ── 等待延迟回调（OCR 状态检测 after(500) + 后台探测线程）执行完 ──
        deadline = time.time() + 2.5
        while time.time() < deadline:
            root.update()
            time.sleep(0.05)
        check("无 Tk 回调异常", not cb_errors,
              (cb_errors[0][-400:] if cb_errors else ""))
    finally:
        try:
            sw._close()
        except Exception:
            pass
        try:
            root.destroy()
        except Exception:
            pass
        if backup is None:
            if os.path.exists(cfg_path):
                os.remove(cfg_path)
        else:
            with open(cfg_path, "wb") as fh:
                fh.write(backup)

    passed = sum(1 for _n, ok, _e in results if ok)
    for name, ok, extra in results:
        line = "{} {}".format("PASS" if ok else "FAIL", name)
        if not ok and extra:
            line += "  ← {}".format(extra)
        print(line)
    print("\n{}  ({}/{} 项通过)".format(
        "全部通过" if passed == len(results) else "存在失败项", passed, len(results)))
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
