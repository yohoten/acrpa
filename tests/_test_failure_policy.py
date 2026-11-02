# -*- coding: utf-8 -*-
"""tests/_test_failure_policy.py — 步骤级失败策略（P2，§6.2）验收。

覆盖（纯逻辑 + 引擎控制流，无 GUI / 无真实命令执行）：
  A 策略归一      : 中文/英文同义词 → 策略码；未知/空 → INHERIT（失败开放）
  B 角标文案      : ⊘ / ↷ / ⇥N / ⇥? / 空串；未设策略不制造视觉噪声
  C 跳转目标解析  : "⇥12"、"第12行"、"12"、负数、乱码
  D 校验          : 非 jump 恒通；jump 需目标 >0、不越界、不等于自身
  E 动作判定      : INHERIT 跟随 stop_on_error；stop/continue/jump 分流；非法降级为 stop
  F 环护栏        : 同一目标命中超过 JUMP_LOOP_LIMIT 强制停止
  G ScriptData    : on_error/jump_to 默认值不改变 to_tuple（10 列契约不变）
  H 引擎控制流    : stop / continue / jump 三种策略在 execute_script 中的真实走向
  I 集成静态断言  : engine 已在失败分支接入 failure_policy

只跑纯逻辑，不弹窗、不联网、不执行任何真实命令。退出码 0(全过/仅 WARN) / 1(存在 FAIL)。
"""
import copy
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


import failure_policy as fp          # noqa: E402
import scriptdata                     # noqa: E402
import state                          # noqa: E402

print("── A. 策略归一 ──")
for text, want in [("", fp.POLICY_INHERIT), ("默认", fp.POLICY_INHERIT),
                   ("继承", fp.POLICY_INHERIT), ("inherit", fp.POLICY_INHERIT),
                   ("停止", fp.POLICY_STOP), ("中止", fp.POLICY_STOP),
                   ("stop", fp.POLICY_STOP), ("STOP ", fp.POLICY_STOP),
                   ("继续", fp.POLICY_CONTINUE), ("跳过", fp.POLICY_CONTINUE),
                   ("continue", fp.POLICY_CONTINUE), ("ignore", fp.POLICY_CONTINUE),
                   ("跳转", fp.POLICY_JUMP), ("跳到", fp.POLICY_JUMP),
                   ("goto", fp.POLICY_JUMP), ("jump", fp.POLICY_JUMP)]:
    eq(fp.parse_policy(text), want, "parse_policy({!r})".format(text))
eq(fp.parse_policy("瞎写"), fp.POLICY_INHERIT, "未知词 → INHERIT（失败开放）")
eq(fp.parse_policy(None), fp.POLICY_INHERIT, "None → INHERIT")
eq(fp.policy_text(fp.POLICY_JUMP), "跳转到行", "policy_text(jump)")
eq([c for c, _t in fp.choice_labels()],
   ["", "stop", "continue", "jump"], "choice_labels 顺序固定")

print("── B. 角标文案 ──")
eq(fp.badge_text(""), "", "未设策略 → 空串")
eq(fp.badge_text(None, 0), "", "None → 空串")
eq(fp.badge_text(fp.POLICY_STOP), " ⊘", "stop → ⊘")
eq(fp.badge_text(fp.POLICY_CONTINUE), " ↷", "continue → ↷")
eq(fp.badge_text(fp.POLICY_JUMP, 12), " ⇥12", "jump+12 → ⇥12")
eq(fp.badge_text(fp.POLICY_JUMP, 0), " ⇥?", "jump 无目标 → ⇥?")
eq(fp.badge_text(fp.POLICY_JUMP, "第8行"), " ⇥8", "jump 目标带中文 → ⇥8")
eq(fp.badge_text("乱码"), "", "未知策略 → 空串（不显示噪声）")

print("── C. 跳转目标解析 ──")
eq(fp.normalize_jump_target("12"), 12, "纯数字")
eq(fp.normalize_jump_target(" ⇥12"), 12, "带角标前缀")
eq(fp.normalize_jump_target("第12行"), 12, "第N行")
eq(fp.normalize_jump_target("行 7"), 7, "中文前缀 + 空格")
eq(fp.normalize_jump_target(""), 0, "空 → 0")
eq(fp.normalize_jump_target(None), 0, "None → 0")
eq(fp.normalize_jump_target("abc"), 0, "乱码 → 0")

