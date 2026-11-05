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
  · S0-04 模块库数据源 (开发计划 §2.1): 左栏「操作库」改由 `ui.module_catalog` 供给
    (`res/help/command_groups.json` + `command_schema.json` → 11 组 88 命令, 带 icon /
    参数签名 / 悬停说明 / 检索命中片段), `_WF_LIB_CATEGORIES` + `_WF_LIB_ACTIONS` 降级为
    **兜底快照** (json 缺失/损坏时仍能建树, 见 `_wf_library_catalog()` + `_wf_lib_notice()`);
    双击/右键覆盖全部 88 条: 老动作走 `_WF_LIB_ACTIONS` 构造器 (行为不变), 其余走通用
    `{"type":"command","cmd":<命令名>,"params":[""]*9}` 节点 (`_wf_library_make_step_by_name`)。
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

from core.scale import clamp_scale, scaled  # P3-2: 工具条缩放按钮共用缩放钳制/换算真源
from core.hit import detect_point  # P3-3: 节点单击/拖拽几何命中 (共享 node 标签, 免逐节点 tag_bind)

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

# ── S0-04 (开发计划 §2.1): 模块库数据源适配器 ──
#   `ui.module_catalog` 是**纯逻辑**模块 (只读 res/help/*.json, 零 tkinter): 模块库树的一级
#   分组 / 二级命令 / 检索 / 悬停说明全部由它供给。导入失败 (冻结版漏打包 / 旧版本目录) 时
#   置 None, 模块库全程退回本模块既有的 `_WF_LIB_CATEGORIES` + `_WF_LIB_ACTIONS` 硬编码兜底。
try:
    from ui import module_catalog as _module_catalog
except Exception:                       # pragma: no cover - 兜底回退硬编码
    try:
        from . import module_catalog as _module_catalog
    except Exception:
        try:
            import module_catalog as _module_catalog
        except Exception:
            _module_catalog = None
# 回退开关: False → 一律走旧单列渲染 (_wf_render_flowchart_legacy), 便于排查
use_flow_canvas = True
# P1 / WF-B1 交互增强开关 (新增, 默认 True):
#   False → 关闭 hover(网格/吸附/框选/拒绝反馈/拖拽指示线), 回到 P0 的连线交互。
#   既有契约 (tag 契约 / zoom 语义 / 单列回退) 不受影响; 仅新增绑定受其保护。
_WF_INTERACT_V2 = True
# P1 / WF-B2 面板/工具栏/小地图/联动总开关 (新增, 默认 True):
#   False → 不创建「检查面板展开列表 / 画布悬浮工具条 / 小地图 / 大纲联动绑定」,
#   整体回退到 WF-B1 行为 (检查面板仍是单行 wf_flow_check_lbl)。
#   各控件均为独立子控件 —— 隐藏即回退 (工具栏/小地图 place_forget,
#   检查列表 grid_remove); wf_flow_check_lbl 始终保留。
_WF_PANEL_V2 = True
# 缩放/平移状态 (首次渲染时登记; 存渲染期 zoom 乘子, 重绘时透传给 render)
_FLOW_ZOOM_STATE = None

__all__ = [
    "build", "refresh_theme", "get_widgets",
    "get_tab_workflow", "get_wf_toolbar", "get_wf_toolbar_outer", "get_wf_canvas",
    "get_wf_scrollbar", "get_wf_loop_var", "get_wf_maxmin_var", "get_wf_name_var",
    "get_wf_tree", "get_wf_paned", "get_wf_flow_canvas", "get_wf_flow_frame",
    "get_wf_outer_paned",
    "get_show_flow", "get_view_btn", "get_flow_colors",
    "set_context_hook", "get_wf_step_count",
    # 行为函数 (原名保留)
    # 行为函数 (原名保留)
    "_wf_update_overflow", "_wf_on_wheel", "_wf_flow_on_wheel",
    "_wf_toggle_view", "_wf_insert_step", "_wf_populate_library", "_wf_filter_library",
    "_wf_library_action_name", "_wf_add_from_library", "_wf_library_context_menu",
    "_wf_add_recent", "_wf_refresh_recent", "_wf_load_recent", "_wf_add_recent_clicked",
    "_wf_render_flowchart", "_wf_render_flowchart_legacy", "_draw_arrow",
    "_wf_edit_step_by_index", "use_flow_canvas",
    "_wf_drag_start", "_wf_drag_move", "_wf_drag_end", "_wf_refresh_tree",
    "_wf_new", "_wf_open", "_wf_save", "_wf_save_as", "_wf_clear",
    "wf_save", "wf_save_as", "wf_clear", "wf_new", "wf_open",
    "wf_is_modified", "get_workflow_name",
    "_wf_add_step", "_wf_edit_step",
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
wf_outer_paned = None      # 外层横向 PanedWindow (操作库 ↔ 步骤列表+流程图), 操作库可拉伸
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
# 注: 「操作库显示名 → 节点构造器」`_WF_LIB_ACTIONS` 在下方 `_WF_LIB_CATEGORIES` 之后定义
# (S0-04 起由 build() 体内提到模块级), 故此处不再前置声明空 dict。

# ── WF-B2 新增控件 / 状态 (P1-1 检查面板 / P1-2 工具栏 + 小地图 / P1-5 联动) ──
wf_flow_toolbar = None      # 画布悬浮工具条 (右上)
wf_minimap = None           # 画布小地图 (右下, sp(120)×sp(80))
wf_check_tree = None        # 检查面板展开列表 (show="tree")
_WF_FLOW_GRID = True        # 网格开关键 (工具条 ⌗)
_WF_MINIMAP_ON = True       # 小地图开关键 (工具条 ◲)
_WF_SYNCING = False         # 大纲 ↔ 画布联动防回环
_FLOW_INTERACT_ST = None    # 最近一次 bind_interactions 状态 (复用 N5 选中集合)
_wf_check_issues = []       # 最近一次 analyze() 结果 (行 iid → issue 映射)
_wf_check_expanded = False  # 检查面板展开态
_wf_check_summary = "检查: —"
_wf_check_color = None
_wf_mini_map = None         # 小地图映射 (flow_canvas.minimap 返回值)
_ISSUE_BADGE = {"error": "错误", "warning": "警告", "info": "提示"}

# ── 注入上下文 (build 填充) ──
_CTX = {
    "ui_theme": None, "tbtn": None, "sep": None, "sp": None, "ctrl_h": None,
    "darken": None, "btn": None, "state": None, "log1": None, "show_toast": None,
    "messagebox": messagebox, "filedialog": filedialog,
    "app_root": "", "root": None, "exec_maxmin_var": None,
    "set_window_icon": None, "bind_sel_bold": None, "bind_row_hover": None,
    "load_pil": None, "get_image_grab": None, "colors": None,
    "ctx": None,
}

# ── 工作流数据 / 图形色板 / 拖拽状态 (原 ACRPA 模块级常量, 原位迁入) ──
_wf_data = {"name": "未命名工作流", "steps": []}
_wf_file = None             # 当前工作流文件 (None = 尚未落盘, 保存时转「另存」)
# 「已保存」基线签名 (见 _wf_signature / wf_is_modified): 初始基线就是空工作流本身,
# 因此启动后新加的任何步骤/改名都会被判定为「未保存」, 不会因为「还没存过」而漏提示。
_wf_saved_sig = json.dumps(_wf_data, sort_keys=True, ensure_ascii=False)
_wf_context_hook = None     # 宿主注入: fn(name, step_count) —— 更新运行栏工作流上下文条

TYPE_LABELS = {"script": "- 脚本", "parallel": "|| 并行", "condition": "? 条件",
               "wait": "~ 等待", "loop": "↻ 循环", "command": "↯ 命令",
               "variable": "✚ 变量", "log": "✉ 日志", "retry": "⟳ 重试",
               "workflow": "⧉ 子工作流"}

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
    "retry":     "#F97316",  # 橙色 (重试)
    "workflow":  "#14B8A6",  # 青绿 (子工作流)
}
_FLOW_ICONS = {"script": "[>]", "parallel": "⫼", "condition": "◇", "wait": "⧗",
               "loop": "↻", "command": "↯", "variable": "✚", "log": "✉",
               "retry": "⟳", "workflow": "⧉"}
_NODE_W = 200
_NODE_H = 36
_NODE_GAP = 56

# ── 流程图「结构/状态」样式真源 (P0-3; 与 _FLOW_COLORS 同级, 禁止迁出本模块) ──
#   类型色仍只由 _FLOW_COLORS 提供; 本表只承载结构色/状态色 (方案 §4.5)。
#   值优先取自当前主题 C[...]; 经 ctx["style"] 传入 ui.flow_canvas 消费。
#   随 build()/refresh_theme() 重建 (换肤刷新); 缺失键由 flow_canvas 回退默认值。
_FLOW_STYLE = {}


def _refresh_flow_style(palette=None):
    """由当前主题色板重建 _FLOW_STYLE (唯一真源: 本模块; 方案 §4.5)。"""
    Cc = palette if isinstance(palette, dict) else (C if isinstance(C, dict) else {})
    st = _CTX.get("state")
    dark = bool(st is not None and getattr(st, "DARK_MODE", False))

    def g(key, default):
        return Cc.get(key) or default

    flowbg = g("flowbg", "#f8fafc")
    edge = g("fgm", "#94A3B8")
    blend = getattr(_flow_canvas, "blend", None) if _flow_canvas is not None else None
    _FLOW_STYLE.clear()
    _FLOW_STYLE.update({
        "flowbg": flowbg,                    # 禁用态 40% 近似的混色底
        "disabled_mix": 0.4,                 # blend(类型色, flowbg, 0.4)
        "shadow": (g("border_strong", "#4A4A4A") if dark else g("bd", "#E1E1E1")),
        "text_on_node": "#FFFFFF",
        "text_sub": (blend("#FFFFFF", edge, 0.15) if callable(blend) else "#E2E8F0"),
        "grid_fine": g("gridline", "#EEEEEE"),
        "grid_coarse": g("bd", "#E1E1E1"),
        "edge": edge,
        "edge_sel": g("ac", "#0078D4"),
        "edge_reject": g("dg", "#EF4444"),
        "edge_back": _FLOW_COLORS.get("loop", "#8B5CF6"),
        "walked": g("sc", "#10B981"),
        "terminal": g("fgm", "#94A3B8"),
        "disabled_color": (blend(edge, flowbg, 0.4) if callable(blend) else "#94A3B8"),
        "port_idle": None,                    # None → 取节点类型色 (由 flow_canvas 处理)
        "port_hover": g("ac", "#0078D4"),
    })
    return _FLOW_STYLE


def _flow_disabled_fill(base_color):
    """禁用态填充 = blend(类型色, flowbg, 0.4) (Tk 无 alpha 的混色近似, P0-2)。"""
    blend = getattr(_flow_canvas, "blend", None) if _flow_canvas is not None else None
    flowbg = _FLOW_STYLE.get("flowbg") or (C.get("flowbg") if isinstance(C, dict) else None)
    if callable(blend) and isinstance(flowbg, str):
        return blend(base_color, flowbg, _FLOW_STYLE.get("disabled_mix", 0.4))
    return base_color


# 拖拽状态
_wf_drag_idx = None
_wf_drag_start_y = 0
_wf_drag_node_items = []
_wf_drag_indic = None   # P1-4/N6: 拖拽插入指示线 item id (局部重绘, 不触发全量渲染)

# ── 操作库分类定义 (参考 AutomationOperation 操作库) ──
# **S0-04 起降级为兜底常量**: 正常路径的数据源是
#   `res/help/command_groups.json` + `res/help/command_schema.json` (11 组 88 条),
# 由 [`ui.module_catalog`](src/ui/module_catalog.py) 适配, 见 `_wf_library_catalog()`。
# 本常量**仅当 `module_catalog` 不可用或分组 json 缺失/损坏时使用** (8 分类 45 动作),
# 保证工作流 Tab 永远打得开; 不删除 (S0-04 卡禁止事项)。
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

# 操作库显示名 → 节点构造器 (返回 dict)
# S0-04: **字面量未改动**, 只是从 `build()` 体内提到模块级 ——
#   ① 兜底路径与「命令名 ↔ 显示名反查」需要在 build() 之前/之外可用 (冒烟测试亦然);
#   ② 行为等价: 原实现每次 build() 都重建同一份字面量, 模块级只建一次, 内容逐字相同;
#   ③ 它仍只服务"旧的 45 个动作显示名 + 同名命令"; 其余新命令走通用构造器
#      `_wf_library_make_step_by_name()` → `{"type":"command","cmd":<命令名>,"params":[""]*9}`。
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

# ── S0-04: 模块库运行态 ──
_WF_LIB_CATALOG = None        # 当前目录 (`module_catalog.load_catalog()` 结果, 惰性缓存)
_WF_LIB_NODES = {}            # iid → 命令/动作节点 dict (双击添加 + 悬停说明的唯一反查表)
_WF_LIB_DEGRADED = False      # 是否已发过"降级"提示 (只提示一次, 绝不弹模态框)
_WF_LIB_STATUS = ""           # 最近一次降级提示文本 (状态行 / 测试可读)
_WF_LIB_MAKER_BY_CMD = None   # 命令名 → 旧构造器 反向索引 (惰性构建, 见 _wf_lib_maker_by_cmd)
_WF_LIB_GROUP_ICONS = {}       # 分组名 → 图标 (填充库时同步; 命令行沿用分组图标)
_WF_LIB_TIP_TIMER = None      # 悬停说明的 500ms after 句柄
_WF_LIB_TIP_IID = None        # 悬停说明当前指向的树行

# 检索命中字段 → 中文标签 (非命令名的命中把它拼到行文本里, 让命中片段可见)
_WF_LIB_FIELD_LABELS = {"cmd": "命令", "sub": "子族", "group": "分组",
                        "params": "参数", "params_text": "参数说明"}

# 最近使用记录 (存入 APP_ROOT/recent_workflow.json)
_WF_RECENT = []
_WF_CLIPBOARD = None  # 复制/粘贴缓冲区

# 工作流撤销/重做快照栈 (深拷贝完整 _wf_data, 有界)
_WF_UNDO = []
_WF_REDO = []
_WF_UNDO_MAX = 100


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

