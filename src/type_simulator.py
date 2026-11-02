#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
type_simulator.py
  - TypeSimulator.Utilities.KeyboardSimulator : SendInput 按键构造与发送
  - TypeSimulator.Utilities.RandomDelay       : 人类化延迟抖动
  - TypeSimulator.Models.TypeSimulatorEngine  : CPM 速度换算、DispatcherTimer 驱动循环
  - TypeSimulator.Models.TextProcessor        : 文本规范化

与原始实现的关键对齐点：
  1. 普通字符（含中文）走 KEYEVENTF_UNICODE，扫描码 = UTF-16 码元，虚拟键码 = 0；
     Tab 用 VK_TAB(9)、换行用 VK_RETURN(13)，均以虚拟键码方式发送（flags=0）。
  2. 每次击键 = 一次 SendInput 提交「按下 + 抬起」两个 INPUT。
  3. 速度换算 base = 60000 / CPM 毫秒；单 tick（10 ms）最多输出 4 个字符，
     若已落后于计划时间则重置节拍，避免雪崩式追赶。
  4. 抖动：0.7–1.3 倍基础延迟；5% 概率追加 300–800 ms 停顿；
     `. , ; ! ?` 与换行追加 100–400 ms。

用法：
    python type_simulator.py "你好，世界" --speed 300 --humanize --start-delay 3
    python type_simulator.py --file note.md --speed 600
    echo "hello" | python type_simulator.py --dry-run

注意：SendInput 只向**当前拥有键盘焦点**的窗口投递输入，
请用 --start-delay 预留切换焦点的时间。以管理员权限运行的窗口（UIPI）无法接收，
此时需要以同等或更高权限运行本脚本。
"""

from __future__ import annotations

import argparse
import ctypes
import math
import os
import random
import sys
import threading
import time
from ctypes import wintypes
from typing import Callable, List, Optional, Sequence

__all__ = [
    "KeyboardSimulator",
    "RandomDelay",
    "TypeSimulatorEngine",
    "TextProcessor",
    "type_text",
    "INPUT",
    "SendInputError",
]

IS_WINDOWS = os.name == "nt"

# ---------------------------------------------------------------------------
# Windows 输入 API
# ---------------------------------------------------------------------------

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
INPUT_HARDWARE = 2

KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004

VK_TAB = 0x09
VK_RETURN = 0x0D

# UIntPtr 宽度随进程位数变化，保证与系统 INPUT 结构布局一致
_UINTPTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_uint),
        ("time", ctypes.c_uint),
        ("dwExtraInfo", _UINTPTR),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_uint),
        ("dwFlags", ctypes.c_uint),
        ("time", ctypes.c_uint),
        ("dwExtraInfo", _UINTPTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", ctypes.c_uint),
        ("wParamL", ctypes.c_ushort),
        ("wParamH", ctypes.c_ushort),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    """对应 C# 中的 Input 结构（Type + InputUnion）。"""

    _anonymous_ = ("u",)
    _fields_ = [("type", ctypes.c_uint), ("u", _INPUTUNION)]


_user32 = None
_SendInput = None


def _load_user32() -> None:
    """惰性加载 user32.dll 与 SendInput，便于在非 Windows 下以演练模式运行。"""
    global _user32, _SendInput
    if _SendInput is not None:
        return
    if not IS_WINDOWS:
        raise SendInputError("SendInput 仅适用于 Windows；请改用演练模式或 Windows 环境。")
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    fn = _user32.SendInput
    fn.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
    fn.restype = wintypes.UINT
    _SendInput = fn


class SendInputError(OSError):
    """SendInput 调用失败或环境不支持。"""


# ---------------------------------------------------------------------------
# KeyboardSimulator —— 对应 TypeSimulator.Utilities.KeyboardSimulator
# ---------------------------------------------------------------------------


