"""
dialogs.py — ACRPA 弹窗集合模块 (从 ACRPA.py 提取)

包含以下独立弹窗:
- show_help_dialog:      帮助 / 命令速查
- open_version_history:  脚本版本历史 (对比/回退)
- open_ai_panel:         AI 脚本生成器
- open_ai_debug_dialog:  AI 智能调试
- open_sched_manager:    计划任务管理 (多任务 + 执行日志)

依赖注入设计:
    dialogs 模块不 import ACRPA (避免循环导入)，
    ACRPA.py 在启动时调用 init_ctx() 注入主窗口/颜色表/字体/回调。
"""
import os, sys, threading
import tkinter
from tkinter import ttk, filedialog, messagebox

import state
from engine import engine
from utils import (_btn, _darken, log1, show_toast, attach_tooltip, themed,
                   bind_sel_bold, place_dialog, FONT_LOG, FONT_TINY,
                   sp, gap)
# UI 基础层 (阶段二第 2 项): 语义角色登记 + 换肤事件总线订阅
from ui import theme as _ui_theme

# ── 依赖注入上下文 (由 ACRPA.py 在启动时调用 init_ctx 填充) ──
root = None              # 主窗口
C = None                 # 颜色表 dict
FONT_TITLE = FONT_BODY = FONT_SMALL = FONT_BUTTON = None
APP_ROOT = ""            # 程序根目录
RES_DIR = ""             # 资源目录 (res/)
_editor_sync_to_tree = None   # 编辑器刷新回调 (版本历史/AI 面板)
_push_undo = None             # 撤销栈回调 (AI 面板)
_main_run = None              # 运行脚本回调 (计划任务"立即执行")
_tlog = None                  # ThreadSafeLog 实例 (AI 调试读取日志缓冲)
_exit_for_update = None       # 退出主程序回调 (自更新替换主程序前调用, 走统一清理流程)

# PIL 惰性加载 (与 ACRPA.py 相同模式)
_PIL_loaded = False
Image = None
ImageTk = None

_prev_colors = None    # 上次换肤时的主题快照 (ThemeBus 未显式传 prev 时的兜底)


def init_ctx(root_win=None, colors=None, fonts=None, app_root="", res_dir="",
             editor_sync_to_tree=None, push_undo=None, main_run=None, tlog=None,
             exit_for_update=None):
    """注入 ACRPA.py 提供的依赖 (模块启动时调用一次)。"""
    global root, C, FONT_TITLE, FONT_BODY, FONT_SMALL, FONT_BUTTON
    global APP_ROOT, RES_DIR, _editor_sync_to_tree, _push_undo, _main_run, _tlog
    global _exit_for_update
    root = root_win
    C = colors
    if fonts:
        FONT_TITLE, FONT_BODY, FONT_SMALL, FONT_BUTTON = fonts
    APP_ROOT = app_root
    RES_DIR = res_dir
    _editor_sync_to_tree = editor_sync_to_tree
    _push_undo = push_undo
    _main_run = main_run
    _tlog = tlog
    _exit_for_update = exit_for_update
    # 阶段二第 2 项: 订阅 ThemeBus —— 主题切换由 _refresh_theme 一次 publish 触发,
    # 本模块不再被 ACRPA 具名同步 (原先的 `dialogs.C = C` 扇出已删除)。
    _ui_theme.subscribe(refresh_theme)


def _retheme_window(win, prev, palette):
    """递归把已打开的弹窗重刷为当前主题 (语义角色优先, 与主窗 _walk 同口径)。

    Label/Button 颜色一律取「控件创建时登记的语义角色」(ui.theme.get_role),
    不做中文文案嗅探, 也不按旧颜色值反推语义组。
    """
    def _walk(p):
        for w in p.winfo_children():
            try:
                cls = w.winfo_class()
                if cls in ("Frame", "TFrame"):
                    _fr_role = _ui_theme.get_role(w)
                    if _fr_role:
                        # 有语义角色的容器 (消息气泡 / 脚本卡片 / 提示条) 按角色回填,
                        # 否则 highlightthickness 判定会把用户气泡刷成普通卡片色。
                        w.configure(bg=palette.get(_fr_role, palette["bgc"]))
                    elif int(w.cget("highlightthickness")) > 0:
                        w.configure(bg=palette["bgc"], highlightbackground=palette["bd"])
                    else:
                        w.configure(bg=palette["bg"])
                elif cls in ("Label", "TLabel"):
                    # 有语义角色的 Label (徽标/标题条/拟按钮) → bg/fg 均按角色回填;
                    # 未登记 → 与父容器同底 + 中性灰字 (不按文案嗅探)。
                    _lbl_role = _ui_theme.get_role(w)
                    _fg_tok = _ui_theme.get_fg_token(w)
                    if _lbl_role:
                        _lb = palette.get(_lbl_role, palette["bgc"])
                        w.configure(bg=_lb, fg=_ui_theme.text_on(_lb))
                    elif _fg_tok:
                        w.configure(bg=str(p.cget("bg")),
                                    fg=palette.get(_fg_tok, palette["fgm"]))
                    else:
                        w.configure(bg=str(p.cget("bg")), fg=palette["fgm"])
                elif cls == "Text":
                    w.configure(bg=palette["logbg"], fg=palette["logfg"],
                                insertbackground=palette["fgt"],
                                selectbackground=palette["acl"],
                                selectforeground=palette["fgt"])
                elif cls == "Scrollbar":
                    w.configure(bg=palette["bd"], activebackground=palette["fgm"],
                                troughcolor=palette["logbg"])
                elif cls == "Button":
                    _role = _ui_theme.get_role(w)
                    if _role:
                        _rb = palette.get(_role, palette["bgc"])
                        _rf = _ui_theme.text_on(_rb)
                        w.configure(bg=_rb, fg=_rf,
                                    activebackground=palette.get("ach", _rb),
                                    activeforeground=_ui_theme.text_on(
                                        palette.get("ach", _rb)),
                                    disabledforeground=palette["fgm"])
                    else:
                        w.configure(bg=palette["bgc"], fg=palette["fgb"],
                                    activebackground=palette["acl"],
                                    activeforeground=palette["fgt"],
                                    disabledforeground=palette["fgm"],
                                    highlightbackground=palette["bd"])
                elif cls == "Entry":
                    w.configure(bg=palette["ebg"], fg=palette["fgb"],
                                insertbackground=palette["fgt"])
                elif cls == "Combobox":
                    w.configure(background=palette["bgc"], fieldbackground=palette["bgc"],
                                foreground=palette["fgb"])
                elif cls == "TCombobox":
                    # ttk 本体走样式; 下拉弹窗是原生 Listbox, 需单独重刷
                    _ui_theme.restyle_combobox_popdown(w, palette)
                elif cls == "Canvas":
                    w.configure(bg=palette["bg"])
                elif cls == "Listbox":
                    # 选中态用 acl(focus 深底)+fgt, 与 Treeview/settings 口径统一;
                    # 旧 ac(浅蓝)+white 在暗色下对比度不足。
                    w.configure(bg=palette["ebg"], fg=palette["fgb"],
                                selectbackground=palette["acl"],
                                selectforeground=palette["fgt"])
                elif cls == "Spinbox":
                    w.configure(bg=palette["ebg"], fg=palette["fgb"],
                                buttonbackground=palette["bgc"],
                                insertbackground=palette["fgt"])
                elif cls in ("Checkbutton", "Radiobutton"):
                    _pbg = str(p.cget("bg"))
                    w.configure(bg=_pbg, fg=palette["fgb"], activebackground=_pbg,
                                activeforeground=palette["fgt"],
                                selectcolor=palette["bgc"],
                                highlightbackground=palette["bd"])
                elif cls == "Menu":
                    w.configure(bg=palette["bgc"], fg=palette["fgb"],
                                activebackground=palette["acl"],
                                activeforeground=palette["fgt"],
                                disabledforeground=palette["fgm"])
                elif cls == "PanedWindow":
                    w.configure(bg=palette["bd"], sashrelief="raised")
            except Exception:
                pass
            _walk(w)

    try:
        _walk(win)
    except Exception:
        pass


def refresh_theme(dark=None, colors=None, prev=None):
    """ThemeBus 订阅回调: 同步模块色表 + 重刷已打开的弹窗 (真实生效)。

    · 同步 `C` —— 之后新开的弹窗直接用当前主题色;
    · 对 root 下**未声明自管换肤**的 Toplevel 逐个递归重刷 —— 已打开的弹窗即时换肤
      (设置/互联/帮助/市场/Mini Bar 等自管窗口由各自模块的订阅回调处理, 此处跳过)。
    参数签名与 `ui.theme.publish(dark, colors, prev)` 对齐。
    """
    global C, _prev_colors
    if isinstance(colors, dict):
        C = colors
    if not isinstance(C, dict):
        return
    if not isinstance(prev, dict):
        prev = _prev_colors if isinstance(_prev_colors, dict) else {}
    if root is not None:
        try:
            for w in root.winfo_children():
                if w.winfo_class() != "Toplevel":
                    continue
                if _ui_theme.is_claimed(w):
                    continue
                _retheme_window(w, prev, C)
        except Exception:
            pass
    _prev_colors = dict(C)


def _load_pil():
    """Lazy load PIL modules on first use"""
    global Image, ImageTk, _PIL_loaded
    if not _PIL_loaded:
        from PIL import Image as Img, ImageTk as ITk
        Image, ImageTk = Img, ITk
        _PIL_loaded = True


def _set_window_icon(window):
    """Set the ACRPA icon on a child Toplevel window (including taskbar icon).

    与 ACRPA.py 内部版本一致: WM_SETICON 同时设置大小图标。
    """
    ico_path = os.path.join(RES_DIR, "automation.ico")
    if not os.path.exists(ico_path):
        ico_path = os.path.join(APP_ROOT, "automation.ico")
    if not os.path.exists(ico_path):
        return
    try:
        import ctypes
        window.iconbitmap(ico_path)
        WM_SETICON = 0x0080
        ICON_SMALL = 0
        ICON_BIG = 1
        IMAGE_ICON = 1
        LR_LOADFROMFILE = 0x00000010
        hicon = ctypes.windll.user32.LoadImageW(
            None, ico_path, IMAGE_ICON, 0, 0, LR_LOADFROMFILE)
        if hicon:
            hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
            if not hwnd:
                hwnd = window.winfo_id()
            ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, hicon)
            ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, hicon)
    except Exception:
        pass


# ======================================================================
# 帮助对话框
# ======================================================================
def show_help_dialog():
    """帮助 / 命令速查入口（薄委托）。

    阶段 1-2：委托给渲染层 `help_window.open_help_window()`；导入/建窗失败时
    自动回落到旧实现 `_show_help_dialog_legacy()`，保证「新窗口崩了也不会没帮助」。
    函数名 / 位置 / 签名与调用方（ACRPA.py 标题栏「?」）保持不变。
    """
    try:
        import help_window
        win = help_window.open_help_window(
            root, C, (FONT_TITLE, FONT_BODY, FONT_SMALL, FONT_BUTTON))
        if win is not None:
            return win
    except Exception as e:
        try:
            log1("帮助窗口委托失败, 回落旧实现: {}".format(e), "warning")
        except Exception:
            pass
    return _show_help_dialog_legacy()


def _show_help_dialog_legacy():
    """旧版帮助对话框（阶段 1 保留为 fallback；模态单页文本）。"""
    def _write_cmd_table(txt_widget, cmds):
        for name, desc, params, _ in cmds:
            txt_widget.insert("end", "  {}  ".format(name), "cmd")
            txt_widget.insert("end", "{}\n".format(desc), "desc")
            txt_widget.insert("end", "        参数: {}\n".format(params), "param")

    try:
        dlg = tkinter.Toplevel(root); dlg.title("帮助 — A/C RPA"); place_dialog(dlg, 500, 600)
        dlg.transient(root); dlg.grab_set(); dlg.configure(bg=C["bgc"])
        _set_window_icon(dlg)
        dlg.columnconfigure(0, weight=1); dlg.rowconfigure(1, weight=1)
        dlg.resizable(width=True, height=True); dlg.minsize(400, 500)

        contact_card = tkinter.Frame(dlg, bg=C["bgc"], highlightbackground=C["bd"], highlightthickness=1)
        contact_card.grid(row=0, column=0, sticky="ew", padx=sp("sp_sm"), pady=(sp("sp_sm"), sp("gap_tight")))
        contact_card.columnconfigure(1, weight=1)

        tkinter.Label(contact_card, text="联系与支持", font=FONT_TITLE, bg=C["bgc"],
            fg=C["fgt"]).grid(row=0, column=0, columnspan=2, sticky="w", padx=sp("sp_md"), pady=(sp("gap"), sp("gap_tight")))

        wp = None
        search_dirs = [RES_DIR, APP_ROOT]
        if getattr(sys, "frozen", False):
            search_dirs.insert(0, os.path.join(sys._MEIPASS, "res"))
        for d in search_dirs:
            p = os.path.join(d, "wechat_qrcode.png")
            if os.path.exists(p): wp = p; break

        qr_frame = tkinter.Frame(contact_card, bg=C["bgc"])
        qr_frame.grid(row=1, column=0, sticky="nw", padx=(sp("sp_md"), sp("gap")), pady=(0, sp("gap")), rowspan=3)

        # Load QR from external file only — drop wechat_qrcode.png next to the exe or in res/
        qr_loaded = False
        for d in [APP_ROOT, RES_DIR]:
            qp = os.path.join(d, "wechat_qrcode.png")
            if os.path.exists(qp):
                try:
                    _load_pil()  # Ensure PIL is loaded before using Image
                    img = Image.open(qp)
                    qr_size = 140
                    ratio = min(qr_size / img.width, qr_size / img.height)
                    img = img.resize((int(img.width * ratio), int(img.height * ratio)), Image.LANCZOS)
                    pimg = ImageTk.PhotoImage(img)
                    qr_lbl = tkinter.Label(qr_frame, image=pimg, bg=C["bgc"], bd=0)
                    qr_lbl.image = pimg
                    qr_lbl.pack()
                    qr_loaded = True
                except Exception as e:
                    log1("二维码加载异常 ({}): {}".format(qp, e), "warning")
                break
        if not qr_loaded:
            log1("未找到二维码图片，请将 wechat_qrcode.png 放到程序目录", "warning")

        info_frame = tkinter.Frame(contact_card, bg=C["bgc"])
        info_frame.grid(row=1, column=1, sticky="w", padx=(0, sp("sp_md")), pady=(sp("gap_tight"), 0))

        tkinter.Label(info_frame, text="作者：yohoten | 微信扫描二维码添加好友", font=FONT_BODY, bg=C["bgc"],
            fg=C["fgb"]).pack(anchor="w")
        tkinter.Label(info_frame, text="邮箱: yoho12138@aliyun.com", font=FONT_SMALL, bg=C["bgc"],
            fg=C["ac"], cursor="hand2").pack(anchor="w", pady=(sp("gap_tight"), 0))
        from version_info import VERSION as _APP_VERSION
        tkinter.Label(info_frame, text="版本: v{}  |  Python tkinter + pyautogui".format(_APP_VERSION),
            font=FONT_SMALL, bg=C["bgc"], fg=C["fgm"]).pack(anchor="w", pady=(sp("gap"), 0))

        btn_row = tkinter.Frame(contact_card, bg=C["bgc"])
        btn_row.grid(row=2, column=1, sticky="w", padx=(0, sp("sp_md")), pady=(sp("gap"), sp("gap")))

        def _open_doc(path):
            try: os.startfile(path)
            except Exception as e: log1("打开文档失败: {} — {}".format(path, e))

        doc_path = os.path.join(APP_ROOT, "使用说明.txt")
        readme_path = os.path.join(APP_ROOT, "README.md")

        # 使用说明: 与 README 一致 — 优先打开在线文件，失败回退本地
        DOC_URL = "https://gitee.com/yohoten/acrpa/blob/master/使用说明.txt"
        def _open_doc_online():
            try:
                os.startfile(DOC_URL)
            except Exception as e:
                log1("打开在线使用说明失败: {}".format(e), "warning")
                if os.path.exists(doc_path):
                    _open_doc(doc_path)
                else:
                    log1("无法打开使用说明（无网络且本地无文件）", "warning")
        _doc_btn = tkinter.Button(btn_row, text="打开使用说明", font=FONT_SMALL, bg=C["ac"],
            fg=_ui_theme.text_on(C["ac"]),
            relief="flat", bd=1, cursor="hand2", padx=sp("sp_md"), pady=sp("gap_tight"),
            activebackground=C["ach"],
            activeforeground=_ui_theme.text_on(C["ach"]),
            command=_open_doc_online)
        _ui_theme.set_role(_doc_btn, "ac")   # 语义角色登记 (换肤按角色回填)
        _doc_btn.pack(side="left", padx=(0, sp("gap_tight")))
        attach_tooltip(_doc_btn, "打开在线使用说明（离线时回退本地文件）")
        # README: 优先打开在线 URL (打包后本地文件不存在且渲染美观)，
        # 失败则回退本地 README.md (开发环境可离线查看)
        README_URL = "https://gitee.com/yohoten/acrpa/blob/master/README.md"
        def _open_readme():
            try:
                os.startfile(README_URL)
            except Exception as e:
                log1("打开在线 README 失败: {}".format(e), "warning")
                if os.path.exists(readme_path):
                    _open_doc(readme_path)
                else:
                    log1("无法打开 README（无网络且本地无文件）", "warning")
        _readme_btn = tkinter.Button(btn_row, text="查看 README", font=FONT_SMALL, bg=C["bgc"], fg=C["fgb"],
            relief="flat", bd=1, cursor="hand2", padx=sp("sp_md"), pady=sp("gap_tight"),
            activebackground=C["acl"], activeforeground=C["fgb"],
            command=_open_readme)
        _readme_btn.pack(side="left", padx=(0, sp("gap_tight")))
        attach_tooltip(_readme_btn, "打开在线 README（离线时回退本地文件）")
        # 打开日志目录: 用户排查「程序没反应 / 报错」时无需手工翻找 logs/
        def _open_log_dir():
            try:
                _dir = state.log_dir()
                if not os.path.isdir(_dir):
                    os.makedirs(_dir, exist_ok=True)
                os.startfile(_dir)
                log1("已打开日志目录: {}".format(_dir))
            except Exception as e:
                log1("打开日志目录失败: {}".format(e), "warning")
        _logdir_btn = tkinter.Button(btn_row, text="打开日志目录", font=FONT_SMALL,
            bg=C["bgc"], fg=C["fgb"], relief="flat", bd=1, cursor="hand2",
            padx=sp("sp_md"), pady=sp("gap_tight"),
            activebackground=C["acl"], activeforeground=C["fgb"],
            command=_open_log_dir)
        _logdir_btn.pack(side="left", padx=(0, sp("gap_tight")))
        attach_tooltip(_logdir_btn, "打开日志保存目录（acrpa_*.log / crash_*.log）")
        tkinter.Button(btn_row, text="关闭", font=FONT_SMALL, bg=C["bgc"], fg=C["fgb"],
            relief="flat", bd=1, cursor="hand2", padx=sp("sp_md"), pady=sp("gap_tight"),
            activebackground=C["acl"], activeforeground=C["fgb"],
            command=dlg.destroy).pack(side="left")

        cmd_card = tkinter.Frame(dlg, bg=C["bgc"], highlightbackground=C["bd"], highlightthickness=1)
        cmd_card.grid(row=1, column=0, sticky="nsew", padx=sp("sp_sm"), pady=(sp("gap_tight"), sp("sp_sm")))
        cmd_card.columnconfigure(0, weight=1); cmd_card.rowconfigure(1, weight=1)

        tkinter.Label(cmd_card, text="命令速查", font=FONT_TITLE, bg=C["bgc"],
            fg=C["fgt"]).grid(row=0, column=0, sticky="w", padx=sp("sp_md"), pady=(sp("gap"), sp("gap_tight")))

        txt_frame = tkinter.Frame(cmd_card, bg=C["bgc"])
        txt_frame.grid(row=1, column=0, sticky="nsew", padx=sp("gap"), pady=(0, sp("gap")))
        txt_frame.columnconfigure(0, weight=1); txt_frame.rowconfigure(0, weight=1)

        help_txt = tkinter.Text(txt_frame, font=FONT_BODY, wrap="word",
            bg=C["logbg"], fg=C["logfg"], relief="flat", bd=0, padx=sp("sp_sm"), pady=sp("gap"),
            selectbackground=C["acl"], selectforeground=C["fgt"], cursor="arrow")
        help_txt.grid(row=0, column=0, sticky="nsew")

        help_scroll = tkinter.Scrollbar(txt_frame, width=sp("sb_w"), relief="flat", elementborderwidth=0,
            bg=C["bd"], activebackground=C["fgm"], troughcolor=C["logbg"])
        help_scroll.grid(row=0, column=1, sticky="ns")
        help_txt.config(yscrollcommand=help_scroll.set); help_scroll.config(command=help_txt.yview)

        help_txt.tag_configure("h", font=FONT_BUTTON, foreground=C["fgt"])
        help_txt.tag_configure("cmd", font=FONT_BUTTON, foreground=C["ac"])
        help_txt.tag_configure("desc", foreground=C["fgb"])
        help_txt.tag_configure("param", foreground=C["fgm"], font=FONT_SMALL)
        help_txt.tag_configure("sep", foreground=C["bd"])

        import commands
        all_cmds = commands.list_all()
        base_ops = all_cmds[:10]
        more_ops = all_cmds[10:]

        help_txt.insert("end", "基础操作\n", "h")
        _write_cmd_table(help_txt, base_ops)
        help_txt.insert("end", "\n更多操作\n", "h")
        _write_cmd_table(help_txt, more_ops)
        help_txt.insert("end", "\n")
        help_txt.insert("end", "━" * 40 + "\n", "sep")
        help_txt.insert("end", "提示: 脚本必须为 .xls 格式, Excel中不填的参数写 None\n", "param")
        help_txt.insert("end", "图片路径不能含中文, 图片名不要用纯数字\n", "param")
        help_txt.insert("end", "True/False 参数前加单引号, 如 'True\n", "param")
        help_txt.insert("end", "━" * 40 + "\n", "sep")
        help_txt.config(state="disabled")

        dlg.bind("<Escape>", lambda e: dlg.destroy())
        dlg.lift(); dlg.focus_force()
    except Exception:
        import traceback
        log1("帮助窗口加载失败:\n{}".format(traceback.format_exc()), "error")


