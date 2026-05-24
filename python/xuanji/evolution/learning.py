"""
xuanji 学习引擎

从经验中提炼可复用的模式——不只是存记忆，是变聪明。

核心能力：
  - 经验沉淀：pattern/pitfall/preference/architecture/tool 五类
  - 关键词搜索：AND逻辑，按置信度排序
  - 过时检测：文件引用失效自动标记
  - 矛盾检测：同key不同insight自动发现
  - 经验替代：新经验自动supersede旧经验（同key）
  - Markdown导出：按类型分组的可读报告

数据格式: JSONL (append-only + 全量覆写更新)
零外部依赖，纯Python标准库。

# 核心逻辑提炼自开源工程实践
"""

import json
import os
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional

VALID_TYPES = {"pattern", "pitfall", "preference", "architecture", "tool"}


# ============================================================
# 数据类
# ============================================================

@dataclass
class Learning:
    """一条经验记录"""
    id: str
    type: str                                  # pattern/pitfall/preference/architecture/tool
    key: str                                   # 2-5词唯一标识
    insight: str                               # 一句话经验
    confidence: int                            # 1-10 置信度
    source: str                                # 来源
    project: str                               # 所属项目
    files: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    timestamp: str = ""
    superseded_by: Optional[str] = None        # 被替代则非None

    def is_active(self) -> bool:
        return self.superseded_by is None


# ============================================================
# 学习引擎
# ============================================================

