# -*- coding: utf-8 -*-
"""后台任务执行器: 工作跑在 threading, 结果经 queue 回主线程执行。

Tk 不是线程安全的 —— 任何 `widget.configure/insert/delete` 都必须在主线程, 否则
表现为随机崩溃或"界面卡住但日志在动"。本模块只做一件事:

    submit(耗时函数, on_done, on_error)
      ├─ 工作线程: 执行耗时函数 (磁盘 IO / 解析 / 网络 / 批量计算)
      └─ 主线程: root.after 轮询 queue → 执行 on_done/on_error (可以安全碰控件)

设计取舍:
- **双队列 + 优先级** (v5 §2.2 B1): 高优/普通进无界 `_Q_HIGH` (用户等待型: 打开/
  保存/截图确认), 低优进有界 `_Q_LOW` (`maxsize=256`, 预加载/更新检查/OCR 预热)。
  队满时**只作用于 low**, 且绝不在主线程阻塞等待。
- **固定 daemon 池** (v5 §2.3): `IO_POOL`(4) / `COMPUTE_POOL`(2) 只是**并发上限**,
  见 `_FixedDaemonPool` 里为什么不用 ThreadPoolExecutor。
- **未 start 时同步执行**: 无 GUI 环境与测试脚本里 `submit()` 退化为直接调用,
  既有"同步完成"的断言与调用约定完全不变 (零行为变化的最小改造路径)。
- **回调永不抛出**: on_done 内部异常只记 stderr 并继续, 避免一个坏回调把轮询
  循环打死 (那样队列会静默堆积, 表现为"后台任务全部无响应")。
- **绝不在 worker 里碰 Tk**: `after` / `event_generate` 跨线程不安全 (v5 §2.3),
  唤醒一律走主线程哨兵 `_poll`。

运行时开关 (v5 §7.2, 默认全开; 置 0/false/off 即回退):
    BG_TASK_V2        = False → 关闭本模块 v2 能力 (有界队列/优先级/取消/分块)
    USE_TASK_POOLS    = False → 回到「每任务一 daemon 线程」(无并发上限)
    BG_WAKEUP_BACKOFF = False → 哨兵回到固定 poll_ms 轮询 (无 60/250/500 退避)
"""

from __future__ import absolute_import

import os
import queue
import sys
import threading
import traceback

__all__ = [
    "start", "shutdown", "submit", "submit_chunked", "CancelToken",
    "is_started", "pending", "pending_len", "drain", "dropped",
    "PRIO_HIGH", "PRIO_NORMAL", "PRIO_LOW", "IO_POOL", "COMPUTE_POOL",
]


def _env_flag(name, default=True):
    """读环境变量开关 (缺失/空 → default; 0/false/no/off → False)。"""
    try:
        v = os.environ.get(name)
    except Exception:
        return default
    if v is None or str(v).strip() == "":
        return default
    return str(v).strip().lower() not in ("0", "false", "no", "off")


BG_TASK_V2 = _env_flag("BG_TASK_V2", True)
USE_TASK_POOLS = BG_TASK_V2 and _env_flag("USE_TASK_POOLS", True)
BG_WAKEUP_BACKOFF = _env_flag("BG_WAKEUP_BACKOFF", True)

# ── 优先级常量 (v5 §2.2 B1) ──
PRIO_HIGH = 0      # 用户等待型: 打开/保存/截图确认 (无界)
PRIO_NORMAL = 1    # 普通后台任务 (无界)
PRIO_LOW = 2       # 后台型: 预加载/更新检查/OCR 预热 (有界 256, 可丢弃)
_PRIO_ALIAS = {"high": PRIO_HIGH, "normal": PRIO_NORMAL, "low": PRIO_LOW}

_QUEUE_MAXSIZE = 256
_Q_HIGH = queue.Queue()                       # 无界: high + normal
_Q_LOW = queue.Queue(maxsize=_QUEUE_MAXSIZE)  # 有界: low, 满了丢最旧

_ROOT = None
_POLL_MS = 60
_POLL_ID = None
_STARTED = False
_PENDING = 0          # 已提交未回调完成的任务数/待回调分块数 (仅统计异步)
_LOCK = threading.Lock()
_CLOSING = False      # D8: shutdown 置位; worker 在入队前检查, 为真则丢弃在飞结果
_THREADS = set()      # D8: 在飞 worker 句柄, shutdown 时 join (单个上限 1.0 s)
_EPOCH = 0            # D8: 轮次号; shutdown 递增, 上一轮的迟到结果不得污染下一轮
_DROPPED = 0          # v2: 因背压被丢弃的低优结果数 (供状态栏提示)

