# -*- coding: utf-8 -*-
"""capabilities — 可选依赖 / 能力四态注册表 (路线图 阶段二新增项①)。

设计约束 (务必保持)
------------------
* **纯 stdlib**: 模块级仅 import ``os`` / ``sys`` / ``threading`` / ``dataclasses`` /
  ``enum``, 外加两个本项目的纯 stdlib 常/函数模块: ``native_check`` (仅 io/os/struct)
  与 ``ocr_consts`` (零 import, 只有字面量清单);
* **import 无副作用 / 无 GUI / 无重依赖**: 所有对 ACRPA 内部模块与第三方库的探测
  都收敛在 probe 函数体内**惰性 import**, 因此 ``import capabilities`` 不会加载
  ``tkinter`` / ``pyautogui`` / ``engine`` / 各家后端;
* **失败开放 (fail-open)**: probe 缺失 / 抛异常 / 能力未注册 → 一律视为 READY, 仅把
  原因写进 ``reason`` —— 绝不因探测自身的故障误伤命令执行;
* **线程安全 + 缓存**: 模块级 ``threading.Lock()`` 保护 ``_caps`` / ``_cache`` /
  ``_disabled``; ``probe`` 幂等且带缓存 (``refresh`` 可清)。

四态 (CapState)
---------------
    READY    探测通过, 能力可用
    MISSING  未安装 / 未启用 (无痕迹)
    BROKEN   有安装痕迹但自检失败 (如 DLL 存在却不可用)
    DISABLED 用户经 ``disable()`` 显式关闭

与既有探测函数的关系 (为何不直接包裹)
------------------------------------
路线图建议「包裹」既有的 ``engine._has_cv2`` /
``browser_backend._check_playwright_available`` / ``paddle_dll.is_enabled`` /
``dd_backend.dd_allowed``。但这些模块 (经 ``utils``) 在 import 期即拉起 ``tkinter``,
会破坏脚本干跑 (``script_validate``) 的「纯函数、不加载 tkinter/pyautogui」硬约束
(见 ``tools/_test_script_validate.py`` V10 与 ``_test_import_side_effect_free.py``)。
因此本模块改为**直接探测底层条件** (``import cv2`` / ``import playwright.sync_api`` /
读 ``state`` 设置 + 检查 DLL 落盘), 语义等价且保持纯净 —— 详见各 probe 注释。
"""
import os
import sys
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, Optional

# native_check = 本项目的**纯 stdlib** 静态检查模块 (模块级仅 io/os/struct, import
# 零副作用), 提供 PE 位数 / 依赖存在性 / 路径 ASCII 判断 (S1-01 新建)。
# 缺它时各 probe 一律**失败开放** (保持"探测自身故障不误伤命令执行"的既有语义)。
# ⚠ **不要**在这里 import ``paddle_dll`` / ``dd_backend`` / ``ocr_backend``:
#   它们经 ``utils`` 在 import 期即拉起 tkinter, 会破坏脚本干跑与
#   ``tools/_test_import_side_effect_free.py`` 的零副作用硬约束。
try:
    import native_check as _native
except Exception:                        # noqa: BLE001 — 打包缺模块时降级为旧式检查
    _native = None

# OCR 依赖/模型清单的**单一定义源** (S1-03): 权威字面量只在 src/ocr_consts.py,
# ``paddle_dll`` 从同一模块 import 同一批对象 → 由此保证
#     ``paddle_dll.DEP_DLLS is capabilities.PADDLE_DEP_DLLS``
# 恒为 True (同一 tuple, 结构上不可能再漂移)。
# ``ocr_consts`` 零 import / 零副作用, 不会破坏"import 无 tkinter/pyautogui"约束。
from ocr_consts import (DEP_DLLS as PADDLE_DEP_DLLS,
                        DLL_NAME as PADDLE_DLL_NAME,
                        MODEL_FILES as PADDLE_MODEL_FILES,
                        MODEL_FILES_LEGACY as PADDLE_MODEL_FILES_LEGACY)


class CapState(Enum):
    """能力四态。"""

    READY = "ready"
    MISSING = "missing"
    BROKEN = "broken"
    DISABLED = "disabled"


