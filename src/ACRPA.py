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
import commands
import script_io
import script_validate  # 路线图 阶段二第 7 项: 运行前静态校验 (纯函数, 无副作用)
import i18n  # 路线图 §5.6: UI 字符串目录 (默认 zh; 文本只作显示, 不作状态/主题判定)
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
# UI 基础层 (阶段二第 2 项): 主题色板 / 样式应用 / 换肤事件总线。
# 各独立窗口通过 ui_theme.subscribe(...) 自注册换肤回调 —— 新增窗口无需再改 _refresh_theme。
from ui import theme as ui_theme
# 日志面板 (阶段二第 3 项第 1 步): 底部常驻日志面板整体抽到 src/ui/log_dock.py。
# 本文件只保留「转发别名」(rz/_tlog/scroll/_log_*) 与构建期的 get_* 取回。
from ui import log_dock as ui_log_dock
# 执行控制栏 (阶段二第 3 项第 2 步): 运行控制条的构建 + 行为函数 + 换肤订阅整体抽到
# src/ui/exec_bar.py。本文件只保留「转发别名」(btn_* / script_name_var / _update_exec_buttons
# 等) 与构建期的 get_widgets() 取回; 执行核心 (main_run/stop_execution/toggle_pause/
# _step_once/_fmt_dur) 仍留在本文件并注入 exec_bar。
from ui import exec_bar as ui_exec_bar
# 工作流 Tab (阶段二第 3 项第 3 步): 构建 + 行为函数 + 换肤订阅整体抽到
# src/ui/workflow_view.py。本文件只保留「转发别名」(tab_workflow / wf_* / _wf_run /
# _wf_stop / _wf_refresh_tree 等) 与构建期的 get_widgets() 取回; 领域层 src/workflow.py 不迁、不改。
from ui import workflow_view as ui_workflow_view

# 托盘图标全局引用
_tray = None  # type: SystemTray | None


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


# ══════════════════════════════════════════════════════════════════════
# ThemeBus 订阅适配器 (阶段二第 2 项)
#
# 取代 `_refresh_theme` 里对下面这些模块的手工 C 注入扇出 (修法③)。
# 每个适配器只做「取到模块 → 调它既有的 refresh_theme/retheme/bind」,
# 因此各窗口模块自身的换肤实现 (netlink_window.refresh_theme /
# help_window.refresh_theme / market_window.retheme_marketplace /
# mini_bar.bind) 完全不变, 变化只在「谁调用它」。
# 新增窗口: 只需 ui_theme.subscribe(自己的刷新函数), 不必再改 _refresh_theme。
# ══════════════════════════════════════════════════════════════════════

def _theme_sync_minibar(dark=None, palette=None, prev=None):
    """订阅: 重新注入 mini_bar 色板/字体, 并按新色刷新其状态与语义按钮。"""
    _bind_minibar()
    try:
        _sync_mini_bar_status()
    except Exception:
        pass


def _theme_sync_netlink(dark=None, palette=None, prev=None):
    """订阅: 已打开的设备互联窗口主题重刷 (惰性 import; 未打开为安全 no-op)。"""
    try:
        import netlink_window
        netlink_window.refresh_theme()
    except Exception:
        pass


def _theme_sync_help(dark=None, palette=None, prev=None):
    """订阅: 已打开帮助窗口主题重刷 (仿 netlink; 不存在为空操作)。"""
    try:
        import help_window
        help_window.refresh_theme(root, palette or C)
    except Exception:
        pass


def _theme_sync_market(dark=None, palette=None, prev=None):
    """订阅: 脚本市场窗口主题重刷 (未打开为安全 no-op)。"""
    try:
        import market_window
        market_window.retheme_marketplace()
    except Exception:
        pass


def _subscribe_theme_bus():
    """把全部「独立窗口」换肤回调登记到 ThemeBus (幂等, 重复调用只登记一次)。"""
    for _fn in (_theme_sync_minibar, _theme_sync_netlink,
                _theme_sync_help, _theme_sync_market):
        ui_theme.subscribe(_fn)




















































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
    # 语义角色 "fgm": 旧实现按「含 ●」文案嗅探 → fg=fgm; 现创建时显式登记 (行为等价)
    rb_dot = ui_theme.roled(tkinter.Label(inner, text="●", font=FONT_ICON_LG,
        fg=C["dg"], bg=C["bgc"]), "fgm")
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

    # ── 运行前校验 (路线图 阶段二第 7 项: dry-run) ──
    # main_run 同时被 定时任务 / 托盘 / 热键 / Mini Bar 复用, 故默认「只记日志 + 非阻断
    # 提示」, 绝不弹模态框打断启动; 若校验本身异常也静默放行 (绝不影响原行为)。
    _pre_issues = []
    try:
        _pre_rows = script_io.load_script(state.filename)
        _pre_issues = script_validate.validate_script(_pre_rows, state.script_dir)
    except Exception:
        _pre_issues = []
    for _it in _pre_issues:
        log1("运行前校验 第{}行 [{}] {}".format(_it["row"], _it["level"], _it["message"]),
             "warning" if _it["level"] == "warning" else "error")
    _pre_errors = [x for x in _pre_issues if x["level"] == "error"]
    if _pre_errors:
        try:
            utils.show_toast(root, "运行前校验发现 {} 个问题(见日志)".format(len(_pre_errors)), "warning")
        except Exception:
            pass
        # 可选阻断: 仅当 state.VALIDATE_BEFORE_RUN=True (默认 False) 才弹一次确认框 → 零行为变化
        if getattr(state, "VALIDATE_BEFORE_RUN", False):
            try:
                _go = messagebox.askyesno(
                    "运行前校验",
                    "发现 {} 个问题(详见日志)。\n是否仍要运行？".format(len(_pre_errors)))
            except Exception:
                _go = True
            if not _go:
                log1("已取消运行 (运行前校验发现 {} 个问题)".format(len(_pre_errors)), "warning")
                return

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
    # 脚本读取统一委托 script_io（唯一实现）；.xls/.xlsx/.acrpas 均可
    
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
            # 统一 I/O: .acrpas → JSON; .xls/.xlsx → xlrd（唯一实现）
            rows_data = script_io.load_script(state.filename)

            state.exec_state["total_rows"] = len(rows_data)
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

# ══════════════════════════════════════════════════════════════════════
# 运行前校验 (路线图 阶段二第 7 项): 「✓ 校验」按钮 → 非模态问题列表窗口
# 复用 script_validate.validate_script (纯函数); 不改动编辑器任何既有行为。
# ══════════════════════════════════════════════════════════════════════
_validate_win = None          # 当前问题列表窗口 (单例: 再次点击先关旧窗)
_validate_widgets = []        # [(widget, bg_key, fg_key|None)] — 换肤刷新用


def _refresh_validate_window(dark=None, colors=None, prev=None):
    """问题列表窗口换肤回调 (ThemeBus 订阅; 单控件失败不影响其它)。"""
    C = colors if isinstance(colors, dict) else getattr(utils, "C", None)
    if not isinstance(C, dict):
        return
    for w, bg_key, fg_key in list(_validate_widgets):
        try:
            if not w.winfo_exists():
                continue
            cfg = {"bg": C.get(bg_key, C.get("bg"))}
            if fg_key:
                cfg["fg"] = C.get(fg_key, C.get("fgb"))
            w.configure(**cfg)
        except Exception:
            pass


def _show_validation_window(issues):
    """弹出非模态「运行前校验」问题列表窗口 (每行: 第 N 行 [级别] 消息)。"""
    global _validate_win, _validate_widgets
    try:
        if _validate_win is not None and _validate_win.winfo_exists():
            _validate_win.destroy()
    except Exception:
        pass
    C = getattr(utils, "C", {}) or {}
    try:
        win = tkinter.Toplevel(root)
    except Exception:
        return
    _validate_win = win
    win.title("运行前校验")
    try:
        _set_window_icon(win)
    except Exception:
        pass
    try:
        ui_theme.claim_window(win, "validate_script")
    except Exception:
        pass
    win.configure(bg=C.get("bg"))
    win.geometry("600x380")
    win.minsize(420, 240)

    errs = [x for x in issues if x["level"] == "error"]
    warns = [x for x in issues if x["level"] == "warning"]
    header = tkinter.Label(
        win,
        text="发现 {} 个问题 ({} 错误 / {} 警告)".format(len(issues), len(errs), len(warns)),
        font=utils.FONT_BODY, fg=C.get("fgb"), bg=C.get("bg"))
    header.pack(anchor="w", padx=12, pady=(12, 6))

    body = tkinter.Frame(win, bg=C.get("bg"))
    body.pack(fill="both", expand=True, padx=12, pady=(0, 8))
    sb = tkinter.Scrollbar(body, orient="vertical")
    sb.pack(side="right", fill="y")
    lb = tkinter.Listbox(body, yscrollcommand=sb.set, font=utils.FONT_SMALL,
                         bg=C.get("ebg"), fg=C.get("fgb"),
                         selectbackground=C.get("ac"), highlightthickness=0,
                         bd=1, relief="solid")
    lb.pack(side="left", fill="both", expand=True)
    sb.config(command=lb.yview)
    if issues:
        for it in issues:
            lvl = "错误" if it["level"] == "error" else "警告"
            lb.insert("end", "第 {} 行  [{}]  {}".format(it["row"], lvl, it["message"]))
    else:
        lb.insert("end", "  ✓ 未发现问题")

    bar = tkinter.Frame(win, bg=C.get("bg"))
    bar.pack(fill="x", padx=12, pady=(0, 12))
    ui_theme.roled(tkinter.Button(
        bar, text="关闭", font=utils.FONT_BUTTON, bg=C.get("ac"), fg="white",
        activebackground=C.get("ach"), activeforeground="white",
        relief="flat", bd=1, cursor="hand2", padx=10, pady=2,
        command=win.destroy), "ac").pack(side="right")
    hint = tkinter.Label(bar, text="仅只读展示行号 (不做跳转)",
                         font=utils.FONT_SMALL, fg=C.get("fgm"), bg=C.get("bg"))
    hint.pack(side="left")

    win.bind("<Escape>", lambda e: win.destroy())
    _validate_widgets = [
        (win, "bg", None),
        (header, "bg", "fgb"),
        (body, "bg", None),
        (lb, "ebg", "fgb"),
        (bar, "bg", None),
        (hint, "bg", "fgm"),
    ]
    try:
        ui_theme.subscribe(_refresh_validate_window)
    except Exception:
        pass
    try:
        win.transient(root)
        win.lift()
        win.focus_force()
    except Exception:
        pass