# ── 空闲唤醒自适应退避 (v5 §2.3: 60 / 250 / 500 ms) ──
_BACKOFF_STEPS = (60, 250, 500)
_IDLE_TO_MID = 3          # 连续 3 轮空 → 250 ms
_IDLE_TO_SLOW = 20        # 连续 20 轮空 → 500 ms
_idle_rounds = 0


def _log_error(msg):
    """D7: 后台异常落日志 (惰性 import —— 本模块可能被更早导入, 避免循环依赖)。

    log1 只是入队写, 工作线程调用安全 (src/core/log.py 队列实现); 优先直接用
    core.log 以免在 worker 里拉起整个 utils。
    """
    try:
        from core.log import log1
    except Exception:
        try:
            from utils import log1
        except Exception:
            return
    try:
        log1(msg, "error")
    except Exception:
        pass


def _norm_prio(priority):
    """优先级入参归一: 接受 0/1/2 或 "high"/"normal"/"low" (未知 → normal)。"""
    try:
        if isinstance(priority, str):
            return _PRIO_ALIAS.get(priority.strip().lower(), PRIO_NORMAL)
        p = int(priority)
    except Exception:
        return PRIO_NORMAL
    if p <= PRIO_HIGH:
        return PRIO_HIGH
    if p >= PRIO_LOW:
        return PRIO_LOW
    return PRIO_NORMAL


class CancelToken(object):
    """取消令牌 (v5 §2.2 B6)。

    worker 侧: 任务体轮询 `token.is_cancelled()` 自行提前返回;
    主线程侧: 结果若在取消之后抵达, `_run_item` 直接跳过回调 (回调不再执行)。
    """

    __slots__ = ("_ev",)

    def __init__(self):
        self._ev = threading.Event()

    def cancel(self):
        try:
            self._ev.set()
        except Exception:
            pass

    def is_cancelled(self):
        try:
            return bool(self._ev.is_set())
        except Exception:
            return False

    def reset(self):
        try:
            self._ev.clear()
        except Exception:
            pass


class _FixedDaemonPool(object):
    """固定大小 daemon 线程池 (v5 §2.3 的 IO_POOL / COMPUTE_POOL)。

    为什么不用 `ThreadPoolExecutor`: 它的 worker 线程是**非 daemon**, 且
    `concurrent.futures.thread` 会在解释器退出时 atexit join 全部 worker ——
    只要有一个长任务在飞, 关窗就卡住, 与 P0-8「shutdown 后 1.5 s 内退出」
    直接冲突。这里用「固定数量 daemon worker + 单队列」, 并发上限与文档一致,
    且永不阻塞进程退出 (shutdown 只放哨兵让 worker 自行退出, 不 join)。
    """

    def __init__(self, name, workers):
        self._name = str(name)
        self._size = max(1, int(workers))
        self._q = queue.Queue()
        self._lock = threading.Lock()
        self._closed = False
        self._threads = []

    # 元数据 (便于自测/诊断, 与文档参数对齐)
    @property
    def max_workers(self):
        return self._size

    @property
    def closed(self):
        return bool(self._closed)

    def submit(self, fn):
        """投递一个无参可调用; 池已关闭时抛 RuntimeError (调用方回退单线程)。"""
        with self._lock:
            if self._closed:
                raise RuntimeError("pool closed: {}".format(self._name))
            while len(self._threads) < self._size:
                t = threading.Thread(target=self._loop,
                                    name="{}-{}".format(self._name, len(self._threads)))
                t.daemon = True          # 关键: 不阻塞进程退出
                self._threads.append(t)
                t.start()
        self._q.put(fn)
        return True

    def _loop(self):
        while True:
            try:
                fn = self._q.get()
            except Exception:
                return
            if fn is None:               # shutdown 哨兵
                return
            try:
                fn()
            except Exception:
                pass

    def shutdown(self, wait=False):
        """置关闭标志 + 放哨兵; wait=True 时 join (单个上限 0.3 s, 默认不等)。"""
        with self._lock:
            self._closed = True
            threads = list(self._threads)
            self._threads = []
        for _ in threads:
            self._q.put(None)
        if wait:
            for t in threads[:2]:        # 最多等 2 个, 避免关闭被拖长
                try:
                    t.join(timeout=0.3)
                except Exception:
                    pass

    def reopen(self):
        """start() 时重新允许投递 (worker 线程按需再起)。"""
        with self._lock:
            self._closed = False


