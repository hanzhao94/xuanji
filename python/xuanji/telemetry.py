"""
xuanji 遥测数据收集

匿名使用数据收集（可关闭），本地存储，不发送到外部。
用于框架开发者了解使用模式。
零外部依赖，仅使用标准库。
"""

import json
import os
import time
import threading
import uuid
import logging
from typing import Any, Dict, List, Optional
from collections import defaultdict
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


# ─── 事件 ──────────────────────────────────────────────────

class TelemetryEvent:
    """遥测事件"""

    __slots__ = ("event_id", "name", "properties", "timestamp", "session_id")

    def __init__(self, name: str, properties: Optional[Dict] = None,
                 session_id: str = ""):
        self.event_id = uuid.uuid4().hex[:12]
        self.name = name
        self.properties = properties or {}
        self.timestamp = time.time()
        self.session_id = session_id

    @property
    def date_str(self) -> str:
        return datetime.fromtimestamp(self.timestamp).strftime("%Y-%m-%d")

    @property
    def time_str(self) -> str:
        return datetime.fromtimestamp(self.timestamp).strftime("%H:%M:%S")

    def to_dict(self) -> Dict:
        return {
            "event_id": self.event_id,
            "name": self.name,
            "properties": self.properties,
            "timestamp": self.timestamp,
            "date": self.date_str,
            "time": self.time_str,
            "session_id": self.session_id,
        }


# ─── 聚合器 ────────────────────────────────────────────────

class EventAggregator:
    """事件聚合统计"""

    def __init__(self):
        self._event_counts: Dict[str, int] = defaultdict(int)
        self._daily_counts: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self._hourly_counts: Dict[int, int] = defaultdict(int)
        self._property_values: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self._lock = threading.Lock()

    def record(self, event: TelemetryEvent) -> None:
        """记录事件到聚合器"""
        with self._lock:
            self._event_counts[event.name] += 1
            self._daily_counts[event.date_str][event.name] += 1

            hour = datetime.fromtimestamp(event.timestamp).hour
            self._hourly_counts[hour] += 1

            # 聚合属性值
            for key, value in event.properties.items():
                str_val = str(value)
                self._property_values[f"{event.name}.{key}"][str_val] += 1

    def get_summary(self) -> Dict:
        """获取聚合摘要"""
        with self._lock:
            return {
                "event_counts": dict(self._event_counts),
                "total_events": sum(self._event_counts.values()),
                "unique_events": len(self._event_counts),
                "hourly_distribution": dict(sorted(self._hourly_counts.items())),
                "daily_counts": {
                    date: dict(counts)
                    for date, counts in sorted(self._daily_counts.items())
                },
            }

    def get_top_events(self, limit: int = 10) -> List[Dict]:
        """获取最频繁的事件"""
        with self._lock:
            sorted_events = sorted(
                self._event_counts.items(),
                key=lambda x: x[1],
                reverse=True,
            )[:limit]
            return [{"name": name, "count": count} for name, count in sorted_events]

    def get_property_stats(self, event_name: str) -> Dict[str, Dict[str, int]]:
        """获取事件属性统计"""
        with self._lock:
            result = {}
            prefix = f"{event_name}."
            for key, values in self._property_values.items():
                if key.startswith(prefix):
                    prop_name = key[len(prefix):]
                    result[prop_name] = dict(values)
            return result

    def reset(self) -> None:
        """重置统计"""
        with self._lock:
            self._event_counts.clear()
            self._daily_counts.clear()
            self._hourly_counts.clear()
            self._property_values.clear()


# ─── Telemetry 主类 ────────────────────────────────────────

