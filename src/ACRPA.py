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
        # Mini Bar 拿到的是注入进来的 PIL 句柄; 装载后必须重新注入一次,
        # 否则它那边的 Image/ImageTk 永远是 None
        try:
            _bind_minibar()
        except NameError:
            pass

# P0 Optimization #1: Lazy load pyautogui (heavy ~500ms import)
_pyautogui = None
def _get_pa():
    global _pyautogui
    if _pyautogui is None:
        import pyautogui as pa
        _pyautogui = pa
    return _pyautogui

import state
import utils
from utils import (create_card, _btn, _darken, apply_theme, _colors,
                    ThreadSafeLog, set_tlog, log1, themed)
from scriptdata import ScriptData
from engine import engine
import recorder
import scheduler as sched
from tray import SystemTray
# 独立弹窗模块 (依赖注入: init_ctx 在 root 创建后调用)
# 同时保留模块对象引用: 主题切换时需重同步 dialogs.C (D1)
import dialogs
from dialogs import (init_ctx, show_help_dialog, show_update_dialog,
                     open_version_history, open_ai_panel, open_ai_debug_dialog,
                     open_sched_manager)
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


def open_devlink():
    """打开「设备互联」窗口（设备互联独立窗口，懒加载避免影响启动）。"""
    try:
        import netlink_window
        netlink_window.open_netlink_window(root)
    except Exception as e:
        log1("打开设备互联窗口失败: {}".format(e), "warning")


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

# ══════════════════════════════════════════════════════════════════════
# Mini Bar 已抽到 src/mini_bar.py
#   拆分第一阶段: 该集群的函数与模块级状态变量整体迁出, 函数体未改动;
#   它们引用的宿主符号在下面 _bind_minibar() 里注入。
#   回归: tools/_test_minibar_split.py (静态核对注入清单) + tools/_smoke_mini_bar.py
# ══════════════════════════════════════════════════════════════════════
import mini_bar
from mini_bar import (  # noqa: F401  供本模块其它函数继续按旧名字调用
    _MB_ACCENT,
    _create_mini_bar,
    _destroy_mini_bar,
    _mb_content_height,
    _mb_effective_height,
    _sync_mini_bar_status,
)


