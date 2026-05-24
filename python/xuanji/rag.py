"""
xuanji RAG 检索增强

基于 SQLite + 关键词匹配的文档存储与检索系统。
零外部依赖，不需要向量数据库。

示例:
    store = DocumentStore("./my_docs.db")
    store.add_document("Python是一种编程语言...", {"source": "wiki"})
    chunks = store.retrieve("Python编程", top_k=3)
    prompt = store.build_prompt("Python怎么用?", chunks)
"""

import hashlib
import json
import math
import os
import re
import sqlite3
import time
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────────

@dataclass
class Chunk:
    """文档切块

    Attributes:
        chunk_id: 唯一 ID
        doc_id: 所属文档 ID
        text: 切块文本
        index: 在文档中的序号
        metadata: 关联元数据
        score: 检索得分（仅检索结果中有值）
    """
    chunk_id: str = ""
    doc_id: str = ""
    text: str = ""
    index: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)
    score: float = 0.0


@dataclass
class Document:
    """文档

    Attributes:
        doc_id: 唯一 ID
        text: 原始文本
        metadata: 元数据
        chunk_count: 切块数量
        created_at: 创建时间
    """
    doc_id: str = ""
    text: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    chunk_count: int = 0
    created_at: float = 0.0


# ─────────────────────────────────────────────
# 文本处理
# ─────────────────────────────────────────────

def chunk_text(
    text: str,
    chunk_size: int = 500,
    overlap: int = 50,
    separator: Optional[str] = None,
) -> List[str]:
    """将文本切成重叠的块

    Args:
        text: 输入文本
        chunk_size: 每块最大字符数
        overlap: 相邻块的重叠字符数
        separator: 分隔符（默认按段落 → 句子 → 字符）

    Returns:
        切块列表
    """
    if not text or not text.strip():
        return []

    if chunk_size <= 0:
        chunk_size = 500
    if overlap < 0:
        overlap = 0
    if overlap >= chunk_size:
        overlap = chunk_size // 4

    # 先按段落分割
    if separator:
        segments = text.split(separator)
    else:
        segments = re.split(r"\n\s*\n", text)
        if len(segments) <= 1:
            # 单段落 → 按句子
            segments = re.split(r"(?<=[。！？.!?])\s*", text)

    chunks: List[str] = []
    current = ""

    for seg in segments:
        seg = seg.strip()
        if not seg:
            continue

        if len(current) + len(seg) + 1 <= chunk_size:
            current = (current + "\n" + seg).strip() if current else seg
        else:
            if current:
                chunks.append(current)
            # 长段落需要强制切分
            if len(seg) > chunk_size:
                sub_chunks = _force_split(seg, chunk_size, overlap)
                chunks.extend(sub_chunks)
                current = ""
            else:
                current = seg

    if current:
        chunks.append(current)

    # 添加重叠
    if overlap > 0 and len(chunks) > 1:
        overlapped = [chunks[0]]
        for i in range(1, len(chunks)):
            prev_tail = chunks[i - 1][-overlap:]
            overlapped.append(prev_tail + "\n" + chunks[i])
        chunks = overlapped

    return chunks


def _force_split(text: str, size: int, overlap: int) -> List[str]:
    """强制按字符数切分长文本"""
    result = []
    step = max(size - overlap, 1)
    for i in range(0, len(text), step):
        result.append(text[i : i + size])
        if i + size >= len(text):
            break
    return result


# ─────────────────────────────────────────────
# 关键词提取与匹配
# ─────────────────────────────────────────────

# 中文停用词（精简版）
_STOP_WORDS = frozenset(
    "的 了 在 是 我 有 和 就 不 人 都 一 一个 上 也 很 到 说 要 去 你 会 "
    "着 没有 看 好 自己 这 他 她 它 们 那 里 又 把 从 被 让 与 而 但 如果 "
    "因为 所以 可以 已经 什么 怎么 为什么 还 得 地 呢 吗 吧 啊 呀 哦 "
    "the a an is are was were be been being have has had do does did "
    "will would shall should may might can could of to in for on with "
    "at by from as into through during before after above below between "
    "and or but not no nor so yet both either neither each every all any "
    "few more most other some such than too very".split()
)

# 匹配中文字符或英文单词
_TOKEN_PATTERN = re.compile(r"[\u4e00-\u9fff]+|[a-zA-Z0-9]+")


def _tokenize(text: str) -> List[str]:
    """分词：中文按字/词，英文按单词

    对中文做简单的 bigram 切分以提高匹配质量。
    """
    raw_tokens = _TOKEN_PATTERN.findall(text.lower())
    tokens = []
    for t in raw_tokens:
        if t in _STOP_WORDS:
            continue
        # 中文 → bigram
        if re.match(r"[\u4e00-\u9fff]", t):
            if len(t) <= 2:
                tokens.append(t)
            else:
                for i in range(len(t) - 1):
                    tokens.append(t[i : i + 2])
                tokens.append(t)  # 也保留完整词
        else:
            tokens.append(t)
    return tokens


