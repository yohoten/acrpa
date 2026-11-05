# -*- coding: utf-8 -*-
"""ui.widgets.rounded_button — Canvas 自绘圆角按钮/开关 (P3-1, 借鉴 maliang 自绘控件)。

背景: ttk 按钮是「扁平 + 1px 描边」, 无法圆角/光晕, 顶栏开关、运行按钮等关键交互
缺自绘反馈。本模块提供 Canvas 自绘的圆角控件, 复用既有动效与颜色基建:

  · core.color.state_color / transition / text_on —— 状态色派生与颜色过渡;
  · utils.motion / motion_frame / reduce_motion —— 时长/帧间隔/减少动效 token;
  · core.animation.Animation —— hover/press 光晕与开关滑块位移动画 (reduce_motion 一步到位)。

设计约束 (P3-1):
  · 只做「新组件试点」, 不替换全局 ttk 按钮 (调用方显式 import)。
  · 不 import ACRPA; 主题色经构造注入或 refresh() 重读 utils.C, 换肤由宿主窗口
    walk 驱动 (识别 ``_acrpa_redraw`` 钩子), **不订阅全局 ThemeBus** → 无订阅残留。
  · 颜色/尺寸全走 token (utils.C / sp / ctrl_h / radius), 不写裸像素、不写 #RRGGBB。
"""

import tkinter

import utils
from core.animation import Animation, out_cubic
from core.color import state_color, transition, text_on

__all__ = ["rounded_polygon", "RoundedButton", "RoundedSwitch"]


def rounded_polygon(x1, y1, x2, y2, r):
    """圆角矩形 polygon 顶点序列 (供 ``create_polygon(smooth=True)``)。

    返回含重复顶点的坐标序列, smooth=True 时四角平滑为半径 ``r`` 的圆弧。
    纯函数 (无 Tk 依赖), RoundedButton/RoundedSwitch 与自测共用。
    """
    r = max(0.0, min(float(r), (float(x2) - x1) / 2.0, (float(y2) - y1) / 2.0))
    return [
        x1 + r, y1,
        x1 + r, y1,
        x2 - r, y1,
        x2 - r, y1,
        x2, y1,
        x2, y1 + r,
        x2, y1 + r,
        x2, y2 - r,
        x2, y2 - r,
        x2, y2,
        x2 - r, y2,
        x2 - r, y2,
        x1 + r, y2,
        x1 + r, y2,
        x1, y2,
        x1, y2 - r,
        x1, y2 - r,
        x1, y1 + r,
        x1, y1 + r,
        x1, y1,
    ]


class _RoundedCanvas(tkinter.Canvas):
    """自绘圆角控件基类: 色板读取 + 换肤钩子 (宿主 walk 识别 ``_acrpa_redraw``)。"""

    def __init__(self, parent, colors=None, **kwargs):
        self._palette = colors if isinstance(colors, dict) else None
        kwargs.setdefault("highlightthickness", 0)
        kwargs.setdefault("bd", 0)
        kwargs.setdefault("takefocus", True)
        kwargs.setdefault("cursor", "hand2")
        super().__init__(parent, **kwargs)

    # ── 色板 ──
    def _colors(self):
        """当前色板: 优先构造注入, 否则实时读 utils.C (apply_theme 会重绑)。"""
        if isinstance(self._palette, dict) and self._palette:
            return self._palette
        try:
            return utils.C or {}
        except Exception:
            return {}

    def _bg(self):
        """canvas 底色 = 父容器底色 (圆角外四角与父容器融合)。"""
        C = self._colors()
        try:
            return self.master.cget("bg") or C.get("bgc", C.get("bg", "white"))
        except Exception:
            return C.get("bgc", C.get("bg", "white"))

    # ── 换肤 ──
    def refresh(self, colors=None):
        """换肤: 重读色板并重绘 (不改 value/文本等业务状态)。"""
        if isinstance(colors, dict):
            self._palette = colors
        self.configure(bg=self._bg())
        self._redraw()
        return self

    def _acrpa_redraw(self, colors=None):
        """宿主 walk 识别钩子 (settings_window.refresh_theme): 委托 refresh。"""
        self.refresh(colors)

    def _redraw(self):
        raise NotImplementedError


