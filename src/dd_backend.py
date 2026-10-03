"""
DD Backend Adapter - Optional high-performance input backend for ACRPA

This module provides an adapter layer for DD (DirectDraw) driver, enabling:
- Kernel-level mouse/keyboard simulation (harder to detect)
- Background window operations (no need to activate windows)
- Faster text input via DD_str API
- Relative mouse movement support

Usage:
    from dd_backend import DDBackend

    dd = DDBackend()
    if dd.enabled:
        dd.type_text("Hello World", mode="direct")
    else:
        # Fallback to PyAutoGUI
        pass

Note: Requires dd.54900.dll in project directory and admin privileges.
"""

import os
import ctypes
import hashlib
from typing import Optional
from utils import log1


class DDError(Exception):
    """DD Driver operation error"""
    pass


class DDInitializationError(DDError):
    """DD Driver initialization failed"""
    pass


class DDBackend:
    """
    DD Driver Backend Adapter

    Provides a unified interface for DD driver operations with automatic
    fallback handling when the driver is unavailable.
    """

    # Mouse button codes (DD driver standard)
    MOUSE_BUTTONS = {
        "left_down": 1,
        "left_up": 2,
        "right_down": 4,
        "right_up": 8,
        "middle_down": 16,
        "middle_up": 32,
    }

    def __init__(self, dll_path: str = None, allow_load: bool = True):
        """
        Initialize DD Backend

        Args:
            dll_path: Path to dd.XXXXX.dll file. If None, searches common locations.
            allow_load: False 时完全不碰 DLL（用户未启用内核驱动），只置 enabled=False。
        """
        self.enabled = False
        self.dd_dll = None
        self.dll_path = ""
        self.disabled_reason = ""

        if not allow_load:
            self.disabled_reason = "用户未启用 DD 内核驱动"
            return

        explicit = bool(dll_path)
        # Auto-detect DLL path
        if dll_path is None:
            dll_path = self._find_dd_dll()

        if not dll_path:
            log1("⚠ DD驱动DLL未找到，将使用PyAutoGUI后端", "warning")
            self.disabled_reason = "未找到 DLL"
            return

        ok, why = self._preflight(dll_path, explicit=explicit)
        if not ok:
            log1("⚠ DD驱动未通过加载前预检, 不加载: {}".format(why), "error")
            self.disabled_reason = why
            return
        self.dll_path = dll_path
        log1("[o] DD驱动预检通过: {} ({})".format(os.path.basename(dll_path), why))

        try:
            # Load DD driver DLL
            self.dd_dll = ctypes.windll.LoadLibrary(dll_path)

            # Setup DD_str function signature
            self.dd_dll.DD_str.argtypes = [ctypes.c_char_p]
            self.dd_dll.DD_str.restype = ctypes.c_int

            # Initialize driver
            result = self.dd_dll.DD_btn(0)
            if result == 1:
                self.enabled = True
                log1("✔ DD驱动初始化成功 (内核级输入后端已启用)", "success")
            else:
                log1("⚠ DD驱动初始化失败，回退到PyAutoGUI", "warning")
                self.disabled_reason = "驱动初始化返回 {}".format(result)

        except Exception as e:
            log1(f"⚠ DD驱动加载错误: {e}，回退到PyAutoGUI", "warning")
            self.enabled = False
            self.disabled_reason = "加载异常: {}".format(e)

    def _preflight(self, path: str, explicit: bool = False):
        """加载前的**静态**预检（不加载 DLL）→ (ok, 说明)。

        为什么不是"子进程预加载"（paddle_dll 的做法）：DD 是**内核驱动**，加载会安装/
        启动系统服务；在临时子进程里试跑可能留下副作用，且二次加载语义不明。这里只做
        无副作用的校验：文件存在 / PE 头 / 体积下限 / 安装目录约束 / 可选 sha256 固定。
        """
        try:
            import state as _state
        except Exception:
            _state = None
        try:
            if not os.path.isfile(path):
                return False, "文件不存在: {}".format(path)
            if os.path.getsize(path) < 4096:
                return False, "文件过小({} 字节), 疑似占位或损坏".format(os.path.getsize(path))
            with open(path, "rb") as f:
                if f.read(2) != b"MZ":
                    return False, "不是有效的 PE 文件 (缺少 MZ 头)"

            install_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
            inside = os.path.abspath(path).lower().startswith(install_root.lower())
            if not inside:
                if explicit:
                    # 用户显式指定的路径予以尊重, 但必须留痕 (可审计)
                    log1("⚠ DD驱动 DLL 位于安装目录之外, 请确认来源可信: {}".format(path),
                         "warning")
                else:
                    return False, "DLL 不在安装目录内, 拒绝加载 (防 DLL 植入)"

            digest = _sha256_file(path)
            expect = ""
            if _state is not None:
                expect = str(getattr(_state, "DD_DLL_SHA256", "") or "").strip().lower()
            if expect:
                if digest != expect:
                    return False, "sha256 与配置不符 (期望 {}… 实际 {}…)".format(
                        expect[:12], digest[:12])
                return True, "sha256 校验通过 ({})".format(digest[:12] + "…")
            return True, "sha256 {}… (如需固定, 把该值写入设置里的 dd_dll_sha256)".format(
                digest[:12])
        except Exception as e:
            return False, "预检异常: {}".format(e)

    def _find_dd_dll(self) -> Optional[str]:
        """
        Search for DD driver DLL in common locations

        Returns:
            DLL path if found, None otherwise
        """
        # 只在安装目录周边搜索。
        # 旧实现把 os.getcwd() 也纳入搜索 —— 那意味着"脚本目录里放一个同名
        # dd63330.dll" 就会被加载执行, 属于典型的 DLL 植入面, 已移除。
        base = os.path.dirname(os.path.abspath(__file__))
        search_paths = [
            os.path.join(base, "..", "lib", "dd_driver"),
            os.path.join(base, ".."),
        ]

        # Common DLL filenames (include dd63330.dll shipped in lib/dd_driver)
        dll_names = ["dd63330.dll", "dd.54900.dll", "dd.dll"]

        for base_path in search_paths:
            for dll_name in dll_names:
                full_path = os.path.join(base_path, dll_name)
                if os.path.exists(full_path):
                    log1(f"[o] 找到DD驱动DLL: {full_path}")
                    return full_path

        return None

    def type_text(self, text: str, mode: str = "auto") -> bool:
        """
        Input text using DD driver

        Args:
            text: Text to input
            mode: Input mode
                - "direct": Use DD_str API (fast, stable, ASCII only)
                - "simulate": Simulate keystrokes (supports Unicode but slower)
                - "auto": Auto-select best method

        Returns:
            True if successful, False if fell back to PyAutoGUI
        """
        if not self.enabled or not text:
            return False

        try:
            if mode == "direct":
                # Direct input via DD_str (fastest, most stable)
                # Only supports ASCII characters
                ascii_text = text.encode("ascii", errors="ignore").decode()
                if ascii_text:
                    self.dd_dll.DD_str(ascii_text.encode("utf-8"))
                    return True

            elif mode == "simulate":
                # Keystroke simulation (supports more characters)
                return self._type_by_keystroke(text)

            elif mode == "auto":
                # Smart selection: pure ASCII uses DD_str (fastest),
                # otherwise fall back to keystroke simulation which handles
                # Unicode via DD_str per-character. This avoids the previous
                # bug where partial ASCII was typed then the WHOLE text was
                # typed again by the PyAutoGUI fallback (duplicate chars).
                if all(ord(c) < 128 for c in text):
                    ascii_text = text.encode("ascii")
                    if ascii_text:
                        self.dd_dll.DD_str(ascii_text)
                        return True
                else:
                    return self._type_by_keystroke(text)

                return False

            return False

        except Exception as e:
            log1(f"DD文本输入错误: {e}", "error")
            return False

    def _type_by_keystroke(self, text: str, interval: float = 0.02) -> bool:
        """
        Simulate keystrokes for text input.

        Handles letters, digits, and printable ASCII punctuation via DD key codes.
        Non-ASCII characters are passed to DD_str as a best-effort fallback.

        Args:
            text: Text to type
            interval: Delay between keystrokes (seconds)

        Returns:
            True if successful
        """
        import time

        # ── DD key codes for US keyboard printable ASCII ──
        # Number row symbols (shifted digits)
        _SHIFT_DIGIT = {
            "!": 202, "@": 203, "#": 204, "$": 205, "%": 206,
            "^": 207, "&": 208, "*": 209, "(": 210, ")": 201,
        }
        # Punctuation keys (base key code, needs_shift flag)
        _PUNCT_BASE = {
            "`":  (223, False), "~":  (223, True),
            "-":  (212, False), "_":  (212, True),
            "=":  (213, False), "+":  (213, True),
            "[":  (405, False), "{":  (405, True),
            "]":  (406, False), "}":  (406, True),
            "\\": (407, False), "|":  (407, True),
            ";":  (408, False), ":":  (408, True),
            "'":  (409, False), "\"": (409, True),
            ",":  (410, False), "<":  (410, True),
            ".":  (411, False), ">":  (411, True),
            "/":  (412, False), "?":  (412, True),
        }

        # Named special keys
        _NAMED = {
            " ":  603,   # SPACE
            "\n": 313,   # ENTER
            "\r": 313,   # ENTER (CR)
            "\t": 300,   # TAB
            "\b": 214,   # BACKSPACE
        }

        def _press_release(code, need_shift):
            if need_shift:
                self.dd_dll.DD_key(500, 1)   # SHIFT down
                self.dd_dll.DD_key(code, 1)
                time.sleep(interval)
                self.dd_dll.DD_key(code, 2)
                self.dd_dll.DD_key(500, 2)   # SHIFT up
            else:
                self.dd_dll.DD_key(code, 1)
                time.sleep(interval)
                self.dd_dll.DD_key(code, 2)

        for char in text:
            try:
                if char in _NAMED:
                    _press_release(_NAMED[char], False)

                elif char.isalpha():
                    key_code = 401 + (ord(char.lower()) - ord("a"))
                    _press_release(key_code, char.isupper())

                elif char.isdigit():
                    key_code = 201 + int(char)
                    _press_release(key_code, False)

                elif char in _SHIFT_DIGIT:
                    _press_release(_SHIFT_DIGIT[char], True)

                elif char in _PUNCT_BASE:
                    code, need_shift = _PUNCT_BASE[char]
                    _press_release(code, need_shift)

                else:
                    # Non-ASCII or unmapped character -> try DD_str
                    self.dd_dll.DD_str(char.encode("utf-8", errors="ignore"))

            except Exception as e:
                log1(f"DD按键模拟错误 '{char}': {e}", "warning")
                continue

            time.sleep(interval)

        return True

    def mouse_move_relative(self, dx: int, dy: int) -> bool:
        """
        Move mouse relative to current position

        Args:
            dx: Horizontal offset
            dy: Vertical offset

        Returns:
            True if successful
        """
        if not self.enabled:
            return False

        try:
            self.dd_dll.DD_movR(dx, dy)
            return True
        except Exception as e:
            log1(f"DD相对移动错误: {e}", "error")
            return False

    def click_at(self, x: int, y: int, button: str = "left") -> bool:
        """
        Click at specified coordinates (can work in background)

        Args:
            x: X coordinate
            y: Y coordinate
            button: Button name ("left", "right", "middle")

        Returns:
            True if successful
        """
        if not self.enabled:
            return False

        try:
            # Move to position
            self.dd_dll.DD_mov(x, y)

            # Get button codes
            button_lower = button.lower()
            down_code = self.MOUSE_BUTTONS.get(f"{button_lower}_down", 1)
            up_code = self.MOUSE_BUTTONS.get(f"{button_lower}_up", 2)

            # Click
            self.dd_dll.DD_btn(down_code)
            self.dd_dll.DD_btn(up_code)

            return True
        except Exception as e:
            log1(f"DD点击错误: {e}", "error")
            return False

    def key_combination(self, *keys: str) -> bool:
        """
        Press key combination (e.g., Ctrl+C)

        Args:
            keys: Key names to press together

        Returns:
            True if successful
        """
        if not self.enabled:
            return False

        # Simple key name to code mapping
        key_codes = {
            "ctrl": 600,
            "shift": 500,
            "alt": 602,
            "win": 601,
            "enter": 313,
            "tab": 300,
            "space": 603,
        }

        try:
            # Press all keys
            for key in keys:
                code = key_codes.get(key.lower(), None)
                if code:
                    self.dd_dll.DD_key(code, 1)

            # Release in reverse order
            for key in reversed(keys):
                code = key_codes.get(key.lower(), None)
                if code:
                    self.dd_dll.DD_key(code, 2)

            return True
        except Exception as e:
            log1(f"DD组合键错误: {e}", "error")
            return False


