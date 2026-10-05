# -*- coding: utf-8 -*-
"""ui.settings.cards.quick — ↯ 快速操作卡 (原 settings_window 1788-1843 行段)。

关于 ACRPA 版本 + 检查更新 + 分级重置默认。本卡**无 badge、无 apply** (无即时保存项)。
导出:
    build(parent, ctx) -> handles
    apply(ctx, handles) -> None       # no-op (占位, 保持 11 卡统一签名)
"""
import tkinter
from tkinter import messagebox

import state
from utils import FONT_TINY

CARD_KEY = "quick"
ROW = 6


def build(parent, ctx):
    C = ctx.C
    _btn = ctx.btn
    _log1 = ctx.log1
    _show_toast = ctx.show_toast
    _close = ctx.close_window
    _reopen = ctx.reopen_window
    root = ctx.root
    _make_collapsible_card = ctx.make_collapsible_card
    _register_nav_card = ctx.register_nav_card

    card, quick_content, quick_title, _ = _make_collapsible_card(parent, "快速操作", "↯")
    card.grid(row=ROW, column=0, sticky="ew", **ctx.PAD)
    _register_nav_card(CARD_KEY, card)
    quick_content.columnconfigure(0, weight=1)

    # ── 关于 ACRPA (P2-7) — 版本号从 VERSION 文件统一获取 ──
    from version_info import get_version, get_manifest_path
    about_frame = tkinter.Frame(quick_content, bg=C["bgc"])
    about_frame.grid(row=0, column=0, columnspan=2, sticky="ew", padx=8, pady=(4, 4))

    about_head = tkinter.Frame(about_frame, bg=C["bgc"])
    about_head.pack(fill="x")
    tkinter.Label(about_head, text="ACRPA v{}".format(get_version()), font=ctx.FONT_TITLE,
        fg=C["ac"], bg=C["bgc"]).pack(side="left")

    def _open_update():
        try:
            import dialogs
            dialogs.show_update_dialog(force_check=True)
        except Exception as e:
            messagebox.showerror("检查更新", "无法打开更新窗口:\n{}".format(e))

    _btn(about_head, "⇪ 检查更新", _open_update,
         tip="检查 GitHub 上的最新版本并直接下载 (支持自动重启更新)"
         ).pack(side="right")

    tkinter.Label(about_frame, text="自动化工作流工具 — 脚本编辑 | 执行控制 | 动作录制 | 模板共创",
        font=FONT_TINY, fg=C["fgm"], bg=C["bgc"]).pack(anchor="w")
    tkinter.Label(about_frame, text="仅供学习研究使用，使用者自行承担风险",
        font=FONT_TINY, fg=C["dg"], bg=C["bgc"]).pack(anchor="w")
    tkinter.Label(about_frame, text="版本来源: {}".format(get_manifest_path() or "(内置回退值)"),
        font=FONT_TINY, fg=C["fgm"], bg=C["bgc"],
        wraplength=520, justify="left").pack(anchor="w")

    # ── 分级重置 (P2-9) ──
    def _reset_defaults(all_settings=False):
        confirm_msg = "确定要恢复所有设置为默认值吗？\n此操作不可撤销。" if all_settings else \
            "确定要恢复本页设置为默认值吗？"
        if messagebox.askyesno("确认", confirm_msg):
            defaults = {k: d for k, d, _ in state._config_schema}
            for key, val in defaults.items():
                setattr(state, key.upper(), val)
            state.save_config()
            _log1("已恢复默认设置")
            _close()
            root.after(200, _reopen)
            _show_toast(root, "已恢复默认设置并应用", "success")

    reset_btn_frame = tkinter.Frame(quick_content, bg=C["bgc"])
    reset_btn_frame.grid(row=1, column=0, columnspan=2, sticky="ew", padx=4, pady=(2, 2))
    _btn(reset_btn_frame, "恢复全部默认", lambda: _reset_defaults(all_settings=True),
        C["dg"], "white", tip="恢复所有设置为默认值").pack(side="left", padx=(0, 4))
    _btn(reset_btn_frame, "恢复本页默认", lambda: _reset_defaults(all_settings=False),
        C["wn"], "white", tip="恢复当前页设置为默认值").pack(side="left")

    return {"card": card, "content": quick_content, "title": quick_title}


def apply(ctx, handles):
    """快速操作卡无即时保存项 (no-op)。"""
    return None
