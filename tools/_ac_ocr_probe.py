# -*- coding: utf-8 -*-
"""一次性探针脚本：对 F:/（8）Desktop/temp/ac/1..5.bmp 做 OCR 并重建文本行。

设计要点
--------
1. **不修改 ACRPA 任何源码**，也不 import 项目模块（避免 state/config 副作用）；
   PaddleOCR.dll 的调用契约在此**独立重写**一份最小实现（仅用于探测）。
2. 多后端降级链：paddle_dll → winrt → tesseract → rapidocr → easyocr，
   每个后端的失败原因（异常类型 + 消息）都原样记录，**绝不静默失败**。
3. 图片一律先转存到 %TEMP% 下的 png（ASCII 路径），不在工作区留临时文件。
4. 输出写到 %TEMP%：
     ac_ocr_raw.json          —— 全量结构化结果（尺寸/model/每框坐标+文本+置信度）
     ac_ocr_draft.md          —— 文本行重建草稿（给人工/Agent 二次加工）
     ac_ocr_probe.log         —— 运行日志（含每后端诊断原文）
   stdout 只打印进度摘要，避免刷屏。

用法（cmd.exe）：
    chcp 65001
    python tools\\_ac_ocr_probe.py
可选参数：
    --dir  <图片目录>      默认 F:/（8）Desktop/temp/ac
    --out  <输出目录>      默认 %TEMP%
"""

import argparse
import ctypes
import glob
import io
import json
import os
import platform
import statistics
import sys
import tempfile
import time
import traceback

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DEFAULT_IMG_DIR = "F:/（8）Desktop/temp/ac"
DLL_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "lib", "paddle_ocr", "PaddleOCR.dll")
DEP_DLLS = ("paddle_inference.dll", "tbb12.dll", "yaml-cpp.dll", "opencv_world470.dll")

_LOG_LINES = []


def log(msg):
    line = "[{}] {}".format(time.strftime("%H:%M:%S"), msg)
    _LOG_LINES.append(line)
    try:
        print(line)
    except Exception:
        pass


def log_block(title, text):
    log("---- {} ----".format(title))
    for ln in str(text).splitlines() or [""]:
        _LOG_LINES.append("    " + ln)


# ══════════════════════════════════════════════════════════════════════
# 0. 环境自检
# ══════════════════════════════════════════════════════════════════════

def env_info():
    info = {
        "python_version": sys.version.replace("\n", " "),
        "python_executable": sys.executable,
        "bits": 64 if sys.maxsize > 2 ** 32 else 32,
        "platform_architecture": str(platform.architecture()),
        "machine": platform.machine(),
        "platform": platform.platform(),
        "cwd": os.getcwd(),
        "dll_path": DLL_PATH,
        "dll_exists": os.path.isfile(DLL_PATH),
    }
    log("Python: {}".format(info["python_version"]))
    log("执行文件: {}".format(info["python_executable"]))
    log("位数: {}bit / platform.architecture()={}".format(info["bits"], info["platform_architecture"]))
    log("PaddleOCR.dll: {} (存在={})".format(DLL_PATH, info["dll_exists"]))
    return info


def _read_cptr(ptr):
    """读 ctypes 返回的宽/窄字符串指针（该 DLL 在 ANSI 关闭时返回 UTF-16LE）。"""
    if not ptr:
        return ""
    try:
        addr = int(ptr)
    except Exception:
        try:
            return str(ptr)
        except Exception:
            return ""
    if addr == 0:
        return ""
    # 先按宽字符试探：前若干字节若每两字节含 0 则判为 UTF-16LE
    try:
        head = ctypes.string_at(addr, 8)
        wide = head[1] == 0 and head[3] == 0
        if wide:
            return ctypes.wstring_at(addr)
        return ctypes.string_at(addr).decode("utf-8", "replace")
    except Exception:
        try:
            return ctypes.wstring_at(addr)
        except Exception:
            return ""


# ══════════════════════════════════════════════════════════════════════
# 1. 后端 paddle_dll（独立最小实现，仅探测）
# ══════════════════════════════════════════════════════════════════════

