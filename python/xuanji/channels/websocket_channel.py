"""
xuanji WebSocket渠道

支持WebSocket长连接接收/发送消息。
零外部依赖，使用asyncio标准库实现简易WebSocket客户端/服务器。

用法:
    from xuanji.channels.websocket_channel import WebSocketChannel
    
    channel = WebSocketChannel()
    await channel.connect({
        "mode": "server",  # or "client"
        "host": "0.0.0.0",
        "port": 8094,
        # client模式:
        # "url": "ws://server:8094/ws",
    })
"""

import asyncio
import base64
import hashlib
import json
import logging
import os
import struct
import time
from typing import Any, Dict, Optional

from xuanji.channels._base import ChannelBase, ChatType, ContentType, Message

logger = logging.getLogger("xuanji.channels.websocket_channel")


class WebSocketChannel(ChannelBase):
    """WebSocket通信渠道

    支持服务器模式和客户端模式。
    简易WebSocket实现，使用asyncio标准库。
    """

    name = "websocket"
    description = "WebSocket渠道（长连接）"

    # WebSocket opcode
    OPCODE_TEXT = 0x1
    OPCODE_BINARY = 0x2
    OPCODE_CLOSE = 0x8
    OPCODE_PING = 0x9
    OPCODE_PONG = 0xA

    def __init__(self):
        super().__init__()
        self._config: Dict[str, Any] = {}
        self._mode: str = "server"
        self._host: str = "0.0.0.0"
        self._port: int = 8094
        self._url: str = ""
        self._server: Optional[asyncio.AbstractServer] = None
        self._clients: Dict[asyncio.StreamReader, asyncio.StreamWriter] = {}
        self._ws_reader: Optional[asyncio.StreamReader] = None
        self._ws_writer: Optional[asyncio.StreamWriter] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._recent_messages: list = []

    async def connect(self, config: Dict) -> None:
        self._config = config
        self._mode = config.get("mode", "server")
        self._host = config.get("host", "0.0.0.0")
        self._port = config.get("port", 8094)
        self._url = config.get("url", "")

        if self._mode == "server":
            await self._start_server()
        else:
            await self._connect_client()

        self._connected = True
        logger.info(f"WebSocket渠道已连接 (mode={self._mode})")

    async def _start_server(self) -> None:
        """启动WebSocket服务器"""
        self._server = await asyncio.start_server(
            self._handle_client,
            self._host,
            self._port,
        )
        logger.info(f"WebSocket服务器: ws://{self._host}:{self._port}")

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """处理WebSocket客户端连接"""
        try:
            # 读取HTTP升级请求
            request = b""
            while b"\r\n\r\n" not in request:
                chunk = await reader.read(1024)
                if not chunk:
                    return
                request += chunk

            # 提取Sec-WebSocket-Key
            request_text = request.decode("utf-8", errors="replace")
            key = ""
            for line in request_text.split("\r\n"):
                if line.lower().startswith("sec-websocket-key:"):
                    key = line.split(":", 1)[1].strip()
                    break

            if not key:
                writer.close()
                return

            # 生成Sec-WebSocket-Accept
            magic = "258EAFA5-E914-47DA-95CA-5AB5DC61B042"
            accept = base64.b64encode(
                hashlib.sha1((key + magic).encode()).digest()
            ).decode()

            # 发送升级响应
            response = (
                "HTTP/1.1 101 Switching Protocols\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                f"Sec-WebSocket-Accept: {accept}\r\n"
                "\r\n"
            )
            writer.write(response.encode())
            await writer.drain()

            self._clients[reader] = writer
            logger.info(f"WebSocket客户端连接: {writer.get_extra_info('peername')}")

            # 开始接收消息
            await self._read_ws_messages(reader)

        except Exception as e:
            logger.error(f"处理WebSocket客户端异常: {e}")
        finally:
            self._clients.pop(reader, None)
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    async def _connect_client(self) -> None:
        """连接WebSocket服务器"""
        # 解析URL ws://host:port/path
        url = self._url
        if url.startswith("wss://"):
            url = url[6:]
            ssl_ctx = True
        elif url.startswith("ws://"):
            url = url[5:]
            ssl_ctx = False
        else:
            ssl_ctx = False

        host_port = url.split("/")[0]
        path = "/" + "/".join(url.split("/")[1:]) if "/" in url else "/"
        if ":" in host_port:
            host, port_str = host_port.rsplit(":", 1)
            port = int(port_str)
        else:
            host = host_port
            port = 443 if ssl_ctx else 80

        self._ws_reader, self._ws_writer = await asyncio.open_connection(host, port, ssl=ssl_ctx)

        # 发送升级请求
        key = base64.b64encode(os.urandom(16)).decode()
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n"
            f"\r\n"
        )
        self._ws_writer.write(request.encode())
        await self._ws_writer.drain()

        # 等待响应
        response = b""
        while b"\r\n\r\n" not in response:
            chunk = await self._ws_reader.read(1024)
            if not chunk:
                raise ConnectionError("WebSocket服务器无响应")
            response += chunk

        if b"101" not in response:
            raise ConnectionError(f"WebSocket升级失败: {response[:200]}")

        logger.info(f"WebSocket客户端已连接: {self._url}")

    async def _read_ws_messages(self, reader: asyncio.StreamReader) -> None:
        """读取WebSocket消息"""
        while self._connected:
            try:
                frame = await self._read_frame(reader)
                if frame is None:
                    break
                opcode, data = frame

                if opcode == self.OPCODE_CLOSE:
                    break
                elif opcode == self.OPCODE_PING:
                    # 回复PONG
                    if reader in self._clients:
                        writer = self._clients[reader]
                        await self._write_frame(writer, self.OPCODE_PONG, data)
                elif opcode == self.OPCODE_TEXT:
                    text = data.decode("utf-8", errors="replace")
                    self._handle_ws_message(text, reader)
                elif opcode == self.OPCODE_BINARY:
                    self._handle_ws_message(data.decode("utf-8", errors="replace"), reader)

            except Exception as e:
                logger.debug(f"读取WebSocket消息异常: {e}")
                break

    async def _read_frame(self, reader: asyncio.StreamReader) -> Optional[tuple]:
        """读取WebSocket帧"""
        header = await reader.read(2)
        if not header or len(header) < 2:
            return None

        fin = (header[0] >> 7) & 1
        opcode = header[0] & 0x0F
        masked = (header[1] >> 7) & 1
        payload_len = header[1] & 0x7F

        if payload_len == 126:
            ext = await reader.read(2)
            payload_len = struct.unpack("!H", ext)[0]
        elif payload_len == 127:
            ext = await reader.read(8)
            payload_len = struct.unpack("!Q", ext)[0]

        mask_key = None
        if masked:
            mask_key = await reader.read(4)

        data = b""
        while len(data) < payload_len:
            chunk = await reader.read(min(payload_len - len(data), 4096))
            if not chunk:
                break
            data += chunk

        if mask_key:
            data = bytes(data[i] ^ mask_key[i % 4] for i in range(len(data)))

        return opcode, data

    async def _write_frame(self, writer: asyncio.StreamWriter, opcode: int, data: bytes) -> None:
        """写入WebSocket帧"""
        length = len(data)
        frame = bytearray()

        # FIN + opcode
        frame.append(0x80 | opcode)

        # 客户端发送需要mask
        if writer != list(self._clients.values())[0] if self._clients else True:
            # 服务器模式不mask
            frame.append(length & 0x7F)
        else:
            # 客户端模式需要mask
            mask_key = os.urandom(4)
            frame.append(0x80 | (length & 0x7F))
            data = bytes(data[i] ^ mask_key[i % 4] for i in range(len(data)))
            frame.extend(mask_key)

        if length > 65535:
            frame.extend(struct.pack("!Q", length))
        elif length > 125:
            frame.extend(struct.pack("!H", length))

        frame.extend(data)
        writer.write(bytes(frame))
        await writer.drain()

    def _handle_ws_message(self, text: str, reader: asyncio.StreamReader = None) -> None:
        """处理WebSocket消息"""
        try:
            data = json.loads(text)
            sender = data.get("sender", "anonymous")
            chat_id = data.get("chat_id", "ws-default")
            content = data.get("content", "")
            content_type = ContentType.TEXT

            ct = ChatType.PRIVATE
            if data.get("group", False):
                ct = ChatType.GROUP

            msg = Message(
                channel="websocket",
                sender=sender,
                sender_name=data.get("sender_name", sender),
                chat_id=chat_id,
                chat_type=ct,
                content_type=content_type,
                content=content,
                media_url=data.get("media_url", ""),
                timestamp=time.time(),
                raw=data,
            )
            self._recent_messages.append(msg)
            if self._loop:
                asyncio.run_coroutine_threadsafe(self.emit("message", msg), self._loop)
        except json.JSONDecodeError:
            # 纯文本消息
            msg = Message(
                channel="websocket",
                sender="anonymous",
                sender_name="anonymous",
                chat_id="ws-default",
                chat_type=ChatType.PRIVATE,
                content_type=ContentType.TEXT,
                content=text,
                timestamp=time.time(),
                raw=text,
            )
            self._recent_messages.append(msg)
            if self._loop:
                asyncio.run_coroutine_threadsafe(self.emit("message", msg), self._loop)

    async def send_text(self, target: str, text: str) -> None:
        """发送WebSocket消息"""
        payload = json.dumps({
            "sender": "xuanji",
            "sender_name": "xuanji",
            "chat_id": target,
            "content": text,
            "timestamp": time.time(),
        }, ensure_ascii=False)

        if self._mode == "server" and self._clients:
            # 广播到所有客户端（或发送到指定chat_id）
            for reader, writer in list(self._clients.items()):
                try:
                    await self._write_frame(writer, self.OPCODE_TEXT, payload.encode())
                except Exception as e:
                    logger.debug(f"WebSocket发送异常: {e}")
        elif self._ws_writer:
            try:
                await self._write_frame(self._ws_writer, self.OPCODE_TEXT, payload.encode())
            except Exception as e:
                logger.error(f"WebSocket发送异常: {e}")

    async def send_image(self, target: str, image: Any) -> None:
        logger.info(f"[WebSocket] 发送图片到 {target}")

    async def send_file(self, target: str, path: str) -> None:
        logger.info(f"[WebSocket] 发送文件到 {target}: {path}")

    async def send_voice(self, target: str, audio: Any) -> None:
        logger.info(f"[WebSocket] 发送语音到 {target}")

    async def listen(self) -> None:
        if not self._connected:
            raise RuntimeError("未连接")
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None

        if self._mode == "client" and self._ws_reader:
            await self._read_ws_messages(self._ws_reader)
        else:
            # 服务器模式，保持运行
            while self._connected:
                await asyncio.sleep(1)

    async def disconnect(self) -> None:
        self._connected = False
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        if self._ws_writer:
            try:
                await self._write_frame(self._ws_writer, self.OPCODE_CLOSE, b"")
                self._ws_writer.close()
                await self._ws_writer.wait_closed()
            except Exception:
                pass
            self._ws_writer = None
            self._ws_reader = None
        self._clients.clear()
        logger.info("WebSocket渠道已断开")
