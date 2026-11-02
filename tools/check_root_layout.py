#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""根目录规范检查器 —— 对应 docs/design/根目录规范.md。

用法::

    python tools/check_root_layout.py            # 常规检查
    python tools/check_root_layout.py --json     # 机器可读
    python tools/check_root_layout.py --strict   # WARN 也视为失败（建议 CI 用）
    python tools/check_root_layout.py -v         # 打印通过项

退出码: 0 通过 / 1 有 FAIL（或 --strict 下有 WARN） / 2 环境异常（非 git 仓库）

设计要点
--------
* **棘轮（ratchet）**：历史遗留的数量类问题（ignore 冲突、docs 根 md 数、一次性脚本数）
  记为基线，只允许持平或减少；减少后需手动下调基线常量，防止反弹。
* **引用完整性**：本仓库出现过 ``使用说明.txt`` 改名后 5 处引用悬空的事故，
  故对 ``os.path.join(APP_ROOT|BASE, "...")`` 与发布清单元组做存在性校验。
* 纯 stdlib，无第三方依赖；不修改任何文件。
"""

from __future__ import print_function

import io
import json
import os
import re
import subprocess
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ══════════════════════════════════════════════════════════════════════
# 白名单与基线
# ══════════════════════════════════════════════════════════════════════

#: 永久条目：职责见规范 §2.1。新增必须同时更新本表与规范文档。
ALLOWED_ROOT = {
    # 入口
    "run.py", "build.py", "VERSION", "requirements.txt",
    # 文档 / 协议
    "README.md", "LICENSE", ".gitignore", ".gitattributes",
    "config.example.json",          # 待建（规范 §2.1）
    # 发布资产
    "index.html", "index.en.html", "img",
    # 代码与资源
    "src", "res", "lib", "samples", "hooks_override", "extensions_src",
    # 工程支撑
    "docs", "tools", "tests", ".github",
    # 开发环境
    ".venv", ".vscode", ".codebuddy",
    # 历史条目：待迁出（template/ → samples/、使用说明.md → docs/guide/），迁完从此表删除
    "template", "使用说明.md",
}

#: 运行/构建产物：允许存在，但必须被 git 忽略（规范 §2.2）
RUNTIME_ALLOWED = {
    "config.json", "recent.json", "recent_workflow.json", "script_versions.db",
    ".ipc_key", "logs", "backups", "screenshots", "__pycache__",
    "dist", "build",
}

#: 允许在根目录不存在的"引用目标"（运行时才创建，或本就不该存在）
REF_SKIP = RUNTIME_ALLOWED | {"userdata", "docs", "src", "res", "lib", "tools", "tests", "img"}

#: 兜底候选路径：这些名字由 ``if not os.path.exists(...)`` 守卫，缺失是正常状态
#: （真身在 res/ 或 C:\Program Files\7-Zip\），故不参与存在性判定
REF_OPTIONAL = {"automation.ico", "automation.png", "7z.exe", "ACRPA.spec"}

#: 只有这些文件里的 (src, dst) 元组才当作"发布清单"解析
PAIR_FILES = {"make_release.py"}

#: 棘轮基线 —— 只许下调，不许上调
BASELINE = {
    "vcs.ignore_conflict": 12,   # 既跟踪又匹配忽略的文件数（当前 template/*）
    "docs.root_md": 14,          # docs/ 根下散落 md 数
    "tools.probe_count": 55,     # tools/ 顶层 `_` 前缀一次性脚本数
}

#: 版本号文件名模式（规范 §3：版本只写在 VERSION 与 name= 字段）
VERSION_PAT = re.compile(r"(?<![0-9])v?\d+\.\d+")
#: 临时/备份后缀（规范 §2.3）
TEMP_PAT = re.compile(r"(\.(bak|tmp|orig|old|rej)$)|(~$)|(\(\d\)$)")

#: 引用提取：os.path.join(APP_ROOT|BASE|state.APP_DIR, "NAME")
JOIN_PAT = re.compile(
    r"os\.path\.join\(\s*(?:APP_ROOT|BASE|state\.APP_DIR)\s*,\s*[\"']([^\"']+)[\"']")
#: 引用提取：发布清单元组 ("NAME", "NAME")
PAIR_PAT = re.compile(r"\(\s*[\"']([^\"'\\/]+)[\"']\s*,\s*[\"']\1[\"']\s*\)")
#: 只有带扩展名的捕获才算"文件引用"，避免把 ("visible","visible") 一类普通元组当路径
NAME_EXT_PAT = re.compile(r"\.[A-Za-z0-9]{2,5}$")

SCAN_ROOTS = ["src", "tools", "tests"]
SCAN_FILES = ["build.py", "run.py"]
SCAN_SKIP_DIRS = {"__pycache__", "_archive", "_out", "_ui_screenshots", ".venv",
                  "_smoke_result_phase1", "_smoke_result_phase2",
                  "_smoke_result_regression", "_smoke_result_regression2"}

# ══════════════════════════════════════════════════════════════════════


class Report(object):
    def __init__(self):
        self.items = []

    def add(self, level, code, msg):
        self.items.append({"level": level, "code": code, "msg": msg})

    def fail(self, code, msg):
        self.add("FAIL", code, msg)

    def warn(self, code, msg):
        self.add("WARN", code, msg)

    def ok(self, code, msg):
        self.add("OK", code, msg)

    def count(self, level):
        return sum(1 for i in self.items if i["level"] == level)


def _ls_root():
    try:
        return sorted(os.listdir(BASE))
    except OSError as e:
        print("[FATAL] 无法读取根目录: {}".format(e))
        raise SystemExit(2)


def _git(args):
    """→ (returncode, stdout)；非 git 仓库返回 (None, '')。"""
    try:
        p = subprocess.Popen(["git"] + args, cwd=BASE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out, _ = p.communicate()
        return p.returncode, out.decode("utf-8", "replace")
    except OSError:
        return None, ""


# ── 检查项 ─────────────────────────────────────────────────────────────

def check_whitelist(rep):
    """§2.1：根目录一级条目必须在白名单内。"""
    unknown = []
    for name in _ls_root():
        if name in (".git", ".gitattributes"):   # .git 特殊；.gitattributes 单列检查
            continue
        if name in ALLOWED_ROOT or name in RUNTIME_ALLOWED:
            continue
        unknown.append(name)
    if unknown:
        rep.fail("root.whitelist",
                 "未登记的一级条目 {} 个: {}".format(len(unknown), ", ".join(sorted(unknown))))
    else:
        rep.ok("root.whitelist", "一级条目全部在白名单内")


def check_naming(rep):
    """§2.3 / §3：空格、版本号、临时后缀。"""
    bad = []
    for name in _ls_root():
        if name == ".git":
            continue
        stem = os.path.splitext(name)[0]
        if " " in name:
            bad.append("{}（含空格）".format(name))
        elif VERSION_PAT.search(stem):
            bad.append("{}（文件名含版本号）".format(name))
        elif TEMP_PAT.search(name):
            bad.append("{}（临时/备份后缀）".format(name))
    if bad:
        rep.fail("root.naming", "命名违规 {} 项: {}".format(len(bad), "; ".join(bad)))
    else:
        rep.ok("root.naming", "根目录命名合规")


def check_ignore_conflict(rep):
    """§2.2：不存在"既被跟踪、又匹配忽略规则"的文件。"""
    rc, out = _git(["ls-files", "-i", "-c", "--exclude-standard"])
    if rc is None:
        rep.warn("vcs.ignore_conflict", "非 git 仓库，跳过")
        return
    files = [l for l in out.splitlines() if l.strip()]
    n, base = len(files), BASELINE["vcs.ignore_conflict"]
    if n == 0:
        rep.ok("vcs.ignore_conflict", "无 ignore 与纳管矛盾")
    elif n <= base:
        sample = ", ".join(files[:3])
        rep.warn("vcs.ignore_conflict",
                 "{} 个文件既被跟踪又匹配忽略规则（基线 {}，需清零）: {}…".format(n, base, sample))
    else:
        rep.fail("vcs.ignore_conflict",
                 "{} 个文件既被跟踪又匹配忽略规则，超过基线 {} —— 请勿在整目录忽略下新增跟踪".format(n, base))


def check_gitattributes(rep):
    """§5.2：.gitattributes 必须纳管且含真实规则。"""
    path = os.path.join(BASE, ".gitattributes")
    if not os.path.exists(path):
        rep.fail("vcs.attr_tracked", ".gitattributes 不存在")
        return
    rc, out = _git(["ls-files", ".gitattributes"])
    tracked = bool(out.strip()) if rc is not None else False
    rules = 0
    try:
        with io.open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                s = line.strip()
                if s and not s.startswith("#") and "filter=lfs" not in s:
                    rules += 1
    except OSError:
        pass
    if not tracked:
        rep.fail("vcs.attr_tracked", ".gitattributes 未纳入版本控制（规则不会对其他 clone 生效）")
    elif rules == 0:
        rep.fail("vcs.attr_tracked", ".gitattributes 已纳管但无有效属性规则（{} 条非注释行全是 LFS）".format(rules))
    else:
        rep.ok("vcs.attr_tracked", ".gitattributes 已纳管，{} 条属性规则".format(rules))


def check_root_refs(rep):
    """§5.3：代码中拼接的根路径必须真实存在。"""
    missing = []
    seen = set()
    for root in SCAN_ROOTS:
        rootdir = os.path.join(BASE, root)
        if not os.path.isdir(rootdir):
            continue
        for dirpath, dirnames, filenames in os.walk(rootdir):
            dirnames[:] = [d for d in dirnames if d not in SCAN_SKIP_DIRS]
            for fn in filenames:
                if not fn.endswith(".py"):
                    continue
                fp = os.path.join(dirpath, fn)
                _scan_file(fp, missing, seen)
    for fn in SCAN_FILES:
        fp = os.path.join(BASE, fn)
        if os.path.isfile(fp):
            _scan_file(fp, missing, seen)
    if missing:
        for rel, line, name in missing:
            rep.fail("refs.root_files",
                     "{}:{} 引用根文件 {}，但该文件不存在".format(rel, line, name))
    else:
        rep.ok("refs.root_files", "根路径引用全部可解析")


def _scan_file(fp, missing, seen):
    try:
        with io.open(fp, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return
    rel = os.path.relpath(fp, BASE).replace("\\", "/")
    allow_pair = os.path.basename(fp) in PAIR_FILES
    for lineno, line in enumerate(lines, 1):
        s = line.lstrip()
        if s.startswith("#"):        # 注释里的示例路径不算引用
            continue
        pats = (JOIN_PAT, PAIR_PAT) if allow_pair else (JOIN_PAT,)
        for pat in pats:
            for m in pat.finditer(line):
                name = m.group(1)
                if not NAME_EXT_PAT.search(name):        # 非文件（无扩展名）
                    continue
                if "{" in name or "%" in name:           # 格式化模板
                    continue
                if "/" in name or "\\" in name or name in REF_SKIP or name in REF_OPTIONAL:
                    continue
                key = (rel, name)
                if key in seen:
                    continue
                seen.add(key)
                if not os.path.exists(os.path.join(BASE, name)):
                    missing.append((rel, lineno, name))


def check_spec_left(rep):
    """§2.3：根目录不应残留 *.spec（build.py 每次重建）。"""
    specs = [n for n in _ls_root() if n.endswith(".spec")]
    if specs:
        rep.fail("build.spec_left",
                 "残留 spec {} 个: {}（应改为固定名且不纳管）".format(len(specs), ", ".join(specs)))
    else:
        rep.ok("build.spec_left", "无残留 spec")


def check_docs_root(rep):
    """§6：docs/ 根下 md 数 ≤ 1。"""
    d = os.path.join(BASE, "docs")
    if not os.path.isdir(d):
        rep.warn("docs.root_md", "docs/ 不存在")
        return
    n = len([f for f in os.listdir(d) if f.endswith(".md")])
    base = BASELINE["docs.root_md"]
    if n <= 1:
        rep.ok("docs.root_md", "docs/ 根下 {} 个 md".format(n))
    elif n <= base:
        rep.warn("docs.root_md", "docs/ 根下散落 {} 个 md（基线 {}，目标 ≤1）".format(n, base))
    else:
        rep.fail("docs.root_md", "docs/ 根下散落 {} 个 md，超过基线 {} —— 请归入四个分类目录".format(n, base))


def check_tools_probe(rep):
    """§7.1：tools/ 顶层一次性脚本数（棘轮）。"""
    d = os.path.join(BASE, "tools")
    if not os.path.isdir(d):
        rep.warn("tools.probe_count", "tools/ 不存在")
        return
    n = len([f for f in os.listdir(d)
             if f.endswith(".py") and f.startswith("_")
             and os.path.isfile(os.path.join(d, f))])
    base = BASELINE["tools.probe_count"]
    if n <= base // 2:
        rep.ok("tools.probe_count", "tools/ 顶层一次性脚本 {} 个".format(n))
    elif n <= base:
        rep.warn("tools.probe_count", "tools/ 顶层一次性脚本 {} 个（基线 {}，目标 ≤{}，应移入 probe/）".format(n, base, base // 2))
    else:
        rep.fail("tools.probe_count", "tools/ 顶层一次性脚本 {} 个，超过基线 {} —— 新脚本请直接放 tools/probe/".format(n, base))


CHECKS = [
    check_whitelist,
    check_naming,
    check_spec_left,
    check_ignore_conflict,
    check_gitattributes,
    check_root_refs,
    check_docs_root,
    check_tools_probe,
]

# ══════════════════════════════════════════════════════════════════════


def main(argv):
    as_json = "--json" in argv
    strict = "--strict" in argv
    verbose = "-v" in argv or "--verbose" in argv

    if not os.path.isdir(os.path.join(BASE, "src")):
        print("[FATAL] 未在 ACRPA 仓库根目录运行（缺 src/）")
        return 2

    rep = Report()
    for fn in CHECKS:
        try:
            fn(rep)
        except SystemExit:
            raise
        except Exception as e:                      # 单检查失败不应掩埋其他结论
            rep.fail("check.error", "{} 异常: {}: {}".format(fn.__name__, type(e).__name__, e))

    fails = rep.count("FAIL")
    warns = rep.count("WARN")

    if as_json:
        print(json.dumps({"root": BASE, "fail": fails, "warn": warns,
                          "items": rep.items}, ensure_ascii=False, indent=2))
    else:
        tag = {"FAIL": "[FAIL]", "WARN": "[WARN]", "OK": "[ OK ]"}
        print("根目录规范检查  {}".format(BASE))
        print("-" * 68)
        for it in rep.items:
            if it["level"] == "OK" and not verbose:
                continue
            print("{} {:<22} {}".format(tag[it["level"]], it["code"], it["msg"]))
        if not verbose and fails == 0 and warns == 0:
            print("[ OK ] 全部检查通过")
        print("-" * 68)
        print("FAIL {} / WARN {} / 检查项 {}".format(fails, warns, len(CHECKS)))

    if fails:
        return 1
    if strict and warns:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
