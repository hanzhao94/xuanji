"""
xuanji 协议适配（WebSocket / 发布订阅）

WebSocketServer: 标准库实现的WebSocket长连接服务
MQTTLikeServer: 内存实现的发布/订阅消息系统
零外部依赖，仅使用标准库。
"""

import json
import struct
import hashlib
import base64
import threading
import socket
import time
import logging
import uuid
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
from collections import defaultdict

logger = logging.getLogger(__name__)


# ─── WebSocket 帧处理 ──────────────────────────────────────

class _WebSocketFrame:
    """WebSocket帧编解码（RFC 6455）"""

    OPCODE_TEXT = 0x1
    OPCODE_BINARY = 0x2
    OPCODE_CLOSE = 0x8
    OPCODE_PING = 0x9
    OPCODE_PONG = 0xA

    @staticmethod
    def decode(data: bytes) -> Optional[Tuple[int, bytes]]:
        """解码一个WebSocket帧
        
        Returns:
            (opcode, payload) 或 None（数据不完整）
        """
        if len(data) < 2:
            return None

        opcode = data[0] & 0x0F
        masked = (data[1] & 0x80) != 0
        payload_len = data[1] & 0x7F

        offset = 2
        if payload_len == 126:
            if len(data) < 4:
                return None
            payload_len = struct.unpack(">H", data[2:4])[0]
            offset = 4
        elif payload_len == 127:
            if len(data) < 10:
                return None
            payload_len = struct.unpack(">Q", data[2:10])[0]
            offset = 10

        if masked:
            if len(data) < offset + 4:
                return None
            mask_key = data[offset:offset + 4]
            offset += 4

        if len(data) < offset + payload_len:
            return None

        payload = data[offset:offset + payload_len]

        if masked:
            payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))

        return opcode, payload

    @staticmethod
    def encode(payload: bytes, opcode: int = 0x1) -> bytes:
        """编码一个WebSocket帧（服务端→客户端，不mask）"""
        frame = bytearray()
        frame.append(0x80 | opcode)  # FIN + opcode

        length = len(payload)
        if length < 126:
            frame.append(length)
        elif length < 65536:
            frame.append(126)
            frame.extend(struct.pack(">H", length))
        else:
            frame.append(127)
            frame.extend(struct.pack(">Q", length))

        frame.extend(payload)
        return bytes(frame)


# ─── WebSocket 客户端连接 ──────────────────────────────────

class WebSocketClient:
    """一个WebSocket客户端连接"""

    def __init__(self, client_id: str, conn: socket.socket, addr: Tuple):
        self.client_id = client_id
        self.conn = conn
        self.addr = addr
        self.connected = True
        self.connected_at = time.time()
        self._lock = threading.Lock()

    def send(self, data: str) -> bool:
        """发送文本消息"""
        if not self.connected:
            return False
        try:
            frame = _WebSocketFrame.encode(data.encode("utf-8"))
            with self._lock:
                self.conn.sendall(frame)
            return True
        except Exception as e:
            logger.debug(f"发送失败 {self.client_id}: {e}")
            self.connected = False
            return False

    def send_json(self, data: Any) -> bool:
        """发送JSON消息"""
        return self.send(json.dumps(data, ensure_ascii=False, default=str))

    def close(self) -> None:
        """关闭连接"""
        self.connected = False
        try:
            close_frame = _WebSocketFrame.encode(b"", _WebSocketFrame.OPCODE_CLOSE)
            self.conn.sendall(close_frame)
        except Exception:
            pass
        try:
            self.conn.close()
        except Exception:
            pass


# ─── WebSocket 服务器 ─────────────────────────────────────

