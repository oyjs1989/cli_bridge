"""Channel implementations for cli-bridge."""

from cli_bridge.channels.base import BaseChannel
from cli_bridge.channels.dingtalk import DingTalkChannel
from cli_bridge.channels.discord import DiscordChannel
from cli_bridge.channels.email import EmailChannel
from cli_bridge.channels.feishu import FeishuChannel
from cli_bridge.channels.manager import ChannelManager, get_channel_class, register_channel
from cli_bridge.channels.mochat import MochatChannel
from cli_bridge.channels.qq import QQChannel
from cli_bridge.channels.slack import SlackChannel

# Channel implementations
from cli_bridge.channels.telegram import TelegramChannel
from cli_bridge.channels.whatsapp import WhatsAppChannel

__all__ = [
    # Base
    "BaseChannel",
    "ChannelManager",
    "register_channel",
    "get_channel_class",
    # Implementations
    "TelegramChannel",
    "DiscordChannel",
    "FeishuChannel",
    "SlackChannel",
    "WhatsAppChannel",
    "DingTalkChannel",
    "QQChannel",
    "EmailChannel",
    "MochatChannel",
]