def _bind_minibar():
    """把宿主符号注入 mini_bar (启动时调用一次; 主题/缩放变更后再次调用以刷新)。"""
    try:
        mini_bar.bind(
            APP_ROOT=APP_ROOT,
            C=C,
            FONT_BODY=FONT_BODY,
            FONT_BUTTON=FONT_BUTTON,
            FONT_ICON_MD=FONT_ICON_MD,
            FONT_LOG=FONT_LOG,
            Image=Image,
            ImageTk=ImageTk,
            RES_DIR=RES_DIR,
            _fmt_dur=_fmt_dur,
            _load_pil=_load_pil,
            _quit_from_tray=_quit_from_tray,
            _set_window_icon=_set_window_icon,
            _step_once=_step_once,
            _toggle_fold=_toggle_fold,
            main_run=main_run,
            root=root,
            stop_execution=stop_execution,
            toggle_pause=toggle_pause,
        )
    except Exception as e:      # 注入失败必须显式暴露, 不能静默 (否则是运行期 NameError)
        try:
            log1("Mini Bar 上下文注入失败: {}".format(e), "error")
        except Exception:
            pass




















































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

    # 尺寸和位置（屏幕顶部居中）—— 高度与 Mini Bar 视觉统一, 宽度随配置缩放
    # (比 Mini Bar 紧凑态略宽 30px, 以容纳录制计数/模式/暂停/停止等更多控件)
    rb_w = int(state.MINI_BAR_WIDTH) + 30
    # 与 Mini Bar 统一高度 (内容/字体自适应); 但录制指示灯用更大字号 FONT_ICON_LG,
    # 其行高需求可能超过 FONT_BUTTON 反推的条高 → 取二者较大者, 避免 "●" 被纵向裁剪。
    rb_h = max(_mb_effective_height(),
               _mb_content_height(FONT_ICON_LG) + _MB_ACCENT)
    screen_w = rb.winfo_screenwidth()
    rb.geometry("{}x{}+{}+{}".format(rb_w, rb_h, (screen_w - rb_w) // 2, 8))

    # 内框
    inner = tkinter.Frame(rb, bg=C["bgc"], bd=0)
    inner.pack(fill="both", expand=True, padx=1, pady=1)

    # ── 录制指示灯 ──
    rb_dot = tkinter.Label(inner, text="●", font=FONT_ICON_LG,
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
    loop_val = str(loop_count_var.get()).strip()
    if loop_val in ("", "无限循环"):
        tn = 9999800001
    else:
        try:
            tn = int(loop_val)
            if tn <= 0:
                tn = 9999800001
        except ValueError:
            log1("无效的运行次数「{}」，已按无限循环处理".format(loop_val), "warning")
            tn = 9999800001
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
    state.exec_state["ok_cmds"]=0; state.exec_state["fail_cmds"]=0
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
# 已应用的主题值快照: 供 _on_config_changed 判定「dark_mode 是否真的变化」,
# 避免无关保存 (拖动 Mini Bar / 设置防抖 / netlink / scheduler) 触发全量重绘。
_applied_dark_mode = bool(getattr(state, "DARK_MODE", False))
# 字体统一取 utils 的命名字体角色 (字符串), 本文件不再自建元组副本
FONT_TITLE = utils.FONT_TITLE; FONT_BODY = utils.FONT_BODY; FONT_LOG = utils.FONT_LOG
FONT_SMALL = utils.FONT_SMALL; FONT_BUTTON = utils.FONT_BUTTON
FONT_SMALL_BOLD = utils.FONT_SMALL_BOLD; FONT_TINY = utils.FONT_TINY
FONT_ICON = utils.FONT_ICON; FONT_ICON_MD = utils.FONT_ICON_MD
FONT_ICON_LG = utils.FONT_ICON_LG

# ── Windows 任务栏图标：必须在创建任何窗口之前设置 AppUserModelID ──
try:
    import ctypes
    from version_info import VERSION
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ACRPA.RPA.v{}".format(VERSION))
except Exception:
    pass

# ── DPI 感知：必须早于 Tk()，否则 100%/150%/200% 系统缩放下界面会被系统位图拉伸模糊 ──
try:
    import ctypes
    try:
        # PROCESS_PER_MONITOR_DPI_AWARE = 2 (Win8.1+)
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        # 回退: Win7/8 的 system-DPI-aware
        ctypes.windll.user32.SetProcessDPIAware()
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
# 命名字体必须在创建任何 widget 之前建立; tk scaling 改变 pt->px 换算后再刷一次
utils.init_fonts(root)
try:
    root.tk.call("tk", "scaling", root.winfo_fpixels("1i") / 72.0)
except Exception:
    pass
utils.init_fonts(root)
root.title("A/C RPA")
# 主窗口几何随「界面缩放」档位 + DPI 缩放: 默认几何由 utils.scaled() 从设计值
# 1000x680 派生 (含 dpi_factor 与 ui_scale), 避免 125% DPI 下内容 (req 高 703)
# 超出硬编码 680 而被纵向裁剪。
# ── 主窗口几何: 记忆 + 最大化恢复 + 虚拟屏越界回退居中; 紧凑模式=500x625 旧布局 ──
# 越界判断 / 居中派生 / 虚拟屏取值已抽为 utils 公共函数 (行为等价):
#   utils.win_virtual_bounds(root) / utils.geometry_in_screen(root, geo) /
#   utils.center_geometry(root, w, h) —— 主窗口与设置窗口共用同一套判定。

def _apply_main_geometry():
    """启动时应用主窗口几何:
       紧凑模式 → 固定 500x625 旧布局;
       否则 → 优先记忆几何(校验越界), 越界/缺省则按「设计 1000x680 × dpi_factor
              × ui_scale」派生默认几何并居中; 再恢复最大化。
    """
    try:
        if getattr(state, "COMPACT_MODE", False):
            if abs(utils.current_ui_scale() - 1.0) > 1e-6:
                root.geometry("{}x{}+400+80".format(utils.scaled(500), utils.scaled(625)))
                root.minsize(utils.scaled(450), utils.scaled(550))
            else:
                root.geometry("500x625+400+80"); root.minsize(450, 550)
            return
        geo = getattr(state, "MAIN_GEOMETRY", "") or ""
        if geo and utils.geometry_in_screen(root, geo):
            root.geometry(geo)
        else:
            # 默认几何须纳入 dpi_factor: 125% DPI 下内容 req 高 703 > 硬编码 680,
            # 会纵向裁剪底部; 经 utils.scaled() 派生保证 reqheight <= winfo_height。
            root.geometry(utils.center_geometry(root, utils.scaled(1000),
                                                utils.scaled(680)))
        root.minsize(640, 520)
        if getattr(state, "MAIN_MAXIMIZED", False):
            try: root.state("zoomed")
            except Exception: pass
    except Exception:
        root.geometry("960x680")

_apply_main_geometry()
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
    # 记忆主窗口几何 + 最大化状态 (紧凑模式下不覆盖用户记忆的普通几何)
    try:
        if root.wm_state() == "zoomed":
            state.MAIN_MAXIMIZED = True
        else:
            state.MAIN_MAXIMIZED = False
            if not getattr(state, "COMPACT_MODE", False):
                state.MAIN_GEOMETRY = root.geometry()
    except Exception:
        pass
    state.save_config()

    # NetLink 多设备互联: 退出清理 (失败不影响主程序)
    try:
        import netlink_window; netlink_window.close_window()
    except Exception: pass
    try:
        import netlink; netlink.stop_netlink()
    except Exception: pass
    
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
        import netlink_window; netlink_window.close_window()
    except Exception: pass
    try:
        import netlink; netlink.stop_netlink()
    except Exception: pass
    try:
        _do_close()
    except Exception:
        try:
            root.destroy()
        except Exception:
            pass

# === Layout - compact spacing ──
root.columnconfigure(0,weight=1)
# ① 标题栏 / ② 执行控制工具栏(常驻) / ③ 主区(内容 + 底部日志) / ⑦ 状态栏
root.rowconfigure(0,weight=0)
root.rowconfigure(1,weight=0)
root.rowconfigure(2,weight=1)
root.rowconfigure(3,weight=0)
PAD = {"padx":6,"pady":3}; PI = {"padx":6,"pady":2}

# Title bar - compact design
title_bar = tkinter.Frame(root,bg=C["bg"])
title_bar.grid(row=0,column=0,sticky="ew",padx=10,pady=(6,2))
title_bar.columnconfigure(0,weight=1)

title_lbl = tkinter.Label(title_bar,text="A/C RPA Automation Workflow",
    font=FONT_TITLE,fg=C["fgt"],bg=C["bg"])
title_lbl.grid(row=0,column=0,sticky="w")

# ── 顶部标题双击内联编辑 (仅会话内存, 绝不写入配置文件) ──────────────
# 需求: 界面顶部标题可双击自定义; 默认文案与现状一致, 重启回默认。
TITLE_DEFAULT_TEXT = "A/C RPA Automation Workflow"  # 空/纯空白时回退的默认文案
_title_text_session = TITLE_DEFAULT_TEXT            # 会话内标题 (模块级, 重启即复位)
_title_edit_entry = None                            # 当前编辑态 Entry (None=非编辑态)

def _commit_title_edit(save=True):
    """结束标题编辑: 保存(或放弃)文本 → 销毁 Entry → 恢复标题 Label。

    仅更新会话变量与 Label 文本, 不触碰 config.json / 任何配置持久化
    (故重启应用后自动回默认文案)。可被 <Return>/<FocusOut> 重复触发,
    以 _title_edit_entry 置空做幂等去重, 避免重复保存。
    """
    global _title_text_session, _title_edit_entry
    ent = _title_edit_entry
    if ent is None:
        return
    _title_edit_entry = None  # 先置空: 后续 FocusOut/Return 事件直接返回, 避免重复保存
    try:
        new = ent.get()
    except Exception:
        new = ""
    try:
        ent.destroy()
    except Exception:
        pass
    if save:
        new = (new or "").strip()
        if not new:
            new = TITLE_DEFAULT_TEXT  # 空或纯空白 → 回退默认文案
        _title_text_session = new
    try:
        title_lbl.config(text=_title_text_session)  # 恢复非编辑态外观 (仍属原布局)
    except Exception:
        pass

def _start_title_edit(event=None):
    """双击标题 → 在原位叠加同字体/同前景背景色的无边框 Entry 进入编辑态。

    - 字体/前景/背景色均取 Label 当前实际值 (随主题变, 不硬编码);
    - borderwidth=0 / highlightthickness=0 / relief=flat → 无可见边框, 外观一致;
    - place() 覆盖在 Label 原位, Label 始终是父容器布局的一部分 → 无位移;
    - 不添加任何 tooltip / 光标样式等暗示可编辑的元素。
    """
    global _title_edit_entry
    if _title_edit_entry is not None:
        return
    try:
        ent = tkinter.Entry(title_bar, font=title_lbl.cget("font"),
            fg=title_lbl.cget("fg"), bg=title_lbl.cget("bg"),
            borderwidth=0, highlightthickness=0, relief="flat",
            insertbackground=title_lbl.cget("fg"))
        ent.place(x=title_lbl.winfo_x(), y=title_lbl.winfo_y(),
            width=title_lbl.winfo_width(), height=title_lbl.winfo_height())
        ent.insert(0, _title_text_session)
        ent.select_range(0, "end")  # 预填当前文本并全选, 便于直接覆盖输入
        ent.icursor("end")
        ent.focus_set()
        _title_edit_entry = ent
        ent.bind("<Return>", lambda e: _commit_title_edit(True))
        ent.bind("<KP_Enter>", lambda e: _commit_title_edit(True))
        ent.bind("<Escape>", lambda e: _commit_title_edit(False))   # 放弃本次修改
        ent.bind("<FocusOut>", lambda e: _commit_title_edit(True))  # 焦点移出自动保存
    except Exception:
        _title_edit_entry = None

title_lbl.bind("<Double-Button-1>", _start_title_edit)

# Dark mode toggle - compact
dark_frame = tkinter.Frame(title_bar,bg=C["bg"])
dark_frame.grid(row=0,column=1,sticky="e")
status_dot = tkinter.Label(dark_frame,text="● 就绪",font=FONT_SMALL,fg=C["fgm"],bg=C["bg"])
status_dot.pack(side="left",padx=(0,6))
pin_btn = tkinter.Label(dark_frame,text="△",font=FONT_ICON,
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
        font=FONT_SMALL, bg=C["tooltip_bg"], fg=C["fgb"], relief="flat", bd=0,
        highlightthickness=1, highlightbackground=C["tooltip_border"], padx=6, pady=2).pack()
pin_btn.bind("<Enter>", _show_pin_tip)

# 折叠按钮 — 将主窗口折叠为 Mini Bar
fold_btn = tkinter.Label(dark_frame,text="⊟",font=FONT_ICON,
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
        font=FONT_SMALL, bg=C["tooltip_bg"], fg=C["fgb"], relief="flat", bd=0,
        highlightthickness=1, highlightbackground=C["tooltip_border"], padx=6, pady=2).pack()
fold_btn.bind("<Enter>", _show_fold_tip)

dark_btn = tkinter.Label(dark_frame,text="◑" if state.DARK_MODE else "◐",
    font=FONT_ICON_LG,fg=C["fgm"],bg=C["bg"],cursor="hand2")
dark_btn.pack(side="left")

# 标题栏底部 1px 发丝线 (§4.1: 1px 发丝线替代原 2px 强调条)
tkinter.Frame(title_bar, bg=C["bd"], height=1).grid(row=1, column=0, columnspan=2, sticky="ew", pady=(5,0))

# Simple tooltip for dark mode toggle
_tip_win = None
def _show_tip(event):
    global _tip_win
    if _tip_win: _tip_win.destroy()
    _tip_win = tkinter.Toplevel(root)
    _tip_win.wm_overrideredirect(True)
    _tip_win.wm_geometry("+{}+{}".format(event.x_root+12, event.y_root-8))
    tkinter.Label(_tip_win, text="切换暗色模式" if not state.DARK_MODE else "切换亮色模式",
        font=FONT_SMALL, bg=C["tooltip_bg"], fg=C["fgb"], relief="flat", bd=0,
        highlightthickness=1, highlightbackground=C["tooltip_border"], padx=6, pady=2).pack()
def _hide_tip(event):
    global _tip_win
    if _tip_win: _tip_win.destroy(); _tip_win = None
dark_btn.bind("<Enter>", _show_tip)
dark_btn.bind("<Leave>", _hide_tip)
fold_btn.bind("<Leave>", _hide_tip)
pin_btn.bind("<Leave>", _hide_tip)

# 设置按钮 — 打开独立设置窗口 (原设置 Tab 已分离，主界面入口)
settings_btn = tkinter.Label(dark_frame, text="⚙", font=FONT_ICON_MD,
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
        font=FONT_SMALL, bg=C["tooltip_bg"], fg=C["fgb"], relief="flat", bd=0,
        highlightthickness=1, highlightbackground=C["tooltip_border"], padx=6, pady=2).pack()
settings_btn.bind("<Enter>", _show_settings_tip)
settings_btn.bind("<Leave>", _hide_tip)

# 帮助按钮 — 「?」图标, 与设置图标并列且位于其后 (原「执行控制」底部「? 帮助」上移)
help_btn = tkinter.Label(dark_frame, text="?", font=FONT_ICON_MD,
    fg=C["fgm"], bg=C["bg"], cursor="hand2", padx=2)
help_btn.pack(side="left", padx=(0, 2))
help_btn.bind("<Button-1>", lambda e: show_help_dialog())
def _show_help_tip(event):
    global _tip_win
    if _tip_win: _tip_win.destroy()
    _tip_win = tkinter.Toplevel(root)
    _tip_win.wm_overrideredirect(True)
    _tip_win.wm_geometry("+{}+{}".format(event.x_root + 12, event.y_root - 8))
    tkinter.Label(_tip_win, text="帮助 / 使用说明",
        font=FONT_SMALL, bg=C["tooltip_bg"], fg=C["fgb"], relief="flat", bd=0,
        highlightthickness=1, highlightbackground=C["tooltip_border"], padx=6, pady=2).pack()
help_btn.bind("<Enter>", _show_help_tip)
help_btn.bind("<Leave>", _hide_tip)

# 互联按钮 — 打开「设备互联」独立窗口 (与设置按钮同级样式)
devlink_btn = tkinter.Label(dark_frame, text="⊕", font=FONT_ICON_MD,
    fg=C["fgm"], bg=C["bg"], cursor="hand2", padx=2)
devlink_btn.pack(side="left", padx=(0, 2))
devlink_btn.bind("<Button-1>", lambda e: open_devlink())
def _show_devlink_tip(event):
    global _tip_win
    if _tip_win: _tip_win.destroy()
    _tip_win = tkinter.Toplevel(root)
    _tip_win.wm_overrideredirect(True)
    _tip_win.wm_geometry("+{}+{}".format(event.x_root + 12, event.y_root - 8))
    tkinter.Label(_tip_win, text="设备互联",
        font=FONT_SMALL, bg=C["tooltip_bg"], fg=C["fgb"], relief="flat", bd=0,
        highlightthickness=1, highlightbackground=C["tooltip_border"], padx=6, pady=2).pack()
devlink_btn.bind("<Enter>", _show_devlink_tip)
devlink_btn.bind("<Leave>", _hide_tip)
# 语义色分组: 组内任一键在旧/当前主题下的取值命中按钮底色 → 归入该语义组。
# 以「组」判定可覆盖 C["ok"]/C["err"]/C["hover"]/C["focus"] 等与主键同值的等价键。
_SEMANTIC_GROUPS = (("sc", ("sc", "ok")), ("dg", ("dg", "err")),
                    ("wn", ("wn",)), ("ac", ("ac", "hover", "focus")))


def _semantic_bg_for(cur_bg, prev):
    """语义按钮底色回填: 命中语义组返回当前主题色, 否则返回 None (中性按钮)。

    判定来源 =「旧主题快照 prev」∪「当前主题 C」: 只要 cur_bg 命中任一语义组
    在旧/新主题下的取值, 即按当前主题重新着色。这样亮↔暗往返切换后
    ▶运行(sc)/⏸暂停(wn)/⏭单步(ac)/●录制(dg) 始终与当前主题一致。
    """
    for primary, keys in _SEMANTIC_GROUPS:
        for k in keys:
            if prev and prev.get(k) == cur_bg:
                return C.get(primary)
            if C.get(k) == cur_bg:
                return C.get(primary)
    return None


def _refresh_theme():
    global C, _applied_dark_mode
    # D2 根因修复: 重绑 C 之前先抓「旧主题色快照」作为语义色回填的判定来源。
    # 旧实现读 C["old_*"], 但此刻 C 已被重绑为不含 old_* 的新 dict → 恒为 None,
    # 使 ▶运行/⏸暂停/⏭单步/●录制 等语义按钮被误判为中性按钮刷成灰。
    _prev = dict(C) if isinstance(C, dict) else {}
    C = _colors()
    # 主题变更后把新色板/字体注入 Mini Bar (它是独立模块, 不反向读本模块的 C)
    _bind_minibar()
    # 记录本次已应用的主题值 (供 _on_config_changed 收窄触发, 见该函数注释)
    _applied_dark_mode = bool(getattr(state, "DARK_MODE", False))
    apply_theme(root, style)

    # 主题切换后同步脚本市场窗口 (已打开则即时重刷颜色；未开为安全 no-op)。
    # 放在 apply_theme 之后: 此时 utils.C 已重绑，market_window 的 themed() 读到新色。
    try:
        import market_window
        market_window.retheme_marketplace()
    except Exception:
        pass

    def _walk(p, prev):
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
                    # 语义按钮: 保留语义并更新为当前主题色; 中性按钮: 统一刷为卡片色
                    _new_bg = _semantic_bg_for(w.cget("bg"), prev)
                    if _new_bg is not None:
                        w.configure(bg=_new_bg)
                    else:
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
                elif cls in ("Checkbutton", "Radiobutton"):
                    # D5: 勾选/单选框 (旧实现遗漏) — 底色随父容器, 选中块用卡片色
                    _pbg = str(p.cget("bg"))
                    w.configure(bg=_pbg, fg=C["fgb"],
                        activebackground=_pbg, activeforeground=C["fgt"],
                        selectcolor=C["bgc"], highlightbackground=C["bd"])
                elif cls == "Menu":
                    # D5: 下拉/右键菜单 (旧实现遗漏 → 暗色下仍是系统浅色)
                    w.configure(bg=C["bgc"], fg=C["fgb"],
                        activebackground=C["ac"], activeforeground="white",
                        disabledforeground=C["fgm"])
            except Exception:
                pass
            _walk(w, prev)

    _walk(root, _prev)
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
    # D3: 日志区 tag 前景随主题刷新 (旧实现仅建窗时设一次, 切换后 tag 文字仍是旧色)
    try:
        rz.tag_configure("info", foreground=C["log_info"])
        rz.tag_configure("success", foreground=C["sc"])
        rz.tag_configure("warning", foreground=C["wn"])
        rz.tag_configure("error", foreground=C["dg"])
        # §4.6 左缘色条 / 行 zebra / 搜索命中 随主题刷新
        rz.tag_configure("cbar_info", foreground=C["log_info"])
        rz.tag_configure("cbar_success", foreground=C["log_success"])
        rz.tag_configure("cbar_warning", foreground=C["log_warn"])
        rz.tag_configure("cbar_error", foreground=C["log_error"])
        rz.tag_configure("lzebra", background=C["zebra"])
        rz.tag_configure("search_hit", background=C["search_hit_bg"])
    except Exception:
        pass
    # D3: 脚本树 tag 随主题刷新 (写死的 #FEF3C7 → 主题色 C["hlbg"])
    # §4.5: zebra 用 C["zebra"] (acl 专属于选中态, 不得再用于斑马纹)
    try:
        tree.tag_configure("even", background=C["zebra"])
        tree.tag_configure("running", background=C["hlbg"])
        tree.tag_configure("breakpoint", foreground=C["dg"])
    except Exception:
        pass
    # §4.5 主题/DPI 变更后重建选中左缘 2px 色条并重刷
    try:
        _build_sel_bar()
        _sync_sel_bars(tree)
    except Exception:
        pass

    # Update status bar
    status_bar.configure(bg=C["bg"])
    status_text.configure(bg=C["bg"])
    try:
        dash_info.configure(bg=C["bg"], fg=C["fgm"])
    except Exception:
        pass

    # Update progress bar container - force ttk style refresh
    progress_bar.configure(style="Exec.Horizontal.TProgressbar")

    # Update all tab frames
    for widget in [tab_edit, tab_workflow]:
        widget.configure(bg=C["bg"])

    # 同步设置窗口颜色 (独立窗口使用自己的 C 引用; 传入旧快照供语义色判定)
    settings_window.C = C
    settings_window.refresh_theme(_prev)
    # D1: 弹窗模块颜色表随主题重同步 (此前仅启动时注入一次, 切换后新开的
    # 帮助/版本/AI/计划/更新/市场等弹窗仍沿用旧配色)
    try:
        dialogs.C = C
    except Exception:
        pass
    # D4: 已打开的「设备互联」独立窗口主题重刷 (窗口不存在时空操作)
    try:
        import netlink_window
        netlink_window.refresh_theme()
    except Exception:
        pass
    # 帮助系统渲染层 (阶段1-2): 已打开帮助窗口主题实时重刷 (仿 netlink; 不存在时空操作)
    try:
        import help_window
        help_window.refresh_theme(root, C)
    except Exception:
        pass
    # 定点回填语义按钮 (D2): 判定来源改用旧主题快照 _prev (不再依赖 C["old_*"])
    for widget in (btn_run, btn_pause, btn_step, btn_record):
        try:
            _new_bg = _semantic_bg_for(widget.cget("bg"), _prev)
            if _new_bg is not None:
                widget.configure(bg=_new_bg)
        except Exception:
            pass

    # 工作流 Tab 主题同步: 刷新步骤树标签颜色 + 重绘流程图使用新主题
    try:
        _wf_refresh_tree()
    except Exception:
        pass

    # 重新应用执行仪表盘进度条样式 (加高 8px)
    try:
        style.configure("Exec.Horizontal.TProgressbar", thickness=8)
    except Exception:
        pass
    # 旧实现此处向 C 注入 old_sc/old_dg/old_wn/old_ac 供下次切换比对;
    # 现改用局部旧快照 _prev, 不再污染主题色表 C。


def toggle_dark():
    state.DARK_MODE = not state.DARK_MODE
    _refresh_theme()
    dark_btn.config(text="◑" if state.DARK_MODE else "◐")
    state.save_config()

dark_btn.bind("<Button-1>", lambda e: toggle_dark())

# ③ 主区: 垂直 PanedWindow —— 上 pane 内容(notebook) / 下 pane 常驻日志面板
# (tkinter grid 无可拖 sash, 「可拖 + minsize + 一键收起」的原生正解即 PanedWindow)
main_paned = tkinter.PanedWindow(root, orient="vertical",
    bg=C["bd"], sashwidth=utils.sp(4), sashrelief="raised",
    opaqueresize=True, bd=0)
main_paned.grid(row=2,column=0,sticky="nsew",padx=8,pady=(0,3))

# 底部日志面板容器 (稍后作为 pane#1 加入)
bottom_dock = tkinter.Frame(main_paned, bg=C["bg"])

# Notebook - compact padding
notebook = ttk.Notebook(main_paned)
main_paned.add(notebook, minsize=200, stretch="always")

# ======================================================================
# TAB 1: 编辑
# ======================================================================
tab_edit = tkinter.Frame(notebook,bg=C["bg"])
notebook.add(tab_edit,text="  脚本编辑  ")
tab_edit.columnconfigure(0,weight=1)
tab_edit.rowconfigure(0,weight=0); tab_edit.rowconfigure(1,weight=1)


# === Toolbar (① 可横向滚动按钮区 + ② 右对齐文件名), 与「执行控制」同规格 ===
toolbar = tkinter.Frame(tab_edit,bg=C["bg"])
toolbar.grid(row=0,column=0,sticky="ew",padx=utils.sp(6),pady=(utils.sp(4),utils.sp(2)))
toolbar.columnconfigure(0,weight=1)

# 文件名标签 (右对齐, 不参与横向滚动)
edit_file_label = tkinter.Label(toolbar,text="",font=FONT_SMALL,
    fg=C["fgm"],bg=C["bg"],anchor="e")
edit_file_label.grid(row=0,column=1,sticky="ne",padx=(utils.sp(4),utils.sp(2)))

# 可横向滚动按钮区 (canvas + 横向滚动条下置 + 溢出提示「⇄」)
tb_scroll = tkinter.Frame(toolbar,bg=C["bg"])
tb_scroll.grid(row=0,column=0,sticky="ew")
tb_scroll.columnconfigure(0,weight=1)

toolbar_canvas = tkinter.Canvas(tb_scroll, bg=C["bg"],
    height=utils.ctrl_h("ctrl_h_lg"), highlightthickness=0, bd=0)
toolbar_scrollbar = tkinter.Scrollbar(tb_scroll, orient="horizontal",
    command=toolbar_canvas.xview, width=utils.sp(6))
toolbar_inner = tkinter.Frame(toolbar_canvas, bg=C["bg"])

# 溢出提示「⇄」: 内容宽度超过可视宽度时显示 (与执行控制一致)
toolbar_overflow_hint = tkinter.Label(tb_scroll,text="⇄",font=FONT_SMALL,
    fg=C["fgm"],bg=C["bg"])

def _tb_update_overflow(event=None):
    try:
        bb = toolbar_canvas.bbox("all")
        need = bb[2] if bb else 0
        if need > toolbar_canvas.winfo_width() + utils.sp(2):
            toolbar_overflow_hint.grid(row=0,column=1,sticky="ne",padx=(utils.sp(2),0))
        else:
            toolbar_overflow_hint.grid_remove()
    except Exception:
        pass

toolbar_inner.bind(
    "<Configure>",
    lambda e: (toolbar_canvas.configure(scrollregion=toolbar_canvas.bbox("all")),
               _tb_update_overflow())
)

toolbar_canvas.create_window((0, 0), window=toolbar_inner, anchor="w")

toolbar_canvas.configure(xscrollcommand=toolbar_scrollbar.set)

toolbar_canvas.bind("<Configure>", lambda e: _tb_update_overflow())
toolbar_canvas.grid(row=0,column=0,sticky="ew")
toolbar_scrollbar.grid(row=1,column=0,sticky="ew")

# 鼠标滚轮横向滚动 - handler 定义（递归绑定在 _init_toolbar_buttons() 之后）
def _tb1_on_wheel(event):
    if event.delta > 0:
        toolbar_canvas.xview_scroll(-1, "units")
    else:
        toolbar_canvas.xview_scroll(1, "units")

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

# ══════════════════════════════════════════════════════════════════════
# 脚本编辑区缩放 (Ctrl+滚轮 / Ctrl+加号 / Ctrl+减号 / Ctrl+0 复位)
#   为什么不直接改 "Treeview" 样式: 变量监视、时序、工作流列表共用它, 一起放大
#   不是用户想要的。这里派生一个只给脚本表格用的样式, 并把倍率持久化到
#   state.EDITOR_ZOOM (0 = 跟随全局 ui_scale)。
# ══════════════════════════════════════════════════════════════════════
_EDITOR_STYLE = "ScriptEditor.Treeview"
_EDITOR_ZOOM_MIN, _EDITOR_ZOOM_MAX = -4, 12
_editor_font = tkinter.font.Font(root=root, name="ACRPA_EDITOR_TABLE", exists=False,
                                 family="Microsoft YaHei UI", size=10)


def _editor_base_font():
    """编辑区基准字体对象。

    注意: utils.font(role) 返回的是**命名字体字符串**(给 Tk 用的), 不是 Font 对象,
    直接 .cget() 会抛 AttributeError —— 而异常一旦被吞掉, 缩放就会静默失效
    (本功能第一版正是这么坏的, 由 tools/_test_ui_zoom_shortcuts.py 的实机断言抓出)。
    这里按 utils.init_fonts 的同一手法包装既有命名。
    """
    try:
        return tkinter.font.Font(root=root, name=utils.font(utils.FONT_BODY), exists=True)
    except Exception:
        return None


def _editor_base_size():
    """编辑区基准字号 (跟随全局字号角色, ui_scale 改的就是它)。"""
    f = _editor_base_font()
    try:
        return int(f.cget("size")) if f is not None else 10
    except Exception:
        return 10


def _apply_editor_zoom(event=None):
    """按 state.EDITOR_ZOOM 重算编辑区字体与行高 (幂等, 可反复调用)。"""
    base = _editor_base_font()
    try:
        delta = int(getattr(state, "EDITOR_ZOOM", 0) or 0)
        base_size = int(base.cget("size")) if base is not None else 10
        size = max(6, min(40, base_size + delta))
        family = base.cget("family") if base is not None else "Microsoft YaHei UI"
        _editor_font.configure(family=family, size=size)
        ttk.Style().configure(_EDITOR_STYLE, font=_editor_font,
                              rowheight=max(16, int(size * 1.9)))
        tree.configure(style=_EDITOR_STYLE)
        return size
    except Exception as e:
        try:
            log1("编辑区缩放应用失败: {}".format(e), "warning")
        except Exception:
            pass
        return None


def _editor_zoom_step(delta, silent=False):
    """调整缩放倍率并落盘 → 'break' 供 Tk 绑定返回。"""
    cur = int(getattr(state, "EDITOR_ZOOM", 0) or 0)
    new = max(_EDITOR_ZOOM_MIN, min(_EDITOR_ZOOM_MAX, cur + delta))
    if new != cur:
        state.EDITOR_ZOOM = new
        _apply_editor_zoom()
        try:
            state.save_config()
        except Exception:
            pass
        if not silent:
            try:
                show_toast(root, "编辑区字号 {}{}".format("+" if new > 0 else "", new),
                           "info", 1200)
            except Exception:
                pass
    return "break"


def _editor_zoom_reset(event=None):
    state.EDITOR_ZOOM = 0
    _apply_editor_zoom()
    try:
        state.save_config()
    except Exception:
        pass
    try:
        show_toast(root, "编辑区字号已复位", "info", 1200)
    except Exception:
        pass
    return "break"


def _widget_in_editor(widget):
    """指针是否落在脚本编辑区内 (Ctrl+滚轮只在编辑区生效)。"""
    try:
        while widget is not None:
            if widget in (tree, tree_frame):
                return True
            widget = widget.master
    except Exception:
        pass
    return False


def _editor_zoom_wheel(event):
    try:
        if not _widget_in_editor(root.winfo_containing(event.x_root, event.y_root)):
            return None
    except Exception:
        return None
    return _editor_zoom_step(1 if event.delta > 0 else -1)


_apply_editor_zoom()
tree.bind("<Control-MouseWheel>", _editor_zoom_wheel)
tree.bind("<Control-plus>", lambda e: _editor_zoom_step(1))
tree.bind("<Control-equal>", lambda e: _editor_zoom_step(1))   # =/+ 同键
tree.bind("<Control-minus>", lambda e: _editor_zoom_step(-1))
tree.bind("<Control-Key-0>", _editor_zoom_reset)

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
    tkinter.Label(f, text="●", font=FONT_ICON,
        fg=color, bg=C["bg"]).pack(side="left")
    tkinter.Label(f, text=label, font=FONT_TINY,
        fg=C["fgm"], bg=C["bg"]).pack(side="left")

# tree double-click binding is set after _edit_cell is defined below
tree.tag_configure("even", background=C["zebra"])   # §4.5 斑马纹 (不再用 acl)
tree.tag_configure("running", background=C["hlbg"])   # 主题色 (旧为写死 #FEF3C7)
tree.tag_configure("breakpoint", foreground=themed("dg"))

# ── §4.5 选中左缘 2px 强调条 (主方案: #0 树列 per-item image) ──
#   ttk Treeview 无 per-row 边框能力, 故在 #0 列每项挂 2px 宽 ac 竖条贴图;
#   #0 列 minwidth=32 可容纳「2px 图 + 行号文本」, 且不干扰 #0 断点点击。
_bar_ac = None


def _build_sel_bar():
    """构造 2px 宽 x row_h 高的 ac 竖条 PhotoImage (随主题/DPI 变更重建)。"""
    global _bar_ac
    try:
        h = int(utils.TOKENS.get("row_h", 22))
    except Exception:
        h = 22
    try:
        img = tkinter.PhotoImage(width=2, height=h)
        img.put(C["ac"], to=(0, 0, 2, h))
        _bar_ac = img
    except Exception:
        _bar_ac = None


def _sel_bar(tree_, item, on):
    try:
        tree_.item(item, image=(_bar_ac if (on and _bar_ac is not None) else ""))
    except Exception:
        pass


def _sync_sel_bars(tree_):
    """重刷选中左缘色条, 并对选中行移除 row_hover (确保 selected 不被冲淡)。"""
    try:
        for it in tree_.get_children(""):
            _sel_bar(tree_, it, False)
        for it in tree_.selection():
            _sel_bar(tree_, it, True)
            tgs = tuple(t for t in tree_.item(it, "tags") if t != "rowhover")
            tree_.item(it, tags=tgs)
    except Exception:
        pass


def _bind_sel_bar(tree_):
    def _on(_e=None):
        _sync_sel_bars(tree_)
    tree_.bind("<<TreeviewSelect>>", _on, add=True)


# ── §4.5/§4.9.2 row_hover: ttk 无内建行 hover, 自绑 <Motion>/<Leave> item tag ──
def _bind_row_hover(tree_):
    try:
        tree_.tag_configure("rowhover", background=C["row_hover"])
    except Exception:
        pass
    st = {"cur": ""}

    def _clear():
        it = st.get("cur")
        if it:
            try:
                tgs = tuple(t for t in tree_.item(it, "tags") if t != "rowhover")
                tree_.item(it, tags=tgs)
            except Exception:
                pass
            st["cur"] = ""

    def _motion(e):
        try:
            it = tree_.identify_row(e.y)
        except Exception:
            it = ""
        if it == st.get("cur"):
            return
        _clear()
        if it and it not in tree_.selection():
            try:
                tgs = tree_.item(it, "tags")
                tree_.item(it, tags=tuple(tgs) + ("rowhover",))
                st["cur"] = it
            except Exception:
                pass

    tree_.bind("<Motion>", _motion, add=True)
    tree_.bind("<Leave>", lambda e: _clear(), add=True)


# ── §4.5 show="headings" 树 (无 #0 列) 降级: 选中整行 acl 底 + 加粗 ──
def _bind_sel_bold(tree_):
    """对无 #0 列的树: 选中行加粗 (整行 acl 底由 utils.apply_theme 的 style.map 提供)。"""
    try:
        tree_.tag_configure("selbold", font=FONT_SMALL_BOLD)
    except Exception:
        pass

    def _on(_e=None):
        try:
            for it in tree_.get_children(""):
                tgs = tuple(t for t in tree_.item(it, "tags") if t != "selbold")
                tree_.item(it, tags=tgs)
            for it in tree_.selection():
                tgs = tree_.item(it, "tags")
                if "selbold" not in tgs:
                    tree_.item(it, tags=tuple(tgs) + ("selbold",))
        except Exception:
            pass

    tree_.bind("<<TreeviewSelect>>", _on, add=True)


_build_sel_bar()
_bind_sel_bar(tree)
_bind_row_hover(tree)
# C5: 缩放档位切换后重建选中左缘条 PhotoImage (高度按 row_h) 并重刷, 与 _refresh_theme 同做法。
# 走 utils 注册回调 (set_ui_scale 在重配字体后触发), 避免把私有名暴露给 settings_window。
try:
    utils.register_ui_scale_hook(lambda: (_build_sel_bar(), _sync_sel_bars(tree),
                                          _bind_minibar(), _apply_editor_zoom()))
except Exception:
    pass
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
            tkinter.Label(example_frame, text="• " + ex, font=FONT_LOG,
                fg=C["ac"], bg=C["bgc"]).pack(anchor="w")
        
        cond_entry = tkinter.Entry(cond_win, font=FONT_LOG, width=50)
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
            
            # P0-6 原子写: 同目录 .tmp → 备份旧文件为 .bak → os.replace 原子替换
            utils.atomic_save(fp, lambda tmp: wb.save(tmp), backup=True)
            state.filename=fp; state.has_script=True
            state._editor_modified=False
            script_name_var.set(os.path.basename(fp))
            edit_file_label.config(text=os.path.basename(fp))
            log1("脚本已保存: {}".format(fp))
            # ── 版本快照: 文件成功落盘后记录, 保证版本库与磁盘内容一致 ──
            try:
                from version_manager import get_version_manager
                vm = get_version_manager()
                vm.save_version(fp, state._editor_rows, "保存")
            except Exception as e:
                log1("版本快照失败: {}".format(e), "error")
            from utils import show_toast
            show_toast(root, "✓ 脚本已保存 ({} 行)".format(len(state._editor_rows)), "success")
        except ImportError:
            messagebox.showerror("错误","需要安装 xlwt: pip install xlwt")
        except Exception as e:
            messagebox.showerror("保存失败","{}\n(原子写入失败, 原文件未受影响)".format(e))
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
            # P0-6 原子写: 同目录 .tmp → 备份旧文件为 .bak → os.replace 原子替换
            utils.atomic_save(fp, lambda tmp: wb.save(tmp), backup=True)
            state.filename=fp; state.has_script=True
            state._editor_modified=False
            script_name_var.set(os.path.basename(fp))
            edit_file_label.config(text=os.path.basename(fp))
            log1("脚本另存为: {}".format(fp))
            # ── 版本快照: 文件成功落盘后记录, 保证版本库与磁盘内容一致 ──
            try:
                from version_manager import get_version_manager
                get_version_manager().save_version(fp, state._editor_rows, "另存为")
            except Exception as e:
                log1("版本快照失败: {}".format(e), "error")
            from utils import show_toast
            show_toast(root, "已另存为: {}".format(os.path.basename(fp)), "success")
        except Exception as e:
            messagebox.showerror("另存为失败","{}\n(原子写入失败, 原文件未受影响)".format(e))

# ── 版本历史管理 ──
# 版本历史对话框已移至 dialogs.open_version_history


# AI 脚本生成器已移至 dialogs.open_ai_panel

def _clone_snippet_rows(rows):
    return [ScriptData(r.cmd_type, list(r.args)) for r in rows]


def _selected_editor_rows():
    """编辑器中被选中的行; 无选中则返回全部行。"""
    try:
        sel = tree.selection()
        if sel:
            out = []
            for i in sel:
                k = tree.index(i)
                if 0 <= k < len(state._editor_rows):
                    out.append(state._editor_rows[k])
            return out
        return list(state._editor_rows)
    except Exception:
        return list(state._editor_rows)


def _ask_insert_mode(n_existing):
    """非空编辑器插入片段时的三选一: 追加/插入到选中行前/替换全部。返回 mode 或 None。"""
    win = tkinter.Toplevel(root); win.title("插入片段")
    win.transient(root); win.grab_set(); win.configure(bg=C["bgc"])
    _set_window_icon(win); win.resizable(False, False)
    tkinter.Label(win, text="当前脚本已有 {} 行, 请选择插入方式:".format(n_existing),
        font=FONT_BODY, bg=C["bgc"], fg=C["fgb"]).pack(padx=18, pady=(14, 8))
    result = {"v": None}
    bar = tkinter.Frame(win, bg=C["bgc"]); bar.pack(padx=18, pady=(0, 14))
    def _pick(v):
        result["v"] = v; win.destroy()
    for _txt, _bg, _v in (("追加到末尾", C["sc"], "append"),
                          ("插入到选中行前", C["ac"], "before"),
                          ("替换全部", C["dg"], "replace"),
                          ("取消", C["bgc"], None)):
        tkinter.Button(bar, text=_txt, font=FONT_BUTTON, bg=_bg,
            fg=("white" if _bg != C["bgc"] else C["fgb"]), relief="flat", bd=1,
            padx=10, pady=3, cursor="hand2",
            command=lambda v=_v: _pick(v)).pack(side="left", padx=4)
    win.update_idletasks()
    try:
        win.geometry("+{}+{}".format(
            root.winfo_rootx() + max(0, (root.winfo_width() - win.winfo_width()) // 2),
            root.winfo_rooty() + 120))
    except Exception:
        pass
    root.wait_window(win)
    return result["v"]


def _insert_snippet_rows(rows):
    """按三选一语义插入片段; 空编辑器默认追加。"""
    rows = [r for r in (rows or [])]
    if not rows:
        log1("片段为空, 未插入", "warning"); return
    if not state._editor_rows:
        _push_undo()
        state._editor_rows.extend(_clone_snippet_rows(rows))
        _editor_sync_to_tree()
        log1("已插入片段 ({} 行)".format(len(rows)))
        return
    mode = _ask_insert_mode(len(state._editor_rows))
    if not mode:
        return
    _push_undo()
    if mode == "append":
        state._editor_rows.extend(_clone_snippet_rows(rows))
    elif mode == "before":
        sel = tree.selection()
        idx = tree.index(sel[0]) if sel else len(state._editor_rows)
        for off, sd in enumerate(_clone_snippet_rows(rows)):
            state._editor_rows.insert(idx + off, sd)
    else:  # replace
        state._editor_rows[:] = _clone_snippet_rows(rows)
    _editor_sync_to_tree()
    log1("已插入片段 ({} 行, {})".format(len(rows), mode))


def _save_selection_as_snippet(default_category="我的"):
    """把编辑器当前选中行 (无选中则全部) 另存为用户片段。"""
    import snippets
    rows = _selected_editor_rows()
    from utils import show_toast
    if not rows:
        show_toast(root, "没有可保存的行 (先选中若干行或在编辑器中添加命令)", "warning")
        return
    from tkinter import simpledialog
    name = simpledialog.askstring("保存为片段", "片段名称:", parent=root)
    if not name:
        return
    cat = simpledialog.askstring("保存为片段", "分类 (默认 我的):", parent=root,
        initialvalue=default_category or "我的")
    sid = snippets.save_user_snippet(name, cat or "我的", rows)
    if sid:
        show_toast(root, "片段已保存: {} ({} 行)".format(name, len(rows)), "success")
    else:
        show_toast(root, "片段保存失败", "error")


def _manage_snippet_dirs(on_changed=None):
    """管理片段库目录: 展示主库+追加库, 支持添加 / 移除 / 打开。"""
    import snippets
    win = tkinter.Toplevel(root); win.title("片段库目录")
    win.geometry("640x360+470+180"); win.transient(root); win.grab_set()
    win.configure(bg=C["bgc"]); _set_window_icon(win)
    win.columnconfigure(0, weight=1); win.rowconfigure(1, weight=1)
    tkinter.Label(win, text="片段库目录（离线可用；主库默认 = 项目 template 文件夹）",
        font=FONT_BODY, bg=C["bgc"], fg=C["fgb"]).grid(row=0, column=0, sticky="w", padx=12, pady=(10, 4))
    lb = tkinter.Listbox(win, font=FONT_SMALL, bg=C["ebg"], fg=C["fgb"], relief="solid", bd=1)
    lb.grid(row=1, column=0, sticky="nsew", padx=12)
    def _refresh():
        lb.delete(0, "end")
        for d in snippets.list_library_dirs():
            lb.insert("end", d)
    _refresh()
    bar = tkinter.Frame(win, bg=C["bgc"]); bar.grid(row=2, column=0, sticky="ew", padx=12, pady=(6, 10))
    def _add():
        p = filedialog.askdirectory(title="选择片段库目录", initialdir=APP_ROOT)
        if p and snippets.add_extra_dir(p):
            _refresh()
            if on_changed: on_changed()
            from utils import show_toast; show_toast(root, "已添加片段库目录", "success")
    def _remove():
        s = lb.curselection()
        if not s: return
        p = lb.get(s[0])
        if p == snippets.get_primary_dir(auto_create=False):
            from utils import show_toast; show_toast(root, "主库目录不可移除", "warning"); return
        snippets.remove_extra_dir(p); _refresh()
        if on_changed: on_changed()
    def _open():
        s = lb.curselection()
        if s:
            try: os.startfile(lb.get(s[0]))
            except Exception: pass
    tkinter.Button(bar, text="添加目录…", font=FONT_BUTTON, bg=C["ac"], fg="white",
        relief="flat", bd=1, padx=10, pady=3, cursor="hand2", command=_add).pack(side="left", padx=(0, 6))
    tkinter.Button(bar, text="移除选中", font=FONT_BUTTON, bg=C["dg"], fg="white",
        relief="flat", bd=1, padx=10, pady=3, cursor="hand2", command=_remove).pack(side="left", padx=(0, 6))
    tkinter.Button(bar, text="打开目录", font=FONT_BUTTON, bg=C["bgc"], fg=C["fgb"],
        relief="flat", bd=1, padx=10, pady=3, cursor="hand2", command=_open).pack(side="left")
    tkinter.Button(bar, text="关闭", font=FONT_BUTTON, bg=C["bgc"], fg=C["fgb"],
        relief="flat", bd=1, padx=10, pady=3, cursor="hand2", command=win.destroy).pack(side="right")


def _cmd_snippet():
    """「片段」库: 左分类 + 中列表(可搜索) + 右常驻预览; 底 [插入][存为片段][管理目录]。

    片段来源: 内置(templates.py) + 用户自定义(JSON) + 库目录 xls (默认 root/template)。
    """
    import snippets
    dlg = tkinter.Toplevel(root); dlg.title("片段库")
    dlg.geometry("840x560+420+130"); dlg.transient(root); dlg.configure(bg=C["bgc"])
    _set_window_icon(dlg)
    dlg.columnconfigure(0, weight=1); dlg.rowconfigure(1, weight=1)

    head = tkinter.Frame(dlg, bg=C["bgc"]); head.grid(row=0, column=0, sticky="ew", padx=10, pady=(8, 4))
    tkinter.Label(head, text="片段库", font=FONT_TITLE, bg=C["bgc"], fg=C["fgt"]).pack(side="left")
    tkinter.Label(head, text="搜索:", font=FONT_SMALL, bg=C["bgc"], fg=C["fgm"]).pack(side="left", padx=(16, 4))
    search_var = tkinter.StringVar(value="")
    tkinter.Entry(head, textvariable=search_var, font=FONT_SMALL, width=24, relief="solid", bd=1,
        bg=C["ebg"], fg=C["fgb"], insertbackground=C["fgt"]).pack(side="left")

    body = tkinter.Frame(dlg, bg=C["bgc"]); body.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 4))
    body.columnconfigure(1, weight=2); body.columnconfigure(2, weight=3); body.rowconfigure(0, weight=1)

    cat_box = tkinter.Listbox(body, font=FONT_SMALL, bg=C["ebg"], fg=C["fgb"], relief="solid",
        bd=1, width=10, exportselection=False, activestyle="none")
    cat_box.grid(row=0, column=0, sticky="ns", padx=(0, 6))

    mid = tkinter.Frame(body, bg=C["bgc"]); mid.grid(row=0, column=1, sticky="nsew", padx=(0, 6))
    mid.columnconfigure(0, weight=1); mid.rowconfigure(0, weight=1)
    stree = ttk.Treeview(mid, columns=("name", "src", "rows"), show="headings", selectmode="browse")
    stree.heading("name", text="片段"); stree.column("name", width=150, anchor="w")
    # §4.5 show="headings" 降级: 选中整行 acl 底 + 加粗 (无 #0 列不可挂左缘条)
    _bind_sel_bold(stree); _bind_row_hover(stree)
    stree.heading("src", text="来源"); stree.column("src", width=54, anchor="center")
    stree.heading("rows", text="行"); stree.column("rows", width=40, anchor="center")
    stree.grid(row=0, column=0, sticky="nsew")

    pv = tkinter.Frame(body, bg=C["logbg"], highlightbackground=C["bd"], highlightthickness=1)
    pv.grid(row=0, column=2, sticky="nsew")
    pv.columnconfigure(0, weight=1); pv.rowconfigure(0, weight=1)
    prev_txt = tkinter.Text(pv, font=FONT_LOG, bg=C["logbg"], fg=C["logfg"], wrap="word",
        relief="flat", bd=0, padx=8, pady=6, state="disabled")
    prev_txt.grid(row=0, column=0, sticky="nsew")
    psb = tkinter.Scrollbar(pv, orient="vertical", command=prev_txt.yview, width=8,
        relief="flat", bg=C["bd"], troughcolor=C["logbg"])
    psb.grid(row=0, column=1, sticky="ns"); prev_txt.config(yscrollcommand=psb.set)
    prev_txt.tag_configure("title", font=FONT_TITLE, foreground=C["fgt"])
    prev_txt.tag_configure("sep", foreground=C["bd"])
    prev_txt.tag_configure("cmd", foreground=C["ac"])
    prev_txt.tag_configure("todo", foreground=C["wn"])
    prev_txt.tag_configure("muted", foreground=C["fgm"])

    _SRC_CN = {"builtin": "内置", "user": "我的", "file": "文件"}
    _cache = {"items": []}

    def _cur_cat():
        try:
            s = cat_box.curselection()
            return cat_box.get(s[0]) if s else "全部"
        except Exception:
            return "全部"

    def _refresh_cats():
        cur = _cur_cat()
        if cur == "全部":
            cur = getattr(state, "SNIPPET_LAST_CAT", "全部") or "全部"
        cats = snippets.categories()
        cat_box.delete(0, "end")
        for c in cats:
            cat_box.insert("end", c)
        i = cats.index(cur) if cur in cats else 0
        cat_box.selection_clear(0, "end"); cat_box.selection_set(i); cat_box.see(i)

    def _show_preview(iid):
        try:
            it = _cache["items"][int(iid)]
        except Exception:
            return
        rows = snippets.get_snippet_rows(it["id"])
        prev_txt.config(state="normal"); prev_txt.delete("1.0", "end")
        if not rows:
            prev_txt.insert("end", "(无内容)", "muted")
        else:
            prev_txt.insert("end", "{}  (共 {} 行)\n".format(it["name"], len(rows)), "title")
            prev_txt.insert("end", "-" * 52 + "\n", "sep")
            for i, sd in enumerate(rows):
                prev_txt.insert("end", "{:02d}  ".format(i + 1), "muted")
                prev_txt.insert("end", str(sd.cmd_type), "cmd")
                prev_txt.insert("end", "   {}\n".format(
                    ", ".join(a if a else "-" for a in sd.args)))
                for a in sd.args:
                    if snippets.is_placeholder(a):
                        prev_txt.insert("end", "      ↑ 待填: {}\n".format(a), "todo")
        prev_txt.config(state="disabled")

    def _refresh_list():
        cat = _cur_cat()
        kw = (search_var.get() or "").strip().lower()
        for iid in stree.get_children():
            stree.delete(iid)
        _cache["items"] = []
        for it in snippets.list_snippets():
            if cat != "全部" and (it.get("category") or "其他") != cat:
                continue
            if kw and kw not in (it.get("name", "") or "").lower():
                continue
            rn = it.get("rows", -1)
            stree.insert("", "end", iid=str(len(_cache["items"])),
                values=(it["name"], _SRC_CN.get(it.get("source"), it.get("source")),
                        rn if rn >= 0 else "?"))
            _cache["items"].append(it)
        kids = stree.get_children()
        if kids:
            stree.selection_set(kids[0]); stree.focus(kids[0]); _show_preview(kids[0])

    def _on_cat(e=None):
        state.SNIPPET_LAST_CAT = _cur_cat()
        try: state.save_config()
        except Exception: pass
        _refresh_list()

    def _on_sel(e=None):
        s = stree.selection()
        if s: _show_preview(s[0])

    def _do_insert():
        s = stree.selection()
        if not s:
            from utils import show_toast; show_toast(root, "请先选择一个片段", "warning"); return
        try:
            sid = _cache["items"][int(s[0])]["id"]
        except Exception:
            return
        rows = snippets.get_snippet_rows(sid)
        dlg.destroy()
        _insert_snippet_rows(rows)

    def _do_manage():
        _manage_snippet_dirs(on_changed=lambda: (_refresh_cats(), _refresh_list()))

    btm = tkinter.Frame(dlg, bg=C["bgc"]); btm.grid(row=2, column=0, sticky="ew", padx=10, pady=(0, 8))
    tkinter.Label(btm, text="选中后点「插入」；占位符需改为实际值", font=FONT_SMALL,
        bg=C["bgc"], fg=C["fgm"]).pack(side="left")
    tkinter.Button(btm, text="插入", font=FONT_BUTTON, bg=C["sc"], fg="white", relief="flat",
        bd=1, padx=12, pady=3, cursor="hand2", command=_do_insert).pack(side="right", padx=(6, 0))
    tkinter.Button(btm, text="存为片段", font=FONT_BUTTON, bg=C["ac"], fg="white", relief="flat",
        bd=1, padx=10, pady=3, cursor="hand2",
        command=lambda: _save_selection_as_snippet(default_category=_cur_cat())).pack(side="right", padx=(6, 0))
    tkinter.Button(btm, text="管理目录…", font=FONT_BUTTON, bg=C["bgc"], fg=C["fgb"], relief="flat",
        bd=1, padx=10, pady=3, cursor="hand2", command=_do_manage).pack(side="right", padx=(6, 0))

    cat_box.bind("<<ListboxSelect>>", _on_cat)
    stree.bind("<<TreeviewSelect>>", _on_sel)
    stree.bind("<Double-1>", lambda e: _do_insert())
    try: search_var.trace_add("write", lambda *a: _refresh_list())
    except Exception: pass

    _refresh_cats(); _refresh_list()


def _cmd_template():
    """[已弃用] 旧「模板」对话框; 由 _cmd_snippet 取代 (保留兼容, 不再绑定按钮)。"""
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

    preview_txt = tkinter.Text(bottom_frame, font=FONT_LOG, bg=C["logbg"], fg=C["logfg"],
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
    """Vertical separator bar between button groups (尺寸走 utils.sp 令牌)."""
    s = tkinter.Frame(parent, bg=C["bd"], width=utils.sp(2), height=utils.sp(20))
    s.pack(side="left", fill="y", padx=utils.sp(4))


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
    # §4.5 show="headings" 降级: 选中整行 acl 底 + 加粗
    _bind_sel_bold(var_tree); _bind_row_hover(var_tree)
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
                var_tree.tag_configure("changed", background=themed("hlbg"))
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
    stack_text = tkinter.Text(stack_frame, font=FONT_LOG,
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
            
            stack_text.tag_configure("info", foreground=themed("ac"))
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
    # §4.5 show="headings" 降级: 选中整行 acl 底 + 加粗
    _bind_sel_bold(timing_tree); _bind_row_hover(timing_tree)
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
            timing_tree.tag_configure("ok", foreground=themed("sc"))
            timing_tree.tag_configure("fail", foreground=themed("dg"))
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
        edit_entry = tkinter.Entry(edit_win, font=FONT_LOG, width=35,
            relief="solid", bd=1, bg=C["ebg"], fg=C["fgb"],
            insertbackground=C["fgt"])
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
    """打开脚本市场窗口 (批次 2：委托独立模块 market_window)。

    工具栏「市场」按钮绑定保持不变；窗口/卡片/详情/账号区/安装进度实现全部
    移入 src/market_window.py (设计 §3)。安装成功回调 _market_on_install 负责
    落盘口径与载入编辑器。
    """
    import market_window
    market_window.open_market_window(root, on_install=_market_on_install)


def _market_on_install(xls_path):
    """市场安装成功回调：把脚本载入编辑器。

    script_dir 口径 (批次 2 固定，与 marketplace 安装落点一致)：
        state.script_dir = os.path.dirname(xls_path)
      - 包模式   : <save_dir>/market_scripts/<id>/  (脚本与拍平 png 同目录)
      - 旧单文件 : <save_dir>/                       (平铺)
    不再另行从 CONFIG_PATH 推算，消除批次 1 遗留的两套口径歧义。
    """
    try:
        state.filename = xls_path
        state.has_script = True
        state.script_dir = os.path.dirname(xls_path)
        script_name_var.set(os.path.basename(xls_path))
        edit_file_label.config(text=os.path.basename(xls_path))
        _editor_load_xls(xls_path)
        from utils import show_toast
        show_toast(root, "脚本已导入: {}".format(os.path.basename(xls_path)), "success")
    except Exception as e:
        log1("市场脚本导入失败: {}".format(e), "error")
        from utils import show_toast
        show_toast(root, "脚本导入失败: {}".format(e), "error")


def _tbtn(parent, text, cmd, bg_c=None, fg_c=None, tip=None):
    """脚本编辑工具栏按钮工厂: 与「执行控制」运行控制条同规格。

    字体 FONT_BUTTON (Microsoft YaHei UI 9pt bold)、内边距 padx=sp(8)/pady=sp(2)。
    §3.2/§4.8 扁平化: 取消旧 `bd=3` 浮雕, 改 `bd=0` + 1px highlight 发丝描边
    (合法边框集合 {bd=0, bd=1, 1px highlightthickness});
    hover 不再仅 `_darken()` 变暗, 次级改「底色 caption_hover + 描边 border_strong」双变化 (§4.9.2)。
    """
    if bg_c is None: bg_c = C["bgc"]
    if fg_c is None: fg_c = C["fgb"]
    neutral = bg_c in ("white", "#ffffff", C["bgc"], C["surface_alt"])
    if neutral:
        abg = C["caption_hover"]
    elif bg_c == C["ac"]:
        abg = C["ach"]          # primary hover 强调实心 (§4.9.2)
    else:
        abg = _darken(bg_c)
    b = tkinter.Button(parent, text=text, font=FONT_BUTTON, bg=bg_c, fg=fg_c,
        activebackground=abg, activeforeground=fg_c, relief="flat", bd=0,
        highlightthickness=1, highlightbackground=C["bd"],
        highlightcolor=C["focus"], takefocus=True,
        cursor="hand2", padx=utils.sp(8), pady=utils.sp(2), command=cmd)
    if tip:
        try: utils.attach_tooltip(b, tip)
        except Exception: pass

    # hover 双变化 (D3, §4.9.2): <Enter> 同时改「底色 + 描边」(非仅变暗);
    #   必须后于 tooltip 绑定 (add="+"; tooltip 的 <Enter>/<Leave> 未用 add)。
    hbd = C["border_strong"] if neutral else abg
    def _on_enter(_e):
        try: b.configure(bg=abg, highlightbackground=hbd)
        except Exception: pass
    def _on_leave(_e):
        try: b.configure(bg=bg_c, highlightbackground=C["bd"])
        except Exception: pass
    b.bind("<Enter>", _on_enter, add="+")
    b.bind("<Leave>", _on_leave, add="+")
    return b


def _init_toolbar_buttons():
    """Initialize toolbar buttons after all command functions are defined.

    按钮规格与「执行控制」对齐 (FONT_BUTTON 9pt bold, sp(8)/sp(2)); 全部带 tooltip。
    """
    _P = utils.sp(1)   # 组内按钮间距
    # Group 1: 行编辑 (增删 + 上下移)
    _tbtn(toolbar_inner,"+ 添加",_cmd_add_row,C["ac"],"white",
          tip="新增一行命令").pack(side="left",padx=_P)
    _tbtn(toolbar_inner,"- 删除",_cmd_del_row,C["dg"],"white",
          tip="删除选中行").pack(side="left",padx=_P)
    _sep(toolbar_inner)
    _tbtn(toolbar_inner,"↑",_cmd_move_up,tip="上移选中行").pack(side="left",padx=_P)
    _tbtn(toolbar_inner,"↓",_cmd_move_down,tip="下移选中行").pack(side="left",padx=_P)
    # Group 2: 文件操作
    _sep(toolbar_inner)
    _tbtn(toolbar_inner,"片段",_cmd_snippet,tip="插入/管理片段库 (离线可用)").pack(side="left",padx=_P)
    _tbtn(toolbar_inner,"新建",_cmd_new,C["ac"],"white",
          tip="新建空白脚本 (Ctrl+N)").pack(side="left",padx=_P)
    _tbtn(toolbar_inner,"打开",_cmd_open,C["ac"],"white",
          tip="打开脚本文件 (.xls) (Ctrl+O)").pack(side="left",padx=_P)
    _tbtn(toolbar_inner,"保存",_cmd_save,C["sc"],"white",
          tip="保存当前脚本 (Ctrl+S)").pack(side="left",padx=_P)
    _tbtn(toolbar_inner,"另存",_cmd_save_as,
          tip="另存为… (Ctrl+Shift+S)").pack(side="left",padx=_P)

    # Group 3: AI 与实用工具
    _sep(toolbar_inner)
    _tbtn(toolbar_inner,"AI生成",open_ai_panel,"#7C3AED","white",
          tip="AI 自然语言生成脚本").pack(side="left",padx=_P)
    _tbtn(toolbar_inner,"AI调试",open_ai_debug_dialog,"#7C3AED","white",
          tip="AI 自然语言调试 (由「执行控制」迁入)").pack(side="left",padx=_P)
    # 统一调色板: 仅 新建/打开(ac)、保存/运行(sc)、删除/停止(dg)、AI(紫) 着色,
    # 其余次要工具一律中性 (bgc/fgb), 三 Tab 保持一致
    _tbtn(toolbar_inner,"市场",_open_marketplace,tip="打开脚本市场").pack(side="left",padx=_P)
    _tbtn(toolbar_inner,"⊕ 取点",capture_mouse_position,
          tip="屏幕取点 (坐标/图像)").pack(side="left",padx=_P)
    _tbtn(toolbar_inner,"截屏",lambda: _screenshot_tool(),
          tip="区域截图 (由「执行控制」迁入)").pack(side="left",padx=_P)
    _tbtn(toolbar_inner,"版本",open_version_history,
          tip="版本历史").pack(side="left",padx=_P)

    # Group 4: 录制 (全局引用 btn_record, 供主题刷新/停止/录制完成回调更新状态)
    # 注意: _start_recording 定义于本函数之后, 须用 lambda 延迟绑定
    _sep(toolbar_inner)
    global btn_record
    btn_record = _tbtn(toolbar_inner,"● 录制",lambda: _start_recording(),C["dg"],"white",
                       tip="录制鼠标键盘操作 (Ctrl+Shift+Q 停止)")
    btn_record.pack(side="left",padx=_P)

    # 「调试 / 单步 / 变量」已移至「执行控制」Tab 运行控制条, 避免两处状态不同步
    _sep(toolbar_inner)
    _tbtn(toolbar_inner,"清空",_cmd_clear,C["dg"],"white",
          tip="清空所有命令行").pack(side="left",padx=_P)


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
    if not state.running:
        log1("脚本未运行，暂停/继续不可用", "warning")
        return
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
# ② 执行控制工具栏 (常驻 · 两 Tab 共用) —— 由原「执行控制」Tab 降级而来
#   运行控制条 → root row1 常驻;  运行日志 → 底部常驻面板(bottom_dock, 见 §⑥)
# 设计: docs/底部常驻日志面板重构设计方案.md
# ======================================================================
exec_bar = tkinter.Frame(root,bg=C["bg"])
exec_bar.grid(row=1,column=0,sticky="ew",padx=8,pady=(0,2))
exec_bar.columnconfigure(0,weight=1)

# ──────────────────────────────────────────────────────────────────────
# ① 运行控制条 (Run Control Bar): 脚本 / 次数 / 四键同组 / 调试入口
# ──────────────────────────────────────────────────────────────────────
card_run = create_card(exec_bar)
card_run.grid(row=0,column=0,sticky="ew",**PAD)
card_run.columnconfigure(0,weight=1)

run_bar_outer = tkinter.Frame(card_run, bg=C["bgc"])
run_bar_outer.grid(row=0,column=0,sticky="ew",padx=6,pady=4)
run_bar_outer.columnconfigure(0,weight=1)

run_canvas = tkinter.Canvas(run_bar_outer, bg=C["bgc"],
    height=utils.ctrl_h("ctrl_h_lg"), highlightthickness=0, bd=0)
run_scrollbar = tkinter.Scrollbar(run_bar_outer, orient="horizontal",
    command=run_canvas.xview, width=utils.sp(6))
run_bar = tkinter.Frame(run_canvas, bg=C["bgc"])

# 溢出提示「⇄」: 内容宽度超过可视宽度时显示 (item 11)
run_overflow_hint = tkinter.Label(run_bar_outer, text="⇄", font=FONT_SMALL,
    fg=C["fgm"], bg=C["bgc"])

def _run_update_overflow(event=None):
    try:
        bb = run_canvas.bbox("all")
        need = bb[2] if bb else 0
        if need > run_canvas.winfo_width() + utils.sp(2):
            run_overflow_hint.grid(row=0, column=1, sticky="e", padx=(utils.sp(2), 0))
        else:
            run_overflow_hint.grid_remove()
    except Exception:
        pass

def _run_on_bar_configure(event=None):
    try:
        run_canvas.configure(scrollregion=run_canvas.bbox("all"))
    except Exception:
        pass
    _run_update_overflow()

run_bar.bind("<Configure>", _run_on_bar_configure)
run_canvas.bind("<Configure>", lambda e: _run_update_overflow())
run_canvas.create_window((0, 0), window=run_bar, anchor="w")
run_canvas.configure(xscrollcommand=run_scrollbar.set)
run_canvas.grid(row=0, column=0, sticky="ew")
run_scrollbar.grid(row=1, column=0, sticky="ew")

def _run_on_wheel(event):
    if event.delta > 0:
        run_canvas.xview_scroll(-1, "units")
    else:
        run_canvas.xview_scroll(1, "units")

# ── ▶/■ 上下文分派: 按当前激活 Tab 决定运行脚本还是工作流 ──
# Tab 顺序: 0=脚本编辑, 1=工作流 (执行控制已升级为常驻工具栏, 不占 Tab)
def _shared_run(event=None):
    try:
        idx = notebook.index(notebook.select())
    except Exception:
        idx = 0
    if idx == 1:
        try: _wf_run()
        except Exception as e: log1("工作流启动失败: {}".format(e), "error")
    else:
        main_run()

def _shared_stop(event=None):
    try:
        idx = notebook.index(notebook.select())
    except Exception:
        idx = 0
    if idx == 1:
        try: _wf_stop()
        except Exception as e: log1("工作流停止失败: {}".format(e), "error")
    else:
        stop_execution()

def _tip_with_hotkey(label, attr):
    """生成「标签 (快捷键: X)」提示; 未配置快捷键时只显示标签。"""
    hk = getattr(state, attr, "") or ""
    return "{} (快捷键: {})".format(label, hk) if hk else label

# 脚本选择: 标签 + 最近脚本下拉 + 选择按钮
tkinter.Label(run_bar,text="脚本:",font=FONT_SMALL,fg=C["fgm"],
    bg=C["bgc"]).pack(side="left",padx=(2,2))

script_name_var=tkinter.StringVar(value="没有选择文件")
# 快速切换：下拉框 + 浏览按钮
_script_switcher = ttk.Combobox(run_bar, textvariable=script_name_var,
    font=FONT_BODY, state="readonly", width=26)
_script_switcher.pack(side="left",padx=(0,4))
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

tkinter.Button(run_bar,text="选择脚本",font=FONT_BUTTON,bg=C["ac"],fg="white",
    activebackground=C["ach"],activeforeground="white",relief="flat",bd=1,
    cursor="hand2",padx=8,pady=2,command=select_script).pack(side="left",padx=(0,4))

# 次数: 可编辑 + 预设 + 自定义
tkinter.Label(run_bar,text="次数:",font=FONT_SMALL,fg=C["fgm"],
    bg=C["bgc"]).pack(side="left",padx=(6,2))
loop_count_var=tkinter.StringVar(value="无限循环")
_loop_combo = ttk.Combobox(run_bar,textvariable=loop_count_var,
    values=('无限循环','1','5','10','50','自定义…'),width=8)
_loop_combo.pack(side="left",padx=(0,4))

def _on_loop_sel(event=None):
    """次数下拉: 选中「自定义…」时弹出输入框"""
    if loop_count_var.get() == "自定义…":
        try:
            from tkinter import simpledialog
            n = simpledialog.askinteger("运行次数","请输入运行次数:",
                parent=root, minvalue=1, maxvalue=1000000)
            loop_count_var.set("无限循环" if n is None else str(n))
        except Exception:
            loop_count_var.set("无限循环")
_loop_combo.bind("<<ComboboxSelected>>", _on_loop_sel)

# 执行参数就地化 (与工作流/设置共用同一口径: MAX_EXECUTION_MINUTES / STOP_ON_ERROR)
tkinter.Label(run_bar,text="最长:",font=FONT_SMALL,fg=C["fgm"],
    bg=C["bgc"]).pack(side="left",padx=(utils.sp(4),utils.sp(2)))
exec_maxmin_var = tkinter.StringVar(value=str(state.MAX_EXECUTION_MINUTES))
exec_maxmin_sp = tkinter.Spinbox(run_bar,textvariable=exec_maxmin_var,from_=0,to=1440,
    width=4,font=FONT_SMALL,bg=C["ebg"],fg=C["fgb"],relief="solid",bd=1)
exec_maxmin_sp.pack(side="left",padx=(0,utils.sp(2)))
tkinter.Label(run_bar,text="分",font=FONT_SMALL,fg=C["fgm"],
    bg=C["bgc"]).pack(side="left",padx=(0,utils.sp(4)))
exec_stoponerror_var = tkinter.BooleanVar(value=bool(state.STOP_ON_ERROR))
tkinter.Checkbutton(run_bar,text="出错即停",variable=exec_stoponerror_var,font=FONT_SMALL,
    fg=C["fgm"],bg=C["bgc"],activebackground=C["bgc"],activeforeground=C["fgb"],
    selectcolor=C["ebg"],bd=0,highlightthickness=0,
    command=lambda: _exec_params_commit()).pack(side="left",padx=(0,utils.sp(4)))

def _exec_params_commit():
    """就地执行参数写回 state (与设置/工作流同源, 无口径差异)"""
    try:
        exec_maxmin_var.set(str(max(0, int(float(exec_maxmin_var.get())))))
    except Exception:
        exec_maxmin_var.set("0")
    try:
        state.MAX_EXECUTION_MINUTES = int(exec_maxmin_var.get())
        state.STOP_ON_ERROR = bool(exec_stoponerror_var.get())
        state.save_config()
    except Exception:
        pass
exec_maxmin_sp.bind("<FocusOut>", lambda e: _exec_params_commit())
exec_maxmin_sp.bind("<Return>", lambda e: _exec_params_commit())

tkinter.Frame(run_bar, bg=C["bd"], width=utils.sp(2), height=utils.sp(22)).pack(
    side="left", fill="y", padx=utils.sp(6), pady=4)

# 四键同组: ▶ 运行 / ⏸ 暂停 / ⏭ 单步 / ■ 停止
# (尺寸走 utils.sp 令牌; 四键文字统一黑色, 与「运行」对齐)
btn_run=tkinter.Button(run_bar,text="▶ 运行",font=FONT_BUTTON,bg=C["sc"],fg="black",
    activebackground=_darken(C["sc"]),activeforeground="black",disabledforeground="black",
    relief="flat",bd=1,
    cursor="hand2",padx=utils.sp(8),pady=utils.sp(2),command=_shared_run)
btn_run.pack(side="left",padx=2)
btn_pause=tkinter.Button(run_bar,text="⏸ 暂停",font=FONT_BUTTON,bg=C["wn"],fg="black",
    activebackground=_darken(C["wn"]),activeforeground="black",disabledforeground="black",
    relief="flat",bd=1,
    cursor="hand2",padx=utils.sp(8),pady=utils.sp(2),command=toggle_pause)
btn_pause.pack(side="left",padx=2)
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

btn_step=tkinter.Button(run_bar,text="⏭ 单步",font=FONT_BUTTON,bg=C["ac"],fg="black",
    activebackground=C["ach"],activeforeground="black",disabledforeground="black",
    relief="flat",bd=1,
    cursor="hand2",padx=utils.sp(8),pady=utils.sp(2),command=_step_once)
btn_step.pack(side="left",padx=2)
btn_stop=tkinter.Button(run_bar,text="■ 停止",font=FONT_BUTTON,bg=C["dg"],fg="black",
    activebackground=_darken(C["dg"]),activeforeground="black",disabledforeground="black",
    relief="flat",bd=1,
    cursor="hand2",padx=utils.sp(8),pady=utils.sp(2),command=_shared_stop)
btn_stop.pack(side="left",padx=2)

# 四键 tooltip (含用户配置快捷键); 单步右键 = 单步模式开关 (调试态内聚)
try:
    utils.attach_tooltip(btn_run, _tip_with_hotkey("运行", "HOTKEY_RUN"))
    utils.attach_tooltip(btn_pause, _tip_with_hotkey("暂停/继续", "HOTKEY_PAUSE"))
    utils.attach_tooltip(btn_stop, _tip_with_hotkey("停止", "HOTKEY_STOP"))
    utils.attach_tooltip(btn_step, "左键单步执行一步；右键切换「单步模式」(每行暂停)")
except Exception:
    pass
btn_step_mode = btn_step   # 供 _toggle_step_mode 高亮 (调试态归执行控制)
btn_step.bind("<Button-3>", lambda e: _toggle_step_mode())

tkinter.Frame(run_bar, bg=C["bd"], width=utils.sp(2), height=utils.sp(22)).pack(
    side="left", fill="y", padx=utils.sp(6), pady=4)

# 调试 / 变量入口 (调试态统一归此; 脚本编辑工具栏已移除该组, 避免两处状态不同步)
btn_debug_exec=tkinter.Button(run_bar,text="⚑ 调试",font=FONT_BUTTON,bg=C["bgc"],fg=C["fgb"],
    activebackground=C["acl"],activeforeground=C["fgb"],relief="flat",bd=1,
    cursor="hand2",padx=utils.sp(8),pady=utils.sp(2),command=_toggle_debug_mode)
btn_debug_exec.pack(side="left",padx=2)
btn_vars_exec=tkinter.Button(run_bar,text="☰ 变量",font=FONT_BUTTON,bg=C["bgc"],fg=C["fgb"],
    activebackground=C["acl"],activeforeground=C["fgb"],relief="flat",bd=1,
    cursor="hand2",padx=utils.sp(8),pady=utils.sp(2),command=_show_variables_window)
btn_vars_exec.pack(side="left",padx=2)
btn_debug_mode = btn_debug_exec   # 供 _toggle_debug_mode 高亮
try:
    utils.attach_tooltip(btn_debug_exec, "调试模式 (断点/变量监视)")
    utils.attach_tooltip(btn_vars_exec, "变量监视器")
except Exception:
    pass

# ──────────────────────────────────────────────────────────────────────
# ② 执行仪表盘已并入底部状态栏 (dash_info 控制信息 + progress_bar 进度条)
# ──────────────────────────────────────────────────────────────────────

# ⑥ 底部常驻日志面板 (两 Tab 共用) —— 原「执行控制」Tab 的日志卡, 换父到 bottom_dock
card_log = create_card(bottom_dock)
card_log.pack(fill="both",expand=True,padx=6,pady=(0,4))
card_log.columnconfigure(0,weight=1); card_log.rowconfigure(2,weight=1)

lh=tkinter.Frame(card_log,bg=C["bgc"]); lh.grid(row=0,column=0,sticky="ew",**PI)
lh.columnconfigure(0,weight=1)
tkinter.Label(lh,text="运行日志",font=FONT_BODY,fg=C["fgm"],bg=C["bgc"]).grid(
    row=0,column=0,sticky="w")
log_auto_scroll = tkinter.BooleanVar(value=True)
tkinter.Checkbutton(lh,text="自动滚动",variable=log_auto_scroll,font=FONT_SMALL,
    fg=C["fgm"],bg=C["bgc"],activebackground=C["bgc"],activeforeground=C["fgb"],
    selectcolor=C["ebg"],bd=0,highlightthickness=0,
    command=lambda: _log_autoscroll_changed()).grid(row=0,column=1,sticky="e")

# ── 日志面板一键收起/展开 + 拖高 (PanedWindow pane, 默认 160 / min 60) ──
_log_dock_state = {"visible": True, "h": 160}

def _toggle_log_dock(event=None):
    """收起/展开底部常驻日志面板 (记忆收起前高度)。"""
    try:
        if _log_dock_state["visible"]:
            try: _log_dock_state["h"] = bottom_dock.winfo_height() or 160
            except Exception: pass
            main_paned.forget(bottom_dock)
            _log_dock_state["visible"] = False
        else:
            main_paned.add(bottom_dock, minsize=60,
                           height=_log_dock_state["h"], stretch="never")
            _log_dock_state["visible"] = True
        try:
            log_dock_toggle_btn.config(
                text=("▾ 日志" if _log_dock_state["visible"] else "▸ 日志"))
        except Exception:
            pass
    except Exception:
        pass

def _on_log_sash_release(event=None):
    """拖动 sash 后记忆日志面板高度。"""
    try:
        if _log_dock_state["visible"]:
            _log_dock_state["h"] = max(60, bottom_dock.winfo_height())
    except Exception:
        pass
main_paned.bind("<ButtonRelease-1>", _on_log_sash_release)

_log_collapse_btn = tkinter.Button(lh,text="▾ 收起",font=FONT_SMALL,bg=C["bgc"],fg=C["fgb"],
    activebackground=C["acl"],relief="flat",bd=1,cursor="hand2",padx=6,pady=1,
    command=_toggle_log_dock)
_log_collapse_btn.grid(row=0,column=2,sticky="e",padx=(4,0))

# 将日志 Dock 作为 pane#1 加入 (默认高 160, 最小 60)
main_paned.add(bottom_dock, minsize=60, height=160, stretch="never")

# ③ 日志工具条: 级别过滤 / 关键字搜索 / 清空 / 导出
lt=tkinter.Frame(card_log,bg=C["bgc"]); lt.grid(row=1,column=0,sticky="ew",padx=6,pady=(0,2))
tkinter.Label(lt,text="级别:",font=FONT_SMALL,fg=C["fgm"],bg=C["bgc"]).pack(side="left")
log_level_var = tkinter.StringVar(value="全部")
_log_level_combo = ttk.Combobox(lt,textvariable=log_level_var,state="readonly",width=8,
    values=("全部","INFO","SUCCESS","WARNING","ERROR"))
_log_level_combo.pack(side="left",padx=(2,8))
_log_level_combo.bind("<<ComboboxSelected>>", lambda e: _log_apply_filter(full=True))
tkinter.Label(lt,text="搜索:",font=FONT_SMALL,fg=C["fgm"],bg=C["bgc"]).pack(side="left")
log_search_var = tkinter.StringVar(value="")
_log_search_entry = tkinter.Entry(lt,textvariable=log_search_var,font=FONT_SMALL,
    width=16,relief="solid",bd=1,bg=C["ebg"],fg=C["fgb"],insertbackground=C["fgt"])
_log_search_entry.pack(side="left",padx=(2,2))
_log_search_entry.bind("<Return>", lambda e: _log_search_next(reset=True))
tkinter.Button(lt,text="搜索",font=FONT_SMALL,bg=C["bgc"],fg=C["fgb"],
    activebackground=C["acl"],relief="flat",bd=1,cursor="hand2",padx=6,pady=1,
    command=lambda: _log_search_next(reset=True)).pack(side="left",padx=2)
tkinter.Button(lt,text="下一个",font=FONT_SMALL,bg=C["bgc"],fg=C["fgb"],
    activebackground=C["acl"],relief="flat",bd=1,cursor="hand2",padx=6,pady=1,
    command=lambda: _log_search_next(reset=False)).pack(side="left",padx=2)
tkinter.Button(lt,text="导出",font=FONT_SMALL,bg=C["ac"],fg="white",
    activebackground=C["ach"],relief="flat",bd=1,cursor="hand2",padx=6,pady=1,
    command=lambda: _log_export_view()).pack(side="right",padx=2)
tkinter.Button(lt,text="清空",font=FONT_SMALL,bg=C["bgc"],fg=C["fgb"],
    activebackground=C["acl"],relief="flat",bd=1,cursor="hand2",padx=6,pady=1,
    command=lambda: _log_clear_view()).pack(side="right",padx=2)

log_frame = tkinter.Frame(card_log,bg=C["logbg"])
log_frame.grid(row=2,column=0,sticky="nsew",padx=6,pady=(0,4))
log_frame.columnconfigure(0,weight=1); log_frame.rowconfigure(0,weight=1)

rz=tkinter.Text(log_frame,font=FONT_LOG,fg=C["logfg"],bg=C["logbg"],
    wrap="word",relief="flat",bd=0,padx=6,pady=4,insertbackground=C["fgt"],
    selectbackground=C["acl"],selectforeground=C["fgt"],undo=True,maxundo=50)
rz.grid(row=0,column=0,sticky="nsew")
scroll=tkinter.Scrollbar(log_frame,width=6,relief="flat",elementborderwidth=0,
    bg=C["bd"],activebackground=C["fgm"],troughcolor=C["logbg"])
scroll.grid(row=0,column=1,sticky="ns")

scroll.config(command=rz.yview); rz.config(yscrollcommand=scroll.set)
rz.tag_configure("info",foreground=C["log_info"])
rz.tag_configure("success",foreground=C["sc"])
rz.tag_configure("warning",foreground=C["wn"])
rz.tag_configure("error",foreground=C["dg"])
# §4.6 级别左缘 2px 色条: 行首字形 ▌(U+258C) + cbar_<tag> 颜色 tag
#   (tkinter Text 无 per-row 边框, 按文档 §4.6 以行首窄形字符承载色条)
_CBAR_LEVEL_COLOR = {
    "cbar_info": C["log_info"], "cbar_success": C["log_success"],
    "cbar_warning": C["log_warn"], "cbar_error": C["log_error"],
}
for _ctag, _ccol in _CBAR_LEVEL_COLOR.items():
    rz.tag_configure(_ctag, foreground=_ccol)
rz.tag_configure("lzebra", background=C["zebra"])   # §4.6 行 zebra 极淡
# ③ 日志工具条: 搜索高亮 + 级别过滤隐藏 (elide 在旧 Tk 上可能不支持, 容错降级)
#   §4.6 命中色改 token (删除硬编码 #FFEB3B); 1px ac 描边受 Tk Text tag 能力限制降级为底色高亮
rz.tag_configure("search_hit",background=C["search_hit_bg"],foreground=C["fgt"])
try:
    rz.tag_configure("lf_hide",elide=True)
except Exception:
    pass

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


# 日志卡底部动作条已整体移除:
#   「■ 停止」→ 运行控制条(四键同组); 「⊕ 屏幕取点」→ 与脚本编辑「⊕ 取点」重复, 删除;
#   「截屏 / [AI] 调试」→ 脚本编辑工具栏; 「? 帮助」→ 顶部「?」图标。

# ======================================================================
# 执行控制辅助: 耗时格式化 / 四态按钮状态机 / 日志工具条
# ======================================================================
def _fmt_dur(sec):
    """秒 → mm:ss / hh:mm:ss (用于仪表盘耗时与 ETA)"""
    try:
        s = int(max(0, sec))
    except Exception:
        return "--:--"
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h > 0:
        return "{:02d}:{:02d}:{:02d}".format(h, m, sec)
    return "{:02d}:{:02d}".format(m, sec)


def _update_exec_buttons(code):
    """执行控制四键的文案/配色切换 (0=就绪 1=已暂停 2=运行中 3=已停止)。

    ⚠ 不再禁用任何按钮 —— 四键始终可点, 各命令 (main_run/toggle_pause/
      _step_once/stop_execution) 内部自行守卫非法时机, 避免非运行态下
      暂停/单步/停止点不动。此处仅切换「暂停 ⇄ 继续」文案与底色。
    """
    # 防御性恢复可点击 (万一被外部置为 disabled)
    for _b in (btn_run, btn_pause, btn_step, btn_stop):
        try: _b.config(state="normal")
        except Exception: pass
    try:
        if code == 1:      # 已暂停
            btn_pause.config(text="▶ 继续", bg=C["ac"])
        else:              # 就绪 / 运行中 / 已停止
            btn_pause.config(text="⏸ 暂停", bg=C["wn"])
    except Exception:
        pass


# ③ 日志工具条: 级别过滤 / 关键字搜索 / 清空 / 导出
_LOG_LEVEL_TAGS = ("info", "success", "warning", "error")
_logfilter = {"applied": 0}   # 已应用过滤到的行号 (增量)


def _log_autoscroll_changed():
    """切换自动滚动: 重新开启时立即滚到底部"""
    try:
        if log_auto_scroll.get():
            rz.see("end")
    except Exception:
        pass


def _log_line_tag(line_no):
    """返回某行(1-based)命中的级别 tag, 无则返回空串"""
    try:
        names = rz.tag_names("{}.0".format(line_no))
    except Exception:
        return ""
    for t in _LOG_LEVEL_TAGS:
        if t in names:
            return t
    # 行首字形 ▌ 覆盖了级别 tag 起点时, 由 cbar_<tag> 反推级别 (§4.6)
    for _ct, _lv in _CBAR_LEVEL_OF.items():
        if _ct in names:
            return _lv
    return ""


# §4.6 左缘 2px 色条: 级别 tag ↔ cbar_<tag> ↔ 行首字形 ▌
_CBAR_LEVEL_OF = {"info": "cbar_info", "success": "cbar_success",
                  "warning": "cbar_warning", "error": "cbar_error"}
_cbar_state = {"done": 0}


def _log_apply_cbar():
    """为新增日志行: 行首插入 ▌ 并上 cbar_<tag> 色, 偶行加极淡 zebra (§4.6)。

    机制: tkinter Text 无 per-row 边框, 故按文档 §4.6 以「行首窄形字符 ▌(U+258C)
    + cbar_<tag> 颜色 tag」承载左缘 2px 色条; 级别由 tag 判定 (§2.5)。
    D1 幂等: **逐行处理成功后立即推进** _cbar_state["done"], 并以「行首已含 ▌」二次
    防重 —— 旧实现仅在整循环结束后赋值, 一旦中途抛错 (异常被外层吞掉) 会重入并对
    已插过 ▌ 的行再次插入, 造成色条重复。
    """
    try:
        total = int(float(rz.index("end-1c")))
    except Exception:
        return
    for i in range(_cbar_state["done"] + 1, total + 1):
        try:
            line = rz.get("{}.0".format(i), "{}.end".format(i))
            # 幂等: 行首已含 ▌ → 本行已处理过, 直接推进而不重复插入
            if line.startswith("▌"):
                _cbar_state["done"] = i
                continue
            # Text 末尾隐含空行: 跳过且不推进, 留待有内容后再处理
            if line == "":
                continue
            lv = _log_line_tag(i)
            ctag = _CBAR_LEVEL_OF.get(lv, "cbar_info")
            rz.insert("{}.0".format(i), "▌")
            rz.tag_add(ctag, "{}.0".format(i), "{}.1".format(i))
            if i % 2 == 0:
                rz.tag_add("lzebra", "{}.1".format(i), "{}.end".format(i))
            _cbar_state["done"] = i
        except Exception:
            # 单行失败: 保留 done 于上一成功行; 下次以「行首▌」判定避免重复
            continue


def _log_apply_filter(full=False):
    """按级别过滤日志行 (用 elide 隐藏不匹配行). 全部=显示所有"""
    try:
        sel = log_level_var.get()
        total = int(float(rz.index("end-1c")))
        start = 1 if full else (_logfilter["applied"] + 1)
        if start < 1:
            start = 1
        for i in range(start, total + 1):
            line_tag = _log_line_tag(i)
            if sel == "全部":
                match = True
            elif sel == "INFO":
                match = line_tag in ("", "info")
            else:
                match = (line_tag == sel.lower())
            a = "{}.0".format(i); b = "{}.end+1c".format(i)
            if match:
                rz.tag_remove("lf_hide", a, b)
            else:
                rz.tag_add("lf_hide", a, b)
        _logfilter["applied"] = total
        # 过滤生效时, 自动滚动到最后一个可见行
        if log_auto_scroll.get() and sel != "全部":
            for i in range(total, 0, -1):
                if "lf_hide" not in rz.tag_names("{}.0".format(i)):
                    rz.see("{}.0".format(i)); break
    except Exception:
        pass


def _log_search_next(reset=False):
    """关键字搜索: reset=True 从头开始, 否则跳到下一个匹配"""
    try:
        kw = log_search_var.get()
        rz.tag_remove("search_hit", "1.0", "end")
        if not kw:
            return
        pos = "1.0" if reset else "{}+1c".format(rz.index("insert"))
        idx = rz.search(kw, pos, stopindex="end", nocase=True)
        if not idx:
            idx = rz.search(kw, "1.0", stopindex="end", nocase=True)
        if idx:
            end = "{}+{}c".format(idx, len(kw))
            rz.tag_add("search_hit", idx, end)
            rz.mark_set("insert", end)
            rz.see(idx)
        else:
            from utils import show_toast
            show_toast(root, "未找到: {}".format(kw), "warning", 1500)
    except Exception:
        pass


def _log_clear_view():
    """清空日志视图 (不删除磁盘日志与内存缓冲)"""
    try:
        rz.delete("1.0", "end")
        _logfilter["applied"] = 0
        _cbar_state["done"] = 0
        rz.tag_remove("search_hit", "1.0", "end")
    except Exception:
        pass


def _log_export_view():
    """导出当前(可见)日志视图到文件"""
    try:
        default_name = "acrpa_log_{}.txt".format(
            datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
        fp = filedialog.asksaveasfilename(
            title="导出运行日志", initialdir=APP_ROOT, initialfile=default_name,
            defaultextension=".txt", filetypes=[("文本文件", "*.txt"), ("日志", "*.log")])
        if not fp:
            return
        total = int(float(rz.index("end-1c")))
        lines = []
        for i in range(1, total + 1):
            if "lf_hide" in rz.tag_names("{}.0".format(i)):
                continue
            lines.append(rz.get("{}.0".format(i), "{}.end".format(i)))
        with open(fp, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + ("\n" if lines else ""))
        from utils import show_toast
        show_toast(root, "日志已导出: {}".format(os.path.basename(fp)), "success")
    except Exception as e:
        log1("日志导出失败: {}".format(e), "error")


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
notebook.add(tab_workflow, text="  工作流  ")   # 第2个Tab (执行控制已升级为常驻工具栏)
# 设置已分离为独立窗口 (settings_window.open_settings_window)，不再占用 Tab
tab_workflow.columnconfigure(0, weight=1)
tab_workflow.rowconfigure(0, weight=0)
tab_workflow.rowconfigure(1, weight=1)

# ── 工具栏 (与「脚本编辑」「执行控制」同规格: ctrl_h_lg / sp 令牌 / 溢出提示) ──
wf_toolbar_outer = tkinter.Frame(tab_workflow, bg=C["bg"])
wf_toolbar_outer.grid(row=0, column=0, sticky="ew",
    padx=utils.sp(6), pady=(utils.sp(4), utils.sp(2)))
wf_toolbar_outer.columnconfigure(0, weight=1)

wf_canvas = tkinter.Canvas(wf_toolbar_outer, bg=C["bg"],
    height=utils.ctrl_h("ctrl_h_lg"), highlightthickness=0, bd=0)
wf_scrollbar = tkinter.Scrollbar(wf_toolbar_outer, orient="horizontal",
    command=wf_canvas.xview, width=utils.sp(6))
wf_toolbar = tkinter.Frame(wf_canvas, bg=C["bg"])

# 溢出提示「⇄」: 内容宽度超过可视宽度时显示 (三 Tab 一致)
wf_toolbar_overflow_hint = tkinter.Label(wf_toolbar_outer, text="⇄", font=FONT_SMALL,
    fg=C["fgm"], bg=C["bg"])

def _wf_update_overflow(event=None):
    try:
        bb = wf_canvas.bbox("all")
        need = bb[2] if bb else 0
        if need > wf_canvas.winfo_width() + utils.sp(2):
            wf_toolbar_overflow_hint.grid(row=0, column=1, sticky="e", padx=(utils.sp(2), 0))
        else:
            wf_toolbar_overflow_hint.grid_remove()
    except Exception:
        pass

wf_toolbar.bind("<Configure>",
    lambda e: (wf_canvas.configure(scrollregion=wf_canvas.bbox("all")),
               _wf_update_overflow()))

wf_canvas.create_window((0, 0), window=wf_toolbar, anchor="w")
wf_canvas.configure(xscrollcommand=wf_scrollbar.set)
wf_canvas.bind("<Configure>", lambda e: _wf_update_overflow())

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
_wfp = utils.sp(1)
_tbtn(wf_toolbar, "新建", lambda: _wf_new(), C["ac"], "white",
      tip="新建工作流").pack(side="left", padx=_wfp)
_tbtn(wf_toolbar, "打开", lambda: _wf_open(), C["ac"], "white",
      tip="打开工作流文件").pack(side="left", padx=_wfp)
_tbtn(wf_toolbar, "保存", lambda: _wf_save(), C["sc"], "white",
      tip="保存工作流").pack(side="left", padx=_wfp)
_tbtn(wf_toolbar, "导出模板", lambda: _wf_export_template(),
      tip="导出为工作流模板").pack(side="left", padx=_wfp)
_sep(wf_toolbar)
_tbtn(wf_toolbar, "+ 脚本", lambda: _wf_add_step("script"),
      tip="添加脚本步骤").pack(side="left", padx=_wfp)
_tbtn(wf_toolbar, "+ 并行", lambda: _wf_add_step("parallel"),
      tip="添加并行步骤").pack(side="left", padx=_wfp)
_tbtn(wf_toolbar, "+ 条件", lambda: _wf_add_step("condition"),
      tip="添加条件分支").pack(side="left", padx=_wfp)
_tbtn(wf_toolbar, "+ 等待", lambda: _wf_add_step("wait"),
      tip="添加等待步骤").pack(side="left", padx=_wfp)
_tbtn(wf_toolbar, "+ 变量", lambda: _wf_add_step("variable"),
      tip="添加变量设置").pack(side="left", padx=_wfp)
_tbtn(wf_toolbar, "+ 循环", lambda: _wf_add_step("loop"),
      tip="添加循环块").pack(side="left", padx=_wfp)
_sep(wf_toolbar)
# 「运行 / 停止」已移除: 由 ② 执行控制工具栏 (root row1) 按当前 Tab 分派承接
_sep(wf_toolbar)
# 执行控制: 循环次数 + 无限最长
tkinter.Label(wf_toolbar, text="循环:", font=FONT_SMALL, bg=C["bg"], fg=C["fgm"]).pack(side="left", padx=(0, 2))
wf_loop_var = tkinter.StringVar(value="1")
tkinter.Spinbox(wf_toolbar, textvariable=wf_loop_var, from_=1, to=9999, width=3,
    font=FONT_SMALL, bg=C["ebg"], fg=C["fgb"], relief="solid", bd=1).pack(side="left", padx=(0, 6))
tkinter.Label(wf_toolbar, text="次 | 最长:", font=FONT_SMALL, bg=C["bg"], fg=C["fgm"]).pack(side="left", padx=(0, 2))
# 执行参数口径统一: 「最长执行分钟」与「执行控制」运行控制条共用同一个变量/state 键
wf_maxmin_var = exec_maxmin_var
tkinter.Spinbox(wf_toolbar, textvariable=wf_maxmin_var, from_=0, to=1440, width=4,
    font=FONT_SMALL, bg=C["ebg"], fg=C["fgb"], relief="solid", bd=1).pack(side="left", padx=(0, 2))
tkinter.Label(wf_toolbar, text="分", font=FONT_SMALL, bg=C["bg"], fg=C["fgm"]).pack(side="left", padx=(0, 6))
_tbtn(wf_toolbar, "变量管理", lambda: _wf_open_variable_manager(),
      tip="管理工作流变量").pack(side="left", padx=_wfp)
_sep(wf_toolbar)
_tbtn(wf_toolbar, "截图", lambda: _wf_export_screenshot(),
      tip="导出流程图截图").pack(side="left", padx=_wfp)
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
_wf_view_btn = _tbtn(wf_toolbar, "[+] 流程图", _wf_toggle_view,
                     tip="显示 / 隐藏流程图")
_wf_view_btn.pack(side="left", padx=_wfp)

# Wire scheduler to GUI
def _wf_scheduler_callback():
    _wf_update_status()
    _wf_update_flowchart()
    _wf_update_log()

# ── 工作流名称 ──
wf_name_var = tkinter.StringVar(value="未命名工作流")
wf_name_entry = tkinter.Entry(wf_toolbar, textvariable=wf_name_var,
    font=FONT_BODY, width=20, relief="solid", bd=1,
    bg=C["ebg"], fg=C["fgb"], insertbackground=C["fgt"])
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
tkinter.Label(wf_lib_frame, text="⊞ 操作库", font=FONT_TITLE,
    bg=C["bgc"], fg=C["fgt"]).grid(row=0, column=0, sticky="ew", padx=8, pady=(6, 2))

# 搜索框
wf_lib_search = tkinter.Entry(wf_lib_frame, font=FONT_SMALL,
    relief="solid", bd=1, bg=C["ebg"], fg=C["fgb"], insertbackground=C["fgt"])
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
               "wait": "~ 等待", "loop": "↻ 循环", "command": "↯ 命令",
               "variable": "✚ 变量", "log": "✉ 日志"}

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
_FLOW_ICONS = {"script": "[>]", "parallel": "⫼", "condition": "◇", "wait": "⧗",
               "loop": "↻", "command": "↯", "variable": "✚", "log": "✉"}
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
        cat_id = wf_lib_tree.insert("", "end", text="▫ {}".format(category), open=True)
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
            font=FONT_BODY, fill=C["fgm"], anchor="center")
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
            display = "○ {} #{} | {} (禁用)".format(icon, i + 1, detail)
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
            font=FONT_SMALL_BOLD,
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
            label = "○ " + label
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
            font=FONT_BODY, bg=C["ebg"], fg=C["fgb"],
            insertbackground=C["fgt"]).pack(side="left", padx=(0, 4))
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
            font=FONT_LOG, bg=C["ebg"], fg=C["fgb"],
            insertbackground=C["fgt"]).pack(pady=4)
        tkinter.Label(dlg, text="(使用 ${var} 引用变量)", font=FONT_SMALL,
            bg=C["bgc"], fg=C["fgm"]).pack()
        tkinter.Label(dlg, text="成立时执行:", bg=C["bgc"], fg=C["fgb"]).pack(pady=(8, 2))
        then_var = tkinter.StringVar(value=step.get("then", ""))
        tkinter.Entry(dlg, textvariable=then_var, width=40,
            font=FONT_BODY, bg=C["ebg"], fg=C["fgb"],
            insertbackground=C["fgt"]).pack(pady=2)
        tkinter.Label(dlg, text="不成立时执行:", bg=C["bgc"], fg=C["fgb"]).pack(pady=(4, 2))
        else_var = tkinter.StringVar(value=step.get("else", ""))
        tkinter.Entry(dlg, textvariable=else_var, width=40,
            font=FONT_BODY, bg=C["ebg"], fg=C["fgb"],
            insertbackground=C["fgt"]).pack(pady=2)
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
            font=FONT_BODY, bg=C["ebg"], fg=C["fgb"],
            insertbackground=C["fgt"]).pack(pady=4)
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
            tkinter.Entry(pf, textvariable=pv, width=5, font=FONT_LOG,
                bg=C["ebg"], fg=C["fgb"], relief="solid", bd=1,
                insertbackground=C["fgt"]).grid(
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
            font=FONT_LOG, bg=C["ebg"], fg=C["fgb"],
            insertbackground=C["fgt"]).pack(pady=2)
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
            font=FONT_LOG, bg=C["ebg"], fg=C["fgb"],
            insertbackground=C["fgt"]).pack(pady=2)
        tkinter.Label(dlg, text="变量值:", bg=C["bgc"], fg=C["fgb"]).pack(pady=(8, 2))
        vv_var = tkinter.StringVar(value=step.get("var_value", ""))
        tkinter.Entry(dlg, textvariable=vv_var, width=30,
            font=FONT_LOG, bg=C["ebg"], fg=C["fgb"],
            insertbackground=C["fgt"]).pack(pady=2)
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
            font=FONT_BODY, bg=C["ebg"], fg=C["fgb"],
            insertbackground=C["fgt"]).pack(pady=4)
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
        font=FONT_SMALL, bg=C["ebg"], fg=C["fgb"],
        insertbackground=C["fgt"]).pack(side="left")

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
                bg=C["ebg"], fg=C["fgb"], insertbackground=C["fgt"])
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
    # §4.5 show="headings" 降级: 选中整行 acl 底 + 加粗
    _bind_sel_bold(var_tree); _bind_row_hover(var_tree)
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
        bg=C["ebg"], fg=C["fgb"], insertbackground=C["fgt"])
    vn_entry.pack(side="left", padx=2)
    tkinter.Label(entry_frame, text="值:", font=FONT_SMALL,
        bg=C["bgc"], fg=C["fgm"]).pack(side="left", padx=(6, 2))
    vv_entry = tkinter.Entry(entry_frame, width=16, font=FONT_SMALL,
        bg=C["ebg"], fg=C["fgb"], insertbackground=C["fgt"])
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
# Status Bar — 状态文本 + 控制信息 + 执行进度条 (进度条自适应窗口宽度)
# ======================================================================
_DASH_EMPTY = "尚未运行 · 选择脚本后点击运行"
status_bar = tkinter.Frame(root,bg=C["bg"])
status_bar.grid(row=3,column=0,sticky="ew",padx=10,pady=(3,6))
status_bar.columnconfigure(2,weight=1)
status_text = tkinter.Label(status_bar,text="就绪 — 请选择脚本文件开始",
    font=FONT_SMALL,fg=C["fgm"],bg=C["bg"],anchor="w")
