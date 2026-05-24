"""
xuanji 记忆压缩器

智能记忆压缩——比简单摘要强。

核心能力：
  - JSONL增量追加（每条记忆一行JSON）
  - 超阈值自动压缩（importance/time_decay/category三种策略）
  - 日志轮转（保留最近N个历史文件）
  - 信号检测（纠正/强化信号自动识别）
  - 记忆去抖（高频写入批量处理）
  - 压缩前钩子（保护有价值信息不被压缩丢失）
  - FNV-1a指纹异常检测

零外部依赖，纯Python标准库。

移植自灵明元系统 memory_compactor.py (1196行)，精简适配xuanji框架。
"""

import json
import math
import re
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# ============================================================
# FNV-1a 哈希
# ============================================================

_FNV_OFFSET = 0xCBF29CE484222325
_FNV_PRIME = 0x100000001B3


def fnv1a_hash(data: bytes) -> int:
    """FNV-1a 64位哈希，用于记忆指纹"""
    h = _FNV_OFFSET
    for b in data:
        h ^= b
        h = (h * _FNV_PRIME) & 0xFFFFFFFFFFFFFFFF
    return h


# ============================================================
# MemoryRecord
# ============================================================

@dataclass
class MemoryRecord:
    """一条记忆记录"""
    id: str
    timestamp: float
    category: str              # experience/decision/lesson/insight/task
    content: str
    importance: float          # 0.0-1.0
    tags: List[str]
    source: str
    compressed: bool = False
    compression_ref: Optional[str] = None
    original_count: Optional[int] = None
    original_ids: Optional[List[str]] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        return {k: v for k, v in d.items() if v is not None}

    @classmethod
    def from_dict(cls, d: dict) -> "MemoryRecord":
        valid = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in d.items() if k in valid})

    @staticmethod
    def gen_id(prefix: str = "mem") -> str:
        return f"{prefix}_{uuid.uuid4().hex[:12]}"


# ============================================================
# MemoryCompactor 核心压缩引擎
# ============================================================

