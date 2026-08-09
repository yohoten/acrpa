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
from utils import _btn, _darken, log1, show_toast, attach_tooltip

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

# PIL 惰性加载 (与 ACRPA.py 相同模式)
_PIL_loaded = False
Image = None
ImageTk = None


def init_ctx(root_win=None, colors=None, fonts=None, app_root="", res_dir="",
             editor_sync_to_tree=None, push_undo=None, main_run=None, tlog=None):
    """注入 ACRPA.py 提供的依赖 (模块启动时调用一次)。"""
    global root, C, FONT_TITLE, FONT_BODY, FONT_SMALL, FONT_BUTTON
    global APP_ROOT, RES_DIR, _editor_sync_to_tree, _push_undo, _main_run, _tlog
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
    """Display command reference and contact info in a modal dialog."""
    def _write_cmd_table(txt_widget, cmds):
        for name, desc, params, _ in cmds:
            txt_widget.insert("end", "  {}  ".format(name), "cmd")
            txt_widget.insert("end", "{}\n".format(desc), "desc")
            txt_widget.insert("end", "        参数: {}\n".format(params), "param")

    try:
        dlg = tkinter.Toplevel(root); dlg.title("帮助 — A/C RPA"); dlg.geometry("500x600+450+150")
        dlg.transient(root); dlg.grab_set(); dlg.configure(bg=C["bgc"])
        _set_window_icon(dlg)
        dlg.columnconfigure(0, weight=1); dlg.rowconfigure(1, weight=1)
        dlg.resizable(width=True, height=True); dlg.minsize(400, 500)

        contact_card = tkinter.Frame(dlg, bg=C["bgc"], highlightbackground=C["bd"], highlightthickness=1)
        contact_card.grid(row=0, column=0, sticky="ew", padx=10, pady=(10,4))
        contact_card.columnconfigure(1, weight=1)

        tkinter.Label(contact_card, text="联系与支持", font=FONT_TITLE, bg=C["bgc"],
            fg=C["fgt"]).grid(row=0, column=0, columnspan=2, sticky="w", padx=12, pady=(8,4))

        wp = None
        search_dirs = [RES_DIR, APP_ROOT]
        if getattr(sys, "frozen", False):
            search_dirs.insert(0, os.path.join(sys._MEIPASS, "res"))
        for d in search_dirs:
            p = os.path.join(d, "wechat_qrcode.png")
            if os.path.exists(p): wp = p; break

        qr_frame = tkinter.Frame(contact_card, bg=C["bgc"])
        qr_frame.grid(row=1, column=0, sticky="nw", padx=(12,8), pady=(0,8), rowspan=3)

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
        info_frame.grid(row=1, column=1, sticky="w", padx=(0,12), pady=(4,0))

        tkinter.Label(info_frame, text="作者：yohoten | 微信扫描二维码添加好友", font=FONT_BODY, bg=C["bgc"],
            fg=C["fgb"]).pack(anchor="w")
        tkinter.Label(info_frame, text="邮箱: yoho12138@aliyun.com", font=FONT_SMALL, bg=C["bgc"],
            fg=C["ac"], cursor="hand2").pack(anchor="w", pady=(4,0))
        from version_info import VERSION as _APP_VERSION
        tkinter.Label(info_frame, text="版本: v{}  |  Python tkinter + pyautogui".format(_APP_VERSION),
            font=FONT_SMALL, bg=C["bgc"], fg=C["fgm"]).pack(anchor="w", pady=(8,0))

        btn_row = tkinter.Frame(contact_card, bg=C["bgc"])
        btn_row.grid(row=2, column=1, sticky="w", padx=(0,12), pady=(8,8))

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
        _doc_btn = tkinter.Button(btn_row, text="打开使用说明", font=FONT_SMALL, bg=C["ac"], fg="white",
            relief="raised", bd=3, cursor="hand2", padx=12, pady=3,
            activebackground=C["ach"], activeforeground="white",
            command=_open_doc_online)
        _doc_btn.pack(side="left", padx=(0,4))
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
            relief="raised", bd=3, cursor="hand2", padx=12, pady=3,
            activebackground=C["acl"], activeforeground=C["fgb"],
            command=_open_readme)
        _readme_btn.pack(side="left", padx=(0,4))
        attach_tooltip(_readme_btn, "打开在线 README（离线时回退本地文件）")
        tkinter.Button(btn_row, text="关闭", font=FONT_SMALL, bg=C["bgc"], fg=C["fgb"],
            relief="raised", bd=3, cursor="hand2", padx=12, pady=3,
            activebackground=C["acl"], activeforeground=C["fgb"],
            command=dlg.destroy).pack(side="left")

        cmd_card = tkinter.Frame(dlg, bg=C["bgc"], highlightbackground=C["bd"], highlightthickness=1)
        cmd_card.grid(row=1, column=0, sticky="nsew", padx=10, pady=(4,10))
        cmd_card.columnconfigure(0, weight=1); cmd_card.rowconfigure(1, weight=1)

        tkinter.Label(cmd_card, text="命令速查", font=FONT_TITLE, bg=C["bgc"],
            fg=C["fgt"]).grid(row=0, column=0, sticky="w", padx=12, pady=(8,4))

        txt_frame = tkinter.Frame(cmd_card, bg=C["bgc"])
        txt_frame.grid(row=1, column=0, sticky="nsew", padx=6, pady=(0,6))
        txt_frame.columnconfigure(0, weight=1); txt_frame.rowconfigure(0, weight=1)

        help_txt = tkinter.Text(txt_frame, font=("Microsoft YaHei UI", 9), wrap="word",
            bg=C["logbg"], fg=C["logfg"], relief="flat", bd=0, padx=10, pady=8,
            selectbackground=C["acl"], selectforeground=C["fgt"], cursor="arrow")
        help_txt.grid(row=0, column=0, sticky="nsew")

        help_scroll = tkinter.Scrollbar(txt_frame, width=6, relief="flat", elementborderwidth=0,
            bg=C["bd"], activebackground=C["fgm"], troughcolor=C["logbg"])
        help_scroll.grid(row=0, column=1, sticky="ns")
        help_txt.config(yscrollcommand=help_scroll.set); help_scroll.config(command=help_txt.yview)

        help_txt.tag_configure("h", font=("Microsoft YaHei UI", 9, "bold"), foreground=C["fgt"])
        help_txt.tag_configure("cmd", font=("Microsoft YaHei UI", 9, "bold"), foreground=C["ac"])
        help_txt.tag_configure("desc", foreground=C["fgb"])
        help_txt.tag_configure("param", foreground=C["fgm"], font=("Microsoft YaHei UI", 8))
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
    dlg.title("版本历史 — {}".format(os.path.basename(state.filename)))
    dlg.geometry("680x480+350+100")
    dlg.minsize(500, 350)
    dlg.transient(root)
    dlg.configure(bg=C["bgc"])
    _set_window_icon(dlg)
    dlg.columnconfigure(0, weight=1)
    dlg.rowconfigure(0, weight=0)
    dlg.rowconfigure(1, weight=1)
    dlg.rowconfigure(2, weight=0)

    # 工具栏
    vh_toolbar = tkinter.Frame(dlg, bg=C["bgc"])
    vh_toolbar.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 4))
    tkinter.Label(vh_toolbar, text="脚本版本历史", font=FONT_TITLE, bg=C["bgc"],
        fg=C["fgt"]).pack(side="left")
    tkinter.Label(vh_toolbar, text="(自动保存每次 Ctrl+S 的快照)", font=FONT_SMALL,
        bg=C["bgc"], fg=C["fgm"]).pack(side="left", padx=(8, 0))
    vh_status = tkinter.StringVar(value="")
    tkinter.Label(vh_toolbar, textvariable=vh_status, font=FONT_SMALL,
        bg=C["bgc"], fg=C["ac"]).pack(side="right")

    # 版本列表
    vh_list_frame = tkinter.Frame(dlg, bg=C["bgc"], highlightbackground=C["bd"], highlightthickness=1)
    vh_list_frame.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 4))
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
    vh_tree.column("comment", width=150)

    vh_sy = tkinter.Scrollbar(vh_list_frame, orient="vertical", command=vh_tree.yview,
        width=8, relief="flat", bg=C["bd"], troughcolor=C["bgc"])
    vh_tree.configure(yscrollcommand=vh_sy.set)
    vh_tree.grid(row=0, column=0, sticky="nsew")
    vh_sy.grid(row=0, column=1, sticky="ns")

    # 底部操作按钮
    vh_btn_frame = tkinter.Frame(dlg, bg=C["bgc"])
    vh_btn_frame.grid(row=2, column=0, sticky="ew", padx=8, pady=(0, 8))

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
        dd.geometry("600x400+380+120")
        dd.transient(dlg)
        dd.configure(bg=C["bgc"])
        _set_window_icon(dd)
        dd.columnconfigure(0, weight=1)
        dd.rowconfigure(0, weight=1)

        dtxt = tkinter.Text(dd, font=("Consolas", 9), bg=C["logbg"], fg=C["logfg"],
            wrap="none", relief="flat", bd=0, padx=10, pady=8)
        dtxt.grid(row=0, column=0, sticky="nsew")
        ds = tkinter.Scrollbar(dd, orient="vertical", command=dtxt.yview, width=6)
        ds.grid(row=0, column=1, sticky="ns")
        dtxt.config(yscrollcommand=ds.set)
        dtxt.insert("1.0", diff_text)
        dtxt.config(state="disabled")

        tkinter.Button(dd, text="关闭", font=FONT_BUTTON, bg=C["bgc"], fg=C["fgb"],
            command=dd.destroy).grid(row=1, column=0, pady=8)
        dd.bind("<Escape>", lambda e: dd.destroy())

    _btn(vh_btn_frame, "回退到此版本", _vh_restore, C["wn"], "white").pack(side="left", padx=2)
    _btn(vh_btn_frame, "对比差异", _vh_diff, C["ac"], "white").pack(side="left", padx=2)
    _btn(vh_btn_frame, "刷新", _vh_refresh, C["bgc"], C["fgb"]).pack(side="left", padx=2)
    tkinter.Label(vh_btn_frame, text="提示: 每次保存自动创建版本快照",
        font=FONT_SMALL, bg=C["bgc"], fg=C["fgm"]).pack(side="right")

    _vh_refresh()
    dlg.bind("<Escape>", lambda e: dlg.destroy())


