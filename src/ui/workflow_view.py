# -*- coding: utf-8 -*-
"""ui.workflow_view — 「工作流」Tab (构建 / 行为 / 换肤)。

路线图 §3.1「阶段二 · 第 3 项」第 3 步: 把 ACRPA.py 中的「工作流 Tab」整体抽出
(第 1 步的底部日志面板见 src/ui/log_dock.py, 第 2 步的执行控制栏见 src/ui/exec_bar.py)。

  · 构建段 (原 ACRPA.py build_app 内 "TAB 4: 工作流" 段): tab_workflow / 工具栏
    (wf_toolbar_outer / wf_canvas / wf_scrollbar / wf_toolbar / 溢出提示) / 按钮组 /
    名称框 (wf_name_var/wf_name_entry) / 主区 wf_main / 左侧操作库 (wf_lib_*) /
    PanedWindow (wf_tree/wf_flow_canvas) / 操作库动作字典 _WF_LIB_ACTIONS / 事件绑定;
  · 行为函数 (原 ACRPA.py:3138-4281): _wf_update_overflow / _wf_on_wheel / _wf_stop /
    _wf_toggle_view / _wf_flow_on_wheel / _wf_insert_step / _wf_populate_library /
    _wf_filter_library / _wf_library_action_name / _wf_add_from_library /
    _wf_library_context_menu / _wf_add_recent / _wf_refresh_recent / _wf_load_recent /
    _wf_add_recent_clicked / _wf_render_flowchart / _draw_arrow / _wf_edit_step_by_index /
    _wf_drag_start/_wf_drag_move/_wf_drag_end / _wf_refresh_tree / _wf_new/_wf_open/_wf_save/
    _wf_add_step/_wf_edit_step / _wf_export_screenshot/_wf_delete_step/_wf_move_up/
    _wf_move_down/_wf_clone_step/_wf_copy_step/_wf_paste_step/_wf_toggle_enable/
    _wf_toggle_comment/_wf_export_template/_wf_context_menu/_wf_run/_wf_open_variable_manager/
    _wf_update_workflow_highlight;
  · 工作流全局: _wf_data / _wf_file / TYPE_LABELS / _FLOW_COLORS / _FLOW_ICONS /
    _NODE_W/_NODE_H/_NODE_GAP / 拖拽状态 / _WF_LIB_CATEGORIES / _WF_RECENT / _WF_CLIPBOARD /
    _WF_LIB_ACTIONS;
  · 换肤回调: 原 ACRPA._refresh_theme 内对工作流的具名回填 (调用 _wf_refresh_tree() +
    wf_flow_canvas 的 flowbg 特判) 移入本模块 ThemeBus 订阅回调 refresh_theme ——
    _refresh_theme 不再出现这两处工作流具名回填。

清理 (行为等价, 详见抽取报告)
------------------------------
  · `_wf_stop` 原定义两次 (ACRPA:3159 / ACRPA:4166, 后定义生效) → 本模块仅保留生效版本;
  · `_wf_scheduler_callback` 为死代码 (体内 `_wf_update_status/_wf_update_flowchart/`
    `_wf_update_log` 在 ACRPA.py 已无定义, 且无任何调用点) → 不再迁移;
  · `_add_sub`/`_del_sub` 虽在 _wf_edit_step 内出现两次, 但分属 parallel / loop 两个
    互斥分支的**嵌套**函数 (非同一作用域重复定义), 语义不同, 二者均保留 (非缺陷)。

领域层
------
`src/workflow.py` (workflow_engine / parse_workflow) **不迁、不改**: 本模块经惰性
`from workflow import ...` 使用领域层。

设计约束
--------
· **零 GUI 副作用**: `import ui.workflow_view` 不建窗、不建 Tk 对象 (仅 import tkinter);
· **不 import ACRPA**: 全部依赖经 `build(..., deps=...)` 显式注入 —— 避免成环
  (src/ 内既有约定: 模块间一律依赖注入, 无反向 import 宿主);
· **字体角色不复制定义**: `FONT_*` 由 build() 从注入值绑定, 本模块不自建
  `_FONT_SPECS` (唯一真源仍在 utils)。

deps 键 (build 的形参)
----------------------
    ui_theme           ui.theme 模块 (subscribe)
    tbtn               宿主 _tbtn (工具栏按钮工厂: 字体/内边距/语义角色/hover)
    sep                宿主 _sep (按钮组分隔竖线)
    sp / ctrl_h        utils.sp / utils.ctrl_h (尺寸令牌)
    darken             utils._darken (流程图节点描边)
    btn                utils._btn (变量管理弹窗按钮)
    state              state 模块 (DARK_MODE / running)
    log1               utils.log1
    show_toast         utils.show_toast
    messagebox         tkinter.messagebox
    filedialog         tkinter.filedialog
    app_root           APP_ROOT (脚本对话框 initialdir / recent_workflow.json 落盘)
    root               主窗口 (Toplevel 父窗口 / 右键菜单底色)
    exec_maxmin_var    执行栏的「最长执行分钟」StringVar (工作流与其共用同一对象)
    set_window_icon    宿主 _set_window_icon (子窗口图标)
    bind_sel_bold     宿主 _bind_sel_bold (变量管理树选中加粗)
    bind_row_hover     宿主 _bind_row_hover (变量管理树行 hover)
    load_pil          宿主 _load_pil (截图前懒加载 PIL)
    get_image_grab    取当前 ImageGrab 类 (流程图截图)

对外接口
--------
    build(parent, *, colors, fonts, deps) -> handle          # 返回 get_widgets()
    refresh_theme(dark=None, colors=None, prev=None)         # ThemeBus 订阅回调
    refresh_tree() / update_highlight() / run() / stop() / toggle_view() / ...
    get_widgets() -> dict / 若干 get_*
"""
import os
import json
import tkinter
from tkinter import ttk
from tkinter import filedialog
from tkinter import messagebox

# ── C 期 (路线图 阶段二 第 9 项): 工作流「图」渲染层 ──
#   flow_graph: 纯逻辑「树 ⇄ 图」(无 tkinter); flow_canvas: 分层布局 + 形状 + 缩放/平移。
#   两者均**不自建配色真源** —— `_FLOW_COLORS` 仍归本模块, 渲染时显式传入。
try:
    import flow_graph as _flow_graph
except Exception:                       # pragma: no cover - 兜底回退旧渲染
    _flow_graph = None
try:
    from ui import flow_canvas as _flow_canvas
except Exception:                       # pragma: no cover
    try:
        from . import flow_canvas as _flow_canvas
    except Exception:
        try:
            import flow_canvas as _flow_canvas
        except Exception:
            _flow_canvas = None

# 回退开关: False → 一律走旧单列渲染 (_wf_render_flowchart_legacy), 便于排查
use_flow_canvas = True
# 缩放/平移状态 (首次渲染时登记; 主题重绘后复位为 1.0x)
_FLOW_ZOOM_STATE = None

__all__ = [
    "build", "refresh_theme", "get_widgets",
    "get_tab_workflow", "get_wf_toolbar", "get_wf_toolbar_outer", "get_wf_canvas",
    "get_wf_scrollbar", "get_wf_loop_var", "get_wf_maxmin_var", "get_wf_name_var",
    "get_wf_tree", "get_wf_paned", "get_wf_flow_canvas", "get_wf_flow_frame",
    "get_show_flow", "get_view_btn", "get_flow_colors",
    # 行为函数 (原名保留)
    "_wf_update_overflow", "_wf_on_wheel", "_wf_flow_on_wheel",
    "_wf_toggle_view", "_wf_insert_step", "_wf_populate_library", "_wf_filter_library",
    "_wf_library_action_name", "_wf_add_from_library", "_wf_library_context_menu",
    "_wf_add_recent", "_wf_refresh_recent", "_wf_load_recent", "_wf_add_recent_clicked",
    "_wf_render_flowchart", "_wf_render_flowchart_legacy", "_draw_arrow",
    "_wf_edit_step_by_index", "use_flow_canvas",
    "_wf_drag_start", "_wf_drag_move", "_wf_drag_end", "_wf_refresh_tree",
    "_wf_new", "_wf_open", "_wf_save", "_wf_add_step", "_wf_edit_step",
    "_wf_export_screenshot", "_wf_delete_step", "_wf_move_up", "_wf_move_down",
    "_wf_clone_step", "_wf_copy_step", "_wf_paste_step", "_wf_toggle_enable",
    "_wf_toggle_comment", "_wf_export_template", "_wf_context_menu", "_wf_run",
    "_wf_stop", "_wf_open_variable_manager", "_wf_update_workflow_highlight",
    # 语义别名 (宿主/自测按语义名引用)
    "update_overflow", "on_wheel", "flow_on_wheel", "toggle_view",
    "refresh_tree", "update_highlight", "run", "stop",
]

# ── 字体角色: 由 build() 从注入值绑定 (本模块不复制 utils._FONT_SPECS) ──
FONT_TITLE = None
FONT_BODY = None
FONT_SMALL = None
FONT_BUTTON = None
FONT_SMALL_BOLD = None
FONT_LOG = None

# ── 当前主题色板 (build / refresh_theme 更新; 行为函数直读 C[key]) ──
C = {}

# ── 模块级控件引用 (build() 后可用; 宿主经 get_* 取回, 亦可直读) ──
tab_workflow = None
wf_toolbar_outer = None
wf_canvas = None
wf_scrollbar = None
wf_toolbar = None
wf_toolbar_overflow_hint = None
wf_loop_var = None
wf_maxmin_var = None
wf_name_var = None
wf_name_entry = None
wf_main = None
wf_lib_frame = None
wf_lib_search = None
wf_lib_tree = None
wf_lib_sy = None
wf_recent_label = None
wf_recent_list = None
wf_paned = None
wf_list_frame = None
wf_tree = None
wf_sy = None
wf_flow_frame = None
wf_flow_canvas = None
wf_flow_scroll_y = None
wf_flow_check_lbl = None   # E 期静态分析摘要标签
_wf_graph_edit = None      # D 期会话内「已编辑图」缓存 (结构未变时复用, 保留连线编辑)
_wf_show_flow = None
_wf_view_btn = None
_WF_LIB_ACTIONS = {}

# ── 注入上下文 (build 填充) ──
_CTX = {
    "ui_theme": None, "tbtn": None, "sep": None, "sp": None, "ctrl_h": None,
    "darken": None, "btn": None, "state": None, "log1": None, "show_toast": None,
    "messagebox": messagebox, "filedialog": filedialog,
    "app_root": "", "root": None, "exec_maxmin_var": None,
    "set_window_icon": None, "bind_sel_bold": None, "bind_row_hover": None,
    "load_pil": None, "get_image_grab": None, "colors": None,
}

# ── 工作流数据 / 图形色板 / 拖拽状态 (原 ACRPA 模块级常量, 原位迁入) ──
_wf_data = {"name": "未命名工作流", "steps": []}
_wf_file = None

TYPE_LABELS = {"script": "- 脚本", "parallel": "|| 并行", "condition": "? 条件",
               "wait": "~ 等待", "loop": "↻ 循环", "command": "↯ 命令",
               "variable": "✚ 变量", "log": "✉ 日志"}

# ── 流程图颜色配置 (唯一真源; ACRPA.py 不再定义 _FLOW_COLORS) ──
_FLOW_COLORS = {
    "script":    "#3B82F6",  # 蓝色
    "parallel":  "#10B981",  # 绿色
    "condition": "#F59E0B",  # 橙色
    "wait":      "#9CA3AF",  # 灰色
    "loop":      "#8B5CF6",  # 紫色
    "command":   "#EF4444",  # 红色
    "variable":  "#0EA5E9",  # 天蓝
    "log":       "#64748B",  # 深灰
}
_FLOW_ICONS = {"script": "[>]", "parallel": "⫼", "condition": "◇", "wait": "⧗",
               "loop": "↻", "command": "↯", "variable": "✚", "log": "✉"}