def build(parent, *, colors, fonts, deps=None, tab=None):
    """构建工作流 Tab (notebook 内), 返回控件句柄 dict (= get_widgets())。

    parent : Tab 容器 (notebook; 本模块在其内创建并 add 工作流 Tab)。
    colors : 当前主题色板 (utils.C / ui.theme.colors())。
    fonts  : {"title","body","small","button","small_bold","log"} → 绑定模块级 FONT_*。
    deps   : 依赖注入字典 (键见模块 docstring)。
    tab    : 已由宿主创建并 add 进 notebook 的空 Tab 容器 —— 延迟构建时传入, 本模块
             直接在它内部填充控件 (不再新建/重复 add)。None → 旧行为 (自建 Tab)。
    """
    global C, tab_workflow, wf_toolbar_outer, wf_canvas, wf_scrollbar, wf_toolbar, \
        wf_toolbar_overflow_hint, wf_loop_var, wf_maxmin_var, wf_name_var, wf_name_entry, \
        wf_main, wf_outer_paned, wf_lib_frame, wf_lib_search, wf_lib_tree, wf_lib_sy, \
        wf_recent_label, wf_recent_list, wf_paned, wf_list_frame, wf_tree, wf_sy, wf_flow_frame, \
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
    _refresh_flow_style(C)

    notebook = parent
    sp = _CTX.get("sp")
    ctrl_h = _CTX.get("ctrl_h")
    tbtn = _CTX.get("tbtn")
    sep = _CTX.get("sep")
    ui_theme = _CTX.get("ui_theme")

    # ======================================================================
    # TAB 4: 工作流
    # ======================================================================
    if tab is not None:
        # 延迟构建 (v3 §5.2 P1): 宿主已建好空 Tab 并 add 进 notebook, 这里只填充内容
        tab_workflow = tab
        try:
            tab_workflow.configure(bg=C["bg"])
        except Exception:
            pass
    else:
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
        command=wf_canvas.xview, width=sp("sb_w_thin"))
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
         tip="保存 (Ctrl+S): 已有文件直接覆盖, 未落盘时另存").pack(side="left", padx=_wfp)
    tbtn(wf_toolbar, "另存", lambda: _wf_save_as(),
         tip="另存为… (Ctrl+Shift+S)").pack(side="left", padx=_wfp)
    tbtn(wf_toolbar, "版本", lambda: _wf_version_history(),
         tip="查看工作流版本历史 (恢复/对比)").pack(side="left", padx=_wfp)
    sep(wf_toolbar)
    tbtn(wf_toolbar, "↶ 撤销", lambda: _wf_undo(),
         tip="撤销上一步修改 (Ctrl+Z)").pack(side="left", padx=_wfp)
    tbtn(wf_toolbar, "↷ 重做", lambda: _wf_redo(),
         tip="重做被撤销的修改 (Ctrl+Shift+Z)").pack(side="left", padx=_wfp)
    sep(wf_toolbar)
    tbtn(wf_toolbar, "AI 生成", lambda: _wf_ai_generate(),
         tip="用自然语言描述生成工作流图 (静态校验兜底)").pack(side="left", padx=_wfp)
    sep(wf_toolbar)
    # 清空紧邻文件组: 工具栏右侧常被溢出裁剪, 放在末尾会「看不见也点不到」
    tbtn(wf_toolbar, "清空", lambda: _wf_clear(), "dg", "white",
         tip="清空工作流所有步骤 (需确认)").pack(side="left", padx=_wfp)
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
    tbtn(wf_toolbar, "+ 重试", lambda: _wf_add_step("retry"),
         tip="添加重试块").pack(side="left", padx=_wfp)
    tbtn(wf_toolbar, "+ 子流程", lambda: _wf_add_step("workflow"),
         tip="添加子工作流引用").pack(side="left", padx=_wfp)
    sep(wf_toolbar)
    # 「运行 / 停止」已移除: 由 ② 执行控制工具栏 (root row1) 按当前 Tab 分派承接。
    # 执行控制「循环次数 / 最长执行分钟」与「变量管理」同样只保留 root row1 运行栏一处
    # 入口: 本工具栏不再重复摆放文字提示/输入框 (原「循环 / 最长: 跟随上方运行栏」
    # 提示已随移出循环按钮一并删除), 这里只保留变量对象引用 (与运行栏同一个
    # StringVar) 供 _wf_run 读取, 避免两处各自维护导致口径分裂。
    wf_loop_var = _CTX.get("exec_loop_var") or tkinter.StringVar(value="1")
    wf_maxmin_var = _CTX.get("exec_maxmin_var") or tkinter.StringVar(value="0")
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
    # 名称变化: 同步进 _wf_data 并刷新运行栏上下文条。
    # 写进数据是「重命名 = 一次修改」的判定依据 (退出确认/保存签名都看 _wf_data)。
    def _wf_on_name_edit(*_a):
        _wf_data["name"] = wf_name_var.get()
        _wf_emit_context()
    try:
        wf_name_var.trace_add("write", _wf_on_name_edit)
    except Exception:
        pass

    # ── 主内容区: PanedWindow[操作库 | PanedWindow(列表 + 流程图)] ──
    #   外层 wf_outer_paned 提供操作库侧边栏的拉伸 sash; 内层 wf_paned 管理列表↔流程图分屏。
    wf_main = tkinter.Frame(tab_workflow, bg=C["bg"])
    wf_main.grid(row=1, column=0, sticky="nsew", padx=6, pady=(0, 4))
    wf_main.columnconfigure(0, weight=1)
    wf_main.rowconfigure(0, weight=1)

    wf_outer_paned = tkinter.PanedWindow(wf_main, orient="horizontal",
        bg=C["bd"], sashwidth=sp("sash"), sashrelief="raised", opaqueresize=True, bd=0)
    wf_outer_paned.grid(row=0, column=0, sticky="nsew")

    # ── 左侧: 操作库 (树形分类, 双击/拖拽添加) ──
    #   宽度由 PanedWindow 的 minsize/width 管理 (默认 180, 最小 120, 可拖拽拉伸)。
    wf_lib_frame = tkinter.Frame(wf_outer_paned, bg=C["bgc"],
        highlightbackground=C["bd"], highlightthickness=1)
    wf_lib_frame.columnconfigure(0, weight=1)
    wf_lib_frame.rowconfigure(2, weight=1)

    # 操作库标题 (fgt 强对比, 登记 fg token 以免换肤降级为 fgm)
    _wf_lib_title = tkinter.Label(wf_lib_frame, text="⊞ 操作库", font=FONT_TITLE,
        bg=C["bgc"], fg=C["fgt"])
    _ut_lib = _CTX.get("ui_theme")
    if _ut_lib is not None:
        try:
            _ut_lib.set_fg_token(_wf_lib_title, "fgt")
        except Exception:
            pass
    _wf_lib_title.grid(row=0, column=0, sticky="ew", padx=8, pady=(6, 2))

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
        command=wf_lib_tree.yview, width=sp("sb_w"), relief="flat",
        bg=C["bd"], troughcolor=C["bgc"])
    wf_lib_sy.grid(row=2, column=1, sticky="ns", pady=(0, 2))
    wf_lib_tree.configure(yscrollcommand=wf_lib_sy.set)

    # 最近使用
    wf_recent_label = tkinter.Label(wf_lib_frame, text="最近使用", font=FONT_SMALL,
        bg=C["bgc"], fg=C["fgm"])
    wf_recent_label.grid(row=3, column=0, sticky="w", padx=8, pady=(4, 0))
    wf_recent_list = tkinter.Listbox(wf_lib_frame, font=FONT_SMALL, height=4,
        bg=C["ebg"], fg=C["fgb"], selectbackground=C["acl"], selectforeground=C["fgt"])
    wf_recent_list.grid(row=4, column=0, columnspan=2, sticky="ew", padx=6, pady=(2, 6))
    wf_recent_list.bind("<Double-1>", lambda e: _wf_add_recent_clicked())

    # ── 右侧: 步骤列表 + 流程图 (内层 PanedWindow 分屏) ──
    wf_paned = tkinter.PanedWindow(wf_outer_paned, orient="horizontal",
        bg=C["bd"], sashwidth=sp("sash"), sashrelief="raised", opaqueresize=True, bd=0)

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
        width=sp("sb_w"), relief="flat", bg=C["bd"], troughcolor=C["bgc"])
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
        command=wf_flow_canvas.yview, width=sp("sb_w"), relief="flat",
        bg=C["bd"], troughcolor=C["bgc"])
    wf_flow_canvas.configure(yscrollcommand=wf_flow_scroll_y.set)
    wf_flow_canvas.grid(row=0, column=0, sticky="nsew")
    wf_flow_scroll_y.grid(row=0, column=1, sticky="ns")

    # E 期: 静态分析摘要 (检查面板) —— 画布下方一行, 随每次渲染刷新
    wf_flow_check_lbl = tkinter.Label(wf_flow_frame, text="检查: —", font=FONT_SMALL,
        bg=C["bgc"], fg=C["fgm"], anchor="w", justify="left",
        highlightthickness=0, wraplength=380)
    wf_flow_check_lbl.grid(row=1, column=0, columnspan=2, sticky="ew", padx=6, pady=(2, 4))
    # wraplength 随容器宽度自适应 (操作库拉伸 / sash 拖拽时流程图宽度变化)
    def _wf_check_lbl_resize(event=None):
        try:
            w = wf_flow_frame.winfo_width()
            if w > 20:
                wf_flow_check_lbl.configure(wraplength=max(60, w - 16))
        except Exception:
            pass
    wf_flow_frame.bind("<Configure>", _wf_check_lbl_resize)

    wf_flow_canvas.bind("<MouseWheel>", _wf_flow_on_wheel)

    # P3-3: 节点交互改为「共享 `node` 标签 + 几何命中」一次性注册 (替代渲染期 N×4 tag_bind)。
    #   所有节点/端口图元均带 `node` 标签, 命中 idx 由 _wf_hit_node_index 按包围盒解析;
    #   拖拽移动/结束沿用已登记的 _wf_drag_idx (几何命中仅在按下时解析一次)。
    wf_flow_canvas.tag_bind("node", "<Double-1>", _wf_node_double_at)
    wf_flow_canvas.tag_bind("node", "<Button-1>", _wf_node_click_at)
    wf_flow_canvas.tag_bind("node", "<B1-Motion>", _wf_node_drag_move_at)
    wf_flow_canvas.tag_bind("node", "<ButtonRelease-1>", _wf_node_drag_end_at)

    # WF-B2 (P1-1/P1-2): 检查面板展开列表 + 画布悬浮工具条 + 小地图
    _wf_build_panel_v2()

    # WF-B2 (P1-5/A10): 大纲 → 画布联动 (画布 → 大纲经共享 `node` 标签 _wf_node_click_at)
    try:
        wf_tree.bind("<<TreeviewSelect>>", _wf_on_tree_select)
    except Exception:
        pass

    wf_paned.add(wf_flow_frame, minsize=200, width=400)

    # 将操作库与右侧分屏加入外层 PanedWindow (操作库可拖拽拉伸, 最小 120, 默认 180)
    wf_outer_paned.add(wf_lib_frame, minsize=120, width=180)
    wf_outer_paned.add(wf_paned, minsize=300)

    # 操作库显示名 → 节点构造器 `_WF_LIB_ACTIONS`: S0-04 起提到**模块级**定义
    #   (见 `_WF_LIB_CATEGORIES` 之后的同名赋值) —— 兜底路径与"命令名 ↔ 显示名"反查需要在
    #   build() 之外可用; 内容逐字未改动, build() 不再重复构造。
    wf_tree.bind("<Double-1>", _wf_edit_step)

    wf_tree.bind("<Delete>", lambda e: _wf_delete_step())

    # 撤销 / 重做 (Ctrl+Z / Ctrl+Shift+Z / Ctrl+Y)
    def _wf_key_undo(event):
        _wf_undo()
        return "break"
    def _wf_key_redo(event):
        _wf_redo()
        return "break"
    wf_tree.bind("<Control-z>", _wf_key_undo)
    wf_tree.bind("<Control-Z>", _wf_key_undo)
    wf_tree.bind("<Control-Shift-z>", _wf_key_redo)
    wf_tree.bind("<Control-Shift-Z>", _wf_key_redo)
    wf_tree.bind("<Control-y>", _wf_key_redo)

    wf_tree.bind("<Button-3>", _wf_context_menu)

    # 操作库事件绑定
    wf_lib_tree.bind("<Double-1>", lambda e: _wf_add_from_library())
    wf_lib_tree.bind("<Button-3>", _wf_library_context_menu)
    # S0-04: 悬停 500ms 显示说明 (命令 desc + 参数签名); 复用既有 tooltip 渲染, 不新增依赖
    wf_lib_tree.bind("<Motion>", _wf_lib_on_motion)
    wf_lib_tree.bind("<Leave>", lambda e: _wf_lib_tip_cancel())
    # 初始化: 填充操作库 (数据源 = module_catalog, 失败退硬编码兜底) + 加载最近使用
    _wf_load_recent()
    _wf_populate_library()
    _wf_refresh_recent()

    # 换肤: 订阅 ThemeBus (幂等, 重复构建只登记一次) —— 取代原 _refresh_theme 的定点回填
    if ui_theme is not None:
        ui_theme.subscribe(refresh_theme)

    # 事件总线 (S3): 订阅宿主发布的工作流高亮刷新 (幂等登记; 本模块为懒构建,
    # 未构建时无订阅者, 宿主 publish 即 no-op, 天然取代 _WF_LAZY["ok"] 守卫)
    try:
        from core.event_bus import bus
        bus.subscribe("wf_highlight", _wf_update_workflow_highlight)
    except Exception:
        pass

    return get_widgets()


# ══════════════════════════════════════════════════════════════════════
# 换肤回调 (原 ACRPA._refresh_theme 内工作流具名回填)
# ══════════════════════════════════════════════════════════════════════

def refresh_theme(dark=None, colors=None, prev=None):
    """ThemeBus 订阅回调 (签名与 publish 派发契约一致): 步骤树 tag + 流程图随主题重配。

    · 流程图画布底色 flowbg (原 _walk 内对 wf_flow_canvas 的特判);
    · 步骤树标签颜色 + 重绘流程图 (原 _refresh_theme 末尾 _wf_refresh_tree() 具名回填)。
    · PanedWindow sash 颜色 + 侧边栏 Frame/Label/Entry/Scrollbar/Listbox 配色回填
      (通用 _walk 对 PanedWindow 无分支, 切换暗色后 sash/边框仍为旧主题色)。
    """
    global C
    Cc = colors if isinstance(colors, dict) else _CTX.get("colors")
    if not isinstance(Cc, dict):
        return
    C = Cc
    _CTX["colors"] = Cc
    _refresh_flow_style(Cc)
    try:
        if wf_flow_canvas is not None and "flowbg" in Cc:
            wf_flow_canvas.configure(bg=Cc["flowbg"])
    except Exception:
        pass
    try:
        _wf_refresh_tree()
    except Exception:
        pass

    # ── PanedWindow sash + 容器 Frame 配色回填 ──
    # tk.PanedWindow.winfo_class() == "PanedWindow", 通用 _walk 无此分支 → bg/sash 不刷新。
    _paned_list = []
    try:
        if wf_outer_paned is not None:
            _paned_list.append(wf_outer_paned)
    except Exception:
        pass
    try:
        if wf_paned is not None:
            _paned_list.append(wf_paned)
    except Exception:
        pass
    for _pw in _paned_list:
        try:
            _pw.configure(bg=Cc["bd"], sashrelief="raised")
        except Exception:
            pass

    # ── 侧边栏各 Frame / Label / Entry / Scrollbar / Listbox 配色回填 ──
    _frame_list = []
    for _name in ("wf_main", "wf_lib_frame", "wf_list_frame", "wf_flow_frame"):
        try:
            _f = globals().get(_name)
            if _f is not None:
                _frame_list.append(_f)
        except Exception:
            pass
    for _f in _frame_list:
        try:
            ht = int(_f.cget("highlightthickness"))
            if ht > 0:
                _f.configure(bg=Cc["bgc"], highlightbackground=Cc["bd"])
            else:
                _f.configure(bg=Cc["bg"])
        except Exception:
            pass

    # 操作库内子控件: 搜索框 Entry / 标题 Label / 滚动条 / 最近使用 Listbox
    try:
        if wf_lib_search is not None:
            wf_lib_search.configure(bg=Cc["ebg"], fg=Cc["fgb"],
                insertbackground=Cc["fgt"])
    except Exception:
        pass
    try:
        if wf_lib_sy is not None:
            wf_lib_sy.configure(bg=Cc["bd"], troughcolor=Cc["bgc"],
                activebackground=Cc["fgm"])
    except Exception:
        pass
    try:
        if wf_sy is not None:
            wf_sy.configure(bg=Cc["bd"], troughcolor=Cc["bgc"],
                activebackground=Cc["fgm"])
    except Exception:
        pass
    try:
        if wf_flow_scroll_y is not None:
            wf_flow_scroll_y.configure(bg=Cc["bd"], troughcolor=Cc["bgc"],
                activebackground=Cc["fgm"])
    except Exception:
        pass
    try:
        if wf_recent_list is not None:
            wf_recent_list.configure(bg=Cc["ebg"], fg=Cc["fgb"],
                selectbackground=Cc["acl"], selectforeground=Cc["fgt"])
    except Exception:
        pass
    try:
        if wf_flow_check_lbl is not None:
            wf_flow_check_lbl.configure(bg=Cc["bgc"], fg=Cc["fgm"])
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
    # 按钮文案跟随状态 (此前恒为「[+] 流程图」, 用户无法判断当前是显示还是隐藏)
    try:
        _wf_view_btn.configure(text="[-] 流程图" if _wf_show_flow.get() else "[+] 流程图")
    except Exception:
        pass


