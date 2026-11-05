# -*- coding: utf-8 -*-
"""高级设置分节：执行、OCR、浏览器与调度。

``build(parent, ctx)`` 仍构建完整高级设置卡；九页窗口通过 ``section=``
分别构建各分节，并在宿主中合并 handles，最终仍由同一个 ``apply`` 保存。
"""
import threading
import tkinter
from tkinter import ttk, filedialog, messagebox

import state
from utils import FONT_TINY

from ui.settings.cards._layout import section, row, switch_row, help_line
from ui.widgets.rounded_button import RoundedSwitch

CARD_KEY = "advanced"
ROW = 7
_SECTION_TITLES = {
    "execution": "运行保护",
    "ocr": "OCR 识别",
    "browser": "浏览器与输入驱动",
    "schedule": "调度高级选项",
}
_OCR_BACKEND_MAP = {"自动": "auto", "PaddleOCR": "paddle",
                    "PaddleOCR.dll": "paddle_dll",
                    "Windows OCR": "winrt", "Tesseract": "tesseract"}
_CHANNEL_LABEL_OF = {"": "内置 Chromium", "chrome": "Chrome", "edge": "Edge",
                     "firefox": "Firefox", "webkit": "WebKit"}
_CHANNEL_VALUE_OF = {v: k for k, v in _CHANNEL_LABEL_OF.items()}


def _int_or(var, fallback, lo=None, hi=None):
    try:
        value = int(str(var.get()).strip())
    except (TypeError, ValueError):
        return fallback
    if lo is not None and value < lo:
        return fallback
    if hi is not None and value > hi:
        return fallback
    return value


