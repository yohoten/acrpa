# -*- coding: utf-8 -*-
"""后台任务执行器: 工作跑在 threading, 结果经 queue 回主线程执行。

Tk 不是线程安全的 —— 任何 `widget.configure/insert/delete` 都必须在主线程, 否则
表现为随机崩溃或"界面卡住但日志在动"。本模块只做一件事:

    submit(耗时函数, on_done, on_error)
      ├─ 工作线程: 执行耗时函数 (磁盘 IO / 解析 / 网络 / 批量计算)
      └─ 主线程: root.after 轮询 queue → 执行 on_done/on_error (可以安全碰控件)

设计取舍:
- **不用线程池**: RPA UI 的耗时任务并发度低 (打开脚本 / 检查更新 / 连接测试),
  每次一个 daemon 线程最简单, 也避免线程池与 Tk 生命周期/退出时序纠缠。
- **未 start 时同步执行**: 无 GUI 环境与测试脚本里 `submit()` 退化为直接调用,
  既有"同步完成"的断言与调用约定完全不变 (零行为变化的最小改造路径)。
- **回调永不抛出**: on_done 内部异常只记 stderr 并继续, 避免一个坏回调把轮询
  循环打死 (那样队列会静默堆积, 表现为"后台任务全部无响应")。
"""

from __future__ import absolute_import

import queue
import sys
import threading
import traceback

__all__ = ["start", "shutdown", "submit", "is_started", "pending", "drain"]

_QUEUE = queue.Queue()
_ROOT = None
_POLL_MS = 60
_POLL_ID = None
_STARTED = False
_PENDING = 0          # 已提交未回调完成的任务数 (仅统计异步)
_LOCK = threading.Lock()


def start(root, poll_ms=60):
    """绑定 root 并启动轮询 (幂等; 重复调用只更新 root/poll_ms)。

    root 需提供 `after(ms, fn)`; 传入 None 等价 shutdown。
    """
    global _ROOT, _POLL_MS, _STARTED
    if root is None:
        shutdown()
        return False
    _ROOT = root
    try:
        _POLL_MS = max(16, int(poll_ms))
    except Exception:
        _POLL_MS = 60
    _STARTED = True
    _schedule()
    return True


def is_started():
    """是否已绑定 root (未绑定时 submit 走同步回退)。"""
    return bool(_STARTED and _ROOT is not None)


def pending():
    """已提交但回调尚未执行的任务数。"""
    return _PENDING


def shutdown():
    """停止轮询 (窗口销毁前调用); 已入队结果直接丢弃。"""
    global _ROOT, _POLL_ID, _STARTED, _PENDING
    _STARTED = False
    if _POLL_ID is not None and _ROOT is not None:
        try:
            _ROOT.after_cancel(_POLL_ID)   # root 可能已销毁, 失败不影响关闭流程
        except Exception:
            pass
    _POLL_ID = None
    _ROOT = None
    with _LOCK:
        _PENDING = 0
    try:
        while True:
            _QUEUE.get_nowait()
    except queue.Empty:
        pass


def _schedule():
    global _POLL_ID
    if _POLL_ID is not None or not _STARTED or _ROOT is None:
        return
    try:
        _POLL_ID = _ROOT.after(_POLL_MS, _poll)
    except Exception:
        # root 已销毁/不是合法 Tk 对象: 退化为同步模式, 不静默挂死调用方
        _POLL_ID = None


def _poll():
    """主线程轮询: 把队列里的结果逐个执行回调, 然后重新排期。"""
    global _POLL_ID
    _POLL_ID = None
    drain()
    _schedule()


def drain(limit=64):
    """执行至多 limit 个已就绪结果 (测试可直接调用; 也供宿主在自旋时手动挤队列)。"""
    done = 0
    while done < limit:
        try:
            item = _QUEUE.get_nowait()
        except queue.Empty:
            break
        done += 1
        _run_item(item)
    return done


def _safe_call(fn, arg):
    if fn is None:
        return
    try:
        fn(arg)
    except Exception:
        try:
            sys.stderr.write("[bg_task] 回调异常: {}\n".format(
                traceback.format_exc(limit=3)))
        except Exception:
            pass


def _run_item(item):
    global _PENDING
    on_done, on_error, ok, payload, name = item
    with _LOCK:
        if _PENDING > 0:
            _PENDING -= 1
    if ok:
        _safe_call(on_done, payload)
    else:
        _safe_call(on_error, payload)
    return name


def submit(fn, on_done=None, on_error=None, name=""):
    """提交一个后台任务。

    fn: 无参可调用, 返回值作为 on_done(arg)。
    返回 True 表示已异步提交; False 表示当前无 GUI 事件循环, 已**同步执行**
    (此时 on_done/on_error 也在本线程内同步调用, 便于测试与脚本复用)。
    """
    global _PENDING
    if not is_started():
        try:
            result = fn()
        except Exception as e:
            if on_error is not None:
                on_error(e)
                return False
            raise
        if on_done is not None:
            on_done(result)
        return False
    with _LOCK:
        _PENDING += 1

    def _work():
        try:
            payload = (True, fn(), None)
        except Exception as e:
            payload = (False, e, None)
        _QUEUE.put((on_done, on_error, payload[0], payload[1], name))

    try:
        t = threading.Thread(target=_work, name="bg:{}".format(name or "task"))
        t.daemon = True
        t.start()
    except Exception as e:
        # 线程创建失败 (极端环境): 回退同步执行, 保证任务不丢
        with _LOCK:
            if _PENDING > 0:
                _PENDING -= 1
        try:
            result = fn()
        except Exception as ee:
            if on_error is not None:
                on_error(ee if ee is not None else e)
                return False
            raise
        if on_done is not None:
            on_done(result)
        return False
    return True