def validate_script_cb():
    """「✓ 校验」按钮回调: load_script + validate_script → 问题列表窗口。"""
    if not state.has_script:
        messagebox.showwarning("运行前校验", "没有选择要校验的脚本文件")
        return
    try:
        rows = script_io.load_script(state.filename)
        issues = script_validate.validate_script(rows, state.script_dir)
    except Exception as e:
        log1("校验失败: {}".format(e), "error")
        try:
            messagebox.showerror("运行前校验", "无法读取脚本:\n{}".format(e))
        except Exception:
            pass
        return
    for it in issues:
        log1("校验 第{}行 [{}] {}".format(it["row"], it["level"], it["message"]),
             "warning" if it["level"] == "warning" else "error")
    if not issues:
        log1("校验通过: 未发现问题")
    _show_validation_window(issues)

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
# 主窗口几何随「界面缩放」档位 + DPI 缩放: 默认几何由 utils.scaled() 从设计值
# 1000x680 派生 (含 dpi_factor 与 ui_scale), 避免 125% DPI 下内容 (req 高 703)
# 超出硬编码 680 而被纵向裁剪。
# ── 主窗口几何: 记忆 + 最大化恢复 + 虚拟屏越界回退居中; 紧凑模式=500x625 旧布局 ──
# 越界判断 / 居中派生 / 虚拟屏取值已抽为 utils 公共函数 (行为等价):
#   utils.win_virtual_bounds(root) / utils.geometry_in_screen(root, geo) /
#   utils.center_geometry(root, w, h) —— 主窗口与设置窗口共用同一套判定。

def _apply_main_geometry():
    """启动时应用主窗口几何 (尺寸全部来自 utils 尺寸 token, 路线图 §5.5):
       紧凑模式 → win_compact_* token 派生尺寸 (旧布局 500x625), 居中落位;
       否则 → 优先记忆几何(校验越界), 越界/缺省则按「设计 win_w×win_h × dpi_factor
              × ui_scale」派生默认几何并居中; 再恢复最大化。
       最小尺寸走 win_min_* token —— 使 set_ui_scale() 真正影响窗口尺寸 (而非只缩字体)。
    """
    try:
        if getattr(state, "COMPACT_MODE", False):
            # 尺寸按 token 派生 (ui_scale 档位走 sp(), 基准档取 token 原值以保持旧行为),
            # 原先写死的屏幕偏移改为 center_geometry 居中派生 (小屏/多显示器不溢出)。
            if abs(utils.current_ui_scale() - 1.0) > 1e-6:
                cw, ch = utils.sp("win_compact_w"), utils.sp("win_compact_h")
                mw, mh = utils.sp("win_compact_min_w"), utils.sp("win_compact_min_h")
            else:
                cw, ch = utils.TOKENS["win_compact_w"], utils.TOKENS["win_compact_h"]
                mw, mh = utils.TOKENS["win_compact_min_w"], utils.TOKENS["win_compact_min_h"]
            root.geometry(utils.center_geometry(root, cw, ch))
            root.minsize(mw, mh)
            return
        geo = getattr(state, "MAIN_GEOMETRY", "") or ""
        if geo and utils.geometry_in_screen(root, geo):
            root.geometry(geo)
        else:
            # 默认几何须纳入 dpi_factor: 125% DPI 下内容 req 高 703 > 硬编码 680,
            # 会纵向裁剪底部; 经 utils.sp() 从 token (win_w/win_h) 派生保证
            # reqheight <= winfo_height。
            root.geometry(utils.center_geometry(root, utils.sp("win_w"),
                                                utils.sp("win_h")))
        root.minsize(utils.sp("win_min_w"), utils.sp("win_min_h"))
        if getattr(state, "MAIN_MAXIMIZED", False):
            try: root.state("zoomed")
            except Exception: pass
    except Exception:
        root.geometry("960x680")

# ── 全局禁止 Combobox / Spinbox 滚轮修改数值 ──
def _block_input_wheel(event):
    """拦截滚轮事件，防止误触修改 Combobox/Spinbox 数值"""
    return "break"

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
PAD = {"padx":6,"pady":3}; PI = {"padx":6,"pady":2}

# ── 顶部标题双击内联编辑 (仅会话内存, 绝不写入配置文件) ──────────────
# 需求: 界面顶部标题可双击自定义; 默认文案与现状一致, 重启回默认。
TITLE_DEFAULT_TEXT = "Adaptive Control Automation Workflow"  # 空/纯空白时回退的默认文案
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
pin_pinned = False
def toggle_pin():
    global pin_pinned
    pin_pinned = not pin_pinned
    root.attributes("-topmost", pin_pinned)
    pin_btn.config(text="▲" if pin_pinned else "△",
        fg=C["ac"] if pin_pinned else C["fgm"])

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
def _show_settings_tip(event):
    global _tip_win
    if _tip_win: _tip_win.destroy()
    _tip_win = tkinter.Toplevel(root)
    _tip_win.wm_overrideredirect(True)
    _tip_win.wm_geometry("+{}+{}".format(event.x_root + 12, event.y_root - 8))
    tkinter.Label(_tip_win, text="打开设置窗口",
        font=FONT_SMALL, bg=C["tooltip_bg"], fg=C["fgb"], relief="flat", bd=0,
        highlightthickness=1, highlightbackground=C["tooltip_border"], padx=6, pady=2).pack()
def _show_help_tip(event):
    global _tip_win
    if _tip_win: _tip_win.destroy()
    _tip_win = tkinter.Toplevel(root)
    _tip_win.wm_overrideredirect(True)
    _tip_win.wm_geometry("+{}+{}".format(event.x_root + 12, event.y_root - 8))
    tkinter.Label(_tip_win, text="帮助 / 使用说明",
        font=FONT_SMALL, bg=C["tooltip_bg"], fg=C["fgb"], relief="flat", bd=0,
        highlightthickness=1, highlightbackground=C["tooltip_border"], padx=6, pady=2).pack()
def _show_devlink_tip(event):
    global _tip_win
    if _tip_win: _tip_win.destroy()
    _tip_win = tkinter.Toplevel(root)
    _tip_win.wm_overrideredirect(True)
    _tip_win.wm_geometry("+{}+{}".format(event.x_root + 12, event.y_root - 8))
    tkinter.Label(_tip_win, text="设备互联",
        font=FONT_SMALL, bg=C["tooltip_bg"], fg=C["fgb"], relief="flat", bd=0,
        highlightthickness=1, highlightbackground=C["tooltip_border"], padx=6, pady=2).pack()
# 阶段二第 2 项: 语义角色不再从「旧主题颜色值」反推 (_semantic_bg_for / _SEMANTIC_GROUPS
# 两份拷贝已删除), 改为控件创建处显式登记 (ui_theme.set_role), 换肤时按角色取色。