status_text.grid(row=0,column=0,sticky="w")

# 控制信息 (循环/行/已用/ETA/成功·失败) — 与进度条同排
dash_info = tkinter.Label(status_bar,text=_DASH_EMPTY,font=FONT_SMALL,
    fg=C["fgm"],bg=C["bg"],anchor="w")
dash_info.grid(row=0,column=1,sticky="w",padx=(12,8))

# 执行进度条 (加高 8px; 数值刷新复用 _periodic 的 cache 去重)
try:
    style.configure("Exec.Horizontal.TProgressbar", thickness=8)
except Exception:
    pass
progress_bar=ttk.Progressbar(status_bar,mode="determinate",
    style="Exec.Horizontal.TProgressbar")
progress_bar.grid(row=0,column=2,sticky="ew")

# 底部日志面板展开入口 (收起后仍可见, 常驻状态栏右侧)
log_dock_toggle_btn = tkinter.Label(status_bar,text="▾ 日志",font=FONT_SMALL,
    fg=C["fgm"],bg=C["bg"],cursor="hand2")
log_dock_toggle_btn.grid(row=0,column=3,sticky="e",padx=(8,0))
log_dock_toggle_btn.bind("<Button-1>", lambda e: _toggle_log_dock())

# ======================================================================
# Periodic Updates
# ======================================================================

