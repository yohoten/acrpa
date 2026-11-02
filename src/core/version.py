# -*- coding: utf-8 -*-
"""core.version — 版本号解析 / 比较 (纯 Python, 无 tkinter 依赖)。

从 ``src/utils.py`` 迁出 (docs/项目结构优化建议.md §3.1 / L2-A), 是版本比较的
**唯一权威实现** (P0-3「版本比较统一」): ``src/updater.py`` 与 ``src/script_package.py``
均委托此处, 消除重复实现。

约束: 本模块保持零第三方依赖, 且不得反向 import updater / script_package
      (core 是共享低层包, updater 依赖 requests 且可能反向引用)。
"""


def parse_version(value):
    """解析版本号 → (数值元组, 是否预发布, 预发布键)；无法解析返回 None。

    兼容写法: "v1.2.3" / "0.1.26-beta" / "0.1.26beta" / "0.1.26_rc1" /
    "1.0.0-rc.1"。预发布键按段生成 —— 数字段按数值、字母段按字典序,
    用 (0, int) / (1, str) 打标签, 避免跨类型比较抛异常。
    """
    raw = str(value or "").strip().lstrip("vV")
    if not raw:
        return None

    core, sep, pre = raw.partition("-")
    if not sep:
        # 兼容 0.1.26beta / 0.1.26_rc1 等写法
        i = 0
        while i < len(core) and (core[i].isdigit() or core[i] == "."):
            i += 1
        core, pre = core[:i], core[i:].lstrip("._-")
    else:
        pre = pre.lstrip("._-")

    parts = [p for p in core.split(".") if p != ""]
    if not parts:
        return None
    try:
        nums = tuple(int(p) for p in parts)
    except ValueError:
        return None

    key = tuple((0, int(p)) if p.isdigit() else (1, p.lower())
                for p in pre.replace("-", ".").replace("_", ".").split(".")
                if p)
    return nums, bool(pre), key


def compare_versions(current, latest):
    """比较两个版本号 → -1 (current 更旧) / 0 (相同) / 1 (current 更新)。

    无法解析任一侧时返回 0, 由调用方决定如何处理 (不擅自判定为新版本)。
    """
    a = parse_version(current)
    b = parse_version(latest)
    if a is None or b is None:
        return 0

    na, pb_a, key_a = a
    nb, pb_b, key_b = b
    width = max(len(na), len(nb))
    na = na + (0,) * (width - len(na))
    nb = nb + (0,) * (width - len(nb))

    if na != nb:
        return -1 if na < nb else 1
    # 数值相同: 正式版 > 预发布版 (1.0.0 > 1.0.0-rc1)
    if pb_a != pb_b:
        return 1 if pb_b else -1
    if key_a != key_b:
        return -1 if key_a < key_b else 1
    return 0


def version_tuple(value):
    """语义化版本 → 数值元组 (major, minor, patch, ...)。

    成功解析时返回真实数值部分 (预发布后缀被正确剥离):
    "0.1.29-beta" → (0, 1, 29)。无法解析 / 空输入返回 () 且不抛异常。
    """
    parsed = parse_version(value)
    if parsed is None:
        return ()
    return parsed[0]


def version_gt(a, b):
    """版本 a > b 返回 True。

    任一侧不可解析时 compare_versions 返回 0, 因此返回 False ——
    即不把不可解析的输入误判为「远端有新版本」。
    """
    return compare_versions(a, b) > 0
