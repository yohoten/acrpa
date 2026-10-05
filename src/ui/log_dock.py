# -*- coding: utf-8 -*-
"""ui.log_dock — 底部常驻日志面板 (构建 / 工具条行为 / 换肤)。

路线图 §3.1「阶段二 · 第 3 项」第 1 步: 把 ACRPA.py 中的「日志面板」整体抽出。

  · 构建段 (原 ACRPA.py:5543-5635): card_log(换父) / 头部 lh / 工具条 lt /
    rz(Text) / scroll / tag 配置 / ThreadSafeLog 装配 + 启动路径日志;
  · 行为函数 (原 ACRPA.py:3213-3445): 收起展开 / 拖高记忆 / 自动滚动 /
    §4.6 左缘色条 / 级别过滤 / 关键字搜索 / 清空视图 / 导出视图;
  · 换肤回调: 原 ACRPA._refresh_theme 内「Update log area」段的定点回填
    (rz / scroll 重着色 + 日志 tag 随主题重配, D3), 现由本模块
    `ui.theme.subscribe(refresh_theme)` 自注册 —— _refresh_theme 不再出现
    这两处具名回填。

设计约束
--------
· **零 GUI 副作用**: `import ui.log_dock` 不建窗、不建 Tk 对象 (仅 import tkinter);
· **不 import ACRPA**: 全部依赖经 `build(..., deps=...)` 显式注入 —— 避免成环
  (src/ 内既有约定: 模块间一律依赖注入, 无反向 import 宿主);
· **字体角色不复制定义**: `FONT_*` 由 build() 从注入值绑定, 本模块不自建
  `_FONT_SPECS` (唯一真源仍在 utils);
· **set_tlog 契约**: build() 内仍调用一次注入的 `utils.set_tlog(_tlog)`, 使
  `utils.log1` 全库可用; 宿主可经 get_tlog() 取回实例。

deps 键 (build 的形参)
----------------------
    ui_theme         ui.theme 模块 (subscribe / roled)
    create_card      utils.create_card
    thread_safe_log  utils.ThreadSafeLog 类
    set_tlog         utils.set_tlog 函数 (log1 全库可用的关键契约)
    log1             utils.log1 函数
    show_toast       utils.show_toast 函数
    main_paned       承载日志 pane 的 PanedWindow (add/forget/bind)
    root             主窗口 (show_toast 父窗口 / _tlog.flush 参数)
    app_root         APP_ROOT (导出对话框 initialdir)
    screenshot_dir   SCREENSHOT_DIR (启动日志文案)
    state            state 模块 (预留: 面板状态与宿主一致)
    toggle_button    状态栏「▾ 日志」标签 (可 None, 之后 set_toggle_button 补)

对外接口
--------
    build(parent, *, log_dir, colors, fonts, deps) -> handle
    init_ctx(**kw)                      # 分步/兼容式注入 (不构建)
    refresh_theme(dark, colors, prev)   # ThemeBus 订阅回调
    flush_and_apply() -> bool           # 供宿主 _periodic 调用
    toggle_dock / on_sash_release / autoscroll_changed / apply_cbar /
    apply_filter / search_next / clear_view / export_view
    set_toggle_button(btn)
    get_rz / get_tlog / get_scroll / get_card / get_header / get_toolbar /
    get_log_frame / get_autoscroll_var / get_level_var / get_search_var /
    get_level_combo / get_search_entry / get_collapse_btn / get_cbar_colors /
    get_log_save_path / get_state
"""
import datetime
import os
import tkinter
from tkinter import filedialog
from tkinter import ttk

__all__ = [
    "build", "init_ctx", "refresh_theme", "flush_and_apply",
    "toggle_dock", "on_sash_release", "autoscroll_changed",
    "apply_cbar", "apply_filter", "search_next", "clear_view", "export_view",
    "set_toggle_button",
    "get_rz", "get_tlog", "get_scroll", "get_card", "get_header", "get_toolbar",
    "get_log_frame", "get_autoscroll_var", "get_level_var", "get_search_var",
    "get_level_combo", "get_search_entry", "get_collapse_btn",
    "get_cbar_colors", "get_log_save_path", "get_state",
]

