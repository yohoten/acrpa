# -*- coding: utf-8 -*-
"""模块库数据源适配器（纯逻辑 / 零 tkinter / 零第三方依赖）。

职责边界（开发计划 §2.1 S0-03 卡）::

    只做「res/help/*.json → 三级树结构」的纯变换 + 检索 + 失败兜底;
    不建任何 Tk 控件、不读 state、不写盘、不在 import 期读文件。

数据来源（唯一真源, 本模块不硬编码任何命令名/分组名）::

    res/help/command_groups.json   分组 → 命令名的有序映射 (+ icon / tier / order)
    res/help/command_schema.json   命令的结构化契约 (+ description / signature / params)

三级结构::

    分组(icon / tier / order) → 子族(可选) → 命令(cmd / desc / sig / params)

**子族规则**: 分组数据层（S0-02 卡）明确规定「不得新增分组级 ``subgroups`` 之类的键」,
故当前 json **没有任何子族声明** → 树自动退化为两级: 每组 ``subgroups`` 恒为 ``[]``、
每条命令 ``sub`` 恒为 ``""``。本模块不发明分类学; 若将来数据层真的提供子族声明,
只需在 ``_build_group()`` 里读取该键, 调用方 API 完全不变。

失败开放（fail-open）::

    json 缺失 / 解析失败 / 结构非法 → 不抛异常。
        fallback=True  (默认) → 返回 ``FALLBACK_CATEGORIES`` 快照（8 分类 45 动作）
        fallback=False        → 返回**空目录**
    两种情况 ``Catalog.ok`` 均为 ``False`` 且 ``Catalog.reason`` 给出可读原因;
    调用方用 ``ok`` 区分「加载失败」与「确实没有命令」（后者 ``ok=True`` +
    ``command_count == 0``）。

用法::

    from ui import module_catalog as mc
    cat = mc.load_catalog()                 # → Catalog(list[{group,icon,tier,order,subgroups,items}])
    nodes = mc.iter_commands(cat)           # → [(group, cmd), ...]
    hits = mc.search("文件", cat)           # → [SearchHit, ...]（含高亮区间）
    tip = mc.tooltip_text(item)             # 悬停说明: description + 参数签名
"""
import io
import json
import os
import sys
from dataclasses import dataclass
from dataclasses import field as _dc_field     # 别名: SearchHit 有同名属性 field
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

__all__ = [
    # 常量
    "GROUPS_JSON_NAME", "SCHEMA_JSON_NAME", "FALLBACK_TITLE", "FALLBACK_ICON",
    "VALID_TIERS", "DEFAULT_TIER", "FALLBACK_CATEGORIES", "FALLBACK_COMMAND_COUNT",
    # 数据结构
    "Catalog", "SearchHit",
    # 路径解析
    "res_roots", "resolve_res_file",
    # 加载
    "load_catalog", "load_fallback_catalog",
    # 遍历
    "iter_groups", "iter_subfamilies", "iter_commands", "iter_command_items",
    "iter_command_names", "get_command",
    # 检索
    "search", "search_catalog",
    # UI 辅助（纯字符串，不建控件）
    "group_label", "node_label", "tooltip_text",
]

# ══════════════════════════════════════════════════════════════════════
# 常量
# ══════════════════════════════════════════════════════════════════════

GROUPS_JSON_NAME = "command_groups.json"
SCHEMA_JSON_NAME = "command_schema.json"

#: command_groups.json 自带的兜底组标题（S0-01 后应为空组，本模块只在必要时用它）
FALLBACK_TITLE = "其他命令"

#: 兜底/缺省分组图标（Unicode 单字符，不引图片资源，避免打包体积）
FALLBACK_ICON = "▫"

#: 分组分层取值（S0-02 契约）；非法值一律降级为 DEFAULT_TIER
VALID_TIERS = ("core", "advanced", "expert")
DEFAULT_TIER = "core"

