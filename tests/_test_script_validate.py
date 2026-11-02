# -*- coding: utf-8 -*-
"""运行前静态校验回归 (路线图 阶段二第 7 项: `src/script_validate.validate_script`)。

断言:
  V1 空脚本        : → error/empty_script (row=0)
  V2 未知命令      : → error/unknown_command (含正确行号)
  V3 参数过多      : → error/param
  V4 非数字        : → error/param
  V5 枚举越界      : → error/param
  V6 图片缺失      : → warning/image_missing (纯路径判定)
  V7 图片存在      : → 不报 image_missing
  V8 块不配对      : → warning/block_unbalanced (row=0)
  V9 块配对正确    : → 不报 block_unbalanced
  V10 纯函数副作用 : 不写盘 (无写模式 open)、不加载 pyautogui / tkinter

用法: .venv\\Scripts\\python.exe -X utf8 tools\\_test_script_validate.py
退出码: 0=全部通过, 1=存在失败
"""
import builtins
import os
import sys
import tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(BASE, "src")
sys.path.insert(0, SRC)

import script_validate                                  # noqa: E402
from scriptdata import ScriptData                       # noqa: E402

_PASS, _FAIL = [], []


def check(cond, msg, detail=""):
    (_PASS if cond else _FAIL).append(msg)
    tail = ("  ({})".format(detail) if detail else "")
    print("[{}] {}{}".format("OK  " if cond else "FAIL", msg, tail))


def _row(cmd, args=None):
    return ScriptData(cmd, list(args or []))


def _by_code(issues, code):
    return [it for it in issues if it["code"] == code]


