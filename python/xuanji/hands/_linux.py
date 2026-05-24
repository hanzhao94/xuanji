"""
Linux 操控实现

优先用 subprocess 调 xdotool。
回退到 pyautogui（如果可用）。
"""

import shutil
import subprocess
import sys
import time
from typing import Optional

from ._base import HandsBase

if sys.platform == "win32":
    raise ImportError("LinuxHands 不支持 Windows")


def _has_cmd(name: str) -> bool:
    return shutil.which(name) is not None


def _xdotool(*args) -> bool:
    """执行 xdotool 命令"""
    try:
        subprocess.run(
            ["xdotool"] + list(args),
            check=True, timeout=5,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return True
    except Exception:
        return False


# ── xdotool 键名映射 ──
_XDOTOOL_KEY_MAP = {
    "enter": "Return", "return": "Return",
    "tab": "Tab",
    "space": "space",
    "backspace": "BackSpace", "back": "BackSpace",
    "delete": "Delete", "del": "Delete",
    "escape": "Escape", "esc": "Escape",
    "insert": "Insert",
    "home": "Home", "end": "End",
    "pageup": "Page_Up", "pgup": "Page_Up",
    "pagedown": "Page_Down", "pgdn": "Page_Down",
    "left": "Left", "up": "Up", "right": "Right", "down": "Down",
    "ctrl": "ctrl", "control": "ctrl",
    "alt": "alt", "menu": "alt",
    "shift": "shift",
    "win": "super", "lwin": "super", "rwin": "super",
    "capslock": "Caps_Lock", "caps": "Caps_Lock",
    "f1": "F1", "f2": "F2", "f3": "F3", "f4": "F4",
    "f5": "F5", "f6": "F6", "f7": "F7", "f8": "F8",
    "f9": "F9", "f10": "F10", "f11": "F11", "f12": "F12",
}


def _resolve_xdotool_key(key: str) -> str:
    k = key.lower().strip()
    return _XDOTOOL_KEY_MAP.get(k, key)


class _PyAutoGUIFallback:
    """pyautogui 回退封装"""

    def __init__(self):
        try:
            import pyautogui
            self._pag = pyautogui
            self._available = True
        except ImportError:
            self._pag = None
            self._available = False

    @property
    def available(self) -> bool:
        return self._available

    def click(self, x, y):
        self._pag.click(x, y)

    def double_click(self, x, y):
        self._pag.doubleClick(x, y)

    def right_click(self, x, y):
        self._pag.rightClick(x, y)

    def move(self, x, y):
        self._pag.moveTo(x, y)

    def drag(self, x1, y1, x2, y2):
        self._pag.moveTo(x1, y1)
        self._pag.drag(x2 - x1, y2 - y1, duration=0.3)

    def scroll(self, x, y, amount):
        self._pag.moveTo(x, y)
        self._pag.scroll(amount)

    def type_text(self, text):
        self._pag.typewrite(text, interval=0.02)

    def press(self, key):
        self._pag.press(key)

    def hotkey(self, *keys):
        self._pag.hotkey(*keys)


class LinuxHands(HandsBase):
    """Linux 操控实现"""

    def __init__(self):
        self._has_xdotool = _has_cmd("xdotool")
        self._fallback = _PyAutoGUIFallback()
        if not self._has_xdotool and not self._fallback.available:
            raise RuntimeError(
                "Linux 操控需要 xdotool 或 pyautogui。\n"
                "安装: sudo apt install xdotool 或 pip install pyautogui"
            )

    def _ensure_backend(self, op: str):
        if not self._has_xdotool and not self._fallback.available:
            raise RuntimeError(f"{op} 需要 xdotool 或 pyautogui")

    def move(self, x: int, y: int) -> None:
        if self._has_xdotool:
            _xdotool("mousemove", str(x), str(y))
        elif self._fallback.available:
            self._fallback.move(x, y)
        else:
            self._ensure_backend("move")

    def click(self, x: int, y: int) -> None:
        if self._has_xdotool:
            _xdotool("mousemove", str(x), str(y))
            _xdotool("click", "1")
        elif self._fallback.available:
            self._fallback.click(x, y)
        else:
            self._ensure_backend("click")

    def double_click(self, x: int, y: int) -> None:
        if self._has_xdotool:
            _xdotool("mousemove", str(x), str(y))
            _xdotool("click", "--repeat", "2", "--delay", "50", "1")
        elif self._fallback.available:
            self._fallback.double_click(x, y)
        else:
            self._ensure_backend("double_click")

    def right_click(self, x: int, y: int) -> None:
        if self._has_xdotool:
            _xdotool("mousemove", str(x), str(y))
            _xdotool("click", "3")
        elif self._fallback.available:
            self._fallback.right_click(x, y)
        else:
            self._ensure_backend("right_click")

    def drag(self, x1: int, y1: int, x2: int, y2: int) -> None:
        if self._has_xdotool:
            _xdotool("mousemove", str(x1), str(y1))
            _xdotool("mousedown", "1")
            time.sleep(0.05)
            _xdotool("mousemove", str(x2), str(y2))
            time.sleep(0.05)
            _xdotool("mouseup", "1")
        elif self._fallback.available:
            self._fallback.drag(x1, y1, x2, y2)
        else:
            self._ensure_backend("drag")

    def scroll(self, x: int, y: int, amount: int) -> None:
        if self._has_xdotool:
            self.move(x, y)
            # xdotool: button 4=上, 5=下
            if amount > 0:
                for _ in range(abs(amount)):
                    _xdotool("click", "4")
            else:
                for _ in range(abs(amount)):
                    _xdotool("click", "5")
        elif self._fallback.available:
            self._fallback.scroll(x, y, amount)
        else:
            self._ensure_backend("scroll")

    def type_text(self, text: str) -> None:
        if self._has_xdotool:
            # xdotool type 支持 Unicode
            _xdotool("type", "--clearmodifiers", "--delay", "20", text)
        elif self._fallback.available:
            self._fallback.type_text(text)
        else:
            self._ensure_backend("type_text")

    def press(self, key: str) -> None:
        if self._has_xdotool:
            xkey = _resolve_xdotool_key(key)
            _xdotool("key", xkey)
        elif self._fallback.available:
            self._fallback.press(key)
        else:
            self._ensure_backend("press")

    def hotkey(self, *keys: str) -> None:
        if self._has_xdotool:
            combo = "+".join(_resolve_xdotool_key(k) for k in keys)
            _xdotool("key", combo)
        elif self._fallback.available:
            self._fallback.hotkey(*keys)
        else:
            self._ensure_backend("hotkey")
