# -*- coding: utf-8 -*-
"""ui.settings.window — 九页设置窗口、搜索、保存与主题协调。"""
import gc
import re
import tkinter
from tkinter import ttk, filedialog, messagebox

import state
import utils
from utils import (FONT_BODY, FONT_SMALL, FONT_TINY, FONT_TITLE,
                   FONT_SMALL_BOLD, ctrl_h, sp)
from ui import theme as _ui_theme
from ui.widgets.rounded_button import RoundedButton
from ui.settings.navigation import (SettingsNavigation, PAGE_ORDER, PAGE_META,
                                    CARD_TO_PAGE)

_APP_ROOT = ""
_MAIN_RUN = None
_tlog = None
_root = None
C = {}
FONT_BODY = ("Segoe UI", 10)
FONT_SMALL = ("Segoe UI", 9)
FONT_TINY = ("Segoe UI", 8)
FONT_TITLE = ("Segoe UI", 11)
FONT_SMALL_BOLD = ("Segoe UI", 9, "bold")
PAD = {"padx": 5, "pady": 3}
_win = None
_dark = False
_prev_colors = {}
_inner = None
_canvas = None
_navigation = None
_page_hosts = {}
_page_scroll = {}
_active_page = "general"
_header_title = None
_header_desc = None
_save_status_var = None
_save_state = {}
_nav_cards = []
_saved_badges = {}
_card_badges = {}
_debounce_after = {}
_apply_map = {}
_handles_map = {}
_last_error = {}
_field_errors = {}
_restart_var = None
_restart_count = 0
_sched_next_label = None
_ensure_tray = None
_destroy_tray = None
_toggle_fold = None
_highlight_after = None
_after_jobs = set()
_search_records = []
_CARD_KEYS = ("exec", "ai", "sched", "record", "log", "system", "quick",
              "advanced", "netlink", "python", "market", "extensions")
_PAGE_BUILD = {
    "general": (("system", None),),
    "execution": (("exec", None), ("advanced", "execution")),
    "recording": (("record", None), ("log", None)),
    "schedule": (("sched", None), ("advanced", "schedule")),
    "ai_ocr": (("ai", None), ("advanced", "ocr")),
    "browser": (("advanced", "browser"),),
    "netlink": (("netlink", None),),
    "extensions": (("python", None), ("market", None), ("extensions", None)),
    "about": (("quick", None),),
}


def _bold_font(font):
    """字体安全加粗: 命名字体字符串 → utils.FONT_SMALL_BOLD; 元组 → (family, size, bold)。

    绝不能对命名字体字符串下标: 'ACRPA_SMALL'[0]/[1] == 'A'/'C' 会拼出
    ('A', 'C', 'bold'), Tk 解析字号时抛 "expected integer but got C",
    导致所有设置卡构建失败、设置页整页空白 (仅侧边栏可见)。
    """
    if isinstance(font, str):
        return utils.FONT_SMALL_BOLD
    try:
        return (font[0], font[1], "bold")
    except Exception:
        return ("Segoe UI", 9, "bold")


def init_ctx(root_win=None, colors=None, fonts=None, app_root="", tlog=None,
             ensure_tray=None, destroy_tray=None, toggle_fold=None, main_run=None):
    global _root, C, _APP_ROOT, _MAIN_RUN, _tlog
    global FONT_BODY, FONT_SMALL, FONT_TINY, FONT_TITLE, FONT_SMALL_BOLD
    global _ensure_tray, _destroy_tray, _toggle_fold
    _root = root_win
    C = colors or _ui_theme.colors(False)
    if fonts:
        # 调用方 (ACRPA.py) 传入顺序为 (FONT_TITLE, FONT_BODY, FONT_SMALL,
        # FONT_BUTTON), 且均为 utils 命名字体字符串; FONT_TINY 未传, 用 utils 的。
        try:
            FONT_TITLE, FONT_BODY, FONT_SMALL = fonts[0], fonts[1], fonts[2]
        except Exception:
            pass
        FONT_TINY = getattr(utils, "FONT_TINY", FONT_TINY)
        FONT_SMALL_BOLD = _bold_font(FONT_SMALL)
    _APP_ROOT = app_root
    _MAIN_RUN = main_run
    _tlog = tlog
    _ensure_tray, _destroy_tray, _toggle_fold = ensure_tray, destroy_tray, toggle_fold


def on_theme_publish(dark=None, colors=None, prev=None):
    global C, _dark, _prev_colors
    _prev_colors = dict(prev or C or {})
    C = colors or _ui_theme.colors(dark)
    _dark = bool(dark)
    for _module, _handles, ctx in _apply_map.values():
        ctx.C = C
    refresh_theme(_prev_colors)


