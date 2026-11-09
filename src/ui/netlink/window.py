# -*- coding: utf-8 -*-
"""NetLinkWindow 门面 + 事件泵 + 模块级单例 (L1-A 拆分)。"""

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

from ui.netlink import panels, shot, pairing, remote, web, audit, sched


# ── 建窗 UI 小工厂 (v5 §4 卡片层级 / V1 按钮语义 / I1 空态) ──────────────

def _mk_card(parent, C, title=None):
    """房子风格卡片: bgc 面 + 1px bd 描边 (同 sched_tab); title 给出时追加 muted 区头。

    注意: 区头与内容体统一走 grid (卡片内禁止 pack/grid 混用)。
    """
    card = tkinter.Frame(parent, bg=C["bgc"],
                         highlightbackground=C["bd"], highlightthickness=1)
    if title:
        head = tkinter.Frame(card, bg=C["bgc"])
        head.grid(row=0, column=0, sticky="ew",
                  padx=utils.sp(10), pady=(utils.sp(8), utils.sp(2)))
        tkinter.Label(head, text=title, font=utils.FONT_BUTTON,
                      fg=C["fgm"], bg=C["bgc"]).pack(side="left")
    return card


def _mk_btn(parent, C, text, command, kind="ghost", font=None,
            state="normal", padx=None):
    """v5 V1/V2: 每窗仅一个实心主按钮 (kind="primary"), 其余 ghost;
    danger 为「dg 字 + errbg hover」的 ghost, 不再整颗实心红。"""
    f = font or utils.FONT_BUTTON
    px = utils.sp(12) if padx is None else padx
    py = utils.sp(1) if f == utils.FONT_SMALL else utils.sp(3)
    kw = dict(font=f, relief="flat", bd=1, cursor="hand2",
              padx=px, pady=py, state=state, command=command)
    if kind == "primary":
        return tkinter.Button(parent, text=text, bg=C["ac"],
                              fg=utils.text_on(C["ac"]),
                              activebackground=C["ach"],
                              activeforeground=utils.text_on(C["ach"]), **kw)
    if kind == "danger":
        return tkinter.Button(parent, text=text, bg=C["bgc"], fg=C["dg"],
                              activebackground=C["errbg"],
                              activeforeground=C["dg"],
                              disabledforeground=C["fgd"], **kw)
    return tkinter.Button(parent, text=text, bg=C["bgc"], fg=C["fgb"],
                          activebackground=C["acl"],
                          activeforeground=C["fgb"],
                          disabledforeground=C["fgd"], **kw)


def _mk_empty(parent, C, icon, main, sub="", btn_text=None, btn_cmd=None):
    """I1 空态: 图标 + 主句 + 副句 (muted, 左对齐); 返回容器, 供 grid_remove() 复用。

    v6 §5.4: 可选主按钮 (btn_text/btn_cmd, primary 实心) —— 空态组件三要素
    「图标 + 主句 + 主按钮」的落地; 不传则维持旧的两行文本形态。"""
    box = tkinter.Frame(parent, bg=C["bgc"])
    row = tkinter.Frame(box, bg=C["bgc"])
    row.pack(anchor="w", padx=utils.sp(10), pady=utils.sp(4))
    tkinter.Label(row, text=icon, font=utils.FONT_BODY,
                  fg=C["fgd"], bg=C["bgc"]).pack(side="left", padx=(0, utils.sp(8)))
    col = tkinter.Frame(row, bg=C["bgc"])
    col.pack(side="left")
    tkinter.Label(col, text=main, font=utils.FONT_SMALL,
                  fg=C["fgm"], bg=C["bgc"], anchor="w").pack(anchor="w")
    if sub:
        tkinter.Label(col, text=sub, font=utils.FONT_TINY,
                      fg=C["fgd"], bg=C["bgc"], anchor="w").pack(anchor="w")
    if btn_text and callable(btn_cmd):
        _mk_btn(col, C, btn_text, btn_cmd, kind="primary",
                font=utils.FONT_SMALL, padx=utils.sp(10)).pack(
            anchor="w", pady=(utils.sp(4), 0))
    return box


