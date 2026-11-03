# -*- coding: utf-8 -*-
"""NetLinkWindow 方法组：shot (L1-A 拆分)。"""

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

# ══════════════════════════════════════════════════════════════════════
# 远端截图预览: 预缩放 / 上限 / PhotoImage 复用 (v5 §4.2)
# ══════════════════════════════════════════════════════════════════════
# 回退开关: NETLINK_SHOT_ASYNC=False → 解码与缩放回到主线程 (旧行为)。
def _env_flag(name, default=True):
    """环境变量开关: 缺失/空 → default; 0/false/no/off → False。"""
    try:
        v = os.environ.get(name)
    except Exception:
        return default
    if v is None or str(v).strip() == "":
        return default
    return str(v).strip().lower() not in ("0", "false", "no", "off")


NETLINK_SHOT_ASYNC = _env_flag("NETLINK_SHOT_ASYNC", True)
SHOT_MAX_EDGE = 1600        # 预览图长边上限 (超出先缩, 解码+PhotoImage 成本降 60%+)
_SHOT_TK_CACHE = {}         # (w, h, 帧长度, ts) → PhotoImage (同尺寸同帧只建一次)
_SHOT_TK_MAX = 4


def _shot_cache_key(width, height, data_b64, ts):
    try:
        return (int(width), int(height), len(data_b64), int(ts or 0))
    except Exception:
        return None


def _shot_cache_put(key, photo):
    if key is None:
        return
    try:
        if len(_SHOT_TK_CACHE) >= _SHOT_TK_MAX:
            _SHOT_TK_CACHE.pop(next(iter(_SHOT_TK_CACHE)), None)
        _SHOT_TK_CACHE[key] = photo
    except Exception:
        pass


def _shot_target_size(width, height, label=None):
    """预览目标尺寸: 长边 ≤ `SHOT_MAX_EDGE`, 且不超过预览区尺寸 (不放大)。

    拿不到预览区尺寸时按 1600 兜底; 帧本身已小于上限则原样返回 (返回源尺寸 = 不缩放)。
    """
    try:
        w, h = int(width), int(height)
    except Exception:
        return None
    if w <= 0 or h <= 0:
        return None
    lim = SHOT_MAX_EDGE
    if label is not None:
        try:
            lw = int(label.winfo_width())
            lh = int(label.winfo_height())
            if lw > 120 and lh > 120:
                # 预留 12px 内边距, 避免贴边抖动; 下限 120px 防止极小窗口导致缩成糊
                lim = min(SHOT_MAX_EDGE, max(min(lim, lw - 12, lh - 12), 120))
        except Exception:
            pass
    longest = max(w, h)
    if longest <= lim:
        return (w, h)
    scale = float(lim) / float(longest)
    return (max(1, int(round(w * scale))), max(1, int(round(h * scale))))


def _shot_pick_resample(src_size, dst_size):
    """缩小类走 BOX (v5 §4.1「缩小类」行: 比单级 LANCZOS 快 ≥40% 且无混叠)。"""
    try:
        from PIL import Image
        sw, sh = int(src_size[0]), int(src_size[1])
        dw, dh = int(dst_size[0]), int(dst_size[1])
        if dw < sw or dh < sh:
            return Image.BOX
        return Image.LANCZOS
    except Exception:
        return None


def _netlink_bg_submit(fn, on_done, on_error, name):
    """解码提交到后台池; 通道不可用/未启动时同步执行 (返回 False)。"""
    if NETLINK_SHOT_ASYNC:
        try:
            import ui.bg_task as _bg
            if _bg.is_started():
                _bg.submit(fn, on_done, on_error, name=name, priority=1)
                return True
        except Exception:
            pass
    try:
        on_done(fn())
    except Exception as e:
        on_error(e)
    return False


