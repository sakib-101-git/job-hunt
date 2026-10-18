"""Telegram Bot API client for sending alerts, within Telegram's limits.

A token bucket keeps the whole bot under `messages_per_second` and a per-chat
gap keeps each chat under one message a second (Bot FAQ). A 429 waits the
`retry_after` Telegram gives; 5xx and network errors back off; a 403 means the
user blocked the bot. Messages use HTML with link previews off.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from jobhunt.config.models import NotificationSettings

Sleep = Callable[[float], Awaitable[None]]
Keyboard = list[list[dict[str, str]]]


class TelegramError(Exception):
    """Telegram refused the message (bad request, chat not found, ...)."""


class BlockedError(TelegramError):
    """HTTP 403: the user blocked the bot or deleted their account."""


class TelegramClient:
    def __init__(
        self,
        settings: NotificationSettings,
        token: str,
        client: httpx.AsyncClient | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._s = settings
        self._url = f"{settings.telegram_api_base_url.rstrip('/')}/bot{token}"
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=settings.request_timeout_seconds)
        self._clock = clock
        self._sleep = sleep
        self._lock = asyncio.Lock()
        self._next_global = 0.0
        self._next_chat: dict[int, float] = {}

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def send_message(self, chat_id: int, text: str, keyboard: Keyboard | None = None) -> int:
        """Send `text` (HTML) and return Telegram's message id."""
        body: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
        }
        if keyboard:
            body["reply_markup"] = {"inline_keyboard": keyboard}
        result = await self._call("sendMessage", body, chat_id)
        return int(result["message_id"])

    async def _call(self, method: str, body: dict[str, Any], chat_id: int) -> dict[str, Any]:
        attempt = 0
        while True:
            await self._wait_turn(chat_id)
            try:
                response = await self._client.post(f"{self._url}/{method}", json=body)
                payload: dict[str, Any] = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                if attempt >= self._s.retries_per_send:
                    raise TelegramError(f"network error: {type(exc).__name__}") from exc
                await self._sleep(self._s.retry_backoff_seconds * 2**attempt)
                attempt += 1
                continue
            if payload.get("ok"):
                result: dict[str, Any] = payload["result"]
                return result
            code = int(payload.get("error_code") or response.status_code)
            description = str(payload.get("description") or f"HTTP {code}")
            if code == httpx.codes.FORBIDDEN:
                raise BlockedError(description)
            retryable = code == httpx.codes.TOO_MANY_REQUESTS or code >= 500
            if not retryable or attempt >= self._s.retries_per_send:
                raise TelegramError(description)
            retry_after = (payload.get("parameters") or {}).get("retry_after")
            delay = (
                float(retry_after) if retry_after else self._s.retry_backoff_seconds * 2**attempt
            )
            await self._sleep(delay)
            attempt += 1

    async def _wait_turn(self, chat_id: int) -> None:
        async with self._lock:
            now = self._clock()
            start = max(now, self._next_global, self._next_chat.get(chat_id, 0.0))
            if start > now:
                await self._sleep(start - now)
            self._next_global = start + 1 / self._s.messages_per_second
            self._next_chat[chat_id] = start + self._s.per_chat_interval_seconds
