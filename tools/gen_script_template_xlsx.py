# -*- coding: utf-8 -*-
"""gen_script_template_xlsx — 生成 ACRPA 脚本编辑模板 (.xlsx)。

产物：``res/template/ACRPA脚本编辑模板.xlsx`` —— 一个供用户**学习与编辑**脚本的多工作表
Excel 模板，内含：

    * 使用指南 / 脚本编辑区 / 命令速查 / 下拉数据源 / 配色图例
    * 场景样例 / 工作流编排 / 快捷键 / 格式规范

设计要点
--------
* **数据零手抄**：命令名、参数、参数 kind（``string``/``number``）、枚举 ``choices``、
  默认值 ``default``、可选性 ``optional`` 全部在运行时从
  ``res/help/command_schema.json`` 读取；分组顺序与组名从
  ``res/help/command_groups.json`` 读取。
* **下拉（数据验证）**：
    - ``命令类型``列 → 绑定全部命令名（引用『下拉数据源』命令名区）；
    - ``参数1..参数9``列 → **合并枚举下拉**：把「所有命令在该参数位出现的枚举取值」
      去重合并为一个列表作为下拉源（同一列在不同命令下含义不同，故用并集，
      既提供辅助又不会误拒合法输入）；某参数位在任何命令里都没有枚举 → 该列不加验证。
    - 注意 ``showDropDown`` 语义：OOXML 中该属性为 ``1`` 表示**隐藏**下拉箭头，
      故此处**不设置**（留默认），以保证下拉箭头可见。
* **配色**：分组配色取自 ACRPA 工作流节点色板（``src/ui/workflow_view.py`` 的
  ``_FLOW_COLORS`` 及任务约定映射），写入编辑区命令列与命令速查/配色图例。
* **幂等**：可重复运行，覆盖输出；运行后自动回读校验并打印摘要。

用法
----
    python -X utf8 tools/gen_script_template_xlsx.py

仅依赖 openpyxl（现有依赖），不引入任何第三方新依赖，不修改任何产品代码。
"""
import json
import os

import openpyxl
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

# ── 路径 ──────────────────────────────────────────────────────────────
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA_PATH = os.path.join(ROOT, "res", "help", "command_schema.json")
GROUPS_PATH = os.path.join(ROOT, "res", "help", "command_groups.json")
# 内置资源真源：直接输出到 res/template/（res/ 已被整体打包）
OUT_PATH = os.path.join(ROOT, "res", "template", "ACRPA脚本编辑模板.xlsx")

# ── 工作表名 ──────────────────────────────────────────────────────────
SH_GUIDE = "使用指南"
SH_EDIT = "脚本编辑区"
SH_REF = "命令速查"
SH_DATA = "下拉数据源"
SH_COLOR = "配色图例"
SH_DEMO = "场景样例"
SH_WF = "工作流编排"
SH_KEY = "快捷键"
SH_FMT = "格式规范"

SHEET_ORDER = [SH_GUIDE, SH_EDIT, SH_REF, SH_DATA, SH_COLOR,
               SH_DEMO, SH_WF, SH_KEY, SH_FMT]

# ── 分组配色（与 ACRPA 工作流节点色板一致） ───────────────────────────
GROUP_COLORS = {
    "基础操作": "#3B82F6",
    "流程控制": "#F59E0B",
    "窗口管理": "#0EA5E9",
    "变量与数据处理": "#8B5CF6",
    "代码执行": "#EF4444",
    "OCR 文字识别": "#10B981",
    "AI 增强": "#EC4899",
    "工作流": "#14B8A6",
    "浏览器自动化": "#6366F1",
    "其他命令": "#6B7280",
}
FALLBACK_GROUP = "其他命令"

# ── 脚本格式常量（与 src/script_io.py 对齐） ─────────────────────────
HEADER_CMD = "命令类型"
TITLE_PLACEHOLDER = "（标题行）"
NUM_ARGS = 9
EDIT_ROWS = 500          # 编辑区预留给用户的写入行数（含预设行）

# ── 样式 ──────────────────────────────────────────────────────────────
THIN = Side(style="thin", color="D9D9D9")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
HEAD_FILL = PatternFill("solid", fgColor="374151")
HEAD_FONT = Font(bold=True, color="FFFFFF", size=11)
SEC_FILL = PatternFill("solid", fgColor="E5E7EB")
SEC_FONT = Font(bold=True, color="111827", size=11)
WRAP = Alignment(vertical="top", wrap_text=True)
CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)
LEFT_TOP = Alignment(horizontal="left", vertical="top", wrap_text=True)
MONO = Font(name="Consolas", size=10)


def _fill(hex_color):
    return PatternFill("solid", fgColor=hex_color.lstrip("#"))


def _load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ── 数据装载 ──────────────────────────────────────────────────────────
class Cmd(object):
    """单条命令的只读视图。"""

    def __init__(self, node):
        self.name = node.get("name", "")
        self.description = node.get("description", "")
        self.params_text = node.get("params_text", "") or ""
        self.params = list(node.get("params") or [])

    def param_at(self, pos):
        """取第 pos 个参数位（1-based），越界返回 None。"""
        if 1 <= pos <= len(self.params):
            return self.params[pos - 1]
        return None