def probe_paddle_dll():
    """返回 (ok, detail_dict)。成功时 detail 内含 engine 句柄等。"""
    det = {"backend": "paddle_dll", "ok": False, "steps": []}

    def step(name, ok, extra=""):
        det["steps"].append({"step": name, "ok": bool(ok), "extra": str(extra)})
        log_block("paddle_dll/{} ok={}".format(name, bool(ok)), extra)

    if not os.path.isfile(DLL_PATH):
        step("dll_exists", False, "未找到 {}".format(DLL_PATH))
        det["error"] = "未找到 PaddleOCR.dll"
        return False, det
    step("dll_exists", True, DLL_PATH)

    dll_dir = os.path.dirname(DLL_PATH)
    missing = [d for d in DEP_DLLS if not os.path.isfile(os.path.join(dll_dir, d))]
    step("native_deps", not missing,
         "缺失依赖: {}".format(", ".join(missing)) if missing else "依赖齐全")
    if missing:
        det["error"] = "缺少原生依赖: {}（需与 PaddleOCR.dll 同目录）".format(", ".join(missing))
        # 仍然尝试 LoadLibrary，把 Windows 的真实报错也记下来
        try:
            if hasattr(os, "add_dll_directory"):
                os.add_dll_directory(dll_dir)
            os.environ["PATH"] = dll_dir + os.pathsep + os.environ.get("PATH", "")
            ctypes.WinDLL(DLL_PATH)
            step("LoadLibrary", True, "意外加载成功")
        except OSError as e:
            step("LoadLibrary", False, "{}: {}".format(type(e).__name__, e))
        except Exception as e:
            step("LoadLibrary", False, "{}: {}".format(type(e).__name__, e))
        return False, det

    try:
        if hasattr(os, "add_dll_directory"):
            os.add_dll_directory(dll_dir)
        os.environ["PATH"] = dll_dir + os.pathsep + os.environ.get("PATH", "")
        lib = ctypes.WinDLL(DLL_PATH)
        step("LoadLibrary", True, dll_dir)
    except OSError as e:
        step("LoadLibrary", False, "{}: {}".format(type(e).__name__, e))
        det["error"] = "DLL 加载失败: {}".format(e)
        return False, det

    # 模型目录探测
    model_dir = ""
    for name in ("inference", "models", "model", "runtime"):
        cand = os.path.join(dll_dir, name)
        if os.path.isdir(cand):
            model_dir = cand
            break
    if not model_dir:
        step("model_dir", False, "{} 下无 inference/models/model/runtime 任一目录".format(dll_dir))
        det["error"] = "无模型目录"
        return False, det
    step("model_dir", True, model_dir)
    det["model_dir"] = model_dir
    return True, det


# ══════════════════════════════════════════════════════════════════════
# 2. 其它后端可用性探测（只探测，不驱动）
# ══════════════════════════════════════════════════════════════════════

def probe_winrt():
    det = {"backend": "winrt", "ok": False, "attempts": []}
    for mod in ("winrt.windows.media.ocr", "winrt", "winsdk.windows.media.ocr", "winsdk"):
        try:
            __import__(mod)
            det["attempts"].append({"module": mod, "ok": True, "error": ""})
        except Exception as e:
            det["attempts"].append({"module": mod, "ok": False,
                                    "error": "{}: {}".format(type(e).__name__, e)})
    det["ok"] = any(a["ok"] for a in det["attempts"])
    log_block("winrt 探测", json.dumps(det["attempts"], ensure_ascii=False, indent=2))
    return det


def probe_tesseract():
    det = {"backend": "tesseract", "ok": False, "attempts": []}
    try:
        import pytesseract  # noqa: F401
        det["attempts"].append({"what": "import pytesseract", "ok": True, "error": ""})
    except Exception as e:
        det["attempts"].append({"what": "import pytesseract", "ok": False,
                                "error": "{}: {}".format(type(e).__name__, e)})
    exe = None
    try:
        import shutil
        exe = shutil.which("tesseract")
    except Exception as e:
        det["attempts"].append({"what": "shutil.which", "ok": False,
                                "error": "{}: {}".format(type(e).__name__, e)})
    det["attempts"].append({"what": "tesseract.exe in PATH", "ok": bool(exe),
                            "error": "" if exe else "PATH 中找不到 tesseract"})
    det["ok"] = bool(exe) and any(a["ok"] for a in det["attempts"]
                                  if a["what"] == "import pytesseract")
    log_block("tesseract 探测", json.dumps(det["attempts"], ensure_ascii=False, indent=2))
    return det