class KeyboardSimulator:
    """构造并投递键盘输入事件（SendInput）。"""

    _lock = threading.RLock()
    simulation_starting: Optional[Callable[[], None]] = None
    simulation_completed: Optional[Callable[[], None]] = None

    # ---- 输入构造 -------------------------------------------------------

    @staticmethod
    def create_pair(virtual_key: int, scan_code: int, flags: int) -> List[INPUT]:
        """生成「按下 + 抬起」一对 INPUT，对应 C# CreatePair。"""
        down = INPUT()
        down.type = INPUT_KEYBOARD
        down.ki.wVk = virtual_key & 0xFFFF
        down.ki.wScan = scan_code & 0xFFFF
        down.ki.dwFlags = flags
        down.ki.time = 0
        down.ki.dwExtraInfo = 0

        up = INPUT()
        up.type = INPUT_KEYBOARD
        up.ki.wVk = virtual_key & 0xFFFF
        up.ki.wScan = scan_code & 0xFFFF
        up.ki.dwFlags = flags | KEYEVENTF_KEYUP
        up.ki.time = 0
        up.ki.dwExtraInfo = 0
        return [down, up]

    @classmethod
    def create_inputs(cls, character: str) -> List[INPUT]:
        """对应 C# CreateInputs：Tab/换行走虚拟键，其余走 Unicode 扫描码。"""
        if len(character) != 1:
            raise ValueError("create_inputs 只接受单个字符")
        if character == "\t":
            return cls.create_pair(VK_TAB, 0, 0)
        if character in ("\n", "\r"):
            return cls.create_pair(VK_RETURN, 0, 0)
        inputs: List[INPUT] = []
        # C# 的 char 是 UTF-16 码元；非 BMP 字符（如 emoji）会被拆成代理对逐次发送
        raw = character.encode("utf-16-le")
        for i in range(0, len(raw), 2):
            unit = int.from_bytes(raw[i : i + 2], "little")
            inputs.extend(cls.create_pair(0, unit, KEYEVENTF_UNICODE))
        return inputs

    # ---- 发送 -----------------------------------------------------------

    @classmethod
    def send(cls, inputs: Sequence[INPUT]) -> None:
        """对应 C# Send：加锁、触发开始/结束通知、校验返回值。"""
        _load_user32()
        with cls._lock:
            try:
                if cls.simulation_starting is not None:
                    cls.simulation_starting()
                count = len(inputs)
                if count == 0:
                    return
                array = (INPUT * count)(*inputs)
                sent = _SendInput(count, array, ctypes.sizeof(INPUT))
                if sent != count:
                    raise ctypes.WinError(ctypes.get_last_error())
            finally:
                if cls.simulation_completed is not None:
                    cls.simulation_completed()

    @classmethod
    def press_key(cls, key_code: int) -> None:
        """按下并释放一个虚拟键码（对应 C# PressKey）。"""
        cls.send(cls.create_pair(key_code, 0, 0))

    @classmethod
    def type_character(cls, character: str) -> None:
        """输入单个字符（对应 C# TypeCharacter）。"""
        cls.send(cls.create_inputs(character))

    @classmethod
    def type_text(cls, text: str, delay_between_chars_ms: int = 0) -> None:
        """连续输入整段文本（对应 C# TypeText）。"""
        if not text:
            return
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        with cls._lock:
            for ch in text:
                cls.type_character(ch)
                if delay_between_chars_ms > 0:
                    time.sleep(delay_between_chars_ms / 1000.0)


# ---------------------------------------------------------------------------
# RandomDelay —— 对应 TypeSimulator.Utilities.RandomDelay
# ---------------------------------------------------------------------------


class RandomDelay:
    """人类化延迟：让输入节奏不机械。"""

    _random = random.Random()
    _lock = threading.RLock()
    _SPECIAL_CHARS = ("\n", ".", ",", ";", "!", "?")

    @classmethod
    def calculate_next_delay(cls, base_delay_ms: int) -> int:
        """0.7–1.3 倍抖动，5% 概率长停顿，下限 10 ms。"""
        if base_delay_ms <= 0:
            return 50
        with cls._lock:
            try:
                factor = 0.7 + cls._random.random() * 0.6
                delay = int(base_delay_ms * factor)
                if cls._random.randrange(100) < 5:
                    delay += cls._random.randrange(300, 800)
                return max(delay, 10)
            except Exception:
                return base_delay_ms

    @classmethod
    def calculate_special_char_delay(cls, ch: str, base_delay_ms: int) -> int:
        """标点与换行追加 100–400 ms 停顿。"""
        with cls._lock:
            try:
                if ch in cls._SPECIAL_CHARS:
                    return base_delay_ms + cls._random.randrange(100, 400)
                return cls.calculate_next_delay(base_delay_ms)
            except Exception:
                return base_delay_ms

    @classmethod
    def execute(cls, base_delay_ms: int) -> None:
        time.sleep(cls.calculate_next_delay(base_delay_ms) / 1000.0)


