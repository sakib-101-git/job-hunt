"""Command-line entry point: `jobhunt <command>`.

Commands are added as each pipeline stage lands:

- `doctor` checks that this environment can run the pipeline at all.
- `validate-config` loads and cross-checks every file in config/.
- `build-shared` writes the Worker's generated config (`--check` only compares).
- `sources` lists the configured sources; `fetch` runs some live, without a
  database, and can record their responses as test fixtures.
- `collect` runs the due sources, stores what they return and gates new jobs.
"""

import argparse
import asyncio
import os
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx

from jobhunt import __version__, shared
from jobhunt.config import ConfigError, find_repo_root, load_config
from jobhunt.db import DatabaseError, SqliteBackend, apply_migrations, open_database
from jobhunt.jobs.gating import GateItem, JobGates, apply_gates
from jobhunt.jobs.store import store_jobs
from jobhunt.normalize.jobs import JobNormalizer
from jobhunt.sources import FetchContext, build_source
from jobhunt.sources.http import Fetcher, FetchError
from jobhunt.sources.recording import RecordingTransport
from jobhunt.sources.runner import run_sources

REQUIRED_PYTHON = (3, 12)


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


def run_checks(cwd: Path, python_version: tuple[int, int]) -> list[Check]:
    checks = [
        Check(
            "python",
            python_version == REQUIRED_PYTHON,
            f"found {python_version[0]}.{python_version[1]}, "
            f"need {REQUIRED_PYTHON[0]}.{REQUIRED_PYTHON[1]}",
        )
    ]
    root = find_repo_root(cwd)
    checks.append(
        Check("repo", root is not None, str(root) if root else "not inside a job-hunt checkout")
    )
    if root is not None:
        try:
            load_config(root / "config")
            checks.append(Check("config", True, str(root / "config")))
        except ConfigError as exc:
            checks.append(Check("config", False, str(exc)))
    return checks


def _write(text: str) -> None:
    sys.stdout.write(f"{text}\n")


def _fail(text: str) -> int:
    sys.stderr.write(f"{text}\n")
    return 1


def _doctor() -> int:
    checks = run_checks(Path.cwd(), (sys.version_info.major, sys.version_info.minor))
    for check in checks:
        mark = "ok  " if check.ok else "FAIL"
        _write(f"[{mark}] {check.name}: {check.detail}")
    return 0 if all(check.ok for check in checks) else 1


def _validate_config() -> int:
    try:
        config = load_config()
    except ConfigError as exc:
        return _fail(str(exc))
    titles = sum(len(t) for t in config.taxonomy.titles_by_domain().values())
    _write(
        f"config ok: {len(config.taxonomy.domains)} domains, {titles} titles, "
        f"{len(config.tracks.tracks)} tracks, {len(config.skills.all_skills)} skills, "
        f"{len(config.locations.districts)} districts"
    )
    return 0


def _build_shared(check: bool) -> int:
    root = find_repo_root(Path.cwd())
    if root is None:
        return _fail("not inside a job-hunt checkout")
    try:
        config = load_config(root / "config")
    except ConfigError as exc:
        return _fail(str(exc))
    path = root / shared.GENERATED_PATH
    if check:
        current = path.read_text(encoding="utf-8") if path.is_file() else None
        if current != shared.render(config):
            return _fail(f"{shared.GENERATED_PATH} is out of date; run `make build-shared`")
        _write(f"{shared.GENERATED_PATH} is up to date")
        return 0
    changed = shared.write(config, path)
    _write(f"{shared.GENERATED_PATH} {'updated' if changed else 'already up to date'}")
    return 0


def _sources() -> int:
    try:
        config = load_config()
    except ConfigError as exc:
        return _fail(str(exc))
    for name, s in config.sources.sources.items():
        status = f"every {s.min_interval_minutes} min" if s.enabled else f"off: {s.disabled_reason}"
        _write(f"{name:16} {s.kind:14} {status}")
    return 0


