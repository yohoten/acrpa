# -*- coding: utf-8 -*-
"""ui.settings.cards.market — ⛁ 脚本市场 账号卡 (原 settings_window 2623-2803 行段)。

账号状态 / 登录 / 注销 / 市场选项。
导出:
    build(parent, ctx) -> handles
    apply(ctx, handles) -> None       # 等价原 _apply_market_settings
"""
import threading
import tkinter
from tkinter import ttk, messagebox

import state
from utils import FONT_TINY

CARD_KEY = "market"
ROW = 10


def build(parent, ctx):
    C = ctx.C
    _btn = ctx.btn
    _log1 = ctx.log1
    _show_toast = ctx.show_toast
    _set_window_icon = ctx.set_window_icon
    root = ctx.root
    _win = ctx.win
    _validate_number = ctx.validate_number
    _make_collapsible_card = ctx.make_collapsible_card
    _make_badge = ctx.make_badge
    _register_nav_card = ctx.register_nav_card
    _track_card_vars = ctx.track_card_vars

    card, mk_content, mk_title, _ = _make_collapsible_card(parent, "脚本市场", "⛁")
    card.grid(row=ROW, column=0, sticky="ew", **ctx.PAD)
    _make_badge(CARD_KEY, mk_title)
    _register_nav_card(CARD_KEY, card)

    import accounts as _accounts

    _mk_status = {}

    def _mk_refresh():
        """无网络刷新两 provider 登录状态 (仅凭据库探测)；同时刷新当前账户。"""
        for _prov in ("gitee", "github"):
            _lbl = _mk_status.get(_prov)
            if _lbl is None:
                continue
            try:
                _has = _accounts.has_token(_prov)
            except Exception:
                _has = False
            try:
                if _has:
                    _lbl.configure(text="● 已登录", fg=C["sc"])
                else:
                    _lbl.configure(text="○ 未登录", fg=C["fgm"])
            except Exception:
                pass
        try:
            _name = getattr(state, "MARKET_USERNAME", "") or ""
            _p = (getattr(state, "MARKET_PROVIDER", "") or "").lower()
            if _name:
                mk_cur_lbl.configure(text="当前账户: {} ({})".format(_name, _p or "-"))
            else:
                _logged = _accounts.list_logged_in()
                mk_cur_lbl.configure(text="当前账户: {}".format(
                    _logged[0] if _logged else "(未登录)"))
        except Exception:
            pass

    def _mk_open_token(prov):
        """内联 PAT 输入弹窗；Token 仅经 accounts.login → 凭据库，绝不明文落盘。"""
        dlg = tkinter.Toplevel(_win)
        dlg.title("登录 {}".format(prov))
        dlg.geometry("440x190+520+320")
        dlg.transient(_win)
        dlg.configure(bg=C["bgc"])
        try:
            dlg.grab_set()
        except Exception:
            pass
        _set_window_icon(dlg)
        tkinter.Label(dlg, text="Personal Access Token ({})".format(prov),
            font=ctx.FONT_BODY, bg=C["bgc"], fg=C["fgt"]).pack(pady=(14, 4))
        tkinter.Label(dlg, text="仅存本机 Windows 凭据库，不上传、不写入配置文件。",
            font=FONT_TINY, bg=C["bgc"], fg=C["fgm"]).pack()
        var = tkinter.StringVar()
        ent = tkinter.Entry(dlg, textvariable=var, show="*", width=46,
            font=ctx.FONT_BODY, relief="solid", bd=1, bg=C["ebg"], fg=C["fgb"])
        ent.pack(pady=8)
        ent.focus_set()

        def _submit():
            token = var.get().strip()
            if not token:
                messagebox.showwarning("提示", "请输入 Token", parent=dlg)
                return
            t = token
            dlg.destroy()
            _mk_login(prov, t)

        btns = tkinter.Frame(dlg, bg=C["bgc"])
        btns.pack(pady=(0, 12))
        _btn(btns, "确定", _submit).pack(side="left", padx=(0, 6))
        _btn(btns, "取消", dlg.destroy).pack(side="left")
        ent.bind("<Return>", lambda e: _submit())

    def _mk_login(prov, token):
        """后台线程校验并保存 token；回主线程刷新状态。"""
        def work():
            try:
                info = _accounts.login(prov, token)
            except Exception as e:
                err = str(e)

                def _err():
                    try:
                        messagebox.showerror("登录失败", err, parent=_win)
                    except Exception:
                        pass
                    _mk_refresh()
                try:
                    _win.after(0, _err)
                except Exception:
                    pass
                return

            def _ok():
                try:
                    state.MARKET_PROVIDER = prov
                    state.MARKET_USERNAME = info.get("login", "") or info.get("name", "")
                    state.save_config()
                except Exception:
                    pass
                try:
                    _show_toast(root, "已登录 {}: {}".format(
                        prov, info.get("login", "")), "success")
                except Exception:
                    pass
                _mk_refresh()
            try:
                _win.after(0, _ok)
            except Exception:
                pass

        threading.Thread(target=work, daemon=True).start()

    def _mk_logout(prov):
        try:
            _accounts.logout(prov)
        except Exception as e:
            _log1("注销失败: {}".format(e), "warning")
        _mk_refresh()
        try:
            _show_toast(root, "已注销 {}".format(prov), "info")
        except Exception:
            pass

    mk_rows = tkinter.Frame(mk_content, bg=C["bgc"])
    mk_rows.grid(row=0, column=0, sticky="ew", padx=8, pady=(2, 1))
    mk_rows.columnconfigure(1, weight=1)
    for _i, (_prov, _label) in enumerate((("gitee", "Gitee"), ("github", "GitHub"))):
        tkinter.Label(mk_rows, text=_label, font=ctx.FONT_BODY, bg=C["bgc"],
            fg=C["fgb"], width=8, anchor="w").grid(row=_i, column=0, sticky="w", pady=1)
        _st = tkinter.Label(mk_rows, text="", font=ctx.FONT_SMALL, bg=C["bgc"], fg=C["fgm"])
        _st.grid(row=_i, column=1, sticky="w")
        _mk_status[_prov] = _st
        _btns = tkinter.Frame(mk_rows, bg=C["bgc"])
        _btns.grid(row=_i, column=2, sticky="e")
        _btn(_btns, "登录", lambda p=_prov: _mk_open_token(p),
             C["sc"], "white", tip="输入 PAT 登录（仅存本机凭据库）").pack(
            side="left", padx=(0, 4))
        _btn(_btns, "注销", lambda p=_prov: _mk_logout(p),
             C["dg"], "white", tip="清除本机保存的 token").pack(side="left")

    mk_cur_lbl = tkinter.Label(mk_content, text="", font=ctx.FONT_SMALL,
        bg=C["bgc"], fg=C["ac"])
    mk_cur_lbl.grid(row=1, column=0, sticky="w", padx=8, pady=(2, 2))

    mk_opt = tkinter.Frame(mk_content, bg=C["bgc"])
    mk_opt.grid(row=2, column=0, sticky="ew", padx=8, pady=(1, 1))
    market_auto_check_var = tkinter.BooleanVar(
        value=bool(getattr(state, "MARKET_AUTO_CHECK_UPDATE", True)))
    ttk.Checkbutton(mk_opt, text="打开市场时后台查更新",
        variable=market_auto_check_var).pack(side="left", padx=(0, 12))
    tkinter.Label(mk_opt, text="索引缓存(秒)", font=ctx.FONT_SMALL,
        fg=C["fgm"], bg=C["bgc"]).pack(side="left", padx=(0, 4))
    market_ttl_var = tkinter.StringVar(
        value=str(getattr(state, "MARKET_INDEX_CACHE_TTL", 3600)))
    _validate_number(tkinter.Entry(mk_opt, textvariable=market_ttl_var, width=7,
        font=ctx.FONT_BODY, relief="solid", bd=1, bg=C["ebg"], fg=C["fgb"]),
        market_ttl_var, "索引缓存秒数", 3600, int, 1, 86400,
        card_key="market").pack(side="left")

    tkinter.Label(mk_content,
        text="Token 仅存 Windows 凭据库；登录/上传均不写入 config.json。",
        font=FONT_TINY, fg=C["fgm"], bg=C["bgc"]).grid(
        row=3, column=0, sticky="w", padx=8, pady=(0, 6))

    _track_card_vars("market", (market_auto_check_var, market_ttl_var))
    _mk_refresh()

    return {
        "card": card, "content": mk_content, "title": mk_title,
        "market_auto_check_var": market_auto_check_var,
        "market_ttl_var": market_ttl_var,
    }


def apply(ctx, handles):
    """保存「脚本市场」卡非敏感配置 (账号 token 走凭据库，不在此)。"""
    state.MARKET_AUTO_CHECK_UPDATE = bool(handles["market_auto_check_var"].get())
    try:
        v = int(handles["market_ttl_var"].get())
        v = 1 if v < 1 else (86400 if v > 86400 else v)
        state.MARKET_INDEX_CACHE_TTL = v
    except (ValueError, TypeError):
        state.MARKET_INDEX_CACHE_TTL = 3600
        handles["market_ttl_var"].set("3600")