def _periodic():
    if state._closing: return
    # ③ 自动滚动: 关闭时保留当前视口, 避免新日志把视图强行拉到底部
    if log_auto_scroll.get():
        try: _tlog.flush(root)
        except Exception: return
        _log_apply_cbar()
    else:
        _prev_y = rz.yview()
        try: _tlog.flush(root)
        except Exception: return
        try: _log_apply_cbar()
        except Exception: pass
        try: rz.yview_moveto(_prev_y[0])
        except Exception: pass
    # ③ 级别过滤生效时, 增量隐藏新增的不匹配行
    if log_level_var.get() != "全部":
        _log_apply_filter(full=False)
    _pc = getattr(_periodic, "_cache", {})
    is_running = state.running
    is_paused = is_running and not state.pause_event.is_set()
    is_stopped = bool(state.quit2)
    is_recording = state.recording
    new_status = (1 if is_paused else 2 if is_running else 3 if is_stopped else 0)
    if _pc.get("status") != new_status:
        _pc["status"] = new_status
        # ① 四态按钮状态机 (就绪/运行中/已暂停/已停止)
        try: _update_exec_buttons(new_status)
        except Exception: pass
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
            # [AI] 徽标口径与守卫一致（凭据库优先）：延迟 import 避免潜在循环依赖
            try:
                from ai_client import has_ai_key as _ai_has_key
                _ai_ok = bool(_ai_has_key())
            except Exception:
                _ai_ok = bool(state.API_KEY)
            ai_status = "[AI]" if _ai_ok else ""
            status_text.config(text=" 就绪{}{}  |  {} 行  |  请选择脚本文件开始".format(
                mod_mark, " "+ai_status if ai_status else "", rows_count), fg=C["fgm"])
            status_dot.config(text="● 就绪",fg=C["fgm"])
            if _update_pending.get("ver"):
                _on_update_hint(_update_pending["ver"], "")
    if is_running and state.exec_state.get("total_rows",0)>0:
        lp=state.exec_state["loop"]; tl=state.exec_state["total_loops"]
        rw=state.exec_state["row"]; tr=state.exec_state["total_rows"]
        pct = max(0, min(100, int(rw / max(1, tr) * 100)))
        el = time.time() - state.exec_state["start_time"]
        # ETA: 依据已完成行数的平均速度估算剩余时间 (尚未执行任何行时显示 --:--)
        if rw > 0:
            eta_txt = _fmt_dur(el / float(rw) * max(0, tr - rw))
        else:
            eta_txt = "--:--"
        ok_n = state.exec_state.get("ok_cmds"); fail_n = state.exec_state.get("fail_cmds")
        dash_txt = "循环 {}/{}  ·  行 {}/{}  ·  已用 {}  ·  ETA {}".format(
            lp, "∞" if tl>99999 else tl, rw, tr, _fmt_dur(el), eta_txt)
        if ok_n is not None:
            dash_txt += "  ·  成功 {}  失败 {}".format(ok_n, fail_n or 0)
        if _pc.get("pct") != pct:
            _pc["pct"] = pct; progress_bar.config(value=pct)
        if _pc.get("dash") != dash_txt:
            _pc["dash"] = dash_txt; dash_info.config(text=dash_txt)
    elif not is_running and _pc.get("pct",0) != 0:
        _pc["pct"] = 0; progress_bar.config(value=0)
        if _pc.get("dash"):
            _pc["dash"] = ""; dash_info.config(text=_DASH_EMPTY)
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

