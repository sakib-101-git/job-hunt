"""The schema in migrations/, applied to a fresh SQLite database (CI requirement)."""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from jobhunt.db import ConstraintError, DatabaseError, SqliteBackend, apply_migrations
from jobhunt.db.migrations import applied_migrations, read_migrations

pytestmark = pytest.mark.anyio

TABLES = {
    "users", "user_prefs", "user_profiles", "jobs", "job_domains", "job_facts", "matches",
    "notifications", "feedback", "source_state", "runs", "llm_usage", "audits", "kv",
    "title_classes", "rate_limits",
}  # fmt: skip


@pytest.fixture
async def db(repo_root: Path) -> AsyncIterator[SqliteBackend]:
    backend = SqliteBackend(":memory:")
    await apply_migrations(backend, repo_root / "migrations")
    yield backend
    await backend.aclose()


async def _user(db: SqliteBackend, user_id: int = 1) -> None:
    await db.execute(
        "INSERT INTO users (id, chat_id, locale) VALUES (?, ?, 'en')", (user_id, user_id)
    )


async def _job(db: SqliteBackend, job_id: int, fingerprint: str = "fp") -> None:
    await db.execute(
        "INSERT INTO jobs (id, source, source_job_id, url, canonical_url, fingerprint, title,"
        " posted_at_source, first_seen_at, last_seen_at)"
        " VALUES (?, 'bdjobs', ?, 'https://x', 'https://x', ?, 'Engineer', 'api',"
        " '2026-10-18T00:00:00Z', '2026-10-18T00:00:00Z')",
        (job_id, str(job_id), fingerprint),
    )


async def _match(db: SqliteBackend, match_id: int, user_id: int, job_id: int) -> None:
    await db.execute(
        "INSERT INTO matches (id, user_id, job_id, score, breakdown_json, explanation_json,"
        " gate_results_json, scorer_version) VALUES (?, ?, ?, 8.5, '{}', '{}', '{}', 'v1')",
        (match_id, user_id, job_id),
    )


async def test_all_tables_exist_and_are_strict(db: SqliteBackend) -> None:
    rows = await db.query("SELECT name, strict FROM pragma_table_list WHERE schema = 'main'")
    tables = {str(r["name"]): r["strict"] for r in rows if not str(r["name"]).startswith("sqlite_")}
    assert set(tables) == TABLES | {"d1_migrations"}
    assert all(tables[name] == 1 for name in TABLES)


async def test_migrations_are_recorded_and_idempotent(db: SqliteBackend, repo_root: Path) -> None:
    names = [m.name for m in read_migrations(repo_root / "migrations")]
    assert await applied_migrations(db) == names
    assert await apply_migrations(db, repo_root / "migrations") == []


async def test_deleting_a_user_removes_all_their_data(db: SqliteBackend) -> None:
    await _user(db)
    await _job(db, 10)
    await _match(db, 100, 1, 10)
    await db.execute(
        "INSERT INTO notifications (match_id, user_id, job_id, fingerprint)"
        " VALUES (100, 1, 10, 'fp')"
    )
    await db.execute("INSERT INTO feedback (user_id, match_id, kind) VALUES (1, 100, 'saved')")
    await db.execute("DELETE FROM users WHERE id = 1")
    for table in ("matches", "notifications", "feedback"):
        assert await db.query(f"SELECT 1 FROM {table}") == []  # noqa: S608 (fixed names)
    assert len(await db.query("SELECT 1 FROM jobs")) == 1


async def test_same_job_from_two_sources_is_notified_once(db: SqliteBackend) -> None:
    await _user(db)
    await _job(db, 10, fingerprint="same-job")
    await _job(db, 11, fingerprint="same-job")
    await _match(db, 100, 1, 10)
    await _match(db, 101, 1, 11)
    insert = (
        "INSERT INTO notifications (match_id, user_id, job_id, fingerprint) VALUES (?, 1, ?, ?)"
    )
    await db.execute(insert, (100, 10, "same-job"))
    with pytest.raises(ConstraintError, match="UNIQUE"):
        await db.execute(insert, (101, 11, "same-job"))


async def test_a_match_is_unique_per_user_and_job(db: SqliteBackend) -> None:
    await _user(db)
    await _job(db, 10)
    await _match(db, 100, 1, 10)
    with pytest.raises(ConstraintError, match="UNIQUE"):
        await _match(db, 101, 1, 10)


async def test_one_primary_domain_per_job(db: SqliteBackend) -> None:
    await _job(db, 10)
    insert = "INSERT INTO job_domains VALUES (10, ?, 1.0, 'exact', ?)"
    await db.execute(insert, ("ai-ml", 1))
    await db.execute(insert, ("data-science-analytics", 0))
    with pytest.raises(ConstraintError, match="UNIQUE"):
        await db.execute(insert, ("nlp-speech", 1))


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO users (id, chat_id, locale, status) VALUES (2, 2, 'en', 'gone')",
        "INSERT INTO users (id, chat_id, locale) VALUES (2, 'not-a-number', 'en')",
        "INSERT INTO users (id, chat_id, locale, onboarding_state) VALUES (2, 2, 'en', '{')",
        "INSERT INTO kv (key, value) VALUES ('k', NULL)",
        "INSERT INTO matches (user_id, job_id, score, breakdown_json, explanation_json,"
        " gate_results_json, scorer_version) VALUES (99, 99, 5, '{}', '{}', '{}', 'v1')",
    ],
    ids=["bad-enum", "wrong-type", "invalid-json", "not-null", "missing-foreign-key"],
)
async def test_invalid_rows_are_rejected(db: SqliteBackend, sql: str) -> None:
    with pytest.raises(ConstraintError):
        await db.execute(sql)


async def test_score_must_be_within_range(db: SqliteBackend) -> None:
    await _user(db)
    await _job(db, 10)
    with pytest.raises(ConstraintError, match="CHECK"):
        await db.execute(
            "INSERT INTO matches (user_id, job_id, score, breakdown_json, explanation_json,"
            " gate_results_json, scorer_version) VALUES (1, 10, 10.5, '{}', '{}', '{}', 'v1')"
        )


async def test_syntax_errors_are_database_errors(db: SqliteBackend) -> None:
    with pytest.raises(DatabaseError) as exc:
        await db.execute("SELEC 1")
    assert not isinstance(exc.value, ConstraintError)
