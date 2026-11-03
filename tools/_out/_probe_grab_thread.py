# -*- coding: utf-8 -*-
"""探针: 确认 PIL ImageGrab / pyautogui.screenshot 能否在**子线程**调用 (A5 前置实测)。

离线环境下 pyautogui 可能不可用; 只要 PIL.ImageGrab 在子线程可抓屏, 即可把
「全屏 grab + 区域 grab + save」整体移出主线程。
输出含中文, 由调用方重定向到 UTF-8 文件再读。
"""
import os
import sys
import threading
import time
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "src"))

RES = {"pil_thread": None, "pil_main": None, "pa_thread": None, "err": {}}


def _pil_grab():
    from PIL import ImageGrab
    t0 = time.perf_counter()
    img = ImageGrab.grab()
    dt = (time.perf_counter() - t0) * 1000.0
    return img.size, dt


def _worker():
    try:
        RES["pil_thread"] = _pil_grab()
    except Exception:
        RES["err"]["pil_thread"] = traceback.format_exc()
    try:
        import pyautogui as pa
        t0 = time.perf_counter()
        img = pa.screenshot()
        dt = (time.perf_counter() - t0) * 1000.0
        RES["pa_thread"] = (img.size, dt)
    except Exception:
        RES["err"]["pa_thread"] = traceback.format_exc()


t = threading.Thread(target=_worker, name="probe-grab")
t.daemon = True
t.start()
t.join(timeout=20)

try:
    RES["pil_main"] = _pil_grab()
except Exception:
    RES["err"]["pil_main"] = traceback.format_exc()

print("PIL.ImageGrab 子线程:", RES["pil_thread"], RES["err"].get("pil_thread", ""))
print("PIL.ImageGrab 主线程:", RES["pil_main"], RES["err"].get("pil_main", ""))
print("pyautogui.screenshot 子线程:", RES["pa_thread"], RES["err"].get("pa_thread", ""))
ok = bool(RES["pil_thread"] or RES["pa_thread"])
print("判定: 子线程截图 {} 可 | 用 (任一成功即可)".format("可" if ok else "不"))
sys.exit(0 if ok else 1)
