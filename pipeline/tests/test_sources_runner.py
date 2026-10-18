from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from jobhunt.config import Config
from jobhunt.db import SqliteBackend, apply_migrations
from jobhunt.sources.base import parse_date, parse_datetime, string_list, text_or_none
from jobhunt.sources.http import Fetcher
from jobhunt.sources.models import RawListing, SourceResult
from jobhunt.sources.recording import ReplayTransport
from jobhunt.sources.runner import run_sources
from jobhunt.sources.state import (
    SourceState,
    load_state,
    record_failure,
    record_success,
    save_state,
)

pytestmark = pytest.mark.anyio

FIXTURES = Path(__file__).parent / "fixtures" / "sources"
NOW = datetime(2026, 10, 18, 9, 0, tzinfo=UTC)
HOUR = timedelta(hours=1)


async def _no_sleep(_: float) -> None:
    return None


@pytest.fixture
async def db(repo_root: Path) -> AsyncIterator[SqliteBackend]:
    backend = SqliteBackend(":memory:")
    await apply_migrations(backend, repo_root / "migrations")
    yield backend
    await backend.aclose()


class MultiReplay(httpx.AsyncBaseTransport):
    """Replays several fixture directories; unknown requests fail like a dead host."""

    def __init__(self, names: list[str]) -> None:
        self._replays = [ReplayTransport(FIXTURES / name) for name in names]

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        for replay in self._replays:
            try:
                return await replay.handle_async_request(request)
            except httpx.ConnectError:
                continue
        raise httpx.ConnectError("unrecorded", request=request)


def _fetcher(config: Config, names: list[str]) -> Fetcher:
    return Fetcher(config.sources.http, httpx.AsyncClient(transport=MultiReplay(names)), _no_sleep)


def _listing(n: int = 1) -> list[RawListing]:
    return [RawListing("s", str(i), "https://x", "t", "d", "text") for i in range(n)]


# --- state rules --------------------------------------------------------------


def test_success_schedules_next_run_and_bootstraps(config: Config) -> None:
    st = SourceState("s")
    assert st.due(NOW)
    alerts = record_success(
        st, SourceResult(_listing(3)), {"k": 1}, NOW, HOUR, config.sources.health
    )
    assert alerts == []
    assert (st.bootstrapped, st.last_count, st.state) == (True, 3, {"k": 1})
    assert st.next_run_at == NOW + HOUR
    assert not st.due(NOW + HOUR - timedelta(seconds=1))
    assert st.due(NOW + HOUR)


def test_failure_alert_fires_once_and_recovery_is_reported(config: Config) -> None:
    health = config.sources.health
    st = SourceState("s")
    alerts = [record_failure(st, "boom", NOW, HOUR, health) for _ in range(5)]
    fired = [i for i, a in enumerate(alerts) if a]
    assert fired == [health.failures_before_alert - 1]
    assert alerts[fired[0]][0].kind == "failing"
    recovered = record_success(st, SourceResult(_listing()), {}, NOW, HOUR, health)
    assert [a.kind for a in recovered] == ["recovered"]
    assert st.consecutive_failures == 0


def test_silent_source_alert_needs_earlier_jobs(config: Config) -> None:
    health = config.sources.health
    always_empty = SourceState("empty")
    for _ in range(health.empty_runs_before_alert + 2):
        assert record_success(always_empty, SourceResult([]), {}, NOW, HOUR, health) == []

    went_quiet = SourceState("quiet")
    record_success(went_quiet, SourceResult(_listing()), {}, NOW, HOUR, health)
    alerts = [
        record_success(went_quiet, SourceResult([]), {}, NOW, HOUR, health)
        for _ in range(health.empty_runs_before_alert + 2)
    ]
    assert [i for i, a in enumerate(alerts) if a] == [health.empty_runs_before_alert - 1]
    assert alerts[health.empty_runs_before_alert - 1][0].kind == "silent"


def test_unchanged_feed_is_not_counted_as_empty(config: Config) -> None:
    health = config.sources.health
    st = SourceState("s")
    record_success(st, SourceResult(_listing(2)), {}, NOW, HOUR, health)
    for _ in range(health.empty_runs_before_alert + 1):
        record_success(st, SourceResult([], unchanged=True), {}, NOW, HOUR, health)
    assert (st.consecutive_empty, st.last_count) == (0, 2)


async def test_state_round_trip(db: SqliteBackend) -> None:
    assert await load_state(db, "s") == SourceState("s")
    st = SourceState(
        "s",
        state={"validators": {"u": {"etag": "e"}}},
        next_run_at=NOW + HOUR,
        last_success_at=NOW,
        last_nonempty_at=NOW,
        consecutive_failures=1,
        consecutive_empty=2,
        last_count=7,
        bootstrapped=True,
    )
    await save_state(db, st, NOW)
    await save_state(db, st, NOW)  # upsert
    assert await load_state(db, "s") == st


# --- runner -------------------------------------------------------------------