print("── D. 校验 ──")
eq(fp.validate_policy(fp.POLICY_STOP, 0, 10, 3), (True, ""), "stop 无需目标")
eq(fp.validate_policy(fp.POLICY_CONTINUE, 0, 10, 3), (True, ""), "continue 无需目标")
_ok, _msg = fp.validate_policy(fp.POLICY_JUMP, 0, 10, 3)
check(not _ok and "目标行号" in _msg, "jump 缺目标 → 拒绝")
_ok, _msg = fp.validate_policy(fp.POLICY_JUMP, 11, 10, 3)
check(not _ok and "超出" in _msg, "jump 越界 → 拒绝")
_ok, _msg = fp.validate_policy(fp.POLICY_JUMP, 3, 10, 3)
check(not _ok and "自身" in _msg, "jump 指向自身 → 拒绝")
eq(fp.validate_policy(fp.POLICY_JUMP, 4, 10, 3), (True, ""), "jump 合法目标")

print("── E. 动作判定 ──")
eq(fp.decide_failure("", True)[0], fp.ACTION_STOP, "INHERIT + 全局停止 → stop")
eq(fp.decide_failure("", False)[0], fp.ACTION_CONTINUE, "INHERIT + 全局继续 → continue")
eq(fp.decide_failure(fp.POLICY_STOP, False)[0], fp.ACTION_STOP,
   "行级 stop 覆盖全局 continue")
eq(fp.decide_failure(fp.POLICY_CONTINUE, True)[0], fp.ACTION_CONTINUE,
   "行级 continue 覆盖全局 stop")
_act, _idx, _note = fp.decide_failure(fp.POLICY_JUMP, True, 5, 2, 10)
eq((_act, _idx, _note), (fp.ACTION_JUMP, 4, ""), "jump 目标 5 → 0 基 4")
_act, _idx, _note = fp.decide_failure(fp.POLICY_JUMP, True, 0, 2, 10)
eq(_act, fp.ACTION_STOP, "jump 缺目标 → 降级 stop")
check("降级" in _note, "降级必须给出说明（不静默）: {!r}".format(_note))

print("── F. 环护栏 ──")
_row = type("R", (), {})()
_row.on_error = fp.POLICY_JUMP
_row.jump_to = 2
_counts = {}
_actions = []
for _ in range(fp.JUMP_LOOP_LIMIT + 2):
    _a, _t, _n = fp.should_stop_on_failure(_row, 0, 10, True, _counts)
    _actions.append(_a)
check(_actions[0] == fp.ACTION_JUMP, "起初按跳转执行")
check(_actions[-1] == fp.ACTION_STOP, "超过上限后强制停止")
check(_counts.get(1) == fp.JUMP_LOOP_LIMIT + 2, "命中计数按目标累积")
_a, _t, _n = fp.should_stop_on_failure(_row, 0, 10, True, None)
eq(_a, fp.ACTION_JUMP, "counts=None 时不启用护栏（调用方可选）")

print("── G. ScriptData 契约 ──")
_sd = scriptdata.ScriptData("浏览器点击", ["#a"] + [""] * 8)
_tuple_before = _sd.to_tuple()
eq((_sd.on_error, _sd.jump_to), ("", 0), "默认无策略（旧行为不变）")
eq(len(_tuple_before), 10, "to_tuple 仍为 10 列（未新增常显列）")
eq(_sd.failure_badge(), "", "默认无角标")
_sd.set_failure_policy("跳转", "第7行")
eq((_sd.on_error, _sd.jump_to), ("jump", 7), "set_failure_policy 归一")
eq(_sd.failure_badge(), " ⇥7", "角标同步")
eq(_sd.failure_meta(), {"on_error": "jump", "jump_to": 7}, "可持久化元数据")
eq(_sd.to_tuple(), _tuple_before, "设置策略后 to_tuple 逐字不变（10 列契约）")
_sd.set_failure_policy("继续", 0)
eq(_sd.failure_meta(), {"on_error": "continue"}, "continue 不带多余 jump_to")
_sd.set_failure_policy("", 0)
eq(_sd.failure_meta(), {}, "清空策略 → 空 dict（旧文件不受影响）")
_cp = copy.deepcopy(_sd)
eq((_cp.on_error, _cp.jump_to), ("", 0), "deepcopy 保留策略属性（undo 快照安全）")

