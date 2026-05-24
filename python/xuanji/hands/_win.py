"""
Windows 操控实现

用 ctypes 调 user32.dll（SendInput / SetCursorPos / keybd_event）。
不依赖 pyautogui。
"""

import ctypes
import ctypes.wintypes
import sys
import time
from typing import Optional

from ._base import HandsBase

if sys.platform != "win32":
    raise ImportError("WinHands 仅支持 Windows")

# ── Win32 API 常量 ──
INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_ABSOLUTE = 0x8000
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
WHEEL_DELTA = 120

# ── 虚拟键码映射表 ──
VK_MAP = {
    # 特殊键
    "enter": 0x0D, "return": 0x0D,
    "tab": 0x09,
    "space": 0x20,
    "backspace": 0x08, "back": 0x08,
    "delete": 0x2E, "del": 0x2E,
    "escape": 0x1B, "esc": 0x1B,
    "insert": 0x2D,
    "home": 0x24,
    "end": 0x23,
    "pageup": 0x21, "pgup": 0x21,
    "pagedown": 0x22, "pgdn": 0x22,
    # 方向键
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    # 修饰键
    "ctrl": 0x11, "control": 0x11,
    "alt": 0x12, "menu": 0x12,
    "shift": 0x10,
    "win": 0x5B, "lwin": 0x5B, "rwin": 0x5C,
    "capslock": 0x14, "caps": 0x14,
    "numlock": 0x90, "scrolllock": 0x91,
    # 功能键
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73,
    "f5": 0x74, "f6": 0x75, "f7": 0x76, "f8": 0x77,
    "f9": 0x78, "f10": 0x79, "f11": 0x7A, "f12": 0x7B,
    # 其他
    "printscreen": 0x2C, "prtsc": 0x2C,
    "pause": 0x13,
    "apps": 0x5D,  # 右键菜单键
}

# 数字和字母的 VK 码就是 ASCII 码
for c in "0123456789":
    VK_MAP[c] = ord(c)
for c in "abcdefghijklmnopqrstuvwxyz":
    VK_MAP[c] = ord(c.upper())

# ── Win32 结构体 ──
user32 = ctypes.windll.user32


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.wintypes.LONG),
        ("dy", ctypes.wintypes.LONG),
        ("mouseData", ctypes.wintypes.DWORD),
        ("dwFlags", ctypes.wintypes.DWORD),
        ("time", ctypes.wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.wintypes.WORD),
        ("wScan", ctypes.wintypes.WORD),
        ("dwFlags", ctypes.wintypes.DWORD),
        ("time", ctypes.wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
    ]


class INPUT(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.wintypes.DWORD),
        ("union", _INPUT_UNION),
    ]


def _send_input(*inputs):
    """发送输入事件"""
    n = len(inputs)
    arr = (INPUT * n)(*inputs)
    user32.SendInput(n, arr, ctypes.sizeof(INPUT))


def _mouse_input(flags, dx=0, dy=0, data=0):
    """构造鼠标输入"""
    inp = INPUT()
    inp.type = INPUT_MOUSE
    inp.union.mi.dx = dx
    inp.union.mi.dy = dy
    inp.union.mi.mouseData = data
    inp.union.mi.dwFlags = flags
    return inp


def _key_input(vk, flags=0):
    """构造键盘输入"""
    inp = INPUT()
    inp.type = INPUT_KEYBOARD
    inp.union.ki.wVk = vk
    inp.union.ki.dwFlags = flags
    return inp


def _key_unicode(char, flags=0):
    """构造 Unicode 字符输入"""
    inp = INPUT()
    inp.type = INPUT_KEYBOARD
    inp.union.ki.wVk = 0
    inp.union.ki.wScan = ord(char)
    inp.union.ki.dwFlags = KEYEVENTF_UNICODE | flags
    return inp


def _resolve_vk(key: str) -> Optional[int]:
    """键名 → VK 码"""
    k = key.lower().strip()
    if k in VK_MAP:
        return VK_MAP[k]
    # 单字符
    if len(k) == 1:
        return ctypes.windll.user32.VkKeyScanW(ord(k)) & 0xFF
    return None


class WinHands(HandsBase):
    """Windows 操控实现"""

    def __init__(self, click_delay: float = 0.02, type_delay: float = 0.01):
        self._click_delay = click_delay
        self._type_delay = type_delay

    def move(self, x: int, y: int) -> None:
        user32.SetCursorPos(x, y)

    def click(self, x: int, y: int) -> None:
        self.move(x, y)
        time.sleep(self._click_delay)
        _send_input(
            _mouse_input(MOUSEEVENTF_LEFTDOWN),
            _mouse_input(MOUSEEVENTF_LEFTUP),
        )

    def double_click(self, x: int, y: int) -> None:
        self.click(x, y)
        time.sleep(self._click_delay)
        _send_input(
            _mouse_input(MOUSEEVENTF_LEFTDOWN),
            _mouse_input(MOUSEEVENTF_LEFTUP),
        )

    def right_click(self, x: int, y: int) -> None:
        self.move(x, y)
        time.sleep(self._click_delay)
        _send_input(
            _mouse_input(MOUSEEVENTF_RIGHTDOWN),
            _mouse_input(MOUSEEVENTF_RIGHTUP),
        )

    def drag(self, x1: int, y1: int, x2: int, y2: int) -> None:
        self.move(x1, y1)
        time.sleep(self._click_delay)
        _send_input(_mouse_input(MOUSEEVENTF_LEFTDOWN))
        # 分步移动，模拟平滑拖拽
        steps = max(abs(x2 - x1), abs(y2 - y1)) // 10
        steps = max(steps, 5)
        for i in range(1, steps + 1):
            cx = x1 + (x2 - x1) * i // steps
            cy = y1 + (y2 - y1) * i // steps
            self.move(cx, cy)
            time.sleep(0.005)
        time.sleep(self._click_delay)
        _send_input(_mouse_input(MOUSEEVENTF_LEFTUP))

    def scroll(self, x: int, y: int, amount: int) -> None:
        self.move(x, y)
        time.sleep(self._click_delay)
        _send_input(_mouse_input(MOUSEEVENTF_WHEEL, data=amount * WHEEL_DELTA))

    def type_text(self, text: str) -> None:
        """输入文本（支持中文/Unicode）"""
        for char in text:
            _send_input(
                _key_unicode(char),
                _key_unicode(char, KEYEVENTF_KEYUP),
            )
            time.sleep(self._type_delay)

    def press(self, key: str) -> None:
        vk = _resolve_vk(key)
        if vk is None:
            raise ValueError(f"未知键名: {key}")
        _send_input(
            _key_input(vk),
            _key_input(vk, KEYEVENTF_KEYUP),
        )

    def hotkey(self, *keys: str) -> None:
        vks = []
        for k in keys:
            vk = _resolve_vk(k)
            if vk is None:
                raise ValueError(f"未知键名: {k}")
            vks.append(vk)

        # 按顺序按下
        for vk in vks:
            _send_input(_key_input(vk))
            time.sleep(0.01)

        # 反序释放
        for vk in reversed(vks):
            _send_input(_key_input(vk, KEYEVENTF_KEYUP))
            time.sleep(0.01)
