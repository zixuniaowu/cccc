"""
CCCC IM Bridge Port

Provides IM platform integration (Telegram, Slack, Discord) for CCCC groups.
Each group can bind to one IM bot for remote control and notifications.

Architecture:
- Bridge runs as independent process per group
- Inbound: IM messages → daemon API (send) → ledger
- Outbound: ledger events → filter → IM platform

Usage:
    cccc im set telegram --group <group_id>
    cccc im start --group <group_id>
    cccc im stop --group <group_id>
    cccc im status --group <group_id>
"""

from .bridge import IMBridge, start_bridge
from .subscribers import SubscriberManager

__all__ = ["IMBridge", "start_bridge", "SubscriberManager"]
