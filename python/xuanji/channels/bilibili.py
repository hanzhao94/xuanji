"""
xuanji B站渠道

支持Bilibili开放平台API。
零外部依赖，使用urllib.request标准库。

用法:
    from xuanji.channels.bilibili import BilibiliChannel
    
    channel = BilibiliChannel()
    await channel.connect({
        "app_id": "...",
        "app_secret": "...",
        "access_token": "...",  # 可选
    })
"""

import asyncio
import json
import logging
import time
from typing import Any, Dict, Optional
from urllib.request import Request, urlopen
from urllib.error import URLError
from urllib.parse import urlencode

from xuanji.channels._base import ChannelBase, ChatType, ContentType, Message

logger = logging.getLogger("xuanji.channels.bilibili")


class BilibiliChannel(ChannelBase):
    """B站通信渠道"""

    name = "bilibili"
    description = "B站渠道（Bilibili Open API）"

    API_BASE = "https://api.bilibili.com"
    MSG_API = "https://api.vc.bilibili.com"

    def __init__(self):
        super().__init__()
        self._config: Dict[str, Any] = {}
        self._access_token: str = ""
        self._csrf: str = ""
        self._uid: str = ""
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._recent_messages: list = []

    async def connect(self, config: Dict) -> None:
        self._config = config
        self._access_token = config.get("access_token", "")
        self._csrf = config.get("csrf", "")
        self._uid = config.get("uid", "")

        if not self._uid:
            # 尝试通过token获取uid
            if self._access_token:
                await self._fetch_uid()

        self._connected = True
        logger.info("B站渠道已连接")

    async def _fetch_uid(self) -> str:
        """通过token获取用户uid"""
        url = f"{self.API_BASE}/x/web-interface/nav"
        try:
            req = Request(url, headers={
                "Authorization": f"Bearer {self._access_token}",
                "User-Agent": "Mozilla/5.0",
            })
            with urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if data.get("code") == 0:
                    self._uid = str(data["data"]["mid"])
                    return self._uid
        except Exception as e:
            logger.error(f"获取B站uid异常: {e}")
        return ""

    async def listen(self) -> None:
        if not self._connected:
            raise RuntimeError("未连接")
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None
        # B站消息通过WebSocket或轮询
        while self._connected:
            await self._poll_messages()
            await asyncio.sleep(5)

    async def _poll_messages(self) -> None:
        """轮询B站私信"""
        if not self._access_token:
            return
        url = f"{self.MSG_API}/msg_sync?f=0&build=0&mobi_app=web&access_key={self._access_token}"
        try:
            req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if data.get("code") == 0:
                    sync_flag = data["data"].get("sync_flag", 0)
                    if sync_flag & 1:  # 有新消息
                        await self._fetch_new_messages()
        except Exception as e:
            logger.debug(f"B站轮询异常: {e}")

    async def _fetch_new_messages(self) -> None:
        """获取新消息"""
        url = f"{self.MSG_API}/session/sessions?sender_device_id=1&group_id=0&talk_type=0&access_key={self._access_token}&mobi_app=web"
        try:
            req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if data.get("code") == 0:
                    for session in data["data"].get("session_list", []):
                        last_msg = json.loads(session.get("last_msg", "{}"))
                        if last_msg:
                            msg = Message(
                                channel="bilibili",
                                sender=str(session.get("talker_id", "")),
                                sender_name=session.get("user_name", ""),
                                chat_id=str(session.get("session_id", "")),
                                chat_type=ChatType.PRIVATE,
                                content_type=ContentType.TEXT,
                                content=last_msg.get("content", ""),
                                timestamp=last_msg.get("timestamp", 0),
                                raw=last_msg,
                            )
                            self._recent_messages.append(msg)
                            if self._loop:
                                asyncio.run_coroutine_threadsafe(self.emit("message", msg), self._loop)
        except Exception as e:
            logger.debug(f"获取B站消息异常: {e}")

    async def send_text(self, target: str, text: str) -> None:
        """发送私信"""
        url = f"{self.MSG_API}/web/im/send"
        payload = {
            "msg[sender_uid]": self._uid,
            "msg[receiver_id]": target,
            "msg[receiver_type]": "1",  # 1=用户
            "msg[msg_type]": "1",  # 1=文本
            "msg[msg_status]": "0",
            "msg[content]": json.dumps({"content": text}),
            "msg[timestamp]": str(int(time.time())),
            "msg[new_face_version]": "0",
            "csrf": self._csrf,
            "access_key": self._access_token,
        }
        req = Request(url, data=urlencode(payload).encode("utf-8"), method="POST")
        try:
            with urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if data.get("code") != 0:
                    logger.error(f"B站发送失败: {data}")
        except Exception as e:
            logger.error(f"B站发送异常: {e}")

    async def send_image(self, target: str, image: Any) -> None:
        """发送图片 — 先上传"""
        url = f"{self.API_BASE}/x/web-interface/archive/upload/bili"
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
                f'Content-Disposition: form-data; name="file"; filename="image.jpg"\r\n'
                f'Content-Type: image/jpeg\r\n\r\n'
            ).encode() + img_data + f"\r\n--{boundary}--\r\n".encode()
            req = Request(url, data=body,
                          headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
                          method="POST")
            with urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if data.get("code") == 0:
                    img_url = data["data"]["url"]
                    # 发送图片消息
                    content = json.dumps({"url": img_url, "size": 1, "type": 2})
                    msg_url = f"{self.MSG_API}/web/im/send"
                    payload = {
                        "msg[sender_uid]": self._uid,
                        "msg[receiver_id]": target,
                        "msg[receiver_type]": "1",
                        "msg[msg_type]": "2",  # 图片
                        "msg[content]": content,
                        "msg[timestamp]": str(int(time.time())),
                        "csrf": self._csrf,
                        "access_key": self._access_token,
                    }
                    req2 = Request(msg_url, data=urlencode(payload).encode("utf-8"), method="POST")
                    with urlopen(req2, timeout=15) as resp2:
                        pass
        except Exception as e:
            logger.error(f"B站发送图片异常: {e}")

    async def send_file(self, target: str, path: str) -> None:
        logger.info(f"[B站] 发送文件到 {target}: {path}")

    async def send_voice(self, target: str, audio: Any) -> None:
        logger.info(f"[B站] 发送语音到 {target}")

    async def disconnect(self) -> None:
        self._connected = False
        logger.info("B站渠道已断开")
