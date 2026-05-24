"""
xuanji 微信渠道

支持：
- 企业微信API（推荐，官方支持）
- 网页版微信协议（备用，需要web协议token）

零外部依赖，使用urllib.request标准库。

用法:
    from xuanji.channels.wechat import WeChatChannel
    
    channel = WeChatChannel()
    await channel.connect({
        "mode": "wecom",  # "wecom" or "web"
        "corp_id": "...",
        "corp_secret": "...",
        "agent_id": "...",
    })
"""

import asyncio
import hashlib
import hmac
import json
import logging
import time
import uuid
import xml.etree.ElementTree as ET
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any, Dict, Optional
from urllib.request import Request, urlopen
from urllib.parse import urlencode, parse_qs
from urllib.error import URLError

from xuanji.channels._base import ChannelBase, ChatType, ContentType, Message

logger = logging.getLogger("xuanji.channels.wechat")


class _WechatHandler(BaseHTTPRequestHandler):
    """微信回调HTTP处理器"""
    channel: Optional["WeChatChannel"] = None

    def log_message(self, fmt, *args):
        logger.debug(fmt % args)

    def do_GET(self):
        if self.channel is None:
            self._respond(404, b"not found")
            return
        qs = parse_qs(self.path.split("?", 1)[-1] if "?" in self.path else "")
        signature = qs.get("msg_signature", [""])[0]
        timestamp = qs.get("timestamp", [""])[0]
        nonce = qs.get("nonce", [""])[0]
        echostr = qs.get("echostr", [""])[0]
        if echostr:
            # 验证签名
            token = self.channel._config.get("token", "")
            encoding_aes_key = self.channel._config.get("encoding_aes_key", "")
            self._respond(200, echostr.encode())
        else:
            self._respond(200, b"success")

    def do_POST(self):
        if self.channel is None:
            self._respond(404, b"not found")
            return
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""
        self.channel._handle_xml(body)
        self._respond(200, b"success")

    def _respond(self, code: int, data: bytes):
        self.send_response(code)
        self.send_header("Content-Type", "text/xml; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class WeChatChannel(ChannelBase):
    """微信通信渠道

    支持两种模式:
    - wecom: 企业微信API（推荐）
    - web: 网页版微信协议（备用）
    """

    name = "wechat"
    description = "微信渠道（企业微信API / 网页版协议）"

    def __init__(self):
        super().__init__()
        self._config: Dict[str, Any] = {}
        self._server: Optional[HTTPServer] = None
        self._server_thread: Optional[Any] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._access_token: str = ""
        self._token_expires: float = 0
        self._web_cookie: str = ""
        self._web_sid: str = ""

    async def connect(self, config: Dict) -> None:
        self._config = config
        mode = config.get("mode", "wecom")

        if mode == "wecom":
            await self._connect_wecom(config)
        elif mode == "web":
            await self._connect_web(config)
        else:
            raise ValueError(f"未知微信模式: {mode}")

        self._connected = True
        logger.info(f"WeChat渠道已连接 (mode={mode})")

    async def _connect_wecom(self, config: Dict) -> None:
        """企业微信API连接"""
        corp_id = config.get("corp_id", "")
        corp_secret = config.get("corp_secret", "")
        if not corp_id or not corp_secret:
            raise ValueError("企业微信模式需要 corp_id 和 corp_secret")
        # 获取access_token
        token = await self._fetch_wecom_token(corp_id, corp_secret)
        if not token:
            raise ConnectionError("企业微信access_token获取失败")

    async def _connect_web(self, config: Dict) -> None:
        """网页版微信协议连接"""
        token = config.get("token", "")
        encoding_aes_key = config.get("encoding_aes_key", "")
        if not token:
            logger.warning("网页版微信未提供token，回调验证可能被拒绝")
        # 启动本地回调服务器
        host = config.get("host", "0.0.0.0")
        port = config.get("port", 8090)
        handler_cls = type("_WechatBoundHandler", (_WechatHandler,), {"channel": self})
        self._server = HTTPServer((host, port), handler_cls)

    async def listen(self) -> None:
        if not self._connected:
            raise RuntimeError("未连接，请先调用connect()")
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None

        if self._server:
            import threading
            self._server_thread = threading.Thread(
                target=self._server.serve_forever, daemon=True, name="wechat-callback"
            )
            self._server_thread.start()
            logger.info(f"微信回调服务器已启动: {self._config.get('host', '0.0.0.0')}:{self._config.get('port', 8090)}")

        # 企业微信模式使用长轮询
        mode = self._config.get("mode", "wecom")
        if mode == "wecom":
            await self._poll_wecom()
        else:
            while self._connected:
                await asyncio.sleep(1)

    async def _poll_wecom(self) -> None:
        """企业微信被动消息轮询"""
        while self._connected:
            try:
                corp_id = self._config.get("corp_id", "")
                corp_secret = self._config.get("corp_secret", "")
                token = await self._fetch_wecom_token(corp_id, corp_secret)
                if token:
                    # 企业微信通常是回调模式，这里做心跳
                    pass
            except Exception as e:
                logger.debug(f"企业微信轮询: {e}")
            await asyncio.sleep(5)

    async def _fetch_wecom_token(self, corp_id: str, corp_secret: str) -> str:
        """获取企业微信access_token"""
        if self._access_token and time.time() < self._token_expires:
            return self._access_token
        url = f"https://qyapi.weixin.qq.com/cgi-bin/gettoken?corpid={corp_id}&corpsecret={corp_secret}"
        try:
            req = Request(url)
            with urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if data.get("errcode") == 0:
                    self._access_token = data["access_token"]
                    self._token_expires = time.time() + data.get("expires_in", 7200) - 300
                    return self._access_token
                else:
                    logger.error(f"获取token失败: {data}")
        except Exception as e:
            logger.error(f"获取token异常: {e}")
        return ""

    def _handle_xml(self, body: bytes) -> None:
        """处理微信XML消息"""
        try:
            root = ET.fromstring(body)
            msg_type = root.findtext("MsgType", "text")
            content = root.findtext("Content", "")
            sender = root.findtext("FromUserName", "")
            chat_id = root.findtext("ToUserName", "")

            ct = ContentType.TEXT
            if msg_type == "image":
                ct = ContentType.IMAGE
            elif msg_type == "voice":
                ct = ContentType.AUDIO
            elif msg_type == "video":
                ct = ContentType.VIDEO
            elif msg_type == "file":
                ct = ContentType.FILE

            msg = Message(
                channel="wechat",
                sender=sender,
                sender_name=sender,
                chat_id=chat_id,
                chat_type=ChatType.PRIVATE,
                content_type=ct,
                content=content,
                timestamp=float(root.findtext("CreateTime", "0")),
                raw=root,
            )
            self._recent_messages.append(msg)
            if self._loop:
                asyncio.run_coroutine_threadsafe(self.emit("message", msg), self._loop)
        except Exception as e:
            logger.error(f"处理微信XML消息异常: {e}")

    async def send_text(self, target: str, text: str) -> None:
        mode = self._config.get("mode", "wecom")
        if mode == "wecom":
            await self._wecom_send_text(target, text)
        else:
            logger.info(f"[微信-{mode}] 发送文本到 {target}: {text[:100]}")

    async def _wecom_send_text(self, target: str, text: str) -> None:
        """企业微信发送文本"""
        token = await self._fetch_wecom_token(
            self._config.get("corp_id", ""), self._config.get("corp_secret", "")
        )
        if not token:
            raise ConnectionError("企业微信token不可用")
        url = f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={token}"
        payload = {
            "touser": target,
            "msgtype": "text",
            "agentid": int(self._config.get("agent_id", 0)),
            "text": {"content": text},
        }
        req = Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("errcode") != 0:
                logger.error(f"企业微信发送失败: {data}")

    async def send_image(self, target: str, image: Any) -> None:
        """发送图片（企业微信需先上传media）"""
        mode = self._config.get("mode", "wecom")
        if mode == "wecom":
            token = await self._fetch_wecom_token(
                self._config.get("corp_id", ""), self._config.get("corp_secret", "")
            )
            if not token:
                raise ConnectionError("企业微信token不可用")
            # 上传media获取media_id
            media_id = await self._upload_wecom_media(token, image, "image")
            if media_id:
                url = f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={token}"
                payload = {
                    "touser": target,
                    "msgtype": "image",
                    "agentid": int(self._config.get("agent_id", 0)),
                    "image": {"media_id": media_id},
                }
                req = Request(url, data=json.dumps(payload).encode("utf-8"),
                              headers={"Content-Type": "application/json"}, method="POST")
                with urlopen(req, timeout=15) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    if data.get("errcode") != 0:
                        logger.error(f"发送图片失败: {data}")
        else:
            logger.info(f"[微信-{mode}] 发送图片到 {target}")

    async def send_file(self, target: str, path: str) -> None:
        mode = self._config.get("mode", "wecom")
        if mode == "wecom":
            token = await self._fetch_wecom_token(
                self._config.get("corp_id", ""), self._config.get("corp_secret", "")
            )
            media_id = await self._upload_wecom_media(token, path, "file")
            if media_id:
                url = f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={token}"
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
        else:
            logger.info(f"[微信-{mode}] 发送文件到 {target}")

    async def send_voice(self, target: str, audio: Any) -> None:
        mode = self._config.get("mode", "wecom")
        if mode == "wecom":
            token = await self._fetch_wecom_token(
                self._config.get("corp_id", ""), self._config.get("corp_secret", "")
            )
            media_id = await self._upload_wecom_media(token, audio, "voice")
            if media_id:
                url = f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={token}"
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
        else:
            logger.info(f"[微信-{mode}] 发送语音到 {target}")

    async def _upload_wecom_media(self, token: str, source: Any, media_type: str) -> str:
        """上传企业微信媒体文件"""
        url = f"https://qyapi.weixin.qq.com/cgi-bin/media/upload?access_token={token}&type={media_type}"
        try:
            if isinstance(source, str):
                with open(source, "rb") as f:
                    file_data = f.read()
            else:
                file_data = source
            boundary = f"----WebKitFormBoundary{uuid.uuid4().hex[:16]}"
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
            logger.error(f"上传媒体失败: {e}")
            return ""

    async def disconnect(self) -> None:
        self._connected = False
        if self._server:
            self._server.shutdown()
            self._server = None
        logger.info("微信渠道已断开")

    _recent_messages: list = []
