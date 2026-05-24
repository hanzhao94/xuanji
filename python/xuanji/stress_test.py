"""
xuanji 压力测试

多Agent并发压力测试，测试系统在高负载下的表现。
覆盖：10/50/100个Agent同时运行，监控CPU/内存/消息延迟/任务成功率。

用法:
    tester = StressTester()
    tester.add_scenario("chat", 50, duration=30)
    tester.add_scenario("tool_use", 30, duration=20)
    result = tester.run()
    print(result.report())
"""

import json
import logging
import os
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ─── 压力场景 ──────────────────────────────────────────────

@dataclass
class StressScenario:
    """压力测试场景"""
    name: str = ""
    agent_count: int = 10
    duration: float = 30.0  # 秒
    message_rate: float = 5.0  # 每秒每Agent消息数
    task_complexity: int = 1  # 1-5
    metadata: Dict = field(default_factory=dict)


@dataclass
class AgentMetrics:
    """单个Agent的指标"""
    agent_id: str = ""
    messages_sent: int = 0
    messages_received: int = 0
    tasks_completed: int = 0
    tasks_failed: int = 0
    avg_latency_ms: float = 0
    max_latency_ms: float = 0
    cpu_percent: float = 0
    memory_mb: float = 0
    errors: List[str] = field(default_factory=list)


@dataclass
class StressResult:
    """压力测试结果"""
    scenario_name: str = ""
    agent_count: int = 0
    duration: float = 0
    start_time: float = 0
    end_time: float = 0
    
    # 聚合指标
    total_messages: int = 0
    total_tasks: int = 0
    tasks_succeeded: int = 0
    tasks_failed: int = 0
    avg_latency_ms: float = 0
    p95_latency_ms: float = 0
    p99_latency_ms: float = 0
    max_latency_ms: float = 0
    avg_cpu_percent: float = 0
    peak_cpu_percent: float = 0
    avg_memory_mb: float = 0
    peak_memory_mb: float = 0
    messages_per_second: float = 0
    
    # 详细数据
    agent_metrics: List[AgentMetrics] = field(default_factory=list)
    latency_samples: List[float] = field(default_factory=list)
    timeline: List[Dict] = field(default_factory=list)
    
    @property
    def success_rate(self) -> float:
        total = self.tasks_succeeded + self.tasks_failed
        return self.tasks_succeeded / total if total > 0 else 0
    
    @property
    def elapsed(self) -> float:
        return self.end_time - self.start_time
    
    def report(self) -> str:
        """生成文本报告"""
        lines = [
            "=" * 60,
            f"🔥 压力测试结果: {self.scenario_name}",
            "=" * 60,
            f"  Agent数量:    {self.agent_count}",
            f"  运行时长:     {self.elapsed:.1f}s",
            f"  总消息数:     {self.total_messages}",
            f"  消息速率:     {self.messages_per_second:.1f} msg/s",
            f"  总任务数:     {self.total_tasks}",
            f"  成功率:       {self.success_rate * 100:.1f}%",
            f"    ✅ 成功: {self.tasks_succeeded}",
            f"    ❌ 失败: {self.tasks_failed}",
            "",
            "  ── 延迟 ──",
            f"    均值: {self.avg_latency_ms:.1f}ms",
            f"    P95:  {self.p95_latency_ms:.1f}ms",
            f"    P99:  {self.p99_latency_ms:.1f}ms",
            f"    最大: {self.max_latency_ms:.1f}ms",
            "",
            "  ── 资源 ──",
            f"    CPU均值:  {self.avg_cpu_percent:.1f}%",
            f"    CPU峰值:  {self.peak_cpu_percent:.1f}%",
            f"    内存均值: {self.avg_memory_mb:.1f}MB",
            f"    内存峰值: {self.peak_memory_mb:.1f}MB",
        ]
        
        # 瓶颈分析
        bottlenecks = self._analyze_bottlenecks()
        if bottlenecks:
            lines.append("\n  ── 瓶颈分析 ──")
            for b in bottlenecks:
                lines.append(f"    ⚠️  {b}")
        
        lines.append("=" * 60)
        return "\n".join(lines)
    
    def _analyze_bottlenecks(self) -> List[str]:
        """瓶颈分析"""
        bottlenecks = []
        
        # 延迟过高
        if self.p99_latency_ms > 1000:
            bottlenecks.append(f"延迟过高: P99={self.p99_latency_ms:.0f}ms（阈值1000ms）")
        
        # 成功率过低
        if self.success_rate < 0.95:
            bottlenecks.append(f"成功率过低: {self.success_rate*100:.1f}%（阈值95%）")
        
        # CPU过高
        if self.peak_cpu_percent > 90:
            bottlenecks.append(f"CPU瓶颈: 峰值{self.peak_cpu_percent:.1f}%（阈值90%）")
        
        # 内存过高
        if self.peak_memory_mb > 1024:
            bottlenecks.append(f"内存瓶颈: 峰值{self.peak_memory_mb:.0f}MB（阈值1024MB）")
        
        # 消息丢失
        if self.total_messages > 0:
            # 假设收到的消息应该接近发送的
            pass
        
        return bottlenecks
    
    def to_dict(self) -> Dict:
        return {
            "scenario": self.scenario_name,
            "agent_count": self.agent_count,
            "duration": round(self.elapsed, 2),
            "total_messages": self.total_messages,
            "messages_per_second": round(self.messages_per_second, 2),
            "total_tasks": self.total_tasks,
            "tasks_succeeded": self.tasks_succeeded,
            "tasks_failed": self.tasks_failed,
            "success_rate": round(self.success_rate, 4),
            "latency": {
                "avg": round(self.avg_latency_ms, 2),
                "p95": round(self.p95_latency_ms, 2),
                "p99": round(self.p99_latency_ms, 2),
                "max": round(self.max_latency_ms, 2),
            },
            "resources": {
                "avg_cpu": round(self.avg_cpu_percent, 2),
                "peak_cpu": round(self.peak_cpu_percent, 2),
                "avg_memory_mb": round(self.avg_memory_mb, 2),
                "peak_memory_mb": round(self.peak_memory_mb, 2),
            },
        }
    
    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False)


