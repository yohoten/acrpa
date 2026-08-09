"""
OCR Backend for ACRPA — screen text recognition commands.

Supports multiple backends (auto-detected in priority order):
1. PaddleOCR (paddleocr)     — Best accuracy, pip install paddlepaddle paddleocr (~250MB)
2. Windows OCR (winrt)       — Windows 10/11 built-in, zero extra install
3. Tesseract (pytesseract)   — Cross-platform, requires Tesseract-OCR install
4. Fallback: clear error message guiding installation

Commands provided:
- 识别文字: OCR a screen region, save result to variable
- 等待文字: Wait until specific text appears/disappears on screen
- 点击文字: Find text on screen and click its location

PaddleOCR 体积说明:
  - pip install paddlepaddle: ~180 MB (CPU推理引擎)
  - pip install paddleocr: ~10 MB (Python封装)
  - 模型自动下载到 ~/.paddleocr/: ~40 MB (PP-OCRv5 中英文模型)
  - 总计约 230 MB，不会打包进 ACRPA.exe
  - 未安装时自动回退到 Windows OCR / Tesseract
"""

import os
import datetime
import state
from utils import log1


# ── Lazy backend detection ──
_ocr_backend = None       # None=未检测, False=不可用, str=后端名 ("paddle"|"winrt"|"tesseract")
_backend_name = None      # Human-readable backend name
_detect_done = False      # 是否已完成检测

# ── PaddleOCR lazy singleton ──
_paddle_ocr = None        # PaddleOCR instance (lazy init)
_paddle_available = None  # None=未检测, True=可用, False=不可用


def _check_paddle_available():
    """Check if paddleocr is installed and importable (without loading models)."""
    global _paddle_available
    if _paddle_available is not None:
        return _paddle_available
    try:
        import paddleocr
        _paddle_available = True
    except ImportError:
        _paddle_available = False
    return _paddle_available


def _get_paddle_ocr():
    """Lazy-init PaddleOCR singleton. Returns instance or None."""
    global _paddle_ocr
    if _paddle_ocr is not None:
        return _paddle_ocr

    if not _check_paddle_available():
        return None

    try:
        from paddleocr import PaddleOCR
        
        # 检查用户是否配置了自定义模型目录（如复用 AutomationOperation 的模型）
        custom_dir = getattr(state, 'OCR_PADDLE_DIR', '') if hasattr(state, 'OCR_PADDLE_DIR') else ''
        det_model_dir = None
        rec_model_dir = None
        cls_model_dir = None
        
        if custom_dir and os.path.isdir(custom_dir):
            # 尝试映射 AutomationOperation 的模型目录结构
            # inference/PP-OCRv5_mobile_det_infer/ -> det
            # inference/PP-OCRv5_mobile_rec_infer/ -> rec
            # inference/ch_ppocr_mobile_v5.0_cls_infer/ -> cls
            det_candidate = os.path.join(custom_dir, "PP-OCRv5_mobile_det_infer")
            rec_candidate = os.path.join(custom_dir, "PP-OCRv5_mobile_rec_infer")
            cls_candidate = os.path.join(custom_dir, "ch_ppocr_mobile_v5.0_cls_infer")
            if os.path.isdir(det_candidate):
                det_model_dir = det_candidate
            if os.path.isdir(rec_candidate):
                rec_model_dir = rec_candidate
            if os.path.isdir(cls_candidate):
                cls_model_dir = cls_candidate
            if det_model_dir or rec_model_dir:
                log1("PaddleOCR: 使用自定义模型目录 '{}'".format(custom_dir), "info")

        # 构建 PaddleOCR 参数
        kwargs = dict(
            use_angle_cls=True,   # 启用文字方向分类（提升竖排/倒置文字识别）
            lang='ch',            # 中英文混合模型
            show_log=False,       # 不打印PaddleOCR内部日志
        )
        if det_model_dir:
            kwargs['det_model_dir'] = det_model_dir
        if rec_model_dir:
            kwargs['rec_model_dir'] = rec_model_dir
        if cls_model_dir:
            kwargs['cls_model_dir'] = cls_model_dir

        _paddle_ocr = PaddleOCR(**kwargs)
        log1("PaddleOCR 初始化成功 (PP-OCRv5, 中英文)", "info")
        return _paddle_ocr
    except Exception as e:
        log1("PaddleOCR 初始化失败: {}".format(e), "warning")
        _paddle_available = False
        return None


