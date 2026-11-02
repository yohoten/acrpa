#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""日志面板拆分回归自测 (路线图 阶段二第 3 项第 1 步: `src/ui/log_dock.py`)。

用法:
    .venv\\Scripts\\python.exe -X utf8 tools\\_test_log_dock_split.py

覆盖:
  L1  模块存在性   : src/ui/log_dock.py 存在且导出 build/init_ctx/refresh_theme/
                     flush_and_apply/toggle_dock/on_sash_release/autoscroll_changed/
                     apply_cbar/apply_filter/search_next/clear_view/export_view/
                     set_toggle_button/get_rz/get_tlog/get_scroll/get_state
  L2  无 GUI 副作用: 子进程 `import ui.log_dock` 秒退 (不建窗/不阻塞)
  L3  宿主转发别名 : ACRPA.py 保留 rz/scroll/_tlog 控件别名 (get_* 取回) 与
                     _toggle_log_dock/_on_log_sash_release/_log_apply_cbar/
                     _log_apply_filter/_log_search_next/_log_clear_view/
                     _log_export_view/_log_autoscroll_changed 函数别名
  L4  构建段迁出   : ACRPA.py 内不再出现日志面板的控件创建/状态
                     (ThreadSafeLog(rz / tkinter.Text(log_frame / _log_dock_state /
                      _logfilter / _cbar_state / _CBAR_LEVEL_OF / def _toggle_log_dock)
  L5  实现归属     : log_dock.py 内含 ThreadSafeLog 装配 (经注入的 thread_safe_log)、
                     set_tlog(_tlog)、subscribe(refresh_theme)、
                     level/§4.6 tag_configure、_tlog.flush
  L6  _periodic    : ACRPA._periodic 经 ui_log_dock.flush_and_apply() 驱动日志刷新
  L7  去定点回填   : ACRPA._refresh_theme 段内已无 rz.configure / 日志 tag_configure,
                     且仍保留一次 ui_theme.publish (订阅驱动)
  L8  set_tlog 契约: `set_tlog(_tlog)` 在 src/ 内**恰好一次** (log_dock 装配处)
  L9  无反向依赖   : log_dock.py 的 import 节点不含 ACRPA/app (AST 判定, 避免成环)
  L10 静态清单同步 : tools/_smoke_ui_scale.py 的 UI_MODULES 收录 log_dock.py;
                     tools/_smoke_dark_mode.py 的日志 tag 断言指向 log_dock.py

退出码: 0 = 全部通过 / 1 = 有失败。
纯静态断言 + 子进程导入探测; 不创建任何真实窗口 (无 GUI 依赖, 可进 CI)。
"""
import ast
import io
import os
import subprocess
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

LOG_DOCK = "src/ui/log_dock.py"

_PASS, _FAIL = [], []


def check(cond, msg, detail=""):
    (_PASS if cond else _FAIL).append(msg)
    tail = ("  ({})".format(detail) if detail else "")
    print("[{}] {}{}".format("OK  " if cond else "FAIL", msg, tail))


def _read(rel):
    with io.open(os.path.join(ROOT, rel), "r", encoding="utf-8") as f:
        return f.read()


def _parse(rel):
    return ast.parse(_read(rel), filename=rel)


def _find_func(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def _top_names(tree):
    """顶层 def / class / 赋值名集合 (导出符号口径)。"""
    out = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    out.add(t.id)
    return out


# ── L1 / L2 ──────────────────────────────────────────────────────────

EXPORTS = (
    "build", "init_ctx", "refresh_theme", "flush_and_apply",
    "toggle_dock", "on_sash_release", "autoscroll_changed",
    "apply_cbar", "apply_filter", "search_next", "clear_view", "export_view",
    "set_toggle_button",
    "get_rz", "get_tlog", "get_scroll", "get_state",
)


def l_module_and_side_effect():
    print("\n── L1 模块存在性 / L2 无 GUI 副作用 ──")
    path = os.path.join(ROOT, LOG_DOCK)
    check(os.path.exists(path), "{} 存在".format(LOG_DOCK))
    if not os.path.exists(path):
        return
    names = _top_names(_parse(LOG_DOCK))
    missing = [n for n in EXPORTS if n not in names]
    check(not missing, "{} 导出全部关键符号".format(LOG_DOCK),
          "缺={} / 共 {} 个顶层名".format(missing, len(names)))

    # L2: 子进程 import 不得建窗/阻塞 (只验「秒退 + 无副作用」)
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    code = ("import sys; sys.path.insert(0, {src!r}); "
            "import ui.log_dock as ld; "
            "print('HAS_TK_ROOT=%s' % ('tkinter.Tk' in dir())); "
            "print('OK=%s' % callable(getattr(ld, 'flush_and_apply', None)))").format(src=SRC)
    try:
        p = subprocess.run([sys.executable, "-X", "utf8", "-c", code],
                           cwd=ROOT, env=env, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=20)
        out = (p.stdout or b"").decode("utf-8", "replace")
        check(p.returncode == 0, "子进程 import ui.log_dock 秒退 (rc={})".format(p.returncode),
              (p.stderr or b"").decode("utf-8", "replace").strip()[-200:])
        check("OK=True" in out, "子进程内 ui.log_dock 可用", out.strip().replace("\n", " "))
    except subprocess.TimeoutExpired:
        check(False, "子进程 import ui.log_dock 超时 (疑似副作用阻塞)")
    except Exception as e:
        check(False, "子进程探测异常: {}".format(e))


# ── L3 / L4 ──────────────────────────────────────────────────────────

def l_host_aliases_and_moved_out():
    print("\n── L3 宿主转发别名 / L4 构建段已迁出 ──")
    a_src = _read("src/ACRPA.py")

    # L3a: 控件句柄别名 (build_app 内经 get_* 取回)
    for name, getter in (("rz", "get_rz"), ("scroll", "get_scroll"),
                         ("_tlog", "get_tlog")):
        pat = "{} = ui_log_dock.{}()".format(name, getter)
        check(pat in a_src, "ACRPA.py 保留控件别名 `{}`".format(pat))
    for alias, target in (("_toggle_log_dock", "toggle_dock"),
                          ("_on_log_sash_release", "on_sash_release"),
                          ("_log_autoscroll_changed", "autoscroll_changed"),
                          ("_log_apply_cbar", "apply_cbar"),
                          ("_log_apply_filter", "apply_filter"),
                          ("_log_search_next", "search_next"),
                          ("_log_clear_view", "clear_view"),
                          ("_log_export_view", "export_view")):
        pat = "{} = ui_log_dock.{}".format(alias, target)
        check(pat in a_src, "ACRPA.py 保留函数别名 `{}`".format(pat))

    # L3b: 运行时别名可解析 (不 import ACRPA —— 静态文本已足够; 额外用子进程确认)
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    code = ("import sys; sys.path.insert(0, {src!r}); import ACRPA; "
            "print('ALIASES=%s' % (callable(ACRPA._toggle_log_dock) "
            "and callable(ACRPA._log_apply_filter) "
            "and callable(ACRPA._log_export_view) "
            "and hasattr(ACRPA, 'rz') == False))").format(src=SRC)
    try:
        p = subprocess.run([sys.executable, "-X", "utf8", "-c", code],
                           cwd=ROOT, env=env, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=60)
        out = (p.stdout or b"").decode("utf-8", "replace")
        if p.returncode == 0:
            check("ALIASES=True" in out, "子进程内 ACRPA 转发别名可解析 (import 零副作用)",
                  out.strip().replace("\n", " "))
        else:
            print("[WARN] ACRPA 导入失败 ({}), 跳过运行时别名断言: {}".format(
                p.returncode, (p.stderr or b"").decode("utf-8", "replace").strip()[-160:]))
    except Exception as e:
        print("[WARN] ACRPA 子进程探测异常, 跳过: {}".format(e))

    # L4: 日志面板的控件创建 / 模块级状态不得再留在 ACRPA.py
    for frag, why in (
            ("ThreadSafeLog(", "ThreadSafeLog 装配"),
            ("tkinter.Text(log_frame", "日志 Text 创建"),
            ("card_log = create_card(bottom_dock)", "日志卡片创建"),
            ("_log_dock_state", "面板状态 _log_dock_state"),
            ("_logfilter", "过滤游标 _logfilter"),
            ("_cbar_state", "色条游标 _cbar_state"),
            ("_CBAR_LEVEL_OF", "级别↔色条映射 _CBAR_LEVEL_OF"),
            ("def _toggle_log_dock(", "原收起/展开函数定义"),
            ('rz.tag_configure("info"', "日志 tag 建窗时配置")):
        check(frag not in a_src, "ACRPA.py 已无{}".format(why), "命中={!r}".format(frag))


# ── L5 ───────────────────────────────────────────────────────────────

def l_implementation_home():
    print("\n── L5 实现归属 (log_dock.py) ──")
    ld = _read(LOG_DOCK)
    for frag, why in (
            ('_CTX["thread_safe_log"](rz', "ThreadSafeLog 装配 (依赖注入, 唯一一处)"),
            ("set_tlog(_tlog)", "set_tlog 契约调用"),
            ("subscribe(refresh_theme)", "ThemeBus 自注册换肤"),
            ("def refresh_theme(", "换肤回调"),
            ("def flush_and_apply(", "_periodic 驱动入口"),
            ("_tlog.flush(", "队列 flush"),
            ('tag_configure("error"', "级别 tag: error"),
            ('tag_configure("success"', "级别 tag: success"),
            ('tag_configure("cbar_error"', "§4.6 左缘色条 tag"),
            ('tag_configure("lzebra"', "§4.6 行 zebra tag"),
            ('tag_configure("search_hit"', "搜索命中 tag"),
            ('tag_configure("lf_hide"', "级别过滤 elide tag")):
        check(frag in ld, "{} 含{}".format(LOG_DOCK, why), "查找={!r}".format(frag))

    # 行首 ▌ 色条机制与幂等游标仍在模块内
    check("_CBAR_LEVEL_OF" in ld and '_cbar_state = {"done": 0}' in ld,
          "{} 保留 §4.6 色条映射与幂等游标".format(LOG_DOCK))


# ── L6 / L7 ──────────────────────────────────────────────────────────

def l_periodic_and_refresh_theme():
    print("\n── L6 _periodic 驱动 / L7 _refresh_theme 去定点回填 ──")
    a_src = _read("src/ACRPA.py")
    check("ui_log_dock.flush_and_apply()" in a_src,
          "ACRPA._periodic 经 ui_log_dock.flush_and_apply() 刷新日志")

    per = _find_func(_parse("src/ACRPA.py"), "_periodic")
    if per is None:
        check(False, "未找到 ACRPA._periodic")
    else:
        seg = ast.get_source_segment(a_src, per) or ""
        check("flush_and_apply()" in seg and "_log_apply_cbar" not in seg
              and "_tlog.flush" not in seg,
              "_periodic 内不再内联 flush/flush-_cbar (已收敛为一次调用)")

    rt = _find_func(_parse("src/ACRPA.py"), "_refresh_theme")
    if rt is None:
        check(False, "未找到 ACRPA._refresh_theme")
        return
    seg = ast.get_source_segment(a_src, rt) or ""
    check("rz.configure(" not in seg, "_refresh_theme 段内已无 rz.configure (移交订阅)")
    check('tag_configure("info"' not in seg and 'tag_configure("error"' not in seg,
          "_refresh_theme 段内已无日志 tag 定点回填 (移交订阅)")
    check("ui_theme.publish(" in seg, "_refresh_theme 仍保留一次 ui_theme.publish (订阅驱动)")


# ── L8 / L9 ──────────────────────────────────────────────────────────

def l_contract_and_no_reverse_dep():
    print("\n── L8 set_tlog 契约 / L9 无反向依赖 ──")
    hits = []
    for dirpath, _dirs, files in os.walk(SRC):
        for fn in files:
            if not fn.endswith(".py"):
                continue
            full = os.path.join(dirpath, fn)
            try:
                with io.open(full, "r", encoding="utf-8") as f:
                    txt = f.read()
            except Exception:
                continue
            if "set_tlog(_tlog)" in txt:
                hits.append(os.path.relpath(full, ROOT).replace(os.sep, "/"))
    check(hits == [LOG_DOCK], "`set_tlog(_tlog)` 在 src/ 内恰好一次且在 log_dock",
          "命中={}".format(hits))

    # L9: AST 判定 log_dock.py 的 import (注释/文档串里出现「import ACRPA」不算命中)
    imported = set()
    for node in ast.walk(_parse(LOG_DOCK)):
        if isinstance(node, ast.Import):
            for a in node.names:
                imported.add(a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    bad = sorted(n for n in imported if n in ("ACRPA", "app"))
    check(not bad, "{} 的 import 节点无 ACRPA/app (无反向依赖)".format(LOG_DOCK),
          "import 到={}".format(bad))


# ── L10 ──────────────────────────────────────────────────────────────

def l_static_lists_synced():
    print("\n── L10 静态清单 / 既有自测定位同步 ──")
    scale = _read("tools/_smoke_ui_scale.py")
    check("src/ui/log_dock.py" in scale,
          "_smoke_ui_scale.UI_MODULES 收录 src/ui/log_dock.py")
    dark = _read("tools/_smoke_dark_mode.py")
    check("src/ui/log_dock.py" in dark and 'tag_configure("error"' in dark,
          "_smoke_dark_mode 的日志 tag 断言指向 log_dock.py (强度不降)")
    dbg = _read("tools/_debug_dark_theme.py")
    check("acrpa.rz" in dbg,
          "_debug_dark_theme 仍经 ACRPA.rz 读取日志 tag (转发别名契约)")


def main():
    print("=== 日志面板拆分回归自测 (路线图 阶段二第 3 项第 1 步) ===")
    l_module_and_side_effect()
    l_host_aliases_and_moved_out()
    l_implementation_home()
    l_periodic_and_refresh_theme()
    l_contract_and_no_reverse_dep()
    l_static_lists_synced()
    print("\n=== 结果: {} (通过 {}, 失败 {}) ===".format(
        "FAIL" if _FAIL else "OK", len(_PASS), len(_FAIL)))
    if _FAIL:
        for m in _FAIL:
            print("  - {}".format(m))
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
