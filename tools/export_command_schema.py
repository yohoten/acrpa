# -*- coding: utf-8 -*-
"""导出命令参数 schema 为机器可读契约。

命令注册表 (src/commands.py) 是命令与参数的唯一来源; 本工具把它导出成 JSON,
供脚本编辑器补全、参数表单、插件作者与文档生成器消费 —— 这些消费方此前只能
去解析 `"图片名, 精度(默认0.96)"` 这类人类可读字符串, 或者干脆各自硬编码。

用法:
    python tools/export_command_schema.py                 # 写到 res/help/command_schema.json
    python tools/export_command_schema.py --out x.json    # 指定输出
    python tools/export_command_schema.py --print         # 只打印到 stdout
    python tools/export_command_schema.py --check         # 只校验不写盘 (漂移门禁)
    python tools/export_command_schema.py --check --out x.json   # 校验指定文件
退出码: 0=成功/无漂移, 1=有漂移

注意: `res/help/command_schema.json` 由本生成器产出, **禁止手改** ——
手改会在下一次 `--check` 时被判为漂移 (差异摘要会指出具体命令与字段)。
"""
import argparse
import io
import json
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "src"))

import commands                                     # noqa: E402
import version_info                                 # noqa: E402

DEFAULT_OUT = os.path.join(BASE, "res", "help", "command_schema.json")


def build():
    items = []
    for name, desc, params, handler in commands.list_all():
        items.append({
            "name": name,
            "description": desc,
            "params_text": params,                 # 原始人类可读说明 (保留, 供文档直出)
            "signature": commands.signature(name),  # 归一化签名 (含默认值/枚举)
            "params": commands.schema(name),
            "has_handler": handler is not None,
        })
    return {
        "app_version": version_info.VERSION,
        "source": "src/commands.py::commands.list_all()",
        "stats": commands.stats(),
        "commands": items,
    }


# 版本号随每次发布变动, 不参与漂移判定 (见开发计划 S0-05 卡)
_CHECK_IGNORED_KEYS = ("app_version",)
_DIFF_LIMIT = 40


def _diff(expect, actual, path, out, limit=_DIFF_LIMIT):
    """递归比对两个 JSON 值, 把差异追加到 ``out`` (最多 ``limit`` 条)。"""
    if len(out) >= limit:
        return
    if isinstance(expect, dict) and isinstance(actual, dict):
        for k in expect:
            if k not in actual:
                out.append("{}.{} 缺失 (磁盘无此键)".format(path, k))
            else:
                _diff(expect[k], actual[k], "{}.{}".format(path, k), out, limit)
        for k in actual:
            if k not in expect:
                out.append("{}.{} 多余 (注册表已无此键)".format(path, k))
        return
    if isinstance(expect, list) and isinstance(actual, list):
        if len(expect) != len(actual):
            out.append("{} 长度不同: 期望 {} / 实际 {}".format(path, len(expect), len(actual)))
        for i in range(min(len(expect), len(actual))):
            _diff(expect[i], actual[i], "{}[{}]".format(path, i), out, limit)
        return
    if type(expect) is not type(actual) or expect != actual:
        out.append("{} 期望 {!r} / 实际 {!r}".format(path, expect, actual))


def _diff_commands(expect, actual, out):
    """commands 数组按命令名比对, 让差异摘要直接点名命令。"""
    exp_names = [c.get("name") for c in expect if isinstance(c, dict)]
    act_names = [c.get("name") for c in actual if isinstance(c, dict)]
    miss = [n for n in exp_names if n not in act_names]
    extra = [n for n in act_names if n not in exp_names]
    if miss:
        out.append("commands 缺 {} 条命令: {}".format(len(miss), miss))
    if extra:
        out.append("commands 多 {} 条命令: {}".format(len(extra), extra))
    if not miss and not extra and exp_names != act_names:
        for i, (e, a) in enumerate(zip(exp_names, act_names)):
            if e != a:
                out.append("commands 顺序漂移: 第 {} 项 期望 {} / 实际 {}".format(i, e, a))
                break
    by_name = {}
    for c in actual:
        if isinstance(c, dict):
            by_name[c.get("name")] = c
    for e in expect:
        if not isinstance(e, dict):
            continue
        name = e.get("name")
        a = by_name.get(name)
        if a is not None:
            _diff(e, a, "commands[{}]".format(name), out)


def check(path):
    """只校验不写盘: 重新计算期望内容并与磁盘文件结构化比对。"""
    rel = os.path.relpath(path, BASE) if path.startswith(BASE) else path
    if not os.path.exists(path):
        print("[FAIL] 找不到 {} —— 请先运行 python tools/export_command_schema.py".format(rel))
        return 1
    try:
        with io.open(path, "r", encoding="utf-8") as f:
            actual = json.load(f)
    except Exception as exc:                      # 半写/手改坏掉的 json 也算漂移
        print("[FAIL] {} 不是合法 JSON: {}".format(rel, exc))
        return 1

    # JSON 往返归一把 tuple 变 list (如 params[].choices), 避免类型差异假报漂移
    expect = json.loads(json.dumps(build(), ensure_ascii=False))
    diffs = []
    for key in expect:
        if key in _CHECK_IGNORED_KEYS:
            continue
        if key not in actual:
            diffs.append("顶层键 {} 缺失 (磁盘无此键)".format(key))
        elif key == "commands":
            _diff_commands(expect[key], actual[key], diffs)
        else:
            _diff(expect[key], actual[key], key, diffs)
    for key in actual:
        if key not in expect:
            diffs.append("顶层键 {} 多余 (注册表不产出此键)".format(key))

    if diffs:
        print("[FAIL] {} 已漂移: {} 处差异".format(rel, len(diffs)))
        for d in diffs:
            print("   - " + d)
        print("       修复: python tools/export_command_schema.py")
        return 1

    st = expect["stats"]
    print("[OK] {} 与注册表一致 ({} 条命令, 已校验 stats/commands 全字段)".format(
        rel, st["commands"]))
    print("     stats = {}".format(json.dumps(st, ensure_ascii=False, sort_keys=True)))
    return 0


def main():
    ap = argparse.ArgumentParser(description="导出命令参数 schema")
    ap.add_argument("--out", default=DEFAULT_OUT, help="输出路径 (缺省 {})".format(
        os.path.relpath(DEFAULT_OUT, BASE)))
    ap.add_argument("--print", dest="to_stdout", action="store_true", help="只打印, 不落盘")
    ap.add_argument("--check", action="store_true",
                    help="只校验不写盘: 重新计算并与 --out 指向的文件比对, 有漂移则退 1")
    args = ap.parse_args()

    out = args.out if os.path.isabs(args.out) else os.path.join(BASE, args.out)

    if args.check:
        return check(out)

    data = build()
    text = json.dumps(data, ensure_ascii=False, indent=2)
    if args.to_stdout:
        print(text)
        return 0

    os.makedirs(os.path.dirname(out), exist_ok=True)
    with io.open(out, "w", encoding="utf-8") as f:
        f.write(text + "\n")
    st = data["stats"]
    print("[OK] 已导出 {} 条命令 → {}".format(st["commands"], os.path.relpath(out, BASE)))
    print("     有参数 {} / 无参数 {} / 变长 {} / 含枚举 {}".format(
        st["with_schema"], st["no_params"], st["variadic"], st["with_choices"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
