# -*- coding: utf-8 -*-
"""ui.settings.cards.extensions — ⧉ 扩展卡 (路线图 阶段二新增项②)。

导出:
    build(parent, ctx) -> handles
    apply(ctx, handles) -> None       # 本卡操作即时生效 (无持久化 state), apply 为 no-op

内容 (对齐路线图 §12.3 表格「extensions 管理器 + 设置窗口新增『扩展』卡片」):
* 遍历 ``capabilities.all_states()`` (四态) 并补充 ``extensions.list_installed()``
  中未注册能力的扩展, 每项一行三列:
    · 状态列 —— 图标 ✔/○/⚠/⏸ + 能力名 + 状态文案;
    · 体积列 —— installed.json / manifest 的 size (人类可读);
    · 操作列 —— MISSING→「安装」 BROKEN→「修复」 DISABLED→「启用」
                READY→「禁用 / 卸载」。
* 操作回调 → ``extensions.*`` → ``capabilities.refresh`` → 重建状态行;
  失败用 ``ctx.show_toast(..., "error")`` 提示。
* 文案明示 §12.5「enable/disable 无法热插拔, 下次启动生效」。

本卡不 import ACRPA; 依赖经 ctx 注入。``extensions`` 为纯逻辑模块 (无 GUI 副作用)。
"""
import tkinter

import capabilities
import extensions

CARD_KEY = "extensions"
ROW = 11
ICON = "⧉"

_STATE_ICON = {
    capabilities.CapState.READY: "✔",
    capabilities.CapState.MISSING: "○",
    capabilities.CapState.BROKEN: "⚠",
    capabilities.CapState.DISABLED: "⏸",
}
_STATE_TEXT = {
    capabilities.CapState.READY: "已就绪",
    capabilities.CapState.MISSING: "未安装",
    capabilities.CapState.BROKEN: "不可用（需修复）",
    capabilities.CapState.DISABLED: "已禁用",
}


def _fmt_size(n):
    """字节 → 人类可读 (B/KB/MB/GB)。无效值返回 '-'。"""
    try:
        n = int(n)
    except (TypeError, ValueError):
        return "-"
    if n < 0:
        return "-"
    units = ("B", "KB", "MB", "GB", "TB")
    f = float(n)
    i = 0
    while f >= 1024.0 and i < len(units) - 1:
        f /= 1024.0
        i += 1
    if i == 0:
        return "{} {}".format(int(f), units[i])
    return "{:.1f} {}".format(f, units[i])


def _state_color(C, state):
    if state == capabilities.CapState.READY:
        return C.get("sc", C["fgm"])
    if state == capabilities.CapState.BROKEN:
        return C.get("err", C.get("dg", C["fgm"]))
    if state == capabilities.CapState.DISABLED:
        return C.get("wn", C["fgm"])
    return C["fgm"]


def build(parent, ctx):
    C = ctx.C
    card, content, title, _ = ctx.make_collapsible_card(parent, "扩展", ICON)
    card.grid(row=ROW, column=0, sticky="ew", **ctx.PAD)
    try:
        ctx.make_badge(CARD_KEY, title)
    except Exception:
        pass
    ctx.register_nav_card(CARD_KEY, card)

    tkinter.Label(
        content,
        text="管理可选依赖扩展：安装 / 卸载 / 启用 / 修复。"
             "启用与禁用无法热插拔，需下次启动应用生效。",
        font=ctx.FONT_SMALL, fg=C["fgm"], bg=C["bgc"],
        wraplength=520, justify="left").grid(
        row=0, column=0, sticky="w", padx=8, pady=(2, 4))

    rows_frame = tkinter.Frame(content, bg=C["bgc"])
    rows_frame.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 6))
    rows_frame.columnconfigure(0, weight=1)

    handles = {"card": card, "content": content, "title": title,
               "rows_frame": rows_frame}
    _render(rows_frame, ctx, handles)
    return handles


def apply(ctx, handles):
    """本卡操作即时生效, 无持久化 state → no-op (仍需导出以满足卡片契约)。"""
    return None


# ── 行渲染 ────────────────────────────────────────────────────────────

def _render(container, ctx, handles):
    """重建状态行 (清空后按当前 capabilities/installed 重新渲染)。"""
    C = ctx.C
    try:
        for w in container.winfo_children():
            w.destroy()
    except Exception:
        pass
    try:
        caps = capabilities.all_states()
    except Exception:
        caps = {}
    try:
        installed = extensions.list_installed()
    except Exception:
        installed = {}

    row = 0
    seen_ext = set()
    for cap_id in sorted(caps.keys()):
        st = caps[cap_id]
        cap = capabilities.get(cap_id)
        ext_id = getattr(cap, "ext_id", None) if cap else None
        if ext_id:
            seen_ext.add(ext_id)
        _render_row(container, ctx, handles, row, capabilities.label(cap_id),
                    st, ext_id, installed.get(ext_id) if ext_id else None)
        row += 1

    for ext_id in sorted(installed.keys()):
        if ext_id in seen_ext:
            continue
        _render_row(container, ctx, handles, row, "扩展 " + ext_id,
                    None, ext_id, installed.get(ext_id))
        row += 1

    if row == 0:
        tkinter.Label(container, text="（暂无可管理的扩展）", font=ctx.FONT_SMALL,
                      fg=C["fgm"], bg=C["bgc"]).grid(row=0, column=0, sticky="w")