def load_registry():
    """读取 schema + groups，返回 (ordered_commands, group_of, groups_order)。"""
    schema = _load_json(SCHEMA_PATH)
    groups_doc = _load_json(GROUPS_PATH)

    by_name = {}
    for node in schema.get("commands") or []:
        c = Cmd(node)
        if c.name:
            by_name[c.name] = c

    ordered = []
    group_of = {}
    groups_order = []
    seen = set()
    for g in groups_doc.get("groups") or []:
        title = g.get("title", "")
        groups_order.append(title)
        for name in g.get("commands") or []:
            c = by_name.get(name)
            if c is None or name in seen:
                continue
            seen.add(name)
            ordered.append(c)
            group_of[name] = title
    # 未在任何分组列出的命令 → 兜底组，永不丢失
    for name, c in by_name.items():
        if name not in seen:
            seen.add(name)
            ordered.append(c)
            group_of[name] = groups_doc.get("_fallback_title", FALLBACK_GROUP)
    if FALLBACK_GROUP not in groups_order:
        groups_order.append(FALLBACK_GROUP)
    return ordered, group_of, groups_order


def build_enum_map(commands):
    """合并枚举：{pos(1..9): [去重后的枚举取值...]}（保持首次出现顺序）。"""
    enum_map = {}
    for pos in range(1, NUM_ARGS + 1):
        vals = []
        for c in commands:
            p = c.param_at(pos)
            if not p:
                continue
            for ch in p.get("choices") or []:
                s = str(ch)
                if s not in vals:
                    vals.append(s)
        if vals:
            enum_map[pos] = vals
    return enum_map


# ── 单元格辅助 ────────────────────────────────────────────────────────
def put(ws, row, col, value, *, fill=None, font=None, align=None,
        border=True, comment=None):
    cell = ws.cell(row=row, column=col, value=value)
    if fill is not None:
        cell.fill = fill
    if font is not None:
        cell.font = font
    if align is not None:
        cell.alignment = align
    if border:
        cell.border = BORDER
    if comment:
        cell.comment = Comment(comment, "ACRPA模板")
    return cell


def section(ws, row, text, span=2):
    """写一条小节标题（跨 span 列，浅灰底）。返回下一行号。"""
    cell = ws.cell(row=row, column=1, value=text)
    cell.fill = SEC_FILL
    cell.font = SEC_FONT
    cell.alignment = Alignment(vertical="center")
    if span > 1:
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=span)
    return row + 1


def kv_row(ws, row, key, val):
    put(ws, row, 1, key, font=Font(bold=True), align=LEFT_TOP)
    put(ws, row, 2, val, align=WRAP)
    return row + 1


# ── 各工作表构建 ──────────────────────────────────────────────────────
def sheet_guide(wb, commands, group_of, groups_order, enum_map):
    ws = wb.create_sheet(SH_GUIDE)
    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 110
    r = 1
    cell = ws.cell(row=r, column=1, value="ACRPA 脚本编辑模板（.xlsx）")
    cell.font = Font(bold=True, size=14, color="111827")
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=2)
    r += 2

    r = section(ws, r, "一、模板用途")
    for k, v in [
        ("用途", "本模板用于**学习与编辑** ACRPA 脚本：内置全部 %d 条命令的下拉选项、"
                 "参数枚举、分组配色与说明，帮助你在 Excel 中直观地书写脚本。" % len(commands)),
        ("定位", "这是**编辑参考模板**，不是产品内置文件；可自由复制、改名、另存使用。"),
        ("覆盖范围", "全部 %d 组 / %d 条命令（数据实时取自 res/help/command_schema.json）。"
                   % (len(groups_order), len(commands))),
    ]:
        r = kv_row(ws, r, k, v)
    r += 1

    r = section(ws, r, "二、如何使用")
    for i, (k, v) in enumerate([
        ("①填表", "切到『脚本编辑区』工作表：A 列选『命令类型』（下拉），B~J 列填『参数1~参数9』。"),
        ("②留白", "未使用的参数位留空即可（不要写多余逗号；留空会被归一为 \"\"）。"),
        ("③存储", "在 ACRPA 中『另存为』导出为 .xls，或按『格式规范』页转成 .acrpas(JSON)。"),
        ("④导入", "回到 ACRPA：菜单『载入脚本』选择 .xls；或直接打开 .acrpas。"),
        ("⑤续写", "编辑区下拉已覆盖 %d 行，超出请在下方继续输入（下拉仍生效）。" % EDIT_ROWS),
    ]):
        r = kv_row(ws, r, k, v)
    r += 1

    r = section(ws, r, "三、列约定（10 列）")
    r = kv_row(ws, r, "第 0 列", "命令类型 —— 命令名（如 等待 / 输入 / 点图）。")
    r = kv_row(ws, r, "第 1..9 列", "参数1..参数9 —— 顺序与『命令速查』中该命令的参数顺序一致。")
    r = kv_row(ws, r, "行 0", "表头行（命令类型, 参数1..参数9）。")
    r = kv_row(ws, r, "行 1", "%s 占位行（ACRPA 读 .xls 时固定跳过前 2 行）。" % TITLE_PLACEHOLDER)
    r = kv_row(ws, r, "行 2 起", "数据行（本模板的预设示例从第 3 行开始，对应此行）。")
    r += 1

    r = section(ws, r, "四、空值约定")
    r = kv_row(ws, r, "留空", "推荐：直接把单元格留空。")
    r = kv_row(ws, r, "写 None", "也可写字面量 None —— ACRPA 载入时会归一为 \"\"。")
    r = kv_row(ws, r, "禁止", "不要在参数里写多余的逗号或空引号（\"\"）：Excel 模式下用单元格分隔列即可。")
    r += 1

    r = section(ws, r, "五、如何被 ACRPA 使用")
    r = kv_row(ws, r, "路径 A", ".xls / .xlsx：ACRPA 读第 0 张表、跳过前 2 行、取第 0 列命令 + 第 1..9 列参数。")
    r = kv_row(ws, r, "路径 B", ".acrpas：JSON 一等格式 {\"schema\":1, \"rows\":[{\"cmd\":..,\"args\":[9 项]}]}。")
    r = kv_row(ws, r, "备注", "工作流步骤脚本当前仅支持 .xls/.xlsx（见 src/script_io.py）。")
    r += 1

    r = section(ws, r, "六、学习路径（建议顺序）")
    for i, v in enumerate([
        "『使用指南』(本页) → 了解列约定与空值约定；",
        "『脚本编辑区』→ 看预设示例行的写法；",
        "『命令速查』→ 查命令的参数、枚举与示例；",
        "『场景样例』→ 参考完整脚本；",
        "『工作流编排』与『快捷键』→ 进阶用法；",
        "『格式规范』→ 排错与 .acrpas 结构。",
    ]):
        r = kv_row(ws, r, "step %d" % (i + 1), v)
    r += 1

    r = section(ws, r, "七、颜色图例说明")
    r = kv_row(ws, r, "含义", "命令列 / 分组列的底色 = 该命令所属分组（白字），与『配色图例』页一致。")
    put(ws, r, 1, "分组", font=Font(bold=True), align=CENTER)
    put(ws, r, 2, "色值（点开『配色图例』查看色块）", font=Font(bold=True), align=LEFT_TOP)
    r += 1
    for title in groups_order:
        hexc = GROUP_COLORS.get(title, GROUP_COLORS[FALLBACK_GROUP])
        put(ws, r, 1, title, fill=_fill(hexc), font=Font(bold=True, color="FFFFFF"),
            align=CENTER, comment="色值 %s" % hexc)
        put(ws, r, 2, hexc, align=LEFT_TOP)
        r += 1
    return ws


