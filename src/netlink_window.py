# -*- coding: utf-8 -*-
"""ACRPA NetLink — 「设备互联」独立窗口 (Phase 1-3)。

设计要点:
  * 独立 Toplevel (主窗口仅 500x625, 互联信息量大, 不挤占主界面);
  * 所有跨线程 (bus/网络线程) 数据一律经 root.after 轮询 drain 到主线程消费,
    绝不在非主线程触碰任何 Tk 控件;
  * 任何 netlink 调用都 try/except, 失败仅 log1(..., "warning"), 不影响主程序;
  * 颜色/字体复用 utils.C / utils.FONT_*; utils.apply_theme 会重绑模块级 C,
    故本模块 `import utils` 并每次 utils.C[...] 取值 (禁止 from utils import C);
  * import 期零副作用 (不创建窗口), 可在无 GUI 环境安全 import。

依赖既有接口 (Phase 1-1/1-2 已定稿, 本处只消费不重写):
  netlink.get_bus()/is_running()/start_netlink(root)/stop_netlink()/get_node()
  netlink.set_control_hooks(...) / send_command(peer_node_id, t, data)
  netlink.node.TOPIC_PEER / TOPIC_PEER_STATE / TOPIC_PEER_LOG / TOPIC_PEER_SCHED /
                TOPIC_STATUS / TOPIC_AUTH / TOPIC_CMD_RESULT
"""
import os
import socket
import time
import tkinter
from tkinter import ttk

import state
import utils
from utils import log1

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DEFAULT_TCP_PORT = 19710
MAX_LOG_LINES = 2000
TRIM_LOG_LINES = 500
PUMP_INTERVAL_MS = 200


# ══════════════════════════════════════════════════════════════════════
# 懒加载辅助 (失败一律返回 None, 绝不抛)
# ══════════════════════════════════════════════════════════════════════

def _nl():
    """懒加载 netlink 门面；失败返回 None。"""
    try:
        import netlink
        return netlink
    except Exception:
        return None

def _topics():
    """懒加载总线主题常量；失败返回 None (调用方需判空)。"""
    try:
        from netlink.node import (TOPIC_PEER, TOPIC_PEER_STATE, TOPIC_PEER_LOG,
                                  TOPIC_PEER_SCHED, TOPIC_STATUS, TOPIC_AUTH,
                                  TOPIC_CMD_RESULT)
        return {"peer": TOPIC_PEER, "state": TOPIC_PEER_STATE,
                "log": TOPIC_PEER_LOG, "sched": TOPIC_PEER_SCHED,
                "status": TOPIC_STATUS, "auth": TOPIC_AUTH,
                "cmd_result": TOPIC_CMD_RESULT}
    except Exception:
        return None
        return None


def _perm_labels():
    """权限 code → 中文标签；取不到时用内置兜底 (不抛)。"""
    try:
        from netlink.security import PERM_LABELS
        return dict(PERM_LABELS)
    except Exception:
        return {"observe": "仅观察", "control": "允许操控", "script": "允许接收脚本"}


def _perm_code(label):
    """中文标签 → 权限 code；未知返回 None。"""
    try:
        for code, lab in _perm_labels().items():
            if lab == label:
                return code
    except Exception:
        pass
    return None


def _perm_ok(perm):
    """perm 是否满足「允许操控」门槛（复用 netlink.security.perm_satisfies）。"""
    try:
        from netlink.security import perm_satisfies
        return bool(perm_satisfies(perm, "control"))
    except Exception:
        return str(perm or "") in ("control", "script")


def _cmd_types():
    """懒加载指令类型常量；失败返回 None。"""
    try:
        from netlink.protocol import (T_CMD_RUN, T_CMD_PAUSE, T_CMD_RESUME,
                                      T_CMD_STOP, T_CMD_ACK, T_CMD_ERR)
        return {"run": T_CMD_RUN, "pause": T_CMD_PAUSE, "resume": T_CMD_RESUME,
                "stop": T_CMD_STOP, "ack": T_CMD_ACK, "err": T_CMD_ERR}
    except Exception:
        return None


def _cmd_run_script_type():
    """懒加载 T_CMD_RUN_SCRIPT；失败返回 None。"""
    try:
        from netlink.protocol import T_CMD_RUN_SCRIPT
        return T_CMD_RUN_SCRIPT
    except Exception:
        return None


def _perm_script(perm):
    """perm 是否满足「允许接收脚本」门槛（复用 netlink.security.perm_satisfies）。"""
    try:
        from netlink.security import perm_satisfies
        return bool(perm_satisfies(perm, "script"))
    except Exception:
        return str(perm or "") == "script"


def _transfer():
    """懒加载 netlink.transfer 模块；失败返回 None。"""
    try:
        from netlink import transfer
        return transfer
    except Exception:
        return None


def _transfer_topic():
    """经门面取脚本分发进度主题；失败退回字面量（不硬编码在调用点）。"""
    try:
        nl = _nl()
        if nl is not None:
            return str(nl.get_transfer_topic())
    except Exception:
        pass
    return "netlink.transfer"


def _screenshot_topic():
    """经门面取远程截图事件主题；失败退回字面量（不硬编码在调用点）。"""
    try:
        nl = _nl()
        if nl is not None:
            return str(nl.get_screenshot_topic())
    except Exception:
        pass
    return "netlink.screenshot"


def _screen():
    """懒加载 netlink.screen 模块（仅用于取常量）；失败返回 None。"""
    try:
        from netlink import screen
        return screen
    except Exception:
        return None


def _safe_int(v, default=0):
    """宽松 int 转换（失败返回 default）。"""
    try:
        return int(v)
    except Exception:
        return default


def _script_root():
    """被控端脚本根目录（取不到返回空串）。"""
    try:
        tf = _transfer()
        return tf.script_root() if tf is not None else ""
    except Exception:
        return ""


def _fmt_size(n):
    """字节数 → 人类可读（失败返回 —）。"""
    try:
        n = int(n)
    except Exception:
        return "—"
    if n < 1024:
        return "{} B".format(n)
    if n < 1024 * 1024:
        return "{:.1f} KB".format(n / 1024.0)
    return "{:.1f} MB".format(n / (1024.0 * 1024.0))


def _fmt_mtime(ts):
    """时间戳 → 本地时间字符串（失败返回 —）。"""
    try:
        ts = int(ts)
        if ts > 0:
            return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
    except Exception:
        pass
    return "—"


# 指令类型 → 中文标签 / 动词（展示用，不依赖 import）
_CMD_LABELS = {"CMD_RUN": "远程运行", "CMD_PAUSE": "远程暂停",
               "CMD_RESUME": "远程恢复", "CMD_STOP": "远程停止",
               "CMD_RUN_SCRIPT": "运行脚本"}
_CMD_VERB = {"run": "运行", "pause": "暂停", "resume": "恢复", "stop": "停止"}


# ══════════════════════════════════════════════════════════════════════
# 设备互联窗口
# ══════════════════════════════════════════════════════════════════════

