# -*- coding: utf-8 -*-
"""_smoke_ui_introspect.py  (阶段2 测试子任务，仅新建，不改业务源码)

UI 显示/体验结构化验证：
  - sys.path 加入 src/，import ACRPA（import 即建 root 窗口）
  - root.update_idletasks()/update() 让其完成布局计算
  - 采集: 主窗口几何/缩放/DPI/字体/顶层窗口与标签页清单/控件溢出(req>actual)
  - 用 PIL ImageGrab 截取主窗口 -> tools\\_ui_screenshots\\app_main.png
  - 结束后 root.destroy()，退出码 0
输出落盘 tools\\_smoke_result_phase2\\_smoke_ui_introspect.out
"""

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
for p in (ROOT, SRC):
    if p not in sys.path:
        sys.path.insert(0, p)

OUTDIR = os.path.join(ROOT, "tools", "_smoke_result_phase2")
os.makedirs(OUTDIR, exist_ok=True)
SHOTDIR = os.path.join(ROOT, "tools", "_ui_screenshots")
os.makedirs(SHOTDIR, exist_ok=True)
REPORT = os.path.join(OUTDIR, "_smoke_ui_introspect.out")

lines = []


def emit(m=""):
    print(m)
    lines.append(m)


def write_report():
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main():
    import tkinter
    from tkinter import ttk

    emit("=== ACRPA UI 显示/体验结构化验证 (introspect) ===")
    emit("cwd(py): %s" % os.getcwd())
    emit("src 已加入 sys.path")
    emit("-" * 64)

    emit("[IMPORT] import ACRPA ...")
    import ACRPA
    import app
    app.build()   # 入口拆分后: import ACRPA 不再建窗, 需显式构建
    import utils
    try:
        import state as state_mod
    except Exception as e:
        state_mod = None
        emit("[WARN] import state 失败: %r" % (e,))

    root = ACRPA.root
    emit("[IMPORT] ACRPA 导入完成, root=%r" % (root,))

    # 让布局完成
    root.deiconify()
    for _ in range(6):
        try:
            root.update_idletasks()
            root.update()
        except Exception:
            pass
        time.sleep(0.08)

    # ---- a. 主窗口几何/缩放 ----
    emit("-" * 64)
    emit("=== [A] 主窗口 ===")
    try:
        title = root.title()
    except Exception as e:
        title = "<err %r>" % (e,)
    geom = root.winfo_geometry()
    w, h = root.winfo_width(), root.winfo_height()
    rw, rh = root.winfo_reqwidth(), root.winfo_reqheight()
    try:
        st = root.state()
    except Exception as e:
        st = "<err %r>" % (e,)
    try:
        tk_scaling = root.tk.call("tk", "scaling")
    except Exception as e:
        tk_scaling = "<err %r>" % (e,)
    try:
        ui_scale = utils.current_ui_scale()
    except Exception as e:
        ui_scale = "<err %r>" % (e,)
    state_ui_scale = getattr(state_mod, "UI_SCALE", None) if state_mod else None
    try:
        dpi = utils.dpi_factor()
    except Exception as e:
        dpi = "<err %r>" % (e,)

    emit("[MAIN] title=%r" % (title,))
    emit("[MAIN] geometry=%r size=%dx%d req=%dx%d" % (geom, w, h, rw, rh))
    emit("[MAIN] state=%r" % (st,))
    emit("[MAIN] tk_scaling=%r dpi_factor=%r" % (tk_scaling, dpi))
    emit("[MAIN] utils.current_ui_scale=%r state.UI_SCALE=%r" % (ui_scale, state_ui_scale))
    main_overflow_w = rw - w
    main_overflow_h = rh - h
    emit("[MAIN] 内容需求-实际: reqw-w=%d reqh-h=%d  -> %s"
         % (main_overflow_w, main_overflow_h,
            "内容超窗(疑似裁剪)" if (main_overflow_w > 1 or main_overflow_h > 1) else "OK(未超窗)"))
    try:
        emit("[MAIN] minsize=%r" % (root.minsize(),))
    except Exception:
        pass
    try:
        emit("[MAIN] compact_mode=%r" % (getattr(state_mod, "COMPACT_MODE", None),))
    except Exception:
        pass

    # ---- b. 字体 ----
    emit("-" * 64)
    emit("=== [B] 命名字体 (_FONT_SPECS 角色) ===")
    roles = []
    try:
        roles = list(getattr(utils, "_FONT_SPECS", {}).keys())
    except Exception:
        pass
    if not roles:
        roles = ["ACRPA_TITLE", "ACRPA_BODY", "ACRPA_SMALL", "ACRPA_SMALL_BOLD",
                 "ACRPA_TINY", "ACRPA_BUTTON", "ACRPA_LOG", "ACRPA_ICON",
                 "ACRPA_ICON_MD", "ACRPA_ICON_LG"]
    for role in roles:
        fam = size = wt = "<err>"
        try:
            fam = root.tk.call("font", "actual", role, "-family")
            size = root.tk.call("font", "actual", role, "-size")
            wt = root.tk.call("font", "actual", role, "-weight")
            ok = "OK"
        except Exception as e:
            ok = "ERR %r" % (e,)
        base = None
        try:
            base = getattr(utils, "_FONT_SPECS", {}).get(role)
        except Exception:
            pass
        emit("[FONT] %-18s family=%r size=%r weight=%r base=%r %s"
             % (role, fam, size, wt, base, ok))

    # ---- c. 顶层窗口 & 标签页 ----
    emit("-" * 64)
    emit("=== [C] 顶层窗口 / 标签页清单 ===")

    def walk(widget, fn):
        fn(widget)
        try:
            for c in widget.winfo_children():
                walk(c, fn)
        except Exception:
            pass

    toplevels = []
    notebooks = []

    def collect(w):
        try:
            if isinstance(w, tkinter.Toplevel):
                toplevels.append(w)
        except Exception:
            pass
        try:
            if isinstance(w, ttk.Notebook):
                notebooks.append(w)
        except Exception:
            pass

    walk(root, collect)

    if toplevels:
        for i, t in enumerate(toplevels):
            try:
                ttl = t.title()
            except Exception:
                ttl = "<err>"
            try:
                mapped = t.winfo_ismapped()
                tstate = t.state()
            except Exception as e:
                mapped = tstate = "<err %r>" % (e,)
            emit("[TOPLEVEL] #%d title=%r mapped=%r state=%r" % (i, ttl, mapped, tstate))
    else:
        emit("[TOPLEVEL] (无独立 Toplevel 窗口 — 均按需打开)")

    if notebooks:
        for i, nb in enumerate(notebooks):
            tabs = []
            try:
                for t in nb.tabs():
                    try:
                        tabs.append(str(nb.tab(t, "text")).strip())
                    except Exception:
                        tabs.append("<err>")
            except Exception as e:
                tabs = ["<err %r>" % (e,)]
            emit("[TAB] notebook#%d tabs(%d)=%r" % (i, len(tabs), tabs))
    else:
        emit("[TAB] (未发现 Notebook)")

    # 关键按需窗口入口存在性
    emit("-" * 64)
    emit("=== [C2] 关键按需窗口打开入口 (模块级函数存在性) ===")
    checks = [
        ("NetLink 窗口", "netlink_window", "open_netlink_window"),
        ("脚本市场窗口", "market_window", "open_market_window"),
        ("设置窗口", "settings_window", "open_settings_window"),
    ]
    for label, modname, fname in checks:
        try:
            mod = __import__(modname)
            has = callable(getattr(mod, fname, None))
            emit("[ENTRY] %-14s %s.%s -> %s" % (label, modname, fname, "存在" if has else "缺失"))
        except Exception as e:
            emit("[ENTRY] %-14s %s.%s -> 导入/取属性失败: %r" % (label, modname, fname, e))

    # ---- d. 控件几何溢出扫描 ----
    emit("-" * 64)
    emit("=== [D] 可见控件几何溢出扫描 (req > actual) ===")
    overflows = []
    scanned = [0]

    def scan(w):
        scanned[0] += 1
        try:
            if not w.winfo_ismapped():
                return
        except Exception:
            return
        try:
            cw = w.winfo_width()
            ch = w.winfo_height()
            crw = w.winfo_reqwidth()
            crh = w.winfo_reqheight()
        except Exception:
            cw = ch = crw = crh = 0
        # 文本
        txt = ""
        for opt in ("text", "title"):
            try:
                v = w.cget(opt)
                if v:
                    txt = str(v)
                    break
            except Exception:
                continue
        cls = "?"
        try:
            cls = w.winfo_class()
        except Exception:
            pass
        ow = crw - cw
        oh = crh - ch
        if cw > 0 and ch > 0 and (ow > 1 or oh > 1):
            overflows.append({
                "cls": cls, "w": cw, "h": ch, "rw": crw, "rh": crh,
                "ow": ow, "oh": oh, "text": txt[:40],
                "score": max(ow, 0) + max(oh, 0),
            })

    def scan_all(w):
        scan(w)
        try:
            for c in w.winfo_children():
                scan_all(c)
        except Exception:
            pass

    scan_all(root)
    overflows.sort(key=lambda d: d["score"], reverse=True)
    emit("[OVERFLOW] 已扫描控件数=%d ; 命中溢出(可见且req>actual)=%d"
         % (scanned[0], len(overflows)))
    for i, d in enumerate(overflows[:20]):
        emit("[OVERFLOW] #%d cls=%s actual=%dx%d req=%dx%d over=(%+d,%+d) text=%r"
             % (i + 1, d["cls"], d["w"], d["h"], d["rw"], d["rh"], d["ow"], d["oh"], d["text"]))

    # ---- e. 屏幕 / DPI ----
    emit("-" * 64)
    emit("=== [E] 屏幕 / DPI / 越界风险 ===")
    try:
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    except Exception as e:
        sw = sh = "<err %r>" % (e,)
    try:
        vx, vy = root.winfo_vrootx(), root.winfo_vrooty()
        vw, vh = root.winfo_vrootwidth(), root.winfo_vrootheight()
    except Exception as e:
        vx = vy = vw = vh = "<err %r>" % (e,)
    emit("[SCREEN] screen=%sx%s  vroot=(%s,%s %sx%s)" % (sw, sh, vx, vy, vw, vh))
    emit("[SCREEN] tk_scaling=%r dpi_factor=%r ui_scale=%r" % (tk_scaling, dpi, ui_scale))
    try:
        right = root.winfo_rootx() + root.winfo_width()
        bottom = root.winfo_rooty() + root.winfo_height()
        emit("[SCREEN] 主窗口右下角=(%d,%d) vs screen=(%s,%s) -> %s"
             % (right, bottom, sw, sh,
                "OK" if (right <= int(sw) and bottom <= int(sh)) else "越界风险"))
    except Exception as e:
        emit("[SCREEN] 越界检测失败: %r" % (e,))

    # ---- f. 截图 ----
    emit("-" * 64)
    emit("=== [F] 主窗口截图 ===")
    shot_path = os.path.join(SHOTDIR, "app_main.png")
    try:
        from PIL import ImageGrab
        x = root.winfo_rootx()
        y = root.winfo_rooty()
        ww = root.winfo_width()
        hh = root.winfo_height()
        bbox = (x, y, x + ww, y + hh)
        emit("[SHOT] bbox=%r" % (bbox,))
        img = ImageGrab.grab(bbox=bbox)
        img.save(shot_path)
        emit("[SHOT] saved=%s size=%s" % (shot_path, img.size))
        emit("[SHOT] 结果: OK")
    except Exception as e:
        emit("[SHOT] 结果: SKIP/FAIL %r" % (e,))

    # ---- 汇总 ----
    emit("-" * 64)
    emit("=== 汇总 ===")
    emit("[SUM] 主窗口 size=%dx%d req=%dx%d 超窗=%s"
         % (w, h, rw, rh, (main_overflow_w > 1 or main_overflow_h > 1)))
    emit("[SUM] ui_scale=%r dpi=%r tk_scaling=%r" % (ui_scale, dpi, tk_scaling))
    emit("[SUM] notebook_tabs=%s"
         % ([str(nb.tab(t, "text")).strip() for nb in notebooks for t in nb.tabs()]
            if notebooks else []))
    emit("[SUM] 控件溢出命中=%d" % len(overflows))
    emit("=== 结果: OK (introspect 完成) ===")

    write_report()

    # 清理
    try:
        root.destroy()
    except Exception as e:
        emit("[WARN] root.destroy() 异常: %r" % (e,))
    return 0


if __name__ == "__main__":
    try:
        rc = main()
    except Exception:
        import traceback
        traceback.print_exc()
        lines.append("!!! 顶层异常 !!!")
        lines.extend(traceback.format_exc().splitlines())
        write_report()
        rc = 1
    sys.exit(rc)
