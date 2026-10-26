# -*- coding: utf-8 -*-
"""reminder.py — 提醒浮窗（方案文档 §5 L4 #5）。

能力
----
* 位置：屏幕中央 / 右下角（``pos``；中文别名自动归一化）；
* 透明度：``-alpha`` 0.3–1.0（``opacity``，也接受 30–100 的百分数）；
* 自动关闭秒数 + 可选倒计时文案（模板支持 ``{剩余}`` / ``{remain}``，
  并**复用** ``interp`` 的 ``{date:}`` 语法，不引入任何新模板引擎）；
* 打盹**三态**：
    ① 稍后提醒我（5 / 10 / 30 分钟，可配）
    ② 不再提醒（本次）
    ③ 彻底关闭该提醒任务

注入式接口（本模块**不 import** ``ACRPA`` / ``dialogs``，避免循环依赖）
------------------------------------------------------------------
``register_reminder_api(root=..., on_snooze=..., on_dismiss=..., on_close_task=...)``
    宿主（``ACRPA.py``）在启动时登记根窗口与默认回调；命令层/触发器只需调
    ``show_reminder(...)``，不需要知道窗口是怎么建的。

线程模型
--------
全部 UI 操作都在调用方线程（必须主线程）执行；倒计时用 ``widget.after``，
不创建线程。`show_reminder` 在无 Tk 环境（无根窗口 / TclError）时**静默返回
``None``**，绝不抛异常，便于无 GUI 的单测与 CLI 场景。

公开 API
--------
``show_reminder(...)`` / ``close_all()`` / ``register_reminder_api(...)`` /
``normalize_position`` / ``clamp_opacity`` / ``normalize_snooze_minutes`` /
``snooze_options`` / ``format_remaining`` / ``render_countdown`` /
``resolve_autoclose``
"""
import time

import i18n
import state


def _log_tick_error(key, msg, level="error", interval=60.0):
    """倒计时/销毁路径的**限流**日志（同一 key 60 s 一条，避免 1 Hz 刷屏）。"""
    try:
        import crashlog
        crashlog.log_once(key, msg, level, interval)
    except Exception:
        pass

__all__ = [
    "show_reminder", "close_all", "register_reminder_api", "get_host",
    "ReminderPopup",
    "normalize_position", "clamp_opacity", "normalize_snooze_minutes",
    "snooze_options", "format_remaining", "render_countdown", "resolve_autoclose",
    "POSITIONS", "DEFAULT_SNOOZE_MINUTES", "AUTO_CLOSE_RANGE", "OPACITY_RANGE",
]

# ── 纯函数层的常量（单测可直接引用，不需要 GUI） ──
POSITIONS = ("center", "bottom_right")
DEFAULT_SNOOZE_MINUTES = (5, 10, 30)
OPACITY_RANGE = (0.3, 1.0)
AUTO_CLOSE_RANGE = (0, 86400)

_POS_ALIASES = {
    "center": "center", "centre": "center", "middle": "center",
    "屏幕中央": "center", "中央": "center", "中心": "center", "居中": "center",
    "bottom_right": "bottom_right", "bottomright": "bottom_right",
    "right_bottom": "bottom_right", "corner": "bottom_right",
    "右下角": "bottom_right", "右下": "bottom_right",
}

# 已创建的浮窗（缩放切换时需要重刷字体；close_all 时统一销毁）
_OPEN = []
# 宿主注入（根窗口 / 默认回调）
_HOST = {"root": None, "on_snooze": None, "on_dismiss": None, "on_close_task": None}
_scale_hook_done = False


# ══════════════════════════════════════════════════════════════════════
# 纯函数层（无 tkinter，可独立单测）
# ══════════════════════════════════════════════════════════════════════

def normalize_position(value, default="center"):
    """位置文案 → ``"center"`` / ``"bottom_right"``（未知回退 ``default``）。"""
    text = str(value if value is not None else "").strip().lower()
    if not text:
        return default if default in POSITIONS else "center"
    if text in _POS_ALIASES:
        return _POS_ALIASES[text]
    # 允许 "bottom-right" / "bottom right"
    key = text.replace("-", "_").replace(" ", "_")
    if key in _POS_ALIASES:
        return _POS_ALIASES[key]
    return default if default in POSITIONS else "center"