# 流程图滚轮 - 使用widget级别绑定
def _wf_flow_on_wheel(event):
    wf_flow_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
    _wf_draw_minimap()          # P1-2: 视口框跟随滚动刷新


# ── 操作库功能 ──

def _wf_insert_step(step, label=None):
    """将步骤插入到当前选中位置之后 (无选中则追加末尾)。

    label: 操作库显示名 (用于最近使用记录, 保证与 _WF_LIB_ACTIONS 键一致)。
    """
    import copy
    sel = wf_tree.selection()
    _wf_snapshot_undo()
    if sel:
        idx = wf_tree.index(sel[0])
        _wf_data["steps"].insert(idx + 1, copy.deepcopy(step))
    else:
        _wf_data["steps"].append(copy.deepcopy(step))
    _wf_refresh_tree()
    _wf_add_recent(step, label)


def _wf_lib_color(key, default=None):
    """取当前主题色 (缺失/未绑定主题时给 default, 保证离屏环境不炸)。"""
    try:
        return (C or {}).get(key) or default
    except Exception:
        return default


def _wf_lib_icon_default():
    """兜底分组图标 (Unicode 单字符; 与 module_catalog.FALLBACK_ICON 对齐)。"""
    if _module_catalog is not None:
        try:
            return getattr(_module_catalog, "FALLBACK_ICON", None) or "▫"
        except Exception:
            pass
    return "▫"


def _wf_lib_tag_cfg(widget, tag, **kw):
    """一次性配置 Treeview tag (None/空串选项丢弃, 避免 ttk 报错)。"""
    opts = dict((k, v) for k, v in kw.items() if v is not None and v != "")
    if not opts:
        return
    try:
        widget.tag_configure(tag, **opts)
    except Exception:
        pass


def _wf_lib_leaf_count(catalog):
    """目录中的命令叶子数 (兜底提示与测试断言共用)。"""
    n = 0
    for g in (catalog or ()):
        if isinstance(g, dict):
            for it in (g.get("items") or ()):
                if isinstance(it, dict):
                    n += 1
    return n


def _wf_lib_legacy_catalog(reason=""):
    """按 `_WF_LIB_CATEGORIES` 构造**兜底目录** (8 分类 45 动作, 与 Catalog 同形)。

    S0-04: `module_catalog` 不可用、或分组 json 缺失/损坏时使用 —— 结构与
    `module_catalog.load_fallback_catalog()` 等价 (普通 list 即可: 遍历/检索函数对
    纯 list 同样适用), 保证模块库永远建得出树、工作流 Tab 永远打得开。
    """
    icon = _wf_lib_icon_default()
    groups = []
    for idx, (title, actions) in enumerate(_WF_LIB_CATEGORIES):
        items = []
        for act in actions:
            items.append({
                "cmd": act, "desc": "", "sig": "", "params": [], "params_text": "",
                "group": title, "sub": "", "has_schema": False, "has_params": False,
                "variadic": False, "fallback": True,
            })
        groups.append({"group": title, "icon": icon, "tier": "core",
                       "order": idx + 1, "subgroups": [], "items": items})
    return groups


def _wf_lib_notice(reason, leaves):
    """降级提示: 记日志 (`log1`) + 记状态字符串; **只提示一次**, 绝不弹模态框。

    返回值即状态文本, 便于调用方/测试读取 (`_WF_LIB_STATUS` 同值)。
    """
    global _WF_LIB_DEGRADED, _WF_LIB_STATUS
    _WF_LIB_STATUS = "模块库已降级: {} (展示 {} 条内置动作)".format(
        reason or "数据源不可用", leaves)
    if not _WF_LIB_DEGRADED:
        _WF_LIB_DEGRADED = True
        _log1(_WF_LIB_STATUS, "WARN")
    return _WF_LIB_STATUS


def _wf_lib_use_legacy(reason):
    """切到硬编码兜底目录 + 一次降级提示 (内部用)。"""
    cat = _wf_lib_legacy_catalog(reason)
    _wf_lib_notice(reason, _wf_lib_leaf_count(cat))
    return cat


def _wf_library_catalog(groups_json=None, schema_json=None, force_reload=False):
    """取模块库数据源 → 目录 (11 组 88 命令, 或兜底 8 组 45 动作)。

    · 正常: `module_catalog.load_catalog()` (分组 json 顺序/`order` + schema 描述签名);
    · 失败开放: json 缺失/损坏/结构非法 → `_WF_LIB_CATEGORIES` 硬编码快照 + 一次性降级提示
      (日志 + `_WF_LIB_STATUS`), 不抛异常、不让工作流 Tab 打不开;
    · `groups_json` / `schema_json` 可注入 (冒烟测试指到不存在路径验证兜底, 不改仓库 json)。
    """
    global _WF_LIB_CATALOG
    cacheable = groups_json is None and schema_json is None
    if cacheable and _WF_LIB_CATALOG is not None and not force_reload:
        return _WF_LIB_CATALOG
    if _module_catalog is None:
        return _wf_lib_use_legacy("module_catalog 不可用")
    try:
        cat = _module_catalog.load_catalog(groups_json, schema_json)
    except Exception as exc:                      # 防御: load_catalog 理论上不抛
        return _wf_lib_use_legacy("module_catalog 异常: {}".format(exc))
    if getattr(cat, "failed", False) or not len(cat):
        return _wf_lib_use_legacy(getattr(cat, "reason", "") or "模块库数据源加载失败")
    if not getattr(cat, "ok", True):
        # 仅 command_schema.json 缺失/损坏: 分组与 88 条命令照出, 只是描述/签名退化
        _wf_lib_notice(getattr(cat, "reason", "") or "参数说明缺失",
                       _wf_lib_leaf_count(cat))
    if cacheable:
        _WF_LIB_CATALOG = cat
    return cat


def _wf_lib_search_hits(keyword, catalog):
    """关键字检索 → `[{"group","cmd","field","snippet"}]` (统一形状)。

    优先 `module_catalog.search()` (命中命令名/参数名/参数说明/分组名, 大小写不敏感);
    module_catalog 不可用时退回"仅命令名子串"的本地极简检索 (兜底路径不裸奔)。
    """
    kw = (keyword or "").strip()
    if not kw:
        return []
    if _module_catalog is not None:
        try:
            return [{"group": getattr(h, "group", ""), "cmd": getattr(h, "cmd", ""),
                     "field": getattr(h, "field", ""), "snippet": getattr(h, "snippet", "")}
                    for h in _module_catalog.search(kw, catalog)]
        except Exception:
            pass
    low = kw.lower()
    hits = []
    for g in (catalog or ()):
        if not isinstance(g, dict):
            continue
        title = g.get("group", "")
        for it in (g.get("items") or ()):
            if not isinstance(it, dict):
                continue
            cmd = str(it.get("cmd") or "")
            if low in cmd.lower():
                hits.append({"group": title, "cmd": cmd, "field": "cmd", "snippet": cmd})
    return hits


def _wf_lib_group_text(group):
    """分组行文本 (`◈ 基础操作`; 优先 `module_catalog.group_label`)。"""
    if _module_catalog is not None:
        try:
            return _module_catalog.group_label(group)
        except Exception:
            pass
    if isinstance(group, dict):
        return "{} {}".format(group.get("icon") or _wf_lib_icon_default(),
                              group.get("group") or "")
    return str(group or "")


def _wf_lib_cmd_icon(item):
    """命令行图标: 命令自带 icon 优先, 否则沿用所属分组图标 (无分组用 ▸)。"""
    if not isinstance(item, dict):
        return "▸"
    icon = str(item.get("icon") or "").strip()
    if icon:
        return icon
    return _WF_LIB_GROUP_ICONS.get(item.get("group")) or "▸"


def _wf_lib_node_text(item):
    """命令行文本: `◈ 命令名  (关键参数1、参数2…)`。

    与「描述」分离的口径: 行内只给**命令名 + 前 2 个关键字段**, 完整签名
    (含默认值/枚举) 与 description 一律留在悬停说明 (`_wf_lib_tooltip_text`) 里,
    避免 180px 侧栏被长参数串撑爆。无结构化参数时退回签名前两项。
    """
    if not isinstance(item, dict):
        return str(item or "")
    cmd = str(item.get("cmd") or "")
    text = "{} {}".format(_wf_lib_cmd_icon(item), cmd)
    if not item.get("has_params"):
        return text
    names = []
    for p in (item.get("params") or ()):
        if isinstance(p, dict):
            nm = str(p.get("name") or "").strip()
            if nm:
                names.append(nm)
    if names:
        return "{}  ({}{})".format(text, "、".join(names[:2]),
                                   "…" if len(names) > 2 else "")
    sig = str(item.get("sig") or "")
    heads = [s.strip() for s in sig.split(",")[:2] if s.strip()]
    if heads:
        return "{}  ({}{})".format(text, ", ".join(heads),
                                   "…" if sig.count(",") > 1 else "")
    return text


def _wf_lib_tooltip_text(item):
    """悬停说明文本: description + 参数签名 (优先 `module_catalog.tooltip_text`)。"""
    if _module_catalog is not None:
        try:
            return _module_catalog.tooltip_text(item)
        except Exception:
            pass
    if not isinstance(item, dict):
        return ""
    lines = []
    desc = str(item.get("desc") or "").strip()
    if desc:
        lines.append(desc)
    sig = str(item.get("sig") or item.get("params_text") or "").strip()
    if sig and item.get("has_params"):
        lines.append("参数: {}".format(sig))
    return "\n".join(lines)


def _wf_populate_library(filter_text="", groups_json=None, schema_json=None,
                         force_reload=False):
    """填充操作库分类树 (数据源: `ui.module_catalog`; 支持搜索过滤)。

    结构 (S0-04):
      一级 = 分组 —— 文本取 `group_label()` (Unicode icon + 组名), iid = `"g|{分组名}"`,
             分组间顺序由分组 json 的 `order` 决定 (module_catalog 已排);
      二级 = 命令 —— 文本取 `node_label()` (命令名 + 参数签名), iid = `"{分组名}|{命令名}"`,
             命令名含半角空格 (如 `HTTP 请求`) 也精确成立, 供双击/右键反查;
      子族恒空 (数据层无 subgroups 声明) → **不凭空造空节点**, 树实际退化为两级。

    搜索: 走 `module_catalog.search()`, 只保留命中分支 (整组无命中即不出现), 命中行打
          `wf_lib_hit` 标签; 命中字段不是命令名时把命中片段拼进行文本, 保证"命中可见"。

    `groups_json` / `schema_json` / `force_reload`: 测试注入用 (指到坏路径验证兜底)。
    :return: 本次插入的命令叶子数
    """
    global _WF_LIB_NODES
    tree = wf_lib_tree
    if tree is None:
        return 0
    try:
        tree.delete(*tree.get_children())
    except Exception:
        return 0
    _WF_LIB_NODES = {}
    catalog = _wf_library_catalog(groups_json, schema_json, force_reload)
    # 分组图标快照: 命令行沿用所属分组图标 (schema 无逐命令 icon 时的低成本方案)
    try:
        _WF_LIB_GROUP_ICONS.clear()
        for _g in (catalog or ()):
            if isinstance(_g, dict):
                _WF_LIB_GROUP_ICONS[str(_g.get("group") or "")] = str(
                    _g.get("icon") or "▫")
    except Exception:
        pass
    kw = (filter_text or "").strip()
    hit_map = {}
    for h in _wf_lib_search_hits(kw, catalog):
        hit_map[(h.get("group", ""), h.get("cmd", ""))] = h
    # tag 一次性配置 (旧实现逐行重复 tag_configure 属无效开销, 见 §6.1.5)
    _wf_lib_tag_cfg(tree, "wf_lib_group", foreground=_wf_lib_color("fgt"),
                    font=FONT_SMALL_BOLD or FONT_SMALL)
    _wf_lib_tag_cfg(tree, "wf_lib_cmd", foreground=_wf_lib_color("fgb"))
    if kw:
        _wf_lib_tag_cfg(tree, "wf_lib_hit", foreground=_wf_lib_color("ac", "blue"))
    leaves = 0
    for g in (catalog or ()):
        if not isinstance(g, dict):
            continue
        title = g.get("group", "")
        items = [it for it in (g.get("items") or ()) if isinstance(it, dict)]
        if kw:
            items = [it for it in items if (title, it.get("cmd", "")) in hit_map]
            if not items:
                continue
        gid = "g|{}".format(title)
        try:
            tree.insert("", "end", iid=gid, text=_wf_lib_group_text(g), open=True,
                        tags=("wf_lib_group",))
        except Exception:
            continue
        for it in items:
            cmd = str(it.get("cmd") or "")
            iid = "{}|{}".format(title, cmd)
            text = "  " + _wf_lib_node_text(it)
            tags = ["wf_lib_cmd"]
            hit = hit_map.get((title, cmd)) if kw else None
            if hit is not None:
                tags.append("wf_lib_hit")
                field = hit.get("field") or ""
                if field and field != "cmd":
                    text = "{}   [{}] {}".format(
                        text, _WF_LIB_FIELD_LABELS.get(field, field),
                        hit.get("snippet") or "")
            try:
                tree.insert(gid, "end", iid=iid, text=text, tags=tuple(tags))
            except Exception:
                continue
            _WF_LIB_NODES[iid] = it
            leaves += 1
    # 展开根 (搜索态同样全展开; 折叠状态持久化属 S4-01, 本卡不做)
    for cid in tree.get_children():
        try:
            tree.item(cid, open=True)
        except Exception:
            pass
    return leaves


def _wf_filter_library():
    """根据搜索框文本过滤操作库 (命中分支 + 命中片段, 由 `module_catalog.search` 驱动)。"""
    txt = ""
    try:
        if wf_lib_search is not None:
            txt = wf_lib_search.get()
    except Exception:
        txt = ""
    _wf_populate_library(txt)


def _wf_library_action_name(iid):
    """从操作库节点反查名称 (命令名 / 兜底动作显示名)。

    S0-04 起 iid 改为 `"{分组}|{命令名}"` 且行文本带参数签名, 故**优先**查
    `_WF_LIB_NODES` 反查表; 表里没有 (异常场景) 才退回旧的"去前缀文本"口径。
    """
    item = _WF_LIB_NODES.get(iid)
    if isinstance(item, dict) and item.get("cmd"):
        return str(item["cmd"])
    try:
        return wf_lib_tree.item(iid, "text").strip()
    except Exception:
        return ""


def _wf_lib_maker_by_cmd():
    """「命令名 → 旧构造器」反向索引 (惰性构建, 一次性)。

    用途: 老动作的**显示名**与**命令名**并不总是同名 (`模拟按键` → `按键`, `屏幕截图` →
    `截屏`), 命令库改数据源后树里出现的是命令名, 靠本索引仍能命中旧构造器, 从而保住
    "老动作插出的步骤类型/参数字面量与改造前逐字段相同"。
    歧义命令名 (一个命令名被多个旧动作产出, 如 `代码` ← `打开程序`/`浏览文件`) **不登记**,
    交由通用构造器产出干净的空参数节点。
    """
    global _WF_LIB_MAKER_BY_CMD
    if _WF_LIB_MAKER_BY_CMD is not None:
        return _WF_LIB_MAKER_BY_CMD
    by_cmd, dup = {}, set()
    for name, maker in (_WF_LIB_ACTIONS or {}).items():
        try:
            step = maker()
        except Exception:
            continue
        if not isinstance(step, dict) or step.get("type") != "command":
            continue
        cmd = str(step.get("cmd") or "")
        if not cmd or cmd == name:
            continue                     # 同名情形已由"显示名直查"覆盖
        if cmd in by_cmd:
            dup.add(cmd)
        else:
            by_cmd[cmd] = maker
    for cmd in dup:
        by_cmd.pop(cmd, None)
    _WF_LIB_MAKER_BY_CMD = by_cmd
    return by_cmd


