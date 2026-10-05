# -*- coding: utf-8 -*-
"""Mini Bar 运行时几何诊断 (不改生产代码)。

真实创建 ACRPA 的 Mini Bar (Toplevel) 并对三形态做运行时内省:
  1. 顶层 winfo_height/reqheight/geometry/winfo_x/y
  2. 递归子控件树: class / x / y / w / h / reqw / reqh / bg / pack_info
  3. 内容 bbox 与 上/下/左/右 空白像素数
  4. 窗口实际高度 vs 配置高度; 内容 reqheight 是否超出窗口
  5. state.MINI_BAR_HEIGHT 实际运行值 + UI 缩放/DPI 对按钮字体 pt 的影响
  6. PIL ImageGrab 截图保存到 F:\\（8）Desktop\\debug-acrpa
  7. 基于像素的空白行/列检测 (与几何 bbox 互为印证)

用法:
    python tools/_debug_mini_bar_geometry.py
退出码 0 (纯诊断, 不判失败)。
"""
import os
import sys
import time
import ctypes

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
_SRC = os.path.join(ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

SHOT_DIR = u"F:\\（8）Desktop\\debug-acrpa"
BAR_POS = (150, 150)          # 诊断时固定摆位, 便于截图
FORMS = ("compact", "run", "icon")
FORM_CN = {"compact": u"紧凑", "run": u"运行", "icon": u"图标"}


def hr(title=""):
    print("\n" + "=" * 72)
    if title:
        print(title)
    print("=" * 72)


# ── 像素工具 ──────────────────────────────────────────────────────────
def _hex2rgb(s):
    s = (s or "#ffffff").lstrip("#")
    try:
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    except Exception:
        return (255, 255, 255)


def pump(root, seconds=0.7):
    """驱动 Tk 事件循环 (跑完 after 链: 宽度动画/布局), 不进入 mainloop。"""
    end = time.time() + seconds
    while time.time() < end:
        try:
            root.update()
        except Exception:
            pass
        time.sleep(0.01)


# ── 控件树内省 ────────────────────────────────────────────────────────
def dump_tree(root_widget, top):
    """递归打印控件树 (相对顶层窗口的绝对坐标)。"""
    rows = []

    def walk(w, depth):
        try:
            cls = w.winfo_class()
            name = type(w).__name__
            ax = w.winfo_rootx() - top.winfo_rootx()
            ay = w.winfo_rooty() - top.winfo_rooty()
            info = {
                "depth": depth, "name": name, "cls": cls,
                "x": ax, "y": ay,
                "w": w.winfo_width(), "h": w.winfo_height(),
                "reqw": w.winfo_reqwidth(), "reqh": w.winfo_reqheight(),
                "mapped": bool(w.winfo_ismapped()),
                "bg": "", "pack": "", "text": "",
            }
            try:
                info["bg"] = str(w.cget("bg"))
            except Exception:
                info["bg"] = "-"
            try:
                info["text"] = str(w.cget("text"))[:18]
            except Exception:
                info["text"] = ""
            try:
                pi = w.pack_info()
                info["pack"] = "side={} fill={} padx={} pady={}".format(
                    pi.get("side"), pi.get("fill"), pi.get("padx"), pi.get("pady"))
            except Exception:
                info["pack"] = "(无 pack)"
            rows.append(info)
        except Exception as e:
            rows.append({"depth": depth, "name": "?", "cls": "?",
                         "x": 0, "y": 0, "w": 0, "h": 0, "reqw": 0, "reqh": 0,
                         "mapped": False, "bg": "?", "pack": "err:{}".format(e),
                         "text": ""})
        try:
            for ch in w.winfo_children():
                walk(ch, depth + 1)
        except Exception:
            pass

    walk(root_widget, 0)
    return rows


def print_tree(rows):
    hdr = "  {:>2} {:<14} {:<10} {:>5} {:>5} {:>5} {:>5} {:>7} {:<9} {:<34} {}".format(
        "#", "类/类型", "cls", "x", "y", "w", "h", "reqh", "映射", "pack(side/fill/pad)", "bg")
    print(hdr)
    for i, r in enumerate(rows):
        print("  {:>2} {:<14} {:<10} {:>5} {:>5} {:>5} {:>5} {:>7} {:<9} {:<34} {}".format(
            i, r["name"], r["cls"], r["x"], r["y"], r["w"], r["h"], r["reqh"],
            "Y" if r["mapped"] else "n", r["pack"], r["bg"] + (" " + r["text"] if r["text"] else "")))


def content_bbox(rows):
    """内容 bbox: 只统计已映射且 w/h>0 的控件 (含 inner/outer)。"""
    sel = [r for r in rows if r["mapped"] and r["w"] > 0 and r["h"] > 0]
    if not sel:
        return None
    left = min(r["x"] for r in sel)
    top = min(r["y"] for r in sel)
    right = max(r["x"] + r["w"] for r in sel)
    bottom = max(r["y"] + r["h"] for r in sel)
    return {"left": left, "top": top, "right": right, "bottom": bottom}


def content_bbox_semantic(rows, exclude_cls=("Frame",)):
    """语义内容 bbox: 排除容器 Frame (outer/inner/sep), 只看真正承载信息/交互的叶子控件。
    sep1 为分隔线也算内容 (Frame), 故仅排除 outer/inner (由调用方按名字剔除)。"""
    sel = [r for r in rows if r["mapped"] and r["w"] > 0 and r["h"] > 0
           and r["name"] not in ("Frame",)]
    if not sel:
        return None
    return {"left": min(r["x"] for r in sel),
            "top": min(r["y"] for r in sel),
            "right": max(r["x"] + r["w"] for r in sel),
            "bottom": max(r["y"] + r["h"] for r in sel)}


# ── 截图 + 像素空白检测 ───────────────────────────────────────────────
def grab_bar(tag):
    """抓取 Mini Bar 区域 -> 保存 PNG; 返回 (cropped_image, note)。"""
    try:
        import PIL.ImageGrab as IG
    except Exception as e:
        return None, "PIL/ImageGrab 不可用: {}".format(e)
    try:
        os.makedirs(SHOT_DIR, exist_ok=True)
    except Exception as e:
        return None, "无法创建截图目录 {}: {}".format(SHOT_DIR, e)
    A = sys.modules.get("ACRPA")
    mb = getattr(A, "_mini_bar", None)
    if mb is None:
        return None, "Mini Bar 不存在"
    u = ctypes.windll.user32
    vx = u.GetSystemMetrics(76)   # SM_XVIRTUALSCREEN
    vy = u.GetSystemMetrics(77)   # SM_YVIRTUALSCREEN
    vw = u.GetSystemMetrics(78)   # SM_CXVIRTUALSCREEN
    vh = u.GetSystemMetrics(79)   # SM_CYVIRTUALSCREEN
    try:
        full = IG.grab(all_screens=True)
    except Exception as e:
        return None, "grab(all_screens=True) 失败: {}".format(e)
    r = float(full.width) / max(1, vw)
    x, y = mb.winfo_rootx(), mb.winfo_rooty()
    w, h = mb.winfo_width(), mb.winfo_height()
    box = (int(round((x - vx) * r)), int(round((y - vy) * r)),
           int(round((x - vx + w) * r)), int(round((y - vy + h) * r)))
    try:
        crop = full.crop(box)
    except Exception as e:
        return None, "crop 失败 {}: {}".format(box, e)
    path = os.path.join(SHOT_DIR, "diag-minibar-{}.png".format(FORM_CN.get(tag, tag)))
    try:
        crop.save(path)
    except Exception as e:
        return None, "保存失败 {}: {}".format(path, e)
    note = "已保存 {} (virtual {}x{}, ratio={:.3f}, box={})".format(
        path, vw, vh, r, box)
    return crop, note


def pixel_blank(img, bg_hex):
    """检测整行/整列是否全为条底色 -> 上下/左右空白像素 (视觉客观证据)。"""
    if img is None:
        return None
    rgb = img.convert("RGB")
    px = rgb.load()
    W, H = rgb.size
    tgt = _hex2rgb(bg_hex)
    tol = 8

    def same(c):
        return (abs(c[0] - tgt[0]) <= tol and abs(c[1] - tgt[1]) <= tol
                and abs(c[2] - tgt[2]) <= tol)

    def row_blank(y):
        for x in range(W):
            if not same(px[x, y]):
                return False
        return True

    def col_blank(x):
        for y in range(H):
            if not same(px[x, y]):
                return False
        return True

    top = 0
    while top < H and row_blank(top):
        top += 1
    bottom = 0
    while bottom < H - top and row_blank(H - 1 - bottom):
        bottom += 1
    left = 0
    while left < W and col_blank(left):
        left += 1
    right = 0
    while right < W - left and col_blank(W - 1 - right):
        right += 1
    return {"img_w": W, "img_h": H, "blank_top": top, "blank_bottom": bottom,
            "blank_left": left, "blank_right": right, "bg": bg_hex}


# ── 形态切换 ──────────────────────────────────────────────────────────
def set_form(A, form):
    import state
    A._mb_form = None            # 强制不早退
    A._mb_last_interact = time.time()
    if form == "compact":
        state.recording = False
        state.running = False
        state.pause_event.set()
    elif form == "run":
        state.recording = False
        state.running = True
        state.pause_event.set()
        state.exec_state["total_rows"] = 10
        state.exec_state["row"] = 3
        state.exec_state["loop"] = 1
        state.exec_state["total_loops"] = 1
        state.exec_state["start_time"] = time.time() - 65
        try:
            A.engine._script_failed = False
        except Exception:
            pass
    elif form == "icon":
        state.recording = False
        state.running = False
        state.pause_event.set()
        A._mb_last_interact = time.time() - (A._MB_IDLE_SEC + 5)
    mb = A._mini_bar
    try:
        mb._sync_sig = None
    except Exception:
        pass
    try:
        import engine as _eng
        _eng.engine._script_failed = False
    except Exception:
        pass
    A._sync_mini_bar_status()
    A._mb_apply_form(force=True)


def ascii_viz(img, colors, rows_n=16, cols_n=86):
    """把截图降采样成 ASCII 图, 用于客观定位「空白」所在行/列。
    '.'=条底色(白), A=强调蓝, S=绿, X=红, W=琥珀, -=分隔灰, t/T=文字色, B=黑。"""
    if img is None:
        return None
    rgb = img.convert("RGB")
    W, H = rgb.size
    px = rgb.load()
    pal = [(".", _hex2rgb(colors.get("bgc", "#ffffff"))),
           ("A", _hex2rgb(colors.get("ac", "#2563eb"))),
           ("S", _hex2rgb(colors.get("sc", "#10b981"))),
           ("X", _hex2rgb(colors.get("dg", "#ef4444"))),
           ("W", _hex2rgb(colors.get("wn", "#f59e0b"))),
           ("-", _hex2rgb(colors.get("bd", "#e2e8f0"))),
           ("t", _hex2rgb(colors.get("fgm", "#718096"))),
           ("T", _hex2rgb(colors.get("fgt", "#1a202c"))),
           ("B", (0, 0, 0))]

    def classify(c):
        best, bd = "?", 1e18
        for ch, crgb in pal:
            d = ((c[0] - crgb[0]) ** 2 + (c[1] - crgb[1]) ** 2
                 + (c[2] - crgb[2]) ** 2)
            if d < bd:
                bd, best = d, ch
        return best

    sy = max(1, H // rows_n)
    sx = max(1, W // cols_n)
    out = []
    for y0 in range(0, H, sy):
        yc = min(H - 1, y0 + sy // 2)
        line = []
        for x0 in range(0, W, sx):
            xc = min(W - 1, x0 + sx // 2)
            line.append(classify(px[xc, yc]))
        out.append((y0, "".join(line)))
    return out, sx, sy


def reset_state():
    import state
    state.running = False
    state.recording = False
    state.pause_event.set()
    state.exec_state["total_rows"] = 0
    state.exec_state["row"] = 0


# ── 主流程 ────────────────────────────────────────────────────────────
def main():
    hr(u"ACRPA Mini Bar 运行时几何诊断 (真实建窗)")
    import state
    state.load_config()
    # 诊断期间禁用 NetLink (避免占端口/起后台线程干扰观测)
    state.NETLINK_ENABLED = False
    state.NETLINK_WEB_ENABLED = False
    state.CHECK_UPDATE = False   # 避免后台更新检查 import requests 抛异常污染输出
    # 严禁改写用户配置: _destroy_mini_bar() → _mb_save_pos() → state.save_config()
    # 会把诊断期的临时状态(netlink_enabled/mini_bar_pos/...)落盘, 故此处禁用落盘。
    state.save_config = lambda: None
    _orig_load = state.load_config
    state.load_config = lambda: None
    try:
        import ACRPA
        import app
        app.build()   # 入口拆分后: import ACRPA 不再建窗, 需显式构建
    finally:
        state.load_config = _orig_load
    import utils

    print(u"[配置实测值]")
    print("  state.MINI_BAR_HEIGHT = {}   (config.json 实测值)".format(state.MINI_BAR_HEIGHT))
    print("  state.MINI_BAR_WIDTH  = {}".format(state.MINI_BAR_WIDTH))
    print("  state.MINI_BAR_OPACITY= {}".format(state.MINI_BAR_OPACITY))
    print("  state.UI_SCALE        = {}".format(getattr(state, "UI_SCALE", None)))
    print("  state.MINI_BAR_POS    = {}".format(getattr(state, "MINI_BAR_POS", "")))
    print("  state.DARK_MODE       = {}".format(getattr(state, "DARK_MODE", None)))
    print("  state.CONFIG_PATH     = {}".format(state.CONFIG_PATH))
    print("  state.folded          = {}".format(state.folded))

    print(u"\n[UI 缩放 / DPI 对字体 pt 的影响]")
    print("  utils.current_ui_scale() = {}".format(utils.current_ui_scale()))
    print("  utils.dpi_factor()       = {}".format(utils.dpi_factor()))
    print("  utils.fit_pt(9)          = {}  (ACRPA_BUTTON 基准 9pt)".format(utils.fit_pt(9)))
    print("  utils.fit_pt(8)          = {}".format(utils.fit_pt(8)))
    try:
        sc = ACRPA.root.tk.call("tk", "scaling")
        print("  tk scaling (px/pt)       = {}".format(sc))
        print("  root.winfo_fpixels('1i') = {}".format(ACRPA.root.winfo_fpixels("1i")))
    except Exception as e:
        print("  tk scaling 取值失败: {}".format(e))
    try:
        import tkinter.font as tkfont
        for role in ("ACRPA_BUTTON", "ACRPA_LOG", "ACRPA_ICON_MD"):
            a = tkfont.nametofont(role).actual()
            print("  命名{} 实际: family={} size={} (pt) weight={}".format(
                role, a.get("family"), a.get("size"), a.get("weight")))
    except Exception as e:
        print("  命名字体取值失败: {}".format(e))

    root = ACRPA.root
    root.withdraw()
    reset_state()
    ACRPA._mb_last_interact = time.time()
    ACRPA._create_mini_bar()
    mb = ACRPA._mini_bar
    mb.geometry("+{}+{}".format(BAR_POS[0], BAR_POS[1]))
    pump(root, 0.5)

    summary = []
    for form in FORMS:
        hr(u"形态: {} ({})".format(form, FORM_CN[form]))
        set_form(ACRPA, form)
        mb.geometry("+{}+{}".format(BAR_POS[0], BAR_POS[1]))
        pump(root, 0.9)

        win_w, win_h = mb.winfo_width(), mb.winfo_height()
        cfg_w = int(state.MINI_BAR_WIDTH)
        cfg_h = int(state.MINI_BAR_HEIGHT)
        print(u"[顶层窗口]")
        print("  geometry()      = {}".format(mb.geometry()))
        print("  winfo_width/height  = {} x {}".format(win_w, win_h))
        print("  winfo_reqwidth/reqheight = {} x {}".format(
            mb.winfo_reqwidth(), mb.winfo_reqheight()))
        print("  winfo_rootx/rooty   = {}, {}".format(mb.winfo_rootx(), mb.winfo_rooty()))
        print("  winfo_x/y           = {}, {}".format(mb.winfo_x(), mb.winfo_y()))
        print(u"  → 窗口高是否等于配置高({})? {}".format(
            cfg_h, "是" if win_h == cfg_h else u"否! (差 {})".format(win_h - cfg_h)))

        rows = dump_tree(mb, mb)
        print(u"\n[子控件树]")
        print_tree(rows)

        bb = content_bbox(rows)
        sb = content_bbox_semantic(rows)
        if bb:
            print(u"\n[内容 bbox — 全部映射控件]")
            print("  left={left} top={top} right={right} bottom={bottom}".format(**bb))
            print(u"  空白: 上={} 下={} 左={} 右={}  (窗口 {}x{})".format(
                bb["top"], win_h - bb["bottom"], bb["left"], win_w - bb["right"],
                win_w, win_h))
            print(u"  内容高度 = {}px / 窗口高 {}px → 上下合计多余 {}px".format(
                bb["bottom"] - bb["top"], win_h,
                win_h - (bb["bottom"] - bb["top"])))
        if sb:
            print(u"[语义内容 bbox — 排除容器 Frame(outer/inner)]")
            print("  left={left} top={top} right={right} bottom={bottom}".format(**sb))
            print(u"  空白: 上={} 下={} 左={} 右={}".format(
                sb["top"], win_h - sb["bottom"], sb["left"], win_w - sb["right"]))

        # 每个已 pack 的子控件: 与窗口高的垂直关系
        print(u"\n[子控件垂直占位 vs 窗口高 {}]".format(win_h))
        for r in rows[1:]:
            if r["mapped"]:
                print(u"    {:<12} y={:>3} h={:>3} reqh={:>3}  (上留 {} / 下留 {})".format(
                    r["name"], r["y"], r["h"], r["reqh"], r["y"],
                    win_h - (r["y"] + r["h"])))

        # 内容 reqheight 是否超窗口
        over = [r for r in rows if r["mapped"] and r["reqh"] > win_h]
        print(u"\n[假设验证] 内容 reqheight > 窗口高 的控件: {}".format(
            ", ".join("{}(reqh={})".format(r["name"], r["reqh"]) for r in over) or u"无"))

        img, note = grab_bar(form)
        print(u"\n[截图] {}".format(note))
        pb = pixel_blank(img, ACRPA.C["bgc"])
        if pb:
            print(u"[像素空白检测 — 底色 {}]  图像 {}x{}".format(
                pb["bg"], pb["img_w"], pb["img_h"]))
            print(u"  纯底色行: 上 {} px / 下 {} px ; 纯底色列: 左 {} px / 右 {} px".format(
                pb["blank_top"], pb["blank_bottom"], pb["blank_left"], pb["blank_right"]))
        viz = ascii_viz(img, ACRPA.C)
        if viz:
            vlines, sx, sy = viz
            print(u"[ASCII 像素可视化] ('.'=条底色/空白 A=蓝 S=绿 X=红 W=琥珀 -=灰 B=黑)"
                  u"  {}px/字符 {}px/行".format(sx, sy))
            for y0, ln in vlines:
                print("   y={:>3} |{}|".format(y0, ln))

        summary.append({
            "form": form, "win": (win_w, win_h), "cfg": (cfg_w, cfg_h),
            "bbox": bb, "sb": sb, "pb": pb,
            "hi": next((r["h"] for r in rows if r["name"] == "Label"
                        and "停止" in r["text"]), None),
        })

    # ── UI 缩放压力测试: 证明「字体被缩放放大时条高自适应, 而不是内容被裁剪」──
    hr(u"UI 缩放压力测试 (UI_SCALE 1.1 → 1.5, 字体随缩放放大)")
    try:
        utils.set_ui_scale(1.5)
        utils.init_fonts(root)
        reset_state()
        ACRPA._mb_last_interact = time.time()
        ACRPA._mb_form = None
        try:
            mb._sync_sig = None
        except Exception:
            pass
        ACRPA._sync_mini_bar_status()
        ACRPA._mb_apply_form(force=True)
        mb.geometry("+{}+{}".format(BAR_POS[0], BAR_POS[1]))
        pump(root, 0.9)
        print(u"  ACRPA_BUTTON linespace 反推内容区高 _mb_content_height() = {}".format(
            ACRPA._mb_content_height()))
        print(u"  自适应有效高 _mb_effective_height() = {}  (配置 MINI_BAR_HEIGHT={})".format(
            ACRPA._mb_effective_height(), state.MINI_BAR_HEIGHT))
        print(u"  窗口 {}x{} ; 顶层需求 {}x{}".format(
            mb.winfo_width(), mb.winfo_height(),
            mb.winfo_reqwidth(), mb.winfo_reqheight()))
        over = mb.winfo_reqheight() > mb.winfo_height()
        print(u"  → 是否发生内容裁剪(reqheight > height)? {}".format(
            u"是(异常)" if over else u"否(自适应生效)"))
        print(u"  → 条高是否随缩放自适应当高(height > 配置)? {}".format(
            u"是(已抬高)" if mb.winfo_height() > int(state.MINI_BAR_HEIGHT) else u"否"))
        img150, note150 = grab_bar("scale150")
        print(u"  [截图] {}".format(note150))
    finally:
        utils.set_ui_scale(1.1)
        utils.init_fonts(root)
        reset_state()

    hr(u"汇总对照表")
    print(u"{:<8} {:>12} {:>10} {:>10} {:>10} {:>9}".format(
        u"形态", u"窗口WxH", u"内容bbox高", u"上留白", u"下留白", u"右留白"))
    for s in summary:
        bb, pb = s["bbox"], s["pb"]
        ch = (bb["bottom"] - bb["top"]) if bb else 0
        up = bb["top"] if bb else -1
        dn = (s["win"][1] - bb["bottom"]) if bb else -1
        rt = (s["win"][0] - bb["right"]) if bb else -1
        print(u"{:<8} {:>12} {:>10} {:>10} {:>10} {:>9}".format(
            FORM_CN[s["form"]], "{}x{}".format(*s["win"]), ch, up, dn, rt))
    print(u"\n(注: bbox 基于控件几何; 像素行检测另见各形态 [像素空白检测])")

    reset_state()
    try:
        ACRPA._destroy_mini_bar()
    except Exception:
        pass
    try:
        root.destroy()
    except Exception:
        pass
    print(u"\n诊断完成。截图目录: {}".format(SHOT_DIR))
    return 0


if __name__ == "__main__":
    try:
        rc = main()
    except Exception:
        import traceback
        traceback.print_exc()
        rc = 1
    sys.stdout.flush()
    os._exit(rc)