#: 兜底快照: 与 workflow_view.py:291-300 的 ``_WF_LIB_CATEGORIES`` **逐字一致**
#: （8 分类 45 动作）。workflow_view 里那一份本卡不动，这里只是等价拷贝。
FALLBACK_CATEGORIES = [
    ("流程控制", ["运行脚本", "循环", "并行分支", "条件判断", "等待", "日志输出", "设置变量"]),
    ("鼠标操作", ["点击坐标", "鼠标悬停", "鼠标拖拽", "滚动滚轮", "相对移动", "按下按键", "释放按键"]),
    ("键盘操作", ["模拟按键", "组合热键", "文本输入", "逐字写入", "复制", "粘贴"]),
    ("识别/OCR", ["查找图片", "区域找图", "点击图片", "区域点图", "OCR识别文字", "等待文字", "点击文字"]),
    ("窗口控制", ["激活窗口", "关闭窗口", "最小化窗口", "最大化窗口", "获取窗口位置", "等待窗口"]),
    ("文件/系统", ["屏幕截图", "打开程序", "浏览文件"]),
    ("变量操作", ["设置变量", "读取剪贴板", "字符串处理", "数学运算"]),
    ("浏览器", ["打开网页", "浏览器点击", "浏览器输入", "等待元素", "浏览器截图"]),
]

#: 兜底快照的动作总数（45）—— 供调用方/测试断言"兜底不是空目录"
FALLBACK_COMMAND_COUNT = sum(len(acts) for _title, acts in FALLBACK_CATEGORIES)


# ══════════════════════════════════════════════════════════════════════
# 数据结构
# ══════════════════════════════════════════════════════════════════════

class Catalog(list):
    """模块库三级树 + 加载元信息。

    ``list`` 本体即 ``[{group, icon, tier, order, subgroups, items}, ...]``
    （S0-03 卡契约 ``list[{group, icon, tier, order, items}]`` 的超集:
    仅多出 ``subgroups`` 空列表以承载"可选子族"这一级）。

    元信息字段：
        ok              是否成功从 json 加载（False = 走了兜底或空目录）
        reason          失败原因（成功时为 ""，可直接展示给用户/写日志）
        source          "json" / "fallback" / "none"
        missing_schema  分组里有、schema 里没有的命令名（正常应为空列表）
    """

    def __init__(self, groups=(), ok=True, reason="", source="json"):
        list.__init__(self, list(groups))
        self.ok = bool(ok)
        self.reason = str(reason or "")
        self.source = str(source or "")
        self.missing_schema = []          # type: List[str]

    # ── 计数 ─────────────────────────────────────────────────────────
    @property
    def failed(self):
        """是否加载失败（``ok`` 的反向别名，便于 UI 层写 ``if cat.failed``）。"""
        return not self.ok

    @property
    def group_count(self):
        """分组数（S0 后应为 11）。"""
        return len(self)

    @property
    def subfamily_count(self):
        """子族数（当前数据层无子族声明 → 恒为 0）。"""
        return sum(len(g.get("subgroups") or ()) for g in self if isinstance(g, dict))

    @property
    def command_count(self):
        """命令总数（S0 后应与 ``len(commands.list_names())`` 相等）。"""
        return sum(len(g.get("items") or ()) for g in self if isinstance(g, dict))

    @property
    def command_names(self):
        """扁平命令名列表（按树内顺序）。"""
        return [it.get("cmd", "") for g in self if isinstance(g, dict)
                for it in (g.get("items") or ()) if isinstance(it, dict)]

    def __repr__(self):
        return "<Catalog source={!r} ok={} groups={} commands={} reason={!r}>".format(
            self.source, self.ok, self.group_count, self.command_count, self.reason)


