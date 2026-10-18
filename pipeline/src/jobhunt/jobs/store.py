"""Upsert normalized listings into `jobs`.

A job seen again keeps its `first_seen_at` and gets a fresh `last_seen_at`. Its
description is replaced only by a longer one, so a listing-only re-sighting
(details are fetched once, for new jobs) never erases the full text.

Whether a job is new is decided by an `INSERT ... DO NOTHING` that returns a row
only when it inserted one, never by comparing timestamps.

A listing seen again with nothing changed (same `record_hash`) is not written
at all unless its `last_seen_at` is older than `last_seen_refresh_hours`: the
sources are polled every few minutes, and D1's free tier counts every row and
index entry written.
"""

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from jobhunt.db import Database, Statement, timestamp
from jobhunt.normalize.jobs import JobRecord

_COLUMNS = """jobs (source, source_job_id, url, canonical_url, fingerprint, title, company,
                  location_raw, posted_at, posted_at_source, deadline, jd_text, jd_hash,
                  listing_json, record_hash, first_seen_at, last_seen_at)"""

_INSERT_NEW = f"""
INSERT INTO {_COLUMNS}
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (source, source_job_id) DO NOTHING
RETURNING id
"""  # noqa: S608 (constant)

_UPSERT = """
INSERT INTO jobs (source, source_job_id, url, canonical_url, fingerprint, title, company,
                  location_raw, posted_at, posted_at_source, deadline, jd_text, jd_hash,
                  listing_json, record_hash, first_seen_at, last_seen_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (source, source_job_id) DO UPDATE SET
    url = excluded.url,
    canonical_url = excluded.canonical_url,
    title = excluded.title,
    company = excluded.company,
    location_raw = COALESCE(excluded.location_raw, jobs.location_raw),
    posted_at = COALESCE(jobs.posted_at, excluded.posted_at),
    posted_at_source = CASE WHEN jobs.posted_at IS NULL
                            THEN excluded.posted_at_source ELSE jobs.posted_at_source END,
    deadline = COALESCE(excluded.deadline, jobs.deadline),
    jd_text = CASE WHEN jobs.jd_text IS NULL OR length(excluded.jd_text) > length(jobs.jd_text)
                   THEN excluded.jd_text ELSE jobs.jd_text END,
    jd_hash = CASE WHEN jobs.jd_text IS NULL OR length(excluded.jd_text) > length(jobs.jd_text)
                   THEN excluded.jd_hash ELSE jobs.jd_hash END,
    listing_json = excluded.listing_json,
    record_hash = excluded.record_hash,
    last_seen_at = excluded.last_seen_at
RETURNING id
"""


@dataclass(frozen=True)
class StoredJob:
    id: int
    is_new: bool
    record: JobRecord


async def store_jobs(
    db: Database,
    records: Sequence[JobRecord],
    now: datetime,
    batch_size: int,
    refresh_hours: float = 0,
) -> list[StoredJob]:
    """Store `records`; returns each row's id and whether it was new. Rows that
    exist unchanged and were seen within `refresh_hours` are left alone."""
    at = timestamp(now)
    stale_before = timestamp(now - timedelta(hours=refresh_hours))
    stored: list[StoredJob] = []
    for start in range(0, len(records), batch_size):
        chunk = records[start : start + batch_size]
        known = await _existing(db, chunk)
        statements: list[Statement] = []
        plan: list[tuple[JobRecord, int | None, str]] = []  # record, kept id, action
        for record in chunk:
            params = _params(record, at)
            row = known.get((record.source, record.source_job_id))
            if row is None:
                statements += [Statement(_INSERT_NEW, params), Statement(_UPSERT, params)]
                plan.append((record, None, "new"))
            elif row[1] == params[_HASH] and row[2] >= (stale_before or ""):
                plan.append((record, row[0], "unchanged"))
            else:
                statements.append(Statement(_UPSERT, params))
                plan.append((record, None, "update"))
        results = iter(await db.batch(statements)) if statements else iter(())
        for record, kept, action in plan:
            if action == "unchanged" and kept is not None:
                stored.append(StoredJob(kept, False, record))
                continue
            inserted = next(results) if action == "new" else None
            upserted = next(results)
            job_id = int(str(upserted.rows[0]["id"]))
            stored.append(StoredJob(job_id, bool(inserted and inserted.rows), record))
    return stored


async def _existing(
    db: Database, chunk: Sequence[JobRecord]
) -> dict[tuple[str, str], tuple[int, str, str]]:
    """(source, source_job_id) -> (id, record_hash, last_seen_at) for stored rows."""
    by_source: dict[str, list[str]] = {}
    for record in chunk:
        by_source.setdefault(record.source, []).append(record.source_job_id)
    found: dict[tuple[str, str], tuple[int, str, str]] = {}
    for source, ids in by_source.items():
        marks = ", ".join("?" for _ in ids)
        rows = await db.query(
            "SELECT id, source_job_id, record_hash, last_seen_at FROM jobs"  # noqa: S608
            f" WHERE source = ? AND source_job_id IN ({marks})",
            (source, *ids),
        )
        for row in rows:
            found[(source, str(row["source_job_id"]))] = (
                int(str(row["id"])),
                str(row["record_hash"] or ""),
                str(row["last_seen_at"]),
            )
    return found


_HASH = 14  # position of record_hash in _params


def _params(r: JobRecord, at: str | None) -> tuple[str | int | None, ...]:
    content = (
        r.source,
        r.source_job_id,
        r.url,
        r.canonical_url,
        r.fingerprint,
        r.title,
        r.company,
        r.location_raw,
        timestamp(r.posted_at),
        r.posted_at_source,
        r.deadline.isoformat() if r.deadline else None,
        r.jd_text,
        r.jd_hash,
        r.listing_json,
    )
    digest = hashlib.sha256("\x1f".join(str(v) for v in content).encode("utf-8")).hexdigest()
    return (*content, digest, at, at)
