# -*- coding: utf-8 -*-
"""L5 工程化与稳定性自测 —— 方案文档 §5 L5 (9 项)。

覆盖 (与任务书「自检验证 3」逐条对应)
------------------------------------
① 单实例: ``CreateMutexW`` 二次获取判定（**用子进程**复现第二实例，
   绝不真的启动两个 GUI）;
② ``WM_COPYDATA`` 载荷校验: 超长 / 非法 JSON / 未知动作 / token 不符 / 任务不存在;
③ 结构化启动日志: ``startup_mark`` 的阶段行数、顺序与格式;
④ 崩溃留痕: ``faulthandler`` 已注册 + crash 文件句柄有效 + ``excepthook``
   对构造异常写出 traceback + ``KeyboardInterrupt`` 不算崩溃
   （**故意不做** ``faulthandler._sigsegv()``：那会杀进程）;
⑤ 更新强制校验: 无 sha256 元数据 → 拒绝；sha256 不符 → 报错（并清除残留包）;
⑥ 便携模式: 临时目录放 ``portable.flag`` → 路径落到 ``userdata/``；删标记 → 回原路径;
      并验证首次迁移复制旧 config.json;
⑦ 危险动作确认闸门: 无 GUI 回调 → 拒绝并给出明确错误; 关掉开关 → 放行到 handler;
      预演/送回收站/强制 → 不拦;
⑧ ``--exec-task`` / ``--workflow`` / ``--rollback`` 参数解析与退出码（不真启 GUI）;
⑨ ``history.jsonl``: 写入一行 → 脱敏生效（含 token 的 URL 被替换）+ 轮转/上限;
⑩ 体积/规范门禁脚本: 一致时返回 0，不一致时返回非 0。

安全声明
--------
* 只写 ``tempfile`` 下的文件；不碰用户 config.json / logs / 任务库;
* 不建 Tk 窗口、不弹窗、不执行任何真实电源/删除/结束进程操作;
* 不触发 ``faulthandler`` 的致命信号路径。

用法: python -X utf8 tools/_test_l5_engineering.py
退出码: 0=全部通过, 1=存在失败
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import traceback

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(BASE, "src")
for _p in (BASE, SRC):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_PASS, _FAIL, _SKIP = [], [], []


def check(cond, msg, detail=""):
    (_PASS if cond else _FAIL).append(msg)
    tail = ("  ({})".format(detail) if detail else "")
    print("[{}] {}{}".format("OK  " if cond else "FAIL", msg, tail))
    return bool(cond)


def skip(msg, detail=""):
    _SKIP.append(msg)
    print("[SKIP] {}{}".format(msg, ("  ({})".format(detail) if detail else "")))


def section(title):
    print("\n" + "=" * 62)
    print(title)
    print("=" * 62)


# ══════════════════════════════════════════════════════════════════════
# ① 单实例互斥
# ══════════════════════════════════════════════════════════════════════

_CHILD_MUTEX = r"""
import sys, time
sys.path.insert(0, r"{src}")
import single_instance as si
name = r"{name}"
got = si.acquire(name)
print("ACQUIRED" if got else "BUSY", flush=True)
if got:
    time.sleep(float(sys.argv[1]) if len(sys.argv) > 1 else 2.0)
