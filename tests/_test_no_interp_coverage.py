# -*- coding: utf-8 -*-
"""`NO_INTERP` 覆盖率门禁（阶段 0 / S0-06，纯数据、无 GUI）。

背景: `commands.NO_INTERP` 是「哪些参数禁止 {var:} 插值」的唯一声明处,
由 `engine._render_args` 的单一 choke point 通过 `commands.get_no_interp()`
消费。它的**失败开放**语义(`get_no_interp` 对未登记命令返回"全部可插值")
意味着: 新增命令忘了登记时, 不会有任何报错 —— 变量名类参数会被插值成
变量的值、安全闸门参数会被 ${x} 动态关掉, 且行为不可审计。
本模块把「漏登记」变成可自动执行的断言。

断言（[OK] / [FAIL]，退出码 0 成功 / 1 失败）：
  A. 双向一致   : set(commands.list_names()) == set(commands.NO_INTERP)，
                  差额分别打印「缺登记」/「多余项」，并逐条打印命令名。
  B. 下标合法   : 非 None 条目的每个下标必须是 int 且落在 0..8（位置式 9 槽），
                  并且不得越出该命令自身的参数个数；`frozenset(spec)` 必须可构造
                  （与 `get_no_interp` 的异常路径同源）。
  C. 块结构意图 : 6 条块结构命令必须登记为 `None`（整条禁插值），
                  不可被悄悄改成 `()`。
  D. 失败开放   : `get_no_interp("不存在的命令")` 必须返回 `(False, frozenset())`。

用法: python -X utf8 tools\\_test_no_interp_coverage.py
退出码: 0=全部通过, 1=存在失败

环境无关: 仅 stdlib + 项目内 `commands`（不 import tkinter / engine / pyautogui），
源码树与冻结版均可运行。
"""
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "src"))

import commands                                    # noqa: E402

# 位置式参数槽上限（workflow.py / scheduler.py / param_form.py 共 9 槽）
MAX_INDEX = 8

# 块结构命令: 不经 execute(), 仍显式登记 None 以表明「永不做插值」的设计意图
BLOCK_COMMANDS = ("如果", "否则", "结束如果", "循环开始", "循环结束", "跳出循环")

_ok = 0
_fail = 0


def ok(msg):
    global _ok
    _ok += 1
    print("[OK]   " + msg)


def fail(msg):
    global _fail
    _fail += 1
    print("[FAIL] " + msg)


def check(cond, ok_msg, fail_msg):
    if cond:
        ok(ok_msg)
    else:
        fail(fail_msg)
    return cond