@dataclass
class ProbeResult:
    """一次探测的结果 (state + 人类可读 reason, 可选 detail)。"""

    state: CapState = CapState.READY
    reason: str = ""
    detail: dict = field(default_factory=dict)


@dataclass
class Capability:
    """一个能力 (可选依赖插件) 的静态描述。"""

    id: str
    label: str
    ext_id: Optional[str] = None
    degraded: str = "hard"          # "hard" | "soft" | "none"
    degraded_note: str = ""
    probe: Optional[Callable[[], ProbeResult]] = None


_caps: Dict[str, Capability] = {}
_cache: Dict[str, ProbeResult] = {}
_disabled = set()
_lock = threading.Lock()

# 应用根目录 (src 的上一级) —— 用于定位 lib/ 下的可选 DLL, 避免 import 后端模块。
_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ── 注册 / 探测 ──────────────────────────────────────────────────────
def register(cap):
    """注册 (或覆盖) 一个能力; 顺带清掉它的缓存。"""
    with _lock:
        _caps[cap.id] = cap
        _cache.pop(cap.id, None)


def _run_probe(cap):
    """执行 probe 并归一化返回值 → ProbeResult。任何异常都失败开放。"""
    fn = cap.probe
    if not callable(fn):
        return ProbeResult(CapState.READY, "探测不可用 (未提供 probe)")
    try:
        r = fn()
    except Exception as e:                # noqa: BLE001 — 探测自身故障 → 失败开放
        return ProbeResult(CapState.READY, "探测不可用: {}".format(e))
    if isinstance(r, ProbeResult):
        return r
    if isinstance(r, CapState):
        return ProbeResult(r, "")
    if isinstance(r, bool):
        return ProbeResult(CapState.READY if r else CapState.MISSING, "")
    if isinstance(r, tuple) and r:        # (ok, reason) 形式
        ok = bool(r[0])
        reason = str(r[1]) if len(r) > 1 and r[1] else ""
        return ProbeResult(CapState.READY if ok else CapState.MISSING, reason)
    return ProbeResult(CapState.READY, "探测返回值不可识别 (fail-open)")


def probe(cap_id):
    """探测能力 (便宜 / 幂等 / 无副作用); 带缓存。

    DISABLED 优先于缓存; 未注册能力 → 失败开放为 READY。
    """
    with _lock:
        if cap_id in _disabled:
            return ProbeResult(CapState.DISABLED, "用户已关闭该能力")
        cached = _cache.get(cap_id)
    if cached is not None:
        return cached
    cap = _caps.get(cap_id)
    if cap is None:
        return ProbeResult(CapState.READY, "未注册能力, 视为可用")
    r = _run_probe(cap)
    with _lock:
        if cap_id in _disabled:
            return ProbeResult(CapState.DISABLED, "用户已关闭该能力")
        _cache[cap_id] = r
    return r


def state(cap_id):
    """→ CapState。"""
    return probe(cap_id).state


def require_reason(cap_id):
    """→ 该能力当前状态的原因文本 (供错误/提示信息拼接)。"""
    return probe(cap_id).reason


def all_states():
    """→ {cap_id: CapState}, 覆盖全部已注册能力。"""
    with _lock:
        ids = list(_caps.keys())
    return {cid: state(cid) for cid in ids}


def refresh(cap_id=None):
    """清缓存以便重探; ``cap_id=None`` 清全部。"""
    with _lock:
        if cap_id is None:
            _cache.clear()
        else:
            _cache.pop(cap_id, None)


def disable(cap_id):
    """用户显式关闭该能力 → 恒为 DISABLED。"""
    with _lock:
        _disabled.add(cap_id)
        _cache.pop(cap_id, None)


def enable(cap_id):
    """取消显式关闭; 下次 probe 重新探测。"""
    with _lock:
        _disabled.discard(cap_id)
        _cache.pop(cap_id, None)


# ── 便捷查询 (消费方拼接信息用) ──────────────────────────────────────
def get(cap_id):
    return _caps.get(cap_id)


def label(cap_id):
    cap = _caps.get(cap_id)
    return cap.label if cap else str(cap_id)


def degraded(cap_id):
    """→ "hard" / "soft" / "none" (未注册默认 hard)。"""
    cap = _caps.get(cap_id)
    return cap.degraded if (cap and cap.degraded) else "hard"

