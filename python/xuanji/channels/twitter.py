"""
xuanji Twitter/X渠道

支持X API v2。
零外部依赖，使用urllib.request标准库。

用法:
    from xuanji.channels.twitter import TwitterChannel
    
    channel = TwitterChannel()
    await channel.connect({
        "bearer_token": "...",
        "api_key": "...",
        "api_secret": "...",
        "access_token": "...",
        "access_token_secret": "...",
    })
"""

import asyncio
import hashlib
import hmac
import json
import logging
import time
import uuid
from typing import Any, Dict, Optional
from urllib.request import Request, urlopen
from urllib.parse import urlencode, quote

from xuanji.channels._base import ChannelBase, ChatType, ContentType, Message

logger = logging.getLogger("xuanji.channels.twitter")


class TwitterChannel(ChannelBase):
    """Twitter/X通信渠道"""

    name = "twitter"
    description = "Twitter/X渠道（X API v2）"

    API_BASE_V2 = "https://api.twitter.com/2"
    API_BASE_V1 = "https://api.twitter.com/1.1"

    def __init__(self):
        super().__init__()
        self._config: Dict[str, Any] = {}
        self._bearer_token: str = ""
        self._api_key: str = ""
        self._api_secret: str = ""
        self._access_token: str = ""
        self._access_token_secret: str = ""
        self._user_id: str = ""
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._recent_messages: list = []

    async def connect(self, config: Dict) -> None:
        self._config = config
        self._bearer_token = config.get("bearer_token", "")
        self._api_key = config.get("api_key", "")
        self._api_secret = config.get("api_secret", "")
        self._access_token = config.get("access_token", "")
        self._access_token_secret = config.get("access_token_secret", "")

        if not self._bearer_token and not self._api_key:
            raise ValueError("Twitter渠道需要 bearer_token 或 api_key")

        self._connected = True
        logger.info("Twitter/X渠道已连接")

    async def listen(self) -> None:
        if not self._connected:
            raise RuntimeError("未连接")
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None
        # Twitter通过Webhook或流式API接收消息
        while self._connected:
            await asyncio.sleep(5)

    async def send_text(self, target: str, text: str) -> None:
        """发送推文或DM
        
        Args:
            target: 用户ID或"tweet"（发推文）
            text: 内容
        """
        if target == "tweet":
            await self._post_tweet(text)
        else:
            await self._send_dm(target, text)

    async def _post_tweet(self, text: str) -> None:
        """发推文"""
        url = f"{self.API_BASE_V2}/tweets"
        payload = {"text": text}
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._bearer_token}",
        }
        req = Request(url, data=json.dumps(payload).encode("utf-8"),
                      headers=headers, method="POST")
        try:
            with urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if "errors" in data:
                    logger.error(f"发推文失败: {data}")
        except Exception as e:
            logger.error(f"发推文异常: {e}")

    async def _send_dm(self, target: str, text: str) -> None:
        """发送DM（需要v1.1 API）"""
        url = f"{self.API_BASE_V1}/direct_messages/events/new.json"
        payload = {
            "event": {
                "type": "message_create",
                "message_create": {
                    "target": {"recipient_id": target},
                    "message_data": {"text": text},
                }
            }
        }
        # OAuth 1.0a签名
        auth_header = self._oauth_sign("POST", url, payload)
        req = Request(url, data=json.dumps(payload).encode("utf-8"),
                      headers={
                          "Content-Type": "application/json",
                          "Authorization": auth_header,
                      }, method="POST")
        try:
            with urlopen(req, timeout=15) as resp:
                pass
        except Exception as e:
            logger.error(f"发送DM异常: {e}")

    def _oauth_sign(self, method: str, url: str, params: Dict) -> str:
        """生成OAuth 1.0a签名"""
        base_params = {
            "oauth_consumer_key": self._api_key,
            "oauth_token": self._access_token,
            "oauth_signature_method": "HMAC-SHA1",
            "oauth_timestamp": str(int(time.time())),
            "oauth_nonce": uuid.uuid4().hex,
            "oauth_version": "1.0",
        }
        all_params = {**base_params, **params}
        sorted_params = sorted(all_params.items())
        param_str = "&".join(f"{quote(str(k))}={quote(str(v))}" for k, v in sorted_params)
        base_string = f"{method}&{quote(url)}&{quote(param_str)}"
        signing_key = f"{quote(self._api_secret)}&{quote(self._access_token_secret)}"
        signature = hmac.new(
            signing_key.encode(), base_string.encode(), hashlib.sha1
        ).digest()
        import base64
        signature = base64.b64encode(signature).decode()
        auth_parts = [f'{k}="{quote(str(v))}"' for k, v in base_params.items()]
        auth_parts.append(f'oauth_signature="{quote(signature)}"')
        return "OAuth " + ", ".join(auth_parts)

    async def send_image(self, target: str, image: Any) -> None:
        """发送图片推文"""
        media_id = await self._upload_media(image)
        if media_id:
            await self._post_tweet_with_media("", media_id)

    async def _upload_media(self, source: Any) -> str:
        """上传媒体到Twitter"""
        url = "https://upload.twitter.com/1.1/media/upload.json"
        try:
            if isinstance(source, str):
                with open(source, "rb") as f:
                    file_data = f.read()
            else:
                file_data = source
            import base64
            import uuid
            boundary = f"----FormBoundary{uuid.uuid4().hex[:16]}"
            body = (
                f'--{boundary}\r\n'
                f'Content-Disposition: form-data; name="media"; filename="media"\r\n'
                f'Content-Type: application/octet-stream\r\n\r\n'
            ).encode() + file_data + f"\r\n--{boundary}--\r\n".encode()
            auth_header = self._oauth_sign("POST", url, {})
            req = Request(url, data=body,
                          headers={
                              "Content-Type": f"multipart/form-data; boundary={boundary}",
                              "Authorization": auth_header,
                          }, method="POST")
            with urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("media_id_string", "")
        except Exception as e:
            logger.error(f"Twitter上传媒体失败: {e}")
            return ""

    async def _post_tweet_with_media(self, text: str, media_id: str) -> None:
        """发带图片的推文"""
        url = f"{self.API_BASE_V2}/tweets"
        payload = {"text": text, "media": {"media_ids": [media_id]}}
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._bearer_token}",
        }
        req = Request(url, data=json.dumps(payload).encode("utf-8"),
                      headers=headers, method="POST")
        try:
            with urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if "errors" in data:
                    logger.error(f"发推文失败: {data}")
        except Exception as e:
            logger.error(f"发推文异常: {e}")

    async def send_file(self, target: str, path: str) -> None:
        logger.info(f"[Twitter] 发送文件到 {target}: {path}")

    async def send_voice(self, target: str, audio: Any) -> None:
        logger.info(f"[Twitter] 发送语音到 {target}")

    async def disconnect(self) -> None:
        self._connected = False
        logger.info("Twitter/X渠道已断开")
