# -*- coding: utf-8 -*-
"""崩溃留痕 (方案文档 §5 L5 #4) —— faulthandler + excepthook + 线程钩子。

设计要点
--------
1. **独立文件**: 崩溃写到 ``<log_dir>/crash_YYYYMMDD.log``，与按天轮转的
   ``acrpa_YYYYMMDD.log`` **分开**。原因: ``faulthandler`` 需要真实文件句柄
   （``fileno()``），且它在硬崩溃时由 C 层直接写入，不能与 ``ThreadSafeLog``
   的批处理缓冲/轮转逻辑共享句柄（否则崩溃信息会被缓冲吞掉或写进已轮转的文件）。
2. **句柄必须保住引用**: 文件对象一旦被 GC 关闭，``faulthandler`` 会静默失效，
   因此句柄存于模块级 ``_HANDLES``，只在 ``install()`` 再次调用时替换。
3. **不记 KeyboardInterrupt**: Ctrl+C / 关闭窗口是正常退出路径，不算崩溃。
4. **不做 minidump / 遥测**（§7 已否决遥测），零第三方依赖、零后台线程。

用法::

    import crashlog
    crashlog.install(state.log_dir())        # 启动早期调用一次 (幂等)
    crashlog.log_once("tray.poll", "…")      # 轮询异常限流日志 (同 key 60s 一次)
"""
import faulthandler
import os
import sys
import threading
import time
import traceback

# faulthandler 的文件句柄引用 (防 GC 关闭 → 崩溃信息静默丢失)
_HANDLES = []
_INSTALLED = False
_CRASH_DIR = ""
_ORIG_EXCEPTHOOK = None
_ORIG_THREAD_HOOK = None

# 轮询/高频路径的日志限流表: {key: last_ts}
_THROTTLE = {}
_THROTTLE_LOCK = threading.Lock()
DEFAULT_THROTTLE_SEC = 60.0


def crash_dir(log_dir=None):
    """→ 崩溃日志目录 (缺省 = state.log_dir())。"""
    if log_dir:
        return os.path.abspath(str(log_dir))
    try:
        import state
        return state.log_dir()
    except Exception:
        return os.path.join(os.getcwd(), "logs")


def crash_log_path(log_dir=None, when=None):
    """→ 当天的崩溃日志绝对路径 (crash_YYYYMMDD.log)。"""
    stamp = time.strftime("%Y%m%d", time.localtime(when))
    return os.path.join(crash_dir(log_dir), "crash_{}.log".format(stamp))


def is_installed():
    return bool(_INSTALLED)


def _open_crash_handle(path):
    """打开崩溃日志句柄并登记引用；失败返回 None (不抛)。"""
    try:
        parent = os.path.dirname(path)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent, exist_ok=True)
        handle = open(path, "a", encoding="utf-8", errors="replace", buffering=1)
        _HANDLES.append(handle)
        # 只保留最新一个句柄, 避免跨天多次 install 越积越多
        while len(_HANDLES) > 2:
            try:
                _HANDLES[0].close()
            except Exception:
                pass
            del _HANDLES[0]
        return handle
    except Exception:
        return None


def _write_block(source, text):
    """把一段文本追加进当天的崩溃日志；失败静默 (崩溃处理路径绝不二次抛)。"""
    path = crash_log_path(_CRASH_DIR or None)
    try:
        parent = os.path.dirname(path)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent, exist_ok=True)
        with open(path, "a", encoding="utf-8", errors="replace") as f:
            f.write("\n===== {} | pid={} | {}\n".format(
                time.strftime("%Y-%m-%d %H:%M:%S"), os.getpid(), source))
            f.write(text.rstrip() + "\n")
    except Exception:
        pass


def _log1(msg, level="error"):
    """→ utils.log1 的安全代理 (utils 未就绪时静默)。"""
    try:
        import utils
        utils.log1(msg, level)
    except Exception:
        pass