def sheet_edit(wb, commands, group_of, enum_map, data_src):
    """数据源信息 data_src: {'cmd_range': '下拉数据源!$A$2:$A$69', 'pos': {pos: (col, n)}}"""
    ws = wb.create_sheet(SH_EDIT)
    ws.column_dimensions["A"].width = 16
    for i in range(2, NUM_ARGS + 2):
        ws.column_dimensions[get_column_letter(i)].width = 16

    # 行1：表头（带批注）
    cmd_comment = ("第 0 列『命令类型』：填写命令名，取值见下拉（共 %d 条命令）。\n"
                   "示例：等待 / 输入 / 点图 / 循环开始。\n"
                   "命令名必须与『命令速查』完全一致。" % len(commands))
    put(ws, 1, 1, HEADER_CMD, fill=HEAD_FILL, font=HEAD_FONT, align=CENTER,
        comment=cmd_comment)
    for pos in range(1, NUM_ARGS + 1):
        enums = enum_map.get(pos)
        txt = ("参数%d：第 %d 个参数位。含义随『命令类型』变化，详见『命令速查』。\n"
               "空值可留空或写 None。" % (pos, pos))
        if enums:
            src_col = data_src["pos"].get(pos, (None, 0))[0]
            txt += ("\n本列合并了所有命令在该参数位的枚举取值"
                    "（下拉源：『下拉数据源』%s 列）：%s" % (src_col, " / ".join(enums)))
            txt += "\n示例：%s" % enums[0]
        else:
            txt += "\n本参数位在所有命令中均无固定枚举 → 本列不加下拉，可自由输入。"
        put(ws, 1, pos + 1, "参数%d" % pos, fill=HEAD_FILL, font=HEAD_FONT,
            align=CENTER, comment=txt)

    # 行2：（标题行）占位
    put(ws, 2, 1, TITLE_PLACEHOLDER, font=Font(italic=True, color="6B7280"), align=CENTER)
    for pos in range(1, NUM_ARGS + 1):
        put(ws, 2, pos + 1, None)

    # 行3起：预设示例行
    presets = [
        ("等待", ["2"]),
        ("输入", ["Hello World"]),
        ("按键", ["enter", "1", "0.1"]),
        ("点图", ["login_btn", "0.96"]),
        ("循环开始", ["3"]),
        ("等待", ["0.5"]),
        ("循环结束", []),
        ("如果", ["${found} == 1"]),
        ("结束如果", []),
    ]
    r = 3
    for cmd_name, args in presets:
        hexc = GROUP_COLORS.get(group_of.get(cmd_name, FALLBACK_GROUP),
                                GROUP_COLORS[FALLBACK_GROUP])
        put(ws, r, 1, cmd_name, fill=_fill(hexc), font=Font(bold=True, color="FFFFFF"),
            align=CENTER,
            comment="分组：%s（%s）" % (group_of.get(cmd_name, FALLBACK_GROUP), hexc))
        for pos in range(1, NUM_ARGS + 1):
            val = args[pos - 1] if pos <= len(args) else None
            put(ws, r, pos + 1, val)
        r += 1

    # 预留下拉覆盖的空白行
    for rr in range(r, 3 + EDIT_ROWS):
        for col in range(1, NUM_ARGS + 2):
            ws.cell(row=rr, column=col).border = BORDER

    # ── 数据验证 ──
    last = 2 + EDIT_ROWS  # 表头行(1) + 占位行(2) + EDIT_ROWS
    dv_cmd = DataValidation(type="list", allow_blank=True,
                            formula1=data_src["cmd_range"],
                            errorStyle="warning", showErrorMessage=True,
                            errorTitle="非标准命令名",
                            error="该值不在命令列表中；仍可保留（警告不拦截）。")
    ws.add_data_validation(dv_cmd)
    dv_cmd.add("A3:A%d" % last)

    for pos, (src_col, n) in data_src["pos"].items():
        edit_col = get_column_letter(pos + 1)
        dv = DataValidation(
            type="list", allow_blank=True,
            formula1="'%s'!$%s$2:$%s$%d" % (SH_DATA, src_col, src_col, 1 + n),
            errorStyle="warning", showErrorMessage=True,
            errorTitle="非标准取值",
            error="该值不在合并枚举内；仍可保留（警告不拦截）。")
        ws.add_data_validation(dv)
        dv.add("%s3:%s%d" % (edit_col, edit_col, last))

    ws.freeze_panes = "A3"
    return ws


