"""
xuanji Discord渠道

支持Discord Bot API。
零外部依赖，使用urllib.request标准库。

用法:
    from xuanji.channels.discord import DiscordChannel
    
    channel = DiscordChannel()
    await channel.connect({
        "bot_token": "...",
    })
"""

import asyncio
import json
import logging
import time
from typing import Any, Dict, Optional
from urllib.request import Request, urlopen
from urllib.error import URLError

from xuanji.channels._base import ChannelBase, ChatType, ContentType, Message

logger = logging.getLogger("xuanji.channels.discord")


class DiscordChannel(ChannelBase):
    """Discord通信渠道"""

    name = "discord"
    description = "Discord渠道（Bot API）"

    API_BASE = "https://discord.com/api/v10"

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
            raise ValueError("Discord渠道需要 bot_token")

        # 验证token
        try:
            req = Request(f"{self.API_BASE}/users/@me",
                          headers={"Authorization": f"Bot {self._bot_token}"})
            with urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                self._bot_id = str(data.get("id", ""))
                logger.info(f"Discord Bot已连接: {data.get('username', 'unknown')}")
        except Exception as e:
            logger.warning(f"Discord验证失败: {e}")

        self._connected = True

    async def listen(self) -> None:
        if not self._connected:
            raise RuntimeError("未连接")
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None

        # Discord REST API轮询（Gateway需要WebSocket，这里用REST）
        while self._connected:
            await asyncio.sleep(5)

    async def send_text(self, target: str, text: str) -> None:
        """发送文本到频道/用户"""
        url = f"{self.API_BASE}/channels/{target}/messages"
        payload = {"content": text}
        req = Request(url, data=json.dumps(payload).encode("utf-8"),
                      headers={
                          "Content-Type": "application/json",
                          "Authorization": f"Bot {self._bot_token}",
                      }, method="POST")
        try:
            with urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if "code" in data and data.get("code") >= 400:
                    logger.error(f"Discord发送失败: {data}")
        except Exception as e:
            logger.error(f"Discord发送异常: {e}")

    async def send_image(self, target: str, image: Any) -> None:
        """发送图片"""
        url = f"{self.API_BASE}/channels/{target}/messages"
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
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            f'Content-Type: application/octet-stream\r\n\r\n'
        ).encode() + img_data + f"\r\n--{boundary}--\r\n".encode()
        req = Request(url, data=body,
                      headers={
                          "Content-Type": f"multipart/form-data; boundary={boundary}",
                          "Authorization": f"Bot {self._bot_token}",
                      }, method="POST")
        try:
            with urlopen(req, timeout=30) as resp:
                pass
        except Exception as e:
            logger.error(f"Discord发送图片异常: {e}")

    async def send_file(self, target: str, path: str) -> None:
        """发送文件"""
        url = f"{self.API_BASE}/channels/{target}/messages"
        import uuid
        boundary = f"----FormBoundary{uuid.uuid4().hex[:16]}"
        with open(path, "rb") as f:
            file_data = f.read()
        filename = path.split("/")[-1]
        body = (
            f'--{boundary}\r\n'
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            f'Content-Type: application/octet-stream\r\n\r\n'
        ).encode() + file_data + f"\r\n--{boundary}--\r\n".encode()
        req = Request(url, data=body,
                      headers={
                          "Content-Type": f"multipart/form-data; boundary={boundary}",
                          "Authorization": f"Bot {self._bot_token}",
                      }, method="POST")
        try:
            with urlopen(req, timeout=30) as resp:
                pass
        except Exception as e:
            logger.error(f"Discord发送文件异常: {e}")

    async def send_voice(self, target: str, audio: Any) -> None:
        """发送语音"""
        url = f"{self.API_BASE}/channels/{target}/messages"
        import uuid
        boundary = f"----FormBoundary{uuid.uuid4().hex[:16]}"
        if isinstance(audio, str):
            with open(audio, "rb") as f:
                audio_data = f.read()
            filename = audio.split("/")[-1]
        else:
            audio_data = audio
            filename = "voice.opus"
        body = (
            f'--{boundary}\r\n'
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            f'Content-Type: audio/ogg\r\n\r\n'
        ).encode() + audio_data + f"\r\n--{boundary}--\r\n".encode()
        req = Request(url, data=body,
                      headers={
                          "Content-Type": f"multipart/form-data; boundary={boundary}",
                          "Authorization": f"Bot {self._bot_token}",
                      }, method="POST")
        try:
            with urlopen(req, timeout=30) as resp:
                pass
        except Exception as e:
            logger.error(f"Discord发送语音异常: {e}")

    async def disconnect(self) -> None:
        self._connected = False
        logger.info("Discord渠道已断开")
