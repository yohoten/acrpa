# -*- coding: utf-8 -*-
"""ui.settings.cards.netlink — ⊕ 网络互联卡 (原 settings_window 2130-2510 行段)。

导出:
    build(parent, ctx) -> handles
    apply(ctx, handles) -> None       # 等价原 _apply_netlink_settings
"""
import tkinter
from tkinter import ttk, filedialog

import state

CARD_KEY = "netlink"
ROW = 8


def build(parent, ctx):
    C = ctx.C
    _btn = ctx.btn
    _flash_saved = ctx.flash_saved
    _log1 = ctx.log1
    _show_toast = ctx.show_toast
    root = ctx.root
    _validate_number = ctx.validate_number
    _make_collapsible_card = ctx.make_collapsible_card
    _make_badge = ctx.make_badge
    _register_nav_card = ctx.register_nav_card
    _track_card_vars = ctx.track_card_vars

    card, nl_content, nl_title, _ = _make_collapsible_card(parent, "网络互联", "⊕")
    card.grid(row=ROW, column=0, sticky="ew", **ctx.PAD)
    _make_badge(CARD_KEY, nl_title)
    _register_nav_card(CARD_KEY, card)

    # 启用开关 + 自动发现
    nl_switch_frame = tkinter.Frame(nl_content, bg=C["bgc"])
    nl_switch_frame.grid(row=0, column=0, sticky="ew", padx=8, pady=(2, 1))
    nl_enabled_var = tkinter.BooleanVar(value=getattr(state, "NETLINK_ENABLED", False))
    ttk.Checkbutton(nl_switch_frame, text="启用设备互联",
        variable=nl_enabled_var).pack(side="left", padx=(0, 16))
    nl_auto_var = tkinter.BooleanVar(value=getattr(state, "NETLINK_AUTODISCOVER", True))
    ttk.Checkbutton(nl_switch_frame, text="UDP 自动发现",
        variable=nl_auto_var).pack(side="left")

    # TCP 端口 / 发现端口 / 设备名
    nl_addr_frame = tkinter.Frame(nl_content, bg=C["bgc"])
    nl_addr_frame.grid(row=1, column=0, sticky="ew", padx=8, pady=1)
    tkinter.Label(nl_addr_frame, text="TCP端口", font=ctx.FONT_SMALL,
        fg=C["fgm"], bg=C["bgc"]).pack(side="left", padx=(0, 4))
    nl_port_var = tkinter.StringVar(value=str(getattr(state, "NETLINK_PORT", 19710)))
    _validate_number(tkinter.Entry(nl_addr_frame, textvariable=nl_port_var, width=7,
        font=ctx.FONT_BODY, relief="solid", bd=1, bg=C["ebg"], fg=C["fgb"]),
        nl_port_var, "互联 TCP 端口", 19710, int, 1, 65535, card_key="netlink").pack(
        side="left", padx=(0, 10))
    tkinter.Label(nl_addr_frame, text="发现端口", font=ctx.FONT_SMALL,
        fg=C["fgm"], bg=C["bgc"]).pack(side="left", padx=(0, 4))
    nl_dport_var = tkinter.StringVar(value=str(getattr(state, "NETLINK_DISCOVERY_PORT", 19711)))
    _validate_number(tkinter.Entry(nl_addr_frame, textvariable=nl_dport_var, width=7,
        font=ctx.FONT_BODY, relief="solid", bd=1, bg=C["ebg"], fg=C["fgb"]),
        nl_dport_var, "互联发现端口", 19711, int, 1, 65535, card_key="netlink").pack(
        side="left", padx=(0, 10))
    tkinter.Label(nl_addr_frame, text="设备名", font=ctx.FONT_SMALL,
        fg=C["fgm"], bg=C["bgc"]).pack(side="left", padx=(0, 4))
    nl_name_var = tkinter.StringVar(value=getattr(state, "NETLINK_DEVICE_NAME", "") or "")
    tkinter.Entry(nl_addr_frame, textvariable=nl_name_var, width=16,
        font=ctx.FONT_BODY, relief="solid", bd=1, bg=C["ebg"], fg=C["fgb"]).pack(side="left")

    # 权限级别
    nl_perm_frame = tkinter.Frame(nl_content, bg=C["bgc"])
    nl_perm_frame.grid(row=2, column=0, sticky="ew", padx=8, pady=1)
    tkinter.Label(nl_perm_frame, text="权限级别", font=ctx.FONT_SMALL,
        fg=C["fgm"], bg=C["bgc"]).pack(side="left", padx=(0, 4))
    _NL_PERM_MAP = {"仅观察": "observe", "允许操控": "control", "允许接收脚本": "script"}
    _nl_perm_label = {v: k for k, v in _NL_PERM_MAP.items()}
    nl_perm_var = tkinter.StringVar(
        value=_nl_perm_label.get(getattr(state, "NETLINK_PERM_LEVEL", "observe"), "仅观察"))
    ttk.Combobox(nl_perm_frame, textvariable=nl_perm_var,
        values=("仅观察", "允许操控", "允许接收脚本"),
        state="readonly", width=14).pack(side="left", padx=(0, 12))

    # 静态对端 (只读显示 + 清空; 明细在「设备互联」窗口维护)
    nl_peers_frame = tkinter.Frame(nl_content, bg=C["bgc"])
    nl_peers_frame.grid(row=3, column=0, sticky="ew", padx=8, pady=(1, 6))
    _nl_peers_count = len(getattr(state, "NETLINK_STATIC_PEERS", []) or [])
    tkinter.Label(nl_peers_frame,
        text="静态对端: {} 个 (在「设备互联」窗口维护)".format(_nl_peers_count),
        font=ctx.FONT_SMALL, fg=C["fgm"], bg=C["bgc"]).pack(side="left", padx=(0, 8))

    def _clear_static_peers():
        try:
            state.NETLINK_STATIC_PEERS = []
            state.save_config()
            _flash_saved("netlink")
            _show_toast(root, "已清空静态对端", "info", 1500)
        except Exception as e:
            _log1("清空静态对端失败: {}".format(e), "warning")

    _btn(nl_peers_frame, "清空", _clear_static_peers, "dg", "white",
        tip="清空手动添加的静态对端列表 (不影响自动发现)").pack(side="left")

    # 配对认证开关 + 关闭风险红字提示 (仅关闭时显示)
    nl_auth_frame = tkinter.Frame(nl_content, bg=C["bgc"])
    nl_auth_frame.grid(row=4, column=0, sticky="ew", padx=8, pady=(1, 1))
    nl_require_auth_var = tkinter.BooleanVar(
        value=bool(getattr(state, "NETLINK_REQUIRE_AUTH", True)))
    ttk.Checkbutton(nl_auth_frame,
        text="启用配对认证（关闭后同网段任意设备可只读查看）",
        variable=nl_require_auth_var).pack(side="left")
    nl_auth_warn = tkinter.Label(nl_content,
        text="⚠ 关闭认证后，同网段任何设备都可读取本机运行状态与日志",
        font=ctx.FONT_SMALL, fg=C["err"], bg=C["bgc"])
    nl_auth_warn.grid(row=5, column=0, sticky="w", padx=24, pady=(0, 2))

    def _toggle_auth_warn():
        try:
            if nl_require_auth_var.get():
                nl_auth_warn.grid_remove()
            else:
                nl_auth_warn.grid()
        except Exception:
            pass

    try:
        nl_require_auth_var.trace_add("write", lambda *_: _toggle_auth_warn())
    except Exception:
        pass
    _toggle_auth_warn()

    # 配对码有效期 (秒)
    nl_ttl_frame = tkinter.Frame(nl_content, bg=C["bgc"])
    nl_ttl_frame.grid(row=6, column=0, sticky="ew", padx=8, pady=(1, 6))
    tkinter.Label(nl_ttl_frame, text="配对码有效期(秒)", font=ctx.FONT_SMALL,
        fg=C["fgm"], bg=C["bgc"]).pack(side="left", padx=(0, 4))
    nl_ttl_var = tkinter.StringVar(value=str(getattr(state, "NETLINK_PIN_TTL", 600)))
    _validate_number(tkinter.Entry(nl_ttl_frame, textvariable=nl_ttl_var, width=7,
        font=ctx.FONT_BODY, relief="solid", bd=1, bg=C["ebg"], fg=C["fgb"]),
        nl_ttl_var, "配对码有效期", 600, int, 10, 86400, card_key="netlink").pack(
        side="left")

    # 浏览器只读监控面板 (Phase4-2: 默认关闭, 启用即要求令牌)
    nl_web_frame = tkinter.Frame(nl_content, bg=C["bgc"])
    nl_web_frame.grid(row=7, column=0, sticky="ew", padx=8, pady=(1, 1))
    nl_web_enabled_var = tkinter.BooleanVar(
        value=bool(getattr(state, "NETLINK_WEB_ENABLED", False)))
    ttk.Checkbutton(nl_web_frame, text="启用浏览器只读监控面板",
        variable=nl_web_enabled_var).pack(side="left", padx=(0, 12))
    tkinter.Label(nl_web_frame, text="端口", font=ctx.FONT_SMALL,
        fg=C["fgm"], bg=C["bgc"]).pack(side="left", padx=(0, 4))
    nl_web_port_var = tkinter.StringVar(
        value=str(getattr(state, "NETLINK_WEB_PORT", 19712)))
    _validate_number(tkinter.Entry(nl_web_frame, textvariable=nl_web_port_var, width=7,
        font=ctx.FONT_BODY, relief="solid", bd=1, bg=C["ebg"], fg=C["fgb"]),
        nl_web_port_var, "面板端口", 19712, int, 1, 65535, card_key="netlink").pack(
        side="left", padx=(0, 10))
    tkinter.Label(nl_web_frame, text="监听范围", font=ctx.FONT_SMALL,
        fg=C["fgm"], bg=C["bgc"]).pack(side="left", padx=(0, 4))
    _NL_WEB_BIND_MAP = {"所有网卡（同网段可访问）": "0.0.0.0", "仅本机": "127.0.0.1"}
    _nl_web_bind_label = {v: k for k, v in _NL_WEB_BIND_MAP.items()}
    nl_web_bind_var = tkinter.StringVar(
        value=_nl_web_bind_label.get(
            str(getattr(state, "NETLINK_WEB_BIND", "0.0.0.0")),
            "所有网卡（同网段可访问）"))
    ttk.Combobox(nl_web_frame, textvariable=nl_web_bind_var,
        values=("所有网卡（同网段可访问）", "仅本机"),
        state="readonly", width=18).pack(side="left")

    nl_web_warn = tkinter.Label(nl_content,
        text="⚠ 面板会暴露本机运行状态与操作日志，请仅在可信内网使用",
        font=ctx.FONT_SMALL, fg=C["wn"], bg=C["bgc"])
    nl_web_warn.grid(row=8, column=0, sticky="w", padx=24, pady=(0, 2))

    def _toggle_web_warn():
        try:
            if nl_web_enabled_var.get():
                nl_web_warn.grid()
            else:
                nl_web_warn.grid_remove()
        except Exception:
            pass

    try:
        nl_web_enabled_var.trace_add("write", lambda *_: _toggle_web_warn())
    except Exception:
        pass
    _toggle_web_warn()

    # ── TLS 可选档 (Phase4-3: 自签证书 + 指纹固定) ──
    nl_tls_frame = tkinter.Frame(nl_content, bg=C["bgc"])
    nl_tls_frame.grid(row=9, column=0, sticky="ew", padx=8, pady=(1, 1))
    nl_tls_var = tkinter.BooleanVar(value=bool(getattr(state, "NETLINK_TLS", False)))
    ttk.Checkbutton(nl_tls_frame, text="启用 TLS 加密（需提供自签证书 PEM）",
        variable=nl_tls_var).pack(side="left")

    nl_tls_cert_frame = tkinter.Frame(nl_content, bg=C["bgc"])
    nl_tls_cert_frame.grid(row=10, column=0, sticky="ew", padx=8, pady=1)
    tkinter.Label(nl_tls_cert_frame, text="证书PEM", font=ctx.FONT_SMALL,
        fg=C["fgm"], bg=C["bgc"]).pack(side="left", padx=(0, 4))
    nl_tls_cert_var = tkinter.StringVar(
        value=str(getattr(state, "NETLINK_TLS_CERT", "") or ""))
    tkinter.Entry(nl_tls_cert_frame, textvariable=nl_tls_cert_var, width=22,
        font=ctx.FONT_SMALL, bg=C["ebg"], fg=C["fgb"], relief="solid", bd=1).pack(
        side="left", padx=(0, 4))

    def _browse_tls_cert():
        try:
            p = filedialog.askopenfilename(
                title="选择服务端证书 PEM",
                filetypes=[("证书 PEM", "*.pem *.crt *.key"), ("All", "*.*")])
            if p:
                nl_tls_cert_var.set(p)
        except Exception:
            pass

    _btn(nl_tls_cert_frame, "浏览…", _browse_tls_cert, "ac", "white",
         tip="选择服务端证书 PEM").pack(side="left", padx=(0, 8))
    tkinter.Label(nl_tls_cert_frame, text="私钥PEM", font=ctx.FONT_SMALL,
        fg=C["fgm"], bg=C["bgc"]).pack(side="left", padx=(0, 4))
    nl_tls_key_var = tkinter.StringVar(
        value=str(getattr(state, "NETLINK_TLS_KEY", "") or ""))
    tkinter.Entry(nl_tls_cert_frame, textvariable=nl_tls_key_var, width=22,
        font=ctx.FONT_SMALL, bg=C["ebg"], fg=C["fgb"], relief="solid", bd=1).pack(
        side="left", padx=(0, 4))

    def _browse_tls_key():
        try:
            p = filedialog.askopenfilename(
                title="选择服务端私钥 PEM",
                filetypes=[("私钥 PEM", "*.pem *.crt *.key"), ("All", "*.*")])
            if p:
                nl_tls_key_var.set(p)
        except Exception:
            pass

    _btn(nl_tls_cert_frame, "浏览…", _browse_tls_key, "ac", "white",
         tip="选择服务端私钥 PEM").pack(side="left")

    nl_tls_warn = tkinter.Label(nl_content,
        text="⚠ 启用 TLS 后必须同时配置证书与私钥，否则设备互联将拒绝启动",
        font=ctx.FONT_SMALL, fg=C["wn"], bg=C["bgc"])
    nl_tls_warn.grid(row=11, column=0, sticky="w", padx=24, pady=(0, 2))

    def _toggle_tls_warn():
        try:
            if nl_tls_var.get():
                nl_tls_warn.grid()
            else:
                nl_tls_warn.grid_remove()
        except Exception:
            pass

    try:
        nl_tls_var.trace_add("write", lambda *_: _toggle_tls_warn())
    except Exception:
        pass
    _toggle_tls_warn()

    # 已固定指纹 (只读展示 + 清空; TOFU 首次信任记录)
    nl_pins_frame = tkinter.Frame(nl_content, bg=C["bgc"])
    nl_pins_frame.grid(row=12, column=0, sticky="ew", padx=8, pady=(1, 6))
    _nl_pins_count = len(getattr(state, "NETLINK_TLS_PINS", []) or [])
    tkinter.Label(nl_pins_frame,
        text="已固定证书指纹: {} 个".format(_nl_pins_count),
        font=ctx.FONT_SMALL, fg=C["fgm"], bg=C["bgc"]).pack(side="left", padx=(0, 8))

    def _clear_tls_pins():
        try:
            state.NETLINK_TLS_PINS = []
            state.save_config()
            _flash_saved("netlink")
            _show_toast(root, "已清空已固定指纹", "info", 1500)
        except Exception as e:
            _log1("清空已固定指纹失败: {}".format(e), "warning")

    _btn(nl_pins_frame, "清空指纹", _clear_tls_pins, "dg", "white",
         tip="清空已固定的对端证书指纹（下次连接将重新 TOFU 首次信任）").pack(side="left")

    # ── 网页面板「有限控制」(Phase4-2 演进；默认全关 = 行为完全不变) ──
    nl_web_ctl_frame = tkinter.Frame(nl_content, bg=C["bgc"])
    nl_web_ctl_frame.grid(row=13, column=0, sticky="ew", padx=8, pady=(1, 1))
    nl_web_control_var = tkinter.BooleanVar(
        value=bool(getattr(state, "NETLINK_WEB_CONTROL", False)))
    ttk.Checkbutton(nl_web_ctl_frame,
        text="启用网页面板有限控制（run/stop，强制 HTTPS）",
        variable=nl_web_control_var).pack(side="left", padx=(0, 12))
    nl_web_tls_var = tkinter.BooleanVar(
        value=bool(getattr(state, "NETLINK_WEB_TLS", False)))
    ttk.Checkbutton(nl_web_ctl_frame,
        text="面板启用 HTTPS（控制必需）",
        variable=nl_web_tls_var).pack(side="left")

    nl_web_ctl2 = tkinter.Frame(nl_content, bg=C["bgc"])
    nl_web_ctl2.grid(row=14, column=0, sticky="ew", padx=8, pady=(1, 1))
    tkinter.Label(nl_web_ctl2, text="控制会话 TTL(秒)", font=ctx.FONT_SMALL,
        fg=C["fgm"], bg=C["bgc"]).pack(side="left", padx=(0, 4))
    nl_web_control_ttl_var = tkinter.StringVar(
        value=str(getattr(state, "NETLINK_WEB_CONTROL_TTL", 300)))
    _validate_number(tkinter.Entry(nl_web_ctl2, textvariable=nl_web_control_ttl_var,
        width=6, font=ctx.FONT_BODY, relief="solid", bd=1, bg=C["ebg"], fg=C["fgb"]),
        nl_web_control_ttl_var, "控制会话 TTL", 300, int, 60, 3600,
        card_key="netlink").pack(side="left", padx=(0, 10))
    nl_web_confirm_control_var = tkinter.BooleanVar(
        value=bool(getattr(state, "NETLINK_WEB_CONFIRM_CONTROL", False)))
    ttk.Checkbutton(nl_web_ctl2, text="本地面板控制也需桌面确认",
        variable=nl_web_confirm_control_var).pack(side="left", padx=(0, 10))
    nl_web_allow_remote_var = tkinter.BooleanVar(
        value=bool(getattr(state, "NETLINK_WEB_ALLOW_REMOTE_CONTROL", False)))
    ttk.Checkbutton(nl_web_ctl2, text="允许局域网控制(高危)",
        variable=nl_web_allow_remote_var).pack(side="left")

    nl_web_ctl_warn = tkinter.Label(nl_content,
        text="⚠ 控制 PIN 在「设备互联 → 网页面板」中设置/重置；控制强制 HTTPS，"
             "需先配置证书与私钥 PEM（否则面板拒绝启用）",
        font=ctx.FONT_SMALL, fg=C["wn"], bg=C["bgc"])
    nl_web_ctl_warn.grid(row=15, column=0, sticky="w", padx=24, pady=(0, 6))

    def _toggle_web_ctl_warn():
        try:
            if nl_web_control_var.get():
                nl_web_ctl_warn.grid()
            else:
                nl_web_ctl_warn.grid_remove()
        except Exception:
            pass

    try:
        nl_web_control_var.trace_add("write", lambda *_: _toggle_web_ctl_warn())
    except Exception:
        pass
    _toggle_web_ctl_warn()

    # 绑定防抖保存 (网络互联卡)
    _track_card_vars("netlink", (nl_enabled_var, nl_port_var, nl_name_var,
        nl_auto_var, nl_dport_var, nl_perm_var, nl_require_auth_var, nl_ttl_var,
        nl_web_enabled_var, nl_web_port_var, nl_web_bind_var,
        nl_tls_var, nl_tls_cert_var, nl_tls_key_var,
        nl_web_control_var, nl_web_tls_var, nl_web_control_ttl_var,
        nl_web_confirm_control_var, nl_web_allow_remote_var))

    return {
        "card": card, "content": nl_content, "title": nl_title,
        "nl_enabled_var": nl_enabled_var, "nl_port_var": nl_port_var,
        "nl_name_var": nl_name_var, "nl_auto_var": nl_auto_var,
        "nl_dport_var": nl_dport_var, "nl_perm_var": nl_perm_var,
        "nl_require_auth_var": nl_require_auth_var, "nl_ttl_var": nl_ttl_var,
        "nl_web_enabled_var": nl_web_enabled_var, "nl_web_port_var": nl_web_port_var,
        "nl_web_bind_var": nl_web_bind_var, "nl_tls_var": nl_tls_var,
        "nl_tls_cert_var": nl_tls_cert_var, "nl_tls_key_var": nl_tls_key_var,
        "nl_web_control_var": nl_web_control_var, "nl_web_tls_var": nl_web_tls_var,
        "nl_web_control_ttl_var": nl_web_control_ttl_var,
        "nl_web_confirm_control_var": nl_web_confirm_control_var,
        "nl_web_allow_remote_var": nl_web_allow_remote_var,
    }


