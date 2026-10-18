import json
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from jobhunt.config import Config
from jobhunt.db import SqliteBackend, apply_migrations
from jobhunt.extract.schema import JobFacts
from jobhunt.notify.outbox import enqueue
from jobhunt.notify.sender import SendPlan, in_quiet_hours, send_pending
from jobhunt.notify.telegram import BlockedError, TelegramClient, TelegramError
from jobhunt.score.matching import match

pytestmark = pytest.mark.anyio
NOW = datetime(2026, 10, 18, 6, 0, tzinfo=UTC)  # 12:00 in Dhaka

Handler = Callable[[httpx.Request], httpx.Response]


class Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(round(seconds, 3))
        self.now += seconds


def ok(message_id: int = 1) -> httpx.Response:
    return httpx.Response(200, json={"ok": True, "result": {"message_id": message_id}})


def error(code: int, description: str, retry_after: int | None = None) -> httpx.Response:
    body: dict[str, Any] = {"ok": False, "error_code": code, "description": description}
    if retry_after is not None:
        body["parameters"] = {"retry_after": retry_after}
    return httpx.Response(code, json=body)


class FakeTelegram:
    def __init__(self, *responses: httpx.Response, default: Handler | None = None) -> None:
        self.responses = list(responses)
        self.default = default
        self.sent: list[dict[str, Any]] = []
        self.next_id = 100

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.sent.append(body)
        if self.responses:
            return self.responses.pop(0)
        if self.default:
            return self.default(request)
        self.next_id += 1
        return ok(self.next_id)


def _telegram(config: Config, fake: Handler, clock: Clock | None = None) -> TelegramClient:
    clock = clock or Clock()
    client = httpx.AsyncClient(transport=httpx.MockTransport(fake))
    return TelegramClient(config.app.notifications, "TOKEN", client, clock, clock.sleep)


# --- client --------------------------------------------------------------------


async def test_send_message_request(config: Config) -> None:
    fake = FakeTelegram(ok(42))
    telegram = _telegram(config, fake)
    keyboard = [[{"text": "Apply", "url": "https://x"}]]
    assert await telegram.send_message(7, "<b>hi</b>", keyboard) == 42
    body = fake.sent[0]
    assert body == {
        "chat_id": 7,
        "text": "<b>hi</b>",
        "parse_mode": "HTML",
        "link_preview_options": {"is_disabled": True},
        "reply_markup": {"inline_keyboard": keyboard},
    }


async def test_rate_limits_space_messages(config: Config) -> None:
    clock = Clock()
    telegram = _telegram(config, FakeTelegram(), clock)
    await telegram.send_message(1, "a")
    await telegram.send_message(2, "b")  # another chat: only the global gap (1/25 s)
    await telegram.send_message(1, "c")  # same chat again: the per-chat gap (1.1 s)
    assert clock.sleeps == [0.04, 1.06]


async def test_retry_after_and_server_errors(config: Config) -> None:
    clock = Clock()
    fake = FakeTelegram(
        error(429, "Too Many Requests", retry_after=7), error(502, "Bad Gateway"), ok(5)
    )
    telegram = _telegram(config, fake, clock)
    assert await telegram.send_message(1, "x") == 5
    assert 7.0 in clock.sleeps
    assert config.app.notifications.retry_backoff_seconds * 2 in clock.sleeps


async def test_blocked_and_bad_requests(config: Config) -> None:
    with pytest.raises(BlockedError):
        await _telegram(
            config, FakeTelegram(error(403, "Forbidden: bot was blocked by the user"))
        ).send_message(1, "x")
    with pytest.raises(TelegramError, match="message is too long"):
        await _telegram(
            config, FakeTelegram(error(400, "Bad Request: message is too long"))
        ).send_message(1, "x")


