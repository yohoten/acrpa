# -*- coding: utf-8 -*-
"""PaddleOCR.dll 原生引擎封装（可选 OCR 后端，默认关闭）

背景
----
`lib/paddle_ocr/PaddleOCR.dll` 不是 Python 包，而是 **x64 原生 C 接口封装**
（免费社区版），导出：

    Initialize / Initializejson / StructureInitialize / StructureInitializejson
    Detect / DetectByte / DetectByteData / DetectBase64 / DetectMat
    GetStructureDetectFile / GetStructureDetectByte / GetStructureDetectBase64
    GetError / FreeEngine / FreeStructureEngine
    EnableJsonResult / EnableANSIResult / libModifyParameter /
    libEnableDetUseRect / libaddLicense

它 **不自带推理引擎**，运行期还需要（与 DLL 同目录）：

    paddle_inference.dll / tbb12.dll / yaml-cpp.dll / opencv_world470.dll

以及 PP-OCR 推理模型目录（该 DLL 会在目录内查找 inference.json +
inference.pdiparams，旧版为 model.pdmodel + model.pdiparams / inference.yml）。

设计原则
--------
1. **默认零影响**：仅当 state.PADDLE_DLL_ENABLED 为 True 时 ocr_backend 才注册本后端。
2. **失败不抛异常**：依赖缺失/加载失败/初始化失败一律记录原因并返回不可用。
3. **调用契约可配置**：该 DLL 无公开头文件，导出的参数个数需实验确定。
   这里不猜死，而是提供原型表 + 自检命令（tools/_check_paddle_dll.py）
   逐组合试跑，把命中的组合写回设置即可。
4. **崩溃隔离**：初始化/推理在独立子进程中预检（源码运行环境），
   预检通过后才在主进程内真正加载，避免一次 Access Violation 打死整个程序。
"""

import base64
import ctypes
import json
import os
import subprocess
import sys
import threading

from utils import log1

try:
    import state
except Exception:  # pragma: no cover - state 必定存在，兜底避免导入链断裂
    state = None


# ══════════════════════════════════════════════════════════════════════
# 常量
# ══════════════════════════════════════════════════════════════════════

DLL_NAME = "PaddleOCR.dll"

#: 运行期必需的同目录原生依赖（缺失则 LoadLibrary 直接失败）
DEP_DLLS = ("paddle_inference.dll", "tbb12.dll", "yaml-cpp.dll", "opencv_world470.dll")

#: 模型目录内的必备文件（新版 Paddle Inference IR）
MODEL_FILES = ("inference.json", "inference.pdiparams")

#: 兼容标记（旧版 .pdmodel / yml 也能被 v6.1 之前版本识别）
MODEL_FILES_LEGACY = ("model.pdmodel", "model.pdiparams", "inference.yml")

#: 初始化原型表 —— name: (导出名, 参数形态)
#:   形态含义: dir=模型目录, file=字典/许可证文件, json=参数 JSON 字符串, flag=整数开关
#: 原型已由 .NET 元数据实测确认（PaddleOCRSharp.PaddleOCREngine 的 DllImport 声明）：
#:   UInt64 Initializejson(String det_infer, String cls_infer, String rec_infer,
#:                         String keys, String parameterjson)      ← 返回引擎句柄
#:   IntPtr Detect        (UInt64 enginePtr, String imagefile)
#:   IntPtr DetectByte    (UInt64 enginePtr, Byte[] data, Int64 size)
#:   IntPtr DetectBase64  (UInt64 enginePtr, String imagebase64)
#:   void   FreeEngine    (UInt64 enginePtr)
#: 注意参数顺序是 det → cls → rec → keys（cls 在 rec 之前），且 Detect 必须带引擎句柄。
PROTO_INIT = {
    "json5":     ("Initializejson",      ("det", "cls", "rec", "keys", "json")),
}

#: 识别原型表 —— name: (导出名, 参数形态)
PROTO_DETECT = {
    "ptr_file": ("Detect",       ("ptr", "file")),
    "ptr_byte": ("DetectByte",   ("ptr", "bytes", "size")),
    "ptr_b64":  ("DetectBase64", ("ptr", "b64")),
}

DEFAULT_PROTO_INIT = "json5"
# 默认用字节流：Detect(路径) 内部按 ANSI 打开图片，中文路径会失败
# （实测 "build\_pdll\中文测试图.png" → files or files may be damaged）；
# DetectByte(引擎句柄, 字节流, 长度) 由 Python 读文件，与路径编码无关。
DEFAULT_PROTO_DETECT = "ptr_byte"

#: 结果 JSON 里可能的容器键（不同版本封装不一，逐个兜底）
_RESULT_LIST_KEYS = ("TextBlocks", "text_blocks", "TextBlock", "results", "Results",
                     "data", "Data", "items", "Items", "blocks", "Blocks")