_NODE_W = 200
_NODE_H = 36
_NODE_GAP = 56

# 拖拽状态
_wf_drag_idx = None
_wf_drag_start_y = 0
_wf_drag_node_items = []

# ── 操作库分类定义 (参考 AutomationOperation 操作库) ──
_WF_LIB_CATEGORIES = [
    ("流程控制", ["运行脚本", "循环", "并行分支", "条件判断", "等待", "日志输出", "设置变量"]),
    ("鼠标操作", ["点击坐标", "鼠标悬停", "鼠标拖拽", "滚动滚轮", "相对移动", "按下按键", "释放按键"]),
    ("键盘操作", ["模拟按键", "组合热键", "文本输入", "逐字写入", "复制", "粘贴"]),
    ("识别/OCR", ["查找图片", "区域找图", "点击图片", "区域点图", "OCR识别文字", "等待文字", "点击文字"]),
    ("窗口控制", ["激活窗口", "关闭窗口", "最小化窗口", "最大化窗口", "获取窗口位置", "等待窗口"]),
    ("文件/系统", ["屏幕截图", "打开程序", "浏览文件"]),
    ("变量操作", ["设置变量", "读取剪贴板", "字符串处理", "数学运算"]),
    ("浏览器", ["打开网页", "浏览器点击", "浏览器输入", "等待元素", "浏览器截图"]),
]

# 最近使用记录 (存入 APP_ROOT/recent_workflow.json)
_WF_RECENT = []
_WF_CLIPBOARD = None  # 复制/粘贴缓冲区


# ══════════════════════════════════════════════════════════════════════
# 依赖注入辅助
# ══════════════════════════════════════════════════════════════════════

def _bind_fonts(fonts):
    """把注入的字体角色绑定到模块级 FONT_* (唯一真源仍是 utils)。"""
    global FONT_TITLE, FONT_BODY, FONT_SMALL, FONT_BUTTON, FONT_SMALL_BOLD, FONT_LOG
    fonts = fonts or {}
    FONT_TITLE = fonts.get("title")
    FONT_BODY = fonts.get("body")
    FONT_SMALL = fonts.get("small")
    FONT_BUTTON = fonts.get("button")
    FONT_SMALL_BOLD = fonts.get("small_bold")
    FONT_LOG = fonts.get("log")


def _toast(msg, kind="info"):
    """经注入的 utils.show_toast 弹提示 (未注入则静默)。"""
    fn = _CTX.get("show_toast")
    if fn is None:
        return
    try:
        fn(_CTX.get("root"), msg, kind)
    except Exception:
        pass


def _log1(msg, tag=None):
    """经注入的 utils.log1 记日志 (未注入则静默)。"""
    fn = _CTX.get("log1")
    if fn is None:
        return
    try:
        fn(msg, tag)
    except Exception:
        pass


def _win_icon(win):
    """经注入的宿主 _set_window_icon 给子窗口设图标 (未注入则静默)。"""
    fn = _CTX.get("set_window_icon")
    if fn is None:
        return
    try:
        fn(win)
    except Exception:
        pass


def _root():
    return _CTX.get("root")


# ══════════════════════════════════════════════════════════════════════
# 构建 (原 ACRPA.build_app 内 "TAB 4: 工作流" 段: :5139-5381)
# ══════════════════════════════════════════════════════════════════════