def probe_easyocr():
    det = {"backend": "easyocr", "ok": False, "attempts": []}
    try:
        import easyocr  # noqa: F401
        det["ok"] = True
        det["attempts"].append({"what": "import easyocr", "ok": True, "error": ""})
    except Exception as e:
        det["attempts"].append({"what": "import easyocr", "ok": False,
                                "error": "{}: {}".format(type(e).__name__, e)})
    log_block("easyocr 探测", json.dumps(det["attempts"], ensure_ascii=False, indent=2))
    return det


def probe_rapidocr():
    det = {"backend": "rapidocr_onnxruntime", "ok": False, "attempts": [], "engine": None}
    try:
        import onnxruntime  # noqa: F401
        det["attempts"].append({"what": "import onnxruntime", "ok": True,
                                "error": getattr(onnxruntime, "__version__", "")})
    except Exception as e:
        det["attempts"].append({"what": "import onnxruntime", "ok": False,
                                "error": "{}: {}".format(type(e).__name__, e)})
    try:
        from rapidocr_onnxruntime import RapidOCR
        det["attempts"].append({"what": "import RapidOCR", "ok": True, "error": ""})

        engine = None
        try:
            engine = RapidOCR(det_limit_side_len=3200, det_limit_type="max", text_score=0.3)
            det["attempts"].append({"what": "RapidOCR(custom params)", "ok": True,
                                    "error": "det_limit_side_len=3200, text_score=0.3"})
        except Exception as e:
            det["attempts"].append({"what": "RapidOCR(custom params)", "ok": False,
                                    "error": "{}: {}".format(type(e).__name__, e)})
            engine = RapidOCR()
            det["attempts"].append({"what": "RapidOCR(default)", "ok": True, "error": ""})
        det["engine"] = engine
        det["ok"] = True
    except Exception as e:
        det["attempts"].append({"what": "import RapidOCR", "ok": False,
                                "error": "{}: {}".format(type(e).__name__, e)})
    log_block("rapidocr 探测",
              json.dumps([{k: v for k, v in a.items()} for a in det["attempts"]],
                         ensure_ascii=False, indent=2))
    return det


# ══════════════════════════════════════════════════════════════════════
# 3. 图片读取 / 预处理
# ══════════════════════════════════════════════════════════════════════

def size_via_system_drawing(path):
    """PIL 不可用时的兜底：用 System.Drawing 取尺寸。返回 (w,h) 或 None。"""
    try:
        import subprocess
        ps = ('Add-Type -AssemblyName System.Drawing;'
              '$i=[System.Drawing.Image]::FromFile("{}");'
              'Write-Output ("{}x{}" -f $i.Width,$i.Height);$i.Dispose()').format(path, "{0}", "{1}")
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                             capture_output=True, text=True, timeout=60)
        txt = (out.stdout or "").strip()
        if "x" in txt:
            w, h = txt.split("x")[:2]
            return int(w), int(h)
    except Exception as e:
        log("System.Drawing 取尺寸失败: {}: {}".format(type(e).__name__, e))
    return None


def load_image(path, temp_dir):
    """返回 dict(path, size, mode, png_path, note)。统一转 RGB png 到 %TEMP%。"""
    info = {"src": path, "size": None, "mode": None, "png": "", "notes": []}
    try:
        from PIL import Image
        with Image.open(path) as im:
            info["size"] = list(im.size)
            info["mode"] = im.mode
            info["format"] = im.format
        with Image.open(path) as im:
            rgb = im.convert("RGB")
            base = os.path.splitext(os.path.basename(path))[0]
            png = os.path.join(temp_dir, "ac_ocr_{}.png".format(base))
            rgb.save(png, format="PNG")
            info["png"] = png
            info["notes"].append("PIL 转存 PNG(RGB) 到 %TEMP%")
        log("{} 尺寸={} 模式={} -> {}".format(os.path.basename(path), info["size"],
                                            info["mode"], info["png"]))
    except Exception as e:
        log_block("PIL 读取失败 {}".format(path),
                  "{}: {}\n{}".format(type(e).__name__, e, traceback.format_exc()))
        info["notes"].append("PIL 读取失败: {}: {}".format(type(e).__name__, e))
        wh = size_via_system_drawing(path)
        if wh:
            info["size"] = [wh[0], wh[1]]
            info["notes"].append("尺寸改由 System.Drawing 获取")
        info["png"] = path
    return info


