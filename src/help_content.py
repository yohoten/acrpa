# -*- coding: utf-8 -*-
"""帮助系统 · 内容层（阶段 0）。

本模块只负责「内容」，不含任何 UI 逻辑，**严禁 import tkinter**，可在 headless
环境下独立测试（见 tools/_test_help_consistency.py）。

对外接口（渲染层阶段的唯一事实来源，签名保持稳定）：
    Section                      — 章节数据模型
    Span / Block                 — Markdown 子集解析结果
    CommandEntry                 — 命令条目
    get_sections()               — 按 order 升序返回全部章节
    get_section(section_id)      — 取单个章节（不存在返回 None）
    parse_markdown_subset(text)  — Markdown 子集 → list[Block]
    parse_inline(text)           — 行内样式 → list[Span]
    get_command_groups()         — 命令分组（命令来自 commands.list_all()，含兜底组）
    get_about_info()             — 关于信息（作者/邮箱/版本/二维码路径）
    resolve_doc_path(rel)        — 解析外链文档（docs/*.md）的绝对路径

内容来源：
    res/help/sections.json       — 章节清单（显式顺序）
    res/help/*.md                — 各章节 Markdown 子集正文
    res/help/command_groups.json — 命令→分组映射（有序分组）
    commands.list_all()          — 命令注册表（唯一命令源）
    version_info.get_version()   — 版本号
    docs/releases/*.md           — 版本更新日志（运行时汇总）
"""
import os
import re
import sys
import json
from dataclasses import dataclass, field

import commands
import version_info

# ── 常量 ──
HELP_SUBDIR = "help"                 # res/help
DEFAULT_FALLBACK_TITLE = "其他命令"   # 未映射命令的兜底分组标题
REL_NOTES_DIR = os.path.join("docs", "releases")


# ======================================================================
# 数据模型
# ======================================================================
@dataclass
class Section:
    """帮助章节。"""
    id: str
    title: str
    order: int
    source: str = ""
    body: str = ""


@dataclass
class Span:
    """行内片段：kind ∈ {text, bold, code, link}。"""
    kind: str
    text: str
    target: str = ""       # 仅 link 使用


@dataclass
class Block:
    """结构化内容块。

    kind 取值：heading / para / ulist / olist / code / table / hr
    字段按需填充：level(heading)、items(列表)、rows(表格)、text(段落/代码)、
    lang(代码语言)、spans(段落行内片段)。
    """
    kind: str
    text: str = ""
    level: int = 0
    items: list = field(default_factory=list)
    rows: list = field(default_factory=list)
    target: str = ""
    lang: str = ""
    spans: list = field(default_factory=list)


@dataclass
class CommandEntry:
    """命令速查条目（取自 commands.list_all() 的 name/desc/params）。"""
    name: str
    desc: str
    params: str


# ======================================================================
# 资源路径解析（同时支持开发态与 PyInstaller 冻结态）
# ======================================================================
def _strip_meipass():
    """冻结态下的解压目录（可能为 None）。"""
    return getattr(sys, "_MEIPASS", None) if getattr(sys, "frozen", False) else None


def _res_roots():
    """返回可能存放 res/ 的目录列表（按优先级，已去重）。"""
    roots = []
    meipass = _strip_meipass()
    if meipass:
        roots.append(os.path.join(meipass, "res"))
    if getattr(sys, "frozen", False):
        roots.append(os.path.join(os.path.dirname(os.path.abspath(sys.executable)), "res"))
    here = os.path.dirname(os.path.abspath(__file__))          # .../src
    project_root = os.path.dirname(here)                       # 项目根
    roots.append(os.path.join(project_root, "res"))
    roots.append(os.path.join(here, "res"))
    unique = []
    for r in roots:
        if r and r not in unique:
            unique.append(r)
    return unique


def _find_res_file(*parts):
    """在 res/ 各候选根下查找文件，返回首个存在的绝对路径，找不到返回 None。"""
    for root in _res_roots():
        path = os.path.join(root, *parts)
        if os.path.exists(path):
            return path
    return None


def resolve_res_path(rel):
    """把相对 res/ 的路径解析为绝对路径（不存在返回 None）。"""
    rel = str(rel).replace("/", os.sep).replace("\\", os.sep)
    return _find_res_file(*rel.split(os.sep))


def resolve_doc_path(rel):
    """解析外链文档（如 docs/xxx.md）的绝对路径，找不到返回 None。

    供渲染层实现「打开文件」：Markdown 内以相对项目根的路径书写链接目标。
    """
    rel = str(rel).replace("/", os.sep)
    candidates = []
    meipass = _strip_meipass()
    if meipass:
        candidates.append(meipass)
    if getattr(sys, "frozen", False):
        candidates.append(os.path.dirname(os.path.abspath(sys.executable)))
    here = os.path.dirname(os.path.abspath(__file__))
    candidates.append(os.path.dirname(here))                   # 项目根
    for base in candidates:
        path = os.path.join(base, rel)
        if os.path.exists(path):
            return path
    return None


