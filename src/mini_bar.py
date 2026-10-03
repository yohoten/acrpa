# -*- coding: utf-8 -*-
"""ACRPA Mini Bar —— 可折叠悬浮条 (从 src/ACRPA.py 抽出的第一个 UI 集群)。

为什么单独成模块
----------------
src/ACRPA.py 曾是一个 5700+ 行、196 个顶层函数、53 处 global 的巨石, 任何改动都得
在整文件里翻找。Mini Bar 是其中边界最清晰的一块: 窗口生命周期 + 定时器 + 位置记忆,
且它的 17 个模块级状态变量在集群外 **0 引用**(已用 AST 核对), 因此作为拆分第一阶段。

依赖注入约定 (与 src/tray.py 一致: 被拆出的模块不反向 import 宿主)
----------------------------------------------------------------
函数体保持原样搬运, 它们引用的宿主符号由 ACRPA.py 启动时经 bind() 注入::

    import mini_bar
    mini_bar.bind(root=root, colors=C, state=state, ...)

代价是"注入清单必须完整"—— 漏一个就是运行期 NameError。因此:

  · tools/_test_minibar_split.py 会静态核对本模块内所有非局部全局名, 要么定义在
    本模块, 要么出现在下面的 INJECTED 清单里;
  · bind() 对清单外的键直接报错, 不会静默吞掉。
"""

import os
import sys
import time
import tkinter
import tkinter.ttk as ttk

# ══════════════════════════════════════════════════════════════════════
# 注入位 (由 ACRPA.py 的 _bind_minibar() 填充, 可重复调用)
# ══════════════════════════════════════════════════════════════════════
from utils import _darken
from engine import engine
import os
import re
import state
import sys
import time
import tkinter
from tkinter import ttk

APP_ROOT = None
C = None
FONT_BODY = None
FONT_BUTTON = None
FONT_ICON_MD = None
FONT_LOG = None
Image = None
ImageTk = None
RES_DIR = None
_fmt_dur = None
_load_pil = None
_quit_from_tray = None
_set_window_icon = None
_step_once = None
_toggle_fold = None
main_run = None
root = None
stop_execution = None
toggle_pause = None

INJECTED = (
    "APP_ROOT",
    "C",
    "FONT_BODY",
    "FONT_BUTTON",
    "FONT_ICON_MD",
    "FONT_LOG",
    "Image",
    "ImageTk",
    "RES_DIR",
    "_fmt_dur",
    "_load_pil",
    "_quit_from_tray",
    "_set_window_icon",
    "_step_once",
    "_toggle_fold",
    "main_run",
    "root",
    "stop_execution",
    "toggle_pause",
)


def bind(**kw):
    """注入宿主符号; 可重复调用 (主题/缩放切换后 ACRPA 会再次调用以刷新颜色与字体)。"""
    bad = sorted(k for k in kw if k not in INJECTED)
    if bad:
        raise KeyError("mini_bar.bind 收到未声明的注入键: {}".format(", ".join(bad)))
    globals().update(kw)


# ══════════════════════════════════════════════════════════════════
# 以下为从 ACRPA.py 原样搬出的实现
# ══════════════════════════════════════════════════════════════════

_mini_bar = None  # Mini Bar Toplevel 引用


_mb_anim_id = None          # 宽度平滑过渡动画 after id


_mb_idle_id = None          # 空闲收起计时 after id


_mb_blink_id = None         # 告警闪烁动画 after id


_mb_breathe_id = None       # 暂停呼吸动画 after id


_mb_breathe_phase = 0       # 暂停呼吸相位 (0/1)


_mb_blink_count = 0         # 闪烁剩余帧数 (3 次 = 6 帧)


_mb_form = "compact"        # 当前形态: icon / compact / run


_mb_last_interact = 0.0     # 上次用户交互时间戳 (空闲 30s 收为图标态)


_mb_prev_failed = False     # 上一周期是否已告警 (失败沿触发闪烁)


_MB_IDLE_SEC = 30           # 空闲多少秒后收起为图标态


_MB_ACCENT = 1              # 顶部强调色描边高度 (从条高中扣除)


_MB_RUN_EXTRA = 130         # 运行态相对紧凑态的额外宽度下限 (实测需求宽更大时自动放宽)


_MB_BTN_PADY = 2            # 操作按钮上下内边距 (配合 linespace 反推内容高)


_MB_BTN_BD = 1              # 操作按钮描边宽度


_MB_ICON_MAX = 32           # 应用图标最大边长


_MB_DOT_H = 10              # 状态点直径


