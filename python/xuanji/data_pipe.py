"""
xuanji 数据管道模块

Agent之间传递结构化数据的管道系统。
支持类型变换、管道串联、线程安全。
零外部依赖。
"""

import copy
import time
import threading
import queue
from typing import Any, Callable, Dict, List, Optional, Tuple
from dataclasses import dataclass, field, asdict


# ============================================================
# 数据包
# ============================================================

@dataclass
class DataPacket:
    """管道中传输的数据包"""
    name: str
    data: Any
    timestamp: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)
    source: str = ""
    packet_id: int = 0

    def to_dict(self) -> Dict:
        return {
            "name": self.name,
            "data": self.data,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
            "source": self.source,
            "packet_id": self.packet_id,
        }


# ============================================================
# 单条管道
# ============================================================

class Pipe:
    """单条命名管道 — 线程安全的数据通道"""

    def __init__(self, name: str, maxsize: int = 1000):
        self.name = name
        self._queue: queue.Queue = queue.Queue(maxsize=maxsize)
        self._transforms: List[Callable] = []
        self._lock = threading.Lock()
        self._counter = 0
        self._stats = {
            "sent": 0,
            "received": 0,
            "dropped": 0,
            "errors": 0,
        }

    def send(self, data: Any, metadata: Optional[Dict] = None, source: str = "") -> bool:
        """发送数据到管道

        Args:
            data: 任意数据（dict/list/str/bytes等）
            metadata: 附加元数据
            source: 发送方标识

        Returns:
            True=成功, False=管道满
        """
        with self._lock:
            self._counter += 1
            packet_id = self._counter

        packet = DataPacket(
            name=self.name,
            data=data,
            metadata=metadata or {},
            source=source,
            packet_id=packet_id,
        )

        try:
            self._queue.put_nowait(packet)
            with self._lock:
                self._stats["sent"] += 1
            return True
        except queue.Full:
            with self._lock:
                self._stats["dropped"] += 1
            return False

    def receive(
        self,
        timeout: Optional[float] = None,
        apply_transforms: bool = True,
    ) -> Optional[DataPacket]:
        """从管道接收数据

        Args:
            timeout: 等待秒数，None=不等待
            apply_transforms: 是否应用变换链

        Returns:
            DataPacket 或 None（超时/空）
        """
        try:
            if timeout is None:
                packet = self._queue.get_nowait()
            else:
                packet = self._queue.get(timeout=timeout)
        except (queue.Empty, TimeoutError):
            return None

        # 应用变换链
        if apply_transforms and self._transforms:
            try:
                for fn in self._transforms:
                    packet.data = fn(packet.data)
            except Exception as e:
                packet.metadata["transform_error"] = str(e)
                with self._lock:
                    self._stats["errors"] += 1

        with self._lock:
            self._stats["received"] += 1
        return packet

    def add_transform(self, fn: Callable) -> "Pipe":
        """添加变换函数到变换链

        Args:
            fn: 变换函数 data -> data

        Returns:
            self（支持链式调用）
        """
        self._transforms.append(fn)
        return self

    def clear_transforms(self):
        """清除所有变换"""
        self._transforms.clear()

    @property
    def pending(self) -> int:
        """待接收的数据包数量"""
        return self._queue.qsize()

    @property
    def empty(self) -> bool:
        return self._queue.empty()

    @property
    def stats(self) -> Dict[str, int]:
        with self._lock:
            return dict(self._stats)

    def drain(self, max_items: int = -1) -> List[DataPacket]:
        """批量取出所有待接收数据"""
        items = []
        count = 0
        while not self._queue.empty():
            if 0 < max_items <= count:
                break
            try:
                items.append(self._queue.get_nowait())
                count += 1
            except queue.Empty:
                break
        with self._lock:
            self._stats["received"] += len(items)
        return items

    def clear(self):
        """清空管道"""
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break


# ============================================================
# 管道链
# ============================================================