def _refresh_theme():
    global C, _applied_dark_mode
    # 重绑 C 之前先抓「旧主题色快照」, 作为本次广播的 prev 参数
    # (订阅方可据此做「旧色→旧键→新色」映射; 旧实现读 C["old_*"], 恒为 None)。
    _prev = dict(C) if isinstance(C, dict) else {}
    C = _colors()
    # 记录本次已应用的主题值 (供 _on_config_changed 收窄触发, 见该函数注释)
    _applied_dark_mode = bool(getattr(state, "DARK_MODE", False))
    # apply_theme 同时重绑 utils.C (themed() 等既有引用依赖它)
    apply_theme(root, style)

    def _walk(p, prev):
        for w in p.winfo_children():
            try:
                cls = w.winfo_class()
                # 独立窗口若已声明「自管换肤」(ui_theme.claim_window), 通用 walk 不再跨入 ——
                # 由该窗口所属模块的 ThemeBus 订阅回调负责, 避免跨窗口串扰/覆盖特化配色。
                if cls == "Toplevel" and ui_theme.is_claimed(w):
                    continue
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
                    # 语义角色由控件创建时显式登记 (ui_theme.set_role), 未登记 → 中性灰 fgm。
                    # **不再嗅探中文文案** (旧实现按 ●/运行/就绪 猜颜色) —— 路线图 §5.2 修法②。
                    fg_color = C.get(ui_theme.get_role(w) or "", C["fgm"])
                    w.configure(bg=parent_bg, fg=fg_color)
                elif cls == "Text":
                    w.configure(bg=C["logbg"], fg=C["logfg"],
                        insertbackground=C["fgt"],
                        selectbackground=C["acl"], selectforeground=C["fgt"])
                elif cls == "Scrollbar":
                    w.configure(bg=C["bd"], activebackground=C["fgm"], troughcolor=C["logbg"])
                elif cls == "Button":
                    # 语义按钮: 按控件登记的语义角色回填当前主题色;
                    # 未登记角色 → 中性按钮, 统一刷为卡片色。
                    # (**不再**按旧颜色值反推语义组) —— 路线图 §5.2 修法②。
                    _role = ui_theme.get_role(w)
                    if _role:
                        w.configure(bg=C.get(_role, C["bgc"]))
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
                    # 工作流流程图画布用 flowbg: 其重着色已移交 src/ui/workflow_view.py
                    # 的 ThemeBus 订阅回调 (本函数末尾 ui_theme.publish 驱动), 此处通用处理仅刷主背景。
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

    # 日志区 rz/scroll 重着色 + 日志 tag 随主题重配 (D3) 已迁至 src/ui/log_dock.py 的
    # ThemeBus 订阅回调 refresh_theme —— 本函数末尾的一次 ui_theme.publish(...) 会驱动
    # 它; 此处不再保留任何具名回填 (阶段二验收: 新增模块自注册即自动换肤)。
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

    # ── 一次广播取代原先 6 处手工扇出 (路线图 §5.2 修法③) ──────────────────
    # 订阅方 = 各独立窗口/模块在各自 init_ctx 或 _subscribe_theme_bus() 中注册的回调:
    #   settings_window.refresh_theme / dialogs.refresh_theme (弹窗) /
    #   mini_bar(重新注入) / netlink_window.refresh_theme / help_window.refresh_theme /
    #   market_window.retheme_marketplace
    # 本函数不再出现任何模块具名调用 —— 新增窗口只需 ui_theme.subscribe(...) 即自动换肤
    # (§9:480 阶段二验收: 新增窗口自动换肤无需改 _refresh_theme)。
    # 放在本窗口自身回填之后: 订阅方各自的配色结果不被本函数覆盖。
    ui_theme.publish(bool(getattr(state, "DARK_MODE", False)), C, _prev)
    # 定点回填语义按钮 (D2): 按控件登记的语义角色取色 (不再按旧值/旧快照反推语义组)。
    # 执行控制四键 (btn_run/btn_pause/btn_step/btn_stop) 的角色回填已迁至
    # src/ui/exec_bar.py 的 ThemeBus 订阅回调 refresh_theme (阶段二第 3 项第 2 步);
    # 此处仅保留脚本编辑工具栏的 btn_record —— 角色登记与回填语义不变。
    try:
        _role = ui_theme.get_role(btn_record)
        if _role:
            btn_record.configure(bg=C.get(_role, C["bgc"]))
    except Exception:
        pass

    # 工作流 Tab 主题同步 (步骤树标签颜色 + 流程图重绘) 已移交 src/ui/workflow_view.py
    # 的 ThemeBus 订阅回调 refresh_theme; 本函数末尾的 ui_theme.publish(...) 会驱动它。

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

# 鼠标滚轮横向滚动 - handler 定义（递归绑定在 _init_toolbar_buttons() 之后）
def _tb1_on_wheel(event):
    if event.delta > 0:
        toolbar_canvas.xview_scroll(-1, "units")
    else:
        toolbar_canvas.xview_scroll(1, "units")

# ══════════════════════════════════════════════════════════════════════
# 脚本编辑区缩放 (Ctrl+滚轮 / Ctrl+加号 / Ctrl+减号 / Ctrl+0 复位)
#   为什么不直接改 "Treeview" 样式: 变量监视、时序、工作流列表共用它, 一起放大
#   不是用户想要的。这里派生一个只给脚本表格用的样式, 并把倍率持久化到
#   state.EDITOR_ZOOM (0 = 跟随全局 ui_scale)。
# ══════════════════════════════════════════════════════════════════════
_EDITOR_STYLE = "ScriptEditor.Treeview"


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
_LEGEND_ITEMS = [
    ("找图/区域找图", "#2563EB"), ("点图/区域点图", "#10B981"),
    ("按键/按下/释放", "#8B5CF6"), ("热键", "#EF4444"),
    ("输入/等待", "#F59E0B"), ("坐标", "#6B7280"),
    ("悬停/拖拽", "#F97316"), ("滚轮", "#EC4899"),
    ("截屏", "#0EA5E9"), ("代码", "#DC2626"),
]

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
# Breakpoint toggle on #0 column click
def _toggle_breakpoint(event):
    region = tree.identify_region(event.x, event.y)
    if region != "tree": return
    item = tree.identify_row(event.y)
    if not item: return
    row_idx = tree.index(item) + 1
    if row_idx in state.breakpoints:
        state.breakpoints.discard(row_idx)
    else:
        state.breakpoints.add(row_idx)
    # 单行变更(断点 toggle): 就地刷新该行 #0 文本, 不再全量重建
    _update_row_inplace(row_idx - 1)

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
    else:
        state.breakpoints.add(row_idx)
    # 单行变更(断点 toggle): 就地刷新该行 #0 文本, 不再全量重建
    _update_row_inplace(row_idx - 1)
    return "break"


def _kb_run_script(event=None):
    """F5 — 运行当前脚本。"""
    main_run()
    return "break"
def _kb_move_up(event=None):
    _cmd_move_up()
    return "break"

def _kb_move_down(event=None):
    _cmd_move_down()
    return "break"

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

def _cmd_edit_row_dialog():
    """「编辑行」对话框: 命令下拉 + ui.param_form 参数表单 (针对当前选中行)。

    与内联编辑 (_edit_cell, 双击单元格) 并存 —— 本入口提供「整行结构化编辑」,
    **不改动** _edit_cell 的 <Double-1> 绑定语义, 也不改 _cmd_add_row (仍插空行)。
    确定后写回 sd.cmd_type / sd.args (恒 9 位), 并 _push_undo() + _update_row_inplace(idx)。
    触发: 工具栏「✎ 编辑」按钮 或 树内 F2。
    """
    sel = tree.selection()
    if not sel:
        log1("请先选择要编辑的行", "warning")
        return
    idx = tree.index(sel[0])
    if idx < 0 or idx >= len(state._editor_rows):
        return
    sd = state._editor_rows[idx]

    from ui import param_form

    win = tkinter.Toplevel(root)
    win.title("编辑行 {}".format(idx + 1))
    win.geometry("460x380")
    win.transient(root)
    win.configure(bg=C["bgc"])
    try:
        _set_window_icon(win)
    except Exception:
        pass
    # 独立窗口: 声明「自管换肤」(ACRPA 通用 walk 跳过) + 订阅 ThemeBus 自动换肤
    try:
        ui_theme.claim_window(win, "edit_row")
    except Exception:
        pass

    tkinter.Label(win, text="命令:", bg=C["bgc"], fg=C["fgb"]).pack(pady=(10, 2))
    cmd_var = tkinter.StringVar(value=sd.cmd_type)
    # 阶段二新增项①: 命令下拉对「缺少外部能力」的命令加 ⚠ 角标 (读取时经 strip 清洗)。
    cmd_combo = ttk.Combobox(win, textvariable=cmd_var, values=commands.display_names(),
        state="readonly", width=26)
    cmd_combo.pack(pady=2)

    tkinter.Label(win, text="参数:", bg=C["bgc"], fg=C["fgb"]).pack(pady=(8, 2))
    holder = tkinter.Frame(win, bg=C["bgc"])
    holder.pack(pady=2, fill="x")

    _fonts = {"body": FONT_BODY, "small": FONT_SMALL,
              "button": FONT_BUTTON, "log": FONT_LOG}
    _pstate = {"get": None}

    def _rebuild(seed):
        for w in holder.winfo_children():
            w.destroy()
        fr, get = param_form.build_arg_form(
            holder, commands.strip_display_badge(cmd_var.get()), seed,
            colors=C, fonts=_fonts, theme=ui_theme)
        fr.pack(fill="x")
        _pstate["get"] = get

    def _on_change(event=None):
        # 切换命令: 用当前参数值作为初值重建表单
        seed = _pstate["get"]() if _pstate["get"] else list(sd.args)
        _rebuild(seed)

    cmd_combo.bind("<<ComboboxSelected>>", _on_change)
    _rebuild(list(sd.args))

    def _refresh(dark=None, colors=None, prev=None):
        """ThemeBus 订阅回调: 按新色板重着色并按当前值重建参数表单。"""
        pal = colors if isinstance(colors, dict) and colors else C
        try:
            win.configure(bg=pal.get("bgc", C["bgc"]))
        except Exception:
            pass
        _rebuild(_pstate["get"]() if _pstate["get"] else list(sd.args))

    try:
        ui_theme.subscribe(_refresh)
    except Exception:
        pass

    def _close():
        try:
            ui_theme.unsubscribe(_refresh)   # 关闭时退订, 避免订阅者泄漏
        except Exception:
            pass
        win.destroy()

    def _ok():
        _push_undo()
        sd.cmd_type = commands.strip_display_badge(cmd_var.get())
        sd.args = _pstate["get"]() if _pstate["get"] else list(sd.args)
        _update_row_inplace(idx)
        _close()

    bar = tkinter.Frame(win, bg=C["bgc"])
    bar.pack(pady=10)
    tkinter.Button(bar, text="确定", command=_ok, font=FONT_BUTTON,
        bg=C["ac"], fg="white", relief="flat", bd=1, cursor="hand2",
        padx=10, pady=2).pack(side="left", padx=4)
    tkinter.Button(bar, text="取消", command=_close, font=FONT_BUTTON,
        bg=C["bgc"], fg=C["fgb"], relief="flat", bd=1, cursor="hand2",
        padx=10, pady=2).pack(side="left", padx=4)
    win.protocol("WM_DELETE_WINDOW", _close)
    win.bind("<Escape>", lambda e: _close())