# ---------------------------------------------------------------------------
# TextProcessor —— 对应 TypeSimulator.Models.TextProcessor
# ---------------------------------------------------------------------------


class TextProcessor:
    @staticmethod
    def process_text(input_text: str) -> str:
        """规范化换行，并去除每行行尾空白。"""
        if not input_text:
            return ""
        text = input_text.replace("\r\n", "\n").replace("\r", "\n")
        lines = text.split("\n")
        return "\n".join(line.rstrip() for line in lines)

    @staticmethod
    def prepare_for_key_mapping(text: str) -> List[str]:
        """剔除控制字符（保留 \\n 与 \\t），返回字符序列。"""
        if not text:
            return []
        return [
            ch
            for ch in text
            if ch in ("\n", "\t") or not (ord(ch) < 0x20 or ord(ch) == 0x7F)
        ]


# ---------------------------------------------------------------------------
# 高精度计时（对应 System.Diagnostics.Stopwatch，支持暂停/恢复）
# ---------------------------------------------------------------------------


class _Stopwatch:
    def __init__(self) -> None:
        self._accumulated = 0.0
        self._t0: Optional[float] = None

    def restart(self) -> None:
        self._accumulated = 0.0
        self._t0 = time.perf_counter()

    def start(self) -> None:
        if self._t0 is None:
            self._t0 = time.perf_counter()

    def stop(self) -> None:
        if self._t0 is not None:
            self._accumulated += time.perf_counter() - self._t0
            self._t0 = None

    @property
    def elapsed_ms(self) -> float:
        total = self._accumulated
        if self._t0 is not None:
            total += time.perf_counter() - self._t0
        return total * 1000.0


# ---------------------------------------------------------------------------
# TypeSimulatorEngine —— 对应 TypeSimulator.Models.TypeSimulatorEngine
# ---------------------------------------------------------------------------


