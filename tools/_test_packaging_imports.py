#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""打包导入完整性守卫 —— 防「单文件打包后设置窗右侧空白」类缺陷复现。

用法:
    .venv\\Scripts\\python.exe -X utf8 tools\\_test_packaging_imports.py

背景
----
设置卡片历史上用 `importlib.import_module("ui.settings.cards." + key)` 运行时拼接
模块名导入 —— PyInstaller 的静态分析看不到这条边，单文件打包后 `ui.settings.cards.*`
未被纳入产物：`open_settings_window()` 在逐卡构建时抛 ModuleNotFoundError，窗口只剩
左侧导航（其标签来自字面量 `_NAV_ITEMS`），右侧内容区空白；而源码运行完全正常。

注意：打包真源是 **`build.py`**（`_PROJECT_MODULES` → `--hidden-import`）；
`ACRPA.spec` 由 PyInstaller 每次构建重新生成，故不作为校验对象。

覆盖:
  A 危险动态导入   : AST 扫描 src/，禁止「动态模块名」或「自身 ui 包」的
                     import_module 调用（第三方运行时目录的两处动态导入已白名单）。
  B 静态卡片注册表 : ui.settings.window._card_modules() 覆盖 _CARD_KEYS 全部键，
                     且每张卡导出 build/apply。
  C build.py 声明  : build.py 的 _PROJECT_MODULES 含全部 ui.settings.cards.<card>
                     与 ui / ui.theme / flow_graph / capabilities / extensions /
                     i18n / cli / script_io / settings_window 等关键模块。
  D 静态可发现     : AST 断言 _card_modules 函数体内含 `from ui.settings.cards import`
                     （PyInstaller 只能跟随静态 import 语句）。

退出码: 0=全部通过 / 1=有失败。
"""
import ast
import io
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

_PASS, _FAIL = [], []


def check(cond, msg, detail=""):
    (_PASS if cond else _FAIL).append(msg)
    tail = ("  ({})".format(detail) if detail else "")
    print("[{}] {}{}".format("OK  " if cond else "FAIL", msg, tail))


def _read(rel):
    with io.open(os.path.join(ROOT, rel), "r", encoding="utf-8") as f:
        return f.read()


# ── A. 禁止危险的运行时模块名导入 ────────────────────────────────────
# 白名单：加载「第三方运行时目录」的动态导入（第三方扩展 / 用户插件），其目标不在
# 打包产物内，PyInstaller 本就不应参与 —— 仅允许**非字面量**形式。
_DYN_ALLOW = {
    "src/extensions/loader.py": "加载扩展包 pure/ 下的第三方模块（LOCALAPPDATA，非自身包）",
    "src/plugins/__init__.py": "加载用户插件文件（运行时目录，非自身包）",
}


def _dyn_import_calls(path):
    """AST 扫描 → [(lineno, literal, target)]；只认真实调用，忽略注释/文档字符串。"""
    try:
        with io.open(path, "r", encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=path)
    except Exception:
        return []
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if isinstance(f, ast.Attribute) and f.attr == "import_module":
            a0 = node.args[0] if node.args else None
            if isinstance(a0, ast.Constant) and isinstance(a0.value, str):
                out.append((node.lineno, True, a0.value))
            else:
                out.append((node.lineno, False, "<dynamic>"))
    return out


def t_no_dynamic_import():
    print("\n── A. 禁止危险的运行时模块名导入 ──")
    bad = []
    for dp, _dirs, files in os.walk(SRC):
        if "__pycache__" in dp:
            continue
        for fn in sorted(files):
            if not fn.endswith(".py"):
                continue
            fp = os.path.join(dp, fn)
            rel = os.path.relpath(fp, ROOT).replace(os.sep, "/")
            for lineno, literal, target in _dyn_import_calls(fp):
                if rel in _DYN_ALLOW and not literal:
                    continue          # 白名单：第三方运行时目录的动态导入
                if literal and not target.startswith("ui"):
                    continue          # 字面量且非自身 UI 包 → 与打包无关
                bad.append("{}:{} -> {}".format(rel, lineno, target))
    check(not bad, "无危险的 import_module（动态模块名 / 自身 ui 包）", "; ".join(bad))


# ── B. 静态卡片注册表覆盖 _CARD_KEYS ─────────────────────────────────
def t_card_registry():
    print("\n── B. 静态卡片注册表 ──")
    import ui.settings.window as w
    keys = tuple(getattr(w, "_CARD_KEYS", ()))
    check(len(keys) == 12, "_CARD_KEYS 为 12 张卡", str(keys))
    fn = getattr(w, "_card_modules", None)
    check(callable(fn), "window._card_modules 存在")
    if not callable(fn):
        return keys
    mods = fn()
    check(set(mods) == set(keys), "_card_modules 覆盖 _CARD_KEYS 全部键",
          "missing={} extra={}".format(sorted(set(keys) - set(mods)),
                                       sorted(set(mods) - set(keys))))
    for k in keys:
        m = mods.get(k)
        check(m is not None and callable(getattr(m, "build", None))
              and callable(getattr(m, "apply", None)),
              "cards.{} 导出 build/apply".format(k))
    return keys


# ── C. build.py 的 _PROJECT_MODULES 声明完整 ────────────────────────
def t_build_py_modules(keys):
    print("\n── C. build.py 的项目模块声明 ──")
    src = _read("build.py")
    required = ["ui", "ui.theme", "ui.settings", "ui.settings.window",
                "ui.settings.cards", "ui.flow_canvas", "ui.workflow_view",
                "ui.command_palette", "flow_graph", "capabilities", "extensions",
                "i18n", "cli", "script_io", "script_model", "script_validate",
                "settings_window", "help_content", "help_window", "version_info"]
    required += ["ui.settings.cards." + k for k in keys]
    missing = [n for n in required if '"{}"'.format(n) not in src]
    check(not missing, "build.py 已声明全部必需模块", "缺失={}".format(missing))


# ── D. AST: _card_modules 内含静态 import ───────────────────────────
def t_static_import_ast():
    print("\n── D. _card_modules 的静态 import ──")
    tree = ast.parse(_read("src/ui/settings/window.py"), filename="window.py")
    fn = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_card_modules":
            fn = node
            break
    check(fn is not None, "window.py 定义 _card_modules")
    if fn is None:
        return
    ok = False
    for node in ast.walk(fn):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                "ui.settings.cards"):
            ok = True
    check(ok, "函数体内为静态 `from ui.settings.cards import ...` (PyInstaller 可发现)")


def main():
    print("=== 打包导入完整性守卫 ===")
    t_no_dynamic_import()
    keys = t_card_registry()
    t_build_py_modules(keys or ())
    t_static_import_ast()

    print("\n" + "-" * 60)
    print("PASS={} FAIL={}".format(len(_PASS), len(_FAIL)))
    for m in _FAIL:
        print("[FAIL] {}".format(m))
    print("=== 结果: {} ===".format("FAIL" if _FAIL else "OK"))
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
