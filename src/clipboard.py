"""Clipboard management for DropFlow with real-time sync, deduplication, and anti-spam protection."""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Deque, Dict, List, Optional, Tuple


@dataclass
class ClipboardItem:
    """Represents a synchronized clipboard entry."""
    id: str
    content: str
    sender_id: str
    content_type: str
    timestamp: str
    char_count: int
    line_count: int
    content_hash: str

    def to_dict(self) -> dict:
        return asdict(self)


class ClipboardManager:
    """
    Manages shared clipboard state with:
    - Content deduplication (prevents infinite echo loops)
    - Anti-spam rate limiting per sender
    - Payload size enforcement
    - Smart content type detection (URL, JSON, code, plain text)
    - In-memory history buffer
    """

    def __init__(
        self,
        max_history: int = 50,
        max_content_bytes: int = 5 * 1024 * 1024,  # 5 MB
        rate_limit_window: float = 5.0,           # seconds
        max_events_per_window: int = 15,          # max events per sender per window
        dedup_window_seconds: float = 10.0,       # duplicate suppression window
    ) -> None:
        self.max_history = max_history
        self.max_content_bytes = max_content_bytes
        self.rate_limit_window = rate_limit_window
        self.max_events_per_window = max_events_per_window
        self.dedup_window_seconds = dedup_window_seconds

        self._history: Deque[ClipboardItem] = deque(maxlen=max_history)
        # Store recent (hash, timestamp) to prevent rapid loops
        self._recent_hashes: Deque[Tuple[str, float]] = deque(maxlen=200)
        # Track sender timestamps for rate limiting: sender_id -> deque of timestamps
        self._sender_events: Dict[str, Deque[float]] = {}

    @staticmethod
    def detect_content_type(text: str) -> str:
        """Infer the semantic type of clipboard content."""
        trimmed = text.strip()
        if not trimmed:
            return "text"

        # Check URL
        if re.match(r"^https?://[^\s/$.?#].[^\s]*$", trimmed, re.IGNORECASE):
            return "url"

        # Check JSON
        if (trimmed.startswith("{") and trimmed.endswith("}")) or (
            trimmed.startswith("[") and trimmed.endswith("]")
        ):
            try:
                json.loads(trimmed)
                return "json"
            except Exception:
                pass

        # Check Code patterns (multiline or typical keywords/symbols)
        code_keywords = (
            "def ", "class ", "import ", "from ", "function", "const ", "let ",
            "var ", "return ", "if (", "for (", "while (", "public ", "private ",
            "SELECT ", "INSERT INTO", "CREATE TABLE", "<!DOCTYPE html>", "<html>"
        )
        if any(kw in trimmed for kw in code_keywords):
            return "code"

        if "\n" in text and any(token in text for token in (" = ", " == ", " => ", "->", "::", "{\n", "}\n")):
            return "code"

        return "text"

    @staticmethod
    def compute_hash(text: str) -> str:
        """Compute SHA-256 hash of normalized text."""
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _is_rate_limited(self, sender_id: str, now: float) -> bool:
        """Check whether sender exceeded rate limits."""
        if sender_id not in self._sender_events:
            self._sender_events[sender_id] = deque()

        events = self._sender_events[sender_id]
        cutoff = now - self.rate_limit_window
        while events and events[0] < cutoff:
            events.popleft()

        if len(events) >= self.max_events_per_window:
            return True

        events.append(now)
        return False

    def _is_duplicate_or_cycle(self, content_hash: str, now: float) -> bool:
        """Check if identical content was received within the deduplication window."""
        cutoff = now - self.dedup_window_seconds
        while self._recent_hashes and self._recent_hashes[0][1] < cutoff:
            self._recent_hashes.popleft()

        for h, t in reversed(self._recent_hashes):
            if h == content_hash:
                return True
        return False

    def process_incoming(
        self, content: str, sender_id: str
    ) -> Tuple[Optional[ClipboardItem], Optional[str]]:
        """
        Process an incoming clipboard event.

        Returns:
            (ClipboardItem, None) on success.
            (None, error_code) on rejection (e.g., 'rate_limited', 'duplicate', 'payload_too_large', 'empty').
        """
        if not content:
            return None, "empty"

        encoded = content.encode("utf-8")
        if len(encoded) > self.max_content_bytes:
            return None, "payload_too_large"

        now = time.time()

        if self._is_rate_limited(sender_id, now):
            return None, "rate_limited"

        content_hash = self.compute_hash(content)

        # Check duplicate/cyclic echo
        if self._is_duplicate_or_cycle(content_hash, now):
            return None, "duplicate"

        # Register hash to prevent cyclic echoes
        self._recent_hashes.append((content_hash, now))

        content_type = self.detect_content_type(content)
        lines = content.splitlines()

        item = ClipboardItem(
            id=str(uuid.uuid4()),
            content=content,
            sender_id=sender_id,
            content_type=content_type,
            timestamp=datetime.now(timezone.utc).isoformat(),
            char_count=len(content),
            line_count=len(lines) if lines else 1,
            content_hash=content_hash,
        )

        self._history.appendleft(item)
        return item, None

    def get_latest(self) -> Optional[ClipboardItem]:
        """Return the most recent clipboard item, if any."""
        return self._history[0] if self._history else None

    def get_history(self, limit: int = 50) -> List[ClipboardItem]:
        """Return history up to the specified limit."""
        return list(self._history)[:limit]

    def clear(self) -> None:
        """Clear all clipboard history and tracking caches."""
        self._history.clear()
        self._recent_hashes.clear()
        self._sender_events.clear()
