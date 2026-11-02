#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""i18n 字符串目录回归自测 (路线图 §5.6「i18n 字符串目录」第一刀)。

用法:
    .venv\\Scripts\\python.exe -X utf8 tools\\_test_i18n.py

覆盖:
  A  目录完整性 : zh 与 en 键集合一致; len(keys()) >= 40; zh/en 均无空模板。
  B  t() 语义   : 已知 key 精确值; **fmt 插值正确; 未知 key 回退返回 key 本身;
                  缺参不抛 (回退未插值模板); set_language("en") 后同一 key 返回英文
                  且默认 zh 可复位; set_language("xx") 不抛且回退 zh。
  C  纯净度      : 子进程 import i18n 后 tkinter 不在 sys.modules (零副作用)。
  D  静态接线    : src/ACRPA.py 与 src/ui/exec_bar.py 文本含 `import i18n` 与
                  `i18n.t(`; 且已抽取、未被既有测试断言的旧字面量 (抽样) 已不在。
  E  逐字一致    : 用 t() 组装的状态串 == 抽取前的等价字面量串
                  (就绪 / 运行中 / 已暂停 / 已停止 各一例)。

退出码: 0=全部通过 / 1=有失败。
"""
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

_PASS, _FAIL = [], []


def check(cond, msg, detail=""):
    (_PASS if cond else _FAIL).append(msg)
    tail = ("  ({})".format(detail) if detail else "")
    print("[{}] {}{}".format("OK  " if cond else "FAIL", msg, tail))


def _read(rel):
    with io.open(os.path.join(ROOT, rel), "r", encoding="utf-8") as f:
        return f.read()


def _child(code, timeout=30):
    """在子进程里执行一段 python → (rc|None(超时), stdout, stderr)。"""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        p = subprocess.run([sys.executable, "-X", "utf8", "-c", code],
                           cwd=ROOT, env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, "", "TIMEOUT after {}s".format(timeout)
    return (p.returncode,
            (p.stdout or b"").decode("utf-8", "replace"),
            (p.stderr or b"").decode("utf-8", "replace"))


import i18n  # noqa: E402


# ── A. 目录完整性 ─────────────────────────────────────────────────────
def t_catalog():
    print("\n── A. 目录完整性 ──")
    check(isinstance(i18n.LANGUAGES, tuple) and i18n.LANGUAGES == ("zh", "en"),
          "LANGUAGES == ('zh', 'en')", str(i18n.LANGUAGES))
    check(i18n.DEFAULT_LANGUAGE == "zh", "DEFAULT_LANGUAGE == 'zh'",
          repr(i18n.DEFAULT_LANGUAGE))
    zh, en = i18n.CATALOG["zh"], i18n.CATALOG["en"]
    only_zh = sorted(set(zh) - set(en))
    only_en = sorted(set(en) - set(zh))
    check(not only_zh and not only_en, "zh 与 en 键集合一致",
          "only_zh={} only_en={}".format(only_zh, only_en))
    check(len(i18n.keys()) >= 40, "len(keys()) >= 40 ({})".format(len(i18n.keys())))
    check(i18n.keys() == set(zh.keys()), "keys() == zh 目录键集合")
    empties = [k for k, v in zh.items() if not v] + [k for k, v in en.items() if not v]
    check(not empties, "zh/en 均无空模板", "empty={}".format(empties))


# ── B. t() 语义 ──────────────────────────────────────────────────────
def t_semantics():
    print("\n── B. t() 语义 ──")
    i18n.set_language("zh")
    check(i18n.t("status.dot_ready") == "● 就绪", "已知 key 精确值 (zh)",
          repr(i18n.t("status.dot_ready")))
    check(i18n.t("status.rows", done=1, total=3) == "行 1/3", "**fmt 插值正确",
          repr(i18n.t("status.rows", done=1, total=3)))
    check(i18n.t("no.such.key") == "no.such.key",
          "未知 key 回退返回 key 本身", repr(i18n.t("no.such.key")))
    check(i18n.t("status.rows") == "行 {done}/{total}",
          "缺参不抛, 回退未插值模板", repr(i18n.t("status.rows")))
    check(i18n.t("status.ready", mod=" *") == " 就绪{mod}{ai}  |  {rows} 行  |  请选择脚本文件开始",
          "缺参不抛: 回退为未插值模板", repr(i18n.t("status.ready", mod=" *")))
    i18n.set_language("en")
    check(i18n.get_language() == "en", "get_language() == 'en'")
    check(i18n.t("status.dot_ready") == "● Ready",
          "set_language('en') 后同一 key 返回英文", repr(i18n.t("status.dot_ready")))
    i18n.set_language("zh")
    check(i18n.get_language() == "zh" and i18n.t("status.dot_ready") == "● 就绪",
          "默认 zh 可复位")
    i18n.set_language("xx")   # 未知语言: 不抛
    check(i18n.get_language() == "zh", "set_language('xx') 回退 zh (get_language)")
    check(i18n.t("status.dot_ready") == "● 就绪",
          "set_language('xx') 后 t() 回退 zh")
    i18n.set_language("zh")


# ── C. 纯净度 (零副作用) ──────────────────────────────────────────────
def t_purity():
    print("\n── C. import i18n 零副作用 ──")
    code = ("import sys; sys.path.insert(0, {src!r}); "
            "import i18n; "
            "print('TK=%s' % ('tkinter' in sys.modules))").format(src=SRC)
    rc, out, err = _child(code)
    check(rc == 0, "子进程 import i18n 正常退出 (rc={})".format(rc),
          (err or "").strip()[-200:])
    check("TK=False" in out, "import i18n 未加载 tkinter", out.strip())


# ── D. 静态接线 ───────────────────────────────────────────────────────
def t_static_wiring():
    print("\n── D. 静态接线 (ACRPA.py / exec_bar.py) ──")
    acrpa = _read("src/ACRPA.py")
    execb = _read("src/ui/exec_bar.py")
    for rel, src in (("src/ACRPA.py", acrpa), ("src/ui/exec_bar.py", execb)):
        check("import i18n" in src, "{} 含 import i18n".format(rel))
        check("i18n.t(" in src, "{} 含 i18n.t(".format(rel))
    # 已抽取、未被既有测试断言的旧字面量应不再出现在源文件 (抽样 >=3)
    gone = [
        (acrpa, "src/ACRPA.py", '" 运行中 — 正在执行自动化任务"'),
        (acrpa, "src/ACRPA.py", '" 已停止 — 点击开始运行重新启动"'),
        (acrpa, "src/ACRPA.py", '" 就绪{}{}  |  {} 行  |  请选择脚本文件开始"'),
        (acrpa, "src/ACRPA.py", '"尚未运行 · 选择脚本后点击运行"'),
        (execb, "src/ui/exec_bar.py", 'text="▶ 运行"'),
        (execb, "src/ui/exec_bar.py", '"没有选择文件"'),
        (execb, "src/ui/exec_bar.py", 'text="出错即停"'),
    ]
    for src, rel, lit in gone:
        check(lit not in src, "{} 旧字面量已移除: {}".format(rel, lit))


# ── E. 逐字一致 (关键) ────────────────────────────────────────────────
def t_byte_exact():
    print("\n── E. t() 组装 == 旧字面量 (逐字一致) ──")
    i18n.set_language("zh")

    # 就绪: 旧 " 就绪{}{}  |  {} 行  |  请选择脚本文件开始".format(mod, ai, rows)
    old_ready = " 就绪{}{}  |  {} 行  |  请选择脚本文件开始".format(" *", " [AI]", 5)
    new_ready = i18n.t("status.ready", mod=" *", ai=" [AI]", rows=5)
    check(new_ready == old_ready, "就绪 状态串逐字一致", repr(new_ready))

    old_running = " 运行中 — 正在执行自动化任务"
    check(i18n.t("status.running") == old_running, "运行中 状态串逐字一致",
          repr(i18n.t("status.running")))

    old_paused = " 已暂停 — 点击继续恢复执行"
    check(i18n.t("status.paused") == old_paused, "已暂停 状态串逐字一致",
          repr(i18n.t("status.paused")))

    old_stopped = " 已停止 — 点击开始运行重新启动"
    check(i18n.t("status.stopped") == old_stopped, "已停止 状态串逐字一致",
          repr(i18n.t("status.stopped")))

    # 附加: 仪表盘串 (循环 / 行 / 已用 / ETA / 计数) 逐字一致
    old_dash = "循环 {}/{}  ·  行 {}/{}  ·  已用 {}  ·  ETA {}".format(
        2, 3, 1, 3, "00:05", "00:10")
    new_dash = (i18n.t("status.loop", cur=2, total=3) + i18n.t("status.sep")
                + i18n.t("status.rows", done=1, total=3) + i18n.t("status.sep")
                + i18n.t("status.elapsed", elapsed="00:05") + i18n.t("status.sep")
                + i18n.t("status.eta", eta="00:10"))
    check(new_dash == old_dash, "仪表盘串逐字一致", repr(new_dash))

    old_counts = "  ·  成功 {}  失败 {}".format(7, 2)
    check(i18n.t("status.counts", ok=7, fail=2) == old_counts,
          "计数片段逐字一致", repr(i18n.t("status.counts", ok=7, fail=2)))


def main():
    print("=== i18n 字符串目录回归 (路线图 §5.6) ===")
    t_catalog()
    t_semantics()
    t_purity()
    t_static_wiring()
    t_byte_exact()
    print("\n=== 结果: {} (通过 {}, 失败 {}) ===".format(
        "FAIL" if _FAIL else "OK", len(_PASS), len(_FAIL)))
    if _FAIL:
        for m in _FAIL:
            print("  - {}".format(m))
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
