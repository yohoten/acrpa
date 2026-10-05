# -*- coding: utf-8 -*-
"""ui.theme — 主题色板 / ttk 样式应用 / 换肤事件总线 (ThemeBus)。

路线图 §5.2「修法三合一」的落地模块:

  ① 语义按钮承载语义角色 —— 由控件创建处显式登记 (set_role)，换肤时按角色回填；
  ② 用 widget 的**语义角色**而非**中文文案/旧颜色值**判定颜色 ——
     彻底删除「嗅探 text 里的 ●/运行/就绪」与「按旧颜色值反推语义组」两种推断；
  ③ 引入 ThemeBus.subscribe(fn) —— 取代 `ACRPA._refresh_theme` 里对若干外部模块的
     手工 `C` 注入扇出；新增窗口只要 subscribe 即可自动换肤 (验收: §9 阶段二)。

设计约束
--------
· **零 GUI 副作用**: 本模块只定义函数/常量，`import ui.theme` 不建窗、不读屏幕。
· **不 import utils (模块级)**: utils 是本模块的调用方之一 (utils._colors/apply_theme
  委托本模块)，为避免循环导入，apply_theme 内在**运行时**惰性 `import utils`。
· 色板实现自 src/utils.py::_colors 迁入；utils._colors 保留为委托 (向后兼容)。
· 样式实现自 src/utils.py::apply_theme 迁入；utils.apply_theme 保留为委托，且
  仍重绑 utils.C (utils.themed 等直读 utils.C 的既有引用保持不变)。
"""
import state

__all__ = [
    "colors", "apply_theme",
    "set_role", "get_role", "clear_role", "roled", "resolve_bg", "SEMANTIC_TOKENS",
    "subscribe", "unsubscribe", "publish", "subscriber_count", "clear_subscribers",
    "claim_window", "is_claimed", "claimed_count",
]

# ── 语义色键 (角色名白名单): 只有这些键允许作为「语义角色」登记 ──
# 组内等价键 (ok=sc / err=dg / hover=ach / cardhover=ac) 一并列出, 便于按角色回填时
# 直接命中创建时所用的键, 无需再做「旧值→角色」反推。
SEMANTIC_TOKENS = ("sc", "ok", "dg", "err", "wn", "ac", "hover", "focus", "acl", "ach", "cardhover")


# ══════════════════════════════════════════════════════════════════════
# ① 色板 (实现自 src/utils.py::_colors 迁入)
# ══════════════════════════════════════════════════════════════════════

