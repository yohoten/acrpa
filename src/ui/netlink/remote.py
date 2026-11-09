# -*- coding: utf-8 -*-
"""NetLinkWindow 方法组：remote (L1-A 拆分)。"""

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

class RemoteMixin(object):
    # _control_state
    def _control_state(self):
        """计算操控按钮可用性与原因（仅主线程调用）。

        返回 dict: run_ok / pause_ok / stop_ok / base / paused / running。
        base 为「不可用」时的统一原因（互联未启动 / 未选择设备 / 未认证 / 权限不足）。
        """
        nid = self._sel
        st = (self._states.get(nid) or {}) if nid else {}
        dev_running = bool(st.get("running"))
        dev_paused = bool(st.get("paused"))
        out = {"run_ok": False, "pause_ok": False, "stop_ok": False,
               "base": "", "paused": dev_paused, "running": dev_running}
        if not self._running:
            out["base"] = "互联未启动"
        elif not nid:
            out["base"] = "未选择设备"
        else:
            perm = self._authed.get(str(nid))
            if perm is None:
                out["base"] = "未认证"
            elif not _perm_ok(perm):
                out["base"] = "权限不足（当前：{}）".format(
                    _perm_labels().get(perm, "仅观察"))
            else:
                out["run_ok"] = True
                out["pause_ok"] = dev_running
                out["stop_ok"] = bool(dev_running or dev_paused)
        return out

    # _action_reason
    def _action_reason(self, kind):
        """某操控按钮不可用时的悬停原因；可用时返回空串（不显示 tooltip）。"""
        s = self._control_state()
        if s.get(kind + "_ok"):
            return ""
        if s.get("base"):
            return s["base"]
        if kind in ("pause", "stop"):
            return "设备未运行"
        return ""

    # _tip_run
    def _tip_run(self):
        return self._action_reason("run")

    # _tip_pause
    def _tip_pause(self):
        return self._action_reason("pause")

    # _tip_stop
    def _tip_stop(self):
        return self._action_reason("stop")

    # _update_control_buttons
    def _update_control_buttons(self):
        """按 _control_state() 刷新四个操控按钮的启用态与暂停按钮文案。"""
        if not self.alive():
            return
        s = self._control_state()
        try:
            self.btn_run.config(state=("normal" if s["run_ok"] else "disabled"))
        except Exception:
            pass
        try:
            self.btn_pause.config(text=("▶ 恢复" if s.get("paused") else "⏸ 暂停"),
                                  state=("normal" if s["pause_ok"] else "disabled"))
        except Exception:
            pass
        try:
            self.btn_stop.config(state=("normal" if s["stop_ok"] else "disabled"))
        except Exception:
            pass
        # Phase3-2: [推脚本] 按「选中集合」门控（多选批量）；[▭ 远端脚本] 仍按主选中
        s3 = self._script_state()
        try:
            self.btn_remote_scripts.config(
                state=("normal" if s3[0] else "disabled"))
        except Exception:
            pass
        try:
            self.btn_script.config(
                state=("normal" if self._batch_script_state()[0] else "disabled"))
        except Exception:
            pass
        # Phase4-1: [◎ 截图]（需「允许操控」权限；防连点期间禁用）
        try:
            self.btn_shot.config(
                state=("normal" if self._shot_state()[0] else "disabled"))
        except Exception:
            pass

    # _send_cmd
    def _send_cmd(self, nid, kind):
        """按 kind（run/pause/resume/stop）向目标设备下发指令；返回 bool。"""
        ct = _cmd_types()
        if ct is None or kind not in ct:
            self._set_status("指令模块不可用", "warning")
            return False
        nl = _nl()
        ok = False
        try:
            ok = bool(nl.send_command(nid, ct[kind]))
        except Exception as e:
            try:
                log1("NetLink 下发指令失败: {}".format(e), "warning")
            except Exception:
                pass
            ok = False
        if ok:
            self._set_status("已下发 {} 指令…".format(_CMD_VERB.get(kind, kind)), "info")
        else:
            self._set_status("指令下发失败：目标连接不可用", "warning")
        return ok

    # _on_remote_run
    def _on_remote_run(self):
        """[▶ 远程运行]：二次确认后下发 CMD_RUN（在被控端运行其已加载脚本）。"""
        nid = self._sel
        if self._get_node() is None or not nid:
            self._set_status("请先选择已认证设备", "warning")
            return
        name = (self._peers.get(nid) or {}).get("name") or nid
        if not self._confirm_action(
                "远程运行",
                "将在被控端「{}」启动其当前已加载的脚本。\n确定下发【远程运行】指令？".format(name)):
            return
        self._send_cmd(nid, "run")

    # _on_remote_pause
    def _on_remote_pause(self):
        """[⏸ 暂停]/[▶ 恢复]：按选中设备 paused 状态切换发送类型。"""
        nid = self._sel
        if not nid:
            return
        st = self._states.get(nid) or {}
        self._send_cmd(nid, ("resume" if st.get("paused") else "pause"))

    # _on_remote_stop
    def _on_remote_stop(self):
        """[⏹ 停止]：二次确认后下发 CMD_STOP。"""
        nid = self._sel
        if not nid:
            return
        name = (self._peers.get(nid) or {}).get("name") or nid
        if not self._confirm_action(
                "远程停止",
                "将停止被控端「{}」当前正在运行的脚本。\n确定下发【远程停止】指令？".format(name)):
            return
        self._send_cmd(nid, "stop")

    # _on_cmd_result
    def _on_cmd_result(self, payload):
        """TOPIC_CMD_RESULT：登记/更新「远程指令」列表一行 + 底部摘要。"""
        if not isinstance(payload, dict):
            return
        data = payload.get("data") or {}
        if not isinstance(data, dict):
            data = {}
        t = str(payload.get("t") or "")
        nid = str(payload.get("node_id") or "")
        cmd = str(data.get("cmd") or "")
        status = str(data.get("status") or "")
        # 只展示真正的操控指令回执（SUBSCRIBE 等 ACK 不进入「远程指令」列表）
        if not cmd.startswith("CMD_"):
            return
        ct = _cmd_types()
        is_err = (t == "CMD_ERR") or (ct is not None and t == ct.get("err"))
        if is_err:
            slabel = "失败"
            detail = str(data.get("reason") or data.get("detail") or "")
            textv, tag, terminal = "失败：{}".format(detail), "cmdfail", True
        elif status == "accepted":
            slabel = "已受理"
            textv, tag, terminal = "已受理", "cmdgray", False
        elif status == "done":
            slabel = "成功"
            detail = str(data.get("detail") or "")
            if len(detail) > 120:
                detail = detail[:120] + "…"
            textv, tag, terminal = ("成功" + (" · " + detail if detail else "")), "cmdok", True
        elif status == "failed":
            slabel = "失败"
            detail = str(data.get("detail") or "")
            textv, tag, terminal = "失败：{}".format(detail), "cmdfail", True
        else:
            slabel = str(status or "完成")
            textv, tag, terminal = (str(data.get("detail") or "") or slabel), "cmdgray", True
        dev = (self._peers.get(nid) or {}).get("name") or nid or "—"
        ts = time.strftime("%H:%M:%S")
        self._cmd_upsert(nid, cmd, ts, dev, textv, tag, terminal)
        self._cmd_summary = "{} {} {}".format(dev, _CMD_LABELS.get(cmd, cmd), slabel)

    # _cmd_upsert
    def _cmd_upsert(self, nid, cmd, ts, dev, textv, tag, terminal):
        """同一设备+指令：accepted 与后续终态更新同一行；否则新增（最新在顶部）。"""
        try:
            row = None
            for r in reversed(self._cmd_rows):
                if (r["node_id"] == nid and r["cmd"] == cmd
                        and not r["terminal"]):
                    row = r
                    break
            label = _CMD_LABELS.get(cmd, cmd)
            if row is not None:
                self.tv_cmd.item(row["iid"], values=(ts, dev, label, textv),
                                 tags=(tag,))
                row["terminal"] = terminal
                row["status"] = textv
                return
            iid = self.tv_cmd.insert("", 0, values=(ts, dev, label, textv),
                                     tags=(tag,))
            self._cmd_rows.append({"node_id": nid, "cmd": cmd, "iid": iid,
                                   "terminal": bool(terminal), "status": textv})
            try:
                self.lbl_cmd_empty.grid_remove()
            except Exception:
                pass
            try:
                while len(self.tv_cmd.get_children()) > 100:
                    oldest = self.tv_cmd.get_children()[-1]
                    self.tv_cmd.delete(oldest)
                    self._cmd_rows = [r for r in self._cmd_rows
                                      if r["iid"] != oldest]
            except Exception:
                pass
        except Exception:
            pass

    # _script_state
    def _script_state(self):
        """[推脚本]/[▭ 远端脚本] 可用性判定；返回 (ok, reason)。

        reason 仅在不可用时非空（互联未启动 / 未选择设备 / 未认证 /
        需「允许接收脚本」权限（当前：<中文权限名>））。
        """
        nid = self._sel
        if not self._running:
            return (False, "互联未启动")
        if not nid:
            return (False, "未选择设备")
        perm = self._authed.get(str(nid))
        if perm is None:
            return (False, "未认证")
        if not _perm_script(perm):
            return (False, "需「允许接收脚本」权限（当前：{}）".format(
                _perm_labels().get(perm, "仅观察")))
        return (True, "")

    # _tip_script
    def _tip_script(self):
        """[推脚本]/[▭ 远端脚本] 动态悬停提示（可用时返回空串）。"""
        return self._script_state()[1]

    # _script_peers
    def _script_peers(self):
        """选中集合中「可推送脚本」的 node_id（运行中 + 已认证 + perm==script）。"""
        out = []
        if not self._running:
            return out
        for nid in self._selected_node_ids():
            perm = self._authed.get(str(nid))
            if perm is None:
                continue
            if _perm_script(perm):
                out.append(nid)
        return out

    # _batch_script_state
    def _batch_script_state(self):
        """[推脚本] 批量门控：返回 (ok, reason, peers)。至少一台可推送即放行。"""
        if not self._running:
            return (False, "互联未启动", [])
        peers = self._script_peers()
        if not peers:
            if not self._selected_node_ids():
                return (False, "未选择设备", [])
            return (False, "选中设备均无「允许接收脚本」权限", [])
        return (True, "", peers)

    # _ask_open_script
    def _ask_open_script(self):
        """弹出文件选择框；取消返回 None。测试可替换本方法注入路径。"""
        try:
            from tkinter import filedialog
            path = filedialog.askopenfilename(
                parent=self.win, title="选择要推送的脚本",
                filetypes=[("脚本文件", "*.xls *.xlsx *.json *.acrpas")],
                initialdir=_script_root() or None)
            return str(path or "") or None
        except Exception:
            return None

    # _ask_push_params
    def _ask_push_params(self, local_path, base):
        """自绘「推送脚本」小对话框：远端文件名 + 是否立即运行。

        返回 (remote_name, run) 或 None（用户取消）。测试可替换本方法以注入结果。
        """
        C = utils.C
        try:
            dlg = tkinter.Toplevel(self.win)
        except Exception:
            return None
        result = {"val": None}
        try:
            dlg.title("推送脚本 — ACRPA")
            dlg.configure(bg=C["bg"])
            dlg.resizable(False, False)
            dlg.transient(self.win)
            self._set_icon(dlg)
        except Exception:
            pass
        var_name = tkinter.StringVar(value=base)
        var_run = tkinter.BooleanVar(value=True)

        def _on_ok():
            nm = str(var_name.get() or "").strip()
            tf = _transfer()
            valid = bool(nm and tf is not None and tf.is_valid_script_name(nm)
                         and not nm.startswith("received/"))
            if not valid:
                self._set_status("远端文件名非法（不得含路径分隔符或以 received/ 开头）",
                                 "warning")
                return
            result["val"] = (nm, bool(var_run.get()))
            try:
                dlg.destroy()
            except Exception:
                pass

        def _on_cancel():
            result["val"] = None
            try:
                dlg.destroy()
            except Exception:
                pass

        try:
            tkinter.Label(dlg, text="源文件：{}".format(os.path.basename(local_path)),
                          font=utils.FONT_SMALL, bg=C["bg"], fg=C["fgm"],
                          wraplength=380, justify="left", anchor="w").pack(
                padx=utils.sp(14), pady=(utils.sp(12), utils.sp(6)), anchor="w")
            row = tkinter.Frame(dlg, bg=C["bg"])
            row.pack(fill="x", padx=utils.sp(14), pady=(0, utils.sp(6)))
            tkinter.Label(row, text="远端文件名", font=utils.FONT_SMALL, bg=C["bg"],
                          fg=C["fgm"]).pack(side="left", padx=(0, utils.sp(6)))
            ent = tkinter.Entry(row, textvariable=var_name, width=30,
                                font=utils.FONT_BODY, relief="solid", bd=1,
                                bg=C["ebg"], fg=C["fgb"])
            ent.pack(side="left")
            tkinter.Label(dlg, text="该文件将保存到被控端脚本目录的 received/ 子目录下",
                          font=utils.FONT_SMALL, bg=C["bg"], fg=C["fgm"],
                          anchor="w").pack(padx=utils.sp(14), pady=(0, utils.sp(6)), anchor="w")
            tkinter.Checkbutton(dlg, text="传输完成后立即运行", variable=var_run).pack(
                padx=utils.sp(14), pady=(0, utils.sp(8)), anchor="w")
            btns = tkinter.Frame(dlg, bg=C["bg"])
            btns.pack(padx=utils.sp(14), pady=(0, utils.sp(12)), anchor="e")
            tkinter.Button(btns, text="[开始传输]", font=utils.FONT_BUTTON,
                           bg=C["ac"], fg=utils.text_on(C["ac"]),
                           activebackground=C["ach"],
                           activeforeground=utils.text_on(C["ach"]), relief="flat", bd=1,
                           cursor="hand2", padx=utils.sp(10), pady=utils.sp(3),
                           command=_on_ok).pack(side="left", padx=(0, utils.sp(6)))
            tkinter.Button(btns, text="[取消]", font=utils.FONT_BUTTON, bg=C["bgc"],
                           fg=C["fgb"], activebackground=C["acl"], relief="flat",
                           bd=1, cursor="hand2", padx=utils.sp(10), pady=utils.sp(3),
                           command=_on_cancel).pack(side="left")
            ent.focus_set()
            dlg.protocol("WM_DELETE_WINDOW", _on_cancel)
            try:
                dlg.grab_set()
            except Exception:
                pass
            self.win.wait_window(dlg)
        except Exception:
            try:
                dlg.destroy()
            except Exception:
                pass
            return None
        return result["val"]

    # _on_push_script
    def _on_push_script(self):
        """[推脚本]：选文件 → 校验 → 参数对话框 →（多选时确认）门面批量/单个下发。

        Phase3-2：设备表多选后本按钮即「批量下发」；选中集合里权限不足的设备
        会被跳过（不阻止操作）。单选流程与本轮之前完全一致。
        """
        ok, reason, peers = self._batch_script_state()
        if not ok:
            self._set_status(reason or "无法推送脚本", "warning")
            return
        tf = _transfer()
        if tf is None:
            self._set_status("脚本分发模块不可用", "warning")
            return
        local_path = self._ask_open_script()
        if not local_path:
            return
        base = os.path.basename(str(local_path))
        ext = os.path.splitext(base)[1].lower()
        if ext not in tf.ALLOWED_EXT:
            self._warn("不支持的扩展名（仅 .xls / .xlsx / .json / .acrpas）")
            return
        if not os.path.isfile(local_path):
            self._warn("文件不存在")
            return
        try:
            size = int(os.path.getsize(local_path))
        except Exception:
            size = 0
        if size > tf.MAX_SCRIPT_BYTES:
            self._warn("文件过大（上限 32 MB）")
            return
        params = self._ask_push_params(local_path, base)
        if not params:
            return
        remote_name, run = params
        # 多选批量：额外一次显式确认（单选沿用既有对话框即确认的交互）
        if len(peers) > 1:
            if not self._confirm_action(
                    "批量推送脚本",
                    "将向 {} 台设备推送「{}」。\n确定开始批量传输？".format(
                        len(peers), remote_name)):
                return
        nl = _nl()
        res = {"ok": {}, "skip": {}}
        try:
            res = nl.push_script_many(peers, local_path, remote_name, run) or res
        except Exception as e:
            try:
                log1("NetLink 批量推送脚本失败: {}".format(e), "warning")
            except Exception:
                pass
            res = {"ok": {}, "skip": {}}
        ok_map = res.get("ok") or {}
        skip_map = res.get("skip") or {}
        for peer, tid in ok_map.items():
            dev = (self._peers.get(peer) or {}).get("name") or peer or "—"
            self._xfer_upsert_row(str(tid), dev, remote_name, "进行中", "xfer_run")
        # 被跳过的设备：在「传输」列表各生成一行 失败：<原因>（红）
        for peer, why in skip_map.items():
            dev = (self._peers.get(peer) or {}).get("name") or peer or "—"
            self._xfer_upsert_row(
                "skip::{}::{}".format(peer, remote_name), dev, remote_name,
                "失败：{}".format(why or "未知原因"), "xfer_fail", "—")
        parts = []
        if ok_map:
            parts.append("开始推送 {} 台".format(len(ok_map)))
        if skip_map:
            skipped = "；".join(
                "{}（{}）".format(
                    (self._peers.get(p) or {}).get("name") or p,
                    skip_map[p] or "未知原因")
                for p in skip_map)
            parts.append("跳过 {} 台：{}".format(len(skip_map), skipped))
        if not parts:
            self._set_status("未找到可推送的连接", "warning")
            return
        self._set_status("{}｜文件 {}".format("，".join(parts), remote_name),
                         "warning" if skip_map else "info")

    # _on_transfer
    def _on_transfer(self, payload):
        """TOPIC_TRANSFER：state=list 走远端脚本对话框；其余走传输列表。"""
        st = str(payload.get("state") or "")
        if st == "list":
            self._on_script_list(payload)
        elif st in ("sending", "done", "failed"):
            self._xfer_upsert(payload)

    # _iid_exists
    @staticmethod
    def _iid_exists(tree, iid):
        try:
            return bool(tree.exists(iid))
        except Exception:
            return False

    # _xfer_upsert
    def _xfer_upsert(self, payload):
        """按 tid 合并发送/接收进度行；终态更新状态行摘要。

        Phase3-2：同一 tid 的 sending→done/failed 更新同一行；远端拒绝事件
        （带 tid、无进度）也会命中既有行并改为 失败：<reason>。
        """
        tid = str(payload.get("tid") or "")
        nid = str(payload.get("node_id") or "")
        name = str(payload.get("name") or "")
        if not tid:
            # 理论上不应发生（Phase3-2 起各 SCRIPT_PUSH 回执均带 tid）；退化为合成键
            tid = "anon::{}::{}".format(nid, name)
        state = str(payload.get("state") or "")
        detail = str(payload.get("detail") or "")
        dev = (self._peers.get(nid) or {}).get("name") or nid or "—"
        try:
            sent = int(payload.get("sent") or 0)
            total = int(payload.get("total") or 0)
        except Exception:
            sent = total = 0
        if total > 0:
            prog = "{}/{} ({}%)".format(sent, total, int(sent * 100 / total))
        else:
            prog = None    # 无进度信息（如远端拒绝事件）→ 保留既有行进度
        if state == "sending":
            stxt, tag = "传输中", "xfer_run"
        elif state == "done":
            d = detail if len(detail) <= 80 else (detail[:80] + "…")
            stxt, tag = ("成功" + (" · " + d if d else "")), "xfer_ok"
        else:
            stxt, tag = "失败：{}".format(detail or "未知错误"), "xfer_fail"
        # 取实际写入行的 (dev, name)：远端终态回执经 node 合成 TOPIC_TRANSFER，
        # 其来源 CMD_ACK 不带 name → name 为空，须沿用既有行的文件名，
        # 否则底部摘要退化为「<设备> 推送  成功」而丢失文件名。
        resolved = self._xfer_upsert_row(tid, dev, name, stxt, tag, prog)
        use_dev, use_name = resolved if resolved else (dev, name)
        if state in ("done", "failed"):
            res = "成功" if state == "done" else "失败"
            self._xfer_summary = "{} 推送 {} {}".format(use_dev, use_name, res)
            self._set_status(self._xfer_summary,
                             "warning" if state == "failed" else "info")

    # _xfer_upsert_row
    def _xfer_upsert_row(self, tid, dev, name, stxt, tag, prog=None):
        """新增/更新传输行；不存在则插入顶部；最多保留 50 行。

        更新既有行时：name/dev 为空则沿用旧值（远端拒绝回执不带 name，
        不应把已有行的文件名清空）；prog=None 则保留原进度列。
        返回实际写入行的 (dev, name)，供调用方生成底部摘要（name 可能被沿用）。
        """
        try:
            iid = self._xfer_items.get(tid)
            if iid is not None and self._iid_exists(self.tv_xfer, iid):
                vals = self.tv_xfer.item(iid).get("values") or ()
                cur_dev = str(vals[0]) if len(vals) >= 1 else ""
                cur_name = str(vals[1]) if len(vals) >= 2 else ""
                cur_prog = str(vals[2]) if len(vals) >= 3 else "—"
                use_dev = dev if dev else cur_dev
                use_name = name if name else cur_name
                use_prog = cur_prog if prog is None else prog
                self.tv_xfer.item(iid, values=(use_dev, use_name, use_prog, stxt),
                                  tags=(tag,))
                return use_dev, use_name
            if prog is None:
                prog = "—"
            iid = self.tv_xfer.insert("", 0, values=(dev, name, prog, stxt),
                                      tags=(tag,))
            self._xfer_items[tid] = iid
            try:
                self.lbl_xfer_empty.grid_remove()
            except Exception:
                pass
            while len(self.tv_xfer.get_children()) > 50:
                oldest = self.tv_xfer.get_children()[-1]
                self.tv_xfer.delete(oldest)
                for k, v in list(self._xfer_items.items()):
                    if v == oldest:
                        self._xfer_items.pop(k, None)
            return dev, name
        except Exception:
            return dev, name

    # _on_remote_scripts
    def _on_remote_scripts(self):
        """[▭ 远端脚本]：向选中设备请求脚本列表（结果经 TOPIC_TRANSFER 回来）。"""
        nid = self._sel
        ok, reason = self._script_state()
        if not ok:
            self._set_status(reason or "无法查看远端脚本", "warning")
            return
        nl = _nl()
        sent = False
        try:
            sent = bool(nl.list_remote_scripts(nid))
        except Exception:
            sent = False
        if not sent:
            self._set_status("无法请求远端脚本列表", "warning")
            return
        self._set_status("正在请求远端脚本列表…", "info")

    # _on_script_list
    def _on_script_list(self, payload):
        """state=list 事件：打开或刷新「远端脚本」对话框。"""
        nid = str(payload.get("node_id") or "")
        scripts = payload.get("scripts")
        if not isinstance(scripts, list):
            scripts = []
        self._open_rs_dialog(nid, scripts, str(payload.get("dir") or ""))

    # _open_rs_dialog
    def _open_rs_dialog(self, nid, scripts, dirname):
        """创建（或前置）远端脚本对话框并填充内容。"""
        dlg = self._rs_dlg
        alive = False
        try:
            alive = dlg is not None and bool(dlg.winfo_exists())
        except Exception:
            alive = False
        if not alive:
            self._rs_dlg = None
            dlg = self._rs_build_dialog()
            if dlg is None:
                return
        self._rs_node_id = nid
        self._rs_dirname = dirname or ""
        self._rs_scripts = list(scripts)
        self._rs_fill()
        try:
            dlg.lift()
        except Exception:
            pass

    # _rs_build_dialog
    def _rs_build_dialog(self):
        """构建远端脚本对话框（约 620x460）：顶部信息 + 列表 + 底部操作。"""
        C = utils.C
        try:
            dlg = tkinter.Toplevel(self.win)
        except Exception:
            return None
        self._rs_dlg = dlg
        try:
            dlg.title("远端脚本 — ACRPA")
            try:    # 外壳: 尺寸/最小尺寸/父窗居中统一走 window_shell (§2.3 P2)
                from ui.window_shell import apply_window_chrome
                apply_window_chrome(dlg, size=(620, 460), min_size=(520, 400),
                                    parent=self.win, bg_key="bg", colors=C)
            except Exception:
                dlg.geometry("620x460")
            dlg.configure(bg=C["bg"])
            self._set_icon(dlg)
            dlg.protocol("WM_DELETE_WINDOW", self._close_rs_dialog)
        except Exception:
            pass
        self._rs_info = tkinter.Label(dlg, text="—", font=utils.FONT_SMALL,
                                      bg=C["bg"], fg=C["fgm"], anchor="w")
        self._rs_info.grid(row=0, column=0, columnspan=2, sticky="ew",
                           padx=utils.sp(10), pady=(utils.sp(8), utils.sp(4)))
        rcols = ("name", "size", "mtime", "place")
        tv = ttk.Treeview(dlg, columns=rcols, show="headings", height=12)
        for cid, text, width in (("name", "名称", 240), ("size", "大小", 90),
                                 ("mtime", "修改时间", 150), ("place", "位置", 90)):
            tv.heading(cid, text=text)
            tv.column(cid, width=width, anchor="w")
        # §4.5 降级: show="headings" 无 #0 列 → 选中行加粗
        utils.bind_sel_bold(tv)
        tv.grid(row=1, column=0, sticky="nsew", padx=(utils.sp(10), 0), pady=(0, utils.sp(6)))
        rsb = ttk.Scrollbar(dlg, orient="vertical", command=tv.yview)
        rsb.grid(row=1, column=1, sticky="ns", padx=(0, utils.sp(10)), pady=(0, utils.sp(6)))
        tv.configure(yscrollcommand=rsb.set)
        tv.tag_configure("rs_empty", foreground=utils.themed("fgm"))
        tv.bind("<Double-1>", lambda e: self._on_rs_run())
        self._rs_tree = tv
        dlg.columnconfigure(0, weight=1)
        dlg.rowconfigure(1, weight=1)
        bar = tkinter.Frame(dlg, bg=C["bg"])
        bar.grid(row=2, column=0, columnspan=2, sticky="e", padx=utils.sp(10), pady=(0, utils.sp(10)))
        self._rs_btn_run = tkinter.Button(bar, text="[▶ 远程运行选中]",
                                          font=utils.FONT_BUTTON, bg=C["ac"],
                                          fg=utils.text_on(C["ac"]),
                                          activebackground=C["ach"],
                                          activeforeground=utils.text_on(C["ach"]),
                                          relief="flat",
                                          bd=1, cursor="hand2", padx=utils.sp(10), pady=utils.sp(3),
                                          command=self._on_rs_run)
        self._rs_btn_run.pack(side="left", padx=(0, utils.sp(6)))
        tkinter.Button(bar, text="[刷新]", font=utils.FONT_BUTTON, bg=C["bgc"],
                       fg=C["fgb"], activebackground=C["acl"], relief="flat",
                       bd=1, cursor="hand2", padx=utils.sp(10), pady=utils.sp(3),
                       command=self._on_rs_refresh).pack(side="left", padx=(0, utils.sp(6)))
        tkinter.Button(bar, text="[关闭]", font=utils.FONT_BUTTON, bg=C["bgc"],
                       fg=C["fgb"], activebackground=C["acl"], relief="flat",
                       bd=1, cursor="hand2", padx=utils.sp(10), pady=utils.sp(3),
                       command=self._close_rs_dialog).pack(side="left")
        return dlg

    # _rs_fill
    def _rs_fill(self):
        """填充远端脚本列表（保留按 name 的选中行）。空列表显示灰字提示。"""
        tv = self._rs_tree
        if tv is None:
            return
        nid = self._rs_node_id or ""
        dev = (self._peers.get(nid) or {}).get("name") or nid or "—"
        try:
            txt = "设备：{}".format(dev)
            if self._rs_dirname:
                txt = "{} · 目录：{}".format(txt, self._rs_dirname)
            self._rs_info.config(text=txt)
        except Exception:
            pass
        keep = self._rs_selected_name()
        try:
            for iid in tv.get_children():
                tv.delete(iid)
        except Exception:
            pass
        self._rs_items = {}
        scripts = self._rs_scripts or []
        if not scripts:
            try:
                tv.insert("", "end", values=("远端无可用脚本", "", "", ""),
                          tags=("rs_empty",))
            except Exception:
                pass
            return
        restore = None
        for it in scripts:
            if not isinstance(it, dict):
                continue
            name = str(it.get("name") or "")
            if not name:
                continue
            place = "已接收" if str(it.get("dir") or "") == "received" else "脚本目录"
            try:
                iid = tv.insert("", "end", values=(name, _fmt_size(it.get("size")),
                                                   _fmt_mtime(it.get("mtime")),
                                                   place))
                self._rs_items[iid] = name
                if keep and name == keep:
                    restore = iid
            except Exception:
                continue
        if restore:
            try:
                tv.selection_set(restore)
            except Exception:
                pass

    # _rs_selected_name
    def _rs_selected_name(self):
        """远端脚本列表当前选中行的相对名；无选中返回空串。"""
        tv = self._rs_tree
        try:
            sel = tv.selection()
        except Exception:
            sel = ()
        if not sel:
            return ""
        return str((self._rs_items or {}).get(sel[0]) or "")

    # _on_rs_run
    def _on_rs_run(self):
        """[▶ 远程运行选中]：二次确认后下发 CMD_RUN_SCRIPT（name 为相对名）。"""
        name = self._rs_selected_name()
        nid = self._rs_node_id or self._sel
        if not name or not nid:
            self._set_status("请先选择要运行的脚本", "warning")
            return
        dev = (self._peers.get(nid) or {}).get("name") or nid
        if not self._confirm_action(
                "远程运行脚本",
                "将在被控端「{}」运行脚本：\n{}\n确定下发【远程运行脚本】指令？".format(
                    dev, name)):
            return
        t = _cmd_run_script_type()
        nl = _nl()
        ok = False
        if t is not None and nl is not None:
            try:
                ok = bool(nl.send_command(nid, t, {"name": name}))
            except Exception as e:
                try:
                    log1("NetLink 远程运行脚本失败: {}".format(e), "warning")
                except Exception:
                    pass
                ok = False
        if ok:
            self._set_status("已下发运行脚本指令：{}".format(name), "info")
        else:
            self._set_status("运行脚本指令下发失败：目标连接不可用", "warning")

    # _on_rs_refresh
    def _on_rs_refresh(self):
        """[刷新]：重新请求远端脚本列表（复用已打开的对话框）。"""
        nid = self._rs_node_id
        if not nid:
            return
        nl = _nl()
        ok = False
        try:
            ok = bool(nl.list_remote_scripts(nid))
        except Exception:
            ok = False
        if ok:
            self._set_status("正在刷新远端脚本列表…", "info")
        else:
            self._set_status("无法请求远端脚本列表", "warning")

    # _close_rs_dialog
    def _close_rs_dialog(self):
        """销毁远端脚本对话框（幂等）；不取消总线，再次点击即重新请求。"""
        dlg = self._rs_dlg
        self._rs_dlg = None
        self._rs_tree = None
        self._rs_info = None
        self._rs_btn_run = None
        self._rs_items = {}
        try:
            if dlg is not None and dlg.winfo_exists():
                dlg.destroy()
        except Exception:
            pass

