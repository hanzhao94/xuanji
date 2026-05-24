"""
xuanji Facebook渠道

支持Facebook Messenger Platform。
零外部依赖，使用urllib.request标准库。

用法:
    from xuanji.channels.facebook import FacebookChannel
    
    channel = FacebookChannel()
    await channel.connect({
        "page_access_token": "...",
        "verify_token": "...",
    })
"""

import asyncio
import json
import logging
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any, Dict, Optional
from urllib.request import Request, urlopen

from xuanji.channels._base import ChannelBase, ChatType, ContentType, Message

logger = logging.getLogger("xuanji.channels.facebook")


class _FBHandler(BaseHTTPRequestHandler):
    """Facebook Messenger Webhook处理器"""
    channel: Optional["FacebookChannel"] = None

    def log_message(self, fmt, *args):
        logger.debug(fmt % args)

    def do_GET(self):
        if self.channel is None:
            self._respond(404, b"not found")
            return
        from urllib.parse import parse_qs
        qs = parse_qs(self.path.split("?", 1)[-1] if "?" in self.path else "")
        mode = qs.get("hub.mode", [""])[0]
        token = qs.get("hub.verify_token", [""])[0]
        challenge = qs.get("hub.challenge", [""])[0]
        if mode == "subscribe" and token == self.channel._config.get("verify_token", ""):
            self._respond(200, challenge.encode())
        else:
            self._respond(403, b"forbidden")

    def do_POST(self):
        if self.channel is None:
            self._respond(404, b"not found")
            return
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""
        try:
            data = json.loads(body.decode("utf-8"))
            self.channel._process_webhook(data)
            self._respond(200, b"ok")
        except Exception as e:
            logger.error(f"处理FB Webhook异常: {e}")
            self._respond(500, b"error")

    def _respond(self, code: int, data: bytes):
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class FacebookChannel(ChannelBase):
    """Facebook Messenger通信渠道"""

    name = "facebook"
    description = "Facebook Messenger渠道（Messenger Platform）"

    API_BASE = "https://graph.facebook.com/v18.0"

    def __init__(self):
        super().__init__()
        self._config: Dict[str, Any] = {}
        self._page_access_token: str = ""
        self._page_id: str = ""
        self._server: Optional[HTTPServer] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._recent_messages: list = []

    async def connect(self, config: Dict) -> None:
        self._config = config
        self._page_access_token = config.get("page_access_token", "")
        self._page_id = config.get("page_id", "")

        if not self._page_access_token:
            raise ValueError("Facebook需要 page_access_token")

        self._connected = True
        logger.info("Facebook Messenger渠道已连接")

    async def listen(self) -> None:
        if not self._connected:
            raise RuntimeError("未连接")
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None

        # 启动Webhook服务器
        if self._config.get("webhook_port"):
            host = self._config.get("host", "0.0.0.0")
            port = self._config.get("webhook_port", 8092)
            handler_cls = type("_FBBoundHandler", (_FBHandler,), {"channel": self})
            self._server = HTTPServer((host, port), handler_cls)
            import threading
            threading.Thread(target=self._server.serve_forever, daemon=True,
                             name="fb-webhook").start()
            logger.info(f"Facebook Webhook服务器: http://{host}:{port}")

        while self._connected:
            await asyncio.sleep(1)

    def _process_webhook(self, data: Dict) -> None:
        """处理Facebook Webhook数据"""
        try:
            for entry in data.get("entry", []):
                for msg_data in entry.get("messaging", []):
                    sender = msg_data.get("sender", {}).get("id", "")
                    recipient = msg_data.get("recipient", {}).get("id", "")

                    ct = ContentType.TEXT
                    content = ""
                    media_url = ""

                    if "message" in msg_data:
                        msg = msg_data["message"]
                        content = msg.get("text", "")
                        attachments = msg.get("attachments", [])
                        if attachments:
                            att = attachments[0]
                            if att.get("type") == "image":
                                ct = ContentType.IMAGE
                            elif att.get("type") == "audio":
                                ct = ContentType.AUDIO
                            elif att.get("type") == "video":
                                ct = ContentType.VIDEO
                            elif att.get("type") == "file":
                                ct = ContentType.FILE
                            media_url = att.get("payload", {}).get("url", "")

                    message_msg = Message(
                        channel="facebook",
                        sender=sender,
                        sender_name="",
                        chat_id=sender,
                        chat_type=ChatType.PRIVATE,
                        content_type=ct,
                        content=content,
                        media_url=media_url,
                        timestamp=time.time(),
                        raw=msg_data,
                    )
                    self._recent_messages.append(message_msg)
                    if self._loop:
                        asyncio.run_coroutine_threadsafe(self.emit("message", message_msg), self._loop)
        except Exception as e:
            logger.error(f"处理Facebook Webhook异常: {e}")

    async def send_text(self, target: str, text: str) -> None:
        url = f"{self.API_BASE}/{self._page_id}/messages"
        payload = {
            "recipient": {"id": target},
            "message": {"text": text},
        }
        req = Request(url, data=json.dumps(payload).encode("utf-8"),
                      headers={"Content-Type": "application/json"}, method="POST")
        req.add_header("Authorization", f"Bearer {self._page_access_token}")
        try:
            with urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if data.get("error"):
                    logger.error(f"Facebook发送失败: {data}")
        except Exception as e:
            logger.error(f"Facebook发送异常: {e}")

    async def send_image(self, target: str, image: Any) -> None:
        url = f"{self.API_BASE}/{self._page_id}/messages"
        if isinstance(image, str) and image.startswith("http"):
            payload = {
                "recipient": {"id": target},
                "message": {
                    "attachment": {
                        "type": "image",
                        "payload": {"url": image},
                    }
                },
            }
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
                f'Content-Disposition: form-data; name="recipient"\r\n\r\n'
                f'{{"id":"{target}"}}\r\n'
                f'--{boundary}\r\n'
                f'Content-Disposition: form-data; name="message"\r\n\r\n'
                f'{{"attachment":{{"type":"image","payload":{{"is_reusable":true}}}}}}\r\n'
                f'--{boundary}\r\n'
                f'Content-Disposition: form-data; name="filedata"; filename="image.jpg"\r\n'
                f'Content-Type: image/jpeg\r\n\r\n'
            ).encode() + img_data + f"\r\n--{boundary}--\r\n".encode()
            req = Request(url, data=body,
                          headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
                          method="POST")
        req.add_header("Authorization", f"Bearer {self._page_access_token}")
        try:
            with urlopen(req, timeout=30) as resp:
                pass
        except Exception as e:
            logger.error(f"Facebook发送图片异常: {e}")

    async def send_file(self, target: str, path: str) -> None:
        logger.info(f"[Facebook] 发送文件到 {target}: {path}")

    async def send_voice(self, target: str, audio: Any) -> None:
        logger.info(f"[Facebook] 发送语音到 {target}")

    async def disconnect(self) -> None:
        self._connected = False
        if self._server:
            self._server.shutdown()
            self._server = None
        logger.info("Facebook Messenger渠道已断开")
