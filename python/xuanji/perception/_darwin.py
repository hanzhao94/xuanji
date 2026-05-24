"""
macOS 视觉感知实现

使用 screencapture (macOS自带命令) 做截屏。
使用 PIL/Pillow 做图像处理。
"""

import os
import platform
import subprocess
import sys
import tempfile
from typing import Any, Tuple

from ._base import PerceptionBase

if sys.platform != "darwin":
    raise ImportError("DarwinPerception 仅支持 macOS")


def _screencapture(output_path: str, region: str = "") -> bool:
    """执行 macOS screencapture 命令"""
    try:
        cmd = ["screencapture"]
        if region:
            cmd.extend(["-R", region])
        cmd.append(output_path)
        subprocess.run(
            cmd,
            check=True, timeout=10,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return True
    except Exception:
        return False


class DarwinPerception(PerceptionBase):
    """macOS 视觉感知实现"""

    def __init__(self):
        self._cached_size = None
        # 测试screencapture是否可用
        try:
            subprocess.run(
                ["screencapture", "-x", "/dev/null"],
                check=True, timeout=5,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            self._available = True
        except Exception:
            self._available = False
            raise RuntimeError(
                "macOS 截屏需要 screencapture 权限。\n"
                "请在 系统偏好设置 → 安全性与隐私 → 屏幕录制 中授权。"
            )

    def screen_size(self) -> Tuple[int, int]:
        """获取屏幕分辨率"""
        if self._cached_size:
            return self._cached_size
        
        try:
            result = subprocess.run(
                ["system_profiler", "SPDisplaysDataType"],
                capture_output=True, text=True, timeout=10,
            )
            # 解析输出找分辨率
            for line in result.stdout.split("\n"):
                if "Resolution" in line:
                    # 例如: "Resolution: 1920 x 1080 Retina"
                    parts = line.split(":")[1].strip()
                    nums = parts.split("x")
                    if len(nums) == 2:
                        w = int(nums[0].strip())
                        h = int(nums[1].strip().split()[0])
                        self._cached_size = (w, h)
                        return (w, h)
        except Exception:
            pass
        
        # 回退：使用AppleScript
        try:
            result = subprocess.run(
                ["osascript", "-e",
                 'tell application "Finder" to bounds of window of desktop'],
                capture_output=True, text=True, timeout=5,
            )
            # 输出格式: "0, 0, 1920, 1080"
            parts = result.stdout.strip().split(",")
            if len(parts) == 4:
                w = int(parts[2].strip())
                h = int(parts[3].strip())
                self._cached_size = (w, h)
                return (w, h)
        except Exception:
            pass
        
        # 最终回退
        return (1920, 1080)

    def screenshot(self) -> Any:
        """截取全屏"""
        from PIL import Image
        tmp_path = os.path.join(tempfile.gettempdir(), "macos_screenshot.png")
        
        if _screencapture(tmp_path):
            return Image.open(tmp_path)
        return None

    def screen_region(self, x: int, y: int, w: int, h: int) -> Any:
        """截取指定区域"""
        from PIL import Image
        tmp_path = os.path.join(tempfile.gettempdir(), "macos_region.png")
        
        # screencapture -R x,y,w,h
        region = f"{x},{y},{w},{h}"
        if _screencapture(tmp_path, region):
            return Image.open(tmp_path)
        return None

    def detect_change(self, prev_img: Any, curr_img: Any, threshold: int = 10) -> bool:
        """检测两张图片是否有变化"""
        try:
            from PIL import ImageChops, ImageStat
            diff = ImageChops.difference(prev_img, curr_img)
            stat = ImageStat.Stat(diff)
            # 检查平均差异是否超过阈值
            for band in stat.mean:
                if band > threshold:
                    return True
            return False
        except ImportError:
            # 没有PIL，做简单尺寸比较
            return prev_img.size != curr_img.size

    def find_text(self, image: Any, text: str) -> Tuple[int, int]:
        """在图片中查找文字位置（需要OCR）"""
        try:
            import pytesseract
            import cv2
            import numpy as np
            
            # 转换PIL图片到OpenCV格式
            img_array = np.array(image)
            gray = cv2.cvtColor(img_array, cv2.COLOR_RGB2GRAY)
            
            # OCR
            data = pytesseract.image_to_data(gray, output_type=pytesseract.Output.DICT)
            
            for i, word in enumerate(data['text']):
                if text.lower() in word.lower():
                    x = data['left'][i]
                    y = data['top'][i]
                    return (x, y)
            
            return None
        except ImportError:
            raise RuntimeError(
                "查找文字需要 pytesseract 和 opencv-python。\n"
                "安装: brew install tesseract && pip install pytesseract opencv-python"
            )
