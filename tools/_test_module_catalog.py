#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""模块库数据源适配器 (src/ui/module_catalog.py) 回归自测 —— 开发计划 §2.1 S0-03 卡。

用法:
    .venv\\Scripts\\python.exe -X utf8 tools\\_test_module_catalog.py

覆盖 (卡内验收断言 1~5 + 用户口径 2~6):
  A  导入无副作用 : import ui.module_catalog 后 sys.modules 无 tkinter / pyautogui / engine;
                    源码文本无 tkinter 导入语句 (零 GUI 依赖, H8)。
  B  树规模       : 11 组 / 88 命令 / 每组 items 非空 / 子族数为 0 (数据层无子族声明)。
  C  注册表交叉   : iter_command_names() 与 commands.list_names() 集合相等、无重复。
  D  参数签名     : params_text / sig 取自 schema (含 `HTTP 请求` 这种带半角空格的命令);
                    无参数条数 == schema stats.no_params。
  E  检索         : `文件` 命中 >0 且命中项含该词; 怪词 0 命中; 空关键字 == 全量;
                    大小写不敏感; 命中自带高亮区间。
  F  失败开放     : 路径不存在 / JSON 损坏 → 不抛异常; 默认退兜底 45 条;
                    fallback=False → 空目录 + 非空原因 (区分"加载失败"与"没有命令");
                    schema 缺失 → 命令仍 88 条 (只是签名退化)。
  G  UI 辅助      : group_label / node_label / tooltip_text / get_command 可用且无控件依赖。

