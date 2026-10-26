"""Run the due sources concurrently. A failing source never stops the others;
its failure is counted in `source_state` and may raise a health alert."""

import asyncio
import logging
import time
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal

from jobhunt.config.models import Sources
from jobhunt.db import Database
from jobhunt.sources import build_source
from jobhunt.sources.base import FetchContext, Source
from jobhunt.sources.http import Fetcher
from jobhunt.sources.models import RawListing
from jobhunt.sources.state import (
    HealthAlert,
    load_state,
    record_failure,
    record_success,
    save_state,
)

log = logging.getLogger(__name__)

RunStatus = Literal["ok", "unchanged", "failed", "skipped", "not_configured"]


@dataclass
class SourceRun:
    source: str
    status: RunStatus
    bootstrap: bool = False
    listings: list[RawListing] = field(default_factory=list)
    error: str | None = None
    seconds: float = 0.0
    warnings: tuple[str, ...] = ()
    failures: int = 0  # failed runs in a row, this one included


@dataclass
class RunReport:
    runs: list[SourceRun]
    alerts: list[HealthAlert]

    @property
    def listings(self) -> list[RawListing]:
        return [listing for run in self.runs for listing in run.listings]


async def run_sources(
    sources: Sources,
    db: Database,
    fetcher: Fetcher,
    now: datetime,
    environ: Mapping[str, str],
    only: Collection[str] | None = None,
    force: bool = False,
) -> RunReport:
    """Fetch every enabled source that is due (all of `only`, if given)."""
    jobs = []
    runs_not_configured: list[SourceRun] = []
    for name, settings in sources.sources.items():
        if only is not None and name not in only:
            continue
        source = build_source(name, settings)
        if source is None or not settings.enabled:
            continue
        if missing := [env for env in settings.required_env if not environ.get(env)]:
            runs_not_configured.append(SourceRun(name, "not_configured", error=", ".join(missing)))
            continue
        interval = timedelta(minutes=settings.min_interval_minutes)
        jobs.append(_run_one(source, interval, sources, db, fetcher, now, environ, force))
    results = await asyncio.gather(*jobs)
    runs = [run for run, _ in results] + runs_not_configured
    alerts = [alert for _, run_alerts in results for alert in run_alerts]
    return RunReport(runs, alerts)


async def _run_one(
    source: Source,
    interval: timedelta,
    sources: Sources,
    db: Database,
    fetcher: Fetcher,
    now: datetime,
    environ: Mapping[str, str],
    force: bool,
) -> tuple[SourceRun, list[HealthAlert]]:
    st = await load_state(db, source.name)
    if not force and not st.due(now):
        return SourceRun(source.name, "skipped"), []
    rows = await db.query("SELECT source_job_id FROM jobs WHERE source = ?", (source.name,))
    ctx = FetchContext(
        fetcher=fetcher,
        now=now,
        bootstrap=not st.bootstrapped,
        known_ids=frozenset(str(row["source_job_id"]) for row in rows),
        environ=environ,
        state=dict(st.state),
    )
    started = time.monotonic()
    try:
        result = await source.fetch(ctx)
    except Exception as exc:  # a broken source must not stop the run
        error = f"{type(exc).__name__}: {exc}"
        log.warning("source %s failed: %s", source.name, error)
        alerts = record_failure(st, error, now, interval, sources.health)
        await save_state(db, st, now)
        run = SourceRun(
            source.name,
            "failed",
            ctx.bootstrap,
            error=error,
            seconds=_since(started),
            failures=st.consecutive_failures,
        )
        return run, alerts
    alerts = record_success(st, result, ctx.state, now, interval, sources.health)
    await save_state(db, st, now)
    status: RunStatus = "unchanged" if result.unchanged else "ok"
    log.info("source %s: %s, %d listings", source.name, status, len(result.listings))
    for warning in result.warnings:
        log.warning("source %s: %s", source.name, warning)
    run = SourceRun(
        source.name,
        status,
        ctx.bootstrap,
        result.listings,
        None,
        _since(started),
        result.warnings,
    )
    return run, alerts


def _since(started: float) -> float:
    return round(time.monotonic() - started, 2)
