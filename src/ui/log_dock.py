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

v6 视觉改造 (docs/design/2026-11-07-ACRPA-UI视觉优化方案-v5.md · A5/A6)
---------------------------------------------------------------------
原「标题行 + 工具条行」两行合并为**单行头** (标题 / 计数 / 级别视图 / 搜索框 /
搜索·下一个 / 自动滚动 / 清空·导出·收起), 释放约 30px 纵向空间给日志体;
日志行改为「时间戳降权 + 定宽徽标色块 + 中性正文」(版式常量唯一真源在
core.log:LOG_TS_W / LOG_BADGE_W / LOG_COL_GAP), 灰度与语义色只保留在
「左缘色条 + 徽标块」两处, 正文回归可读中性色; 另加行悬停提示。

设计约束
--------
· **零 GUI 副作用**: `import ui.log_dock` 不建窗、不建 Tk 对象 (仅 import tkinter);
· **不 import ACRPA**: 全部依赖经 `build(..., deps=...)` 显式注入 —— 避免成环
  (src/ 内既有约定: 模块间一律依赖注入, 无反向 import 宿主);
· **字体角色不复制定义**: `FONT_*` 由 build() 从注入值绑定, 本模块不自建
  `_FONT_SPECS` (唯一真源仍在 utils);
· **配色不自建**: 徽标色块/悬停底等新色一律取 colors() 的 `log_*` 键;
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

__all__ = [
    "build", "init_ctx", "refresh_theme", "flush_and_apply",
    "toggle_dock", "toggle_body", "on_sash_release", "autoscroll_changed",
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
FONT_SMALL_BOLD = None      # 头部「运行日志」标题 (语义: 小号加粗)
FONT_LOG_BOLD = None        # 徽标色块 (等宽加粗, 列宽不变)

# 头部留白 (设计 px, 经 sp() 缩放): 左右 10 / 上下 4 —— 单行头 ≈ 32px
_PAD_X = 10
_PAD_Y = 4

# 级别 → (徽标 tag, 徽标字色 token): 徽标块 = 定宽色块 + 级别色字 (§2.5 四级)
_BADGE_SPEC = (
    ("info", "lbadge_info", "log_info"),
    ("success", "lbadge_success", "log_success"),
    ("warning", "lbadge_warning", "log_warn"),
    ("error", "lbadge_error", "log_error"),
)
_BADGE_TAG_OF = dict((lv, tag) for lv, tag, _tok in _BADGE_SPEC)
_BADGE_NAME_OF = dict((lv, name) for lv, name in (
    ("info", "INFO"), ("success", "SUCCESS"),
    ("warning", "WARNING"), ("error", "ERROR")))

# 行版式常量与生产端 (core/log.py) **同源**: 列宽/列距只有一份定义, 本模块只读派生值。
try:
    from core.log import (LOG_TS_W as _CORE_TS_W, LOG_BADGE_W as _CORE_BADGE_W,
                          LOG_COL_GAP as _CORE_COL_GAP, log_row_layout as _core_layout)
except Exception:                       # core.log 不可用 (孤立导入) → 与默认版式一致
    _CORE_TS_W, _CORE_BADGE_W, _CORE_COL_GAP = 8, 8, 2

    def _core_layout(lead=1):
        ts_a = int(lead)
        b_a = ts_a + _CORE_TS_W + _CORE_COL_GAP
        return ts_a, ts_a + _CORE_TS_W, b_a, b_a + _CORE_BADGE_W

LOG_TS_W = _CORE_TS_W
LOG_BADGE_W = _CORE_BADGE_W

# 行悬停 (视觉稿 A5: 光标所在行淡底提示) —— 只记「上一个悬停行」, 换行才动 Tcl
_hover = {"line": 0}

# 级别视图选项 = 徽标去括号 (§2.5 权威: 过滤下拉文案 = 徽标去括号)
_LEVEL_CHOICES = ("全部", "INFO", "SUCCESS", "WARNING", "ERROR")

# 单行头控件登记表 (换肤按此逐项回填; build 填充, refresh_theme 消费)
_UI = {}

# 单行头自适应: 关键字框是唯一的「弹性」控件 (宽度按剩余空间伸缩 12..26 字符),
# 其余控件保持自然宽度 —— 窄窗时不至于把右侧「清空/导出/收起」挤出可视区。
_FIT = {"cols": 0}

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
_log_collapse_btn = None    # 头部「收起 ▲ / 展开 ▼」折叠按钮
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
    "ctx": None,
}

# ── 面板状态 / 级别 tag 表 / 过滤与色条游标 (原 ACRPA 模块级常量, 原位迁入) ──
_log_dock_state = {"visible": True, "h": 160}
_log_body_collapsed = False   # 日志体折叠态 (§3.5: 只隐表体, 保留标题 + 工具条)
_LOG_LEVEL_TAGS = ("info", "success", "warning", "error")
_logfilter = {"applied": 0}   # 已应用过滤到的行号 (增量)
# §4.6 左缘 2px 色条: 级别 tag ↔ cbar_<tag> ↔ 行首字形 ▌
_CBAR_LEVEL_OF = {"info": "cbar_info", "success": "cbar_success",
                  "warning": "cbar_warning", "error": "cbar_error"}
_cbar_state = {"done": 0}


# ── 运行时回退开关 (v5 §3.4 L4 / §7.2; 默认全开, 置 0/false/off 回退) ──
def _env_flag(name, default=True):
    """环境变量开关: 缺失/空 → default; 0/false/no/off → False。"""
    try:
        v = os.environ.get(name)
    except Exception:
        return default
    if v is None or str(v).strip() == "":
        return default
    return str(v).strip().lower() not in ("0", "false", "no", "off")


# LOG_CBAR_INLINE 与生产端 (core/log.py) **同源**: 生产端已把 ▌ 拼进文本,
# 本模块只需批量 tag_add; 置 False 时恢复「逐行 insert("▌") + tag_add」旧路径。
try:
    from core.log import LOG_CBAR_INLINE as _CORE_CBAR_INLINE
    LOG_CBAR_INLINE = bool(_CORE_CBAR_INLINE)
except Exception:                      # core.log 不可用 (孤立导入) → 按环境变量
    LOG_CBAR_INLINE = _env_flag("LOG_CBAR_INLINE", True)

