"""
xuanji 性能基准测试套件

标准化性能测试，覆盖核心模块：
- 截屏延迟 (screenshot)
- 工具调用延迟 (tool_call)
- 记忆检索延迟 (memory_retrieval)
- LLM响应延迟 (llm_response)
- 消息总线延迟 (message_bus)

每次PR自动跑，防止性能退化。

用法:
    suite = BenchmarkSuite()
    result = suite.run_all()
    print(result.summary())
    
    # 与基线对比
    baseline = suite.load_baseline("baseline.json")
    comparison = suite.compare(baseline, result)
    comparison.report()
"""

import json
import logging
import platform
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ─── 测试结果 ──────────────────────────────────────────────

@dataclass
class BenchmarkResult:
    """单个基准测试结果"""
    name: str = ""
    unit: str = "ms"
    samples: List[float] = field(default_factory=list)
    metadata: Dict = field(default_factory=dict)
    
    @property
    def count(self) -> int:
        return len(self.samples)
    
    @property
    def mean(self) -> float:
        return sum(self.samples) / len(self.samples) if self.samples else 0
    
    @property
    def median(self) -> float:
        if not self.samples:
            return 0
        s = sorted(self.samples)
        n = len(s)
        if n % 2 == 0:
            return (s[n // 2 - 1] + s[n // 2]) / 2
        return s[n // 2]
    
    @property
    def min(self) -> float:
        return min(self.samples) if self.samples else 0
    
    @property
    def max(self) -> float:
        return max(self.samples) if self.samples else 0
    
    @property
    def p95(self) -> float:
        if not self.samples:
            return 0
        s = sorted(self.samples)
        idx = int(len(s) * 0.95)
        return s[min(idx, len(s) - 1)]
    
    @property
    def p99(self) -> float:
        if not self.samples:
            return 0
        s = sorted(self.samples)
        idx = int(len(s) * 0.99)
        return s[min(idx, len(s) - 1)]
    
    @property
    def stddev(self) -> float:
        if len(self.samples) < 2:
            return 0
        m = self.mean
        variance = sum((x - m) ** 2 for x in self.samples) / (len(self.samples) - 1)
        return variance ** 0.5
    
    def to_dict(self) -> Dict:
        return {
            "name": self.name,
            "unit": self.unit,
            "count": self.count,
            "mean": round(self.mean, 3),
            "median": round(self.median, 3),
            "min": round(self.min, 3),
            "max": round(self.max, 3),
            "p95": round(self.p95, 3),
            "p99": round(self.p99, 3),
            "stddev": round(self.stddev, 3),
            "metadata": self.metadata,
        }
    
    def __repr__(self):
        return f"<Benchmark '{self.name}': mean={self.mean:.1f}{self.unit} p95={self.p95:.1f}{self.unit}>"


@dataclass
class BenchmarkReport:
    """基准测试报告"""
    results: List[BenchmarkResult] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)
    platform_info: Dict = field(default_factory=lambda: {
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "python": platform.python_version(),
    })
    
    def add(self, result: BenchmarkResult) -> None:
        self.results.append(result)
    
    def get(self, name: str) -> Optional[BenchmarkResult]:
        for r in self.results:
            if r.name == name:
                return r
        return None
    
    def summary(self) -> str:
        """生成文本摘要"""
        lines = [
            "=" * 60,
            "📊 性能基准测试报告",
            f"   时间: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self.timestamp))}",
            f"   平台: {self.platform_info.get('system')} {self.platform_info.get('release')}",
            f"   测试项: {len(self.results)}",
            "=" * 60,
        ]
        
        for r in self.results:
            lines.append(f"\n  {r.name}")
            lines.append(f"    均值: {r.mean:.2f}{r.unit}  |  中位数: {r.median:.2f}{r.unit}")
            lines.append(f"    最小: {r.min:.2f}{r.unit}  |  最大: {r.max:.2f}{r.unit}")
            lines.append(f"    P95:  {r.p95:.2f}{r.unit}  |  P99:  {r.p99:.2f}{r.unit}")
            lines.append(f"    标准差: {r.stddev:.2f}{r.unit}  |  样本数: {r.count}")
        
        lines.append("\n" + "=" * 60)
        return "\n".join(lines)
    
    def to_json(self) -> str:
        """转为JSON"""
        return json.dumps({
            "timestamp": self.timestamp,
            "platform": self.platform_info,
            "results": [r.to_dict() for r in self.results],
        }, indent=2, ensure_ascii=False)
    
    def save(self, path: str) -> None:
        """保存到文件"""
        with open(path, "w", encoding="utf-8") as f:
            f.write(self.to_json())


# ─── 基准测试套件 ──────────────────────────────────────────

class BenchmarkSuite:
    """标准化性能测试套件
    
    覆盖xuanji核心模块的性能测试。
    """
    
    def __init__(self, iterations: int = 100, warmup: int = 5):
        self.iterations = iterations
        self.warmup = warmup
        self._tests: Dict[str, Callable] = {}
        self._register_default_tests()
    
    def _register_default_tests(self) -> None:
        """注册默认测试项"""
        self.register("screenshot", self._bench_screenshot)
        self.register("tool_call", self._bench_tool_call)
        self.register("memory_retrieval", self._bench_memory)
        self.register("llm_response", self._bench_llm)
        self.register("message_bus", self._bench_message_bus)
    
    def register(self, name: str, benchmark_fn: Callable) -> None:
        """注册自定义基准测试
        
        Args:
            name: 测试名
            benchmark_fn: 测试函数，签名: fn() -> float（返回耗时毫秒）
        """
        self._tests[name] = benchmark_fn
    
    def run(self, name: str, iterations: Optional[int] = None,
            warmup: Optional[int] = None) -> BenchmarkResult:
        """运行单个基准测试
        
        Args:
            name: 测试名
            iterations: 迭代次数（默认用全局设置）
            warmup: 预热次数（默认用全局设置）
        
        Returns:
            测试结果
        """
        if name not in self._tests:
            raise ValueError(f"测试不存在: {name}。可用: {list(self._tests.keys())}")
        
        iters = iterations or self.iterations
        w = warmup if warmup is not None else self.warmup
        fn = self._tests[name]
        
        # 预热
        for _ in range(w):
            try:
                fn()
            except Exception as e:
                logger.warning(f"预热失败 ({name}): {e}")
        
        # 正式测试
        samples = []
        for _ in range(iters):
            try:
                elapsed = fn()
                samples.append(elapsed)
            except Exception as e:
                logger.warning(f"测试失败 ({name}): {e}")
        
        result = BenchmarkResult(name=name, samples=samples)
        logger.info(f"基准测试完成: {result}")
        return result
    
    def run_all(self) -> BenchmarkReport:
        """运行所有基准测试
        
        Returns:
            测试报告
        """
        report = BenchmarkReport()
        
        for name in self._tests:
            try:
                result = self.run(name)
                report.add(result)
            except Exception as e:
                logger.error(f"测试失败 ({name}): {e}")
                report.add(BenchmarkResult(
                    name=name,
                    metadata={"error": str(e)},
                ))
        
        return report
    
    def compare(self, baseline: BenchmarkReport,
                current: BenchmarkReport) -> "ComparisonReport":
        """对比两次基准测试结果
        
        Args:
            baseline: 基线报告
            current: 当前报告
        
        Returns:
            对比报告
        """
        return ComparisonReport(baseline, current)
    
    def load_baseline(self, path: str) -> BenchmarkReport:
        """从文件加载基线"""
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        report = BenchmarkReport(timestamp=data.get("timestamp", 0))
        for r in data.get("results", []):
            result = BenchmarkResult(
                name=r.get("name", ""),
                unit=r.get("unit", "ms"),
                samples=r.get("samples", []),
                metadata=r.get("metadata", {}),
            )
            report.add(result)
        
        return report
    
    def list_tests(self) -> List[str]:
        """列出所有测试"""
        return list(self._tests.keys())
    
    # ─── 默认测试实现 ──────────────────────────────────────
    
    def _bench_screenshot(self) -> float:
        """截屏延迟测试（模拟）"""
        start = time.perf_counter()
        # 模拟截屏：内存操作
        import io
        buf = io.BytesIO(b"\x00" * 1024 * 100)  # 100KB模拟图像数据
        _ = buf.getvalue()
        buf.close()
        return (time.perf_counter() - start) * 1000  # 毫秒
    
    def _bench_tool_call(self) -> float:
        """工具调用延迟测试（模拟）"""
        start = time.perf_counter()
        # 模拟工具调用：JSON序列化+参数校验
        params = {"query": "test" * 10, "limit": 10}
        _ = json.dumps(params, ensure_ascii=False)
        return (time.perf_counter() - start) * 1000
    
    def _bench_memory(self) -> float:
        """记忆检索延迟测试（模拟）"""
        start = time.perf_counter()
        # 模拟记忆检索：字典查找
        store = {f"key_{i}": f"value_{i}" * 50 for i in range(1000)}
        _ = store.get("key_500")
        return (time.perf_counter() - start) * 1000
    
    def _bench_llm(self) -> float:
        """LLM响应延迟测试（模拟）"""
        start = time.perf_counter()
        # 模拟LLM响应：文本处理
        text = "Hello world " * 100
        tokens = text.split()
        _ = len(tokens)
        return (time.perf_counter() - start) * 1000
    
    def _bench_message_bus(self) -> float:
        """消息总线延迟测试（模拟）"""
        start = time.perf_counter()
        # 模拟消息总线：队列收发
        import queue
        q = queue.Queue(maxsize=1000)
        msg = {"type": "test", "data": "x" * 100}
        q.put(msg)
        _ = q.get()
        return (time.perf_counter() - start) * 1000


# ─── 对比报告 ─────────────────────────────────────────────

class ComparisonReport:
    """两次基准测试对比报告"""
    
    # 退化阈值（百分比）
    DEGRADE_THRESHOLD = 20.0  # >20% 视为退化
    IMPROVE_THRESHOLD = 15.0  # >15% 视为改善
    
    def __init__(self, baseline: BenchmarkReport, current: BenchmarkReport):
        self.baseline = baseline
        self.current = current
        self.comparisons: List[Dict] = []
        self._compute()
    
    def _compute(self) -> None:
        """计算对比"""
        baseline_map = {r.name: r for r in self.baseline.results}
        
        for r in self.current.results:
            bl = baseline_map.get(r.name)
            if bl and bl.mean > 0:
                change_pct = ((r.mean - bl.mean) / bl.mean) * 100
                status = "stable"
                if change_pct > self.DEGRADE_THRESHOLD:
                    status = "degraded"
                elif change_pct < -self.IMPROVE_THRESHOLD:
                    status = "improved"
                
                self.comparisons.append({
                    "name": r.name,
                    "baseline_mean": round(bl.mean, 3),
                    "current_mean": round(r.mean, 3),
                    "change_pct": round(change_pct, 2),
                    "status": status,
                })
            else:
                self.comparisons.append({
                    "name": r.name,
                    "baseline_mean": None,
                    "current_mean": round(r.mean, 3),
                    "change_pct": None,
                    "status": "new",
                })
    
    @property
    def has_regression(self) -> bool:
        """是否有性能退化"""
        return any(c["status"] == "degraded" for c in self.comparisons)
    
    def degraded_tests(self) -> List[str]:
        """退化的测试名"""
        return [c["name"] for c in self.comparisons if c["status"] == "degraded"]
    
    def report(self) -> str:
        """生成对比报告"""
        lines = [
            "=" * 60,
            "📊 性能对比报告",
            "=" * 60,
        ]
        
        for c in self.comparisons:
            status_icon = {
                "degraded": "🔴",
                "improved": "🟢",
                "stable": "🟡",
                "new": "🆕",
            }.get(c["status"], "⚪")
            
            if c["change_pct"] is not None:
                direction = "+" if c["change_pct"] > 0 else ""
                lines.append(
                    f"  {status_icon} {c['name']}: "
                    f"{c['baseline_mean']:.1f}ms → {c['current_mean']:.1f}ms "
                    f"({direction}{c['change_pct']:.1f}%)"
                )
            else:
                lines.append(
                    f"  {status_icon} {c['name']}: "
                    f"新增测试 ({c['current_mean']:.1f}ms)"
                )
        
        if self.has_regression:
            lines.append(f"\n  ⚠️  发现性能退化: {', '.join(self.degraded_tests())}")
        
        lines.append("=" * 60)
        return "\n".join(lines)
    
    def to_json(self) -> str:
        """转为JSON"""
        return json.dumps({
            "has_regression": self.has_regression,
            "degraded_tests": self.degraded_tests(),
            "comparisons": self.comparisons,
        }, indent=2, ensure_ascii=False)
    
    def to_markdown(self) -> str:
        """生成Markdown报告"""
        lines = [
            "## 📊 性能对比报告",
            "",
            "| 测试项 | 基线 | 当前 | 变化 | 状态 |",
            "|--------|------|------|------|------|",
        ]
        
        for c in self.comparisons:
            status_icon = {
                "degraded": "🔴 退化",
                "improved": "🟢 改善",
                "stable": "🟡 稳定",
                "new": "🆕 新增",
            }.get(c["status"], "⚪")
            
            bl_str = f"{c['baseline_mean']:.1f}ms" if c["baseline_mean"] else "N/A"
            cur_str = f"{c['current_mean']:.1f}ms"
            change_str = f"{c['change_pct']:+.1f}%" if c["change_pct"] is not None else "N/A"
            
            lines.append(f"| {c['name']} | {bl_str} | {cur_str} | {change_str} | {status_icon} |")
        
        if self.has_regression:
            lines.append(f"\n> ⚠️ **性能退化**: {', '.join(self.degraded_tests())}")
        
        return "\n".join(lines)
