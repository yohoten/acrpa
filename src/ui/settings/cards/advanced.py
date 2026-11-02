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
from tkinter import ttk, filedialog, messagebox

import state
from utils import FONT_TINY

from ui.settings.cards._layout import section, row, help_line

CARD_KEY = "advanced"
ROW = 7

# 高级设置下拉选项 ↔ state 存储值映射（单一来源，UI 与保存共用）
_OCR_BACKEND_MAP = {"自动": "auto", "PaddleOCR": "paddle",
                    "PaddleOCR.dll": "paddle_dll",
                    "Windows OCR": "winrt", "Tesseract": "tesseract"}

# 浏览器类型（通道）↔ state 存储值；空串=内置 Chromium
_CHANNEL_LABEL_OF = {"": "内置 Chromium", "chrome": "Chrome", "edge": "Edge",
                     "firefox": "Firefox", "webkit": "WebKit"}
_CHANNEL_VALUE_OF = {v: k for k, v in _CHANNEL_LABEL_OF.items()}


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
    sp = ctx.sp

    card, adv_content, adv_title, _ = _make_collapsible_card(parent, "高级设置", "⚒")
    card.grid(row=ROW, column=0, sticky="ew", **ctx.PAD)
    _make_badge(CARD_KEY, adv_title)
    _register_nav_card(CARD_KEY, card)

    _r = 0

    def _add(w, pady=None):
        nonlocal _r
        w.grid(row=_r, column=0, sticky="ew",
               pady=(sp("row_gap") if pady is None else pady))
        _r += 1
        return w

    def _sec(name, first=False):
        return _add(section(adv_content, ctx, name),
                    pady=(sp("sp_sm") if first else sp("section_gap"), 0))

    def _spin(p, var, frm, to, inc, w, key, fb, lo, hi):
        return _validate_number(tkinter.Spinbox(p, textvariable=var, from_=frm, to=to,
            increment=inc, width=w, font=ctx.FONT_SMALL, bg=C["ebg"], fg=C["fgb"],
            relief="solid", bd=1), var, key, fb, int, lo, hi, card_key="advanced")

    def _entry(p, var, w, show=None, tiny=False):
        return tkinter.Entry(p, textvariable=var, width=w, show=show,
            font=(FONT_TINY if tiny else ctx.FONT_SMALL), bg=C["ebg"], fg=C["fgb"],
            relief="solid", bd=1)

    _sec("执行与绑定", first=True)

    max_minutes_var = tkinter.StringVar(value=str(state.MAX_EXECUTION_MINUTES))
    _add(row(adv_content, ctx, "最大执行", lambda p: _spin(
        p, max_minutes_var, 0, 1440, 1, 5, "最大执行时间", 0, 0, 1440),
        unit="分钟", help="0=不限，到达时限后自动停止"))
    bound_window_var = tkinter.StringVar(value=state.BOUND_WINDOW_TITLE)
    _add(row(adv_content, ctx, "绑定窗口", lambda p: _entry(
        p, bound_window_var, 26), help="标题包含即匹配，空=不绑定"))
    stop_on_error_var = tkinter.BooleanVar(value=state.STOP_ON_ERROR)
    _add(row(adv_content, ctx, "", lambda p: ttk.Checkbutton(
        p, text="出错立即停止", variable=stop_on_error_var)))

    _sec("OCR 识别")
    _backend_label = {v: k for k, v in _OCR_BACKEND_MAP.items()}
    ocr_backend_var = tkinter.StringVar(
        value=_backend_label.get(state.OCR_PREFERRED_BACKEND, "自动"))
    _add(row(adv_content, ctx, "OCR 后端", lambda p: ttk.Combobox(
        p, textvariable=ocr_backend_var,
        values=("自动", "PaddleOCR", "PaddleOCR.dll", "Windows OCR", "Tesseract"),
        state="readonly", width=13),
        help="自动=按可用性探测；PaddleOCR.dll 需在下方开关勾选"))

    ocr_paddle_var = tkinter.StringVar(value=state.OCR_PADDLE_DIR)

    def _mk_paddle_dir(p):
        f = tkinter.Frame(p, bg=C["bgc"])
        tkinter.Entry(f, textvariable=ocr_paddle_var, width=22,
            font=ctx.FONT_SMALL, bg=C["ebg"], fg=C["fgb"],
            relief="solid", bd=1).pack(side="left", padx=(0, 4))
        _btn(f, "浏览", lambda: ocr_paddle_var.set(filedialog.askdirectory(
            title="选择 PaddleOCR 模型目录", initialdir=APP_ROOT) or ocr_paddle_var.get()),
            C["ac"], "white", tip="选择 PaddleOCR 模型目录").pack(side="left")
        return f

    _add(row(adv_content, ctx, "Paddle 模型目录", _mk_paddle_dir,
        help="pip 版 PaddleOCR 的模型目录"))

    # OCR 状态（只读状态行 + 动作，不参与保存）
    ocr_status_var = tkinter.StringVar(value="检测中…")

    def _ocr_unavailable_reason():
        """无可用后端时的**可读原因**（复用 capabilities / ocr_backend 的 reason）。

        不新造一套文案体系：原生 DLL 后端的原因直接取
        ``capabilities.probe("ocr.paddle_dll").reason`` —— 与命令角标、能力体检
        卡片是同一份文本；未启用时给出可执行的下一步。
        """
        bits = []
        try:
            import capabilities
            if bool(getattr(state, "PADDLE_DLL_ENABLED", False)):
                why = (capabilities.probe("ocr.paddle_dll").reason or "").strip()
                if why:
                    bits.append("PaddleOCR.dll: {}".format(why))
            else:
                bits.append("PaddleOCR.dll 原生后端未启用")
        except Exception:                  # noqa: BLE001 — 探测故障不影响状态行显示
            pass
        if not bits:
            bits.append("pip 版 PaddleOCR / Windows OCR / Tesseract 均未就绪")
        bits.append("最短修复路径: 勾「启用 PaddleOCR.dll」→ 点「DLL 自检」按清单补齐"
                    "依赖与模型; 或 pip install paddlepaddle paddleocr")
        return "；".join(bits)

    def _refresh_ocr_status():
        """显示真实后端状态（ocr_backend.ocr_get_backend_info）+ 不可用时说明原因。

        状态探测会触发后端惰性加载（pip 版 PaddleOCR 需加载模型，可能耗时数秒），
        因此放后台线程执行，结果再回主线程刷新文本，避免打开设置窗口时卡顿。
        """
        ocr_status_var.set("检测中…")

        def _probe():
            try:
                from ocr_backend import ocr_get_backend_info
                info = ocr_get_backend_info() or {}
                pref = _backend_label.get(
                    getattr(state, "OCR_PREFERRED_BACKEND", "auto"), "自动")
                if info.get("available"):
                    text = "● 已就绪: {}（偏好: {}）".format(
                        info.get("name") or info.get("backend") or "未知后端", pref)
                else:
                    text = "○ 无可用后端（偏好: {}）—— {}".format(
                        pref, _ocr_unavailable_reason())
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

    def _mk_ocr_status(p):
        f = tkinter.Frame(p, bg=C["bgc"])
        tkinter.Label(f, textvariable=ocr_status_var, font=ctx.FONT_SMALL,
            fg=C["sc"], bg=C["bgc"], wraplength=330,
            justify="left").pack(side="left", padx=(0, 8))
        _btn(f, "刷新状态", _refresh_ocr_status, "ac", "white",
            tip="重新检测当前可用的 OCR 后端").pack(side="left", padx=(0, 6))
        _btn(f, "应用并重载", _reload_ocr, "ac", "white",
            tip="清空已缓存的 OCR 引擎，下次识别按新设置重新加载").pack(side="left")
        return f

    _add(row(adv_content, ctx, "OCR 状态", _mk_ocr_status))

    ocr_preload_var = tkinter.BooleanVar(value=getattr(state, 'OCR_PRELOAD', False))
    _add(row(adv_content, ctx, "", lambda p: ttk.Checkbutton(
        p, text="启动时预热", variable=ocr_preload_var)))
    ocr_threads_var = tkinter.StringVar(value=str(getattr(state, 'OCR_THREADS', 8)))
    _add(row(adv_content, ctx, "CPU 线程", lambda p: _spin(
        p, ocr_threads_var, 1, 32, 1, 4, "OCR 线程数", 8, 1, 32),
        unit="1-32", help="pip 版与 DLL 后端共用"))

    _sec("PaddleOCR.dll（原生后端）")
    paddle_dll_enabled_var = tkinter.BooleanVar(
        value=bool(getattr(state, "PADDLE_DLL_ENABLED", False)))
    pdll_status_var = tkinter.StringVar(value="未自检")

    def _format_self_check(report):
        """``paddle_dll.self_check_report()`` → 人类可读的**逐项清单**
        （✔ 就绪 / ✘ 缺失，逐文件列举，缺失项附应放路径）。"""
        verdict = {"ready": "✔ 就绪（可在左侧勾选启用）",
                   "missing": "✘ 未安装 / 未放对目录",
                   "broken": "✘ 存在但不可用 —— 请按下面标 ✘ 的项补齐"}.get(
                       report.get("state"), str(report.get("state")))
        kind = {"dll": "主模块", "dep": "原生依赖", "model": "模型文件",
                "legacy": "旧版兼容"}
        lines = ["结论: {}".format(verdict),
                 "DLL 目录 : {}".format(report.get("dll_dir") or "未找到"),
                 "模型目录 : {}".format(report.get("model_dir") or "未找到/未配置"),
                 ""]
        for it in report.get("items") or []:
            lines.append("{} {:<24} ({})".format(
                "✔" if it.get("ok") else "✘", it.get("name"),
                kind.get(it.get("kind"), it.get("kind"))))
            if not it.get("ok") and it.get("path"):
                lines.append("     缺: {}".format(it.get("path")))
        lines.append("")
        lines.append(report.get("reason") or "")
        return "\n".join(lines)

    def _check_paddle_dll():
        """「DLL 自检」: 就地出结论 + 弹出逐项清单（数据来自 paddle_dll.self_check()）。

        静态检查（**不加载 DLL**，不会因 Access Violation 打死进程），故同步执行；
        完成后重置后端探测，使新设置立即参与下次识别。
        """
        try:
            import paddle_dll
            report = paddle_dll.self_check_report()
        except Exception as e:
            pdll_status_var.set("自检失败: {}".format(e))
            return
        chk_state = report.get("state")
        missing = list(report.get("missing") or [])
        if chk_state == "ready":
            pdll_status_var.set("✔ 就绪: 依赖齐全 + 模型齐全 + 路径纯 ASCII")
        elif chk_state == "missing":
            pdll_status_var.set("✘ 未找到 {} —— 见「DLL 自检」清单".format(
                getattr(paddle_dll, "DLL_NAME", "PaddleOCR.dll")))
        elif missing:
            pdll_status_var.set("✘ 缺 {} 项: {}".format(len(missing), ", ".join(missing)))
        else:
            pdll_status_var.set("✘ 不可用: {}".format((report.get("reason") or "")[:70]))
        try:
            from ocr_backend import ocr_reset_backend
            ocr_reset_backend()
        except Exception:
            pass
        _refresh_ocr_status()
        text = _format_self_check(report)
        _log1("PaddleOCR.dll 自检: {}".format(report.get("reason") or chk_state))
        _log1(text)
        try:
            messagebox.showinfo("PaddleOCR.dll 自检", text, parent=_win)
        except Exception:                  # noqa: BLE001 — 无 GUI 环境下只留日志
            pass
    def _mk_pdll_enable(p):
        f = tkinter.Frame(p, bg=C["bgc"])
        ttk.Checkbutton(f, text="启用 PaddleOCR.dll",
            variable=paddle_dll_enabled_var).pack(side="left", padx=(0, 8))
        _btn(f, "DLL 自检", _check_paddle_dll, "ac", "white",
            tip="静态检查 DLL/4 个依赖/模型文件与路径编码，并逐项列出缺失文件").pack(
                side="left", padx=(0, 8))
        tkinter.Label(f, textvariable=pdll_status_var, font=FONT_TINY,
            fg=C["fgm"], bg=C["bgc"], wraplength=320,
            justify="left").pack(side="left")
        return f

    _add(row(adv_content, ctx, "", _mk_pdll_enable))

    paddle_dll_dir_var = tkinter.StringVar(value=getattr(state, "PADDLE_DLL_DIR", ""))

    def _mk_pdll_dir(p):
        f = tkinter.Frame(p, bg=C["bgc"])
        tkinter.Entry(f, textvariable=paddle_dll_dir_var, width=18,
            font=ctx.FONT_SMALL, bg=C["ebg"], fg=C["fgb"],
            relief="solid", bd=1).pack(side="left", padx=(0, 4))
        _btn(f, "浏览", lambda: paddle_dll_dir_var.set(filedialog.askdirectory(
            title="选择含 PaddleOCR.dll 的目录", initialdir=APP_ROOT) or paddle_dll_dir_var.get()),
            C["ac"], "white",
            tip="包含 PaddleOCR.dll 与 4 个依赖 DLL 的纯英文路径目录(空=lib/paddle_ocr)").pack(
                side="left")
        return f

    _add(row(adv_content, ctx, "DLL 目录", _mk_pdll_dir,
        help="含 PaddleOCR.dll 与 4 个依赖 DLL 的纯英文路径（空=lib/paddle_ocr）"))

    paddle_dll_model_dir_var = tkinter.StringVar(value=getattr(state, "PADDLE_DLL_MODEL_DIR", ""))

    def _mk_pdll_model(p):
        f = tkinter.Frame(p, bg=C["bgc"])
        tkinter.Entry(f, textvariable=paddle_dll_model_dir_var, width=18,
            font=ctx.FONT_SMALL, bg=C["ebg"], fg=C["fgb"],
            relief="solid", bd=1).pack(side="left", padx=(0, 4))
        _btn(f, "浏览", lambda: paddle_dll_model_dir_var.set(filedialog.askdirectory(
            title="选择模型目录 (含 inference.json + inference.pdiparams)",
            initialdir=paddle_dll_dir_var.get() or APP_ROOT) or paddle_dll_model_dir_var.get()),
            C["ac"], "white",
            tip="模型集合目录(如 inference/)，空=在 DLL 目录下自动查找").pack(side="left")
        return f

    _add(row(adv_content, ctx, "模型目录", _mk_pdll_model,
        help="模型集合目录（如 inference/），空=在 DLL 目录下自动查找"))

    paddle_dll_config_var = tkinter.StringVar(value=getattr(state, "PADDLE_DLL_CONFIG", ""))
    _add(row(adv_content, ctx, "推理参数", lambda p: _entry(
        p, paddle_dll_config_var, 20, tiny=True), help="JSON（空=用自带）"))
    paddle_dll_license_var = tkinter.StringVar(value=getattr(state, "PADDLE_DLL_LICENSE", ""))
    _add(row(adv_content, ctx, "授权串", lambda p: _entry(
        p, paddle_dll_license_var, 10, show="*", tiny=True)))

    paddle_dll_proto_init_var = tkinter.StringVar(
        value=getattr(state, "PADDLE_DLL_PROTO_INIT", "json5"))
    paddle_dll_proto_detect_var = tkinter.StringVar(
        value=getattr(state, "PADDLE_DLL_PROTO_DETECT", "ptr_byte"))

    def _mk_proto(p):
        f = tkinter.Frame(p, bg=C["bgc"])
        tkinter.Entry(f, textvariable=paddle_dll_proto_init_var, width=8,
            font=FONT_TINY, bg=C["ebg"], fg=C["fgb"],
            relief="solid", bd=1).pack(side="left", padx=(0, 2))
        tkinter.Label(f, text="/", font=FONT_TINY,
            fg=C["fgm"], bg=C["bgc"]).pack(side="left")
        tkinter.Entry(f, textvariable=paddle_dll_proto_detect_var, width=9,
            font=FONT_TINY, bg=C["ebg"], fg=C["fgb"],
            relief="solid", bd=1).pack(side="left")
        return f

    _add(row(adv_content, ctx, "调用原型", _mk_proto, help="init / detect 调用原型"))

    # OCR 状态首次检测放到窗口显示后执行（避免打开时阻塞）
    _win.after(500, _refresh_ocr_status)

    _sec("浏览器")
    browser_headless_var = tkinter.BooleanVar(value=state.BROWSER_HEADLESS)
    _add(row(adv_content, ctx, "", lambda p: ttk.Checkbutton(
        p, text="浏览器无头模式", variable=browser_headless_var)))
    browser_slowmo_var = tkinter.StringVar(value=str(state.BROWSER_SLOW_MO))
    _add(row(adv_content, ctx, "慢放", lambda p: _spin(
        p, browser_slowmo_var, 0, 5000, 50, 6, "浏览器慢放", 0, 0, 5000),
        unit="ms", help="0=最快"))
    # 通道 / 持久化上下文: 复用系统已装 Chrome/Edge (免下 150MB 内核), 并保留登录态
    browser_channel_var = tkinter.StringVar(
        value=_CHANNEL_LABEL_OF.get(getattr(state, "BROWSER_CHANNEL", ""), "内置 Chromium"))
    _add(row(adv_content, ctx, "类型", lambda p: ttk.Combobox(
        p, textvariable=browser_channel_var, values=tuple(_CHANNEL_LABEL_OF.values()),
        state="readonly", width=13),
        help="Chrome/Edge 复用系统浏览器, 免装内核"))
    browser_persistent_var = tkinter.BooleanVar(value=getattr(state, "BROWSER_PERSISTENT", False))
    _add(row(adv_content, ctx, "持久化", lambda p: ttk.Checkbutton(
        p, text="复用浏览器 profile", variable=browser_persistent_var),
        help="开启后每次运行复用同一 profile, 保留登录态"))
    browser_profile_var = tkinter.StringVar(value=getattr(state, "BROWSER_USER_DATA_DIR", ""))

    def _mk_profile(p):
        f = tkinter.Frame(p, bg=C["bgc"])
        tkinter.Entry(f, textvariable=browser_profile_var, width=18,
            font=ctx.FONT_SMALL, bg=C["ebg"], fg=C["fgb"],
            relief="solid", bd=1).pack(side="left", padx=(0, 4))
        _btn(f, "浏览", lambda: browser_profile_var.set(filedialog.askdirectory(
            title="选择浏览器用户数据目录") or browser_profile_var.get()),
            C["ac"], "white", tip="浏览器 profile 目录").pack(side="left")
        return f

    _add(row(adv_content, ctx, "Profile", _mk_profile,
             help="留空=配置目录/browser_profile"))

    _sec("调度与驱动")
    sched_poll_var = tkinter.StringVar(value=str(state.SCHED_POLL_INTERVAL))
    _add(row(adv_content, ctx, "调度轮询", lambda p: _spin(
        p, sched_poll_var, 5, 600, 5, 5, "调度轮询间隔", 30, 5, 600),
        unit="秒", help="定时任务检查间隔"))

    dd_dll_path_var = tkinter.StringVar(value=state.DD_DLL_PATH)

    def _mk_dd(p):
        f = tkinter.Frame(p, bg=C["bgc"])
        tkinter.Entry(f, textvariable=dd_dll_path_var, width=20,
            font=ctx.FONT_SMALL, bg=C["ebg"], fg=C["fgb"],
            relief="solid", bd=1).pack(side="left", padx=(0, 4))
        _btn(f, "浏览", lambda: dd_dll_path_var.set(filedialog.askopenfilename(
            title="选择 DD 驱动 DLL", filetypes=[("DLL", "*.dll"), ("All", "*.*")],
            initialdir=APP_ROOT) or dd_dll_path_var.get()),
            C["ac"], "white", tip="选择 DD 驱动 DLL 文件").pack(side="left")
        return f

    _add(row(adv_content, ctx, "DD DLL 路径", _mk_dd, help="DD 驱动 DLL 文件路径"))

    _add(help_line(adv_content, ctx,
        "提示：以上设置大多在下次识别/启动时生效；卡片角标显示「✔ 已保存」表示已落盘，"
        "若显示「⚠ 保存失败」可点击查看原因并重试"))

    # 绑定防抖保存 (高级设置卡)
    _track_card_vars("advanced", (max_minutes_var, bound_window_var, stop_on_error_var,
        ocr_backend_var, ocr_paddle_var, ocr_preload_var, ocr_threads_var,
        browser_headless_var, browser_slowmo_var, browser_channel_var,
        browser_persistent_var, browser_profile_var, sched_poll_var, dd_dll_path_var,
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
        "browser_channel_var": browser_channel_var,
        "browser_persistent_var": browser_persistent_var,
        "browser_profile_var": browser_profile_var,
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
    state.BROWSER_CHANNEL = _CHANNEL_VALUE_OF.get(
        handles["browser_channel_var"].get(), "")
    state.BROWSER_PERSISTENT = bool(handles["browser_persistent_var"].get())
    state.BROWSER_USER_DATA_DIR = handles["browser_profile_var"].get().strip()
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
