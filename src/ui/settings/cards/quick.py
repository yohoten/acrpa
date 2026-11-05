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
from ui.settings.cards._layout import help_line, section

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

    card, quick_content, quick_title, _ = _make_collapsible_card(parent, "关于", "↯")
    card.grid(row=ROW, column=0, sticky="ew", **ctx.PAD)
    _register_nav_card(CARD_KEY, card)
    quick_content.columnconfigure(0, weight=1)

    # ── 关于 ACRPA (P2-7) — 版本号从 VERSION 文件统一获取 ──
    from version_info import get_version, get_manifest_path
    about_frame = tkinter.Frame(quick_content, bg=C["bgc"])
    about_frame.grid(row=0, column=0, sticky="ew", pady=(0, ctx.sp("sp_sm")))

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

    _btn(about_head, "检查更新", _open_update,
         tip="检查 GitHub 上的最新版本并直接下载 (支持自动重启更新)"
         ).pack(side="right")

    tkinter.Label(about_frame, text="自动化工作流工具 — 脚本编辑 | 执行控制 | 动作录制 | 模板共创",
        font=FONT_TINY, fg=C["fgm"], bg=C["bgc"]).pack(anchor="w")
    tkinter.Label(about_frame, text="仅供学习研究使用，使用者自行承担风险",
        font=FONT_TINY, fg=C["dg"], bg=C["bgc"]).pack(anchor="w")
    tkinter.Label(about_frame, text="版本来源: {}".format(get_manifest_path() or "(内置回退值)"),
        font=FONT_TINY, fg=C["fgm"], bg=C["bgc"],
        wraplength=ctx.sp("help_wrap"), justify="left").pack(anchor="w")

    # ── 分级重置 (P2-9) ──
    def _reset_defaults(all_settings=False):
        confirm_msg = ("确定要恢复所有设置为默认值吗？\n"
                       "此操作会影响全部配置，但不会清空凭据库中的密钥。") if all_settings else \
            "仅恢复“启动时检查更新”为默认值，确定继续吗？"
        if not messagebox.askyesno("确认", confirm_msg):
            return
        try:
            if all_settings:
                defaults = {k: d for k, d, _ in state._config_schema}
                for key, val in defaults.items():
                    setattr(state, key.upper(), val)
                state.save_config()
                _close(force=True)
                try:
                    root.after(200, _reopen)
                except Exception:
                    pass
            elif not ctx.reset_current_page("about"):
                raise RuntimeError("本页设置保存失败，请查看底部状态栏")
        except Exception as exc:
            messagebox.showerror("恢复默认失败", str(exc), parent=ctx.win)
            return
        _log1("已恢复全部默认设置" if all_settings else "已恢复关于页默认设置")
        _show_toast(root, "已恢复默认设置并应用", "success")

    # ── 分节: 维护 (安全操作与危险操作必须视觉分区, 避免误点) ──
    section(quick_content, ctx, "维护").grid(
        row=1, column=0, sticky="ew", pady=(ctx.sp("section_gap"), 0))

    page_row = tkinter.Frame(quick_content, bg=C["bgc"])
    page_row.grid(row=2, column=0, sticky="ew", pady=(ctx.sp("sp_sm"), 0))
    _btn(page_row, "恢复本页默认", lambda: _reset_defaults(all_settings=False),
         C["wn"], "white", tip="恢复当前页设置为默认值").pack(side="left")
    tkinter.Label(page_row, text="仅恢复本页声明的设置项，不影响其他页面",
                  font=FONT_TINY, fg=C["fgm"], bg=C["bgc"]).pack(
        side="left", padx=(ctx.sp("sp_sm"), 0))

    help_line(quick_content, ctx,
              "恢复全部默认会重置所有配置；凭据库中的密钥不会被清除，设置窗口将重新打开。",
              level="warn").grid(row=3, column=0, sticky="ew",
                                 pady=(ctx.sp("sp_md"), ctx.sp("gap_tight")))
    _btn(quick_content, "恢复全部默认", lambda: _reset_defaults(all_settings=True),
         C["dg"], "white", tip="恢复所有设置为默认值").grid(row=4, column=0, sticky="w")

    return {"card": card, "content": quick_content, "title": quick_title}


def apply(ctx, handles):
    """快速操作卡无即时保存项 (no-op)。"""
    return None
