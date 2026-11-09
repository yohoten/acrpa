# -*- coding: utf-8 -*-
"""help_window.py — 帮助系统渲染层（阶段 1-2）。

分层：渲染层只负责建窗/排版/交互；内容一律来自内容层 `help_content`
（命令唯一来源是 `commands.list_all()`），本模块**不硬编码任何帮助文案**。

对外接口（供 dialogs.show_help_dialog 兼容外壳 / ACRPA 主题接线调用）：
    open_help_window(root=None, colors=None, fonts=None, section=None, modal=None)
        — 打开帮助窗口（单实例复用：已打开则聚焦/刷新，不重复建窗）。
    refresh_theme(root=None, colors=None)
        — 主题切换时重刷已打开帮助窗口（仿 netlink_window.refresh_theme）。
    get_instance() / is_open()
        — 供冒烟测试读取当前窗口实例与存活状态。

特性：
    · 非模态（默认 transient 但不 grab_set）；配置键 help_modal=True 时恢复模态。
    · 左侧章节导航（Treeview 悬停/选中高亮）+ 右侧可滚动内容区（Text）。
    · 顶部搜索框（即时过滤章节/命令 + 命中高亮，聚焦高亮）；「下载模板」「关闭」按钮；内容区复制图标。
    · Markdown 子集渲染（标题分级/列表/代码块/表格/水平线/可点击链接）。
    · 链接：docs/… 走 resolve_doc_path + os.startfile；http(s) 走浏览器；失败可见提示。
    · 命令速查：get_command_groups() 渲染分组 + 条目，逐条可复制。
    · 主题实时（refresh_theme 重刷全部控件与 Text 的 tag 前景色）。
    · 缩放走 utils.scaled；字体走 utils 命名字体角色（随 ui_scale 自动重配）。
    · 几何记忆：state.HELP_GEOMETRY（+ HELP_MAXIMIZED），恢复前用 geometry_in_screen 校验。
    · 容错：内容层缺失/异常时降级为仅「命令速查」（commands.list_all() 直出）。
"""
import os
import shutil
import sys
import tkinter
import tkinter.font as tkfont
from tkinter import filedialog
from tkinter import ttk

import state

import utils
from utils import (scaled, sp, center_geometry, geometry_in_screen, log1,
                   attach_tooltip, show_toast, text_on)

# 字体角色（命名字体，随 ui_scale 自动重配；直接引用角色名即可）
FONT_TITLE = utils.FONT_TITLE
FONT_BODY = utils.FONT_BODY
FONT_SMALL = utils.FONT_SMALL
FONT_BUTTON = utils.FONT_BUTTON
FONT_LOG = utils.FONT_LOG
FONT_TINY = utils.FONT_TINY

# 内容层（可选）：缺失/异常时降级，不阻断建窗
try:
    import help_content
    _CONTENT_OK = True
except Exception:
    help_content = None
    _CONTENT_OK = False

# 图片加载层（可选）：res/bg-ac.ico 需经 Pillow 转 PhotoImage。
# 不可用时 _bg_image 为 None，「联系与支持」章节只是少一张图，功能完全不受影响。
try:
    from ui import bg_image as _bg_image
except Exception:
    _bg_image = None

# 「联系与支持」章节末尾空白处展示的单张图标（不平铺、不放大）
_CONTACT_SECTION_ID = "contact"
_CONTACT_ICON_NAME = "bg-ac.ico"

# 内置资源：脚本编辑模板（相对 res/；res/ 已被整体打包，冻结首启释放到 <exe>/res）
_TEMPLATE_RES_REL = "template/ACRPA脚本编辑模板与说明.xlsx"

# 在线仓库：便携版只打包「帮助页实际引用」的 docs/*.md + docs/releases/，
# 其余仓库路径（docs/design、docs/analysis、src/… 等）解析不到时回落到在线仓库链接。
_REPO_BASE = "https://github.com/yohoten/acrpa/blob/main/"
_REPO_PREFIXES = ("docs/", "src/", "lib/", "res/", "tools/", "tests/")


def _repo_url(target):
    """把仓库相对路径（docs/…、src/… 等）转成在线仓库链接；非仓库路径返回 None。"""
    t = (target or "").strip()
    low = t.lower()
    for p in _REPO_PREFIXES:
        if low.startswith(p):
            return _REPO_BASE + t
    return None

# 模块级单实例
_instance = None


class _SimpleSection(object):
    """降级用极简章节对象（与 help_content.Section 字段兼容）。"""

    def __init__(self, sid, title, order=0, source="", body=""):
        self.id = sid
        self.title = title
        self.order = order
        self.source = source
        self.body = body


def _default_root():
    """尽力获取可用 root：优先 dialogs.root，其次 Tk 默认根。"""
    try:
        import dialogs
        r = getattr(dialogs, "root", None)
        if r is not None:
            return r
    except Exception:
        pass
    try:
        return tkinter._default_root
    except Exception:
        return None


def _block_text(block):
    """把一个 Block 拍平成可读纯文本（供复制本章）。"""
    kind = getattr(block, "kind", "para")
    if kind == "heading":
        return "#" * max(1, int(getattr(block, "level", 1) or 1)) + " " + \
            (getattr(block, "text", "") or "")
    if kind == "para":
        return getattr(block, "text", "") or ""
    if kind == "ulist":
        return "\n".join("• " + str(i) for i in getattr(block, "items", []))
    if kind == "olist":
        return "\n".join("{}. {}".format(n + 1, it)
                         for n, it in enumerate(getattr(block, "items", [])))
    if kind == "code":
        return getattr(block, "text", "") or ""
    if kind == "table":
        return "\n".join(" | ".join(str(c) for c in row)
                         for row in getattr(block, "rows", []))
    if kind == "blockquote":
        return "\n".join("> " + ln
                         for ln in (getattr(block, "text", "") or "").split("\n"))
    if kind == "hr":
        return "-" * 40
    return getattr(block, "text", "") or ""


