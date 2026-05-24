"""
xuanji Webhook渠道

最简单的渠道实现，用于本地测试：
- 启动一个HTTP服务器（标准库http.server）
- POST /message 接收消息
- GET /health 健康检查
- 不依赖任何外部服务

用法:
    from xuanji.channels.webhook import WebhookChannel
    from xuanji.channels import ChannelRouter
    
    router = ChannelRouter()
    webhook = WebhookChannel(port=8080)
    router.register("webhook", webhook)
    
    @router.on_message
    async def handle(msg):
        print(f"收到: {msg.content}")
        await router.reply(msg, f"Echo: {msg.content}")
    
    # 测试: curl -X POST http://localhost:8080/message \\
    #        -H "Content-Type: application/json" \\
    #        -d '{"content": "hello", "sender": "test-user"}'
"""

import asyncio
import json
import logging
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any, Dict, List, Optional

from xuanji.channels._base import ChannelBase, ChatType, ContentType, Message

logger = logging.getLogger("xuanji.channels.webhook")


# ============================================================
# Webhook HTTP处理器
# ============================================================

class _WebhookHandler(BaseHTTPRequestHandler):
    """Webhook HTTP请求处理器"""
    
    # 由WebhookChannel设置
    channel: Optional["WebhookChannel"] = None
    
    def log_message(self, format, *args):
        """覆盖日志方法，使用标准logging"""
        logger.debug(format % args)
    
    def do_GET(self):
        """GET请求处理"""
        if self.path == "/health":
            self._respond_json(200, {
                "status": "ok",
                "channel": "webhook",
                "uptime": time.time() - (self.channel._start_time if self.channel else 0),
            })
        elif self.path == "/messages":
            # 返回最近消息（用于调试）
            if self.channel:
                msgs = [m.to_dict() for m in self.channel._recent_messages[-20:]]
                self._respond_json(200, {"messages": msgs})
            else:
                self._respond_json(200, {"messages": []})
        else:
            self._respond_json(404, {"error": "Not Found"})
    
    def do_POST(self):
        """POST请求处理"""
        if self.path == "/message":
            self._handle_message()
        else:
            self._respond_json(404, {"error": "Not Found"})
    
    def _handle_message(self):
        """处理POST /message"""
        try:
            # 读取body
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            
            if not body:
                self._respond_json(400, {"error": "Empty body"})
                return
            
            data = json.loads(body.decode("utf-8"))
            
            # 构造Message
            msg = Message(
                channel="webhook",
                sender=data.get("sender", "anonymous"),
                sender_name=data.get("sender_name", data.get("sender", "匿名")),
                chat_id=data.get("chat_id", "webhook-default"),
                chat_type=ChatType.PRIVATE,
                content_type=ContentType.TEXT,
                content=data.get("content", ""),
                media_url=data.get("media_url", ""),
                reply_to=data.get("reply_to", ""),
                timestamp=time.time(),
                raw=data,
            )
            
            # 存储并分发
            if self.channel:
                self.channel._recent_messages.append(msg)
                # 保持最近100条
                if len(self.channel._recent_messages) > 100:
                    self.channel._recent_messages = self.channel._recent_messages[-100:]
                
                # 触发消息事件（异步）
                asyncio.run_coroutine_threadsafe(
                    self.channel.emit("message", msg),
                    self.channel._loop,
                ) if self.channel._loop else None
            
            self._respond_json(200, {
                "status": "received",
                "message_id": f"wh-{int(msg.timestamp * 1000)}",
            })
        
        except json.JSONDecodeError:
            self._respond_json(400, {"error": "Invalid JSON"})
        except Exception as e:
            logger.error(f"处理消息异常: {e}")
            self._respond_json(500, {"error": str(e)})
    
    def _respond_json(self, code: int, data: dict):
        """返回JSON响应"""
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


# ============================================================
# Webhook渠道
# ============================================================

