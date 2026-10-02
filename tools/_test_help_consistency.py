# -*- coding: utf-8 -*-
"""帮助系统内容层一致性守卫（阶段 0，纯数据、无 GUI）。

覆盖断言（[OK] / [WARN] / [FAIL]，退出码 0 成功 / 1 失败）：
  A. 命令双向一致：get_command_groups() 展开的命令集合 == commands.list_all() 名称集合
     （含兜底组），不多不少。
  B. 章节：不少于 13 章、各章 body 非空、sections.json 的 order 顺序生效。
  C. command_groups.json 为合法 JSON、分组有序、所列命令均存在于注册表
     （未知命令记 [WARN] 而非静默）。
  D. parse_markdown_subset 对样例解析出预期块类型；parse_inline 解析粗体/行内代码/链接。
  E. get_about_info() 字段完整且 qr_path 文件存在。
  F. help_content 可导入且未引入 tkinter 依赖。
  G. 兜底行为：注册表新增（模拟插件）命令会落入兜底分组。
"""
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "src"))

import commands
import help_content

# 期望的 13 个章节标题（顺序即 sections.json 声明顺序）
EXPECTED_TITLES = [
    "快速开始", "命令速查", "脚本编辑", "执行控制", "工作流",
    "浏览器自动化", "NetLink 设备互联", "脚本市场", "设置",
    "常见问题 FAQ", "快捷键", "版本与更新", "联系与支持",
]

_fail = 0
_warn = 0


def ok(msg):
    print("[OK]   " + msg)


def warn(msg):
    global _warn
    _warn += 1
    print("[WARN] " + msg)


def fail(msg):
    global _fail
    _fail += 1
    print("[FAIL] " + msg)


def check(cond, ok_msg, fail_msg):
    if cond:
        ok(ok_msg)
    else:
        fail(fail_msg)
    return cond


# ── A. 命令双向一致 ──
def check_command_consistency():
    registry_names = set(commands.list_names())
    rendered = set()
    groups = help_content.get_command_groups()
    for _title, entries in groups:
        for e in entries:
            rendered.add(e.name)

    missing = registry_names - rendered          # 注册表有、帮助没有
    extra = rendered - registry_names            # 帮助有、注册表没有
    if not missing and not extra:
        ok("命令双向一致：注册表 {} 条 == 帮助渲染 {} 条".format(
            len(registry_names), len(rendered)))
    else:
        if missing:
            fail("帮助缺失命令 {} 条: {}".format(len(missing), sorted(missing)))
        if extra:
            fail("帮助多余命令 {} 条: {}".format(len(extra), sorted(extra)))
    return len(registry_names)


# ── B. 章节齐全 + 顺序 ──
def check_sections():
    sections = help_content.get_sections()
    check(len(sections) >= 13,
          "章节数 {} (>=13)".format(len(sections)),
          "章节数不足: {} (<13)".format(len(sections)))

    ids = [s.id for s in sections]
    if len(set(ids)) != len(ids):
        fail("章节 id 存在重复: {}".format(ids))
    else:
        ok("章节 id 唯一（{} 个）".format(len(ids)))

    empty = [s.id for s in sections if not (s.body or "").strip()]
    check(not empty,
          "全部章节 body 非空",
          "章节 body 为空: {}".format(empty))

    titles = [s.title for s in sections]
    if titles == EXPECTED_TITLES:
        ok("13 章标题与顺序符合预期")
    else:
        fail("章节标题/顺序与预期不符:\n  实际={}\n  预期={}".format(titles, EXPECTED_TITLES))

    # sections.json 的 order 是否真正决定排序
    orders = [s.order for s in sections]
    check(orders == sorted(orders),
          "sections.json 的 order 顺序生效",
          "章节未按 order 升序: {}".format(orders))

    # get_section 单章查询
    first = help_content.get_section("quickstart")
    check(first is not None and first.title == "快速开始",
          "get_section('quickstart') 正常",
          "get_section('quickstart') 返回异常")
    check(help_content.get_section("__no_such__") is None,
          "get_section(不存在) 返回 None",
          "get_section(不存在) 未返回 None")
    return sections


# ── C. command_groups.json 合法性 ──
def check_groups_json():
    data = help_content.read_command_groups_raw()
    if not check(isinstance(data, dict), "command_groups.json 可解析为对象",
                 "command_groups.json 缺失或格式非法"):
        return
    groups = data.get("groups")
    if not check(isinstance(groups, list) and groups, "分组列表非空且有序",
                 "groups 缺失或非列表"):
        return
    ok("分组数量: {}，标题: {}".format(
        len(groups), [g.get("title") for g in groups]))

    registry = set(commands.list_names())
    unknown = []
    for g in groups:
        for name in g.get("commands", []) or []:
            if name not in registry:
                unknown.append(name)
    if unknown:
        warn("command_groups.json 中存在注册表未定义的命令（陈旧项）: {}".format(
            sorted(set(unknown))))
    else:
        ok("command_groups.json 所列命令均在注册表内")
    return groups


