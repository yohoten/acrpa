#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""设置窗口 11 卡拆分回归自测 (路线图 §3.1 阶段二第 4 项 / §12 阶段二新增项②)。

用法:
    .venv\\Scripts\\python.exe -X utf8 tools\\_test_settings_split.py

覆盖:
  A  包/卡片存在性 : src/ui/settings/ 包存在; 11 张 cards/*.py 各导出 build/apply
                     (quick 允许无 apply, 本实现仍提供 no-op);
                     src/ui/settings/window.py 提供门面 open_settings_window。
  B  薄壳瘦身      : src/settings_window.py 行数 < 300 (较拆分前 2809 行显著下降);
                     不再包含卡片构建大块 (_make_collapsible_card(_inner, "..."));
                     re-export 四个入口 (open_settings_window/init_ctx/refresh_theme/
                     update_sched_next_label 均有 def)。
  C  无 ACRPA 依赖 : src/settings_window.py + src/ui/settings/** 全树 AST 上
                     不出现 `import ACRPA`/`from ACRPA import`。
  D  ThemeBus 字面量: src/settings_window.py 文本仍含
                     `_ui_theme.subscribe(_on_theme_publish)` 与
                     `def refresh_theme(prev=None):`。
  E  实机句柄可取  : 真实 Tk 下 open_settings_window() 后
                     _nav_cards 覆盖 11 卡、_apply_map 覆盖 10 卡 (无 quick)。
                      无 GUI 环境记 [WARN] (不假通过)。

退出码: 0 = 全部通过 / 1 = 有失败。纯静态断言 + 真实 Tk 动态断言。
"""
import ast
import io
import importlib
import os
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

CARD_KEYS = ("exec", "ai", "record", "log", "system", "quick",
             "advanced", "netlink", "python", "market", "extensions")

_PASS, _FAIL, _WARN = [], [], []


def check(cond, msg, detail=""):
    (_PASS if cond else _FAIL).append(msg)
    tail = ("  ({})".format(detail) if detail else "")
    print("[{}] {}{}".format("OK  " if cond else "FAIL", msg, tail))


def warn(msg):
    _WARN.append(msg)
    print("[WARN] {}".format(msg))


def _read(rel):
    with io.open(os.path.join(ROOT, rel), "r", encoding="utf-8") as f:
        return f.read()


def _parse(rel):
    return ast.parse(_read(rel), filename=rel)


# ── A. 包 / 卡片存在性 ───────────────────────────────────────────────

def t_package_and_cards():
    print("\n── A. 包存在性 / 卡片 build+apply ──")
    check(os.path.exists(os.path.join(SRC, "ui", "settings", "__init__.py")),
          "src/ui/settings/__init__.py 存在")
    check(os.path.exists(os.path.join(SRC, "ui", "settings", "window.py")),
          "src/ui/settings/window.py 存在")
    check(os.path.exists(os.path.join(SRC, "ui", "settings", "cards",
                                      "__init__.py")),
          "src/ui/settings/cards/__init__.py 存在")

    for key in CARD_KEYS:
        rel = "src/ui/settings/cards/{}.py".format(key)
        p = os.path.join(ROOT, rel)
        if not os.path.exists(p):
            check(False, "卡片模块存在: {}".format(rel))
            continue
        try:
            tree = _parse(rel)
        except SyntaxError as e:
            check(False, "{} 语法错误: {}".format(rel, e))
            continue
        funcs = {n.name for n in tree.body
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        check("build" in funcs, "{} 导出 def build(parent, ctx)".format(rel))
        check("apply" in funcs, "{} 导出 def apply(ctx, handles)".format(rel))

    # 运行时可导入 (无 GUI 副作用)
    try:
        import ui.settings as us
        check(callable(getattr(us, "open_settings_window", None)),
              "import ui.settings 后 open_settings_window 可用")
        check(callable(getattr(us, "refresh_theme", None)),
              "import ui.settings 后 refresh_theme 可用")
    except Exception as e:
        check(False, "import ui.settings 失败: {}".format(e))


# ── B. 薄壳瘦身 / 去构建残留 ─────────────────────────────────────────

BANNED_IN_SHELL = (
    '_make_collapsible_card(_inner, "基础执行"',
    '_make_collapsible_card(_inner, "网络互联"',
    '_make_collapsible_card(_inner, "高级设置"',
    'def _apply_exec_settings',
    'def _apply_ai_settings',
    'def _apply_system_settings',
    'def _apply_netlink_settings',
)

SHELL_ENTRY_DEFS = (
    "def open_settings_window(",
    "def init_ctx(",
    "def refresh_theme(prev=None):",
    "def update_sched_next_label(",
)


def t_shell_thin():
    print("\n── B. 薄壳瘦身 / 去构建残留 / re-export ──")
    src = _read("src/settings_window.py")
    n_lines = len(src.splitlines())
    check(n_lines < 300,
          "src/settings_window.py 行数显著下降 (< 300)", "lines={}".format(n_lines))

    hits = [b for b in BANNED_IN_SHELL if b in src]
    check(not hits, "薄壳不再含卡片构建/apply 大块", "残留={}".format(hits))

    for d in SHELL_ENTRY_DEFS:
        check(d in src, "薄壳 re-export {}".format(d))

    # 门面 (真正实现) 在 window.py
    wsrc = _read("src/ui/settings/window.py")
    check("def open_settings_window(" in wsrc,
          "window.py 提供 open_settings_window 门面")
    # 卡片控件树统一经 cards.build(parent, ctx) 构建; window.py 自身不内联建卡
    check("_make_collapsible_card(_inner" not in wsrc,
          "window.py 不内联卡片控件树 (统一经 cards.build)")
    check("importlib" not in wsrc,
          "window.py 不使用动态导入（PyInstaller 可静态分析）")
    for key in CARD_KEYS:
        check("as c_{}".format(key) in wsrc,
              "window.py 静态导入卡片 {}".format(key))


# ── C. 无 ACRPA 依赖 (AST) ───────────────────────────────────────────

def _imports_acrpa(rel):
    for n in ast.walk(_parse(rel)):
        if isinstance(n, ast.Import):
            for a in n.names:
                if a.name == "ACRPA" or a.name.startswith("ACRPA."):
                    return True
        elif isinstance(n, ast.ImportFrom):
            if n.module == "ACRPA":
                return True
    return False


def t_no_acrpa_import():
    print("\n── C. 无 ACRPA 依赖 (AST) ──")
    rels = ["src/settings_window.py", "src/ui/settings/__init__.py",
            "src/ui/settings/window.py", "src/ui/settings/cards/__init__.py"]
    for key in CARD_KEYS:
        rels.append("src/ui/settings/cards/{}.py".format(key))
    bad = []
    for rel in rels:
        if not os.path.exists(os.path.join(ROOT, rel)):
            continue
        if _imports_acrpa(rel):
            bad.append(rel)
    check(not bad, "设置窗口全树无 `import ACRPA`", "命中={}".format(bad))


# ── D. ThemeBus 字面量保留 ───────────────────────────────────────────

def t_theme_literals():
    print("\n── D. ThemeBus 订阅 / refresh_theme 字面量 ──")
    src = _read("src/settings_window.py")
    check("_ui_theme.subscribe(_on_theme_publish)" in src,
          "薄壳保留 _ui_theme.subscribe(_on_theme_publish)")
    check("def refresh_theme(prev=None):" in src,
          "薄壳保留 def refresh_theme(prev=None):")


# ── E1. 字体契约: init_ctx 必须兼容命名字体字符串 (2026-11-04 回归) ──
# 历史 bug: init_ctx 用 (FONT_SMALL[0], FONT_SMALL[1], "bold") 构造粗体,
# 对命名字体字符串 'ACRPA_SMALL' 下标得 ('A','C','bold') → TclError
# "expected integer but got C" → 11 卡构建中断, 设置页整页空白。

def t_font_contract():
    print("\n── E1. init_ctx 字体契约 (命名字体字符串兼容) ──")
    wsrc = _read("src/ui/settings/window.py")
    check("_bold_font" in wsrc,
          "window.py 提供 _bold_font 安全加粗 (不再对字符串下标)")
    check("FONT_SMALL[0]" not in wsrc,
          "window.py 不再出现 FONT_SMALL[0] 字符串下标反模式")
    check("_ui_theme.claim_window(" in wsrc,
          "window.py 声明 claim_window 自管换肤")
    # 实机: 按 ACRPA.py 真实调用方式传命名字体, init_ctx 后粗体必须合法
    try:
        import tkinter
        import utils
        import settings_window as sw
        from ui import theme as ui_theme
    except Exception as e:
        check(False, "import 失败: {}".format(e))
        return
    try:
        root = tkinter.Tk()
        root.withdraw()
    except Exception as e:
        warn("无法创建 Tk root, 字体契约实机断言跳过: {}".format(e))
        return
    try:
        sw.init_ctx(root_win=root, colors=ui_theme.colors(False),
                    fonts=(utils.FONT_TITLE, utils.FONT_BODY,
                           utils.FONT_SMALL, utils.FONT_BUTTON))
        from ui.settings import window as w
        check(w.FONT_SMALL_BOLD != ("A", "C", "bold"),
              "FONT_SMALL_BOLD 不为 ('A','C','bold') 退化元组")
        check(w.FONT_SMALL_BOLD == utils.FONT_SMALL_BOLD,
              "命名字体传入时 FONT_SMALL_BOLD 复用 utils 命名字体")
        check(w.FONT_TITLE == utils.FONT_TITLE and w.FONT_BODY == utils.FONT_BODY
              and w.FONT_SMALL == utils.FONT_SMALL,
              "init_ctx 字体解包顺序与调用方 (TITLE,BODY,SMALL) 一致")
        # 用解析结果真实建一次 Label (Tk 层验证)
        tkinter.Label(root, text="x", font=w.FONT_SMALL_BOLD,
                      fg=ui_theme.colors(False)["fgt"])
        check(True, "FONT_SMALL_BOLD 可直接创建 Tk Label")
    except Exception as e:
        check(False, "字体契约实机断言失败: {}".format(e))
    finally:
        root.destroy()


# ── E. 实机: 11 卡句柄可取 ───────────────────────────────────────────

def t_live_handles():
    print("\n── E. 实机: open_settings_window 后 11 卡句柄 ──")
    try:
        import tkinter  # noqa: F401
        import state
        import settings_window as sw
        from ui import theme as ui_theme
    except Exception as e:
        check(False, "import 失败: {}".format(e))
        return
    try:
        root = tkinter.Tk()
        root.withdraw()
    except Exception as e:
        warn("无法创建 Tk root (无 GUI 环境), 实机断言跳过: {}".format(e))
        return

    if not hasattr(state, "_sched_enabled_var"):
        state._sched_enabled_var = tkinter.BooleanVar(
            value=bool(getattr(state, "SCHED_ENABLED", False)))

    cfg_path = getattr(state, "CONFIG_PATH", os.path.join(ROOT, "config.json"))
    backup = None
    if os.path.exists(cfg_path):
        with open(cfg_path, "rb") as fh:
            backup = fh.read()
    try:
        sw.init_ctx(root_win=root, colors=ui_theme.colors(False),
                    fonts=(("Segoe UI", 10), ("Segoe UI", 9),
                           ("Segoe UI", 8), ("Segoe UI", 9)),
                    app_root=ROOT)
        sw.open_settings_window()
        for _ in range(6):
            root.update()
            root.update_idletasks()
            time.sleep(0.05)

        nav = getattr(sw, "_nav_cards", None)
        check(isinstance(nav, list) and len(nav) == 11,
              "_nav_cards 覆盖 11 卡", "count={}".format(len(nav) if nav else nav))
        if isinstance(nav, list):
            keys = sorted(k for k, _w in nav)
            check(keys == sorted(CARD_KEYS), "导航卡 key 与 11 卡一致", "keys={}".format(keys))

        applied = set(getattr(sw, "_apply_map", {}).keys())
        expect_apply = set(CARD_KEYS) - {"quick"}
        check(expect_apply.issubset(applied),
              "_apply_map 覆盖 10 卡 (无 quick)",
              "缺失={}".format(sorted(expect_apply - applied)))
        check("quick" not in applied, "quick 卡不注册 apply (与原语义一致)")

        # 等待延迟回调 (高级卡 OCR 状态 after(500) + 后台探测) 收尾
        deadline = time.time() + 1.6
        while time.time() < deadline:
            root.update()
            time.sleep(0.05)
    except Exception as e:
        check(False, "实机打开设置窗口失败: {}".format(e))
    finally:
        try:
            sw._close()
        except Exception:
            pass
        try:
            root.destroy()
        except Exception:
            pass
        if backup is None:
            if os.path.exists(cfg_path):
                os.remove(cfg_path)
        else:
            with open(cfg_path, "wb") as fh:
                fh.write(backup)


def main():
    print("=== 设置窗口 11 卡拆分回归自测 (阶段二第 4 项 / 新增项②) ===")
    t_package_and_cards()
    t_shell_thin()
    t_no_acrpa_import()
    t_theme_literals()
    t_font_contract()
    t_live_handles()
    print("\n=== 结果: {} (通过 {}, 失败 {}, 警告 {}) ===".format(
        "FAIL" if _FAIL else "OK", len(_PASS), len(_FAIL), len(_WARN)))
    if _FAIL:
        for m in _FAIL:
            print("  - {}".format(m))
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
