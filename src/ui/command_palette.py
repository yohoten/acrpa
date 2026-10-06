# -*- coding: utf-8 -*-
"""ui.command_palette — 命令库 (Ctrl+K) + 命令分组 (路线图 §5.3)。

分层（与 ui.theme / ui.flow_canvas 同一取向：逻辑与建窗分离）
------------------------------------------------------------
* **纯逻辑（无 tkinter，可 headless 测试）**
    ``entries()``        → 全部命令条目（含分组），按分组顺序；
    ``grouped()``        → ``[(组标题, [条目, ...]), ...]``；
    ``filter_entries(q)``→ 空白分词模糊匹配 名称/说明，按相关度升序返回。

  数据源 **单一事实** = ``help_content.get_command_groups()``（即
  ``res/help/command_groups.json`` + ``commands`` 注册表 + 兜底组），与帮助窗口
  命令速查同源；失败时回退 ``commands.list_all()``（分组记兜底组）。

* **弹窗（惰性 import tkinter）**
    ``open_palette(parent, colors, fonts, on_pick, ...)`` —— 只在实际打开时导入
    tkinter；``import ui.command_palette`` 不建窗、无副作用。

设计取舍
--------
* 命令分组不新增配置：直接复用帮助系统的 ``command_groups.json``，避免两处维护。
* 相关度排序把「名称精确 > 名称前缀 > 名称包含 > 说明包含」分级，且全部关键字
  都必须命中（AND），避免宽泛说明词污染结果。
* 列表用经典 ``tkinter.Listbox``（``itemconfig`` 可按行着色）：组标题行着色 + 不可选，
  导航自动跳过标题行。
"""
__all__ = [
    "FALLBACK_GROUP", "entries", "grouped", "filter_entries", "group_titles",
    "open_palette",
]

# 未在 command_groups.json 中出现的命令（含插件新增）落此兜底组，永不丢失。
FALLBACK_GROUP = "其他命令"

# 相关度权重（越小越靠前）
_S_EXACT, _S_PREFIX, _S_NAME, _S_DESC = 0, 1, 2, 4


# ══════════════════════════════════════════════════════════════════════
# 纯逻辑
# ══════════════════════════════════════════════════════════════════════

def _from_help_content():
    """从 help_content.get_command_groups() 取条目（含分组标题）。"""
    import help_content
    out = []
    for title, items in help_content.get_command_groups():
        for it in items:
            out.append({"name": getattr(it, "name", ""),
                        "desc": getattr(it, "desc", "") or "",
                        "params": getattr(it, "params", "") or "",
                        "group": title or FALLBACK_GROUP})
    return [e for e in out if e["name"]]


def _from_registry():
    """兜底：直接读 commands 注册表。"""
    import commands
    return [{"name": n, "desc": d or "", "params": p or "", "group": FALLBACK_GROUP}
            for (n, d, p, _h) in commands.list_all() if n]


def entries():
    """→ ``[{"name","desc","params","group"}, ...]``，按分组顺序；数据源失败回退注册表。"""
    try:
        got = _from_help_content()
        if got:
            return got
    except Exception:
        pass
    try:
        return _from_registry()
    except Exception:
        return []


def grouped():
    """→ ``[(组标题, [条目, ...]), ...]``，组顺序 = 首次出现顺序。"""
    order, bag = [], {}
    for e in entries():
        g = e.get("group") or FALLBACK_GROUP
        if g not in bag:
            bag[g] = []
            order.append(g)
        bag[g].append(e)
    return [(g, bag[g]) for g in order]


def group_titles():
    """→ 分组标题列表（顺序）。"""
    return [g for g, _ in grouped()]


def _score(entry, tokens):
    """全部 token 都必须命中名称或说明，否则返回 None；否则返回相关度分。"""
    nl = (entry.get("name") or "").lower()
    dl = (entry.get("desc") or "").lower()
    total = 0
    for t in tokens:
        if nl == t:
            total += _S_EXACT
        elif nl.startswith(t):
            total += _S_PREFIX
        elif t in nl:
            total += _S_NAME
        elif t in dl:
            total += _S_DESC
        else:
            return None
    return total


def filter_entries(query):
    """空白分词模糊匹配 名称/说明；空 query 返回全部（保持分组顺序）。

    多关键字为 AND（全部命中）；相关度：名称精确 < 名称前缀 < 名称包含 < 说明包含；
    同分按名称长度升序（短名优先）。
    """
    q = (query or "").strip().lower()
    if not q:
        return entries()
    tokens = q.split()
    scored = []
    for e in entries():
        s = _score(e, tokens)
        if s is None:
            continue
        scored.append((s, len(e.get("name") or ""), e))
    scored.sort(key=lambda t: (t[0], t[1]))
    return [e for _s, _n, e in scored]


# ══════════════════════════════════════════════════════════════════════
# 弹窗（惰性 tkinter）
# ══════════════════════════════════════════════════════════════════════