def _read_text(path):
    """读取文本文件（UTF-8，容错），失败返回空串。"""
    if not path:
        return ""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except Exception:
        return ""


def _load_json(path):
    """读取 JSON 文件，失败返回 None。"""
    if not path:
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


# ======================================================================
# Markdown 子集解析
# ======================================================================
# 支持：标题(#/##/###) · 无序列表(- / *) · 有序列表(1.) · 行内代码(`x`)
#       · 粗体(**x**) · 链接([t](u)) · 代码块(```) · 表格(| a | b |)
#       · 水平线(---)
_INLINE_RE = re.compile(
    r"\*\*(?P<bold>[^*]+?)\*\*"                       # 粗体
    r"|`(?P<code>[^`]+?)`"                            # 行内代码
    r"|\[(?P<ltext>[^\]]+?)\]\((?P<ltarget>[^)]+?)\)"  # 链接
)
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_ULIST_RE = re.compile(r"^[-*]\s+(.*)$")
_OLIST_RE = re.compile(r"^\d+\.\s+(.*)$")
_HR_RE = re.compile(r"^(?:-{3,}|\*{3,}|_{3,})$")
_TABLE_SEP_RE = re.compile(r"^[\s:\-|]+$")


def _split_table_row(line):
    """拆分表格行：去掉首尾竖线后按 | 切分并 strip。"""
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def _is_table_separator(line):
    """判断是否为表格分隔行（仅由 | - : 空格组成且至少含一个 -）。"""
    s = line.strip()
    return ("-" in s) and bool(_TABLE_SEP_RE.match(s))


def parse_inline(text):
    """解析行内样式，返回 list[Span]（kind ∈ text/bold/code/link）。"""
    spans = []
    pos = 0
    for m in _INLINE_RE.finditer(text):
        if m.start() > pos:
            spans.append(Span("text", text[pos:m.start()]))
        if m.group("bold") is not None:
            spans.append(Span("bold", m.group("bold")))
        elif m.group("code") is not None:
            spans.append(Span("code", m.group("code")))
        else:
            spans.append(Span("link", m.group("ltext"), m.group("ltarget")))
        pos = m.end()
    if pos < len(text):
        spans.append(Span("text", text[pos:]))
    return spans


def parse_markdown_subset(text):
    """解析 Markdown 子集为 list[Block]。

    白名单语法：标题 / 无序列表 / 有序列表 / 代码块 / 表格 / 水平线 / 段落。
    段落的行内样式（粗体、行内代码、链接）同时解析到 Block.spans。
    """
    blocks = []
    if not text:
        return blocks
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    n = len(lines)
    i = 0
    while i < n:
        raw = lines[i]
        s = raw.strip()

        # 空行
        if not s:
            i += 1
            continue

        # 代码块 ```
        if s.startswith("```"):
            lang = s[3:].strip()
            i += 1
            buf = []
            while i < n and not lines[i].strip().startswith("```"):
                buf.append(lines[i])
                i += 1
            i += 1  # 跳过结束围栏
            blocks.append(Block(kind="code", text="\n".join(buf), lang=lang))
            continue

        # 水平线
        if _HR_RE.match(s):
            blocks.append(Block(kind="hr"))
            i += 1
            continue

        # 标题
        m = _HEADING_RE.match(s)
        if m:
            blocks.append(Block(kind="heading", level=len(m.group(1)),
                                text=m.group(2).strip()))
            i += 1
            continue

        # 表格：当前行以 | 开头，且下一行是分隔行
        if s.startswith("|") and i + 1 < n and _is_table_separator(lines[i + 1]):
            rows = [_split_table_row(s)]
            i += 2
            while i < n and lines[i].strip().startswith("|"):
                rows.append(_split_table_row(lines[i]))
                i += 1
            blocks.append(Block(kind="table", rows=rows))
            continue

        # 无序列表
        if _ULIST_RE.match(s):
            items = []
            while i < n:
                mm = _ULIST_RE.match(lines[i].strip())
                if not mm:
                    break
                items.append(mm.group(1).strip())
                i += 1
            blocks.append(Block(kind="ulist", items=items))
            continue

        # 有序列表
        if _OLIST_RE.match(s):
            items = []
            while i < n:
                mm = _OLIST_RE.match(lines[i].strip())
                if not mm:
                    break
                items.append(mm.group(1).strip())
                i += 1
            blocks.append(Block(kind="olist", items=items))
            continue

        # 段落：合并连续非空、非块起始行
        buf = [s]
        i += 1
        while i < n:
            nxt = lines[i].strip()
            if (not nxt or nxt.startswith("#") or nxt.startswith("|")
                    or nxt.startswith("```") or _HR_RE.match(nxt)
                    or _ULIST_RE.match(nxt) or _OLIST_RE.match(nxt)):
                break
            buf.append(nxt)
            i += 1
        para = " ".join(buf)
        blocks.append(Block(kind="para", text=para, spans=parse_inline(para)))
    return blocks