def _render_row(container, ctx, handles, row, label, st, ext_id, rec):
    C = ctx.C
    frame = tkinter.Frame(container, bg=C["bgc"])
    frame.grid(row=row, column=0, sticky="ew", pady=1)
    frame.columnconfigure(0, weight=1)

    # 能力状态为 None (扩展未注册能力) 时按 installed.enabled 推断
    if st is None:
        st = (capabilities.CapState.READY if rec and rec.get("enabled", True)
              else capabilities.CapState.DISABLED)

    icon = _STATE_ICON.get(st, "•")
    text = _STATE_TEXT.get(st, str(getattr(st, "value", st)))

    tkinter.Label(frame, text="{} {}  ·  {}".format(icon, label, text),
                  font=ctx.FONT_SMALL, fg=_state_color(C, st), bg=C["bgc"],
                  anchor="w").grid(row=0, column=0, sticky="w")

    size = _fmt_size((rec or {}).get("size") if ext_id else None)
    tkinter.Label(frame, text=size, font=ctx.FONT_SMALL, fg=C["fgm"],
                  bg=C["bgc"], width=9, anchor="e").grid(
        row=0, column=1, sticky="e", padx=(6, 6))

    actions = tkinter.Frame(frame, bg=C["bgc"])
    actions.grid(row=0, column=2, sticky="e")
    _render_actions(actions, ctx, handles, st, ext_id, rec)


def _render_actions(parent, ctx, handles, st, ext_id, rec):
    C = ctx.C
    if not ext_id:
        tkinter.Label(parent, text="主包内置", font=ctx.FONT_SMALL, fg=C["fgm"],
                      bg=C["bgc"]).pack(side="right")
        return

    installed = bool(rec)
    if st == capabilities.CapState.MISSING:
        ctx.btn(parent, "安装", lambda _e=None: _do_install(ctx, handles, ext_id),
                tip="选择本地扩展包文件 (.zip) 安装").pack(side="right")
    elif st == capabilities.CapState.BROKEN:
        ctx.btn(parent, "修复", lambda _e=None: _do_repair(ctx, handles, ext_id),
                tip="重跑解包与自检, 失败则回滚并隔离").pack(side="right")
    elif st == capabilities.CapState.DISABLED:
        ctx.btn(parent, "启用", lambda _e=None: _do_enable(ctx, handles, ext_id),
                "sc", "white", tip="下次启动生效").pack(side="right")
    else:  # READY
        if installed:
            ctx.btn(parent, "卸载", lambda _e=None: _do_uninstall(ctx, handles, ext_id),
                    "dg", "white", tip="删除扩展目录并更新登记").pack(side="right")
            ctx.btn(parent, "禁用", lambda _e=None: _do_disable(ctx, handles, ext_id),
                    tip="下次启动生效").pack(side="right", padx=(0, 4))
        else:
            tkinter.Label(parent, text="已就绪", font=ctx.FONT_SMALL, fg=C["fgm"],
                          bg=C["bgc"]).pack(side="right")


# ── 操作回调 (统一: 执行 → toast → refresh → 重建行) ──────────────────

def _run(ctx, handles, fn):
    try:
        ok, msg = fn()
    except Exception as e:                                  # noqa: BLE001
        ok, msg = False, "{}: {}".format(type(e).__name__, e)
    try:
        ctx.show_toast(ctx.root, msg, "info" if ok else "error")
    except Exception:                                       # noqa: BLE001
        pass
    try:
        capabilities.refresh()
    except Exception:                                       # noqa: BLE001
        pass
    _render(handles["rows_frame"], ctx, handles)


def _do_install(ctx, handles, ext_id):
    path = ""
    try:
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            parent=ctx.win, title="选择扩展包 ({})".format(ext_id),
            filetypes=[("扩展包", "*.zip *.acrpaext"), ("所有文件", "*.*")])
    except Exception:                                       # noqa: BLE001
        path = ""
    if not path:
        return
    _run(ctx, handles,
         lambda: extensions.install(path, allow_unsigned=True))


def _do_repair(ctx, handles, ext_id):
    _run(ctx, handles, lambda: extensions.repair(ext_id))


def _do_enable(ctx, handles, ext_id):
    _run(ctx, handles, lambda: extensions.enable(ext_id))


def _do_disable(ctx, handles, ext_id):
    _run(ctx, handles, lambda: extensions.disable(ext_id))


def _do_uninstall(ctx, handles, ext_id):
    _run(ctx, handles, lambda: extensions.uninstall(ext_id))