IO_POOL = _FixedDaemonPool("bg-io", 4)          # 磁盘/网络/截图/OCR/DLL
COMPUTE_POOL = _FixedDaemonPool("bg-compute", 2)  # 解析/校验/文本拼装


def start(root, poll_ms=60):
    """绑定 root 并启动轮询 (幂等; 重复调用只更新 root/poll_ms)。

    root 需提供 `after(ms, fn)`; 传入 None 等价 shutdown。
    """
    global _ROOT, _POLL_MS, _STARTED, _CLOSING, _idle_rounds
    if root is None:
        shutdown()
        return False
    _ROOT = root
    try:
        _POLL_MS = max(16, int(poll_ms))
    except Exception:
        _POLL_MS = 60
    _STARTED = True
    _idle_rounds = 0
    # D8: 清理关闭标志, 使 shutdown 之后可以重新 start; 轮次号只由 shutdown
    # 递增, 故重复 start 不会误伤本轮仍在飞的任务 (只关闭「上一轮」的迟到结果)。
    _CLOSING = False
    try:
        IO_POOL.reopen()
        COMPUTE_POOL.reopen()
    except Exception:
        pass
    _schedule()
    return True


def is_started():
    """是否已绑定 root (未绑定时 submit 走同步回退)。"""
    return bool(_STARTED and _ROOT is not None)


def pending():
    """已提交但回调尚未执行的任务数。"""
    return _PENDING


def pending_len():
    """两个结果队列里待主线程处理的结果数 (哨兵退避的直接输入)。"""
    try:
        return int(_Q_HIGH.qsize()) + int(_Q_LOW.qsize())
    except Exception:
        return 0


def dropped():
    """因背压被丢弃的低优结果累计数 (状态栏可用它提示「已跳过 N 项后台刷新」)。"""
    return _DROPPED


def shutdown():
    """停止轮询 (窗口销毁前调用); 已入队结果直接丢弃。

    D8 改造: ① 先置 _CLOSING 再清队列 —— 在飞 worker 会在入队之前发现标志并
    直接丢弃, 不再往无人消费的队列里堆结果; ② 递增 _EPOCH, 即使某个 worker 在
    join 超时后仍在飞, 其迟到结果也进不了下一轮; ③ 末尾 join 所有 worker
    (单个上限 1.0 s)。worker 只做「计算 + 入队」, 不依赖主线程泵, 故主线程
    join 不会死锁; 总关闭时长最多 1.0 s。

    v2: 同时关闭两个固定池 (只放哨兵不 join —— worker 是 daemon, 进程可立即退出)。
    """
    global _ROOT, _POLL_ID, _STARTED, _PENDING, _CLOSING, _EPOCH, _idle_rounds
    _CLOSING = True
    _EPOCH += 1
    _STARTED = False
    _idle_rounds = 0
    if _POLL_ID is not None and _ROOT is not None:
        try:
            _ROOT.after_cancel(_POLL_ID)   # root 可能已销毁, 失败不影响关闭流程
        except Exception:
            pass
    _POLL_ID = None
    _ROOT = None
    with _LOCK:
        _PENDING = 0
    for q in (_Q_HIGH, _Q_LOW):
        try:
            while True:
                q.get_nowait()
        except queue.Empty:
            pass
        except Exception:
            pass
    try:
        IO_POOL.shutdown()
        COMPUTE_POOL.shutdown()
    except Exception:
        pass
    with _LOCK:
        threads = list(_THREADS)
    for t in threads:
        try:
            t.join(timeout=1.0)
        except Exception:
            pass


def _pick_interval(processed=0):
    """本轮结束后下个哨兵周期 (v5 §2.3: 有结果 60 / 连续 3 轮空 250 / 20 轮空 500)。"""
    if not BG_WAKEUP_BACKOFF:
        return _POLL_MS
    # 队列非空 (单轮 limit 切不完) → 立即回到 60 ms, 不拖尾
    if processed or pending_len() > 0:
        return _BACKOFF_STEPS[0]
    if _idle_rounds >= _IDLE_TO_SLOW:
        return _BACKOFF_STEPS[2]
    if _idle_rounds >= _IDLE_TO_MID:
        return _BACKOFF_STEPS[1]
    return _BACKOFF_STEPS[0]


