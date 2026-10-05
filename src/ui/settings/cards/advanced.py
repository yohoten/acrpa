# -*- coding: utf-8 -*-
"""ui.settings.cards.advanced — ⚒ 高级设置卡 (原 settings_window 1847-2126 行段)。

执行时限 / 绑定窗口 / 出错处理 / OCR 后端·状态·线程 / 浏览器 / 调度轮询 / DD DLL /
PaddleOCR.dll (开关·路径·高级参数)。

导出:
    build(parent, ctx) -> handles
    apply(ctx, handles) -> None       # 等价原 _apply_advanced_settings
"""
import threading
import tkinter
from tkinter import ttk, filedialog

import state
from utils import FONT_TINY

CARD_KEY = "advanced"
ROW = 7

# 高级设置下拉选项 ↔ state 存储值映射（单一来源，UI 与保存共用）
_OCR_BACKEND_MAP = {"自动": "auto", "PaddleOCR": "paddle",
                    "PaddleOCR.dll": "paddle_dll",
                    "Windows OCR": "winrt", "Tesseract": "tesseract"}


def _int_or(var, fallback, lo=None, hi=None):
    """取整数; 非法或越界 → 回退 fallback, 绝不抛异常。

    旧实现用 try/except 包住整块赋值, 一旦某项非法会连带跳过后续所有赋值
    (且有三项被误写进 except 分支 → 永远不落盘, 即 P0-1)。
    """
    try:
        v = int(str(var.get()).strip())
    except (TypeError, ValueError):
        return fallback
    if lo is not None and v < lo:
        return fallback
    if hi is not None and v > hi:
        return fallback
    return v


