"""
变化检测 — 纯算法，零依赖

像素级 diff：两段 raw bytes 逐字节比较。
返回变化比例和变化区域定位。
"""

from typing import Optional, Tuple


def pixel_diff(prev: bytes, curr: bytes) -> float:
    """计算两帧之间的变化比例

    对两段 bytes 逐字节比较，统计差异字节占比。
    支持任意格式的 raw bytes（BGRA/RGBA/RGB/灰度）。

    Args:
        prev: 前一帧的 raw bytes
        curr: 当前帧的 raw bytes

    Returns:
        变化比例 0.0 ~ 1.0
        - 0.0 = 完全相同
        - 1.0 = 完全不同
    """
    if not prev and not curr:
        return 0.0
    if not prev or not curr:
        return 1.0

    # 取较短长度进行比较
    min_len = min(len(prev), len(curr))
    if min_len == 0:
        return 1.0

    changed = 0
    # 逐字节比较，每4字节为一个像素（假设32位色深）
    # 使用阈值过滤噪声：像素差值 > 30 才算变化
    threshold = 30

    for i in range(min_len):
        if abs(prev[i] - curr[i]) > threshold:
            changed += 1

    # 如果长度不同，多出部分全算变化
    max_len = max(len(prev), len(curr))
    changed += (max_len - min_len)

    return changed / max_len


def pixel_diff_fast(prev: bytes, curr: bytes) -> float:
    """快速变化检测（采样版）

    每隔 stride 字节采样比较，速度更快但精度稍低。
    适用于大图实时检测。

    Args:
        prev: 前一帧 bytes
        curr: 当前帧 bytes

    Returns:
        变化比例 0.0 ~ 1.0
    """
    if not prev and not curr:
        return 0.0
    if not prev or not curr:
        return 1.0

    min_len = min(len(prev), len(curr))
    if min_len == 0:
        return 1.0

    # 采样步长：大图用大步长
    stride = max(1, min_len // 10000)
    threshold = 30
    samples = 0
    changed = 0

    for i in range(0, min_len, stride):
        samples += 1
        if abs(prev[i] - curr[i]) > threshold:
            changed += 1

    if samples == 0:
        return 0.0

    return changed / samples


def find_changed_region(
    prev: bytes,
    curr: bytes,
    width: int,
    height: int,
    channels: int = 4,
) -> Optional[Tuple[int, int, int, int]]:
    """定位变化最大的矩形区域

    将图像按 width × height × channels 解析，
    找出包含所有变化像素的最小外接矩形。

    Args:
        prev: 前一帧 raw bytes
        curr: 当前帧 raw bytes
        width: 图像宽度
        height: 图像高度
        channels: 每像素字节数（默认4=BGRA/RGBA）

    Returns:
        (x, y, w, h) 变化区域，无变化返回 None
    """
    expected = width * height * channels
    if len(prev) < expected or len(curr) < expected:
        return None

    threshold = 30
    min_x, min_y = width, height
    max_x, max_y = -1, -1

    for y in range(height):
        row_offset = y * width * channels
        for x in range(width):
            px_offset = row_offset + x * channels
            # 检查任一通道差异
            diff = False
            for c in range(min(channels, 3)):  # 只检查 RGB，跳过 Alpha
                if abs(prev[px_offset + c] - curr[px_offset + c]) > threshold:
                    diff = True
                    break
            if diff:
                if x < min_x:
                    min_x = x
                if x > max_x:
                    max_x = x
                if y < min_y:
                    min_y = y
                if y > max_y:
                    max_y = y

    if max_x < 0:
        return None

    return (min_x, min_y, max_x - min_x + 1, max_y - min_y + 1)


def find_changed_region_fast(
    prev: bytes,
    curr: bytes,
    width: int,
    height: int,
    channels: int = 4,
    grid: int = 8,
) -> Optional[Tuple[int, int, int, int]]:
    """快速定位变化区域（网格采样版）

    将图像分成 grid × grid 的网格，只检查网格交点。
    速度比逐像素快 grid² 倍，精度到网格级别。

    Args:
        prev, curr: raw bytes
        width, height: 图像尺寸
        channels: 每像素字节数
        grid: 采样网格大小（默认8，即每8个像素采样一次）

    Returns:
        (x, y, w, h) 变化区域（精度为 grid 像素），无变化返回 None
    """
    expected = width * height * channels
    if len(prev) < expected or len(curr) < expected:
        return None

    threshold = 30
    min_x, min_y = width, height
    max_x, max_y = -1, -1

    for y in range(0, height, grid):
        row_offset = y * width * channels
        for x in range(0, width, grid):
            px_offset = row_offset + x * channels
            diff = False
            for c in range(min(channels, 3)):
                if px_offset + c < expected:
                    if abs(prev[px_offset + c] - curr[px_offset + c]) > threshold:
                        diff = True
                        break
            if diff:
                if x < min_x:
                    min_x = x
                if x + grid > max_x:
                    max_x = x + grid
                if y < min_y:
                    min_y = y
                if y + grid > max_y:
                    max_y = y + grid

    if max_x < 0:
        return None

    # 限制边界
    max_x = min(max_x, width)
    max_y = min(max_y, height)

    return (min_x, min_y, max_x - min_x, max_y - min_y)
