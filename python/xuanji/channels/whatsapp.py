"""
xuanji WhatsApp渠道

支持WhatsApp Business API（Cloud API）。
零外部依赖，使用urllib.request标准库。

用法:
    from xuanji.channels.whatsapp import WhatsAppChannel
    
    channel = WhatsAppChannel()
    await channel.connect({
        "phone_number_id": "...",
        "access_token": "...",
    })
"""

import asyncio
import json
import logging
import time
from typing import Any, Dict, Optional
from urllib.request import Request, urlopen

from xuanji.channels._base import ChannelBase, ChatType, ContentType, Message

logger = logging.getLogger("xuanji.channels.whatsapp")


class WhatsAppChannel(ChannelBase):
    """WhatsApp通信渠道"""

    name = "whatsapp"
    description = "WhatsApp渠道（Business Cloud API）"

    API_BASE = "https://graph.facebook.com/v17.0"

    def __init__(self):
        super().__init__()
        self._config: Dict[str, Any] = {}
        self._phone_number_id: str = ""
        self._access_token: str = ""
        self._wa_business_id: str = ""
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._recent_messages: list = []

    async def connect(self, config: Dict) -> None:
        self._config = config
        self._phone_number_id = config.get("phone_number_id", "")
        self._access_token = config.get("access_token", "")
        self._wa_business_id = config.get("wa_business_id", "")

        if not self._phone_number_id or not self._access_token:
            raise ValueError("WhatsApp需要 phone_number_id 和 access_token")

        self._connected = True
        logger.info("WhatsApp渠道已连接")

    async def listen(self) -> None:
        if not self._connected:
            raise RuntimeError("未连接")
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None
        # WhatsApp通过Webhook推送消息
        while self._connected:
            await asyncio.sleep(5)

    def _process_webhook_data(self, data: Dict) -> None:
        """处理WhatsApp Webhook数据"""
        try:
            entry = data.get("entry", [{}])[0]
            changes = entry.get("changes", [{}])[0]
            messages = changes.get("value", {}).get("messages", [])
            for msg in messages:
                from_id = msg.get("from", "")
                ct = ContentType.TEXT
                content = ""
                media_url = ""

                if "text" in msg:
                    content = msg["text"].get("body", "")
                elif "image" in msg:
                    ct = ContentType.IMAGE
                    media_url = msg["image"].get("url", "")
                elif "audio" in msg:
                    ct = ContentType.AUDIO
                    media_url = msg["audio"].get("url", "")
                elif "video" in msg:
                    ct = ContentType.VIDEO
                    media_url = msg["video"].get("url", "")
                elif "document" in msg:
                    ct = ContentType.FILE
                    media_url = msg["document"].get("url", "")
                elif "location" in msg:
                    ct = ContentType.LOCATION
                    loc = msg["location"]
                    content = f"Location: ({loc.get('latitude', 0)}, {loc.get('longitude', 0)})"
                elif "sticker" in msg:
                    ct = ContentType.STICKER
                    media_url = msg["sticker"].get("url", "")

                message_msg = Message(
                    channel="whatsapp",
                    sender=from_id,
                    sender_name=msg.get("contact", {}).get("profile", {}).get("name", ""),
                    chat_id=from_id,
                    chat_type=ChatType.PRIVATE,
                    content_type=ct,
                    content=content,
                    media_url=media_url,
                    reply_to=msg.get("context", {}).get("id", ""),
                    timestamp=int(msg.get("timestamp", 0)),
                    raw=msg,
                )
                self._recent_messages.append(message_msg)
                if self._loop:
                    asyncio.run_coroutine_threadsafe(self.emit("message", message_msg), self._loop)
        except Exception as e:
            logger.error(f"处理WhatsApp Webhook数据异常: {e}")

    async def send_text(self, target: str, text: str) -> None:
        url = f"{self.API_BASE}/{self._phone_number_id}/messages"
        payload = {
            "messaging_product": "whatsapp",
            "to": target,
            "type": "text",
            "text": {"body": text},
        }
        req = Request(url, data=json.dumps(payload).encode("utf-8"),
                      headers={
                          "Content-Type": "application/json",
                          "Authorization": f"Bearer {self._access_token}",
                      }, method="POST")
        try:
            with urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if data.get("messages", [{}])[0].get("error"):
                    logger.error(f"WhatsApp发送失败: {data}")
        except Exception as e:
            logger.error(f"WhatsApp发送异常: {e}")

    async def send_image(self, target: str, image: Any) -> None:
        """发送图片 — 先上传到WhatsApp媒体服务器"""
        media_id = await self._upload_media(image, "image/jpeg")
        if media_id:
            url = f"{self.API_BASE}/{self._phone_number_id}/messages"
            payload = {
                "messaging_product": "whatsapp",
                "to": target,
                "type": "image",
                "image": {"id": media_id},
            }
            req = Request(url, data=json.dumps(payload).encode("utf-8"),
                          headers={
                              "Content-Type": "application/json",
                              "Authorization": f"Bearer {self._access_token}",
                          }, method="POST")
            try:
                with urlopen(req, timeout=15) as resp:
                    pass
            except Exception as e:
                logger.error(f"WhatsApp发送图片异常: {e}")

    async def send_file(self, target: str, path: str) -> None:
        media_id = await self._upload_media(path, "application/octet-stream")
        if media_id:
            url = f"{self.API_BASE}/{self._phone_number_id}/messages"
            payload = {
                "messaging_product": "whatsapp",
                "to": target,
                "type": "document",
                "document": {"id": media_id, "filename": path.split("/")[-1]},
            }
            req = Request(url, data=json.dumps(payload).encode("utf-8"),
                          headers={
                              "Content-Type": "application/json",
                              "Authorization": f"Bearer {self._access_token}",
                          }, method="POST")
            try:
                with urlopen(req, timeout=15) as resp:
                    pass
            except Exception as e:
                logger.error(f"WhatsApp发送文件异常: {e}")

    async def send_voice(self, target: str, audio: Any) -> None:
        media_id = await self._upload_media(audio, "audio/ogg")
        if media_id:
            url = f"{self.API_BASE}/{self._phone_number_id}/messages"
            payload = {
                "messaging_product": "whatsapp",
                "to": target,
                "type": "audio",
                "audio": {"id": media_id},
            }
            req = Request(url, data=json.dumps(payload).encode("utf-8"),
                          headers={
                              "Content-Type": "application/json",
                              "Authorization": f"Bearer {self._access_token}",
                          }, method="POST")
            try:
                with urlopen(req, timeout=15) as resp:
                    pass
            except Exception as e:
                logger.error(f"WhatsApp发送语音异常: {e}")

    async def _upload_media(self, source: Any, mime_type: str) -> str:
        """上传媒体到WhatsApp"""
        url = f"{self.API_BASE}/{self._phone_number_id}/media"
        try:
            if isinstance(source, str):
                with open(source, "rb") as f:
                    file_data = f.read()
            else:
                file_data = source
            import uuid
            boundary = f"----FormBoundary{uuid.uuid4().hex[:16]}"
            body = (
                f'--{boundary}\r\n'
                f'Content-Disposition: form-data; name="file"; filename="media"\r\n'
                f'Content-Type: {mime_type}\r\n\r\n'
            ).encode() + file_data + f"\r\n--{boundary}--\r\n".encode()
            req = Request(url, data=body,
                          headers={
                              "Content-Type": f"multipart/form-data; boundary={boundary}",
                              "Authorization": f"Bearer {self._access_token}",
                          }, method="POST")
            with urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("id", "")
        except Exception as e:
            logger.error(f"WhatsApp上传媒体失败: {e}")
            return ""

    async def disconnect(self) -> None:
        self._connected = False
        logger.info("WhatsApp渠道已断开")
