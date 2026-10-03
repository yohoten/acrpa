# -*- coding: utf-8 -*-
"""脚本编辑区缩放 + 通用快捷键 回归。

需求（用户提出）：
  1. 脚本编辑区的 Excel 表格要能放大缩小 —— Ctrl+滚轮、Ctrl+加号 / Ctrl+减号
  2. 要支持通用快捷键 —— Ctrl+S 保存、Ctrl+Shift+S 另存、Ctrl+N 新建 …

断言：
  Z1 配置项      : state 有 editor_zoom（默认 0，int）
  Z2 独立样式    : 编辑区用派生样式 ScriptEditor.Treeview（不牵连其它 Treeview）
  Z3 绑定点      : Ctrl+滚轮 / Ctrl++ / Ctrl+= / Ctrl+- / Ctrl+0 均已绑定
  Z4 快捷键      : 窗口级绑定 Ctrl+S / Ctrl+Shift+S / Ctrl+N / Ctrl+O，且指向正确命令
  Z5 子窗口护栏  : 只在主窗口聚焦时执行（避免在设置/AI 窗口里误存脚本）
  Z6 可发现性    : 工具栏提示里写明了快捷键
  Z7 真实行为    : 起一个真实进程导入 ACRPA，调缩放 → 字号真的变大 → 复位 → 真的还原
                   （这一步不是静态断言，是实机执行）

用法: .venv\\Scripts\\python.exe -X utf8 tools\\_test_ui_zoom_shortcuts.py
退出码: 0=全部通过, 1=存在失败
"""
import ast
import os
import re
import subprocess
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ACRPA_SRC = os.path.join(BASE, "src", "ACRPA.py")
STATE_SRC = os.path.join(BASE, "src", "state.py")

_PASS, _FAIL = [], []


def check(cond, msg):
    (_PASS if cond else _FAIL).append(msg)
    print("[{}] {}".format("OK  " if cond else "FAIL", msg))


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def t_config():
    print("\n── Z1 配置项 ──")
    src = read(STATE_SRC)
    m = re.search(r'\("editor_zoom",\s*(\S+?),\s*(\w+)\)', src)
    check(m is not None, "state 配置 schema 含 editor_zoom")
    if m:
        check(m.group(1) == "0" and m.group(2) == "int",
              "默认值与类型: {} / {}".format(m.group(1), m.group(2)))


def t_source():
    print("\n── Z2/Z3/Z4/Z5/Z6 源码断言 ──")
    src = read(ACRPA_SRC)

    check('_EDITOR_STYLE = "ScriptEditor.Treeview"' in src,
          "编辑区使用派生样式, 不影响变量/时序/工作流列表")
    check("_apply_editor_zoom()" in src and "tree.configure(style=_EDITOR_STYLE)" in src,
          "启动时应用缩放并挂到 tree")
    check("_apply_editor_zoom()" in src.split("register_ui_scale_hook")[1][:200]
          if "register_ui_scale_hook" in src else False,
          "ui_scale 变更后重新应用 (基准字号会变)")

    for seq in ("<Control-MouseWheel>", "<Control-plus>", "<Control-equal>",
                "<Control-minus>", "<Control-Key-0>"):
        check(seq in src, "绑定了 {}".format(seq))

    check("root.bind_all(_seq" in src or "root.bind_all(_seq, _hotkey(_fn))" in src,
          "快捷键走窗口级 bind_all")
    for seq, fn in (("<Control-s>", "_cmd_save"), ("<Control-S>", "_cmd_save_as"),
                    ("<Control-n>", "_cmd_new"), ("<Control-o>", "_cmd_open")):
        pat = r'\("{}",\s*{}\s*\)'.format(re.escape(seq), fn)
        check(re.search(pat, src) is not None,
              "{} → {}".format(seq, fn))
    check("def _main_window_focused" in src and "winfo_toplevel() is root" in src,
          "仅主窗口聚焦时生效 (子窗口护栏)")

    for label, tip in (("新建", "Ctrl+N"), ("打开", "Ctrl+O"),
                       ("保存", "Ctrl+S"), ("另存", "Ctrl+Shift+S")):
        check(tip in src, "工具栏「{}」提示写明 {}".format(label, tip))


