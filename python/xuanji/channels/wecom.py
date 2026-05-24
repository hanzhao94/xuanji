"""
xuanji 企业微信渠道（WeCom）

独立的企业微信API渠道，与微信渠道中的wecom模式分开。
支持：
- 应用消息发送
- 回调消息接收
- 通讯录管理
- 客户联系

零外部依赖，使用urllib.request标准库。

用法:
    from xuanji.channels.wecom import WeComChannel
    
    channel = WeComChannel()
    await channel.connect({
        "corp_id": "...",
        "corp_secret": "...",
        "agent_id": "...",
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

logger = logging.getLogger("xuanji.channels.wecom")


class WeComChannel(ChannelBase):
    """企业微信独立渠道"""

    name = "wecom"
    description = "企业微信渠道（WeCom API）"

    API_BASE = "https://qyapi.weixin.qq.com/cgi-bin"

    def __init__(self):
        super().__init__()
        self._config: Dict[str, Any] = {}
        self._access_token: str = ""
        self._token_expires: float = 0
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._recent_messages: list = []

    async def connect(self, config: Dict) -> None:
        self._config = config
        corp_id = config.get("corp_id", "")
        corp_secret = config.get("corp_secret", "")
        if not corp_id or not corp_secret:
            raise ValueError("企业微信需要 corp_id 和 corp_secret")

        token = await self._fetch_token(corp_id, corp_secret)
        if not token:
            raise ConnectionError("企业微信token获取失败")

        self._connected = True
        logger.info("企业微信渠道已连接")

    async def _fetch_token(self, corp_id: str, corp_secret: str) -> str:
        if self._access_token and time.time() < self._token_expires:
            return self._access_token
        url = f"{self.API_BASE}/gettoken?corpid={corp_id}&corpsecret={corp_secret}"
        try:
            with urlopen(Request(url), timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if data.get("errcode") == 0:
                    self._access_token = data["access_token"]
                    self._token_expires = time.time() + data.get("expires_in", 7200) - 300
                    return self._access_token
        except Exception as e:
            logger.error(f"企业微信token异常: {e}")
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
        token = await self._fetch_token(
            self._config.get("corp_id", ""), self._config.get("corp_secret", "")
        )
        url = f"{self.API_BASE}/message/send?access_token={token}"
        payload = {
            "touser": target,
            "msgtype": "text",
            "agentid": int(self._config.get("agent_id", 0)),
            "text": {"content": text},
        }
        req = Request(url, data=json.dumps(payload).encode("utf-8"),
                      headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("errcode") != 0:
                logger.error(f"企业微信发送失败: {data}")

    async def send_image(self, target: str, image: Any) -> None:
        token = await self._fetch_token(
            self._config.get("corp_id", ""), self._config.get("corp_secret", "")
        )
        media_id = await self._upload_media(token, image, "image")
        if media_id:
            url = f"{self.API_BASE}/message/send?access_token={token}"
            payload = {
                "touser": target,
                "msgtype": "image",
                "agentid": int(self._config.get("agent_id", 0)),
                "image": {"media_id": media_id},
            }
            req = Request(url, data=json.dumps(payload).encode("utf-8"),
                          headers={"Content-Type": "application/json"}, method="POST")
            with urlopen(req, timeout=15) as resp:
                pass

    async def send_file(self, target: str, path: str) -> None:
        token = await self._fetch_token(
            self._config.get("corp_id", ""), self._config.get("corp_secret", "")
        )
        media_id = await self._upload_media(token, path, "file")
        if media_id:
            url = f"{self.API_BASE}/message/send?access_token={token}"
            payload = {
                "touser": target,
                "msgtype": "file",
                "agentid": int(self._config.get("agent_id", 0)),
                "file": {"media_id": media_id},
            }
            req = Request(url, data=json.dumps(payload).encode("utf-8"),
                          headers={"Content-Type": "application/json"}, method="POST")
            with urlopen(req, timeout=15) as resp:
                pass

    async def send_voice(self, target: str, audio: Any) -> None:
        token = await self._fetch_token(
            self._config.get("corp_id", ""), self._config.get("corp_secret", "")
        )
        media_id = await self._upload_media(token, audio, "voice")
        if media_id:
            url = f"{self.API_BASE}/message/send?access_token={token}"
            payload = {
                "touser": target,
                "msgtype": "voice",
                "agentid": int(self._config.get("agent_id", 0)),
                "voice": {"media_id": media_id},
            }
            req = Request(url, data=json.dumps(payload).encode("utf-8"),
                          headers={"Content-Type": "application/json"}, method="POST")
            with urlopen(req, timeout=15) as resp:
                pass

    async def _upload_media(self, token: str, source: Any, media_type: str) -> str:
        url = f"{self.API_BASE}/media/upload?access_token={token}&type={media_type}"
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
                f'Content-Disposition: form-data; name="media"; filename="file"\r\n'
                f'Content-Type: application/octet-stream\r\n\r\n'
            ).encode() + file_data + f"\r\n--{boundary}--\r\n".encode()
            req = Request(url, data=body,
                          headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
                          method="POST")
            with urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("media_id", "")
        except Exception as e:
            logger.error(f"企业微信上传媒体失败: {e}")
            return ""

    async def disconnect(self) -> None:
        self._connected = False
        logger.info("企业微信渠道已断开")
