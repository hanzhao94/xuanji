"""
xuanji A/B测试模块

创建多变体测试、分配变体、记录结果、统计分析。
支持多变体（A/B/C/D...），简单统计：均值/方差/胜率。
零外部依赖。
"""

import os
import json
import time
import hashlib
import math
import random
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field, asdict
from pathlib import Path


# ============================================================
# 数据结构
# ============================================================

@dataclass
class Variant:
    """变体"""
    name: str
    description: str = ""
    weight: float = 1.0  # 分配权重

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class TestRecord:
    """单条测试记录"""
    subject_id: str
    variant: str
    metric: str
    value: float
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class VariantStats:
    """变体统计"""
    name: str
    count: int = 0
    mean: float = 0.0
    variance: float = 0.0
    std_dev: float = 0.0
    min_val: float = float("inf")
    max_val: float = float("-inf")
    sum_val: float = 0.0
    win_rate: float = 0.0  # 相对于其他变体的胜率

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class TestAnalysis:
    """测试分析结果"""
    test_name: str
    metric: str
    winner: str = ""
    confidence: float = 0.0
    variant_stats: Dict[str, VariantStats] = field(default_factory=dict)
    total_records: int = 0
    total_subjects: int = 0
    duration_days: float = 0.0

    def to_dict(self) -> Dict:
        d = asdict(self)
        d["variant_stats"] = {k: v.to_dict() for k, v in self.variant_stats.items()}
        return d

    @property
    def summary(self) -> str:
        if self.winner:
            return (
                f"Winner: {self.winner} "
                f"(confidence: {self.confidence:.1%}, "
                f"records: {self.total_records})"
            )
        return f"No winner yet (records: {self.total_records})"


# ============================================================
# A/B测试引擎
# ============================================================

