#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""工作流模块库 (左栏「操作库」) 数据源改造 冒烟自测 —— 开发计划 §2.1 S0-04 卡。

用法:
    .venv\\Scripts\\python.exe -X utf8 tools\\_test_workflow_library.py

设计约束:
  · **非交互**: 只建一个 `Tk()` 根窗口并 `withdraw()`, 全程不弹可见窗口、不用 messagebox;
  · **不可用即跳过**: 起不了 Tk (CI 服务会话 / 无显示) → 打印 SKIP 并 exit 0;
  · **不改仓库数据**: 兜底分支通过 `_wf_populate_library(groups_json=<不存在路径>)` 注入,
    `res/help/*.json` 一个字节都不动; 最近使用落盘指向临时目录。

覆盖 (卡内验收断言 2~3 + 用户口径):
  A  数据源驱动 : 模块库树 11 个分组节点 / 88 个命令叶子; 叶子命令名集合 == commands.list_names();
                  子族恒空 → 不造空节点 (分组节点下直接是命令);
  B  检索       : `文件` → 命中命令数 >0 且 <=88; `zzz不存在zzz` → 0 条 (树为空);
  C  兜底分支   : groups_json 指到不存在路径 → 仍建成树、叶子数 == 45 (8 分类快照)、无异常;
                  且产生 1 条降级提示 (log1 日志 + `_WF_LIB_STATUS`), 第二次不重复刷日志;
  D  双击添加   : 老动作 `按键` → 与改造前逐字段一致 (走 `_WF_LIB_ACTIONS`);
                  `等待` → `{"type":"wait","seconds":1}`; 新 L2 命令 `复制文件` → 通用
                  `{"type":"command","cmd":"复制文件","params":[9 个 ""]}`;
                  分组节点双击不插入任何步骤;
  E  悬停说明   : `tooltip_text` 对带 schema 的命令非空 (desc / 参数签名), 计时器可安全取消。

