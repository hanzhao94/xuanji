"""
xuanji Email渠道

支持SMTP/IMAP收发邮件。
零外部依赖，使用smtplib/email/imaplib标准库。

用法:
    from xuanji.channels.email import EmailChannel
    
    channel = EmailChannel()
    await channel.connect({
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 587,
        "smtp_user": "...",
        "smtp_password": "...",
        "imap_host": "imap.gmail.com",
        "imap_port": 993,
        "imap_user": "...",
        "imap_password": "...",
    })
"""

import asyncio
import email
import imaplib
import logging
import smtplib
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from email.header import decode_header
from typing import Any, Dict, List, Optional

from xuanji.channels._base import ChannelBase, ChatType, ContentType, Message

logger = logging.getLogger("xuanji.channels.email")


class EmailChannel(ChannelBase):
    """Email通信渠道

    支持SMTP发送和IMAP接收邮件。
    """

    name = "email"
    description = "Email渠道（SMTP/IMAP）"

    def __init__(self):
        super().__init__()
        self._config: Dict[str, Any] = {}
        self._smtp_host: str = ""
        self._smtp_port: int = 587
        self._smtp_user: str = ""
        self._smtp_password: str = ""
        self._imap_host: str = ""
        self._imap_port: int = 993
        self._imap_user: str = ""
        self._imap_password: str = ""
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._recent_messages: list = []
        self._last_uid: str = ""

    async def connect(self, config: Dict) -> None:
        self._config = config
        self._smtp_host = config.get("smtp_host", "smtp.gmail.com")
        self._smtp_port = config.get("smtp_port", 587)
        self._smtp_user = config.get("smtp_user", "")
        self._smtp_password = config.get("smtp_password", "")
        self._imap_host = config.get("imap_host", "imap.gmail.com")
        self._imap_port = config.get("imap_port", 993)
        self._imap_user = config.get("imap_user", "")
        self._imap_password = config.get("imap_password", "")

        if not self._smtp_user or not self._smtp_password:
            raise ValueError("Email需要 smtp_user 和 smtp_password")

        self._connected = True
        logger.info(f"Email渠道已连接 ({self._smtp_user})")

    async def listen(self) -> None:
        if not self._connected:
            raise RuntimeError("未连接")
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None

        while self._connected:
            try:
                await self._check_new_emails()
            except Exception as e:
                logger.debug(f"Email检查异常: {e}")
            await asyncio.sleep(30)

    async def _check_new_emails(self) -> None:
        """检查新邮件"""
        try:
            # 使用IMAP4_SSL连接
            imap = imaplib.IMAP4_SSL(self._imap_host, self._imap_port)
            imap.login(self._imap_user, self._imap_password)
            imap.select("INBOX")

            # 搜索未读邮件
            status, messages = imap.search(None, "UNSEEN")
            if status != "OK":
                imap.logout()
                return

            msg_ids = messages[0].split()
            if not msg_ids:
                imap.logout()
                return

            # 获取最新邮件
            for msg_id in msg_ids[-10:]:  # 最多处理10封
                status, msg_data = imap.fetch(msg_id, "(RFC822)")
                if status != "OK":
                    continue
                raw_email = msg_data[0][1]
                msg = email.message_from_bytes(raw_email)
                self._process_email(msg, msg_id.decode())

            imap.logout()
        except Exception as e:
            logger.error(f"检查邮件异常: {e}")

    def _process_email(self, msg: email.message.Message, msg_id: str) -> None:
        """处理邮件"""
        try:
            # 解析发件人
            from_addr = msg.get("From", "")
            subject = self._decode_mime_header(msg.get("Subject", ""))

            ct = ContentType.TEXT
            content = ""
            media_url = ""

            # 解析邮件内容
            if msg.is_multipart():
                for part in msg.walk():
                    content_type = part.get_content_type()
                    if content_type == "text/plain":
                        payload = part.get_payload(decode=True)
                        if payload:
                            charset = part.get_content_charset() or "utf-8"
                            content += payload.decode(charset, errors="replace")
                            ct = ContentType.TEXT
                    elif content_type == "text/html":
                        payload = part.get_payload(decode=True)
                        if payload:
                            charset = part.get_content_charset() or "utf-8"
                            content += payload.decode(charset, errors="replace")
                    elif content_type.startswith("image/"):
                        ct = ContentType.IMAGE
                    elif content_type.startswith("audio/"):
                        ct = ContentType.AUDIO
                    elif content_type.startswith("video/"):
                        ct = ContentType.VIDEO
            else:
                payload = msg.get_payload(decode=True)
                if payload:
                    charset = msg.get_content_charset() or "utf-8"
                    content = payload.decode(charset, errors="replace")

            email_msg = Message(
                channel="email",
                sender=from_addr,
                sender_name=from_addr,
                chat_id=from_addr,
                chat_type=ChatType.PRIVATE,
                content_type=ct,
                content=f"{subject}\n\n{content}",
                media_url=media_url,
                reply_to=msg.get("Message-ID", ""),
                timestamp=time.time(),
                raw=msg,
            )
            self._recent_messages.append(email_msg)
            if self._loop:
                asyncio.run_coroutine_threadsafe(self.emit("message", email_msg), self._loop)
        except Exception as e:
            logger.error(f"处理邮件异常: {e}")

    def _decode_mime_header(self, header: str) -> str:
        """解码MIME编码的邮件头"""
        if not header:
            return ""
        decoded = decode_header(header)
        parts = []
        for data, charset in decoded:
            if isinstance(data, bytes):
                parts.append(data.decode(charset or "utf-8", errors="replace"))
            else:
                parts.append(data)
        return "".join(parts)

    async def send_text(self, target: str, text: str) -> None:
        """发送邮件
        
        Args:
            target: 收件人邮箱
            text: 邮件内容
        """
        try:
            msg = MIMEMultipart()
            msg["From"] = self._smtp_user
            msg["To"] = target
            msg["Subject"] = "xuanji Message"
            msg.attach(MIMEText(text, "plain", "utf-8"))

            server = smtplib.SMTP(self._smtp_host, self._smtp_port)
            server.starttls()
            server.login(self._smtp_user, self._smtp_password)
            server.send_message(msg)
            server.quit()
        except Exception as e:
            logger.error(f"发送邮件异常: {e}")

    async def send_image(self, target: str, image: Any) -> None:
        """发送带图片的邮件"""
        try:
            msg = MIMEMultipart()
            msg["From"] = self._smtp_user
            msg["To"] = target
            msg["Subject"] = "xuanji Image"

            if isinstance(image, str):
                with open(image, "rb") as f:
                    img_data = f.read()
                filename = image.split("/")[-1]
            else:
                img_data = image
                filename = "image.jpg"

            part = MIMEBase("image", filename.split(".")[-1])
            part.set_payload(img_data)
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", f"attachment; filename={filename}")
            msg.attach(part)

            server = smtplib.SMTP(self._smtp_host, self._smtp_port)
            server.starttls()
            server.login(self._smtp_user, self._smtp_password)
            server.send_message(msg)
            server.quit()
        except Exception as e:
            logger.error(f"发送图片邮件异常: {e}")

    async def send_file(self, target: str, path: str) -> None:
        """发送带附件的邮件"""
        try:
            msg = MIMEMultipart()
            msg["From"] = self._smtp_user
            msg["To"] = target
            msg["Subject"] = f"xuanji File: {path.split('/')[-1]}"

            with open(path, "rb") as f:
                file_data = f.read()

            part = MIMEBase("application", "octet-stream")
            part.set_payload(file_data)
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", f"attachment; filename={path.split('/')[-1]}")
            msg.attach(part)

            server = smtplib.SMTP(self._smtp_host, self._smtp_port)
            server.starttls()
            server.login(self._smtp_user, self._smtp_password)
            server.send_message(msg)
            server.quit()
        except Exception as e:
            logger.error(f"发送文件邮件异常: {e}")

    async def send_voice(self, target: str, audio: Any) -> None:
        """发送语音邮件"""
        try:
            msg = MIMEMultipart()
            msg["From"] = self._smtp_user
            msg["To"] = target
            msg["Subject"] = "xuanji Voice"

            if isinstance(audio, str):
                with open(audio, "rb") as f:
                    audio_data = f.read()
                filename = audio.split("/")[-1]
            else:
                audio_data = audio
                filename = "voice.ogg"

            part = MIMEBase("audio", "ogg")
            part.set_payload(audio_data)
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", f"attachment; filename={filename}")
            msg.attach(part)

            server = smtplib.SMTP(self._smtp_host, self._smtp_port)
            server.starttls()
            server.login(self._smtp_user, self._smtp_password)
            server.send_message(msg)
            server.quit()
        except Exception as e:
            logger.error(f"发送语音邮件异常: {e}")

    async def disconnect(self) -> None:
        self._connected = False
        logger.info("Email渠道已断开")