class LearningEngine:
    """
    学习沉淀引擎 — 经验的积累、检索、维护。

    每条经验有置信度和类型，支持搜索、过时检测、矛盾检测。
    同key的新经验自动supersede旧经验。
    """

    def __init__(self, data_dir: str = "data"):
        self.data_dir = data_dir
        self.data_file = os.path.join(data_dir, "learnings.jsonl")
        os.makedirs(data_dir, exist_ok=True)

    # ─── 内部方法 ───

    def _load_all(self) -> List[Learning]:
        learnings = []
        if not os.path.exists(self.data_file):
            return learnings
        with open(self.data_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    learnings.append(Learning(**json.loads(line)))
                except (json.JSONDecodeError, TypeError):
                    continue
        return learnings

    def _save_all(self, learnings: List[Learning]):
        with open(self.data_file, "w", encoding="utf-8") as f:
            for l in learnings:
                f.write(json.dumps(asdict(l), ensure_ascii=False) + "\n")

    def _append(self, learning: Learning):
        with open(self.data_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(learning), ensure_ascii=False) + "\n")

    def _active(self) -> List[Learning]:
        return [l for l in self._load_all() if l.is_active()]

    # ─── 核心方法 ───

    def add(self, type: str, key: str, insight: str, confidence: int,
            source: str, project: str, files: Optional[List[str]] = None,
            tags: Optional[List[str]] = None) -> str:
        """
        添加一条经验。如果key已存在，旧条目自动标记superseded。

        Args:
            type: 经验类型 (pattern/pitfall/preference/architecture/tool)
            key: 唯一标识（2-5词）
            insight: 一句话经验描述
            confidence: 置信度 1-10
            source: 来源
            project: 所属项目
            files: 相关文件路径
            tags: 标签

        Returns:
            新经验的uuid
        """
        if type not in VALID_TYPES:
            raise ValueError(f"type必须是 {VALID_TYPES} 之一")
        if not (1 <= confidence <= 10):
            raise ValueError(f"confidence必须在1-10之间")

        new_id = str(uuid.uuid4())
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # supersede同key旧条目
        all_learnings = self._load_all()
        dirty = False
        for l in all_learnings:
            if l.key == key and l.is_active():
                l.superseded_by = new_id
                dirty = True

        new_learning = Learning(
            id=new_id, type=type, key=key, insight=insight,
            confidence=confidence, source=source, project=project,
            files=files or [], tags=tags or [], timestamp=now,
        )

        if dirty:
            all_learnings.append(new_learning)
            self._save_all(all_learnings)
        else:
            self._append(new_learning)

        return new_id

    def search(self, query: str, limit: int = 10) -> List[Learning]:
        """
        关键词搜索（AND逻辑），只返回有效条目，按置信度降序。
        """
        keywords = query.lower().split()
        if not keywords:
            return []
        results = []
        for l in self._active():
            text = f"{l.key} {l.insight} {' '.join(l.tags)}".lower()
            if all(kw in text for kw in keywords):
                results.append(l)
        results.sort(key=lambda x: x.confidence, reverse=True)
        return results[:limit]

    def by_project(self, project: str) -> List[Learning]:
        """按项目查看所有有效经验"""
        return [l for l in self._active() if l.project == project]

    def by_type(self, type: str) -> List[Learning]:
        """按类型查看所有有效经验"""
        return [l for l in self._active() if l.type == type]

    def top_pitfalls(self, n: int = 5) -> List[Learning]:
        """置信度最高的N个pitfall"""
        pitfalls = self.by_type("pitfall")
        pitfalls.sort(key=lambda x: x.confidence, reverse=True)
        return pitfalls[:n]

    def check_stale(self) -> List[Learning]:
        """检查过期经验：files字段引用的文件是否还存在"""
        stale = []
        for l in self._active():
            if l.files and any(not os.path.exists(f) for f in l.files):
                stale.append(l)
        return stale

    def check_conflicts(self) -> List[Dict[str, Any]]:
        """检查矛盾：同key有多个有效条目且insight不同"""
        active = self._active()
        by_key: Dict[str, List[Learning]] = {}
        for l in active:
            by_key.setdefault(l.key, []).append(l)
        return [
            {"key": key, "conflicting_learnings": group}
            for key, group in by_key.items()
            if len(group) >= 2 and len(set(l.insight for l in group)) > 1
        ]

    def prune(self, learning_id: str) -> bool:
        """标记一条经验为过期（不物理删除）"""
        all_learnings = self._load_all()
        for l in all_learnings:
            if l.id == learning_id and l.is_active():
                l.superseded_by = "pruned"
                self._save_all(all_learnings)
                return True
        return False

    def export_markdown(self, project: str = None) -> str:
        """导出为Markdown格式，按类型分组"""
        active = self._active()
        if project:
            active = [l for l in active if l.project == project]
        if not active:
            return "# 学习经验库\n\n*暂无记录*\n"

        type_labels = {
            "pitfall": "⚠️ 坑 (Pitfall)",
            "pattern": "🔄 模式 (Pattern)",
            "architecture": "🏗️ 架构 (Architecture)",
            "preference": "💡 偏好 (Preference)",
            "tool": "🔧 工具 (Tool)",
        }
        by_type: Dict[str, List[Learning]] = {}
        for l in active:
            by_type.setdefault(l.type, []).append(l)

        title = f"# 学习经验库" + (f" — {project}" if project else "")
        lines = [title, ""]
        for t in ["pitfall", "pattern", "architecture", "preference", "tool"]:
            group = by_type.get(t, [])
            if not group:
                continue
            group.sort(key=lambda x: x.confidence, reverse=True)
            lines.append(f"## {type_labels.get(t, t)}")
            lines.append("")
            for l in group:
                lines.append(f"- **{l.key}** (置信度:{l.confidence}) — {l.insight}")
                if l.tags:
                    lines.append(f"  - 标签: {' '.join(f'`{t}`' for t in l.tags)}")
                lines.append(f"  - 来源: {l.source} | 项目: {l.project} | {l.timestamp}")
            lines.append("")
        return "\n".join(lines)

    def stats(self) -> Dict[str, Any]:
        """统计：总数、按type/project分布、平均置信度"""
        all_l = self._load_all()
        active = [l for l in all_l if l.is_active()]
        by_type: Dict[str, int] = {}
        by_project: Dict[str, int] = {}
        total_conf = 0
        for l in active:
            by_type[l.type] = by_type.get(l.type, 0) + 1
            by_project[l.project] = by_project.get(l.project, 0) + 1
            total_conf += l.confidence
        return {
            "total": len(all_l),
            "active": len(active),
            "superseded": len(all_l) - len(active),
            "by_type": by_type,
            "by_project": by_project,
            "avg_confidence": round(total_conf / len(active), 2) if active else 0,
        }