@dataclass(frozen=True)
class SearchHit:
    """一条检索命中（一个命令最多一条，含首个命中字段与高亮区间）。

    ``field``  首个命中字段，取值 ``cmd`` / ``sub`` / ``group`` / ``params`` / ``params_text``；
                空关键字时为 ``""``（表示"全量返回，非命中"）。
    ``fields`` 该命令**全部**命中字段（UI 可据此多处着色）。
    ``snippet`` 命中字段的原文片段（超长时两端带 ``…``）；
    ``start`` / ``end`` 是命中词在 ``snippet`` 内的半开区间，直接可喂给 Text 的 tag。
    """
    group: str
    cmd: str
    field: str = ""
    snippet: str = ""
    start: int = 0
    end: int = 0
    sub: str = ""
    params_text: str = ""
    fields: Tuple[str, ...] = _dc_field(default_factory=tuple)

    @property
    def matched(self):
        """是否真的命中（空关键字返回的全量条目为 False）。"""
        return bool(self.field)


# ══════════════════════════════════════════════════════════════════════
# 资源路径解析（源码态 + PyInstaller 冻结态）
# ══════════════════════════════════════════════════════════════════════

def res_roots():
    """返回可能存放 ``res/`` 的目录列表（已去重，按优先级）。

    沿用既有写法 ``src/help_content.py:97-113``（同项目唯一一处 res/help 数据读取
    的路径口径）：冻结态 ``sys._MEIPASS/res`` → EXE 同级 ``res`` → 仓库根 ``res``
    → ``src/res``。本文件位于 ``src/ui/``，故仓库根是"向上两级"。
    """
    roots = []                       # type: List[str]
    meipass = None
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        roots.append(os.path.join(meipass, "res"))
    if getattr(sys, "frozen", False):
        try:
            roots.append(os.path.join(
                os.path.dirname(os.path.abspath(sys.executable)), "res"))
        except Exception:
            pass
    try:
        here = os.path.dirname(os.path.abspath(__file__))          # .../src/ui
        roots.append(os.path.join(os.path.dirname(os.path.dirname(here)), "res"))
        roots.append(os.path.join(os.path.dirname(here), "res"))   # .../src/res
    except Exception:
        pass
    unique = []                      # type: List[str]
    for r in roots:
        if r and r not in unique:
            unique.append(r)
    return unique


def resolve_res_file(name):
    """在 ``res/help/`` 各候选根下查找文件；返回首个存在的绝对路径，找不到返回 ``None``。"""
    for root in res_roots():
        try:
            path = os.path.join(root, "help", name)
        except Exception:
            continue
        if os.path.isfile(path):
            return path
    return None


# ══════════════════════════════════════════════════════════════════════
# json 读取与结构解析（全部 fail-open：返回 (值, 原因) 而不抛）
# ══════════════════════════════════════════════════════════════════════

def _load_json(path, label):
    """读取并解析 json → ``(data, err)``；``err`` 非空表示失败（绝不抛异常）。"""
    if not path:
        return None, "未找到 {}".format(label)
    try:
        if not os.path.isfile(path):
            return None, "{} 不存在: {}".format(label, path)
        with io.open(path, "r", encoding="utf-8") as f:
            return json.load(f), ""
    except ValueError as exc:                       # JSONDecodeError 是 ValueError 子类
        return None, "{} 解析失败: {}".format(label, exc)
    except OSError as exc:
        return None, "{} 读取失败: {}".format(label, exc)
    except Exception as exc:                        # 兜底：任何异常都不得炸掉 UI
        return None, "{} 加载异常: {}".format(label, exc)


