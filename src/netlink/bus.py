"""ACRPA NetLink 事件总线 — 线程安全订阅/发布 + 主线程消费队列（零 UI 依赖）。

两条路径（为什么分开）：
  * publish = 立即同步通知订阅者（网络/Agent 线程用），内部先 post 一份，
    使 UI 线程也能通过 drain 拿到同一事件；
  * post    = 只入队，等 UI 线程 drain（跨线程送 UI 用）。
任何订阅者抛错都不得影响其它订阅者，也绝不向上传播。
"""
import queue
import threading


class NetBus(object):
    """线程安全事件总线：UI 与 Agent 都从这里取数据。"""

    def __init__(self):
        self._lock = threading.RLock()
        self._subs = {}    # {topic: [callbacks]}  同一 callback 只登记一次
        self._q = queue.Queue()

    # ── 主题订阅（UI/Agent 内部消费）──
    def subscribe(self, topic, callback):
        """callback(topic, payload_dict)。同一 callback 重复订阅只登记一次。"""
        try:
            with self._lock:
                lst = self._subs.get(topic)
                if lst is None:
                    lst = []
                    self._subs[topic] = lst
                if callback not in lst:
                    lst.append(callback)
        except Exception:
            pass

    def unsubscribe(self, topic, callback):
        try:
            with self._lock:
                lst = self._subs.get(topic)
                if lst:
                    self._subs[topic] = [cb for cb in lst if cb is not callback]
        except Exception:
            pass

    def unsubscribe_all(self, callback):
        try:
            with self._lock:
                for topic in list(self._subs.keys()):
                    self._subs[topic] = [cb for cb in self._subs[topic]
                                         if cb is not callback]
        except Exception:
            pass

    # ── 生产者发布（任意线程可调）──
    def publish(self, topic, payload):
        """线程安全 + 整体 try/except；单个订阅者抛错不影响其它订阅者，绝不向上抛。"""
        # 先入队一份，保证 UI 线程 drain 也能拿到
        self.post(topic, payload)
        try:
            with self._lock:
                callbacks = list(self._subs.get(topic, ()))
        except Exception:
            callbacks = []
        for cb in callbacks:
            try:
                cb(topic, payload)
            except Exception:
                pass

    def post(self, topic, payload):
        """把事件投进队列（供 UI 线程 drain 的路径）。"""
        try:
            self._q.put((topic, payload))
        except Exception:
            pass

    # ── 主线程消费口（UI 用 root.after 轮询）──
    def drain(self, max_items=500):
        """返回并清空事件队列 → [(topic, payload), ...]，最多 max_items 条。"""
        out = []
        try:
            while len(out) < max_items:
                try:
                    out.append(self._q.get_nowait())
                except queue.Empty:
                    break
        except Exception:
            pass
        return out
