# -*- coding: utf-8 -*-
"""GitHub Pages (Jekyll) 液体语法守卫 —— 被渲染的 .md/.html 里不许出现 ``{{`` / ``{%``。

为什么需要这条守卫
------------------
本仓库的 Pages 站点走内置的 ``pages build and deployment`` 流水线
(github-pages v232 + jekyll v3.10)。关键在于它启用了
``jekyll-optional-front-matter``: **没有 front matter 的 .md 同样会被当页面渲染**,
因此正文里任何 ``{{`` / ``{%`` 都会先交给 Liquid 解析 ——

  * 未闭合 (例如 ``$`` 后接三个左花括号) → ``Liquid Exception: Variable ... was
    not properly terminated`` → **整次构建失败**, 站点静默停在旧版本;
  * 闭合但变量不存在 (例如双花括号包住 ``literal``) → 该段文字在网页上被
    吞成空串, 构建却是绿的, 更难发现。

两者都不是"文档写错", 而是 Jekyll 的渲染副作用, 人工 review 抓不住。
所以把它固化成静态门禁: 扫全仓 .md/.html, 命中即失败并给出 ``file:line``。

不在扫描范围: .py / .json / .txt / 二进制 —— Jekyll 不渲染这些扩展名
(本文件自己的 docstring 就写了 ``{{``, 正是这个道理)。

合法例外怎么写: 确实需要在正文展示 Liquid 字面量时, 用
``{% raw %}...{% endraw %}`` 包起来, 或改用文字描述(例如"三个连续左花括号")。

用法: python tools/_test_pages_liquid.py     退出码 0=干净, 1=有命中
"""
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Jekyll 会渲染的扩展名 (index.html / docs/*.md / README.md ...)
SCAN_EXT = (".md", ".markdown", ".html", ".htm")

# 不参与站点构建的目录: 版本库元数据、虚拟环境、构建产物、运行时产物
SKIP_DIRS = {
    ".git", ".venv", "venv", "__pycache__", "_site", "node_modules",
    "build", "dist", "backups", "logs", ".vscode", ".idea", ".workbuddy",
}

# 单个文件最多报告的命中行数, 防止一屏刷爆 CI 日志
MAX_HITS_PER_FILE = 5


def iter_files():
    """按稳定顺序产出待扫描的 .md/.html 绝对路径。"""
    for root, dirs, files in os.walk(BASE):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for name in sorted(files):
            if name.lower().endswith(SCAN_EXT):
                yield os.path.join(root, name)


def scan_file(path):
    """→ [(行号, 行文本)]; 文件读不到时返回 None。"""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return [((no), line.rstrip("\n"))
                    for no, line in enumerate(fh, 1)
                    if "{{" in line or "{%" in line]
    except OSError:
        return None


def rel(path):
    return os.path.relpath(path, BASE).replace("\\", "/")


def main():
    scanned = 0
    failed = 0
    unreadable = 0
    for path in iter_files():
        scanned += 1
        hits = scan_file(path)
        if hits is None:
            unreadable += 1
            print("[WARN] 读不到, 已跳过: {}".format(rel(path)))
            continue
        if not hits:
            continue
        failed += 1
        print("[FAIL] {} 命中 {} 行 (最多显示 {} 行)".format(
            rel(path), len(hits), MAX_HITS_PER_FILE))
        for no, line in hits[:MAX_HITS_PER_FILE]:
            print("       :{}  {}".format(no, line.strip()[:160]))

    print("-" * 66)
    if failed:
        print("Pages 液体语法守卫: 扫描 {} 个文件, {} 个命中".format(scanned, failed))
        print("Pages 构建会失败或静默吞字。改法: 用文字描述花括号, 或包 {% raw %}。")
        return 1
    print("Pages 液体语法守卫: 扫描 {} 个文件, 0 命中"
          "{}".format(scanned, "" if not unreadable else
                      " ({} 个读不到)".format(unreadable)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
