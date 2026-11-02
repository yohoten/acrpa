# -*- coding: utf-8 -*-
"""NetLinkWindow 方法组：web (L1-A 拆分)。"""

import os
import socket
import time
import tkinter
from tkinter import ttk

import state
import utils
from utils import log1

from ui.netlink.common import (
    APP_ROOT, DEFAULT_TCP_PORT, MAX_LOG_LINES, TRIM_LOG_LINES, PUMP_INTERVAL_MS,
    _CMD_LABELS, _CMD_VERB,
    _nl, _topics, _perm_labels, _perm_code, _perm_ok, _cmd_types,
    _cmd_run_script_type, _perm_script, _transfer, _transfer_topic,
    _screenshot_topic, _screen, _safe_int, _script_root, _fmt_size, _fmt_mtime,
)

class WebMixin(object):
    # _on_web_panel
    def _on_web_panel(self):
        """[⊕ 网页面板]：打开启用/地址/令牌管理对话框。

        默认只读；可选开启「有限控制」（仅 run / stop，需控制 PIN 解锁 +
        强制 HTTPS）。控制 PIN 的设置/重置在本对话框完成。
        """
        nl = _nl()
        if nl is None:
            self._set_status("网页面板不可用（netlink 未加载）", "warning")
            return None
        if not self.alive():
            return None
        C = utils.C
        dlg = self._web_dlg
        if dlg is not None:
            try:
                if dlg.winfo_exists():
                    self._web_refresh()
                    dlg.lift()
                    dlg.focus_force()
                    return dlg
            except Exception:
                pass
            self._web_dlg = None
        try:
            dlg = tkinter.Toplevel(self.win)
        except Exception:
            return None
        self._web_dlg = dlg
        try:
            dlg.title("网页面板 — ACRPA")
            try:    # 外壳: 尺寸/最小尺寸/父窗居中统一走 window_shell (§2.3 P2)
                from ui.window_shell import apply_window_chrome
                apply_window_chrome(dlg, size=(560, 420), min_size=(480, 360),
                                    parent=self.win, bg_key="bg", colors=C)
            except Exception:
                dlg.geometry("560x420")
            dlg.configure(bg=C["bg"])
            self._set_icon(dlg)
            dlg.protocol("WM_DELETE_WINDOW", self._close_web_dialog)
        except Exception:
            pass
        dlg.columnconfigure(0, weight=1)

        self._web_status_lbl = tkinter.Label(dlg, text="面板状态：已停止",
            font=utils.FONT_BODY, bg=C["bg"], fg=C["fgt"], anchor="w")
        self._web_status_lbl.grid(row=0, column=0, sticky="ew", padx=utils.sp(12), pady=(utils.sp(10), utils.sp(4)))

        self._web_btn_toggle = tkinter.Button(dlg, text="启用",
            font=utils.FONT_BUTTON, bg=C["ac"], fg=utils.text_on(C["ac"]),
            activebackground=C["ach"],
            activeforeground=utils.text_on(C["ach"]),
            relief="flat", bd=1, cursor="hand2", padx=utils.sp(12), pady=utils.sp(3),
            command=self._on_web_toggle)
        self._web_btn_toggle.grid(row=1, column=0, sticky="w", padx=utils.sp(12), pady=(0, utils.sp(6)))

        tkinter.Label(dlg, text="访问地址（含令牌，等同密码，请勿外泄）",
            font=utils.FONT_SMALL, bg=C["bg"], fg=C["fgm"], anchor="w").grid(
            row=2, column=0, sticky="w", padx=utils.sp(12), pady=(0, utils.sp(2)))
        self._web_url_var = tkinter.StringVar(value="")
        tkinter.Entry(dlg, textvariable=self._web_url_var, font=utils.FONT_BODY,
            relief="solid", bd=1, bg=C["ebg"], fg=C["fgb"], state="readonly").grid(
            row=3, column=0, sticky="ew", padx=utils.sp(12))

        btns = tkinter.Frame(dlg, bg=C["bg"])
        btns.grid(row=4, column=0, sticky="ew", padx=utils.sp(12), pady=(utils.sp(8), utils.sp(4)))
        tkinter.Button(btns, text="复制地址", font=utils.FONT_SMALL, bg=C["bgc"],
            fg=C["fgb"], activebackground=C["acl"], relief="flat", bd=1,
            cursor="hand2", padx=utils.sp(10), pady=utils.sp(1),
            command=self._on_web_copy).pack(side="left", padx=(0, utils.sp(6)))
        tkinter.Button(btns, text="在浏览器打开", font=utils.FONT_SMALL, bg=C["bgc"],
            fg=C["fgb"], activebackground=C["acl"], relief="flat", bd=1,
            cursor="hand2", padx=utils.sp(10), pady=utils.sp(1),
            command=self._on_web_open).pack(side="left", padx=(0, utils.sp(6)))
        tkinter.Button(btns, text="重新生成令牌", font=utils.FONT_SMALL, bg=C["dg"],
            fg=utils.text_on(C["dg"]), activebackground=C["dg"],
            activeforeground=utils.text_on(C["dg"]),
            relief="flat", bd=1, cursor="hand2", padx=utils.sp(10), pady=utils.sp(1),
            command=self._on_web_rotate).pack(side="left")

        tkinter.Label(dlg,
            text="⚠ 默认只读；开启「有限控制」后经 HTTPS + 控制 PIN 可远程运行/停止本机脚本，请仅在可信内网启用",
            font=utils.FONT_SMALL, bg=C["bg"], fg=C["wn"], anchor="w",
            wraplength=520, justify="left").grid(
            row=5, column=0, sticky="ew", padx=utils.sp(12), pady=(utils.sp(2), utils.sp(4)))

        ctl = tkinter.Frame(dlg, bg=C["bg"])
        ctl.grid(row=6, column=0, sticky="ew", padx=utils.sp(12), pady=(0, utils.sp(2)))
        self._web_control_var = tkinter.BooleanVar(
            value=bool(getattr(state, "NETLINK_WEB_CONTROL", False)))
        tkinter.Checkbutton(ctl, text="启用有限控制（run/stop，强制 HTTPS）",
            variable=self._web_control_var, bg=C["bg"], fg=C["fgb"],
            activebackground=C["bg"], selectcolor=C["bgc"], anchor="w",
            font=utils.FONT_SMALL,
            command=self._on_web_control_toggle).pack(side="left", padx=(0, utils.sp(8)))
        self._web_pin_btn = tkinter.Button(ctl, text="设置/重置控制 PIN",
            font=utils.FONT_SMALL, bg=C["bgc"], fg=C["fgb"],
            activebackground=C["acl"], relief="flat", bd=1, cursor="hand2",
            padx=utils.sp(8), pady=utils.sp(1), command=self._on_web_pin_manage)
        self._web_pin_btn.pack(side="left")

        ctl2 = tkinter.Frame(dlg, bg=C["bg"])
        ctl2.grid(row=7, column=0, sticky="ew", padx=utils.sp(12), pady=(0, utils.sp(4)))
        self._web_allow_remote_var = tkinter.BooleanVar(
            value=bool(getattr(state, "NETLINK_WEB_ALLOW_REMOTE_CONTROL", False)))
        tkinter.Checkbutton(ctl2, text="允许局域网控制（高危；默认收窄仅本机）",
            variable=self._web_allow_remote_var, bg=C["bg"], fg=C["wn"],
            activebackground=C["bg"], selectcolor=C["bgc"], anchor="w",
            font=utils.FONT_SMALL,
            command=self._on_web_allow_remote_toggle).pack(side="left")

        self._web_tls_lbl = tkinter.Label(dlg, text="协议：—",
            font=utils.FONT_SMALL, bg=C["bg"], fg=C["fgm"], anchor="w",
            justify="left", wraplength=520)
        self._web_tls_lbl.grid(row=8, column=0, sticky="ew", padx=utils.sp(12), pady=(0, utils.sp(10)))

        self._web_refresh()

        # 未启用 netlink → 提示「请先启动互联」
        try:
            if not nl.is_running():
                self._set_status("请先启动互联", "warning")
                try:
                    from tkinter import messagebox
                    messagebox.showinfo("网页面板", "请先启动互联", parent=dlg)
                except Exception:
                    pass
        except Exception:
            pass
        return dlg

    # _web_refresh
    def _web_refresh(self):
        """刷新对话框状态行/地址/按钮文案（全部 try/except）。"""
        try:
            nl = _nl()
            running = False
            url = ""
            try:
                running = bool(nl.is_webui_running()) if nl is not None else False
            except Exception:
                running = False
            try:
                url = (nl.webui_url() or "") if nl is not None else ""
            except Exception:
                url = ""
            try:
                if self._web_status_lbl is not None:
                    self._web_status_lbl.config(
                        text="面板状态：运行中" if running else "面板状态：已停止",
                        fg=utils.C["sc"] if running else utils.C["fgt"])
            except Exception:
                pass
            try:
                if self._web_url_var is not None:
                    self._web_url_var.set(url or "（未启用）")
            except Exception:
                pass
            try:
                if self._web_btn_toggle is not None:
                    self._web_btn_toggle.config(text="停用" if running else "启用")
            except Exception:
                pass
            try:
                if self._web_control_var is not None:
                    self._web_control_var.set(
                        bool(getattr(state, "NETLINK_WEB_CONTROL", False)))
            except Exception:
                pass
            try:
                if self._web_allow_remote_var is not None:
                    self._web_allow_remote_var.set(
                        bool(getattr(state, "NETLINK_WEB_ALLOW_REMOTE_CONTROL", False)))
            except Exception:
                pass
            try:
                if self._web_tls_lbl is not None:
                    cert = str(getattr(state, "NETLINK_TLS_CERT", "") or "")
                    key = str(getattr(state, "NETLINK_TLS_KEY", "") or "")
                    if url.startswith("https"):
                        scheme = "https"
                    elif url:
                        scheme = "http"
                    else:
                        scheme = "—"
                    has_pin = False
                    try:
                        has_pin = bool(nl.has_webui_control_pin()) if nl is not None else False
                    except Exception:
                        has_pin = False
                    ctl_txt = "已启用" if bool(getattr(
                        state, "NETLINK_WEB_CONTROL", False)) else "未启用"
                    self._web_tls_lbl.config(
                        text="协议：{} ｜ 控制：{} ｜ 控制 PIN：{} ｜ 证书：{}\n"
                             "⚠ 自签证书浏览器会提示「不安全」，需手动信任/继续".format(
                                 scheme, ctl_txt,
                                 "已设置" if has_pin else "未设置",
                                 "已配置" if (cert and key) else "未配置"))
            except Exception:
                pass
        except Exception:
            pass

    # _on_web_toggle
    def _on_web_toggle(self):
        """[启用]/[停用]：写 state + save_config + start/stop webui。"""
        try:
            nl = _nl()
            if nl is None:
                self._set_status("网页面板不可用", "warning")
                return
            running = False
            try:
                running = bool(nl.is_webui_running())
            except Exception:
                running = False
            if running:
                try:
                    state.NETLINK_WEB_ENABLED = False
                    state.save_config()
                except Exception:
                    pass
                nl.stop_webui()
                self._set_status("网页面板已停用", "info")
            else:
                if not nl.is_running():
                    self._set_status("请先启动互联", "warning")
                    try:
                        from tkinter import messagebox
                        messagebox.showinfo("网页面板", "请先启动互联",
                                            parent=self._web_dlg or self.win)
                    except Exception:
                        pass
                    return
                # 控制开启但证书不可用 → 拒绝启用（绝不回落明文，§2.1）
                try:
                    ctl_on = bool(getattr(state, "NETLINK_WEB_CONTROL", False))
                except Exception:
                    ctl_on = False
                cert = str(getattr(state, "NETLINK_TLS_CERT", "") or "")
                key = str(getattr(state, "NETLINK_TLS_KEY", "") or "")
                if ctl_on and (not cert or not key):
                    self._set_status("启用失败：控制需要 TLS，但证书未配置", "warning")
                    try:
                        from tkinter import messagebox
                        messagebox.showwarning(
                            "网页面板",
                            "已启用「有限控制」，控制强制 HTTPS：\n"
                            "请先在「设置 → 网络互联」配置证书 PEM 与私钥 PEM，"
                            "或先关闭控制。",
                            parent=self._web_dlg or self.win)
                    except Exception:
                        pass
                    return
                try:
                    state.NETLINK_WEB_ENABLED = True
                    state.save_config()
                except Exception:
                    pass
                if nl.start_webui():
                    self._set_status("网页面板已启用", "info")
                else:
                    why = ""
                    try:
                        why = str(nl.webui_last_error() or "")
                    except Exception:
                        why = ""
                    self._set_status(
                        "网页面板启用失败：{}".format(
                            why or "端口被占用或被拒绝访问"), "warning")
                    try:
                        from tkinter import messagebox
                        messagebox.showwarning(
                            "网页面板",
                            "启用失败：{}".format(
                                why or "端口可能被占用或被拒绝访问。"),
                            parent=self._web_dlg or self.win)
                    except Exception:
                        pass
        except Exception as e:
            try:
                self._set_status("网页面板操作失败：{}".format(e), "warning")
            except Exception:
                pass
        self._web_refresh()

    # _on_web_copy
    def _on_web_copy(self):
        """[复制地址]：把访问链接写入剪贴板。"""
        try:
            url = ""
            if self._web_url_var is not None:
                url = str(self._web_url_var.get() or "")
            if not url or url.startswith("（"):
                self._set_status("暂无可复制的地址", "warning")
                return
            self.win.clipboard_clear()
            self.win.clipboard_append(url)
            self._set_status("地址已复制到剪贴板", "info")
        except Exception as e:
            self._set_status("复制失败：{}".format(e), "warning")

    # _on_web_open
    def _on_web_open(self):
        """[在浏览器打开]：webbrowser.open(url)，失败只提示。"""
        try:
            nl = _nl()
            url = (nl.webui_url() or "") if nl is not None else ""
            if not url:
                self._set_status("面板未运行，无法打开", "warning")
                return
            import webbrowser
            webbrowser.open(url)
            self._set_status("已尝试在浏览器打开", "info")
        except Exception as e:
            self._set_status("打开浏览器失败：{}".format(e), "warning")

    # _on_web_rotate
    def _on_web_rotate(self):
        """[重新生成令牌]：二次确认后轮换（旧链接立即失效）。"""
        try:
            from tkinter import messagebox
            if not messagebox.askyesno(
                    "重新生成令牌",
                    "重新生成后，旧访问链接将立即失效（需重新复制新链接）。是否继续？",
                    parent=self._web_dlg or self.win):
                return
        except Exception:
            pass
        try:
            nl = _nl()
            if nl is None:
                self._set_status("网页面板不可用", "warning")
                return
            nl.rotate_webui_token()
            self._set_status("已重新生成令牌，旧链接立即失效", "info")
        except Exception as e:
            self._set_status("重新生成令牌失败：{}".format(e), "warning")
        self._web_refresh()

    # _revert_web_control
    def _revert_web_control(self):
        """回退控制开关勾选（前置条件不满足时）。"""
        try:
            if self._web_control_var is not None:
                self._web_control_var.set(False)
        except Exception:
            pass

    # _restart_webui
    def _restart_webui(self):
        """设置变化后重启面板使其生效；失败展示 webui_last_error()。"""
        try:
            nl = _nl()
            if nl is None:
                return
            running = False
            try:
                running = bool(nl.is_webui_running())
            except Exception:
                running = False
            if not running:
                self._set_status("设置已保存（面板未运行，下次启用生效）", "info")
                return
            try:
                nl.stop_webui()
            except Exception:
                pass
            ok = False
            try:
                ok = bool(nl.start_webui())
            except Exception:
                ok = False
            if ok:
                self._set_status("网页面板已重启生效", "info")
            else:
                why = ""
                try:
                    why = str(nl.webui_last_error() or "")
                except Exception:
                    why = ""
                self._set_status(
                    "面板重启失败：{}".format(why or "未知原因"), "warning")
                try:
                    from tkinter import messagebox
                    messagebox.showwarning(
                        "网页面板",
                        "重启失败：{}".format(why or "未知原因（证书/端口）"),
                        parent=self._web_dlg or self.win)
                except Exception:
                    pass
        except Exception:
            pass

    # _on_web_control_toggle
    def _on_web_control_toggle(self):
        """[启用有限控制]：需控制 PIN + 可用证书（强制 HTTPS）；联动重启面板。"""
        try:
            nl = _nl()
            if nl is None:
                self._set_status("网页面板不可用", "warning")
                return
            try:
                want = bool(self._web_control_var.get()) \
                    if self._web_control_var is not None else False
            except Exception:
                want = False
            if want:
                has_pin = False
                try:
                    has_pin = bool(nl.has_webui_control_pin())
                except Exception:
                    has_pin = False
                if not has_pin:
                    self._set_status("启用控制前请先设置控制 PIN", "warning")
                    try:
                        from tkinter import messagebox
                        messagebox.showwarning(
                            "网页面板控制",
                            "启用「有限控制」前必须设置 6 位控制 PIN"
                            "（点击「设置/重置控制 PIN」）。",
                            parent=self._web_dlg or self.win)
                    except Exception:
                        pass
                    self._revert_web_control()
                    return
                cert = str(getattr(state, "NETLINK_TLS_CERT", "") or "")
                key = str(getattr(state, "NETLINK_TLS_KEY", "") or "")
                if not cert or not key:
                    self._set_status("启用控制需要 HTTPS 证书", "warning")
                    try:
                        from tkinter import messagebox
                        messagebox.showwarning(
                            "网页面板控制",
                            "面板控制强制使用 HTTPS：请先在「设置 → 网络互联」"
                            "配置证书 PEM 与私钥 PEM。",
                            parent=self._web_dlg or self.win)
                    except Exception:
                        pass
                    self._revert_web_control()
                    return
                bind = str(getattr(state, "NETLINK_WEB_BIND", "0.0.0.0") or "")
                if bind in ("0.0.0.0", ""):
                    try:
                        from tkinter import messagebox
                        ok = messagebox.askyesno(
                            "局域网控制风险",
                            "当前监听范围为「所有网卡」。启用控制后，同网段设备可经"
                            "HTTPS + PIN 解锁运行/停止本机脚本。\n\n"
                            "默认将收窄为仅本机（127.0.0.1）；如需局域网控制，请勾选"
                            "「允许局域网控制」。\n\n是否启用控制？",
                            parent=self._web_dlg or self.win)
                    except Exception:
                        ok = True
                    if not ok:
                        self._revert_web_control()
                        return
                state.NETLINK_WEB_CONTROL = True
                state.NETLINK_WEB_TLS = True     # 联动强制 TLS
            else:
                state.NETLINK_WEB_CONTROL = False
            try:
                state.save_config()
            except Exception:
                pass
            self._restart_webui()
        except Exception as e:
            try:
                self._set_status("切换面板控制失败：{}".format(e), "warning")
            except Exception:
                pass
        self._web_refresh()

    # _on_web_allow_remote_toggle
    def _on_web_allow_remote_toggle(self):
        """[允许局域网控制]：二次确认后写 state 并重启面板。"""
        try:
            want = bool(self._web_allow_remote_var.get()) \
                if self._web_allow_remote_var is not None else False
            if want:
                try:
                    from tkinter import messagebox
                    if not messagebox.askyesno(
                            "允许局域网控制",
                            "允许局域网内其它设备在 PIN 解锁后运行/停止本机脚本，"
                            "风险显著提升。是否继续？",
                            parent=self._web_dlg or self.win):
                        if self._web_allow_remote_var is not None:
                            self._web_allow_remote_var.set(False)
                        return
                except Exception:
                    pass
            state.NETLINK_WEB_ALLOW_REMOTE_CONTROL = want
            try:
                state.save_config()
            except Exception:
                pass
            self._restart_webui()
        except Exception:
            pass
        self._web_refresh()

    # _on_web_pin_manage
    def _on_web_pin_manage(self):
        """[设置/重置控制 PIN]：设置（6 位）/ 随机重置（显示一次）/ 清除。"""
        try:
            nl = _nl()
            if nl is None:
                self._set_status("网页面板不可用", "warning")
                return
            from tkinter import messagebox, simpledialog
            parent = self._web_dlg or self.win
            has = False
            try:
                has = bool(nl.has_webui_control_pin())
            except Exception:
                has = False
            pin = simpledialog.askstring(
                "控制 PIN",
                "当前状态：{}\n\n请输入新的 6 位数字控制 PIN\n"
                "（留空并确定 → 询问随机重置 / 清除）".format(
                    "已设置" if has else "未设置"),
                parent=parent, show="*")
            if pin is None:
                return
            pin = str(pin).strip()
            if pin:
                if len(pin) != 6 or not pin.isdigit():
                    messagebox.showwarning(
                        "控制 PIN", "PIN 必须为 6 位数字。", parent=parent)
                    return
                ok = False
                try:
                    ok = bool(nl.set_webui_control_pin(pin))
                except Exception:
                    ok = False
                self._set_status("控制 PIN 已设置" if ok else "控制 PIN 设置失败",
                                 "info" if ok else "warning")
                self._web_refresh()
                return
            if messagebox.askyesno(
                    "控制 PIN",
                    "随机生成一个新的 6 位 PIN 并显示一次？\n"
                    "（选择「否」将询问是否清除现有 PIN）",
                    parent=parent):
                import random
                new = "".join(random.choice("0123456789") for _ in range(6))
                ok = False
                try:
                    ok = bool(nl.set_webui_control_pin(new))
                except Exception:
                    ok = False
                if ok:
                    messagebox.showinfo(
                        "控制 PIN",
                        "新的控制 PIN（请立即记录，仅显示一次）：\n\n{}".format(new),
                        parent=parent)
                self._set_status("控制 PIN 已重置" if ok else "控制 PIN 重置失败",
                                 "info" if ok else "warning")
            else:
                if has and messagebox.askyesno(
                        "控制 PIN",
                        "确定清除控制 PIN？清除后网页面板将无法解锁控制。",
                        parent=parent):
                    try:
                        nl.clear_webui_control_pin()
                    except Exception:
                        pass
                    self._set_status("控制 PIN 已清除", "info")
            self._web_refresh()
        except Exception as e:
            try:
                self._set_status("控制 PIN 操作失败：{}".format(e), "warning")
            except Exception:
                pass

    # _close_web_dialog
    def _close_web_dialog(self):
        """关闭网页面板对话框；幂等。"""
        dlg = self._web_dlg
        self._web_dlg = None
        self._web_status_lbl = None
        self._web_url_var = None
        self._web_btn_toggle = None
        self._web_control_var = None
        self._web_allow_remote_var = None
        self._web_pin_btn = None
        self._web_tls_lbl = None
        try:
            if dlg is not None and dlg.winfo_exists():
                dlg.destroy()
        except Exception:
            pass

    # _web_dlg_alive
    def _web_dlg_alive(self):
        try:
            return self._web_dlg is not None and bool(self._web_dlg.winfo_exists())
        except Exception:
            return False

