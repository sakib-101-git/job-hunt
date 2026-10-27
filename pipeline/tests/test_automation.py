import json
from collections import Counter
from collections.abc import AsyncIterator
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from test_notify import NOW, Clock, FakeTelegram, add_job, add_user

from jobhunt.automation.events import EventError, handle_event, read_event
from jobhunt.automation.pipeline import Pipeline, RunSummary, StageReport, admin_ids
from jobhunt.automation.summary import markdown
from jobhunt.config import Config
from jobhunt.db import SqliteBackend, apply_migrations, get_kv
from jobhunt.extract.llm import LlmClient
from jobhunt.jobs.maintenance import daily_maintenance
from jobhunt.notify.outbox import first_time_users, split_backfill
from jobhunt.notify.telegram import TelegramClient
from jobhunt.score.matching import NewMatch
from jobhunt.sources.http import Fetcher
from jobhunt.sources.state import SourceState, save_state

pytestmark = pytest.mark.anyio


@pytest.fixture
async def db(repo_root: Path) -> AsyncIterator[SqliteBackend]:
    backend = SqliteBackend(":memory:")
    await apply_migrations(backend, repo_root / "migrations")
    yield backend
    await backend.aclose()


def _offline(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("offline in tests", request=request)


async def _no_sleep(_: float) -> None:
    return None


def _pipeline(
    db: SqliteBackend,
    config: Config,
    fake: FakeTelegram | None,
    environ: dict[str, str] | None = None,
    llm: httpx.MockTransport | None = None,
) -> Pipeline:
    clock = Clock()
    telegram = None
    if fake is not None:
        client = httpx.AsyncClient(transport=httpx.MockTransport(fake))
        telegram = TelegramClient(config.app.notifications, "T", client, clock, clock.sleep)
    environ = environ if environ is not None else {}
    llm_client = LlmClient(
        config.llm, environ, httpx.AsyncClient(transport=llm or httpx.MockTransport(_offline))
    )
    fetcher = Fetcher(
        config.sources.http, httpx.AsyncClient(transport=httpx.MockTransport(_offline)), _no_sleep
    )
    return Pipeline(
        db, config, environ, telegram=telegram, llm=llm_client, fetcher=fetcher, clock=lambda: NOW
    )


# --- backfill selection --------------------------------------------------------


def _m(match_id: int, user_id: int, score: str) -> NewMatch:
    return NewMatch(match_id, user_id, match_id, Decimal(score))


def test_split_backfill_keeps_the_best_per_new_user() -> None:
    matches = [_m(1, 1, "6.0"), _m(2, 1, "9.0"), _m(3, 1, "7.5"), _m(4, 2, "8.0")]
    regular, backfill = split_backfill(matches, {1}, 2)
    assert regular == [_m(4, 2, "8.0")]
    assert [m.match_id for m in backfill] == [2, 3]


async def test_first_time_users(db: SqliteBackend, config: Config) -> None:
    await add_job(db, 1)
    await add_user(db, 1)
    await add_user(db, 2)
    assert await first_time_users(db) == {1, 2}
    assert await first_time_users(db, [2, 3]) == {2}
    await db.execute(
        "INSERT INTO matches (user_id, job_id, score, breakdown_json, explanation_json,"
        " gate_results_json, scorer_version) VALUES (1, 1, 7, '{}', '{}', '{}', 'v')"
    )
    assert await first_time_users(db) == {2}


async def test_new_user_gets_top_n_including_seeded_jobs(db: SqliteBackend, config: Config) -> None:
    top_n = config.app.notifications.backfill_top_n
    for job_id in range(1, top_n + 4):
        await add_job(db, job_id, seeded=job_id % 2 == 0)
    await add_user(db, 1)
    pipeline = _pipeline(db, config, FakeTelegram())
    report = await pipeline.match()
    assert report.counts["queued"] == top_n
    assert report.counts["queued (backfill)"] == top_n
    assert report.counts["not queued: beyond backfill"] == 3
    # The same user's later matches follow the regular rules: seeded jobs wait.
    await add_job(db, 100, seeded=True)
    await add_job(db, 101)
    report = await pipeline.match()
    assert (report.counts["queued"], report.counts["not queued"]) == (1, 1)


# --- maintenance ---------------------------------------------------------------


async def test_daily_maintenance_expires_and_clears(db: SqliteBackend, config: Config) -> None:
    max_age = config.app.freshness.max_age_days
    await add_job(db, 1)  # fresh
    await add_job(db, 2, posted_days_ago=max_age + 1)  # too old
    await add_job(db, 3)
    await db.execute("UPDATE jobs SET deadline = '2026-10-17' WHERE id = 3")  # passed
    await add_job(db, 4)
    old = (NOW - timedelta(days=config.app.retention.jd_text_days + 1)).isoformat()
    await db.execute("UPDATE jobs SET jd_text = 'text', last_seen_at = ? WHERE id = 4", (old,))
    await db.execute("UPDATE jobs SET jd_text = 'kept' WHERE id = 1")

    counts = await daily_maintenance(db, config, NOW)
    assert counts == {"jobs expired": 2, "descriptions cleared": 1}
    rows = await db.query("SELECT id, status, jd_text FROM jobs ORDER BY id")
    assert [(r["status"], r["jd_text"]) for r in rows] == [
        ("open", "kept"),
        ("expired", None),
        ("expired", None),
        ("open", None),
    ]
    # Once a day: the same local day does nothing.
    assert await daily_maintenance(db, config, NOW + timedelta(hours=1)) is None
    assert await daily_maintenance(db, config, NOW + timedelta(days=1)) is not None


# --- runs ----------------------------------------------------------------------


async def test_cycle_records_the_run_and_heartbeat(db: SqliteBackend, config: Config) -> None:
    await add_job(db, 1)
    await add_user(db, 1)
    fake = FakeTelegram()
    pipeline = _pipeline(db, config, fake)
    # Every source has failed before and fails again offline: collect fails,
    # the other stages still run.
    await _failing_streak(db, config, config.sources.health.failures_before_alert - 1)
    summary = await pipeline.cycle("schedule")
    assert [s.name for s in summary.stages] == [
        "collect",
        "enrich",
        "match",
        "notify",
        "maintenance",
    ]
    assert summary.stages[0].error == "every source that ran failed, 3+ runs in a row"
    assert summary.stages[3].counts == {"sent": 1}
    assert not summary.ok
    run = (await db.query("SELECT * FROM runs"))[0]
    assert (run["trigger"], run["status"]) == ("schedule", "failed")
    assert json.loads(str(run["stats_json"]))["notify"]["counts"] == {"sent": 1}
    assert await get_kv(db, config.app.heartbeat.kv_key) is None  # a failed run: no heartbeat


async def _failing_streak(db: SqliteBackend, config: Config, failures: int) -> None:
    for name in config.sources.sources:
        await save_state(db, SourceState(name, consecutive_failures=failures), NOW)


async def test_one_failed_fetch_does_not_fail_collect(db: SqliteBackend, config: Config) -> None:
    pipeline = _pipeline(db, config, FakeTelegram())
    report = await pipeline.collect()  # every source fails offline, for the first time
    assert report.error is None
    assert report.counts["sources failed"] > 0
    assert report.counts["sources ok"] == 0


async def test_successful_cycle_writes_the_heartbeat(
    db: SqliteBackend, config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    pipeline = _pipeline(db, config, FakeTelegram())

    async def collected() -> StageReport:
        return StageReport("collect", Counter({"new jobs": 0}))

    monkeypatch.setattr(pipeline, "collect", collected)
    summary = await pipeline.cycle("dispatch")
    assert summary.ok
    assert await get_kv(db, config.app.heartbeat.kv_key) == "2026-10-18T06:00:00Z"
    run = (await db.query("SELECT status, finished_at FROM runs"))[0]
    assert (run["status"], run["finished_at"]) == ("succeeded", "2026-10-18T06:00:00Z")


async def test_notify_without_a_token_fails_the_stage(db: SqliteBackend, config: Config) -> None:
    pipeline = _pipeline(db, config, None)
    summary = RunSummary("t", NOW)
    report = await pipeline.run_stage(summary, "notify", pipeline.notify)
    assert report.error == "TELEGRAM_BOT_TOKEN is not set"


async def test_unexpected_errors_are_recorded(db: SqliteBackend, config: Config) -> None:
    pipeline = _pipeline(db, config, FakeTelegram())

    async def broken() -> StageReport:
        raise RuntimeError("boom")

    report = await pipeline.run_stage(RunSummary("t", NOW), "x", broken)
    assert report.error == "RuntimeError: boom"


async def test_stage_errors_never_show_secrets(db: SqliteBackend, config: Config) -> None:
    token = "123456:secret-bot-token"  # noqa: S105 (a fake)
    pipeline = _pipeline(db, config, FakeTelegram(), {"TELEGRAM_BOT_TOKEN": token})

    async def leaky() -> StageReport:
        raise RuntimeError(f"cannot reach https://api.telegram.org/bot{token}/sendMessage")

    report = await pipeline.run_stage(RunSummary("t", NOW), "x", leaky)
    assert report.error is not None
    assert token not in report.error
    assert "bot***/sendMessage" in report.error


async def test_backfill_sends_now_whatever_the_mode(db: SqliteBackend, config: Config) -> None:
    await add_job(db, 1, seeded=True)
    await add_user(db, 1, mode="digest")
    await add_user(db, 2)
    fake = FakeTelegram()
    summary = await _pipeline(db, config, fake).backfill(1)
    assert summary.ok
    assert summary.stages[1].counts == {"sent": 1}
    assert [b["chat_id"] for b in fake.sent] == [10]  # user 2 waits for the regular run


async def test_source_alerts_reach_the_admins(db: SqliteBackend, config: Config) -> None:
    fake = FakeTelegram()
    pipeline = _pipeline(db, config, fake, {"ADMIN_TELEGRAM_IDS": "7, 8,x"})
    await pipeline.alert_admins(["a"])
    assert [b["chat_id"] for b in fake.sent] == [7, 8]
    assert admin_ids({}) == []


async def test_broadcast(db: SqliteBackend, config: Config) -> None:
    await add_user(db, 1)
    await add_user(db, 2)
    await add_user(db, 3, status="paused")
    blocked = {"ok": False, "error_code": 403, "description": "Forbidden: bot was blocked"}

    def answer(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["chat_id"] == 20:
            return httpx.Response(403, json=blocked)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    fake = FakeTelegram(default=answer)
    summary = await _pipeline(db, config, fake).broadcast("<b>Hello</b>", 9001)
    assert summary.stages[0].counts == {"sent": 1, "failed": 1}
    assert [b["chat_id"] for b in fake.sent] == [10, 20, 9001]
    assert "1 users; 1 failed" in fake.sent[-1]["text"]
    status = await db.query("SELECT status FROM users WHERE id = 2")
    assert status[0]["status"] == "blocked"


# --- events --------------------------------------------------------------------


def test_read_event(tmp_path: Path) -> None:
    path = tmp_path / "event.json"
    path.write_text(json.dumps({"action": "parse-cv", "client_payload": {"user_id": 5}}))
    assert read_event(path) == ("parse-cv", {"user_id": 5})
    path.write_text(json.dumps({"ref": "refs/heads/main"}))
    with pytest.raises(EventError, match="no action"):
        read_event(path)
    path.write_text(json.dumps({"action": "x", "client_payload": [1]}))
    with pytest.raises(EventError, match="not an object"):
        read_event(path)
    with pytest.raises(EventError, match="cannot read"):
        read_event(tmp_path / "missing.json")


@pytest.mark.parametrize(
    ("action", "payload", "error"),
    [
        ("backfill-user", {}, "bad payload"),
        ("backfill-user", {"user_id": "5"}, "bad payload"),  # strict: no string ids
        ("backfill-user", {"user_id": 5, "extra": 1}, "bad payload"),
        ("broadcast", {"html": "", "admin_id": 1}, "bad payload"),
        ("run-pipeline", {"x": 1}, "bad payload"),
        ("deploy", {}, "unknown event type"),
    ],
)
async def test_bad_events(
    db: SqliteBackend, config: Config, action: str, payload: dict[str, Any], error: str
) -> None:
    with pytest.raises(EventError, match=error):
        await handle_event(_pipeline(db, config, FakeTelegram()), action, payload)


async def test_events_run_their_job(db: SqliteBackend, config: Config) -> None:
    await add_job(db, 1)
    await add_user(db, 1)
    fake = FakeTelegram()
    pipeline = _pipeline(db, config, fake)
    summary = await handle_event(pipeline, "send-digest", {"user_id": 1})
    assert summary.trigger == "send-digest"
    summary = await handle_event(pipeline, "backfill-user", {"user_id": 1})
    assert summary.stages[1].counts == {"sent": 1}
    summary = await handle_event(pipeline, "parse-cv", {"user_id": 1})
    assert summary.stages[0].counts == {"no_cv": 1}
    summary = await handle_event(pipeline, "broadcast", {"html": "hi", "admin_id": 9})
    assert summary.trigger == "broadcast"
    summary = await handle_event(pipeline, "run-pipeline", {})
    assert summary.trigger == "dispatch"
    triggers = [r["trigger"] for r in await db.query("SELECT trigger FROM runs ORDER BY id")]
    assert triggers == ["send-digest", "backfill-user", "parse-cv", "broadcast", "dispatch"]


# --- summary -------------------------------------------------------------------


def test_markdown_summary() -> None:
    summary = RunSummary("schedule", NOW)
    summary.stages = [
        StageReport("collect", Counter({"new jobs": 3}), ["bdjobs: ok | 3"], 1.25),
        StageReport("notify", error="TELEGRAM_BOT_TOKEN is not set"),
    ]
    text = markdown(summary)
    assert "## Pipeline run: schedule, failed" in text
    assert "| collect | 1.2 s | new jobs: 3 |" in text
    assert "| notify | 0.0 s | **failed**: TELEGRAM_BOT_TOKEN is not set |" in text
    assert "- collect: bdjobs: ok \\| 3" in text