async def test_run_isolates_failures_and_records_state(db: SqliteBackend, config: Config) -> None:
    # Only jobicy's responses are recorded: remoteok fails like a dead host.
    fetcher = _fetcher(config, ["jobicy"])
    report = await run_sources(
        config.sources, db, fetcher, NOW, {}, only={"jobicy", "remoteok", "themuse"}
    )
    by_source = {run.source: run for run in report.runs}
    assert set(by_source) == {"jobicy", "remoteok"}  # themuse has no implementation
    assert by_source["jobicy"].status == "ok"
    assert by_source["jobicy"].bootstrap is True
    assert len(by_source["jobicy"].listings) == 5
    assert by_source["remoteok"].status == "failed"
    assert "unrecorded" in (by_source["remoteok"].error or "")
    assert len(report.listings) == 5

    jobicy = await load_state(db, "jobicy")
    assert jobicy.bootstrapped
    assert jobicy.last_count == 5
    assert jobicy.next_run_at == NOW + timedelta(minutes=60)
    assert (await load_state(db, "remoteok")).consecutive_failures == 1


async def test_sources_that_are_not_due_are_skipped(db: SqliteBackend, config: Config) -> None:
    fetcher = _fetcher(config, ["jobicy"])
    await run_sources(config.sources, db, fetcher, NOW, {}, only={"jobicy"})
    again = await run_sources(
        config.sources, db, fetcher, NOW + timedelta(minutes=5), {}, {"jobicy"}
    )
    assert [run.status for run in again.runs] == ["skipped"]
    forced = await run_sources(
        config.sources, db, fetcher, NOW + timedelta(minutes=5), {}, {"jobicy"}, force=True
    )
    assert [run.status for run in forced.runs] == ["ok"]
    assert forced.runs[0].bootstrap is False


async def test_disabled_sources_never_run(db: SqliteBackend, config: Config) -> None:
    report = await run_sources(config.sources, db, _fetcher(config, []), NOW, {}, {"remotive"})
    assert report.runs == []


async def test_sources_missing_env_are_not_configured(db: SqliteBackend, config: Config) -> None:
    report = await run_sources(
        config.sources, db, _fetcher(config, []), NOW, {}, {"linkedin_alerts"}
    )
    [run] = report.runs
    assert run.status == "not_configured"
    assert run.error == "IMAP_HOST, IMAP_EMAIL, IMAP_APP_PASSWORD"
    assert (await load_state(db, "linkedin_alerts")).consecutive_failures == 0


async def test_failing_source_raises_alert_at_threshold(db: SqliteBackend, config: Config) -> None:
    fetcher = _fetcher(config, [])
    alerts = []
    for run in range(config.sources.health.failures_before_alert):
        report = await run_sources(
            config.sources, db, fetcher, NOW + run * HOUR, {}, {"jobicy"}, force=True
        )
        alerts.extend(report.alerts)
    assert [(a.source, a.kind) for a in alerts] == [("jobicy", "failing")]


async def test_known_jobs_come_from_the_database(db: SqliteBackend, config: Config) -> None:
    await db.execute(
        "INSERT INTO jobs (source, source_job_id, url, canonical_url, fingerprint, title,"
        " posted_at_source, first_seen_at, last_seen_at)"
        " VALUES ('bdjobs', '1546635', 'u', 'u', 'f', 't', 'api', 'x', 'x')"
    )
    # bdjobs fixtures were recorded on an incremental run: mark it bootstrapped.
    await save_state(db, SourceState("bdjobs", bootstrapped=True), NOW)
    report = await run_sources(
        config.sources, db, _fetcher(config, ["bdjobs"]), NOW, {}, {"bdjobs"}
    )
    listing = next(x for x in report.listings if x.source_job_id == "1546635")
    assert listing.extra["details"] == "missing"


# --- parsing helpers ----------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-10-18T09:30:00Z", datetime(2026, 10, 18, 9, 30, tzinfo=UTC)),
        ("2026-10-18T15:30:00+06:00", datetime(2026, 10, 18, 9, 30, tzinfo=UTC)),
        ("2026-10-18T09:30:00", datetime(2026, 10, 18, 9, 30, tzinfo=UTC)),
        ("Sun, 18 Oct 2026 09:30:00 +0000", datetime(2026, 10, 18, 9, 30, tzinfo=UTC)),
        ("1792315800", datetime(2026, 10, 18, 9, 30, tzinfo=UTC)),
        ("", None),
        (None, None),
        ("not a date", None),
    ],
)
def test_parse_datetime(value: str | None, expected: datetime | None) -> None:
    assert parse_datetime(value) == expected


def test_other_helpers() -> None:
    assert parse_date("2026-11-05T00:00:00Z") == date(2026, 11, 5)
    assert parse_date("Nov 5") is None
    assert parse_date(None) is None
    assert text_or_none("  None ") is None
    assert text_or_none(" NA") is None
    assert text_or_none(" Dhaka ") == "Dhaka"
    assert string_list(["a", " ", None, "b"]) == ("a", "b")
    assert string_list("x, y,,") == ("x", "y")
    assert string_list(None) == ()