# ── 字体角色: 由 build() 从注入值绑定 (本模块不复制 utils._FONT_SPECS) ──
FONT_TITLE = None
FONT_BODY = None
FONT_SMALL = None
FONT_BUTTON = None
FONT_LOG = None

# 卡片内边距 (与宿主 build_app 的 PI 同规格: {"padx":6,"pady":2})
_PI = {"padx": 6, "pady": 2}

# ── 模块级控件引用 (build() 后可用; 宿主经 get_* 取回, 亦可直读) ──
_rz = None                  # 日志 Text (宿主 _periodic / tools 直读)
_scroll = None              # 日志 Scrollbar
_tlog = None                # utils.ThreadSafeLog 实例 (set_tlog 契约)
_card_log = None            # 卡片容器
_lh = None                  # 头部 (标题 / 自动滚动 / 收起)
_lt = None                  # 工具条 (级别 / 搜索 / 清空 / 导出)
_log_frame = None           # Text + Scrollbar 所在 Frame
_log_auto_scroll = None     # BooleanVar 自动滚动
_log_level_var = None       # StringVar 级别过滤
_log_search_var = None      # StringVar 搜索关键字
_log_level_combo = None     # 级别下拉
_log_search_entry = None    # 搜索输入框
_log_collapse_btn = None    # 头部「▾ 收起」按钮
_log_dock_toggle_btn = None # 状态栏「▾ 日志」展开入口
_parent_dock = None         # main_paned 内的 pane 容器 (原 bottom_dock)
_main_paned = None          # 承载 pane 的 PanedWindow
_log_save_path = None       # 当日日志文件路径 (启动日志用)
_CBAR_LEVEL_COLOR = {}      # cbar_<tag> → 颜色 (宿主兼容别名)

# ── 注入上下文 (build / init_ctx 填充) ──
_CTX = {
    "ui_theme": None, "create_card": None, "thread_safe_log": None,
    "set_tlog": None, "log1": None, "show_toast": None,
    "main_paned": None, "root": None, "app_root": "", "screenshot_dir": "",
    "state": None, "colors": None, "log_dir": "",
}

# ── 面板状态 / 级别 tag 表 / 过滤与色条游标 (原 ACRPA 模块级常量, 原位迁入) ──
_log_dock_state = {"visible": True, "h": 160}
_LOG_LEVEL_TAGS = ("info", "success", "warning", "error")
_logfilter = {"applied": 0}   # 已应用过滤到的行号 (增量)
# §4.6 左缘 2px 色条: 级别 tag ↔ cbar_<tag> ↔ 行首字形 ▌
_CBAR_LEVEL_OF = {"info": "cbar_info", "success": "cbar_success",
                  "warning": "cbar_warning", "error": "cbar_error"}
_cbar_state = {"done": 0}


# ══════════════════════════════════════════════════════════════════════
# 依赖注入
# ══════════════════════════════════════════════════════════════════════

def init_ctx(**kw):
    """分步注入依赖 (与 build 的 deps 等价); 只合并已知键, 未知键忽略。"""
    for k in kw:
        if k in _CTX:
            _CTX[k] = kw[k]
    return _CTX


def _bind_fonts(fonts):
    """把注入的字体角色绑定到模块级 FONT_* (唯一真源仍是 utils)。"""
    global FONT_TITLE, FONT_BODY, FONT_SMALL, FONT_BUTTON, FONT_LOG
    fonts = fonts or {}
    FONT_TITLE = fonts.get("title")
    FONT_BODY = fonts.get("body")
    FONT_SMALL = fonts.get("small")
    FONT_BUTTON = fonts.get("button")
    FONT_LOG = fonts.get("log")


def _toast(msg, kind="info", ms=2000):
    """经注入的 utils.show_toast 弹提示 (未注入则静默)。"""
    fn = _CTX.get("show_toast")
    if fn is None:
        return
    try:
        fn(_CTX.get("root"), msg, kind, ms)
    except Exception:
        pass


def _log1(msg, tag=None):
    """经注入的 utils.log1 记日志 (未注入则静默)。"""
    fn = _CTX.get("log1")
    if fn is None:
        return
    try:
        fn(msg, tag)
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════
# 构建 (原 ACRPA.build_app 内日志面板段: :5543-5635)
# ══════════════════════════════════════════════════════════════════════

