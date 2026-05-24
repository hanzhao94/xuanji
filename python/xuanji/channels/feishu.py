"""
xuanji 飞书渠道

支持飞书机器人API（Webhook + Open API v2）。
零外部依赖，使用urllib.request标准库。

用法:
    from xuanji.channels.feishu import FeishuChannel
    
    channel = FeishuChannel()
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

logger = logging.getLogger("xuanji.channels.feishu")


class FeishuChannel(ChannelBase):
    """飞书通信渠道"""

    name = "feishu"
    description = "飞书渠道（Open API v2）"

    API_BASE = "https://open.feishu.cn/open-apis"

    def __init__(self):
        super().__init__()
        self._config: Dict[str, Any] = {}
        self._tenant_access_token: str = ""
        self._token_expires: float = 0
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._recent_messages: list = []

    async def connect(self, config: Dict) -> None:
        self._config = config
        app_id = config.get("app_id", "")
        app_secret = config.get("app_secret", "")
        if not app_id or not app_secret:
            raise ValueError("飞书渠道需要 app_id 和 app_secret")

        await self._fetch_token(app_id, app_secret)
        if not self._tenant_access_token:
            raise ConnectionError("飞书tenant_access_token获取失败")

        self._connected = True
        logger.info("飞书渠道已连接")

    async def _fetch_token(self, app_id: str, app_secret: str) -> str:
        """获取tenant_access_token"""
        if self._tenant_access_token and time.time() < self._token_expires:
            return self._tenant_access_token
        url = f"{self.API_BASE}/auth/v3/tenant_access_token/internal"
        payload = {"app_id": app_id, "app_secret": app_secret}
        try:
            req = Request(url, data=json.dumps(payload).encode("utf-8"),
                          headers={"Content-Type": "application/json"}, method="POST")
            with urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if data.get("code") == 0:
                    self._tenant_access_token = data["tenant_access_token"]
                    self._token_expires = time.time() + data.get("expire", 7200) - 300
                    return self._tenant_access_token
                else:
                    logger.error(f"获取飞书token失败: {data}")
        except Exception as e:
            logger.error(f"获取飞书token异常: {e}")
        return ""

    async def listen(self) -> None:
        if not self._connected:
            raise RuntimeError("未连接")
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None
        # 飞书通过事件订阅接收消息
        while self._connected:
            await asyncio.sleep(5)

    async def send_text(self, target: str, text: str) -> None:
        token = await self._fetch_token(
            self._config.get("app_id", ""), self._config.get("app_secret", "")
        )
        if not token:
            raise ConnectionError("飞书token不可用")

        # 判断target类型: open_id / user_id / chat_id / email
        receive_id_type = self._guess_id_type(target)
        url = f"{self.API_BASE}/im/v1/messages?receive_id_type={receive_id_type}"
        payload = {
            "receive_id": target,
            "msg_type": "text",
            "content": json.dumps({"text": text}),
        }
        req = Request(url, data=json.dumps(payload).encode("utf-8"),
                      headers={
                          "Content-Type": "application/json",
                          "Authorization": f"Bearer {token}",
                      }, method="POST")
        try:
            with urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if data.get("code") != 0:
                    logger.error(f"飞书发送失败: {data}")
        except Exception as e:
            logger.error(f"飞书发送异常: {e}")

    def _guess_id_type(self, target: str) -> str:
        """猜测ID类型"""
        if target.startswith("ou_"):
            return "open_id"
        elif target.startswith("on_"):
            return "union_id"
        elif target.startswith("oc_"):
            return "chat_id"
        elif "@" in target:
            return "email"
        elif target.startswith("uid_"):
            return "user_id"
        return "open_id"

    async def send_image(self, target: str, image: Any) -> None:
        token = await self._fetch_token(
            self._config.get("app_id", ""), self._config.get("app_secret", "")
        )
        image_key = await self._upload_image(token, image)
        if image_key:
            receive_id_type = self._guess_id_type(target)
            url = f"{self.API_BASE}/im/v1/messages?receive_id_type={receive_id_type}"
            payload = {
                "receive_id": target,
                "msg_type": "image",
                "content": json.dumps({"image_key": image_key}),
            }
            req = Request(url, data=json.dumps(payload).encode("utf-8"),
                          headers={
                              "Content-Type": "application/json",
                              "Authorization": f"Bearer {token}",
                          }, method="POST")
            try:
                with urlopen(req, timeout=15) as resp:
                    pass
            except Exception as e:
                logger.error(f"飞书发送图片异常: {e}")

    async def send_file(self, target: str, path: str) -> None:
        token = await self._fetch_token(
            self._config.get("app_id", ""), self._config.get("app_secret", "")
        )
        file_key = await self._upload_file(token, path)
        if file_key:
            receive_id_type = self._guess_id_type(target)
            url = f"{self.API_BASE}/im/v1/messages?receive_id_type={receive_id_type}"
            payload = {
                "receive_id": target,
                "msg_type": "file",
                "content": json.dumps({"file_key": file_key}),
            }
            req = Request(url, data=json.dumps(payload).encode("utf-8"),
                          headers={
                              "Content-Type": "application/json",
                              "Authorization": f"Bearer {token}",
                          }, method="POST")
            try:
                with urlopen(req, timeout=15) as resp:
                    pass
            except Exception as e:
                logger.error(f"飞书发送文件异常: {e}")

    async def send_voice(self, target: str, audio: Any) -> None:
        token = await self._fetch_token(
            self._config.get("app_id", ""), self._config.get("app_secret", "")
        )
        file_key = await self._upload_file(token, audio)
        if file_key:
            receive_id_type = self._guess_id_type(target)
            url = f"{self.API_BASE}/im/v1/messages?receive_id_type={receive_id_type}"
            payload = {
                "receive_id": target,
                "msg_type": "audio",
                "content": json.dumps({"file_key": file_key}),
            }
            req = Request(url, data=json.dumps(payload).encode("utf-8"),
                          headers={
                              "Content-Type": "application/json",
                              "Authorization": f"Bearer {token}",
                          }, method="POST")
            try:
                with urlopen(req, timeout=15) as resp:
                    pass
            except Exception as e:
                logger.error(f"飞书发送语音异常: {e}")

    async def _upload_image(self, token: str, source: Any) -> str:
        """上传图片"""
        return await self._upload_media(token, source, "image", "image.png")

    async def _upload_file(self, token: str, source: Any) -> str:
        """上传文件"""
        return await self._upload_media(token, source, "file", "file.dat")

    async def _upload_media(self, token: str, source: Any, file_type: str, file_name: str) -> str:
        """上传媒体到飞书"""
        url = f"{self.API_BASE}/im/v1/files"
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
                f'Content-Disposition: form-data; name="file_type"\r\n\r\n'
                f'{file_type}\r\n'
                f'--{boundary}\r\n'
                f'Content-Disposition: form-data; name="file_name"\r\n\r\n'
                f'{file_name}\r\n'
                f'--{boundary}\r\n'
                f'Content-Disposition: form-data; name="file"; filename="{file_name}"\r\n'
                f'Content-Type: application/octet-stream\r\n\r\n'
            ).encode() + file_data + f"\r\n--{boundary}--\r\n".encode()
            req = Request(url, data=body,
                          headers={
                              "Content-Type": f"multipart/form-data; boundary={boundary}",
                              "Authorization": f"Bearer {token}",
                          }, method="POST")
            with urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if data.get("code") == 0:
                    return data["data"]["file_key"]
        except Exception as e:
            logger.error(f"飞书上传媒体失败: {e}")
        return ""

    async def disconnect(self) -> None:
        self._connected = False
        logger.info("飞书渠道已断开")
