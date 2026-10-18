"""The golden sets in eval/golden/ (brief §8.3).

- `jobs.jsonl`: real public postings, with hand-checked labels for the facts
  extraction must get right. Contact details are redacted before a posting is
  saved.
- `profiles.jsonl`: synthetic users, one or more per track. Never real users.
- `pairs.jsonl`: (profile, job) pairs labelled 0 (not relevant), 1 (maybe) or
  2 (apply).

Labels drafted by the coding agent are `silver`; the owner confirms or edits
them with `jobhunt eval label`, which makes them `gold`. Metrics are reported
for each separately.
"""

import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from jobhunt.config.models import ExperienceLevel
from jobhunt.extract.schema import EmploymentType, RemoteEligibility
from jobhunt.sources.models import PostedAtSource, RawListing

LabelStatus = Literal["silver", "gold"]
GOLDEN_DIR = Path("eval") / "golden"

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
_PHONE = re.compile(r"(?<![\w/:])\+?\d[\d\s().-]{7,}\d(?![\w/:])")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
MIN_PHONE_DIGITS = 10


def _phone(match: re.Match[str]) -> str:
    text = match[0]
    digits = sum(ch.isdigit() for ch in text)
    return "[phone]" if digits >= MIN_PHONE_DIGITS and not _DATE.search(text) else text


def redact(text: str) -> str:
    """Remove e-mail addresses and phone numbers (recruiters' contact details);
    dates and other short numbers stay."""
    return _PHONE.sub(_phone, _EMAIL.sub("[email]", text))


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GoldenListing(_Strict):
    """What a source returned, as stored (and redacted)."""

    source: str
    source_job_id: str
    url: str
    title: str
    company: str | None
    location: str | None
    location_restrictions: list[str] = []
    work_mode: str | None = None  # as the source wrote them
    employment_type: str | None = None
    posted_at: datetime | None
    posted_at_source: PostedAtSource
    deadline: date | None
    salary: str | None = None
    tags: list[str] = []
    extra: dict[str, str] = {}
    description: str

    def raw(self) -> RawListing:
        return RawListing(
            source=self.source,
            source_job_id=self.source_job_id,
            url=self.url,
            title=self.title,
            description=self.description,
            description_format="text",
            company=self.company,
            location=self.location,
            location_restrictions=tuple(self.location_restrictions),
            work_mode=self.work_mode,
            employment_type=self.employment_type,
            posted_at=self.posted_at,
            posted_at_source=self.posted_at_source,
            deadline=self.deadline,
            salary=self.salary,
            tags=tuple(self.tags),
            extra=dict(self.extra),
        )


class JobLabels(_Strict):
    is_cse_role: bool
    primary_domain: str | None  # a taxonomy slug; None for non-CSE roles
    secondary_domains: list[str] = []
    min_years: int | None
    remote_eligibility: RemoteEligibility
    country: str | None = Field(pattern=r"^[A-Z]{2}$")
    deadline: date | None
    skills: list[str]  # skills.yaml ids the posting asks for
    # Whether the job should reach users at all: CSE, and in Bangladesh or
    # remote for anyone, anywhere (brief R10, R11).
    eligible: bool


class GoldenJob(_Strict):
    id: str  # "<source>:<source_job_id>"
    seen_at: datetime  # when it was collected: the evaluation's "now" for it
    listing: GoldenListing
    labels: JobLabels
    label_status: LabelStatus
    notes: str = ""


class GoldenProfile(_Strict):
    id: str
    description: str
    tracks: list[str] = Field(min_length=1)
    level: ExperienceLevel
    years: float | None
    locations: list[Literal["bangladesh", "remote"]] = Field(min_length=1)
    job_types: list[EmploymentType] = Field(min_length=1)
    min_score: float = Field(ge=0, le=10)
    skills: list[str]
    preferred_cities: list[str] = []


class GoldenPair(_Strict):
    profile: str
    job: str
    label: Annotated[int, Field(ge=0, le=2)]
    label_status: LabelStatus
    notes: str = ""


class Dataset(_Strict):
    jobs: list[GoldenJob]
    profiles: list[GoldenProfile]
    pairs: list[GoldenPair]

    def job(self, job_id: str) -> GoldenJob:
        return next(j for j in self.jobs if j.id == job_id)


def _read[M: BaseModel](path: Path, model: type[M]) -> list[M]:
    # split("\n"), not splitlines(): descriptions may hold U+2028 and friends,
    # which splitlines() would treat as line ends inside a JSON string.
    lines = path.read_text(encoding="utf-8").split("\n") if path.is_file() else []
    return [model.model_validate_json(line) for line in lines if line.strip()]


def _write(path: Path, rows: list[BaseModel]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(
        json.dumps(row.model_dump(mode="json"), ensure_ascii=False, sort_keys=True) + "\n"
        for row in rows
    )
    path.write_text(text, encoding="utf-8")


def load_dataset(directory: Path) -> Dataset:
    dataset = Dataset(
        jobs=_read(directory / "jobs.jsonl", GoldenJob),
        profiles=_read(directory / "profiles.jsonl", GoldenProfile),
        pairs=_read(directory / "pairs.jsonl", GoldenPair),
    )
    check_dataset(dataset)
    return dataset


def load_profiles(directory: Path) -> list[GoldenProfile]:
    return _read(directory / "profiles.jsonl", GoldenProfile)


def save_dataset(directory: Path, dataset: Dataset) -> None:
    check_dataset(dataset)
    _write(directory / "jobs.jsonl", list(dataset.jobs))
    _write(directory / "profiles.jsonl", list(dataset.profiles))
    _write(directory / "pairs.jsonl", list(dataset.pairs))


class DatasetError(ValueError):
    """The golden set is inconsistent."""


def check_dataset(dataset: Dataset) -> None:
    jobs = [j.id for j in dataset.jobs]
    profiles = [p.id for p in dataset.profiles]
    for name, ids in (("job", jobs), ("profile", profiles)):
        if len(ids) != len(set(ids)):
            raise DatasetError(f"duplicate {name} ids")
    for job in dataset.jobs:
        if job.id != f"{job.listing.source}:{job.listing.source_job_id}":
            raise DatasetError(f"{job.id}: id must be <source>:<source_job_id>")
        if _EMAIL.search(job.listing.description):
            raise DatasetError(f"{job.id}: the description holds an e-mail address")
    pairs = {(p.profile, p.job) for p in dataset.pairs}
    if len(pairs) != len(dataset.pairs):
        raise DatasetError("duplicate (profile, job) pairs")
    unknown = {p.profile for p in dataset.pairs} - set(profiles)
    unknown |= {p.job for p in dataset.pairs} - set(jobs)
    if unknown:
        raise DatasetError(f"pairs name unknown ids: {', '.join(sorted(unknown))}")
