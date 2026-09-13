"""
ACRPA — 自动化工作流工具
Features: Script Editor | Execution Control | Action Recorder | Templates
          Log Export | Screenshot Tool | Error Retry | Dark Mode | Config | Hotkeys

P0 Optimization #1: Lazy imports for heavy libraries (PIL, pyautogui, pyperclip)
"""
import tkinter
from tkinter import ttk
from tkinter import filedialog
from tkinter import messagebox
import os, sys, time, queue, json, re, ctypes
import shutil, threading, datetime
# P0 Optimization #1: Lazy import PIL modules
_PIL_loaded = False
Image = None
ImageTk = None
ImageGrab = None

def _load_pil():
    """Lazy load PIL modules on first use"""
    global Image, ImageTk, ImageGrab, _PIL_loaded
    if not _PIL_loaded:
        from PIL import Image as Img, ImageTk as ITk, ImageGrab as IGr
        Image, ImageTk, ImageGrab = Img, ITk, IGr
        _PIL_loaded = True

# P0 Optimization #1: Lazy load pyautogui (heavy ~500ms import)
_pyautogui = None
def _get_pa():
    global _pyautogui
    if _pyautogui is None:
        import pyautogui as pa
        _pyautogui = pa
    return _pyautogui

import state
from utils import (create_card, _btn, _darken, apply_theme, _colors,
                    ThreadSafeLog, set_tlog, log1)
from scriptdata import ScriptData
from engine import engine
import recorder
import scheduler as sched
from tray import SystemTray
# 独立弹窗模块 (依赖注入: init_ctx 在 root 创建后调用)
from dialogs import (init_ctx, show_help_dialog, open_version_history,
                     open_ai_panel, open_ai_debug_dialog, open_sched_manager)
# 独立设置窗口模块 (设置 Tab 已分离，主题切换时由 _refresh_theme 同步)
import settings_window

# 托盘图标全局引用
_tray = None  # type: SystemTray | None

state.load_config()


# ======================================================================
# SECTION: System Tray Management
# ======================================================================

def _ensure_tray():
    """确保托盘图标已创建（幂等）。"""
    global _tray
    if _tray is not None:
        return
    ico = os.path.join(RES_DIR, "automation.ico")
    if not os.path.exists(ico):
        ico = os.path.join(APP_ROOT, "automation.ico")
    _tray = SystemTray(root, icon_path=ico,
                       on_restore=_restore_from_tray,
                       on_quit=_quit_from_tray,
                       on_run=_tray_run,
                       on_stop=_tray_stop,
                       on_open_settings=_tray_open_settings,
                       on_open_scheduler=_tray_open_scheduler)

def _tray_run():
    """托盘菜单：执行全部"""
    try:
        if not state.running:
            main_run()
    except Exception as e:
        log1("托盘执行失败: {}".format(e), "error")

def _tray_stop():
    """托盘菜单：取消执行"""
    try:
        if state.running or state.recording:
            stop_execution()
            if state.recording:
                state.record_stop = True
    except Exception as e:
        log1("托盘停止失败: {}".format(e), "error")

def _tray_open_settings():
    """托盘菜单：打开设置"""
    try:
        settings_window.open_settings_window()
        root.deiconify()
        root.lift()
        root.focus_force()
    except Exception as e:
        log1("打开设置失败: {}".format(e), "error")

def _tray_open_scheduler():
    """托盘菜单：打开定时执行设置"""
    try:
        open_sched_manager()
        root.deiconify()
        root.lift()
        root.focus_force()
    except Exception as e:
        log1("打开定时设置失败: {}".format(e), "error")


def _restore_from_tray():
    """从托盘恢复主窗口（考虑 Mini Bar 折叠状态）。"""
    global _tray
    if state.folded and state.MINI_BAR_ENABLED:
        # 如果之前是折叠状态，恢复时显示 Mini Bar
        _create_mini_bar()
        log1("已恢复 Mini Bar")
        from utils import show_toast
        show_toast(root, "ACRPA Mini Bar 已恢复", "info", 1500)
    else:
        root.deiconify()
        root.lift()
        root.focus_force()
        # 如果之前在最小化状态，恢复正常
        try:
            if root.state() == "iconic":
                root.state("normal")
        except Exception:
            pass
        log1("主窗口已恢复")
        from utils import show_toast
        show_toast(root, "ACRPA 已恢复", "info", 1500)


def _quit_from_tray():
    """从托盘菜单彻底退出程序。"""
    global _tray
    # 先销毁托盘图标
    if _tray:
        _tray.destroy()
        _tray = None
    # 恢复窗口（可能处于 withdraw 状态），然后执行关闭
    try:
        root.deiconify()
    except Exception:
        pass
    _do_close()


def _destroy_tray():
    """安全销毁托盘图标。"""
    global _tray
    if _tray:
        _tray.destroy()
        _tray = None


# ======================================================================
# SECTION: Mini Bar (折叠模式)
# ======================================================================

_mini_bar = None  # Mini Bar Toplevel 引用


def _mb_set_icon(label):
    """在 Mini Bar 左侧加载并显示应用图标（20×20）。"""
    try:
        png_path = os.path.join(RES_DIR, "automation.png")
        if not os.path.exists(png_path):
            png_path = os.path.join(APP_ROOT, "automation.png")
        if not os.path.exists(png_path):
            # PyInstaller onedir: res/ 在 exe 同级的 _internal 目录
            exe_dir = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else ""
            if exe_dir:
                alt = os.path.join(exe_dir, "_internal", "res", "automation.png")
                if os.path.exists(alt):
                    png_path = alt
                else:
                    alt = os.path.join(exe_dir, "res", "automation.png")
                    if os.path.exists(alt):
                        png_path = alt
        if os.path.exists(png_path):
            _load_pil()
            img = Image.open(png_path).resize((20, 20), Image.LANCZOS)
            photo = ImageTk.PhotoImage(img)
            label.configure(image=photo)
            label._photo = photo  # 保存引用防止被 GC
        else:
            # 回退：用文字图标
            label.configure(text="⚙", font=("Segoe UI Symbol", 11),
                fg=C["ac"], bg=C["bgc"])
    except Exception:
        # 回退：用文字图标
        try:
            label.configure(text="⚙", font=("Segoe UI Symbol", 11),
                fg=C["ac"], bg=C["bgc"])
        except Exception:
            pass


def _create_mini_bar():
    """创建 Mini Bar 悬浮条（折叠后的微型状态栏）。

    Mini Bar 设计（增强版）：
    ┌─────────────────────────────────────────────────────────────────────┐
    │ [🔧] ● 运行中  循环2/5  行12/45  00:03:22 │ ■停止 │ ▶运行 │ ⏸暂停 │ ⏭ │ □展开 │
    └─────────────────────────────────────────────────────────────────────┘
    """
    global _mini_bar
    if _mini_bar is not None:
        return

    mb = tkinter.Toplevel(root)
    _set_window_icon(mb)
    mb.overrideredirect(True)
    mb.attributes("-topmost", True)
    mb.configure(bg=C["bgc"])

    # 尺寸和初始位置（主窗口左上角附近）—— 空闲时紧凑，运行时由 _sync_mini_bar_status 扩展
    mb_w, mb_h = 430, 30
    rx, ry = root.winfo_x(), root.winfo_y()
    mb.geometry("{}x{}+{}+{}".format(mb_w, mb_h, max(0, rx), max(0, ry - mb_h - 4)))

    # 外框
    outer = tkinter.Frame(mb, bg=C["ac"], bd=0)
    outer.pack(fill="both", expand=True, padx=1, pady=1)
    inner = tkinter.Frame(outer, bg=C["bgc"], bd=0)
    inner.pack(fill="both", expand=True, padx=1, pady=1)

    # ── 应用图标 ──
    mb_icon_label = tkinter.Label(inner, text="", bg=C["bgc"], cursor="hand2")
    mb_icon_label.pack(side="left", padx=(6, 2))
    # 加载并设置图标
    _mb_set_icon(mb_icon_label)

    # ── 状态指示 + 运行信息 ──
    mb_status = tkinter.Label(inner, text="● 就绪",
        font=FONT_BUTTON, fg=C["fgm"], bg=C["bgc"])
    mb_status.pack(side="left", padx=(2, 4))
    mb_info = tkinter.Label(inner, text="",
        font=("Consolas", 8), fg=C["fgm"], bg=C["bgc"])
    mb_info.pack(side="left", padx=(0, 4))

    # ── 分隔符 ──
    mb_sep1 = tkinter.Frame(inner, bg=C["bd"], width=1)
    mb_sep1.pack(side="left", fill="y", padx=3, pady=4)

    # ── 操作按钮 ──
    def _mb_btn(text, color, cmd):
        btn = tkinter.Label(inner, text=text, font=FONT_BUTTON,
            fg="white", bg=color, padx=7, pady=1,
            activebackground=_darken(color), activeforeground="white",
            cursor="hand2", relief="raised", bd=1)
        btn.bind("<Button-1>", lambda e, c=cmd: c())
        return btn

    mb_stop = _mb_btn("■ 停止", C["dg"], stop_execution)
    mb_stop.pack(side="left", padx=1)
    mb_run = _mb_btn("▶ 运行", C["sc"], main_run)
    mb_run.pack(side="left", padx=1)
    mb_pause = _mb_btn("⏸ 暂停", C["wn"], toggle_pause)
    mb_pause.pack(side="left", padx=1)
    mb_step = _mb_btn("⏭", C["ac"], _step_once)
    mb_step.pack(side="left", padx=1)

    # ── 分隔符 ──
    tkinter.Frame(inner, bg=C["bd"], width=1).pack(side="left", fill="y", padx=3, pady=4)

    # ── 展开按钮 ──
    mb_expand = tkinter.Label(inner, text="□ 展开",
        font=FONT_BUTTON, fg=C["fgb"], bg=C["bgc"],
        padx=7, pady=1, cursor="hand2",
        activebackground=C["acl"], activeforeground=C["fgb"],
        relief="raised", bd=1)
    mb_expand.pack(side="right", padx=(1, 6))
    mb_expand.bind("<Button-1>", lambda e: _toggle_fold())

    # ── 拖拽支持 ──
    _drag_data = {"x": 0, "y": 0}

    def _drag_start(event):
        _drag_data["x"] = event.x_root - mb.winfo_x()
        _drag_data["y"] = event.y_root - mb.winfo_y()

    def _drag_move(event):
        mb.geometry("+{}+{}".format(
            event.x_root - _drag_data["x"],
            event.y_root - _drag_data["y"]))

    # 整个 Mini Bar 可拖拽
    mb.bind("<Button-1>", _drag_start)
    mb.bind("<B1-Motion>", _drag_move)
    inner.bind("<Button-1>", _drag_start)
    inner.bind("<B1-Motion>", _drag_move)
    mb_status.bind("<Button-1>", _drag_start)
    mb_status.bind("<B1-Motion>", _drag_move)
    mb_info.bind("<Button-1>", _drag_start)
    mb_info.bind("<B1-Motion>", _drag_move)
    mb_icon_label.bind("<Button-1>", _drag_start)
    mb_icon_label.bind("<B1-Motion>", _drag_move)
    mb_expand.bind("<B1-Motion>", _drag_move)

    # ── 右键菜单 ──
    def _mb_right_menu(event):
        menu = tkinter.Menu(mb, tearoff=0, font=FONT_BODY)
        menu.add_command(label="展开完整窗口", command=_toggle_fold)
        menu.add_separator()
        menu.add_command(label="退出 ACRPA", command=_quit_from_tray)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
    mb.bind("<Button-3>", _mb_right_menu)
    inner.bind("<Button-3>", _mb_right_menu)

    # 存储子控件引用以便后续更新
    mb._widgets = {
        "icon": mb_icon_label, "status": mb_status,
        "info": mb_info, "sep1": mb_sep1,
        "stop": mb_stop, "run": mb_run,
        "pause": mb_pause, "step": mb_step,
        "expand": mb_expand, "inner": inner,
    }
    _mini_bar = mb
    _sync_mini_bar_status()


def _destroy_mini_bar():
    """销毁 Mini Bar。"""
    global _mini_bar
    if _mini_bar is not None:
        try:
            _mini_bar.destroy()
        except Exception:
            pass
        _mini_bar = None


# ======================================================================
# SECTION: Recording Bar (录制专用 Mini Bar —  )
# ======================================================================

_recording_bar = None  # Recording Bar Toplevel 引用
_rec_bar_poll_id = None  # after() ID for action count polling


