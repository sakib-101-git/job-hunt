"""Command-line entry point: `jobhunt <command>`.

Commands are added as each pipeline stage lands:

- `doctor` checks that this environment can run the pipeline at all.
- `validate-config` loads and cross-checks every file in config/.
- `build-shared` writes the Worker's generated config (`--check` only compares).
- `sources` lists the configured sources; `fetch` runs some live, without a
  database, and can record their responses as test fixtures.
- `collect` runs the due sources, stores what they return and gates new jobs.
- `enrich` classifies unknown titles, extracts facts from open jobs and gates
  them again.
- `match` scores eligible jobs for users and queues notifications; `notify`
  sends what is queued (needs TELEGRAM_BOT_TOKEN).
- `run` is one full pipeline run (what the scheduled workflow runs); `event`
  handles a repository_dispatch event file (run-pipeline, backfill-user,
  parse-cv, send-digest, broadcast); `backfill` and `parse-cv` run those jobs
  for one user by hand.
- `eval` measures the system on the golden set in eval/golden/ (recorded LLM
  answers by default; `--live` calls the models); `eval label` turns silver
  labels gold; `eval draft` drafts golden jobs from a collected database.
- `audit` has the auditor model re-score a sample of last week's matches.
- `bench` times the scorer on synthetic users and jobs.
"""

import argparse
import asyncio
import os
import sys
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from jobhunt import __version__, shared
from jobhunt.automation.events import EventError, handle_event, read_event
from jobhunt.automation.pipeline import Pipeline, RunSummary, StageReport
from jobhunt.automation.summary import markdown
from jobhunt.config import ConfigError, find_repo_root, load_config
from jobhunt.db import DatabaseError, open_database
from jobhunt.eval import commands as evaluation
from jobhunt.eval.dataset import DatasetError
from jobhunt.logs import configure_logging
from jobhunt.notify.sender import SendPlan
from jobhunt.score.bench import bench
from jobhunt.sources import FetchContext, build_source
from jobhunt.sources.http import Fetcher, FetchError
from jobhunt.sources.recording import RecordingTransport

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


@asynccontextmanager
async def _pipeline() -> AsyncIterator[Pipeline]:
    """A Pipeline on the configured database (D1, or local SQLite with migrations)."""
    config = load_config()
    db = open_database(config.app.database, os.environ)
    pipeline = Pipeline(db, config, os.environ)
    try:
        await pipeline.prepare()
        yield pipeline
    finally:
        await pipeline.aclose()
        await db.aclose()


def _print_stage(report: StageReport) -> None:
    for line in report.details:
        _write(line)
    for outcome, count in sorted(report.counts.items()):
        _write(f"{count:5} {outcome}")
    if report.error:
        _write(f"FAILED: {report.error}")


async def _stage(name: str, run: Callable[[Pipeline], Awaitable[StageReport]]) -> int:
    try:
        async with _pipeline() as pipeline:
            summary = RunSummary(name, datetime.now(UTC))
            report = await pipeline.run_stage(summary, name, lambda: run(pipeline))
    except (ConfigError, DatabaseError) as exc:
        return _fail(str(exc))
    _print_stage(report)
    return 0 if report.error is None else 1


def _report(summary: RunSummary) -> int:
    for stage in summary.stages:
        _write(f"== {stage.name} ({stage.seconds:.1f} s)")
        _print_stage(stage)
    if path := os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(path).open("a", encoding="utf-8") as out:
            out.write(markdown(summary))
    return 0 if summary.ok else 1


async def _run(trigger: str) -> int:
    try:
        async with _pipeline() as pipeline:
            summary = await pipeline.cycle(trigger)
    except (ConfigError, DatabaseError) as exc:
        return _fail(str(exc))
    return _report(summary)


async def _event(action: str, payload: dict[str, Any]) -> int:
    try:
        async with _pipeline() as pipeline:
            summary = await handle_event(pipeline, action, payload)
    except (ConfigError, DatabaseError, EventError) as exc:
        return _fail(str(exc))
    return _report(summary)


def _event_file(path: Path) -> int:
    try:
        action, payload = read_event(path)
    except EventError as exc:
        return _fail(str(exc))
    return asyncio.run(_event(action, payload))


def _eval(args: argparse.Namespace) -> int:
    try:
        config = load_config()
    except ConfigError as exc:
        return _fail(str(exc))
    directory = args.dir or config.root / evaluation.GOLDEN_DIR
    try:
        if args.eval_command == "label":
            return evaluation.label(directory, args.pairs, input, _write)
        if args.eval_command == "draft":
            return asyncio.run(evaluation.draft(config, directory, args.db, args.count, _write))
        if args.record and not args.live:
            return _fail("--record needs --live")
        return asyncio.run(
            evaluation.evaluate(
                config,
                directory,
                live=args.live,
                record=args.record,
                check=args.check,
                report_path=args.report,
                write=_write,
                wait_seconds=args.wait,
            )
        )
    except (DatasetError, ValueError, DatabaseError) as exc:
        return _fail(str(exc))