def update_sched_next_label(text):
    if _sched_next_label is not None:
        try:
            _sched_next_label.config(text=text)
        except Exception:
            pass


def _log1(msg, level="info"):
    try:
        utils.log_to_tab(msg, level)
    except Exception:
        pass


def _card_modules():
    from ui.settings.cards import exec as c_exec, ai as c_ai, sched as c_sched
    from ui.settings.cards import record as c_record, log as c_log, system as c_system
    from ui.settings.cards import quick as c_quick, advanced as c_advanced
    from ui.settings.cards import netlink as c_netlink, python as c_python
    from ui.settings.cards import market as c_market, extensions as c_extensions
    return {"exec": c_exec, "ai": c_ai, "sched": c_sched, "record": c_record,
            "log": c_log, "system": c_system, "quick": c_quick, "advanced": c_advanced,
            "netlink": c_netlink, "python": c_python, "market": c_market,
            "extensions": c_extensions}


def _set_window_icon(w):
    """设置窗口(及本模块弹出的子对话框)统一套 ACRPA 产品图标。

    实现收在 utils.set_window_icon: iconbitmap + WM_SETICON + <Map> 后补设,
    否则任务栏/Alt+Tab 会退回 Tk 默认图标。
    """
    try:
        return utils.set_window_icon(w)
    except Exception:
        return False


def _darken(hexc, factor=0.85):
    value = str(hexc or "").lstrip("#")
    if len(value) != 6:
        return hexc
    try:
        channels = [max(0, min(255, round(int(value[i:i + 2], 16) * factor)))
                    for i in (0, 2, 4)]
        return "#" + "".join("{:02x}".format(channel) for channel in channels)
    except (TypeError, ValueError):
        return hexc


def _role_of(colors, value):
    """颜色值 → 语义角色名 (反向查表), 换肤时按角色回填而非猜文案。"""
    for key, color in (colors or {}).items():
        if color == value:
            return key
    return None


_GEOMETRY_RE = re.compile(r"^\d{3,4}x\d{3,4}([+-]\d+[+-]\d+)?$")


def _valid_geometry(text):
    return bool(_GEOMETRY_RE.match(str(text or "").strip()))


def _window_geometry(win):
    """历史 geometry 只接受合法值, 并按 DPI/缩放档位给出默认尺寸与最小尺寸。"""
    try:
        screen_w, screen_h = win.winfo_screenwidth(), win.winfo_screenheight()
    except Exception:
        screen_w, screen_h = 1920, 1080
    min_w = min(sp(820), int(screen_w * 0.9))
    min_h = min(sp(560), int(screen_h * 0.85))
    saved = str(getattr(state, "WIN_GEOMETRY", "") or "").strip()
    if _valid_geometry(saved):
        return saved, min_w, min_h
    default_w = max(min_w, min(sp("win_w"), int(screen_w * 0.9)))
    default_h = max(min_h, min(sp("win_h"), int(screen_h * 0.85)))
    return "{}x{}".format(default_w, default_h), min_w, min_h


def _later(delay, callback):
    if _win is None:
        return None
    job = [None]

    def run():
        _after_jobs.discard(job[0])
        if _win is None:
            return
        try:
            callback()
        except tkinter.TclError:
            pass
    job[0] = _win.after(delay, run)
    _after_jobs.add(job[0])
    return job[0]


def _cancel_later(job):
    if job is None:
        return
    _after_jobs.discard(job)
    try:
        _win.after_cancel(job)
    except Exception:
        pass


def _flash_saved(card_key):
    _save_state[card_key] = "saved"
    for badge in _card_badges.get(card_key, ()):
        try:
            badge.config(text="✔ 已保存", fg=C["sc"])
        except Exception:
            pass
    _refresh_save_status()

    def reset():
        if _save_state.get(card_key) != "saved":
            return
        for badge in _card_badges.get(card_key, ()):
            try:
                badge.config(text="", fg=C["fgm"])
            except Exception:
                pass
    if _win is not None:
        _later(1200, reset)


def _save_fail(card_key, exc):
    _last_error[card_key] = str(exc)
    _save_state[card_key] = "error"
    for badge in _card_badges.get(card_key, ()):
        try:
            badge.config(text="⚠ 保存失败（点击重试）", fg=C["dg"])
        except Exception:
            pass
    _refresh_save_status()
    _log1("[{}] 保存失败: {}".format(card_key, exc), "error")


def _note_restart():
    """统计「需重启生效」项数，供底部状态栏提示。"""
    global _restart_count
    _restart_count += 1
    if _restart_var is None:
        return
    try:
        _restart_var.set("⏱ 需重启生效 {} 项".format(_restart_count))
    except Exception:
        pass