def _parse_groups(data):
    """``command_groups.json`` → ``[(title, icon, tier, order, [cmd...]), ...]``。

    结构非法时抛 ``ValueError``（由调用方转成"可读原因 + 兜底"）。
    分组间按 ``order`` 排序（稳定排序: 同 order 保持 json 声明顺序）；
    组内命令顺序**原样保留**（既有顺序承载"常用在前"的产品意图）。
    """
    if not isinstance(data, dict):
        raise ValueError("顶层不是对象")
    groups = data.get("groups")
    if not isinstance(groups, list):
        raise ValueError("缺少 groups 数组")
    parsed = []
    for idx, g in enumerate(groups):
        if not isinstance(g, dict):
            continue
        title = str(g.get("title") or "").strip()
        if not title:
            continue
        raw_cmds = g.get("commands")
        if not isinstance(raw_cmds, list):
            raw_cmds = []
        cmds = [str(c) for c in raw_cmds if str(c or "").strip()]
        icon = str(g.get("icon") or FALLBACK_ICON)
        tier = str(g.get("tier") or DEFAULT_TIER)
        if tier not in VALID_TIERS:
            tier = DEFAULT_TIER
        try:
            order = int(g.get("order"))
        except (TypeError, ValueError):
            order = idx + 1
        parsed.append((title, icon, tier, order, cmds))
    if groups and not parsed:
        # 有分组对象但无一合法 → 视为结构非法（走兜底）
        raise ValueError("groups 内没有合法分组")
    parsed.sort(key=lambda t: t[3])     # 稳定排序：只按 order 排分组间
    return parsed


def _parse_schema(data):
    """``command_schema.json`` → ``{命令名: entry}``（命令名含半角空格，必须精确匹配）。"""
    if not isinstance(data, dict):
        raise ValueError("顶层不是对象")
    items = data.get("commands")
    if not isinstance(items, list):
        raise ValueError("缺少 commands 数组")
    out = {}
    for c in items:
        if isinstance(c, dict) and str(c.get("name") or "").strip():
            out[str(c["name"])] = c
    return out


def _build_item(cmd, group_title, entry, fallback=False):
    """构造命令节点 ``{cmd, desc, sig, params, ...}``（``entry`` 为 schema 条目或 None）。"""
    entry = entry if isinstance(entry, dict) else {}
    params = entry.get("params")
    if not isinstance(params, list):
        params = []
    params = [p for p in params if isinstance(p, dict)]
    return {
        "cmd": cmd,                                   # 命令名（注册表原名，含半角空格）
        "desc": str(entry.get("description") or ""),  # 悬停说明用
        "sig": str(entry.get("signature") or ""),     # 归一化签名（含默认值/枚举）
        "params": params,                             # 结构化参数（名称/类型/枚举/默认）
        "params_text": str(entry.get("params_text") or ""),   # 原文参数说明（schema 直出）
        "group": group_title,
        "sub": "",                                    # 子族（数据层无声明 → 恒空）
        "has_schema": bool(entry),
        "has_params": bool(params),
        "variadic": any(bool(p.get("variadic")) for p in params),
        "fallback": bool(fallback),
    }


def _build_group(title, icon, tier, order, items):
    """构造分组节点（``subgroups`` 恒为空列表 = 退化两级）。"""
    return {
        "group": title,
        "icon": icon or FALLBACK_ICON,
        "tier": tier if tier in VALID_TIERS else DEFAULT_TIER,
        "order": order,
        "subgroups": [],      # 数据层无子族声明（S0-02 禁止新增该键）
        "items": items,
    }


# ══════════════════════════════════════════════════════════════════════
# 加载
# ══════════════════════════════════════════════════════════════════════

def load_fallback_catalog(reason="手工兜底"):
    """按 ``FALLBACK_CATEGORIES`` 构造兜底目录（8 组 45 条，``ok=False`` + 原因）。"""
    groups = []
    for idx, (title, actions) in enumerate(FALLBACK_CATEGORIES):
        items = [_build_item(a, title, None, fallback=True) for a in actions]
        groups.append(_build_group(title, FALLBACK_ICON, DEFAULT_TIER, idx + 1, items))
    return Catalog(groups, ok=False, reason=reason, source="fallback")


