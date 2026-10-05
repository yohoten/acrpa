# -*- coding: utf-8 -*-
"""ui.settings.cards.ai — ✦ AI 增强卡 (原 settings_window 891-1236 行段)。

导出:
    build(parent, ctx) -> handles
    apply(ctx, handles) -> None       # 等价原 _apply_ai_settings
"""
import tkinter
from tkinter import ttk, messagebox

import state
from utils import FONT_TINY, FONT_ICON

CARD_KEY = "ai"
ROW = 1


def build(parent, ctx):
    C = ctx.C
    _darken = ctx.darken
    _attach_tooltip = ctx.attach_tooltip
    _log1 = ctx.log1
    _show_toast = ctx.show_toast
    _ui_theme = ctx.ui_theme

    _make_collapsible_card = ctx.make_collapsible_card
    _make_badge = ctx.make_badge
    _register_nav_card = ctx.register_nav_card
    _set_window_icon = ctx.set_window_icon
    _track_card_vars = ctx.track_card_vars
    _win = ctx.win
    root = ctx.root

    card, content, title, _ = _make_collapsible_card(parent, "AI 增强", "✦")
    card.grid(row=ROW, column=0, sticky="ew", **ctx.PAD)
    _make_badge(CARD_KEY, title)
    _register_nav_card(CARD_KEY, card)

    ai_frame = tkinter.Frame(content, bg=C["bgc"])
    ai_frame.grid(row=0, column=0, sticky="ew", padx=8, pady=(4, 2))

    tkinter.Label(ai_frame, text="AI增强:", font=ctx.FONT_BODY,
        fg=C["fgb"], bg=C["bgc"]).pack(side="left", padx=(0, 6))

    ai_smart_retry_var = tkinter.BooleanVar(value=state.AI_SMART_RETRY)
    ai_smart_cb = ttk.Checkbutton(ai_frame, text="智能重试 (失败时AI分析调整)",
        variable=ai_smart_retry_var)
    ai_smart_cb.pack(side="left", padx=(0, 8))

    # AI 智能重试说明
    ai_desc_frame = tkinter.Frame(content, bg=C["bgc"])
    ai_desc_frame.grid(row=2, column=0, sticky="ew", padx=8, pady=(0, 2))
    tkinter.Label(ai_desc_frame, text="失败时 AI 分析截图并给出修正建议（需配置 API Key）", font=FONT_TINY,
        fg=C["fgm"], bg=C["bgc"]).pack(anchor="w")

    ai_anomaly_var = tkinter.BooleanVar(value=state.AI_ANOMALY_DETECT)
    ai_anomaly_cb = ttk.Checkbutton(ai_frame, text="异常检测 (AI监控执行)",
        variable=ai_anomaly_var)
    ai_anomaly_cb.pack(side="left", padx=(0, 8))

    def _show_ai_info(event):
        info = (
            "AI 增强功能说明:\n\n"
            "智能重试: 命令失败时AI自动分析截图，给出修正建议并重试\n"
            "  - 仅在使用图像识别类命令失败时生效\n"
            "  - 需要配置 API Key\n\n"
            "异常检测: AI实时监控执行过程，检测异常模式并告警\n"
            "  - 每10条命令自动检测一次\n"
            "  - 不需要手动干预，后台静默运行\n\n"
            "[AI] 调试按钮在「执行控制」页面底部"
        )
        messagebox.showinfo("AI 增强功能", info)

    ai_info_btn = tkinter.Label(ai_frame, text="ⓘ", font=FONT_ICON,
        fg=C["ac"], bg=C["bgc"], cursor="hand2")
    ai_info_btn.pack(side="left")
    ai_info_btn.bind("<Button-1>", _show_ai_info)

    # === API config row (自定义 AI 提供商 / 模型 / BaseURL / Key  PR-4) ──
    api_frame = tkinter.Frame(content, bg=C["bgc"])
    api_frame.grid(row=1, column=0, sticky="ew", padx=8, pady=(2,6))
    api_frame.columnconfigure(0, weight=0); api_frame.columnconfigure(1, weight=1)

    try:
        import ai_client as _aic
    except Exception:
        _aic = None

    _prov_by_id = {}
    _label_to_id = {}
    _prov_labels = []
    for _p in (_aic.list_providers() if _aic is not None else []):
        _prov_by_id[_p["id"]] = _p
        _label_to_id[_p["label"]] = _p["id"]
        _prov_labels.append(_p["label"])

    _init_pid = (getattr(state, "AI_PROVIDER", "") or "").strip()
    if _init_pid not in _prov_by_id and _aic is not None:
        try:
            _init_pid = _aic.resolve_provider()
        except Exception:
            _init_pid = ""
    if _init_pid not in _prov_by_id and _prov_labels:
        _init_pid = _label_to_id[_prov_labels[0]]
    _init_p = _prov_by_id.get(_init_pid, {})

    # 提供商
    tkinter.Label(api_frame, text="提供商", font=ctx.FONT_BODY,
        fg=C["fgb"], bg=C["bgc"]).grid(row=0, column=0, sticky="w", padx=(0,6), pady=2)
    _prov_var = tkinter.StringVar(
        value=_init_p.get("label", _prov_labels[0] if _prov_labels else ""))
    _prov_combo = ttk.Combobox(api_frame, textvariable=_prov_var, width=26,
        values=_prov_labels, state="readonly")
    _prov_combo.grid(row=0, column=1, sticky="ew", pady=2)

    # 模型（可编辑）
    tkinter.Label(api_frame, text="模型", font=ctx.FONT_BODY,
        fg=C["fgb"], bg=C["bgc"]).grid(row=1, column=0, sticky="w", padx=(0,6), pady=2)
    try:
        _init_model = _aic.resolve_model() if _aic is not None else state.API_MODEL
    except Exception:
        _init_model = state.API_MODEL
    api_model_var = tkinter.StringVar(value=_init_model)
    api_model_entry = tkinter.Entry(api_frame, textvariable=api_model_var, width=28,
        font=ctx.FONT_SMALL, relief="solid", bd=1, bg=C["ebg"], fg=C["fgb"])
    api_model_entry.grid(row=1, column=1, sticky="ew", pady=2)

    # BaseURL
    tkinter.Label(api_frame, text="BaseURL", font=ctx.FONT_BODY,
        fg=C["fgb"], bg=C["bgc"]).grid(row=2, column=0, sticky="w", padx=(0,6), pady=2)
    _init_base = (getattr(state, "AI_BASE_URL", "") or "").strip() or _init_p.get("base_url", "")
    api_base_var = tkinter.StringVar(value=_init_base)
    api_base_entry = tkinter.Entry(api_frame, textvariable=api_base_var, width=28,
        font=ctx.FONT_SMALL, relief="solid", bd=1, bg=C["ebg"], fg=C["fgb"])
    api_base_entry.grid(row=2, column=1, sticky="ew", pady=2)

    # API Key（掩码显示）
    tkinter.Label(api_frame, text="API Key", font=ctx.FONT_BODY,
        fg=C["fgb"], bg=C["bgc"]).grid(row=3, column=0, sticky="w", padx=(0,6), pady=2)
    try:
        _init_key = (_aic.get_provider_key(_init_pid) if _aic is not None else None) \
            or getattr(state, "API_KEY", "")
    except Exception:
        _init_key = getattr(state, "API_KEY", "")
    api_key_var = tkinter.StringVar(value=_init_key)
    api_key_entry = tkinter.Entry(api_frame, textvariable=api_key_var, width=22,
        font=ctx.FONT_SMALL, show="*", relief="solid", bd=1, bg=C["ebg"], fg=C["fgb"])
    api_key_entry.grid(row=3, column=1, sticky="ew", pady=2)

    # 显示/隐藏切换 (眼睛图标)
    def _toggle_api_key_visibility():
        if api_key_entry.cget("show") == "*":
            api_key_entry.config(show="")
            api_eye_btn.config(text="◉")
        else:
            api_key_entry.config(show="*")
            api_eye_btn.config(text="○")
    api_eye_btn = tkinter.Label(api_frame, text="○", font=FONT_ICON,
        bg=C["bgc"], fg=C["fgm"], cursor="hand2")
    api_eye_btn.grid(row=3, column=2, padx=(4, 0))
    api_eye_btn.bind("<Button-1>", lambda e: _toggle_api_key_visibility())
    _attach_tooltip(api_eye_btn, "显示/隐藏 API Key")

    # 测试连接 + 结果标签
    ai_test_result = tkinter.Label(api_frame, text="", font=ctx.FONT_SMALL,
        fg=C["fgm"], bg=C["bgc"], anchor="w")

    def _on_test_connection():
        try:
            from utils import themed
            ok_color, bad_color = themed("sc"), themed("dg")
        except Exception:
            ok_color, bad_color = C["sc"], C["dg"]
        pid = _label_to_id.get(_prov_var.get(), "")
        ai_test_result.config(text="测试中…（最长 8 秒）", fg=C["fgm"])
        try:
            ai_test_result.update_idletasks()
        except Exception:
            pass
        if _aic is None:
            ai_test_result.config(text="AI 客户端不可用", fg=bad_color)
            return
        try:
            ok, msg = _aic.test_connection(
                provider=pid,
                base_url=api_base_var.get().strip(),
                model=api_model_var.get().strip(),
                api_key=api_key_var.get().strip(),
                timeout=8)
        except Exception as e:
            ok, msg = False, "测试失败：{}".format(e)
        ai_test_result.config(text=msg, fg=(ok_color if ok else bad_color))
        try:
            _log1("[AI 测试连接] {}: {}".format("成功" if ok else "失败", msg))
        except Exception:
            pass

    ai_test_btn = tkinter.Button(api_frame, text="测试连接", font=ctx.FONT_SMALL,
        bg=C["bgc"], fg=C["fgb"], relief="raised", bd=1, padx=8, pady=1,
        cursor="hand2", activebackground=_darken(C["bgc"]), activeforeground=C["fgb"],
        highlightbackground=C["bd"], highlightthickness=1, command=_on_test_connection)
    ai_test_btn.grid(row=4, column=0, sticky="w", pady=(2, 0))
    ai_test_result.grid(row=4, column=1, columnspan=2, sticky="ew", pady=(2, 0))

    # 添加自定义提供商（自绘 Toplevel）
    def _open_custom_provider_dialog():
        dlg = tkinter.Toplevel(_win)
        dlg.title("自定义 AI 提供商")
        dlg.configure(bg=C["bgc"])
        try:
            dlg.transient(_win)
            dlg.grab_set()
        except Exception:
            pass
        _set_window_icon(dlg)

        frm = tkinter.Frame(dlg, bg=C["bgc"])
        frm.pack(fill="both", expand=True, padx=12, pady=10)
        frm.columnconfigure(1, weight=1)

        v_id = tkinter.StringVar()
        v_name = tkinter.StringVar()
        v_url = tkinter.StringVar()
        v_models = tkinter.StringVar()
        for _i, (_lbl, _var) in enumerate((
                ("提供商 ID", v_id), ("显示名", v_name),
                ("BaseURL", v_url), ("模型列表(逗号分隔)", v_models))):
            tkinter.Label(frm, text=_lbl, font=ctx.FONT_BODY, fg=C["fgb"],
                bg=C["bgc"]).grid(row=_i, column=0, sticky="w", padx=(0, 6), pady=3)
            tkinter.Entry(frm, textvariable=_var, font=ctx.FONT_SMALL, width=34,
                relief="solid", bd=1, bg=C["ebg"], fg=C["fgb"]).grid(
                row=_i, column=1, sticky="ew", pady=3)

        tkinter.Label(frm, text="已添加的自定义提供商：", font=ctx.FONT_BODY,
            fg=C["fgb"], bg=C["bgc"]).grid(row=4, column=0, columnspan=2,
            sticky="w", pady=(8, 2))
        list_frame = tkinter.Frame(frm, bg=C["bgc"])
        list_frame.grid(row=5, column=0, columnspan=2, sticky="ew")
        list_frame.columnconfigure(0, weight=1)

        def _refresh_options():
            """重建主窗口提供商下拉选项（含删/加自定义后同步）。"""
            del _prov_labels[:]
            _prov_by_id.clear()
            _label_to_id.clear()
            for _p in (_aic.list_providers() if _aic is not None else []):
                _prov_by_id[_p["id"]] = _p
                _label_to_id[_p["label"]] = _p["id"]
                _prov_labels.append(_p["label"])
            try:
                _prov_combo.config(values=_prov_labels)
            except Exception:
                pass

        def _select_provider(pid):
            p = _prov_by_id.get(pid)
            if not p:
                return
            _prov_var.set(p["label"])
            try:
                _on_provider_change()
            except Exception:
                pass

        def _refresh_list():
            for w in list_frame.winfo_children():
                w.destroy()
            rows = [c for c in _prov_by_id.values() if c.get("custom")]
            if not rows:
                tkinter.Label(list_frame, text="（暂无）", font=ctx.FONT_SMALL,
                    fg=C["fgm"], bg=C["bgc"]).grid(row=0, column=0, sticky="w")
                return
            for _i, c in enumerate(rows):
                tkinter.Label(list_frame, text="{}（{}）".format(c["id"], c["label"]),
                    font=ctx.FONT_SMALL, fg=C["fgb"], bg=C["bgc"]).grid(
                    row=_i, column=0, sticky="w", pady=1)
                tkinter.Button(list_frame, text="删除", font=ctx.FONT_SMALL,
                    bg=C["bgc"], fg=C["fgb"], relief="raised", bd=1, padx=6, pady=0,
                    cursor="hand2", command=lambda _pid=c["id"]: _remove(_pid)).grid(
                    row=_i, column=1, sticky="e", padx=(6, 0), pady=1)

        def _remove(pid):
            try:
                state.AI_CUSTOM_PROVIDERS = [
                    c for c in (getattr(state, "AI_CUSTOM_PROVIDERS", None) or [])
                    if c.get("id") != pid]
                state.save_config()
            except Exception as e:
                _log1("删除自定义提供商失败: {}".format(e), "error")
            _refresh_options()
            _refresh_list()

        def _do_add():
            cid = v_id.get().strip()
            name = v_name.get().strip() or cid
            url = v_url.get().strip()
            models = [m.strip() for m in v_models.get().split(",") if m.strip()]
            if not cid or not url:
                messagebox.showwarning("提示", "提供商 ID 与 BaseURL 为必填", parent=dlg)
                return
            try:
                lst = [c for c in (getattr(state, "AI_CUSTOM_PROVIDERS", None) or [])
                       if c.get("id") != cid]
                lst.append({"id": cid, "name": name, "base_url": url, "models": models})
                state.AI_CUSTOM_PROVIDERS = lst
                state.save_config()
            except Exception as e:
                _log1("添加自定义提供商失败: {}".format(e), "error")
                return
            _refresh_options()
            _refresh_list()
            _select_provider(cid)

        btn_row = tkinter.Frame(frm, bg=C["bgc"])
        btn_row.grid(row=6, column=0, columnspan=2, sticky="e", pady=(10, 0))
        _ui_theme.roled(tkinter.Button(btn_row, text="添加/更新", font=ctx.FONT_SMALL, bg=C["sc"],
            fg="white", relief="raised", bd=1, padx=12, pady=3, cursor="hand2",
            activebackground=_darken(C["sc"]), activeforeground="white",
            command=_do_add), "sc").pack(side="left", padx=(0, 6))
        tkinter.Button(btn_row, text="关闭", font=ctx.FONT_SMALL, bg=C["bgc"],
            fg=C["fgb"], relief="raised", bd=1, padx=12, pady=3, cursor="hand2",
            activebackground=_darken(C["bgc"]), activeforeground=C["fgb"],
            highlightbackground=C["bd"], highlightthickness=1,
            command=dlg.destroy).pack(side="left")

        _refresh_options()
        _refresh_list()

    ai_custom_btn = tkinter.Button(api_frame, text="添加自定义提供商", font=ctx.FONT_SMALL,
        bg=C["bgc"], fg=C["fgb"], relief="raised", bd=1, padx=8, pady=1,
        cursor="hand2", activebackground=_darken(C["bgc"]), activeforeground=C["fgb"],
        highlightbackground=C["bd"], highlightthickness=1,
        command=_open_custom_provider_dialog)
    ai_custom_btn.grid(row=5, column=0, columnspan=3, sticky="w", pady=(4, 0))

    # 切换提供商：自动带出 BaseURL/模型（不覆盖用户手填）+ 载入已存密钥
    def _on_provider_change(event=None):
        pid = _label_to_id.get(_prov_var.get(), "")
        prev_pid = getattr(_on_provider_change, "_prev", None)
        new_p = _prov_by_id.get(pid, {})
        prev_p = _prov_by_id.get(prev_pid, {}) if prev_pid else {}
        cur_base = api_base_var.get().strip()
        prev_base = (prev_p.get("base_url") or "").strip()
        new_base = (new_p.get("base_url") or "").strip()
        if (not cur_base) or (prev_base and cur_base == prev_base):
            api_base_var.set(new_base)
        prev_models = prev_p.get("models") or []
        prev_first = prev_models[0] if prev_models else ""
        new_models = new_p.get("models") or []
        new_first = new_models[0] if new_models else ""
        cur_model = api_model_var.get().strip()
        if (not cur_model) or (prev_first and cur_model == prev_first):
            if new_first:
                api_model_var.set(new_first)
        try:
            api_key_var.set((_aic.get_provider_key(pid) if _aic is not None else None) or "")
        except Exception:
            pass
        try:
            ai_test_result.config(text="", fg=C["fgm"])
        except Exception:
            pass
        if _aic is not None:
            try:
                _aic.refresh_key_cache()
            except Exception:
                pass
        _on_provider_change._prev = pid

    _on_provider_change._prev = _init_pid
    _prov_combo.bind("<<ComboboxSelected>>", _on_provider_change)

    # 绑定防抖保存 (AI 增强卡)
    _track_card_vars("ai", (api_key_var, api_model_var, api_base_var, _prov_var,
        ai_smart_retry_var, ai_anomaly_var))

    return {
        "card": card, "content": content, "title": title,
        "_aic": _aic, "_prov_var": _prov_var, "_label_to_id": _label_to_id,
        "api_model_var": api_model_var, "api_base_var": api_base_var,
        "api_key_var": api_key_var, "ai_smart_retry_var": ai_smart_retry_var,
        "ai_anomaly_var": ai_anomaly_var,
    }