def handle_exception(exc_type, exc_value, exc_tb, source="main"):
    """记录一个未捕获异常 → 崩溃文件 + log1 摘要；KeyboardInterrupt 不算崩溃。

    返回 True 表示已记为崩溃，False 表示被忽略 (或写入失败)。
    """
    if exc_type is None:
        return False
    try:
        if issubclass(exc_type, KeyboardInterrupt):
            return False
    except Exception:
        pass
    try:
        text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    except Exception:
        try:
            text = "{}: {}".format(getattr(exc_type, "__name__", "Exception"), exc_value)
        except Exception:
            text = "<无法格式化 traceback>"
    _write_block(source, text)
    name = getattr(exc_type, "__name__", "Exception")
    _log1("程序发生未捕获异常 [{}]: {}: {} (详情见 crash 日志)".format(
        source, name, exc_value), "error")
    return True


def _excepthook(exc_type, exc_value, exc_tb):
    """替换 sys.excepthook：先落崩溃文件，**再**交回原钩子。

    两者都要: 崩溃文件用于事后复盘（§L5 #4），原钩子保证控制台/开发者仍能看到
    完整 traceback —— 绝不能因为"已落盘"就把异常吞掉。
    """
    try:
        handle_exception(exc_type, exc_value, exc_tb, "main")
    except Exception:
        pass
    try:
        if _ORIG_EXCEPTHOOK is not None:
            _ORIG_EXCEPTHOOK(exc_type, exc_value, exc_tb)
        else:
            sys.__excepthook__(exc_type, exc_value, exc_tb)
    except Exception:
        pass


def _thread_excepthook(args):
    """替换 threading.excepthook：子线程未捕获异常同样留痕。"""
    try:
        name = getattr(getattr(args, "thread", None), "name", "thread")
        if getattr(args, "exc_type", None) is not None:
            handle_exception(args.exc_type, args.exc_value, args.exc_traceback,
                             "thread:{}".format(name))
            return
    except Exception:
        pass
    try:
        if _ORIG_THREAD_HOOK is not None:
            _ORIG_THREAD_HOOK(args)
    except Exception:
        pass


def install(log_dir=None):
    """启用崩溃留痕 (幂等)。返回崩溃日志路径；任何一步失败都不影响启动。"""
    global _INSTALLED, _CRASH_DIR, _ORIG_EXCEPTHOOK, _ORIG_THREAD_HOOK
    _CRASH_DIR = crash_dir(log_dir)
    path = crash_log_path(_CRASH_DIR)
    if not _INSTALLED:
        handle = _open_crash_handle(path)
        if handle is not None:
            try:
                # all_threads=True: 任何线程的致命信号都留栈 (Windows 上是 SEH 异常)
                faulthandler.enable(file=handle, all_threads=True)
            except Exception:
                pass
        try:
            if _ORIG_EXCEPTHOOK is None:
                _ORIG_EXCEPTHOOK = sys.excepthook
            sys.excepthook = _excepthook
        except Exception:
            pass
        try:
            if hasattr(threading, "excepthook"):
                if _ORIG_THREAD_HOOK is None:
                    _ORIG_THREAD_HOOK = threading.excepthook
                threading.excepthook = _thread_excepthook
        except Exception:
            pass
        _INSTALLED = True
    else:
        # 二次调用 (换目录 / 跨天): 只把 faulthandler 指向新句柄
        handle = _open_crash_handle(path)
        if handle is not None:
            try:
                faulthandler.enable(file=handle, all_threads=True)
            except Exception:
                pass
    return path


def close_handles():
    """关闭已打开的崩溃日志句柄（退出路径 / 测试收尾；幂等）。

    Windows 上只要句柄还在，crash 文件就无法被删除/改名（日志轮转、
    便携模式迁移都会踩到），因此提供一个显式关闭点。
    """
    while _HANDLES:
        try:
            _HANDLES.pop().close()
        except Exception:
            pass