def _schedule(interval=None):
    global _POLL_ID
    if _POLL_ID is not None or not _STARTED or _ROOT is None:
        return
    try:
        _POLL_ID = _ROOT.after(int(interval if interval else _POLL_MS), _poll)
    except Exception:
        # root 已销毁/不是合法 Tk 对象: 退化为同步模式, 不静默挂死调用方
        _POLL_ID = None


def _poll():
    """主线程哨兵: 把结果队列里已就绪的结果执行回调, 再按自适应周期重新排期。"""
    global _POLL_ID, _idle_rounds
    _POLL_ID = None
    done = 0
    try:
        done = drain(limit=32)
    except Exception:
        done = 0
    if BG_WAKEUP_BACKOFF:
        _idle_rounds = 0 if done else (_idle_rounds + 1)
    _schedule(_pick_interval(done))


def _next_item():
    """高优先队列优先; 都空返回 None。"""
    try:
        return _Q_HIGH.get_nowait()
    except queue.Empty:
        pass
    except Exception:
        pass
    try:
        return _Q_LOW.get_nowait()
    except queue.Empty:
        return None
    except Exception:
        return None


def drain(limit=32):
    """执行至多 limit 个已就绪结果 (测试可直接调用; 也供宿主在自旋时手动挤队列)。"""
    done = 0
    while done < limit:
        item = _next_item()
        if item is None:
            break
        done += 1
        try:
            _run_item(item)
        except Exception:
            pass
    return done


def _dec_pending():
    global _PENDING
    with _LOCK:
        if _PENDING > 0:
            _PENDING -= 1


def _enqueue(item, prio):
    """worker → 主线程结果入队 (CLOSING/EPOCH 守卫 + low 有界背压)。

    背压策略 (v5 §2.2 B1 选定「丢弃最旧」一种, 不做同 name 合并):
    为什么不合并同类: 合并需要线性遍历 `queue.Queue` 内部 deque (非线程安全),
    或另维护一份 name 索引 (与出队路径必须严格同步, 否则索引与队列漂移);
    而「丢最旧 low」已把内存上界钉死在 256 项, 且本工程的 low 任务全是
    「可重算的刷新/预热」, 丢弃旧结果不损失任何终态 —— 故只实现这一种。
    """
    global _DROPPED
    if _CLOSING or item[6] != _EPOCH:
        _dec_pending()
        return False
    if prio >= PRIO_LOW:
        try:
            _Q_LOW.put_nowait(item)
            return True
        except queue.Full:
            pass
        except Exception:
            pass
        try:                                  # ① 丢最旧, 给新结果腾槽
            _Q_LOW.get_nowait()
            _DROPPED += 1
            _dec_pending()                    # 丢掉的那项不再进主线程
        except Exception:
            pass
        try:
            _Q_LOW.put_nowait(item)
            return True
        except Exception:
            _DROPPED += 1
            _dec_pending()
            return False
    try:
        _Q_HIGH.put_nowait(item)               # 无界: 永不阻塞主线程
        return True
    except Exception:
        _dec_pending()
        return False


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
    on_done, on_error, ok, payload, name, tb, _epoch, token = item
    # 主线程侧取消: 回调不再执行 (B6)
    if token is not None and token.is_cancelled():
        _dec_pending()
        return name
    _dec_pending()
    if ok:
        _safe_call(on_done, payload)
    else:
        # D7: on_error 为 None 时也必须落日志 —— 否则后台失败完全无痕。
        #     带 traceback, 便于定位是哪个后台任务炸了。
        _log_error("[bg:{}] 任务异常: {}{}".format(
            name or "task", payload, ("\n" + tb) if tb else ""))
        _safe_call(on_error, payload)
    return name


def _dispatch(work, name, pool):
    """把 _work 交给固定池执行; 池不可用 (关闭/未启用) 时回退独立 daemon 线程。"""
    if USE_TASK_POOLS:
        try:
            target = COMPUTE_POOL if str(pool or "").lower().startswith("comp") else IO_POOL
            target.submit(work)
            return True
        except Exception:
            pass
    try:
        t = threading.Thread(target=work, name="bg:{}".format(name or "task"))
        t.daemon = True
        with _LOCK:
            _THREADS.add(t)
        t.start()
        return True
    except Exception:
        return False