# Mini Bar 上下文注入 (整个界面此时已构建完毕; _refresh_theme / _load_pil /
# ui_scale 钩子还会在运行期按需重新注入)
_bind_minibar()

# ══════════════════════════════════════════════════════════════════════
# 通用快捷键 (窗口级)
#   此前只有脚本表格绑了 Ctrl+C/V/Z/Y/D, 且必须表格获得焦点才有效; 保存 / 另存 /
#   新建 / 打开 完全没有快捷键。这里统一提到窗口级, 但只在主窗口拥有焦点时执行,
#   避免在设置、AI 生成等子窗口里误触发。
# ══════════════════════════════════════════════════════════════════════
def _main_window_focused():
    try:
        w = root.focus_get()
        return w is None or w.winfo_toplevel() is root
    except Exception:
        return True


def _hotkey(fn):
    """包装成窗口级快捷键: 主窗口聚焦时才执行, 并阻止默认行为。"""
    def _run(event=None):
        if not _main_window_focused():
            return None
        try:
            fn()
        except Exception as e:
            try:
                log1("快捷键执行失败: {}".format(e), "error")
            except Exception:
                pass
        return "break"
    return _run


for _seq, _fn in (
    ("<Control-s>", _cmd_save),      # 保存
    ("<Control-S>", _cmd_save_as),   # 另存为 (Tk: 大写 keysym 即 Shift 组合)
    ("<Control-n>", _cmd_new),       # 新建
    ("<Control-o>", _cmd_open),      # 打开
):
    try:
        root.bind_all(_seq, _hotkey(_fn))
    except Exception:
        pass