def build(parent, ctx):
    C = ctx.C
    _btn = ctx.btn
    _log1 = ctx.log1
    _show_toast = ctx.show_toast
    APP_ROOT = ctx.APP_ROOT
    root = ctx.root
    _win = ctx.win
    _validate_number = ctx.validate_number
    _make_collapsible_card = ctx.make_collapsible_card
    _make_badge = ctx.make_badge
    _register_nav_card = ctx.register_nav_card
    _track_card_vars = ctx.track_card_vars

    card, adv_content, adv_title, _ = _make_collapsible_card(parent, "高级设置", "⚒")
    card.grid(row=ROW, column=0, sticky="ew", **ctx.PAD)
    _make_badge(CARD_KEY, adv_title)
    _register_nav_card(CARD_KEY, card)

    # === 行0：执行时限（数值） ===
    adv_exec_frame = tkinter.Frame(adv_content, bg=C["bgc"])
    adv_exec_frame.grid(row=0, column=0, sticky="ew", padx=8, pady=(2, 1))
    max_minutes_var = tkinter.StringVar(value=str(state.MAX_EXECUTION_MINUTES))
    tkinter.Label(adv_exec_frame, text="最大执行:", font=ctx.FONT_BODY,
        fg=C["fgb"], bg=C["bgc"]).pack(side="left", padx=(0,4))
    max_minutes_spin = _validate_number(tkinter.Spinbox(adv_exec_frame, textvariable=max_minutes_var, from_=0, to=1440, width=5,
        font=ctx.FONT_BODY, bg=C["ebg"], fg=C["fgb"],
        relief="solid", bd=1), max_minutes_var, "最大执行时间", 0, int, 0, 1440, card_key="advanced")
    max_minutes_spin.pack(side="left", padx=(0,4))
    tkinter.Label(adv_exec_frame, text="分钟 (0=不限，到达时限后自动停止)", font=ctx.FONT_SMALL,
        fg=C["fgm"], bg=C["bgc"]).pack(side="left")

    # === 行1：绑定窗口（文本） ===
    adv_bind_frame = tkinter.Frame(adv_content, bg=C["bgc"])
    adv_bind_frame.grid(row=1, column=0, sticky="ew", padx=8, pady=1)
    tkinter.Label(adv_bind_frame, text="绑定窗口:", font=ctx.FONT_BODY,
        fg=C["fgb"], bg=C["bgc"]).pack(side="left", padx=(0,4))
    bound_window_var = tkinter.StringVar(value=state.BOUND_WINDOW_TITLE)
    tkinter.Entry(adv_bind_frame, textvariable=bound_window_var, width=26,
        font=ctx.FONT_SMALL, bg=C["ebg"], fg=C["fgb"],
        relief="solid", bd=1).pack(side="left", padx=(0,4))
    tkinter.Label(adv_bind_frame, text="(标题包含即匹配，空=不绑定)", font=ctx.FONT_SMALL,
        fg=C["fgm"], bg=C["bgc"]).pack(side="left")

    # === 行2：出错处理（开关） ===
    adv_opt_frame = tkinter.Frame(adv_content, bg=C["bgc"])
    adv_opt_frame.grid(row=2, column=0, sticky="ew", padx=8, pady=(1, 2))
    stop_on_error_var = tkinter.BooleanVar(value=state.STOP_ON_ERROR)
    ttk.Checkbutton(adv_opt_frame, text="出错立即停止", variable=stop_on_error_var).pack(side="left")

    # === OCR 后端行 (P1-5 增强) ===
    adv_ocr_frame = tkinter.Frame(adv_content, bg=C["bgc"])
    adv_ocr_frame.grid(row=3, column=0, sticky="ew", padx=8, pady=(2, 1))
    tkinter.Label(adv_ocr_frame, text="OCR后端:", font=ctx.FONT_BODY,
        fg=C["fgb"], bg=C["bgc"]).pack(side="left", padx=(0,4))
    _backend_label = {v: k for k, v in _OCR_BACKEND_MAP.items()}
    ocr_backend_var = tkinter.StringVar(value=_backend_label.get(state.OCR_PREFERRED_BACKEND, "自动"))
    ttk.Combobox(adv_ocr_frame, textvariable=ocr_backend_var,
        values=("自动", "PaddleOCR", "PaddleOCR.dll", "Windows OCR", "Tesseract"),
        state="readonly", width=13).pack(side="left", padx=(0,8))
    tkinter.Label(adv_ocr_frame, text="Paddle模型目录:", font=ctx.FONT_BODY,
        fg=C["fgb"], bg=C["bgc"]).pack(side="left", padx=(0,4))
    ocr_paddle_var = tkinter.StringVar(value=state.OCR_PADDLE_DIR)
    tkinter.Entry(adv_ocr_frame, textvariable=ocr_paddle_var, width=22,
        font=ctx.FONT_SMALL, bg=C["ebg"], fg=C["fgb"],
        relief="solid", bd=1).pack(side="left", padx=(0,4))
    _ocr_browse_btn = _btn(adv_ocr_frame, "浏览", lambda: ocr_paddle_var.set(filedialog.askdirectory(
        title="选择 PaddleOCR 模型目录", initialdir=APP_ROOT) or ocr_paddle_var.get()),
        C["ac"], "white", tip="选择 PaddleOCR 模型目录")
    _ocr_browse_btn.pack(side="left")

    # === 行4：OCR 状态（只读状态行 + 动作，不参与保存） ===
    ocr_status_frame = tkinter.Frame(adv_content, bg=C["bgc"])
    ocr_status_frame.grid(row=4, column=0, sticky="ew", padx=8, pady=1)
    ocr_status_var = tkinter.StringVar(value="检测中…")
    tkinter.Label(ocr_status_frame, text="OCR 状态:", font=ctx.FONT_SMALL,
        fg=C["fgm"], bg=C["bgc"]).pack(side="left", padx=(0, 6))
    tkinter.Label(ocr_status_frame, textvariable=ocr_status_var, font=ctx.FONT_SMALL,
        fg=C["sc"], bg=C["bgc"], wraplength=330,
        justify="left").pack(side="left", padx=(0, 8))

    def _refresh_ocr_status():
        """显示真实后端状态（ocr_backend.ocr_get_backend_info），不可用时说明原因。

        状态探测会触发后端惰性加载（pip 版 PaddleOCR 需加载模型，可能耗时数秒），
        因此放后台线程执行，结果再回主线程刷新文本，避免打开设置窗口时卡顿。
        """
        ocr_status_var.set("检测中…")

        def _probe():
            try:
                from ocr_backend import ocr_get_backend_info
                info = ocr_get_backend_info() or {}
                if info.get("available"):
                    text = "● 已就绪: {}".format(
                        info.get("name") or info.get("backend") or "未知后端")
                else:
                    text = ("○ 无可用后端（pip 版 PaddleOCR / Windows OCR / "
                            "Tesseract 均未就绪）")
            except Exception as e:
                text = "状态检测失败: {}".format(e)
            try:
                _win.after(0, lambda: ocr_status_var.set(text))
            except Exception:
                pass

        try:
            threading.Thread(target=_probe, daemon=True).start()
        except Exception:
            _probe()

    def _reload_ocr():
        """重置后端探测，使 OCR 后端/模型目录/线程等改动立即参与下次识别。"""
        try:
            from ocr_backend import ocr_reset_backend
            ocr_reset_backend()
            _log1("OCR 后端已重置（下次识别按新设置重新探测）")
            _show_toast(root, "OCR 后端已重置", "success")
        except Exception as e:
            _log1("OCR 后端重置失败: {}".format(e), "error")
            _show_toast(root, "OCR 后端重置失败: {}".format(e), "error")
        _refresh_ocr_status()

    _btn(ocr_status_frame, "刷新状态", _refresh_ocr_status, "ac", "white",
        tip="重新检测当前可用的 OCR 后端").pack(side="left", padx=(0, 6))
    _btn(ocr_status_frame, "应用并重载", _reload_ocr, "ac", "white",
        tip="清空已缓存的 OCR 引擎，下次识别按新设置重新加载").pack(side="left")

    # === 行5：OCR 预热 + CPU 线程（数值/开关） ===
    ocr_pref_frame = tkinter.Frame(adv_content, bg=C["bgc"])
    ocr_pref_frame.grid(row=5, column=0, sticky="ew", padx=8, pady=(1, 2))
    ocr_preload_var = tkinter.BooleanVar(value=getattr(state, 'OCR_PRELOAD', False))
    ttk.Checkbutton(ocr_pref_frame, text="启动时预热", variable=ocr_preload_var).pack(side="left", padx=(0, 12))
    tkinter.Label(ocr_pref_frame, text="CPU线程:", font=ctx.FONT_SMALL,
        fg=C["fgm"], bg=C["bgc"]).pack(side="left", padx=(0, 4))
    ocr_threads_var = tkinter.StringVar(value=str(getattr(state, 'OCR_THREADS', 8)))
    ocr_threads_spin = _validate_number(tkinter.Spinbox(ocr_pref_frame, textvariable=ocr_threads_var,
        from_=1, to=32, width=4, font=ctx.FONT_SMALL,
        bg=C["ebg"], fg=C["fgb"], relief="solid", bd=1), ocr_threads_var,
        "OCR 线程数", 8, int, 1, 32, card_key="advanced")
    ocr_threads_spin.pack(side="left", padx=(0,4))
    tkinter.Label(ocr_pref_frame, text="(1-32，pip 版与 DLL 后端共用)", font=FONT_TINY,
        fg=C["fgm"], bg=C["bgc"]).pack(side="left")

    # === 行8：PaddleOCR.dll 开关 + 自检（结论就地显示，不再弹模态） ===
    adv_pdll_frame = tkinter.Frame(adv_content, bg=C["bgc"])
    adv_pdll_frame.grid(row=8, column=0, sticky="ew", padx=8, pady=(4, 1))
    paddle_dll_enabled_var = tkinter.BooleanVar(
        value=bool(getattr(state, "PADDLE_DLL_ENABLED", False)))
    ttk.Checkbutton(adv_pdll_frame, text="启用 PaddleOCR.dll",
        variable=paddle_dll_enabled_var).pack(side="left", padx=(0,8))
    pdll_status_var = tkinter.StringVar(value="未自检")

    def _check_paddle_dll():
        """就地自检: 显示结论并重置后端探测，使新设置立即参与下次识别。"""
        try:
            import paddle_dll
            report = paddle_dll.format_diagnosis()
            _log1(report)
            try:
                from ocr_backend import ocr_reset_backend
                ocr_reset_backend()
            except Exception:
                pass
            lines = [ln.strip() for ln in report.splitlines() if ln.strip()]
            pdll_status_var.set((lines[-1][:80] if lines else "自检完成"))
            _refresh_ocr_status()
        except Exception as e:
            pdll_status_var.set("自检失败: {}".format(e))
    _btn(adv_pdll_frame, "DLL 自检", _check_paddle_dll, "ac", "white",
        tip="检查 DLL/依赖/模型是否就绪(依赖不全时不可调用)").pack(side="left", padx=(0,8))
    tkinter.Label(adv_pdll_frame, textvariable=pdll_status_var, font=FONT_TINY,
        fg=C["fgm"], bg=C["bgc"], wraplength=320,
        justify="left").pack(side="left")

    # === 行9：PaddleOCR.dll 路径（目录类） ===
    adv_pdll_path = tkinter.Frame(adv_content, bg=C["bgc"])
    adv_pdll_path.grid(row=9, column=0, sticky="ew", padx=8, pady=1)
    tkinter.Label(adv_pdll_path, text="DLL目录:", font=ctx.FONT_BODY,
        fg=C["fgb"], bg=C["bgc"]).pack(side="left", padx=(0,4))
    paddle_dll_dir_var = tkinter.StringVar(value=getattr(state, "PADDLE_DLL_DIR", ""))
    tkinter.Entry(adv_pdll_path, textvariable=paddle_dll_dir_var, width=18,
        font=ctx.FONT_SMALL, bg=C["ebg"], fg=C["fgb"],
        relief="solid", bd=1).pack(side="left", padx=(0,4))
    _pdll_dir_btn = _btn(adv_pdll_path, "浏览", lambda: paddle_dll_dir_var.set(filedialog.askdirectory(
        title="选择含 PaddleOCR.dll 的目录", initialdir=APP_ROOT) or paddle_dll_dir_var.get()),
        C["ac"], "white", tip="包含 PaddleOCR.dll 与 4 个依赖 DLL 的纯英文路径目录(空=lib/paddle_ocr)")
    _pdll_dir_btn.pack(side="left", padx=(0,8))
    tkinter.Label(adv_pdll_path, text="模型目录:", font=ctx.FONT_BODY,
        fg=C["fgb"], bg=C["bgc"]).pack(side="left", padx=(0,4))
    paddle_dll_model_dir_var = tkinter.StringVar(value=getattr(state, "PADDLE_DLL_MODEL_DIR", ""))
    tkinter.Entry(adv_pdll_path, textvariable=paddle_dll_model_dir_var, width=18,
        font=ctx.FONT_SMALL, bg=C["ebg"], fg=C["fgb"],
        relief="solid", bd=1).pack(side="left", padx=(0,4))
    _pdll_model_btn = _btn(adv_pdll_path, "浏览", lambda: paddle_dll_model_dir_var.set(filedialog.askdirectory(
        title="选择模型目录 (含 inference.json + inference.pdiparams)",
        initialdir=paddle_dll_dir_var.get() or APP_ROOT) or paddle_dll_model_dir_var.get()),
        C["ac"], "white", tip="模型集合目录(如 inference/)，空=在 DLL 目录下自动查找")
    _pdll_model_btn.pack(side="left")

    # === 行10：PaddleOCR.dll 高级参数（JSON / 授权 / 调用原型） ===
    adv_pdll_adv = tkinter.Frame(adv_content, bg=C["bgc"])
    adv_pdll_adv.grid(row=10, column=0, sticky="ew", padx=8, pady=(1, 2))
    tkinter.Label(adv_pdll_adv, text="推理参数:", font=ctx.FONT_BODY,
        fg=C["fgb"], bg=C["bgc"]).pack(side="left", padx=(0,4))
    paddle_dll_config_var = tkinter.StringVar(value=getattr(state, "PADDLE_DLL_CONFIG", ""))
    tkinter.Entry(adv_pdll_adv, textvariable=paddle_dll_config_var, width=20,
        font=FONT_TINY, bg=C["ebg"], fg=C["fgb"],
        relief="solid", bd=1).pack(side="left", padx=(0,4))
    tkinter.Label(adv_pdll_adv, text="JSON(空=用自带)", font=FONT_TINY,
        fg=C["fgm"], bg=C["bgc"]).pack(side="left", padx=(0,10))
    tkinter.Label(adv_pdll_adv, text="授权串:", font=ctx.FONT_BODY,
        fg=C["fgb"], bg=C["bgc"]).pack(side="left", padx=(0,4))
    paddle_dll_license_var = tkinter.StringVar(value=getattr(state, "PADDLE_DLL_LICENSE", ""))
    tkinter.Entry(adv_pdll_adv, textvariable=paddle_dll_license_var, width=10, show="*",
        font=FONT_TINY, bg=C["ebg"], fg=C["fgb"],
        relief="solid", bd=1).pack(side="left", padx=(0,10))
    tkinter.Label(adv_pdll_adv, text="调用原型:", font=ctx.FONT_BODY,
        fg=C["fgb"], bg=C["bgc"]).pack(side="left", padx=(0,4))
    paddle_dll_proto_init_var = tkinter.StringVar(
        value=getattr(state, "PADDLE_DLL_PROTO_INIT", "json5"))
    tkinter.Entry(adv_pdll_adv, textvariable=paddle_dll_proto_init_var, width=8,
        font=FONT_TINY, bg=C["ebg"], fg=C["fgb"],
        relief="solid", bd=1).pack(side="left", padx=(0,2))
    tkinter.Label(adv_pdll_adv, text="/", font=FONT_TINY,
        fg=C["fgm"], bg=C["bgc"]).pack(side="left")
    paddle_dll_proto_detect_var = tkinter.StringVar(
        value=getattr(state, "PADDLE_DLL_PROTO_DETECT", "ptr_byte"))
    tkinter.Entry(adv_pdll_adv, textvariable=paddle_dll_proto_detect_var, width=9,
        font=FONT_TINY, bg=C["ebg"], fg=C["fgb"],
        relief="solid", bd=1).pack(side="left")

    # OCR 状态首次检测放到窗口显示后执行（避免打开时阻塞）
    _win.after(500, _refresh_ocr_status)

    # === 浏览器行 ===
    adv_br_frame = tkinter.Frame(adv_content, bg=C["bgc"])
    adv_br_frame.grid(row=6, column=0, sticky="ew", padx=8, pady=(4, 1))
    browser_headless_var = tkinter.BooleanVar(value=state.BROWSER_HEADLESS)
    ttk.Checkbutton(adv_br_frame, text="浏览器无头模式", variable=browser_headless_var).pack(side="left", padx=(0,16))
    tkinter.Label(adv_br_frame, text="慢放:", font=ctx.FONT_BODY,
        fg=C["fgb"], bg=C["bgc"]).pack(side="left", padx=(0,4))
    browser_slowmo_var = tkinter.StringVar(value=str(state.BROWSER_SLOW_MO))
    browser_slowmo_spin = _validate_number(tkinter.Spinbox(adv_br_frame, textvariable=browser_slowmo_var, from_=0, to=5000,
        increment=50, width=6, font=ctx.FONT_BODY,
        bg=C["ebg"], fg=C["fgb"], relief="solid", bd=1), browser_slowmo_var,
        "浏览器慢放", 0, int, 0, 5000, card_key="advanced")
    browser_slowmo_spin.pack(side="left", padx=(0,4))
    tkinter.Label(adv_br_frame, text="ms (0=最快)", font=ctx.FONT_SMALL,
        fg=C["fgm"], bg=C["bgc"]).pack(side="left")

    # === 定时轮询 + DD DLL 行 ===
    adv_sys_frame = tkinter.Frame(adv_content, bg=C["bgc"])
    adv_sys_frame.grid(row=7, column=0, sticky="ew", padx=8, pady=(1, 2))
    tkinter.Label(adv_sys_frame, text="调度轮询:", font=ctx.FONT_BODY,
        fg=C["fgb"], bg=C["bgc"]).pack(side="left", padx=(0,4))
    sched_poll_var = tkinter.StringVar(value=str(state.SCHED_POLL_INTERVAL))
    sched_poll_spin = _validate_number(tkinter.Spinbox(adv_sys_frame, textvariable=sched_poll_var, from_=5, to=600,
        increment=5, width=5, font=ctx.FONT_BODY,
        bg=C["ebg"], fg=C["fgb"], relief="solid", bd=1), sched_poll_var,
        "调度轮询间隔", 30, int, 5, 600, card_key="advanced")
    sched_poll_spin.pack(side="left", padx=(0,4))
    tkinter.Label(adv_sys_frame, text="秒", font=ctx.FONT_SMALL,
        fg=C["fgm"], bg=C["bgc"]).pack(side="left", padx=(0,12))
    tkinter.Label(adv_sys_frame, text="DD DLL路径:", font=ctx.FONT_BODY,
        fg=C["fgb"], bg=C["bgc"]).pack(side="left", padx=(0,4))
    dd_dll_path_var = tkinter.StringVar(value=state.DD_DLL_PATH)
    tkinter.Entry(adv_sys_frame, textvariable=dd_dll_path_var, width=20,
        font=ctx.FONT_SMALL, bg=C["ebg"], fg=C["fgb"],
        relief="solid", bd=1).pack(side="left", padx=(0,4))
    _dd_browse_btn = _btn(adv_sys_frame, "浏览", lambda: dd_dll_path_var.set(filedialog.askopenfilename(
        title="选择 DD 驱动 DLL", filetypes=[("DLL", "*.dll"), ("All", "*.*")],
        initialdir=APP_ROOT) or dd_dll_path_var.get()),
        C["ac"], "white", tip="选择 DD 驱动 DLL 文件")
    _dd_browse_btn.pack(side="left")

    tkinter.Label(adv_content,
        text="提示：以上设置大多在下次识别/启动时生效；卡片角标显示「✔ 已保存」表示已落盘，"
             "若显示「⚠ 保存失败」可点击查看原因并重试",
        font=ctx.FONT_SMALL, bg=C["bgc"], fg=C["fgm"], wraplength=560,
        justify="left").grid(row=11, column=0, sticky="w", padx=10, pady=(2, 6))

    # 绑定防抖保存 (高级设置卡)
    _track_card_vars("advanced", (max_minutes_var, bound_window_var, stop_on_error_var,
        ocr_backend_var, ocr_paddle_var, ocr_preload_var, ocr_threads_var,
        browser_headless_var, browser_slowmo_var, sched_poll_var, dd_dll_path_var,
        paddle_dll_enabled_var, paddle_dll_dir_var, paddle_dll_model_dir_var,
        paddle_dll_config_var, paddle_dll_license_var,
        paddle_dll_proto_init_var, paddle_dll_proto_detect_var))

    return {
        "card": card, "content": adv_content, "title": adv_title,
        "max_minutes_var": max_minutes_var, "bound_window_var": bound_window_var,
        "stop_on_error_var": stop_on_error_var, "ocr_backend_var": ocr_backend_var,
        "ocr_paddle_var": ocr_paddle_var, "ocr_preload_var": ocr_preload_var,
        "ocr_threads_var": ocr_threads_var,
        "browser_headless_var": browser_headless_var,
        "browser_slowmo_var": browser_slowmo_var,
        "sched_poll_var": sched_poll_var, "dd_dll_path_var": dd_dll_path_var,
        "paddle_dll_enabled_var": paddle_dll_enabled_var,
        "paddle_dll_dir_var": paddle_dll_dir_var,
        "paddle_dll_model_dir_var": paddle_dll_model_dir_var,
        "paddle_dll_config_var": paddle_dll_config_var,
        "paddle_dll_license_var": paddle_dll_license_var,
        "paddle_dll_proto_init_var": paddle_dll_proto_init_var,
        "paddle_dll_proto_detect_var": paddle_dll_proto_detect_var,
    }