def _fmt_param(p):
    parts = [p.get("name", "")]
    if p.get("kind") == "number":
        parts.append("数值")
    else:
        parts.append("文本")
    if p.get("optional"):
        parts.append("可选")
    if p.get("variadic"):
        parts.append("可变参数")
    if p.get("default") not in (None, ""):
        parts.append("默认=%s" % p["default"])
    if p.get("choices"):
        parts.append("取值[%s]" % "/".join(str(c) for c in p["choices"]))
    return "，".join(parts)


EXAMPLES = {
    "找图": "找图,login_btn,0.96",
    "区域找图": "区域找图,login_btn,0.96,200,150,400,300,True",
    "点图": "点图,submit,0.96",
    "区域点图": "区域点图,submit,0.95,200,150,400,300,True,左,1",
    "按键": "按键,enter,1,0.1",
    "热键": "热键,win,r",
    "输入": "输入,Hello World",
    "写入": "写入,Hello,0.05",
    "等待": "等待,2",
    "坐标": "坐标,500,300,左,1,0.1",
    "悬停": "悬停,400,250",
    "拖拽": "拖拽,600,400",
    "滚轮": "滚轮,-300",
    "按下": "按下,shift",
    "释放": "释放,shift",
    "截屏": "截屏,result,E:/screenshots",
    "复制": "复制",
    "粘贴": "粘贴",
    "如果": "如果,${found} == 1",
    "否则": "否则",
    "结束如果": "结束如果",
    "循环开始": "循环开始,3",
    "循环结束": "循环结束",
    "跳出循环": "跳出循环",
    "设置变量": "设置变量,count,10",
    "读取剪贴板": "读取剪贴板,clip",
    "字符串处理": "字符串处理,src,截取,0:5,dst",
    "数学运算": "数学运算,${a} + ${b},result",
    "代码": "代码,mail.py",
    "Python": "Python,print('hi'),sandbox",
    "识别文字": "识别文字,0,0,800,600,text",
    "等待文字": "等待文字,登录成功,10,出现",
    "点击文字": "点击文字,登录,0.9,左,1",
    "运行工作流": "运行工作流,workflow001.json",
    "工作流变量": "工作流变量,env,prod",
    "打开网页": "打开网页,https://example.com",
    "浏览器点击": "浏览器点击,#submit",
    "浏览器输入": "浏览器输入,#username,admin",
    "等待元素": "等待元素,#result,10,出现",
    "浏览器截图": "浏览器截图,page,E:/shots",
    "浏览器执行JS": "浏览器执行JS,return document.title,title",
    "切换标签页": "切换标签页,1",
    "浏览器读取Cookie": "浏览器读取Cookie,cookie,json",
    "浏览器设置Cookie": "浏览器设置Cookie,cookie,json",
    "开始监听": "开始监听,/api/items,json",
    "等待数据包": "等待数据包,/api/items,resp,20",
    "停止监听": "停止监听",
}


def make_example(c):
    if c.name in EXAMPLES:
        return EXAMPLES[c.name]
    if not c.params:
        return c.name
    vals = []
    for p in c.params[:5]:
        d = p.get("default")
        vals.append(str(d) if d not in (None, "") else "<%s>" % p.get("name", ""))
    return ",".join([c.name] + vals)


def sheet_ref(wb, commands, group_of):
    ws = wb.create_sheet(SH_REF)
    headers = ["序号", "分组", "命令", "参数说明（含枚举与默认值）", "简要说明", "示例"]
    widths = [6, 16, 16, 60, 40, 46]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    for i, h in enumerate(headers, start=1):
        put(ws, 1, i, h, fill=HEAD_FILL, font=HEAD_FONT, align=CENTER)

    prev_group = None
    for idx, c in enumerate(commands, start=1):
        grp = group_of.get(c.name, FALLBACK_GROUP)
        hexc = GROUP_COLORS.get(grp, GROUP_COLORS[FALLBACK_GROUP])
        if c.params:
            pdesc = "；".join(_fmt_param(p) for p in c.params)
        else:
            pdesc = "无参数"
        if c.params_text and c.params_text != pdesc:
            pdesc = "%s\n（签名：%s）" % (pdesc, c.params_text) if c.params_text else pdesc
        row = idx + 1
        put(ws, row, 1, idx, align=CENTER)
        put(ws, row, 2, grp, fill=_fill(hexc),
            font=Font(bold=True, color="FFFFFF"), align=CENTER)
        put(ws, row, 3, c.name, align=CENTER,
            comment="所属分组：%s（%s）" % (grp, hexc))
        put(ws, row, 4, pdesc, align=WRAP)
        put(ws, row, 5, c.description, align=WRAP)
        put(ws, row, 6, make_example(c), align=LEFT_TOP, font=MONO)
        if prev_group is not None and grp != prev_group:
            for col in range(1, len(headers) + 1):
                cell = ws.cell(row=row, column=col)
                cell.border = Border(left=THIN, right=THIN, bottom=THIN,
                                     top=Side(style="medium", color="9CA3AF"))
        prev_group = grp

    last = len(commands) + 1
    ws.auto_filter.ref = "A1:F%d" % last
    ws.freeze_panes = "A2"
    return ws, last


