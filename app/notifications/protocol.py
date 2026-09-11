from __future__ import annotations

from typing import Protocol


class NotificationError(Exception):
    """Raised when a notifier fails after the job has already been persisted."""


class Notifier(Protocol):
    channel: str

    async def send(self, text: str) -> None:
        """Deliver a preformatted message. Must not raise on disabled no-op."""