# ======================================================================
# 版本历史对话框
# ======================================================================
def open_version_history():
    """打开版本历史对话框：查看/对比/回退脚本版本"""
    if not state.filename:
        messagebox.showwarning("提示", "请先保存脚本后再查看版本历史")
        return

    from version_manager import get_version_manager
    vm = get_version_manager()

    dlg = tkinter.Toplevel(root)
    # v3 §2.3 试点: 骨架统一负责 底色/标题/尺寸/最小尺寸/居中/模态,
    # 留白全部走 utils token (原先 680x480 / minsize(500,350) / padx=sp("gap") 等字面量)
    from ui.window_shell import apply_window_shell
    shell = apply_window_shell(
        dlg, "版本历史 — {}".format(os.path.basename(state.filename)),
        size="lg", min_size=("dlg_sm_w", "dlg_sm_h"), parent=root,
        icon_setter=_set_window_icon, colors=C)
    vh_body = shell.content

    # 工具栏
    vh_toolbar = tkinter.Frame(vh_body, bg=C["bgc"])
    vh_toolbar.grid(row=0, column=0, sticky="ew")
    tkinter.Label(vh_toolbar, text="脚本版本历史", font=FONT_TITLE, bg=C["bgc"],
        fg=C["fgt"]).pack(side="left")
    tkinter.Label(vh_toolbar, text="(自动保存每次 Ctrl+S 的快照)", font=FONT_SMALL,
        bg=C["bgc"], fg=C["fgm"]).pack(side="left", padx=(sp("sp_sm"), 0))
    vh_status = tkinter.StringVar(value="")
    tkinter.Label(vh_toolbar, textvariable=vh_status, font=FONT_SMALL,
        bg=C["bgc"], fg=C["ac"]).pack(side="right")

    # 版本列表
    vh_list_frame = tkinter.Frame(vh_body, bg=C["bgc"], highlightbackground=C["bd"], highlightthickness=1)
    vh_list_frame.grid(row=1, column=0, sticky="nsew",
                       pady=(gap("gap_tight"), gap("gap_tight")))
    vh_list_frame.columnconfigure(0, weight=1)
    vh_list_frame.rowconfigure(0, weight=1)

    vh_tree = ttk.Treeview(vh_list_frame,
        columns=("version", "time", "rows", "comment"), show="headings", selectmode="browse")
    vh_tree.heading("version", text="版本")
    vh_tree.heading("time", text="时间")
    vh_tree.heading("rows", text="行数")
    vh_tree.heading("comment", text="备注")
    vh_tree.column("version", width=60, anchor="center")
    vh_tree.column("time", width=160)
    vh_tree.column("rows", width=60, anchor="center")
    vh_tree.column("comment", width=150, anchor="center")
    # §4.5 降级: show="headings" 无 #0 列 → 选中行加粗 (整行 acl 底由 style.map 提供)
    bind_sel_bold(vh_tree)

    vh_sy = tkinter.Scrollbar(vh_list_frame, orient="vertical", command=vh_tree.yview,
        width=sp("sb_w"), relief="flat", bg=C["bd"], troughcolor=C["bgc"])
    vh_tree.configure(yscrollcommand=vh_sy.set)
    vh_tree.grid(row=0, column=0, sticky="nsew")
    vh_sy.grid(row=0, column=1, sticky="ns")

    # 底部操作按钮
    vh_btn_frame = tkinter.Frame(vh_body, bg=C["bgc"])
    vh_btn_frame.grid(row=2, column=0, sticky="ew")

    def _vh_refresh():
        for item in vh_tree.get_children():
            vh_tree.delete(item)
        history = vm.get_history(state.filename)
        for vn, ts, rc, comment in history:
            vh_tree.insert("", "end", values=("v{}".format(vn), ts, rc, comment or ""))
        vh_status.set("共 {} 个版本".format(len(history)))

    def _vh_restore():
        sel = vh_tree.selection()
        if not sel:
            messagebox.showwarning("提示", "请先选择一个版本")
            return
        vals = vh_tree.item(sel[0], "values")
        vn_str = vals[0]  # "v3"
        vn = int(vn_str[1:])

        if not messagebox.askyesno("确认回退",
            "确定要回退到 {} ({}) 吗？\n当前未保存的修改将丢失。".format(vn_str, vals[1])):
            return

        rows = vm.restore_version(state.filename, vn)
        if rows:
            state._editor_rows = rows
            _editor_sync_to_tree()
            show_toast(root, "已回退到 {} ({} 行)".format(vn_str, len(rows)), "success")
            dlg.destroy()

    def _vh_diff():
        sel = vh_tree.selection()
        if not sel:
            messagebox.showwarning("提示", "请先选择一个版本")
            return
        vals = vh_tree.item(sel[0], "values")
        vn = int(vals[0][1:])

        # 对比选中的版本与当前编辑器内容
        diff_text = vm.diff_versions(state.filename, vn, None)

        # 弹窗显示 diff
        dd = tkinter.Toplevel(dlg)
        dd.title("版本对比: v{} ↔ 当前".format(vn))
        place_dialog(dd, 600, 400, parent=dlg)
        dd.transient(dlg)
        dd.configure(bg=C["bgc"])
        _set_window_icon(dd)
        dd.columnconfigure(0, weight=1)
        dd.rowconfigure(0, weight=1)

        dtxt = tkinter.Text(dd, font=FONT_LOG, bg=C["logbg"], fg=C["logfg"],
            wrap="none", relief="flat", bd=0, padx=sp("sp_sm"), pady=sp("gap"))
        dtxt.grid(row=0, column=0, sticky="nsew")
        ds = tkinter.Scrollbar(dd, orient="vertical", command=dtxt.yview,
                               width=sp("sb_w"))
        ds.grid(row=0, column=1, sticky="ns")
        dtxt.config(yscrollcommand=ds.set)
        dtxt.insert("1.0", diff_text)
        dtxt.config(state="disabled")

        tkinter.Button(dd, text="关闭", font=FONT_BUTTON, bg=C["bgc"], fg=C["fgb"],
            command=dd.destroy).grid(row=1, column=0, pady=sp("gap"))
        dd.bind("<Escape>", lambda e: dd.destroy())

    _btn(vh_btn_frame, "回退到此版本", _vh_restore, "wn", "white").pack(side="left", padx=sp("gap_tight"))
    _btn(vh_btn_frame, "对比差异", _vh_diff, "ac", "white").pack(side="left", padx=sp("gap_tight"))
    _btn(vh_btn_frame, "刷新", _vh_refresh, C["bgc"], C["fgb"]).pack(side="left", padx=sp("gap_tight"))
    tkinter.Label(vh_btn_frame, text="提示: 每次保存自动创建版本快照",
        font=FONT_SMALL, bg=C["bgc"], fg=C["fgm"]).pack(side="right")

    _vh_refresh()
    dlg.bind("<Escape>", lambda e: dlg.destroy())


# ======================================================================
# 失败现场查看器 (只读: 查看变量/截图/上下文 + 两次运行对比, 不重跑)
# ======================================================================
def open_failure_viewer():
    """打开失败现场查看器。

    扫描 ``engine.failure_context_dir()`` 下的 ``fail_context_*.json``,
    按时间倒序列出; 选中一条查看失败时的变量 (脱敏)、截图、命令参数与上下文;
    支持选取两条记录做「两次运行对比」(变量并集 diff + 截图并排 + 元信息)。
    全程只读, 不触发任何重跑。
    """
    try:
        from engine import scan_failure_contexts as _scan
    except Exception:
        _scan = lambda d=None: []

    def _red(v):
        try:
            import interp
            return interp.redact(v)
        except Exception:
            return str(v)

    def _show_img(label, path, w=340, h=210):
        if not path or not os.path.exists(path):
            label.configure(image="", text="(无截图)")
            label.image = None
            return
        try:
            _load_pil()
            img = Image.open(path)
            ratio = min(w / img.width, h / img.height)
            if ratio < 1.0:
                img = img.resize((max(1, int(img.width * ratio)),
                                  max(1, int(img.height * ratio))), Image.LANCZOS)
            pimg = ImageTk.PhotoImage(img)
            label.configure(image=pimg, text="")
            label.image = pimg
        except Exception:
            label.configure(image="", text="(截图加载失败)")
            label.image = None

    dlg = tkinter.Toplevel(root)
    from ui.window_shell import apply_window_shell
    shell = apply_window_shell(
        dlg, "失败现场查看", size="lg", min_size=("dlg_sm_w", "dlg_sm_h"),
        parent=root, icon_setter=_set_window_icon, colors=C)
    body = shell.content
    body.columnconfigure(0, weight=1)
    body.rowconfigure(1, weight=1)

    # ── 工具栏 ──
    tb = tkinter.Frame(body, bg=C["bgc"])
    tb.grid(row=0, column=0, sticky="ew", pady=(0, gap("gap_tight")))
    tkinter.Label(tb, text="失败现场查看", font=FONT_TITLE, bg=C["bgc"],
                  fg=C["fgt"]).pack(side="left")
    status_var = tkinter.StringVar(value="")
    tkinter.Label(tb, textvariable=status_var, font=FONT_SMALL,
                  bg=C["bgc"], fg=C["ac"]).pack(side="right")

    # ── 主体: 左列表 + 右详情 ──
    pan = tkinter.PanedWindow(body, orient="horizontal",
                              bg=C["bgc"], sashwidth=sp("sb_w"),
                              sashrelief="flat", relief="flat", bd=0)
    pan.grid(row=1, column=0, sticky="nsew")

    # 左: 记录列表
    left = tkinter.Frame(pan, bg=C["bgc"])
    left.columnconfigure(0, weight=1)
    left.rowconfigure(0, weight=1)
    tree = ttk.Treeview(left, columns=("time", "run", "target", "row", "cmd", "msg"),
                        show="headings", selectmode="extended")
    for cid, txt, w, anchor in (
        ("time", "时间", 150, "w"),
        ("run", "run_id", 110, "center"),
        ("target", "目标", 120, "w"),
        ("row", "行", 44, "center"),
        ("cmd", "命令", 100, "w"),
        ("msg", "错误", 220, "w"),
    ):
        tree.heading(cid, text=txt)
        tree.column(cid, width=w, anchor=anchor, stretch=(cid == "msg"))
    bind_sel_bold(tree)
    tsy = tkinter.Scrollbar(left, orient="vertical", command=tree.yview,
                            width=sp("sb_w"), relief="flat", bg=C["bd"],
                            troughcolor=C["bgc"])
    tree.configure(yscrollcommand=tsy.set)
    tree.grid(row=0, column=0, sticky="nsew")
    tsy.grid(row=0, column=1, sticky="ns")
    pan.add(left, minsize=280, width=560)

    # 右: 详情
    right = tkinter.Frame(pan, bg=C["bgc"])
    right.columnconfigure(0, weight=1)
    right.rowconfigure(2, weight=1)
    meta_var = tkinter.StringVar(value="(选择左侧记录查看详情)")
    tkinter.Label(right, textvariable=meta_var, font=FONT_SMALL, bg=C["bgc"],
                  fg=C["fgb"], justify="left", anchor="w",
                  wraplength=520).grid(row=0, column=0, sticky="ew")

    sub = tkinter.PanedWindow(right, orient="horizontal", bg=C["bgc"],
                              sashwidth=sp("sb_w"), sashrelief="flat",
                              relief="flat", bd=0)
    sub.grid(row=1, column=0, sticky="nsew", pady=gap("gap_tight"))

    # 变量表
    vf = tkinter.Frame(sub, bg=C["bgc"])
    vf.columnconfigure(0, weight=1)
    vf.rowconfigure(0, weight=1)
    vtree = ttk.Treeview(vf, columns=("k", "v"), show="headings", selectmode="browse")
    vtree.heading("k", text="变量")
    vtree.heading("v", text="值 (脱敏)")
    vtree.column("k", width=140, anchor="w")
    vtree.column("v", width=260, anchor="w")
    vsy = tkinter.Scrollbar(vf, orient="vertical", command=vtree.yview,
                            width=sp("sb_w"), relief="flat", bg=C["bd"],
                            troughcolor=C["bgc"])
    vtree.configure(yscrollcommand=vsy.set)
    vtree.grid(row=0, column=0, sticky="nsew")
    vsy.grid(row=0, column=1, sticky="ns")
    sub.add(vf, minsize=200, width=400)

    # 截图
    shot_frame = tkinter.Frame(sub, bg=C["bgc"])
    shot_lbl = tkinter.Label(shot_frame, text="(无截图)", font=FONT_SMALL,
                             bg=C["bgc"], fg=C["fgm"])
    shot_lbl.pack(expand=True, fill="both")
    sub.add(shot_frame, minsize=200, width=360)

    # 上下文 (prev/next 行)
    ctx_text = tkinter.Text(right, font=FONT_LOG, bg=C["logbg"], fg=C["logfg"],
                            height=4, wrap="none", relief="flat", bd=0,
                            padx=sp("sp_sm"), pady=sp("gap"))
    ctx_sy = tkinter.Scrollbar(right, orient="vertical", command=ctx_text.yview,
                               width=sp("sb_w"))
    ctx_text.grid(row=2, column=0, sticky="nsew", pady=(gap("gap_tight"), 0))
    ctx_sy.grid(row=2, column=1, sticky="ns")
    ctx_text.config(yscrollcommand=ctx_sy.set, state="disabled")
    pan.add(right, minsize=320, width=620)

    # ── 底部操作 ──
    bf = tkinter.Frame(body, bg=C["bgc"])
    bf.grid(row=2, column=0, sticky="ew", pady=(gap("gap_tight"), 0))
    _btn(bf, "对比两次运行", lambda: _compare_placeholder(), "ac", "white").pack(side="left", padx=sp("gap_tight"))
    _btn(bf, "刷新", lambda: _refresh(), C["bgc"], C["fgb"]).pack(side="left", padx=sp("gap_tight"))
    _btn(bf, "关闭", dlg.destroy, C["bgc"], C["fgb"]).pack(side="right", padx=sp("gap_tight"))

    _all = []

    def _refresh():
        nonlocal _all
        _all = _scan()
        for item in tree.get_children():
            tree.delete(item)
        for it in _all:
            tree.insert("", "end", iid=str(id(it)), values=(
                it.get("time", ""),
                it.get("run_id", "") or "-",
                os.path.basename(str(it.get("script", "") or "")),
                it.get("row", ""),
                it.get("cmd", ""),
                str(it.get("message", ""))[:80],
            ))
        status_var.set("共 {} 条失败记录".format(len(_all)))

    def _by_selection():
        sel = tree.selection()
        out = []
        for iid in sel:
            for it in _all:
                if str(id(it)) == iid:
                    out.append(it)
                    break
        return out

    def _on_select(_evt=None):
        sel = _by_selection()
        if not sel:
            meta_var.set("(选择左侧记录查看详情)")
            vtree.delete(*vtree.get_children())
            _show_img(shot_lbl, "", 0, 0)
            return
        it = sel[0]
        meta_var.set(
            "run_id: {}\n时间: {}\n目标: {}\n行: {}    命令: {}    尝试: {}\n错误: {}".format(
                it.get("run_id") or "-",
                it.get("time") or "-",
                it.get("script") or "-",
                it.get("row") or "-",
                it.get("cmd") or "-",
                it.get("attempts") or 0,
                str(it.get("message") or "")[:200],
            ))
        for item in vtree.get_children():
            vtree.delete(item)
        for k, v in (it.get("vars") or {}).items():
            vtree.insert("", "end", values=(str(k), _red(v)))
        _show_img(shot_lbl, it.get("screenshot") or "", 340, 210)
        # 上下文
        ctx = it.get("context") or {}
        lines = []
        prev = ctx.get("prev")
        nxt = ctx.get("next")
        if prev:
            lines.append("上一行: {} {}".format(prev.get("cmd", ""), " ".join(prev.get("args", []))))
        lines.append(">>> 失败行: {} {}".format(it.get("cmd", ""), " ".join(it.get("args", []))))
        if nxt:
            lines.append("下一行: {} {}".format(nxt.get("cmd", ""), " ".join(nxt.get("args", []))))
        ctx_text.config(state="normal")
        ctx_text.delete("1.0", "end")
        ctx_text.insert("1.0", "\n".join(lines))
        ctx_text.config(state="disabled")

    def _compare_placeholder():
        sel = _by_selection()
        if len(sel) < 2:
            messagebox.showwarning("提示", "请先在左侧按住 Ctrl 选中两条失败记录")
            return
        _compare(sel[0], sel[1])

    def _compare(a, b):
        dd = tkinter.Toplevel(dlg)
        dd.title("失败现场对比")
        place_dialog(dd, 720, 560, parent=dlg)
        dd.transient(dlg)
        dd.configure(bg=C["bgc"])
        _set_window_icon(dd)
        dd.columnconfigure(0, weight=1)
        dd.rowconfigure(0, weight=1)

        outer = tkinter.Frame(dd, bg=C["bgc"])
        outer.grid(row=0, column=0, sticky="nsew", padx=sp("gap"), pady=sp("gap"))
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(1, weight=1)

        tkinter.Label(outer, text="A: {}  [{}]        B: {}  [{}]".format(
            a.get("time"), a.get("run_id") or "-", b.get("time"), b.get("run_id") or "-"),
            font=FONT_SMALL, bg=C["bgc"], fg=C["fgt"], anchor="w",
            justify="left").grid(row=0, column=0, sticky="ew", pady=(0, gap("gap_tight")))

        # 变量并集 diff 表
        dvf = tkinter.Frame(outer, bg=C["bgc"])
        dvf.grid(row=1, column=0, sticky="nsew")
        dvf.columnconfigure(0, weight=1)
        dvf.rowconfigure(0, weight=1)
        dtree = ttk.Treeview(dvf, columns=("k", "sa", "va", "vb"),
                             show="headings", selectmode="browse")
        dtree.heading("k", text="变量")
        dtree.heading("sa", text="状态")
        dtree.heading("va", text="运行 A")
        dtree.heading("vb", text="运行 B")
        dtree.column("k", width=130, anchor="w")
        dtree.column("sa", width=56, anchor="center")
        dtree.column("va", width=230, anchor="w")
        dtree.column("vb", width=230, anchor="w")
        dsy = tkinter.Scrollbar(dvf, orient="vertical", command=dtree.yview,
                                width=sp("sb_w"), relief="flat", bg=C["bd"],
                                troughcolor=C["bgc"])
        dtree.configure(yscrollcommand=dsy.set)
        dtree.grid(row=0, column=0, sticky="nsew")
        dsy.grid(row=0, column=1, sticky="ns")

        _MISSING = object()
        av = a.get("vars") or {}
        bv = b.get("vars") or {}
        for k in sorted(set(av) | set(bv)):
            x, y = av.get(k, _MISSING), bv.get(k, _MISSING)
            if x is _MISSING:
                st, sx, sy = "仅B", "-", _red(y)
            elif y is _MISSING:
                st, sx, sy = "仅A", _red(x), "-"
            elif x != y:
                st, sx, sy = "变更", _red(x), _red(y)
            else:
                st, sx, sy = "相同", _red(x), _red(y)
            dtree.insert("", "end", values=(str(k), st, sx, sy),
                         tags=(st,))
        dtree.tag_configure("仅B", foreground=C["wn"])
        dtree.tag_configure("仅A", foreground=C["wn"])
        dtree.tag_configure("变更", foreground=C["ac"])

        _btn(outer, "关闭", dd.destroy, C["bgc"], C["fgb"]).grid(
            row=2, column=0, sticky="e", pady=(gap("gap_tight"), 0))
        dd.bind("<Escape>", lambda e: dd.destroy())

    tree.bind("<<TreeviewSelect>>", _on_select)
    _refresh()
    dlg.bind("<Escape>", lambda e: dlg.destroy())