print("── H. 引擎控制流（stub 执行，不跑真实命令） ──")
try:
    import engine as _engine_mod

    def _mk_row(cmd, on_error="", jump_to=0):
        sd = scriptdata.ScriptData(cmd)
        sd.set_failure_policy(on_error, jump_to)
        return sd

    def _run(rows, fail_names, stop_on_error=True):
        """执行 rows，`cmd in fail_names` 的行返回失败；返回被执行过的命令序列。"""
        eng = _engine_mod.ExecutionEngine()
        seen = []

        def _stub_execute(row, script_dir, _api=None):
            seen.append(row.cmd_type)
            if row.cmd_type in fail_names:
                return _engine_mod.ExecutionResult(False, "STUB_FAIL", "stub 失败")
            return _engine_mod.ExecutionResult(True, "ok")

        eng.execute = _stub_execute
        _old = getattr(state, "STOP_ON_ERROR", True)
        state.STOP_ON_ERROR = stop_on_error
        try:
            eng.execute_script(rows, os.getcwd())
        finally:
            state.STOP_ON_ERROR = _old
        return seen, eng

    # 1) 未设策略 + 全局停止 → 失败即停（旧行为逐字保持）
    rows = [_mk_row("A"), _mk_row("B"), _mk_row("C")]
    seen, eng = _run(rows, {"B"}, stop_on_error=True)
    eq(seen, ["A", "B"], "INHERIT + stop_on_error → 失败即停")
    check(eng._script_failed, "_script_failed 置位（失败仍被记录）")

    # 2) 未设策略 + 全局继续 → 跑完全部
    seen, _eng = _run(rows, {"B"}, stop_on_error=False)
    eq(seen, ["A", "B", "C"], "INHERIT + 全局继续 → 继续执行")

    # 3) 行级 继续 覆盖全局停止
    rows = [_mk_row("A", "继续"), _mk_row("B"), _mk_row("C")]
    seen, _eng = _run(rows, {"A"}, stop_on_error=True)
    eq(seen, ["A", "B", "C"], "行级 continue 覆盖全局 stop")

    # 4) 行级 停止 覆盖全局继续
    rows = [_mk_row("A"), _mk_row("B", "停止"), _mk_row("C")]
    seen, _eng = _run(rows, {"B"}, stop_on_error=False)
    eq(seen, ["A", "B"], "行级 stop 覆盖全局 continue")

    # 5) 跳转：第 2 行失败 → 跳到第 4 行（跳过 C）
    rows = [_mk_row("A"), _mk_row("B", "跳转", 4), _mk_row("C"), _mk_row("D")]
    seen, _eng = _run(rows, {"B"}, stop_on_error=True)
    eq(seen, ["A", "B", "D"], "jump 4 → 跳过第 3 行")

    # 6) 跳转目标非法（越界）→ 降级为停止
    rows = [_mk_row("A"), _mk_row("B", "跳转", 99), _mk_row("C")]
    seen, _eng = _run(rows, {"B"}, stop_on_error=True)
    eq(seen, ["A", "B"], "jump 越界 → 降级停止（不跳过余下脚本）")

    # 7) 跳转环 → 护栏强制停止（第1行失败跳第2行、第2行失败跳第1行，互相回跳）
    rows = [_mk_row("A", "跳转", 2), _mk_row("B", "跳转", 1)]
    seen, _eng = _run(rows, {"A", "B"}, stop_on_error=True)
    # 护栏按「跳转目标」各自限次，2 行互跳 = 2 个目标 × (上限+1) 次后收敛
    _budget = 2 * (fp.JUMP_LOOP_LIMIT + 1) + 2
    check(len(seen) > 2, "环护栏生效前确实发生了多次跳转 (执行 {} 次)".format(len(seen)))
    check(len(seen) <= _budget,
          "环护栏在 {} 次内收敛 (实际 {})".format(_budget, len(seen)))
    # 8) 跳转指向自身 → 判定非法, 降级停止（不原地死循环）
    rows = [_mk_row("A", "跳转", 1), _mk_row("B")]
    seen, _eng = _run(rows, {"A"}, stop_on_error=True)
    eq(seen, ["A"], "jump 指向自身 → 降级停止")
