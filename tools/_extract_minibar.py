# -*- coding: utf-8 -*-
"""把 src/ACRPA.py 里的 Mini Bar 集群抽到 src/mini_bar.py (一次性搬迁脚本, 保留以便复现)。

为什么用脚本而不是手改
----------------------
要搬的是 22 个函数 + 17 个模块级变量, 手抄 600 行极易出现"少抄一个分支/改错一个
名字"。脚本按 AST 精确定位行区间, **函数体原样搬运**, 只做三件事:
  1. 计算出被搬代码引用的所有"非局部全局名", 生成注入清单;
  2. 校验每个名字都能在 ACRPA 顶层找到 (找不到就中止, 不生成半成品);
  3. 从 ACRPA.py 删除原定义, 插入 re-export 与 bind 调用。

用法: .venv\\Scripts\\python.exe -X utf8 tools\\_extract_minibar.py [--check]
      --check 只做静态核算 (不改文件), 供回归测试调用。
"""
import argparse
import ast
import builtins
import io
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ACRPA_PATH = os.path.join(BASE, "src", "ACRPA.py")
OUT_PATH = os.path.join(BASE, "src", "mini_bar.py")

CLUSTER_EXACT = {"_create_mini_bar", "_destroy_mini_bar", "_sync_mini_bar_status"}
CLUSTER_PREFIXES = ("_mb_",)
MOVED_VAR_PREFIXES = ("_mb_", "_MB_")
MOVED_VAR_EXACT = {"_mini_bar"}

# 归属 ACRPA 顶层、但被搬走代码引用, 需要 re-export 回 ACRPA 命名空间的名字
REEXPORT = ("_create_mini_bar", "_destroy_mini_bar", "_sync_mini_bar_status",
            "_mb_content_height", "_mb_effective_height", "_MB_ACCENT")

HEADER = '''# -*- coding: utf-8 -*-
"""ACRPA Mini Bar —— 可折叠悬浮条 (从 src/ACRPA.py 抽出的第一个 UI 集群)。

为什么单独成模块
----------------
src/ACRPA.py 曾是一个 5700+ 行、196 个顶层函数、53 处 global 的巨石, 任何改动都得
在整文件里翻找。Mini Bar 是其中边界最清晰的一块: 窗口生命周期 + 定时器 + 位置记忆,
且它的 17 个模块级状态变量在集群外 **0 引用**(已用 AST 核对), 因此作为拆分第一阶段。

依赖注入约定 (与 src/tray.py 一致: 被拆出的模块不反向 import 宿主)
----------------------------------------------------------------
函数体保持原样搬运, 它们引用的宿主符号由 ACRPA.py 启动时经 bind() 注入::

    import mini_bar
    mini_bar.bind(root=root, colors=C, state=state, ...)

代价是"注入清单必须完整"—— 漏一个就是运行期 NameError。因此:

  · tools/_test_minibar_split.py 会静态核对本模块内所有非局部全局名, 要么定义在
    本模块, 要么出现在下面的 INJECTED 清单里;
  · bind() 对清单外的键直接报错, 不会静默吞掉。
"""

import os
import sys
import time
import tkinter
import tkinter.ttk as ttk

# ══════════════════════════════════════════════════════════════════════
# 注入位 (由 ACRPA.py 的 _bind_minibar() 填充, 可重复调用)
# ══════════════════════════════════════════════════════════════════════
'''


def collect(tree):
    """→ (移动的函数节点, 移动的赋值节点, 移动的名字集合)"""
    funcs, assigns, names = [], [], set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and (
                node.name in CLUSTER_EXACT
                or any(node.name.startswith(p) for p in CLUSTER_PREFIXES)):
            funcs.append(node)
            names.add(node.name)
        elif isinstance(node, ast.Assign) and all(isinstance(t, ast.Name) for t in node.targets):
            ids = [t.id for t in node.targets]
            if any(i in MOVED_VAR_EXACT or any(i.startswith(p) for p in MOVED_VAR_PREFIXES)
                   for i in ids):
                assigns.append(node)
                names.update(ids)
    return funcs, assigns, names


def local_names(fn):
    """函数内"局部"名字 (参数 / 赋值 / 嵌套定义 / import / except / 推导式目标)。"""
    local = set()
    for a in ast.walk(fn):
        if isinstance(a, ast.arg):
            local.add(a.arg)
        elif isinstance(a, ast.Name) and isinstance(a.ctx, ast.Store):
            local.add(a.id)
        elif isinstance(a, (ast.FunctionDef, ast.ClassDef, ast.Lambda)) and a is not fn:
            for sub in ast.walk(a):
                if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store):
                    local.add(sub.id)
            if hasattr(a, "name"):
                local.add(a.name)
            if getattr(a, "args", None):
                for arg in a.args.args + a.args.kwonlyargs:
                    local.add(arg.arg)
        elif isinstance(a, ast.ExceptHandler) and a.name:
            local.add(a.name)
        elif isinstance(a, (ast.Import, ast.ImportFrom)):
            for al in a.names:
                local.add((al.asname or al.name).split(".")[0])
        elif isinstance(a, ast.comprehension):
            for t in ast.walk(a.target):
                if isinstance(t, ast.Name):
                    local.add(t.id)
    return local