def _mb_set_icon(label):
    """在 Mini Bar 左侧加载并显示应用图标（尺寸随条高自适应, 垂直居中）。

    资源回退链: res/automation.png → res/icon.png → res/icon-32.png
    → (打包态) exe 同级 _internal/res 下的同名资源 → 最后回退 Unicode "⚙"。
    之前仅尝试 automation.png（该文件不存在）导致永远走 Unicode 回退。
    """
    def _fallback_text():
        """Unicode 回退: Segoe UI Symbol 在 Windows 下可正常渲染 ⚙, 颜色取主题强调色。"""
        label.configure(image="", text="⚙", font=FONT_ICON_MD,
            fg=C["ac"], bg=C["bgc"], anchor="center")

    # 图标边长随「内容区高度」自适应: 内容区高 - 2px 视觉边距, 夹取 [12, _MB_ICON_MAX]。
    # 旧实现上限 20px 且图标未铺满, 实测左端上下各留 4px 空白 (见诊断脚本), 现放开上限,
    # 让图标随条高/UI 缩放同步变大, 视觉上与按钮等高。
    h = _mb_effective_height()
    size = max(12, min(_mb_inner_h(h) - 2, _MB_ICON_MAX))
    cands = [os.path.join(RES_DIR, "automation.png"),
             os.path.join(RES_DIR, "icon.png"),
             os.path.join(RES_DIR, "icon-32.png"),
             os.path.join(APP_ROOT, "automation.png")]
    # PyInstaller onedir: res/ 在 exe 同级的 _internal 目录
    exe_dir = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else ""
    if exe_dir:
        for base in (os.path.join(exe_dir, "_internal", "res"),
                     os.path.join(exe_dir, "res")):
            cands.append(os.path.join(base, "automation.png"))
            cands.append(os.path.join(base, "icon.png"))
    png_path = next((p for p in cands if os.path.exists(p)), None)
    if png_path:
        try:
            _load_pil()
            img = Image.open(png_path).convert("RGBA").resize(
                (size, size), Image.LANCZOS)
            photo = ImageTk.PhotoImage(img)
            label.configure(image=photo, text="")
            label._photo = photo  # 保存引用防止被 GC
            return
        except Exception:
            pass
    try:
        _fallback_text()
    except Exception:
        pass


def _mb_inner_h(h):
    """内容区高度 = 窗口高 - 顶部强调线 (子控件可用的真实高度)。"""
    return max(1, int(h) - _MB_ACCENT)


def _mb_content_height(font=None):
    """按指定命名字体实测「内容区所需高度」(文本行高 + 内边距 + 描边)。

    FONT_BUTTON 的字号随 state.UI_SCALE 变化 (9pt→10pt→13pt...), 实测
    tk scaling=1.667 时 10pt 的 linespace ≈ 24px, 故条高不能是常量:
    这里用 tkfont 反推, 保证「字体被缩放放大」时是条被撑开而不是内容被裁剪。
    参数化 font (默认 FONT_BUTTON): 录制条含更大字号的指示灯 (FONT_ICON_LG),
    需按「最大子控件字体」反推高度, 否则 UI_SCALE≈1.4~1.5 时 "●" 被纵向裁剪。
    """
    try:
        from tkinter import font as _tkfont
        # Python 3.9 的 nametofont 不接受 root=; Font(exists=True) 等价且跨版本。
        _f = _tkfont.Font(root=root, name=font or FONT_BUTTON, exists=True)
        text_h = int(_f.metrics("linespace"))
    except Exception:
        text_h = 16
    return max(12, text_h + 2 * _MB_BTN_PADY + 2 * _MB_BTN_BD)


def _mb_content_width():
    """当前布局下所有可见子控件的合计需求宽度 (防横向裁剪)。"""
    if _mini_bar is None:
        return 0
    try:
        _mini_bar.update_idletasks()
        return int(_mini_bar.winfo_reqwidth())
    except Exception:
        return 0


def _mb_effective_height():
    """窗口高度: 配置值作为下限, 内容需求(含强调线)超出时自适应抬高。

    实测 config=32 / UI_SCALE=1.1 时内容只需 31px, 故仍取 32 (不无故变矮);
    但若 UI_SCALE 调大导致内容需求 > 32, 则自动变高, 避免出现
    「窗口 32px 而内容 reqheight 39px 被裁 7px」的情况。
    """
    try:
        h_cfg = int(getattr(state, "MINI_BAR_HEIGHT", 28) or 28)
    except Exception:
        h_cfg = 28
    return max(h_cfg, _mb_content_height() + _MB_ACCENT)