class Telemetry:
    """遥测数据收集器
    
    - 所有数据本地存储，不发送到外部
    - 可随时开关
    - 支持事件追踪和聚合统计
    
    用法::
    
        telemetry = Telemetry(enabled=True)
        
        # 追踪事件
        telemetry.track_event("agent.start", {"agent": "assistant", "model": "gpt-4o"})
        telemetry.track_event("tool.use", {"tool": "web_search", "duration_ms": 1234})
        telemetry.track_event("llm.call", {"model": "claude-3.5", "tokens": 500})
        
        # 查看统计
        summary = telemetry.summary()
        top = telemetry.top_events()
        
        # 导出到文件
        telemetry.save("/path/to/telemetry.json")
        
        # 关闭遥测
        telemetry.disable()
    """

    def __init__(self, enabled: bool = True, max_events: int = 50000,
                 storage_path: Optional[str] = None):
        """
        Args:
            enabled: 是否启用遥测
            max_events: 最大事件存储数
            storage_path: 持久化存储路径（可选）
        """
        self._enabled = enabled
        self._max_events = max_events
        self._storage_path = storage_path
        self._events: List[TelemetryEvent] = []
        self._aggregator = EventAggregator()
        self._session_id = uuid.uuid4().hex[:8]
        self._start_time = time.time()
        self._lock = threading.Lock()

        # 加载已有数据
        if storage_path and os.path.isfile(storage_path):
            self._load(storage_path)

        if enabled:
            logger.info(f"遥测已启用 (session={self._session_id})")
        else:
            logger.info("遥测已禁用")

    @property
    def enabled(self) -> bool:
        return self._enabled

    def enable(self) -> None:
        """启用遥测"""
        self._enabled = True
        logger.info("遥测已启用")

    def disable(self) -> None:
        """禁用遥测"""
        self._enabled = False
        logger.info("遥测已禁用")

    def track_event(self, name: str, properties: Optional[Dict] = None) -> Optional[str]:
        """追踪事件
        
        Args:
            name: 事件名（如 "agent.start", "tool.use", "llm.call"）
            properties: 事件属性
        
        Returns:
            事件ID，或None（禁用时）
        """
        if not self._enabled:
            return None

        event = TelemetryEvent(name, properties, self._session_id)

        with self._lock:
            self._events.append(event)
            if len(self._events) > self._max_events:
                self._events = self._events[-self._max_events:]

        self._aggregator.record(event)

        # 自动持久化
        if self._storage_path and len(self._events) % 100 == 0:
            self._auto_save()

        return event.event_id

    def track_duration(self, name: str, start_time: float,
                       properties: Optional[Dict] = None) -> Optional[str]:
        """追踪带时长的事件
        
        Args:
            name: 事件名
            start_time: 开始时间（time.time()）
            properties: 额外属性
        """
        props = dict(properties or {})
        props["duration_ms"] = round((time.time() - start_time) * 1000, 2)
        return self.track_event(name, props)

    # ── 查询 ──

    def get_events(self, name: Optional[str] = None, limit: int = 100,
                   since: Optional[float] = None) -> List[Dict]:
        """获取事件列表
        
        Args:
            name: 按事件名过滤
            limit: 最多返回数量
            since: 只返回此时间戳之后的事件
        """
        with self._lock:
            events = list(self._events)

        if name:
            events = [e for e in events if e.name == name]
        if since:
            events = [e for e in events if e.timestamp >= since]

        return [e.to_dict() for e in events[-limit:]]

    def summary(self) -> Dict:
        """获取遥测摘要"""
        with self._lock:
            total = len(self._events)
            uptime = time.time() - self._start_time

        agg = self._aggregator.get_summary()
        agg.update({
            "enabled": self._enabled,
            "session_id": self._session_id,
            "total_events_stored": total,
            "uptime_seconds": round(uptime, 1),
            "events_per_minute": round(total / max(uptime / 60, 1), 2),
        })
        return agg

    def top_events(self, limit: int = 10) -> List[Dict]:
        """获取最频繁的事件"""
        return self._aggregator.get_top_events(limit)

    def event_stats(self, event_name: str) -> Dict:
        """获取特定事件的统计"""
        with self._lock:
            events = [e for e in self._events if e.name == event_name]

        if not events:
            return {"event": event_name, "count": 0}

        return {
            "event": event_name,
            "count": len(events),
            "first_seen": events[0].date_str,
            "last_seen": events[-1].date_str,
            "property_stats": self._aggregator.get_property_stats(event_name),
        }

    def daily_report(self, date: Optional[str] = None) -> Dict:
        """日报
        
        Args:
            date: 日期 (YYYY-MM-DD)，默认今天
        """
        if date is None:
            date = datetime.now().strftime("%Y-%m-%d")

        with self._lock:
            events = [e for e in self._events if e.date_str == date]

        event_counts: Dict[str, int] = defaultdict(int)
        for e in events:
            event_counts[e.name] += 1

        # 每小时分布
        hourly: Dict[int, int] = defaultdict(int)
        for e in events:
            hour = datetime.fromtimestamp(e.timestamp).hour
            hourly[hour] += 1

        return {
            "date": date,
            "total_events": len(events),
            "event_breakdown": dict(sorted(event_counts.items(),
                                           key=lambda x: -x[1])),
            "hourly_distribution": dict(sorted(hourly.items())),
            "unique_sessions": len(set(e.session_id for e in events)),
        }

    # ── 持久化 ──

    def save(self, path: Optional[str] = None) -> str:
        """保存遥测数据到文件
        
        Args:
            path: 输出路径，默认使用初始化时的storage_path
        
        Returns:
            保存的文件路径
        """
        path = path or self._storage_path
        if not path:
            raise ValueError("未指定存储路径")

        with self._lock:
            data = {
                "session_id": self._session_id,
                "saved_at": time.time(),
                "events": [e.to_dict() for e in self._events],
                "summary": self._aggregator.get_summary(),
            }

        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)

        logger.info(f"遥测数据已保存: {path} ({len(self._events)} 事件)")
        return path

    def _auto_save(self) -> None:
        """自动保存（静默）"""
        try:
            if self._storage_path:
                self.save(self._storage_path)
        except Exception as e:
            logger.debug(f"自动保存失败: {e}")

    def _load(self, path: str) -> None:
        """从文件加载"""
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)

            events_data = data.get("events", [])
            for ed in events_data:
                event = TelemetryEvent(ed["name"], ed.get("properties", {}),
                                       ed.get("session_id", ""))
                event.timestamp = ed.get("timestamp", time.time())
                event.event_id = ed.get("event_id", uuid.uuid4().hex[:12])
                self._events.append(event)
                self._aggregator.record(event)

            logger.info(f"已加载 {len(events_data)} 条遥测事件")
        except Exception as e:
            logger.warning(f"加载遥测数据失败: {e}")

    def load(self, path: str) -> int:
        """从文件加载遥测数据
        
        Args:
            path: 数据文件路径
        
        Returns:
            加载的事件数
        """
        before = len(self._events)
        self._load(path)
        return len(self._events) - before

    # ── 清理 ──

    def clear(self) -> int:
        """清空所有遥测数据
        
        Returns:
            清空的事件数
        """
        with self._lock:
            count = len(self._events)
            self._events.clear()
        self._aggregator.reset()
        logger.info(f"已清空 {count} 条遥测事件")
        return count

    def purge_before(self, timestamp: float) -> int:
        """清除指定时间之前的事件
        
        Args:
            timestamp: Unix时间戳
        
        Returns:
            清除的事件数
        """
        with self._lock:
            before = len(self._events)
            self._events = [e for e in self._events if e.timestamp >= timestamp]
            removed = before - len(self._events)
        if removed > 0:
            logger.info(f"已清除 {removed} 条过期遥测事件")
        return removed

    def export_json(self, indent: int = 2) -> str:
        """导出为JSON字符串"""
        with self._lock:
            data = {
                "enabled": self._enabled,
                "session_id": self._session_id,
                "events": [e.to_dict() for e in self._events],
                "summary": self._aggregator.get_summary(),
            }
        return json.dumps(data, ensure_ascii=False, indent=indent, default=str)