def _detect_backend():
    """Auto-detect the best available OCR backend. Called once on first use.
    
    Priority: PaddleOCR > Windows OCR (winrt) > Tesseract > None
    Respects state.OCR_PREFERRED_BACKEND if set.
    """
    global _ocr_backend, _backend_name, _detect_done

    if _detect_done:
        return _ocr_backend is not False and _ocr_backend is not None

    # ── Check user preference ──
    preferred = getattr(state, 'OCR_PREFERRED_BACKEND', 'auto') if hasattr(state, 'OCR_PREFERRED_BACKEND') else 'auto'

    # ── Try backends in priority order ──
    backends_to_try = []
    if preferred == 'paddle':
        backends_to_try = ['paddle', 'winrt', 'tesseract']
    elif preferred == 'winrt':
        backends_to_try = ['winrt', 'paddle', 'tesseract']
    elif preferred == 'tesseract':
        backends_to_try = ['tesseract', 'paddle', 'winrt']
    else:
        # auto: PaddleOCR first (best accuracy)
        backends_to_try = ['paddle', 'winrt', 'tesseract']

    for backend in backends_to_try:
        if backend == 'paddle':
            if _try_paddle_backend():
                return True
        elif backend == 'winrt':
            if _try_winrt_backend():
                return True
        elif backend == 'tesseract':
            if _try_tesseract_backend():
                return True

    # ── No backend available ──
    _ocr_backend = False
    _backend_name = None
    _detect_done = True
    log1("OCR不可用: 请安装任一OCR后端:\n"
         "  • PaddleOCR (推荐): pip install paddlepaddle paddleocr\n"
         "  • Windows OCR: Windows 10+ 自带，无需安装\n"
         "  • Tesseract: pip install pytesseract + 安装 Tesseract-OCR", "warning")
    return False


def _try_paddle_backend():
    global _ocr_backend, _backend_name, _detect_done
    if not _check_paddle_available():
        return False
    try:
        ocr = _get_paddle_ocr()
        if ocr is not None:
            _ocr_backend = "paddle"
            _backend_name = "PaddleOCR (PP-OCRv5)"
            _detect_done = True
            return True
    except Exception as e:
        log1("PaddleOCR 后端检测失败: {}".format(e), "warning")
    return False


def _try_winrt_backend():
    global _ocr_backend, _backend_name, _detect_done
    try:
        import winrt.windows.media.ocr as winrt_ocr
        import winrt.windows.graphics.imaging as winrt_imaging
        import winrt.windows.storage.streams as winrt_streams
        _ocr_backend = "winrt"
        _backend_name = "Windows OCR (内置)"
        _detect_done = True
        log1("OCR后端: {}".format(_backend_name), "info")
        return True
    except ImportError:
        pass
    except Exception as e:
        log1("Windows OCR 初始化失败: {}".format(e), "warning")
    return False


def _try_tesseract_backend():
    global _ocr_backend, _backend_name, _detect_done
    try:
        import pytesseract
        from PIL import Image
        tesseract_version = pytesseract.get_tesseract_version()
        _ocr_backend = "tesseract"
        _backend_name = "Tesseract OCR v{}".format(tesseract_version)
        _detect_done = True
        log1("OCR后端: {}".format(_backend_name), "info")
        return True
    except ImportError:
        pass
    except Exception as e:
        log1("Tesseract OCR 初始化失败: {}".format(e), "warning")
    return False


def ocr_get_backend_info():
    """Return current backend info dict: {backend, name, available}."""
    _detect_backend()
    return {
        "backend": _ocr_backend,
        "name": _backend_name,
        "available": _ocr_backend is not False and _ocr_backend is not None,
    }


def ocr_reset_backend():
    """Reset backend detection (useful after installing new OCR package at runtime)."""
    global _ocr_backend, _backend_name, _detect_done, _paddle_ocr, _paddle_available
    _ocr_backend = None
    _backend_name = None
    _detect_done = False
    _paddle_ocr = None
    _paddle_available = None


# ======================================================================
# OCR Read (dispatch to active backend)
# ======================================================================