退出码: 0=全部通过 / 1=有失败。
"""
import io
import json
import os
import shutil
import subprocess
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

MODULE_FILE = os.path.join(SRC, "ui", "module_catalog.py")
GROUPS_JSON = os.path.join(ROOT, "res", "help", "command_groups.json")
SCHEMA_JSON = os.path.join(ROOT, "res", "help", "command_schema.json")

_PASS, _FAIL, _WARN = [], [], []


def check(cond, msg, detail=""):
    (_PASS if cond else _FAIL).append(msg)
    tail = ("  ({})".format(detail) if detail else "")
    print("[{}] {}{}".format("OK  " if cond else "FAIL", msg, tail))


def warn(msg):
    _WARN.append(msg)
    print("[WARN] {}".format(msg))


def _read(path):
    with io.open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


# ── A. 导入无副作用 ──────────────────────────────────────────────────
def t_import_side_effect_free():
    print("\n── A. 导入无副作用（零 tkinter / 零 GUI 依赖） ──")
    code = ("import sys; sys.path.insert(0, {src!r}); "
            "import ui.module_catalog as mc; "
            "bad=[m for m in ('tkinter','pyautogui','engine','workflow') if m in sys.modules]; "
            "print('BAD=' + ','.join(bad)); "
            "print('MODFILE=' + mc.__file__)").format(src=SRC)
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        p = subprocess.run([sys.executable, "-X", "utf8", "-c", code],
                           cwd=ROOT, env=env, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=60)
        out = (p.stdout or b"").decode("utf-8", "replace")
        err = (p.stderr or b"").decode("utf-8", "replace")
        rc = p.returncode
    except Exception as exc:                      # 子进程失败也不能炸测试
        rc, out, err = -1, "", str(exc)
    check(rc == 0, "子进程 import ui.module_catalog 正常退出 (rc={})".format(rc),
          err.strip()[-200:])
    bad = ""
    for tok in out.split():
        if tok.startswith("BAD="):
            bad = tok[len("BAD="):]
    check(bad == "", "未间接引入 tkinter / pyautogui / engine / workflow", "bad={!r}".format(bad))

    src_text = _read(MODULE_FILE)
    check("import tkinter" not in src_text and "from tkinter" not in src_text,
          "源码文本无 tkinter 导入语句")
    check("pyautogui" not in src_text, "源码文本无 pyautogui 引用")


# ── B. 树规模 ───────────────────────────────────────────────────────
def t_tree_shape():
    print("\n── B. 三级树规模 ──")
    from ui import module_catalog as mc
    cat = mc.load_catalog()
    check(isinstance(cat, list), "Catalog 是 list 子类（卡契约 list[{group,...}]）")
    check(cat.ok, "json 加载成功 (ok=True)", "reason={!r}".format(cat.reason))
    check(cat.group_count == 11, "分组数 == 11", "实际={}".format(cat.group_count))
    check(cat.command_count == 88, "命令数 == 88", "实际={}".format(cat.command_count))
    check(all(g.get("items") for g in cat), "每个分组的 items 非空")
    check(cat.subfamily_count == 0,
          "子族数 == 0（数据层无子族声明 → 退化两级）", "实际={}".format(cat.subfamily_count))
    keys = ("group", "icon", "tier", "order", "items")
    check(all(all(k in g for k in keys) for g in cat), "每个分组含 group/icon/tier/order/items")
    check(all(g.get("icon") for g in cat), "每个分组 icon 非空")
    check(all(g.get("tier") in mc.VALID_TIERS for g in cat),
          "tier 取值 ⊆ " + str(mc.VALID_TIERS))
    orders = [g.get("order") for g in cat]
    check(orders == sorted(orders), "分组按 order 递增", str(orders))
    items = mc.iter_command_items(cat)
    check(all(("cmd" in it and "desc" in it and "sig" in it and "params" in it) for it in items),
          "每条命令节点含 cmd/desc/sig/params")


# ── C. 与注册表交叉校验 ──────────────────────────────────────────────
def t_registry_crosscheck():
    print("\n── C. 与注册表交叉校验（无丢失 / 无重复） ──")
    import commands
    from ui import module_catalog as mc
    cat = mc.load_catalog()
    names = mc.iter_command_names(cat)
    reg = commands.list_names()
    check(len(names) == 88, "命令节点数 == 88", "实际={}".format(len(names)))
    check(len(set(names)) == len(names), "无重复命令", "去重后={}".format(len(set(names))))
    miss = sorted(set(reg) - set(names))
    extra = sorted(set(names) - set(reg))
    check(not miss and not extra, "与 commands.list_names() 集合相等",
          "缺={} 多={}".format(miss, extra))
    pairs = mc.iter_commands(cat)
    check(len(pairs) == 88 and all(isinstance(p, tuple) and len(p) == 2 for p in pairs),
          "iter_commands() 返回 88 个 (group, cmd)")
    check(cat.missing_schema == [], "所有命令都能在 schema 里找到", str(cat.missing_schema))


# ── D. 参数签名 ─────────────────────────────────────────────────────
def t_params_signature():
    print("\n── D. 参数签名（取自 schema，非重新解析） ──")
    from ui import module_catalog as mc
    cat = mc.load_catalog()
    with io.open(SCHEMA_JSON, "r", encoding="utf-8") as f:
        schema = json.load(f)
    by_name = {c["name"]: c for c in schema["commands"]}
    for name in ("HTTP 请求", "JSON 取值", "按键"):
        item = mc.get_command(name, cat)
        ok = bool(item) and bool(item.get("params_text")) and item["params_text"] == \
            by_name[name]["params_text"]
        check(ok, "{} 的 params_text 与 schema 逐字一致".format(name),
              repr(item.get("params_text") if item else None)[:60])
    no_params = sum(1 for it in mc.iter_command_items(cat) if not it.get("params"))
    check(no_params == schema["stats"]["no_params"],
          "无参数命令条数 == stats.no_params（{}）".format(schema["stats"]["no_params"]),
          "实际={}".format(no_params))
    sig_bad = [it["cmd"] for it in mc.iter_command_items(cat)
               if it.get("has_schema") and it["sig"] != by_name[it["cmd"]]["signature"]]
    check(not sig_bad, "全部 sig 与 schema signature 一致", str(sig_bad[:5]))


# ── E. 检索 ─────────────────────────────────────────────────────────
def t_search():
    print("\n── E. 检索（命令名 / 参数 / 分组名，大小写不敏感） ──")
    from ui import module_catalog as mc
    cat = mc.load_catalog()
    hits = mc.search("文件", cat)
    check(len(hits) > 0, "关键字『文件』有命中", "命中={}".format(len(hits)))
    bad = [h.cmd for h in hits
           if "文件" not in (h.cmd + h.params_text + h.group + h.sub).lower()]
    check(not bad, "命中项的命令名/参数/分组名都含该词", str(bad[:5]))
    check(all(h.matched for h in hits), "命中项 matched=True")
    check(all(isinstance(h.start, int) and isinstance(h.end, int) and h.end > h.start
              for h in hits), "命中项带高亮区间 start/end")
    check(all(h.snippet[max(0, h.start):h.end].lower() == "文件" for h in hits),
          "高亮区间切出的文本恰为关键字")
    check(len(mc.search("zzz不存在zzz", cat)) == 0, "怪词 0 命中")
    all_hits = mc.search("", cat)
    check(len(all_hits) == 88, "空关键字返回全量 88", "实际={}".format(len(all_hits)))
    check(not all_hits[0].matched, "空关键字条目标记为未命中(matched=False)")
    lower = [h.cmd for h in mc.search("ai", cat)]
    check("AI找图" in lower, "大小写不敏感: 'ai' 命中 AI找图", str(lower[:5]))
    grp = [h.cmd for h in mc.search("文件与系统", cat)]
    check(len(grp) == 15, "分组名命中整组 15 条", "实际={}".format(len(grp)))


# ── F. 失败开放 ─────────────────────────────────────────────────────
def t_fail_open():
    print("\n── F. 失败开放（json 缺失 / 损坏 / schema 缺失） ──")
    from ui import module_catalog as mc
    tmpdir = tempfile.mkdtemp(prefix="acrpa_cat_")
    missing = os.path.join(tmpdir, "no_such", "command_groups.json")
    try:
        # 1. 路径不存在 + 默认兜底 → 45 条分类动作, 不抛
        try:
            cat = mc.load_catalog(groups_json=missing)
            raised = ""
        except Exception as exc:
            cat, raised = None, repr(exc)
        check(not raised, "路径不存在时不抛异常", raised)
        check(cat is not None and cat.command_count == 45,
              "路径不存在 → 兜底 45 条分类动作",
              "实际={}".format(cat.command_count if cat is not None else "N/A"))
        check(cat is not None and cat.source == "fallback" and not cat.ok and cat.reason,
              "兜底状态 source=fallback / ok=False / reason 非空",
              repr(cat.reason if cat is not None else ""))

        # 2. 路径不存在 + fallback=False → 空目录 + 原因，且可区分"没有命令"
        empty = mc.load_catalog(groups_json=missing, fallback=False)
        check(len(empty) == 0 and empty.group_count == 0 and empty.command_count == 0,
              "fallback=False → 空目录", "len={}".format(len(empty)))
        check(bool(empty.reason) and not empty.ok,
              "空目录带可读原因（可区分加载失败 / 确实没有命令）", repr(empty.reason)[:60])

        # 3. JSON 损坏 ('{') → 同上
        broken = os.path.join(tmpdir, "broken_groups.json")
        with io.open(broken, "w", encoding="utf-8") as f:
            f.write("{")
        bad_cat = mc.load_catalog(groups_json=broken)
        check(bad_cat.command_count == 45 and not bad_cat.ok,
              "JSON 损坏 → 兜底 45 条且 ok=False", "实际={}".format(bad_cat.command_count))
        bad_empty = mc.load_catalog(groups_json=broken, fallback=False)
        check(len(bad_empty) == 0 and bool(bad_empty.reason),
              "JSON 损坏 + fallback=False → 空目录 + 原因", repr(bad_empty.reason)[:60])

        # 4. 结构非法（groups 不是数组）→ 同样 fail-open
        badshape = os.path.join(tmpdir, "bad_shape.json")
        with io.open(badshape, "w", encoding="utf-8") as f:
            json.dump({"groups": "oops"}, f)
        shape_cat = mc.load_catalog(groups_json=badshape)
        check(shape_cat.command_count == 45 and not shape_cat.ok,
              "结构非法 → 兜底 45 条且 ok=False", "实际={}".format(shape_cat.command_count))

        # 5. 合法但空目录（ok=True 且 0 条）→ 与"加载失败"可区分
        emptydoc = os.path.join(tmpdir, "empty_groups.json")
        with io.open(emptydoc, "w", encoding="utf-8") as f:
            json.dump({"groups": []}, f)
        zerocat = mc.load_catalog(groups_json=emptydoc, schema_json=SCHEMA_JSON)
        check(zerocat.command_count == 0 and zerocat.ok and zerocat.reason == "",
              "合法空目录: ok=True + reason='' + 0 条（区别于加载失败）",
              "ok={} reason={!r}".format(zerocat.ok, zerocat.reason))

        # 6. schema 缺失 → 命令照出 88 条（只退化签名）
        noschema = mc.load_catalog(schema_json=missing)
        check(noschema.command_count == 88 and not noschema.ok and noschema.reason,
              "schema 缺失 → 仍出 88 条命令 + 原因",
              "count={} reason={!r}".format(noschema.command_count, noschema.reason[:40]))
        check(all(it["sig"] == "" and it["params"] == []
                  for it in mc.iter_command_items(noschema)),
              "schema 缺失时签名/参数退化为空（不抛异常）")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ── G. UI 辅助（纯字符串） ───────────────────────────────────────────
def t_ui_helpers():
    print("\n── G. UI 辅助（纯字符串，不建控件） ──")
    from ui import module_catalog as mc
    cat = mc.load_catalog()
    grp = cat[0]
    check(mc.group_label(grp).startswith(grp["icon"]),
          "group_label 带图标前缀", repr(mc.group_label(grp)))
    item = mc.get_command("找图", cat)
    check(item is not None and mc.node_label(item).startswith("找图"),
          "node_label 以命令名开头", repr(mc.node_label(item) if item else None))
    tip = mc.tooltip_text(item) if item else ""
    check("参数:" in tip and item["desc"] in tip,
          "tooltip_text 含 description + 参数签名", repr(tip)[:80])
    nop = mc.get_command("复制", cat)
    check(nop is not None and mc.node_label(nop) == "复制",
          "无参数命令的 node_label 不拼签名", repr(mc.node_label(nop) if nop else None))
    check(mc.get_command("不存在的命令", cat) is None, "get_command 未命中返回 None")


def main():
    print("=" * 70)
    print("module_catalog 回归自测 (开发计划 S0-03)")
    print("模块: {}".format(MODULE_FILE))
    print("=" * 70)
    for fn in (t_import_side_effect_free, t_tree_shape, t_registry_crosscheck,
               t_params_signature, t_search, t_fail_open, t_ui_helpers):
        try:
            fn()
        except Exception as exc:                # 单段异常不掩盖其它结论
            check(False, "{} 执行异常".format(fn.__name__), repr(exc))
    print("\n" + "=" * 70)
    print("通过 {} / 失败 {} / 警告 {}".format(len(_PASS), len(_FAIL), len(_WARN)))
    if _FAIL:
        for m in _FAIL:
            print("  [FAIL] {}".format(m))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