def _audit() -> int:
    try:
        config = load_config()
        return asyncio.run(evaluation.audit(config, os.environ, _write))
    except (ConfigError, DatabaseError, ValueError) as exc:
        return _fail(str(exc))


def _bench(users: int, jobs: int) -> int:
    try:
        config = load_config()
    except ConfigError as exc:
        return _fail(str(exc))
    result = bench(config, users, jobs)
    _write(
        f"{result.pairs} pairs scored in {result.seconds:.2f} s ({result.passed} passed the gates)"
    )
    return 0


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
    enrich_cmd = commands.add_parser("enrich", help="extract facts from open jobs")
    enrich_cmd.add_argument("--limit", type=int, metavar="N", help="jobs to extract at most")
    commands.add_parser("match", help="score jobs for users and queue notifications")
    notify = commands.add_parser("notify", help="send queued notifications")
    notify.add_argument("--digest", action="store_true", help="serve every digest user now")
    notify.add_argument("--user", type=int, action="append", metavar="ID", help="only this user")
    run = commands.add_parser("run", help="one full pipeline run: collect to notify")
    run.add_argument("--trigger", default="manual", help="recorded in the runs table")
    event = commands.add_parser("event", help="handle a repository_dispatch event file")
    event.add_argument("path", type=Path, metavar="EVENT_JSON")
    backfill = commands.add_parser("backfill", help="match and send a new user's best jobs")
    backfill.add_argument("--user", type=int, required=True, metavar="ID")
    parse_cv = commands.add_parser("parse-cv", help="read the CV a user sent the bot")
    parse_cv.add_argument("--user", type=int, required=True, metavar="ID")
    ev = commands.add_parser("eval", help="measure the system on the golden set")
    ev.add_argument("--dir", type=Path, default=None, help="golden set (eval/golden)")
    ev.add_argument("--live", action="store_true", help="call the LLMs instead of recordings")
    ev.add_argument("--record", action="store_true", help="with --live: save the answers")
    ev.add_argument("--check", action="store_true", help="fail when a threshold is not met")
    ev.add_argument("--report", type=Path, metavar="FILE", help="also write the report here")
    ev.add_argument(
        "--wait", type=float, metavar="SECONDS", help="with --live: wait this long for model room"
    )
    ev_commands = ev.add_subparsers(dest="eval_command")
    lab = ev_commands.add_parser("label", help="confirm or fix silver labels (makes them gold)")
    lab.add_argument("--pairs", action="store_true", help="label pairs instead of jobs")
    dr = ev_commands.add_parser("draft", help="draft golden jobs from a collected database")
    dr.add_argument("--db", type=Path, required=True, metavar="SQLITE")
    dr.add_argument("--count", type=int, default=160)
    commands.add_parser("audit", help="re-score recent matches with the auditor model")
    bench_cmd = commands.add_parser("bench", help="time the scorer on synthetic users and jobs")
    bench_cmd.add_argument("--users", type=int, default=1000)
    bench_cmd.add_argument("--jobs", type=int, default=300)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging(os.environ)
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
        return asyncio.run(_stage("collect", lambda p: p.collect(args.only, args.force)))
    if args.command == "enrich":
        return asyncio.run(_stage("enrich", lambda p: p.enrich(args.limit)))
    if args.command == "match":
        return asyncio.run(_stage("match", lambda p: p.match()))
    if args.command == "notify":
        users = frozenset(args.user) if args.user else None
        plan = SendPlan(digest_run=args.digest, only_users=users)
        return asyncio.run(_stage("notify", lambda p: p.notify(plan)))
    if args.command == "run":
        return asyncio.run(_run(args.trigger))
    if args.command == "event":
        return _event_file(args.path)
    if args.command == "backfill":
        return asyncio.run(_event("backfill-user", {"user_id": args.user}))
    if args.command == "parse-cv":
        return asyncio.run(_event("parse-cv", {"user_id": args.user}))
    if args.command == "eval":
        return _eval(args)
    if args.command == "audit":
        return _audit()
    if args.command == "bench":
        return _bench(args.users, args.jobs)
    raise AssertionError(f"unhandled command {args.command!r}")  # argparse rejects unknown ones


if __name__ == "__main__":
    sys.exit(main())