# ── OCR / 内核输入 的依赖清单 (单一定义源, 供 probe 静态自检) ───────────
# capabilities **不得** import ``paddle_dll``: 后者经 ``utils`` 在 import 期即拉起
# tkinter, 会破坏脚本干跑与 ``tools/_test_import_side_effect_free.py`` 的
# 「import 无副作用」硬约束。
#
# S1-03 已把权威字面量**下沉**到 src/ocr_consts.py (零 import / 零副作用);
# 本文件顶部 import 进来的 PADDLE_* 就是**同一批对象**, 故
#     ``paddle_dll.DEP_DLLS is capabilities.PADDLE_DEP_DLLS`` 恒为 True,
# 不再有"两份清单靠注释自觉同步"的漂移面。
# 🔒 锁定测试: ``tools/_test_ocr_selfcheck.py`` (O1 三处同源 / O2 唯一字面量清单)
#    与 ``tools/_test_native_check.py::t_consts_in_sync``。
# 四个名字 (PADDLE_DLL_NAME / PADDLE_DEP_DLLS / PADDLE_MODEL_FILES /
# PADDLE_MODEL_FILES_LEGACY) 全部来自顶部那一处 import, 本段不再有字面量。


# ── 探测用的小工具 (纯 stdlib; 全部失败开放, 不抛异常) ────────────────
def _is_frozen():
    """是否运行在 PyInstaller 冻结环境。"""
    return bool(getattr(sys, "frozen", False)) or hasattr(sys, "_MEIPASS")


def _paddle_candidate_dirs(custom=""):
    """PaddleOCR.dll 候选目录 (与 ``paddle_dll.candidate_dirs`` 同序, 但不 import 它)。"""
    dirs = []
    if custom:
        dirs.append(os.path.abspath(custom))
    dirs.append(os.path.join(_APP_DIR, "lib", "paddle_ocr"))
    dirs.append(os.path.join(_APP_DIR, "lib", "PaddleOCR"))
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        dirs.append(os.path.join(meipass, "lib", "paddle_ocr"))
    out, seen = [], set()
    for d in dirs:
        try:
            key = os.path.normcase(os.path.abspath(d))
        except Exception:                 # noqa: BLE001
            continue
        if key not in seen:
            seen.add(key)
            out.append(d)
    return out


def _paddle_model_dir(dll_dir, custom=""):
    """模型目录: 用户设置优先, 否则 ``{models,model,inference,runtime}`` (同 paddle_dll)。"""
    try:
        if custom and os.path.isdir(custom):
            return os.path.abspath(custom)
    except Exception:                     # noqa: BLE001
        pass
    for name in ("models", "model", "inference", "runtime"):
        cand = os.path.join(dll_dir, name)
        try:
            if os.path.isdir(cand):
                return cand
        except Exception:                 # noqa: BLE001
            continue
    return ""


def _paddle_model_state(model_dir):
    """→ (ok, 说明); 兼容单模型 / 模型集合 / 旧版三种形态 (同 paddle_dll._model_state)。"""
    if not model_dir or not os.path.isdir(model_dir):
        return False, "未找到模型目录"
    try:
        have = set(os.listdir(model_dir))
    except Exception as e:                # noqa: BLE001
        return False, "模型目录不可读: {}".format(e)
    if all(f in have for f in PADDLE_MODEL_FILES):
        return True, "模型文件齐全 ({})".format(" + ".join(PADDLE_MODEL_FILES))
    subs = [d for d in sorted(have) if os.path.isdir(os.path.join(model_dir, d))]
    good = [d for d in subs
            if os.path.isfile(os.path.join(model_dir, d, "inference.json"))]
    if good:
        return True, "模型集合目录 ({} 个子模型, 如 {})".format(len(good), good[0])
    legacy = [f for f in PADDLE_MODEL_FILES_LEGACY if f in have]
    if legacy:
        return True, "旧版模型 ({}) —— .pdmodel 自 v6.1 起不再支持".format(
            ", ".join(legacy))
    return False, "缺少 {}".format(", ".join(PADDLE_MODEL_FILES))


def _playwright_browser_dirs():
    """playwright 浏览器安装目录候选 (ms-playwright; 同序去重)。"""
    dirs = []
    env = str(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "") or "").strip()
    if env and env != "0":
        dirs.append(env)
    local = os.environ.get("LOCALAPPDATA") or ""
    if local:
        dirs.append(os.path.join(local, "ms-playwright"))
    try:
        home = os.environ.get("HOME") or os.path.expanduser("~") or ""
    except Exception:                     # noqa: BLE001
        home = ""
    if home:
        dirs.append(os.path.join(home, "AppData", "Local", "ms-playwright"))
        dirs.append(os.path.join(home, ".cache", "ms-playwright"))
    out, seen = [], set()
    for d in dirs:
        try:
            key = os.path.normcase(os.path.abspath(d))
        except Exception:                 # noqa: BLE001
            continue
        if key not in seen:
            seen.add(key)
            out.append(d)
    return out


def _playwright_browser_present():
    """浏览器是否已 `playwright install` → (present, 路径或候选目录说明)。

    只查目录, **不启动浏览器**。"包在但浏览器没下"是极常见陷阱:
    ``sync_playwright()`` 能起, ``chromium.launch()`` 才抛
    ``Executable doesn't exist at …`` —— 那时用户已在跑命令, 错误被埋进日志。
    这里把该失败前移到探测期。
    """
    env = str(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "") or "").strip()
    if env == "0":
        # 浏览器装进 playwright 包内 (.local-browsers), 路径由包自己算 → 静态无法判定
        return True, "PLAYWRIGHT_BROWSERS_PATH=0 (浏览器随包安装, 静态不判定)"
    cands = _playwright_browser_dirs()
    for d in cands:
        try:
            if not os.path.isdir(d):
                continue
            names = os.listdir(d)
        except Exception:                 # noqa: BLE001
            continue
        hit = sorted(n for n in names if n.lower().startswith("chromium"))
        if hit:
            return True, os.path.join(d, hit[0])
    return False, ("、".join(cands) if cands else "未找到 ms-playwright 目录")


# ── 探测实现 (惰性 import; 全部避免拉起 tkinter) ──────────────────────
def _probe_cv_match():
    """OpenCV 图像匹配 (阶段二新增项③: 已装 ``cv.match`` 扩展优先)。

    探测顺序:
      ① **已安装扩展** → 惰性加载提供者并跑其 ``self_check()``; 通过 → READY
         (reason 标注「由扩展 cv.match 提供」);
      ② **冻结版分支 (S1-02 新增)**: PyInstaller 包内 cv2 已被
         ``build.py`` / ``ACRPA.spec`` 明确 exclude (体积门禁), 所以**不再尝试
         import cv2** —— 在冻结版里它必然 ImportError, 白付一次 sys.path 搜索与
         失败开销。直接走 Pillow 回退, reason 写明「冻结版不含 cv2」;
      ③ 源码运行: cv2 可用 → READY; 否则 Pillow 可导入 → READY (精度受限);
         两者皆无 → MISSING。

    失败开放: 探测自身任何异常一律 READY (绝不因探测故障误伤找图命令)。
    零副作用: ``extensions`` / ``cv2`` / ``PIL`` 全部惰性 import。
    """
    # ① 已安装扩展优先
    try:
        import extensions
        if extensions.is_installed("cv.match"):
            ok, why = extensions.provider_self_check("cv.match")
            if ok:
                return ProbeResult(CapState.READY, "由扩展 cv.match 提供")
            # 已装但自检不通过 → 落到下方内置回退 (仍可能 READY)
    except Exception:                     # noqa: BLE001 — 探测自身故障 → 失败开放
        pass
    # ② 冻结版: 不碰 cv2 (被 excludes 移除, import 必失败且拖慢启动)
    if _is_frozen():
        try:
            import PIL  # noqa: F401
            return ProbeResult(CapState.READY,
                               "冻结版不含 cv2, 使用 Pillow 回退匹配 (精度受限)")
        except Exception:                 # noqa: BLE001
            return ProbeResult(CapState.MISSING,
                               "冻结版不含 cv2 且无 Pillow 回退")
    # ③ 源码运行: cv2 / Pillow
    try:
        import cv2  # noqa: F401
        return ProbeResult(CapState.READY, "OpenCV(cv2) 可用 (高精度/高速度匹配)")
    except ImportError:
        pass
    except Exception as e:                # cv2 装了但坏了 → 仍可 Pillow 回退
        return ProbeResult(CapState.READY, "cv2 导入异常, 回退 Pillow: {}".format(e))
    try:
        import PIL  # noqa: F401
        return ProbeResult(CapState.READY,
                           "未安装 cv2, 使用 Pillow 回退匹配 (精度受限)")
    except Exception:
        return ProbeResult(CapState.MISSING, "未安装 cv2 且无 Pillow 回退")


