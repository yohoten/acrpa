# -*- coding: utf-8 -*-
"""打包规范门禁 (方案文档 §8.2 / 路线图 §12 P0-C 未落地项)。

三件事，任一不满足即返回非 0:

1. **一致性**: ``ACRPA.spec`` 的 ``hiddenimports`` 必须覆盖
   ``build.py::_PROJECT_MODULES`` 的每一项（漏一项 = 打包后运行期 ImportError，
   这类问题在源码模式下永远测不出来）。
2. **误排**: ``_PROJECT_MODULES`` 里的模块**不得**被 ``ACRPA.spec`` 的
   ``excludes`` 命中（例如某天把 ``history`` 写进 excludes，打包能成功但功能静默消失）。
3. **反向多余**: ``hiddenimports`` 里不得出现 ``excludes`` 也命中的名字
   （自相矛盾的声明）。

用法::

    python tools/_check_spec_excludes.py            # 通过 → 退出码 0
    python -c "import tools._check_spec_excludes as m; print(m.check())"

设计: 全部用 ``ast`` 静态解析，**不执行** spec（执行会触发 PyInstaller Analysis）。
"""
import ast
import fnmatch
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)
for _p in (ROOT, os.path.join(ROOT, "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

SPEC_PATH = os.path.join(ROOT, "ACRPA.spec")
BUILD_PATH = os.path.join(ROOT, "build.py")


def _str_list(node):
    """把 ast 里的 list/tuple 字面量 → [str]（非字面量项跳过）。"""
    out = []
    try:
        for elt in node.elts:
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                out.append(elt.value)
    except Exception:
        pass
    return out


def spec_lists(spec_path=SPEC_PATH):
    """→ (hiddenimports, excludes)。解析失败返回 ([], [])。"""
    try:
        with open(spec_path, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=spec_path)
    except Exception:
        return [], []
    hidden, excludes = [], []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = getattr(func, "id", None) or getattr(func, "attr", None)
        if name != "Analysis":
            continue
        for kw in node.keywords:
            if kw.arg == "hiddenimports":
                hidden = _str_list(kw.value)
            elif kw.arg == "excludes":
                excludes = _str_list(kw.value)
    return hidden, excludes


def project_modules(build_path=BUILD_PATH):
    """读取 ``build.py::_PROJECT_MODULES``（静态解析，不 import 打包脚本）。"""
    try:
        with open(build_path, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=build_path)
    except Exception:
        return []
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if getattr(target, "id", None) == "_PROJECT_MODULES":
                return _str_list(node.value)
    return []


def _matches(name, patterns):
    """模块名是否被任一 exclude 模式命中（支持 ``pkg.*``）。"""
    for pat in patterns:
        if pat == name:
            return pat
        if fnmatch.fnmatchcase(name, pat):
            return pat
        if pat.endswith(".*") and name.startswith(pat[:-1]):
            return pat
    return ""


def check(spec_path=SPEC_PATH, build_path=BUILD_PATH):
    """→ (rc, problems)。rc=0 表示规范一致。"""
    problems = []
    hidden, excludes = spec_lists(spec_path)
    modules = project_modules(build_path)

    if not hidden:
        problems.append("ACRPA.spec 未解析出 hiddenimports（文件缺失或结构变化）")
    if not modules:
        problems.append("build.py 未解析出 _PROJECT_MODULES")

    hidden_set = set(hidden)
    for name in modules:
        if name not in hidden_set:
            problems.append("ACRPA.spec hiddenimports 缺少项目模块: {}".format(name))
        hit = _matches(name, excludes)
        if hit:
            problems.append(
                "必需模块被 excludes 误排: {} (命中模式 {})".format(name, hit))

    for name in hidden:
        hit = _matches(name, excludes)
        if hit:
            problems.append(
                "hiddenimports 与 excludes 自相矛盾: {} (命中模式 {})".format(name, hit))

    return (0 if not problems else 1), problems


def main(argv=None):
    rc, problems = check()
    if rc == 0:
        hidden, excludes = spec_lists()
        modules = project_modules()
        print("[OK] 打包规范一致: 项目模块 {} 个 / hiddenimports {} 项 / excludes {} 项".format(
            len(modules), len(hidden), len(excludes)))
    else:
        print("[FAIL] 打包规范门禁未通过 ({} 项):".format(len(problems)))
        for p in problems[:50]:
            print("  - {}".format(p))
    return rc


if __name__ == "__main__":
    sys.exit(main())