root.after(100,_periodic)

# === 更新检查 (启动 3s 后静默检查一次) ──────────────────────────────
# 旧实现: 发现新版只把 status_text 变成一个 os.startfile 链接 —— 用户点了跳到浏览器,
# 既看不到下载进度, 也无法校验拿到的到底是不是目标版本。现改为点击进入更新窗口,
# 在应用内完成「下载(带进度) → 完整性校验 → 替换并重启」。
_update_done = False
# 更新提示缓存: {"ver": <版本号>}。v0.1.26 缺失该定义, 导致 _periodic 每 100ms
# 抛 NameError、状态栏/进度刷新链路中断, 且 _on_update_hint 置位失败 —— 本版修复。
_update_pending = {}


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

# === OCR 预热 (P0-7 接线: state.OCR_PRELOAD) ===
# 仅当用户开启「启动时后台预热 OCR 引擎」时, 用 daemon 线程预检测后端/建 PaddleOCR
# 单例, 让首次「识别文字」不再等 1~3 秒。绝不阻塞 Tk 主循环: 不在此处同步 import
# paddleocr, 重型初始化全部落在 ocr_backend.preload() 的后台线程里; 失败仅记日志。
try:
    if getattr(state, "OCR_PRELOAD", False):
        import ocr_backend as _ocr_backend_mod

        def _ocr_preload_work():
            try:
                _ocr_backend_mod.preload()
            except Exception as e:
                log1("OCR 预热异常 (已忽略): {}".format(e), "warning")

        threading.Thread(target=_ocr_preload_work, daemon=True).start()