def _refresh_save_status():
    if _save_status_var is None:
        return
    if _field_errors:
        text = "输入无效 · 请检查标红的输入框"
        color = C["wn"]
    elif _last_error:
        text = "保存失败 · 点击卡片角标重试"
        color = C["dg"]
    elif _debounce_after:
        text = "有待保存的修改…"
        color = C["wn"]
    elif "saving" in _save_state.values():
        text = "正在保存…"
        color = C["fgm"]
    else:
        text = "✔ 所有设置已自动保存"
        color = C["sc"]
    try:
        _save_status_var.set(text)
        _save_status_var._status_color = color
        for widget in getattr(_save_status_var, "_labels", ()):
            widget.configure(fg=color)
    except Exception:
        pass


def _cancel_pending(card_key):
    """取消某卡的防抖定时器 (键是卡名, 值是 after id, 二者不可混用)。"""
    timer = _debounce_after.pop(card_key, None)
    if timer is None or _win is None:
        return
    try:
        _win.after_cancel(timer)
    except Exception:
        pass


def _do_save(card_key):
    entry = _apply_map.get(card_key)
    if entry is None:
        return True
    # 直连保存 (角标重试/关闭冲刷) 也要撤销待触发的防抖任务, 否则会二次保存。
    _cancel_pending(card_key)
    _save_state[card_key] = "saving"
    _refresh_save_status()
    mod, handles, ctx = entry
    try:
        mod.apply(ctx, handles)
        state.save_config()
    except Exception as exc:
        _save_fail(card_key, exc)
        return False
    _last_error.pop(card_key, None)
    _flash_saved(card_key)
    return True


def flush_pending_saves():
    """同步冲刷全部待保存卡片；任一失败时返回 False。"""
    pending = list(_debounce_after)
    for card_key in pending:
        _cancel_pending(card_key)
    ok = True
    for card_key in pending:
        if not _do_save(card_key):
            ok = False
    _refresh_save_status()
    return ok


def _reset_current_page(page):
    """只重置当前页声明的字段；关于页对应启动时检查更新。"""
    fields = {"about": (("check_update", "exec", "check_update_var"),)}.get(page, ())
    if not fields:
        return False
    defaults = {key: default for key, default, _cast in state._config_schema}
    for state_key, card_key, handle_key in fields:
        value = defaults[state_key]
        setattr(state, state_key.upper(), value)
        variable = _handles_map.get(card_key, {}).get(handle_key)
        if variable is not None:
            variable.set(value)
    for card_key in {item[1] for item in fields}:
        _auto_save(card_key)
    return flush_pending_saves()


def _auto_save(card_key, *_args):
    if _win is None or card_key not in _apply_map:
        return
    old = _debounce_after.pop(card_key, None)
    if old is not None:
        try:
            _win.after_cancel(old)
        except Exception:
            pass
    _save_state[card_key] = "pending"
    for badge in _card_badges.get(card_key, ()):
        try:
            badge.config(text="● 待保存", fg=C["wn"])
        except Exception:
            pass
    _refresh_save_status()
    _debounce_after[card_key] = _win.after(600, lambda k=card_key: _do_save(k))


def _track_card_vars(card_key, variables):
    for var in variables:
        try:
            var.trace_add("write", lambda *_a, k=card_key: _auto_save(k))
        except Exception:
            pass


def _make_badge(card_key, parent):
    badge = tkinter.Label(parent, text="", font=FONT_TINY, fg=C["fgm"], bg=C["bgc"])
    badge.pack(side="left", padx=(8, 0))
    _saved_badges[card_key] = badge
    _card_badges.setdefault(card_key, []).append(badge)

    def retry(_event=None):
        if card_key not in _last_error:
            return
        if _do_save(card_key):
            try:
                _show_toast(_root, "设置已保存", "success")
            except Exception:
                pass
        else:
            messagebox.showerror("保存失败", _last_error.get(card_key, "未知错误"), parent=_win)
    badge.bind("<Button-1>", retry)
    badge.configure(cursor="hand2")
    return badge


def _register_nav_card(key, widget):
    if key not in {item[0] for item in _nav_cards}:
        _nav_cards.append((key, widget))