class NetLinkWindow(panels.PanelsMixin, shot.ShotMixin, pairing.PairingMixin,
                     remote.RemoteMixin, web.WebMixin, audit.AuditMixin,
                     sched.SchedMixin, object):
    """设备互联窗口 (薄壳 + mixin 组合)。"""
    # __init__
    def __init__(self, root, host=None):
        self.root = root
        self.win = None
        self.host = None
        self._canvas = None
        self._inner = None
        self._vscroll = None
        self._embedded = host is not None
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
        self._build(host)

    # alive
    def alive(self):
        try:
            return self.host is not None and bool(self.host.winfo_exists())
        except Exception:
            return False

    # show
    def show(self):
        if not self.alive():
            return
        if not self._embedded:
            try:
                self.win.deiconify()
                self.win.lift()
                self.win.focus_force()
            except Exception:
                pass
        if self._pump_id is None:
            self._schedule_pump(PUMP_INTERVAL_MS)

    # close
    def close(self):
        """保存窗口几何 → 销毁；幂等。内嵌 Tab 不销毁宿主/根窗口。"""
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
        if self._embedded:
            self.host = None
            self.win = None
            return
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
        self.host = None

    # _set_icon
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

    # refresh_theme
    def refresh_theme(self, dark=None, colors=None, prev=None):
        """主题切换后重刷本窗口配色 (含各 Treeview/Text 的 tag 前景)。

        dark/colors/prev 为 ThemeBus.publish 派发契约 (订阅回调统一签名);
        本实现直接读 utils.C (publish 前已切到新色板), 参数仅占位不依赖。

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

        # 语义实心底色跨主题会翻转明度 (亮色 ac=#0078D4 深, 暗色 ac=#4CC2FF 浅),
        # 固定白字在暗色下不可读 → 这些键上的文字一律按底色亮度重算。
        _strong = {"ac", "ach", "acp", "dg", "sc", "wn", "err", "ai", "hlbg"}

        def _fix_fg(w):
            """按钮/文字控件的语义底色可能刚被替换, 依亮度回填前景色。"""
            for _bg_opt, _fg_opts in (
                ("bg", ("fg", "disabledforeground")),
                ("activebackground", ("activeforeground",)),
            ):
                try:
                    _bg = str(w.cget(_bg_opt))
                except Exception:
                    continue
                if vmap.get(_bg) not in _strong:
                    continue
                _fg = utils.text_on(_bg)
                _cfg = {o: _fg for o in _fg_opts if o != "disabledforeground"}
                if "disabledforeground" in _fg_opts:
                    _cfg["disabledforeground"] = cur.get("fgm", _fg)
                try:
                    w.configure(**_cfg)
                except Exception:
                    pass

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
                        if cls in ("Button", "Menubutton", "Label", "TLabel",
                                   "Checkbutton", "Radiobutton"):
                            _fix_fg(w)
                        # 输入类控件的插入符/选中态在 Tk 里是系统默认色 (不在旧色板里,
                        # _map_color 永远映射不到) → 暗色下光标黑不可见, 必须按 token 直刷。
                        if cls in ("Text", "Entry", "Spinbox", "Listbox"):
                            _sel = {"selectbackground": cur["acl"],
                                    "selectforeground": cur["fgt"]}
                            if cls != "Listbox":
                                _sel["insertbackground"] = cur["fgt"]
                            if cls == "Spinbox":
                                _sel["buttonbackground"] = cur["bgc"]
                            try:
                                w.configure(**_sel)
                            except Exception:
                                pass
                        if cls == "TCombobox":
                            # 下拉弹窗为原生 Listbox, 不走 ttk 样式
                            try:
                                from ui import theme as _ui_theme
                                _ui_theme.restyle_combobox_popdown(w, cur)
                            except Exception:
                                pass
                except Exception:
                    pass
                _walk(w)

        # 宿主 (独立窗口或内嵌 Tab 面板) + 已打开的子对话框 (存在则活, 不存在为空操作)
        windows = [self.host if self.host is not None else self.win]
        for attr in ("_pair_dlg", "_audit_dlg", "_rs_dlg",
                     "_shot_dlg", "_web_dlg"):
            d = getattr(self, attr, None)
            if d is not None:
                windows.append(d)
        for win in windows:
            try:
                if win is not None and win.winfo_exists():
                    win.configure(bg=cur["bg"])   # 顶层容器自身不在 _walk 的遍历里
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
        try:      # 视图切页按钮高亮随主题重刷 (选中 accent 浅底/未选中 ghost)
            self._set_view(getattr(self, "_view", "dev"))
        except Exception:
            pass
        self._theme_snapshot = dict(cur)
        try:
            (self.host if self.host is not None else self.win).update_idletasks()
        except Exception:
            pass

    # _build
    def _build(self, host=None):
        C = utils.C
        self._theme_snapshot = dict(C)
        if host is not None:
            # 内嵌到 Tab 面板: 宿主为传入容器, 对话框父窗口回退到主窗口。
            # 内容包进纵向可滚动 Canvas, 避免设备互联控件树过高时被 Tab 裁切。
            self.host = host
            self.win = host.winfo_toplevel() if hasattr(host, "winfo_toplevel") else self.root
            host.columnconfigure(0, weight=1)
            host.rowconfigure(0, weight=1)
            canvas = tkinter.Canvas(host, bg=C["bg"], highlightthickness=0,
                                    bd=0, relief="flat")
            sb = ttk.Scrollbar(host, orient="vertical", command=canvas.yview)
            canvas.configure(yscrollcommand=sb.set)
            canvas.grid(row=0, column=0, sticky="nsew")
            sb.grid(row=0, column=1, sticky="ns")
            win = tkinter.Frame(canvas, bg=C["bg"])
            _inner_id = canvas.create_window((0, 0), window=win, anchor="nw")
            self._canvas = canvas
            self._inner = win
            self._vscroll = sb
            # 内容宽度随视口伸缩, 高度按内容自适应 (整栏纵向滚动)
            def _sync_inner_width(event):
                try:
                    canvas.itemconfigure(_inner_id, width=event.width)
                except Exception:
                    pass

            def _on_wheel(event):
                try:
                    canvas.yview_scroll(int(-event.delta / 120), "units")
                except Exception:
                    pass

            def _bind_wheel(w):
                for child in w.winfo_children():
                    try:
                        cls = child.winfo_class()
                        if cls in ("Treeview", "Text", "Listbox",
                                   "TCombobox", "Spinbox"):
                            continue
                        child.bind("<MouseWheel>", _on_wheel, add="+")
                    except Exception:
                        pass
                    _bind_wheel(child)

            canvas.bind("<Configure>", _sync_inner_width, add="+")
            canvas.bind("<MouseWheel>", _on_wheel, add="+")
            try:    # 声明自管换肤: 通用 walk 不再跨入, 由本类 refresh_theme 负责
                from ui import theme as _ui_theme
                _ui_theme.claim_window(host, "netlink_window")
            except Exception:
                pass
        else:
            win = tkinter.Toplevel(self.root)
            self.win = win
            self.host = win
            try:    # 声明自管换肤: 通用 walk 不再跨入, 由本类 refresh_theme 负责
                from ui import theme as _ui_theme
                _ui_theme.claim_window(win, "netlink_window")
            except Exception:
                pass
        if host is None:
            # 独立窗口外壳 (标题/尺寸/最小尺寸/居中/记忆位置/图标/关闭协议)
            win.title("设备互联 — ACRPA")
            geom = ""
            try:
                geom = getattr(state, "NETLINK_UI_WINDOW", "") or ""
            except Exception:
                geom = ""
            try:
                ok = bool(geom) and bool(utils.geometry_in_screen(self.root, geom))
            except Exception:
                ok = False
            try:
                from ui.window_shell import apply_window_chrome
                apply_window_chrome(win, size=(800, 840), min_size=(700, 620),
                                    geometry=geom if ok else None, center=not ok,
                                    bg_key="bg", colors=C)
            except Exception:
                if ok:
                    try:
                        win.geometry(geom)
                    except Exception:
                        win.geometry("800x840")
                else:
                    win.geometry("800x840")
                try:
                    win.minsize(utils.sp(700), utils.sp(560))
                except Exception:
                    pass
            self._set_icon(win)
            win.protocol("WM_DELETE_WINDOW", self.close)
        win.configure(bg=C["bg"])
        win.columnconfigure(0, weight=1)
        win.rowconfigure(1, weight=1)   # 主区 (2 列 PanedWindow) 可伸缩 (row0=概览卡)

        # ── 0. 概览卡: 状态摘要 + 启停/配对 + 连接配置 (v5 S4/P1-3) ──
        # 原「顶部状态条 + 单行配置」在 780px 独立窗口下横向溢出被裁切,
        # 收进卡片并把「手动添加」折到第二行, 任意宽度 ≥ minsize 都放得下。
        ov = _mk_card(win, C)
        ov.grid(row=0, column=0, sticky="ew", padx=utils.sp(10), pady=(utils.sp(8), utils.sp(6)))
        ov.columnconfigure(0, weight=1)

        top = tkinter.Frame(ov, bg=C["bgc"])
        top.grid(row=0, column=0, sticky="ew", padx=utils.sp(10), pady=(utils.sp(8), utils.sp(4)))
        top.columnconfigure(0, weight=1)
        self.lbl_top = tkinter.Label(top, text="—  ·  ○ 已停止",
                                     font=utils.FONT_BODY, bg=C["bgc"], fg=C["fgt"],
                                     anchor="w")
        self.lbl_top.grid(row=0, column=0, sticky="w")
        self.lbl_pair = tkinter.Label(top, text="配对码 —", font=utils.FONT_SMALL,
                                      bg=C["bgc"], fg=C["fgm"], anchor="e")
        self.lbl_pair.grid(row=0, column=1, sticky="e", padx=(utils.sp(8), utils.sp(6)))
        self.btn_pair_code = _mk_btn(top, C, "配对码", self._on_pair_code)
        self.btn_pair_code.grid(row=0, column=2, sticky="e", padx=(0, utils.sp(6)))
        try:
            utils.attach_tooltip(self.btn_pair_code,
                                 "生成 6 位配对码，供其它设备输入以完成配对 (被控端)")
        except Exception:
            pass
        self.btn_toggle = _mk_btn(top, C, "启动互联", self._on_toggle, kind="primary")
        self.btn_toggle.grid(row=0, column=3, sticky="e")

        # 连接配置 (行 1): 自动发现 / 端口 / 设备名 / 应用
        cfg = tkinter.Frame(ov, bg=C["bgc"])
        cfg.grid(row=1, column=0, sticky="ew", padx=utils.sp(10), pady=(0, utils.sp(2)))

        self.var_auto = tkinter.BooleanVar(
            value=bool(getattr(state, "NETLINK_AUTODISCOVER", True)))
        ttk.Checkbutton(cfg, text="自动发现(UDP)", variable=self.var_auto).pack(
            side="left", padx=(0, utils.sp(8)))

        tkinter.Label(cfg, text="端口", font=utils.FONT_SMALL,
                      bg=C["bgc"], fg=C["fgm"]).pack(side="left", padx=(0, utils.sp(4)))
        self.var_port = tkinter.StringVar(
            value=str(getattr(state, "NETLINK_PORT", DEFAULT_TCP_PORT)))
        tkinter.Entry(cfg, textvariable=self.var_port, width=6, font=utils.FONT_BODY,
                      relief="solid", bd=1, bg=C["ebg"], fg=C["fgb"],
                      insertbackground=C["fgt"]).pack(
            side="left", padx=(0, utils.sp(8)))

        tkinter.Label(cfg, text="设备名", font=utils.FONT_SMALL,
                      bg=C["bgc"], fg=C["fgm"]).pack(side="left", padx=(0, utils.sp(4)))
        self.var_name = tkinter.StringVar(
            value=getattr(state, "NETLINK_DEVICE_NAME", "") or "")
        tkinter.Entry(cfg, textvariable=self.var_name, width=12, font=utils.FONT_BODY,
                      relief="solid", bd=1, bg=C["ebg"], fg=C["fgb"],
                      insertbackground=C["fgt"]).pack(
            side="left", padx=(0, utils.sp(8)))

        _mk_btn(cfg, C, "应用", self._on_apply, font=utils.FONT_SMALL,
                padx=utils.sp(10)).pack(side="left")

        # 手动添加 (行 2, 独立一行防溢出)
        cfg2 = tkinter.Frame(ov, bg=C["bgc"])
        cfg2.grid(row=2, column=0, sticky="ew", padx=utils.sp(10), pady=(0, utils.sp(8)))
        tkinter.Label(cfg2, text="手动添加", font=utils.FONT_SMALL,
                      bg=C["bgc"], fg=C["fgm"]).pack(side="left", padx=(0, utils.sp(4)))
        self.var_add = tkinter.StringVar(value="")
        tkinter.Entry(cfg2, textvariable=self.var_add, width=18, font=utils.FONT_BODY,
                      relief="solid", bd=1, bg=C["ebg"], fg=C["fgb"],
                      insertbackground=C["fgt"]).pack(
            side="left", padx=(0, utils.sp(6)))
        _mk_btn(cfg2, C, "添加", self._on_add_peer, font=utils.FONT_SMALL,
                padx=utils.sp(10)).pack(side="left")
        tkinter.Label(cfg2, text="格式 host:port",
                      font=utils.FONT_TINY, bg=C["bgc"], fg=C["fgd"]).pack(
            side="left", padx=(utils.sp(10), 0))

        # ── 主区: 2 列 PanedWindow (左3右1, 可调节) ──
        # 左列 = 设备管理 (设备列表/详情/已配对/操控/远程指令/传输),
        # 右列 = 日志面板 (常驻侧栏)。分隔条可拖动调整比例。
        paned = ttk.PanedWindow(win, orient="horizontal")
        paned.grid(row=1, column=0, sticky="nsew", padx=utils.sp(10), pady=(0, utils.sp(4)))
        self._paned = paned
        left = tkinter.Frame(paned, bg=C["bg"])
        right = tkinter.Frame(paned, bg=C["bg"])
        paned.add(left, weight=3)
        paned.add(right, weight=1)
        left.columnconfigure(0, weight=1)
        # S4/P1-3: 左列 = 单表多视图卡 (设备|配对|指令|传输) + 操控按钮组。
        left.rowconfigure(0, weight=1)   # 单表多视图 (可伸缩)
        left.rowconfigure(1, weight=0)   # 操控按钮组
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=1)  # 日志面板 (可伸缩)

        # 默认按 3:1 分栏; 容器映射后恢复缓存 sashpos (无缓存则 0.75)。
        def _init_sash():
            try:
                w = paned.winfo_width()
                if w > 0:
                    cached = None
                    try:
                        cached = state.get_layout_sash().get("netlink", {}).get("main")
                    except Exception:
                        cached = None
                    if cached is not None:
                        pos = max(300, min(int(cached), w - 200))
                    else:
                        pos = int(w * 0.75)
                    paned.sashpos(0, pos)
            except Exception:
                pass

        # §5.4 分栏 sash 记忆: 拖拽结束写盘
        def _save_sash(_e=None):
            try:
                state.set_layout_sash("netlink", "main", paned.sashpos(0), persist=True)
            except Exception:
                pass
        paned.bind("<ButtonRelease-1>", _save_sash)

        try:
            self._sash_init_id = win.after(80, _init_sash)
        except Exception:
            self._sash_init_id = None

        # ── 2. 单表多视图 (S4/P1-3): 一张表 + 切页 (设备｜配对｜指令｜传输) ──
        # 替代原「多张空表纵向堆叠」: 同一区域按视图切换, 首屏不再是一屏空白表头。
        self._view = "dev"
        self._view_btns = {}
        self._view_frames = {}
        card_table = tkinter.Frame(left, bg=C["bgc"],
                                   highlightbackground=C["bd"], highlightthickness=1)
        card_table.grid(row=0, column=0, sticky="nsew", pady=(0, utils.sp(6)))
        card_table.columnconfigure(0, weight=1)
        card_table.rowconfigure(1, weight=1)

        # 切页条: 视图按钮 (含计数) + 右侧在线数 (P1-3 首屏信息 ≥3 条)
        thead = tkinter.Frame(card_table, bg=C["bgc"])
        thead.grid(row=0, column=0, sticky="ew", padx=utils.sp(8),
                   pady=(utils.sp(6), utils.sp(4)))
        self.lbl_online = tkinter.Label(thead, text="在线 0 / 0", font=utils.FONT_SMALL,
                                        bg=C["bgc"], fg=C["fgm"], anchor="e")
        self.lbl_online.pack(side="right")
        for _key, _cap in (("dev", "设备"), ("paired", "配对"),
                           ("cmd", "指令"), ("xfer", "传输")):
            _b = _mk_btn(thead, C, _cap,
                         (lambda k=_key: self._set_view(k)),
                         font=utils.FONT_SMALL, padx=utils.sp(10))
            _b.pack(side="left", padx=(0, utils.sp(4)))
            self._view_btns[_key] = _b

        # 视图堆叠: 4 个子页同格叠放, 仅当前视图可见 (其余 grid_remove)
        stack = tkinter.Frame(card_table, bg=C["bgc"])
        stack.grid(row=1, column=0, sticky="nsew", padx=utils.sp(8), pady=(0, utils.sp(8)))
        stack.columnconfigure(0, weight=1)
        stack.rowconfigure(0, weight=1)
        self._view_stack = stack

        def _mk_view(key):
            f = tkinter.Frame(stack, bg=C["bgc"])
            f.grid(row=0, column=0, sticky="nsew")
            f.columnconfigure(0, weight=1)
            f.rowconfigure(0, weight=1)
            self._view_frames[key] = f
            return f

        # 视图①设备: 设备表 + 选中详情
        dev_body = _mk_view("dev")
        cols = ("dev", "state", "script", "progress", "latency", "perm")
        # Phase3-2: 设备表支持多选（批量下发）；主选中取 selection()[0]
        self.tv = ttk.Treeview(dev_body, columns=cols, show="headings", height=9,
                               selectmode="extended")
        for cid, text, width in (
                ("dev", "设备", 120), ("state", "状态", 70),
                ("script", "当前脚本", 120), ("progress", "进度", 110),
                ("latency", "延迟", 50), ("perm", "权限", 60)):
            self.tv.heading(cid, text=text)
            self.tv.column(cid, width=width, anchor="w")
        # §4.5 降级: show="headings" 无 #0 列 → 选中行加粗 (整行 acl 底由 style.map)
        utils.bind_sel_bold(self.tv)
        self.tv.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(dev_body, orient="vertical", command=self.tv.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.tv.configure(yscrollcommand=sb.set)
        self.tv.tag_configure("run", foreground=C["sc"])
        self.tv.tag_configure("pause", foreground=C["wn"])
        self.tv.tag_configure("idle", foreground=C["fgm"])
        self.tv.tag_configure("off", foreground=C["fgm"])
        self.tv.tag_configure("unauth", foreground=C["fgm"])
        self.tv.bind("<<TreeviewSelect>>", self._on_select)

        # v6 §5.2/§5.4: 设备表空态 (图标 + 主句 + 副句 + [显示配对码] 主按钮);
        # panels._refresh_table 按 grid_remove/grid 切换, 与已配对区空态同法。
        self.lbl_dev_empty = _mk_empty(
            dev_body, C, "◌", "还没有已配对设备",
            "在对方机器开启允许被发现后会自动出现在这里",
            btn_text="显示配对码", btn_cmd=self._on_pair_code)
        self.lbl_dev_empty.grid(row=1, column=0, columnspan=2, sticky="ew",
                                pady=(utils.sp(2), 0))

        # 选中设备详情 (设备视图内)
        det = tkinter.Frame(dev_body, bg=C["bgc"])
        det.grid(row=2, column=0, sticky="ew", pady=(utils.sp(6), 0))
        det.columnconfigure(1, weight=1)
        tkinter.Label(det, text="进度", font=utils.FONT_SMALL,
                      bg=C["bgc"], fg=C["fgm"]).grid(row=0, column=0, sticky="w", padx=(0, utils.sp(6)))
        self.pb_detail = ttk.Progressbar(det, mode="determinate", maximum=1,
                                         value=0, length=100)
        self.pb_detail.grid(row=0, column=1, sticky="ew")
        self.lbl_detail = tkinter.Label(det, text="未选择设备", font=utils.FONT_SMALL,
                                        bg=C["bgc"], fg=C["fgm"], anchor="w")
        self.lbl_detail.grid(row=1, column=0, columnspan=2, sticky="w", pady=(utils.sp(2), 0))
        self.btn_pair_peer = _mk_btn(det, C, "⇄ 配对", self._on_pair_peer,
                                     font=utils.FONT_SMALL, padx=utils.sp(10),
                                     state="disabled")
        self.btn_pair_peer.grid(row=0, column=2, rowspan=2, sticky="e", padx=(utils.sp(8), 0))
        try:
            utils.attach_tooltip(self.btn_pair_peer,
                                 "对选中的未认证设备发起配对 (需输入对方配对码)")
        except Exception:
            pass
        # Phase3-1b: 查看远端脚本列表并可直接远程运行 (需 perm=script)
        self.btn_remote_scripts = _mk_btn(det, C, "▭ 远端脚本", self._on_remote_scripts,
                                          font=utils.FONT_SMALL, padx=utils.sp(10),
                                          state="disabled")
        self.btn_remote_scripts.grid(row=0, column=3, rowspan=2, sticky="e", padx=(utils.sp(8), 0))
        try:
            utils.attach_tooltip(self.btn_remote_scripts, self._tip_script)
        except Exception:
            pass

        # Phase3-2: 定时任务状态（消费 TOPIC_PEER_SCHED，只读展示）
        self.lbl_sched = tkinter.Label(det, text="定时任务：—", font=utils.FONT_SMALL,
                                       bg=C["bgc"], fg=C["fgm"], anchor="w")
        self.lbl_sched.grid(row=2, column=0, columnspan=4, sticky="w", pady=(utils.sp(2), 0))

        # 视图②已配对: 已配对设备 + 本机授予的远程权限
        pbody = _mk_view("paired")
        self.lf_paired = pbody
        pcols = ("name", "fp", "perm", "last")
        self.tv_paired = ttk.Treeview(pbody, columns=pcols,
                                      show="headings", height=9)
        for cid, text, width in (("name", "设备名", 120), ("fp", "指纹", 180),
                                 ("perm", "权限", 90), ("last", "最近连接", 120)):
            self.tv_paired.heading(cid, text=text)
            self.tv_paired.column(cid, width=width, anchor="w")
        # §4.5 降级: show="headings" 无 #0 列 → 选中行加粗
        utils.bind_sel_bold(self.tv_paired)
        self.tv_paired.grid(row=0, column=0, sticky="nsew")
        psb = ttk.Scrollbar(pbody, orient="vertical",
                            command=self.tv_paired.yview)
        psb.grid(row=0, column=1, sticky="ns")
        self.tv_paired.configure(yscrollcommand=psb.set)
        self.tv_paired.bind("<<TreeviewSelect>>", self._on_paired_select)
        # I1 空态: 图标 + 主句 + 副句 (panels._refresh_paired 按 grid_remove/grid 切换)
        self.lbl_paired_empty = _mk_empty(pbody, C, "◌", "暂无已配对设备",
                                          "选中设备点「⇄ 配对」完成配对")
        self.lbl_paired_empty.grid(row=1, column=0, columnspan=2, sticky="ew",
                                   pady=(utils.sp(2), 0))
        pctl = tkinter.Frame(pbody, bg=C["bgc"])
        pctl.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(utils.sp(2), 0))
        tkinter.Label(pctl, text="权限", font=utils.FONT_SMALL, bg=C["bgc"],
                      fg=C["fgm"]).pack(side="left", padx=(0, utils.sp(4)))
        self.var_perm = tkinter.StringVar(value="仅观察")
        self.cmb_perm = ttk.Combobox(pctl, textvariable=self.var_perm,
                                     values=("仅观察", "允许操控", "允许接收脚本"),
                                     state="readonly", width=12)
        self.cmb_perm.pack(side="left", padx=(0, utils.sp(6)))
        # v5 V1: 应用不再是第二颗实心主按钮 → ghost
        self.btn_perm_apply = _mk_btn(pctl, C, "应用", self._on_perm_apply,
                                      font=utils.FONT_SMALL, padx=utils.sp(10),
                                      state="disabled")
        self.btn_perm_apply.pack(side="left", padx=(0, utils.sp(6)))
        self.btn_peer_remove = _mk_btn(pctl, C, "移除", self._on_peer_remove,
                                       kind="danger", font=utils.FONT_SMALL,
                                       padx=utils.sp(10), state="disabled")
        self.btn_peer_remove.pack(side="left")

        # ── 4. 日志面板 (右列, 常驻侧栏) ──
        logfr = _mk_card(right, C, title="设备日志")
        logfr.grid(row=0, column=0, sticky="nsew", padx=(utils.sp(6), 0), pady=0)
        logfr.columnconfigure(0, weight=1)
        logfr.rowconfigure(1, weight=1)
        logbody = tkinter.Frame(logfr, bg=C["bgc"])
        logbody.grid(row=1, column=0, sticky="nsew", padx=utils.sp(8), pady=(0, utils.sp(8)))
        logbody.columnconfigure(0, weight=1)
        logbody.rowconfigure(0, weight=1)
        # v6 §5.2: 外框接主题 —— Text 默认 highlight 框是系统黑 (截图里的「粗黑外框」),
        # 显式设为 bd 1px (聚焦时 accent); 换肤由 refresh_theme 的 _walk 旧色→新色映射跟随。
        self.txt_log = tkinter.Text(logbody, height=8, width=10, font=utils.FONT_LOG,
                                    bg=C["logbg"], fg=C["logfg"], relief="solid", bd=1,
                                    wrap="none", state="disabled",
                                    highlightthickness=1,
                                    highlightbackground=C["bd"],
                                    highlightcolor=C["focus"])
        self.txt_log.grid(row=0, column=0, sticky="nsew")
        logsb = ttk.Scrollbar(logbody, orient="vertical", command=self.txt_log.yview)
        logsb.grid(row=0, column=1, sticky="ns")
        self.txt_log.configure(yscrollcommand=logsb.set)
        self.txt_log.tag_configure("err", foreground=C["err"])
        self.txt_log.tag_configure("wn", foreground=C["wn"])

        # ── 5. 操控按钮组 (Phase 2-2b: 按选中设备权限/运行态启用) ──
        # 两行布局: 指令行 + 工具行, 避免左栏 570px 下按钮被挤到只剩图标。
        ctl = tkinter.Frame(left, bg=C["bg"])
        ctl.grid(row=1, column=0, sticky="ew", padx=0, pady=(0, utils.sp(6)))
        ctl.columnconfigure(0, weight=1)
        ctl.rowconfigure(0, weight=0)
        ctl.rowconfigure(1, weight=0)
        ctl_cmd = tkinter.Frame(ctl, bg=C["bg"])
        ctl_cmd.grid(row=0, column=0, sticky="w")
        ctl_tool = tkinter.Frame(ctl, bg=C["bg"])
        ctl_tool.grid(row=1, column=0, sticky="w", pady=(utils.sp(4), 0))

        def _mk_ctl(parent, text, command):
            b = _mk_btn(parent, C, text, command, font=utils.FONT_SMALL,
                        state="disabled", padx=utils.sp(8))
            b.pack(side="left", padx=(0, utils.sp(5)))
            return b

        self.btn_run = _mk_ctl(ctl_cmd, "▶ 运行", self._on_remote_run)
        self.btn_pause = _mk_ctl(ctl_cmd, "⏸ 暂停", self._on_remote_pause)
        self.btn_stop = _mk_ctl(ctl_cmd, "⏹ 停止", self._on_remote_stop)
        self.btn_script = _mk_ctl(ctl_cmd, "推脚本", self._on_push_script)
        # Phase4-1: ◎ 截图（需「允许操控」权限，control/script 均可）
        self.btn_shot = _mk_ctl(ctl_cmd, "◎ 截图", self._on_remote_shot)
        try:
            utils.attach_tooltip(self.btn_run, self._tip_run)
            utils.attach_tooltip(self.btn_pause, self._tip_pause)
            utils.attach_tooltip(self.btn_stop, self._tip_stop)
            utils.attach_tooltip(self.btn_script, self._tip_script)
            utils.attach_tooltip(self.btn_shot, self._tip_shot)
        except Exception:
            pass

        self.btn_audit = _mk_btn(ctl_tool, C, "≡ 审计日志", self._on_audit,
                                 font=utils.FONT_SMALL, padx=utils.sp(8))
        self.btn_audit.pack(side="left", padx=(0, utils.sp(5)))
        try:
            utils.attach_tooltip(self.btn_audit, "查看远程操控审计日志（只读）")
        except Exception:
            pass

        # Phase4-2: ⊕ 网页面板（浏览器只读监控面板，默认关闭）
        self.btn_web = _mk_btn(ctl_tool, C, "⊕ 网页面板", self._on_web_panel,
                               font=utils.FONT_SMALL, padx=utils.sp(8))
        self.btn_web.pack(side="left")
        try:
            utils.attach_tooltip(self.btn_web,
                                 "浏览器只读监控面板：手机/平板可查看进度（默认关闭，需启用）")
        except Exception:
            pass

        # ── 6. 视图③远程指令 / 视图④传输 ──
        cbody = _mk_view("cmd")
        self.lf_cmd = cbody
        ccols = ("time", "dev", "cmd", "result")
        self.tv_cmd = ttk.Treeview(cbody, columns=ccols, show="headings",
                                   height=9)
        for cid, text, width in (("time", "时间", 60), ("dev", "设备", 120),
                                 ("cmd", "指令", 90), ("result", "结果", 250)):
            self.tv_cmd.heading(cid, text=text)
            self.tv_cmd.column(cid, width=width, anchor="w")
        # §4.5 降级: show="headings" 无 #0 列 → 选中行加粗
        utils.bind_sel_bold(self.tv_cmd)
        self.tv_cmd.grid(row=0, column=0, sticky="nsew")
        csb = ttk.Scrollbar(cbody, orient="vertical", command=self.tv_cmd.yview)
        csb.grid(row=0, column=1, sticky="ns")
        self.tv_cmd.configure(yscrollcommand=csb.set)
        self.tv_cmd.tag_configure("cmdok", foreground=C["sc"])
        self.tv_cmd.tag_configure("cmdfail", foreground=C["err"])
        self.tv_cmd.tag_configure("cmdgray", foreground=C["fgm"])
        self.lbl_cmd_empty = _mk_empty(cbody, C, "◌", "暂无远程指令",
                                       "选中设备后用操控按钮下发指令")
        self.lbl_cmd_empty.grid(row=1, column=0, columnspan=2, sticky="ew",
                                pady=(utils.sp(2), 0))

        # 视图④传输 (Phase3-1b: TOPIC_TRANSFER 进度)
        xbody = _mk_view("xfer")
        self.lf_xfer = xbody
        xcols = ("dev", "file", "progress", "state")
        self.tv_xfer = ttk.Treeview(xbody, columns=xcols, show="headings",
                                    height=9)
        for cid, text, width in (("dev", "设备", 120), ("file", "文件", 150),
                                 ("progress", "进度", 140), ("state", "状态", 160)):
            self.tv_xfer.heading(cid, text=text)
            self.tv_xfer.column(cid, width=width, anchor="w")
        # §4.5 降级: show="headings" 无 #0 列 → 选中行加粗
        utils.bind_sel_bold(self.tv_xfer)
        self.tv_xfer.grid(row=0, column=0, sticky="nsew")
        xsb = ttk.Scrollbar(xbody, orient="vertical", command=self.tv_xfer.yview)
        xsb.grid(row=0, column=1, sticky="ns")
        self.tv_xfer.configure(yscrollcommand=xsb.set)
        self.tv_xfer.tag_configure("xfer_run", foreground=C["ac"])
        self.tv_xfer.tag_configure("xfer_ok", foreground=C["sc"])
        self.tv_xfer.tag_configure("xfer_fail", foreground=C["err"])
        self.lbl_xfer_empty = _mk_empty(xbody, C, "◌", "暂无传输",
                                        "「推脚本」的文件进度显示在这里")
        self.lbl_xfer_empty.grid(row=1, column=0, columnspan=2, sticky="ew",
                                 pady=(utils.sp(2), 0))

        # 初始只显示「设备」视图 (其余 3 个子页 grid_remove)
        self._set_view("dev")

        # ── 9. 底部状态行 ──
        self.lbl_bottom = tkinter.Label(win, text="互联未启动", font=utils.FONT_SMALL,
                                        bg=C["bg"], fg=C["fgm"], anchor="w")
        self.lbl_bottom.grid(row=2, column=0, sticky="ew", padx=utils.sp(10), pady=(utils.sp(4), utils.sp(2)))
        # Phase4-3 传输加密状态（只读：明文 / TLS（指纹 N 个））
        self.lbl_tls = tkinter.Label(win, text="传输：明文", font=utils.FONT_SMALL,
                                     bg=C["bg"], fg=C["fgm"], anchor="w")
        self.lbl_tls.grid(row=3, column=0, sticky="ew", padx=utils.sp(10), pady=(0, utils.sp(8)))

        # 内嵌模式: 全部控件就绪后再递归绑定滚轮 (可滚动的 Treeview/Text/Listbox 除外)
        if host is not None:
            try:
                _bind_wheel(win)
            except Exception:
                pass

        self._schedule_pump(PUMP_INTERVAL_MS)

    # _set_view
    def _set_view(self, key):
        """切换单表视图 (设备｜配对｜指令｜传输): 仅当前子页可见, 按钮高亮 (S4/P1-3)。"""
        self._view = key
        C = utils.C
        for k, f in getattr(self, "_view_frames", {}).items():
            try:
                if k == key:
                    f.grid()
                else:
                    f.grid_remove()
            except Exception:
                pass
        for k, b in getattr(self, "_view_btns", {}).items():
            try:
                if k == key:      # 选中: accent 浅底 + 前景白
                    b.configure(bg=C.get("acl"), fg=C.get("fgt"),
                                activebackground=C.get("acl"),
                                activeforeground=C.get("fgt"))
                else:             # 未选中: 卡片底 + 正文色 (ghost)
                    b.configure(bg=C.get("bgc"), fg=C.get("fgb"),
                                activebackground=C.get("acl"),
                                activeforeground=C.get("fgb"))
            except Exception:
                pass

    # _schedule_pump
    def _schedule_pump(self, delay):
        try:
            self._pump_id = self.root.after(delay, self._pump)
        except Exception:
            self._pump_id = None

    # _pump
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

    # _on_event
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

    # _on_log
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

    # _log_clear
    def _log_clear(self):
        try:
            self.txt_log.config(state="normal")
            self.txt_log.delete("1.0", "end")
            self.txt_log.config(state="disabled")
        except Exception:
            pass

    # _log_append
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

    # _refresh
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
        try:
            self.lbl_top.config(text="{}  ·  {}".format(
                name, "● 运行中" if running else "○ 已停止"))
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
        # 视图计数 + 在线数 (P1-3 首屏信息 ≥3 条: 状态/配对码/在线/各视图计数)
        try:
            self.lbl_online.config(text="在线 {} / {}".format(online, len(self._peers)))
        except Exception:
            pass
        try:
            counts = {
                "dev": len(self._peers),
                "paired": len(self.tv_paired.get_children("")),
                "cmd": len(self.tv_cmd.get_children("")),
                "xfer": len(self.tv_xfer.get_children("")),
            }
            _caps = {"dev": "设备", "paired": "配对", "cmd": "指令", "xfer": "传输"}
            for k, b in getattr(self, "_view_btns", {}).items():
                n = counts.get(k, 0)
                b.configure(text=(_caps.get(k, k) + (" {}".format(n) if n else "")))
        except Exception:
            pass
        self._refresh_tls_label(node, running)

    # _refresh_tls_label
    def _refresh_tls_label(self, node, running):
        """刷新「传输」状态行：明文 / TLS（指纹 N 个）；未就绪→error 配色。"""
        try:
            if not running or node is None or not hasattr(node, "tls_status"):
                self.lbl_tls.config(text="传输：—", fg=utils.themed("fgm"))
                return
            st = node.tls_status() or {}
            if not st.get("enabled"):
                self.lbl_tls.config(text="传输：明文", fg=utils.themed("fgm"))
                return
            if not st.get("ready"):
                self.lbl_tls.config(
                    text="传输：TLS 未就绪 — {}".format(st.get("reason") or "unknown"),
                    fg=utils.themed("err"))
                return
            self.lbl_tls.config(
                text="传输：TLS（指纹 {} 个）".format(int(st.get("pins") or 0)),
                fg=utils.themed("sc"))
        except Exception:
            pass

    # _refresh_pair_label
    def _refresh_pair_label(self, node):
        """顶部状态条配对码提示 (被控端, 随 _pump 更新)。"""
        txt = "配对码 —"
        fg = utils.themed("fgm")
        try:
            st = node.pair_window_status() if node is not None else None
            if st and st.get("active"):
                pin = str(st.get("pin") or "")
                left = int(st.get("expires_in") or 0)
                left = left if left > 0 else 0
                txt = "配对码 {}  (剩余 {:02d}:{:02d})".format(
                    " ".join(pin), left // 60, left % 60)
                fg = utils.themed("ac")
        except Exception:
            pass
        try:
            self.lbl_pair.config(text=txt, fg=fg)
        except Exception:
            pass

    # _get_node
    def _get_node(self):
        """取当前 netlink 节点；未启动返回 None (不抛)。"""
        try:
            nl = _nl()
            return nl.get_node() if nl is not None else None
        except Exception:
            return None

    # _set_status
    def _set_status(self, text, level="info"):
        """更新底部状态行文案 (仅主线程调用)。"""
        self._status_msg = str(text or "")
        self._status_level = str(level or "info")
        try:
            self.lbl_bottom.config(text=self._status_msg)
        except Exception:
            pass

    # _confirm_action
    def _confirm_action(self, title, message):
        """本地二次确认。测试可替换本方法以绕过人工弹窗。"""
        try:
            from tkinter import messagebox
            return bool(messagebox.askyesno(title, message, parent=self.win))
        except Exception:
            return True

    # _warn
    def _warn(self, text):
        """状态行 + 告警弹窗（弹窗失败静默）。"""
        self._set_status(text, "warning")
        try:
            from tkinter import messagebox
            messagebox.showwarning("推送脚本", text, parent=self.win)
        except Exception:
            pass

    # _selected_node_ids
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

    # _on_select
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

    # _on_toggle
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

    # _on_apply
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

    # _on_add_peer
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


# ── 模块级单例门面 ──
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

