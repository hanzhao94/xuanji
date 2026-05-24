"""
感知系统基类

定义所有感知操作的抽象接口。
子类实现平台差异（Windows/Linux）。
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Tuple, Union


class PerceptionBase(ABC):
    """感知引擎抽象基类"""

    @abstractmethod
    def screenshot(self) -> Any:
        """全屏截屏

        Returns:
            PIL.Image（如果Pillow可用）或 bytes（原始像素数据）
        """
        ...

    @abstractmethod
    def screen_region(self, x: int, y: int, w: int, h: int) -> Any:
        """截取屏幕指定区域

        Args:
            x: 左上角X坐标
            y: 左上角Y坐标
            w: 宽度
            h: 高度

        Returns:
            PIL.Image 或 bytes
        """
        ...

    def detect_change(self, prev: Any, curr: Any) -> float:
        """检测两帧之间的变化程度

        Args:
            prev: 前一帧（bytes 或 PIL.Image）
            curr: 当前帧（bytes 或 PIL.Image）

        Returns:
            变化比例 0.0（无变化）~ 1.0（完全变化）
        """
        from .diff import pixel_diff
        prev_bytes = self._to_bytes(prev)
        curr_bytes = self._to_bytes(curr)
        return pixel_diff(prev_bytes, curr_bytes)

    def find_text(self, image: Any) -> str:
        """OCR识别图片中的文字

        需要第三方OCR库支持（如pytesseract）。
        不可用时返回空字符串。

        Args:
            image: PIL.Image 或 bytes

        Returns:
            识别到的文本
        """
        try:
            import pytesseract
            pil_img = self._to_pil(image)
            if pil_img is not None:
                return pytesseract.image_to_string(pil_img)
        except ImportError:
            pass
        except Exception:
            pass
        return ""

    def screen_size(self) -> Tuple[int, int]:
        """获取屏幕分辨率

        Returns:
            (width, height)
        """
        raise NotImplementedError

    # ── 内部工具方法 ──

    @staticmethod
    def _to_bytes(img: Any) -> bytes:
        """将图片统一转为 bytes"""
        if isinstance(img, bytes):
            return img
        if isinstance(img, bytearray):
            return bytes(img)
        # PIL.Image
        try:
            import io
            buf = io.BytesIO()
            img.save(buf, format="BMP")
            return buf.getvalue()
        except Exception:
            pass
        raise TypeError(f"无法将 {type(img).__name__} 转为 bytes")

    @staticmethod
    def _to_pil(img: Any):
        """尝试将图片转为 PIL.Image，失败返回 None"""
        try:
            from PIL import Image
            if isinstance(img, Image.Image):
                return img
            if isinstance(img, (bytes, bytearray)):
                import io
                return Image.open(io.BytesIO(img))
        except ImportError:
            pass
        except Exception:
            pass
        return None