def _on_nav_click(key, _event=None):
    page = key if key in PAGE_META else CARD_TO_PAGE.get(key)
    if not page or page not in _page_hosts or _canvas is None:
        return
    global _active_page
    if _active_page in _page_hosts:
        try:
            _page_scroll[_active_page] = _canvas.yview()[0]
            _page_hosts[_active_page].grid_remove()
        except Exception:
            pass
    _active_page = page
    host = _page_hosts[page]
    host.grid(row=0, column=0, sticky="ew")
    title, desc = PAGE_META[page]
    _header_title.configure(text=title)
    _header_desc.configure(text=desc)
    if _navigation is not None:
        _navigation.show_page(page)
    _win.update_idletasks()
    _canvas.configure(scrollregion=_canvas.bbox("all"))
    _canvas.yview_moveto(_page_scroll.get(page, 0.0))


def _indexable_text(value):
    """搜索索引只收有语义的文本, 过滤「⏱ 需重启」「px (380-900)」这类装饰标签。"""
    text = " ".join(str(value or "").split())
    if len(text) < 2 or len(text) > 120:
        return ""
    if not any(ch.isalnum() for ch in text):
        return ""
    has_cjk = any("\u4e00" <= ch <= "\u9fff" for ch in text)
    # 纯 ASCII + 数字 = 单位/范围/版本号, 不是可搜索字段
    if not has_cjk and any(ch.isdigit() for ch in text):
        return ""
    return text


def _build_search_index():
    records = []
    seen = set()

    def add(page, title, target, aliases=""):
        title = _indexable_text(title)
        if not title:
            return
        key = (page, title)
        if key in seen:
            return
        seen.add(key)
        page_title, desc = PAGE_META[page]
        search = " ".join((page_title, desc, title, aliases)).lower()
        records.append({"page": page, "page_title": page_title, "title": title,
                        "target": target, "search": search})

    from ui.settings.navigation import PAGE_ALIASES
    for page in PAGE_ORDER:
        host = _page_hosts.get(page)
        if host is None:
            continue
        aliases = " ".join(PAGE_ALIASES.get(page, ()))
        add(page, PAGE_META[page][0], host, aliases)

        def walk(widget):
            for child in widget.winfo_children():
                try:
                    if child.winfo_class() in ("Entry", "TEntry", "Spinbox", "TCombobox"):
                        walk(child)
                        continue
                    if str(child.cget("textvariable") or ""):
                        walk(child)
                        continue
                    text = child.cget("text")
                    if text and child.winfo_class() not in ("Canvas",):
                        add(page, text, child, aliases)
                except Exception:
                    pass
                walk(child)
        walk(host)
    return records


def _clear_highlight():
    global _highlight_after
    if _highlight_after is not None:
        _cancel_later(_highlight_after)
        _highlight_after = None


_FOCUSABLE = ("Entry", "TEntry", "Spinbox", "Text", "TCombobox", "Listbox")


def _focus_first(widget):
    """让定位结果真正可编辑: Label 自身无法获得焦点, 需下钻到行内输入控件。"""
    try:
        if widget.winfo_class() in _FOCUSABLE:
            widget.focus_set()
            return True
    except Exception:
        return False
    try:
        children = widget.winfo_children()
    except Exception:
        return False
    for child in children:
        if _focus_first(child):
            return True
    return False


def _locate_search_result(record):
    global _highlight_after
    _clear_highlight()
    _on_nav_click(record["page"])
    target = record.get("target")
    if target is None:
        return
    try:
        _win.update_idletasks()
        if target is not _page_hosts[record["page"]]:
            y = target.winfo_rooty() - _canvas.winfo_rooty() + _canvas.canvasy(0)
            total = max(1, _inner.winfo_reqheight())
            _canvas.yview_moveto(max(0.0, min(1.0, (y - 80) / total)))
            # 向上找几层, 命中目标所在的表单行后聚焦其输入框 (Label 本身不可聚焦)
            node = target
            for _ in range(4):
                if node is None or _focus_first(node):
                    break
                node = getattr(node, "master", None)
        highlight = target
        while highlight is not None and highlight.winfo_class() not in ("Frame", "Label"):
            highlight = highlight.master
        original = None
        if highlight is not None:
            try:
                original = highlight.cget("bg")
                highlight.configure(bg=C["acl"])
            except Exception:
                highlight = None

        def restore():
            global _highlight_after
            _highlight_after = None
            if highlight is not None and original is not None:
                try:
                    highlight.configure(bg=original)
                except Exception:
                    pass
        _highlight_after = _later(1400, restore)
    except Exception as exc:
        _log1("设置定位失败: {}".format(exc), "warning")


def _on_escape(_event=None):
    if _navigation is not None and _navigation.query_var.get():
        _navigation.clear_search()
        return "break"
    _close()
    return "break"


def _show_toast(root, message, kind="info"):
    try:
        from ui.base import show_toast
        show_toast(root, message, kind)
    except Exception:
        pass


