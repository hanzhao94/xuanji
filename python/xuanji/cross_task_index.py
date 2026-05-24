"""
xuanji 跨任务泛化索引系统

不同领域的经验在记忆库中交叉索引，LLM从不同领域的经验中提取共性策略。

架构:
  任务完成 → 提取策略 → 交叉索引 → 泛化检索 → 共性策略提取

零外部依赖。
"""

import json
import os
import time
import hashlib
from typing import Any, Dict, List, Optional, Set
from dataclasses import dataclass, field, asdict


# ============================================================
# 经验索引格式
# ============================================================

@dataclass
class ExperienceIndex:
    """经验索引条目"""
    index_id: str                             # 索引ID
    task_type: str                            # 任务类型 (cli_app/web_scraper/code_gen/...)
    domain: str                               # 领域 (development/web_research/analysis/...)
    
    # 策略
    strategy: str = ""                            # 使用的策略
    strategy_tags: List[str] = field(default_factory=list)
    # ["parallel", "iterative", "template_based", "llm_driven", ...]
    
    # 效果
    success: bool = True                             # 是否成功
    metrics: Dict = field(default_factory=dict)
    # {"time": 90, "quality": 0.85, "tokens": 15000}
    
    # 适用场景
    context: Dict = field(default_factory=dict)
    # {"complexity": "medium", "data_size": "small", "tools_needed": [...]}
    
    # 泛化能力
    generalizable_to: List[str] = field(default_factory=list)
    # 可泛化到的任务类型列表
    
    # 抽象策略（跨领域共性）
    abstract_strategy: str = ""
    # 如: "分而治之→并行执行→结果聚合"
    
    created_at: float = field(default_factory=time.time)
    reuse_count: int = 0
    
    def to_dict(self) -> Dict:
        return asdict(self)


# ============================================================
# 策略提取器
# ============================================================

class StrategyExtractor:
    """从任务经验中提取抽象策略"""
    
    # 通用策略模式
    STRATEGY_PATTERNS = {
        "divide_and_conquer": {
            "name": "分而治之",
            "description": "将大任务分解为小任务，并行执行后聚合结果",
            "applicable_domains": ["development", "research", "analysis"],
        },
        "iterative_refinement": {
            "name": "迭代精炼",
            "description": "先生成粗糙版本，再逐步改进",
            "applicable_domains": ["development", "writing", "design"],
        },
        "template_based": {
            "name": "模板驱动",
            "description": "用预定义模板生成基础结构，再填充内容",
            "applicable_domains": ["development", "writing", "reporting"],
        },
        "llm_driven": {
            "name": "LLM驱动",
            "description": "用LLM生成/分析/决策，人工验证",
            "applicable_domains": ["development", "research", "analysis"],
        },
        "sandbox_verify": {
            "name": "沙盒验证",
            "description": "在隔离环境中验证产出，失败则重试",
            "applicable_domains": ["development", "testing"],
        },
        "memory_driven": {
            "name": "记忆驱动",
            "description": "检索历史经验注入上下文，提升质量",
            "applicable_domains": ["development", "research", "analysis"],
        },
    }
    
    def extract(self, task_type: str, task_result: Dict) -> List[str]:
        """从任务结果中提取使用的策略标签
        
        Returns:
            策略标签列表，如 ["divide_and_conquer", "llm_driven", "memory_driven"]
        """
        tags = []
        
        # 分析任务结构
        if task_result.get("subtasks"):
            tags.append("divide_and_conquer")
        
        # 分析是否用LLM
        if task_result.get("llm_calls", 0) > 0:
            tags.append("llm_driven")
        
        # 分析是否用记忆
        if task_result.get("memory_retrieved", 0) > 0:
            tags.append("memory_driven")
        
        # 分析是否有迭代
        if task_result.get("iterations", 0) > 1:
            tags.append("iterative_refinement")
        
        # 分析是否用模板
        if task_result.get("template_used"):
            tags.append("template_based")
        
        # 分析是否有沙盒验证
        if task_result.get("sandbox_verified"):
            tags.append("sandbox_verify")
        
        return tags
    
    def get_abstract_strategy(self, tags: List[str]) -> str:
        """从策略标签生成抽象策略描述"""
        names = []
        for tag in tags:
            if tag in self.STRATEGY_PATTERNS:
                names.append(self.STRATEGY_PATTERNS[tag]["name"])
        
        if not names:
            return "未知策略"
        
        return "→".join(names)


# ============================================================
# 跨任务索引引擎
# ============================================================

