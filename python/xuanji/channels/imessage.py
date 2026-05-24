"""
xuanji iMessage渠道

支持通过AppleScript/macOS自动化发送iMessage。
仅限macOS平台。

用法:
    from xuanji.channels.imessage import iMessageChannel
    
    channel = iMessageChannel()
    await channel.connect({})
"""

import asyncio
import json
import logging
import platform
import subprocess
import time
from typing import Any, Dict, Optional

from xuanji.channels._base import ChannelBase, ChatType, ContentType, Message

logger = logging.getLogger("xuanji.channels.imessage")


class iMessageChannel(ChannelBase):
    """iMessage通信渠道

    通过AppleScript与macOS Messages.app交互。
    仅限macOS平台。
    """

    name = "imessage"
    description = "iMessage渠道（AppleScript/macOS）"

    def __init__(self):
        super().__init__()
        self._config: Dict[str, Any] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._recent_messages: list = []
        self._is_macos = platform.system() == "Darwin"

    async def connect(self, config: Dict) -> None:
        self._config = config

        if not self._is_macos:
            logger.warning("iMessage渠道仅在macOS上可用")

        self._connected = True
        logger.info("iMessage渠道已连接")

    async def listen(self) -> None:
        if not self._connected:
            raise RuntimeError("未连接")
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None
        # iMessage通过AppleScript轮询
        while self._connected:
            if self._is_macos:
                await self._poll_messages()
            await asyncio.sleep(5)

    async def _poll_messages(self) -> None:
        """轮询Messages.app获取新消息"""
        script = '''
        tell application "Messages"
            set theBuddy to name of every buddy
            return theBuddy as text
        end tell
        '''
        try:
            proc = await asyncio.create_subprocess_exec(
                "osascript", "-e", script,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            # 解析消息...
        except Exception as e:
            logger.debug(f"iMessage轮询异常: {e}")

    async def send_text(self, target: str, text: str) -> None:
        """发送iMessage
        
        Args:
            target: 手机号或邮箱 (e.g., "+8613800138000" or "user@icloud.com")
            text: 消息内容
        """
        if not self._is_macos:
            logger.error("iMessage仅在macOS上可用")
            return

        # 转义AppleScript字符串
        escaped_text = text.replace('"', '\\"').replace('\\', '\\\\')
        script = f'''
        tell application "Messages"
            set targetBuddy to "{target}"
            set targetService to 1st account whose service type = iMessage
            set theBuddy to buddy targetBuddy of targetService
            send "{escaped_text}" to theBuddy
        end tell
        '''
        try:
            proc = await asyncio.create_subprocess_exec(
                "osascript", "-e", script,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.wait()
            if proc.returncode != 0:
                logger.error(f"iMessage发送失败")
        except Exception as e:
            logger.error(f"iMessage发送异常: {e}")

    async def send_image(self, target: str, image: Any) -> None:
        """发送图片"""
        if not self._is_macos:
            logger.error("iMessage仅在macOS上可用")
            return
        if isinstance(image, str):
            script = f'''
            tell application "Messages"
                set targetBuddy to "{target}"
                set targetService to 1st account whose service type = iMessage
                set theBuddy to buddy targetBuddy of targetService
                send POSIX file "{image}" to theBuddy
            end tell
            '''
            try:
                proc = await asyncio.create_subprocess_exec(
                    "osascript", "-e", script,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await proc.wait()
            except Exception as e:
                logger.error(f"iMessage发送图片异常: {e}")

    async def send_file(self, target: str, path: str) -> None:
        if not self._is_macos:
            logger.error("iMessage仅在macOS上可用")
            return
        script = f'''
        tell application "Messages"
            set targetBuddy to "{target}"
            set targetService to 1st account whose service type = iMessage
            set theBuddy to buddy targetBuddy of targetService
            send POSIX file "{path}" to theBuddy
        end tell
        '''
        try:
            proc = await asyncio.create_subprocess_exec(
                "osascript", "-e", script,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.wait()
        except Exception as e:
            logger.error(f"iMessage发送文件异常: {e}")

    async def send_voice(self, target: str, audio: Any) -> None:
        logger.info(f"[iMessage] 发送语音到 {target}")

    async def disconnect(self) -> None:
        self._connected = False
        logger.info("iMessage渠道已断开")