def _probe_playwright():
    """playwright 是否可用: **包可导入 且 浏览器已 ``playwright install``**。

    旧实现只 import 包 → "包装了但浏览器没下"也 READY, 而第一次
    ``chromium.launch()`` 必失败。S1-02 补静态浏览器检查 (只看目录, 不启动)。
    """
    try:
        import playwright.sync_api  # noqa: F401
    except ImportError as e:
        return ProbeResult(CapState.MISSING, "未安装 playwright: {}".format(e))
    except Exception as e:                # noqa: BLE001
        return ProbeResult(CapState.BROKEN, "playwright 已安装但导入失败: {}".format(e))
    ok, where = _playwright_browser_present()
    if ok:
        return ProbeResult(CapState.READY, "playwright 可用 (浏览器: {})".format(where))
    return ProbeResult(
        CapState.BROKEN,
        "playwright 已安装但未下载浏览器 (查 {:s}), 请运行: "
        "playwright install chromium".format(where))


def _probe_paddle_dll():
    """PaddleOCR.dll 后端: 用户开关 + **静态**自检 (依赖/模型/ASCII/位数)。

    旧实现只判 ``PaddleOCR.dll`` 是否存在 → 只要目录里有一个裸 DLL 就 READY,
    而第一次调用必然因「缺 4 个原生依赖」或「缺模型目录」失败 (典型假就绪)。
    新实现逐项静态核对 (不加载 DLL: 加载会触发杀软启发式), BROKEN 的 reason
    **逐个列出缺失文件名**, 用户可照单补齐。
    """
    try:
        import state
        enabled = bool(getattr(state, "PADDLE_DLL_ENABLED", False))
        custom_dir = str(getattr(state, "PADDLE_DLL_DIR", "") or "")
        custom_model = str(getattr(state, "PADDLE_DLL_MODEL_DIR", "") or "")
    except Exception as e:                # noqa: BLE001
        return ProbeResult(CapState.READY, "探测不可用: {}".format(e))
    dll_dir, dll_path = "", ""
    for d in _paddle_candidate_dirs(custom_dir):
        cand = os.path.join(d, PADDLE_DLL_NAME)
        try:
            if os.path.isfile(cand):
                dll_dir, dll_path = d, cand
                break
        except Exception:                 # noqa: BLE001
            continue
    if not dll_dir:
        if enabled:
            return ProbeResult(
                CapState.BROKEN,
                "已启用但未找到 {} (候选目录: {})".format(
                    PADDLE_DLL_NAME, "; ".join(_paddle_candidate_dirs(custom_dir))))
        return ProbeResult(CapState.MISSING, "未安装/未启用 PaddleOCR.dll 后端")
    if not enabled:
        return ProbeResult(
            CapState.MISSING,
            "未启用 PaddleOCR.dll 后端 (已找到 {}, 未启用时走 pip paddleocr)".format(
                dll_path))
    if _native is None:                   # 静态检查模块缺失 → 失败开放
        return ProbeResult(CapState.READY, "探测不可用 (缺 native_check), 视为可用")
    problems = []
    # ① 同目录 4 个原生依赖 (缺任一 → LoadLibrary 直接失败)
    missing = _native.missing_files(dll_dir, PADDLE_DEP_DLLS)
    if missing:
        problems.append("缺少原生依赖: {}".format(", ".join(missing)))
    # ② 模型目录 + 模型文件
    model_dir = _paddle_model_dir(dll_dir, custom_model)
    model_ok, model_msg = _paddle_model_state(model_dir)
    if not model_ok:
        problems.append("模型不可用: {} (模型目录={})".format(
            model_msg, model_dir or "未找到"))
    # ③ 路径必须纯 ASCII (DLL 内部按 ANSI 查模型文件)
    for label, p in (("DLL 目录", dll_dir), ("模型目录", model_dir)):
        if not p:
            continue
        ok, why = _native.dir_ascii_only(p)
        if not ok:
            problems.append("{}: {}".format(label, why))
    # ④ 位数: 32 位 DLL 在 64 位进程内加载必失败 (WinError 193)
    ok64, why64 = _native.is_x64(dll_path)
    if not ok64:
        problems.append(why64)
    if problems:
        return ProbeResult(
            CapState.BROKEN,
            "已启用但静态自检未通过 —— {}".format("; ".join(problems)),
            detail={"dll_dir": dll_dir, "model_dir": model_dir,
                    "missing_deps": missing})
    return ProbeResult(
        CapState.READY,
        "PaddleOCR.dll 静态自检通过 (依赖齐全, 目录={}, 模型: {})".format(
            dll_dir, model_msg),
        detail={"dll_dir": dll_dir, "model_dir": model_dir, "missing_deps": []})