def apply(ctx, handles):
    """保存「AI 增强」卡配置，并按需启停异常检测器 (等价原 _apply_ai_settings)。

    提供商/模型/BaseURL 写入 state.AI_*；密钥走 Windows 凭据库
    ACRPA/ai_key/<pid>（绝不落 config.json）并清空 Entry 显示值。
    """
    _aic = handles["_aic"]
    _label_to_id = handles["_label_to_id"]
    _prov_var = handles["_prov_var"]
    api_model_var = handles["api_model_var"]
    api_base_var = handles["api_base_var"]
    api_key_var = handles["api_key_var"]

    pid = _label_to_id.get(_prov_var.get(), "") or (getattr(state, "AI_PROVIDER", "") or "")
    state.AI_PROVIDER = pid
    # 提供商变更后无条件失效 has_ai_key 缓存：否则切换到「无已存 key 的 provider」
    # 且未输入新 key 时，缓存会残留旧 provider 的 True（守卫放行但请求 401）。
    if _aic is not None:
        try:
            _aic.refresh_key_cache()
        except Exception:
            pass
    state.AI_MODEL = api_model_var.get().strip()
    state.AI_BASE_URL = api_base_var.get().strip()
    key = api_key_var.get().strip()
    if key:
        if pid:
            if _aic is not None:
                try:
                    _aic.set_provider_key(pid, key)
                except Exception:
                    pass
        else:
            # provider 为空 → 回退旧路径（save_config 会写入凭据库 ACRPA/api_key）
            state.API_KEY = key
        if _aic is not None:
            try:
                _aic.refresh_key_cache()
            except Exception:
                pass
        # 清空 Entry 显示值，避免明文常驻 UI／被误存
        try:
            api_key_var.set("")
        except Exception:
            pass
    # api_model 同步策略：仅当选自预设（非 custom）、且 api_model 仍是旧默认值时同步。
    if (state.AI_MODEL and state.AI_MODEL != state.API_MODEL
            and state.API_MODEL == "deepseek-v4-flash"
            and pid and pid != "custom"):
        state.API_MODEL = state.AI_MODEL
    state.AI_SMART_RETRY = handles["ai_smart_retry_var"].get()
    state.AI_ANOMALY_DETECT = handles["ai_anomaly_var"].get()
    # 异常检测启用条件与真实可用性一致（凭据库优先，state.API_KEY 回退）
    try:
        _key_ok = bool(_aic.has_ai_key()) if _aic is not None else bool(state.API_KEY)
    except Exception:
        _key_ok = bool(state.API_KEY)
    if state.AI_ANOMALY_DETECT and _key_ok:
        try:
            from ai_enhance import anomaly_detector
            anomaly_detector.enable()
        except Exception:
            pass
    else:
        try:
            from ai_enhance import anomaly_detector
            anomaly_detector.disable()
        except Exception:
            pass
