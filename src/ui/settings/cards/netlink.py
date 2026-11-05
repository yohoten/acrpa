# -*- coding: utf-8 -*-
"""ui.settings.cards.netlink — ⊕ 网络互联卡 (原 settings_window 2130-2510 行段)。

导出:
    build(parent, ctx) -> handles
    apply(ctx, handles) -> None       # 等价原 _apply_netlink_settings
"""
import tkinter
from tkinter import ttk, filedialog

import state

from ui.settings.cards._layout import section, row, switch_row, help_line

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
    sp = ctx.sp

    card, nl_content, nl_title, _ = _make_collapsible_card(parent, "网络互联", "⊕")
    card.grid(row=ROW, column=0, sticky="ew", **ctx.PAD)
    _make_badge(CARD_KEY, nl_title)
    _register_nav_card(CARD_KEY, card)

    _r = 0

    def _add(w, pady=None):
        nonlocal _r
        w.grid(row=_r, column=0, sticky="ew",
               pady=((0, sp("row_gap")) if pady is None else pady))
        _r += 1
        return w

    def _sec(name, first=False):
        return _add(section(nl_content, ctx, name),
                    pady=(sp("sp_sm") if first else sp("section_gap"), 0))

    def _num(p, var, key, fb, lo, hi, w=7):
        return _validate_number(tkinter.Entry(p, textvariable=var, width=w,
            font=ctx.FONT_SMALL, bg=C["ebg"], fg=C["fgb"],
            relief="solid", bd=1), var, key, fb, int, lo, hi, card_key="netlink")

    def _entry(p, var, w):
        return tkinter.Entry(p, textvariable=var, width=w,
            font=ctx.FONT_SMALL, bg=C["ebg"], fg=C["fgb"],
            relief="solid", bd=1)

    _NL_PERM_MAP = {"仅观察": "observe", "允许操控": "control", "允许接收脚本": "script"}
    _nl_perm_label = {v: k for k, v in _NL_PERM_MAP.items()}
    _NL_WEB_BIND_MAP = {"所有网卡（同网段可访问）": "0.0.0.0", "仅本机": "127.0.0.1"}
    _nl_web_bind_label = {v: k for k, v in _NL_WEB_BIND_MAP.items()}

    # ── 基础连接 ──
    _sec("基础连接", first=True)
    nl_enabled_var = tkinter.BooleanVar(value=getattr(state, "NETLINK_ENABLED", False))
    _add(switch_row(nl_content, ctx, "启用设备互联", nl_enabled_var,
                    help="允许受信任设备发现并连接本机"))
    nl_auto_var = tkinter.BooleanVar(value=getattr(state, "NETLINK_AUTODISCOVER", True))
    _add(switch_row(nl_content, ctx, "UDP 自动发现", nl_auto_var,
                    help="在可信局域网内自动发现其他 ACRPA 设备"))
    nl_port_var = tkinter.StringVar(value=str(getattr(state, "NETLINK_PORT", 19710)))
    _add(row(nl_content, ctx, "TCP 端口", lambda p: _num(
        p, nl_port_var, "互联 TCP 端口", 19710, 1, 65535), unit="1-65535"))
    nl_dport_var = tkinter.StringVar(value=str(getattr(state, "NETLINK_DISCOVERY_PORT", 19711)))
    _add(row(nl_content, ctx, "发现端口", lambda p: _num(
        p, nl_dport_var, "互联发现端口", 19711, 1, 65535), unit="1-65535"))
    nl_name_var = tkinter.StringVar(value=getattr(state, "NETLINK_DEVICE_NAME", "") or "")
    _add(row(nl_content, ctx, "设备名", lambda p: _entry(p, nl_name_var, 16),
        help="本机在「设备互联」中显示的名称，空=使用主机名"))
    nl_perm_var = tkinter.StringVar(
        value=_nl_perm_label.get(getattr(state, "NETLINK_PERM_LEVEL", "observe"), "仅观察"))
    _add(row(nl_content, ctx, "权限级别", lambda p: ttk.Combobox(
        p, textvariable=nl_perm_var,
        values=("仅观察", "允许操控", "允许接收脚本"), state="readonly", width=14)))

    # ── 静态对端 ──
    _sec("静态对端")
    _nl_peers_count = len(getattr(state, "NETLINK_STATIC_PEERS", []) or [])

    def _clear_static_peers():
        try:
            state.NETLINK_STATIC_PEERS = []
            state.save_config()
            _flash_saved("netlink")
            _show_toast(root, "已清空静态对端", "info", 1500)
        except Exception as e:
            _log1("清空静态对端失败: {}".format(e), "warning")

    def _mk_peers(p):
        f = tkinter.Frame(p, bg=C["bgc"])
        tkinter.Label(f, text="已配置 {} 个（在「设备互联」窗口维护）".format(_nl_peers_count),
            font=ctx.FONT_SMALL, fg=C["fgm"], bg=C["bgc"]).pack(side="left", padx=(0, 8))
        _btn(f, "清空", _clear_static_peers, "dg", "white",
            tip="清空手动添加的静态对端列表 (不影响自动发现)").pack(side="left")
        return f

    _add(row(nl_content, ctx, "", _mk_peers))

    # ── 配对认证 ──
    _sec("配对认证")
    nl_require_auth_var = tkinter.BooleanVar(
        value=bool(getattr(state, "NETLINK_REQUIRE_AUTH", True)))
    _add(switch_row(nl_content, ctx, "启用配对认证", nl_require_auth_var,
                    help="关闭后同网段任意设备可只读查看运行状态与日志"))
    nl_auth_warn = _add(help_line(nl_content, ctx,
        "⚠ 关闭认证后，同网段任何设备都可读取本机运行状态与日志", level="err"))

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

    nl_ttl_var = tkinter.StringVar(value=str(getattr(state, "NETLINK_PIN_TTL", 600)))
    _add(row(nl_content, ctx, "配对码有效期", lambda p: _num(
        p, nl_ttl_var, "配对码有效期", 600, 10, 86400), unit="秒 (10-86400)"))

    # ── 浏览器只读监控 ──
    _sec("浏览器只读监控")
    nl_web_enabled_var = tkinter.BooleanVar(
        value=bool(getattr(state, "NETLINK_WEB_ENABLED", False)))
    _add(row(nl_content, ctx, "", lambda p: ttk.Checkbutton(
        p, text="启用浏览器只读监控面板", variable=nl_web_enabled_var)))
    nl_web_port_var = tkinter.StringVar(
        value=str(getattr(state, "NETLINK_WEB_PORT", 19712)))
    _add(row(nl_content, ctx, "面板端口", lambda p: _num(
        p, nl_web_port_var, "面板端口", 19712, 1, 65535), unit="1-65535"))
    nl_web_bind_var = tkinter.StringVar(
        value=_nl_web_bind_label.get(
            str(getattr(state, "NETLINK_WEB_BIND", "0.0.0.0")),
            "所有网卡（同网段可访问）"))
    _add(row(nl_content, ctx, "监听范围", lambda p: ttk.Combobox(
        p, textvariable=nl_web_bind_var,
        values=("所有网卡（同网段可访问）", "仅本机"), state="readonly", width=18)))
    nl_web_warn = _add(help_line(nl_content, ctx,
        "⚠ 面板会暴露本机运行状态与操作日志，请仅在可信内网使用", level="warn"))

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

    # ── TLS 加密 ──
    _sec("TLS 加密")
    nl_tls_var = tkinter.BooleanVar(value=bool(getattr(state, "NETLINK_TLS", False)))
    _add(row(nl_content, ctx, "", lambda p: ttk.Checkbutton(
        p, text="启用 TLS 加密（需提供自签证书 PEM）", variable=nl_tls_var)))

    nl_tls_cert_var = tkinter.StringVar(
        value=str(getattr(state, "NETLINK_TLS_CERT", "") or ""))

    def _browse_tls_cert():
        try:
            p = filedialog.askopenfilename(
                title="选择服务端证书 PEM",
                filetypes=[("证书 PEM", "*.pem *.crt *.key"), ("All", "*.*")])
            if p:
                nl_tls_cert_var.set(p)
        except Exception:
            pass

    def _mk_tls_cert(p):
        f = tkinter.Frame(p, bg=C["bgc"])
        _entry(f, nl_tls_cert_var, 22).pack(side="left", padx=(0, 4))
        _btn(f, "浏览…", _browse_tls_cert, "ac", "white",
             tip="选择服务端证书 PEM").pack(side="left")
        return f

    _add(row(nl_content, ctx, "证书 PEM", _mk_tls_cert))

    nl_tls_key_var = tkinter.StringVar(
        value=str(getattr(state, "NETLINK_TLS_KEY", "") or ""))

    def _browse_tls_key():
        try:
            p = filedialog.askopenfilename(
                title="选择服务端私钥 PEM",
                filetypes=[("私钥 PEM", "*.pem *.crt *.key"), ("All", "*.*")])
            if p:
                nl_tls_key_var.set(p)
        except Exception:
            pass

    def _mk_tls_key(p):
        f = tkinter.Frame(p, bg=C["bgc"])
        _entry(f, nl_tls_key_var, 22).pack(side="left", padx=(0, 4))
        _btn(f, "浏览…", _browse_tls_key, "ac", "white",
             tip="选择服务端私钥 PEM").pack(side="left")
        return f

    _add(row(nl_content, ctx, "私钥 PEM", _mk_tls_key))

    nl_tls_warn = _add(help_line(nl_content, ctx,
        "⚠ 启用 TLS 后必须同时配置证书与私钥，否则设备互联将拒绝启动", level="warn"))

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
    _nl_pins_count = len(getattr(state, "NETLINK_TLS_PINS", []) or [])

    def _clear_tls_pins():
        try:
            state.NETLINK_TLS_PINS = []
            state.save_config()
            _flash_saved("netlink")
            _show_toast(root, "已清空已固定指纹", "info", 1500)
        except Exception as e:
            _log1("清空已固定指纹失败: {}".format(e), "warning")

    def _mk_pins(p):
        f = tkinter.Frame(p, bg=C["bgc"])
        tkinter.Label(f, text="已固定证书指纹: {} 个".format(_nl_pins_count),
            font=ctx.FONT_SMALL, fg=C["fgm"], bg=C["bgc"]).pack(side="left", padx=(0, 8))
        _btn(f, "清空指纹", _clear_tls_pins, "dg", "white",
             tip="清空已固定的对端证书指纹（下次连接将重新 TOFU 首次信任）").pack(side="left")
        return f

    _add(row(nl_content, ctx, "", _mk_pins))

    # ── 网页面板有限控制 ──
    _sec("网页面板有限控制")
    nl_web_control_var = tkinter.BooleanVar(
        value=bool(getattr(state, "NETLINK_WEB_CONTROL", False)))
    _add(row(nl_content, ctx, "", lambda p: ttk.Checkbutton(
        p, text="启用有限控制（run/stop，强制 HTTPS）", variable=nl_web_control_var)))
    nl_web_tls_var = tkinter.BooleanVar(
        value=bool(getattr(state, "NETLINK_WEB_TLS", False)))
    _add(row(nl_content, ctx, "", lambda p: ttk.Checkbutton(
        p, text="面板启用 HTTPS（控制必需）", variable=nl_web_tls_var)))
    nl_web_control_ttl_var = tkinter.StringVar(
        value=str(getattr(state, "NETLINK_WEB_CONTROL_TTL", 300)))
    _add(row(nl_content, ctx, "控制会话 TTL", lambda p: _num(
        p, nl_web_control_ttl_var, "控制会话 TTL", 300, 60, 3600),
        unit="秒 (60-3600)"))
    nl_web_confirm_control_var = tkinter.BooleanVar(
        value=bool(getattr(state, "NETLINK_WEB_CONFIRM_CONTROL", False)))
    _add(row(nl_content, ctx, "", lambda p: ttk.Checkbutton(
        p, text="本地面板控制也需桌面确认", variable=nl_web_confirm_control_var)))
    nl_web_allow_remote_var = tkinter.BooleanVar(
        value=bool(getattr(state, "NETLINK_WEB_ALLOW_REMOTE_CONTROL", False)))
    _add(row(nl_content, ctx, "", lambda p: ttk.Checkbutton(
        p, text="允许局域网控制（高危）", variable=nl_web_allow_remote_var)))
    nl_web_ctl_warn = _add(help_line(nl_content, ctx,
        "⚠ 控制 PIN 在「设备互联 → 网页面板」中设置/重置；控制强制 HTTPS，"
        "需先配置证书与私钥 PEM（否则面板拒绝启用）", level="warn"))

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