def _kb_command_palette(event=None):
    """Ctrl+K — 命令库 (路线图 §5.3): 分组 + 模糊搜索命令, Enter 写入当前行。

    有选中行 → 覆盖该行命令类型并清空参数; 无选中 → 追加一行。
    命令分组/条目数据源见 ui.command_palette (与帮助窗口命令速查同源)。
    """
    from ui import command_palette

    def _apply(name):
        _push_undo()
        sel = tree.selection()
        if sel:
            idx = tree.index(sel[0])
            if 0 <= idx < len(state._editor_rows):
                sd = state._editor_rows[idx]
                sd.cmd_type = name
                sd.args = [""] * 9
                _update_row_inplace(idx, sd)
                log1("已将命令写入第 {} 行: {}".format(idx + 1, name))
                return
        sd = ScriptData(name, [""] * 9)
        state._editor_rows.append(sd)
        _editor_sync_to_tree()
        try:
            tree.see(tree.get_children()[-1])
        except Exception:
            pass
        log1("已新增命令行: {}".format(name))

    try:
        command_palette.open_palette(
            root, C,
            {"body": FONT_BODY, "small": FONT_SMALL, "button": FONT_BUTTON},
            _apply, set_icon=_set_window_icon)
    except Exception as e:
        log1("命令库打开失败: {}".format(e), "error")
    return "break"


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
    """加载脚本到编辑器（.xls/.xlsx/.acrpas；自动清理旧状态）。

    读取统一委托 script_io.load_script（唯一实现）；本函数只保留编辑器状态
    清理（撤销/重做栈、断点、modified）与树重建逻辑。函数名/签名保持不变，
    供 _market_on_install / exec_bar / 市场冒烟测试继续调用。
    """
    _push_undo()
    state._editor_rows = []
    state.breakpoints.clear()
    state._editor_modified = False
    state._undo_stack.clear()
    state._redo_stack.clear()
    tree.delete(*tree.get_children())
    try:
        rows = script_io.load_script(fp)
        state._editor_rows = rows
        for sd in rows:
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


def _row_text_for(row_num, sd=None):
    """生成 #0 列文本: 两位行号 + 断点 ● + 条件 [C:...] (row_num 为 1-based)。

    就地刷新与全量重建共用此逻辑, 保证两条路径 #0 文本逐字一致。
    """
    num_text = "{:02d}".format(row_num)
    bp_mark = "●" if row_num in state.breakpoints else ""
    cond = engine.conditional_breakpoints.get(row_num, "") if hasattr(engine, 'conditional_breakpoints') else ""
    cond_mark = " [C:{}]".format(cond[:12]) if cond else ""
    return num_text + bp_mark + cond_mark


def _update_row_inplace(index, sd=None):
    """就地刷新单行 (index 为 0-based): 仅改该 iid 的 text/values/tags, 不 delete。

    用于「单行数据变更 (内联编辑)」与「断点 toggle」; 结果与全量重建后该行等价。
    失败时回退 :func:`_editor_sync_to_tree` (全量重建), 保证 UI 与真源一致。
    """
    if index is None or index < 0 or index >= len(state._editor_rows):
        return
    if sd is None:
        sd = state._editor_rows[index]
    try:
        children = tree.get_children()
        if index >= len(children):
            return
        item = children[index]
        tag = sd.cmd_type if sd.cmd_type in _CMD_COLORS else ""
        if tag and not tree.tag_has(tag):
            tree.tag_configure(tag, foreground=_CMD_COLORS.get(tag, C["fgb"]))
        tags = (tag,)
        if index % 2 == 0:
            tags = tags + ("even",)
        tree.item(item, text=_row_text_for(index + 1, sd),
                  values=sd.to_tuple(), tags=tags)
    except Exception:
        # 就地刷新异常 → 回退全量重建 (行为安全网)
        _editor_sync_to_tree()