def open_palette(parent, colors, fonts, on_pick, initial_query="",
                 title="命令库", width=520, height=440, set_icon=None):
    """打开命令库弹窗；选中条目后回调 ``on_pick(name)``。

    键盘：↑/↓ 选择（跳过组标题）、Enter 插入、Esc 取消、输入即时过滤。
    返回创建的 ``Toplevel``；``import ui.command_palette`` 本身不建窗。
    """
    import tkinter
    import utils

    C = colors if isinstance(colors, dict) else {}
    _f = fonts if isinstance(fonts, dict) else {}
    F_BODY = _f.get("body") or utils.FONT_BODY
    F_SMALL = _f.get("small") or utils.FONT_SMALL

    def _c(key, default="#000000"):
        return C.get(key, default)

    win = tkinter.Toplevel(parent)
    win.title(title)
    try:
        win.transient(parent)
    except Exception:
        pass
    win.configure(bg=_c("bgc", "#FFFFFF"))
    if callable(set_icon):
        try:
            set_icon(win)
        except Exception:
            pass
    try:
        parent.update_idletasks()
        px, py = parent.winfo_rootx(), parent.winfo_rooty()
        pw, ph = parent.winfo_width(), parent.winfo_height()
        x = px + max((pw - width) // 2, 0)
        y = py + max((ph - height) // 3, 0)
        win.geometry("{}x{}+{}+{}".format(width, height, x, y))
    except Exception:
        win.geometry("{}x{}".format(width, height))

    # ── 搜索框 ──
    top = tkinter.Frame(win, bg=_c("bgc", "#FFFFFF"))
    top.pack(fill="x", padx=8, pady=(8, 4))
    var = tkinter.StringVar(value=initial_query)
    ent = tkinter.Entry(top, textvariable=var, font=F_BODY,
                        bg=_c("ebg", "#FFFFFF"), fg=_c("fgb", "#333333"),
                        insertbackground=_c("fgt", "#000000"),
                        relief="solid", bd=1)
    ent.pack(fill="x", ipady=3)

    # ── 结果列表 ──
    mid = tkinter.Frame(win, bg=_c("bgc", "#FFFFFF"))
    mid.pack(fill="both", expand=True, padx=8)
    lb = tkinter.Listbox(mid, font=F_BODY, activestyle="none",
                         bg=_c("bgc", "#FFFFFF"), fg=_c("fgb", "#333333"),
                         selectbackground=_c("acl", "#CCE4F7"),
                         selectforeground=_c("fgt", "#000000"),
                         highlightthickness=0, bd=0)
    sb = tkinter.Scrollbar(mid, orient="vertical", command=lb.yview,
                           bg=_c("bd", "#E1E1E1"),
                           troughcolor=_c("bgc", "#FFFFFF"), relief="flat")
    lb.configure(yscrollcommand=sb.set)
    sb.pack(side="right", fill="y")
    lb.pack(side="left", fill="both", expand=True)

    # ── 提示行 ──
    tkinter.Label(win, text="↑/↓ 选择    Enter 插入当前行    Esc 取消",
                  font=F_SMALL, bg=_c("bgc", "#FFFFFF"),
                  fg=_c("fgm", "#5F5F5F")).pack(fill="x", padx=8, pady=(2, 6))

    rows = []          # 与 lb 行一一对应: {"kind": "header"/"item", "entry": {...}}

    def _select_first_item():
        for i, r in enumerate(rows):
            if r["kind"] == "item":
                lb.selection_clear(0, "end")
                lb.selection_set(i)
                lb.activate(i)
                lb.see(i)
                return

    def _render(*_a):
        res = filter_entries(var.get())
        lb.delete(0, "end")
        del rows[:]
        last = None
        for e in res:
            if e["group"] != last:
                rows.append({"kind": "header"})
                lb.insert("end", "  " + e["group"])
                try:
                    lb.itemconfig("end", foreground=_c("ac", "#0078D4"))
                except Exception:
                    pass
                last = e["group"]
            label = "    " + e["name"]
            if e.get("desc"):
                label += "   ·   " + e["desc"]
            rows.append({"kind": "item", "entry": e})
            lb.insert("end", label)
        if not res:
            rows.append({"kind": "header"})
            lb.insert("end", "  (无匹配命令)")
            try:
                lb.itemconfig("end", foreground=_c("fgm", "#5F5F5F"))
            except Exception:
                pass
        _select_first_item()

    def _move(delta):
        n = len(rows)
        if not n:
            return
        cur = lb.curselection()
        j = (cur[0] if cur else -1) + delta
        while 0 <= j < n and rows[j]["kind"] != "item":
            j += delta
        if 0 <= j < n:
            lb.selection_clear(0, "end")
            lb.selection_set(j)
            lb.activate(j)
            lb.see(j)

    def _close():
        try:
            win.destroy()
        except Exception:
            pass

    def _pick(*_a):
        cur = lb.curselection()
        if not cur:
            return "break"
        r = rows[cur[0]]
        if r["kind"] != "item":
            return "break"
        name = r["entry"]["name"]
        _close()
        try:
            on_pick(name)
        except Exception:
            pass
        return "break"

    def _on_key(event):
        k = getattr(event, "keysym", "")
        if k == "Down":
            _move(1)
            return "break"
        if k == "Up":
            _move(-1)
            return "break"
        if k in ("Return", "KP_Enter"):
            return _pick()
        if k == "Escape":
            _close()
            return "break"
        return None

    var.trace_add("write", _render)
    ent.bind("<Key>", _on_key)
    lb.bind("<Double-1>", _pick)
    lb.bind("<Return>", _pick)
    win.bind("<Escape>", lambda e: _close())
    win.protocol("WM_DELETE_WINDOW", _close)

    _render()
    try:
        ent.focus_force()
        ent.icursor("end")
    except Exception:
        pass
    try:
        win.lift()
    except Exception:
        pass
    return win