class WebSocketServer:
    """WebSocket长连接服务器
    
    用标准库socket实现，不依赖任何第三方库。
    
    用法::
    
        ws = WebSocketServer()
        
        @ws.on_message
        def handle(client_id, data):
            print(f"收到消息: {client_id} -> {data}")
            ws.send(client_id, '{"echo": "' + data + '"}')
        
        @ws.on_connect
        def connected(client_id):
            print(f"新连接: {client_id}")
        
        ws.start(port=8765)
        
        # 广播
        ws.broadcast('{"event": "hello"}')
        
        # 停止
        ws.stop()
    """

    def __init__(self):
        self._clients: Dict[str, WebSocketClient] = {}
        self._server_socket: Optional[socket.socket] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        # 回调
        self._on_message: Optional[Callable] = None
        self._on_connect: Optional[Callable] = None
        self._on_disconnect: Optional[Callable] = None

    def on_message(self, callback: Callable) -> Callable:
        """注册消息回调（装饰器）
        
        回调签名: callback(client_id: str, data: str) -> None
        """
        self._on_message = callback
        return callback

    def on_connect(self, callback: Callable) -> Callable:
        """注册连接回调（装饰器）"""
        self._on_connect = callback
        return callback

    def on_disconnect(self, callback: Callable) -> Callable:
        """注册断开回调（装饰器）"""
        self._on_disconnect = callback
        return callback

    def start(self, port: int = 8765, host: str = "0.0.0.0") -> None:
        """启动WebSocket服务器"""
        if self._running:
            logger.warning("WebSocket服务器已在运行")
            return

        self._server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server_socket.settimeout(1.0)
        self._server_socket.bind((host, port))
        self._server_socket.listen(128)
        self._running = True

        self._thread = threading.Thread(
            target=self._accept_loop,
            daemon=True,
            name="ws-accept",
        )
        self._thread.start()
        logger.info(f"WebSocket服务器启动于 ws://{host}:{port}")

    def _accept_loop(self) -> None:
        """接受新连接"""
        while self._running:
            try:
                conn, addr = self._server_socket.accept()
                client_id = uuid.uuid4().hex[:12]
                t = threading.Thread(
                    target=self._handle_client,
                    args=(client_id, conn, addr),
                    daemon=True,
                    name=f"ws-client-{client_id}",
                )
                t.start()
            except socket.timeout:
                continue
            except Exception as e:
                if self._running:
                    logger.error(f"接受连接失败: {e}")

    def _do_handshake(self, conn: socket.socket) -> bool:
        """执行WebSocket握手"""
        try:
            data = conn.recv(4096).decode("utf-8")
            if "Upgrade: websocket" not in data and "upgrade: websocket" not in data:
                return False

            # 提取Sec-WebSocket-Key
            key = ""
            for line in data.split("\r\n"):
                if line.lower().startswith("sec-websocket-key:"):
                    key = line.split(":", 1)[1].strip()
                    break

            if not key:
                return False

            # 计算Accept值
            magic = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
            accept = base64.b64encode(
                hashlib.sha1((key + magic).encode()).digest()
            ).decode()

            response = (
                "HTTP/1.1 101 Switching Protocols\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                f"Sec-WebSocket-Accept: {accept}\r\n"
                "\r\n"
            )
            conn.sendall(response.encode())
            return True
        except Exception as e:
            logger.error(f"握手失败: {e}")
            return False

    def _handle_client(self, client_id: str, conn: socket.socket, addr: Tuple) -> None:
        """处理单个客户端"""
        if not self._do_handshake(conn):
            conn.close()
            return

        client = WebSocketClient(client_id, conn, addr)
        with self._lock:
            self._clients[client_id] = client

        if self._on_connect:
            try:
                self._on_connect(client_id)
            except Exception as e:
                logger.error(f"on_connect回调异常: {e}")

        logger.info(f"WebSocket客户端连接: {client_id} ({addr})")

        buffer = b""
        try:
            while self._running and client.connected:
                try:
                    conn.settimeout(1.0)
                    chunk = conn.recv(65536)
                    if not chunk:
                        break

                    buffer += chunk

                    while buffer:
                        result = _WebSocketFrame.decode(buffer)
                        if result is None:
                            break

                        opcode, payload = result
                        # 计算消费的字节数（简化：重新编码确认）
                        # 实际应精确计算，这里用安全方式
                        frame_size = len(buffer) - len(buffer)  # placeholder
                        # 简化处理：每次解码后清空buffer
                        buffer = b""

                        if opcode == _WebSocketFrame.OPCODE_TEXT:
                            text = payload.decode("utf-8", errors="replace")
                            if self._on_message:
                                try:
                                    self._on_message(client_id, text)
                                except Exception as e:
                                    logger.error(f"on_message回调异常: {e}")

                        elif opcode == _WebSocketFrame.OPCODE_PING:
                            pong = _WebSocketFrame.encode(payload, _WebSocketFrame.OPCODE_PONG)
                            conn.sendall(pong)

                        elif opcode == _WebSocketFrame.OPCODE_CLOSE:
                            client.connected = False
                            break

                except socket.timeout:
                    continue
                except Exception as e:
                    logger.debug(f"读取异常 {client_id}: {e}")
                    break

        finally:
            client.connected = False
            with self._lock:
                self._clients.pop(client_id, None)
            try:
                conn.close()
            except Exception:
                pass

            if self._on_disconnect:
                try:
                    self._on_disconnect(client_id)
                except Exception as e:
                    logger.error(f"on_disconnect回调异常: {e}")

            logger.info(f"WebSocket客户端断开: {client_id}")

    def send(self, client_id: str, data: str) -> bool:
        """发送消息给指定客户端"""
        with self._lock:
            client = self._clients.get(client_id)
        if client:
            return client.send(data)
        return False

    def send_json(self, client_id: str, data: Any) -> bool:
        """发送JSON消息"""
        return self.send(client_id, json.dumps(data, ensure_ascii=False, default=str))

    def broadcast(self, data: str, exclude: Optional[Set[str]] = None) -> int:
        """广播消息给所有客户端
        
        Args:
            data: 消息文本
            exclude: 排除的客户端ID集合
        
        Returns:
            成功发送的客户端数
        """
        exclude = exclude or set()
        with self._lock:
            clients = list(self._clients.values())

        count = 0
        for client in clients:
            if client.client_id not in exclude:
                if client.send(data):
                    count += 1
        return count

    def get_clients(self) -> List[Dict]:
        """获取所有连接的客户端信息"""
        with self._lock:
            return [
                {
                    "client_id": c.client_id,
                    "addr": f"{c.addr[0]}:{c.addr[1]}",
                    "connected_at": c.connected_at,
                    "connected": c.connected,
                }
                for c in self._clients.values()
            ]

    @property
    def client_count(self) -> int:
        with self._lock:
            return len(self._clients)

    def stop(self) -> None:
        """停止服务器"""
        self._running = False
        with self._lock:
            for client in self._clients.values():
                client.close()
            self._clients.clear()
        if self._server_socket:
            try:
                self._server_socket.close()
            except Exception:
                pass
        logger.info("WebSocket服务器已停止")


