# -*- coding: utf-8 -*-
"""帮助系统重构 · 阶段4：外置文档命令清单生成器 + 文档漂移守卫。

背景
----
命令的权威来源是 `commands.list_all()`（注册表，当前 68 条），分组映射来自
`res/help/command_groups.json`。应用内「帮助 → 命令速查」已由注册表**自动生成**；
根目录 `使用说明.txt` 是外置副本，正文按「一：找图 … 四十五：跳出循环」逐条手写，
与注册表容易漂移（缺新命令、参数默认值各说各话）。

本工具职责
----------
    (默认)            从注册表 + 分组映射生成「分组命令表」（列：命令名 | 描述 | 参数）→ stdout
    --write <path>    把命令表写入文件（UTF-8）
    --check [path]    漂移守卫：校验 注册表命令集合 ⊆ 文档命令名集合（默认 使用说明.txt）；
                      缺失打印 [FAIL] 并非零退出，全部命中打印 [OK] 退出 0
    --apply [path]    就地重排文档命令区：加「权威来源」横幅 + 分组命令清单 + 补充说明
                      （严格按原文件编码写回，不转码、不动文末更新日志区）
    --detect [path]   探测目标文档编码（调试用）

设计约束
--------
  * 只读注册表与 command_groups.json，绝不修改 `commands.py` / `res/help/*`。
  * 写文档时**必须**使用探测到的原编码，避免乱码或整文件转码。
"""
import argparse
import json
import os
import sys
import unicodedata

