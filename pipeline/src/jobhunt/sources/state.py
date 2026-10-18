"""Per-source scheduling and health, stored in `source_state`.

A source runs when `next_run_at` has passed (its `min_interval_minutes`
apart). Health alerts fire once, at the threshold: after N failed runs in a row,
or after M empty runs in a row from a source that has returned jobs before
(silent breakage). A source that recovers from failing reports that too.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal

from jobhunt.config.models import HealthSettings
from jobhunt.db import Database, timestamp
from jobhunt.sources.models import SourceResult

AlertKind = Literal["failing", "silent", "recovered"]


@dataclass(frozen=True)
class HealthAlert:
    source: str
    kind: AlertKind
    message: str


@dataclass
class SourceState:
    source: str
    state: dict[str, Any] = field(default_factory=dict)
    next_run_at: datetime | None = None
    last_success_at: datetime | None = None
    last_nonempty_at: datetime | None = None
    consecutive_failures: int = 0
    consecutive_empty: int = 0
    last_count: int | None = None
    bootstrapped: bool = False

    def due(self, now: datetime) -> bool:
        return self.next_run_at is None or self.next_run_at <= now


def _parse(value: Any) -> datetime | None:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")) if value else None


async def load_state(db: Database, source: str) -> SourceState:
    rows = await db.query("SELECT * FROM source_state WHERE source = ?", (source,))
    if not rows:
        return SourceState(source)
    row = rows[0]
    return SourceState(
        source=source,
        state=json.loads(str(row["state_json"])) if row["state_json"] else {},
        next_run_at=_parse(row["next_run_at"]),
        last_success_at=_parse(row["last_success_at"]),
        last_nonempty_at=_parse(row["last_nonempty_at"]),
        consecutive_failures=int(row["consecutive_failures"] or 0),
        consecutive_empty=int(row["consecutive_empty"] or 0),
        last_count=None if row["last_count"] is None else int(row["last_count"]),
        bootstrapped=bool(row["bootstrapped"]),
    )


async def save_state(db: Database, st: SourceState, now: datetime) -> None:
    await db.execute(
        """INSERT INTO source_state (source, state_json, next_run_at, last_success_at,
               last_nonempty_at, consecutive_failures, consecutive_empty, last_count,
               bootstrapped, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT (source) DO UPDATE SET
               state_json = excluded.state_json,
               next_run_at = excluded.next_run_at,
               last_success_at = excluded.last_success_at,
               last_nonempty_at = excluded.last_nonempty_at,
               consecutive_failures = excluded.consecutive_failures,
               consecutive_empty = excluded.consecutive_empty,
               last_count = excluded.last_count,
               bootstrapped = excluded.bootstrapped,
               updated_at = excluded.updated_at""",
        (
            st.source,
            json.dumps(st.state, sort_keys=True),
            timestamp(st.next_run_at),
            timestamp(st.last_success_at),
            timestamp(st.last_nonempty_at),
            st.consecutive_failures,
            st.consecutive_empty,
            st.last_count,
            int(st.bootstrapped),
            timestamp(now),
        ),
    )


def record_success(
    st: SourceState,
    result: SourceResult,
    new_state: dict[str, Any],
    now: datetime,
    interval: timedelta,
    health: HealthSettings,
) -> list[HealthAlert]:
    alerts = []
    if st.consecutive_failures >= health.failures_before_alert:
        alerts.append(HealthAlert(st.source, "recovered", f"{st.source} is working again"))
    st.consecutive_failures = 0
    st.state = new_state
    st.next_run_at = now + interval
    st.last_success_at = now
    st.bootstrapped = True
    if result.unchanged:
        return alerts
    count = len(result.listings)
    st.last_count = count
    if count:
        st.consecutive_empty = 0
        st.last_nonempty_at = now
        return alerts
    st.consecutive_empty += 1
    if st.consecutive_empty == health.empty_runs_before_alert and st.last_nonempty_at:
        alerts.append(
            HealthAlert(
                st.source,
                "silent",
                f"{st.source} returned no jobs {st.consecutive_empty} runs in a row; "
                f"last jobs at {timestamp(st.last_nonempty_at)}",
            )
        )
    return alerts


def record_failure(
    st: SourceState, error: str, now: datetime, interval: timedelta, health: HealthSettings
) -> list[HealthAlert]:
    st.consecutive_failures += 1
    st.next_run_at = now + interval
    if st.consecutive_failures == health.failures_before_alert:
        return [
            HealthAlert(
                st.source,
                "failing",
                f"{st.source} failed {st.consecutive_failures} runs in a row: {error}",
            )
        ]
    return []
