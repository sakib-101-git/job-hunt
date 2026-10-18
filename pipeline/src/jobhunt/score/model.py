"""What the scorer reads: a user's profile and a gated job with its facts."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from jobhunt.config.models import ExperienceLevel
from jobhunt.extract.schema import EmploymentType, JobFacts

UserLocation = Literal["bangladesh", "remote"]


@dataclass(frozen=True)
class UserProfile:
    user_id: int
    tracks: tuple[str, ...]
    level: ExperienceLevel
    years: float | None
    locations: frozenset[UserLocation]
    job_types: frozenset[EmploymentType]
    min_score: Decimal
    skills: frozenset[str]  # skills.yaml ids, or lower-cased raw names
    include_adjacent: bool = True
    # Domains the user switched off inside a chosen track: {track id: [slugs]}.
    deselected: Mapping[str, frozenset[str]] = field(default_factory=dict)
    preferred_cities: tuple[str, ...] = ()  # district ids from locations.yaml


@dataclass(frozen=True)
class JobView:
    job_id: int
    title: str
    company: str | None
    url: str
    source: str
    location_raw: str | None
    facts: JobFacts
    primary_domain: str | None
    secondary_domains: tuple[str, ...]
    # The location gate's verdict for the job: "bangladesh" or "worldwide", its
    # quote, and any time-zone warnings.
    location_reason: str
    location_evidence: str | None
    city: str | None  # district id when the job names a Bangladeshi place
    posted_at: datetime | None
    deadline: date | None
    warnings: tuple[str, ...] = ()
    salary_text: str | None = None
    quick: bool = False  # facts came from the rules-only fallback
