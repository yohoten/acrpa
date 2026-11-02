# -*- coding: utf-8 -*-
"""tests/_test_browser_captcha.py — 滑块验证码（P2）纯逻辑验收。

覆盖（无 playwright / 无真实浏览器亦可运行）：
  * ease_out_cubic：端点/单调性/越界夹取
  * build_drag_path：末值恒等于 distance、整数序列、末段减速、0/1 步特例、负距离
  * column_profile：等长/参差行、空输入、非数值元素
  * detect_gap_offset：合成缺口定位、min_travel 跳过首段、min_delta 抑制噪声、空输入
  * compute_drag_distance：包围盒相减、偏移修正、滑块包围盒缺失、非法入参
  * _clamp_int / _clamp_float：默认值与上下界夹取
  * 命令注册：「浏览器验证码」已注册、handler 绑定到 browser_backend、requires 声明
  * 懒加载：import browser_backend 不触发 import playwright
  * PIL 适配（可选）：有 Pillow 时用合成图片验证 _image_column_profile + detect_gap_offset

只跑纯逻辑，不弹窗、不联网、不启动浏览器。退出码 0(全过/仅 WARN) / 1(存在 FAIL)。
输出行前缀: [OK] / [WARN] / [FAIL]。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_FAILS = []
_WARNS = []


def ok(msg):
    print("[OK] " + msg)


def warn(msg):
    _WARNS.append(msg)
    print("[WARN] " + msg)


def fail(msg):
    _FAILS.append(msg)
    print("[FAIL] " + msg)


def check(cond, msg):
    if cond:
        ok(msg)
    else:
        fail(msg)


def eq(actual, expected, msg):
    if actual == expected:
        ok("{} -> {!r}".format(msg, actual))
    else:
        fail("{}: 期望 {!r}，实际 {!r}".format(msg, expected, actual))


import commands                  # noqa: E402
import browser_backend as bb     # noqa: E402


def _flat_profile(width, value=200):
    return [float(value)] * width


def _notch_profile(width=320, left=120, right=140, bg=200, gap=60):
    """合成背景：left..right 列亮度更低（模拟缺口）。"""
    prof = _flat_profile(width, bg)
    for x in range(left, min(right + 1, width)):
        prof[x] = float(gap)
    return prof


print("── 懒加载硬约束 ──")
check("playwright.sync_api" not in sys.modules,
      "import browser_backend 未触发 import playwright")

print("── ease_out_cubic 缓出曲线 ──")
eq(bb.ease_out_cubic(0), 0.0, "ease(0)")
eq(bb.ease_out_cubic(1), 1.0, "ease(1)")
eq(bb.ease_out_cubic(0.5), 0.875, "ease(0.5) 前段快")
eq(bb.ease_out_cubic(-1.0), 0.0, "ease(-1) 夹取到 0")
eq(bb.ease_out_cubic(2.0), 1.0, "ease(2) 夹取到 1")
_prev = -1.0
_mono = True
for i in range(0, 41):
    v = bb.ease_out_cubic(i / 40.0)
    if v < _prev - 1e-12:
        _mono = False
    _prev = v
check(_mono, "ease 在 [0,1] 上单调不减")

print("── build_drag_path 缓动位移序列 ──")
path = bb.build_drag_path(180, 30)
eq(path[-1], 180, "distance=180 末值等于 distance")
check(len(path) <= 30, "步数不超过 steps ({} <= 30)".format(len(path)))
check(all(isinstance(p, int) for p in path), "序列全为整数（鼠标坐标）")
check(all(b >= a for a, b in zip(path, path[1:])), "序列单调不减")
check(path[0] > 0, "首步已产生位移（非空拖拽）")
inc_first = path[1] - path[0]
inc_last = path[-1] - path[-2]
check(inc_first > inc_last,
      "前段步长({}) > 末段步长({})，即末段减速".format(inc_first, inc_last))
eq(bb.build_drag_path(0, 30), [0], "distance=0 退化为 [0]")
eq(bb.build_drag_path(180, 1), [180], "steps=1 退化为单步")
eq(bb.build_drag_path("180", "30")[-1], 180, "字符串入参可解析")
eq(bb.build_drag_path("abc", 30), [], "非数字 distance → []")
_neg = bb.build_drag_path(-100, 20)
eq(_neg[-1], -100, "负距离末值等于 distance")
check(all(b <= a for a, b in zip(_neg, _neg[1:])), "负距离序列单调不增")
check(len(bb.build_drag_path(500, 9999)) <= 400, "steps 上限夹取到 400")

print("── column_profile 逐列平均亮度 ──")
eq(bb.column_profile([[0, 10, 20], [0, 10, 20]]), [0.0, 10.0, 20.0], "两行等长")
eq(bb.column_profile([[0, 10], [0, 10, 20]]), [0.0, 10.0, 20.0], "参差行按最长对齐")
eq(bb.column_profile([[1, None, 3]]), [1.0, 0.0, 3.0], "非数值元素按缺失跳过")
eq(bb.column_profile([]), [], "空输入 → []")
eq(bb.column_profile([[]]), [], "全空行 → []")

print("── detect_gap_offset 灰度列差分 ──")
eq(bb.detect_gap_offset(_notch_profile()), 120, "合成缺口左缘 = 120")
eq(bb.detect_gap_offset(_notch_profile(), smooth=1), 120, "smooth=1 同样命中 120")
eq(bb.detect_gap_offset(_flat_profile(320)), None, "平坦图无跳变 → None")
eq(bb.detect_gap_offset([]), None, "空剖面 → None")
eq(bb.detect_gap_offset(_notch_profile(left=10, right=30), min_travel=40), None,
   "缺口整体落在 min_travel 之前 → None")
eq(bb.detect_gap_offset(_notch_profile(left=10, right=30), min_travel=20), 31,
   "只跳过左缘、右缘仍可命中（按跳变后第一列返回）")
eq(bb.detect_gap_offset(_notch_profile(bg=100, gap=99)), None,
   "跳变幅度 1 < min_delta 3 → None")
eq(bb.detect_gap_offset(_notch_profile(bg=200, gap=150), min_delta=3.0), 120,
   "幅度 50 ≥ min_delta → 命中")

print("── compute_drag_distance 距离测算 ──")
_bg = {"x": 100.0, "y": 0.0, "width": 300.0, "height": 150.0}
_hd = {"x": 110.0, "y": 100.0, "width": 40.0, "height": 40.0}
eq(bb.compute_drag_distance(_bg, _hd, 200), 190, "offset 200 - 滑块相对偏移 10 = 190")
eq(bb.compute_drag_distance(_bg, _hd, 200, 3), 193, "偏移修正 +3")
eq(bb.compute_drag_distance(_bg, _hd, 200, -5), 185, "偏移修正 -5")
eq(bb.compute_drag_distance(_bg, None, 200), 200, "滑块包围盒缺失 → 按左边界处理")
eq(bb.compute_drag_distance(_bg, _hd, "abc"), None, "非法 gap_offset → None")
eq(bb.compute_drag_distance("bad", _hd, 200), None, "非法 bg_box → None")

print("── _clamp_int / _clamp_float 取参夹取 ──")
eq(bb._clamp_int("", 800, 50, 20000), 800, "空 → 默认值")
eq(bb._clamp_int("1200", 800, 50, 20000), 1200, "正常值原样")
eq(bb._clamp_int("1", 800, 50, 20000), 50, "低于下界 → 夹到 50")
eq(bb._clamp_int("99999", 800, 50, 20000), 20000, "高于上界 → 夹到 20000")
eq(bb._clamp_int("abc", 800, 50, 20000), 800, "非数字 → 默认值")
eq(bb._clamp_float("", 5.0, 0.5, 120.0), 5.0, "浮点空 → 默认值")
eq(bb._clamp_float("0.1", 5.0, 0.5, 120.0), 0.5, "浮点低于下界 → 夹取")
eq(bb._clamp_float("8.5", 5.0, 0.5, 120.0), 8.5, "浮点正常值原样")

print("── PIL 适配（可选） ──")
try:
    from PIL import Image as _Image
    _img = _Image.new("L", (320, 60), 200)
    for _y in range(60):
        for _x in range(120, 141):
            _img.putpixel((_x, _y), 60)
    _prof = bb._image_column_profile(_img)
    eq(len(_prof), 320, "PIL 图片 → 320 列亮度")
    eq(bb.detect_gap_offset(_prof), 120, "PIL 图片缺口左缘 = 120")
except ImportError:
    warn("未安装 Pillow，跳过 PIL 适配用例（截图路径需 Pillow）")
eq(bb._image_column_profile(None), [], "_image_column_profile(None) → []")

print("── 命令注册与绑定 ──")
check("浏览器验证码" in commands.list_names(), "「浏览器验证码」已注册")
_desc = dict((n, d) for n, d, _p, _h in commands.list_all()).get("浏览器验证码", "")
check("滑块" in _desc and "缓动" in _desc, "描述含 滑块/缓动 关键字")
_params = dict((n, p) for n, _d, p, _h in commands.list_all()).get("浏览器验证码", "")
check("拖动时长" in _params and "偏移修正" in _params, "参数说明含 拖动时长/偏移修正")
check("浏览器验证码" in commands.NO_INTERP, "NO_INTERP 已登记（避免漏登记静默失效）")
eq(commands.requires("浏览器验证码"), ("browser.playwright",),
   "requires 声明为 browser.playwright")
_schema = commands.schema("浏览器验证码")
check(isinstance(_schema, list) and len(_schema) == 9,
      "schema 解析出 9 个参数位（位置式 9 槽上限内）")

check(callable(getattr(bb, "_browser_captcha", None)),
      "handler 实现存在: browser_backend._browser_captcha")
# engine 侧绑定做静态校验（import engine 会拉起 GUI 依赖，不适合进纯逻辑用例）
try:
    with open(os.path.join(SRC, "engine.py"), "rb") as fh:
        _engine_src = fh.read().decode("utf-8", "replace")
    check('_set_handler("浏览器验证码", _browser_captcha)' in _engine_src,
          "engine 侧已 _set_handler(\"浏览器验证码\", _browser_captcha)")
    check("_browser_captcha," in _engine_src, "engine 侧已从 browser_backend 导入该 handler")
except Exception as e:
    warn("engine 绑定静态校验跳过: {}".format(e))

print()
print("=" * 60)
print("PASS 全部通过" if not _FAILS else "FAIL {} 项失败".format(len(_FAILS)))
if _WARNS:
    print("WARN {} 项提示".format(len(_WARNS)))
sys.exit(0 if not _FAILS else 1)