class ABTestEngine:
    """A/B测试引擎

    用法:
        engine = ABTestEngine()

        # 创建测试
        engine.create_test("button_color", ["red", "blue", "green"])

        # 分配变体
        variant = engine.assign("button_color", "user_123")  # → "blue"

        # 记录结果
        engine.record("button_color", "user_123", "click_rate", 0.15)

        # 分析
        result = engine.analyze("button_color")
        print(result.winner)
    """

    DEFAULT_DIR = os.path.join(
        os.path.expanduser("~"), ".xuanji", "ab_tests"
    )

    def __init__(self, storage_dir: Optional[str] = None):
        """
        Args:
            storage_dir: 测试数据存储目录
        """
        self.storage_dir = storage_dir or self.DEFAULT_DIR
        os.makedirs(self.storage_dir, exist_ok=True)

        # 内存缓存
        self._tests: Dict[str, Dict] = {}       # test_name -> test config
        self._assignments: Dict[str, Dict] = {}  # test_name -> {subject_id: variant}
        self._records: Dict[str, List[TestRecord]] = {}  # test_name -> records

        # 加载已有测试
        self._load_all()

    # ----------------------------------------------------------
    # 核心API
    # ----------------------------------------------------------

    def create_test(
        self,
        name: str,
        variants: List[str],
        description: str = "",
        weights: Optional[List[float]] = None,
    ) -> Dict:
        """创建测试

        Args:
            name: 测试名称
            variants: 变体名称列表（如 ["A", "B"] 或 ["red", "blue", "green"]）
            description: 测试描述
            weights: 各变体权重（默认等权重）

        Returns:
            测试配置dict
        """
        if not variants or len(variants) < 2:
            raise ValueError("至少需要2个变体")

        if weights and len(weights) != len(variants):
            raise ValueError("权重数量必须与变体数量一致")

        if not weights:
            weights = [1.0] * len(variants)

        # 归一化权重
        total_w = sum(weights)
        weights = [w / total_w for w in weights]

        test_config = {
            "name": name,
            "description": description,
            "variants": [
                Variant(name=v, weight=w).to_dict()
                for v, w in zip(variants, weights)
            ],
            "created_at": time.time(),
            "status": "active",
        }

        self._tests[name] = test_config
        self._assignments[name] = {}
        self._records[name] = []
        self._save_test(name)

        return test_config

    def assign(
        self,
        test_name: str,
        subject_id: str,
    ) -> str:
        """为subject分配变体

        同一subject始终返回同一变体（一致性哈希）。

        Args:
            test_name: 测试名称
            subject_id: 受试者ID（用户ID/会话ID等）

        Returns:
            分配的变体名称
        """
        if test_name not in self._tests:
            raise KeyError(f"测试不存在: {test_name}")

        # 已分配过 → 返回缓存
        assignments = self._assignments.get(test_name, {})
        if subject_id in assignments:
            return assignments[subject_id]

        # 一致性哈希分配
        test = self._tests[test_name]
        variants = test["variants"]
        variant_name = self._hash_assign(test_name, subject_id, variants)

        # 缓存
        if test_name not in self._assignments:
            self._assignments[test_name] = {}
        self._assignments[test_name][subject_id] = variant_name
        self._save_test(test_name)

        return variant_name

    def record(
        self,
        test_name: str,
        subject_id: str,
        metric: str,
        value: float,
    ) -> TestRecord:
        """记录测试结果

        Args:
            test_name: 测试名称
            subject_id: 受试者ID
            metric: 指标名（如 "click_rate", "conversion", "satisfaction"）
            value: 指标值

        Returns:
            TestRecord
        """
        if test_name not in self._tests:
            raise KeyError(f"测试不存在: {test_name}")

        # 确保已分配
        variant = self.assign(test_name, subject_id)

        record = TestRecord(
            subject_id=subject_id,
            variant=variant,
            metric=metric,
            value=value,
        )

        if test_name not in self._records:
            self._records[test_name] = []
        self._records[test_name].append(record)
        self._save_test(test_name)

        return record

    def analyze(
        self,
        test_name: str,
        metric: Optional[str] = None,
    ) -> TestAnalysis:
        """分析测试结果

        Args:
            test_name: 测试名称
            metric: 指标名（None=分析第一个出现的指标）

        Returns:
            TestAnalysis
        """
        if test_name not in self._tests:
            raise KeyError(f"测试不存在: {test_name}")

        records = self._records.get(test_name, [])
        if not records:
            return TestAnalysis(test_name=test_name, metric=metric or "")

        # 确定指标
        if not metric:
            metric = records[0].metric

        # 按变体分组
        variant_values: Dict[str, List[float]] = {}
        metric_records = [r for r in records if r.metric == metric]

        for r in metric_records:
            variant_values.setdefault(r.variant, []).append(r.value)

        # 计算各变体统计
        variant_stats: Dict[str, VariantStats] = {}
        for vname, values in variant_values.items():
            stats = self._compute_stats(vname, values)
            variant_stats[vname] = stats

        # 确定赢家
        winner, confidence = self._determine_winner(variant_stats)

        # 计算持续时间
        timestamps = [r.timestamp for r in records]
        duration = (max(timestamps) - min(timestamps)) / 86400 if len(timestamps) > 1 else 0

        # 唯一subject数
        subjects = set(r.subject_id for r in metric_records)

        return TestAnalysis(
            test_name=test_name,
            metric=metric,
            winner=winner,
            confidence=confidence,
            variant_stats=variant_stats,
            total_records=len(metric_records),
            total_subjects=len(subjects),
            duration_days=duration,
        )

    # ----------------------------------------------------------
    # 管理API
    # ----------------------------------------------------------

    def list_tests(self) -> List[Dict]:
        """列出所有测试"""
        result = []
        for name, config in self._tests.items():
            records = self._records.get(name, [])
            assignments = self._assignments.get(name, {})
            result.append({
                "name": name,
                "status": config.get("status", "active"),
                "variants": len(config["variants"]),
                "subjects": len(assignments),
                "records": len(records),
                "created_at": config.get("created_at", 0),
            })
        return result

    def get_test(self, test_name: str) -> Optional[Dict]:
        """获取测试配置"""
        return self._tests.get(test_name)

    def stop_test(self, test_name: str):
        """停止测试"""
        if test_name in self._tests:
            self._tests[test_name]["status"] = "stopped"
            self._save_test(test_name)

    def delete_test(self, test_name: str) -> bool:
        """删除测试"""
        self._tests.pop(test_name, None)
        self._assignments.pop(test_name, None)
        self._records.pop(test_name, None)

        path = self._test_path(test_name)
        if os.path.isfile(path):
            os.remove(path)
            return True
        return False

    def get_assignment(self, test_name: str, subject_id: str) -> Optional[str]:
        """查看某subject的分配"""
        return self._assignments.get(test_name, {}).get(subject_id)

    def get_records(
        self,
        test_name: str,
        metric: Optional[str] = None,
        variant: Optional[str] = None,
    ) -> List[TestRecord]:
        """获取测试记录"""
        records = self._records.get(test_name, [])
        if metric:
            records = [r for r in records if r.metric == metric]
        if variant:
            records = [r for r in records if r.variant == variant]
        return records

    # ----------------------------------------------------------
    # 统计工具
    # ----------------------------------------------------------

    def _compute_stats(self, name: str, values: List[float]) -> VariantStats:
        """计算变体统计"""
        n = len(values)
        if n == 0:
            return VariantStats(name=name)

        total = sum(values)
        mean = total / n
        variance = sum((x - mean) ** 2 for x in values) / n if n > 1 else 0.0
        std_dev = math.sqrt(variance)

        return VariantStats(
            name=name,
            count=n,
            mean=mean,
            variance=variance,
            std_dev=std_dev,
            min_val=min(values),
            max_val=max(values),
            sum_val=total,
        )

    def _determine_winner(
        self,
        stats: Dict[str, VariantStats],
    ) -> Tuple[str, float]:
        """确定赢家和置信度

        使用简单的Z-test近似。
        """
        if len(stats) < 2:
            return ("", 0.0)

        # 找最高均值的变体
        sorted_variants = sorted(
            stats.values(),
            key=lambda s: s.mean,
            reverse=True,
        )

        best = sorted_variants[0]
        second = sorted_variants[1]

        # 样本太少 → 无法判断
        if best.count < 5 or second.count < 5:
            return (best.name, 0.0)

        # 简化Z-test
        # Z = (mean1 - mean2) / sqrt(var1/n1 + var2/n2)
        se1 = best.variance / best.count if best.count > 0 else 0
        se2 = second.variance / second.count if second.count > 0 else 0
        se_diff = math.sqrt(se1 + se2)

        if se_diff == 0:
            # 完全相同
            return (best.name, 1.0 if best.mean > second.mean else 0.0)

        z = (best.mean - second.mean) / se_diff

        # Z → 近似置信度（简化版正态分布CDF）
        confidence = self._z_to_confidence(z)

        # 计算胜率
        for s in stats.values():
            if s.name == best.name:
                s.win_rate = confidence
            else:
                s.win_rate = 1.0 - confidence

        return (best.name, confidence)

    def _z_to_confidence(self, z: float) -> float:
        """Z值转置信度（简化版）"""
        if z <= 0:
            return 0.5
        # 近似: Φ(z) ≈ 1 - 0.5 * exp(-0.5 * z^2 * (1 + 0.33 * z))
        # 简化版，精度足够用于A/B测试
        if z > 4:
            return 0.99
        try:
            p = 1.0 - 0.5 * math.exp(-0.5 * z * z * (1 + 0.33 * abs(z)))
            return min(max(p, 0.5), 0.99)
        except (OverflowError, ValueError):
            return 0.99

    # ----------------------------------------------------------
    # 一致性哈希
    # ----------------------------------------------------------

    def _hash_assign(
        self,
        test_name: str,
        subject_id: str,
        variants: List[Dict],
    ) -> str:
        """一致性哈希分配变体"""
        # 生成哈希
        key = f"{test_name}:{subject_id}"
        h = hashlib.md5(key.encode()).hexdigest()
        hash_val = int(h[:8], 16) / 0xFFFFFFFF  # 0~1

        # 按权重分配
        cumulative = 0.0
        for v in variants:
            cumulative += v.get("weight", 1.0 / len(variants))
            if hash_val <= cumulative:
                return v["name"]

        return variants[-1]["name"]

    # ----------------------------------------------------------
    # 持久化
    # ----------------------------------------------------------

    def _test_path(self, name: str) -> str:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
        return os.path.join(self.storage_dir, f"{safe}.json")

    def _save_test(self, name: str):
        """保存测试到文件"""
        data = {
            "config": self._tests.get(name, {}),
            "assignments": self._assignments.get(name, {}),
            "records": [r.to_dict() for r in self._records.get(name, [])],
        }
        path = self._test_path(name)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _load_all(self):
        """加载所有测试"""
        if not os.path.isdir(self.storage_dir):
            return

        for fname in os.listdir(self.storage_dir):
            if not fname.endswith(".json"):
                continue
            path = os.path.join(self.storage_dir, fname)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                name = data["config"]["name"]
                self._tests[name] = data["config"]
                self._assignments[name] = data.get("assignments", {})
                self._records[name] = [
                    TestRecord(**r) for r in data.get("records", [])
                ]
            except (json.JSONDecodeError, KeyError, TypeError):
                continue


