"""
Windows 感知实现

用 ctypes 调 user32.dll / gdi32.dll 原生截屏。
如果 Pillow 可用则返回 PIL.Image，否则返回 raw bytes。
"""

import ctypes
import ctypes.wintypes
import struct
import sys
from typing import Any, Optional, Tuple

from ._base import PerceptionBase

if sys.platform != "win32":
    raise ImportError("WinPerception 仅支持 Windows")

# ── Win32 API 常量 ──
SRCCOPY = 0x00CC0020
BI_RGB = 0
DIB_RGB_COLORS = 0
HORZRES = 8
VERTRES = 10

# ── Win32 API 绑定 ──
user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

GetDC = user32.GetDC
ReleaseDC = user32.ReleaseDC
GetSystemMetrics = user32.GetSystemMetrics

CreateCompatibleDC = gdi32.CreateCompatibleDC
CreateCompatibleBitmap = gdi32.CreateCompatibleBitmap
SelectObject = gdi32.SelectObject
BitBlt = gdi32.BitBlt
GetDIBits = gdi32.GetDIBits
DeleteObject = gdi32.DeleteObject
DeleteDC = gdi32.DeleteDC
GetDeviceCaps = gdi32.GetDeviceCaps

SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", ctypes.wintypes.DWORD),
        ("biWidth", ctypes.wintypes.LONG),
        ("biHeight", ctypes.wintypes.LONG),
        ("biPlanes", ctypes.wintypes.WORD),
        ("biBitCount", ctypes.wintypes.WORD),
        ("biCompression", ctypes.wintypes.DWORD),
        ("biSizeImage", ctypes.wintypes.DWORD),
        ("biXPelsPerMeter", ctypes.wintypes.LONG),
        ("biYPelsPerMeter", ctypes.wintypes.LONG),
        ("biClrUsed", ctypes.wintypes.DWORD),
        ("biClrImportant", ctypes.wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [
        ("bmiHeader", BITMAPINFOHEADER),
        ("bmiColors", ctypes.wintypes.DWORD * 3),
    ]


def _capture_region(x: int, y: int, w: int, h: int) -> bytes:
    """用 GDI 原生截取屏幕区域，返回 BGRA raw bytes"""
    hdc_screen = GetDC(0)
    hdc_mem = CreateCompatibleDC(hdc_screen)
    hbmp = CreateCompatibleBitmap(hdc_screen, w, h)
    old_bmp = SelectObject(hdc_mem, hbmp)

    BitBlt(hdc_mem, 0, 0, w, h, hdc_screen, x, y, SRCCOPY)

    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = w
    bmi.bmiHeader.biHeight = -h  # 负值 = top-down
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    bmi.bmiHeader.biCompression = BI_RGB

    buf_size = w * h * 4
    buf = ctypes.create_string_buffer(buf_size)
    GetDIBits(hdc_mem, hbmp, 0, h, buf, ctypes.byref(bmi), DIB_RGB_COLORS)

    # 清理资源
    SelectObject(hdc_mem, old_bmp)
    DeleteObject(hbmp)
    DeleteDC(hdc_mem)
    ReleaseDC(0, hdc_screen)

    return buf.raw


def _raw_to_pil(raw: bytes, w: int, h: int):
    """BGRA raw bytes → PIL.Image (RGBA)，失败返回 None"""
    try:
        from PIL import Image
        img = Image.frombytes("RGBA", (w, h), raw, "raw", "BGRA")
        return img
    except ImportError:
        return None
    except Exception:
        return None


class WinPerception(PerceptionBase):
    """Windows 感知实现"""

    def screen_size(self) -> Tuple[int, int]:
        w = GetSystemMetrics(SM_CXVIRTUALSCREEN)
        h = GetSystemMetrics(SM_CYVIRTUALSCREEN)
        if w == 0 or h == 0:
            # 回退到主显示器
            w = GetSystemMetrics(0)
            h = GetSystemMetrics(1)
        return (w, h)

    def screenshot(self) -> Any:
        """全屏截屏"""
        x = GetSystemMetrics(SM_XVIRTUALSCREEN)
        y = GetSystemMetrics(SM_YVIRTUALSCREEN)
        w, h = self.screen_size()
        raw = _capture_region(x, y, w, h)
        pil = _raw_to_pil(raw, w, h)
        return pil if pil is not None else raw

    def screen_region(self, x: int, y: int, w: int, h: int) -> Any:
        """截取指定区域"""
        if w <= 0 or h <= 0:
            raise ValueError(f"区域尺寸无效: w={w}, h={h}")
        raw = _capture_region(x, y, w, h)
        pil = _raw_to_pil(raw, w, h)
        return pil if pil is not None else raw

    def detect_change(self, prev: Any, curr: Any) -> float:
        """检测变化程度"""
        from .diff import pixel_diff
        prev_bytes = self._ensure_raw(prev)
        curr_bytes = self._ensure_raw(curr)
        return pixel_diff(prev_bytes, curr_bytes)

    @staticmethod
    def _ensure_raw(img: Any) -> bytes:
        """确保是 raw bytes（跳过 BMP 头部开销）"""
        if isinstance(img, (bytes, bytearray)):
            return bytes(img)
        try:
            # PIL.Image → raw RGBA bytes
            return img.tobytes("raw", "RGBA")
        except Exception:
            return PerceptionBase._to_bytes(img)
