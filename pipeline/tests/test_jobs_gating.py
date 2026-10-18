import json
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from jobhunt.config import Config
from jobhunt.db import SqliteBackend, apply_migrations
from jobhunt.jobs.gating import GateItem, JobGates, apply_gates
from jobhunt.jobs.rows import listing_from_row
from jobhunt.jobs.store import store_jobs
from jobhunt.normalize.jobs import JobNormalizer
from jobhunt.sources.models import RawListing
from jobhunt.taxonomy.matcher import TitleMatcher

pytestmark = pytest.mark.anyio
NOW = datetime(2026, 10, 18, 9, 0, tzinfo=UTC)


def _listing(job_id: str, **fields: Any) -> RawListing:
    base: dict[str, Any] = {
        "source": "jobicy",
        "source_job_id": job_id,
        "url": f"https://x/{job_id}",
        "title": "Senior Python Developer",
        "description": "<p>Python</p>",
        "description_format": "html",
        "company": "Acme",
        "location": "Anywhere",
        "location_restrictions": ("Anywhere",),
        "work_mode": "remote",
        "posted_at": NOW - timedelta(days=2),
        "posted_at_source": "api",
        "deadline": date(2026, 11, 1),
        "tags": ("python",),
        "extra": {"level": "Senior"},
    }
    base.update(fields)
    return RawListing(**base)


@pytest.fixture
async def db(repo_root: Path) -> AsyncIterator[SqliteBackend]:
    backend = SqliteBackend(":memory:")
    await apply_migrations(backend, repo_root / "migrations")
    yield backend
    await backend.aclose()


async def _gate(
    db: SqliteBackend,
    config: Config,
    matcher: TitleMatcher,
    listings: list[RawListing],
    bootstrap: bool = False,
) -> dict[str, int]:
    normalizer = JobNormalizer(config)
    stored = await store_jobs(db, [normalizer.record(x) for x in listings], NOW, 10)
    items = [GateItem(job.id, x, NOW, bootstrap) for job, x in zip(stored, listings, strict=True)]
    return dict(await apply_gates(db, JobGates(config, matcher), items, NOW, batch_size=2))


async def test_gates_are_stored(db: SqliteBackend, config: Config, matcher: TitleMatcher) -> None:
    listings = [
        _listing("ok"),
        _listing("old", posted_at=NOW - timedelta(days=30)),
        _listing("us", location="USA Only", location_restrictions=("USA Only",)),
        _listing("vague", location=None, location_restrictions=()),
        _listing("civil", title="Senior Civil Engineer"),
    ]
    counts = await _gate(db, config, matcher, listings)
    assert counts == {
        "open, passed": 1,
        "filtered freshness:too_old": 1,
        "filtered location:region_restricted": 1,
        "open, needs facts": 1,
        "filtered cse:non_cse_title": 1,
    }
    rows = {r["source_job_id"]: r for r in await db.query("SELECT * FROM jobs")}
    ok = rows["ok"]
    assert (ok["status"], ok["filter_reason"]) == ("open", None)
    assert ok["remote_eligibility_evidence"] == "Anywhere"
    gates = json.loads(str(ok["gates_json"]))
    assert gates["cse"]["status"] == "pass"
    assert gates["needs_facts"] == []
    assert gates["seeded"] is False
    assert json.loads(str(rows["vague"]["gates_json"]))["needs_facts"] == ["location"]
    assert rows["us"]["filter_reason"] == "location:region_restricted"

    domains = await db.query(
        "SELECT domain_slug, is_primary, method FROM job_domains WHERE job_id = ?", (ok["id"],)
    )
    assert {"domain_slug": "language-specialists", "is_primary": 1, "method": "exact"} in domains


async def test_regating_replaces_domains(
    db: SqliteBackend, config: Config, matcher: TitleMatcher
) -> None:
    await _gate(db, config, matcher, [_listing("a")])
    await _gate(db, config, matcher, [_listing("a")])
    assert len(await db.query("SELECT 1 FROM job_domains WHERE is_primary = 1")) == 1


async def test_bootstrap_jobs_are_seeded(
    db: SqliteBackend, config: Config, matcher: TitleMatcher
) -> None:
    await _gate(db, config, matcher, [_listing("s")], bootstrap=True)
    row = (await db.query("SELECT gates_json FROM jobs"))[0]
    assert json.loads(str(row["gates_json"]))["seeded"] is True


async def test_linkedin_alert_time_stands_in_for_posting_date(
    config: Config, matcher: TitleMatcher
) -> None:
    listing = _listing(
        "l", source="linkedin_alerts", posted_at=None, deadline=None,
        extra={"alert_sent_at": (NOW - timedelta(days=1)).isoformat()},
    )  # fmt: skip
    outcome = JobGates(config, matcher).evaluate(GateItem(1, listing, NOW, False), NOW)
    fresh = next(r for r in outcome.results if r.gate == "freshness")
    assert (fresh.status, fresh.reason) == ("pass", "posted_recently")


async def test_listing_round_trip(db: SqliteBackend, config: Config) -> None:
    original = _listing("rt", salary="1000 USD")
    await store_jobs(db, [JobNormalizer(config).record(original)], NOW, 10)
    restored = listing_from_row((await db.query("SELECT * FROM jobs"))[0])
    assert restored.location_restrictions == original.location_restrictions
    assert restored.work_mode == "remote"
    assert restored.salary == "1000 USD"
    assert restored.tags == ("python",)
    assert restored.extra == {"level": "Senior"}
    assert restored.posted_at == original.posted_at
    assert restored.deadline == original.deadline
    assert restored.description == "Python"
