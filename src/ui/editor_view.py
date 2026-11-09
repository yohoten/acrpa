# -*- coding: utf-8 -*-
"""ui.editor_view — 脚本编辑 Tab (构建 / 换肤)。

把 ACRPA.py build_app 内「TAB 1: 编辑」控件构建段抽到本模块; 编辑行为簇回调一律
经 build(..., deps=...) 注入, 绝不 import ACRPA。构建期内立即读取宿主编辑器控件
全局 (tree / toolbar_canvas / _editor_font 等) 的行为函数不在此调用, 由宿主在
get_widgets() 取回句柄同步自身 globals 后补调 (事件驱动绑定则 mainloop 前不派发,
可安全在此 bind)。依赖键见 _CTX 注释。
"""
import tkinter
import tkinter.font
from tkinter import ttk

from ui import editor_columns

__all__ = [
    "build", "refresh_theme", "get_widgets",
    "get_tab_edit", "get_toolbar", "get_edit_file_label", "get_tb_scroll",
    "get_toolbar_canvas", "get_toolbar_scrollbar", "get_toolbar_inner",
    "get_toolbar_overflow_hint", "get_tree_frame", "get_tree", "get_tsy",
    "get_tsx", "get_legend_frame", "get_legend_inner", "get_editor_font",
]

FONT_SMALL = None
FONT_ICON = None
FONT_TINY = None

_tab_edit = None
_toolbar = None
_edit_file_label = None
_tb_scroll = None
_toolbar_canvas = None
_toolbar_scrollbar = None
_toolbar_inner = None
_toolbar_overflow_hint = None
_tree_frame = None
_tree = None
_tsy = None
_tsx = None
_legend_frame = None
_legend_inner = None
_editor_font = None
_idle_bg_label = None
_empty_hint = None

_CTX = {
    "ui_theme": None, "sp": None, "ctrl_h": None, "themed": None,
    "state": None, "root": None, "attach_tooltip": None, "log1": None,
    "idle_bg": None, "empty_hint": None, "empty_hint_text": "",
    "legend_items": None, "legend_groups": None, "legend_dots": None,
    "debounce_cb": None, "tb_sync_layout": None,
    "tree_columns_on_configure": None,
    "editor_zoom_wheel": None, "editor_zoom_step": None, "editor_zoom_reset": None,
    "toggle_breakpoint": None, "show_conditional_breakpoint_menu": None,
    "kb_copy": None, "kb_paste": None, "cmd_del_row": None,
    "cmd_undo": None, "cmd_redo": None, "kb_duplicate_row": None,
    "kb_toggle_comment": None, "kb_command_palette": None,
    "kb_move_up": None, "kb_move_down": None, "kb_toggle_breakpoint": None,
    "kb_run_script": None, "cmd_edit_row_dialog": None, "edit_cell": None,
    "sync_idle_bg": None, "idle_bg_on_configure": None,
    "bind_paste_new_task": None, "interp_hint_for_values": None,
    "build_sel_bar": None, "sync_sel_bars": None, "idle_bg_retheme": None,
    "colors": None,
    "ctx": None,
}


def _bind_fonts(fonts):
    global FONT_SMALL, FONT_ICON, FONT_TINY
    fonts = fonts or {}
    FONT_SMALL = fonts.get("small")
    FONT_ICON = fonts.get("icon")
    FONT_TINY = fonts.get("tiny")


def _pal():
    c = _CTX.get("colors")
    return c if isinstance(c, dict) else {}


def _call(name, *args, **kw):
    fn = _CTX.get(name)
    if fn is None:
        _c = _CTX.get("ctx")
        if _c is not None:
            try:
                fn = _c.get(name)
            except Exception:
                fn = None
    if fn is None:
        return None
    try:
        return fn(*args, **kw)
    except Exception:
        return None


