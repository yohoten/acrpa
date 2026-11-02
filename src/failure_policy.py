# -*- coding: utf-8 -*-
"""failure_policy — 步骤级失败策略（对标 AutomationOperation 的
``FailureHandlingMode`` + ``JumpTargetIndex``，纯逻辑独立实现）。

本模块只做**纯函数**：文本归一、参数校验、角标文案、失败动作判定。不 import
tkinter / engine / playwright，可离线单测；行数据由调用方以「策略码 + 跳转目标」
两个值传入，因此既能服务 ``ScriptData``，也能服务 xlrd 行 / 工作流步骤。

策略码
------
========  ========  ==================================================
常量      取值      语义
========  ========  ==================================================
INHERIT   ``""``    未设置 → 沿用全局 ``stop_on_error``（默认行为，零破坏）
STOP      stop      该行失败即停止整个脚本（等价旧的全局行为）
CONTINUE  continue  该行失败仅告警，继续执行后续行
JUMP      jump      该行失败跳转到「跳转目标」行继续执行
========  ========  ==================================================

设计约束（与项目既有约定一致）
------------------------------
* **不新增常显列**：策略存在行的元数据上，编辑器只在 ``#0`` 序号列尾部追加一个
  角标（:func:`badge_text`），信息量靠角标而非列。
* **失败开放**：策略值无法识别 → 视为 ``INHERIT``；跳转目标非法 → 退化为
  ``stop`` 并返回原因，绝不默默跳过整段脚本。
* 跳转目标用 **1 基行号**（与 UI 序号列、断点行号口径一致），返回给执行层时
  换算成 0 基下标。
"""
import re

# ── 策略码 ────────────────────────────────────────────────────────────
POLICY_INHERIT = ""
POLICY_STOP = "stop"
POLICY_CONTINUE = "continue"
POLICY_JUMP = "jump"

ALL_POLICIES = (POLICY_INHERIT, POLICY_STOP, POLICY_CONTINUE, POLICY_JUMP)

# 动作（执行层消费）
ACTION_STOP = "stop"
ACTION_CONTINUE = "continue"
ACTION_JUMP = "jump"

# 角标（#0 序号列尾部）
_BADGES = {
    POLICY_STOP: "⊘",
    POLICY_CONTINUE: "↷",
    POLICY_JUMP: "⇥",
}

# 中文 → 策略码（编辑表单下拉与脚本文本共用同一套同义词）
_ALIASES = {
    "": POLICY_INHERIT,
    "默认": POLICY_INHERIT,
    "继承": POLICY_INHERIT,
    "全局": POLICY_INHERIT,
    "inherit": POLICY_INHERIT,
    "default": POLICY_INHERIT,
    "停止": POLICY_STOP,
    "中止": POLICY_STOP,
    "终止": POLICY_STOP,
    "停": POLICY_STOP,
    "stop": POLICY_STOP,
    "abort": POLICY_STOP,
    "继续": POLICY_CONTINUE,
    "忽略": POLICY_CONTINUE,
    "跳过": POLICY_CONTINUE,
    "continue": POLICY_CONTINUE,
    "ignore": POLICY_CONTINUE,
    "skip": POLICY_CONTINUE,
    "跳转": POLICY_JUMP,
    "跳到": POLICY_JUMP,
    "goto": POLICY_JUMP,
    "jump": POLICY_JUMP,
}

# 跳转防死循环上限（同一目标连续命中超过此值即强制停止）
JUMP_LOOP_LIMIT = 20


def parse_policy(text):
    """文本 → 策略码（纯函数）。

    ``""`` / ``None`` / 未知词 → :data:`POLICY_INHERIT`（失败开放，保持旧行为）。
    支持中文同义词与英文别名，大小写与首尾空白不敏感。
    """
    if text is None:
        return POLICY_INHERIT
    key = str(text).strip().lower().replace(" ", "")
    return _ALIASES.get(key, POLICY_INHERIT)


def policy_text(code):
    """策略码 → 中文标签（供下拉框/日志；纯函数）。"""
    return {
        POLICY_INHERIT: "默认(跟随全局)",
        POLICY_STOP: "停止",
        POLICY_CONTINUE: "继续",
        POLICY_JUMP: "跳转到行",
    }.get(code or POLICY_INHERIT, "默认(跟随全局)")


def choice_labels():
    """下拉框选项（策略码, 中文标签）列表（纯数据）。"""
    return [(code, policy_text(code)) for code in ALL_POLICIES]


def normalize_jump_target(raw):
    """跳转目标文本/数字 → 1 基行号（纯函数）；非法/空 → ``0`` 表示"未填"。"""
    if raw is None:
        return 0
    text = str(raw).strip()
    if not text:
        return 0
    m = re.match(r"^[^\d\-]*(-?\d+)", text)      # 容忍 "⇥12" / "第12行" / "12 行"
    if not m:
        return 0
    try:
        value = int(m.group(1))
    except (TypeError, ValueError):
        return 0
    return value


