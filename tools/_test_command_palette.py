#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""命令库 (Ctrl+K) 与命令分组 回归自测 (路线图 §5.3)。

用法:
    .venv\\Scripts\\python.exe -X utf8 tools\\_test_command_palette.py

覆盖:
  A  导入无副作用 : import ui.command_palette 后 sys.modules 无 tkinter。
  B  条目/分组      : entries() 覆盖注册表(≥60); grouped() 含 command_groups.json 的
                      分组标题; 每组非空; 未映射命令落兜底组 (不丢失)。
  C  过滤/排序      : 空 query==全部; 精确名优先; 前缀/包含命中; 说明命中;
                      多关键字 AND; 大小写不敏感(ai→AI*); 无匹配→空。
  D  静态接线      : ACRPA.py 含 `def _kb_command_palette(` / `"<Control-k>"` /
                      惰性 `from ui import command_palette`(非模块顶层)。
  E  弹窗冒烟      : 真实 Tk 下 open_palette 建出 Toplevel(含 Entry+Listbox+条目);
                     无 GUI 环境记 [WARN](不假通过)。

退出码: 0=全部通过 / 1=有失败。
"""
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


# ── A. 导入无副作用 ──────────────────────────────────────────────────
def t_import_side_effect_free():
    print("\n── A. 导入无副作用 ──")
    before = set(sys.modules)
    import ui.command_palette as cp  # noqa: F401
    after = set(sys.modules)
    newly = {m for m in (after - before) if m == "tkinter" or m.startswith("tkinter.")}
    check(not newly, "import ui.command_palette 未引入 tkinter", "newly={}".format(sorted(newly)))
    check(hasattr(cp, "open_palette") and hasattr(cp, "filter_entries"),
          "模块导出 open_palette / filter_entries")


# ── B. 条目 / 分组 ───────────────────────────────────────────────────
def t_entries_groups():
    print("\n── B. 条目 / 分组 ──")
    import commands
    import ui.command_palette as cp

    ents = cp.entries()
    n_reg = len(commands.list_names())
    check(len(ents) >= 60, "entries() 覆盖 ≥60 条命令",
          "entries={} registry={}".format(len(ents), n_reg))
    names = {e["name"] for e in ents}
    missing = [n for n in commands.list_names() if n not in names]
    # 兜底组保证注册表命令不丢失（除空名）
    check(not missing, "注册表命令全部出现在命令库中", "缺失={}".format(missing[:8]))

    g = cp.grouped()
    titles = cp.group_titles()
    check(len(g) >= 5, "分组数 ≥5", "groups={}".format(len(g)))
    check(titles == [t for t, _ in g], "group_titles() 与 grouped() 一致")
    # command_groups.json 里的预期分组标题
    for want in ("基础操作", "流程控制", "浏览器自动化"):
        check(want in titles, "含分组「{}」".format(want))
    check(all(items for _t, items in g), "每个分组均非空")


# ── C. 过滤 / 排序 ───────────────────────────────────────────────────
def t_filter():
    print("\n── C. 过滤 / 排序 ──")
    import ui.command_palette as cp

    all_e = cp.entries()
    check(cp.filter_entries("") == all_e, "空 query 返回全部")
    check(cp.filter_entries("   ") == all_e, "纯空白 query 返回全部")

    r = cp.filter_entries("点图")
    check(bool(r) and r[0]["name"] == "点图", "精确名「点图」排首位",
          "top={}".format(r[0]["name"] if r else None))

    r2 = [e["name"] for e in cp.filter_entries("AI")]
    check(any(n.startswith("AI") for n in r2), "大写 AI 命中 AI* 命令", "{}".format(r2[:4]))
    r3 = [e["name"] for e in cp.filter_entries("ai")]
    check(set(r2) == set(r3) or any(n.startswith("AI") for n in r3),
          "查询大小写不敏感(ai 命中 AI*)", "{}".format(r3[:4]))

    r4 = [e["name"] for e in cp.filter_entries("浏览器 点击")]
    check("浏览器点击" in r4, "多关键字 AND 命中「浏览器点击」", "{}".format(r4[:5]))

    check(cp.filter_entries("zzz不存在zzz") == [], "无匹配返回空")

    # 说明字段命中: 取任意有 desc 的条目, 用其 desc 首个非空白字符做查询
    with_desc = [e for e in all_e if e.get("desc") and e["desc"].strip()]
    if with_desc:
        e = with_desc[0]
        tok = "".join(e["desc"].split())[0]
        hit = [x["name"] for x in cp.filter_entries(tok)]
        check(e["name"] in hit, "说明字段可被命中",
              "token={!r} 目标={}".format(tok, e["name"]))


# ── D. 静态接线 ──────────────────────────────────────────────────────
def t_wiring():
    print("\n── D. 静态接线 ──")
    src = _read("src/ACRPA.py")
    check("def _kb_command_palette(" in src, "ACRPA.py 定义 _kb_command_palette")
    check('"<Control-k>"' in src, "ACRPA.py 绑定 <Control-k>")
    check("from ui import command_palette" in src, "ACRPA.py 惰性导入 ui.command_palette")
    # 惰性: 顶层不得 import ui.command_palette（应位于函数体内）
    lines = src.splitlines()
    top_level_import = any(
        ln.startswith(("import ui.command_palette", "from ui.command_palette"))
        for ln in lines)
    check(not top_level_import, "ui.command_palette 未被顶层导入 (惰性)")
    # 命令下拉仍走能力角标接口 (未破坏)
    check("commands.display_names()" in src, "命令下拉仍使用 display_names()")


# ── E. 弹窗冒烟 (真实 Tk) ────────────────────────────────────────────
def t_popup_smoke():
    print("\n── E. 弹窗冒烟 ──")
    try:
        import tkinter
    except Exception as e:
        warn("tkinter 不可用, 跳过弹窗冒烟: {!r}".format(e))
        return
    try:
        root = tkinter.Tk()
    except Exception as e:
        warn("无法创建 Tk root (无显示), 跳过弹窗冒烟: {!r}".format(e))
        return
    root.withdraw()
    import ui.command_palette as cp
    picked = []
    win = None
    try:
        win = cp.open_palette(root, {"bgc": "#FFFFFF", "fgb": "#333", "ebg": "#FFF",
                                     "fgt": "#000", "fgm": "#666", "acl": "#CCE4F7",
                                     "bd": "#DDD", "ac": "#0078D4"},
                              {"body": ("Microsoft YaHei UI", 10),
                               "small": ("Microsoft YaHei UI", 9)},
                              lambda n: picked.append(n))
        for _ in range(3):
            root.update_idletasks()
            root.update()

        def find(w, cls, acc):
            for c in w.winfo_children():
                if isinstance(c, cls):
                    acc.append(c)
                find(c, cls, acc)

        entries, listboxes = [], []
        find(win, tkinter.Entry, entries)
        find(win, tkinter.Listbox, listboxes)
        check(any(isinstance(w, tkinter.Toplevel) for w in root.winfo_children()) or bool(win),
              "open_palette 建出 Toplevel")
        check(bool(entries), "弹窗含搜索 Entry")
        check(bool(listboxes), "弹窗含结果 Listbox")
        if listboxes:
            n = listboxes[0].size()
            check(n >= 1, "Listbox 已填充条目", "rows={}".format(n))
    except Exception as e:
        check(False, "open_palette 未抛异常", repr(e))
    finally:
        try:
            if win is not None:
                win.destroy()
        except Exception:
            pass
        try:
            root.destroy()
        except Exception:
            pass


def main():
    print("=== 命令库 (Ctrl+K) / 命令分组 回归自测 ===")
    t_import_side_effect_free()
    t_entries_groups()
    t_filter()
    t_wiring()
    t_popup_smoke()

    print("\n" + "-" * 60)
    print("PASS={} FAIL={} WARN={}".format(len(_PASS), len(_FAIL), len(_WARN)))
    for m in _FAIL:
        print("[FAIL] {}".format(m))
    for m in _WARN:
        print("[WARN] {}".format(m))
    print("=== 结果: {} ===".format("FAIL" if _FAIL else "OK"))
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
