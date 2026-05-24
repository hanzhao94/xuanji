"""
xuanji 通信渠道基础定义

- Message: 统一消息数据类（跨渠道标准化）
- ChannelBase: 渠道基类（与plugin.py的ChannelPlugin接口对齐）

所有渠道实现继承ChannelBase。
"""

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional


# ============================================================
# 消息类型
# ============================================================

class ContentType(str, Enum):
    """消息内容类型"""
    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
    FILE = "file"
    LOCATION = "location"
    STICKER = "sticker"
    COMMAND = "command"     # 指令消息（如 /start）
    SYSTEM = "system"      # 系统消息


class ChatType(str, Enum):
    """聊天类型"""
    PRIVATE = "private"    # 私聊
    GROUP = "group"        # 群聊
    CHANNEL = "channel"    # 频道


# ============================================================
# 统一消息数据类
# ============================================================

@dataclass
class Message:
    """统一消息结构 — 所有渠道的消息都转换为此格式
    
    字段说明:
        channel: 来源渠道名（如 "webhook", "telegram", "discord"）
        sender: 发送者ID（渠道特定格式）
        sender_name: 发送者显示名
        chat_id: 聊天/会话ID
        chat_type: 聊天类型（私聊/群聊/频道）
        content_type: 内容类型
        content: 文本内容
        media_url: 媒体URL（图片/音频/视频/文件）
        reply_to: 回复的消息ID
        timestamp: 消息时间戳（秒）
        raw: 原始渠道消息（用于渠道特定处理）
    """
    
    channel: str = ""                          # 来源渠道名
    channel_id: str = ""                       # 别名，同channel（兼容不同命名习惯）
    sender: str = ""
    sender_name: str = ""
    chat_id: str = ""
    chat_type: ChatType = ChatType.PRIVATE
    content_type: ContentType = ContentType.TEXT
    content: str = ""
    media_url: str = ""
    reply_to: str = ""
    timestamp: float = 0.0
    raw: Any = None
    
    # 扩展元数据
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def __post_init__(self):
        # 兼容channel和channel_id别名
        if self.channel_id and not self.channel:
            self.channel = self.channel_id
        elif self.channel and not self.channel_id:
            self.channel_id = self.channel
        
        if self.timestamp == 0.0:
            self.timestamp = time.time()
    
    @property
    def is_text(self) -> bool:
        return self.content_type == ContentType.TEXT
    
    @property
    def is_command(self) -> bool:
        return self.content_type == ContentType.COMMAND or (
            self.content and self.content.startswith("/")
        )
    
    @property
    def is_private(self) -> bool:
        return self.chat_type == ChatType.PRIVATE
    
    @property
    def is_group(self) -> bool:
        return self.chat_type == ChatType.GROUP
    
    def to_dict(self) -> Dict[str, Any]:
        """转为字典"""
        return {
            "channel": self.channel,
            "sender": self.sender,
            "sender_name": self.sender_name,
            "chat_id": self.chat_id,
            "chat_type": self.chat_type.value if isinstance(self.chat_type, ChatType) else self.chat_type,
            "content_type": self.content_type.value if isinstance(self.content_type, ContentType) else self.content_type,
            "content": self.content,
            "media_url": self.media_url,
            "reply_to": self.reply_to,
            "timestamp": self.timestamp,
        }
    
    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Message":
        """从字典构造"""
        ct = d.get("chat_type", "private")
        if isinstance(ct, str) and ct in ChatType.__members__:
            ct = ChatType(ct)
        elif isinstance(ct, str):
            ct = ChatType.PRIVATE
        
        cnt = d.get("content_type", "text")
        if isinstance(cnt, str) and cnt in ContentType.__members__:
            cnt = ContentType(cnt)
        elif isinstance(cnt, str):
            cnt = ContentType.TEXT
        
        return cls(
            channel=d.get("channel", ""),
            sender=d.get("sender", ""),
            sender_name=d.get("sender_name", ""),
            chat_id=d.get("chat_id", ""),
            chat_type=ct,
            content_type=cnt,
            content=d.get("content", ""),
            media_url=d.get("media_url", ""),
            reply_to=d.get("reply_to", ""),
            timestamp=d.get("timestamp", 0.0),
            raw=d.get("raw"),
        )
    
    def __repr__(self):
        return (
            f"<Message [{self.channel}] "
            f"{self.sender_name or self.sender}: "
            f"{self.content[:50]}{'...' if len(self.content) > 50 else ''}>"
        )


# ============================================================
# 渠道基类
# ============================================================

class ChannelBase(ABC):
    """通信渠道基类
    
    与plugin.py的ChannelPlugin接口对齐。
    所有渠道实现继承此类。
    """
    
    name: str = ""
    description: str = ""
    
    def __init__(self):
        self._callbacks: Dict[str, List[Callable]] = {}
        self._connected = False
    
    @property
    def connected(self) -> bool:
        """是否已连接"""
        return self._connected
    
    # ============================================================
    # 抽象方法（必须实现）
    # ============================================================
    
    @abstractmethod
    async def connect(self, config: Dict) -> None:
        """连接到平台
        
        Args:
            config: 渠道配置
        """
        ...
    
    @abstractmethod
    async def listen(self) -> None:
        """开始监听消息（长连接/轮询/Webhook）"""
        ...
    
    @abstractmethod
    async def send_text(self, target: str, text: str) -> None:
        """发送文本消息
        
        Args:
            target: 目标（chat_id/user_id等，渠道特定）
            text: 文本内容
        """
        ...
    
    # ============================================================
    # 可选方法（默认抛NotImplementedError）
    # ============================================================
    
    async def send_image(self, target: str, image: Any) -> None:
        """发送图片"""
        raise NotImplementedError(f"{self.name} 不支持发送图片")
    
    async def send_file(self, target: str, path: str) -> None:
        """发送文件"""
        raise NotImplementedError(f"{self.name} 不支持发送文件")
    
    async def send_voice(self, target: str, audio: Any) -> None:
        """发送语音"""
        raise NotImplementedError(f"{self.name} 不支持发送语音")
    
    # ============================================================
    # 事件系统
    # ============================================================
    
    async def emit(self, event: str, data: Any) -> None:
        """触发事件
        
        Args:
            event: 事件名（如 "message", "connected", "error"）
            data: 事件数据
        """
        for cb in self._callbacks.get(event, []):
            try:
                result = cb(data)
                # 支持同步和异步回调
                if hasattr(result, "__await__"):
                    await result
            except Exception as e:
                import logging
                logging.getLogger("xuanji.channels").error(
                    f"事件回调异常 [{event}]: {e}"
                )
    
    def on(self, event: str, callback: Callable) -> None:
        """注册事件回调
        
        Args:
            event: 事件名
            callback: 回调函数（同步或异步）
        """
        self._callbacks.setdefault(event, []).append(callback)
    
    # ============================================================
    # 生命周期
    # ============================================================
    
    async def disconnect(self) -> None:
        """断开连接"""
        self._connected = False
    
    def __repr__(self):
        status = "connected" if self._connected else "disconnected"
        return f"<{self.__class__.__name__} name={self.name} {status}>"