def apply(ctx, handles):
    """保存「高级设置」卡配置（运行时由对应模块按需读取）。

    所有赋值平铺: 任一项非法都不影响其它项落盘; 越界值回退到既有值 (P0-1 修复)。
    """
    state.MAX_EXECUTION_MINUTES = _int_or(handles["max_minutes_var"], state.MAX_EXECUTION_MINUTES, 0, 1440)
    state.BOUND_WINDOW_TITLE = handles["bound_window_var"].get().strip()
    state.STOP_ON_ERROR = bool(handles["stop_on_error_var"].get())
    state.OCR_PREFERRED_BACKEND = _OCR_BACKEND_MAP.get(handles["ocr_backend_var"].get(), "auto")
    state.OCR_PADDLE_DIR = handles["ocr_paddle_var"].get().strip()
    state.OCR_PRELOAD = bool(handles["ocr_preload_var"].get())
    state.OCR_THREADS = _int_or(handles["ocr_threads_var"], 8, 1, 32)
    state.BROWSER_HEADLESS = bool(handles["browser_headless_var"].get())
    state.BROWSER_SLOW_MO = _int_or(handles["browser_slowmo_var"], 0, 0, 5000)
    state.SCHED_POLL_INTERVAL = _int_or(handles["sched_poll_var"], 30, 5, 600)
    state.DD_DLL_PATH = handles["dd_dll_path_var"].get().strip()
    # PaddleOCR.dll 原生后端（默认关闭；依赖/模型缺失时自动回退其它后端）
    state.PADDLE_DLL_ENABLED = bool(handles["paddle_dll_enabled_var"].get())
    state.PADDLE_DLL_DIR = handles["paddle_dll_dir_var"].get().strip()
    state.PADDLE_DLL_MODEL_DIR = handles["paddle_dll_model_dir_var"].get().strip()
    state.PADDLE_DLL_PROTO_INIT = handles["paddle_dll_proto_init_var"].get().strip() or "json5"
    state.PADDLE_DLL_PROTO_DETECT = handles["paddle_dll_proto_detect_var"].get().strip() or "ptr_byte"
    state.PADDLE_DLL_CONFIG = handles["paddle_dll_config_var"].get().strip()
    state.PADDLE_DLL_LICENSE = handles["paddle_dll_license_var"].get().strip()
