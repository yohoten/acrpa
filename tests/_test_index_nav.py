# -*- coding: utf-8 -*-
"""index.html / index.en.html 导航结构回归测试。

守住三件事:
1. 导航覆盖率 —— 页面每一屏都必须归属于某个导航项(否则用户点不到);
2. 中英同步 —— 两个文件的导航 href 序列、section 序列必须逐项一致;
3. 无重名 —— 导航文案不得与任何 section 的 h2 标题相同(点「X」却落到「Y」).
"""
import io
import os
import re
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
ZH = os.path.join(ROOT, "index.html")
EN = os.path.join(ROOT, "index.en.html")

# 导航分组: 导航文案 -> 该导航项负责的连续 section 序列。
# 顺序必须与页面自上而下的实际顺序一致, 这样导航就是一份可信的目录。
GROUPS = [
    ("核心特性", ["scenarios", "quickstart"]),
    ("界面与能力", ["screenshots", "features"]),
    ("多机互联", ["netlink"]),
    ("命令与工作流", ["commands", "workflow", "ai", "templates"]),
    ("帮助与联系", ["env", "changelog", "trust", "faq", "contact"]),
]

PASS = FAIL = 0


def check(title, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS {}".format(title))
    else:
        FAIL += 1
        print("  FAIL {} {}".format(title, detail))


def read(path):
    with io.open(path, encoding="utf-8") as f:
        return f.read()


def sections(html):
    """页面内所有 <section id="..."> 的 id, 按出现顺序。"""
    return re.findall(r'<section\s+id="([^"]+)"', html)


def nav_items(html):
    """桌面导航 (cite=nav-links) 的 [(href, text)]。

    只取第一个 nav-links 块; mobile-nav 由调用方单独比对。
    """
    m = re.search(r'<div class="nav-links">(.*?)</div>', html, re.S)
    if not m:
        return []
    out = []
    for href, text in re.findall(r'<a\s+href="#([^"]+)"[^>]*>(.*?)</a>', m.group(1), re.S):
        out.append((href, re.sub(r"<[^>]+>", "", text).strip()))
    return out


def mobile_items(html):
    m = re.search(r'<div class="mobile-nav" id="mobileNav">(.*?)</div>', html, re.S)
    if not m:
        return []
    return re.findall(r'<a\s+href="#([^"]+)"[^>]*>(.*?)</a>', m.group(1), re.S)


def h2_titles(html):
    return [re.sub(r"<[^>]+>", "", t).strip() for t in re.findall(r"<h2>(.*?)</h2>", html, re.S)]


def main():
    zh, en = read(ZH), read(EN)

    print("[1] 导航项数量与目标")
    z_nav, e_nav = nav_items(zh), nav_items(en)
    check("中文导航为 5 项", len(z_nav) == 5, "实际 {}".format(len(z_nav)))
    check("英文导航为 5 项", len(e_nav) == 5, "实际 {}".format(len(e_nav)))
    check("中文导航无空文案", all(t for _, t in z_nav))
    check("英文导航无空文案", all(t for _, t in e_nav))

    print("[2] 中英导航 href 逐项一致")
    z_hrefs = [h for h, _ in z_nav]
    e_hrefs = [h for h, _ in e_nav]
    check("href 序列一致", z_hrefs == e_hrefs, "{} vs {}".format(z_hrefs, e_hrefs))

    print("[3] 移动端导航与桌面端一致")
    for name, html, desktop in (("中文", zh, z_hrefs), ("英文", en, e_hrefs)):
        mob = [h for h, _ in mobile_items(html)]
        check("{}移动端 href 一致".format(name), mob == desktop, "{} vs {}".format(mob, desktop))

    print("[4] 导航分组覆盖全部 section")
    z_secs = sections(zh)
    covered = [sid for _, ids in GROUPS for sid in ids]
    check("分组序列 == 页面 section 序列", covered == z_secs,
          "\n    分组 {}\n    实际 {}".format(covered, z_secs))
    check("分组无重复 section", len(covered) == len(set(covered)))
    check("导航项数 == 分组数", len(z_hrefs) == len(GROUPS))

    print("[5] 每个导航 href 指向该组第一个 section")
    pairs = list(zip(GROUPS, z_hrefs))
    for (label, ids), href in pairs:
        check("「{}」-> #{}".format(label, ids[0]), href == ids[0],
              "导航指向 #{}".format(href))

    print("[6] 导航文案不与 section 标题重名")
    titles = set(h2_titles(zh))
    for label, _ in GROUPS:
        check("「{}」未与 h2 撞名".format(label), label not in titles)
    e_titles = set(h2_titles(en))
    for _, text in e_nav:
        check("「{}」未与 h2 撞名".format(text), text not in e_titles)

    print("[7] 英文版 section 序列与中文一致")
    check("section id 序列一致", sections(en) == z_secs,
          "{} vs {}".format(sections(en), z_secs))

    print("\n{} passed, {} failed".format(PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