# Singleton instance for easy access
_dd_instance: Optional[DDBackend] = None


def _sha256_file(path: str) -> str:
    """文件 sha256 (十六进制小写); 读失败返回空串。"""
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for blk in iter(lambda: f.read(1 << 20), b""):
                h.update(blk)
        return h.hexdigest()
    except Exception:
        return ""


def dd_allowed():
    """按用户设置判断是否允许启用 DD 内核驱动 → (allowed, reason)。

    这是**唯一**的策略判定点: engine 与任何未来调用方都应走这里, 避免各处各判一套。
    DD 是内核态驱动, 加载有系统级副作用, 因此默认（配置缺省）就是不允许。
    """
    try:
        import state
        mode = str(getattr(state, "INPUT_MODE", "") or "").strip().lower()
        flag = bool(getattr(state, "USE_DD_DRIVER", False))
    except Exception as e:
        return False, "读取配置失败, 保守起见不加载内核驱动: {}".format(e)
    if flag:
        return True, "use_dd_driver=True"
    if mode == "dd":
        return True, "input_mode=dd"
    return False, "input_mode={!r}, use_dd_driver={}".format(mode, flag)


def get_dd_backend(force: bool = False) -> DDBackend:
    """Get or create DD Backend singleton instance.

    force=False（缺省）时先过用户设置门禁: 未启用则**完全不碰 DLL**, 直接返回一个
    enabled=False 的实例（内核驱动的加载本身就有副作用, 不能被"顺手初始化"触发）。
    """
    global _dd_instance
    if _dd_instance is None:
        allowed, reason = dd_allowed()
        if not allowed and not force:
            log1("DD 内核驱动未启用, 跳过加载 ({})".format(reason))
            _dd_instance = DDBackend(allow_load=False)
            _dd_instance.disabled_reason = reason
        else:
            # 优先使用用户在设置页「高级设置」中配置的 DLL 路径
            try:
                import state
                dll = getattr(state, "DD_DLL_PATH", "") or None
            except Exception:
                dll = None
            _dd_instance = DDBackend(dll_path=dll)
    return _dd_instance


def reset_dd_backend():
    """Reset DD backend instance (useful for testing)"""
    global _dd_instance
    _dd_instance = None


# Test/demo
if __name__ == "__main__":
    print("Testing DD Backend...")
    dd = get_dd_backend()

    if dd.enabled:
        print("【OK】 DD Backend is enabled!")
        print("Testing text input...")
        dd.type_text("Hello from DD!", mode="direct")
    else:
        print("【ERROR】 Backend is not available")
        print("Please ensure dd.54900.dll is in the project directory")