def _compute_tf(tokens: List[str]) -> Dict[str, float]:
    """计算词频 (TF)"""
    freq: Dict[str, int] = {}
    for t in tokens:
        freq[t] = freq.get(t, 0) + 1
    total = len(tokens) or 1
    return {t: c / total for t, c in freq.items()}


def _bm25_score(
    query_tokens: List[str],
    doc_tokens: List[str],
    avg_dl: float,
    df: Dict[str, int],
    total_docs: int,
    k1: float = 1.5,
    b: float = 0.75,
) -> float:
    """BM25 评分

    Args:
        query_tokens: 查询分词
        doc_tokens: 文档分词
        avg_dl: 平均文档长度
        df: 文档频率
        total_docs: 总文档数
        k1: 词频饱和参数
        b: 文档长度归一化参数

    Returns:
        BM25 分数
    """
    dl = len(doc_tokens)
    if dl == 0 or avg_dl == 0:
        return 0.0

    doc_tf = _compute_tf(doc_tokens)
    score = 0.0

    for qt in set(query_tokens):
        tf = doc_tf.get(qt, 0)
        if tf == 0:
            continue
        doc_freq = df.get(qt, 0)
        idf = math.log((total_docs - doc_freq + 0.5) / (doc_freq + 0.5) + 1)
        numerator = tf * (k1 + 1)
        denominator = tf + k1 * (1 - b + b * dl / avg_dl)
        score += idf * numerator / denominator

    return score


# ─────────────────────────────────────────────
# 文档存储
# ─────────────────────────────────────────────