# ======================================================================
# AI 脚本生成器
# ======================================================================
def open_ai_panel():
    """Open AI script generation dialog with new layout."""
    dlg = tkinter.Toplevel(root)
    dlg.title("AI 脚本生成器")
    dlg.geometry("500x600+400+80")
    dlg.minsize(400, 500)
    dlg.transient(root)
    dlg.grab_set()
    dlg.configure(bg=C["bgc"])
    _set_window_icon(dlg)

    # Configure grid for the main container
    dlg.columnconfigure(0, weight=1)
    dlg.rowconfigure(4, weight=1)  # Preview area expands

    # === Row 0: Title Label ===
    title_label = tkinter.Label(dlg, text="描述你要实现的操作",
        font=("Microsoft YaHei UI", 10, "bold"),
        bg=C["bgc"], fg=C["fgt"])
    title_label.grid(row=0, column=0, sticky="w", padx=16, pady=(12, 4))

    # === Row 1: Input Text Area ===
    prompt_txt = tkinter.Text(dlg, height=4, font=("Microsoft YaHei UI", 10),
        bg=C["logbg"], fg=C["logfg"], wrap="word", relief="solid", bd=1,
        padx=8, pady=6)
    prompt_txt.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 8))
    prompt_txt.insert("1.0", "快速示例：打开记事本程序，输入'Hello World'，然后保存文件到桌面")
    prompt_txt.tag_add("placeholder", "1.0", "end")
    prompt_txt.tag_configure("placeholder", foreground="#9CA3AF")

    def _on_prompt_focus_in(event):
        if prompt_txt.tag_ranges("placeholder"):
            prompt_txt.delete("1.0", "end")
            prompt_txt.tag_remove("placeholder", "1.0", "end")

    def _on_prompt_focus_out(event):
        if not prompt_txt.get("1.0", "end-1c").strip():
            prompt_txt.insert("1.0", "快速示例：打开记事本程序，输入'Hello World'，然后保存文件到桌面")
            prompt_txt.tag_add("placeholder", "1.0", "end")

    prompt_txt.bind("<FocusIn>", _on_prompt_focus_in)
    prompt_txt.bind("<FocusOut>", _on_prompt_focus_out)

    # === Quick Example Buttons Frame ===
    quick_frame = tkinter.Frame(dlg, bg=C["bgc"])
    quick_frame.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 6))
    quick_frame.columnconfigure(0, weight=1)

    def _set_quick_example(example_text):
        prompt_txt.delete("1.0", "end")
        prompt_txt.insert("1.0", example_text)
        prompt_txt.tag_remove("placeholder", "1.0", "end")

    from templates import AI_QUICK_PROMPTS
    example_names = list(AI_QUICK_PROMPTS.keys())[:4]  # Show first 4 examples

    btn_frame = tkinter.Frame(quick_frame, bg=C["bgc"])
    btn_frame.grid(row=0, column=0, sticky="w")

    for i, name in enumerate(example_names):
        btn = tkinter.Button(btn_frame, text=name, font=("Microsoft YaHei UI", 7),
            bg=C["bgc"], fg=C["ac"], relief="raised", bd=2, cursor="hand2",
            activebackground=C["acl"], activeforeground=C["ach"],
            command=lambda n=name: _set_quick_example(AI_QUICK_PROMPTS[n]))
        btn.grid(row=0, column=i, padx=2, sticky="w")

    # === Row 3: Preview Label ===
    preview_label = tkinter.Label(dlg, text="生成的脚本预览",
        font=FONT_BODY, fg=C["fgm"], bg=C["bgc"])
    preview_label.grid(row=3, column=0, sticky="w", padx=16, pady=(8, 2))

    # === Row 4: Preview Text Area (expands) ===
    result_txt = tkinter.Text(dlg, height=12, font=("Consolas", 10),
        bg=C["logbg"], fg=C["logfg"], wrap="word", relief="solid", bd=1,
        padx=8, pady=6)
    result_txt.grid(row=4, column=0, sticky="nsew", padx=16, pady=(0, 8))

    # === Row 5: Progress Bar ===
    progress_frame = tkinter.Frame(dlg, bg=C["bgc"])
    progress_frame.grid(row=5, column=0, sticky="ew", padx=16, pady=(0, 4))
    progress_frame.columnconfigure(0, weight=1)

    progress_var = tkinter.DoubleVar(value=0)
    progress_bar = ttk.Progressbar(progress_frame, variable=progress_var,
        maximum=100, mode='indeterminate', length=400)
    progress_bar.grid(row=0, column=0, sticky="ew")

    # === Row 6: Status Text ===
    status_var = tkinter.StringVar(value="输入操作描述后点击生成（支持Ctrl+Enter快捷生成）")
    status_label = tkinter.Label(dlg, textvariable=status_var,
        font=FONT_SMALL, bg=C["bgc"], fg=C["fgm"])
    status_label.grid(row=6, column=0, sticky="w", padx=16, pady=(0, 8))

    # === Row 7: Action Buttons ===
    button_frame = tkinter.Frame(dlg, bg=C["bgc"])
    button_frame.grid(row=7, column=0, sticky="ew", padx=16, pady=(0, 14))
    button_frame.columnconfigure(1, weight=1)

    # Generate button
    gen_btn = tkinter.Button(button_frame, text="▶ 生成脚本",
        font=FONT_BUTTON, bg=C["ac"], fg="white",
        relief="raised", bd=3, padx=16, pady=4, cursor="hand2",
        activebackground=C["ach"], activeforeground="white")
    gen_btn.grid(row=0, column=0, padx=(0, 6))
    attach_tooltip(gen_btn, "根据描述生成脚本 (Ctrl+Enter)")

    # Spacer to push buttons to right
    spacer = tkinter.Frame(button_frame, bg=C["bgc"])
    spacer.grid(row=0, column=1, sticky="ew")

    # Insert button
    insert_btn = tkinter.Button(button_frame, text="↓ 插入表格",
        font=FONT_BUTTON, bg=C["sc"], fg="white",
        relief="raised", bd=3, padx=16, pady=4, cursor="hand2",
        activebackground=_darken(C["sc"]), activeforeground="white")
    insert_btn.grid(row=0, column=2, padx=3)
    attach_tooltip(insert_btn, "将生成的脚本插入编辑器表格")

    # Cancel button
    cancel_btn = tkinter.Button(button_frame, text="取消",
        font=FONT_BUTTON, bg=C["bgc"], fg=C["fgb"],
        relief="raised", bd=3, padx=16, pady=4, cursor="hand2",
        activebackground=_darken(C["bgc"]), activeforeground=C["fgb"],
        highlightbackground=C["bd"], highlightthickness=1,
        command=dlg.destroy)
    cancel_btn.grid(row=0, column=3, padx=(6, 0))

    # Define generate function
    def _do_generate():
        """Generate RPA script using AI."""
        user_input = prompt_txt.get("1.0", "end-1c").strip()

        # Check if placeholder is still present
        if "快速示例" in user_input or not user_input:
            show_toast(root, "请先输入操作描述", "warning")
            return

        # Start progress animation
        progress_bar.start(10)
        status_var.set("正在生成脚本...")
        gen_btn.config(state="disabled")
        result_txt.delete("1.0", "end")

        def _generate_thread():
            try:
                from ai_client import create_client, APIError, APIAuthError, APIRateLimitError, APITimeoutError
                from templates import build_ai_prompt, normalize_ai_output

                # Build prompt
                prompt = build_ai_prompt(user_input)

                # Create AI client
                client = create_client(state.API_KEY, state.API_MODEL)

                # Call AI API
                response = client.chat_completions(
                    model=state.API_MODEL,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.3,
                    max_tokens=2000,
                    timeout=60
                )

                # Get generated content
                raw_output = response.choices[0].message.content.strip()

                # ✨ 关键修复：使用normalize_ai_output进行规范化处理
                generated_script = normalize_ai_output(raw_output)

                if not generated_script:
                    raise ValueError("AI返回的内容为空或格式无效")

                # Display in result text
                result_txt.delete("1.0", "end")
                result_txt.insert("1.0", generated_script)

                # Update status
                def _safe_progress_stop():
                    try: progress_bar.stop()
                    except Exception: pass
                def _safe_gen_ok():
                    try: progress_bar.stop()
                    except Exception: pass
                    status_var.set("✓ 生成成功 ({} 字符)".format(len(generated_script)))
                    gen_btn.config(state="normal")
                dlg.after(0, _safe_gen_ok)

            except APIAuthError as e:
                def _safe_auth(err=e):
                    try: progress_bar.stop()
                    except Exception: pass
                    status_var.set("✗ 认证失败: {}".format(err))
                    gen_btn.config(state="normal")
                    messagebox.showwarning("认证失败", str(err))
                dlg.after(0, _safe_auth)
            except APIRateLimitError as e:
                def _safe_rate(err=e):
                    try: progress_bar.stop()
                    except Exception: pass
                    status_var.set("✗ 频率超限: {}".format(err))
                    gen_btn.config(state="normal")
                    messagebox.showwarning("请求频率超限", str(err))
                dlg.after(0, _safe_rate)
            except APITimeoutError as e:
                def _safe_timeout(err=e):
                    try: progress_bar.stop()
                    except Exception: pass
                    status_var.set("✗ 请求超时: {}".format(err))
                    gen_btn.config(state="normal")
                    messagebox.showwarning("请求超时", str(err))
                dlg.after(0, _safe_timeout)
            except APIError as e:
                def _safe_api(err=e):
                    try: progress_bar.stop()
                    except Exception: pass
                    status_var.set("✗ API 错误: {}".format(err))
                    gen_btn.config(state="normal")
                    messagebox.showerror("API 错误", str(err))
                dlg.after(0, _safe_api)
            except Exception as e:
                def _safe_exc(err=e):
                    try: progress_bar.stop()
                    except Exception: pass
                    status_var.set("✗ 生成失败: {}".format(err))
                    gen_btn.config(state="normal")
                    messagebox.showerror("生成失败", "发生未知错误:\n{}".format(err))
                dlg.after(0, _safe_exc)
        # Start generation in background thread
        thread = threading.Thread(target=_generate_thread, daemon=True)
        thread.start()

    def _insert_to_editor():
        """Insert generated script into editor."""
        generated_content = result_txt.get("1.0", "end-1c").strip()

        if not generated_content:
            show_toast(root, "没有可插入的内容", "warning")
            return

        try:
            # Parse CSV format
            lines = generated_content.split("\n")

            # Skip header rows (支持多种标题格式)
            data_lines = []
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                # 跳过标题行和说明行
                if line.startswith("操作") or line.startswith("命令类型") or line.startswith("（标题行）"):
                    continue
                data_lines.append(line)

            if not data_lines:
                show_toast(root, "未找到有效的脚本数据", "warning")
                return

            # Parse each line
            from scriptdata import ScriptData
            _push_undo()
            imported_count = 0
            skipped_count = 0

            for line in data_lines:
                parts = line.split(",")

                # 检查是否有命令类型
                if len(parts) < 1:
                    skipped_count += 1
                    continue

                cmd_type = parts[0].strip()

                # 确保至少有10个字段（命令类型 + 9个参数）
                while len(parts) < 10:
                    parts.append("None")

                # 处理None值：将字符串"None"转换为实际的None
                args = []
                for p in parts[1:10]:  # 只处理参数部分（跳过命令类型）
                    p_stripped = p.strip()
                    if p_stripped.lower() == 'none' or p_stripped == '':
                        args.append(None)
                    else:
                        args.append(p_stripped)

                # Validate command type
                if cmd_type in ScriptData.COMMANDS:
                    state._editor_rows.append(ScriptData(cmd_type, args))
                    imported_count += 1
                else:
                    log1("⚠️  跳过未知命令: {}".format(cmd_type), "warning")
                    skipped_count += 1
            # Show success message
            msg = "✓ 已导入 {} 条命令".format(imported_count)
            if skipped_count > 0:
                msg += " (跳过 {} 条)".format(skipped_count)

            log1(msg)
            show_toast(root, msg, "success")

            # Sync to tree view
            _editor_sync_to_tree()

            # Close dialog
            dlg.destroy()

        except Exception as e:
            messagebox.showerror("导入失败", "解析脚本时出错:\n{}".format(e))
            show_toast(root, "导入失败: {}".format(e), "error")

    # Bind button commands
    gen_btn.config(command=_do_generate)
    insert_btn.config(command=_insert_to_editor)

    # Bind Ctrl+Enter to generate
    def _on_ctrl_enter(event):
        if event.state & 0x4:  # Ctrl key
            _do_generate()
            return "break"

    prompt_txt.bind("<Control-Return>", _on_ctrl_enter)
    result_txt.bind("<Control-Return>", _on_ctrl_enter)


