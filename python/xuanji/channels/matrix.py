"""
xuanji Matrix渠道

支持Matrix Client-Server API (v1.6+)。
零外部依赖，使用urllib.request标准库。

用法:
    from xuanji.channels.matrix import MatrixChannel
    
    channel = MatrixChannel()
    await channel.connect({
        "homeserver": "https://matrix-client.matrix.org",
        "user_id": "@bot:matrix.org",
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

logger = logging.getLogger("xuanji.channels.matrix")


class MatrixChannel(ChannelBase):
    """Matrix通信渠道"""

    name = "matrix"
    description = "Matrix渠道（Client-Server API）"

    def __init__(self):
        super().__init__()
        self._config: Dict[str, Any] = {}
        self._homeserver: str = ""
        self._access_token: str = ""
        self._user_id: str = ""
        self._next_batch: str = ""
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._recent_messages: list = []

    async def connect(self, config: Dict) -> None:
        self._config = config
        self._homeserver = config.get("homeserver", "https://matrix-client.matrix.org")
        self._access_token = config.get("access_token", "")
        self._user_id = config.get("user_id", "")

        if not self._access_token:
            raise ValueError("Matrix需要 access_token")

        self._connected = True
        logger.info(f"Matrix渠道已连接 ({self._homeserver})")

    async def listen(self) -> None:
        if not self._connected:
            raise RuntimeError("未连接")
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None

        # 同步轮询
        while self._connected:
            try:
                url = f"{self._homeserver}/_matrix/client/v3/sync"
                params = f"?access_token={self._access_token}"
                if self._next_batch:
                    params += f"&since={self._next_batch}"
                params += "&timeout=30000"

                req = Request(url + params)
                with urlopen(req, timeout=35) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    self._next_batch = data.get("next_batch", "")
                    rooms = data.get("rooms", {})
                    for room_id, room_data in rooms.get("join", {}).items():
                        for event in room_data.get("timeline", {}).get("events", []):
                            self._process_event(event, room_id)
            except Exception as e:
                logger.debug(f"Matrix同步异常: {e}")
                await asyncio.sleep(2)

    def _process_event(self, event: Dict, room_id: str) -> None:
        """处理Matrix事件"""
        try:
            if event.get("type") != "m.room.message":
                return

            content = event.get("content", {})
            msg_type = content.get("msgtype", "m.text")

            ct = ContentType.TEXT
            text = ""
            media_url = ""

            if msg_type == "m.text":
                text = content.get("body", "")
            elif msg_type == "m.image":
                ct = ContentType.IMAGE
                media_url = content.get("url", "")
            elif msg_type == "m.audio":
                ct = ContentType.AUDIO
                media_url = content.get("url", "")
            elif msg_type == "m.video":
                ct = ContentType.VIDEO
                media_url = content.get("url", "")
            elif msg_type == "m.file":
                ct = ContentType.FILE
                text = content.get("body", "")
                media_url = content.get("url", "")
            elif msg_type == "m.sticker":
                ct = ContentType.STICKER
                media_url = content.get("url", "")

            message_msg = Message(
                channel="matrix",
                sender=event.get("sender", ""),
                sender_name="",
                chat_id=room_id,
                chat_type=ChatType.GROUP,
                content_type=ct,
                content=text,
                media_url=media_url,
                reply_to=event.get("event_id", ""),
                timestamp=time.time(),
                raw=event,
            )
            self._recent_messages.append(message_msg)
            if self._loop:
                asyncio.run_coroutine_threadsafe(self.emit("message", message_msg), self._loop)
        except Exception as e:
            logger.error(f"处理Matrix事件异常: {e}")

    async def send_text(self, target: str, text: str) -> None:
        """发送消息到Matrix房间"""
        url = f"{self._homeserver}/_matrix/client/v3/rooms/{target}/send/m.room.message"
        payload = {
            "msgtype": "m.text",
            "body": text,
        }
        req = Request(url, data=json.dumps(payload).encode("utf-8"),
                      headers={"Content-Type": "application/json"}, method="PUT")
        req.add_header("Authorization", f"Bearer {self._access_token}")
        try:
            with urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if resp.status >= 400:
                    logger.error(f"Matrix发送失败: {data}")
        except Exception as e:
            logger.error(f"Matrix发送异常: {e}")

    async def send_image(self, target: str, image: Any) -> None:
        """发送图片 — 先上传到Matrix媒体服务器"""
        mxc_url = await self._upload_media(image, "image/jpeg")
        if mxc_url:
            url = f"{self._homeserver}/_matrix/client/v3/rooms/{target}/send/m.room.message"
            payload = {
                "msgtype": "m.image",
                "body": "image",
                "url": mxc_url,
            }
            req = Request(url, data=json.dumps(payload).encode("utf-8"),
                          headers={"Content-Type": "application/json"}, method="PUT")
            req.add_header("Authorization", f"Bearer {self._access_token}")
            try:
                with urlopen(req, timeout=15) as resp:
                    pass
            except Exception as e:
                logger.error(f"Matrix发送图片异常: {e}")

    async def send_file(self, target: str, path: str) -> None:
        mxc_url = await self._upload_media(path, "application/octet-stream")
        if mxc_url:
            url = f"{self._homeserver}/_matrix/client/v3/rooms/{target}/send/m.room.message"
            payload = {
                "msgtype": "m.file",
                "body": path.split("/")[-1],
                "url": mxc_url,
            }
            req = Request(url, data=json.dumps(payload).encode("utf-8"),
                          headers={"Content-Type": "application/json"}, method="PUT")
            req.add_header("Authorization", f"Bearer {self._access_token}")
            try:
                with urlopen(req, timeout=15) as resp:
                    pass
            except Exception as e:
                logger.error(f"Matrix发送文件异常: {e}")

    async def send_voice(self, target: str, audio: Any) -> None:
        mxc_url = await self._upload_media(audio, "audio/ogg")
        if mxc_url:
            url = f"{self._homeserver}/_matrix/client/v3/rooms/{target}/send/m.room.message"
            payload = {
                "msgtype": "m.audio",
                "body": "audio",
                "url": mxc_url,
            }
            req = Request(url, data=json.dumps(payload).encode("utf-8"),
                          headers={"Content-Type": "application/json"}, method="PUT")
            req.add_header("Authorization", f"Bearer {self._access_token}")
            try:
                with urlopen(req, timeout=15) as resp:
                    pass
            except Exception as e:
                logger.error(f"Matrix发送语音异常: {e}")

    async def _upload_media(self, source: Any, mime_type: str) -> str:
        """上传媒体到Matrix"""
        url = f"{self._homeserver}/_matrix/media/v3/upload"
        try:
            if isinstance(source, str):
                with open(source, "rb") as f:
                    file_data = f.read()
            else:
                file_data = source
            req = Request(url, data=file_data,
                          headers={
                              "Content-Type": mime_type,
                              "Authorization": f"Bearer {self._access_token}",
                          }, method="POST")
            with urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("content_uri", "")
        except Exception as e:
            logger.error(f"Matrix上传媒体失败: {e}")
            return ""

    async def disconnect(self) -> None:
        self._connected = False
        logger.info("Matrix渠道已断开")