def build(parent, *, log_dir, colors, fonts, deps=None):
    """构建底部常驻日志面板 (两 Tab 共用), 返回控件句柄 dict (= get_state())。

    parent : main_paned 内的 pane 容器 (原 bottom_dock)。
    log_dir: 日志落盘目录 (utils.ThreadSafeLog 第 2 参数)。
    colors : 当前主题色板 (utils.C / ui.theme.colors())。
    fonts  : {"title","body","small","button","log"} → 绑定模块级 FONT_*。
    deps   : 依赖注入字典 (键见模块 docstring)。
    """
    global _rz, _scroll, _tlog, _card_log, _lh, _lt, _log_frame, \
        _log_auto_scroll, _log_level_var, _log_search_var, _log_level_combo, \
        _log_search_entry, _log_collapse_btn, _parent_dock, _main_paned, \
        _log_save_path, _CBAR_LEVEL_COLOR

    _CTX.update(deps or {})
    _CTX["log_dir"] = log_dir
    _CTX["colors"] = colors
    _bind_fonts(fonts)

    ui_theme = _CTX.get("ui_theme")
    create_card = _CTX.get("create_card")
    C = colors
    _parent_dock = parent
    _main_paned = _CTX.get("main_paned")

    # ⑥ 底部常驻日志面板 (两 Tab 共用) —— 原「执行控制」Tab 的日志卡, 换父到 bottom_dock
    card_log = create_card(parent)
    card_log.pack(fill="both", expand=True, padx=6, pady=(0, 4))
    card_log.columnconfigure(0, weight=1); card_log.rowconfigure(2, weight=1)
    _card_log = card_log

    lh = tkinter.Frame(card_log, bg=C["bgc"]); lh.grid(row=0, column=0, sticky="ew", **_PI)
    lh.columnconfigure(0, weight=1)
    # 语义角色显式登记: 旧实现按文案含「运行」嗅探 → fg=sc; 迁移为登记 "sc" (行为等价)
    ui_theme.roled(tkinter.Label(lh, text="运行日志", font=FONT_BODY, fg=C["fgm"], bg=C["bgc"]), "sc").grid(
        row=0, column=0, sticky="w")
    log_auto_scroll = tkinter.BooleanVar(value=True)
    tkinter.Checkbutton(lh, text="自动滚动", variable=log_auto_scroll, font=FONT_SMALL,
        fg=C["fgm"], bg=C["bgc"], activebackground=C["bgc"], activeforeground=C["fgb"],
        selectcolor=C["ebg"], bd=0, highlightthickness=0,
        command=lambda: autoscroll_changed()).grid(row=0, column=1, sticky="e")
    if _main_paned is not None:
        _main_paned.bind("<ButtonRelease-1>", on_sash_release)
    _lh = lh

    _log_collapse_btn = tkinter.Button(lh, text="▾ 收起", font=FONT_SMALL, bg=C["bgc"], fg=C["fgb"],
        activebackground=C["acl"], relief="flat", bd=1, cursor="hand2", padx=6, pady=1,
        command=toggle_dock)
    _log_collapse_btn.grid(row=0, column=2, sticky="e", padx=(4, 0))

    # 将日志 Dock 作为 pane#1 加入 (默认高 160, 最小 60)
    if _main_paned is not None:
        _main_paned.add(parent, minsize=60, height=160, stretch="never")

    # ③ 日志工具条: 级别过滤 / 关键字搜索 / 清空 / 导出
    lt = tkinter.Frame(card_log, bg=C["bgc"]); lt.grid(row=1, column=0, sticky="ew", padx=6, pady=(0, 2))
    tkinter.Label(lt, text="级别:", font=FONT_SMALL, fg=C["fgm"], bg=C["bgc"]).pack(side="left")
    log_level_var = tkinter.StringVar(value="全部")
    _log_level_combo = ttk.Combobox(lt, textvariable=log_level_var, state="readonly", width=8,
        values=("全部", "INFO", "SUCCESS", "WARNING", "ERROR"))
    _log_level_combo.pack(side="left", padx=(2, 8))
    _log_level_combo.bind("<<ComboboxSelected>>", lambda e: apply_filter(full=True))
    tkinter.Label(lt, text="搜索:", font=FONT_SMALL, fg=C["fgm"], bg=C["bgc"]).pack(side="left")
    log_search_var = tkinter.StringVar(value="")
    _log_search_entry = tkinter.Entry(lt, textvariable=log_search_var, font=FONT_SMALL,
        width=16, relief="solid", bd=1, bg=C["ebg"], fg=C["fgb"], insertbackground=C["fgt"])
    _log_search_entry.pack(side="left", padx=(2, 2))
    _log_search_entry.bind("<Return>", lambda e: search_next(reset=True))
    tkinter.Button(lt, text="搜索", font=FONT_SMALL, bg=C["bgc"], fg=C["fgb"],
        activebackground=C["acl"], relief="flat", bd=1, cursor="hand2", padx=6, pady=1,
        command=lambda: search_next(reset=True)).pack(side="left", padx=2)
    tkinter.Button(lt, text="下一个", font=FONT_SMALL, bg=C["bgc"], fg=C["fgb"],
        activebackground=C["acl"], relief="flat", bd=1, cursor="hand2", padx=6, pady=1,
        command=lambda: search_next(reset=False)).pack(side="left", padx=2)
    ui_theme.roled(tkinter.Button(lt, text="导出", font=FONT_SMALL, bg=C["ac"], fg="white",
        activebackground=C["ach"], relief="flat", bd=1, cursor="hand2", padx=6, pady=1,
        command=lambda: export_view()), "ac").pack(side="right", padx=2)
    tkinter.Button(lt, text="清空", font=FONT_SMALL, bg=C["bgc"], fg=C["fgb"],
        activebackground=C["acl"], relief="flat", bd=1, cursor="hand2", padx=6, pady=1,
        command=lambda: clear_view()).pack(side="right", padx=2)
    _lt = lt

    log_frame = tkinter.Frame(card_log, bg=C["logbg"])
    log_frame.grid(row=2, column=0, sticky="nsew", padx=6, pady=(0, 4))
    log_frame.columnconfigure(0, weight=1); log_frame.rowconfigure(0, weight=1)
    _log_frame = log_frame

    rz = tkinter.Text(log_frame, font=FONT_LOG, fg=C["logfg"], bg=C["logbg"],
        wrap="word", relief="flat", bd=0, padx=6, pady=4, insertbackground=C["fgt"],
        selectbackground=C["acl"], selectforeground=C["fgt"], undo=True, maxundo=50)
    rz.grid(row=0, column=0, sticky="nsew")
    scroll = tkinter.Scrollbar(log_frame, width=6, relief="flat", elementborderwidth=0,
        bg=C["bd"], activebackground=C["fgm"], troughcolor=C["logbg"])
    scroll.grid(row=0, column=1, sticky="ns")

    scroll.config(command=rz.yview); rz.config(yscrollcommand=scroll.set)
    rz.tag_configure("info", foreground=C["log_info"])
    rz.tag_configure("success", foreground=C["sc"])
    rz.tag_configure("warning", foreground=C["wn"])
    rz.tag_configure("error", foreground=C["dg"])
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
    rz.tag_configure("search_hit", background=C["search_hit_bg"], foreground=C["fgt"])
    try:
        rz.tag_configure("lf_hide", elide=True)
    except Exception:
        pass
    _rz = rz
    _scroll = scroll
    _log_auto_scroll = log_auto_scroll
    _log_level_var = log_level_var
    _log_search_var = log_search_var

    # set_tlog 契约: 让 utils.log1 全库可用 (必须被调用一次)
    _tlog = _CTX["thread_safe_log"](rz, log_dir)
    _CTX["set_tlog"](_tlog)

    # ── 启动时输出日志保存路径，方便用户定位 ──
    _log_save_path = os.path.join(log_dir, "acrpa_{}.log".format(
        datetime.datetime.now().strftime("%Y%m%d")))
    _log1("日志保存路径: {}".format(_log_save_path))
    _log1("截图保存路径: {}".format(_CTX.get("screenshot_dir", "")))

    # 换肤: 订阅 ThemeBus (幂等, 重复构建只登记一次) —— 取代原 _refresh_theme 的定点回填
    if ui_theme is not None:
        ui_theme.subscribe(refresh_theme)

    return get_state()