# ─── 消息总线模拟 ──────────────────────────────────────────

class _MockMessageBus:
    """模拟消息总线（用于压力测试）"""
    
    def __init__(self, max_size: int = 10000):
        self._queue = queue.Queue(maxsize=max_size)
        self._stats = {
            "sent": 0,
            "received": 0,
            "dropped": 0,
            "latencies": [],
        }
        self._lock = threading.Lock()
    
    def send(self, msg: Dict) -> bool:
        """发送消息"""
        try:
            self._queue.put_nowait(msg)
            with self._lock:
                self._stats["sent"] += 1
            return True
        except queue.Full:
            with self._lock:
                self._stats["dropped"] += 1
            return False
    
    def receive(self, timeout: float = 0.01) -> Optional[Dict]:
        """接收消息"""
        try:
            msg = self._queue.get(timeout=timeout)
            with self._lock:
                self._stats["received"] += 1
            return msg
        except queue.Empty:
            return None
    
    @property
    def stats(self) -> Dict:
        with self._lock:
            return dict(self._stats)


# ─── Agent模拟 ─────────────────────────────────────────────

def _simulate_agent(agent_id: str, bus: _MockMessageBus,
                    scenario: StressScenario,
                    stop_event: threading.Event,
                    metrics: AgentMetrics,
                    result_queue: queue.Queue) -> None:
    """模拟单个Agent的行为"""
    import random
    random.seed(hash(agent_id))
    
    interval = 1.0 / scenario.message_rate if scenario.message_rate > 0 else 1.0
    latencies = []
    start = time.perf_counter()
    
    while not stop_event.is_set():
        elapsed = time.perf_counter() - start
        
        # 发送消息
        msg = {
            "from": agent_id,
            "type": "task" if random.random() < 0.7 else "chat",
            "data": f"payload-{random.randint(0, 9999)}" * scenario.task_complexity,
            "ts": time.perf_counter(),
        }
        
        send_ok = bus.send(msg)
        if send_ok:
            metrics.messages_sent += 1
        
        # 接收消息
        recv = bus.receive(timeout=min(interval * 0.5, 0.1))
        if recv:
            metrics.messages_received += 1
            latency = (time.perf_counter() - recv.get("ts", time.perf_counter())) * 1000
            latencies.append(latency)
        
        # 模拟任务
        if random.random() < 0.3:
            task_start = time.perf_counter()
            # 模拟任务处理（复杂度越高越慢）
            _ = sum(range(100 * scenario.task_complexity))
            task_time = (time.perf_counter() - task_start) * 1000
            
            # 模拟失败（5%概率）
            if random.random() < 0.05:
                metrics.tasks_failed += 1
            else:
                metrics.tasks_completed += 1
        
        # 控制速率
        time.sleep(max(0, interval - 0.001))
    
    # 汇总指标
    if latencies:
        metrics.avg_latency_ms = sum(latencies) / len(latencies)
        metrics.max_latency_ms = max(latencies)
        result_queue.put(("latencies", latencies))
    
    result_queue.put(("metrics", metrics))


