"""
xuanji LINE渠道

支持LINE Messaging API。
零外部依赖，使用urllib.request标准库。

用法:
    from xuanji.channels.line import LINEChannel
    
    channel = LINEChannel()
    await channel.connect({
        "channel_access_token": "...",
        "channel_secret": "...",
    })
"""

import asyncio
import hashlib
import hmac
import json
import logging
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any, Dict, Optional
from urllib.request import Request, urlopen

from xuanji.channels._base import ChannelBase, ChatType, ContentType, Message

logger = logging.getLogger("xuanji.channels.line")


class _LINEHandler(BaseHTTPRequestHandler):
    """LINE Webhook处理器"""
    channel: Optional["LINEChannel"] = None

    def log_message(self, fmt, *args):
        logger.debug(fmt % args)

    def do_POST(self):
        if self.channel is None:
            self._respond(404, b"not found")
            return
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""
        signature = self.headers.get("X-Line-Signature", "")
        # 验证签名
        secret = self.channel._config.get("channel_secret", "")
        expected = hmac.new(
            secret.encode(), body, hashlib.sha256
        ).digest()
        import base64
        if signature != base64.b64encode(expected).decode():
            logger.warning("LINE Webhook签名验证失败")
        try:
            data = json.loads(body.decode("utf-8"))
            self.channel._process_events(data)
            self._respond(200, b"ok")
        except Exception as e:
            logger.error(f"处理LINE Webhook异常: {e}")
            self._respond(500, b"error")

    def _respond(self, code: int, data: bytes):
        self.send_response(code)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class LINEChannel(ChannelBase):
    """LINE通信渠道"""

    name = "line"
    description = "LINE渠道（Messaging API）"

    API_BASE = "https://api.line.me/v2"

    def __init__(self):
        super().__init__()
        self._config: Dict[str, Any] = {}
        self._channel_access_token: str = ""
        self._server: Optional[HTTPServer] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._recent_messages: list = []

    async def connect(self, config: Dict) -> None:
        self._config = config
        self._channel_access_token = config.get("channel_access_token", "")
        if not self._channel_access_token:
            raise ValueError("LINE需要 channel_access_token")

        self._connected = True
        logger.info("LINE渠道已连接")

    async def listen(self) -> None:
        if not self._connected:
            raise RuntimeError("未连接")
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None

        if self._config.get("webhook_port"):
            host = self._config.get("host", "0.0.0.0")
            port = self._config.get("webhook_port", 8093)
            handler_cls = type("_LINEBoundHandler", (_LINEHandler,), {"channel": self})
            self._server = HTTPServer((host, port), handler_cls)
            import threading
            threading.Thread(target=self._server.serve_forever, daemon=True,
                             name="line-webhook").start()
            logger.info(f"LINE Webhook服务器: http://{host}:{port}")

        while self._connected:
            await asyncio.sleep(1)

    def _process_events(self, data: Dict) -> None:
        """处理LINE事件"""
        try:
            for event in data.get("events", []):
                if event.get("type") != "message":
                    continue
                msg = event.get("message", {})
                sender = event.get("source", {}).get("userId", "")
                source_type = event.get("source", {}).get("type", "user")

                ct = ChatType.PRIVATE if source_type == "user" else ChatType.GROUP
                content_type = ContentType.TEXT
                content = ""
                media_url = ""

                msg_type = msg.get("type", "text")
                if msg_type == "text":
                    content = msg.get("text", "")
                elif msg_type == "image":
                    content_type = ContentType.IMAGE
                    media_url = msg.get("contentProvider", {}).get("originalContentUrl", "")
                elif msg_type == "video":
                    content_type = ContentType.VIDEO
                    media_url = msg.get("contentProvider", {}).get("originalContentUrl", "")
                elif msg_type == "audio":
                    content_type = ContentType.AUDIO
                elif msg_type == "file":
                    content_type = ContentType.FILE
                    content = msg.get("fileName", "")
                elif msg_type == "location":
                    content_type = ContentType.LOCATION
                    loc = msg
                    content = f"Location: ({loc.get('latitude', 0)}, {loc.get('longitude', 0)})"
                elif msg_type == "sticker":
                    content_type = ContentType.STICKER

                message_msg = Message(
                    channel="line",
                    sender=sender,
                    sender_name="",
                    chat_id=event.get("source", {}).get("groupId", sender),
                    chat_type=ct,
                    content_type=content_type,
                    content=content,
                    media_url=media_url,
                    reply_to=msg.get("id", ""),
                    timestamp=time.time(),
                    raw=event,
                )
                self._recent_messages.append(message_msg)
                if self._loop:
                    asyncio.run_coroutine_threadsafe(self.emit("message", message_msg), self._loop)
        except Exception as e:
            logger.error(f"处理LINE事件异常: {e}")

    async def send_text(self, target: str, text: str) -> None:
        """发送消息
        
        Args:
            target: userId / groupId / room_id 或 "replyToken"（回复模式）
            text: 内容
        """
        url = f"{self.API_BASE}/bot/message/push"
        payload = {
            "to": target,
            "messages": [{"type": "text", "text": text}],
        }
        req = Request(url, data=json.dumps(payload).encode("utf-8"),
                      headers={
                          "Content-Type": "application/json",
                          "Authorization": f"Bearer {self._channel_access_token}",
                      }, method="POST")
        try:
            with urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if resp.status >= 300:
                    logger.error(f"LINE发送失败: {data}")
        except Exception as e:
            logger.error(f"LINE发送异常: {e}")

    async def send_image(self, target: str, image: Any) -> None:
        """发送图片"""
        url = f"{self.API_BASE}/bot/message/push"
        if isinstance(image, str) and image.startswith("http"):
            payload = {
                "to": target,
                "messages": [{
                    "type": "image",
                    "originalContentUrl": image,
                    "previewImageUrl": image,
                }],
            }
            req = Request(url, data=json.dumps(payload).encode("utf-8"),
                          headers={
                              "Content-Type": "application/json",
                              "Authorization": f"Bearer {self._channel_access_token}",
                          }, method="POST")
        else:
            # 上传富媒体
            media_id = await self._upload_rich_media(image)
            if media_id:
                payload = {
                    "to": target,
                    "messages": [{"type": "image", "originalContentUrl": media_id, "previewImageUrl": media_id}],
                }
                req = Request(url, data=json.dumps(payload).encode("utf-8"),
                              headers={
                                  "Content-Type": "application/json",
                                  "Authorization": f"Bearer {self._channel_access_token}",
                              }, method="POST")
            else:
                return
        try:
            with urlopen(req, timeout=15) as resp:
                pass
        except Exception as e:
            logger.error(f"LINE发送图片异常: {e}")

    async def send_file(self, target: str, path: str) -> None:
        logger.info(f"[LINE] 发送文件到 {target}: {path}")

    async def send_voice(self, target: str, audio: Any) -> None:
        logger.info(f"[LINE] 发送语音到 {target}")

    async def _upload_rich_media(self, source: Any) -> str:
        """上传富媒体到LINE"""
        url = f"{self.API_BASE}/bot/message/{len(self._recent_messages)}/content"  # placeholder
        # 实际应使用 /bot/message/{messageId}/content
        logger.debug("LINE富媒体上传需要messageId")
        return ""

    async def disconnect(self) -> None:
        self._connected = False
        if self._server:
            self._server.shutdown()
            self._server = None
        logger.info("LINE渠道已断开")
