"""从窗口统一骨架 (docs/UI优化方案-v3.md §2.3)。

问题: 主窗口全面消费 `utils.TOKENS`(`sp/ctrl_h/gap`), 而 dialogs / help /
market / netlink 等从窗口大量使用字面量 `padx=10`、`minsize(400,500)`、
固定 `geometry("WxH+X+Y")` —— 等于同一应用里跑着两套尺度语言,
`utils.set_ui_scale()` 只能影响主窗口。

本模块把「从窗口样板」收敛成一处: 底色 / 标题 / 尺寸 / 最小尺寸 / 居中 /
模态 / 内容区 / 底部按钮区。**只做布局与尺度, 不碰业务逻辑**。

用法::

    from ui.window_shell import open_shell

    shell = open_shell(root, "脚本版本历史", size="md", modal=False)
    body = shell.content                     # 已套内边距的内容区 (grid 到 row1)
    shell.header("脚本版本历史", "双击行查看差异")   # 可选标题区
    shell.add_button("关闭", shell.close)     # 可选底部按钮区

设计约定:
  * 尺寸一律来自 `utils.TOKENS` 的 `dlg_*` 键 (`size="md"` → `dlg_md_w/h`);
  * 留白一律 `utils.sp()`, 控件高度 `utils.ctrl_h()`, 不写字面量;
  * 主题: 只用 `ui_theme` 既有色键, 不新增色板; 如传入 colors 则优先用调用方快照。
"""
import tkinter

import utils
from ui import theme as ui_theme


def _colors(colors=None):
    if colors:
        return colors
    try:
        return ui_theme.colors()
    except Exception:
        return {}


def sb_width(orientation="vertical"):
    """滚动条厚度 (实际 px)。

    docs/UI优化方案-v3.md §5.2 D10: 垂直统一 8px (`sb_w`); 横向内嵌在工具条/预览区
    的窄条用 6px (`sb_w_thin`), 两者都只从 TOKENS 派生, 不再出现 width=6/7/8/16。
    """
    horizontal = str(orientation or "").lower().startswith("h")
    return utils.sp("sb_w_thin" if horizontal else "sb_w")


def shell_size(size):
    """size 语义 → (**设计 px**, 未缩放) 的 (宽, 高)。

    注意: 返回的是设计像素, 缩放由 `utils.place_dialog()` 内部统一做 (`scaled()`),
    这里再乘一次 `sp()` 就会在 ui_scale≠1 时双重放大。

    size 可为:
      * `"sm" | "md" | "lg" | "xl"` —— 取 `TOKENS["dlg_<size>_w/h"]`;
      * `(w, h)` 设计 px 元组 —— 原样返回;
      * `None` —— 默认 `md`。
    """
    if isinstance(size, (tuple, list)) and len(size) == 2:
        return int(size[0]), int(size[1])
    key = (size or "md").lower()
    if key not in ("sm", "md", "lg", "xl"):
        key = "md"
    return int(utils.TOKENS["dlg_{}_w".format(key)]), int(utils.TOKENS["dlg_{}_h".format(key)])


class ShellBody:
    """骨架返回体: `.win / .content / .footer` 与少量构建助手。"""

    def __init__(self, win, content, footer, colors):
        self.win = win
        self.content = content
        self.footer = footer
        self.colors = colors
        self._footer_used = False

    # ── 内容区 ──
    def header(self, title, subtitle="", row=0):
        """在内容区顶部放标准标题区 (标题 + 可选副标题), 返回其 frame。"""
        C = self.colors
        f = tkinter.Frame(self.content, bg=C.get("bgc", "whitesmoke"))
        f.grid(row=row, column=0, sticky="ew")
        f.columnconfigure(1, weight=1)
        tkinter.Label(f, text=title, font=utils.FONT_TITLE, bg=C.get("bgc", "whitesmoke"),
                      fg=C.get("fgt", "#111")).grid(row=0, column=0, sticky="w")
        if subtitle:
            tkinter.Label(f, text=subtitle, font=utils.FONT_SMALL,
                          bg=C.get("bgc", "whitesmoke"), fg=C.get("fgm", "#666")
                          ).grid(row=0, column=1, sticky="w", padx=(utils.sp("sp_sm"), 0))
        return f

    # ── 底部按钮区 ──
    def _ensure_footer(self):
        if not self._footer_used:
            self.footer.grid(row=99, column=0, sticky="ew",
                             pady=(utils.gap("gap_tight"), 0))
            self.footer.columnconfigure(0, weight=1)
            self._footer_used = True
        return self.footer

    def add_button(self, text, command=None, kind="normal", tooltip=None):
        """底部按钮 (右对齐)。kind: normal / primary / danger。"""
        C = self.colors
        bar = self._ensure_footer()
        inner = getattr(self, "_footer_inner", None)
        if inner is None:
            inner = tkinter.Frame(bar, bg=C.get("bgc", "whitesmoke"))
            inner.grid(row=0, column=1, sticky="e")
            self._footer_inner = inner
        if kind == "primary":
            role, bg, fg = "ac", C.get("ac", "steelblue"), "white"
        elif kind == "danger":
            role, bg, fg = "dg", C.get("dg", "firebrick"), "white"
        else:
            role, bg, fg = "fgb", C.get("fgb", "lightgray"), C.get("fgt", "#111")
        btn = tkinter.Button(inner, text=text, command=command, font=utils.FONT_BUTTON,
                             bg=bg, fg=fg, relief="flat", bd=0, cursor="hand2",
                             padx=utils.sp("sp_md"), pady=2,
                             activebackground=C.get("ach", bg),
                             activeforeground=fg, highlightthickness=0)
        btn.pack(side="left", padx=(utils.gap("gap_tight"), 0))
        # 登记语义角色 → 换肤时按角色回填底色, 不会被刷成中性灰
        try:
            ui_theme.set_role(btn, role)
        except Exception:
            pass
        if tooltip:
            try:
                utils.attach_tooltip(btn, tooltip)
            except Exception:
                pass
        return btn

    def close(self):
        try:
            self.win.destroy()
        except Exception:
            pass