class _HelpWindow(object):
    """帮助窗口（单实例）。"""

    def __init__(self, root, colors=None, fonts=None, initial_section=None,
                 modal=None):
        self.root = root
        self.colors = dict(colors) if isinstance(colors, dict) and colors \
            else dict(utils.C)
        self.fonts = fonts or (FONT_TITLE, FONT_BODY, FONT_SMALL, FONT_BUTTON)
        self.f_title, self.f_body, self.f_small, self.f_button = (
            list(self.fonts) + [FONT_TITLE, FONT_BODY, FONT_SMALL,
                                FONT_BUTTON])[:4]
        self.f_log = FONT_LOG
        self.f_tiny = FONT_TINY

        self.win = None
        self.nav = None
        self.text = None
        self.entry = None
        self.search_var = None
        self._close_btn = None
        self._dl_btn = None
        self._search_lbl = None
        self._copy_icon_btn = None
        self._copy_icon_cv = None
        self._copy_icon_hover = False
        self._scrolls = []
        self._contact_photo = None
        self._nav_frame = None
        self._nav_title = None
        self._nav_sep = None
        self._nav_style = None
        self._nav_hover_id = None
        self._txt_frame = None
        self._txt_header = None
        self._txt_title = None
        self._txt_header_sep = None
        self._bar_frame = None
        self._sep_frame = None
        self._hint_bar = None
        self._hint_lbl = None

        # 内容区缩放（Ctrl+滚轮 / Ctrl+加减），只影响正文 tag 字体，不污染命名字体
        self._zoom = 1.0
        self._zoom_fonts = {}
        self._zoom_fonts_zoom = None

        self._link_targets = {}
        self._copy_targets = {}
        self._link_seq = 0
        self._copy_seq = 0
        self._suppress_nav = False
        self._current_section = None
        self._fallback_only = False
        self._content_ok = False
        self._hit_sections = None      # None=不过滤
        self._hit_commands = None      # None=不过滤
        self._sections = []
        self._command_groups = []
        self._cmd_entries = []
        self.modal = False

        self._load_content()
        self._load_commands()
        self._build()
        self._load_nav()
        # 初始章节
        target = initial_section
        if target and not self.select_section(target):
            self.select_index(0)
        elif not target:
            self.select_index(0)

    # ── 存活判定 ────────────────────────────────────────────────
    def alive(self):
        try:
            return self.win is not None and self.win.winfo_exists()
        except Exception:
            return False

    # ── 内容装载（含降级） ──────────────────────────────────────
    def _load_content(self):
        self._sections = []
        if _CONTENT_OK:
            try:
                self._sections = list(help_content.get_sections())
            except Exception as e:
                log1("帮助章节加载失败, 降级为命令速查: {}".format(e), "warning")
                self._sections = []
        self._content_ok = bool(self._sections)
        if not self._sections:
            # 降级：仅「命令速查」
            self._fallback_only = True
            self._sections = [_SimpleSection(
                "commands", "命令速查", 1, "commands.list_all()（降级直出）", "")]

    def _load_commands(self):
        self._command_groups = []
        if _CONTENT_OK:
            try:
                self._command_groups = list(help_content.get_command_groups())
            except Exception as e:
                log1("命令分组加载失败, 降级为全部命令: {}".format(e), "warning")
                self._command_groups = []
        if not self._command_groups:
            entries = []
            try:
                import commands
                for item in commands.list_all():
                    name = item[0]
                    desc = item[1] if len(item) > 1 else ""
                    params = item[2] if len(item) > 2 else ""
                    entries.append(_SimpleEntry(name, desc, params))
            except Exception:
                pass
            self._command_groups = [("全部命令", entries)]
        self._cmd_entries = []
        for _t, es in self._command_groups:
            self._cmd_entries.extend(es)

    # ── 建窗 ────────────────────────────────────────────────────
    def _set_icon(self):
        try:
            import dialogs
            res = getattr(dialogs, "RES_DIR", "") or ""
            app = getattr(dialogs, "APP_ROOT", "") or ""
        except Exception:
            res = app = ""
        for cand in (os.path.join(res, "automation.ico"),
                     os.path.join(app, "automation.ico"),
                     os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "..", "res", "automation.ico")):
            try:
                if cand and os.path.exists(cand):
                    self.win.iconbitmap(cand)
                    return
            except Exception:
                continue

    def _build(self):
        C = self.colors
        win = tkinter.Toplevel(self.root)
        self.win = win
        try:    # 声明自管换肤: 通用 walk 不再跨入, 由本类 _apply_theme 负责
            from ui import theme as _ui_theme
            _ui_theme.claim_window(win, "help_window")
        except Exception:
            pass
        win.title("帮助 — A/C RPA")
        try:
            win.configure(bg=C["bgc"])
        except Exception:
            pass
        self._set_icon()
        # 非模态：transient 但不 grab_set；help_modal=True 时恢复模态
        try:
            win.transient(self.root)
        except Exception:
            pass
        self.modal = self._resolve_modal()
        if self.modal:
            try:
                win.grab_set()
            except Exception:
                pass
        win.protocol("WM_DELETE_WINDOW", self._on_close)

        win.columnconfigure(0, weight=0)
        win.columnconfigure(1, weight=1)
        win.rowconfigure(2, weight=1)

        # 顶部工具栏
        self._bar_frame = tkinter.Frame(win, bg=C["bgc"])
        self._bar_frame.grid(row=0, column=0, columnspan=2, sticky="ew",
                             padx=scaled(8), pady=(scaled(8), scaled(6)))
        self._search_lbl = tkinter.Label(self._bar_frame, text="搜索:",
                                         font=self.f_small, fg=C["fgm"],
                                         bg=C["bgc"])
        self._search_lbl.pack(side="left")
        self.search_var = tkinter.StringVar()
        self.entry = tkinter.Entry(self._bar_frame, textvariable=self.search_var,
                                   font=self.f_small, bg=C["ebg"], fg=C["fgb"],
                                   relief="flat", bd=0, insertbackground=C["fgt"],
                                   highlightthickness=1,
                                   highlightbackground=C["bd"],
                                   highlightcolor=C["ac"], width=30)
        self.entry.pack(side="left", padx=(scaled(4), scaled(8)))
        self.search_var.trace_add("write", lambda *a: self._on_search())
        self.entry.bind("<Return>", lambda e: self._jump_first_hit())
        attach_tooltip(self.entry, "输入即时过滤章节与命令（Ctrl+F 聚焦）")

        self._close_btn = tkinter.Button(
            self._bar_frame, text="关闭", font=self.f_small, bg=C["bgc"],
            fg=C["fgb"], relief="flat", bd=1, cursor="hand2",
            padx=scaled(12), pady=scaled(3), activebackground=C["acl"],
            activeforeground=C["fgb"], command=self._on_close)
        self._close_btn.pack(side="right")
        self._dl_btn = tkinter.Button(
            self._bar_frame, text="下载模板", font=self.f_small, bg=C["bgc"],
            fg=C["fgb"], relief="flat", bd=1, cursor="hand2",
            padx=scaled(12), pady=scaled(3), activebackground=C["acl"],
            activeforeground=C["fgb"], command=self._download_template)
        self._dl_btn.pack(side="right", padx=(0, scaled(6)))
        attach_tooltip(self._dl_btn, "下载 ACRPA 脚本编辑模板 (.xlsx) 到本地")

        # 工具栏底部细分隔线
        self._sep_frame = tkinter.Frame(win, bg=C["bd"], height=scaled(1))
        self._sep_frame.grid(row=1, column=0, columnspan=2, sticky="ew",
                             padx=scaled(8))

        # 左：章节导航（含「目录」标题 + 细分隔线）
        self._nav_frame = tkinter.Frame(win, bg=C["bgc"])
        self._nav_frame.grid(row=2, column=0, sticky="nsew",
                             padx=(scaled(8), scaled(4)),
                             pady=(scaled(4), scaled(8)))
        self._nav_frame.columnconfigure(0, weight=1)
        self._nav_frame.rowconfigure(2, weight=1)
        self._nav_title = tkinter.Label(
            self._nav_frame, text="目 录", font=self.f_small, fg=C["fgm"],
            bg=C["bgc"], anchor="w", padx=scaled(6), pady=scaled(4))
        self._nav_title.grid(row=0, column=0, columnspan=2, sticky="ew")
        self._nav_sep = tkinter.Frame(self._nav_frame, bg=C["bd"],
                                      height=scaled(1))
        self._nav_sep.grid(row=1, column=0, columnspan=2, sticky="ew",
                           pady=(0, scaled(4)))
        # 章节导航改用 Treeview：支持整行悬停高亮 + 选中高亮（Listbox 无法逐项着色）
        self._nav_style = ttk.Style()
        self._nav_style.configure("HelpNav.Treeview",
                                  background=C["bgc"],
                                  fieldbackground=C["bgc"],
                                  foreground=C["fgb"],
                                  borderwidth=0, relief="flat",
                                  rowheight=scaled(26))
        self._nav_style.map("HelpNav.Treeview",
                            background=[("selected", C["acl"])],
                            foreground=[("selected", C["fgt"])])
        self.nav = ttk.Treeview(
            self._nav_frame, show="tree", selectmode="browse",
            style="HelpNav.Treeview", cursor="hand2")
        self.nav.grid(row=2, column=0, sticky="nsew")
        self.nav.tag_configure("hover", background=C["ebg"])
        nav_scroll = tkinter.Scrollbar(
            self._nav_frame, width=sp("sb_w"), relief="flat", bg=C["bd"],
            troughcolor=C["logbg"], elementborderwidth=0,
            activebackground=C["fgm"])
        nav_scroll.grid(row=2, column=1, sticky="ns")
        self.nav.configure(yscrollcommand=nav_scroll.set)
        nav_scroll.configure(command=self.nav.yview)
        self._scrolls.append(nav_scroll)
        self.nav.bind("<<TreeviewSelect>>", self._on_nav_select)
        self.nav.bind("<Motion>", self._on_nav_motion)
        self.nav.bind("<Leave>", self._on_nav_leave)

        # 右：内容区（卡片边框 + 顶部标题栏）
        self._txt_frame = tkinter.Frame(win, bg=C["bgc"], bd=0,
                                        highlightthickness=1,
                                        highlightbackground=C["bd"])
        self._txt_frame.grid(row=2, column=1, sticky="nsew",
                             padx=(scaled(4), scaled(8)),
                             pady=(scaled(4), scaled(8)))
        self._txt_frame.columnconfigure(0, weight=1)
        self._txt_frame.rowconfigure(2, weight=1)
        # 内容卡片头部：左「内 容」标题，右复制图标
        self._txt_header = tkinter.Frame(self._txt_frame, bg=C["bgc"])
        self._txt_header.grid(row=0, column=0, sticky="ew")
        self._txt_header.columnconfigure(0, weight=1)
        self._txt_title = tkinter.Label(
            self._txt_header, text="内 容", font=self.f_small, fg=C["fgm"],
            bg=C["bgc"], anchor="w", padx=scaled(6), pady=scaled(4))
        self._txt_title.grid(row=0, column=0, sticky="w")
        self._copy_icon_btn = self._create_copy_icon(self._txt_header)
        self._copy_icon_btn.grid(row=0, column=1, sticky="e",
                                 padx=(0, scaled(6)), pady=scaled(2))
        attach_tooltip(self._copy_icon_btn, "复制当前章节为纯文本到剪贴板")
        # attach_tooltip 会绑定 Enter/Leave；这里用 add 追加悬停高亮，二者并存
        self._copy_icon_btn.bind("<Enter>", lambda e: self._on_copy_icon_enter(),
                                 add="+")
        self._copy_icon_btn.bind("<Leave>", lambda e: self._on_copy_icon_leave(),
                                 add="+")
        self._txt_header_sep = tkinter.Frame(self._txt_frame, bg=C["bd"],
                                             height=scaled(1))
        self._txt_header_sep.grid(row=1, column=0, sticky="ew")
        self.text = tkinter.Text(
            self._txt_frame, font=self.f_body, wrap="word", bg=C["logbg"],
            fg=C["logfg"], relief="flat", bd=0, padx=scaled(12),
            pady=scaled(10), insertbackground=C["fgt"],
            selectbackground=C["acl"], selectforeground=C["fgt"], cursor="arrow")
        self.text.grid(row=2, column=0, sticky="nsew")
        txt_scroll = tkinter.Scrollbar(
            self._txt_frame, width=sp("sb_w"), relief="flat", bg=C["bd"],
            troughcolor=C["logbg"], elementborderwidth=0,
            activebackground=C["fgm"])
        txt_scroll.grid(row=2, column=1, sticky="ns")
        self.text.config(yscrollcommand=txt_scroll.set)
        txt_scroll.config(command=self.text.yview)
        self._scrolls.append(txt_scroll)
        self.text.bind("<Button-1>", self._on_text_click)
        self.text.bind("<Motion>", self._on_text_motion)

        # 底部：快捷键提示条（弱化显示，帮助新用户发现操作）
        self._hint_bar = tkinter.Frame(win, bg=C["bgc"])
        self._hint_bar.grid(row=3, column=0, columnspan=2, sticky="ew",
                            padx=scaled(8), pady=(scaled(2), scaled(6)))
        self._hint_lbl = tkinter.Label(
            self._hint_bar, font=self.f_tiny, fg=C["fgm"], bg=C["bgc"],
            anchor="w", text="Esc 关闭 · Ctrl+F 搜索 · ↑↓ 切换章节 · Ctrl+滚轮/加减缩放 · 点击下划线文字可打开链接或复制")
        self._hint_lbl.pack(side="left")

        # 主题标签一次性配置
        self._configure_tags()

        # 键盘：Esc 关闭 / Ctrl+F 聚焦搜索 / ↑↓ 导航 / Ctrl+加减·滚轮 缩放
        win.bind("<Escape>", lambda e: self._on_close())
        win.bind("<Control-f>", lambda e: (self.entry.focus_set(),
                                           "break")[1])
        self.nav.bind("<Up>", lambda e: (self._move_nav(-1), "break")[1])
        self.nav.bind("<Down>", lambda e: (self._move_nav(1), "break")[1])
        for seq in ("<Control-plus>", "<Control-equal>", "<Control-KP_Add>"):
            win.bind(seq, self._on_zoom_in)
        for seq in ("<Control-minus>", "<Control-KP_Subtract>"):
            win.bind(seq, self._on_zoom_out)
        win.bind("<Control-0>", self._on_zoom_reset)
        self.text.bind("<Control-MouseWheel>", self._on_text_wheel)
        self.text.bind("<Control-Button-4>", self._on_text_wheel_up)
        self.text.bind("<Control-Button-5>", self._on_text_wheel_down)

        # 几何
        self._apply_geometry()

    def _resolve_modal(self):
        try:
            return bool(getattr(state, "HELP_MODAL", False))
        except Exception:
            return False

    def _apply_geometry(self):
        """几何 / 最小尺寸 / 最大化统一走 window_shell 外壳 (尺寸按设计 px 只缩放一次)。"""
        try:
            geom = getattr(state, "HELP_GEOMETRY", "") or ""
        except Exception:
            geom = ""
        try:
            maxed = bool(getattr(state, "HELP_MAXIMIZED", False))
        except Exception:
            maxed = False
        try:
            ok = bool(geom) and bool(geometry_in_screen(self.root, geom))
        except Exception:
            ok = False
        try:
            from ui.window_shell import apply_window_chrome
            apply_window_chrome(
                self.win, size=(900, 640), min_size=(560, 400),
                parent=self.root, bg_key="bgc", colors=self.colors,
                geometry=geom if ok else None, center=not ok,
                maximized=maxed)
            return
        except Exception:
            pass
        # 回退: 骨架不可用时保持旧行为
        w, h = scaled(900), scaled(640)
        if ok:
            try:
                self.win.geometry(geom)
            except Exception:
                self.win.geometry(center_geometry(self.root, w, h))
        else:
            try:
                self.win.geometry(center_geometry(self.root, w, h))
            except Exception:
                self.win.geometry("900x640")
        try:
            self.win.minsize(scaled(560), scaled(400))
        except Exception:
            pass
        if maxed:
            try:
                self.win.state("zoomed")
            except Exception:
                pass

    # ── 主题标签 ────────────────────────────────────────────────
    def _configure_tags(self):
        C = self.colors
        t = self.text
        F = self._make_zoom_fonts()
        t.tag_configure("h1", font=F["title"], foreground=C["fgt"],
                        spacing1=scaled(6), spacing3=scaled(4))
        t.tag_configure("h2", font=F["button"], foreground=C["fgt"],
                        spacing1=scaled(6), spacing3=scaled(2))
        t.tag_configure("h3", font=F["button"], foreground=C["fgb"],
                        spacing1=scaled(4), spacing3=scaled(1))
        # v6 §5.7: 章节标题下 1px 实线 (整行背景 = bd 色, 2px 行高)
        if getattr(self, "_rule_font", None) is None:
            try:
                self._rule_font = tkfont.Font(family="Microsoft YaHei UI", size=2)
            except Exception:
                self._rule_font = None
        t.tag_configure("titlebar", font=(self._rule_font or F["tiny"]),
                        background=C["bd"],
                        spacing1=scaled(2), spacing3=scaled(8))
        t.tag_configure("para", font=F["body"], foreground=C["fgb"],
                        spacing1=scaled(2), spacing3=scaled(6))
        t.tag_configure("src", font=F["tiny"], foreground=C["fgm"])
        t.tag_configure("desc", font=F["small"], foreground=C["fgb"])
        t.tag_configure("param", font=F["small"], foreground=C["fgm"])
        t.tag_configure("cmd", font=F["button"], foreground=C["ac"])
        t.tag_configure("bold", font=F["button"], foreground=C["fgt"])
        # v6 §5.7: 行内代码 chip —— 等宽字体 + surface_alt 浅底 (与日志面板 code 风格一致)
        t.tag_configure("inline", font=F["log"], foreground=C["ac"],
                        background=C["surface_alt"])
        # 代码块：衬底色 + 缩进，形成独立块，阅读更清晰
        t.tag_configure("code", font=F["log"], foreground=C["logfg"],
                        background=C["ebg"],
                        lmargin1=scaled(12), lmargin2=scaled(12),
                        spacing1=scaled(4), spacing3=scaled(6))
        # 引用块：弱化字号/颜色 + 左缩进，作为「提示/说明」的视觉提示
        t.tag_configure("blockquote", font=F["small"], foreground=C["fgm"],
                        lmargin1=scaled(18), lmargin2=scaled(18),
                        spacing1=scaled(2), spacing3=scaled(4))
        t.tag_configure("ulist", font=F["body"], foreground=C["fgb"],
                        lmargin1=scaled(14), lmargin2=scaled(26),
                        spacing3=scaled(2))
        t.tag_configure("olist", font=F["body"], foreground=C["fgb"],
                        lmargin1=scaled(14), lmargin2=scaled(26),
                        spacing3=scaled(2))
        t.tag_configure("table", font=F["small"], foreground=C["fgb"],
                        lmargin1=scaled(8), lmargin2=scaled(8))
        t.tag_configure("thead", font=F["button"], foreground=C["fgt"],
                        background=C["ebg"],
                        lmargin1=scaled(8), lmargin2=scaled(8))
        t.tag_configure("hr", font=F["body"], foreground=C["bd"],
                        spacing1=scaled(4), spacing3=scaled(4))
        t.tag_configure("hit", background=C["ac"],
                        foreground=text_on(C["ac"]))
        t.tag_configure("copyhint", font=F["tiny"], foreground=C["fgm"],
                        underline=True)
        # 已有链接标签按新主题重刷
        for lk in self._link_targets:
            try:
                t.tag_configure(lk, foreground=C["ac"], underline=True)
            except Exception:
                pass
        for cp in self._copy_targets:
            try:
                t.tag_configure(cp, foreground=C["ac"], underline=True)
            except Exception:
                pass

    def set_colors(self, colors=None):
        """更新当前颜色表（无参时从 utils.C 重取）。"""
        if isinstance(colors, dict) and colors:
            self.colors = dict(colors)
        elif colors is None:
            try:
                self.colors = dict(utils.C)
            except Exception:
                pass

    def _apply_theme(self):
        """主题切换时重刷全部控件与 tag 前景（供 refresh_theme 调用）。"""
        if not self.alive():
            return
        C = self.colors
        try:
            self.win.configure(bg=C["bgc"])
        except Exception:
            pass
        for f in (self._bar_frame, self._nav_frame, self._txt_frame,
                  self._hint_bar, self._txt_header):
            try:
                f.configure(bg=C["bgc"])
            except Exception:
                pass
        try:
            self._txt_frame.configure(highlightbackground=C["bd"])
        except Exception:
            pass
        try:
            self._sep_frame.configure(bg=C["bd"])
        except Exception:
            pass
        try:
            self._nav_sep.configure(bg=C["bd"])
        except Exception:
            pass
        try:
            self._txt_header_sep.configure(bg=C["bd"])
        except Exception:
            pass
        try:
            self._nav_title.configure(bg=C["bgc"], fg=C["fgm"])
        except Exception:
            pass
        try:
            self._txt_title.configure(bg=C["bgc"], fg=C["fgm"])
        except Exception:
            pass
        try:
            self._hint_lbl.configure(bg=C["bgc"], fg=C["fgm"])
        except Exception:
            pass
        try:
            self._search_lbl.configure(bg=C["bgc"], fg=C["fgm"])
        except Exception:
            pass
        try:
            self.entry.configure(bg=C["ebg"], fg=C["fgb"],
                                 insertbackground=C["fgt"],
                                 highlightbackground=C["bd"],
                                 highlightcolor=C["ac"])
        except Exception:
            pass
        try:
            # 导航 Treeview：背景/前景/选中色走 style（hover 色走 tag）
            self._nav_style.configure("HelpNav.Treeview",
                                      background=C["bgc"],
                                      fieldbackground=C["bgc"],
                                      foreground=C["fgb"])
            self._nav_style.map("HelpNav.Treeview",
                                background=[("selected", C["acl"])],
                                foreground=[("selected", C["fgt"])])
            self.nav.tag_configure("hover", background=C["ebg"])
        except Exception:
            pass
        try:
            self.text.configure(bg=C["logbg"], fg=C["logfg"],
                                insertbackground=C["fgt"],
                                selectbackground=C["acl"], selectforeground=C["fgt"])
        except Exception:
            pass
        for sb in self._scrolls:
            try:
                sb.configure(bg=C["bd"], troughcolor=C["logbg"],
                             activebackground=C["fgm"])
            except Exception:
                pass
        try:
            self._close_btn.configure(bg=C["bgc"], fg=C["fgb"],
                                      activebackground=C["acl"],
                                      activeforeground=C["fgb"])
        except Exception:
            pass
        try:
            self._dl_btn.configure(bg=C["bgc"], fg=C["fgb"],
                                   activebackground=C["acl"],
                                   activeforeground=C["fgb"])
        except Exception:
            pass
        try:
            # 复制图标是 Canvas 绘制，主题切换时重绘即可
            self._redraw_copy_icon()
        except Exception:
            pass
        self._configure_tags()

    # ── 复制图标（内容区右上角）─────────────────────────────────
    def _create_copy_icon(self, parent):
        """用 Canvas 画一个「双页」复制图标（不依赖字体字形，跨平台可靠）。"""
        s = scaled(16)
        cv = tkinter.Canvas(parent, width=s, height=s, bg=self.colors["bgc"],
                            highlightthickness=0, bd=0, cursor="hand2")
        self._copy_icon_cv = cv
        cv.bind("<Button-1>", lambda e: self._copy_current())
        self._redraw_copy_icon()
        return cv

    def _redraw_copy_icon(self):
        cv = getattr(self, "_copy_icon_cv", None)
        if cv is None:
            return
        C = self.colors
        bg = C["acl"] if self._copy_icon_hover else C["bgc"]
        fg = C["fgb"]
        cv.delete("all")
        cv.configure(bg=bg)
        # 前页（左下，实心，盖住后页重叠处）
        cv.create_rectangle(scaled(3), scaled(6), scaled(11), scaled(14),
                            fill=bg, outline=fg, width=1)
        # 后页（右上，仅轮廓）
        cv.create_rectangle(scaled(6), scaled(3), scaled(14), scaled(11),
                            outline=fg, width=1)

    def _on_copy_icon_enter(self):
        self._copy_icon_hover = True
        self._redraw_copy_icon()

    def _on_copy_icon_leave(self):
        self._copy_icon_hover = False
        self._redraw_copy_icon()

    # ── 内容缩放（Ctrl+滚轮 / Ctrl+加减）────────────────────────
    def _zoom_font(self, role):
        """按当前 _zoom 生成局部缩放字体，绝不改动命名字体。"""
        try:
            base = tkfont.Font(root=self.root, name=role, exists=True)
            fam = base.cget("family")
            size = int(base.cget("size"))
            weight = base.cget("weight")
            slant = base.cget("slant")
        except Exception:
            fam, size, weight, slant = "TkDefaultFont", 10, "normal", "roman"
        return tkfont.Font(root=self.root, family=fam,
                           size=max(1, int(round(size * self._zoom))),
                           weight=weight, slant=slant)

    def _make_zoom_fonts(self):
        """生成/缓存各角色的缩放字体（zoom 变化时才重建）。"""
        if (getattr(self, "_zoom_fonts_zoom", None) == self._zoom
                and self._zoom_fonts):
            return self._zoom_fonts
        out = {}
        for role in ("title", "body", "small", "button", "log", "tiny"):
            out[role] = self._zoom_font(role)
        self._zoom_fonts = out
        self._zoom_fonts_zoom = self._zoom
        return out

    def _apply_zoom(self, delta):
        new = max(0.6, min(2.5, self._zoom + delta))
        if abs(new - self._zoom) < 1e-6:
            return
        self._zoom = new
        # 仅重配 tag 字体即可，已渲染正文会随 tag 自动重排
        self._configure_tags()

    def _on_zoom_in(self, _e=None):
        self._apply_zoom(0.1)
        return "break"

    def _on_zoom_out(self, _e=None):
        self._apply_zoom(-0.1)
        return "break"

    def _on_zoom_reset(self, _e=None):
        self._apply_zoom(1.0 - self._zoom)
        return "break"

    def _on_text_wheel(self, event):
        try:
            delta = int(event.delta)
        except Exception:
            delta = 0
        self._apply_zoom(0.1 if delta > 0 else (-0.1 if delta < 0 else 0))
        return "break"

    def _on_text_wheel_up(self, _e=None):
        self._apply_zoom(0.1)
        return "break"

    def _on_text_wheel_down(self, _e=None):
        self._apply_zoom(-0.1)
        return "break"

    # ── 导航 ────────────────────────────────────────────────────
    def _section_by_id(self, sid):
        for s in self._sections:
            if s.id == sid:
                return s
        return None

    def _visible_sections(self):
        """当前查询下可见的章节列表。"""
        if self._hit_sections is None:
            return list(self._sections)
        keep = set(self._hit_sections)
        # 命令命中时始终保留命令速查章
        if self._hit_commands:
            keep.add("commands")
        vis = [s for s in self._sections if s.id in keep]
        return vis if vis else list(self._sections)

    def _load_nav(self):
        self._suppress_nav = True
        try:
            self.nav.delete(*self.nav.get_children())
            self._nav_ids = []
            for s in self._visible_sections():
                self.nav.insert("", "end", iid=s.id, text=s.title)
                self._nav_ids.append(s.id)
            self._nav_hover_id = None
        finally:
            self._suppress_nav = False

    def select_index(self, idx):
        try:
            if 0 <= idx < len(self._nav_ids):
                sid = self._nav_ids[idx]
                self.nav.selection_set(sid)
                self.nav.see(sid)
                self._set_nav_hover(None)
                self._render_by_id(sid)
                return True
        except Exception:
            pass
        return False

    def select_section(self, sid):
        try:
            if sid in self._nav_ids:
                return self.select_index(self._nav_ids.index(sid))
        except Exception:
            pass
        return False

    def _move_nav(self, delta):
        try:
            sel = self.nav.selection()
            if sel and sel[0] in self._nav_ids:
                idx = self._nav_ids.index(sel[0]) + delta
            else:
                idx = 0 if delta > 0 else len(self._nav_ids) - 1
            idx = max(0, min(len(self._nav_ids) - 1, idx))
            self.select_index(idx)
        except Exception:
            pass

    def _on_nav_select(self, _evt=None):
        if self._suppress_nav:
            return
        try:
            sel = self.nav.selection()
            if not sel:
                return
            self._set_nav_hover(None)
            self._render_by_id(sel[0])
        except Exception:
            pass

    def _set_nav_hover(self, iid):
        """给 Treeview 行加/去 hover 背景（选中行保持 accent，不叠加）。"""
        if getattr(self, "_nav_hover_id", None) == iid:
            return
        if getattr(self, "_nav_hover_id", None):
            try:
                self.nav.item(self._nav_hover_id, tags=())
            except Exception:
                pass
        self._nav_hover_id = iid
        if iid:
            try:
                self.nav.item(iid, tags=("hover",))
            except Exception:
                pass

    def _on_nav_motion(self, event):
        try:
            row = self.nav.identify_row(event.y)
        except Exception:
            row = ""
        sel = self.nav.selection()
        if row and sel and row == sel[0]:
            row = ""
        self._set_nav_hover(row if row else None)

    def _on_nav_leave(self, _evt=None):
        self._set_nav_hover(None)

    # ── 搜索 ────────────────────────────────────────────────────
    def search_hits(self, query):
        """返回 {sections:[sid...], commands:[name...]}；query 为空则空集。"""
        q = (query or "").strip().lower()
        res = {"sections": [], "commands": []}
        if not q:
            return res
        for s in self._sections:
            if (q in (s.title or "").lower() or q in (s.body or "").lower()
                    or q in (s.id or "").lower()):
                res["sections"].append(s.id)
        for e in self._cmd_entries:
            if (q in (e.name or "").lower()
                    or q in (getattr(e, "desc", "") or "").lower()
                    or q in (getattr(e, "params", "") or "").lower()):
                res["commands"].append(e.name)
        return res

    def _on_search(self, _evt=None):
        q = self.search_var.get() if self.search_var is not None else ""
        if not q.strip():
            self._hit_sections = None
            self._hit_commands = None
        else:
            hits = self.search_hits(q)
            self._hit_sections = set(hits["sections"])
            self._hit_commands = set(hits["commands"])
        # 记录当前章节，重建导航后尽量保持选中
        cur_id = None
        try:
            sel = self.nav.selection()
            if sel:
                cur_id = sel[0]
        except Exception:
            pass
        self._load_nav()
        if cur_id and cur_id in self._nav_ids:
            self.select_section(cur_id)
        else:
            self.select_index(0)
        # 重绘当前内容以反映命令过滤 + 命中高亮
        if self._current_section is not None:
            self._render_section(self._current_section)

    def _jump_first_hit(self):
        vis = self._visible_sections()
        if vis:
            self.select_section(vis[0].id)

    # ── 「联系与支持」章节空白处的单张图标（不平铺、不放大）─────
    def _res_dir(self):
        """res/ 目录：优先 dialogs.RES_DIR（已处理 frozen/源码两种情形）。"""
        try:
            import dialogs
            res = getattr(dialogs, "RES_DIR", "") or ""
            if res:
                return res
        except Exception:
            pass
        try:
            if getattr(sys, "frozen", False):
                return os.path.join(os.path.dirname(sys.executable), "res")
        except Exception:
            pass
        return os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "res")

    def _insert_contact_image(self):
        """在正文末尾空白处插入**单张**图标（居中、不平铺、绝不放大）。

        只对「联系与支持」章节生效；图片层不可用/加载失败时静默跳过，
        其它章节与所有交互完全不受影响。
        """
        if _bg_image is None or self.text is None:
            return
        try:
            width = int(self.text.winfo_width())
        except Exception:
            width = 0
        margin = scaled(48)
        # 仅在正文区比图还窄时才等比缩小（绝不放大）
        max_w = (width - margin) if width > margin * 2 else None
        photo = None
        try:
            photo = _bg_image.load_icon_photo(
                self._res_dir(), _CONTACT_ICON_NAME, max_width=max_w,
                master=self.win)
        except Exception:
            photo = None
        if photo is None:
            return
        try:
            self.text.tag_configure("contact_img", justify="center")
        except Exception:
            pass
        try:
            self.text.insert("end", "\n")
            idx0 = self.text.index("end-1c")
            self.text.image_create("end", image=photo, align="center",
                                   pady=scaled(10))
            self.text.tag_add("contact_img", idx0, self.text.index("end"))
            self._contact_photo = photo      # 保活，否则 PhotoImage 被 GC → 空白
        except Exception:
            self._contact_photo = None

    # ── 渲染 ────────────────────────────────────────────────────
    def _render_by_id(self, sid):
        s = self._section_by_id(sid)
        if s is not None:
            self._render_section(s)

    def _render_section(self, section):
        self._current_section = section
        C = self.colors
        self.text.config(state="normal")
        self.text.delete("1.0", "end")
        self._link_targets = {}
        self._copy_targets = {}
        self._link_seq = 0
        self._copy_seq = 0
        try:
            self.text.insert("end", section.title + "\n", "h1")
        except Exception:
            pass
        # 「来源」为内部维护元数据（如「内嵌 + docs/xxx.md」），对终端用户无意义，
        # 不再渲染到正文，避免干扰阅读；正文本身已能说明内容。
        # v6 §5.7: 标题下改 1px 实线 (bd 色) —— 对只含换行的行打背景 tag,
        # Tk 会把该行背景铺满整行宽, 配合 2px 字体即成全宽发丝线;
        # 替代旧「─」*28 字符虚线的廉价观感 (修 E13)。
        self.text.insert("end", "\n", "titlebar")
        if self._fallback_only or section.id == "commands":
            # 命令速查章：先正文（若有），再动态命令分组
            if self._content_ok and (section.body or "").strip():
                self._render_blocks(section.body)
            self._render_commands()
        else:
            if self._content_ok:
                self._render_blocks(section.body)
            else:
                self.text.insert("end", (section.body or "") + "\n", "para")
        # 「联系与支持」：正文下方空白处补一张单张图标（不平铺）
        if getattr(section, "id", "") == _CONTACT_SECTION_ID:
            self._insert_contact_image()
        self.text.config(state="disabled")
        # 命中高亮
        q = self.search_var.get() if self.search_var is not None else ""
        if q.strip():
            self._apply_highlight(q)

    def _render_blocks(self, body):
        try:
            blocks = help_content.parse_markdown_subset(body)
        except Exception as e:
            log1("Markdown 子集解析失败: {}".format(e), "warning")
            blocks = []
        for b in blocks:
            self._render_block(b)

    def _render_block(self, b):
        kind = getattr(b, "kind", "para")
        if kind == "heading":
            lvl = max(1, min(3, int(getattr(b, "level", 1) or 1)))
            self.text.insert("end", (getattr(b, "text", "") or "") + "\n",
                             "h{}".format(lvl))
        elif kind == "para":
            self._insert_inline(getattr(b, "text", ""),
                                getattr(b, "spans", None))
            self.text.insert("end", "\n")
        elif kind == "ulist":
            # v6 §5.7: 列表项也走行内渲染 (项内 **粗体**/`代码` 不再露字面标记)
            for it in getattr(b, "items", []) or []:
                self.text.insert("end", "•  ", "ulist")
                self._insert_inline(str(it), None, "ulist")
                self.text.insert("end", "\n")
        elif kind == "olist":
            for n, it in enumerate(getattr(b, "items", []) or []):
                self.text.insert("end", "{}. ".format(n + 1), "olist")
                self._insert_inline(str(it), None, "olist")
                self.text.insert("end", "\n")
        elif kind == "code":
            self.text.insert("end", (getattr(b, "text", "") or "") + "\n", "code")
        elif kind == "table":
            rows = getattr(b, "rows", []) or []
            for ri, row in enumerate(rows):
                line = " | ".join(str(c) for c in row)
                self.text.insert("end", line + "\n",
                                 "thead" if ri == 0 else "table")
        elif kind == "blockquote":
            for ln in (getattr(b, "text", "") or "").split("\n"):
                self._insert_inline(ln, None, "blockquote")
                self.text.insert("end", "\n", "blockquote")
        elif kind == "hr":
            self.text.insert("end", "─" * 48 + "\n", "hr")
        else:
            self.text.insert("end", (getattr(b, "text", "") or "") + "\n", "para")

    def _insert_inline(self, text, spans=None, base="para"):
        if not spans and _CONTENT_OK:
            try:
                spans = help_content.parse_inline(text)
            except Exception:
                spans = None
        if not spans:
            self.text.insert("end", text or "", base)
            return
        for sp in spans:
            kind = getattr(sp, "kind", "text")
            txt = getattr(sp, "text", "")
            if kind == "bold":
                self.text.insert("end", txt, (base, "bold"))
            elif kind == "code":
                self.text.insert("end", txt, (base, "inline"))
            elif kind == "link":
                lk = "link_{}".format(self._link_seq)
                self._link_seq += 1
                self._link_targets[lk] = getattr(sp, "target", "")
                try:
                    self.text.tag_configure(lk, foreground=self.colors["ac"],
                                            underline=True)
                except Exception:
                    pass
                self.text.insert("end", txt, (base, lk))
            else:
                self.text.insert("end", txt, base)

    def _render_commands(self):
        C = self.colors
        hit = self._hit_commands
        for title, entries in self._command_groups:
            if hit is not None:
                entries = [e for e in entries if e.name in hit]
                if not entries:
                    continue
            self.text.insert("end", (title or "命令") + "\n", "h2")
            for e in entries:
                self.text.insert("end", "  " + (e.name or ""), "cmd")
                cp = "copy_{}".format(self._copy_seq)
                self._copy_seq += 1
                copy_text = (e.name or "")
                params = getattr(e, "params", "") or ""
                if params:
                    copy_text = "{} {}".format(e.name, params)
                self._copy_targets[cp] = copy_text
                try:
                    self.text.tag_configure(cp, foreground=C["ac"],
                                            underline=True)
                except Exception:
                    pass
                self.text.insert("end", "  [复制]", ("copyhint", cp))
                self.text.insert("end", "\n")
                desc = getattr(e, "desc", "") or ""
                if desc:
                    # v6 §5.7: desc 内含 markdown 行内标记 (**粗体**/`选择器`),
                    # 走 _insert_inline 渲染, 不再露字面星号 (修 E13)
                    self.text.insert("end", "      ", "desc")
                    self._insert_inline(desc, None, "desc")
                    self.text.insert("end", "\n")
                self.text.insert("end", "      参数: " + (params or "无") + "\n",
                                 "param")
            self.text.insert("end", "\n")

    def _apply_highlight(self, query):
        q = (query or "").strip()
        if not q:
            return
        try:
            self.text.tag_remove("hit", "1.0", "end")
        except Exception:
            return
        start = "1.0"
        low = q.lower()
        count = 0
        while count < 500:
            try:
                pos = self.text.search(q, start, stopindex="end", nocase=True)
            except Exception:
                break
            if not pos:
                break
            end = "{}+{}c".format(pos, len(q))
            try:
                self.text.tag_add("hit", pos, end)
            except Exception:
                break
            start = end
            count += 1

    # ── 交互：链接 / 复制 ───────────────────────────────────────
    def _tags_at(self, x, y):
        try:
            idx = self.text.index("@%d,%d" % (x, y))
            return self.text.tag_names(idx)
        except Exception:
            return ()

    def _on_text_motion(self, event):
        tags = self._tags_at(event.x, event.y)
        hand = any(t.startswith("link_") or t.startswith("copy_")
                   for t in tags)
        try:
            self.text.configure(cursor="hand2" if hand else "arrow")
        except Exception:
            pass

    def _on_text_click(self, event):
        for t in self._tags_at(event.x, event.y):
            if t in self._link_targets:
                self._open_link(self._link_targets[t])
                return "break"
            if t in self._copy_targets:
                self._copy_to_clipboard(self._copy_targets[t])
                return "break"
        return None

    def _open_link(self, target):
        """打开链接：http(s) 走浏览器；docs/res 本地资源定位后 os.startfile；
        本地未命中但属仓库路径（docs/…、src/… 等）时回落在线仓库链接。"""
        target = (target or "").strip()
        if not target:
            return False
        low = target.lower()
        if low.startswith("http://") or low.startswith("https://"):
            try:
                import webbrowser
                webbrowser.open(target)
                return True
            except Exception as e:
                self._link_error(target, e)
                return False
        if low.startswith("download:"):
            # 触发内置脚本编辑模板下载（target 中的文件名仅作展示，实际用内置资源）
            return self._download_template()
        path = None
        if _CONTENT_OK:
            try:
                path = help_content.resolve_doc_path(target)
            except Exception:
                path = None
            if not path:
                try:
                    path = help_content.resolve_res_path(target)
                except Exception:
                    path = None
        if path and os.path.exists(path):
            try:
                os.startfile(path)
                return True
            except Exception as e:
                # .md 文档在未安装 Markdown 编辑器/无文件关联的系统上会失败：
                # 退化为默认浏览器打开本地文件，保证内容始终可读。
                if path.lower().endswith(".md"):
                    try:
                        import webbrowser
                        from pathlib import Path
                        webbrowser.open(Path(path).as_uri())
                        return True
                    except Exception:
                        pass
                self._link_error(target, e)
                return False
        # 本地未命中：未打包的仓库文档（docs/design、docs/analysis、src/… 等）
        # 回落到在线仓库链接，保持「维护在线链接」的兜底能力。
        url = _repo_url(target)
        if url:
            try:
                import webbrowser
                webbrowser.open(url)
                return True
            except Exception as e:
                self._link_error(target, e)
                return False
        self._link_error(target, "文件不存在或未随程序分发")
        return False

    def _link_error(self, target, err):
        """链接打开失败：可见提示（非静默），不阻断窗口。"""
        msg = "无法打开链接: {}（{}）".format(target, err)
        try:
            log1(msg, "warning")
        except Exception:
            pass
        try:
            show_toast(self.win, "打开失败: " + target, "warning")
        except Exception:
            pass

    # ── 下载脚本编辑模板 ────────────────────────────────────────
    def _notify(self, message, level="info"):
        """轻量提示：日志 + Toast（均失败静默，不阻断窗口）。"""
        try:
            log1(message, "warning" if level == "warning" else None)
        except Exception:
            pass
        try:
            show_toast(self.win, message, level)
        except Exception:
            pass

    def _download_template(self):
        """把内置的「脚本编辑模板」另存到用户自选路径。

        资源缺失、用户取消、复制失败均友好处理，不抛异常。
        返回 True 表示已成功写出文件。
        """
        path = None
        if _CONTENT_OK:
            try:
                path = help_content.resolve_res_path(_TEMPLATE_RES_REL)
            except Exception:
                path = None
        if not path or not os.path.exists(path):
            self._notify("未找到内置模板资源，请重装或更新 ACRPA", "warning")
            return False
        try:
            dst = filedialog.asksaveasfilename(
                parent=self.win,
                title="保存脚本编辑模板",
                defaultextension=".xlsx",
                initialfile=os.path.basename(path),
                filetypes=[("Excel 工作簿", "*.xlsx"), ("所有文件", "*.*")])
        except Exception as e:
            self._notify("无法打开「另存为」对话框: {}".format(e), "warning")
            return False
        if not dst:            # 用户取消：静默返回
            return False
        try:
            shutil.copyfile(path, dst)
        except Exception as e:
            self._notify("保存模板失败: {}".format(e), "warning")
            return False
        self._notify("已保存脚本编辑模板: " + dst, "success")
        return True

    def _copy_to_clipboard(self, text):
        try:
            self.win.clipboard_clear()
            self.win.clipboard_append(text or "")
        except Exception:
            pass
        try:
            show_toast(self.win, "已复制: " + (text or "")[:30], "success")
        except Exception:
            pass

    def _copy_current(self):
        if self._current_section is None:
            return
        self._copy_to_clipboard(self._section_plaintext(self._current_section))

    def _section_plaintext(self, section):
        lines = [section.title, ""]
        if self._fallback_only or section.id == "commands":
            for title, entries in self._command_groups:
                lines.append(title)
                for e in entries:
                    params = getattr(e, "params", "") or ""
                    lines.append("  {}  {}".format(e.name, params))
                    desc = getattr(e, "desc", "") or ""
                    if desc:
                        lines.append("      " + desc)
                lines.append("")
        elif self._content_ok and (section.body or "").strip():
            try:
                for b in help_content.parse_markdown_subset(section.body):
                    lines.append(_block_text(b))
            except Exception:
                lines.append(section.body or "")
        else:
            lines.append(section.body or "")
        return "\n".join(lines).rstrip() + "\n"

    # ── 焦点 / 关闭 ─────────────────────────────────────────────
    def focus(self):
        try:
            self.win.deiconify()
        except Exception:
            pass
        try:
            self.win.lift()
        except Exception:
            pass
        try:
            self.win.focus_force()
        except Exception:
            pass

    def _on_close(self):
        try:
            if self.alive():
                try:
                    state.HELP_MAXIMIZED = (self.win.state() == "zoomed")
                except Exception:
                    state.HELP_MAXIMIZED = False
                if not getattr(state, "HELP_MAXIMIZED", False):
                    try:
                        state.HELP_GEOMETRY = self.win.geometry()
                    except Exception:
                        pass
                try:
                    state.save_config()
                except Exception:
                    pass
        except Exception:
            pass
        try:
            if self.win is not None:
                self.win.destroy()
        except Exception:
            pass
        self.win = None


