"""
xuanji Telegram渠道

支持Telegram Bot API。
零外部依赖，使用urllib.request标准库。

用法:
    from xuanji.channels.telegram import TelegramChannel
    
    channel = TelegramChannel()
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

logger = logging.getLogger("xuanji.channels.telegram")


class TelegramChannel(ChannelBase):
    """Telegram通信渠道"""

    name = "telegram"
    description = "Telegram渠道（Bot API）"

    def __init__(self):
        super().__init__()
        self._config: Dict[str, Any] = {}
        self._bot_token: str = ""
        self._api_url: str = ""
        self._offset: int = 0
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._recent_messages: list = []

    async def connect(self, config: Dict) -> None:
        self._config = config
        self._bot_token = config.get("bot_token", "")
        if not self._bot_token:
            raise ValueError("Telegram渠道需要 bot_token")

        self._api_url = f"https://api.telegram.org/bot{self._bot_token}"

        # 验证token
        try:
            req = Request(f"{self._api_url}/getMe")
            with urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if not data.get("ok"):
                    raise ValueError(f"Telegram token无效: {data}")
                bot_info = data["result"]
                logger.info(f"Telegram Bot已连接: @{bot_info.get('username', 'unknown')}")
        except Exception as e:
            if "token无效" in str(e):
                raise
            logger.warning(f"Telegram getMe失败: {e}")

        self._connected = True

    async def listen(self) -> None:
        if not self._connected:
            raise RuntimeError("未连接")
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None

        # 长轮询获取更新
        while self._connected:
            try:
                url = f"{self._api_url}/getUpdates?offset={self._offset}&timeout=30&allowed_updates=[\"message\",\"channel_post\",\"callback_query\"]"
                req = Request(url)
                with urlopen(req, timeout=35) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    if data.get("ok"):
                        for update in data["result"]:
                            self._offset = update["update_id"] + 1
                            self._process_update(update)
            except Exception as e:
                logger.debug(f"Telegram轮询异常: {e}")
                await asyncio.sleep(2)

    def _process_update(self, update: Dict) -> None:
        """处理Telegram更新"""
        try:
            msg_data = update.get("message") or update.get("edited_message")
            if not msg_data:
                return

            chat = msg_data.get("chat", {})
            chat_type = chat.get("type", "private")

            if chat_type == "private":
                ct = ChatType.PRIVATE
            elif chat_type in ("group", "supergroup", "channel"):
                ct = ChatType.GROUP
            else:
                ct = ChatType.PRIVATE

            from_user = msg_data.get("from", {})
            content_type = ContentType.TEXT
            content = ""
            media_url = ""

            if "text" in msg_data:
                content = msg_data["text"]
            elif "photo" in msg_data:
                content_type = ContentType.IMAGE
                photos = msg_data["photo"]
                if photos:
                    media_url = photos[-1].get("file_id", "")
            elif "voice" in msg_data:
                content_type = ContentType.AUDIO
                media_url = msg_data["voice"].get("file_id", "")
            elif "video" in msg_data:
                content_type = ContentType.VIDEO
                media_url = msg_data["video"].get("file_id", "")
            elif "document" in msg_data:
                content_type = ContentType.FILE
                media_url = msg_data["document"].get("file_id", "")
            elif "sticker" in msg_data:
                content_type = ContentType.STICKER
                media_url = msg_data["sticker"].get("file_id", "")
            elif "location" in msg_data:
                content_type = ContentType.LOCATION
                loc = msg_data["location"]
                content = f"Location: ({loc.get('latitude', 0)}, {loc.get('longitude', 0)})"
            elif "audio" in msg_data:
                content_type = ContentType.AUDIO
                media_url = msg_data["audio"].get("file_id", "")

            msg = Message(
                channel="telegram",
                sender=str(from_user.get("id", "")),
                sender_name=from_user.get("first_name", "") + " " + from_user.get("last_name", ""),
                chat_id=str(chat.get("id", "")),
                chat_type=ct,
                content_type=content_type,
                content=content,
                media_url=media_url,
                reply_to=str(msg_data.get("reply_to_message", {}).get("message_id", "")),
                timestamp=time.time(),
                raw=update,
            )
            self._recent_messages.append(msg)
            if self._loop:
                asyncio.run_coroutine_threadsafe(self.emit("message", msg), self._loop)
        except Exception as e:
            logger.error(f"处理Telegram更新异常: {e}")

    async def send_text(self, target: str, text: str) -> None:
        url = f"{self._api_url}/sendMessage"
        payload = {"chat_id": target, "text": text}
        req = Request(url, data=json.dumps(payload).encode("utf-8"),
                      headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if not data.get("ok"):
                    logger.error(f"Telegram发送失败: {data}")
        except Exception as e:
            logger.error(f"Telegram发送异常: {e}")

    async def send_image(self, target: str, image: Any) -> None:
        """发送图片"""
        url = f"{self._api_url}/sendPhoto"
        if isinstance(image, str) and image.startswith(("http://", "https://")):
            payload = {"chat_id": target, "photo": image}
            req = Request(url, data=json.dumps(payload).encode("utf-8"),
                          headers={"Content-Type": "application/json"}, method="POST")
        else:
            # 上传本地图片
            import uuid
            boundary = f"----FormBoundary{uuid.uuid4().hex[:16]}"
            if isinstance(image, str):
                with open(image, "rb") as f:
                    img_data = f.read()
            else:
                img_data = image
            body = (
                f'--{boundary}\r\n'
                f'Content-Disposition: form-data; name="chat_id"\r\n\r\n'
                f'{target}\r\n'
                f'--{boundary}\r\n'
                f'Content-Disposition: form-data; name="photo"; filename="photo.jpg"\r\n'
                f'Content-Type: image/jpeg\r\n\r\n'
            ).encode() + img_data + f"\r\n--{boundary}--\r\n".encode()
            req = Request(url, data=body,
                          headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
                          method="POST")
        try:
            with urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if not data.get("ok"):
                    logger.error(f"Telegram发送图片失败: {data}")
        except Exception as e:
            logger.error(f"Telegram发送图片异常: {e}")

    async def send_file(self, target: str, path: str) -> None:
        url = f"{self._api_url}/sendDocument"
        import uuid
        boundary = f"----FormBoundary{uuid.uuid4().hex[:16]}"
        with open(path, "rb") as f:
            file_data = f.read()
        body = (
            f'--{boundary}\r\n'
            f'Content-Disposition: form-data; name="chat_id"\r\n\r\n'
            f'{target}\r\n'
            f'--{boundary}\r\n'
            f'Content-Disposition: form-data; name="document"; filename="{path.split("/")[-1]}"\r\n'
            f'Content-Type: application/octet-stream\r\n\r\n'
        ).encode() + file_data + f"\r\n--{boundary}--\r\n".encode()
        req = Request(url, data=body,
                      headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
                      method="POST")
        try:
            with urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if not data.get("ok"):
                    logger.error(f"Telegram发送文件失败: {data}")
        except Exception as e:
            logger.error(f"Telegram发送文件异常: {e}")

    async def send_voice(self, target: str, audio: Any) -> None:
        url = f"{self._api_url}/sendVoice"
        import uuid
        boundary = f"----FormBoundary{uuid.uuid4().hex[:16]}"
        if isinstance(audio, str):
            with open(audio, "rb") as f:
                audio_data = f.read()
        else:
            audio_data = audio
        body = (
            f'--{boundary}\r\n'
            f'Content-Disposition: form-data; name="chat_id"\r\n\r\n'
            f'{target}\r\n'
            f'--{boundary}\r\n'
            f'Content-Disposition: form-data; name="voice"; filename="voice.ogg"\r\n'
            f'Content-Type: audio/ogg\r\n\r\n'
        ).encode() + audio_data + f"\r\n--{boundary}--\r\n".encode()
        req = Request(url, data=body,
                      headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
                      method="POST")
        try:
            with urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if not data.get("ok"):
                    logger.error(f"Telegram发送语音失败: {data}")
        except Exception as e:
            logger.error(f"Telegram发送语音异常: {e}")

    async def disconnect(self) -> None:
        self._connected = False
        logger.info("Telegram渠道已断开")
