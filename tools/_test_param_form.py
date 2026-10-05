# -*- coding: utf-8 -*-
"""命令参数表单 (ui.param_form) 回归自测 (路线图 §阶段二第 8 项, 离线)。

背景: 编辑器 / 工作流此前把命令参数一律摆成「固定 9 个无标签 Entry」, 对
`commands.schema(cmd_type)` 已有的类型/枚举/默认/变长一无所知。本模块把参数
表升级为「schema 驱动的具名控件」, 同时保持「args 恒 9 位」对外契约不变。

断言:
  P1 契约     : get_args() 长度恒 9 且元素为 str (空位 ""); 反向映射恒 9 位
  P2 回退     : 无 schema / 未知命令 → 9 个通用 Entry, 取值与输入一致
  P3 类型渲染 : 数字字段校验; 枚举 Combobox(左/右, sandbox/trusted/full);
                变长命令「+ 追加」可用
  P4 往返     : schema_values_to_args(args_to_schema_values(cmd, args)) == 归一 args
  P5 校验一致 : 字段校验结论与 commands.validate 一致 (空值放行)
  P6 GUI 冒烟 : (ACRPA_GUI_TEST=1 opt-in) 构建 param_form 无异常, get_args 仍 9 位
  W  接线(静态): workflow_view command 分支与 ACRPA「编辑行」入口确实接了 param_form

用法: .venv\\Scripts\\python.exe -X utf8 tools\\_test_param_form.py
退出码: 0=全部通过, 1=存在失败
注: P1–P5 需要 Tk 控件; 无可用显示时这几项自动 SKIP (不计失败), 纯逻辑与静态
    接线断言仍必跑, 以保证 CI 无头环境同样绿。
"""
import io
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(BASE, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import commands                                     # noqa: E402
import ui.param_form as param_form                  # noqa: E402

_PASS, _FAIL, _SKIP = [], [], []


def check(cond, msg, detail=""):
    (_PASS if cond else _FAIL).append(msg)
    tail = ("  ({})".format(detail) if detail else "")
    print("[{}] {}{}".format("OK  " if cond else "FAIL", msg, tail))


def skip(msg):
    _SKIP.append(msg)
    print("[SKIP] {}".format(msg))


def _read(rel):
    with io.open(os.path.join(BASE, rel), "r", encoding="utf-8") as f:
        return f.read()


def norm9(args):
    """本地归一 (与 commands._clean 同口径): 长度 9, None/空白/"None"/"-" → ""。"""
    out = []
    for i in range(9):
        v = args[i] if i < len(args) else None
        s = "" if v is None else str(v).strip()
        out.append("" if s.lower() in ("none", "null", "-", "") else s)
    return out


# ── Tk 可用性 (无头环境 → 控件级断言 SKIP, 不计失败) ──
_TK_ROOT = None
try:
    import tkinter
    from tkinter import ttk
    _TK_ROOT = tkinter.Tk()
    _TK_ROOT.withdraw()
    HAVE_TK = True
except Exception as _e:                              # pragma: no cover
    HAVE_TK = False
    _TK_ERR = str(_e)


_COLORS = {"bgc": "#FFFFFF", "fgb": "#333333", "ebg": "#FFFFFF", "fgt": "#000000",
           "errbg": "#fff0f0", "ac": "#0078D4"}
_FONTS = {"body": None, "small": None, "button": None, "log": None}


def _build(cmd, args=None):
    frame, get = param_form.build_arg_form(_TK_ROOT, cmd, args,
                                           colors=_COLORS, fonts=_FONTS)
    return frame, get


def _walk(w):
    yield w
    for c in w.winfo_children():
        for x in _walk(c):
            yield x


def _combos(frame):
    return [w for w in _walk(frame) if isinstance(w, ttk.Combobox)]


def _entries(frame):
    return [w for w in _walk(frame) if isinstance(w, tkinter.Entry)
            and not isinstance(w, ttk.Combobox)]


def _buttons(frame, text=None):
    out = []
    for w in _walk(frame):
        if isinstance(w, tkinter.Button):
            try:
                if text is None or w.cget("text") == text:
                    out.append(w)
            except Exception:
                pass
    return out


# ══════════════════════════════════════════════════════════════════════
# P1 契约
# ══════════════════════════════════════════════════════════════════════

def t_p1_contract():
    print("\n── P1 契约: get_args() 恒 9 位 · 反向映射恒 9 位 ──")
    # 纯逻辑: 反向映射对「任意命令 × 任意值」恒 9 位 str
    bad = []
    for name in commands.list_names():
        for vals in (["a", "b", "c"], [], None, ["1", None, ""]):
            out = param_form.schema_values_to_args(name, vals)
            if len(out) != 9 or not all(isinstance(x, str) for x in out):
                bad.append((name, vals))
    check(not bad, "schema_values_to_args 对所有命令恒返回 9 位 str", "异常={}".format(bad[:3]))

    if not HAVE_TK:
        skip("P1 控件级 (get_args): 无可用 Tk ({})".format(_TK_ERR))
        return
    ok = True
    for name in ("找图", "坐标", "按键", "Python", "浏览器上传", "复制",
                 "不存在的命令__x"):
        _fr, get = _build(name, ["a", "b", None, "c"])
        got = get()
        if len(got) != 9 or not all(isinstance(x, str) for x in got):
            ok = False
            check(False, "{} get_args 返回 9 位 str".format(name), "got={}".format(got))
    check(ok, "各命令 get_args() 长度恒 9 且元素为 str (空格为 \"\")")

    _fr, get = _build("找图", ["图", "0.9"])
    got = get()
    check(len(got) == 9 and got[0] == "图" and got[1] == "0.9",
          "get_args 保留已填位置的值", "got={}".format(got))
    check(got[2:] == [""] * 7, "空位置一律为 \"\" (非 None / 非 \"None\")", "got={}".format(got[2:]))


# ══════════════════════════════════════════════════════════════════════
# P2 回退
# ══════════════════════════════════════════════════════════════════════

def t_p2_fallback():
    print("\n── P2 回退: 无 schema / 未知命令 → 9 通用 Entry ──")
    check(commands.schema("复制") == [], "『复制』确无参数 schema")
    check(commands.schema("不存在的命令__x") == [], "未知命令无 schema")
    if not HAVE_TK:
        skip("P2 控件级: 无可用 Tk ({})".format(_TK_ERR))
        return
    for cmd in ("复制", "不存在的命令__x"):
        _fr, get = _build(cmd, ["v1", "v2", "v3", "v4", "v5", "v6", "v7", "v8", "v9"])
        n_entries = len(_entries(_fr))
        check(n_entries == 9, "{} 回退为 9 个通用 Entry".format(cmd),
              "entries={}".format(n_entries))
        # 取值与输入一致 (逐位回读)
        got = get()
        check(got == ["v1", "v2", "v3", "v4", "v5", "v6", "v7", "v8", "v9"],
              "{} 取值与输入一致".format(cmd), "got={}".format(got))


# ══════════════════════════════════════════════════════════════════════
# P3 类型渲染
# ══════════════════════════════════════════════════════════════════════

def t_p3_render():
    print("\n── P3 类型渲染: 数字 / 枚举 / 变长 ──")
    s = commands.schema("找图")
    check(s[1]["kind"] == "number", "找图.精度 schema kind == number")
    # 数字校验口径 (FocusOut 使用同一 field_error)
    check(param_form.field_error("number", (), "abc") is not None,
          "数字字段非数字被判定非法")
    check(param_form.field_error("number", (), "0.9") is None, "数字字段合法值通过")
    check(param_form.field_error("number", (), "") is None, "数字字段空值放行")

    if not HAVE_TK:
        skip("P3 控件级: 无可用 Tk ({})".format(_TK_ERR))
        return

    fr, _get = _build("找图", ["图", "0.9"])
    check(len(_entries(fr)) >= 1, "找图 渲染出 Entry 控件 (图片名/精度)")

    fr, _get = _build("坐标", ["10", "20", "左", "2", "0.5"])
    vals = [tuple(c.cget("values")) for c in _combos(fr)]
    check(("左", "右") in vals, "坐标.按键 渲染为 Combobox(左/右)", "combos={}".format(vals))

    fr, _get = _build("Python", ["print(1)", "sandbox"])
    vals = [tuple(c.cget("values")) for c in _combos(fr)]
    check(("sandbox", "trusted", "full") in vals,
          "Python.权限 渲染为 Combobox(sandbox/trusted/full)", "combos={}".format(vals))

    check(any(p.get("variadic") for p in commands.schema("浏览器上传")),
          "浏览器上传 schema 含变长参数")
    fr, get = _build("浏览器上传", ["#input", "a.txt"])
    add_btns = _buttons(fr, "+ 追加")
    check(len(add_btns) == 1, "浏览器上传 渲染出「+ 追加」按钮",
          "buttons={}".format(len(add_btns)))
    before = len(_entries(fr))
    if add_btns:
        add_btns[0].invoke()          # 追加一项
    after = len(_entries(fr))
    check(after == before + 1, "「+ 追加」可新增一项录入位",
          "before={} after={}".format(before, after))
    got = get()
    check(len(got) == 9, "变长命令 get_args 仍恒 9 位", "len={}".format(len(got)))


# ══════════════════════════════════════════════════════════════════════
# P4 往返
# ══════════════════════════════════════════════════════════════════════

def t_p4_roundtrip():
    print("\n── P4 往返: values ↔ 9 位 args ──")
    cases = [
        ("找图", ["目标图", "0.95"]),
        ("坐标", ["100", "200", "左", "2", "0.5"]),
        ("按键", ["a", None, ""]),
        ("Python", ["print(1)", "trusted"]),
        ("浏览器上传", ["#input", "a.txt", "b.txt", "c.txt", "是"]),
        ("复制", []),
        ("不存在的命令__x", ["x", "y"]),
        ("找图", None),
    ]
    ok = True
    for cmd, args in cases:
        vals = param_form.args_to_schema_values(cmd, args)
        back = param_form.schema_values_to_args(cmd, vals)
        exp = norm9(args or [])
        if back != exp:
            ok = False
            check(False, "{} 往返一致".format(cmd), "back={} exp={}".format(back, exp))
    check(ok, "schema_values_to_args(args_to_schema_values(cmd, args)) == 归一 args")


# ══════════════════════════════════════════════════════════════════════
# P5 校验一致性
# ══════════════════════════════════════════════════════════════════════

def _field_verdicts(cmd, args):
    """逐具名字段的 field_error 结论 → {name: bool(有不合法)}。"""
    ps = commands.schema(cmd)
    out = {}
    for i, p in enumerate(ps):
        if i >= 9:
            break
        v = args[i] if i < len(args) else None
        err = param_form.field_error(p["kind"], tuple(p.get("choices") or ()), v)
        out[p["name"]] = err is not None
    return out


def t_p5_validate_consistency():
    print("\n── P5 校验一致: field_error ↔ commands.validate ──")
    cases = [
        ("找图", ["目标", "0.95"]),
        ("找图", ["目标", "abc"]),
        ("坐标", ["10", "20", "中", "2"]),
        ("Python", ["code", "bogus"]),
        ("按键", ["a", "", ""]),
        ("按键", ["a", "None", "None"]),
    ]
    ok = True
    for cmd, args in cases:
        errs, _norm = commands.validate(cmd, args)
        verdicts = _field_verdicts(cmd, args)
        for name, bad in verdicts.items():
            v_bad = any(("『{}』".format(name)) in e for e in errs)
            if v_bad != bad:
                ok = False
                check(False, "{} 字段『{}』校验结论一致".format(cmd, name),
                      "field={} validate={}".format(bad, errs))
    check(ok, "字段校验结论与 commands.validate 一致 (空值放行)")


# ══════════════════════════════════════════════════════════════════════
# W 接线 (静态, 始终运行)
# ══════════════════════════════════════════════════════════════════════

def t_wiring_static():
    print("\n── W 接线 (静态) ──")
    wf = _read("src/ui/workflow_view.py")
    check("param_form.build_arg_form(" in wf,
          "workflow_view command 分支改用 param_form.build_arg_form")
    check('step["params"] = ' in wf, "workflow_view 仍写 step[\"params\"]")
    check("<<ComboboxSelected>>" in wf, "workflow_view 命令切换时重建表单")

    a = _read("src/ACRPA.py")
    check("param_form.build_arg_form(" in a, "ACRPA 编辑行对话框接入 param_form")
    check("def _cmd_edit_row_dialog(" in a, "ACRPA 新增 _cmd_edit_row_dialog()")
    check('claim_window(win, "edit_row")' in a, "编辑行窗口声明自管换肤 (claim_window)")
    check("ui_theme.subscribe(_refresh)" in a, "编辑行窗口订阅 ThemeBus 换肤")
    check("ui_theme.unsubscribe(_refresh)" in a, "编辑行窗口关闭时退订 (unsubscribe)")
    check('tree.bind("<F2>"' in a, "F2 绑定「编辑行」")
    check('_tbtn(toolbar_inner,"✎ 编辑",_cmd_edit_row_dialog' in a,
          "工具栏「✎ 编辑」入口指向 _cmd_edit_row_dialog")
    # 不得破坏既有静态断言依赖的字面量
    check('_tbtn(toolbar_inner,"+ 添加",_cmd_add_row,"ac","white"' in a,
          "「+ 添加」按钮字面量保持不变 (theme_bus 断言依赖)")
    check("tree.bind(\"<Double-1>\", lambda e: _edit_cell())" in a,
          "内联编辑 <Double-1> 绑定语义未被改动")


# ══════════════════════════════════════════════════════════════════════
# P6 GUI 冒烟 (opt-in)
# ══════════════════════════════════════════════════════════════════════

def t_p6_gui_optin():
    print("\n── P6 GUI 冒烟 (ACRPA_GUI_TEST=1 opt-in) ──")
    if os.environ.get("ACRPA_GUI_TEST") != "1":
        skip("P6 未启用 (设置 ACRPA_GUI_TEST=1 运行)")
        return
    if not HAVE_TK:
        skip("P6 无可用 Tk ({})".format(_TK_ERR))
        return
    for cmd in commands.list_names():
        fr, get = _build(cmd, ["a", "b", "c"])
        got = get()
        check(len(got) == 9, "P6 构建 param_form({}) 无异常且 get_args 恒 9 位".format(cmd),
              "len={}".format(len(got)))


def main():
    print("=" * 68)
    print("命令参数表单 ui.param_form 自测 (路线图 §阶段二第 8 项)")
    print("=" * 68)
    print("HAVE_TK = {}".format(HAVE_TK))
    t_p1_contract()
    t_p2_fallback()
    t_p3_render()
    t_p4_roundtrip()
    t_p5_validate_consistency()
    t_wiring_static()
    t_p6_gui_optin()
    try:
        if _TK_ROOT is not None:
            _TK_ROOT.destroy()
    except Exception:
        pass
    print("\n" + "=" * 68)
    print("通过 {} / 失败 {} / 跳过 {}".format(len(_PASS), len(_FAIL), len(_SKIP)))
    print("结论: {}".format("FAIL" if _FAIL else "PASS"))
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
