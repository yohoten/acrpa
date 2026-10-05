# -*- coding: utf-8 -*-
"""capabilities — 可选依赖 / 能力四态注册表 (路线图 阶段二新增项①)。

设计约束 (务必保持)
------------------
* **纯 stdlib**: 模块级仅 import ``os`` / ``threading`` / ``dataclasses`` / ``enum``;
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
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, Optional


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


# ── 探测实现 (惰性 import; 全部避免拉起 tkinter) ──────────────────────
def _probe_cv_match():
    """OpenCV 图像匹配 (阶段二新增项③: 已装 ``cv.match`` 扩展优先)。

    探测顺序:
      ① **已安装扩展** → 惰性加载提供者并跑其 ``self_check()``; 通过 → READY
         (reason 标注「由扩展 cv.match 提供」);
      ② 未装/扩展自检不通过 → 维持原有逻辑: cv2 可用 → READY; 否则 Pillow 可导入
         → READY (找图回退 Pillow, 精度受限); 两者皆无 → MISSING。

    失败开放: 探测自身任何异常一律 READY (绝不因探测故障误伤找图命令)。
    零副作用: ``extensions`` 惰性 import (仅 stdlib), 不拉 tkinter/pyautogui。
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
    # ② 内置回退 (cv2 / Pillow)
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
    """playwright 是否可导入 (仅 import, 不启动浏览器)。"""
    try:
        import playwright.sync_api  # noqa: F401
        return ProbeResult(CapState.READY, "playwright 可用")
    except ImportError as e:
        return ProbeResult(CapState.MISSING, "未安装 playwright: {}".format(e))
    except Exception as e:
        return ProbeResult(CapState.BROKEN, "playwright 已安装但导入失败: {}".format(e))


def _probe_paddle_dll():
    """PaddleOCR.dll 后端: 读用户开关 + 检查 DLL 是否落盘 (不加载 DLL)。"""
    try:
        import state
        enabled = bool(getattr(state, "PADDLE_DLL_ENABLED", False))
    except Exception as e:                # noqa: BLE001
        return ProbeResult(CapState.READY, "探测不可用: {}".format(e))
    dll = os.path.join(_APP_DIR, "lib", "paddle_ocr", "PaddleOCR.dll")
    has_dll = os.path.exists(dll)
    if enabled:
        if has_dll:
            return ProbeResult(CapState.READY, "PaddleOCR.dll 后端已启用")
        return ProbeResult(CapState.BROKEN, "已启用但未找到 PaddleOCR.dll")
    if has_dll:
        return ProbeResult(CapState.MISSING,
                           "未启用 PaddleOCR.dll 后端 (可用 pip paddleocr)")
    return ProbeResult(CapState.MISSING, "未安装/未启用 PaddleOCR.dll 后端")


def _probe_input_dd():
    """DD 内核驱动 (可选, 缺失时 engine 回退 PyAutoGUI)。"""
    try:
        import state
        mode = str(getattr(state, "INPUT_MODE", "") or "").strip().lower()
        flag = bool(getattr(state, "USE_DD_DRIVER", False))
    except Exception as e:                # noqa: BLE001
        return ProbeResult(CapState.READY, "探测不可用: {}".format(e))
    if flag or mode == "dd":
        dll = os.path.join(_APP_DIR, "lib", "dd_driver", "dd63330.dll")
        if os.path.exists(dll):
            return ProbeResult(CapState.READY, "DD 内核驱动已启用")
        return ProbeResult(CapState.BROKEN, "已启用 DD 但未找到 dd63330.dll")
    return ProbeResult(
        CapState.MISSING,
        "未启用 DD 内核驱动 (input_mode={!r}), 回退 PyAutoGUI".format(mode))


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
