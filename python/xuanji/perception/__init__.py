"""
xuanji 感知系统

自动检测平台，选择正确的感知实现。

Usage:
    from xuanji.perception import PerceptionEngine

    engine = PerceptionEngine()
    img = engine.screenshot()
    region = engine.screen_region(100, 100, 400, 300)
    change = engine.detect_change(prev_img, curr_img)
"""

import sys


def _create_engine():
    """根据平台创建感知引擎实例"""
    if sys.platform == "win32":
        from ._win import WinPerception
        return WinPerception
    elif sys.platform == "darwin":
        from ._darwin import DarwinPerception
        return DarwinPerception
    else:
        from ._linux import LinuxPerception
        return LinuxPerception


PerceptionEngine = _create_engine()

__all__ = ["PerceptionEngine"]