def _wf_library_make_step_by_name(name):
    """名称 → 步骤 dict (**双路径**)。

    ① 显示名/同名命令命中 `_WF_LIB_ACTIONS` → 走旧构造器 (行为与改造前逐字段一致);
    ② 未命中但命令名由某个旧动作产出 (唯一) → 仍走旧构造器 (保住默认参数值);
    ③ 其余 (新增的 L2 命令等) → **通用路径**: `{"type":"command","cmd":<命令名>,
       "params":[""]*9}` —— 9 槽空串, 参数由右栏/`param_form` 后续填写, 执行层按
       `command` + `cmd` 识别 (参数说明取自 schema, 见悬停说明)。
    """
    name = str(name or "").strip()
    if not name:
        return None
    maker = (_WF_LIB_ACTIONS or {}).get(name)
    if maker is None:
        maker = _wf_lib_maker_by_cmd().get(name)
    if maker is not None:
        try:
            step = maker()
            if isinstance(step, dict):
                return step
        except Exception:
            pass
    return {"type": "command", "cmd": name, "params": [""] * 9}


def _wf_lib_insert_by_name(name):
    """按名称构造节点并插入 (双击 / 右键 / 最近使用共用)。"""
    step = _wf_library_make_step_by_name(name)
    if step is None:
        return
    _wf_insert_step(step, label=name)


def _wf_add_from_library():
    """操作库双击: 添加节点到工作流 (覆盖全部 88 条命令)。"""
    try:
        sel = wf_lib_tree.selection()
    except Exception:
        return
    if not sel:
        return
    iid = sel[0]
    # 忽略分组节点 (有子节点)
    if wf_lib_tree.get_children(iid):
        return
    action = _wf_library_action_name(iid)
    if not action:
        return
    _wf_lib_insert_by_name(action)
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
    # 高亮条 acl+fgt: ac+white 在暗色下为浅底白字, 不可读
    menu = tkinter.Menu(_root(), tearoff=0, bg=_wf_lib_color("bgc", "white"),
        fg=_wf_lib_color("fgt", "black"), activebackground=_wf_lib_color("acl", "#CCE4F7"),
        activeforeground=_wf_lib_color("fgt", "black"))
    menu.add_command(label="添加到末尾",
        command=lambda: _wf_lib_insert_by_name(action))
    try:
        menu.tk_popup(event.x_root, event.y_root)
    finally:
        menu.grab_release()


class _WfLibTipEvent(object):
    """伪事件: 只暴露 `utils._show_tooltip` 需要的 `widget` / `x_root` / `y_root`。

    用于 500ms 惰性悬停 —— 复用项目既有 tooltip 渲染 (浅黄底 + `tooltip_border` token),
    不新建第二套提示窗口、不引第三方依赖。
    """
    __slots__ = ("widget", "x_root", "y_root")

    def __init__(self, widget, x_root, y_root):
        self.widget = widget
        self.x_root = x_root
        self.y_root = y_root


def _wf_lib_tip_cancel():
    """取消挂起的悬停计时并关闭提示窗口。"""
    global _WF_LIB_TIP_TIMER, _WF_LIB_TIP_IID
    if _WF_LIB_TIP_TIMER is not None:
        try:
            wf_lib_tree.after_cancel(_WF_LIB_TIP_TIMER)
        except Exception:
            pass
        _WF_LIB_TIP_TIMER = None
    _WF_LIB_TIP_IID = None
    try:
        import utils as _u
        hide = getattr(_u, "_hide_tooltip", None)
        if callable(hide):
            hide()
    except Exception:
        pass


def _wf_lib_tip_show(x_root, y_root, iid):
    """(500ms 到点) 显示悬停说明: 命令 desc + 参数签名。"""
    global _WF_LIB_TIP_TIMER
    _WF_LIB_TIP_TIMER = None
    if iid != _WF_LIB_TIP_IID:
        return
    txt = _wf_lib_tooltip_text(_WF_LIB_NODES.get(iid))
    if not txt:
        return
    try:
        import utils as _u
        show = getattr(_u, "_show_tooltip", None)
        if callable(show):
            show(_WfLibTipEvent(wf_lib_tree, x_root, y_root), txt)
    except Exception:
        pass


def _wf_lib_on_motion(event):
    """鼠标在模块库上移动: 行变化才重启 500ms 计时 (同一行不重复弹)。"""
    global _WF_LIB_TIP_TIMER, _WF_LIB_TIP_IID
    try:
        iid = wf_lib_tree.identify_row(event.y)
    except Exception:
        return
    if iid == _WF_LIB_TIP_IID:
        return
    _wf_lib_tip_cancel()
    if not iid or iid not in _WF_LIB_NODES:
        return
    _WF_LIB_TIP_IID = iid
    try:
        _WF_LIB_TIP_TIMER = wf_lib_tree.after(
            500, lambda: _wf_lib_tip_show(event.x_root, event.y_root, iid))
    except Exception:
        _WF_LIB_TIP_TIMER = None


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
    """点击最近使用项: 添加对应节点。

    S0-04: 记录里的名称可能是**旧显示名** (`模拟按键`) 也可能是**新命令名** (`复制文件`),
    统一走 `_wf_library_make_step_by_name()` 的双路径解析 (旧构造器 / 通用 command 节点),
    故 `recent_workflow.json` 结构无需迁移。
    """
    sel = wf_recent_list.curselection()
    if not sel:
        return
    label = wf_recent_list.get(sel[0])
    _wf_lib_insert_by_name(label)


def _wf_render_flowchart(*args, dirty=None):
    """在 Canvas 上绘制工作流流程图。

    C 期 (路线图 阶段二 第 9 项): 内部改为调用 `ui.flow_canvas.render(...)` ——
    Sugiyama 简化版分层布局 + 形状语义 + 结构化两行文本。函数名与 `_draw_arrow`
    符号保留 (tools/_test_workflow_view_split.py 断言), 并提供回退开关 `use_flow_canvas`:
    关闭或渲染异常时退回旧单列渲染 `_wf_render_flowchart_legacy`。

    P2-7: ``dirty`` 透传 flow_canvas.render 的脏矩形局部重绘 (执行高亮仅外观变化时
    只重画受影响节点+连线, 不整体重绘)。
    """
    if use_flow_canvas and _flow_canvas is not None and _flow_graph is not None:
        try:
            return _wf_render_flow_canvas(dirty=dirty)
        except Exception:
            pass  # 渲染失败 → 旧渲染兜底, 保证画布不空
    return _wf_render_flowchart_legacy(*args)


def _wf_flow_ctx(g):
    """构建 flow_canvas 渲染/动画上下文 (P2-2 抽离, 渲染与高亮动画同口径)。

    顶层节点 ↔ 原始 steps 序号 tag 映射 + 执行高亮 (current_step / step_results)
    + 结构色 style 全部在此读取一次, 保证 render() 与 animate_nodes_outline()
    计算出的目标描边逐位一致。
    """
    roots = g.roots()
    index_tags = {n.id: "node_{}".format(i) for i, n in enumerate(roots)}

    cur, results = None, {}
    try:
        from workflow import workflow_engine
        cur = workflow_engine.current_step
        results = dict(getattr(workflow_engine, "_step_results", {}) or {})
    except Exception:
        cur, results = None, {}

    state = _CTX.get("state")
    style = _FLOW_STYLE or _refresh_flow_style(C)
    return {
        "darken": _CTX.get("darken"),
        "dark": bool(state is not None and getattr(state, "DARK_MODE", False)),
        "current_step": cur,
        "step_results": results,
        # 结构色经 style 下沉 (P0-3); 下列 fgm/sc/dg/ac 为 flow_canvas 的次选
        "fgm": style.get("edge"),
        "sc": style.get("walked"),
        "dg": style.get("edge_reject"),
        "type_labels": TYPE_LABELS,
        "icons": _FLOW_ICONS,
        "index_tags": index_tags,
        "show_ports": True,
        "ac": style.get("edge_sel"),
        # P0-1/P0-3/P0-4: 几何 token / 结构色真源 / 缩放乘子 经 ctx 注入渲染层
        "style": style,
        "sp": _CTX.get("sp"),
        # P1/WF-B1: 交互增强开关 (受 _WF_INTERACT_V2 保护, 便于回滚) + 网格开关
        "interact_v2": _WF_INTERACT_V2,
        "grid": bool(_WF_INTERACT_V2 and _WF_FLOW_GRID),
    }


# ── P3-3 几何命中 (节点单击/双击/拖拽统一按包围盒解析, 替代逐节点 tag_bind) ──
_WF_NODE_REGIONS = None
_WF_NODE_HIT_PAD = 2.0


def _wf_set_node_regions(regions, pad=2.0):
    """渲染后灌入节点包围盒 ``[(idx, (x1, y1, x2, y2)), ...]`` (绘制顺序)。

    idx 与 ``roots`` 枚举顺序同源; 单击/双击按 ``detect_point`` 解析 idx,
    拖拽移动/结束沿用已登记的 ``_wf_drag_idx`` (视觉位移中图坐标不变, 无需重解析)。
    """
    global _WF_NODE_REGIONS, _WF_NODE_HIT_PAD
    _WF_NODE_REGIONS = list(regions)
    try:
        _WF_NODE_HIT_PAD = float(pad)
    except Exception:
        _WF_NODE_HIT_PAD = 2.0


def _wf_hit_node_index(x, y):
    """→ 光标下节点 idx 或 None (后绘制者优先, 语义对齐旧 node_i tag_bind)。"""
    if not _WF_NODE_REGIONS:
        return None
    try:
        return detect_point(float(x), float(y), _WF_NODE_REGIONS, _WF_NODE_HIT_PAD)
    except Exception:
        return None


def _wf_node_hit_pad():
    """P3-3: 节点点击命中外扩 = 端口半径 sp(4)*zoom + 2 (覆盖端口圆点, 对齐旧 tag_bind)。"""
    try:
        sp = _CTX.get("sp")
        r = float(sp(4)) if callable(sp) else 4.0
        return max(2.0, r * _wf_current_zoom() + 2.0)
    except Exception:
        return 2.0


def _wf_render_flow_canvas(graph=None, dirty=None):
    """`_wf_data` (树) → promote 成图 → 交由 ui.flow_canvas 分层渲染。

    配色仍取本模块唯一真源 `_FLOW_COLORS`; 缩放/平移由 flow_canvas.bind_zoom_pan 挂载
    (垂直滚轮语义仍属 `_wf_flow_on_wheel`, 不删原绑定)。

    D 期: 支持 `graph` 参数复用「已编辑的图」(端口连线保留) —— 结构未变时
    (`demote(编辑图) == _wf_data`) 复用会话内最后一次编辑的图, 结构变化自动重新提升。

    P2-7: ``dirty`` 为节点 id 集合时走脏矩形局部重绘 (供执行高亮); None 保持全量。
    """
    global _FLOW_ZOOM_STATE, _wf_graph_edit, _FLOW_INTERACT_ST
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
    ctx = _wf_flow_ctx(g)
    fonts = {"body": FONT_BODY, "small": FONT_SMALL, "small_bold": FONT_SMALL_BOLD}

    # 缩放/平移: 仅登记一次 (重复渲染不重复绑定); 回调本次重绘以补偿线宽/字号 (P0-4)
    if not _FLOW_ZOOM_STATE:
        try:
            _FLOW_ZOOM_STATE = _flow_canvas.bind_zoom_pan(
                wf_flow_canvas, re_render=_wf_render_flow_canvas)
        except Exception:
            _FLOW_ZOOM_STATE = {}
    # 当前 zoom 乘子: 由状态取出并透传 render (布局坐标不变; 无状态时 1.0)
    zoom = float(_FLOW_ZOOM_STATE.get("scale", 1.0)) if _FLOW_ZOOM_STATE else 1.0
    ctx["zoom"] = zoom
    _flow_canvas.render(wf_flow_canvas, g, colors=_FLOW_COLORS, fonts=fonts,
                        ctx=ctx, zoom=zoom, dirty=dirty)

    # D 期连线交互 (端口拖拽建边 / 边选中·Delete 删除 / F 适应窗口 / Ctrl+0 重置
    #   缩放 / Ctrl+Z·Ctrl+Y 撤销重做)。图编辑为视图级 (§11.4), 执行仍走树。
    #   P1-2: on_fit 走 zoom-aware 适应窗口 (F 键); 返回值登记供 P1-5 复用选中集合。
    try:
        _FLOW_INTERACT_ST = _flow_canvas.bind_interactions(
            wf_flow_canvas, g, colors=_FLOW_COLORS, fonts=fonts, ctx=ctx,
            re_render=_wf_render_flow_canvas, get_zoom_state=lambda: _FLOW_ZOOM_STATE,
            toast=_toast,                 # N4: 拒绝反馈经宿主 _toast (未注入 show_toast 静默)
            on_fit=_wf_flow_fit)
    except Exception:
        _FLOW_INTERACT_ST = None

    # E 期静态分析摘要 + P1-1 检查面板: 与图同源 (analyze 每渲染仅调用一次)
    try:
        issues = _flow_graph.analyze(g)
    except Exception:
        issues = []
    try:
        _wf_refresh_check_panel(issues)
    except Exception:
        pass

    # P3-3: 节点单击/双击/拖拽绑定已在 build 时一次性挂到共享 `node` 标签; 此处仅更新
    #   节点包围盒 (几何命中真源)。pad 覆盖端口圆点, 保持「点端口也先进入节点单击」的旧
    #   node_i tag_bind 语义 (画布 → 大纲联动 + 双击编辑 + 拖拽换序均不回退)。
    _wf_set_node_regions(
        [(i, (n.x, n.y, n.x + n.w, n.y + n.h)) for i, n in enumerate(roots)],
        pad=_wf_node_hit_pad())

    # P1-2: 小地图随本次渲染刷新 (数据源 layout() 已写回的 n.x/y/w/h)
    _wf_draw_minimap()


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
        if disabled:                       # P0-2: 保留类型色, 混色近似 40% 透明
            color = _flow_disabled_fill(color)
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
            fill=(_flow_canvas.text_color_on(color) if _flow_canvas is not None else "white"),
            tags=("node", "node_{}".format(i)))

        # 节点阴影效果 (深色半透明 — 根据主题切换颜色)
        state = _CTX.get("state")
        shadow_color = _FLOW_STYLE.get("shadow") or (
            "#4A4A4A" if (state is not None and state.DARK_MODE) else "#E1E1E1")
        shadow_id = wf_flow_canvas.create_rectangle(
            x + 2, y + 2, x + _NODE_W + 2, y + _NODE_H + 2,
            fill=shadow_color, outline="", tags=("shadow",))
        wf_flow_canvas.tag_lower(shadow_id, node_id)

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

    # P3-3: 单列回退路径同样走共享 `node` 标签 + 几何命中 (绑定已在 build 时一次性注册),
    #   此处仅更新节点包围盒。单列无端口 → pad 取 2 (与旧 node_i tag_bind 覆盖一致)。
    _wf_set_node_regions(
        [(i, (x, y, x + _NODE_W, y + _NODE_H)) for i, (x, y) in enumerate(node_positions)],
        pad=2.0)


def _draw_arrow(canvas, x1, y1, x2, y2, color=None):
    """绘制带箭头的连线 (color 缺省取当前主题 fgm, 不写死浅灰)。"""
    if not color:
        color = (C or {}).get("fgm", "#94A3B8")
    canvas.create_line(x1, y1, x2, y2 - 6, fill=color, width=2, tags="arrow")
    # 箭头三角形
    canvas.create_polygon(
        x2 - 5, y2 - 6, x2 + 5, y2 - 6, x2, y2,
        fill=color, outline=color, tags="arrow")