def _probe_input_dd():
    """DD 内核驱动 (可选, 缺失时 engine 回退 PyAutoGUI)。

    旧实现只看 ``dd63330.dll`` 是否存在 → 一个 **32 位**同名文件也会 READY, 而
    64 位 ACRPA 加载它必然 ``WinError 193``。S1-02 补 PE 位数校验。
    """
    try:
        import state
        mode = str(getattr(state, "INPUT_MODE", "") or "").strip().lower()
        flag = bool(getattr(state, "USE_DD_DRIVER", False))
    except Exception as e:                # noqa: BLE001
        return ProbeResult(CapState.READY, "探测不可用: {}".format(e))
    if not (flag or mode == "dd"):
        return ProbeResult(
            CapState.MISSING,
            "未启用 DD 内核驱动 (input_mode={!r}), 回退 PyAutoGUI".format(mode))
    dll = os.path.join(_APP_DIR, "lib", "dd_driver", "dd63330.dll")
    if not os.path.exists(dll):
        return ProbeResult(CapState.BROKEN, "已启用 DD 但未找到 dd63330.dll")
    if _native is None:                   # 静态检查模块缺失 → 失败开放
        return ProbeResult(CapState.READY, "探测不可用 (缺 native_check), 视为可用")
    ok64, why = _native.is_x64(dll)
    if not ok64:
        return ProbeResult(
            CapState.BROKEN,
            "已启用 DD 但该文件不可用: {} —— 请换成 64 位 dd63330.dll".format(why))
    return ProbeResult(CapState.READY, "DD 内核驱动已启用 ({})".format(why))


# ── L2 动作库扩充 (方案文档 §5 L2) 的能力探测 ───────────────────────
# 这 5 个能力都是 **Windows 原生/标准库** 具备的, 注册它们的目的不是"拦命令",
# 而是让 requires() 声明的 id 有单一事实来源 (label/degraded/原因), 并让
# 非 Windows 环境 / 缺 ssl 的裁剪环境能给出可读的 MISSING 原因。
def _probe_proc_kill():
    """进程管理 (OpenProcess/TerminateProcess, 纯 ctypes)。"""
    if os.name != "nt":
        return ProbeResult(CapState.MISSING, "非 Windows 平台无进程管理能力")
    try:
        import ctypes
        ctypes.windll.kernel32
        return ProbeResult(CapState.READY, "ctypes kernel32 可用")
    except Exception as e:                # noqa: BLE001
        return ProbeResult(CapState.BROKEN, "kernel32 不可用: {}".format(e))


def _probe_net_http():
    """HTTP(S) 请求 (urllib.request + ssl)。"""
    try:
        import ssl  # noqa: F401
        import urllib.request  # noqa: F401
        return ProbeResult(CapState.READY, "urllib.request + ssl 可用")
    except ImportError as e:
        return ProbeResult(CapState.MISSING, "标准库缺 urllib/ssl: {}".format(e))
    except Exception as e:                # noqa: BLE001
        return ProbeResult(CapState.BROKEN, "urllib/ssl 导入失败: {}".format(e))


