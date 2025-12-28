"""
IM Platform Adapters

Each adapter handles platform-specific communication:
- Telegram: Long-poll getUpdates
- Slack: Socket Mode + Web API
- Discord: Gateway
"""

from .base import IMAdapter
from .telegram import TelegramAdapter
from .slack import SlackAdapter
from .discord import DiscordAdapter

__all__ = ["IMAdapter", "TelegramAdapter", "SlackAdapter", "DiscordAdapter"]