def apply_window_chrome(win, title=None, size="md", min_size=None,
                        modal=False, parent=None, bg_key="bgc",
                        resizable=True, center=True, colors=None,
                        icon_setter=None, geometry=None, maximized=False):
    """只套「窗口外壳」: 底色 / 标题 / 图标 / 尺寸 / 最小尺寸 / 居中 / 模态。

    适用于**自管布局**的既有窗口 (help / market / settings: 它们在 win 上直接
    grid 导航栏/状态栏, 塞进 `content` 层会打乱既有行列权重), 让它们也能共用
    同一套尺寸与缩放口径, 而不必重排内部结构。

    参数:
        geometry:  已校验过的几何串 (如记忆的 "1100x700+90+40"); 给了就直接用,
                   **不再**居中 (记忆位置优先), 且不做二次 scaling (已是实际 px)。
        maximized: True → 应用几何后尝试 `state("zoomed")`。
    返回传入窗口的 colors 快照 (供调用方继续取色)。
    """
    C = _colors(colors)
    bg = C.get(bg_key, "whitesmoke")
    try:
        win.configure(bg=bg)
    except Exception:
        pass
    if title:
        try:
            win.title(title)
        except Exception:
            pass
    if callable(icon_setter):
        try:
            icon_setter(win)
        except Exception:
            pass

    w, h = shell_size(size)
    if parent is not None:
        try:
            win.transient(parent)
        except Exception:
            pass

    placed = False
    if isinstance(geometry, str) and geometry.strip():
        try:
            win.geometry(geometry.strip())
            placed = True
        except Exception:
            placed = False
    if not placed and center:
        # place_dialog 内部已 scaled() 且自带越界钳制 / 父窗居中, 无需二次写 geometry
        try:
            anchor = parent or win.master or win
            if not utils.place_dialog(win, w, h, anchor):
                win.geometry(utils.center_geometry(
                    anchor, utils.sp(w), utils.sp(h)))
        except Exception:
            try:
                win.geometry(utils.center_geometry(
                    anchor, utils.sp(w), utils.sp(h)))
            except Exception:
                pass

    if min_size is None:
        mw, mh = utils.sp("dlg_min_w"), utils.sp("dlg_min_h")
    else:
        mw, mh = utils.sp(min_size[0]), utils.sp(min_size[1])
    try:
        win.minsize(mw, mh)
    except Exception:
        pass

    if isinstance(resizable, (tuple, list)):
        try:
            win.resizable(bool(resizable[0]), bool(resizable[1]))
        except Exception:
            pass
    else:
        try:
            win.resizable(bool(resizable), bool(resizable))
        except Exception:
            pass

    if modal:
        try:
            win.grab_set()
        except Exception:
            pass

    if maximized:
        try:
            win.state("zoomed")
        except Exception:
            pass
    return C


def apply_window_shell(win, title=None, size="md", min_size=None,
                       modal=False, parent=None, bg_key="bgc",
                       resizable=True, pad="sp_md", center=True,
                       colors=None, fonts=None, icon_setter=None):
    """把统一的从窗口骨架套到已有 Toplevel 上, 返回 `ShellBody`。

    参数:
        win:        已创建的 `tkinter.Toplevel`
        title:      窗口标题 (None 则不改)
        size:       "sm"/"md"/"lg"/"xl" 或 (w,h) 设计 px
        min_size:   (w,h) 设计 px; None → TOKENS["dlg_min_w/h"]
        modal:      True → transient(parent) + grab_set()
        parent:     父窗口 (居中与模态用)
        bg_key:     底色键 (默认 bgc)
        resizable:  (宽, 高) 或 bool
        pad:        内容区内边距 token
        center:     是否按父窗居中 (走 utils.place_dialog)
        icon_setter: 可选 callable(win) 设置窗口图标 (各窗自行注入)
    """
    C = apply_window_chrome(win, title=title, size=size, min_size=min_size,
                            modal=modal, parent=parent, bg_key=bg_key,
                            resizable=resizable, center=center, colors=colors,
                            icon_setter=icon_setter)
    bg = C.get(bg_key, "whitesmoke")

    # 内容区: row0 标题(可选) / row1 主体(伸缩) / row99 按钮区
    win.columnconfigure(0, weight=1)
    win.rowconfigure(1, weight=1)
    content = tkinter.Frame(win, bg=bg)
    content.grid(row=1, column=0, sticky="nsew",
                 padx=utils.sp(pad), pady=utils.sp(pad))
    content.columnconfigure(0, weight=1)
    content.rowconfigure(1, weight=1)

    footer = tkinter.Frame(win, bg=bg)
    return ShellBody(win, content, footer, C)


def open_shell(parent, title, **kwargs):
    """创建 Toplevel 并套用骨架, 返回 `ShellBody`。"""
    win = tkinter.Toplevel(parent)
    return apply_window_shell(win, title, parent=parent, **kwargs)


def scaled_pad(token="sp_md"):
    """便捷: 取一个内边距实际值 (供调用方在 shell 之外复用同一节奏)。"""
    return utils.sp(token)
