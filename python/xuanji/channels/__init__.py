"""
xuanji 通信渠道系统 v2

提供统一的多渠道通信能力：
- Message: 统一消息数据类
- ChannelBase: 渠道基类（与plugin.py的ChannelPlugin对齐）
- ChannelRouter: 多渠道路由器（注册/回调/发送/回复/群发/智能路由）
- 28个渠道适配器（国内10个 + 国外14个 + 协议4个）

国内渠道:
    wechat, qq, dingtalk, feishu, wecom, weibo, douyin, bilibili, xiaohongshu, sms

国外渠道:
    telegram, discord, whatsapp, slack, signal, imessage,
    twitter, instagram, facebook, line, matrix, mattermost, teams, email

通用协议:
    irc, xmpp, websocket, webhook

用法:
    from xuanji.channels import ChannelRouter, Message
    
    router = ChannelRouter()
    
    @router.on_message
    async def handle(msg: Message):
        await router.reply(msg, f"收到: {msg.content}")
    
    # 注册渠道
    from xuanji.channels.telegram import TelegramChannel
    router.register("telegram", TelegramChannel())
    
    # 智能路由发送
    await router.smart_route("+8613800138000", "Hello!")
    
    # 启动
    await router.start()
"""

from xuanji.channels._base import ChannelBase, Message, ContentType, ChatType
from xuanji.channels.router import ChannelRouter, SmartRouter

# 国内渠道
from xuanji.channels.wechat import WeChatChannel
from xuanji.channels.qq import QQChannel
from xuanji.channels.dingtalk import DingTalkChannel
from xuanji.channels.feishu import FeishuChannel
from xuanji.channels.wecom import WeComChannel
from xuanji.channels.weibo import WeiboChannel
from xuanji.channels.douyin import DouyinChannel
from xuanji.channels.bilibili import BilibiliChannel
from xuanji.channels.xiaohongshu import XiaohongshuChannel
from xuanji.channels.sms import SMSChannel

# 国外渠道
from xuanji.channels.telegram import TelegramChannel
from xuanji.channels.discord import DiscordChannel
from xuanji.channels.whatsapp import WhatsAppChannel
from xuanji.channels.slack import SlackChannel
from xuanji.channels.signal import SignalChannel
from xuanji.channels.imessage import iMessageChannel
from xuanji.channels.twitter import TwitterChannel
from xuanji.channels.instagram import InstagramChannel
from xuanji.channels.facebook import FacebookChannel
from xuanji.channels.line import LINEChannel
from xuanji.channels.matrix import MatrixChannel
from xuanji.channels.mattermost import MattermostChannel
from xuanji.channels.teams import TeamsChannel
from xuanji.channels.email import EmailChannel

# 通用协议
from xuanji.channels.irc import IRCChannel
from xuanji.channels.xmpp import XMPPChannel
from xuanji.channels.websocket_channel import WebSocketChannel
from xuanji.channels.webhook import WebhookChannel

__all__ = [
    # 核心
    "ChannelBase",
    "ChannelRouter",
    "SmartRouter",
    "Message",
    "ContentType",
    "ChatType",
    # 国内渠道
    "WeChatChannel",
    "QQChannel",
    "DingTalkChannel",
    "FeishuChannel",
    "WeComChannel",
    "WeiboChannel",
    "DouyinChannel",
    "BilibiliChannel",
    "XiaohongshuChannel",
    "SMSChannel",
    # 国外渠道
    "TelegramChannel",
    "DiscordChannel",
    "WhatsAppChannel",
    "SlackChannel",
    "SignalChannel",
    "iMessageChannel",
    "TwitterChannel",
    "InstagramChannel",
    "FacebookChannel",
    "LINEChannel",
    "MatrixChannel",
    "MattermostChannel",
    "TeamsChannel",
    "EmailChannel",
    # 通用协议
    "IRCChannel",
    "XMPPChannel",
    "WebSocketChannel",
    "WebhookChannel",
]
