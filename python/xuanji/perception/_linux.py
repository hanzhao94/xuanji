"""
Linux 感知实现

优先用 subprocess 调 xdotool/import 命令截屏。
回退到 Pillow ImageGrab。
"""

import os
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Optional, Tuple

from ._base import PerceptionBase

if sys.platform == "win32":
    raise ImportError("LinuxPerception 不支持 Windows")


def _has_cmd(name: str) -> bool:
    """检查系统命令是否可用"""
    return shutil.which(name) is not None


def _import_screenshot(x: int = 0, y: int = 0, w: int = 0, h: int = 0) -> Optional[bytes]:
    """用 ImageMagick import 命令截屏，返回 PNG bytes"""
    if not _has_cmd("import"):
        return None
    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            tmp = f.name
        if w > 0 and h > 0:
            geometry = f"{w}x{h}+{x}+{y}"
            subprocess.run(
                ["import", "-window", "root", "-crop", geometry, tmp],
                check=True, timeout=10,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        else:
            subprocess.run(
                ["import", "-window", "root", tmp],
                check=True, timeout=10,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        with open(tmp, "rb") as f:
            data = f.read()
        return data
    except Exception:
        return None
    finally:
        try:
            os.unlink(tmp)
        except Exception:
            pass


def _xdotool_screen_size() -> Optional[Tuple[int, int]]:
    """用 xdotool 获取屏幕尺寸"""
    if not _has_cmd("xdotool"):
        return None
    try:
        result = subprocess.run(
            ["xdotool", "getdisplaygeometry"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            parts = result.stdout.strip().split()
            if len(parts) >= 2:
                return (int(parts[0]), int(parts[1]))
    except Exception:
        pass
    return None


def _xrandr_screen_size() -> Optional[Tuple[int, int]]:
    """用 xrandr 获取屏幕尺寸"""
    if not _has_cmd("xrandr"):
        return None
    try:
        result = subprocess.run(
            ["xrandr", "--current"],
            capture_output=True, text=True, timeout=5,
        )
        for line in result.stdout.splitlines():
            if " connected " in line and "+" in line:
                # 例: "1920x1080+0+0"
                for part in line.split():
                    if "x" in part and "+" in part:
                        res = part.split("+")[0]
                        w, h = res.split("x")
                        return (int(w), int(h))
    except Exception:
        pass
    return None


def _pillow_screenshot(x: int = 0, y: int = 0, w: int = 0, h: int = 0):
    """用 Pillow ImageGrab 截屏"""
    try:
        from PIL import ImageGrab
        if w > 0 and h > 0:
            bbox = (x, y, x + w, y + h)
            return ImageGrab.grab(bbox=bbox)
        else:
            return ImageGrab.grab()
    except ImportError:
        return None
    except Exception:
        return None


def _bytes_to_pil(data: bytes):
    """PNG/BMP bytes → PIL.Image"""
    try:
        from PIL import Image
        import io
        return Image.open(io.BytesIO(data))
    except ImportError:
        return None
    except Exception:
        return None


class LinuxPerception(PerceptionBase):
    """Linux 感知实现"""

    def screen_size(self) -> Tuple[int, int]:
        size = _xdotool_screen_size()
        if size:
            return size
        size = _xrandr_screen_size()
        if size:
            return size
        # 尝试 Pillow
        try:
            from PIL import ImageGrab
            img = ImageGrab.grab()
            return img.size
        except Exception:
            pass
        raise RuntimeError("无法获取屏幕尺寸：需要 xdotool/xrandr 或 Pillow")

    def screenshot(self) -> Any:
        """全屏截屏"""
        # 优先 import 命令
        data = _import_screenshot()
        if data:
            pil = _bytes_to_pil(data)
            return pil if pil is not None else data

        # 回退 Pillow
        pil = _pillow_screenshot()
        if pil is not None:
            return pil

        raise RuntimeError("截屏失败：需要 ImageMagick(import) 或 Pillow")

    def screen_region(self, x: int, y: int, w: int, h: int) -> Any:
        """截取指定区域"""
        if w <= 0 or h <= 0:
            raise ValueError(f"区域尺寸无效: w={w}, h={h}")

        # 优先 import 命令
        data = _import_screenshot(x, y, w, h)
        if data:
            pil = _bytes_to_pil(data)
            return pil if pil is not None else data

        # 回退 Pillow
        pil = _pillow_screenshot(x, y, w, h)
        if pil is not None:
            return pil

        raise RuntimeError("区域截屏失败：需要 ImageMagick(import) 或 Pillow")
