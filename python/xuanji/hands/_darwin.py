"""
macOS 操控实现

优先用 cliclick（如果可用）。
回退到 osascript (AppleScript) + pyautogui。

安装 cliclick: brew install cliclick
"""

import os
import platform
import shutil
import subprocess
import sys
import time
from typing import Optional

from ._base import HandsBase

if sys.platform != "darwin":
    raise ImportError("DarwinHands 仅支持 macOS")


def _has_cmd(name: str) -> bool:
    return shutil.which(name) is not None


def _cliclick(*args) -> bool:
    """执行 cliclick 命令"""
    try:
        subprocess.run(
            ["cliclick"] + list(args),
            check=True, timeout=5,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return True
    except Exception:
        return False


def _osascript(script: str) -> bool:
    """执行 AppleScript"""
    try:
        subprocess.run(
            ["osascript", "-e", script],
            check=True, timeout=10,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return True
    except Exception:
        return False


def _osascript_applescript(*lines: str) -> bool:
    """执行多行AppleScript"""
    script = "\n".join(lines)
    return _osascript(script)


# ── AppleScript 键名映射 ──
_OSASCRIPT_KEY_MAP = {
    "enter": "return", "return": "return",
    "tab": "tab",
    "space": "space",
    "backspace": "delete", "back": "delete",
    "delete": "forward delete", "del": "forward delete",
    "escape": "esc", "esc": "esc",
    "insert": "help",
    "home": "home", "end": "end",
    "pageup": "page up", "pgup": "page up",
    "pagedown": "page down", "pgdn": "page down",
    "left": "left arrow", "up": "up arrow",
    "right": "right arrow", "down": "down arrow",
    "ctrl": "control", "control": "control",
    "alt": "option", "option": "option", "menu": "option",
    "shift": "shift",
    "cmd": "command", "command": "command", "win": "command",
    "capslock": "caps lock", "caps": "caps lock",
    "f1": "f1", "f2": "f2", "f3": "f3", "f4": "f4",
    "f5": "f5", "f6": "f6", "f7": "f7", "f8": "f8",
    "f9": "f9", "f10": "f10", "f11": "f11", "f12": "f12",
}


def _resolve_applescript_key(key: str) -> str:
    k = key.lower().strip()
    return _OSASCRIPT_KEY_MAP.get(k, k)


def _make_applescript_hotkey(*keys: str) -> str:
    """生成AppleScript按键组合"""
    if not keys:
        return ""
    
    resolved = [_resolve_applescript_key(k) for k in keys]
    
    # 分离修饰键和普通键
    modifiers_map = {
        "control": "control down",
        "option": "option down",
        "command": "command down",
        "shift": "shift down",
    }
    
    mods = []
    normal_keys = []
    for k in resolved:
        if k in modifiers_map:
            mods.append(modifiers_map[k])
        else:
            normal_keys.append(k)
    
    if not mods:
        # 没有修饰键，直接按键
        lines = []
        for k in normal_keys:
            if len(k) == 1:
                lines.append(f'keystroke "{k}"')
            else:
                lines.append(f'key code {k}')
        return "\n".join(lines)
    
    # 有修饰键
    mod_str = " and ".join(mods)
    lines = []
    for k in normal_keys:
        if len(k) == 1:
            lines.append(f'keystroke "{k}" using {mod_str}')
        else:
            # 需要按下修饰键
            for m in mods:
                lines.append(f'key down {m.split()[0]}')
            lines.append(f'key code {k}')
            for m in reversed(mods):
                lines.append(f'key up {m.split()[0]}')
    return "\n".join(lines)


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


class DarwinHands(HandsBase):
    """macOS 操控实现"""

    def __init__(self):
        self._has_cliclick = _has_cmd("cliclick")
        self._fallback = _PyAutoGUIFallback()
        if not self._has_cliclick and not self._fallback.available:
            raise RuntimeError(
                "macOS 操控需要 cliclick 或 pyautogui。\n"
                "安装: brew install cliclick 或 pip install pyautogui"
            )

    def _ensure_backend(self, op: str):
        if not self._has_cliclick and not self._fallback.available:
            raise RuntimeError(f"{op} 需要 cliclick 或 pyautogui")

    def move(self, x: int, y: int) -> None:
        if self._has_cliclick:
            _cliclick(f"m:{x},{y}")
        elif self._fallback.available:
            self._fallback.move(x, y)
        else:
            self._ensure_backend("move")

    def click(self, x: int, y: int) -> None:
        if self._has_cliclick:
            _cliclick(f"m:{x},{y}", "c:")
        elif self._fallback.available:
            self._fallback.click(x, y)
        else:
            self._ensure_backend("click")

    def double_click(self, x: int, y: int) -> None:
        if self._has_cliclick:
            _cliclick(f"m:{x},{y}", "dc:")
        elif self._fallback.available:
            self._fallback.double_click(x, y)
        else:
            self._ensure_backend("double_click")

    def right_click(self, x: int, y: int) -> None:
        if self._has_cliclick:
            _cliclick(f"m:{x},{y}", "rc:")
        elif self._fallback.available:
            self._fallback.right_click(x, y)
        else:
            self._ensure_backend("right_click")

    def drag(self, x1: int, y1: int, x2: int, y2: int) -> None:
        if self._has_cliclick:
            _cliclick(f"m:{x1},{y1}", "md:", f"m:{x2},{y2}", "mu:")
        elif self._fallback.available:
            self._fallback.drag(x1, y1, x2, y2)
        else:
            self._ensure_backend("drag")

    def scroll(self, x: int, y: int, amount: int) -> None:
        if self._has_cliclick:
            # cliclick 不直接支持滚动，移动到位置后模拟
            _cliclick(f"m:{x},{y}")
            # 使用AppleScript滚动
            for _ in range(abs(amount)):
                direction = "up" if amount > 0 else "down"
                _osascript_applescript(
                    'tell application "System Events"',
                    f'    tell process "FrontmostApplication"',
                    f'        scroll {direction} 10',
                    "    end tell",
                    "end tell"
                )
        elif self._fallback.available:
            self._fallback.scroll(x, y, amount)
        else:
            self._ensure_backend("scroll")

    def type_text(self, text: str) -> None:
        if self._has_cliclick:
            # cliclick type - 一次输入一个字符
            for char in text:
                if char == '"':
                    _cliclick('t:\\"')
                elif char == '\\':
                    _cliclick('t:\\\\')
                else:
                    _cliclick(f"t:{char}")
        elif self._fallback.available:
            self._fallback.type_text(text)
        else:
            self._ensure_backend("type_text")

    def press(self, key: str) -> None:
        resolved = _resolve_applescript_key(key)
        script = _make_applescript_hotkey(key)
        if script:
            _osascript(f'''
                tell application "System Events"
                    {script}
                end tell
            ''')
        elif self._fallback.available:
            self._fallback.press(key)
        else:
            self._ensure_backend("press")

    def hotkey(self, *keys: str) -> None:
        script = _make_applescript_hotkey(*keys)
        if script:
            _osascript(f'''
                tell application "System Events"
                    {script}
                end tell
            ''')
        elif self._fallback.available:
            self._fallback.hotkey(*keys)
        else:
            self._ensure_backend("hotkey")
