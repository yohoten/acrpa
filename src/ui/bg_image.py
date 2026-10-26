# -*- coding: utf-8 -*-
"""bg_image.py — 窗口背景图加载与"伪半透明"合成（阶段二 UI 层）。

背景：Tk 控件没有通用 alpha，tk.Text / ttk.Treeview / Frame 的背景永远不透明，
对 toplevel 用 `wm_attributes("-alpha", ...)` 会把文字一起变透明（不可用）。
因此「40% 透明的背景图」只能由 Pillow 预合成：
    ico → convert("RGBA")
        → 排布到目标像素尺寸（默认 mode="contain"：裁掉四周全透明留白后
          **单图居中、绝不放大**，只有区域比原图小才等比缩小；
          mode="cover" = Cover 缩放裁剪；mode="tile" = 原尺寸 1:1 平铺）
        → 整体 alpha 压到指定比例（保留图标自身透明区域）
        → 与主题底色 alpha_composite 成一张**不透明**位图
        → ImageTk.PhotoImage（调用方用 `widget._photo = photo` 保活）

对外接口：
    load_bg_photo(res_dir, name="bg-ac.ico", alpha=0.4, bg_color="#FFFFFF",
                  size=None, master=None, tile=True) -> PhotoImage | None
    resolve_icon_path(res_dir, name) -> str          # 资源解析（存在才返回）
    clear_cache()                                    # 主题切换后清缓存

约定（与 src/ACRPA.py 的 _load_pil / src/mini_bar.py 一致）：
  · PIL 惰性 import；不可用时所有接口返回 None，**绝不抛异常**（静默降级，
    回退为纯色背景，不阻断启动）；
  · 相同 (path, size, alpha, bg_color) 的合成结果走小缓存，避免重复合成。
"""
import os
import sys

# PIL 惰性加载（不要在 import 期引入重型依赖）
Image = None
ImageTk = None
_PIL_LOADED = False


def _load_pil():
    """惰性装载 PIL 的 Image / ImageTk；不可用返回 False（不抛异常）。"""
    global Image, ImageTk, _PIL_LOADED
    if _PIL_LOADED and Image is not None and ImageTk is not None:
        return True
    try:
        from PIL import Image as _Img, ImageTk as _ITk
        Image, ImageTk = _Img, _ITk
        _PIL_LOADED = True
        return True
    except Exception:
        Image = None
        ImageTk = None
        _PIL_LOADED = False
        return False


# 合成结果缓存: key = (path, w, h, alpha, bg_color, mode) -> PhotoImage
_cache = {}
_CACHE_MAX = 8

# 最近一次失败原因：供调用方写日志（打包后无控制台，必须能自证原因）
_last_error = ""


def _set_error(msg):
    """记录最近一次失败原因（不抛异常）。"""
    global _last_error
    try:
        _last_error = str(msg or "")
    except Exception:
        _last_error = ""


def get_last_error():
    """最近一次加载/合成失败的原因（成功时为空字符串）。"""
    return _last_error


def clear_cache():
    """清空合成缓存（主题切换 / 底色变化后调用）。"""
    try:
        _cache.clear()
    except Exception:
        pass


def _name_variants(name):
    """同名资源的候选文件名（**PNG 优先**）。

    打包配置里 `PIL.IcoImagePlugin` 曾被 ACRPA.spec 的 excludes 裁掉，
    .ico 在冻结版里可能直接读不出来；因此优先找同名 .png
    （`PIL.PngImagePlugin` 已随包），其次才用原扩展名。
    """
    name = str(name or "").strip()
    if not name:
        return []
    base, ext = os.path.splitext(name)
    ext = ext.lower()
    if ext == ".ico":
        return [base + ".png", name]
    if ext == ".png":
        return [name, base + ".ico"]
    return [name + ".png", name, name + ".ico"]


def resolve_icon_path(res_dir, name="bg-ac.ico"):
    """解析图标真实路径（不存在返回 ""）；同名 .png 优先于 .ico。

    依次尝试：传入的 res_dir → 冻结态的 exe 同级 res / _MEIPASS/res
    → 源码树 <repo>/res（本文件位于 src/ui/，向上三级即仓库根）。
    res/ 目录已由 ACRPA.spec 的 datas 整目录打包，无需额外改 datas。
    """
    variants = _name_variants(name)
    if not variants:
        return ""
    dirs = []
    if res_dir:
        dirs.append(str(res_dir))
    try:
        if getattr(sys, "frozen", False):
            dirs.append(os.path.join(os.path.dirname(sys.executable), "res"))
            meipass = getattr(sys, "_MEIPASS", "")
            if meipass:
                dirs.append(os.path.join(meipass, "res"))
    except Exception:
        pass
    try:
        repo_root = os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))))
        dirs.append(os.path.join(repo_root, "res"))
    except Exception:
        pass
    # 按扩展名优先级扫一遍所有目录（PNG 优先），全部落空才算找不到
    for v in variants:
        for d in dirs:
            try:
                p = os.path.join(d, v)
                if os.path.isfile(p):
                    return p
            except Exception:
                continue
    return ""


