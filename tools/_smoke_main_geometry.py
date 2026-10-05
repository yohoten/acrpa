#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""主窗口几何 + 最大化 + 紧凑 + 越界回退 冒烟测试 (退出码 0/1, 输出 [OK]/[WARN]/[FAIL])。

覆盖:
  A. utils 三个公共几何函数: win_virtual_bounds / geometry_in_screen / center_geometry
  B. 默认分支 (无记忆几何): 按 dpi_factor * ui_scale 派生并居中, 落在虚拟屏内
  C. 记忆分支: 屏内几何原样应用; 明显越界几何 (900x700+99999+99999) 回退居中且屏内
  D. 最大化分支: state.MAIN_MAXIMIZED=True → root.state()=="zoomed"
  E. 紧凑分支: state.COMPACT_MODE=True → 近似 500x625 (ui_scale=1.0 下为 500x625)

实现要点:
  src/ACRPA.py 在 import 时即执行 _apply_main_geometry() 并创建完整 GUI,
  故分支验证统一放到「子进程」: 预设 state 后 import ACRPA, 读取 root 几何/状态,
  打印 SMOKEJSON::<json> 供父进程断言, 随后 os._exit(0) 彻底退出 (无 GUI 残留)。
  子进程内把 state.load_config / state.save_config 置为 no-op, 保证:
    (1) ACRPA import 不会用真实 config.json 覆盖我们预设的 MAIN_GEOMETRY/MAXIMIZED/COMPACT;
    (2) 不向磁盘写回真实配置。

用法:
    .venv\\Scripts\\python.exe -X utf8 tools\\_smoke_main_geometry.py
