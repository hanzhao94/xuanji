"""
xuanji 微博渠道

支持微博开放平台API。
零外部依赖，使用urllib.request标准库。

用法:
    from xuanji.channels.weibo import WeiboChannel
    
    channel = WeiboChannel()
    await channel.connect({
        "app_key": "...",
        "app_secret": "...",
        "access_token": "...",
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

logger = logging.getLogger("xuanji.channels.weibo")


class WeiboChannel(ChannelBase):
    """微博通信渠道"""

    name = "weibo"
    description = "微博渠道（Weibo Open API）"

    API_BASE = "https://api.weibo.com/2"

    def __init__(self):
        super().__init__()
        self._config: Dict[str, Any] = {}
        self._access_token: str = ""
        self._uid: str = ""
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._recent_messages: list = []

    async def connect(self, config: Dict) -> None:
        self._config = config
        token = config.get("access_token", "")
        if not token:
            # 尝试OAuth获取
            token = await self._oauth_token()
        if not token:
            raise ValueError("微博渠道需要 access_token")

        self._access_token = token
        self._uid = config.get("uid", "")
        self._connected = True
        logger.info("微博渠道已连接")

    async def _oauth_token(self) -> str:
        """OAuth2获取token"""
        app_key = self._config.get("app_key", "")
        app_secret = self._config.get("app_secret", "")
        if not app_key or not app_secret:
            return ""
        url = f"https://api.weibo.com/oauth2/access_token"
        payload = {
            "client_id": app_key,
            "client_secret": app_secret,
            "grant_type": "client_credentials",
        }
        try:
            req = Request(url, data=json.dumps(payload).encode("utf-8"),
                          headers={"Content-Type": "application/json"}, method="POST")
            with urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("access_token", "")
        except Exception as e:
            logger.error(f"微博OAuth异常: {e}")
            return ""

    async def listen(self) -> None:
        if not self._connected:
            raise RuntimeError("未连接")
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None
        while self._connected:
            await asyncio.sleep(5)

    async def send_text(self, target: str, text: str) -> None:
        """发送微博/私信"""
        # 微博私信
        url = f"{self.API_BASE}/messages/send.json"
        payload = {
            "access_token": self._access_token,
            "uid": target,
            "text": text,
        }
        req = Request(url, data=json.dumps(payload).encode("utf-8"),
                      headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if "error_code" in data:
                    logger.error(f"微博发送失败: {data}")
        except Exception as e:
            logger.error(f"微博发送异常: {e}")

    async def send_image(self, target: str, image: Any) -> None:
        """发送图片微博"""
        url = f"{self.API_BASE}/statuses/upload.json"
        try:
            if isinstance(image, str):
                with open(image, "rb") as f:
                    img_data = f.read()
            else:
                img_data = image
            import uuid
            boundary = f"----FormBoundary{uuid.uuid4().hex[:16]}"
            body = (
                f'--{boundary}\r\n'
                f'Content-Disposition: form-data; name="access_token"\r\n\r\n'
                f'{self._access_token}\r\n'
                f'--{boundary}\r\n'
                f'Content-Disposition: form-data; name="status"\r\n\r\n'
                f'{target}\r\n'
                f'--{boundary}\r\n'
                f'Content-Disposition: form-data; name="pic"; filename="image.jpg"\r\n'
                f'Content-Type: image/jpeg\r\n\r\n'
            ).encode() + img_data + f"\r\n--{boundary}--\r\n".encode()
            req = Request(url, data=body,
                          headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
                          method="POST")
            with urlopen(req, timeout=30) as resp:
                pass
        except Exception as e:
            logger.error(f"微博发送图片异常: {e}")

    async def send_file(self, target: str, path: str) -> None:
        logger.info(f"[微博] 发送文件到 {target}: {path}")

    async def send_voice(self, target: str, audio: Any) -> None:
        logger.info(f"[微博] 发送语音到 {target}")

    async def disconnect(self) -> None:
        self._connected = False
        logger.info("微博渠道已断开")