class ShotMixin(object):
    # _shot_state
    def _shot_state(self):
        """[◎ 截图] 可用性判定；返回 (ok, reason)。

        reason 仅在不可用时非空（互联未启动 / 未选择设备 / 未认证 /
        需「允许操控」权限（当前：<中文权限名>））；防连点期间返回 (False, "")。
        """
        nid = self._sel
        if not self._running:
            return (False, "互联未启动")
        if not nid:
            return (False, "未选择设备")
        perm = self._authed.get(str(nid))
        if perm is None:
            return (False, "未认证")
        if not _perm_ok(perm):
            return (False, "需「允许操控」权限（当前：{}）".format(
                _perm_labels().get(perm, "仅观察")))
        if self._shot_pending and time.time() < self._shot_deadline:
            return (False, "")
        return (True, "")

    # _tip_shot
    def _tip_shot(self):
        """[◎ 截图] 动态悬停提示（可用时返回空串）。"""
        return self._shot_state()[1]

    # _on_remote_shot
    def _on_remote_shot(self):
        """[◎ 截图]：请求目标设备截图，按钮进入 ≤3s 防连点禁用。"""
        ok, reason = self._shot_state()
        if not ok:
            if reason:
                self._set_status(reason, "warning")
            return
        nid = self._sel
        nl = _nl()
        sent = False
        try:
            sent = bool(nl.request_screenshot(nid)) if nl is not None else False
        except Exception:
            sent = False
        if not sent:
            self._set_status("未找到到该设备的连接", "warning")
            return
        self._shot_pending = True
        self._shot_deadline = time.time() + 3.0
        self._shot_node_id = nid
        self._set_status("已请求截图…", "info")
        try:
            self.btn_shot.config(state="disabled")
        except Exception:
            pass

    # _on_screenshot
    def _on_screenshot(self, payload):
        """TOPIC_SCREENSHOT：结果到达 → 解除防连点；失败仅状态行告警，成功开窗。"""
        if not isinstance(payload, dict):
            return
        self._shot_pending = False
        nid = str(payload.get("node_id") or "")
        if not bool(payload.get("ok")):
            err = str(payload.get("error") or "unknown")
            self._set_status("截图失败：{}".format(err), "warning")
            return
        data_b64 = str(payload.get("data_b64") or "")
        if not data_b64:
            self._set_status("截图失败：empty payload", "warning")
            return
        width = _safe_int(payload.get("width"))
        height = _safe_int(payload.get("height"))
        nbytes = _safe_int(payload.get("bytes"))
        ts = payload.get("ts")
        dev = (self._peers.get(nid) or {}).get("name") or nid or "设备"
        self._shot_node_id = nid
        self._shot_data = data_b64
        self._open_shot_dialog(dev, data_b64, width, height, nbytes, ts)

    # _shot_dlg_alive
    def _shot_dlg_alive(self):
        try:
            return self._shot_dlg is not None and bool(self._shot_dlg.winfo_exists())
        except Exception:
            return False

    # _open_shot_dialog
    def _open_shot_dialog(self, dev, data_b64, width, height, nbytes, ts):
        """打开/刷新「远程截图」窗口，并渲染图像（PIL 不可用则降级为临时文件）。"""
        C = utils.C
        if not self._shot_dlg_alive():
            try:
                dlg = tkinter.Toplevel(self.win)
            except Exception:
                return
            self._shot_dlg = dlg
            try:
                dlg.title("远程截图 — {}".format(dev))
                w = max(420, int(width) + 40)
                h = int(height) + 140
                try:    # 外壳: 尺寸/最小尺寸/父窗居中统一走 window_shell (§2.3 P2)
                    from ui.window_shell import apply_window_chrome
                    apply_window_chrome(dlg, size=(w, h), min_size=(420, 320),
                                        parent=self.win, bg_key="bg", colors=C)
                except Exception:
                    dlg.geometry("{}x{}".format(w, h))
                dlg.configure(bg=C["bg"])
                self._set_icon(dlg)
                dlg.protocol("WM_DELETE_WINDOW", self._close_shot_dialog)
                dlg.columnconfigure(0, weight=1)
                dlg.rowconfigure(1, weight=1)
            except Exception:
                pass
            self._shot_info = tkinter.Label(dlg, text="", font=utils.FONT_SMALL,
                                            bg=C["bg"], fg=C["fgm"], anchor="w")
            self._shot_info.grid(row=0, column=0, sticky="ew", padx=utils.sp(10), pady=(utils.sp(8), utils.sp(2)))
            self._shot_img_lbl = tkinter.Label(dlg, text="", bg=C["logbg"],
                                               fg=C["logfg"], anchor="center")
            self._shot_img_lbl.grid(row=1, column=0, sticky="nsew",
                                    padx=utils.sp(10), pady=(0, utils.sp(6)))
            self._shot_bar = tkinter.Frame(dlg, bg=C["bg"])
            self._shot_bar.grid(row=2, column=0, sticky="e", padx=utils.sp(10), pady=(0, utils.sp(10)))
            tkinter.Button(self._shot_bar, text="[刷新]", font=utils.FONT_BUTTON,
                           bg=C["bgc"], fg=C["fgb"], activebackground=C["acl"],
                           relief="flat", bd=1, cursor="hand2", padx=utils.sp(10), pady=utils.sp(3),
                           command=self._shot_refresh).pack(side="left", padx=(0, utils.sp(6)))
            tkinter.Button(self._shot_bar, text="[保存为…]", font=utils.FONT_BUTTON,
                           bg=C["bgc"], fg=C["fgb"], activebackground=C["acl"],
                           relief="flat", bd=1, cursor="hand2", padx=utils.sp(10), pady=utils.sp(3),
                           command=self._shot_save).pack(side="left", padx=(0, utils.sp(6)))
            tkinter.Button(self._shot_bar, text="[关闭]", font=utils.FONT_BUTTON,
                           bg=C["bgc"], fg=C["fgb"], activebackground=C["acl"],
                           relief="flat", bd=1, cursor="hand2", padx=utils.sp(10), pady=utils.sp(3),
                           command=self._close_shot_dialog).pack(side="left")
        else:
            try:
                self._shot_dlg.title("远程截图 — {}".format(dev))
            except Exception:
                pass
        self._shot_render(data_b64, width, height, nbytes, ts)

    # _shot_render
    def _shot_render(self, data_b64, width, height, nbytes, ts):
        """把 base64 JPEG 渲染到窗口；优先 PIL 内嵌，失败降级为临时文件。"""
        try:
            tstr = time.strftime("%H:%M:%S", time.localtime(int(ts))) if ts \
                else time.strftime("%H:%M:%S")
        except Exception:
            tstr = time.strftime("%H:%M:%S")
        sc = _screen()
        try:
            q = int(getattr(sc, "QUALITY_DEFAULT", 60))
        except Exception:
            q = 60
        info = "分辨率 {}x{} · 质量 {} · 大小 {} · 时间 {}".format(
            width, height, q, _fmt_size(nbytes), tstr)
        try:
            self._shot_info.config(text=info)
        except Exception:
            pass
        # 1) PIL 内嵌预览 (解码/预缩放下放 IO 池; PhotoImage 必须主线程建)
        if self._shot_pil_try(data_b64, width, height, ts):
            return
        # 2) 降级：写临时文件 + [打开文件]
        self._shot_fallback_temp(data_b64)

    # _shot_img_apply
    def _shot_img_apply(self, photo):
        """主线程: 把 PhotoImage 贴到预览 Label (含保活与降级按钮状态复位)。"""
        self._shot_img = photo               # 保活，避免被 GC 白屏
        try:
            self._shot_img_lbl.config(image=photo, text="")
        except Exception:
            pass
        self._shot_cleanup_temp()
        self._shot_open_btn_pack(False)

    # _shot_fallback_temp
    def _shot_fallback_temp(self, data_b64):
        """降级路径: 写临时文件 + 提示「不支持内嵌预览」(行为与改造前一致)。"""
        path = self._shot_write_temp(data_b64)
        self._shot_img = None
        if path:
            try:
                self._shot_img_lbl.config(
                    image="",
                    text="已保存到：{}\n（当前环境不支持内嵌预览）".format(path))
            except Exception:
                pass
            self._shot_open_btn_pack(True)
            self._set_status("已保存到 {}".format(path), "warning")
        else:
            try:
                self._shot_img_lbl.config(
                    image="", text="无法渲染截图（PIL 不可用且临时文件写入失败）")
            except Exception:
                pass
            self._set_status("截图渲染失败", "warning")

    # _shot_pil_try
    def _shot_pil_try(self, data_b64, width, height, ts):
        """PIL 内嵌预览: 预缩放 + 缓存复用 + 解码外移 (v5 §4.2)。

        返回 True 表示已接管 (同步贴图或已提交后台);
        返回 False 表示 PIL/base64 不可用 → 交给临时文件降级路径。
        PhotoImage 的创建**始终在主线程** (工作线程只做 decode + resize)。
        """
        try:
            import io as _io
            import base64 as _b64
            from PIL import Image, ImageTk      # noqa: F401 (可用性探针)
        except Exception:
            return False
        key = _shot_cache_key(width, height, data_b64, ts)
        hit = _SHOT_TK_CACHE.get(key) if key is not None else None
        if hit is not None:
            self._shot_img_apply(hit)           # 同尺寸同帧: 0 次解码/0 次 PhotoImage
            return True
        try:
            raw = _b64.b64decode(data_b64)
        except Exception:
            return False
        target = _shot_target_size(width, height, self._shot_img_lbl)

        def _decode():
            """后台: 解码 + 预缩放 (长边 ≤1600 且不超过预览区)。"""
            img = Image.open(_io.BytesIO(raw))
            try:
                img.load()                      # 明确在此处完成解码 (不留给 PhotoImage)
            except Exception:
                pass
            if target and tuple(img.size) != tuple(target):
                img = img.resize(tuple(target),
                                 _shot_pick_resample(img.size, target))
            return img

        def _done(img):
            """主线程: 建 PhotoImage (Tk 对象只能主线程), 再贴图 + 入缓存。"""
            try:
                photo = ImageTk.PhotoImage(img)
            except Exception:
                self._shot_fallback_temp(data_b64)
                return
            _shot_cache_put(key, photo)
            self._shot_img_apply(photo)

        def _err(_e):
            self._shot_fallback_temp(data_b64)

        _netlink_bg_submit(_decode, _done, _err, "nl-shot-decode")
        return True

    # _shot_open_btn_pack
    def _shot_open_btn_pack(self, show):
        """降级路径下显示/隐藏 [打开文件] 按钮。"""
        try:
            if show:
                if self._shot_open_btn is None:
                    C = utils.C
                    self._shot_open_btn = tkinter.Button(
                        self._shot_bar, text="[打开文件]", font=utils.FONT_BUTTON,
                        bg=C["bgc"], fg=C["fgb"], activebackground=C["acl"],
                        relief="flat", bd=1, cursor="hand2", padx=utils.sp(10), pady=utils.sp(3),
                        command=self._shot_open_file)
                self._shot_open_btn.pack(side="left", padx=(utils.sp(6), 0))
            elif self._shot_open_btn is not None:
                self._shot_open_btn.pack_forget()
        except Exception:
            pass

    # _shot_open_file
    def _shot_open_file(self):
        """用系统默认程序打开降级保存的临时文件。"""
        path = self._shot_path
        if not path:
            return
        try:
            if os.name == "nt":
                os.startfile(path)
            else:
                import subprocess
                subprocess.Popen(["xdg-open", path])
        except Exception as e:
            self._set_status("打开文件失败：{}".format(e), "warning")

    # _shot_write_temp
    def _shot_write_temp(self, data_b64):
        """把 base64 JPEG 写到临时文件；返回路径（失败返回空串）。"""
        try:
            import base64 as _b64
            import tempfile
            raw = _b64.b64decode(data_b64)
            fd, path = tempfile.mkstemp(prefix="acrpa_shot_", suffix=".jpg")
            try:
                os.write(fd, raw)
            finally:
                os.close(fd)
            self._shot_path = path
            return path
        except Exception:
            return ""

    # _shot_cleanup_temp
    def _shot_cleanup_temp(self):
        """删除降级路径的临时文件并清空引用。"""
        p = self._shot_path
        self._shot_path = None
        if p:
            try:
                os.remove(p)
            except Exception:
                pass

    # _shot_refresh
    def _shot_refresh(self):
        """[刷新]：重发截图请求（沿用当前设备）。"""
        nid = self._shot_node_id or self._sel
        nl = _nl()
        sent = False
        if nid and nl is not None:
            try:
                sent = bool(nl.request_screenshot(nid))
            except Exception:
                sent = False
        if sent:
            self._set_status("已请求截图…", "info")
        else:
            self._set_status("未找到到该设备的连接", "warning")

    # _shot_save
    def _shot_save(self):
        """[保存为…]：把当前截图 JPEG 另存到用户选择路径。"""
        data = self._shot_data
        if not data:
            self._set_status("无可保存的截图", "warning")
            return
        path = ""
        try:
            from tkinter import filedialog
            path = filedialog.asksaveasfilename(
                parent=self._shot_dlg or self.win, title="保存截图",
                defaultextension=".jpg",
                filetypes=[("JPEG 图片", "*.jpg"), ("全部文件", "*.*")])
        except Exception:
            path = ""
        if not path:
            return
        try:
            import base64 as _b64
            with open(path, "wb") as f:
                f.write(_b64.b64decode(data))
            self._set_status("已保存到 {}".format(path), "info")
        except Exception as e:
            self._set_status("保存失败：{}".format(e), "warning")

    # _close_shot_dialog
    def _close_shot_dialog(self):
        """关闭截图窗口：清空 PhotoImage 引用与临时文件。"""
        self._shot_img = None
        self._shot_data = ""
        self._shot_cleanup_temp()
        dlg = self._shot_dlg
        self._shot_dlg = None
        self._shot_info = None
        self._shot_img_lbl = None
        self._shot_bar = None
        self._shot_open_btn = None
        self._shot_node_id = None
        if dlg is not None:
            try:
                dlg.destroy()
            except Exception:
                pass

