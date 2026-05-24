"""
xuanji 智能多渠道路由器 v2

统一管理多个通信渠道，提供智能路由能力：
- register(name, channel) — 注册渠道
- on_message(callback) — 统一消息回调
- send(channel, target, text) — 发送到指定渠道
- reply(msg, text) — 回复到来源渠道
- broadcast(targets, text) — 群发
- smart_route(target, text) — 智能路由发送
- fallback_send(targets, text) — 带fallback的发送

智能路由策略：
- 国内渠道优先（微信/QQ/钉钉/飞书等）
- 国外渠道自动切换（Telegram/Discord/Slack等）
- 根据目标ID特征自动选择最佳渠道
- 支持优先级和fallback

所有渠道的消息统一转换为Message格式，通过回调分发。
"""

import asyncio
import logging
import re
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from xuanji.channels._base import ChannelBase, Message

logger = logging.getLogger("xuanji.channels.router")


# ============================================================
# 渠道分类
# ============================================================

# 国内渠道（按优先级排序）
DOMESTIC_CHANNELS = [
    "wechat", "wecom", "qq", "dingtalk", "feishu",
    "weibo", "douyin", "bilibili", "xiaohongshu", "sms",
]

# 国外渠道（按优先级排序）
INTERNATIONAL_CHANNELS = [
    "telegram", "discord", "whatsapp", "slack", "signal",
    "imessage", "twitter", "instagram", "facebook", "line",
    "matrix", "mattermost", "teams", "email",
]

# 通用协议
PROTOCOL_CHANNELS = [
    "irc", "xmpp", "websocket", "webhook",
]

# 目标ID特征模式
TARGET_PATTERNS = {
    # 手机号（中国大陆）
    "cn_phone": re.compile(r"^(\+?86)?1[3-9]\d{9}$"),
    # 微信open_id
    "wechat_id": re.compile(r"^o[A-Za-z0-9_-]{20,}$"),
    # QQ号
    "qq_id": re.compile(r"^\d{5,12}$"),
    # Telegram user_id
    "telegram_id": re.compile(r"^[-+]?\d{8,}$"),
    # Discord channel/user_id
    "discord_id": re.compile(r"^\d{17,19}$"),
    # WhatsApp（国际手机号）
    "whatsapp_id": re.compile(r"^\+?\d{10,15}$"),
    # Matrix user_id
    "matrix_id": re.compile(r"^@[a-z0-9._=-]+:[a-z0-9.-]+$"),
    # Email
    "email": re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"),
    # IRC channel
    "irc_channel": re.compile(r"^[#&][^\s]+$"),
    # XMPP JID
    "xmpp_jid": re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+$"),
}


class SmartRouter:
    """智能路由引擎
    
    根据目标特征、渠道状态、优先级自动选择最佳渠道。
    """
    
    def __init__(self):
        self._channel_categories: Dict[str, str] = {}  # name → category
        self._channel_priorities: Dict[str, int] = {}  # name → priority (lower = higher)
        self._fallback_chains: Dict[str, List[str]] = {}  # category → fallback chain
    
    def register_channel(self, name: str, category: str, priority: int = 0) -> None:
        """注册渠道分类"""
        self._channel_categories[name] = category
        self._channel_priorities[name] = priority
    
    def get_best_channel(self, target: str, available: List[str]) -> Optional[str]:
        """根据目标特征选择最佳渠道
        
        Args:
            target: 目标ID
            available: 可用渠道列表
        
        Returns:
            最佳渠道名称，或None
        """
        # 1. 根据目标特征匹配渠道
        matched = self._match_target(target)
        if matched:
            # 检查匹配的渠道是否可用
            for ch in matched:
                if ch in available:
                    return ch
        
        # 2. 按优先级排序可用渠道
        sorted_channels = sorted(
            available,
            key=lambda ch: self._channel_priorities.get(ch, 999)
        )
        
        return sorted_channels[0] if sorted_channels else None
    
    def _match_target(self, target: str) -> List[str]:
        """根据目标ID特征匹配渠道"""
        results = []
        
        for pattern_name, pattern in TARGET_PATTERNS.items():
            if pattern.match(target):
                # 根据模式名称映射到渠道
                mapping = {
                    "cn_phone": ["sms", "wechat", "wecom", "dingtalk"],
                    "wechat_id": ["wechat", "wecom"],
                    "qq_id": ["qq"],
                    "telegram_id": ["telegram"],
                    "discord_id": ["discord"],
                    "whatsapp_id": ["whatsapp", "sms"],
                    "matrix_id": ["matrix"],
                    "email": ["email"],
                    "irc_channel": ["irc"],
                    "xmpp_jid": ["xmpp"],
                }
                results.extend(mapping.get(pattern_name, []))
        
        return results
    
    def get_fallback_chain(self, category: str) -> List[str]:
        """获取渠道fallback链"""
        if category in self._fallback_chains:
            return self._fallback_chains[category]
        
        # 默认fallback
        if category == "domestic":
            return DOMESTIC_CHANNELS
        elif category == "international":
            return INTERNATIONAL_CHANNELS
        else:
            return PROTOCOL_CHANNELS
    
    def categorize_target(self, target: str) -> str:
        """判断目标是国内还是国外"""
        # 中国大陆手机号 → 国内
        if TARGET_PATTERNS["cn_phone"].match(target):
            return "domestic"
        
        # 有+86前缀 → 国内
        if target.startswith("+86") or target.startswith("86"):
            return "domestic"
        
        # 邮箱 → 检查域名
        if TARGET_PATTERNS["email"].match(target):
            domain = target.split("@")[-1]
            cn_domains = ["qq.com", "163.com", "126.com", "sina.com", "aliyun.com",
                          "yeah.net", "sohu.com", "139.com", "wo.cn", "189.cn"]
            if any(domain.endswith(d) for d in cn_domains):
                return "domestic"
            return "international"
        
        # 默认国外
        return "international"