def main():
    print("=" * 68)
    print("运行前静态校验自测 (script_validate)")
    print("=" * 68)

    # ── V1 空脚本 ──
    print("\n── V1 空脚本 ──")
    iss = script_validate.validate_script([])
    check(len(iss) == 1 and iss[0]["level"] == "error"
          and iss[0]["code"] == "empty_script" and iss[0]["row"] == 0,
          "空脚本 → error/empty_script (row=0)", str(iss))

    # ── V2 未知命令 ──
    print("\n── V2 未知命令 ──")
    iss = script_validate.validate_script([
        _row("等待", ["1"]),
        _row("__不存在的命令__", ["x"]),
    ])
    uc = _by_code(iss, "unknown_command")
    check(len(uc) == 1 and uc[0]["level"] == "error" and uc[0]["row"] == 2,
          "未知命令 → error/unknown_command 且 row=2", str(uc))
    check("__不存在的命令__" in uc[0]["message"] if uc else False,
          "未知命令 message 含命令名", str(uc[:1]))

    # ── V3 参数过多 ──
    print("\n── V3-V5 参数校验 ──")
    iss = script_validate.validate_script([_row("按键", ["a", "1", "0.1", "x", "y"])])
    pe = _by_code(iss, "param")
    check(len(pe) == 1 and pe[0]["level"] == "error" and pe[0]["row"] == 1
          and "过多" in pe[0]["message"],
          "参数过多 → error/param 且 message 含『过多』", str(pe))

    # ── V4 非数字 ──
    iss = script_validate.validate_script([_row("按键", ["a", "abc"])])
    pe = _by_code(iss, "param")
    check(len(pe) == 1 and "不是数字" in pe[0]["message"],
          "非数字 → error/param 且 message 含『不是数字』", str(pe))

    # ── V5 枚举越界 ──
    iss = script_validate.validate_script([_row("写入", ["text", "0.1", "BADMODE"])])
    pe = _by_code(iss, "param")
    check(len(pe) == 1 and "取值应为" in pe[0]["message"],
          "枚举越界 → error/param 且 message 含『取值应为』", str(pe))

    # ── V6 图片缺失 (warning) ──
    print("\n── V6-V7 图片存在性 (仅路径判定) ──")
    with tempfile.TemporaryDirectory() as tmp:
        iss = script_validate.validate_script([_row("找图", ["nope.png"])], tmp)
        im = _by_code(iss, "image_missing")
        check(len(im) == 1 and im[0]["level"] == "warning" and im[0]["row"] == 1,
              "图片缺失 → warning/image_missing 且 row=1", str(im))

        # ── V7 图片存在 → 不报 ──
        with open(os.path.join(tmp, "here.png"), "wb") as f:
            f.write(b"\x89PNG")
        iss = script_validate.validate_script([_row("找图", ["here.png"])], tmp)
        check(not _by_code(iss, "image_missing"),
              "图片存在 → 不报 image_missing", str(iss))

    # ── V8 块不配对 ──
    print("\n── V8-V9 块配对 (仅计数) ──")
    iss = script_validate.validate_script([_row("循环开始", ["3"])])
    bu = _by_code(iss, "block_unbalanced")
    check(len(bu) == 1 and bu[0]["level"] == "warning" and bu[0]["row"] == 0,
          "循环缺『循环结束』→ warning/block_unbalanced (row=0)", str(bu))

    iss = script_validate.validate_script([_row("如果", ["1 == 1"])])
    bu = _by_code(iss, "block_unbalanced")
    check(len(bu) == 1 and "结束如果" in bu[0]["message"],
          "缺『结束如果』→ block_unbalanced 且 message 含『结束如果』", str(bu))

    # ── V9 块配对正确 → 不报 ──
    iss = script_validate.validate_script([
        _row("如果", ["1 == 1"]),
        _row("结束如果", [None] * 9),
        _row("循环开始", ["3"]),
        _row("循环结束", [None] * 9),
    ])
    check(not _by_code(iss, "block_unbalanced"),
          "配对正确 → 不报 block_unbalanced", str(iss))

    # ── V11 能力缺失 (missing_capability) ──
    # 强制目标能力非 READY, 保证跨环境确定性 (本机装/未装 playwright 都不影响)。
    print("\n── V11 能力缺失 (missing_capability) ──")
    import capabilities as _caps
    _caps.disable("browser.playwright")   # hard
    _caps.disable("input.dd")             # soft
    try:
        iss = script_validate.validate_script([_row("打开网页", ["https://x"])])
        mc = _by_code(iss, "missing_capability")
        check(len(mc) == 1 and mc[0]["level"] == "error" and mc[0]["row"] == 1,
              "hard 能力缺失 → error/missing_capability 且 row=1", str(mc))
        check(bool(mc) and "Playwright" in mc[0]["message"]
              and "disabled" in mc[0]["message"],
              "message 含能力名与状态", str(mc[:1]))

        iss = script_validate.validate_script([_row("写入", ["hi", "0.1", "auto"])])
        mc = _by_code(iss, "missing_capability")
        check(len(mc) == 1 and mc[0]["level"] == "warning",
              "soft 能力缺失 → warning/missing_capability", str(mc))

        iss = script_validate.validate_script([_row("等待", ["1"])])
        check(not _by_code(iss, "missing_capability"),
              "core 命令不产生 missing_capability", str(iss))
    finally:
        _caps.enable("browser.playwright")
        _caps.enable("input.dd")

    # ── V10 纯函数副作用 ──
    print("\n── V10 纯函数: 不写盘 / 不加载 GUI ──")
    writes = {"n": 0}
    _orig_open = builtins.open

    def _spy_open(file, mode="r", *a, **k):
        try:
            if any(c in str(mode) for c in ("w", "a", "x", "+")):
                writes["n"] += 1
        except Exception:
            pass
        return _orig_open(file, mode, *a, **k)

    builtins.open = _spy_open
    try:
        script_validate.validate_script(
            [_row("找图", ["nope.png"]), _row("__不存在__", [])], "")
    finally:
        builtins.open = _orig_open
    check(writes["n"] == 0, "validate_script 不写盘 (无写模式 open)", str(writes))

    check("pyautogui" not in sys.modules,
          "validate_script 未加载 pyautogui")
    check("tkinter" not in sys.modules,
          "validate_script 未加载 tkinter")

    print()
    print("=" * 68)
    print("script_validate 回归: 通过 {} 项, 失败 {} 项".format(len(_PASS), len(_FAIL)))
    print("=" * 68)
    if _FAIL:
        for m in _FAIL:
            print("  [FAIL] {}".format(m))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