def _stop_animations(widget):
    """销毁前停掉自绘控件动画, 否则残留 after 任务会在 Tk 里报 invalid command name。"""
    anim = getattr(widget, "_anim", None)
    if anim is not None:
        try:
            anim.stop()
        except Exception:
            pass
    try:
        children = widget.winfo_children()
    except Exception:
        return
    for child in children:
        _stop_animations(child)


def _close(force=False):
    global _win, _inner, _canvas, _navigation, _sched_next_label, _highlight_after
    global _save_status_var, _restart_var
    if _win is None:
        return
    if not force and _debounce_after:
        if not flush_pending_saves():
            choice = messagebox.askyesnocancel(
                "设置尚未保存",
                "部分设置保存失败。\n\n是：重试保存\n否：放弃未保存修改并关闭\n取消：返回设置窗口",
                parent=_win)
            if choice is None:
                return
            if choice:
                if not flush_pending_saves():
                    return
            else:
                force = True
    try:
        # 最大化/贴边时 geometry() 返回的是展开后的尺寸, 记下来会让下次启动“假最大化”。
        if _win.state() == "normal":
            state.WIN_GEOMETRY = _win.geometry()
            state.save_config()
    except Exception as exc:
        _log1("保存设置窗口位置失败: {}".format(exc), "warning")
    for card_key in list(_debounce_after):
        _cancel_pending(card_key)
    _debounce_after.clear()
    for timer in list(_after_jobs):
        _cancel_later(timer)
    _after_jobs.clear()
    _clear_highlight()
    _stop_animations(_win)
    # 先释放对控件/Tk 变量的引用再 destroy: 否则 Variable.__del__ 会在解释器
    # 退出阶段调用已销毁的解释器, 打出 "main thread is not in main loop"。
    _page_hosts.clear()
    _page_scroll.clear()
    _handles_map.clear()
    _apply_map.clear()
    _saved_badges.clear()
    _card_badges.clear()
    _nav_cards[:] = []
    _search_records[:] = []
    _save_state.clear()
    _last_error.clear()
    _field_errors.clear()
    _save_status_var = None
    _restart_var = None
    # 引用释放后立即回收, 让 Tk Variable 在解释器仍存活时析构 (否则 __del__ 报
    # "main thread is not in main loop")。
    try:
        gc.collect()
    except Exception:
        pass
    try:
        _win.destroy()
    except Exception:
        pass
    _win = _inner = _canvas = _navigation = None
    _sched_next_label = None


def _reopen_window():
    open_settings_window()


def _validate_number(entry, var, name, default, cast=int, lo=None, hi=None, card_key=None):
    """数值输入即时校验: 只标红 + 状态栏提示, 不伪造「保存失败」。

    此前把校验错误写进 `_last_error` 并直接 `_save_fail`, 于是用户刚清空输入框
    就会看到「保存失败」并留下橙色角标; 且键用的是字段名而不是卡名, 纠正后也
    不会被 `_do_save` 清掉, 状态栏永久停在失败态。
    """
    error_key = "{}:{}".format(card_key or "", name)

    def validate(*_args):
        try:
            value = cast(var.get())
            if lo is not None and value < lo:
                raise ValueError("过小")
            if hi is not None and value > hi:
                raise ValueError("过大")
            entry.config(fg=C["fgb"])
            if _field_errors.pop(error_key, None) is not None:
                _refresh_save_status()
        except (ValueError, TypeError):
            entry.config(fg=C["dg"])
            if error_key not in _field_errors:
                _field_errors[error_key] = "{} 无效".format(name)
                _refresh_save_status()
    var.trace_add("write", validate)
    return entry


