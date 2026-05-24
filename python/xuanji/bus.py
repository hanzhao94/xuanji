"""
xuanji 消息总线 Python封装

双模实现：
- 有C底座 → 调用C层无锁环形缓冲区（高性能）
- 无C底座 → 用multiprocessing.Queue实现纯Python版（兼容）

统一接口，调用方无需关心底层实现。

用法:
    from xuanji.bus import MessageBus, Message
    
    bus = MessageBus(capacity=1024)
    
    # 发布
    bus.publish(Message(
        from_agent=1,
        to_agent=2,
        msg_type=MsgType.CHAT,
        payload=b"hello"
    ))
    
    # 订阅+接收
    msg = bus.receive(agent_id=2, timeout_ms=1000)
    
    # 异步接口
    msg = await bus.async_receive(agent_id=2, timeout_ms=1000)
"""

import asyncio
import enum
import json
import struct
import threading
import time
from dataclasses import dataclass, field
from multiprocessing import Queue as MPQueue
from queue import Empty, Full
from typing import Any, Callable, Dict, List, Optional, Set


# ============================================================
# 消息类型
# ============================================================

class MsgType(enum.IntEnum):
    """消息类型枚举"""
    
    # 系统消息 (0-99)
    HEARTBEAT = 0       # 心跳
    SYSTEM = 1          # 系统通知
    SHUTDOWN = 2        # 关闭信号
    ERROR = 3           # 错误报告
    
    # Agent通信 (100-199)
    CHAT = 100          # 聊天消息
    TASK = 101          # 任务分配
    TASK_RESULT = 102   # 任务结果
    TASK_CANCEL = 103   # 任务取消
    
    # 资源管理 (200-299)
    RES_REQUEST = 200   # 资源请求
    RES_GRANT = 201     # 资源授权
    RES_RELEASE = 202   # 资源释放
    RES_REVOKE = 203    # 资源回收
    
    # 感知 (300-399)
    PERCEPTION = 300    # 感知数据
    SCREEN = 301        # 屏幕截图
    AUDIO = 302         # 音频数据
    
    # 动作 (400-499)
    ACTION = 400        # 动作指令
    MOUSE = 401         # 鼠标操作
    KEYBOARD = 402      # 键盘操作
    
    # 自定义 (1000+)
    CUSTOM = 1000       # 用户自定义起始


class Priority(enum.IntEnum):
    """消息优先级"""
    CRITICAL = 0    # 最高
    HIGH = 1
    NORMAL = 5
    LOW = 9


# ============================================================
# 消息数据类
# ============================================================

@dataclass
class Message:
    """统一消息结构
    
    与C层oa_msg_t字段对齐，Python侧使用dataclass。
    payload支持bytes和dict（自动序列化）。
    """
    
    from_agent: int = 0
    to_agent: int = 0           # 0 = 广播
    msg_type: int = MsgType.CHAT
    priority: int = Priority.NORMAL
    timestamp: int = 0          # 微秒时间戳，0=自动填充
    trace_id: int = 0
    payload: bytes = b""
    
    # Python侧扩展字段（不传给C层）
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def __post_init__(self):
        if self.timestamp == 0:
            self.timestamp = int(time.time() * 1_000_000)
        # dict payload自动JSON序列化
        if isinstance(self.payload, dict):
            self.payload = json.dumps(
                self.payload, ensure_ascii=False
            ).encode("utf-8")
        elif isinstance(self.payload, str):
            self.payload = self.payload.encode("utf-8")
    
    def payload_as_str(self) -> str:
        """payload解码为字符串"""
        return self.payload.decode("utf-8", errors="replace")
    
    def payload_as_json(self) -> Any:
        """payload解码为JSON对象"""
        return json.loads(self.payload)
    
    def to_dict(self) -> Dict:
        """转为dict（用于C层FFI）"""
        return {
            "from_agent": self.from_agent,
            "to_agent": self.to_agent,
            "msg_type": self.msg_type,
            "priority": self.priority,
            "timestamp": self.timestamp,
            "trace_id": self.trace_id,
            "payload": self.payload,
        }
    
    @classmethod
    def from_dict(cls, d: Dict) -> "Message":
        """从dict构造"""
        return cls(
            from_agent=d.get("from_agent", 0),
            to_agent=d.get("to_agent", 0),
            msg_type=d.get("msg_type", MsgType.CHAT),
            priority=d.get("priority", Priority.NORMAL),
            timestamp=d.get("timestamp", 0),
            trace_id=d.get("trace_id", 0),
            payload=d.get("payload", b""),
        )


