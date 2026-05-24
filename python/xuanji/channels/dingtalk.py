"""
xuanji 钉钉渠道

支持钉钉机器人API（Webhook + Open API）。
零外部依赖，使用urllib.request标准库。

用法:
    from xuanji.channels.dingtalk import DingTalkChannel
    
    channel = DingTalkChannel()
    await channel.connect({
        "access_token": "...",
        "secret": "...",  # 可选，签名验证
    })
"""

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import time
import urllib.parse
from typing import Any, Dict, Optional
from urllib.request import Request, urlopen
from urllib.error import URLError

from xuanji.channels._base import ChannelBase, ChatType, ContentType, Message

logger = logging.getLogger("xuanji.channels.dingtalk")


class DingTalkChannel(ChannelBase):
    """钉钉通信渠道"""

    name = "dingtalk"
    description = "钉钉渠道（Webhook + Open API）"

    API_BASE = "https://oapi.dingtalk.com"
    WEBHOOK_BASE = "https://oapi.dingtalk.com/robot/send"

    def __init__(self):
        super().__init__()
        self._config: Dict[str, Any] = {}
        self._access_token: str = ""
        self._token_expires: float = 0
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._recent_messages: list = []

    async def connect(self, config: Dict) -> None:
        self._config = config
        app_key = config.get("app_key", "")
        app_secret = config.get("app_secret", "")
        access_token = config.get("access_token", "")

        if access_token:
            self._access_token = access_token
        elif app_key and app_secret:
            await self._fetch_token(app_key, app_secret)
        else:
            # Webhook模式，不需要token
            webhook_url = config.get("webhook_url", "")
            if not webhook_url:
                raise ValueError("钉钉渠道需要 access_token 或 app_key+app_secret 或 webhook_url")

        self._connected = True
        logger.info("钉钉渠道已连接")

    async def _fetch_token(self, app_key: str, app_secret: str) -> str:
        """获取钉钉access_token"""
        if self._access_token and time.time() < self._token_expires:
            return self._access_token
        url = f"{self.API_BASE}/gettoken?appkey={app_key}&appsecret={app_secret}"
        try:
            req = Request(url)
            with urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if data.get("errcode") == 0:
                    self._access_token = data["access_token"]
                    self._token_expires = time.time() + data.get("expires_in", 7200) - 300
                    return self._access_token
        except Exception as e:
            logger.error(f"获取钉钉token异常: {e}")
        return ""

    async def _refresh_token(self, app_key: str, app_secret: str) -> str:
        """获取新版access_token（v2 API）"""
        url = f"https://api.dingtalk.com/v1.0/oauth2/accessToken"
        payload = {"appKey": app_key, "appSecret": app_secret}
        try:
            req = Request(url, data=json.dumps(payload).encode("utf-8"),
                          headers={"Content-Type": "application/json"}, method="POST")
            with urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if data.get("accessToken"):
                    self._access_token = data["accessToken"]
                    self._token_expires = time.time() + data.get("expireTime", 7200) - 300
                    return self._access_token
        except Exception as e:
            logger.error(f"获取钉钉v2 token异常: {e}")
        return ""

    async def listen(self) -> None:
        if not self._connected:
            raise RuntimeError("未连接")
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None
        # 钉钉通常通过回调接收消息，轮询做心跳
        while self._connected:
            await asyncio.sleep(5)

    async def send_text(self, target: str, text: str) -> None:
        webhook_url = self._config.get("webhook_url", "")
        if webhook_url:
            await self._send_webhook(webhook_url, text)
        else:
            await self._send_api(target, text)

    async def _send_webhook(self, webhook_url: str, text: str) -> None:
        """通过Webhook发送消息"""
        secret = self._config.get("secret", "")
        if secret:
            timestamp = str(int(time.time() * 1000))
            string_to_sign = f"{timestamp}\n{secret}"
            hmac_code = hmac.new(
                secret.encode("utf-8"), string_to_sign.encode("utf-8"),
                digestmod=hashlib.sha256
            ).digest()
            sign = urllib.parse.quote_plus(base64.b64encode(hmac_code).decode("utf-8"))
            webhook_url = f"{webhook_url}&timestamp={timestamp}&sign={sign}"

        payload = {"msgtype": "text", "text": {"content": text}}
        req = Request(webhook_url, data=json.dumps(payload).encode("utf-8"),
                      headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("errcode") != 0:
                logger.error(f"钉钉Webhook发送失败: {data}")

    async def _send_api(self, target: str, text: str) -> None:
        """通过Open API发送消息"""
        token = await self._fetch_token(
            self._config.get("app_key", ""), self._config.get("app_secret", "")
        )
        if not token:
            raise ConnectionError("钉钉token不可用")

        # 判断是群聊还是单聊
        url = f"{self.API_BASE}/robot/send?access_token={token}"
        payload = {
            "msgtype": "text",
            "text": {"content": text},
            "at": {"atMobiles": [], "isAtAll": False},
        }
        req = Request(url, data=json.dumps(payload).encode("utf-8"),
                      headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("errcode") != 0:
                logger.error(f"钉钉API发送失败: {data}")

    async def send_image(self, target: str, image: Any) -> None:
        """发送图片 — 先上传再发送"""
        token = await self._fetch_token(
            self._config.get("app_key", ""), self._config.get("app_secret", "")
        )
        if not token:
            raise ConnectionError("钉钉token不可用")
        media_id = await self._upload_media(token, image, "image")
        if media_id:
            url = f"{self.API_BASE}/robot/send?access_token={token}"
            payload = {"msgtype": "image", "image": {"pic_url": media_id}}
            req = Request(url, data=json.dumps(payload).encode("utf-8"),
                          headers={"Content-Type": "application/json"}, method="POST")
            with urlopen(req, timeout=15) as resp:
                pass

    async def send_file(self, target: str, path: str) -> None:
        token = await self._fetch_token(
            self._config.get("app_key", ""), self._config.get("app_secret", "")
        )
        media_id = await self._upload_media(token, path, "file")
        if media_id:
            url = f"{self.API_BASE}/robot/send?access_token={token}"
            payload = {"msgtype": "file", "file": {"media_id": media_id}}
            req = Request(url, data=json.dumps(payload).encode("utf-8"),
                          headers={"Content-Type": "application/json"}, method="POST")
            with urlopen(req, timeout=15) as resp:
                pass

    async def send_voice(self, target: str, audio: Any) -> None:
        token = await self._fetch_token(
            self._config.get("app_key", ""), self._config.get("app_secret", "")
        )
        media_id = await self._upload_media(token, audio, "audio")
        if media_id:
            url = f"{self.API_BASE}/robot/send?access_token={token}"
            payload = {"msgtype": "voice", "voice": {"media_id": media_id, "duration": 60000}}
            req = Request(url, data=json.dumps(payload).encode("utf-8"),
                          headers={"Content-Type": "application/json"}, method="POST")
            with urlopen(req, timeout=15) as resp:
                pass

    async def _upload_media(self, token: str, source: Any, media_type: str) -> str:
        """上传媒体文件"""
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
                return data.get("media_id", "") or data.get("url", "")
        except Exception as e:
            logger.error(f"钉钉上传媒体失败: {e}")
            return ""

    async def disconnect(self) -> None:
        self._connected = False
        logger.info("钉钉渠道已断开")