def _probe_power():
    """电源操作 (shutdown.exe + user32/powrprof)。"""
    if os.name != "nt":
        return ProbeResult(CapState.MISSING, "非 Windows 平台无电源操作能力")
    try:
        import ctypes
        ctypes.windll.user32
        return ProbeResult(CapState.READY, "user32/powrprof 可用")
    except Exception as e:                # noqa: BLE001
        return ProbeResult(CapState.BROKEN, "电源接口不可用: {}".format(e))


def _probe_audio():
    """音量控制 (IAudioEndpointVolume COM / keybd_event 回退)。"""
    if os.name != "nt":
        return ProbeResult(CapState.MISSING, "非 Windows 平台无音量控制能力")
    try:
        import ctypes
        ctypes.windll.ole32
        ctypes.windll.user32
        return ProbeResult(CapState.READY, "ole32/user32 可用 (COM 优先, 键击回退)")
    except Exception as e:                # noqa: BLE001
        return ProbeResult(CapState.BROKEN, "音频接口不可用: {}".format(e))


def _probe_win32():
    """Win32 窗口样式 (SetLayeredWindowAttributes/SetWindowPos)。"""
    if os.name != "nt":
        return ProbeResult(CapState.MISSING, "非 Windows 平台无窗口样式能力")
    try:
        import ctypes
        ctypes.windll.user32
        return ProbeResult(CapState.READY, "user32 可用")
    except Exception as e:                # noqa: BLE001
        return ProbeResult(CapState.BROKEN, "user32 不可用: {}".format(e))


# ── 首期注册 4 个能力 ────────────────────────────────────────────────
# 阶段二新增项③: cv.match 由可选扩展 cv.match 提供 (ext_id 指向该扩展);
# 其余 3 个能力暂无扩展来源, ext_id 保持 None。
register(Capability(id="cv.match", label="🖼 OpenCV 图像匹配", ext_id="cv.match",
                    degraded="hard",
                    degraded_note="缺失时找图回退 Pillow (精度/速度下降)",
                    probe=_probe_cv_match))
register(Capability(id="browser.playwright", label="🌐 Playwright 浏览器",
                    ext_id=None, degraded="hard",
                    degraded_note="浏览器自动化命令依赖 playwright",
                    probe=_probe_playwright))
register(Capability(id="ocr.paddle_dll", label="🔤 PaddleOCR.dll",
                    ext_id=None, degraded="soft",
                    degraded_note="缺失时回退 pip paddleocr / 不可用",
                    probe=_probe_paddle_dll))
register(Capability(id="input.dd", label="⌨ DD 内核输入",
                    ext_id=None, degraded="soft",
                    degraded_note="缺失时回退 PyAutoGUI",
                    probe=_probe_input_dd))

# ── L2 动作库扩充新增的 5 个能力 (方案文档 §5 L2 的 requires 列) ──────
# degraded 取值说明: soft = 非 READY 只记一次 warning 并继续 (允许降级执行),
# 因此这些 Windows 原生能力不会因探测抖动误拦用户脚本; net.http 取 hard,
# 因为缺 ssl 时 HTTPS 请求必然失败, 提前拦下比执行期报错更清晰。
register(Capability(id="proc.kill", label="⚙ 结束进程",
                    ext_id=None, degraded="soft",
                    degraded_note="仅 Windows 可用; 系统关键进程被硬白名单拒绝",
                    probe=_probe_proc_kill))
register(Capability(id="net.http", label="🌐 HTTP 请求",
                    ext_id=None, degraded="hard",
                    degraded_note="依赖标准库 urllib.request + ssl",
                    probe=_probe_net_http))
register(Capability(id="power", label="⏻ 电源操作",
                    ext_id=None, degraded="soft",
                    degraded_note="仅 Windows 可用; 真实执行需二次确认",
                    probe=_probe_power))
register(Capability(id="audio", label="🔊 音量控制",
                    ext_id=None, degraded="soft",
                    degraded_note="COM 不可用时回退音量键 (精度受限)",
                    probe=_probe_audio))
register(Capability(id="win32", label="🪟 Win32 窗口样式",
                    ext_id=None, degraded="soft",
                    degraded_note="仅 Windows 可用; 缺 pywin32 时用 ctypes 查找窗口",
                    probe=_probe_win32))
