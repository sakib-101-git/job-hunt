"""What a source hands to the rest of the pipeline."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal

PostedAtSource = Literal["api", "rss", "text", "unknown"]


@dataclass(frozen=True)
class RawListing:
    """One job as the source published it. Nothing is guessed: a missing
    location, date or deadline stays None (defect D4)."""

    source: str
    source_job_id: str
    url: str  # the job's page on the source, used for attribution and Apply
    title: str
    description: str
    description_format: Literal["html", "text"]
    company: str | None = None
    location: str | None = None
    # Structured eligibility as published, e.g. ["United States"] or ["Anywhere"].
    location_restrictions: tuple[str, ...] = ()
    work_mode: str | None = None
    employment_type: str | None = None
    posted_at: datetime | None = None
    posted_at_source: PostedAtSource = "unknown"
    deadline: date | None = None
    salary: str | None = None
    tags: tuple[str, ...] = ()
    # Source-specific facts later stages may use (BDJobs overseas note, skills, ...).
    extra: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SourceResult:
    listings: list[RawListing]
    # True when every URL answered 304 Not Modified: no news, not an empty source.
    unchanged: bool = False
