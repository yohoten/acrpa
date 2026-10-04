# -*- coding: utf-8 -*-
"""market_window.py — ACRPA 脚本市场 v2 窗口 UI (批次 2)。

把原内联在 ACRPA._open_marketplace 的市场窗口抽出并重构为独立模块
(docs/marketplace-v2-design.md §3)。批次 2 范围：窗口骨架与四态、卡片列表、
搜索/分类/标签筛选、详情弹窗、安装进度反馈、账号区 (GitHub/Gitee 登录入口)。
上传向导属批次 3，本模块仅预留占位按钮 (点击给出提示，绝不抛未实现异常)。

对外接口:
    open_market_window(parent, on_install=None) -> None
    open_marketplace(root, on_install=None, *, host=None) -> None   # 设计 §3.1 命名
    close_marketplace() -> None
    retheme_marketplace() -> None

设计约束:
  * 所有颜色一律经 utils.themed(token) 实时取，禁止 hex 字面量；
  * 所有间距/控件高/圆角/图标尺寸一律经 utils 的 TOKENS 访问器
    (sp/gap/ctrl_h/radius/icon_size/tk_px)，禁止魔法像素 (padding 尤其)；
  * 主题切换后由主程序调用 retheme_marketplace() 即时重刷 (未开则安全 no-op)；
  * ui_scale 变更后需重建卡片列表 (padding 随 scaled() 同步)；本模块提供
    MarketWindow.rebuild() 供外部/自测触发。

script_dir 口径 (批次 2 固定，消除批次 1 遗留歧义):
    安装回调 on_install(fp) 收到的是 marketplace.download_script 返回的 .xls
    绝对路径；调用方 (ACRPA._market_on_install) 统一以 os.path.dirname(fp)
    作为 state.script_dir。该目录即安装落点：
      - 包模式   : <save_dir>/market_scripts/<id>/   (脚本 + 拍平后的 png 同目录)
      - 旧单文件 : <save_dir>/                        (平铺，行为不变)
    不再从 CONFIG_PATH 另行推算，避免两套口径不一致。
"""
import os
import sys
import json
import threading
import webbrowser
import tkinter
from tkinter import ttk, messagebox, filedialog

import state
import utils
from utils import (themed, log1, sp, gap, ctrl_h, radius, icon_size, tk_px,
                   create_card, _btn, FONT_TITLE, FONT_BODY, FONT_SMALL,
                   FONT_SMALL_BOLD, FONT_BUTTON, FONT_TINY, FONT_LOG,
                   FONT_ICON_MD, FONT_ICON_LG)
import marketplace as mkt
import accounts
import script_package
import market_upload as mup


# ══════════════════════════════════════════════════════════════════════
# 常量 (颜色一律走 themed()，此处只放语义映射，不放 hex)
# ══════════════════════════════════════════════════════════════════════

CATEGORIES = ("全部", "办公", "财务", "系统", "其他")

# 分类 → 主题色 token (设计 §3.3 分类色条)
_CATEGORY_TOKEN = {"办公": "ac", "财务": "wn", "系统": "fgm", "其他": "bd"}
# 分类 → 默认图标 (无 icon 字段时的占位)
_CATEGORY_EMOJI = {"办公": "☰", "财务": "¥", "系统": "⚙", "其他": "▩"}

# 强调底色按钮上的文字色 (与既有工具栏按钮同款写法，非 hex 硬编码)
_ON_ACCENT = "white"

# 帮助链接 (创建 PAT)
_TOKEN_URL = {
    "gitee":  "https://gitee.com/profile/personal_access_tokens",
    "github": "https://github.com/settings/tokens",
}

# 底部状态栏 / 计数文案
_LOADING_TEXT = "正在加载脚本库…"

# 单例窗口引用 (供 retheme / close 使用)
_WINDOW = None


def _category_color(category):
    """分类 → 当前主题色 (实时 themed())。"""
    return themed(_CATEGORY_TOKEN.get(category, "bd"))


def _category_icon(info):
    """卡片图标：优先 info.icon，缺省回退分类图标。"""
    icon = (getattr(info, "icon", "") or "").strip()
    if icon:
        return icon
    return _CATEGORY_EMOJI.get(getattr(info, "category", ""), "▩")


def _star_text(rating):
    """评分 → 星串 (★★★☆☆)。"""
    try:
        n = int(round(float(rating)))
    except Exception:
        n = 0
    n = max(0, min(5, n))
    return "★" * n + "☆" * (5 - n)


def _try_set_icon(window):
    """尽力为子窗口设置图标；失败静默 (不改动主程序图标逻辑)。"""
    try:
        if getattr(sys, "frozen", False):
            base = os.path.dirname(sys.executable)
        else:
            base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        ico = os.path.join(base, "res", "automation.ico")
        if not os.path.exists(ico):
            ico = os.path.join(base, "automation.ico")
        if os.path.exists(ico):
            window.iconbitmap(ico)
    except Exception:
        pass


def _toast(parent, msg, kind="info"):
    """安全弹 toast；parent 不可用时退回 log1。"""
    try:
        from utils import show_toast
        show_toast(parent, msg, kind)
    except Exception:
        log1(msg, "warning" if kind == "error" else None)


# ══════════════════════════════════════════════════════════════════════
# 已装脚本更新自检 (P0-7 接线: state.MARKET_AUTO_CHECK_UPDATE)
# ══════════════════════════════════════════════════════════════════════

def _installed_local_versions(save_dir):
    """扫描市场安装目录 → {script_id: local_version}。

    本地版本来源: <market_install_root>/<id>/manifest.json —— 即
    marketplace._download_and_install_package 的包模式安装落点
    (script_package.unpack 会把 manifest.json 一并写入安装目录)。
    读不到清单 / 取不到 id 或 version 的目录一律跳过, 绝不误报。
    旧单文件安装 (平铺 .xls, 无 manifest) 无法取版本 → 跳过 (限制见模块说明)。
    """
    result = {}
    try:
        root = mkt.market_install_root(save_dir)
    except Exception:
        return result
    try:
        names = os.listdir(root)
    except Exception:
        return result
    for name in names:
        try:
            d = os.path.join(root, name)
            if not os.path.isdir(d):
                continue
            mf = os.path.join(d, script_package.MANIFEST_NAME)
            if not os.path.exists(mf):
                continue
            with open(mf, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                continue
            sid = str(data.get("id") or name or "").strip()
            ver = str(data.get("version") or "").strip()
            if sid and ver:
                result[sid] = ver
        except Exception:
            continue
    return result


def _collect_updates(save_dir):
    """对已装脚本逐个问远端是否有新版本 → 可更新 id 列表 (失败静默)。"""
    out = []
    for sid, ver in _installed_local_versions(save_dir).items():
        try:
            if mkt.check_update(sid, ver):
                out.append(sid)
        except Exception:
            continue
    return out


# ══════════════════════════════════════════════════════════════════════
# 市场窗口
# ══════════════════════════════════════════════════════════════════════

class MarketWindow:
    """脚本市场窗口 (单例)。"""

    def __init__(self, parent, on_install=None, host=None):
        self.parent = parent
        self.on_install = on_install
        self.host = host

        self._alive = True
        self._all_scripts = []      # 全量 (未筛选)
        self._tag_selected = set()  # 多选标签
        self._state = "loading"     # loading/empty/error/list
        self._error_msg = ""
        self._regs = []             # [(widget, opts_callable)] 固定控件主题重刷登记
        self._card_widgets = []     # 当前卡片 (重建时销毁)
        self._wrap_labels = []      # 需随宽度重算 wraplength 的描述标签
        self._download_active = False

        self._build_ui()
        self._load(force=True)

    # ────────────────────────────────────────────────────────────────
    # 主题重刷登记
    # ────────────────────────────────────────────────────────────────
    def _reg(self, widget, opts, apply_now=True):
        """登记需随主题重刷的固定控件；opts 为返回 configure kwargs 的可调用。"""
        self._regs.append((widget, opts))
        if apply_now:
            try:
                widget.configure(**opts())
            except Exception:
                pass
        return widget

    def _reg_map(self):
        """重新绑定 utils.C 后重刷全部登记控件 (颜色实时取 themed())。"""
        for widget, opts in list(self._regs):
            try:
                widget.configure(**opts())
            except Exception:
                pass

    # ────────────────────────────────────────────────────────────────
    # UI 骨架
    # ────────────────────────────────────────────────────────────────
    def _build_ui(self):
        dlg = tkinter.Toplevel(self.parent)
        self.dlg = dlg
        dlg.title("脚本市场 — ACRPA")
        dlg.geometry("{}x{}+{}+{}".format(
            tk_px(660), tk_px(560), tk_px(340), tk_px(80)))
        dlg.minsize(tk_px(640), tk_px(520))
        dlg.transient(self.parent)
        dlg.configure(bg=themed("bgc"))
        _try_set_icon(dlg)
        dlg.columnconfigure(0, weight=1)
        dlg.rowconfigure(2, weight=1)   # body 占满

        self._build_header(dlg)
        self._build_action_bar(dlg)
        self._build_body(dlg)
        self._build_statusbar(dlg)
        self.refresh_account()   # 状态栏就绪后再刷新账号区 (会写 status_var)

        dlg.protocol("WM_DELETE_WINDOW", self.close)
        dlg.bind("<Escape>", lambda e: self.close())
        try:
            dlg.grab_set()
        except Exception:
            pass

    # ── row0 顶部：账号区 + 标题 + 副标题 + 搜索 + 分类 + 标签 ──
    def _build_header(self, dlg):
        header = tkinter.Frame(dlg, bg=themed("bgc"))
        header.grid(row=0, column=0, sticky="ew",
                    padx=gap("sp_md"), pady=(gap("sp_md"), gap("gap_tight")))
        header.columnconfigure(3, weight=1)
        self._reg(header, lambda: {"bg": themed("bgc")})

        # 账号区 (登录状态 / 当前账户)
        self.avatar_btn = tkinter.Label(
            header, text="登录", font=FONT_SMALL_BOLD, cursor="hand2",
            padx=sp("sp_sm"), pady=sp("sp_xs"))
        self._reg(self.avatar_btn, lambda: {
            "bg": themed("acl"), "fg": themed("ac")})
        self.avatar_btn.grid(row=0, column=0, rowspan=2, sticky="w",
                             padx=(0, gap("sp_md")))
        self.avatar_btn.bind("<Button-1>", lambda e: self._post_account_menu(e))

        title = tkinter.Label(header, text="脚本市场", font=FONT_TITLE)
        self._reg(title, lambda: {"bg": themed("bgc"), "fg": themed("fgt")})
        title.grid(row=0, column=1, sticky="w")

        self.account_lbl = tkinter.Label(header, text="", font=FONT_TINY)
        self._reg(self.account_lbl, lambda: {
            "bg": themed("bgc"), "fg": themed("fgm")})
        self.account_lbl.grid(row=1, column=1, sticky="w")

        # 搜索框 (输入即筛)
        self.search_var = tkinter.StringVar()
        entry = tkinter.Entry(
            header, textvariable=self.search_var, font=FONT_BODY,
            relief="solid", bd=1)
        self._reg(entry, lambda: {
            "bg": themed("ebg"), "fg": themed("fgb"),
            "insertbackground": themed("fgt")})
        entry.grid(row=0, column=3, rowspan=2, sticky="ew", padx=(gap("sp_md"), gap("sp_sm")))
        self.search_entry = entry

        # 分类下拉
        self.cat_var = tkinter.StringVar(value="全部")
        self.cat_combo = ttk.Combobox(
            header, textvariable=self.cat_var, width=8,
            values=CATEGORIES, state="readonly")
        self.cat_combo.grid(row=0, column=4, rowspan=2)

        # 标签筛选 (下拉多选)
        self.tag_btn = tkinter.Menubutton(
            header, text="标签 ▾", font=FONT_SMALL, relief="raised", bd=1,
            cursor="hand2", padx=sp("sp_sm"), pady=sp("sp_xs"))
        self._reg(self.tag_btn, lambda: {
            "bg": themed("bgc"), "fg": themed("fgb"),
            "activebackground": themed("acl"), "activeforeground": themed("fgt")})
        self.tag_btn.grid(row=0, column=5, rowspan=2, padx=(gap("sp_sm"), 0))
        self.tag_menu = tkinter.Menu(self.tag_btn, tearoff=0)
        self.tag_btn.configure(menu=self.tag_menu)

        self.search_var.trace_add("write", lambda *a: self.rebuild())
        self.cat_var.trace_add("write", lambda *a: self._on_category_change())

        # 副标题提示 (置于标题行右侧留白处)
        sub = tkinter.Label(header, text="浏览社区共享的自动化脚本", font=FONT_SMALL)
        self._reg(sub, lambda: {"bg": themed("bgc"), "fg": themed("fgm")})
        sub.grid(row=2, column=1, columnspan=5, sticky="w", pady=(sp("sp_xs"), 0))

    # ── row1 操作栏：刷新 / 上传(占位) ──
    def _build_action_bar(self, dlg):
        bar = tkinter.Frame(dlg, bg=themed("bgc"))
        bar.grid(row=1, column=0, sticky="ew",
                 padx=gap("sp_md"), pady=(0, gap("gap_tight")))
        self._reg(bar, lambda: {"bg": themed("bgc")})

        self._btn_refresh = _btn(bar, "刷新", lambda: self._load(force=True),
                                 tip="重新从远端拉取脚本索引")
        self._btn_refresh.pack(side="left")

        self._btn_upload = _btn(bar, "上传脚本", self._on_upload_clicked,
                                tip="分享你的脚本到市场 (Phase 3 接入)")
        self._btn_upload.pack(side="left", padx=(gap("sp_sm"), 0))

        self.count_lbl = tkinter.Label(bar, text="", font=FONT_TINY)
        self._reg(self.count_lbl, lambda: {
            "bg": themed("bgc"), "fg": themed("fgm")})
        self.count_lbl.pack(side="right")

    # ── row2 主体：四态容器 (同一网格位切换) ──
    def _build_body(self, dlg):
        body = tkinter.Frame(dlg, bg=themed("bgc"))
        body.grid(row=2, column=0, sticky="nsew",
                  padx=gap("sp_md"), pady=(0, gap("gap_tight")))
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        self._reg(body, lambda: {"bg": themed("bgc")})
        self.body = body

        # 加载中
        self.frm_loading = tkinter.Frame(body, bg=themed("bgc"))
        self._reg(self.frm_loading, lambda: {"bg": themed("bgc")})
        self.frm_loading.grid(row=0, column=0, sticky="nsew")
        self.frm_loading.columnconfigure(0, weight=1)
        self.frm_loading.rowconfigure(0, weight=1)
        inner_load = tkinter.Frame(self.frm_loading, bg=themed("bgc"))
        self._reg(inner_load, lambda: {"bg": themed("bgc")})
        inner_load.grid(row=0, column=0)
        lbl_load = tkinter.Label(inner_load, text=_LOADING_TEXT, font=FONT_BODY)
        self._reg(lbl_load, lambda: {"bg": themed("bgc"), "fg": themed("fgm")})
        lbl_load.pack(pady=(0, gap("sp_sm")))
        self.load_bar = ttk.Progressbar(inner_load, mode="indeterminate",
                                        length=tk_px(220))
        self.load_bar.pack()

        # 空态
        self.frm_empty = tkinter.Frame(body, bg=themed("bgc"))
        self._reg(self.frm_empty, lambda: {"bg": themed("bgc")})
        self.frm_empty.grid(row=0, column=0, sticky="nsew")
        self.frm_empty.columnconfigure(0, weight=1)
        self.frm_empty.rowconfigure(0, weight=1)
        inner_empty = tkinter.Frame(self.frm_empty, bg=themed("bgc"))
        self._reg(inner_empty, lambda: {"bg": themed("bgc")})
        inner_empty.grid(row=0, column=0)
        lbl_empty = tkinter.Label(inner_empty, text="没有找到匹配的脚本",
                                  font=FONT_BODY)
        self._reg(lbl_empty, lambda: {"bg": themed("bgc"), "fg": themed("fgm")})
        lbl_empty.pack(pady=(0, gap("sp_sm")))
        _btn(inner_empty, "清除筛选", self._clear_filter).pack()

        # 错误态
        self.frm_error = tkinter.Frame(body, bg=themed("bgc"))
        self._reg(self.frm_error, lambda: {"bg": themed("bgc")})
        self.frm_error.grid(row=0, column=0, sticky="nsew")
        self.frm_error.columnconfigure(0, weight=1)
        self.frm_error.rowconfigure(0, weight=1)
        inner_err = tkinter.Frame(self.frm_error, bg=themed("bgc"))
        self._reg(inner_err, lambda: {"bg": themed("bgc")})
        inner_err.grid(row=0, column=0)
        lbl_err = tkinter.Label(inner_err, text="脚本库加载失败", font=FONT_BODY)
        self._reg(lbl_err, lambda: {"bg": themed("bgc"), "fg": themed("err")})
        lbl_err.pack(pady=(0, gap("sp_xs")))
        self.err_detail = tkinter.Label(
            inner_err, text="", font=FONT_SMALL, wraplength=tk_px(420),
            justify="center")
        self._reg(self.err_detail, lambda: {
            "bg": themed("bgc"), "fg": themed("fgm")})
        self.err_detail.pack(pady=(0, gap("sp_sm")))
        row_err = tkinter.Frame(inner_err, bg=themed("bgc"))
        self._reg(row_err, lambda: {"bg": themed("bgc")})
        row_err.pack()
        _btn(row_err, "重试", lambda: self._load(force=True)).pack(side="left")
        _btn(row_err, "使用离线脚本库", self._use_builtin).pack(
            side="left", padx=(gap("sp_sm"), 0))

        # 正常列表
        self.frm_list = tkinter.Frame(
            body, bg=themed("bgc"),
            highlightbackground=themed("bd"), highlightthickness=1)
        self.frm_list.grid(row=0, column=0, sticky="nsew")
        self.frm_list.columnconfigure(0, weight=1)
        self.frm_list.rowconfigure(0, weight=1)
        self._reg(self.frm_list, lambda: {
            "bg": themed("bgc"), "highlightbackground": themed("bd")})

        self.canvas = tkinter.Canvas(self.frm_list, highlightthickness=0, bd=0)
        self._reg(self.canvas, lambda: {"bg": themed("bgc")})
        self.scrollbar = tkinter.Scrollbar(self.frm_list, orient="vertical",
                                           command=self.canvas.yview)
        self._reg(self.scrollbar, lambda: {
            "bg": themed("bd"), "troughcolor": themed("logbg"),
            "activebackground": themed("fgm")})
        self.inner = tkinter.Frame(self.canvas, bg=themed("bgc"))
        self._reg(self.inner, lambda: {"bg": themed("bgc")})
        self.inner.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self._win_id = self.canvas.create_window((0, 0), window=self.inner,
                                                 anchor="nw")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")
        self.canvas.bind("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind("<Configure>", self._on_canvas_configure)

        self._set_state("loading")

    # ── row3 状态栏 ──
    def _build_statusbar(self, dlg):
        bar = tkinter.Frame(dlg, bg=themed("bgc"))
        bar.grid(row=3, column=0, sticky="ew",
                 padx=gap("sp_md"), pady=(0, gap("sp_md")))
        bar.columnconfigure(0, weight=1)
        self._reg(bar, lambda: {"bg": themed("bgc")})

        self.status_var = tkinter.StringVar(value="")
        self.status_lbl = tkinter.Label(bar, textvariable=self.status_var,
                                        font=FONT_TINY, anchor="w")
        self._reg(self.status_lbl, lambda: {
            "bg": themed("bgc"), "fg": themed("fgm")})
        self.status_lbl.grid(row=0, column=0, sticky="ew")

        self.progress = ttk.Progressbar(bar, mode="determinate",
                                        length=tk_px(160))
        self.progress.grid(row=0, column=1, sticky="e")
        self.progress.grid_remove()

    # ────────────────────────────────────────────────────────────────
    # 四态切换
    # ────────────────────────────────────────────────────────────────
    def _set_state(self, name):
        self._state = name
        frames = {"loading": self.frm_loading, "empty": self.frm_empty,
                  "error": self.frm_error, "list": self.frm_list}
        for key, frm in frames.items():
            try:
                if key == name:
                    frm.grid()
                else:
                    frm.grid_remove()
            except Exception:
                pass
        if name == "loading":
            try:
                self.load_bar.start(12)
            except Exception:
                pass
        else:
            try:
                self.load_bar.stop()
            except Exception:
                pass

    def state_name(self):
        """当前状态名 (供自测断言)。"""
        return self._state

    # ────────────────────────────────────────────────────────────────
    # 数据加载
    # ────────────────────────────────────────────────────────────────
    def _load(self, force=True):
        self._set_state("loading")
        self.status_var.set(_LOADING_TEXT)

        def work():
            scripts, err = None, None
            try:
                # 强制在线 (allow_builtin_fallback=False) 以区分「真在线数据」
                # 与「离线回退」，使错误态可达 (设计 §3.4)。
                scripts = mkt.fetch_index(force_refresh=True,
                                          allow_builtin_fallback=False)
            except Exception as e:
                err = str(e)
            if self._alive:
                self.dlg.after(0, lambda: self._on_loaded(scripts, err))

        threading.Thread(target=work, daemon=True).start()

    def _on_loaded(self, scripts, err):
        if not self._alive:
            return
        if err:
            self._error_msg = err
            self.err_detail.configure(text=err)
            self._set_state("error")
            self.status_var.set("脚本库加载失败")
            self.count_lbl.configure(text="")
            return
        self._all_scripts = list(scripts or [])
        self._build_tag_menu()
        self.rebuild()
        self._maybe_auto_check_update()

    # ────────────────────────────────────────────────────────────────
    # 已装脚本更新自检 (后台, 从简呈现)
    # ────────────────────────────────────────────────────────────────
    def _maybe_auto_check_update(self):
        """按 state.MARKET_AUTO_CHECK_UPDATE 在后台查已装脚本更新。

        全程 daemon 线程, 绝不阻塞主线程; 无网络/无已装脚本/异常均静默。
        结果呈现从简: log1 记录 + 状态栏一行提示 (不新增 UI)。
        """
        try:
            if not getattr(state, "MARKET_AUTO_CHECK_UPDATE", True):
                return
        except Exception:
            return

        def work():
            try:
                updates = _collect_updates(self._save_dir())
            except Exception:
                updates = []
            if not updates:
                return
            try:
                log1("脚本市场: {} 个已装脚本可更新".format(len(updates)))
            except Exception:
                pass
            if not self._alive:
                return
            msg = "{} 个已装脚本可更新".format(len(updates))
            try:
                self.dlg.after(0, lambda: self.status_var.set(msg))
            except Exception:
                pass

        threading.Thread(target=work, daemon=True).start()

    def _use_builtin(self):
        """错误态下的离线回退：直接载入内置脚本库。"""
        try:
            self._all_scripts = mkt._load_builtin_scripts()
        except Exception as e:
            self.err_detail.configure(text=str(e))
            return
        self._build_tag_menu()
        self.rebuild()

    # ────────────────────────────────────────────────────────────────
    # 筛选
    # ────────────────────────────────────────────────────────────────
    def _filtered(self):
        scripts = list(self._all_scripts)
        cat = self.cat_var.get()
        if cat and cat != "全部":
            scripts = [s for s in scripts if getattr(s, "category", "") == cat]
        kw = (self.search_var.get() or "").strip().lower()
        if kw:
            def _hit(s):
                text = "{} {} {} {}".format(
                    getattr(s, "name", ""), getattr(s, "description", ""),
                    getattr(s, "category", ""),
                    " ".join(getattr(s, "tags", []) or []))
                return kw in text.lower()
            scripts = [s for s in scripts if _hit(s)]
        if self._tag_selected:
            def _tag_hit(s):
                tags = set(getattr(s, "tags", []) or [])
                return bool(tags & self._tag_selected)
            scripts = [s for s in scripts if _tag_hit(s)]
        return scripts

    def _on_category_change(self):
        try:
            state.MARKET_LAST_CATEGORY = self.cat_var.get()
        except Exception:
            pass
        self.rebuild()

    def _clear_filter(self):
        self.search_var.set("")
        self.cat_var.set("全部")
        self._tag_selected = set()
        self.rebuild()

    def _build_tag_menu(self):
        """由当前脚本集合生成标签多选菜单。"""
        try:
            self.tag_menu.delete(0, "end")
        except Exception:
            pass
        tags = []
        for s in self._all_scripts:
            for t in (getattr(s, "tags", []) or []):
                if t not in tags:
                    tags.append(t)
        if not tags:
            self.tag_menu.add_command(label="(无标签)", state="disabled")
            return
        for t in tags[:20]:
            var = tkinter.BooleanVar(value=(t in self._tag_selected))
            self.tag_menu.add_checkbutton(
                label=t, variable=var,
                command=lambda t=t, var=var: self._toggle_tag(t, var))

    def _toggle_tag(self, tag, var):
        if var.get():
            self._tag_selected.add(tag)
        else:
            self._tag_selected.discard(tag)
        self.rebuild()

    # ────────────────────────────────────────────────────────────────
    # 卡片重建
    # ────────────────────────────────────────────────────────────────
    def rebuild(self):
        """按当前筛选重建卡片列表 (同时视情况切换 list/empty 态)。"""
        for w in self._card_widgets:
            try:
                w.destroy()
            except Exception:
                pass
        self._card_widgets = []
        self._wrap_labels = []
        try:
            for w in self.inner.winfo_children():
                w.destroy()
        except Exception:
            pass

        scripts = self._filtered()
        if not scripts:
            self._set_state("empty")
            self.count_lbl.configure(text="0 个脚本")
            self.status_var.set("共 0 个脚本")
            return

        for s in scripts:
            self._build_card(s)
        self._set_state("list")
        self.count_lbl.configure(text="{} 个脚本".format(len(scripts)))
        self._update_status(len(scripts))
        try:
            self.canvas.update_idletasks()
            self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        except Exception:
            pass

    def _update_status(self, shown):
        total = len(self._all_scripts)
        parts = ["共 {} 个脚本".format(total)]
        if shown != total:
            parts.append("筛选后 {} 个".format(shown))
        logged = self._logged_providers()
        if logged:
            prov = self._active_provider(logged)
            name = getattr(state, "MARKET_USERNAME", "") or prov
            parts.append("已登录 {}: {}".format(prov, name))
        var = getattr(self, "status_var", None)
        if var is not None:
            var.set(" · ".join(parts))

    def _build_card(self, s):
        card = create_card(self.inner)
        card.pack(fill="x", padx=gap("gap_tight"), pady=gap("gap_tight"))
        self._card_widgets.append(card)

        hover_widgets = [card]

        # 分类色条
        bar = tkinter.Frame(card, width=sp("bar_h"),
                            bg=_category_color(getattr(s, "category", "")))
        bar.pack(side="left", fill="y")

        # 图标
        icon = tkinter.Label(card, text=_category_icon(s), font=FONT_ICON_LG,
                             bg=themed("bgc"),
                             fg=_category_color(getattr(s, "category", "")))
        icon.pack(side="left", padx=(gap("sp_md"), gap("sp_sm")),
                  pady=gap("sp_md"))
        hover_widgets.append(icon)

        # 操作列 (先 pack 右侧，稳定布局)
        act = tkinter.Frame(card, bg=themed("bgc"))
        act.pack(side="right", padx=gap("sp_md"), pady=gap("sp_sm"))
        hover_widgets.append(act)

        _btn(act, "导入", lambda ss=s: self._do_import(ss),
             themed("sc"), _ON_ACCENT).pack(side="top", fill="x")
        _btn(act, "详情", lambda ss=s: self._show_detail(ss)).pack(
            side="top", fill="x", pady=(gap("gap_tight"), 0))
        if getattr(s, "is_package", False):
            badge = tkinter.Label(act, text="包", font=FONT_TINY,
                                  bg=themed("acl"), fg=themed("ac"),
                                  padx=sp("sp_xs"), pady=0)
            badge.pack(side="top", pady=(gap("gap_tight"), 0))

        # 主列
        main = tkinter.Frame(card, bg=themed("bgc"))
        main.pack(side="left", fill="both", expand=True, pady=gap("sp_sm"))
        hover_widgets.append(main)

        title_row = tkinter.Frame(main, bg=themed("bgc"))
        title_row.pack(fill="x")
        hover_widgets.append(title_row)
        name = tkinter.Label(title_row, text=getattr(s, "name", ""),
                             font=FONT_TITLE, bg=themed("bgc"), fg=themed("fgt"))
        name.pack(side="left")
        hover_widgets.append(name)
        ver = tkinter.Label(title_row, text="v{}".format(getattr(s, "version", "")),
                            font=FONT_TINY, bg=themed("acl"), fg=themed("fgm"),
                            padx=sp("sp_xs"))
        ver.pack(side="left", padx=(gap("sp_sm"), 0))
        cat = tkinter.Label(title_row,
                            text="[{}]".format(getattr(s, "category", "")),
                            font=FONT_TINY, bg=themed("bgc"), fg=themed("ac"),
                            padx=sp("sp_xs"))
        cat.pack(side="left", padx=(gap("gap_tight"), 0))

        desc = tkinter.Label(main, text=getattr(s, "description", ""),
                             font=FONT_SMALL, bg=themed("bgc"), fg=themed("fgb"),
                             wraplength=tk_px(380), justify="left", anchor="w")
        desc.pack(fill="x", pady=(sp("sp_xs"), 0))
        hover_widgets.append(desc)
        self._wrap_labels.append(desc)

        meta = tkinter.Frame(main, bg=themed("bgc"))
        meta.pack(fill="x", pady=(sp("sp_xs"), 0))
        hover_widgets.append(meta)
        author = tkinter.Label(meta, text=getattr(s, "author", ""),
                               font=FONT_TINY, bg=themed("bgc"), fg=themed("fgm"))
        author.pack(side="left")
        hover_widgets.append(author)
        stars = tkinter.Label(
            meta, text="{} {} ({})".format(
                _star_text(getattr(s, "rating", 0)),
                getattr(s, "rating", 0), getattr(s, "rating_count", 0)),
            font=FONT_SMALL, bg=themed("bgc"), fg=themed("wn"))
        stars.pack(side="left", padx=(gap("sp_md"), 0))
        hover_widgets.append(stars)
        dl = tkinter.Label(meta, text="{} 下载".format(getattr(s, "downloads", 0)),
                           font=FONT_TINY, bg=themed("bgc"), fg=themed("fgm"))
        dl.pack(side="right")
        hover_widgets.append(dl)

        tags = list(getattr(s, "tags", []) or [])
        if tags:
            tag_row = tkinter.Frame(main, bg=themed("bgc"))
            tag_row.pack(fill="x", pady=(sp("sp_xs"), 0))
            hover_widgets.append(tag_row)
            for t in tags[:4]:
                tkinter.Label(tag_row, text=t, font=FONT_TINY,
                              bg=themed("acl"), fg=themed("fgb"),
                              padx=sp("sp_xs"), pady=0).pack(
                    side="left", padx=(0, gap("gap_tight")))

        self._bind_hover(hover_widgets)

    def _bind_hover(self, widgets):
        def _enter(_e):
            for w in widgets:
                try:
                    w.configure(bg=themed("acl"))
                except Exception:
                    pass

        def _leave(_e):
            for w in widgets:
                try:
                    w.configure(bg=themed("bgc"))
                except Exception:
                    pass

        for w in widgets:
            try:
                w.bind("<Enter>", _enter, add="+")
                w.bind("<Leave>", _leave, add="+")
            except Exception:
                pass

    # ────────────────────────────────────────────────────────────────
    # 详情弹窗 (设计 §3.5)
    # ────────────────────────────────────────────────────────────────
    def _show_detail(self, s):
        d = tkinter.Toplevel(self.dlg)
        d.title(getattr(s, "name", "详情"))
        d.geometry("{}x{}+{}+{}".format(
            tk_px(460), tk_px(400), tk_px(420), tk_px(140)))
        d.transient(self.dlg)
        d.configure(bg=themed("bgc"))
        _try_set_icon(d)

        head = tkinter.Frame(d, bg=themed("bgc"))
        head.pack(fill="x", padx=gap("sp_lg"), pady=(gap("sp_lg"), 0))
        tkinter.Label(head, text=_category_icon(s), font=FONT_ICON_LG,
                      bg=themed("bgc"),
                      fg=_category_color(getattr(s, "category", ""))).pack(side="left")
        tkinter.Label(head, text=getattr(s, "name", ""), font=FONT_TITLE,
                      bg=themed("bgc"), fg=themed("fgt")).pack(
            side="left", padx=(gap("sp_sm"), 0))

        meta = tkinter.Label(
            d, text="作者: {} | 版本: {} | 分类: {}".format(
                getattr(s, "author", ""), getattr(s, "version", ""),
                getattr(s, "category", "")),
            font=FONT_SMALL, bg=themed("bgc"), fg=themed("fgm"))
        meta.pack(pady=(gap("sp_sm"), 0))

        tkinter.Label(d, text=getattr(s, "description", ""), font=FONT_BODY,
                      bg=themed("bgc"), fg=themed("fgb"),
                      wraplength=tk_px(400), justify="left").pack(
            padx=gap("sp_lg"), pady=(gap("sp_sm"), gap("gap_tight")))

        tkinter.Label(
            d, text="评分: {} {} ({}) | 下载: {}".format(
                _star_text(getattr(s, "rating", 0)),
                getattr(s, "rating", 0), getattr(s, "rating_count", 0),
                getattr(s, "downloads", 0)),
            font=FONT_SMALL, bg=themed("bgc"), fg=themed("wn")).pack()

        tags = list(getattr(s, "tags", []) or [])
        if tags:
            tag_row = tkinter.Frame(d, bg=themed("bgc"))
            tag_row.pack(pady=(gap("sp_sm"), 0))
            for t in tags[:6]:
                tkinter.Label(tag_row, text=t, font=FONT_TINY,
                              bg=themed("acl"), fg=themed("fgb"),
                              padx=sp("sp_xs"), pady=0).pack(
                    side="left", padx=(0, gap("gap_tight")))

        requires = list(getattr(s, "requires", []) or [])
        if requires:
            tkinter.Label(d, text="需要: {} (请自行确认已安装)".format(
                ", ".join(requires)), font=FONT_SMALL, bg=themed("bgc"),
                fg=themed("wn")).pack(pady=(gap("sp_sm"), 0))

        if getattr(s, "is_package", False):
            size = getattr(s, "size", 0) or 0
            tkinter.Label(
                d, text="脚本包: {}  ({} KB)".format(
                    getattr(s, "pkg", ""), int(size / 1024)),
                font=FONT_TINY, bg=themed("bgc"), fg=themed("fgm"),
                wraplength=tk_px(400)).pack(pady=(gap("gap_tight"), 0))

        # 预览占位
        ph = tkinter.Frame(d, bg=themed("bg"), bd=0,
                           highlightbackground=themed("bd"), highlightthickness=1,
                           height=tk_px(90), width=tk_px(400))
        ph.pack(fill="x", padx=gap("sp_lg"), pady=gap("sp_sm"))
        ph.pack_propagate(False)
        tkinter.Label(ph, text="（无可预览图片）", font=FONT_TINY,
                      bg=themed("bg"), fg=themed("fgm")).pack(expand=True)

        btns = tkinter.Frame(d, bg=themed("bgc"))
        btns.pack(pady=(0, gap("sp_lg")))
        _btn(btns, "导入到编辑器",
             lambda: (d.destroy(), self._do_import(s)),
             themed("sc"), _ON_ACCENT).pack(side="left")
        url = mkt.resolve_download_url(s) if hasattr(mkt, "resolve_download_url") else ""
        _btn(btns, "在浏览器打开", lambda: self._open_url(url or mkt.MARKETPLACE_REPO)).pack(
            side="left", padx=(gap("sp_sm"), 0))

    def _open_url(self, url):
        try:
            if url:
                webbrowser.open(url)
        except Exception as e:
            log1("打开链接失败: {}".format(e), "warning")

    # ────────────────────────────────────────────────────────────────
    # 安装 / 下载
    # ────────────────────────────────────────────────────────────────
    def _save_dir(self):
        try:
            return os.path.dirname(state.CONFIG_PATH)
        except Exception:
            return os.getcwd()

    def _do_import(self, s):
        if self._download_active:
            return
        self._download_active = True
        self.status_var.set("正在下载: {}".format(getattr(s, "name", "")))
        try:
            self.progress.configure(value=0, mode="determinate")
            self.progress.grid()
        except Exception:
            pass

        def _prog(got, total):
            if not self._alive:
                return
            def _apply():
                try:
                    if total:
                        self.progress.configure(maximum=total, value=got)
                    else:
                        self.progress.configure(mode="indeterminate")
                        self.progress.start(12)
                except Exception:
                    pass
            try:
                self.dlg.after(0, _apply)
            except Exception:
                pass

        def work():
            try:
                fp = mkt.download_script(getattr(s, "id", ""), self._save_dir(),
                                         progress=_prog)
                if self._alive:
                    self.dlg.after(0, lambda: self._on_install_done(fp))
            except Exception as e:
                err = str(e)
                if self._alive:
                    self.dlg.after(0, lambda: self._on_install_error(err))

        threading.Thread(target=work, daemon=True).start()

    def _stop_progress(self):
        try:
            self.progress.stop()
        except Exception:
            pass
        try:
            self.progress.grid_remove()
        except Exception:
            pass
        self._download_active = False

    def _on_install_done(self, fp):
        self._stop_progress()
        self.status_var.set("已导入: {}".format(os.path.basename(fp)))
        if callable(self.on_install):
            try:
                self.on_install(fp)
            except Exception as e:
                log1("安装回调失败: {}".format(e), "error")
        _toast(self.parent, "脚本已导入: {}".format(os.path.basename(fp)),
               "success")
        self.close()

    def _on_install_error(self, err):
        self._stop_progress()
        self.status_var.set("导入失败: {}".format(err))
        _toast(self.parent, "导入失败: {}".format(err), "error")

    def import_script(self, script_id):
        """按 id 触发导入 (供自测/外部调用)。"""
        for s in self._all_scripts:
            if getattr(s, "id", "") == script_id:
                self._do_import(s)
                return True
        return False

    # ────────────────────────────────────────────────────────────────
    # 账号区 (GitHub / Gitee 登录入口 — 本批次范围)
    # ────────────────────────────────────────────────────────────────
    def _logged_providers(self):
        try:
            return accounts.list_logged_in()
        except Exception:
            return []

    def _active_provider(self, logged):
        prov = (getattr(state, "MARKET_PROVIDER", "") or "").lower()
        if prov in logged:
            return prov
        return logged[0] if logged else ""

    def refresh_account(self):
        """刷新账号区 (无网络，仅凭据库探测)。"""
        logged = self._logged_providers()
        if logged:
            prov = self._active_provider(logged)
            name = getattr(state, "MARKET_USERNAME", "") or prov
            initial = (name[:1] or "?").upper()
            self.avatar_btn.configure(text="{} {}".format(initial, name))
            self.account_lbl.configure(text="已登录 {}".format(prov))
        else:
            self.avatar_btn.configure(text="登录")
            self.account_lbl.configure(text="未登录 · 点击登录以分享脚本")
        self._update_status(len(self._filtered()) if self._all_scripts else 0)

    def _post_account_menu(self, event):
        logged = self._logged_providers()
        menu = tkinter.Menu(self.dlg, tearoff=0)
        menu.add_command(label="使用 Gitee 登录",
                         command=lambda: self._open_token_dialog("gitee"))
        menu.add_command(label="使用 GitHub 登录",
                         command=lambda: self._open_token_dialog("github"))
        if logged:
            prov = self._active_provider(logged)
            name = getattr(state, "MARKET_USERNAME", "") or prov
            menu.add_separator()
            menu.add_command(label="当前账户: {}".format(name), state="disabled")
            menu.add_command(label="注销 {}".format(prov),
                             command=lambda p=prov: self._logout(p))
        menu.add_separator()
        menu.add_command(label="如何获取 Token？",
                         command=lambda: self._open_url(_TOKEN_URL["gitee"]))
        menu.add_command(label="上传脚本 (Phase 3 接入)",
                         command=self._on_upload_clicked)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            try:
                menu.grab_release()
            except Exception:
                pass

    def _open_token_dialog(self, provider):
        d = tkinter.Toplevel(self.dlg)
        d.title("登录 {}".format(provider))
        d.transient(self.dlg)
        d.configure(bg=themed("bgc"))
        d.resizable(False, False)
        _try_set_icon(d)

        tkinter.Label(d, text="Personal Access Token ({})".format(provider),
                      font=FONT_BODY, bg=themed("bgc"), fg=themed("fgt")).pack(
            padx=gap("sp_lg"), pady=(gap("sp_lg"), gap("gap_tight")))
        tkinter.Label(d, text="仅存本机 Windows 凭据库，不上传、不写入配置文件。",
                      font=FONT_TINY, bg=themed("bgc"), fg=themed("fgm")).pack(
            padx=gap("sp_lg"))

        var = tkinter.StringVar()
        ent = tkinter.Entry(d, textvariable=var, show="*", font=FONT_BODY,
                            relief="solid", bd=1, width=44,
                            bg=themed("ebg"), fg=themed("fgb"),
                            insertbackground=themed("fgt"))
        ent.pack(padx=gap("sp_lg"), pady=gap("sp_sm"))
        ent.focus_set()

        def _help():
            self._open_url(_TOKEN_URL.get(provider, ""))

        btns = tkinter.Frame(d, bg=themed("bgc"))
        btns.pack(pady=(0, gap("sp_lg")))

        def _submit():
            token = var.get().strip()
            if not token:
                messagebox.showwarning("提示", "请输入 Token", parent=d)
                return
            d.destroy()
            self._do_login(provider, token)

        _btn(btns, "确定", _submit, themed("sc"), _ON_ACCENT).pack(side="left")
        _btn(btns, "取消", d.destroy).pack(side="left", padx=(gap("sp_sm"), 0))
        _btn(btns, "获取 Token", _help).pack(side="left", padx=(gap("sp_sm"), 0))
        ent.bind("<Return>", lambda e: _submit())

    def _do_login(self, provider, token):
        self.status_var.set("正在校验 {} 账号…".format(provider))

        def work():
            try:
                info = accounts.login(provider, token)
            except Exception as e:
                err = str(e)
                if self._alive:
                    self.dlg.after(0, lambda: self._on_login_error(err))
                return
            if self._alive:
                self.dlg.after(0, lambda: self._on_login_ok(provider, info))

        threading.Thread(target=work, daemon=True).start()

    def _on_login_ok(self, provider, info):
        try:
            state.MARKET_PROVIDER = provider
            state.MARKET_USERNAME = info.get("login", "") or info.get("name", "")
            state.save_config()
        except Exception:
            pass
        self.refresh_account()
        _toast(self.parent, "已登录 {}: {}".format(
            provider, info.get("login", "")), "success")

    def _on_login_error(self, err):
        self.status_var.set("登录失败")
        try:
            messagebox.showerror("登录失败", err, parent=self.dlg)
        except Exception:
            pass

    def _logout(self, provider):
        try:
            accounts.logout(provider)
        except Exception as e:
            log1("注销失败: {}".format(e), "warning")
        self.refresh_account()
        _toast(self.parent, "已注销 {}".format(provider), "info")
    def _on_upload_clicked(self):
        """打开上传向导 (批次 3 接入，已替换批次 2 的 Phase 3 占位回调)。

        向导在 step4 内联处理「未登录先引导登录」，故此处直接打开即可；
        异常一律兜底提示，绝不抛未实现异常。
        """
        try:
            UploadWizard(self)
        except Exception as e:
            log1("打开上传向导失败: {}".format(e), "error")
            try:
                messagebox.showerror("上传脚本", "无法打开上传向导:\n{}".format(e),
                                     parent=self.dlg)
            except Exception:
                pass

    # ────────────────────────────────────────────────────────────────
    # 自适应 / 主题
    # ────────────────────────────────────────────────────────────────
    def _on_mousewheel(self, event):
        try:
            self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        except Exception:
            pass

    def _on_canvas_configure(self, event):
        try:
            self.canvas.itemconfigure(self._win_id, width=event.width)
        except Exception:
            pass
        wrap = max(tk_px(200), event.width - tk_px(220))
        for lbl in list(self._wrap_labels):
            try:
                lbl.configure(wraplength=wrap)
            except Exception:
                pass

    def retheme(self):
        """主题切换后：重刷固定控件颜色 + 重建卡片 (颜色为 themed() 实时值)。"""
        self._reg_map()
        try:
            self.dlg.configure(bg=themed("bgc"))
        except Exception:
            pass
        for key, frm in (("loading", self.frm_loading), ("empty", self.frm_empty),
                         ("error", self.frm_error), ("list", self.frm_list)):
            try:
                frm.configure(bg=themed("bgc"))
            except Exception:
                pass
        self.rebuild()

    # ────────────────────────────────────────────────────────────────
    # 关闭 / 自测辅助
    # ────────────────────────────────────────────────────────────────
    def cards_count(self):
        return len(self._card_widgets)

    def close(self):
        global _WINDOW
        self._alive = False
        try:
            self._stop_progress()
        except Exception:
            pass
        try:
            self.load_bar.stop()
        except Exception:
            pass
        try:
            self.dlg.grab_release()
        except Exception:
            pass
        try:
            self.dlg.destroy()
        except Exception:
            pass
        if _WINDOW is self:
            _WINDOW = None


# ══════════════════════════════════════════════════════════════════════
# 上传向导 (设计 §3.7，5 步)
# ══════════════════════════════════════════════════════════════════════

_UPLOAD_STAGE_TEXT = {
    "verify": "校验登录…", "branch": "创建分支…", "upload_pkg": "上传脚本包…",
    "upload_index": "更新 index.json…", "pull": "创建 PR…", "done": "完成",
}


class UploadWizard(object):
    """上传向导：选择 .xls → 元数据 → 打包预览 → 登录校验 → 上传。

    5 步单窗口多步，后台线程执行耗时动作、dlg.after 回主线程刷新 UI。
    前端校验（validate_meta + check_script_commands）实时拦截非法提交。
    """

    STEPS = ("选择脚本", "填写元数据", "打包预览", "登录校验", "上传")

    def __init__(self, market):
        self.mw = market
        self.dlg = tkinter.Toplevel(market.dlg)
        self.dlg.title("上传脚本到市场 — ACRPA")
        self.dlg.geometry("{}x{}+{}+{}".format(
            tk_px(600), tk_px(560), tk_px(380), tk_px(110)))
        self.dlg.minsize(tk_px(560), tk_px(520))
        self.dlg.transient(market.dlg)
        self.dlg.configure(bg=themed("bgc"))
        _try_set_icon(self.dlg)

        self.step = 1
        self.script_path = ""
        self.images = []
        self.pkg_path = ""
        self.sha256 = ""
        self.manifest = {}
        self.pr_url = ""
        self._busy = False
        self._last_valid = False
        self._cancel_flag = threading.Event()

        self._build()
        self._goto(1)
        try:
            self.dlg.protocol("WM_DELETE_WINDOW", self._close)
        except Exception:
            pass

    # ── 骨架 ──
    def _build(self):
        head = tkinter.Frame(self.dlg, bg=themed("bgc"))
        head.pack(fill="x", padx=gap("sp_lg"), pady=(gap("sp_md"), 0))
        tkinter.Label(head, text="上传脚本到市场", font=FONT_TITLE,
                      bg=themed("bgc"), fg=themed("fgt")).pack(side="left")
        self.step_lbl = tkinter.Label(head, text="", font=FONT_SMALL,
                                      bg=themed("bgc"), fg=themed("fgm"))
        self.step_lbl.pack(side="right")

        self.body = tkinter.Frame(self.dlg, bg=themed("bgc"))
        self.body.pack(fill="both", expand=True, padx=gap("sp_lg"),
                       pady=gap("sp_sm"))

        self.frames = {}
        self.frames[1] = self._build_step1(self.body)
        self.frames[2] = self._build_step2(self.body)
        self.frames[3] = self._build_step3(self.body)
        self.frames[4] = self._build_step4(self.body)
        self.frames[5] = self._build_step5(self.body)

        foot = tkinter.Frame(self.dlg, bg=themed("bgc"))
        foot.pack(fill="x", padx=gap("sp_lg"), pady=(0, gap("sp_md")))
        self.btn_cancel = _btn(foot, "取消", self._close)
        self.btn_cancel.pack(side="left")
        self.btn_next = _btn(foot, "下一步", self._next, themed("sc"), _ON_ACCENT)
        self.btn_next.pack(side="right")
        self.btn_prev = _btn(foot, "上一步", self._prev)
        self.btn_prev.pack(side="right", padx=(0, gap("sp_sm")))

    def _build_step1(self, parent):
        f = tkinter.Frame(parent, bg=themed("bgc"))
        tkinter.Label(f, text="步骤 1 / 选择要上传的脚本 (.xls)", font=FONT_SMALL_BOLD,
                      bg=themed("bgc"), fg=themed("fgt")).pack(anchor="w")
        tkinter.Label(f, text="脚本需为 ACRPA 导出的 .xls；同目录下的 .png 会一并打包。",
                      font=FONT_TINY, bg=themed("bgc"),
                      fg=themed("fgm")).pack(anchor="w",
                                             pady=(gap("gap_tight"), gap("sp_sm")))
        _btn(f, "选择 .xls 文件…", self._pick_script,
             themed("ac"), _ON_ACCENT).pack(anchor="w")
        self.s1_path_lbl = tkinter.Label(f, text="(未选择)", font=FONT_SMALL,
                                         bg=themed("bgc"), fg=themed("fgb"),
                                         wraplength=tk_px(520), justify="left")
        self.s1_path_lbl.pack(anchor="w", pady=(gap("sp_sm"), 0))
        self.s1_img_lbl = tkinter.Label(f, text="", font=FONT_TINY,
                                        bg=themed("bgc"), fg=themed("fgm"))
        self.s1_img_lbl.pack(anchor="w", pady=(gap("gap_tight"), 0))
        return f

    def _build_step2(self, parent):
        f = tkinter.Frame(parent, bg=themed("bgc"))
        tkinter.Label(f, text="步骤 2 / 填写元数据", font=FONT_SMALL_BOLD,
                      bg=themed("bgc"), fg=themed("fgt")).grid(
            row=0, column=0, columnspan=3, sticky="w")

        self.id_var = tkinter.StringVar()
        self.name_var = tkinter.StringVar()
        self.desc_var = tkinter.StringVar()
        self.cat_var = tkinter.StringVar(value=script_package.CATEGORIES[0])
        self.author_var = tkinter.StringVar()
        self.ver_var = tkinter.StringVar(value="1.0.0")
        self.tags_var = tkinter.StringVar()
        self.icon_var = tkinter.StringVar()
        try:
            from version_info import VERSION as _APPVER
        except Exception:
            _APPVER = ""
        self.minver_var = tkinter.StringVar(value=_APPVER or "")

        simple = [
            ("id", self.id_var, "小写字母/数字/下划线，不以 builtin_ 开头"),
            ("名称", self.name_var, "≤60 字符"),
            ("简介", self.desc_var, "≤300 字符"),
            ("作者", self.author_var, "≤40 字符"),
            ("版本", self.ver_var, "MAJOR.MINOR[.PATCH]，如 1.0.0"),
            ("标签", self.tags_var, "逗号分隔，≤8 个"),
            ("图标", self.icon_var, "Emoji 或短字符，可空"),
            ("最低版本", self.minver_var, "min_app_version，可空"),
        ]
        r = 1
        for label, var, hint in simple:
            tkinter.Label(f, text=label, font=FONT_SMALL, bg=themed("bgc"),
                          fg=themed("fgb")).grid(
                row=r, column=0, sticky="w",
                pady=gap("gap_tight"), padx=(0, gap("sp_sm")))
            tkinter.Entry(f, textvariable=var, font=FONT_SMALL, width=40,
                          relief="solid", bd=1, bg=themed("ebg"),
                          fg=themed("fgb"),
                          insertbackground=themed("fgt")).grid(
                row=r, column=1, sticky="ew", pady=gap("gap_tight"))
            tkinter.Label(f, text=hint, font=FONT_TINY, bg=themed("bgc"),
                          fg=themed("fgm")).grid(
                row=r, column=2, sticky="w", padx=(gap("sp_sm"), 0))
            r += 1

        tkinter.Label(f, text="分类", font=FONT_SMALL, bg=themed("bgc"),
                      fg=themed("fgb")).grid(
            row=r, column=0, sticky="w",
            pady=gap("gap_tight"), padx=(0, gap("sp_sm")))
        ttk.Combobox(f, textvariable=self.cat_var, width=12, state="readonly",
                     values=script_package.CATEGORIES).grid(
            row=r, column=1, sticky="w", pady=gap("gap_tight"))
        tkinter.Label(f, text="四选一", font=FONT_TINY, bg=themed("bgc"),
                      fg=themed("fgm")).grid(row=r, column=2, sticky="w",
                                             padx=(gap("sp_sm"), 0))
        r += 1

        tkinter.Label(f, text="依赖", font=FONT_SMALL, bg=themed("bgc"),
                      fg=themed("fgb")).grid(
            row=r, column=0, sticky="w",
            pady=gap("gap_tight"), padx=(0, gap("sp_sm")))
        req_box = tkinter.Frame(f, bg=themed("bgc"))
        req_box.grid(row=r, column=1, columnspan=2, sticky="w",
                     pady=gap("gap_tight"))
        self.req_vars = {}
        for k in script_package.REQUIRES_VALUES:
            v = tkinter.BooleanVar(value=False)
            self.req_vars[k] = v
            ttk.Checkbutton(req_box, text=k, variable=v).pack(
                side="left", padx=(0, gap("sp_sm")))
        r += 1

        self.s2_err = tkinter.Label(f, text="", font=FONT_SMALL,
                                    bg=themed("bgc"), fg=themed("err"),
                                    wraplength=tk_px(430), justify="left")
        self.s2_err.grid(row=r, column=0, columnspan=3, sticky="w",
                         pady=(gap("sp_sm"), 0))
        f.columnconfigure(1, weight=1)

        for v in (self.id_var, self.name_var, self.desc_var, self.cat_var,
                  self.author_var, self.ver_var, self.tags_var, self.icon_var,
                  self.minver_var):
            try:
                v.trace_add("write", lambda *a: self._validate_step2())
            except Exception:
                pass
        return f

    def _build_step3(self, parent):
        f = tkinter.Frame(parent, bg=themed("bgc"))
        tkinter.Label(f, text="步骤 3 / 打包预览", font=FONT_SMALL_BOLD,
                      bg=themed("bgc"), fg=themed("fgt")).pack(anchor="w")
        _btn(f, "打包 (.acrpapkg)", self._do_pack,
             themed("ac"), _ON_ACCENT).pack(anchor="w", pady=gap("sp_sm"))
        self.s3_info = tkinter.Label(f, text="尚未打包", font=FONT_SMALL,
                                     bg=themed("bgc"), fg=themed("fgb"),
                                     justify="left")
        self.s3_info.pack(anchor="w")
        tkinter.Label(f, text="manifest.json 预览:", font=FONT_TINY,
                      bg=themed("bgc"), fg=themed("fgm")).pack(
            anchor="w", pady=(gap("sp_sm"), gap("gap_tight")))
        self.s3_text = tkinter.Text(f, height=12, wrap="word", font=FONT_LOG,
                                    bg=themed("ebg"), fg=themed("fgb"),
                                    relief="solid", bd=1)
        self.s3_text.pack(fill="both", expand=True)
        self.s3_text.configure(state="disabled")
        return f

    def _build_step4(self, parent):
        f = tkinter.Frame(parent, bg=themed("bgc"))
        tkinter.Label(f, text="步骤 4 / 登录校验", font=FONT_SMALL_BOLD,
                      bg=themed("bgc"), fg=themed("fgt")).pack(anchor="w")
        self.s4_status = tkinter.Label(f, text="", font=FONT_SMALL,
                                       bg=themed("bgc"), fg=themed("fgb"),
                                       justify="left", wraplength=tk_px(500))
        self.s4_status.pack(anchor="w", pady=gap("sp_sm"))
        row = tkinter.Frame(f, bg=themed("bgc"))
        row.pack(anchor="w")
        _btn(row, "登录 Gitee", lambda: self.mw._open_token_dialog("gitee"),
             themed("sc"), _ON_ACCENT).pack(side="left")
        _btn(row, "登录 GitHub",
             lambda: self.mw._open_token_dialog("github")).pack(
            side="left", padx=(gap("sp_sm"), 0))
        _btn(row, "刷新状态", self._refresh_account).pack(
            side="left", padx=(gap("sp_sm"), 0))
        tkinter.Label(f, text="Token 仅存本机 Windows 凭据库，不上传、不写入配置文件。",
                      font=FONT_TINY, bg=themed("bgc"),
                      fg=themed("fgm")).pack(anchor="w", pady=(gap("sp_sm"), 0))
        return f

    def _build_step5(self, parent):
        f = tkinter.Frame(parent, bg=themed("bgc"))
        tkinter.Label(f, text="步骤 5 / 上传", font=FONT_SMALL_BOLD,
                      bg=themed("bgc"), fg=themed("fgt")).pack(anchor="w")
        self.s5_stage = tkinter.Label(f, text="准备就绪，点击右下「上传」。",
                                      font=FONT_SMALL, bg=themed("bgc"),
                                      fg=themed("fgb"))
        self.s5_stage.pack(anchor="w", pady=gap("sp_sm"))
        self.s5_bar = ttk.Progressbar(f, mode="determinate",
                                      maximum=len(mup._STAGES),
                                      length=tk_px(360))
        self.s5_bar.pack(anchor="w")
        self.s5_link = tkinter.Label(f, text="", font=FONT_SMALL,
                                     bg=themed("bgc"), fg=themed("ac"),
                                     cursor="hand2", wraplength=tk_px(500),
                                     justify="left")
        self.s5_link.pack(anchor="w", pady=(gap("sp_md"), 0))
        self.s5_link.bind("<Button-1>", lambda e: self._open_pr())
        return f

    # ── 步骤切换 ──
    def _goto(self, n):
        self.step = n
        for k, fr in self.frames.items():
            try:
                if k == n:
                    fr.pack(fill="both", expand=True)
                else:
                    fr.pack_forget()
            except Exception:
                pass
        self.step_lbl.configure(text="步骤 {}/5 · {}".format(n, self.STEPS[n - 1]))
        self.btn_prev.configure(state=("normal" if n > 1 else "disabled"))
        self.btn_next.configure(text=("上传" if n == 5 else "下一步"))
        if n == 2:
            self._validate_step2()
        if n == 4:
            self._refresh_account()
        self._update_footer()

    def _can_next(self):
        if self.step == 1:
            return bool(self.script_path)
        if self.step == 2:
            return bool(self._last_valid)
        if self.step == 3:
            return bool(self.pkg_path)
        if self.step == 4:
            return bool(self.mw._logged_providers())
        if self.step == 5:
            return bool(self.mw._logged_providers())
        return True

    def _update_footer(self):
        try:
            if self._busy:
                self.btn_next.configure(state="disabled")
                return
            self.btn_next.configure(state=("normal" if self._can_next() else "disabled"))
        except Exception:
            pass

    def _prev(self):
        if self.step > 1:
            self._goto(self.step - 1)

    def _next(self):
        if self.pr_url:
            self._close()
            return
        if self.step < 5:
            if not self._can_next():
                return
            self._goto(self.step + 1)
        else:
            self._do_upload()

    # ── step1 ──
    def _pick_script(self):
        try:
            fp = filedialog.askopenfilename(
                title="选择 .xls 脚本",
                filetypes=[("Excel 脚本", "*.xls"), ("All", "*.*")])
        except Exception:
            fp = ""
        if not fp:
            return
        if not fp.lower().endswith(".xls"):
            messagebox.showwarning("提示", "请选择 .xls 脚本文件", parent=self.dlg)
            return
        self.script_path = fp
        self.pkg_path = ""
        self.sha256 = ""
        self.manifest = {}
        self.pr_url = ""
        self.s1_path_lbl.configure(text=fp)
        try:
            self.images = script_package.find_local_images(fp)
        except Exception:
            self.images = []
        self.s1_img_lbl.configure(text="同目录图片: {} 个".format(len(self.images)))
        self._update_footer()

    # ── step2 校验 ──
    def _meta_dict(self):
        return {
            "id": self.id_var.get().strip(),
            "name": self.name_var.get().strip(),
            "description": self.desc_var.get().strip(),
            "category": self.cat_var.get().strip(),
            "author": self.author_var.get().strip(),
            "version": self.ver_var.get().strip(),
            "tags": [t.strip() for t in self.tags_var.get().split(",") if t.strip()],
            "icon": self.icon_var.get().strip(),
            "requires": [k for k, v in self.req_vars.items() if v.get()],
            "min_app_version": self.minver_var.get().strip(),
        }

    def _validate_step2(self):
        meta = self._meta_dict()
        errs, warns = mup.validate_meta(meta)
        unknown = []
        if self.script_path:
            try:
                unknown = mup.check_script_commands(self.script_path)
            except Exception:
                unknown = []
        msgs = list(errs)
        if unknown:
            msgs.append("脚本含未注册命令: {}".format(", ".join(unknown)))
        self._last_valid = (len(errs) == 0 and not unknown)
        try:
            if msgs:
                self.s2_err.configure(
                    text="\n".join(msgs),
                    fg=(themed("err") if not self._last_valid else themed("wn")))
            elif warns:
                self.s2_err.configure(text="提示: " + "; ".join(warns),
                                      fg=themed("wn"))
            else:
                self.s2_err.configure(text="")
        except Exception:
            pass
        self._update_footer()
        return self._last_valid

    # ── step3 打包 ──
    def _do_pack(self):
        if self._busy:
            return
        if not self.script_path:
            messagebox.showwarning("提示", "请先选择脚本", parent=self.dlg)
            return
        if not self._validate_step2():
            messagebox.showwarning("提示", "元数据校验未通过，请修正后再打包",
                                   parent=self.dlg)
            return
        meta = self._meta_dict()
        self._set_busy(True)

        def work():
            try:
                out = os.path.join(
                    os.path.dirname(self.script_path),
                    "{}-{}.acrpapkg".format(meta["id"], meta["version"]))
                pkg, sha, man = mup.prepare_package(
                    self.script_path, meta, images=self.images, out_path=out)
            except Exception as e:
                self.dlg.after(0, lambda: self._on_pack_error(str(e)))
                return
            self.dlg.after(0, lambda: self._on_pack_done(pkg, sha, man))

        threading.Thread(target=work, daemon=True).start()

    def _on_pack_done(self, pkg, sha, man):
        self._set_busy(False)
        self.pkg_path, self.sha256, self.manifest = pkg, sha, man
        size = os.path.getsize(pkg) if os.path.exists(pkg) else 0
        self.s3_info.configure(text="包: {}  ({:.1f} KB)\nsha256: {}\n图片: {} 个".format(
            os.path.basename(pkg), size / 1024.0, sha,
            len(man.get("images", []) or [])))
        try:
            self.s3_text.configure(state="normal")
            self.s3_text.delete("1.0", "end")
            self.s3_text.insert("1.0", json.dumps(man, ensure_ascii=False, indent=2))
            self.s3_text.configure(state="disabled")
        except Exception:
            pass
        self._update_footer()

    def _on_pack_error(self, err):
        self._set_busy(False)
        _toast(self.mw.parent, "打包失败: {}".format(err), "error")
        try:
            messagebox.showerror("打包失败", err, parent=self.dlg)
        except Exception:
            pass

    # ── step4 账号 ──
    def _refresh_account(self):
        logged = self.mw._logged_providers()
        try:
            if logged:
                prov = self.mw._active_provider(logged)
                name = getattr(state, "MARKET_USERNAME", "") or prov
                self.s4_status.configure(text="已登录: {} （{}）".format(prov, name),
                                         fg=themed("sc"))
            else:
                self.s4_status.configure(
                    text="未登录。上传需先在 Gitee 或 GitHub 登录。",
                    fg=themed("err"))
        except Exception:
            pass
        self._update_footer()

    # ── step5 上传 ──
    def _do_upload(self):
        if self._busy:
            return
        if not self.pkg_path:
            messagebox.showwarning("提示", "请先在步骤 3 打包", parent=self.dlg)
            self._goto(3)
            return
        logged = self.mw._logged_providers()
        if not logged:
            messagebox.showwarning("提示", "请先在步骤 4 登录", parent=self.dlg)
            self._goto(4)
            return
        provider = self.mw._active_provider(logged)
        meta = self._meta_dict()
        self._cancel_flag.clear()
        self._busy = True
        try:
            self.s5_bar.configure(value=0, maximum=len(mup._STAGES))
        except Exception:
            pass
        try:
            self.s5_link.configure(text="")
        except Exception:
            pass
        self.btn_next.configure(state="disabled")
        self.btn_prev.configure(state="disabled")

        def prog(stage, done, total):
            try:
                self.dlg.after(0, lambda: self._on_progress(stage, done, total))
            except Exception:
                pass

        def work():
            try:
                res = mup.upload(provider, self.pkg_path, meta, progress=prog,
                                 cancel=self._cancel_flag.is_set)
            except Exception as e:
                err = accounts._redact(e)
                self.dlg.after(0, lambda: self._on_upload_error(err))
                return
            self.dlg.after(0, lambda: self._on_upload_done(res))

        threading.Thread(target=work, daemon=True).start()

    def _on_progress(self, stage, done, total):
        try:
            self.s5_stage.configure(text="上传中… {}".format(
                _UPLOAD_STAGE_TEXT.get(stage, stage)))
            self.s5_bar.configure(value=done)
        except Exception:
            pass

    def _on_upload_done(self, res):
        self._busy = False
        self.pr_url = (res or {}).get("pr_url", "")
        try:
            self.s5_bar.configure(value=len(mup._STAGES))
        except Exception:
            pass
        try:
            self.s5_stage.configure(text="上传成功！PR 已创建。")
            if self.pr_url:
                self.s5_link.configure(text="打开 PR: {}".format(self.pr_url))
        except Exception:
            pass
        try:
            self.btn_prev.configure(state="disabled")
            self.btn_next.configure(text="完成", state="normal")
        except Exception:
            pass

    def _on_upload_error(self, err):
        self._busy = False
        try:
            self.s5_stage.configure(text="上传失败")
            self.btn_prev.configure(state="normal")
        except Exception:
            pass
        self._update_footer()
        try:
            messagebox.showerror("上传失败", err, parent=self.dlg)
        except Exception:
            pass

    def _open_pr(self):
        if self.pr_url:
            try:
                webbrowser.open(self.pr_url)
            except Exception as e:
                log1("打开 PR 链接失败: {}".format(e), "warning")

    def _set_busy(self, busy):
        self._busy = busy
        try:
            self.dlg.configure(cursor=("watch" if busy else ""))
        except Exception:
            pass
        self._update_footer()

    def _close(self):
        try:
            self._cancel_flag.set()
        except Exception:
            pass
        try:
            self.dlg.destroy()
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════════════
# 对外接口
# ══════════════════════════════════════════════════════════════════════

def open_market_window(parent, on_install=None, host=None):
    """打开市场窗口 (单例)。已打开则置顶复用。

    Args:
        parent     : Tk 主窗口
        on_install : callable(fp) -> None  安装成功后回调 (把脚本载入编辑器)
        host       : 可选适配器 (预留 toast/after 钩子)，当前仅记录。
    """
    global _WINDOW
    if _WINDOW is not None:
        try:
            _WINDOW.dlg.deiconify()
            _WINDOW.dlg.lift()
            _WINDOW.dlg.focus_force()
            if on_install is not None:
                _WINDOW.on_install = on_install
            return _WINDOW
        except Exception:
            _WINDOW = None
    _WINDOW = MarketWindow(parent, on_install=on_install, host=host)
    return _WINDOW


def open_marketplace(root, on_install=None, *, host=None):
    """设计 §3.1 命名 (open_marketplace)；语义与 open_market_window 一致。"""
    return open_market_window(root, on_install=on_install, host=host)


def close_marketplace():
    """若市场窗口已打开则关闭 (单例)。"""
    global _WINDOW
    if _WINDOW is not None:
        try:
            _WINDOW.close()
        except Exception:
            _WINDOW = None


def retheme_marketplace():
    """主题切换后重刷市场窗口颜色；未打开则为安全 no-op。"""
    if _WINDOW is not None:
        try:
            _WINDOW.retheme()
        except Exception as e:
            log1("市场窗口重绘失败: {}".format(e), "warning")


def get_window():
    """返回当前市场窗口实例 (供自测)，未开则 None。"""
    return _WINDOW
