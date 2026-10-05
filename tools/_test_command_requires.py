# -*- coding: utf-8 -*-
"""commands 可选依赖声明回归 (路线图 阶段二新增项①)。

断言:
  R1 向后兼容    : list_all() 仍返回四元组且 list_names() 与之一致 (68 条)
  R2 register    : register(requires=) 末位新增且默认兼容 (旧调用不传 → ())
  R3 契约        : requires() 未声明返回 (); list_requires() 为 dict 快照
  R4 覆盖率 ≥95% : requires(name) != () 的命令数 / 总数 ≥ 0.95
  R5 外部能力分配: 找图→cv.match / 打开网页→browser.playwright /
                   识别文字→ocr.paddle_dll / 写入→input.dd
  R6 默认 core   : 其余命令声明 ("core",) (永不被拦截/加角标)
  R7 展示辅助    : display_names() 与 strip_display_badge() 往返一致

用法: .venv\\Scripts\\python.exe -X utf8 tools\\_test_command_requires.py
退出码: 0=全部通过, 1=存在失败
"""
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(BASE, "src")
sys.path.insert(0, SRC)

import commands                                         # noqa: E402

_PASS, _FAIL = [], []


def check(cond, msg, detail=""):
    (_PASS if cond else _FAIL).append(msg)
    tail = ("  ({})".format(detail) if detail else "")
    print("[{}] {}{}".format("OK  " if cond else "FAIL", msg, tail))


def main():
    print("=" * 68)
    print("commands 能力声明自测")
    print("=" * 68)

    names = commands.list_names()
    total = len(names)

    # ── R1 向后兼容 (四元组) ──
    print("\n── R1 向后兼容 (四元组契约) ──")
    allc = commands.list_all()
    check(total > 0 and all(len(t) == 4 for t in allc),
          "list_all() 仍返回四元组 ({} 条)".format(len(allc)))
    check(names == [t[0] for t in allc], "list_names() 与 list_all() 顺序一致")
    check(total == 68, "命令总数 = 68 (实际 {})".format(total))

    # ── R2 register(requires=) 默认兼容 ──
    print("\n── R2 register(requires=) 默认兼容 ──")
    commands.register("__req_probe_plain__", "自测用", "无参数")
    check(commands.requires("__req_probe_plain__") == (),
          "不传 requires → requires() == ()")
    commands.register("__req_probe_ext__", "自测用", "无参数", None,
                      requires=["cap.x", "cap.y"])
    check(commands.requires("__req_probe_ext__") == ("cap.x", "cap.y"),
          "显式 requires 生效 (list → tuple)")
    # 清理 (含 _requires)
    for _n in ("__req_probe_plain__", "__req_probe_ext__"):
        commands._registry[:] = [r for r in commands._registry if r[0] != _n]
        commands._schemas.pop(_n, None)
        commands._requires.pop(_n, None)

    # ── R3 契约 ──
    print("\n── R3 requires() / list_requires() 契约 ──")
    lr = commands.list_requires()
    check(isinstance(lr, dict), "list_requires() 返回 dict")
    check(set(names) <= set(lr.keys()),
          "list_requires() 覆盖全部已注册命令")
    check(all(isinstance(v, tuple) for v in lr.values()),
          "list_requires() 值均为 tuple")
    check(commands.requires("__definitely_not_registered__") == (),
          "未声明/未注册命令 → ()")

    # ── R4 覆盖率 ≥95% ──
    print("\n── R4 覆盖率 ──")
    covered = sum(1 for n in names if commands.requires(n))
    rate = covered / float(total) if total else 0.0
    check(rate >= 0.95,
          "requires 覆盖率 {:.1%} ≥ 95% ({}/{})".format(rate, covered, total))

    # ── R5 外部能力分配 ──
    print("\n── R5 外部能力分配 ──")
    expect = {
        "找图": ("cv.match",), "区域找图": ("cv.match",),
        "点图": ("cv.match",), "区域点图": ("cv.match",),
        "打开网页": ("browser.playwright",),
        "浏览器点击": ("browser.playwright",),
        "启动浏览器录制": ("browser.playwright",),
        "识别文字": ("ocr.paddle_dll",),
        "等待文字": ("ocr.paddle_dll",),
        "点击文字": ("ocr.paddle_dll",),
        "写入": ("input.dd",),
    }
    for name, want in expect.items():
        check(commands.requires(name) == want,
              "{} → {}".format(name, want), str(commands.requires(name)))

    # 浏览器系列应全部声明 browser.playwright
    browser = [n for n in names if n in {
        "打开网页", "浏览器点击", "浏览器输入", "等待元素", "浏览器截图",
        "浏览器执行JS", "执行JS", "浏览器读取Cookie", "浏览器设置Cookie",
        "切换框架", "返回主框架", "新建标签页", "切换标签页", "关闭标签页",
        "等待下载", "浏览器上传", "连接已开浏览器", "接管浏览器", "开始监听",
        "等待数据包", "停止监听", "启动浏览器录制"}]
    check(len(browser) == 22, "浏览器系列命令 = 22 条 (实际 {})".format(len(browser)))
    check(all(commands.requires(n) == ("browser.playwright",) for n in browser),
          "浏览器系列全部声明 browser.playwright")

    # ── R6 默认 core ──
    print("\n── R6 其余命令默认 core ──")
    non_external = [n for n in names if commands.requires(n) == ("core",)]
    check(len(non_external) == total - 30,
          "core 命令 = 总数 - 30 条外部能力命令 (实际 {} 条 core)".format(
              len(non_external)))
    check(commands.requires("设置变量") == ("core",), "设置变量 → core")
    check(commands.requires("如果") == ("core",), "如果 → core")

    # ── R7 展示辅助 ──
    print("\n── R7 下拉展示辅助 ──")
    disp = commands.display_names()
    check(len(disp) == total, "display_names() 长度 == 命令数")
    check(all(commands.strip_display_badge(d) == n
              for n, d in zip(names, disp)),
          "strip_display_badge(display) 往返还原命令名")
    check(commands.strip_display_badge("找图 ⚠") == "找图", "角标可被剥离")
    check(commands.strip_display_badge("等待") == "等待", "无角标命令名原样返回")

    print()
    print("=" * 68)
    print("command_requires 回归: 通过 {} 项, 失败 {} 项".format(len(_PASS), len(_FAIL)))
    print("=" * 68)
    if _FAIL:
        for m in _FAIL:
            print("  [FAIL] {}".format(m))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
