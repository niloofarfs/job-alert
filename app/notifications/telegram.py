from __future__ import annotations

import httpx

from app.notifications.protocol import NotificationError
from app.sources.errors import SourceNetworkError
from app.sources.http import request


class TelegramNotifier:
    channel = "telegram"

    def __init__(
        self,
        token: str,
        chat_id: str,
        client: httpx.AsyncClient,
        *,
        max_retries: int = 3,
    ) -> None:
        self._token = token
        self._chat_id = chat_id
        self._client = client
        self._max_retries = max_retries

    async def send(self, text: str) -> None:
        url = f"https://api.telegram.org/bot{self._token}/sendMessage"
        try:
            response = await request(
                self._client,
                "POST",
                url,
                max_retries=self._max_retries,
                json={
                    "chat_id": self._chat_id,
                    "text": text,
                    "disable_web_page_preview": True,
                },
            )
        except SourceNetworkError as exc:
            raise NotificationError("Telegram request failed") from exc
        if response.status_code >= 400:
            raise NotificationError(f"Telegram HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise NotificationError("Telegram returned non-JSON") from exc
        if isinstance(payload, dict) and payload.get("ok") is False:
            raise NotificationError("Telegram API returned ok=false")