def sheet_datasource(wb, commands, enum_map):
    ws = wb.create_sheet(SH_DATA)
    ws.column_dimensions["A"].width = 18
    put(ws, 1, 1, "命令名（命令类型下拉源）", fill=HEAD_FILL, font=HEAD_FONT, align=CENTER)
    for i, c in enumerate(commands, start=2):
        put(ws, i, 1, c.name)
    cmd_range = "'%s'!$A$2:$A$%d" % (SH_DATA, len(commands) + 1)

    pos_src = {}
    col = 2
    for pos in range(1, NUM_ARGS + 1):
        vals = enum_map.get(pos)
        if not vals:
            continue
        letter = get_column_letter(col)
        put(ws, 1, col, "参数%d 合并枚举" % pos, fill=HEAD_FILL, font=HEAD_FONT,
            align=CENTER, comment="本列 = 所有命令在第 %d 个参数位出现的枚举取值并集（去重）。" % pos)
        for i, v in enumerate(vals, start=2):
            put(ws, i, col, v)
        pos_src[pos] = (letter, len(vals))
        ws.column_dimensions[letter].width = max(14, min(40, max(len(v) for v in vals) + 4))
        col += 1

    return ws, {"cmd_range": cmd_range, "pos": pos_src}


def sheet_colors(wb, groups_order):
    ws = wb.create_sheet(SH_COLOR)
    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 14
    ws.column_dimensions["C"].width = 16
    ws.column_dimensions["D"].width = 60
    for i, h in enumerate(["分组", "色块", "色值", "说明"], start=1):
        put(ws, 1, i, h, fill=HEAD_FILL, font=HEAD_FONT, align=CENTER)
    notes = {
        "基础操作": "找图/点图/按键/输入/等待/鼠标操作等",
        "流程控制": "如果/否则/结束如果/循环开始/循环结束/跳出循环",
        "窗口管理": "激活/关闭/最小化/最大化/等待窗口/窗口坐标",
        "变量与数据处理": "设置变量/读取剪贴板/字符串处理/数学运算",
        "代码执行": "代码（外部文件）/ Python（内联）",
        "OCR 文字识别": "识别文字/等待文字/点击文字",
        "AI 增强": "AI找图/AI识别界面/AI优化建议",
        "工作流": "运行工作流/工作流变量",
        "浏览器自动化": "打开网页/点击/输入/cookie/监听/下载等",
        "其他命令": "未在上述分组中列出的命令（兜底）",
    }
    r = 2
    for title in groups_order:
        hexc = GROUP_COLORS.get(title, GROUP_COLORS[FALLBACK_GROUP])
        put(ws, r, 1, title, align=CENTER, font=Font(bold=True))
        put(ws, r, 2, "", fill=_fill(hexc), align=CENTER)
        put(ws, r, 3, hexc, align=CENTER, font=MONO)
        put(ws, r, 4, notes.get(title, ""), align=WRAP)
        r += 1
    put(ws, r + 1, 1, "说明", font=Font(bold=True))
    put(ws, r + 1, 2,
        "色板与 ACRPA 工作流节点色板一致（src/ui/workflow_view.py 的 _FLOW_COLORS）；"
        "编辑区命令列与命令速查「分组」列按此着色（白字）。", align=WRAP)
    ws.merge_cells(start_row=r + 1, start_column=2, end_row=r + 1, end_column=4)
    return ws


DEMOS = [
    ("场景 1：登录流程（图像识别 + 键盘输入）", [
        ("等待", ["2"]),
        ("找图", ["login_btn", "0.96"]),
        ("区域点图", ["login_btn", "0.96", "200", "150", "400", "300", "True", "左", "1"]),
        ("输入", ["admin"]),
        ("按键", ["tab", "1", "0.1"]),
        ("输入", ["123456"]),
        ("按键", ["enter", "1", "0.1"]),
        ("等待", ["3"]),
        ("点图", ["home_icon", "0.96"]),
    ]),
    ("场景 2：网页表单填写（浏览器自动化）", [
        ("打开网页", ["https://example.com/signup"]),
        ("等待元素", ["#username", "10", "出现"]),
        ("浏览器输入", ["#username", "admin"]),
        ("浏览器输入", ["#email", "test@example.com"]),
        ("浏览器点击", ["#agree"]),
        ("浏览器点击", ["text=注册"]),
        ("等待元素", ["#welcome", "10", "出现"]),
        ("浏览器截图", ["reg_ok", "E:/shots"]),
    ]),
    ("场景 3：数据采集（网络监听 + 循环）", [
        ("打开网页", ["https://example.com/list"]),
        ("开始监听", ["/api/items", "json"]),
        ("循环开始", ["3"]),
        ("按键", ["pagedown", "1", "0.1"]),
        ("等待", ["1"]),
        ("循环结束", []),
        ("等待数据包", ["/api/items", "resp", "20"]),
        ("停止监听", []),
        ("截屏", ["collect", "E:/shots"]),
    ]),
    ("场景 4：条件截图（流程控制）", [
        ("如果", ["${need_shots} == 1"]),
        ("打开网页", ["https://example.com"]),
        ("等待元素", ["#main", "10", "出现"]),
        ("浏览器截图", ["home", "E:/shots"]),
        ("否则", []),
        ("等待", ["0.5"]),
        ("结束如果", []),
    ]),
]


def sheet_demos(wb, group_of):
    ws = wb.create_sheet(SH_DEMO)
    ws.column_dimensions["A"].width = 16
    for i in range(2, NUM_ARGS + 2):
        ws.column_dimensions[get_column_letter(i)].width = 15
    r = 1
    for title, rows in DEMOS:
        c = ws.cell(row=r, column=1, value=title)
        c.font = Font(bold=True, size=12, color="111827")
        c.fill = SEC_FILL
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=NUM_ARGS + 1)
        r += 1
        put(ws, r, 1, HEADER_CMD, fill=HEAD_FILL, font=HEAD_FONT, align=CENTER)
        for pos in range(1, NUM_ARGS + 1):
            put(ws, r, pos + 1, "参数%d" % pos, fill=HEAD_FILL, font=HEAD_FONT, align=CENTER)
        r += 1
        for cmd_name, args in rows:
            hexc = GROUP_COLORS.get(group_of.get(cmd_name, FALLBACK_GROUP),
                                    GROUP_COLORS[FALLBACK_GROUP])
            put(ws, r, 1, cmd_name, fill=_fill(hexc),
                font=Font(bold=True, color="FFFFFF"), align=CENTER,
                comment="分组：%s（%s）" % (group_of.get(cmd_name, FALLBACK_GROUP), hexc))
            for pos in range(1, NUM_ARGS + 1):
                val = args[pos - 1] if pos <= len(args) else None
                put(ws, r, pos + 1, val)
            r += 1
        r += 1
    return ws


