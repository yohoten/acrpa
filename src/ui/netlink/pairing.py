# -*- coding: utf-8 -*-
"""NetLinkWindow 方法组：pairing (L1-A 拆分)。"""

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

class PairingMixin(object):
    # _on_auth
    def _on_auth(self, payload):
        """TOPIC_AUTH: stage=result 更新认证态；stage=perm 更新权限列与按钮可用性。"""
        stage = str(payload.get("stage") or "")
        if stage == "perm":
            nid = str(payload.get("node_id") or "")
            perm = str(payload.get("perm") or "observe")
            labels = _perm_labels()
            if perm not in labels:
                perm = "observe"
            if bool(payload.get("ok")) and nid:
                self._authed[nid] = perm
                self._status_msg = "权限已更新：{}（{}）".format(
                    str(payload.get("name") or nid), labels.get(perm, "仅观察"))
                self._status_level = "info"
            return
        if stage != "result":
            return
        nid = str(payload.get("node_id") or "")
        name = str(payload.get("name") or "") or nid or "设备"
        labels = _perm_labels()
        if bool(payload.get("ok")):
            perm = str(payload.get("perm") or "observe")
            if perm not in labels:
                perm = "observe"
            if nid:
                self._authed[nid] = perm
            self._status_msg = "配对成功：{}（{}）".format(name, labels.get(perm, "仅观察"))
            self._status_level = "info"
        else:
            reason = str(payload.get("reason") or "")
            self._status_msg = "配对失败：{} — {}".format(name, reason)
            self._status_level = "warning"
        try:
            log1("[NetLink] {}".format(self._status_msg),
                 "warning" if self._status_level != "info" else "info")
        except Exception:
            pass

    # _on_pair_code
    def _on_pair_code(self):
        """顶部「配对码」按钮: 未启动则提示, 否则打开配对码对话框。"""
        if self._get_node() is None:
            self._set_status("请先启动互联", "warning")
            return
        self._open_pair_dialog()

    # _open_pair_dialog
    def _open_pair_dialog(self):
        """打开/前置配对码对话框 (不改动配对窗口状态)。"""
        node = self._get_node()
        if node is None:
            self._set_status("请先启动互联", "warning")
            return None
        dlg = self._pair_dlg
        if dlg is not None:
            try:
                if dlg.winfo_exists():
                    dlg.lift()
                    dlg.focus_force()
                    return dlg
            except Exception:
                pass
            self._pair_dlg = None
        C = utils.C
        try:
            dlg = tkinter.Toplevel(self.win)
        except Exception:
            return None
        self._pair_dlg = dlg
        try:
            dlg.title("配对码 — ACRPA")
            dlg.configure(bg=C["bg"])
            dlg.resizable(False, False)
            dlg.transient(self.win)
            self._set_icon(dlg)
            dlg.protocol("WM_DELETE_WINDOW", self._close_pair_dialog)
        except Exception:
            pass
        try:
            tkinter.Label(dlg,
                          text="其它设备在本窗口输入此配对码即可连接（默认获得「仅观察」权限）",
                          font=utils.FONT_SMALL, bg=C["bg"], fg=C["fgm"],
                          wraplength=320, justify="left").pack(
                padx=utils.sp(14), pady=(utils.sp(12), utils.sp(4)), anchor="w")
            lbl_pin = tkinter.Label(dlg, text="—", font=utils.FONT_PIN,
                                    bg=C["bg"], fg=C["ac"])
            lbl_pin.pack(padx=utils.sp(14), pady=(utils.sp(2), utils.sp(2)))
            lbl_left = tkinter.Label(dlg, text="剩余 --:--", font=utils.FONT_BODY,
                                     bg=C["bg"], fg=C["fgm"])
            lbl_left.pack(padx=utils.sp(14), pady=(0, utils.sp(6)))
            btns = tkinter.Frame(dlg, bg=C["bg"])
            btns.pack(padx=utils.sp(14), pady=(0, utils.sp(12)))
            tkinter.Button(btns, text="生成/重新生成", font=utils.FONT_BUTTON,
                           bg=C["ac"], fg=utils.text_on(C["ac"]),
                           activebackground=C["ach"],
                           activeforeground=utils.text_on(C["ach"]), relief="flat", bd=1,
                           cursor="hand2", padx=utils.sp(10), pady=utils.sp(3),
                           command=lambda: self._pair_generate(lbl_pin, lbl_left)).pack(
                side="left", padx=(0, utils.sp(6)))
            tkinter.Button(btns, text="关闭配对", font=utils.FONT_BUTTON, bg=C["bgc"],
                           fg=C["fgb"], activebackground=C["acl"], relief="flat",
                           bd=1, cursor="hand2", padx=utils.sp(10), pady=utils.sp(3),
                           command=lambda: self._pair_close(lbl_pin, lbl_left)).pack(
                side="left", padx=(0, utils.sp(6)))
            tkinter.Button(btns, text="关闭窗口", font=utils.FONT_BUTTON, bg=C["bgc"],
                           fg=C["fgb"], activebackground=C["acl"], relief="flat",
                           bd=1, cursor="hand2", padx=utils.sp(10), pady=utils.sp(3),
                           command=self._close_pair_dialog).pack(side="left")
            self._pair_tick(lbl_pin, lbl_left)
        except Exception:
            pass
        return dlg

    # _pair_tick
    def _pair_tick(self, lbl_pin, lbl_left):
        """刷新配对码显示并每 500ms 重排下一次 (对话框存活时)。"""
        self._pair_refresh(lbl_pin, lbl_left)
        dlg = self._pair_dlg
        try:
            if dlg is not None and dlg.winfo_exists():
                self._pair_dlg_after = dlg.after(
                    500, lambda: self._pair_tick(lbl_pin, lbl_left))
        except Exception:
            self._pair_dlg_after = None

    # _pair_refresh
    def _pair_refresh(self, lbl_pin, lbl_left):
        """按 node.pair_window_status() 刷新 PIN 大字与倒计时。"""
        st = None
        node = self._get_node()
        if node is not None:
            try:
                st = node.pair_window_status()
            except Exception:
                st = None
        active = bool(st and st.get("active"))
        pin = str((st or {}).get("pin") or "")
        C = utils.C
        try:
            if active and pin:
                lbl_pin.config(text=" ".join(pin), fg=C["ac"])
                left = int(st.get("expires_in") or 0)
                left = left if left > 0 else 0
                lbl_left.config(text="剩余 {:02d}:{:02d}".format(
                    left // 60, left % 60), fg=C["fgm"])
            else:
                lbl_pin.config(text="—", fg=C["fgm"])
                lbl_left.config(text="已过期，请重新生成", fg=C["err"])
        except Exception:
            pass

    # _pair_generate
    def _pair_generate(self, lbl_pin, lbl_left):
        """生成/重新生成配对码。"""
        node = self._get_node()
        if node is None:
            self._set_status("请先启动互联", "warning")
        else:
            try:
                if node.open_pair_window():
                    self._set_status("已生成新的配对码", "info")
            except Exception as e:
                self._set_status("生成配对码失败：{}".format(e), "warning")
        self._pair_refresh(lbl_pin, lbl_left)

    # _pair_close
    def _pair_close(self, lbl_pin, lbl_left):
        """关闭配对窗口 (不销毁对话框)。"""
        node = self._get_node()
        if node is not None:
            try:
                node.close_pair_window()
            except Exception:
                pass
        self._set_status("已关闭配对窗口", "info")
        self._pair_refresh(lbl_pin, lbl_left)

    # _close_pair_dialog
    def _close_pair_dialog(self):
        """销毁配对码对话框 (不影响配对窗口状态)。"""
        dlg = self._pair_dlg
        self._pair_dlg = None
        after_id = self._pair_dlg_after
        self._pair_dlg_after = None
        try:
            if dlg is not None and after_id is not None:
                dlg.after_cancel(after_id)
        except Exception:
            pass
        try:
            if dlg is not None and dlg.winfo_exists():
                dlg.destroy()
        except Exception:
            pass

    # _on_pair_peer
    def _on_pair_peer(self):
        """对选中设备发起配对: 输入 6 位 PIN → node.register_target。"""
        node = self._get_node()
        if node is None:
            self._set_status("请先启动互联", "warning")
            return
        info = self._peers.get(self._sel) or {}
        host = str(info.get("host") or "")
        try:
            port = int(info.get("port") or 0)
        except Exception:
            port = 0
        if not host or port <= 0:
            self._set_status("该设备缺少有效地址，无法配对", "warning")
            return
        pin = None
        try:
            from tkinter import simpledialog
            pin = simpledialog.askstring(
                "输入配对码", "请输入对方设备「配对码」窗口中显示的 6 位数字：",
                parent=self.win)
        except Exception:
            pin = None
        if pin is None:
            return
        pin = str(pin).strip()
        if not (len(pin) == 6 and pin.isdigit()):
            self._set_status("配对码为 6 位数字", "warning")
            return
        try:
            node.register_target(host, port, pin)
        except Exception as e:
            self._set_status("发起配对失败：{}".format(e), "warning")
            return
        self._set_status("已发起配对…（等待对方确认）", "info")