def _mb_target_size():
    """按三形态计算 Mini Bar 目标尺寸 (w, h)。

    高度统一为「内容自适应高度」_mb_effective_height(): 三形态等高, 状态点始终
    在内容区垂直居中 —— 旧实现运行/图标态给 h+6, 而 _mb_animate_width 每帧又用
    winfo_height() 把高回填成旧值, 导致 h+6 从未生效、状态点坐标却按 h+6 计算
    (实测偏移 7px), 这里一并消除。
    图标态: 正方形, 边长 = 条高; 紧凑态: 宽 = 配置宽; 运行/录制态: 宽 = 配置宽 + 运行余量。
    """
    w = int(state.MINI_BAR_WIDTH)
    h = _mb_effective_height()
    if state.recording or state.running:
        return (w + _MB_RUN_EXTRA, h)
    if time.time() - _mb_last_interact >= _MB_IDLE_SEC:
        return (h, h)
    return (w, h)


def _mb_clamp_pos(x, y, w, h):
    """把坐标夹取到当前屏幕范围内 (换分辨率后 Mini Bar 不跑到屏幕外)。"""
    try:
        sw = root.winfo_screenwidth()
        sh = root.winfo_screenheight()
    except Exception:
        return x, y
    x = max(0, min(int(x), max(0, sw - int(w))))
    y = max(0, min(int(y), max(0, sh - int(h))))
    return x, y


def _mb_save_pos():
    """记录 Mini Bar 当前位置到 config (拖动结束/关闭时持久化)。"""
    if _mini_bar is None:
        return
    try:
        pos = "{}+{}".format(max(0, _mini_bar.winfo_x()),
                             max(0, _mini_bar.winfo_y()))
        if getattr(state, "MINI_BAR_POS", "") != pos:
            state.MINI_BAR_POS = pos
            state.save_config()
    except Exception:
        pass


def _mb_set_size(w, h):
    """只设置尺寸不移动窗口 (wm geometry 仅给 WxH 时保留当前位置)。"""
    try:
        if _mini_bar is not None:
            _mini_bar.geometry("{}x{}".format(int(w), int(h)))
    except Exception:
        pass


def _mb_animate_width(target, height=None, steps=8, delay=12):
    """宽度平滑过渡: root.after 链逐帧插值, 未到位才继续下一帧。

    高度由调用方显式传入 (height): 旧实现每帧用 _mini_bar.winfo_height() 回填
    高度, 而 Tk 的 geometry 变更要到 idle 才生效, 于是把 _mb_apply_form 刚设好的
    目标高度覆盖回「上一个形态的旧高度」—— 实测运行/图标态因此永远停在 32px,
    而状态点坐标却按 38px 计算。此处改为显式传递, 并保证「差距 < 4px」时也同步高度。
    动画 id 存于 _mb_anim_id, 由 _destroy_mini_bar 统一 after_cancel。
    """
    global _mb_anim_id
    if _mini_bar is None:
        return
    if _mb_anim_id is not None:
        try:
            root.after_cancel(_mb_anim_id)
        except Exception:
            pass
        _mb_anim_id = None
    try:
        cur = int(_mini_bar.winfo_width())
    except Exception:
        return
    try:
        h_now = int(height) if height else int(_mini_bar.winfo_height())
    except Exception:
        h_now = int(_mb_effective_height())
    if abs(cur - target) < 4:
        _mb_set_size(target, h_now)   # 宽度无需动画, 但要立即同步高度
        return
    step = max(1, int(abs(target - cur) / max(1, steps)))

    def _tick(val):
        global _mb_anim_id
        if _mini_bar is None:
            _mb_anim_id = None
            return
        nxt = val + step if target > val else val - step
        if (target > val and nxt >= target) or (target < val and nxt <= target):
            nxt = target
        _mb_set_size(nxt, h_now)
        _mb_anim_id = None if nxt == target else root.after(
            delay, lambda: _tick(nxt))

    _tick(cur)


def _mb_relayout(form):
    """按形态重新 pack 子控件: 图标态只留状态点与展开按钮。

    关键: info/prog 也要一并 forget。旧实现只 forget 基础布局控件, 而 info/prog
    已按 `before=sep1` 挂在最前面, 重排基础控件后它们仍占据队首 pack 顺序
    (实测运行态顺序变成 info/prog/icon/dot/status/... 且总需求宽 768px > 窗口
    560px, 尾部「暂停/单步/展开」被挤出而不显示)。现按形态统一重建顺序。
    """
    if _mini_bar is None:
        return
    wd = _mini_bar._widgets
    for _n, wdg, _o, _f in _mini_bar._mb_layout:
        try:
            wdg.pack_forget()
        except Exception:
            pass
    for key in ("info", "prog"):
        try:
            wd[key].pack_forget()
        except Exception:
            pass
    for _n, wdg, opts, forms in _mini_bar._mb_layout:
        if form in forms:
            try:
                wdg.pack(**opts)
            except Exception:
                pass
    # 运行信息/进度条只进运行态, 且必须紧跟状态文字、排在整个操作区之前 (before=sep1)
    if form == "run" and str(wd["info"].cget("text") or ""):
        try:
            wd["info"].pack(side="left", padx=(0, 4), before=wd["sep1"])
            wd["prog"].pack(side="left", padx=(0, 4), before=wd["sep1"])
        except Exception:
            pass