def sheet_workflow(wb):
    ws = wb.create_sheet(SH_WF)
    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 110
    r = 1
    c = ws.cell(row=r, column=1, value="工作流编排（运行工作流 / 工作流变量）")
    c.font = Font(bold=True, size=13, color="111827")
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=2)
    r += 2

    r = section(ws, r, "一、两条工作流命令")
    r = kv_row(ws, r, "运行工作流", "参数：工作流文件(.json)。在脚本中调用多脚本编排；例如 运行工作流,workflow001.json")
    r = kv_row(ws, r, "工作流变量", "参数：变量名, 值。设置工作流级共享变量，供各子脚本以 ${名} 读取。")
    r += 1

    r = section(ws, r, "二、工作流文件结构（参考 src/workflow.py 与 template/workflow001.json）")
    r = kv_row(ws, r, "顶层", "{\"name\": 工作流名称, \"steps\": [ ...步骤... ]}")
    r = kv_row(ws, r, "script", "{\"type\":\"script\", \"path\":\"脚本.xls\"} —— 执行一个脚本文件")
    r = kv_row(ws, r, "command", "{\"type\":\"command\", \"cmd\":\"坐标\", \"params\":[9 个参数]} —— 内联一条命令")
    r = kv_row(ws, r, "variable", "{\"type\":\"variable\", \"var_name\":\"x\", \"var_value\":\"10\"} —— 设置工作流变量")
    r = kv_row(ws, r, "condition", "{\"type\":\"condition\", \"if\":\"${ok}\", \"then\":{...}, \"else\":{...}}")
    r = kv_row(ws, r, "loop", "{\"type\":\"loop\", \"times\":\"3\" 或 \"${x} < 5\", \"steps\":[...]} —— 循环（支持条件循环）")
    r = kv_row(ws, r, "parallel", "{\"type\":\"parallel\", \"steps\":[...]} —— 并行执行子步骤")
    r = kv_row(ws, r, "wait", "{\"type\":\"wait\", \"seconds\":5} —— 等待秒数")
    r = kv_row(ws, r, "log", "{\"type\":\"log\", \"text\":\"...\"} —— 写日志")
    r = kv_row(ws, r, "简写", "步骤也可以是字符串：\"script.xls\" 等价 {\"type\":\"script\",\"path\":\"script.xls\"}。")
    r = kv_row(ws, r, "通用字段", "enabled（false 跳过）、comment（注释）、stop_on_error（错误是否中断）。")
    r += 1

    r = section(ws, r, "三、workflow001.json 示例（节选）")
    example = (
        '{\n'
        '  "name": "未命名工作流",\n'
        '  "steps": [\n'
        '    {"type": "parallel", "steps": []},\n'
        '    {"type": "command", "cmd": "区域找图",\n'
        '     "params": ["img.png","0.96","0","0","800","600","True","",""]},\n'
        '    {"type": "loop", "times": "3", "steps": [], "enabled": true},\n'
        '    {"type": "variable", "var_name": "var", "var_value": ""},\n'
        '    {"type": "script", "path": "template/数据采集.xls", "enabled": true}\n'
        '  ]\n'
        '}'
    )
    lines = example.split("\n")
    for i, ln in enumerate(lines):
        put(ws, r + i, 2, ln, font=MONO, align=LEFT_TOP)
    r += len(lines) + 1

    r = section(ws, r, "四、控制流图（CFG）与静态检查")
    r = kv_row(ws, r, "图形语义", "开始/结束（圆角）、脚本、命令、条件（菱形双出口）、并行（容器框）、循环（范围框+回边）。")
    r = kv_row(ws, r, "交互", "端口拖拽连线；点选连线 Delete 删除；成环自动标记回边。")
    r = kv_row(ws, r, "静态检查", "不可达步骤、孤立步骤、条件缺出口、循环风险、重复连线、路径数过多、环路复杂度超阈值。")
    r = kv_row(ws, r, "注意", "图编辑为视图级；结构变化会重新提升为图，临时连线编辑随之重置。")
    return ws


