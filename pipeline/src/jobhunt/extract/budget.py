"""LLMBudget (brief §4.3): pick the first model in the chain with room left.

Daily use per (day, provider, model) is kept in `llm_usage`, so every run sees
what earlier runs spent; per-minute use is tracked in memory for this run. A
model that answers 429 is skipped for the rest of the run. A share of each
daily limit (`daily_safety_margin`) is never used, and a model is skipped
before it would go over, not after.
"""

import asyncio
import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable, Collection
from datetime import datetime

from jobhunt.config.models import ChainEntry, Llm
from jobhunt.db import Database

Sleep = Callable[[float], Awaitable[None]]
WINDOW = 60.0


class LlmBudget:
    def __init__(
        self,
        settings: Llm,
        db: Database,
        now: datetime,
        usable: Callable[[ChainEntry], bool],
        clock: Callable[[], float] = time.monotonic,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._s = settings
        self._db = db
        self._day = now.strftime("%Y-%m-%d")
        self._usable = usable
        self._clock = clock
        self._sleep = sleep
        self._daily: dict[str, tuple[int, int]] = {}
        self._minute: defaultdict[str, deque[tuple[float, int]]] = defaultdict(deque)
        self._exhausted: set[str] = set()

    async def load(self) -> None:
        rows = await self._db.query(
            "SELECT provider, model, requests, tokens_in + tokens_out AS tokens"
            " FROM llm_usage WHERE day = ?",
            (self._day,),
        )
        self._daily = {
            f"{row['provider']!s}/{row['model']!s}": (
                int(str(row["requests"])),
                int(str(row["tokens"])),
            )
            for row in rows
        }

    def exhaust(self, entry: ChainEntry) -> None:
        self._exhausted.add(entry.key)

    async def acquire(self, tokens: int, skip: Collection[str] = ()) -> ChainEntry | None:
        """A model with room for one request of about `tokens` tokens, or None.
        Models in `skip` (keys) are not considered."""
        candidates = [
            e
            for e in self._s.chain
            if e.key not in skip and self._usable(e) and self._daily_room(e, tokens)
        ]
        deadline = self._clock() + self._s.request.max_wait_seconds
        while candidates:
            waits = []
            for entry in candidates:
                wait = self._minute_wait(entry, tokens)
                if wait == 0:
                    self._minute[entry.key].append((self._clock(), tokens))
                    return entry
                waits.append(wait)
            wait = min(waits)
            if self._clock() + wait > deadline:
                return None
            await self._sleep(wait)
        return None

    async def record(self, entry: ChainEntry, tokens_in: int, tokens_out: int) -> None:
        """Count a finished request; its real token count replaces the estimate
        `acquire` put in the per-minute window."""
        window = self._minute[entry.key]
        if window:
            window[-1] = (window[-1][0], tokens_in + tokens_out)
        requests, tokens = self._daily.get(entry.key, (0, 0))
        self._daily[entry.key] = (requests + 1, tokens + tokens_in + tokens_out)
        await self._db.execute(
            """INSERT INTO llm_usage (day, provider, model, requests, tokens_in, tokens_out)
               VALUES (?, ?, ?, 1, ?, ?)
               ON CONFLICT (day, provider, model) DO UPDATE SET
                   requests = requests + 1,
                   tokens_in = tokens_in + excluded.tokens_in,
                   tokens_out = tokens_out + excluded.tokens_out""",
            (self._day, entry.provider, entry.model, tokens_in, tokens_out),
        )

    def _daily_room(self, entry: ChainEntry, tokens: int) -> bool:
        if entry.key in self._exhausted:
            return False
        requests, used = self._daily.get(entry.key, (0, 0))
        keep = 1 - self._s.request.daily_safety_margin
        if entry.rpd is not None and requests + 1 > entry.rpd * keep:
            return False
        return entry.tpd is None or used + tokens <= entry.tpd * keep

    def _minute_wait(self, entry: ChainEntry, tokens: int) -> float:
        window = self._minute[entry.key]
        now = self._clock()
        while window and now - window[0][0] >= WINDOW:
            window.popleft()
        over_rpm = entry.rpm is not None and len(window) + 1 > entry.rpm
        over_tpm = entry.tpm is not None and sum(t for _, t in window) + tokens > entry.tpm
        if not (over_rpm or over_tpm):
            return 0.0
        if not window:
            return float("inf")  # one request alone is over the per-minute limit
        return WINDOW - (now - window[0][0])
