# -*- coding: utf-8 -*-
"""设置窗口九页导航与静态字段搜索。"""
import tkinter

import utils


PAGE_DEFS = (
    ("基础设置", (
        ("general", "常规", "设置启动、界面布局、快捷键与安全选项。"),
    )),
    ("自动化", (
        ("execution", "执行", "设置输入模式、重试、超时与运行保护。"),
        ("recording", "录制与日志", "管理动作录制、日志保存和诊断导出。"),
        ("schedule", "定时任务", "配置执行计划、重复规则与轮询间隔。"),
    )),
    ("智能与连接", (
        ("ai_ocr", "AI 与 OCR", "配置 AI 服务、文字识别和原生 OCR 后端。"),
        ("browser", "浏览器与驱动", "设置浏览器内核、登录态与 DD 输入驱动。"),
        ("netlink", "设备互联", "管理局域网发现、配对认证与远程监控。"),
    )),
    ("扩展与关于", (
        ("extensions", "扩展与市场", "配置 Python 环境、脚本市场和扩展。"),
        ("about", "关于", "查看版本、检查更新与执行维护操作。"),
    )),
)

PAGE_ORDER = tuple(page for _group, pages in PAGE_DEFS for page, _title, _desc in pages)
PAGE_META = {page: (title, desc) for _group, pages in PAGE_DEFS
             for page, title, desc in pages}
CARD_TO_PAGE = {
    "system": "general", "exec": "execution", "record": "recording",
    "log": "recording", "sched": "schedule", "ai": "ai_ocr",
    "advanced": "execution", "netlink": "netlink", "python": "extensions",
    "market": "extensions", "extensions": "extensions", "quick": "about",
}

PAGE_ALIASES = {
    "general": ("auto_start", "minimize_to_tray", "compact_mode", "mini_bar",
                "ui_scale", "hotkey", "confirm_dangerous", "系统", "外观"),
    "execution": ("retry_max", "retry_interval", "image_timeout", "input_mode",
                  "app_protect", "failsafe", "max_execution_minutes",
                  "bound_window_title", "stop_on_error", "基础执行"),
    "recording": ("recording_stop_hotkey", "enable_log_saving", "log_level",
                  "log_retention", "record", "日志"),
    "schedule": ("sched_enabled", "sched_hour", "sched_minute",
                 "sched_repeat_mode", "sched_poll_interval", "scheduler"),
    "ai_ocr": ("api_model", "api_key", "ai_provider", "ai_base_url",
               "ai_smart_retry", "ai_anomaly_detect", "ocr_preload",
               "ocr_threads", "ocr_preferred_backend", "paddle_dll", "ocr"),
    "browser": ("browser_headless", "browser_slow_mo", "browser_channel",
                "browser_persistent", "browser_user_data_dir", "dd_dll_path",
                "webdriver", "浏览器"),
    "netlink": ("netlink_enabled", "netlink_port", "netlink_tls", "网络互联"),
    "extensions": ("python_path", "market_auto_check_update", "market_install_dir",
                   "extensions", "插件", "脚本市场"),
    "about": ("check_update", "version", "更新", "重置"),
}


def search_records(records, query, limit=30):
    """在预构建静态索引中做不区分大小写的子串匹配。"""
    words = tuple(word for word in str(query or "").lower().split() if word)
    if not words:
        return []
    matches = []
    for record in records:
        haystack = record.get("search", "")
        if all(word in haystack for word in words):
            matches.append(record)
            if len(matches) >= limit:
                break
    return matches


class SettingsNavigation(tkinter.Frame):
    """左侧分组导航；搜索框和结果列表不接触任何配置变量。"""

    def __init__(self, parent, colors, on_page, on_result):
        self.C = colors
        self.on_page = on_page
        self.on_result = on_result
        self.active_page = "general"
        self.records = []
        self.matches = []
        self._buttons = {}
        self._groups = {}
        super().__init__(parent, bg=colors["bg"], width=utils.sp(200))
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)

        tkinter.Label(self, text="设置", font=utils.FONT_TITLE,
                      bg=self.C["bg"], fg=self.C["fgt"], anchor="w").grid(
            row=0, column=0, sticky="ew", padx=14, pady=(16, 10))
        self.query_var = tkinter.StringVar()
        self.search_entry = tkinter.Entry(
            self, textvariable=self.query_var, font=utils.FONT_SMALL,
            bg=self.C["ebg"], fg=self.C["fgb"], insertbackground=self.C["fgb"],
            relief="solid", bd=1)
        self.search_entry.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 8),
                               ipady=utils.sp("gap_tight"))
        self.query_var.trace_add("write", self._on_query)
        self.search_entry.bind("<Down>", self._focus_results)
        self.search_entry.bind("<Return>", self._accept_first)
        self.search_entry.bind("<Escape>", self.clear_search)
        self.search_placeholder = tkinter.Label(
            self.search_entry, text="搜索设置、关键词、配置键…",
            font=utils.FONT_TINY, bg=self.C["ebg"], fg=self.C["fgm"], anchor="w")
        self.search_placeholder.bind("<Button-1>", lambda _e: self.search_entry.focus_set())
        self.search_entry.bind("<FocusIn>", lambda _e: self.search_placeholder.place_forget())
        self.search_entry.bind("<FocusOut>", lambda _e: self._sync_placeholder())
        self._sync_placeholder()

        self.results = tkinter.Listbox(
            self, font=utils.FONT_SMALL, height=6, activestyle="none",
            bg=self.C["bgc"], fg=self.C["fgb"], selectbackground=self.C["acl"],
            selectforeground=self.C["fgt"], relief="solid", bd=1,
            highlightthickness=0, exportselection=False)
        self.results.bind("<Return>", self._accept_selected)
        self.results.bind("<Double-Button-1>", self._accept_selected)
        self.results.bind("<Escape>", self.clear_search)
        self.results.bind("<Up>", self._maybe_return_to_search)

        self.pages = tkinter.Frame(self, bg=self.C["bg"])
        self.pages.grid(row=3, column=0, sticky="nsew")
        self.pages.columnconfigure(0, weight=1)
        row = 0
        for group, page_defs in PAGE_DEFS:
            group_label = tkinter.Label(self.pages, text=group, font=utils.FONT_TINY,
                                        bg=self.C["bg"], fg=self.C["fgm"], anchor="w")
            group_label.grid(row=row, column=0, sticky="ew", padx=14,
                             pady=(utils.sp("gap_tight"), 1))
            self._groups[group] = group_label
            row += 1
            for page, title, _desc in page_defs:
                button = tkinter.Label(
                    self.pages, text="  " + title, font=utils.FONT_SMALL,
                    bg=self.C["bg"], fg=self.C["fgb"], anchor="w",
                    cursor="hand2", padx=6, pady=utils.sp("gap_tight"))
                button.grid(row=row, column=0, sticky="ew", padx=(8, 12), pady=1)
                button.bind("<Button-1>", lambda _e, key=page: self.on_page(key))
                button.bind("<Enter>", lambda _e, key=page: self._hover(key, True))
                button.bind("<Leave>", lambda _e, key=page: self._hover(key, False))
                self._buttons[page] = button
                row += 1
        self.show_page("general")

    def set_records(self, records):
        self.records = list(records)
        self._on_query()

    def show_page(self, page):
        if page not in PAGE_META:
            return
        self.active_page = page
        for key, button in self._buttons.items():
            selected = key == page
            button.configure(
                bg=self.C["acl"] if selected else self.C["bg"],
                fg=self.C["ac"] if selected else self.C["fgb"],
                font=utils.FONT_SMALL_BOLD if selected else utils.FONT_SMALL)

    def _hover(self, page, entering):
        if page == self.active_page:
            return
        self._buttons[page].configure(
            bg=self.C["caption_hover"] if entering else self.C["bg"])

    def _sync_placeholder(self):
        if self.query_var.get() or self.focus_get() is self.search_entry:
            self.search_placeholder.place_forget()
        else:
            self.search_placeholder.place(x=5, y=0, relheight=1.0)

    def _on_query(self, *_args):
        self._sync_placeholder()
        query = self.query_var.get().strip()
        self.matches = search_records(self.records, query)
        self.results.delete(0, "end")
        if not query:
            self.results.grid_remove()
            return
        self.results.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 4))
        if not self.matches:
            self.results.insert("end", "  未找到匹配设置")
            self.results.itemconfigure(0, fg=self.C["fgm"])
            return
        for record in self.matches:
            self.results.insert("end", "  {} / {}".format(record["page_title"], record["title"]))

    def _focus_results(self, _event=None):
        if self.matches:
            self.results.focus_set()
            self.results.selection_clear(0, "end")
            self.results.selection_set(0)
            self.results.activate(0)
        return "break"

    def _accept_first(self, _event=None):
        if self.matches:
            self.results.selection_clear(0, "end")
            self.results.selection_set(0)
            self._accept_selected()
        return "break"

    def _accept_selected(self, _event=None):
        selection = self.results.curselection()
        if not selection or not self.matches:
            return "break"
        index = int(selection[0])
        if index >= len(self.matches):
            return "break"
        self.on_result(self.matches[index])
        return "break"

    def _maybe_return_to_search(self, _event=None):
        selection = self.results.curselection()
        if selection and int(selection[0]) == 0:
            self.search_entry.focus_set()
            self.search_entry.icursor("end")
            return "break"
        return None

    def clear_search(self, _event=None):
        self.query_var.set("")
        self.search_entry.focus_set()
        return "break"

    def refresh(self, colors):
        self.C = colors
        self.configure(bg=colors["bg"])
        self.pages.configure(bg=colors["bg"])
        self.search_entry.configure(bg=colors["ebg"], fg=colors["fgb"],
                                    insertbackground=colors["fgb"])
        self.search_placeholder.configure(bg=colors["ebg"], fg=colors["fgm"])
        self.results.configure(bg=colors["bgc"], fg=colors["fgb"],
                               selectbackground=colors["acl"],
                               selectforeground=colors["fgt"])
        for label in self._groups.values():
            label.configure(bg=colors["bg"], fg=colors["fgm"])
        self.show_page(self.active_page)
        self._on_query()