def main():
    print("=" * 68)
    print("NO_INTERP 覆盖率门禁")
    print("=" * 68)

    names = commands.list_names()
    no_interp = commands.NO_INTERP

    # ── A. 双向一致（无「既不登记也无默认策略」的命令） ──
    print("\n── A. 注册表 / NO_INTERP 双向一致 ──")
    reg = set(names)
    entry = set(no_interp.keys())
    missing = sorted(reg - entry)      # 注册了却没登记 → 失败开放, 最危险
    extra = sorted(entry - reg)        # 登记了但命令已不存在 → 死条目
    if missing:
        fail("缺登记: {} 条命令未在 NO_INTERP 声明插值策略".format(len(missing)))
        for n in missing:
            print("       缺登记: {!r}".format(n))
    else:
        ok("无缺登记：{} 条命令全部有显式插值策略".format(len(reg)))
    if extra:
        fail("多余项: {} 条 NO_INTERP 键在注册表中不存在".format(len(extra)))
        for n in extra:
            print("       多余项: {!r}".format(n))
    else:
        ok("无多余项：NO_INTERP 没有失效命令名")
    check(reg == entry,
          "set(list_names()) == set(NO_INTERP.keys()) ({} 条)".format(len(reg)),
          "set(list_names()) != set(NO_INTERP.keys())")

    dup = len(names) - len(reg)
    check(dup == 0, "注册表无重名命令", "注册表存在 {} 个重名命令".format(dup))

    # ── B. 下标值合法性 ──
    print("\n── B. 登记下标值合法 (0..{}, None 例外) ──".format(MAX_INDEX))
    bad_type, bad_range, bad_param, bad_set = [], [], [], []
    whole_off, part_off = [], []
    for name in names:
        if name not in no_interp:
            continue
        spec = no_interp[name]
        if spec is None:
            whole_off.append(name)
            continue
        try:
            idx = frozenset(spec)
        except TypeError:
            bad_set.append(name)
            continue
        part_off.append(name)
        for i in idx:
            if not isinstance(i, int) or isinstance(i, bool):
                bad_type.append((name, i))
            elif i < 0 or i > MAX_INDEX:
                bad_range.append((name, i))
        nparam = len(commands.schema(name))
        for i in sorted(x for x in idx if isinstance(x, int) and not isinstance(x, bool)):
            if i >= nparam:
                bad_param.append((name, i, nparam))

    check(not bad_set, "所有条目均可构造 frozenset",
          "无法构造 frozenset 的条目: {}".format(bad_set))
    check(not bad_type, "所有下标都是 int",
          "非 int 下标: {}".format(bad_type))
    check(not bad_range, "所有下标都在 0..{} 内".format(MAX_INDEX),
          "越界下标: {}".format(bad_range))
    check(not bad_param, "所有下标都在该命令参数个数内",
          "下标超出参数个数（登记了也不会生效）: {}".format(bad_param))

    # 与 get_no_interp 的一致性: 逐条跑一遍真实消费路径, 不许抛异常
    crashed = []
    for name in names:
        if name not in no_interp:               # 缺登记已由 A 段判失败, 此处不重复计错
            continue
        try:
            flag, idx = commands.get_no_interp(name)
        except Exception as exc:               # noqa: BLE001
            crashed.append((name, repr(exc)))
            continue
        spec = no_interp[name]
        if spec is None:
            if not (flag is True and idx == frozenset()):
                crashed.append((name, "None 条目应返回 (True, frozenset())"))
        elif flag is not False or idx != frozenset(spec):
            crashed.append((name, "返回值与登记不一致: {!r}".format((flag, idx))))
    check(not crashed, "get_no_interp() 对全部命令的返回值与登记一致",
          "get_no_interp() 异常/不一致: {}".format(crashed))

    print("       整条禁插值 {} 条 / 部分参数禁插值 {} 条 / 全部可插值 {} 条".format(
        len(whole_off), len(part_off), len(names) - len(whole_off) - len(part_off)))

    # ── C. 块结构命令登记为 None ──
    print("\n── C. 块结构命令必须登记为 None ──")
    for name in BLOCK_COMMANDS:
        if name not in reg:
            fail("块结构命令 {!r} 不在注册表中（注册表改名了？）".format(name))
            continue
        check(no_interp.get(name, "<未登记>") is None,
              "{} 登记为 None (整条禁插值)".format(name),
              "{} 应登记为 None, 实际 {!r}".format(name, no_interp.get(name, "<未登记>")))

    # ── D. 失败开放语义 ──
    print("\n── D. 未登记命令的失败开放语义 ──")
    got = commands.get_no_interp("不存在的命令")
    check(got == (False, frozenset()),
          'get_no_interp("不存在的命令") == (False, frozenset())',
          'get_no_interp("不存在的命令") 期望 (False, frozenset()), 实际 {!r}'.format(got))

    # ── 汇总 ──
    covered = len(reg & entry)
    print("\n" + "=" * 68)
    print("覆盖 {}/{} 条命令（显式插值策略登记）".format(covered, len(reg)))
    print("通过 {} / 失败 {}".format(_ok, _fail))
    print("=" * 68)
    if _fail:
        print("[FAIL] NO_INTERP 覆盖率门禁未通过")
        return 1
    print("[OK] NO_INTERP 覆盖率门禁通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