class TypeSimulatorEngine:
    """CPM 驱动的打字引擎。

    以 10 ms 节拍推进（原实现为 DispatcherTimer），单节拍最多输出 4 个字符。
    """

    MINIMUM_SPEED = 10
    MAXIMUM_SPEED = 6000
    _MAX_CHARACTERS_PER_TICK = 4
    _TICK_INTERVAL = 0.01

    def __init__(self, type_character: Optional[Callable[[str], None]] = None) -> None:
        self._type_character: Callable[[str], None] = (
            type_character or KeyboardSimulator.type_character
        )
        self._text_to_type = ""
        self._typing_speed = 300
        self._enable_random_delay = False
        self._start_delay_seconds = 0
        self._current_position = 0
        self._next_character_at = 0.0
        self._clock = _Stopwatch()
        self._lock = threading.RLock()
        self._thread: Optional[threading.Thread] = None

        self.is_typing = False
        self.is_paused = False

        # 事件回调
        self.on_character_typed: Optional[Callable[[str], None]] = None
        self.on_typing_completed: Optional[Callable[[], None]] = None
        self.on_typing_failed: Optional[Callable[[BaseException], None]] = None
        self.on_start_delay_changed: Optional[Callable[[int], None]] = None

    # ---- 状态属性 -------------------------------------------------------

    @property
    def characters_typed(self) -> int:
        return self._current_position

    @property
    def total_characters(self) -> int:
        return len(self._text_to_type)

    @property
    def remaining_delay_seconds(self) -> int:
        if not self.is_typing or self._current_position != 0:
            return 0
        delta = self._next_character_at - self._clock.elapsed_ms
        return max(0, int(math.ceil(delta / 1000.0)))

    # ---- 控制 -----------------------------------------------------------

    def configure(
        self,
        text: str,
        speed: int = 300,
        enable_random_delay: bool = False,
        start_delay_seconds: int = 0,
    ) -> None:
        if text is None:
            raise ValueError("text 不能为 None")
        if not (self.MINIMUM_SPEED <= speed <= self.MAXIMUM_SPEED):
            raise ValueError(
                f"速度范围为 {self.MINIMUM_SPEED}–{self.MAXIMUM_SPEED} CPM，当前为 {speed}。"
            )
        if not (0 <= start_delay_seconds <= 60):
            raise ValueError("start_delay_seconds 必须在 0–60 之间。")
        if self.is_typing:
            raise RuntimeError("请先停止当前打字任务。")
        self._text_to_type = text.replace("\r\n", "\n").replace("\r", "\n")
        self._typing_speed = speed
        self._enable_random_delay = enable_random_delay
        self._start_delay_seconds = start_delay_seconds
        self._current_position = 0

    def start(self) -> None:
        with self._lock:
            if self.is_typing:
                return
            if self.total_characters == 0:
                raise RuntimeError("没有可打字的文本")
            self._current_position = 0
            self._next_character_at = float(self._start_delay_seconds) * 1000.0
            self.is_typing = True
            self.is_paused = False
            self._clock.restart()
            if self.on_start_delay_changed is not None:
                self.on_start_delay_changed(self.remaining_delay_seconds)
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()

    def pause(self) -> None:
        with self._lock:
            if self.is_typing and not self.is_paused:
                self.is_paused = True
                self._clock.stop()

    def resume(self) -> None:
        with self._lock:
            if self.is_typing and self.is_paused:
                self.is_paused = False
                self._clock.start()

    def stop(self) -> None:
        with self._lock:
            self.is_typing = False
            self.is_paused = False
            self._clock.stop()

    def wait(self, poll_interval: float = 0.05) -> None:
        """阻塞直到打字结束。"""
        while self.is_typing:
            time.sleep(poll_interval)

    # ---- 内部循环 -------------------------------------------------------

    def _run(self) -> None:
        while self.is_typing:
            self._tick()
            time.sleep(self._TICK_INTERVAL)

    def _tick(self) -> None:
        if not self.is_typing or self.is_paused:
            return
        try:
            if self._current_position == 0 and self.on_start_delay_changed is not None:
                self.on_start_delay_changed(self.remaining_delay_seconds)

            elapsed = self._clock.elapsed_ms
            emitted = 0
            while (
                self.is_typing
                and not self.is_paused
                and elapsed >= self._next_character_at
                and emitted < self._MAX_CHARACTERS_PER_TICK
            ):
                ch = self._text_to_type[self._current_position]
                self._type_character(ch)
                self._current_position += 1
                emitted += 1

                interval = 60000.0 / self._typing_speed
                if self._enable_random_delay:
                    interval = RandomDelay.calculate_special_char_delay(
                        ch, max(1, int(interval))
                    )
                self._next_character_at += interval

                if self.on_character_typed is not None:
                    self.on_character_typed(ch)

                if not self.is_typing:
                    return
                if self._current_position == len(self._text_to_type):
                    self.stop()
                    if self.on_typing_completed is not None:
                        self.on_typing_completed()
                    return

            if emitted == self._MAX_CHARACTERS_PER_TICK and elapsed >= self._next_character_at:
                # 落后于计划：重置节拍，避免无节制追赶
                self._next_character_at = elapsed + 60000.0 / self._typing_speed
        except BaseException as exc:  # noqa: BLE001 —— 与原始实现一致：出错即暂停
            self.pause()
            if self.on_typing_failed is not None:
                self.on_typing_failed(exc)


# ---------------------------------------------------------------------------
# 便捷函数
# ---------------------------------------------------------------------------


