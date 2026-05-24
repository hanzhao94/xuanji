"""
xuanji 事件系统（发布/订阅）

事件驱动架构的核心组件。支持通配符匹配、异步回调、事件历史。

示例:
    bus = EventBus()

    @bus.on("task.completed")
    def on_done(data):
        print(f"任务完成: {data}")

    bus.on("task.*", lambda d: print(f"任务事件: {d}"))
    bus.once("system.ready", lambda d: print("系统就绪!"))

    bus.emit("task.completed", {"id": 1, "result": "ok"})
    bus.emit("system.ready", {})
"""

import asyncio
import fnmatch
import inspect
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Deque, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────────

@dataclass
class EventRecord:
    """事件历史记录

    Attributes:
        event_name: 事件名
        data: 事件数据
        timestamp: 触发时间
        listener_count: 触发的监听器数量
        errors: 监听器执行错误
    """
    event_name: str = ""
    data: Any = None
    timestamp: float = field(default_factory=time.time)
    listener_count: int = 0
    errors: List[str] = field(default_factory=list)


@dataclass
class Listener:
    """事件监听器

    Attributes:
        callback: 回调函数
        event_pattern: 事件名模式（支持通配符）
        once: 是否只触发一次
        priority: 优先级（越大越先执行）
        is_async: 是否为异步回调
        call_count: 已触发次数
        created_at: 注册时间
    """
    callback: Callable
    event_pattern: str = ""
    once: bool = False
    priority: int = 0
    is_async: bool = False
    call_count: int = 0
    created_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        self.is_async = inspect.iscoroutinefunction(self.callback)


# ─────────────────────────────────────────────
# 事件总线
# ─────────────────────────────────────────────