class CrossTaskIndex:
    """跨任务泛化索引引擎
    
    Usage:
        index = CrossTaskIndex(data_dir="D:\\openagent\\workspace\\cross_index")
        
        # 记录经验
        index.record(
            task_type="cli_app",
            domain="development",
            strategy="分而治之→LLM驱动→记忆驱动",
            strategy_tags=["divide_and_conquer", "llm_driven", "memory_driven"],
            success=True,
            metrics={"time": 90, "tokens": 15000},
            generalizable_to=["crud_app", "data_tool"],
        )
        
        # 跨领域检索共性策略
        common = index.find_common_strategies(
            domains=["development", "research", "analysis"],
            min_reuse=1,
        )
        for c in common:
            print(f"[{c.abstract_strategy}] 适用于: {c.generalizable_to}")
    """
    
    def __init__(self, data_dir: str = None):
        self.data_dir = data_dir or os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "workspace", "cross_index"
        )
        self.db_path = os.path.join(self.data_dir, "experience_index.json")
        self._indices: Dict[str, ExperienceIndex] = {}
        self._load()
    
    def _load(self):
        if os.path.exists(self.db_path):
            try:
                with open(self.db_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                for item in data:
                    idx = ExperienceIndex(**item)
                    self._indices[idx.index_id] = idx
            except Exception:
                self._indices = {}
    
    def _save(self):
        os.makedirs(self.data_dir, exist_ok=True)
        with open(self.db_path, 'w', encoding='utf-8') as f:
            json.dump([i.to_dict() for i in self._indices.values()],
                     f, ensure_ascii=False, indent=2)
    
    def record(
        self,
        task_type: str,
        domain: str,
        strategy: str,
        strategy_tags: List[str],
        success: bool = True,
        metrics: Dict = None,
        context: Dict = None,
        generalizable_to: List[str] = None,
        abstract_strategy: str = "",
    ) -> str:
        """记录一条经验索引"""
        index_id = f"{task_type}_{hashlib.md5(str(time.time()).encode()).hexdigest()[:8]}"
        
        extractor = StrategyExtractor()
        if not abstract_strategy:
            abstract_strategy = extractor.get_abstract_strategy(strategy_tags)
        
        idx = ExperienceIndex(
            index_id=index_id,
            task_type=task_type,
            domain=domain,
            strategy=strategy,
            strategy_tags=strategy_tags,
            success=success,
            metrics=metrics or {},
            context=context or {},
            generalizable_to=generalizable_to or [],
            abstract_strategy=abstract_strategy,
        )
        self._indices[index_id] = idx
        self._save()
        return index_id
    
    def search(
        self,
        domain: Optional[str] = None,
        strategy_tag: Optional[str] = None,
        task_type: Optional[str] = None,
        success_only: bool = False,
        limit: int = 20,
    ) -> List[ExperienceIndex]:
        """搜索经验索引"""
        results = list(self._indices.values())
        
        if domain:
            results = [i for i in results if i.domain == domain]
        if strategy_tag:
            results = [i for i in results if strategy_tag in i.strategy_tags]
        if task_type:
            results = [i for i in results if i.task_type == task_type or task_type in i.generalizable_to]
        if success_only:
            results = [i for i in results if i.success]
        
        # 按复用次数排序
        results.sort(key=lambda i: i.reuse_count, reverse=True)
        
        return results[:limit]
    
    def find_common_strategies(
        self,
        domains: List[str],
        min_reuse: int = 1,
    ) -> List[Dict]:
        """找出跨领域的共性策略
        
        Args:
            domains: 要比较的领域列表
            min_reuse: 最小复用次数
        
        Returns:
            [{"abstract_strategy": "...", "domains": [...], "count": N}, ...]
        """
        # 按抽象策略分组
        strategy_map: Dict[str, Dict] = {}
        
        for idx in self._indices.values():
            if idx.domain not in domains:
                continue
            if idx.reuse_count < min_reuse:
                continue
            
            key = idx.abstract_strategy
            if key not in strategy_map:
                strategy_map[key] = {
                    "abstract_strategy": key,
                    "domains": set(),
                    "task_types": set(),
                    "count": 0,
                    "avg_metrics": {},
                }
            
            strategy_map[key]["domains"].add(idx.domain)
            strategy_map[key]["task_types"].add(idx.task_type)
            strategy_map[key]["count"] += 1
            
            # 聚合指标
            for k, v in idx.metrics.items():
                if isinstance(v, (int, float)):
                    if k not in strategy_map[key]["avg_metrics"]:
                        strategy_map[key]["avg_metrics"][k] = []
                    strategy_map[key]["avg_metrics"][k].append(v)
        
        # 计算平均值
        results = []
        for s in strategy_map.values():
            avg = {}
            for k, vals in s["avg_metrics"].items():
                avg[k] = sum(vals) / len(vals) if vals else 0
            s["avg_metrics"] = avg
            s["domains"] = list(s["domains"])
            s["task_types"] = list(s["task_types"])
            results.append(s)
        
        # 按跨领域数量排序
        results.sort(key=lambda x: len(x["domains"]), reverse=True)
        
        return results
    
    def recommend_strategy(
        self,
        target_task_type: str,
        target_domain: str,
    ) -> List[Dict]:
        """为目标任务推荐策略
        
        Returns:
            [{"strategy": "...", "source_domain": "...", "confidence": 0.8}, ...]
        """
        # 找直接匹配
        direct = self.search(task_type=target_task_type, success_only=True)
        # 找同领域匹配
        domain = self.search(domain=target_domain, success_only=True)
        # 找泛化匹配
        generalized = self.search(strategy_tag="llm_driven", success_only=True)
        
        # 合并去重
        seen = set()
        recommendations = []
        
        for idx in direct + domain + generalized:
            if idx.abstract_strategy in seen:
                continue
            seen.add(idx.abstract_strategy)
            
            # 计算置信度
            confidence = 0.5
            if idx.task_type == target_task_type:
                confidence = 0.9
            elif idx.domain == target_domain:
                confidence = 0.7
            elif target_task_type in idx.generalizable_to:
                confidence = 0.6
            
            recommendations.append({
                "strategy": idx.strategy,
                "abstract_strategy": idx.abstract_strategy,
                "source_domain": idx.domain,
                "source_task": idx.task_type,
                "confidence": confidence,
                "metrics": idx.metrics,
            })
        
        recommendations.sort(key=lambda x: x["confidence"], reverse=True)
        return recommendations
    
    def stats(self) -> Dict:
        return {
            "total": len(self._indices),
            "by_domain": self._count_by("domain"),
            "by_task_type": self._count_by("task_type"),
            "total_reuse": sum(i.reuse_count for i in self._indices.values()),
        }
    
    def _count_by(self, key: str) -> Dict:
        counts = {}
        for i in self._indices.values():
            val = getattr(i, key, "unknown")
            counts[val] = counts.get(val, 0) + 1
        return counts


# ============================================================
# 测试
# ============================================================

if __name__ == "__main__":
    import tempfile
    
    tmpdir = tempfile.mkdtemp()
    index = CrossTaskIndex(data_dir=tmpdir)
    
    # 记录今天的经验
    print("=== 记录经验 ===")
    
    index.record(
        task_type="cli_app",
        domain="development",
        strategy="分而治之→LLM驱动→记忆驱动→沙盒验证",
        strategy_tags=["divide_and_conquer", "llm_driven", "memory_driven", "sandbox_verify"],
        success=True,
        metrics={"time": 90, "tokens": 15000, "files": 5},
        generalizable_to=["crud_app", "data_tool", "cli_tool"],
    )
    
    index.record(
        task_type="web_scraper",
        domain="research",
        strategy="分而治之→LLM驱动→记忆驱动",
        strategy_tags=["divide_and_conquer", "llm_driven", "memory_driven"],
        success=True,
        metrics={"time": 47, "tokens": 4270, "pages": 3},
        generalizable_to=["web_research", "content_analysis"],
    )
    
    index.record(
        task_type="code_gen",
        domain="development",
        strategy="模板驱动→LLM驱动→沙盒验证",
        strategy_tags=["template_based", "llm_driven", "sandbox_verify"],
        success=True,
        metrics={"time": 37, "tokens": 35000},
        generalizable_to=["code_review", "code_refactor"],
    )
    
    # 跨领域检索
    print("\n=== 跨领域共性策略 ===")
    common = index.find_common_strategies(
        domains=["development", "research"],
        min_reuse=1,
    )
    for c in common:
        print(f"  [{c['count']}次] {c['abstract_strategy']}")
        print(f"    领域: {c['domains']}")
        print(f"    任务: {c['task_types']}")
    
    # 推荐策略
    print("\n=== 为 data_tool 任务推荐策略 ===")
    recs = index.recommend_strategy("data_tool", "development")
    for r in recs[:3]:
        print(f"  [{r['confidence']}] {r['abstract_strategy']} (来自: {r['source_domain']}/{r['source_task']})")
    
    print(f"\n=== 统计 ===")
    print(f"  {index.stats()}")
