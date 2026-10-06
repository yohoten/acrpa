#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""阶段三-5「尺寸 token 系统化 + 对话框落位 + 标题栏可达性」回归自测 (路线图 §5.5)。

用法:
    .venv\\Scripts\\python.exe -X utf8 tools\\_test_ui_a11y_tokens.py

覆盖:
  A 尺寸 token / 缩放 : utils 暴露 TOKENS / sp / ctrl_h / scaled / dialog_geometry;
                        scaled(100) 随 set_ui_scale 单调变化;
                        主窗口 minsize 与默认几何派生出 token (win_min_*/win_w/win_h);
                        dialog_geometry 为纯函数且结果落在虚拟屏内 (headless)。
  B 硬编码几何清理    : 目标文件集合内字面量 "\\d+x\\d+[+-]\\d+[+-]\\d+" 计数 ≤ 白名单。
  C 可达性静态        : 标题栏图标处调用 utils.bind_icon_activate (takefocus / <Return> /
                        <space> / accessible-name); _FONT_SPECS 正文字号下限 ≥ 9。
  D 可达性实机        : (WARN 守卫) app.build() 后标题栏图标 cget("takefocus") 为真
                        且 bind("<Return>") / bind("<space>") 非空。

退出码: 0=全部通过 / 1=存在失败。无显示环境下 D 段记 [WARN], 不判失败。
"""
import json
import os
import re
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

_PASS, _FAIL, _WARN = [], [], []


def check(cond, msg, detail=""):
    (_PASS if cond else _FAIL).append(msg)
    tail = ("  ({})".format(detail) if detail else "")
    print("[{}] {}{}".format("OK  " if cond else "FAIL", msg, tail))


def warn(msg):
    _WARN.append(msg)
    print("[WARN] {}".format(msg))


def _read(rel):
    with open(os.path.join(ROOT, rel), "r", encoding="utf-8") as f:
        return f.read()


def _parse_geo(geo):
    m = re.match(r"^(\d+)x(\d+)([+-]\d+)([+-]\d+)$", geo or "")
    if not m:
        return None
    return tuple(int(m.group(i)) for i in range(1, 5))


import utils  # noqa: E402


# ── A. 尺寸 token / 缩放 ──────────────────────────────────────────────

def t_tokens_api():
    print("\n── A1. utils token API ──")
    for fn in ("sp", "ctrl_h", "scaled", "tk_px", "gap", "icon_size", "dialog_geometry",
               "place_dialog", "set_accessible_name", "bind_icon_activate"):
        check(callable(getattr(utils, fn, None)), "utils.{} 可调用".format(fn))
    keys = ("sp_xs", "sp_md", "ctrl_h", "gap", "row_h",
            "win_w", "win_h", "win_min_w", "win_min_h",
            "win_compact_w", "win_compact_h")
    miss = [k for k in keys if k not in getattr(utils, "TOKENS", {})]
    check(not miss, "TOKENS 含间距/控件高/窗口尺寸键", "缺 {}".format(miss))
    check(utils.ctrl_h() == utils.scaled(utils.TOKENS["ctrl_h"]),
          "ctrl_h() == scaled(TOKENS['ctrl_h'])")
    check(utils.sp("win_min_w") == utils.scaled(utils.TOKENS["win_min_w"]),
          "sp('win_min_w') == scaled(TOKENS['win_min_w'])")


def t_scaled_monotonic():
    print("\n── A2. scaled() 随 set_ui_scale 单调 ──")
    s0 = utils.current_ui_scale()
    try:
        utils.set_ui_scale(1.0)
        v100 = utils.scaled(100)
        min1 = (utils.sp("win_min_w"), utils.sp("win_min_h"))
        geo1 = utils.dialog_geometry(utils.sp("win_w"), utils.sp("win_h"),
                                     None, (0, 0, 4000, 4000))
        utils.set_ui_scale(1.2)
        v120 = utils.scaled(100)
        min2 = (utils.sp("win_min_w"), utils.sp("win_min_h"))
        geo2 = utils.dialog_geometry(utils.sp("win_w"), utils.sp("win_h"),
                                     None, (0, 0, 4000, 4000))
        check(v120 > v100, "scaled(100): 1.0→1.2 严格变大", "{} → {}".format(v100, v120))
        check(min2[0] > min1[0] and min2[1] > min1[1],
              "minsize 随 ui_scale 变化 (派生自 win_min_* token)",
              "{} → {}".format(min1, min2))
        g1, g2 = _parse_geo(geo1), _parse_geo(geo2)
        check(g1 and g2 and g2[0] > g1[0] and g2[1] > g1[1],
              "默认几何随 ui_scale 变化 (派生自 win_w/win_h token)",
              "{} → {}".format(geo1, geo2))
        check(min2 == (utils.scaled(utils.TOKENS["win_min_w"]),
                       utils.scaled(utils.TOKENS["win_min_h"])),
              "minsize 严格等于 token 派生值 (scaled(640), scaled(520))",
              "{}".format(min2))
    finally:
        utils.set_ui_scale(s0)


def t_dialog_geometry_pure():
    print("\n── A3. dialog_geometry 纯函数 + 越界钳制 (headless) ──")
    # 父窗居中: x = px + (pw-w)//2, y = py + (ph-h)//3
    g = utils.dialog_geometry(400, 300, (100, 100, 800, 600), (0, 0, 1920, 1080))
    c = _parse_geo(g)
    check(c == (400, 300, 300, 200), "父窗居中断言 x/y", "{} → {}".format(g, c))
    # 父窗跑到屏外 → 结果被钳制回虚拟屏内
    g2 = utils.dialog_geometry(400, 300, (5000, 5000, 100, 100), (0, 0, 1920, 1080))
    c2 = _parse_geo(g2)
    ok = c2 is not None and 0 <= c2[2] <= 1920 - c2[0] and 0 <= c2[3] <= 1080 - c2[1]
    check(ok, "越界父窗 → 结果钳制在虚拟屏内", "{}".format(g2))
    # 无父窗: 居中于 vroot
    g3 = utils.dialog_geometry(600, 400, None, (0, 0, 1200, 900))
    check(_parse_geo(g3) == (600, 400, 300, 166), "无父窗时居中于 vroot", g3)


def t_main_geometry_tokens():
    print("\n── A4. 主窗口几何派生自 token (源码契约) ──")
    src = _read("src/ACRPA.py")
    fn = src.split("def _apply_main_geometry", 1)[-1].split("\ndef ", 1)[0]
    for tok in ('sp("win_w")', 'sp("win_h")', 'sp("win_min_w")', 'sp("win_min_h")'):
        check(tok in fn, "_apply_main_geometry 使用 {}".format(tok))
    check("center_geometry" in fn, "_apply_main_geometry 经 center_geometry 落位")
    check('"500x625+400+80"' not in fn and "+400+80" not in fn,
          "紧凑模式固定 +400+80 屏幕偏移已消除")


# ── B. 硬编码几何清理 ────────────────────────────────────────────────

# 目标文件集合: 承载对话框/子窗口的 UI 文件 (state.py 为配置模块, 不在范围)。
TARGET_FILES = (
    "src/ACRPA.py",
    "src/dialogs.py",
    "src/help_window.py",
    "src/market_window.py",
    "src/netlink_window.py",
    "src/settings_window.py",
    "src/mini_bar.py",
    "src/ui/settings/window.py",
    "src/ui/settings/cards/record.py",
    "src/ui/settings/cards/market.py",
    "src/ui/settings/cards/system.py",
)

# 白名单: 允许保留的硬编码 "WxH+x+y" 条目 (source, 字面量) → 保留原因。
# 本轮已把全部固定屏幕偏移改为 utils.place_dialog / utils.center_geometry, 故为空。
HARD_GEO_WHITELIST = {
    # ("src/xxx.py", "123x456+100+100"): "原因",
}

_HARD_GEO_RE = re.compile(r'"\d+x\d+[+-]\d+[+-]\d+"')


def t_hardcoded_geometry():
    print("\n── B. 硬编码 \"WxH+x+y\" 屏幕偏移清理 ──")
    total = 0
    for rel in TARGET_FILES:
        try:
            text = _read(rel)
        except Exception as e:
            warn("读取失败 {}: {!r}".format(rel, e))
            continue
        n = len(_HARD_GEO_RE.findall(text))
        total += n
        if n:
            print("      {}: {} 处".format(rel, n))
    allowed = len(HARD_GEO_WHITELIST)
    check(total <= allowed,
          "目标文件集合硬编码几何计数 ≤ 白名单 {}".format(allowed),
          "实测 {} 处".format(total))
    if HARD_GEO_WHITELIST:
        for (src_f, lit), why in HARD_GEO_WHITELIST.items():
            print("      白名单: {} {} —— {}".format(src_f, lit, why))
    check("place_dialog" in _read("src/dialogs.py"),
          "dialogs.py 已改用 place_dialog")
    check("place_dialog" in _read("src/ACRPA.py"),
          "ACRPA.py 已改用 place_dialog")


# ── C. 可达性静态 ────────────────────────────────────────────────────

def t_a11y_static():
    print("\n── C. 标题栏图标可达性 (静态) ──")
    src = _read("src/ACRPA.py")
    for var in ("pin_btn", "fold_btn", "dark_btn", "settings_btn", "help_btn", "devlink_btn"):
        check(re.search(r"bind_icon_activate\(\s*{}\s*,".format(var), src) is not None,
              "{} 经 utils.bind_icon_activate 接入".format(var))
    usrc = _read("src/utils.py")
    check("def set_accessible_name(" in usrc, "utils.set_accessible_name 已定义")
    check("def bind_icon_activate(" in usrc, "utils.bind_icon_activate 已定义")
    for frag, label in (("takefocus=True", "takefocus=True"),
                        ('"<Return>"', "<Return> 绑定"),
                        ('"<space>"', "<space> 绑定"),
                        ("highlightcolor", "焦点环 highlightcolor"),
                        ("acrpa_accessible_name", "可访问名属性")):
        check(frag in usrc, "utils 含 {}".format(label))

    print("\n── C2. 正文字号下限 ≥ 9pt ──")
    specs = usrc.split("_FONT_SPECS = {", 1)[-1].split("}", 1)[0]
    sizes = {}
    for role, size in re.findall(r'"(ACRPA_[A-Z_]+)":\s*\("[^"]+",\s*(\d+),', specs):
        sizes[role] = int(size)
    for role in ("ACRPA_TINY", "ACRPA_SMALL", "ACRPA_SMALL_BOLD", "ACRPA_BODY", "ACRPA_BUTTON"):
        check(sizes.get(role, 0) >= 9, "{} ≥ 9pt".format(role),
              "= {}pt".format(sizes.get(role)))


# ── D. 可达性实机 (WARN 守卫) ────────────────────────────────────────

_CHILD = r'''
import os, sys, json
BASE = r"{base}"
sys.path.insert(0, os.path.join(BASE, "src"))
sys.path.insert(0, BASE)
import state
state.save_config = lambda *a, **k: None
state.load_config = lambda *a, **k: None
try:
    import ACRPA, app
    app.build()
except Exception as e:
    print("BUILDFAIL::" + repr(e))
    sys.stdout.flush()
    os._exit(0)
root = ACRPA.root
found = []
def walk(w):
    try:
        kids = w.winfo_children()
    except Exception:
        kids = []
    for k in kids:
        if getattr(k, "acrpa_accessible_name", None):
            found.append(k)
        walk(k)
try:
    root.update_idletasks()
except Exception:
    pass
walk(root)
rows = []
for w in found:
    try: tf = str(w.cget("takefocus"))
    except Exception: tf = ""
    try: ret = bool(w.bind("<Return>"))
    except Exception: ret = False
    try: spc = bool(w.bind("<space>"))
    except Exception: spc = False
    rows.append((getattr(w, "acrpa_accessible_name", None), tf, ret, spc))
print("A11Y::" + json.dumps(rows, ensure_ascii=False))
sys.stdout.flush()
try:
    root.destroy()
except Exception:
    pass
os._exit(0)
'''


def t_a11y_live():
    print("\n── D. 标题栏图标可达性 (实机, WARN 守卫) ──")
    snippet = _CHILD.format(base=ROOT.replace("\\", "/"))
    try:
        p = subprocess.run([sys.executable, "-X", "utf8", "-c", snippet],
                           cwd=ROOT, capture_output=True, timeout=180)
        out = (p.stdout or b"").decode("utf-8", "replace")
        err = (p.stderr or b"").decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        warn("实机进程超时, 跳过 D 段")
        return
    except Exception as e:
        warn("实机进程启动失败 ({!r}), 跳过 D 段".format(e))
        return

    if "BUILDFAIL::" in out:
        warn("无可用显示/构建失败, 跳过 D 段: "
             + out.split("BUILDFAIL::", 1)[1].strip()[:160])
        return
    m = re.search(r"A11Y::(.+)", out)
    if not m:
        warn("实机未产出结果 (无显示?): {}".format((err or out).strip()[-160:]))
        return

    try:
        rows = json.loads(m.group(1).strip())
    except Exception as e:
        warn("结果解析失败: {!r}".format(e))
        return

    check(len(rows) >= 6, "标题栏图标控件数 ≥ 6 (含可访问名)",
          "实测 {}".format(len(rows)))
    bad_focus = [r for r in rows if str(r[1]) not in ("1", "true", "True")]
    check(not bad_focus, "全部图标 cget('takefocus') 为真", "{}".format(bad_focus))
    bad_ret = [r for r in rows if not r[2]]
    check(not bad_ret, "全部图标 bind('<Return>') 非空", "{}".format(bad_ret))
    bad_spc = [r for r in rows if not r[3]]
    check(not bad_spc, "全部图标 bind('<space>') 非空", "{}".format(bad_spc))


def main():
    print("=" * 68)
    print("阶段三-5 尺寸 token + 对话框落位 + 标题栏可达性 回归自测")
    print("=" * 68)
    t_tokens_api()
    t_scaled_monotonic()
    t_dialog_geometry_pure()
    t_main_geometry_tokens()
    t_hardcoded_geometry()
    t_a11y_static()
    t_a11y_live()

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
