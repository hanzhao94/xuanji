"""
xuanji Signal渠道

支持Signal CLI（命令行接口）。
零外部依赖，使用标准库。需要Signal CLI作为外部依赖。

用法:
    from xuanji.channels.signal import SignalChannel
    
    channel = SignalChannel()
    await channel.connect({
        "phone_number": "+86...",
        "socket_path": "/tmp/signald-ipc.sock",  # signald IPC socket
    })
"""

import asyncio
import json
import logging
import os
import subprocess
import time
from typing import Any, Dict, Optional

from xuanji.channels._base import ChannelBase, ChatType, ContentType, Message

logger = logging.getLogger("xuanji.channels.signal")


class SignalChannel(ChannelBase):
    """Signal通信渠道

    通过Signal CLI或signald与Signal交互。
    需要安装signal-cli: https://github.com/asammler/signal-cli
    """

    name = "signal"
    description = "Signal渠道（Signal CLI / signald）"

    def __init__(self):
        super().__init__()
        self._config: Dict[str, Any] = {}
        self._phone_number: str = ""
        self._socket_path: str = ""
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._recent_messages: list = []
        self._process: Optional[subprocess.Popen] = None

    async def connect(self, config: Dict) -> None:
        self._config = config
        self._phone_number = config.get("phone_number", "")
        self._socket_path = config.get("socket_path", "")

        if not self._phone_number:
            raise ValueError("Signal渠道需要 phone_number")

        # 检查signal-cli是否可用
        try:
            result = await asyncio.create_subprocess_exec(
                "signal-cli", "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await result.wait()
        except FileNotFoundError:
            logger.warning("signal-cli未安装，Signal渠道可能无法工作")
            logger.info("安装方法: https://github.com/asammler/signal-cli")

        self._connected = True
        logger.info(f"Signal渠道已连接 ({self._phone_number})")

    async def listen(self) -> None:
        if not self._connected:
            raise RuntimeError("未连接")
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None

        # 启动signal-cli接收模式
        try:
            self._process = await asyncio.create_subprocess_exec(
                "signal-cli", "-u", self._phone_number, "receive",
                "-m",  # JSON mode
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            if self._process.stdout:
                while self._connected:
                    line = await self._process.stdout.readline()
                    if not line:
                        break
                    await self._parse_signal_message(line.decode("utf-8").strip())
        except Exception as e:
            logger.error(f"Signal监听异常: {e}")

    async def _parse_signal_message(self, line: str) -> None:
        """解析Signal消息"""
        try:
            if not line:
                return
            data = json.loads(line)
            envelope = data.get("envelope", {})

            source = envelope.get("source", "")
            content = envelope.get("data", {}).get("message", "")

            ct = ContentType.TEXT
            attachments = envelope.get("data", {}).get("attachments", [])
            if attachments:
                ct = ContentType.IMAGE  # 简化处理

            msg = Message(
                channel="signal",
                sender=source,
                sender_name=source,
                chat_id=source,
                chat_type=ChatType.PRIVATE,
                content_type=ct,
                content=content,
                timestamp=time.time(),
                raw=data,
            )
            self._recent_messages.append(msg)
            if self._loop:
                asyncio.run_coroutine_threadsafe(self.emit("message", msg), self._loop)
        except json.JSONDecodeError:
            pass
        except Exception as e:
            logger.error(f"解析Signal消息异常: {e}")

    async def send_text(self, target: str, text: str) -> None:
        """发送Signal消息"""
        try:
            proc = await asyncio.create_subprocess_exec(
                "signal-cli", "-u", self._phone_number, "send",
                target, "-m", text,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.wait()
            if proc.returncode != 0:
                logger.error(f"Signal发送失败 (code={proc.returncode})")
        except Exception as e:
            logger.error(f"Signal发送异常: {e}")

    async def send_image(self, target: str, image: Any) -> None:
        """发送图片"""
        if isinstance(image, str):
            try:
                proc = await asyncio.create_subprocess_exec(
                    "signal-cli", "-u", self._phone_number, "send",
                    target, "-a", image,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await proc.wait()
            except Exception as e:
                logger.error(f"Signal发送图片异常: {e}")
        else:
            logger.info(f"[Signal] 发送图片到 {target}（需要本地文件路径）")

    async def send_file(self, target: str, path: str) -> None:
        try:
            proc = await asyncio.create_subprocess_exec(
                "signal-cli", "-u", self._phone_number, "send",
                target, "-a", path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.wait()
        except Exception as e:
            logger.error(f"Signal发送文件异常: {e}")

    async def send_voice(self, target: str, audio: Any) -> None:
        logger.info(f"[Signal] 发送语音到 {target}")

    async def disconnect(self) -> None:
        self._connected = False
        if self._process:
            self._process.terminate()
            try:
                await self._process.wait()
            except Exception:
                pass
            self._process = None
        logger.info("Signal渠道已断开")
