# -*- coding: utf-8 -*-
"""ACRPA NetLink — 远程截图内核（Phase 4-1，纯标准库 + 懒加载 Pillow）。

职责：
  * 抓取主屏 → 等比缩小到指定宽度 → JPEG 编码 → base64（全程内存编码，不落盘）；
  * 模块级节流（同一被控端 1 秒内最多 1 张）；
  * 宽/质量夹取到安全上限，保证整帧 < 协议 MAX_FRAME_BYTES(8MB)。

设计约束：
  * Pillow 只在函数内懒 import：无 Pillow / 无显示环境仍可安全 import 本模块；
  * 本模块不 import utils（utils 顶层 import tkinter），日志走 connection._nl_log；
  * 抓屏/编码整体 try/except，异常只返回稳定英文 error 短语，绝不向上抛；
  * 不写任何文件，避免污染磁盘（响应里直接携带 base64）。
"""
import base64
import io
import threading
import time

from .connection import _nl_log


MAX_WIDTH_DEFAULT = 640
MAX_WIDTH_LIMIT = 1280
QUALITY_DEFAULT = 60
QUALITY_LIMIT = 80
MIN_INTERVAL = 1.0                     # 秒，同一被控端的截图节流窗口
MIN_WIDTH = 64
MIN_QUALITY = 10
MAX_PAYLOAD_BYTES = 5 * 1024 * 1024    # 单帧 JPEG 原始字节上限（base64 后仍 < 8MB）


# 模块级节流状态（threading.Lock 保护）
_last_ts = 0.0
_throttle_lock = threading.Lock()


def _clamp_int(v, lo, hi, default):
    """把 v 夹取到 [lo, hi]；无法转 int 时返回 default。"""
    try:
        n = int(v)
    except Exception:
        return default
    if n < lo:
        return lo
    if n > hi:
        return hi
    return n


def _empty_result():
    return {"ok": False, "ts": int(time.time()), "width": 0, "height": 0,
            "format": "jpeg", "data_b64": "", "bytes": 0, "error": ""}


def capture(max_width=MAX_WIDTH_DEFAULT, quality=QUALITY_DEFAULT):
    """抓取主屏 → 等比缩放到 max_width 宽度 → 编码 JPEG → base64。

    返回 SCREENSHOT_DAT 的 data 结构：
      {"ok":True,"ts":int,"width":int,"height":int,"format":"jpeg",
       "data_b64":str,"bytes":int,"error":""}
    失败：{"ok":False,...,"error":"<稳定英文短语>"}

    error 取值（稳定英文短语）：
      * "rate limited"      —— 距上次截图 < MIN_INTERVAL 秒；
      * "pillow unavailable"—— Pillow / ImageGrab 不可用；
      * "grab failed"       —— 抓屏或编码失败（无桌面会话 / 无权限）；
      * "payload too large" —— 编码后原始字节 > MAX_PAYLOAD_BYTES。
    """
    out = _empty_result()

    # ── 节流（任何一次尝试都会占用时间窗，避免失败高并发重试）──
    now = time.time()
    try:
        with _throttle_lock:
            global _last_ts
            if _last_ts and (now - _last_ts) < MIN_INTERVAL:
                out["error"] = "rate limited"
                return out
            _last_ts = now
    except Exception:
        pass

    # ── 懒加载 Pillow（无 Pillow → 明确短语，不抛）──
    try:
        from PIL import ImageGrab, Image
    except Exception:
        out["error"] = "pillow unavailable"
        return out

    # ── 抓屏 ──
    try:
        img = ImageGrab.grab()
    except Exception as e:
        _nl_log("screen grab failed: {}".format(e), "WARN")
        out["error"] = "grab failed"
        return out
    if img is None:
        out["error"] = "grab failed"
        return out

    mw = _clamp_int(max_width, MIN_WIDTH, MAX_WIDTH_LIMIT, MAX_WIDTH_DEFAULT)
    q = _clamp_int(quality, MIN_QUALITY, QUALITY_LIMIT, QUALITY_DEFAULT)

    # ── 等比缩放（仅缩小，绝不放大小图）+ JPEG 编码 ──
    try:
        w, h = img.size
        try:
            w = int(w)
            h = int(h)
        except Exception:
            pass
        if w > mw:
            ratio = float(mw) / float(w)
            nh = int(round(h * ratio))
            if nh < 1:
                nh = 1
            try:
                img = img.resize((mw, nh), getattr(Image, "LANCZOS", 1))
            except Exception:
                img = img.resize((mw, nh))
        nw, nh2 = img.size
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=q)
        raw = buf.getvalue()
    except Exception as e:
        _nl_log("screen encode failed: {}".format(e), "WARN")
        out["error"] = "grab failed"
        return out

    n = len(raw)
    if n > MAX_PAYLOAD_BYTES:
        out["width"] = int(nw)
        out["height"] = int(nh2)
        out["bytes"] = int(n)
        out["error"] = "payload too large"
        return out

    out["ok"] = True
    out["width"] = int(nw)
    out["height"] = int(nh2)
    out["bytes"] = int(n)
    out["data_b64"] = base64.b64encode(raw).decode("ascii")
    out["error"] = ""
    return out
