# -*- coding: utf-8 -*-
"""命令参数 schema 回归自测 (离线)。

背景: 命令注册表此前只有一句人类可读的参数说明字符串
(`"图片名, 精度(默认0.96)"`), 没有任何机器可读的元数据 —— 于是参数校验、
编辑器补全、文档生成只能各自硬编码或干脆不做。本模块把说明解析成结构化
schema, 并保持 `list_all()` 的四元组形状不变 (20+ 处消费方依赖它)。

断言:
  S1 兼容性     : list_all() 仍是 (name, desc, params, handler) 四元组
  S2 全覆盖     : 每条注册命令都有 schema 条目, 且解析不抛异常
  S3 解析正确性 : 默认值 / 可选项 / 枚举 / 变长 / 数字类型
  S4 校验       : 参数过多、非数字、枚举越界被拦; 空值/None 一律放行
  S5 显式 schema: register(..., schema=[...]) 优先于字符串解析
  S6 签名重建   : signature() 能重建出含默认值与枚举的可读签名

用法: .venv\\Scripts\\python.exe -X utf8 tools\\_test_command_schema.py
退出码: 0=全部通过, 1=存在失败
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

_PASS, _FAIL = [], []


def check(cond, msg):
    (_PASS if cond else _FAIL).append(msg)
    print("[{}] {}".format("OK  " if cond else "FAIL", msg))


def p(names):
    return [x["name"] for x in names]


def main():
    print("=" * 68)
    print("命令参数 schema 自测")
    print("=" * 68)

    # ── S1 兼容性 ──
    print("\n── S1 向后兼容 ──")
    all_cmds = commands.list_all()
    check(all_cmds and all(len(t) == 4 for t in all_cmds),
          "list_all() 仍返回四元组 ({} 条)".format(len(all_cmds)))
    check(commands.list_names() == [t[0] for t in all_cmds],
          "list_names() 与 list_all() 顺序一致")

    # ── S2 全覆盖 ──
    print("\n── S2 全命令覆盖 ──")
    missing = [n for n in commands.list_names() if not isinstance(commands.schema(n), list)]
    check(not missing, "所有命令都有 schema 条目 (缺: {})".format(missing or "无"))
    st = commands.stats()
    check(st["commands"] == len(all_cmds),
          "stats().commands == 注册条数 ({})".format(st["commands"]))
    check(st["no_params"] > 0, "识别出无参数命令 {} 条".format(st["no_params"]))
    check(st["variadic"] >= 1, "识别出变长参数命令 {} 条".format(st["variadic"]))
    check(st["with_choices"] >= 5, "识别出带枚举的命令 {} 条".format(st["with_choices"]))
    print("      stats = {}".format(st))

    # ── S3 解析正确性 ──
    print("\n── S3 解析正确性 ──")
    s = commands.schema("找图")
    check(p(s) == ["图片名", "精度"], "找图 参数名 = {}".format(p(s)))
    check(s[1]["kind"] == "number" and s[1]["default"] == "0.96",
          "精度 识别为 number, 默认 0.96")
    check(s[0]["default"] is None and s[0]["kind"] == "string", "图片名 无默认值/字符串")

    s = commands.schema("按键")
    check(p(s) == ["按键名", "次数", "间隔"], "按键 参数名 = {}".format(p(s)))
    check(s[1]["default"] == "1" and s[2]["default"] == "0.1",
          "按键 默认值 1 / 0.1")
    check(all(x["kind"] == "number" for x in s[1:]), "次数/间隔 均为 number")

    s = commands.schema("写入")
    mode = [x for x in s if x["name"] == "模式"][0]
    check(mode["choices"] == ("auto", "direct", "simulate"),
          "写入 模式枚举 = {}".format(mode["choices"]))

    s = commands.schema("Python")
    perm = [x for x in s if x["name"] == "权限"][0]
    check(perm["choices"] == ("sandbox", "trusted", "full"),
          "Python 权限枚举 = {}".format(perm["choices"]))

    s = commands.schema("浏览器上传")
    check(any(x["variadic"] for x in s), "浏览器上传 识别出变长参数")
    check(commands.max_args("浏览器上传") is None, "变长命令不设参数个数上限")

    check(commands.schema("复制") == [], "『复制』是无参数命令")
    check(commands.signature("复制") == "无参数", "无参数命令签名 = 无参数")

    # ── S4 校验 ──
    print("\n── S4 参数校验 ──")
    e, _ = commands.validate("按键", ["a", "3", "0.5"])
    check(e == [], "合法参数无错误: {}".format(e))
    e, _ = commands.validate("按键", ["a", "abc"])
    check(any("不是数字" in x for x in e), "非数字被拦: {}".format(e))
    e, _ = commands.validate("按键", ["a", "3", "0.5", "多余"])
    check(any("参数过多" in x for x in e), "超限参数被拦: {}".format(e))
    e, _ = commands.validate("写入", ["文本", "0.1", "bogus"])
    check(any("取值应为" in x for x in e), "枚举越界被拦: {}".format(e))
    e, _ = commands.validate("按键", ["a", "None", "None"])
    check(e == [], "None 视为未填写, 放行: {}".format(e))
    e, _ = commands.validate("按键", ["a", "", ""])
    check(e == [], "空字符串视为未填写, 放行: {}".format(e))
    e, _ = commands.validate("按键", ["a", "3", "", "  "])
    check(e == [], "尾部空列不算参数过多: {}".format(e))
    e, _ = commands.validate("复制", ["不该有"])
    check(any("参数过多" in x for x in e), "无参数命令收到参数时报错: {}".format(e))
    e, _ = commands.validate("不存在的命令", ["x"])
    check(e == [], "未知命令不报校验错误 (交给 engine 报未知命令)")

    e, _ = commands.validate("浏览器上传", ["定位", "f1.txt", "f2.txt", "f3.txt", "是"])
    check(e == [], "变长参数多文件不误报: {}".format(e))

    # ── S5 显式 schema ──
    print("\n── S5 显式 schema 优先 ──")
    commands.register("__schema_probe__", "自测用", "a, b", None,
                      schema=[{"name": "only", "kind": "string", "choices": ("x",),
                               "default": None, "optional": False, "variadic": False}])
    sp = commands.schema("__schema_probe__")
    check(len(sp) == 1 and sp[0]["name"] == "only",
          "显式 schema 覆盖字符串解析 (解析结果 {} 项)".format(len(sp)))

    # ── S6 签名重建 ──
    print("\n── S6 签名重建 ──")
    sig = commands.signature("按键")
    check("按键名" in sig and "=1" in sig and "=0.1" in sig,
          "signature(按键) = {}".format(sig))
    sig = commands.signature("写入")
    check("auto/direct/simulate" in sig, "signature(写入) = {}".format(sig))
    check("…" in commands.signature("浏览器上传"),
          "变长参数在签名中以 … 标注: {}".format(commands.signature("浏览器上传")))
    for name in commands.list_names():
        commands.signature(name)
    check(True, "全部命令 signature() 均可重建且不抛异常")

    print("\n" + "=" * 68)
    print("通过 {} / 失败 {}".format(len(_PASS), len(_FAIL)))
    print("结论: {}".format("FAIL" if _FAIL else "PASS"))
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