def type_text(text: str, speed: int = 300, humanize: bool = False,
              start_delay_seconds: int = 0, block: bool = True) -> TypeSimulatorEngine:
    """一次性输入整段文本，返回引擎实例以便中途暂停/停止。"""
    engine = TypeSimulatorEngine()
    engine.configure(text, speed, humanize, start_delay_seconds)
    engine.start()
    if block:
        engine.wait()
    return engine


# ---------------------------------------------------------------------------
# 命令行
# ---------------------------------------------------------------------------


def _read_source(args: argparse.Namespace) -> str:
    if args.file:
        with open(args.file, "r", encoding=args.encoding) as fh:
            return fh.read()
    if args.text_opt is not None:
        return args.text_opt
    if args.text is not None:
        return args.text
    if not sys.stdin.isatty():
        return sys.stdin.read()
    raise SystemExit("未提供文本：请使用位置参数、--file，或从标准输入管道读入。")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="type_simulator",
        description="模拟真人打字（SendInput / Unicode），由 TypeSimulator.exe 复刻。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例：python type_simulator.py \"你好，世界\" --speed 300 --humanize --start-delay 3",
    )
    parser.add_argument("text", nargs="?", help="要输入的文本；省略则从标准输入读取")
    parser.add_argument("-t", "--text", dest="text_opt",
                        help="要输入的文本（与位置参数等价）")
    parser.add_argument("-f", "--file", help="从文本文件读取内容")
    parser.add_argument("--encoding", default="utf-8", help="文件编码（默认 utf-8）")
    parser.add_argument("-s", "--speed", type=int, default=300,
                        help="打字速度 CPM（字符/分钟），范围 10–6000，默认 300")
    parser.add_argument("--humanize", action="store_true",
                        help="启用人类化随机延迟抖动（标点停顿、偶发长停顿）")
    parser.add_argument("--start-delay", type=int, default=3,
                        help="开始前倒计时秒数（0–60），用于切换到目标窗口，默认 3")
    parser.add_argument("--process", action="store_true",
                        help="预处理文本：规范化换行并去除每行行尾空白")
    parser.add_argument("--strip-control", action="store_true",
                        help="剔除控制字符（保留换行与制表符）")
    parser.add_argument("--dry-run", action="store_true",
                        help="演练：不发送真实按键，仅打印时序（可用于验证与非 Windows 环境）")
    args = parser.parse_args(argv)

    text = _read_source(args)
    if args.process:
        text = TextProcessor.process_text(text)
    if args.strip_control:
        text = "".join(TextProcessor.prepare_for_key_mapping(text))
    if not text:
        raise SystemExit("文本为空，无需输入。")

    # 演练模式：仅记录，不调用 SendInput
    typed: List[str] = []
    if args.dry_run:
        def action(ch: str) -> None:
            typed.append(ch)
    else:
        action = KeyboardSimulator.type_character

    engine = TypeSimulatorEngine(action)
    try:
        engine.configure(text, args.speed, args.humanize, args.start_delay)
    except ValueError as exc:
        raise SystemExit(f"参数错误：{exc}")

    print(f"字符数 {engine.total_characters}｜速度 {args.speed} CPM｜"
          f"抖动 {'开' if args.humanize else '关'}｜"
          f"{'演练模式（不发送按键）' if args.dry_run else '真实发送'}")

    started = time.perf_counter()
    engine.start()
    try:
        while engine.is_typing:
            remaining = engine.remaining_delay_seconds
            if remaining > 0:
                print(f"\r{remaining} 秒后开始…请切换到目标窗口   ",
                      end="", flush=True)
            else:
                done = engine.characters_typed
                total = engine.total_characters
                print(f"\r进度 {done}/{total}（{done * 100 // max(total, 1)}%）   ",
                      end="", flush=True)
            time.sleep(0.1)
    except KeyboardInterrupt:
        engine.stop()
        print("\n已取消。")
        return 130

    elapsed = time.perf_counter() - started
    print(f"\r完成：{engine.characters_typed}/{engine.total_characters} 字符，"
          f"用时 {elapsed:.1f} 秒。")
    if args.dry_run:
        preview = "".join(typed)
        if len(preview) > 60:
            preview = preview[:60] + "…"
        print(f"演练内容预览：{preview!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