def make_enhanced(png_path, temp_dir, scale=2):
    """增强副本：灰度 + 放大 scale 倍。返回新路径或 None。"""
    try:
        from PIL import Image
        base = os.path.splitext(os.path.basename(png_path))[0]
        out = os.path.join(temp_dir, "{}_x{}_gray.png".format(base, scale))
        with Image.open(png_path) as im:
            g = im.convert("L")
            g = g.resize((int(g.width * scale), int(g.height * scale)), Image.LANCZOS)
            g.save(out, format="PNG")
        return out
    except Exception as e:
        log("增强失败: {}: {}".format(type(e).__name__, e))
        return None


# ══════════════════════════════════════════════════════════════════════
# 4. OCR 执行（rapidocr 主用，tesseract 兜底）
# ══════════════════════════════════════════════════════════════════════

def norm_box(box):
    """四点 -> [x1,y1,x2,y2]（外接矩形）+ 原始四点。"""
    xs = [float(p[0]) for p in box]
    ys = [float(p[1]) for p in box]
    return [min(xs), min(ys), max(xs), max(ys)]


def ocr_rapidocr(engine, img_path):
    out = engine(img_path)
    res = out[0] if isinstance(out, (tuple, list)) and len(out) >= 1 else out
    if isinstance(out, (tuple, list)) and len(out) == 2:
        res = out[0]
    items = []
    if not res:
        return items, ""
    for it in res:
        try:
            box, text, score = it[0], it[1], it[2]
        except Exception:
            continue
        try:
            pts = [[float(p[0]), float(p[1])] for p in box]
        except Exception:
            continue
        rect = norm_box(pts)
        items.append({
            "text": str(text),
            "score": float(score) if score is not None else 0.0,
            "box": pts,
            "rect": rect,
        })
    return items, ""


def ocr_tesseract(img_path, lang="chi_sim+eng"):
    """仅在 rapidocr 不可用时使用。"""
    import pytesseract
    from PIL import Image
    with Image.open(img_path) as im:
        data = pytesseract.image_to_data(im, lang=lang, output_type=pytesseract.Output.DICT)
    items = []
    n = len(data.get("text", []))
    for i in range(n):
        txt = (data["text"][i] or "").strip()
        if not txt:
            continue
        try:
            conf = float(data["conf"][i])
        except Exception:
            conf = -1.0
        if conf < 0:
            continue
        x, y, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
        rect = [float(x), float(y), float(x + w), float(y + h)]
        items.append({
            "text": txt, "score": conf / 100.0,
            "box": [[rect[0], rect[1]], [rect[2], rect[1]],
                    [rect[2], rect[3]], [rect[0], rect[3]]],
            "rect": rect,
        })
    return items, ""


# ══════════════════════════════════════════════════════════════════════
# 5. 文本行重建
# ══════════════════════════════════════════════════════════════════════

def build_lines(items):
    """按 y 中心聚类成文本行（同一条 y 差 < 0.6 行高即归为一行），行内按 x 排序。"""
    if not items:
        return []
    enriched = []
    for it in items:
        x1, y1, x2, y2 = it["rect"]
        enriched.append({
            "text": it["text"],
            "score": it["score"],
            "x1": x1, "y1": y1, "x2": x2, "y2": y2,
            "w": max(1.0, x2 - x1), "h": max(1.0, y2 - y1),
            "cx": (x1 + x2) / 2.0, "cy": (y1 + y2) / 2.0,
        })
    enriched.sort(key=lambda t: (t["cy"], t["x1"]))

    lines = []
    for it in enriched:
        target = None
        for ln in lines:
            tol = 0.6 * max(it["h"], ln["h"])
            if abs(it["cy"] - ln["cy"]) <= tol:
                target = ln
                break
        if target is None:
            lines.append({"cy": it["cy"], "h": it["h"], "items": [it],
                          "y1": it["y1"], "y2": it["y2"]})
        else:
            target["items"].append(it)
            n = len(target["items"])
            target["cy"] = sum(i["cy"] for i in target["items"]) / n
            target["h"] = max(target["h"], it["h"])
            target["y1"] = min(target["y1"], it["y1"])
            target["y2"] = max(target["y2"], it["y2"])

    for ln in lines:
        ln["items"].sort(key=lambda t: t["x1"])
        ln["x1"] = min(i["x1"] for i in ln["items"])
        ln["x2"] = max(i["x2"] for i in ln["items"])
    lines.sort(key=lambda l: l["cy"])
    return lines