# ============================================================
# 订阅过滤器
# ============================================================

@dataclass
class Subscription:
    """订阅描述 — 指定过滤条件"""
    
    agent_id: int                             # 订阅者Agent ID
    msg_types: Optional[Set[int]] = None      # None=所有类型
    from_agents: Optional[Set[int]] = None    # None=所有来源
    callback: Optional[Callable] = None       # 回调函数（推模式）
    
    def matches(self, msg: Message) -> bool:
        """检查消息是否匹配此订阅"""
        # 目标匹配（广播或定向）
        if msg.to_agent != 0 and msg.to_agent != self.agent_id:
            return False
        # 不接收自己发的
        if msg.from_agent == self.agent_id:
            return False
        # 类型过滤
        if self.msg_types and msg.msg_type not in self.msg_types:
            return False
        # 来源过滤
        if self.from_agents and msg.from_agent not in self.from_agents:
            return False
        return True


# ============================================================
# 消息总线
# ============================================================

class MessageBus:
    """消息总线 — 自动选择C底座或纯Python实现
    
    接口：
    - publish(msg)       发布消息
    - subscribe(sub)     注册订阅
    - unsubscribe(id)    取消订阅
    - receive(id, ms)    拉模式接收
    - pending(id)        查询待处理数
    - async_receive()    异步接收
    - close()            关闭总线
    """
    
    def __init__(self, capacity: int = 1024, use_native: Optional[bool] = None):
        """
        Args:
            capacity: 每个Agent的队列容量
            use_native: 强制指定后端。None=自动检测
        """
        self.capacity = capacity
        self._closed = False
        self._subscriptions: Dict[int, Subscription] = {}  # agent_id → sub
        self._lock = threading.Lock()
        
        # 选择后端
        if use_native is True:
            self._init_native(capacity)
        elif use_native is False:
            self._init_fallback(capacity)
        else:
            self._init_auto(capacity)
    
    def _init_auto(self, capacity: int):
        """自动选择后端"""
        try:
            from xuanji._ffi import ffi
            if ffi.is_native:
                self._init_native(capacity)
            else:
                self._init_fallback(capacity)
        except ImportError:
            self._init_fallback(capacity)
    
    def _init_native(self, capacity: int):
        """使用C底座"""
        from xuanji._ffi import ffi
        self._backend = "native"
        self._ffi = ffi
        self._bus_handle = ffi.bus_create(capacity)
    
    def _init_fallback(self, capacity: int):
        """纯Python实现"""
        self._backend = "fallback"
        self._ffi = None
        self._bus_handle = None
        
        # agent_id → [Message, ...]
        self._queues: Dict[int, list] = {}
        self._cond = threading.Condition(self._lock)
    
    @property
    def backend(self) -> str:
        """当前后端: 'native' 或 'fallback'"""
        return self._backend
    
    # ============================================================
    # 发布
    # ============================================================
    
    def publish(self, msg: Message) -> bool:
        """发布消息
        
        Args:
            msg: 消息对象
        
        Returns:
            是否成功
        """
        if self._closed:
            return False
        
        if self._backend == "native":
            from xuanji._ffi import OA_OK
            code = self._ffi.bus_publish(self._bus_handle, msg.to_dict())
            return code == OA_OK
        
        # fallback
        return self._fallback_publish(msg)
    
    def _fallback_publish(self, msg: Message) -> bool:
        """纯Python发布"""
        with self._cond:
            if msg.to_agent == 0:
                # 广播
                for agent_id, sub in self._subscriptions.items():
                    if sub.matches(msg):
                        q = self._queues.setdefault(agent_id, [])
                        if len(q) >= self.capacity:
                            continue  # 满了跳过，不阻塞
                        q.append(msg)
                        # 推模式回调
                        if sub.callback:
                            try:
                                sub.callback(msg)
                            except Exception:
                                pass
            else:
                # 定向
                q = self._queues.setdefault(msg.to_agent, [])
                if len(q) >= self.capacity:
                    return False
                q.append(msg)
                # 推模式回调
                sub = self._subscriptions.get(msg.to_agent)
                if sub and sub.callback:
                    try:
                        sub.callback(msg)
                    except Exception:
                        pass
            
            self._cond.notify_all()
        return True
    
    # ============================================================
    # 订阅
    # ============================================================
    
    def subscribe(self, sub: Subscription) -> None:
        """注册订阅
        
        Args:
            sub: 订阅描述
        """
        with self._lock:
            self._subscriptions[sub.agent_id] = sub
            if self._backend == "fallback":
                self._queues.setdefault(sub.agent_id, [])
    
    def unsubscribe(self, agent_id: int) -> None:
        """取消订阅"""
        with self._lock:
            self._subscriptions.pop(agent_id, None)
            if self._backend == "fallback":
                self._queues.pop(agent_id, None)
    
    # ============================================================
    # 接收
    # ============================================================
    
    def receive(self, agent_id: int, timeout_ms: int = 0) -> Optional[Message]:
        """接收消息（拉模式）
        
        Args:
            agent_id: 接收方Agent ID
            timeout_ms: 超时毫秒（0=非阻塞）
        
        Returns:
            消息对象，或None（超时/无消息）
        """
        if self._closed:
            return None
        
        if self._backend == "native":
            result = self._ffi.bus_receive(
                self._bus_handle, agent_id, timeout_ms
            )
            if result is None:
                return None
            return Message.from_dict(result)
        
        return self._fallback_receive(agent_id, timeout_ms)
    
    def _fallback_receive(self, agent_id: int, timeout_ms: int) -> Optional[Message]:
        """纯Python接收"""
        deadline = (time.monotonic() + timeout_ms / 1000.0) if timeout_ms > 0 else None
        
        with self._cond:
            while True:
                q = self._queues.get(agent_id, [])
                
                # 过滤匹配的消息
                sub = self._subscriptions.get(agent_id)
                for i, msg in enumerate(q):
                    if sub is None or sub.matches(msg):
                        q.pop(i)
                        return msg
                
                # 非阻塞模式
                if deadline is None:
                    return None
                
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                
                self._cond.wait(timeout=remaining)
    
    async def async_receive(self, agent_id: int,
                            timeout_ms: int = 1000) -> Optional[Message]:
        """异步接收（在事件循环中使用）
        
        不阻塞事件循环，通过executor在线程中等待。
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, self.receive, agent_id, timeout_ms
        )
    
    # ============================================================
    # 查询
    # ============================================================
    
    def pending(self, agent_id: int) -> int:
        """查询待处理消息数"""
        if self._backend == "native":
            return self._ffi.bus_pending(self._bus_handle, agent_id)
        
        with self._lock:
            return len(self._queues.get(agent_id, []))
    
    # ============================================================
    # 便捷方法
    # ============================================================
    
    def send_chat(self, from_id: int, to_id: int, text: str,
                  priority: int = Priority.NORMAL) -> bool:
        """发送聊天消息（便捷方法）"""
        return self.publish(Message(
            from_agent=from_id,
            to_agent=to_id,
            msg_type=MsgType.CHAT,
            priority=priority,
            payload=text.encode("utf-8"),
        ))
    
    def send_task(self, from_id: int, to_id: int, task_data: dict,
                  priority: int = Priority.NORMAL) -> bool:
        """发送任务（便捷方法）"""
        return self.publish(Message(
            from_agent=from_id,
            to_agent=to_id,
            msg_type=MsgType.TASK,
            priority=priority,
            payload=json.dumps(task_data, ensure_ascii=False).encode("utf-8"),
        ))
    
    def broadcast(self, from_id: int, msg_type: int, payload: bytes = b"",
                  priority: int = Priority.NORMAL) -> bool:
        """广播消息（便捷方法）"""
        return self.publish(Message(
            from_agent=from_id,
            to_agent=0,
            msg_type=msg_type,
            priority=priority,
            payload=payload,
        ))
    
    def send_shutdown(self, from_id: int = 0) -> bool:
        """广播关闭信号"""
        return self.broadcast(
            from_id, MsgType.SHUTDOWN, b"shutdown",
            priority=Priority.CRITICAL
        )
    
    # ============================================================
    # 生命周期
    # ============================================================
    
    def close(self):
        """关闭总线"""
        if self._closed:
            return
        self._closed = True
        
        if self._backend == "native" and self._bus_handle:
            self._ffi.bus_destroy(self._bus_handle)
            self._bus_handle = None
        
        if self._backend == "fallback":
            with self._lock:
                self._queues.clear()
                self._subscriptions.clear()
    
    def __del__(self):
        self.close()
    
    def __enter__(self):
        return self
    
    def __exit__(self, *args):
        self.close()
    
    def __repr__(self):
        subs = len(self._subscriptions)
        return (
            f"<MessageBus backend={self._backend} "
            f"capacity={self.capacity} subscribers={subs}>"
        )