def _ocr_read_text(image_path, language="ch"):
    """
    Read text from a saved image file using the detected backend.

    Args:
        image_path: Path to the screenshot PNG file
        language: OCR language (default: ch for Chinese+English)

    Returns:
        Recognized text string, or None on failure
    """
    if not _detect_backend():
        return None

    try:
        if _ocr_backend == "paddle":
            return _ocr_paddle(image_path)
        elif _ocr_backend == "winrt":
            return _ocr_winrt(image_path, "chi_sim+eng")
        elif _ocr_backend == "tesseract":
            return _ocr_tesseract(image_path, "chi_sim+eng")
    except Exception as e:
        log1("OCR识别失败 ({}): {}".format(_ocr_backend, e), "error")
    return None


# ======================================================================
# PaddleOCR Backend
# ======================================================================

def _ocr_paddle(image_path):
    """OCR via PaddleOCR (PP-OCRv5). Returns recognized text."""
    ocr = _get_paddle_ocr()
    if ocr is None:
        return None

    result = ocr.ocr(image_path, cls=True)
    if not result or not result[0]:
        return ""

    # Extract all recognized text lines
    lines = []
    for line_info in result[0]:
        if len(line_info) >= 2:
            text = line_info[1][0]  # (text, confidence)
            if text:
                lines.append(text)
    return "\n".join(lines)


def _ocr_paddle_with_positions(image_path):
    """OCR via PaddleOCR, returning text with bounding box positions.
    
    Returns:
        list of dicts: [{"text": str, "confidence": float, 
                         "bbox": [[x1,y1],[x2,y2],[x3,y3],[x4,y4]],
                         "center": (cx, cy)}, ...]
        or None on failure
    """
    ocr = _get_paddle_ocr()
    if ocr is None:
        return None

    result = ocr.ocr(image_path, cls=True)
    if not result or not result[0]:
        return []

    items = []
    for line_info in result[0]:
        if len(line_info) >= 2:
            bbox = line_info[0]       # [[x1,y1],[x2,y2],[x3,y3],[x4,y4]]
            text, confidence = line_info[1]
            if text and bbox:
                # Calculate center of bounding box
                xs = [p[0] for p in bbox]
                ys = [p[1] for p in bbox]
                cx = int(sum(xs) / len(xs))
                cy = int(sum(ys) / len(ys))
                items.append({
                    "text": text,
                    "confidence": confidence,
                    "bbox": bbox,
                    "center": (cx, cy),
                })
    return items


# ======================================================================
# Windows OCR Backend
# ======================================================================

def _ocr_winrt(image_path, language="chi_sim+eng"):
    """OCR via Windows Runtime OCR API."""
    import winrt.windows.media.ocr as winrt_ocr
    import winrt.windows.graphics.imaging as winrt_imaging
    import winrt.windows.storage.streams as winrt_streams
    import winrt.windows.globalization as winrt_globalization

    from winrt.windows.storage import StorageFile, FileAccessMode
    import asyncio

    async def _do_ocr():
        file = await StorageFile.get_file_from_path_async(os.path.abspath(image_path))
        stream = await file.open_async(FileAccessMode.READ)
        decoder = await winrt_imaging.BitmapDecoder.create_async(stream)
        bitmap = await decoder.get_software_bitmap_async()

        engine = winrt_ocr.OcrEngine.try_create_from_user_profile_languages()
        if engine is None:
            engine = winrt_ocr.OcrEngine.try_create_from_language(
                winrt_globalization.Language("zh-Hans"))

        result = await engine.recognize_async(bitmap)
        return result.text if result else ""

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        text = loop.run_until_complete(_do_ocr())
        loop.close()
        return text
    except RuntimeError:
        import threading
        result_container = []

        def _run():
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                result_container.append(loop.run_until_complete(_do_ocr()))
                loop.close()
            except Exception:
                result_container.append("")

        t = threading.Thread(target=_run)
        t.start()
        t.join(timeout=30)
        return result_container[0] if result_container else ""


# ======================================================================
# Tesseract Backend
# ======================================================================

def _ocr_tesseract(image_path, language="chi_sim+eng"):
    """OCR via Tesseract."""
    import pytesseract
    from PIL import Image
    img = Image.open(image_path)
    return pytesseract.image_to_string(img, lang=language)