class PipeChain:
    """管道串联：A → transform → B → transform → C

    数据从头部管道进入，经过每一级变换后传到下一级。
    """

    def __init__(self, pipes: List[Pipe]):
        """
        Args:
            pipes: 管道列表，按顺序串联
        """
        if len(pipes) < 2:
            raise ValueError("PipeChain需要至少2条管道")
        self._pipes = pipes
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def start(self, poll_interval: float = 0.1):
        """启动自动转发线程"""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._forward_loop,
            args=(poll_interval,),
            daemon=True,
        )
        self._thread.start()

    def stop(self):
        """停止转发"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    def _forward_loop(self, interval: float):
        """转发循环：从每条管道取出数据，经变换后送入下一条"""
        while self._running:
            forwarded = False
            for i in range(len(self._pipes) - 1):
                src = self._pipes[i]
                dst = self._pipes[i + 1]
                packet = src.receive(timeout=0, apply_transforms=True)
                if packet:
                    dst.send(packet.data, metadata=packet.metadata, source=src.name)
                    forwarded = True
            if not forwarded:
                time.sleep(interval)

    @property
    def pipes(self) -> List[str]:
        return [p.name for p in self._pipes]

    @property
    def head(self) -> Pipe:
        return self._pipes[0]

    @property
    def tail(self) -> Pipe:
        return self._pipes[-1]


# ============================================================
# 数据管道管理器
# ============================================================

class DataPipe:
    """数据管道管理器 — Agent之间传结构化数据

    用法:
        pipe = DataPipe()

        # 简单收发
        pipe.send("task_results", {"score": 95})
        packet = pipe.receive("task_results", timeout=5)

        # 带变换
        pipe.transform("metrics", lambda d: {**d, "normalized": True})
        pipe.send("metrics", {"value": 100})
        packet = pipe.receive("metrics")  # data带normalized=True

        # 管道串联
        chain = pipe.create_chain(["raw", "processed", "output"])
        chain.start()
        pipe.send("raw", {"text": "hello"})
        # 数据自动流经 raw → processed → output
    """

    def __init__(self, default_maxsize: int = 1000):
        self._pipes: Dict[str, Pipe] = {}
        self._chains: List[PipeChain] = []
        self._lock = threading.Lock()
        self._default_maxsize = default_maxsize

    def _get_or_create(self, name: str) -> Pipe:
        """获取管道，不存在则创建"""
        if name not in self._pipes:
            with self._lock:
                if name not in self._pipes:
                    self._pipes[name] = Pipe(name, maxsize=self._default_maxsize)
        return self._pipes[name]

    # ----------------------------------------------------------
    # 核心API
    # ----------------------------------------------------------

    def send(
        self,
        name: str,
        data: Any,
        metadata: Optional[Dict] = None,
        source: str = "",
    ) -> bool:
        """发送数据到命名管道

        Args:
            name: 管道名
            data: dict/list/str/bytes/任意可序列化数据
            metadata: 附加元数据
            source: 发送方标识

        Returns:
            True=成功
        """
        pipe = self._get_or_create(name)
        return pipe.send(data, metadata=metadata, source=source)

    def receive(
        self,
        name: str,
        timeout: Optional[float] = None,
    ) -> Optional[DataPacket]:
        """从管道接收数据

        Args:
            name: 管道名
            timeout: 等待秒数

        Returns:
            DataPacket 或 None
        """
        pipe = self._get_or_create(name)
        return pipe.receive(timeout=timeout)

    def transform(self, name: str, func: Callable) -> "DataPipe":
        """给管道添加数据变换

        Args:
            name: 管道名
            func: 变换函数 data -> data

        Returns:
            self（链式调用）
        """
        pipe = self._get_or_create(name)
        pipe.add_transform(func)
        return self

    def create_chain(self, names: List[str]) -> PipeChain:
        """创建管道链

        Args:
            names: 管道名列表，按顺序串联

        Returns:
            PipeChain对象
        """
        pipes = [self._get_or_create(n) for n in names]
        chain = PipeChain(pipes)
        self._chains.append(chain)
        return chain

    # ----------------------------------------------------------
    # 批量操作
    # ----------------------------------------------------------

    def send_batch(self, name: str, items: List[Any], source: str = "") -> int:
        """批量发送"""
        sent = 0
        for item in items:
            if self.send(name, item, source=source):
                sent += 1
        return sent

    def receive_all(self, name: str, max_items: int = -1) -> List[DataPacket]:
        """批量接收"""
        pipe = self._get_or_create(name)
        return pipe.drain(max_items=max_items)

    # ----------------------------------------------------------
    # 管理
    # ----------------------------------------------------------

    def list_pipes(self) -> List[str]:
        """列出所有管道名"""
        return list(self._pipes.keys())

    def pipe_stats(self, name: str) -> Dict[str, int]:
        """获取管道统计"""
        if name in self._pipes:
            return self._pipes[name].stats
        return {}

    def all_stats(self) -> Dict[str, Dict]:
        """所有管道统计"""
        return {name: pipe.stats for name, pipe in self._pipes.items()}

    def pending(self, name: str) -> int:
        """管道中待接收数量"""
        if name in self._pipes:
            return self._pipes[name].pending
        return 0

    def clear(self, name: str):
        """清空管道"""
        if name in self._pipes:
            self._pipes[name].clear()

    def clear_all(self):
        """清空所有管道"""
        for pipe in self._pipes.values():
            pipe.clear()

    def remove(self, name: str):
        """删除管道"""
        with self._lock:
            self._pipes.pop(name, None)

    def shutdown(self):
        """关闭所有链和管道"""
        for chain in self._chains:
            chain.stop()
        self._chains.clear()
        self.clear_all()


# ============================================================
# 便捷函数
# ============================================================

_default_pipe: Optional[DataPipe] = None


def get_pipe(**kwargs) -> DataPipe:
    """获取/创建默认管道管理器"""
    global _default_pipe
    if _default_pipe is None:
        _default_pipe = DataPipe(**kwargs)
    return _default_pipe


# ============================================================
# 测试
# ============================================================

if __name__ == "__main__":
    dp = DataPipe()

    print("=== 基本收发 ===")
    dp.send("test", {"key": "value"}, source="agent_a")
    packet = dp.receive("test")
    print(f"  received: {packet.data}")

    print("\n=== 变换管道 ===")
    dp.transform("metrics", lambda d: {**d, "doubled": d.get("value", 0) * 2})
    dp.send("metrics", {"value": 42})
    packet = dp.receive("metrics")
    print(f"  transformed: {packet.data}")

    print("\n=== 批量操作 ===")
    dp.send_batch("batch", [1, 2, 3, 4, 5])
    items = dp.receive_all("batch")
    print(f"  batch received: {[p.data for p in items]}")

    print("\n=== 管道链 ===")
    dp.transform("stage_a", lambda d: d.upper() if isinstance(d, str) else d)
    dp.transform("stage_b", lambda d: f"[{d}]" if isinstance(d, str) else d)
    chain = dp.create_chain(["stage_a", "stage_b", "stage_c"])
    chain.start()
    dp.send("stage_a", "hello world")
    time.sleep(0.5)
    result = dp.receive("stage_c")
    if result:
        print(f"  chain result: {result.data}")
    chain.stop()

    print(f"\n=== 统计 ===\n  {dp.all_stats()}")
    dp.shutdown()