class RoundedButton(_RoundedCanvas):
    """自绘圆角按钮: hover/press 填充色渐变 (state_color 派生), reduce_motion 一步到位。

    Args:
        role: 语义色键 ("ac"/"sc"/"dg"/"wn" 等); None 走中性底 surface_alt。
        width/height: 设计像素 (走 utils.sp 缩放); width=None 时按文本宽自适应。
    """

    def __init__(self, parent, text="", command=None, *, colors=None, role=None,
                 width=None, height=None, radius=None, font=None,
                 state="normal", tooltip=None):
        self._text = str(text)
        self._command = command
        self._role = role
        self._state = state
        self._font = font or utils.FONT_BUTTON
        self._width = width
        self._height = height or utils.ctrl_h("ctrl_h")
        self._radius = radius if radius is not None else utils.radius("radius")
        self._hover = False
        self._pressed = False
        self._anim = None
        self._shape = None
        super().__init__(parent, colors=colors)
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        self.bind("<ButtonPress-1>", lambda e: self._set_pressed(True))
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Key-space>", lambda e: self._activate())
        self.bind("<Key-Return>", lambda e: self._activate())
        self.bind("<FocusIn>", lambda e: self._redraw())
        self.bind("<FocusOut>", lambda e: self._redraw())
        self._redraw()
        if tooltip:
            try:
                utils.attach_tooltip(self, tooltip)
            except Exception:
                pass

    # ── 颜色派生 ──
    def _base(self):
        C = self._colors()
        if self._role and self._role in C:
            return C[self._role]
        return C.get("surface_alt", C.get("bgc", "white"))

    def _fg(self, base):
        C = self._colors()
        if self._role and self._role in C:
            return text_on(base)
        return C.get("fgb", C.get("fgt", "black"))

    def _target_fill(self):
        if self._state == "disabled":
            return state_color(self._base(), "disabled", bg=self._bg())
        if self._pressed:
            return state_color(self._base(), "active")
        if self._hover:
            return state_color(self._base(), "hover")
        return self._base()

    # ── 交互 ──
    def _set_hover(self, on):
        if on == self._hover or self._state == "disabled":
            return
        self._hover = on
        self._animate_fill()

    def _set_pressed(self, on):
        if on == self._pressed or self._state == "disabled":
            return
        self._pressed = on
        self._animate_fill()

    def _on_release(self, _event=None):
        was = self._pressed
        self._pressed = False
        if was and self._state != "disabled":
            self._activate()
        else:
            self._animate_fill()

    def _activate(self):
        if self._state == "disabled":
            return
        self._pressed = False
        if self._command is not None:
            self._command()

    # ── 动画 ──
    def _animate_fill(self):
        if self._anim is not None:
            try:
                self._anim.stop()
            except Exception:
                pass
            self._anim = None
        start = self._fill
        target = self._target_fill()
        if not start or start == target or utils.reduce_motion():
            self._set_fill(target)
            return

        def _apply(p):
            self._set_fill(transition(start, target, p))

        self._anim = Animation(
            utils.motion("motion_fast"), _apply, controller=out_cubic,
            fps=1000 // utils.motion_frame(), root=self,
            reduce_motion=utils.reduce_motion)
        self._anim.start()

    def _set_fill(self, color):
        self._fill = color
        try:
            self.itemconfigure(self._shape, fill=color)
            for item in self.find_withtag("text"):
                self.itemconfigure(item, fill=self._fg(color))
        except Exception:
            pass

    def _redraw(self):
        self.delete("all")
        w = self._width
        h = self._height
        if w is None:
            pad_x = utils.sp("sp_lg")
            try:
                tw = tkinter.font.Font(font=self._font).measure(self._text)
            except Exception:
                tw = 0
            w = max(int(tw) + pad_x * 2, self._height)
        r = self._radius
        C = self._colors()
        focused = self.focus_get() is self
        outline = C.get("focus", C.get("ac", "dodgerblue")) if focused else C.get(
            "bd", C.get("border_strong", "gray85"))
        self._fill = self._target_fill()
        self._shape = self.create_polygon(
            rounded_polygon(1, 1, w - 1, h - 1, r),
            fill=self._fill, outline=outline,
            width=utils.sp("focus_w") if focused else 1, smooth=True)
        self.create_text(w / 2.0, h / 2.0, text=self._text,
                         font=self._font, fill=self._fg(self._fill), tags="text")
        self.configure(width=w, height=h)