def line_text(ln, sep=" | "):
    return sep.join(i["text"] for i in ln["items"])


def guess_columns(items):
    """疑似"列"结构：x1 相近且纵向出现 >=3 次的文本。"""
    if not items:
        return []
    buckets = {}
    for it in items:
        key = int(round(it["rect"][0] / 12.0))
        buckets.setdefault(key, []).append(it)
    cols = []
    for key, arr in buckets.items():
        if len(arr) < 3:
            continue
        ys = sorted(a["rect"][1] for a in arr)
        span = ys[-1] - ys[0]
        if span < 60:
            continue
        cols.append({
            "x1_approx": round(sum(a["rect"][0] for a in arr) / len(arr), 1),
            "count": len(arr),
            "yspan": [round(ys[0], 1), round(ys[-1], 1)],
            "texts": [a["text"] for a in arr][:12],
        })
    cols.sort(key=lambda c: c["x1_approx"])
    return cols


# ══════════════════════════════════════════════════════════════════════
# 6. 主流程
# ══════════════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=DEFAULT_IMG_DIR)
    ap.add_argument("--out", default=None)
    ap.add_argument("--images", default="")
    args = ap.parse_args()

    temp_dir = os.path.join(tempfile.gettempdir(), "ac_ocr")
    os.makedirs(temp_dir, exist_ok=True)
    out_dir = args.out or tempfile.gettempdir()

    info = env_info()
    results = {"env": info, "backends": {}, "images": []}

    log("== 后端探测 ==")
    ok_pd, det_pd = probe_paddle_dll()
    results["backends"]["paddle_dll"] = {"ok": ok_pd, "detail": det_pd}

    det_w = probe_winrt()
    results["backends"]["winrt"] = det_w
    det_t = probe_tesseract()
    results["backends"]["tesseract"] = det_t
    det_e = probe_easyocr()
    results["backends"]["easyocr"] = det_e
    det_r = probe_rapidocr()
    results["backends"]["rapidocr"] = {k: v for k, v in det_r.items() if k != "engine"}

    # 图片清单
    if args.images:
        names = [n.strip() for n in args.images.split(",") if n.strip()]
        img_paths = [os.path.join(args.dir, n if n.lower().endswith(".bmp") else n + ".bmp")
                     for n in names]
    else:
        img_paths = []
        for i in range(1, 6):
            p = os.path.join(args.dir, "{}.bmp".format(i))
            if os.path.isfile(p):
                img_paths.append(p)
        if not img_paths:
            img_paths = sorted(glob.glob(os.path.join(args.dir, "*.bmp")))

    if not img_paths:
        log("!! 没有找到任何 bmp：{}".format(args.dir))

    use_rapid = bool(det_r.get("ok"))
    engine = det_r.get("engine") if use_rapid else None
    active_backend = "rapidocr_onnxruntime" if use_rapid else None

    for path in img_paths:
        t0 = time.time()
        rec = {"file": os.path.basename(path), "path": path}
        log("== 处理 {} ==".format(os.path.basename(path)))
        img = load_image(path, temp_dir)
        rec["size"] = img["size"]
        rec["mode"] = img["mode"]
        rec["format"] = img.get("format")
        rec["notes"] = list(img["notes"])
        rec["png"] = img["png"]

        items, enh_note, err = [], "", ""
        if use_rapid:
            try:
                items, err = ocr_rapidocr(engine, img["png"])
            except Exception as e:
                err = "{}: {}".format(type(e).__name__, e)
                log_block("rapidocr 识别异常",
                          err + "\n" + traceback.format_exc())
            if not items:
                log("  首轮无结果，尝试灰度+2x 放大重识别")
                enh = make_enhanced(img["png"], temp_dir, scale=2)
                if enh:
                    enh_note = "增强重试: 灰度+2x 放大（{}）".format(os.path.basename(enh))
                    try:
                        items, err2 = ocr_rapidocr(engine, enh)
                        if err2:
                            err = (err + "; " if err else "") + err2
                    except Exception as e:
                        log_block("增强识别异常", "{}: {}".format(type(e).__name__, e))
                else:
                    enh_note = "增强失败（PIL 不可用或写临时文件失败）"
        else:
            # 降级：tesseract
            if det_t.get("ok"):
                try:
                    items, err = ocr_tesseract(img["png"])
                    active_backend = active_backend or "tesseract"
                except Exception as e:
                    err = "{}: {}".format(type(e).__name__, e)
                    log_block("tesseract 识别异常", err)
            else:
                err = "无可用 OCR 后端"

        if enh_note:
            rec["notes"].append(enh_note)
        rec["ocr_error"] = err
        rec["backend"] = active_backend
        rec["raw_items"] = items
        rec["count"] = len(items)
        rec["avg_score"] = (round(statistics.fmean([it["score"] for it in items]), 4)
                            if items else 0.0)
        lines = build_lines(items)
        rec["lines"] = [{
            "y1": round(l["y1"], 1), "y2": round(l["y2"], 1),
            "x1": round(l["x1"], 1), "x2": round(l["x2"], 1),
            "text": line_text(l),
            "n": len(l["items"]),
            "avg_score": round(statistics.fmean([i["score"] for i in l["items"]]), 3),
        } for l in lines]
        rec["columns"] = guess_columns(items)
        rec["elapsed_s"] = round(time.time() - t0, 2)
        log("  {} 个文本框 / {} 行 / 平均置信度 {} / 耗时 {}s".format(
            rec["count"], len(rec["lines"]), rec["avg_score"], rec["elapsed_s"]))
        results["images"].append(rec)

    results["active_backend"] = active_backend
    results["total_elapsed"] = sum(r.get("elapsed_s", 0) for r in results["images"])

    raw_json = os.path.join(out_dir, "ac_ocr_raw.json")
    with io.open(raw_json, "w", encoding="utf-8") as fh:
        json.dump(results, fh, ensure_ascii=False, indent=1)
    log("已写 {}".format(raw_json))

    draft = os.path.join(out_dir, "ac_ocr_draft.md")
    with io.open(draft, "w", encoding="utf-8") as fh:
        fh.write("# OCR 文本行重建草稿\n\n")
        fh.write("- backend: {}\n".format(active_backend))
        fh.write("- total_elapsed: {}s\n\n".format(results["total_elapsed"]))
        for rec in results["images"]:
            fh.write("## {}  size={} mode={} count={} avg={} elapsed={}s\n\n".format(
                rec["file"], rec["size"], rec["mode"], rec["count"],
                rec["avg_score"], rec["elapsed_s"]))
            if rec.get("ocr_error"):
                fh.write("- OCR error: {}\n".format(rec["ocr_error"]))
            for note in rec.get("notes", []):
                fh.write("- note: {}\n".format(note))
            fh.write("\n### 行重建（y区间 | x区间 | 文本）\n\n```\n")
            for l in rec["lines"]:
                fh.write("y{}-{} | x{}-{} | {}\n".format(
                    l["y1"], l["y2"], l["x1"], l["x2"], l["text"]))
            fh.write("```\n\n### 单框明细（x1,y1,x2,y2 score text）\n\n```\n")
            for it in rec["raw_items"]:
                r = it["rect"]
                fh.write("[{}, {}, {}, {}] {:.3f} {}\n".format(
                    round(r[0], 1), round(r[1], 1), round(r[2], 1), round(r[3], 1),
                    it["score"], it["text"]))
            fh.write("```\n\n### 疑似列结构\n\n```\n")
            for c in rec["columns"]:
                fh.write("x≈{} count={} yspan={} -> {}\n".format(
                    c["x1_approx"], c["count"], c["yspan"], " / ".join(c["texts"])))
            fh.write("```\n\n")
    log("已写 {}".format(draft))

    log_path = os.path.join(out_dir, "ac_ocr_probe.log")
    with io.open(log_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(_LOG_LINES))
    print("DONE raw={} draft={} log={}".format(raw_json, draft, log_path))


if __name__ == "__main__":
    main()