def _mb_dot_color():
    """状态点基础颜色 (实时取主题色, 不缓存颜色值)。"""
    if state.recording:
        return C["dg"]
    if state.running:
        return C["wn"] if not state.pause_event.is_set() else C["sc"]
    return C["fgm"]


def _mb_set_dot(color):
    """设置 Canvas 状态点填充/描边颜色。"""
    if _mini_bar is None:
        return
    try:
        dot = _mini_bar._widgets["dot"]
        dot.itemconfig(_mini_bar._widgets["dot_item"], fill=color, outline=color)
    except Exception:
        pass


def _mb_breathe_tick():
    """暂停呼吸动画 (800ms 周期): 每 400ms 翻转一次亮度。"""
    global _mb_breathe_id, _mb_breathe_phase
    if _mini_bar is None or not (state.running
                                 and not state.pause_event.is_set()
                                 and not state.recording):
        _mb_breathe_id = None
        return
    _mb_breathe_phase ^= 1
    base = C["wn"]
    _mb_set_dot(base if _mb_breathe_phase else _darken(base))
    _mb_breathe_id = root.after(400, _mb_breathe_tick)


def _mb_blink_tick():
    """告警闪烁: 红/基色交替, 共闪 3 次 (6 帧)。"""
    global _mb_blink_id, _mb_blink_count
    if _mini_bar is None or _mb_blink_count <= 0:
        _mb_blink_id = None
        _mb_set_dot(_mb_dot_color())
        return
    _mb_blink_count -= 1
    _mb_set_dot(C["dg"] if (_mb_blink_count % 2) else _mb_dot_color())
    _mb_blink_id = root.after(160, _mb_blink_tick)


def _mb_flash_alert():
    """出错/告警 → 状态点闪烁 3 次 (id 可被 _destroy_mini_bar 取消)。"""
    global _mb_blink_id, _mb_blink_count
    if _mini_bar is None:
        return
    if _mb_blink_id is not None:
        try:
            root.after_cancel(_mb_blink_id)
        except Exception:
            pass
    _mb_blink_count = 6
    _mb_blink_tick()


def _mb_start_dot_anim():
    """按运行状态启停呼吸动画; 非暂停态直接用基础色。"""
    global _mb_breathe_id, _mb_breathe_phase
    if _mini_bar is None:
        return
    paused = state.running and not state.pause_event.is_set() and not state.recording
    if paused:
        if _mb_breathe_id is None:
            _mb_breathe_phase = 0
            _mb_breathe_tick()
    else:
        if _mb_breathe_id is not None:
            try:
                root.after_cancel(_mb_breathe_id)
            except Exception:
                pass
            _mb_breathe_id = None
        if _mb_blink_id is None:   # 闪烁进行中不覆盖其颜色
            _mb_set_dot(_mb_dot_color())


def _mb_schedule_idle_check():
    """每秒检查一次空闲时长 (超过 _MB_IDLE_SEC 收起为图标态)。"""
    global _mb_idle_id
    if _mb_idle_id is not None or _mini_bar is None:
        return
    _mb_idle_id = root.after(1000, _mb_idle_check)


def _mb_idle_check():
    global _mb_idle_id
    _mb_idle_id = None
    if _mini_bar is None:
        return
    _mb_apply_form()