# ======================================================================
# AI 脚本生成器
# ======================================================================
# 工作流 AI 生成的建议提示词 (与脚本侧 AI_QUICK_PROMPTS 对齐的轻量版)
_WF_AI_QUICK_PROMPTS = {
    "自动归档文件": "把指定文件夹里的文件按扩展名归档到不同子文件夹，完成后输出日志",
    "网页数据抓取": "打开网页抓取表格数据，写入变量并记录日志",
    "定时签到": "每天打开指定网页，点击签到按钮，截图保存结果",
    "并行批量处理": "并行处理多个文件，各自独立，最后汇总结果",
    "失败重试": "执行一个可能失败的操作，失败时等待后重试最多 3 次",
    "条件分支": "判断某个变量，满足条件执行 A，否则执行 B",
}


def _wf_step_summary(s):
    """单行工作流步骤摘要 (AI 工作流生成结果卡片用)。"""
    if not isinstance(s, dict):
        return str(s)
    t = s.get("type", "script")
    if t == "script":
        return "script {}".format(s.get("path", ""))
    if t == "command":
        return "command {}".format(s.get("cmd", ""))
    if t == "variable":
        return "variable {} = {}".format(s.get("var_name", ""), s.get("var_value", ""))
    if t in ("parallel", "loop", "retry"):
        return "{} ({} 子步骤)".format(t, len(s.get("steps", [])))
    if t == "workflow":
        return "workflow {}".format(s.get("path", ""))
    if t == "condition":
        return "condition if {}".format(s.get("if", ""))
    if t == "wait":
        return "wait {}s".format(s.get("seconds", 1))
    if t == "log":
        return "log {}".format(s.get("text", ""))
    return t