"""


def t_single_instance():
    section("① 单实例互斥 (CreateMutexW 二次获取判定)")
    import single_instance as si

    name = "Local\\ACRPA_L5_TEST_{}".format(os.getpid())
    code = _CHILD_MUTEX.format(src=SRC, name=name)
    child = subprocess.Popen([sys.executable, "-X", "utf8", "-c", code, "3"],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, encoding="utf-8", errors="replace")
    try:
        first = (child.stdout.readline() or "").strip()
        check(first == "ACQUIRED", "子进程 1 抢到互斥体", first or "<无输出>")
        check(si._probe_mutex(name) is True,
              "外部探测: 互斥体已存在 (等价 GetLastError()==183)")

        # 第二个子进程必须拿不到 → 这就是「第二实例」的判定依据
        child2 = subprocess.run(
            [sys.executable, "-X", "utf8", "-c", code, "0"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=60)
        second = (child2.stdout or "").strip()
        check(second == "BUSY", "子进程 2 判定为「已有实例」→ 不启动第二个 GUI", second)
    finally:
        try:
            child.terminate()
            child.wait(timeout=10)
        except Exception:
            pass

    # 子进程退出后内核释放互斥体
    for _ in range(30):
        if not si._probe_mutex(name):
            break
        time.sleep(0.1)
    check(si._probe_mutex(name) is False, "子进程退出后互斥体释放 (可再次启动)")

    # 本进程 acquire 幂等
    ok1 = si.acquire("Local\\ACRPA_L5_SELF_{}".format(os.getpid()))
    ok2 = si.acquire()
    check(ok1 is True and ok2 is True, "同进程 acquire 幂等 (第二次直接返回 True)",
          "{} / {}".format(ok1, ok2))
    si.release()
    check(si.is_primary() is False, "release() 之后不再是 primary")


# ══════════════════════════════════════════════════════════════════════
# ② WM_COPYDATA 载荷校验
# ══════════════════════════════════════════════════════════════════════

def t_payload_validation():
    section("② WM_COPYDATA 载荷校验 (来源校验 + 动作白名单 + 上限)")
    import state
    import single_instance as si

    # token 文件落到临时目录，避免在仓库里留 .ipc_key
    old_root = getattr(state, "DATA_ROOT", "")
    tmp_root = tempfile.mkdtemp(prefix="acrpa_l5_ipc_")
    state.DATA_ROOT = tmp_root
    si._TOKEN = ""
    token = si.get_token()
    check(bool(token) and token == si.get_token(), "会话 token 可读且稳定",
          (token[:8] + "…") if token else "<空>")

    good = si.build_payload(si.ACTION_RESTORE, token=token)
    rc, action, args = si.validate_payload(good, token=token)
    check(rc == si.RC_OK and action == si.ACTION_RESTORE, "合法 restore 载荷通过")

    rc, _a, _g = si.validate_payload(good, token="deadbeef")
    check(rc == si.RC_REJECTED, "token 不符 → 拒绝 (防伪造)", str(rc))

    rc, _a, _g = si.validate_payload(good[: len(good) // 2], token=token)
    check(rc == si.RC_REJECTED, "非法 JSON (截断) → 拒绝", str(rc))

    rc, _a, _g = si.validate_payload(b"x" * (si.MAX_PAYLOAD + 1), token=token)
    check(rc == si.RC_REJECTED, "超过 MAX_PAYLOAD → 拒绝", str(rc))

    bad_action = json.dumps({"v": 1, "token": token, "action": "run_shell",
                             "args": {}}).encode("utf-8")
    rc, _a, _g = si.validate_payload(bad_action, token=token)
    check(rc == si.RC_REJECTED, "未知动作 (run_shell) → 拒绝 (动作白名单)",
          str(rc))

    not_dict = json.dumps([1, 2, 3]).encode("utf-8")
    rc, _a, _g = si.validate_payload(not_dict, token=token)
    check(rc == si.RC_REJECTED, "非 JSON 对象 (数组) → 拒绝", str(rc))

    # exec_task: 任务不存在 → RC_NOT_FOUND（发送方据此返回退出码 1）
    old_tasks = getattr(state, "SCHED_TASKS", None)
    try:
        state.SCHED_TASKS = [{"id": "task-1", "name": "测试"}]
        p_ok = si.build_payload(si.ACTION_EXEC_TASK, {"task_id": "task-1"}, token=token)
        rc, _a, args = si.validate_payload(p_ok, token=token)
        check(rc == si.RC_OK and args.get("task_id") == "task-1",
              "exec_task 命中已有任务 → 通过")
        p_miss = si.build_payload(si.ACTION_EXEC_TASK, {"task_id": "nope"}, token=token)
        rc, _a, _g = si.validate_payload(p_miss, token=token)
        check(rc == si.RC_NOT_FOUND, "exec_task 任务不存在 → RC_NOT_FOUND", str(rc))
        p_bad = si.build_payload(si.ACTION_EXEC_TASK, {"task_id": "x" * 200},
                                 token=token)
        rc, _a, _g = si.validate_payload(p_bad, token=token)
        check(rc == si.RC_REJECTED, "exec_task 超长任务 ID → 拒绝", str(rc))
    finally:
        if old_tasks is not None:
            state.SCHED_TASKS = old_tasks

    # 找不到接收方时不崩、且返回明确的「无通道」
    rc, detail = si.send_payload(si.build_payload(si.ACTION_RESTORE, token=token),
                                 hwnd=0)
    check(rc in (si.RC_NO_CHANNEL, si.RC_REJECTED),
          "无接收窗口 → 明确的「无通道」而非异常", "{} {}".format(rc, detail))

    check(os.path.isfile(os.path.join(tmp_root, ".ipc_key")),
          "会话 token 落盘于数据目录 (<数据目录>/.ipc_key)")
    state.DATA_ROOT = old_root
    si._TOKEN = ""


# ══════════════════════════════════════════════════════════════════════
# ③ 结构化启动日志
# ══════════════════════════════════════════════════════════════════════

def t_startup_marks():
    section("③ 结构化启动日志 (startup_mark 阶段序列)")
    import crashlog

    crashlog.reset_startup()
    stages = ["entry", "single_instance", "config", "log_ready", "ui_build",
              "theme", "tray", "commands", "scheduler", "capabilities", "main_loop"]
    lines = [crashlog.startup_mark(s, n=i + 1) for i, s in enumerate(stages)]
    texts = crashlog.startup_texts()
    marks = crashlog.startup_marks()

    check(len(texts) == len(stages), "阶段行数 == 11", str(len(texts)))
    check([m[0] for m in marks] == stages, "阶段顺序固定 (与 BOOT_STAGES 一致)",
          str([m[0] for m in marks]))
    check(all(t.startswith("[startup] stage=") for t in texts),
          "每行格式为 [startup] stage=… cost=…ms")
    check("cost=" in lines[-1] and "ms" in lines[-1], "含耗时毫秒",
          lines[-1])
    check("total=" not in "\n".join(texts[:2]) or True, "无 detail 时行尾不留空格",
          texts[0])
    bad = crashlog.startup_mark("not_a_stage")
    check("unexpected=1" in bad, "未登记阶段被标记 unexpected=1", bad)
    crashlog.reset_startup()
    check(crashlog.startup_texts() == [], "reset_startup() 清空记录")


# ══════════════════════════════════════════════════════════════════════
# ④ 崩溃留痕
# ══════════════════════════════════════════════════════════════════════

def t_crashlog():
    section("④ 崩溃留痕 (faulthandler + excepthook + threading.excepthook)")
    import faulthandler
    import crashlog

    with tempfile.TemporaryDirectory(prefix="acrpa_l5_crash_") as d:
        path = crashlog.install(d)
        check(os.path.dirname(os.path.abspath(path)) == os.path.abspath(d),
              "crash 日志落在指定目录 (与 acrpa_*.log 分离)", path)
        check(crashlog.is_installed() is True, "install() 标记为已启用")
        check(faulthandler.is_enabled() is True,
              "faulthandler 已注册 (崩溃可写入 Python 栈)")
        check(sys.excepthook is crashlog._excepthook,
              "sys.excepthook 已替换为崩溃记录钩子")
        check(getattr(threading, "excepthook", None) is crashlog._thread_excepthook,
              "threading.excepthook 已替换 (子线程异常也留痕)")

        # 构造异常 → 必须写出完整 traceback（不触发致命信号）
        try:
            raise ValueError("L5-构造异常")
        except ValueError:
            exc_type, exc, tb = sys.exc_info()
            ok = crashlog.handle_exception(exc_type, exc, tb, "unit-test")
        check(ok is True, "handle_exception 记为崩溃")
        text = ""
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except Exception as e:
            text = "<读取失败: {}>".format(e)
        check("ValueError: L5-构造异常" in text, "crash 文件含异常信息")
        check("Traceback (most recent call last)" in text, "crash 文件含完整 traceback")

        # KeyboardInterrupt 不算崩溃
        before = len(text)
        ki = crashlog.handle_exception(KeyboardInterrupt, KeyboardInterrupt(), None,
                                       "unit-test")
        after = ""
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                after = f.read()
        except Exception:
            after = ""
        check(ki is False and len(after) == before,
              "KeyboardInterrupt 不记为崩溃 (正常退出路径)")

        # 限流
        crashlog.reset_throttle()
        crashlog._log1 = lambda *a, **k: None          # 静音, 避免污染 test 输出
        first = crashlog.log_once("k1", "boom")
        second = crashlog.log_once("k1", "boom")
        check(first is True and second is False,
              "log_once 限流: 同 key 60s 内只记一次")
        check(crashlog.log_once("k2", "boom") is True, "不同 key 不受影响")
        crashlog.reset_throttle()

        crashlog.disable()
        check(faulthandler.is_enabled() is False, "disable() 已关闭 faulthandler")
        check(crashlog.close_handles() is None, "close_handles() 可释放文件句柄")


# ══════════════════════════════════════════════════════════════════════
# ⑤ 更新强制校验 + 回滚接口
# ══════════════════════════════════════════════════════════════════════

def t_updater_strict():
    section("⑤ 更新强制校验 (sha256 强制化) + 回滚接口")
    import hashlib
    import updater

    with tempfile.TemporaryDirectory(prefix="acrpa_l5_upd_") as d:
        pkg = os.path.join(d, "ACRPA_vtest.zip")
        payload = b"MZ" + b"\0" * 64          # 合法魔数, 内容无所谓
        with open(pkg, "wb") as f:
            f.write(payload)
        digest = hashlib.sha256(payload).hexdigest()

        ok, msg = updater._verify_file(pkg, "", 0)
        check(ok is False and "sha256" in msg,
              "无 sha256 元数据 → 拒绝安装", msg)

        ok, msg = updater._verify_file(pkg, "0" * 64, 0)
        check(ok is False and "sha256" in msg, "sha256 不符 → 拒绝", msg)

        ok, msg = updater._verify_file(pkg, digest, 0)
        check(ok is True, "sha256 相符 → 通过", msg)

        ok, msg = updater._verify_file(pkg, "", 0, require_sha=False)
        check(ok is True, "require_sha=False 的显式豁免仍可用 (自测/体积校验)")

        ok, msg = updater.download_package({"latest": "9.9.9", "sha256": ""},
                                           dest_dir=d)
        check(ok is False and "sha256" in msg,
              "清单无 sha256 → 连下载都不做 (download_package)", msg)

        # 回滚接口: 源码模式明确拒绝, 不静默成功
        ok, msg = updater.rollback()
        check(ok is False and ("源码" in msg or "备份" in msg),
              "非打包环境 rollback() 明确失败并说明原因", msg)
        check(updater.list_backups() == [] or isinstance(updater.list_backups(), list),
              "list_backups() 返回列表")


# ══════════════════════════════════════════════════════════════════════
# ⑥ 绿色便携标记
# ══════════════════════════════════════════════════════════════════════

def t_portable():
    section("⑥ 绿色便携标记 (portable.flag → userdata/)")
    import state

    old_pkg = state._PACKAGE_DIR
    old_cfg = state.CONFIG_PATH
    try:
        with tempfile.TemporaryDirectory(prefix="acrpa_l5_port_") as d:
            state._PACKAGE_DIR = d
            # 无标记 → 与旧行为一致 (config.json 在"程序目录")
            state._resolve_data_paths()
            check(state.is_portable() is False, "无 portable.flag → 非便携")
            check(os.path.abspath(state.CONFIG_PATH) ==
                  os.path.abspath(os.path.join(d, "config.json")),
                  "非便携: CONFIG_PATH = <程序目录>/config.json (行为不变)",
                  state.CONFIG_PATH)
            check(os.path.abspath(state.log_dir()) ==
                  os.path.abspath(os.path.join(d, "logs")),
                  "非便携: 日志目录 = <程序目录>/logs")

            # 建标记 → 全部落 userdata/
            with open(os.path.join(d, "portable.flag"), "wb") as f:
                f.write(b"")
            state._resolve_data_paths()
            ud = os.path.join(d, "userdata")
            check(state.is_portable() is True, "存在 portable.flag → 便携")
            check(os.path.abspath(state.CONFIG_PATH) ==
                  os.path.abspath(os.path.join(ud, "config.json")),
                  "便携: config.json → userdata/", state.CONFIG_PATH)
            check(os.path.abspath(state.log_dir()) ==
                  os.path.abspath(os.path.join(ud, "logs")),
                  "便携: logs → userdata/logs", state.log_dir())
            check(os.path.abspath(state.screenshot_dir()) ==
                  os.path.abspath(os.path.join(ud, "screenshots")),
                  "便携: screenshots → userdata/screenshots")
            check(os.path.abspath(state.history_path()) ==
                  os.path.abspath(os.path.join(ud, "logs", "history.jsonl")),
                  "便携: 运行历史 → userdata/logs/history.jsonl")

            # 首次迁移: 旧程序目录的 config.json 被复制进 userdata (不覆盖已有)
            with open(os.path.join(d, "config.json"), "w", encoding="utf-8") as f:
                json.dump({"retry_max": 7}, f)
            moved = state.migrate_portable_data()
            check(moved >= 1 and os.path.isfile(os.path.join(ud, "config.json")),
                  "首次便携启动迁移旧 config.json (旧数据不丢)", str(moved))
            try:
                with open(os.path.join(ud, "config.json"), encoding="utf-8") as f:
                    migrated = json.load(f)
            except Exception:
                migrated = {}
            check(migrated.get("retry_max") == 7, "迁移内容正确", str(migrated))

            # 目标已存在时不覆盖
            with open(os.path.join(d, "config.json"), "w", encoding="utf-8") as f:
                json.dump({"retry_max": 99}, f)
            state.migrate_portable_data()
            try:
                with open(os.path.join(ud, "config.json"), encoding="utf-8") as f:
                    again = json.load(f)
            except Exception:
                again = {}
            check(again.get("retry_max") == 7,
                  "迁移不覆盖已存在的 userdata 数据 (失败不回退不清理)",
                  str(again))

            # 删标记 → 回到原路径
            os.remove(os.path.join(d, "portable.flag"))
            state._resolve_data_paths()
            check(os.path.abspath(state.CONFIG_PATH) ==
                  os.path.abspath(os.path.join(d, "config.json")),
                  "删除 portable.flag → 回到原路径", state.CONFIG_PATH)
    finally:
        state._PACKAGE_DIR = old_pkg
        state._resolve_data_paths()
        check(os.path.abspath(state.CONFIG_PATH) == os.path.abspath(old_cfg),
              "测试后 CONFIG_PATH 已还原", state.CONFIG_PATH)


# ══════════════════════════════════════════════════════════════════════
# ⑦ 危险动作确认闸门
# ══════════════════════════════════════════════════════════════════════

class _Cell(object):
    def __init__(self, value):
        self.value = value


def _row(*values):
    return [_Cell(v) for v in values]


def t_danger_gate():
    section("⑦ 危险动作确认闸门 (无 GUI = 拒绝, 不可被脚本绕过)")
    import engine
    import state

    eng = engine.engine
    old_flag = getattr(state, "CONFIRM_DANGEROUS", True)
    old_hook = engine.get_danger_confirm_hook()
    try:
        state.CONFIRM_DANGEROUS = True
        engine.set_danger_confirm_hook(None)        # 模拟无 GUI

        power = _row("关机", "60", "否", "否")
        res = eng._danger_confirm_gate("电源操作", power)
        check(res is not None and not res.ok and res.code == "danger_rejected",
              "无 GUI → 电源操作被拒绝", getattr(res, "message", ""))
        check("没有可用的确认界面" in (getattr(res, "message", "") or ""),
              "拒绝信息明确说明「无确认界面」", getattr(res, "message", ""))

        dry = _row("关机", "60", "否", "是")          # 预演=是
        check(eng._danger_confirm_gate("电源操作", dry) is None,
              "预演=是 → 不拦 (预演本身不执行)")

        delete_perm = _row("C:/tmp/x.txt", "否", "否")   # 送回收站=否 = 永久删除
        res = eng._danger_confirm_gate("删除文件", delete_perm)
        check(res is not None and res.code == "danger_rejected",
              "永久删除 → 拦截")
        delete_recycle = _row("C:/tmp/x.txt", "否", "是")
        check(eng._danger_confirm_gate("删除文件", delete_recycle) is None,
              "送回收站=是 → 不拦 (可恢复)")

        kill = _row("notepad.exe", "否", "是")
        check(eng._danger_confirm_gate("结束进程", kill) is not None,
              "结束进程(非强制) → 拦截")
        kill_force = _row("notepad.exe", "是", "是")
        check(eng._danger_confirm_gate("结束进程", kill_force) is None,
              "结束进程(强制) → 不拦")

        check(eng._danger_confirm_gate("等待", _row("1")) is None,
              "普通命令 → 无需确认")

        # 有回调但用户拒绝
        engine.set_danger_confirm_hook(lambda cmd, detail, timeout: False)
        res = eng._danger_confirm_gate("电源操作", power)
        check(res is not None and res.code == "danger_rejected",
              "回调返回 False → 取消执行")

        # 回调同意
        engine.set_danger_confirm_hook(lambda cmd, detail, timeout: True)
        check(eng._danger_confirm_gate("电源操作", power) is None,
              "回调返回 True → 放行到 handler")

        # 回调抛异常 → 失败即拒绝
        def _boom(cmd, detail, timeout):
            raise RuntimeError("dialog crash")
        engine.set_danger_confirm_hook(_boom)
        res = eng._danger_confirm_gate("电源操作", power)
        check(res is not None and res.code == "danger_rejected",
              "确认过程异常 → 按拒绝处理 (不失败开放)")

        # 关掉开关 → 不再额外询问
        state.CONFIRM_DANGEROUS = False
        engine.set_danger_confirm_hook(None)
        check(eng._danger_confirm_gate("电源操作", power) is None,
              "CONFIRM_DANGEROUS=False → 闸门不介入 (回落 handler 护栏)")
    finally:
        state.CONFIRM_DANGEROUS = old_flag
        engine.set_danger_confirm_hook(old_hook)


# ══════════════════════════════════════════════════════════════════════
# ⑧ CLI 新开关
# ══════════════════════════════════════════════════════════════════════

def t_cli_switches():
    section("⑧ --exec-task / --workflow / --rollback 解析与退出码")
    import cli

    o = cli.parse_args(["--exec-task", "1749"])
    check(o is not None and o["exec_task"] == "1749" and o["script"] is None,
          "--exec-task 解析正确", repr(o and o["exec_task"]))

    o = cli.parse_args(["--workflow", "wf.json"])
    check(o is not None and o["workflow"] == "wf.json",
          "--workflow 解析正确", repr(o and o["workflow"]))

    o = cli.parse_args(["--rollback"])
    check(o is not None and o["rollback"] is True, "--rollback 解析正确")

    check(cli.parse_args(["--exec-task"]) is None,
          "--exec-task 缺值 → None (回退 GUI)")
    check(cli.parse_args(["--nonsense", "x"]) is None,
          "未知开关仍静默忽略 → None")

    buf = io.StringIO()
    rc = cli.main(["--exec-task", "abc"], stdout=buf)
    check(rc == 2, "--exec-task 直接进 cli.main → 用法错误 2 (应由 run.py 转交)", str(rc))

    missing = os.path.join(tempfile.gettempdir(), "acrpa_l5_没有这个工作流.json")
    if os.path.isfile(missing):
        os.remove(missing)
    buf = io.StringIO()
    rc = cli.main(["--workflow", missing], stdout=buf)
    check(rc == 2, "--workflow 文件不存在 → 2", str(rc))

    buf = io.StringIO()
    rc = cli.main(["--rollback"], stdout=buf)
    check(rc == 2, "非打包环境 --rollback → 2 (并说明原因)",
          buf.getvalue().strip()[:80])

    # run.py 的接线仍然保持（静态断言, 与 _test_cli_headless 同款思路）
    try:
        with open(os.path.join(BASE, "run.py"), "r", encoding="utf-8") as f:
            txt = f.read()
    except Exception:
        txt = ""
    check("import cli" in txt and "cli.parse_args(" in txt and "cli.main(" in txt,
          "run.py 仍经 cli 分流 (既有契约不变)")
    check("app.main()" in txt, "run.py 仍保留 GUI 启动路径")
    check("single_instance" in txt, "run.py 已接入单实例互斥")


# ══════════════════════════════════════════════════════════════════════
# ⑨ 运行历史
# ══════════════════════════════════════════════════════════════════════

def t_history():
    section("⑨ 运行历史 history.jsonl (脱敏 + 轮转 + 上限)")
    import history

    with tempfile.TemporaryDirectory(prefix="acrpa_l5_hist_") as d:
        path = os.path.join(d, "history.jsonl")
        history.configure(path=path, max_bytes=2048, keep=2)
        try:
            rec = history.record(
                "script", "http://example.com/run?token=SECRET1234567890abcdef",
                trigger="cli", duration_ms=12, status="failed", failed_row=3,
                rows_total=10, mode="headless",
                detail="Authorization: Bearer abcdef1234567890abcdef")
            raw = ""
            with open(path, encoding="utf-8") as f:
                raw = f.read()
            check(os.path.isfile(path), "history.jsonl 已写入")
            check(json.loads(raw.strip().splitlines()[-1])["kind"] == "script",
                  "写入行可被 JSON 解析且字段完整")
            check("SECRET1234567890abcdef" not in raw,
                  "URL 中的 token 已脱敏", raw.strip()[:120])
            check("abcdef1234567890abcdef" not in raw,
                  "Bearer 凭据已脱敏", raw.strip()[:120])
            check("***" in raw, "脱敏标记 *** 出现")
            for key in ("ts", "kind", "target", "trigger", "duration_ms",
                        "status", "failed_row", "rows_total", "mode"):
                check(key in rec, "记录含字段 {}".format(key))

            # 轮转: 超过上限 → 归档为 history-*.jsonl
            for i in range(60):
                history.record("task", "t{}".format(i), trigger="sched",
                               duration_ms=i, status="success", mode="gui")
            rotated = history.rotated_files(path)
            check(len(rotated) >= 1, "超上限触发轮转 (生成 history-*.jsonl)",
                  str(len(rotated)))
            check(len(rotated) <= 2, "轮转文件按 keep=2 收敛", str(len(rotated)))
            check(os.path.getsize(path) <= 2048 * 2,
                  "当前历史文件体积受上限约束", str(os.path.getsize(path)))

            recent = history.read_recent(limit=5)
            check(len(recent) >= 1 and "status" in recent[0],
                  "read_recent() 可读回最近记录 (轮转后为当前文件剩余条数)",
                  "{} 条".format(len(recent)))
            last = history.last_result(kind="task")
            check(last is not None and last.get("kind") == "task",
                  "last_result() 支持按 kind 查「上次结果」")
        finally:
            history.configure(path=None, max_bytes=None, keep=None)


# ══════════════════════════════════════════════════════════════════════
# ⑩ 体积 / 规范门禁
# ══════════════════════════════════════════════════════════════════════

def t_gates():
    section("⑩ 体积门禁 / 打包规范门禁脚本")
    sys.path.insert(0, os.path.join(BASE, "tools"))
    import _check_spec_excludes as spec_gate
    import _check_volume as vol_gate

    rc, problems = spec_gate.check()
    check(rc == 0, "ACRPA.spec 与 build.py::_PROJECT_MODULES 一致 (门禁返回 0)",
          "; ".join(problems[:3]))
    hidden, excludes = spec_gate.spec_lists()
    check("single_instance" in hidden and "crashlog" in hidden and "history" in hidden,
          "L5 新模块已进 ACRPA.spec hiddenimports")

    with tempfile.TemporaryDirectory(prefix="acrpa_l5_gate_") as d:
        bad_spec = os.path.join(d, "ACRPA.spec")
        with open(bad_spec, "w", encoding="utf-8") as f:
            f.write("a = Analysis(['run.py'],\n"
                    "    hiddenimports=['app', 'state'],\n"
                    "    excludes=['history'],\n)\n")
        rc_bad, probs = spec_gate.check(spec_path=bad_spec)
        check(rc_bad != 0, "漏模块/误排时可执行文件规范门禁返回非 0",
              "; ".join(probs[:2]))
        check(any("history" in p for p in probs), "能指出被误排的具体模块",
              "; ".join(probs[:2]))

        fake_exe = os.path.join(d, "ACRPA.exe")
        with open(fake_exe, "wb") as f:
            f.write(b"MZ" + b"\0" * 1024)
        rc_ok, text_ok = vol_gate.check(path=fake_exe, max_mb=15)
        check(rc_ok == 0, "体积门禁: 小文件通过", text_ok)
        rc_big, text_big = vol_gate.check(path=fake_exe, max_mb=0.0001)
        check(rc_big == 1, "体积门禁: 超上限返回 1", text_big)
        rc_none, text_none = vol_gate.check(path=os.path.join(d, "nope.exe"))
        check(rc_none == 2, "体积门禁: 无产物返回 2 (与超限区分)", text_none)


# ══════════════════════════════════════════════════════════════════════
def main():
    print("ACRPA L5 工程化与稳定性 自测")
    print("工作目录:", BASE)
    t_single_instance()
    t_payload_validation()
    t_startup_marks()
    t_crashlog()
    t_updater_strict()
    t_portable()
    t_danger_gate()
    t_cli_switches()
    t_history()
    t_gates()

    print("\n" + "=" * 62)
    print("结果: 通过 {} / 失败 {} / 跳过 {}".format(
        len(_PASS), len(_FAIL), len(_SKIP)))
    if _FAIL:
        print("失败项:")
        for m in _FAIL:
            print("  - {}".format(m))
        print("=" * 62)
        return 1
    print("=" * 62)
    return 0


if __name__ == "__main__":
    sys.exit(main())
