#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""显示大小与字体缩放 (§五, pt 制) 自测 — 静态 AST 断言 + 真实 Tk 动态断言。

用法:
    python tools/_smoke_ui_scale.py

覆盖:
  A. src/utils.py   — 存在 _FONT_SPECS / init_fonts / font / set_ui_scale /
                      scaled / dpi_factor / fit_pt / current_ui_scale;
                      FONT_* 常量为字符串角色名(非元组); 缩放常量存在
  B. 全项目          — 不再出现 (*FONT_xxx, ...) 形式的元组解包
  C. 动态(有 root)   — init_fonts 后 font()/nametofont 字号 == fit_pt;
                      set_ui_scale(1.2) 字号变大且 state.UI_SCALE==1.2;
                      set_ui_scale(9.9) 被 clamp 到 1.5; set_ui_scale(1.0) 复位;
                      scaled(100) >= 100; 打印 dpi_factor() 实测值
  D. 替换计数        — src/ 内剩余「硬编码字体元组」数量 <= 白名单数量
  E. 守护断言        — workflow.py 未被改动; NetLink 集成块 / Mini Bar 定时器 /
                      utils.themed / ai_client.PROVIDER_PRESETS /
                      py_sandbox.precheck / devlink_btn text="🌐" 均仍在
  F. 依赖探测        — pyautogui/xlrd/pyperclip 缺失仅 [WARN] (不假通过)