# ======================================================================
# AI 智能调试对话框
# ======================================================================
def open_ai_debug_dialog():
    """Open AI natural language debugging dialog."""
    if not state.API_KEY:
        messagebox.showwarning("需要 API Key", "AI 调试功能需要在设置中配置 API Key")
        return

    dlg = tkinter.Toplevel(root)
    dlg.title("AI 智能调试 — ACRPA")
    dlg.geometry("600x550+400+80")
    dlg.minsize(480, 420)
    dlg.transient(root)
    dlg.grab_set()
    dlg.configure(bg=C["bgc"])
    _set_window_icon(dlg)
    dlg.columnconfigure(0, weight=1)
    dlg.rowconfigure(3, weight=1)

    # Title
    title_frame = tkinter.Frame(dlg, bg=C["bgc"])
    title_frame.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 4))
    tkinter.Label(title_frame, text="AI 智能调试", font=FONT_TITLE, bg=C["bgc"],
        fg=C["fgt"]).pack(side="left")
    tkinter.Label(title_frame, text="用自然语言提问，AI 分析执行记录回答",
        font=FONT_SMALL, bg=C["bgc"], fg=C["fgm"]).pack(side="left", padx=(8, 0))

    # Quick questions
    quick_frame = tkinter.Frame(dlg, bg=C["bgc"])
    quick_frame.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 4))
    quick_questions = [
        "为什么最后一条命令失败了?",
        "哪行命令最耗时?",
        "脚本为什么停止了?",
        "变量值是否符合预期?",
        "有哪些优化建议?",
    ]
    for i, q in enumerate(quick_questions):
        btn = tkinter.Button(quick_frame, text=q, font=("Microsoft YaHei UI", 7),
            bg=C["acl"], fg=C["ac"], relief="raised", bd=1, cursor="hand2",
            command=lambda qq=q: question_var.set(qq))
        btn.pack(side="left", padx=1, pady=1)

    # Question input
    input_frame = tkinter.Frame(dlg, bg=C["bgc"])
    input_frame.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 6))
    input_frame.columnconfigure(0, weight=1)

    question_var = tkinter.StringVar(value="请分析最近的执行情况，有什么问题?")
    question_entry = tkinter.Entry(input_frame, textvariable=question_var,
        font=FONT_BODY, relief="solid", bd=1, bg=C["ebg"], fg=C["fgb"])
    question_entry.grid(row=0, column=0, sticky="ew", padx=(0, 4))
    question_entry.select_range(0, "end")

    ask_btn = tkinter.Button(input_frame, text="▶ 提问",
        font=FONT_BUTTON, bg=C["ac"], fg="white",
        relief="raised", bd=3, padx=12, pady=2, cursor="hand2")
    ask_btn.grid(row=0, column=1)
    attach_tooltip(ask_btn, "提交问题给 AI 分析执行日志")

    # Answer area
    answer_frame = tkinter.Frame(dlg, bg=C["bgc"],
        highlightbackground=C["bd"], highlightthickness=1)
    answer_frame.grid(row=3, column=0, sticky="nsew", padx=12, pady=(0, 8))
    answer_frame.columnconfigure(0, weight=1)
    answer_frame.rowconfigure(0, weight=1)

    answer_txt = tkinter.Text(answer_frame, font=("Microsoft YaHei UI", 9),
        bg=C["logbg"], fg=C["logfg"], wrap="word", relief="flat", bd=0,
        padx=10, pady=8, state="disabled",
        selectbackground=C["acl"], selectforeground=C["fgt"])
    answer_txt.grid(row=0, column=0, sticky="nsew")
    answer_scroll = tkinter.Scrollbar(answer_frame, width=6, relief="flat",
        bg=C["bd"], troughcolor=C["logbg"])
    answer_scroll.grid(row=0, column=1, sticky="ns")
    answer_txt.config(yscrollcommand=answer_scroll.set)
    answer_scroll.config(command=answer_txt.yview)

    # Status
    status_var = tkinter.StringVar(value="输入问题后点击提问（需要配置 API Key）")
    status_label = tkinter.Label(dlg, textvariable=status_var, font=FONT_SMALL,
        bg=C["bgc"], fg=C["fgm"])
    status_label.grid(row=4, column=0, sticky="w", padx=12, pady=(0, 8))

    # Progress bar
    progress = ttk.Progressbar(dlg, mode="indeterminate", length=300)
    progress.grid(row=5, column=0, sticky="ew", padx=12, pady=(0, 8))

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
        answer_txt.insert("1.0", "⏳ 正在分析执行数据和日志，请稍候...\n")
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
                        status_var.set("✓ 分析完成")
                    else:
                        answer_txt.insert("1.0", "AI 分析未返回结果，请检查 API Key 和网络连接")
                        status_var.set("✗ 分析失败")
                    answer_txt.config(state="disabled")
                dlg.after(0, _on_result)
            except Exception as e:
                def _on_error(err=str(e)):
                    progress.stop()
                    ask_btn.config(state="normal")
                    status_var.set("✗ 出错: {}".format(err))
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
    btn_frame.grid(row=6, column=0, sticky="ew", padx=12, pady=(0, 12))
    _copy_btn = tkinter.Button(btn_frame, text="复制回答", font=FONT_BUTTON,
        bg=C["bgc"], fg=C["fgb"], relief="raised", bd=3, padx=12, pady=3,
        command=_copy_answer)
    _copy_btn.pack(side="left")
    attach_tooltip(_copy_btn, "复制 AI 回答到剪贴板")
    tkinter.Button(btn_frame, text="关闭", font=FONT_BUTTON,
        bg=C["dg"], fg="white", relief="raised", bd=3, padx=16, pady=3,
        command=dlg.destroy).pack(side="right")

    dlg.bind("<Escape>", lambda e: dlg.destroy())


