"""Rebuild what gates and extraction need from a stored `jobs` row."""

import json
from datetime import date, datetime
from typing import Any

from jobhunt.db import Row
from jobhunt.sources.models import PostedAtSource, RawListing


def listing_from_row(row: Row) -> RawListing:
    extra: dict[str, Any] = json.loads(str(row["listing_json"] or "{}"))
    posted_at_source: PostedAtSource = row["posted_at_source"]  # type: ignore[assignment]
    return RawListing(
        source=str(row["source"]),
        source_job_id=str(row["source_job_id"]),
        url=str(row["url"]),
        title=str(row["title"]),
        description=str(row["jd_text"] or ""),
        description_format="text",
        company=_text(row["company"]),
        location=_text(row["location_raw"]),
        location_restrictions=tuple(extra.get("location_restrictions") or ()),
        work_mode=extra.get("work_mode"),
        employment_type=extra.get("employment_type"),
        posted_at=_datetime(row["posted_at"]),
        posted_at_source=posted_at_source,
        deadline=date.fromisoformat(str(row["deadline"])) if row["deadline"] else None,
        salary=extra.get("salary"),
        tags=tuple(extra.get("tags") or ()),
        extra=dict(extra.get("extra") or {}),
    )


def _text(value: object) -> str | None:
    return str(value) if value is not None else None


def _datetime(value: object) -> datetime | None:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")) if value else None