# ── 路径：项目根 + src（导入 commands / version_info） ──
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)
SRC = os.path.join(BASE, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import commands          # noqa: E402  命令注册表（唯一权威来源）
import version_info      # noqa: E402  版本号（横幅「最后同步版本」用）

DEFAULT_DOC = os.path.join(BASE, "使用说明.txt")
GROUPS_JSON = os.path.join(BASE, "res", "help", "command_groups.json")

# 横幅中的稳定标记（用于 --apply 幂等判断 / --check 定位命令区）
BANNER_MARK = "命令清单权威来源声明"

# 编码探测优先级：先 UTF-8（带/不带 BOM），再 GB 系，最后 big5/latin-1 兜底
_ENC_CANDIDATES = ("utf-8-sig", "utf-8", "gbk", "gb18030", "big5", "latin-1")

# ── 注册表未包含、仅存于原使用说明.txt 的参数细节 / 默认值（逐条保留） ──
SUPPLEMENT_NOTES = [
    "找图：参数2 识别准确度 选填，默认 0.96，填写范围 0.1~1.0；图片须为 png，参数填不含后缀名（hello.png → hello）。",
    "区域找图：参数3~6 检测区域 左/上/宽/高 选填，默认 0（0 即全屏检测，缩小范围可提速）；参数7 灰度匹配 选填，默认 True，对颜色要求高时设 False。",
    "点图 / 区域点图：点图参数同找图；区域点图在「区域找图」参数后追加 参数8 鼠标按键（默认左）、参数9 点击次数（默认 1）。",
    "按键：按键名用小写（如 ctrl / a / down）；参数2 按下次数 默认 1；参数3 每次间隔 默认 0.1。",
    "坐标：参数3 鼠标按键 默认左；参数4 点击次数 默认 1；参数5 每次间隔 默认 0.1。",
    "滚轮：参数1 滑动距离 选填，默认 -300（负数向下滚动，正数向上滚动）。",
    "等待：参数1 秒数，支持随机范围（如 10 或 1.0-3.0，1.0-3.0 表示随机等待 1~3 秒）。",
    "写入：参数2 按键间隔秒数 选填，默认 0.05；参数3 输入模式 选填 auto/direct/simulate，默认 auto。",
    "截屏：参数1 图片名称为「设置名称 + 日时分秒」；参数2 保存路径 必填，路径中必须使用 /（用 \\ 可能导致路径错误），如 E:/ACRPA/截图。",
    "复制 / 粘贴：内置热键、无需参数（复制 = ctrl+a,ctrl+c；粘贴 = ctrl+a,ctrl+v）。",
    "代码：参数1 仅填文件名（不含后缀），文件必须是 txt，代码中不能定义函数，且只能使用本程序所用到的库。",
    "获取窗口位置：参数2~5 变量名 选填，默认 win_x / win_y / win_width / win_height。",
    "等待窗口：参数2 超时秒数 选填，默认 10；参数3 存在/不存在 选填，默认「存在」。",
    "窗口坐标：参数4 鼠标按键 默认左；参数5 点击次数 默认 1；参数6 点击间隔 默认 0.1。",
    "设置变量：参数2 变量值 支持数字自动转换。",
    "读取剪贴板：参数1 变量名 选填，默认 clipboard。",
    "字符串处理：参数3 操作参数 如 \"0:5\" 或 \"old,new\"；参数4 目标变量名 选填，默认 result。",
    "数学运算：参数1 表达式 支持 ${var} 引用变量；参数2 结果变量名 选填，默认 result。",
    "运行工作流：参数1 工作流文件路径，支持 .json / .yaml 格式。",
    "识别文字：参数5 变量名 选填，默认 ocr_text。",
    "等待文字：参数2 超时秒数 选填，默认 10；参数3 存在/不存在 选填，默认「存在」；参数4~7 搜索区域（左/上/宽/高）选填。",
    "点击文字：参数2 置信度 选填，默认 0.8；参数3 鼠标按键 默认左；参数4~7 搜索区域（左/上/宽/高）选填。",
    "AI找图：参数2 置信度 选填，默认 0.8（范围 0.1~1.0）；参数3 操作类型 选填，默认 click（click 点击 / hover 悬停 / find 仅查找）；参数4 鼠标按键 默认左。",
    "AI识别界面：结果存入 ${前缀}_elements 变量（前缀 选填，默认 ui）。",
    "AI优化建议：参数1 输出模式 选填，默认 log（log 仅日志 / save 保存文件 / var 存入变量）；参数2 变量名 选填，默认 optimize_result（模式=var 时使用）。",
    "如果：参数1 条件表达式 支持 ${var} 引用变量（如 ${count}>10）。",
    "循环开始：参数1 循环次数（数字，如 5）或条件表达式（如 ${i}<10）。",
]

# 命令区中夹带的非命令备注（原文有、且不属命令枚举，替换时保留）
EXTRA_NOTES_TITLE = "其他备注（原文保留）"
EXTRA_NOTES = [
    "工作流Tab支持可视化流程图（节点+连线+拖拽排序），可以在四种步骤类型中切换：",
    "- 脚本：顺序执行单个.xls脚本",
    "- 并行：同时执行多个子步骤",
    "- 条件：if/else分支执行",
    "- 等待：延时等待N秒",
]


# ======================================================================
# 注册表 / 分组
# ======================================================================
def load_registry():
    """返回 commands.list_all() 的 (name, desc, params) 列表（保持注册顺序）。"""
    return [(n, d, p) for (n, d, p, _h) in commands.list_all()]


def load_groups():
    """读取 command_groups.json → (fallback_title, [(title, [names])])。"""
    fallback = "其他命令"
    groups = []
    try:
        with open(GROUPS_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:                       # pragma: no cover - 环境异常
        sys.stderr.write("[WARN] 读取 command_groups.json 失败: {}\n".format(e))
        return fallback, groups
    fallback = data.get("_fallback_title") or fallback
    for g in data.get("groups", []) or []:
        title = g.get("title") or fallback
        groups.append((title, list(g.get("commands", []) or [])))
    return fallback, groups


def build_grouped():
    """把注册表按分组映射编排为有序 [(title, [(name, desc, params)])]。

    未在 command_groups.json 列出的命令（含插件新增）落入兜底组，永不丢失；
    分组内命令顺序以注册表顺序为准，注册表未定义的名字在分组中被忽略。
    """
    fallback, groups = load_groups()
    reg = load_registry()
    info = {n: (d, p) for (n, d, p) in reg}
    order = {n: i for i, (n, _d, _p) in enumerate(reg)}

    seen = set()
    out = []
    for title, names in groups:
        entries = []
        for nm in names:
            if nm in info and nm not in seen:
                d, p = info[nm]
                entries.append((nm, d, p))
                seen.add(nm)
        if entries:
            out.append((title, entries))

    leftover = [n for (n, _d, _p) in reg if n not in seen]
    if leftover:
        leftover.sort(key=lambda n: order[n])
        out.append((fallback, [(n,) + info[n] for n in leftover]))
    return out


# ======================================================================
# 文本对齐（中日韩宽字符按 2 列计）
# ======================================================================
def disp_width(s):
    w = 0
    for ch in s:
        w += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return w


def _pad(s, width):
    return s + " " * max(0, width - disp_width(s))


def render_table(grouped=None):
    """渲染「分组命令表」为纯文本（列：命令名 | 描述 | 参数）。"""
    if grouped is None:
        grouped = build_grouped()
    total = sum(len(entries) for _t, entries in grouped)

    w_name = max([disp_width(n) for _t, es in grouped for (n, _d, _p) in es] + [4])
    w_desc = max([disp_width(d) for _t, es in grouped for (_n, d, _p) in es] + [4])

    lines = []
    lines.append("命令清单（与命令注册表对齐 · 共 {} 条）".format(total))
    lines.append("来源：commands.list_all() + res/help/command_groups.json"
                 "（可用 tools/_gen_help_command_table.py 重新生成）")
    for title, entries in grouped:
        lines.append("")
        lines.append("【{}】".format(title))
        for name, desc, params in entries:
            lines.append("  " + _pad(name, w_name) + "  "
                         + _pad(desc, w_desc) + "  参数: " + params)
    return "\n".join(lines)


# ======================================================================
# 编码探测 / 读写
# ======================================================================
def detect_encoding_bytes(raw):
    """按优先级返回首个可无损解码 raw 的编码名（默认 utf-8）。"""
    for enc in _ENC_CANDIDATES:
        try:
            raw.decode(enc)
            return enc
        except (UnicodeDecodeError, LookupError):
            continue
    return "utf-8"


def read_text(path):
    """读取文件 → (text, enc, newline)。保持原编码与换行风格。"""
    with open(path, "rb") as f:
        raw = f.read()
    enc = detect_encoding_bytes(raw)
    text = raw.decode(enc)
    nl = "\r\n" if "\r\n" in text else "\n"
    return text, enc, nl


def write_text(path, text, enc):
    """按指定编码写回（utf-8-sig 会自动补 BOM）。"""
    with open(path, "wb") as f:
        f.write(text.encode(enc))


# ======================================================================
# 漂移守卫
# ======================================================================
def extract_doc_command_names(text):
    """从文档中提取「作为行首独立词」出现的命令名集合。

    以每行首个空白分隔 token 为准（生成的分组表每行首 token 即命令名）。
    这样「一：找图（…）」「找图：…」这类旧写法不会被误判为命中，
    从而保证守卫真的能发现「文档漏命令」。
    """
    names = set()
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        parts = line.split()
        if parts:
            names.add(parts[0])
    return names


def check_doc(path):
    """校验注册表命令集合 ⊆ 文档命令名集合。返回 0/1。"""
    if not os.path.exists(path):
        print("[FAIL] 目标文档不存在: {}".format(path))
        return 1
    text, enc, _nl = read_text(path)
    registry = [n for (n, _d, _p) in load_registry()]
    found = extract_doc_command_names(text)
    missing = [n for n in registry if n not in found]

    print("[INFO] 目标文档: {} (编码: {})".format(path, enc))
    print("[INFO] 注册表命令 {} 条，文档命中 {} 条".format(
        len(registry), len(registry) - len(missing)))
    if missing:
        print("[FAIL] 文档缺失 {} 条命令: {}".format(len(missing), ", ".join(missing)))
        return 1
    print("[OK]   注册表命令集合 ⊆ 文档命令名集合（文档未漏命令）")
    return 0


# ======================================================================
# 就地重排（加横幅 + 分组清单 + 补充说明）
# ======================================================================
def _banner_lines():
    ver = version_info.get_version() or version_info.VERSION
    tag = ver if str(ver).lower().startswith("v") else "v" + str(ver)
    sep = "=" * 78
    return [
        sep,
        "【重要 · {}】".format(BANNER_MARK),
        sep,
        "本文件（使用说明.txt）中的「命令清单」为外置副本，仅供参考，可能滞后于程序。",
        "命令的唯一权威来源是：应用内「帮助 → 命令速查」——该页面由命令注册表",
        "commands.list_all() 自动生成，永远与程序实际支持的命令保持一致。",
        "如需最新、最准确的命令说明，请以应用内帮助为准。",
        "最后同步版本：{}".format(tag),
        sep,
        "",
    ]


def _supplement_lines():
    lines = [
        "补充说明（注册表未包含、仅存于本文件的参数细节 / 默认值）",
        "=" * 56,
        "以下条目为原使用说明中记录、而命令注册表描述未涵盖的参数细节与默认值，",
        "为避免信息丢失而逐条保留；命令名均与注册表一致。",
        "",
    ]
    for note in SUPPLEMENT_NOTES:
        lines.append("- " + note)
    return lines


def build_command_section():
    """生成替换「命令区」的完整文本块（list[str]，不含换行符）。"""
    lines = []
    lines += _banner_lines()
    lines += render_table().split("\n")
    lines.append("")
    lines += _supplement_lines()
    lines.append("")
    lines.append(EXTRA_NOTES_TITLE)
    lines.append("-" * len(EXTRA_NOTES_TITLE))
    lines += EXTRA_NOTES
    lines.append("")
    return lines


def apply_doc(path):
    """就地重排文档命令区。返回统计 dict。使用原编码写回。"""
    text, enc, nl = read_text(path)
    norm = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = norm.split("\n")

    if any(BANNER_MARK in l for l in lines):
        return {"skipped": True, "enc": enc, "path": path}

    start = next((i for i, l in enumerate(lines) if l.startswith("一：找图")), None)
    end = next((i for i, l in enumerate(lines) if l.startswith("四十五：跳出循环")), None)
    if start is None or end is None or end < start:
        raise SystemExit("[FAIL] 未能在文档中定位命令区边界（一：找图 … 四十五：跳出循环）")

    new_block = build_command_section()
    new_lines = lines[:start] + new_block + lines[end + 1:]
    write_text(path, nl.join(new_lines), enc)

    return {
        "skipped": False,
        "path": path,
        "enc": enc,
        "newline": "CRLF" if nl == "\r\n" else "LF",
        "replaced_from": start + 1,          # 1-based
        "replaced_to": end + 1,
        "registry_count": len(load_registry()),
        "supplement_count": len(SUPPLEMENT_NOTES),
    }


# ======================================================================
# CLI
# ======================================================================
def main(argv=None):
    p = argparse.ArgumentParser(
        description="命令清单生成器 / 使用说明.txt 漂移守卫（阶段4）")
    p.add_argument("--write", metavar="PATH", help="把分组命令表写入文件（UTF-8）")
    p.add_argument("--check", nargs="?", const=DEFAULT_DOC, default=None,
                   metavar="PATH", help="漂移守卫：校验注册表 ⊆ 文档（默认 使用说明.txt）")
    p.add_argument("--apply", nargs="?", const=DEFAULT_DOC, default=None,
                   metavar="PATH", help="就地重排文档命令区（默认 使用说明.txt）")
    p.add_argument("--detect", nargs="?", const=DEFAULT_DOC, default=None,
                   metavar="PATH", help="探测文档编码（调试）")
    args = p.parse_args(argv)

    if args.detect is not None:
        if not os.path.exists(args.detect):
            print("[FAIL] 文件不存在: {}".format(args.detect))
            return 1
        _t, enc, nl = read_text(args.detect)
        print("[INFO] {} 编码: {}  换行: {}".format(
            args.detect, enc, "CRLF" if nl == "\r\n" else "LF"))
        return 0

    if args.check is not None:
        return check_doc(args.check)

    if args.apply is not None:
        res = apply_doc(args.apply)
        if res.get("skipped"):
            print("[SKIP] 文档命令区已含权威来源横幅，跳过（幂等）: {}".format(res["path"]))
            return 0
        print("[OK]   已重排命令区: {}".format(res["path"]))
        print("       原编码: {}  换行: {}".format(res["enc"], res["newline"]))
        print("       替换行区间(1-based): {} - {}".format(
            res["replaced_from"], res["replaced_to"]))
        print("       注册表命令: {} 条  补充说明: {} 条".format(
            res["registry_count"], res["supplement_count"]))
        return 0

    table = render_table()
    if args.write:
        with open(args.write, "w", encoding="utf-8", newline="\n") as f:
            f.write(table + "\n")
        print("[OK]   已写入命令表: {}".format(args.write))
    else:
        print(table)
    return 0


if __name__ == "__main__":
    sys.exit(main())