def _create_recording_bar():
    """创建录制专用 Mini Bar（录制时主窗口折叠，仅显示此悬浮条）。

    设计（  录制时折叠 UI）：
    ┌───────────────────────────────────────────────────────────────┐
    │ [●] ● 录制中  动作:5  模式:相对坐标  │  ⏸ 暂停  │  ■ 停止录制 │
    └───────────────────────────────────────────────────────────────┘
    """
    global _recording_bar
    if _recording_bar is not None:
        return

    rb = tkinter.Toplevel(root)
    _set_window_icon(rb)
    rb.overrideredirect(True)
    rb.attributes("-topmost", True)
    rb.configure(bg=C["dg"])  # 红色边框 — 录制状态

    # 尺寸和位置（屏幕顶部居中）
    rb_w, rb_h = 460, 30
    screen_w = rb.winfo_screenwidth()
    rb.geometry("{}x{}+{}+{}".format(rb_w, rb_h, (screen_w - rb_w) // 2, 8))

    # 内框
    inner = tkinter.Frame(rb, bg=C["bgc"], bd=0)
    inner.pack(fill="both", expand=True, padx=1, pady=1)

    # ── 录制指示灯 ──
    rb_dot = tkinter.Label(inner, text="●", font=("Segoe UI Symbol", 12),
        fg=C["dg"], bg=C["bgc"])
    rb_dot.pack(side="left", padx=(8, 2))

    # ── 状态文字 ──
    rb_status = tkinter.Label(inner, text="录制中", font=FONT_BUTTON,
        fg=C["dg"], bg=C["bgc"])
    rb_status.pack(side="left", padx=(0, 8))

    # ── 动作计数 ──
    rb_count = tkinter.Label(inner, text="动作: 0",
        font=("Consolas", 9, "bold"), fg=C["fgb"], bg=C["bgc"])
    rb_count.pack(side="left", padx=(0, 8))

    # ── 录制模式 ──
    mode_text = "相对" if getattr(state, 'RECORDING_MODE', 'absolute') == 'relative' else "绝对"
    rb_mode = tkinter.Label(inner, text="坐标: {}".format(mode_text),
        font=FONT_SMALL, fg=C["fgm"], bg=C["bgc"])
    rb_mode.pack(side="left", padx=(0, 4))

    # ── 分隔符 ──
    tkinter.Frame(inner, bg=C["bd"], width=1).pack(side="left", fill="y", padx=6, pady=4)

    # ── 暂停按钮 ──
    rb_pause = tkinter.Label(inner, text="⏸ 暂停", font=FONT_BUTTON,
        fg="white", bg=C["wn"], padx=8, pady=1, cursor="hand2",
        activebackground=_darken(C["wn"]), activeforeground="white",
        relief="raised", bd=1)
    rb_pause.pack(side="left", padx=2)
    rb_pause.bind("<Button-1>", lambda e: _toggle_recording_pause())

    # ── 停止按钮 ──
    rb_stop = tkinter.Label(inner, text="■ 停止录制", font=FONT_BUTTON,
        fg="white", bg=C["dg"], padx=8, pady=1, cursor="hand2",
        activebackground=_darken(C["dg"]), activeforeground="white",
        relief="raised", bd=1)
    rb_stop.pack(side="left", padx=2)
    rb_stop.bind("<Button-1>", lambda e: _stop_recording_from_bar())

    # ── 拖拽支持 ──
    _drag_data = {"x": 0, "y": 0}

    def _drag_start(event):
        _drag_data["x"] = event.x_root - rb.winfo_x()
        _drag_data["y"] = event.y_root - rb.winfo_y()

    def _drag_move(event):
        rb.geometry("+{}+{}".format(
            event.x_root - _drag_data["x"],
            event.y_root - _drag_data["y"]))

    for w in (rb, inner, rb_status, rb_count, rb_mode):
        w.bind("<Button-1>", _drag_start)
        w.bind("<B1-Motion>", _drag_move)

    # ── 右键菜单 ──
    def _rb_right_menu(event):
        menu = tkinter.Menu(rb, tearoff=0, font=FONT_BODY)
        menu.add_command(label="停止录制并恢复窗口", command=_stop_recording_from_bar)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
    rb.bind("<Button-3>", _rb_right_menu)
    inner.bind("<Button-3>", _rb_right_menu)

    # 存储子控件引用
    rb._widgets = {
        "dot": rb_dot, "status": rb_status,
        "count": rb_count, "mode": rb_mode,
        "pause": rb_pause, "stop": rb_stop,
        "inner": inner, "frame": rb,
    }
    _recording_bar = rb

    # 启动轮询更新动作计数
    _poll_recording_action_count()


def _destroy_recording_bar():
    """销毁 Recording Bar 并停止轮询。"""
    global _recording_bar, _rec_bar_poll_id
    if _rec_bar_poll_id is not None:
        try:
            root.after_cancel(_rec_bar_poll_id)
        except Exception:
            pass
        _rec_bar_poll_id = None
    if _recording_bar is not None:
        try:
            _recording_bar.destroy()
        except Exception:
            pass
        _recording_bar = None


def _poll_recording_action_count():
    """轮询更新 Recording Bar 上的动作计数（每 200ms）。"""
    global _recording_bar, _rec_bar_poll_id
    if _recording_bar is None:
        _rec_bar_poll_id = None
        return
    try:
        if _recording_bar.winfo_exists():
            count = len(state.recorded_actions) if hasattr(state, 'recorded_actions') else 0
            w = _recording_bar._widgets
            w["count"].configure(text="动作: {}".format(count))
            # 暂停状态更新
            paused = not state.pause_event.is_set() if hasattr(state, 'pause_event') else False
            if paused:
                w["pause"].configure(text="▶ 继续", bg=C["ac"],
                    activebackground=_darken(C["ac"]))
            else:
                w["pause"].configure(text="⏸ 暂停", bg=C["wn"],
                    activebackground=_darken(C["wn"]))
    except Exception:
        pass
    _rec_bar_poll_id = root.after(200, _poll_recording_action_count)


def _toggle_recording_pause():
    """暂停/恢复录制（暂停时停止捕获新动作）。"""
    if not state.recording:
        return
    if state.pause_event.is_set():
        state.pause_event.clear()
        log1("录制已暂停")
    else:
        state.pause_event.set()
        log1("录制已恢复")


def _stop_recording_from_bar():
    """从 Recording Bar 停止录制，恢复主窗口到前台。"""
    global _recording_bar
    # 发信号停止录制线程
    state.record_stop = True
    log1("正在停止录制...")
    # 销毁 Recording Bar
    _destroy_recording_bar()
    # 恢复主窗口
    _restore_from_recording()
    # 更新按钮文字
    try:
        btn_record.config(text="● 录制", bg=C["dg"])
    except Exception:
        pass


def _restore_from_recording():
    """录制结束后恢复主窗口到前台。"""
    # 如果已经在 Mini Bar 折叠模式，先销毁 Mini Bar
    _destroy_mini_bar()
    # 恢复主窗口
    try:
        root.deiconify()
        root.lift()
        root.focus_force()
        if root.state() == "iconic":
            root.state("normal")
    except Exception:
        pass
    state.folded = False
    try:
        fold_btn.config(text="⊟", fg=C["fgm"])
    except Exception:
        pass


def _sync_mini_bar_status():
    """同步 Mini Bar 上的状态指示、运行信息、按钮颜色到当前主题和运行状态。"""
    if _mini_bar is None:
        return
    w = _mini_bar._widgets
    try:
        # 更新背景色（主题切换时）
        w["inner"].configure(bg=C["bgc"])
        w["expand"].configure(bg=C["bgc"], fg=C["fgb"], activebackground=C["acl"])
        w["status"].configure(bg=C["bgc"])
        w["info"].configure(bg=C["bgc"])
        if "icon" in w:
            w["icon"].configure(bg=C["bgc"])

        # ── 状态文字 ──
        if state.recording:
            w["status"].configure(text="● 录制中", fg=C["dg"])
        elif state.running:
            if not state.pause_event.is_set():
                w["status"].configure(text="⏸ 已暂停", fg=C["wn"])
            else:
                w["status"].configure(text="● 运行中", fg=C["sc"])
        else:
            w["status"].configure(text="● 就绪", fg=C["fgm"])

        # ── 运行信息（循环/行数/耗时）：不运行时折叠隐藏，并收缩窗口宽度 ──
        _idle_w, _run_w = 430, 560
        if state.running and state.exec_state.get("total_rows", 0) > 0:
            lp = state.exec_state["loop"]
            tl = state.exec_state["total_loops"]
            rw = state.exec_state["row"]
            tr = state.exec_state["total_rows"]
            el = time.time() - state.exec_state["start_time"]
            if el >= 3600:
                el_str = "{:02d}:{:02d}:{:02d}".format(
                    int(el // 3600), int((el % 3600) // 60), int(el % 60))
            else:
                el_str = "{:02d}:{:02d}".format(
                    int(el // 60), int(el % 60))
            w["info"].configure(
                text="循环{}/{}  行{}/{}  {}".format(
                    lp, "∞" if tl > 99999 else tl, rw, tr, el_str),
                fg=C["fgm"])
            if not w["info"].winfo_ismapped():
                w["info"].pack(side="left", padx=(0, 4), before=w["sep1"])
            # 扩展窗口宽度以容纳运行信息
            if _mini_bar.winfo_width() < _run_w - 10:
                _mini_bar.geometry("{}x{}".format(_run_w, _mini_bar.winfo_height()))
        else:
            if w["info"].winfo_ismapped():
                w["info"].pack_forget()
            # 收缩窗口宽度到紧凑模式
            if _mini_bar.winfo_width() > _idle_w + 10:
                _mini_bar.geometry("{}x{}".format(_idle_w, _mini_bar.winfo_height()))

        # ── 暂停按钮动态文本 ──
        if state.running and not state.pause_event.is_set():
            # 已暂停 → 显示"继续"
            w["pause"].configure(text="▶ 继续", bg=C["ac"],
                activebackground=_darken(C["ac"]))
        else:
            w["pause"].configure(text="⏸ 暂停", bg=C["wn"],
                activebackground=_darken(C["wn"]))

        # ── 按钮颜色 ──
        w["stop"].configure(bg=C["dg"], activebackground=_darken(C["dg"]))
        w["run"].configure(bg=C["sc"], activebackground=_darken(C["sc"]))
        w["step"].configure(bg=C["ac"], activebackground=_darken(C["ac"]))
    except Exception:
        pass  # Mini Bar 可能已被销毁


def _toggle_fold():
    """切换折叠/展开状态。"""
    if state.folded:
        # 展开：销毁 Mini Bar，恢复主窗口
        _destroy_mini_bar()
        root.deiconify()
        root.lift()
        root.focus_force()
        state.folded = False
        fold_btn.config(text="⊟", fg=C["fgm"])
        log1("已展开完整窗口")
    else:
        # 折叠：隐藏主窗口，显示 Mini Bar
        root.withdraw()
        _create_mini_bar()
        state.folded = True
        fold_btn.config(text="⊞", fg=C["ac"])
        log1("已折叠为 Mini Bar（点击 □ 展开恢复）")
        from utils import show_toast
        show_toast(root, "已折叠为 Mini Bar", "info", 2000)


# ======================================================================
# SECTION: Path Configuration
# ======================================================================
if getattr(sys, "frozen", False):
    APP_ROOT = os.path.dirname(sys.executable)
    RES_DIR = os.path.join(APP_ROOT, "res")
    if not os.path.exists(APP_ROOT): os.makedirs(APP_ROOT)
    try:
        bundle_res = os.path.join(sys._MEIPASS, "res")
        if os.path.isdir(bundle_res) and not os.path.isdir(RES_DIR):
            shutil.copytree(bundle_res, RES_DIR)
    except Exception: pass
else:
    APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    RES_DIR = os.path.join(APP_ROOT, "res")

LOG_DIR = os.path.join(APP_ROOT, "logs")
SCREENSHOT_DIR = os.path.join(APP_ROOT, "screenshots")
os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(SCREENSHOT_DIR, exist_ok=True)

# ======================================================================
# SECTION: Core Logic
# ======================================================================


def _bind_scroll_recursive(parent, handler):
    """递归绑定 MouseWheel 到 parent 及其所有后代 widget，确保滚轮在按钮等子控件上也生效"""
    parent.bind("<MouseWheel>", handler)
    for child in parent.winfo_children():
        _bind_scroll_recursive(child, handler)



# ======================================================================


# 帮助对话框已移至 dialogs.show_help_dialog (依赖注入: init_ctx)

def capture_mouse_position():
    """Poll mouse coordinates every 2 seconds and log them. Toggle on/off."""
    if getattr(capture_mouse_position, '_active', False):
        state.quit3 = True; capture_mouse_position._active = False
        log1("坐标获取已停止"); return
    state.quit3 = False
    capture_mouse_position._active = True
    rz.delete(0.0, "end")
    # 恢复主窗口到前台
    try:
        root.deiconify()
        root.lift()
        root.focus_force()
    except Exception:
        pass
    log1("坐标获取已启动 — 每2秒更新一次，再次点击按钮停止")

    def _poll():
        while not state.quit3:
            pos = _get_pa().position()
            log1("当前鼠标坐标是: {}".format(str(pos)))
            for _ in range(20):
                if state.quit3: break
                time.sleep(0.1)
            if not state.quit3:
                log1("--- 2秒后再次定位 ---")
        capture_mouse_position._active = False

    t = threading.Thread(target=_poll); t.daemon = True; t.start()

def stop_execution():
    """Stop script execution and recording gracefully."""
    state.quit3=True; state.quit2=True; state.running=False
    state.pause_event.set(); state.exec_state["loop"]=0; state.exec_state["row"]=0
    log1("停止")

def main_run():
    """Validate script selection and launch execution in background thread."""
    if not state.has_script: messagebox.showwarning("错误","没有选择要执行的脚本文件"); return
    if state.running: log1("点击停止按钮再开始运行"); return
    state.quit2=False; state.quit3=True; state.pause_event.set()
    engine.retry = state.RETRY_MAX; engine.retry_interval = state.RETRY_INTERVAL
    loop_val = loop_count_var.get()
    tn = 9999800001 if loop_val == "无限循环" else int(loop_val)
    log1("启动任务 (最大重试:{}次, 间隔:{}s)".format(state.RETRY_MAX,state.RETRY_INTERVAL))
    
    # ── 执行增强: 窗口绑定 —   BoundWindow ──
    if hasattr(state, 'BOUND_WINDOW_TITLE') and state.BOUND_WINDOW_TITLE:
        try:
            title = state.BOUND_WINDOW_TITLE.strip()
            if title:
                log1("绑定窗口: 正在激活 '{}' ...".format(title))
                engine._activate_window_by_title(title)
        except Exception as e:
            log1("绑定窗口激活失败: {}".format(e), "warning")
    
    t=threading.Thread(target=autorun, args=(tn,)); t.daemon=True; t.start()

def autorun(tn):
    state.exec_state["total_loops"]=tn; state.exec_state["start_time"]=time.time()
    state.exec_state["loop"]=0; state.exec_state["row"]=0
    # Use xlrd to load the script for auto-run
    import xlrd
    
    # ── 执行增强: 最大执行时间限制 (  MaxExecutionMinutes) ──
    max_minutes = getattr(state, 'MAX_EXECUTION_MINUTES', 0)
    timeout_seconds = max_minutes * 60 if max_minutes > 0 else 0

    while state.exec_state["loop"]<tn:
        if state.quit2: break
        
        # ── 超时检测 ──
        if timeout_seconds > 0:
            elapsed = time.time() - state.exec_state["start_time"]
            if elapsed >= timeout_seconds:
                log1("执行超时: 已达到最大执行时间 {} 分钟，自动停止".format(max_minutes), "warning")
                break
        
        state.exec_state["loop"]+=1
        log1("开始第{}次循环".format(state.exec_state["loop"]))

        try:
            wb = xlrd.open_workbook(state.filename)
            s1 = wb.sheet_by_index(0)
            rows_data = []
            # Skip header rows (usually first 2 rows in ACRPA format)
            for row_idx in range(2, s1.nrows):
                row = s1.row_values(row_idx)
                if not row or not row[0]: continue
                cmd_type = str(row[0]) if row[0] else ""
                args = [str(cell) if cell else "" for cell in row[1:10]]
                while len(args) < 9: args.append("")
                rows_data.append(ScriptData(cmd_type, args[:9]))

            state.exec_state["total_rows"] = len(rows_data)
            wb.release_resources()
        except Exception as e:
            log1("自动运行加载失败: {}".format(e), "error")
            break

        # Use the new execute_script method that supports conditions and loops
        state.running=True
        engine.execute_script(rows_data, state.script_dir)

        # 失败语义: stop_on_error=True 时脚本已在失败行停止，此处同步终止外层循环，
        # 避免带着同一个失败反复重跑整段脚本 (无限循环模式下尤其致命)
        if getattr(engine, '_script_failed', False) and getattr(state, 'STOP_ON_ERROR', True):
            log1("脚本执行失败，按 stop_on_error 设置终止任务", "error")
            break

        state.exec_state["elapsed"]=time.time()-state.exec_state["start_time"]
    state.running=False; state.exec_state["row"]=0; log1("任务终止")


# ======================================================================
# SECTION: GUI
# ======================================================================

C = _colors()  # uses utils._colors (imported above)
FONT_TITLE = ("Microsoft YaHei UI",10,"bold"); FONT_BODY = ("Microsoft YaHei UI",9)
FONT_LOG = ("Consolas",9); FONT_SMALL = ("Microsoft YaHei UI",8); FONT_BUTTON = ("Microsoft YaHei UI",9,"bold")

# ── Windows 任务栏图标：必须在创建任何窗口之前设置 AppUserModelID ──
try:
    import ctypes
    from version_info import VERSION
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ACRPA.RPA.v{}".format(VERSION))
except Exception:
    pass

# === Icon helper for child windows ──
def _set_window_icon(window):
    """Set the ACRPA icon on a child Toplevel window (including taskbar icon).
    
    Uses WM_SETICON to properly set both small and large icons.
    """
    ico_path = os.path.join(RES_DIR, "automation.ico")
    if not os.path.exists(ico_path):
        ico_path = os.path.join(APP_ROOT, "automation.ico")
    if not os.path.exists(ico_path):
        return
    
    try:
        import ctypes.wintypes
        
        # tkinter 标准图标设置（标题栏左上角）
        window.iconbitmap(ico_path)
        
        # WM_SETICON: 设置任务栏图标（关键！）
        WM_SETICON = 0x0080
        ICON_SMALL = 0
        ICON_BIG = 1
        IMAGE_ICON = 1
        LR_LOADFROMFILE = 0x00000010
        
        hicon = ctypes.windll.user32.LoadImageW(
            None, ico_path, IMAGE_ICON, 0, 0, LR_LOADFROMFILE)
        
        if hicon:
            # 获取真实 HWND
            hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
            if not hwnd:
                hwnd = window.winfo_id()
            
            # 同时设置小图标和大图标
            ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, hicon)
            ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, hicon)
    except Exception:
        pass

root = tkinter.Tk()
root.title("A/C RPA")
root.geometry("500x625+400+80"); root.minsize(450,550)
root.configure(bg=C["bg"]); root.resizable(width=True,height=True)

# ── 全局禁止 Combobox / Spinbox 滚轮修改数值 ──
def _block_input_wheel(event):
    """拦截滚轮事件，防止误触修改 Combobox/Spinbox 数值"""
    return "break"

root.bind_class("TCombobox", "<MouseWheel>", _block_input_wheel)
root.bind_class("TSpinbox", "<MouseWheel>", _block_input_wheel)

# ── 设置窗口图标和任务栏图标 ──
ico_path = os.path.join(RES_DIR,"automation.ico")
if not os.path.exists(ico_path): ico_path = os.path.join(APP_ROOT,"automation.ico")
if os.path.exists(ico_path):
    # tkinter 标准图标设置（标题栏 + Alt+Tab）
    root.iconbitmap(ico_path)
    
    # WM_SETICON: 直接设置任务栏图标（关键！iconbitmap 不会设置任务栏图标）
    try:
        import ctypes.wintypes
        
        # Windows API 常量
        WM_SETICON = 0x0080
        ICON_SMALL = 0
        ICON_BIG = 1
        IMAGE_ICON = 1
        LR_LOADFROMFILE = 0x00000010
        
        # 加载 .ico 文件为 HICON 句柄
        hicon = ctypes.windll.user32.LoadImageW(
            None,           # hInst (NULL for loading from file)
            ico_path,       # 图标文件路径
            IMAGE_ICON,     # uType: IMAGE_ICON
            0,              # cx: 0 = 使用默认宽度
            0,              # cy: 0 = 使用默认高度
            LR_LOADFROMFILE # fuLoad: 从文件加载
        )
        
        if hicon:
            # 获取真实的窗口句柄（tkinter 窗口的父窗口）
            hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
            
            # 同时设置小图标和大图标
            # ICON_BIG 用于任务栏和 Alt+Tab
            # ICON_SMALL 用于窗口标题栏左上角
            ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, hicon)
            ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, hicon)
            
            log1("任务栏图标已设置 (WM_SETICON)")
        else:
            log1("警告: 无法加载图标文件")
    except Exception as e:
        log1(f"设置任务栏图标失败: {e}")

# 设置任务栏窗口标题（与 AppUserModelID 配合）
try:
    hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
    ctypes.windll.user32.SetWindowTextW(hwnd, "A/C RPA")
except Exception:
    pass

# ttk Style
style = ttk.Style(); style.theme_use("clam")

apply_theme(root, style)

def window_close():
    """Handle window close event with app protection.

    Behavior when MINIMIZE_TO_TRAY is enabled:
      - Clicking × minimizes to system tray (with tray icon + right-click menu)
      - Left-double-click tray icon → restore window
      - Right-click tray icon → menu: 显示主窗口 / 退出
    
    Protection levels (when MINIMIZE_TO_TRAY is disabled):
    - Script running → BLOCK close (must stop first)
    - Recording active → BLOCK close (must stop first)
    - Scheduler enabled → WARNING with confirm (user can choose to close)
    - Otherwise → close normally
    """
    # === Minimize to system tray (with real tray icon)
    if state.MINIMIZE_TO_TRAY:
        _ensure_tray()
        if _tray:
            _tray.create()
            # 更新托盘提示文字
            try:
                if state.running:
                    _tray.update_tip("A/C RPA — 脚本运行中")
                elif state.recording:
                    _tray.update_tip("A/C RPA — 录制中")
                else:
                    _tray.update_tip("A/C RPA — 空闲")
            except Exception:
                pass
        root.withdraw()
        log1("已最小化到系统托盘（双击托盘图标恢复）")
        from utils import show_toast
        show_toast(root, "ACRPA 已最小化到托盘，双击图标恢复", "info", 3000)
        return

    # === Check if app protection is disabled
    if not state.APP_PROTECT:
        return _do_close()
    
    # === Level 1: Script actively running (BLOCK) ──
    if state.running:
        messagebox.showinfo(
            "应用保护 - 无法关闭",
            "脚本正在运行中，无法关闭程序。\n\n"
            "请先点击「停止」按钮结束脚本运行后再关闭窗口。",
            icon=messagebox.WARNING
        )
        return
    
    # === Level 2: Recording active (BLOCK) ──
    if state.recording:
        messagebox.showinfo(
            "应用保护 - 无法关闭",
            "正在录制操作中，无法关闭程序。\n\n"
            "请先点击「停止录制」按钮结束录制后再关闭窗口。",
            icon=messagebox.WARNING
        )
        return
    
    # === Level 3: Scheduler enabled (WARNING) ──
    if state.SCHED_ENABLED:
        result = messagebox.askyesno(
            "应用保护",
            "检测到定时任务已启用（下次执行: {}）。\n\n"
            "确定要退出程序吗？\n退出后定时任务将停止执行。".format(
                state.SCHED_NEXT_RUN if state.SCHED_NEXT_RUN else "未计算"),
            icon=messagebox.WARNING
        )
        if not result:
            return  # User cancelled, don't close
    
    # === No protection needed or user confirmed ──
    _do_close()

def _do_close():
    """Perform the actual close sequence."""
    state._closing = True
    state.save_config()
    
    # Clean up tray icon, Mini Bar, and Recording Bar
    _destroy_tray()
    _destroy_mini_bar()
    _destroy_recording_bar()
    
    # Unbind mouse wheel to prevent memory leak
    try:
        root.unbind_all("<MouseWheel>")
    except Exception:
        pass
    
    # Stop scheduler if running
    try:
        import scheduler as sched
        sched.stop_scheduler()
    except Exception:
        pass
    
    if state.quit2:
        root.destroy()
    else:
        state.quit2 = True
        state.pause_event.set()
        root.destroy()

root.protocol("WM_DELETE_WINDOW", window_close)


def _exit_for_update():
    """自更新专用退出路径: 走统一清理后结束进程。

    与 window_close 的差别是不弹确认、也不最小化到托盘 —— 主程序必须真正退出,
    否则更新助手会一直等待主程序文件解锁, 替换环节会挂到超时。
    """
    state.quit2 = True
    state._closing = True
    try:
        _do_close()
    except Exception:
        try:
            root.destroy()
        except Exception:
            pass

# === Layout - compact spacing ──
root.columnconfigure(0,weight=1)
root.rowconfigure(0,weight=0); root.rowconfigure(1,weight=1); root.rowconfigure(2,weight=0)
PAD = {"padx":6,"pady":3}; PI = {"padx":6,"pady":2}

# Title bar - compact design
title_bar = tkinter.Frame(root,bg=C["bg"])
title_bar.grid(row=0,column=0,sticky="ew",padx=10,pady=(6,2))
title_bar.columnconfigure(0,weight=1)

title_lbl = tkinter.Label(title_bar,text="A/C RPA 自动化工作流",
    font=FONT_TITLE,fg=C["fgt"],bg=C["bg"])
title_lbl.grid(row=0,column=0,sticky="w")

# Dark mode toggle - compact
dark_frame = tkinter.Frame(title_bar,bg=C["bg"])
dark_frame.grid(row=0,column=1,sticky="e")
status_dot = tkinter.Label(dark_frame,text="● 就绪",font=FONT_SMALL,fg=C["fgm"],bg=C["bg"])
status_dot.pack(side="left",padx=(0,6))
pin_btn = tkinter.Label(dark_frame,text="△",font=("Segoe UI Symbol",9),
    fg=C["fgm"],bg=C["bg"],cursor="hand2",padx=2)
pin_btn.pack(side="left",padx=(0,2))
pin_pinned = False
def toggle_pin():
    global pin_pinned
    pin_pinned = not pin_pinned
    root.attributes("-topmost", pin_pinned)
    pin_btn.config(text="▲" if pin_pinned else "△",
        fg=C["ac"] if pin_pinned else C["fgm"])
pin_btn.bind("<Button-1>", lambda e: toggle_pin())

# 置顶按钮 tooltip (动态提示: 根据置顶状态显示不同文本)
def _show_pin_tip(event):
    global _tip_win
    if _tip_win: _tip_win.destroy()
    _tip_win = tkinter.Toplevel(root)
    _tip_win.wm_overrideredirect(True)
    _tip_win.wm_geometry("+{}+{}".format(event.x_root + 12, event.y_root - 8))
    tkinter.Label(_tip_win, text="取消置顶" if pin_pinned else "置顶窗口",
        font=FONT_SMALL, bg=C["bgc"], fg=C["fgb"], relief="solid", bd=1, padx=6, pady=2).pack()
pin_btn.bind("<Enter>", _show_pin_tip)

# 折叠按钮 — 将主窗口折叠为 Mini Bar
fold_btn = tkinter.Label(dark_frame,text="⊟",font=("Segoe UI Symbol",9),
    fg=C["fgm"],bg=C["bg"],cursor="hand2",padx=2)
fold_btn.pack(side="left",padx=(0,2))
fold_btn.bind("<Button-1>", lambda e: _toggle_fold())
# Tooltip for fold button
def _show_fold_tip(event):
    global _tip_win
    if _tip_win: _tip_win.destroy()
    _tip_win = tkinter.Toplevel(root)
    _tip_win.wm_overrideredirect(True)
    _tip_win.wm_geometry("+{}+{}".format(event.x_root+12, event.y_root-8))
    tkinter.Label(_tip_win, text="折叠为 Mini Bar" if not state.folded else "展开完整窗口",
        font=FONT_SMALL, bg=C["bgc"], fg=C["fgb"], relief="solid", bd=1, padx=6, pady=2).pack()
fold_btn.bind("<Enter>", _show_fold_tip)

dark_btn = tkinter.Label(dark_frame,text="◑" if state.DARK_MODE else "◐",
    font=("Segoe UI Symbol",12),fg=C["fgm"],bg=C["bg"],cursor="hand2")
dark_btn.pack(side="left")

# Accent separator below title bar
tkinter.Frame(title_bar, bg=C["ac"], height=2).grid(row=1, column=0, columnspan=2, sticky="ew", pady=(5,0))

# Simple tooltip for dark mode toggle
_tip_win = None
def _show_tip(event):
    global _tip_win
    if _tip_win: _tip_win.destroy()
    _tip_win = tkinter.Toplevel(root)
    _tip_win.wm_overrideredirect(True)
    _tip_win.wm_geometry("+{}+{}".format(event.x_root+12, event.y_root-8))
    tkinter.Label(_tip_win, text="切换暗色模式" if not state.DARK_MODE else "切换亮色模式",
        font=FONT_SMALL, bg=C["bgc"], fg=C["fgb"], relief="solid", bd=1, padx=6, pady=2).pack()
def _hide_tip(event):
    global _tip_win
    if _tip_win: _tip_win.destroy(); _tip_win = None
dark_btn.bind("<Enter>", _show_tip)
dark_btn.bind("<Leave>", _hide_tip)
fold_btn.bind("<Leave>", _hide_tip)
pin_btn.bind("<Leave>", _hide_tip)

# 设置按钮 — 打开独立设置窗口 (原设置 Tab 已分离，主界面入口)
settings_btn = tkinter.Label(dark_frame, text="⚙", font=("Segoe UI Symbol", 11),
    fg=C["fgm"], bg=C["bg"], cursor="hand2", padx=2)
settings_btn.pack(side="left", padx=(0, 2))
settings_btn.bind("<Button-1>", lambda e: settings_window.open_settings_window())
def _show_settings_tip(event):
    global _tip_win
    if _tip_win: _tip_win.destroy()
    _tip_win = tkinter.Toplevel(root)
    _tip_win.wm_overrideredirect(True)
    _tip_win.wm_geometry("+{}+{}".format(event.x_root + 12, event.y_root - 8))
    tkinter.Label(_tip_win, text="打开设置窗口",
        font=FONT_SMALL, bg=C["bgc"], fg=C["fgb"], relief="solid", bd=1, padx=6, pady=2).pack()
settings_btn.bind("<Enter>", _show_settings_tip)
settings_btn.bind("<Leave>", _hide_tip)

def _refresh_theme():
    global C
    C = _colors()
    apply_theme(root, style)

    def _walk(p):
        for w in p.winfo_children():
            try:
                cls = w.winfo_class()
                if cls in ("Frame", "TFrame"):
                    # Card = has non-default highlightthickness + highlightbackground
                    ht = int(w.cget("highlightthickness"))
                    if ht > 0:
                        w.configure(bg=C["bgc"], highlightbackground=C["bd"])
                    else:
                        w.configure(bg=C["bg"])
                elif cls in ("Label", "TLabel"):
                    # Labels on main bg have same bg as their parent
                    parent_bg = str(p.cget("bg"))
                    fg_color = C["fgm"]
                    # Preserve accent color for important labels
                    txt = w.cget("text")
                    if txt and ("●" in txt or "运行" in txt or "就绪" in txt):
                        fg_color = C["ac"] if "就绪" in txt else (C["sc"] if "运行" in txt else C["fgm"])
                    w.configure(bg=parent_bg, fg=fg_color)
                elif cls == "Text":
                    w.configure(bg=C["logbg"], fg=C["logfg"],
                        insertbackground=C["fgt"],
                        selectbackground=C["acl"], selectforeground=C["fgt"])
                elif cls == "Scrollbar":
                    w.configure(bg=C["bd"], activebackground=C["fgm"], troughcolor=C["logbg"])
                elif cls == "Button":
                    # Update button colors based on their current bg
                    cur_bg = w.cget("bg")
                    # Keep semantic colors (green/red/yellow/blue) but update neutral buttons
                    # Update semantic buttons to current theme colors
                    _old_map = [("old_sc", "sc"), ("old_dg", "dg"),
                                ("old_wn", "wn"), ("old_ac", "ac")]
                    _updated = False
                    for old_key, new_key in _old_map:
                        if cur_bg == C.get(old_key):
                            w.configure(bg=C[new_key])
                            _updated = True
                            break
                    if not _updated and cur_bg not in (C["sc"], C["dg"], C["wn"], C["ac"]):
                        # Neutral buttons - update to new theme
                        w.configure(bg=C["bgc"], fg=C["fgb"],
                            activebackground=C["acl"],
                            highlightbackground=C["bd"])
                elif cls == "Entry":
                    w.configure(bg=C["ebg"], fg=C["fgb"],
                        insertbackground=C["fgt"])
                elif cls == "Combobox":
                    w.configure(background=C["bgc"], fieldbackground=C["bgc"],
                        foreground=C["fgb"])
                elif cls == "Canvas":
                    # 工作流流程图画布用 flowbg, 其余画布用主背景 (主题切换后重着色)
                    try:
                        if str(w) == str(wf_flow_canvas):
                            w.configure(bg=C["flowbg"])
                        else:
                            w.configure(bg=C["bg"])
                    except Exception:
                        w.configure(bg=C["bg"])
                elif cls == "Listbox":
                    w.configure(bg=C["ebg"], fg=C["fgb"],
                        selectbackground=C["ac"], selectforeground="white")
                elif cls == "Spinbox":
                    w.configure(bg=C["ebg"], fg=C["fgb"],
                        buttonbackground=C["bgc"], insertbackground=C["fgt"])
            except Exception:
                pass
            _walk(w)

    _walk(root)

    # 强制刷新所有 widget 以确保暗色模式完整
    try:
        root.update_idletasks()
        notebook.update()
    except Exception:
        pass

    # Fix specific overrides
    root.configure(bg=C["bg"])
    title_lbl.configure(bg=C["bg"], fg=C["fgt"])
    status_dot.configure(bg=C["bg"])
    pin_btn.configure(bg=C["bg"])
    fold_btn.configure(bg=C["bg"])
    dark_btn.configure(bg=C["bg"])
    
    # 同步 Mini Bar 主题（如果折叠中）
    _sync_mini_bar_status()
    
    # Update log area
    rz.configure(bg=C["logbg"], fg=C["logfg"],
        selectbackground=C["acl"], selectforeground=C["fgt"])
    scroll.configure(bg=C["bd"], activebackground=C["fgm"], troughcolor=C["logbg"])
    
    # Update status bar
    status_bar.configure(bg=C["bg"])
    status_text.configure(bg=C["bg"])
    
    # Update progress bar container - force ttk style refresh
    progress_bar.configure(style="Success.Horizontal.TProgressbar")
    
    # Update all tab frames
    for widget in [tab_edit, tab_exec, tab_workflow]:
        widget.configure(bg=C["bg"])
    
    # 同步设置窗口颜色 (独立窗口使用自己的 C 引用)
    settings_window.C = C
    settings_window.refresh_theme()
    # Update all semantic colored buttons to use current theme colors
    # This ensures buttons maintain their semantic meaning across theme changes
    for widget in [btn_run, btn_pause, btn_step]:
        try:
            cur_bg = widget.cget("bg")
            if cur_bg == C.get("old_sc") or cur_bg in (C["sc"], C["ok"]):
                widget.configure(bg=C["sc"])
            elif cur_bg == C.get("old_wn") or cur_bg == C["wn"]:
                widget.configure(bg=C["wn"])
            elif cur_bg == C.get("old_ac") or cur_bg in (C["ac"], C["hover"], C["focus"]):
                widget.configure(bg=C["ac"])
        except Exception:
            pass
    
    # Update record button color
    try:
        cur_bg = btn_record.cget("bg")
        if cur_bg == C.get("old_dg") or cur_bg in (C["dg"], C["err"]):
            btn_record.configure(bg=C["dg"])
        elif cur_bg == C.get("old_wn") or cur_bg == C["wn"]:
            btn_record.configure(bg=C["wn"])
    except Exception:
        pass
    
    # 工作流 Tab 主题同步: 刷新步骤树标签颜色 + 重绘流程图使用新主题
    try:
        _wf_refresh_tree()
    except Exception:
        pass
    
    # 重新应用微型进度条样式
    try:
        style.configure("Micro.Horizontal.TProgressbar", thickness=4)
    except Exception:
        pass

    # Save old colors for next toggle
    C["old_sc"] = C["sc"]
    C["old_dg"] = C["dg"]
    C["old_wn"] = C["wn"]
    C["old_ac"] = C["ac"]

def toggle_dark():
    state.DARK_MODE = not state.DARK_MODE
    _refresh_theme()
    dark_btn.config(text="◑" if state.DARK_MODE else "◐")
    state.save_config()

dark_btn.bind("<Button-1>", lambda e: toggle_dark())

# Notebook - compact padding
notebook = ttk.Notebook(root)
notebook.grid(row=1,column=0,sticky="nsew",padx=8,pady=(0,3))

# ======================================================================
# TAB 1: 编辑
# ======================================================================
tab_edit = tkinter.Frame(notebook,bg=C["bg"])
notebook.add(tab_edit,text="  脚本编辑  ")
tab_edit.columnconfigure(0,weight=1)
tab_edit.rowconfigure(0,weight=0); tab_edit.rowconfigure(1,weight=1)


# === Toolbar with horizontal scrolling for 500px width ──
toolbar = tkinter.Frame(tab_edit,bg=C["bg"])
toolbar.grid(row=0,column=0,sticky="ew",padx=3,pady=(3,2))

# Create a canvas with scrollbar for the toolbar buttons
toolbar_canvas = tkinter.Canvas(toolbar, bg=C["bg"], height=28, 
    highlightthickness=0, bd=0)
toolbar_scrollbar = tkinter.Scrollbar(toolbar, orient="horizontal", 
    command=toolbar_canvas.xview, width=6)
toolbar_inner = tkinter.Frame(toolbar_canvas, bg=C["bg"])

toolbar_inner.bind(
    "<Configure>",
    lambda e: toolbar_canvas.configure(scrollregion=toolbar_canvas.bbox("all"))
)

toolbar_canvas.create_window((0, 0), window=toolbar_inner, anchor="w")

toolbar_canvas.configure(xscrollcommand=toolbar_scrollbar.set)

toolbar_canvas.pack(side="left", fill="x", expand=True)
toolbar_scrollbar.pack(side="right", fill="x")

# 鼠标滚轮横向滚动 - handler 定义（递归绑定在 _init_toolbar_buttons() 之后）
def _tb1_on_wheel(event):
    if event.delta > 0:
        toolbar_canvas.xview_scroll(-1, "units")
    else:
        toolbar_canvas.xview_scroll(1, "units")

# Filename label (right-aligned, outside scrollable area)
edit_file_label = tkinter.Label(toolbar,text="",font=FONT_SMALL,
    fg=C["fgm"],bg=C["bg"],anchor="e")
edit_file_label.pack(side="right", padx=(4,3))

# TreeView - compact layout
tree_frame = tkinter.Frame(tab_edit,bg=C["bgc"],
    highlightbackground=C["bd"],highlightthickness=1)
tree_frame.grid(row=1,column=0,sticky="nsew",padx=3,pady=(0,4))
tree_frame.columnconfigure(0,weight=1); tree_frame.rowconfigure(0,weight=1)

state._editor_rows = []
tree = ttk.Treeview(tree_frame,
    columns=("cmd",)+tuple("p{}".format(i) for i in range(1,10)),
    show="tree headings",selectmode="browse")
tree.grid(row=0,column=0,sticky="nsew")
tree.heading("#0",text="#"); tree.column("#0",width=32,minwidth=32,stretch=False)
tree.heading("cmd",text="命令类型"); tree.column("cmd",width=100,minwidth=80)
for i in range(1,10):
    c="p{}".format(i); tree.heading(c,text="参数{}".format(i))
    tree.column(c,width=65,minwidth=50)

tsy=tkinter.Scrollbar(tree_frame,orient="vertical",command=tree.yview,
    width=8,relief="flat",elementborderwidth=0,bg=C["bd"],troughcolor=C["bgc"])
tsx=tkinter.Scrollbar(tree_frame,orient="horizontal",command=tree.xview,
    width=8,relief="flat",elementborderwidth=0,bg=C["bd"],troughcolor=C["bgc"])
tree.configure(yscrollcommand=tsy.set,xscrollcommand=tsx.set)
tsy.grid(row=0,column=1,sticky="ns"); tsx.grid(row=1,column=0,sticky="ew")

# ── 颜色图例条 ──
legend_frame = tkinter.Frame(tab_edit, bg=C["bg"])

# ── 工作流名称 ──
legend_frame.grid(row=2, column=0, sticky="ew", padx=3, pady=(0, 2))
legend_inner = tkinter.Frame(legend_frame, bg=C["bg"])
legend_inner.pack(anchor="w")
_LEGEND_ITEMS = [
    ("找图/区域找图", "#2563EB"), ("点图/区域点图", "#10B981"),
    ("按键/按下/释放", "#8B5CF6"), ("热键", "#EF4444"),
    ("输入/等待", "#F59E0B"), ("坐标", "#6B7280"),
    ("悬停/拖拽", "#F97316"), ("滚轮", "#EC4899"),
    ("截屏", "#0EA5E9"), ("代码", "#DC2626"),
]
for label, color in _LEGEND_ITEMS:
    f = tkinter.Frame(legend_inner, bg=C["bg"])
    f.pack(side="left", padx=(2, 0))
    tkinter.Label(f, text="●", font=("Segoe UI Symbol", 7),
        fg=color, bg=C["bg"]).pack(side="left")
    tkinter.Label(f, text=label, font=("Microsoft YaHei UI", 7),
        fg=C["fgm"], bg=C["bg"]).pack(side="left")

# tree double-click binding is set after _edit_cell is defined below
tree.tag_configure("even", background=C["acl"])
tree.tag_configure("running", background="#FEF3C7")
tree.tag_configure("breakpoint", foreground="#EF4444")

# Breakpoint toggle on #0 column click
def _toggle_breakpoint(event):
    region = tree.identify_region(event.x, event.y)
    if region != "tree": return
    item = tree.identify_row(event.y)
    if not item: return
    row_idx = tree.index(item) + 1
    num_text = "{:02d}".format(row_idx)
    # 检查条件断点
    cond = engine.conditional_breakpoints.get(row_idx, "") if hasattr(engine, 'conditional_breakpoints') else ""
    cond_suffix = " [C:{}]".format(cond[:12]) if cond else ""
    if row_idx in state.breakpoints:
        state.breakpoints.discard(row_idx)
        tree.item(item, text=num_text + cond_suffix)
    else:
        state.breakpoints.add(row_idx)
        tree.item(item, text=num_text + "●" + cond_suffix)
tree.bind("<Button-1>", _toggle_breakpoint, add=True)

# P1 Enhancement: Conditional breakpoint context menu
def _show_conditional_breakpoint_menu(event):
    """Show context menu for setting conditional breakpoints"""
    item = tree.identify_row(event.y)
    if not item:
        return
    
    row_idx = tree.index(item) + 1  # 1-based
    
    # Create context menu
    menu = tkinter.Menu(root, tearoff=0, bg=C["bgc"], fg=C["fgt"],
                       activebackground=C["ac"], activeforeground="white",
                       relief="solid", bd=1)
    
    def set_conditional_bp():
        """Set a conditional breakpoint"""
        cond_win = tkinter.Toplevel(root)
        cond_win.title("设置条件断点 - 行{}".format(row_idx))
        cond_win.geometry("450x180")
        cond_win.resizable(False, False)
        _set_window_icon(cond_win)
        
        tkinter.Label(cond_win, text="条件表达式 (使用 ${var} 引用变量)", 
            font=FONT_BODY, bg=C["bgc"]).pack(pady=(10, 5))
        
        # Example conditions
        examples = ["${count} > 10", "${found} == True", "${x} >= 100 and ${y} <= 200"]
        example_frame = tkinter.Frame(cond_win, bg=C["bgc"])
        example_frame.pack(fill="x", padx=10, pady=2)
        tkinter.Label(example_frame, text="示例:", font=FONT_SMALL, 
            fg=C["fgm"], bg=C["bgc"]).pack(anchor="w")
        for ex in examples:
            tkinter.Label(example_frame, text="• " + ex, font=("Consolas", 8), 
                fg=C["ac"], bg=C["bgc"]).pack(anchor="w")
        
        cond_entry = tkinter.Entry(cond_win, font=("Consolas", 10), width=50)
        cond_entry.pack(pady=10, padx=10, fill="x")
        
        # Check if there's already a conditional breakpoint
        if hasattr(engine, 'conditional_breakpoints') and row_idx in engine.conditional_breakpoints:
            cond_entry.insert(0, engine.conditional_breakpoints[row_idx])
        
        def confirm_condition():
            cond = cond_entry.get().strip()
            if cond and hasattr(engine, 'set_conditional_breakpoint'):
                engine.set_conditional_breakpoint(row_idx, cond)
                _push_undo()
                state.breakpoints.add(row_idx)
                _editor_sync_to_tree()
            cond_win.destroy()
        
        btn_frame = tkinter.Frame(cond_win, bg=C["bgc"])
        btn_frame.pack(pady=10)
        
        tkinter.Button(btn_frame, text="确定", command=confirm_condition,
            bg=C["ac"], fg="white", font=FONT_BUTTON, width=10).pack(side="left", padx=5)
        tkinter.Button(btn_frame, text="取消", command=cond_win.destroy,
            bg=C["dg"], fg="white", font=FONT_BUTTON, width=10).pack(side="left", padx=5)
    
    def remove_conditional_bp():
        """Remove conditional breakpoint"""
        if hasattr(engine, 'set_conditional_breakpoint'):
            engine.set_conditional_breakpoint(row_idx, "")
            _editor_sync_to_tree()  # 刷新以移除 [C:...] 标记
    
    def _skip_row():
        """Skip this row during execution (debugger enhancement)"""
        state._skip_next_row = True
        state.pause_event.set()
        log1("跳过第{}行".format(row_idx))
        from utils import show_toast; show_toast(root, "跳过第{}行".format(row_idx), "info")

    def _run_to_cursor():
        """Run until this row (debugger enhancement)"""
        state._run_to_row = row_idx
        state.pause_event.set()
        log1("运行到第{}行".format(row_idx))
        from utils import show_toast; show_toast(root, "运行到第{}行".format(row_idx), "info")

    menu.add_command(label="设置条件断点...", command=set_conditional_bp)
    menu.add_command(label="移除条件断点", command=remove_conditional_bp)
    menu.add_separator()
    menu.add_command(label="普通断点", command=lambda: _toggle_breakpoint(event))
    menu.add_separator()
    menu.add_command(label="▶ 运行到此处", command=_run_to_cursor)
    menu.add_command(label="⏭ 跳过此行", command=_skip_row)
    
    # Show timing info if available
    if state.debug_mode and hasattr(state, '_exec_timings') and state._exec_timings:
        if row_idx in state._exec_timings:
            t = state._exec_timings[row_idx]
            menu.add_separator()
            menu.add_command(label="⏱ 耗时: {:.3f}s ({})".format(
                t["elapsed"], "✓" if t["success"] else "✗"),
                command=lambda: None, state="disabled")
    
    try:
        menu.tk_popup(event.x_root, event.y_root)
    finally:
        menu.grab_release()

tree.bind("<Button-3>", _show_conditional_breakpoint_menu)

# Keyboard shortcuts for editor
def _kb_copy(event=None):
    sel = tree.selection()
    if not sel: return
    state._editor_clipboard = []
    for item in sel:
        idx = tree.index(item)
        if idx < len(state._editor_rows):
            sd = state._editor_rows[idx]
            state._editor_clipboard.append(ScriptData(sd.cmd_type, list(sd.args)))
    log1("已复制 {} 行".format(len(state._editor_clipboard)))
    from utils import show_toast; show_toast(root, "已复制 {} 行".format(len(state._editor_clipboard)), "info")
def _kb_paste(event=None):
    if not state._editor_clipboard: return
    _push_undo()
    sel = tree.selection()
    insert_at = tree.index(sel[-1]) + 1 if sel else len(state._editor_rows)
    for sd in state._editor_clipboard:
        state._editor_rows.insert(insert_at, ScriptData(sd.cmd_type, list(sd.args)))
        insert_at += 1
    _editor_sync_to_tree()
    log1("已粘贴 {} 行".format(len(state._editor_clipboard)))
    from utils import show_toast; show_toast(root, "已粘贴 {} 行".format(len(state._editor_clipboard)), "info")
tree.bind("<Control-c>", _kb_copy)
tree.bind("<Control-v>", _kb_paste)
tree.bind("<Delete>", lambda e: _cmd_del_row())

# ── 撤销/重做系统 ──
def _push_undo():
    """保存当前编辑器状态到撤销栈"""
    import copy
    snapshot = (copy.deepcopy(state._editor_rows), set(state.breakpoints))
    state._undo_stack.append(snapshot)
    if len(state._undo_stack) > state._MAX_UNDO:
        state._undo_stack.pop(0)
    state._redo_stack.clear()
    state._editor_modified = True

def _cmd_undo(event=None):
    """撤销 (Ctrl+Z)"""
    if not state._undo_stack:
        return
    # 保存当前状态到重做栈
    import copy
    redo_snapshot = (copy.deepcopy(state._editor_rows), set(state.breakpoints))
    state._redo_stack.append(redo_snapshot)
    # 恢复上一个状态
    rows_snap, bp_snap = state._undo_stack.pop()
    state._editor_rows = rows_snap
    state.breakpoints = bp_snap
    _editor_sync_to_tree()
    log1("已撤销")

def _cmd_redo(event=None):
    """重做 (Ctrl+Y)"""
    if not state._redo_stack:
        return
    _push_undo()  # 保存当前状态到撤销栈
    rows_snap, bp_snap = state._redo_stack.pop()
    state._editor_rows = rows_snap
    state.breakpoints = bp_snap
    _editor_sync_to_tree()
    log1("已重做")

tree.bind("<Control-z>", _cmd_undo)
tree.bind("<Control-y>", _cmd_redo)
tree.bind("<Control-Z>", _cmd_undo)
tree.bind("<Control-Y>", _cmd_redo)

# ── 增强快捷键 (P2) ──
def _kb_duplicate_row(event=None):
    """Ctrl+D — 复制选中行到其后。"""
    sel = tree.selection()
    if not sel:
        return
    _push_undo()
    for item in sel:
        idx = tree.index(item)
        if idx < len(state._editor_rows):
            sd = state._editor_rows[idx]
            state._editor_rows.insert(idx + 1, ScriptData(sd.cmd_type, list(sd.args)))
    _editor_sync_to_tree()
    log1("已复制 {} 行".format(len(sel)))
    return "break"


def _kb_toggle_comment(event=None):
    """Ctrl+/ — 注释/取消注释选中行 (命令类型加 '#' 前缀，引擎自动跳过)。"""
    sel = tree.selection()
    if not sel:
        return
    _push_undo()
    # 全部已注释 → 取消注释；否则 → 注释
    all_commented = all(
        state._editor_rows[tree.index(item)].cmd_type.startswith("#")
        for item in sel)
    for item in sel:
        sd = state._editor_rows[tree.index(item)]
        if all_commented:
            sd.cmd_type = sd.cmd_type[1:] if sd.cmd_type.startswith("#") else sd.cmd_type
        elif sd.cmd_type and not sd.cmd_type.startswith("#"):
            sd.cmd_type = "#" + sd.cmd_type
    _editor_sync_to_tree()
    log1("已{}注释 {} 行".format("取消" if all_commented else "", len(sel)))
    return "break"


def _kb_toggle_breakpoint(event=None):
    """F9 — 为选中行切换断点。"""
    sel = tree.selection()
    if not sel:
        return
    item = sel[0]
    row_idx = tree.index(item) + 1
    if row_idx in state.breakpoints:
        state.breakpoints.discard(row_idx)
        tree.item(item, text="{:02d}".format(row_idx))
    else:
        state.breakpoints.add(row_idx)
        tree.item(item, text="{:02d}●".format(row_idx))
    return "break"


def _kb_run_script(event=None):
    """F5 — 运行当前脚本。"""
    main_run()
    return "break"


tree.bind("<Control-d>", _kb_duplicate_row)
tree.bind("<Control-D>", _kb_duplicate_row)
tree.bind("<Control-slash>", _kb_toggle_comment)
def _kb_move_up(event=None):
    _cmd_move_up()
    return "break"

def _kb_move_down(event=None):
    _cmd_move_down()
    return "break"

tree.bind("<Alt-Up>", _kb_move_up)
tree.bind("<Alt-Down>", _kb_move_down)
tree.bind("<F9>", _kb_toggle_breakpoint)
tree.bind("<F5>", _kb_run_script)

# Editor row operations
def _cmd_add_row():
    """Add a new empty row to the editor."""
    _push_undo()
    sd = ScriptData("", [""] * 9)
    state._editor_rows.append(sd)
    _editor_sync_to_tree()
    # Scroll to the new row
    tree.see(tree.get_children()[-1])
    log1("已添加新行")

def _cmd_del_row():
    """Delete selected rows from the editor."""
    _push_undo()
    sel = tree.selection()
    if not sel:
        log1("请先选择要删除的行", "warning")
        return
    indices = sorted([tree.index(item) for item in sel], reverse=True)
    for idx in indices:
        if 0 <= idx < len(state._editor_rows):
            state._editor_rows.pop(idx)
    _editor_sync_to_tree()
    log1("已删除 {} 行".format(len(indices)))
    from utils import show_toast; show_toast(root, "已删除 {} 行".format(len(indices)), "warning")

def _cmd_move_up():
    """Move selected rows up."""
    _push_undo()
    sel = tree.selection()
    if not sel: return
    indices = [tree.index(item) for item in sel]
    if not indices or min(indices) == 0: return
    for idx in sorted(indices):
        if idx > 0:
            state._editor_rows[idx-1], state._editor_rows[idx] = \
                state._editor_rows[idx], state._editor_rows[idx-1]
    _editor_sync_to_tree()
    # Reselect moved rows
    for idx in sorted(indices):
        if idx > 0:
            tree.selection_add(tree.get_children()[idx-1])

def _cmd_move_down():
    """Move selected rows down."""
    _push_undo()
    sel = tree.selection()
    if not sel: return
    indices = [tree.index(item) for item in sel]
    if not indices or max(indices) >= len(state._editor_rows) - 1: return
    for idx in sorted(indices, reverse=True):
        if idx < len(state._editor_rows) - 1:
            state._editor_rows[idx], state._editor_rows[idx+1] = \
                state._editor_rows[idx+1], state._editor_rows[idx]
    _editor_sync_to_tree()
    # Reselect moved rows
    for idx in sorted(indices, reverse=True):
        if idx < len(state._editor_rows) - 1:
            tree.selection_add(tree.get_children()[idx+1])


# Editor ops
def _editor_load_xls(fp):
    """加载 Excel 脚本到编辑器（自动清理旧状态）。"""
    _push_undo()
    state._editor_rows = []
    state.breakpoints.clear()
    state._editor_modified = False
    state._undo_stack.clear()
    state._redo_stack.clear()
    tree.delete(*tree.get_children())
    try:
        # Use xlrd for reading Excel files
        import xlrd
        wb = xlrd.open_workbook(fp)
        ws = wb.sheet_by_index(0)
        # Skip first 2 rows (header and description)
        for row_idx in range(2, ws.nrows):
            row = ws.row_values(row_idx)
            if not row or not row[0]:  # Skip empty rows
                continue
            sd = ScriptData.from_xlrd_row_values(row)
            state._editor_rows.append(sd)
            tree.insert("", "end", values=sd.to_tuple())
    except Exception as e:
        log1("加载失败: {}".format(e), "error")
        messagebox.showerror("加载失败", str(e))

# Command-type color map
_CMD_COLORS = {
    "找图":"#2563EB","区域找图":"#2563EB","点图":"#10B981","区域点图":"#10B981",
    "按键":"#8B5CF6","热键":"#EF4444","输入":"#F59E0B","等待":"#6366F1",
    "坐标":"#6B7280","滚轮":"#EC4899","复制":"#14B8A6","粘贴":"#14B8A6",
    "悬停":"#F97316","拖拽":"#F97316","截屏":"#0EA5E9","相移":"#84CC16",
    "按下":"#8B5CF6","释放":"#8B5CF6","代码":"#DC2626",
}


def _editor_sync_to_tree():
    tree.delete(*tree.get_children())
    for i, sd in enumerate(state._editor_rows):
        tag = sd.cmd_type if sd.cmd_type in _CMD_COLORS else ""
        vals = sd.to_tuple()
        # 步骤序号: 01, 02, 03... 断点标记叠加 (条件断点显示 [C:...])
        row_num = i + 1
        num_text = "{:02d}".format(row_num)
        bp_mark = "●" if row_num in state.breakpoints else ""
        cond = engine.conditional_breakpoints.get(row_num, "") if hasattr(engine, 'conditional_breakpoints') else ""
        cond_mark = " [C:{}]".format(cond[:12]) if cond else ""
        item = tree.insert("", "end", text=num_text + bp_mark + cond_mark, values=vals, tags=(tag,))
        if tag and not tree.tag_has(tag):
            tree.tag_configure(tag, foreground=_CMD_COLORS.get(tag, C["fgb"]))
        tags = tree.item(item, "tags")
        if i % 2 == 0:
            tags = tags + ("even",)
        tree.item(item, tags=tags)


def _cmd_clear():
    if messagebox.askyesno("确认","确定要清空所有行吗？"):
        _push_undo()
        state._editor_rows=[]; state.breakpoints.clear(); tree.delete(*tree.get_children())
        from utils import show_toast; show_toast(root, "已清空所有行", "warning")

def _cmd_new():
    if state._editor_modified and state._editor_rows:
        if not messagebox.askyesno("未保存的修改", "当前脚本有未保存的修改，是否放弃？"):
            return
    state._editor_rows=[]; state.breakpoints.clear()
    state._undo_stack.clear(); state._redo_stack.clear()
    state._editor_modified=False
    tree.delete(*tree.get_children())
    state.has_script=False; state.filename=None; state.script_dir=None
    script_name_var.set("新建脚本（未保存）")
    edit_file_label.config(text="未保存")
    log1("已创建新脚本")
    from utils import show_toast; show_toast(root, "已创建新脚本", "success")

def _cmd_open():
    if state._editor_modified and state._editor_rows:
        if not messagebox.askyesno("未保存的修改", "当前脚本有未保存的修改，是否放弃？"):
            return
    fp=filedialog.askopenfilename(title="打开脚本",filetypes=[('Excel 文件','*.xlsx *.xls'), ('xlsx','*.xlsx'), ('xls','*.xls')],initialdir=APP_ROOT)
    if fp:
        state._undo_stack.clear(); state._redo_stack.clear()
        state._editor_modified=False
        state.filename=fp; state.has_script=True; state.script_dir=os.path.dirname(fp)
        script_name_var.set(os.path.basename(fp))
        edit_file_label.config(text=os.path.basename(fp))
        _editor_load_xls(fp)
        _add_recent_script(fp)
        from utils import show_toast; show_toast(root, "已加载: {}".format(os.path.basename(fp)), "success")

def _cmd_save():
    """Save current script to Excel file."""
    if not state._editor_rows:
        messagebox.showwarning("提示", "脚本为空，无法保存")
        return
    
    fp=filedialog.asksaveasfilename(title="保存脚本",defaultextension=".xls",
        filetypes=[('Excel 文件','*.xls')],initialdir=APP_ROOT)
    if fp:
        try:
            # Use xlwt for writing Excel files
            import xlwt
            wb = xlwt.Workbook()
            ws = wb.add_sheet("Sheet1")
            
            # Write header row
            ws.write(0, 0, "命令类型")
            for j in range(1, 10):
                ws.write(0, j, "参数{}".format(j))
            
            # Write description row
            ws.write(1, 0, "（标题行）")
            
            # Write data rows
            for i, sd in enumerate(state._editor_rows, start=2):
                ws.write(i, 0, sd.cmd_type)
                for j in range(9):
                    arg_value = sd.args[j] if j < len(sd.args) else ""
                    ws.write(i, j+1, arg_value)
            
            wb.save(fp)
            state.filename=fp; state.has_script=True
            state._editor_modified=False
            script_name_var.set(os.path.basename(fp))
            edit_file_label.config(text=os.path.basename(fp))
            log1("脚本已保存: {}".format(fp))
            # ── 自动保存版本快照 ──
            try:
                from version_manager import get_version_manager
                vm = get_version_manager()
                vm.save_version(fp, state._editor_rows, "保存")
            except Exception:
                pass
            from utils import show_toast
            show_toast(root, "✓ 脚本已保存 ({} 行)".format(len(state._editor_rows)), "success")
        except ImportError:
            messagebox.showerror("错误","需要安装 xlwt: pip install xlwt")
        except Exception as e:
            messagebox.showerror("保存失败",str(e))
            from utils import show_toast
            show_toast(root, "保存失败: {}".format(e), "error")

def _cmd_save_as():
    """另存为 — 保存脚本副本到新文件"""
    if not state._editor_rows:
        messagebox.showwarning("提示", "脚本为空，无法保存")
        return
    fp=filedialog.asksaveasfilename(title="另存为",defaultextension=".xls",
        filetypes=[('Excel 文件','*.xls')],initialdir=APP_ROOT)
    if fp:
        try:
            import xlwt
            wb = xlwt.Workbook()
            ws = wb.add_sheet("Sheet1")
            ws.write(0, 0, "命令类型")
            for j in range(1, 10):
                ws.write(0, j, "参数{}".format(j))
            ws.write(1, 0, "（标题行）")
            for i, sd in enumerate(state._editor_rows, start=2):
                ws.write(i, 0, sd.cmd_type)
                for j in range(9):
                    ws.write(i, j+1, sd.args[j] if j < len(sd.args) else "")
            wb.save(fp)
            state.filename=fp; state.has_script=True
            state._editor_modified=False
            script_name_var.set(os.path.basename(fp))
            edit_file_label.config(text=os.path.basename(fp))
            log1("脚本另存为: {}".format(fp))
            from utils import show_toast
            show_toast(root, "已另存为: {}".format(os.path.basename(fp)), "success")
            try:
                from version_manager import get_version_manager
                get_version_manager().save_version(fp, state._editor_rows, "另存为")
            except Exception:
                pass
        except Exception as e:
            messagebox.showerror("另存为失败",str(e))


# ── 版本历史管理 ──
# 版本历史对话框已移至 dialogs.open_version_history


# AI 脚本生成器已移至 dialogs.open_ai_panel

def _cmd_template():
    """Pop up template selection dialog with preview - 上模板列表，下预览(带滚轴)。"""
    dlg=tkinter.Toplevel(root); dlg.title("选择模板"); dlg.geometry("540x520+500+150")
    dlg.transient(root); dlg.grab_set(); dlg.configure(bg=C["bgc"])
    _set_window_icon(dlg)
    dlg.columnconfigure(0, weight=1)
    dlg.rowconfigure(1, weight=0)
    dlg.rowconfigure(2, weight=1)

    # Header
    header_frame = tkinter.Frame(dlg, bg=C["bgc"])
    header_frame.grid(row=0, column=0, sticky="ew", padx=12, pady=(8, 4))
    tkinter.Label(header_frame, text="选择脚本模板", font=FONT_TITLE, bg=C["bgc"],
        fg=C["fgt"]).pack(side="left")
    tkinter.Label(header_frame, text="上:选择模板  下:预览(含滚轴)", font=FONT_SMALL,
        bg=C["bgc"], fg=C["fgm"]).pack(side="left", padx=(12, 0))

    # 上方: 模板按钮 (可横向滚动)
    top_outer = tkinter.Frame(dlg, bg=C["bgc"])
    top_outer.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 4))
    top_outer.columnconfigure(0, weight=1)

    top_canvas = tkinter.Canvas(top_outer, bg=C["bgc"], highlightthickness=0, bd=0, height=40)
    top_hscroll = tkinter.Scrollbar(top_outer, orient="horizontal", command=top_canvas.xview, width=6)
    top_inner = tkinter.Frame(top_canvas, bg=C["bgc"])

    top_inner.bind("<Configure>", lambda e: top_canvas.configure(scrollregion=top_canvas.bbox("all")))
    top_canvas.create_window((0, 0), window=top_inner, anchor="w")
    top_canvas.configure(xscrollcommand=top_hscroll.set)

    top_canvas.grid(row=0, column=0, sticky="ew")
    top_hscroll.grid(row=1, column=0, sticky="ew")

    def _tmpl_on_wheel(event):
        if event.delta > 0:
            top_canvas.xview_scroll(-1, "units")
        else:
            top_canvas.xview_scroll(1, "units")

    # 下方: 预览面板 (带纵向滚轴)
    bottom_frame = tkinter.Frame(dlg, bg=C["bgc"], highlightbackground=C["bd"], highlightthickness=1)
    bottom_frame.grid(row=2, column=0, sticky="nsew", padx=12, pady=(0, 4))
    bottom_frame.columnconfigure(0, weight=1); bottom_frame.rowconfigure(0, weight=1)

    preview_txt = tkinter.Text(bottom_frame, font=("Consolas", 9), bg=C["logbg"], fg=C["logfg"],
        wrap="word", relief="flat", bd=0, padx=8, pady=6, state="disabled")
    preview_txt.grid(row=0, column=0, sticky="nsew")
    preview_scroll = tkinter.Scrollbar(bottom_frame, orient="vertical", command=preview_txt.yview, width=8,
        relief="flat", bg=C["bd"], troughcolor=C["logbg"])
    preview_scroll.grid(row=0, column=1, sticky="ns")
    preview_txt.config(yscrollcommand=preview_scroll.set)

    from templates import list_templates, get_template

    def _show_preview(name):
        preview_txt.config(state="normal")
        preview_txt.delete("1.0", "end")
        rows = get_template(name)
        if rows:
            preview_txt.insert("end", "模板: {} (共 {} 行)\n".format(name, len(rows)), "title")
            preview_txt.insert("end", "-" * 60 + "\n", "sep")
            for i, sd in enumerate(rows):
                preview_txt.insert("end", "{:02d}  {}".format(i+1, sd.cmd_type), "cmd")
                args_str = ", ".join(a if a else "-" for a in sd.args)
                preview_txt.insert("end", "  参数: {}".format(args_str))
                preview_txt.insert("end", "\n")
        preview_txt.config(state="disabled")
    preview_txt.tag_configure("title", font=FONT_TITLE, foreground=C["fgt"])
    preview_txt.tag_configure("cmd", foreground=C["ac"])
    preview_txt.tag_configure("sep", foreground=C["bd"])
    preview_txt.tag_configure("info", foreground=C["fgm"])

    template_names = list_templates()
    for tname in template_names:
        btn = tkinter.Button(top_inner, text=tname, font=FONT_BODY,
            bg=C["ac"], fg="white", relief="raised", bd=1, cursor="hand2",
            padx=8, pady=2, activebackground=C["ach"],
            command=lambda n=tname: (_load_template_lazy(n), dlg.destroy()))
        btn.pack(side="left", padx=2, pady=2)
        btn.bind("<Enter>", lambda e, n=tname: _show_preview(n))

    def _bind_tmpl_scroll(w):
        try:
            w.bind("<MouseWheel>", _tmpl_on_wheel)
        except Exception: pass
        for c in w.winfo_children(): _bind_tmpl_scroll(c)
    dlg.after(50, lambda: _bind_tmpl_scroll(top_canvas))

    # 底部按钮
    btm = tkinter.Frame(dlg, bg=C["bgc"])
    btm.grid(row=3, column=0, sticky="ew", padx=12, pady=(2, 8))
    tkinter.Button(btm, text="关闭", font=FONT_BUTTON, bg=C["bgc"], fg=C["fgb"],
        command=dlg.destroy).pack(side="right")
    tkinter.Label(btm, text="悬停鼠标查看模板预览，点击加载", font=FONT_SMALL,
        bg=C["bgc"], fg=C["fgm"]).pack(side="right", padx=(0, 12))

    # 默认预览第一个
    if template_names:
        _show_preview(template_names[0])