def top_level_names(tree):
    """ACRPA 模块级可见的名字 → 种类 (func / assign / import 规格)。

    必须穿过 if/try/with 等复合语句 (例如 `APP_ROOT` 是在 `if frozen:` 分支里赋值的),
    但不能钻进函数体 —— 否则会把函数内的局部名当成模块级。
    """
    kinds = {}
    _COMPOUND = (ast.If, ast.Try, ast.With, ast.For, ast.While)

    def walk_body(body):
        for node in body:
            if isinstance(node, ast.FunctionDef):
                kinds[node.name] = ("func",)
            elif isinstance(node, ast.ClassDef):
                kinds[node.name] = ("class",)
            elif isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        kinds[t.id] = ("assign",)
            elif isinstance(node, ast.Import):
                for al in node.names:
                    kinds[(al.asname or al.name).split(".")[0]] = (
                        "import", al.name, al.asname)
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.level == 0:
                    for al in node.names:
                        kinds[al.asname or al.name] = ("from", node.module, al.name,
                                                       al.asname)
            elif isinstance(node, _COMPOUND):
                walk_body(getattr(node, "body", []))
                walk_body(getattr(node, "orelse", []))
                walk_body(getattr(node, "finalbody", []))
                for h in getattr(node, "handlers", []):
                    walk_body(getattr(h, "body", []))
    walk_body(tree.body)
    return kinds


def analyze():
    src = io.open(ACRPA_PATH, encoding="utf-8").read()
    tree = ast.parse(src)
    funcs, assigns, moved = collect(tree)
    kinds = top_level_names(tree)

    externals = {}
    for fn in funcs:
        local = local_names(fn)
        for x in ast.walk(fn):
            if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Load):
                if x.id in local or x.id in moved:
                    continue
                if x.id in dir(builtins) or x.id.startswith("__"):
                    continue
                externals.setdefault(x.id, set()).add(fn.name)

    injectable = sorted(n for n in externals if n in kinds and n not in moved)
    stdlib_imports = [n for n in injectable if kinds[n][0] in ("import", "from")]
    to_inject = sorted(n for n in injectable if kinds[n][0] not in ("import", "from"))
    unknown = sorted(n for n in externals if n not in kinds)

    # 被搬走的名字里, 有谁仍在 ACRPA 顶层被引用 (需要 re-export)
    in_module_refs = {}
    for name in sorted(moved):
        hits = []
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name in moved:
                continue
            if isinstance(node, ast.Assign):
                continue
            for x in ast.walk(node):
                if isinstance(x, ast.Name) and x.id == name:
                    hits.append(getattr(node, "name", "<top-level>"))
        if hits:
            in_module_refs[name] = sorted(set(hits))
    return {"src": src, "tree": tree, "funcs": funcs, "assigns": assigns,
            "moved": moved, "kinds": kinds, "to_inject": to_inject,
            "stdlib_imports": stdlib_imports, "unknown": unknown,
            "in_module_refs": in_module_refs}


def render_module(info):
    """生成 mini_bar.py 正文 (函数体逐行原样搬运)。"""
    lines = info["src"].splitlines(keepends=True)
    out = [HEADER]
    # 1) 沿用 ACRPA 里同样的 import 写法, 而不是简单 `import X`
    #    (例如 `_darken` 是 from utils import _darken, `engine` 是 from engine import engine)
    emitted = set()
    for name in info["stdlib_imports"]:
        spec = info["kinds"][name]
        if spec[0] == "import":
            _, mod, alias = spec
            stmt = "import {}{}".format(mod, " as " + alias if alias else "")
        else:
            _, mod, orig, alias = spec
            stmt = "from {} import {}{}".format(mod, orig, " as " + alias if alias else "")
        if stmt in emitted:
            continue
        emitted.add(stmt)
        out.append(stmt + "\n")
    out.append("\n")
    for name in info["to_inject"]:
        out.append("{} = None\n".format(name))
    out.append("\nINJECTED = (\n")
    for name in info["to_inject"]:
        out.append('    "{}",\n'.format(name))
    out.append(")\n\n\n")
    out.append('''def bind(**kw):
    """注入宿主符号; 可重复调用 (主题/缩放切换后 ACRPA 会再次调用以刷新颜色与字体)。"""
    bad = sorted(k for k in kw if k not in INJECTED)
    if bad:
        raise KeyError("mini_bar.bind 收到未声明的注入键: {}".format(", ".join(bad)))
    globals().update(kw)

''')
    out.append("\n# ══════════════════════════════════════════════════════════════════\n")
    out.append("# 以下为从 ACRPA.py 原样搬出的实现\n")
    out.append("# ══════════════════════════════════════════════════════════════════\n\n")
    for node in sorted(info["funcs"] + info["assigns"], key=lambda n: n.lineno):
        out.extend(lines[node.lineno - 1:node.end_lineno])
        out.append("\n\n")
    return "".join(out)