class _Builder:
    def __init__(self, card, content, ctx):
        self.card = card
        self.content = content
        self.ctx = ctx
        self.C = ctx.C
        self.handles = {"card": card, "content": content}
        self.vars = []
        self._row = 0

    def add(self, widget, pady=None):
        widget.grid(row=self._row, column=0, sticky="ew",
                    pady=((0, self.ctx.sp("row_gap")) if pady is None else pady))
        self._row += 1
        return widget

    def sec(self, name, first=False):
        return self.add(section(self.content, self.ctx, name),
                        pady=(self.ctx.sp("sp_sm") if first else self.ctx.sp("section_gap"), 0))

    def var(self, cls, value):
        variable = cls(value=value)
        self.vars.append(variable)
        return variable

    def spin(self, parent, var, frm, to, inc, width, key, fallback, lo, hi):
        return self.ctx.validate_number(
            tkinter.Spinbox(parent, textvariable=var, from_=frm, to=to,
                            increment=inc, width=width, font=self.ctx.FONT_SMALL,
                            bg=self.C["ebg"], fg=self.C["fgb"], relief="solid", bd=1),
            var, key, fallback, int, lo, hi, card_key=CARD_KEY)

    def entry(self, parent, var, width, show=None, tiny=False):
        return tkinter.Entry(parent, textvariable=var, width=width, show=show,
                             font=(FONT_TINY if tiny else self.ctx.FONT_SMALL),
                             bg=self.C["ebg"], fg=self.C["fgb"], relief="solid", bd=1)

    def execution(self):
        ctx = self.ctx
        self.sec("执行与绑定", first=True)
        max_minutes = self.var(tkinter.StringVar, str(state.MAX_EXECUTION_MINUTES))
        self.add(row(self.content, ctx, "最大执行", lambda p: self.spin(
            p, max_minutes, 0, 1440, 1, 5, "最大执行时间", 0, 0, 1440),
            unit="分钟", help="0=不限，到达时限后自动停止"))
        self.handles["max_minutes_var"] = max_minutes

        bound_window = self.var(tkinter.StringVar, state.BOUND_WINDOW_TITLE)
        self.add(row(self.content, ctx, "绑定窗口", lambda p: self.entry(
            p, bound_window, 26), help="标题包含即匹配，留空表示不绑定窗口"))
        self.handles["bound_window_var"] = bound_window

        stop_on_error = self.var(tkinter.BooleanVar, state.STOP_ON_ERROR)
        self.add(switch_row(self.content, ctx, "脚本出错时立即停止", stop_on_error,
                            help="关闭后记录错误并继续后续步骤"))
        self.handles["stop_on_error_var"] = stop_on_error

    def ocr(self):
        ctx = self.ctx
        self.sec("OCR 识别", first=True)
        backend_label = {v: k for k, v in _OCR_BACKEND_MAP.items()}
        backend = self.var(tkinter.StringVar, backend_label.get(state.OCR_PREFERRED_BACKEND, "自动"))
        self.add(row(self.content, ctx, "OCR 后端", lambda p: ttk.Combobox(
            p, textvariable=backend, values=tuple(_OCR_BACKEND_MAP),
            state="readonly", width=13),
            help="自动=按可用性探测；PaddleOCR.dll 需在下方启用"))
        self.handles["ocr_backend_var"] = backend

        paddle_dir = self.var(tkinter.StringVar, state.OCR_PADDLE_DIR)

        def make_paddle_dir(parent):
            frame = tkinter.Frame(parent, bg=self.C["bgc"])
            tkinter.Entry(frame, textvariable=paddle_dir, width=22,
                          font=ctx.FONT_SMALL, bg=self.C["ebg"], fg=self.C["fgb"],
                          relief="solid", bd=1).pack(side="left", padx=(0, 4))
            ctx.btn(frame, "浏览…", lambda: paddle_dir.set(filedialog.askdirectory(
                title="选择 PaddleOCR 模型目录", initialdir=ctx.APP_ROOT) or paddle_dir.get()),
                self.C["ac"], "white", tip="选择 PaddleOCR 模型目录").pack(side="left")
            return frame

        self.add(row(self.content, ctx, "Paddle 模型目录", make_paddle_dir,
                     help="pip 版 PaddleOCR 的模型目录"))
        self.handles["ocr_paddle_var"] = paddle_dir
        status = tkinter.StringVar(value="检测中…")

        def unavailable_reason():
            bits = []
            try:
                import capabilities
                if bool(getattr(state, "PADDLE_DLL_ENABLED", False)):
                    reason = (capabilities.probe("ocr.paddle_dll").reason or "").strip()
                    if reason:
                        bits.append("PaddleOCR.dll: {}".format(reason))
                else:
                    bits.append("PaddleOCR.dll 原生后端未启用")
            except Exception:
                pass
            if not bits:
                bits.append("pip 版 PaddleOCR / Windows OCR / Tesseract 均未就绪")
            bits.append("可启用 PaddleOCR.dll 并运行「DLL 自检」，或安装 paddlepaddle 与 paddleocr")
            return "；".join(bits)

        def refresh_status():
            status.set("检测中…")

            def probe():
                try:
                    from ocr_backend import ocr_get_backend_info
                    info = ocr_get_backend_info() or {}
                    preferred = backend_label.get(
                        getattr(state, "OCR_PREFERRED_BACKEND", "auto"), "自动")
                    if info.get("available"):
                        text = "已就绪: {}（偏好: {}）".format(
                            info.get("name") or info.get("backend") or "未知后端", preferred)
                    else:
                        text = "无可用后端（偏好: {}）—— {}".format(preferred, unavailable_reason())
                except Exception as exc:
                    text = "状态检测失败: {}".format(exc)
                try:
                    ctx.later(0, lambda: status.set(text))
                except Exception:
                    pass

            try:
                threading.Thread(target=probe, daemon=True).start()
            except Exception:
                probe()

        def reload_ocr():
            try:
                from ocr_backend import ocr_reset_backend
                ocr_reset_backend()
                ctx.log1("OCR 后端已重置（下次识别按新设置重新探测）")
                ctx.show_toast(ctx.root, "OCR 后端已重置", "success")
            except Exception as exc:
                ctx.log1("OCR 后端重置失败: {}".format(exc), "error")
                ctx.show_toast(ctx.root, "OCR 后端重置失败: {}".format(exc), "error")
            refresh_status()

        def make_status(parent):
            frame = tkinter.Frame(parent, bg=self.C["bgc"])
            tkinter.Label(frame, textvariable=status, font=ctx.FONT_SMALL,
                          fg=self.C["sc"], bg=self.C["bgc"], wraplength=330,
                          justify="left").pack(side="left", padx=(0, 8))
            ctx.btn(frame, "刷新状态", refresh_status, "ac", "white",
                    tip="重新检测当前可用的 OCR 后端").pack(side="left", padx=(0, 6))
            ctx.btn(frame, "应用并重载", reload_ocr, "ac", "white",
                    tip="清空已缓存的 OCR 引擎，下次识别按新设置重新加载").pack(side="left")
            return frame

        self.add(row(self.content, ctx, "OCR 状态", make_status))
        preload = self.var(tkinter.BooleanVar, getattr(state, "OCR_PRELOAD", False))
        self.add(switch_row(self.content, ctx, "启动时预热 OCR", preload,
                            help="提前加载识别引擎，降低首次识别等待时间"))
        self.handles["ocr_preload_var"] = preload
        threads = self.var(tkinter.StringVar, str(getattr(state, "OCR_THREADS", 8)))
        self.add(row(self.content, ctx, "CPU 线程", lambda p: self.spin(
            p, threads, 1, 32, 1, 4, "OCR 线程数", 8, 1, 32),
            unit="1-32", help="pip 版与 DLL 后端共用"))
        self.handles["ocr_threads_var"] = threads

        self.sec("PaddleOCR.dll（原生后端）")
        dll_enabled = self.var(tkinter.BooleanVar, bool(getattr(state, "PADDLE_DLL_ENABLED", False)))
        dll_status = tkinter.StringVar(value="未自检")

        def format_report(report):
            verdict = {"ready": "就绪（可在上方启用）",
                       "missing": "未安装或未放到正确目录",
                       "broken": "文件存在但不可用，请补齐缺失项"}.get(
                           report.get("state"), str(report.get("state")))
            kind = {"dll": "主模块", "dep": "原生依赖", "model": "模型文件", "legacy": "旧版兼容"}
            lines = ["结论: {}".format(verdict),
                     "DLL 目录: {}".format(report.get("dll_dir") or "未找到"),
                     "模型目录: {}".format(report.get("model_dir") or "未找到/未配置"), ""]
            for item in report.get("items") or []:
                lines.append("{} {:<24} ({})".format(
                    "✔" if item.get("ok") else "✘", item.get("name"),
                    kind.get(item.get("kind"), item.get("kind"))))
                if not item.get("ok") and item.get("path"):
                    lines.append("     缺: {}".format(item.get("path")))
            lines.extend(("", report.get("reason") or ""))
            return "\n".join(lines)

        def check_dll():
            try:
                import paddle_dll
                report = paddle_dll.self_check_report()
            except Exception as exc:
                dll_status.set("自检失败: {}".format(exc))
                return
            report_state = report.get("state")
            missing = list(report.get("missing") or [])
            if report_state == "ready":
                dll_status.set("就绪：依赖、模型与路径检查均通过")
            elif report_state == "missing":
                dll_status.set("未找到 {}，请查看自检清单".format(
                    getattr(paddle_dll, "DLL_NAME", "PaddleOCR.dll")))
            elif missing:
                dll_status.set("缺少 {} 项: {}".format(len(missing), ", ".join(missing)))
            else:
                dll_status.set("不可用: {}".format((report.get("reason") or "")[:70]))
            try:
                from ocr_backend import ocr_reset_backend
                ocr_reset_backend()
            except Exception:
                pass
            refresh_status()
            text = format_report(report)
            ctx.log1("PaddleOCR.dll 自检: {}".format(report.get("reason") or report_state))
            ctx.log1(text)
            try:
                messagebox.showinfo("PaddleOCR.dll 自检", text, parent=ctx.win)
            except Exception:
                pass

        def make_dll_enable(parent):
            frame = tkinter.Frame(parent, bg=self.C["bgc"])
            tkinter.Label(frame, text="启用 PaddleOCR.dll", font=ctx.FONT_SMALL,
                          fg=self.C["fgb"], bg=self.C["bgc"]).pack(side="left", padx=(0, 8))
            RoundedSwitch(frame, variable=dll_enabled, colors=self.C).pack(side="left", padx=(0, 8))
            ctx.btn(frame, "DLL 自检", check_dll, "ac", "white",
                    tip="静态检查 DLL、依赖、模型文件与路径编码").pack(side="left", padx=(0, 8))
            tkinter.Label(frame, textvariable=dll_status, font=FONT_TINY,
                          fg=self.C["fgm"], bg=self.C["bgc"], wraplength=320,
                          justify="left").pack(side="left")
            return frame

        self.add(row(self.content, ctx, "", make_dll_enable))
        self.handles["paddle_dll_enabled_var"] = dll_enabled
        dll_dir = self.var(tkinter.StringVar, getattr(state, "PADDLE_DLL_DIR", ""))

        def make_dll_dir(parent):
            frame = tkinter.Frame(parent, bg=self.C["bgc"])
            tkinter.Entry(frame, textvariable=dll_dir, width=18, font=ctx.FONT_SMALL,
                          bg=self.C["ebg"], fg=self.C["fgb"], relief="solid", bd=1).pack(
                side="left", padx=(0, 4))
            ctx.btn(frame, "浏览…", lambda: dll_dir.set(filedialog.askdirectory(
                title="选择含 PaddleOCR.dll 的目录", initialdir=ctx.APP_ROOT) or dll_dir.get()),
                self.C["ac"], "white", tip="空=lib/paddle_ocr").pack(side="left")
            return frame

        self.add(row(self.content, ctx, "DLL 目录", make_dll_dir,
                     help="包含主 DLL 与依赖的纯英文路径（空=lib/paddle_ocr）"))
        self.handles["paddle_dll_dir_var"] = dll_dir
        model_dir = self.var(tkinter.StringVar, getattr(state, "PADDLE_DLL_MODEL_DIR", ""))

        def make_model_dir(parent):
            frame = tkinter.Frame(parent, bg=self.C["bgc"])
            tkinter.Entry(frame, textvariable=model_dir, width=18, font=ctx.FONT_SMALL,
                          bg=self.C["ebg"], fg=self.C["fgb"], relief="solid", bd=1).pack(
                side="left", padx=(0, 4))
            ctx.btn(frame, "浏览…", lambda: model_dir.set(filedialog.askdirectory(
                title="选择模型目录 (含 inference.json + inference.pdiparams)",
                initialdir=dll_dir.get() or ctx.APP_ROOT) or model_dir.get()),
                self.C["ac"], "white", tip="空=在 DLL 目录下自动查找").pack(side="left")
            return frame

        self.add(row(self.content, ctx, "模型目录", make_model_dir,
                     help="模型集合目录（如 inference/），留空则自动查找"))
        self.handles["paddle_dll_model_dir_var"] = model_dir
        config = self.var(tkinter.StringVar, getattr(state, "PADDLE_DLL_CONFIG", ""))
        self.add(row(self.content, ctx, "推理参数", lambda p: self.entry(
            p, config, 20, tiny=True), help="JSON（留空使用内置参数）"))
        self.handles["paddle_dll_config_var"] = config
        license_var = self.var(tkinter.StringVar, getattr(state, "PADDLE_DLL_LICENSE", ""))
        self.add(row(self.content, ctx, "授权串", lambda p: self.entry(
            p, license_var, 10, show="*", tiny=True)))
        self.handles["paddle_dll_license_var"] = license_var
        proto_init = self.var(tkinter.StringVar, getattr(state, "PADDLE_DLL_PROTO_INIT", "json5"))
        proto_detect = self.var(tkinter.StringVar, getattr(state, "PADDLE_DLL_PROTO_DETECT", "ptr_byte"))

        def make_proto(parent):
            frame = tkinter.Frame(parent, bg=self.C["bgc"])
            tkinter.Entry(frame, textvariable=proto_init, width=8, font=FONT_TINY,
                          bg=self.C["ebg"], fg=self.C["fgb"], relief="solid", bd=1).pack(
                side="left", padx=(0, 2))
            tkinter.Label(frame, text="/", font=FONT_TINY, fg=self.C["fgm"],
                          bg=self.C["bgc"]).pack(side="left")
            tkinter.Entry(frame, textvariable=proto_detect, width=9, font=FONT_TINY,
                          bg=self.C["ebg"], fg=self.C["fgb"], relief="solid", bd=1).pack(side="left")
            return frame

        self.add(row(self.content, ctx, "调用原型", make_proto, help="init / detect 调用原型"))
        self.handles["paddle_dll_proto_init_var"] = proto_init
        self.handles["paddle_dll_proto_detect_var"] = proto_detect
        probe_after = ctx.later(500, refresh_status)

        def cancel_probe(event):
            if event.widget is self.card:
                try:
                    ctx.win.after_cancel(probe_after)
                except Exception:
                    pass
        self.card.bind("<Destroy>", cancel_probe)

    def browser(self):
        ctx = self.ctx
        self.sec("浏览器", first=True)
        headless = self.var(tkinter.BooleanVar, state.BROWSER_HEADLESS)
        self.add(switch_row(self.content, ctx, "后台运行浏览器（无头模式）", headless,
                            help="不显示浏览器窗口；调试登录或页面操作时可关闭"))
        self.handles["browser_headless_var"] = headless
        slowmo = self.var(tkinter.StringVar, str(state.BROWSER_SLOW_MO))
        self.add(row(self.content, ctx, "慢放", lambda p: self.spin(
            p, slowmo, 0, 5000, 50, 6, "浏览器慢放", 0, 0, 5000),
            unit="ms", help="0=最快"))
        self.handles["browser_slowmo_var"] = slowmo
        channel = self.var(tkinter.StringVar, _CHANNEL_LABEL_OF.get(
            getattr(state, "BROWSER_CHANNEL", ""), "内置 Chromium"))
        self.add(row(self.content, ctx, "类型", lambda p: ttk.Combobox(
            p, textvariable=channel, values=tuple(_CHANNEL_LABEL_OF.values()),
            state="readonly", width=13), help="Chrome/Edge 复用系统浏览器，免装内核"))
        self.handles["browser_channel_var"] = channel
        persistent = self.var(tkinter.BooleanVar, getattr(state, "BROWSER_PERSISTENT", False))
        self.add(switch_row(self.content, ctx, "保留浏览器登录状态", persistent,
                            help="复用同一浏览器 Profile，减少重复登录"))
        self.handles["browser_persistent_var"] = persistent
        profile = self.var(tkinter.StringVar, getattr(state, "BROWSER_USER_DATA_DIR", ""))

        def make_profile(parent):
            frame = tkinter.Frame(parent, bg=self.C["bgc"])
            tkinter.Entry(frame, textvariable=profile, width=18, font=ctx.FONT_SMALL,
                          bg=self.C["ebg"], fg=self.C["fgb"], relief="solid", bd=1).pack(
                side="left", padx=(0, 4))
            ctx.btn(frame, "浏览…", lambda: profile.set(filedialog.askdirectory(
                title="选择浏览器用户数据目录") or profile.get()),
                self.C["ac"], "white", tip="浏览器 profile 目录").pack(side="left")
            return frame

        self.add(row(self.content, ctx, "Profile", make_profile,
                   help="留空=配置目录/browser_profile"))
        self.handles["browser_profile_var"] = profile

        self.sec("DD 输入驱动")
        dll_path = self.var(tkinter.StringVar, state.DD_DLL_PATH)

        def make_dd(parent):
            frame = tkinter.Frame(parent, bg=self.C["bgc"])
            tkinter.Entry(frame, textvariable=dll_path, width=20, font=ctx.FONT_SMALL,
                          bg=self.C["ebg"], fg=self.C["fgb"], relief="solid", bd=1).pack(
                side="left", padx=(0, 4))
            ctx.btn(frame, "浏览…", lambda: dll_path.set(filedialog.askopenfilename(
                title="选择 DD 驱动 DLL", filetypes=[("DLL", "*.dll"), ("All", "*.*")],
                initialdir=ctx.APP_ROOT) or dll_path.get()),
                self.C["ac"], "white", tip="选择 DD 驱动 DLL 文件").pack(side="left")
            return frame

        self.add(row(self.content, ctx, "DD DLL 路径", make_dd,
                   help="仅在选择驱动键鼠输入模式时使用"))
        self.handles["dd_dll_path_var"] = dll_path

    def schedule(self):
        self.sec("调度轮询", first=True)
        poll = self.var(tkinter.StringVar, str(state.SCHED_POLL_INTERVAL))
        self.add(row(self.content, self.ctx, "轮询间隔", lambda p: self.spin(
            p, poll, 5, 600, 5, 5, "调度轮询间隔", 30, 5, 600),
            unit="秒", help="定时任务检查间隔；修改后自动保存"))
        self.handles["sched_poll_var"] = poll