# ======================================================================
# 计划任务管理窗口
# ======================================================================
def open_sched_manager():
    """打开计划任务管理窗口（多任务表格 + 执行日志）"""
    import scheduler as sched_module
    dlg = tkinter.Toplevel(root)
    dlg.title("计划任务管理 — ACRPA")
    dlg.geometry("780x520+350+100")
    dlg.minsize(600, 400)
    dlg.transient(root)
    dlg.configure(bg=C["bgc"])
    _set_window_icon(dlg)
    dlg.columnconfigure(0, weight=1)
    dlg.rowconfigure(0, weight=0)
    dlg.rowconfigure(1, weight=1)

    # 工具栏
    mgr_toolbar = tkinter.Frame(dlg, bg=C["bgc"])
    mgr_toolbar.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 2))

    def _mgr_new_task():
        ndlg = tkinter.Toplevel(dlg)
        ndlg.title("新建计划任务")
        ndlg.geometry("440x340+400+150")
        ndlg.transient(dlg); ndlg.grab_set()
        ndlg.configure(bg=C["bgc"])
        _set_window_icon(ndlg)

        tkinter.Label(ndlg, text="新建计划任务", font=FONT_TITLE, bg=C["bgc"],
            fg=C["fgt"]).pack(pady=(10, 6))
        f1 = tkinter.Frame(ndlg, bg=C["bgc"])
        f1.pack(fill="x", padx=14, pady=2)
        tkinter.Label(f1, text="任务名称:", font=FONT_BODY, bg=C["bgc"], fg=C["fgb"],
            width=9, anchor="e").pack(side="left", padx=(0, 6))
        nv = tkinter.StringVar(value="新任务")
        tkinter.Entry(f1, textvariable=nv, font=FONT_BODY, width=28,
            bg=C["ebg"], fg=C["fgb"], relief="solid", bd=1).pack(side="left")

        f2 = tkinter.Frame(ndlg, bg=C["bgc"])
        f2.pack(fill="x", padx=14, pady=2)
        tkinter.Label(f2, text="脚本文件:", font=FONT_BODY, bg=C["bgc"], fg=C["fgb"],
            width=9, anchor="e").pack(side="left", padx=(0, 6))
        sv = tkinter.StringVar()
        tkinter.Entry(f2, textvariable=sv, font=FONT_BODY, width=22,
            bg=C["ebg"], fg=C["fgb"], relief="solid", bd=1).pack(side="left")
        _browse_btn = tkinter.Button(f2, text="浏览", font=FONT_SMALL, bg=C["ac"], fg="white",
            command=lambda: sv.set(filedialog.askopenfilename(
                title="选择脚本", filetypes=[('Excel', '*.xls *.xlsx')],
                initialdir=APP_ROOT) or sv.get()))
        _browse_btn.pack(side="left", padx=4)
        attach_tooltip(_browse_btn, "选择脚本文件")

        f3 = tkinter.Frame(ndlg, bg=C["bgc"])
        f3.pack(fill="x", padx=14, pady=2)
        tkinter.Label(f3, text="执行周期:", font=FONT_BODY, bg=C["bgc"], fg=C["fgb"],
            width=9, anchor="e").pack(side="left", padx=(0, 6))
        ptv = tkinter.StringVar(value="daily")
        ttk.Combobox(f3, textvariable=ptv, width=12,
            values=("daily", "interval", "weekly", "once"), state="readonly").pack(side="left")

        f4 = tkinter.Frame(ndlg, bg=C["bgc"])
        f4.pack(fill="x", padx=14, pady=2)
        tkinter.Label(f4, text="周期值:", font=FONT_BODY, bg=C["bgc"], fg=C["fgb"],
            width=9, anchor="e").pack(side="left", padx=(0, 6))
        pvv = tkinter.StringVar(value="09:00")
        tkinter.Entry(f4, textvariable=pvv, font=FONT_BODY, width=15,
            bg=C["ebg"], fg=C["fgb"], relief="solid", bd=1).pack(side="left")
        ph = tkinter.Label(f4, text="(HH:MM / 秒 / 日期)", font=FONT_SMALL,
            bg=C["bgc"], fg=C["fgm"])
        ph.pack(side="left", padx=4)
        def _on_pt(*a):
            h = {"daily":"(HH:MM)","interval":"(秒数)","weekly":"(0,1|HH:MM)","once":"(YYYY-MM-DD HH:MM)"}
            ph.config(text=h.get(ptv.get(), ""))
        ptv.trace_add("write", _on_pt)

        ev = tkinter.BooleanVar(value=True)
        ttk.Checkbutton(ndlg, text="创建后立即启用", variable=ev).pack(pady=6)

        bf = tkinter.Frame(ndlg, bg=C["bgc"]); bf.pack(pady=8)
        def _confirm():
            n = nv.get().strip(); s = sv.get().strip()
            if not n: messagebox.showwarning("提示", "请输入任务名称"); return
            if not s: messagebox.showwarning("提示", "请选择脚本"); return
            sched_module.add_task(n, s, ptv.get(), pvv.get(), ev.get())
            _mgr_refresh()
            ndlg.destroy()
            show_toast(root, "任务已添加: {}".format(n), "success")
        tkinter.Button(bf, text="确定", command=_confirm, font=FONT_BUTTON,
            bg=C["ac"], fg="white", padx=14, pady=3).pack(side="left", padx=4)
        tkinter.Button(bf, text="取消", command=ndlg.destroy, font=FONT_BUTTON,
            bg=C["bgc"], fg=C["fgb"], padx=14, pady=3).pack(side="left", padx=4)
        ndlg.bind("<Escape>", lambda e: ndlg.destroy())

    _btn(mgr_toolbar, "新建任务", _mgr_new_task, C["ac"], "white").pack(side="left", padx=1)
    _btn(mgr_toolbar, "刷新", lambda: _mgr_refresh(), C["bgc"], C["fgb"]).pack(side="left", padx=1)
    tkinter.Frame(mgr_toolbar, bg=C["bd"], width=2, height=20).pack(side="left", fill="y", padx=4)
    _btn(mgr_toolbar, "查看日志", lambda: _mgr_show_logs(dlg),
        C["bgc"], C["fgb"]).pack(side="left", padx=1)
    tkinter.Label(mgr_toolbar, text="( 计划任务模块)",
        font=FONT_SMALL, bg=C["bgc"], fg=C["fgm"]).pack(side="right")

    # 任务表格
    list_f = tkinter.Frame(dlg, bg=C["bgc"], highlightbackground=C["bd"], highlightthickness=1)
    list_f.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))
    list_f.columnconfigure(0, weight=1); list_f.rowconfigure(0, weight=1)

    mgr_tree = ttk.Treeview(list_f,
        columns=("name", "script", "period", "next_run", "last_run", "enabled"),
        show="headings", selectmode="browse")
    for c, t, w in [("name", "任务名称", 110), ("script", "脚本名称", 140),
        ("period", "周期", 60), ("next_run", "下次执行", 140),
        ("last_run", "上次执行", 140), ("enabled", "启用", 45)]:
        mgr_tree.heading(c, text=t); mgr_tree.column(c, width=w, minwidth=40)
    msy = tkinter.Scrollbar(list_f, orient="vertical", command=mgr_tree.yview,
        width=8, relief="flat", bg=C["bd"], troughcolor=C["bgc"])
    mgr_tree.configure(yscrollcommand=msy.set)
    mgr_tree.grid(row=0, column=0, sticky="nsew"); msy.grid(row=0, column=1, sticky="ns")

    def _mgr_refresh():
        for item in mgr_tree.get_children():
            mgr_tree.delete(item)
        for task in state.SCHED_TASKS:
            tid = task["id"]
            nr = sched_module._task_next_runs.get(tid, "--")
            lr = sched_module._task_last_runs.get(tid, "--")
            pl = {"daily":"每天","interval":"间隔","weekly":"每周","once":"一次"}.get(
                task.get("period_type",""), task.get("period_type",""))
            en = "✓" if task.get("enabled", True) else "✗"
            vals = (task.get("name",""), os.path.basename(task.get("script","")),
                    pl, nr, lr, en)
            tag = "en" if task.get("enabled", True) else "dis"
            mgr_tree.insert("", "end", values=vals, iid=tid, tags=(tag,))
            mgr_tree.tag_configure("en", foreground=C["fgb"])
            mgr_tree.tag_configure("dis", foreground=C["fgm"])

    def _mgr_context(event):
        sel = mgr_tree.selection()
        if not sel: return
        tid = sel[0]
        task = next((t for t in state.SCHED_TASKS if t["id"] == tid), None)
        menu = tkinter.Menu(dlg, tearoff=0, bg=C["bgc"], fg=C["fgt"],
            activebackground=C["ac"], activeforeground="white")
        if task:
            en = task.get("enabled", True)
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
        ldlg.geometry("700x420+380+120")
        ldlg.transient(parent)
        ldlg.configure(bg=C["bgc"])
        _set_window_icon(ldlg)
        ldlg.columnconfigure(0, weight=1); ldlg.rowconfigure(1, weight=1)

        ltbar = tkinter.Frame(ldlg, bg=C["bgc"])
        ltbar.grid(row=0, column=0, sticky="ew", padx=8, pady=(8,2))
        _btn(ltbar, "刷新", lambda: _log_refresh(), C["bgc"], C["fgb"]).pack(side="left", padx=1)
        _btn(ltbar, "清除", lambda: (sched_module.clear_task_logs(), _log_refresh()),
            C["dg"], "white").pack(side="left", padx=1)

        lf = tkinter.Frame(ldlg, bg=C["bgc"], highlightbackground=C["bd"], highlightthickness=1)
        lf.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0,8))
        lf.columnconfigure(0, weight=1); lf.rowconfigure(0, weight=1)

        log_tree = ttk.Treeview(lf,
            columns=("task","script","time","status","trigger"),
            show="headings", selectmode="browse")
        for c, t, w in [("task","任务",100),("script","脚本",120),("time","时间",140),
            ("status","状态",70),("trigger","触发",60)]:
            log_tree.heading(c, text=t); log_tree.column(c, width=w, minwidth=40)
        lsy = tkinter.Scrollbar(lf, orient="vertical", command=log_tree.yview,
            width=8, relief="flat", bg=C["bd"], troughcolor=C["bgc"])
        log_tree.configure(yscrollcommand=lsy.set)
        log_tree.grid(row=0, column=0, sticky="nsew"); lsy.grid(row=0, column=1, sticky="ns")
        log_tree.tag_configure("ok", foreground=C["sc"])
        log_tree.tag_configure("fail", foreground=C["dg"])
        log_tree.tag_configure("info", foreground=C["ac"])

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

    _mgr_refresh()
    dlg.bind("<Escape>", lambda e: dlg.destroy())