def _mb_apply_form(force=False):
    """计算并切换到目标形态 (含自适应尺寸、状态点对齐、防裁剪修正)。"""
    global _mb_form
    if _mini_bar is None:
        return
    if state.recording or state.running:
        form = "run"
    elif time.time() - _mb_last_interact >= _MB_IDLE_SEC:
        form = "icon"
    else:
        form = "compact"
    if not force and form == _mb_form:
        _mb_schedule_idle_check()
        return
    _mb_form = form
    _mb_relayout(form)
    tw, th = _mb_target_size()
    # 高度自适应: 内容(字体随 UI_SCALE 变化)所需高度超过配置时抬高窗口, 防裁剪
    th = max(th, _mb_content_height() + _MB_ACCENT)
    # 宽度自适应: 按挂载后的实测需求宽二次放大。
    # 图标态也参与 —— 旧实现固定 h+6 的正方形 (实测 38x32) 装不下「状态点+展开」,
    # 展开按钮被挤到 7px 宽 (文字被裁)。改为以正方形边长为下限、按内容贴合。
    try:
        need_w = _mb_content_width()
        if need_w > tw:
            tw = need_w
    except Exception:
        pass
    try:
        _mb_set_size(_mini_bar.winfo_width() or tw, th)   # 先按当前宽定高
        ih = _mb_inner_h(th)                              # 内容区高 (扣除强调线)
        dot = _mini_bar._widgets["dot"]
        dot.configure(height=ih)
        dot.coords(_mini_bar._widgets["dot_item"], 2, ih // 2 - _MB_DOT_H // 2,
                   2 + _MB_DOT_H, ih // 2 + _MB_DOT_H // 2)
    except Exception:
        pass
    _mb_animate_width(tw, th)
    _mb_start_dot_anim()
    _mb_schedule_idle_check()


def _mb_touch():
    """记录用户交互: 重置空闲计时并回到紧凑态/运行态。"""
    global _mb_last_interact
    _mb_last_interact = time.time()
    _mb_apply_form()


def _mb_bind_hover(widget):
    """给 Mini Bar 按钮绑定悬停/离开视觉反馈。

    高亮基准在「进入时」实时读取控件当前 bg (而非创建时的字面量), 因此
    _sync_mini_bar_status 在主题切换/状态变化后改写的颜色也能被正确高亮。
    """
    def _on_enter(_e, w=widget):
        try:
            w._mb_base_bg = w.cget("bg")
            w.configure(bg=_darken(w._mb_base_bg))
        except Exception:
            pass

    def _on_leave(_e, w=widget):
        try:
            w.configure(bg=getattr(w, "_mb_base_bg", None) or w.cget("bg"))
        except Exception:
            pass

    try:
        widget.bind("<Enter>", _on_enter)
        widget.bind("<Leave>", _on_leave)
    except Exception:
        pass


def _create_mini_bar():
    """创建 Mini Bar 悬浮条（折叠后的微型状态栏）。

    Mini Bar 三形态 (高度统一 = 内容自适应高度 _mb_effective_height):
      图标态: 边长以条高为下限, 按「状态点+展开」贴合 (空闲 30s 自动收起)
      紧凑态: 宽 = 配置宽 (内容更宽时自动放宽), 高 = 条高
      运行态: 宽 = 配置宽 + 运行余量 (并按 info/prog 实测需求宽放宽), 高 = 条高
    尺寸 = 配置 (MINI_BAR_WIDTH / MINI_BAR_HEIGHT) 与「内容实测需求」取较大者,
    使内容永不超出窗口 (消除裁剪/挤压造成的视觉空白)。
    """
    global _mini_bar, _mb_form, _mb_last_interact
    if _mini_bar is not None:
        return

    _mb_last_interact = time.time()
    _mb_form = "compact"

    mb = tkinter.Toplevel(root)
    _set_window_icon(mb)
    mb.overrideredirect(True)
    mb.attributes("-topmost", True)
    mb.configure(bg=C["bgc"])
    # 透明度来自配置 (旧设置项是安慰剂, 现在真正生效)
    try:
        mb.attributes("-alpha", max(0.3, min(1.0, int(state.MINI_BAR_OPACITY) / 100.0)))
    except Exception:
        pass

    # 初始位置: 优先恢复记忆位置, 并按当前屏幕范围夹取 (换分辨率后不越界)
    mb_w, mb_h = _mb_target_size()
    px = py = None
    m = re.match(r"^([+-]?\d+)([+-]\d+)$", str(getattr(state, "MINI_BAR_POS", "") or ""))
    if m:
        px, py = int(m.group(1)), int(m.group(2))
    if px is None:
        rx, ry = root.winfo_x(), root.winfo_y()
        px, py = max(0, rx), max(0, ry - mb_h - 4)
    px, py = _mb_clamp_pos(px, py, mb_w, mb_h)
    mb.geometry("{}x{}+{}+{}".format(mb_w, mb_h, px, py))

    # 外框: 顶部 _MB_ACCENT 像素强调色描边。
    # 内容区高度 = 条高 - _MB_ACCENT, 由 _mb_inner_h() 统一扣除, 并由 _mb_apply_form
    # 同步到 Canvas/dot 坐标 —— 旧实现 Canvas 高度直接写整条高 (32), 使子控件需求高
    # 33 > 窗口 32 而被裁 1px, 状态点也因坐标基准不同而下移。
    outer = tkinter.Frame(mb, bg=C["ac"], bd=0, height=_MB_ACCENT)
    outer.pack(side="top", fill="x")
    inner = tkinter.Frame(mb, bg=C["bgc"], bd=0)
    inner.pack(fill="both", expand=True)

    # ── 应用图标 (fill="y" 铺满内容区高度; 尺寸由 _mb_set_icon 随内容区高自适应) ──
    mb_icon_label = tkinter.Label(inner, text="", bg=C["bgc"],
        cursor="hand2", anchor="center", bd=0)
    _mb_set_icon(mb_icon_label)

    # ── 状态圆点 (Canvas 高 = 内容区高, 圆点在内容区垂直居中) ──
    _mb_ih0 = _mb_inner_h(mb_h)
    mb_dot = tkinter.Canvas(inner, width=14, height=_mb_ih0, bg=C["bgc"],
        highlightthickness=0, bd=0, cursor="hand2")
    dot_item = mb_dot.create_oval(2, _mb_ih0 // 2 - _MB_DOT_H // 2,
        2 + _MB_DOT_H, _mb_ih0 // 2 + _MB_DOT_H // 2,
        fill=C["fgm"], outline=C["fgm"])

    # ── 状态文字 + 运行信息 (fill="y" 让底色铺满, 文字仍居中) ──
    mb_status = tkinter.Label(inner, text="就绪", bd=0,
        font=FONT_BUTTON, fg=C["fgm"], bg=C["bgc"], anchor="center")
    mb_info = tkinter.Label(inner, text="", bd=0,
        font=FONT_LOG, fg=C["fgm"], bg=C["bgc"], anchor="center")

    # ── 进度条 (仅运行态显示) ──
    mb_prog = ttk.Progressbar(inner, length=104, mode="determinate",
        style="Success.Horizontal.TProgressbar")

    # ── 分隔符 ──
    mb_sep1 = tkinter.Frame(inner, bg=C["bd"], width=1)

    # ── 操作按钮 (统一字号/内边距/描边; fill="y" 纵向铺满内容区 → 上下留白 0) ──
    def _mb_btn(text, color, cmd):
        def _invoke():
            _mb_touch()
            cmd()
        btn = tkinter.Label(inner, text=text, font=FONT_BUTTON,
            fg="white", bg=color, padx=7, pady=_MB_BTN_PADY, anchor="center",
            activebackground=_darken(color), activeforeground="white",
            cursor="hand2", relief="flat", bd=_MB_BTN_BD)
        btn.bind("<Button-1>", lambda e, c=_invoke: c())
        _mb_bind_hover(btn)
        return btn

    mb_stop = _mb_btn("■ 停止", C["dg"], stop_execution)
    mb_run = _mb_btn("▶ 运行", C["sc"], main_run)
    mb_pause = _mb_btn("⏸ 暂停", C["wn"], toggle_pause)
    mb_step = _mb_btn("⏭", C["ac"], _step_once)

    # ── 展开按钮 (与操作按钮同规格: padx/pady/bd/raised) ──
    # 底色用浅强调色 (acl) 而非条底色: 旧实现是白底, 实测最右端 ~66px 呈「空白」观感
    mb_expand = tkinter.Label(inner, text="□ 展开",
        font=FONT_BUTTON, fg=C["ac"], bg=C["acl"],
        padx=7, pady=_MB_BTN_PADY, cursor="hand2", anchor="center",
        activebackground=C["ac"], activeforeground="white",
        relief="flat", bd=_MB_BTN_BD)
    _mb_bind_hover(mb_expand)

    def _expand(event=None):
        _mb_touch()
        _toggle_fold()
    mb_expand.bind("<Button-1>", _expand)

    # ── 布局表: (名字, 控件, pack 选项, 可见形态集合) ──
    # 所有组件均 fill="y" 纵向铺满内容区高度 (文字/图形仍居中), 保证上下留白为 0。
    mb._mb_layout = [
        ("icon",   mb_icon_label, {"side": "left", "fill": "y", "padx": (7, 3)}, {"compact", "run"}),
        ("dot",    mb_dot,        {"side": "left", "padx": (2, 2)}, {"icon", "compact", "run"}),
        ("status", mb_status,     {"side": "left", "fill": "y", "padx": (0, 4)}, {"compact", "run"}),
        ("sep1",   mb_sep1,       {"side": "left", "fill": "y", "padx": 3, "pady": 4}, {"compact", "run"}),
        ("stop",   mb_stop,       {"side": "left", "fill": "y", "padx": 1}, {"compact", "run"}),
        ("run",    mb_run,        {"side": "left", "fill": "y", "padx": 1}, {"compact", "run"}),
        ("pause",  mb_pause,      {"side": "left", "fill": "y", "padx": 1}, {"compact", "run"}),
        ("step",   mb_step,       {"side": "left", "fill": "y", "padx": 1}, {"compact", "run"}),
        ("expand", mb_expand,     {"side": "right", "fill": "y", "padx": (1, 6)}, {"icon", "compact", "run"}),
    ]

    # ── 拖拽支持 ──
    _drag_data = {"x": 0, "y": 0}

    def _drag_start(event):
        _mb_touch()
        _drag_data["x"] = event.x_root - mb.winfo_x()
        _drag_data["y"] = event.y_root - mb.winfo_y()

    def _drag_move(event):
        mb.geometry("+{}+{}".format(
            event.x_root - _drag_data["x"],
            event.y_root - _drag_data["y"]))

    def _drag_end(event):
        _mb_save_pos()   # 拖动结束记位, 下次打开恢复

    mb.bind("<Button-1>", _drag_start)
    mb.bind("<B1-Motion>", _drag_move)
    mb.bind("<ButtonRelease-1>", _drag_end)
    inner.bind("<Button-1>", _drag_start)
    inner.bind("<B1-Motion>", _drag_move)
    inner.bind("<ButtonRelease-1>", _drag_end)
    for wdg in (mb_status, mb_info, mb_icon_label):
        wdg.bind("<Button-1>", _drag_start)
        wdg.bind("<B1-Motion>", _drag_move)
        wdg.bind("<ButtonRelease-1>", _drag_end)
    mb_dot.bind("<B1-Motion>", _drag_move)
    mb_dot.bind("<ButtonRelease-1>", _drag_end)
    # 鼠标进入 → 重置空闲计时并回到紧凑/运行态
    mb.bind("<Enter>", lambda e: _mb_touch())
    inner.bind("<Enter>", lambda e: _mb_touch())

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
        "icon": mb_icon_label, "dot": mb_dot, "dot_item": dot_item,
        "status": mb_status, "info": mb_info, "prog": mb_prog,
        "sep1": mb_sep1, "stop": mb_stop, "run": mb_run,
        "pause": mb_pause, "step": mb_step,
        "expand": mb_expand, "inner": inner, "outer": outer,
    }
    _mini_bar = mb
    _mb_relayout(_mb_form)      # 初始布局; 之后由 _sync / _mb_apply_form 接管
    _sync_mini_bar_status()
    _mb_schedule_idle_check()