# ══════════════════════════════════════════════════════════════════════
# 换肤回调 (原 ACRPA._refresh_theme 内「Update log area」段: :1183-1201)
# ══════════════════════════════════════════════════════════════════════

def refresh_theme(dark=None, colors=None, prev=None):
    """ThemeBus 订阅回调 (签名与 publish 派发契约一致): 日志区 + 日志 tag 随主题重配。

    D3: 日志区 tag 前景随主题刷新 (旧实现仅建窗时设一次, 切换后 tag 文字仍是旧色)。
    """
    C = colors if isinstance(colors, dict) else _CTX.get("colors")
    if not isinstance(C, dict) or _rz is None:
        return
    _CTX["colors"] = C
    try:
        _rz.configure(bg=C["logbg"], fg=C["logfg"],
            selectbackground=C["acl"], selectforeground=C["fgt"])
        if _scroll is not None:
            _scroll.configure(bg=C["bd"], activebackground=C["fgm"], troughcolor=C["logbg"])
    except Exception:
        pass
    try:
        _rz.tag_configure("info", foreground=C["log_info"])
        _rz.tag_configure("success", foreground=C["sc"])
        _rz.tag_configure("warning", foreground=C["wn"])
        _rz.tag_configure("error", foreground=C["dg"])
        # §4.6 左缘色条 / 行 zebra / 搜索命中 随主题刷新
        _rz.tag_configure("cbar_info", foreground=C["log_info"])
        _rz.tag_configure("cbar_success", foreground=C["log_success"])
        _rz.tag_configure("cbar_warning", foreground=C["log_warn"])
        _rz.tag_configure("cbar_error", foreground=C["log_error"])
        _rz.tag_configure("lzebra", background=C["zebra"])
        _rz.tag_configure("search_hit", background=C["search_hit_bg"])
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════
# 周期刷新 (原 ACRPA._periodic 内联段, 现收敛为一个调用)
# ══════════════════════════════════════════════════════════════════════

