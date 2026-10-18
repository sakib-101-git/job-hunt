"""Freshness gate (brief §6.8).

Fresh: posted at most `max_age_days` ago, and the deadline (if known) has not
passed, judged in the configured time zone. Without a posting date, a job is
fresh only with a future deadline, first seen within `max_age_days`, and not
from a source's first (bootstrap) run; before extraction has looked for a date in
the description, such a job `needs_facts`. The same check runs again at send time.
"""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from jobhunt.config import Config
from jobhunt.gates.base import GateResult

GATE = "freshness"


class FreshnessGate:
    def __init__(self, config: Config) -> None:
        self._max_age = timedelta(days=config.app.freshness.max_age_days)
        self._zone = ZoneInfo(config.app.timezone)

    def __call__(
        self,
        *,
        now: datetime,
        posted_at: datetime | None,
        deadline: date | None,
        first_seen_at: datetime,
        bootstrap: bool = False,
        facts_checked: bool = False,
    ) -> GateResult:
        today = now.astimezone(self._zone).date()
        if deadline is not None and deadline < today:
            return GateResult(GATE, "fail", "deadline_passed", deadline.isoformat())
        if posted_at is not None:
            if now - posted_at > self._max_age:
                return GateResult(GATE, "fail", "too_old", posted_at.isoformat())
            return GateResult(GATE, "pass", "posted_recently", posted_at.isoformat())
        if deadline is not None and not bootstrap and now - first_seen_at <= self._max_age:
            return GateResult(GATE, "pass", "future_deadline", deadline.isoformat())
        if not facts_checked:
            return GateResult(GATE, "needs_facts", "no_posting_date")
        reason = "bootstrap_without_date" if bootstrap else "no_posting_date"
        return GateResult(GATE, "fail", reason)