# ======================================================================
# 章节
# ======================================================================
def _load_sections_meta():
    """读取 sections.json（章节清单）。"""
    data = _load_json(_find_res_file(HELP_SUBDIR, "sections.json"))
    if isinstance(data, dict):                 # 兼容 {"sections": [...]} 写法
        data = data.get("sections")
    if not isinstance(data, list):
        return []
    return [d for d in data if isinstance(d, dict)]


def get_version_notes_text():
    """汇总 docs/releases/*.md 为 Markdown 子集文本（版本从新到旧）。"""
    base = resolve_doc_path(REL_NOTES_DIR)
    if not base or not os.path.isdir(base):
        return ""
    lines = ["## 版本更新",
             "",
             "按版本从新到旧汇总（数据源：`docs/releases/*.md`）。",
             ""]
    try:
        names = sorted(os.listdir(base))
    except Exception:
        return ""
    for fn in reversed(names):                 # 新版本在前
        if not fn.lower().endswith(".md"):
            continue
        txt = _read_text(os.path.join(base, fn))
        if not txt:
            continue
        title = ""
        summary = ""
        for ln in txt.split("\n"):
            t = ln.strip()
            if not title and t.startswith("# "):
                title = t[2:].strip()
                continue
            if t and not t.startswith("#") and not summary:
                summary = t
        heading = title or os.path.splitext(fn)[0]
        if len(summary) > 300:
            summary = summary[:300] + "…"
        lines.append("### {}".format(heading))
        lines.append("")
        if summary:
            lines.append(summary)
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def get_sections():
    """按 order 升序返回全部章节。"""
    sections = []
    for meta in _load_sections_meta():
        sid = str(meta.get("id") or "")
        title = str(meta.get("title") or sid)
        try:
            order = int(meta.get("order", 0))
        except (TypeError, ValueError):
            order = 0
        fname = str(meta.get("file") or "")
        source = str(meta.get("source") or "")
        body = _read_text(_find_res_file(HELP_SUBDIR, fname)) if fname else ""
        if meta.get("dynamic") == "releases":          # 版本更新：运行时汇总
            generated = get_version_notes_text()
            if generated:
                body = generated
        sections.append(Section(id=sid, title=title, order=order,
                                source=source, body=body))
    sections.sort(key=lambda sec: (sec.order, sec.id))
    return sections


def get_section(section_id):
    """按 id 获取单个章节，不存在返回 None。"""
    for sec in get_sections():
        if sec.id == section_id:
            return sec
    return None


# ======================================================================
# 命令分组
# ======================================================================
def _registry_map():
    """注册表 → {name: (desc, params)} 与注册顺序列表。"""
    mapping = {}
    order = []
    for item in commands.list_all():
        name = item[0]
        desc = item[1] if len(item) > 1 else ""
        params = item[2] if len(item) > 2 else ""
        if name in mapping:
            continue
        mapping[name] = (desc, params)
        order.append(name)
    return mapping, order


def _entry(name, mapping):
    desc, params = mapping.get(name, ("", ""))
    return CommandEntry(name=name, desc=desc, params=params)


def get_command_groups():
    """按分组顺序返回 [(标题, [CommandEntry, ...]), ...]。

    命令取自 commands.list_all()；映射命中归入对应分组，未命中（含插件新增命令）
    落入兜底分组「其他命令」，保证命令集合与注册表**双向一致、不多不少**。
    映射文件缺失/损坏时整体回退为单组「全部命令」。
    """
    mapping, order = _registry_map()
    data = _load_json(_find_res_file(HELP_SUBDIR, "command_groups.json"))

    groups = []
    if isinstance(data, dict) and isinstance(data.get("groups"), list):
        fallback_title = str(data.get("_fallback_title") or DEFAULT_FALLBACK_TITLE)
        mapped = set()
        for grp in data["groups"]:
            if not isinstance(grp, dict):
                continue
            title = str(grp.get("title") or "未命名分组")
            entries = []
            for name in grp.get("commands", []) or []:
                if name in mapping and name not in mapped:
                    entries.append(_entry(name, mapping))
                    mapped.add(name)
            if entries:
                groups.append((title, entries))
        leftovers = [_entry(nm, mapping) for nm in order if nm not in mapped]
        if leftovers:
            groups.append((fallback_title, leftovers))
    else:
        # 降级：映射不可用 → 全部命令单组，UI 不崩、命令不少
        groups.append(("全部命令", [_entry(nm, mapping) for nm in order]))
    return groups


def read_command_groups_raw():
    """返回原始 command_groups.json 数据（供一致性校验使用）。"""
    return _load_json(_find_res_file(HELP_SUBDIR, "command_groups.json"))


# ======================================================================
# 关于信息
# ======================================================================
def get_about_info():
    """返回关于信息：{author, email, version, qr_path}。"""
    qr = _find_res_file("wechat_qrcode.png")
    return {
        "author": "yohoten",
        "email": "yoho12138@aliyun.com",
        "version": version_info.get_version() or getattr(version_info, "VERSION", ""),
        "qr_path": qr or "",
    }
