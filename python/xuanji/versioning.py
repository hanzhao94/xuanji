"""
xuanji 版本管理模块

对任意数据创建版本快照，支持列表/恢复/差异比较。
存储在 ~/.xuanji/versions/，最多保留20个版本。
零外部依赖。
"""

import os
import json
import time
import hashlib
import shutil
from typing import Any, Dict, List, Optional, Tuple, Union
from dataclasses import dataclass, field, asdict
from pathlib import Path


# ============================================================
# 版本快照
# ============================================================

@dataclass
class VersionInfo:
    """版本元信息"""
    version_id: str
    name: str
    timestamp: float
    description: str = ""
    data_hash: str = ""
    size_bytes: int = 0

    def to_dict(self) -> Dict:
        return asdict(self)

    @property
    def time_str(self) -> str:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.timestamp))

    def __repr__(self) -> str:
        return f"v{self.version_id} ({self.time_str}) {self.description}"


# ============================================================
# Diff工具
# ============================================================

class DiffResult:
    """两个版本的差异"""

    def __init__(self):
        self.added: Dict[str, Any] = {}
        self.removed: Dict[str, Any] = {}
        self.changed: Dict[str, Tuple[Any, Any]] = {}
        self.unchanged: List[str] = []

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.removed or self.changed)

    def summary(self) -> str:
        parts = []
        if self.added:
            parts.append(f"+{len(self.added)} added")
        if self.removed:
            parts.append(f"-{len(self.removed)} removed")
        if self.changed:
            parts.append(f"~{len(self.changed)} changed")
        if self.unchanged:
            parts.append(f"={len(self.unchanged)} unchanged")
        return ", ".join(parts) if parts else "identical"

    def to_dict(self) -> Dict:
        return {
            "added": self.added,
            "removed": self.removed,
            "changed": {k: {"old": v[0], "new": v[1]} for k, v in self.changed.items()},
            "unchanged_count": len(self.unchanged),
            "summary": self.summary(),
        }

    def __repr__(self) -> str:
        return f"DiffResult({self.summary()})"


def _deep_diff(old: Any, new: Any, prefix: str = "") -> DiffResult:
    """深度比较两个数据结构"""
    result = DiffResult()

    if isinstance(old, dict) and isinstance(new, dict):
        all_keys = set(old.keys()) | set(new.keys())
        for key in sorted(all_keys):
            path = f"{prefix}.{key}" if prefix else key
            if key not in old:
                result.added[path] = new[key]
            elif key not in new:
                result.removed[path] = old[key]
            elif old[key] != new[key]:
                # 递归比较嵌套dict
                if isinstance(old[key], dict) and isinstance(new[key], dict):
                    sub = _deep_diff(old[key], new[key], path)
                    result.added.update(sub.added)
                    result.removed.update(sub.removed)
                    result.changed.update(sub.changed)
                    result.unchanged.extend(sub.unchanged)
                else:
                    result.changed[path] = (old[key], new[key])
            else:
                result.unchanged.append(path)
    elif isinstance(old, list) and isinstance(new, list):
        max_len = max(len(old), len(new))
        for i in range(max_len):
            path = f"{prefix}[{i}]" if prefix else f"[{i}]"
            if i >= len(old):
                result.added[path] = new[i]
            elif i >= len(new):
                result.removed[path] = old[i]
            elif old[i] != new[i]:
                result.changed[path] = (old[i], new[i])
            else:
                result.unchanged.append(path)
    elif old != new:
        path = prefix or "<root>"
        result.changed[path] = (old, new)
    else:
        path = prefix or "<root>"
        result.unchanged.append(path)

    return result


# ============================================================
# 版本管理器
# ============================================================