def clamp_opacity(value, default=0.92):
    """透明度归一化到 0.3–1.0。

    ``>1`` 且 ``<=100`` 视为百分数（``92`` → ``0.92``）；``>100`` 直接钳到上限 1.0。
    """
    try:
        num = float(value)
    except (TypeError, ValueError):
        num = float(default)
    if 1.0 < num <= 100.0:
        num = num / 100.0
    low, high = OPACITY_RANGE
    return round(max(low, min(high, num)), 2)


def normalize_snooze_minutes(value, default=5):
    """打盹分钟数 → 1–1440 的整数。"""
    try:
        num = int(float(str(value).strip()))
    except (TypeError, ValueError):
        num = int(default)
    return max(1, min(1440, num))


def snooze_options(extra=None):
    """→ 打盹可选分钟列表（默认 5/10/30，去重升序；``extra`` 可追加一项）。"""
    out = []
    for candidate in list(DEFAULT_SNOOZE_MINUTES) + list(extra or []):
        try:
            minute = normalize_snooze_minutes(candidate)
        except Exception:                   # noqa: BLE001
            continue
        if minute not in out:
            out.append(minute)
    return sorted(out)


def resolve_autoclose(sec):
    """自动关闭秒数 → 0–86400 的整数（0 = 不自动关闭）。"""
    try:
        num = int(float(str(sec).strip()))
    except (TypeError, ValueError):
        num = 0
    low, high = AUTO_CLOSE_RANGE
    return max(low, min(high, num))


def format_remaining(secs):
    """秒数 → 人类可读倒计时（``95`` → ``1分35秒``）。"""
    try:
        total = int(max(0, float(secs)))
    except (TypeError, ValueError):
        total = 0
    if total <= 0:
        return "0秒"
    if total < 60:
        return "{}秒".format(total)
    minutes, rest = divmod(total, 60)
    if not rest:
        return "{}分".format(minutes)
    return "{}分{}秒".format(minutes, rest)


def render_countdown(template, remaining_sec):
    """倒计时文案渲染。

    支持的占位符（**不引入新模板引擎**）：
        ``{剩余}`` / ``{remain}`` / ``{secs}``   → 人类可读剩余时间
        ``{date:...}``                          → 交给 ``interp`` 的既有实现

    模板为空的回退是 ``i18n`` 的 ``reminder.remaining``。
    任何失败回退为纯剩余时间文本。
    """
    remain_text = format_remaining(remaining_sec)
    try:
        tmpl = str(template or "")
    except Exception:                       # noqa: BLE001
        tmpl = ""
    if not tmpl:
        try:
            tmpl = i18n.t("reminder.remaining")
        except Exception:                   # noqa: BLE001
            tmpl = "还剩 {remain}"
    text = tmpl
    for token in ("{剩余}", "{remain}", "{secs}"):
        text = text.replace(token, remain_text)
    # {date:} 由既有 interp 负责；失败保持字面值
    try:
        import interp
        text = interp.interpolate(text)
    except Exception:                       # noqa: BLE001
        pass
    return text


# ══════════════════════════════════════════════════════════════════════
# 宿主注入
# ══════════════════════════════════════════════════════════════════════

def register_reminder_api(root=None, on_snooze=None, on_dismiss=None,
                          on_close_task=None):
    """登记宿主根窗口与默认回调（可重复调用，后者覆盖前者）。

    传 ``None`` 的项**不覆盖**已有值，便于分点登记。
    """
    if root is not None:
        _HOST["root"] = root
    if on_snooze is not None:
        _HOST["on_snooze"] = on_snooze
    if on_dismiss is not None:
        _HOST["on_dismiss"] = on_dismiss
    if on_close_task is not None:
        _HOST["on_close_task"] = on_close_task


def get_host():
    """→ 宿主注入表副本（调试/单测用）。"""
    return dict(_HOST)


def _resolve_root(parent=None):
    """→ 可用的根窗口；没有则 ``None``（无 GUI 环境）。"""
    if parent is not None:
        return parent
    try:
        if _HOST.get("root") is not None:
            return _HOST["root"]
    except Exception:                       # noqa: BLE001
        pass
    try:
        import tkinter
        return getattr(tkinter, "_default_root", None)
    except Exception:                       # noqa: BLE001
        return None


# ══════════════════════════════════════════════════════════════════════
# 浮窗实现
# ══════════════════════════════════════════════════════════════════════

def _palette():
    """→ 主题色表（取 utils.C；失败回退浅色默认值）。"""
    try:
        import utils
        colors = getattr(utils, "C", None)
        if isinstance(colors, dict) and colors.get("bgc"):
            return colors
    except Exception:                       # noqa: BLE001
        pass
    return {"bgc": "#FFFFFF", "fgt": "#1F1F1F", "fgb": "#333333",
            "fgm": "#5F5F5F", "ac": "#0078D4", "bd": "#E1E1E1", "dg": "#C42B1C"}


def _font(name, fallback):
    """→ 字体名（utils 里的命名字体，便于随缩放档位重配）。"""
    try:
        import utils
        value = getattr(utils, name, None)
        if value:
            return value
    except Exception:                       # noqa: BLE001
        pass
    return fallback


class ReminderPopup(object):
    """一个提醒浮窗（Toplevel）。销毁幂等，全部 UI 操作限主线程。"""

    WIDTH = 380

    def __init__(self, parent, title="", body="", pos="center", opacity=0.92,
                 autoclose_sec=0, show_countdown=True, snooze_minutes=None,
                 countdown_template=None, on_snooze=None, on_dismiss=None,
                 on_close_task=None, allow_snooze=True, task_id=None):
        import tkinter
        from tkinter import ttk  # noqa: F401  (保持与其他 UI 模块一致的依赖面)

        self.closed = False
        self._after_id = None
        self._deadline = None
        self._remaining = 0
        self.auto_closed = False
        self.close_reason = ""

        self.pos = normalize_position(pos)
        self.opacity = clamp_opacity(opacity)
        self.autoclose_sec = resolve_autoclose(autoclose_sec)
        self.show_countdown = bool(show_countdown)
        self.snooze_minutes = snooze_options([snooze_minutes] if snooze_minutes else None)
        self.countdown_template = countdown_template
        self.on_snooze = on_snooze
        self.on_dismiss = on_dismiss
        self.on_close_task = on_close_task
        self.task_id = task_id

        colors = _palette()
        self._colors = colors

        win = tkinter.Toplevel(parent)
        self.win = win
        win.title(str(title or i18n.t("reminder.default_title")))
        win.configure(bg=colors["bgc"])
        win.resizable(False, False)
        try:
            win.attributes("-topmost", True)
        except Exception:                   # noqa: BLE001
            pass
        try:
            win.attributes("-alpha", self.opacity)
        except Exception:                   # noqa: BLE001
            pass

        box = tkinter.Frame(win, bg=colors["bgc"], padx=14, pady=12)
        box.pack(fill="both", expand=True)

        self.title_label = tkinter.Label(
            box, text=str(title or i18n.t("reminder.default_title")),
            font=_font("FONT_TITLE", "Microsoft YaHei UI 12 bold"),
            bg=colors["bgc"], fg=colors["fgt"], anchor="w", justify="left")
        self.title_label.pack(fill="x")

        self.body_label = tkinter.Label(
            box, text=str(body or ""), font=_font("FONT_BODY", "Microsoft YaHei UI 9"),
            bg=colors["bgc"], fg=colors["fgb"], anchor="w", justify="left",
            wraplength=self.WIDTH - 40)
        self.body_label.pack(fill="x", pady=(6, 4))

        self.countdown_label = tkinter.Label(
            box, text="", font=_font("FONT_SMALL", "Microsoft YaHei UI 8"),
            bg=colors["bgc"], fg=colors["fgm"], anchor="w")
        if self.autoclose_sec > 0 and self.show_countdown:
            self.countdown_label.pack(fill="x")

        bar = tkinter.Frame(box, bg=colors["bgc"])
        bar.pack(fill="x", pady=(8, 0))

        self.buttons = {}
        if allow_snooze:
            for minute in self.snooze_minutes:
                text = i18n.t("reminder.snooze", min=minute)
                btn = tkinter.Button(
                    bar, text=text, font=_font("FONT_SMALL", "Microsoft YaHei UI 8"),
                    bg=colors["bgc"], fg=colors["fgb"], relief="solid", bd=1,
                    padx=8, pady=2, cursor="hand2",
                    command=lambda m=minute: self._do_snooze(m))
                btn.pack(side="left", padx=(0, 4))
                self.buttons["snooze_{}".format(minute)] = btn

        btn_once = tkinter.Button(
            bar, text=i18n.t("reminder.dismiss_once"),
            font=_font("FONT_SMALL", "Microsoft YaHei UI 8"),
            bg=colors["bgc"], fg=colors["fgb"], relief="solid", bd=1,
            padx=8, pady=2, cursor="hand2", command=self._do_dismiss_once)
        btn_once.pack(side="left", padx=(0, 4))
        self.buttons["dismiss_once"] = btn_once

        btn_stop = tkinter.Button(
            bar, text=i18n.t("reminder.close"),
            font=_font("FONT_SMALL", "Microsoft YaHei UI 8"),
            bg=colors["ac"], fg="white", relief="flat", bd=1,
            padx=10, pady=2, cursor="hand2", command=self._do_stop_task)
        btn_stop.pack(side="right")
        self.buttons["stop_task"] = btn_stop

        win.protocol("WM_DELETE_WINDOW", self._do_dismiss_once)
        try:
            win.bind("<Escape>", lambda e: self._do_dismiss_once())
        except Exception:                   # noqa: BLE001
            pass

        self._place(parent)
        _OPEN.append(self)

        if self.autoclose_sec > 0:
            self._deadline = time.time() + self.autoclose_sec
            self._tick()

    # ── 几何 ──────────────────────────────────────────────────
    def _place(self, parent):
        """按 pos 落位（center 居中于父窗/屏幕，bottom_right 贴工作区右下角）。"""
        win = self.win
        try:
            win.update_idletasks()
            width = max(self.WIDTH, win.winfo_reqwidth())
            height = max(80, win.winfo_reqheight())
            if self.pos == "bottom_right":
                screen_w = win.winfo_screenwidth()
                screen_h = win.winfo_screenheight()
                x = max(0, screen_w - width - 24)
                y = max(0, screen_h - height - 64)
            else:
                try:
                    px = parent.winfo_rootx()
                    py = parent.winfo_rooty()
                    pw = parent.winfo_width()
                    ph = parent.winfo_height()
                except Exception:           # noqa: BLE001
                    px = py = 0
                    pw = win.winfo_screenwidth()
                    ph = win.winfo_screenheight()
                x = px + max(0, (pw - width) // 2)
                y = py + max(0, (ph - height) // 2)
            win.geometry("{}x{}+{}+{}".format(width, height, int(x), int(y)))
        except Exception:                   # noqa: BLE001
            pass

    # ── 倒计时 ────────────────────────────────────────────────
    def _tick(self):
        if self.closed:
            return
        remain = int(round(self._deadline - time.time())) if self._deadline else 0
        self._remaining = max(0, remain)
        try:
            if self.show_countdown:
                self.countdown_label.configure(
                    text=render_countdown(self.countdown_template, self._remaining))
        except Exception as e:              # noqa: BLE001
            _log_tick_error("reminder.tick.label",
                            "提醒浮窗倒计时刷新失败: {}".format(e))
        if self._remaining <= 0:
            self.auto_closed = True
            self._close("timeout")
            return
        try:
            self._after_id = self.win.after(1000, self._tick)
        except Exception as e:              # noqa: BLE001
            _log_tick_error("reminder.tick.after",
                            "提醒浮窗重新排程失败: {}".format(e))
            self._after_id = None

    def remaining(self):
        """→ 当前剩余秒数（未启用自动关闭时恒为 0）。"""
        if not self._deadline:
            return 0
        return max(0, int(round(self._deadline - time.time())))

    # ── 三态回调 ──────────────────────────────────────────────
    def _do_snooze(self, minutes):
        cb = self.on_snooze or _HOST.get("on_snooze")
        minute = normalize_snooze_minutes(minutes)
        self._close("snooze")
        if callable(cb):
            try:
                cb(minute)
            except Exception:               # noqa: BLE001
                pass

    def _do_dismiss_once(self):
        cb = self.on_dismiss or _HOST.get("on_dismiss")
        self._close("dismiss")
        if callable(cb):
            try:
                cb()
            except Exception:               # noqa: BLE001
                pass

    def _do_stop_task(self):
        cb = self.on_close_task or _HOST.get("on_close_task")
        self._close("stop_task")
        if callable(cb):
            try:
                cb(self.task_id) if self.task_id is not None else cb()
            except TypeError:
                try:
                    cb()
                except Exception:           # noqa: BLE001
                    pass
            except Exception:               # noqa: BLE001
                pass

    # ── 缩放 / 销毁 ───────────────────────────────────────────
    def apply_scale(self):
        """UI 缩放切换后重刷字体（不重建窗口）。"""
        if self.closed:
            return
        try:
            self.title_label.configure(font=_font("FONT_TITLE", "Microsoft YaHei UI 12 bold"))
            self.body_label.configure(font=_font("FONT_BODY", "Microsoft YaHei UI 9"),
                                      wraplength=self.WIDTH - 40)
            self.countdown_label.configure(font=_font("FONT_SMALL", "Microsoft YaHei UI 8"))
            for btn in self.buttons.values():
                btn.configure(font=_font("FONT_SMALL", "Microsoft YaHei UI 8"))
        except Exception:                   # noqa: BLE001
            pass

    def _close(self, reason=""):
        if self.closed:
            return
        self.closed = True
        self.close_reason = reason
        if self._after_id is not None:
            try:
                self.win.after_cancel(self._after_id)
            except Exception:               # noqa: BLE001
                pass
            self._after_id = None
        try:
            self.win.destroy()
        except Exception:                   # noqa: BLE001
            pass
        try:
            if self in _OPEN:
                _OPEN.remove(self)
        except Exception:                   # noqa: BLE001
            pass

    def destroy(self):
        """外部关闭（同 ``_close``，幂等）。"""
        self._close("destroy")


# ══════════════════════════════════════════════════════════════════════
# 模块级 API
# ══════════════════════════════════════════════════════════════════════

def _ensure_scale_hook():
    """惰性登记 UI 缩放回调（一次即可；失败忽略）。"""
    global _scale_hook_done
    if _scale_hook_done:
        return
    _scale_hook_done = True
    try:
        import utils
        utils.register_ui_scale_hook(_reapply_scale)
    except Exception:                       # noqa: BLE001
        pass


def _reapply_scale():
    """缩放切换后重刷所有打开中的浮窗。"""
    for popup in list(_OPEN):
        try:
            popup.apply_scale()
        except Exception:                   # noqa: BLE001
            pass


def show_reminder(title=None, body="", parent=None, pos=None, opacity=None,
                  autoclose_sec=None, show_countdown=True, snooze_minutes=None,
                  countdown_template=None, on_snooze=None, on_dismiss=None,
                  on_close_task=None, allow_snooze=True, task_id=None):
    """弹出一个提醒浮窗 → ``ReminderPopup``；无 GUI 环境返回 ``None``。

    参数全部可省略：省略项取 ``state`` 里的持久化默认值
    （``reminder_pos`` / ``reminder_opacity`` / ``reminder_autoclose_sec`` /
    ``reminder_snooze_min``）。

    打盹三态回调：``on_snooze(minutes)`` / ``on_dismiss()`` /
    ``on_close_task(task_id)``（不接受参数时自动降级为 ``on_close_task()``）。
    """
    root = _resolve_root(parent)
    if root is None:
        return None
    _ensure_scale_hook()
    try:
        if pos is None:
            pos = getattr(state, "REMINDER_POS", "center")
        if opacity is None:
            opacity = getattr(state, "REMINDER_OPACITY", 92)
        if autoclose_sec is None:
            autoclose_sec = getattr(state, "REMINDER_AUTOCLOSE_SEC", 0)
        if snooze_minutes is None:
            snooze_minutes = getattr(state, "REMINDER_SNOOZE_MIN", 5)
        popup = ReminderPopup(
            root, title=title or i18n.t("reminder.default_title"), body=body,
            pos=pos, opacity=opacity, autoclose_sec=autoclose_sec,
            show_countdown=show_countdown, snooze_minutes=snooze_minutes,
            countdown_template=countdown_template, on_snooze=on_snooze,
            on_dismiss=on_dismiss, on_close_task=on_close_task,
            allow_snooze=allow_snooze, task_id=task_id)
        return popup
    except Exception:                       # noqa: BLE001 —— 无 GUI/TclError 一律降级
        return None


def close_all():
    """销毁全部提醒浮窗（退出 / 切主题 / 进程收尾时调用）。

    L5: run.py 的 cleanup_and_exit 会显式调用一次，确保退出路径不残留置顶窗口。
    """
    for popup in list(_OPEN):
        try:
            popup.destroy()
        except Exception as e:              # noqa: BLE001
            _log_tick_error("reminder.close_all",
                            "提醒浮窗销毁失败: {}".format(e))
    del _OPEN[:]


def open_count():
    """→ 当前打开的浮窗数量（单测用）。"""
    return len(_OPEN)