def sheet_hotkeys(wb):
    ws = wb.create_sheet(SH_KEY)
    ws.column_dimensions["A"].width = 20
    ws.column_dimensions["B"].width = 22
    ws.column_dimensions["C"].width = 70
    for i, h in enumerate(["范围", "快捷键", "说明"], start=1):
        put(ws, 1, i, h, fill=HEAD_FILL, font=HEAD_FONT, align=CENTER)

    data = [
        ("全局（可配置）", "F5", "执行默认配置（配置项 hotkey_run）"),
        ("全局（可配置）", "F10", "单步执行"),
        ("全局（可配置）", "F6", "暂停 / 继续执行（配置项 hotkey_pause）"),
        ("全局（可配置）", "Shift+F5", "取消执行（配置项 hotkey_stop）"),
        ("全局（可配置）", "Alt+Shift+F4", "强制关闭"),
        ("全局（可配置）", "Ctrl+Shift+A", "截图"),
        ("脚本编辑区", "Ctrl+K", "命令库（按领域分组 + 模糊搜索，回车写入当前行）"),
        ("脚本编辑区", "F2", "编辑当前行（命令下拉 + 参数表单）"),
        ("脚本编辑区", "双击单元格", "就地编辑"),
        ("脚本编辑区", "Ctrl+D / Delete", "复制选中行 / 删除选中行"),
        ("脚本编辑区", "Ctrl+/", "注释 / 取消注释选中行"),
        ("脚本编辑区", "Alt+↑ / Alt+↓", "上移 / 下移选中行"),
        ("脚本编辑区", "Ctrl+C / Ctrl+V", "复制 / 粘贴选中行"),
        ("脚本编辑区", "Ctrl+Z / Ctrl+Y", "撤销 / 重做"),
        ("脚本编辑区", "Ctrl+滚轮 或 Ctrl+=/Ctrl+-", "编辑器字号缩放；Ctrl+0 复位字号"),
        ("工作流画布", "Ctrl+滚轮", "以光标为锚点缩放（0.4×–2.5×）"),
        ("工作流画布", "Shift+滚轮 / 中键拖拽", "水平滚动 / 平移"),
        ("工作流画布", "F / Ctrl+0", "适应窗口 / 重置缩放"),
        ("工作流画布", "端口拖拽 / Delete", "建立连线 / 删除选中连线"),
        ("工作流画布", "Ctrl+Z / Ctrl+Y", "撤销 / 重做图编辑"),
    ]
    r = 2
    for scope, key, desc in data:
        put(ws, r, 1, scope, align=CENTER)
        put(ws, r, 2, key, align=CENTER, font=Font(bold=True))
        put(ws, r, 3, desc, align=WRAP)
        r += 1
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = "A1:C%d" % (r - 1)
    put(ws, r + 1, 1, "来源", font=Font(bold=True))
    put(ws, r + 1, 2, "res/help/10-快捷键.md（可配置项见『设置 → 高级设置』）", align=WRAP)
    ws.merge_cells(start_row=r + 1, start_column=2, end_row=r + 1, end_column=3)
    return ws


def sheet_format(wb):
    ws = wb.create_sheet(SH_FMT)
    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 110
    r = 1
    c = ws.cell(row=r, column=1, value="脚本格式规范与排错")
    c.font = Font(bold=True, size=13, color="111827")
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=2)
    r += 2

    r = section(ws, r, "一、.xls / .xlsx 10 列约定")
    r = kv_row(ws, r, "行 0", "表头：命令类型, 参数1, 参数2, ..., 参数9")
    r = kv_row(ws, r, "行 1", "%s（ACRPA 读表时固定跳过前 2 行）" % TITLE_PLACEHOLDER)
    r = kv_row(ws, r, "行 2 起", "数据行：第 0 列 = 命令名，第 1..9 列 = 参数1..参数9")
    r = kv_row(ws, r, "读表规则", "跳过第 0 列为空的行；参数自动补齐/裁剪为 9 个。")
    r += 1

    r = section(ws, r, "二、.acrpas（JSON 一等格式）")
    r = kv_row(ws, r, "结构", "{\"schema\":1, \"rows\":[{\"cmd\":\"等待\",\"args\":[\"2\",\"\",...9 项]}]}")
    example = (
        '{\n'
        '  "schema": 1,\n'
        '  "name": "示例脚本",\n'
        '  "created": "2025-01-01T00:00:00",\n'
        '  "meta": {},\n'
        '  "rows": [\n'
        '    {"cmd": "等待", "args": ["2", "", "", "", "", "", "", "", ""]},\n'
        '    {"cmd": "输入", "args": ["Hello", "", "", "", "", "", "", "", ""]}\n'
        '  ],\n'
        '  "vars": {},\n'
        '  "images": []\n'
        '}'
    )
    for i, ln in enumerate(example.split("\n")):
        put(ws, r + i, 2, ln, font=MONO, align=LEFT_TOP)
    r += len(example.split("\n")) + 1
    r = kv_row(ws, r, "args", "每行恰好 9 个字符串；缺省会自动补齐。")
    r = kv_row(ws, r, "兼容", "args 支持 dict（按声明序取值）；行也可写成 [\"cmd\", arg1, ...]。")
    r += 1

    r = section(ws, r, "三、注释约定")
    r = kv_row(ws, r, "文本模式", "ACRPA 编辑器的文本输入模式：列以 | 分隔，以 # 开头的行为注释，执行时忽略。")
    r = kv_row(ws, r, "Excel 模式", "Excel 脚本不使用 | 分隔（用单元格分列）；如需注释请留空后单独备注。")
    r += 1

    r = section(ws, r, "四、空值约定")
    r = kv_row(ws, r, "推荐", "单元格留空。")
    r = kv_row(ws, r, "等价写法", "字面量 None —— 载入时归一为 \"\"。")
    r = kv_row(ws, r, "禁止", "多余的逗号 / 空引号 \"\"（Excel 模式不需要它们）。")
    r += 1

    r = section(ws, r, "五、常见错误提示")
    for k, v in [
        ("字段不足", "Excel 模式请勿手写逗号分隔；用单元格分列即可。"),
        ("命令名拼错", "命令名须与『命令速查』完全一致（如『循环开始』而非『循环』）。"),
        ("参数错位", "参数顺序与『命令速查』一致；跳过的参数位必须留空占位。"),
        ("图片路径", "图片路径可用绝对路径，或相对脚本目录的图片名。"),
        ("权限限制", "代码 / Python 命令受权限（sandbox/trusted/full）与 AST 预检约束。"),
        ("工作流脚本", "工作流步骤脚本当前仅支持 .xls/.xlsx（见 src/script_io.py）。"),
        ("下拉被拒", "本模板下拉对非标准值仅『警告』不拦截，可自由输入。"),
    ]:
        r = kv_row(ws, r, k, v)
    return ws