def open_ai_panel(mode="script", on_workflow_import=None):
    """Open AI generation dialog with conversational (chat-style) layout.

    mode: "script" (脚本生成, 默认) | "workflow" (工作流图生成)。两者共用同一套
    对话式界面 / 流式展示 / API Key 检查 / 错误处理, 仅生成后端与结果卡片不同:
    · script    → generate_script_content + normalize_ai_output, 卡片「插入表格」;
    · workflow  → generate_workflow_from_text, 卡片「导入工作流」(回调注入 on_workflow_import)。

    借鉴 AI Agent 对话界面:
    · 主区域为对话消息流 (用户消息 + AI 回复交替展示)
    · 底部为聊天式输入栏 (输入框 + 发送按钮一体化, Enter 发送)
    · 快速示例变为建议提示词 (suggested prompts)
    · AI 生成的脚本/工作流以结构化卡片形式展示, 附带操作按钮
    """
    _is_workflow = (mode == "workflow")
    dlg = tkinter.Toplevel(root)
    dlg.title("AI 工作流生成器" if _is_workflow else "AI 脚本生成器")
    place_dialog(dlg, 660, 720)
    dlg.minsize(500, 560)
    dlg.transient(root)
    dlg.grab_set()
    dlg.configure(bg=C["bg"])
    _set_window_icon(dlg)

    # ── 布局: 顶部状态栏(行0) + 对话流(行1, weight=1) + 输入栏(行2) ──
    dlg.columnconfigure(0, weight=1)
    dlg.rowconfigure(1, weight=1)  # 对话流随窗口扩展

    from ai_client import has_ai_key, resolve_model
    _ai_ready = has_ai_key()
    _model_name = resolve_model() or "默认"

    # ═══ Row 0: 顶部状态栏 (标题 + API 状态 + 清空对话) ═══
    top_bar = tkinter.Frame(dlg, bg=C["bgc"],
        highlightbackground=C["bd"], highlightthickness=1)
    top_bar.grid(row=0, column=0, sticky="ew")
    top_bar.columnconfigure(1, weight=1)

    # 左: 标题 (fgt 强对比, 换肤时保持)
    _title_lbl = tkinter.Label(top_bar,
        text=("  AI 工作流生成器" if _is_workflow else "  AI 脚本生成器"),
        font=FONT_TITLE, bg=C["bgc"], fg=C["fgt"])
    _ui_theme.set_fg_token(_title_lbl, "fgt")
    _title_lbl.grid(row=0, column=0, sticky="w", pady=sp("gap"))

    # 中: 模型 + API 状态 (可点击配置)
    info_frame = tkinter.Frame(top_bar, bg=C["bgc"])
    info_frame.grid(row=0, column=1, sticky="ew")

    api_dot = "●" if _ai_ready else "○"
    api_color = C["sc"] if _ai_ready else C["dg"]
    api_text = " {} ".format(api_dot)
    api_lbl = tkinter.Label(info_frame, text=api_text,
        font=FONT_SMALL, bg=C["bgc"], fg=api_color, cursor="hand2")
    # 状态点绿/红需保持语义 → 仅前景色 token
    _ui_theme.set_fg_token(api_lbl, "sc" if _ai_ready else "dg")
    api_lbl.pack(side="left")

    model_lbl = tkinter.Label(info_frame,
        text="模型: {}  ".format(_model_name),
        font=FONT_TINY, bg=C["bgc"], fg=C["fgm"], cursor="hand2")
    model_lbl.pack(side="left")

    def _open_settings():
        try:
            from ui.settings.window import open_settings_window
            open_settings_window()
        except Exception:
            pass
    api_lbl.bind("<Button-1>", lambda e: _open_settings())
    model_lbl.bind("<Button-1>", lambda e: _open_settings())
    attach_tooltip(api_lbl, "点击打开设置 → AI 增强卡" if not _ai_ready
        else "已配置, 点击修改设置")
    attach_tooltip(model_lbl, "当前模型: {}\n点击修改设置".format(_model_name))

    # 右: 清空对话按钮
    clear_chat_btn = tkinter.Button(top_bar, text="✕ 清空对话", font=FONT_TINY,
        bg=C["bgc"], fg=C["fgm"], relief="flat", bd=0, cursor="hand2",
        activebackground=C["acl"], activeforeground=C["ach"],
        highlightthickness=0, padx=sp("gap"), pady=sp("hairline"))
    clear_chat_btn.grid(row=0, column=2, sticky="e", padx=(0, sp("gap")))

    # ═══ Row 1: 对话消息流 (Canvas + Scrollbar, 支持消息气泡) ═══
    chat_frame = tkinter.Frame(dlg, bg=C["bg"])
    chat_frame.grid(row=1, column=0, sticky="nsew")
    chat_frame.columnconfigure(0, weight=1)
    chat_frame.rowconfigure(0, weight=1)

    chat_canvas = tkinter.Canvas(chat_frame, bg=C["bg"],
        highlightthickness=0, bd=0)
    chat_canvas.grid(row=0, column=0, sticky="nsew")

    chat_scroll = tkinter.Scrollbar(chat_frame, orient="vertical",
        command=chat_canvas.yview, width=sp("sb_w"), relief="flat",
        bg=C["bd"], troughcolor=C["bg"])
    chat_scroll.grid(row=0, column=1, sticky="ns")
    chat_canvas.configure(yscrollcommand=chat_scroll.set)

    # 消息容器 (所有消息气泡挂在此 Frame 内)
    chat_inner = tkinter.Frame(chat_canvas, bg=C["bg"])
    chat_inner.bind("<Configure>",
        lambda e: chat_canvas.configure(scrollregion=chat_canvas.bbox("all")))
    chat_win_id = chat_canvas.create_window((0, 0), window=chat_inner, anchor="nw")

    def _on_chat_canvas_configure(event):
        # 让内部 Frame 宽度跟随 Canvas, 消息气泡才能正确 wrap
        chat_canvas.itemconfig(chat_win_id, width=event.width)
    chat_canvas.bind("<Configure>", _on_chat_canvas_configure)

    # 鼠标滚轮滚动对话流
    def _on_chat_wheel(event):
        # 对话框关闭后, 全局 <MouseWheel> 绑定可能残留触发; 画布已销毁时静默忽略
        try:
            if chat_canvas.winfo_exists():
                chat_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        except tkinter.TclError:
            pass
    chat_canvas.bind("<MouseWheel>", _on_chat_wheel)
    # 鼠标进入对话区时绑定滚轮 (防止被其他控件抢走)
    def _on_chat_enter(event):
        chat_canvas.bind_all("<MouseWheel>", _on_chat_wheel)
    def _on_chat_leave(event):
        chat_canvas.unbind_all("<MouseWheel>")
    chat_canvas.bind("<Enter>", _on_chat_enter)
    chat_canvas.bind("<Leave>", _on_chat_leave)

    # ── 对话消息管理 ──
    _msg_widgets = []     # 所有消息行 (用于清空)
    _last_script = [None] # 最近一次生成的脚本内容 (供插入按钮使用)
    _last_workflow = [None]  # 最近一次生成的工作流 (data, issues), 供导入按钮使用

    def _scroll_to_bottom():
        dlg.update_idletasks()
        chat_canvas.yview_moveto(1.0)

    def _add_message(role, text, script_content=None, workflow_data=None):
        """向对话流添加一条消息。

        role: "user" / "ai" / "system"
        text: 消息文本
        script_content: 若提供, 在消息下方追加脚本预览卡片 + 操作按钮
        workflow_data: 若提供 (wf_data, issues), 追加工作流预览卡片 + 操作按钮
        """
        msg_frame = tkinter.Frame(chat_inner, bg=C["bg"])
        msg_frame.pack(fill="x", padx=sp("sp_md"), pady=(sp("gap_tight"), sp("gap_tight")))
        _msg_widgets.append(msg_frame)

        # 消息气泡容器
        if role == "user":
            # 用户消息: 右对齐, 强调色边框
            bubble_bg = C["acl"]
            bubble_fg = C["fgt"]
            tag_text = "你"
            tag_fg = C["ac"]
            anchor = "e"
        elif role == "ai":
            # AI 消息: 左对齐, 常规底色
            bubble_bg = C["bgc"]
            bubble_fg = C["fgb"]
            tag_text = "AI"
            tag_fg = C["sc"] if _ai_ready else C["ac"]
            anchor = "w"
        else:
            # 系统消息: 居中, 灰色
            bubble_bg = C["bg"]
            bubble_fg = C["fgm"]
            tag_text = ""
            tag_fg = C["fgm"]
            anchor = "center"

        # 角色标签
        if tag_text:
            tag_frame = tkinter.Frame(msg_frame, bg=C["bg"])
            tag_frame.pack(fill="x")
            tkinter.Label(tag_frame, text=tag_text, font=FONT_TINY,
                bg=C["bg"], fg=tag_fg).pack(side="left" if role == "ai" else "right",
                    padx=sp("gap_tight"))

        # 气泡 Frame
        bubble_frame = tkinter.Frame(msg_frame, bg=bubble_bg,
            highlightbackground=C["bd"] if role != "system" else C["bg"],
            highlightthickness=1 if role != "system" else 0,
            bd=0)
        # 气泡底色登记角色: 换肤时不会被「ht>0 → bgc」判定刷成普通卡片色
        if role == "user":
            _ui_theme.set_role(bubble_frame, "acl")
        elif role == "ai":
            _ui_theme.set_role(bubble_frame, "bgc")
        if anchor == "e":
            bubble_frame.pack(side="right", anchor="e", padx=(sp(60), 0))
        elif anchor == "w":
            bubble_frame.pack(side="left", anchor="w", padx=(0, sp(60)))
        else:
            bubble_frame.pack(anchor="center", padx=sp(60))

        bubble_frame.columnconfigure(0, weight=1)

        # 消息文本
        msg_lbl = tkinter.Label(bubble_frame, text=text, font=FONT_BODY,
            bg=bubble_bg, fg=bubble_fg, justify="left", anchor="w",
            wraplength=520, padx=sp("sp_sm"), pady=sp("gap"))
        msg_lbl.pack(fill="x")
        _ui_theme.set_role(msg_lbl, "bgc" if role == "ai" else "acl")

        # wraplength 随气泡宽度自适应 (窗口缩放时文本正确折行)
        def _adjust_wrap(event, lbl=msg_lbl):
            try:
                w = bubble_frame.winfo_width()
                if w > 40:
                    lbl.configure(wraplength=max(60, w - 24))
            except Exception:
                pass
        bubble_frame.bind("<Configure>", _adjust_wrap)

        # 脚本预览卡片 (仅 AI 生成结果)
        if script_content:
            _last_script[0] = script_content

            card_frame = tkinter.Frame(bubble_frame, bg=C["logbg"],
                highlightbackground=C["ac"], highlightthickness=1)
            _ui_theme.set_role(card_frame, "logbg")
            card_frame.pack(fill="x", padx=sp("gap"), pady=(sp("gap_tight"), sp("gap")))

            # 卡片标题行
            card_header = tkinter.Frame(card_frame, bg=C["logbg"])
            _ui_theme.set_role(card_header, "logbg")
            card_header.pack(fill="x", padx=sp("gap"), pady=(sp("gap"), sp("gap_tight")))
            tkinter.Label(card_header, text="📋 生成的脚本", font=FONT_SMALL,
                bg=C["logbg"], fg=C["ac"]).pack(side="left")

            # 行数统计
            line_count = len([l for l in script_content.split("\n")
                              if l.strip() and not l.startswith("操作")])
            tkinter.Label(card_header, text="{} 行命令".format(line_count),
                font=FONT_TINY, bg=C["logbg"], fg=C["fgm"]).pack(side="right")

            # 脚本内容 (Text widget, 带滚动条, 限高)
            script_txt = tkinter.Text(card_frame, font=FONT_LOG,
                bg=C["logbg"], fg=C["logfg"], wrap="none", relief="flat", bd=0,
                padx=sp("gap"), pady=sp("gap_tight"), height=min(12, max(4, line_count + 1)),
                insertbackground=C["fgt"],
                selectbackground=C["acl"], selectforeground=C["fgt"])
            script_txt.pack(fill="x", padx=sp("gap"), pady=(0, sp("gap_tight")))
            script_txt.insert("1.0", script_content)
            script_txt.configure(state="disabled")

            # 卡片操作按钮行
            card_btns = tkinter.Frame(card_frame, bg=C["logbg"])
            _ui_theme.set_role(card_btns, "logbg")
            card_btns.pack(fill="x", padx=sp("gap"), pady=(0, sp("gap")))

            insert_card_btn = tkinter.Button(card_btns, text="↓ 插入表格",
                font=FONT_SMALL, bg=C["sc"], fg=_ui_theme.text_on(C["sc"]),
                relief="flat", bd=1, padx=sp("sp_sm"), pady=sp("gap_tight"), cursor="hand2",
                activebackground=_darken(C["sc"]),
                activeforeground=_ui_theme.text_on(_darken(C["sc"])),
                command=lambda c=script_content: _insert_script(c))
            _ui_theme.set_role(insert_card_btn, "sc")
            insert_card_btn.pack(side="left")

            copy_card_btn = tkinter.Button(card_btns, text="📋 复制",
                font=FONT_SMALL, bg=C["bgc"], fg=C["fgb"],
                relief="flat", bd=1, padx=sp("gap"), pady=sp("gap_tight"), cursor="hand2",
                activebackground=_darken(C["bgc"]), activeforeground=C["fgb"],
                highlightbackground=C["bd"], highlightthickness=1,
                command=lambda c=script_content: _copy_script(c))
            copy_card_btn.pack(side="left", padx=sp("gap_tight"))

        # 工作流预览卡片 (AI 工作流生成结果)
        if workflow_data:
            _last_workflow[0] = workflow_data
            wf_d, wf_issues = workflow_data
            import json as _json
            steps = list(wf_d.get("steps", []))
            lines = ["工作流: {}".format(wf_d.get("name", ""))]
            for i, s in enumerate(steps):
                lines.append("[{}] {}".format(i + 1, _wf_step_summary(s)))
            if wf_issues:
                lines.append("")
                lines.append("静态检查提示:")
                for it in wf_issues:
                    lines.append("  - [{}] {}".format(
                        it.get("level", ""), it.get("message", "")))
            wf_summary = "\n".join(lines)
            wf_json = _json.dumps(wf_d, ensure_ascii=False, indent=2)

            card_frame = tkinter.Frame(bubble_frame, bg=C["logbg"],
                highlightbackground=C["ac"], highlightthickness=1)
            _ui_theme.set_role(card_frame, "logbg")
            card_frame.pack(fill="x", padx=sp("gap"), pady=(sp("gap_tight"), sp("gap")))

            card_header = tkinter.Frame(card_frame, bg=C["logbg"])
            _ui_theme.set_role(card_header, "logbg")
            card_header.pack(fill="x", padx=sp("gap"), pady=(sp("gap"), sp("gap_tight")))
            tkinter.Label(card_header, text="📋 生成的工作流", font=FONT_SMALL,
                bg=C["logbg"], fg=C["ac"]).pack(side="left")
            tkinter.Label(card_header, text="{} 步骤".format(len(steps)),
                font=FONT_TINY, bg=C["logbg"], fg=C["fgm"]).pack(side="right")

            wf_txt = tkinter.Text(card_frame, font=FONT_LOG,
                bg=C["logbg"], fg=C["logfg"], wrap="none", relief="flat", bd=0,
                padx=sp("gap"), pady=sp("gap_tight"),
                height=min(12, max(4, len(lines) + 1)),
                insertbackground=C["fgt"],
                selectbackground=C["acl"], selectforeground=C["fgt"])
            wf_txt.pack(fill="x", padx=sp("gap"), pady=(0, sp("gap_tight")))
            wf_txt.insert("1.0", wf_summary)
            wf_txt.configure(state="disabled")

            card_btns = tkinter.Frame(card_frame, bg=C["logbg"])
            _ui_theme.set_role(card_btns, "logbg")
            card_btns.pack(fill="x", padx=sp("gap"), pady=(0, sp("gap")))

            import_card_btn = tkinter.Button(card_btns, text="↓ 导入工作流",
                font=FONT_SMALL, bg=C["sc"], fg=_ui_theme.text_on(C["sc"]),
                relief="flat", bd=1, padx=sp("sp_sm"), pady=sp("gap_tight"), cursor="hand2",
                activebackground=_darken(C["sc"]),
                activeforeground=_ui_theme.text_on(_darken(C["sc"])),
                command=lambda d=wf_d, iss=wf_issues: _import_workflow(d, iss))
            _ui_theme.set_role(import_card_btn, "sc")
            import_card_btn.pack(side="left")

            copy_card_btn = tkinter.Button(card_btns, text="📋 复制 JSON",
                font=FONT_SMALL, bg=C["bgc"], fg=C["fgb"],
                relief="flat", bd=1, padx=sp("gap"), pady=sp("gap_tight"), cursor="hand2",
                activebackground=_darken(C["bgc"]), activeforeground=C["fgb"],
                highlightbackground=C["bd"], highlightthickness=1,
                command=lambda j=wf_json: _copy_script(j))
            copy_card_btn.pack(side="left", padx=sp("gap_tight"))

        _scroll_to_bottom()

    def _copy_script(content):
        """复制脚本内容到剪贴板。"""
        try:
            dlg.clipboard_clear()
            dlg.clipboard_append(content)
            show_toast(root, "已复制到剪贴板", "success")
        except Exception:
            show_toast(root, "复制失败", "error")

    def _clear_chat():
        """清空对话消息流。"""
        for w in _msg_widgets:
            try:
                w.destroy()
            except Exception:
                pass
        _msg_widgets.clear()
        _last_script[0] = None
        _last_workflow[0] = None
        _add_welcome()

    clear_chat_btn.config(command=_clear_chat)

    # ── 欢迎消息 + 建议提示词 ──
    def _add_welcome():
        if _is_workflow:
            _add_message("ai",
                "你好！我是 ACRPA AI 工作流助手。\n\n"
                "用自然语言描述你要自动化的流程，我会生成对应的工作流图"
                "（脚本 / 命令 / 变量 / 并行 / 重试 / 子工作流等步骤）。\n\n"
                "你可以试试下方的建议示例，或者直接输入你的需求。")
            _quick_prompts = _WF_AI_QUICK_PROMPTS
        else:
            _add_message("ai",
                "你好！我是 ACRPA AI 脚本助手。\n\n"
                "用自然语言描述你想要实现的自动化操作，我会生成对应的 RPA 脚本。\n\n"
                "你可以试试下方的建议示例，或者直接输入你的需求。")
            from templates import AI_QUICK_PROMPTS
            _quick_prompts = AI_QUICK_PROMPTS

        # 建议提示词卡片
        suggest_frame = tkinter.Frame(chat_inner, bg=C["bg"])
        suggest_frame.pack(fill="x", padx=sp("sp_md"), pady=(sp("gap_tight"), sp("gap")))
        _msg_widgets.append(suggest_frame)

        tkinter.Label(suggest_frame, text="💡 建议试试:", font=FONT_SMALL,
            bg=C["bg"], fg=C["fgm"]).pack(anchor="w", padx=sp("gap_tight"), pady=(0, sp("gap_tight")))

        suggestions_frame = tkinter.Frame(suggest_frame, bg=C["bg"])
        suggestions_frame.pack(fill="x", padx=sp("gap_tight"))

        # 两列布局展示建议
        names = list(_quick_prompts.keys())[:8]
        for i, name in enumerate(names):
            row = i // 2
            col = i % 2
            chip = tkinter.Button(suggestions_frame, text=name, font=FONT_SMALL,
                bg=C["bgc"], fg=C["ac"], relief="flat", bd=1, cursor="hand2",
                activebackground=C["acl"], activeforeground=C["ach"],
                highlightbackground=C["bd"], highlightthickness=1,
                padx=sp("gap"), pady=sp("gap_tight"), anchor="w",
                command=lambda n=name: _use_suggestion(_quick_prompts[n]))
            chip.grid(row=row, column=col, sticky="ew", padx=(0, sp("gap_tight")) if col == 0 else (sp("gap_tight"), 0),
                      pady=sp("gap_tight"))
            _ui_theme.set_role(chip, "ac")
        suggestions_frame.columnconfigure(0, weight=1)
        suggestions_frame.columnconfigure(1, weight=1)

        _scroll_to_bottom()

    def _use_suggestion(text):
        """点击建议提示词 → 填入输入框并聚焦。"""
        prompt_entry.delete("1.0", "end")
        prompt_entry.insert("1.0", text)
        prompt_entry.focus_set()
        _update_send_state()

    # ═══ Row 2: 底部输入栏 (聊天式: 输入框 + 发送按钮) ═══
    input_bar = tkinter.Frame(dlg, bg=C["bgc"],
        highlightbackground=C["bd"], highlightthickness=1)
    input_bar.grid(row=2, column=0, sticky="ew")
    input_bar.columnconfigure(0, weight=1)

    # 输入框容器
    input_container = tkinter.Frame(input_bar, bg=C["bgc"])
    input_container.grid(row=0, column=0, sticky="ew", padx=sp("gap"), pady=sp("gap"))
    input_container.columnconfigure(0, weight=1)

    prompt_entry = tkinter.Text(input_container, height=3, font=FONT_BODY,
        bg=C["ebg"], fg=C["fgb"], wrap="word", relief="flat", bd=0,
        padx=sp("gap"), pady=sp("gap"), insertbackground=C["fgt"],
        selectbackground=C["acl"], selectforeground=C["fgt"],
        highlightbackground=C["bd"], highlightthickness=1)
    prompt_entry.grid(row=0, column=0, sticky="ew")

    # 字符计数 (输入框右下角)
    count_var = tkinter.StringVar(value="")
    count_lbl = tkinter.Label(input_container, textvariable=count_var,
        font=FONT_TINY, bg=C["bgc"], fg=C["fgd"])
    count_lbl.place(relx=1.0, rely=1.0, anchor="se", x=-4, y=-2)

    # 发送按钮
    send_btn = tkinter.Button(input_container, text="发送", font=FONT_BUTTON,
        bg=C["ac"], fg=_ui_theme.text_on(C["ac"]), relief="flat", bd=1,
        padx=sp("sp_md"), pady=sp("gap_tight"), cursor="hand2",
        activebackground=C["ach"], activeforeground=_ui_theme.text_on(C["ach"]),
        disabledforeground=C["fgm"], state="disabled")
    _ui_theme.set_role(send_btn, "ac")
    send_btn.grid(row=0, column=1, padx=(sp("gap"), 0), sticky="ns")

    # 底部提示行 (surface_alt 登记角色, 否则换肤会被 ht 判定刷成 bg/bgc)
    hint_frame = tkinter.Frame(input_bar, bg=C["surface_alt"])
    _ui_theme.set_role(hint_frame, "surface_alt")
    hint_frame.grid(row=1, column=0, sticky="ew")
    hint_var = tkinter.StringVar(value=(
        " Enter 发送 · Ctrl+Enter 强制发送 · 输入流程描述生成工作流图" if _is_workflow
        else " Enter 发送 · Ctrl+Enter 强制发送 · 输入操作描述生成 RPA 脚本"))
    tkinter.Label(hint_frame, textvariable=hint_var,
        font=FONT_TINY, bg=C["surface_alt"], fg=C["fgm"]).pack(anchor="w", padx=sp("gap"), pady=sp("gap_tight"))

    # ── 输入框交互 ──
    _placeholder_active = [True]
    _PH_TEXT = ("描述你要自动化的流程..." if _is_workflow
                else "描述你要实现的自动化操作...")

    def _show_placeholder():
        prompt_entry.delete("1.0", "end")
        prompt_entry.insert("1.0", _PH_TEXT)
        prompt_entry.tag_add("ph", "1.0", "end")
        prompt_entry.tag_configure("ph", foreground=C["fgm"])
        _placeholder_active[0] = True
        count_var.set("")

    def _clear_placeholder():
        if _placeholder_active[0]:
            prompt_entry.delete("1.0", "end")
            prompt_entry.tag_remove("ph", "1.0", "end")
            _placeholder_active[0] = False

    def _on_entry_focus_in(event):
        _clear_placeholder()
        _update_send_state()

    def _on_entry_focus_out(event):
        if not prompt_entry.get("1.0", "end-1c").strip():
            _show_placeholder()
        _update_send_state()

    def _update_count(event=None):
        if _placeholder_active[0]:
            count_var.set("")
            return
        n = len(prompt_entry.get("1.0", "end-1c"))
        count_var.set("{} 字".format(n) if n > 0 else "")

    def _update_send_state(event=None):
        """输入框有内容时启用发送按钮。"""
        has_text = not _placeholder_active[0] and bool(prompt_entry.get("1.0", "end-1c").strip())
        send_btn.config(state="normal" if has_text else "disabled")

    prompt_entry.bind("<FocusIn>", _on_entry_focus_in)
    prompt_entry.bind("<FocusOut>", _on_entry_focus_out)
    prompt_entry.bind("<KeyRelease>", lambda e: (_update_count(), _update_send_state()))

    # Enter 发送, Shift+Enter 换行
    def _on_entry_return(event):
        if event.state & 0x1:  # Shift → 换行
            return  # 不拦截, 让 Text 正常插入换行
        _do_generate()
        return "break"  # 拦截默认换行

    prompt_entry.bind("<Return>", _on_entry_return)
    prompt_entry.bind("<Control-Return>", lambda e: (_do_generate(), "break"))

    # ── 生成逻辑 ──
    _is_generating = [False]

    def _do_generate():
        if _is_generating[0]:
            return
        user_input = prompt_entry.get("1.0", "end-1c").strip()
        if _placeholder_active[0] or not user_input:
            show_toast(root, "请先输入操作描述", "warning")
            return

        # 提前检查 API Key
        if not has_ai_key():
            _add_message("system", "⚠ 未配置 API Key, 请先在设置中配置 AI 密钥。")
            if messagebox.askyesno("未配置 API Key",
                    "尚未配置 AI 密钥, 是否打开设置窗口?"):
                _open_settings()
            return

        # 用户消息上屏
        _add_message("user", user_input)

        # 清空输入框
        prompt_entry.delete("1.0", "end")
        _placeholder_active[0] = False
        _update_send_state()
        count_var.set("")

        # AI "正在思考" 占位消息
        _is_generating[0] = True
        send_btn.config(state="disabled", text="思考中...")
        hint_var.set((" AI 正在生成工作流, 请稍候... (模型: {})" if _is_workflow
                      else " AI 正在生成脚本, 请稍候... (模型: {})").format(_model_name))

        # 思考动画消息
        thinking_frame = tkinter.Frame(chat_inner, bg=C["bg"])
        thinking_frame.pack(fill="x", padx=sp("sp_md"), pady=sp("gap_tight"))
        _msg_widgets.append(thinking_frame)
        thinking_lbl = tkinter.Label(thinking_frame,
            text="⧗ AI 正在思考...", font=FONT_SMALL,
            bg=C["bgc"], fg=C["fgm"],
            highlightbackground=C["bd"], highlightthickness=1, padx=sp("sp_sm"), pady=sp("gap_tight"))
        thinking_lbl.pack(side="left", padx=(0, sp(60)))
        _ui_theme.set_role(thinking_lbl, "bgc")
        _scroll_to_bottom()

        # 动画: 轮换省略号
        _dots = [0]
        def _animate_thinking():
            if not _is_generating[0]:
                return
            _dots[0] = (_dots[0] + 1) % 4
            try:
                thinking_lbl.configure(text="⧗ AI 正在思考" + "." * _dots[0])
                dlg.after(400, _animate_thinking)
            except Exception:
                pass
        dlg.after(400, _animate_thinking)

        def _remove_thinking():
            try:
                thinking_frame.destroy()
                if thinking_frame in _msg_widgets:
                    _msg_widgets.remove(thinking_frame)
            except Exception:
                pass

        def _generate_thread():
            try:
                if _is_workflow:
                    from ai_client import (create_client_active,
                                           generate_workflow_from_text,
                                           APIError, APIAuthError, APIRateLimitError,
                                           APITimeoutError)
                    client = create_client_active()

                    # 流式展示: 逐段更新思考占位标签 (尾部预览)
                    _streamed = []
                    def _on_delta(chunk):
                        _streamed.append(chunk)
                        tail = "".join(_streamed)[-64:].replace("\n", " ")
                        try:
                            dlg.after(0, lambda t=tail: thinking_lbl.configure(
                                text="⧗ 生成中… " + t))
                        except Exception:
                            pass

                    wf_data, wf_issues = generate_workflow_from_text(
                        client, user_input, resolve_model(), on_delta=_on_delta)

                    def _on_success():
                        _remove_thinking()
                        _is_generating[0] = False
                        send_btn.config(state="normal", text="发送")
                        hint_var.set(" ✔ 生成成功 · Enter 发送 · Ctrl+Enter 强制发送")
                        _add_message("ai",
                            "已为你生成工作流图 ({} 步骤)。\n"
                            "请查看下方卡片, 点击「导入工作流」载入编辑器。".format(
                                len(wf_data.get("steps", []))),
                            workflow_data=(wf_data, wf_issues))
                    dlg.after(0, _on_success)
                    return

                from ai_client import (create_client_active,
                                       generate_script_content,
                                       APIError, APIAuthError, APIRateLimitError,
                                       APITimeoutError)
                from templates import build_ai_prompt, normalize_ai_output

                prompt = build_ai_prompt(user_input)
                client = create_client_active()

                def _note_retry(reason, bigger):
                    try:
                        dlg.after(0, lambda: thinking_lbl.configure(
                            text="⧗ 模型思考占满预算, 以 {} tokens 重试...".format(bigger)))
                    except Exception:
                        pass

                # 流式展示: 逐段更新思考占位标签 (尾部预览)
                _streamed = []
                def _on_delta(chunk):
                    _streamed.append(chunk)
                    tail = "".join(_streamed)[-64:].replace("\n", " ")
                    try:
                        dlg.after(0, lambda t=tail: thinking_lbl.configure(
                            text="⧗ 生成中… " + t))
                    except Exception:
                        pass

                raw_output, _resp = generate_script_content(
                    client,
                    messages=[{"role": "user", "content": prompt}],
                    model=resolve_model(),
                    on_retry=_note_retry,
                    on_delta=_on_delta)

                generated_script = normalize_ai_output(raw_output)

                if not generated_script:
                    raise ValueError(
                        "AI 返回的内容无法解析为脚本表格"
                        "（已收到 {} 字符，规范化后为空）".format(len(raw_output)))

                def _on_success():
                    _remove_thinking()
                    _is_generating[0] = False
                    send_btn.config(state="normal", text="发送")
                    hint_var.set(" ✔ 生成成功 · Enter 发送 · Ctrl+Enter 强制发送")
                    _add_message("ai",
                        "已为你生成 RPA 脚本 ({} 字符)。\n"
                        "请查看下方脚本卡片, 点击「插入表格」导入编辑器。".format(
                            len(generated_script)),
                        script_content=generated_script)
                dlg.after(0, _on_success)

            except APIAuthError as e:
                def _on_err(err=e):
                    _remove_thinking()
                    _is_generating[0] = False
                    send_btn.config(state="normal", text="发送")
                    hint_var.set(" ✘ 认证失败")
                    _add_message("system", "✘ 认证失败: {}".format(err))
                    messagebox.showwarning("认证失败", str(err))
                dlg.after(0, _on_err)
            except APIRateLimitError as e:
                def _on_err(err=e):
                    _remove_thinking()
                    _is_generating[0] = False
                    send_btn.config(state="normal", text="发送")
                    hint_var.set(" ✘ 频率超限")
                    _add_message("system", "✘ 请求频率超限: {}".format(err))
                    messagebox.showwarning("请求频率超限", str(err))
                dlg.after(0, _on_err)
            except APITimeoutError as e:
                def _on_err(err=e):
                    _remove_thinking()
                    _is_generating[0] = False
                    send_btn.config(state="normal", text="发送")
                    hint_var.set(" ✘ 请求超时")
                    _add_message("system", "✘ 请求超时: {}".format(err))
                    messagebox.showwarning("请求超时", str(err))
                dlg.after(0, _on_err)
            except APIError as e:
                def _on_err(err=e):
                    _remove_thinking()
                    _is_generating[0] = False
                    send_btn.config(state="normal", text="发送")
                    hint_var.set(" ✘ API 错误")
                    _add_message("system", "✘ API 错误: {}".format(err))
                    messagebox.showerror("API 错误", str(err))
                dlg.after(0, _on_err)
            except Exception as e:
                def _on_err(err=e):
                    _remove_thinking()
                    _is_generating[0] = False
                    send_btn.config(state="normal", text="发送")
                    hint_var.set(" ✘ 生成失败")
                    _add_message("system", "✘ 生成失败: {}".format(err))
                    messagebox.showerror("生成失败", "发生未知错误:\n{}".format(err))
                dlg.after(0, _on_err)

        thread = threading.Thread(target=_generate_thread, daemon=True)
        thread.start()

    send_btn.config(command=_do_generate)

    # ── 脚本插入逻辑 ──
    def _insert_script(script_content):
        """将脚本内容插入编辑器表格。"""
        try:
            lines = script_content.split("\n")
            data_lines = []
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                if line.startswith("操作") or line.startswith("命令类型") or line.startswith("（标题行）"):
                    continue
                data_lines.append(line)

            if not data_lines:
                show_toast(root, "未找到有效的脚本数据", "warning")
                return

            from scriptdata import ScriptData
            _push_undo()
            imported_count = 0
            skipped_count = 0

            for line in data_lines:
                parts = line.split(",")
                if len(parts) < 1:
                    skipped_count += 1
                    continue
                cmd_type = parts[0].strip()
                while len(parts) < 10:
                    parts.append("None")
                args = []
                for p in parts[1:10]:
                    p_stripped = p.strip()
                    if p_stripped.lower() == 'none' or p_stripped == '':
                        args.append(None)
                    else:
                        args.append(p_stripped)
                if cmd_type in ScriptData.COMMANDS:
                    state._editor_rows.append(ScriptData(cmd_type, args))
                    imported_count += 1
                else:
                    log1("⚠  跳过未知命令: {}".format(cmd_type), "warning")
                    skipped_count += 1

            msg = "✔ 已导入 {} 条命令".format(imported_count)
            if skipped_count > 0:
                msg += " (跳过 {} 条)".format(skipped_count)
            log1(msg)
            show_toast(root, msg, "success")
            _editor_sync_to_tree()
            _add_message("system", msg)

        except Exception as e:
            messagebox.showerror("导入失败", "解析脚本时出错:\n{}".format(e))
            show_toast(root, "导入失败: {}".format(e), "error")

    # ── 工作流导入逻辑 (回调注入, 与脚本「插入表格」保持一致的地位) ──
    def _import_workflow(wf_data, issues):
        if on_workflow_import is None:
            show_toast(root, "当前编辑器不支持导入工作流", "warning")
            return
        try:
            on_workflow_import(wf_data, issues)
            n = len(wf_data.get("steps", []))
            msg = "✔ 已导入工作流「{}」({} 步骤)".format(
                wf_data.get("name", ""), n)
            show_toast(root, msg, "success")
            _add_message("system", msg)
        except Exception as e:
            messagebox.showerror("导入失败", "导入工作流时出错:\n{}".format(e))
            show_toast(root, "导入失败: {}".format(e), "error")

    # ── 初始化: 显示欢迎消息 + 建议提示词 ──
    _show_placeholder()
    dlg.after(100, _add_welcome)
    prompt_entry.focus_set()


