"""
xuanji 知识库模块

结构化知识管理（区别于记忆）。
SQLite存储，关键词索引，FAQ导入导出。
零外部依赖。
"""

import os
import json
import time
import sqlite3
import re
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field, asdict
from pathlib import Path


# ============================================================
# 数据结构
# ============================================================

@dataclass
class KnowledgeEntry:
    """知识条目"""
    id: int = 0
    category: str = ""
    question: str = ""
    answer: str = ""
    tags: List[str] = field(default_factory=list)
    source: str = ""
    confidence: float = 1.0
    created_at: float = 0.0
    updated_at: float = 0.0
    access_count: int = 0

    def to_dict(self) -> Dict:
        d = asdict(self)
        return d

    def __repr__(self) -> str:
        return f"KB#{self.id} [{self.category}] Q: {self.question[:50]}..."


@dataclass
class QueryResult:
    """查询结果"""
    entries: List[KnowledgeEntry]
    total: int
    query: str
    duration_ms: float = 0.0

    def to_dict(self) -> Dict:
        return {
            "entries": [e.to_dict() for e in self.entries],
            "total": self.total,
            "query": self.query,
            "duration_ms": self.duration_ms,
        }


# ============================================================
# 关键词工具
# ============================================================

# 中文停用词（精简版）
_STOP_WORDS_ZH = {
    "的", "了", "在", "是", "我", "有", "和", "就", "不", "人",
    "都", "一", "一个", "上", "也", "很", "到", "说", "要", "去",
    "你", "会", "着", "没有", "看", "好", "自己", "这", "他", "她",
    "什么", "怎么", "如何", "为什么", "可以", "能", "吗", "呢",
}

_STOP_WORDS_EN = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been",
    "being", "have", "has", "had", "do", "does", "did", "will",
    "would", "could", "should", "may", "might", "can", "shall",
    "to", "of", "in", "for", "on", "with", "at", "by", "from",
    "it", "this", "that", "and", "or", "but", "not", "no",
    "what", "how", "why", "when", "where", "who", "which",
}

STOP_WORDS = _STOP_WORDS_ZH | _STOP_WORDS_EN


def extract_keywords(text: str) -> List[str]:
    """从文本提取关键词

    简单分词：
    - 英文按空格分
    - 中文按字符分（2-4字组合）
    - 过滤停用词和短词
    """
    text = text.lower().strip()
    keywords = set()

    # 英文词
    en_words = re.findall(r"[a-zA-Z]+", text)
    for w in en_words:
        w = w.lower()
        if len(w) >= 2 and w not in STOP_WORDS:
            keywords.add(w)

    # 中文：提取2-4字的组合
    zh_chars = re.findall(r"[\u4e00-\u9fff]+", text)
    for seg in zh_chars:
        if len(seg) >= 2:
            # 2字组合
            for i in range(len(seg) - 1):
                w = seg[i:i+2]
                if w not in STOP_WORDS:
                    keywords.add(w)
            # 3字组合
            for i in range(len(seg) - 2):
                w = seg[i:i+3]
                if w not in STOP_WORDS:
                    keywords.add(w)
            # 4字组合
            for i in range(len(seg) - 3):
                w = seg[i:i+4]
                keywords.add(w)

    return list(keywords)


def _compute_relevance(query_keywords: List[str], entry_keywords: str) -> float:
    """计算查询与条目的相关度（0~1）"""
    if not query_keywords or not entry_keywords:
        return 0.0

    entry_kw_set = set(entry_keywords.lower().split(","))
    matches = sum(1 for kw in query_keywords if kw in entry_kw_set)
    return matches / len(query_keywords) if query_keywords else 0.0


# ============================================================
# 知识库
# ============================================================