def colors(dark=None):
    """Windows-aligned palette (docs/UI美化设计方案.md §2.1/§2.2/§2.4).

    强调色收敛为 Windows 系统蓝 #0078D4 (旧 #2563eb 网页蓝已废弃);
    §6.1 新增键全部补齐 (light/dark 两套), **既有键一律保留** (向后兼容)。

    dark=None 时取 state.DARK_MODE。
    """
    if dark is None:
        dark = bool(getattr(state, "DARK_MODE", False))
    # hlbg: 「当前行/变更项」高亮底色 (脚本树 running 行、变量树 changed 项);
    #       旧实现写死 #FEF3C7, 暗色下刺眼, 现纳入主题色表统一随主题切换。
    if dark:
        return dict(
            # ── 既有键 (值按 §2.2 修订; 语义等价键 ok=sc / err=dg / hover=ach / cardhover=ac) ──
            bg="#202020", bgc="#2B2B2B", fgt="#FFFFFF", fgb="#E0E0E0",
            fgm="#A0A0A0", ac="#4CC2FF", ach="#3793D4", acl="#094771",
            sc="#6CCB5F", dg="#FF99A4", wn="#FCE100", bd="#3A3A3A",
            logbg="#2B2B2B", logfg="#E0E0E0", ebg="#2B2B2B",
            err="#FF99A4", errbg="#3a2525", ok="#6CCB5F",
            hover="#3793D4", cardhover="#4CC2FF", focus="#4CC2FF",
            flowbg="#0f172a", hlbg="#4D3B00",
            # ── 新增键 (§6.1: surface_alt/acp/fgd/border_strong/gridline/gutter_bg/
            #    heading_hover/caption_hover/row_hover/zebra/tooltip_*/log_*/search_hit_*) ──
            surface_alt="#262626", acp="#2B7AB8", fgd="#6E6E6E",
            border_strong="#4A4A4A", gridline="#333333", gutter_bg="#2A2A2A",
            heading_hover="#333333", caption_hover="#333333", row_hover="#2A2A2A",
            zebra="#262626", tooltip_bg="#3A3A3A", tooltip_border="#5A5A5A",
            log_info="#9A9A9A", log_success="#6CCB5F",
            log_warn="#FCE100", log_error="#FF99A4",
            search_hit_bg="#4D3B00", search_hit_border="#4CC2FF")
    return dict(
        # ── 既有键 (值按 §2.1 修订) ──
        bg="#F3F3F3", bgc="#FFFFFF", fgt="#1F1F1F", fgb="#333333",
        fgm="#5F5F5F", ac="#0078D4", ach="#106EBE", acl="#CCE4F7",
        sc="#107C10", dg="#C42B1C", wn="#F7630C", bd="#E1E1E1",
        logbg="#FFFFFF", logfg="#1F1F1F", ebg="#FFFFFF",
        err="#C42B1C", errbg="#fff0f0", ok="#107C10",
        hover="#106EBE", cardhover="#0078D4", focus="#0078D4",
        flowbg="#f8fafc", hlbg="#FFF4CE",
        # ── 新增键 (§6.1) ──
        surface_alt="#FAFAFA", acp="#005A9E", fgd="#9A9A9A",
        border_strong="#C8C8C8", gridline="#EEEEEE", gutter_bg="#F6F6F6",
        heading_hover="#F0F0F0", caption_hover="#E9E9E9", row_hover="#F3F3F3",
        zebra="#FAFAFA", tooltip_bg="#FFFFE1", tooltip_border="#646464",
        log_info="#8A8886", log_success="#107C10",
        log_warn="#F7630C", log_error="#C42B1C",
        search_hit_bg="#FFF4CE", search_hit_border="#0078D4")


# ══════════════════════════════════════════════════════════════════════
# ② 语义角色登记 (widget 自带的换肤身份, 取代文案/旧值推断)
# ══════════════════════════════════════════════════════════════════════

_ROLE_ATTR = "_acrpa_role"


def set_role(widget, token):
    """在控件上登记语义角色 (色板键名, 如 "sc"/"dg"/"wn"/"ac")。

    创建控件的那一刻就知道自己是什么语义 —— 显式登记即可, 换肤时按角色回填,
    无需再从「中文文案」或「旧主题颜色值」反推 (路线图 §5.2 修法②)。
    返回 widget, 便于链式写法 `theme.roled(tkinter.Button(...), "ac").pack()`。
    """
    try:
        setattr(widget, _ROLE_ATTR, token)
    except Exception:
        pass
    return widget


def get_role(widget):
    """取回控件登记的语义角色; 未登记返回 None (视为中性控件)。"""
    return getattr(widget, _ROLE_ATTR, None)


def clear_role(widget):
    """清除语义角色 (控件回到中性态, 如调试/单步模式关闭时)。"""
    try:
        delattr(widget, _ROLE_ATTR)
    except Exception:
        pass
    return widget


# roled(): set_role 的别名, 语义上「给这个控件一个角色」
def roled(widget, token):
    return set_role(widget, token)


def resolve_bg(value, palette=None):
    """把「色板键名」或「字面颜色」解析为 (颜色值, 角色名)。

    · 传入 "ac"/"sc"/"dg" 等色板键名 → (palette["ac"], "ac") —— 调用点即声明语义角色;
    · 传入 "#FFFFFF"/"white"/"#7C3AED" 等字面量 → (原值, None) —— 中性/品牌固定色。
    控件工厂 (_btn / _tbtn) 用它把「角色声明」与「取色」一次做完。
    """
    if palette is None:
        palette = colors()
    if isinstance(value, str) and value in palette:
        return palette[value], value
    return value, None


