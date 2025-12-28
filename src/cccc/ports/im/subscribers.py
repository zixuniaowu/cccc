"""
Subscriber management for IM Bridge.

Manages which chats are subscribed to receive messages from a group.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


class Subscriber:
    """Represents a subscribed chat."""

    def __init__(
        self,
        chat_id: int,
        subscribed: bool = True,
        verbose: bool = True,
        subscribed_at: Optional[str] = None,
        chat_title: str = "",
    ):
        self.chat_id = chat_id
        self.subscribed = subscribed
        self.verbose = verbose  # Default True: show all messages including agent-to-agent
        self.subscribed_at = subscribed_at or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self.chat_title = chat_title

    def to_dict(self) -> Dict[str, Any]:
        return {
            "subscribed": self.subscribed,
            "verbose": self.verbose,
            "subscribed_at": self.subscribed_at,
            "chat_title": self.chat_title,
        }

    @classmethod
    def from_dict(cls, chat_id: int, data: Dict[str, Any]) -> "Subscriber":
        return cls(
            chat_id=chat_id,
            subscribed=bool(data.get("subscribed", True)),
            verbose=bool(data.get("verbose", True)),
            subscribed_at=data.get("subscribed_at"),
            chat_title=str(data.get("chat_title", "")),
        )


class SubscriberManager:
    """Manages subscribers for a group's IM bridge."""

    def __init__(self, state_dir: Path):
        self.state_dir = state_dir
        self.subscribers_path = state_dir / "im_subscribers.json"
        self._subscribers: Dict[int, Subscriber] = {}
        self._load()

    def _load(self) -> None:
        """Load subscribers from disk."""
        if not self.subscribers_path.exists():
            self._subscribers = {}
            return

        try:
            data = json.loads(self.subscribers_path.read_text(encoding="utf-8"))
            self._subscribers = {}
            for chat_id_str, sub_data in data.items():
                try:
                    chat_id = int(chat_id_str)
                    self._subscribers[chat_id] = Subscriber.from_dict(chat_id, sub_data)
                except (ValueError, TypeError):
                    continue
        except Exception:
            self._subscribers = {}

    def _save(self) -> None:
        """Save subscribers to disk."""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        data = {str(chat_id): sub.to_dict() for chat_id, sub in self._subscribers.items()}
        tmp = self.subscribers_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.subscribers_path)

    def subscribe(self, chat_id: int, chat_title: str = "") -> Subscriber:
        """Subscribe a chat. Returns the subscriber."""
        if chat_id in self._subscribers:
            sub = self._subscribers[chat_id]
            sub.subscribed = True
            if chat_title:
                sub.chat_title = chat_title
        else:
            sub = Subscriber(chat_id=chat_id, chat_title=chat_title)
            self._subscribers[chat_id] = sub
        self._save()
        return sub

    def unsubscribe(self, chat_id: int) -> bool:
        """Unsubscribe a chat. Returns True if was subscribed."""
        if chat_id in self._subscribers:
            self._subscribers[chat_id].subscribed = False
            self._save()
            return True
        return False

    def set_verbose(self, chat_id: int, verbose: bool) -> bool:
        """Set verbose mode for a chat. Returns True if chat exists."""
        if chat_id in self._subscribers:
            self._subscribers[chat_id].verbose = verbose
            self._save()
            return True
        return False

    def toggle_verbose(self, chat_id: int) -> Optional[bool]:
        """Toggle verbose mode. Returns new value or None if not subscribed."""
        if chat_id in self._subscribers:
            sub = self._subscribers[chat_id]
            sub.verbose = not sub.verbose
            self._save()
            return sub.verbose
        return None

    def is_subscribed(self, chat_id: int) -> bool:
        """Check if a chat is subscribed."""
        sub = self._subscribers.get(chat_id)
        return sub is not None and sub.subscribed

    def is_verbose(self, chat_id: int) -> bool:
        """Check if a chat has verbose mode enabled."""
        sub = self._subscribers.get(chat_id)
        return sub is not None and sub.verbose

    def get_subscriber(self, chat_id: int) -> Optional[Subscriber]:
        """Get subscriber info."""
        return self._subscribers.get(chat_id)

    def get_subscribed_chats(self) -> List[int]:
        """Get list of subscribed chat IDs."""
        return [chat_id for chat_id, sub in self._subscribers.items() if sub.subscribed]

    def count(self) -> int:
        """Count subscribed chats."""
        return len(self.get_subscribed_chats())