def refresh_theme(prev=None):
    global C
    if prev is not None and prev is not C:
        _prev_colors.update(prev)
    if _win is None:
        return
    _win.configure(bg=C["bg"])
    if _inner is not None:
        _inner.configure(bg=C["bg"])
    if _canvas is not None:
        _canvas.configure(bg=C["bg"])
    if _header_title is not None:
        _header_title.configure(bg=C["bg"], fg=C["fgt"])
    if _header_desc is not None:
        _header_desc.configure(bg=C["bg"], fg=C["fgm"])

    def walk(w):
        for child in w.winfo_children():
            try:
                cls = child.winfo_class()
                if cls in ("Frame", "Label", "Labelframe"):
                    fg = child.cget("fg") if cls == "Label" else None
                    bg = child.cget("bg")
                    # 按角色回填: 搜索框占位 Label 的底色是 ebg, 旧逻辑一律改成 bg,
                    # 会在输入框里留下与主题不符的色块。
                    bg_role = _role_of(_prev_colors, bg) or _role_of(C, bg)
                    if bg_role in ("bg", "bgc", "ebg", "acl"):
                        child.configure(bg=C[bg_role])
                    else:
                        child.configure(bg=C["bgc"] if bg in
                                        (_prev_colors.get("bgc"), C["bgc"]) else C["bg"])
                    if cls == "Label" and fg:
                        role = _role_of(_prev_colors, fg) or _role_of(C, fg)
                        if role in C:
                            child.configure(fg=C[role])
                        else:
                            child.configure(fg=C["fgm"])
                elif cls in ("Entry", "Spinbox"):
                    # tk 的 relief="solid" 边框恒为黑色, 深色下几乎不可见、浅色下过重;
                    # 统一改成 1px 发丝描边 + 强调色 focus 环。
                    child.configure(bg=C["ebg"], fg=C["fgb"], insertbackground=C["fgb"],
                                    relief="flat", bd=0, highlightthickness=1,
                                    highlightbackground=C["bd"], highlightcolor=C["focus"],
                                    selectbackground=C["acl"], selectforeground=C["fgt"])
                elif cls == "Button":
                    bg = child.cget("bg")
                    role = {v: k for k, v in _prev_colors.items()}.get(bg)
                    role = role or {v: k for k, v in C.items()}.get(bg)
                    if role in ("ac", "sc", "wn", "dg"):
                        child.configure(bg=C[role], fg=_ui_theme.text_on(C[role]),
                                        activebackground=_darken(C[role]),
                                        activeforeground=_ui_theme.text_on(_darken(C[role])),
                                        highlightbackground=C["bd"])
                    else:
                        child.configure(bg=C["bgc"], fg=C["fgb"],
                                        activebackground=_darken(C["bgc"]),
                                        activeforeground=C["fgb"], highlightbackground=C["bd"])
                elif cls == "TLabel":
                    child.configure(background=C["bgc"], foreground=C["fgb"])
                elif cls == "TFrame":
                    child.configure(style="Card.TFrame")
                redraw = getattr(child, "_acrpa_redraw", None)
                if callable(redraw):
                    redraw(C)
            except Exception:
                pass
            walk(child)
    walk(_win)
    # 页头在通用 Label 换肤后最后设置，避免新主题前景色被误识别为次要文字。
    if _header_title is not None:
        _header_title.configure(bg=C["bg"], fg=C["fgt"])
    if _header_desc is not None:
        _header_desc.configure(bg=C["bg"], fg=C["fgm"])
    if _navigation is not None:
        _navigation.refresh(C)
    _refresh_save_status()


