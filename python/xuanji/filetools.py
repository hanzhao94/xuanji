"""
xuanji 文件处理工具集

读写 JSON / CSV / 文本，目录操作，临时文件管理，路径安全检查。

示例:
    ft = FileTools(sandbox="/data/workspace")
    data = ft.read_json("config.json")
    ft.write_csv("output.csv", [{"name": "Alice", "role": "AI"}])
    ft.list_dir(".")
"""

import csv
import io
import json
import os
import shutil
import stat
import tempfile
import time
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union
from pathlib import Path

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────────

@dataclass
class FileInfo:
    """文件信息

    Attributes:
        name: 文件名
        path: 完整路径
        size: 文件大小（字节）
        is_dir: 是否为目录
        is_file: 是否为文件
        extension: 扩展名
        modified: 最后修改时间（UNIX 时间戳）
        created: 创建时间
        permissions: 权限字符串
    """
    name: str = ""
    path: str = ""
    size: int = 0
    is_dir: bool = False
    is_file: bool = False
    extension: str = ""
    modified: float = 0.0
    created: float = 0.0
    permissions: str = ""


@dataclass
class TempFile:
    """临时文件记录

    Attributes:
        path: 临时文件路径
        created: 创建时间
        auto_delete: 是否自动删除
    """
    path: str = ""
    created: float = field(default_factory=time.time)
    auto_delete: bool = True


# ─────────────────────────────────────────────
# 路径安全
# ─────────────────────────────────────────────

class PathSecurity:
    """路径安全检查器

    确保所有文件操作不超出沙箱目录。

    Args:
        sandbox: 沙箱根目录（None 则不限制）
    """

    def __init__(self, sandbox: Optional[str] = None) -> None:
        self._sandbox = os.path.realpath(sandbox) if sandbox else None

    def check(self, path: str) -> str:
        """检查并返回安全的绝对路径

        Args:
            path: 输入路径

        Returns:
            安全的绝对路径

        Raises:
            PermissionError: 路径超出沙箱
        """
        resolved = os.path.realpath(os.path.abspath(path))

        if self._sandbox:
            # 确保路径在沙箱内
            if not resolved.startswith(self._sandbox + os.sep) and resolved != self._sandbox:
                raise PermissionError(
                    f"路径 '{path}' 超出沙箱 '{self._sandbox}'"
                )

        return resolved

    @property
    def sandbox(self) -> Optional[str]:
        """沙箱根目录"""
        return self._sandbox


# ─────────────────────────────────────────────
# 文件工具
# ─────────────────────────────────────────────

