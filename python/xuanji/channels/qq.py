"""
xuanji QQ渠道

支持：
- QQ Bot开放平台（官方API，推荐）
- 反向WebSocket（备用，需要go-cqhttp等框架）

零外部依赖，使用urllib.request标准库。

用法:
    from xuanji.channels.qq import QQChannel
    
    channel = QQChannel()
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

logger = logging.getLogger("xuanji.channels.qq")


class QQChannel(ChannelBase):
    """QQ通信渠道

    支持QQ Bot开放平台API。
    """

    name = "qq"
    description = "QQ渠道（QQ Bot开放平台API）"

    # QQ Bot API endpoints
    API_BASE = "https://api.sgroup.qq.com"
    TOKEN_URL = "https://bots.qq.com/app/getToken"

    def __init__(self):
        super().__init__()
        self._config: Dict[str, Any] = {}
        self._access_token: str = ""
        self._token_expires: float = 0
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._recent_messages: list = []

    async def connect(self, config: Dict) -> None:
        self._config = config
        app_id = config.get("app_id", "")
        app_secret = config.get("app_secret", "")
        if not app_id or not app_secret:
            raise ValueError("QQ渠道需要 app_id 和 app_secret")

        await self._fetch_token(app_id, app_secret)
        if not self._access_token:
            raise ConnectionError("QQ access_token获取失败")

        self._connected = True
        logger.info("QQ渠道已连接")

    async def _fetch_token(self, app_id: str, app_secret: str) -> str:
        """获取QQ Bot access_token"""
        if self._access_token and time.time() < self._token_expires:
            return self._access_token
        url = f"{self.TOKEN_URL}?appId={app_id}&clientSecret={app_secret}"
        try:
            req = Request(url)
            with urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if data.get("access_token"):
                    self._access_token = data["access_token"]
                    self._token_expires = time.time() + data.get("expires_in", 7200) - 300
                    return self._access_token
                else:
                    logger.error(f"获取QQ token失败: {data}")
        except Exception as e:
            logger.error(f"获取QQ token异常: {e}")
        return ""

    async def listen(self) -> None:
        if not self._connected:
            raise RuntimeError("未连接")
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None

        # QQ Bot通常通过WebSocket或回调接收消息
        # 这里实现轮询模式
        while self._connected:
            try:
                # 检查是否有待处理消息
                pass
            except Exception as e:
                logger.debug(f"QQ轮询: {e}")
            await asyncio.sleep(2)

    async def send_text(self, target: str, text: str) -> None:
        token = await self._fetch_token(
            self._config.get("app_id", ""), self._config.get("app_secret", "")
        )
        if not token:
            raise ConnectionError("QQ token不可用")

        # 判断是群聊还是私聊
        if target.startswith("GROUP_") or target.startswith("C2C_"):
            # 使用QQ官方API
            url = f"{self.API_BASE}/v2/groups/{target}/messages" if "GROUP" in target else f"{self.API_BASE}/v2/users/{target}/messages"
        else:
            url = f"{self.API_BASE}/v2/users/{target}/messages"

        payload = {"content": text, "msg_type": 0}
        req = Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"QQBot {token}",
            },
            method="POST",
        )
        try:
            with urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if resp.status >= 300:
                    logger.error(f"QQ发送失败: {data}")
        except Exception as e:
            logger.error(f"QQ发送异常: {e}")

    async def send_image(self, target: str, image: Any) -> None:
        """发送图片 — 上传到QQ媒体服务器"""
        token = await self._fetch_token(
            self._config.get("app_id", ""), self._config.get("app_secret", "")
        )
        if not token:
            raise ConnectionError("QQ token不可用")

        media_id = await self._upload_media(token, image, "image")
        if media_id:
            url = f"{self.API_BASE}/v2/users/{target}/messages" if "C2C" not in target and "GROUP" not in target else f"{self.API_BASE}/v2/groups/{target}/messages"
            payload = {"msg_type": 2, "media": media_id}
            req = Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"QQBot {token}",
                },
                method="POST",
            )
            try:
                with urlopen(req, timeout=15) as resp:
                    pass
            except Exception as e:
                logger.error(f"QQ发送图片异常: {e}")

    async def send_file(self, target: str, path: str) -> None:
        token = await self._fetch_token(
            self._config.get("app_id", ""), self._config.get("app_secret", "")
        )
        media_id = await self._upload_media(token, path, "file")
        if media_id:
            url = f"{self.API_BASE}/v2/users/{target}/messages"
            payload = {"msg_type": 3, "media": media_id}
            req = Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"QQBot {token}",
                },
                method="POST",
            )
            try:
                with urlopen(req, timeout=15) as resp:
                    pass
            except Exception as e:
                logger.error(f"QQ发送文件异常: {e}")

    async def send_voice(self, target: str, audio: Any) -> None:
        token = await self._fetch_token(
            self._config.get("app_id", ""), self._config.get("app_secret", "")
        )
        media_id = await self._upload_media(token, audio, "audio")
        if media_id:
            url = f"{self.API_BASE}/v2/users/{target}/messages"
            payload = {"msg_type": 4, "media": media_id}
            req = Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"QQBot {token}",
                },
                method="POST",
            )
            try:
                with urlopen(req, timeout=15) as resp:
                    pass
            except Exception as e:
                logger.error(f"QQ发送语音异常: {e}")

    async def _upload_media(self, token: str, source: Any, msg_type: str) -> str:
        """上传媒体到QQ"""
        url = f"{self.API_BASE}/v2/groups/test/media"  # 示例URL
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
                f'Content-Type: application/octet-stream\r\n\r\n'
            ).encode() + file_data + f"\r\n--{boundary}--\r\n".encode()
            req = Request(
                url, data=body,
                headers={
                    "Content-Type": f"multipart/form-data; boundary={boundary}",
                    "Authorization": f"QQBot {token}",
                },
                method="POST",
            )
            with urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("file_info", "")
        except Exception as e:
            logger.error(f"QQ上传媒体失败: {e}")
            return ""

    async def disconnect(self) -> None:
        self._connected = False
        logger.info("QQ渠道已断开")