退出码: 0=通过 (或 SKIP) / 1=有失败。
"""
import os
import sys
import tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

_PASS, _FAIL = [], []
_LOG = []          # 捕获 log1 (降级提示可见性断言)


def check(cond, msg, detail=""):
    (_PASS if cond else _FAIL).append(msg)
    tail = ("  ({})".format(detail) if detail else "")
    print("[{}] {}{}".format("OK  " if cond else "FAIL", msg, tail))


def _degrade_logs():
    return [m for m, _t in _LOG if "模块库已降级" in str(m)]


# 最小色板 (模块库填充只用到 fgt/fgb/ac, 步骤树刷新用到 fgm/fgb)
COLORS = {
    "bg": "#F6F7F9", "bgc": "#FFFFFF", "bd": "#D8DEE6", "ebg": "#FFFFFF",
    "fgb": "#1F2937", "fgt": "#111827", "fgm": "#6B7280", "ac": "#2563EB",
    "flowbg": "#F8FAFC",
}


def _mount(root):
    """挂载承载模块库的那部分 (与 `build()` 内同名控件一一对应, 不建完整主窗)。"""
    import tkinter
    from tkinter import ttk
    import ui.workflow_view as wv

    frame = tkinter.Frame(root)
    entry = tkinter.Entry(frame)
    tree = ttk.Treeview(frame, show="tree", selectmode="browse")
    steps = ttk.Treeview(frame, columns=("type", "detail"), show="tree headings")

    temp_root = tempfile.mkdtemp(prefix="acrpa_wflib_")

    wv.C = dict(COLORS)
    wv._CTX["colors"] = dict(COLORS)
    wv._CTX["sp"] = (lambda v: float(v))
    wv._CTX["log1"] = (lambda msg, tag=None: _LOG.append((msg, tag)))
    wv._CTX["show_toast"] = None
    wv._CTX["app_root"] = temp_root
    wv.FONT_BODY = ("Microsoft YaHei", 9)
    wv.FONT_SMALL = ("Microsoft YaHei", 9)
    wv.FONT_SMALL_BOLD = ("Microsoft YaHei", 9, "bold")
    wv.FONT_TITLE = ("Microsoft YaHei", 10, "bold")

    wv.wf_lib_frame = frame
    wv.wf_lib_search = entry
    wv.wf_lib_tree = tree
    wv.wf_recent_list = None
    wv.wf_tree = steps
    wv._wf_data = {"name": "t", "steps": []}
    wv._wf_render_flowchart = (lambda *a, **k: None)   # 本轮不验流程图渲染
    wv._FW_LIB_MAKER_BY_CMD = None                     # 反向索引按新环境重建
    return wv, tree, temp_root


def _leaves(tree):
    """树里全部命令叶子 (无子节点的行) 的 iid 列表。"""
    out = []
    for gid in tree.get_children():
        for cid in tree.get_children(gid):
            out.append(cid)
    return out


def _leaf_names(tree, wv):
    names = []
    for iid in _leaves(tree):
        names.append(wv._wf_library_action_name(iid))
    return names


def _find_iid(tree, wv, cmd):
    for iid in _leaves(tree):
        if wv._wf_library_action_name(iid) == cmd:
            return iid
    return None


def main():
    try:
        import tkinter
        root = tkinter.Tk()
        root.withdraw()
    except Exception as exc:                       # 无显示/无 Tk → 明确跳过
        print("SKIP: 无法创建 Tk 根窗口, 本环境不跑 GUI 冒烟 ({})".format(exc))
        return 0

    import commands
    wv, tree, temp_root = _mount(root)
    print("模块库数据源适配器: {!r}".format(
        getattr(wv._module_catalog, "__name__", None)))

    try:
        # ── A. 数据源驱动: 11 组 / 88 命令 / 集合一致 ────────────────────
        print("\n── A. 数据源驱动 (module_catalog → 树) ──")
        leaves = wv._wf_populate_library()
        groups = tree.get_children()
        check(len(groups) == 11, "分组节点数 == 11", "实际 {}".format(len(groups)))
        check(leaves == 88 and len(_leaves(tree)) == 88,
              "命令叶子节点数 == 88", "返回 {} / 树内 {}".format(leaves, len(_leaves(tree))))
        names = _leaf_names(tree, wv)
        want = set(commands.list_names())
        check(len(names) == len(set(names)), "命令叶子无重复",
              "重复={}".format(sorted(n for n in set(names) if names.count(n) > 1)))
        check(set(names) == want, "树内命令名集合 == commands.list_names()",
              "缺={} 多={}".format(sorted(want - set(names)), sorted(set(names) - want)))
        no_empty = all(tree.get_children(gid) for gid in groups)
        check(no_empty, "每个分组节点都有命令子节点 (子族恒空 → 不造空节点)")
        # 分组行带 Unicode icon (group_label 口径), 命令名含半角空格的能精确反查
        first_text = tree.item(groups[0], "text")
        check(bool(first_text.split(" ")[0]), "分组行带 icon 前缀",
              "第一组文本={!r}".format(first_text))
        http_iid = _find_iid(tree, wv, "HTTP 请求")
        check(http_iid is not None and wv._wf_library_action_name(http_iid) == "HTTP 请求",
              "带半角空格的命令名 (HTTP 请求) 可精确命中",
              "iid={!r}".format(http_iid))
        check(not _degrade_logs(), "正常路径不产生降级提示",
              "logs={}".format(_degrade_logs()))

        # ── B. 检索 ──────────────────────────────────────────────────────
        print("\n── B. 检索 (module_catalog.search 驱动) ──")
        n_file = wv._wf_populate_library("文件")
        check(0 < n_file <= 88, "搜索 `文件` 后可见命令数 >0 且 <=88", "实际 {}".format(n_file))
        hit_tags = 0
        for cid in _leaves(tree):
            if "wf_lib_hit" in (tree.item(cid, "tags") or ()):
                hit_tags += 1
        check(hit_tags == n_file, "命中行全部打上 wf_lib_hit 标签",
              "{} vs {}".format(hit_tags, n_file))
        n_none = wv._wf_populate_library("zzz不存在zzz")
        check(n_none == 0 and len(_leaves(tree)) == 0,
              "搜索怪词后可见命令数 == 0 (树为空)",
              "返回 {} / 树内 {}".format(n_none, len(_leaves(tree))))
        n_all = wv._wf_populate_library("")
        check(n_all == 88, "清空搜索框恢复 88 条", "实际 {}".format(n_all))

        # ── C. 兜底分支 (注入坏路径, 不改仓库 json) ──────────────────────
        print("\n── C. 兜底分支 (groups_json 不存在) ──")
        wv._WF_LIB_DEGRADED = False
        wv._WF_LIB_STATUS = ""
        del _LOG[:]
        bad = os.path.join(temp_root, "no_such_groups.json")
        try:
            n_fb = wv._wf_populate_library("", groups_json=bad)
            crashed = None
        except Exception as exc:                    # 兜底分支**不得**抛异常
            n_fb, crashed = -1, exc
        check(crashed is None, "兜底路径不抛异常", repr(crashed))
        check(n_fb == 45, "兜底叶子数 == 45 (8 分类硬编码快照)", "实际 {}".format(n_fb))
        check(len(tree.get_children()) == 8, "兜底分组数 == 8",
              "实际 {}".format(len(tree.get_children())))
        logs = _degrade_logs()
        check(len(logs) == 1, "兜底触发 1 条降级提示 (log1)",
              "logs={}".format(logs))
        check(bool(wv._WF_LIB_STATUS) and "模块库已降级" in wv._WF_LIB_STATUS,
              "降级状态字符串可读 (状态行口径)", repr(wv._WF_LIB_STATUS))
        # 第二次不再刷日志 (只提示一次)
        wv._wf_populate_library("", groups_json=bad)
        check(len(_degrade_logs()) == 1, "降级提示只产生一次 (不刷屏)",
              "logs={}".format(_degrade_logs()))
        # 兜底树里双击老动作仍走旧构造器
        fb_iid = _find_iid(tree, wv, "点击坐标")
        check(fb_iid is not None, "兜底树内可反查老动作 `点击坐标`", "iid={!r}".format(fb_iid))
        wv._wf_data["steps"] = []
        tree.selection_set(fb_iid)
        wv._wf_add_from_library()
        check(bool(wv._wf_data["steps"])
              and wv._wf_data["steps"][-1].get("cmd") == "坐标",
              "兜底路径双击 `点击坐标` → cmd 坐标 (旧构造器)",
              "{}".format(wv._wf_data["steps"][-1] if wv._wf_data["steps"] else None))

        # ── D. 双击添加的双路径 ─────────────────────────────────────────
        print("\n── D. 双击添加 (老动作 / 新命令) ──")
        wv._wf_populate_library()                   # 回到正常数据源 (缓存命中)
        wv._wf_data["steps"] = []

        iid_key = _find_iid(tree, wv, "按键")
        check(iid_key is not None, "正常树内可反查 `按键`", "iid={!r}".format(iid_key))
        tree.selection_set(iid_key)
        wv._wf_add_from_library()
        got = wv._wf_data["steps"][-1] if wv._wf_data["steps"] else {}
        want_key = {"type": "command", "cmd": "按键",
                    "params": ["enter", "1", "0.1", "", "", "", "", "", ""]}
        check(got == want_key, "老动作 `按键` 与改造前逐字段一致", "{}".format(got))

        iid_wait = _find_iid(tree, wv, "等待")
        check(iid_wait is not None, "正常树内可反查 `等待`", "iid={!r}".format(iid_wait))
        tree.selection_set(iid_wait)
        wv._wf_add_from_library()
        got = wv._wf_data["steps"][-1]
        check(got == {"type": "wait", "seconds": 1},
              "`等待` 仍走 _WF_LIB_ACTIONS → wait/seconds=1", "{}".format(got))

        iid_copy = _find_iid(tree, wv, "复制文件")
        check(iid_copy is not None, "正常树内可反查新 L2 命令 `复制文件`",
              "iid={!r}".format(iid_copy))
        tree.selection_set(iid_copy)
        wv._wf_add_from_library()
        got = wv._wf_data["steps"][-1]
        check(got.get("type") == "command" and got.get("cmd") == "复制文件"
              and isinstance(got.get("params"), list) and len(got["params"]) == 9
              and all(p == "" for p in got["params"]),
              "新命令 `复制文件` → command 节点 + params 9 槽空串", "{}".format(got))

        n_before = len(wv._wf_data["steps"])
        gid = tree.get_children()[0]
        tree.selection_set(gid)
        wv._wf_add_from_library()
        check(len(wv._wf_data["steps"]) == n_before,
              "双击分组节点不插入步骤", "{} vs {}".format(len(wv._wf_data["steps"]), n_before))

        # 保存/执行层可达性: 通用节点是 command 类型且 cmd 在注册表内
        check(got.get("cmd") in set(commands.list_names()),
              "通用节点 cmd 命中注册表 (执行层可识别)", "cmd={!r}".format(got.get("cmd")))

        # ── E. 悬停说明 ─────────────────────────────────────────────────
        print("\n── E. 悬停说明 (desc + 参数签名) ──")
        item = wv._WF_LIB_NODES.get(iid_copy) or {}
        tip = wv._wf_lib_tooltip_text(item)
        check(bool(tip.strip()), "`复制文件` 悬停说明非空", repr(tip))
        item_key = wv._WF_LIB_NODES.get(iid_key) or {}
        tip_key = wv._wf_lib_tooltip_text(item_key)
        check(bool(tip_key.strip()), "`按键` 悬停说明非空", repr(tip_key))
        wv._wf_lib_on_motion  # 绑定存在于 build(); 此处只验证可安全取消
        wv._wf_lib_tip_cancel()
        check(wv._WF_LIB_TIP_TIMER is None and wv._WF_LIB_TIP_IID is None,
              "悬停计时可安全取消 (无挂起 timer)")
    finally:
        try:
            root.destroy()
        except Exception:
            pass

    print("\n{} 通过 / {} 失败".format(len(_PASS), len(_FAIL)))
    if _FAIL:
        print("失败断言:")
        for msg in _FAIL:
            print("  - {}".format(msg))
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