注: 无 GUI 环境 (无法建 Tk root) 时 C 段整体记 [WARN], 不假通过。
"""
import ast
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

FAILS = []

FAMILIES = ("Microsoft YaHei UI", "Consolas", "Segoe UI Symbol", "Segoe UI Emoji")

BTN_TUPLE = 'devlink_btn = tkinter.Label(dark_frame, text="🌐", font=("Segoe UI Symbol", 11),'
KEYCAP_TUPLE = 'font=("Consolas", 8, "bold"), bg=C["ac"], fg="white",'

# 白名单: (相对路径, 该行 strip 后的源码) -> 保留原因
#   —— 只有在「无对应字体角色」或「被既有回归断言锁死」时才允许保留元组字面量
WHITELIST = {
    ("src/ACRPA.py", 'font=("Consolas", 9, "bold"), fg=C["fgb"], bg=C["bgc"])'):
        "录制动作计数 = 等宽粗体; _FONT_SPECS 未定义 mono-bold 角色, 保留原视觉",
    ("src/ACRPA.py", BTN_TUPLE):
        "tools/_smoke_dark_mode.py:191 与 tools/_test_ai_provider.py:484 断言该 font "
        "必须为含 11 的元组字面量, 改角色名会打破既有回归",
    ("src/netlink_window.py", 'lbl_pin = tkinter.Label(dlg, text="—", font=("Consolas", 28, "bold"),'):
        "配对码 28pt 大字, 无对应角色, 保留原视觉",
    ("src/settings_window.py", KEYCAP_TUPLE):
        "快捷键键帽 (等宽粗体), 无 mono-bold 角色",
}


def _p(status, msg):
    print("[{}] {}".format(status, msg))


def _read(rel):
    with open(os.path.join(ROOT, rel), "r", encoding="utf-8") as f:
        return f.read()


def _parse(rel):
    return ast.parse(_read(rel), filename=rel)


UI_MODULES = ("src/utils.py", "src/ACRPA.py", "src/settings_window.py",
              "src/dialogs.py", "src/tray.py", "src/netlink_window.py")


def _find_func(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def _src_files():
    """列出 src/ 下全部 .py (相对 ROOT 的 posix 路径)。"""
    out = []
    for dirpath, _dirs, files in os.walk(SRC):
        for fn in files:
            if fn.endswith(".py"):
                full = os.path.join(dirpath, fn)
                out.append(os.path.relpath(full, ROOT).replace(os.sep, "/"))
    return sorted(out)


# ── A. utils.py 静态 AST 断言 ──

def check_utils_ast():
    tree = _parse("src/utils.py")
    top_funcs, top_names = set(), set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            top_funcs.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    top_names.add(t.id)

    for fn in ("init_fonts", "font", "set_ui_scale", "scaled", "dpi_factor",
               "fit_pt", "current_ui_scale"):
        if fn in top_funcs:
            _p("OK", "utils.py 存在 def {}()".format(fn))
        else:
            FAILS.append("utils.py 缺 def {}()".format(fn))
            _p("FAIL", "utils.py 缺 def {}()".format(fn))

    for nm in ("_FONT_SPECS", "UI_SCALE_DEFAULT", "UI_SCALE_MIN", "UI_SCALE_MAX"):
        if nm in top_names:
            _p("OK", "utils.py 存在 {}".format(nm))
        else:
            FAILS.append("utils.py 缺 {}".format(nm))
            _p("FAIL", "utils.py 缺 {}".format(nm))

    # 缩放常量取值
    spec_vals = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name):
            nm = node.targets[0].id
            if nm in ("UI_SCALE_DEFAULT", "UI_SCALE_MIN", "UI_SCALE_MAX") \
                    and isinstance(node.value, ast.Constant):
                spec_vals[nm] = node.value.value
    if spec_vals.get("UI_SCALE_DEFAULT") == 1.0 and spec_vals.get("UI_SCALE_MIN") == 0.8 \
            and spec_vals.get("UI_SCALE_MAX") == 1.5:
        _p("OK", "缩放常量 = 默认 1.0 / 下限 0.8 / 上限 1.5")
    else:
        FAILS.append("缩放常量取值异常: {}".format(spec_vals))
        _p("FAIL", "缩放常量取值异常: {}".format(spec_vals))

    # FONT_* 常量必须是字符串角色名 (不能是元组/列表)
    bad = []
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for t in node.targets:
            if isinstance(t, ast.Name) and t.id.startswith("FONT_"):
                v = node.value
                if not (isinstance(v, ast.Constant) and isinstance(v.value, str)):
                    bad.append(t.id)
    if not bad:
        _p("OK", "FONT_* 常量均为字符串角色名 (非元组)")
    else:
        FAILS.append("FONT_* 仍非字符串: {}".format(bad))
        _p("FAIL", "FONT_* 仍非字符串: {}".format(bad))

    # 角色表必须覆盖 6 个公共角色
    specs = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "_FONT_SPECS" for t in node.targets):
            if isinstance(node.value, ast.Dict):
                specs = node.value
    if specs is None:
        FAILS.append("_FONT_SPECS 不是字面量 dict, 无法静态校验角色")
        _p("FAIL", "_FONT_SPECS 不是字面量 dict")
    else:
        keys = [k.value for k in specs.keys if isinstance(k, ast.Constant)]
        need = {"ACRPA_TITLE", "ACRPA_BODY", "ACRPA_LOG", "ACRPA_SMALL",
                "ACRPA_BUTTON", "ACRPA_SMALL_BOLD", "ACRPA_TINY",
                "ACRPA_ICON", "ACRPA_ICON_LG"}
        missing = sorted(need - set(keys))
        if missing:
            FAILS.append("_FONT_SPECS 缺角色: {}".format(missing))
            _p("FAIL", "_FONT_SPECS 缺角色: {}".format(missing))
        else:
            _p("OK", "_FONT_SPECS 含 {} 个角色 (含全部必需角色)".format(len(keys)))


# ── G. 字体角色名可解析性 ──

def check_font_names_resolvable():
    """每个 UI 模块内裸引用的 FONT_* 名字必须在模块级已绑定 (赋值或 from utils import)。

    这条断言专门防「注入式模块 (settings_window/dialogs) 引用了未被注入的角色名」
    —— 此类错误 py_compile 与普通静态断言都查不出, 只在打开窗口时 NameError。
    """
    for rel in UI_MODULES:
        tree = _parse(rel)
        used, bound = set(), set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id.startswith("FONT_"):
                used.add(node.id)
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name) and t.id.startswith("FONT_"):
                        bound.add(t.id)
            elif isinstance(node, ast.ImportFrom):
                for a in node.names:
                    nm = a.asname or a.name
                    if nm.startswith("FONT_"):
                        bound.add(nm)
        missing = sorted(used - bound)
        if not missing:
            _p("OK", "{} 的 FONT_* 引用全部已绑定 ({})".format(
                rel, ", ".join(sorted(bound)) or "仅用 utils.FONT_* 属性式"))
        else:
            FAILS.append("{} 引用未绑定的字体角色: {}".format(rel, missing))
            _p("FAIL", "{} 引用未绑定的字体角色: {}".format(rel, missing))


# ── B. 元组解包残留扫描 ──

def check_unpack_removed():
    hits = []
    for rel in _src_files():
        src = _read(rel)
        idx = src.find("(*FONT_")
        while idx != -1:
            hits.append("{}:{}".format(rel, src.count("\n", 0, idx) + 1))
            idx = src.find("(*FONT_", idx + 1)
    if not hits:
        _p("OK", "src/ 内已无 (*FONT_ , ...) 元组解包 (3 处已迁移)")
    else:
        FAILS.append("仍有 (*FONT_ 解包: {}".format(hits))
        _p("FAIL", "仍有 (*FONT_ 解包: {}".format(hits))

    # FONT_xxx[...] 下标式取元组元素 (改为字符串后必然出错)
    sub_hits = []
    for rel in _src_files():
        try:
            tree = _parse(rel)
        except SyntaxError as e:
            FAILS.append("{} 语法错误: {}".format(rel, e))
            _p("FAIL", "{} 语法错误: {}".format(rel, e))
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) \
                    and node.value.id.startswith("FONT_"):
                sub_hits.append("{}:{}".format(rel, node.lineno))
    if not sub_hits:
        _p("OK", "src/ 内已无 FONT_*[i] 下标式解构 (settings_window 键帽处已修正)")
    else:
        FAILS.append("仍有 FONT_*[] 下标: {}".format(sub_hits))
        _p("FAIL", "仍有 FONT_*[] 下标: {}".format(sub_hits))


# ── C. 动态断言 (真实 Tk root) ──

def check_dynamic():
    try:
        import tkinter
        import tkinter.font
    except Exception as e:
        _p("WARN", "tkinter 不可用, 跳过动态断言: {}".format(e))
        return
    try:
        import utils
        import state
    except Exception as e:
        FAILS.append("import utils/state 失败: {}".format(e))
        _p("FAIL", "import utils/state 失败: {}".format(e))
        return

    try:
        r = tkinter.Tk()
        r.withdraw()
    except Exception as e:
        _p("WARN", "无法创建 Tk root (无 GUI 环境), 动态断言跳过: {}".format(e))
        _p("INFO", "无 root 时 utils.scaled(100) = {} (dpi_factor 降级为 1.0)".format(
            utils.scaled(100)))
        return

    try:
        utils.init_fonts(r)
        _p("INFO", "dpi_factor() 实测值 = {} (供 DPI 档位决策)".format(utils.dpi_factor()))

        if utils.font("ACRPA_BODY") == "ACRPA_BODY":
            _p("OK", "utils.font('ACRPA_BODY') 返回命名字符串")
        else:
            FAILS.append("utils.font 返回值异常: {}".format(utils.font("ACRPA_BODY")))
            _p("FAIL", "utils.font 返回值异常: {}".format(utils.font("ACRPA_BODY")))

        if utils.font("NO_SUCH_ROLE_XYZ") == utils.FONT_BODY:
            _p("OK", "utils.font(未知角色) 回退 FONT_BODY")
        else:
            FAILS.append("utils.font 未知角色未回退 FONT_BODY")
            _p("FAIL", "utils.font 未知角色未回退 FONT_BODY")

        def _size(role="ACRPA_BODY"):
            # Python 3.9 的 nametofont 不接受 root=; Font(exists=True) 精确取同一命名。
            return int(tkinter.font.Font(root=r, name=role,
                                         exists=True).actual()["size"])

        want = utils.fit_pt(9, 1.0)
        got = _size()
        if got == want:
            _p("OK", "init_fonts 后 ACRPA_BODY 字号 {} == fit_pt(9, 1.0)".format(got))
        else:
            FAILS.append("ACRPA_BODY 字号 {} != fit_pt(9,1.0)={}".format(got, want))
            _p("FAIL", "ACRPA_BODY 字号 {} != fit_pt(9,1.0)={}".format(got, want))

        # 幂等: 再调一次不应报错也不应改变字号
        utils.init_fonts(r)
        if _size() == want:
            _p("OK", "init_fonts 幂等 (重复调用不改变字号)")
        else:
            FAILS.append("init_fonts 非幂等: 字号变为 {}".format(_size()))
            _p("FAIL", "init_fonts 非幂等: 字号变为 {}".format(_size()))

        # set_ui_scale(1.2)
        eff = utils.set_ui_scale(1.2)
        big = utils.fit_pt(9, 1.2)
        if abs(eff - 1.2) < 1e-9 and abs(float(state.UI_SCALE) - 1.2) < 1e-9 \
                and _size() == big and big > want:
            _p("OK", "set_ui_scale(1.2) -> state.UI_SCALE=1.2, 字号 {} -> {}".format(want, big))
        else:
            FAILS.append("set_ui_scale(1.2) 异常: eff={} state={} size={} want={}".format(
                eff, getattr(state, "UI_SCALE", None), _size(), big))
            _p("FAIL", "set_ui_scale(1.2) 异常: eff={} state={} size={}".format(
                eff, getattr(state, "UI_SCALE", None), _size()))

        # clamp 上限
        eff = utils.set_ui_scale(9.9)
        if abs(eff - 1.5) < 1e-9 and abs(float(state.UI_SCALE) - 1.5) < 1e-9:
            _p("OK", "set_ui_scale(9.9) 被 clamp 到 1.5")
        else:
            FAILS.append("set_ui_scale(9.9) 未 clamp 到 1.5: {}".format(eff))
            _p("FAIL", "set_ui_scale(9.9) 未 clamp 到 1.5: {}".format(eff))

        # clamp 下限
        eff = utils.set_ui_scale(0.1)
        if abs(eff - 0.8) < 1e-9:
            _p("OK", "set_ui_scale(0.1) 被 clamp 到 0.8")
        else:
            FAILS.append("set_ui_scale(0.1) 未 clamp 到 0.8: {}".format(eff))
            _p("FAIL", "set_ui_scale(0.1) 未 clamp 到 0.8: {}".format(eff))

        # 复位
        eff = utils.set_ui_scale(1.0)
        if abs(eff - 1.0) < 1e-9 and _size() == want and abs(utils.current_ui_scale() - 1.0) < 1e-9:
            _p("OK", "set_ui_scale(1.0) 复位, 字号回到 {}".format(want))
        else:
            FAILS.append("set_ui_scale(1.0) 复位失败: size={}".format(_size()))
            _p("FAIL", "set_ui_scale(1.0) 复位失败: size={}".format(_size()))

        # scaled()
        s100 = utils.scaled(100)
        if isinstance(s100, int) and s100 >= 100:
            _p("OK", "scaled(100) = {} (ui_scale=1.0, dpi_factor={})".format(
                s100, utils.dpi_factor()))
        else:
            FAILS.append("scaled(100) 异常: {!r}".format(s100))
            _p("FAIL", "scaled(100) 异常: {!r}".format(s100))

        if utils.scaled(0) == 1 and utils.scaled(-5) == 1:
            _p("OK", "scaled() 下限为 1 (含 0/负数输入)")
        else:
            FAILS.append("scaled() 下限未生效")
            _p("FAIL", "scaled() 下限未生效")
    except Exception as e:
        FAILS.append("动态断言异常: {!r}".format(e))
        _p("FAIL", "动态断言异常: {!r}".format(e))
    finally:
        try:
            r.destroy()
        except Exception:
            pass


# ── D. 替换计数校验 ──

def _hardcoded_font_tuples(rel):
    """返回 (lineno, 该行 strip) 列表; utils.py 的 _FONT_SPECS 是唯一真源, 不计入。"""
    src = _read(rel)
    tree = ast.parse(src, filename=rel)
    skip = set()
    if rel == "src/utils.py":
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == "_FONT_SPECS"
                    for t in node.targets):
                for sub in ast.walk(node.value):
                    skip.add(id(sub))
    lines = src.splitlines()
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Tuple) and len(node.elts) >= 2 and id(node) not in skip:
            first = node.elts[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str) \
                    and first.value in FAMILIES:
                out.append((node.lineno, lines[node.lineno - 1].strip()))
    return out


def check_replacements():
    total = 0
    allowed = 0
    unlisted = []
    seen = set()
    for rel in _src_files():
        for lineno, text in _hardcoded_font_tuples(rel):
            total += 1
            if (rel, text) in WHITELIST:
                allowed += 1
                seen.add((rel, text))
            else:
                unlisted.append("{}:{} {}".format(rel, lineno, text))
    if unlisted:
        FAILS.append("存在未列白名单的硬编码字体元组: {}".format(unlisted))
        _p("FAIL", "存在未列白名单的硬编码字体元组:")
        for u in unlisted:
            print("        {}".format(u))
    else:
        _p("OK", "剩余硬编码字体元组 {} 处, 全部命中白名单 ({} 条白名单条目已用)".format(
            total, len(seen)))
        for rel, text in sorted(seen):
            _p("INFO", "白名单 {} :: {}".format(rel, WHITELIST[(rel, text)]))
    # 白名单条目若已不再需要, 提示清理 (不算 FAIL)
    for key in WHITELIST:
        if key not in seen:
            _p("WARN", "白名单条目已失效 (可清理): {}".format(key[0]))
    return total, allowed


# ── E. 守护断言 ──

def check_guards():
    # workflow.py 未被改动
    try:
        proc = subprocess.run(["git", "diff", "--stat", "--", "src/workflow.py"],
                              cwd=ROOT, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT)
        out = (proc.stdout or b"").decode("utf-8", "ignore").strip()
        if proc.returncode == 0 and not out:
            _p("OK", "git diff --stat -- src/workflow.py 为空 (workflow.py 未改动)")
        else:
            FAILS.append("workflow.py 疑似被改动: {}".format(out))
            _p("FAIL", "workflow.py 疑似被改动: {}".format(out))
    except Exception as e:
        _p("WARN", "无法校验 workflow.py (git 不可用): {}".format(e))

    tree = _parse("src/ACRPA.py")
    counts = {"open_devlink": 0, "set_control_hooks": 0,
              "start_netlink": 0, "_nl_hook_run": 0}
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef):
            if n.name in ("open_devlink", "_nl_hook_run"):
                counts[n.name] += 1
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
            if n.func.attr in ("set_control_hooks", "start_netlink"):
                counts[n.func.attr] += 1
    for k, v in sorted(counts.items()):
        if v == 1:
            _p("OK", "守护: NetLink 块 '{}' 出现 1 次".format(k))
        else:
            FAILS.append("NetLink 块 '{}' 出现 {} 次 (期望 1)".format(k, v))
            _p("FAIL", "NetLink 块 '{}' 出现 {} 次 (期望 1)".format(k, v))

    if _find_func(tree, "_mb_animate_width") is not None:
        _p("OK", "守护: 存在 _mb_animate_width (Mini Bar 宽度动画)")
    else:
        FAILS.append("守护: 缺 _mb_animate_width")
        _p("FAIL", "守护: 缺 _mb_animate_width")

    node = _find_func(tree, "_destroy_mini_bar")
    has_cancel = False
    if node is not None:
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) \
                    and sub.func.attr == "after_cancel":
                has_cancel = True
    if has_cancel:
        _p("OK", "守护: _destroy_mini_bar 含 after_cancel (定时器可取消)")
    else:
        FAILS.append("守护: _destroy_mini_bar 缺 after_cancel")
        _p("FAIL", "守护: _destroy_mini_bar 缺 after_cancel")

    if _find_func(_parse("src/utils.py"), "themed") is not None:
        _p("OK", "守护: utils.themed 仍在")
    else:
        FAILS.append("守护: utils.themed 丢失")
        _p("FAIL", "守护: utils.themed 丢失")

    if "PROVIDER_PRESETS" in _read("src/ai_client.py"):
        _p("OK", "守护: ai_client.PROVIDER_PRESETS 仍在")
    else:
        FAILS.append("守护: ai_client.PROVIDER_PRESETS 丢失")
        _p("FAIL", "守护: ai_client.PROVIDER_PRESETS 丢失")

    if _find_func(_parse("src/py_sandbox.py"), "precheck") is not None:
        _p("OK", "守护: py_sandbox.precheck 仍在")
    else:
        FAILS.append("守护: py_sandbox.precheck 丢失")
        _p("FAIL", "守护: py_sandbox.precheck 丢失")

    dev_ok = False
    for n in ast.walk(tree):
        if isinstance(n, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "devlink_btn" for t in n.targets) \
                and isinstance(n.value, ast.Call):
            kw = {k.arg: k.value for k in n.value.keywords}
            if getattr(kw.get("text"), "value", None) == "🌐":
                dev_ok = True
    if dev_ok:
        _p("OK", "守护: devlink_btn 仍为 text='🌐' (保留元组字面量字号 11)")
    else:
        FAILS.append("守护: devlink_btn text 不再是 '🌐'")
        _p("FAIL", "守护: devlink_btn text 不再是 '🌐'")


# ── F. 依赖探测 ──

def probe_deps():
    code = "import pyautogui, xlrd, pyperclip"
    try:
        proc = subprocess.run([sys.executable, "-c", code],
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              cwd=ROOT)
        rc = proc.returncode
        tail = (proc.stdout or b"").decode("utf-8", "ignore").strip()
    except Exception as e:
        rc, tail = 1, str(e)
    if rc == 0:
        _p("OK", "依赖探测: python -c \"{}\" 通过 (rc=0)".format(code))
    else:
        _p("WARN", "依赖探测: python -c \"{}\" 失败 (rc={}) :: {}".format(
            code, rc, tail.splitlines()[-1] if tail else ""))
    return rc


def main():
    print("=== ACRPA 显示大小与字体缩放 (pt 制) 自测 ===")
    print("--- A. utils.py 静态 AST 断言 ---")
    check_utils_ast()
    print("--- B. 字体角色名可解析性 ---")
    check_font_names_resolvable()
    print("--- B2. 元组解包残留扫描 ---")
    check_unpack_removed()
    print("--- C. 动态断言 (真实 Tk root) ---")
    check_dynamic()
    print("--- D. 硬编码字体元组替换计数校验 ---")
    total, allowed = check_replacements()
    print("    合计: 剩余 {} 处 (白名单 {} 处, 白名单条目 {} 条)".format(
        total, allowed, len(WHITELIST)))
    print("--- E. 守护断言 ---")
    check_guards()
    print("--- F. 依赖探测 ---")
    probe_deps()
    print("=== 结果: {} ===".format(
        "FAIL ({})".format(len(FAILS)) if FAILS else "OK"))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