def _wf_btn(parent, text, role, command=None, font=None, **kw):
    """语义按钮工厂 (对话框用): 建时登记 ui_theme 角色, 换肤按角色回填。

    旧实现各分支直接 `tkinter.Button(..., bg=C["ac"], fg="white")` 且未登记角色,
    主题切换后会被 _walk 的「无角色」分支刷成中性灰 (按钮失去语义色)。
    """
    _C = C or {}
    _bg = _C.get(role, "#3B82F6")
    _abg = _C.get("ach", _bg)
    _ut = _CTX.get("ui_theme")
    # 语义底色的明度跨主题翻转 → 字色建窗时即按亮度自适应 (避免浅底白字)
    _fg = _ut.text_on(_bg) if _ut is not None else "white"
    _afg = _ut.text_on(_abg) if _ut is not None else "white"
    b = tkinter.Button(parent, text=text,
                       font=font if font is not None else FONT_BUTTON,
                       bg=_bg, fg=_fg, command=command,
                       activebackground=_abg,
                       activeforeground=_afg,
                       disabledforeground=_C.get("fgm", "#888888"),
                       relief="flat", bd=1, cursor="hand2",
                       padx=kw.pop("padx", 12), pady=kw.pop("pady", 3),
                       **kw)
    if _ut is not None:
        try:
            _ut.set_role(b, role)
        except Exception:
            pass
    return b


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


def _wf_alt_down(event):
    """P1-7/N3: 是否按住 Alt (跨平台掩码: X11 Mod1=0x08 / Windows=0x20000)。"""
    state = getattr(event, "state", 0) or 0
    return bool(state & 0x0008) or bool(state & 0x20000)


def _wf_current_zoom():
    """P1 收尾: 当前渲染期 zoom 乘子 (无缩放状态时 1.0)。

    与 flow_canvas.render 的几何口径同源 —— 布局几何 (节点/间距/margin) 按 zoom
    等比缩放; 故 snap 网格与拖拽落点映射在 zoom!=1.0 下与画布几何保持一致。
    """
    st = _FLOW_ZOOM_STATE
    if isinstance(st, dict):
        try:
            z = float(st.get("scale", 1.0))
            return z if z > 0 else 1.0
        except Exception:
            return 1.0
    return 1.0


def _wf_snap_coord(value, event=None):
    """P1-7: 拖拽坐标吸附网格; 按住 Alt 临时关闭吸附 (N3)。

    P1 收尾: 吸附格 = sp(8) * zoom (与布局几何同口径等比缩放) —— zoom==1.0 时仍为
    sp(8) (与 P0 基线逐位一致), zoom!=1.0 时随几何一起放大; Alt 关闭语义不变。
    """
    if event is not None and _wf_alt_down(event):
        return value
    sp = _CTX.get("sp")
    if not callable(sp):
        return value
    try:
        step = float(sp(8)) * _wf_current_zoom()
    except Exception:
        return value
    if step <= 0:
        return value
    try:
        return round(float(value) / step) * step
    except Exception:
        return value


def _wf_draw_drag_indicator(event):
    """P1-4/N6 插入指示线: ac 3px 水平线, 复用同一 item (局部重绘, 不触发全量渲染)。"""
    global _wf_drag_indic
    canvas = wf_flow_canvas
    if canvas is None:
        return
    if _wf_drag_indic is not None:
        try:
            canvas.delete(_wf_drag_indic)
        except Exception:
            pass
        _wf_drag_indic = None
    y = _wf_snap_coord(event.y, event)
    color = (C.get("ac") if isinstance(C, dict) else None) \
        or _FLOW_STYLE.get("edge_sel") or "#0078D4"
    try:
        x1 = float(str(canvas.cget("scrollregion")).split()[2])
    except Exception:
        try:
            x1 = float(canvas.winfo_width())
        except Exception:
            x1 = 600.0
    if x1 <= 1.0:
        x1 = 600.0
    try:
        _wf_drag_indic = canvas.create_line(
            0, y, x1, y, fill=color, width=3, tags=("drag_indicator",))
    except Exception:
        _wf_drag_indic = None


def _wf_drag_move(event, idx):
    """拖拽移动中 (P1-4): 只 move 被拖图元 + 绘制插入指示线, **不**触发全量重绘。"""
    global _wf_drag_idx, _wf_drag_start_y
    if _wf_drag_idx is None or _wf_drag_idx != idx:
        return
    dy = event.y - _wf_drag_start_y
    # 移动节点图形 (局部: find_withtag + move, O(被拖图元))
    for item in wf_flow_canvas.find_withtag("node_{}".format(idx)):
        wf_flow_canvas.move(item, 0, dy)
    _wf_drag_start_y = event.y
    if _WF_INTERACT_V2:
        _wf_draw_drag_indicator(event)

def _wf_drop_index(y, n):
    """拖拽落点 y (画布坐标) → 顶层步骤插入索引 [0, n-1]。

    P1 收尾修正: 原实现用硬编码 ``gap=_NODE_GAP(=56)`` / ``start_y=20`` (旧单列几何),
    与 V2 分层布局实际步距 ``node_h+v_gap`` (zoom!=1.0 时再 ×zoom) 不符 —— 落点
    错位 (zoom==1.0 时中心 138 被误判为 idx2, 应为 idx1; zoom!=1.0 时步距更大, 偏差
    被放大)。现改为按**画布实际几何**判定, 不依赖任何硬编码步距:

      · 优先取图数据中顶层节点 (``_wf_graph_edit.roots()``) 的中心 y —— 其 x/y/w/h
        由 flow_canvas.layout() 写回 (已含 zoom 等比缩放); 拖拽中被拖图元只是画布
        视觉位移、图坐标不变, 故不受干扰; 取与落点 y 最近的节点索引。
      · 无图数据时回退: 按「当前布局步距 node_h+v_gap (含 zoom)」线性映射。

    zoom∈{1.0, 2.0} 下映射均正确 (步距随几何等比缩放, 结果自动跟随)。
    """
    if n <= 1:
        return 0
    centers = []
    g = _wf_graph_edit
    if g is not None:
        try:
            for i, nd in enumerate(g.roots()):
                centers.append((i, float(nd.y) + float(nd.h) / 2.0))
        except Exception:
            centers = []
    if centers:
        best = min(centers, key=lambda c: abs(float(y) - c[1]))[0]
        return max(0, min(n - 1, best))
    # 回退: 无图数据 → 单列分层几何 (节点高 + 间隙, 含 zoom 与 sp 令牌)
    zoom = _wf_current_zoom()
    sp = _CTX.get("sp")
    fc = _flow_canvas
    try:
        tok = (lambda v: float(sp(v))) if callable(sp) else (lambda v: float(v))
        node_h = tok(_NODE_H)
        v_gap = tok(_NODE_GAP)
        margin = tok(getattr(fc, "MARGIN", 28))
    except Exception:
        node_h, v_gap, margin = float(_NODE_H), float(_NODE_GAP), 28.0
    step = (node_h + v_gap) * zoom
    if step <= 0:
        return 0
    start = (margin + node_h / 2.0) * zoom
    try:
        idx = round((float(y) - start) / step)
    except Exception:
        return 0
    return max(0, min(n - 1, idx))


def _wf_drag_end(event, idx):
    """结束拖拽 (P1-4): 先清插入指示线, 再据落点换序并重排 —— 此步才全量重绘。"""
    global _wf_drag_idx, _wf_drag_indic
    if _wf_drag_idx is None:
        return
    if _wf_drag_indic is not None and wf_flow_canvas is not None:
        try:
            wf_flow_canvas.delete(_wf_drag_indic)
        except Exception:
            pass
    _wf_drag_indic = None
    steps = _wf_data["steps"]
    n = len(steps)
    if n < 2:
        _wf_drag_idx = None
        _wf_render_flowchart()
        return

    # 落点 → 新索引 (P1 收尾: 与实际渲染几何一致, 不再依赖硬编码步距)
    y = _wf_snap_coord(event.y, event) if _WF_INTERACT_V2 else event.y
    new_idx = _wf_drop_index(y, n)          # 已按实际几何映射并钳制到 [0, n-1]

    old_idx = _wf_drag_idx
    _wf_drag_idx = None

    if new_idx != old_idx:
        # 交换步骤
        step = steps.pop(old_idx)
        steps.insert(new_idx, step)
        _wf_refresh_tree()

    _wf_render_flowchart()


def _wf_step_param_summary(step):
    """命令步骤详情: `命令  关键参数=值 …` (只显示非空的前 3 项, 名称取自 schema)。"""
    cmd = str(step.get("cmd") or "")
    vals = step.get("params") or []
    names = []
    if _module_catalog is not None:
        try:
            item = _module_catalog.get_command(cmd)
            if isinstance(item, dict):
                names = [str(p.get("name") or "") for p in (item.get("params") or ())
                         if isinstance(p, dict)]
        except Exception:
            names = []
    parts = []
    for i, v in enumerate(vals[:9]):
        sv = "" if v is None else str(v).strip()
        if not sv:
            continue
        nm = names[i] if i < len(names) and names[i] else "参数{}".format(i + 1)
        parts.append("{}={}".format(nm, sv))
        if len(parts) >= 3:
            break
    return "{}  {}".format(cmd, " ".join(parts)) if parts else cmd


def _wf_refresh_tree():
    """刷新工作流步骤列表"""
    wf_tree.delete(*wf_tree.get_children())
    for i, step in enumerate(_wf_data.get("steps", [])):
        stype = step.get("type", "script")
        label = TYPE_LABELS.get(stype, stype)
        disabled = step.get("enabled", True) is False
        comment = step.get("comment", "")
        if stype == "script":
            _p = str(step.get("path") or "")
            detail = os.path.basename(_p) if _p else "(未选择脚本)"
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
            detail = _wf_step_param_summary(step)
        elif stype == "variable":
            detail = "{} = {}".format(step.get("var_name", ""), step.get("var_value", ""))
        elif stype == "log":
            detail = step.get("text", "")
        elif stype == "retry":
            detail = "最多 {} 次, 间隔 {}s, {} 子步骤".format(
                step.get("max_attempts", 3), step.get("delay", 1.0),
                len(step.get("steps", [])))
        elif stype == "workflow":
            _p = str(step.get("path") or "")
            detail = os.path.basename(_p) if _p else "(未选择工作流)"
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
    _wf_emit_context()


def _wf_step_count(steps):
    """递归统计可执行步骤数 (含并行子步骤 / 条件分支 / 循环体), 供运行栏上下文条。"""
    n = 0
    for s in (steps or []):
        if not isinstance(s, dict):
            continue
        n += 1
        t = s.get("type")
        if t in ("parallel", "loop", "retry"):
            n += _wf_step_count(s.get("steps") or [])
        elif t == "condition":
            for k in ("then", "else"):
                v = s.get(k)
                if isinstance(v, dict) and v:
                    n += _wf_step_count([v])
                elif isinstance(v, list):
                    n += _wf_step_count(v)
    return n


def _wf_step_label(s):
    """单行步骤摘要 (版本历史预览用)。"""
    if not isinstance(s, dict):
        return str(s)
    t = s.get("type", "script")
    if t == "script":
        return "script {}".format(s.get("path", ""))
    if t == "command":
        return _wf_step_param_summary(s)
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


def _wf_ai_generate():
    """自然语言 → 工作流图。

    直接复用脚本编辑器同款对话式 AI 面板 (dialogs.open_ai_panel), 仅把
    mode 切到 "workflow"、并注入导入回调, 保证两处「AI 生成」按钮体验一致:
    同一套流式展示 / API Key 检查 / 模型选择 / 错误处理 / 建议提示词。
    """
    try:
        from dialogs import open_ai_panel
    except Exception as e:
        _toast("打开 AI 生成面板失败: {}".format(e), "error")
        return
    open_ai_panel(mode="workflow", on_workflow_import=_wf_ai_import_result)


def _wf_ai_import_result(data, issues):
    """AI 生成的工作流导入当前编辑器 (撤销栈可回退)。"""
    import copy as _copy
    if not isinstance(data, dict) or not data.get("steps"):
        _toast("生成结果缺少步骤, 未导入", "error")
        return
    _wf_snapshot_undo()
    _wf_data["name"] = data.get("name") or _wf_data.get("name") or "未命名工作流"
    _wf_data["steps"] = _copy.deepcopy(data.get("steps", []))
    try:
        wf_name_var.set(_wf_data["name"])
    except Exception:
        pass
    _wf_refresh_tree()
    _toast("已导入 AI 生成的工作流", "success")


def _wf_emit_context():
    """把 (工作流名, 步数) 推给宿主注入的上下文条回调 (运行栏工作流上下文条)。"""
    if _wf_context_hook is None:
        return
    try:
        name = (wf_name_var.get() if wf_name_var is not None
                else _wf_data.get("name", "未命名工作流"))
        _wf_context_hook(name, _wf_step_count(_wf_data.get("steps", [])))
    except Exception:
        pass


def set_context_hook(fn):
    """注入工作流上下文回调: fn(name, step_count) —— 由宿主 ACRPA 调用 (P1-5 后续 / §7-4)。"""
    global _wf_context_hook
    _wf_context_hook = fn


def _wf_signature():
    """工作流内容的稳定签名 (「是否有未保存修改」的判定依据)。

    用内容快照比对代替逐点打脏标: 步骤对话框 / 右键菜单 / 拖拽 / 名称输入等任何
    路径改动 _wf_data 都会被覆盖, 不会出现「改了却没提示保存」的漏标。
    """
    try:
        return json.dumps(_wf_data, sort_keys=True, ensure_ascii=False, default=str)
    except Exception:
        return ""


def _wf_snapshot_undo():
    """在修改前深拷贝当前 _wf_data 入撤销栈 (清空重做栈)。"""
    global _WF_UNDO, _WF_REDO
    import copy
    _WF_UNDO.append(copy.deepcopy(_wf_data))
    if len(_WF_UNDO) > _WF_UNDO_MAX:
        _WF_UNDO.pop(0)
    _WF_REDO = []


def _wf_restore_snapshot(snap):
    """恢复快照到 _wf_data 并刷新 UI (不额外记账, 不触碰 _wf_file)。"""
    import copy
    _wf_data["name"] = snap.get("name", "未命名工作流")
    _wf_data["steps"] = copy.deepcopy(snap.get("steps", []))
    # 名称回写工具栏
    try:
        if wf_name_var is not None:
            wf_name_var.set(_wf_data["name"])
    except Exception:
        pass
    _wf_refresh_tree()


def _wf_undo():
    """撤销上一步修改 (Ctrl+Z)。"""
    global _WF_UNDO, _WF_REDO
    if not _WF_UNDO:
        _toast("没有可撤销的操作", "info")
        return
    import copy
    _WF_REDO.append(copy.deepcopy(_wf_data))
    snap = _WF_UNDO.pop()
    _wf_restore_snapshot(snap)
    _toast("已撤销", "info")


def _wf_redo():
    """重做被撤销的修改 (Ctrl+Shift+Z)。"""
    global _WF_UNDO, _WF_REDO
    if not _WF_REDO:
        _toast("没有可重做的操作", "info")
        return
    import copy
    _WF_UNDO.append(copy.deepcopy(_wf_data))
    snap = _WF_REDO.pop()
    _wf_restore_snapshot(snap)
    _toast("已重做", "info")


def _wf_mark_saved():
    """把当前内容记为「已保存基线」(新建 / 打开 / 保存 / 清空成功后调用)。"""
    global _wf_saved_sig
    _wf_saved_sig = _wf_signature()


def wf_is_modified():
    """当前工作流是否有未保存修改 (退出确认 / 宿主判断用)。"""
    global _wf_saved_sig
    current = _wf_signature()
    if _wf_saved_sig is None:      # 首次调用: 以当前内容为基线 (尚未编辑)
        _wf_saved_sig = current
        return False
    return current != _wf_saved_sig


def get_workflow_name():
    """当前工作流名 (工具栏未构建时退回数据里的名字)。"""
    try:
        if wf_name_var is not None:
            return wf_name_var.get() or "未命名工作流"
    except Exception:
        pass
    return str(_wf_data.get("name") or "未命名工作流")


def _wf_sync_name():
    """把工具栏名称写回 _wf_data (名称也是工作流内容的一部分)。"""
    try:
        if wf_name_var is not None:
            _wf_data["name"] = wf_name_var.get() or _wf_data.get("name") or "未命名工作流"
    except Exception:
        pass
    return _wf_data.get("name")


def _wf_save_dir():
    """保存对话框默认目录: 当前工作流所在目录 > 程序根目录。"""
    if _wf_file:
        try:
            d = os.path.dirname(os.path.abspath(_wf_file))
            if os.path.isdir(d):
                return d
        except Exception:
            pass
    return _CTX.get("app_root", "") or ""