def load_catalog(groups_json=None, schema_json=None, fallback=True):
    """把两份 json 适配成模块库三级树。

    :param groups_json: 分组 json 路径；``None`` 走 ``res/help/`` 自动解析。
    :param schema_json: schema json 路径；``None`` 走 ``res/help/`` 自动解析。
    :param fallback:    加载失败时是否返回 ``FALLBACK_CATEGORIES`` 快照。
                        ``True``（默认，S0-03 卡契约）→ 45 条兜底动作；
                        ``False`` → 空目录（``len(cat) == 0``），原因仍在 ``cat.reason``。
    :return: :class:`Catalog`（``list`` 子类 + ``ok`` / ``reason`` / ``source`` 元信息）

    本函数**不抛异常**；所有失败都体现在返回值上。
    """
    groups_path = groups_json if groups_json is not None else resolve_res_file(GROUPS_JSON_NAME)
    schema_path = schema_json if schema_json is not None else resolve_res_file(SCHEMA_JSON_NAME)

    gdata, gerr = _load_json(groups_path, GROUPS_JSON_NAME)
    if gerr:
        return load_fallback_catalog(gerr) if fallback else Catalog(
            [], ok=False, reason=gerr, source="none")
    try:
        parsed = _parse_groups(gdata)
    except Exception as exc:
        reason = "{} 结构非法: {}".format(GROUPS_JSON_NAME, exc)
        return load_fallback_catalog(reason) if fallback else Catalog(
            [], ok=False, reason=reason, source="none")

    sdata, serr = _load_json(schema_path, SCHEMA_JSON_NAME)
    schema_map = {}
    if not serr:
        try:
            schema_map = _parse_schema(sdata)
        except Exception as exc:
            serr = "{} 结构非法: {}".format(SCHEMA_JSON_NAME, exc)

    groups = []
    missing = []
    for title, icon, tier, order, cmds in parsed:
        items = []
        for cmd in cmds:
            entry = schema_map.get(cmd)
            if entry is None:
                missing.append(cmd)
            items.append(_build_item(cmd, title, entry))
        groups.append(_build_group(title, icon, tier, order, items))

    # schema 缺失不算致命：分组/命令照出，仅 desc/sig/params 退化（失败开放）
    cat = Catalog(groups, ok=not serr, reason=serr, source="json")
    cat.missing_schema = missing
    return cat


# ══════════════════════════════════════════════════════════════════════
# 遍历
# ══════════════════════════════════════════════════════════════════════

def iter_groups(catalog):
    """遍历分组节点（``catalog`` 本身即分组列表，此处只做类型过滤）。"""
    return [g for g in (catalog or ()) if isinstance(g, dict)]


def iter_subfamilies(group):
    """某分组的子族名列表（当前数据层无子族声明 → 恒为 ``[]``）。"""
    if not isinstance(group, dict):
        return []
    return list(group.get("subgroups") or ())


def iter_commands(catalog):
    """扁平 ``[(group, cmd), ...]``（S0-03 卡契约；供搜索与注册表交叉校验）。"""
    out = []
    for g in iter_groups(catalog):
        title = g.get("group", "")
        for it in (g.get("items") or ()):
            if isinstance(it, dict):
                out.append((title, it.get("cmd", "")))
    return out


def iter_command_items(catalog=None):
    """扁平命令节点列表（每条 item 自带 ``group`` / ``sub``，便于列表视图直接渲染）。"""
    if catalog is None:
        catalog = load_catalog()
    out = []
    for g in iter_groups(catalog):
        title = g.get("group", "")
        for it in (g.get("items") or ()):
            if not isinstance(it, dict):
                continue
            node = dict(it)
            node.setdefault("group", title)
            node.setdefault("sub", "")
            out.append(node)
    return out


def iter_command_names(catalog=None):
    """全部命令名（按树内顺序；与 ``commands.list_names()`` 集合比对用）。"""
    if catalog is None:
        catalog = load_catalog()
    return [name for _group, name in iter_commands(catalog)]


def get_command(cmd, catalog=None):
    """按命令名精确查找命令节点（含半角空格的命令名可正常命中）；未找到返回 ``None``。"""
    if catalog is None:
        catalog = load_catalog()
    for item in iter_command_items(catalog):
        if item.get("cmd") == cmd:
            return item
    return None


# ══════════════════════════════════════════════════════════════════════
# 检索
# ══════════════════════════════════════════════════════════════════════

_SNIPPET_PAD = 8          # 命中词左右各留的上下文字符数