def badge_text(code, jump_to=0):
    """策略 → ``#0`` 序号列尾部角标（纯函数）。

    未设置 / 未知策略 → 空串（序号列保持原样，不制造视觉噪声）；
    ``jump`` 且目标有效 → ``⇥N``，目标无效 → ``⇥?`` 以便一眼看出配置缺失。
    """
    code = code or POLICY_INHERIT
    mark = _BADGES.get(code, "")
    if not mark:
        return ""
    if code != POLICY_JUMP:
        return " " + mark
    target = normalize_jump_target(jump_to)
    return " {}{}".format(mark, target if target > 0 else "?")


def validate_policy(code, jump_to=0, total_rows=None, row_no=None):
    """校验「策略 + 跳转目标」组合 → ``(ok, message)``（纯函数）。

    - 非 ``jump`` 策略：恒通过（``jump_to`` 留值无害，仅不生效）；
    - ``jump``：目标必须 > 0；给出 ``total_rows`` 时还必须 <= 总行数；
      给出 ``row_no`` 时目标不得等于自身行号（否则必然原地死循环）。
    """
    code = code or POLICY_INHERIT
    if code not in (POLICY_STOP, POLICY_CONTINUE, POLICY_JUMP):
        return True, ""
    if code != POLICY_JUMP:
        return True, ""
    target = normalize_jump_target(jump_to)
    if target <= 0:
        return False, "跳转策略需要填写目标行号（1 起算）"
    if total_rows is not None:
        try:
            total = int(total_rows)
        except (TypeError, ValueError):
            total = None
        if total is not None and total > 0 and target > total:
            return False, "跳转目标 {} 超出脚本行数 {}".format(target, total)
    if row_no is not None and target == int(row_no):
        return False, "跳转目标不能是本行自身（会原地死循环）"
    return True, ""


def decide_failure(code, stop_on_error=True, jump_to=0,
                   current_index=0, total_rows=0):
    """失败动作判定（纯函数）→ ``(action, target_index, note)``。

    * ``action``：:data:`ACTION_STOP` / :data:`ACTION_CONTINUE` / :data:`ACTION_JUMP`；
    * ``target_index``：仅 ``ACTION_JUMP`` 有效，**0 基**下标；
    * ``note``：为空表示按配置正常执行；非空是降级说明（如"跳转目标非法，
      退化为停止"），由调用方写日志。

    ``code`` 为 :data:`POLICY_INHERIT` 时按 ``stop_on_error`` 决定（旧行为）。
    """
    code = code or POLICY_INHERIT
    if code == POLICY_STOP:
        return ACTION_STOP, current_index, ""
    if code == POLICY_CONTINUE:
        return ACTION_CONTINUE, current_index, ""
    if code == POLICY_JUMP:
        target = normalize_jump_target(jump_to)
        row_no = int(current_index) + 1
        ok, msg = validate_policy(code, target, total_rows or None, row_no)
        if not ok:
            return ACTION_STOP, current_index, "失败策略降级为停止: " + msg
        return ACTION_JUMP, target - 1, ""
    # 未设置 → 沿用全局开关（默认行为）
    if stop_on_error:
        return ACTION_STOP, current_index, ""
    return ACTION_CONTINUE, current_index, ""


def row_policy(row):
    """从行对象读取 (策略码, 跳转目标)（纯函数；缺属性 → 默认值）。

    兼容 ``ScriptData``（属性 ``on_error`` / ``jump_to``）与工作流步骤 dict
    （键 ``on_error`` / ``jump_to``），取不到即 INHERIT / 0。
    """
    if row is None:
        return POLICY_INHERIT, 0
    if isinstance(row, dict):
        raw_code = row.get("on_error", "")
        raw_target = row.get("jump_to", 0)
    else:
        raw_code = getattr(row, "on_error", "")
        raw_target = getattr(row, "jump_to", 0)
    return parse_policy(raw_code), normalize_jump_target(raw_target)


def should_stop_on_failure(row, index, total_rows, stop_on_error=True,
                           jump_counts=None):
    """一站式判定（供执行层直接调用）→ ``(action, target_index, note)``。

    在 :func:`decide_failure` 之上补「跳转次数护栏」：同一目标行的跳转命中次数
    超过 :data:`JUMP_LOOP_LIMIT` 时强制停止，避免 A→B→A 这类环把脚本挂死。
    ``jump_counts`` 为调用方持有的 ``{target_index: count}`` 字典（可为 None）。
    """
    code, target = row_policy(row)
    action, idx, note = decide_failure(code, stop_on_error, target,
                                       index, total_rows)
    if action == ACTION_JUMP and jump_counts is not None:
        hits = int(jump_counts.get(idx, 0)) + 1
        jump_counts[idx] = hits
        if hits > JUMP_LOOP_LIMIT:
            return (ACTION_STOP, index,
                    "跳转到第 {} 行已触发 {} 次，判定为环，强制停止".format(
                        idx + 1, hits))
    return action, idx, note


__all__ = [
    "POLICY_INHERIT", "POLICY_STOP", "POLICY_CONTINUE", "POLICY_JUMP",
    "ALL_POLICIES", "ACTION_STOP", "ACTION_CONTINUE", "ACTION_JUMP",
    "JUMP_LOOP_LIMIT",
    "parse_policy", "policy_text", "choice_labels", "normalize_jump_target",
    "badge_text", "validate_policy", "decide_failure", "row_policy",
    "should_stop_on_failure",
]