def _editor_sync_to_tree():
    """全量重建编辑器树 (结构性操作: 增/删/移动/载入/undo/redo/clear)。

    重建前记录选中行的稳定 id, 重建后按 id 恢复选中 —— 使重排后仍保持原选中行。
    """
    prev_sel_ids = []
    try:
        for it in tree.selection():
            k = tree.index(it)
            if 0 <= k < len(state._editor_rows):
                prev_sel_ids.append(getattr(state._editor_rows[k], "id", None))
    except Exception:
        prev_sel_ids = []
    tree.delete(*tree.get_children())
    for i, sd in enumerate(state._editor_rows):
        tag = sd.cmd_type if sd.cmd_type in _CMD_COLORS else ""
        vals = sd.to_tuple()
        item = tree.insert("", "end", text=_row_text_for(i + 1, sd),
                           values=vals, tags=(tag,))
        if tag and not tree.tag_has(tag):
            tree.tag_configure(tag, foreground=_CMD_COLORS.get(tag, C["fgb"]))
        tags = tree.item(item, "tags")
        if i % 2 == 0:
            tags = tags + ("even",)
        tree.item(item, tags=tags)
    # 按稳定 id 恢复选中 (重建后保持原选中行; 结构性操作不再丢失选中)
    if prev_sel_ids:
        try:
            children = tree.get_children()
            id_to_iid = {}
            for i, sd in enumerate(state._editor_rows):
                rid = getattr(sd, "id", None)
                if rid is not None and i < len(children):
                    id_to_iid.setdefault(rid, children[i])
            for rid in prev_sel_ids:
                iid = id_to_iid.get(rid)
                if iid:
                    tree.selection_add(iid)
        except Exception:
            pass


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
    fp=filedialog.askopenfilename(title="打开脚本",filetypes=[('Excel 文件','*.xlsx *.xls'), ('ACRPA 脚本','*.acrpas'), ('xlsx','*.xlsx'), ('xls','*.xls')],initialdir=APP_ROOT)
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
    """Save current script to disk (.acrpas → JSON, 其它 → .xls)."""
    if not state._editor_rows:
        messagebox.showwarning("提示", "脚本为空，无法保存")
        return

    fp=filedialog.asksaveasfilename(title="保存脚本",defaultextension=".xls",
        filetypes=[('Excel 文件','*.xls'), ('ACRPA 脚本','*.acrpas')],initialdir=APP_ROOT)
    if fp:
        try:
            # 统一 I/O: .acrpas → JSON; 其它 → xls(xlwt); 均经 utils.atomic_save 原子写
            script_io.save_script(
                fp, state._editor_rows,
                meta={"name": os.path.splitext(os.path.basename(fp))[0]})
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
    """另存为 — 保存脚本副本到新文件（.acrpas → JSON, 其它 → .xls）"""
    if not state._editor_rows:
        messagebox.showwarning("提示", "脚本为空，无法保存")
        return
    fp=filedialog.asksaveasfilename(title="另存为",defaultextension=".xls",
        filetypes=[('Excel 文件','*.xls'), ('ACRPA 脚本','*.acrpas')],initialdir=APP_ROOT)
    if fp:
        try:
            # 统一 I/O: .acrpas → JSON; 其它 → xls(xlwt); 均经 utils.atomic_save 原子写
            script_io.save_script(
                fp, state._editor_rows,
                meta={"name": os.path.splitext(os.path.basename(fp))[0]})
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
        except ImportError:
            messagebox.showerror("错误","需要安装 xlwt: pip install xlwt")
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
    utils.place_dialog(win, 640, 360); win.transient(root); win.grab_set()
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
    utils.place_dialog(dlg, 840, 560); dlg.transient(root); dlg.configure(bg=C["bgc"])
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
    dlg=tkinter.Toplevel(root); dlg.title("选择模板"); utils.place_dialog(dlg, 540, 520)
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
        _inline_edit_widget = ttk.Combobox(tree_frame, values=commands.display_names(),
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
            sd.cmd_type = commands.strip_display_badge(new_val)
        else:
            sd.args[col - 1] = new_val
        # 单行数据变更: 就地刷新该行, 不再全量重建
        _update_row_inplace(idx)
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
        # 显式登记语义角色 (换肤时按角色回填; 关闭时清除回中性)
        ui_theme.set_role(btn_debug_mode, "ac")
        log1("调试模式已开启", "info")
    else:
        btn_debug_mode.config(bg=C["bgc"], fg=C["fgb"])
        ui_theme.clear_role(btn_debug_mode)
        state.step_mode = False
        btn_step_mode.config(bg=C["bgc"], fg=C["fgb"])
        ui_theme.clear_role(btn_step_mode)
        log1("调试模式已关闭", "info")

def _toggle_step_mode():
    """Toggle step-by-step execution mode"""
    if not state.debug_mode:
        log1("请先开启调试模式", "warning")
        return
    
    state.step_mode = not state.step_mode
    if state.step_mode:
        btn_step_mode.config(bg=C["ac"], fg="white")
        ui_theme.set_role(btn_step_mode, "ac")
        log1("单步执行模式已开启 - 每执行一行将暂停", "info")
        if state.running:
            state.pause_event.clear()
    else:
        btn_step_mode.config(bg=C["bgc"], fg=C["fgb"])
        ui_theme.clear_role(btn_step_mode)
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

    bg_c 允许传 **色板键名** ("ac"/"sc"/"dg"): 解析为当前主题色并登记语义角色
    (ui_theme.set_role), 与 utils._btn 同一约定 —— 换肤按角色回填, 不做旧值反推。
    """
    if bg_c is None:
        bg_c, _role = C["bgc"], None
    else:
        bg_c, _role = ui_theme.resolve_bg(bg_c, C)
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
    if _role:
        ui_theme.set_role(b, _role)
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
    _tbtn(toolbar_inner,"+ 添加",_cmd_add_row,"ac","white",
          tip="新增一行命令").pack(side="left",padx=_P)
    _tbtn(toolbar_inner,"- 删除",_cmd_del_row,"dg","white",
          tip="删除选中行").pack(side="left",padx=_P)
    _tbtn(toolbar_inner,"✎ 编辑",_cmd_edit_row_dialog,
          tip="编辑选中行 (结构化参数表单, F2)").pack(side="left",padx=_P)
    _sep(toolbar_inner)
    _tbtn(toolbar_inner,"↑",_cmd_move_up,tip="上移选中行").pack(side="left",padx=_P)
    _tbtn(toolbar_inner,"↓",_cmd_move_down,tip="下移选中行").pack(side="left",padx=_P)
    # Group 2: 文件操作
    _sep(toolbar_inner)
    _tbtn(toolbar_inner,"片段",_cmd_snippet,tip="插入/管理片段库 (离线可用)").pack(side="left",padx=_P)
    _tbtn(toolbar_inner,"新建",_cmd_new,"ac","white",
          tip="新建空白脚本 (Ctrl+N)").pack(side="left",padx=_P)
    _tbtn(toolbar_inner,"打开",_cmd_open,"ac","white",
          tip="打开脚本文件 (.xls/.acrpas) (Ctrl+O)").pack(side="left",padx=_P)
    _tbtn(toolbar_inner,"保存",_cmd_save,"sc","white",
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
    btn_record = _tbtn(toolbar_inner,"● 录制",lambda: _start_recording(),"dg","white",
                       tip="录制鼠标键盘操作 (Ctrl+Shift+Q 停止)")
    btn_record.pack(side="left",padx=_P)

    # 「调试 / 单步 / 变量」已移至「执行控制」Tab 运行控制条, 避免两处状态不同步
    _sep(toolbar_inner)
    _tbtn(toolbar_inner,"清空",_cmd_clear,"dg","white",
          tip="清空所有命令行").pack(side="left",padx=_P)

# === Shared helpers defined before TAB 2 ──
# 「执行控制栏」行为函数 (select_script 等) 已迁至 src/ui/exec_bar.py (阶段二第 3 项第 2 步);
# 此处保留转发别名, 既有直读 ACRPA.<name> 的引用契约不变。
select_script = ui_exec_bar.select_script

def toggle_pause():
    if not state.running:
        log1("脚本未运行，暂停/继续不可用", "warning")
        return
    if state.pause_event.is_set():
        state.pause_event.clear(); btn_pause.config(text=i18n.t("exec.btn_resume"),bg=C["ac"])
        status_text.config(text=i18n.t("status.paused_resume"),fg=C["wn"])
        status_dot.config(text=i18n.t("status.dot_paused"),fg=C["wn"])
    else:
        state.pause_event.set(); btn_pause.config(text="⏸ 暂停",bg=C["wn"])
        status_text.config(text=i18n.t("status.running"),fg=C["sc"])
        status_dot.config(text=i18n.t("status.dot_running"),fg=C["sc"])


# 区域截图工具 (曾由「执行控制」迁入脚本编辑工具栏) 随执行栏一并抽到 exec_bar。
_screenshot_tool = ui_exec_bar.screenshot_tool

# 运行控制条溢出/滚动行为已迁至 exec_bar; 保留转发别名 (_setup_scroll_bindings 直读)。
_run_update_overflow = ui_exec_bar.update_overflow
_run_on_bar_configure = ui_exec_bar.on_bar_configure
_run_on_wheel = ui_exec_bar.on_wheel

# ── ▶/■ 上下文分派 (按当前激活 Tab: 0=脚本编辑 / 1=工作流) 已迁至 exec_bar ──
_shared_run = ui_exec_bar.shared_run
_shared_stop = ui_exec_bar.shared_stop

# 最近脚本 / 次数 / 执行参数 / 快捷键提示等行为函数已迁至 exec_bar; 保留转发别名。
_tip_with_hotkey = ui_exec_bar.tip_with_hotkey
_update_recent_scripts = ui_exec_bar.update_recent_scripts
_on_script_switched = ui_exec_bar.on_script_switched
_add_recent_script = ui_exec_bar.add_recent_script
_on_loop_sel = ui_exec_bar.on_loop_sel
_exec_params_commit = ui_exec_bar.exec_params_commit


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

# ── 日志面板 (阶段二第 3 项第 1 步): 构建 + 行为 + 换肤已整体迁至 src/ui/log_dock.py ──
# 本文件仅保留「转发别名」: _periodic / 状态栏 / tools 直读 ACRPA.<name> 的既有引用契约
# 不变, 真实实现见 ui_log_dock (控件句柄别名在 build_app() 内构建后经 get_* 取回)。
_toggle_log_dock = ui_log_dock.toggle_dock
_on_log_sash_release = ui_log_dock.on_sash_release
_log_autoscroll_changed = ui_log_dock.autoscroll_changed
_log_apply_cbar = ui_log_dock.apply_cbar
_log_apply_filter = ui_log_dock.apply_filter
_log_search_next = ui_log_dock.search_next
_log_clear_view = ui_log_dock.clear_view
_log_export_view = ui_log_dock.export_view

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


# 执行控制四键状态机 (文案/配色切换) 已迁至 src/ui/exec_bar.py::set_state;
# ACRPA 保留转发别名 (供 _periodic 直读)。
_update_exec_buttons = ui_exec_bar.set_state


# ③ 日志工具条 (级别过滤 / 关键字搜索 / 清空 / 导出)、§4.6 左缘色条与行 tag 逻辑
#    已整体迁至 src/ui/log_dock.py (阶段二第 3 项第 1 步)。本文件仅保留上方 3212 处的
#    转发别名 (_log_apply_cbar / _log_apply_filter / _log_search_next / _log_clear_view /
#    _log_export_view / _log_autoscroll_changed), 既有引用契约不变。


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

# ── 工作流 Tab (阶段二第 3 项第 3 步): 构建 + 行为函数 + 换肤订阅已整体迁至
#    src/ui/workflow_view.py; 本文件保留「转发别名」, 既有直读 ACRPA.<name> 的
#    引用契约不变 (真源在 ui_workflow_view; 控件句柄别名在 build_app() 内经
#    ui_workflow_view.get_widgets() 取回)。
#    迁移时已清理: _wf_stop 原重复定义 (保留生效版本)、_wf_scheduler_callback 死引用。
_wf_update_overflow = ui_workflow_view.update_overflow
_wf_on_wheel = ui_workflow_view.on_wheel
_wf_flow_on_wheel = ui_workflow_view.flow_on_wheel
_wf_toggle_view = ui_workflow_view.toggle_view
_wf_refresh_tree = ui_workflow_view.refresh_tree
_wf_update_workflow_highlight = ui_workflow_view.update_highlight
_wf_run = ui_workflow_view.run
_wf_stop = ui_workflow_view.stop
_wf_new = ui_workflow_view._wf_new
_wf_open = ui_workflow_view._wf_open
_wf_save = ui_workflow_view._wf_save
_wf_add_step = ui_workflow_view._wf_add_step
_wf_edit_step = ui_workflow_view._wf_edit_step
_wf_delete_step = ui_workflow_view._wf_delete_step
_wf_move_up = ui_workflow_view._wf_move_up
_wf_move_down = ui_workflow_view._wf_move_down
_wf_export_template = ui_workflow_view._wf_export_template
_wf_export_screenshot = ui_workflow_view._wf_export_screenshot
_wf_context_menu = ui_workflow_view._wf_context_menu
_wf_open_variable_manager = ui_workflow_view._wf_open_variable_manager
_wf_library_context_menu = ui_workflow_view._wf_library_context_menu
_wf_add_from_library = ui_workflow_view._wf_add_from_library
_wf_add_recent_clicked = ui_workflow_view._wf_add_recent_clicked
_wf_filter_library = ui_workflow_view._wf_filter_library
_wf_render_flowchart = ui_workflow_view._wf_render_flowchart

# === Recording done callback: inject recorded actions into editor ──
def _on_recording_done():
    for sd in state.recorded_actions:
        state._editor_rows.append(sd)
    _editor_sync_to_tree()
    log1("已从录制导入 {} 条命令".format(len(state.recorded_actions)))
    from utils import show_toast; show_toast(root, "录制完成 ({} 条)".format(len(state.recorded_actions)), "success")

# ======================================================================
# Status Bar — 状态文本 + 控制信息 + 执行进度条 (进度条自适应窗口宽度)
# ======================================================================
_DASH_EMPTY = i18n.t("status.dash_empty")

# ======================================================================
# Periodic Updates
# ======================================================================

def _periodic():
    if state._closing: return
    # ③ 日志面板: flush + 左缘色条 + 增量级别过滤 (自动滚动关闭时保留视口)。
    #    内联实现已迁至 src/ui/log_dock.py::flush_and_apply —— 其返回 False 等价原
    #    「flush 失败 → return」语义 (提前结束本轮 _periodic)。
    if not ui_log_dock.flush_and_apply():
        return
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
            status_text.config(text=i18n.t("status.paused"),fg=C["wn"])
            status_dot.config(text=i18n.t("status.dot_paused"),fg=C["wn"])
        elif is_running:
            status_text.config(text=i18n.t("status.running"),fg=C["sc"])
            status_dot.config(text=i18n.t("status.dot_running"),fg=C["sc"])
        elif is_stopped:
            status_text.config(text=i18n.t("status.stopped"),fg=C["dg"])
            status_dot.config(text=i18n.t("status.dot_stopped"),fg=C["dg"])
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
            status_text.config(text=i18n.t("status.ready",
                mod=mod_mark, ai=(" "+ai_status if ai_status else ""), rows=rows_count), fg=C["fgm"])
            status_dot.config(text=i18n.t("status.dot_ready"),fg=C["fgm"])
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
        dash_txt = (i18n.t("status.loop", cur=lp, total=("∞" if tl>99999 else tl))
                    + i18n.t("status.sep")
                    + i18n.t("status.rows", done=rw, total=tr)
                    + i18n.t("status.sep")
                    + i18n.t("status.elapsed", elapsed=_fmt_dur(el))
                    + i18n.t("status.sep")
                    + i18n.t("status.eta", eta=eta_txt))
        if ok_n is not None:
            dash_txt += i18n.t("status.counts", ok=ok_n, fail=(fail_n or 0))
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
            status_text.config(text=i18n.t("status.recording", n=n),fg=C["dg"])
            status_dot.config(text=i18n.t("status.dot_recording"),fg=C["dg"])
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
        status_text.config(text=i18n.t("status.update_available", ver=ver), fg=C["wn"])
        status_dot.config(text=i18n.t("status.dot_update"), fg=C["wn"])
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

# 注意: mainloop() 调用已移至 run.py,避免重复调用
# root.mainloop()


def build_app():
    """构建应用(全部 GUI 控件与依赖装配)。原先的模块级副作用全部收于此函数,
    import ACRPA 不再产生任何副作用(不建窗/不 after/不起线程/不 makedirs/不 load_config)。"""
    global APP_ROOT, C, FONT_BODY, FONT_BUTTON, FONT_ICON, FONT_ICON_LG, FONT_ICON_MD, FONT_LOG, \
        FONT_SMALL, FONT_SMALL_BOLD, FONT_TINY, FONT_TITLE, ICON_BIG, ICON_SMALL, IMAGE_ICON, \
        LOG_DIR, LR_LOADFROMFILE, RES_DIR, SCREENSHOT_DIR, VERSION, WM_SETICON, \
        _CBAR_LEVEL_COLOR, _EDITOR_ZOOM_MAX, _EDITOR_ZOOM_MIN, _WF_LIB_ACTIONS, \
        _applied_dark_mode, _ccol, _ctag, _e, _editor_font, _fn, _hotkey_executors, \
        _log_collapse_btn, _log_level_combo, _log_save_path, _log_search_entry, _loop_combo, \
        _main_thread, _netlink_mod, _ocr_backend_mod, _script_switcher, _seq, \
        _title_text_session, _tlog, _wf_show_flow, _wf_view_btn, _wfp, bottom_dock, \
        btn_debug_exec, btn_debug_mode, btn_pause, btn_run, btn_step, btn_step_mode, btn_stop, \
        btn_validate, btn_vars_exec, bundle_res, c, card_log, card_run, color, ctypes, dark_btn, dark_frame, \
        dash_info, devlink_btn, e, edit_file_label, exec_bar, exec_maxmin_sp, exec_maxmin_var, \
        exec_stoponerror_var, f, fold_btn, help_btn, hicon, hwnd, i, ico_path, label, \
        legend_frame, legend_inner, lh, log_auto_scroll, log_dock_toggle_btn, log_frame, \
        log_level_var, log_search_var, loop_count_var, lt, main_paned, netlink, notebook, \
        pin_btn, progress_bar, root, run_bar, run_bar_outer, run_canvas, run_overflow_hint, \
        run_scrollbar, rz, script_name_var, scroll, settings_btn, status_bar, status_dot, \
        status_text, style, tab_edit, tab_workflow, tb_scroll, title_bar, title_lbl, toolbar, \
        toolbar_canvas, toolbar_inner, toolbar_overflow_hint, toolbar_scrollbar, tree, \
        tree_frame, tsx, tsy, wf_canvas, wf_flow_canvas, wf_flow_frame, wf_flow_scroll_y, \
        wf_lib_frame, wf_lib_search, wf_lib_sy, wf_lib_tree, wf_list_frame, wf_loop_var, wf_main, \
        wf_maxmin_var, wf_name_entry, wf_name_var, wf_paned, wf_recent_label, wf_recent_list, \
        wf_scrollbar, wf_sy, wf_toolbar, wf_toolbar_outer, wf_toolbar_overflow_hint, wf_tree
    state.load_config()


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

    def _window_title():
        """窗口标题（标题栏 / 任务栏 / Alt-Tab 共用同一文案）: "A/C RPA v<版本>"。

        版本号取自 version_info（唯一事实来源）；这里不依赖上面 try 块里的
        `from version_info import VERSION`（那条 import 在 except 分支下不保证
        名字可见），改为在本函数内自行安全获取。任何一步失败都退回不带版本号的
        "A/C RPA"，绝不让"取版本"这件事影响启动。
        注: 界面内自绘标题 Label（TITLE_DEFAULT_TEXT）不受影响。
        """
        try:
            import version_info as _vi
            ver = _vi.get_version() or getattr(_vi, "VERSION", "")
        except Exception:
            ver = ""
        ver = str(ver).strip()
        return "A/C RPA v{}".format(ver) if ver else "A/C RPA"

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

    root = tkinter.Tk()
    # 命名字体必须在创建任何 widget 之前建立; tk scaling 改变 pt->px 换算后再刷一次
    utils.init_fonts(root)
    try:
        root.tk.call("tk", "scaling", root.winfo_fpixels("1i") / 72.0)
    except Exception:
        pass
    utils.init_fonts(root)
    try:
        root.title(_window_title())     # 标题栏 / 任务栏 / Alt-Tab 统一带版本号
    except Exception:
        pass

    _apply_main_geometry()
    root.configure(bg=C["bg"]); root.resizable(width=True,height=True)

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

    # 设置任务栏窗口标题（与 AppUserModelID 配合）；与 root.title() 共用 _window_title()
    try:
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        ctypes.windll.user32.SetWindowTextW(hwnd, _window_title())
    except Exception:
        pass

    # ttk Style
    style = ttk.Style(); style.theme_use("clam")

    apply_theme(root, style)

    root.protocol("WM_DELETE_WINDOW", window_close)

    # === Layout - compact spacing ──
    root.columnconfigure(0,weight=1)
    # ① 标题栏 / ② 执行控制工具栏(常驻) / ③ 主区(内容 + 底部日志) / ⑦ 状态栏
    root.rowconfigure(0,weight=0)
    root.rowconfigure(1,weight=0)
    root.rowconfigure(2,weight=1)
    root.rowconfigure(3,weight=0)

    # Title bar - compact design
    title_bar = tkinter.Frame(root,bg=C["bg"])
    title_bar.grid(row=0,column=0,sticky="ew",padx=utils.sp(10),pady=(utils.sp(6),utils.sp(2)))
    title_bar.columnconfigure(0,weight=1)

    title_lbl = tkinter.Label(title_bar,text="Adaptive Control Automation Workflow",
        font=FONT_TITLE,fg=C["fgt"],bg=C["bg"])
    title_lbl.grid(row=0,column=0,sticky="w")
    _title_text_session = TITLE_DEFAULT_TEXT            # 会话内标题 (模块级, 重启即复位)

    title_lbl.bind("<Double-Button-1>", _start_title_edit)

    # Dark mode toggle - compact
    dark_frame = tkinter.Frame(title_bar,bg=C["bg"])
    dark_frame.grid(row=0,column=1,sticky="e")
    # 语义角色 "ac": 旧实现按「● 就绪」文案嗅探 → fg=ac; 现创建时显式登记 (行为等价)
    status_dot = ui_theme.roled(
        tkinter.Label(dark_frame,text=i18n.t("status.dot_ready"),font=FONT_SMALL,fg=C["fgm"],bg=C["bg"]), "ac")
    status_dot.pack(side="left",padx=(0,utils.sp(6)))
    pin_btn = tkinter.Label(dark_frame,text="△",font=FONT_ICON,
        fg=C["fgm"],bg=C["bg"],cursor="hand2",padx=utils.sp(2))
    pin_btn.pack(side="left",padx=(0,utils.sp(2)))
    pin_btn.bind("<Enter>", _show_pin_tip)

    # 折叠按钮 — 将主窗口折叠为 Mini Bar
    fold_btn = tkinter.Label(dark_frame,text="⊟",font=FONT_ICON,
        fg=C["fgm"],bg=C["bg"],cursor="hand2",padx=utils.sp(2))
    fold_btn.pack(side="left",padx=(0,utils.sp(2)))
    fold_btn.bind("<Enter>", _show_fold_tip)

    dark_btn = tkinter.Label(dark_frame,text="◑" if state.DARK_MODE else "◐",
        font=FONT_ICON_LG,fg=C["fgm"],bg=C["bg"],cursor="hand2")
    dark_btn.pack(side="left")

    # 标题栏底部 1px 发丝线 (§4.1: 1px 发丝线替代原 2px 强调条)
    tkinter.Frame(title_bar, bg=C["bd"], height=1).grid(row=1, column=0, columnspan=2, sticky="ew", pady=(utils.sp(5),0))
    dark_btn.bind("<Enter>", _show_tip)
    dark_btn.bind("<Leave>", _hide_tip)
    fold_btn.bind("<Leave>", _hide_tip)
    pin_btn.bind("<Leave>", _hide_tip)

    # 设置按钮 — 打开独立设置窗口 (原设置 Tab 已分离，主界面入口)
    settings_btn = tkinter.Label(dark_frame, text="⚙", font=FONT_ICON_MD,
        fg=C["fgm"], bg=C["bg"], cursor="hand2", padx=utils.sp(2))
    settings_btn.pack(side="left", padx=(0, utils.sp(2)))
    settings_btn.bind("<Enter>", _show_settings_tip)
    settings_btn.bind("<Leave>", _hide_tip)

    # 帮助按钮 — 「?」图标, 与设置图标并列且位于其后 (原「执行控制」底部「? 帮助」上移)
    help_btn = tkinter.Label(dark_frame, text="?", font=FONT_ICON_MD,
        fg=C["fgm"], bg=C["bg"], cursor="hand2", padx=utils.sp(2))
    help_btn.pack(side="left", padx=(0, utils.sp(2)))
    help_btn.bind("<Enter>", _show_help_tip)
    help_btn.bind("<Leave>", _hide_tip)

    # 互联按钮 — 打开「设备互联」独立窗口 (与设置按钮同级样式)
    devlink_btn = tkinter.Label(dark_frame, text="⊕", font=FONT_ICON_MD,
        fg=C["fgm"], bg=C["bg"], cursor="hand2", padx=utils.sp(2))
    devlink_btn.pack(side="left", padx=(0, utils.sp(2)))
    devlink_btn.bind("<Enter>", _show_devlink_tip)
    devlink_btn.bind("<Leave>", _hide_tip)

    # ── 可达性补齐 (路线图 §5.5): 图标 Label → 可 Tab 聚焦 + 可键盘激活 + 可访问名 ──
    # 在 <Enter>/<Leave> tooltip 绑定之后调用, 以免 set_accessible_name 抢先挂上 tooltip。
    # <Return>/<space> 与鼠标点击触发同一处理函数 (纯键盘等价)。
    utils.bind_icon_activate(pin_btn, toggle_pin, "固定窗口（置顶）")
    utils.bind_icon_activate(fold_btn, _toggle_fold, "折叠为 Mini Bar")
    utils.bind_icon_activate(dark_btn, toggle_dark, "切换深浅色主题")
    utils.bind_icon_activate(settings_btn, settings_window.open_settings_window, "打开设置")
    utils.bind_icon_activate(help_btn, show_help_dialog, "帮助")
    utils.bind_icon_activate(devlink_btn, open_devlink, "设备互联")

    # ③ 主区: 垂直 PanedWindow —— 上 pane 内容(notebook) / 下 pane 常驻日志面板
    # (tkinter grid 无可拖 sash, 「可拖 + minsize + 一键收起」的原生正解即 PanedWindow)
    main_paned = tkinter.PanedWindow(root, orient="vertical",
        bg=C["bd"], sashwidth=utils.sp(4), sashrelief="raised",
        opaqueresize=True, bd=0)
    main_paned.grid(row=2,column=0,sticky="nsew",padx=utils.sp(8),pady=(0,utils.sp(3)))

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

    # TreeView - compact layout
    tree_frame = tkinter.Frame(tab_edit,bg=C["bgc"],
        highlightbackground=C["bd"],highlightthickness=1)
    tree_frame.grid(row=1,column=0,sticky="nsew",padx=utils.sp(3),pady=(0,utils.sp(4)))
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
    _EDITOR_ZOOM_MIN, _EDITOR_ZOOM_MAX = -4, 12
    _editor_font = tkinter.font.Font(root=root, name="ACRPA_EDITOR_TABLE", exists=False,
                                     family="Microsoft YaHei UI", size=10)


    _apply_editor_zoom()
    tree.bind("<Control-MouseWheel>", _editor_zoom_wheel)
    tree.bind("<Control-plus>", lambda e: _editor_zoom_step(1))
    tree.bind("<Control-equal>", lambda e: _editor_zoom_step(1))   # =/+ 同键
    tree.bind("<Control-minus>", lambda e: _editor_zoom_step(-1))
    tree.bind("<Control-Key-0>", _editor_zoom_reset)

    # ── 颜色图例条 ──
    legend_frame = tkinter.Frame(tab_edit, bg=C["bg"])

    # ── 工作流名称 ──
    legend_frame.grid(row=2, column=0, sticky="ew", padx=utils.sp(3), pady=(0, utils.sp(2)))
    legend_inner = tkinter.Frame(legend_frame, bg=C["bg"])
    legend_inner.pack(anchor="w")
    for label, color in _LEGEND_ITEMS:
        f = tkinter.Frame(legend_inner, bg=C["bg"])
        f.pack(side="left", padx=(2, 0))
        # 图例圆点同样登记 "fgm" 角色 (旧实现按「含 ●」嗅探 → fg=fgm)
        ui_theme.roled(tkinter.Label(f, text="●", font=FONT_ICON,
            fg=color, bg=C["bg"]), "fgm").pack(side="left")
        tkinter.Label(f, text=label, font=FONT_TINY,
            fg=C["fgm"], bg=C["bg"]).pack(side="left")

    # tree double-click binding is set after _edit_cell is defined below
    tree.tag_configure("even", background=C["zebra"])   # §4.5 斑马纹 (不再用 acl)
    tree.tag_configure("running", background=C["hlbg"])   # 主题色 (旧为写死 #FEF3C7)
    tree.tag_configure("breakpoint", foreground=themed("dg"))


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
    tree.bind("<Button-1>", _toggle_breakpoint, add=True)

    tree.bind("<Button-3>", _show_conditional_breakpoint_menu)
    tree.bind("<Control-c>", _kb_copy)
    tree.bind("<Control-v>", _kb_paste)
    tree.bind("<Delete>", lambda e: _cmd_del_row())

    tree.bind("<Control-z>", _cmd_undo)
    tree.bind("<Control-y>", _cmd_redo)
    tree.bind("<Control-Z>", _cmd_undo)
    tree.bind("<Control-Y>", _cmd_redo)


    tree.bind("<Control-d>", _kb_duplicate_row)
    tree.bind("<Control-D>", _kb_duplicate_row)
    tree.bind("<Control-slash>", _kb_toggle_comment)

    # Ctrl+K — 命令库 (命令分组 + 模糊搜索); 写入当前行 (无选中则新增行)。
    # 同时绑到 root 以便焦点不在树时也能唤起; 处理器返回 "break" 避免重复触发。
    tree.bind("<Control-k>", _kb_command_palette)
    tree.bind("<Control-K>", _kb_command_palette)
    try:
        root.bind("<Control-k>", _kb_command_palette)
        root.bind("<Control-K>", _kb_command_palette)
    except Exception:
        pass

    tree.bind("<Alt-Up>", _kb_move_up)
    tree.bind("<Alt-Down>", _kb_move_down)
    tree.bind("<F9>", _kb_toggle_breakpoint)
    tree.bind("<F5>", _kb_run_script)
    # F2 — 「编辑行」结构化对话框 (与双击内联编辑 _edit_cell 并存, 不改其绑定)
    tree.bind("<F2>", lambda e: _cmd_edit_row_dialog())

    # 将双击绑定放在 _edit_cell 定义之后
    tree.bind("<Double-1>", lambda e: _edit_cell())


    # Initialize toolbar buttons now that all command functions are defined
    _init_toolbar_buttons()


    # ======================================================================
    # ② 执行控制工具栏 (常驻 · 两 Tab 共用) —— 由原「执行控制」Tab 降级而来
    #   构建 / 行为 / 换肤订阅整体抽到 src/ui/exec_bar.py (阶段二第 3 项第 2 步):
    #   宿主在此仅注入依赖并取回控件句柄 (转发别名: 四键 / script_name_var /
    #   最近脚本 / 次数 / 执行参数)。执行核心 (main_run/stop_execution/toggle_pause/
    #   _step_once/_fmt_dur) 留在本文件, 经 deps 注入 exec_bar 的命令与分派回调。
    # 设计: docs/底部常驻日志面板重构设计方案.md
    # ======================================================================
    ui_exec_bar.build(
        root, notebook=notebook, colors=C,
        fonts={"title": FONT_TITLE, "body": FONT_BODY,
               "small": FONT_SMALL, "button": FONT_BUTTON},
        deps={"ui_theme": ui_theme, "create_card": create_card,
              "sp": utils.sp, "ctrl_h": utils.ctrl_h,
              "attach_tooltip": utils.attach_tooltip, "darken": _darken,
              "state": state, "log1": log1, "show_toast": utils.show_toast,
              "root": root, "notebook": notebook, "app_root": APP_ROOT,
              "screenshot_dir": SCREENSHOT_DIR,
              "get_rz": lambda: rz, "get_status_dot": lambda: status_dot,
              "get_status_text": lambda: status_text,
              "get_edit_file_label": lambda: edit_file_label,
              "editor_load_xls": _editor_load_xls,
              "main_run": main_run, "stop_execution": stop_execution,
              "validate_script_cb": validate_script_cb,
              "toggle_pause": toggle_pause, "step_once": _step_once,
              "run_workflow": _wf_run, "stop_workflow": _wf_stop,
              "toggle_debug_mode": _toggle_debug_mode,
              "show_variables_window": _show_variables_window,
              "toggle_step_mode": _toggle_step_mode,
              "load_pil": _load_pil, "get_pa": _get_pa,
              "get_image_tk": lambda: ImageTk})
    # ── 转发别名: 既有直读 ACRPA.<name> 的引用 (mini_bar/热键/NetLink/_periodic/
    #    _setup_scroll_bindings/tools) 契约不变; 真源在 ui_exec_bar ──
    _ew = ui_exec_bar.get_widgets()
    exec_bar = _ew["exec_bar"]
    card_run = _ew["card_run"]
    run_bar_outer = _ew["run_bar_outer"]
    run_canvas = _ew["run_canvas"]
    run_scrollbar = _ew["run_scrollbar"]
    run_bar = _ew["run_bar"]
    run_overflow_hint = _ew["run_overflow_hint"]
    script_name_var = _ew["script_name_var"]
    _script_switcher = _ew["script_switcher"]
    loop_count_var = _ew["loop_count_var"]
    _loop_combo = _ew["loop_combo"]
    exec_maxmin_var = _ew["exec_maxmin_var"]
    exec_maxmin_sp = _ew["exec_maxmin_sp"]
    exec_stoponerror_var = _ew["exec_stoponerror_var"]
    btn_validate = _ew["btn_validate"]
    btn_run = _ew["btn_run"]
    btn_pause = _ew["btn_pause"]
    btn_step = _ew["btn_step"]
    btn_stop = _ew["btn_stop"]
    btn_step_mode = _ew["btn_step_mode"]
    btn_debug_exec = _ew["btn_debug_exec"]
    btn_vars_exec = _ew["btn_vars_exec"]
    btn_debug_mode = _ew["btn_debug_mode"]

    # ──────────────────────────────────────────────────────────────────────
    # ② 执行仪表盘已并入底部状态栏 (dash_info 控制信息 + progress_bar 进度条)
    # ──────────────────────────────────────────────────────────────────────

    # ⑥ 底部常驻日志面板 (两 Tab 共用) —— 已整体抽到 src/ui/log_dock.py
    #    (阶段二第 3 项第 1 步): 构建 / 工具条行为 / 换肤订阅全内聚在该模块;
    #    宿主在此仅注入依赖并取回控件句柄 (转发别名: _periodic / 状态栏 / tools 直读)。
    _log_handle = ui_log_dock.build(
        bottom_dock, log_dir=LOG_DIR, colors=C,
        fonts={"title": FONT_TITLE, "body": FONT_BODY, "small": FONT_SMALL,
               "button": FONT_BUTTON, "log": FONT_LOG},
        deps={"ui_theme": ui_theme, "create_card": create_card,
              "thread_safe_log": ThreadSafeLog, "set_tlog": set_tlog,
              "log1": log1, "show_toast": utils.show_toast,
              "main_paned": main_paned, "root": root,
              "app_root": APP_ROOT, "screenshot_dir": SCREENSHOT_DIR,
              "state": state, "toggle_button": None})
    # ── 转发别名: 既有直读 ACRPA.<name> 的引用 (_periodic / tools) 契约不变 ──
    #    (set_tlog 契约已在 log_dock.build 内调用一次, utils.log1 全库可用)
    rz = ui_log_dock.get_rz()
    scroll = ui_log_dock.get_scroll()
    _tlog = ui_log_dock.get_tlog()
    log_auto_scroll = ui_log_dock.get_autoscroll_var()
    log_level_var = ui_log_dock.get_level_var()
    log_search_var = ui_log_dock.get_search_var()
    log_frame = ui_log_dock.get_log_frame()
    lt = ui_log_dock.get_toolbar()
    lh = ui_log_dock.get_header()
    _log_collapse_btn = ui_log_dock.get_collapse_btn()
    _log_level_combo = ui_log_dock.get_level_combo()
    _log_search_entry = ui_log_dock.get_search_entry()
    _log_save_path = ui_log_dock.get_log_save_path()
    _CBAR_LEVEL_COLOR = ui_log_dock.get_cbar_colors()

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

    # 设置卡片 (基础执行/AI增强/定时调度/日志/系统/快速操作/高级) 已移至 settings_window

    # 日志/快速操作/高级设置/系统卡片已移至 settings_window

    # ======================================================================
    # TAB 4: 工作流
    #   构建 / 行为 / 换肤订阅已整体抽到 src/ui/workflow_view.py (阶段二第 3 项第 3 步):
    #   宿主在此仅注入依赖并取回控件句柄 (转发别名: tab_workflow / wf_* / _wf_show_flow /
    #   _wf_view_btn)。领域层 src/workflow.py 不迁、不改。
    # ======================================================================
    ui_workflow_view.build(
        notebook, colors=C,
        fonts={"title": FONT_TITLE, "body": FONT_BODY,
               "small": FONT_SMALL, "button": FONT_BUTTON,
               "small_bold": FONT_SMALL_BOLD, "log": FONT_LOG},
        deps={"ui_theme": ui_theme, "tbtn": _tbtn, "sep": _sep,
              "sp": utils.sp, "ctrl_h": utils.ctrl_h, "darken": _darken,
              "btn": _btn, "state": state, "log1": log1,
              "show_toast": utils.show_toast, "messagebox": messagebox,
              "filedialog": filedialog, "app_root": APP_ROOT, "root": root,
              "exec_maxmin_var": exec_maxmin_var,
              "set_window_icon": _set_window_icon,
              "bind_sel_bold": _bind_sel_bold, "bind_row_hover": _bind_row_hover,
              "load_pil": _load_pil, "get_image_grab": lambda: ImageGrab})
    # ── 转发别名: 既有直读 ACRPA.<name> 的引用 (_setup_scroll_bindings / tools /
    #    _periodic) 契约不变; 真源在 ui_workflow_view ──
    _wfh = ui_workflow_view.get_widgets()
    tab_workflow = _wfh["tab_workflow"]
    wf_toolbar_outer = _wfh["wf_toolbar_outer"]
    wf_canvas = _wfh["wf_canvas"]
    wf_scrollbar = _wfh["wf_scrollbar"]
    wf_toolbar = _wfh["wf_toolbar"]
    wf_toolbar_overflow_hint = _wfh["wf_toolbar_overflow_hint"]
    wf_loop_var = _wfh["wf_loop_var"]
    wf_maxmin_var = _wfh["wf_maxmin_var"]
    wf_name_var = _wfh["wf_name_var"]
    wf_name_entry = _wfh["wf_name_entry"]
    wf_main = _wfh["wf_main"]
    wf_lib_frame = _wfh["wf_lib_frame"]
    wf_lib_search = _wfh["wf_lib_search"]
    wf_lib_tree = _wfh["wf_lib_tree"]
    wf_lib_sy = _wfh["wf_lib_sy"]
    wf_recent_label = _wfh["wf_recent_label"]
    wf_recent_list = _wfh["wf_recent_list"]
    wf_paned = _wfh["wf_paned"]
    wf_list_frame = _wfh["wf_list_frame"]
    wf_tree = _wfh["wf_tree"]
    wf_sy = _wfh["wf_sy"]
    wf_flow_frame = _wfh["wf_flow_frame"]
    wf_flow_canvas = _wfh["wf_flow_canvas"]
    wf_flow_scroll_y = _wfh["wf_flow_scroll_y"]
    _wf_show_flow = _wfh["_wf_show_flow"]
    _wf_view_btn = _wfh["_wf_view_btn"]
    _WF_LIB_ACTIONS = _wfh["_WF_LIB_ACTIONS"]

    # Wire scheduler to GUI (设置窗口为独立窗口: 勾选变量在此创建并复用，下次执行标签由设置窗口注入)
    state._sched_enabled_var = tkinter.BooleanVar(value=state.SCHED_ENABLED)
    sched.set_gui_refs(root, state._sched_enabled_var, None)
    recorder.set_root(root)
    state._on_recording_done = _on_recording_done
    status_bar = tkinter.Frame(root,bg=C["bg"])
    status_bar.grid(row=3,column=0,sticky="ew",padx=utils.sp(10),pady=(utils.sp(3),utils.sp(6)))
    status_bar.columnconfigure(2,weight=1)
    # 语义角色 "ac": 旧实现按文案含「就绪」嗅探 → fg=ac; 现创建时显式登记 (行为等价)
    status_text = ui_theme.roled(tkinter.Label(status_bar,text=i18n.t("status.ready_initial"),
        font=FONT_SMALL,fg=C["fgm"],bg=C["bg"],anchor="w"), "ac")
    status_text.grid(row=0,column=0,sticky="w")

    # 控制信息 (循环/行/已用/ETA/成功·失败) — 与进度条同排
    dash_info = tkinter.Label(status_bar,text=_DASH_EMPTY,font=FONT_SMALL,
        fg=C["fgm"],bg=C["bg"],anchor="w")
    dash_info.grid(row=0,column=1,sticky="w",padx=(utils.sp(12),utils.sp(8)))

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
    log_dock_toggle_btn.grid(row=0,column=3,sticky="e",padx=(utils.sp(8),0))
    log_dock_toggle_btn.bind("<Button-1>", lambda e: _toggle_log_dock())
    # 注入日志面板的「展开入口」(日志面板先于状态栏构建, 故此处回填)
    ui_log_dock.set_toggle_button(log_dock_toggle_btn)

    # Mini Bar 上下文注入 (整个界面此时已构建完毕; _load_pil / ui_scale 钩子
    # 与 ThemeBus 订阅还会在运行期按需重新注入)
    _bind_minibar()
    # 阶段二第 2 项: 登记各独立窗口的换肤订阅 (新增窗口只需 subscribe, 无需改 _refresh_theme)
    _subscribe_theme_bus()


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


    _hotkey_executors = {
        "run": main_run,
        "pause": toggle_pause,
        "stop": stop_execution,
    }


    # 主线程标识: 用于把工作线程触发的配置变更回调调度回 Tk 主线程
    _main_thread = threading.current_thread()


    state.on_config_change(_on_config_changed, keys=(
        "dark_mode", "retry_max", "retry_interval",
        "hotkey_run", "hotkey_pause", "hotkey_stop",
        "recording_stop_hotkey"))
    _rebuild_hotkey_specs()

    root.after(200, _hotkey_poll)

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

    return root