def open_settings_window():
    global _win, _inner, _canvas, _navigation, _header_title, _header_desc
    global _save_status_var, _active_page, _search_records
    global _restart_var, _restart_count
    if _win is not None:
        try:
            _win.deiconify()
            _win.lift()
            _win.focus_force()
            return
        except Exception:
            _win = None
    _win = tkinter.Toplevel(_root)
    _win.title("ACRPA 设置")
    # 声明自管换肤: ThemeBus publish 时通用 walk 跳过本窗口, 由本模块的
    # on_theme_publish → refresh_theme 负责 (含 walk 的角色回填逻辑)。
    _ui_theme.claim_window(_win, "settings_window")
    geometry, min_w, min_h = _window_geometry(_win)
    _win.geometry(geometry)
    _win.minsize(min_w, min_h)
    _win.configure(bg=C["bg"])
    _win.protocol("WM_DELETE_WINDOW", _close)
    _set_window_icon(_win)
    _win.columnconfigure(1, weight=1)
    _win.rowconfigure(0, weight=1)

    _navigation = SettingsNavigation(_win, C, _on_nav_click, _locate_search_result)
    _navigation.grid(row=0, column=0, rowspan=2, sticky="nsew")
    # Frame 的 width 会被内容顶开/压窄, 用列最小宽锁住导航区, 保证九项不折行
    _win.columnconfigure(0, minsize=sp(200))
    separator = tkinter.Frame(_win, bg=C["bd"], width=sp("hairline"))
    separator.grid(row=0, column=0, rowspan=2, sticky="nes")
    separator.lift()

    workspace = tkinter.Frame(_win, bg=C["bg"])
    workspace.grid(row=0, column=1, sticky="nsew")
    workspace.columnconfigure(0, weight=1)
    workspace.rowconfigure(1, weight=1)
    header = tkinter.Frame(workspace, bg=C["bg"])
    header.grid(row=0, column=0, sticky="ew", padx=sp("sp_xl"), pady=(sp("sp_lg"), sp("sp_sm")))
    header.columnconfigure(0, weight=1)
    _header_title = tkinter.Label(header, text="常规", font=FONT_TITLE,
                                  bg=C["bg"], fg=C["fgt"], anchor="w")
    _header_title.grid(row=0, column=0, sticky="w")
    _header_desc = tkinter.Label(header, text=PAGE_META["general"][1], font=FONT_SMALL,
                                 bg=C["bg"], fg=C["fgm"], anchor="w")
    _header_desc.grid(row=1, column=0, sticky="w", pady=(sp("gap_tight"), 0))

    content = tkinter.Frame(workspace, bg=C["bg"])
    content.grid(row=1, column=0, sticky="nsew", padx=(sp("sp_lg"), sp("sp_sm")))
    content.columnconfigure(0, weight=1)
    content.rowconfigure(0, weight=1)
    scrollbar = ttk.Scrollbar(content, orient="vertical")
    scrollbar.grid(row=0, column=1, sticky="ns")
    _canvas = tkinter.Canvas(content, bg=C["bg"], highlightthickness=0,
                             yscrollcommand=scrollbar.set)
    _canvas.grid(row=0, column=0, sticky="nsew")
    scrollbar.configure(command=_canvas.yview)
    _inner = tkinter.Frame(_canvas, bg=C["bg"])
    _inner.columnconfigure(0, weight=1)
    _canvas.create_window((0, 0), window=_inner, anchor="nw")

    def on_configure(_event):
        _canvas.configure(scrollregion=_canvas.bbox("all"))
        try:
            _canvas.itemconfigure(_canvas.find_all()[0], width=_canvas.winfo_width())
        except Exception:
            pass
    _inner.bind("<Configure>", on_configure)
    _canvas.bind("<Configure>", on_configure)

    def on_wheel(event):
        if _win is None or _canvas is None:
            return None
        # 指针落在自带滚动/步进的控件上时交回它自己处理, 避免双重滚动
        try:
            if event.widget.winfo_class() in ("Listbox", "Text", "Spinbox", "TCombobox"):
                return None
        except Exception:
            pass
        delta = int(getattr(event, "delta", 0) or 0)
        if not delta:
            return None
        steps = int(-delta / 120) or (-1 if delta > 0 else 1)
        _canvas.yview_scroll(steps, "units")
        return "break"

    def on_wheel_linux(event):
        if _canvas is None:
            return None
        _canvas.yview_scroll(-1 if getattr(event, "num", 5) == 4 else 1, "units")
        return "break"

    # 绑在 Toplevel 上: Tk 的 bindtags 含所属 toplevel, 卡片里的子控件才能冒泡到
    # 这里; 之前只绑 canvas/inner, 指针停在卡片内容上滚轮完全无效。
    _win.bind("<MouseWheel>", on_wheel)
    _win.bind("<Button-4>", on_wheel_linux)
    _win.bind("<Button-5>", on_wheel_linux)

    footer = tkinter.Frame(_win, bg=C["bg"])
    footer.grid(row=1, column=1, sticky="ew", padx=sp("sp_lg"), pady=(sp("sp_sm"), sp("sp_md")))
    footer.columnconfigure(0, weight=1)
    _save_status_var = tkinter.StringVar(value="✔ 所有设置已自动保存")
    _save_status_var._labels = []
    save_label = tkinter.Label(footer, textvariable=_save_status_var, font=FONT_SMALL,
                               bg=C["bg"], fg=C["sc"], anchor="w")
    save_label.grid(row=0, column=0, sticky="w")
    _save_status_var._labels.append(save_label)
    _restart_var = tkinter.StringVar(value="")
    restart_label = tkinter.Label(footer, textvariable=_restart_var, font=FONT_TINY,
                                  bg=C["bg"], fg=C["wn"], anchor="e")
    restart_label.grid(row=0, column=1, sticky="e", padx=(sp("sp_sm"), sp("sp_md")))
    RoundedButton(footer, text="关闭", command=_close, colors=C,
                  width=sp(76)).grid(row=0, column=2, sticky="e")

    def make_collapsible_card(parent, title, icon=""):
        card = tkinter.Frame(parent, bg=C["bgc"], highlightbackground=C["bd"],
                             highlightthickness=sp("hairline"), bd=0)
        card.columnconfigure(0, weight=1)
        title_row = tkinter.Frame(card, bg=C["bgc"])
        title_row.grid(row=0, column=0, sticky="ew", padx=sp("sp_lg"), pady=(sp("sp_md"), sp("sp_sm")))
        tkinter.Label(title_row, text=title, font=FONT_SMALL_BOLD,
                      fg=C["fgt"], bg=C["bgc"]).pack(side="left")
        arrow = tkinter.Label(title_row, text="−", font=FONT_BODY,
                              fg=C["fgm"], bg=C["bgc"], cursor="hand2")
        arrow.pack(side="right")
        content_frame = tkinter.Frame(card, bg=C["bgc"])
        content_frame.grid(row=1, column=0, sticky="ew", padx=sp("sp_lg"), pady=(0, sp("sp_lg")))
        content_frame.columnconfigure(0, weight=1)

        def toggle(_event=None):
            if content_frame.winfo_ismapped():
                content_frame.grid_remove()
                arrow.config(text="+")
            else:
                content_frame.grid()
                arrow.config(text="−")
        arrow.bind("<Button-1>", toggle)
        return card, content_frame, title_row, arrow

    def btn(parent, text, command, bg=None, fg=None, tip=None):
        bg = bg or C["bgc"]
        role = next((key for key in ("ac", "sc", "wn", "dg") if bg == C.get(key)), None)
        button = RoundedButton(parent, text=text, command=command, colors=C,
                               role=role, tooltip=tip)
        return button

    ctx = type("SettingsContext", (), {})()
    ctx.C = C
    ctx.FONT_BODY = FONT_BODY
    ctx.FONT_SMALL = FONT_SMALL
    ctx.FONT_SMALL_BOLD = FONT_SMALL_BOLD
    ctx.FONT_TITLE = FONT_TITLE
    ctx.FONT_TINY = FONT_TINY
    # 卡片外距按缩放令牌派生, 避免 ui_scale 1.5 时卡片仍用固定 5/3px 挤在一起
    PAD.update({"padx": sp("sp_xs"), "pady": sp("sp_sm")})
    ctx.PAD = PAD
    ctx.APP_ROOT = _APP_ROOT
    ctx.sp = sp
    ctx.ctrl_h = ctrl_h
    ctx.root = _root
    ctx.win = _win
    ctx.tlog = _tlog
    ctx.darken = _darken
    ctx.attach_tooltip = utils.attach_tooltip
    ctx.log1 = _log1
    ctx.show_toast = _show_toast
    ctx.ui_theme = _ui_theme
    ctx.set_window_icon = _set_window_icon
    ctx.make_collapsible_card = make_collapsible_card
    ctx.make_badge = _make_badge
    ctx.register_nav_card = _register_nav_card
    ctx.btn = btn
    ctx.validate_number = _validate_number
    ctx.note_restart = _note_restart
    ctx.flash_saved = _flash_saved
    ctx.later = _later
    ctx.track_card_vars = _track_card_vars
    ctx.set_sched_label = lambda label: globals().__setitem__("_sched_next_label", label)
    ctx.ensure_tray = _ensure_tray
    ctx.destroy_tray = _destroy_tray
    ctx.toggle_fold = _toggle_fold
    ctx.main_run = _MAIN_RUN
    ctx.close_window = _close
    ctx.reopen_window = _reopen_window
    ctx.reset_current_page = _reset_current_page
    ctx.filedialog = filedialog

    _nav_cards[:] = []
    _saved_badges.clear()
    _card_badges.clear()
    _debounce_after.clear()
    _apply_map.clear()
    _handles_map.clear()
    _last_error.clear()
    _field_errors.clear()
    _save_state.clear()
    _page_hosts.clear()
    _page_scroll.clear()
    _active_page = "general"
    _restart_count = 0
    modules = _card_modules()
    for page in PAGE_ORDER:
        host = tkinter.Frame(_inner, bg=C["bg"])
        host.columnconfigure(0, weight=1)
        _page_hosts[page] = host
        for card_key, section in _PAGE_BUILD[page]:
            module = modules[card_key]
            handles = (module.build(host, ctx, section=section) if section
                       else module.build(host, ctx))
            if card_key == "advanced":
                _handles_map.setdefault(card_key, {}).update(handles)
            else:
                _handles_map[card_key] = handles
            if card_key != "quick":
                _apply_map[card_key] = (module, _handles_map[card_key], ctx)
    # advanced 的 entry 必须在四分节 handles 合并后更新，避免保存时拿到部分句柄。
    _apply_map["advanced"] = (modules["advanced"], _handles_map["advanced"], ctx)

    refresh_theme()
    _search_records = _build_search_index()
    _navigation.set_records(_search_records)
    _on_nav_click("general")
    _win.bind("<Control-f>", lambda _e: (_navigation.search_entry.focus_set(), "break")[1])
    _win.bind("<Control-F>", lambda _e: (_navigation.search_entry.focus_set(), "break")[1])
    _win.bind("<Escape>", _on_escape)
    _later(250, lambda: _win.focus_set() if _win else None)
