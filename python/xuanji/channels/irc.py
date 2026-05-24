"""
xuanji IRC渠道

支持标准IRC协议。
零外部依赖，使用socket标准库。

用法:
    from xuanji.channels.irc import IRCChannel
    
    channel = IRCChannel()
    await channel.connect({
        "server": "irc.libera.chat",
        "port": 6697,
        "nickname": "xuanji",
        "channels": ["#general", "#bot"],
        "ssl": True,
    })
"""

import asyncio
import json
import logging
import ssl
import time
from typing import Any, Dict, List, Optional
from urllib.request import Request, urlopen

from xuanji.channels._base import ChannelBase, ChatType, ContentType, Message

logger = logging.getLogger("xuanji.channels.irc")


class IRCChannel(ChannelBase):
    """IRC通信渠道

    标准IRC协议实现。
    """

    name = "irc"
    description = "IRC渠道（标准IRC协议）"

    def __init__(self):
        super().__init__()
        self._config: Dict[str, Any] = {}
        self._server: str = ""
        self._port: int = 6667
        self._nickname: str = ""
        self._channels: List[str] = []
        self._ssl: bool = False
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._recent_messages: list = []

    async def connect(self, config: Dict) -> None:
        self._config = config
        self._server = config.get("server", "irc.libera.chat")
        self._port = config.get("port", 6697)
        self._nickname = config.get("nickname", "xuanji")
        self._channels = config.get("channels", [])
        self._ssl = config.get("ssl", True)

        await self._connect_irc()
        self._connected = True
        logger.info(f"IRC渠道已连接 ({self._server}:{self._port})")

    async def _connect_irc(self) -> None:
        """连接IRC服务器"""
        ctx = None
        if self._ssl:
            ctx = ssl.create_default_context()

        self._reader, self._writer = await asyncio.open_connection(
            self._server, self._port, ssl=ctx
        )

        # 发送NICK和USER
        await self._send(f"NICK {self._nickname}")
        await self._send(f"USER {self._nickname} 0 * :xuanji Bot")

        # 等待欢迎消息
        await self._read_until_welcome()

        # 加入频道
        for channel in self._channels:
            await self._send(f"JOIN {channel}")
            logger.info(f"IRC加入频道: {channel}")

    async def _send(self, data: str) -> None:
        """发送IRC命令"""
        if self._writer:
            self._writer.write((data + "\r\n").encode("utf-8"))
            await self._writer.drain()

    async def _read_until_welcome(self) -> None:
        """等待IRC欢迎消息"""
        if not self._reader:
            return
        while True:
            line = await self._reader.readline()
            if not line:
                raise ConnectionError("IRC连接断开")
            text = line.decode("utf-8").strip()
            logger.debug(f"IRC: {text}")
            # 001 = RPL_WELCOME
            if " 001 " in text or "Welcome" in text:
                break

    async def _send_raw(self, data: str) -> None:
        """发送原始IRC数据"""
        await self._send(data)

    async def listen(self) -> None:
        if not self._connected or not self._reader:
            raise RuntimeError("未连接")
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None

        while self._connected:
            try:
                line = await self._reader.readline()
                if not line:
                    raise ConnectionError("IRC连接断开")
                text = line.decode("utf-8").strip()
                self._handle_irc_line(text)
            except Exception as e:
                logger.debug(f"IRC监听异常: {e}")
                await asyncio.sleep(2)
                try:
                    await self._connect_irc()
                except Exception:
                    pass

    def _handle_irc_line(self, line: str) -> None:
        """处理IRC行"""
        try:
            if not line:
                return

            # PING
            if line.startswith("PING"):
                pong = line.replace("PING", "PONG")
                asyncio.ensure_future(self._send(pong))
                return

            # 解析消息
            # :nick!user@host PRIVMSG #channel :message
            # :nick!user@host PRIVMSG bot :message
            parts = line.split(" ", 2)
            if len(parts) < 3:
                return

            prefix = parts[0]
            command = parts[1]
            params = parts[2] if len(parts) > 2 else ""

            if command == "PRIVMSG":
                # 提取发送者
                sender = prefix[1:].split("!")[0] if prefix.startswith(":") else ""
                # 提取目标（频道或私聊）
                target_parts = params.split(" :", 1)
                target = target_parts[0].strip()
                content = target_parts[1].strip() if len(target_parts) > 1 else ""

                ct = ChatType.GROUP if target.startswith("#") or target.startswith("&") else ChatType.PRIVATE

                msg = Message(
                    channel="irc",
                    sender=sender,
                    sender_name=sender,
                    chat_id=target,
                    chat_type=ct,
                    content_type=ContentType.TEXT,
                    content=content,
                    timestamp=time.time(),
                    raw=line,
                )
                self._recent_messages.append(msg)
                if self._loop:
                    asyncio.run_coroutine_threadsafe(self.emit("message", msg), self._loop)

            elif command == "JOIN":
                sender = prefix[1:].split("!")[0] if prefix.startswith(":") else ""
                logger.info(f"IRC JOIN: {sender}")

            elif command == "PART":
                sender = prefix[1:].split("!")[0] if prefix.startswith(":") else ""
                logger.info(f"IRC PART: {sender}")

        except Exception as e:
            logger.error(f"处理IRC行异常: {e}")

    async def send_text(self, target: str, text: str) -> None:
        """发送IRC消息"""
        await self._send(f"PRIVMSG {target} :{text}")

    async def send_image(self, target: str, image: Any) -> None:
        logger.info(f"[IRC] 发送图片到 {target}")

    async def send_file(self, target: str, path: str) -> None:
        logger.info(f"[IRC] 发送文件到 {target}: {path}")

    async def send_voice(self, target: str, audio: Any) -> None:
        logger.info(f"[IRC] 发送语音到 {target}")

    async def disconnect(self) -> None:
        self._connected = False
        if self._writer:
            try:
                await self._send(f"QUIT :xuanji disconnecting")
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
            self._writer = None
            self._reader = None
        logger.info("IRC渠道已断开")