def submit(fn, on_done=None, on_error=None, name="", priority=PRIO_NORMAL,
           token=None, pool="io"):
    """提交一个后台任务。

    fn: 无参可调用, 返回值作为 on_done(arg)。
    priority: PRIO_HIGH/PRIO_NORMAL (无界) 或 PRIO_LOW (有界 256, 可被丢弃);
              也接受 "high"/"normal"/"low"。
    token: CancelToken; 取消后不再执行 fn 的后续批次, 且主线程不再回调。
    pool: "io" (默认, IO_POOL 4 线程) 或 "compute" (COMPUTE_POOL 2 线程)。
    返回 True 表示已异步提交; False 表示当前无 GUI 事件循环, 已**同步执行**
    (此时 on_done/on_error 也在本线程内同步调用, 便于测试与脚本复用)。
    """
    global _PENDING
    if not is_started():
        if token is not None and token.is_cancelled():
            return False
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
    prio = _norm_prio(priority) if BG_TASK_V2 else PRIO_NORMAL
    with _LOCK:
        _PENDING += 1

    def _work():
        epoch = _EPOCH
        try:
            try:
                if token is not None and token.is_cancelled():
                    _dec_pending()          # 取消: 不入队, 也不回调
                    return
                payload = (True, fn(), None)
            except Exception as e:
                # D7: traceback 一并放进 payload (第三位不再恒为 None)
                payload = (False, e, traceback.format_exc())
            # D8: 关窗 / 换轮之后绝不再入队, 主线程也就永远不会执行旧回调
            _enqueue((on_done, on_error, payload[0], payload[1], name,
                      payload[2], epoch, token), prio)
        finally:
            with _LOCK:
                _THREADS.discard(threading.current_thread())

    if _dispatch(_work, name, pool):
        return True
    # 线程/池投递失败 (极端环境): 回退同步执行, 保证任务不丢
    _dec_pending()
    try:
        result = fn()
    except Exception as ee:
        if on_error is not None:
            on_error(ee)
            return False
        raise
    if on_done is not None:
        on_done(result)
    return False


def submit_chunked(iterable, chunk_fn, on_chunk, chunk_size=200,
                   on_done=None, on_error=None, name="", priority=PRIO_LOW,
                   token=None, pool="compute"):
    """把长任务按 chunk_size 切片处理, 每片结果回主线程 `on_chunk(partial)`。

    用途 (v5 §2.2 B7): 「5000 行一次性回主线程 → 主线程一次插 51 ms」的拆解 ——
    每批 ≤200 行, 主线程每次 on_chunk 的插入量有上界 (≤5 ms)。
    顺序保证: worker 串行切片、按序入队, 主线程单哨兵 FIFO 出队 → 分块顺序与
    iterable 一致。token 在**每批之前**检查, 取消后不再产出后续批次。

    返回 True (异步) / False (同步回退: 无 GUI 事件循环时当场逐批回调)。
    """
    global _PENDING
    try:
        chunk_size = max(1, int(chunk_size))
    except Exception:
        chunk_size = 200
    prio = _norm_prio(priority) if BG_TASK_V2 else PRIO_NORMAL

    def _chunks():
        buf = []
        for x in iterable:
            buf.append(x)
            if len(buf) >= chunk_size:
                yield buf
                buf = []
        if buf:
            yield buf

    if not is_started():
        # 同步退化: 逐批执行 + 逐批回调 (测试/CLI 语义与 submit 一致)
        try:
            for chunk in _chunks():
                if token is not None and token.is_cancelled():
                    break
                on_chunk(chunk_fn(chunk))
        except Exception as e:
            if on_error is not None:
                on_error(e)
                return False
            raise
        if on_done is not None:
            on_done(None)
        return False

    with _LOCK:
        _PENDING += 1

    def _work():
        epoch = _EPOCH
        try:
            last = None
            for chunk in _chunks():
                if token is not None and token.is_cancelled():
                    _dec_pending()
                    return
                partial = chunk_fn(chunk)
                last = partial
                with _LOCK:
                    _PENDING += 1      # 每批结果各自计一次待回调
                if not _enqueue((on_chunk, None, True, partial, name, None,
                                 epoch, token), prio):
                    return
            if on_done is not None:
                with _LOCK:
                    _PENDING += 1
                _enqueue((on_done, None, True, last, name, None, epoch, token), prio)
        except Exception as e:
            tb = traceback.format_exc()
            _enqueue((None, on_error, False, e, name, tb, epoch, token), PRIO_HIGH)
        finally:
            _dec_pending()             # 任务本身完成
            with _LOCK:
                _THREADS.discard(threading.current_thread())

    if _dispatch(_work, name, pool):
        return True
    _dec_pending()
    return False
