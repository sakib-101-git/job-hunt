"""Upsert normalized listings into `jobs`.

A job seen again keeps its `first_seen_at` and gets a fresh `last_seen_at`. Its
description is replaced only by a longer one, so a listing-only re-sighting
(details are fetched once, for new jobs) never erases the full text.

Whether a job is new is decided by an `INSERT ... DO NOTHING` that returns a row
only when it inserted one, never by comparing timestamps.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from jobhunt.db import Database, Statement, timestamp
from jobhunt.normalize.jobs import JobRecord

_COLUMNS = """jobs (source, source_job_id, url, canonical_url, fingerprint, title, company,
                  location_raw, posted_at, posted_at_source, deadline, jd_text, jd_hash,
                  listing_json, first_seen_at, last_seen_at)"""

_INSERT_NEW = f"""
INSERT INTO {_COLUMNS}
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (source, source_job_id) DO NOTHING
RETURNING id
"""  # noqa: S608 (constant)

_UPSERT = """
INSERT INTO jobs (source, source_job_id, url, canonical_url, fingerprint, title, company,
                  location_raw, posted_at, posted_at_source, deadline, jd_text, jd_hash,
                  listing_json, first_seen_at, last_seen_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
    last_seen_at = excluded.last_seen_at
RETURNING id
"""


@dataclass(frozen=True)
class StoredJob:
    id: int
    is_new: bool
    record: JobRecord


async def store_jobs(
    db: Database, records: Sequence[JobRecord], now: datetime, batch_size: int
) -> list[StoredJob]:
    """Upsert `records`; returns each row's id and whether it was new."""
    at = timestamp(now)
    stored: list[StoredJob] = []
    for start in range(0, len(records), batch_size):
        chunk = records[start : start + batch_size]
        statements = []
        for record in chunk:
            params = _params(record, at)
            statements += [Statement(_INSERT_NEW, params), Statement(_UPSERT, params)]
        results = await db.batch(statements)
        for i, record in enumerate(chunk):
            inserted, upserted = results[2 * i], results[2 * i + 1]
            job_id = int(str(upserted.rows[0]["id"]))
            stored.append(StoredJob(job_id, bool(inserted.rows), record))
    return stored


def _params(r: JobRecord, at: str | None) -> tuple[str | int | None, ...]:
    return (
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
        at,
        at,
    )