def build(parent, ctx, section=None):
    if section is not None and section not in _SECTION_TITLES:
        raise ValueError("未知高级设置分节: {}".format(section))
    sections = (section,) if section else ("execution", "ocr", "browser", "schedule")
    title = _SECTION_TITLES.get(section, "高级设置")
    card, content, title_row, _ = ctx.make_collapsible_card(parent, title, "⚒")
    card.grid(row=ROW, column=0, sticky="ew", **ctx.PAD)
    ctx.make_badge(CARD_KEY, title_row)
    ctx.register_nav_card(CARD_KEY, card)
    builder = _Builder(card, content, ctx)
    builder.handles["title"] = title_row
    for name in sections:
        getattr(builder, name)()
    if section is None:
        builder.add(help_line(content, ctx,
            "以上设置按所属页面分别生效；保存失败时可在底部状态栏查看原因并重试"))
    ctx.track_card_vars(CARD_KEY, tuple(builder.vars))
    return builder.handles


def apply(ctx, handles):
    """保存所有已构建分节；任一项非法不影响其它项。"""
    state.MAX_EXECUTION_MINUTES = _int_or(handles["max_minutes_var"], state.MAX_EXECUTION_MINUTES, 0, 1440)
    state.BOUND_WINDOW_TITLE = handles["bound_window_var"].get().strip()
    state.STOP_ON_ERROR = bool(handles["stop_on_error_var"].get())
    state.OCR_PREFERRED_BACKEND = _OCR_BACKEND_MAP.get(handles["ocr_backend_var"].get(), "auto")
    state.OCR_PADDLE_DIR = handles["ocr_paddle_var"].get().strip()
    state.OCR_PRELOAD = bool(handles["ocr_preload_var"].get())
    state.OCR_THREADS = _int_or(handles["ocr_threads_var"], 8, 1, 32)
    state.BROWSER_HEADLESS = bool(handles["browser_headless_var"].get())
    state.BROWSER_SLOW_MO = _int_or(handles["browser_slowmo_var"], 0, 0, 5000)
    state.BROWSER_CHANNEL = _CHANNEL_VALUE_OF.get(handles["browser_channel_var"].get(), "")
    state.BROWSER_PERSISTENT = bool(handles["browser_persistent_var"].get())
    state.BROWSER_USER_DATA_DIR = handles["browser_profile_var"].get().strip()
    state.SCHED_POLL_INTERVAL = _int_or(handles["sched_poll_var"], 30, 5, 600)
    state.DD_DLL_PATH = handles["dd_dll_path_var"].get().strip()
    state.PADDLE_DLL_ENABLED = bool(handles["paddle_dll_enabled_var"].get())
    state.PADDLE_DLL_DIR = handles["paddle_dll_dir_var"].get().strip()
    state.PADDLE_DLL_MODEL_DIR = handles["paddle_dll_model_dir_var"].get().strip()
    state.PADDLE_DLL_PROTO_INIT = handles["paddle_dll_proto_init_var"].get().strip() or "json5"
    state.PADDLE_DLL_PROTO_DETECT = handles["paddle_dll_proto_detect_var"].get().strip() or "ptr_byte"
    state.PADDLE_DLL_CONFIG = handles["paddle_dll_config_var"].get().strip()
    state.PADDLE_DLL_LICENSE = handles["paddle_dll_license_var"].get().strip()