# ══════════════════════════════════════════════════════════════════════
# ③ 换肤事件总线 (ThemeBus)
# ══════════════════════════════════════════════════════════════════════

_subscribers = []   # [(fn,)] —— fn(dark, colors, prev)


def subscribe(fn):
    """注册换肤回调 fn(dark, colors, prev); 去重, 重复注册只登记一次。

    新增窗口只需 `ui.theme.subscribe(我的刷新函数)` 即可自动换肤,
    不必再去改 `ACRPA._refresh_theme` (阶段二验收 §9:480)。
    """
    if fn not in _subscribers:
        _subscribers.append(fn)
    return fn


def unsubscribe(fn):
    """注销换肤回调 (不存在时空操作, 不抛错)。"""
    global _subscribers
    _subscribers = [f for f in _subscribers if f is not fn]


def subscriber_count():
    """当前订阅者数量 (供自测断言幂等/去重)。"""
    return len(_subscribers)


def clear_subscribers():
    """清空全部订阅 (仅供自测隔离使用)。"""
    del _subscribers[:]


def publish(dark=None, colors=None, prev=None):
    """广播一次换肤事件: 逐个调用订阅者, 单个订阅者抛异常 **不影响** 其它订阅者。

    参数与 utils.register_ui_scale_hook 的隔离策略一致 (遍历副本 + try/except),
    避免某个窗口刷新失败导致整应用换肤中断。
    """
    if colors is None:
        colors = globals()["colors"](dark)
    for fn in list(_subscribers):
        try:
            fn(dark, colors, prev)
        except Exception:
            pass


# ── 独立窗口「自管换肤」声明 ──
# 各独立窗口 (设置/互联/帮助/市场/Mini Bar) 都有自己的主题重刷实现。若不声明,
# 通用 walk (ACRPA._walk / dialogs.refresh_theme) 会跨窗口把它们按「通用规则」重刷
# 一遍, 既浪费又可能覆盖各自的特化配色。声明后通用 walk 不再跨入该窗口,
# 由所属模块的订阅回调负责 —— 这也是「新增窗口自动换肤」的边界约定。
_claimed_windows = []   # [(widget, owner)]


def claim_window(win, owner=""):
    """声明该 Toplevel 由 owner 模块自行换肤 (通用 walk 将跳过它)。幂等。"""
    global _claimed_windows
    _claimed_windows = [(w, o) for (w, o) in _claimed_windows if w is not win]
    _claimed_windows.append((win, owner))
    return win


def _prune_claimed():
    """清掉已销毁的窗口条目, 避免无界增长。"""
    global _claimed_windows
    alive = []
    for w, o in _claimed_windows:
        try:
            if w.winfo_exists():
                alive.append((w, o))
        except Exception:
            pass
    _claimed_windows = alive


def is_claimed(win):
    """该 Toplevel 是否已声明「自管换肤」 (顺便清理已销毁条目)。"""
    _prune_claimed()
    for w, _o in _claimed_windows:
        if w is win:
            return True
    return False


def claimed_count():
    """当前存活的自管窗口数量 (自测用)。"""
    _prune_claimed()
    return len(_claimed_windows)


# ══════════════════════════════════════════════════════════════════════
# ④ ttk 样式应用 (实现自 src/utils.py::apply_theme 迁入)
# ══════════════════════════════════════════════════════════════════════

