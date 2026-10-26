# -*- coding: utf-8 -*-
"""L2 动作库扩充冒烟自测 —— 20 条 P0 新命令（方案文档 §5 L2）。

覆盖断言（全部**无 GUI 依赖**，窗口样式一节在无 Tk 时跳过并说明）：
  A 注册表契约 : 命令总数 88（68 + 20）；既有 68 条顺序/名称未被改动；
                 20 条新命令均有已绑定的 handler、可解析 schema、非空 requires
  B 文件族     : 写入/读取/复制/移动(改名)/列目录/建目录/存在性 一圈闭环
  C 回收站删除 : 临时文件送回收站后原路径消失；永久删除在无确认界面时被拒
  D 进程       : 结束自建进程成功；explorer.exe / svchost.exe 被白名单硬拒绝
  E HTTP       : 本地 http.server 做 GET，断言状态码与响应体写入变量
  F JSON/编码  : 路径取值（含数组下标/缺键默认值）、Base64/URL/哈希 往返正确
  G 电源       : **只做 dry-run 与参数校验**，绝不真实关机/重启/注销/休眠
  H 音量/磁盘  : 返回值合法（0-100、磁盘数值 > 0）
  I 窗口样式   : 自建 Tk 窗口断言置顶/透明度成功（无 GUI 环境则 SKIP）

用法: python -X utf8 tools/_test_commands_l2.py
退出码: 0=全部通过, 1=存在失败

安全声明
--------
* 只操作 ``tempfile.mkdtemp()`` 下的临时目录，绝不碰用户既有数据；
* 「结束进程」只结束本用例自建的 ``ping.exe`` 子进程；
* 「电源操作」只跑 ``预演=是`` 分支，真实执行分支仅断言"无确认界面 → 拒绝"。
"""
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(BASE, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import commands                                     # noqa: E402
import file_ops                                     # noqa: E402
import net_ops                                      # noqa: E402
import sys_ops                                      # noqa: E402

_PASS, _FAIL, _SKIP = [], [], []

L2_COMMANDS = [
    "复制文件", "移动文件", "删除文件", "创建目录", "列出目录",
    "读取文本文件", "写入文本文件", "文件是否存在", "启动程序", "结束进程",
    "HTTP 请求", "JSON 取值", "Base64 编解码", "URL 编解码", "哈希值",
    "打开系统面板", "电源操作", "音量控制", "磁盘空间", "设置窗口样式",
]


def check(cond, msg, detail=""):
    (_PASS if cond else _FAIL).append(msg)
    tail = ("  ({})".format(detail) if detail else "")
    print("[{}] {}{}".format("OK  " if cond else "FAIL", msg, tail))
    return bool(cond)


def skip(msg):
    _SKIP.append(msg)
    print("[SKIP] " + msg)


# ── 命令执行脚手架（模拟 engine 的 三参 handler 调用约定） ─────────────
class _Cell(object):
    __slots__ = ("value",)

    def __init__(self, value):
        self.value = value


class _Row(object):
    """等价于 engine.RowAdapter：row[i].value，越界返回空 Cell。"""

    def __init__(self, values):
        self.cells = [_Cell(v) for v in values]

    def __getitem__(self, index):
        if index < len(self.cells):
            return self.cells[index]
        return _Cell(None)


class _Api(object):
    """等价于 acrpa_api.AcrpaAPI 的变量读写面。"""

    def __init__(self):
        self.variables = {}

    def get_var(self, name):
        return self.variables.get(str(name))

    def set_var(self, name, value):
        self.variables[str(name)] = value
        return True


_TMP = ""


def run(cmd, *args):
    """→ (ok, 变量 dict, 参数原始列表)。"""
    handler = commands.get_handler(cmd)
    if handler is None:
        return None, {}, list(args)
    api = _Api()
    row = _Row([cmd] + list(args))
    ok = handler(row, _TMP, api)
    return ok, api.variables, list(args)


# ── A. 注册表契约 ────────────────────────────────────────────────────
def t_registry():
    print("\n── A. 注册表契约 ──")
    names = commands.list_names()
    check(len(names) == 88, "命令总数 = 88 (实际 {})".format(len(names)))
    check(len(set(names)) == len(names), "命令名无重复")
    check(names[0] == "找图" and names[67] == "启动浏览器录制",
          "既有 68 条顺序未变（首=找图 / 第68=启动浏览器录制）",
          "{} / {}".format(names[0], names[67]))
    check(names[68:] == L2_COMMANDS,
          "新 20 条按文档顺序追加在末尾", str(names[68:]))
    for name in L2_COMMANDS:
        handler = commands.get_handler(name)
        schema = commands.schema(name)
        req = commands.requires(name)
        check(callable(handler) and isinstance(schema, list) and bool(req),
              "{}: handler/schema/requires 齐全".format(name),
              "params={} requires={}".format(len(schema), req))


# ── B. 文件族闭环 ────────────────────────────────────────────────────
def t_file_family():
    print("\n── B. 文件族闭环 ──")
    work = os.path.join(_TMP, "files")
    ok, _v, _a = run("创建目录", work, "是")
    check(ok is not False and os.path.isdir(work), "创建目录")

    src = os.path.join(work, "a.txt")
    ok, _v, _a = run("写入文本文件", src, "hello-世界", "覆盖", "utf-8")
    check(ok is not False and os.path.isfile(src), "写入文本文件(覆盖)")

    ok, vars_, _a = run("读取文本文件", src, "utf-8", "txt")
    check(ok is not False and vars_.get("txt") == "hello-世界",
          "读取文本文件 → 变量", repr(vars_.get("txt")))

    ok, _v, _a = run("写入文本文件", src, "-tail", "追加", "utf-8")
    ok2, vars_, _a = run("读取文本文件", src, "auto", "txt2")
    check(ok is not False and ok2 is not False and
          str(vars_.get("txt2", "")).endswith("hello-世界-tail"),
          "写入文本文件(追加，auto 编码读取)", repr(vars_.get("txt2")))

    dst = os.path.join(work, "b.txt")
    ok, vars_, _a = run("复制文件", src, dst, "是", "copied")
    check(ok is not False and os.path.isfile(dst) and
          vars_.get("copied") == dst, "复制文件 → 目标路径写变量")

    ok, _v, _a = run("复制文件", src, dst, "否")
    check(ok is False, "复制文件(覆盖=否) 对已存在目标报错")

    renamed = os.path.join(work, "c.txt")
    ok, _v, _a = run("移动文件", dst, renamed, "是")
    check(ok is not False and os.path.isfile(renamed) and not os.path.exists(dst),
          "移动文件(同目录改名)")

    ok, vars_, _a = run("列出目录", work, "*.txt", "名", "names")
    listed = str(vars_.get("names", ""))
    check(ok is not False and "a.txt" in listed and "c.txt" in listed,
          "列出目录 → 换行名称串", repr(listed))

    ok, vars_, _a = run("文件是否存在", renamed, "ex")
    check(ok is not False and vars_.get("ex") == "1", "文件是否存在 → 1")
    ok, vars_, _a = run("文件是否存在", os.path.join(work, "nope.txt"), "ex")
    check(ok is not False and vars_.get("ex") == "0", "文件是否存在 → 0")

    # 相对路径按 script_dir 解析
    ok, vars_, _a = run("文件是否存在", "files/a.txt", "rel")
    check(ok is not False and vars_.get("rel") == "1",
          "相对路径按脚本目录解析", repr(vars_.get("rel")))

    # 空路径必须失败（禁止危险默认值）
    for cmd in ("复制文件", "移动文件", "删除文件", "创建目录", "列出目录",
                "读取文本文件", "写入文本文件", "文件是否存在"):
        ok, _v, _a = run(cmd, "", "")
        check(ok is False, "{} 空路径被拒".format(cmd))


# ── C. 回收站删除 ────────────────────────────────────────────────────
def t_recycle_delete():
    print("\n── C. 回收站删除 ──")
    victim = os.path.join(_TMP, "recycle_me.txt")
    with open(victim, "w", encoding="utf-8") as fh:
        fh.write("delete me")
    ok, _v, _a = run("删除文件", victim, "否", "是")
    check(ok is not False, "送回收站删除成功", "")
    check(not os.path.exists(victim), "回收站删除后原路径已不存在")

    # 永久删除：无确认界面时必须拒绝（安全闸门）
    keep = os.path.join(_TMP, "perm.txt")
    with open(keep, "w", encoding="utf-8") as fh:
        fh.write("x")
    ok, _v, _a = run("删除文件", keep, "否", "否")
    check(ok is False and os.path.isfile(keep),
          "永久删除在无确认界面时被拒绝（文件仍在）")

    # 通配符必须被拒（禁止 os.remove 走通配）
    ok, _v, _a = run("删除文件", os.path.join(_TMP, "*.txt"), "否", "是")
    check(ok is False, "删除命令拒绝通配符")


# ── D. 结束进程 ──────────────────────────────────────────────────────
def t_kill_process():
    print("\n── D. 结束进程 ──")
    ok, msg, n = sys_ops.kill_process("explorer.exe", force=True)
    check(ok is False and "白名单" in msg, "explorer.exe 被硬白名单拒绝", msg)
    ok, msg, n = sys_ops.kill_process("svchost.exe", force=True)
    check(ok is False and "白名单" in msg, "svchost.exe 被硬白名单拒绝", msg)
    ok, msg, n = sys_ops.kill_process("acrpa.exe", force=True)
    check(ok is False, "拒绝结束本程序自身", msg)
    ok, msg, n = sys_ops.kill_process("no_such_proc_xyz.exe", force=True)
    check(ok is False and "未找到" in msg, "不存在的进程报未找到", msg)

    # 自建子进程 → 结束（只处理本用例起的 ping.exe）
    proc = subprocess.Popen(["ping.exe", "-n", "30", "127.0.0.1"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(0.8)
        ok, msg, n = sys_ops.kill_process("ping.exe", force=True)
        check(ok is True and n >= 1, "结束自建 ping.exe 成功", msg)
    finally:
        try:
            if proc.poll() is None:
                proc.kill()
        except Exception:
            pass

    # 无「强制」且无 GUI 时，走二次确认闸门 → 拒绝
    ok, msg, n = sys_ops.kill_process("ping.exe", force=False, confirm=None)
    check(ok is False, "非强制且无确认界面时拒绝执行", msg)


# ── E. HTTP 请求 ─────────────────────────────────────────────────────
_BODY = "ACRPA-L2-<html>你好</html>"


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):                       # noqa: N802 — BaseHTTPRequestHandler 约定
        data = _BODY.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


def t_http():
    print("\n── E. HTTP 请求 ──")
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    port = srv.server_address[1]
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        url = "http://127.0.0.1:{}/".format(port)
        ok, vars_, _a = run("HTTP 请求", "GET", url, "", "", "10", "body", "status")
        check(ok is not False, "本地 GET 请求成功")
        check(str(vars_.get("status")) == "200",
              "状态码写入变量", repr(vars_.get("status")))
        check(vars_.get("body") == _BODY,
              "响应体写入变量", repr(vars_.get("body"))[:40])

        # 请求头多行 k:v
        ok, vars_, _a = run("HTTP 请求", "GET", url, "X-Test: 1\nAccept: */*",
                            "", "10", "body2", "status2")
        check(ok is not False and str(vars_.get("status2")) == "200",
              "自定义请求头可解析")

        # 保存到文件
        out = os.path.join(_TMP, "resp.txt")
        ok, vars_, _a = run("HTTP 请求", "GET", url, "", "", "10", "b3", "s3", out)
        check(ok is not False and os.path.isfile(out), "响应保存到文件")

        # 安全: 非法协议 / 非回环 http / 非法方法 / 超时钳制
        ok, msg, detail = net_ops.http_request("GET", "file:///C:/Windows/win.ini")
        check(ok is False and "http" in msg, "拒绝 file:// 协议", msg)
        ok, msg, detail = net_ops.http_request("GET", "http://example.com/")
        check(ok is False and "https" in msg, "默认拒绝非回环 http://", msg)
        ok, msg, detail = net_ops.http_request("OPTIONS", url)
        check(ok is False and "方法" in msg, "非法方法被拒", msg)
        check(net_ops.MAX_BODY > 0 and net_ops.MAX_TIMEOUT <= 120,
              "响应体上限 {} 字节 / 超时上限 {} 秒".format(
                  net_ops.MAX_BODY, net_ops.MAX_TIMEOUT))
    finally:
        srv.shutdown()
        srv.server_close()


# ── F. JSON / 编码 ───────────────────────────────────────────────────
def t_json_and_codec():
    print("\n── F. JSON 取值 与 编码工具 ──")
    payload = '{"data": {"items": [{"name": "第一"}, {"name": "第二"}]}, "n": 3}'
    ok, vars_, _a = run("JSON 取值", payload, "data.items[0].name", "无", "got")
    check(ok is not False and vars_.get("got") == "第一",
          "数组下标路径取值", repr(vars_.get("got")))
    ok, vars_, _a = run("JSON 取值", payload, "data.items[1].name", "无", "got2")
    check(ok is not False and vars_.get("got2") == "第二", "第二项取值")
    ok, vars_, _a = run("JSON 取值", payload, "data.nothing", "默认值", "dflt")
    check(vars_.get("dflt") == "默认值", "缺键返回默认值", repr(vars_.get("dflt")))
    ok, vars_, _a = run("JSON 取值", "{不是JSON", "a", "fallback", "bad")
    check(vars_.get("bad") == "fallback", "非法 JSON 返回默认值而非抛异常")

    # 变量作为源
    _api = _Api()
    _api.variables["jv"] = '{"k": "v"}'
    row = _Row(["JSON 取值", "jv", "k", "", "from_var"])
    commands.get_handler("JSON 取值")(row, _TMP, _api)
    check(_api.variables.get("from_var") == "v", "源为脚本变量时可直接取值")

    # Base64 往返
    ok, vars_, _a = run("Base64 编解码", "编码", "中文abc", "b64")
    encoded = vars_.get("b64")
    check(ok is not False and encoded == "5Lit5paHYWJj", "Base64 编码", encoded)
    ok, vars_, _a = run("Base64 编解码", "解码", encoded, "plain")
    check(vars_.get("plain") == "中文abc", "Base64 解码往返", repr(vars_.get("plain")))

    # URL 往返
    ok, vars_, _a = run("URL 编解码", "编码", "a b&c=中文", "u")
    check(vars_.get("u") == "a%20b%26c%3D%E4%B8%AD%E6%96%87", "URL 编码", vars_.get("u"))
    ok, vars_, _a = run("URL 编解码", "解码", vars_.get("u"), "u2")
    check(vars_.get("u2") == "a b&c=中文", "URL 解码往返", repr(vars_.get("u2")))

    # 哈希
    ok, vars_, _a = run("哈希值", "md5", "文本", "abc", "h")
    check(vars_.get("h") == "900150983cd24fb0d6963f7d28e17f72", "MD5(abc)", vars_.get("h"))
    ok, vars_, _a = run("哈希值", "sha1", "文本", "abc", "h1")
    check(vars_.get("h1") == "a9993e364706816aba3e25717850c26c9cd0d89d", "SHA1(abc)")
    ok, vars_, _a = run("哈希值", "sha256", "文本", "abc", "h2")
    check(vars_.get("h2") ==
          "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
          "SHA256(abc)")
    file_path = os.path.join(_TMP, "hash_me.txt")
    with open(file_path, "w", encoding="utf-8", newline="") as fh:
        fh.write("abc")
    ok, vars_, _a = run("哈希值", "sha256", "文件", file_path, "h3")
    # 注意: 每次 run() 都是独立的变量空间, 故与常量比对而不是与上一次的变量比对
    check(vars_.get("h3") ==
          "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
          "文件哈希与文本哈希一致", str(vars_.get("h3")))
    ok, vars_, _a = run("哈希值", "md5", "文件", os.path.join(_TMP, "no.txt"), "h4")
    check(ok is False, "文件不存在时报错（不静默返回空摘要）")


# ── G. 电源（只跑 dry-run / 参数校验） ───────────────────────────────
def t_power_dry_run():
    print("\n── G. 电源操作（仅预演与参数校验） ──")
    for act in sys_ops.POWER_ACTIONS:
        ok, msg, detail = sys_ops.power_action(act, 60, False, True)
        check(ok is True and "预演" in msg and "未真正执行" in msg,
              "{}: dry-run 只预演".format(act), msg)
    ok, msg, detail = sys_ops.power_action("关机", 60, False, False, confirm=None)
    check(ok is False and "二次确认" in msg,
          "真实执行在无确认界面时被拒绝（未关机）", msg)
    ok, msg, detail = sys_ops.power_action("炸掉机房", 10, False, True)
    check(ok is False and "不支持" in msg, "非法动作被拒", msg)
    ok, msg, detail = sys_ops.power_action("关机", 999999, False, True)
    check(ok is True and detail.get("delay") == 86400,
          "延迟秒数被钳制到 86400", str(detail.get("delay")))
    # 命令层同样只走预演
    ok, _v, _a = run("电源操作", "关机", "30", "否", "是")
    check(ok is not False, "命令层 预演=是 通过")


# ── H. 音量 / 磁盘 ───────────────────────────────────────────────────
def t_volume_disk():
    print("\n── H. 音量控制 与 磁盘空间 ──")
    ok, msg, level = sys_ops.get_volume()
    if ok:
        check(isinstance(level, int) and 0 <= level <= 100,
              "当前音量在 0-100 范围内", "{}%".format(level))
        ok2, vars_, _a = run("音量控制", "设置", str(level))
        check(ok2 is not False and 0 <= (vars_.get("volume_level") or 0) <= 100,
              "音量控制(设置) 返回合法值", str(vars_.get("volume_level")))
    else:
        skip("音量读取不可用（无音频设备/非 Windows）: {}".format(msg))

    ok, msg, detail = sys_ops.disk_space("C:")
    check(ok is True and isinstance(detail, dict) and detail["total"] > 0
          and detail["free"] > 0, "磁盘空间 C: 数值合法", msg)
    ok, vars_, _a = run("磁盘空间", "C:", "total", "free", "pct")
    check(ok is not False and (vars_.get("total") or 0) > 0
          and 0 <= float(vars_.get("pct") or 0) <= 100,
          "磁盘空间命令写入三个变量",
          "total={} pct={}".format(vars_.get("total"), vars_.get("pct")))
    ok, msg, detail = sys_ops.disk_space("")
    check(ok is False, "空盘符被拒（禁止危险默认值）", msg)


# ── I. 窗口样式（无 Tk 则跳过） ──────────────────────────────────────
def t_window_style():
    print("\n── I. 设置窗口样式 ──")
    try:
        import tkinter as tk
        root = tk.Tk()
    except Exception as e:                 # noqa: BLE001
        skip("无 GUI 环境，跳过窗口样式断言: {}".format(e))
        return
    title = "ACRPA_L2_TEST_WIN"
    try:
        root.title(title)
        root.geometry("240x120+120+120")
        root.update()
        root.update_idletasks()
        ok, msg, hwnd = sys_ops.window_style(title, 200, "是", None)
        check(ok is True and hwnd, "置顶 + 透明度设置成功", msg)
        ok, msg, hwnd = sys_ops.window_style(title, None, "否", "显示")
        check(ok is True, "取消置顶 + 显示", msg)
        ok, msg, hwnd = sys_ops.window_style("__no_such_window__", 200, None, None)
        check(ok is False, "不存在的窗口标题返回失败", msg)
        ok, _v, _a = run("设置窗口样式", title, "180", "是", "")
        check(ok is not False, "命令层 设置窗口样式 通过")
    finally:
        try:
            root.destroy()
        except Exception:
            pass


# ── 其它纯函数级安全断言 ─────────────────────────────────────────────
def t_misc_safety():
    print("\n── J. 其它安全断言 ──")
    ok, msg, value = sys_ops.open_panel("rm -rf /")
    check(ok is False and "不支持" in msg, "系统面板拒绝任意命令串", msg)
    targets = [v[1] for v in sys_ops.PANELS.values()]
    check(all(not t.startswith(("cmd", "powershell")) for t in targets),
          "系统面板白名单内无可执行 shell", str(sorted(targets)[:3]))
    check("explorer.exe" in sys_ops.HARD_PROTECT
          and "svchost.exe" in sys_ops.HARD_PROTECT
          and "lsass.exe" in sys_ops.HARD_PROTECT,
          "硬白名单含 explorer/svchost/lsass")
    ok, msg, value = net_ops.hash_source("sha999", "文本", "x")
    check(ok is False, "不支持的哈希算法被拒", msg)
    ok, msg, value = net_ops.b64("中文不是base64", "解码")
    check(ok is False and "解码" in msg, "非法 Base64 返回明确错误而不是抛异常", msg)
    # 音量控制用非法动作验证参数校验（**不能用空参数**：空动作默认「设置」，
    # 会真的把系统音量改掉）
    ok, _v, _a = run("音量控制", "乱来", "10")
    check(ok is False, "音量控制非法动作被拒")
    # 命令层：空参数一律失败
    for cmd in ("启动程序", "结束进程", "HTTP 请求", "打开系统面板", "电源操作",
                "磁盘空间", "设置窗口样式"):
        ok, _v, _a = run(cmd, "")
        check(ok is False, "{} 空参数被拒".format(cmd))


def main():
    global _TMP
    print("=" * 68)
    print("L2 动作库扩充冒烟自测（20 条新命令）")
    print("=" * 68)
    _TMP = tempfile.mkdtemp(prefix="acrpa_l2_")
    print("临时目录: {}".format(_TMP))
    try:
        t_registry()
        t_file_family()
        t_recycle_delete()
        t_kill_process()
        t_http()
        t_json_and_codec()
        t_power_dry_run()
        t_volume_disk()
        t_window_style()
        t_misc_safety()
    finally:
        try:
            shutil.rmtree(_TMP, ignore_errors=True)
        except Exception:
            pass

    print()
    print("=" * 68)
    print("L2 冒烟: 通过 {} 项, 失败 {} 项, 跳过 {} 项".format(
        len(_PASS), len(_FAIL), len(_SKIP)))
    if _SKIP:
        for msg in _SKIP:
            print("  SKIP {}".format(msg))
    print("=" * 68)
    if _FAIL:
        print("失败明细:")
        for msg in _FAIL:
            print("  - {}".format(msg))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
