"""
xuanji 记忆守护

来自灵明血泪史:
  2026-04-02 记忆丢失 — 对话中断导致记忆全丢
  2026-04-04 身份断裂 — 核心身份记忆被覆盖

框架级防护，不再依赖"记得要保存"这种脆弱机制:
  - WAL写入: 记忆先写日志文件再写数据库，崩了可恢复
  - 自动checkpoint: 每N分钟自动存档当前状态
  - 身份保护: identity标记为PERMANENT，不可删除
  - 完整性校验: 启动时检查记忆数量/核心记忆是否完整
  - recover(): 从WAL恢复未提交的记忆
  - checkpoint(): 手动存档
  - verify_integrity(): 完整性检查
"""

import os
import json
import time
import shutil
import threading
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from xuanji.memory.store import MemoryStore


class IntegrityError(Exception):
    """记忆完整性异常"""
    pass


class MemoryGuard:
    """记忆守护者
    
    Args:
        store: MemoryStore实例
        checkpoint_interval: 自动checkpoint间隔（秒），默认300（5分钟）
        min_l3_count: L3最低记忆数（低于此数触发告警），默认0（首次使用）
        identity_keywords: 身份类关键词，包含这些词的记忆自动标记为permanent
    """
    
    def __init__(
        self,
        store: MemoryStore,
        checkpoint_interval: int = 300,
        min_l3_count: int = 0,
        identity_keywords: Optional[List[str]] = None,
    ):
        self.store = store
        self.checkpoint_interval = checkpoint_interval
        self.min_l3_count = min_l3_count
        self.identity_keywords = identity_keywords or [
            "identity", "身份", "灵明", "核心", "誓约", "soul",
            "我是", "名字", "创造者", "老大",
        ]
        
        # checkpoint目录
        self.checkpoint_dir = str(Path(store.db_path).parent / "checkpoints")
        os.makedirs(self.checkpoint_dir, exist_ok=True)
        
        # 自动checkpoint线程
        self._auto_checkpoint_active = False
        self._checkpoint_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        
        # 完整性基线（首次运行后记录）
        self._baseline_file = str(Path(store.db_path).parent / "integrity_baseline.json")
    
    # ========== WAL恢复 ==========
    
    async def recover(self) -> Dict[str, Any]:
        """从WAL日志恢复未提交的记忆
        
        扫描WAL目录，将未入库的记忆重新写入。
        
        Returns:
            恢复报告 {"recovered": 数量, "failed": 数量, "details": [...]}
        """
        wal_dir = self.store.wal_dir
        if not os.path.exists(wal_dir):
            return {"recovered": 0, "failed": 0, "details": []}
        
        recovered = 0
        failed = 0
        details = []
        
        for fname in os.listdir(wal_dir):
            if not fname.endswith(".wal"):
                continue
            
            wal_path = os.path.join(wal_dir, fname)
            try:
                with open(wal_path, "r", encoding="utf-8") as f:
                    entry = json.load(f)
                
                # 检查是否已在数据库中
                mem_id = entry["id"]
                exists = self._memory_exists(mem_id)
                
                if not exists:
                    # 重新存储
                    await self.store.store(
                        content=entry["content"],
                        importance=entry["importance"],
                        tags=entry.get("tags", []),
                        source=entry.get("source", "wal_recovery"),
                        permanent=entry.get("permanent", False),
                    )
                    recovered += 1
                    details.append(f"recovered: {mem_id}")
                
                # 清理WAL文件
                os.remove(wal_path)
                
            except (json.JSONDecodeError, KeyError, OSError) as e:
                failed += 1
                details.append(f"failed: {fname} — {e}")
        
        return {"recovered": recovered, "failed": failed, "details": details}
    
    def _memory_exists(self, mem_id: str) -> bool:
        """检查记忆是否已存在于数据库"""
        with self.store._db_lock:
            row = self.store._conn.execute(
                "SELECT 1 FROM l2_memory WHERE id = ? UNION SELECT 1 FROM l3_memory WHERE id = ?",
                (mem_id, mem_id)
            ).fetchone()
            return row is not None
    
    # ========== Checkpoint ==========
    
    def checkpoint(self, label: str = "") -> str:
        """手动创建检查点（数据库快照）
        
        Args:
            label: 检查点标签
        
        Returns:
            检查点文件路径
        """
        timestamp = int(time.time())
        label_str = f"_{label}" if label else ""
        cp_name = f"checkpoint_{timestamp}{label_str}.db"
        cp_path = os.path.join(self.checkpoint_dir, cp_name)
        
        # 复制数据库文件
        with self.store._db_lock:
            # 先执行SQLite checkpoint确保WAL合并
            self.store._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            shutil.copy2(self.store.db_path, cp_path)
        
        # 清理旧检查点（保留最近10个）
        self._cleanup_checkpoints(keep=10)
        
        return cp_path
    
    def restore_checkpoint(self, checkpoint_path: str) -> bool:
        """从检查点恢复数据库
        
        Args:
            checkpoint_path: 检查点文件路径
        
        Returns:
            是否成功
        """
        if not os.path.exists(checkpoint_path):
            return False
        
        with self.store._db_lock:
            self.store._conn.close()
            
            # 备份当前数据库
            backup_path = self.store.db_path + ".before_restore"
            shutil.copy2(self.store.db_path, backup_path)
            
            # 用检查点替换
            shutil.copy2(checkpoint_path, self.store.db_path)
            
            # 重新连接
            self.store._conn = sqlite3.connect(self.store.db_path, check_same_thread=False)
            self.store._conn.execute("PRAGMA journal_mode=WAL")
            self.store._conn.execute("PRAGMA synchronous=NORMAL")
        
        return True
    
    def _cleanup_checkpoints(self, keep: int = 10):
        """清理旧检查点"""
        checkpoints = sorted(
            [f for f in os.listdir(self.checkpoint_dir) if f.startswith("checkpoint_")],
            reverse=True
        )
        for old in checkpoints[keep:]:
            try:
                os.remove(os.path.join(self.checkpoint_dir, old))
            except OSError:
                pass
    
    # ========== 自动Checkpoint ==========
    
    def start_auto_checkpoint(self):
        """启动自动checkpoint线程"""
        if self._auto_checkpoint_active:
            return
        
        self._auto_checkpoint_active = True
        self._stop_event.clear()
        self._checkpoint_thread = threading.Thread(
            target=self._auto_checkpoint_loop,
            daemon=True,
            name="memory-guard-checkpoint"
        )
        self._checkpoint_thread.start()
    
    def stop_auto_checkpoint(self):
        """停止自动checkpoint"""
        self._auto_checkpoint_active = False
        self._stop_event.set()
        if self._checkpoint_thread:
            self._checkpoint_thread.join(timeout=5)
            self._checkpoint_thread = None
    
    def _auto_checkpoint_loop(self):
        """自动checkpoint循环"""
        while not self._stop_event.is_set():
            self._stop_event.wait(self.checkpoint_interval)
            if self._stop_event.is_set():
                break
            try:
                self.checkpoint(label="auto")
            except Exception:
                pass  # 自动checkpoint失败不影响主流程
    
    # ========== 身份保护 ==========
    
    def protect_identity(self, content: str, tags: Optional[List[str]] = None) -> bool:
        """检查内容是否包含身份关键词，如果是则标记为permanent
        
        Returns:
            是否为身份类内容
        """
        check_text = content.lower()
        if tags:
            check_text += " " + " ".join(t.lower() for t in tags)
        
        for kw in self.identity_keywords:
            if kw.lower() in check_text:
                return True
        return False
    
    async def mark_permanent(self, memory_id: str) -> bool:
        """将记忆标记为永久不可删除
        
        Returns:
            是否成功
        """
        with self.store._db_lock:
            cur = self.store._conn.execute(
                "UPDATE l3_memory SET is_permanent = 1 WHERE id = ?",
                (memory_id,)
            )
            self.store._conn.commit()
            return cur.rowcount > 0
    
    # ========== 完整性校验 ==========
    
    def verify_integrity(self) -> Dict[str, Any]:
        """完整性检查
        
        检查项:
          1. 数据库是否可连接
          2. 表结构是否完整
          3. 记忆数量是否低于基线
          4. 核心记忆（permanent）是否完整
          5. 关键词索引是否一致
        
        Returns:
            检查报告 {"ok": bool, "checks": [...], "warnings": [...], "errors": [...]}
        """
        checks = []
        warnings = []
        errors = []
        
        # 1. 数据库连接
        try:
            with self.store._db_lock:
                self.store._conn.execute("SELECT 1").fetchone()
            checks.append("✓ 数据库连接正常")
        except Exception as e:
            errors.append(f"✗ 数据库连接失败: {e}")
            return {"ok": False, "checks": checks, "warnings": warnings, "errors": errors}
        
        # 2. 表结构
        required_tables = {"l2_memory", "l3_memory", "l3_keywords"}
        with self.store._db_lock:
            existing = {
                row[0] for row in
                self.store._conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        
        missing = required_tables - existing
        if missing:
            errors.append(f"✗ 缺失表: {missing}")
        else:
            checks.append("✓ 表结构完整")
        
        # 3. 记忆数量基线
        stats = self.store.stats()
        baseline = self._load_baseline()
        
        if baseline:
            l3_baseline = baseline.get("l3_count", 0)
            if stats["l3_count"] < l3_baseline * 0.8:  # 减少超过20%
                warnings.append(
                    f"⚠ L3记忆数量异常下降: {stats['l3_count']} (基线: {l3_baseline})"
                )
            else:
                checks.append(f"✓ L3记忆数量正常: {stats['l3_count']}")
        else:
            checks.append(f"✓ 首次运行，L3记忆数: {stats['l3_count']}")
        
        if self.min_l3_count > 0 and stats["l3_count"] < self.min_l3_count:
            warnings.append(
                f"⚠ L3记忆低于最低要求: {stats['l3_count']} < {self.min_l3_count}"
            )
        
        # 4. 核心记忆完整性
        with self.store._db_lock:
            permanent_count = self.store._conn.execute(
                "SELECT COUNT(*) FROM l3_memory WHERE is_permanent = 1"
            ).fetchone()[0]
        
        if baseline and baseline.get("permanent_count", 0) > 0:
            if permanent_count < baseline["permanent_count"]:
                errors.append(
                    f"✗ 核心记忆丢失! 当前: {permanent_count}, 基线: {baseline['permanent_count']}"
                )
            else:
                checks.append(f"✓ 核心记忆完整: {permanent_count}条")
        else:
            checks.append(f"✓ 核心记忆: {permanent_count}条")
        
        # 5. 关键词索引一致性
        with self.store._db_lock:
            orphan_count = self.store._conn.execute(
                """SELECT COUNT(*) FROM l3_keywords 
                   WHERE memory_id NOT IN (SELECT id FROM l3_memory)"""
            ).fetchone()[0]
        
        if orphan_count > 0:
            warnings.append(f"⚠ 发现{orphan_count}条孤立关键词索引")
            # 自动清理
            with self.store._db_lock:
                self.store._conn.execute(
                    "DELETE FROM l3_keywords WHERE memory_id NOT IN (SELECT id FROM l3_memory)"
                )
                self.store._conn.commit()
            checks.append(f"✓ 已清理{orphan_count}条孤立索引")
        else:
            checks.append("✓ 关键词索引一致")
        
        # 更新基线
        self._save_baseline(stats)
        
        ok = len(errors) == 0
        return {
            "ok": ok,
            "checks": checks,
            "warnings": warnings,
            "errors": errors,
            "stats": stats,
        }
    
    def _load_baseline(self) -> Optional[Dict]:
        """加载完整性基线"""
        if not os.path.exists(self._baseline_file):
            return None
        try:
            with open(self._baseline_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None
    
    def _save_baseline(self, stats: Dict):
        """保存完整性基线"""
        baseline = {
            "l3_count": stats["l3_count"],
            "permanent_count": stats.get("l3_permanent", 0),
            "total": stats["total"],
            "updated_at": time.time(),
        }
        try:
            with open(self._baseline_file, "w", encoding="utf-8") as f:
                json.dump(baseline, f, ensure_ascii=False, indent=2)
        except OSError:
            pass
    
    # ========== 生命周期 ==========
    
    async def startup(self) -> Dict[str, Any]:
        """启动时调用：恢复 + 校验 + 启动自动checkpoint
        
        Returns:
            启动报告
        """
        report = {}
        
        # 1. WAL恢复
        recovery = await self.recover()
        report["recovery"] = recovery
        
        # 2. 完整性校验
        integrity = self.verify_integrity()
        report["integrity"] = integrity
        
        # 3. 启动自动checkpoint
        self.start_auto_checkpoint()
        report["auto_checkpoint"] = "started"
        
        return report
    
    async def shutdown(self):
        """关闭时调用：最终checkpoint + 停止自动checkpoint"""
        self.stop_auto_checkpoint()
        self.checkpoint(label="shutdown")
    
    def __del__(self):
        try:
            self.stop_auto_checkpoint()
        except Exception:
            pass
