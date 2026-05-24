"""
xuanji Instagram渠道

支持Instagram Graph API。
零外部依赖，使用urllib.request标准库。

用法:
    from xuanji.channels.instagram import InstagramChannel
    
    channel = InstagramChannel()
    await channel.connect({
        "access_token": "...",
        "instagram_business_account_id": "...",
    })
"""

import asyncio
import json
import logging
import time
from typing import Any, Dict, Optional
from urllib.request import Request, urlopen

from xuanji.channels._base import ChannelBase, ChatType, ContentType, Message

logger = logging.getLogger("xuanji.channels.instagram")


class InstagramChannel(ChannelBase):
    """Instagram通信渠道"""

    name = "instagram"
    description = "Instagram渠道（Graph API）"

    API_BASE = "https://graph.facebook.com/v18.0"

    def __init__(self):
        super().__init__()
        self._config: Dict[str, Any] = {}
        self._access_token: str = ""
        self._ig_business_id: str = ""
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._recent_messages: list = []

    async def connect(self, config: Dict) -> None:
        self._config = config
        self._access_token = config.get("access_token", "")
        self._ig_business_id = config.get("instagram_business_account_id", "")

        if not self._access_token or not self._ig_business_id:
            raise ValueError("Instagram需要 access_token 和 instagram_business_account_id")

        self._connected = True
        logger.info("Instagram渠道已连接")

    async def listen(self) -> None:
        if not self._connected:
            raise RuntimeError("未连接")
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None
        # Instagram通过Webhook推送消息
        while self._connected:
            await asyncio.sleep(5)

    def _process_webhook(self, data: Dict) -> None:
        """处理Instagram Webhook数据"""
        try:
            entry = data.get("entry", [{}])[0]
            messaging = entry.get("messaging", [])
            for msg_data in messaging:
                sender = msg_data.get("sender", {}).get("id", "")
                recipient = msg_data.get("recipient", {}).get("id", "")

                ct = ContentType.TEXT
                content = ""
                media_url = ""

                if "message" in msg_data:
                    msg = msg_data["message"]
                    content = msg.get("text", "")
                    attachments = msg.get("attachments", [])
                    if attachments:
                        att = attachments[0]
                        if att.get("type") == "image":
                            ct = ContentType.IMAGE
                            media_url = att.get("payload", {}).get("url", "")
                        elif att.get("type") == "audio":
                            ct = ContentType.AUDIO
                            media_url = att.get("payload", {}).get("url", "")
                        elif att.get("type") == "video":
                            ct = ContentType.VIDEO
                            media_url = att.get("payload", {}).get("url", "")
                        elif att.get("type") == "file":
                            ct = ContentType.FILE
                            media_url = att.get("payload", {}).get("url", "")

                message_msg = Message(
                    channel="instagram",
                    sender=sender,
                    sender_name="",
                    chat_id=sender,
                    chat_type=ChatType.PRIVATE,
                    content_type=ct,
                    content=content,
                    media_url=media_url,
                    timestamp=time.time(),
                    raw=msg_data,
                )
                self._recent_messages.append(message_msg)
                if self._loop:
                    asyncio.run_coroutine_threadsafe(self.emit("message", message_msg), self._loop)
        except Exception as e:
            logger.error(f"处理Instagram Webhook异常: {e}")

    async def send_text(self, target: str, text: str) -> None:
        url = f"{self.API_BASE}/{self._ig_business_id}/messages"
        payload = {
            "recipient": {"id": target},
            "message": {"text": text},
        }
        req = Request(url, data=json.dumps(payload).encode("utf-8"),
                      headers={"Content-Type": "application/json"},
                      method="POST")
        req.add_header("Authorization", f"Bearer {self._access_token}")
        try:
            with urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if data.get("error"):
                    logger.error(f"Instagram发送失败: {data}")
        except Exception as e:
            logger.error(f"Instagram发送异常: {e}")

    async def send_image(self, target: str, image: Any) -> None:
        media_url = await self._upload_media(image)
        if media_url:
            url = f"{self.API_BASE}/{self._ig_business_id}/messages"
            payload = {
                "recipient": {"id": target},
                "message": {
                    "attachment": {
                        "type": "image",
                        "payload": {"url": media_url},
                    }
                },
            }
            req = Request(url, data=json.dumps(payload).encode("utf-8"),
                          headers={"Content-Type": "application/json"}, method="POST")
            req.add_header("Authorization", f"Bearer {self._access_token}")
            try:
                with urlopen(req, timeout=15) as resp:
                    pass
            except Exception as e:
                logger.error(f"Instagram发送图片异常: {e}")

    async def send_file(self, target: str, path: str) -> None:
        logger.info(f"[Instagram] 发送文件到 {target}: {path}")

    async def send_voice(self, target: str, audio: Any) -> None:
        logger.info(f"[Instagram] 发送语音到 {target}")

    async def _upload_media(self, source: Any) -> str:
        """上传媒体"""
        url = f"{self.API_BASE}/{self._ig_business_id}/media_publish"
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
                f'Content-Type: image/jpeg\r\n\r\n'
            ).encode() + file_data + f"\r\n--{boundary}--\r\n".encode()
            req = Request(url, data=body,
                          headers={
                              "Content-Type": f"multipart/form-data; boundary={boundary}",
                              "Authorization": f"Bearer {self._access_token}",
                          }, method="POST")
            with urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("url", "")
        except Exception as e:
            logger.error(f"Instagram上传媒体失败: {e}")
            return ""

    async def disconnect(self) -> None:
        self._connected = False
        logger.info("Instagram渠道已断开")