def _wf_confirm_discard(action="继续"):
    """有未保存修改时先确认 (与脚本编辑「新建/打开前放弃修改」同口径)。"""
    try:
        if not wf_is_modified():
            return True
    except Exception:
        return True
    return bool(messagebox.askyesno(
        "未保存的修改",
        "工作流「{}」有未保存的修改，{}将丢弃这些修改，是否继续？".format(
            get_workflow_name(), action)))


def _wf_write(fp, action="已保存"):
    """原子写盘 (临时文件 + os.replace), 成功后把该路径设为当前文件。"""
    global _wf_file
    tmp = fp + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_wf_data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, fp)
    except Exception as e:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        messagebox.showerror("保存失败", str(e))
        _log1("工作流保存失败: {}".format(e), "error")
        return False
    _wf_file = fp
    _wf_mark_saved()
    _wf_save_version()
    _toast("{}: {} ({}步骤)".format(action, os.path.basename(fp),
                                   len(_wf_data.get("steps") or [])), "success")
    _log1("工作流{}: {}".format(action, fp))
    return True


def _wf_save_version():
    """保存当前工作流快照到版本历史 (失败静默, 不阻断保存主流程)。"""
    try:
        import copy as _copy
        from version_manager import get_version_manager
        get_version_manager().save_workflow_version(
            _wf_file, _copy.deepcopy(_wf_data), comment="保存")
    except Exception:
        pass


def _wf_new():
    """新建 (Ctrl+N 于工作流 Tab): 有未保存修改先确认, 再重置为空白工作流。"""
    if not _wf_confirm_discard("新建工作流"):
        return False
    global _wf_file
    _wf_snapshot_undo()
    _wf_file = None
    _wf_data["name"] = "未命名工作流"
    _wf_data["steps"] = []
    try:
        wf_name_var.set("未命名工作流")
    except Exception:
        pass
    _wf_refresh_tree()
    _wf_mark_saved()
    return True


def _wf_open():
    """打开工作流文件 (有未保存修改先确认)。"""
    if not _wf_confirm_discard("打开其他工作流"):
        return False
    fp = filedialog.askopenfilename(title="打开工作流",
        filetypes=[('JSON', '*.json'), ('YAML', '*.yaml')], initialdir=_wf_save_dir())
    if not fp:
        return False
    try:
        from workflow import parse_workflow
        data = parse_workflow(fp)
    except Exception as e:
        messagebox.showerror("加载失败", str(e))
        return False
    global _wf_file
    _wf_snapshot_undo()
    _wf_file = fp
    _wf_data["name"] = data.get("name") or os.path.basename(fp)
    _wf_data["steps"] = data.get("steps", [])
    try:
        wf_name_var.set(_wf_data["name"])
    except Exception:
        pass
    _wf_refresh_tree()
    _wf_mark_saved()
    _toast("已加载: {} ({}步骤)".format(_wf_data["name"], len(_wf_data["steps"])), "success")
    return True


def _wf_save():
    """保存 (Ctrl+S): 已有目标文件 → 原地覆盖不弹框; 尚未落盘 → 转「另存」。"""
    if not _wf_data.get("steps"):
        messagebox.showwarning("提示", "工作流为空")
        return False
    _wf_sync_name()
    if _wf_file:
        return _wf_write(_wf_file, "已保存")
    return _wf_save_as()


def _wf_save_as():
    """另存为 (Ctrl+Shift+S): 总是询问新路径, 成功后该路径成为当前文件。"""
    if not _wf_data.get("steps"):
        messagebox.showwarning("提示", "工作流为空")
        return False
    _wf_sync_name()
    cur = _wf_file or ""
    initial = (os.path.basename(cur) if cur
               else "{}.json".format(get_workflow_name().strip() or "未命名工作流"))
    fp = filedialog.asksaveasfilename(title="另存工作流",
        defaultextension=".json", filetypes=[('JSON', '*.json')],
        initialdir=_wf_save_dir(), initialfile=initial)
    if not fp:
        return False
    if not os.path.splitext(fp)[1]:
        fp += ".json"
    return _wf_write(fp, "已另存为")


def _wf_version_history():
    """工作流版本历史对话框: 查看 / 恢复 / 对比。"""
    if not _wf_file:
        _toast("请先保存工作流 (版本历史跟随文件路径)", "info")
        return
    from version_manager import get_version_manager
    vm = get_version_manager()
    history = vm.get_workflow_history(_wf_file)
    if not history:
        _toast("暂无版本历史", "info")
        return

    import tkinter as _tk
    import copy as _copy
    top = _tk.Toplevel()
    top.title("工作流版本历史")
    top.configure(bg=C["bgc"])
    top.transient(_window if "_window" in globals() else None)
    sp = _CTX.get("sp") or (lambda v: v)

    _tk.Label(top, text=os.path.basename(_wf_file), bg=C["bgc"], fg=C["fgt"],
        font=FONT_BODY).pack(pady=(sp(8), 2))

    frame = _tk.Frame(top, bg=C["bgc"])
    frame.pack(fill="both", expand=True, padx=sp(8), pady=sp(4))

    lb = _tk.Listbox(frame, width=48, height=10, font=FONT_BODY,
        bg=C["ebg"], fg=C["fgb"], selectbackground=C["acl"], selectforeground=C["fgt"])
    lb.pack(side="left", fill="both", expand=True)
    for vnum, ts, cnt, comment in history:
        lb.insert("end", "v{}  |  {}  |  {}步骤  {}".format(vnum, ts, cnt, comment or ""))

    btn_frame = _tk.Frame(top, bg=C["bgc"])
    btn_frame.pack(pady=sp(4))

    def _selected():
        sel = lb.curselection()
        return history[sel[0]][0] if sel else None

    def _view():
        vnum = _selected()
        if not vnum:
            return
        data = vm.restore_workflow_version(_wf_file, vnum)
        if data is None:
            messagebox.showwarning("提示", "无法加载版本 v{}".format(vnum))
            return
        _show_wf_steps_preview(data, "版本 v{}".format(vnum))

    def _restore():
        vnum = _selected()
        if not vnum:
            return
        data = vm.restore_workflow_version(_wf_file, vnum)
        if data is None:
            messagebox.showwarning("提示", "无法加载版本 v{}".format(vnum))
            return
        if not messagebox.askyesno("确认", "恢复工作流到版本 v{}？\n(当前内容将被覆盖, 且会写入一条新版本)".format(vnum)):
            return
        _wf_data["name"] = data.get("name") or _wf_data.get("name") or "未命名工作流"
        _wf_data["steps"] = _copy.deepcopy(data.get("steps", []))
        try:
            wf_name_var.set(_wf_data["name"])
        except Exception:
            pass
        _wf_refresh_tree()
        top.destroy()
        _toast("已恢复到版本 v{}".format(vnum), "success")

    def _compare():
        vnum = _selected()
        if not vnum:
            return
        vm._wf_current_steps = _wf_data.get("steps", [])
        text = vm.diff_workflow_versions(_wf_file, vnum, None)
        _show_diff_text(text, "工作流对比 (v{} → 当前)".format(vnum))

    _wf_btn(btn_frame, "查看", "ac", command=_view, font=FONT_SMALL).pack(side="left", padx=2)
    _wf_btn(btn_frame, "恢复", "dg", command=_restore, font=FONT_SMALL).pack(side="left", padx=2)
    _wf_btn(btn_frame, "对比", "ac", command=_compare, font=FONT_SMALL).pack(side="left", padx=2)
    _wf_btn(btn_frame, "关闭", "ac", command=top.destroy, font=FONT_SMALL).pack(side="left", padx=2)


def _show_wf_steps_preview(data, title):
    """只读展示某版本工作流的步骤结构 (树形文本)。"""
    import tkinter as _tk
    top = _tk.Toplevel()
    top.title(title)
    top.configure(bg=C["bgc"])
    top.transient(_window if "_window" in globals() else None)

    txt = _tk.Text(top, width=64, height=18, font=FONT_BODY, bg=C["ebg"], fg=C["fgb"],
        insertbackground=C["fgt"])
    txt.pack(fill="both", expand=True, padx=8, pady=8)
    lines = ["工作流: {}".format(data.get("name", "")), "=" * 40]
    for i, s in enumerate(data.get("steps", [])):
        lines.append("[{}] {}".format(i + 1, _wf_step_label(s)))
    txt.insert("1.0", "\n".join(lines))
    txt.configure(state="disabled")
    _wf_btn(top, "关闭", "ac", command=top.destroy).pack(pady=4)


def _show_diff_text(text, title):
    """只读展示 diff 文本。"""
    import tkinter as _tk
    top = _tk.Toplevel()
    top.title(title)
    top.configure(bg=C["bgc"])
    top.transient(_window if "_window" in globals() else None)

    txt = _tk.Text(top, width=80, height=20, font=("Consolas", 10), bg=C["ebg"],
        fg=C["fgb"], insertbackground=C["fgt"])
    txt.pack(fill="both", expand=True, padx=8, pady=8)
    txt.insert("1.0", text)
    txt.configure(state="disabled")
    _wf_btn(top, "关闭", "ac", command=top.destroy).pack(pady=4)


def _wf_clear():
    """清空工作流所有步骤 (与「脚本编辑 → 清空」同规格: 先确认, 破坏性提示)。"""
    if not _wf_data.get("steps"):
        _toast("工作流已是空的", "info")
        return False
    if not messagebox.askyesno("确认", "确定要清空工作流的所有步骤吗？"):
        return False
    global _wf_file
    _wf_snapshot_undo()
    _wf_file = None
    _wf_data["steps"] = []
    _wf_data["name"] = "未命名工作流"
    try:
        wf_name_var.set("未命名工作流")
    except Exception:
        pass
    _wf_refresh_tree()
    _wf_mark_saved()
    _toast("已清空工作流所有步骤", "warning")
    return True


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
    elif stype == "retry":
        step = {"type": "retry", "max_attempts": 3, "delay": 1.0, "steps": []}
    elif stype == "workflow":
        fp = filedialog.askopenfilename(title="选择子工作流",
            filetypes=[('工作流', '*.json'), ('YAML', '*.yaml *.yml'), ('All', '*.*')],
            initialdir=_CTX.get("app_root", ""))
        if fp:
            if _wf_file and os.path.commonprefix([fp, os.path.dirname(_wf_file)]):
                fp = os.path.relpath(fp, os.path.dirname(_wf_file))
            step = {"type": "workflow", "path": fp.replace("\\", "/"),
                    "inputs": {}, "outputs": []}
        else:
            return
    else:
        return
    step["enabled"] = True
    step["comment"] = ""
    _wf_snapshot_undo()
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
    # 编辑对话框会原地修改 step (引用), 打开前先入撤销栈
    _wf_snapshot_undo()

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
        _wf_btn(pf, "浏览", "ac", command=_browse, font=FONT_SMALL).pack(side="left")
        def _save_script():
            step["path"] = path_var.get()
            _wf_refresh_tree()
            dlg.destroy()
        _wf_btn(dlg, "确定", "ac", command=_save_script).pack(pady=10)

    elif stype == "parallel":
        sub_steps = step.get("steps", [])
        tkinter.Label(dlg, text="子步骤 ({} 个):".format(len(sub_steps)),
            bg=C["bgc"], fg=C["fgb"]).pack(pady=4)
        sf = tkinter.Frame(dlg, bg=C["bgc"])
        sf.pack(fill="both", expand=True, padx=10, pady=4)
        sub_list = tkinter.Listbox(sf, font=FONT_BODY, bg=C["ebg"], fg=C["fgb"],
            selectbackground=C["acl"], selectforeground=C["fgt"], height=6)
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
        _wf_btn(bf, "+ 添加", "ac", command=_add_sub, font=FONT_SMALL).pack(side="left", padx=2, pady=4)
        _wf_btn(bf, "- 删除", "dg", command=_del_sub, font=FONT_SMALL).pack(side="left", padx=2, pady=4)
        def _save_parallel():
            step["steps"] = sub_steps
            _wf_refresh_tree()
            dlg.destroy()
        _wf_btn(dlg, "确定", "ac", command=_save_parallel).pack(pady=4)

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
        _wf_btn(dlg, "确定", "ac", command=_save_cond).pack(pady=10)

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
        _wf_btn(dlg, "确定", "ac", command=_save_wait).pack(pady=10)

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
        _wf_btn(dlg, "确定", "ac", command=_save_command).pack(pady=10)

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
            selectbackground=C["acl"], selectforeground=C["fgt"], height=4)
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
        _wf_btn(bf, "+ 添加", "ac", command=_add_sub, font=FONT_SMALL).pack(side="left", padx=2, pady=2)
        _wf_btn(bf, "- 删除", "dg", command=_del_sub, font=FONT_SMALL).pack(side="left", padx=2, pady=2)
        def _save_loop():
            if not sub_steps:
                messagebox.showwarning("提示", "循环节点至少需要 1 个子步骤，请先添加")
                return
            step["times"] = loop_var.get().strip()
            step["steps"] = sub_steps
            _wf_refresh_tree()
            dlg.destroy()
        _wf_btn(dlg, "确定", "ac", command=_save_loop).pack(pady=4)

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
        _wf_btn(dlg, "确定", "ac", command=_save_var).pack(pady=10)

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
        _wf_btn(dlg, "确定", "ac", command=_save_log).pack(pady=10)

    elif stype == "retry":
        # 重试节点: 最大次数 + 间隔 + 子步骤
        tkinter.Label(dlg, text="最大重试次数:", bg=C["bgc"], fg=C["fgb"]).pack(pady=(10, 2))
        ma_var = tkinter.StringVar(value=str(step.get("max_attempts", 3)))
        tkinter.Entry(dlg, textvariable=ma_var, width=10,
            font=FONT_BODY, bg=C["ebg"], fg=C["fgb"],
            insertbackground=C["fgt"]).pack(pady=2)
        tkinter.Label(dlg, text="重试间隔 (秒):", bg=C["bgc"], fg=C["fgb"]).pack(pady=(6, 2))
        dly_var = tkinter.StringVar(value=str(step.get("delay", 1.0)))
        tkinter.Entry(dlg, textvariable=dly_var, width=10,
            font=FONT_BODY, bg=C["ebg"], fg=C["fgb"],
            insertbackground=C["fgt"]).pack(pady=2)
        # 子步骤管理
        sub_steps = step.get("steps", []) or []
        tkinter.Label(dlg, text="子步骤 ({} 个):".format(len(sub_steps)),
            bg=C["bgc"], fg=C["fgb"]).pack(pady=(8, 2))
        sf = tkinter.Frame(dlg, bg=C["bgc"])
        sf.pack(fill="both", expand=True, padx=10, pady=2)
        sub_list = tkinter.Listbox(sf, font=FONT_BODY, bg=C["ebg"], fg=C["fgb"],
            selectbackground=C["acl"], selectforeground=C["fgt"], height=4)
        sub_list.pack(side="left", fill="both", expand=True)
        for s in sub_steps:
            sub_list.insert("end", s.get("path", s.get("cmd", str(s))))
        def _add_retry_sub():
            fp = filedialog.askopenfilename(
                filetypes=[('Excel', '*.xls'), ('ACRPA 脚本', '*.acrpas')])
            if fp:
                if _wf_file and os.path.commonprefix([fp, os.path.dirname(_wf_file)]):
                    fp = os.path.relpath(fp, os.path.dirname(_wf_file))
                sub_steps.append({"type": "script", "path": fp.replace("\\", "/")})
                sub_list.insert("end", fp)
        def _del_retry_sub():
            sel = sub_list.curselection()
            if sel:
                del sub_steps[sel[0]]
                sub_list.delete(sel[0])
        bf = tkinter.Frame(dlg, bg=C["bgc"])
        bf.pack()
        _wf_btn(bf, "+ 添加", "ac", command=_add_retry_sub, font=FONT_SMALL).pack(side="left", padx=2, pady=2)
        _wf_btn(bf, "- 删除", "dg", command=_del_retry_sub, font=FONT_SMALL).pack(side="left", padx=2, pady=2)
        def _save_retry():
            if not sub_steps:
                messagebox.showwarning("提示", "重试节点至少需要 1 个子步骤，请先添加")
                return
            try:
                ma = int(ma_var.get())
            except ValueError:
                ma = 3
            try:
                dly = float(dly_var.get())
            except ValueError:
                dly = 1.0
            step["max_attempts"] = max(1, ma)
            step["delay"] = max(0.0, dly)
            step["steps"] = sub_steps
            _wf_refresh_tree()
            dlg.destroy()
        _wf_btn(dlg, "确定", "ac", command=_save_retry).pack(pady=4)

    elif stype == "workflow":
        # 子工作流引用: 路径 + 输入变量 + 输出变量
        tkinter.Label(dlg, text="子工作流路径:", bg=C["bgc"], fg=C["fgb"]).pack(pady=(10, 2))
        path_var = tkinter.StringVar(value=step.get("path", ""))
        pf = tkinter.Frame(dlg, bg=C["bgc"])
        pf.pack(pady=4)
        tkinter.Entry(pf, textvariable=path_var, width=34,
            font=FONT_BODY, bg=C["ebg"], fg=C["fgb"],
            insertbackground=C["fgt"]).pack(side="left", padx=(0, 4))
        def _browse_wf():
            fp = filedialog.askopenfilename(
                filetypes=[('工作流', '*.json'), ('YAML', '*.yaml *.yml'), ('全部', '*.*')])
            if fp:
                if _wf_file and os.path.commonprefix([fp, os.path.dirname(_wf_file)]):
                    fp = os.path.relpath(fp, os.path.dirname(_wf_file))
                path_var.set(fp.replace("\\", "/"))
        _wf_btn(pf, "浏览", "ac", command=_browse_wf, font=FONT_SMALL).pack(side="left")
        tkinter.Label(dlg, text="输入变量 (每行 名称=值, 支持 ${var}):", bg=C["bgc"],
            fg=C["fgb"]).pack(pady=(6, 2))
        inputs_text = tkinter.Text(dlg, height=3, width=40,
            font=FONT_BODY, bg=C["ebg"], fg=C["fgb"], insertbackground=C["fgt"])
        inputs_text.pack(pady=2)
        inputs_text.insert("1.0", "\n".join(
            "{}={}".format(k, v) for k, v in (step.get("inputs") or {}).items()))
        tkinter.Label(dlg, text="输出变量 (逗号分隔, 执行后写回父作用域):", bg=C["bgc"],
            fg=C["fgb"]).pack(pady=(6, 2))
        out_var = tkinter.StringVar(value=", ".join(str(x) for x in (step.get("outputs") or [])))
        tkinter.Entry(dlg, textvariable=out_var, width=40,
            font=FONT_BODY, bg=C["ebg"], fg=C["fgb"],
            insertbackground=C["fgt"]).pack(pady=2)
        def _save_workflow():
            inputs = {}
            for line in inputs_text.get("1.0", "end").splitlines():
                line = line.strip()
                if not line or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                inputs[k.strip()] = v.strip()
            outputs = [x.strip() for x in out_var.get().split(",") if x.strip()]
            step["path"] = path_var.get()
            step["inputs"] = inputs
            step["outputs"] = outputs
            _wf_refresh_tree()
            dlg.destroy()
        _wf_btn(dlg, "确定", "ac", command=_save_workflow).pack(pady=10)

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