# ── D. Markdown 子集解析 ──
def check_markdown():
    sample = (
        "# 一级标题\n"
        "\n"
        "这是一段包含 **粗体**、`行内代码` 与 [链接](docs/x.md) 的正文。\n"
        "\n"
        "- 列表项一\n"
        "- 列表项二\n"
        "\n"
        "1. 有序一\n"
        "2. 有序二\n"
        "\n"
        "| 名称 | 说明 |\n"
        "| --- | --- |\n"
        "| 找图 | 找目标图 |\n"
        "\n"
        "```\n"
        "print('hi')\n"
        "```\n"
        "\n"
        "---\n"
    )
    blocks = help_content.parse_markdown_subset(sample)
    kinds = [b.kind for b in blocks]
    expected = ["heading", "para", "ulist", "olist", "table", "code", "hr"]
    check(kinds == expected,
          "块类型顺序符合预期: {}".format(kinds),
          "块类型不符:\n  实际={}\n  预期={}".format(kinds, expected))

    for b in blocks:
        if b.kind == "heading":
            check(b.level == 1 and b.text == "一级标题",
                  "标题 level/text 正确", "标题解析异常")
        if b.kind == "table":
            check(len(b.rows) == 2 and b.rows[0] == ["名称", "说明"],
                  "表格行解析正确", "表格解析异常: {}".format(b.rows))
        if b.kind == "ulist":
            check(b.items == ["列表项一", "列表项二"],
                  "无序列表解析正确", "无序列表异常: {}".format(b.items))
        if b.kind == "olist":
            check(b.items == ["有序一", "有序二"],
                  "有序列表解析正确", "有序列表异常: {}".format(b.items))
        if b.kind == "code":
            check("print('hi')" in b.text, "代码块解析正确", "代码块异常")

    # 行内：粗体 / 行内代码 / 链接
    spans = help_content.parse_inline("含 **粗**、`码` 与 [文字](docs/x.md) 的行")
    skinds = {s.kind for s in spans}
    check({"bold", "code", "link"} <= skinds,
          "行内解析出 bold/code/link: {}".format(sorted(skinds)),
          "行内解析缺失: {}".format(sorted({"bold", "code", "link"} - skinds)))
    link = [s for s in spans if s.kind == "link"]
    if link:
        check(link[0].target == "docs/x.md",
              "链接 target 正确", "链接 target 异常: {}".format(link[0].target))

    # 段落行内片段已挂载
    para = [b for b in blocks if b.kind == "para"]
    if para:
        pskinds = {s.kind for s in para[0].spans}
        check({"bold", "code", "link"} <= pskinds,
              "段落 spans 含 bold/code/link",
              "段落 spans 缺失: {}".format(pskinds))


# ── E. 关于信息 ──
def check_about():
    info = help_content.get_about_info()
    for key in ("author", "email", "version", "qr_path"):
        if not info.get(key):
            fail("get_about_info() 缺少字段: {}".format(key))
            return
    ok("get_about_info() 字段完整: author={}, version={}".format(
        info["author"], info["version"]))
    check(os.path.isfile(info["qr_path"]),
          "qr_path 指向存在文件: {}".format(info["qr_path"]),
          "qr_path 文件不存在: {}".format(info["qr_path"]))


# ── F. 无 tkinter 依赖 ──
def check_no_tkinter():
    check("tkinter" not in sys.modules,
          "help_content 未引入 tkinter 依赖",
          "检测到 tkinter 已被导入（help_content 可能间接依赖 GUI）")


# ── G. 兜底行为（模拟插件命令） ──
def check_fallback():
    sentinel = "__HELP_FALLBACK_PROBE__"
    commands._registry.append((sentinel, "哨兵命令", "无参数", None))
    try:
        groups = help_content.get_command_groups()
    finally:
        # 移除哨兵，避免影响后续（进程退出前全局状态干净）
        try:
            commands._registry.pop()
        except Exception:
            pass
    hit = any(e.name == sentinel for _t, entries in groups for e in entries)
    check(hit,
          "未映射/插件命令落入兜底分组（不丢失）",
          "未映射命令未出现在任何分组（可能被丢弃）")


def main():
    print("=== 帮助系统内容层一致性校验 (_test_help_consistency.py) ===")
    check_no_tkinter()
    n = check_command_consistency()
    check_sections()
    check_groups_json()
    check_markdown()
    check_about()
    check_fallback()
    print("-" * 64)
    print("注册表命令数: {} | FAIL={} | WARN={}".format(n, _fail, _warn))
    print("结论: {}".format("PASS" if _fail == 0 else "FAIL"))
    return 0 if _fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