def build(parent, *, notebook, colors, fonts, deps=None):
    """构建脚本编辑 Tab 的全部控件并绑定事件。

    · 不 import ACRPA; 宿主回调经 deps 注入。
    · 返回后宿主经 get_widgets() 取回句柄同步自身 globals, 再补调
      _preconfig_cmd_tags / _apply_editor_zoom / _build_sel_bar /
      _bind_sel_bar / _bind_row_hover / _init_toolbar_buttons 等构建期行为。
    """
    global _tab_edit, _toolbar, _edit_file_label, _tb_scroll
    global _toolbar_canvas, _toolbar_scrollbar, _toolbar_inner, _toolbar_overflow_hint
    global _tree_frame, _tree, _tsy, _tsx
    global _legend_frame, _legend_inner, _editor_font, _idle_bg_label, _empty_hint

    if deps:
        for _k in list(_CTX.keys()):
            if _k in deps:
                _CTX[_k] = deps[_k]
    _CTX["colors"] = colors if isinstance(colors, dict) else _CTX.get("colors")
    _bind_fonts(fonts)

    C = _pal()
    sp = _CTX.get("sp") or (lambda v: v)
    ctrl_h = _CTX.get("ctrl_h") or (lambda v: 34)
    ui_theme = _CTX.get("ui_theme")
    root = _CTX.get("root")

    _idle_bg_d = _CTX.get("idle_bg") or {}
    _empty_hint_d = _CTX.get("empty_hint") or {}

    # ── Tab 容器 ──
    tab_edit = tkinter.Frame(parent, bg=C.get("bg"))
    tab_edit.grid(row=0, column=0, sticky="nsew")
    tab_edit.columnconfigure(0, weight=1)
    tab_edit.rowconfigure(1, weight=1)
    notebook.add(tab_edit, text="  脚本编辑  ")

    # ── 工具栏 (横向滚动) ──
    toolbar = tkinter.Frame(tab_edit, bg=C.get("bg"))
    toolbar.grid(row=0, column=0, sticky="ew", padx=sp(6), pady=(sp(4), sp(2)))
    toolbar.columnconfigure(0, weight=1)

    edit_file_label = tkinter.Label(toolbar, text="", font=FONT_SMALL,
                                    fg=C.get("fgm"), bg=C.get("bg"), anchor="e")
    edit_file_label.grid(row=0, column=1, sticky="ne", padx=(sp(4), sp(2)))

    tb_scroll = tkinter.Frame(toolbar, bg=C.get("bg"))
    tb_scroll.grid(row=0, column=0, sticky="ew")
    tb_scroll.columnconfigure(0, weight=1)

    toolbar_canvas = tkinter.Canvas(tb_scroll, bg=C.get("bg"),
                                    height=ctrl_h("ctrl_h_lg"),
                                    highlightthickness=0, bd=0)
    toolbar_scrollbar = tkinter.Scrollbar(tb_scroll, orient="horizontal",
                                          command=toolbar_canvas.xview,
                                          width=sp("sb_w_thin"))
    toolbar_inner = tkinter.Frame(toolbar_canvas, bg=C.get("bg"))
    toolbar_overflow_hint = tkinter.Label(tb_scroll, text="⇄", font=FONT_SMALL,
                                          fg=C.get("fgm"), bg=C.get("bg"))

    _tb_sync_debounced = _call("debounce_cb", toolbar_canvas, "tb_layout", 120,
                               _CTX.get("tb_sync_layout"))
    if _tb_sync_debounced is not None:
        toolbar_inner.bind("<Configure>", _tb_sync_debounced)
        toolbar_canvas.bind("<Configure>", _tb_sync_debounced)
    toolbar_canvas.create_window((0, 0), window=toolbar_inner, anchor="w")
    toolbar_canvas.configure(xscrollcommand=toolbar_scrollbar.set)
    toolbar_canvas.grid(row=0, column=0, sticky="ew")
    toolbar_scrollbar.grid(row=1, column=0, sticky="ew")

    # ── 树区 + 待机背景层 ──
    tree_frame = tkinter.Frame(tab_edit, bg=C.get("bgc"),
                               highlightbackground=C.get("bd"),
                               highlightthickness=1)
    tree_frame.grid(row=1, column=0, sticky="nsew", padx=sp(3), pady=(0, sp(4)))
    tree_frame.columnconfigure(0, weight=1)
    tree_frame.rowconfigure(0, weight=1)

    _idle_bg_label = tkinter.Label(tab_edit, bd=0, highlightthickness=1,
                                   highlightbackground=C.get("bd"),
                                   bg=C.get("bgc"))
    _idle_bg_label.grid(row=1, column=0, sticky="nsew", padx=sp(3), pady=(0, sp(4)))
    _idle_bg_label.grid_remove()
    _idle_bg_d["label"] = _idle_bg_label
    try:
        tab_edit.bind("<Configure>", _CTX.get("idle_bg_on_configure"), add=True)
        _idle_bg_label.bind("<Configure>", _CTX.get("idle_bg_on_configure"), add=True)
    except Exception:
        pass

    _st = _CTX.get("state")
    if _st is not None:
        _st._editor_rows = []

    tree = ttk.Treeview(tree_frame,
                        columns=("cmd",) + tuple("p{}".format(i) for i in range(1, 10)),
                        show="tree headings", selectmode="browse")
    tree.grid(row=0, column=0, sticky="nsew")
    tree.heading("#0", text="#")
    tree.column("#0", width=40, minwidth=40, stretch=False)
    tree.heading("cmd", text="命令类型")
    tree.column("cmd", width=100, minwidth=80, stretch=True)
    for i in range(1, 10):
        _c = "p{}".format(i)
        tree.heading(_c, text="参数{}".format(i))
        tree.column(_c, width=65, minwidth=50, stretch=True)

    tree.bind("<<TreeviewSelect>>",
              lambda _e: editor_columns.sync_param_headings(tree), add=True)
    try:
        editor_columns.sync_param_headings(tree)
    except Exception:
        pass
    try:
        editor_columns.bind_param_header_tip(tree)
    except Exception:
        pass
    tree.bind("<Configure>", _CTX.get("tree_columns_on_configure"), add=True)

    # ── 滚动条 ──
    tsy = tkinter.Scrollbar(tree_frame, orient="vertical", command=tree.yview,
                            width=sp("sb_w"), relief="flat",
                            elementborderwidth=0, bg=C.get("bd"),
                            troughcolor=C.get("bgc"))
    tsx = tkinter.Scrollbar(tree_frame, orient="horizontal", command=tree.xview,
                            width=sp("sb_w"), relief="flat",
                            elementborderwidth=0, bg=C.get("bd"),
                            troughcolor=C.get("bgc"))
    tree.configure(yscrollcommand=tsy.set, xscrollcommand=tsx.set)
    tsy.grid(row=0, column=1, sticky="ns")
    tsx.grid(row=1, column=0, sticky="ew")

    # ── 空态提示 (v6 §5.5: 快捷键渲染为键帽 —— accent 描边小色块 Label,
    #    全静态控件, 无 Canvas/动画, 显隐仍走宿主 place/place_forget 幂等切换) ──
    _hint_title = (_CTX.get("empty_hint_text") or "还没有任何步骤").split("\n", 1)[0]
    _empty_hint = tkinter.Frame(tab_edit, bg=C.get("bgc"),
                                highlightthickness=1,
                                highlightbackground=C.get("bd"))
    tkinter.Label(_empty_hint, text=_hint_title, font=FONT_SMALL,
                  bg=C.get("bgc"), fg=C.get("fgm")).pack(
        padx=sp(14), pady=(sp(10), 0))
    _keys_row = tkinter.Frame(_empty_hint, bg=C.get("bgc"))
    _keys_row.pack(padx=sp(14), pady=(sp(6), sp(10)))
    for _i, (_cap, _act) in enumerate((("Ctrl+N", "新建脚本"),
                                       ("Ctrl+O", "打开脚本"),
                                       ("Ctrl+I", "插入命令"))):
        if _i:
            tkinter.Label(_keys_row, text="·", font=FONT_SMALL,
                          bg=C.get("bgc"), fg=C.get("fgd")).pack(
                side="left", padx=sp(6))
        tkinter.Label(_keys_row, text=_cap, font=FONT_TINY,
                      bg=C.get("bgc"), fg=C.get("ac"),
                      padx=sp(4), pady=0,
                      highlightthickness=1,
                      highlightbackground=C.get("ac")).pack(side="left")
        tkinter.Label(_keys_row, text=" " + _act, font=FONT_SMALL,
                      bg=C.get("bgc"), fg=C.get("fgm")).pack(
            side="left", padx=(sp(2), 0))
    _empty_hint_d["label"] = _empty_hint
    _empty_hint_d["shown"] = False

    # ── 编辑区字体 + 缩放上限 (缩放值宿主后续补设) ──
    _editor_font = tkinter.font.Font(root=root, name="ACRPA_EDITOR_TABLE",
                                     exists=False, family="Microsoft YaHei UI",
                                     size=10)

    # ── 图例 ──
    legend_frame = tkinter.Frame(tab_edit, bg=C.get("bg"))
    legend_frame.grid(row=2, column=0, sticky="ew", padx=sp(3), pady=(0, sp(2)))
    legend_inner = tkinter.Frame(legend_frame, bg=C.get("bg"))
    legend_inner.pack(anchor="w")
    _lg = _CTX.get("legend_groups")
    _ld = _CTX.get("legend_dots")
    if _lg is not None:
        _lg.clear()
    if _ld is not None:
        _ld.clear()
    for label, group in (_CTX.get("legend_items") or []):
        f = tkinter.Frame(legend_inner, bg=C.get("bg"))
        f.pack(side="left", padx=(12, 0))  # §4.1 P2-1: 图例组间距统一 12
        _dot = tkinter.Label(f, text="●", font=FONT_ICON,
                             fg=ui_theme.cat_color(group), bg=C.get("bg"))
        _dot._acrpa_keep_fg = True
        _dot.pack(side="left")
        if _ld is not None:
            _ld.append((_dot, group))
        _lbl = tkinter.Label(f, text=label, font=FONT_TINY, fg=C.get("fgm"),
                             bg=C.get("bg"))
        ui_theme.set_fg_token(_lbl, "fgm")
        _lbl.pack(side="left")
        if _lg is not None:
            _lg.append((tuple(n for n in label.split("/") if n), f))

    # ── 树 tag ──
    tree.tag_configure("even", background=C.get("zebra"))
    tree.tag_configure("running", background=C.get("hlbg"))
    _themed = _CTX.get("themed")
    tree.tag_configure("breakpoint",
                       foreground=_themed("dg") if _themed else C.get("dg"))

    # ── 缩放绑定 (事件驱动, mainloop 前不派发) ──
    tree.bind("<Control-MouseWheel>", _CTX.get("editor_zoom_wheel"))
    _zstep = _CTX.get("editor_zoom_step")
    tree.bind("<Control-plus>", lambda e: _zstep(1))
    tree.bind("<Control-equal>", lambda e: _zstep(1))
    tree.bind("<Control-minus>", lambda e: _zstep(-1))
    tree.bind("<Control-Key-0>", _CTX.get("editor_zoom_reset"))

    # ── 编辑行为绑定 (事件驱动) ──
    tree.bind("<Button-1>", _CTX.get("toggle_breakpoint"), add=True)
    tree.bind("<Button-3>", _CTX.get("show_conditional_breakpoint_menu"))
    tree.bind("<Control-c>", _CTX.get("kb_copy"))
    tree.bind("<Control-v>", _CTX.get("kb_paste"))
    tree.bind("<Delete>", lambda e: _call("cmd_del_row"))
    tree.bind("<Control-z>", _CTX.get("cmd_undo"))
    tree.bind("<Control-y>", _CTX.get("cmd_redo"))
    tree.bind("<Control-Z>", _CTX.get("cmd_undo"))
    tree.bind("<Control-Y>", _CTX.get("cmd_redo"))
    tree.bind("<Control-d>", _CTX.get("kb_duplicate_row"))
    tree.bind("<Control-D>", _CTX.get("kb_duplicate_row"))
    tree.bind("<Control-slash>", _CTX.get("kb_toggle_comment"))
    tree.bind("<Control-k>", _CTX.get("kb_command_palette"))
    tree.bind("<Control-K>", _CTX.get("kb_command_palette"))
    try:
        root.bind("<Control-k>", _CTX.get("kb_command_palette"))
        root.bind("<Control-K>", _CTX.get("kb_command_palette"))
    except Exception:
        pass
    try:
        _call("bind_paste_new_task", root, root, None)
    except Exception as _e:
        _call("log1", "[editor_view] 粘贴绑定失败: {}".format(_e))
    try:
        _call("attach_tooltip", tree,
              lambda: _call("interp_hint_for_values",
                            [tree.set(_iid, "cmd") for _iid in tree.selection()]))
    except Exception:
        pass
    tree.bind("<Alt-Up>", _CTX.get("kb_move_up"))
    tree.bind("<Alt-Down>", _CTX.get("kb_move_down"))
    tree.bind("<F9>", _CTX.get("kb_toggle_breakpoint"))
    tree.bind("<F5>", _CTX.get("kb_run_script"))
    tree.bind("<F2>", lambda e: _call("cmd_edit_row_dialog"))
    tree.bind("<Double-1>", _CTX.get("edit_cell"))
    for _seq in ("<<TreeviewSelect>>", "<Button-1>", "<Double-1>", "<Key>",
                 "<ButtonRelease-1>"):
        try:
            tree.bind(_seq, lambda e: _call("sync_idle_bg"), add=True)
        except Exception:
            pass
    try:
        tree_frame.bind("<Configure>", _CTX.get("idle_bg_on_configure"), add=True)
    except Exception:
        pass

    # ── 主题订阅 ──
    if ui_theme is not None and hasattr(ui_theme, "subscribe"):
        try:
            ui_theme.subscribe(refresh_theme)
        except Exception:
            pass

    # ── 落到模块级 globals ──
    _tab_edit = tab_edit
    _toolbar = toolbar
    _edit_file_label = edit_file_label
    _tb_scroll = tb_scroll
    _toolbar_canvas = toolbar_canvas
    _toolbar_scrollbar = toolbar_scrollbar
    _toolbar_inner = toolbar_inner
    _toolbar_overflow_hint = toolbar_overflow_hint
    _tree_frame = tree_frame
    _tree = tree
    _tsy = tsy
    _tsx = tsx
    _legend_frame = legend_frame
    _legend_inner = legend_inner

    return get_widgets()