async def test_network_errors_give_up(config: Config) -> None:
    def down(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    with pytest.raises(TelegramError, match="network error"):
        await _telegram(config, down).send_message(1, "x")


@pytest.mark.parametrize(
    ("hours", "when", "quiet"),
    [
        (None, NOW, False),
        (
            {"start": "22:00", "end": "08:00"},
            datetime(2026, 10, 18, 17, 0, tzinfo=UTC),
            True,
        ),  # 23:00 Dhaka
        (
            {"start": "22:00", "end": "08:00"},
            datetime(2026, 10, 18, 3, 0, tzinfo=UTC),
            False,
        ),  # 09:00
        (
            {"start": "13:00", "end": "14:00"},
            datetime(2026, 10, 18, 7, 30, tzinfo=UTC),
            True,
        ),  # 13:30
        (
            {"start": "13:00", "end": "14:00", "tz": "UTC"},
            datetime(2026, 10, 18, 7, 30, tzinfo=UTC),
            False,
        ),
    ],
)
def test_quiet_hours(
    config: Config, hours: dict[str, str] | None, when: datetime, quiet: bool
) -> None:
    from zoneinfo import ZoneInfo

    raw = json.dumps(hours) if hours else None
    assert in_quiet_hours(raw, when, ZoneInfo(config.app.timezone)) is quiet


# --- outbox and sender ---------------------------------------------------------


def _facts(**changes: Any) -> str:
    base: dict[str, Any] = {
        "is_cse_role": True, "is_cse_role_evidence": "x", "primary_domain": "ai-ml",
        "secondary_domains": [], "normalized_title": "ML Engineer", "seniority": "junior",
        "seniority_evidence": None, "min_years": 0, "max_years": 1, "years_evidence": "0-1 years",
        "education": {"level": "unknown", "fields": [], "required": False},
        "education_evidence": None, "required_skills": ["python"], "preferred_skills": [],
        "employment_type": "full_time", "work_mode": "onsite", "country": "BD", "city": "Dhaka",
        "remote_eligibility": "not_remote", "remote_eligibility_evidence": "Dhaka",
        "timezone_requirement": None, "salary": None, "application_deadline": None,
        "deadline_evidence": None, "posted_date_in_text": None, "language_requirements": [],
        "red_flags": [], "summary": "Models.",
    }  # fmt: skip
    base.update(changes)
    return JobFacts.model_validate(base).model_dump_json()


@pytest.fixture
async def db(repo_root: Path) -> AsyncIterator[SqliteBackend]:
    backend = SqliteBackend(":memory:")
    await apply_migrations(backend, repo_root / "migrations")
    yield backend
    await backend.aclose()


async def add_job(
    db: SqliteBackend, job_id: int, *, fingerprint: str | None = None, seeded: bool = False,
    posted_days_ago: int = 1, title: str = "Junior Machine Learning Engineer",
) -> None:  # fmt: skip
    gates = {
        "needs_facts": [], "seeded": seeded,
        "location": {"status": "pass", "reason": "bangladesh", "evidence": "Dhaka"},
    }  # fmt: skip
    posted = (NOW - timedelta(days=posted_days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    await db.execute(
        "INSERT INTO jobs (id, source, source_job_id, url, canonical_url, fingerprint, title,"
        " company, location_raw, posted_at, posted_at_source, first_seen_at, last_seen_at,"
        " gates_json) VALUES (?, 'bdjobs', ?, ?, ?, ?, ?, 'Acme', 'Dhaka', ?, 'api', ?, ?, ?)",
        (job_id, str(job_id), f"https://x/{job_id}", f"https://x/{job_id}",
         fingerprint or f"fp{job_id}", title, posted, posted, posted, json.dumps(gates)),
    )  # fmt: skip
    await db.execute("INSERT INTO job_domains VALUES (?, 'ai-ml', 1.0, 'exact', 1)", (job_id,))
    await db.execute(
        "INSERT INTO job_facts (job_id, facts_json, provider, model, prompt_version, input_hash,"
        " grounding_ok) VALUES (?, ?, 'groq', 'm', 'v', 'h', 1)",
        (job_id, _facts()),
    )


async def add_user(
    db: SqliteBackend, user_id: int, *, status: str = "active", mode: str = "instant",
    min_score: float = 6.0, cap: int = 5, quiet: dict[str, str] | None = None,
) -> None:  # fmt: skip
    await db.execute(
        "INSERT INTO users (id, chat_id, locale, status) VALUES (?, ?, 'en', ?)",
        (user_id, user_id * 10, status),
    )
    await db.execute(
        "INSERT INTO user_prefs (user_id, tracks_json, include_adjacent, experience_level,"
        " locations_json, job_types_json, min_score, delivery_mode, quiet_hours_json,"
        " max_per_cycle) VALUES (?, '[\"ai_ml\"]', 1, 'fresh_grad', '[\"bangladesh\"]',"
        " '[\"full_time\"]', ?, ?, ?, ?)",
        (user_id, min_score, mode, json.dumps(quiet) if quiet else None, cap),
    )
    await db.execute(
        "INSERT INTO user_profiles (user_id, skills_json) VALUES (?, '[\"python\"]')", (user_id,)
    )


async def _queue(db: SqliteBackend, config: Config) -> dict[str, int]:
    new, _ = await match(db, config, NOW)
    return dict(await enqueue(db, new, NOW))


async def _statuses(db: SqliteBackend) -> dict[tuple[int, int], str]:
    rows = await db.query("SELECT user_id, job_id, status FROM notifications")
    return {(int(str(r["user_id"])), int(str(r["job_id"]))): str(r["status"]) for r in rows}


async def test_outbox_rules(db: SqliteBackend, config: Config) -> None:
    await add_job(db, 1)
    await add_job(db, 2, fingerprint="fp1")  # the same job from another board
    await add_job(db, 3, seeded=True)
    await add_user(db, 1)
    await add_user(db, 2, status="paused")
    await add_user(db, 3, min_score=9.9)
    counts = await _queue(db, config)
    assert await _statuses(db) == {(1, 1): "pending"}
    assert counts == {"queued": 1, "not queued": 8}
    assert await _queue(db, config) == {}  # nothing new to match or queue


async def test_send_alerts_with_keyboard(db: SqliteBackend, config: Config) -> None:
    await add_job(db, 1)
    await add_user(db, 1)
    await _queue(db, config)
    fake = FakeTelegram()
    counts = await send_pending(db, config, _telegram(config, fake), NOW)
    assert counts == {"sent": 1}
    body = fake.sent[0]
    assert body["chat_id"] == 10
    assert body["text"].startswith("🎯 <b>Junior Machine Learning Engineer</b>")
    match_id = int(str((await db.query("SELECT id FROM matches"))[0]["id"]))
    keyboard = body["reply_markup"]["inline_keyboard"]
    assert keyboard[0][0] == {"text": "🔗 Apply", "url": "https://x/1"}
    assert keyboard[1][0]["callback_data"] == f"wy:{match_id}"
    assert [b["callback_data"] for b in keyboard[2]] == [
        f"ap:{match_id}",
        f"sv:{match_id}",
        f"hd:{match_id}",
    ]
    assert all(len(b.get("callback_data", "").encode()) <= 64 for row in keyboard for b in row)
    row = (await db.query("SELECT * FROM notifications"))[0]
    assert (row["status"], row["telegram_message_id"], row["attempts"]) == ("sent", 101, 1)
    assert row["sent_at"] == "2026-10-18T06:00:00Z"
    assert await send_pending(db, config, _telegram(config, fake), NOW) == {}  # sent once


async def test_cap_collapses_extras_into_one_list(db: SqliteBackend, config: Config) -> None:
    for job_id in range(1, 5):
        await add_job(db, job_id, title=f"ML Engineer {job_id}")
    await add_user(db, 1, cap=2)
    await _queue(db, config)
    fake = FakeTelegram()
    counts = await send_pending(db, config, _telegram(config, fake), NOW)
    assert counts == {"sent": 2, "sent in a list": 2}
    assert len(fake.sent) == 3
    listing = fake.sent[2]["text"]
    assert "<b>2 more matches</b>" in listing.splitlines()[0]
    assert '<a href="https://x/3">' in listing
    assert set((await _statuses(db)).values()) == {"sent"}


async def test_stale_jobs_are_skipped_at_send_time(db: SqliteBackend, config: Config) -> None:
    await add_job(db, 1, posted_days_ago=9)
    await add_user(db, 1)
    await _queue(db, config)
    later = NOW + timedelta(days=2)  # now 11 days old
    counts = await send_pending(db, config, _telegram(config, FakeTelegram()), later)
    assert counts == {"skipped: freshness:too_old": 1}
    row = (await db.query("SELECT status, last_error FROM notifications"))[0]
    assert (row["status"], row["last_error"]) == ("skipped", "freshness:too_old")


async def test_digest_quiet_hours_and_on_demand(db: SqliteBackend, config: Config) -> None:
    await add_job(db, 1)
    await add_user(db, 1, mode="digest")
    await add_user(db, 2, quiet={"start": "07:00", "end": "13:00"})  # it is 12:00 in Dhaka
    await _queue(db, config)
    fake = FakeTelegram()
    early = NOW.replace(hour=2)  # 08:00 in Dhaka, before digest_time
    assert await send_pending(db, config, _telegram(config, fake), early) == {}
    # A forced digest run serves digest users at any time.
    assert await send_pending(
        db, config, _telegram(config, fake), early, SendPlan(digest_run=True)
    ) == {"sent": 1}
    assert [b["chat_id"] for b in fake.sent] == [10]
    # /digest: the user asked, so quiet hours don't apply.
    on_demand = SendPlan(only_users=frozenset({2}))
    assert await send_pending(db, config, _telegram(config, fake), NOW, on_demand) == {"sent": 1}


async def test_digest_goes_out_once_a_day(db: SqliteBackend, config: Config) -> None:
    await add_job(db, 1)
    await add_job(db, 2, fingerprint="fp-2")
    await add_user(db, 1, mode="digest")
    await _queue(db, config)
    fake = FakeTelegram()
    # 12:00 in Dhaka: past 09:00 and nothing sent today, so the digest is due.
    assert await send_pending(db, config, _telegram(config, fake), NOW) == {"sent": 2}
    await add_job(db, 3, fingerprint="fp-3")
    await _queue(db, config)
    # Later the same day: already served.
    later = NOW + timedelta(hours=3)
    assert await send_pending(db, config, _telegram(config, fake), later) == {}
    # The next morning after 09:00 it is due again.
    tomorrow = NOW + timedelta(days=1)
    assert await send_pending(db, config, _telegram(config, fake), tomorrow) == {"sent": 1}


def test_digest_due() -> None:
    from zoneinfo import ZoneInfo

    from jobhunt.notify.sender import digest_due

    dhaka = ZoneInfo("Asia/Dhaka")
    nine = "09:00"
    assert not digest_due(None, NOW.replace(hour=2), dhaka, nine)  # 08:00 local
    assert digest_due(None, NOW.replace(hour=3), dhaka, nine)  # 09:00 local
    assert digest_due("2026-10-18T02:59:59Z", NOW, dhaka, nine)  # sent before 09:00
    assert not digest_due("2026-10-18T03:00:00Z", NOW, dhaka, nine)  # sent at 09:00


async def test_overlapping_senders_send_each_row_once(db: SqliteBackend, config: Config) -> None:
    await add_job(db, 1)
    await add_user(db, 1)
    await _queue(db, config)
    # Another run claimed the row a minute ago and is still sending it.
    lease_until = (NOW + timedelta(minutes=14)).strftime("%Y-%m-%dT%H:%M:%SZ")
    await db.execute("UPDATE notifications SET lease_id = 'other', lease_until = ?", (lease_until,))
    fake = FakeTelegram()
    assert await send_pending(db, config, _telegram(config, fake), NOW) == {}
    assert fake.sent == []
    # That run died: once its lease runs out the row is sent, and the lease cleared.
    later = NOW + timedelta(minutes=15)
    assert await send_pending(db, config, _telegram(config, fake), later) == {"sent": 1}
    row = (await db.query("SELECT status, lease_id, lease_until FROM notifications"))[0]
    assert (row["status"], row["lease_id"], row["lease_until"]) == ("sent", None, None)


async def test_a_row_is_claimed_by_one_run_only(db: SqliteBackend, config: Config) -> None:
    from jobhunt.notify.sender import _claim

    await add_job(db, 1)
    await add_user(db, 1)
    await _queue(db, config)
    # Both runs read the pending row before either claims it.
    rows = await db.query("SELECT id AS notification_id FROM notifications")
    first = await _claim(db, {1: rows}, NOW, 15)
    second = await _claim(db, {1: rows}, NOW, 15)
    assert len(first[1]) == 1
    assert second == {}


async def test_two_concurrent_runs_one_send(db: SqliteBackend, config: Config) -> None:
    import asyncio

    await add_job(db, 1)
    await add_user(db, 1)
    await _queue(db, config)
    fake = FakeTelegram()
    results = await asyncio.gather(
        send_pending(db, config, _telegram(config, fake), NOW),
        send_pending(db, config, _telegram(config, fake), NOW),
    )
    assert sorted(sum(c.values()) for c in results) == [0, 1]
    assert len(fake.sent) == 1


async def test_failures_are_reported_and_retried(db: SqliteBackend, config: Config) -> None:
    await add_job(db, 1)
    await add_user(db, 1)
    await _queue(db, config)
    failing = FakeTelegram(error(400, "Bad Request: chat not found"))
    assert await send_pending(db, config, _telegram(config, failing), NOW) == {"failed": 1}
    row = (await db.query("SELECT status, attempts, last_error FROM notifications"))[0]
    assert (row["status"], row["attempts"]) == ("failed", 1)
    assert "chat not found" in str(row["last_error"])
    assert await send_pending(db, config, _telegram(config, FakeTelegram()), NOW) == {"sent": 1}


async def test_failed_rows_stop_after_max_attempts(db: SqliteBackend, config: Config) -> None:
    await add_job(db, 1)
    await add_user(db, 1)
    await _queue(db, config)
    await db.execute(
        "UPDATE notifications SET status = 'failed', attempts = ?",
        (config.app.notifications.max_attempts,),
    )
    assert await send_pending(db, config, _telegram(config, FakeTelegram()), NOW) == {}


async def test_blocked_user(db: SqliteBackend, config: Config) -> None:
    await add_job(db, 1)
    await add_job(db, 2)
    await add_user(db, 1)
    await _queue(db, config)
    fake = FakeTelegram(ok(9), error(403, "Forbidden: bot was blocked by the user"))
    counts = await send_pending(db, config, _telegram(config, fake), NOW)
    assert counts == {"sent": 1, "failed: user blocked the bot": 1}
    assert (await db.query("SELECT status FROM users"))[0]["status"] == "blocked"
    assert sorted((await _statuses(db)).values()) == ["failed", "sent"]
    # A blocked user gets nothing more.
    assert await send_pending(db, config, _telegram(config, FakeTelegram()), NOW) == {}


async def test_job_filtered_after_queueing_is_skipped(db: SqliteBackend, config: Config) -> None:
    await add_job(db, 1)
    await add_user(db, 1)
    await _queue(db, config)
    await db.execute("UPDATE jobs SET status = 'expired'")
    counts = await send_pending(db, config, _telegram(config, FakeTelegram()), NOW)
    assert counts == {"skipped: job no longer open": 1}


async def test_match_stores_the_why_text(db: SqliteBackend, config: Config) -> None:
    await add_job(db, 1)
    await add_user(db, 1)
    await _queue(db, config)
    row = (await db.query("SELECT score, why_html FROM matches"))[0]
    title = f"<b>Why {row['score']!s}/10 for “Junior Machine Learning Engineer”</b>"
    assert str(row["why_html"]).startswith(title)


async def test_expired_pauses_end_at_the_next_run(db: SqliteBackend, config: Config) -> None:
    await add_job(db, 1)
    await add_user(db, 1, status="paused")
    await add_user(db, 2, status="paused")
    await db.execute("UPDATE users SET paused_until = '2026-10-18T05:59:59Z' WHERE id = 1")
    await db.execute("UPDATE users SET paused_until = '2026-10-19T00:00:00Z' WHERE id = 2")
    await _queue(db, config)
    rows = await db.query("SELECT id, status, paused_until FROM users ORDER BY id")
    assert [(r["status"], r["paused_until"]) for r in rows] == [
        ("active", None),
        ("paused", "2026-10-19T00:00:00Z"),
    ]
    assert await _statuses(db) == {(1, 1): "pending"}
