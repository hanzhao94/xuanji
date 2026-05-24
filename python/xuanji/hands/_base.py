"""
操控系统基类

定义所有操控操作的抽象接口。
子类实现平台差异（Windows/Linux）。
"""

from abc import ABC, abstractmethod
from typing import Any, Optional, Tuple


class HandsBase(ABC):
    """操控引擎抽象基类"""

    # ── 鼠标操作 ──

    @abstractmethod
    def click(self, x: int, y: int) -> None:
        """左键单击

        Args:
            x: 屏幕X坐标
            y: 屏幕Y坐标
        """
        ...

    @abstractmethod
    def double_click(self, x: int, y: int) -> None:
        """左键双击"""
        ...

    @abstractmethod
    def right_click(self, x: int, y: int) -> None:
        """右键单击"""
        ...

    @abstractmethod
    def move(self, x: int, y: int) -> None:
        """移动鼠标到指定位置"""
        ...

    @abstractmethod
    def drag(self, x1: int, y1: int, x2: int, y2: int) -> None:
        """从 (x1,y1) 拖拽到 (x2,y2)"""
        ...

    @abstractmethod
    def scroll(self, x: int, y: int, amount: int) -> None:
        """在指定位置滚动

        Args:
            x: 滚动位置X
            y: 滚动位置Y
            amount: 滚动量（正=向上，负=向下）
        """
        ...

    # ── 键盘操作 ──

    @abstractmethod
    def type_text(self, text: str) -> None:
        """输入文本字符串

        Args:
            text: 要输入的文本
        """
        ...

    @abstractmethod
    def press(self, key: str) -> None:
        """按下并释放单个键

        Args:
            key: 键名（如 'enter', 'tab', 'escape', 'a', 'f1'）
        """
        ...

    @abstractmethod
    def hotkey(self, *keys: str) -> None:
        """组合键（按顺序按下，反序释放）

        Args:
            keys: 键名序列，如 hotkey('ctrl', 'c')

        Example:
            engine.hotkey('ctrl', 'shift', 'esc')  # 打开任务管理器
        """
        ...

    # ── 便捷方法 ──

    def click_and_type(self, x: int, y: int, text: str) -> None:
        """点击后输入文本"""
        self.click(x, y)
        self.type_text(text)

    def copy(self) -> None:
        """Ctrl+C"""
        self.hotkey("ctrl", "c")

    def paste(self) -> None:
        """Ctrl+V"""
        self.hotkey("ctrl", "v")

    def select_all(self) -> None:
        """Ctrl+A"""
        self.hotkey("ctrl", "a")