# LOG_EXPORT_ASYNC : 导出「拼装 + 写盘」交给后台池 (False → 主线程逐行拼装, 旧行为)
# LOG_FLUSH_IDLE_SKIP: 日志队列为空时整段跳过 flush_and_apply 的全部 Tcl 调用
LOG_EXPORT_ASYNC = _env_flag("LOG_EXPORT_ASYNC", True)
LOG_FLUSH_IDLE_SKIP = _env_flag("LOG_FLUSH_IDLE_SKIP", True)


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
    global FONT_TITLE, FONT_BODY, FONT_SMALL, FONT_BUTTON, FONT_LOG, \
        FONT_SMALL_BOLD, FONT_LOG_BOLD
    fonts = fonts or {}
    FONT_TITLE = fonts.get("title")
    FONT_BODY = fonts.get("body")
    FONT_SMALL = fonts.get("small")
    FONT_BUTTON = fonts.get("button")
    FONT_LOG = fonts.get("log")
    # 新增角色缺失时按语义回退 (旧注入点只传 5 个键, 不得因此报错)
    FONT_SMALL_BOLD = fonts.get("small_bold") or FONT_SMALL or FONT_BODY
    FONT_LOG_BOLD = fonts.get("log_bold") or FONT_LOG


def _spx(n):
    """设计 px → 实际 px (经 utils.sp 缩放; 未注入 utils 时按原值, 绝不抛错)。"""
    try:
        from utils import sp as _sp
        return _sp(n)
    except Exception:
        return int(n)


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
# 日志 tag 表 (建窗与换肤共用同一份配置, 避免两处漂移)
# ══════════════════════════════════════════════════════════════════════

def _configure_tags(rz, C):
    """配置日志 Text 的全部 tag (唯一实现, 建窗与 refresh_theme 都调它)。

    **tag 优先级由创建顺序决定** (Tk: 后创建者优先), 故顺序不可随意调整:
      级别(整行字色) → cbar_*(行首色条) → lzebra(斑马底) → lhover(悬停底)
      → lts(时间戳字色) → lbadge_*(徽标块字色+底色) → search_hit(搜索命中)
    换肤时只 reconfigure 已存在的 tag, 不新建 → 优先级保持不变。
    """
    get = C.get
    # ① 级别 tag: 整行前景 = 正文色。级别色只由「左缘色条 + 徽标块」承载,
    #    INFO/SUCCESS 正文回归中性可读色 (视觉稿 A5: 长日志纵向扫读不刺眼)。
    rz.tag_configure("info", foreground=get("logfg", C["fgb"]))
    rz.tag_configure("success", foreground=get("logfg", C["fgb"]))
    rz.tag_configure("warning", foreground=C["wn"])
    rz.tag_configure("error", foreground=C["dg"])

    # ② §4.6 级别左缘色条: 行首字形 ▌(U+258C) + cbar_<tag> 颜色 tag
    #    (tkinter Text 无 per-row 边框, 按文档 §4.6 以行首窄形字符承载色条)
    #    _CBAR_LEVEL_COLOR 同时是「级别 ↔ 左缘色」的唯一映射 (宿主经 get_cbar_colors 取回),
    #    四个 tag 逐条显式配置, 便于逐个核对。
    _CBAR_LEVEL_COLOR.update({
        "cbar_info": C["log_info"], "cbar_success": C["log_success"],
        "cbar_warning": C["log_warn"], "cbar_error": C["log_error"],
    })
    rz.tag_configure("cbar_info", foreground=_CBAR_LEVEL_COLOR["cbar_info"])
    rz.tag_configure("cbar_success", foreground=_CBAR_LEVEL_COLOR["cbar_success"])
    rz.tag_configure("cbar_warning", foreground=_CBAR_LEVEL_COLOR["cbar_warning"])
    rz.tag_configure("cbar_error", foreground=_CBAR_LEVEL_COLOR["cbar_error"])

    rz.tag_configure("lzebra", background=C["zebra"])            # §4.6 行 zebra 极淡
    rz.tag_configure("lhover", background=get("log_hover", C["surface_alt"]))

    # ③ 时间戳降权 (视觉稿 A5: 时间戳只作定位锚点, 不抢正文)
    rz.tag_configure("lts", foreground=get("log_ts", C["fgm"]))

    # ④ 徽标块: 定宽色块 + 级别色加粗字 (§2.5 四级; 色值取 log_* 系列 token)
    for _lv, _tag, _token in _BADGE_SPEC:
        rz.tag_configure(_tag,
            foreground=get(_token, C["fgb"]),
            background=get("log_badge_{}_bg".format(_lv), C["surface_alt"]),
            font=FONT_LOG_BOLD or FONT_LOG)

    # ⑤ 搜索命中 / 级别过滤隐藏 (elide 在旧 Tk 上可能不支持, 容错降级)
    rz.tag_configure("search_hit", background=C["search_hit_bg"], foreground=C["fgt"])
    try:
        rz.tag_configure("lf_hide", elide=True)
    except Exception:
        pass


def sync_auto_look():
    """自动滚动开关的外观随状态/主题同步 (● 开 = 语义成功色, ○ 关 = 弱灰)。"""
    dot = _UI.get("auto_dot")
    txt = _UI.get("auto_txt")
    var = _log_auto_scroll
    if dot is None or var is None:
        return
    C = _CTX.get("colors") or {}
    try:
        on = bool(var.get())
    except Exception:
        return
    bg = C.get("bgc", "white")
    try:
        dot.configure(text=("●" if on else "○"),
                      fg=(C.get("sc", "green") if on else C.get("fdg", C.get("fgm", "dimgray"))),
                      bg=bg)
        if txt is not None:
            txt.configure(fg=(C.get("fgb", "black") if on else C.get("fgm", "dimgray")), bg=bg)
    except Exception:
        pass


def _log_hover(event=None):
    """光标所在行加淡底 (lhover) —— 只在「换行」时动 Tcl, 每次 2 次调用。"""
    if _rz is None:
        return
    try:
        line = int(float(str(_rz.index("@%d,%d" % (event.x, event.y)))))
    except Exception:
        return
    old = _hover.get("line", 0)
    if line == old:
        return
    try:
        if old:
            _rz.tag_remove("lhover", "{}.0".format(old), "{}.end+1c".format(old))
        _rz.tag_add("lhover", "{}.0".format(line), "{}.end+1c".format(line))
        _hover["line"] = line
    except Exception:
        pass


def _log_hover_leave(event=None):
    """鼠标离开日志体: 清掉悬停行。"""
    old = _hover.get("line", 0)
    _hover["line"] = 0
    if _rz is None or not old:
        return
    try:
        _rz.tag_remove("lhover", "{}.0".format(old), "{}.end+1c".format(old))
    except Exception:
        pass