def build(parent, *, colors, fonts, deps=None):
    """构建工作流 Tab (notebook 内), 返回控件句柄 dict (= get_widgets())。

    parent : Tab 容器 (notebook; 本模块在其内创建并 add 工作流 Tab)。
    colors : 当前主题色板 (utils.C / ui.theme.colors())。
    fonts  : {"title","body","small","button","small_bold","log"} → 绑定模块级 FONT_*。
    deps   : 依赖注入字典 (键见模块 docstring)。
    """
    global C, tab_workflow, wf_toolbar_outer, wf_canvas, wf_scrollbar, wf_toolbar, \
        wf_toolbar_overflow_hint, wf_loop_var, wf_maxmin_var, wf_name_var, wf_name_entry, \
        wf_main, wf_lib_frame, wf_lib_search, wf_lib_tree, wf_lib_sy, wf_recent_label, \
        wf_recent_list, wf_paned, wf_list_frame, wf_tree, wf_sy, wf_flow_frame, \
        wf_flow_canvas, wf_flow_scroll_y, wf_flow_check_lbl, _wf_show_flow, \
        _wf_view_btn, _WF_LIB_ACTIONS

    _CTX.update(deps or {})
    _CTX["colors"] = colors
    if fonts:
        if _CTX.get("messagebox") is None:
            _CTX["messagebox"] = messagebox
        if _CTX.get("filedialog") is None:
            _CTX["filedialog"] = filedialog
    _bind_fonts(fonts)
    C = colors

    notebook = parent
    sp = _CTX.get("sp")
    ctrl_h = _CTX.get("ctrl_h")
    tbtn = _CTX.get("tbtn")
    sep = _CTX.get("sep")
    ui_theme = _CTX.get("ui_theme")

    # ======================================================================
    # TAB 4: 工作流
    # ======================================================================
    tab_workflow = tkinter.Frame(notebook, bg=C["bg"])
    notebook.add(tab_workflow, text="  工作流  ")   # 第2个Tab (执行控制已升级为常驻工具栏)
    # 设置已分离为独立窗口 (settings_window.open_settings_window)，不再占用 Tab
    tab_workflow.columnconfigure(0, weight=1)
    tab_workflow.rowconfigure(0, weight=0)
    tab_workflow.rowconfigure(1, weight=1)

    # ── 工具栏 (与「脚本编辑」「执行控制」同规格: ctrl_h_lg / sp 令牌 / 溢出提示) ──
    wf_toolbar_outer = tkinter.Frame(tab_workflow, bg=C["bg"])
    wf_toolbar_outer.grid(row=0, column=0, sticky="ew",
        padx=sp(6), pady=(sp(4), sp(2)))
    wf_toolbar_outer.columnconfigure(0, weight=1)

    wf_canvas = tkinter.Canvas(wf_toolbar_outer, bg=C["bg"],
        height=ctrl_h("ctrl_h_lg"), highlightthickness=0, bd=0)
    wf_scrollbar = tkinter.Scrollbar(wf_toolbar_outer, orient="horizontal",
        command=wf_canvas.xview, width=sp(6))
    wf_toolbar = tkinter.Frame(wf_canvas, bg=C["bg"])

    # 溢出提示「⇄」: 内容宽度超过可视宽度时显示 (三 Tab 一致)
    wf_toolbar_overflow_hint = tkinter.Label(wf_toolbar_outer, text="⇄", font=FONT_SMALL,
        fg=C["fgm"], bg=C["bg"])

    wf_toolbar.bind("<Configure>",
        lambda e: (wf_canvas.configure(scrollregion=wf_canvas.bbox("all")),
                   _wf_update_overflow()))

    wf_canvas.create_window((0, 0), window=wf_toolbar, anchor="w")
    wf_canvas.configure(xscrollcommand=wf_scrollbar.set)
    wf_canvas.bind("<Configure>", lambda e: _wf_update_overflow())

    wf_canvas.grid(row=0, column=0, sticky="ew")
    wf_scrollbar.grid(row=1, column=0, sticky="ew")

    # ======================================================================
    # SECTION: WorkFlow Toolbar Buttons
    # ======================================================================
    _wfp = sp(1)
    tbtn(wf_toolbar, "新建", lambda: _wf_new(), "ac", "white",
         tip="新建工作流").pack(side="left", padx=_wfp)
    tbtn(wf_toolbar, "打开", lambda: _wf_open(), "ac", "white",
         tip="打开工作流文件").pack(side="left", padx=_wfp)
    tbtn(wf_toolbar, "保存", lambda: _wf_save(), "sc", "white",
         tip="保存工作流").pack(side="left", padx=_wfp)
    tbtn(wf_toolbar, "导出模板", lambda: _wf_export_template(),
         tip="导出为工作流模板").pack(side="left", padx=_wfp)
    sep(wf_toolbar)
    tbtn(wf_toolbar, "+ 脚本", lambda: _wf_add_step("script"),
         tip="添加脚本步骤").pack(side="left", padx=_wfp)
    tbtn(wf_toolbar, "+ 并行", lambda: _wf_add_step("parallel"),
         tip="添加并行步骤").pack(side="left", padx=_wfp)
    tbtn(wf_toolbar, "+ 条件", lambda: _wf_add_step("condition"),
         tip="添加条件分支").pack(side="left", padx=_wfp)
    tbtn(wf_toolbar, "+ 等待", lambda: _wf_add_step("wait"),
         tip="添加等待步骤").pack(side="left", padx=_wfp)
    tbtn(wf_toolbar, "+ 变量", lambda: _wf_add_step("variable"),
         tip="添加变量设置").pack(side="left", padx=_wfp)
    tbtn(wf_toolbar, "+ 循环", lambda: _wf_add_step("loop"),
         tip="添加循环块").pack(side="left", padx=_wfp)
    sep(wf_toolbar)
    # 「运行 / 停止」已移除: 由 ② 执行控制工具栏 (root row1) 按当前 Tab 分派承接
    sep(wf_toolbar)
    # 执行控制: 循环次数 + 无限最长
    tkinter.Label(wf_toolbar, text="循环:", font=FONT_SMALL, bg=C["bg"], fg=C["fgm"]).pack(side="left", padx=(0, 2))
    wf_loop_var = tkinter.StringVar(value="1")
    tkinter.Spinbox(wf_toolbar, textvariable=wf_loop_var, from_=1, to=9999, width=3,
        font=FONT_SMALL, bg=C["ebg"], fg=C["fgb"], relief="solid", bd=1).pack(side="left", padx=(0, 6))
    tkinter.Label(wf_toolbar, text="次 | 最长:", font=FONT_SMALL, bg=C["bg"], fg=C["fgm"]).pack(side="left", padx=(0, 2))
    # 执行参数口径统一: 「最长执行分钟」与「执行控制」运行控制条共用同一个变量/state 键
    wf_maxmin_var = _CTX.get("exec_maxmin_var")
    tkinter.Spinbox(wf_toolbar, textvariable=wf_maxmin_var, from_=0, to=1440, width=4,
        font=FONT_SMALL, bg=C["ebg"], fg=C["fgb"], relief="solid", bd=1).pack(side="left", padx=(0, 2))
    tkinter.Label(wf_toolbar, text="分", font=FONT_SMALL, bg=C["bg"], fg=C["fgm"]).pack(side="left", padx=(0, 6))
    tbtn(wf_toolbar, "变量管理", lambda: _wf_open_variable_manager(),
         tip="管理工作流变量").pack(side="left", padx=_wfp)
    sep(wf_toolbar)
    tbtn(wf_toolbar, "截图", lambda: _wf_export_screenshot(),
         tip="导出流程图截图").pack(side="left", padx=_wfp)
    # 视图切换按钮
    _wf_show_flow = tkinter.BooleanVar(value=True)
    _wf_view_btn = tbtn(wf_toolbar, "[+] 流程图", _wf_toggle_view,
                        tip="显示 / 隐藏流程图")
    _wf_view_btn.pack(side="left", padx=_wfp)

    # ── 工作流名称 ──
    wf_name_var = tkinter.StringVar(value="未命名工作流")
    wf_name_entry = tkinter.Entry(wf_toolbar, textvariable=wf_name_var,
        font=FONT_BODY, width=20, relief="solid", bd=1,
        bg=C["ebg"], fg=C["fgb"], insertbackground=C["fgt"])
    wf_name_entry.pack(side="left", padx=(8, 4))

    # ── 主内容区: [操作库 | PanedWindow(列表 + 流程图)] ──
    wf_main = tkinter.Frame(tab_workflow, bg=C["bg"])
    wf_main.grid(row=1, column=0, sticky="nsew", padx=6, pady=(0, 4))
    wf_main.columnconfigure(1, weight=1)
    wf_main.rowconfigure(0, weight=1)

    # ── 左侧: 操作库 (树形分类, 双击/拖拽添加) ──
    wf_lib_frame = tkinter.Frame(wf_main, bg=C["bgc"],
        highlightbackground=C["bd"], highlightthickness=1, width=180)
    wf_lib_frame.grid(row=0, column=0, sticky="ns")
    wf_lib_frame.grid_propagate(False)
    wf_lib_frame.columnconfigure(0, weight=1)
    wf_lib_frame.rowconfigure(2, weight=1)

    # 操作库标题
    tkinter.Label(wf_lib_frame, text="⊞ 操作库", font=FONT_TITLE,
        bg=C["bgc"], fg=C["fgt"]).grid(row=0, column=0, sticky="ew", padx=8, pady=(6, 2))

    # 搜索框
    wf_lib_search = tkinter.Entry(wf_lib_frame, font=FONT_SMALL,
        relief="solid", bd=1, bg=C["ebg"], fg=C["fgb"], insertbackground=C["fgt"])
    wf_lib_search.grid(row=1, column=0, sticky="ew", padx=6, pady=(0, 4))
    wf_lib_search.insert(0, "")
    wf_lib_search.bind("<KeyRelease>", lambda e: _wf_filter_library())

    # 操作库分类树
    wf_lib_tree = ttk.Treeview(wf_lib_frame, show="tree", selectmode="browse")
    wf_lib_tree.grid(row=2, column=0, sticky="nsew", padx=2, pady=(0, 2))
    wf_lib_sy = tkinter.Scrollbar(wf_lib_frame, orient="vertical",
        command=wf_lib_tree.yview, width=6, relief="flat",
        bg=C["bd"], troughcolor=C["bgc"])
    wf_lib_sy.grid(row=2, column=1, sticky="ns", pady=(0, 2))
    wf_lib_tree.configure(yscrollcommand=wf_lib_sy.set)

    # 最近使用
    wf_recent_label = tkinter.Label(wf_lib_frame, text="最近使用", font=FONT_SMALL,
        bg=C["bgc"], fg=C["fgm"]).grid(row=3, column=0, sticky="w", padx=8, pady=(4, 0))
    wf_recent_list = tkinter.Listbox(wf_lib_frame, font=FONT_SMALL, height=4,
        bg=C["ebg"], fg=C["fgb"], selectbackground=C["ac"], selectforeground="white")
    wf_recent_list.grid(row=4, column=0, columnspan=2, sticky="ew", padx=6, pady=(2, 6))
    wf_recent_list.bind("<Double-1>", lambda e: _wf_add_recent_clicked())

    # ── 右侧: 步骤列表 + 流程图 (PanedWindow 分屏) ──
    wf_paned = tkinter.PanedWindow(wf_main, orient="horizontal",
        bg=C["bd"], sashwidth=3, sashrelief="raised")
    wf_paned.grid(row=0, column=1, sticky="nsew")

    # 左侧: Treeview 步骤列表
    wf_list_frame = tkinter.Frame(wf_paned, bg=C["bgc"],
        highlightbackground=C["bd"], highlightthickness=1)
    wf_list_frame.columnconfigure(0, weight=1)
    wf_list_frame.rowconfigure(0, weight=1)

    wf_tree = ttk.Treeview(wf_list_frame,
        columns=("type", "detail"), show="tree headings", selectmode="browse")
    wf_tree.heading("#0", text="")
    wf_tree.column("#0", width=30, minwidth=30, stretch=False)
    wf_tree.heading("type", text="类型")
    wf_tree.column("type", width=70, minwidth=60)
    wf_tree.heading("detail", text="详情")
    wf_tree.column("detail", width=200, minwidth=100)

    wf_sy = tkinter.Scrollbar(wf_list_frame, orient="vertical", command=wf_tree.yview,
        width=8, relief="flat", bg=C["bd"], troughcolor=C["bgc"])
    wf_tree.configure(yscrollcommand=wf_sy.set)
    wf_tree.grid(row=0, column=0, sticky="nsew")
    wf_sy.grid(row=0, column=1, sticky="ns")

    wf_paned.add(wf_list_frame, minsize=150, width=280)

    # 右侧: Canvas 流程图视图
    wf_flow_frame = tkinter.Frame(wf_paned, bg=C["bgc"],
        highlightbackground=C["bd"], highlightthickness=1)
    wf_flow_frame.columnconfigure(0, weight=1)
    wf_flow_frame.rowconfigure(0, weight=1)

    wf_flow_canvas = tkinter.Canvas(wf_flow_frame, bg=C["flowbg"] if "flowbg" in C else "#F8FAFC",
        highlightthickness=0, bd=0)
    wf_flow_scroll_y = tkinter.Scrollbar(wf_flow_frame, orient="vertical",
        command=wf_flow_canvas.yview, width=8, relief="flat",
        bg=C["bd"], troughcolor=C["bgc"])
    wf_flow_canvas.configure(yscrollcommand=wf_flow_scroll_y.set)
    wf_flow_canvas.grid(row=0, column=0, sticky="nsew")
    wf_flow_scroll_y.grid(row=0, column=1, sticky="ns")

    # E 期: 静态分析摘要 (检查面板) —— 画布下方一行, 随每次渲染刷新
    wf_flow_check_lbl = tkinter.Label(wf_flow_frame, text="检查: —", font=FONT_SMALL,
        bg=C["bgc"], fg=C["fgm"], anchor="w", justify="left",
        highlightthickness=0, wraplength=380)
    wf_flow_check_lbl.grid(row=1, column=0, columnspan=2, sticky="ew", padx=6, pady=(2, 4))

    wf_flow_canvas.bind("<MouseWheel>", _wf_flow_on_wheel)

    wf_paned.add(wf_flow_frame, minsize=200, width=400)

    # 操作库显示名 → 节点构造器 (返回 dict)
    _WF_LIB_ACTIONS = {
        "运行脚本":   lambda: {"type": "script", "path": ""},
        "循环":       lambda: {"type": "loop", "times": "3", "steps": []},
        "并行分支":   lambda: {"type": "parallel", "steps": []},
        "条件判断":   lambda: {"type": "condition", "if": "${var} == True", "then": "", "else": ""},
        "等待":       lambda: {"type": "wait", "seconds": 1},
        "日志输出":   lambda: {"type": "log", "text": ""},
        "设置变量":   lambda: {"type": "variable", "var_name": "", "var_value": ""},
        "点击坐标":   lambda: {"type": "command", "cmd": "坐标", "params": ["0", "0", "左", "1", "0.1", "", "", "", ""]},
        "鼠标悬停":   lambda: {"type": "command", "cmd": "悬停", "params": ["0", "0", "0.1", "", "", "", "", "", ""]},
        "鼠标拖拽":   lambda: {"type": "command", "cmd": "拖拽", "params": ["0", "0", "0.1", "", "", "", "", "", ""]},
        "滚动滚轮":   lambda: {"type": "command", "cmd": "滚轮", "params": ["-300", "", "", "", "", "", "", "", ""]},
        "相对移动":   lambda: {"type": "command", "cmd": "相移", "params": ["10", "10", "0.1", "", "", "", "", "", ""]},
        "按下按键":   lambda: {"type": "command", "cmd": "按下", "params": ["ctrl", "", "", "", "", "", "", "", ""]},
        "释放按键":   lambda: {"type": "command", "cmd": "释放", "params": ["ctrl", "", "", "", "", "", "", "", ""]},
        "模拟按键":   lambda: {"type": "command", "cmd": "按键", "params": ["enter", "1", "0.1", "", "", "", "", "", ""]},
        "组合热键":   lambda: {"type": "command", "cmd": "热键", "params": ["ctrl", "c", "", "", "", "", "", "", ""]},
        "文本输入":   lambda: {"type": "command", "cmd": "输入", "params": ["", "", "", "", "", "", "", "", ""]},
        "逐字写入":   lambda: {"type": "command", "cmd": "写入", "params": ["", "0.05", "auto", "", "", "", "", "", ""]},
        "复制":       lambda: {"type": "command", "cmd": "复制", "params": ["", "", "", "", "", "", "", "", ""]},
        "粘贴":       lambda: {"type": "command", "cmd": "粘贴", "params": ["", "", "", "", "", "", "", "", ""]},
        "查找图片":   lambda: {"type": "command", "cmd": "找图", "params": ["img.png", "0.96", "", "", "", "", "", "", ""]},
        "区域找图":   lambda: {"type": "command", "cmd": "区域找图", "params": ["img.png", "0.96", "0", "0", "800", "600", "True", "", ""]},
        "点击图片":   lambda: {"type": "command", "cmd": "点图", "params": ["img.png", "0.96", "", "", "", "", "", "", ""]},
        "区域点图":   lambda: {"type": "command", "cmd": "区域点图", "params": ["img.png", "0.96", "0", "0", "800", "600", "True", "左", "1"]},
        "OCR识别文字": lambda: {"type": "command", "cmd": "识别文字", "params": ["0", "0", "800", "600", "text", "", "", "", ""]},
        "等待文字":   lambda: {"type": "command", "cmd": "等待文字", "params": ["文字", "10", "存在", "0", "0", "800", "600", "", ""]},
        "点击文字":   lambda: {"type": "command", "cmd": "点击文字", "params": ["文字", "0.7", "左", "0", "0", "800", "600", "", ""]},
        "激活窗口":   lambda: {"type": "command", "cmd": "激活窗口", "params": ["窗口标题", "", "", "", "", "", "", "", ""]},
        "关闭窗口":   lambda: {"type": "command", "cmd": "关闭窗口", "params": ["窗口标题", "", "", "", "", "", "", "", ""]},
        "最小化窗口": lambda: {"type": "command", "cmd": "最小化窗口", "params": ["窗口标题", "", "", "", "", "", "", "", ""]},
        "最大化窗口": lambda: {"type": "command", "cmd": "最大化窗口", "params": ["窗口标题", "", "", "", "", "", "", "", ""]},
        "获取窗口位置": lambda: {"type": "command", "cmd": "获取窗口位置", "params": ["窗口标题", "x", "y", "w", "h", "", "", "", ""]},
        "等待窗口":   lambda: {"type": "command", "cmd": "等待窗口", "params": ["窗口标题", "10", "存在", "", "", "", "", "", ""]},
        "屏幕截图":   lambda: {"type": "command", "cmd": "截屏", "params": ["shot", "", "", "", "", "", "", "", ""]},
        "打开程序":   lambda: {"type": "command", "cmd": "代码", "params": ["run_program", "", "", "", "", "", "", "", ""]},
        "浏览文件":   lambda: {"type": "command", "cmd": "代码", "params": ["browse_file", "", "", "", "", "", "", "", ""]},
        "读取剪贴板": lambda: {"type": "command", "cmd": "读取剪贴板", "params": ["clip", "", "", "", "", "", "", "", ""]},
        "字符串处理": lambda: {"type": "command", "cmd": "字符串处理", "params": ["src", "截取", "0,5", "dst", "", "", "", "", ""]},
        "数学运算":   lambda: {"type": "command", "cmd": "数学运算", "params": ["a + b", "result", "", "", "", "", "", "", ""]},
        "打开网页":   lambda: {"type": "command", "cmd": "打开网页", "params": ["https://", "", "", "", "", "", "", "", ""]},
        "浏览器点击": lambda: {"type": "command", "cmd": "浏览器点击", "params": ["#btn", "", "", "", "", "", "", "", ""]},
        "浏览器输入": lambda: {"type": "command", "cmd": "浏览器输入", "params": ["#input", "text", "", "", "", "", "", "", ""]},
        "等待元素":   lambda: {"type": "command", "cmd": "等待元素", "params": ["#el", "10", "出现", "", "", "", "", "", ""]},
        "浏览器截图": lambda: {"type": "command", "cmd": "浏览器截图", "params": ["page", "page", "", "", "", "", "", "", ""]},
    }
    wf_tree.bind("<Double-1>", _wf_edit_step)

    wf_tree.bind("<Delete>", lambda e: _wf_delete_step())

    wf_tree.bind("<Button-3>", _wf_context_menu)

    # 操作库事件绑定
    wf_lib_tree.bind("<Double-1>", lambda e: _wf_add_from_library())
    wf_lib_tree.bind("<Button-3>", _wf_library_context_menu)
    # 初始化: 填充操作库 + 加载最近使用
    _wf_load_recent()
    _wf_populate_library()
    _wf_refresh_recent()

    # 换肤: 订阅 ThemeBus (幂等, 重复构建只登记一次) —— 取代原 _refresh_theme 的定点回填
    if ui_theme is not None:
        ui_theme.subscribe(refresh_theme)

    return get_widgets()


# ══════════════════════════════════════════════════════════════════════
# 换肤回调 (原 ACRPA._refresh_theme 内工作流具名回填)
# ══════════════════════════════════════════════════════════════════════

def refresh_theme(dark=None, colors=None, prev=None):
    """ThemeBus 订阅回调 (签名与 publish 派发契约一致): 步骤树 tag + 流程图随主题重配。

    · 流程图画布底色 flowbg (原 _walk 内对 wf_flow_canvas 的特判);
    · 步骤树标签颜色 + 重绘流程图 (原 _refresh_theme 末尾 _wf_refresh_tree() 具名回填)。
    """
    global C
    Cc = colors if isinstance(colors, dict) else _CTX.get("colors")
    if not isinstance(Cc, dict):
        return
    C = Cc
    _CTX["colors"] = Cc
    try:
        if wf_flow_canvas is not None and "flowbg" in Cc:
            wf_flow_canvas.configure(bg=Cc["flowbg"])
    except Exception:
        pass
    try:
        _wf_refresh_tree()
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════
# 工具栏溢出 / 滚轮 (原 ACRPA:3138-3154 / 3181-3182)
# ══════════════════════════════════════════════════════════════════════

def _wf_update_overflow(event=None):
    sp = _CTX.get("sp")
    try:
        bb = wf_canvas.bbox("all")
        need = bb[2] if bb else 0
        if need > wf_canvas.winfo_width() + sp(2):
            wf_toolbar_overflow_hint.grid(row=0, column=1, sticky="e", padx=(sp(2), 0))
        else:
            wf_toolbar_overflow_hint.grid_remove()
    except Exception:
        pass


# 滚轮横向滚动 - handler 定义（递归绑定在按钮创建之后）
def _wf_on_wheel(event):
    if event.delta > 0:
        wf_canvas.xview_scroll(-1, "units")
    else:
        wf_canvas.xview_scroll(1, "units")


def _wf_toggle_view():
    if _wf_show_flow.get():
        # 隐藏流程图: 从 PanedWindow 中移除 pane
        wf_paned.forget(wf_flow_frame)
        _wf_show_flow.set(False)
    else:
        # 重新显示流程图面板
        wf_paned.add(wf_flow_frame, minsize=200, width=400)
        _wf_show_flow.set(True)
        _wf_render_flowchart()


# 流程图滚轮 - 使用widget级别绑定
def _wf_flow_on_wheel(event):
    wf_flow_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")


# ── 操作库功能 ──

def _wf_insert_step(step, label=None):
    """将步骤插入到当前选中位置之后 (无选中则追加末尾)。

    label: 操作库显示名 (用于最近使用记录, 保证与 _WF_LIB_ACTIONS 键一致)。
    """
    import copy
    sel = wf_tree.selection()
    if sel:
        idx = wf_tree.index(sel[0])
        _wf_data["steps"].insert(idx + 1, copy.deepcopy(step))
    else:
        _wf_data["steps"].append(copy.deepcopy(step))
    _wf_refresh_tree()
    _wf_add_recent(step, label)


def _wf_populate_library(filter_text=""):
    """填充操作库分类树 (支持搜索过滤)。"""
    wf_lib_tree.delete(*wf_lib_tree.get_children())
    filter_text = (filter_text or "").strip().lower()
    for category, actions in _WF_LIB_CATEGORIES:
        # 过滤
        if filter_text:
            visible = [a for a in actions if filter_text in a.lower()]
            if not visible:
                continue
        else:
            visible = actions
        cat_id = wf_lib_tree.insert("", "end", text="▫ {}".format(category), open=True)
        for act in visible:
            iid = "{}_{}".format(category, act)
            wf_lib_tree.insert(cat_id, "end", iid=iid, text="  {}".format(act))
            wf_lib_tree.tag_configure(act, foreground=C["fgb"])
    # 展开根
    for cid in wf_lib_tree.get_children():
        wf_lib_tree.item(cid, open=True)


def _wf_filter_library():
    """根据搜索框文本过滤操作库。"""
    _wf_populate_library(wf_lib_search.get())


def _wf_library_action_name(iid):
    """从操作库 Treeview item 反查动作显示名 (去掉前缀)。"""
    txt = wf_lib_tree.item(iid, "text").strip()
    return txt


def _wf_add_from_library():
    """操作库双击: 添加节点到工作流。"""
    sel = wf_lib_tree.selection()
    if not sel:
        return
    iid = sel[0]
    # 忽略分类节点 (有子节点)
    if wf_lib_tree.get_children(iid):
        return
    action = _wf_library_action_name(iid)
    maker = _WF_LIB_ACTIONS.get(action)
    if maker:
        step = maker()
        _wf_insert_step(step, label=action)
        _toast("已添加: {}".format(action), "success")


def _wf_library_context_menu(event):
    """操作库右键: 快速添加到末尾。"""
    iid = wf_lib_tree.identify_row(event.y)
    if not iid:
        return
    wf_lib_tree.selection_set(iid)
    action = _wf_library_action_name(iid)
    if not action or wf_lib_tree.get_children(iid):
        return
    menu = tkinter.Menu(_root(), tearoff=0, bg=C["bgc"], fg=C["fgt"],
        activebackground=C["ac"], activeforeground="white")
    menu.add_command(label="添加到末尾",
        command=lambda: (_wf_insert_step(_WF_LIB_ACTIONS[action](), label=action)))
    try:
        menu.tk_popup(event.x_root, event.y_root)
    finally:
        menu.grab_release()


def _wf_add_recent(step, label=None):
    """记录最近使用的节点 (最多 5 个, 存 recent_workflow.json)。

    label: 操作库显示名 (与 _WF_LIB_ACTIONS 键一致, 保证最近使用可点击回填)。
    """
    global _WF_RECENT
    label = label or step.get("cmd", "") or step.get("type", "")
    if not label:
        return
    if label in _WF_RECENT:
        _WF_RECENT.remove(label)
    _WF_RECENT.insert(0, label)
    _WF_RECENT = _WF_RECENT[:5]
    try:
        with open(os.path.join(_CTX.get("app_root", ""), "recent_workflow.json"),
                  "w", encoding="utf-8") as f:
            json.dump(_WF_RECENT, f, ensure_ascii=False)
    except Exception:
        pass
    _wf_refresh_recent()


def _wf_refresh_recent():
    """刷新最近使用列表。"""
    if wf_recent_list is None:
        return
    wf_recent_list.delete(0, "end")
    for label in _WF_RECENT:
        wf_recent_list.insert("end", label)


def _wf_load_recent():
    """启动时加载最近使用记录。"""
    global _WF_RECENT
    try:
        fp = os.path.join(_CTX.get("app_root", ""), "recent_workflow.json")
        if os.path.exists(fp):
            with open(fp, encoding="utf-8") as f:
                _WF_RECENT = json.load(f)[:5]
    except Exception:
        _WF_RECENT = []


def _wf_add_recent_clicked():
    """点击最近使用项: 添加对应节点。"""
    sel = wf_recent_list.curselection()
    if not sel:
        return
    label = wf_recent_list.get(sel[0])
    maker = _WF_LIB_ACTIONS.get(label)
    if maker:
        _wf_insert_step(maker(), label=label)


def _wf_render_flowchart(*args):
    """在 Canvas 上绘制工作流流程图。

    C 期 (路线图 阶段二 第 9 项): 内部改为调用 `ui.flow_canvas.render(...)` ——
    Sugiyama 简化版分层布局 + 形状语义 + 结构化两行文本。函数名与 `_draw_arrow`
    符号保留 (tools/_test_workflow_view_split.py 断言), 并提供回退开关 `use_flow_canvas`:
    关闭或渲染异常时退回旧单列渲染 `_wf_render_flowchart_legacy`。
    """
    if use_flow_canvas and _flow_canvas is not None and _flow_graph is not None:
        try:
            return _wf_render_flow_canvas()
        except Exception:
            pass  # 渲染失败 → 旧渲染兜底, 保证画布不空
    return _wf_render_flowchart_legacy(*args)


def _wf_render_flow_canvas(graph=None):
    """`_wf_data` (树) → promote 成图 → 交由 ui.flow_canvas 分层渲染。

    配色仍取本模块唯一真源 `_FLOW_COLORS`; 缩放/平移由 flow_canvas.bind_zoom_pan 挂载
    (垂直滚轮语义仍属 `_wf_flow_on_wheel`, 不删原绑定)。

    D 期: 支持 `graph` 参数复用「已编辑的图」(端口连线保留) —— 结构未变时
    (`demote(编辑图) == _wf_data`) 复用会话内最后一次编辑的图, 结构变化自动重新提升。
    """
    global _FLOW_ZOOM_STATE, _wf_graph_edit
    if graph is not None:
        _wf_graph_edit = graph
    else:
        reuse = None
        if _wf_graph_edit is not None:
            try:
                if _flow_graph.demote(_wf_graph_edit) == _wf_data:
                    reuse = _wf_graph_edit
            except Exception:
                reuse = None
        graph = reuse if reuse is not None else _flow_graph.promote(_wf_data)
        _wf_graph_edit = graph
    g = graph
    roots = g.roots()
    # 顶层节点 ↔ 原始 steps 序号 的 tag 映射 (双击编辑 / 拖拽换序契约不变)
    index_tags = {n.id: "node_{}".format(i) for i, n in enumerate(roots)}

    cur, results = None, {}
    try:
        from workflow import workflow_engine
        cur = workflow_engine.current_step
        results = dict(getattr(workflow_engine, "_step_results", {}) or {})
    except Exception:
        cur, results = None, {}

    state = _CTX.get("state")
    ctx = {
        "darken": _CTX.get("darken"),
        "dark": bool(state is not None and getattr(state, "DARK_MODE", False)),
        "current_step": cur,
        "step_results": results,
        "fgm": C.get("fgm", "#94A3B8"),
        "sc": C.get("sc", "#10B981"),
        "dg": C.get("dg", "#EF4444"),
        "type_labels": TYPE_LABELS,
        "icons": _FLOW_ICONS,
        "index_tags": index_tags,
        "show_ports": True,
        "ac": C.get("ac", "#0078D4"),
    }
    fonts = {"body": FONT_BODY, "small": FONT_SMALL, "small_bold": FONT_SMALL_BOLD}
    _flow_canvas.render(wf_flow_canvas, g, colors=_FLOW_COLORS, fonts=fonts, ctx=ctx)

    # 缩放/平移: 仅登记一次 (重复渲染不重复绑定)
    if not _FLOW_ZOOM_STATE:
        try:
            _FLOW_ZOOM_STATE = _flow_canvas.bind_zoom_pan(wf_flow_canvas)
        except Exception:
            _FLOW_ZOOM_STATE = {}
    if _FLOW_ZOOM_STATE:
        _FLOW_ZOOM_STATE["scale"] = 1.0   # 重绘回到 1.0x, 与画布坐标一致

    # D 期连线交互 (端口拖拽建边 / 边选中·Delete 删除 / F 适应窗口 / Ctrl+0 重置
    #   缩放 / Ctrl+Z·Ctrl+Y 撤销重做)。图编辑为视图级 (§11.4), 执行仍走树。
    try:
        _flow_canvas.bind_interactions(
            wf_flow_canvas, g, colors=_FLOW_COLORS, fonts=fonts, ctx=ctx,
            re_render=_wf_render_flow_canvas, get_zoom_state=lambda: _FLOW_ZOOM_STATE)
    except Exception:
        pass

    # E 期静态分析摘要 (检查面板): 与图同源, 随渲染刷新
    try:
        issues = _flow_graph.analyze(g)
        e_n, w_n, i_n = _flow_graph.issue_summary(issues)
        if issues:
            head = issues[0]["message"]
            more = "" if len(issues) == 1 else " 等 {} 项".format(len(issues))
            txt = "检查: 错误{} 警告{} 提示{} — {}{}".format(e_n, w_n, i_n, head, more)
            color = C.get("dg") if e_n else (C.get("wn") if w_n else C.get("fgm"))
        else:
            txt = "检查: 无问题"
            color = C.get("sc", C.get("fgm"))
        if wf_flow_check_lbl is not None:
            wf_flow_check_lbl.configure(text=txt, fg=color)
    except Exception:
        pass

    for i in range(len(roots)):
        tag = "node_{}".format(i)
        wf_flow_canvas.tag_bind(tag, "<Double-1>",
            lambda e, idx=i: _wf_edit_step_by_index(idx))
        wf_flow_canvas.tag_bind(tag, "<Button-1>",
            lambda e, idx=i: _wf_drag_start(e, idx))
        wf_flow_canvas.tag_bind(tag, "<B1-Motion>",
            lambda e, idx=i: _wf_drag_move(e, idx))
        wf_flow_canvas.tag_bind(tag, "<ButtonRelease-1>",
            lambda e, idx=i: _wf_drag_end(e, idx))


def _wf_render_flowchart_legacy(*args):
    """(旧) 单列纵向渲染 —— 回退路径; 布局与交互与抽取前一致。"""
    wf_flow_canvas.delete("all")
    steps = _wf_data.get("steps", [])
    if not steps:
        wf_flow_canvas.create_text(200, 60, text="暂无步骤\n点击 [+ 脚本] 等按钮添加",
            font=FONT_BODY, fill=C["fgm"], anchor="center")
        wf_flow_canvas.configure(scrollregion=(0, 0, 400, 150))
        return

    canvas_w = _NODE_W + 80
    node_positions = []
    y = 20

    for i, step in enumerate(steps):
        stype = step.get("type", "script")
        disabled = step.get("enabled", True) is False
        color = _FLOW_COLORS.get(stype, "#6B7280")
        icon = _FLOW_ICONS.get(stype, "?")
        label = TYPE_LABELS.get(stype, stype)
        comment = step.get("comment", "")

        # 节点标签文本
        if stype == "script":
            detail = os.path.basename(step.get("path", ""))[:20] or "未选择"
        elif stype == "parallel":
            detail = "{}子步骤".format(len(step.get("steps", [])))
        elif stype == "condition":
            detail = step.get("if", "")[:18] or "条件"
        elif stype == "wait":
            detail = "{}s".format(step.get("seconds", 1))
        elif stype == "loop":
            detail = "{}次".format(step.get("times", "1"))[:18]
        elif stype == "command":
            detail = "{}".format(step.get("cmd", ""))[:18]
        elif stype == "variable":
            detail = "{}={}".format(step.get("var_name", ""), step.get("var_value", ""))[:20]
        elif stype == "log":
            detail = "{}".format(step.get("text", ""))[:18]
        else:
            detail = str(step)[:20]

        if comment:
            detail = "{} #{}".format(detail, comment[:8])
        # 禁用节点前缀
        if disabled:
            display = "○ {} #{} | {} (禁用)".format(icon, i + 1, detail)
        else:
            display = "{} {} #{} | {}".format(icon, label, i + 1, detail)

        x = 20
        # 执行高亮: 当前执行步骤绿色边框
        darken = _CTX.get("darken")
        outline_color = darken(color) if darken else color
        outline_width = 2
        try:
            from workflow import workflow_engine
            if workflow_engine.current_step == i:
                outline_color = C["sc"]
                outline_width = 3
            elif workflow_engine._step_results.get(i) == "error":
                outline_color = C["dg"]
                outline_width = 3
        except Exception:
            pass
        if disabled:
            color = "#94A3B8"  # 禁用节点灰色

        # 绘制节点矩形
        node_id = wf_flow_canvas.create_rectangle(
            x, y, x + _NODE_W, y + _NODE_H,
            fill=color, outline=outline_color, width=outline_width,
            tags=("node", "node_{}".format(i)))
        # 节点文字
        text_id = wf_flow_canvas.create_text(
            x + 8, y + _NODE_H // 2,
            text=display, anchor="w",
            font=FONT_SMALL_BOLD,
            fill="white", tags=("node", "node_{}".format(i)))

        # 节点阴影效果 (深色半透明 — 根据主题切换颜色)
        state = _CTX.get("state")
        shadow_color = "#334155" if (state is not None and state.DARK_MODE) else "#CBD5E0"
        shadow_id = wf_flow_canvas.create_rectangle(
            x + 2, y + 2, x + _NODE_W + 2, y + _NODE_H + 2,
            fill=shadow_color, outline="", tags=("shadow",))
        wf_flow_canvas.tag_lower(shadow_id, node_id)

        # 绑定双击编辑
        for tag in ("node_{}".format(i),):
            wf_flow_canvas.tag_bind(tag, "<Double-1>",
                lambda e, idx=i: _wf_edit_step_by_index(idx))

        node_positions.append((x, y))
        y += _NODE_GAP

    # 绘制箭头连线
    for i in range(len(steps) - 1):
        x1 = 20 + _NODE_W // 2
        y1 = node_positions[i][1] + _NODE_H
        x2 = 20 + _NODE_W // 2
        y2 = node_positions[i + 1][1]
        _draw_arrow(wf_flow_canvas, x1, y1, x2, y2, color=C["fgm"])

    # 更新滚动区域
    total_h = y + 20
    wf_flow_canvas.configure(scrollregion=(0, 0, canvas_w, max(total_h, 200)))

    # 节点拖拽绑定
    for i in range(len(steps)):
        wf_flow_canvas.tag_bind("node_{}".format(i), "<Button-1>",
            lambda e, idx=i: _wf_drag_start(e, idx))
        wf_flow_canvas.tag_bind("node_{}".format(i), "<B1-Motion>",
            lambda e, idx=i: _wf_drag_move(e, idx))
        wf_flow_canvas.tag_bind("node_{}".format(i), "<ButtonRelease-1>",
            lambda e, idx=i: _wf_drag_end(e, idx))


def _draw_arrow(canvas, x1, y1, x2, y2, color="#94A3B8"):
    """绘制带箭头的连线"""
    canvas.create_line(x1, y1, x2, y2 - 6, fill=color, width=2, tags="arrow")
    # 箭头三角形
    canvas.create_polygon(
        x2 - 5, y2 - 6, x2 + 5, y2 - 6, x2, y2,
        fill=color, outline=color, tags="arrow")


def _wf_edit_step_by_index(idx):
    """通过索引编辑步骤（供流程图双击使用）"""
    if idx >= len(_wf_data["steps"]):
        return
    # 选中 Treeview 对应行
    children = wf_tree.get_children()
    if idx < len(children):
        wf_tree.selection_set(children[idx])
        wf_tree.see(children[idx])
    _wf_edit_step()


def _wf_drag_start(event, idx):
    """开始拖拽节点"""
    global _wf_drag_idx, _wf_drag_start_y
    _wf_drag_idx = idx
    _wf_drag_start_y = event.y


def _wf_drag_move(event, idx):
    """拖拽移动中"""
    global _wf_drag_idx, _wf_drag_start_y
    if _wf_drag_idx is None or _wf_drag_idx != idx:
        return
    dy = event.y - _wf_drag_start_y
    # 移动节点图形
    for item in wf_flow_canvas.find_withtag("node_{}".format(idx)):
        wf_flow_canvas.move(item, 0, dy)
    _wf_drag_start_y = event.y


def _wf_drag_end(event, idx):
    """结束拖拽：根据位置交换步骤顺序"""
    global _wf_drag_idx
    if _wf_drag_idx is None:
        return
    steps = _wf_data["steps"]
    n = len(steps)
    if n < 2:
        _wf_drag_idx = None
        _wf_render_flowchart()
        return

    # 根据最终 y 位置计算新索引
    gap = _NODE_GAP
    start_y = 20
    new_idx = round((event.y - start_y) / gap)
    new_idx = max(0, min(n - 1, new_idx))

    old_idx = _wf_drag_idx
    _wf_drag_idx = None

    if new_idx != old_idx:
        # 交换步骤
        step = steps.pop(old_idx)
        steps.insert(new_idx, step)
        _wf_refresh_tree()

    _wf_render_flowchart()


def _wf_refresh_tree():
    """刷新工作流步骤列表"""
    wf_tree.delete(*wf_tree.get_children())
    for i, step in enumerate(_wf_data.get("steps", [])):
        stype = step.get("type", "script")
        label = TYPE_LABELS.get(stype, stype)
        disabled = step.get("enabled", True) is False
        comment = step.get("comment", "")
        if stype == "script":
            detail = step.get("path", "")
        elif stype == "parallel":
            count = len(step.get("steps", []))
            detail = "{} 个子步骤".format(count)
        elif stype == "condition":
            detail = "if {} then...".format(step.get("if", ""))
        elif stype == "wait":
            detail = "{}s".format(step.get("seconds", 1))
        elif stype == "loop":
            detail = "循环 {} 次, {} 子步骤".format(step.get("times", "1"), len(step.get("steps", [])))
        elif stype == "command":
            detail = "{} {}".format(step.get("cmd", ""), step.get("params", [])[:2])
        elif stype == "variable":
            detail = "{} = {}".format(step.get("var_name", ""), step.get("var_value", ""))
        elif stype == "log":
            detail = step.get("text", "")
        else:
            detail = str(step)
        if comment:
            detail = "#{} {}".format(comment, detail)
        if disabled:
            label = "○ " + label
        tag = "step_{}".format(i)
        wf_tree.insert("", "end", text=str(i + 1), values=(label, detail), tags=(tag,))
        wf_tree.tag_configure(tag, foreground=C["fgm"] if disabled else C["fgb"])
    _wf_render_flowchart()


def _wf_new():
    _wf_data["name"] = "未命名工作流"
    _wf_data["steps"] = []
    global _wf_file
    _wf_file = None
    wf_name_var.set("未命名工作流")
    _wf_refresh_tree()


def _wf_open():
    fp = filedialog.askopenfilename(title="打开工作流",
        filetypes=[('JSON', '*.json'), ('YAML', '*.yaml')], initialdir=_CTX.get("app_root", ""))
    if fp:
        try:
            from workflow import parse_workflow
            data = parse_workflow(fp)
            _wf_data["name"] = data.get("name", os.path.basename(fp))
            _wf_data["steps"] = data.get("steps", [])
            global _wf_file
            _wf_file = fp
            wf_name_var.set(_wf_data["name"])
            _wf_refresh_tree()
            _toast("已加载: {} ({}步骤)".format(_wf_data["name"], len(_wf_data["steps"])), "success")
        except Exception as e:
            messagebox.showerror("加载失败", str(e))


def _wf_save():
    if not _wf_data.get("steps"):
        messagebox.showwarning("提示", "工作流为空")
        return
    _wf_data["name"] = wf_name_var.get()
    fp = filedialog.asksaveasfilename(title="保存工作流",
        defaultextension=".json", filetypes=[('JSON', '*.json')],
        initialdir=_CTX.get("app_root", ""))
    if fp:
        try:
            with open(fp, "w", encoding="utf-8") as f:
                json.dump(_wf_data, f, indent=2, ensure_ascii=False)
            global _wf_file
            _wf_file = fp
            _toast("已保存 ({}步骤)".format(len(_wf_data["steps"])), "success")
        except Exception as e:
            messagebox.showerror("保存失败", str(e))


def _wf_add_step(stype):
    import copy
    if stype == "script":
        # 选择脚本文件
        fp = filedialog.askopenfilename(title="选择脚本",
            filetypes=[('Excel', '*.xls'), ('ACRPA 脚本', '*.acrpas'), ('All', '*.*')],
            initialdir=_CTX.get("app_root", ""))
        if fp:
            # 使用相对路径（如果在工作流同目录下）
            if _wf_file and os.path.commonprefix([fp, os.path.dirname(_wf_file)]):
                fp = os.path.relpath(fp, os.path.dirname(_wf_file))
            step = {"type": "script", "path": fp.replace("\\", "/")}
        else:
            return
    elif stype == "parallel":
        step = {"type": "parallel", "steps": []}
    elif stype == "condition":
        step = {"type": "condition", "if": "${var} == True", "then": "", "else": ""}
    elif stype == "wait":
        step = {"type": "wait", "seconds": 5}
    elif stype == "variable":
        step = {"type": "variable", "var_name": "var", "var_value": ""}
    elif stype == "loop":
        step = {"type": "loop", "times": "3", "steps": []}
    else:
        return
    step["enabled"] = True
    step["comment"] = ""
    # 插入到选中位置之后
    sel = wf_tree.selection()
    if sel:
        idx = wf_tree.index(sel[0])
        _wf_data["steps"].insert(idx + 1, step)
    else:
        _wf_data["steps"].append(step)
    _wf_refresh_tree()


def _wf_edit_step(event=None):
    """双击编辑步骤"""
    sel = wf_tree.selection()
    if not sel:
        return
    idx = wf_tree.index(sel[0])
    if idx >= len(_wf_data["steps"]):
        return
    step = _wf_data["steps"][idx]
    stype = step.get("type", "script")

    dlg = tkinter.Toplevel(_root())
    dlg.title("编辑步骤 {}".format(idx + 1))
    dlg.geometry("420x300")
    dlg.transient(_root())
    dlg.grab_set()
    dlg.configure(bg=C["bgc"])
    _win_icon(dlg)

    tkinter.Label(dlg, text="类型: {}".format(TYPE_LABELS.get(stype, stype)),
        font=FONT_TITLE, bg=C["bgc"], fg=C["fgt"]).pack(pady=(10, 6))

    if stype == "script":
        tkinter.Label(dlg, text="脚本路径:", bg=C["bgc"], fg=C["fgb"]).pack()
        path_var = tkinter.StringVar(value=step.get("path", ""))
        pf = tkinter.Frame(dlg, bg=C["bgc"])
        pf.pack(pady=4)
        tkinter.Entry(pf, textvariable=path_var, width=40,
            font=FONT_BODY, bg=C["ebg"], fg=C["fgb"],
            insertbackground=C["fgt"]).pack(side="left", padx=(0, 4))
        def _browse():
            fp = filedialog.askopenfilename(
                filetypes=[('Excel', '*.xls'), ('ACRPA 脚本', '*.acrpas')])
            if fp:
                if _wf_file and os.path.commonprefix([fp, os.path.dirname(_wf_file)]):
                    fp = os.path.relpath(fp, os.path.dirname(_wf_file))
                path_var.set(fp.replace("\\", "/"))
        tkinter.Button(pf, text="浏览", command=_browse, font=FONT_SMALL,
            bg=C["ac"], fg="white").pack(side="left")
        def _save_script():
            step["path"] = path_var.get()
            _wf_refresh_tree()
            dlg.destroy()
        tkinter.Button(dlg, text="确定", command=_save_script,
            font=FONT_BUTTON, bg=C["ac"], fg="white").pack(pady=10)

    elif stype == "parallel":
        sub_steps = step.get("steps", [])
        tkinter.Label(dlg, text="子步骤 ({} 个):".format(len(sub_steps)),
            bg=C["bgc"], fg=C["fgb"]).pack(pady=4)
        sf = tkinter.Frame(dlg, bg=C["bgc"])
        sf.pack(fill="both", expand=True, padx=10, pady=4)
        sub_list = tkinter.Listbox(sf, font=FONT_BODY, bg=C["ebg"], fg=C["fgb"],
            selectbackground=C["ac"], selectforeground="white", height=6)
        sub_list.pack(side="left", fill="both", expand=True)
        for s in sub_steps:
            sub_list.insert("end", s.get("path", str(s)))
        def _add_sub():
            fp = filedialog.askopenfilename(
                filetypes=[('Excel', '*.xls'), ('ACRPA 脚本', '*.acrpas')])
            if fp:
                if _wf_file and os.path.commonprefix([fp, os.path.dirname(_wf_file)]):
                    fp = os.path.relpath(fp, os.path.dirname(_wf_file))
                sub_steps.append({"type": "script", "path": fp.replace("\\", "/")})
                sub_list.insert("end", fp)
        def _del_sub():
            sel = sub_list.curselection()
            if sel:
                idx_s = sel[0]
                del sub_steps[idx_s]
                sub_list.delete(idx_s)
        bf = tkinter.Frame(dlg, bg=C["bgc"])
        bf.pack()
        tkinter.Button(bf, text="+ 添加", command=_add_sub,
            font=FONT_SMALL, bg=C["ac"], fg="white").pack(side="left", padx=2, pady=4)
        tkinter.Button(bf, text="- 删除", command=_del_sub,
            font=FONT_SMALL, bg=C["dg"], fg="white").pack(side="left", padx=2, pady=4)
        def _save_parallel():
            step["steps"] = sub_steps
            _wf_refresh_tree()
            dlg.destroy()
        tkinter.Button(dlg, text="确定", command=_save_parallel,
            font=FONT_BUTTON, bg=C["ac"], fg="white").pack(pady=4)

    elif stype == "condition":
        tkinter.Label(dlg, text="条件表达式:", bg=C["bgc"], fg=C["fgb"]).pack(pady=(10, 2))
        cond_var = tkinter.StringVar(value=step.get("if", ""))
        tkinter.Entry(dlg, textvariable=cond_var, width=40,
            font=FONT_LOG, bg=C["ebg"], fg=C["fgb"],
            insertbackground=C["fgt"]).pack(pady=4)
        tkinter.Label(dlg, text="(使用 ${var} 引用变量)", font=FONT_SMALL,
            bg=C["bgc"], fg=C["fgm"]).pack()
        tkinter.Label(dlg, text="成立时执行:", bg=C["bgc"], fg=C["fgb"]).pack(pady=(8, 2))
        then_var = tkinter.StringVar(value=step.get("then", ""))
        tkinter.Entry(dlg, textvariable=then_var, width=40,
            font=FONT_BODY, bg=C["ebg"], fg=C["fgb"],
            insertbackground=C["fgt"]).pack(pady=2)
        tkinter.Label(dlg, text="不成立时执行:", bg=C["bgc"], fg=C["fgb"]).pack(pady=(4, 2))
        else_var = tkinter.StringVar(value=step.get("else", ""))
        tkinter.Entry(dlg, textvariable=else_var, width=40,
            font=FONT_BODY, bg=C["ebg"], fg=C["fgb"],
            insertbackground=C["fgt"]).pack(pady=2)
        def _save_cond():
            step["if"] = cond_var.get()
            step["then"] = then_var.get() if then_var.get() else ""
            step["else"] = else_var.get() if else_var.get() else ""
            _wf_refresh_tree()
            dlg.destroy()
        tkinter.Button(dlg, text="确定", command=_save_cond,
            font=FONT_BUTTON, bg=C["ac"], fg="white").pack(pady=10)

    elif stype == "wait":
        tkinter.Label(dlg, text="等待秒数:", bg=C["bgc"], fg=C["fgb"]).pack(pady=(10, 4))
        sec_var = tkinter.StringVar(value=str(step.get("seconds", 1)))
        tkinter.Entry(dlg, textvariable=sec_var, width=10,
            font=FONT_BODY, bg=C["ebg"], fg=C["fgb"],
            insertbackground=C["fgt"]).pack(pady=4)
        def _save_wait():
            try:
                step["seconds"] = float(sec_var.get())
            except ValueError:
                step["seconds"] = 1
            _wf_refresh_tree()
            dlg.destroy()
        tkinter.Button(dlg, text="确定", command=_save_wait,
            font=FONT_BUTTON, bg=C["ac"], fg="white").pack(pady=10)

    elif stype == "command":
        # 命令节点: 命令名下拉 + schema 驱动的参数表单 (ui.param_form)
        # 阶段二第 8 项: 9 个无标签 Entry → 具名控件 (类型/枚举/默认/变长)。
        import commands
        from ui import param_form
        # 阶段二新增项①: 命令下拉对缺外部能力的命令加 ⚠ 角标 (读取时 strip 清洗)。
        _cmd_names = commands.display_names()
        tkinter.Label(dlg, text="命令:", bg=C["bgc"], fg=C["fgb"]).pack(pady=(10, 2))
        cmd_var = tkinter.StringVar(value=step.get("cmd", ""))
        cmd_combo = ttk.Combobox(dlg, textvariable=cmd_var, values=_cmd_names,
            state="readonly", width=24)
        cmd_combo.pack(pady=2)
        tkinter.Label(dlg, text="参数:", bg=C["bgc"], fg=C["fgb"]).pack(pady=(8, 2))
        _pf_holder = tkinter.Frame(dlg, bg=C["bgc"])
        _pf_holder.pack(pady=2, fill="x")
        _pf_fonts = {"body": FONT_BODY, "small": FONT_SMALL,
                     "button": FONT_BUTTON, "log": FONT_LOG}
        _pf_state = {"get": None}

        def _rebuild_command_form(seed_args):
            for w in _pf_holder.winfo_children():
                w.destroy()
            _fr, _get = param_form.build_arg_form(
                _pf_holder, commands.strip_display_badge(cmd_var.get()), seed_args,
                colors=C, fonts=_pf_fonts, theme=_CTX.get("ui_theme"))
            _fr.pack(fill="x")
            _pf_state["get"] = _get

        def _on_command_change(event=None):
            # 切换命令: 用当前参数值作为初值重建表单
            seed = _pf_state["get"]() if _pf_state["get"] else (
                step.get("params") or [""] * 9)
            _rebuild_command_form(seed)

        cmd_combo.bind("<<ComboboxSelected>>", _on_command_change)
        _rebuild_command_form(step.get("params", []) or [""] * 9)

        def _save_command():
            step["cmd"] = commands.strip_display_badge(cmd_var.get())
            # get_args() 长度恒为 9 (与 9 位 args 契约一致)
            step["params"] = _pf_state["get"]() if _pf_state["get"] else [""] * 9
            _wf_refresh_tree()
            dlg.destroy()
        tkinter.Button(dlg, text="确定", command=_save_command,
            font=FONT_BUTTON, bg=C["ac"], fg="white").pack(pady=10)

    elif stype == "loop":
        # 循环节点: 次数/条件 + 子步骤
        tkinter.Label(dlg, text="循环次数/条件:", bg=C["bgc"], fg=C["fgb"]).pack(pady=(10, 2))
        loop_var = tkinter.StringVar(value=str(step.get("times", "1")))
        tkinter.Entry(dlg, textvariable=loop_var, width=30,
            font=FONT_LOG, bg=C["ebg"], fg=C["fgb"],
            insertbackground=C["fgt"]).pack(pady=2)
        tkinter.Label(dlg, text="(数字=固定次数, 或 ${x} < 5 条件循环)", font=FONT_SMALL,
            bg=C["bgc"], fg=C["fgm"]).pack()
        # 子步骤管理
        sub_steps = step.get("steps", []) or []
        tkinter.Label(dlg, text="子步骤 ({} 个):".format(len(sub_steps)),
            bg=C["bgc"], fg=C["fgb"]).pack(pady=(8, 2))
        sf = tkinter.Frame(dlg, bg=C["bgc"])
        sf.pack(fill="both", expand=True, padx=10, pady=2)
        sub_list = tkinter.Listbox(sf, font=FONT_BODY, bg=C["ebg"], fg=C["fgb"],
            selectbackground=C["ac"], selectforeground="white", height=4)
        sub_list.pack(side="left", fill="both", expand=True)
        for s in sub_steps:
            sub_list.insert("end", s.get("path", s.get("cmd", str(s))))
        def _add_sub():
            fp = filedialog.askopenfilename(
                filetypes=[('Excel', '*.xls'), ('ACRPA 脚本', '*.acrpas')])
            if fp:
                if _wf_file and os.path.commonprefix([fp, os.path.dirname(_wf_file)]):
                    fp = os.path.relpath(fp, os.path.dirname(_wf_file))
                sub_steps.append({"type": "script", "path": fp.replace("\\", "/")})
                sub_list.insert("end", fp)
        def _del_sub():
            sel = sub_list.curselection()
            if sel:
                idx_s = sel[0]
                del sub_steps[idx_s]
                sub_list.delete(idx_s)
        bf = tkinter.Frame(dlg, bg=C["bgc"])
        bf.pack()
        tkinter.Button(bf, text="+ 添加", command=_add_sub,
            font=FONT_SMALL, bg=C["ac"], fg="white").pack(side="left", padx=2, pady=2)
        tkinter.Button(bf, text="- 删除", command=_del_sub,
            font=FONT_SMALL, bg=C["dg"], fg="white").pack(side="left", padx=2, pady=2)
        def _save_loop():
            if not sub_steps:
                messagebox.showwarning("提示", "循环节点至少需要 1 个子步骤，请先添加")
                return
            step["times"] = loop_var.get().strip()
            step["steps"] = sub_steps
            _wf_refresh_tree()
            dlg.destroy()
        tkinter.Button(dlg, text="确定", command=_save_loop,
            font=FONT_BUTTON, bg=C["ac"], fg="white").pack(pady=4)

    elif stype == "variable":
        # 变量节点
        tkinter.Label(dlg, text="变量名:", bg=C["bgc"], fg=C["fgb"]).pack(pady=(10, 2))
        vn_var = tkinter.StringVar(value=step.get("var_name", ""))
        tkinter.Entry(dlg, textvariable=vn_var, width=30,
            font=FONT_LOG, bg=C["ebg"], fg=C["fgb"],
            insertbackground=C["fgt"]).pack(pady=2)
        tkinter.Label(dlg, text="变量值:", bg=C["bgc"], fg=C["fgb"]).pack(pady=(8, 2))
        vv_var = tkinter.StringVar(value=step.get("var_value", ""))
        tkinter.Entry(dlg, textvariable=vv_var, width=30,
            font=FONT_LOG, bg=C["ebg"], fg=C["fgb"],
            insertbackground=C["fgt"]).pack(pady=2)
        def _save_var():
            step["var_name"] = vn_var.get().strip()
            step["var_value"] = vv_var.get()
            _wf_refresh_tree()
            dlg.destroy()
        tkinter.Button(dlg, text="确定", command=_save_var,
            font=FONT_BUTTON, bg=C["ac"], fg="white").pack(pady=10)

    elif stype == "log":
        # 日志节点
        tkinter.Label(dlg, text="日志内容:", bg=C["bgc"], fg=C["fgb"]).pack(pady=(10, 2))
        lg_var = tkinter.StringVar(value=step.get("text", ""))
        tkinter.Entry(dlg, textvariable=lg_var, width=40,
            font=FONT_BODY, bg=C["ebg"], fg=C["fgb"],
            insertbackground=C["fgt"]).pack(pady=4)
        tkinter.Label(dlg, text="(支持 ${var} 变量引用)", font=FONT_SMALL,
            bg=C["bgc"], fg=C["fgm"]).pack()
        def _save_log():
            step["text"] = lg_var.get()
            _wf_refresh_tree()
            dlg.destroy()
        tkinter.Button(dlg, text="确定", command=_save_log,
            font=FONT_BUTTON, bg=C["ac"], fg="white").pack(pady=10)

    # ── 通用: 启用/禁用 + 备注 ──
    common_frame = tkinter.Frame(dlg, bg=C["bgc"])
    common_frame.pack(fill="x", padx=10, pady=(4, 2))
    enabled_var = tkinter.BooleanVar(value=step.get("enabled", True))
    ttk.Checkbutton(common_frame, text="启用此步骤",
        variable=enabled_var).pack(side="left")
    cmt_var = tkinter.StringVar(value=step.get("comment", ""))
    tkinter.Label(common_frame, text="备注:", font=FONT_SMALL,
        bg=C["bgc"], fg=C["fgm"]).pack(side="left", padx=(8, 2))
    tkinter.Entry(common_frame, textvariable=cmt_var, width=18,
        font=FONT_SMALL, bg=C["ebg"], fg=C["fgb"],
        insertbackground=C["fgt"]).pack(side="left")

    # 统一保存通用字段 (enabled/comment) — 无论点击哪个类型的"确定"或关闭窗口都会生效
    def _apply_common():
        step["enabled"] = enabled_var.get()
        step["comment"] = cmt_var.get()

    # 包装 dlg.destroy: 任何关闭路径都先保存通用字段
    _orig_destroy = dlg.destroy
    def _destroy_with_common():
        try:
            _apply_common()
        except Exception:
            pass
        _wf_refresh_tree()
        _orig_destroy()
    dlg.destroy = _destroy_with_common
    dlg.bind("<Escape>", lambda e: dlg.destroy())


