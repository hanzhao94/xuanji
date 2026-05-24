"""
xuanji Slack渠道

支持Slack Bot API / Web API。
零外部依赖，使用urllib.request标准库。

用法:
    from xuanji.channels.slack import SlackChannel
    
    channel = SlackChannel()
    await channel.connect({
        "bot_token": "xoxb-...",
    })
"""

import asyncio
import json
import logging
import time
from typing import Any, Dict, Optional
from urllib.request import Request, urlopen

from xuanji.channels._base import ChannelBase, ChatType, ContentType, Message

logger = logging.getLogger("xuanji.channels.slack")


class SlackChannel(ChannelBase):
    """Slack通信渠道"""

    name = "slack"
    description = "Slack渠道（Web API / RTM）"

    API_BASE = "https://slack.com/api"

    def __init__(self):
        super().__init__()
        self._config: Dict[str, Any] = {}
        self._bot_token: str = ""
        self._bot_id: str = ""
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._recent_messages: list = []

    async def connect(self, config: Dict) -> None:
        self._config = config
        self._bot_token = config.get("bot_token", "")
        if not self._bot_token:
            raise ValueError("Slack渠道需要 bot_token (xoxb-...)")

        # 验证token
        try:
            req = Request(f"{self.API_BASE}/auth.test",
                          headers={"Authorization": f"Bearer {self._bot_token}"})
            with urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if not data.get("ok"):
                    raise ValueError(f"Slack token无效: {data}")
                self._bot_id = data.get("user_id", "")
                logger.info(f"Slack Bot已连接: {data.get('user', 'unknown')}")
        except Exception as e:
            if "token无效" in str(e):
                raise
            logger.warning(f"Slack验证失败: {e}")

        self._connected = True

    async def listen(self) -> None:
        if not self._connected:
            raise RuntimeError("未连接")
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None
        # Slack通过Events API或Socket Mode接收消息
        while self._connected:
            await asyncio.sleep(5)

    def _process_event(self, event: Dict) -> None:
        """处理Slack事件"""
        try:
            if event.get("type") not in ("message",):
                return

            channel_type = event.get("channel_type", "")
            if channel_type == "im":
                ct = ChatType.PRIVATE
            elif channel_type in ("group", "public_channel", "private_channel"):
                ct = ChatType.GROUP
            else:
                ct = ChatType.PRIVATE

            content_type = ContentType.TEXT
            content = event.get("text", "")
            media_url = ""

            # 检查文件附件
            files = event.get("files", [])
            if files:
                f = files[0]
                ftype = f.get("filetype", "")
                if ftype in ("png", "jpg", "jpeg", "gif", "bmp"):
                    content_type = ContentType.IMAGE
                elif ftype in ("mp3", "ogg", "wav"):
                    content_type = ContentType.AUDIO
                elif ftype in ("mp4", "mov"):
                    content_type = ContentType.VIDEO
                else:
                    content_type = ContentType.FILE
                media_url = f.get("url_private", "")

            msg = Message(
                channel="slack",
                sender=event.get("user", ""),
                sender_name=event.get("username", ""),
                chat_id=event.get("channel", ""),
                chat_type=ct,
                content_type=content_type,
                content=content,
                media_url=media_url,
                reply_to=event.get("thread_ts", ""),
                timestamp=float(event.get("ts", 0)),
                raw=event,
            )
            self._recent_messages.append(msg)
            if self._loop:
                asyncio.run_coroutine_threadsafe(self.emit("message", msg), self._loop)
        except Exception as e:
            logger.error(f"处理Slack事件异常: {e}")

    async def send_text(self, target: str, text: str) -> None:
        url = f"{self.API_BASE}/chat.postMessage"
        payload = {"channel": target, "text": text}
        req = Request(url, data=json.dumps(payload).encode("utf-8"),
                      headers={
                          "Content-Type": "application/json; charset=utf-8",
                          "Authorization": f"Bearer {self._bot_token}",
                      }, method="POST")
        try:
            with urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if not data.get("ok"):
                    logger.error(f"Slack发送失败: {data}")
        except Exception as e:
            logger.error(f"Slack发送异常: {e}")

    async def send_image(self, target: str, image: Any) -> None:
        """发送图片 — 使用files.upload"""
        url = f"{self.API_BASE}/files.upload"
        import uuid
        boundary = f"----FormBoundary{uuid.uuid4().hex[:16]}"
        if isinstance(image, str):
            with open(image, "rb") as f:
                img_data = f.read()
            filename = image.split("/")[-1]
        else:
            img_data = image
            filename = "image.png"
        body = (
            f'--{boundary}\r\n'
            f'Content-Disposition: form-data; name="channels"\r\n\r\n'
            f'{target}\r\n'
            f'--{boundary}\r\n'
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            f'Content-Type: image/jpeg\r\n\r\n'
        ).encode() + img_data + f"\r\n--{boundary}--\r\n".encode()
        req = Request(url, data=body,
                      headers={
                          "Content-Type": f"multipart/form-data; boundary={boundary}",
                          "Authorization": f"Bearer {self._bot_token}",
                      }, method="POST")
        try:
            with urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if not data.get("ok"):
                    logger.error(f"Slack发送图片失败: {data}")
        except Exception as e:
            logger.error(f"Slack发送图片异常: {e}")

    async def send_file(self, target: str, path: str) -> None:
        url = f"{self.API_BASE}/files.upload"
        import uuid
        boundary = f"----FormBoundary{uuid.uuid4().hex[:16]}"
        with open(path, "rb") as f:
            file_data = f.read()
        filename = path.split("/")[-1]
        body = (
            f'--{boundary}\r\n'
            f'Content-Disposition: form-data; name="channels"\r\n\r\n'
            f'{target}\r\n'
            f'--{boundary}\r\n'
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            f'Content-Type: application/octet-stream\r\n\r\n'
        ).encode() + file_data + f"\r\n--{boundary}--\r\n".encode()
        req = Request(url, data=body,
                      headers={
                          "Content-Type": f"multipart/form-data; boundary={boundary}",
                          "Authorization": f"Bearer {self._bot_token}",
                      }, method="POST")
        try:
            with urlopen(req, timeout=30) as resp:
                pass
        except Exception as e:
            logger.error(f"Slack发送文件异常: {e}")

    async def send_voice(self, target: str, audio: Any) -> None:
        url = f"{self.API_BASE}/files.upload"
        import uuid
        boundary = f"----FormBoundary{uuid.uuid4().hex[:16]}"
        if isinstance(audio, str):
            with open(audio, "rb") as f:
                audio_data = f.read()
            filename = audio.split("/")[-1]
        else:
            audio_data = audio
            filename = "voice.ogg"
        body = (
            f'--{boundary}\r\n'
            f'Content-Disposition: form-data; name="channels"\r\n\r\n'
            f'{target}\r\n'
            f'--{boundary}\r\n'
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            f'Content-Type: audio/ogg\r\n\r\n'
        ).encode() + audio_data + f"\r\n--{boundary}--\r\n".encode()
        req = Request(url, data=body,
                      headers={
                          "Content-Type": f"multipart/form-data; boundary={boundary}",
                          "Authorization": f"Bearer {self._bot_token}",
                      }, method="POST")
        try:
            with urlopen(req, timeout=30) as resp:
                pass
        except Exception as e:
            logger.error(f"Slack发送语音异常: {e}")

    async def disconnect(self) -> None:
        self._connected = False
        logger.info("Slack渠道已断开")