class KnowledgeBase:
    """知识库 — 结构化知识管理

    用法:
        kb = KnowledgeBase()

        # 添加知识
        kb.add("Python", "如何创建虚拟环境?",
               "使用 python -m venv myenv",
               tags=["python", "venv"])

        # 查询
        results = kb.query("虚拟环境")

        # 导入FAQ
        kb.import_faq("faq.json")

        # 导出
        kb.export("json")
    """

    DEFAULT_DB = os.path.join(
        os.path.expanduser("~"), ".xuanji", "knowledge.db"
    )

    def __init__(self, db_path: Optional[str] = None):
        """
        Args:
            db_path: SQLite数据库路径
        """
        self.db_path = db_path or self.DEFAULT_DB
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()

    def _init_db(self):
        """初始化数据库表"""
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS knowledge (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    category TEXT NOT NULL DEFAULT '',
                    question TEXT NOT NULL,
                    answer TEXT NOT NULL,
                    tags TEXT DEFAULT '',
                    keywords TEXT DEFAULT '',
                    source TEXT DEFAULT '',
                    confidence REAL DEFAULT 1.0,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    access_count INTEGER DEFAULT 0
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_category
                ON knowledge(category)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_keywords
                ON knowledge(keywords)
            """)

    def _conn(self) -> sqlite3.Connection:
        """获取数据库连接"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _row_to_entry(self, row: sqlite3.Row) -> KnowledgeEntry:
        """数据库行 -> KnowledgeEntry"""
        tags_str = row["tags"] or ""
        tags = [t.strip() for t in tags_str.split(",") if t.strip()]
        return KnowledgeEntry(
            id=row["id"],
            category=row["category"],
            question=row["question"],
            answer=row["answer"],
            tags=tags,
            source=row["source"] or "",
            confidence=row["confidence"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            access_count=row["access_count"],
        )

    # ----------------------------------------------------------
    # 核心API
    # ----------------------------------------------------------

    def add(
        self,
        category: str,
        question: str,
        answer: str,
        tags: Optional[List[str]] = None,
        source: str = "",
        confidence: float = 1.0,
    ) -> KnowledgeEntry:
        """添加知识条目

        Args:
            category: 分类
            question: 问题
            answer: 回答
            tags: 标签列表
            source: 来源
            confidence: 置信度(0~1)

        Returns:
            创建的KnowledgeEntry
        """
        now = time.time()
        tags_list = tags or []
        tags_str = ",".join(tags_list)

        # 提取关键词（从question + answer + tags）
        all_text = f"{question} {answer} {' '.join(tags_list)}"
        keywords = extract_keywords(all_text)
        keywords_str = ",".join(keywords)

        with self._conn() as conn:
            cursor = conn.execute(
                """INSERT INTO knowledge
                   (category, question, answer, tags, keywords,
                    source, confidence, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (category, question, answer, tags_str, keywords_str,
                 source, confidence, now, now),
            )
            entry_id = cursor.lastrowid

        return KnowledgeEntry(
            id=entry_id,
            category=category,
            question=question,
            answer=answer,
            tags=tags_list,
            source=source,
            confidence=confidence,
            created_at=now,
            updated_at=now,
        )

    def query(
        self,
        question: str,
        category: Optional[str] = None,
        top_k: int = 5,
        min_confidence: float = 0.0,
    ) -> QueryResult:
        """查询知识库

        Args:
            question: 查询问题
            category: 限定分类
            top_k: 返回条数
            min_confidence: 最低置信度

        Returns:
            QueryResult
        """
        start = time.monotonic()
        query_keywords = extract_keywords(question)

        # 构建SQL
        sql = "SELECT * FROM knowledge WHERE confidence >= ?"
        params: list = [min_confidence]

        if category:
            sql += " AND category = ?"
            params.append(category)

        # 关键词LIKE匹配（基础筛选）
        if query_keywords:
            like_clauses = []
            for kw in query_keywords[:10]:  # 最多10个关键词
                like_clauses.append("(keywords LIKE ? OR question LIKE ? OR answer LIKE ?)")
                pattern = f"%{kw}%"
                params.extend([pattern, pattern, pattern])
            sql += " AND (" + " OR ".join(like_clauses) + ")"

        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()

        # 计算相关度排序
        entries_with_score = []
        for row in rows:
            entry = self._row_to_entry(row)
            # 综合打分：关键词匹配 + 置信度 + 访问量
            kw_score = _compute_relevance(query_keywords, row["keywords"] or "")
            # 问题文本直接匹配加分
            q_lower = question.lower()
            if q_lower in entry.question.lower():
                kw_score += 0.5
            elif entry.question.lower() in q_lower:
                kw_score += 0.3

            final_score = (
                kw_score * 0.7
                + entry.confidence * 0.2
                + min(entry.access_count / 100, 0.1)
            )
            entries_with_score.append((entry, final_score))

        # 排序取top_k
        entries_with_score.sort(key=lambda x: x[1], reverse=True)
        results = [e for e, _ in entries_with_score[:top_k]]

        # 更新访问计数
        if results:
            with self._conn() as conn:
                ids = [e.id for e in results]
                placeholders = ",".join("?" * len(ids))
                conn.execute(
                    f"UPDATE knowledge SET access_count = access_count + 1 "
                    f"WHERE id IN ({placeholders})",
                    ids,
                )

        elapsed = (time.monotonic() - start) * 1000
        return QueryResult(
            entries=results,
            total=len(entries_with_score),
            query=question,
            duration_ms=elapsed,
        )

    def categories(self) -> List[Dict[str, Any]]:
        """列出所有分类及条目数"""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT category, COUNT(*) as count "
                "FROM knowledge GROUP BY category ORDER BY count DESC"
            ).fetchall()
        return [{"category": r["category"], "count": r["count"]} for r in rows]

    # ----------------------------------------------------------
    # 导入导出
    # ----------------------------------------------------------

    def import_faq(
        self,
        file_path: str,
        default_category: str = "FAQ",
    ) -> int:
        """从文件导入FAQ

        支持格式:
        - JSON: [{"question": "...", "answer": "...", "category": "...", "tags": [...]}]
        - 纯文本: Q: ...\nA: ...\n\n (按空行分隔)

        Args:
            file_path: 文件路径
            default_category: 默认分类

        Returns:
            导入条数
        """
        if not os.path.isfile(file_path):
            raise FileNotFoundError(f"文件不存在: {file_path}")

        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()

        count = 0

        if file_path.endswith(".json"):
            # JSON格式
            data = json.loads(content)
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict) and "question" in item and "answer" in item:
                        self.add(
                            category=item.get("category", default_category),
                            question=item["question"],
                            answer=item["answer"],
                            tags=item.get("tags", []),
                            source=file_path,
                        )
                        count += 1
        else:
            # 纯文本 Q&A 格式
            blocks = content.split("\n\n")
            for block in blocks:
                block = block.strip()
                if not block:
                    continue
                lines = block.split("\n")
                q = ""
                a = ""
                for line in lines:
                    line = line.strip()
                    if line.startswith("Q:") or line.startswith("Q："):
                        q = line[2:].strip()
                    elif line.startswith("A:") or line.startswith("A："):
                        a = line[2:].strip()
                if q and a:
                    self.add(
                        category=default_category,
                        question=q,
                        answer=a,
                        source=file_path,
                    )
                    count += 1

        return count

    def export(
        self,
        format: str = "json",
        category: Optional[str] = None,
    ) -> str:
        """导出知识库

        Args:
            format: 导出格式 json/text/markdown
            category: 限定分类

        Returns:
            导出内容字符串
        """
        sql = "SELECT * FROM knowledge"
        params: list = []
        if category:
            sql += " WHERE category = ?"
            params.append(category)
        sql += " ORDER BY category, id"

        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()

        entries = [self._row_to_entry(row) for row in rows]

        if format == "json":
            return json.dumps(
                [e.to_dict() for e in entries],
                ensure_ascii=False,
                indent=2,
            )
        elif format == "markdown":
            lines = ["# Knowledge Base\n"]
            current_cat = ""
            for e in entries:
                if e.category != current_cat:
                    current_cat = e.category
                    lines.append(f"\n## {current_cat}\n")
                lines.append(f"### Q: {e.question}")
                lines.append(f"**A:** {e.answer}")
                if e.tags:
                    lines.append(f"*Tags: {', '.join(e.tags)}*")
                lines.append("")
            return "\n".join(lines)
        else:
            # 纯文本
            lines = []
            for e in entries:
                lines.append(f"[{e.category}]")
                lines.append(f"Q: {e.question}")
                lines.append(f"A: {e.answer}")
                lines.append("")
            return "\n".join(lines)

    # ----------------------------------------------------------
    # 管理API
    # ----------------------------------------------------------

    def get(self, entry_id: int) -> Optional[KnowledgeEntry]:
        """获取单个条目"""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM knowledge WHERE id = ?", (entry_id,)
            ).fetchone()
        return self._row_to_entry(row) if row else None

    def update(
        self,
        entry_id: int,
        question: Optional[str] = None,
        answer: Optional[str] = None,
        category: Optional[str] = None,
        tags: Optional[List[str]] = None,
        confidence: Optional[float] = None,
    ) -> bool:
        """更新条目"""
        entry = self.get(entry_id)
        if not entry:
            return False

        updates = {}
        if question is not None:
            updates["question"] = question
        if answer is not None:
            updates["answer"] = answer
        if category is not None:
            updates["category"] = category
        if tags is not None:
            updates["tags"] = ",".join(tags)
        if confidence is not None:
            updates["confidence"] = confidence

        if not updates:
            return False

        updates["updated_at"] = time.time()

        # 重新生成关键词
        q = question or entry.question
        a = answer or entry.answer
        t = tags or entry.tags
        keywords = extract_keywords(f"{q} {a} {' '.join(t)}")
        updates["keywords"] = ",".join(keywords)

        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [entry_id]

        with self._conn() as conn:
            conn.execute(
                f"UPDATE knowledge SET {set_clause} WHERE id = ?",
                values,
            )
        return True

    def delete(self, entry_id: int) -> bool:
        """删除条目"""
        with self._conn() as conn:
            cursor = conn.execute(
                "DELETE FROM knowledge WHERE id = ?", (entry_id,)
            )
        return cursor.rowcount > 0

    def count(self, category: Optional[str] = None) -> int:
        """条目总数"""
        sql = "SELECT COUNT(*) FROM knowledge"
        params: list = []
        if category:
            sql += " WHERE category = ?"
            params.append(category)
        with self._conn() as conn:
            return conn.execute(sql, params).fetchone()[0]

    def clear(self, category: Optional[str] = None):
        """清空知识库"""
        sql = "DELETE FROM knowledge"
        params: list = []
        if category:
            sql += " WHERE category = ?"
            params.append(category)
        with self._conn() as conn:
            conn.execute(sql, params)

    def stats(self) -> Dict[str, Any]:
        """统计信息"""
        cats = self.categories()
        total = sum(c["count"] for c in cats)
        return {
            "total_entries": total,
            "categories": len(cats),
            "category_details": cats,
            "db_path": self.db_path,
        }


# ============================================================
# 便捷函数
# ============================================================

_default_kb: Optional[KnowledgeBase] = None


def get_kb(**kwargs) -> KnowledgeBase:
    """获取/创建默认知识库"""
    global _default_kb
    if _default_kb is None:
        _default_kb = KnowledgeBase(**kwargs)
    return _default_kb


# ============================================================
# 测试
# ============================================================

if __name__ == "__main__":
    import tempfile

    db_path = os.path.join(tempfile.gettempdir(), "test_kb.db")
    kb = KnowledgeBase(db_path=db_path)

    print("=== 添加知识 ===")
    e1 = kb.add("Python", "如何创建虚拟环境?",
                 "使用 python -m venv myenv 创建虚拟环境",
                 tags=["python", "venv", "环境"])
    print(f"  added: {e1}")

    e2 = kb.add("Python", "如何安装包?",
                 "使用 pip install package_name",
                 tags=["python", "pip"])
    print(f"  added: {e2}")

    e3 = kb.add("Git", "如何创建分支?",
                 "使用 git checkout -b branch_name",
                 tags=["git", "branch"])
    print(f"  added: {e3}")

    print("\n=== 查询 ===")
    result = kb.query("虚拟环境怎么创建")
    print(f"  found {result.total} entries in {result.duration_ms:.1f}ms")
    for e in result.entries:
        print(f"    {e}")

    print("\n=== 分类 ===")
    for cat in kb.categories():
        print(f"  {cat}")

    print("\n=== 导出(markdown) ===")
    md = kb.export("markdown")
    print(f"  {md[:300]}...")

    print(f"\n=== 统计 ===\n  {kb.stats()}")

    # 清理 - 关闭连接后删除
    kb._conn().close()
    import gc
    gc.collect()
    try:
        os.unlink(db_path)
    except OSError:
        pass