async def _fetch(names: Sequence[str], bootstrap: bool, record: Path | None, show: int) -> int:
    try:
        config = load_config()
    except ConfigError as exc:
        return _fail(str(exc))
    http = config.sources.http
    client = None
    if record is not None:
        client = httpx.AsyncClient(
            transport=RecordingTransport(record),
            follow_redirects=True,
            timeout=http.timeout_seconds,
        )
    fetcher = Fetcher(http, client=client)
    failed = False
    try:
        for name in names:
            settings = config.sources.sources.get(name)
            source = build_source(name, settings) if settings else None
            if settings is None or source is None:
                failed = True
                _fail(f"{name}: no such source, or it has no implementation")
                continue
            ctx = FetchContext(fetcher, datetime.now(UTC), bootstrap, frozenset(), os.environ)
            try:
                result = await source.fetch(ctx)
            except FetchError as exc:
                failed = True
                _fail(f"{name}: {exc}")
                continue
            note = "" if settings.enabled else " (disabled in config)"
            _write(f"{name}: {len(result.listings)} listings{note}")
            for listing in result.listings[:show]:
                posted = listing.posted_at.date().isoformat() if listing.posted_at else "-"
                _write(f"  {posted}  {listing.title} | {listing.company} | {listing.location}")
    finally:
        await fetcher.aclose()
        if client is not None:
            await client.aclose()
    return 1 if failed else 0


async def _collect(only: Sequence[str] | None, force: bool) -> int:
    try:
        config = load_config()
        db = open_database(config.app.database, os.environ)
    except (ConfigError, DatabaseError) as exc:
        return _fail(str(exc))
    fetcher = Fetcher(config.sources.http)
    now = datetime.now(UTC)
    try:
        root = find_repo_root(Path.cwd())
        if isinstance(db, SqliteBackend) and root is not None:
            await apply_migrations(db, root / "migrations")  # local databases only
        report = await run_sources(config.sources, db, fetcher, now, os.environ, only, force)
        normalizer = JobNormalizer(config)
        listings = report.listings
        batch = config.app.normalization.store_batch_size
        stored = await store_jobs(db, [normalizer.record(x) for x in listings], now, batch)
        bootstrap = {run.source for run in report.runs if run.bootstrap}
        items = [
            GateItem(job.id, listing, now, listing.source in bootstrap)
            for job, listing in zip(stored, listings, strict=True)
            if job.is_new
        ]
        gated = await apply_gates(db, JobGates(config), items, now, batch)
    finally:
        await fetcher.aclose()
        await db.aclose()
    new = Counter(job.record.source for job in stored if job.is_new)
    for run in sorted(report.runs, key=lambda r: r.source):
        detail = f"{len(run.listings):4} listings {new[run.source]:4} new"
        if run.error:
            detail = run.error
        _write(f"{run.source:16} {run.status:15} {detail}")
        for warning in run.warnings:
            _write(f"{'':16} warning: {warning}")
    for outcome, count in sorted(gated.items()):
        _write(f"gates: {count:5} {outcome}")
    for alert in report.alerts:
        _write(f"ALERT [{alert.kind}] {alert.message}")
    failed = [run for run in report.runs if run.status == "failed"]
    return 1 if report.runs and len(failed) == len(report.runs) else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jobhunt")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", help="check that this environment can run the pipeline")
    commands.add_parser("validate-config", help="load and cross-check every file in config/")
    build = commands.add_parser("build-shared", help="write the Worker's generated config")
    build.add_argument("--check", action="store_true", help="fail if the file is out of date")
    commands.add_parser("sources", help="list the configured job sources")
    fetch = commands.add_parser("fetch", help="fetch sources live and print what they return")
    fetch.add_argument("names", nargs="+", metavar="SOURCE")
    fetch.add_argument("--bootstrap", action="store_true", help="fetch as on a first run")
    fetch.add_argument("--record", type=Path, metavar="DIR", help="save responses as fixtures")
    fetch.add_argument("--show", type=int, default=5, metavar="N", help="listings to print")
    collect = commands.add_parser("collect", help="run the due sources and store their jobs")
    collect.add_argument("--source", action="append", dest="only", metavar="SOURCE")
    collect.add_argument("--force", action="store_true", help="run sources that are not due")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "doctor":
        return _doctor()
    if args.command == "validate-config":
        return _validate_config()
    if args.command == "build-shared":
        return _build_shared(args.check)
    if args.command == "sources":
        return _sources()
    if args.command == "fetch":
        return asyncio.run(_fetch(args.names, args.bootstrap, args.record, args.show))
    if args.command == "collect":
        return asyncio.run(_collect(args.only, args.force))
    raise AssertionError(f"unhandled command {args.command!r}")  # argparse rejects unknown ones


if __name__ == "__main__":
    sys.exit(main())