def flush_and_apply():
    """等价原 _periodic 的日志段: flush + 左缘色条 + 增量级别过滤。

    ③ 自动滚动: 关闭时保留当前视口, 避免新日志把视图强行拉到底部。
    返回 False 表示 flush 失败 (原实现此处 `return` 提前结束 _periodic, 宿主须沿用)。
    """
    if _tlog is None or _rz is None:
        return True
    if _log_auto_scroll is not None and _log_auto_scroll.get():
        try:
            _tlog.flush(_CTX.get("root"))
        except Exception:
            return False
        _log_apply_cbar()
    else:
        _prev_y = _rz.yview()
        try:
            _tlog.flush(_CTX.get("root"))
        except Exception:
            return False
        try:
            _log_apply_cbar()
        except Exception:
            pass
        try:
            _rz.yview_moveto(_prev_y[0])
        except Exception:
            pass
    # ③ 级别过滤生效时, 增量隐藏新增的不匹配行
    if _log_level_var is not None and _log_level_var.get() != "全部":
        _log_apply_filter(full=False)
    return True


# ══════════════════════════════════════════════════════════════════════
# 收起/展开 + 拖高 (原 ACRPA:3213-3241)
# ══════════════════════════════════════════════════════════════════════

def _toggle_log_dock(event=None):
    """收起/展开底部常驻日志面板 (记忆收起前高度)。"""
    try:
        if _log_dock_state["visible"]:
            try: _log_dock_state["h"] = _parent_dock.winfo_height() or 160
            except Exception: pass
            _main_paned.forget(_parent_dock)
            _log_dock_state["visible"] = False
        else:
            _main_paned.add(_parent_dock, minsize=60,
                            height=_log_dock_state["h"], stretch="never")
            _log_dock_state["visible"] = True
        try:
            _log_dock_toggle_btn.config(
                text=("▾ 日志" if _log_dock_state["visible"] else "▸ 日志"))
        except Exception:
            pass
    except Exception:
        pass