class FileTools:
    """文件处理工具集

    提供安全的文件读写操作，支持 JSON / CSV / 文本格式。

    Args:
        sandbox: 沙箱目录（限制文件操作范围）
        encoding: 默认编码
        temp_dir: 临时文件目录
    """

    def __init__(
        self,
        sandbox: Optional[str] = None,
        encoding: str = "utf-8",
        temp_dir: Optional[str] = None,
    ) -> None:
        self._security = PathSecurity(sandbox)
        self._encoding = encoding
        self._temp_dir = temp_dir
        self._temp_files: List[TempFile] = []

    # ── JSON ──

    def read_json(self, path: str) -> Any:
        """读取 JSON 文件

        Args:
            path: 文件路径

        Returns:
            解析后的 Python 对象
        """
        safe_path = self._security.check(path)
        with open(safe_path, "r", encoding=self._encoding) as f:
            return json.load(f)

    def write_json(
        self,
        path: str,
        data: Any,
        indent: int = 2,
        ensure_ascii: bool = False,
    ) -> str:
        """写入 JSON 文件

        Args:
            path: 文件路径
            data: 要写入的数据
            indent: 缩进空格数
            ensure_ascii: 是否转义非 ASCII 字符

        Returns:
            写入的文件路径
        """
        safe_path = self._security.check(path)
        os.makedirs(os.path.dirname(safe_path) or ".", exist_ok=True)

        with open(safe_path, "w", encoding=self._encoding) as f:
            json.dump(data, f, indent=indent, ensure_ascii=ensure_ascii)

        return safe_path

    # ── CSV ──

    def read_csv(
        self,
        path: str,
        has_header: bool = True,
        delimiter: str = ",",
    ) -> Union[List[Dict[str, str]], List[List[str]]]:
        """读取 CSV 文件

        Args:
            path: 文件路径
            has_header: 是否有表头
            delimiter: 分隔符

        Returns:
            有表头返回 List[Dict]，无表头返回 List[List]
        """
        safe_path = self._security.check(path)

        with open(safe_path, "r", encoding=self._encoding, newline="") as f:
            if has_header:
                reader = csv.DictReader(f, delimiter=delimiter)
                return [dict(row) for row in reader]
            else:
                reader = csv.reader(f, delimiter=delimiter)
                return [list(row) for row in reader]

    def write_csv(
        self,
        path: str,
        data: Union[List[Dict[str, Any]], List[List[Any]]],
        headers: Optional[List[str]] = None,
        delimiter: str = ",",
    ) -> str:
        """写入 CSV 文件

        Args:
            path: 文件路径
            data: 数据（字典列表或嵌套列表）
            headers: 表头（字典列表时可自动推断）
            delimiter: 分隔符

        Returns:
            写入的文件路径
        """
        safe_path = self._security.check(path)
        os.makedirs(os.path.dirname(safe_path) or ".", exist_ok=True)

        with open(safe_path, "w", encoding=self._encoding, newline="") as f:
            if data and isinstance(data[0], dict):
                fieldnames = headers or list(data[0].keys())
                writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter=delimiter)
                writer.writeheader()
                writer.writerows(data)
            else:
                writer = csv.writer(f, delimiter=delimiter)
                if headers:
                    writer.writerow(headers)
                writer.writerows(data)

        return safe_path

    # ── 文本 ──

    def read_text(self, path: str, encoding: Optional[str] = None) -> str:
        """读取文本文件

        Args:
            path: 文件路径
            encoding: 编码（默认使用实例编码）

        Returns:
            文件内容
        """
        safe_path = self._security.check(path)
        with open(safe_path, "r", encoding=encoding or self._encoding) as f:
            return f.read()

    def write_text(
        self,
        path: str,
        content: str,
        append: bool = False,
        encoding: Optional[str] = None,
    ) -> str:
        """写入文本文件

        Args:
            path: 文件路径
            content: 文本内容
            append: 是否追加模式
            encoding: 编码

        Returns:
            写入的文件路径
        """
        safe_path = self._security.check(path)
        os.makedirs(os.path.dirname(safe_path) or ".", exist_ok=True)

        mode = "a" if append else "w"
        with open(safe_path, mode, encoding=encoding or self._encoding) as f:
            f.write(content)

        return safe_path

    def read_lines(self, path: str, strip: bool = True) -> List[str]:
        """按行读取文件

        Args:
            path: 文件路径
            strip: 是否去除行尾空白

        Returns:
            行列表
        """
        text = self.read_text(path)
        lines = text.splitlines()
        if strip:
            lines = [line.strip() for line in lines]
        return lines

    # ── 目录操作 ──

    def list_dir(
        self,
        path: str = ".",
        pattern: Optional[str] = None,
        recursive: bool = False,
        include_hidden: bool = False,
    ) -> List[FileInfo]:
        """列出目录内容

        Args:
            path: 目录路径
            pattern: 文件名匹配模式（简单通配符，如 "*.py"）
            recursive: 是否递归
            include_hidden: 是否包含隐藏文件

        Returns:
            FileInfo 列表
        """
        safe_path = self._security.check(path)
        results: List[FileInfo] = []

        if recursive:
            for root, dirs, files in os.walk(safe_path):
                for name in dirs + files:
                    full = os.path.join(root, name)
                    info = self._get_file_info(full)
                    if info and self._matches(info, pattern, include_hidden):
                        results.append(info)
        else:
            try:
                entries = os.listdir(safe_path)
            except PermissionError:
                return []

            for name in sorted(entries):
                full = os.path.join(safe_path, name)
                info = self._get_file_info(full)
                if info and self._matches(info, pattern, include_hidden):
                    results.append(info)

        return results

    def file_info(self, path: str) -> Optional[FileInfo]:
        """获取文件详细信息

        Args:
            path: 文件路径

        Returns:
            FileInfo 或 None
        """
        safe_path = self._security.check(path)
        return self._get_file_info(safe_path)

    def _get_file_info(self, path: str) -> Optional[FileInfo]:
        """内部：获取文件信息"""
        try:
            st = os.stat(path)
            name = os.path.basename(path)
            _, ext = os.path.splitext(name)

            return FileInfo(
                name=name,
                path=path,
                size=st.st_size,
                is_dir=stat.S_ISDIR(st.st_mode),
                is_file=stat.S_ISREG(st.st_mode),
                extension=ext.lower(),
                modified=st.st_mtime,
                created=getattr(st, "st_birthtime", st.st_ctime),
                permissions=oct(st.st_mode)[-3:],
            )
        except (OSError, PermissionError):
            return None

    def _matches(
        self,
        info: FileInfo,
        pattern: Optional[str],
        include_hidden: bool,
    ) -> bool:
        """检查文件是否匹配过滤条件"""
        if not include_hidden and info.name.startswith("."):
            return False

        if pattern:
            import fnmatch
            if not fnmatch.fnmatch(info.name, pattern):
                return False

        return True

    # ── 文件操作 ──

    def copy(self, src: str, dst: str) -> str:
        """复制文件或目录

        Args:
            src: 源路径
            dst: 目标路径

        Returns:
            目标路径
        """
        safe_src = self._security.check(src)
        safe_dst = self._security.check(dst)

        if os.path.isdir(safe_src):
            shutil.copytree(safe_src, safe_dst)
        else:
            os.makedirs(os.path.dirname(safe_dst) or ".", exist_ok=True)
            shutil.copy2(safe_src, safe_dst)

        return safe_dst

    def move(self, src: str, dst: str) -> str:
        """移动文件或目录

        Args:
            src: 源路径
            dst: 目标路径

        Returns:
            目标路径
        """
        safe_src = self._security.check(src)
        safe_dst = self._security.check(dst)
        os.makedirs(os.path.dirname(safe_dst) or ".", exist_ok=True)
        shutil.move(safe_src, safe_dst)
        return safe_dst

    def delete(self, path: str, use_trash: bool = True) -> bool:
        """删除文件或目录

        优先移到回收站（trash），找不到 trash 模块则直接删除。

        Args:
            path: 文件路径
            use_trash: 是否优先使用回收站

        Returns:
            是否成功
        """
        safe_path = self._security.check(path)

        if not os.path.exists(safe_path):
            return False

        if use_trash:
            try:
                # 尝试使用 send2trash
                from send2trash import send2trash as _trash
                _trash(safe_path)
                return True
            except ImportError:
                # 没有 send2trash，移到 .trash 目录
                trash_dir = os.path.join(
                    self._security.sandbox or tempfile.gettempdir(),
                    ".trash",
                )
                os.makedirs(trash_dir, exist_ok=True)
                trash_path = os.path.join(
                    trash_dir,
                    f"{os.path.basename(safe_path)}_{int(time.time())}",
                )
                shutil.move(safe_path, trash_path)
                logger.info("已移到回收站: %s → %s", safe_path, trash_path)
                return True

        # 直接删除
        if os.path.isdir(safe_path):
            shutil.rmtree(safe_path)
        else:
            os.remove(safe_path)
        return True

    def ensure_dir(self, path: str) -> str:
        """确保目录存在

        Args:
            path: 目录路径

        Returns:
            绝对路径
        """
        safe_path = self._security.check(path)
        os.makedirs(safe_path, exist_ok=True)
        return safe_path

    def exists(self, path: str) -> bool:
        """检查路径是否存在"""
        safe_path = self._security.check(path)
        return os.path.exists(safe_path)

    # ── 临时文件 ──

    def create_temp(
        self,
        suffix: str = "",
        prefix: str = "xuanji_",
        content: Optional[str] = None,
    ) -> str:
        """创建临时文件

        Args:
            suffix: 文件后缀
            prefix: 文件前缀
            content: 初始内容

        Returns:
            临时文件路径
        """
        fd, path = tempfile.mkstemp(
            suffix=suffix,
            prefix=prefix,
            dir=self._temp_dir,
        )
        os.close(fd)

        if content:
            with open(path, "w", encoding=self._encoding) as f:
                f.write(content)

        self._temp_files.append(TempFile(path=path))
        return path

    def create_temp_dir(self, prefix: str = "xuanji_") -> str:
        """创建临时目录

        Args:
            prefix: 目录前缀

        Returns:
            临时目录路径
        """
        path = tempfile.mkdtemp(prefix=prefix, dir=self._temp_dir)
        self._temp_files.append(TempFile(path=path))
        return path

    def cleanup_temp(self) -> int:
        """清理所有临时文件

        Returns:
            清理的文件数
        """
        count = 0
        for tf in self._temp_files:
            if tf.auto_delete and os.path.exists(tf.path):
                try:
                    if os.path.isdir(tf.path):
                        shutil.rmtree(tf.path)
                    else:
                        os.remove(tf.path)
                    count += 1
                except OSError as e:
                    logger.warning("清理临时文件失败: %s: %s", tf.path, e)
        self._temp_files.clear()
        return count

    # ── 便捷方法 ──

    def file_size(self, path: str) -> int:
        """获取文件大小（字节）"""
        safe_path = self._security.check(path)
        return os.path.getsize(safe_path)

    def file_hash(self, path: str, algorithm: str = "md5") -> str:
        """计算文件哈希值

        Args:
            path: 文件路径
            algorithm: 哈希算法 (md5/sha1/sha256)

        Returns:
            十六进制哈希字符串
        """
        import hashlib

        safe_path = self._security.check(path)
        h = hashlib.new(algorithm)

        with open(safe_path, "rb") as f:
            while True:
                chunk = f.read(8192)
                if not chunk:
                    break
                h.update(chunk)

        return h.hexdigest()

    def tree(self, path: str = ".", max_depth: int = 3, prefix: str = "") -> str:
        """生成目录树字符串

        Args:
            path: 根目录
            max_depth: 最大深度
            prefix: 行前缀

        Returns:
            树形结构字符串
        """
        safe_path = self._security.check(path)
        lines = [os.path.basename(safe_path) + "/"]
        self._tree_walk(safe_path, lines, prefix, max_depth, 0)
        return "\n".join(lines)

    def _tree_walk(
        self,
        path: str,
        lines: List[str],
        prefix: str,
        max_depth: int,
        depth: int,
    ) -> None:
        """递归构建目录树"""
        if depth >= max_depth:
            return

        try:
            entries = sorted(os.listdir(path))
        except PermissionError:
            return

        entries = [e for e in entries if not e.startswith(".")]

        for i, entry in enumerate(entries):
            full = os.path.join(path, entry)
            is_last = i == len(entries) - 1
            connector = "└── " if is_last else "├── "
            suffix = "/" if os.path.isdir(full) else ""
            lines.append(f"{prefix}{connector}{entry}{suffix}")

            if os.path.isdir(full):
                extension = "    " if is_last else "│   "
                self._tree_walk(full, lines, prefix + extension, max_depth, depth + 1)

    def __del__(self) -> None:
        """析构时清理临时文件"""
        try:
            self.cleanup_temp()
        except Exception:
            pass