except Exception as e:
    log1("OCR 预热启动失败 (不影响使用): {}".format(e), "warning")

# === Config-driven global hotkeys (run/pause/stop) ===
# 启用 state.on_config_change 机制: 配置保存后自动重建快捷键映射、同步 engine 参数、刷新主题
_HOTKEY_ACTIONS = {}   # {action: (mods_set, vk)} — 由 _rebuild_hotkey_specs() 重建
_hotkey_prev = {}      # 边沿检测: {action: bool}，避免按住时重复触发
# 「停止录制」热键 (P0-7 接线): 同款解析 + 独立边沿检测状态
_RECORD_STOP_SPEC = None   # (mods_set, vk) 或 None(未配置/解析失败)
_recstop_prev = False      # 边沿检测: 避免按住时每 200ms 重复触发


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
    """根据 state 中的 HOTKEY_* / RECORDING_STOP_HOTKEY 配置重建快捷键映射表。

    配置变更时调用 (见 _on_config_changed)。解析失败/为空一律得到 None，
    调用方据此退回「仅硬编码热键」，绝不抛异常。
    """
    global _HOTKEY_ACTIONS, _hotkey_prev, _RECORD_STOP_SPEC, _recstop_prev
    _HOTKEY_ACTIONS = {
        action: _parse_hotkey_spec(getattr(state, key, ""))
        for action, key in (("run", "HOTKEY_RUN"), ("pause", "HOTKEY_PAUSE"),
                            ("stop", "HOTKEY_STOP"))
    }
    _HOTKEY_ACTIONS = {k: v for k, v in _HOTKEY_ACTIONS.items() if v}
    _hotkey_prev = {k: False for k in _HOTKEY_ACTIONS}
    # 用户配置的「停止录制」热键 (默认 Ctrl+Alt+F12)；None = 不参与判定
    _RECORD_STOP_SPEC = _parse_hotkey_spec(getattr(state, "RECORDING_STOP_HOTKEY", ""))
    _recstop_prev = False


