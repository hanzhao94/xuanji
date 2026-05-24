"""
xuanji 三级记忆缓存

L1 工作记忆 — dict，当前任务上下文，任务结束清除
L2 短期记忆 — SQLite，当天活跃记忆，每天consolidate到L3
L3 长期记忆 — SQLite+关键词索引，永久存储，关键词搜索+重要度排序

设计原则:
  - 零外部依赖（sqlite3是Python标准库）
  - importance决定存储层级（1-3→L1, 4-6→L2, 7-10→L3）
  - 自动建表，数据库文件在 ~/.xuanji/data/memory.db
  - 支持标签过滤和关键词搜索
"""

import os
import sqlite3
import time
import json
import hashlib
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# 重要度阈值
L2_THRESHOLD = 4   # importance >= 4 进L2
L3_THRESHOLD = 7   # importance >= 7 直接进L3


def _default_db_path() -> str:
    """默认数据库路径: ~/.xuanji/data/memory.db"""
    home = Path.home() / ".xuanji" / "data"
    home.mkdir(parents=True, exist_ok=True)
    return str(home / "memory.db")


def _content_hash(content: str) -> str:
    """内容指纹，用于去重"""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


class MemoryStore:
    """三级记忆缓存
    
    Args:
        db_path: SQLite数据库路径，默认 ~/.xuanji/data/memory.db
        wal_dir: WAL日志目录，默认 ~/.xuanji/data/wal/
    """
    
    def __init__(self, db_path: Optional[str] = None, wal_dir: Optional[str] = None):
        self.db_path = db_path or _default_db_path()
        
        # WAL目录（供MemoryGuard使用）
        if wal_dir is None:
            self.wal_dir = str(Path(self.db_path).parent / "wal")
        else:
            self.wal_dir = wal_dir
        os.makedirs(self.wal_dir, exist_ok=True)
        
        # L1: 工作记忆（纯内存）
        self._l1: Dict[str, Dict[str, Any]] = {}
        self._l1_lock = threading.Lock()
        
        # L2/L3: SQLite
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")  # WAL模式，崩溃可恢复
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._db_lock = threading.Lock()
        
        self._init_tables()
    
    def _init_tables(self):
        """自动建表"""
        with self._db_lock:
            cur = self._conn.cursor()
            
            # L2 短期记忆表
            cur.execute("""
                CREATE TABLE IF NOT EXISTS l2_memory (
                    id TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    importance INTEGER DEFAULT 5,
                    tags TEXT DEFAULT '[]',
                    created_at REAL NOT NULL,
                    accessed_at REAL NOT NULL,
                    access_count INTEGER DEFAULT 0,
                    content_hash TEXT NOT NULL,
                    source TEXT DEFAULT '',
                    key TEXT DEFAULT '',
                    UNIQUE(content_hash)
                )
            """)
            
            # L3 长期记忆表
            cur.execute("""
                CREATE TABLE IF NOT EXISTS l3_memory (
                    id TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    importance INTEGER DEFAULT 7,
                    tags TEXT DEFAULT '[]',
                    created_at REAL NOT NULL,
                    accessed_at REAL NOT NULL,
                    access_count INTEGER DEFAULT 0,
                    content_hash TEXT NOT NULL,
                    keywords TEXT DEFAULT '',
                    is_permanent INTEGER DEFAULT 0,
                    source TEXT DEFAULT '',
                    consolidated_from TEXT DEFAULT '',
                    key TEXT DEFAULT '',
                    UNIQUE(content_hash)
                )
            """)
            
            # 为已有数据库添加key列
            try:
                cur.execute("ALTER TABLE l2_memory ADD COLUMN key TEXT DEFAULT ''")
            except:
                pass
            try:
                cur.execute("ALTER TABLE l3_memory ADD COLUMN key TEXT DEFAULT ''")
            except:
                pass
            
            # 关键词索引表（L3专用）
            cur.execute("""
                CREATE TABLE IF NOT EXISTS l3_keywords (
                    keyword TEXT NOT NULL,
                    memory_id TEXT NOT NULL,
                    weight REAL DEFAULT 1.0,
                    PRIMARY KEY(keyword, memory_id),
                    FOREIGN KEY(memory_id) REFERENCES l3_memory(id) ON DELETE CASCADE
                )
            """)
            
            # 索引
            cur.execute("CREATE INDEX IF NOT EXISTS idx_l2_importance ON l2_memory(importance DESC)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_l2_created ON l2_memory(created_at DESC)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_l3_importance ON l3_memory(importance DESC)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_l3_keywords ON l3_keywords(keyword)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_l3_permanent ON l3_memory(is_permanent)")
            
            self._conn.commit()
    
    # ========== 存储 ==========
    
    async def store(
        self,
        content: str,
        importance: int = 5,
        tags: Optional[List[str]] = None,
        source: str = "",
        permanent: bool = False,
        key: str = None,
    ) -> str:
        """存储记忆，按importance决定存哪层
        
        用法:
            await ms.store("今天老大说要做开源")
            await ms.store("今天老大说要做开源", importance=8)
            await ms.store(content="记忆内容", importance=5, tags=["工作"])
        
        Args:
            content: 记忆内容
            importance: 重要度 1-10（默认5）
                1-3: L1工作记忆（纯内存，任务结束清除）
                4-6: L2短期记忆（SQLite，当天）
                7-10: L3长期记忆（SQLite，永久）
            tags: 标签列表
            source: 来源标识
            permanent: 是否永久不可删除（身份类记忆）
            key: 自定义记忆ID前缀（可选，默认自动生成）
        
        Returns:
            记忆ID
        """
        importance = max(1, min(10, int(importance)))
        tags = tags or []
        now = time.time()
        content_hash = _content_hash(content)
        mem_id = f"mem_{key or content_hash}_{int(now)}"
        
        # 永久标记强制L3
        if permanent:
            importance = max(importance, L3_THRESHOLD)
        
        if importance < L2_THRESHOLD:
            # L1: 工作记忆
            return self._store_l1(mem_id, content, importance, tags, now, source, key)
        elif importance < L3_THRESHOLD:
            # L2: 短期记忆
            return self._store_l2(mem_id, content, importance, tags, now, content_hash, source, key)
        else:
            # L3: 长期记忆
            return self._store_l3(
                mem_id, content, importance, tags, now, 
                content_hash, source, permanent, key
            )
    
    def _store_l1(
        self, mem_id: str, content: str, importance: int,
        tags: List[str], now: float, source: str, key: str = None
    ) -> str:
        """存入L1工作记忆"""
        with self._l1_lock:
            self._l1[mem_id] = {
                "id": mem_id,
                "content": content,
                "importance": importance,
                "tags": tags,
                "created_at": now,
                "accessed_at": now,
                "access_count": 0,
                "source": source,
                "key": key or "",
                "level": "L1",
            }
        return mem_id
    
    def _store_l2(
        self, mem_id: str, content: str, importance: int,
        tags: List[str], now: float, content_hash: str, source: str, key: str = None
    ) -> str:
        """存入L2短期记忆"""
        # 先写WAL
        self._write_wal("L2", mem_id, content, importance, tags, now, source)
        
        with self._db_lock:
            try:
                self._conn.execute(
                    """INSERT OR IGNORE INTO l2_memory 
                       (id, content, importance, tags, created_at, accessed_at, content_hash, source, key)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (mem_id, content, importance, json.dumps(tags, ensure_ascii=False),
                     now, now, content_hash, source, key or "")
                )
                self._conn.commit()
            except sqlite3.Error:
                # 去重冲突，静默跳过
                pass
        return mem_id
    
    def _store_l3(
        self, mem_id: str, content: str, importance: int,
        tags: List[str], now: float, content_hash: str,
        source: str, permanent: bool, key: str = None
    ) -> str:
        """存入L3长期记忆"""
        keywords = self._extract_keywords(content, tags)
        
        # 先写WAL
        self._write_wal("L3", mem_id, content, importance, tags, now, source, permanent)
        
        with self._db_lock:
            try:
                self._conn.execute(
                    """INSERT OR IGNORE INTO l3_memory
                       (id, content, importance, tags, created_at, accessed_at,
                        content_hash, keywords, is_permanent, source, key)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (mem_id, content, importance, json.dumps(tags, ensure_ascii=False),
                     now, now, content_hash, json.dumps(keywords, ensure_ascii=False),
                     1 if permanent else 0, source, key or "")
                )
                # 写关键词索引
                for kw in keywords:
                    self._conn.execute(
                        """INSERT OR IGNORE INTO l3_keywords (keyword, memory_id, weight)
                           VALUES (?, ?, ?)""",
                        (kw, mem_id, 1.0)
                    )
                self._conn.commit()
            except sqlite3.Error:
                pass
        return mem_id
    
    # ========== 搜索 ==========
    
    async def search(
        self,
        query: str,
        limit: int = 5,
        tags: Optional[List[str]] = None,
        min_importance: int = 0,
        levels: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """搜索记忆 — 关键词搜索+重要度排序
        
        搜索策略:
          1. 从query提取关键词
          2. L3: 通过关键词索引匹配 + 内容LIKE搜索
          3. L2: 内容LIKE搜索
          4. L1: 内存遍历
          5. 合并去重，按 (关键词命中数*2 + importance) 排序
        
        Args:
            query: 搜索关键词
            limit: 最大返回数
            tags: 过滤标签（可选）
            min_importance: 最低重要度
            levels: 指定搜索层级，如 ["L1","L2","L3"]
        
        Returns:
            记忆列表，按相关度+重要度排序
        """
        if not levels:
            levels = ["L1", "L2", "L3"]
        
        query_keywords = self._extract_keywords(query, [])
        results: List[Dict[str, Any]] = []
        seen_hashes: set = set()
        
        # 搜索L3
        if "L3" in levels:
            l3_results = self._search_l3(query, query_keywords, tags, min_importance)
            for r in l3_results:
                h = r.get("content_hash", r["id"])
                if h not in seen_hashes:
                    seen_hashes.add(h)
                    results.append(r)
        
        # 搜索L2
        if "L2" in levels:
            l2_results = self._search_l2(query, tags, min_importance)
            for r in l2_results:
                h = r.get("content_hash", r["id"])
                if h not in seen_hashes:
                    seen_hashes.add(h)
                    results.append(r)
        
        # 搜索L1
        if "L1" in levels:
            l1_results = self._search_l1(query, tags, min_importance)
            for r in l1_results:
                if r["id"] not in seen_hashes:
                    seen_hashes.add(r["id"])
                    results.append(r)
        
        # 排序: 匹配分*2 + importance
        def _safe_float(val, default=0.0):
            try:
                return float(val) if val not in (None, '', 0, "0") else default
            except (ValueError, TypeError):
                return default
        results.sort(key=lambda x: _safe_float(x.get("_score", 0)) * 2 + _safe_float(x.get("importance", 0)), reverse=True)
        
        # 更新访问时间
        top = results[:limit]
        self._touch_memories(top)
        
        return top

    def retrieve(self, key: str) -> Optional[Dict[str, Any]]:
        """同步读取已知key的记忆
        
        Args:
            key: 存储时使用的key
            
        Returns:
            记忆dict或None
        """
        # 先查L1
        for mem in self._l1.values():
            if mem.get("key") == key:
                return mem
        
        # 查L3
        with self._db_lock:
            cursor = self._conn.execute(
                "SELECT * FROM l3_memory WHERE key = ?", (key,)
            )
            row = cursor.fetchone()
            if row:
                cols = [desc[0] for desc in cursor.description]
                return dict(zip(cols, row))
            
            # 查L2
            cursor = self._conn.execute(
                "SELECT * FROM l2_memory WHERE key = ?", (key,)
            )
            row = cursor.fetchone()
            if row:
                cols = [desc[0] for desc in cursor.description]
                return dict(zip(cols, row))
        
        return None

    def _search_l3(
        self, query: str, keywords: List[str],
        tags: Optional[List[str]], min_importance: int
    ) -> List[Dict]:
        """L3搜索：关键词索引 + 内容LIKE"""
        results = []
        with self._db_lock:
            # 关键词索引匹配
            if keywords:
                placeholders = ",".join("?" for _ in keywords)
                rows = self._conn.execute(
                    f"""SELECT m.*, COUNT(k.keyword) as hit_count
                        FROM l3_memory m
                        JOIN l3_keywords k ON m.id = k.memory_id
                        WHERE k.keyword IN ({placeholders})
                          AND m.importance >= ?
                        GROUP BY m.id
                        ORDER BY hit_count DESC, m.importance DESC
                        LIMIT 50""",
                    (*keywords, min_importance)
                ).fetchall()
                
                cols = ["id", "content", "importance", "tags", "created_at",
                        "accessed_at", "access_count", "content_hash", "keywords",
                        "is_permanent", "source", "consolidated_from", "hit_count"]
                for row in rows:
                    d = dict(zip(cols, row))
                    d["level"] = "L3"
                    d["_score"] = d.pop("hit_count", 1)
                    d["tags"] = json.loads(d.get("tags", "[]"))
                    if tags and not set(tags) & set(d["tags"]):
                        continue
                    results.append(d)
            
            # 补充LIKE搜索（捕获关键词索引遗漏的）
            like_pattern = f"%{query}%"
            rows = self._conn.execute(
                """SELECT * FROM l3_memory
                   WHERE content LIKE ? AND importance >= ?
                   ORDER BY importance DESC
                   LIMIT 20""",
                (like_pattern, min_importance)
            ).fetchall()
            
            cols = ["id", "content", "importance", "tags", "created_at",
                    "accessed_at", "access_count", "content_hash", "keywords",
                    "is_permanent", "source", "consolidated_from"]
            seen_ids = {r["id"] for r in results}
            for row in rows:
                d = dict(zip(cols, row))
                if d["id"] in seen_ids:
                    continue
                d["level"] = "L3"
                d["_score"] = 1
                d["tags"] = json.loads(d.get("tags", "[]"))
                if tags and not set(tags) & set(d["tags"]):
                    continue
                results.append(d)
        
        return results
    
    def _search_l2(
        self, query: str, tags: Optional[List[str]], min_importance: int
    ) -> List[Dict]:
        """L2搜索：内容LIKE"""
        results = []
        like_pattern = f"%{query}%"
        with self._db_lock:
            rows = self._conn.execute(
                """SELECT * FROM l2_memory
                   WHERE content LIKE ? AND importance >= ?
                   ORDER BY importance DESC, created_at DESC
                   LIMIT 20""",
                (like_pattern, min_importance)
            ).fetchall()
            
            cols = ["id", "content", "importance", "tags", "created_at",
                    "accessed_at", "access_count", "content_hash", "source"]
            for row in rows:
                d = dict(zip(cols, row))
                d["level"] = "L2"
                d["_score"] = 1
                d["tags"] = json.loads(d.get("tags", "[]"))
                if tags and not set(tags) & set(d["tags"]):
                    continue
                results.append(d)
        
        return results
    
    def _search_l1(
        self, query: str, tags: Optional[List[str]], min_importance: int
    ) -> List[Dict]:
        """L1搜索：内存遍历"""
        results = []
        query_lower = query.lower()
        with self._l1_lock:
            for mem in self._l1.values():
                if mem["importance"] < min_importance:
                    continue
                if query_lower not in mem["content"].lower():
                    continue
                if tags and not set(tags) & set(mem.get("tags", [])):
                    continue
                entry = {**mem, "_score": 1}
                results.append(entry)
        
        return sorted(results, key=lambda x: x["importance"], reverse=True)
    
    def _touch_memories(self, memories: List[Dict]):
        """更新访问时间和计数"""
        now = time.time()
        with self._db_lock:
            for mem in memories:
                level = mem.get("level", "")
                mid = mem["id"]
                if level == "L2":
                    self._conn.execute(
                        "UPDATE l2_memory SET accessed_at=?, access_count=access_count+1 WHERE id=?",
                        (now, mid)
                    )
                elif level == "L3":
                    self._conn.execute(
                        "UPDATE l3_memory SET accessed_at=?, access_count=access_count+1 WHERE id=?",
                        (now, mid)
                    )
            self._conn.commit()
        
        # L1
        with self._l1_lock:
            for mem in memories:
                if mem.get("level") == "L1" and mem["id"] in self._l1:
                    self._l1[mem["id"]]["accessed_at"] = now
                    self._l1[mem["id"]]["access_count"] += 1
    
    # ========== 沉淀 ==========
    
    async def consolidate(self):
        """记忆沉淀: L1→L2→L3
        
        沉淀规则:
          L1→L2: 工作记忆中importance>=4的，沉淀到L2
          L2→L3: 超过24小时且importance>=7的，或access_count>=3的，沉淀到L3
        """
        await self._consolidate_l1_to_l2()
        await self._consolidate_l2_to_l3()
    
    async def _consolidate_l1_to_l2(self):
        """L1→L2沉淀"""
        to_promote = []
        with self._l1_lock:
            for mid, mem in list(self._l1.items()):
                if mem["importance"] >= L2_THRESHOLD:
                    to_promote.append(mem)
                    del self._l1[mid]
        
        for mem in to_promote:
            await self.store(
                mem["content"],
                importance=mem["importance"],
                tags=mem.get("tags", []),
                source=f"consolidated_from_l1:{mem['id']}"
            )
    
    async def _consolidate_l2_to_l3(self):
        """L2→L3沉淀"""
        now = time.time()
        one_day = 86400
        
        with self._db_lock:
            # 条件: 超过24小时且importance>=7, 或 access_count>=3
            rows = self._conn.execute(
                """SELECT * FROM l2_memory
                   WHERE (created_at < ? AND importance >= ?)
                      OR access_count >= 3
                   ORDER BY importance DESC""",
                (now - one_day, L3_THRESHOLD)
            ).fetchall()
            
            cols = ["id", "content", "importance", "tags", "created_at",
                    "accessed_at", "access_count", "content_hash", "source"]
        
        for row in rows:
            d = dict(zip(cols, row))
            tags = json.loads(d.get("tags", "[]"))
            importance = max(d["importance"], L3_THRESHOLD)
            
            # 存入L3
            content_hash = d["content_hash"]
            keywords = self._extract_keywords(d["content"], tags)
            mem_id = f"mem_{content_hash}_{int(now)}"
            
            self._write_wal("L3", mem_id, d["content"], importance, tags, now, d.get("source", ""))
            
            with self._db_lock:
                try:
                    self._conn.execute(
                        """INSERT OR IGNORE INTO l3_memory
                           (id, content, importance, tags, created_at, accessed_at,
                            content_hash, keywords, is_permanent, source, consolidated_from)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)""",
                        (mem_id, d["content"], importance,
                         json.dumps(tags, ensure_ascii=False),
                         d["created_at"], now, content_hash,
                         json.dumps(keywords, ensure_ascii=False),
                         d.get("source", ""), d["id"])
                    )
                    for kw in keywords:
                        self._conn.execute(
                            "INSERT OR IGNORE INTO l3_keywords (keyword, memory_id) VALUES (?, ?)",
                            (kw, mem_id)
                        )
                    # 从L2删除
                    self._conn.execute("DELETE FROM l2_memory WHERE id = ?", (d["id"],))
                    self._conn.commit()
                except sqlite3.Error:
                    pass
    
    # ========== 清除 ==========
    
    def clear_l1(self):
        """清除L1工作记忆（任务结束时调用）"""
        with self._l1_lock:
            self._l1.clear()
    
    async def forget(self, memory_id: str = None, key: str = None) -> bool:
        """删除记忆（永久记忆不可删除）
        
        Args:
            memory_id: 记忆ID（由store返回）
            key: 兼容用法，通过关键词删除
        
        Returns:
            是否成功删除
        """
        # 兼容 key 参数
        if memory_id is None and key is not None:
            memory_id = key
        if memory_id is None:
            raise ValueError("forget() requires 'memory_id' or 'key' argument")
        # 检查是否永久记忆
        with self._db_lock:
            row = self._conn.execute(
                "SELECT is_permanent FROM l3_memory WHERE id = ?",
                (memory_id,)
            ).fetchone()
            if row and row[0] == 1:
                return False  # 永久记忆不可删除
            
            # 尝试从L3删除
            cur = self._conn.execute("DELETE FROM l3_memory WHERE id = ?", (memory_id,))
            if cur.rowcount > 0:
                self._conn.execute("DELETE FROM l3_keywords WHERE memory_id = ?", (memory_id,))
                self._conn.commit()
                return True
            
            # 尝试从L2删除
            cur = self._conn.execute("DELETE FROM l2_memory WHERE id = ?", (memory_id,))
            if cur.rowcount > 0:
                self._conn.commit()
                return True
        
        # 尝试从L1删除
        with self._l1_lock:
            if memory_id in self._l1:
                del self._l1[memory_id]
                return True
        
        return False
    
    # ========== 统计 ==========
    
    def stats(self) -> Dict[str, Any]:
        """记忆统计"""
        with self._l1_lock:
            l1_count = len(self._l1)
        
        with self._db_lock:
            l2_count = self._conn.execute("SELECT COUNT(*) FROM l2_memory").fetchone()[0]
            l3_count = self._conn.execute("SELECT COUNT(*) FROM l3_memory").fetchone()[0]
            l3_permanent = self._conn.execute(
                "SELECT COUNT(*) FROM l3_memory WHERE is_permanent = 1"
            ).fetchone()[0]
            kw_count = self._conn.execute(
                "SELECT COUNT(DISTINCT keyword) FROM l3_keywords"
            ).fetchone()[0]
        
        return {
            "l1_count": l1_count,
            "l2_count": l2_count,
            "l3_count": l3_count,
            "l3_permanent": l3_permanent,
            "keyword_count": kw_count,
            "total": l1_count + l2_count + l3_count,
            "db_path": self.db_path,
        }
    
    # ========== 工具方法 ==========
    
    def _extract_keywords(self, content: str, tags: List[str]) -> List[str]:
        """从内容和标签提取关键词
        
        简单分词策略:
          1. 标签直接作为关键词
          2. 按空格/标点分词
          3. 过滤停用词和短词
          4. 去重
        """
        keywords = set()
        
        # 标签直接加入
        for tag in tags:
            keywords.add(tag.lower().strip())
        
        # 中文不按空格分词，保留2-4字的连续中文片段
        # 英文按空格分
        import re
        
        # 英文单词
        english_words = re.findall(r'[a-zA-Z]{3,}', content)
        for w in english_words:
            keywords.add(w.lower())
        
        # 中文: 提取2-4字连续汉字片段（简单n-gram）
        chinese_chars = re.findall(r'[\u4e00-\u9fff]+', content)
        for segment in chinese_chars:
            if len(segment) >= 2:
                # 2-gram
                for i in range(len(segment) - 1):
                    keywords.add(segment[i:i+2])
                # 3-gram
                for i in range(len(segment) - 2):
                    keywords.add(segment[i:i+3])
                # 完整片段（如果不太长）
                if 2 <= len(segment) <= 6:
                    keywords.add(segment)
        
        # 停用词过滤
        stop_words = {
            "the", "and", "for", "with", "that", "this", "from", "are",
            "was", "were", "been", "have", "has", "had", "will", "can",
            "not", "but", "all", "any", "的", "了", "是", "在", "有",
            "和", "与", "或", "也", "都", "就", "被", "把", "让",
        }
        keywords -= stop_words
        
        return list(keywords)[:50]  # 限制关键词数量
    
    def _write_wal(
        self, level: str, mem_id: str, content: str,
        importance: int, tags: List[str], timestamp: float,
        source: str = "", permanent: bool = False
    ):
        """写WAL日志（供MemoryGuard恢复用）"""
        wal_entry = {
            "level": level,
            "id": mem_id,
            "content": content,
            "importance": importance,
            "tags": tags,
            "timestamp": timestamp,
            "source": source,
            "permanent": permanent,
        }
        wal_file = os.path.join(self.wal_dir, f"{mem_id}.wal")
        try:
            with open(wal_file, "w", encoding="utf-8") as f:
                json.dump(wal_entry, f, ensure_ascii=False)
        except OSError:
            pass  # WAL写入失败不阻塞主流程
    
    def _remove_wal(self, mem_id: str):
        """删除已提交的WAL"""
        wal_file = os.path.join(self.wal_dir, f"{mem_id}.wal")
        try:
            os.remove(wal_file)
        except OSError:
            pass
    
    def close(self):
        """关闭数据库连接"""
        with self._db_lock:
            self._conn.close()
    
    def __del__(self):
        try:
            self.close()
        except Exception:
            pass