def _load_template_lazy(name):
    """Load template on demand using lazy loading (P0 optimization #2)."""
    from templates import get_template
    rows = get_template(name)
    if rows:
        _push_undo()
        state._editor_rows.extend([ScriptData(r.cmd_type,list(r.args)) for r in rows])
        _editor_sync_to_tree()
        log1("已加载模板: {} ({} 行)".format(name,len(rows)))
# ── 内联编辑 (模块级定义, 供 tree 双击事件使用) ──
_inline_edit_widget = None

def _edit_cell():
    """内联编辑：双击单元格直接在原位编辑"""
    global _inline_edit_widget
    if _inline_edit_widget:
        _inline_edit_widget.destroy()
        _inline_edit_widget = None

    sel = tree.selection()
    if not sel: return
    col = int(tree.identify_column(tree.winfo_pointerx()-tree.winfo_rootx()).replace("#","")) - 1
    idx = tree.index(sel[0])
    if idx >= len(state._editor_rows) or col < 0: return
    sd = state._editor_rows[idx]
    item = sel[0]

    try:
        bbox = tree.bbox(item, column="#{}".format(col + 1))
        if not bbox: return
        x, y, w, h = bbox
    except Exception:
        return

    cur_val = sd.cmd_type if col == 0 else sd.args[col - 1]

    if col == 0:
        _inline_edit_widget = ttk.Combobox(tree_frame, values=ScriptData.COMMANDS,
            state="readonly", font=FONT_BODY)
        _inline_edit_widget.set(cur_val)
    else:
        _inline_edit_widget = tkinter.Entry(tree_frame, font=FONT_BODY,
            relief="solid", bd=1, bg=C["ebg"], fg=C["fgb"],
            insertbackground=C["fgt"])
        _inline_edit_widget.insert(0, cur_val)
        _inline_edit_widget.select_range(0, "end")

    _inline_edit_widget.place(x=x, y=y, width=w + 4, height=h + 2)
    _inline_edit_widget.focus_set()

    def _save_inline(event=None):
        global _inline_edit_widget
        w = _inline_edit_widget
        # 防止竞态: 仅当全局引用与事件源一致时才保存
        if w is None or (event and event.widget is not w):
            return
        _push_undo()  # 内联编辑前保存撤销快照
        new_val = w.get()
        if col == 0:
            sd.cmd_type = new_val
        else:
            sd.args[col - 1] = new_val
        _editor_sync_to_tree()
        w.destroy()
        _inline_edit_widget = None

    def _cancel_inline(event=None):
        global _inline_edit_widget
        w = _inline_edit_widget
        if w and (not event or event.widget is w):
            w.destroy()
            _inline_edit_widget = None

    _inline_edit_widget.bind("<Return>", _save_inline)
    _inline_edit_widget.bind("<Escape>", _cancel_inline)

    def _on_focus_out(event, w=_inline_edit_widget):
        """焦点移出内联编辑控件时保存。

        处理 ttk.Combobox 下拉框 (popdown) 销毁后 focus_get() 抛 KeyError 的异常:
        该时刻不保存, 等待下一次焦点移出时保存, 避免崩溃。
        """
        if not w or event.widget is not w:
            return
        try:
            fw = root.focus_get()
        except Exception:
            # popdown 已销毁导致 focus_get 失败 → 本次不保存, 防崩溃
            return
        if fw is None or str(fw) != str(w):
            _save_inline(event)

    _inline_edit_widget.bind("<FocusOut>", _on_focus_out)