# ======================================================================
# Public API
# ======================================================================

def ocr_read_region(region, script_dir=""):
    """
    OCR a screen region and return recognized text.

    Args:
        region: (left, top, width, height) tuple, or None for fullscreen
        script_dir: Script directory (unused, for API consistency)

    Returns:
        Recognized text string
    """
    if not _detect_backend():
        return ""

    import pyautogui as pa

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    tmp_dir = os.path.join(os.path.dirname(state.CONFIG_PATH), "screenshots")
    os.makedirs(tmp_dir, exist_ok=True)
    tmp_path = os.path.join(tmp_dir, "ocr_tmp_{}.png".format(ts))

    try:
        if region and len(region) >= 4:
            left, top, w, h = region[0], region[1], region[2], region[3]
            if w > 0 and h > 0:
                pa.screenshot(tmp_path, region=(left, top, w, h))
            else:
                pa.screenshot(tmp_path)
        else:
            pa.screenshot(tmp_path)
        text = _ocr_read_text(tmp_path)
        return text.strip() if text else ""
    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass


def ocr_find_text_position(target_text, region=None, confidence=0.7):
    """
    Find text position on screen and return center coordinates.

    PaddleOCR: Uses precise bounding box positions for sub-pixel accuracy.
    Other backends: Uses grid-scan heuristic approach.

    Args:
        target_text: Text to find on screen
        region: (left, top, width, height) or None for fullscreen
        confidence: Minimum match confidence (0-1)

    Returns:
        (x, y) center coordinates or None
    """
    if not _detect_backend():
        return None

    import pyautogui as pa
    screen_w, screen_h = pa.size()

    if region is None:
        left, top, width, height = 0, 0, screen_w, screen_h
    else:
        left, top, width, height = region[0], region[1], region[2], region[3]

    # ── PaddleOCR: use precise bounding box positions ──
    if _ocr_backend == "paddle":
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        tmp_dir = os.path.join(os.path.dirname(state.CONFIG_PATH), "screenshots")
        os.makedirs(tmp_dir, exist_ok=True)
        tmp_path = os.path.join(tmp_dir, "ocr_pos_{}.png".format(ts))

        try:
            if width > 0 and height > 0:
                pa.screenshot(tmp_path, region=(left, top, width, height))
            else:
                pa.screenshot(tmp_path)

            items = _ocr_paddle_with_positions(tmp_path)
            if items is None:
                return None  # PaddleOCR init failed

            # Find best matching text, adjusted for region offset
            best_item = None
            best_conf = 0
            target_lower = target_text.lower()

            for item in items:
                item_text = item["text"]
                # Exact match or substring match
                if target_lower == item_text.lower():
                    if item["confidence"] > best_conf:
                        best_item = item
                        best_conf = item["confidence"]
                elif target_lower in item_text.lower():
                    # Substring match: lower priority, but still valid
                    if item["confidence"] > best_conf and item["confidence"] >= confidence:
                        best_item = item
                        best_conf = item["confidence"] * 0.9  # Slight penalty

            if best_item:
                cx, cy = best_item["center"]
                abs_x = left + cx
                abs_y = top + cy
                log1("PaddleOCR定位: '{}' → ({}, {}) 置信度: {:.2f}".format(
                    target_text, abs_x, abs_y, best_conf))
                return (abs_x, abs_y)
            return None
        finally:
            try:
                os.remove(tmp_path)
            except Exception:
                pass

    # ── Other backends: grid-scan heuristic ──
    text = ocr_read_region(region)
    if not text or target_text.lower() not in text.lower():
        return None

    best_score = 0
    best_center = (left + width // 2, top + height // 2)

    grid_size = 3
    for gx in range(grid_size):
        for gy in range(grid_size):
            sub_left = left + width * gx // grid_size
            sub_top = top + height * gy // grid_size
            sub_w = width // grid_size
            sub_h = height // grid_size

            sub_text = ocr_read_region((sub_left, sub_top, sub_w, sub_h))
            if sub_text and target_text.lower() in sub_text.lower():
                sub_center = (sub_left + sub_w // 2, sub_top + sub_h // 2)
                score = 1.0 / max(sub_w * sub_h, 1)
                if score > best_score:
                    best_score = score
                    best_center = sub_center

    return best_center
