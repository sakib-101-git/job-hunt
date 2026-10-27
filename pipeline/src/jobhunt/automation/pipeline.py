"""The pipeline's stages, and the runs built from them (brief §4.2, §10.5).

A cycle is collect → enrich → match → notify → daily maintenance. Each stage
reports what it did; one stage failing is recorded and the later stages still
run. Every cycle is a row in `runs`, and a cycle with no failed stage writes the
heartbeat the Worker watches. The jobs the bot asks for (backfill, CV parsing,
digest, broadcast) run the same stages for one user, or one message.
"""

import json
import logging
import time
from collections import Counter
from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from jobhunt.config import Config
from jobhunt.cv.parse import CvParser
from jobhunt.db import Database, SqliteBackend, apply_migrations, set_kv, timestamp
from jobhunt.extract.llm import LlmClient
from jobhunt.jobs.enrich import enrich
from jobhunt.jobs.gating import GateItem, JobGates, apply_gates
from jobhunt.jobs.maintenance import daily_maintenance
from jobhunt.jobs.store import store_jobs
from jobhunt.normalize.jobs import JobNormalizer
from jobhunt.notify.outbox import first_time_users, queue_matches
from jobhunt.notify.sender import SendPlan, send_pending
from jobhunt.notify.telegram import BlockedError, TelegramClient, TelegramError
from jobhunt.score.matching import match
from jobhunt.score.render import Locale
from jobhunt.sources.http import Fetcher
from jobhunt.sources.runner import run_sources

log = logging.getLogger(__name__)

TOKEN_ENV = "TELEGRAM_BOT_TOKEN"  # noqa: S105 (a variable name)
ADMINS_ENV = "ADMIN_TELEGRAM_IDS"
_NOT_TRIED = {"skipped", "not_configured"}  # not due, or its secrets aren't set
SECRET_SUFFIXES = ("_TOKEN", "_KEY", "_PASSWORD", "_SECRET")
MIN_SECRET_LENGTH = 8


@dataclass
class StageReport:
    name: str
    counts: Counter[str] = field(default_factory=Counter)
    details: list[str] = field(default_factory=list)
    seconds: float = 0.0
    error: str | None = None


@dataclass
class RunSummary:
    trigger: str
    started_at: datetime
    stages: list[StageReport] = field(default_factory=list)
    finished_at: datetime | None = None

    @property
    def ok(self) -> bool:
        return all(stage.error is None for stage in self.stages)

    def stats(self) -> dict[str, object]:
        return {
            stage.name: {
                "counts": dict(stage.counts),
                "seconds": round(stage.seconds, 1),
                **({"error": stage.error} if stage.error else {}),
            }
            for stage in self.stages
        }


def admin_ids(environ: Mapping[str, str]) -> list[int]:
    """ADMIN_TELEGRAM_IDS: comma-separated Telegram user ids; others are ignored."""
    raw = environ.get(ADMINS_ENV, "")
    return sorted({int(part) for part in raw.split(",") if part.strip().isdigit()})


class StageFailedError(Exception):
    """A stage could not do its job (all sources failed, no bot token, ...)."""


