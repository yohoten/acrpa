# -*- coding: utf-8 -*-
"""NetLinkWindow 方法组：panels (L1-A 拆分)。"""

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

class PanelsMixin(object):
    # _refresh_table
    def _refresh_table(self):
        for nid, info in list(self._peers.items()):
            st = self._states.get(nid) or {}
            values = (self._dev_text(info), self._state_text(info, st),
                      st.get("script") or "—", self._progress_text(st),
                      "—", self._perm_text(nid))
            # 未认证设备整行置灰 (Treeview 无法单列着色, 以行灰度表达)
            tag = ("unauth" if not self._authed.get(str(nid or ""))
                   else self._row_tag(info, st))
            item = self._items.get(nid)
            try:
                if item is None:
                    self._items[nid] = self.tv.insert("", "end", values=values,
                                                      tags=(tag,))
                elif self.tv.exists(item):
                    self.tv.item(item, values=values, tags=(tag,))
            except Exception:
                pass

    # _perm_text
    def _perm_text(self, nid):
        """设备表权限列文本: 未运行→—; 未认证→未认证; 已认证→中文标签。"""
        if not self._running:
            return "—"
        perm = self._authed.get(str(nid or ""))
        if perm is None:
            return "未认证"
        return _perm_labels().get(perm, "仅观察")

    # _refresh_detail
    def _refresh_detail(self):
        sel_ids = self._selected_node_ids()
        self._update_pair_btn()
        self._refresh_sched_label()
        if len(sel_ids) > 1:
            try:
                self.pb_detail.config(maximum=1, value=0)
                self.lbl_detail.config(text="已选中 {} 台设备".format(len(sel_ids)))
            except Exception:
                pass
            return
        st = self._states.get(self._sel) if self._sel else None
        if not st:
            try:
                self.pb_detail.config(maximum=1, value=0)
                self.lbl_detail.config(text="未选择设备")
            except Exception:
                pass
            return
        try:
            total = int(st.get("total_rows") or 0)
            row = int(st.get("row") or 0)
            maximum = total if total > 0 else 1
            self.pb_detail.config(maximum=maximum, value=min(row, maximum))
            script = st.get("script") or st.get("node") or "—"
            try:
                elapsed = int(st.get("elapsed") or 0)
            except Exception:
                elapsed = 0
            self.lbl_detail.config(text="{} · 已运行 {:02d}:{:02d}".format(
                script, elapsed // 60, elapsed % 60))
        except Exception:
            pass

    # _dev_text
    @staticmethod
    def _dev_text(info):
        name = info.get("name") or ""
        host = info.get("host") or ""
        port = info.get("port") or ""
        addr = "{}:{}".format(host, port) if host else str(port or "")
        if name and addr:
            return "{}({})".format(name, addr)
        return name or addr or "—"

    # _state_text
    @staticmethod
    def _state_text(info, st):
        if not info.get("online", True):
            return "○ 离线"
        if st:
            if st.get("recording"):
                return "● 录制中"
            if st.get("running"):
                return "⏸ 暂停" if st.get("paused") else "● 运行"
        return "○ 空闲"

    # _row_tag
    @staticmethod
    def _row_tag(info, st):
        if not info.get("online", True):
            return "off"
        if st:
            if st.get("recording"):
                return "run"
            if st.get("running"):
                return "pause" if st.get("paused") else "run"
        return "idle"

    # _progress_text
    @staticmethod
    def _progress_text(st):
        try:
            total = int(st.get("total_rows") or 0)
        except Exception:
            total = 0
        if total <= 0:
            return "—"
        try:
            row = int(st.get("row") or 0)
            loop = int(st.get("loop") or 0)
            total_loops = int(st.get("total_loops") or 0)
        except Exception:
            row = loop = total_loops = 0
        return "{}/{} 行 · 循环 {}/{}".format(row, total, loop, total_loops)

    # _update_pair_btn
    def _update_pair_btn(self):
        """按选中设备是否「运行中 + 未认证 + 有地址」启用配对按钮。"""
        enable = False
        try:
            if self._running and self._sel and self._sel not in self._authed:
                info = self._peers.get(self._sel) or {}
                if info.get("host") and info.get("port"):
                    enable = True
        except Exception:
            enable = False
        try:
            self.btn_pair_peer.config(state=("normal" if enable else "disabled"))
        except Exception:
            pass

    # _fmt_time
    @staticmethod
    def _fmt_time(ts):
        try:
            ts = int(ts)
            if ts > 0:
                return time.strftime("%H:%M:%S", time.localtime(ts))
        except Exception:
            pass
        return "—"

    # _refresh_paired
    def _refresh_paired(self):
        """低频刷新已配对区: 仅数据签名变化时重建, 避免闪烁。"""
        node = self._get_node()
        peers = []
        if node is not None:
            try:
                peers = node.list_peers() or []
            except Exception:
                peers = []
        labels = _perm_labels()
        sig = []
        for p in peers:
            if not isinstance(p, dict) or not p.get("fingerprint"):
                continue
            try:
                last = int(p.get("last_seen") or 0)
            except Exception:
                last = 0
            sig.append((str(p.get("fingerprint") or ""),
                        str(p.get("node_id") or ""),
                        str(p.get("name") or ""),
                        str(p.get("perm") or ""), last))
        if sig != self._paired_sig:
            self._paired_sig = sig
            try:
                for iid in self.tv_paired.get_children():
                    self.tv_paired.delete(iid)
            except Exception:
                pass
            self._paired_items = {}
            for fp, nid, name, perm, last in sig:
                try:
                    iid = self.tv_paired.insert("", "end", values=(
                        name or nid or "—", fp, labels.get(perm, "仅观察"),
                        self._fmt_time(last)))
                    self._paired_items[fp] = iid
                except Exception:
                    pass
        try:
            if self.tv_paired.get_children():
                self.lbl_paired_empty.grid_remove()
            else:
                self.lbl_paired_empty.grid()
        except Exception:
            pass
        self._set_paired_enabled(bool(self._running and node is not None))
        self._restore_paired_sel()

    # _restore_paired_sel
    def _restore_paired_sel(self):
        """重建后恢复已配对区选中态。"""
        fp = self._sel_fp
        if fp and fp in self._paired_items:
            try:
                cur = self.tv_paired.selection()
                if cur != (self._paired_items[fp],):
                    self.tv_paired.selection_set(self._paired_items[fp])
            except Exception:
                pass
        else:
            self._sel_fp = self._sel_fp_from_tree()

    # _set_paired_enabled
    def _set_paired_enabled(self, enabled):
        """按运行态/选中态启用或禁用已配对区控件。"""
        can_act = bool(enabled and self._sel_fp and self._sel_fp in self._paired_items)
        try:
            self.cmb_perm.config(state=("readonly" if enabled else "disabled"))
        except Exception:
            pass
        for b in (self.btn_perm_apply, self.btn_peer_remove):
            try:
                b.config(state=("normal" if can_act else "disabled"))
            except Exception:
                pass

    # _sel_fp_from_tree
    def _sel_fp_from_tree(self):
        """当前已配对区选中的 fingerprint; 无返回 None。"""
        try:
            sel = self.tv_paired.selection()
        except Exception:
            sel = ()
        if not sel:
            return None
        iid = sel[0]
        for fp, it in self._paired_items.items():
            if it == iid:
                return fp
        return None

    # _on_paired_select
    def _on_paired_select(self, event=None):
        """已配对区选中行 → 同步权限下拉框。"""
        self._sel_fp = self._sel_fp_from_tree()
        if self._sel_fp:
            perm = "observe"
            node = self._get_node()
            if node is not None:
                try:
                    rec = node.peer_store.get(self._sel_fp) or {}
                    perm = rec.get("perm") or "observe"
                except Exception:
                    perm = "observe"
            try:
                self.var_perm.set(_perm_labels().get(perm, "仅观察"))
            except Exception:
                pass
        self._set_paired_enabled(bool(self._running and self._get_node() is not None))

    # _on_perm_apply
    def _on_perm_apply(self):
        """应用所选权限 (即时生效, 无需重启)。"""
        fp = self._sel_fp or self._sel_fp_from_tree()
        if not fp:
            self._set_status("请先在「已配对设备」中选择一行", "warning")
            return
        code = _perm_code(self.var_perm.get())
        if not code:
            self._set_status("权限取值非法", "warning")
            return
        node = self._get_node()
        if node is None:
            self._set_status("请先启动互联", "warning")
            return
        ok = False
        try:
            ok = bool(node.set_peer_perm(fp, code))
        except Exception as e:
            try:
                log1("设置权限失败: {}".format(e), "warning")
            except Exception:
                pass
        if ok:
            self._set_status("已更新权限：{} → {}".format(
                fp[:8], self.var_perm.get()), "info")
        else:
            self._set_status("权限更新失败（条目可能已被移除）", "warning")
        self._refresh_paired()

    # _on_peer_remove
    def _on_peer_remove(self):
        """移除已配对设备 (二次确认后调用 node.remove_peer)。"""
        fp = self._sel_fp or self._sel_fp_from_tree()
        if not fp:
            self._set_status("请先在「已配对设备」中选择一行", "warning")
            return
        name = ""
        node = self._get_node()
        if node is not None:
            try:
                name = (node.peer_store.get(fp) or {}).get("name") or ""
            except Exception:
                name = ""
        confirmed = True
        try:
            from tkinter import messagebox
            confirmed = bool(messagebox.askyesno(
                "移除已配对设备",
                "确定移除设备「{}」({}) 的配对与权限？".format(name or "—", fp[:8]),
                parent=self.win))
        except Exception:
            confirmed = True
        if not confirmed:
            return
        node = self._get_node()
        ok = False
        if node is not None:
            try:
                ok = bool(node.remove_peer(fp))
            except Exception:
                ok = False
        self._sel_fp = None
        if ok:
            self._set_status("已移除设备：{}".format(fp[:8]), "info")
        else:
            self._set_status("移除失败（条目不存在）", "warning")
        self._paired_sig = None
        self._refresh_paired()