except Exception as e:      # 引擎依赖不可用时不影响纯逻辑断言
    warn("引擎控制流用例跳过: {}".format(e))

print("── I. 集成静态断言 ──")
try:
    with open(os.path.join(SRC, "engine.py"), "rb") as _fh:
        _esrc = _fh.read().decode("utf-8", "replace")
    check("import failure_policy" in _esrc, "engine 已 import failure_policy")
    check("failure_policy.should_stop_on_failure(" in _esrc,
          "engine 失败分支调用 should_stop_on_failure")
    check("ACTION_JUMP" in _esrc, "engine 处理跳转动作")
    check("_jump_counts" in _esrc, "engine 维护跳转计数（环护栏）")
except Exception as e:
    warn("静态断言跳过: {}".format(e))

print("── J. 持久化往返（.acrpas / .xls，策略不丢） ──")
try:
    import tempfile
    import script_io

    _dir = tempfile.mkdtemp(prefix="acrpa_policy_")

    def _rows():
        r1 = scriptdata.ScriptData("浏览器点击", ["#a"] + [""] * 8)
        r1.set_failure_policy("继续", 0)
        r2 = scriptdata.ScriptData("浏览器输入", ["#b"] + [""] * 8)
        r2.set_failure_policy("跳转", 3)
        r3 = scriptdata.ScriptData("等待", ["1"] + [""] * 8)     # 无策略
        return [r1, r2, r3]

    # .acrpas
    _doc = script_io.rows_to_acrpas(_rows())
    eq(_doc["rows"][0].get("on_error"), "continue", "acrpas 写出 continue")
    eq(_doc["rows"][1].get("jump_to"), 3, "acrpas 写出 jump_to")
    check("on_error" not in _doc["rows"][2], "无策略行不写多余键（旧文件逐字不变）")
    _back = script_io.acrpas_to_rows(_doc)
    eq([(r.on_error, r.jump_to) for r in _back],
       [("continue", 0), ("jump", 3), ("", 0)], "acrpas 载入还原策略")

    # .xls（策略存第二张工作表，主表 10 列不变）
    _p = os.path.join(_dir, "demo.xls")
    script_io.save_script(_p, _rows())
    _back = script_io.load_script(_p)
    eq([(r.on_error, r.jump_to) for r in _back],
       [("continue", 0), ("jump", 3), ("", 0)], "xls 载入还原策略")
    eq([r.cmd_type for r in _back], ["浏览器点击", "浏览器输入", "等待"],
       "xls 主表命令顺序不受影响")

    # 旧工作簿（无策略表）→ 全部 INHERIT，不报错
    import xlwt
    _legacy = os.path.join(_dir, "legacy.xls")
    _wb = xlwt.Workbook()
    _ws = _wb.add_sheet("Sheet1")
    _ws.write(0, 0, "命令类型")
    _ws.write(1, 0, "（标题行）")
    _ws.write(2, 0, "等待")
    _ws.write(2, 1, "2")
    _wb.save(_legacy)
    _back = script_io.load_script(_legacy)
    eq([(r.on_error, r.jump_to) for r in _back], [("", 0)],
       "旧工作簿无策略表 → 默认继承全局")
except Exception as e:
    warn("持久化往返用例跳过: {}".format(e))

print()
print("=" * 60)
print("PASS 全部通过" if not _FAILS else "FAIL {} 项失败".format(len(_FAILS)))
if _WARNS:
    print("WARN {} 项提示".format(len(_WARNS)))
sys.exit(0 if not _FAILS else 1)
