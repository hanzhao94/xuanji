"""
xuanji Mattermost渠道

支持Mattermost REST API。
零外部依赖，使用urllib.request标准库。

用法:
    from xuanji.channels.mattermost import MattermostChannel
    
    channel = MattermostChannel()
    await channel.connect({
        "server_url": "https://mattermost.example.com",
        "token": "...",
    })
"""

import asyncio
import json
import logging
import time
from typing import Any, Dict, Optional
from urllib.request import Request, urlopen

from xuanji.channels._base import ChannelBase, ChatType, ContentType, Message

logger = logging.getLogger("xuanji.channels.mattermost")


class MattermostChannel(ChannelBase):
    """Mattermost通信渠道"""

    name = "mattermost"
    description = "Mattermost渠道（REST API）"

    def __init__(self):
        super().__init__()
        self._config: Dict[str, Any] = {}
        self._server_url: str = ""
        self._token: str = ""
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._recent_messages: list = []

    async def connect(self, config: Dict) -> None:
        self._config = config
        self._server_url = config.get("server_url", "").rstrip("/")
        self._token = config.get("token", "")

        if not self._server_url or not self._token:
            raise ValueError("Mattermost需要 server_url 和 token")

        self._connected = True
        logger.info(f"Mattermost渠道已连接 ({self._server_url})")

    async def listen(self) -> None:
        if not self._connected:
            raise RuntimeError("未连接")
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None
        # Mattermost通过WebSocket或Webhook接收消息
        while self._connected:
            await asyncio.sleep(5)

    async def send_text(self, target: str, text: str) -> None:
        """发送消息到频道/DM
        
        Args:
            target: channel_id
            text: 内容
        """
        url = f"{self._server_url}/api/v4/posts"
        payload = {
            "channel_id": target,
            "message": text,
        }
        req = Request(url, data=json.dumps(payload).encode("utf-8"),
                      headers={
                          "Content-Type": "application/json",
                          "Authorization": f"Bearer {self._token}",
                      }, method="POST")
        try:
            with urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if resp.status >= 400:
                    logger.error(f"Mattermost发送失败: {data}")
        except Exception as e:
            logger.error(f"Mattermost发送异常: {e}")

    async def send_image(self, target: str, image: Any) -> None:
        """发送图片 — 先上传"""
        file_id = await self._upload_file(target, image, "image/jpeg")
        if file_id:
            url = f"{self._server_url}/api/v4/posts"
            payload = {
                "channel_id": target,
                "message": "",
                "file_ids": [file_id],
            }
            req = Request(url, data=json.dumps(payload).encode("utf-8"),
                          headers={
                              "Content-Type": "application/json",
                              "Authorization": f"Bearer {self._token}",
                          }, method="POST")
            try:
                with urlopen(req, timeout=15) as resp:
                    pass
            except Exception as e:
                logger.error(f"Mattermost发送图片异常: {e}")

    async def send_file(self, target: str, path: str) -> None:
        file_id = await self._upload_file(target, path, "application/octet-stream")
        if file_id:
            url = f"{self._server_url}/api/v4/posts"
            payload = {
                "channel_id": target,
                "message": "",
                "file_ids": [file_id],
            }
            req = Request(url, data=json.dumps(payload).encode("utf-8"),
                          headers={
                              "Content-Type": "application/json",
                              "Authorization": f"Bearer {self._token}",
                          }, method="POST")
            try:
                with urlopen(req, timeout=15) as resp:
                    pass
            except Exception as e:
                logger.error(f"Mattermost发送文件异常: {e}")

    async def send_voice(self, target: str, audio: Any) -> None:
        file_id = await self._upload_file(target, audio, "audio/ogg")
        if file_id:
            url = f"{self._server_url}/api/v4/posts"
            payload = {
                "channel_id": target,
                "message": "",
                "file_ids": [file_id],
            }
            req = Request(url, data=json.dumps(payload).encode("utf-8"),
                          headers={
                              "Content-Type": "application/json",
                              "Authorization": f"Bearer {self._token}",
                          }, method="POST")
            try:
                with urlopen(req, timeout=15) as resp:
                    pass
            except Exception as e:
                logger.error(f"Mattermost发送语音异常: {e}")

    async def _upload_file(self, channel_id: str, source: Any, mime_type: str) -> str:
        """上传文件到Mattermost"""
        url = f"{self._server_url}/api/v4/files"
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
                f'Content-Disposition: form-data; name="channel_id"\r\n\r\n'
                f'{channel_id}\r\n'
                f'--{boundary}\r\n'
                f'Content-Disposition: form-data; name="foo"; filename="file"\r\n'
                f'Content-Type: {mime_type}\r\n\r\n'
            ).encode() + file_data + f"\r\n--{boundary}--\r\n".encode()
            req = Request(url, data=body,
                          headers={
                              "Content-Type": f"multipart/form-data; boundary={boundary}",
                              "Authorization": f"Bearer {self._token}",
                          }, method="POST")
            with urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                file_infos = data.get("file_infos", [])
                return file_infos[0]["id"] if file_infos else ""
        except Exception as e:
            logger.error(f"Mattermost上传文件失败: {e}")
            return ""

    async def disconnect(self) -> None:
        self._connected = False
        logger.info("Mattermost渠道已断开")