def _on_log_sash_release(event=None):
    """拖动 sash 后记忆日志面板高度。"""
    try:
        if _log_dock_state["visible"]:
            _log_dock_state["h"] = max(60, _parent_dock.winfo_height())
    except Exception:
        pass


def set_toggle_button(btn):
    """注入状态栏「▾ 日志」展开入口 (构建顺序: 状态栏晚于日志面板)。"""
    global _log_dock_toggle_btn
    _log_dock_toggle_btn = btn
    return btn


# ══════════════════════════════════════════════════════════════════════
# 级别 tag / 自动滚动 / 搜索 / 清空 / 导出 (原 ACRPA:3287-3444)
# ══════════════════════════════════════════════════════════════════════

def _log_autoscroll_changed():
    """切换自动滚动: 重新开启时立即滚到底部"""
    try:
        if _log_auto_scroll.get():
            _rz.see("end")
    except Exception:
        pass


def _log_line_tag(line_no):
    """返回某行(1-based)命中的级别 tag, 无则返回空串"""
    try:
        names = _rz.tag_names("{}.0".format(line_no))
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


def _log_apply_cbar():
    """为新增日志行: 行首插入 ▌ 并上 cbar_<tag> 色, 偶行加极淡 zebra (§4.6)。

    机制: tkinter Text 无 per-row 边框, 故按文档 §4.6 以「行首窄形字符 ▌(U+258C)
    + cbar_<tag> 颜色 tag」承载左缘 2px 色条; 级别由 tag 判定 (§2.5)。
    D1 幂等: **逐行处理成功后立即推进** _cbar_state["done"], 并以「行首已含 ▌」二次
    防重 —— 旧实现仅在整循环结束后赋值, 一旦中途抛错 (异常被外层吞掉) 会重入并对
    已插过 ▌ 的行再次插入, 造成色条重复。
    """
    try:
        total = int(float(_rz.index("end-1c")))
    except Exception:
        return
    for i in range(_cbar_state["done"] + 1, total + 1):
        try:
            line = _rz.get("{}.0".format(i), "{}.end".format(i))
            # 幂等: 行首已含 ▌ → 本行已处理过, 直接推进而不重复插入
            if line.startswith("▌"):
                _cbar_state["done"] = i
                continue
            # Text 末尾隐含空行: 跳过且不推进, 留待有内容后再处理
            if line == "":
                continue
            lv = _log_line_tag(i)
            ctag = _CBAR_LEVEL_OF.get(lv, "cbar_info")
            _rz.insert("{}.0".format(i), "▌")
            _rz.tag_add(ctag, "{}.0".format(i), "{}.1".format(i))
            if i % 2 == 0:
                _rz.tag_add("lzebra", "{}.1".format(i), "{}.end".format(i))
            _cbar_state["done"] = i
        except Exception:
            # 单行失败: 保留 done 于上一成功行; 下次以「行首▌」判定避免重复
            continue


def _log_apply_filter(full=False):
    """按级别过滤日志行 (用 elide 隐藏不匹配行). 全部=显示所有"""
    try:
        sel = _log_level_var.get()
        total = int(float(_rz.index("end-1c")))
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
                _rz.tag_remove("lf_hide", a, b)
            else:
                _rz.tag_add("lf_hide", a, b)
        _logfilter["applied"] = total
        # 过滤生效时, 自动滚动到最后一个可见行
        if _log_auto_scroll.get() and sel != "全部":
            for i in range(total, 0, -1):
                if "lf_hide" not in _rz.tag_names("{}.0".format(i)):
                    _rz.see("{}.0".format(i)); break
    except Exception:
        pass


def _log_search_next(reset=False):
    """关键字搜索: reset=True 从头开始, 否则跳到下一个匹配"""
    try:
        kw = _log_search_var.get()
        _rz.tag_remove("search_hit", "1.0", "end")
        if not kw:
            return
        pos = "1.0" if reset else "{}+1c".format(_rz.index("insert"))
        idx = _rz.search(kw, pos, stopindex="end", nocase=True)
        if not idx:
            idx = _rz.search(kw, "1.0", stopindex="end", nocase=True)
        if idx:
            end = "{}+{}c".format(idx, len(kw))
            _rz.tag_add("search_hit", idx, end)
            _rz.mark_set("insert", end)
            _rz.see(idx)
        else:
            _toast("未找到: {}".format(kw), "warning", 1500)
    except Exception:
        pass