class WebhookChannel(ChannelBase):
    """Webhook通信渠道 — 最简单的渠道实现
    
    启动一个本地HTTP服务器，通过POST /message接收消息。
    主要用于本地开发测试。
    
    API:
        POST /message — 发送消息
            body: {"content": "...", "sender": "...", "chat_id": "..."}
        
        GET /health — 健康检查
        GET /messages — 查看最近消息
    """
    
    name = "webhook"
    description = "HTTP Webhook渠道（用于本地测试）"
    
    def __init__(self, host: str = "0.0.0.0", port: int = 8080):
        super().__init__()
        self._host = host
        self._port = port
        self._server: Optional[HTTPServer] = None
        self._server_thread: Optional[threading.Thread] = None
        self._start_time = 0.0
        self._recent_messages: List[Message] = []
        self._responses: Dict[str, str] = {}  # chat_id → 待发送的回复
        self._loop: Optional[asyncio.AbstractEventLoop] = None
    
    async def connect(self, config: Dict) -> None:
        """连接（启动HTTP服务器）
        
        Args:
            config: 可选配置 {"host": "...", "port": ...}
        """
        host = config.get("host", self._host)
        port = config.get("port", self._port)
        
        # 创建自定义Handler类（绑定channel实例）
        handler_class = type(
            "_BoundHandler",
            (_WebhookHandler,),
            {"channel": self},
        )
        
        self._server = HTTPServer((host, port), handler_class)
        self._start_time = time.time()
        self._connected = True
        
        logger.info(f"Webhook服务器准备就绪: http://{host}:{port}")
    
    async def listen(self) -> None:
        """开始监听（在线程中运行HTTP服务器）"""
        if not self._server:
            raise RuntimeError("未连接，请先调用connect()")
        
        # 获取当前事件循环
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None
        
        # 在线程中运行HTTP服务器
        self._server_thread = threading.Thread(
            target=self._server.serve_forever,
            daemon=True,
            name="webhook-server",
        )
        self._server_thread.start()
        
        logger.info(
            f"Webhook服务器已启动: "
            f"http://{self._host}:{self._port}"
        )
        
        # 保持异步运行（直到断开连接）
        while self._connected:
            await asyncio.sleep(1)
    
    async def send_text(self, target: str, text: str) -> None:
        """发送文本消息
        
        Webhook渠道是被动的，"发送"实际上是存储回复。
        可以通过GET /messages查看。
        
        Args:
            target: chat_id
            text: 回复文本
        """
        reply_msg = Message(
            channel="webhook",
            sender="system",
            sender_name="xuanji",
            chat_id=target,
            content=text,
            timestamp=time.time(),
        )
        self._recent_messages.append(reply_msg)
        logger.debug(f"Webhook回复 [{target}]: {text[:100]}")

    async def send_image(self, target: str, image_path: str, caption: str = "") -> None:
        """发送图片消息（webhook渠道存储元数据）
        
        Args:
            target: chat_id
            image_path: 图片路径
            caption: 图片描述
        """
        reply_msg = Message(
            channel="webhook",
            sender="system",
            sender_name="xuanji",
            chat_id=target,
            content=caption or f"[图片: {image_path}]",
            media_url=image_path,
            timestamp=time.time(),
        )
        self._recent_messages.append(reply_msg)
        logger.debug(f"Webhook图片 [{target}]: {image_path}")

    async def send_file(self, target: str, file_path: str, caption: str = "") -> None:
        """发送文件消息（webhook渠道存储元数据）
        
        Args:
            target: chat_id
            file_path: 文件路径
            caption: 文件描述
        """
        reply_msg = Message(
            channel="webhook",
            sender="system",
            sender_name="xuanji",
            chat_id=target,
            content=caption or f"[文件: {file_path}]",
            media_url=file_path,
            timestamp=time.time(),
        )
        self._recent_messages.append(reply_msg)
        logger.debug(f"Webhook文件 [{target}]: {file_path}")
    
    async def disconnect(self) -> None:
        """断开连接（停止HTTP服务器）"""
        self._connected = False
        if self._server:
            self._server.shutdown()
            self._server = None
        if self._server_thread:
            self._server_thread.join(timeout=5)
            self._server_thread = None
        logger.info("Webhook服务器已停止")
    
    def __repr__(self):
        status = "running" if self._connected else "stopped"
        return f"<WebhookChannel {self._host}:{self._port} {status}>"