_TEXT_KEYS = ("Text", "text", "Label", "label", "Value", "value")
_SCORE_KEYS = ("Score", "score", "Confidence", "confidence", "TextScore", "text_score")
_BOX_KEYS = ("BoxPoints", "box_points", "Boxes", "boxes", "Box", "box", "points", "Points")

_engine = None
_engine_lock = threading.Lock()


# ══════════════════════════════════════════════════════════════════════
# 路径解析
# ══════════════════════════════════════════════════════════════════════

def _app_root():
    """程序根目录（源码运行 = 项目根；冻结 = EXE 同级）。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def candidate_dirs():
    """候选目录（按优先级）: 用户设置 → 项目/EXE 同级 lib/paddle_ocr → _MEIPASS。"""
    dirs = []
    try:
        if state is not None:
            custom = getattr(state, "PADDLE_DLL_DIR", "") or ""
            if custom:
                dirs.append(os.path.abspath(custom))
    except Exception:
        pass

    root = _app_root()
    dirs.append(os.path.join(root, "lib", "paddle_ocr"))
    dirs.append(os.path.join(root, "lib", "PaddleOCR"))
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        dirs.append(os.path.join(meipass, "lib", "paddle_ocr"))

    # 去重保序
    out, seen = [], set()
    for d in dirs:
        k = os.path.normcase(os.path.abspath(d))
        if k not in seen:
            seen.add(k)
            out.append(d)
    return out


def find_dll_dir():
    """返回实际包含 PaddleOCR.dll 的目录；找不到返回 None。"""
    for d in candidate_dirs():
        if os.path.isfile(os.path.join(d, DLL_NAME)):
            return d
    return None


def find_model_dir():
    """解析模型目录：用户设置优先，否则尝试 lib/paddle_ocr/{models,model,inference}。"""
    try:
        if state is not None:
            custom = getattr(state, "PADDLE_DLL_MODEL_DIR", "") or ""
            if custom and os.path.isdir(custom):
                return os.path.abspath(custom)
    except Exception:
        pass
    dll_dir = find_dll_dir()
    if not dll_dir:
        return ""
    for name in ("models", "model", "inference", "runtime"):
        cand = os.path.join(dll_dir, name)
        if os.path.isdir(cand):
            return cand
    return ""


def _missing_deps(dll_dir):
    if not dll_dir:
        return list(DEP_DLLS)
    return [d for d in DEP_DLLS if not os.path.isfile(os.path.join(dll_dir, d))]


def _model_state(model_dir):
    """返回 (ok, 说明)。

    兼容两种形态:
      A. 单模型目录: 目录内直接有 inference.json + inference.pdiparams
      B. 模型集合目录: 子目录里各自带 inference.json（绿色版 inference/ 即此形态）
    """
    if not model_dir or not os.path.isdir(model_dir):
        return False, "未配置模型目录"
    have = set(os.listdir(model_dir))
    if all(f in have for f in MODEL_FILES):
        return True, "inference.json + inference.pdiparams"
    subs = [d for d in sorted(os.listdir(model_dir))
            if os.path.isdir(os.path.join(model_dir, d))]
    ok_subs = [d for d in subs
               if os.path.isfile(os.path.join(model_dir, d, "inference.json"))]
    if ok_subs:
        return True, "模型集合目录（{} 个子模型，如 {}）".format(len(ok_subs), ok_subs[0])
    legacy = [f for f in MODEL_FILES_LEGACY if f in have]
    if legacy:
        return True, "旧版模型 ({}) —— 注意: .pdmodel 自 v6.1 起不再支持".format(", ".join(legacy))
    return False, "缺少 {}".format(", ".join(MODEL_FILES))


#: keys 字典候选文件名（绿色版为 ppocr_keys.txt）
KEYS_CANDIDATES = ("ppocr_keys.txt", "ppocr_keys_v1.txt", "keys.txt",
                   "dict.txt", "character_dict.txt")


def _detect_layout(model_dir, prefer=("v5", "mobile", "tiny", "small")):
    """推断模型集合目录布局: {"det":dir, "rec":dir, "cls":dir, "keys":file}。

    绿色版 inference/ 下同时存在 PP-OCRv5_mobile / v6_small / v6_tiny 多套模型，
    默认按 prefer 顺序优先选 PP-OCRv5_mobile（体积与精度平衡、与社区版最匹配）。
    """
    lay = {"det": "", "rec": "", "cls": "", "keys": ""}
    if not model_dir or not os.path.isdir(model_dir):
        return lay
    for cand in KEYS_CANDIDATES:
        p = os.path.join(model_dir, cand)
        if os.path.isfile(p):
            lay["keys"] = p
            break

    subs = [d for d in sorted(os.listdir(model_dir))
            if os.path.isdir(os.path.join(model_dir, d))]

    def pick(tag):
        matches = [d for d in subs if tag in d.lower() and "infer" in d.lower()]
        if not matches:
            matches = [d for d in subs if tag in d.lower()]
        if not matches:
            return ""
        for p in prefer:
            for d in matches:
                if p in d.lower():
                    return os.path.join(model_dir, d)
        return os.path.join(model_dir, matches[0])

    lay["det"] = pick("det")
    lay["rec"] = pick("rec")
    lay["cls"] = pick("cls")
    return lay


# ══════════════════════════════════════════════════════════════════════
# 参数 JSON（键名来自 DLL 内嵌字符串）
# ══════════════════════════════════════════════════════════════════════

def build_config_json(model_dir=None):
    """构造传给 Initializejson 的参数 JSON（字符串）。

    优先直接采用绿色版自带的 `PaddleOCR.config.json`（官方可用参数集）；
    否则用默认值构造，并允许 state.PADDLE_DLL_CONFIG 覆盖任意键。

    注意: 该 DLL 用 nlohmann::json 严格类型校验，布尔键绝不能给字符串
    （实测 "det_db_score_mode": "slow" 会报 type must be boolean）。
    """
    # ① 模型目录内若有官方配置文件，原样使用（与绿色版行为一致，风险最低）
    try:
        bases = []
        if model_dir:
            bases.append(model_dir)
        if state is not None:
            bases.append(getattr(state, "PADDLE_DLL_MODEL_DIR", "") or "")
        bases.append(find_model_dir())
        for base in bases:
            if not base:
                continue
            p = os.path.join(base, "PaddleOCR.config.json")
            if os.path.isfile(p):
                with open(p, "r", encoding="utf-8") as fh:
                    txt = fh.read()
                json.loads(txt)          # 语法校验，坏文件则回退默认值
                return txt
    except Exception:
        pass
    threads = 8
    try:
        threads = int(getattr(state, "OCR_THREADS", 8) or 8)
    except Exception:
        threads = 8
    threads = max(1, min(64, threads))

    cfg = {
        "use_gpu": bool(getattr(state, "PADDLE_DLL_USE_GPU", False)) if state else False,
        "gpu_id": 0,
        "gpu_mem": 500,
        "cpu_math_library_num_threads": threads,
        "enable_mkldnn": True,
        "det": True,
        "rec": True,
        "cls": False,
        "max_side_len": 960,
        "det_db_thresh": 0.3,
        "det_db_box_thresh": 0.5,
        "det_db_unclip_ratio": 1.6,
        "use_dilation": False,
        "det_db_score_mode": True,      # 必须是布尔（DLL 严格类型校验）
        "visualize": False,
        "use_angle_cls": True,
        "cls_thresh": 0.9,
        "cls_batch_num": 1,
        "rec_batch_num": 6,
        "rec_img_h": 48,
        "rec_img_w": 320,
        "show_img_vis": False,
        "use_tensorrt": False,
        "table_max_len": 488,
        "merge_no_span_structure": True,
        "table_batch_num": 1,
    }

    raw = ""
    try:
        raw = (getattr(state, "PADDLE_DLL_CONFIG", "") or "") if state else ""
    except Exception:
        raw = ""
    if raw.strip():
        try:
            override = json.loads(raw)
            if isinstance(override, dict):
                cfg.update(override)
        except Exception as e:
            log1("PaddleOCR.dll 参数 JSON 解析失败，改用默认值: {}".format(e), "warning")
    return json.dumps(cfg, ensure_ascii=False)


# ══════════════════════════════════════════════════════════════════════
# 结果解析
# ══════════════════════════════════════════════════════════════════════

def _first_key(d, keys, default=None):
    for k in keys:
        if k in d:
            return d[k]
    return default


def _norm_bbox(points):
    """把各种 BoxPoints 形态归一为 [[x1,y1],[x2,y2],[x3,y3],[x4,y4]] 或 None。"""
    try:
        pts = list(points)
    except Exception:
        return None
    if len(pts) == 4 and all(isinstance(v, (int, float)) for v in pts):
        x1, y1, x2, y2 = pts
        return [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]
    out = []
    for p in pts:
        if isinstance(p, dict):
            # 实测该 DLL 输出形如 [{"X":16,"Y":24}, {"X":377,"Y":24}, ...]
            x = p.get("X", p.get("x"))
            y = p.get("Y", p.get("y"))
            if x is None or y is None:
                return None
            try:
                out.append([float(x), float(y)])
            except Exception:
                return None
            continue
        try:
            x, y = float(p[0]), float(p[1])
        except Exception:
            return None
        out.append([x, y])
    return out if len(out) >= 3 else None


def _item_from_dict(d):
    text = _first_key(d, _TEXT_KEYS)
    if text is None:
        return None
    bbox = _norm_bbox(_first_key(d, _BOX_KEYS))
    score = _first_key(d, _SCORE_KEYS, 0.0)
    try:
        score = float(score)
    except Exception:
        score = 0.0
    center = None
    if bbox:
        xs = [p[0] for p in bbox]
        ys = [p[1] for p in bbox]
        center = (int(sum(xs) / len(xs)), int(sum(ys) / len(ys)))
    return {"text": str(text), "confidence": score, "bbox": bbox, "center": center}


def _walk_collect(node, out, depth=0):
    if depth > 8:
        return
    if isinstance(node, dict):
        item = _item_from_dict(node)
        if item is not None:
            out.append(item)
            return
        for v in node.values():
            _walk_collect(v, out, depth + 1)
    elif isinstance(node, list):
        for v in node:
            _walk_collect(v, out, depth + 1)


def parse_result(raw):
    """把 DLL 返回的文本解析为 [{text, confidence, bbox, center}, ...]。

    - JSON（EnableJsonResult(1) 后为 JSON，常见形如 {"TextBlocks":[...]}）
    - 纯文本兜底：逐行作为无坐标结果
    """
    if not raw:
        return []
    txt = raw.strip()
    if txt[:1] in ("{", "["):
        try:
            data = json.loads(txt)
        except Exception:
            data = None
        if data is not None:
            items = []
            if isinstance(data, dict):
                for k in _RESULT_LIST_KEYS:
                    v = data.get(k)
                    if isinstance(v, list):
                        _walk_collect(v, items)
                        if items:
                            return items
                _walk_collect(data, items)
            else:
                _walk_collect(data, items)
            if items:
                return items
    # 纯文本回退
    return [{"text": ln.strip(), "confidence": 0.0, "bbox": None, "center": None}
            for ln in txt.splitlines() if ln.strip()]


# ══════════════════════════════════════════════════════════════════════
# 引擎
# ══════════════════════════════════════════════════════════════════════

def _prepend_path(d):
    try:
        os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
    except Exception:
        pass


def _add_dll_dir(d):
    """Python 3.8+ 需要显式加入 DLL 搜索路径（返回值需持有引用）。"""
    if hasattr(os, "add_dll_directory"):
        try:
            return os.add_dll_directory(d)
        except Exception:
            return None
    return None


class PaddleDllEngine(object):
    """PaddleOCR.dll 引擎：加载 → 初始化 → 识别 → 释放。所有方法都不抛异常。"""

    def __init__(self, dll_dir=None, model_dir=None,
                 proto_init=None, proto_detect=None, layout=None):
        self.dll_dir = os.path.abspath(dll_dir) if dll_dir else (find_dll_dir() or "")
        self.model_dir = os.path.abspath(model_dir) if model_dir else find_model_dir()
        # 模型布局（det/rec/cls/keys）；未显式给出时按目录内容自动推断
        self.layout = dict(layout or {})
        if not any(self.layout.get(k) for k in ("det", "rec", "cls", "keys")):
            self.layout = _detect_layout(self.model_dir)
        self.proto_init = (proto_init
                           or (getattr(state, "PADDLE_DLL_PROTO_INIT", "") if state else "")
                           or DEFAULT_PROTO_INIT)
        self.proto_detect = (proto_detect
                             or (getattr(state, "PADDLE_DLL_PROTO_DETECT", "") if state else "")
                             or DEFAULT_PROTO_DETECT)

        self.ok = False               # 是否已完成初始化
        self.error = ""               # 最近一次失败原因
        self.backend_name = "PaddleOCR.dll (原生)"

        self._lib = None
        self._cookie = None
        self._dll_path = ""
        self._engine_ptr = 0        # Initializejson 返回的引擎句柄（Detect 必需）
        self._lock = threading.Lock()

    # ── 加载 ──
    def resolve_dll(self):
        if self.dll_dir and os.path.isfile(os.path.join(self.dll_dir, DLL_NAME)):
            return os.path.join(self.dll_dir, DLL_NAME)
        found = find_dll_dir()
        if found:
            self.dll_dir = found
            return os.path.join(found, DLL_NAME)
        return ""

    def load(self):
        """加载 DLL（含依赖目录注入）。成功返回 True。"""
        if self._lib is not None:
            return True
        dll_path = self.resolve_dll()
        if not dll_path:
            self.error = "未找到 {}（候选目录: {}）".format(
                DLL_NAME, "; ".join(candidate_dirs()))
            log1("PaddleOCR.dll: {}".format(self.error), "warning")
            return False
        self._dll_path = dll_path
        self.dll_dir = os.path.dirname(dll_path)

        missing = _missing_deps(self.dll_dir)
        self._cookie = _add_dll_dir(self.dll_dir)
        _prepend_path(self.dll_dir)
        try:
            self._lib = ctypes.WinDLL(dll_path)
        except OSError as e:
            if missing:
                self.error = "缺少原生依赖: {}（需与 PaddleOCR.dll 同目录）".format(
                    ", ".join(missing))
            else:
                self.error = "加载失败: {}".format(e)
            log1("PaddleOCR.dll: {}".format(self.error), "warning")
            self._lib = None
            return False
        self._bind()
        log1("PaddleOCR.dll: 已加载 {}".format(dll_path))
        return True

    def _bind(self):
        lib = self._lib
        for name in ("Initialize", "Initializejson", "StructureInitialize",
                     "StructureInitializejson", "libModifyParameter",
                     "libEnableDetUseRect", "libaddLicense",
                     "FreeEngine", "FreeStructureEngine"):
            try:
                getattr(lib, name).restype = None
            except Exception:
                pass
        for name in ("EnableJsonResult", "EnableANSIResult"):
            try:
                fn = getattr(lib, name)
                fn.argtypes = [ctypes.c_int]
                fn.restype = None
            except Exception:
                pass
        # 统一用 c_void_p：该 DLL 在 EnableANSIResult(0) 下返回 UTF-16LE，
        # 若声明成 c_char_p 会被第一个 NUL 截断（实测只拿到 "[" 一个字符）。
        for name in ("Detect", "DetectByte", "DetectByteData", "DetectBase64",
                     "DetectMat", "GetError", "GetStructureDetectFile",
                     "GetStructureDetectByte", "GetStructureDetectBase64"):
            try:
                getattr(lib, name).restype = ctypes.c_void_p
            except Exception:
                pass

    def get_error(self):
        """读取 GetError() 的错误串（无错误时通常返回空串/None）。"""
        try:
            fn = self._lib.GetError
            fn.argtypes = []
            fn.restype = ctypes.c_void_p
            return _read_cptr(fn())
        except Exception:
            return ""

    # ── 初始化 ──
    def _license(self):
        try:
            lic = (getattr(state, "PADDLE_DLL_LICENSE", "") or "") if state else ""
        except Exception:
            lic = ""
        return lic

    def initialize(self):
        """按配置的原型初始化引擎。成功返回 True（并把 self.ok 置 True）。"""
        if self._lib is None and not self.load():
            return False

        lic = self._license()
        if lic:
            try:
                fn = self._lib.libaddLicense
                fn.argtypes = [ctypes.c_char_p]
                fn.restype = None
                fn(lic.encode("utf-8"))
                log1("PaddleOCR.dll: 已提交许可证串")
            except Exception as e:
                log1("PaddleOCR.dll: libaddLicense 调用失败: {}".format(e), "warning")

        entry = PROTO_INIT.get(self.proto_init) or PROTO_INIT[DEFAULT_PROTO_INIT]
        name, shape = entry
        fn = getattr(self._lib, name, None)
        if fn is None:
            self.error = "导出函数不存在: {}".format(name)
            log1("PaddleOCR.dll: {}".format(self.error), "warning")
            return False

        cfg_json = build_config_json(self.model_dir)
        keys_file = self.layout.get("keys") or ""
        if not keys_file:
            for cand in KEYS_CANDIDATES:
                p = os.path.join(self.model_dir or "", cand)
                if os.path.isfile(p):
                    keys_file = p
                    break

        args = []
        for kind in shape:
            if kind == "dir":
                args.append((self.model_dir or "").encode("utf-8"))
            elif kind in ("det", "rec", "cls"):
                args.append((self.layout.get(kind) or self.model_dir or "").encode("utf-8"))
            elif kind in ("file", "keys"):
                args.append(keys_file.encode("utf-8"))
            elif kind == "json":
                args.append(cfg_json.encode("utf-8"))
            else:
                args.append((self.model_dir or "").encode("utf-8"))

        try:
            fn.argtypes = [ctypes.c_char_p] * len(args)
            fn.restype = ctypes.c_uint64          # 返回引擎句柄
            self._engine_ptr = int(fn(*args))
        except Exception as e:
            self.error = "{} 调用异常: {}".format(name, e)
            log1("PaddleOCR.dll: {}".format(self.error), "error")
            return False

        if not self._engine_ptr:
            self.error = "初始化失败: 引擎句柄为空; GetError={}".format(self.get_error() or "空")
            log1("PaddleOCR.dll: {}".format(self.error), "warning")
            return False

        err = self.get_error()
        if err:
            self.error = "初始化失败: {}".format(err)
            log1("PaddleOCR.dll: {}".format(self.error), "warning")
            return False

        # 输出格式：JSON + UTF-8（EnableANSIResult(0)）
        try:
            self._lib.EnableJsonResult(1)
        except Exception:
            pass
        try:
            self._lib.EnableANSIResult(0)
        except Exception:
            pass

        self.ok = True
        log1("PaddleOCR.dll 初始化成功 (proto_init={}, model={})".format(
            self.proto_init, self.model_dir or "<未配置>"))
        return True

    # ── 识别 ──
    def read_raw(self, image_path):
        """调用 Detect 家族，返回原始字符串（失败返回 None）。"""
        with self._lock:
            if not self.ok and not self.initialize():
                return None
            entry = PROTO_DETECT.get(self.proto_detect) or PROTO_DETECT[DEFAULT_PROTO_DETECT]
            name, shape = entry
            fn = getattr(self._lib, name, None)
            if fn is None:
                self.error = "导出函数不存在: {}".format(name)
                return None
            try:
                fn.restype = ctypes.c_void_p
                handle = ctypes.c_uint64(self._engine_ptr)
                if shape == ("ptr", "b64"):
                    with open(image_path, "rb") as fh:
                        data = fh.read()
                    # 必须 NUL 结尾：DLL 内部按 C 字符串读取
                    payload = base64.b64encode(data) + b"\x00"
                    fn.argtypes = [ctypes.c_uint64, ctypes.c_char_p]
                    ptr = fn(handle, payload)
                elif shape == ("ptr", "bytes", "size"):
                    with open(image_path, "rb") as fh:
                        data = fh.read()
                    buf = ctypes.create_string_buffer(data, len(data) + 1)
                    # size 是 Int64（.NET 声明），不能用 32 位 int
                    fn.argtypes = [ctypes.c_uint64, ctypes.c_char_p, ctypes.c_int64]
                    ptr = fn(handle, ctypes.cast(buf, ctypes.c_char_p),
                             ctypes.c_int64(len(data)))
                elif shape[:1] == ("ptr",):
                    args = [handle, os.path.abspath(image_path).encode("utf-8")]
                    types = [ctypes.c_uint64, ctypes.c_char_p]
                    fn.argtypes = types
                    ptr = fn(*args)
                elif shape == ("b64",):
                    with open(image_path, "rb") as fh:
                        data = fh.read()
                    payload = base64.b64encode(data) + b"\x00"
                    fn.argtypes = [ctypes.c_char_p]
                    ptr = fn(payload)
                elif shape == ("bytes",):
                    with open(image_path, "rb") as fh:
                        data = fh.read()
                    buf = ctypes.create_string_buffer(data, len(data) + 1)
                    fn.argtypes = [ctypes.c_char_p, ctypes.c_int]
                    ptr = fn(ctypes.cast(buf, ctypes.c_char_p), len(data))
                else:
                    args = [os.path.abspath(image_path).encode("utf-8")]
                    types = [ctypes.c_char_p]
                    if "flag" in shape:
                        args.append(0)
                        types.append(ctypes.c_int)
                    fn.argtypes = types
                    ptr = fn(*args)
            except Exception as e:
                self.error = "{} 调用异常: {}".format(name, e)
                log1("PaddleOCR.dll: {}".format(self.error), "error")
                return None

            if not ptr:
                err = self.get_error()
                if err and "box" in err.lower() and "<100" in err:
                    err = ("{}（免费社区版限制：单张图检测框必须 <100，"
                           "请缩小识别区域或简化画面）".format(err))
                self.error = err or "识别返回空指针"
                return None
            return _read_cptr(ptr)

    def read_positions(self, image_path):
        """返回 [{text, confidence, bbox, center}, ...]（失败返回 None）。"""
        raw = self.read_raw(image_path)
        if raw is None:
            return None
        return parse_result(raw)

    def read_text(self, image_path):
        """返回纯文本（多行以 \\n 连接）；失败返回 None。"""
        items = self.read_positions(image_path)
        if items is None:
            return None
        return "\n".join(i["text"] for i in items if i.get("text"))

    def release(self):
        """释放引擎句柄（FreeEngine(enginePtr)）。"""
        try:
            fn = self._lib.FreeEngine
            fn.argtypes = [ctypes.c_uint64]
            fn.restype = None
            if self._engine_ptr:
                fn(ctypes.c_uint64(self._engine_ptr))
        except Exception:
            pass
        self._engine_ptr = 0
        self.ok = False


def _read_cptr(ptr, probe=64):
    """读取 DLL 返回的字符串指针，自动识别 UTF-16LE / 单字节输出。

    实测: EnableANSIResult(0) 时 DLL 返回 UTF-16LE，必须按宽字符读取，
    否则只能拿到第一个字符（如 "["）。
    """
    if not ptr:
        return ""
    try:
        head = ctypes.string_at(ptr, probe)
    except Exception:
        return ""
    if len(head) >= 2 and head[1] == 0 and head[0] != 0:
        try:
            return ctypes.wstring_at(ptr)
        except Exception:
            pass
    try:
        return _decode(ctypes.string_at(ptr))
    except Exception:
        return ""


def _decode(ptr):
    """DLL 返回的 char* → str（UTF-8 优先，回退 GBK/单字节）。"""
    if not ptr:
        return ""
    if isinstance(ptr, bytes):
        raw = ptr
    else:  # 某些绑定返回 str
        return str(ptr)
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            return raw.decode(enc)
        except Exception:
            continue
    return ""


# ══════════════════════════════════════════════════════════════════════
# 单例 / 开关
# ══════════════════════════════════════════════════════════════════════

def is_enabled():
    """是否在设置中启用了该后端（默认 False，保持既有行为不变）。"""
    try:
        return bool(getattr(state, "PADDLE_DLL_ENABLED", False))
    except Exception:
        return False


def get_engine(force_reload=False, probe=True):
    """返回全局引擎（初始化失败返回 None）。probe=True 时先做子进程预检。"""
    global _engine
    with _engine_lock:
        if _engine is not None and not force_reload:
            return _engine if _engine.ok else None
        if force_reload and _engine is not None:
            try:
                _engine.release()
            except Exception:
                pass
            _engine = None

        eng = PaddleDllEngine()
        if probe and not getattr(sys, "frozen", False):
            ok, msg = preflight_child(eng.proto_init, eng.proto_detect,
                                      dll_dir=eng.dll_dir, model_dir=eng.model_dir,
                                      layout=eng.layout)
            if not ok:
                eng.error = msg or "预检失败"
                log1("PaddleOCR.dll 预检未通过: {}".format(eng.error), "warning")
                _engine = eng
                return None
        if not eng.initialize():
            _engine = eng
            return None
        _engine = eng
        return _engine


def reset():
    """释放引擎（切换设置/校验后调用）。"""
    global _engine
    with _engine_lock:
        if _engine is not None:
            try:
                _engine.release()
            except Exception:
                pass
        _engine = None


# ══════════════════════════════════════════════════════════════════════
# 自检（诊断 / 子进程预检 / 原型扫描）
# ══════════════════════════════════════════════════════════════════════

def diagnose():
    """静态自检：不加载 DLL，只检查文件与依赖。返回 dict（可直接 json 序列化）。"""
    dll_dir = find_dll_dir()
    model_dir = find_model_dir()
    model_ok, model_msg = _model_state(model_dir)
    missing = _missing_deps(dll_dir)
    return {
        "enabled": is_enabled(),
        "dll_dir": dll_dir or "",
        "dll_found": bool(dll_dir),
        "candidate_dirs": candidate_dirs(),
        "missing_deps": missing,
        "deps_ok": bool(dll_dir) and not missing,
        "model_dir": model_dir or "",
        "model_ok": model_ok,
        "model_msg": model_msg,
        "proto_init": getattr(state, "PADDLE_DLL_PROTO_INIT", DEFAULT_PROTO_INIT) if state else DEFAULT_PROTO_INIT,
        "proto_detect": getattr(state, "PADDLE_DLL_PROTO_DETECT", DEFAULT_PROTO_DETECT) if state else DEFAULT_PROTO_DETECT,
        # 该 DLL 打开模型文件走 ANSI 路径，含中文/全角字符的目录会导致
        # "can not found model file in ..."（实测：F:\（8）Desktop\... 必失败）
        "non_ascii": [p for p in (dll_dir or "", model_dir or "")
                      if p and any(ord(ch) > 127 for ch in p)],
        "ready": bool(dll_dir) and not missing and model_ok,
    }


def format_diagnosis(d=None):
    """把 diagnose() 结果格式化为人类可读文本（设置面板 / CLI 共用）。"""
    d = d or diagnose()
    lines = []
    lines.append("PaddleOCR.dll 自检")
    lines.append("  开关      : {}".format("已启用" if d["enabled"] else "未启用（默认关闭）"))
    lines.append("  DLL       : {}".format(d["dll_dir"] or "未找到"))
    if not d["dll_found"]:
        lines.append("    候选目录: {}".format("; ".join(d["candidate_dirs"])))
    lines.append("  依赖      : {}".format(
        "齐全" if d["deps_ok"] else "缺少 {}".format(", ".join(d["missing_deps"]) or "未知")))
    lines.append("  模型目录  : {}".format(d["model_dir"] or "未配置"))
    lines.append("  模型状态  : {}".format(d["model_msg"]))
    if d.get("non_ascii"):
        lines.append("  ⚠ 路径含非 ASCII 字符: {}".format("; ".join(d["non_ascii"])))
        lines.append("    该 DLL 内部按 ANSI 路径查找模型文件，非 ASCII 路径会报")
        lines.append("    'can not found model file in ...' —— 请改用纯英文/数字路径")
    lines.append("  调用原型  : init={} / detect={}".format(d["proto_init"], d["proto_detect"]))
    lines.append("  结论      : {}".format(
        "可调用（可在设置中启用）" if d["ready"] else "不可调用 —— 请补齐上面标注的缺失项"))
    return "\n".join(lines)


_PREFLIGHT_CODE = (
    "import json,sys\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "import paddle_dll\n"
    "opts = json.loads(sys.argv[5]) if len(sys.argv) > 5 and sys.argv[5] else {}\n"
    "r = paddle_dll.selftest(proto_init=sys.argv[2], proto_detect=sys.argv[3],\n"
    "                        sample=(sys.argv[4] or None), quiet=True, **opts)\n"
    "sys.stdout.write('@@RESULT@@' + json.dumps(r, ensure_ascii=False))\n"
)

_RESULT_MARK = "@@RESULT@@"


def _src_dir():
    return os.path.dirname(os.path.abspath(__file__))


def run_child(proto_init=DEFAULT_PROTO_INIT, proto_detect=DEFAULT_PROTO_DETECT,
              sample=None, timeout=180, dll_dir=None, model_dir=None, layout=None):
    """在子进程中执行自检（一次原生崩溃不会影响主进程）。

    返回 (ok, payload) —— payload 为 selftest() 的结果 dict，或错误字符串。
    """
    if getattr(sys, "frozen", False):
        return False, "冻结环境不支持子进程自检（请用源码环境运行 tools/_check_paddle_dll.py）"
    opts = {}
    if dll_dir:
        opts["dll_dir"] = dll_dir
    if model_dir:
        opts["model_dir"] = model_dir
    if layout and any(layout.get(k) for k in ("det", "rec", "cls", "keys")):
        opts["layout"] = layout
    cmd = [sys.executable, "-c", _PREFLIGHT_CODE, _src_dir(),
           proto_init, proto_detect, sample or "",
           json.dumps(opts, ensure_ascii=False)]
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=timeout)
    except Exception as e:
        return False, "子进程执行失败: {}".format(e)
    out = (proc.stdout or b"").decode("utf-8", "replace")
    idx = out.find(_RESULT_MARK)
    if idx < 0:
        tail = (proc.stderr or b"").decode("utf-8", "replace")[-400:]
        return False, "子进程退出码 {} 且无结果（很可能是原生崩溃）: {}".format(
            proc.returncode, tail.replace("\n", " "))
    try:
        payload = json.loads(out[idx + len(_RESULT_MARK):])
    except Exception as e:
        return False, "结果解析失败: {}".format(e)
    return bool(payload.get("initialized")), payload


def preflight_child(proto_init, proto_detect, dll_dir=None, model_dir=None, layout=None):
    """轻量预检：只验证「加载 + 初始化」能否成功（不做识别）。"""
    ok, payload = run_child(proto_init, proto_detect, dll_dir=dll_dir,
                            model_dir=model_dir, layout=layout)
    if isinstance(payload, dict):
        if ok:
            return True, ""
        return False, payload.get("error") or "初始化失败"
    return False, str(payload)


def selftest(proto_init=DEFAULT_PROTO_INIT, proto_detect=DEFAULT_PROTO_DETECT,
             sample=None, quiet=False, dll_dir=None, model_dir=None, layout=None):
    """就地自检：加载 → 初始化 →（可选）识别一张图片。返回结果 dict。"""
    res = {"dll_dir": "", "model_dir": "", "loaded": False, "initialized": False,
           "error": "", "proto_init": proto_init, "proto_detect": proto_detect,
           "layout": {}, "raw": "", "items": [], "text": ""}
    eng = PaddleDllEngine(proto_init=proto_init, proto_detect=proto_detect,
                          dll_dir=dll_dir, model_dir=model_dir, layout=layout)
    res["dll_dir"] = eng.dll_dir or ""
    res["model_dir"] = eng.model_dir or ""
    res["layout"] = eng.layout
    if not eng.load():
        res["error"] = eng.error
        return res
    res["loaded"] = True
    if not eng.initialize():
        res["error"] = eng.error
        return res
    res["initialized"] = True
    if sample and os.path.isfile(sample):
        raw = eng.read_raw(sample)
        if raw is None:
            res["error"] = eng.error
            res["initialized"] = True
        else:
            res["raw"] = raw[:4000]
            items = parse_result(raw)
            res["items"] = [{"text": i["text"], "confidence": i["confidence"],
                             "center": i["center"]} for i in items[:50]]
            res["text"] = "\n".join(i["text"] for i in items)
    elif not quiet:
        log1("PaddleOCR.dll 自检: 未提供样图，跳过识别阶段")
    return res


def sweep(sample=None, quiet=True, dll_dir=None, model_dir=None, layout=None):
    """扫描所有 原型组合，返回可用组合列表。

    必须通过 run_child 在子进程里逐组合执行（错误原型极易触发 Access Violation）。
    """
    good = []
    for pi in PROTO_INIT:
        for pd in PROTO_DETECT:
            if sample:
                ok, payload = run_child(pi, pd, sample, dll_dir=dll_dir,
                                        model_dir=model_dir, layout=layout)
                if ok and isinstance(payload, dict) and payload.get("items"):
                    good.append((pi, pd, len(payload["items"])))
                    if not quiet:
                        log1("  可用组合: init={} detect={} ({} 条结果)".format(
                            pi, pd, len(payload["items"])))
                    continue
            ok, payload = run_child(pi, pd, dll_dir=dll_dir,
                                    model_dir=model_dir, layout=layout)  # 无样图：仅验证初始化
            if ok:
                good.append((pi, pd, 0))
                if not quiet:
                    log1("  可用组合(仅初始化): init={} detect={}".format(pi, pd))
    return good