def apply(ctx, handles):
    """保存「网络互联」卡配置并同步 NetLink 节点 (等价原 _apply_netlink_settings)。"""
    _log1 = ctx.log1
    _show_toast = ctx.show_toast
    root = ctx.root

    state.NETLINK_ENABLED = handles["nl_enabled_var"].get()
    try:
        state.NETLINK_PORT = int(handles["nl_port_var"].get())
    except (ValueError, TypeError):
        state.NETLINK_PORT = 19710; handles["nl_port_var"].set("19710")
    state.NETLINK_DEVICE_NAME = handles["nl_name_var"].get().strip()
    state.NETLINK_AUTODISCOVER = handles["nl_auto_var"].get()
    try:
        state.NETLINK_DISCOVERY_PORT = int(handles["nl_dport_var"].get())
    except (ValueError, TypeError):
        state.NETLINK_DISCOVERY_PORT = 19711; handles["nl_dport_var"].set("19711")
    state.NETLINK_PERM_LEVEL = {"仅观察": "observe", "允许操控": "control",
                                "允许接收脚本": "script"}.get(
        handles["nl_perm_var"].get(), "observe")
    state.NETLINK_REQUIRE_AUTH = bool(handles["nl_require_auth_var"].get())
    try:
        state.NETLINK_PIN_TTL = int(handles["nl_ttl_var"].get())
    except (ValueError, TypeError):
        state.NETLINK_PIN_TTL = 600; handles["nl_ttl_var"].set("600")
    # Phase4-2 浏览器只读监控面板
    state.NETLINK_WEB_ENABLED = bool(handles["nl_web_enabled_var"].get())
    try:
        state.NETLINK_WEB_PORT = int(handles["nl_web_port_var"].get())
    except (ValueError, TypeError):
        state.NETLINK_WEB_PORT = 19712; handles["nl_web_port_var"].set("19712")
    state.NETLINK_WEB_BIND = {"所有网卡（同网段可访问）": "0.0.0.0",
                              "仅本机": "127.0.0.1"}.get(
        handles["nl_web_bind_var"].get(), "0.0.0.0")
    # Phase4-3 TLS 可选档
    state.NETLINK_TLS = bool(handles["nl_tls_var"].get())
    state.NETLINK_TLS_CERT = handles["nl_tls_cert_var"].get().strip()
    state.NETLINK_TLS_KEY = handles["nl_tls_key_var"].get().strip()
    # Phase4-2 网页面板有限控制 (默认全关)
    state.NETLINK_WEB_CONTROL = bool(handles["nl_web_control_var"].get())
    try:
        state.NETLINK_WEB_CONTROL_TTL = int(handles["nl_web_control_ttl_var"].get())
    except (ValueError, TypeError):
        state.NETLINK_WEB_CONTROL_TTL = 300
        handles["nl_web_control_ttl_var"].set("300")
    # 联动强制：控制开启 → 面板 TLS 必须为真（绝不回落明文）
    state.NETLINK_WEB_TLS = bool(handles["nl_web_tls_var"].get()) or state.NETLINK_WEB_CONTROL
    state.NETLINK_WEB_CONFIRM_CONTROL = bool(handles["nl_web_confirm_control_var"].get())
    state.NETLINK_WEB_ALLOW_REMOTE_CONTROL = bool(handles["nl_web_allow_remote_var"].get())
    # 开关变更即时生效 (节点重启; 全部 try/except, 失败不影响设置保存)
    try:
        import netlink
        if state.NETLINK_ENABLED:
            netlink.start_netlink(root)
            if state.NETLINK_WEB_ENABLED:
                # 控制/TLS 变更需重启面板才生效 → 先停后起
                try:
                    netlink.stop_webui()
                except Exception:
                    pass
                if not netlink.start_webui():
                    why = ""
                    try:
                        why = str(netlink.webui_last_error() or "")
                    except Exception:
                        why = ""
                    _log1("网页面板启动失败: {}".format(why or "unknown"), "warning")
                    try:
                        _show_toast(root, "网页面板启动失败：{}".format(
                            why or "证书/端口"), "warning", 3000)
                    except Exception:
                        pass
            else:
                netlink.stop_webui()
        else:
            netlink.stop_netlink()
    except Exception as e:
        _log1("NetLink 开关同步失败: {}".format(e), "warning")