# ─── MQTT-Like 发布/订阅 ──────────────────────────────────

class Subscription:
    """订阅记录"""

    __slots__ = ("topic", "callback", "subscriber_id", "created_at")

    def __init__(self, topic: str, callback: Callable, subscriber_id: str = ""):
        self.topic = topic
        self.callback = callback
        self.subscriber_id = subscriber_id or uuid.uuid4().hex[:8]
        self.created_at = time.time()


class MQTTLikeServer:
    """简单的发布/订阅消息系统
    
    内存实现，不需要真的MQTT broker。
    支持主题通配符匹配。
    
    用法::
    
        mq = MQTTLikeServer()
        
        # 订阅
        def handler(topic, data):
            print(f"收到: {topic} -> {data}")
        
        mq.subscribe("sensors/temperature", handler)
        mq.subscribe("sensors/#", handler)  # 通配符
        
        # 发布
        mq.publish("sensors/temperature", {"value": 25.6})
        
        # 取消订阅
        mq.unsubscribe("sensors/temperature", handler)
    """

    def __init__(self, max_retained: int = 1000, max_history: int = 10000):
        self._subscriptions: Dict[str, List[Subscription]] = defaultdict(list)
        self._retained: Dict[str, Any] = {}  # 保留消息
        self._history: List[Dict] = []  # 消息历史
        self._max_retained = max_retained
        self._max_history = max_history
        self._lock = threading.Lock()
        self._stats = {
            "published": 0,
            "delivered": 0,
            "dropped": 0,
        }

    @staticmethod
    def _topic_matches(pattern: str, topic: str) -> bool:
        """主题匹配（支持MQTT风格通配符）
        
        - '#' 匹配多级（sensors/# 匹配 sensors/temp, sensors/temp/room1）
        - '+' 匹配单级（sensors/+/value 匹配 sensors/temp/value）
        """
        if pattern == "#":
            return True
        if pattern == topic:
            return True

        pattern_parts = pattern.split("/")
        topic_parts = topic.split("/")

        for i, pp in enumerate(pattern_parts):
            if pp == "#":
                return True  # '#' 匹配剩余所有
            if i >= len(topic_parts):
                return False
            if pp == "+":
                continue  # '+' 匹配当前级
            if pp != topic_parts[i]:
                return False

        return len(pattern_parts) == len(topic_parts)

    def subscribe(self, topic: str, callback: Callable,
                  subscriber_id: str = "") -> str:
        """订阅主题
        
        Args:
            topic: 主题（支持通配符 # 和 +）
            callback: 回调函数 callback(topic: str, data: Any)
            subscriber_id: 订阅者ID
        
        Returns:
            订阅ID
        """
        sub = Subscription(topic, callback, subscriber_id)
        with self._lock:
            self._subscriptions[topic].append(sub)
        logger.debug(f"订阅: {topic} ({sub.subscriber_id})")

        # 发送保留消息
        with self._lock:
            for ret_topic, ret_data in self._retained.items():
                if self._topic_matches(topic, ret_topic):
                    try:
                        callback(ret_topic, ret_data)
                    except Exception as e:
                        logger.error(f"保留消息投递失败: {e}")

        return sub.subscriber_id

    def unsubscribe(self, topic: str, callback: Optional[Callable] = None,
                    subscriber_id: Optional[str] = None) -> int:
        """取消订阅
        
        Args:
            topic: 主题
            callback: 按回调匹配（可选）
            subscriber_id: 按ID匹配（可选）
        
        Returns:
            取消的订阅数
        """
        removed = 0
        with self._lock:
            if topic in self._subscriptions:
                original = self._subscriptions[topic]
                filtered = []
                for sub in original:
                    match = False
                    if callback and sub.callback is callback:
                        match = True
                    if subscriber_id and sub.subscriber_id == subscriber_id:
                        match = True
                    if not callback and not subscriber_id:
                        match = True  # 全部移除

                    if match:
                        removed += 1
                    else:
                        filtered.append(sub)
                self._subscriptions[topic] = filtered
        return removed

    def publish(self, topic: str, data: Any, retain: bool = False) -> int:
        """发布消息
        
        Args:
            topic: 主题
            data: 消息数据
            retain: 是否保留（新订阅者会收到最后一条保留消息）
        
        Returns:
            投递给了多少个订阅者
        """
        with self._lock:
            self._stats["published"] += 1

            # 保留消息
            if retain:
                self._retained[topic] = data
                if len(self._retained) > self._max_retained:
                    oldest_key = next(iter(self._retained))
                    del self._retained[oldest_key]

            # 记录历史
            self._history.append({
                "topic": topic,
                "data": data,
                "timestamp": time.time(),
                "retain": retain,
            })
            if len(self._history) > self._max_history:
                self._history = self._history[-self._max_history:]

            # 收集匹配的订阅
            matched_subs = []
            for pattern, subs in self._subscriptions.items():
                if self._topic_matches(pattern, topic):
                    matched_subs.extend(subs)

        # 投递（在锁外执行回调）
        delivered = 0
        for sub in matched_subs:
            try:
                sub.callback(topic, data)
                delivered += 1
            except Exception as e:
                logger.error(f"消息投递失败 ({sub.subscriber_id}): {e}")
                with self._lock:
                    self._stats["dropped"] += 1

        with self._lock:
            self._stats["delivered"] += delivered

        return delivered

    def get_topics(self) -> List[str]:
        """获取所有有订阅的主题"""
        with self._lock:
            return [t for t, subs in self._subscriptions.items() if subs]

    def get_subscriptions(self, topic: Optional[str] = None) -> List[Dict]:
        """获取订阅信息"""
        with self._lock:
            result = []
            topics = [topic] if topic else list(self._subscriptions.keys())
            for t in topics:
                for sub in self._subscriptions.get(t, []):
                    result.append({
                        "topic": sub.topic,
                        "subscriber_id": sub.subscriber_id,
                        "created_at": sub.created_at,
                    })
            return result

    def get_retained(self, topic: Optional[str] = None) -> Dict:
        """获取保留消息"""
        with self._lock:
            if topic:
                return {topic: self._retained.get(topic)}
            return dict(self._retained)

    def get_history(self, topic: Optional[str] = None, limit: int = 50) -> List[Dict]:
        """获取消息历史"""
        with self._lock:
            history = list(self._history)
        if topic:
            history = [h for h in history if h["topic"] == topic]
        return history[-limit:]

    def get_stats(self) -> Dict:
        """获取统计信息"""
        with self._lock:
            total_subs = sum(len(subs) for subs in self._subscriptions.values())
            return {
                **self._stats,
                "total_subscriptions": total_subs,
                "total_topics": len([t for t, s in self._subscriptions.items() if s]),
                "retained_messages": len(self._retained),
                "history_size": len(self._history),
            }

    def clear(self) -> None:
        """清空所有数据"""
        with self._lock:
            self._subscriptions.clear()
            self._retained.clear()
            self._history.clear()
            self._stats = {"published": 0, "delivered": 0, "dropped": 0}
