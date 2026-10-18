import json
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from jobhunt.config import Config
from jobhunt.db import Result, SqliteBackend, Statement, apply_migrations
from jobhunt.jobs.store import store_jobs
from jobhunt.normalize.jobs import JobNormalizer
from jobhunt.normalize.text import clean_text, html_to_text, truncate
from jobhunt.sources.models import RawListing

NOW = datetime(2026, 10, 18, 9, 0, tzinfo=UTC)


def _listing(**changes: object) -> RawListing:
    base: dict[str, object] = {
        "source": "bdjobs",
        "source_job_id": "1",
        "url": "https://bdjobs.com/h/details/1?utm_source=x&b=2&a=1#top",
        "title": "Sr. Software Engineer",
        "description": "<p>Build things</p><ul><li>Python</li></ul>",
        "description_format": "html",
        "company": "Example Tech Ltd.",
        "location": "Dhaka (Banani)",
        "posted_at": NOW - timedelta(days=1),
        "posted_at_source": "api",
    }
    base.update(changes)
    return RawListing(**base)  # type: ignore[arg-type]


@pytest.fixture(scope="module")
def normalizer(config: Config) -> JobNormalizer:
    return JobNormalizer(config)


# --- text ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("markup", "text"),
    [
        ("<p>Hello&nbsp;world</p><ul><li>A</li><li>B &amp; C</li></ul>",
         "Hello world\n\n• A\n• B & C"),
        ("&lt;p&gt;Double &amp;amp; escaped&lt;/p&gt;", "Double & escaped"),
        ("<style>p{}</style><script>x()</script><b>Bold</b> text", "Bold text"),
        ("line<br>break<br/>again", "line\nbreak\nagain"),
        ("<p>a</p><p></p><p></p><p>b</p>", "a\n\nb"),
        ("plain text", "plain text"),
    ],
)  # fmt: skip
def test_html_to_text(markup: str, text: str) -> None:
    assert html_to_text(markup) == text


def test_clean_text_and_truncate() -> None:
    assert clean_text("  a \t b \r\n\r\n\r\n c ") == "a b\n\nc"
    assert clean_text("ﬁle") == "file"  # NFKC
    assert truncate("short", 10) == "short"
    assert truncate("one two three four five", 12) == "one two …"
    assert truncate("x" * 30, 10) == "x" * 10 + " …"


# --- records ------------------------------------------------------------------


def test_record(normalizer: JobNormalizer) -> None:
    record = normalizer.record(_listing(deadline=date(2026, 11, 1), salary="Negotiable"))
    assert record.canonical_url == "https://bdjobs.com/h/details/1?a=1&b=2"
    assert record.jd_text == "Build things\n\n• Python"
    assert len(record.jd_hash) == 64
    assert record.deadline == date(2026, 11, 1)
    assert json.loads(record.listing_json)["salary"] == "Negotiable"


def test_text_descriptions_are_not_parsed_as_html(normalizer: JobNormalizer) -> None:
    record = normalizer.record(_listing(description="a <b> c", description_format="text"))
    assert record.jd_text == "a <b> c"


def test_long_descriptions_are_capped(config: Config, normalizer: JobNormalizer) -> None:
    record = normalizer.record(_listing(description="word " * 5000))
    assert len(record.jd_text) <= config.app.normalization.jd_max_chars_stored + 2


def test_unknown_date_is_not_invented(normalizer: JobNormalizer) -> None:
    record = normalizer.record(_listing(posted_at=None, posted_at_source="api"))
    assert record.posted_at is None
    assert record.posted_at_source == "unknown"


@pytest.mark.parametrize(
    ("a", "b"),
    [
        # Same job on two boards: punctuation, case, company suffix and city spelling differ.
        (
            {"title": "Sr. Software Engineer", "company": "Example Tech Ltd.",
             "location": "Chittagong"},
            {"title": "Senior Software Developer", "company": "EXAMPLE TECH LIMITED",
             "location": "Chattogram, Bangladesh"},
        ),
        (
            {"title": "SQA Engineer", "location": "Gulshan, Dhaka"},
            {"title": "Software Quality Assurance Engineer", "location": "Dhaka"},
        ),
        (
            {"title": "Backend Engineer", "location": None, "work_mode": "remote"},
            {"title": "Back-end Engineer", "location": None, "work_mode": "Remote"},
        ),
    ],
)  # fmt: skip
def test_same_job_same_fingerprint(
    normalizer: JobNormalizer, a: dict[str, object], b: dict[str, object]
) -> None:
    assert normalizer.fingerprint(_listing(**a)) == normalizer.fingerprint(
        _listing(source="linkedin_alerts", **b)
    )