def _open_rgba(path):
    """打开图片并转 RGBA（多帧 ico 取首帧 = 最大尺寸帧）。失败返回 None。"""
    if not _load_pil():
        _set_error("PIL 不可用（Image/ImageTk 导入失败）")
        return None
    try:
        img = Image.open(path)
    except Exception as e:
        _set_error("Image.open 失败: {!r} | path={}".format(e, path))
        return None
    try:
        # .ico 可能含多帧（多尺寸）；Pillow 的 IcoImagePlugin 默认已选最大帧，
        # 这里再兜底 seek(0)，避免某些分支拿到 16x16 小帧。
        n = int(getattr(img, "n_frames", 1) or 1)
        if n > 1:
            try:
                img.seek(0)
            except Exception:
                pass
        return img.convert("RGBA")
    except Exception:
        return None


def _cover_resize(img, size):
    """按 Cover 语义缩放并居中裁剪到 size（等同 CSS background-size: cover）。"""
    try:
        w, h = int(size[0]), int(size[1])
        if w <= 0 or h <= 0:
            return None
        sw, sh = img.size
        if sw <= 0 or sh <= 0:
            return None
        scale = max(float(w) / float(sw), float(h) / float(sh))
        nw = max(w, int(round(sw * scale)))
        nh = max(h, int(round(sh * scale)))
        img = img.resize((nw, nh), Image.LANCZOS)
        left = max(0, (nw - w) // 2)
        top = max(0, (nh - h) // 2)
        return img.crop((left, top, left + w, top + h))
    except Exception:
        return None


def _fit_no_upscale(img, size):
    """等比缩小到能放进 size（**绝不放大**）；原本就放得下则原样返回。

    原尺寸优先：只有区域比原图小时才缩放，避免位图被放大后发虚。
    """
    try:
        w, h = int(size[0]), int(size[1])
        sw, sh = img.size
        if w <= 0 or h <= 0 or sw <= 0 or sh <= 0:
            return None
        if sw <= w and sh <= h:
            return img
        scale = min(float(w) / float(sw), float(h) / float(sh))
        nw = max(1, int(round(sw * scale)))
        nh = max(1, int(round(sh * scale)))
        if nw == sw and nh == sh:
            return img
        return img.resize((nw, nh), Image.LANCZOS)
    except Exception:
        return None


def _paste_clipped(canvas, tile, x, y):
    """把 tile 贴到 canvas 的 (x, y)（越界部分自动裁剪，负坐标也安全）。"""
    try:
        cw, ch = canvas.size
        tw, th = tile.size
        x0, y0 = max(0, int(x)), max(0, int(y))
        x1, y1 = min(cw, int(x) + tw), min(ch, int(y) + th)
        if x1 <= x0 or y1 <= y0:
            return
        sx, sy = x0 - int(x), y0 - int(y)
        part = tile.crop((sx, sy, sx + (x1 - x0), sy + (y1 - y0)))
        canvas.paste(part, (x0, y0), part)
    except Exception:
        pass


def _trim_transparent(img):
    """裁掉四周「全透明留白」（像素保持 1:1，不缩放）。

    背景图（如 bg-ac.ico = 256x256）往往四周留了大片完全透明的空白
    （实测左 33px / 右 36px / 上 5px / 下 3px 全透明）。若不裁掉，平铺后
    每个磁贴之间会出现整片空白，且贴边显示的窄条几乎看不到任何图案。
    """
    try:
        bbox = img.getchannel("A").getbbox()
        if not bbox:
            return img
        if tuple(bbox) != (0, 0) + tuple(img.size):
            return img.crop(bbox)
        return img
    except Exception:
        return img


def _tile_to_size(img, size):
    """按**原尺寸 1:1 平铺**铺满 size（先裁透明留白；居中起始，边缘整块裁切，绝不放大）。"""
    if not _load_pil():
        return None
    try:
        w, h = int(size[0]), int(size[1])
    except Exception:
        return None
    if w <= 0 or h <= 0:
        return None
    tile = _fit_no_upscale(_trim_transparent(img), (w, h))
    if tile is None:
        return None
    tw, th = tile.size
    if tw <= 0 or th <= 0:
        return None
    try:
        canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    except Exception:
        return None
    # 居中起始：让平铺图案在区域内左右/上下对称，而不是从左上角硬起点
    start_x = -((w % tw) // 2) if w > tw else 0
    start_y = -((h % th) // 2) if h > th else 0
    y = start_y
    while y < h:
        x = start_x
        while x < w:
            _paste_clipped(canvas, tile, x, y)
            x += tw
        y += th
    return canvas


def _contain_center(img, size):
    """单图**居中**放入 size：先裁透明留白，只缩小不放大，四周留底色。

    即 CSS 的 `background-size: contain; background-position: center`，
    区域内**只出现一张图**（不平铺）。
    """
    if not _load_pil():
        return None
    try:
        w, h = int(size[0]), int(size[1])
    except Exception:
        return None
    if w <= 0 or h <= 0:
        return None
    tile = _fit_no_upscale(_trim_transparent(img), (w, h))
    if tile is None:
        return None
    try:
        canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    except Exception:
        return None
    _paste_clipped(canvas, tile, (w - tile.size[0]) // 2,
                   (h - tile.size[1]) // 2)
    return canvas


def load_bg_photo(res_dir, name="bg-ac.ico", alpha=0.4, bg_color="#FFFFFF",
                  size=None, master=None, mode="contain"):
    """把 res_dir/name 合成为「alpha 透明度叠加在 bg_color 上」的 PhotoImage。

    参数：
        res_dir   — 资源目录（如 ACRPA 的 RES_DIR；解析失败会走内置回退路径）
        name      — 图标文件名（默认 bg-ac.ico）
        alpha     — 背景图不透明度 0~1（0.4 = 40%）。按图标自身 alpha 相乘，
                    因此图标透明区域仍然透明。
        bg_color  — 主题底色（如 C["bgc"]），用于把半透明层压成不透明位图
        size      — 目标像素尺寸 (w, h)；None/非法则返回 None
        master    — 传给 ImageTk.PhotoImage 的 master（多 Tk 实例时建议传）
        mode      — 排布方式：
                    "contain"（默认）单图居中、绝不放大，只有区域更小才缩小；
                    "cover"  等比放大裁剪铺满（会放大，仅作兼容保留）；
                    "tile"   原尺寸 1:1 平铺（不平铺需求下请勿使用）

    返回 PhotoImage；任何一步失败返回 None（不抛异常）。
    """
    if not _load_pil():
        return None
    try:
        w, h = int(size[0]), int(size[1])
    except Exception:
        return None
    if w <= 1 or h <= 1:
        return None
    path = resolve_icon_path(res_dir, name)
    if not path:
        _set_error("找不到资源 {}（res_dir={}）".format(name, res_dir))
        return None
    try:
        a = float(alpha)
        a = 0.0 if a < 0 else (1.0 if a > 1 else a)
    except Exception:
        a = 0.4
    bg = str(bg_color or "#FFFFFF")
    key = (path, w, h, round(a, 3), bg, str(mode))
    try:
        hit = _cache.get(key)
        if hit is not None:
            return hit
    except Exception:
        pass

    try:
        img = _open_rgba(path)
        if img is None:
            return None
        if mode == "tile":
            img = _tile_to_size(img, (w, h))
        elif mode == "cover":
            img = _cover_resize(img, (w, h))
        else:
            img = _contain_center(img, (w, h))
        if img is None:
            return None
        # 40% 透明: 与图标自身的 alpha 相乘（保留挖空区域）
        if a < 1.0:
            try:
                ch = img.getchannel("A").point(
                    lambda v: int(round(v * a)))
                img.putalpha(ch)
            except Exception:
                return None
        # 与主题底色合成成不透明位图（Tk 才能直接显示）
        try:
            base = Image.new("RGBA", (w, h), bg)
        except Exception:
            base = Image.new("RGBA", (w, h), "#FFFFFF")
        out = Image.alpha_composite(base, img).convert("RGB")
        try:
            photo = (ImageTk.PhotoImage(out, master=master)
                     if master is not None else ImageTk.PhotoImage(out))
        except Exception:
            photo = None
        if photo is None:
            return None
    except Exception:
        return None

    try:
        if len(_cache) >= _CACHE_MAX:
            _cache.clear()
        _cache[key] = photo
    except Exception:
        pass
    return photo


def load_icon_photo(res_dir, name="bg-ac.ico", max_width=None, master=None):
    """加载为**单张图片**（不铺满、不平铺、绝不放大），保留 RGBA 由 Tk 混底色。

    用于把图标当作「一张图」嵌到正文里（如帮助窗口「联系与支持」章节末尾），
    因此不会像 load_bg_photo 那样生成与区域等大的画布。

    参数：
        res_dir   — 资源目录（如 ACRPA 的 RES_DIR；解析失败会走内置回退路径）
        name      — 图标文件名（默认 bg-ac.ico）
        max_width — 可选最大显示宽度(px)；仅当图片更宽时等比缩小，绝不放大
        master    — 传给 ImageTk.PhotoImage 的 master（多 Tk 实例时建议传）

    返回 PhotoImage；任何一步失败返回 None（不抛异常）。
    """
    if not _load_pil():
        return None
    path = resolve_icon_path(res_dir, name)
    if not path:
        _set_error("找不到资源 {}（res_dir={}）".format(name, res_dir))
        return None
    try:
        img = _trim_transparent(_open_rgba(path))
        if img is None:
            return None
        if max_width:
            try:
                mw = int(max_width)
            except Exception:
                mw = 0
            if mw > 1 and img.size[0] > mw:
                scale = float(mw) / float(img.size[0])
                nh = max(1, int(round(img.size[1] * scale)))
                img = img.resize((mw, nh), Image.LANCZOS)
        photo = (ImageTk.PhotoImage(img, master=master)
                 if master is not None else ImageTk.PhotoImage(img))
        return photo
    except Exception:
        return None