def rewrite_acrpa(info):
    """从 ACRPA.py 删除被搬走定义, 在原位插入 re-export 与注入引导。"""
    lines = info["src"].splitlines(keepends=True)
    spans = []
    for node in info["funcs"] + info["assigns"]:
        start = node.lineno
        # 连带紧贴其上的注释行一起搬走 (避免留下指向已迁走函数的注释)
        i = start - 2
        while i >= 0 and lines[i].lstrip().startswith("#"):
            start = i + 1
            i -= 1
        spans.append((start, node.end_lineno))
    spans.sort()

    reexport = sorted(info["in_module_refs"])
    reexport_block = "".join("    {},\n".format(n) for n in reexport)
    bind_kwargs = ",\n            ".join("{0}={0}".format(n) for n in info["to_inject"])
    inject_block = '''# ══════════════════════════════════════════════════════════════════════
# Mini Bar 已抽到 src/mini_bar.py
#   拆分第一阶段: 该集群的函数与模块级状态变量整体迁出, 函数体未改动;
#   它们引用的宿主符号在下面 _bind_minibar() 里注入。
#   回归: tools/_test_minibar_split.py (静态核对注入清单) + tools/_smoke_mini_bar.py
# ══════════════════════════════════════════════════════════════════════
import mini_bar
from mini_bar import (  # noqa: F401  供本模块其它函数继续按旧名字调用
''' + reexport_block + ''')


def _bind_minibar():
    """把宿主符号注入 mini_bar (启动时调用一次; 主题/缩放变更后再次调用以刷新)。"""
    try:
        mini_bar.bind(
            ''' + bind_kwargs + ''',
        )
    except Exception as e:      # 注入失败必须显式暴露, 不能静默 (否则是运行期 NameError)
        try:
            log1("Mini Bar 上下文注入失败: {}".format(e), "error")
        except Exception:
            pass


'''
    out, pos = [], 0
    inserted = False
    for start, end in spans:
        if start - 1 > pos:
            out.extend(lines[pos:start - 1])
        if not inserted:
            out.append(inject_block)
            inserted = True
        pos = end
    out.extend(lines[pos:])
    return "".join(out)


def verify_module(text):
    """静态核对生成的模块: 函数体内每个全局名都必须能解析到, 否则运行期 NameError。"""
    tree = ast.parse(text)
    defined = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            defined.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    defined.add(t.id)
        elif isinstance(node, ast.Import):
            for al in node.names:
                defined.add((al.asname or al.name).split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for al in node.names:
                defined.add(al.asname or al.name)

    unresolved = {}
    for fn in [n for n in tree.body if isinstance(n, ast.FunctionDef)]:
        local = local_names(fn)
        for x in ast.walk(fn):
            if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Load):
                if x.id in local or x.id in defined or x.id in dir(builtins):
                    continue
                unresolved.setdefault(x.id, set()).add(fn.name)
    return {k: sorted(v) for k, v in unresolved.items()}


def main():
    ap = argparse.ArgumentParser(description="抽取 Mini Bar 集群")
    ap.add_argument("--check", action="store_true", help="只核算, 不写文件")
    args = ap.parse_args()

    info = analyze()
    print("待搬迁函数 : {} 个".format(len(info["funcs"])))
    print("待搬迁变量 : {} 个".format(len(info["assigns"])))
    print("需注入符号 : {} 个 → {}".format(len(info["to_inject"]), ", ".join(info["to_inject"])))
    print("沿用 import: {}".format(", ".join(info["stdlib_imports"]) or "无"))
    print("原地被引用 : {}".format(info["in_module_refs"] or "无"))
    if info["unknown"]:
        print("[FAIL] 有 {} 个名字在 ACRPA 顶层找不到 (需人工确认): {}".format(
            len(info["unknown"]), ", ".join(info["unknown"])), file=sys.stderr)
        return 1

    module_text = render_module(info)
    unresolved = verify_module(module_text)
    if unresolved:
        print("[FAIL] 生成的模块里有 {} 个全局名无法解析 (会变成运行期 NameError):".format(
            len(unresolved)), file=sys.stderr)
        for name, users in sorted(unresolved.items()):
            print("   {} ← {}".format(name, ", ".join(users[:4])), file=sys.stderr)
        return 1
    print("[OK] 注入清单完整性核对通过 (生成的模块无未解析全局名)")

    if not args.check:
        with io.open(OUT_PATH, "w", encoding="utf-8") as f:
            f.write(module_text)
        with io.open(ACRPA_PATH, "w", encoding="utf-8") as f:
            f.write(rewrite_acrpa(info))
        print("[OK] 已写出 {} 与改写后的 ACRPA.py".format(
            os.path.relpath(OUT_PATH, BASE)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