def _snippet(text, pos, kw_len):
    """截取命中片段 → ``(snippet, start, end)``，``start``/``end`` 为片段内命中词区间。"""
    start = max(0, pos - _SNIPPET_PAD)
    end = min(len(text), pos + kw_len + _SNIPPET_PAD)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    body = text[start:end]
    off = len(prefix) + (pos - start)          # 命中词在片段内的起点
    return prefix + body + suffix, off, off + kw_len


def _hit_fields(item, group_title, sub):
    """按优先级产出可被检索的 ``(字段名, 文本)`` 序列（不含 description，见模块文档）。"""
    cmd = str(item.get("cmd") or "")
    if cmd:
        yield "cmd", cmd
    if sub:
        yield "sub", sub
    if group_title:
        yield "group", group_title
    names = " ".join(str(p.get("name") or "") for p in (item.get("params") or [])
                     if isinstance(p, dict) and p.get("name"))
    if names:
        yield "params", names
    ptext = str(item.get("params_text") or "")
    if ptext:
        yield "params_text", ptext


def search(keyword, catalog=None):
    """关键字检索（大小写不敏感）。空关键字 → 全量返回（``SearchHit.matched`` 为 False）。

    命中字段: 命令名 / 参数名 / 参数说明 / 分组名（以及将来可能出现的子族名）。
    每条命令最多产生一条 :class:`SearchHit`，``field`` 为优先级最高的命中字段，
    ``fields`` 列出全部命中字段，``snippet`` / ``start`` / ``end`` 可直接用于高亮。

    :return: ``[SearchHit, ...]``（顺序 = 树内顺序）
    """
    if catalog is None:
        catalog = load_catalog()
    kw = str(keyword or "").strip().lower()
    hits = []
    for g in iter_groups(catalog):
        title = g.get("group", "")
        subs = iter_subfamilies(g)
        for it in (g.get("items") or ()):
            if not isinstance(it, dict):
                continue
            sub = str(it.get("sub") or "")
            cmd = str(it.get("cmd") or "")
            ptext = str(it.get("params_text") or "")
            if not kw:
                hits.append(SearchHit(group=title, cmd=cmd, field="", snippet=cmd,
                                      start=0, end=0, sub=sub, params_text=ptext))
                continue
            matched = []
            best = None
            for fname, text in _hit_fields(it, title, sub):
                pos = text.lower().find(kw)
                if pos < 0:
                    continue
                matched.append(fname)
                if best is None:
                    snip, s, e = _snippet(text, pos, len(kw))
                    best = (fname, snip, s, e)
            if best is None:
                continue
            hits.append(SearchHit(group=title, cmd=cmd, field=best[0], snippet=best[1],
                                  start=best[2], end=best[3], sub=sub,
                                  params_text=ptext, fields=tuple(matched)))
    return hits


#: ``search`` 的同义名（卡里叫"检索"未规定函数名，两个都留以免调用方猜测）
search_catalog = search


# ══════════════════════════════════════════════════════════════════════
# UI 辅助（纯字符串拼接，不碰任何控件）
# ══════════════════════════════════════════════════════════════════════

def group_label(group):
    """分组行文本 ``"◈ 基础操作"``（图标取自 json 的 ``icon``，缺失用 ``▫``）。"""
    if not isinstance(group, dict):
        return str(group or "")
    return "{} {}".format(group.get("icon") or FALLBACK_ICON, group.get("group") or "")


def node_label(item):
    """命令行文本：有参数时拼上签名（``按键  按键名, 次数=1``），无参数只显示命令名。"""
    if not isinstance(item, dict):
        return str(item or "")
    cmd = str(item.get("cmd") or "")
    sig = str(item.get("sig") or "")
    if item.get("has_params") and sig:
        return "{}  {}".format(cmd, sig)
    return cmd


def tooltip_text(item):
    """悬停说明文本: ``description`` + ``参数: 签名``（供 §6.1.1 的 tooltip 直接用）。"""
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