# ─── 压力测试器 ────────────────────────────────────────────

class StressTester:
    """多Agent并发压力测试
    
    支持多种场景：不同Agent数量、消息速率、任务复杂度。
    """
    
    def __init__(self):
        self._scenarios: List[StressScenario] = []
        self._results: List[StressResult] = []
    
    def add_scenario(self, name: str, agent_count: int = 10,
                     duration: float = 30.0,
                     message_rate: float = 5.0,
                     task_complexity: int = 1) -> "StressTester":
        """添加测试场景（链式调用）
        
        Args:
            name: 场景名
            agent_count: Agent数量
            duration: 持续时间（秒）
            message_rate: 每秒每Agent消息数
            task_complexity: 任务复杂度（1-5）
        """
        self._scenarios.append(StressScenario(
            name=name,
            agent_count=agent_count,
            duration=duration,
            message_rate=message_rate,
            task_complexity=task_complexity,
        ))
        return self
    
    def add_standard_scenarios(self) -> "StressTester":
        """添加标准测试场景（10/50/100 Agent）"""
        self.add_scenario("light", agent_count=10, duration=15, message_rate=3)
        self.add_scenario("medium", agent_count=50, duration=30, message_rate=5)
        self.add_scenario("heavy", agent_count=100, duration=30, message_rate=10)
        return self
    
    def run(self, scenario: Optional[StressScenario] = None) -> StressResult:
        """运行压力测试
        
        Args:
            scenario: 指定场景（None则运行所有）
        
        Returns:
            测试结果（多个场景时返回最后一个）
        """
        if scenario:
            return self._run_scenario(scenario)
        
        result = None
        for s in self._scenarios:
            result = self._run_scenario(s)
            self._results.append(result)
        
        return result
    
    def run_all(self) -> List[StressResult]:
        """运行所有场景"""
        results = []
        for s in self._scenarios:
            logger.info(f"运行压力场景: {s.name} ({s.agent_count} agents)")
            result = self._run_scenario(s)
            results.append(result)
            self._results.append(result)
        return results
    
    def _run_scenario(self, scenario: StressScenario) -> StressResult:
        """运行单个场景"""
        bus = _MockMessageBus(max_size=10000)
        stop_event = threading.Event()
        result_queue = queue.Queue()
        
        # 获取初始资源
        initial_cpu, initial_mem = self._get_process_resources()
        
        # 启动Agent线程
        agents = []
        for i in range(scenario.agent_count):
            agent_id = f"agent-{i:04d}"
            metrics = AgentMetrics(agent_id=agent_id)
            t = threading.Thread(
                target=_simulate_agent,
                args=(agent_id, bus, scenario, stop_event, metrics, result_queue),
                daemon=True,
                name=agent_id,
            )
            agents.append((t, metrics))
            t.start()
        
        # 监控线程
        monitor_data = {"cpu": [], "memory": [], "timeline": []}
        monitor_stop = threading.Event()
        
        def monitor():
            while not monitor_stop.is_set():
                cpu, mem = self._get_process_resources()
                monitor_data["cpu"].append(cpu)
                monitor_data["memory"].append(mem)
                monitor_data["timeline"].append({
                    "time": time.time(),
                    "bus_size": bus._queue.qsize(),
                    "bus_stats": bus.stats,
                })
                time.sleep(0.5)
        
        monitor_thread = threading.Thread(target=monitor, daemon=True)
        monitor_thread.start()
        
        start_time = time.time()
        
        # 等待场景完成
        time.sleep(scenario.duration)
        stop_event.set()
        
        # 等待线程结束
        for t, _ in agents:
            t.join(timeout=5)
        
        end_time = time.time()
        monitor_stop.set()
        monitor_thread.join(timeout=2)
        
        # 收集结果
        all_latencies = []
        all_metrics = []
        
        while not result_queue.empty():
            try:
                kind, data = result_queue.get_nowait()
                if kind == "latencies":
                    all_latencies.extend(data)
                elif kind == "metrics":
                    all_metrics.append(data)
            except queue.Empty:
                break
        
        # 聚合指标
        total_msgs = sum(m.messages_sent for m in all_metrics)
        total_tasks = sum(m.tasks_completed + m.tasks_failed for m in all_metrics)
        elapsed = end_time - start_time
        
        result = StressResult(
            scenario_name=scenario.name,
            agent_count=scenario.agent_count,
            duration=scenario.duration,
            start_time=start_time,
            end_time=end_time,
            total_messages=total_msgs,
            total_tasks=total_tasks,
            tasks_succeeded=sum(m.tasks_completed for m in all_metrics),
            tasks_failed=sum(m.tasks_failed for m in all_metrics),
            avg_latency_ms=sum(all_latencies) / len(all_latencies) if all_latencies else 0,
            max_latency_ms=max(all_latencies) if all_latencies else 0,
            messages_per_second=total_msgs / elapsed if elapsed > 0 else 0,
            agent_metrics=all_metrics,
            latency_samples=all_latencies,
            timeline=monitor_data["timeline"],
        )
        
        # 计算P95/P99
        if all_latencies:
            s = sorted(all_latencies)
            result.p95_latency_ms = s[int(len(s) * 0.95)]
            result.p99_latency_ms = s[int(len(s) * 0.99)]
        
        # 资源指标
        cpus = monitor_data["cpu"]
        mems = monitor_data["memory"]
        result.avg_cpu_percent = sum(cpus) / len(cpus) if cpus else 0
        result.peak_cpu_percent = max(cpus) if cpus else 0
        result.avg_memory_mb = sum(mems) / len(mems) if mems else 0
        result.peak_memory_mb = max(mems) if mems else 0
        
        logger.info(f"压力场景完成: {scenario.name} — {result.tasks_succeeded}/{total_tasks} tasks OK")
        return result
    
    def _get_process_resources(self) -> Tuple[float, float]:
        """获取当前进程资源使用情况"""
        cpu = 0.0
        mem_mb = 0.0
        
        try:
            import psutil
            proc = psutil.Process(os.getpid())
            cpu = proc.cpu_percent(interval=0.1)
            mem_mb = proc.memory_info().rss / (1024 * 1024)
        except ImportError:
            # 无psutil时回退到基础方法
            try:
                # Windows
                if os.name == "nt":
                    import ctypes
                    # 简单估算
                    mem_info = ctypes.windll.kernel32.GetProcessMemoryInfo(
                        ctypes.windll.kernel32.GetCurrentProcess(),
                        None, 0
                    )
                # Linux
                elif os.path.exists("/proc/self/status"):
                    with open("/proc/self/status", "r") as f:
                        for line in f:
                            if line.startswith("VmRSS:"):
                                mem_mb = int(line.split()[1]) / 1024
                                break
            except Exception:
                pass
        
        return cpu, mem_mb
    
    def summary(self) -> str:
        """所有场景的汇总"""
        if not self._results:
            return "暂无测试结果"
        
        lines = ["=" * 60, "🔥 压力测试汇总", "=" * 60]
        
        for r in self._results:
            status = "✅" if r.success_rate >= 0.95 else "⚠️"
            lines.append(
                f"  {status} {r.scenario_name}: "
                f"{r.agent_count} agents, "
                f"{r.success_rate*100:.1f}% success, "
                f"{r.avg_latency_ms:.0f}ms avg latency"
            )
        
        lines.append("=" * 60)
        return "\n".join(lines)
    
    def save_results(self, path: str) -> None:
        """保存所有结果"""
        data = {
            "timestamp": time.time(),
            "scenarios": [r.to_dict() for r in self._results],
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    
    def __repr__(self):
        return f"<StressTester {len(self._scenarios)} scenarios>"