class MemoryCompactor:
    """
    JSONL记忆的增量追加、自动压缩与日志轮转。

    Args:
        memory_dir: 记忆文件目录
        max_file_size_kb: 单文件最大大小，超过触发轮转
        max_rotated_files: 最多保留的历史文件数
        compression_threshold: 未压缩记忆超此数触发压缩
        importance_threshold: 低于此重要性的优先压缩
    """

    FILE_NAME = "memory.jsonl"

    def __init__(self, memory_dir, max_file_size_kb: int = 256,
                 max_rotated_files: int = 3, compression_threshold: int = 100,
                 importance_threshold: float = 0.3):
        self.memory_dir = Path(memory_dir)
        self.max_file_size_kb = max_file_size_kb
        self.max_rotated_files = max_rotated_files
        self.compression_threshold = compression_threshold
        self.importance_threshold = importance_threshold
        self._lock = threading.Lock()
        self._debouncer = MemoryDebouncer(window_seconds=30.0)
        self.memory_dir.mkdir(parents=True, exist_ok=True)

    @property
    def current_file(self) -> Path:
        return self.memory_dir / self.FILE_NAME

    def _ensure_header(self):
        fp = self.current_file
        if not fp.exists() or fp.stat().st_size == 0:
            header = {"_header": True, "version": 1, "created_at": time.time()}
            with open(fp, "w", encoding="utf-8") as f:
                f.write(json.dumps(header, ensure_ascii=False) + "\n")

    def _read_all(self, filepath: Path = None) -> Tuple[Optional[dict], List[MemoryRecord]]:
        fp = filepath or self.current_file
        if not fp.exists():
            return None, []
        header, records = None, []
        with open(fp, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if obj.get("_header"):
                    header = obj
                else:
                    records.append(MemoryRecord.from_dict(obj))
        return header, records

    def _write_all(self, records: List[MemoryRecord], filepath: Path = None):
        fp = filepath or self.current_file
        header = {"_header": True, "version": 1, "created_at": time.time()}
        with open(fp, "w", encoding="utf-8") as f:
            f.write(json.dumps(header, ensure_ascii=False) + "\n")
            for r in records:
                f.write(json.dumps(r.to_dict(), ensure_ascii=False) + "\n")

    # ─── 追加 ───

    def append(self, record: MemoryRecord):
        """追加一条记忆，自动检查轮转和压缩"""
        with self._lock:
            self._ensure_header()
            with open(self.current_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")
            # 轮转检查
            if self.current_file.stat().st_size / 1024 >= self.max_file_size_kb:
                self._rotate_locked()
            # 压缩检查
            _, recs = self._read_all()
            if sum(1 for r in recs if not r.compressed) >= self.compression_threshold:
                self._compress_locked("importance")

    def append_debounced(self, record: MemoryRecord) -> int:
        """去抖追加——窗口内缓冲，超时批量写入"""
        still_buf = self._debouncer.add(record)
        if still_buf:
            return 0
        return self.flush_debounce_buffer()

    def flush_debounce_buffer(self) -> int:
        """强制刷新去抖缓冲"""
        records = self._debouncer.flush()
        for r in records:
            self.append(r)
        return len(records)

    # ─── 压缩 ───

    def compress(self, strategy: str = "importance") -> int:
        """
        手动触发压缩。

        策略:
          importance — 低重要性优先压缩
          time_decay — 越旧越容易被压缩
          category   — 每类别只保留最重要的N条
        """
        with self._lock:
            return self._compress_locked(strategy)

    def _compress_locked(self, strategy: str) -> int:
        _, records = self._read_all()
        if not records:
            return 0
        uncompressed = [r for r in records if not r.compressed]
        already = [r for r in records if r.compressed]
        if len(uncompressed) < 2:
            return 0

        to_keep, to_compress = self._select(uncompressed, strategy)
        if not to_compress:
            return 0

        # 压缩前保护有价值信息
        preserved = PreCompressionHook.extract_valuable(to_compress)
        if preserved:
            to_keep.extend(preserved)

        # 按类别分组生成摘要
        grouped: Dict[str, List[MemoryRecord]] = {}
        for r in to_compress:
            grouped.setdefault(r.category, []).append(r)

        summaries = []
        for cat, recs in grouped.items():
            avg_imp = sum(r.importance for r in recs) / len(recs)
            all_tags = list(set(t for r in recs for t in r.tags))[:10]
            preview = "; ".join(r.content[:60] for r in recs[:5])
            if len(recs) > 5:
                preview += f"; ...共{len(recs)}条"
            summaries.append(MemoryRecord(
                id=MemoryRecord.gen_id("summary"),
                timestamp=time.time(), category=cat,
                content=f"[压缩摘要] {len(recs)}条{cat}: {preview}",
                importance=min(avg_imp + 0.1, 1.0),
                tags=["compressed"] + all_tags,
                source="compactor", compressed=True,
                original_count=len(recs),
                original_ids=[r.id for r in recs],
            ))

        final = sorted(to_keep + already + summaries, key=lambda r: r.timestamp)
        self._write_all(final)
        return len(to_compress)

    def _select(self, records, strategy):
        if strategy == "importance":
            keep = [r for r in records if r.importance >= self.importance_threshold]
            comp = [r for r in records if r.importance < self.importance_threshold]
            if not comp and len(records) >= self.compression_threshold:
                s = sorted(records, key=lambda r: r.importance)
                split = len(s) // 2
                comp = s[:split]
                keep_ids = {r.id for r in s[split:]}
                keep = [r for r in records if r.id in keep_ids]
            return keep, comp
        elif strategy == "time_decay":
            now = time.time()
            scored = [(r.importance * math.exp(-0.05 * (now - r.timestamp) / 86400), r) for r in records]
            scored.sort(key=lambda x: x[0], reverse=True)
            split = max(len(scored) // 2, 1)
            return [r for _, r in scored[:split]], [r for _, r in scored[split:]]
        elif strategy == "category":
            grouped: Dict[str, List[MemoryRecord]] = {}
            for r in records:
                grouped.setdefault(r.category, []).append(r)
            keep, comp = [], []
            for recs in grouped.values():
                s = sorted(recs, key=lambda r: r.importance, reverse=True)
                keep.extend(s[:5])
                comp.extend(s[5:])
            return keep, comp
        raise ValueError(f"未知策略: {strategy}")

    # ─── 轮转 ───

    def rotate(self):
        with self._lock:
            self._rotate_locked()

    def _rotate_locked(self):
        base = self.current_file
        oldest = self.memory_dir / f"{self.FILE_NAME}.{self.max_rotated_files}"
        if oldest.exists():
            oldest.unlink()
        for i in range(self.max_rotated_files - 1, 0, -1):
            src = self.memory_dir / f"{self.FILE_NAME}.{i}"
            dst = self.memory_dir / f"{self.FILE_NAME}.{i + 1}"
            if src.exists():
                src.rename(dst)
        if base.exists() and base.stat().st_size > 0:
            base.rename(self.memory_dir / f"{self.FILE_NAME}.1")
        self._ensure_header()

    # ─── 搜索 ───

    def search(self, query: str, limit: int = 10) -> List[MemoryRecord]:
        """关键词AND搜索，按 importance×新鲜度 排序"""
        keywords = query.strip().lower().split()
        if not keywords:
            return []
        with self._lock:
            _, records = self._read_all()
        for i in range(1, self.max_rotated_files + 1):
            rp = self.memory_dir / f"{self.FILE_NAME}.{i}"
            if rp.exists():
                _, recs = self._read_all(rp)
                records.extend(recs)

        matched = [
            r for r in records
            if all(kw in (r.content + " " + " ".join(r.tags) + " " + r.category).lower()
                   for kw in keywords)
        ]
        now = time.time()
        matched.sort(
            key=lambda r: r.importance * math.exp(-0.01 * max((now - r.timestamp) / 86400, 0.01)),
            reverse=True,
        )
        return matched[:limit]

    # ─── 统计 ───

    def get_stats(self) -> dict:
        with self._lock:
            _, records = self._read_all()
        total = len(records)
        compressed = sum(1 for r in records if r.compressed)
        size = self.current_file.stat().st_size if self.current_file.exists() else 0
        cats: Dict[str, int] = {}
        for r in records:
            cats[r.category] = cats.get(r.category, 0) + 1
        ts = [r.timestamp for r in records] if records else []
        rotated = [
            f"{self.FILE_NAME}.{i}"
            for i in range(1, self.max_rotated_files + 1)
            if (self.memory_dir / f"{self.FILE_NAME}.{i}").exists()
        ]
        return {
            "total_records": total,
            "compressed_records": compressed,
            "compression_ratio": compressed / total if total else 0.0,
            "file_size_kb": round(size / 1024, 2),
            "category_counts": cats,
            "oldest_timestamp": min(ts) if ts else None,
            "newest_timestamp": max(ts) if ts else None,
            "rotated_files": rotated,
        }


# ============================================================
# SignalDetector — 纠正/强化信号检测
# ============================================================

class SignalDetector:
    """检测用户消息中的纠正/强化信号"""

    _CORRECTION = [
        re.compile(r'(?:你|我)(?:理解|说|做)错了'),
        re.compile(r'不对|不是这样|搞错了|误解了'),
        re.compile(r'应该是|正确的是|实际上是'),
        re.compile(r'重新来|重试|再试一次'),
        re.compile(r'不是这个意思|我的意思是'),
        re.compile(r'you.?(?:got|understood?|said).?(?:it|that).?wrong', re.I),
        re.compile(r'(?:that.?s|this.?is).?(?:not|in)correct', re.I),
        re.compile(r'try.?again|redo', re.I),
    ]
    _REINFORCEMENT = [
        re.compile(r'完全正确|说得对|就是这样'),
        re.compile(r'很好|非常好|太棒了'),
        re.compile(r'继续|保持|没问题'),
        re.compile(r'正是我想要的|就是这个'),
        re.compile(r'(?:that.?s|this.?is).?(?:exactly|perfectly)', re.I),
        re.compile(r'(?:well|great|good|nice).?(?:job|work|done)', re.I),
        re.compile(r'perfect|spot.?on|nailed.?it', re.I),
    ]

    @classmethod
    def detect(cls, text: str) -> Optional[str]:
        """返回 'correction' / 'reinforcement' / None"""
        if not text:
            return None
        for p in cls._CORRECTION:
            if p.search(text):
                return "correction"
        for p in cls._REINFORCEMENT:
            if p.search(text):
                return "reinforcement"
        return None

    @classmethod
    def detect_detail(cls, text: str) -> dict:
        """返回详细信息：signal, matched_pattern, category"""
        if not text:
            return {"signal": None, "matched_pattern": "", "category": ""}
        for p in cls._CORRECTION:
            m = p.search(text)
            if m:
                return {"signal": "correction", "matched_pattern": m.group(), "category": "user_correction"}
        for p in cls._REINFORCEMENT:
            m = p.search(text)
            if m:
                return {"signal": "reinforcement", "matched_pattern": m.group(), "category": "user_reinforcement"}
        return {"signal": None, "matched_pattern": "", "category": ""}


# ============================================================
# MemoryDebouncer — 记忆去抖
# ============================================================

class MemoryDebouncer:
    """窗口内缓冲记忆，超时批量写入"""

    def __init__(self, window_seconds: float = 30.0):
        self._buffer: List[MemoryRecord] = []
        self._window = window_seconds
        self._last_flush = time.time()
        self._lock = threading.Lock()

    def add(self, record: MemoryRecord) -> bool:
        """添加到缓冲。返回True=仍在缓冲，False=需要刷新"""
        with self._lock:
            self._buffer.append(record)
            return (time.time() - self._last_flush) < self._window

    def flush(self) -> List[MemoryRecord]:
        with self._lock:
            records = list(self._buffer)
            self._buffer.clear()
            self._last_flush = time.time()
            return records

    def pending_count(self) -> int:
        with self._lock:
            return len(self._buffer)


# ============================================================
# PreCompressionHook — 压缩前保护钩子
# ============================================================

class PreCompressionHook:
    """压缩前提取有价值信息，防止重要记忆被压缩丢失"""

    _VALUE_PATTERNS = [
        re.compile(r'决定|选择|采用|确认|同意', re.I),
        re.compile(r'重要|关键|注意|警告|危险', re.I),
        re.compile(r'密码|密钥|key|token|secret', re.I),
        re.compile(r'配置|设置|参数|版本', re.I),
        re.compile(r'TODO|FIXME|HACK|BUG|待办|未完成', re.I),
        re.compile(r'教训|经验|学到|踩坑', re.I),
    ]

    @classmethod
    def extract_valuable(cls, records: List[MemoryRecord]) -> List[MemoryRecord]:
        """从即将压缩的记忆中提取有价值的"""
        valuable = []
        for rec in records:
            has_value = rec.importance >= 0.7 or any(p.search(rec.content) for p in cls._VALUE_PATTERNS)
            if has_value:
                valuable.append(MemoryRecord(
                    id=MemoryRecord.gen_id("preserved"),
                    timestamp=time.time(), category=rec.category,
                    content=f"[压缩前保留] {rec.content[:200]}",
                    importance=min(rec.importance + 0.1, 1.0),
                    tags=["pre_compression_preserved"] + rec.tags[:5],
                    source="pre_compression_hook",
                ))
        return valuable


# ============================================================
# MemoryFingerprint — 指纹异常检测
# ============================================================

class MemoryFingerprint:
    """追踪记忆指纹变化，检测异常（记忆消失等）"""

    def __init__(self):
        self.fingerprints: Dict[str, int] = {}
        self._counts: Dict[str, int] = {}
        self._lock = threading.RLock()

    def update(self, category: str, records: List[MemoryRecord]):
        with self._lock:
            data = "|".join(sorted(r.id for r in records)).encode("utf-8")
            self.fingerprints[category] = fnv1a_hash(data)
            self._counts[category] = len(records)

    def detect_anomaly(self) -> Optional[str]:
        with self._lock:
            gone = [c for c, n in self._counts.items() if n == 0]
            return f"记忆消失: {', '.join(gone)}" if gone else None

    def snapshot(self) -> Dict[str, dict]:
        with self._lock:
            cats = set(list(self.fingerprints.keys()) + list(self._counts.keys()))
            return {c: {"fingerprint": self.fingerprints.get(c), "count": self._counts.get(c, 0)} for c in cats}

    def compare(self, old: Dict[str, dict]) -> List[str]:
        with self._lock:
            current = self.snapshot()
            changes = []
            for cat in sorted(set(list(old.keys()) + list(current.keys()))):
                oc, cc = old.get(cat, {}), current.get(cat, {})
                on, cn = oc.get("count", 0), cc.get("count", 0)
                if on > 0 and cn == 0:
                    changes.append(f"[严重] '{cat}': {on}条记忆全部消失")
                elif on > 0 and cn < on * 0.5:
                    changes.append(f"[警告] '{cat}': {on}→{cn}条 (减少>{50}%)")
                elif oc.get("fingerprint") and cc.get("fingerprint") and oc["fingerprint"] != cc["fingerprint"]:
                    changes.append(f"[变更] '{cat}': 指纹变化 ({on}→{cn})")
                elif cat not in old:
                    changes.append(f"[新增] '{cat}': 新增{cn}条")
            return changes