def _wf_export_screenshot():
    """导出流程图截图为 PNG"""
    if not _wf_data.get("steps"):
        messagebox.showwarning("提示", "工作流为空，无法导出")
        return
    fp = filedialog.asksaveasfilename(title="导出流程图",
        defaultextension=".png", filetypes=[('PNG', '*.png')],
        initialdir=_CTX.get("app_root", ""))
    if fp:
        try:
            load_pil = _CTX.get("load_pil")
            if load_pil:
                load_pil()
            # 确保流程图已渲染
            wf_flow_canvas.update_idletasks()
            x = wf_flow_canvas.winfo_rootx()
            y = wf_flow_canvas.winfo_rooty()
            w = wf_flow_canvas.winfo_width()
            h = wf_flow_canvas.winfo_height()
            if w > 0 and h > 0:
                grab = _CTX.get("get_image_grab")
                img = grab().grab(bbox=(x, y, x + w, y + h))
                img.save(fp)
                _toast("流程图已保存: {}".format(os.path.basename(fp)), "success")
                _log1("流程图导出: {}".format(fp))
            else:
                messagebox.showwarning("提示", "流程图区域不可见，请先切换到工作流Tab")
        except Exception as e:
            messagebox.showerror("导出失败", str(e))


def _wf_delete_step():
    sel = wf_tree.selection()
    if sel:
        idx = wf_tree.index(sel[0])
        if idx < len(_wf_data["steps"]):
            del _wf_data["steps"][idx]
            _wf_refresh_tree()


def _wf_move_up():
    sel = wf_tree.selection()
    if sel:
        idx = wf_tree.index(sel[0])
        if idx > 0:
            _wf_data["steps"][idx], _wf_data["steps"][idx - 1] = \
                _wf_data["steps"][idx - 1], _wf_data["steps"][idx]
            _wf_refresh_tree()


def _wf_move_down():
    sel = wf_tree.selection()
    if sel:
        idx = wf_tree.index(sel[0])
        if idx < len(_wf_data["steps"]) - 1:
            _wf_data["steps"][idx], _wf_data["steps"][idx + 1] = \
                _wf_data["steps"][idx + 1], _wf_data["steps"][idx]
            _wf_refresh_tree()


# 右键菜单
def _wf_clone_step():
    """克隆当前选中的步骤"""
    sel = wf_tree.selection()
    if not sel: return
    idx = wf_tree.index(sel[0])
    if idx < len(_wf_data["steps"]):
        import copy
        clone = copy.deepcopy(_wf_data["steps"][idx])
        _wf_data["steps"].insert(idx + 1, clone)
        _wf_refresh_tree()


def _wf_copy_step():
    """复制选中步骤到缓冲区"""
    global _WF_CLIPBOARD
    sel = wf_tree.selection()
    if not sel: return
    idx = wf_tree.index(sel[0])
    if idx < len(_wf_data["steps"]):
        import copy
        _WF_CLIPBOARD = copy.deepcopy(_wf_data["steps"][idx])
        _toast("已复制步骤", "info")


def _wf_paste_step():
    """粘贴缓冲区的步骤到选中位置之后"""
    global _WF_CLIPBOARD
    if _WF_CLIPBOARD is None:
        return
    import copy
    sel = wf_tree.selection()
    if sel:
        idx = wf_tree.index(sel[0])
        _wf_data["steps"].insert(idx + 1, copy.deepcopy(_WF_CLIPBOARD))
    else:
        _wf_data["steps"].append(copy.deepcopy(_WF_CLIPBOARD))
    _wf_refresh_tree()
    _toast("已粘贴步骤", "success")


def _wf_toggle_enable():
    """禁用/启用选中步骤"""
    sel = wf_tree.selection()
    if not sel: return
    idx = wf_tree.index(sel[0])
    if idx < len(_wf_data["steps"]):
        step = _wf_data["steps"][idx]
        step["enabled"] = not step.get("enabled", True)
        _wf_refresh_tree()
        _toast("已{}步骤".format("启用" if step["enabled"] else "禁用"),
               "success" if step["enabled"] else "warning")


def _wf_toggle_comment():
    """为选中步骤添加/移除注释标记"""
    sel = wf_tree.selection()
    if not sel: return
    idx = wf_tree.index(sel[0])
    if idx < len(_wf_data["steps"]):
        step = _wf_data["steps"][idx]
        if step.get("comment"):
            step["comment"] = ""
        else:
            # 提示输入注释
            dlg = tkinter.Toplevel(_root())
            dlg.title("步骤注释")
            dlg.geometry("360x120")
            dlg.transient(_root()); dlg.grab_set()
            dlg.configure(bg=C["bgc"])
            _win_icon(dlg)
            tkinter.Label(dlg, text="注释内容:", font=FONT_BODY,
                bg=C["bgc"], fg=C["fgb"]).pack(pady=(10, 4))
            entry = tkinter.Entry(dlg, font=FONT_BODY, width=40,
                bg=C["ebg"], fg=C["fgb"], insertbackground=C["fgt"])
            entry.pack(pady=4, padx=10)
            entry.focus_set()
            def _save():
                step["comment"] = entry.get().strip()
                _wf_refresh_tree()
                dlg.destroy()
            def _cancel():
                step["comment"] = ""
                dlg.destroy()
            bf = tkinter.Frame(dlg, bg=C["bgc"])
            bf.pack(pady=6)
            tkinter.Button(bf, text="确定", command=_save,
                font=FONT_BUTTON, bg=C["ac"], fg="white").pack(side="left", padx=4)
            tkinter.Button(bf, text="取消", command=_cancel,
                font=FONT_BUTTON, bg=C["bgc"], fg=C["fgb"]).pack(side="left", padx=4)
            dlg.bind("<Return>", lambda e: _save())
            dlg.bind("<Escape>", lambda e: _cancel())
        _wf_refresh_tree()