def _log_update_count():
    """刷新头部「N 条」计数 (与日志体实际行数一致, 含被过滤隐藏的行)。"""
    lbl = _UI.get("count")
    if lbl is None or _rz is None:
        return
    try:
        total = int(float(_rz.index("end-1c")))
    except Exception:
        return
    text = "{} 条".format(max(0, total - 1))   # 末尾隐含空行不计
    if text == _UI.get("count_text"):
        return
    _UI["count_text"] = text
    try:
        lbl.configure(text=text)
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
    _FIT["cols"] = 0                 # 重新构建 → 自适应状态复位
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

    if _main_paned is not None:
        _main_paned.bind("<ButtonRelease-1>", on_sash_release)

    pad_x = _spx(_PAD_X)
    pad_y = _spx(_PAD_Y)

    # ── ① 单行头 (视觉稿 A5) ─────────────────────────────────────────────
    #    左: 运行日志 · 计数 · 级别视图 · 搜索框 · 搜索/下一个
    #    右: 自动滚动 · 清空 · 导出 · 收起
    #    旧版「标题行 + 工具条行」两行合一 —— 日志体多出约 30px 可视高度。
    lh = tkinter.Frame(card_log, bg=C["bgc"])
    lh.grid(row=0, column=0, sticky="ew", padx=pad_x, pady=(pad_y, 0))
    _lh = lh
    _lt = lh          # 兼容: 旧「工具条」已并入单行头, get_toolbar() 仍指向该行

    # 标题: 只登记「前景 token」(底色随卡片) —— 旧实现 roled(.., "sc") 会让通用
    # walker 把标题刷成绿底 (Label 分支按角色同时刷 bg/fg), 属语义误用, 此处纠正。
    title = tkinter.Label(lh, text="运行日志", font=FONT_SMALL_BOLD,
                          fg=C["fgt"], bg=C["bgc"])
    ui_theme.set_fg_token(title, "fgt")
    title.pack(side="left")

    # 计数: 条数与日志体行数实时同步 (flush / 清空时刷新, 见 _log_update_count)
    count_lbl = tkinter.Label(lh, text="0 条", font=FONT_SMALL,
                              fg=C["log_ts"], bg=C["bgc"])
    count_lbl.pack(side="left", padx=(_spx(8), _spx(10)))

    # 级别视图: 幽灵 Menubutton (文本「全部 ▾」+ 下拉菜单), 取代旧 ttk.Combobox —
    # 去边框/去槽底, 与单行头的文字按钮同一视觉层级 (§4.9.3 tertiary)。
    log_level_var = tkinter.StringVar(value="全部")
    level_btn = tkinter.Menubutton(lh, text="全部 ▾", font=FONT_SMALL,
        bg=C["bgc"], fg=C["fgm"], activebackground=C["caption_hover"],
        activeforeground=C["fgt"], relief="flat", bd=0, highlightthickness=0,
        cursor="hand2", padx=_spx(6), pady=0, indicatoron=False, anchor="w")
    level_btn.pack(side="left", padx=(0, _spx(6)))
    level_menu = tkinter.Menu(level_btn, tearoff=0, font=FONT_SMALL,
        bg=C["bgc"], fg=C["fgb"], activebackground=C["acl"],
        activeforeground=C["fgt"], bd=0)
    level_btn.configure(menu=level_menu)

    def _pick_level(value):
        try:
            log_level_var.set(value)
        except Exception:
            pass
        try:
            level_btn.configure(text="{} ▾".format(value))
        except Exception:
            pass
        apply_filter(full=True)

    for _lv in _LEVEL_CHOICES:
        level_menu.add_command(label=_lv, command=lambda v=_lv: _pick_level(v))
    _log_level_combo = level_btn

    # 搜索框: 1px 发丝描边容器 + 放大镜字形 + 无边框 Entry + 占位提示
    sbox = tkinter.Frame(lh, bg=C["ebg"], bd=0, highlightthickness=1,
        highlightbackground=C["bd"], highlightcolor=C["focus"])
    sbox.pack(side="left", padx=(0, _spx(2)))
    search_icon = tkinter.Label(sbox, text="⊙", font=FONT_SMALL,
                                fg=C["log_ts"], bg=C["ebg"])
    search_icon.pack(side="left", padx=(_spx(6), 0))
    log_search_var = tkinter.StringVar(value="")
    entry = tkinter.Entry(sbox, textvariable=log_search_var, font=FONT_SMALL,
        width=22, relief="flat", bd=0, bg=C["ebg"], fg=C["fgb"],
        insertbackground=C["fgt"], highlightthickness=0)
    entry.pack(side="left", padx=(_spx(4), _spx(8)), pady=_spx(1))
    entry.bind("<Return>", lambda e: search_next(reset=True))
    entry.bind("<KP_Enter>", lambda e: search_next(reset=True))
    # 占位提示: 用叠加 Label 而非写进 StringVar —— 后者会污染搜索关键字 (get() 带提示语)
    ph = tkinter.Label(entry, text="搜索关键字…", font=FONT_SMALL,
                       fg=C["log_ts"], bg=C["ebg"])

    def _ph_sync(*_a):
        try:
            if log_search_var.get():
                ph.place_forget()
            else:
                ph.place(x=1, rely=0.5, anchor="w")
        except Exception:
            pass

    try:
        log_search_var.trace_add("write", _ph_sync)
    except Exception:
        pass
    _ph_sync()
    ph.bind("<Button-1>", lambda e: entry.focus_set())
    _log_search_entry = entry
    _log_search_var = log_search_var

    # 幽灵文字按钮 (tertiary: 无底无边, hover 才显 caption_hover 底)
    def _ghost(text, cmd, side="left", padx=(0, _spx(2))):
        b = tkinter.Button(lh, text=text, font=FONT_SMALL, bg=C["bgc"], fg=C["fgm"],
            activebackground=C["caption_hover"], activeforeground=C["fgt"],
            relief="flat", bd=0, highlightthickness=0, cursor="hand2",
            padx=_spx(6), pady=0, command=cmd)
        b.pack(side=side, padx=padx)
        return b

    _ghost("搜索", lambda: search_next(reset=True), padx=(_spx(4), _spx(2)))
    _ghost("下一个", lambda: search_next(reset=False))

    # 右侧组 (自右向左 pack): 收起 ▸ 导出 ▸ 清空 ▸ 自动滚动
    _log_collapse_btn = tkinter.Button(lh, text="▲ 收起", font=FONT_SMALL,
        bg=C["surface_alt"], fg=C["fgb"], activebackground=C["caption_hover"],
        activeforeground=C["fgt"], relief="flat", bd=0, highlightthickness=1,
        highlightbackground=C["bd"], cursor="hand2", padx=_spx(8), pady=0,
        command=toggle_body)
    _log_collapse_btn.pack(side="right", padx=(_spx(6), 0))
    export_btn = _ghost("导出", lambda: export_view(), side="right")
    clear_btn = _ghost("清空", lambda: clear_view(), side="right")

    # 自动滚动: 「状态点 + 文字」两段式 (点随状态着色, 文字保持中性; 旧 Checkbutton
    # 的方框在 30px 单行头里过于笨重, 且无法只给「点」上色)
    log_auto_scroll = tkinter.BooleanVar(value=True)
    auto_box = tkinter.Frame(lh, bg=C["bgc"])
    auto_box.pack(side="right", padx=(_spx(10), 0))
    auto_dot = tkinter.Label(auto_box, text="●", font=FONT_SMALL, bg=C["bgc"],
                             fg=C["sc"], cursor="hand2")
    auto_dot.pack(side="left", padx=(0, _spx(3)))
    auto_txt = tkinter.Label(auto_box, text="自动滚动", font=FONT_SMALL, bg=C["bgc"],
                             fg=C["fgb"], cursor="hand2")
    auto_txt.pack(side="left")
    for _w in (auto_dot, auto_txt):
        _w.bind("<Button-1>", lambda e: _toggle_autoscroll())

    def _toggle_autoscroll():
        try:
            log_auto_scroll.set(not log_auto_scroll.get())
        except Exception:
            return
        sync_auto_look()
        autoscroll_changed()

    _log_auto_scroll = log_auto_scroll
    _log_level_var = log_level_var

    # 头部与日志体之间的分隔线 (视觉稿 A5: 1px 发丝线, 全宽)
    rule = tkinter.Frame(card_log, bg=C.get("log_rule", C["bd"]), height=1)
    rule.grid(row=1, column=0, sticky="ew")

    # 将日志 Dock 作为 pane#1 加入 (最小 60; stretch=always 随窗口等比, 不抢增量)
    if _main_paned is not None:
        _main_paned.add(parent, minsize=60, height=160, stretch="always")
        # 首帧后恢复/设置 sash: 优先 state.LAYOUT_SASH, 否则「窗口高 − 180」
        try:
            _main_paned.after_idle(_restore_sash)
        except Exception:
            pass

    # ── ② 日志体 ────────────────────────────────────────────────────────
    log_frame = tkinter.Frame(card_log, bg=C["logbg"])
    log_frame.grid(row=2, column=0, sticky="nsew", padx=0, pady=(0, _spx(4)))
    log_frame.columnconfigure(0, weight=1); log_frame.rowconfigure(0, weight=1)
    _log_frame = log_frame

    # spacing1 = 行上留白 (≈18px 行高, 视觉稿 A5); 由控件级选项承担, 不额外占用 tag
    rz = tkinter.Text(log_frame, font=FONT_LOG, fg=C["logfg"], bg=C["logbg"],
        wrap="word", relief="flat", bd=0, padx=pad_x, pady=_spx(2),
        spacing1=_spx(2), insertbackground=C["fgt"],
        selectbackground=C["acl"], selectforeground=C["fgt"], undo=True, maxundo=50)
    rz.grid(row=0, column=0, sticky="nsew")
    # 滚动条厚度统一取 TOKENS["sb_w"] (D10); 本模块不 import utils, 故运行时惰性取
    try:
        from utils import sp as _sp
        _sb_w = _sp("sb_w")
    except Exception:
        _sb_w = 8
    scroll = tkinter.Scrollbar(log_frame, width=_sb_w, relief="flat", elementborderwidth=0,
        bg=C["bd"], activebackground=C["fgm"], troughcolor=C["logbg"])
    scroll.grid(row=0, column=1, sticky="ns")

    scroll.config(command=rz.yview); rz.config(yscrollcommand=scroll.set)
    _configure_tags(rz, C)
    _rz = rz
    _scroll = scroll

    # 行悬停: 光标所在行淡底 (只在跨行时动 Tcl; 3 次调用/次跨行)
    rz.bind("<Motion>", _log_hover, add="+")
    rz.bind("<Leave>", _log_hover_leave, add="+")

    # 头部自适应: 关键字框吃掉剩余宽度 (窄窗 / 大字号时不挤掉右侧功能组)
    def _head_fit(event=None):
        try:
            w = lh.winfo_width()
        except Exception:
            return
        if w <= 1:
            return
        others = 0
        for _c in lh.winfo_children():
            if _c not in (sbox,):
                try:
                    others += int(_c.winfo_reqwidth())
                except Exception:
                    pass
        try:
            per = max(1.0, entry.winfo_reqwidth() / float(max(1, int(entry.cget("width")))))
        except Exception:
            per = 8.0
        avail = w - others - _spx(60)          # 60: 预留组间距与 pack 内边距
        cols = int(max(10, min(26, avail / per)))
        if cols == _FIT["cols"]:
            return
        _FIT["cols"] = cols
        try:
            entry.configure(width=cols)
        except Exception:
            pass

    lh.bind("<Configure>", _head_fit, add="+")

    # 控件登记表: 换肤回调按此逐项回填 (避免 refresh_theme 里再散落一堆闭包变量)
    _UI.update({
        "card": card_log, "head": lh, "title": title, "count": count_lbl,
        "rule": rule, "level": level_btn, "level_menu": level_menu,
        "search_box": sbox, "search_icon": search_icon, "ph": ph, "entry": entry,
        "auto_box": auto_box, "auto_dot": auto_dot, "auto_txt": auto_txt,
        "clear": clear_btn, "export": export_btn, "collapse": _log_collapse_btn,
    })

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
    """ThemeBus 订阅回调 (签名与 publish 派发契约一致): 单行头 + 日志体 + 全部 tag 随主题重配。

    D3: 日志区 tag 前景随主题刷新 (旧实现仅建窗时设一次, 切换后 tag 文字仍是旧色)。
    v6: 头部控件已从 ttk.Combobox / Checkbutton 改为 tk Button/Menubutton/Label,
    通用 walker 只会把它们刷成中性色, 故这里逐项按设计语义回填 (在 publish 时执行,
    晚于 _theme_walk → 本函数的结果为准)。
    """
    C = colors if isinstance(colors, dict) else _CTX.get("colors")
    if not isinstance(C, dict) or _rz is None:
        return
    _CTX["colors"] = C
    U = _UI
    # ① 日志体 + 滚动条
    try:
        _rz.configure(bg=C["logbg"], fg=C["logfg"],
            selectbackground=C["acl"], selectforeground=C["fgt"],
            insertbackground=C["fgt"])
        if _scroll is not None:
            _scroll.configure(bg=C["bd"], activebackground=C["fgm"], troughcolor=C["logbg"])
    except Exception:
        pass
    # ② 全部 tag (与建窗同一实现: 单一真源)
    try:
        _configure_tags(_rz, C)
    except Exception:
        pass
    # ③ 卡片 / 单行头
    try:
        U["card"].configure(bg=C["bgc"], highlightbackground=C["bd"])
        U["head"].configure(bg=C["bgc"])
        U["title"].configure(bg=C["bgc"], fg=C["fgt"])
        U["count"].configure(bg=C["bgc"], fg=C.get("log_ts", C["fgm"]))
        U["rule"].configure(bg=C.get("log_rule", C["bd"]))
    except Exception:
        pass
    # ④ 级别视图 (幽灵 Menubutton + 下拉菜单)
    try:
        U["level"].configure(bg=C["bgc"], fg=C["fgm"],
            activebackground=C["caption_hover"], activeforeground=C["fgt"])
        U["level_menu"].configure(bg=C["bgc"], fg=C["fgb"],
            activebackground=C["acl"], activeforeground=C["fgt"])
    except Exception:
        pass
    # ⑤ 搜索框 (1px 发丝描边容器)
    try:
        U["search_box"].configure(bg=C["ebg"], highlightbackground=C["bd"],
                                  highlightcolor=C["focus"])
        U["search_icon"].configure(bg=C["ebg"], fg=C.get("log_ts", C["fgm"]))
        U["ph"].configure(bg=C["ebg"], fg=C.get("log_ts", C["fgm"]))
        U["entry"].configure(bg=C["ebg"], fg=C["fgb"], insertbackground=C["fgt"])
    except Exception:
        pass
    # ⑥ 幽灵按钮组 / 收起钮
    for _key in ("clear", "export"):
        try:
            U[_key].configure(bg=C["bgc"], fg=C["fgm"],
                activebackground=C["caption_hover"], activeforeground=C["fgt"])
        except Exception:
            pass
    try:
        U["collapse"].configure(bg=C["surface_alt"], fg=C["fgb"],
            activebackground=C["caption_hover"], activeforeground=C["fgt"],
            highlightbackground=C["bd"])
    except Exception:
        pass
    # ⑦ 自动滚动开关 (开/关两态色不同, 由 sync_auto_look 统一决定)
    try:
        U["auto_box"].configure(bg=C["bgc"])
    except Exception:
        pass
    sync_auto_look()


