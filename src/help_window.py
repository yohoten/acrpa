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
    · 左侧章节导航（Listbox 选中高亮）+ 右侧可滚动内容区（Text）。
    · 顶部搜索框（即时过滤章节/命令 + 命中高亮）；「复制本章」「下载模板」按钮；「关闭」按钮。
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
from tkinter import filedialog

import state

import utils
from utils import (scaled, center_geometry, geometry_in_screen, log1,
                   attach_tooltip, show_toast)

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

# 内置资源：脚本编辑模板（相对 res/；res/ 已被整体打包，冻结首启释放到 <exe>/res）
_TEMPLATE_RES_REL = "template/ACRPA脚本编辑模板.xlsx"

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
        self._copy_btn = None
        self._close_btn = None
        self._dl_btn = None
        self._search_lbl = None
        self._scrolls = []
        self._nav_frame = None
        self._txt_frame = None
        self._bar_frame = None

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
        win.rowconfigure(1, weight=1)

        # 顶部工具栏
        self._bar_frame = tkinter.Frame(win, bg=C["bgc"])
        self._bar_frame.grid(row=0, column=0, columnspan=2, sticky="ew",
                             padx=scaled(8), pady=(scaled(8), scaled(4)))
        self._search_lbl = tkinter.Label(self._bar_frame, text="搜索:",
                                         font=self.f_small, fg=C["fgm"],
                                         bg=C["bgc"])
        self._search_lbl.pack(side="left")
        self.search_var = tkinter.StringVar()
        self.entry = tkinter.Entry(self._bar_frame, textvariable=self.search_var,
                                   font=self.f_small, bg=C["ebg"], fg=C["fgb"],
                                   relief="solid", bd=1, insertbackground=C["fgt"],
                                   width=30)
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
        self._copy_btn = tkinter.Button(
            self._bar_frame, text="复制本章", font=self.f_small, bg=C["ac"],
            fg="white", relief="flat", bd=1, cursor="hand2",
            padx=scaled(12), pady=scaled(3), activebackground=C["ach"],
            activeforeground="white", command=self._copy_current)
        self._copy_btn.pack(side="right", padx=(0, scaled(6)))
        attach_tooltip(self._copy_btn, "复制当前章节为纯文本到剪贴板")
        self._dl_btn = tkinter.Button(
            self._bar_frame, text="下载模板", font=self.f_small, bg=C["bgc"],
            fg=C["fgb"], relief="flat", bd=1, cursor="hand2",
            padx=scaled(12), pady=scaled(3), activebackground=C["acl"],
            activeforeground=C["fgb"], command=self._download_template)
        self._dl_btn.pack(side="right", padx=(0, scaled(6)))
        attach_tooltip(self._dl_btn, "下载 ACRPA 脚本编辑模板 (.xlsx) 到本地")

        # 左：章节导航
        self._nav_frame = tkinter.Frame(win, bg=C["bgc"])
        self._nav_frame.grid(row=1, column=0, sticky="nsew",
                             padx=(scaled(8), scaled(4)),
                             pady=(0, scaled(8)))
        self._nav_frame.rowconfigure(0, weight=1)
        self._nav_frame.columnconfigure(0, weight=1)
        self.nav = tkinter.Listbox(
            self._nav_frame, font=self.f_body, activestyle="none",
            bg=C["bgc"], fg=C["fgb"], selectbackground=C["ac"],
            selectforeground="white", highlightthickness=0, bd=0,
            exportselection=False, width=18)
        self.nav.grid(row=0, column=0, sticky="nsew")
        nav_scroll = tkinter.Scrollbar(
            self._nav_frame, width=scaled(7), relief="flat", bg=C["bd"],
            troughcolor=C["logbg"], elementborderwidth=0,
            activebackground=C["fgm"])
        nav_scroll.grid(row=0, column=1, sticky="ns")
        self.nav.config(yscrollcommand=nav_scroll.set)
        nav_scroll.config(command=self.nav.yview)
        self._scrolls.append(nav_scroll)
        self.nav.bind("<<ListboxSelect>>", self._on_nav_select)

        # 右：内容区
        self._txt_frame = tkinter.Frame(win, bg=C["bgc"])
        self._txt_frame.grid(row=1, column=1, sticky="nsew",
                             padx=(scaled(4), scaled(8)),
                             pady=(0, scaled(8)))
        self._txt_frame.rowconfigure(0, weight=1)
        self._txt_frame.columnconfigure(0, weight=1)
        self.text = tkinter.Text(
            self._txt_frame, font=self.f_body, wrap="word", bg=C["logbg"],
            fg=C["logfg"], relief="flat", bd=0, padx=scaled(12),
            pady=scaled(10), insertbackground=C["fgt"],
            selectbackground=C["acl"], selectforeground=C["fgt"], cursor="arrow")
        self.text.grid(row=0, column=0, sticky="nsew")
        txt_scroll = tkinter.Scrollbar(
            self._txt_frame, width=scaled(7), relief="flat", bg=C["bd"],
            troughcolor=C["logbg"], elementborderwidth=0,
            activebackground=C["fgm"])
        txt_scroll.grid(row=0, column=1, sticky="ns")
        self.text.config(yscrollcommand=txt_scroll.set)
        txt_scroll.config(command=self.text.yview)
        self._scrolls.append(txt_scroll)
        self.text.bind("<Button-1>", self._on_text_click)
        self.text.bind("<Motion>", self._on_text_motion)

        # 主题标签一次性配置
        self._configure_tags()

        # 键盘：Esc 关闭 / Ctrl+F 聚焦搜索 / ↑↓ 导航
        win.bind("<Escape>", lambda e: self._on_close())
        win.bind("<Control-f>", lambda e: (self.entry.focus_set(),
                                           "break")[1])
        self.nav.bind("<Up>", lambda e: (self._move_nav(-1), "break")[1])
        self.nav.bind("<Down>", lambda e: (self._move_nav(1), "break")[1])

        # 几何
        self._apply_geometry()

    def _resolve_modal(self):
        try:
            return bool(getattr(state, "HELP_MODAL", False))
        except Exception:
            return False

    def _apply_geometry(self):
        try:
            geom = getattr(state, "HELP_GEOMETRY", "") or ""
        except Exception:
            geom = ""
        try:
            maxed = bool(getattr(state, "HELP_MAXIMIZED", False))
        except Exception:
            maxed = False
        w, h = scaled(900), scaled(640)
        try:
            ok = geometry_in_screen(self.root, geom)
        except Exception:
            ok = False
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
        t.tag_configure("h1", font=self.f_title, foreground=C["fgt"],
                        spacing1=scaled(6), spacing3=scaled(4))
        t.tag_configure("h2", font=self.f_button, foreground=C["fgt"],
                        spacing1=scaled(6), spacing3=scaled(2))
        t.tag_configure("h3", font=self.f_button, foreground=C["fgb"],
                        spacing1=scaled(4), spacing3=scaled(1))
        t.tag_configure("para", font=self.f_body, foreground=C["fgb"],
                        spacing1=scaled(1), spacing3=scaled(1))
        t.tag_configure("src", font=self.f_tiny, foreground=C["fgm"])
        t.tag_configure("desc", font=self.f_small, foreground=C["fgb"])
        t.tag_configure("param", font=self.f_small, foreground=C["fgm"])
        t.tag_configure("cmd", font=self.f_button, foreground=C["ac"])
        t.tag_configure("bold", font=self.f_button, foreground=C["fgt"])
        t.tag_configure("inline", font=self.f_log, foreground=C["ac"])
        t.tag_configure("code", font=self.f_log, foreground=C["logfg"],
                        lmargin1=scaled(12), lmargin2=scaled(12),
                        spacing1=scaled(2), spacing3=scaled(2))
        t.tag_configure("ulist", font=self.f_body, foreground=C["fgb"],
                        lmargin1=scaled(14), lmargin2=scaled(26))
        t.tag_configure("olist", font=self.f_body, foreground=C["fgb"],
                        lmargin1=scaled(14), lmargin2=scaled(26))
        t.tag_configure("table", font=self.f_small, foreground=C["fgb"],
                        lmargin1=scaled(8), lmargin2=scaled(8))
        t.tag_configure("thead", font=self.f_button, foreground=C["fgt"],
                        lmargin1=scaled(8), lmargin2=scaled(8))
        t.tag_configure("hr", font=self.f_body, foreground=C["bd"],
                        spacing1=scaled(4), spacing3=scaled(4))
        t.tag_configure("hit", background=C["ac"], foreground="white")
        t.tag_configure("copyhint", font=self.f_tiny, foreground=C["fgm"],
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
        for f in (self._bar_frame, self._nav_frame, self._txt_frame):
            try:
                f.configure(bg=C["bgc"])
            except Exception:
                pass
        try:
            self._search_lbl.configure(bg=C["bgc"], fg=C["fgm"])
        except Exception:
            pass
        try:
            self.entry.configure(bg=C["ebg"], fg=C["fgb"],
                                 insertbackground=C["fgt"])
        except Exception:
            pass
        try:
            self.nav.configure(bg=C["bgc"], fg=C["fgb"],
                               selectbackground=C["ac"], selectforeground="white")
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
            self._copy_btn.configure(bg=C["ac"], fg="white",
                                     activebackground=C["ach"],
                                     activeforeground="white")
        except Exception:
            pass
        try:
            self._dl_btn.configure(bg=C["bgc"], fg=C["fgb"],
                                   activebackground=C["acl"],
                                   activeforeground=C["fgb"])
        except Exception:
            pass
        self._configure_tags()

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
            self.nav.delete(0, "end")
            self._nav_ids = []
            for s in self._visible_sections():
                self.nav.insert("end", s.title)
                self._nav_ids.append(s.id)
        finally:
            self._suppress_nav = False

    def select_index(self, idx):
        try:
            if 0 <= idx < self.nav.size():
                self.nav.selection_clear(0, "end")
                self.nav.selection_set(idx)
                self.nav.activate(idx)
                self.nav.see(idx)
                self._render_by_id(self._nav_ids[idx])
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
            sel = self.nav.curselection()
            idx = (sel[0] if sel else -1) + delta
            idx = max(0, min(self.nav.size() - 1, idx))
            self.select_index(idx)
        except Exception:
            pass

    def _on_nav_select(self, _evt=None):
        if self._suppress_nav:
            return
        try:
            sel = self.nav.curselection()
            if not sel:
                return
            self._render_by_id(self._nav_ids[sel[0]])
        except Exception:
            pass

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
            sel = self.nav.curselection()
            if sel:
                cur_id = self._nav_ids[sel[0]]
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
        src = getattr(section, "source", "") or ""
        if src:
            self.text.insert("end", "来源: " + src + "\n", "src")
        self.text.insert("end", "\n")
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
            for it in getattr(b, "items", []) or []:
                self.text.insert("end", "•  " + str(it) + "\n", "ulist")
        elif kind == "olist":
            for n, it in enumerate(getattr(b, "items", []) or []):
                self.text.insert("end", "{}. {}\n".format(n + 1, it), "olist")
        elif kind == "code":
            self.text.insert("end", (getattr(b, "text", "") or "") + "\n", "code")
        elif kind == "table":
            rows = getattr(b, "rows", []) or []
            for ri, row in enumerate(rows):
                line = " | ".join(str(c) for c in row)
                self.text.insert("end", line + "\n",
                                 "thead" if ri == 0 else "table")
        elif kind == "hr":
            self.text.insert("end", "─" * 48 + "\n", "hr")
        else:
            self.text.insert("end", (getattr(b, "text", "") or "") + "\n", "para")

    def _insert_inline(self, text, spans=None):
        if not spans and _CONTENT_OK:
            try:
                spans = help_content.parse_inline(text)
            except Exception:
                spans = None
        if not spans:
            self.text.insert("end", text or "", "para")
            return
        for sp in spans:
            kind = getattr(sp, "kind", "text")
            txt = getattr(sp, "text", "")
            if kind == "bold":
                self.text.insert("end", txt, ("para", "bold"))
            elif kind == "code":
                self.text.insert("end", txt, ("para", "inline"))
            elif kind == "link":
                lk = "link_{}".format(self._link_seq)
                self._link_seq += 1
                self._link_targets[lk] = getattr(sp, "target", "")
                try:
                    self.text.tag_configure(lk, foreground=self.colors["ac"],
                                            underline=True)
                except Exception:
                    pass
                self.text.insert("end", txt, ("para", lk))
            else:
                self.text.insert("end", txt, "para")

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
                    self.text.insert("end", "      " + desc + "\n", "desc")
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
        """打开链接：http(s) 走浏览器；其余按 docs/ 资源定位后 os.startfile。"""
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
                self._link_error(target, e)
                return False
        self._link_error(target, FileNotFoundError(target))
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
