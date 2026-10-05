# -*- coding: utf-8 -*-
"""cv_match_ext — ``cv.match`` 扩展提供者 (纯 Python + Pillow, 路线图 阶段二新增项③)。

本模块是**首个上线扩展**的功能主体: 用 Pillow 实现模板匹配, 为引擎 ``找图`` 系列
命令提供一个可选的「高精度匹配」落地路径 (无需真实 OpenCV wheel)。

硬约束 (与扩展管理器契约一致)
------------------------------
* **纯 Python**, 仅依赖已内置的 Pillow; 模块 import 期**不** import ``cv2`` /
  ``tkinter`` / ``pyautogui`` / ``engine`` —— 保证 ``extensions.provider`` 导入时零副作用;
* 需要的 Pillow 子模块一律**函数内惰性 import**;
* ``match`` 对任何文件/解码/计算失败**返回 None 而非抛异常** (失败开放);
* **无 numpy 也能工作** (纯 Python 逐像素计算; 测试用图较小, 代价可接受)。

对外 API
--------
    match(screen_img, template_path, confidence=0.96, region=None, grayscale=True)
        -> (x, y, score) | None
        screen_img: None(截屏) | PIL.Image | 路径;  template_path: PIL.Image | 路径
        region: (left, top, width, height) 屏幕限定区域 (可选)
    self_check() -> (ok: bool, reason: str)
"""

__all__ = ["match", "self_check"]

_DEFAULT_CONFIDENCE = 0.96


def _is_image(obj):
    """是否 PIL.Image 形对象 (轻量 duck-typing, 不 import PIL)。"""
    return hasattr(obj, "convert") and hasattr(obj, "size")


def _load_image(src):
    """src: None(截屏) | PIL.Image | 路径 → PIL.Image; 失败返回 None。"""
    if src is None:
        try:
            from PIL import ImageGrab          # 惰性: 仅截屏路径才加载
            return ImageGrab.grab().convert("RGB")
        except Exception:                      # noqa: BLE001
            return None
    if _is_image(src):
        return src
    try:
        from PIL import Image                  # 惰性
        return Image.open(str(src))
    except Exception:                          # noqa: BLE001
        return None


def _channels(img, grayscale):
    """→ (width, height, [channel_values...])。灰度 1 通道, 彩色 3 通道。"""
    if grayscale:
        g = img.convert("L")
        return int(g.width), int(g.height), [list(g.getdata())]
    c = img.convert("RGB")
    data = list(c.getdata())
    return int(c.width), int(c.height), [
        [p[0] for p in data], [p[1] for p in data], [p[2] for p in data]]


def _best(scr_ch, sw, sh, tpl_ch, tw, th, x0, y0, x1, y1):
    """在 screen[x0:x1, y0:y1] 内滑窗做归一化互相关 (NCC)。

    → (score, ox, oy) 或 None。纯 Python, 逐通道累加; 无 numpy 依赖。
    """
    n = tw * th
    inv_n = 1.0 / n
    nch = len(tpl_ch)
    tms = []
    sq_total = 0.0
    for tc in tpl_ch:
        tm = sum(tc) * inv_n
        ss = 0.0
        for v in tc:
            d = v - tm
            ss += d * d
        tms.append(tm)
        sq_total += ss
    if sq_total <= 1e-9:
        return None                            # 模板近似纯色 → 无法可靠匹配
    import math
    norm_t = math.sqrt(sq_total)

    best = None
    y_last = y1 - th
    x_last = x1 - tw
    if y_last < y0 or x_last < x0:
        return None
    for oy in range(y0, y_last + 1):
        row_off = oy * sw
        for ox in range(x0, x_last + 1):
            dot = 0.0
            ss_s = 0.0
            for c in range(nch):
                tc = tpl_ch[c]
                sc = scr_ch[c]
                tm = tms[c]
                s = 0.0
                sq = 0.0
                ts = 0.0
                for ky in range(th):
                    base_s = row_off + ky * sw + ox
                    base_t = ky * tw
                    for kx in range(tw):
                        vs = sc[base_s + kx]
                        vt = tc[base_t + kx]
                        s += vs
                        sq += vs * vs
                        ts += vt * vs
                dot += ts - tm * s
                ss_s += sq - s * s * inv_n
            if ss_s <= 1e-9:
                continue
            score = dot / (norm_t * math.sqrt(ss_s))
            if best is None or score > best[0]:
                best = (score, ox, oy)
    return best


def match(screen_img, template_path, confidence=_DEFAULT_CONFIDENCE,
          region=None, grayscale=True):
    """Pillow 模板匹配 → (x, y, score) | None。

    任何失败 (文件不存在 / 解码失败 / 参数非法) 一律返回 None, 不抛异常。
    ``confidence`` 为命中阈值 (NCC 分数需 >= 阈值); 越界/非法回退 0.96。
    """
    try:
        scr = _load_image(screen_img)
        if scr is None:
            return None
        tpl = _load_image(template_path)
        if tpl is None:
            return None

        try:
            conf = float(confidence)
        except Exception:                       # noqa: BLE001
            conf = _DEFAULT_CONFIDENCE
        if conf > 1.0 or conf < -1.0:
            conf = _DEFAULT_CONFIDENCE

        sw, sh, scr_ch = _channels(scr, bool(grayscale))
        tw, th, tpl_ch = _channels(tpl, bool(grayscale))
        if tw <= 0 or th <= 0 or tw > sw or th > sh:
            return None

        x0, y0, x1, y1 = 0, 0, sw, sh
        if region:
            try:
                l = int(region[0]); t = int(region[1])
                w = int(region[2]); h = int(region[3])
            except Exception:                   # noqa: BLE001
                return None
            x0 = max(0, l)
            y0 = max(0, t)
            x1 = min(sw, l + w)
            y1 = min(sh, t + h)
            if (x1 - x0) < tw or (y1 - y0) < th:
                return None

        best = _best(scr_ch, sw, sh, tpl_ch, tw, th, x0, y0, x1, y1)
        if not best:
            return None
        score, ox, oy = best
        if score >= conf:
            return (int(ox + tw // 2), int(oy + th // 2), float(score))
        return None
    except Exception:                           # noqa: BLE001 — 失败开放
        return None


def self_check():
    """轻量自检: 生成小图并自匹配; 无副作用 (不触屏/不落盘)。→ (ok, reason)。"""
    try:
        from PIL import Image                  # 惰性
    except Exception as e:                      # noqa: BLE001
        return (False, "Pillow 不可用: {}".format(e))
    try:
        bg = Image.new("RGB", (48, 48), (10, 20, 30))
        for yy in range(6):
            for xx in range(6):
                bg.putpixel((20 + xx, 20 + yy),
                            ((xx * 40) % 256, (yy * 40) % 256, 200))
        tpl = bg.crop((20, 20, 26, 26))
        res = match(bg, tpl, confidence=0.9, grayscale=True)
        if not res:
            return (False, "自匹配未命中 (Pillow 模板匹配不可用)")
        x, y, score = res
        if abs(x - 23) > 2 or abs(y - 23) > 2:
            return (False, "自匹配坐标偏差: ({}, {})".format(x, y))
        return (True, "Pillow 模板匹配可用 (score={:.3f})".format(score))
    except Exception as e:                      # noqa: BLE001
        return (False, "自检异常: {}".format(e))
