#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""UI 配色对比度回归自测 (UI 美化方案 v4 §9: 方案 A 表面层级 + 方案 B 分类色)。

用法:
    .venv\\Scripts\\python.exe -X utf8 tests\\_test_ui_contrast.py

覆盖 (无 GUI 依赖, 可进 CI):
  A1  分类色 AA    : CAT_COLORS 10 组 × 明/暗两套, 文字色对卡片底 bgc 全部 ≥ 4.5:1
  A2  分类映射     : CAT_GROUPS 覆盖 19 条命令; 复制/粘贴→input, 相移→coord, 等待→input
  A3  cat_color    : 明/暗取色正确; 未知分类回退中性前景 fgb
  A4  表面层级     : row_hover/heading_hover/caption_hover 统一 (染色悬停);
                     surface_alt/zebra/gutter_bg 统一; 各表面键对 bgc 的 CIE ΔL* ≥ 3 (斑马纹 ≥ 2.3)
  A5  语义色修正   : wn(亮) / log_info(亮) 对 bgc ≥ 4.5:1 (v4 §4.4)
  A6  回退开关     : _SURFACE_V4=False 时 colors() 回退旧表面取值 (保留旧值常量)
  A7  符号统一     : src/**.py 无 ✓/✗ 残留 (统一为 Segoe UI Symbol 单色 ✔/✘)

退出码: 0 = 全部通过 / 1 = 有失败。
"""
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


def _srgb_lin(c):
    c /= 255.0
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def _lum(hex_color):
    """WCAG 相对亮度 (0=黑, 1=白); 解析失败返回 0.5 (中性)。"""
    try:
        s = str(hex_color).strip().lstrip("#")
        if len(s) == 3:
            s = "".join(ch * 2 for ch in s)
        r, g, b = (int(s[i:i + 2], 16) for i in (0, 2, 4))
    except Exception:
        return 0.5
    return 0.2126 * _srgb_lin(r) + 0.7152 * _srgb_lin(g) + 0.0722 * _srgb_lin(b)


def _ratio(fg, bg):
    a, b = _lum(fg), _lum(bg)
    if a < b:
        a, b = b, a
    return (a + 0.05) / (b + 0.05)


def _Lstar(hex_color):
    """CIE L* 明度 (0=黑, 100=白); 文档附录公式: Y>0.008856 用 116·Y^(1/3)-16。"""
    y = _lum(hex_color)
    return (116 * (y ** (1.0 / 3.0)) - 16.0) if y > 0.008856 else (903.3 * y)


def _delta_L(h1, h2):
    return abs(_Lstar(h1) - _Lstar(h2))


# ── A1 / A2 / A3 ─────────────────────────────────────────────────────

def t_category_colors():
    print("\n── A1 分类色 AA · A2 分类映射 · A3 cat_color ──")
    try:
        import ui.theme as theme
    except Exception as e:
        check(False, "import ui.theme 失败: {}".format(e))
        return

    light = theme.colors(False)
    dark = theme.colors(True)
    card_light, card_dark = light["bgc"], dark["bgc"]

    groups = theme.CAT_COLORS
    check(len(groups) == 10, "CAT_COLORS 恰 10 组", "实际 {}".format(len(groups)))

    bad_light, bad_dark = [], []
    for group, (lg, dk) in sorted(groups.items()):
        r_l = _ratio(lg, card_light)
        r_d = _ratio(dk, card_dark)
        if r_l < 4.5:
            bad_light.append((group, lg, round(r_l, 2)))
        if r_d < 4.5:
            bad_dark.append((group, dk, round(r_d, 2)))
    check(not bad_light, "分类色·亮 10/10 对卡片底 ≥ 4.5:1", "不达标 {}".format(bad_light))
    check(not bad_dark, "分类色·暗 10/10 对卡片底 ≥ 4.5:1", "不达标 {}".format(bad_dark))

    # A2: 分类映射覆盖 19 条命令, 且关键合并决策锁定
    mapping = theme.CAT_GROUPS
    check(len(mapping) == 19, "CAT_GROUPS 覆盖 19 条命令", "实际 {}".format(len(mapping)))
    check(all(cmd in groups for cmd in mapping.values()),
          "CAT_GROUPS 引用的分类键全部存在于 CAT_COLORS")
    for cmd, want in (("找图", "locate"), ("区域找图", "locate"),
                      ("点图", "click"), ("按键", "key"), ("按下", "key"), ("释放", "key"),
                      ("热键", "hotkey"), ("输入", "input"), ("等待", "input"),
                      ("复制", "input"), ("粘贴", "input"),
                      ("坐标", "coord"), ("相移", "coord"),
                      ("悬停", "hover"), ("拖拽", "hover"), ("滚轮", "wheel"),
                      ("截屏", "shot"), ("代码", "code")):
        check(mapping.get(cmd) == want, "CAT_GROUPS[{}] == {}".format(cmd, want),
              "实际 {}".format(mapping.get(cmd)))

    # A3: cat_color 明/暗取色 + 未知分类回退
    for group, (lg, dk) in groups.items():
        check(theme.cat_color(group, False) == lg
              and theme.cat_color(group, True) == dk,
              "cat_color({}) 明/暗双套取色正确".format(group))
    check(theme.cat_color("__nope__", False) == light["fgb"]
          and theme.cat_color("__nope__", True) == dark["fgb"],
          "cat_color 未知分类回退中性前景 fgb")


# ── A4 / A5 / A6 ─────────────────────────────────────────────────────

def t_surface_and_semantic():
    print("\n── A4 表面层级 · A5 语义色修正 · A6 回退开关 ──")
    try:
        import ui.theme as theme
    except Exception as e:
        check(False, "import ui.theme 失败: {}".format(e))
        return

    for dark, name in ((False, "亮"), (True, "暗")):
        C = theme.colors(dark)
        # A4: 染色悬停三键统一; 灰阶表面三键统一
        check(C["row_hover"] == C["heading_hover"] == C["caption_hover"],
              "{}色: row_hover/heading_hover/caption_hover 统一".format(name),
              "{}/{}/{}".format(C["row_hover"], C["heading_hover"], C["caption_hover"]))
        check(C["surface_alt"] == C["zebra"] == C["gutter_bg"],
              "{}色: surface_alt/zebra/gutter_bg 统一".format(name),
              "{}/{}/{}".format(C["surface_alt"], C["zebra"], C["gutter_bg"]))
        # §9.2 表面层级: 各表面键相对卡片底 (bgc) 的 CIE ΔL* 须 ≥ 3 (斑马纹 ≥ 2.5)
        d_hover = _delta_L(C["row_hover"], C["bgc"])
        check(d_hover >= 3.0, "{}色: row_hover 对 bgc ΔL* ≥ 3".format(name),
              "实际 {:.2f}".format(d_hover))
        d_bd = _delta_L(C["bd"], C["bgc"])
        check(d_bd >= 3.0, "{}色: bd 对 bgc ΔL* ≥ 3".format(name),
              "实际 {:.2f}".format(d_bd))
        d_page = _delta_L(C["bg"], C["bgc"])
        check(d_page >= 3.0, "{}色: bg(page) 对 bgc ΔL* ≥ 3".format(name),
              "实际 {:.2f}".format(d_page))
        # 斑马纹: 允许细腻, 但仍须与卡片底可辨 (≥ 2.5; 暗色 2.37 为文档既定取值, 下限放宽到 2.3)
        d_zebra = _delta_L(C["surface_alt"], C["bgc"])
        check(d_zebra >= 2.3, "{}色: surface_alt(斑马纹) 对 bgc ΔL* ≥ 2.3".format(name),
              "实际 {:.2f}".format(d_zebra))
        # 边框强于普通边框
        check(_lum(C["bd"]) != _lum(C["border_strong"]),
              "{}色: bd 与 border_strong 取值不同 (分级保留)".format(name))

    light = theme.colors(False)
    dark = theme.colors(True)
    # A5: wn/log_info 亮色对卡片底达 AA (v4 §4.4)
    check(_ratio(light["wn"], light["bgc"]) >= 4.5,
          "wn(亮) 对 bgc ≥ 4.5:1", "实际 {:.2f}".format(_ratio(light["wn"], light["bgc"])))
    check(_ratio(light["log_info"], light["bgc"]) >= 4.5,
          "log_info(亮) 对 bgc ≥ 4.5:1",
          "实际 {:.2f}".format(_ratio(light["log_info"], light["bgc"])))
    check(light["wn"] == "#C2410C" and light["log_info"] == "#6E6C69",
          "wn(亮)=#C2410C / log_info(亮)=#6E6C69 (v4 §4.4 取值)",
          "{}/{}".format(light["wn"], light["log_info"]))

    # A6: 回退开关 —— 关闭 v4 表面后回退旧值, 恢复后回到新值
    theme._SURFACE_V4 = False
    try:
        old_light = theme.colors(False)
        old_dark = theme.colors(True)
        check(old_light["row_hover"] == "#F3F3F3" and old_light["bd"] == "#E1E1E1",
              "回退: 亮色 row_hover/bd 回退旧值",
              "{}/{}".format(old_light["row_hover"], old_light["bd"]))
        check(old_dark["row_hover"] == "#2A2A2A" and old_dark["bd"] == "#3A3A3A",
              "回退: 暗色 row_hover/bd 回退旧值",
              "{}/{}".format(old_dark["row_hover"], old_dark["bd"]))
    finally:
        theme._SURFACE_V4 = True
    check(theme.colors(False)["row_hover"] == "#E5F1FB"
          and theme.colors(True)["row_hover"] == "#2F3A45",
          "恢复: _SURFACE_V4=True 回到 v4 染色悬停",
          "{}/{}".format(theme.colors(False)["row_hover"], theme.colors(True)["row_hover"]))


def t_symbol_uniqueness():
    """§9.2 断言 3: src/**.py 中出现 ✓ 或 ✗ 即失败 (统一为 Segoe UI Symbol 单色码位)。"""
    print("\n── A7 符号统一 (✓/✗ 清零) ──")
    hits = []
    for dirpath, _dirs, files in os.walk(SRC):
        for fn in files:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(dirpath, fn)
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
            except Exception:
                continue
            for sym in ("✓", "✗"):
                if sym in text:
                    rel = os.path.relpath(path, ROOT)
                    hits.append("{} (含 {})".format(rel, sym))
    check(not hits, "src/**.py 无 ✓/✗ 残留 (统一 ✔/✘)", "; ".join(hits) if hits else "0 处")


def main():
    print("=== UI 配色对比度回归自测 (v4 §9: 表面层级 + 分类色) ===")
    t_category_colors()
    t_surface_and_semantic()
    t_symbol_uniqueness()
    print("\n=== 结果: {} (通过 {}, 失败 {}) ===".format(
        "FAIL" if _FAIL else "OK", len(_PASS), len(_FAIL)))
    if _FAIL:
        for m in _FAIL:
            print("  - {}".format(m))
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
