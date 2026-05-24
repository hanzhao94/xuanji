"""
xuanji 可观测性（指标采集 + 链路追踪）

Metrics: 计数器、仪表盘、直方图、计时器
Tracer: 链路追踪、Span管理、调用链分析
零外部依赖，仅使用标准库。
"""

import time
import threading
import uuid
import json
import math
import logging
from contextlib import contextmanager
from typing import Any, Dict, List, Optional, Tuple
from collections import defaultdict

logger = logging.getLogger(__name__)


# ─── 指标类型 ───────────────────────────────────────────────

class _Counter:
    """计数器：只增不减"""
    __slots__ = ("name", "value", "_lock")

    def __init__(self, name: str):
        self.name = name
        self.value = 0.0
        self._lock = threading.Lock()

    def inc(self, amount: float = 1.0) -> None:
        with self._lock:
            self.value += amount

    def get(self) -> float:
        return self.value

    def reset(self) -> None:
        with self._lock:
            self.value = 0.0


class _Gauge:
    """仪表盘：可增可减"""
    __slots__ = ("name", "value", "_lock")

    def __init__(self, name: str):
        self.name = name
        self.value = 0.0
        self._lock = threading.Lock()

    def set(self, value: float) -> None:
        with self._lock:
            self.value = value

    def inc(self, amount: float = 1.0) -> None:
        with self._lock:
            self.value += amount

    def dec(self, amount: float = 1.0) -> None:
        with self._lock:
            self.value -= amount

    def get(self) -> float:
        return self.value


class _Histogram:
    """直方图：记录值的分布"""
    __slots__ = ("name", "_values", "_lock", "_max_samples")

    def __init__(self, name: str, max_samples: int = 10000):
        self.name = name
        self._values: List[float] = []
        self._lock = threading.Lock()
        self._max_samples = max_samples

    def observe(self, value: float) -> None:
        with self._lock:
            self._values.append(value)
            if len(self._values) > self._max_samples:
                self._values = self._values[-self._max_samples:]

    def get_stats(self) -> Dict[str, float]:
        with self._lock:
            if not self._values:
                return {"count": 0, "sum": 0, "avg": 0, "min": 0, "max": 0,
                        "p50": 0, "p90": 0, "p95": 0, "p99": 0}
            sorted_vals = sorted(self._values)
            n = len(sorted_vals)
            total = sum(sorted_vals)
            return {
                "count": n,
                "sum": round(total, 4),
                "avg": round(total / n, 4),
                "min": round(sorted_vals[0], 4),
                "max": round(sorted_vals[-1], 4),
                "p50": round(sorted_vals[int(n * 0.50)], 4),
                "p90": round(sorted_vals[int(n * 0.90)], 4),
                "p95": round(sorted_vals[min(int(n * 0.95), n - 1)], 4),
                "p99": round(sorted_vals[min(int(n * 0.99), n - 1)], 4),
            }

    def reset(self) -> None:
        with self._lock:
            self._values.clear()


# ─── Metrics 主类 ───────────────────────────────────────────

class Metrics:
    """指标采集器
    
    用法::
    
        m = Metrics()
        
        # 计数器
        m.counter("requests_total", 1)
        m.counter("requests_total", 1)
        
        # 仪表盘
        m.gauge("active_agents", 3)
        
        # 直方图
        m.histogram("response_time_ms", 123.4)
        m.histogram("response_time_ms", 89.2)
        
        # 计时器
        with m.timer("process_duration"):
            do_work()
        
        # 导出
        data = m.export()
    """

    def __init__(self):
        self._counters: Dict[str, _Counter] = {}
        self._gauges: Dict[str, _Gauge] = {}
        self._histograms: Dict[str, _Histogram] = {}
        self._lock = threading.Lock()

    def counter(self, name: str, value: float = 1.0) -> None:
        """递增计数器
        
        Args:
            name: 指标名
            value: 增量，默认1
        """
        with self._lock:
            if name not in self._counters:
                self._counters[name] = _Counter(name)
        self._counters[name].inc(value)

    def gauge(self, name: str, value: float) -> None:
        """设置仪表盘值
        
        Args:
            name: 指标名
            value: 当前值
        """
        with self._lock:
            if name not in self._gauges:
                self._gauges[name] = _Gauge(name)
        self._gauges[name].set(value)

    def gauge_inc(self, name: str, amount: float = 1.0) -> None:
        """仪表盘递增"""
        with self._lock:
            if name not in self._gauges:
                self._gauges[name] = _Gauge(name)
        self._gauges[name].inc(amount)

    def gauge_dec(self, name: str, amount: float = 1.0) -> None:
        """仪表盘递减"""
        with self._lock:
            if name not in self._gauges:
                self._gauges[name] = _Gauge(name)
        self._gauges[name].dec(amount)

    def histogram(self, name: str, value: float) -> None:
        """记录直方图观测值
        
        Args:
            name: 指标名
            value: 观测值
        """
        with self._lock:
            if name not in self._histograms:
                self._histograms[name] = _Histogram(name)
        self._histograms[name].observe(value)

    @contextmanager
    def timer(self, name: str):
        """计时器（上下文管理器）
        
        自动记录代码块执行耗时到直方图。
        
        Args:
            name: 指标名
            
        用法::
        
            with metrics.timer("process_time_ms"):
                do_work()
        """
        start = time.perf_counter()
        try:
            yield
        finally:
            elapsed_ms = (time.perf_counter() - start) * 1000
            self.histogram(name, elapsed_ms)

    def export(self) -> Dict[str, Any]:
        """导出所有指标为JSON可序列化字典"""
        result = {
            "counters": {},
            "gauges": {},
            "histograms": {},
            "exported_at": time.time(),
        }
        with self._lock:
            for name, c in self._counters.items():
                result["counters"][name] = c.get()
            for name, g in self._gauges.items():
                result["gauges"][name] = g.get()
            for name, h in self._histograms.items():
                result["histograms"][name] = h.get_stats()
        return result

    def reset(self) -> None:
        """重置所有指标"""
        with self._lock:
            for c in self._counters.values():
                c.reset()
            for g in self._gauges.values():
                g.set(0)
            for h in self._histograms.values():
                h.reset()

    def export_json(self, indent: int = 2) -> str:
        """导出为JSON字符串"""
        return json.dumps(self.export(), ensure_ascii=False, indent=indent, default=str)


