"""
xuanji 小红书渠道

小红书没有公开API，通过浏览器自动化方式实现。
提供两种模式：
- browser: 使用浏览器自动化（需要playwright/selenium）
- webhook: 通过Webhook接收/发送（手动转发）

零外部依赖（基础模式），可选依赖playwright。

用法:
    from xuanji.channels.xiaohongshu import XiaohongshuChannel
    
    channel = XiaohongshuChannel()
    await channel.connect({
        "mode": "webhook",  # or "browser"
        "port": 8091,
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

logger = logging.getLogger("xuanji.channels.xiaohongshu")


class _XHSHandler(BaseHTTPRequestHandler):
    """小红书Webhook处理器"""
    channel: Optional["XiaohongshuChannel"] = None

    def log_message(self, fmt, *args):
        logger.debug(fmt % args)

    def do_POST(self):
        if self.channel is None:
            self._respond(404, b"not found")
            return
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""
        try:
            data = json.loads(body.decode("utf-8"))
            msg = Message(
                channel="xiaohongshu",
                sender=data.get("sender", ""),
                sender_name=data.get("sender_name", ""),
                chat_id=data.get("chat_id", ""),
                chat_type=ChatType.PRIVATE,
                content_type=ContentType.TEXT,
                content=data.get("content", ""),
                media_url=data.get("media_url", ""),
                timestamp=time.time(),
                raw=data,
            )
            self.channel._recent_messages.append(msg)
            if self.channel._loop:
                asyncio.run_coroutine_threadsafe(self.channel.emit("message", msg), self.channel._loop)
            self._respond(200, json.dumps({"status": "ok"}).encode())
        except Exception as e:
            logger.error(f"处理小红书消息异常: {e}")
            self._respond(500, json.dumps({"error": str(e)}).encode())

    def _respond(self, code: int, data: bytes):
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class XiaohongshuChannel(ChannelBase):
    """小红书通信渠道

    由于小红书没有公开API，提供两种工作模式：
    - webhook: 启动本地HTTP服务器，通过外部工具转发消息
    - browser: 使用浏览器自动化（可选依赖playwright）
    """

    name = "xiaohongshu"
    description = "小红书渠道（Webhook / 浏览器自动化）"

    def __init__(self):
        super().__init__()
        self._config: Dict[str, Any] = {}
        self._server: Optional[HTTPServer] = None
        self._server_thread: Optional[Any] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._recent_messages: list = []
        self._browser: Optional[Any] = None

    async def connect(self, config: Dict) -> None:
        self._config = config
        mode = config.get("mode", "webhook")

        if mode == "webhook":
            await self._start_webhook(config)
        elif mode == "browser":
            await self._start_browser(config)
        else:
            raise ValueError(f"未知小红书模式: {mode}")

        self._connected = True
        logger.info(f"小红书渠道已连接 (mode={mode})")

    async def _start_webhook(self, config: Dict) -> None:
        """启动Webhook模式"""
        host = config.get("host", "0.0.0.0")
        port = config.get("port", 8091)
        handler_cls = type("_XHSBoundHandler", (_XHSHandler,), {"channel": self})
        self._server = HTTPServer((host, port), handler_cls)
        logger.info(f"小红书Webhook服务器: http://{host}:{port}")

    async def _start_browser(self, config: Dict) -> None:
        """启动浏览器自动化模式"""
        try:
            from playwright.async_api import async_playwright
            self._browser = await async_playwright().start()
            browser = await self._browser.chromium.launch(headless=config.get("headless", True))
            context = await browser.new_context()
            # 加载小红书页面
            page = await context.new_page()
            await page.goto("https://www.xiaohongshu.com")
            logger.info("小红书浏览器已启动")
        except ImportError:
            logger.warning("小红书浏览器模式需要 playwright: pip install playwright && playwright install")
            raise

    async def listen(self) -> None:
        if not self._connected:
            raise RuntimeError("未连接")
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None

        if self._server:
            import threading
            self._server_thread = threading.Thread(
                target=self._server.serve_forever, daemon=True, name="xhs-webhook"
            )
            self._server_thread.start()

        while self._connected:
            await asyncio.sleep(1)

    async def send_text(self, target: str, text: str) -> None:
        mode = self._config.get("mode", "webhook")
        if mode == "browser" and self._browser:
            logger.info(f"[小红书] 浏览器发送文本到 {target}: {text[:100]}")
        else:
            logger.info(f"[小红书-Webhook] 发送文本到 {target}: {text[:100]}")

    async def send_image(self, target: str, image: Any) -> None:
        logger.info(f"[小红书] 发送图片到 {target}")

    async def send_file(self, target: str, path: str) -> None:
        logger.info(f"[小红书] 发送文件到 {target}: {path}")

    async def send_voice(self, target: str, audio: Any) -> None:
        logger.info(f"[小红书] 发送语音到 {target}")

    async def disconnect(self) -> None:
        self._connected = False
        if self._server:
            self._server.shutdown()
            self._server = None
        if self._browser:
            await self._browser.stop()
            self._browser = None
        logger.info("小红书渠道已断开")