def _wf_export_template():
    """导出工作流为模板"""
    if not _wf_data.get("steps"):
        messagebox.showwarning("提示", "工作流为空")
        return
    fp = filedialog.asksaveasfilename(title="导出工作流模板",
        defaultextension=".json", filetypes=[('JSON', '*.json')],
        initialdir=_CTX.get("app_root", ""))
    if fp:
        try:
            with open(fp, "w", encoding="utf-8") as f:
                json.dump(_wf_data, f, indent=2, ensure_ascii=False)
            _toast("模板已导出: {}".format(os.path.basename(fp)), "success")
        except Exception as e:
            messagebox.showerror("导出失败", str(e))


def _wf_context_menu(event):
    menu = tkinter.Menu(_root(), tearoff=0, bg=C["bgc"], fg=C["fgt"],
        activebackground=C["ac"], activeforeground="white")
    menu.add_command(label="编辑", command=_wf_edit_step)
    menu.add_separator()
    menu.add_command(label="克隆", command=_wf_clone_step)
    menu.add_command(label="复制", command=_wf_copy_step)
    menu.add_command(label="粘贴", command=_wf_paste_step)
    menu.add_separator()
    menu.add_command(label="上移", command=_wf_move_up)
    menu.add_command(label="下移", command=_wf_move_down)
    menu.add_separator()
    menu.add_command(label="禁用/启用", command=_wf_toggle_enable)
    menu.add_command(label="添加注释", command=_wf_toggle_comment)
    menu.add_separator()
    menu.add_command(label="删除", command=_wf_delete_step)
    try:
        menu.tk_popup(event.x_root, event.y_root)
    finally:
        menu.grab_release()


def _wf_run():
    """运行工作流"""
    if not _wf_data.get("steps"):
        messagebox.showwarning("提示", "工作流为空，请先添加步骤")
        return
    state = _CTX.get("state")
    if state is not None and state.running:
        _log1("有任务正在运行，请先停止", "warning")
        return

    if state is not None:
        state.quit2 = False
        state.quit3 = True
        state.pause_event.set()

    # 读取执行控制参数
    try:
        loop_count = int(wf_loop_var.get())
    except ValueError:
        loop_count = 1
    try:
        max_minutes = float(wf_maxmin_var.get())
    except ValueError:
        max_minutes = 0

    def _run_thread():
        try:
            from workflow import workflow_engine
            workflow_engine._variables.clear()
            base = os.path.dirname(_wf_file) if _wf_file else _CTX.get("app_root", "")
            wf_def = dict(_wf_data)
            wf_def["loop_count"] = loop_count
            wf_def["max_minutes"] = max_minutes
            workflow_engine.run_workflow(wf_def, base)
        except Exception as e:
            _log1("工作流执行异常: {}".format(e), "error")

    import threading
    t = threading.Thread(target=_run_thread, daemon=True)
    t.start()


def _wf_stop():
    from workflow import workflow_engine
    workflow_engine.stop()
    _log1("工作流已停止")


def _wf_open_variable_manager():
    """打开工作流变量管理弹窗 (编辑 workflow_engine._variables)。"""
    try:
        from workflow import workflow_engine
    except Exception:
        return

    btn = _CTX.get("btn")
    dlg = tkinter.Toplevel(_root())
    dlg.title("工作流变量管理")
    dlg.geometry("420x380")
    dlg.transient(_root()); dlg.grab_set()
    dlg.configure(bg=C["bgc"])
    _win_icon(dlg)

    tkinter.Label(dlg, text="工作流共享变量", font=FONT_TITLE,
        bg=C["bgc"], fg=C["fgt"]).pack(pady=(10, 6))

    lf = tkinter.Frame(dlg, bg=C["bgc"])
    lf.pack(fill="both", expand=True, padx=10, pady=4)
    var_tree = ttk.Treeview(lf, columns=("name", "value"), show="headings", height=8)
    var_tree.heading("name", text="变量名")
    # §4.5 show="headings" 降级: 选中整行 acl 底 + 加粗
    bind_bold = _CTX.get("bind_sel_bold")
    bind_hover = _CTX.get("bind_row_hover")
    if bind_bold:
        bind_bold(var_tree)
    if bind_hover:
        bind_hover(var_tree)
    var_tree.heading("value", text="值")
    var_tree.column("name", width=150)
    var_tree.column("value", width=200)
    var_tree.pack(side="left", fill="both", expand=True)
    sy = tkinter.Scrollbar(lf, orient="vertical", command=var_tree.yview, width=6)
    sy.pack(side="right", fill="y")
    var_tree.configure(yscrollcommand=sy.set)

    def _refresh_vars():
        var_tree.delete(*var_tree.get_children())
        for k, v in sorted(workflow_engine._variables.items()):
            var_tree.insert("", "end", values=(k, str(v)))

    _refresh_vars()

    # 输入行
    entry_frame = tkinter.Frame(dlg, bg=C["bgc"])
    entry_frame.pack(fill="x", padx=10, pady=4)
    tkinter.Label(entry_frame, text="变量名:", font=FONT_SMALL,
        bg=C["bgc"], fg=C["fgm"]).pack(side="left")
    vn_entry = tkinter.Entry(entry_frame, width=12, font=FONT_SMALL,
        bg=C["ebg"], fg=C["fgb"], insertbackground=C["fgt"])
    vn_entry.pack(side="left", padx=2)
    tkinter.Label(entry_frame, text="值:", font=FONT_SMALL,
        bg=C["bgc"], fg=C["fgm"]).pack(side="left", padx=(6, 2))
    vv_entry = tkinter.Entry(entry_frame, width=16, font=FONT_SMALL,
        bg=C["ebg"], fg=C["fgb"], insertbackground=C["fgt"])
    vv_entry.pack(side="left", padx=2)

    def _add_var():
        name = vn_entry.get().strip()
        value = vv_entry.get()
        if not name:
            return
        # 数值转换
        try:
            if "." in value:
                value = float(value)
            else:
                value = int(value)
        except ValueError:
            pass
        workflow_engine._variables[name] = value
        _refresh_vars()
        vn_entry.delete(0, "end"); vv_entry.delete(0, "end")
        _log1("工作流变量: {} = {}".format(name, value))

    def _del_var():
        sel = var_tree.selection()
        if sel:
            name = var_tree.item(sel[0], "values")[0]
            workflow_engine._variables.pop(name, None)
            _refresh_vars()

    btn_frame = tkinter.Frame(dlg, bg=C["bgc"])
    btn_frame.pack(fill="x", padx=10, pady=4)
    if btn:
        btn(btn_frame, "添加", _add_var, "ac", "white").pack(side="left", padx=2)
        btn(btn_frame, "删除选中", _del_var, "dg", "white").pack(side="left", padx=2)
        btn(btn_frame, "清空", lambda: (workflow_engine._variables.clear(), _refresh_vars()),
            C["bgc"], C["fgb"]).pack(side="left", padx=2)

    tkinter.Button(dlg, text="关闭", command=dlg.destroy,
        font=FONT_BUTTON, bg=C["bgc"], fg=C["fgb"]).pack(pady=8)
    dlg.bind("<Escape>", lambda e: dlg.destroy())
    dlg.lift(); dlg.focus_force()


def _wf_update_workflow_highlight():
    """执行工作流时周期刷新流程图高亮 (供 _periodic 调用)。

    仅在当前步骤变化时重绘, 避免每 100ms 全量重绘导致的闪烁与 CPU 开销;
    执行结束后复位一次高亮。
    """
    try:
        from workflow import workflow_engine
        state = _CTX.get("state")
        _last = getattr(_wf_update_workflow_highlight, "_last_step", -2)
        if state is not None and state.running and workflow_engine.current_step >= 0:
            cur = workflow_engine.current_step
            if _last != cur:
                _wf_update_workflow_highlight._last_step = cur
                _wf_render_flowchart()
        elif _last != -1:
            # 执行结束/停止: 复位一次高亮
            _wf_update_workflow_highlight._last_step = -1
            _wf_render_flowchart()
    except Exception:
        pass


# ── 语义别名 (宿主/自测按语义名引用; 原名保留以便逐行比对迁移) ──
update_overflow = _wf_update_overflow
on_wheel = _wf_on_wheel
flow_on_wheel = _wf_flow_on_wheel
toggle_view = _wf_toggle_view
refresh_tree = _wf_refresh_tree
update_highlight = _wf_update_workflow_highlight
run = _wf_run
stop = _wf_stop


# ══════════════════════════════════════════════════════════════════════
# 控件取回 (宿主转发别名 / tools 直读)
# ══════════════════════════════════════════════════════════════════════

def get_tab_workflow():
    return tab_workflow


def get_wf_toolbar():
    return wf_toolbar


def get_wf_toolbar_outer():
    return wf_toolbar_outer


def get_wf_canvas():
    return wf_canvas


def get_wf_scrollbar():
    return wf_scrollbar


def get_wf_loop_var():
    return wf_loop_var


def get_wf_maxmin_var():
    return wf_maxmin_var


def get_wf_name_var():
    return wf_name_var


def get_wf_tree():
    return wf_tree


def get_wf_paned():
    return wf_paned


def get_wf_flow_canvas():
    return wf_flow_canvas


def get_wf_flow_frame():
    return wf_flow_frame


def get_show_flow():
    return _wf_show_flow


def get_view_btn():
    return _wf_view_btn


def get_flow_colors():
    """流程图图形色板 (宿主/tools 兼容别名; 唯一真源在本模块)。"""
    return dict(_FLOW_COLORS)


def get_widgets():
    """取回全部控件句柄 (与构建返回值一致)。"""
    return {
        "tab_workflow": tab_workflow,
        "wf_toolbar_outer": wf_toolbar_outer,
        "wf_canvas": wf_canvas,
        "wf_scrollbar": wf_scrollbar,
        "wf_toolbar": wf_toolbar,
        "wf_toolbar_overflow_hint": wf_toolbar_overflow_hint,
        "wf_loop_var": wf_loop_var,
        "wf_maxmin_var": wf_maxmin_var,
        "wf_name_var": wf_name_var,
        "wf_name_entry": wf_name_entry,
        "wf_main": wf_main,
        "wf_lib_frame": wf_lib_frame,
        "wf_lib_search": wf_lib_search,
        "wf_lib_tree": wf_lib_tree,
        "wf_lib_sy": wf_lib_sy,
        "wf_recent_label": wf_recent_label,
        "wf_recent_list": wf_recent_list,
        "wf_paned": wf_paned,
        "wf_list_frame": wf_list_frame,
        "wf_tree": wf_tree,
        "wf_sy": wf_sy,
        "wf_flow_frame": wf_flow_frame,
        "wf_flow_canvas": wf_flow_canvas,
        "wf_flow_scroll_y": wf_flow_scroll_y,
        "_wf_show_flow": _wf_show_flow,
        "_wf_view_btn": _wf_view_btn,
        "_WF_LIB_ACTIONS": _WF_LIB_ACTIONS,
        # 语义别名 (宿主可读)
        "loop_var": wf_loop_var, "maxmin_var": wf_maxmin_var,
        "name_var": wf_name_var, "tree": wf_tree, "paned": wf_paned,
        "show_flow": _wf_show_flow, "view_btn": _wf_view_btn,
    }