# ══════════════════════════════════════════════════════════════════════
# 周期刷新 (原 ACRPA._periodic 内联段, 现收敛为一个调用)
# ══════════════════════════════════════════════════════════════════════

_LOG_MAX_LINES = 6000   # 兜底上限, 实际由 state.LOG_MAX_LINES 覆盖 (0=不限)


def _log_max_lines():
    """日志面板行数上限 (state.LOG_MAX_LINES; 缺省/异常时用模块兜底值)。

    注意 state 的配置项在模块里是大写属性 (``globals()[key.upper()]``), 配置键
    ``log_max_lines`` 对应 ``state.LOG_MAX_LINES``; 这里顺带兼容小写访问, 避免
    调用方/测试自行 setattr 小写名时"设了不生效"。0 表示不限, 是合法值, 不能
    用 ``or`` 兜底。
    """
    try:
        import state
    except Exception:
        return _LOG_MAX_LINES
    for name in ("LOG_MAX_LINES", "log_max_lines"):
        value = getattr(state, name, None)
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
    return _LOG_MAX_LINES


def _log_trim():
    """裁剪超限的早期日志行 (行数环形上限), 并按裁剪行数回退增量游标。

    为什么需要: Text 控件行数无上限增长, 长时间运行时 insert/tag_add 与重绘
    成本线性上升, 表现为「越跑越卡、滚动发涩」。这里用一次 delete 裁掉最早的行,
    同时把 _cbar_state/_logfilter 的 1-based 游标左移, 避免游标与新行号错位。
    带滞回 (超出上限 10%/200 行才裁), 避免每批日志都触发整段删除重绘。
    """
    limit = _log_max_lines()
    if limit <= 0:
        return 0
    try:
        total = int(float(_rz.index("end-1c")))
    except Exception:
        return 0
    # D6: 滞回门槛原为 limit + max(200, limit//10), limit<200 时永远达不到 →
    #   小上限配置 (LOG_MAX_LINES=10/50) 事实上不裁剪, 行数无上限增长。
    if total <= limit + max(20, min(200, limit // 2)):
        return 0
    excess = total - limit
    try:
        _rz.delete("1.0", "{}.0".format(excess + 1))
    except Exception:
        return 0
    _cbar_state["done"] = max(0, _cbar_state["done"] - excess)
    _logfilter["applied"] = max(0, _logfilter["applied"] - excess)
    _hover["line"] = 0          # 行号整体左移 → 旧的悬停行号失效, 交给下一次 Motion 重建
    # D6: 裁掉奇数行会让 zebra 奇偶整体反转 (后续追加新行时出现连续两行同样式),
    #   故裁剪后按新行号整段重建 zebra (一次 tag_remove + 一次批量 tag_add)。
    try:
        _log_rebuild_zebra()
    except Exception:
        pass
    return excess


def _log_rebuild_zebra():
    """按当前行号重建斑马纹 (lzebra) —— 裁剪后行号整体左移, 奇偶必须重算。

    只对「已处理行」上 zebra (即行首已插过 ▌ 的行, 1.._cbar_state["done"]):
    未处理行由 _log_apply_cbar 自己加, 这里不抢跑, 避免与增量游标交叉。
    批量 tag_add (一次 Tcl 调用) 取代逐行 tag_add, 保持热路径成本可控。
    """
    try:
        total = int(float(_rz.index("end-1c")))
    except Exception:
        return
    try:
        done = max(0, min(int(_cbar_state.get("done", 0) or 0), total))
    except Exception:
        done = total
    try:
        _rz.tag_remove("lzebra", "1.0", "end")
    except Exception:
        return
    pairs = []
    for i in range(1, done + 1):
        if i % 2 == 0:
            pairs.append("{}.1".format(i))
            pairs.append("{}.end".format(i))
    if pairs:
        try:
            _rz.tag_add("lzebra", *pairs)
        except Exception:
            pass


def _log_has_pending():
    """生产端队列里是否还有待渲染的日志行。

    v5 §3.5 G3 的「事件驱动短路」判据: 队列空 = 没有新内容 = 本轮 flush 什么都不用做。
    旧实现每 100/250 ms 无条件 flush 一次 (4 次/秒空转), 这是空闲唤醒的大头之一。
    """
    if _log_flush_force:
        return True
    hp = getattr(_tlog, "has_pending", None)
    if hp is None:
        return True
    try:
        return bool(hp())
    except Exception:
        return True


# 一次性强制 flush (建面板后首帧/换肤/清空后重绘用), 见 force_flush()
_log_flush_force = False


def force_flush():
    """下一次 flush_and_apply 忽略「队列空短路」, 强制跑一遍全链路。"""
    global _log_flush_force
    _log_flush_force = True


def flush_and_apply():
    """等价原 _periodic 的日志段: flush + 裁剪 + 左缘色条 + 增量级别过滤。

    ③ 自动滚动: 关闭时保留当前视口, 避免新日志把视图强行拉到底部。
    返回 False 表示 flush 失败 (原实现此处 `return` 提前结束 _periodic, 宿主须沿用)。

    开关 `LOG_FLUSH_IDLE_SKIP` (默认开): 队列空时**立即返回**, 0 次 Tcl 调用;
    置 False 回到旧行为 (每轮无条件 flush)。
    """
    global _log_flush_force
    if _tlog is None or _rz is None:
        return True
    if LOG_FLUSH_IDLE_SKIP and not _log_has_pending():
        return True
    _log_flush_force = False
    if _log_auto_scroll is not None and _log_auto_scroll.get():
        try:
            _tlog.flush(_CTX.get("root"))
        except Exception:
            return False
        _log_trim()
        _log_apply_cbar()
    else:
        _prev_y = _rz.yview()
        try:
            _tlog.flush(_CTX.get("root"))
        except Exception:
            return False
        try:
            _log_trim()
        except Exception:
            pass
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
    # ④ 头部「N 条」计数 (仅在文本变化后的 flush 里跑, 空闲轮次已被短路)
    _log_update_count()
    return True


# ══════════════════════════════════════════════════════════════════════
# 收起/展开 + 拖高 (原 ACRPA:3213-3241)
# ══════════════════════════════════════════════════════════════════════

def _toggle_log_dock(event=None):
    """收起/展开底部常驻日志面板 (记忆收起前高度, 并持久化 sash)。"""
    try:
        if _log_dock_state["visible"]:
            try: _log_dock_state["h"] = _parent_dock.winfo_height() or 160
            except Exception: pass
            _persist_sash(_log_dock_state["h"])
            _main_paned.forget(_parent_dock)
            _log_dock_state["visible"] = False
        else:
            _main_paned.add(_parent_dock, minsize=60,
                            height=_log_dock_state["h"], stretch="always")
            _log_dock_state["visible"] = True
            try:
                _main_paned.after_idle(_restore_sash)
            except Exception:
                pass
        try:
            _log_dock_toggle_btn.config(
                text=("▾ 日志" if _log_dock_state["visible"] else "▸ 日志"))
        except Exception:
            pass
    except Exception:
        pass


def toggle_body(event=None):
    """折叠/展开日志体 (§3.5): 只隐表体, 保留标题 + 工具条, 按钮文案随状态切换。"""
    global _log_body_collapsed
    try:
        _log_body_collapsed = not _log_body_collapsed
        if _log_frame is not None:
            if _log_body_collapsed:
                _log_frame.grid_remove()
            else:
                _log_frame.grid()
        if _log_collapse_btn is not None:
            _log_collapse_btn.config(text="展开 ▼" if _log_body_collapsed else "收起 ▲")
        if not _log_body_collapsed:
            # 展开时补一次刷新, 确保表体重新可见后内容最新 (§3.5)
            try:
                flush_and_apply()
            except Exception:
                pass
    except Exception:
        pass


def _persist_sash(h=None):
    """把日志 Dock 高度写入 state.LAYOUT_SASH["main"]["dock"] (单键写盘)。"""
    try:
        if h is None:
            h = _log_dock_state.get("h", 160)
        import state
        state.set_layout_sash("main", "dock", max(60, int(h)), persist=True)
    except Exception:
        pass


def _restore_sash(_attempt=0):
    """构建/展开后恢复日志 Dock 高度 (优先缓存, 否则「窗口高 − 180」)。"""
    try:
        if _main_paned is None or _parent_dock is None:
            return
        total_h = _main_paned.winfo_height()
        if total_h <= 1:
            # 首帧可能尚未映射; 有界重试, 避免「默认 160 永不纠正」
            if _attempt < 3:
                try:
                    _main_paned.after(120, lambda: _restore_sash(_attempt + 1))
                except Exception:
                    pass
            return
        cached = None
        try:
            import state
            cached = state.get_layout_sash().get("main", {}).get("dock")
        except Exception:
            cached = None
        if cached is None:
            h = 180
        else:
            h = max(60, min(int(cached), max(60, total_h - 200)))
        # tkinter.PanedWindow 无 sashpos; 垂直分栏用 sash_place(0, 0, y)
        state.sash_set_pos(_main_paned, 0, max(200, total_h - h))
    except Exception:
        pass


def _on_log_sash_release(event=None):
    """拖动 sash 后记忆日志面板高度并持久化 (§5.4)。"""
    try:
        if _log_dock_state["visible"]:
            _log_dock_state["h"] = max(60, _parent_dock.winfo_height())
            _persist_sash(_log_dock_state["h"])
    except Exception:
        pass


def is_visible():
    """日志面板当前是否展开 (供宿主做高度断点判定)。"""
    return bool(_log_dock_state.get("visible", False))


def set_visible(visible):
    """按需展开/收起日志面板, **幂等** (状态未变则什么都不做)。

    宿主用它实现「窗口变矮自动收起、变高自动展开」的高度断点 (v3 §2.1 断点表),
    不用关心当前状态, 也不会因重复调用而反复 add/forget pane。
    """
    want = bool(visible)
    if want == is_visible():
        return want
    try:
        _toggle_log_dock()
    except Exception:
        return is_visible()
    return is_visible()


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
    """返回某行(1-based)命中的级别 tag, 无则返回空串。

    v5 §3.4 L4: 级别 tag 的区间仍从行首开始 (只是被优先级更高的 cbar_* 在
    第 1 个字符上盖住), 故 `tag_names(line.0)` 依然能取到级别 tag —— 原「由
    cbar_<tag> 反推级别」的反查分支已按文档删除 (不再需要)。
    """
    try:
        names = _rz.tag_names("{}.0".format(line_no))
    except Exception:
        return ""
    for t in _LOG_LEVEL_TAGS:
        if t in names:
            return t
    return ""


def _log_level_runs(start, total):
    """一次性把「行号 → 级别」摊平, 返回按级别分组的连续区间 [(lv, a, b), ...]。

    改造前 `_log_apply_cbar` / `_log_apply_filter` 对**每一行**都做
    `tag_names(line)` 查询 (一次 Tcl 往返/行), 6000 行日志就是 6000 次解释器调用;
    这里改成对每个级别各取一次 `tag_ranges`, 在 Python 里建行号映射。

    复杂度: Tcl 往返从 O(行) 降到 O(级别数=5), 其余全在内存里算。
    """
    line_level = {}
    for lv in _LOG_LEVEL_TAGS:
        try:
            rngs = _rz.tag_ranges(lv)
        except Exception:
            continue
        for k in range(0, len(rngs), 2):
            # D1: 相邻同 tag 的日志在 ThreadSafeLog 里被合并成**一个跨多行**的
            # tag range, 只登记左端会让第 2..N 行丢级别 (色条回退 cbar_info、
            # 级别过滤把这些行一起隐藏) → 必须把整个区间摊平进 line_level。
            try:
                a = int(float(str(rngs[k])))
            except Exception:
                continue
            b = a
            if k + 1 < len(rngs):
                try:
                    end = float(str(rngs[k + 1]))
                    b = int(end)
                    # end 落在 "N.0" 表示区间止于上一行行尾 → 归到 N-1 行
                    if end - b < 1e-9 and b > a:
                        b -= 1
                except Exception:
                    b = a
            if b < a:
                b = a
            for ln in range(max(a, start), min(b, total) + 1):
                line_level.setdefault(ln, lv)
    runs = []
    i = start
    while i <= total:
        lv = line_level.get(i, "")
        j = i
        while j + 1 <= total and line_level.get(j + 1, "") == lv:
            j += 1
        runs.append((lv, i, j))
        i = j + 1
    return runs


def _row_marks(line, level):
    """求一行的「时间戳 / 徽标块」字符列区间: (ts_a, ts_b, b_a, b_b, btag)。

    列偏移由 core.log 的版式表派生 (log_row_layout), 不在此另写一份;
    再以「徽标名是否真的落在徽标槽位内」做一次校验 —— 不符合行版式的行
    (例如外部直接 insert 的纯文本) 返回 None, 调用方跳过着色即可。
    """
    if not level or level not in _BADGE_NAME_OF:
        return None
    lead = 1 if line.startswith("▌") else 0
    ts_a, ts_b, b_a, b_b = _core_layout(lead)
    if len(line) < ts_b + 1:
        return None
    name = _BADGE_NAME_OF[level]
    if line.find(name, b_a, b_b) < 0:
        return None
    # 色块严格取「徽标槽位」的 8 列 (文案在槽内居中), 与版式表一一对应
    return ts_a, ts_b, b_a, min(len(line), b_b), _BADGE_TAG_OF.get(level)


def _log_apply_cbar():
    """为新增日志行: 行首色条着色 + 偶行加极淡 zebra (§4.6)。

    机制: tkinter Text 无 per-row 边框, 故按文档 §4.6 以「行首窄形字符 ▌(U+258C)
    + cbar_<tag> 颜色 tag」承载左缘 2px 色条; 级别由 tag 判定 (§2.5)。

    v5 §3.4 L4 收敛 (开关 LOG_CBAR_INLINE, 默认开):
      ① ▌ 字形由生产端 (core/log.py::flush) 内联进整批文本 → 本函数 0 次 insert;
      ② 着色改为「先收集区间列表, 再按 tag 一次 tag_add(*flat_ranges)」——
         单级别 400 行一批只需 **2 次** tag_add (cbar 1 次 + lzebra 1 次),
         改造前是 400 次 insert + 400~600 次 tag_add ≈1200 次 Tcl 往返。
         混合级别时次数 = 出现的级别数 + (有偶数行 ? 1 : 0), 仍远低于逐行方案。
    TEXT 的 tag_add 支持一次传多组 (i1,i2,i3,i4,...) 区间, 这是收敛的关键。
    回退: LOG_CBAR_INLINE=False → 走下面的逐行分支, 行为与改造前一致。

    幂等: 以 _cbar_state["done"] 游标为准; 内联分支额外用「行首 ▌」二次防重。
    批量读: 全文切片 get 一次 + 每级 tag_ranges 一次, 替代逐行 get/tag_names。
    """
    start = _cbar_state["done"] + 1
    try:
        total = int(float(_rz.index("end-1c")))
    except Exception:
        return
    if start > total:
        return
    try:
        lines = _rz.get("{}.0".format(start), "end-1c").split("\n")
    except Exception:
        return
    line_level = {}
    for lv, a, b in _log_level_runs(start, total):
        if lv:
            for n in range(a, b + 1):
                line_level[n] = lv
    if LOG_CBAR_INLINE:
        # ── 批量分支: 收集区间 → 每 tag 一次 tag_add ──
        groups = {}          # ctag → [i1, i2, i3, i4, ...] (成对区间)
        badges = {}          # lbadge_* → 成对区间 (徽标色块)
        stamps = []          # lts 成对区间 (时间戳降权)
        zebra = []           # lzebra 的扁平区间列表
        done = _cbar_state["done"]
        for i in range(start, total + 1):
            try:
                line = lines[i - start]
            except Exception:
                break
            if line == "":
                # Text 末尾隐含空行: 不推进游标, 留待有内容后再处理
                continue
            if not line.startswith("▌"):
                # 未内联 (开关中途翻转/外部直接 insert): 就地补一次字形
                try:
                    _rz.insert("{}.0".format(i), "▌")
                    line = "▌" + line
                except Exception:
                    continue
            lv = line_level.get(i, "")
            ctag = _CBAR_LEVEL_OF.get(lv, "cbar_info")
            groups.setdefault(ctag, []).extend(
                ["{}.0".format(i), "{}.1".format(i)])
            if i % 2 == 0:
                zebra.extend(["{}.1".format(i), "{}.end".format(i)])
            # 时间戳 / 徽标块着色 (定宽槽位, 同一批内仍按 tag 各一次 tag_add)
            marks = _row_marks(line, lv)
            if marks is not None:
                ts_a, ts_b, b_a, b_b, btag = marks
                stamps.extend(["{}.{}".format(i, ts_a), "{}.{}".format(i, ts_b)])
                if btag:
                    badges.setdefault(btag, []).extend(
                        ["{}.{}".format(i, b_a), "{}.{}".format(i, b_b)])
            done = i
        for ctag, rngs in groups.items():
            try:
                _rz.tag_add(ctag, *rngs)
            except Exception:
                pass
        if stamps:
            try:
                _rz.tag_add("lts", *stamps)
            except Exception:
                pass
        for btag, rngs in badges.items():
            try:
                _rz.tag_add(btag, *rngs)
            except Exception:
                pass
        if zebra:
            try:
                _rz.tag_add("lzebra", *zebra)
            except Exception:
                pass
        _cbar_state["done"] = done
        return
    # ── 逐行分支 (LOG_CBAR_INLINE=False 的回退路径, 与改造前一致) ──
    for i in range(start, total + 1):
        try:
            line = lines[i - start]
            # 幂等: 行首已含 ▌ → 本行已处理过, 直接推进而不重复插入
            if line.startswith("▌"):
                _cbar_state["done"] = i
                continue
            # Text 末尾隐含空行: 跳过且不推进, 留待有内容后再处理
            if line == "":
                continue
            lv = line_level.get(i, "")
            ctag = _CBAR_LEVEL_OF.get(lv, "cbar_info")
            _rz.insert("{}.0".format(i), "▌")
            line = "▌" + line
            _rz.tag_add(ctag, "{}.0".format(i), "{}.1".format(i))
            if i % 2 == 0:
                _rz.tag_add("lzebra", "{}.1".format(i), "{}.end".format(i))
            marks = _row_marks(line, lv)
            if marks is not None:
                ts_a, ts_b, b_a, b_b, btag = marks
                _rz.tag_add("lts", "{}.{}".format(i, ts_a), "{}.{}".format(i, ts_b))
                if btag:
                    _rz.tag_add(btag, "{}.{}".format(i, b_a), "{}.{}".format(i, b_b))
            _cbar_state["done"] = i
        except Exception:
            # 单行失败: 保留 done 于上一成功行; 下次以「行首▌」判定避免重复
            continue


def _log_apply_filter(full=False):
    """按级别过滤日志行 (用 elide 隐藏不匹配行). 全部=显示所有

    批量: 用 _log_level_runs 得到「级别连续段」, 每段一次 tag_add/tag_remove,
    取代逐行 tag_names+tag_add (6000 行时从 12000 次 Tcl 调用降到几十次)。
    """
    try:
        sel = _log_level_var.get()
        total = int(float(_rz.index("end-1c")))
        start = 1 if full else (_logfilter["applied"] + 1)
        if start < 1:
            start = 1
        if start <= total:
            if sel == "全部":
                _match = lambda _lv: True            # noqa: E731 (分支内小型判定)
            elif sel == "INFO":
                _match = lambda lv: lv in ("", "info")   # noqa: E731
            else:
                _want = sel.lower()
                _match = lambda lv, _w=_want: lv == _w   # noqa: E731

            # 合并相邻且判定一致的段, 一段一次 tag 操作
            segs = []
            for lv, a, b in _log_level_runs(start, total):
                ok = _match(lv)
                if segs and segs[-1][0] == ok and segs[-1][2] + 1 == a:
                    segs[-1] = (ok, segs[-1][1], b)
                else:
                    segs.append((ok, a, b))
            for ok, a, b in segs:
                if ok:
                    _rz.tag_remove("lf_hide", "{}.0".format(a), "{}.end+1c".format(b))
                else:
                    _rz.tag_add("lf_hide", "{}.0".format(a), "{}.end+1c".format(b))
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
        _hover["line"] = 0
        _rz.tag_remove("search_hit", "1.0", "end")
    except Exception:
        pass
    _log_update_count()


def _log_export_snapshot():
    """主线程一次性取走导出所需数据 (纯 Python 快照, 后台线程不再碰控件)。

    改造前: 逐行 `tag_names` + 逐行 `get` → 6000 行 = 12000 次 Tcl 往返 (30~80 ms
    全在主线程)。现在只需 2 次固定 Tcl 调用:
      ① `get("1.0","end-1c")` 一次取全文;
      ② `tag_ranges("lf_hide")` 一次取被过滤隐藏的行区间。
    返回 (lines, hidden_line_numbers)。
    """
    text = _rz.get("1.0", "end-1c")
    lines = text.split("\n")
    hidden = set()
    try:
        rngs = list(_rz.tag_ranges("lf_hide"))
    except Exception:
        rngs = []
    for k in range(0, len(rngs) - 1, 2):
        try:
            a = int(float(str(rngs[k])))
            end = float(str(rngs[k + 1]))
            b = int(end)
            if end - b < 1e-9 and b > a:
                b -= 1                      # end 落在 "N.0" = 止于上一行
        except Exception:
            continue
        for ln in range(max(1, a), b + 1):
            hidden.add(ln)
    return lines, hidden


def _log_export_write(fp, lines, hidden):
    """后台线程: 过滤隐藏行 + 去掉行首色条字形 ▌ 后写盘 (不碰任何 Tk 对象)。"""
    out = []
    for i, ln in enumerate(lines, 1):
        if i in hidden:
            continue
        # L4 副作用①: ▌ 是纯装饰字形, 导出文本必须 strip 掉 (与旧版逐字节一致)
        if ln.startswith("▌"):
            ln = ln[1:]
        out.append(ln)
    with open(fp, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + ("\n" if out else ""))
    return len(out)


def _log_export_view():
    """导出当前(可见)日志视图到文件。

    v5 P1-7: 主线程只做「文件对话框 + 一次性快照」(≤10 ms), 文本拼装与写盘
    交给 `ui_bg_task` 的 IO 池。开关 `LOG_EXPORT_ASYNC` (默认开) 置 False 时
    回到「主线程逐行 tag_names + get + 写盘」的旧实现。
    """
    try:
        default_name = "acrpa_log_{}.txt".format(
            datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
        fp = filedialog.asksaveasfilename(
            title="导出运行日志", initialdir=_CTX.get("app_root", ""), initialfile=default_name,
            defaultextension=".txt", filetypes=[("文本文件", "*.txt"), ("日志", "*.log")])
        if not fp:
            return
        if LOG_EXPORT_ASYNC:
            lines, hidden = _log_export_snapshot()
            try:
                import ui.bg_task as _bg
                def _ok(n):
                    _toast("日志已导出: {} ({} 行)".format(
                        os.path.basename(fp), n), "success")
                def _fail(e):
                    _log1("日志导出失败: {}".format(e), "error")
                    _toast("日志导出失败: {}".format(e), "error")
                if _bg.submit(lambda: _log_export_write(fp, lines, hidden),
                              _ok, _fail, name="log-export", pool="io"):
                    return
                # 无事件循环 (测试/CLI) → submit 已同步执行完毕
                return
            except Exception as e:
                _log1("日志导出降级为主线程: {}".format(e), "warning")
        # 回退路径: 主线程同步 (行为与改造前一致)
        total = int(float(_rz.index("end-1c")))
        out = []
        for i in range(1, total + 1):
            if "lf_hide" in _rz.tag_names("{}.0".format(i)):
                continue
            line = _rz.get("{}.0".format(i), "{}.end".format(i))
            out.append(line[1:] if line.startswith("▌") else line)
        with open(fp, "w", encoding="utf-8") as f:
            f.write("\n".join(out) + ("\n" if out else ""))
        _toast("日志已导出: {}".format(os.path.basename(fp)), "success")
    except Exception as e:
        _log1("日志导出失败: {}".format(e), "error")


# ── 公开接口别名 (宿主/自测按语义名引用; 原名保留以便逐行比对迁移) ──
toggle_dock = _toggle_log_dock
on_sash_release = _on_log_sash_release
autoscroll_changed = _log_autoscroll_changed
apply_cbar = _log_apply_cbar
apply_filter = _log_apply_filter
trim = _log_trim
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
    """头部「收起 ▲ / 展开 ▼」折叠按钮。"""
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