# ─── Span ──────────────────────────────────────────────────

class Span:
    """链路追踪中的一个步骤"""

    __slots__ = ("name", "trace_id", "span_id", "parent_id",
                 "start_time", "end_time", "attributes", "status", "children")

    def __init__(self, name: str, trace_id: str, span_id: str,
                 parent_id: Optional[str] = None):
        self.name = name
        self.trace_id = trace_id
        self.span_id = span_id
        self.parent_id = parent_id
        self.start_time = time.time()
        self.end_time: Optional[float] = None
        self.attributes: Dict[str, Any] = {}
        self.status = "running"
        self.children: List["Span"] = []

    @property
    def duration_ms(self) -> float:
        if self.end_time is None:
            return (time.time() - self.start_time) * 1000
        return (self.end_time - self.start_time) * 1000

    def finish(self, status: str = "ok") -> None:
        self.end_time = time.time()
        self.status = status

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value

    def to_dict(self) -> Dict:
        return {
            "name": self.name,
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_id": self.parent_id,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_ms": round(self.duration_ms, 2),
            "status": self.status,
            "attributes": self.attributes,
            "children": [c.to_dict() for c in self.children],
        }


# ─── Tracer 主类 ───────────────────────────────────────────

class Tracer:
    """链路追踪器
    
    用法::
    
        tracer = Tracer()
        
        # 手动管理Span
        trace_id = tracer.new_trace()
        span = tracer.span_start("llm_call", trace_id=trace_id)
        # ... 执行工作 ...
        tracer.span_end(span.span_id)
        
        # 上下文管理器
        with tracer.trace("request_handling") as span:
            with tracer.child_span(span, "parse_input") as child:
                parse()
            with tracer.child_span(span, "llm_call") as child:
                call_llm()
        
        # 查看调用链
        chain = tracer.get_trace(trace_id)
        print(tracer.format_trace(trace_id))
    """

    def __init__(self, max_traces: int = 1000):
        self._traces: Dict[str, List[Span]] = {}
        self._spans: Dict[str, Span] = {}
        self._active_spans: Dict[str, Span] = {}
        self._max_traces = max_traces
        self._lock = threading.Lock()

    @staticmethod
    def _gen_id() -> str:
        return uuid.uuid4().hex[:16]

    def new_trace(self) -> str:
        """生成新的trace_id"""
        trace_id = self._gen_id()
        with self._lock:
            self._traces[trace_id] = []
            # 清理旧trace
            if len(self._traces) > self._max_traces:
                oldest = list(self._traces.keys())[:len(self._traces) - self._max_traces]
                for old_id in oldest:
                    spans = self._traces.pop(old_id, [])
                    for s in spans:
                        self._spans.pop(s.span_id, None)
                        self._active_spans.pop(s.span_id, None)
        return trace_id

    def span_start(self, name: str, trace_id: Optional[str] = None,
                   parent_id: Optional[str] = None) -> Span:
        """开始一个Span
        
        Args:
            name: Span名称
            trace_id: 所属trace，不传则自动创建
            parent_id: 父Span ID
        
        Returns:
            Span对象
        """
        if trace_id is None:
            trace_id = self.new_trace()

        span_id = self._gen_id()
        span = Span(name, trace_id, span_id, parent_id)

        with self._lock:
            self._spans[span_id] = span
            self._active_spans[span_id] = span
            if trace_id not in self._traces:
                self._traces[trace_id] = []
            self._traces[trace_id].append(span)

            # 挂载到父Span
            if parent_id and parent_id in self._spans:
                self._spans[parent_id].children.append(span)

        return span

    def span_end(self, span_id: str, status: str = "ok") -> Optional[float]:
        """结束一个Span
        
        Args:
            span_id: Span ID
            status: 状态 (ok/error)
        
        Returns:
            耗时(ms)，未找到返回None
        """
        with self._lock:
            span = self._spans.get(span_id)
            if span is None:
                return None
            span.finish(status)
            self._active_spans.pop(span_id, None)
            return span.duration_ms

    @contextmanager
    def trace(self, name: str, trace_id: Optional[str] = None):
        """Trace上下文管理器
        
        用法::
        
            with tracer.trace("my_operation") as span:
                do_work()
                span.set_attribute("key", "value")
        """
        span = self.span_start(name, trace_id=trace_id)
        try:
            yield span
        except Exception as e:
            span.set_attribute("error", str(e))
            self.span_end(span.span_id, status="error")
            raise
        else:
            self.span_end(span.span_id, status="ok")

    @contextmanager
    def child_span(self, parent: Span, name: str):
        """子Span上下文管理器
        
        用法::
        
            with tracer.trace("parent") as parent:
                with tracer.child_span(parent, "child") as child:
                    do_child_work()
        """
        span = self.span_start(name, trace_id=parent.trace_id, parent_id=parent.span_id)
        try:
            yield span
        except Exception as e:
            span.set_attribute("error", str(e))
            self.span_end(span.span_id, status="error")
            raise
        else:
            self.span_end(span.span_id, status="ok")

    def get_trace(self, trace_id: str) -> List[Dict]:
        """获取完整调用链
        
        Args:
            trace_id: Trace ID
        
        Returns:
            Span列表（字典形式）
        """
        with self._lock:
            spans = self._traces.get(trace_id, [])
            # 只返回根Span（子Span嵌套在children里）
            root_spans = [s for s in spans if s.parent_id is None]
            return [s.to_dict() for s in root_spans]

    def format_trace(self, trace_id: str) -> str:
        """格式化显示调用链
        
        Args:
            trace_id: Trace ID
        
        Returns:
            可读的调用链文本
        """
        def _fmt(span_dict: Dict, depth: int = 0) -> List[str]:
            indent = "  " * depth
            status_icon = "✓" if span_dict["status"] == "ok" else "✗"
            dur = span_dict["duration_ms"]
            lines = [f"{indent}{status_icon} {span_dict['name']} ({dur:.1f}ms) [{span_dict['status']}]"]
            for attr_k, attr_v in span_dict.get("attributes", {}).items():
                lines.append(f"{indent}  · {attr_k}: {attr_v}")
            for child in span_dict.get("children", []):
                lines.extend(_fmt(child, depth + 1))
            return lines

        trace_data = self.get_trace(trace_id)
        if not trace_data:
            return f"Trace {trace_id} 不存在或为空"

        lines = [f"=== Trace {trace_id} ==="]
        for root in trace_data:
            lines.extend(_fmt(root))
        return "\n".join(lines)

    def get_active_spans(self) -> List[Dict]:
        """获取所有活跃Span"""
        with self._lock:
            return [s.to_dict() for s in self._active_spans.values()]

    def list_traces(self, limit: int = 20) -> List[Dict]:
        """列出最近的Trace
        
        Args:
            limit: 最多返回数量
        
        Returns:
            Trace摘要列表
        """
        with self._lock:
            result = []
            for trace_id, spans in list(self._traces.items())[-limit:]:
                if not spans:
                    continue
                total_ms = sum(s.duration_ms for s in spans if s.end_time)
                result.append({
                    "trace_id": trace_id,
                    "span_count": len(spans),
                    "root_name": spans[0].name if spans else "",
                    "total_duration_ms": round(total_ms, 2),
                    "start_time": spans[0].start_time if spans else 0,
                    "status": "error" if any(s.status == "error" for s in spans) else "ok",
                })
            return result

    def export(self) -> Dict[str, Any]:
        """导出所有追踪数据"""
        with self._lock:
            return {
                "traces": {
                    tid: [s.to_dict() for s in spans]
                    for tid, spans in self._traces.items()
                },
                "active_spans": len(self._active_spans),
                "total_traces": len(self._traces),
                "exported_at": time.time(),
            }

    def export_json(self, indent: int = 2) -> str:
        """导出为JSON字符串"""
        return json.dumps(self.export(), ensure_ascii=False, indent=indent, default=str)
