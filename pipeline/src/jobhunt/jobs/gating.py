"""Run the job-level gates (CSE, location, freshness) and record the outcome.

A job that fails any gate is `filtered`, with the first failure as
`filter_reason` ("location:region_restricted"). A job that passes or still needs
facts stays `open`; `gates_json` lists which gates are waiting for extraction.
Jobs from a source's first (bootstrap) run are marked `seeded`: they fill the
database but are never notified.
"""

import json
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from jobhunt.config import Config
from jobhunt.db import Database, Statement
from jobhunt.gates.base import GateResult
from jobhunt.gates.cse import CseGate
from jobhunt.gates.freshness import FreshnessGate
from jobhunt.gates.location import LocationGate
from jobhunt.sources.models import RawListing
from jobhunt.taxonomy.matcher import TitleMatch, TitleMatcher


@dataclass(frozen=True)
class GateItem:
    job_id: int
    listing: RawListing
    first_seen_at: datetime
    bootstrap: bool


@dataclass(frozen=True)
class GateOutcome:
    status: str  # open | filtered
    filter_reason: str | None
    results: tuple[GateResult, ...]
    match: TitleMatch
    seeded: bool

    @property
    def needs_facts(self) -> list[str]:
        return [r.gate for r in self.results if r.status == "needs_facts"]

    @property
    def location_evidence(self) -> str | None:
        return next(
            (r.evidence for r in self.results if r.gate == "location" and r.status == "pass"),
            None,
        )

    def gates_json(self) -> str:
        data: dict[str, object] = {r.gate: r.as_dict() for r in self.results}
        data["needs_facts"] = self.needs_facts
        data["seeded"] = self.seeded
        return json.dumps(data, ensure_ascii=False, sort_keys=True)


class JobGates:
    def __init__(self, config: Config, matcher: TitleMatcher | None = None) -> None:
        self._matcher = matcher or TitleMatcher(config)
        self._cse = CseGate(config)
        self._location = LocationGate(config)
        self._freshness = FreshnessGate(config)

    def evaluate(self, item: GateItem, now: datetime) -> GateOutcome:
        listing = item.listing
        match = self._matcher.match(listing.title)
        posted = listing.posted_at or _alert_time(listing)
        results = (
            self._cse(listing.title, match),
            self._location(listing),
            self._freshness(
                now=now,
                posted_at=posted,
                deadline=listing.deadline,
                first_seen_at=item.first_seen_at,
                bootstrap=item.bootstrap,
            ),
        )
        failed = next((r for r in results if r.status == "fail"), None)
        return GateOutcome(
            status="filtered" if failed else "open",
            filter_reason=f"{failed.gate}:{failed.reason}" if failed else None,
            results=results,
            match=match,
            seeded=item.bootstrap,
        )


async def apply_gates(
    db: Database, gates: JobGates, items: Sequence[GateItem], now: datetime, batch_size: int
) -> Counter[str]:
    """Evaluate and store the gates for `items`; returns counts by outcome."""
    counts: Counter[str] = Counter()
    for start in range(0, len(items), batch_size):
        statements: list[Statement] = []
        for item in items[start : start + batch_size]:
            outcome = gates.evaluate(item, now)
            statements += _statements(item.job_id, outcome)
            if outcome.status == "filtered":
                counts[f"filtered {outcome.filter_reason}"] += 1
            elif outcome.needs_facts:
                counts["open, needs facts"] += 1
            else:
                counts["open, passed"] += 1
        await db.batch(statements)
    return counts


def _statements(job_id: int, outcome: GateOutcome) -> list[Statement]:
    statements = [
        Statement(
            "UPDATE jobs SET status = ?, filter_reason = ?, remote_eligibility_evidence = ?,"
            " gates_json = ? WHERE id = ?",
            (
                outcome.status,
                outcome.filter_reason,
                outcome.location_evidence,
                outcome.gates_json(),
                job_id,
            ),
        ),
        Statement("DELETE FROM job_domains WHERE job_id = ?", (job_id,)),
    ]
    match = outcome.match
    if match.primary_domain and match.method != "none":
        domains = [(match.primary_domain, 1), *((d, 0) for d in match.secondary_domains)]
        statements += [
            Statement(
                "INSERT INTO job_domains (job_id, domain_slug, confidence, method, is_primary)"
                " VALUES (?, ?, ?, ?, ?)",
                (job_id, slug, match.confidence, match.method, primary),
            )
            for slug, primary in domains
        ]
    return statements


def _alert_time(listing: RawListing) -> datetime | None:
    """LinkedIn alerts carry no posting date; the alert's send time stands in for it."""
    sent = listing.extra.get("alert_sent_at")
    return datetime.fromisoformat(sent) if sent else None
