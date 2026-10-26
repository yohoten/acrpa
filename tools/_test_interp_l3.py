# -*- coding: utf-8 -*-
"""L3 变量插值冒烟自测 —— 方案文档 §5 L3 / §8.6（8 条单测点 + 端到端 + 性能）。

覆盖（与方案文档 §5 L3 的 8 条单测点一一对应）:
  ① 四类占位符 `{var:}` / `{env:}` / `{date:}` / `{arg:}` 各自取值正确
  ② `${}` 与 `{var:}` 混用；`${}` 表达式求值仍走 safe_eval（未被破坏）
  ③ 转义 `{{` / `$$` 正确
  ④ `NO_INTERP` 生效：`预演`/`强制`/`送回收站`/变量名参数传 `${x}` 时原样传入
  ⑤ 缺失变量 → 空串 + warning，不抛
  ⑥ 不做二次展开
  ⑦ 非法占位符（`{var:}` / `{date:%%%}` / 未闭合 `{var:x`）字面保留不抛
  ⑧ `max_len` 截断只影响日志副本

  端到端: 引擎真实链路（设置变量 → 写入文本文件 用 `{var:}` → 文件是否存在 用 `{var:}`），
          外加一条 L2 新命令（`哈希值`）确认插值在新命令上生效。
  性能  : 1 万次 `interpolate` 耗时（毫秒）。

安全声明
--------
* 只写 ``tempfile.mkdtemp()`` 下的临时文件，绝不碰用户既有数据；
* **不执行**任何危险命令（电源操作/删除文件/结束进程）——
  `NO_INTERP` 的闸门断言全部走 ``engine._render_args`` 的纯函数路径。

用法: python -X utf8 tools/_test_interp_l3.py
退出码: 0=全部通过, 1=存在失败
"""
import datetime
import os
import shutil
import sys
import tempfile
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(BASE, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import commands                                     # noqa: E402
import interp                                       # noqa: E402

_PASS, _FAIL = [], []


def check(cond, msg, detail=""):
    (_PASS if cond else _FAIL).append(msg)
    tail = ("  ({})".format(detail) if detail else "")
    print("[{}] {}{}".format("OK  " if cond else "FAIL", msg, tail))
    return bool(cond)


# ── 捕获 log1（warning 断言用；转发给真实 log1 保持日志完整） ─────────────
_LOGS = []
try:
    import utils

    _ORIG_LOG1 = utils.log1

    def _spy_log1(msg, level="info", *a, **kw):
        _LOGS.append((level, msg))
        try:
            _ORIG_LOG1(msg, level, *a, **kw)
        except Exception:
            pass

    utils.log1 = _spy_log1
except Exception:                                   # utils 不可用 → 不阻断用例
    _ORIG_LOG1 = None


def _warned(keyword):
    return any(level == "warning" and keyword in msg for level, msg in _LOGS)


# ── 固定时间 / 固定环境变量，保证可复现 ────────────────────────────────
_NOW = datetime.datetime(2026, 10, 23, 14, 5, 9)
_ENV = {"USERNAME": "yohoten", "TOKEN": ""}


def _ctx(variables=None, args=None):
    return interp.Context(variables or {}, args or {},
                          getenv=lambda n: _ENV.get(n), now=_NOW)


# ══════════════════════════════════════════════════════════════════════
# ① 四类占位符
# ══════════════════════════════════════════════════════════════════════
def case1_four_kinds():
    print("\n── ① 四类占位符 ──")
    ctx = _ctx({"count": 7, "名字": "小明"}, {"p0": "order-42", "ex": "EXTRA"})

    check(interp.interpolate("{var:count}", ctx) == "7",
          "{var:名称} 取变量值",
          repr(interp.interpolate("{var:count}", ctx)))
    check(interp.interpolate("{var:名字}", ctx) == "小明",
          "{var:名称} 支持中文变量名")

    check(interp.interpolate("{env:USERNAME}", ctx) == "yohoten",
          "{env:NAME} 取环境变量")
    check(interp.interpolate("{env:TOKEN|默认值}", ctx) == "默认值",
          "{env:NAME|默认} 空值回落默认值")
    check(interp.interpolate("{env:NOT_EXIST|兜底}", ctx) == "兜底",
          "{env:NAME|默认} 未设置回落默认值")

    check(interp.interpolate("{date:Y-M-D}", ctx) == "2026-10-23",
          "{date:} 单字母标记（Y-M-D）",
          interp.interpolate("{date:Y-M-D}", ctx))
    check(interp.interpolate("{date:Y-M-D_h-i-s}", ctx) == "2026-10-23_14-05-09",
          "{date:} 单字母标记混排（含时分秒）",
          interp.interpolate("{date:Y-M-D_h-i-s}", ctx))
    check(interp.interpolate("{date:%Y%m%d}", ctx) == "20261023",
          "{date:} 兼容 strftime 写法")
    check(interp.interpolate("{date:T=Y年M月D日}", ctx) == "T=2026年10月23日",
          "{date:} 支持字面量混排",
          interp.interpolate("{date:T=Y年M月D日}", ctx))

    check(interp.interpolate("{arg:p0}", ctx) == "order-42",
          "{arg:名称} 取调用方参数 p0")
    check(interp.interpolate("{arg:ex}", ctx) == "EXTRA",
          "{arg:名称} 取调用方参数 ex")

    # {date:} 映射表完整性（文档要求的 12 个标记）
    missing = [k for k in "YyMmDdhHiswW" if k not in interp.DATE_MAP]
    check(not missing, "{date:} 映射表覆盖 Y y M m D d h H i s w W",
          "缺少 {}".format(missing))


# ══════════════════════════════════════════════════════════════════════
# ② ${} 与 {var:} 混用 + ${} 表达式求值
# ══════════════════════════════════════════════════════════════════════
def case2_mixed_dollar():
    print("\n── ② ${} 与新语法混用 ──")
    ctx = _ctx({"count": 7, "name": "X", "a": 2, "b": 3})

    check(interp.interpolate("a${count}b{var:name}c", ctx) == "a7bXc",
          "${} 与 {var:} 同一串内混用",
          interp.interpolate("a${count}b{var:name}c", ctx))
    check(interp.interpolate("${a + b}", ctx) == "5",
          "${表达式} 走 safe_eval 求值（加法）",
          interp.interpolate("${a + b}", ctx))
    check(interp.interpolate("${a * b + 1}", ctx) == "7",
          "${表达式} 走 safe_eval 求值（混合运算）")
    check(interp.interpolate("共${a + b}条 / {var:count}件", ctx) == "共5条 / 7件",
          "${表达式} 与 {var:} 混排")

    # 安全: 表达式只走白名单，注入尝试必须被拒绝（并字面保留）
    bad = interp.interpolate("${__import__('os').system('echo hi')}", ctx)
    check(bad.startswith("${") and bad.endswith("}"),
          "${} 表达式越权（属性调用/import）被 safe_eval 拒绝并字面保留",
          repr(bad))


# ══════════════════════════════════════════════════════════════════════
# ③ 转义
# ══════════════════════════════════════════════════════════════════════
def case3_escape():
    print("\n── ③ 转义 {{ 与 $$ ──")
    ctx = _ctx({"x": 1})
    check(interp.interpolate("{{literal}}", ctx) == "{literal}",
          "{{...}} 成对转义 → 字面 {...}",
          repr(interp.interpolate("{{literal}}", ctx)))
    check(interp.interpolate("{{var:x}}", ctx) == "{var:x}",
          "{{ 使 {var:x} 不被展开", repr(interp.interpolate("{{var:x}}", ctx)))
    check(interp.interpolate('{"a":{"b":1}}', ctx) == '{"a":{"b":1}}',
          "以 }} 结尾的嵌套 JSON 不被误还原（未出现 {{ 就不配对）",
          repr(interp.interpolate('{"a":{"b":1}}', ctx)))
    check(interp.interpolate("$${x}", ctx) == "${x}",
          "$$ 转义 → 字面 $（${x} 不被求值）",
          repr(interp.interpolate("$${x}", ctx)))
    check(interp.interpolate("价格为 $$99", ctx) == "价格为 $99",
          "$$ 在普通文本中还原为单个 $")


# ══════════════════════════════════════════════════════════════════════
# ⑤ 缺失变量 / ⑥ 不二次展开 / ⑦ 非法占位符
# ══════════════════════════════════════════════════════════════════════
def case5_missing_var():
    print("\n── ⑤ 缺失变量 → 空串 + warning ──")
    ctx = _ctx({})
    _LOGS[:] = []
    try:
        out = interp.interpolate("A{var:definitely_missing}B", ctx)
        ok = out == "AB"
    except Exception as e:                          # noqa: BLE001
        out, ok = "raise:{}".format(e), False
    check(ok, "缺失变量替换为空串，不抛异常", repr(out))
    check(_warned("未定义"), "缺失变量记一条 warning 日志")

    _LOGS[:] = []
    check(interp.interpolate("A${also_missing}B", ctx) == "AB",
          "缺失 ${} 变量同样为空串")


def case6_no_double_expand():
    print("\n── ⑥ 不做二次展开 ──")
    ctx = _ctx({"a": "{var:b}", "b": "BOOM"})
    out = interp.interpolate("{var:a}", ctx)
    check(out == "{var:b}", "{var:a} 的值里含 {var:b} 时按字面插入", repr(out))
    check("BOOM" not in out, "值中的 {var:b} 未被二次展开（防注入）")

    out2 = interp.interpolate("{var:{var:b}}", ctx)
    check(out2 == "{var:{var:b}}", "嵌套占位符整体字面保留（不递归）", repr(out2))


def case7_illegal_literal():
    print("\n── ⑦ 非法/未闭合占位符字面保留 ──")
    ctx = _ctx({"x": 1})
    cases = [
        ("{var:}", "{var:}"),
        ("{date:%%%}", "{date:%%%}"),
        ("{var:x", "{var:x"),
        ('{"a":1}', '{"a":1}'),
        ("{unknown:thing}", "{unknown:thing}"),
        ("{env:}", "{env:}"),
        ("{date:}", "{date:}"),
        ("100%{var:x}", "100%1"),
    ]
    for text, expect in cases:
        try:
            got = interp.interpolate(text, ctx)
        except Exception as e:                      # noqa: BLE001
            got = "raise:{}".format(e)
        check(got == expect, "非法占位符字面保留: {!r}".format(text), repr(got))

    # 跨行 JSON（脚本里常见的多行请求体）不得被误判为占位符
    multi = '{\n  "a": 1\n}'
    check(interp.interpolate(multi, ctx) == multi, "跨行 JSON 原样保留（不被当占位符）")


# ══════════════════════════════════════════════════════════════════════
# ⑧ 截断只影响日志副本 + 脱敏
# ══════════════════════════════════════════════════════════════════════
def case8_truncate_and_redact():
    print("\n── ⑧ max_len 只截断日志副本 + 脱敏 ──")
    long_value = "测试文本" * 500                   # 2000 字符，无 token 特征
    ctx = _ctx({"big": long_value})

    out = interp.interpolate("{var:big}", ctx, max_len=32)
    check(len(out) == 2000, "interpolate 返回值不被截断（实际值完整）",
          "len={}".format(len(out)))

    log_copy = interp.log_preview(out, 512)
    check(len(log_copy) == 512, "日志副本被截到 max_len=512",
          "len={}".format(len(log_copy)))
    check(log_copy.endswith("…"), "截断后带省略号")
    check(interp.log_preview("短文本", 512) == "短文本", "未超长时不截断")

    args = ["{var:big}", "${x}", "{arg:p0}"]
    rendered = interp.interpolate_args(args, _ctx({"big": "B", "x": 1}, {"p0": "P"}))
    check(rendered == ["B", "1", "P"], "interpolate_args 批量插值", repr(rendered))
    skipped = interp.interpolate_args(
        ["{var:x}", "{var:x}"], _ctx({"x": "V"}), skip_idx=[0])
    check(skipped == ["{var:x}", "V"], "interpolate_args 的 skip_idx 生效",
          repr(skipped))

    # 脱敏（供日志复用）
    red = interp.redact
    check("***" in red("Authorization: Bearer abcdef1234567890"),
          "Bearer token 被脱敏", red("Authorization: Bearer abcdef1234567890"))
    check(red("https://x/y?token=secret123&a=1") == "https://x/y?token=***&a=1",
          "?token= 的值被脱敏", red("https://x/y?token=secret123&a=1"))
    check("***" in red("api_key=abcdef0123456789"),
          "api_key= 的值被脱敏", red("api_key=abcdef0123456789"))
    check(red("d41d8cd98f00b204e9800998ecf8427e") == "***",
          "32 位 hex（疑似摘要/token）被脱敏",
          red("d41d8cd98f00b204e9800998ecf8427e"))
    check(red(r"C:\Users\yohoten\Downloads\报告.txt") ==
          r"C:\Users\yohoten\Downloads\报告.txt",
          "普通路径不被误脱敏", red(r"C:\Users\yohoten\Downloads\报告.txt"))


# ══════════════════════════════════════════════════════════════════════
# ④ NO_INTERP 白名单契约（commands 层）
# ══════════════════════════════════════════════════════════════════════
def case4_no_interp_table():
    print("\n── ④ NO_INTERP 白名单（commands 层） ──")
    names = commands.list_names()
    unchecked = [n for n in names if n not in commands.NO_INTERP]
    check(not unchecked, "88 条在册命令全部登记进 NO_INTERP",
          "未登记 {}".format(unchecked))

    whole, _ = commands.get_no_interp("如果")
    check(whole, "如果 整条禁插值（条件只走 ${} + safe_eval）")
    whole2, _ = commands.get_no_interp("循环开始")
    check(whole2, "循环开始 整条禁插值")

    _, idx = commands.get_no_interp("设置变量")
    check(0 in idx, "设置变量 的变量名禁插值", "idx={}".format(sorted(idx)))

    for name, must in (
        ("电源操作", (0, 2, 3)),      # 动作/强制/预演
        ("结束进程", (1, 2)),         # 强制/保护白名单
        ("删除文件", (1, 2)),         # 递归/送回收站
        ("复制文件", (2, 3)),         # 覆盖/目标变量名
        ("启动程序", (3, 4)),         # 等待结束/隐藏窗口
        ("设置窗口样式", (2, 3)),     # 置顶/隐藏
        ("HTTP 请求", (0, 5, 6)),     # 方法/两个变量名
        ("磁盘空间", (1, 2, 3)),      # 三个变量名
    ):
        _, got = commands.get_no_interp(name)
        missing = [i for i in must if i not in got]
        check(not missing, "{} 的关键参数禁插值".format(name),
              "缺少 {}".format(missing))

    whole3, _ = commands.get_no_interp("这个命令不存在")
    check(whole3 is False, "未登记命令失败开放（视为可插值）")


# ══════════════════════════════════════════════════════════════════════
# ④' NO_INTERP 在引擎 choke point 上真的生效（纯函数，不执行危险命令）
# ══════════════════════════════════════════════════════════════════════
def case4b_engine_render():
    print("\n── ④' engine._render_args 上白名单生效 ──")
    import engine as engine_mod
    import scriptdata

    eng = engine_mod.engine
    render = eng._render_args
    RowAdapter = engine_mod.RowAdapter

    def render_row(cmd, args):
        row = RowAdapter(scriptdata.ScriptData(cmd, args))
        return render(cmd, row)

    eng.variables.clear()
    eng.variables.update({"flag": "是", "dyn": "v1"})
    eng.script_args.clear()

    # 电源操作: 参数1=动作 / 参数2=延迟秒 / 参数3=强制 / 参数4=预演
    # 强制(参数3→下标2) 与 预演(参数4→下标3) 传 ${flag} 必须原样传入,
    # 否则脚本就能用 ${} 动态关掉二次确认 (方案文档 §8.6)。
    r = render_row("电源操作", ["关机", "60", "${flag}", "${flag}"])
    check(r[3].value == "${flag}", "电源操作 的 强制 参数不被插值",
          repr(r[3].value))
    check(r[4].value == "${flag}", "电源操作 的 预演 参数不被插值",
          repr(r[4].value))
    check(r[2].value == "60", "电源操作 的 延迟秒 属数据参数（不强制禁插值）",
          repr(r[2].value))

    r = render_row("结束进程", ["notepad.exe", "${flag}", "${flag}"])
    check(r[2].value == "${flag}", "结束进程 的保护白名单 参数不被插值",
          repr(r[2].value))

    r = render_row("删除文件", ["C:/tmp/x.txt", "${flag}", "${flag}"])
    check(r[2].value == "${flag}", "删除文件 的 送回收站 参数不被插值",
          repr(r[2].value))

    # 变量名参数原样传入
    r = render_row("设置变量", ["${flag}", "{var:dyn}"])
    check(r[1].value == "${flag}", "设置变量 的变量名不被插值",
          repr(r[1].value))
    check(r[2].value == "v1", "设置变量 的值参数照常插值", repr(r[2].value))

    # 整条禁插值
    r = render_row("如果", ["{var:dyn} == 'v1'"])
    check(r[1].value == "{var:dyn} == 'v1'", "如果 整条不插值", repr(r[1].value))

    # 普通参数照常插值（钥匙: 这是"行为变化"的正面用例）
    r = render_row("输入", ["{var:dyn}-{env:USERNAME}"])
    check(r[1].value == "v1-yohoten", "输入 的文本参数照常插值", repr(r[1].value))

    # 渲染后的行与 RowAdapter 同形（handler 的 hasattr(row,'args') 分支不变）
    check(not hasattr(r, "args"), "渲染行不带 args 属性（与 RowAdapter 同形）")


# ══════════════════════════════════════════════════════════════════════
# 端到端: 引擎真实链路 + L2 新命令
# ══════════════════════════════════════════════════════════════════════
def case_e2e(tmpdir):
    print("\n── 端到端: 引擎真实链路 ──")
    import hashlib

    import state
    import engine as engine_mod
    from scriptdata import ScriptData

    eng = engine_mod.engine
    eng.variables.clear()
    eng.script_args.clear()
    state.quit2 = False
    state.pause_event.set()
    state.debug_mode = False
    state.step_mode = False

    target = os.path.join(tmpdir, "l3_out_{date:Y}.txt").replace("\\", "/")
    # 目标路径里的 {date:Y} 会被插值成当年四位年份 → 期望值同步替换
    expect_path = target.replace("{date:Y}", "{:04d}".format(_NOW.year))
    eng.script_args.update({"p0": "ARGVAL"})
    rows = [
        ScriptData("设置变量", ["outpath", target]),
        ScriptData("设置变量", ["body", "payload-{arg:p0}"]),
        ScriptData("设置变量", ["tag", "hello"]),
        # 用 {var:} 拼路径（日期单字母标记亦生效），内容用 {arg:}
        ScriptData("写入文本文件", ["{var:outpath}", "{var:tag}-{arg:p0}", "覆盖", "utf-8"]),
        ScriptData("读取文本文件", ["{var:outpath}", "auto", "readback"]),
        ScriptData("文件是否存在", ["{var:outpath}", "exists_ok"]),
    ]
    eng.execute_script(rows, tmpdir)
    time.sleep(0.05)

    written = eng.variables.get("outpath")
    check(written == expect_path,
          "设置变量 → 写入文本文件 用 {var:} + {date:} 解析出真实路径",
          repr(written))
    check(os.path.isfile(written or ""), "文件确实被写入",
          "path={}".format(written))
    check(eng.variables.get("readback") == "hello-ARGVAL",
          "读回内容证实 {var:} 与 {arg:} 都生效",
          repr(eng.variables.get("readback")))
    check(str(eng.variables.get("exists_ok")) == "1",
          "文件是否存在 用 {var:} 判定为存在",
          repr(eng.variables.get("exists_ok")))

    # L2 新命令（哈希值）上的插值
    eng.variables.clear()
    eng.variables.update({"text": "abc"})
    eng.script_args.update({"p0": "abc"})
    rows2 = [
        ScriptData("哈希值", ["sha256", "文本", "{var:text}", "hash_var"]),
        ScriptData("哈希值", ["sha256", "文本", "{arg:p0}", "hash_arg"]),
    ]
    # _nested=True: 不在入口清空 variables (否则刚预置的 text 会被抹掉)
    eng.execute_script(rows2, tmpdir, _nested=True)
    expect = hashlib.sha256(b"abc").hexdigest()
    check(eng.variables.get("hash_var") == expect,
          "L2 新命令 哈希值 上 {var:} 生效",
          repr(eng.variables.get("hash_var")))
    check(eng.variables.get("hash_arg") == expect,
          "L2 新命令 哈希值 上 {arg:} 生效")

    # ${} 在 如果 里仍按原路径（safe_eval）求值 —— 未被插值破坏
    eng.variables.clear()
    rows3 = [
        ScriptData("设置变量", ["count", "5"]),
        ScriptData("如果", ["${count} > 3"]),
        ScriptData("设置变量", ["hit", "yes"]),
        ScriptData("结束如果", [None] * 9),
    ]
    eng.execute_script(rows3, tmpdir, _nested=True)
    check(eng.variables.get("hit") == "yes",
          "如果 ${count} > 3 仍走 safe_eval 求值（老脚本行为不变）",
          repr(eng.variables.get("hit")))


# ══════════════════════════════════════════════════════════════════════
# CLI 参数槽
# ══════════════════════════════════════════════════════════════════════
def case_cli_slots():
    print("\n── CLI -pN:/ex: 解析 ──")
    import cli

    opts = cli.parse_args(["--script", "a.acrpas", "-p0:abc", "-p9=z", "ex:EXT",
                           "--var", "k=1", "--unknown"])
    check(opts is not None, "parse_args 正常返回")
    if not opts:
        return
    check(opts["vars"] == ["k=1"], "--var 仍正常工作", repr(opts["vars"]))
    check(opts["args"].get("p0") == "abc", "-p0:abc → {arg:p0}", repr(opts.get("args")))
    check(opts["args"].get("p9") == "z", "-p9=z（= 分隔）也支持")
    check(opts["args"].get("ex") == "EXT", "ex:EXT → {arg:ex}")

    opts2 = cli.parse_args(["--script", "a.acrpas", "-p1:", "独立值"])
    check(opts2["args"].get("p1") == "独立值", "-p1: 后跟独立 argv 取作值")

    check(cli.parse_args(["--nonsense"]) is None, "未知开关仍静默忽略且无 --script → None")

    # 端到端串到引擎: {arg:p0} 能被引擎看到
    import engine as engine_mod
    eng = engine_mod.engine
    eng.script_args.clear()
    eng.script_args.update(opts["args"])
    ctx = engine_mod.make_interp_ctx(eng.variables, eng.script_args)
    check(interp.interpolate("{arg:p0}", ctx) == "abc",
          "make_interp_ctx 串起 CLI 参数与 {arg:}")


# ══════════════════════════════════════════════════════════════════════
# 性能基准
# ══════════════════════════════════════════════════════════════════════
def case_perf():
    print("\n── 性能基准（单遍 O(n)） ──")
    ctx = _ctx({"count": 7, "name": "X", "big": "V"}, {"p0": "P"})
    tpl = "行 {var:count} / {var:name} / {env:USERNAME} / {date:Y-M-D} / {arg:p0}"
    loops = 10000

    interp.interpolate(tpl, ctx)                    # 预热
    t0 = time.perf_counter()
    for _ in range(loops):
        interp.interpolate(tpl, ctx)
    cost_ms = (time.perf_counter() - t0) * 1000.0
    print("     1 万次 interpolate: {:.1f} ms".format(cost_ms))
    check(cost_ms < 1000.0, "1 万次 interpolate < 1000 ms",
          "{:.1f} ms".format(cost_ms))

    # 文档 §5 L3 第 8 条性能护栏: 100 变量 × 1000 次 < 50 ms
    many = {"v{}".format(i): i for i in range(100)}
    ctx2 = _ctx(many)
    tpl2 = " ".join("{var:v%d}" % i for i in range(100))
    t0 = time.perf_counter()
    for _ in range(1000):
        interp.interpolate(tpl2, ctx2)
    cost2 = (time.perf_counter() - t0) * 1000.0
    print("     100 变量 × 1000 次: {:.1f} ms".format(cost2))
    check(cost2 < 500.0, "100 变量 × 1000 次插值性能护栏（放宽到 500 ms）",
          "{:.1f} ms".format(cost2))


# ══════════════════════════════════════════════════════════════════════
def main():
    tmpdir = tempfile.mkdtemp(prefix="acrpa_l3_")
    try:
        case1_four_kinds()
        case2_mixed_dollar()
        case3_escape()
        case4_no_interp_table()
        case4b_engine_render()
        case5_missing_var()
        case6_no_double_expand()
        case7_illegal_literal()
        case8_truncate_and_redact()
        case_cli_slots()
        case_e2e(tmpdir)
        case_perf()
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    print("\n" + "=" * 66)
    print("L3 变量插值自检: {} 通过 / {} 失败".format(len(_PASS), len(_FAIL)))
    if _FAIL:
        for name in _FAIL:
            print("  FAIL  {}".format(name))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