class VersionManager:
    """版本管理器 — 对任意数据创建版本快照

    用法:
        vm = VersionManager()

        # 创建快照
        vm.snapshot("config", {"model": "gpt-4", "temp": 0.7}, "初始配置")

        # 修改后再快照
        vm.snapshot("config", {"model": "gpt-4o", "temp": 0.5}, "切换模型")

        # 查看版本
        versions = vm.list_versions("config")

        # 恢复
        old_data = vm.restore("config", versions[0].version_id)

        # 差异比较
        diff = vm.diff("config", versions[0].version_id, versions[1].version_id)
    """

    DEFAULT_DIR = os.path.join(os.path.expanduser("~"), ".xuanji", "versions")
    MAX_VERSIONS = 20

    def __init__(
        self,
        storage_dir: Optional[str] = None,
        max_versions: int = MAX_VERSIONS,
    ):
        """
        Args:
            storage_dir: 版本存储目录
            max_versions: 每个name最多保留版本数
        """
        self.storage_dir = storage_dir or self.DEFAULT_DIR
        self.max_versions = max_versions
        os.makedirs(self.storage_dir, exist_ok=True)

    # ----------------------------------------------------------
    # 核心API
    # ----------------------------------------------------------

    def snapshot(
        self,
        name: str,
        data: Any,
        description: str = "",
    ) -> VersionInfo:
        """创建版本快照

        Args:
            name: 数据名称（如 "config", "prompt", "model_params"）
            data: 要保存的数据（需JSON可序列化）
            description: 版本描述

        Returns:
            VersionInfo
        """
        name_dir = self._name_dir(name)
        os.makedirs(name_dir, exist_ok=True)

        # 生成版本ID
        ts = time.time()
        version_id = self._make_id(ts)

        # 序列化数据
        data_json = json.dumps(data, ensure_ascii=False, indent=2, default=str)
        data_hash = hashlib.sha256(data_json.encode()).hexdigest()[:12]

        # 保存数据
        data_path = os.path.join(name_dir, f"{version_id}.json")
        with open(data_path, "w", encoding="utf-8") as f:
            f.write(data_json)

        # 创建元信息
        info = VersionInfo(
            version_id=version_id,
            name=name,
            timestamp=ts,
            description=description,
            data_hash=data_hash,
            size_bytes=len(data_json.encode()),
        )

        # 保存元信息
        meta_path = os.path.join(name_dir, f"{version_id}.meta.json")
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(info.to_dict(), f, ensure_ascii=False, indent=2)

        # 自动清理旧版本
        self._auto_cleanup(name)

        return info

    def list_versions(self, name: str) -> List[VersionInfo]:
        """列出某个name的所有版本

        Args:
            name: 数据名称

        Returns:
            版本列表（按时间降序）
        """
        name_dir = self._name_dir(name)
        if not os.path.isdir(name_dir):
            return []

        versions = []
        for fname in os.listdir(name_dir):
            if fname.endswith(".meta.json"):
                meta_path = os.path.join(name_dir, fname)
                try:
                    with open(meta_path, "r", encoding="utf-8") as f:
                        meta = json.load(f)
                    versions.append(VersionInfo(**meta))
                except (json.JSONDecodeError, TypeError, KeyError):
                    continue

        # 按时间降序
        versions.sort(key=lambda v: v.timestamp, reverse=True)
        return versions

    def restore(self, name: str, version_id: str) -> Any:
        """恢复到某版本

        Args:
            name: 数据名称
            version_id: 版本ID

        Returns:
            该版本的数据

        Raises:
            FileNotFoundError: 版本不存在
        """
        data_path = os.path.join(self._name_dir(name), f"{version_id}.json")
        if not os.path.isfile(data_path):
            raise FileNotFoundError(f"版本不存在: {name}@{version_id}")

        with open(data_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def diff(self, name: str, v1: str, v2: str) -> DiffResult:
        """比较两个版本的差异

        Args:
            name: 数据名称
            v1: 旧版本ID
            v2: 新版本ID

        Returns:
            DiffResult
        """
        data1 = self.restore(name, v1)
        data2 = self.restore(name, v2)
        return _deep_diff(data1, data2)

    # ----------------------------------------------------------
    # 扩展API
    # ----------------------------------------------------------

    def latest(self, name: str) -> Optional[Any]:
        """获取最新版本的数据"""
        versions = self.list_versions(name)
        if not versions:
            return None
        return self.restore(name, versions[0].version_id)

    def list_names(self) -> List[str]:
        """列出所有有版本记录的name"""
        if not os.path.isdir(self.storage_dir):
            return []
        names = []
        for entry in os.listdir(self.storage_dir):
            if os.path.isdir(os.path.join(self.storage_dir, entry)):
                names.append(entry)
        return sorted(names)

    def delete_version(self, name: str, version_id: str) -> bool:
        """删除某个版本"""
        name_dir = self._name_dir(name)
        data_path = os.path.join(name_dir, f"{version_id}.json")
        meta_path = os.path.join(name_dir, f"{version_id}.meta.json")

        deleted = False
        for path in [data_path, meta_path]:
            if os.path.isfile(path):
                os.remove(path)
                deleted = True
        return deleted

    def delete_all(self, name: str) -> int:
        """删除某个name的所有版本"""
        name_dir = self._name_dir(name)
        if not os.path.isdir(name_dir):
            return 0

        count = 0
        for fname in os.listdir(name_dir):
            os.remove(os.path.join(name_dir, fname))
            count += 1

        try:
            os.rmdir(name_dir)
        except OSError:
            pass
        return count

    def stats(self) -> Dict[str, Any]:
        """整体统计"""
        total_versions = 0
        total_bytes = 0
        names = self.list_names()

        for name in names:
            versions = self.list_versions(name)
            total_versions += len(versions)
            total_bytes += sum(v.size_bytes for v in versions)

        return {
            "names": len(names),
            "total_versions": total_versions,
            "total_bytes": total_bytes,
            "storage_dir": self.storage_dir,
            "max_versions_per_name": self.max_versions,
        }

    # ----------------------------------------------------------
    # 内部方法
    # ----------------------------------------------------------

    def _name_dir(self, name: str) -> str:
        """获取name对应的目录"""
        # 安全化name（移除路径分隔符等）
        safe_name = "".join(c if c.isalnum() or c in "-_." else "_" for c in name)
        return os.path.join(self.storage_dir, safe_name)

    def _make_id(self, ts: float) -> str:
        """生成版本ID"""
        time_part = time.strftime("%Y%m%d_%H%M%S", time.localtime(ts))
        ms = int((ts % 1) * 1000)
        return f"{time_part}_{ms:03d}"

    def _auto_cleanup(self, name: str):
        """自动清理旧版本，保留最新的max_versions个"""
        versions = self.list_versions(name)
        if len(versions) <= self.max_versions:
            return

        # 删除多余的旧版本（versions已按时间降序）
        to_delete = versions[self.max_versions:]
        for v in to_delete:
            self.delete_version(name, v.version_id)


# ============================================================
# 便捷函数
# ============================================================

_default_manager: Optional[VersionManager] = None


def get_manager(**kwargs) -> VersionManager:
    """获取/创建默认版本管理器"""
    global _default_manager
    if _default_manager is None:
        _default_manager = VersionManager(**kwargs)
    return _default_manager


def snapshot(name: str, data: Any, description: str = "") -> VersionInfo:
    """快速创建快照"""
    return get_manager().snapshot(name, data, description)


def restore(name: str, version_id: str) -> Any:
    """快速恢复"""
    return get_manager().restore(name, version_id)


# ============================================================
# 测试
# ============================================================

if __name__ == "__main__":
    import tempfile

    # 用临时目录测试
    with tempfile.TemporaryDirectory() as tmp:
        vm = VersionManager(storage_dir=tmp, max_versions=5)

        print("=== 创建快照 ===")
        v1 = vm.snapshot("config", {"model": "gpt-4", "temp": 0.7}, "初始配置")
        print(f"  v1: {v1}")

        time.sleep(0.01)
        v2 = vm.snapshot("config", {"model": "gpt-4o", "temp": 0.5, "top_p": 0.9}, "切换模型")
        print(f"  v2: {v2}")

        print("\n=== 列出版本 ===")
        for v in vm.list_versions("config"):
            print(f"  {v}")

        print("\n=== 恢复版本 ===")
        data = vm.restore("config", v1.version_id)
        print(f"  v1 data: {data}")

        print("\n=== 版本差异 ===")
        diff = vm.diff("config", v1.version_id, v2.version_id)
        print(f"  {diff}")
        print(f"  details: {json.dumps(diff.to_dict(), ensure_ascii=False, indent=2)}")

        print("\n=== 最新版本 ===")
        latest = vm.latest("config")
        print(f"  latest: {latest}")

        print("\n=== 自动清理测试 ===")
        for i in range(10):
            vm.snapshot("cleanup_test", {"i": i}, f"版本{i}")
            time.sleep(0.01)
        versions = vm.list_versions("cleanup_test")
        print(f"  保留版本数: {len(versions)} (max={vm.max_versions})")

        print(f"\n=== 统计 ===\n  {vm.stats()}")