class RoundedSwitch(_RoundedCanvas):
    """自绘圆角开关 (iOS 风格 toggle): 滑块位移 + 轨道色渐变, reduce_motion 一步到位。

    Args:
        variable: 可选 tkinter.BooleanVar; 点击时写入并调用 command。
        command: 用户切换后回调 (读 variable 取新值)。
        on/off: 轨道开/关填充色键 (默认 "sc" / "bd")。
    """

    def __init__(self, parent, *, variable=None, command=None, colors=None,
                 value=None, on="sc", off="bd", height=None, pad=None):
        self._variable = variable
        self._command = command
        self._on_key = on
        self._off_key = off
        self._pad = pad if pad is not None else utils.sp("gap_tight")
        h = height if height is not None else utils.ctrl_h("ctrl_h_sm")
        # 命名避开 tkinter.BaseWidget 内部 `_w` (Tk 路径名) —— 否则超类 __init__ 覆盖。
        self._th = max(1, int(h))
        self._tw = max(1, int(self._th * 1.8))
        self._value = bool(self._read_var() if value is None else value)
        self._anim = None
        self._track = None
        self._thumb_item = None
        self._hover = False
        self._fill = None
        super().__init__(parent, colors=colors)
        self.bind("<Button-1>", lambda e: self.toggle())
        self.bind("<Key-space>", lambda e: self.toggle())
        self.bind("<Key-Return>", lambda e: self.toggle())
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        self.bind("<FocusIn>", lambda e: self._redraw())
        self.bind("<FocusOut>", lambda e: self._redraw())
        self._redraw()

    # ── 读取 ──
    def _read_var(self):
        try:
            if self._variable is not None:
                return bool(self._variable.get())
        except Exception:
            pass
        return False

    def get(self):
        return self._value

    def set(self, value, animate=True):
        """程序化设置 (写 variable, 不触发 command)。"""
        value = bool(value)
        if value == self._value:
            return self
        self._value = value
        self._write_var(value)
        self._animate(animate)
        return self

    def toggle(self):
        """用户切换 (点击/键盘): 写 variable + 回调 command。"""
        self._value = not self._value
        self._write_var(self._value)
        self._animate(True)
        if self._command is not None:
            self._command()
        return self._value

    def _write_var(self, value):
        try:
            if self._variable is not None:
                self._variable.set(bool(value))
        except Exception:
            pass

    # ── 颜色派生 ──
    def _track_on(self):
        C = self._colors()
        return C.get(self._on_key, C.get("sc", "green"))

    def _track_off(self):
        C = self._colors()
        return C.get(self._off_key, C.get("bd", "gray85"))

    def _thumb_color(self):
        C = self._colors()
        return C.get("bgc", C.get("bg", "white"))

    def _thumb_border_color(self):
        C = self._colors()
        return C.get("bd", C.get("border_strong", "gray85"))

    def _track_fill(self):
        base = self._track_on() if self._value else self._track_off()
        return state_color(base, "hover") if self._hover else base

    def _set_hover(self, on):
        if on == self._hover:
            return
        self._hover = on
        self._animate(False)

    # ── 几何 ──
    def _thumb_center(self):
        r = self._thumb_radius()
        x_on = self._tw - self._pad - r
        x_off = self._pad + r
        return x_on if self._value else x_off

    def _thumb_radius(self):
        return max(1, (self._th - 2 * self._pad) // 2)

    def _thumb_center_actual(self):
        try:
            coords = self.coords(self._thumb_item)
            return (coords[0] + coords[2]) / 2.0
        except Exception:
            return self._thumb_center()

    # ── 动画 ──
    def _animate(self, animate=True):
        if self._anim is not None:
            try:
                self._anim.stop()
            except Exception:
                pass
            self._anim = None
        start_fill = self._fill
        target_fill = self._track_fill()
        start_x = self._thumb_center_actual()
        target_x = self._thumb_center()
        if not animate or utils.reduce_motion() or start_fill == target_fill:
            self._apply_visuals(target_x, target_fill)
            return

        def _apply(p):
            self._apply_visuals(
                start_x + (target_x - start_x) * p,
                transition(start_fill, target_fill, p))

        self._anim = Animation(
            utils.motion("motion_fast"), _apply, controller=out_cubic,
            fps=1000 // utils.motion_frame(), root=self,
            reduce_motion=utils.reduce_motion)
        self._anim.start()

    def _apply_visuals(self, cx, track_fill):
        self._fill = track_fill
        r = self._thumb_radius()
        try:
            self.itemconfigure(self._track, fill=track_fill)
            self.coords(self._thumb_item, cx - r, self._pad, cx + r, self._th - self._pad)
        except Exception:
            pass

    def _redraw(self):
        self.delete("all")
        C = self._colors()
        r = self._th / 2.0
        self._track = self.create_polygon(
            rounded_polygon(0, 0, self._tw, self._th, r),
            fill=self._track_fill(), outline=C.get("bd", "gray85"),
            width=1, smooth=True)
        tr = self._thumb_radius()
        cx = self._thumb_center()
        self._thumb_item = self.create_oval(
            cx - tr, self._pad, cx + tr, self._th - self._pad,
            fill=self._thumb_color(), outline=self._thumb_border_color(), width=1)
        self._fill = self._track_fill()
        try:
            if self.focus_get() is self:
                self.create_polygon(
                    rounded_polygon(0, 0, self._tw, self._th, r + 1),
                    fill="", outline=C.get("focus", C.get("ac", "dodgerblue")),
                    width=utils.sp("focus_w"), smooth=True)
        except Exception:
            pass
        self.configure(width=self._tw, height=self._th)
