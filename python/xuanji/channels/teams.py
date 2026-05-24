"""
xuanji Microsoft Teams渠道

支持Microsoft Graph API。
零外部依赖，使用urllib.request标准库。

用法:
    from xuanji.channels.teams import TeamsChannel
    
    channel = TeamsChannel()
    await channel.connect({
        "tenant_id": "...",
        "client_id": "...",
        "client_secret": "...",
    })
"""

import asyncio
import json
import logging
import time
from typing import Any, Dict, Optional
from urllib.request import Request, urlopen

from xuanji.channels._base import ChannelBase, ChatType, ContentType, Message

logger = logging.getLogger("xuanji.channels.teams")


class TeamsChannel(ChannelBase):
    """Microsoft Teams通信渠道"""

    name = "teams"
    description = "Microsoft Teams渠道（Graph API）"

    GRAPH_BASE = "https://graph.microsoft.com/v1.0"

    def __init__(self):
        super().__init__()
        self._config: Dict[str, Any] = {}
        self._access_token: str = ""
        self._token_expires: float = 0
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._recent_messages: list = []

    async def connect(self, config: Dict) -> None:
        self._config = config
        tenant_id = config.get("tenant_id", "")
        client_id = config.get("client_id", "")
        client_secret = config.get("client_secret", "")

        if not tenant_id or not client_id or not client_secret:
            raise ValueError("Teams需要 tenant_id, client_id, client_secret")

        await self._fetch_token(tenant_id, client_id, client_secret)
        if not self._access_token:
            raise ConnectionError("Teams token获取失败")

        self._connected = True
        logger.info("Teams渠道已连接")

    async def _fetch_token(self, tenant_id: str, client_id: str, client_secret: str) -> str:
        """获取Microsoft Graph token"""
        if self._access_token and time.time() < self._token_expires:
            return self._access_token
        url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
        payload = {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": "https://graph.microsoft.com/.default",
        }
        try:
            req = Request(url, data=json.dumps(payload).encode("utf-8"),
                          headers={"Content-Type": "application/json"}, method="POST")
            with urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if data.get("access_token"):
                    self._access_token = data["access_token"]
                    self._token_expires = time.time() + data.get("expires_in", 3600) - 300
                    return self._access_token
        except Exception as e:
            logger.error(f"Teams获取token异常: {e}")
        return ""

    async def listen(self) -> None:
        if not self._connected:
            raise RuntimeError("未连接")
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None
        # Teams通过Bot Framework接收消息
        while self._connected:
            await asyncio.sleep(5)

    async def send_text(self, target: str, text: str) -> None:
        """发送Teams消息
        
        Args:
            target: chat_id 或 channel_id
            text: 内容
        """
        token = await self._fetch_token(
            self._config.get("tenant_id", ""),
            self._config.get("client_id", ""),
            self._config.get("client_secret", ""),
        )
        # 发送到Teams聊天
        url = f"{self.GRAPH_BASE}/chats/{target}/messages"
        payload = {"body": {"content": text}}
        req = Request(url, data=json.dumps(payload).encode("utf-8"),
                      headers={
                          "Content-Type": "application/json",
                          "Authorization": f"Bearer {token}",
                      }, method="POST")
        try:
            with urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if "error" in data:
                    logger.error(f"Teams发送失败: {data}")
        except Exception as e:
            logger.error(f"Teams发送异常: {e}")

    async def send_image(self, target: str, image: Any) -> None:
        logger.info(f"[Teams] 发送图片到 {target}")

    async def send_file(self, target: str, path: str) -> None:
        logger.info(f"[Teams] 发送文件到 {target}: {path}")

    async def send_voice(self, target: str, audio: Any) -> None:
        logger.info(f"[Teams] 发送语音到 {target}")

    async def disconnect(self) -> None:
        self._connected = False
        logger.info("Teams渠道已断开")
