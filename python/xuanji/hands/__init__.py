"""
xuanji 操控系统

自动检测平台，选择正确的操控实现。

Usage:
    from xuanji.hands import HandsEngine

    engine = HandsEngine()
    engine.click(500, 300)
    engine.type_text("Hello, World!")
    engine.hotkey("ctrl", "s")

浏览器操控:
    from xuanji.hands import BrowserHands

    browser = BrowserHands()
    await browser.open_url("https://example.com")
"""

import sys

from .browser import BrowserHands


def _create_engine():
    """根据平台创建操控引擎实例"""
    if sys.platform == "win32":
        from ._win import WinHands
        return WinHands
    elif sys.platform == "darwin":
        from ._darwin import DarwinHands
        return DarwinHands
    else:
        from ._linux import LinuxHands
        return LinuxHands


HandsEngine = _create_engine()

__all__ = ["HandsEngine", "BrowserHands"]