# 将双击绑定放在 _edit_cell 定义之后
tree.bind("<Double-1>", lambda e: _edit_cell())

# === Toolbar buttons
# === Toolbar buttons (grouped with accent strips) - Deferred initialization ──
def _sep(parent):
    """Vertical separator bar between button groups."""
    s = tkinter.Frame(parent, bg=C["bd"], width=2, height=20)
    s.pack(side="left", fill="y", padx=3)


# ======================================================================
# SECTION: Debugger Functions (must be before _init_toolbar_buttons)
# ======================================================================

def _toggle_debug_mode():
    """Toggle debug mode on/off"""
    state.debug_mode = not state.debug_mode
    if state.debug_mode:
        btn_debug_mode.config(bg=C["ac"], fg="white")
        log1("调试模式已开启", "info")
    else:
        btn_debug_mode.config(bg=C["bgc"], fg=C["fgb"])
        state.step_mode = False
        btn_step_mode.config(bg=C["bgc"], fg=C["fgb"])
        log1("调试模式已关闭", "info")

def _toggle_step_mode():
    """Toggle step-by-step execution mode"""
    if not state.debug_mode:
        log1("请先开启调试模式", "warning")
        return
    
    state.step_mode = not state.step_mode
    if state.step_mode:
        btn_step_mode.config(bg=C["ac"], fg="white")
        log1("单步执行模式已开启 - 每执行一行将暂停", "info")
        if state.running:
            state.pause_event.clear()
    else:
        btn_step_mode.config(bg=C["bgc"], fg=C["fgb"])
        log1("单步执行模式已关闭", "info")
        if state.running:
            state.pause_event.set()

def _show_variables_window():
    """Show the variables monitoring window with event-driven updates"""
    # Check if window already exists
    if hasattr(_show_variables_window, '_win') and _show_variables_window._win.winfo_exists():
        _show_variables_window._win.lift()
        _show_variables_window._win.focus_force()
        return
    
    win = tkinter.Toplevel(root)
    win.title("变量监视器 - ACRPA")
    win.geometry("500x600")
    win.resizable(True, True)
    _set_window_icon(win)
    
    # Store window reference
    _show_variables_window._win = win
    
    # Title bar
    title_frame = tkinter.Frame(win, bg=C["ac"])
    title_frame.pack(fill="x")
    tkinter.Label(title_frame, text="[V] 变量监视器", font=FONT_TITLE, 
        fg="white", bg=C["ac"]).pack(pady=5)
    
    # Notebook for tabs (Variables | Call Stack)
    notebook = ttk.Notebook(win)
    notebook.pack(fill="both", expand=True, padx=10, pady=10)
    
    # === Tab 1: Variables ===
    var_frame = tkinter.Frame(notebook, bg=C["bgc"])
    notebook.add(var_frame, text="变量")
    
    # Variables list frame
    list_frame = tkinter.Frame(var_frame, bg=C["bgc"], 
        highlightbackground=C["bd"], highlightthickness=1)
    list_frame.pack(fill="both", expand=True, padx=5, pady=5)
    
    # Create treeview for variables
    var_tree = ttk.Treeview(list_frame, columns=("name", "value"), show="headings")
    var_tree.heading("name", text="变量名")
    var_tree.heading("value", text="值")
    var_tree.column("name", width=180, minwidth=100)
    var_tree.column("value", width=350, minwidth=150)
    
    # Scrollbar
    vsb = ttk.Scrollbar(list_frame, orient="vertical", command=var_tree.yview)
    var_tree.configure(yscrollcommand=vsb.set)
    
    var_tree.pack(side="left", fill="both", expand=True)
    vsb.pack(side="right", fill="y")
    
    # Button frame for variables tab
    btn_frame = tkinter.Frame(var_frame, bg=C["bg"])
    btn_frame.pack(fill="x", padx=5, pady=(0, 5))
    
    def refresh_vars():
        """Refresh the variables display"""
        for item in var_tree.get_children():
            var_tree.delete(item)
        
        # Add engine variables
        if hasattr(engine, 'variables'):
            for var_name, var_value in engine.variables.items():
                var_tree.insert("", "end", values=(var_name, str(var_value)))
        
        # Add state variables
        state_vars = {
            "running": state.running,
            "recording": state.recording,
            "debug_mode": state.debug_mode,
            "step_mode": state.step_mode,
            "breakpoints_count": len(state.breakpoints),
        }
        for var_name, var_value in state_vars.items():
            var_tree.insert("", "end", values=(var_name, str(var_value)), tags=("state",))
        
        var_tree.tag_configure("state", foreground="#7C3AED")
    
    def add_var():
        """Add a new variable"""
        add_win = tkinter.Toplevel(win)
        add_win.title("添加变量")
        add_win.geometry("350x150")
        add_win.resizable(False, False)
        _set_window_icon(add_win)
        
        tkinter.Label(add_win, text="变量名:", font=FONT_BODY).pack(pady=(10, 2))
        name_entry = tkinter.Entry(add_win, font=FONT_BODY, width=30)
        name_entry.pack(pady=2)
        
        tkinter.Label(add_win, text="值:", font=FONT_BODY).pack(pady=(5, 2))
        value_entry = tkinter.Entry(add_win, font=FONT_BODY, width=30)
        value_entry.pack(pady=2)
        
        def confirm_add():
            name = name_entry.get().strip()
            value = value_entry.get().strip()
            if name:
                if hasattr(engine, 'variables'):
                    try:
                        from safe_eval import safe_eval
                        evaluated_value = safe_eval(value, {})
                        engine.variables[name] = evaluated_value
                        log1("已添加变量: {} = {}".format(name, evaluated_value))
                    except:
                        engine.variables[name] = value
                        log1("已添加变量: {} = '{}'".format(name, value))
                    refresh_vars()
            add_win.destroy()
        
        tkinter.Button(add_win, text="确定", command=confirm_add, 
            bg=C["ac"], fg="white", font=FONT_BUTTON).pack(pady=10)
    
    tkinter.Button(btn_frame, text="刷新", command=refresh_vars,
        bg=C["ac"], fg="white", font=FONT_SMALL).pack(side="left", padx=2)
    tkinter.Button(btn_frame, text="+ 添加", command=add_var,
        bg=C["sc"], fg="white", font=FONT_SMALL).pack(side="left", padx=2)
    
    # Initial refresh
    refresh_vars()
    
    # P1 Enhancement: Event-driven variable updates
    def on_variable_change(changed_vars):
        """Callback when variables change - only update changed items"""
        if not win.winfo_exists():
            return
        
        # Update only changed variables in the tree
        for item in var_tree.get_children():
            values = var_tree.item(item, "values")
            if values and values[0] in changed_vars:
                var_tree.item(item, values=(values[0], str(changed_vars[values[0]])))
                # Highlight changed items briefly
                var_tree.item(item, tags=("changed",))
                var_tree.tag_configure("changed", background="#FEF3C7")
                win.after(1000, lambda i=item: var_tree.item(i, tags=()))
                return
        
        # If variable is new, refresh entire list
        refresh_vars()
    
    # Register the observer with engine's variable watcher
    if hasattr(engine, 'variable_watcher'):
        engine.variable_watcher.register_observer(on_variable_change)
    
    # Cleanup on window close
    def on_close():
        if hasattr(engine, 'variable_watcher'):
            engine.variable_watcher.unregister_observer(on_variable_change)
        win.destroy()
    
    win.protocol("WM_DELETE_WINDOW", on_close)
    
    # === Tab 2: Call Stack ===
    stack_frame = tkinter.Frame(notebook, bg=C["bgc"])
    notebook.add(stack_frame, text="调用栈")
    
    # Call stack display
    stack_text = tkinter.Text(stack_frame, font=("Consolas", 10), 
        bg=C["logbg"], fg=C["logfg"], wrap="word", relief="solid", bd=1,
        padx=10, pady=10, state="disabled")
    stack_text.pack(fill="both", expand=True, padx=5, pady=5)
    
    def refresh_call_stack():
        """Refresh call stack display"""
        if not win.winfo_exists():
            return
        
        if hasattr(engine, 'call_stack'):
            stack_info = engine.call_stack.get_formatted_stack()
            depth = engine.call_stack.get_depth()
            
            stack_text.config(state="normal")
            stack_text.delete("1.0", "end")
            
            if depth == 0:
                stack_text.insert("end", "当前无嵌套执行上下文\n\n", "info")
                stack_text.insert("end", "提示:\n", "heading")
                stack_text.insert("end", "- 循环开始时会自动推入调用栈\n")
                stack_text.insert("end", "- 条件判断时会记录分支结果\n")
                stack_text.insert("end", "- 可用于调试复杂的嵌套逻辑\n")
            else:
                stack_text.insert("end", "嵌套深度: {}\n\n".format(depth), "info")
                stack_text.insert("end", stack_info + "\n")
            
            stack_text.tag_configure("info", foreground="#3B82F6")
            stack_text.tag_configure("heading", foreground=C["ok"], font=FONT_TITLE)
            stack_text.config(state="disabled")
        
        # Schedule next refresh
        if win.winfo_exists():
            win.after(1000, refresh_call_stack)
    
    # Start call stack refresh
    refresh_call_stack()

    # === Tab 3: Execution Timing (调试器增强) ===
    timing_frame = tkinter.Frame(notebook, bg=C["bgc"])
    notebook.add(timing_frame, text="执行耗时")

    # Timing treeview
    timing_tree = ttk.Treeview(timing_frame, columns=("row", "cmd", "time", "status"),
        show="headings")
    timing_tree.heading("row", text="行")
    timing_tree.heading("cmd", text="命令")
    timing_tree.heading("time", text="耗时")
    timing_tree.heading("status", text="状态")
    timing_tree.column("row", width=40, minwidth=30)
    timing_tree.column("cmd", width=100, minwidth=60)
    timing_tree.column("time", width=80, minwidth=60)
    timing_tree.column("status", width=50, minwidth=40)

    tsb = ttk.Scrollbar(timing_frame, orient="vertical", command=timing_tree.yview)
    timing_tree.configure(yscrollcommand=tsb.set)
    timing_tree.pack(side="left", fill="both", expand=True, padx=5, pady=5)
    tsb.pack(side="right", fill="y", pady=5)

    def refresh_timing():
        """Refresh execution timing display"""
        if not win.winfo_exists():
            return
        for item in timing_tree.get_children():
            timing_tree.delete(item)
        if hasattr(state, '_exec_timings') and state._exec_timings:
            # Sort by row number
            sorted_rows = sorted(state._exec_timings.items(), key=lambda x: x[0])
            for row_num, tdata in sorted_rows:
                status_text = "✓" if tdata["success"] else "✗"
                tag = "ok" if tdata["success"] else "fail"
                timing_tree.insert("", "end", values=(
                    row_num, tdata["cmd"][:12],
                    "{:.3f}s".format(tdata["elapsed"]),
                    status_text), tags=(tag,))
            timing_tree.tag_configure("ok", foreground="#10B981")
            timing_tree.tag_configure("fail", foreground="#EF4444")
            # Auto-scroll to latest
            children = timing_tree.get_children()
            if children:
                timing_tree.see(children[-1])
        if win.winfo_exists():
            win.after(2000, refresh_timing)
    refresh_timing()

    # === Inline variable editing (双击变量值修改) ===
    def _on_var_double_click(event):
        """Handle double-click on variable to edit value inline"""
        sel = var_tree.selection()
        if not sel:
            return
        values = var_tree.item(sel[0], "values")
        if not values or len(values) < 2:
            return
        var_name = values[0]
        var_value = values[1]

        # Create inline entry overlay
        edit_win = tkinter.Toplevel(win)
        edit_win.title("编辑变量")
        edit_win.geometry("350x150")
        edit_win.transient(win)
        edit_win.configure(bg=C["bgc"])
        _set_window_icon(edit_win)

        tkinter.Label(edit_win, text="编辑变量: {}".format(var_name),
            font=FONT_TITLE, bg=C["bgc"], fg=C["fgt"]).pack(pady=(10, 6))
        edit_entry = tkinter.Entry(edit_win, font=("Consolas", 11), width=35,
            relief="solid", bd=1, bg=C["ebg"], fg=C["fgb"])
        edit_entry.insert(0, var_value)
        edit_entry.pack(pady=(0, 8), padx=10)
        edit_entry.select_range(0, "end")
        edit_entry.focus_set()

        def _save_edit():
            new_val = edit_entry.get().strip()
            if hasattr(engine, 'variables'):
                # Try to convert to number if possible
                try:
                    if '.' in new_val:
                        engine.variables[var_name] = float(new_val)
                    else:
                        engine.variables[var_name] = int(new_val)
                except ValueError:
                    engine.variables[var_name] = new_val
                log1("变量已修改: {} = {}".format(var_name, engine.variables[var_name]))
                refresh_vars()
            edit_win.destroy()

        btn_frame = tkinter.Frame(edit_win, bg=C["bgc"])
        btn_frame.pack(pady=(0, 8))
        tkinter.Button(btn_frame, text="确定", command=_save_edit,
            bg=C["ac"], fg="white", font=FONT_BUTTON, padx=16, pady=3).pack(
            side="left", padx=4)
        tkinter.Button(btn_frame, text="取消", command=edit_win.destroy,
            bg=C["bgc"], fg=C["fgb"], font=FONT_BUTTON, padx=16, pady=3).pack(
            side="left", padx=4)

        edit_win.bind("<Return>", lambda e: _save_edit())
        edit_win.bind("<Escape>", lambda e: edit_win.destroy())

    var_tree.bind("<Double-1>", _on_var_double_click)