# ── 回读校验 ──────────────────────────────────────────────────────────
def verify(path, expected):
    """用 openpyxl 重新打开生成的 xlsx 并断言关键项。返回可打印的结果行列表。"""
    out = []
    ok = True

    def check(cond, msg):
        nonlocal ok
        out.append(("  [OK] " if cond else "  [FAIL] ") + msg)
        if not cond:
            ok = False

    wb = openpyxl.load_workbook(path)
    out.append("回读校验：%s" % path)

    names = wb.sheetnames
    check(names == SHEET_ORDER, "工作表齐全且顺序一致: %s" % names)

    ws = wb[SH_EDIT]
    header = [ws.cell(row=1, column=i).value for i in range(1, 11)]
    check(header[0] == HEADER_CMD, "编辑区第 0 列表头 == 命令类型（实际 %r）" % header[0])
    check(header[1:] == ["参数%d" % p for p in range(1, 10)],
          "编辑区参数表头为 参数1..参数9（实际 %r）" % header[1:])
    check(ws.cell(row=2, column=1).value == TITLE_PLACEHOLDER,
          "行 1 为占位行（实际 %r）" % ws.cell(row=2, column=1).value)
    # 批注
    n_comments = sum(1 for i in range(1, 11) if ws.cell(row=1, column=i).comment)
    check(n_comments == 10, "编辑区表头批注数 == 10（实际 %d）" % n_comments)
    # 配色
    fill0 = ws.cell(row=3, column=1).fill
    has_color = fill0 is not None and fill0.fgColor is not None and \
        str(fill0.fgColor.rgb).upper().endswith("3B82F6")
    check(has_color, "预设行命令列已写入分组配色（实际 %s）"
          % (fill0.fgColor.rgb if fill0 and fill0.fgColor else None))
    # 数据验证
    dvs = ws.data_validations.dataValidation
    cmd_dvs = [d for d in dvs if d.formula1 and str(d.formula1).startswith("'%s'!$A$2" % SH_DATA)]
    check(bool(cmd_dvs), "命令类型列数据验证引用『下拉数据源』A2:A%d" % (len(expected["commands"]) + 1))
    n_param_dv = len([d for d in dvs if d.sqref and any(
        get_column_letter(c) in str(d.sqref) for c in range(2, 11))])
    check(n_param_dv == len(expected["enum_map"]),
          "参数列数据验证数 == 合并枚举参数位数（%d，实际 %d）"
          % (len(expected["enum_map"]), n_param_dv))

    ws_ref = wb[SH_REF]
    n_rows = sum(1 for r in range(2, ws_ref.max_row + 1)
                 if ws_ref.cell(row=r, column=3).value)
    check(n_rows == expected["total"], "命令速查行数 == 命令总数（%d，实际 %d）"
          % (expected["total"], n_rows))
    check(ws_ref.freeze_panes == "A2" and bool(ws_ref.auto_filter.ref),
          "命令速查已冻结首行并加筛选")

    ws_data = wb[SH_DATA]
    n_cmds = sum(1 for r in range(2, ws_data.max_row + 1)
                 if ws_data.cell(row=r, column=1).value)
    check(n_cmds == expected["total"], "下拉数据源命令名数 == 命令总数（%d，实际 %d）"
          % (expected["total"], n_cmds))

    ws_color = wb[SH_COLOR]
    swatch_ok = False
    for r in range(2, ws_color.max_row + 1):
        f = ws_color.cell(row=r, column=2).fill
        if f and f.fgColor and f.fgColor.rgb and str(f.fgColor.rgb).upper().endswith("3B82F6"):
            swatch_ok = True
            break
    check(swatch_ok, "配色图例含色块（基础操作 #3B82F6）")

    out.append("回读校验结果：%s" % ("全部通过" if ok else "存在失败项"))
    return ok, out


def main():
    commands, group_of, groups_order = load_registry()
    enum_map = build_enum_map(commands)

    wb = openpyxl.Workbook()
    wb.remove(wb.active)

    sheet_guide(wb, commands, group_of, groups_order, enum_map)
    # 先建数据源以取得引用范围，再建编辑区
    ws_data, data_src = sheet_datasource(wb, commands, enum_map)
    wb.move_sheet(SH_DATA, offset=2)  # 放到编辑区/速查之后
    sheet_edit(wb, commands, group_of, enum_map, data_src)
    ws_ref, ref_last = sheet_ref(wb, commands, group_of)
    sheet_colors(wb, groups_order)
    sheet_demos(wb, group_of)
    sheet_workflow(wb)
    sheet_hotkeys(wb)
    sheet_format(wb)

    # 规范顺序
    wb._sheets = [wb[n] for n in SHEET_ORDER]

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    wb.save(OUT_PATH)

    print("=" * 60)
    print("ACRPA 脚本编辑模板生成摘要")
    print("=" * 60)
    print("输出路径      : %s" % OUT_PATH)
    print("工作表数      : %d  ->  %s" % (len(wb.sheetnames), " / ".join(wb.sheetnames)))
    print("命令总数      : %d" % len(commands))
    print("分组数        : %d  ->  %s" % (len(groups_order), " / ".join(groups_order)))
    print("合并枚举参数位: %d  ->  %s" % (
        len(enum_map), " / ".join("参数%d(%d项)" % (p, len(v)) for p, v in sorted(enum_map.items()))))
    print("编辑区预设行  : %d 行；下拉覆盖至第 %d 行" % (9, 2 + EDIT_ROWS))
    print("数据验证      : 命令类型 1 个 + 参数列 %d 个" % len(enum_map))

    ok, lines = verify(OUT_PATH, {"commands": commands, "enum_map": enum_map,
                                  "total": len(commands)})
    print("-" * 60)
    for ln in lines:
        print(ln)
    print("-" * 60)
    print("总体结论      : %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