def refresh_theme(dark=None, colors=None, prev=None):
    """ThemeBus 订阅回调: 树 tag / 选中左缘色条 / 待机背景随主题重绘。

    dark/colors/prev 由 ui_theme.publish 传入; colors 须为色板 dict。
    """
    C = colors if isinstance(colors, dict) else _CTX.get("colors")
    if not isinstance(C, dict):
        return
    _CTX["colors"] = C
    tree = _tree
    if tree is not None:
        try:
            tree.tag_configure("even", background=C.get("zebra"))
            tree.tag_configure("running", background=C.get("hlbg"))
            tree.tag_configure("breakpoint", foreground=C.get("dg"))
        except Exception:
            pass
    _call("build_sel_bar")
    if tree is not None:
        _call("sync_sel_bars", tree)
    _call("idle_bg_retheme")


def get_widgets():
    return {
        "tab_edit": _tab_edit, "toolbar": _toolbar,
        "edit_file_label": _edit_file_label, "tb_scroll": _tb_scroll,
        "toolbar_canvas": _toolbar_canvas, "toolbar_scrollbar": _toolbar_scrollbar,
        "toolbar_inner": _toolbar_inner,
        "toolbar_overflow_hint": _toolbar_overflow_hint,
        "tree_frame": _tree_frame, "tree": _tree, "tsy": _tsy, "tsx": _tsx,
        "legend_frame": _legend_frame, "legend_inner": _legend_inner,
        "editor_font": _editor_font,
        "idle_bg_label": _idle_bg_label, "empty_hint": _empty_hint,
    }


def get_tab_edit():
    return _tab_edit


def get_toolbar():
    return _toolbar


def get_edit_file_label():
    return _edit_file_label


def get_tb_scroll():
    return _tb_scroll


def get_toolbar_canvas():
    return _toolbar_canvas


def get_toolbar_scrollbar():
    return _toolbar_scrollbar


def get_toolbar_inner():
    return _toolbar_inner


def get_toolbar_overflow_hint():
    return _toolbar_overflow_hint


def get_tree_frame():
    return _tree_frame


def get_tree():
    return _tree


def get_tsy():
    return _tsy


def get_tsx():
    return _tsx


def get_legend_frame():
    return _legend_frame


def get_legend_inner():
    return _legend_inner


def get_editor_font():
    return _editor_font