def _open_marketplace():
    """Open script marketplace dialog to browse and download community scripts."""
    dlg = tkinter.Toplevel(root)
    dlg.title("脚本市场 — ACRPA")
    dlg.geometry("600x520+400+80")
    dlg.minsize(500, 420)
    dlg.transient(root)
    dlg.grab_set()
    dlg.configure(bg=C["bgc"])
    _set_window_icon(dlg)
    dlg.columnconfigure(0, weight=1)
    dlg.rowconfigure(3, weight=1)

    # Title
    title_frame = tkinter.Frame(dlg, bg=C["bgc"])
    title_frame.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 4))
    tkinter.Label(title_frame, text="脚本市场", font=FONT_TITLE, bg=C["bgc"],
        fg=C["fgt"]).pack(side="left")
    tkinter.Label(title_frame, text="浏览社区共享的自动化脚本", font=FONT_SMALL,
        bg=C["bgc"], fg=C["fgm"]).pack(side="left", padx=(8, 0))

    # Search bar
    search_frame = tkinter.Frame(dlg, bg=C["bgc"])
    search_frame.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 6))
    search_var = tkinter.StringVar()
    search_entry = tkinter.Entry(search_frame, textvariable=search_var,
        font=FONT_BODY, width=30, relief="solid", bd=1, bg=C["ebg"], fg=C["fgb"])
    search_entry.pack(side="left", fill="x", expand=True, padx=(0, 4))

    # Category filter
    cat_var = tkinter.StringVar(value="全部")
    cat_combo = ttk.Combobox(search_frame, textvariable=cat_var, width=8,
        values=("全部", "办公", "财务", "系统", "其他"), state="readonly")
    cat_combo.pack(side="left", padx=2)

    # Script list frame
    list_frame = tkinter.Frame(dlg, bg=C["bgc"],
        highlightbackground=C["bd"], highlightthickness=1)
    list_frame.grid(row=2, column=0, sticky="nsew", padx=12, pady=(0, 4))
    list_frame.columnconfigure(0, weight=1)
    list_frame.rowconfigure(0, weight=1)

    # Use a canvas with scroll for script cards
    canvas = tkinter.Canvas(list_frame, bg=C["bgc"], highlightthickness=0, bd=0)
    scrollbar = tkinter.Scrollbar(list_frame, orient="vertical", command=canvas.yview, width=6)
    scripts_inner = tkinter.Frame(canvas, bg=C["bgc"])

    scripts_inner.bind("<Configure>",
        lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.create_window((0, 0), window=scripts_inner, anchor="nw")
    canvas.configure(yscrollcommand=scrollbar.set)

    canvas.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")

    # Mouse wheel binding - use widget-level binding instead of bind_all
    def _on_canvas_mousewheel(event):
        canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
    
    # Bind directly to canvas, no need for bind_all/enter/leave
    canvas.bind("<MouseWheel>", _on_canvas_mousewheel)
    # Status
    status_var = tkinter.StringVar(value="正在加载脚本库...")
    status_label = tkinter.Label(dlg, textvariable=status_var, font=FONT_SMALL,
        bg=C["bgc"], fg=C["fgm"])
    status_label.grid(row=3, column=0, sticky="w", padx=12, pady=(0, 8))

    # Progress bar
    progress = ttk.Progressbar(dlg, mode="indeterminate", length=300)
    progress.grid(row=4, column=0, sticky="ew", padx=12, pady=(0, 8))
    progress.start(10)

    # Store loaded scripts
    _market_scripts = []

    def _refresh_script_list(scripts=None, keyword=""):
        """Refresh the script card list."""
        for w in scripts_inner.winfo_children():
            w.destroy()

        if scripts is None:
            scripts = _market_scripts

        # Filter by category
        cat = cat_var.get()
        if cat != "全部":
            scripts = [s for s in scripts if s.category == cat]

        # Filter by keyword
        kw = keyword.strip().lower()
        if kw:
            scripts = [s for s in scripts if kw in s.name.lower()
                       or kw in s.description.lower()
                       or kw in s.category.lower()
                       or any(kw in t.lower() for t in s.tags)]

        if not scripts:
            tkinter.Label(scripts_inner, text="没有找到匹配的脚本",
                font=FONT_BODY, fg=C["fgm"], bg=C["bgc"]).pack(pady=20)
            return

        for i, s in enumerate(scripts):
            card = tkinter.Frame(scripts_inner, bg=C["bgc"],
                highlightbackground=C["bd"], highlightthickness=1 if i > 0 else 0)
            card.pack(fill="x", padx=4, pady=(0 if i == 0 else 1, 1))

            # Star rating
            stars = "★" * int(s.rating) + "☆" * (5 - int(s.rating))
            header = tkinter.Frame(card, bg=C["bgc"])
            header.pack(fill="x", padx=8, pady=(6, 2))
            tkinter.Label(header, text=s.name, font=FONT_TITLE,
                fg=C["fgt"], bg=C["bgc"]).pack(side="left")
            tkinter.Label(header, text="  {} {} ({}评价)".format(
                stars, s.rating, s.rating_count),
                font=FONT_SMALL, fg=C["wn"], bg=C["bgc"]).pack(side="left", padx=(4, 0))
            tkinter.Label(header, text="{} 下载".format(s.downloads),
                font=FONT_SMALL, fg=C["fgm"], bg=C["bgc"]).pack(side="right")

            # Description + tags
            body = tkinter.Frame(card, bg=C["bgc"])
            body.pack(fill="x", padx=8, pady=(0, 2))
            tkinter.Label(body, text=s.description, font=FONT_SMALL,
                fg=C["fgb"], bg=C["bgc"], wraplength=500, justify="left").pack(
                anchor="w")
            tag_row = tkinter.Frame(body, bg=C["bgc"])
            tag_row.pack(fill="x", pady=(3, 0))
            tkinter.Label(tag_row, text="[{}]".format(s.category), font=("Microsoft YaHei UI", 7),
                fg=C["ac"], bg=C["bgc"]).pack(side="left", padx=(0, 4))
            for t in s.tags[:4]:
                tkinter.Label(tag_row, text=t, font=("Microsoft YaHei UI", 7),
                    fg=C["fgm"], bg=C["acl"], padx=4, pady=1).pack(side="left", padx=1)
            if s.requires:
                tkinter.Label(tag_row, text="需要: {}".format(",".join(s.requires)),
                    font=("Microsoft YaHei UI", 7), fg=C["wn"], bg=C["bgc"]).pack(
                    side="right")

            # Button row
            btn_row = tkinter.Frame(card, bg=C["bgc"])
            btn_row.pack(fill="x", padx=8, pady=(2, 6))
            tkinter.Button(btn_row, text="导入到编辑器", font=FONT_SMALL,
                bg=C["sc"], fg="white", relief="raised", bd=2, cursor="hand2",
                activebackground=_darken(C["sc"]), activeforeground="white",
                command=lambda sid=s.id: _import_script(sid)).pack(
                side="left", padx=(0, 4))
            tkinter.Button(btn_row, text="详情", font=FONT_SMALL,
                bg=C["bgc"], fg=C["fgb"], relief="raised", bd=2, cursor="hand2",
                command=lambda s_=s: _show_script_detail(s_)).pack(side="left")

        status_var.set("共 {} 个脚本".format(len(scripts)))

    def _import_script(script_id):
        """Download and import a script into the editor."""
        progress.start(10)
        status_var.set("正在下载脚本...")

        def _do_import():
            try:
                from marketplace import download_script
                save_dir = os.path.dirname(state.CONFIG_PATH)
                fp = download_script(script_id, save_dir)

                def _on_done():
                    progress.stop()
                    state.filename = fp
                    state.has_script = True
                    state.script_dir = os.path.dirname(fp)
                    script_name_var.set(os.path.basename(fp))
                    edit_file_label.config(text=os.path.basename(fp))
                    _editor_load_xls(fp)
                    dlg.destroy()
                    from utils import show_toast
                    show_toast(root, "脚本已导入: {}".format(os.path.basename(fp)), "success")
                dlg.after(0, _on_done)
            except Exception as e:
                _err_msg = str(e)
                def _on_error(err=_err_msg):
                    progress.stop()
                    status_var.set("导入失败: {}".format(err))
                    from utils import show_toast
                    show_toast(root, "导入失败: {}".format(err), "error")
                dlg.after(0, _on_error)

        threading.Thread(target=_do_import, daemon=True).start()

    def _show_script_detail(s_info):
        """Show script detail popup."""
        detail = tkinter.Toplevel(dlg)
        detail.title(s_info.name)
        detail.geometry("400x300")
        detail.transient(dlg)
        detail.configure(bg=C["bgc"])
        _set_window_icon(detail)

        tkinter.Label(detail, text=s_info.name, font=FONT_TITLE,
            fg=C["fgt"], bg=C["bgc"]).pack(pady=(10, 4))
        tkinter.Label(detail, text="作者: {} | 版本: {} | 分类: {}".format(
            s_info.author, s_info.version, s_info.category),
            font=FONT_SMALL, fg=C["fgm"], bg=C["bgc"]).pack()
        tkinter.Label(detail, text=s_info.description, font=FONT_BODY,
            fg=C["fgb"], bg=C["bgc"], wraplength=350, justify="left").pack(
            pady=(10, 6), padx=20)
        tkinter.Label(detail, text="下载: {} | 评分: {} ({})".format(
            s_info.downloads, s_info.rating, s_info.rating_count),
            font=FONT_SMALL, fg=C["ac"], bg=C["bgc"]).pack()

        if s_info.requires:
            tkinter.Label(detail, text="依赖: {}".format(", ".join(s_info.requires)),
                font=FONT_SMALL, fg=C["wn"], bg=C["bgc"]).pack(pady=(4, 0))

        tkinter.Button(detail, text="导入此脚本", font=FONT_BUTTON,
            bg=C["sc"], fg="white", relief="raised", bd=3,
            command=lambda: (_import_script(s_info.id), detail.destroy())).pack(pady=10)

    # Search on type
    def _on_search(*args):
        _refresh_script_list(_market_scripts, search_var.get())
    search_var.trace_add("write", _on_search)

    # Category change
    def _on_cat_change(*args):
        _refresh_script_list(_market_scripts, search_var.get())
    cat_var.trace_add("write", _on_cat_change)

    # Load scripts in background
    def _load_scripts():
        try:
            from marketplace import fetch_index
            scripts = fetch_index()
        except Exception:
            from marketplace import _load_builtin_scripts
            scripts = _load_builtin_scripts()

        def _on_loaded():
            nonlocal _market_scripts
            _market_scripts = scripts
            progress.stop()
            progress.grid_remove()
            _refresh_script_list(scripts)
        dlg.after(0, _on_loaded)

    # Ensure mousewheel restored on ALL close paths (X button + code destroy)
    _orig_dlg_destroy = dlg.destroy
    def _safe_dlg_destroy():
        canvas.unbind_all("<MouseWheel>")
        _orig_dlg_destroy()
    dlg.destroy = _safe_dlg_destroy

    threading.Thread(target=_load_scripts, daemon=True).start()


def _init_toolbar_buttons():
    """Initialize toolbar buttons after all command functions are defined."""
    # Group 1: Row editing operations
    _btn(toolbar_inner,"+ 添加",_cmd_add_row,C["ac"],"white").pack(side="left",padx=1)
    _btn(toolbar_inner,"- 删除",_cmd_del_row,C["dg"],"white").pack(side="left",padx=1)
    _sep(toolbar_inner)
    _btn(toolbar_inner,"↑",_cmd_move_up).pack(side="left",padx=1)
    _btn(toolbar_inner,"↓",_cmd_move_down).pack(side="left",padx=1)
    # Group 2: File operations
    _sep(toolbar_inner)
    _btn(toolbar_inner,"模板",_cmd_template).pack(side="left",padx=1)
    _btn(toolbar_inner,"新建",_cmd_new,C["ac"],"white").pack(side="left",padx=1)
    _btn(toolbar_inner,"打开",_cmd_open,C["ac"],"white").pack(side="left",padx=1)
    _btn(toolbar_inner,"保存",_cmd_save,C["sc"],"white").pack(side="left",padx=1)
    _btn(toolbar_inner,"另存",_cmd_save_as).pack(side="left",padx=1)

    # Group 3: AI and utilities
    _sep(toolbar_inner)
    _btn(toolbar_inner,"AI生成",open_ai_panel,"#7C3AED","white").pack(side="left",padx=1)
    _btn(toolbar_inner,"市场",_open_marketplace,"#059669","white").pack(side="left",padx=1)
    _btn(toolbar_inner,"⊕ 取点",capture_mouse_position,"#F59E0B","white").pack(side="left",padx=1)
    _btn(toolbar_inner,"版本",open_version_history,"#8B5CF6","white").pack(side="left",padx=1)

    # 录制按钮（全局引用 btn_record，供主题刷新/停止录制/录制完成回调更新状态）
    # 注意: _start_recording 定义于本函数之后，须用 lambda 延迟绑定
    global btn_record
    btn_record = _btn(toolbar_inner,"● 录制",lambda: _start_recording(),C["dg"],"white")
    btn_record.pack(side="left",padx=1)
    
    # Group 4: Debugger
    _sep(toolbar_inner)
    global btn_debug_mode, btn_step_mode, btn_variables
    btn_debug_mode = _btn(toolbar_inner,"[D] 调试",_toggle_debug_mode,C["bgc"],C["fgb"])
    btn_debug_mode.pack(side="left",padx=1)
    btn_step_mode = _btn(toolbar_inner,"[S] 单步",_toggle_step_mode,C["bgc"],C["fgb"])
    btn_step_mode.pack(side="left",padx=1)
    btn_variables = _btn(toolbar_inner,"[V] 变量",_show_variables_window,C["bgc"],C["fgb"])
    btn_variables.pack(side="left",padx=1)
    
    _sep(toolbar_inner)
    _btn(toolbar_inner,"清空",_cmd_clear,C["dg"],"white").pack(side="left",padx=1)


# Initialize toolbar buttons now that all command functions are defined
_init_toolbar_buttons()

# === Shared helpers defined before TAB 2 ──
def select_script():
    """Open file dialog to choose an Excel (.xls) script file."""
    state.filename = filedialog.askopenfilename(
        title="选择一个脚本文件",filetypes=[('xls','*.xls')],initialdir=APP_ROOT)
    if state.filename and os.path.getsize(state.filename):
        state.has_script=True
        script_name_var.set(os.path.basename(state.filename))
        edit_file_label.config(text=os.path.basename(state.filename))
        state.script_dir=os.path.dirname(state.filename)
        rz.delete(0.0,"end"); log1("脚本资源存放目录：{}".format(state.script_dir))
        status_dot.config(text="● 已加载",fg=C["sc"])
        status_text.config(text=" 脚本已加载 — 点击「开始运行」启动",fg=C["fgb"])
        _editor_load_xls(state.filename)
        _add_recent_script(state.filename)
    else: state.has_script=False

def toggle_pause():
    if state.pause_event.is_set():
        state.pause_event.clear(); btn_pause.config(text="▶ 继续",bg=C["ac"])
        status_text.config(text=" 已暂停 — 点击「继续」恢复执行",fg=C["wn"])
        status_dot.config(text="● 已暂停",fg=C["wn"])
    else:
        state.pause_event.set(); btn_pause.config(text="⏸ 暂停",bg=C["wn"])
        status_text.config(text=" 运行中 — 正在执行自动化任务",fg=C["sc"])
        status_dot.config(text="● 运行中",fg=C["sc"])


def _screenshot_tool():
    """Region screenshot using transparent overlay."""
    _load_pil()  # P0 Optimization #1: Lazy load PIL
    root.withdraw()
    time.sleep(0.3)
    ss = _get_pa().screenshot()
    overlay = tkinter.Toplevel()
    overlay.attributes("-fullscreen", True)
    overlay.attributes("-alpha", 0.4)
    overlay.attributes("-topmost", True)
    overlay.configure(bg="black")
    overlay.config(cursor="cross")

    canvas = tkinter.Canvas(overlay, bg="black", highlightthickness=0)
    canvas.pack(fill="both", expand=True)
    ss_img = ImageTk.PhotoImage(ss)
    canvas.create_image(0, 0, image=ss_img, anchor="nw")
    canvas.ss_img = ss_img
    rect = None; start_x = [0]; start_y = [0]

    def on_press(event):
        start_x[0] = event.x; start_y[0] = event.y

    def on_drag(event):
        nonlocal rect
        if rect: canvas.delete(rect)
        rect = canvas.create_rectangle(start_x[0], start_y[0], event.x, event.y,
            outline="white", width=2)

    def on_release(event):
        nonlocal rect
        overlay.destroy()
        root.deiconify()
        x1 = min(start_x[0], event.x)
        y1 = min(start_y[0], event.y)
        x2 = max(start_x[0], event.x)
        y2 = max(start_y[0], event.y)
        width = x2 - x1
        height = y2 - y1
        # 确保截图区域有效（至少 1x1 像素）
        if width <= 0 or height <= 0:
            log1("截图区域无效，请重新选择")
            return
        ss = _get_pa().screenshot(region=(x1, y1, width, height))
        ts = datetime.datetime.now().strftime("%m%d_%H%M%S")
        fp = os.path.join(SCREENSHOT_DIR, "shot_{}.png".format(ts))
        ss.save(fp)
        log1("截图已保存: {}".format(fp))
        from utils import show_toast; show_toast(root, "截图已保存", "success")

    overlay.bind("<Button-1>", on_press)
    overlay.bind("<B1-Motion>", on_drag)
    overlay.bind("<ButtonRelease-1>", on_release)
    overlay.bind("<Escape>", lambda e: (overlay.destroy(), root.deiconify()))
    overlay.mainloop()


# ======================================================================
# TAB 2: 执行控制 - compact layout
# ======================================================================
tab_exec = tkinter.Frame(notebook,bg=C["bg"])
notebook.add(tab_exec,text="  执行控制  ")
tab_exec.columnconfigure(0,weight=1)
tab_exec.rowconfigure(0,weight=0); tab_exec.rowconfigure(1,weight=1)

# Config card - merged with progress and buttons
card_config = create_card(tab_exec)
card_config.grid(row=0,column=0,sticky="ew",**PAD)
card_config.columnconfigure(1,weight=1)

tkinter.Label(card_config,text="当前脚本",font=FONT_BODY,fg=C["fgb"],
    bg=C["bgc"]).grid(row=0,column=0,sticky="w",**PI)

script_name_var=tkinter.StringVar(value="没有选择文件")
# 快速切换：下拉框 + 浏览按钮
_script_switcher = ttk.Combobox(card_config, textvariable=script_name_var,
    font=FONT_BODY, state="readonly", width=28)
_script_switcher.grid(row=0, column=1, sticky="ew", padx=(0, 4), pady=2)
_recent_scripts = []

def _update_recent_scripts():
    """更新快速切换下拉列表"""
    global _recent_scripts
    try:
        recent_file = os.path.join(APP_ROOT, "recent.json")
        if os.path.exists(recent_file):
            with open(recent_file, "r", encoding="utf-8") as f:
                _recent_scripts = json.load(f)[:10]
    except Exception:
        _recent_scripts = []
    scripts = [os.path.basename(s) for s in _recent_scripts] if _recent_scripts else ["没有选择文件"]
    _script_switcher["values"] = scripts

def _on_script_switched(event=None):
    """快速切换到最近使用的脚本"""
    idx = _script_switcher.current()
    if idx >= 0 and idx < len(_recent_scripts):
        fp = _recent_scripts[idx]
        if os.path.exists(fp):
            state.filename = fp; state.has_script = True
            state.script_dir = os.path.dirname(fp)
            script_name_var.set(os.path.basename(fp))
            edit_file_label.config(text=os.path.basename(fp))
            _editor_load_xls(fp)
            from utils import show_toast
            show_toast(root, "已切换: {}".format(os.path.basename(fp)), "info")

def _add_recent_script(filepath):
    """添加脚本到最近使用列表"""
    global _recent_scripts
    if filepath in _recent_scripts:
        _recent_scripts.remove(filepath)
    _recent_scripts.insert(0, filepath)
    _recent_scripts = _recent_scripts[:10]
    try:
        with open(os.path.join(APP_ROOT, "recent.json"), "w", encoding="utf-8") as f:
            json.dump(_recent_scripts, f)
    except Exception:
        pass
    _update_recent_scripts()

_script_switcher.bind("<<ComboboxSelected>>", _on_script_switched)
_update_recent_scripts()

tkinter.Button(card_config,text="选择脚本",font=FONT_BUTTON,bg=C["ac"],fg="white",
    activebackground=C["ach"],activeforeground="white",relief="raised",bd=3,
    cursor="hand2",padx=8,pady=2,command=select_script).grid(row=0,column=2,sticky="e",**PI)

tkinter.Label(card_config,text="运行次数",font=FONT_BODY,fg=C["fgb"],
    bg=C["bgc"]).grid(row=1,column=0,sticky="w",**PI)
loop_count_var=tkinter.StringVar(value="无限循环")
ttk.Combobox(card_config,textvariable=loop_count_var,
    values=('无限循环',1,2,3,4,5,6,7,8,9,10),state="readonly",width=10).grid(
    row=1,column=1,sticky="w",padx=(6,4),pady=2)

rf=tkinter.Frame(card_config,bg=C["bgc"]); rf.grid(row=1,column=2,sticky="e",**PI)
btn_run=tkinter.Button(rf,text="▶ 运行",font=FONT_BUTTON,bg=C["sc"],fg="white",
    activebackground=_darken(C["sc"]),activeforeground="white",relief="raised",bd=3,
    cursor="hand2",padx=6,pady=2,command=main_run)
btn_run.pack(side="left",padx=(0,2))
btn_pause=tkinter.Button(rf,text="⏸ 暂停",font=FONT_BUTTON,bg=C["wn"],fg="white",
    activebackground=_darken(C["wn"]),activeforeground="white",relief="raised",bd=3,
    cursor="hand2",padx=6,pady=2,command=toggle_pause)
btn_pause.pack(side="left")
def _step_once():
    """Advance execution by one step: unpause briefly so the engine runs one row.
    
    Uses a slightly longer window (200ms) to account for slower operations like
    image recognition, then re-pauses automatically.
    """
    if not state.running:
        log1("脚本未运行，无法单步执行", "warning")
        return
    state.pause_event.set()
    # Give the engine thread a window to process one command, then re-pause
    root.after(200, lambda: (state.pause_event.clear() if not state.quit2 else None))

btn_step=tkinter.Button(rf,text="⏭ 单步",font=FONT_BUTTON,bg=C["ac"],fg="white",
    activebackground=C["ach"],activeforeground="white",relief="raised",bd=3,
    cursor="hand2",padx=6,pady=2,
    command=_step_once)
btn_step.pack(side="left",padx=(2,0))

# Progress bar inline - 紧凑单行: 状态文本 + 微型进度条
prog_frame=tkinter.Frame(card_config,bg=C["bgc"])
prog_frame.grid(row=2,column=0,columnspan=3,sticky="ew",padx=8,pady=(2,3))
prog_frame.columnconfigure(0,weight=1)

progress_label=tkinter.Label(prog_frame,text="",font=FONT_SMALL,fg=C["fgm"],bg=C["bgc"])
progress_label.grid(row=0,column=0,sticky="w")
elapsed_label=tkinter.Label(prog_frame,text="",font=FONT_SMALL,fg=C["fgm"],bg=C["bgc"])
elapsed_label.grid(row=0,column=1,sticky="e",padx=(8,0))
# 微型进度条 (高度 4px)
style.configure("Micro.Horizontal.TProgressbar", thickness=4)
progress_bar=ttk.Progressbar(prog_frame,mode="determinate",style="Micro.Horizontal.TProgressbar")
progress_bar.grid(row=1,column=0,columnspan=2,sticky="ew",pady=(1,0))

# Log - compact layout
card_log = create_card(tab_exec)
card_log.grid(row=1,column=0,sticky="nsew",**PAD)
card_log.columnconfigure(0,weight=1); card_log.rowconfigure(1,weight=1)

lh=tkinter.Frame(card_log,bg=C["bgc"]); lh.grid(row=0,column=0,sticky="ew",**PI)
lh.columnconfigure(0,weight=1)
tkinter.Label(lh,text="运行日志",font=FONT_BODY,fg=C["fgm"],bg=C["bgc"]).grid(
    row=0,column=0,sticky="w")
tkinter.Label(lh,text="自动滚动",font=FONT_SMALL,fg=C["fgm"],bg=C["bgc"]).grid(
    row=0,column=1,sticky="e")

log_frame = tkinter.Frame(card_log,bg=C["logbg"])
log_frame.grid(row=1,column=0,sticky="nsew",padx=6,pady=(0,4))
log_frame.columnconfigure(0,weight=1); log_frame.rowconfigure(0,weight=1)

rz=tkinter.Text(log_frame,font=("Consolas",9),fg=C["logfg"],bg=C["logbg"],
    wrap="word",relief="flat",bd=0,padx=6,pady=4,
    selectbackground=C["acl"],selectforeground=C["fgt"],undo=True,maxundo=50)
rz.grid(row=0,column=0,sticky="nsew")
scroll=tkinter.Scrollbar(log_frame,width=6,relief="flat",elementborderwidth=0,
    bg=C["bd"],activebackground=C["fgm"],troughcolor=C["logbg"])
scroll.grid(row=0,column=1,sticky="ns")

scroll.config(command=rz.yview); rz.config(yscrollcommand=scroll.set)
rz.tag_configure("info",foreground=C["fgb"])
rz.tag_configure("success",foreground=C["sc"])
rz.tag_configure("warning",foreground=C["wn"])
rz.tag_configure("error",foreground=C["dg"])

_tlog=ThreadSafeLog(rz, LOG_DIR); set_tlog(_tlog)

# ── 启动时输出日志保存路径，方便用户定位 ──
_log_save_path = os.path.join(LOG_DIR, "acrpa_{}.log".format(
    __import__("datetime").datetime.now().strftime("%Y%m%d")))
log1("日志保存路径: {}".format(_log_save_path))
log1("截图保存路径: {}".format(SCREENSHOT_DIR))

# ── dialogs 模块依赖注入 (在 root/C/FONT/回调全部就绪后调用) ──
init_ctx(root_win=root, colors=C,
         fonts=(FONT_TITLE, FONT_BODY, FONT_SMALL, FONT_BUTTON),
         app_root=APP_ROOT, res_dir=RES_DIR,
         editor_sync_to_tree=_editor_sync_to_tree,
         push_undo=_push_undo, main_run=main_run, tlog=_tlog,
         exit_for_update=_exit_for_update)
# ── settings_window 模块依赖注入 ──
settings_window.init_ctx(root_win=root, colors=C,
         fonts=(FONT_TITLE, FONT_BODY, FONT_SMALL, FONT_BUTTON),
         app_root=APP_ROOT, tlog=_tlog,
         ensure_tray=_ensure_tray, destroy_tray=_destroy_tray,
         toggle_fold=_toggle_fold, main_run=main_run)

# ── AI 自然语言调试对话框 (在使用前定义) ──
# AI 智能调试对话框已移至 dialogs.open_ai_debug_dialog


# Action bar inside log card - compact (支持横向滚动)
ab_outer = tkinter.Frame(card_log, bg=C["bgc"])
ab_outer.grid(row=2, column=0, sticky="ew", padx=6, pady=(0,4))

ab_canvas = tkinter.Canvas(ab_outer, bg=C["bgc"], height=34,
    highlightthickness=0, bd=0)
ab_scrollbar = tkinter.Scrollbar(ab_outer, orient="horizontal",
    command=ab_canvas.xview, width=6)
ab = tkinter.Frame(ab_canvas, bg=C["bgc"])

ab.bind("<Configure>",
    lambda e: ab_canvas.configure(scrollregion=ab_canvas.bbox("all")))

ab_canvas.create_window((0, 0), window=ab, anchor="w")
ab_canvas.configure(xscrollcommand=ab_scrollbar.set)

ab_outer.columnconfigure(0, weight=1)
ab_canvas.grid(row=0, column=0, sticky="ew")
ab_scrollbar.grid(row=1, column=0, sticky="ew")

# 滚轮横向滚动 - handler 定义（递归绑定在按钮创建之后）
def _ab_on_wheel(event):
    if event.delta > 0:
        ab_canvas.xview_scroll(-1, "units")
    else:
        ab_canvas.xview_scroll(1, "units")

# 按钮使用 pack 横向排列 (替代原 grid 布局)
tkinter.Button(ab,text="■ 停止",font=FONT_BUTTON,bg=C["dg"],fg="white",
    activebackground=_darken(C["dg"]),activeforeground="white",relief="raised",bd=3,
    cursor="hand2",padx=8,pady=3,command=stop_execution).pack(side="left",padx=2)
tkinter.Button(ab,text="⊕ 屏幕取点",font=FONT_BUTTON,bg=C["bgc"],fg=C["fgb"],
    activebackground=C["acl"],activeforeground=C["fgb"],relief="raised",bd=3,cursor="hand2",
    padx=8,pady=3,
    command=capture_mouse_position).pack(side="left",padx=2)
tkinter.Button(ab,text="截屏",font=FONT_BUTTON,bg=C["bgc"],fg=C["fgb"],
    activebackground=C["acl"],activeforeground=C["fgb"],relief="raised",bd=3,cursor="hand2",
    padx=8,pady=3,
    command=_screenshot_tool).pack(side="left",padx=2)
tkinter.Button(ab,text="? 帮助",font=FONT_BUTTON,bg=C["bgc"],fg=C["fgb"],
    activebackground=C["acl"],activeforeground=C["fgb"],relief="raised",bd=3,cursor="hand2",
    padx=8,pady=3,
    command=show_help_dialog).pack(side="left",padx=2)
tkinter.Button(ab,text="[AI] 调试",font=FONT_BUTTON,bg="#7C3AED",fg="white",
    activebackground=_darken("#7C3AED"),activeforeground="white",relief="raised",bd=3,
    cursor="hand2",padx=8,pady=3,
    command=open_ai_debug_dialog).pack(side="left",padx=2)

# ======================================================================
# TAB 3: 设置 — 已分离为独立窗口 (settings_window.open_settings_window)
# ======================================================================


# 设置分卡保存 handlers 已移至 settings_window (各卡 ✓ 按钮独立保存)

def _start_recording():
    """开始录制 —  : 折叠主窗口 → 显示录制 Mini Bar。"""
    if state.recording:
        # 已在录制中 → 停止
        state.record_stop = True
        log1("正在停止录制...")
        return
    
    # 设置录制完成回调：录制线程结束后自动恢复窗口并加载到编辑器
    def _on_rec_done():
        _destroy_recording_bar()
        _restore_from_recording()
        btn_record.config(text="● 录制", bg=C["dg"])
        # ── 将录制的动作加载到编辑器 ──
        for sd in state.recorded_actions:
            state._editor_rows.append(sd)
        _editor_sync_to_tree()
        from utils import show_toast
        show_toast(root, "录制已停止 ({} 个动作已加载到编辑器)".format(
            len(state.recorded_actions)), "success")
    state._on_recording_done = _on_rec_done
    
    # 折叠主窗口
    root.withdraw()
    state.folded = True
    try:
        fold_btn.config(text="⊞", fg=C["ac"])
    except Exception:
        pass
    
    # 创建录制 Mini Bar
    _create_recording_bar()
    
    # 启动录制线程
    state.quit2 = False
    state.record_stop = False
    state.pause_event.set()
    t = threading.Thread(target=recorder.recorder_thread); t.daemon = True; t.start()
    
    log1("录制已开始 — 窗口已折叠，点击 Recording Bar「■ 停止录制」结束")
    from utils import show_toast
    show_toast(root, "● 录制已开始 (窗口已最小化)", "info", 2000)

# 设置卡片 (基础执行/AI增强/定时调度/日志/系统/快速操作/高级) 已移至 settings_window

# 日志/快速操作/高级设置/系统卡片已移至 settings_window

# ======================================================================
# TAB 4: 工作流
# ======================================================================
tab_workflow = tkinter.Frame(notebook, bg=C["bg"])
notebook.add(tab_workflow, text="  工作流  ")   # 第3个Tab
# 设置已分离为独立窗口 (settings_window.open_settings_window)，不再占用 Tab
tab_workflow.columnconfigure(0, weight=1)
tab_workflow.rowconfigure(0, weight=0)
tab_workflow.rowconfigure(1, weight=1)

# ── 工具栏 (支持横向滚动) ──
wf_toolbar_outer = tkinter.Frame(tab_workflow, bg=C["bg"])
wf_toolbar_outer.grid(row=0, column=0, sticky="ew", padx=6, pady=(4, 2))
wf_toolbar_outer.columnconfigure(0, weight=1)

wf_canvas = tkinter.Canvas(wf_toolbar_outer, bg=C["bg"], height=28,
    highlightthickness=0, bd=0)
wf_scrollbar = tkinter.Scrollbar(wf_toolbar_outer, orient="horizontal",
    command=wf_canvas.xview, width=6)
wf_toolbar = tkinter.Frame(wf_canvas, bg=C["bg"])

wf_toolbar.bind("<Configure>",
    lambda e: wf_canvas.configure(scrollregion=wf_canvas.bbox("all")))

wf_canvas.create_window((0, 0), window=wf_toolbar, anchor="w")
wf_canvas.configure(xscrollcommand=wf_scrollbar.set)

wf_canvas.grid(row=0, column=0, sticky="ew")
wf_scrollbar.grid(row=1, column=0, sticky="ew")

# 滚轮横向滚动 - handler 定义（递归绑定在按钮创建之后）
def _wf_on_wheel(event):
    if event.delta > 0:
        wf_canvas.xview_scroll(-1, "units")
    else:
        wf_canvas.xview_scroll(1, "units")

# ── 工作流名称 ──


def _wf_stop():
    from workflow import workflow_engine
    workflow_engine.stop()
    log1("工作流已停止")

# ======================================================================
# SECTION: WorkFlow Toolbar Buttons (moved after all _wf_* function definitions)
# ======================================================================
_btn(wf_toolbar, "新建", lambda: _wf_new(), C["ac"], "white").pack(side="left", padx=1)
_btn(wf_toolbar, "打开", lambda: _wf_open(), C["ac"], "white").pack(side="left", padx=1)
_btn(wf_toolbar, "保存", lambda: _wf_save(), C["sc"], "white").pack(side="left", padx=1)
_btn(wf_toolbar, "导出模板", lambda: _wf_export_template(), C["bgc"], C["fgb"]).pack(side="left", padx=1)
tkinter.Frame(wf_toolbar, bg=C["bd"], width=2, height=20).pack(side="left", fill="y", padx=4)
_btn(wf_toolbar, "+ 脚本", lambda: _wf_add_step("script"), C["bgc"], C["fgb"]).pack(side="left", padx=1)
_btn(wf_toolbar, "+ 并行", lambda: _wf_add_step("parallel"), C["bgc"], C["fgb"]).pack(side="left", padx=1)
_btn(wf_toolbar, "+ 条件", lambda: _wf_add_step("condition"), C["bgc"], C["fgb"]).pack(side="left", padx=1)
_btn(wf_toolbar, "+ 等待", lambda: _wf_add_step("wait"), C["bgc"], C["fgb"]).pack(side="left", padx=1)
_btn(wf_toolbar, "+ 变量", lambda: _wf_add_step("variable"), C["bgc"], C["fgb"]).pack(side="left", padx=1)
_btn(wf_toolbar, "+ 循环", lambda: _wf_add_step("loop"), C["bgc"], C["fgb"]).pack(side="left", padx=1)
tkinter.Frame(wf_toolbar, bg=C["bd"], width=2, height=20).pack(side="left", fill="y", padx=4)
_btn(wf_toolbar, "运行", lambda: _wf_run(), C["sc"], "white").pack(side="left", padx=1)
_btn(wf_toolbar, "停止", lambda: _wf_stop(), C["dg"], "white").pack(side="left", padx=1)
tkinter.Frame(wf_toolbar, bg=C["bd"], width=2, height=20).pack(side="left", fill="y", padx=4)
# 执行控制: 循环次数 + 无限最长
tkinter.Label(wf_toolbar, text="循环:", font=FONT_SMALL, bg=C["bg"], fg=C["fgm"]).pack(side="left", padx=(0, 2))
wf_loop_var = tkinter.StringVar(value="1")
tkinter.Spinbox(wf_toolbar, textvariable=wf_loop_var, from_=1, to=9999, width=3,
    font=FONT_SMALL, bg=C["ebg"], fg=C["fgb"], relief="solid", bd=1).pack(side="left", padx=(0, 6))
tkinter.Label(wf_toolbar, text="次 | 最长:", font=FONT_SMALL, bg=C["bg"], fg=C["fgm"]).pack(side="left", padx=(0, 2))
wf_maxmin_var = tkinter.StringVar(value="0")
tkinter.Spinbox(wf_toolbar, textvariable=wf_maxmin_var, from_=0, to=1440, width=4,
    font=FONT_SMALL, bg=C["ebg"], fg=C["fgb"], relief="solid", bd=1).pack(side="left", padx=(0, 2))
tkinter.Label(wf_toolbar, text="分", font=FONT_SMALL, bg=C["bg"], fg=C["fgm"]).pack(side="left", padx=(0, 6))
_btn(wf_toolbar, "变量管理", lambda: _wf_open_variable_manager(), C["ac"], "white").pack(side="left", padx=1)
tkinter.Frame(wf_toolbar, bg=C["bd"], width=2, height=20).pack(side="left", fill="y", padx=4)
_btn(wf_toolbar, "截图", lambda: _wf_export_screenshot(), "#8B5CF6", "white").pack(side="left", padx=1)
# 视图切换按钮
_wf_show_flow = tkinter.BooleanVar(value=True)
def _wf_toggle_view():
    if _wf_show_flow.get():
        # 隐藏流程图: 从 PanedWindow 中移除 pane
        wf_paned.forget(wf_flow_frame)
        _wf_show_flow.set(False)
    else:
        # 重新显示流程图面板
        wf_paned.add(wf_flow_frame, minsize=200, width=400)
        _wf_show_flow.set(True)
        _wf_render_flowchart()
_wf_view_btn = _btn(wf_toolbar, "[+] 流程图", _wf_toggle_view, "#6366F1", "white")
_wf_view_btn.pack(side="left", padx=1)

# Wire scheduler to GUI
def _wf_scheduler_callback():
    _wf_update_status()
    _wf_update_flowchart()
    _wf_update_log()

# ── 工作流名称 ──
wf_name_var = tkinter.StringVar(value="未命名工作流")
wf_name_entry = tkinter.Entry(wf_toolbar, textvariable=wf_name_var,
    font=FONT_BODY, width=20, relief="solid", bd=1,
    bg=C["ebg"], fg=C["fgb"])
wf_name_entry.pack(side="left", padx=(8, 4))

# ── 主内容区: [操作库 | PanedWindow(列表 + 流程图)] ──
wf_main = tkinter.Frame(tab_workflow, bg=C["bg"])
wf_main.grid(row=1, column=0, sticky="nsew", padx=6, pady=(0, 4))
wf_main.columnconfigure(1, weight=1)
wf_main.rowconfigure(0, weight=1)

# ── 左侧: 操作库 (树形分类, 双击/拖拽添加) ──
wf_lib_frame = tkinter.Frame(wf_main, bg=C["bgc"],
    highlightbackground=C["bd"], highlightthickness=1, width=180)
wf_lib_frame.grid(row=0, column=0, sticky="ns")
wf_lib_frame.grid_propagate(False)
wf_lib_frame.columnconfigure(0, weight=1)
wf_lib_frame.rowconfigure(2, weight=1)

# 操作库标题
tkinter.Label(wf_lib_frame, text="📚 操作库", font=FONT_TITLE,
    bg=C["bgc"], fg=C["fgt"]).grid(row=0, column=0, sticky="ew", padx=8, pady=(6, 2))

# 搜索框
wf_lib_search = tkinter.Entry(wf_lib_frame, font=FONT_SMALL,
    relief="solid", bd=1, bg=C["ebg"], fg=C["fgb"])
wf_lib_search.grid(row=1, column=0, sticky="ew", padx=6, pady=(0, 4))
wf_lib_search.insert(0, "")
wf_lib_search.bind("<KeyRelease>", lambda e: _wf_filter_library())

# 操作库分类树
wf_lib_tree = ttk.Treeview(wf_lib_frame, show="tree", selectmode="browse")
wf_lib_tree.grid(row=2, column=0, sticky="nsew", padx=2, pady=(0, 2))
wf_lib_sy = tkinter.Scrollbar(wf_lib_frame, orient="vertical",
    command=wf_lib_tree.yview, width=6, relief="flat",
    bg=C["bd"], troughcolor=C["bgc"])
wf_lib_sy.grid(row=2, column=1, sticky="ns", pady=(0, 2))
wf_lib_tree.configure(yscrollcommand=wf_lib_sy.set)

# 最近使用
wf_recent_label = tkinter.Label(wf_lib_frame, text="最近使用", font=FONT_SMALL,
    bg=C["bgc"], fg=C["fgm"]).grid(row=3, column=0, sticky="w", padx=8, pady=(4, 0))
wf_recent_list = tkinter.Listbox(wf_lib_frame, font=FONT_SMALL, height=4,
    bg=C["ebg"], fg=C["fgb"], selectbackground=C["ac"], selectforeground="white")
wf_recent_list.grid(row=4, column=0, columnspan=2, sticky="ew", padx=6, pady=(2, 6))
wf_recent_list.bind("<Double-1>", lambda e: _wf_add_recent_clicked())

# ── 右侧: 步骤列表 + 流程图 (PanedWindow 分屏) ──
wf_paned = tkinter.PanedWindow(wf_main, orient="horizontal",
    bg=C["bd"], sashwidth=3, sashrelief="raised")
wf_paned.grid(row=0, column=1, sticky="nsew")

# 左侧: Treeview 步骤列表
wf_list_frame = tkinter.Frame(wf_paned, bg=C["bgc"],
    highlightbackground=C["bd"], highlightthickness=1)
wf_list_frame.columnconfigure(0, weight=1)
wf_list_frame.rowconfigure(0, weight=1)

wf_tree = ttk.Treeview(wf_list_frame,
    columns=("type", "detail"), show="tree headings", selectmode="browse")
wf_tree.heading("#0", text="")
wf_tree.column("#0", width=30, minwidth=30, stretch=False)
wf_tree.heading("type", text="类型")
wf_tree.column("type", width=70, minwidth=60)
wf_tree.heading("detail", text="详情")
wf_tree.column("detail", width=200, minwidth=100)

wf_sy = tkinter.Scrollbar(wf_list_frame, orient="vertical", command=wf_tree.yview,
    width=8, relief="flat", bg=C["bd"], troughcolor=C["bgc"])
wf_tree.configure(yscrollcommand=wf_sy.set)
wf_tree.grid(row=0, column=0, sticky="nsew")
wf_sy.grid(row=0, column=1, sticky="ns")

wf_paned.add(wf_list_frame, minsize=150, width=280)

# 右侧: Canvas 流程图视图
wf_flow_frame = tkinter.Frame(wf_paned, bg=C["bgc"],
    highlightbackground=C["bd"], highlightthickness=1)
wf_flow_frame.columnconfigure(0, weight=1)
wf_flow_frame.rowconfigure(0, weight=1)

wf_flow_canvas = tkinter.Canvas(wf_flow_frame, bg=C["flowbg"] if "flowbg" in C else "#F8FAFC",
    highlightthickness=0, bd=0)
wf_flow_scroll_y = tkinter.Scrollbar(wf_flow_frame, orient="vertical",
    command=wf_flow_canvas.yview, width=8, relief="flat",
    bg=C["bd"], troughcolor=C["bgc"])
wf_flow_canvas.configure(yscrollcommand=wf_flow_scroll_y.set)
wf_flow_canvas.grid(row=0, column=0, sticky="nsew")
wf_flow_scroll_y.grid(row=0, column=1, sticky="ns")

# 流程图滚轮 - 使用widget级别绑定
def _wf_flow_on_wheel(event):
    wf_flow_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

wf_flow_canvas.bind("<MouseWheel>", _wf_flow_on_wheel)

wf_paned.add(wf_flow_frame, minsize=200, width=400)

# ── 工作流数据 ──
_wf_data = {"name": "未命名工作流", "steps": []}
_wf_file = None

TYPE_LABELS = {"script": "- 脚本", "parallel": "|| 并行", "condition": "? 条件",
               "wait": "~ 等待", "loop": "⟳ 循环", "command": "⚡ 命令",
               "variable": "📌 变量", "log": "💬 日志"}

# ── 流程图颜色配置 ──
_FLOW_COLORS = {
    "script":    "#3B82F6",  # 蓝色
    "parallel":  "#10B981",  # 绿色
    "condition": "#F59E0B",  # 橙色
    "wait":      "#9CA3AF",  # 灰色
    "loop":      "#8B5CF6",  # 紫色
    "command":   "#EF4444",  # 红色
    "variable":  "#0EA5E9",  # 天蓝
    "log":       "#64748B",  # 深灰
}
_FLOW_ICONS = {"script": "[>]", "parallel": "⫼", "condition": "◇", "wait": "⏳",
               "loop": "⟳", "command": "⚡", "variable": "📌", "log": "💬"}
_NODE_W = 200
_NODE_H = 36
_NODE_GAP = 56

# 拖拽状态
_wf_drag_idx = None
_wf_drag_start_y = 0
_wf_drag_node_items = []

# ── 操作库分类定义 (参考 AutomationOperation 操作库) ──
_WF_LIB_CATEGORIES = [
    ("流程控制", ["运行脚本", "循环", "并行分支", "条件判断", "等待", "日志输出", "设置变量"]),
    ("鼠标操作", ["点击坐标", "鼠标悬停", "鼠标拖拽", "滚动滚轮", "相对移动", "按下按键", "释放按键"]),
    ("键盘操作", ["模拟按键", "组合热键", "文本输入", "逐字写入", "复制", "粘贴"]),
    ("识别/OCR", ["查找图片", "区域找图", "点击图片", "区域点图", "OCR识别文字", "等待文字", "点击文字"]),
    ("窗口控制", ["激活窗口", "关闭窗口", "最小化窗口", "最大化窗口", "获取窗口位置", "等待窗口"]),
    ("文件/系统", ["屏幕截图", "打开程序", "浏览文件"]),
    ("变量操作", ["设置变量", "读取剪贴板", "字符串处理", "数学运算"]),
    ("浏览器", ["打开网页", "浏览器点击", "浏览器输入", "等待元素", "浏览器截图"]),
]

# 操作库显示名 → 节点构造器 (返回 dict)
_WF_LIB_ACTIONS = {
    "运行脚本":   lambda: {"type": "script", "path": ""},
    "循环":       lambda: {"type": "loop", "times": "3", "steps": []},
    "并行分支":   lambda: {"type": "parallel", "steps": []},
    "条件判断":   lambda: {"type": "condition", "if": "${var} == True", "then": "", "else": ""},
    "等待":       lambda: {"type": "wait", "seconds": 1},
    "日志输出":   lambda: {"type": "log", "text": ""},
    "设置变量":   lambda: {"type": "variable", "var_name": "", "var_value": ""},
    "点击坐标":   lambda: {"type": "command", "cmd": "坐标", "params": ["0", "0", "左", "1", "0.1", "", "", "", ""]},
    "鼠标悬停":   lambda: {"type": "command", "cmd": "悬停", "params": ["0", "0", "0.1", "", "", "", "", "", ""]},
    "鼠标拖拽":   lambda: {"type": "command", "cmd": "拖拽", "params": ["0", "0", "0.1", "", "", "", "", "", ""]},
    "滚动滚轮":   lambda: {"type": "command", "cmd": "滚轮", "params": ["-300", "", "", "", "", "", "", "", ""]},
    "相对移动":   lambda: {"type": "command", "cmd": "相移", "params": ["10", "10", "0.1", "", "", "", "", "", ""]},
    "按下按键":   lambda: {"type": "command", "cmd": "按下", "params": ["ctrl", "", "", "", "", "", "", "", ""]},
    "释放按键":   lambda: {"type": "command", "cmd": "释放", "params": ["ctrl", "", "", "", "", "", "", "", ""]},
    "模拟按键":   lambda: {"type": "command", "cmd": "按键", "params": ["enter", "1", "0.1", "", "", "", "", "", ""]},
    "组合热键":   lambda: {"type": "command", "cmd": "热键", "params": ["ctrl", "c", "", "", "", "", "", "", ""]},
    "文本输入":   lambda: {"type": "command", "cmd": "输入", "params": ["", "", "", "", "", "", "", "", ""]},
    "逐字写入":   lambda: {"type": "command", "cmd": "写入", "params": ["", "0.05", "auto", "", "", "", "", "", ""]},
    "复制":       lambda: {"type": "command", "cmd": "复制", "params": ["", "", "", "", "", "", "", "", ""]},
    "粘贴":       lambda: {"type": "command", "cmd": "粘贴", "params": ["", "", "", "", "", "", "", "", ""]},
    "查找图片":   lambda: {"type": "command", "cmd": "找图", "params": ["img.png", "0.96", "", "", "", "", "", "", ""]},
    "区域找图":   lambda: {"type": "command", "cmd": "区域找图", "params": ["img.png", "0.96", "0", "0", "800", "600", "True", "", ""]},
    "点击图片":   lambda: {"type": "command", "cmd": "点图", "params": ["img.png", "0.96", "", "", "", "", "", "", ""]},
    "区域点图":   lambda: {"type": "command", "cmd": "区域点图", "params": ["img.png", "0.96", "0", "0", "800", "600", "True", "左", "1"]},
    "OCR识别文字": lambda: {"type": "command", "cmd": "识别文字", "params": ["0", "0", "800", "600", "text", "", "", "", ""]},
    "等待文字":   lambda: {"type": "command", "cmd": "等待文字", "params": ["文字", "10", "存在", "0", "0", "800", "600", "", ""]},
    "点击文字":   lambda: {"type": "command", "cmd": "点击文字", "params": ["文字", "0.7", "左", "0", "0", "800", "600", "", ""]},
    "激活窗口":   lambda: {"type": "command", "cmd": "激活窗口", "params": ["窗口标题", "", "", "", "", "", "", "", ""]},
    "关闭窗口":   lambda: {"type": "command", "cmd": "关闭窗口", "params": ["窗口标题", "", "", "", "", "", "", "", ""]},
    "最小化窗口": lambda: {"type": "command", "cmd": "最小化窗口", "params": ["窗口标题", "", "", "", "", "", "", "", ""]},
    "最大化窗口": lambda: {"type": "command", "cmd": "最大化窗口", "params": ["窗口标题", "", "", "", "", "", "", "", ""]},
    "获取窗口位置": lambda: {"type": "command", "cmd": "获取窗口位置", "params": ["窗口标题", "x", "y", "w", "h", "", "", "", ""]},
    "等待窗口":   lambda: {"type": "command", "cmd": "等待窗口", "params": ["窗口标题", "10", "存在", "", "", "", "", "", ""]},
    "屏幕截图":   lambda: {"type": "command", "cmd": "截屏", "params": ["shot", "", "", "", "", "", "", "", ""]},
    "打开程序":   lambda: {"type": "command", "cmd": "代码", "params": ["run_program", "", "", "", "", "", "", "", ""]},
    "浏览文件":   lambda: {"type": "command", "cmd": "代码", "params": ["browse_file", "", "", "", "", "", "", "", ""]},
    "读取剪贴板": lambda: {"type": "command", "cmd": "读取剪贴板", "params": ["clip", "", "", "", "", "", "", "", ""]},
    "字符串处理": lambda: {"type": "command", "cmd": "字符串处理", "params": ["src", "截取", "0,5", "dst", "", "", "", "", ""]},
    "数学运算":   lambda: {"type": "command", "cmd": "数学运算", "params": ["a + b", "result", "", "", "", "", "", "", ""]},
    "打开网页":   lambda: {"type": "command", "cmd": "打开网页", "params": ["https://", "", "", "", "", "", "", "", ""]},
    "浏览器点击": lambda: {"type": "command", "cmd": "浏览器点击", "params": ["#btn", "", "", "", "", "", "", "", ""]},
    "浏览器输入": lambda: {"type": "command", "cmd": "浏览器输入", "params": ["#input", "text", "", "", "", "", "", "", ""]},
    "等待元素":   lambda: {"type": "command", "cmd": "等待元素", "params": ["#el", "10", "出现", "", "", "", "", "", ""]},
    "浏览器截图": lambda: {"type": "command", "cmd": "浏览器截图", "params": ["page", "page", "", "", "", "", "", "", ""]},
}

# 最近使用记录 (存入 APP_ROOT/recent_workflow.json)
_WF_RECENT = []
_WF_CLIPBOARD = None  # 复制/粘贴缓冲区


# ── 操作库功能 ──

def _wf_insert_step(step, label=None):
    """将步骤插入到当前选中位置之后 (无选中则追加末尾)。

    label: 操作库显示名 (用于最近使用记录, 保证与 _WF_LIB_ACTIONS 键一致)。
    """
    import copy
    sel = wf_tree.selection()
    if sel:
        idx = wf_tree.index(sel[0])
        _wf_data["steps"].insert(idx + 1, copy.deepcopy(step))
    else:
        _wf_data["steps"].append(copy.deepcopy(step))
    _wf_refresh_tree()
    _wf_add_recent(step, label)


def _wf_populate_library(filter_text=""):
    """填充操作库分类树 (支持搜索过滤)。"""
    wf_lib_tree.delete(*wf_lib_tree.get_children())
    filter_text = (filter_text or "").strip().lower()
    for category, actions in _WF_LIB_CATEGORIES:
        # 过滤
        if filter_text:
            visible = [a for a in actions if filter_text in a.lower()]
            if not visible:
                continue
        else:
            visible = actions
        cat_id = wf_lib_tree.insert("", "end", text="📁 {}".format(category), open=True)
        for act in visible:
            iid = "{}_{}".format(category, act)
            wf_lib_tree.insert(cat_id, "end", iid=iid, text="  {}".format(act))
            wf_lib_tree.tag_configure(act, foreground=C["fgb"])
    # 展开根
    for cid in wf_lib_tree.get_children():
        wf_lib_tree.item(cid, open=True)


def _wf_filter_library():
    """根据搜索框文本过滤操作库。"""
    _wf_populate_library(wf_lib_search.get())


def _wf_library_action_name(iid):
    """从操作库 Treeview item 反查动作显示名 (去掉前缀)。"""
    txt = wf_lib_tree.item(iid, "text").strip()
    return txt


def _wf_add_from_library():
    """操作库双击: 添加节点到工作流。"""
    sel = wf_lib_tree.selection()
    if not sel:
        return
    iid = sel[0]
    # 忽略分类节点 (有子节点)
    if wf_lib_tree.get_children(iid):
        return
    action = _wf_library_action_name(iid)
    maker = _WF_LIB_ACTIONS.get(action)
    if maker:
        step = maker()
        _wf_insert_step(step, label=action)
        from utils import show_toast
        show_toast(root, "已添加: {}".format(action), "success")


def _wf_library_context_menu(event):
    """操作库右键: 快速添加到末尾。"""
    iid = wf_lib_tree.identify_row(event.y)
    if not iid:
        return
    wf_lib_tree.selection_set(iid)
    action = _wf_library_action_name(iid)
    if not action or wf_lib_tree.get_children(iid):
        return
    menu = tkinter.Menu(root, tearoff=0, bg=C["bgc"], fg=C["fgt"],
        activebackground=C["ac"], activeforeground="white")
    menu.add_command(label="添加到末尾",
        command=lambda: (_wf_insert_step(_WF_LIB_ACTIONS[action](), label=action)))
    try:
        menu.tk_popup(event.x_root, event.y_root)
    finally:
        menu.grab_release()


def _wf_add_recent(step, label=None):
    """记录最近使用的节点 (最多 5 个, 存 recent_workflow.json)。

    label: 操作库显示名 (与 _WF_LIB_ACTIONS 键一致, 保证最近使用可点击回填)。
    """
    global _WF_RECENT
    label = label or step.get("cmd", "") or step.get("type", "")
    if not label:
        return
    if label in _WF_RECENT:
        _WF_RECENT.remove(label)
    _WF_RECENT.insert(0, label)
    _WF_RECENT = _WF_RECENT[:5]
    try:
        with open(os.path.join(APP_ROOT, "recent_workflow.json"), "w", encoding="utf-8") as f:
            json.dump(_WF_RECENT, f, ensure_ascii=False)
    except Exception:
        pass
    _wf_refresh_recent()


def _wf_refresh_recent():
    """刷新最近使用列表。"""
    if wf_recent_list is None:
        return
    wf_recent_list.delete(0, "end")
    for label in _WF_RECENT:
        wf_recent_list.insert("end", label)


def _wf_load_recent():
    """启动时加载最近使用记录。"""
    global _WF_RECENT
    try:
        fp = os.path.join(APP_ROOT, "recent_workflow.json")
        if os.path.exists(fp):
            with open(fp, encoding="utf-8") as f:
                _WF_RECENT = json.load(f)[:5]
    except Exception:
        _WF_RECENT = []


def _wf_add_recent_clicked():
    """点击最近使用项: 添加对应节点。"""
    sel = wf_recent_list.curselection()
    if not sel:
        return
    label = wf_recent_list.get(sel[0])
    maker = _WF_LIB_ACTIONS.get(label)
    if maker:
        _wf_insert_step(maker(), label=label)

def _wf_render_flowchart(*args):
    """在 Canvas 上绘制工作流流程图：彩色节点 + 箭头连线"""
    wf_flow_canvas.delete("all")
    steps = _wf_data.get("steps", [])
    if not steps:
        wf_flow_canvas.create_text(200, 60, text="暂无步骤\n点击 [+ 脚本] 等按钮添加",
            font=("Microsoft YaHei UI", 10), fill=C["fgm"], anchor="center")
        wf_flow_canvas.configure(scrollregion=(0, 0, 400, 150))
        return

    canvas_w = _NODE_W + 80
    node_positions = []
    y = 20

    for i, step in enumerate(steps):
        stype = step.get("type", "script")
        disabled = step.get("enabled", True) is False
        color = _FLOW_COLORS.get(stype, "#6B7280")
        icon = _FLOW_ICONS.get(stype, "?")
        label = TYPE_LABELS.get(stype, stype)
        comment = step.get("comment", "")

        # 节点标签文本
        if stype == "script":
            detail = os.path.basename(step.get("path", ""))[:20] or "未选择"
        elif stype == "parallel":
            detail = "{}子步骤".format(len(step.get("steps", [])))
        elif stype == "condition":
            detail = step.get("if", "")[:18] or "条件"
        elif stype == "wait":
            detail = "{}s".format(step.get("seconds", 1))
        elif stype == "loop":
            detail = "{}次".format(step.get("times", "1"))[:18]
        elif stype == "command":
            detail = "{}".format(step.get("cmd", ""))[:18]
        elif stype == "variable":
            detail = "{}={}".format(step.get("var_name", ""), step.get("var_value", ""))[:20]
        elif stype == "log":
            detail = "{}".format(step.get("text", ""))[:18]
        else:
            detail = str(step)[:20]

        if comment:
            detail = "{} #{}".format(detail, comment[:8])
        # 禁用节点前缀
        if disabled:
            display = "⚪ {} #{} | {} (禁用)".format(icon, i + 1, detail)
        else:
            display = "{} {} #{} | {}".format(icon, label, i + 1, detail)

        x = 20
        # 执行高亮: 当前执行步骤绿色边框
        outline_color = _darken(color)
        outline_width = 2
        try:
            from workflow import workflow_engine
            if workflow_engine.current_step == i:
                outline_color = C["sc"]
                outline_width = 3
            elif workflow_engine._step_results.get(i) == "error":
                outline_color = C["dg"]
                outline_width = 3
        except Exception:
            pass
        if disabled:
            color = "#94A3B8"  # 禁用节点灰色

        # 绘制节点矩形
        node_id = wf_flow_canvas.create_rectangle(
            x, y, x + _NODE_W, y + _NODE_H,
            fill=color, outline=outline_color, width=outline_width,
            tags=("node", "node_{}".format(i)))
        # 节点文字
        text_id = wf_flow_canvas.create_text(
            x + 8, y + _NODE_H // 2,
            text=display, anchor="w",
            font=(*FONT_SMALL, "bold"),
            fill="white", tags=("node", "node_{}".format(i)))

        # 节点阴影效果 (深色半透明 — 根据主题切换颜色)
        shadow_color = "#334155" if state.DARK_MODE else "#CBD5E0"
        shadow_id = wf_flow_canvas.create_rectangle(
            x + 2, y + 2, x + _NODE_W + 2, y + _NODE_H + 2,
            fill=shadow_color, outline="", tags=("shadow",))
        wf_flow_canvas.tag_lower(shadow_id, node_id)

        # 绑定双击编辑
        for tag in ("node_{}".format(i),):
            wf_flow_canvas.tag_bind(tag, "<Double-1>",
                lambda e, idx=i: _wf_edit_step_by_index(idx))

        node_positions.append((x, y))
        y += _NODE_GAP

    # 绘制箭头连线
    for i in range(len(steps) - 1):
        x1 = 20 + _NODE_W // 2
        y1 = node_positions[i][1] + _NODE_H
        x2 = 20 + _NODE_W // 2
        y2 = node_positions[i + 1][1]
        _draw_arrow(wf_flow_canvas, x1, y1, x2, y2, color=C["fgm"])

    # 更新滚动区域
    total_h = y + 20
    wf_flow_canvas.configure(scrollregion=(0, 0, canvas_w, max(total_h, 200)))

    # 节点拖拽绑定
    for i in range(len(steps)):
        wf_flow_canvas.tag_bind("node_{}".format(i), "<Button-1>",
            lambda e, idx=i: _wf_drag_start(e, idx))
        wf_flow_canvas.tag_bind("node_{}".format(i), "<B1-Motion>",
            lambda e, idx=i: _wf_drag_move(e, idx))
        wf_flow_canvas.tag_bind("node_{}".format(i), "<ButtonRelease-1>",
            lambda e, idx=i: _wf_drag_end(e, idx))

def _draw_arrow(canvas, x1, y1, x2, y2, color="#94A3B8"):
    """绘制带箭头的连线"""
    canvas.create_line(x1, y1, x2, y2 - 6, fill=color, width=2, tags="arrow")
    # 箭头三角形
    canvas.create_polygon(
        x2 - 5, y2 - 6, x2 + 5, y2 - 6, x2, y2,
        fill=color, outline=color, tags="arrow")

def _wf_edit_step_by_index(idx):
    """通过索引编辑步骤（供流程图双击使用）"""
    if idx >= len(_wf_data["steps"]):
        return
    # 选中 Treeview 对应行
    children = wf_tree.get_children()
    if idx < len(children):
        wf_tree.selection_set(children[idx])
        wf_tree.see(children[idx])
    _wf_edit_step()

def _wf_drag_start(event, idx):
    """开始拖拽节点"""
    global _wf_drag_idx, _wf_drag_start_y
    _wf_drag_idx = idx
    _wf_drag_start_y = event.y

def _wf_drag_move(event, idx):
    """拖拽移动中"""
    global _wf_drag_idx, _wf_drag_start_y
    if _wf_drag_idx is None or _wf_drag_idx != idx:
        return
    dy = event.y - _wf_drag_start_y
    # 移动节点图形
    for item in wf_flow_canvas.find_withtag("node_{}".format(idx)):
        wf_flow_canvas.move(item, 0, dy)
    _wf_drag_start_y = event.y

def _wf_drag_end(event, idx):
    """结束拖拽：根据位置交换步骤顺序"""
    global _wf_drag_idx
    if _wf_drag_idx is None:
        return
    steps = _wf_data["steps"]
    n = len(steps)
    if n < 2:
        _wf_drag_idx = None
        _wf_render_flowchart()
        return

    # 根据最终 y 位置计算新索引
    gap = _NODE_GAP
    start_y = 20
    new_idx = round((event.y - start_y) / gap)
    new_idx = max(0, min(n - 1, new_idx))

    old_idx = _wf_drag_idx
    _wf_drag_idx = None

    if new_idx != old_idx:
        # 交换步骤
        step = steps.pop(old_idx)
        steps.insert(new_idx, step)
        _wf_refresh_tree()

    _wf_render_flowchart()

def _wf_refresh_tree():
    """刷新工作流步骤列表"""
    wf_tree.delete(*wf_tree.get_children())
    for i, step in enumerate(_wf_data.get("steps", [])):
        stype = step.get("type", "script")
        label = TYPE_LABELS.get(stype, stype)
        disabled = step.get("enabled", True) is False
        comment = step.get("comment", "")
        if stype == "script":
            detail = step.get("path", "")
        elif stype == "parallel":
            count = len(step.get("steps", []))
            detail = "{} 个子步骤".format(count)
        elif stype == "condition":
            detail = "if {} then...".format(step.get("if", ""))
        elif stype == "wait":
            detail = "{}s".format(step.get("seconds", 1))
        elif stype == "loop":
            detail = "循环 {} 次, {} 子步骤".format(step.get("times", "1"), len(step.get("steps", [])))
        elif stype == "command":
            detail = "{} {}".format(step.get("cmd", ""), step.get("params", [])[:2])
        elif stype == "variable":
            detail = "{} = {}".format(step.get("var_name", ""), step.get("var_value", ""))
        elif stype == "log":
            detail = step.get("text", "")
        else:
            detail = str(step)
        if comment:
            detail = "#{} {}".format(comment, detail)
        if disabled:
            label = "⚪ " + label
        tag = "step_{}".format(i)
        wf_tree.insert("", "end", text=str(i + 1), values=(label, detail), tags=(tag,))
        wf_tree.tag_configure(tag, foreground=C["fgm"] if disabled else C["fgb"])
    _wf_render_flowchart()

def _wf_new():
    _wf_data["name"] = "未命名工作流"
    _wf_data["steps"] = []
    global _wf_file
    _wf_file = None
    wf_name_var.set("未命名工作流")
    _wf_refresh_tree()

def _wf_open():
    fp = filedialog.askopenfilename(title="打开工作流",
        filetypes=[('JSON', '*.json'), ('YAML', '*.yaml')], initialdir=APP_ROOT)
    if fp:
        try:
            from workflow import parse_workflow
            data = parse_workflow(fp)
            _wf_data["name"] = data.get("name", os.path.basename(fp))
            _wf_data["steps"] = data.get("steps", [])
            global _wf_file
            _wf_file = fp
            wf_name_var.set(_wf_data["name"])
            _wf_refresh_tree()
            from utils import show_toast
            show_toast(root, "已加载: {} ({}步骤)".format(_wf_data["name"], len(_wf_data["steps"])), "success")
        except Exception as e:
            messagebox.showerror("加载失败", str(e))

def _wf_save():
    if not _wf_data.get("steps"):
        messagebox.showwarning("提示", "工作流为空")
        return
    _wf_data["name"] = wf_name_var.get()
    fp = filedialog.asksaveasfilename(title="保存工作流",
        defaultextension=".json", filetypes=[('JSON', '*.json')],
        initialdir=APP_ROOT)
    if fp:
        try:
            with open(fp, "w", encoding="utf-8") as f:
                json.dump(_wf_data, f, indent=2, ensure_ascii=False)
            global _wf_file
            _wf_file = fp
            from utils import show_toast
            show_toast(root, "已保存 ({}步骤)".format(len(_wf_data["steps"])), "success")
        except Exception as e:
            messagebox.showerror("保存失败", str(e))

def _wf_add_step(stype):
    import copy
    if stype == "script":
        # 选择脚本文件
        fp = filedialog.askopenfilename(title="选择脚本",
            filetypes=[('Excel', '*.xls'), ('All', '*.*')],
            initialdir=APP_ROOT)
        if fp:
            # 使用相对路径（如果在工作流同目录下）
            if _wf_file and os.path.commonprefix([fp, os.path.dirname(_wf_file)]):
                fp = os.path.relpath(fp, os.path.dirname(_wf_file))
            step = {"type": "script", "path": fp.replace("\\", "/")}
        else:
            return
    elif stype == "parallel":
        step = {"type": "parallel", "steps": []}
    elif stype == "condition":
        step = {"type": "condition", "if": "${var} == True", "then": "", "else": ""}
    elif stype == "wait":
        step = {"type": "wait", "seconds": 5}
    elif stype == "variable":
        step = {"type": "variable", "var_name": "var", "var_value": ""}
    elif stype == "loop":
        step = {"type": "loop", "times": "3", "steps": []}
    else:
        return
    step["enabled"] = True
    step["comment"] = ""
    # 插入到选中位置之后
    sel = wf_tree.selection()
    if sel:
        idx = wf_tree.index(sel[0])
        _wf_data["steps"].insert(idx + 1, step)
    else:
        _wf_data["steps"].append(step)
    _wf_refresh_tree()

def _wf_edit_step(event=None):
    """双击编辑步骤"""
    sel = wf_tree.selection()
    if not sel:
        return
    idx = wf_tree.index(sel[0])
    if idx >= len(_wf_data["steps"]):
        return
    step = _wf_data["steps"][idx]
    stype = step.get("type", "script")

    dlg = tkinter.Toplevel(root)
    dlg.title("编辑步骤 {}".format(idx + 1))
    dlg.geometry("420x300")
    dlg.transient(root)
    dlg.grab_set()
    dlg.configure(bg=C["bgc"])
    _set_window_icon(dlg)

    tkinter.Label(dlg, text="类型: {}".format(TYPE_LABELS.get(stype, stype)),
        font=FONT_TITLE, bg=C["bgc"], fg=C["fgt"]).pack(pady=(10, 6))

    if stype == "script":
        tkinter.Label(dlg, text="脚本路径:", bg=C["bgc"], fg=C["fgb"]).pack()
        path_var = tkinter.StringVar(value=step.get("path", ""))
        pf = tkinter.Frame(dlg, bg=C["bgc"])
        pf.pack(pady=4)
        tkinter.Entry(pf, textvariable=path_var, width=40,
            font=FONT_BODY, bg=C["ebg"], fg=C["fgb"]).pack(side="left", padx=(0, 4))
        def _browse():
            fp = filedialog.askopenfilename(filetypes=[('Excel', '*.xls')])
            if fp:
                if _wf_file and os.path.commonprefix([fp, os.path.dirname(_wf_file)]):
                    fp = os.path.relpath(fp, os.path.dirname(_wf_file))
                path_var.set(fp.replace("\\", "/"))
        tkinter.Button(pf, text="浏览", command=_browse, font=FONT_SMALL,
            bg=C["ac"], fg="white").pack(side="left")
        def _save_script():
            step["path"] = path_var.get()
            _wf_refresh_tree()
            dlg.destroy()
        tkinter.Button(dlg, text="确定", command=_save_script,
            font=FONT_BUTTON, bg=C["ac"], fg="white").pack(pady=10)

    elif stype == "parallel":
        sub_steps = step.get("steps", [])
        tkinter.Label(dlg, text="子步骤 ({} 个):".format(len(sub_steps)),
            bg=C["bgc"], fg=C["fgb"]).pack(pady=4)
        sf = tkinter.Frame(dlg, bg=C["bgc"])
        sf.pack(fill="both", expand=True, padx=10, pady=4)
        sub_list = tkinter.Listbox(sf, font=FONT_BODY, bg=C["ebg"], fg=C["fgb"],
            selectbackground=C["ac"], selectforeground="white", height=6)
        sub_list.pack(side="left", fill="both", expand=True)
        for s in sub_steps:
            sub_list.insert("end", s.get("path", str(s)))
        def _add_sub():
            fp = filedialog.askopenfilename(filetypes=[('Excel', '*.xls')])
            if fp:
                if _wf_file and os.path.commonprefix([fp, os.path.dirname(_wf_file)]):
                    fp = os.path.relpath(fp, os.path.dirname(_wf_file))
                sub_steps.append({"type": "script", "path": fp.replace("\\", "/")})
                sub_list.insert("end", fp)
        def _del_sub():
            sel = sub_list.curselection()
            if sel:
                idx_s = sel[0]
                del sub_steps[idx_s]
                sub_list.delete(idx_s)
        bf = tkinter.Frame(dlg, bg=C["bgc"])
        bf.pack()
        tkinter.Button(bf, text="+ 添加", command=_add_sub,
            font=FONT_SMALL, bg=C["ac"], fg="white").pack(side="left", padx=2, pady=4)
        tkinter.Button(bf, text="- 删除", command=_del_sub,
            font=FONT_SMALL, bg=C["dg"], fg="white").pack(side="left", padx=2, pady=4)
        def _save_parallel():
            step["steps"] = sub_steps
            _wf_refresh_tree()
            dlg.destroy()
        tkinter.Button(dlg, text="确定", command=_save_parallel,
            font=FONT_BUTTON, bg=C["ac"], fg="white").pack(pady=4)

    elif stype == "condition":
        tkinter.Label(dlg, text="条件表达式:", bg=C["bgc"], fg=C["fgb"]).pack(pady=(10, 2))
        cond_var = tkinter.StringVar(value=step.get("if", ""))
        tkinter.Entry(dlg, textvariable=cond_var, width=40,
            font=("Consolas", 10), bg=C["ebg"], fg=C["fgb"]).pack(pady=4)
        tkinter.Label(dlg, text="(使用 ${var} 引用变量)", font=FONT_SMALL,
            bg=C["bgc"], fg=C["fgm"]).pack()
        tkinter.Label(dlg, text="成立时执行:", bg=C["bgc"], fg=C["fgb"]).pack(pady=(8, 2))
        then_var = tkinter.StringVar(value=step.get("then", ""))
        tkinter.Entry(dlg, textvariable=then_var, width=40,
            font=FONT_BODY, bg=C["ebg"], fg=C["fgb"]).pack(pady=2)
        tkinter.Label(dlg, text="不成立时执行:", bg=C["bgc"], fg=C["fgb"]).pack(pady=(4, 2))
        else_var = tkinter.StringVar(value=step.get("else", ""))
        tkinter.Entry(dlg, textvariable=else_var, width=40,
            font=FONT_BODY, bg=C["ebg"], fg=C["fgb"]).pack(pady=2)
        def _save_cond():
            step["if"] = cond_var.get()
            step["then"] = then_var.get() if then_var.get() else ""
            step["else"] = else_var.get() if else_var.get() else ""
            _wf_refresh_tree()
            dlg.destroy()
        tkinter.Button(dlg, text="确定", command=_save_cond,
            font=FONT_BUTTON, bg=C["ac"], fg="white").pack(pady=10)

    elif stype == "wait":
        tkinter.Label(dlg, text="等待秒数:", bg=C["bgc"], fg=C["fgb"]).pack(pady=(10, 4))
        sec_var = tkinter.StringVar(value=str(step.get("seconds", 1)))
        tkinter.Entry(dlg, textvariable=sec_var, width=10,
            font=FONT_BODY, bg=C["ebg"], fg=C["fgb"]).pack(pady=4)
        def _save_wait():
            try:
                step["seconds"] = float(sec_var.get())
            except ValueError:
                step["seconds"] = 1
            _wf_refresh_tree()
            dlg.destroy()
        tkinter.Button(dlg, text="确定", command=_save_wait,
            font=FONT_BUTTON, bg=C["ac"], fg="white").pack(pady=10)

    elif stype == "command":
        # 命令节点: 命令名下拉 + 9 参数表格
        import commands
        _cmd_names = commands.list_names()
        tkinter.Label(dlg, text="命令:", bg=C["bgc"], fg=C["fgb"]).pack(pady=(10, 2))
        cmd_var = tkinter.StringVar(value=step.get("cmd", ""))
        cmd_combo = ttk.Combobox(dlg, textvariable=cmd_var, values=_cmd_names,
            state="readonly", width=24)
        cmd_combo.pack(pady=2)
        # 参数输入 (9 格)
        tkinter.Label(dlg, text="参数 (9 个):", bg=C["bgc"], fg=C["fgb"]).pack(pady=(8, 2))
        params = step.get("params", []) or [""] * 9
        while len(params) < 9:
            params.append("")
        param_vars = []
        pf = tkinter.Frame(dlg, bg=C["bgc"])
        pf.pack(pady=2)
        for i in range(9):
            pv = tkinter.StringVar(value=str(params[i]) if i < len(params) else "")
            param_vars.append(pv)
            tkinter.Entry(pf, textvariable=pv, width=5, font=("Consolas", 8),
                bg=C["ebg"], fg=C["fgb"], relief="solid", bd=1).grid(
                row=i // 3, column=i % 3, padx=2, pady=2)
        def _save_command():
            step["cmd"] = cmd_var.get()
            step["params"] = [v.get() for v in param_vars]
            _wf_refresh_tree()
            dlg.destroy()
        tkinter.Button(dlg, text="确定", command=_save_command,
            font=FONT_BUTTON, bg=C["ac"], fg="white").pack(pady=10)

    elif stype == "loop":
        # 循环节点: 次数/条件 + 子步骤
        tkinter.Label(dlg, text="循环次数/条件:", bg=C["bgc"], fg=C["fgb"]).pack(pady=(10, 2))
        loop_var = tkinter.StringVar(value=str(step.get("times", "1")))
        tkinter.Entry(dlg, textvariable=loop_var, width=30,
            font=("Consolas", 10), bg=C["ebg"], fg=C["fgb"]).pack(pady=2)
        tkinter.Label(dlg, text="(数字=固定次数, 或 ${x} < 5 条件循环)", font=FONT_SMALL,
            bg=C["bgc"], fg=C["fgm"]).pack()
        # 子步骤管理
        sub_steps = step.get("steps", []) or []
        tkinter.Label(dlg, text="子步骤 ({} 个):".format(len(sub_steps)),
            bg=C["bgc"], fg=C["fgb"]).pack(pady=(8, 2))
        sf = tkinter.Frame(dlg, bg=C["bgc"])
        sf.pack(fill="both", expand=True, padx=10, pady=2)
        sub_list = tkinter.Listbox(sf, font=FONT_BODY, bg=C["ebg"], fg=C["fgb"],
            selectbackground=C["ac"], selectforeground="white", height=4)
        sub_list.pack(side="left", fill="both", expand=True)
        for s in sub_steps:
            sub_list.insert("end", s.get("path", s.get("cmd", str(s))))
        def _add_sub():
            fp = filedialog.askopenfilename(filetypes=[('Excel', '*.xls')])
            if fp:
                if _wf_file and os.path.commonprefix([fp, os.path.dirname(_wf_file)]):
                    fp = os.path.relpath(fp, os.path.dirname(_wf_file))
                sub_steps.append({"type": "script", "path": fp.replace("\\", "/")})
                sub_list.insert("end", fp)
        def _del_sub():
            sel = sub_list.curselection()
            if sel:
                idx_s = sel[0]
                del sub_steps[idx_s]
                sub_list.delete(idx_s)
        bf = tkinter.Frame(dlg, bg=C["bgc"])
        bf.pack()
        tkinter.Button(bf, text="+ 添加", command=_add_sub,
            font=FONT_SMALL, bg=C["ac"], fg="white").pack(side="left", padx=2, pady=2)
        tkinter.Button(bf, text="- 删除", command=_del_sub,
            font=FONT_SMALL, bg=C["dg"], fg="white").pack(side="left", padx=2, pady=2)
        def _save_loop():
            if not sub_steps:
                messagebox.showwarning("提示", "循环节点至少需要 1 个子步骤，请先添加")
                return
            step["times"] = loop_var.get().strip()
            step["steps"] = sub_steps
            _wf_refresh_tree()
            dlg.destroy()
        tkinter.Button(dlg, text="确定", command=_save_loop,
            font=FONT_BUTTON, bg=C["ac"], fg="white").pack(pady=4)

    elif stype == "variable":
        # 变量节点
        tkinter.Label(dlg, text="变量名:", bg=C["bgc"], fg=C["fgb"]).pack(pady=(10, 2))
        vn_var = tkinter.StringVar(value=step.get("var_name", ""))
        tkinter.Entry(dlg, textvariable=vn_var, width=30,
            font=("Consolas", 10), bg=C["ebg"], fg=C["fgb"]).pack(pady=2)
        tkinter.Label(dlg, text="变量值:", bg=C["bgc"], fg=C["fgb"]).pack(pady=(8, 2))
        vv_var = tkinter.StringVar(value=step.get("var_value", ""))
        tkinter.Entry(dlg, textvariable=vv_var, width=30,
            font=("Consolas", 10), bg=C["ebg"], fg=C["fgb"]).pack(pady=2)
        def _save_var():
            step["var_name"] = vn_var.get().strip()
            step["var_value"] = vv_var.get()
            _wf_refresh_tree()
            dlg.destroy()
        tkinter.Button(dlg, text="确定", command=_save_var,
            font=FONT_BUTTON, bg=C["ac"], fg="white").pack(pady=10)

    elif stype == "log":
        # 日志节点
        tkinter.Label(dlg, text="日志内容:", bg=C["bgc"], fg=C["fgb"]).pack(pady=(10, 2))
        lg_var = tkinter.StringVar(value=step.get("text", ""))
        tkinter.Entry(dlg, textvariable=lg_var, width=40,
            font=FONT_BODY, bg=C["ebg"], fg=C["fgb"]).pack(pady=4)
        tkinter.Label(dlg, text="(支持 ${var} 变量引用)", font=FONT_SMALL,
            bg=C["bgc"], fg=C["fgm"]).pack()
        def _save_log():
            step["text"] = lg_var.get()
            _wf_refresh_tree()
            dlg.destroy()
        tkinter.Button(dlg, text="确定", command=_save_log,
            font=FONT_BUTTON, bg=C["ac"], fg="white").pack(pady=10)

    # ── 通用: 启用/禁用 + 备注 ──
    common_frame = tkinter.Frame(dlg, bg=C["bgc"])
    common_frame.pack(fill="x", padx=10, pady=(4, 2))
    enabled_var = tkinter.BooleanVar(value=step.get("enabled", True))
    ttk.Checkbutton(common_frame, text="启用此步骤",
        variable=enabled_var).pack(side="left")
    cmt_var = tkinter.StringVar(value=step.get("comment", ""))
    tkinter.Label(common_frame, text="备注:", font=FONT_SMALL,
        bg=C["bgc"], fg=C["fgm"]).pack(side="left", padx=(8, 2))
    tkinter.Entry(common_frame, textvariable=cmt_var, width=18,
        font=FONT_SMALL, bg=C["ebg"], fg=C["fgb"]).pack(side="left")

    # 统一保存通用字段 (enabled/comment) — 无论点击哪个类型的"确定"或关闭窗口都会生效
    def _apply_common():
        step["enabled"] = enabled_var.get()
        step["comment"] = cmt_var.get()

    # 包装 dlg.destroy: 任何关闭路径都先保存通用字段
    _orig_destroy = dlg.destroy
    def _destroy_with_common():
        try:
            _apply_common()
        except Exception:
            pass
        _wf_refresh_tree()
        _orig_destroy()
    dlg.destroy = _destroy_with_common
    dlg.bind("<Escape>", lambda e: dlg.destroy())
wf_tree.bind("<Double-1>", _wf_edit_step)

def _wf_export_screenshot():
    """导出流程图截图为 PNG"""
    if not _wf_data.get("steps"):
        messagebox.showwarning("提示", "工作流为空，无法导出")
        return
    fp = filedialog.asksaveasfilename(title="导出流程图",
        defaultextension=".png", filetypes=[('PNG', '*.png')],
        initialdir=APP_ROOT)
    if fp:
        try:
            _load_pil()
            # 确保流程图已渲染
            wf_flow_canvas.update_idletasks()
            x = wf_flow_canvas.winfo_rootx()
            y = wf_flow_canvas.winfo_rooty()
            w = wf_flow_canvas.winfo_width()
            h = wf_flow_canvas.winfo_height()
            if w > 0 and h > 0:
                img = ImageGrab.grab(bbox=(x, y, x + w, y + h))
                img.save(fp)
                from utils import show_toast
                show_toast(root, "流程图已保存: {}".format(os.path.basename(fp)), "success")
                log1("流程图导出: {}".format(fp))
            else:
                messagebox.showwarning("提示", "流程图区域不可见，请先切换到工作流Tab")
        except Exception as e:
            messagebox.showerror("导出失败", str(e))


def _wf_delete_step():
    sel = wf_tree.selection()
    if sel:
        idx = wf_tree.index(sel[0])
        if idx < len(_wf_data["steps"]):
            del _wf_data["steps"][idx]
            _wf_refresh_tree()

def _wf_move_up():
    sel = wf_tree.selection()
    if sel:
        idx = wf_tree.index(sel[0])
        if idx > 0:
            _wf_data["steps"][idx], _wf_data["steps"][idx - 1] = \
                _wf_data["steps"][idx - 1], _wf_data["steps"][idx]
            _wf_refresh_tree()

def _wf_move_down():
    sel = wf_tree.selection()
    if sel:
        idx = wf_tree.index(sel[0])
        if idx < len(_wf_data["steps"]) - 1:
            _wf_data["steps"][idx], _wf_data["steps"][idx + 1] = \
                _wf_data["steps"][idx + 1], _wf_data["steps"][idx]
            _wf_refresh_tree()

wf_tree.bind("<Delete>", lambda e: _wf_delete_step())

# 右键菜单
def _wf_clone_step():
    """克隆当前选中的步骤"""
    sel = wf_tree.selection()
    if not sel: return
    idx = wf_tree.index(sel[0])
    if idx < len(_wf_data["steps"]):
        import copy
        clone = copy.deepcopy(_wf_data["steps"][idx])
        _wf_data["steps"].insert(idx + 1, clone)
        _wf_refresh_tree()

def _wf_copy_step():
    """复制选中步骤到缓冲区"""
    global _WF_CLIPBOARD
    sel = wf_tree.selection()
    if not sel: return
    idx = wf_tree.index(sel[0])
    if idx < len(_wf_data["steps"]):
        import copy
        _WF_CLIPBOARD = copy.deepcopy(_wf_data["steps"][idx])
        from utils import show_toast
        show_toast(root, "已复制步骤", "info")

def _wf_paste_step():
    """粘贴缓冲区的步骤到选中位置之后"""
    global _WF_CLIPBOARD
    if _WF_CLIPBOARD is None:
        return
    import copy
    sel = wf_tree.selection()
    if sel:
        idx = wf_tree.index(sel[0])
        _wf_data["steps"].insert(idx + 1, copy.deepcopy(_WF_CLIPBOARD))
    else:
        _wf_data["steps"].append(copy.deepcopy(_WF_CLIPBOARD))
    _wf_refresh_tree()
    from utils import show_toast
    show_toast(root, "已粘贴步骤", "success")

def _wf_toggle_enable():
    """禁用/启用选中步骤"""
    sel = wf_tree.selection()
    if not sel: return
    idx = wf_tree.index(sel[0])
    if idx < len(_wf_data["steps"]):
        step = _wf_data["steps"][idx]
        step["enabled"] = not step.get("enabled", True)
        _wf_refresh_tree()
        from utils import show_toast
        show_toast(root, "已{}步骤".format("启用" if step["enabled"] else "禁用"),
                   "success" if step["enabled"] else "warning")

def _wf_toggle_comment():
    """为选中步骤添加/移除注释标记"""
    sel = wf_tree.selection()
    if not sel: return
    idx = wf_tree.index(sel[0])
    if idx < len(_wf_data["steps"]):
        step = _wf_data["steps"][idx]
        if step.get("comment"):
            step["comment"] = ""
        else:
            # 提示输入注释
            dlg = tkinter.Toplevel(root)
            dlg.title("步骤注释")
            dlg.geometry("360x120")
            dlg.transient(root); dlg.grab_set()
            dlg.configure(bg=C["bgc"])
            _set_window_icon(dlg)
            tkinter.Label(dlg, text="注释内容:", font=FONT_BODY,
                bg=C["bgc"], fg=C["fgb"]).pack(pady=(10, 4))
            entry = tkinter.Entry(dlg, font=FONT_BODY, width=40,
                bg=C["ebg"], fg=C["fgb"])
            entry.pack(pady=4, padx=10)
            entry.focus_set()
            def _save():
                step["comment"] = entry.get().strip()
                _wf_refresh_tree()
                dlg.destroy()
            def _cancel():
                step["comment"] = ""
                dlg.destroy()
            bf = tkinter.Frame(dlg, bg=C["bgc"])
            bf.pack(pady=6)
            tkinter.Button(bf, text="确定", command=_save,
                font=FONT_BUTTON, bg=C["ac"], fg="white").pack(side="left", padx=4)
            tkinter.Button(bf, text="取消", command=_cancel,
                font=FONT_BUTTON, bg=C["bgc"], fg=C["fgb"]).pack(side="left", padx=4)
            dlg.bind("<Return>", lambda e: _save())
            dlg.bind("<Escape>", lambda e: _cancel())
        _wf_refresh_tree()

def _wf_export_template():
    """导出工作流为模板"""
    if not _wf_data.get("steps"):
        messagebox.showwarning("提示", "工作流为空")
        return
    fp = filedialog.asksaveasfilename(title="导出工作流模板",
        defaultextension=".json", filetypes=[('JSON', '*.json')],
        initialdir=APP_ROOT)
    if fp:
        try:
            with open(fp, "w", encoding="utf-8") as f:
                json.dump(_wf_data, f, indent=2, ensure_ascii=False)
            from utils import show_toast
            show_toast(root, "模板已导出: {}".format(os.path.basename(fp)), "success")
        except Exception as e:
            messagebox.showerror("导出失败", str(e))

def _wf_context_menu(event):
    menu = tkinter.Menu(root, tearoff=0, bg=C["bgc"], fg=C["fgt"],
        activebackground=C["ac"], activeforeground="white")
    menu.add_command(label="编辑", command=_wf_edit_step)
    menu.add_separator()
    menu.add_command(label="克隆", command=_wf_clone_step)
    menu.add_command(label="复制", command=_wf_copy_step)
    menu.add_command(label="粘贴", command=_wf_paste_step)
    menu.add_separator()
    menu.add_command(label="上移", command=_wf_move_up)
    menu.add_command(label="下移", command=_wf_move_down)
    menu.add_separator()
    menu.add_command(label="禁用/启用", command=_wf_toggle_enable)
    menu.add_command(label="添加注释", command=_wf_toggle_comment)
    menu.add_separator()
    menu.add_command(label="删除", command=_wf_delete_step)
    try:
        menu.tk_popup(event.x_root, event.y_root)
    finally:
        menu.grab_release()

wf_tree.bind("<Button-3>", _wf_context_menu)

# 操作库事件绑定
wf_lib_tree.bind("<Double-1>", lambda e: _wf_add_from_library())
wf_lib_tree.bind("<Button-3>", _wf_library_context_menu)
# 初始化: 填充操作库 + 加载最近使用
_wf_load_recent()
_wf_populate_library()
_wf_refresh_recent()

def _wf_run():
    """运行工作流"""
    if not _wf_data.get("steps"):
        messagebox.showwarning("提示", "工作流为空，请先添加步骤")
        return
    if state.running:
        log1("有任务正在运行，请先停止", "warning")
        return

    state.quit2 = False
    state.quit3 = True
    state.pause_event.set()

    # 读取执行控制参数
    try:
        loop_count = int(wf_loop_var.get())
    except ValueError:
        loop_count = 1
    try:
        max_minutes = float(wf_maxmin_var.get())
    except ValueError:
        max_minutes = 0

    def _run_thread():
        try:
            from workflow import workflow_engine
            workflow_engine._variables.clear()
            base = os.path.dirname(_wf_file) if _wf_file else APP_ROOT
            wf_def = dict(_wf_data)
            wf_def["loop_count"] = loop_count
            wf_def["max_minutes"] = max_minutes
            workflow_engine.run_workflow(wf_def, base)
        except Exception as e:
            log1("工作流执行异常: {}".format(e), "error")

    t = threading.Thread(target=_run_thread, daemon=True)
    t.start()

def _wf_stop():
    from workflow import workflow_engine
    workflow_engine.stop()
    log1("工作流已停止")


def _wf_open_variable_manager():
    """打开工作流变量管理弹窗 (编辑 workflow_engine._variables)。"""
    try:
        from workflow import workflow_engine
    except Exception:
        return

    dlg = tkinter.Toplevel(root)
    dlg.title("工作流变量管理")
    dlg.geometry("420x380")
    dlg.transient(root); dlg.grab_set()
    dlg.configure(bg=C["bgc"])
    _set_window_icon(dlg)

    tkinter.Label(dlg, text="工作流共享变量", font=FONT_TITLE,
        bg=C["bgc"], fg=C["fgt"]).pack(pady=(10, 6))

    lf = tkinter.Frame(dlg, bg=C["bgc"])
    lf.pack(fill="both", expand=True, padx=10, pady=4)
    var_tree = ttk.Treeview(lf, columns=("name", "value"), show="headings", height=8)
    var_tree.heading("name", text="变量名")
    var_tree.heading("value", text="值")
    var_tree.column("name", width=150)
    var_tree.column("value", width=200)
    var_tree.pack(side="left", fill="both", expand=True)
    sy = tkinter.Scrollbar(lf, orient="vertical", command=var_tree.yview, width=6)
    sy.pack(side="right", fill="y")
    var_tree.configure(yscrollcommand=sy.set)

    def _refresh_vars():
        var_tree.delete(*var_tree.get_children())
        for k, v in sorted(workflow_engine._variables.items()):
            var_tree.insert("", "end", values=(k, str(v)))

    _refresh_vars()

    # 输入行
    entry_frame = tkinter.Frame(dlg, bg=C["bgc"])
    entry_frame.pack(fill="x", padx=10, pady=4)
    tkinter.Label(entry_frame, text="变量名:", font=FONT_SMALL,
        bg=C["bgc"], fg=C["fgm"]).pack(side="left")
    vn_entry = tkinter.Entry(entry_frame, width=12, font=FONT_SMALL,
        bg=C["ebg"], fg=C["fgb"])
    vn_entry.pack(side="left", padx=2)
    tkinter.Label(entry_frame, text="值:", font=FONT_SMALL,
        bg=C["bgc"], fg=C["fgm"]).pack(side="left", padx=(6, 2))
    vv_entry = tkinter.Entry(entry_frame, width=16, font=FONT_SMALL,
        bg=C["ebg"], fg=C["fgb"])
    vv_entry.pack(side="left", padx=2)

    def _add_var():
        name = vn_entry.get().strip()
        value = vv_entry.get()
        if not name:
            return
        # 数值转换
        try:
            if "." in value:
                value = float(value)
            else:
                value = int(value)
        except ValueError:
            pass
        workflow_engine._variables[name] = value
        _refresh_vars()
        vn_entry.delete(0, "end"); vv_entry.delete(0, "end")
        log1("工作流变量: {} = {}".format(name, value))

    def _del_var():
        sel = var_tree.selection()
        if sel:
            name = var_tree.item(sel[0], "values")[0]
            workflow_engine._variables.pop(name, None)
            _refresh_vars()

    btn_frame = tkinter.Frame(dlg, bg=C["bgc"])
    btn_frame.pack(fill="x", padx=10, pady=4)
    _btn(btn_frame, "添加", _add_var, C["ac"], "white").pack(side="left", padx=2)
    _btn(btn_frame, "删除选中", _del_var, C["dg"], "white").pack(side="left", padx=2)
    _btn(btn_frame, "清空", lambda: (workflow_engine._variables.clear(), _refresh_vars()),
         C["bgc"], C["fgb"]).pack(side="left", padx=2)

    tkinter.Button(dlg, text="关闭", command=dlg.destroy,
        font=FONT_BUTTON, bg=C["bgc"], fg=C["fgb"]).pack(pady=8)
    dlg.bind("<Escape>", lambda e: dlg.destroy())
    dlg.lift(); dlg.focus_force()


def _wf_update_workflow_highlight():
    """执行工作流时周期刷新流程图高亮 (供 _periodic 调用)。

    仅在当前步骤变化时重绘, 避免每 100ms 全量重绘导致的闪烁与 CPU 开销;
    执行结束后复位一次高亮。
    """
    try:
        from workflow import workflow_engine
        _last = getattr(_wf_update_workflow_highlight, "_last_step", -2)
        if state.running and workflow_engine.current_step >= 0:
            cur = workflow_engine.current_step
            if _last != cur:
                _wf_update_workflow_highlight._last_step = cur
                _wf_render_flowchart()
        elif _last != -1:
            # 执行结束/停止: 复位一次高亮
            _wf_update_workflow_highlight._last_step = -1
            _wf_render_flowchart()
    except Exception:
        pass

# Wire scheduler to GUI (设置窗口为独立窗口: 勾选变量在此创建并复用，下次执行标签由设置窗口注入)
state._sched_enabled_var = tkinter.BooleanVar(value=state.SCHED_ENABLED)
sched.set_gui_refs(root, state._sched_enabled_var, None)
recorder.set_root(root)

# === Recording done callback: inject recorded actions into editor ──
def _on_recording_done():
    for sd in state.recorded_actions:
        state._editor_rows.append(sd)
    _editor_sync_to_tree()
    log1("已从录制导入 {} 条命令".format(len(state.recorded_actions)))
    from utils import show_toast; show_toast(root, "录制完成 ({} 条)".format(len(state.recorded_actions)), "success")
state._on_recording_done = _on_recording_done

# ======================================================================
# Status Bar - compact
status_bar = tkinter.Frame(root,bg=C["bg"])
status_bar.grid(row=2,column=0,sticky="ew",padx=10,pady=(3,6))
status_bar.columnconfigure(0,weight=1)
status_text = tkinter.Label(status_bar,text="就绪 — 请选择脚本文件开始",
    font=FONT_SMALL,fg=C["fgm"],bg=C["bg"],anchor="w")
status_text.grid(row=0,column=0,sticky="w")

# ======================================================================
# Periodic Updates
# ======================================================================

def _periodic():
    if state._closing: return
    try: _tlog.flush(root)
    except Exception: return
    _pc = getattr(_periodic, "_cache", {})
    is_running = state.running
    is_paused = is_running and not state.pause_event.is_set()
    is_stopped = bool(state.quit2)
    is_recording = state.recording
    new_status = (1 if is_paused else 2 if is_running else 3 if is_stopped else 0)
    if _pc.get("status") != new_status:
        _pc["status"] = new_status
        if is_paused:
            status_text.config(text=" 已暂停 — 点击继续恢复执行",fg=C["wn"])
            status_dot.config(text="● 已暂停",fg=C["wn"])
        elif is_running:
            status_text.config(text=" 运行中 — 正在执行自动化任务",fg=C["sc"])
            status_dot.config(text="● 运行中",fg=C["sc"])
        elif is_stopped:
            status_text.config(text=" 已停止 — 点击开始运行重新启动",fg=C["dg"])
            status_dot.config(text="● 已停止",fg=C["dg"])
        else:
            rows_count = len(state._editor_rows) if state._editor_rows else 0
            mod_mark = " *" if state._editor_modified else ""
            ai_status = "[AI]" if state.API_KEY else ""
            status_text.config(text=" 就绪{}{}  |  {} 行  |  请选择脚本文件开始".format(
                mod_mark, " "+ai_status if ai_status else "", rows_count), fg=C["fgm"])
            status_dot.config(text="● 就绪",fg=C["fgm"])
            if _update_pending.get("ver"):
                _on_update_hint(_update_pending["ver"], "")
    if is_running and state.exec_state.get("total_rows",0)>0:
        lp=state.exec_state["loop"]; tl=state.exec_state["total_loops"]
        rw=state.exec_state["row"]; tr=state.exec_state["total_rows"]
        pct = max(0, min(100, int(rw / max(1, tr) * 100)))
        if _pc.get("pct") != pct:
            _pc["pct"] = pct; progress_bar.config(value=pct)
            el=time.time()-state.exec_state["start_time"]
            progress_label.config(text="  循环: {}/{} · 当前行: {}/{}".format(
                lp,"∞" if tl>99999 else tl,rw,tr))
            elapsed_label.config(text="已用时: {:.0f}s  ".format(el))
    elif not is_running and _pc.get("pct",0) != 0:
        _pc["pct"] = 0; progress_bar.config(value=0)
        progress_label.config(text="")
        elapsed_label.config(text="")
    hl = state.highlight_row
    if _pc.get("hl") != hl:
        children = tree.get_children()
        if _pc.get("hl_old"):
            oi = _pc["hl_old"] - 1
            if 0 <= oi < len(children):
                tags = list(tree.item(children[oi],"tags"))
                if "running" in tags: tags.remove("running"); tree.item(children[oi],tags=tags)
        if hl > 0 and not is_stopped:
            ni = hl - 1
            if 0 <= ni < len(children):
                tags = list(tree.item(children[ni],"tags"))
                if "running" not in tags: tags.append("running"); tree.item(children[ni],tags=tags)
        _pc["hl"] = hl; _pc["hl_old"] = hl
    _periodic._cache = _pc
    if is_recording:
        n = len(state.recorded_actions)
        if _pc.get("rec_n") != n:
            _pc["rec_n"] = n
            status_text.config(text=" ● 录制中 · {} 个动作 · Ctrl+Shift+Q 停止".format(n),fg=C["dg"])
            status_dot.config(text="● 录制",fg=C["dg"])
    if state.SCHED_ENABLED or state.SCHED_NEXT_RUN:
        sched_val = state.SCHED_NEXT_RUN if state.SCHED_NEXT_RUN else "--:--"
        if _pc.get("sched_val") != sched_val:
            _pc["sched_val"] = sched_val
            settings_window.update_sched_next_label(sched_val)
    if state.SCHED_ENABLED and not state._sched_thread_active:
        sched.start_scheduler(main_run, state.save_config)
    # 同步 Mini Bar 状态（如果折叠模式激活）
    if state.folded:
        _sync_mini_bar_status()
    # 工作流执行高亮 (运行中刷新流程图当前节点)
    try:
        _wf_update_workflow_highlight()
    except Exception:
        pass
    root.after(100,_periodic)
root.after(100,_periodic)

# === 更新检查 (启动 3s 后静默检查一次) ──────────────────────────────
# 旧实现: 发现新版只把 status_text 变成一个 os.startfile 链接 —— 用户点了跳到浏览器,
# 既看不到下载进度, 也无法校验拿到的到底是不是目标版本。现改为点击进入更新窗口,
# 在应用内完成「下载(带进度) → 完整性校验 → 替换并重启」。
_update_done = False


def _on_update_hint(ver, url):
    """状态栏提示可更新 (由 updater 在发现新版本时回调)。"""
    _update_pending["ver"] = ver
    try:
        status_text.config(text=" 新版本 v{} 可用 — 点击更新".format(ver), fg=C["wn"])
        status_dot.config(text=" 更新", fg=C["wn"])
        status_text.config(cursor="hand2")
    except Exception:
        pass


def _check_update(status=None):
    """启动后的静默检查；结果写入状态栏，点击后打开更新窗口。

    status 为 updater 的结构化结果: 只有 ok 才提示更新 —— 网络故障不再被
    伪装成「已是最新」，但也不打扰用户 (仅在日志留痕)。
    """
    global _update_done
    if _update_done:
        return
    _update_done = True
    if not state.CHECK_UPDATE:
        return
    import updater

    def _done(res):
        if not res:
            return
        if res.get("status") == "network_error":
            log1("检查更新失败 (不影响使用): {}".format(res.get("error", "")), "warning")
        try:
            root.after(0, updater.cleanup_updates, True)
        except Exception:
            pass

    updater.check_async(callback=_on_update_hint, on_done=_done)


status_text.bind("<Button-1>", lambda e: show_update_dialog())
root.after(3000, _check_update)

# === Config-driven global hotkeys (run/pause/stop) ===
# 启用 state.on_config_change 机制: 配置保存后自动重建快捷键映射、同步 engine 参数、刷新主题
_HOTKEY_ACTIONS = {}   # {action: (mods_set, vk)} — 由 _rebuild_hotkey_specs() 重建
_hotkey_prev = {}      # 边沿检测: {action: bool}，避免按住时重复触发


def _parse_hotkey_spec(spec):
    """解析快捷键字符串 'ctrl+shift+q' → (mods:set, vk:int)；无效返回 None。"""
    if not spec or spec == "未设置":
        return None
    parts = [p.strip().lower() for p in str(spec).split("+")]
    mod_map = {"ctrl": 0x11, "control": 0x11, "alt": 0x12, "shift": 0x10, "win": 0x5B}
    named = {"space": 0x20, "enter": 0x0D, "esc": 0x1B, "tab": 0x09,
             "backspace": 0x08, "delete": 0x2E, "home": 0x24, "end": 0x23,
             "pageup": 0x21, "pagedown": 0x22, "insert": 0x2D,
             "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
             "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73, "f5": 0x74,
             "f6": 0x75, "f7": 0x76, "f8": 0x77, "f9": 0x78, "f10": 0x79,
             "f11": 0x7A, "f12": 0x7B}
    mods = set()
    key = None
    for p in parts:
        if p in mod_map:
            mods.add(mod_map[p])
        elif p in named:
            key = named[p]
        elif len(p) == 1 and p.isalnum():
            key = ord(p.upper())
        elif p:
            key = None
            break
    if not mods or key is None:
        return None
    return (mods, key)


def _rebuild_hotkey_specs():
    """根据 state 中的 HOTKEY_* 配置重建快捷键映射表（配置变更时调用）。"""
    global _HOTKEY_ACTIONS, _hotkey_prev
    _HOTKEY_ACTIONS = {
        action: _parse_hotkey_spec(getattr(state, key, ""))
        for action, key in (("run", "HOTKEY_RUN"), ("pause", "HOTKEY_PAUSE"),
                            ("stop", "HOTKEY_STOP"))
    }
    _HOTKEY_ACTIONS = {k: v for k, v in _HOTKEY_ACTIONS.items() if v}
    _hotkey_prev = {k: False for k in _HOTKEY_ACTIONS}


_hotkey_executors = {
    "run": main_run,
    "pause": toggle_pause,
    "stop": stop_execution,
}


# ── 配置变更监听器: 保存配置后自动同步运行时状态 ──
def _on_config_changed(data, changed):
    """配置保存后的统一处理入口 (通过 state.on_config_change 注册)。"""
    if not changed:
        return
    try:
        if "dark_mode" in changed:
            _refresh_theme()
            dark_btn.config(text="◑" if state.DARK_MODE else "◐")
        if "retry_max" in changed:
            engine.retry = state.RETRY_MAX
        if "retry_interval" in changed:
            engine.retry_interval = state.RETRY_INTERVAL
        if any(k in changed for k in ("hotkey_run", "hotkey_pause", "hotkey_stop")):
            _rebuild_hotkey_specs()
            log1("全局快捷键配置已更新")
    except Exception as e:
        log1("配置监听器处理失败: {}".format(e), "warning")


state.on_config_change(_on_config_changed, keys=(
    "dark_mode", "retry_max", "retry_interval",
    "hotkey_run", "hotkey_pause", "hotkey_stop"))
_rebuild_hotkey_specs()


# === Global hotkey polling (lightweight: poll keyboard state via ctypes) ===
def _hotkey_poll():
    if state._closing: return
    try:
        # 固定录制停止热键: Ctrl+Shift+Q / Ctrl+Shift+S
        ctrl = ctypes.windll.user32.GetAsyncKeyState(0x11) & 0x8000
        shift = ctypes.windll.user32.GetAsyncKeyState(0x10) & 0x8000
        q = ctypes.windll.user32.GetAsyncKeyState(0x51) & 0x8000
        s = ctypes.windll.user32.GetAsyncKeyState(0x53) & 0x8000
        if state.recording and ctrl and shift and (q or s):
            state.record_stop = True
            log1("热键: 停止录制")

        # 用户配置的全局快捷键 (边沿检测)
        for action, (mods, vk) in _HOTKEY_ACTIONS.items():
            pressed = all(
                ctypes.windll.user32.GetAsyncKeyState(m) & 0x8000 for m in mods)
            now = bool(pressed and (ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000))
            if now and not _hotkey_prev.get(action):
                if action == "pause" and not state.running:
                    pass  # 未运行时忽略暂停热键
                else:
                    log1("热键触发: {}".format(action))
                    root.after(0, _hotkey_executors[action])
            _hotkey_prev[action] = now
    except Exception: pass
    root.after(200, _hotkey_poll)

root.after(200, _hotkey_poll)

# ── 延迟递归绑定滚轮事件（所有子控件创建完毕后生效）──
def _setup_scroll_bindings():
    """在所有 widget 创建完毕后，递归绑定滚轮事件到各滚动区域"""
    try:
        _bind_scroll_recursive(toolbar_inner, _tb1_on_wheel)
        toolbar_canvas.bind("<MouseWheel>", _tb1_on_wheel)
    except Exception: pass
    try:
        _bind_scroll_recursive(ab, _ab_on_wheel)
        ab_canvas.bind("<MouseWheel>", _ab_on_wheel)
    except Exception: pass
    try:
        _bind_scroll_recursive(wf_toolbar, _wf_on_wheel)
        wf_canvas.bind("<MouseWheel>", _wf_on_wheel)
    except Exception: pass

root.after(10, _setup_scroll_bindings)

# 注意: mainloop() 调用已移至 run.py,避免重复调用
# root.mainloop()