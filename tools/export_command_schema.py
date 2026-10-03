# -*- coding: utf-8 -*-
"""导出命令参数 schema 为机器可读契约。

命令注册表 (src/commands.py) 是命令与参数的唯一来源; 本工具把它导出成 JSON,
供脚本编辑器补全、参数表单、插件作者与文档生成器消费 —— 这些消费方此前只能
去解析 `"图片名, 精度(默认0.96)"` 这类人类可读字符串, 或者干脆各自硬编码。

用法:
    python tools/export_command_schema.py                 # 写到 res/help/command_schema.json
    python tools/export_command_schema.py --out x.json    # 指定输出
    python tools/export_command_schema.py --print         # 只打印到 stdout
退出码: 0=成功
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


def main():
    ap = argparse.ArgumentParser(description="导出命令参数 schema")
    ap.add_argument("--out", default=DEFAULT_OUT, help="输出路径 (缺省 {})".format(
        os.path.relpath(DEFAULT_OUT, BASE)))
    ap.add_argument("--print", dest="to_stdout", action="store_true", help="只打印, 不落盘")
    args = ap.parse_args()

    data = build()
    text = json.dumps(data, ensure_ascii=False, indent=2)
    if args.to_stdout:
        print(text)
        return 0

    out = args.out if os.path.isabs(args.out) else os.path.join(BASE, args.out)
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