class _SimpleEntry(object):
    """降级用极简命令条目（与 help_content.CommandEntry 字段兼容）。"""

    def __init__(self, name, desc, params):
        self.name = name
        self.desc = desc
        self.params = params


# ======================================================================
# 对外函数
# ======================================================================
def open_help_window(root=None, colors=None, fonts=None, section=None,
                     modal=None):
    """打开帮助窗口（单实例复用）。返回 Toplevel（失败返回 None）。"""
    global _instance
    if root is None:
        root = _default_root()
    if root is None:
        return None
    if _instance is not None and _instance.alive():
        # 已打开：聚焦 + 主题刷新 + 可选跳转
        _instance.set_colors(colors)
        _instance._apply_theme()
        if modal is not None:
            _instance.modal = bool(modal)
        if section:
            _instance.select_section(section)
        try:
            _instance._load_nav()
            if section:
                _instance.select_section(section)
        except Exception:
            pass
        _instance.focus()
        return _instance.win
    try:
        _instance = _HelpWindow(root, colors, fonts, initial_section=section,
                                modal=modal)
    except Exception:
        import traceback
        try:
            log1("帮助窗口创建失败:\n{}".format(traceback.format_exc()), "error")
        except Exception:
            pass
        _instance = None
        return None
    try:
        _instance.focus()
    except Exception:
        pass
    return _instance.win


# 设计文档 10.2 的别名
open_help = open_help_window


def refresh_theme(root=None, colors=None):
    """主题切换时重刷已打开帮助窗口（不存在时空操作）。"""
    inst = _instance
    if inst is None or not inst.alive():
        return
    inst.set_colors(colors)
    inst._apply_theme()


def get_instance():
    """返回当前帮助窗口实例（未打开返回 None）。"""
    return _instance


def is_open():
    """帮助窗口是否已打开。"""
    return _instance is not None and _instance.alive()