class ChannelRouter:
    """智能多渠道路由器 v2
    
    管理多个ChannelBase实例，提供统一的消息收发接口和智能路由。
    
    用法:
        router = ChannelRouter()
        router.register("webhook", webhook_channel)
        router.register("telegram", telegram_channel)
        router.register("wechat", wechat_channel)
        
        @router.on_message
        async def handle(msg):
            await router.reply(msg, f"Echo: {msg.content}")
        
        # 智能路由发送
        await router.smart_route("+8613800138000", "Hello!")
        
        await router.start()
    """
    
    def __init__(self):
        self._channels: Dict[str, ChannelBase] = {}
        self._message_callbacks: List[Callable] = []
        self._middleware: List[Callable] = []
        self._running = False
        self._smart_router = SmartRouter()
        self._rate_limits: Dict[str, List[float]] = {}  # channel → [timestamps]
        self._rate_limit_max: int = 20  # 每分钟最大消息数
        self._rate_limit_window: float = 60.0  # 时间窗口（秒）
        
        # 注册渠道分类
        self._register_categories()
    
    def _register_categories(self) -> None:
        """注册渠道分类"""
        for i, ch in enumerate(DOMESTIC_CHANNELS):
            self._smart_router.register_channel(ch, "domestic", i)
        for i, ch in enumerate(INTERNATIONAL_CHANNELS):
            self._smart_router.register_channel(ch, "international", i)
        for i, ch in enumerate(PROTOCOL_CHANNELS):
            self._smart_router.register_channel(ch, "protocol", i + 100)
    
    # ============================================================
    # 渠道管理
    # ============================================================
    
    def register(self, name: str, channel: ChannelBase) -> None:
        """注册渠道
        
        Args:
            name: 渠道名称（唯一标识）
            channel: 渠道实例
        """
        if name in self._channels:
            logger.warning(f"渠道 '{name}' 已存在，将被覆盖")
        
        channel.name = name
        self._channels[name] = channel
        
        # 自动注册消息回调
        channel.on("message", lambda msg: self._dispatch_message(msg))
        
        logger.info(f"注册渠道: {name} ({channel.__class__.__name__})")
    
    def unregister(self, name: str) -> Optional[ChannelBase]:
        """注销渠道"""
        channel = self._channels.pop(name, None)
        if channel:
            logger.info(f"注销渠道: {name}")
        return channel
    
    def get_channel(self, name: str) -> Optional[ChannelBase]:
        """获取渠道实例"""
        return self._channels.get(name)
    
    @property
    def channel_names(self) -> List[str]:
        """所有已注册的渠道名称"""
        return list(self._channels.keys())
    
    def get_connected_channels(self, category: Optional[str] = None) -> List[str]:
        """获取已连接的渠道列表
        
        Args:
            category: 渠道分类（domestic/international/protocol），None表示全部
        """
        result = []
        for name, ch in self._channels.items():
            if not ch.connected:
                continue
            if category:
                cat = self._smart_router._channel_categories.get(name, "")
                if cat != category:
                    continue
            result.append(name)
        return result
    
    # ============================================================
    # 消息回调
    # ============================================================
    
    def on_message(self, callback: Callable) -> Callable:
        """注册统一消息回调
        
        可用作装饰器:
            @router.on_message
            async def handle(msg: Message):
                ...
        """
        self._message_callbacks.append(callback)
        return callback
    
    def use(self, middleware: Callable) -> None:
        """添加中间件"""
        self._middleware.append(middleware)
    
    def _dispatch_message(self, msg: Any) -> None:
        """分发消息到所有回调"""
        if not isinstance(msg, Message):
            logger.warning(f"非标准消息格式: {type(msg)}")
            return
        
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._run_callbacks(msg))
        except RuntimeError:
            asyncio.run(self._run_callbacks(msg))
    
    async def _run_callbacks(self, msg: Message) -> None:
        """执行回调链（含中间件）"""
        async def run_next(index: int, m: Message):
            if index < len(self._middleware):
                await self._middleware[index](m, lambda mm: run_next(index + 1, mm))
            else:
                for cb in self._message_callbacks:
                    try:
                        result = cb(m)
                        if hasattr(result, "__await__"):
                            await result
                    except Exception as e:
                        logger.error(f"消息回调异常: {e}")
        
        await run_next(0, msg)
    
    # ============================================================
    # 消息发送
    # ============================================================
    
    async def send(self, channel: str, target: str, text: str) -> bool:
        """发送消息到指定渠道"""
        ch = self._channels.get(channel)
        if not ch:
            logger.error(f"渠道不存在: {channel}")
            return False
        
        if not ch.connected:
            logger.error(f"渠道未连接: {channel}")
            return False
        
        # 速率限制检查
        if not self._check_rate_limit(channel):
            logger.warning(f"渠道 {channel} 速率限制，消息被丢弃")
            return False
        
        try:
            await ch.send_text(target, text)
            self._record_rate(channel)
            return True
        except Exception as e:
            logger.error(f"发送失败 [{channel} → {target}]: {e}")
            return False
    
    async def reply(self, msg: Message, text: str) -> bool:
        """回复到来源渠道"""
        return await self.send(msg.channel, msg.chat_id, text)
    
    async def broadcast(self, targets: List[Tuple[str, str]], text: str) -> Dict[str, bool]:
        """群发消息
        
        Args:
            targets: 目标列表，每项为 (channel_name, target_id)
            text: 文本内容
        """
        results = {}
        tasks = []
        
        for channel_name, target_id in targets:
            async def _send(ch=channel_name, tgt=target_id):
                key = f"{ch}:{tgt}"
                results[key] = await self.send(ch, tgt, text)
            tasks.append(_send())
        
        await asyncio.gather(*tasks, return_exceptions=True)
        return results
    
    # ============================================================
    # 智能路由
    # ============================================================
    
    async def smart_route(self, target: str, text: str, 
                          prefer: Optional[str] = None) -> bool:
        """智能路由发送
        
        根据目标特征自动选择最佳渠道。
        
        Args:
            target: 目标ID
            text: 文本内容
            prefer: 首选渠道（可选，优先级最高）
        
        Returns:
            是否成功
        """
        # 1. 如果指定了首选渠道，直接使用
        if prefer and prefer in self._channels:
            ch = self._channels[prefer]
            if ch.connected:
                return await self.send(prefer, target, text)
        
        # 2. 判断目标是国内还是国外
        category = self._smart_router.categorize_target(target)
        
        # 3. 获取该分类下已连接的渠道
        available = self.get_connected_channels(category)
        
        # 4. 如果没有该分类的渠道，尝试所有渠道
        if not available:
            available = self.get_connected_channels()
        
        # 5. 选择最佳渠道
        best = self._smart_router.get_best_channel(target, available)
        
        if best:
            logger.info(f"智能路由: {target} → {best} (category={category})")
            return await self.send(best, target, text)
        
        logger.error(f"智能路由失败: 没有可用的渠道 (target={target})")
        return False
    
    async def fallback_send(self, target: str, text: str,
                            channels: Optional[List[str]] = None) -> Dict[str, bool]:
        """带fallback的发送
        
        按顺序尝试多个渠道，直到成功。
        
        Args:
            target: 目标ID
            text: 文本内容
            channels: 渠道列表（按优先级），None则自动选择
        
        Returns:
            {channel_name: success} 结果字典
        """
        if channels is None:
            # 自动选择渠道
            category = self._smart_router.categorize_target(target)
            channels = self._smart_router.get_fallback_chain(category)
            # 只保留已连接的渠道
            channels = [ch for ch in channels if ch in self._channels and self._channels[ch].connected]
        
        results = {}
        for ch_name in channels:
            success = await self.send(ch_name, target, text)
            results[ch_name] = success
            if success:
                logger.info(f"fallback_send成功: {ch_name} → {target}")
                return results
        
        logger.warning(f"fallback_send全部失败: target={target}")
        return results
    
    async def multi_channel_send(self, target: str, text: str,
                                 max_channels: int = 3) -> Dict[str, bool]:
        """多渠道并行发送
        
        同时发送到多个渠道（适用于跨平台通知）。
        
        Args:
            target: 目标ID
            text: 文本内容
            max_channels: 最大渠道数
        
        Returns:
            {channel_name: success} 结果字典
        """
        # 获取所有已连接渠道
        available = self.get_connected_channels()
        channels_to_use = available[:max_channels]
        
        results = {}
        tasks = []
        
        for ch_name in channels_to_use:
            async def _send(ch=ch_name):
                results[ch] = await self.send(ch, target, text)
            tasks.append(_send())
        
        await asyncio.gather(*tasks, return_exceptions=True)
        
        success_count = sum(1 for v in results.values() if v)
        logger.info(f"multi_channel_send: {success_count}/{len(channels_to_use)} 成功")
        return results
    
    # ============================================================
    # 速率限制
    # ============================================================
    
    def _check_rate_limit(self, channel: str) -> bool:
        """检查速率限制"""
        now = time.time()
        timestamps = self._rate_limits.get(channel, [])
        
        # 清理过期记录
        timestamps = [t for t in timestamps if now - t < self._rate_limit_window]
        self._rate_limits[channel] = timestamps
        
        return len(timestamps) < self._rate_limit_max
    
    def _record_rate(self, channel: str) -> None:
        """记录发送"""
        now = time.time()
        if channel not in self._rate_limits:
            self._rate_limits[channel] = []
        self._rate_limits[channel].append(now)
    
    def set_rate_limit(self, max_per_minute: int = 20, window_seconds: float = 60.0) -> None:
        """设置速率限制"""
        self._rate_limit_max = max_per_minute
        self._rate_limit_window = window_seconds
    
    # ============================================================
    # 生命周期
    # ============================================================
    
    async def start(self, configs: Optional[Dict[str, Dict]] = None) -> None:
        """启动所有渠道"""
        if not self._channels:
            logger.warning("没有注册任何渠道")
            return
        
        self._running = True
        configs = configs or {}
        
        # 连接所有渠道
        for name, channel in self._channels.items():
            config = configs.get(name, {})
            try:
                await channel.connect(config)
                logger.info(f"渠道已连接: {name}")
            except Exception as e:
                logger.error(f"渠道连接失败 [{name}]: {e}")
        
        # 启动所有渠道的监听
        listen_tasks = []
        for name, channel in self._channels.items():
            if channel.connected:
                listen_tasks.append(
                    asyncio.create_task(
                        self._safe_listen(name, channel),
                        name=f"listen-{name}",
                    )
                )
        
        if listen_tasks:
            logger.info(f"启动 {len(listen_tasks)} 个渠道监听")
            await asyncio.gather(*listen_tasks, return_exceptions=True)
    
    async def _safe_listen(self, name: str, channel: ChannelBase) -> None:
        """安全监听（捕获异常）"""
        try:
            await channel.listen()
        except asyncio.CancelledError:
            logger.info(f"渠道监听取消: {name}")
        except Exception as e:
            logger.error(f"渠道监听异常 [{name}]: {e}")
    
    async def stop(self) -> None:
        """停止所有渠道"""
        self._running = False
        
        for name, channel in self._channels.items():
            try:
                await channel.disconnect()
                logger.info(f"渠道已断开: {name}")
            except Exception as e:
                logger.error(f"渠道断开异常 [{name}]: {e}")
    
    # ============================================================
    # 状态查询
    # ============================================================
    
    def get_status(self) -> Dict[str, Any]:
        """获取路由器状态"""
        return {
            "running": self._running,
            "channels": {
                name: {
                    "type": ch.__class__.__name__,
                    "connected": ch.connected,
                    "category": self._smart_router._channel_categories.get(name, "unknown"),
                }
                for name, ch in self._channels.items()
            },
            "domestic_connected": len(self.get_connected_channels("domestic")),
            "international_connected": len(self.get_connected_channels("international")),
            "callbacks": len(self._message_callbacks),
            "middleware": len(self._middleware),
        }
    
    def get_routing_info(self) -> Dict[str, Any]:
        """获取路由信息"""
        return {
            "domestic_channels": DOMESTIC_CHANNELS,
            "international_channels": INTERNATIONAL_CHANNELS,
            "protocol_channels": PROTOCOL_CHANNELS,
            "target_patterns": {k: v.pattern for k, v in TARGET_PATTERNS.items()},
            "rate_limit": {
                "max_per_minute": self._rate_limit_max,
                "window_seconds": self._rate_limit_window,
            },
        }
    
    def __repr__(self):
        n = len(self._channels)
        connected = sum(1 for ch in self._channels.values() if ch.connected)
        return f"<ChannelRouter v2 channels={n} connected={connected}>"