# ======================================================================
# AI 智能调试对话框
# ======================================================================
def open_ai_debug_dialog():
    """Open AI natural language debugging dialog."""
    try:
        from ai_client import has_ai_key
        _has_key = has_ai_key()
    except Exception:
        _has_key = bool(state.API_KEY)
    if not _has_key:
        messagebox.showwarning("需要 API Key", "AI 调试功能未配置可用的 AI 密钥（请在设置中配置提供商与密钥）")
        return

    dlg = tkinter.Toplevel(root)
    # v3 §2.3 模板: 从窗口骨架统一 尺寸/最小尺寸/居中/模态/外边距 (字面量 → token)
    from ui.window_shell import apply_window_shell
    shell = apply_window_shell(
        dlg, "AI 智能调试 — ACRPA", size="md",
        min_size=("dlg_min_w", "dlg_min_h"), parent=root, modal=True,
        icon_setter=_set_window_icon, colors=C)
    ai_body = shell.content
    ai_body.rowconfigure(3, weight=1)

    # Title
    title_frame = tkinter.Frame(ai_body, bg=C["bgc"])
    title_frame.grid(row=0, column=0, sticky="ew", pady=(0, gap("gap_tight")))
    tkinter.Label(title_frame, text="AI 智能调试", font=FONT_TITLE, bg=C["bgc"],
        fg=C["fgt"]).pack(side="left")
    tkinter.Label(title_frame, text="用自然语言提问，AI 分析执行记录回答",
        font=FONT_SMALL, bg=C["bgc"], fg=C["fgm"]).pack(side="left", padx=(sp("sp_sm"), 0))

    # Quick questions
    quick_frame = tkinter.Frame(ai_body, bg=C["bgc"])
    quick_frame.grid(row=1, column=0, sticky="ew", pady=(0, gap("gap_tight")))
    quick_questions = [
        "为什么最后一条命令失败了?",
        "哪行命令最耗时?",
        "脚本为什么停止了?",
        "变量值是否符合预期?",
        "有哪些优化建议?",
    ]
    for i, q in enumerate(quick_questions):
        btn = tkinter.Button(quick_frame, text=q, font=FONT_TINY,
            bg=C["acl"], fg=C["ac"], relief="raised", bd=1, cursor="hand2",
            command=lambda qq=q: question_var.set(qq))
        btn.pack(side="left", padx=sp("hairline"), pady=sp("hairline"))

    # Question input
    input_frame = tkinter.Frame(ai_body, bg=C["bgc"])
    input_frame.grid(row=2, column=0, sticky="ew", pady=(0, gap("gap_tight")))
    input_frame.columnconfigure(0, weight=1)

    question_var = tkinter.StringVar(value="请分析最近的执行情况，有什么问题?")
    question_entry = tkinter.Entry(input_frame, textvariable=question_var,
        font=FONT_BODY, relief="solid", bd=1, bg=C["ebg"], fg=C["fgb"])
    question_entry.grid(row=0, column=0, sticky="ew", padx=(0, sp("gap_tight")))
    question_entry.select_range(0, "end")

    ask_btn = tkinter.Button(input_frame, text="▶ 提问",
        font=FONT_BUTTON, bg=C["ac"], fg=_ui_theme.text_on(C["ac"]),
        relief="flat", bd=1, padx=sp("sp_md"), pady=sp("gap_tight"), cursor="hand2")
    _ui_theme.set_role(ask_btn, "ac")
    ask_btn.grid(row=0, column=1)
    attach_tooltip(ask_btn, "提交问题给 AI 分析执行日志")

    # Answer area
    answer_frame = tkinter.Frame(ai_body, bg=C["bgc"],
        highlightbackground=C["bd"], highlightthickness=1)
    answer_frame.grid(row=3, column=0, sticky="nsew", pady=(0, gap("gap_tight")))
    answer_frame.columnconfigure(0, weight=1)
    answer_frame.rowconfigure(0, weight=1)

    answer_txt = tkinter.Text(answer_frame, font=FONT_BODY,
        bg=C["logbg"], fg=C["logfg"], wrap="word", relief="flat", bd=0,
        padx=sp("sp_sm"), pady=sp("gap"), state="disabled",
        selectbackground=C["acl"], selectforeground=C["fgt"])
    answer_txt.grid(row=0, column=0, sticky="nsew")
    answer_scroll = tkinter.Scrollbar(answer_frame, width=sp("sb_w"), relief="flat",
        bg=C["bd"], troughcolor=C["logbg"])
    answer_scroll.grid(row=0, column=1, sticky="ns")
    answer_txt.config(yscrollcommand=answer_scroll.set)
    answer_scroll.config(command=answer_txt.yview)

    # Status
    status_var = tkinter.StringVar(value="输入问题后点击提问（需要配置 API Key）")
    status_label = tkinter.Label(ai_body, textvariable=status_var, font=FONT_SMALL,
        bg=C["bgc"], fg=C["fgm"])
    status_label.grid(row=4, column=0, sticky="w", pady=(0, gap("gap_tight")))

    # Progress bar
    progress = ttk.Progressbar(ai_body, mode="indeterminate",
                               length=sp("dlg_sm_w") // 2)
    progress.grid(row=5, column=0, sticky="ew", pady=(0, gap("gap_tight")))

    def _do_ask():
        question = question_var.get().strip()
        if not question:
            show_toast(root, "请输入问题", "warning")
            return

        progress.start(10)
        status_var.set("AI 正在分析...")
        ask_btn.config(state="disabled")
        answer_txt.config(state="normal")
        answer_txt.delete("1.0", "end")
        answer_txt.insert("1.0", "⧗ 正在分析执行数据和日志，请稍候...\n")
        answer_txt.config(state="disabled")

        def _ask_thread():
            try:
                from ai_enhance import ai_debug_explain
                # 收集上下文
                timings = getattr(state, '_exec_timings', {})
                try:
                    variables = engine.variables
                except NameError:
                    variables = {}
                log_buffer = _tlog._buffer if hasattr(_tlog, '_buffer') else []
                rows = getattr(state, '_editor_rows', [])

                result = ai_debug_explain(question, timings, log_buffer, variables, rows)

                def _on_result():
                    progress.stop()
                    ask_btn.config(state="normal")
                    answer_txt.config(state="normal")
                    answer_txt.delete("1.0", "end")
                    if result:
                        answer_txt.insert("1.0", result)
                        status_var.set("✔ 分析完成")
                    else:
                        answer_txt.insert("1.0", "AI 分析未返回结果，请检查 API Key 和网络连接")
                        status_var.set("✘ 分析失败")
                    answer_txt.config(state="disabled")
                dlg.after(0, _on_result)
            except Exception as e:
                def _on_error(err=str(e)):
                    progress.stop()
                    ask_btn.config(state="normal")
                    status_var.set("✘ 出错: {}".format(err))
                dlg.after(0, _on_error)

        threading.Thread(target=_ask_thread, daemon=True).start()

    ask_btn.config(command=_do_ask)
    question_entry.bind("<Return>", lambda e: _do_ask())

    # Copy answer button
    def _copy_answer():
        content = answer_txt.get("1.0", "end-1c")
        if content.strip():
            import pyperclip
            pyperclip.copy(content)
            show_toast(root, "已复制到剪贴板", "success")

    btn_frame = tkinter.Frame(dlg, bg=C["bgc"])
    btn_frame.grid(row=6, column=0, sticky="ew", padx=sp("sp_md"), pady=(0, sp("sp_md")))
    _copy_btn = tkinter.Button(btn_frame, text="复制回答", font=FONT_BUTTON,
        bg=C["bgc"], fg=C["fgb"], relief="flat", bd=1, padx=sp("sp_md"), pady=sp("gap_tight"),
        command=_copy_answer)
    _copy_btn.pack(side="left")
    attach_tooltip(_copy_btn, "复制 AI 回答到剪贴板")
    _ui_theme.roled(tkinter.Button(btn_frame, text="关闭", font=FONT_BUTTON,
        bg=C["dg"], fg=_ui_theme.text_on(C["dg"]),
        relief="flat", bd=1, padx=sp("sp_lg"), pady=sp("gap_tight"),
        command=dlg.destroy), "dg").pack(side="right")

    dlg.bind("<Escape>", lambda e: dlg.destroy())


# ======================================================================
# 计划任务编辑表单 (L0/L1: 目标三型 + 触发器注册表 + 执行语义字段)
# ======================================================================
_SCHED_TARGET_LABELS = (("脚本", "script"), ("工作流", "workflow"), ("单命令", "command"))

# 触发器参数输入框的动态提示 (与 triggers.from_text 的解析口径一一对应)
_SCHED_TRIGGER_HINTS = {
    "once": "格式: YYYY-MM-DD HH:MM",
    "daily": "格式: HH:MM",
    "weekly": "格式: 0,1,2|HH:MM  (0=周一 … 6=周日)",
    "interval": "格式: 秒数 (如 3600)",
    "countdown": "格式: 秒数 或 H:M:S (如 90 或 0:1:30)",
    "hourly": "格式: 小时白名单 如 9,12,18 (空=每小时)",
    "hotkey": "格式: ctrl+shift+1 (需含至少一个修饰键)",
    "on_boot": "格式: 延迟秒数 (如 30)",
}


def _sched_target_kind(label):
    """目标类型下拉文本 → target_type。"""
    for text, kind in _SCHED_TARGET_LABELS:
        if text == label:
            return kind
    return "script"


def _sched_spec_text(spec):
    """trigger spec → 表单里那一行文本 (与 triggers.from_text 互逆)。"""
    spec = spec if isinstance(spec, dict) else {}
    kind = str(spec.get("kind") or "daily")
    if kind == "once":
        return str(spec.get("at") or "")
    if kind == "daily":
        return str(spec.get("time") or "09:00")
    if kind == "weekly":
        days = spec.get("days") or [0]
        try:
            days_text = ",".join(str(int(d)) for d in days)
        except Exception:
            days_text = "0"
        return "{}|{}".format(days_text, spec.get("time") or "09:00")
    if kind == "interval":
        return str(spec.get("seconds") or 3600)
    if kind == "countdown":
        if spec.get("seconds") not in (None, ""):
            return str(spec.get("seconds"))
        return "{}:{}:{}".format(int(spec.get("hours") or 0),
                                 int(spec.get("minutes") or 0),
                                 int(spec.get("secs") or 0))
    if kind == "hourly":
        try:
            return ",".join(str(int(h)) for h in (spec.get("hours") or []))
        except Exception:
            return ""
    if kind == "hotkey":
        return str(spec.get("spec") or "")
    if kind == "on_boot":
        return str(spec.get("delay_sec") or getattr(state, "DEFAULT_BOOT_DELAY_SEC", 30))
    return ""


# ══════════════════════════════════════════════════════════════════════
# L4: 搜索增强 / 粘贴路径 / 占位符提示 —— 纯逻辑（无 tkinter，可单测）
# ══════════════════════════════════════════════════════════════════════

_PINYIN = {"tried": False, "mod": None}
_PINYIN_CACHE = {}


def _pinyin_module():
    """可选增强：环境里装了 ``pypinyin`` 才启用首字母匹配，未装静默降级。

    注意：**不**把 ``pypinyin`` 写进 requirements.txt（体积约束，方案文档 §8.2）。
    """
    if not _PINYIN["tried"]:
        _PINYIN["tried"] = True
        try:
            import pypinyin                     # noqa: F401 —— 可选依赖
            _PINYIN["mod"] = pypinyin
        except Exception:                       # noqa: BLE001
            _PINYIN["mod"] = None
    return _PINYIN["mod"]


def pinyin_available():
    """→ 拼音首字母匹配是否启用（当前环境真实状态，供 UI 提示）。"""
    return _pinyin_module() is not None


def pinyin_initials(text):
    """→ 文本的拼音首字母串（小写无分隔）；未装 pypinyin 返回空串。"""
    text = str(text or "")
    if not text:
        return ""
    cached = _PINYIN_CACHE.get(text)
    if cached is not None:
        return cached
    out = ""
    mod = _pinyin_module()
    if mod is not None:
        try:
            groups = mod.lazy_pinyin(text, style=mod.Style.FIRST_LETTER)
            out = "".join(groups).lower()
        except Exception:                       # noqa: BLE001
            out = ""
    if len(_PINYIN_CACHE) > 512:
        _PINYIN_CACHE.clear()
    _PINYIN_CACHE[text] = out
    return out


def sched_search_tokens(query):
    """空格分隔的多关键词 → list（空查询返回 []）。"""
    text = str(query or "").replace("\u3000", " ")
    return [tok for tok in text.split(" ") if tok]


def normalize_date_token(token):
    """日期关键词归一化：``2026/10`` / ``2026.10`` → ``2026-10``。"""
    return str(token or "").replace("/", "-").replace(".", "-").strip()


def search_blob(row, task=None):
    """把任务列表一行的可搜索字段拼成一个字符串。"""
    parts = []
    if isinstance(row, dict):
        for key in ("name", "target", "plan", "next_run", "last_run", "status"):
            parts.append(str(row.get(key) or ""))
    if isinstance(task, dict):
        for key in ("id", "created", "start_date", "end_date"):
            parts.append(str(task.get(key) or ""))
    return " ".join(parts)


def sched_row_matches(row, query, task=None, blob=None):
    """多关键词 **AND** 过滤（含日期关键词 + 可选拼音首字母）。

    * 每个关键词都必须命中（AND 语义）；
    * 命中判定：子串（大小写不敏感）或拼音首字母子串；
    * 日期关键词（``2026-10`` / ``10-23``）归一化后子串匹配
      ``next_run / last_run / created`` 等字段 —— 无需专用日期解析。
    """
    tokens = sched_search_tokens(query)
    if not tokens:
        return True
    text = blob if blob is not None else search_blob(row, task)
    haystack = text.lower()
    initials = pinyin_initials(text)
    for token in tokens:
        needle = normalize_date_token(token).lower()
        if not needle:
            continue
        if needle in haystack:
            continue
        if initials and needle in initials:
            continue
        return False
    return True


_TARGET_EXT_KINDS = {
    ".acrpas": "script", ".xls": "script", ".xlsx": "script",
    ".json": "workflow", ".yaml": "workflow", ".yml": "workflow",
    ".exe": "command", ".lnk": "command",
}
LAUNCH_COMMAND = "启动程序"


def classify_pasted_path(text):
    """粘贴文本 → 新建任务的预填信息（纯逻辑，可单测）。

    扩展名 → 目标类型映射：
        ``.acrpas/.xls/.xlsx`` → ``script``（脚本）
        ``.json/.yaml/.yml``   → ``workflow``（工作流）
        ``.exe/.lnk``          → ``command``（单命令「启动程序」，路径进参数1）

    → ``{"ok":True,"target_type","target","target_params","name","ext"}``
      或 ``{"ok":False,"reason":...}``
    """
    raw = str(text or "").strip()
    if len(raw) > 1 and raw[:1] in ("\"", "'") and raw[-1:] == raw[:1]:
        raw = raw[1:-1].strip()
    raw = raw.strip().strip("\r\n")
    if not raw:
        return {"ok": False, "reason": "剪贴板为空"}
    if "\n" in raw or "\r" in raw:
        return {"ok": False, "reason": "只支持单个路径"}
    ext = os.path.splitext(raw)[1].lower()
    kind = _TARGET_EXT_KINDS.get(ext)
    if kind is None:
        return {"ok": False,
                "reason": "不支持的文件类型: {}".format(ext or "(无扩展名)")}
    if not os.path.exists(raw):
        return {"ok": False, "reason": "路径不存在: {}".format(raw)}
    base = os.path.splitext(os.path.basename(raw))[0] or "新任务"
    info = {"ok": True, "ext": ext, "target_type": kind, "name": base}
    if kind == "command":
        info["target"] = LAUNCH_COMMAND
        info["target_params"] = {"args": [raw]}
    else:
        info["target"] = raw
        info["target_params"] = None
    return info


INTERP_HINT_HEAD = "参数支持 {var:}/{env:}/{date:}/{arg:}"


def interp_hint_text(cmd_name=None):
    """参数输入占位符提示（判定依据 ``commands.get_no_interp``）。

    返回一段中文提示：支持哪些占位符、该命令的哪些参数被安全闸门排除插值，
    并附 ``interp.DATE_MAP_TEXT`` 的日期标记说明。**不做**参数表达式预览求值。
    """
    whole_blocked, blocked = False, frozenset()
    name = str(cmd_name or "").strip()
    if name:
        try:
            import commands as _cmd_mod
            whole_blocked, blocked = _cmd_mod.get_no_interp(name)
        except Exception:                       # noqa: BLE001
            whole_blocked, blocked = False, frozenset()
    date_text = ""
    try:
        import interp as _interp
        date_text = str(_interp.DATE_MAP_TEXT or "")
    except Exception:                           # noqa: BLE001
        date_text = "Y=四位年 M=两位月 D=两位日 h=24小时 m=两位分 s=两位秒"
    if name and whole_blocked:
        body = "「{}」全部参数不做插值（安全闸门），原样传入".format(name)
    elif name and blocked:
        body = "「{}」{} 不做插值（安全闸门）；其余{}".format(
            name, "/".join("参数{}".format(i + 1) for i in sorted(blocked)),
            INTERP_HINT_HEAD)
    elif name:
        body = "「{}」{}".format(name, INTERP_HINT_HEAD)
    else:
        body = INTERP_HINT_HEAD
    if not date_text:
        return body
    return "{}（日期标记: {}）".format(body, date_text)


def interp_hint_for_values(cmd_names):
    """脚本编辑页选中行 → 提示文案（单行给精确提示，多行/空给通用提示）。"""
    names = [str(n) for n in (cmd_names or []) if str(n or "").strip()]
    if len(names) == 1:
        return interp_hint_text(names[0])
    return interp_hint_text(None)


def _spec_hotkey_text(specs):
    """从触发器列表里取 hotkey spec 的按键文本。"""
    for spec in specs or []:
        if isinstance(spec, dict) and str(spec.get("kind")) == "hotkey":
            return str(spec.get("spec") or "")
    return ""


# ══════════════════════════════════════════════════════════════════════
# L4: 粘贴路径 / 拖放降级入口
# ══════════════════════════════════════════════════════════════════════

def new_task_from_path(parent=None, path="", on_done=None):
    """粘贴路径 → 打开「新建任务」并预填目标（拖放的**等效降级**入口）。

    ``tkinterdnd2`` 是第三方 GUI 依赖（违反纯 tkinter 约束，方案文档 §8.3），
    因此不做真拖放，改用 Ctrl+V 粘贴路径达成同样效果。
    """
    info = classify_pasted_path(path)
    if not info.get("ok"):
        try:
            show_toast(root, "无法用该路径新建任务: {}".format(info.get("reason")),
                       "warning")
        except Exception:                       # noqa: BLE001
            pass
        return None
    prefill = {"name": info["name"], "target_type": info["target_type"],
               "target": info["target"], "target_params": info["target_params"]}
    return _sched_task_dialog(parent or root, None, on_done, prefill=prefill)


def paste_path_from_clipboard(widget=None):
    """→ 剪贴板文本；失败返回空串。"""
    try:
        src = widget if widget is not None else root
        return str(src.clipboard_get())
    except Exception:                           # noqa: BLE001
        return ""


# 文本类控件：在这些控件里 Ctrl+V 必须保留原生的「粘贴文本」语义
_TEXT_WIDGET_CLASSES = frozenset({
    "Text", "Entry", "TEntry", "TCombobox", "Listbox", "Spinbox", "TSpinbox",
})


def paste_should_intercept(widget):
    """→ 当前焦点是否**不该**被 Ctrl+V 新建任务拦截（纯判定，可单测）。"""
    try:
        focus = widget.focus_get() if widget is not None else None
    except Exception:                           # noqa: BLE001
        return True
    if focus is None:
        return False
    try:
        cls = focus.winfo_class()
    except Exception:                           # noqa: BLE001
        return True
    return cls in _TEXT_WIDGET_CLASSES


def bind_paste_new_task(widget, parent=None, on_done=None):
    """给 widget 绑定 Ctrl+V：剪贴板是受支持路径时打开「新建任务」并预填。

    焦点在文本类控件时**不**拦截，避免抢掉编辑器自身的粘贴。
    """
    def _handler(event=None):
        if paste_should_intercept(widget):
            return None
        text = paste_path_from_clipboard(widget)
        if not classify_pasted_path(text).get("ok"):
            return None
        new_task_from_path(parent or widget, text, on_done)
        return "break"

    try:
        widget.bind("<Control-v>", _handler, add="+")
        widget.bind("<Control-V>", _handler, add="+")
    except Exception:                           # noqa: BLE001
        pass
    return _handler


# ══════════════════════════════════════════════════════════════════════
# L4: 任务编辑窗 —— 四页签（任务 / 计划 / 热键 / 执行）
# ══════════════════════════════════════════════════════════════════════

def _sched_task_dialog(parent, task=None, on_done=None, prefill=None):
    """新建 / 编辑计划任务（四页签：任务 / 计划 / 热键 / 执行）。

    Args:
        parent:   父窗口
        task:     编辑时的任务 dict；``None`` = 新建（旧签名保持不变）
        on_done:  保存成功后的回调
        prefill:  新建时的预填 dict（键同 task），供「粘贴路径新建」使用

    设计要点（方案文档 §5 L4 #4）：
    * **触发器 / 热键 / 执行策略是三件正交的事**，各占一页；
    * 「计划」页支持**增删多条**触发器，kind 由 ``triggers.list_infos()`` 动态生成；
    * 「热键」页编辑 ``hotkey`` 触发器 spec 与任务级 ``stop_hotkey``；
    * 保存仍走 ``scheduler.add_task()/update_task()``，列表取数仍走
      ``task_summary()/task_status()`` —— 数据面零改动（向后兼容）；
    * 校验失败会切到出错页签并把输入框描红（沿用设置窗口的定位高亮风格）。
    """
    import scheduler as sched_module
    import triggers as trg_mod
    import commands as cmd_mod

    editing = isinstance(task, dict)
    base = dict(task) if editing else dict(prefill or {})
    _specs = [dict(s) for s in (base.get("triggers") or []) if isinstance(s, dict)]
    if not _specs:
        _specs = [trg_mod.default_trigger()]

    def _to_int(text, default=0):
        try:
            return int(float(str(text).strip()))
        except (TypeError, ValueError):
            return default

    def _to_int_or_none(text):
        text = str(text).strip()
        return None if not text else _to_int(text, 0)

    dlg2 = tkinter.Toplevel(parent)
    dlg2.title("编辑计划任务" if editing else "新建计划任务")
    place_dialog(dlg2, 650, 680, parent=parent)
    dlg2.minsize(600, 620)
    dlg2.transient(parent); dlg2.grab_set()
    dlg2.configure(bg=C["bgc"])
    _set_window_icon(dlg2)

    tkinter.Label(dlg2, text="编辑计划任务" if editing else "新建计划任务",
        font=FONT_TITLE, bg=C["bgc"], fg=C["fgt"]).pack(pady=(sp("gap"), sp("gap_tight")))

    nb = ttk.Notebook(dlg2)
    nb.pack(fill="both", expand=True, padx=sp("sp_md"), pady=(0, sp("gap_tight")))
    tab_task = tkinter.Frame(nb, bg=C["bgc"])
    tab_plan = tkinter.Frame(nb, bg=C["bgc"])
    tab_hotkey = tkinter.Frame(nb, bg=C["bgc"])
    tab_exec = tkinter.Frame(nb, bg=C["bgc"])
    nb.add(tab_task, text="任务")
    nb.add(tab_plan, text="计划")
    nb.add(tab_hotkey, text="热键")
    nb.add(tab_exec, text="执行")
    _TAB_TASK, _TAB_PLAN, _TAB_HOTKEY, _TAB_EXEC = 0, 1, 2, 3

    def _mk_grid(frame):
        """在 frame 上建一套「标签 + 输入控件」网格工具 → (new_row, entry)。"""
        frame.columnconfigure(1, weight=1)
        counter = [0]

        def _new_row(text, width=11):
            idx = counter[0]
            counter[0] += 1
            if text:
                tkinter.Label(frame, text=text, font=FONT_BODY, bg=C["bgc"],
                    fg=C["fgb"], width=width, anchor="e").grid(
                    row=idx, column=0, sticky="e", padx=(0, sp("gap")), pady=sp("gap_tight"))
            return idx

        def _entry(var, width=32):
            return tkinter.Entry(frame, textvariable=var, font=FONT_BODY,
                width=width, bg=C["ebg"], fg=C["fgb"], relief="solid", bd=1)

        return _new_row, _entry

    # ══════════════════ 页签① 任务 ══════════════════
    _row_t, _entry_t = _mk_grid(tab_task)

    nv = tkinter.StringVar(value=str(base.get("name") or "新任务"))
    i = _row_t("任务名称:")
    name_entry = _entry_t(nv)
    name_entry.grid(row=i, column=1, sticky="ew", pady=sp("gap_tight"))

    cur_type = str(base.get("target_type") or "script").lower()
    ttv = tkinter.StringVar(
        value={k: t for t, k in _SCHED_TARGET_LABELS}.get(cur_type, "脚本"))
    i = _row_t("目标类型:")
    ttk.Combobox(tab_task, textvariable=ttv,
        values=[t for t, _k in _SCHED_TARGET_LABELS],
        state="readonly", width=12).grid(row=i, column=1, sticky="w", pady=sp("gap_tight"))

    tv = tkinter.StringVar(value=str(base.get("target") or base.get("script") or ""))
    i_tgt = _row_t("目标:")
    tgt_frame = tkinter.Frame(tab_task, bg=C["bgc"])
    tgt_frame.grid(row=i_tgt, column=1, sticky="ew", pady=sp("gap_tight"))
    tgt_frame.columnconfigure(0, weight=1)
    tgt_entry = tkinter.Entry(tgt_frame, textvariable=tv, font=FONT_BODY,
        bg=C["ebg"], fg=C["fgb"], relief="solid", bd=1)
    tgt_entry.grid(row=0, column=0, sticky="ew")

    def _browse_target():
        kind = _sched_target_kind(ttv.get())
        if kind == "command":
            return
        if kind == "workflow":
            ftypes = [('工作流', '*.json *.yaml *.yml'), ('全部', '*.*')]
            title = "选择工作流文件"
        else:
            ftypes = [('Excel', '*.xls *.xlsx'), ('ACRPA 脚本', '*.acrpas'), ('全部', '*.*')]
            title = "选择脚本"
        path = filedialog.askopenfilename(title=title, filetypes=ftypes, initialdir=APP_ROOT)
        if path:
            tv.set(path)

    _browse = tkinter.Button(tgt_frame, text="浏览", font=FONT_SMALL, bg=C["ac"],
        fg=_ui_theme.text_on(C["ac"]), command=_browse_target)
    _ui_theme.set_role(_browse, "ac")
    _browse.grid(row=0, column=1, padx=sp("gap_tight"))
    attach_tooltip(_browse, "选择脚本 / 工作流文件")
    cmd_combo = ttk.Combobox(tgt_frame, textvariable=tv, width=26,
        values=tuple(cmd_mod.list_names()))
    cmd_combo.grid(row=0, column=0, sticky="ew")
    cmd_combo.grid_remove()

    # 单命令参数（逗号分隔，依次填入参数槽）+ L4 #7 占位符提示
    _params = base.get("target_params") if isinstance(base.get("target_params"), dict) else {}
    _args_val = _params.get("args", _params.get("params", ""))
    if isinstance(_args_val, (list, tuple)):
        _args_val = ",".join(str(x) for x in _args_val)
    av = tkinter.StringVar(value=str(_args_val or ""))
    i = _row_t("命令参数:")
    args_entry = _entry_t(av)
    args_entry.grid(row=i, column=1, sticky="ew", pady=sp("gap_tight"))
    i = _row_t("")
    param_hint_var = tkinter.StringVar(value=interp_hint_text(None))
    tkinter.Label(tab_task, textvariable=param_hint_var, font=FONT_SMALL,
        bg=C["bgc"], fg=C["fgm"], anchor="w", justify="left",
        wraplength=420).grid(row=i, column=1, sticky="w")
    attach_tooltip(args_entry, lambda: param_hint_var.get())

    ev = tkinter.BooleanVar(value=bool(base.get("enabled", True)))
    i = _row_t("")
    ttk.Checkbutton(tab_task, text="启用该任务", variable=ev).grid(
        row=i, column=1, sticky="w", pady=(sp("gap"), 0))

    def _refresh_param_hint(*_a):
        kind = _sched_target_kind(ttv.get())
        cmd_name = tv.get().strip() if kind == "command" else None
        try:
            param_hint_var.set(interp_hint_text(cmd_name))
        except Exception:                       # noqa: BLE001
            pass

    def _on_type_change(*_a):
        kind = _sched_target_kind(ttv.get())
        if kind == "command":
            tgt_entry.grid_remove(); cmd_combo.grid()
            _browse.configure(text="命令")
        else:
            cmd_combo.grid_remove(); tgt_entry.grid()
            _browse.configure(text="浏览")
        _refresh_param_hint()

    ttv.trace_add("write", _on_type_change)
    tv.trace_add("write", _refresh_param_hint)

    # ══════════════════ 页签② 计划（触发器可多条）══════════════════
    _row_p, _entry_p = _mk_grid(tab_plan)
    _infos = trg_mod.list_infos()
    _label_of = {k: lab for k, lab in _infos}
    _kind_of_label = {lab: k for k, lab in _infos}
    _KIND_DEFAULT_TEXT = {
        "once": "", "daily": "09:00", "weekly": "0,1|09:00", "interval": "3600",
        "countdown": "60", "hourly": "", "hotkey": "",
        "on_boot": str(getattr(state, "DEFAULT_BOOT_DELAY_SEC", 30)),
    }

    i = _row_p("触发器列表:")
    trg_wrap = tkinter.Frame(tab_plan, bg=C["bgc"],
        highlightbackground=C["bd"], highlightthickness=1)
    trg_wrap.grid(row=i, column=1, sticky="ew", pady=sp("gap_tight"))
    trg_wrap.columnconfigure(0, weight=1)
    trg_tree = ttk.Treeview(trg_wrap, columns=("kind", "param"), show="headings",
        height=4, selectmode="browse")
    trg_tree.heading("kind", text="类型")
    trg_tree.column("kind", width=90, anchor="w")
    trg_tree.heading("param", text="参数 / 摘要")
    trg_tree.column("param", width=260, anchor="w")
    trg_tree.grid(row=0, column=0, sticky="ew")
    bind_sel_bold(trg_tree)

    _loading = {"on": False}
    _sel = {"idx": None}

    ktv = tkinter.StringVar(value=_label_of.get("daily", "每天"))
    i = _row_p("类型:")
    ttk.Combobox(tab_plan, textvariable=ktv, values=[lab for _k, lab in _infos],
        state="readonly", width=14).grid(row=i, column=1, sticky="w", pady=sp("gap_tight"))

    pv = tkinter.StringVar(value=_sched_spec_text(trg_mod.default_trigger()))
    i = _row_p("参数:")
    pv_entry = _entry_p(pv)
    pv_entry.grid(row=i, column=1, sticky="ew", pady=sp("gap_tight"))
    i = _row_p("")
    hint_var = tkinter.StringVar(value=_SCHED_TRIGGER_HINTS.get("daily", ""))
    tkinter.Label(tab_plan, textvariable=hint_var, font=FONT_SMALL, bg=C["bgc"],
        fg=C["fgm"], anchor="w", justify="left", wraplength=420).grid(
        row=i, column=1, sticky="w")

    mode_var = tkinter.StringVar(value="full")
    i = _row_p("整点模式:")
    mode_combo = ttk.Combobox(tab_plan, textvariable=mode_var,
        values=("full", "half", "both"), state="disabled", width=10)
    mode_combo.grid(row=i, column=1, sticky="w", pady=sp("gap_tight"))

    def _on_kind_change(*_a):
        kind = _kind_of_label.get(ktv.get(), "daily")
        hint_var.set(_SCHED_TRIGGER_HINTS.get(kind, ""))
        try:
            mode_combo.configure(state=("readonly" if kind == "hourly" else "disabled"))
        except Exception:                       # noqa: BLE001
            pass
        if not _loading["on"]:
            pv.set(_KIND_DEFAULT_TEXT.get(kind, ""))
    ktv.trace_add("write", _on_kind_change)

    def _spec_summary(spec):
        try:
            inst = trg_mod.make(spec)
            return inst.describe() if inst is not None else str(spec.get("kind"))
        except Exception:                       # noqa: BLE001
            return str(spec.get("kind"))

    def _trg_load(iid):
        try:
            idx = int(iid)
        except (TypeError, ValueError):
            return
        if not (0 <= idx < len(_specs)):
            return
        spec = _specs[idx]
        _sel["idx"] = idx
        kind = str(spec.get("kind") or "daily")
        _loading["on"] = True
        try:
            ktv.set(_label_of.get(kind, _label_of.get("daily", "每天")))
            pv.set(_sched_spec_text(spec))
            mode_var.set(str(spec.get("mode") or "full"))
        finally:
            _loading["on"] = False
        hint_var.set(_SCHED_TRIGGER_HINTS.get(kind, ""))
        try:
            mode_combo.configure(state=("readonly" if kind == "hourly" else "disabled"))
        except Exception:                       # noqa: BLE001
            pass

    def _trg_refresh(select=None):
        for iid in trg_tree.get_children():
            trg_tree.delete(iid)
        for idx, spec in enumerate(_specs):
            kind = str(spec.get("kind") or "daily")
            trg_tree.insert("", "end", iid=str(idx),
                values=(_label_of.get(kind, kind), _spec_summary(spec)))
        kids = trg_tree.get_children()
        if kids:
            want = str(select) if select is not None else kids[0]
            if want not in kids:
                want = kids[0]
            trg_tree.selection_set(want)
            trg_tree.focus(want)
            _trg_load(want)
        else:
            _sel["idx"] = None

    def _current_spec():
        kind = _kind_of_label.get(ktv.get(), "daily")
        return trg_mod.from_text(kind, pv.get().strip(), mode=mode_var.get())

    def _trg_add():
        kind = _kind_of_label.get(ktv.get(), "daily")
        if kind == "once" and not pv.get().strip():
            _alert(_TAB_PLAN, pv_entry, "「一次性」触发器需要填写具体时间")
            return
        _specs.append(_current_spec())
        _trg_refresh(len(_specs) - 1)

    def _trg_update():
        idx = _sel["idx"]
        if idx is None or not (0 <= idx < len(_specs)):
            _alert(_TAB_PLAN, pv_entry, "请先在列表里选择要更新的触发器")
            return
        _specs[idx] = _current_spec()
        _trg_refresh(idx)

    def _trg_del():
        idx = _sel["idx"]
        if idx is None or not (0 <= idx < len(_specs)):
            _alert(_TAB_PLAN, pv_entry, "请先在列表里选择要删除的触发器")
            return
        _specs.pop(idx)
        _trg_refresh(idx if idx < len(_specs) else (len(_specs) - 1))

    def _trg_reset():
        kind = _kind_of_label.get(ktv.get(), "daily")
        pv.set(_KIND_DEFAULT_TEXT.get(kind, ""))

    trg_tree.bind("<<TreeviewSelect>>",
        lambda e: _trg_load(trg_tree.selection()[0]) if trg_tree.selection() else None)

    i = _row_p("")
    _trg_bar = tkinter.Frame(tab_plan, bg=C["bgc"])
    _trg_bar.grid(row=i, column=1, sticky="w", pady=(sp("gap_tight"), sp("gap")))
    _btn(_trg_bar, "添加", _trg_add, "ac", "white").pack(side="left", padx=(0, sp("gap_tight")))
    _btn(_trg_bar, "更新选中", _trg_update, C["bgc"], C["fgb"]).pack(side="left", padx=(0, sp("gap_tight")))
    _btn(_trg_bar, "删除", _trg_del, C["dg"], "white").pack(side="left", padx=(0, sp("gap_tight")))
    _btn(_trg_bar, "清空重填", _trg_reset, C["bgc"], C["fgb"]).pack(side="left")

    sdv = tkinter.StringVar(value=str(base.get("start_date") or ""))
    i = _row_p("生效开始:")
    sd_entry = _entry_p(sdv, 16)
    sd_entry.grid(row=i, column=1, sticky="w", pady=sp("gap_tight"))
    edv = tkinter.StringVar(value=str(base.get("end_date") or ""))
    i = _row_p("生效结束:")
    ed_entry = _entry_p(edv, 16)
    ed_entry.grid(row=i, column=1, sticky="w", pady=sp("gap_tight"))
    i = _row_p("")
    tkinter.Label(tab_plan, text="格式 YYYY-MM-DD，空 = 不限（含起止当日）",
        font=FONT_SMALL, bg=C["bgc"], fg=C["fgm"], anchor="w").grid(
        row=i, column=1, sticky="w")

    # ══════════════════ 页签③ 热键 ══════════════════
    _row_h, _entry_h = _mk_grid(tab_hotkey)
    hkv = tkinter.StringVar(value=_spec_hotkey_text(_specs))
    i = _row_h("任务热键:")
    hk_entry = _entry_h(hkv, 20)
    hk_entry.grid(row=i, column=1, sticky="w", pady=sp("gap_tight"))
    attach_tooltip(hk_entry, "如 ctrl+alt+1（需含至少一个修饰键）；留空 = 不绑热键")
    i = _row_h("")
    tkinter.Label(tab_hotkey, text="如 ctrl+alt+1（需含至少一个修饰键）；留空 = 该任务不绑热键",
        font=FONT_SMALL, bg=C["bgc"], fg=C["fgm"], anchor="w", justify="left",
        wraplength=420).grid(row=i, column=1, sticky="w")

    shv = tkinter.StringVar(value=str(sched_module.exec_opt(base, "stop_hotkey", "")))
    i = _row_h("停止热键:")
    sh_entry = _entry_h(shv, 20)
    sh_entry.grid(row=i, column=1, sticky="w", pady=sp("gap_tight"))
    attach_tooltip(sh_entry, "任务级停止热键，如 ctrl+alt+f12")
    i = _row_h("")
    tkinter.Label(tab_hotkey, text="如 ctrl+alt+f12；与内建热键冲突时会在日志告警并忽略",
        font=FONT_SMALL, bg=C["bgc"], fg=C["fgm"], anchor="w", justify="left",
        wraplength=420).grid(row=i, column=1, sticky="w")

    i = _row_h("")
    tkinter.Label(tab_hotkey,
        text="说明：热键是独立的触发方式，不占用「计划」页的触发器列表；"
             "保存时会自动合并为 hotkey 触发器。",
        font=FONT_SMALL, bg=C["bgc"], fg=C["fgm"], anchor="w", justify="left",
        wraplength=420).grid(row=i, column=1, sticky="w", pady=(sp("sp_sm"), 0))

    # ══════════════════ 页签④ 执行 ══════════════════
    _row_e, _entry_e = _mk_grid(tab_exec)

    ov = tkinter.StringVar(value=str(sched_module.exec_opt(base, "on_overlap", "skip")))
    i = _row_e("重入策略:")
    ttk.Combobox(tab_exec, textvariable=ov, values=("skip", "queue", "terminate"),
        state="readonly", width=12).grid(row=i, column=1, sticky="w", pady=sp("gap_tight"))
    i = _row_e("")
    tkinter.Label(tab_exec, text="skip=运行中跳过 / queue=排队等上次结束 / terminate=先停止再执行",
        font=FONT_SMALL, bg=C["bgc"], fg=C["fgm"], anchor="w", justify="left",
        wraplength=420).grid(row=i, column=1, sticky="w")

    tov = tkinter.StringVar(value=str(sched_module.exec_opt(base, "timeout_sec", 0)))
    i = _row_e("任务超时(秒):")
    tov_entry = _entry_e(tov, 10)
    tov_entry.grid(row=i, column=1, sticky="w", pady=sp("gap_tight"))
    i = _row_e("")
    tkinter.Label(tab_exec, text="0 = 不限；到点会真正中断本次执行",
        font=FONT_SMALL, bg=C["bgc"], fg=C["fgm"], anchor="w").grid(
        row=i, column=1, sticky="w")

    mrv = tkinter.StringVar(value=str(sched_module.exec_opt(base, "missed_run", "ignore")))
    i = _row_e("错过补偿:")
    ttk.Combobox(tab_exec, textvariable=mrv,
        values=("ignore", "run_now", "run_delay"), state="readonly",
        width=12).grid(row=i, column=1, sticky="w", pady=sp("gap_tight"))

    mdv = tkinter.StringVar(value=str(sched_module.exec_opt(base, "missed_delay_sec", 60)))
    i = _row_e("补偿延迟(秒):")
    mdv_entry = _entry_e(mdv, 10)
    mdv_entry.grid(row=i, column=1, sticky="w", pady=sp("gap_tight"))

    _max_runs = base.get("max_runs")
    maxv = tkinter.StringVar(value="" if _max_runs in (None, "") else str(_max_runs))
    i = _row_e("次数上限:")
    maxv_entry = _entry_e(maxv, 10)
    maxv_entry.grid(row=i, column=1, sticky="w", pady=sp("gap_tight"))
    i = _row_e("")
    tkinter.Label(tab_exec,
        text="空 = 不限；已执行 {} 次（只读）".format(int(base.get("run_count") or 0)),
        font=FONT_SMALL, bg=C["bgc"], fg=C["fgm"], anchor="w").grid(
        row=i, column=1, sticky="w")

    # ══════════════════ 校验 / 保存 ══════════════════
    _watch = [name_entry, tgt_entry, args_entry, pv_entry, sd_entry, ed_entry,
              hk_entry, sh_entry, tov_entry, mdv_entry, maxv_entry]

    def _clear_marks():
        for widget in _watch:
            try:
                widget.configure(highlightthickness=0)
            except Exception:                   # noqa: BLE001
                pass

    def _alert(tab_idx, widget, msg):
        """保存失败定位：切页签 + 输入框描红 + 聚焦 + 提示。"""
        try:
            nb.select(tab_idx)
        except Exception:                       # noqa: BLE001
            pass
        try:
            widget.configure(highlightthickness=2,
                             highlightbackground=C["dg"], highlightcolor=C["dg"])
            widget.focus_set()
        except Exception:                       # noqa: BLE001
            pass
        try:
            messagebox.showwarning("提示", msg, parent=dlg2)
        except Exception:                       # noqa: BLE001
            pass

    def _confirm():
        _clear_marks()
        name = nv.get().strip()
        if not name:
            _alert(_TAB_TASK, name_entry, "请输入任务名称")
            return
        tkind = _sched_target_kind(ttv.get())
        target = tv.get().strip()
        if not target:
            _alert(_TAB_TASK, tgt_entry if tkind != "command" else cmd_combo,
                   "请指定目标（脚本 / 工作流 / 命令）")
            return
        if tkind == "command":
            try:
                known = set(cmd_mod.list_names())
            except Exception:                   # noqa: BLE001
                known = set()
            if known and target not in known:
                _alert(_TAB_TASK, cmd_combo,
                       "未知命令：{}（请从下拉列表选择）".format(target))
                return
        if not _specs:
            _alert(_TAB_PLAN, pv_entry, "请至少添加一个触发器")
            return
        start_date = sdv.get().strip()
        end_date = edv.get().strip()
        if start_date and trg_mod.parse_date(start_date) is None:
            _alert(_TAB_PLAN, sd_entry, "生效开始日期格式应为 YYYY-MM-DD")
            return
        if end_date and trg_mod.parse_date(end_date) is None:
            _alert(_TAB_PLAN, ed_entry, "生效结束日期格式应为 YYYY-MM-DD")
            return
        if start_date and end_date and end_date < start_date:
            _alert(_TAB_PLAN, ed_entry, "结束日期不能早于开始日期")
            return
        timeout_val = _to_int(tov.get(), 0)
        if timeout_val < 0:
            _alert(_TAB_EXEC, tov_entry, "任务超时需为 0（不限）或正整数秒")
            return
        delay_val = _to_int(mdv.get(), 60)
        if delay_val < 0:
            _alert(_TAB_EXEC, mdv_entry, "补偿延迟需为非负整数秒")
            return
        max_runs = _to_int_or_none(maxv.get())
        if max_runs is not None and max_runs <= 0:
            max_runs = None

        # 热键页 → hotkey 触发器（与「计划」页列表合并，不重复）
        hk_value = hkv.get().strip()
        if hk_value and "+" not in hk_value:
            _alert(_TAB_HOTKEY, hk_entry, "热键需含至少一个修饰键，如 ctrl+alt+1")
            return
        specs = [dict(s) for s in _specs if str(s.get("kind")) != "hotkey"]
        if hk_value:
            specs.append({"kind": "hotkey", "spec": hk_value})
        if not specs:
            _alert(_TAB_PLAN, pv_entry, "请至少保留一个触发器，或在「热键」页填写任务热键")
            return

        target_params = None
        if tkind == "command":
            target_params = {"args": [a.strip() for a in av.get().split(",") if a.strip()]}
        patch = {
            "name": name,
            "target_type": tkind,
            "target": target,
            "triggers": specs,
            "enabled": bool(ev.get()),
            "on_overlap": ov.get(),
            "timeout_sec": timeout_val,
            "missed_run": mrv.get(),
            "missed_delay_sec": delay_val,
            "max_runs": max_runs,
            "start_date": start_date,
            "end_date": end_date,
            "stop_hotkey": shv.get().strip(),
            "target_params": target_params,
        }
        try:
            if editing:
                sched_module.update_task(base.get("id"), patch)
            else:
                sched_module.add_task(
                    name, target if tkind == "script" else "", "daily", "09:00",
                    bool(ev.get()), None, tkind, target, specs,
                    on_overlap=patch["on_overlap"], timeout_sec=patch["timeout_sec"],
                    missed_run=patch["missed_run"],
                    missed_delay_sec=patch["missed_delay_sec"],
                    stop_hotkey=patch["stop_hotkey"], max_runs=max_runs,
                    start_date=start_date, end_date=end_date,
                    target_params=target_params)
        except Exception as e:                  # noqa: BLE001
            _alert(_TAB_TASK, name_entry, "保存失败: {}".format(e))
            return
        dlg2.destroy()
        if on_done:
            try:
                on_done()
            except Exception:                   # noqa: BLE001
                pass
        show_toast(root, "任务已保存: {}".format(name), "success")

    bf = tkinter.Frame(dlg2, bg=C["bgc"])
    bf.pack(pady=(sp("gap_tight"), sp("sp_sm")))
    _ui_theme.roled(tkinter.Button(bf, text="确定", command=_confirm, font=FONT_BUTTON,
        bg=C["ac"], fg=_ui_theme.text_on(C["ac"]), padx=sp("sp_md"), pady=sp("gap_tight")),
        "ac").pack(side="left", padx=sp("gap_tight"))
    tkinter.Button(bf, text="取消", command=dlg2.destroy, font=FONT_BUTTON,
        bg=C["bgc"], fg=C["fgb"], padx=sp("sp_md"), pady=sp("gap_tight")).pack(side="left", padx=sp("gap_tight"))
    dlg2.bind("<Escape>", lambda e: dlg2.destroy())

    _on_type_change()
    _trg_refresh(0)
    return dlg2
# ======================================================================
# 计划任务管理窗口
# ======================================================================
def open_sched_manager():
    """打开计划任务管理窗口（多任务表格 + 执行日志）"""
    import scheduler as sched_module
    dlg = tkinter.Toplevel(root)
    dlg.title("计划任务管理 — ACRPA")
    place_dialog(dlg, 780, 520)
    dlg.minsize(600, 400)
    dlg.transient(root)
    dlg.configure(bg=C["bgc"])
    _set_window_icon(dlg)
    dlg.columnconfigure(0, weight=1)
    dlg.rowconfigure(0, weight=0)
    dlg.rowconfigure(1, weight=1)

    # 工具栏
    mgr_toolbar = tkinter.Frame(dlg, bg=C["bgc"])
    mgr_toolbar.grid(row=0, column=0, sticky="ew", padx=sp("gap"), pady=(sp("gap"), sp("gap_tight")))

    def _mgr_new_task():
        # 表单统一走 _sched_task_dialog (目标三型 + 触发器 + 执行语义, L0/L1)
        _sched_task_dialog(dlg, None, _mgr_refresh)

    def _mgr_edit_task(tid):
        task = next((t for t in state.SCHED_TASKS if t.get("id") == tid), None)
        if task is None:
            return
        _sched_task_dialog(dlg, task, _mgr_refresh)

    _btn(mgr_toolbar, "新建任务", _mgr_new_task, "ac", "white").pack(side="left", padx=sp("hairline"))
    _btn(mgr_toolbar, "编辑", lambda: (
        _mgr_edit_task(mgr_tree.selection()[0]) if mgr_tree.selection() else None),
        C["bgc"], C["fgb"]).pack(side="left", padx=sp("hairline"))
    _btn(mgr_toolbar, "刷新", lambda: _mgr_refresh(), C["bgc"], C["fgb"]).pack(side="left", padx=sp("hairline"))
    tkinter.Frame(mgr_toolbar, bg=C["bd"], width=2, height=20).pack(side="left", fill="y", padx=sp("gap_tight"))
    _btn(mgr_toolbar, "查看日志", lambda: _mgr_show_logs(dlg),
        C["bgc"], C["fgb"]).pack(side="left", padx=sp("hairline"))
    tkinter.Frame(mgr_toolbar, bg=C["bd"], width=2, height=20).pack(side="left", fill="y", padx=sp("gap_tight"))
    # L4 #5 搜索增强: 空格分隔多关键词 AND + 日期关键词(2026-10 / 10-23)
    tkinter.Label(mgr_toolbar, text="搜索:", font=FONT_SMALL,
        bg=C["bgc"], fg=C["fgm"]).pack(side="left", padx=(0, sp("gap_tight")))
    mgr_search_var = tkinter.StringVar(value="")
    _mgr_search = tkinter.Entry(mgr_toolbar, textvariable=mgr_search_var,
        font=FONT_SMALL, width=22, relief="solid", bd=1,
        bg=C["ebg"], fg=C["fgb"], insertbackground=C["fgt"])
    _mgr_search.pack(side="left")
    attach_tooltip(_mgr_search,
        "空格分隔多关键词（同时满足）；可输日期如 2026-10 / 10-23"
        + ("；已启用拼音首字母匹配" if pinyin_available() else ""))
    _mgr_count = tkinter.Label(mgr_toolbar, text="",
        font=FONT_SMALL, bg=C["bgc"], fg=C["fgm"])
    _mgr_count.pack(side="right", padx=(sp("gap"), 0))
    tkinter.Label(mgr_toolbar, text="( 计划任务模块)",
        font=FONT_SMALL, bg=C["bgc"], fg=C["fgm"]).pack(side="right")

    # 任务表格
    list_f = tkinter.Frame(dlg, bg=C["bgc"], highlightbackground=C["bd"], highlightthickness=1)
    list_f.grid(row=1, column=0, sticky="nsew", padx=sp("gap"), pady=(0, sp("gap")))
    list_f.columnconfigure(0, weight=1); list_f.rowconfigure(0, weight=1)

    mgr_tree = ttk.Treeview(list_f,
        columns=("name", "target", "plan", "next_run", "last_run", "status"),
        show="headings", selectmode="browse")
    for c, t, w in [("name", "任务名称", 110), ("target", "目标", 150),
        ("plan", "计划", 160), ("next_run", "下次执行", 130),
        ("last_run", "上次执行", 130), ("status", "状态", 60)]:
        mgr_tree.heading(c, text=t); mgr_tree.column(c, width=w, minwidth=40)
    # §4.5 降级: show="headings" 无 #0 列 → 选中行加粗
    bind_sel_bold(mgr_tree)
    msy = tkinter.Scrollbar(list_f, orient="vertical", command=mgr_tree.yview,
        width=sp("sb_w"), relief="flat", bg=C["bd"], troughcolor=C["bgc"])
    mgr_tree.configure(yscrollcommand=msy.set)
    mgr_tree.grid(row=0, column=0, sticky="nsew"); msy.grid(row=0, column=1, sticky="ns")

    def _mgr_refresh():
        for item in mgr_tree.get_children():
            mgr_tree.delete(item)
        try:
            query = mgr_search_var.get()
        except Exception:                       # noqa: BLE001
            query = ""
        shown = 0
        for task in state.SCHED_TASKS:
            # 单一数据来源: scheduler.task_summary() (目标/计划/下次/上次/状态)
            row = sched_module.task_summary(task)
            # L4 #5: 纯函数过滤（多关键词 AND / 日期关键词 / 可选拼音首字母）
            if not sched_row_matches(row, query, task):
                continue
            tag = "en" if row["enabled"] else "dis"
            if row["status"] in ("已过期", "已达上限"):
                tag = "exp"
            vals = (row["name"], row["target"], row["plan"],
                    row["next_run"], row["last_run"], row["status"])
            mgr_tree.insert("", "end", values=vals, iid=row["id"], tags=(tag,))
            mgr_tree.tag_configure("en", foreground=C["fgb"])
            mgr_tree.tag_configure("dis", foreground=C["fgm"])
            mgr_tree.tag_configure("exp", foreground=C["wn"])
            shown += 1
        try:
            _mgr_count.configure(text="共 {} 条{}".format(
                shown, "" if shown == len(state.SCHED_TASKS)
                else " / 全部 {} 条".format(len(state.SCHED_TASKS))))
        except Exception:                       # noqa: BLE001
            pass

    def _mgr_context(event):
        sel = mgr_tree.selection()
        if not sel: return
        tid = sel[0]
        task = next((t for t in state.SCHED_TASKS if t["id"] == tid), None)
        menu = tkinter.Menu(dlg, tearoff=0, bg=C["bgc"], fg=C["fgt"],
            activebackground=C["acl"], activeforeground=C["fgt"])
        if task:
            en = task.get("enabled", True)
            menu.add_command(label="编辑…",
                command=lambda: _mgr_edit_task(tid))
            menu.add_command(label="禁用" if en else "启用",
                command=lambda: (sched_module.toggle_task(tid, not en), _mgr_refresh()))
            menu.add_command(label="立即执行",
                command=lambda: sched_module.run_task_now(tid, _main_run))
            menu.add_separator()
        menu.add_command(label="删除",
            command=lambda: (_del_task(tid)))
        try: menu.tk_popup(event.x_root, event.y_root)
        finally: menu.grab_release()

    def _del_task(tid):
        if messagebox.askyesno("确认", "确定删除此任务?"):
            sched_module.remove_task(tid); _mgr_refresh()
            show_toast(root, "任务已删除", "warning")

    mgr_tree.bind("<Button-3>", _mgr_context)
    mgr_tree.bind("<Delete>", lambda e: (
        _del_task(mgr_tree.selection()[0]) if mgr_tree.selection() else None))

    def _mgr_show_logs(parent):
        ldlg = tkinter.Toplevel(parent)
        ldlg.title("执行日志")
        place_dialog(ldlg, 700, 420, parent=parent)
        ldlg.transient(parent)
        ldlg.configure(bg=C["bgc"])
        _set_window_icon(ldlg)
        ldlg.columnconfigure(0, weight=1); ldlg.rowconfigure(1, weight=1)

        ltbar = tkinter.Frame(ldlg, bg=C["bgc"])
        ltbar.grid(row=0, column=0, sticky="ew", padx=sp("gap"), pady=(sp("gap"), sp("gap_tight")))
        _btn(ltbar, "刷新", lambda: _log_refresh(), C["bgc"], C["fgb"]).pack(side="left", padx=sp("hairline"))
        _btn(ltbar, "清除", lambda: (sched_module.clear_task_logs(), _log_refresh()),
            C["dg"], "white").pack(side="left", padx=sp("hairline"))

        lf = tkinter.Frame(ldlg, bg=C["bgc"], highlightbackground=C["bd"], highlightthickness=1)
        lf.grid(row=1, column=0, sticky="nsew", padx=sp("gap"), pady=(0, sp("gap")))
        lf.columnconfigure(0, weight=1); lf.rowconfigure(0, weight=1)

        log_tree = ttk.Treeview(lf,
            columns=("task","script","time","status","trigger"),
            show="headings", selectmode="browse")
        for c, t, w in [("task","任务",100),("script","脚本",120),("time","时间",140),
            ("status","状态",70),("trigger","触发",60)]:
            log_tree.heading(c, text=t); log_tree.column(c, width=w, minwidth=40)
        lsy = tkinter.Scrollbar(lf, orient="vertical", command=log_tree.yview,
            width=sp("sb_w"), relief="flat", bg=C["bd"], troughcolor=C["bgc"])
        log_tree.configure(yscrollcommand=lsy.set)
        log_tree.grid(row=0, column=0, sticky="nsew"); lsy.grid(row=0, column=1, sticky="ns")
        log_tree.tag_configure("ok", foreground=C["sc"])
        log_tree.tag_configure("fail", foreground=C["dg"])
        log_tree.tag_configure("info", foreground=C["ac"])
        # §4.5 降级: show="headings" 无 #0 列 → 选中行加粗
        bind_sel_bold(log_tree)

        def _log_refresh():
            for item in log_tree.get_children():
                log_tree.delete(item)
            for e in reversed(sched_module.get_task_logs(200)):
                s = e.get("status","")
                tag = "ok" if "成功" in s or "运行" in s else ("fail" if "失败" in s or "异常" in s else "info")
                log_tree.insert("", "end", values=(
                    e.get("task_name",""), e.get("script_name",""),
                    e.get("time",""), s, e.get("trigger","")), tags=(tag,))
        _log_refresh()

        ldlg.bind("<Escape>", lambda e: ldlg.destroy())

    # L4 #5: 搜索输入即时过滤
    try:
        mgr_search_var.trace_add("write", lambda *a: _mgr_refresh())
    except Exception:                           # noqa: BLE001
        pass
    # L4 #6 降级: 拖放需第三方依赖 → Ctrl+V 粘贴路径即新建任务
    bind_paste_new_task(mgr_tree, dlg, _mgr_refresh)
    bind_paste_new_task(dlg, dlg, _mgr_refresh)

    _mgr_refresh()
    dlg.bind("<Escape>", lambda e: dlg.destroy())


# ======================================================================
# 软件更新对话框 (检查 → 直链下载 → 校验 → 重启自更新)
# ======================================================================
def show_update_dialog(force_check=False):
    """一站式更新窗口。

    与旧实现的差别: 旧版只在状态栏挂一个 os.startfile 链接 —— 用户点击后跳到
    浏览器, 既无进度、无校验, 也无法确认下载到的到底是不是目标版本。本窗口
    把「检查 → 下载(带进度与体积) → 完整性校验 → 替换并重启」收在应用内完成。

    未打包运行 (源码模式) 时仍可下载, 但会提示手动替换 —— 自更新只对 EXE 生效。
    """
    import updater
    from version_info import GITHUB_REPO

    releases_page = "https://github.com/{}/releases".format(GITHUB_REPO)

    dlg = tkinter.Toplevel(root)
    dlg.title("软件更新 — ACRPA")
    dlg.configure(bg=C["bg"])
    dlg.transient(root)
    dlg.resizable(False, False)
    _set_window_icon(dlg)

    body = tkinter.Frame(dlg, bg=C["bg"])
    body.grid(row=0, column=0, sticky="nsew", padx=sp(20), pady=sp("sp_lg"))
    body.columnconfigure(0, weight=1)

    head = tkinter.Label(body, text="正在检查更新…", font=FONT_TITLE,
                         fg=C["fgt"], bg=C["bg"], anchor="w")
    head.grid(row=0, column=0, sticky="ew")

    sub = tkinter.Label(body, text="当前版本 v{}".format(updater.VERSION),
                        font=FONT_SMALL, fg=C["fgm"], bg=C["bg"], anchor="w")
    sub.grid(row=1, column=0, sticky="ew", pady=(sp("gap_tight"), 0))

    notes = tkinter.Text(body, height=7, width=52, font=FONT_SMALL, wrap="word",
                         bg=C["logbg"], fg=C["logfg"], relief="flat",
                         highlightbackground=C["bd"], highlightthickness=1)
    notes.grid(row=2, column=0, sticky="ew", pady=(sp("sp_sm"), 0))
    notes.configure(state="disabled")
    notes.grid_remove()

    prog = ttk.Progressbar(body, orient="horizontal", length=460,
                           mode="determinate", maximum=100)
    prog.grid(row=3, column=0, sticky="ew", pady=(sp("sp_sm"), 0))
    prog.grid_remove()

    detail = tkinter.Label(body, text="", font=FONT_SMALL, fg=C["fgm"], bg=C["bg"],
                           anchor="w", justify="left", wraplength=460)
    detail.grid(row=4, column=0, sticky="ew", pady=(sp("gap"), 0))

    bar = tkinter.Frame(body, bg=C["bg"])
    bar.grid(row=5, column=0, sticky="ew", pady=(sp("sp_md"), 0))

    ui = {"info": None, "downloading": False, "done": False, "started": 0}

    def _say(text, color=None):
        try:
            detail.config(text=text, fg=color or C["fgm"])
        except Exception:
            pass

    def _set_notes(text):
        try:
            if not text:
                notes.grid_remove()
                return
            notes.configure(state="normal")
            notes.delete("1.0", "end")
            notes.insert("1.0", text[:4000])
            notes.configure(state="disabled")
            notes.grid()
        except Exception:
            pass

    def _render(res):
        ui["info"] = res
        status = (res or {}).get("status")
        btn_download.grid_remove()
        btn_page.grid_remove()
        btn_retry.grid_remove()
        btn_restart.grid_remove()
        btn_folder.grid_remove()

        if status == "ok":
            head.config(text="发现新版本 v{}".format(res["latest"]), fg=C["sc"])
            size_txt = updater.format_size(res["size"]) if res.get("size") else "体积未知"
            sub.config(text="当前 v{}　→　最新 v{}　·　{}".format(
                updater.VERSION, res["latest"], size_txt))
            if res.get("sha256"):
                _say("下载将进行 sha256 校验: {}…".format(res["sha256"][:16]))
            else:
                _say("下载后将校验包体积与容器格式。")
            _set_notes(res.get("notes", ""))
            btn_download.grid()
            btn_page.grid()
        elif status == "no_update":
            head.config(text="已是最新版本 (v{})".format(updater.VERSION), fg=C["sc"])
            sub.config(text="上次检查来源: {}".format(res.get("source", "—") or "—"))
            _say("如需强制重新检查, 点击「重新检查」。")
            btn_retry.grid()
        else:
            head.config(text="无法获取更新信息", fg=C["wn"])
            sub.config(text="当前版本 v{}".format(updater.VERSION))
            _say("检查更新失败: {}\n可在「打开下载页」中手动获取最新版本。".format(
                (res or {}).get("error", "未知原因")), C["wn"])
            btn_retry.grid()
            btn_page.grid()

    # ── 按需创建按钮 (避免 Toplevel 尺寸抖动) ──
    def _mk(text, cmd, bg_c=None, fg_c=None, tip=None):
        b = _btn(bar, text, cmd, bg_c, fg_c, tip)
        return b

    btn_retry = _mk("重新检查", lambda: _start_check(force=True),
                    tip="忽略本次结果重新请求更新清单")
    btn_retry.grid(row=0, column=0, padx=(0, sp("gap"))); btn_retry.grid_remove()
    btn_download = _mk("↓ 下载更新", lambda: _start_download(), C["ac"], "white",
                       tip="下载到 updates/ 并校验完整性")
    btn_download.grid(row=0, column=1, padx=(0, sp("gap"))); btn_download.grid_remove()
    btn_page = _mk("打开下载页", lambda: _open(releases_page))
    btn_page.grid(row=0, column=2, padx=(0, sp("gap"))); btn_page.grid_remove()
    btn_restart = _mk("↻ 立即重启更新", lambda: _apply(), C["sc"], "white",
                      tip="退出程序 → 替换主程序与 VERSION → 自动重启")
    btn_restart.grid(row=0, column=3, padx=(0, sp("gap"))); btn_restart.grid_remove()
    btn_folder = _mk("打开所在文件夹", lambda: _open_folder())
    btn_folder.grid(row=0, column=4, padx=(0, sp("gap"))); btn_folder.grid_remove()
    _mk("关闭", dlg.destroy).grid(row=0, column=5)

    def _open(path):
        try:
            os.startfile(path)
        except Exception as e:
            messagebox.showerror("打开失败", str(e), parent=dlg)

    def _open_folder():
        path = ui.get("zip") or os.path.dirname(updater.get_app_dir())
        _open(os.path.dirname(path) if os.path.isfile(path) else path)

    # ── 检查 ──
    def _start_check():
        head.config(text="正在检查更新…", fg=C["fgt"])
        sub.config(text="当前版本 v{}".format(updater.VERSION))
        _set_notes("")
        prog.grid_remove()
        _say("正在请求更新清单 (GitHub Releases → 镜像回退)…")
        btn_retry.grid_remove(); btn_download.grid_remove(); btn_page.grid_remove()

        def _done(res):
            try:
                root.after(0, lambda: _render(res))
            except Exception:
                pass

        updater.check_async(on_done=_done)

    # ── 下载 ──
    def _on_progress(done, total):
        pct = (done * 100.0 / total) if total else 0
        txt = "已下载 {} / {}".format(updater.format_size(done),
                                    updater.format_size(total) if total else "未知")

        def _apply_ui():
            try:
                prog.configure(value=pct)
                _say(txt + ("　（正在校验…）" if total and done >= total else ""))
            except Exception:
                pass
        try:
            root.after(0, _apply_ui)
        except Exception:
            pass

    def _start_download():
        if ui["downloading"] or not ui["info"]:
            return
        ui["downloading"] = True
        prog.configure(value=0)
        prog.grid()
        btn_download.config(text="↓ 下载中…", state="disabled")
        _say("正在连接下载源…")

        def _worker():
            ok, res = updater.download_package(ui["info"], progress=_on_progress)
            if ok:
                ui["zip"] = res
                try:
                    root.after(0, lambda: _on_ok(res))
                except Exception:
                    pass
            else:
                try:
                    root.after(0, lambda: _on_fail(res))
                except Exception:
                    pass
            ui["downloading"] = False

        threading.Thread(target=_worker, daemon=True,
                         name="acrpa-update-download").start()

    def _on_fail(reason):
        prog.grid_remove()
        btn_download.config(text="↓ 重试下载", state="normal")
        _say("下载失败: {}".format(reason), C["dg"])

    def _on_ok(path):
        ui["done"] = True
        prog.configure(value=100)
        btn_download.config(text="✔ 已下载", state="disabled")
        _say("安装包已下载并通过完整性校验。\n{}".format(path), C["sc"])

        ok, why = updater.can_self_update()
        if ok:
            btn_restart.grid()
            btn_folder.grid()
            _say("安装包已下载并通过完整性校验。\n{}\n\n"
                 "点击「立即重启更新」将: 退出程序 → 替换主程序与 VERSION → 自动重启。\n"
                 "配置、脚本、模板、日志均不受影响。".format(path), C["sc"])
        else:
            btn_folder.grid()
            _say("安装包已下载并通过完整性校验。\n{}\n\n"
                 "当前无法自动更新: {}\n请手动替换主程序（zip 包解压后替换）。".format(path, why), C["wn"])

    # ── 应用并退出 ──
    def _apply():
        path = ui.get("zip")
        if not path:
            return
        if not messagebox.askyesno(
                "确认更新",
                "程序将立即退出并替换主程序, 随后自动重启。\n\n"
                "更新包: {}\n\n继续吗?".format(os.path.basename(path)), parent=dlg):
            return
        info = ui.get("info") or {}
        ok, why = updater.apply_update(
            path,
            expected_version=info.get("latest") or "",
            download_url=info.get("download_url") or "",
            sha256=info.get("sha256") or "")
        if not ok:
            messagebox.showerror("无法启动更新", why, parent=dlg)
            return
        log1("已启动自更新助手, 即将退出以完成主程序替换")
        try:
            dlg.destroy()
        except Exception:
            pass
        if _exit_for_update:
            _exit_for_update()
        else:
            try:
                root.destroy()
            except Exception:
                pass

    dlg.bind("<Escape>", lambda e: dlg.destroy())

    cached = None if force_check else updater.get_status()
    if cached is None:
        _start_check()
    else:
        _render(cached)
    return dlg