def _destroy_mini_bar():
    """销毁 Mini Bar，并取消全部 after 定时器。

    重要: 必须在这里 after_cancel 掉宽度动画 / 空闲计时 / 呼吸 / 闪烁的
    after id, 否则反复折叠/展开会残留定时器 (回调触碰已销毁窗口抛异常)。
    """
    global _mini_bar, _mb_anim_id, _mb_idle_id, _mb_blink_id, _mb_breathe_id
    _mb_save_pos()
    for aid in (_mb_anim_id, _mb_idle_id, _mb_blink_id, _mb_breathe_id):
        if aid is not None:
            try:
                root.after_cancel(aid)
            except Exception:
                pass
    _mb_anim_id = _mb_idle_id = _mb_blink_id = _mb_breathe_id = None
    if _mini_bar is not None:
        try:
            _mini_bar.destroy()
        except Exception:
            pass
        _mini_bar = None


def _sync_mini_bar_status():
    """同步 Mini Bar 状态/信息/主题色；先算签名再比对，无变化直接返回。

    签名必须包含主题色: 主题切换时 utils.C / 本模块 C 被重新绑定, 若签名
    不含颜色会被误判为「无变化」而跳过刷新, 导致暗黑模式下 Mini Bar 配色
    不更新。颜色一律实时取 C[...] (绝不缓存 C 或 C[...] 到实例属性)。
    """
    global _mb_prev_failed
    if _mini_bar is None:
        return
    w = _mini_bar._widgets
    try:
        rows = int(state.exec_state.get("total_rows", 0) or 0)
    except Exception:
        rows = 0
    el = 0
    if state.running and rows > 0:
        try:
            el = int(time.time() - state.exec_state.get("start_time", time.time()))
        except Exception:
            el = 0
    failed = bool(getattr(engine, "_script_failed", False))
    # 主题同步元组须覆盖 Mini Bar 实际用到的全部主题色 (含 ac/bd/acl),
    # 否则仅这些颜色变化时签名不变 → 误判「无变化」而跳过刷新。
    theme = (C["bgc"], C["sc"], C["fgm"], C["dg"], C["wn"],
             C["ac"], C["bd"], C["acl"])
    # 配置尺寸纳入签名: 设置页改宽/高/透明度后, 打开中的 Mini Bar 也要即时生效
    cfg = (int(state.MINI_BAR_WIDTH), int(state.MINI_BAR_HEIGHT),
           int(state.MINI_BAR_OPACITY))
    sig = (state.running, state.pause_event.is_set(), state.recording,
           rows, state.exec_state.get("loop"), state.exec_state.get("row"),
           el, failed, _mb_form, theme, cfg)
    if getattr(_mini_bar, "_sync_sig", None) == sig:
        return
    _mini_bar._sync_sig = sig
    try:
        if getattr(_mini_bar, "_cfg", None) != cfg:
            _mini_bar._cfg = cfg
            try:
                _mini_bar.attributes("-alpha", max(0.3, min(1.0, cfg[2] / 100.0)))
            except Exception:
                pass
            _mb_apply_form(force=True)   # 尺寸变了 → 强制重算三形态尺寸
        # 背景色 (主题切换时; outer 为顶部 1px 强调色描边)
        w["outer"].configure(bg=C["ac"])
        w["inner"].configure(bg=C["bgc"])
        w["dot"].configure(bg=C["bgc"])
        w["expand"].configure(bg=C["acl"], fg=C["ac"], activebackground=C["ac"])
        w["status"].configure(bg=C["bgc"])
        w["info"].configure(bg=C["bgc"])
        # D6: 前景也随主题刷新 (Unicode 回退 "⚙" 用 C["ac"]; PNG 图标时无副作用)
        w["icon"].configure(bg=C["bgc"], fg=C["ac"])
        w["sep1"].configure(bg=C["bd"])

        # ── 状态文字 ──
        if state.recording:
            w["status"].configure(text="录制中", fg=C["dg"])
        elif state.running:
            if not state.pause_event.is_set():
                w["status"].configure(text="已暂停", fg=C["wn"])
            else:
                w["status"].configure(text="运行中", fg=C["sc"])
        else:
            w["status"].configure(text="就绪", fg=C["fgm"])

        # ── 运行信息 + 进度条 (仅运行态且有数据时挂载) ──
        if state.running and rows > 0:
            lp = state.exec_state["loop"]
            tl = state.exec_state["total_loops"]
            rw = state.exec_state["row"]
            tr = rows
            pct = max(0, min(100, int(rw / max(1, tr) * 100)))
            if el >= 3600:
                el_str = "{:02d}:{:02d}:{:02d}".format(
                    int(el // 3600), int((el % 3600) // 60), int(el % 60))
            else:
                el_str = "{:02d}:{:02d}".format(int(el // 60), int(el % 60))
            eta_str = _fmt_dur(el / float(rw) * max(0, tr - rw)) if rw > 0 else "--:--"
            w["info"].configure(
                text="循环{}/{}  行{}/{}  {}  ETA {}".format(
                    lp, "∞" if tl > 99999 else tl, rw, tr, el_str, eta_str), fg=C["fgm"])
            if not w["info"].winfo_ismapped():
                w["info"].pack(side="left", padx=(0, 4), before=w["sep1"])
            if not w["prog"].winfo_ismapped():
                w["prog"].pack(side="left", padx=(0, 4), before=w["sep1"])
            w["prog"].configure(value=pct)
        else:
            for key in ("info", "prog"):
                if w[key].winfo_ismapped():
                    w[key].pack_forget()

        # ── 暂停按钮动态文本 ──
        if state.running and not state.pause_event.is_set():
            w["pause"].configure(text="▶ 继续", bg=C["ac"],
                activebackground=_darken(C["ac"]))
        else:
            w["pause"].configure(text="⏸ 暂停", bg=C["wn"],
                activebackground=_darken(C["wn"]))

        # ── 按钮颜色 ──
        w["stop"].configure(bg=C["dg"], activebackground=_darken(C["dg"]))
        w["run"].configure(bg=C["sc"], activebackground=_darken(C["sc"]))
        w["step"].configure(bg=C["ac"], activebackground=_darken(C["ac"]))

        # ── 悬停基色缓存同步: _on_leave 回填 _mb_base_bg, 主题/状态变更后必须
        # 一并刷新, 否则「悬停中切主题」→ 离开时回填旧主题基色 (陈旧色)。 ──
        for _bk in ("stop", "run", "pause", "step", "expand"):
            try:
                w[_bk]._mb_base_bg = w[_bk].cget("bg")
            except Exception:
                pass

        # ── 失败沿 → 闪烁告警; 形态切换(运行态自动展开); 状态点动画 ──
        if failed and not _mb_prev_failed:
            _mb_flash_alert()
        _mb_apply_form()
        _mb_start_dot_anim()
    except Exception:
        pass  # Mini Bar 可能已被销毁
    _mb_prev_failed = failed


