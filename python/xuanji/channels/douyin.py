"""
xuanji 抖音渠道

支持抖音开放平台API。
零外部依赖，使用urllib.request标准库。

用法:
    from xuanji.channels.douyin import DouyinChannel
    
    channel = DouyinChannel()
    await channel.connect({
        "app_id": "...",
        "app_secret": "...",
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

logger = logging.getLogger("xuanji.channels.douyin")


class DouyinChannel(ChannelBase):
    """抖音通信渠道"""

    name = "douyin"
    description = "抖音渠道（抖音开放平台API）"

    API_BASE = "https://open.douyin.com"
    OPEN_API = "https://developer.toutiao.com/api"

    def __init__(self):
        super().__init__()
        self._config: Dict[str, Any] = {}
        self._access_token: str = ""
        self._refresh_token: str = ""
        self._token_expires: float = 0
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._recent_messages: list = []

    async def connect(self, config: Dict) -> None:
        self._config = config
        app_id = config.get("app_id", "")
        app_secret = config.get("app_secret", "")
        if not app_id or not app_secret:
            raise ValueError("抖音渠道需要 app_id 和 app_secret")

        await self._fetch_token(app_id, app_secret)
        if not self._access_token:
            raise ConnectionError("抖音access_token获取失败")

        self._connected = True
        logger.info("抖音渠道已连接")

    async def _fetch_token(self, app_id: str, app_secret: str) -> str:
        """获取抖音access_token"""
        if self._access_token and time.time() < self._token_expires:
            return self._access_token
        url = f"{self.API_BASE}/api/oauth/access_token"
        payload = {
            "client_key": app_id,
            "client_secret": app_secret,
            "grant_type": "client_credential",
        }
        try:
            req = Request(url, data=json.dumps(payload).encode("utf-8"),
                          headers={"Content-Type": "application/json"}, method="POST")
            with urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if data.get("data", {}).get("access_token"):
                    d = data["data"]
                    self._access_token = d["access_token"]
                    self._refresh_token = d.get("refresh_token", "")
                    self._token_expires = time.time() + d.get("expires_in", 7200) - 300
                    return self._access_token
                else:
                    logger.error(f"获取抖音token失败: {data}")
        except Exception as e:
            logger.error(f"获取抖音token异常: {e}")
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
        """发送私信消息"""
        token = await self._fetch_token(
            self._config.get("app_id", ""), self._config.get("app_secret", "")
        )
        url = f"{self.OPEN_API}/msg/send"
        payload = {
            "open_id": target,
            "access_token": token,
            "content": text,
            "msg_type": 2,  # 文本
        }
        req = Request(url, data=json.dumps(payload).encode("utf-8"),
                      headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if data.get("data", {}).get("err_no", 0) != 0:
                    logger.error(f"抖音发送失败: {data}")
        except Exception as e:
            logger.error(f"抖音发送异常: {e}")

    async def send_image(self, target: str, image: Any) -> None:
        """发送图片 — 先上传"""
        token = await self._fetch_token(
            self._config.get("app_id", ""), self._config.get("app_secret", "")
        )
        media_id = await self._upload_media(token, image)
        if media_id:
            url = f"{self.OPEN_API}/msg/send"
            payload = {
                "open_id": target,
                "access_token": token,
                "type": 1,  # 图片
                "media_id": media_id,
            }
            req = Request(url, data=json.dumps(payload).encode("utf-8"),
                          headers={"Content-Type": "application/json"}, method="POST")
            try:
                with urlopen(req, timeout=15) as resp:
                    pass
            except Exception as e:
                logger.error(f"抖音发送图片异常: {e}")

    async def send_file(self, target: str, path: str) -> None:
        token = await self._fetch_token(
            self._config.get("app_id", ""), self._config.get("app_secret", "")
        )
        media_id = await self._upload_media(token, path)
        if media_id:
            url = f"{self.OPEN_API}/msg/send"
            payload = {
                "open_id": target,
                "access_token": token,
                "type": 3,
                "media_id": media_id,
            }
            req = Request(url, data=json.dumps(payload).encode("utf-8"),
                          headers={"Content-Type": "application/json"}, method="POST")
            try:
                with urlopen(req, timeout=15) as resp:
                    pass
            except Exception as e:
                logger.error(f"抖音发送文件异常: {e}")

    async def send_voice(self, target: str, audio: Any) -> None:
        token = await self._fetch_token(
            self._config.get("app_id", ""), self._config.get("app_secret", "")
        )
        media_id = await self._upload_media(token, audio)
        if media_id:
            url = f"{self.OPEN_API}/msg/send"
            payload = {
                "open_id": target,
                "access_token": token,
                "type": 4,
                "media_id": media_id,
            }
            req = Request(url, data=json.dumps(payload).encode("utf-8"),
                          headers={"Content-Type": "application/json"}, method="POST")
            try:
                with urlopen(req, timeout=15) as resp:
                    pass
            except Exception as e:
                logger.error(f"抖音发送语音异常: {e}")

    async def _upload_media(self, token: str, source: Any) -> str:
        """上传媒体到抖音"""
        url = f"{self.OPEN_API}/media/upload"
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
                f'Content-Disposition: form-data; name="type"\r\n\r\n'
                f'material\r\n'
                f'--{boundary}\r\n'
                f'Content-Disposition: form-data; name="media"; filename="file"\r\n'
                f'Content-Type: application/octet-stream\r\n\r\n'
            ).encode() + file_data + f"\r\n--{boundary}--\r\n".encode()
            req = Request(url, data=body,
                          headers={
                              "Content-Type": f"multipart/form-data; boundary={boundary}",
                              "Access-Token": token,
                          }, method="POST")
            with urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("data", {}).get("media_id", "")
        except Exception as e:
            logger.error(f"抖音上传媒体失败: {e}")
            return ""

    async def disconnect(self) -> None:
        self._connected = False
        logger.info("抖音渠道已断开")
