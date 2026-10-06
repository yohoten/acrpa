#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""设置窗口「界面显示」测试 (重点: 设置页面) —— 手动/离线冒烟。

用法:
    .venv\\Scripts\\python.exe -X utf8 tools\\_smoke_settings_ui.py

注: 属重 GUI 冒烟 (经 ``app.build()`` 建真实主窗), 与 ``_smoke_ui_introspect.py``
    同族; 因 ``run_tests --safe`` 会跳过 ``_smoke_*``, 本测试不进入 CI 绿灯集合,
    仅作人工「界面显示」审查 (退出码 1 表示检测到显示缺陷)。

设计:
  · 真实 Tk: import ACRPA + app.build() 建主窗, 再 open_settings_window() 建设置窗,
    在真实布局下采集几何/滚动/裁剪/扩展卡/主题往返/导航点击, 并截图。
  · **隔离**: 结束时恢复 config.json (本测试不写窗口几何持久化); 不调用 _close() 以免落盘。
  · 无 GUI 环境 (无法建 Tk root) → 实机段记 [WARN] 并退出码 0 (不假通过)。

覆盖:
  A  静态清单   : 12 张 cards/*.py 各有 build/apply; window._CARD_KEYS / _NAV_ITEMS == 12 且键序一致。
  B  实机显示   : (1) 窗口存在/标题/minsize; (2) 导航 12 / 卡片句柄 12 / apply 11 (无 quick);
                  (3) 卡片全部 grid; (4) 画布 scrollregion 覆盖全部内容且内容高于视口;
                  (5) 各卡 reqwidth 表 + 在 580/680/820 三档窗宽下的横向裁剪判定
                      (默认 680 有裁剪 → FAIL; 580 有裁剪 → WARN, 属设计文档已知「窄窗必然错位」);
                  (6) 定位最宽卡内最宽控件 (给出可操作定位);
                  (7) 「扩展」卡渲染状态行 (四态图标); (8) 主题往返换肤 (导航底色变化→复原);
                  (9) 点击导航滚动 + 高亮; (10) 子树 req>actual 溢出扫描 (WARN);
                  (11) Tk 回调无异常; (12) 截图落盘。

退出码: 0 = 无 FAIL / 1 = 有 FAIL。
"""
import os
import sys
import time
import traceback

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
for p in (ROOT, SRC):
    if p not in sys.path:
        sys.path.insert(0, p)

OUTDIR = os.path.join(ROOT, "tools", "_ui_screenshots")
os.makedirs(OUTDIR, exist_ok=True)
REPORT = os.path.join(OUTDIR, "settings_ui_display.out")

CARD_KEYS = ("exec", "ai", "sched", "record", "log", "system", "quick",
             "advanced", "netlink", "python", "market", "extensions")
STATE_ICONS = ("✔", "○", "⚠", "⏸")

_PASS, _FAIL, _WARN = [], [], []
_lines = []


def emit(m=""):
    print(m)
    _lines.append(m)


def check(cond, msg, detail=""):
    (_PASS if cond else _FAIL).append(msg)
    tail = ("  ({})".format(detail) if detail else "")
    emit("[{}] {}{}".format("OK  " if cond else "FAIL", msg, tail))


def warn(msg):
    _WARN.append(msg)
    emit("[WARN] {}".format(msg))


def _pump(root, n=6, dt=0.06):
    for _ in range(n):
        try:
            root.update_idletasks()
            root.update()
        except Exception:
            pass
        time.sleep(dt)


# ══════════════════════════════════════════════════════════════════════
# A. 静态清单
# ══════════════════════════════════════════════════════════════════════

def t_static():
    emit("\n── A. 静态: 卡片 / key 清单 ──")
    import importlib
    try:
        import ui.settings.window as w
    except Exception as e:
        check(False, "import ui.settings.window", repr(e))
        return
    check(tuple(getattr(w, "_CARD_KEYS", ())) == CARD_KEYS,
          "window._CARD_KEYS == 12 键", str(getattr(w, "_CARD_KEYS", None)))
    nav_keys = [it[0] for it in getattr(w, "_NAV_ITEMS", [])]
    check(nav_keys == list(CARD_KEYS),
          "_NAV_ITEMS 键序与 _CARD_KEYS 一致", str(nav_keys))
    for key in CARD_KEYS:
        try:
            mod = importlib.import_module("ui.settings.cards." + key)
        except Exception as e:
            check(False, "import cards.{}".format(key), repr(e))
            continue
        check(callable(getattr(mod, "build", None)),
              "cards.{}.build 可调用".format(key))
        check(callable(getattr(mod, "apply", None)),
              "cards.{}.apply 可调用".format(key))


# ══════════════════════════════════════════════════════════════════════
# B. 实机显示
# ══════════════════════════════════════════════════════════════════════

def _walk(widget):
    yield widget
    try:
        children = widget.winfo_children()
    except Exception:
        return
    for c in children:
        for x in _walk(c):
            yield x


def _scan_overflow(rootw):
    """扫描可见控件的 req>actual 溢出 (裁剪信号), 返回按严重度排序的列表。"""
    out = []
    for wd in _walk(rootw):
        try:
            if not wd.winfo_ismapped():
                continue
        except Exception:
            continue
        try:
            cw, ch = wd.winfo_width(), wd.winfo_height()
            rw, rh = wd.winfo_reqwidth(), wd.winfo_reqheight()
        except Exception:
            continue
        if cw <= 0 or ch <= 0:
            continue
        ow, oh = rw - cw, rh - ch
        if ow > 1 or oh > 1:
            try:
                cls = wd.winfo_class()
            except Exception:
                cls = "?"
            txt = ""
            for opt in ("text", "title"):
                try:
                    v = wd.cget(opt)
                    if v:
                        txt = str(v)
                        break
                except Exception:
                    continue
            out.append({"cls": cls, "w": cw, "h": ch, "rw": rw, "rh": rh,
                        "ow": ow, "oh": oh, "text": txt[:36],
                        "score": max(ow, 0) + max(oh, 0)})
    out.sort(key=lambda d: d["score"], reverse=True)
    return out


def _widest(rootw, top=3):
    """返回子树内可见控件按 reqwidth 降序的 (reqw, class, text)。"""
    arr = []
    for wd in _walk(rootw):
        try:
            if not wd.winfo_ismapped():
                continue
            rw = wd.winfo_reqwidth()
            cls = wd.winfo_class()
        except Exception:
            continue
        txt = ""
        for opt in ("text", "title"):
            try:
                v = wd.cget(opt)
                if v:
                    txt = str(v)
                    break
            except Exception:
                continue
        arr.append((rw, cls, txt[:40]))
    arr.sort(key=lambda t: t[0], reverse=True)
    return arr[:top]


def _count_state_rows(card_widget):
    n = 0
    for wd in _walk(card_widget):
        try:
            if wd.winfo_class() in ("Label", "TLabel"):
                t = str(wd.cget("text"))
                if any(ch in t for ch in STATE_ICONS):
                    n += 1
        except Exception:
            pass
    return n


def t_live():
    emit("\n── B. 实机: 打开设置窗口并采集显示 ──")
    try:
        import tkinter
    except Exception as e:
        warn("tkinter 不可用, 跳过实机段: {!r}".format(e))
        return

    cb_errors = []
    try:
        import ACRPA
        import app
        app.build()
    except Exception as e:
        warn("构建主窗失败, 跳过实机段: {!r}".format(e))
        return

    root = ACRPA.root
    try:
        root.report_callback_exception = (
            lambda exc, val, tb: cb_errors.append(
                "".join(traceback.format_exception(exc, val, tb))))
    except Exception:
        pass

    root.deiconify()
    _pump(root)

    import state as state_mod
    import ui.settings.window as w
    import settings_window as sw  # 薄壳
    try:
        import capabilities
    except Exception:
        capabilities = None

    # ── 隔离: 备份 config.json; 复位内存几何以取默认 680 尺寸 ──
    cfg = getattr(state_mod, "CONFIG_PATH", os.path.join(ROOT, "config.json"))
    try:
        with open(cfg, "rb") as fh:
            cfg_backup = fh.read()
    except Exception:
        cfg_backup = None
    orig_geo = getattr(state_mod, "WIN_GEOMETRY", "")
    orig_dark = bool(getattr(state_mod, "DARK_MODE", False))
    state_mod.WIN_GEOMETRY = ""   # 使窗口按默认 680x680 居中打开 (确定性)

    try:
        sw.open_settings_window()
    except Exception as e:
        check(False, "open_settings_window() 未抛异常", repr(e))
        return
    _pump(root)

    win = getattr(w, "_win", None)
    check(win is not None and win.winfo_exists(), "设置窗口存在")
    if win is None or not win.winfo_exists():
        return

    # (1) 基本属性
    try:
        title = win.title()
    except Exception as e:
        title = "<err %r>" % (e,)
    check("设置" in str(title), "窗口标题含「设置」", repr(title))
    try:
        minw, minh = win.minsize()
    except Exception as e:
        minw = minh = "<err %r>" % (e,)
    check((minw, minh) == (580, 500), "minsize == (580, 500)",
          "({}, {})".format(minw, minh))
    w_actual, h_actual = win.winfo_width(), win.winfo_height()
    emit("[GEOM] 默认打开尺寸 = {}x{}".format(w_actual, h_actual))

    # (2) 导航 / 句柄
    nav_labels = getattr(w, "_nav_labels", {})
    check(isinstance(nav_labels, dict) and len(nav_labels) == 12,
          "导航项 12 个", "count={}".format(len(nav_labels) if nav_labels else nav_labels))
    nav_cards = getattr(w, "_nav_cards", [])
    check(isinstance(nav_cards, list) and len(nav_cards) == 12,
          "注册卡片句柄 12 张", "count={}".format(len(nav_cards) if nav_cards else nav_cards))
    if isinstance(nav_cards, list):
        got_keys = [k for k, _cw in nav_cards]
        check(got_keys == list(CARD_KEYS), "_nav_cards 键序 == _CARD_KEYS", str(got_keys))
    amap = getattr(w, "_apply_map", {})
    check(len(amap) == 11, "_apply_map 覆盖 11 卡 (无 quick)", "count={}".format(len(amap)))
    check("quick" not in amap, "_apply_map 不含 quick")

    # (3) 卡片 grid
    if isinstance(nav_cards, list):
        bad = []
        for k, cw in nav_cards:
            try:
                if cw.winfo_manager() != "grid":
                    bad.append(k)
            except Exception:
                bad.append(k)
        check(not bad, "12 张卡片均以 grid 布局", "非 grid: {}".format(bad))

    # (4) 画布滚动区
    canvas = getattr(w, "_nav_canvas", None)
    check(canvas is not None, "_nav_canvas 存在")
    inner_h = canvas_h = canvas_w = None
    inner = None
    if canvas is not None:
        try:
            bbox = canvas.bbox("all")
            inner_h = (bbox[3] - bbox[1]) if bbox else 0
            canvas_w = canvas.winfo_width()
            canvas_h = canvas.winfo_height()
            sr = canvas.cget("scrollregion")
        except Exception as e:
            sr = "<err %r>" % (e,)
        check(bool(inner_h and inner_h > 0),
              "画布 scrollregion 覆盖全部卡片内容",
              "inner_h={} scrollregion={}".format(inner_h, sr))
        check(bool(canvas_h and inner_h and inner_h > canvas_h),
              "内容高度 > 视口高度 (需要并可滚动)",
              "inner_h={} canvas_h={}".format(inner_h, canvas_h))
        try:
            item = canvas.find_withtag("inner")[0]
            inner = canvas.nametowidget(canvas.itemcget(item, "window"))
        except Exception as e:
            emit("       (取 inner frame 失败: {!r})".format(e))

    # (5) 各卡 reqwidth + 三档窗宽横向裁剪判定
    per = []
    if isinstance(nav_cards, list):
        for k, cw in nav_cards:
            try:
                per.append((k, cw.winfo_reqwidth()))
            except Exception:
                per.append((k, -1))
    per.sort(key=lambda t: t[1], reverse=True)
    emit("[WIDTH] 各卡 reqwidth (降序): " +
         ", ".join("{}={}".format(k, v) for k, v in per))

    # 实测各窗宽下的画布绘制宽
    def canvas_width_at(width):
        try:
            win.geometry("{}x680".format(width))
            _pump(root, n=3)
            return canvas.winfo_width()
        except Exception:
            return None

    measured = {}
    for width in (580, 680, 820):
        measured[width] = canvas_width_at(width)
    emit("[WIDTH] 各窗宽 → 画布绘制宽: " +
         ", ".join("{}→{}".format(k, v) for k, v in measured.items()))

    def clip_at(width):
        cwid = measured.get(width)
        if not cwid:
            return []
        return [(k, v) for k, v in per if v > cwid + 1]

    clip580 = clip_at(580)
    clip680 = clip_at(680)
    clip820 = clip_at(820)
    emit("[WIDTH] 裁剪(卡req>画布宽) 580:{}".format(clip580))
    emit("[WIDTH] 裁剪(卡req>画布宽) 680:{}".format(clip680))
    emit("[WIDTH] 裁剪(卡req>画布宽) 820:{}".format(clip820))
    check(not clip680,
          "默认窗宽(680) 无横向裁剪",
          "裁剪卡: {}".format([k for k, _ in clip680]))
    if clip580:
        warn("最小窗宽(580) 存在横向裁剪 (设计文档 §P0 已记为「窄窗必然错位」): {}".format(
            [k for k, _ in clip580]))

    # (6) 定位最宽卡内最宽控件
    if isinstance(nav_cards, list) and per:
        cardmap = dict(nav_cards)
        for k, _v in per[:2]:
            cw = cardmap.get(k)
            if cw is None:
                continue
            emit("[WIDEST] 卡 {} 内最宽控件(可见): {}".format(k, _widest(cw, 3)))

    # 复位到默认 680 供后续采集
    try:
        win.geometry("680x680")
        _pump(root, n=3)
    except Exception:
        pass

    # (7) 「扩展」卡状态行
    ext_card = dict(nav_cards).get("extensions") if isinstance(nav_cards, list) else None
    if ext_card is not None:
        rows = _count_state_rows(ext_card)
        exp = 0
        if capabilities is not None:
            try:
                exp = len(capabilities.all_states())
            except Exception:
                exp = 0
        check(rows >= 1, "「扩展」卡渲染状态行 (四态图标)", "icon_labels={}".format(rows))
        check(exp == 0 or rows >= exp,
              "「扩展」卡状态行数 >= 能力数",
              "rows={} capabilities={}".format(rows, exp))
    else:
        check(False, "定位到「扩展」卡控件")

    # (8) 主题往返换肤
    sample = nav_labels.get("extensions") if isinstance(nav_labels, dict) else None
    if sample is not None:
        try:
            bg_before = str(sample.cget("bg"))
        except Exception as e:
            bg_before = "<err %r>" % (e,)
        try:
            ACRPA.toggle_dark()
            _pump(root)
            bg_dark = str(sample.cget("bg"))
            check(bg_before != bg_dark,
                  "切换主题后导航项底色变化 (设置窗跟随换肤)",
                  "{} -> {}".format(bg_before, bg_dark))
            ACRPA.toggle_dark()
            _pump(root)
            bg_after = str(sample.cget("bg"))
            check(bg_after == bg_before,
                  "主题切回后导航项底色复原",
                  "{} -> {}".format(bg_dark, bg_after))
        except Exception as e:
            check(False, "主题往返换肤未抛异常", repr(e))
    else:
        check(False, "定位到导航项用于换肤采样")

    # (9) 点击导航滚动 + 高亮
    if canvas is not None and sample is not None:
        try:
            yv_before = canvas.yview()
        except Exception:
            yv_before = None
        try:
            w._on_nav_click("extensions")
            _pump(root, n=3)
            active = getattr(w, "_nav_active_key", None)
            check(active == "extensions", "点击导航后 active=extensions", repr(active))
            yv_after = canvas.yview()
            check(yv_before is None or (yv_after != yv_before or yv_after[0] > 0.0),
                  "点击导航滚动到目标卡", "yview {} -> {}".format(yv_before, yv_after))
            hl = str(sample.cget("bg"))
            C = getattr(w, "C", {})
            check(hl == str(C.get("acl", hl)),
                  "导航项被高亮 (bg == C['acl'])", "bg={} acl={}".format(hl, C.get("acl")))
        except Exception as e:
            check(False, "导航点击未抛异常", repr(e))

    # (10) 溢出扫描 (设置窗子树)
    over = _scan_overflow(win)
    emit("[OVERFLOW] 设置窗子树 req>actual 命中={} 条".format(len(over)))
    for i, d in enumerate(over[:10]):
        emit("[OVERFLOW] #{:<2} cls={:<8} actual={}x{} req={}x{} over=({:+d},{:+d}) text={!r}"
             .format(i + 1, d["cls"], d["w"], d["h"], d["rw"], d["rh"],
                     d["ow"], d["oh"], d["text"]))
    if over:
        warn("设置窗存在 {} 处 req>actual 溢出 (多为横向裁剪的从属控件)".format(len(over)))

    # (11) Tk 回调异常
    check(not cb_errors, "打开/刷新设置窗期间无 Tk 回调异常",
          (cb_errors[0].strip().splitlines()[-1] if cb_errors else ""))

    # (12) 截图
    shot = os.path.join(OUTDIR, "settings_window.png")
    try:
        from PIL import ImageGrab
        x, y = win.winfo_rootx(), win.winfo_rooty()
        bbox = (x, y, x + win.winfo_width(), y + win.winfo_height())
        img = ImageGrab.grab(bbox=bbox)
        img.save(shot)
        check(os.path.exists(shot), "设置窗截图落盘", "{} size={}".format(shot, img.size))
    except Exception as e:
        warn("截图 SKIP/FAIL: {!r}".format(e))

    # ── 收尾: 直接销毁 (不经 _close, 避免落盘几何) ──
    try:
        win.destroy()
        _pump(root, n=2)
    except Exception as e:
        emit("       (win.destroy 失败: {!r})".format(e))
    try:
        w._win = None
    except Exception:
        pass

    # ── 复位内存状态 + 恢复 config.json 备份 ──
    try:
        state_mod.WIN_GEOMETRY = orig_geo
        if bool(getattr(state_mod, "DARK_MODE", False)) != orig_dark:
            ACRPA.toggle_dark()
    except Exception:
        pass
    try:
        if cfg_backup is not None:
            with open(cfg, "wb") as fh:
                fh.write(cfg_backup)
        emit("[CLEANUP] config.json 已还原 (隔离完成)")
    except Exception as e:
        warn("config.json 还原失败: {!r}".format(e))


def main():
    emit("=== ACRPA 设置窗口 界面显示测试 ===")
    emit("cwd(py): %s" % os.getcwd())
    emit("src 已加入 sys.path")

    t_static()
    try:
        t_live()
    except Exception:
        check(False, "实机段未抛未捕获异常", "见下 traceback")
        _lines.append(traceback.format_exc())
        print(traceback.format_exc())

    emit("-" * 64)
    emit("=== 汇总 ===")
    emit("[SUM] PASS={} FAIL={} WARN={}".format(len(_PASS), len(_FAIL), len(_WARN)))
    if _FAIL:
        for m in _FAIL:
            emit("[SUM][FAIL] {}".format(m))
    if _WARN:
        for m in _WARN:
            emit("[SUM][WARN] {}".format(m))
    emit("=== 结果: {} ===".format("FAIL" if _FAIL else "OK"))

    with open(REPORT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(_lines) + "\n")
    emit("[REPORT] {}".format(REPORT))

    try:
        import ACRPA
        ACRPA.root.destroy()
    except Exception:
        pass
    return 1 if _FAIL else 0


if __name__ == "__main__":
    try:
        rc = main()
    except Exception:
        traceback.print_exc()
        rc = 1
    sys.exit(rc)