_hotkey_executors = {
    "run": main_run,
    "pause": toggle_pause,
    "stop": stop_execution,
}


# 主线程标识: 用于把工作线程触发的配置变更回调调度回 Tk 主线程
_main_thread = threading.current_thread()


# ── 配置变更监听器: 保存配置后自动同步运行时状态 ──
def _on_config_changed(data, changed):
    """配置保存后的统一处理入口 (通过 state.on_config_change 注册)。

    线程安全: save_config() 可能由 netlink/security/scheduler 等工作线程调用,
    而本回调会触碰 Tk (dark_btn / _refresh_theme) → 非主线程时统一调度回主线程。
    触发收窄: save_config() 的 changed 恒含 dark_mode, 旧实现导致每次保存都触发
    整应用重绘 → 现仅当 dark_mode 相对「已应用主题」_applied_dark_mode 真的变化
    时才调用 _refresh_theme(); toggle_dark() 已先行刷新, 此处仅补 text 不重复刷新。
    """
    if not changed:
        return
    if threading.current_thread() is not _main_thread:
        try:
            root.after(0, lambda d=data, c=changed: _on_config_changed(d, c))
        except Exception:
            pass
        return
    try:
        if "dark_mode" in changed:
            if bool(state.DARK_MODE) != _applied_dark_mode:
                _refresh_theme()
            dark_btn.config(text="◑" if state.DARK_MODE else "◐")
        if "retry_max" in changed:
            engine.retry = state.RETRY_MAX
        if "retry_interval" in changed:
            engine.retry_interval = state.RETRY_INTERVAL
        if any(k in changed for k in ("hotkey_run", "hotkey_pause", "hotkey_stop",
                                      "recording_stop_hotkey")):
            _rebuild_hotkey_specs()
            log1("全局快捷键配置已更新")
    except Exception as e:
        log1("配置监听器处理失败: {}".format(e), "warning")


state.on_config_change(_on_config_changed, keys=(
    "dark_mode", "retry_max", "retry_interval",
    "hotkey_run", "hotkey_pause", "hotkey_stop",
    "recording_stop_hotkey"))
_rebuild_hotkey_specs()


# === Global hotkey polling (lightweight: poll keyboard state via ctypes) ===
def _hotkey_poll():
    global _recstop_prev
    if state._closing: return
    try:
        # 固定录制停止热键: Ctrl+Shift+Q / Ctrl+Shift+S (行为保留，不做回归)
        ctrl = ctypes.windll.user32.GetAsyncKeyState(0x11) & 0x8000
        shift = ctypes.windll.user32.GetAsyncKeyState(0x10) & 0x8000
        q = ctypes.windll.user32.GetAsyncKeyState(0x51) & 0x8000
        s = ctypes.windll.user32.GetAsyncKeyState(0x53) & 0x8000
        if state.recording and ctrl and shift and (q or s):
            state.record_stop = True
            log1("热键: 停止录制")

        # 用户配置的停止录制热键 (state.RECORDING_STOP_HOTKEY, P0-7 接线)。
        # 复用 _parse_hotkey_spec 的解析结果与同款边沿检测, 按住不重复触发;
        # 解析失败/为空时 _RECORD_STOP_SPEC 为 None → 仅走上面的硬编码热键。
        rnow = False
        if _RECORD_STOP_SPEC:
            rmods, rvk = _RECORD_STOP_SPEC
            rbase = all(ctypes.windll.user32.GetAsyncKeyState(m) & 0x8000
                        for m in rmods)
            rnow = bool(rbase and
                        (ctypes.windll.user32.GetAsyncKeyState(rvk) & 0x8000))
            if rnow and not _recstop_prev and state.recording:
                state.record_stop = True
                log1("热键: 停止录制 (配置热键)")
        _recstop_prev = rnow

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
        _bind_scroll_recursive(run_bar, _run_on_wheel)
        run_canvas.bind("<MouseWheel>", _run_on_wheel)
    except Exception: pass
    try:
        _bind_scroll_recursive(wf_toolbar, _wf_on_wheel)
        wf_canvas.bind("<MouseWheel>", _wf_on_wheel)
    except Exception: pass

root.after(10, _setup_scroll_bindings)

# ── NetLink 多设备互联: 按配置自动启动 (失败不影响主程序) ──
try:
    if getattr(state, "NETLINK_ENABLED", False):
        import netlink
        netlink.start_netlink(root)
except Exception as _e:
    log1("NetLink 启动失败: {}".format(_e), "warning")

# ── NetLink 远程操控钩子 (Phase 2-2): 供被控端回主线程执行 运行/停止 ──
try:
    import netlink as _netlink_mod

    def _nl_hook_run(loops=None):
        try:
            if loops:
                loop_count_var.set(str(loops))
        except Exception:
            pass
        main_run()

    _netlink_mod.set_control_hooks(run=_nl_hook_run, stop=stop_execution)
except Exception as _e:
    log1("NetLink 控制钩子注册失败: {}".format(_e), "warning")

# 注意: mainloop() 调用已移至 run.py,避免重复调用
# root.mainloop()