class NetLinkWindow(object):
    """「设备互联」独立窗口。首次 open 时创建 Toplevel, 之后 lift+focus。"""

    def __init__(self, root):
        self.root = root
        self.win = None
        self._pump_id = None
        # 主题色表快照 (建窗时的 utils.C), 供 refresh_theme 做旧色→新色映射
        self._theme_snapshot = None
        # ── 数据缓存 (仅在主线程被读写) ──
        self._peers = {}        # {node_id: {"name","host","port","version","online"}}
        self._states = {}       # {node_id: state_snapshot dict}
        self._scheds = {}       # {node_id: sched_status dict}
        self._logseq = {}       # {node_id: last_seq}
        self._items = {}        # {node_id: treeview item id}
        self._sel = None        # 当前选中 node_id
        self._status_msg = ""
        self._status_level = "info"
        # ── Phase2-1b 配对/权限状态 (仅在主线程读写) ──
        self._authed = {}        # {node_id: perm_code} 已认证设备
        self._running = False    # 最近一次刷新得到的运行态
        self._paired_sig = None  # 已配对区数据签名 (diff 防闪烁)
        self._paired_items = {}  # {fingerprint: treeview iid}
        self._sel_fp = None      # 已配对区当前选中 fingerprint
        self._pair_dlg = None    # 配对码对话框 (Toplevel)
        self._pair_dlg_after = None
        # ── Phase2-2b 远程操控状态 (仅在主线程读写) ──
        self._cmd_rows = []      # 远程指令行 [{node_id,cmd,iid,terminal,status}]
        self._cmd_summary = ""   # 底部状态行最新指令摘要
        self._audit_dlg = None   # 审计日志对话框 (Toplevel)
        self._audit_text = None  # 审计日志只读 Text
        self._audit_path_lbl = None
        # ── Phase3-1b 脚本分发状态 (仅在主线程读写) ──
        self._xfer_items = {}    # {tid: 传输列表 treeview iid}
        self._xfer_summary = ""  # 底部状态行最新传输摘要
        self._rs_dlg = None      # 远端脚本对话框 (Toplevel)
        self._rs_tree = None     # 远端脚本 treeview
        self._rs_info = None     # 远端脚本对话框顶部信息 Label
        self._rs_btn_run = None  # [▶ 远程运行选中] 按钮
        self._rs_items = {}      # {iid: 相对脚本名}
        self._rs_scripts = []    # 最近一次列表事件里的 scripts
        self._rs_node_id = None  # 远端脚本对话框当前设备 node_id
        self._rs_dirname = ""    # 被控端脚本目录名 (事件未带则为空)
        # ── Phase4-1 远程截图状态 (仅在主线程读写) ──
        self._shot_dlg = None        # 截图查看窗口 (Toplevel)
        self._shot_img = None        # PhotoImage 引用 (必须保活, 否则白屏)
        self._shot_img_lbl = None    # 图像 Label
        self._shot_info = None       # 顶部信息 Label
        self._shot_bar = None        # 底部按钮条
        self._shot_open_btn = None   # 降级路径的 [打开文件] 按钮
        self._shot_path = None       # 降级临时文件路径
        self._shot_data = ""         # 最近一次截图的 base64
        self._shot_node_id = None    # 最近一次截图的设备 node_id
        self._shot_pending = False   # 防连点标记
        self._shot_deadline = 0.0    # 防连点截止时间
        # ── Phase4-2 浏览器只读监控面板状态 (仅在主线程读写) ──
        self._web_dlg = None         # 网页面板对话框 (Toplevel)
        self._web_status_lbl = None  # 面板状态行 Label
        self._web_url_var = None     # 访问地址 StringVar
        self._web_btn_toggle = None  # [启用]/[停用] 按钮
        self._web_control_var = None       # 有限控制开关 BooleanVar
        self._web_allow_remote_var = None  # 允许局域网控制 BooleanVar
        self._web_pin_btn = None           # [设置/重置控制 PIN] 按钮
        self._web_tls_lbl = None           # 协议/控制/证书状态 Label
        self._build()

    # ── 公开 API ────────────────────────────────────────────────────
    def alive(self):
        try:
            return self.win is not None and bool(self.win.winfo_exists())
        except Exception:
            return False

    def show(self):
        if not self.alive():
            return
        try:
            self.win.deiconify()
            self.win.lift()
            self.win.focus_force()
        except Exception:
            pass
        if self._pump_id is None:
            self._schedule_pump(PUMP_INTERVAL_MS)

    def close(self):
        """保存窗口几何 → 销毁；幂等。"""
        self._close_pair_dialog()
        self._close_audit_dialog()
        self._close_rs_dialog()
        self._close_shot_dialog()
        self._close_web_dialog()
        if self._pump_id is not None:
            try:
                self.root.after_cancel(self._pump_id)
            except Exception:
                pass
            self._pump_id = None
        try:
            if self.alive():
                state.NETLINK_UI_WINDOW = self.win.geometry()
                state.save_config()
        except Exception:
            pass
        try:
            if self.win is not None:
                self.win.destroy()
        except Exception:
            pass
        self.win = None

    # ── UI 构建 ─────────────────────────────────────────────────────
    def _set_icon(self, window):
        ico = os.path.join(APP_ROOT, "res", "automation.ico")
        if not os.path.exists(ico):
            ico = os.path.join(APP_ROOT, "automation.ico")
        if not os.path.exists(ico):
            return
        try:
            window.iconbitmap(ico)
        except Exception:
            pass

    # ── 主题刷新 (D4) ───────────────────────────────
    def refresh_theme(self):
        """主题切换后重刷本窗口配色 (含各 Treeview/Text 的 tag 前景)。

        utils.apply_theme 会重绑 utils.C, 但本窗口的面板/按钮颜色是建窗时写死的旧色,
        且各 Treeview 的 tag 前景只在建窗时配置过一次 —— 若不重刷, 切换主题后本窗口仍停留在旧配色。
        """
        if not self.alive():
            return
        cur = utils.C
        prev = self._theme_snapshot if isinstance(self._theme_snapshot, dict) else {}
        # 旧色 → 旧键 → 新色 的取值映射: 保留语义 (强调色仍是强调色, 中性色仍是中性色)
        vmap = {}
        for k, v in prev.items():
            if isinstance(v, str) and v:
                vmap[v] = k

        def _map_color(color):
            try:
                key = vmap.get(str(color))
                if key and key in cur:
                    return cur[key]
            except Exception:
                pass
            return None

        def _walk(p):
            for w in p.winfo_children():
                try:
                    cls = w.winfo_class()
                    if cls in ("Frame", "TFrame", "Label", "TLabel", "Button",
                               "Entry", "Text", "Canvas", "Scrollbar",
                               "Listbox", "Spinbox", "Checkbutton",
                               "Radiobutton", "Menubutton"):
                        for opt in ("bg", "fg", "activebackground",
                                    "activeforeground", "selectbackground",
                                    "selectforeground", "insertbackground",
                                    "selectcolor", "troughcolor",
                                    "highlightbackground"):
                            try:
                                old = w.cget(opt)
                            except Exception:
                                continue
                            new = _map_color(old)
                            if new and new != old:
                                try:
                                    w.configure(**{opt: new})
                                except Exception:
                                    pass
                except Exception:
                    pass
                _walk(w)

        # 本窗 + 已打开的子对话框 (存在则活, 不存在为空操作)
        windows = [self.win]
        for attr in ("_pair_dlg", "_audit_dlg", "_rs_dlg",
                     "_shot_dlg", "_web_dlg"):
            d = getattr(self, attr, None)
            if d is not None:
                windows.append(d)
        for win in windows:
            try:
                if win is not None and win.winfo_exists():
                    _walk(win)
            except Exception:
                pass
        # Treeview / Text 的 tag 前景 (建窗时只配置过一次)
        try:
            self.tv.tag_configure("run", foreground=cur["sc"])
            self.tv.tag_configure("pause", foreground=cur["wn"])
            for t in ("idle", "off", "unauth"):
                self.tv.tag_configure(t, foreground=cur["fgm"])
        except Exception:
            pass
        try:
            self.txt_log.tag_configure("err", foreground=cur["err"])
            self.txt_log.tag_configure("wn", foreground=cur["wn"])
        except Exception:
            pass
        try:
            self.tv_cmd.tag_configure("cmdok", foreground=cur["sc"])
            self.tv_cmd.tag_configure("cmdfail", foreground=cur["err"])
            self.tv_cmd.tag_configure("cmdgray", foreground=cur["fgm"])
        except Exception:
            pass
        try:
            self.tv_xfer.tag_configure("xfer_run", foreground=cur["ac"])
            self.tv_xfer.tag_configure("xfer_ok", foreground=cur["sc"])
            self.tv_xfer.tag_configure("xfer_fail", foreground=cur["err"])
        except Exception:
            pass
        if self._rs_tree is not None:
            try:
                self._rs_tree.tag_configure("rs_empty", foreground=cur["fgm"])
            except Exception:
                pass
        self._theme_snapshot = dict(cur)
        try:
            self.win.update_idletasks()
        except Exception:
            pass

    def _build(self):
        C = utils.C
        self._theme_snapshot = dict(C)
        win = tkinter.Toplevel(self.root)
        self.win = win
        try:    # 声明自管换肤: 通用 walk 不再跨入, 由本类 refresh_theme 负责
            from ui import theme as _ui_theme
            _ui_theme.claim_window(win, "netlink_window")
        except Exception:
            pass
        win.title("设备互联 — ACRPA")
        geom = ""
        try:
            geom = getattr(state, "NETLINK_UI_WINDOW", "") or ""
        except Exception:
            geom = ""
        if geom:
            try:
                win.geometry(geom)
            except Exception:
                win.geometry("780x700")
        else:
            win.geometry("780x700")
        win.minsize(700, 560)
        win.configure(bg=C["bg"])
        self._set_icon(win)
        win.protocol("WM_DELETE_WINDOW", self.close)
        win.columnconfigure(0, weight=1)
        win.rowconfigure(5, weight=1)   # 日志面板可伸缩

        # ── 0. 顶部状态条 ──
        top = tkinter.Frame(win, bg=C["bg"])
        top.grid(row=0, column=0, sticky="ew", padx=10, pady=(8, 4))
        top.columnconfigure(0, weight=1)
        self.lbl_top = tkinter.Label(top, text="本机名: —  |  TCP :—  |  ○ 已停止",
                                     font=utils.FONT_BODY, bg=C["bg"], fg=C["fgt"],
                                     anchor="w")
        self.lbl_top.grid(row=0, column=0, sticky="w")
        self.lbl_pair = tkinter.Label(top, text="配对码 —", font=utils.FONT_SMALL,
                                      bg=C["bg"], fg=C["fgm"], anchor="e")
        self.lbl_pair.grid(row=0, column=1, sticky="e", padx=(8, 6))
        self.btn_pair_code = tkinter.Button(top, text="配对码", font=utils.FONT_BUTTON,
                                            bg=C["bgc"], fg=C["fgb"],
                                            activebackground=C["acl"], relief="flat",
                                            bd=1, cursor="hand2", padx=12, pady=3,
                                            command=self._on_pair_code)
        self.btn_pair_code.grid(row=0, column=2, sticky="e", padx=(0, 6))
        try:
            utils.attach_tooltip(self.btn_pair_code,
                                 "生成 6 位配对码，供其它设备输入以完成配对 (被控端)")
        except Exception:
            pass
        self.btn_toggle = tkinter.Button(top, text="启动互联", font=utils.FONT_BUTTON,
                                         bg=C["ac"], fg="white",
                                         activebackground=C["ach"], activeforeground="white",
                                         relief="flat", bd=1, cursor="hand2",
                                         padx=12, pady=3, command=self._on_toggle)
        self.btn_toggle.grid(row=0, column=3, sticky="e")

        # ── 1. 配置行 ──
        cfg = tkinter.Frame(win, bg=C["bg"])
        cfg.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 4))

        self.var_auto = tkinter.BooleanVar(
            value=bool(getattr(state, "NETLINK_AUTODISCOVER", True)))
        ttk.Checkbutton(cfg, text="自动发现(UDP)", variable=self.var_auto).pack(
            side="left", padx=(0, 8))

        tkinter.Label(cfg, text="端口", font=utils.FONT_SMALL,
                      bg=C["bg"], fg=C["fgm"]).pack(side="left", padx=(0, 4))
        self.var_port = tkinter.StringVar(
            value=str(getattr(state, "NETLINK_PORT", DEFAULT_TCP_PORT)))
        tkinter.Entry(cfg, textvariable=self.var_port, width=7, font=utils.FONT_BODY,
                      relief="solid", bd=1, bg=C["ebg"], fg=C["fgb"]).pack(
            side="left", padx=(0, 8))

        tkinter.Label(cfg, text="设备名", font=utils.FONT_SMALL,
                      bg=C["bg"], fg=C["fgm"]).pack(side="left", padx=(0, 4))
        self.var_name = tkinter.StringVar(
            value=getattr(state, "NETLINK_DEVICE_NAME", "") or "")
        tkinter.Entry(cfg, textvariable=self.var_name, width=14, font=utils.FONT_BODY,
                      relief="solid", bd=1, bg=C["ebg"], fg=C["fgb"]).pack(
            side="left", padx=(0, 8))

        tkinter.Label(cfg, text="手动添加", font=utils.FONT_SMALL,
                      bg=C["bg"], fg=C["fgm"]).pack(side="left", padx=(0, 4))
        self.var_add = tkinter.StringVar(value="")
        tkinter.Entry(cfg, textvariable=self.var_add, width=16, font=utils.FONT_BODY,
                      relief="solid", bd=1, bg=C["ebg"], fg=C["fgb"]).pack(
            side="left", padx=(0, 4))
        tkinter.Button(cfg, text="添加", font=utils.FONT_SMALL,
                       bg=C["bgc"], fg=C["fgb"], relief="flat", bd=1,
                       cursor="hand2", padx=8, pady=1,
                       command=self._on_add_peer).pack(side="left", padx=(0, 8))
        tkinter.Button(cfg, text="应用", font=utils.FONT_SMALL,
                       bg=C["ac"], fg="white", activebackground=C["ach"],
                       activeforeground="white", relief="flat", bd=1,
                       cursor="hand2", padx=10, pady=1,
                       command=self._on_apply).pack(side="left")

        # ── 2. 设备列表 ──
        mid = tkinter.Frame(win, bg=C["bg"])
        mid.grid(row=2, column=0, sticky="nsew", padx=10, pady=(0, 4))
        mid.columnconfigure(0, weight=1)

        cols = ("dev", "state", "script", "progress", "latency", "perm")
        # Phase3-2: 设备表支持多选（批量下发）；主选中取 selection()[0]
        self.tv = ttk.Treeview(mid, columns=cols, show="headings", height=6,
                               selectmode="extended")
        for cid, text, width in (
                ("dev", "设备", 150), ("state", "状态", 90),
                ("script", "当前脚本", 160), ("progress", "进度", 150),
                ("latency", "延迟", 60), ("perm", "权限", 70)):
            self.tv.heading(cid, text=text)
            self.tv.column(cid, width=width, anchor="w")
        # §4.5 降级: show="headings" 无 #0 列 → 选中行加粗 (整行 acl 底由 style.map)
        utils.bind_sel_bold(self.tv)
        self.tv.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(mid, orient="vertical", command=self.tv.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.tv.configure(yscrollcommand=sb.set)
        self.tv.tag_configure("run", foreground=utils.themed("sc", "#10b981"))
        self.tv.tag_configure("pause", foreground=utils.themed("wn", "#f59e0b"))
        self.tv.tag_configure("idle", foreground=utils.themed("fgm", "#718096"))
        self.tv.tag_configure("off", foreground=utils.themed("fgm", "#718096"))
        self.tv.tag_configure("unauth", foreground=utils.themed("fgm", "#718096"))
        self.tv.bind("<<TreeviewSelect>>", self._on_select)

        # ── 3. 选中设备详情 ──
        det = tkinter.Frame(win, bg=C["bg"])
        det.grid(row=3, column=0, sticky="ew", padx=10, pady=(0, 4))
        det.columnconfigure(1, weight=1)
        tkinter.Label(det, text="进度", font=utils.FONT_SMALL,
                      bg=C["bg"], fg=C["fgm"]).grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.pb_detail = ttk.Progressbar(det, mode="determinate", maximum=1, value=0)
        self.pb_detail.grid(row=0, column=1, sticky="ew")
        self.lbl_detail = tkinter.Label(det, text="未选择设备", font=utils.FONT_SMALL,
                                        bg=C["bg"], fg=C["fgm"], anchor="w")
        self.lbl_detail.grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 0))
        self.btn_pair_peer = tkinter.Button(det, text="⇄ 配对", font=utils.FONT_SMALL,
                                            bg=C["bgc"], fg=C["fgb"],
                                            activebackground=C["acl"], relief="flat",
                                            bd=1, cursor="hand2", padx=10, pady=2,
                                            state="disabled", command=self._on_pair_peer)
        self.btn_pair_peer.grid(row=0, column=2, rowspan=2, sticky="e", padx=(8, 0))
        try:
            utils.attach_tooltip(self.btn_pair_peer,
                                 "对选中的未认证设备发起配对 (需输入对方配对码)")
        except Exception:
            pass
        # Phase3-1b: 查看远端脚本列表并可直接远程运行 (需 perm=script)
        self.btn_remote_scripts = tkinter.Button(det, text="▭ 远端脚本",
                                                 font=utils.FONT_SMALL, bg=C["bgc"],
                                                 fg=C["fgb"], activebackground=C["acl"],
                                                 relief="flat", bd=1, cursor="hand2",
                                                 padx=10, pady=2, state="disabled",
                                                 command=self._on_remote_scripts)
        self.btn_remote_scripts.grid(row=0, column=3, rowspan=2, sticky="e", padx=(8, 0))
        try:
            utils.attach_tooltip(self.btn_remote_scripts, self._tip_script)
        except Exception:
            pass

        # Phase3-2: 定时任务状态（消费 TOPIC_PEER_SCHED，只读展示）
        self.lbl_sched = tkinter.Label(det, text="定时任务：—", font=utils.FONT_SMALL,
                                       bg=C["bg"], fg=C["fgm"], anchor="w")
        self.lbl_sched.grid(row=2, column=0, columnspan=4, sticky="w", pady=(2, 0))

        # ── 3b. 已配对设备（本机授予的远程权限）──
        self.lf_paired = ttk.LabelFrame(win, text="已配对设备（本机授予的远程权限）")
        self.lf_paired.grid(row=4, column=0, sticky="ew", padx=10, pady=(0, 4))
        self.lf_paired.columnconfigure(0, weight=1)
        pcols = ("name", "fp", "perm", "last")
        self.tv_paired = ttk.Treeview(self.lf_paired, columns=pcols,
                                      show="headings", height=4)
        for cid, text, width in (("name", "设备名", 140), ("fp", "指纹", 230),
                                 ("perm", "权限", 110), ("last", "最近连接", 140)):
            self.tv_paired.heading(cid, text=text)
            self.tv_paired.column(cid, width=width, anchor="w")
        # §4.5 降级: show="headings" 无 #0 列 → 选中行加粗
        utils.bind_sel_bold(self.tv_paired)
        self.tv_paired.grid(row=0, column=0, sticky="ew", padx=(6, 0), pady=(2, 0))
        psb = ttk.Scrollbar(self.lf_paired, orient="vertical",
                            command=self.tv_paired.yview)
        psb.grid(row=0, column=1, sticky="ns", pady=(2, 0))
        self.tv_paired.configure(yscrollcommand=psb.set)
        self.tv_paired.bind("<<TreeviewSelect>>", self._on_paired_select)
        self.lbl_paired_empty = tkinter.Label(self.lf_paired, text="暂无已配对设备",
                                              font=utils.FONT_SMALL, bg=C["bgc"],
                                              fg=C["fgm"])
        self.lbl_paired_empty.grid(row=1, column=0, sticky="w", padx=8, pady=(0, 2))
        pctl = tkinter.Frame(self.lf_paired, bg=C["bgc"])
        pctl.grid(row=2, column=0, columnspan=2, sticky="ew", padx=6, pady=(0, 4))
        tkinter.Label(pctl, text="权限", font=utils.FONT_SMALL, bg=C["bgc"],
                      fg=C["fgm"]).pack(side="left", padx=(0, 4))
        self.var_perm = tkinter.StringVar(value="仅观察")
        self.cmb_perm = ttk.Combobox(pctl, textvariable=self.var_perm,
                                     values=("仅观察", "允许操控", "允许接收脚本"),
                                     state="readonly", width=12)
        self.cmb_perm.pack(side="left", padx=(0, 6))
        self.btn_perm_apply = tkinter.Button(pctl, text="应用", font=utils.FONT_SMALL,
                                             bg=C["ac"], fg="white",
                                             activebackground=C["ach"],
                                             activeforeground="white", relief="flat",
                                             bd=1, cursor="hand2", padx=10, pady=1,
                                             state="disabled", command=self._on_perm_apply)
        self.btn_perm_apply.pack(side="left", padx=(0, 6))
        self.btn_peer_remove = tkinter.Button(pctl, text="移除", font=utils.FONT_SMALL,
                                              bg=C["dg"], fg="white",
                                              activebackground=C["dg"],
                                              activeforeground="white", relief="flat",
                                              bd=1, cursor="hand2", padx=10, pady=1,
                                              state="disabled", command=self._on_peer_remove)
        self.btn_peer_remove.pack(side="left")

        # ── 4. 日志面板 ──
        logfr = tkinter.Frame(win, bg=C["bg"])
        logfr.grid(row=5, column=0, sticky="nsew", padx=10, pady=(0, 4))
        logfr.columnconfigure(0, weight=1)
        logfr.rowconfigure(0, weight=1)
        self.txt_log = tkinter.Text(logfr, height=8, font=utils.FONT_LOG,
                                    bg=C["logbg"], fg=C["logfg"], relief="solid", bd=1,
                                    wrap="none", state="disabled")
        self.txt_log.grid(row=0, column=0, sticky="nsew")
        logsb = ttk.Scrollbar(logfr, orient="vertical", command=self.txt_log.yview)
        logsb.grid(row=0, column=1, sticky="ns")
        self.txt_log.configure(yscrollcommand=logsb.set)
        self.txt_log.tag_configure("err", foreground=utils.themed("err", "#ef4444"))
        self.txt_log.tag_configure("wn", foreground=utils.themed("wn", "#f59e0b"))

        # ── 5. 操控按钮组 (Phase 2-2b: 按选中设备权限/运行态启用) ──
        ctl = tkinter.Frame(win, bg=C["bg"])
        ctl.grid(row=6, column=0, sticky="ew", padx=10, pady=(0, 4))

        def _mk_ctl(text, command):
            b = tkinter.Button(ctl, text=text, font=utils.FONT_BUTTON,
                               bg=C["bgc"], fg=C["fgm"], relief="flat", bd=1,
                               padx=10, pady=3, state="disabled", command=command)
            b.pack(side="left", padx=(0, 6))
            return b

        self.btn_run = _mk_ctl("[▶ 远程运行]", self._on_remote_run)
        self.btn_pause = _mk_ctl("[⏸ 暂停]", self._on_remote_pause)
        self.btn_stop = _mk_ctl("[⏹ 停止]", self._on_remote_stop)
        self.btn_script = _mk_ctl("[推脚本]", self._on_push_script)
        # Phase4-1: [◎ 截图]（需「允许操控」权限，control/script 均可）
        self.btn_shot = _mk_ctl("[◎ 截图]", self._on_remote_shot)
        try:
            utils.attach_tooltip(self.btn_run, self._tip_run)
            utils.attach_tooltip(self.btn_pause, self._tip_pause)
            utils.attach_tooltip(self.btn_stop, self._tip_stop)
            utils.attach_tooltip(self.btn_script, self._tip_script)
            utils.attach_tooltip(self.btn_shot, self._tip_shot)
        except Exception:
            pass

        self.btn_audit = tkinter.Button(ctl, text="[≡ 审计日志]",
                                        font=utils.FONT_BUTTON, bg=C["bgc"],
                                        fg=C["fgb"], activebackground=C["acl"],
                                        relief="flat", bd=1, cursor="hand2",
                                        padx=10, pady=3, command=self._on_audit)
        self.btn_audit.pack(side="left", padx=(12, 0))
        try:
            utils.attach_tooltip(self.btn_audit, "查看远程操控审计日志（只读）")
        except Exception:
            pass

        # Phase4-2: [⊕ 网页面板]（浏览器只读监控面板，默认关闭）
        self.btn_web = tkinter.Button(ctl, text="[⊕ 网页面板]",
                                      font=utils.FONT_BUTTON, bg=C["bgc"],
                                      fg=C["fgb"], activebackground=C["acl"],
                                      relief="flat", bd=1, cursor="hand2",
                                      padx=10, pady=3, command=self._on_web_panel)
        self.btn_web.pack(side="left", padx=(12, 0))
        try:
            utils.attach_tooltip(self.btn_web,
                                 "浏览器只读监控面板：手机/平板可查看进度（默认关闭，需启用）")
        except Exception:
            pass

        # ── 6. 远程指令结果 ──
        self.lf_cmd = ttk.LabelFrame(win, text="远程指令")
        self.lf_cmd.grid(row=7, column=0, sticky="ew", padx=10, pady=(0, 4))
        self.lf_cmd.columnconfigure(0, weight=1)
        ccols = ("time", "dev", "cmd", "result")
        self.tv_cmd = ttk.Treeview(self.lf_cmd, columns=ccols, show="headings",
                                   height=5)
        for cid, text, width in (("time", "时间", 90), ("dev", "设备", 150),
                                 ("cmd", "指令", 110), ("result", "结果", 380)):
            self.tv_cmd.heading(cid, text=text)
            self.tv_cmd.column(cid, width=width, anchor="w")
        # §4.5 降级: show="headings" 无 #0 列 → 选中行加粗
        utils.bind_sel_bold(self.tv_cmd)
        self.tv_cmd.grid(row=0, column=0, sticky="ew", padx=(6, 0), pady=(2, 0))
        csb = ttk.Scrollbar(self.lf_cmd, orient="vertical", command=self.tv_cmd.yview)
        csb.grid(row=0, column=1, sticky="ns", pady=(2, 0))
        self.tv_cmd.configure(yscrollcommand=csb.set)
        self.tv_cmd.tag_configure("cmdok", foreground=utils.themed("sc", "#10b981"))
        self.tv_cmd.tag_configure("cmdfail", foreground=utils.themed("err", "#ef4444"))
        self.tv_cmd.tag_configure("cmdgray", foreground=utils.themed("fgm", "#718096"))
        self.lbl_cmd_empty = tkinter.Label(self.lf_cmd, text="暂无远程指令",
                                           font=utils.FONT_SMALL, bg=C["bgc"],
                                           fg=C["fgm"])
        self.lbl_cmd_empty.grid(row=1, column=0, sticky="w", padx=8, pady=(0, 2))

        # ── 8. 传输 (Phase3-1b: TOPIC_TRANSFER 进度) ──
        self.lf_xfer = ttk.LabelFrame(win, text="传输")
        self.lf_xfer.grid(row=8, column=0, sticky="ew", padx=10, pady=(0, 4))
        self.lf_xfer.columnconfigure(0, weight=1)
        xcols = ("dev", "file", "progress", "state")
        self.tv_xfer = ttk.Treeview(self.lf_xfer, columns=xcols, show="headings",
                                    height=4)
        for cid, text, width in (("dev", "设备", 150), ("file", "文件", 190),
                                 ("progress", "进度", 190), ("state", "状态", 230)):
            self.tv_xfer.heading(cid, text=text)
            self.tv_xfer.column(cid, width=width, anchor="w")
        # §4.5 降级: show="headings" 无 #0 列 → 选中行加粗
        utils.bind_sel_bold(self.tv_xfer)
        self.tv_xfer.grid(row=0, column=0, sticky="ew", padx=(6, 0), pady=(2, 0))
        xsb = ttk.Scrollbar(self.lf_xfer, orient="vertical", command=self.tv_xfer.yview)
        xsb.grid(row=0, column=1, sticky="ns", pady=(2, 0))
        self.tv_xfer.configure(yscrollcommand=xsb.set)
        self.tv_xfer.tag_configure("xfer_run", foreground=utils.themed("ac", "#2563eb"))
        self.tv_xfer.tag_configure("xfer_ok", foreground=utils.themed("sc", "#10b981"))
        self.tv_xfer.tag_configure("xfer_fail", foreground=utils.themed("err", "#ef4444"))
        self.lbl_xfer_empty = tkinter.Label(self.lf_xfer, text="暂无传输",
                                            font=utils.FONT_SMALL, bg=C["bgc"],
                                            fg=C["fgm"])
        self.lbl_xfer_empty.grid(row=1, column=0, sticky="w", padx=8, pady=(0, 2))

        # ── 9. 底部状态行 ──
        self.lbl_bottom = tkinter.Label(win, text="互联未启动", font=utils.FONT_SMALL,
                                        bg=C["bg"], fg=C["fgm"], anchor="w")
        self.lbl_bottom.grid(row=9, column=0, sticky="ew", padx=10, pady=(0, 2))
        # Phase4-3 传输加密状态（只读：明文 / TLS（指纹 N 个））
        self.lbl_tls = tkinter.Label(win, text="传输：明文", font=utils.FONT_SMALL,
                                     bg=C["bg"], fg=C["fgm"], anchor="w")
        self.lbl_tls.grid(row=10, column=0, sticky="ew", padx=10, pady=(0, 8))

        self._schedule_pump(PUMP_INTERVAL_MS)

    # ── 轮询泵 (主线程消费 bus) ─────────────────────────────────────
    def _schedule_pump(self, delay):
        try:
            self._pump_id = self.root.after(delay, self._pump)
        except Exception:
            self._pump_id = None

    def _pump(self):
        self._pump_id = None
        if not self.alive():
            return
        try:
            nl = _nl()
            bus = nl.get_bus() if nl is not None else None
            if bus is not None:
                try:
                    events = bus.drain(500)
                except Exception:
                    events = []
                for topic, payload in events:
                    try:
                        self._on_event(topic, payload)
                    except Exception:
                        pass
            self._refresh()
            if self._web_dlg_alive():
                self._web_refresh()
        except Exception:
            pass
        finally:
            if self.alive():
                self._schedule_pump(PUMP_INTERVAL_MS)

    # ── 事件消费 ────────────────────────────────────────────────────
    def _on_event(self, topic, payload):
        if topic == _transfer_topic():
            if isinstance(payload, dict):
                self._on_transfer(payload)
            return
        if topic == _screenshot_topic():
            if isinstance(payload, dict):
                self._on_screenshot(payload)
            return
        T = _topics()
        if not T or not isinstance(payload, dict):
            return
        nid = str(payload.get("node_id") or "")
        if topic == T["peer"]:
            if not nid:
                return
            prev = self._peers.get(nid) or {}
            self._peers[nid] = {
                "name": payload.get("name") or prev.get("name") or "",
                "host": payload.get("host") or prev.get("host") or "",
                "port": payload.get("port") or prev.get("port") or 0,
                "version": payload.get("version") or prev.get("version") or "",
                "online": bool(payload.get("online", True)),
            }
        elif topic == T["state"]:
            if nid:
                self._states[nid] = dict(payload)
        elif topic == T["sched"]:
            if nid:
                self._scheds[nid] = dict(payload)
        elif topic == T["log"]:
            self._on_log(payload)
        elif topic == T["status"]:
            self._status_msg = str(payload.get("msg") or "")
            self._status_level = str(payload.get("level") or "info")
            try:
                log1("[NetLink] {}".format(self._status_msg),
                     "warning" if self._status_level != "info" else "info")
            except Exception:
                pass
        elif topic == T["auth"]:
            self._on_auth(payload)
        elif topic == T["cmd_result"]:
            self._on_cmd_result(payload)

    def _on_log(self, payload):
        nid = str(payload.get("node_id") or "")
        if not nid or nid != self._sel:
            return
        seq = payload.get("seq")
        full = bool(payload.get("full"))
        if full:
            self._log_clear()
        else:
            last = self._logseq.get(nid)
            if seq is not None and last is not None and seq <= last:
                return
        if seq is not None:
            self._logseq[nid] = seq
        self._log_append(payload.get("lines") or [])

    def _log_clear(self):
        try:
            self.txt_log.config(state="normal")
            self.txt_log.delete("1.0", "end")
            self.txt_log.config(state="disabled")
        except Exception:
            pass

    def _log_append(self, lines):
        if not lines:
            return
        try:
            txt = self.txt_log
            txt.config(state="normal")
            for ln in lines:
                if not isinstance(ln, dict):
                    continue
                ts = str(ln.get("ts") or "")
                tag = str(ln.get("tag") or "")
                try:
                    level = int(ln.get("level") or 1)
                except Exception:
                    level = 1
                msg = str(ln.get("msg") or "")
                text = "{} {}{}".format(ts, ("[{}] ".format(tag)) if tag else "", msg)
                if level >= 3:
                    txt.insert("end", text + "\n", "err")
                elif level >= 2:
                    txt.insert("end", text + "\n", "wn")
                else:
                    txt.insert("end", text + "\n")
            # 总行数上限 2000: 超出删最前 500 行
            try:
                nlines = int(txt.index("end-1c").split(".")[0])
                if nlines > MAX_LOG_LINES:
                    txt.delete("1.0", "{}.0".format(TRIM_LOG_LINES + 1))
            except Exception:
                pass
            txt.see("end")
            txt.config(state="disabled")
        except Exception:
            pass

    # ── 界面刷新 ────────────────────────────────────────────────────
    def _refresh(self):
        if not self.alive():
            return
        nl = _nl()
        running = False
        name = ""
        node = None
        if nl is not None:
            try:
                running = bool(nl.is_running())
            except Exception:
                running = False
            try:
                node = nl.get_node()
                if node is not None:
                    name = (node.info or {}).get("name") or ""
            except Exception:
                node = None
                name = ""
        self._running = running
        if not name:
            name = getattr(state, "NETLINK_DEVICE_NAME", "") or ""
        if not name:
            try:
                name = socket.gethostname()
            except Exception:
                name = "ACRPA"
        port = getattr(state, "NETLINK_PORT", DEFAULT_TCP_PORT)
        try:
            self.lbl_top.config(text="本机名: {}  |  TCP :{}  |  {}".format(
                name, port, "● 运行中" if running else "○ 已停止"))
            self.btn_toggle.config(text="停止互联" if running else "启动互联")
        except Exception:
            pass
        self._refresh_pair_label(node)
        self._refresh_table()
        self._refresh_detail()
        self._refresh_paired()
        self._update_control_buttons()
        online = sum(1 for p in self._peers.values() if p.get("online"))
        msg = self._status_msg or ("互联未启动" if not running else "已就绪")
        text = "{}   |   设备 {} / 在线 {}".format(msg, len(self._peers), online)
        if self._cmd_summary:
            text = "{}   |   {}".format(text, self._cmd_summary)
        if self._xfer_summary:
            text = "{}   |   {}".format(text, self._xfer_summary)
        try:
            self.lbl_bottom.config(text=text)
        except Exception:
            pass
        self._refresh_tls_label(node, running)

    def _refresh_tls_label(self, node, running):
        """刷新「传输」状态行：明文 / TLS（指纹 N 个）；未就绪→error 配色。"""
        try:
            if not running or node is None or not hasattr(node, "tls_status"):
                self.lbl_tls.config(text="传输：—", fg=utils.themed("fgm", "#718096"))
                return
            st = node.tls_status() or {}
            if not st.get("enabled"):
                self.lbl_tls.config(text="传输：明文", fg=utils.themed("fgm", "#718096"))
                return
            if not st.get("ready"):
                self.lbl_tls.config(
                    text="传输：TLS 未就绪 — {}".format(st.get("reason") or "unknown"),
                    fg=utils.themed("err", "#ef4444"))
                return
            self.lbl_tls.config(
                text="传输：TLS（指纹 {} 个）".format(int(st.get("pins") or 0)),
                fg=utils.themed("sc", "#22c55e"))
        except Exception:
            pass

    def _refresh_pair_label(self, node):
        """顶部状态条配对码提示 (被控端, 随 _pump 更新)。"""
        txt = "配对码 —"
        fg = utils.themed("fgm", "#718096")
        try:
            st = node.pair_window_status() if node is not None else None
            if st and st.get("active"):
                pin = str(st.get("pin") or "")
                left = int(st.get("expires_in") or 0)
                left = left if left > 0 else 0
                txt = "配对码 {}  (剩余 {:02d}:{:02d})".format(
                    " ".join(pin), left // 60, left % 60)
                fg = utils.themed("ac", "#2563eb")
        except Exception:
            pass
        try:
            self.lbl_pair.config(text=txt, fg=fg)
        except Exception:
            pass

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

    def _perm_text(self, nid):
        """设备表权限列文本: 未运行→—; 未认证→未认证; 已认证→中文标签。"""
        if not self._running:
            return "—"
        perm = self._authed.get(str(nid or ""))
        if perm is None:
            return "未认证"
        return _perm_labels().get(perm, "仅观察")

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

    def _refresh_sched_label(self):
        """刷新选中设备详情区「定时任务」文本（无数据 → 定时任务：—）。"""
        try:
            self.lbl_sched.config(text=self._sched_text())
        except Exception:
            pass

    def _sched_text(self):
        """TOPIC_PEER_SCHED → 详情区文本。

        无数据 → "定时任务：—"；否则
        "定时任务：<启用|未启用> · 下次运行 <next_run 或 未安排/—> （N 个任务）"。
        """
        nid = self._sel
        if not nid:
            return "定时任务：—"
        s = self._scheds.get(str(nid))
        if not isinstance(s, dict):
            return "定时任务：—"
        enabled = bool(s.get("enabled"))
        next_run = str(s.get("next_run") or "")
        try:
            cnt = int(s.get("task_count") or 0)
        except Exception:
            cnt = 0
        if not next_run:
            nr = "未安排" if enabled else "—"
        else:
            nr = next_run
        return "定时任务：{} · 下次运行 {} （{} 个任务）".format(
            "启用" if enabled else "未启用", nr, cnt)

    @staticmethod
    def _dev_text(info):
        name = info.get("name") or ""
        host = info.get("host") or ""
        port = info.get("port") or ""
        addr = "{}:{}".format(host, port) if host else str(port or "")
        if name and addr:
            return "{}({})".format(name, addr)
        return name or addr or "—"

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

    # ── Phase2-1b 配对 / 权限 / 已配对设备 ───────────────────────────
    def _get_node(self):
        """取当前 netlink 节点；未启动返回 None (不抛)。"""
        try:
            nl = _nl()
            return nl.get_node() if nl is not None else None
        except Exception:
            return None

    def _set_status(self, text, level="info"):
        """更新底部状态行文案 (仅主线程调用)。"""
        self._status_msg = str(text or "")
        self._status_level = str(level or "info")
        try:
            self.lbl_bottom.config(text=self._status_msg)
        except Exception:
            pass

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

    # ── Phase2-2b 远程操控 ──────────────────────────────────────────
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

    def _tip_run(self):
        return self._action_reason("run")

    def _tip_pause(self):
        return self._action_reason("pause")

    def _tip_stop(self):
        return self._action_reason("stop")

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
            self.btn_pause.config(text=("[▶ 恢复]" if s.get("paused") else "[⏸ 暂停]"),
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

    # ── Phase4-1 远程截图 ────────────────────────────────────────────
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

    def _tip_shot(self):
        """[◎ 截图] 动态悬停提示（可用时返回空串）。"""
        return self._shot_state()[1]

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

    def _shot_dlg_alive(self):
        try:
            return self._shot_dlg is not None and bool(self._shot_dlg.winfo_exists())
        except Exception:
            return False

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
                dlg.geometry("{}x{}".format(w, h))
                dlg.configure(bg=C["bg"])
                dlg.transient(self.win)
                self._set_icon(dlg)
                dlg.protocol("WM_DELETE_WINDOW", self._close_shot_dialog)
                dlg.columnconfigure(0, weight=1)
                dlg.rowconfigure(1, weight=1)
            except Exception:
                pass
            self._shot_info = tkinter.Label(dlg, text="", font=utils.FONT_SMALL,
                                            bg=C["bg"], fg=C["fgm"], anchor="w")
            self._shot_info.grid(row=0, column=0, sticky="ew", padx=10, pady=(8, 2))
            self._shot_img_lbl = tkinter.Label(dlg, text="", bg=C["logbg"],
                                               fg=C["logfg"], anchor="center")
            self._shot_img_lbl.grid(row=1, column=0, sticky="nsew",
                                    padx=10, pady=(0, 6))
            self._shot_bar = tkinter.Frame(dlg, bg=C["bg"])
            self._shot_bar.grid(row=2, column=0, sticky="e", padx=10, pady=(0, 10))
            tkinter.Button(self._shot_bar, text="[刷新]", font=utils.FONT_BUTTON,
                           bg=C["bgc"], fg=C["fgb"], activebackground=C["acl"],
                           relief="flat", bd=1, cursor="hand2", padx=10, pady=3,
                           command=self._shot_refresh).pack(side="left", padx=(0, 6))
            tkinter.Button(self._shot_bar, text="[保存为…]", font=utils.FONT_BUTTON,
                           bg=C["bgc"], fg=C["fgb"], activebackground=C["acl"],
                           relief="flat", bd=1, cursor="hand2", padx=10, pady=3,
                           command=self._shot_save).pack(side="left", padx=(0, 6))
            tkinter.Button(self._shot_bar, text="[关闭]", font=utils.FONT_BUTTON,
                           bg=C["bgc"], fg=C["fgb"], activebackground=C["acl"],
                           relief="flat", bd=1, cursor="hand2", padx=10, pady=3,
                           command=self._close_shot_dialog).pack(side="left")
        else:
            try:
                self._shot_dlg.title("远程截图 — {}".format(dev))
            except Exception:
                pass
        self._shot_render(data_b64, width, height, nbytes, ts)

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
        # 1) PIL 内嵌显示
        try:
            import io as _io
            import base64 as _b64
            from PIL import Image, ImageTk
            raw = _b64.b64decode(data_b64)
            img = Image.open(_io.BytesIO(raw))
            photo = ImageTk.PhotoImage(img)
            self._shot_img = photo           # 保活，避免被 GC 白屏
            self._shot_img_lbl.config(image=photo, text="")
            self._shot_cleanup_temp()
            self._shot_open_btn_pack(False)
            return
        except Exception:
            pass
        # 2) 降级：写临时文件 + [打开文件]
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

    def _shot_open_btn_pack(self, show):
        """降级路径下显示/隐藏 [打开文件] 按钮。"""
        try:
            if show:
                if self._shot_open_btn is None:
                    C = utils.C
                    self._shot_open_btn = tkinter.Button(
                        self._shot_bar, text="[打开文件]", font=utils.FONT_BUTTON,
                        bg=C["bgc"], fg=C["fgb"], activebackground=C["acl"],
                        relief="flat", bd=1, cursor="hand2", padx=10, pady=3,
                        command=self._shot_open_file)
                self._shot_open_btn.pack(side="left", padx=(6, 0))
            elif self._shot_open_btn is not None:
                self._shot_open_btn.pack_forget()
        except Exception:
            pass

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

    def _shot_cleanup_temp(self):
        """删除降级路径的临时文件并清空引用。"""
        p = self._shot_path
        self._shot_path = None
        if p:
            try:
                os.remove(p)
            except Exception:
                pass

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

    def _confirm_action(self, title, message):
        """本地二次确认。测试可替换本方法以绕过人工弹窗。"""
        try:
            from tkinter import messagebox
            return bool(messagebox.askyesno(title, message, parent=self.win))
        except Exception:
            return True

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

    def _on_remote_pause(self):
        """[⏸ 暂停]/[▶ 恢复]：按选中设备 paused 状态切换发送类型。"""
        nid = self._sel
        if not nid:
            return
        st = self._states.get(nid) or {}
        self._send_cmd(nid, ("resume" if st.get("paused") else "pause"))

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

    # ── 远程指令结果展示 ────────────────────────────────────────────
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

    # ── 审计日志（只读展示）───────────────────────────────────────────
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
            dlg.geometry("760x520")
            dlg.configure(bg=C["bg"])
            dlg.transient(self.win)
            self._set_icon(dlg)
            dlg.protocol("WM_DELETE_WINDOW", self._close_audit_dialog)
        except Exception:
            pass
        try:
            self._audit_path_lbl = tkinter.Label(
                dlg, text="审计文件：{}".format(apath or "—"), font=utils.FONT_SMALL,
                bg=C["bg"], fg=C["fgm"], anchor="w")
            self._audit_path_lbl.grid(row=0, column=0, columnspan=2, sticky="ew",
                                      padx=10, pady=(8, 2))
            txt = tkinter.Text(dlg, font=utils.FONT_LOG, bg=C["logbg"],
                               fg=C["logfg"], relief="solid", bd=1, wrap="none",
                               state="normal")
            txt.grid(row=1, column=0, sticky="nsew", padx=(10, 0), pady=(0, 6))
            sb = ttk.Scrollbar(dlg, orient="vertical", command=txt.yview)
            sb.grid(row=1, column=1, sticky="ns", padx=(0, 10), pady=(0, 6))
            txt.configure(yscrollcommand=sb.set)
            dlg.columnconfigure(0, weight=1)
            dlg.rowconfigure(1, weight=1)
            self._audit_text = txt
            bar = tkinter.Frame(dlg, bg=C["bg"])
            bar.grid(row=2, column=0, columnspan=2, sticky="e", padx=10, pady=(0, 10))
            tkinter.Button(bar, text="刷新", font=utils.FONT_BUTTON, bg=C["bgc"],
                           fg=C["fgb"], activebackground=C["acl"], relief="flat",
                           bd=1, cursor="hand2", padx=10, pady=3,
                           command=lambda: self._audit_refresh(dlg)).pack(
                side="left", padx=(0, 6))
            tkinter.Button(bar, text="关闭", font=utils.FONT_BUTTON, bg=C["bgc"],
                           fg=C["fgb"], activebackground=C["acl"], relief="flat",
                           bd=1, cursor="hand2", padx=10, pady=3,
                           command=self._close_audit_dialog).pack(side="left")
        except Exception:
            pass
        self._audit_fill(dlg, lines, apath)
        return dlg

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

    # ── 配对码对话框 (被控端) ───────────────────────────────────────
    def _on_pair_code(self):
        """顶部「配对码」按钮: 未启动则提示, 否则打开配对码对话框。"""
        if self._get_node() is None:
            self._set_status("请先启动互联", "warning")
            return
        self._open_pair_dialog()

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
                padx=14, pady=(12, 4), anchor="w")
            lbl_pin = tkinter.Label(dlg, text="—", font=("Consolas", 28, "bold"),
                                    bg=C["bg"], fg=C["ac"])
            lbl_pin.pack(padx=14, pady=(2, 2))
            lbl_left = tkinter.Label(dlg, text="剩余 --:--", font=utils.FONT_BODY,
                                     bg=C["bg"], fg=C["fgm"])
            lbl_left.pack(padx=14, pady=(0, 6))
            btns = tkinter.Frame(dlg, bg=C["bg"])
            btns.pack(padx=14, pady=(0, 12))
            tkinter.Button(btns, text="生成/重新生成", font=utils.FONT_BUTTON,
                           bg=C["ac"], fg="white", activebackground=C["ach"],
                           activeforeground="white", relief="flat", bd=1,
                           cursor="hand2", padx=10, pady=3,
                           command=lambda: self._pair_generate(lbl_pin, lbl_left)).pack(
                side="left", padx=(0, 6))
            tkinter.Button(btns, text="关闭配对", font=utils.FONT_BUTTON, bg=C["bgc"],
                           fg=C["fgb"], activebackground=C["acl"], relief="flat",
                           bd=1, cursor="hand2", padx=10, pady=3,
                           command=lambda: self._pair_close(lbl_pin, lbl_left)).pack(
                side="left", padx=(0, 6))
            tkinter.Button(btns, text="关闭窗口", font=utils.FONT_BUTTON, bg=C["bgc"],
                           fg=C["fgb"], activebackground=C["acl"], relief="flat",
                           bd=1, cursor="hand2", padx=10, pady=3,
                           command=self._close_pair_dialog).pack(side="left")
            self._pair_tick(lbl_pin, lbl_left)
        except Exception:
            pass
        return dlg

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

    # ── 控制端配对入口 (选中设备) ───────────────────────────────────
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

    # ── 已配对设备区域 ─────────────────────────────────────────────
    @staticmethod
    def _fmt_time(ts):
        try:
            ts = int(ts)
            if ts > 0:
                return time.strftime("%H:%M:%S", time.localtime(ts))
        except Exception:
            pass
        return "—"

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

    # ── Phase3-1b 脚本分发 UI ───────────────────────────────────────
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

    def _tip_script(self):
        """[推脚本]/[▭ 远端脚本] 动态悬停提示（可用时返回空串）。"""
        return self._script_state()[1]

    # ── Phase3-2 批量下发门控 ────────────────────────────────────────
    def _selected_node_ids(self):
        """设备表当前选中集合对应的 node_id 列表（按选中顺序，去重）。"""
        out = []
        try:
            iids = list(self.tv.selection())
        except Exception:
            iids = []
        rev = {}
        for nid, iid in self._items.items():
            rev[iid] = nid
        for iid in iids:
            nid = rev.get(iid)
            if nid and (nid not in out):
                out.append(nid)
        return out

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

    # ── Phase4-2 浏览器只读监控面板（对话框）────────────────────────────
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
            dlg.geometry("560x420")
            dlg.configure(bg=C["bg"])
            dlg.transient(self.win)
            self._set_icon(dlg)
            dlg.protocol("WM_DELETE_WINDOW", self._close_web_dialog)
        except Exception:
            pass
        dlg.columnconfigure(0, weight=1)

        self._web_status_lbl = tkinter.Label(dlg, text="面板状态：已停止",
            font=utils.FONT_BODY, bg=C["bg"], fg=C["fgt"], anchor="w")
        self._web_status_lbl.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 4))

        self._web_btn_toggle = tkinter.Button(dlg, text="启用",
            font=utils.FONT_BUTTON, bg=C["ac"], fg="white",
            activebackground=C["ach"], activeforeground="white",
            relief="flat", bd=1, cursor="hand2", padx=12, pady=3,
            command=self._on_web_toggle)
        self._web_btn_toggle.grid(row=1, column=0, sticky="w", padx=12, pady=(0, 6))

        tkinter.Label(dlg, text="访问地址（含令牌，等同密码，请勿外泄）",
            font=utils.FONT_SMALL, bg=C["bg"], fg=C["fgm"], anchor="w").grid(
            row=2, column=0, sticky="w", padx=12, pady=(0, 2))
        self._web_url_var = tkinter.StringVar(value="")
        tkinter.Entry(dlg, textvariable=self._web_url_var, font=utils.FONT_BODY,
            relief="solid", bd=1, bg=C["ebg"], fg=C["fgb"], state="readonly").grid(
            row=3, column=0, sticky="ew", padx=12)

        btns = tkinter.Frame(dlg, bg=C["bg"])
        btns.grid(row=4, column=0, sticky="ew", padx=12, pady=(8, 4))
        tkinter.Button(btns, text="复制地址", font=utils.FONT_SMALL, bg=C["bgc"],
            fg=C["fgb"], activebackground=C["acl"], relief="flat", bd=1,
            cursor="hand2", padx=10, pady=1,
            command=self._on_web_copy).pack(side="left", padx=(0, 6))
        tkinter.Button(btns, text="在浏览器打开", font=utils.FONT_SMALL, bg=C["bgc"],
            fg=C["fgb"], activebackground=C["acl"], relief="flat", bd=1,
            cursor="hand2", padx=10, pady=1,
            command=self._on_web_open).pack(side="left", padx=(0, 6))
        tkinter.Button(btns, text="重新生成令牌", font=utils.FONT_SMALL, bg=C["dg"],
            fg="white", activebackground=C["dg"], activeforeground="white",
            relief="flat", bd=1, cursor="hand2", padx=10, pady=1,
            command=self._on_web_rotate).pack(side="left")

        tkinter.Label(dlg,
            text="⚠ 默认只读；开启「有限控制」后经 HTTPS + 控制 PIN 可远程运行/停止本机脚本，请仅在可信内网启用",
            font=utils.FONT_SMALL, bg=C["bg"], fg=C["wn"], anchor="w",
            wraplength=520, justify="left").grid(
            row=5, column=0, sticky="ew", padx=12, pady=(2, 4))

        ctl = tkinter.Frame(dlg, bg=C["bg"])
        ctl.grid(row=6, column=0, sticky="ew", padx=12, pady=(0, 2))
        self._web_control_var = tkinter.BooleanVar(
            value=bool(getattr(state, "NETLINK_WEB_CONTROL", False)))
        tkinter.Checkbutton(ctl, text="启用有限控制（run/stop，强制 HTTPS）",
            variable=self._web_control_var, bg=C["bg"], fg=C["fgb"],
            activebackground=C["bg"], selectcolor=C["bgc"], anchor="w",
            font=utils.FONT_SMALL,
            command=self._on_web_control_toggle).pack(side="left", padx=(0, 8))
        self._web_pin_btn = tkinter.Button(ctl, text="设置/重置控制 PIN",
            font=utils.FONT_SMALL, bg=C["bgc"], fg=C["fgb"],
            activebackground=C["acl"], relief="flat", bd=1, cursor="hand2",
            padx=8, pady=1, command=self._on_web_pin_manage)
        self._web_pin_btn.pack(side="left")

        ctl2 = tkinter.Frame(dlg, bg=C["bg"])
        ctl2.grid(row=7, column=0, sticky="ew", padx=12, pady=(0, 4))
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
        self._web_tls_lbl.grid(row=8, column=0, sticky="ew", padx=12, pady=(0, 10))

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

    def _revert_web_control(self):
        """回退控制开关勾选（前置条件不满足时）。"""
        try:
            if self._web_control_var is not None:
                self._web_control_var.set(False)
        except Exception:
            pass

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

    def _web_dlg_alive(self):
        try:
            return self._web_dlg is not None and bool(self._web_dlg.winfo_exists())
        except Exception:
            return False

    def _warn(self, text):
        """状态行 + 告警弹窗（弹窗失败静默）。"""
        self._set_status(text, "warning")
        try:
            from tkinter import messagebox
            messagebox.showwarning("推送脚本", text, parent=self.win)
        except Exception:
            pass

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
                padx=14, pady=(12, 6), anchor="w")
            row = tkinter.Frame(dlg, bg=C["bg"])
            row.pack(fill="x", padx=14, pady=(0, 6))
            tkinter.Label(row, text="远端文件名", font=utils.FONT_SMALL, bg=C["bg"],
                          fg=C["fgm"]).pack(side="left", padx=(0, 6))
            ent = tkinter.Entry(row, textvariable=var_name, width=30,
                                font=utils.FONT_BODY, relief="solid", bd=1,
                                bg=C["ebg"], fg=C["fgb"])
            ent.pack(side="left")
            tkinter.Label(dlg, text="该文件将保存到被控端脚本目录的 received/ 子目录下",
                          font=utils.FONT_SMALL, bg=C["bg"], fg=C["fgm"],
                          anchor="w").pack(padx=14, pady=(0, 6), anchor="w")
            tkinter.Checkbutton(dlg, text="传输完成后立即运行", variable=var_run).pack(
                padx=14, pady=(0, 8), anchor="w")
            btns = tkinter.Frame(dlg, bg=C["bg"])
            btns.pack(padx=14, pady=(0, 12), anchor="e")
            tkinter.Button(btns, text="[开始传输]", font=utils.FONT_BUTTON,
                           bg=C["ac"], fg="white", activebackground=C["ach"],
                           activeforeground="white", relief="flat", bd=1,
                           cursor="hand2", padx=10, pady=3,
                           command=_on_ok).pack(side="left", padx=(0, 6))
            tkinter.Button(btns, text="[取消]", font=utils.FONT_BUTTON, bg=C["bgc"],
                           fg=C["fgb"], activebackground=C["acl"], relief="flat",
                           bd=1, cursor="hand2", padx=10, pady=3,
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

    # ── 传输列表 (TOPIC_TRANSFER) ───────────────────────────────────
    def _on_transfer(self, payload):
        """TOPIC_TRANSFER：state=list 走远端脚本对话框；其余走传输列表。"""
        st = str(payload.get("state") or "")
        if st == "list":
            self._on_script_list(payload)
        elif st in ("sending", "done", "failed"):
            self._xfer_upsert(payload)

    @staticmethod
    def _iid_exists(tree, iid):
        try:
            return bool(tree.exists(iid))
        except Exception:
            return False

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

    # ── 远端脚本对话框 ──────────────────────────────────────────────
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

    def _on_script_list(self, payload):
        """state=list 事件：打开或刷新「远端脚本」对话框。"""
        nid = str(payload.get("node_id") or "")
        scripts = payload.get("scripts")
        if not isinstance(scripts, list):
            scripts = []
        self._open_rs_dialog(nid, scripts, str(payload.get("dir") or ""))

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
            dlg.geometry("620x460")
            dlg.configure(bg=C["bg"])
            dlg.transient(self.win)
            self._set_icon(dlg)
            dlg.protocol("WM_DELETE_WINDOW", self._close_rs_dialog)
        except Exception:
            pass
        self._rs_info = tkinter.Label(dlg, text="—", font=utils.FONT_SMALL,
                                      bg=C["bg"], fg=C["fgm"], anchor="w")
        self._rs_info.grid(row=0, column=0, columnspan=2, sticky="ew",
                           padx=10, pady=(8, 4))
        rcols = ("name", "size", "mtime", "place")
        tv = ttk.Treeview(dlg, columns=rcols, show="headings", height=12)
        for cid, text, width in (("name", "名称", 240), ("size", "大小", 90),
                                 ("mtime", "修改时间", 150), ("place", "位置", 90)):
            tv.heading(cid, text=text)
            tv.column(cid, width=width, anchor="w")
        # §4.5 降级: show="headings" 无 #0 列 → 选中行加粗
        utils.bind_sel_bold(tv)
        tv.grid(row=1, column=0, sticky="nsew", padx=(10, 0), pady=(0, 6))
        rsb = ttk.Scrollbar(dlg, orient="vertical", command=tv.yview)
        rsb.grid(row=1, column=1, sticky="ns", padx=(0, 10), pady=(0, 6))
        tv.configure(yscrollcommand=rsb.set)
        tv.tag_configure("rs_empty", foreground=utils.themed("fgm", "#718096"))
        tv.bind("<Double-1>", lambda e: self._on_rs_run())
        self._rs_tree = tv
        dlg.columnconfigure(0, weight=1)
        dlg.rowconfigure(1, weight=1)
        bar = tkinter.Frame(dlg, bg=C["bg"])
        bar.grid(row=2, column=0, columnspan=2, sticky="e", padx=10, pady=(0, 10))
        self._rs_btn_run = tkinter.Button(bar, text="[▶ 远程运行选中]",
                                          font=utils.FONT_BUTTON, bg=C["ac"],
                                          fg="white", activebackground=C["ach"],
                                          activeforeground="white", relief="flat",
                                          bd=1, cursor="hand2", padx=10, pady=3,
                                          command=self._on_rs_run)
        self._rs_btn_run.pack(side="left", padx=(0, 6))
        tkinter.Button(bar, text="[刷新]", font=utils.FONT_BUTTON, bg=C["bgc"],
                       fg=C["fgb"], activebackground=C["acl"], relief="flat",
                       bd=1, cursor="hand2", padx=10, pady=3,
                       command=self._on_rs_refresh).pack(side="left", padx=(0, 6))
        tkinter.Button(bar, text="[关闭]", font=utils.FONT_BUTTON, bg=C["bgc"],
                       fg=C["fgb"], activebackground=C["acl"], relief="flat",
                       bd=1, cursor="hand2", padx=10, pady=3,
                       command=self._close_rs_dialog).pack(side="left")
        return dlg

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

    # ── 交互回调 ────────────────────────────────────────────────────
    def _on_select(self, event=None):
        nid = None
        try:
            sel = self.tv.selection()
        except Exception:
            sel = ()
        if sel:
            for k, v in self._items.items():
                if v == sel[0]:
                    nid = k
                    break
        self._sel = nid
        if nid:
            self._logseq.pop(nid, None)
        self._log_clear()
        self._refresh_detail()

    def _on_toggle(self):
        nl = _nl()
        if nl is None:
            try:
                self.lbl_bottom.config(text="互联模块不可用")
            except Exception:
                pass
            return
        try:
            if nl.is_running():
                nl.stop_netlink()
            else:
                state.NETLINK_ENABLED = True
                try:
                    state.save_config()
                except Exception:
                    pass
                nl.start_netlink(self.root)
        except Exception as e:
            try:
                log1("NetLink 启停失败: {}".format(e), "warning")
            except Exception:
                pass
        self._refresh()

    def _on_apply(self):
        """保存配置 (走 state.save_config) → 重启节点生效。"""
        try:
            state.NETLINK_AUTODISCOVER = bool(self.var_auto.get())
            try:
                state.NETLINK_PORT = int(self.var_port.get())
            except Exception:
                state.NETLINK_PORT = DEFAULT_TCP_PORT
                self.var_port.set(str(DEFAULT_TCP_PORT))
            state.NETLINK_DEVICE_NAME = self.var_name.get().strip()
            state.save_config()
        except Exception as e:
            try:
                log1("NetLink 配置保存失败: {}".format(e), "warning")
            except Exception:
                pass
        try:
            nl = _nl()
            if nl is not None:
                nl.stop_netlink()
                if getattr(state, "NETLINK_ENABLED", False):
                    nl.start_netlink(self.root)
        except Exception as e:
            try:
                log1("NetLink 重启失败: {}".format(e), "warning")
            except Exception:
                pass
        try:
            self.lbl_bottom.config(text="配置已应用，节点已重启")
        except Exception:
            pass
        self._refresh()

    def _on_add_peer(self):
        """按 node.py `_parse_static_peers` 既有格式写入 "host:port" 字符串。"""
        raw = (self.var_add.get() or "").strip()
        if not raw:
            return
        if ":" in raw:
            host, _, port_s = raw.rpartition(":")
            host = host.strip()
            try:
                port = int(port_s.strip())
            except Exception:
                port = None
        else:
            host, port = raw, None
        if not host:
            return
        if port is None:
            port = int(getattr(state, "NETLINK_PORT", DEFAULT_TCP_PORT))
        item = "{}:{}".format(host, port)
        try:
            lst = list(getattr(state, "NETLINK_STATIC_PEERS", []) or [])
            if item not in lst:
                lst.append(item)
            state.NETLINK_STATIC_PEERS = lst
            state.save_config()
        except Exception as e:
            try:
                log1("添加静态对端失败: {}".format(e), "warning")
            except Exception:
                pass
            return
        self.var_add.set("")
        try:
            self.lbl_bottom.config(text="已添加 {}，点应用生效".format(item))
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════════════
# 模块级单例门面
# ══════════════════════════════════════════════════════════════════════

_win_instance = None


def open_netlink_window(root=None):
    """打开「设备互联」窗口 (懒加载, 单例); 失败返回已存在/None。"""
    global _win_instance
    if _win_instance is not None and _win_instance.alive():
        _win_instance.show()
        return _win_instance
    if root is None:
        try:
            root = tkinter._default_root
        except Exception:
            root = None
    if root is None:
        return None
    try:
        _win_instance = NetLinkWindow(root)
    except Exception as e:
        try:
            log1("创建设备互联窗口失败: {}".format(e), "warning")
        except Exception:
            pass
        _win_instance = None
        return None
    _win_instance.show()
    return _win_instance


def is_open():
    """设备互联窗口当前是否打开。"""
    return _win_instance is not None and _win_instance.alive()


def refresh_theme():
    """主题切换时由 ACRPA._refresh_theme 调用: 窗口存在则重刷配色。"""
    if _win_instance is not None:
        try:
            _win_instance.refresh_theme()
        except Exception:
            pass


def close_window():
    """应用退出时调用, 幂等关闭并释放单例。"""
    global _win_instance
    if _win_instance is not None:
        try:
            _win_instance.close()
        except Exception:
            pass
        _win_instance = None