"""
import os
import re
import sys
import json
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
sys.path.insert(0, ROOT)
sys.path.insert(0, SRC)

FAILS = []
WARNS = []


def _p(status, msg):
    print("[{}] {}".format(status, msg))


def _ok(msg):
    _p("OK", msg)


def _warn(msg):
    WARNS.append(msg)
    _p("WARN", msg)


def _fail(msg):
    FAILS.append(msg)
    _p("FAIL", msg)


def _parse_geo(geo):
    """解析 "WxH+X+Y" -> (w, h, x, y); 失败返回 None。"""
    m = re.match(r"^(\d+)x(\d+)([+-]\d+)([+-]\d+)$", geo or "")
    if not m:
        return None
    return tuple(int(m.group(i)) for i in range(1, 5))


def _in_bounds(rect, vroot):
    """(w,h,x,y) 是否完整落在虚拟屏 (vx,vy,vw,vh) 内。"""
    w, h, x, y = rect
    vx, vy, vw, vh = vroot
    return x >= vx and y >= vy and (x + w) <= (vx + vw) and (y + h) <= (vy + vh)


# ── 子进程脚本 (通过 -c 传入, 不走 shell) ──
_CHILD = r'''
import os, sys, json
BASE = os.environ["SMOKE_BASE"]
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "src"))
import tkinter
import state

# 阻止 ACRPA import 期间 load_config/save_config 覆盖预设或写出真实配置
state.load_config = lambda *a, **k: None
state.save_config = lambda *a, **k: None

import utils

mode = os.environ.get("SMOKE_MODE", "default")
used_geo = ""

try:
    _t = tkinter.Tk(); _t.withdraw()
    vx, vy, vw, vh = utils.win_virtual_bounds(_t)
    _t.destroy()
except Exception:
    vx, vy, vw, vh = 0, 0, 1024, 768

if mode == "default":
    state.MAIN_GEOMETRY = ""
    state.MAIN_MAXIMIZED = False
    state.COMPACT_MODE = False
elif mode == "memo_valid":
    w = min(700, max(300, vw - 60)); h = min(600, max(250, vh - 60))
    used_geo = "{}x{}+{}+{}".format(w, h, vx + 20, vy + 20)
    state.MAIN_GEOMETRY = used_geo
    state.MAIN_MAXIMIZED = False
    state.COMPACT_MODE = False
elif mode == "memo_bad":
    state.MAIN_GEOMETRY = "900x700+99999+99999"
    state.MAIN_MAXIMIZED = False
    state.COMPACT_MODE = False
elif mode == "max":
    state.MAIN_GEOMETRY = ""
    state.MAIN_MAXIMIZED = True
    state.COMPACT_MODE = False
elif mode == "compact":
    state.MAIN_GEOMETRY = ""
    state.MAIN_MAXIMIZED = False
    state.COMPACT_MODE = True

import ACRPA
import app
app.build()   # 入口拆分后: import ACRPA 不再建窗, 需显式构建
root = ACRPA.root
try:
    root.update_idletasks()
except Exception:
    pass

geo = root.geometry()
try:
    st = root.state()
except Exception:
    st = "?"

data = {
    "mode": mode,
    "geo": geo,
    "state": st,
    "used_geo": used_geo,
    "ui_scale": utils.current_ui_scale(),
    "dpi": utils.dpi_factor(),
    "scaled1000": utils.scaled(1000),
    "scaled680": utils.scaled(680),
    "scaled500": utils.scaled(500),
    "scaled625": utils.scaled(625),
    "vroot": [vx, vy, vw, vh],
    "in_screen": utils.geometry_in_screen(root, geo),
}
print("SMOKEJSON::" + json.dumps(data))
sys.stdout.flush()
try:
    root.destroy()
except Exception:
    pass
os._exit(0)
'''


def _run_child(mode, timeout=240):
    """在子进程中以指定分支导入 ACRPA, 返回 (data_dict | None, 子进程输出)。"""
    env = dict(os.environ)
    env["SMOKE_BASE"] = ROOT
    env["SMOKE_MODE"] = mode
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        proc = subprocess.run(
            [PY, "-X", "utf8", "-c", _CHILD],
            cwd=ROOT, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=timeout)
    except Exception as e:
        return None, "<<子进程启动/运行异常: {!r}>>".format(e)
    out = proc.stdout.decode("utf-8", "replace") if proc.stdout else ""
    for line in out.splitlines():
        if line.startswith("SMOKEJSON::"):
            try:
                return json.loads(line[len("SMOKEJSON::"):]), out
            except Exception as e:
                return None, "<<SMOKEJSON 解析失败: {!r}>>\n{}".format(e, out)
    return None, out


# ── A. utils 公共函数单测 ──

def test_utils_unit():
    import tkinter
    import utils
    try:
        r = tkinter.Tk()
    except Exception as e:
        _fail("无法创建 Tk root 做 utils 单测: {!r}".format(e))
        return
    r.withdraw()
    try:
        vx, vy, vw, vh = utils.win_virtual_bounds(r)
        if vw > 0 and vh > 0:
            _ok("win_virtual_bounds -> ({}, {}, {}, {}) 合理".format(vx, vy, vw, vh))
        else:
            _fail("win_virtual_bounds 返回非正尺寸: ({}, {}, {}, {})".format(vx, vy, vw, vh))

        good = "400x300+10+10"
        if utils.geometry_in_screen(r, good):
            _ok("geometry_in_screen 对屏内 {} -> True".format(good))
        else:
            _fail("geometry_in_screen 误判屏内 {} -> False".format(good))

        for bad in ("900x700+99999+99999", "100x100+10+10", "", "abc", "400x300"):
            if not utils.geometry_in_screen(r, bad):
                _ok("geometry_in_screen 对无效/越界 {!r} -> False".format(bad))
            else:
                _fail("geometry_in_screen 误判 {!r} -> True".format(bad))

        cg = utils.center_geometry(r, 800, 600)
        c = _parse_geo(cg)
        if c is None:
            _fail("center_geometry 返回不可解析: {!r}".format(cg))
        elif _in_bounds(c, (vx, vy, vw, vh)):
            _ok("center_geometry 结果 {} 落在虚拟屏内".format(cg))
        else:
            _fail("center_geometry 结果 {} 超出虚拟屏 ({}, {}, {}, {})".format(
                cg, vx, vy, vw, vh))
    finally:
        try:
            r.destroy()
        except Exception:
            pass


# ── B. 默认分支 ──

def test_default(d):
    geo = d["geo"]
    c = _parse_geo(geo)
    if c is None:
        _fail("默认分支 geometry 不可解析: {!r}".format(geo))
        return
    w, h, x, y = c
    vroot = d["vroot"]
    vx, vy, vw, vh = vroot
    lo_w = min(d["scaled1000"], max(200, vw - 40))
    lo_h = min(d["scaled680"], max(150, vh - 40))
    if w >= lo_w and h >= lo_h:
        _ok("默认分支尺寸 {0}x{1} ≥ 设计派生下限 {2}x{3} (dpi={4:.3g} ui_scale={5:.3g})".format(
            w, h, lo_w, lo_h, d["dpi"], d["ui_scale"]))
    else:
        _fail("默认分支尺寸 {0}x{1} < 下限 {2}x{3}".format(w, h, lo_w, lo_h))
    if _in_bounds(c, vroot):
        _ok("默认分支几何 {} 落在虚拟屏内".format(geo))
    else:
        _fail("默认分支几何 {} 超出虚拟屏 ({}x{}+{}+{})".format(geo, vw, vh, vx, vy))
    if d.get("in_screen"):
        _ok("默认分支 utils.geometry_in_screen -> True")
    else:
        _fail("默认分支 utils.geometry_in_screen -> False")


# ── C. 记忆分支 ──

def test_memo_valid(d):
    c = _parse_geo(d["geo"])
    u = _parse_geo(d["used_geo"])
    if c is None or u is None:
        _fail("记忆分支(屏内) 解析失败: geo={!r} used={!r}".format(d["geo"], d["used_geo"]))
        return
    if c == u:
        _ok("记忆分支(屏内) geometry 与写入值一致: {}".format(d["geo"]))
    else:
        _fail("记忆分支(屏内)不匹配: 期望 {} 实得 {}".format(d["used_geo"], d["geo"]))


def test_memo_bad(d, default_d):
    geo = d["geo"]
    c = _parse_geo(geo)
    if c is None:
        _fail("越界记忆分支 geometry 不可解析: {!r}".format(geo))
        return
    w, h, x, y = c
    vroot = d["vroot"]
    vx, vy, vw, vh = vroot
    lo_w = min(d["scaled1000"], max(200, vw - 40))
    lo_h = min(d["scaled680"], max(150, vh - 40))
    if _in_bounds(c, vroot) and w >= lo_w and h >= lo_h:
        _ok("越界记忆 (900x700+99999+99999) 回退居中: {} 屏内且尺寸达标".format(geo))
    else:
        _fail("越界记忆回退异常: {} (vroot={})".format(geo, vroot))
    if default_d and geo == default_d.get("geo"):
        _ok("越界记忆回退居中的几何与默认分支完全一致: {}".format(geo))
    else:
        _warn("越界记忆回退几何 {} 与默认分支 {} 不一致".format(
            geo, default_d.get("geo") if default_d else None))


# ── D. 最大化分支 ──

def test_max(d):
    if d.get("state") == "zoomed":
        _ok("最大化分支 root.state() == 'zoomed'")
    else:
        _fail("最大化分支 root.state() == {!r} (期望 'zoomed')".format(d.get("state")))


# ── E. 紧凑分支 ──

def test_compact(d):
    geo = d["geo"]
    c = _parse_geo(geo)
    if c is None:
        _fail("紧凑分支 geometry 不可解析: {!r}".format(geo))
        return
    w, h, x, y = c
    us = d["ui_scale"]
    if abs(us - 1.0) <= 1e-6:
        exp = (500, 625)
    else:
        exp = (d["scaled500"], d["scaled625"])
    if (w, h) == exp:
        _ok("紧凑分支 geometry {0}x{1} == 期望 {2}x{3} (ui_scale={4:.3g})".format(
            w, h, exp[0], exp[1], us))
    else:
        _fail("紧凑分支几何 {0}x{1} != 期望 {2}x{3} (ui_scale={4:.3g})".format(
            w, h, exp[0], exp[1], us))


def _summary():
    print("-" * 56)
    print("FAIL={} WARN={}".format(len(FAILS), len(WARNS)))
    if FAILS:
        print("结论: [FAIL] 存在 {} 项失败".format(len(FAILS)))
        return 1
    print("结论: [OK] 全部通过 (WARN={})".format(len(WARNS)))
    return 0


def main():
    print("=== 主窗口几何 冒烟测试 (utils + 4 分支) ===")
    print("解释器: {}".format(PY))
    print("解释器存在: {}".format(os.path.exists(PY)))
    print("-" * 56)

    if not os.path.exists(PY):
        _fail("未找到 .venv 解释器: {}".format(PY))
        return _summary()

    test_utils_unit()

    results = {}
    outs = {}
    for mode in ("default", "memo_valid", "memo_bad", "max", "compact"):
        data, out = _run_child(mode)
        results[mode] = data
        outs[mode] = out
        if data is None:
            tail = "\n".join(out.strip().splitlines()[-12:]) if out else "(无输出)"
            _fail("分支 {} 子进程未返回 SMOKEJSON; 输出尾部:\n{}".format(mode, tail))

    if results["default"]:
        test_default(results["default"])
    if results["memo_valid"]:
        test_memo_valid(results["memo_valid"])
    if results["memo_bad"]:
        test_memo_bad(results["memo_bad"], results["default"])
    if results["max"]:
        test_max(results["max"])
    if results["compact"]:
        test_compact(results["compact"])

    return _summary()


if __name__ == "__main__":
    sys.exit(main())