def disable():
    """关闭 faulthandler（测试收尾用；不还原 sys.excepthook 以免影响其它测试）。"""
    global _INSTALLED
    try:
        faulthandler.disable()
    except Exception:
        pass
    close_handles()
    _INSTALLED = False


def log_once(key, msg, level="error", interval=DEFAULT_THROTTLE_SEC):
    """限流日志：同一 ``key`` 在 ``interval`` 秒内最多记一条。

    用途: 托盘轮询 / 提醒 tick 等**高频** except 分支，避免同一异常刷屏。
    返回 True 表示本次真的写入了日志。
    """
    now = time.time()
    try:
        with _THROTTLE_LOCK:
            last = _THROTTLE.get(key, 0.0)
            if now - last < max(0.0, float(interval)):
                return False
            _THROTTLE[key] = now
            if len(_THROTTLE) > 256:      # 防御: key 无限增长
                for k in [k for k, ts in _THROTTLE.items() if now - ts > 3600]:
                    _THROTTLE.pop(k, None)
    except Exception:
        pass
    _log1(msg, level)
    return True


def reset_throttle():
    """清空限流表（测试用）。"""
    try:
        with _THROTTLE_LOCK:
            _THROTTLE.clear()
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════
# 结构化启动日志 (方案文档 §5 L5 #3)
# ══════════════════════════════════════════════════════════════════════
# 固定 11 个阶段；每个阶段一条 INFO::
#
#     [startup] stage=config cost=12ms keys=126
#
# 说明: 阶段由 run.py / app.py 在**各自边界**调用 ``startup_mark(stage, **kv)``
# 产出（不引新框架、不碰 ACRPA.build_app 内部），cost 为距上一阶段的毫秒数。

BOOT_STAGES = (
    "entry",            # 进程入口
    "single_instance",  # 单实例判定
    "config",           # 配置加载
    "log_ready",        # 日志系统就绪
    "ui_build",         # UI 构建
    "theme",            # 主题 / 缩放
    "tray",             # 托盘初始化
    "ipc",              # 单实例 IPC 接收窗口
    "commands",         # 命令注册表（命令总数）
    "scheduler",        # 调度器启动
    "capabilities",     # 扩展 / 能力探测
    "main_loop",        # 主循环开始
)

_MARKS = []
_MARK_LOCK = threading.Lock()
_MARK_T0 = time.time()


def reset_startup():
    """清空已记录的启动阶段（测试用）。"""
    global _MARK_T0
    with _MARK_LOCK:
        _MARKS[:] = []
        _MARK_T0 = time.time()


def startup_marks():
    """→ 已记录的阶段列表 ``[(stage, cost_ms, detail_dict), ...]`` (副本)。"""
    with _MARK_LOCK:
        return list(_MARKS)


def startup_texts():
    """→ 已记录的每阶段完整日志行（便于测试断言行数与顺序）。"""
    out = []
    with _MARK_LOCK:
        for stage, cost, detail in _MARKS:
            extra = " ".join("{}={}".format(k, v) for k, v in detail.items())
            out.append("[startup] stage={} cost={}ms{}".format(
                stage, cost, (" " + extra) if extra else ""))
    return out


def startup_mark(stage, **detail):
    """记一个启动阶段 → 返回日志行（不抛）。

    未登记的 stage 名仍会记录（并在 detail 里标 ``unexpected=1``），
    以免某天新增阶段时静默丢失日志。
    """
    global _MARK_T0
    now = time.time()
    line = ""
    try:
        with _MARK_LOCK:
            cost = int(round((now - _MARK_T0) * 1000))
            _MARK_T0 = now
            extra = dict(detail or {})
            if stage not in BOOT_STAGES:
                extra["unexpected"] = 1
            _MARKS.append((str(stage), cost, extra))
            detail_text = " ".join("{}={}".format(k, v) for k, v in extra.items())
            line = "[startup] stage={} cost={}ms{}".format(
                stage, cost, (" " + detail_text) if detail_text else "")
    except Exception:
        line = "[startup] stage={}".format(stage)
    _log1(line, "info")
    return line