# ============================================================
# 便捷函数
# ============================================================

_default_engine: Optional[ABTestEngine] = None


def get_engine(**kwargs) -> ABTestEngine:
    """获取/创建默认引擎"""
    global _default_engine
    if _default_engine is None:
        _default_engine = ABTestEngine(**kwargs)
    return _default_engine


# ============================================================
# 测试
# ============================================================

if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        engine = ABTestEngine(storage_dir=tmp)

        print("=== 创建测试 ===")
        engine.create_test(
            "button_color",
            ["red", "blue", "green"],
            description="测试按钮颜色对点击率的影响",
        )
        print("  created: button_color [red, blue, green]")

        print("\n=== 分配变体 ===")
        for i in range(10):
            uid = f"user_{i}"
            v = engine.assign("button_color", uid)
            print(f"  {uid} → {v}")

        print("\n=== 一致性检查 ===")
        v1 = engine.assign("button_color", "user_0")
        v2 = engine.assign("button_color", "user_0")
        print(f"  user_0: {v1} == {v2} → {'OK' if v1 == v2 else 'FAIL'}")

        print("\n=== 记录结果 ===")
        random.seed(42)
        for i in range(100):
            uid = f"user_{i}"
            v = engine.assign("button_color", uid)
            # 模拟不同变体的效果
            base = {"red": 0.10, "blue": 0.15, "green": 0.12}
            value = base.get(v, 0.10) + random.gauss(0, 0.03)
            engine.record("button_color", uid, "click_rate", max(0, value))

        print("  recorded 100 entries")

        print("\n=== 分析结果 ===")
        result = engine.analyze("button_color", metric="click_rate")
        print(f"  {result.summary}")
        for vname, stats in result.variant_stats.items():
            print(
                f"    {vname}: n={stats.count}, "
                f"mean={stats.mean:.4f}, std={stats.std_dev:.4f}, "
                f"win_rate={stats.win_rate:.1%}"
            )

        print("\n=== 测试列表 ===")
        for t in engine.list_tests():
            print(f"  {t}")