def _log_clear_view():
    """清空日志视图 (不删除磁盘日志与内存缓冲)"""
    try:
        _rz.delete("1.0", "end")
        _logfilter["applied"] = 0
        _cbar_state["done"] = 0
        _rz.tag_remove("search_hit", "1.0", "end")
    except Exception:
        pass


def _log_export_view():
    """导出当前(可见)日志视图到文件"""
    try:
        default_name = "acrpa_log_{}.txt".format(
            datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
        fp = filedialog.asksaveasfilename(
            title="导出运行日志", initialdir=_CTX.get("app_root", ""), initialfile=default_name,
            defaultextension=".txt", filetypes=[("文本文件", "*.txt"), ("日志", "*.log")])
        if not fp:
            return
        total = int(float(_rz.index("end-1c")))
        lines = []
        for i in range(1, total + 1):
            if "lf_hide" in _rz.tag_names("{}.0".format(i)):
                continue
            lines.append(_rz.get("{}.0".format(i), "{}.end".format(i)))
        with open(fp, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + ("\n" if lines else ""))
        _toast("日志已导出: {}".format(os.path.basename(fp)), "success")
    except Exception as e:
        _log1("日志导出失败: {}".format(e), "error")


# ── 公开接口别名 (宿主/自测按语义名引用; 原名保留以便逐行比对迁移) ──
toggle_dock = _toggle_log_dock
on_sash_release = _on_log_sash_release
autoscroll_changed = _log_autoscroll_changed
apply_cbar = _log_apply_cbar
apply_filter = _log_apply_filter
search_next = _log_search_next
clear_view = _log_clear_view
export_view = _log_export_view


# ══════════════════════════════════════════════════════════════════════
# 控件取回 (宿主转发别名 / tools 直读)
# ══════════════════════════════════════════════════════════════════════

def get_rz():
    """日志 Text 控件 (宿主 _periodic / tools 直读契约)。"""
    return _rz


def get_tlog():
    """utils.ThreadSafeLog 实例 (已 set_tlog, 全库 log1 可用)。"""
    return _tlog


def get_scroll():
    """日志 Scrollbar。"""
    return _scroll


def get_card():
    """日志卡片容器。"""
    return _card_log


def get_header():
    """头部 (标题 / 自动滚动 / 收起)。"""
    return _lh


def get_toolbar():
    """工具条 (级别 / 搜索 / 清空 / 导出)。"""
    return _lt


def get_log_frame():
    """Text + Scrollbar 所在 Frame。"""
    return _log_frame


def get_autoscroll_var():
    """自动滚动 BooleanVar。"""
    return _log_auto_scroll


def get_level_var():
    """级别过滤 StringVar。"""
    return _log_level_var


def get_search_var():
    """搜索关键字 StringVar。"""
    return _log_search_var


def get_level_combo():
    """级别下拉 Combobox。"""
    return _log_level_combo


def get_search_entry():
    """搜索输入框。"""
    return _log_search_entry


def get_collapse_btn():
    """头部「▾ 收起」按钮。"""
    return _log_collapse_btn


def get_cbar_colors():
    """cbar_<tag> → 颜色映射 (宿主兼容别名 _CBAR_LEVEL_COLOR)。"""
    return dict(_CBAR_LEVEL_COLOR)


def get_log_save_path():
    """当日日志文件路径。"""
    return _log_save_path


def get_state():
    """取回面板状态 + 关键控件句柄 (与构建返回值一致)。"""
    return {
        "rz": _rz, "scroll": _scroll, "tlog": _tlog,
        "card": _card_log, "header": _lh, "toolbar": _lt, "log_frame": _log_frame,
        "autoscroll_var": _log_auto_scroll, "level_var": _log_level_var,
        "search_var": _log_search_var, "level_combo": _log_level_combo,
        "search_entry": _log_search_entry, "collapse_btn": _log_collapse_btn,
        "toggle_btn": _log_dock_toggle_btn, "parent_dock": _parent_dock,
        "visible": _log_dock_state["visible"], "h": _log_dock_state["h"],
        "log_save_path": _log_save_path,
    }