# ── 运行时回退开关 (v5 §4.3 / §7.2; 默认开, 置 0/false/off 回退) ──
def _env_flag(name, default=True):
    """环境变量开关: 缺失/空 → default; 0/false/no/off → False。"""
    try:
        v = os.environ.get(name)
    except Exception:
        return default
    if v is None or str(v).strip() == "":
        return default
    return str(v).strip().lower() not in ("0", "false", "no", "off")


# WF_EXPORT_ASYNC : True → 流程图导出的 grab+save 走后台池; False → 主线程同步 (旧行为)
WF_EXPORT_ASYNC = _env_flag("WF_EXPORT_ASYNC", True)


def _wf_submit_bg(fn, on_done, on_error, name):
    """把 fn 交给后台池; 无事件循环/通道不可用时同步执行 (返回 False)。"""
    if WF_EXPORT_ASYNC:
        try:
            import ui.bg_task as _bg
            return bool(_bg.submit(fn, on_done, on_error, name=name, priority=0))
        except Exception:
            pass
    try:
        on_done(fn())
    except Exception as e:
        on_error(e)
    return False


def _wf_export_screenshot():
    """导出流程图截图为 PNG。

    v5 §4.3 要点 4: `ImageGrab.grab` 依赖**实时屏幕坐标与画布内容**, 故坐标系仍在
    主线程 `update_idletasks()` 后同步取; 取完把「grab + save」整段提交后台
    (20~120 ms 不再占主线程), 回调里只 toast/log。
    开关 `WF_EXPORT_ASYNC` (默认开) 置 False 回到主线程同步实现。
    """
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
            # 确保流程图已渲染 (坐标系必须主线程取, 见 docstring)
            wf_flow_canvas.update_idletasks()
            x = wf_flow_canvas.winfo_rootx()
            y = wf_flow_canvas.winfo_rooty()
            w = wf_flow_canvas.winfo_width()
            h = wf_flow_canvas.winfo_height()
            if w > 0 and h > 0:
                box = (x, y, x + w, y + h)
                # ImageGrab 模块句柄在主线程解析一次, 后台不再触碰 _CTX/控件
                getter = _CTX.get("get_image_grab")
                grab_mod = getter() if getter else None

                def _do_export(box=box, mod=grab_mod, fp=fp):
                    """后台: 屏幕 grab + 写盘 (不碰任何 Tk 对象)。"""
                    if mod is None:
                        raise RuntimeError("ImageGrab 不可用")
                    img = mod.grab(bbox=box)
                    img.save(fp)
                    return fp

                def _done(saved):
                    _toast("流程图已保存: {}".format(os.path.basename(saved)), "success")
                    _log1("流程图导出: {}".format(saved))

                def _err(e):
                    _log1("流程图导出失败: {}".format(e), "error")
                    _toast("流程图导出失败: {}".format(e), "error")

                _wf_submit_bg(_do_export, _done, _err, "wf-export-png")
            else:
                messagebox.showwarning("提示", "流程图区域不可见，请先切换到工作流Tab")
        except Exception as e:
            messagebox.showerror("导出失败", str(e))


def _wf_delete_step():
    sel = wf_tree.selection()
    if sel:
        idx = wf_tree.index(sel[0])
        if idx < len(_wf_data["steps"]):
            _wf_snapshot_undo()
            del _wf_data["steps"][idx]
            _wf_refresh_tree()


def _wf_move_up():
    sel = wf_tree.selection()
    if sel:
        idx = wf_tree.index(sel[0])
        if idx > 0:
            _wf_snapshot_undo()
            _wf_data["steps"][idx], _wf_data["steps"][idx - 1] = \
                _wf_data["steps"][idx - 1], _wf_data["steps"][idx]
            _wf_refresh_tree()


def _wf_move_down():
    sel = wf_tree.selection()
    if sel:
        idx = wf_tree.index(sel[0])
        if idx < len(_wf_data["steps"]) - 1:
            _wf_snapshot_undo()
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
        _wf_snapshot_undo()
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
    _wf_snapshot_undo()
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
        _wf_snapshot_undo()
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
            _wf_btn(bf, "确定", "ac", command=_save).pack(side="left", padx=4)
            tkinter.Button(bf, text="取消", command=_cancel,
                font=FONT_BUTTON, bg=C["bgc"], fg=C["fgb"],
                activebackground=C["acl"], activeforeground=C["fgt"],
                relief="flat", bd=1, cursor="hand2", padx=12, pady=3).pack(side="left", padx=4)
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
        activebackground=C["acl"], activeforeground=C["fgt"])
    menu.add_command(label="编辑", command=_wf_edit_step)
    menu.add_separator()
    insert_menu = tkinter.Menu(menu, tearoff=0, bg=C["bgc"], fg=C["fgt"],
        activebackground=C["acl"], activeforeground=C["fgt"])
    insert_menu.add_command(label="重试块", command=lambda: _wf_add_step("retry"))
    insert_menu.add_command(label="子工作流引用", command=lambda: _wf_add_step("workflow"))
    menu.add_cascade(label="插入", menu=insert_menu)
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

    # 读取执行控制参数 (与运行栏共用变量; loop_count<=0 约定为无限循环)
    try:
        _lv = str(wf_loop_var.get() or "").strip()
        try:
            import i18n
            _inf = i18n.t("exec.loop_infinite")
        except Exception:
            _inf = "无限"
        if _lv and not _lv.isdigit():
            loop_count = 0 if _lv == _inf else 1
        else:
            loop_count = int(_lv) if _lv else 1
    except Exception:
        loop_count = 1
    try:
        max_minutes = float(wf_maxmin_var.get())
    except (TypeError, ValueError):
        max_minutes = 0

    def _run_thread():
        try:
            from core import run_id as _run_id
            _run_id.new_run()
        except Exception:
            pass
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
        finally:
            try:
                from core import run_id as _run_id
                _run_id.reset()
            except Exception:
                pass

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
    # 尺寸令牌: 与其它弹窗一致走注入的 utils.sp; 注入缺失时退化为 1:1 像素,
    # 避免此前直接引用 build() 局部变量 `sp` 导致的 NameError (变量管理弹窗打不开)。
    sp = _CTX.get("sp")
    if not callable(sp):
        sp = lambda v: float(v)
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
    sy = tkinter.Scrollbar(lf, orient="vertical", command=var_tree.yview,
                           width=sp("sb_w"))
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


def _wf_highlight_dirty_ids(indices):
    """把顶层步骤序号映射为图节点 id 列表 (P2-7 脏矩形局部重绘)。

    仅当「编辑图已存在且结构与 _wf_data 一致」(positions 已由上次全量 layout 写回)
    时才返回节点 id, 否则返回 None → 调用方退回全量渲染。
    """
    g = _wf_graph_edit
    if g is None or _flow_graph is None:
        return None
    try:
        if _flow_graph.demote(g) != _wf_data:
            return None
        roots = g.roots()
    except Exception:
        return None
    out = []
    for idx in indices:
        if idx is None or idx < 0 or idx >= len(roots):
            continue
        out.append(roots[idx].id)
    return out or None


def _wf_animate_highlight(indices):
    """执行高亮 step 切换的颜色渐变 (P2-2, GradientItem 语义)。

    结构可安全映射时, 只对受影响节点做 outline 颜色渐变 (itemconfigure, 不做
    delete/rebuild); 否则退回全量渲染 —— 脏矩形局部重绘 (P2-7) 保持为回退路径。
    """
    g = _wf_graph_edit
    ids = _wf_highlight_dirty_ids(indices)
    if g is None or ids is None:
        _wf_render_flowchart()
        return
    zoom = float(_FLOW_ZOOM_STATE.get("scale", 1.0)) if _FLOW_ZOOM_STATE else 1.0
    try:
        _flow_canvas.animate_nodes_outline(
            wf_flow_canvas, g, ids, colors=_FLOW_COLORS,
            ctx=_wf_flow_ctx(g), zoom=zoom)
    except Exception:
        _wf_render_flowchart()


def _wf_update_workflow_highlight():
    """执行工作流时周期刷新流程图高亮 (供 _periodic 调用)。

    仅在当前步骤变化时刷新, 避免每 100ms 全量重绘导致的闪烁与 CPU 开销;
    执行结束后复位一次高亮。P2-2: 步骤切换改为 outline 颜色渐变 (动画只
    itemconfigure 受影响节点); P2-7 脏矩形局部重绘保持为回退路径。
    """
    try:
        from workflow import workflow_engine
        state = _CTX.get("state")
        _last = getattr(_wf_update_workflow_highlight, "_last_step", -2)
        if state is not None and state.running and workflow_engine.current_step >= 0:
            cur = workflow_engine.current_step
            if _last != cur:
                _wf_update_workflow_highlight._last_step = cur
                _wf_animate_highlight([_last, cur])
        elif _last != -1:
            # 执行结束/停止: 复位一次高亮
            _wf_update_workflow_highlight._last_step = -1
            _wf_animate_highlight([_last])
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════
# WF-B2: 检查面板 (P1-1) / 悬浮工具条 + 小地图 (P1-2) / 大纲联动 (P1-5)
# ══════════════════════════════════════════════════════════════════════

def _wf_a11y_helpers():
    """惰性取 utils 的可达性助手 (attach_tooltip/set_accessible_name/bind_icon_activate)。

    本模块仅经依赖注入使用 utils (不 import ACRPA); 此处惰性 import utils,
    取不到则回退为最简 <Button-1> 绑定, 保证离屏/裁剪环境下工具条仍可点击。
    """
    try:
        import utils as _u
        return (getattr(_u, "attach_tooltip", None),
                getattr(_u, "set_accessible_name", None),
                getattr(_u, "bind_icon_activate", None))
    except Exception:
        return (None, None, None)


def _wf_mk_icon_btn(parent, text, tip, command):
    """图标按钮 (Label + 可达性): attach_tooltip + set_accessible_name + bind_icon_activate。"""
    bg = (C.get("bgc") or C.get("bg")) if isinstance(C, dict) else None
    fg = C.get("fgb") if isinstance(C, dict) else None
    try:
        lbl = tkinter.Label(parent, text=text, font=FONT_SMALL_BOLD, bg=bg, fg=fg,
                            cursor="hand2", padx=3, pady=1)
        lbl.pack(side="left", padx=1)
    except Exception:
        return None
    tooltip, acc, activate = _wf_a11y_helpers()
    if callable(tooltip):
        try:
            tooltip(lbl, tip)
        except Exception:
            pass
    if callable(activate):
        try:
            activate(lbl, command, name=tip)
        except Exception:
            pass
    else:
        try:
            lbl.bind("<Button-1>", lambda e: (command(), "break")[1])
        except Exception:
            pass
        if callable(acc):
            try:
                acc(lbl, tip)
            except Exception:
                pass
    return lbl


def _wf_build_panel_v2():
    """WF-B2 构建: 检查面板展开列表 + 画布悬浮工具条 + 小地图 (隐藏即回退)。

    全部为独立子控件 (不改变 wf_flow_frame 既有 grid 行 0/1); 工具条/小地图以
    `place()` 悬浮于画布之上 (`in_` 不作为: 直接以画布为父容器, 保证覆盖且不影响
    画布滚动)。`_WF_PANEL_V2 == False` 时整体不建 → 回退单行 wf_flow_check_lbl。
    """
    global wf_flow_toolbar, wf_minimap, wf_check_tree
    if not _WF_PANEL_V2 or wf_flow_frame is None or wf_flow_canvas is None:
        return
    sp = _CTX.get("sp") or (lambda v: v)

    # ── P1-1: 检查面板 (折叠态 = wf_flow_check_lbl 可点击; 展开态 = Treeview) ──
    if wf_flow_check_lbl is not None:
        try:
            wf_flow_check_lbl.configure(cursor="hand2")
            wf_flow_check_lbl.bind("<Button-1>", _wf_toggle_check_panel)
        except Exception:
            pass
    try:
        wf_check_tree = ttk.Treeview(wf_flow_frame, show="tree", height=5,
                                     selectmode="browse")
        wf_check_tree.grid(row=2, column=0, columnspan=2, sticky="ew",
                           padx=6, pady=(0, sp(4)))
        wf_check_tree.grid_remove()          # 默认折叠
        wf_check_tree.bind("<<TreeviewSelect>>", _wf_check_tree_click)
    except Exception:
        wf_check_tree = None

    # ── P1-2: 画布右上悬浮工具条 (＋/－/⤢fit/⌗网格/◲小地图/⌁自动整理) ──
    try:
        wf_flow_toolbar = tkinter.Frame(wf_flow_canvas,
                                        bg=C.get("bgc") if "bgc" in C else C.get("bg"),
                                        highlightbackground=C.get("bd"),
                                        highlightthickness=1)
        for text, tip, cmd in (
                ("＋", "放大画布", lambda: _wf_flow_zoom(1.25)),
                ("－", "缩小画布", lambda: _wf_flow_zoom(0.8)),
                ("⤢", "适应窗口", _wf_flow_fit),
                ("⌗", "网格开关", _wf_flow_toggle_grid),
                ("◲", "小地图开关", _wf_flow_toggle_minimap),
                ("⌁", "自动整理布局", _wf_flow_auto_layout)):
            _wf_mk_icon_btn(wf_flow_toolbar, text, tip, cmd)
        wf_flow_toolbar.place(relx=1.0, rely=0.0, anchor="ne", x=-sp(8), y=sp(8))
    except Exception:
        wf_flow_toolbar = None

    # ── P1-2: 右下小地图 (sp(120) × sp(80)) ──
    try:
        wf_minimap = tkinter.Canvas(wf_flow_canvas, width=sp(120), height=sp(80),
                                    bg=C["flowbg"] if "flowbg" in C else "#F8FAFC",
                                    highlightbackground=C.get("bd"),
                                    highlightthickness=1)
        wf_minimap.bind("<Button-1>", _wf_minimap_click)
        wf_minimap.bind("<B1-Motion>", _wf_minimap_click)
        if _WF_MINIMAP_ON:
            _wf_minimap_show()
    except Exception:
        wf_minimap = None

    # 视口跟随: 纵向滚动条 + 画布尺寸变化时刷新小地图
    try:
        wf_flow_scroll_y.configure(command=_wf_flow_yview)
    except Exception:
        pass
    try:
        wf_flow_canvas.bind("<Configure>", lambda e: _wf_draw_minimap(), add="+")
    except Exception:
        pass


