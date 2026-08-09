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

    def __init__(self, dll_path: str = None):
        """
        Initialize DD Backend

        Args:
            dll_path: Path to dd.XXXXX.dll file. If None, searches common locations.
        """
        self.enabled = False
        self.dd_dll = None

        # Auto-detect DLL path
        if dll_path is None:
            dll_path = self._find_dd_dll()

        if not dll_path:
            log1("⚠️ DD驱动DLL未找到，将使用PyAutoGUI后端", "warning")
            return

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
                log1("✅ DD驱动初始化成功 (内核级输入后端已启用)", "success")
            else:
                log1("⚠️ DD驱动初始化失败，回退到PyAutoGUI", "warning")

        except Exception as e:
            log1(f"⚠️ DD驱动加载错误: {e}，回退到PyAutoGUI", "warning")
            self.enabled = False

    def _find_dd_dll(self) -> Optional[str]:
        """
        Search for DD driver DLL in common locations

        Returns:
            DLL path if found, None otherwise
        """
        # Search paths in order of preference
        search_paths = [
            os.path.join(os.path.dirname(__file__), "..", "lib", "dd_driver"),
            os.path.join(os.path.dirname(__file__), ".."),
            os.path.join(os.getcwd(), "lib", "dd_driver"),
            os.getcwd(),
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


def get_dd_backend() -> DDBackend:
    """
    Get or create DD Backend singleton instance

    Returns:
        DDBackend instance
    """
    global _dd_instance
    if _dd_instance is None:
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