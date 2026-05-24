"""
xuanji 断点续做

长任务中断后恢复执行。基于 JSON 文件的轻量级检查点系统。

示例:
    cm = CheckpointManager()

    # 保存进度
    cm.save("batch_process", {
        "current_index": 42,
        "processed": ["a", "b", "c"],
        "total": 100,
    })

    # 恢复进度
    if cm.has_checkpoint("batch_process"):
        state = cm.load("batch_process")
        start_from = state["current_index"]
    else:
        start_from = 0

    # 完成后清除
    cm.clear("batch_process")
"""

import json
import os
import time
import shutil
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from pathlib import Path

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────────

@dataclass
class CheckpointInfo:
    """检查点信息

    Attributes:
        task_id: 任务 ID
        state: 保存的状态
        created_at: 创建时间
        updated_at: 最后更新时间
        version: 版本号（每次 save 递增）
        metadata: 元数据
        file_path: 文件路径
        file_size: 文件大小（字节）
    """
    task_id: str = ""
    state: Dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0
    updated_at: float = 0.0
    version: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)
    file_path: str = ""
    file_size: int = 0


@dataclass
class CheckpointStats:
    """检查点统计

    Attributes:
        total: 检查点总数
        total_size: 总大小（字节）
        oldest: 最早的检查点时间
        newest: 最新的检查点时间
        task_ids: 所有任务 ID
    """
    total: int = 0
    total_size: int = 0
    oldest: float = 0.0
    newest: float = 0.0
    task_ids: List[str] = field(default_factory=list)


# ─────────────────────────────────────────────
# 检查点管理器
# ─────────────────────────────────────────────