def _wf_minimap_show():
    if wf_minimap is None:
        return
    sp = _CTX.get("sp") or (lambda v: v)
    try:
        wf_minimap.place(relx=1.0, rely=1.0, anchor="se", x=-sp(8), y=-sp(8))
    except Exception:
        pass


def _wf_flow_yview(*args):
    """纵向滚动条命令包装: 先滚动, 再刷新小地图视口框 (P1-2)。"""
    try:
        wf_flow_canvas.yview(*args)
    except Exception:
        pass
    _wf_draw_minimap()


# ── P1-2 工具条动作 ──────────────────────────────────────────────────
def _wf_flow_zoom(factor):
    """工具条 ＋/－: 按乘子缩放 (0.4–2.5 由 bind_zoom_pan 状态钳制) 并重绘。"""
    st = _FLOW_ZOOM_STATE
    if not isinstance(st, dict):
        return
    try:
        s = scaled(float(st.get("scale", 1.0)), float(factor))
    except Exception:
        return
    lo = float(st.get("min_scale", 0.4))
    hi = float(st.get("max_scale", 2.5))
    # P3-2: 乘子缩放 + 钳制统一走 core.scale (等价于 max(lo, min(hi, s)))。
    st["scale"] = clamp_scale(s, lo, hi)
    _wf_render_flowchart()


def _wf_flow_fit():
    """工具条 ⤢ / F 键: 按视口等比算 zoom + 居中 (P1-2)。"""
    try:
        _flow_canvas.fit_to_view(wf_flow_canvas, zoom_state=_FLOW_ZOOM_STATE,
                                 re_render=_wf_render_flowchart)
    except Exception:
        pass
    _wf_draw_minimap()


def _wf_flow_toggle_grid():
    global _WF_FLOW_GRID
    _WF_FLOW_GRID = not _WF_FLOW_GRID
    _wf_render_flowchart()


def _wf_flow_toggle_minimap():
    global _WF_MINIMAP_ON
    _WF_MINIMAP_ON = not _WF_MINIMAP_ON
    if wf_minimap is None:
        return
    if _WF_MINIMAP_ON:
        _wf_minimap_show()
        _wf_draw_minimap()
    else:
        try:
            wf_minimap.place_forget()
        except Exception:
            pass


def _wf_flow_auto_layout():
    """工具条 ⌁: 丢弃会话内编辑图 → 重新 promote (重跑 layout) 并 fit。"""
    global _wf_graph_edit
    _wf_graph_edit = None
    _wf_render_flowchart()
    _wf_flow_fit()


# ── P1-2 小地图 ──────────────────────────────────────────────────────
def _wf_flow_view_rect():
    """主画布当前视口对应的内容坐标矩形 (x0,y0,x1,y1); 不可用时 None。"""
    canvas = wf_flow_canvas
    if canvas is None:
        return None
    try:
        parts = [float(p) for p in str(canvas.cget("scrollregion")).split()]
        if len(parts) != 4:
            return None
        vx0 = canvas.canvasx(0)
        vy0 = canvas.canvasy(0)
        vx1 = canvas.canvasx(max(1, canvas.winfo_width()))
        vy1 = canvas.canvasy(max(1, canvas.winfo_height()))
        return (vx0, vy0, vx1, vy1)
    except Exception:
        return None


def _wf_draw_minimap(event=None):
    """P1-2: 用 graph.nodes 的 n.x/y/w/h 重绘小地图 (含视口框)。"""
    global _wf_mini_map
    if wf_minimap is None or not _WF_MINIMAP_ON:
        return
    g = _wf_graph_edit
    if g is None:
        return
    sp = _CTX.get("sp") or (lambda v: v)
    state = _CTX.get("state")
    ctx = {"dark": bool(state is not None and getattr(state, "DARK_MODE", False)),
           "style": _FLOW_STYLE or _refresh_flow_style(C), "sp": sp}
    try:
        _wf_mini_map = _flow_canvas.minimap(
            wf_minimap, g, colors=_FLOW_COLORS, ctx=ctx,
            width=sp(120), height=sp(80), view=_wf_flow_view_rect())
    except Exception:
        _wf_mini_map = None


def _wf_minimap_click(event):
    """小地图点击/拖框 → 主画布跳转到对应内容点 (居中)。"""
    if _wf_mini_map:
        try:
            _flow_canvas.minimap_to_view(wf_flow_canvas, _wf_mini_map, event.x, event.y)
        except Exception:
            pass
        _wf_draw_minimap()
    return "break"


# ── P1-1 检查面板 ────────────────────────────────────────────────────
def _wf_check_header():
    """按 _wf_check_summary + 展开态刷新折叠摘要行 (▸/▾)。"""
    if wf_flow_check_lbl is None:
        return
    mark = "▾" if _wf_check_expanded else "▸"
    try:
        wf_flow_check_lbl.configure(text="{} {}".format(mark, _wf_check_summary),
                                    fg=_wf_check_color)
    except Exception:
        pass


def _wf_refresh_check_panel(issues):
    """P1-1: 用 analyze(g) 结果刷新检查面板 (折叠摘要 + 展开列表)。

    列表项数恒等于 ``len(issues)`` (= ``len(flow_graph.analyze(g))``) —— A9 断言点;
    每行 iid = ``issue_<k>``, 点击即以第 k 条 issue 的 ``nodes`` 定位高亮。
    """
    global _wf_check_issues, _wf_check_summary, _wf_check_color
    _wf_check_issues = list(issues or [])
    try:
        e_n, w_n, i_n = _flow_graph.issue_summary(_wf_check_issues)
    except Exception:
        e_n = w_n = i_n = 0
    if _wf_check_issues:
        head = _wf_check_issues[0].get("message", "")
        more = "" if len(_wf_check_issues) == 1 else " 等 {} 项".format(len(_wf_check_issues))
        _wf_check_summary = "检查: 错误{} 警告{} 提示{} — {}{}".format(
            e_n, w_n, i_n, head, more)
        _wf_check_color = C.get("dg") if e_n else (C.get("wn") if w_n else C.get("fgm"))
    else:
        _wf_check_summary = "检查: 无问题"
        _wf_check_color = C.get("sc", C.get("fgm"))
    _wf_check_header()
    if wf_check_tree is None:
        return
    try:
        wf_check_tree.delete(*wf_check_tree.get_children())
        for k, it in enumerate(_wf_check_issues):
            lvl = str(it.get("level", "info"))
            badge = _ISSUE_BADGE.get(lvl, "提示")
            wf_check_tree.insert("", "end", iid="issue_{}".format(k),
                                 text="[{}] {}".format(badge, it.get("message", "")),
                                 tags=(lvl,))
        for lvl, col in (("error", C.get("dg")), ("warning", C.get("wn")),
                         ("info", C.get("fgm"))):
            wf_check_tree.tag_configure(lvl, foreground=col or C.get("fgb"))
    except Exception:
        pass


def _wf_toggle_check_panel(event=None):
    """点击折叠摘要行: 展开/收起检查列表面板 (P1-1)。"""
    global _wf_check_expanded
    if wf_check_tree is None:
        return "break"
    _wf_check_expanded = not _wf_check_expanded
    try:
        if _wf_check_expanded:
            wf_check_tree.grid()
            wf_check_tree.tkraise()
        else:
            wf_check_tree.grid_remove()
    except Exception:
        pass
    _wf_check_header()
    return "break"


def _wf_check_tree_click(event=None):
    """点击第 k 行 → 高亮第 k 条 issue 的 ``nodes`` 全部节点并滚动定位 (A9)。"""
    if wf_check_tree is None:
        return
    try:
        sel = wf_check_tree.selection()
    except Exception:
        return
    if not sel:
        return
    try:
        k = int(str(sel[0]).split("_", 1)[1])
    except Exception:
        return
    if 0 <= k < len(_wf_check_issues):
        _wf_flow_highlight_nodes(_wf_check_issues[k].get("nodes") or [])


# ── P1-5 大纲 ↔ 画布双向联动 ─────────────────────────────────────────
def _wf_flow_highlight_nodes(nids):
    """在画布给 nids 画选中环 (tag ``wfhl`` / ``wfsel_<nid>``) 并把首节点居中。

    复用 P1-4/N5 的选中集合: 若最近一次 bind_interactions 状态可用, 同步
    ``selected_nodes``; 不同步即回退为纯视觉高亮。
    """
    canvas = wf_flow_canvas
    g = _wf_graph_edit
    if canvas is None or g is None or not nids:
        return
    try:
        canvas.delete("wfhl")
    except Exception:
        pass
    color = (C.get("ac") if isinstance(C, dict) else None) \
        or _FLOW_STYLE.get("edge_sel") or "#0078D4"
    first = None
    for nid in nids:
        n = g.node(nid)
        if n is None:
            continue
        if first is None:
            first = n
        try:
            canvas.create_rectangle(n.x - 2, n.y - 2, n.x + n.w + 2, n.y + n.h + 2,
                                    outline=color, width=2, dash=(3, 2),
                                    tags=("wfhl", "wfsel_" + str(nid)))
        except Exception:
            pass
    if first is not None:
        try:
            _flow_canvas.view_center(canvas, first.x + first.w / 2.0,
                                     first.y + first.h / 2.0)
        except Exception:
            pass
    st = _FLOW_INTERACT_ST
    if isinstance(st, dict):
        try:
            st["selected_nodes"] = set(nids)
        except Exception:
            pass


def _wf_flow_highlight_index(idx):
    """大纲第 idx 行 (== 顶层 step 序号) → 画布对应节点高亮 (tag ``node_<idx>``)。

    A10 行序假设: 渲染期 `index_tags = {n.id: "node_<i>" for i, n in enumerate(roots)}`
    (见 `_wf_render_flow_canvas`), 即 tag 的序号 i 与 `g.roots()` 的枚举顺序**同源**;
    大纲行序亦与顶层 steps 一一对应 —— 故 `roots[idx]` 的 id 必映射到 `node_<idx>`。
    (roots 顺序在单次渲染内稳定, 无并发变更。)
    """
    g = _wf_graph_edit
    if g is None or idx is None:
        return
    try:
        roots = g.roots()
    except Exception:
        return
    if 0 <= idx < len(roots):
        _wf_flow_highlight_nodes([roots[idx].id])


def _wf_select_row_for_idx(idx):
    """画布 → 大纲: 选中第 idx 行并滚动可见 (置 _WF_SYNCING 防回环)。"""
    global _WF_SYNCING
    if wf_tree is None or idx is None:
        return
    try:
        children = wf_tree.get_children()
        if idx < 0 or idx >= len(children):
            return
        _WF_SYNCING = True
        try:
            wf_tree.selection_set(children[idx])
            wf_tree.see(children[idx])
        finally:
            _WF_SYNCING = False
    except Exception:
        _WF_SYNCING = False


def _wf_node_click(event, idx):
    """画布节点单击: 补「画布 → 大纲」选中 + 画布高亮 (P1-5), 再做既有拖拽起始。"""
    _wf_select_row_for_idx(idx)
    _wf_flow_highlight_index(idx)
    return _wf_drag_start(event, idx)


# ── P3-3 共享 `node` 标签事件入口 (几何命中解析 idx, 替代逐节点 tag_bind) ──
def _wf_node_click_at(event):
    """共享 `node` 标签 <Button-1>: 几何命中 → 既有单击 (选中/高亮/拖拽起始)。"""
    idx = _wf_hit_node_index(event.x, event.y)
    if idx is None:
        return None
    return _wf_node_click(event, idx)


def _wf_node_double_at(event):
    """共享 `node` 标签 <Double-1>: 几何命中 → 编辑对应步骤。"""
    idx = _wf_hit_node_index(event.x, event.y)
    if idx is None:
        return None
    return _wf_edit_step_by_index(idx)


def _wf_node_drag_move_at(event):
    """共享 `node` 标签 <B1-Motion>: 沿用已登记 _wf_drag_idx (按下时已几何解析)。"""
    if _wf_drag_idx is None:
        return None
    return _wf_drag_move(event, _wf_drag_idx)


def _wf_node_drag_end_at(event):
    """共享 `node` 标签 <ButtonRelease-1>: 沿用 _wf_drag_idx 收尾。"""
    if _wf_drag_idx is None:
        return None
    return _wf_drag_end(event, _wf_drag_idx)


def _wf_on_tree_select(event=None):
    """大纲 → 画布 (P1-5/A10): 选中第 i 行 → 画布对应节点高亮 + 视口居中。"""
    if _WF_SYNCING or wf_tree is None:
        return
    try:
        sel = wf_tree.selection()
        if not sel:
            return
        idx = wf_tree.index(sel[0])
    except Exception:
        return
    _wf_flow_highlight_index(idx)


# ── 语义别名 (宿主/自测按语义名引用; 原名保留以便逐行比对迁移) ──
update_overflow = _wf_update_overflow
on_wheel = _wf_on_wheel
flow_on_wheel = _wf_flow_on_wheel
toggle_view = _wf_toggle_view
refresh_tree = _wf_refresh_tree
update_highlight = _wf_update_workflow_highlight
run = _wf_run
stop = _wf_stop
wf_save = _wf_save
wf_save_as = _wf_save_as
wf_clear = _wf_clear
wf_new = _wf_new
wf_open = _wf_open


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


def get_wf_step_count():
    """当前工作流的可执行步数 (含嵌套子步骤), 供运行栏上下文条取初值。"""
    try:
        return _wf_step_count(_wf_data.get("steps", []))
    except Exception:
        return 0


def get_wf_tree():
    return wf_tree


def get_wf_paned():
    return wf_paned


def get_wf_outer_paned():
    return wf_outer_paned


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
        "wf_outer_paned": wf_outer_paned,
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
        # WF-B2 新增 (P1-1/P1-2): 检查列表 / 悬浮工具条 / 小地图
        "wf_flow_toolbar": wf_flow_toolbar,
        "wf_minimap": wf_minimap,
        "wf_check_tree": wf_check_tree,
        "_wf_show_flow": _wf_show_flow,
        "_wf_view_btn": _wf_view_btn,
        "_WF_LIB_ACTIONS": _WF_LIB_ACTIONS,
        # 语义别名 (宿主可读)
        "loop_var": wf_loop_var, "maxmin_var": wf_maxmin_var,
        "name_var": wf_name_var, "tree": wf_tree, "paned": wf_paned,
        "show_flow": _wf_show_flow, "view_btn": _wf_view_btn,
    }
