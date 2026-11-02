# -*- coding: utf-8 -*-
"""NetLinkWindow 方法组：audit (L1-A 拆分)。"""

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

class AuditMixin(object):
    # _on_audit
    def _on_audit(self):
        """[≡ 审计日志]：读取 node.control.audit.tail(300) 只读展示。"""
        node = self._get_node()
        audit = None
        try:
            audit = getattr(getattr(node, "control", None), "audit", None) if node else None
        except Exception:
            audit = None
        if audit is None:
            self._audit_unavailable()
            return
        lines = []
        apath = ""
        try:
            lines = audit.tail(300) or []
            apath = str(getattr(audit, "path", "") or "")
        except Exception:
            self._audit_unavailable()
            return
        self._open_audit_dialog(lines, apath)

    # _audit_unavailable
    def _audit_unavailable(self):
        """互联未启动 / 无 node / 读取失败 → 提示且不抛异常。"""
        self._set_status("审计日志不可用", "warning")
        try:
            from tkinter import messagebox
            messagebox.showinfo("审计日志不可用",
                                "当前无法读取审计日志（互联未启动或节点不可用）。",
                                parent=self.win)
        except Exception:
            pass

    # _open_audit_dialog
    def _open_audit_dialog(self, lines, apath):
        """打开（或前置）审计日志只读窗口。"""
        dlg = self._audit_dlg
        if dlg is not None:
            try:
                if dlg.winfo_exists():
                    self._audit_fill(dlg, lines, apath)
                    dlg.lift()
                    dlg.focus_force()
                    return dlg
            except Exception:
                pass
            self._audit_dlg = None
            self._audit_text = None
            self._audit_path_lbl = None
        C = utils.C
        try:
            dlg = tkinter.Toplevel(self.win)
        except Exception:
            return None
        self._audit_dlg = dlg
        try:
            dlg.title("远程操控审计日志 — ACRPA")
            try:    # 外壳: 尺寸/最小尺寸/父窗居中统一走 window_shell (§2.3 P2)
                from ui.window_shell import apply_window_chrome
                apply_window_chrome(dlg, size=(760, 520), min_size=(600, 420),
                                    parent=self.win, bg_key="bg", colors=C)
            except Exception:
                dlg.geometry("760x520")
            dlg.configure(bg=C["bg"])
            self._set_icon(dlg)
            dlg.protocol("WM_DELETE_WINDOW", self._close_audit_dialog)
        except Exception:
            pass
        try:
            self._audit_path_lbl = tkinter.Label(
                dlg, text="审计文件：{}".format(apath or "—"), font=utils.FONT_SMALL,
                bg=C["bg"], fg=C["fgm"], anchor="w")
            self._audit_path_lbl.grid(row=0, column=0, columnspan=2, sticky="ew",
                                      padx=utils.sp(10), pady=(utils.sp(8), utils.sp(2)))
            txt = tkinter.Text(dlg, font=utils.FONT_LOG, bg=C["logbg"],
                               fg=C["logfg"], relief="solid", bd=1, wrap="none",
                               state="normal")
            txt.grid(row=1, column=0, sticky="nsew", padx=(utils.sp(10), 0), pady=(0, utils.sp(6)))
            sb = ttk.Scrollbar(dlg, orient="vertical", command=txt.yview)
            sb.grid(row=1, column=1, sticky="ns", padx=(0, utils.sp(10)), pady=(0, utils.sp(6)))
            txt.configure(yscrollcommand=sb.set)
            dlg.columnconfigure(0, weight=1)
            dlg.rowconfigure(1, weight=1)
            self._audit_text = txt
            bar = tkinter.Frame(dlg, bg=C["bg"])
            bar.grid(row=2, column=0, columnspan=2, sticky="e", padx=utils.sp(10), pady=(0, utils.sp(10)))
            tkinter.Button(bar, text="刷新", font=utils.FONT_BUTTON, bg=C["bgc"],
                           fg=C["fgb"], activebackground=C["acl"], relief="flat",
                           bd=1, cursor="hand2", padx=utils.sp(10), pady=utils.sp(3),
                           command=lambda: self._audit_refresh(dlg)).pack(
                side="left", padx=(0, utils.sp(6)))
            tkinter.Button(bar, text="关闭", font=utils.FONT_BUTTON, bg=C["bgc"],
                           fg=C["fgb"], activebackground=C["acl"], relief="flat",
                           bd=1, cursor="hand2", padx=utils.sp(10), pady=utils.sp(3),
                           command=self._close_audit_dialog).pack(side="left")
        except Exception:
            pass
        self._audit_fill(dlg, lines, apath)
        return dlg

    # _audit_fill
    def _audit_fill(self, dlg, lines, apath):
        """把审计行写入只读 Text（最新在底部）。"""
        try:
            txt = self._audit_text
            if txt is None:
                return
            txt.config(state="normal")
            txt.delete("1.0", "end")
            txt.insert("end", "\n".join(str(x) for x in (lines or [])))
            if lines:
                txt.insert("end", "\n")
            txt.config(state="disabled")
            txt.see("end")
        except Exception:
            pass
        try:
            if self._audit_path_lbl is not None:
                self._audit_path_lbl.config(text="审计文件：{}".format(apath or "—"))
        except Exception:
            pass

    # _audit_refresh
    def _audit_refresh(self, dlg):
        """[刷新]：重新读取审计尾部。"""
        try:
            if dlg is None or not dlg.winfo_exists():
                return
        except Exception:
            return
        audit = None
        try:
            node = self._get_node()
            audit = getattr(getattr(node, "control", None), "audit", None) if node else None
        except Exception:
            audit = None
        lines, apath = [], ""
        if audit is not None:
            try:
                lines = audit.tail(300) or []
                apath = str(getattr(audit, "path", "") or "")
            except Exception:
                lines = []
        self._audit_fill(dlg, lines, apath)

    # _close_audit_dialog
    def _close_audit_dialog(self):
        """销毁审计日志窗口（幂等）。"""
        dlg = self._audit_dlg
        self._audit_dlg = None
        self._audit_text = None
        self._audit_path_lbl = None
        try:
            if dlg is not None and dlg.winfo_exists():
                dlg.destroy()
        except Exception:
            pass