class Pipeline:
    def __init__(
        self,
        db: Database,
        config: Config,
        environ: Mapping[str, str],
        *,
        telegram: TelegramClient | None = None,
        llm: LlmClient | None = None,
        fetcher: Fetcher | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.db = db
        self.config = config
        self._environ = environ
        self._clock = clock
        token = environ.get(TOKEN_ENV)
        self._owned: list[Callable[[], Awaitable[None]]] = []
        if telegram is None and token:
            telegram = TelegramClient(config.app.notifications, token)
            self._owned.append(telegram.aclose)
        self.telegram = telegram
        if llm is None:
            llm = LlmClient(config.llm, environ)
            self._owned.append(llm.aclose)
        self.llm = llm
        if fetcher is None:
            fetcher = Fetcher(config.sources.http)
            self._owned.append(fetcher.aclose)
        self._fetcher = fetcher
        self._locale = Locale.load(config.root)

    async def aclose(self) -> None:
        for close in self._owned:
            await close()

    async def prepare(self) -> None:
        """Local SQLite databases get their migrations here; D1 gets them at deploy."""
        if isinstance(self.db, SqliteBackend):
            await apply_migrations(self.db, self.config.root / "migrations")

    # --- stages ---------------------------------------------------------------

    async def collect(
        self, only: Collection[str] | None = None, force: bool = False
    ) -> StageReport:
        report = StageReport("collect")
        now = self._clock()
        config = self.config
        sources = await run_sources(
            config.sources, self.db, self._fetcher, now, self._environ, only, force
        )
        normalizer = JobNormalizer(config)
        listings = sources.listings
        batch = config.app.normalization.store_batch_size
        refresh = config.app.normalization.last_seen_refresh_hours
        records = [normalizer.record(x) for x in listings]
        stored = await store_jobs(self.db, records, now, batch, refresh)
        bootstrap = {run.source for run in sources.runs if run.bootstrap}
        items = [
            GateItem(job.id, listing, now, listing.source in bootstrap)
            for job, listing in zip(stored, listings, strict=True)
            if job.is_new
        ]
        gated = await apply_gates(self.db, JobGates(config), items, now, batch)
        new = Counter(job.record.source for job in stored if job.is_new)
        for run in sorted(sources.runs, key=lambda r: r.source):
            detail = run.error or f"{len(run.listings)} listings, {new[run.source]} new"
            report.details.append(f"{run.source}: {run.status}, {detail}")
            report.details += [f"{run.source}: warning: {w}" for w in run.warnings]
            report.counts[f"sources {run.status}"] += 1
        report.counts["listings"] = len(listings)
        report.counts["new jobs"] = sum(new.values())
        report.counts.update({f"gates: {k}": v for k, v in gated.items()})
        await self.alert_admins(
            [self._locale("admin.source_alert", message=a.message) for a in sources.alerts]
        )
        # One timeout is not an outage: the next run usually recovers. The stage
        # fails only when every source that ran has failed as often in a row as
        # it takes to raise a health alert.
        streak = config.sources.health.failures_before_alert
        tried = [run for run in sources.runs if run.status not in _NOT_TRIED]
        if tried and all(run.status == "failed" and run.failures >= streak for run in tried):
            raise StageFailedError(f"every source that ran failed, {streak}+ runs in a row")
        return report

    async def enrich(self, limit: int | None = None) -> StageReport:
        models = [e.key for e in self.config.llm.chain if self.llm.available(e)]
        counts = await enrich(self.db, self.config, self.llm, self._clock(), limit)
        details = [f"models with a key: {', '.join(models) or 'none (rules-only facts)'}"]
        return StageReport("enrich", counts, details)

    async def match(self, user_ids: Sequence[int] | None = None) -> StageReport:
        now = self._clock()
        first_time = await first_time_users(self.db, user_ids)
        new, counts = await match(self.db, self.config, now, user_ids=user_ids)
        top_n = self.config.app.notifications.backfill_top_n
        counts.update(await queue_matches(self.db, new, first_time, top_n, now))
        return StageReport("match", counts)

    async def notify(self, plan: SendPlan = SendPlan()) -> StageReport:  # noqa: B008 (frozen)
        if self.telegram is None:
            raise StageFailedError(f"{TOKEN_ENV} is not set")
        counts = await send_pending(self.db, self.config, self.telegram, self._clock(), plan)
        return StageReport("notify", counts)

    async def maintenance(self) -> StageReport:
        counts = await daily_maintenance(self.db, self.config, self._clock())
        report = StageReport("maintenance", counts or Counter())
        if counts is None:
            report.details.append("already ran today")
        return report

    # --- runs -----------------------------------------------------------------

    async def run_stage(
        self, summary: RunSummary, name: str, stage: Callable[[], Awaitable[StageReport]]
    ) -> StageReport:
        started = time.monotonic()
        try:
            report = await stage()
        except StageFailedError as exc:
            report = StageReport(name, error=str(exc))
        except Exception as exc:  # one stage failing must not stop the others
            log.exception("stage %s failed", name)
            report = StageReport(name, error=self._scrub(f"{type(exc).__name__}: {exc}")[:500])
        report.seconds = time.monotonic() - started
        summary.stages.append(report)
        return report

    def _scrub(self, text: str) -> str:
        """Error text goes to the public run summary: blank out any secret
        (an API URL with the bot token in it, say)."""
        for name, value in self._environ.items():
            if len(value) >= MIN_SECRET_LENGTH and name.endswith(SECRET_SUFFIXES):
                text = text.replace(value, "***")
        return text

    async def cycle(self, trigger: str) -> RunSummary:
        """One full pipeline run."""
        summary = RunSummary(trigger, self._clock())
        run_id = await self._start_run(summary)
        await self.run_stage(summary, "collect", self.collect)
        await self.run_stage(summary, "enrich", self.enrich)
        await self.run_stage(summary, "match", self.match)
        await self.run_stage(summary, "notify", self.notify)
        await self.run_stage(summary, "maintenance", self.maintenance)
        await self._finish_run(run_id, summary, heartbeat=True)
        return summary

    async def backfill(self, user_id: int) -> RunSummary:
        """A user just finished setup: score the open jobs for them and send
        their best matches now (brief §6.10). No LLM calls."""
        summary = RunSummary("backfill-user", self._clock())
        run_id = await self._start_run(summary)
        await self.run_stage(summary, "match", lambda: self.match([user_id]))
        plan = SendPlan(only_users=frozenset({user_id}))
        await self.run_stage(summary, "notify", lambda: self.notify(plan))
        await self._finish_run(run_id, summary)
        return summary

    async def send_digest(self, user_id: int) -> RunSummary:
        """/digest: the user's waiting matches, now, whatever their delivery mode."""
        summary = RunSummary("send-digest", self._clock())
        run_id = await self._start_run(summary)
        plan = SendPlan(only_users=frozenset({user_id}))
        await self.run_stage(summary, "notify", lambda: self.notify(plan))
        await self._finish_run(run_id, summary)
        return summary

    async def parse_cv(self, user_id: int) -> RunSummary:
        summary = RunSummary("parse-cv", self._clock())
        run_id = await self._start_run(summary)

        async def stage() -> StageReport:
            if self.telegram is None:
                raise StageFailedError(f"{TOKEN_ENV} is not set")
            parser = CvParser(self.db, self.config, self.telegram, self.llm)
            outcome = await parser.parse(user_id, self._clock())
            return StageReport("parse-cv", Counter({outcome: 1}))

        await self.run_stage(summary, "parse-cv", stage)
        await self._finish_run(run_id, summary)
        return summary

    async def broadcast(self, html: str, admin_id: int) -> RunSummary:
        """/broadcast: one message to every active user, then a report to the admin."""
        summary = RunSummary("broadcast", self._clock())
        run_id = await self._start_run(summary)

        async def stage() -> StageReport:
            if self.telegram is None:
                raise StageFailedError(f"{TOKEN_ENV} is not set")
            counts = await self._broadcast(self.telegram, html)
            done = self._locale(
                "admin.broadcast_done", sent=counts["sent"], failed=counts["failed"]
            )
            await self._send_quietly(admin_id, done)
            return StageReport("broadcast", counts)

        await self.run_stage(summary, "broadcast", stage)
        await self._finish_run(run_id, summary)
        return summary

    async def _broadcast(self, telegram: TelegramClient, html: str) -> Counter[str]:
        counts: Counter[str] = Counter()
        rows = await self.db.query("SELECT id, chat_id FROM users WHERE status = 'active'")
        for row in rows:
            try:
                await telegram.send_message(int(str(row["chat_id"])), html)
            except BlockedError:
                await self.db.execute(
                    "UPDATE users SET status = 'blocked', updated_at = ? WHERE id = ?",
                    (timestamp(self._clock()), int(str(row["id"]))),
                )
                counts["failed"] += 1
            except TelegramError:
                counts["failed"] += 1
            else:
                counts["sent"] += 1
        return counts

    async def alert_admins(self, messages: Sequence[str]) -> None:
        for chat_id in admin_ids(self._environ) if messages else []:
            for message in messages:
                await self._send_quietly(chat_id, message)

    async def _send_quietly(self, chat_id: int, text: str) -> None:
        if self.telegram is None:
            log.warning("admin message not sent: %s is not set", TOKEN_ENV)
            return
        try:
            await self.telegram.send_message(chat_id, text)
        except TelegramError as exc:
            log.warning("admin message not sent: %s", exc)

    async def _start_run(self, summary: RunSummary) -> int:
        rows = await self.db.query(
            "INSERT INTO runs (trigger, started_at, status) VALUES (?, ?, 'running') RETURNING id",
            (summary.trigger, timestamp(summary.started_at)),
        )
        return int(str(rows[0]["id"]))

    async def _finish_run(self, run_id: int, summary: RunSummary, heartbeat: bool = False) -> None:
        summary.finished_at = self._clock()
        status = "succeeded" if summary.ok else "failed"
        await self.db.execute(
            "UPDATE runs SET finished_at = ?, status = ?, stats_json = ? WHERE id = ?",
            (timestamp(summary.finished_at), status, json.dumps(summary.stats()), run_id),
        )
        if heartbeat and summary.ok:
            stamp = timestamp(summary.finished_at) or ""
            await set_kv(self.db, self.config.app.heartbeat.kv_key, stamp, summary.finished_at)
