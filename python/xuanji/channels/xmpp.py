"""
xuanji XMPP渠道

支持XMPP/Jabber协议。
零外部依赖，使用socket/xml.etree标准库。

用法:
    from xuanji.channels.xmpp import XMPPChannel
    
    channel = XMPPChannel()
    await channel.connect({
        "jid": "bot@example.com",
        "password": "...",
        "server": "example.com",
        "rooms": ["general@conference.example.com"],
    })
"""

import asyncio
import logging
import ssl
import time
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional

from xuanji.channels._base import ChannelBase, ChatType, ContentType, Message

logger = logging.getLogger("xuanji.channels.xmpp")


class XMPPChannel(ChannelBase):
    """XMPP通信渠道

    标准XMPP/Jabber协议实现。
    """

    name = "xmpp"
    description = "XMPP渠道（Jabber协议）"

    NS_STREAM = "http://etherx.jabber.org/streams"
    NS_CLIENT = "jabber:client"
    NS_TLS = "urn:ietf:params:xml:ns:xmpp-tls"
    NS_SASL = "urn:ietf:params:xml:ns:xmpp-sasl"
    NS_BIND = "urn:ietf:params:xml:ns:xmpp-bind"
    NS_SESSION = "urn:ietf:params:xml:ns:xmpp-session"
    NS_ROSTER = "jabber:iq:roster"

    def __init__(self):
        super().__init__()
        self._config: Dict[str, Any] = {}
        self._jid: str = ""
        self._password: str = ""
        self._server: str = ""
        self._rooms: List[str] = []
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._recent_messages: list = []
        self._stream_id: str = ""
        self._full_jid: str = ""

    async def connect(self, config: Dict) -> None:
        self._config = config
        self._jid = config.get("jid", "")
        self._password = config.get("password", "")
        self._server = config.get("server", "")
        self._rooms = config.get("rooms", [])

        if not self._jid or not self._password:
            raise ValueError("XMPP需要 jid 和 password")

        if not self._server:
            self._server = self._jid.split("@")[-1] if "@" in self._jid else ""

        await self._connect_xmpp()
        self._connected = True
        logger.info(f"XMPP渠道已连接 ({self._jid})")

    async def _connect_xmpp(self) -> None:
        """连接XMPP服务器"""
        ctx = ssl.create_default_context()
        self._reader, self._writer = await asyncio.open_connection(
            self._server, 5222, ssl=ctx
        )

        # 发送stream open
        await self._send_xml(f'''<stream:stream
            to="{self._server}"
            xmlns="{self.NS_CLIENT}"
            xmlns:stream="{self.NS_STREAM}"
            version="1.0">''')

        # 读取stream features
        features = await self._read_xml()
        logger.debug(f"XMPP stream features: {features[:200]}")

        # STARTTLS（如果支持）
        if "<starttls" in features:
            await self._send_xml("<starttls xmlns='urn:ietf:params:xml:ns:xmpp-tls'/>")
            await self._read_xml()
            # 重新协商TLS
            self._writer.close()
            await self._writer.wait_closed()
            self._reader, self._writer = await asyncio.open_connection(
                self._server, 5222, ssl=ctx
            )
            await self._send_xml(f'''<stream:stream
                to="{self._server}"
                xmlns="{self.NS_CLIENT}"
                xmlns:stream="{self.NS_STREAM}"
                version="1.0">''')
            features = await self._read_xml()

        # SASL认证
        await self._sasl_auth(features)

        # 绑定资源
        await self._bind_resource()

        # 加入MUC房间
        for room in self._rooms:
            await self._join_muc(room)

    async def _send_xml(self, xml: str) -> None:
        """发送XML"""
        if self._writer:
            self._writer.write(xml.encode("utf-8"))
            await self._writer.drain()

    async def _read_xml(self) -> str:
        """读取XML数据"""
        if not self._reader:
            return ""
        data = b""
        while b"</stream:stream>" not in data and b"</stream>" not in data:
            chunk = await self._reader.read(4096)
            if not chunk:
                break
            data += chunk
            # 检查是否有完整的XML标签
            if b">" in data:
                break
        return data.decode("utf-8", errors="replace")

    async def _sasl_auth(self, features: str) -> None:
        """SASL认证"""
        # 提取支持的机制
        mechanisms = []
        if "<mechanisms" in features:
            import re
            mechanisms = re.findall(r"<mechanism>(.*?)</mechanism>", features)

        # 使用PLAIN认证
        if "PLAIN" in mechanisms:
            import base64
            auth_str = f"\x00{self._jid}\x00{self._password}"
            auth_b64 = base64.b64encode(auth_str.encode()).decode()
            await self._send_xml(f"<auth xmlns='{self.NS_SASL}' mechanism='PLAIN'>{auth_b64}</auth>")
            result = await self._read_xml()
            if "<success" not in result:
                raise ConnectionError(f"XMPP SASL认证失败: {result[:200]}")
            logger.info("XMPP SASL认证成功")

    async def _bind_resource(self) -> None:
        """绑定资源"""
        iq_id = "bind_1"
        await self._send_xml(f'''<iq id="{iq_id}" type="set">
            <bind xmlns="urn:ietf:params:xml:ns:xmpp-bind">
                <resource>xuanji</resource>
            </bind>
        </iq>''')
        result = await self._read_xml()
        logger.debug(f"XMPP bind result: {result[:200]}")

    async def _join_muc(self, room: str) -> None:
        """加入MUC房间"""
        nick = self._jid.split("@")[0]
        await self._send_xml(f'''<presence to="{room}/{nick}">
            <x xmlns="http://jabber.org/protocol/muc"/>
        </presence>''')
        logger.info(f"XMPP加入MUC: {room}")

    async def listen(self) -> None:
        if not self._connected or not self._reader:
            raise RuntimeError("未连接")
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None

        while self._connected:
            try:
                line = await self._read_xml()
                if not line:
                    raise ConnectionError("XMPP连接断开")
                self._handle_xmpp_stanza(line)
            except Exception as e:
                logger.debug(f"XMPP监听异常: {e}")
                await asyncio.sleep(2)
                try:
                    await self._connect_xmpp()
                except Exception:
                    pass

    def _handle_xmpp_stanza(self, data: str) -> None:
        """处理XMPP stanza"""
        try:
            if not data.strip():
                return

            # 处理<message>
            if "<message" in data:
                # 提取message元素
                start = data.find("<message")
                end = data.find("</message>") + len("</message>")
                if start >= 0 and end > start:
                    msg_xml = data[start:end]
                    self._parse_message(msg_xml)
        except Exception as e:
            logger.error(f"处理XMPP stanza异常: {e}")

    def _parse_message(self, xml: str) -> None:
        """解析XMPP消息"""
        try:
            root = ET.fromstring(xml)
            msg_type = root.get("type", "normal")
            from_jid = root.get("from", "")

            # 判断聊天类型
            if msg_type == "chat":
                ct = ChatType.PRIVATE
            elif msg_type == "groupchat":
                ct = ChatType.GROUP
            else:
                ct = ChatType.GROUP if "@" in from_jid and "conference" in from_jid else ChatType.PRIVATE

            # 提取内容
            body_elem = root.find(f"{{{self.NS_CLIENT}}}body")
            if body_elem is None:
                body_elem = root.find("body")

            content = ""
            if body_elem is not None and body_elem.text:
                content = body_elem.text

            # 提取发送者
            sender = from_jid.split("/")[0] if "/" in from_jid else from_jid
            sender_name = from_jid.split("/")[-1] if "/" in from_jid else sender

            xmpp_msg = Message(
                channel="xmpp",
                sender=sender,
                sender_name=sender_name,
                chat_id=from_jid,
                chat_type=ct,
                content_type=ContentType.TEXT,
                content=content,
                timestamp=time.time(),
                raw=xml,
            )
            self._recent_messages.append(xmpp_msg)
            if self._loop:
                asyncio.run_coroutine_threadsafe(self.emit("message", xmpp_msg), self._loop)
        except Exception as e:
            logger.error(f"解析XMPP消息异常: {e}")

    async def send_text(self, target: str, text: str) -> None:
        """发送XMPP消息"""
        # 判断目标类型
        if "@" in target and "conference" in target:
            msg_type = "groupchat"
        else:
            msg_type = "chat"

        import uuid
        msg_id = uuid.uuid4().hex[:12]
        await self._send_xml(f'''<message to="{target}" type="{msg_type}" id="{msg_id}">
            <body>{self._escape_xml(text)}</body>
        </message>''')

    def _escape_xml(self, text: str) -> str:
        """转义XML特殊字符"""
        return (text
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace('"', "&quot;")
                .replace("'", "&apos;"))

    async def send_image(self, target: str, image: Any) -> None:
        logger.info(f"[XMPP] 发送图片到 {target}")

    async def send_file(self, target: str, path: str) -> None:
        logger.info(f"[XMPP] 发送文件到 {target}: {path}")

    async def send_voice(self, target: str, audio: Any) -> None:
        logger.info(f"[XMPP] 发送语音到 {target}")

    async def disconnect(self) -> None:
        self._connected = False
        if self._writer:
            try:
                await self._send_xml("</stream:stream>")
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
            self._writer = None
            self._reader = None
        logger.info("XMPP渠道已断开")
