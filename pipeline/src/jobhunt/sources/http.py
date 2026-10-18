"""Polite HTTP for sources: per-host concurrency and spacing, retries with
jittered backoff on 429/5xx/network errors (Retry-After honoured), fail-fast on
other 4xx, and conditional requests."""

import asyncio
import random
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

from jobhunt.config.models import HttpSettings

Sleep = Callable[[float], Awaitable[None]]


class FetchError(Exception):
    def __init__(self, url: str, message: str, status: int | None = None) -> None:
        super().__init__(f"{url}: {message}")
        self.url = url
        self.status = status


@dataclass(frozen=True)
class Validators:
    """HTTP validators from a previous response, for If-None-Match / If-Modified-Since."""

    etag: str | None = None
    last_modified: str | None = None

    def as_dict(self) -> dict[str, str]:
        return {k: v for k, v in (("etag", self.etag), ("last_modified", self.last_modified)) if v}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Validators":
        return cls(data.get("etag"), data.get("last_modified"))


@dataclass(frozen=True)
class Fetched:
    url: str
    status: int
    headers: Mapping[str, str]
    content: bytes

    @property
    def not_modified(self) -> bool:
        return self.status == httpx.codes.NOT_MODIFIED

    @property
    def validators(self) -> Validators:
        return Validators(self.headers.get("etag"), self.headers.get("last-modified"))

    def json(self) -> Any:
        try:
            return httpx.Response(self.status, content=self.content).json()
        except ValueError as exc:
            raise FetchError(self.url, "response is not JSON", self.status) from exc

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")


class _HostGate:
    def __init__(self, concurrency: int, min_delay: float) -> None:
        self.semaphore = asyncio.Semaphore(concurrency)
        self.min_delay = min_delay
        self.lock = asyncio.Lock()
        self.last_start = float("-inf")


class Fetcher:
    def __init__(
        self,
        settings: HttpSettings,
        client: httpx.AsyncClient | None = None,
        sleep: Sleep = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        self._settings = settings
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            http2=True, follow_redirects=True, timeout=settings.timeout_seconds
        )
        self._sleep = sleep
        self._clock = clock
        self._jitter = jitter
        self._gates: dict[str, _HostGate] = {}

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def get(
        self,
        url: str,
        *,
        params: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
        validators: Validators | None = None,
    ) -> Fetched:
        request_headers = {"User-Agent": self._settings.user_agent, **(headers or {})}
        if validators and validators.etag:
            request_headers["If-None-Match"] = validators.etag
        if validators and validators.last_modified:
            request_headers["If-Modified-Since"] = validators.last_modified

        attempt = 0
        while True:
            try:
                async with self._gate(url):
                    response = await self._client.get(url, params=params, headers=request_headers)
            except httpx.TransportError as exc:
                if attempt >= self._settings.max_retries:
                    raise FetchError(url, f"network error: {exc!r}") from exc
                await self._sleep(self._backoff(attempt, None))
                attempt += 1
                continue
            except httpx.HTTPError as exc:  # bad encoding, redirect loops: not retried
                raise FetchError(url, f"{type(exc).__name__}: {exc}") from exc

            status = response.status_code
            retryable = status == httpx.codes.TOO_MANY_REQUESTS or status >= 500
            if retryable and attempt < self._settings.max_retries:
                await self._sleep(self._backoff(attempt, response.headers.get("retry-after")))
                attempt += 1
                continue
            if status == httpx.codes.NOT_MODIFIED or response.is_success:
                return Fetched(str(response.url), status, dict(response.headers), response.content)
            raise FetchError(url, f"HTTP {status}", status)

    # --- helpers --------------------------------------------------------------

    def _gate(self, url: str) -> "_GateContext":
        host = urlsplit(url).hostname or ""
        gate = self._gates.get(host)
        if gate is None:
            override = self._settings.host_overrides.get(host)
            concurrency = (override and override.concurrency) or self._settings.per_host_concurrency
            delay = self._settings.per_host_min_delay_seconds
            if override and override.min_delay_seconds is not None:
                delay = override.min_delay_seconds
            gate = self._gates[host] = _HostGate(concurrency, delay)
        return _GateContext(gate, self._sleep, self._clock)

    def _backoff(self, attempt: int, retry_after: str | None) -> float:
        cap = self._settings.backoff_max_seconds
        if retry_after is not None:
            try:
                return min(max(float(retry_after), 0.0), cap)
            except ValueError:
                pass
        delay = min(cap, self._settings.backoff_base_seconds * 2**attempt)
        return float(delay / 2 + self._jitter() * delay / 2)


class _GateContext:
    """Holds a host slot and spaces request starts by the host's minimum delay."""

    def __init__(self, gate: _HostGate, sleep: Sleep, clock: Callable[[], float]) -> None:
        self._gate = gate
        self._sleep = sleep
        self._clock = clock

    async def __aenter__(self) -> None:
        await self._gate.semaphore.acquire()
        async with self._gate.lock:
            wait = self._gate.last_start + self._gate.min_delay - self._clock()
            if wait > 0:
                await self._sleep(wait)
            self._gate.last_start = self._clock()

    async def __aexit__(self, *_: object) -> None:
        self._gate.semaphore.release()