@pytest.mark.parametrize(
    "change",
    [
        {"title": "Software Engineer"},  # seniority differs
        {"title": "Senior QA Engineer"},
        {"company": "Other Tech Ltd"},
        {"location": "Sylhet"},
    ],
)
def test_different_jobs_differ(normalizer: JobNormalizer, change: dict[str, object]) -> None:
    assert normalizer.fingerprint(_listing()) != normalizer.fingerprint(_listing(**change))


def test_city_key(normalizer: JobNormalizer) -> None:
    assert normalizer.city_key(_listing(location="Uttara")) == "dhaka"
    assert normalizer.city_key(_listing(location="CTG")) == "chattogram"
    assert normalizer.city_key(_listing(location="Berlin, Germany")) == "berlin"
    assert normalizer.city_key(_listing(location=None, work_mode=None)) == ""


def test_company_key(normalizer: JobNormalizer) -> None:
    assert normalizer.company_key("Brain Station 23 Ltd.") == "brain station 23"
    assert normalizer.company_key(None) == ""


# --- storage ------------------------------------------------------------------


@pytest.fixture
async def db(repo_root: Path) -> AsyncIterator[SqliteBackend]:
    backend = SqliteBackend(":memory:")
    await apply_migrations(backend, repo_root / "migrations")
    yield backend
    await backend.aclose()


@pytest.mark.anyio
async def test_store_new_then_seen_again(db: SqliteBackend, normalizer: JobNormalizer) -> None:
    first = await store_jobs(db, [normalizer.record(_listing())], NOW, batch_size=10)
    assert [job.is_new for job in first] == [True]

    later = NOW + timedelta(hours=1)
    # Seen again with only a short description (no details fetched): keep the long one.
    short = _listing(description="<p>x</p>", deadline=date(2026, 11, 2))
    again = await store_jobs(db, [normalizer.record(short)], later, batch_size=10)
    assert [(job.id, job.is_new) for job in again] == [(first[0].id, False)]

    row = (await db.query("SELECT * FROM jobs"))[0]
    assert row["jd_text"] == "Build things\n\n• Python"
    assert row["first_seen_at"] == "2026-10-18T09:00:00Z"
    assert row["last_seen_at"] == "2026-10-18T10:00:00Z"
    assert row["deadline"] == "2026-11-02"
    assert row["status"] == "open"


@pytest.mark.anyio
async def test_store_in_batches(db: SqliteBackend, normalizer: JobNormalizer) -> None:
    records = [normalizer.record(_listing(source_job_id=str(i))) for i in range(7)]
    stored = await store_jobs(db, records, NOW, batch_size=3)
    assert len({job.id for job in stored}) == 7
    assert all(job.is_new for job in stored)
    assert len(await db.query("SELECT id FROM jobs")) == 7


@pytest.mark.anyio
async def test_rerun_in_the_same_second_is_not_new(
    db: SqliteBackend, normalizer: JobNormalizer
) -> None:
    record = normalizer.record(_listing())
    assert [j.is_new for j in await store_jobs(db, [record], NOW, batch_size=10)] == [True]
    assert [j.is_new for j in await store_jobs(db, [record], NOW, batch_size=10)] == [False]


class CountingDb(SqliteBackend):
    """Counts the statements that write to jobs."""

    def __init__(self) -> None:
        super().__init__(":memory:")
        self.writes = 0

    async def batch(self, statements: Sequence[Statement]) -> list[Result]:
        self.writes += sum(1 for s in statements if s.sql.lstrip().startswith("INSERT"))
        return await super().batch(statements)


@pytest.mark.anyio
async def test_unchanged_listings_are_not_rewritten(
    repo_root: Path, normalizer: JobNormalizer
) -> None:
    db = CountingDb()
    await apply_migrations(db, repo_root / "migrations")
    record = normalizer.record(_listing())
    await store_jobs(db, [record], NOW, 10, refresh_hours=24)
    first = db.writes
    # Polled again an hour later, unchanged: nothing is written.
    again = await store_jobs(db, [record], NOW + timedelta(hours=1), 10, refresh_hours=24)
    assert db.writes == first
    assert again[0].is_new is False
    row = (await db.query("SELECT last_seen_at FROM jobs"))[0]
    assert row["last_seen_at"] == "2026-10-18T09:00:00Z"
    # A day later the sighting is refreshed; a change is written at once.
    await store_jobs(db, [record], NOW + timedelta(hours=25), 10, refresh_hours=24)
    assert db.writes == first + 1
    changed = normalizer.record(_listing(title="Senior Python Developer"))
    await store_jobs(db, [changed], NOW + timedelta(hours=26), 10, refresh_hours=24)
    assert db.writes == first + 2
    assert (await db.query("SELECT title FROM jobs"))[0]["title"] == "Senior Python Developer"
    await db.aclose()