class DocumentStore:
    """RAG 文档存储与检索

    使用 SQLite 存储文档和切块，基于 BM25 关键词匹配检索。

    Args:
        db_path: SQLite 数据库路径，默认内存数据库
        chunk_size: 默认切块大小
        chunk_overlap: 默认切块重叠
    """

    def __init__(
        self,
        db_path: str = ":memory:",
        chunk_size: int = 500,
        chunk_overlap: int = 50,
    ) -> None:
        self.db_path = db_path
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        """初始化数据库表"""
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS documents (
                doc_id TEXT PRIMARY KEY,
                text TEXT NOT NULL,
                metadata TEXT DEFAULT '{}',
                chunk_count INTEGER DEFAULT 0,
                created_at REAL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS chunks (
                chunk_id TEXT PRIMARY KEY,
                doc_id TEXT NOT NULL,
                text TEXT NOT NULL,
                tokens TEXT DEFAULT '[]',
                idx INTEGER DEFAULT 0,
                metadata TEXT DEFAULT '{}',
                FOREIGN KEY (doc_id) REFERENCES documents(doc_id)
            );

            CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id);
        """)
        self._conn.commit()

    # ── 文档操作 ──

    def add_document(
        self,
        text: str,
        metadata: Optional[Dict[str, Any]] = None,
        doc_id: Optional[str] = None,
    ) -> str:
        """添加文档并自动切块

        Args:
            text: 文档文本（纯文本或 Markdown）
            metadata: 元数据（来源、标签等）
            doc_id: 自定义文档 ID（默认自动生成）

        Returns:
            文档 ID
        """
        if not text or not text.strip():
            raise ValueError("文档文本不能为空")

        meta = metadata or {}
        if doc_id is None:
            doc_id = hashlib.md5(
                (text[:200] + str(time.time())).encode()
            ).hexdigest()[:16]

        now = time.time()

        # 切块
        chunks = chunk_text(text, self.chunk_size, self.chunk_overlap)

        # 存文档
        self._conn.execute(
            "INSERT OR REPLACE INTO documents (doc_id, text, metadata, chunk_count, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (doc_id, text, json.dumps(meta, ensure_ascii=False), len(chunks), now),
        )

        # 存切块
        for i, chunk in enumerate(chunks):
            cid = f"{doc_id}_c{i}"
            tokens = _tokenize(chunk)
            self._conn.execute(
                "INSERT OR REPLACE INTO chunks (chunk_id, doc_id, text, tokens, idx, metadata) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (cid, doc_id, chunk, json.dumps(tokens, ensure_ascii=False), i,
                 json.dumps(meta, ensure_ascii=False)),
            )

        self._conn.commit()
        logger.info("添加文档 %s，切分为 %d 块", doc_id, len(chunks))
        return doc_id

    def remove_document(self, doc_id: str) -> bool:
        """删除文档及其切块

        Args:
            doc_id: 文档 ID

        Returns:
            是否成功删除
        """
        cursor = self._conn.execute(
            "SELECT doc_id FROM documents WHERE doc_id = ?", (doc_id,)
        )
        if cursor.fetchone() is None:
            return False

        self._conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
        self._conn.execute("DELETE FROM documents WHERE doc_id = ?", (doc_id,))
        self._conn.commit()
        return True

    def get_document(self, doc_id: str) -> Optional[Document]:
        """获取文档

        Args:
            doc_id: 文档 ID

        Returns:
            Document 或 None
        """
        row = self._conn.execute(
            "SELECT * FROM documents WHERE doc_id = ?", (doc_id,)
        ).fetchone()
        if row is None:
            return None
        return Document(
            doc_id=row["doc_id"],
            text=row["text"],
            metadata=json.loads(row["metadata"]),
            chunk_count=row["chunk_count"],
            created_at=row["created_at"],
        )

    def list_documents(self) -> List[Document]:
        """列出所有文档"""
        rows = self._conn.execute(
            "SELECT * FROM documents ORDER BY created_at DESC"
        ).fetchall()
        return [
            Document(
                doc_id=r["doc_id"],
                text=r["text"][:200] + "..." if len(r["text"]) > 200 else r["text"],
                metadata=json.loads(r["metadata"]),
                chunk_count=r["chunk_count"],
                created_at=r["created_at"],
            )
            for r in rows
        ]

    def count(self) -> Dict[str, int]:
        """统计文档和切块数量"""
        docs = self._conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        chunks = self._conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        return {"documents": docs, "chunks": chunks}

    # ── 检索 ──

    def retrieve(
        self,
        query: str,
        top_k: int = 5,
        metadata_filter: Optional[Dict[str, Any]] = None,
    ) -> List[Chunk]:
        """检索最相关的文档块

        基于 BM25 算法进行关键词匹配检索。

        Args:
            query: 查询文本
            top_k: 返回前 K 个结果
            metadata_filter: 元数据过滤条件

        Returns:
            按相关性排序的 Chunk 列表
        """
        if not query or not query.strip():
            return []

        query_tokens = _tokenize(query)
        if not query_tokens:
            return []

        # 加载所有切块
        rows = self._conn.execute("SELECT * FROM chunks").fetchall()
        if not rows:
            return []

        # 计算 DF 和平均文档长度
        all_token_lists: List[List[str]] = []
        df: Dict[str, int] = {}

        for row in rows:
            tokens = json.loads(row["tokens"])
            all_token_lists.append(tokens)
            seen = set(tokens)
            for t in seen:
                df[t] = df.get(t, 0) + 1

        total_docs = len(rows)
        avg_dl = sum(len(tl) for tl in all_token_lists) / total_docs if total_docs else 1

        # BM25 评分
        scored: List[Tuple[float, int]] = []
        for i, row in enumerate(rows):
            # 元数据过滤
            if metadata_filter:
                meta = json.loads(row["metadata"])
                match = all(meta.get(k) == v for k, v in metadata_filter.items())
                if not match:
                    continue

            score = _bm25_score(
                query_tokens, all_token_lists[i], avg_dl, df, total_docs
            )
            if score > 0:
                scored.append((score, i))

        # 排序取 top_k
        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[:top_k]

        results = []
        for score, idx in top:
            row = rows[idx]
            results.append(
                Chunk(
                    chunk_id=row["chunk_id"],
                    doc_id=row["doc_id"],
                    text=row["text"],
                    index=row["idx"],
                    metadata=json.loads(row["metadata"]),
                    score=round(score, 4),
                )
            )

        return results

    # ── Prompt 组装 ──

    def build_prompt(
        self,
        query: str,
        chunks: Optional[List[Chunk]] = None,
        top_k: int = 5,
        system_prefix: str = "请根据以下参考资料回答用户问题。如果参考资料中没有相关信息，请如实说明。",
        max_context_chars: int = 4000,
    ) -> str:
        """组装 RAG prompt

        Args:
            query: 用户问题
            chunks: 预检索的切块（None 则自动检索）
            top_k: 自动检索时的数量
            system_prefix: 系统提示前缀
            max_context_chars: 最大上下文字符数

        Returns:
            组装好的 prompt 字符串
        """
        if chunks is None:
            chunks = self.retrieve(query, top_k=top_k)

        # 截断到最大字符数
        context_parts = []
        total_chars = 0
        for i, chunk in enumerate(chunks):
            remaining = max_context_chars - total_chars
            if remaining <= 0:
                break
            text = chunk.text[:remaining]
            context_parts.append(f"[参考{i + 1}] {text}")
            total_chars += len(text)

        context = "\n\n".join(context_parts)

        prompt = f"""{system_prefix}

---参考资料---
{context}
---参考资料结束---

用户问题: {query}

请回答:"""
        return prompt

    # ── 清理 ──

    def clear(self) -> None:
        """清空所有数据"""
        self._conn.execute("DELETE FROM chunks")
        self._conn.execute("DELETE FROM documents")
        self._conn.commit()

    def close(self) -> None:
        """关闭数据库连接"""
        self._conn.close()

    def __del__(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass
