# -*- coding: utf-8 -*-
"""等比缩放语义统一 P3-2 回归自测: core.scale 收拢 + 三处消费方接入。

用法:
    .venv\\Scripts\\python.exe -X utf8 tests\\_test_scale.py

覆盖:
  A 设计尺寸↔缩放系数 : ratio_of / scaled (maliang ratios = size/init_size 语义)。
  B 缩放钳制/步进     : clamp_scale / step_scale (上/下互为倒数, lo>hi 自交换)。
  C 适应窗口系数      : fit_scale 与 flow_canvas.fit_to_view 旧内联公式逐位等价。
  D 编辑器字号换算    : editor_zoom_ratio / editor_zoom_size 与
                        clamp(base+offset, 6, 40) 逐位等价 (渲染结果不回退)。
  E 统一接入          : flow_canvas / workflow_view / ACRPA 均 import 并调用 core.scale;
                        core.scale 保持零 tkinter (P0 纯度护栏)。

退出码: 0=全部通过 / 1=有失败。
"""
import ast
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


import core.scale as scale  # noqa: E402


def _read(rel):
    with open(os.path.join(SRC, rel), encoding="utf-8") as f:
        return f.read()


def t_ratio_scaled():
    print("\n── A. ratio_of / scaled (设计尺寸 ↔ 缩放系数) ──")
    check(abs(scale.ratio_of(120.0, 100.0) - 1.2) < 1e-12, "ratio_of(120,100)=1.2")
    check(abs(scale.ratio_of(80.0, 100.0) - 0.8) < 1e-12, "ratio_of(80,100)=0.8")
    check(scale.ratio_of(50.0, 0.0) == 1.0, "设计尺寸 0 → 回退 1.0 (除零护栏)")
    check(abs(scale.scaled(100.0, 1.2) - 120.0) < 1e-9, "scaled(100, 1.2)=120")
    check(abs(scale.scaled(16.0, 0.5) - 8.0) < 1e-9, "scaled(16, 0.5)=8")
    # 往返: design → scaled → ratio_of 还原系数
    r = 1.25
    check(abs(scale.ratio_of(scale.scaled(88.0, r), 88.0) - r) < 1e-12,
          "scaled/ratio_of 往返还原缩放系数")


def t_clamp_step():
    print("\n── B. clamp_scale / step_scale ──")
    check(scale.clamp_scale(3.0, 0.4, 2.5) == 2.5, "上钳制 (3.0 → 2.5)")
    check(scale.clamp_scale(0.1, 0.4, 2.5) == 0.4, "下钳制 (0.1 → 0.4)")
    check(scale.clamp_scale(1.0, 2.5, 0.4) == 1.0, "lo>hi 自交换后不误钳")
    check(abs(scale.step_scale(1.0, 1.12, True) - 1.12) < 1e-12, "step_scale up=×1.12")
    check(abs(scale.step_scale(1.0, 1.12, False) - (1.0 / 1.12)) < 1e-12,
          "step_scale down=÷1.12")
    # 上/下互逆
    s0 = 1.3
    check(abs(scale.step_scale(scale.step_scale(s0, 1.12, True), 1.12, False) - s0) < 1e-12,
          "step up 后再 down 还原")


def _old_fit(vw, vh, sw, sh, margin):
    """flow_canvas.fit_to_view 旧内联公式 (等比系数, 未乘 z0 / 未钳制)。"""
    return min(max(vw - 2.0 * margin, 1.0) / max(sw, 1.0),
               max(vh - 2.0 * margin, 1.0) / max(sh, 1.0))


def t_fit_scale():
    print("\n── C. fit_scale 与 fit_to_view 旧内联公式逐位等价 ──")
    cases = [
        (800.0, 600.0, 1200.0, 900.0, 24.0),
        (500.0, 400.0, 300.0, 200.0, 0.0),
        (640.0, 480.0, 4000.0, 2000.0, 24.0),
        (100.0, 100.0, 1.0, 1.0, 24.0),
        (320.0, 240.0, 999.0, 88.0, 8.0),
    ]
    ok = True
    for vw, vh, sw, sh, m in cases:
        got = scale.fit_scale(vw, vh, sw, sh, m)
        exp = _old_fit(vw, vh, sw, sh, m)
        if abs(got - exp) > 1e-12:
            ok = False
            check(False, "fit_scale({},{},{},{},{}) 不等价".format(vw, vh, sw, sh, m),
                  "got={} exp={}".format(got, exp))
    check(ok, "fit_scale 与旧内联公式 {} 组逐位一致".format(len(cases)))


def t_editor_zoom():
    print("\n── D. editor_zoom_ratio / editor_zoom_size 与 clamp(base+offset) 等价 ──")
    ok = True
    for base in range(6, 31):
        for off in range(-20, 31):
            exp = max(6, min(40, base + off))
            got = scale.editor_zoom_size(base, off)
            if got != exp or not isinstance(got, int):
                ok = False
                check(False, "editor_zoom_size({},{}) 不等价".format(base, off),
                      "got={} exp={}".format(got, exp))
                break
        if not ok:
            break
    check(ok, "editor_zoom_size 与 clamp(base+offset, 6, 40) 全量等价 (base 6..30, off -20..30)")
    check(abs(scale.editor_zoom_ratio(10, 3) - 1.3) < 1e-12, "editor_zoom_ratio(10,+3)=1.3")
    check(abs(scale.editor_zoom_ratio(10, -2) - 0.8) < 1e-12, "editor_zoom_ratio(10,-2)=0.8")
    check(scale.editor_zoom_ratio(0, 5) == 1.0, "base 0 → 回退 1.0 (除零护栏)")
    # ratio 与 scaled 组合还原 base+offset
    base, off = 10, 3
    check(abs(scale.scaled(base, scale.editor_zoom_ratio(base, off)) - (base + off)) < 1e-9,
          "scaled(base, editor_zoom_ratio) == base+offset")


def t_integration():
    print("\n── E. 三处消费方统一接入 + 纯度护栏 ──")
    fc = _read("ui/flow_canvas.py")
    wv = _read("ui/workflow_view.py")
    ac = _read("ACRPA.py")
    sc = _read("core/scale.py")

    check("from core.scale import" in fc and "clamp_scale(" in fc and "step_scale(" in fc
          and "fit_scale(" in fc,
          "flow_canvas: bind_zoom_pan/fit_to_view 已接 core.scale")
    check("from core.scale import" in wv and "clamp_scale(" in wv and "scaled(" in wv,
          "workflow_view: 工具条缩放按钮已接 core.scale")
    check("from core.scale import" in ac and "editor_zoom_size(" in ac,
          "ACRPA: _apply_editor_zoom 已接 core.scale.editor_zoom_size")

    # P0 纯度护栏: 用 AST 检查真实 import 语句 (docstring 里提到的 "tkinter" 不算)。
    def _imports_tkinter(node):
        if isinstance(node, ast.Import):
            return any(a.name.split(".")[0] == "tkinter" for a in node.names)
        if isinstance(node, ast.ImportFrom):
            return bool(node.module) and node.module.split(".")[0] == "tkinter"
        return False

    has_tk_import = any(_imports_tkinter(n) for n in ast.walk(ast.parse(sc)))
    check(not has_tk_import, "core.scale 零 tkinter (P0 纯度护栏, AST 实检)")


def main():
    print("=" * 68)
    print("等比缩放语义统一 P3-2 回归自测")
    print("=" * 68)
    t_ratio_scaled()
    t_clamp_step()
    t_fit_scale()
    t_editor_zoom()
    t_integration()
    print("\n" + "=" * 68)
    print("通过 {} / 失败 {}".format(len(_PASS), len(_FAIL)))
    print("结论: {}".format("FAIL" if _FAIL else "PASS"))
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