def apply_theme(root_widget, style):
    """Refresh colors dict and re-apply all ttk + widget styles.

    保持既有行为: 重绑 `utils.C` 为新色板 —— utils.themed()/tools 等既有引用
    直读 utils.C, 必须随之更新 (向后兼容契约)。
    字体角色/尺寸令牌仍唯一来自 utils (本模块不复制一份定义)。
    """
    import utils
    C = colors()
    utils.C = C
    _darken = utils._darken
    root_widget.configure(bg=C["bg"])

    # === Notebook / Tabs (经典矩形页签; 去选中蓝字 → 加粗 + 强调条) ===
    # §4.3 / §2.4: 未选 surface_alt, 选中 bgc 面 + 字色保持 fgt (删除 [("selected", C["ac"])])。
    # clam 无「顶部 2px 条」原生元素 → 降级: 选中加粗 + 强调色描边 (见结果说明)。
    style.configure("TNotebook", background=C["bg"], borderwidth=0)
    style.configure("TNotebook.Tab", font=utils.FONT_BODY, padding=(16, 6),
                    background=C["surface_alt"], foreground=C["fgm"],
                    borderwidth=1)
    style.map("TNotebook.Tab",
              font=[("selected", utils.FONT_BUTTON)],
              background=[("selected", C["bgc"]), ("active", C["caption_hover"])],
              foreground=[("selected", C["fgt"])],
              bordercolor=[("selected", C["ac"])],
              lightcolor=[("selected", C["ac"])],
              darkcolor=[("selected", C["ac"])])

    # === Treeview (数据表): 行高唯一 row_h + 选中整行 acl ===
    # §4.5: 消除旧 22 与 24 双写冲突, 统一 TOKENS["row_h"] (=22), 只保留一处。
    style.configure("Treeview", font=utils.FONT_BODY, rowheight=utils.TOKENS["row_h"],
                    background=C["bgc"], fieldbackground=C["bgc"],
                    foreground=C["fgb"], borderwidth=0)
    style.map("Treeview",
              background=[("selected", C["acl"])],
              foreground=[("selected", C["fgt"])],
              bordercolor=[("focus", C["focus"])])
    # 表头: 去蓝底白字 → 灰底 surface_alt + 深色字 fgb(#333) + 1px 下边线 bd (§4.5 / §10)
    style.configure("Treeview.Heading", font=utils.FONT_SMALL_BOLD,
                    background=C["surface_alt"], foreground=C["fgb"],
                    relief="flat", borderwidth=1, bordercolor=C["bd"],
                    padding=(8, 4))
    style.map("Treeview.Heading",
              background=[("active", C["heading_hover"])])

    # === Cards ===
    style.configure("Card.TFrame", background=C["bgc"], relief="solid", borderwidth=1)
    style.configure("MainBg.TFrame", background=C["bg"])

    # === Labels ===
    style.configure("Title.TLabel", background=C["bg"], foreground=C["fgt"], font=utils.FONT_TITLE)
    style.configure("Body.TLabel", background=C["bgc"], foreground=C["fgb"], font=utils.FONT_BODY)
    style.configure("Muted.TLabel", background=C["bgc"], foreground=C["fgm"], font=utils.FONT_SMALL)
    style.configure("Status.TLabel", background=C["bg"], foreground=C["fgm"], font=utils.FONT_SMALL)

    # === Entry / Combobox: 1px 发丝描边 + hover 加深 + focus 可视环 (§4.9.1) ===
    style.configure("TEntry", fieldbackground=C["ebg"], foreground=C["fgb"],
                    bordercolor=C["bd"], borderwidth=1, relief="flat",
                    insertcolor=C["fgt"])
    style.map("TEntry",
              bordercolor=[("focus", C["focus"]), ("hover", C["border_strong"])],
              lightcolor=[("focus", C["focus"])],
              darkcolor=[("focus", C["focus"])])
    style.configure("TCombobox", fieldbackground=C["bgc"], background=C["bgc"],
                    foreground=C["fgb"], font=utils.FONT_BODY, arrowsize=12,
                    bordercolor=C["bd"], borderwidth=1)
    style.map("TCombobox",
              fieldbackground=[("readonly", C["bgc"])],
              background=[("readonly", C["bgc"]), ("active", C["caption_hover"])],
              bordercolor=[("focus", C["focus"]), ("hover", C["border_strong"])],
              lightcolor=[("focus", C["focus"])],
              darkcolor=[("focus", C["focus"])],
              arrowcolor=[("focus", C["focus"])])

    # ─ Buttons (ttk): 扁平 + 1px 描边 + focus 可视环 + hover 底色&描边双变化 ─
    # 普通按钮 (secondary): surface_alt 底 + bd 发丝描边; hover = caption_hover 底 + border_strong 描边 (§4.9.2)
    style.configure("TButton", font=utils.FONT_BUTTON, padding=(10, 4),
                    background=C["surface_alt"], foreground=C["fgb"],
                    borderwidth=1, relief="flat",
                    bordercolor=C["bd"], focuscolor=C["focus"])
    style.map("TButton",
              background=[("pressed", C["acl"]), ("active", C["caption_hover"]),
                          ("disabled", C["bgc"])],
              foreground=[("disabled", C["fgd"])],
              bordercolor=[("focus", C["focus"]), ("active", C["border_strong"])],
              lightcolor=[("focus", C["focus"])],
              darkcolor=[("focus", C["focus"])])

    # 主操作 (primary, 全窗唯一实心 ac): Action —— 取消 focuscolor="none", 保留实心强调
    style.configure("Action.TButton", background=C["ac"], foreground="white",
                    borderwidth=1, relief="flat", bordercolor=C["ac"],
                    focuscolor=C["focus"], font=utils.FONT_BUTTON, padding=(10, 4))
    style.map("Action.TButton",
              background=[("active", C["ach"]), ("pressed", C["acp"]),
                          ("disabled", C["fgm"])],
              bordercolor=[("focus", C["focus"]), ("active", C["ach"])],
              lightcolor=[("focus", C["focus"])],
              darkcolor=[("focus", C["focus"])])

    # 语义实心按钮: 扁平 + hover「底色 + 描边」双变化 (非仅变暗); 取消 focuscolor="none"
    style.configure("Success.TButton", background=C["sc"], foreground="white",
                    borderwidth=1, relief="flat", bordercolor=C["sc"],
                    focuscolor=C["focus"], font=utils.FONT_BUTTON, padding=(10, 4))
    style.map("Success.TButton",
              background=[("active", _darken(C["sc"])), ("pressed", _darken(C["sc"], 0.3))],
              bordercolor=[("focus", C["focus"]), ("active", _darken(C["sc"]))],
              lightcolor=[("focus", C["focus"])],
              darkcolor=[("focus", C["focus"])])
    style.configure("Danger.TButton", background=C["dg"], foreground="white",
                    borderwidth=1, relief="flat", bordercolor=C["dg"],
                    focuscolor=C["focus"], font=utils.FONT_BUTTON, padding=(10, 4))
    style.map("Danger.TButton",
              background=[("active", _darken(C["dg"])), ("pressed", _darken(C["dg"], 0.3))],
              bordercolor=[("focus", C["focus"]), ("active", _darken(C["dg"]))],
              lightcolor=[("focus", C["focus"])],
              darkcolor=[("focus", C["focus"])])
    style.configure("Warning.TButton", background=C["wn"], foreground="white",
                    borderwidth=1, relief="flat", bordercolor=C["wn"],
                    focuscolor=C["focus"], font=utils.FONT_BUTTON, padding=(10, 4))
    style.map("Warning.TButton",
              background=[("active", _darken(C["wn"])), ("pressed", _darken(C["wn"], 0.3))],
              bordercolor=[("focus", C["focus"]), ("active", _darken(C["wn"]))],
              lightcolor=[("focus", C["focus"])],
              darkcolor=[("focus", C["focus"])])

    # === Progressbar - compact ===
    style.configure("Horizontal.TProgressbar", background=C["sc"],
                    troughcolor=C["bg"], borderwidth=0, thickness=5)
    style.configure("Success.Horizontal.TProgressbar", background=C["sc"],
                    troughcolor=C["acl"], borderwidth=0, thickness=5)