def t_live():
    print("\n── Z7 实机行为 (真实进程导入 ACRPA) ──")
    snippet = r'''
import os, sys
sys.path.insert(0, os.path.join(r"{base}", "src"))
sys.path.insert(0, r"{base}")
import ACRPA, state, utils, tkinter
try:
    before = int(ACRPA._editor_font.cget("size"))
    base = int(ACRPA._editor_base_size())
    ACRPA._editor_zoom_step(3, silent=True)
    after = int(ACRPA._editor_font.cget("size"))
    zoom_saved = int(getattr(state, "EDITOR_ZOOM", -999))
    style_font = tkinter.font.Font(root=ACRPA.root, name=ACRPA._editor_font.name, exists=True).cget("size")
    ACRPA._editor_zoom_reset()
    reset = int(ACRPA._editor_font.cget("size"))
    ACRPA._editor_zoom_step(-999, silent=True)
    low = int(ACRPA._editor_font.cget("size"))
    ACRPA._editor_zoom_step(999, silent=True)
    high = int(ACRPA._editor_font.cget("size"))
    seqs = [s for s in ACRPA.root.bind_all() if "Control" in s and "-" in s]
    ctrl_s = bool(ACRPA.root.bind_all("<Control-s>"))
    has_guard = callable(getattr(ACRPA, "_main_window_focused", None))
    tree_style = str(ACRPA.tree.cget("style"))
    print("RESULT before=%d base=%d after=%d saved=%d reset=%d low=%d high=%d style=%s ctrl_s=%s guard=%s tree_style=%s"
          % (before, base, after, zoom_saved, reset, low, high, style_font, ctrl_s, has_guard, tree_style))
    sys.stdout.flush()
finally:
    os._exit(0)   # 必须显式 flush: os._exit 不走 atexit, 管道下缓冲会丢输出
'''.format(base=BASE.replace("\\", "/"))

    try:
        p = subprocess.run([sys.executable, "-X", "utf8", "-c", snippet],
                           cwd=BASE, capture_output=True, timeout=180)
        out = (p.stdout or b"").decode("utf-8", "replace")
        err = (p.stderr or b"").decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        check(False, "实机进程超时")
        return

    m = re.search(r"RESULT (.+)", out)
    if not m:
        check(False, "实机执行未产出结果: {}".format((err or out)[-300:]))
        return
    kv = dict(x.split("=", 1) for x in m.group(1).split())
    print("      " + m.group(1))

    check(int(kv["after"]) == int(kv["base"]) + 3,
          "放大 3 档后字号 = 基准+3 ({} → {})".format(kv["before"], kv["after"]))
    check(kv["saved"] == "3", "倍率已写入 state.EDITOR_ZOOM")
    check(int(kv["reset"]) == int(kv["base"]), "Ctrl+0 复位回基准字号")
    check(int(kv["low"]) >= 6, "下限被钳制 (最小 {}pt)".format(kv["low"]))
    check(int(kv["high"]) <= 40, "上限被钳制 (最大 {}pt)".format(kv["high"]))
    check(kv["tree_style"] == "ScriptEditor.Treeview", "tree 使用独立样式")
    check(kv["ctrl_s"] == "True", "Ctrl+S 已注册到窗口")
    check(kv["guard"] == "True", "存在主窗口聚焦护栏")


def main():
    print("=" * 68)
    print("编辑区缩放 + 通用快捷键 回归")
    print("=" * 68)
    t_config()
    t_source()
    t_live()
    print("\n" + "=" * 68)
    print("通过 {} / 失败 {}".format(len(_PASS), len(_FAIL)))
    print("结论: {}".format("FAIL" if _FAIL else "PASS"))
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