class CheckpointManager:
    """断点续做管理器

    将任务状态持久化到 JSON 文件，支持中断恢复。

    检查点存储在 ~/.xuanji/checkpoints/ 下，
    每个任务一个 JSON 文件。

    Args:
        checkpoint_dir: 检查点目录（默认 ~/.xuanji/checkpoints/）
        max_versions: 保留的历史版本数（0 = 不保留历史）
        auto_backup: 是否在覆盖前自动备份
    """

    def __init__(
        self,
        checkpoint_dir: Optional[str] = None,
        max_versions: int = 3,
        auto_backup: bool = True,
    ) -> None:
        if checkpoint_dir is None:
            home = os.path.expanduser("~")
            checkpoint_dir = os.path.join(home, ".xuanji", "checkpoints")

        self._dir = os.path.abspath(checkpoint_dir)
        self._max_versions = max_versions
        self._auto_backup = auto_backup

        # 确保目录存在
        os.makedirs(self._dir, exist_ok=True)

    # ── 核心操作 ──

    def save(
        self,
        task_id: str,
        state: Any,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> CheckpointInfo:
        """保存检查点

        如果已有检查点，会先备份再覆盖。

        Args:
            task_id: 任务 ID（用作文件名）
            state: 要保存的状态（必须可 JSON 序列化）
            metadata: 附加元数据

        Returns:
            CheckpointInfo
        """
        self._validate_task_id(task_id)
        file_path = self._task_path(task_id)
        now = time.time()

        # 读取现有版本号
        version = 0
        if os.path.exists(file_path):
            try:
                existing = self._read_file(file_path)
                version = existing.get("version", 0)
            except Exception:
                pass

            # 备份
            if self._auto_backup:
                self._backup(task_id, version)

        version += 1

        # 构建检查点数据
        checkpoint_data = {
            "task_id": task_id,
            "state": state,
            "created_at": now if version == 1 else self._get_created_at(task_id, now),
            "updated_at": now,
            "version": version,
            "metadata": metadata or {},
        }

        # 原子写入（先写临时文件，再 rename）
        tmp_path = file_path + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(checkpoint_data, f, indent=2, ensure_ascii=False, default=str)

            # Windows 不支持 rename 到已存在的文件
            if os.path.exists(file_path):
                os.replace(tmp_path, file_path)
            else:
                os.rename(tmp_path, file_path)

        except Exception:
            # 清理临时文件
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise

        file_size = os.path.getsize(file_path)

        info = CheckpointInfo(
            task_id=task_id,
            state=state,
            created_at=checkpoint_data["created_at"],
            updated_at=now,
            version=version,
            metadata=metadata or {},
            file_path=file_path,
            file_size=file_size,
        )

        logger.info(
            "保存检查点: %s (v%d, %d bytes)",
            task_id, version, file_size,
        )
        return info

    def load(self, task_id: str) -> Any:
        """加载检查点状态

        Args:
            task_id: 任务 ID

        Returns:
            保存的状态

        Raises:
            FileNotFoundError: 检查点不存在
        """
        self._validate_task_id(task_id)
        file_path = self._task_path(task_id)

        if not os.path.exists(file_path):
            raise FileNotFoundError(f"检查点不存在: {task_id}")

        data = self._read_file(file_path)
        logger.info("加载检查点: %s (v%d)", task_id, data.get("version", 0))
        return data.get("state")

    def load_info(self, task_id: str) -> Optional[CheckpointInfo]:
        """加载完整检查点信息

        Args:
            task_id: 任务 ID

        Returns:
            CheckpointInfo 或 None
        """
        self._validate_task_id(task_id)
        file_path = self._task_path(task_id)

        if not os.path.exists(file_path):
            return None

        data = self._read_file(file_path)
        return CheckpointInfo(
            task_id=task_id,
            state=data.get("state", {}),
            created_at=data.get("created_at", 0),
            updated_at=data.get("updated_at", 0),
            version=data.get("version", 0),
            metadata=data.get("metadata", {}),
            file_path=file_path,
            file_size=os.path.getsize(file_path),
        )

    def has_checkpoint(self, task_id: str) -> bool:
        """检查是否存在检查点

        Args:
            task_id: 任务 ID

        Returns:
            是否存在
        """
        self._validate_task_id(task_id)
        return os.path.exists(self._task_path(task_id))

    def clear(self, task_id: str) -> bool:
        """清除检查点

        同时清除所有历史版本。

        Args:
            task_id: 任务 ID

        Returns:
            是否成功
        """
        self._validate_task_id(task_id)
        file_path = self._task_path(task_id)

        removed = False
        if os.path.exists(file_path):
            os.remove(file_path)
            removed = True

        # 清除备份
        backup_dir = os.path.join(self._dir, ".backup", task_id)
        if os.path.exists(backup_dir):
            shutil.rmtree(backup_dir)
            removed = True

        if removed:
            logger.info("清除检查点: %s", task_id)
        return removed

    def clear_all(self) -> int:
        """清除所有检查点

        Returns:
            清除的数量
        """
        count = 0
        for filename in os.listdir(self._dir):
            if filename.endswith(".json"):
                os.remove(os.path.join(self._dir, filename))
                count += 1

        # 清除备份目录
        backup_dir = os.path.join(self._dir, ".backup")
        if os.path.exists(backup_dir):
            shutil.rmtree(backup_dir)

        logger.info("清除所有检查点: %d 个", count)
        return count

    # ── 查询 ──

    def list_checkpoints(self) -> List[CheckpointInfo]:
        """列出所有检查点

        Returns:
            CheckpointInfo 列表
        """
        results = []

        for filename in sorted(os.listdir(self._dir)):
            if not filename.endswith(".json"):
                continue

            file_path = os.path.join(self._dir, filename)
            task_id = filename[:-5]  # 去掉 .json

            try:
                data = self._read_file(file_path)
                results.append(
                    CheckpointInfo(
                        task_id=task_id,
                        state=data.get("state", {}),
                        created_at=data.get("created_at", 0),
                        updated_at=data.get("updated_at", 0),
                        version=data.get("version", 0),
                        metadata=data.get("metadata", {}),
                        file_path=file_path,
                        file_size=os.path.getsize(file_path),
                    )
                )
            except Exception as e:
                logger.warning("读取检查点失败: %s: %s", filename, e)

        return results

    def stats(self) -> CheckpointStats:
        """检查点统计

        Returns:
            CheckpointStats
        """
        checkpoints = self.list_checkpoints()

        if not checkpoints:
            return CheckpointStats()

        return CheckpointStats(
            total=len(checkpoints),
            total_size=sum(c.file_size for c in checkpoints),
            oldest=min(c.created_at for c in checkpoints),
            newest=max(c.updated_at for c in checkpoints),
            task_ids=[c.task_id for c in checkpoints],
        )

    # ── 版本管理 ──

    def list_versions(self, task_id: str) -> List[Dict[str, Any]]:
        """列出检查点的历史版本

        Args:
            task_id: 任务 ID

        Returns:
            版本信息列表
        """
        backup_dir = os.path.join(self._dir, ".backup", task_id)
        versions = []

        if os.path.exists(backup_dir):
            for filename in sorted(os.listdir(backup_dir)):
                if filename.endswith(".json"):
                    file_path = os.path.join(backup_dir, filename)
                    try:
                        data = self._read_file(file_path)
                        versions.append({
                            "version": data.get("version", 0),
                            "updated_at": data.get("updated_at", 0),
                            "file_path": file_path,
                            "file_size": os.path.getsize(file_path),
                        })
                    except Exception:
                        pass

        # 加上当前版本
        current = self.load_info(task_id)
        if current:
            versions.append({
                "version": current.version,
                "updated_at": current.updated_at,
                "file_path": current.file_path,
                "file_size": current.file_size,
                "current": True,
            })

        return sorted(versions, key=lambda v: v.get("version", 0))

    def restore_version(self, task_id: str, version: int) -> bool:
        """恢复到指定版本

        Args:
            task_id: 任务 ID
            version: 目标版本号

        Returns:
            是否成功
        """
        backup_dir = os.path.join(self._dir, ".backup", task_id)
        backup_path = os.path.join(backup_dir, f"v{version}.json")

        if not os.path.exists(backup_path):
            return False

        # 读取备份
        data = self._read_file(backup_path)

        # 覆盖当前
        file_path = self._task_path(task_id)
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False, default=str)

        logger.info("恢复检查点: %s → v%d", task_id, version)
        return True

    # ── 便捷方法 ──

    def update(self, task_id: str, updates: Dict[str, Any]) -> CheckpointInfo:
        """增量更新检查点状态

        Args:
            task_id: 任务 ID
            updates: 要更新的键值对

        Returns:
            更新后的 CheckpointInfo
        """
        state = {}
        if self.has_checkpoint(task_id):
            state = self.load(task_id)
            if isinstance(state, dict):
                state.update(updates)
            else:
                state = updates
        else:
            state = updates

        return self.save(task_id, state)

    def get_or_default(self, task_id: str, default: Any = None) -> Any:
        """获取检查点状态，不存在则返回默认值

        Args:
            task_id: 任务 ID
            default: 默认值

        Returns:
            状态或默认值
        """
        if self.has_checkpoint(task_id):
            return self.load(task_id)
        return default

    def cleanup_old(self, max_age_days: float = 30) -> int:
        """清理过期检查点

        Args:
            max_age_days: 最大保留天数

        Returns:
            清理的数量
        """
        cutoff = time.time() - max_age_days * 86400
        count = 0

        for filename in os.listdir(self._dir):
            if not filename.endswith(".json"):
                continue

            file_path = os.path.join(self._dir, filename)
            try:
                data = self._read_file(file_path)
                updated = data.get("updated_at", 0)
                if updated < cutoff:
                    task_id = filename[:-5]
                    self.clear(task_id)
                    count += 1
            except Exception:
                pass

        return count

    # ── 内部方法 ──

    def _task_path(self, task_id: str) -> str:
        """获取任务的检查点文件路径"""
        return os.path.join(self._dir, f"{task_id}.json")

    def _validate_task_id(self, task_id: str) -> None:
        """验证任务 ID（防路径注入）"""
        if not task_id or not task_id.strip():
            raise ValueError("task_id 不能为空")

        # 只允许字母、数字、下划线、连字符、点
        import re
        if not re.match(r"^[\w\-. ]+$", task_id):
            raise ValueError(
                f"task_id 包含非法字符: '{task_id}' "
                f"(只允许字母/数字/下划线/连字符/点/空格)"
            )

        # 防路径遍历
        if ".." in task_id or "/" in task_id or "\\" in task_id:
            raise ValueError(f"task_id 不能包含路径分隔符: '{task_id}'")

    def _read_file(self, path: str) -> Dict:
        """读取 JSON 文件"""
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _get_created_at(self, task_id: str, default: float) -> float:
        """获取原始创建时间"""
        try:
            data = self._read_file(self._task_path(task_id))
            return data.get("created_at", default)
        except Exception:
            return default

    def _backup(self, task_id: str, version: int) -> None:
        """备份当前版本"""
        if self._max_versions <= 0:
            return

        file_path = self._task_path(task_id)
        if not os.path.exists(file_path):
            return

        backup_dir = os.path.join(self._dir, ".backup", task_id)
        os.makedirs(backup_dir, exist_ok=True)

        backup_path = os.path.join(backup_dir, f"v{version}.json")
        shutil.copy2(file_path, backup_path)

        # 清理旧版本
        backups = sorted(
            [f for f in os.listdir(backup_dir) if f.endswith(".json")]
        )
        while len(backups) > self._max_versions:
            oldest = backups.pop(0)
            os.remove(os.path.join(backup_dir, oldest))

    @property
    def directory(self) -> str:
        """检查点存储目录"""
        return self._dir