class EventBus:
    """事件总线 — 发布/订阅模式

    支持精确匹配和通配符匹配:
    - "task.completed" — 精确匹配
    - "task.*" — 匹配 task 下的所有事件
    - "*.error" — 匹配所有 error 事件
    - "*" — 匹配所有事件

    Args:
        max_history: 事件历史最大条数
        max_listeners: 每个事件的最大监听器数
    """

    def __init__(
        self,
        max_history: int = 100,
        max_listeners: int = 100,
    ) -> None:
        self._listeners: List[Listener] = []
        self._lock = threading.Lock()
        self._history: Deque[EventRecord] = deque(maxlen=max_history)
        self._max_listeners = max_listeners
        self._paused = False
        self._stats: Dict[str, int] = {}  # event_name → emit count

    # ── 订阅 ──

    def on(
        self,
        event: str,
        callback: Optional[Callable] = None,
        priority: int = 0,
    ) -> Callable:
        """订阅事件

        可作为装饰器或直接调用。

        Args:
            event: 事件名（支持通配符 *）
            callback: 回调函数 (data) -> None
            priority: 优先级（越大越先执行）

        Returns:
            回调函数（装饰器模式）
        """
        def decorator(fn: Callable) -> Callable:
            listener = Listener(
                callback=fn,
                event_pattern=event,
                once=False,
                priority=priority,
            )
            with self._lock:
                self._listeners.append(listener)
                self._listeners.sort(key=lambda l: l.priority, reverse=True)
            return fn

        if callback is not None:
            decorator(callback)
            return callback
        return decorator

    def once(
        self,
        event: str,
        callback: Optional[Callable] = None,
        priority: int = 0,
    ) -> Callable:
        """订阅事件（只触发一次）

        Args:
            event: 事件名
            callback: 回调函数
            priority: 优先级

        Returns:
            回调函数
        """
        def decorator(fn: Callable) -> Callable:
            listener = Listener(
                callback=fn,
                event_pattern=event,
                once=True,
                priority=priority,
            )
            with self._lock:
                self._listeners.append(listener)
                self._listeners.sort(key=lambda l: l.priority, reverse=True)
            return fn

        if callback is not None:
            decorator(callback)
            return callback
        return decorator

    def off(self, event: str, callback: Optional[Callable] = None) -> int:
        """取消订阅

        Args:
            event: 事件名
            callback: 特定回调（None 则移除该事件所有监听器）

        Returns:
            移除的监听器数量
        """
        with self._lock:
            before = len(self._listeners)
            if callback is not None:
                self._listeners = [
                    l for l in self._listeners
                    if not (l.event_pattern == event and l.callback is callback)
                ]
            else:
                self._listeners = [
                    l for l in self._listeners
                    if l.event_pattern != event
                ]
            removed = before - len(self._listeners)

        if removed:
            logger.debug("移除 %d 个监听器: %s", removed, event)
        return removed

    def off_all(self) -> int:
        """移除所有监听器

        Returns:
            移除的数量
        """
        with self._lock:
            count = len(self._listeners)
            self._listeners.clear()
        return count

    # ── 发布 ──

    def emit(self, event: str, data: Any = None) -> EventRecord:
        """发布事件（同步）

        触发所有匹配的监听器。异步回调会在新事件循环中运行。

        Args:
            event: 事件名
            data: 事件数据

        Returns:
            事件记录
        """
        if self._paused:
            record = EventRecord(event_name=event, data=data, listener_count=0)
            self._history.append(record)
            return record

        record = EventRecord(event_name=event, data=data)

        # 更新统计
        self._stats[event] = self._stats.get(event, 0) + 1

        # 找匹配的监听器
        with self._lock:
            matched = [l for l in self._listeners if self._matches(l.event_pattern, event)]
            # 标记 once 监听器待移除
            to_remove: List[Listener] = [l for l in matched if l.once]

        # 执行回调
        for listener in matched:
            try:
                if listener.is_async:
                    self._run_async(listener.callback, data)
                else:
                    listener.callback(data)
                listener.call_count += 1
            except Exception as e:
                error_msg = f"{listener.event_pattern} → {e}"
                record.errors.append(error_msg)
                logger.error("事件回调异常: %s", error_msg)

        record.listener_count = len(matched)

        # 移除 once 监听器
        if to_remove:
            with self._lock:
                for l in to_remove:
                    if l in self._listeners:
                        self._listeners.remove(l)

        # 记录历史
        self._history.append(record)

        return record

    async def emit_async(self, event: str, data: Any = None) -> EventRecord:
        """异步发布事件

        所有异步回调并发执行。

        Args:
            event: 事件名
            data: 事件数据

        Returns:
            事件记录
        """
        if self._paused:
            record = EventRecord(event_name=event, data=data, listener_count=0)
            self._history.append(record)
            return record

        record = EventRecord(event_name=event, data=data)
        self._stats[event] = self._stats.get(event, 0) + 1

        with self._lock:
            matched = [l for l in self._listeners if self._matches(l.event_pattern, event)]
            to_remove = [l for l in matched if l.once]

        tasks = []
        sync_listeners = []

        for listener in matched:
            if listener.is_async:
                tasks.append((listener, listener.callback(data)))
            else:
                sync_listeners.append(listener)

        # 执行同步回调
        for listener in sync_listeners:
            try:
                listener.callback(data)
                listener.call_count += 1
            except Exception as e:
                record.errors.append(f"{listener.event_pattern} → {e}")

        # 执行异步回调
        for listener, coro in tasks:
            try:
                await coro
                listener.call_count += 1
            except Exception as e:
                record.errors.append(f"{listener.event_pattern} → {e}")

        record.listener_count = len(matched)

        if to_remove:
            with self._lock:
                for l in to_remove:
                    if l in self._listeners:
                        self._listeners.remove(l)

        self._history.append(record)
        return record

    # ── 匹配逻辑 ──

    @staticmethod
    def _matches(pattern: str, event: str) -> bool:
        """检查事件名是否匹配模式

        支持:
        - 精确匹配: "task.done" == "task.done"
        - 通配符: "task.*" 匹配 "task.done", "task.failed"
        - 全局: "*" 匹配所有
        """
        if pattern == event:
            return True
        if "*" in pattern or "?" in pattern:
            return fnmatch.fnmatch(event, pattern)
        return False

    # ── 异步辅助 ──

    @staticmethod
    def _run_async(callback: Callable, data: Any) -> None:
        """在新事件循环中运行异步回调"""
        try:
            loop = asyncio.get_running_loop()
            if loop.is_running():
                asyncio.ensure_future(callback(data))
            else:
                loop.run_until_complete(callback(data))
        except RuntimeError:
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(callback(data))
            finally:
                loop.close()

    # ── 控制 ──

    def pause(self) -> None:
        """暂停事件发布（emit 不再触发回调）"""
        self._paused = True

    def resume(self) -> None:
        """恢复事件发布"""
        self._paused = False

    @property
    def is_paused(self) -> bool:
        return self._paused

    # ── 查询 ──

    def listeners(self, event: Optional[str] = None) -> List[Dict[str, Any]]:
        """列出监听器

        Args:
            event: 过滤事件名（None 返回所有）

        Returns:
            监听器信息列表
        """
        with self._lock:
            result = []
            for l in self._listeners:
                if event and not self._matches(l.event_pattern, event):
                    continue
                result.append({
                    "pattern": l.event_pattern,
                    "once": l.once,
                    "priority": l.priority,
                    "is_async": l.is_async,
                    "call_count": l.call_count,
                    "callback": l.callback.__name__ if hasattr(l.callback, "__name__") else str(l.callback),
                })
            return result

    def listener_count(self, event: Optional[str] = None) -> int:
        """监听器数量"""
        if event:
            return len(self.listeners(event))
        return len(self._listeners)

    def history(
        self,
        event: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """查看事件历史

        Args:
            event: 过滤事件名
            limit: 返回条数

        Returns:
            事件记录列表
        """
        records = list(self._history)
        if event:
            records = [r for r in records if r.event_name == event]
        records = records[-limit:]

        return [
            {
                "event": r.event_name,
                "data": r.data,
                "timestamp": r.timestamp,
                "listeners": r.listener_count,
                "errors": r.errors,
            }
            for r in records
        ]

    def stats(self) -> Dict[str, Any]:
        """事件统计

        Returns:
            统计信息
        """
        return {
            "total_listeners": len(self._listeners),
            "total_events_emitted": sum(self._stats.values()),
            "event_counts": dict(self._stats),
            "history_size": len(self._history),
            "paused": self._paused,
        }

    def clear_history(self) -> None:
        """清空事件历史"""
        self._history.clear()
        self._stats.clear()

    # ── 等待事件 ──

    def wait_for(
        self,
        event: str,
        timeout: float = 30.0,
    ) -> Optional[Any]:
        """同步等待事件

        Args:
            event: 等待的事件名
            timeout: 超时秒数

        Returns:
            事件数据，超时返回 None
        """
        result: List[Any] = []
        done = threading.Event()

        def handler(data: Any) -> None:
            result.append(data)
            done.set()

        self.once(event, handler)

        if done.wait(timeout):
            return result[0] if result else None
        else:
            # 超时 → 移除监听器
            self.off(event, handler)
            return None

    async def wait_for_async(
        self,
        event: str,
        timeout: float = 30.0,
    ) -> Optional[Any]:
        """异步等待事件

        Args:
            event: 等待的事件名
            timeout: 超时秒数

        Returns:
            事件数据
        """
        future: asyncio.Future = asyncio.get_running_loop().create_future()

        def handler(data: Any) -> None:
            if not future.done():
                future.get_loop().call_soon_threadsafe(future.set_result, data)

        self.once(event, handler)

        try:
            return await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError:
            self.off(event, handler)
            return None
