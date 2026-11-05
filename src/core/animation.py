# -*- coding: utf-8 -*-
"""core.animation — 轻量动画调度器 (P2-1, 借鉴 maliang animation/Animation + controllers)。

纯 Python 实现, **不 import tkinter**: 动画运行时通过鸭子类型的 ``root.after`` /
``root.after_cancel`` 调度, 故本模块可在无 tkinter 的解释器里导入与单测。

设计对齐 maliang:
  · ``Animation(duration, command, *, controller, end, fps, repeat, repeat_delay,
    derivation, root)`` —— command 每帧回调; controller 缓动; end 结束回调;
    repeat/repeat_delay 循环。
  · ``derivation=False`` (默认): command 收「绝对进度」p = controller(t) ∈ [0,1],
    用于颜色渐变 / 透明度; ``derivation=True``: command 收「增量进度」(相邻帧进度差),
    用于 move 类逐帧位移。
  · 「减少动效」: ``start()`` 时若 reduce_motion 为真, 立即同步跳到终点
    (``command(1.0)`` + ``end()``), 不调度任何 after()。

真源约定 (P2-1):
  · 时长/帧间隔 token (``motion()``/``motion_frame()``) 与 ``reduce_motion()`` 仍留在
    ``utils.py``; UI 层构造 Animation 时把 token 映射为参数
    (如 ``fps=1000 // motion_frame()``、``duration=motion("motion_base")``、
    ``reduce_motion=utils.reduce_motion``), 本模块保持纯函数、可单测、可复用。
"""

from __future__ import annotations

__all__ = (
    "linear", "out_quad", "out_cubic", "in_out", "CONTROLLERS", "Animation",
)

from collections.abc import Callable


# ── 缓动控制器 (纯函数 t∈[0,1] → [0,1], 端点 0/1, 单调不减) ──────────────────

def linear(t: float) -> float:
    """匀速。"""
    return t


def out_quad(t: float) -> float:
    """先快后慢 (二次减速)。"""
    return 1.0 - (1.0 - t) * (1.0 - t)


def out_cubic(t: float) -> float:
    """先快后慢 (三次减速, 默认)。"""
    return 1.0 - (1.0 - t) ** 3


def in_out(t: float) -> float:
    """先慢后快再慢 (ease-in-out 二次)。"""
    if t < 0.5:
        return 2.0 * t * t
    return 1.0 - ((-2.0 * t + 2.0) ** 2) / 2.0


CONTROLLERS: dict[str, Callable[[float], float]] = {
    "linear": linear,
    "out_quad": out_quad,
    "out_cubic": out_cubic,
    "in_out": in_out,
}


class Animation:
    """轻量动画调度器 (借鉴 maliang ``animation.Animation``)。

    用法::

        Animation(200, lambda p: canvas.itemconfigure(i, fill=blend(c1, c2, p)),
                  controller=out_cubic, end=on_done, root=root).start()

    ``command`` 回调签名依 ``derivation`` 而定:
      · ``derivation=False`` (默认): ``command(p)``, p = controller(进度) ∈ [0,1]。
      · ``derivation=True``: ``command(delta)``, delta = 相邻帧进度差 (逐帧增量)。
    """

    def __init__(self, duration, command, *, controller=out_cubic, end=None,
                 fps=60, repeat=0, repeat_delay=0, derivation=False,
                 root=None, reduce_motion=None):
        """
        Args:
            duration: 单次动画时长 (ms, 负值按 0)。
            command: 每帧回调。
            controller: 缓动函数 (linear/out_quad/out_cubic/in_out)。
            end: 单次动画结束回调 (on_done)。
            fps: 帧率 (默认 60 ≈ TOKENS["motion_frame"]=16ms)。
            repeat: 额外重复次数 (0=只播一次)。
            repeat_delay: 两次动画之间的间隔 (ms)。
            derivation: command 是否收「增量进度」。
            root: 拥有 after/after_cancel 的根控件 (tkinter.Tk / Widget)。
            reduce_motion: 可选 ``() -> bool``; 缺省时惰性读 ``state.REDUCE_MOTION``。
        """
        self.duration = max(0, int(duration))
        self.command = command
        self.controller = controller
        self.end = end
        self.fps = max(1, int(fps))
        self.repeat = max(0, int(repeat))
        self.repeat_delay = max(0, int(repeat_delay))
        self.derivation = bool(derivation)
        self.root = root
        self._reduce_motion = reduce_motion

        frame_ms = max(1, 1000 // self.fps)
        if self.duration <= 0:
            self._frame_ms, self._total_frames, self._leave_ms = 0, 1, 0
        elif frame_ms <= self.duration:
            self._total_frames, self._leave_ms = divmod(self.duration, frame_ms)
            self._frame_ms = frame_ms
        else:
            self._frame_ms, self._total_frames, self._leave_ms = self.duration, 1, 0

        self._tasks: list = []
        self._count = self.repeat

    # ── 状态 ────────────────────────────────────────────────────────────────
    @property
    def active(self) -> bool:
        """是否仍有已调度任务。"""
        return bool(self._tasks)

    @property
    def count(self) -> int:
        """剩余循环次数。"""
        return self._count

    @property
    def total_frames(self) -> int:
        """单次动画总帧数。"""
        return self._total_frames

    # ── 减少动效判定 ───────────────────────────────────────────────────────
    def _reduce_motion_enabled(self) -> bool:
        if self._reduce_motion is not None:
            try:
                return bool(self._reduce_motion())
            except Exception:
                return False
        try:
            import state
            return bool(getattr(state, "REDUCE_MOTION", False))
        except Exception:
            return False

    # ── 启动 / 停止 ────────────────────────────────────────────────────────
    def start(self, *, delay: int = 0):
        """启动动画。``delay>0`` 时返回延迟任务的 id, 否则返回 None。"""
        if self._reduce_motion_enabled():
            # 减少动效: 一步到位, 不调度 after()。
            self.command(1.0)
            if self.end is not None:
                self.end()
            self._count = self.repeat
            return None

        if delay > 0:
            return self._require_root().after(delay, self.start)

        root = self._require_root()
        delay_ms = 0
        last = 0.0
        for i in range(1, self._total_frames + 1):
            delay_ms += self._frame_ms + (1 if i < self._leave_ms else 0)
            p = self.controller(i / self._total_frames)
            value = (p - last) if self.derivation else p
            self._tasks.append(root.after(delay_ms, self.command, value))
            if self.derivation:
                last = p

        if self.end is not None:
            self._tasks.append(root.after(delay_ms, self.end))
        self._tasks.append(root.after(delay_ms, self._repeat))
        return None

    def stop(self, *, delay: int = 0):
        """停止动画, 取消所有已调度任务; 循环计数复位。"""
        if delay > 0:
            return self._require_root().after(delay, self.stop)
        if self.root is not None:
            while self._tasks:
                self.root.after_cancel(self._tasks.pop())
        else:
            self._tasks.clear()
        self._count = self.repeat
        return None

    def skip(self, count: int = 1) -> None:
        """跳过若干次循环。"""
        self._count = max(self._count - int(count), 0)

    # ── 内部 ────────────────────────────────────────────────────────────────
    def _require_root(self):
        if self.root is None:
            raise RuntimeError("Animation 需要 root (提供 after/after_cancel)")
        return self.root

    def _repeat(self) -> None:
        self._tasks.clear()
        if self._count != 0:
            self._count -= 1
            if self.repeat_delay > 0:
                task = self.start(delay=self.repeat_delay)
                if task is not None:
                    self._tasks.append(task)
            else:
                self.start()
        else:
            self._count = self.repeat
