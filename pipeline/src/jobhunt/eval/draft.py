"""`jobhunt eval draft`: pick golden-set candidates from a database the pipeline
collected into, redact them, and draft silver labels from the rules-only
extractor and the gates. The drafts are a starting point: every label is then
checked by hand (by the coding agent for silver, by the owner for gold).

The pick is spread over sources and gate outcomes (passed, not a CSE role,
overseas, region-restricted, unspecified remote, ...), so the set covers the
cases the brief names, and is deterministic for a given database.
"""

import hashlib
from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime

from jobhunt.config import Config
from jobhunt.db import Database, Row
from jobhunt.eval.dataset import GoldenJob, GoldenListing, JobLabels, redact
from jobhunt.extract.grounding import Grounder, experience_years
from jobhunt.gates.location import LocationGate
from jobhunt.jobs.rows import listing_from_row
from jobhunt.normalize.jobs import JobNormalizer
from jobhunt.taxonomy.matcher import TitleMatcher

OPEN_SHARE = 0.6

_REMOTE = {
    "worldwide": "worldwide",
    "region_with_bangladesh": "bangladesh_included",
    "region_restricted": "region_restricted",
    "unspecified_remote": "unspecified",
}


def _order(row: Row) -> str:
    return hashlib.sha256(f"{row['source']!s}:{row['source_job_id']!s}".encode()).hexdigest()


def _round_robin(buckets: dict[str, list[Row]], total: int, weight: dict[str, int]) -> list[Row]:
    chosen: list[Row] = []
    while len(chosen) < total and any(buckets.values()):
        for key in sorted(buckets):
            for _ in range(weight.get(key, 1)):
                if buckets[key] and len(chosen) < total:
                    chosen.append(buckets[key].pop(0))
    return chosen


def pick(
    rows: Sequence[Row], total: int, bd_sources: Sequence[str], open_share: float
) -> list[Row]:
    """`open_share` of the set from jobs that passed the job gates (round-robin
    by source, Bangladesh boards twice per round: the primary market), the rest
    round-robin over (source, filter reason). Stable for a given database."""
    passed: dict[str, list[Row]] = defaultdict(list)
    filtered: dict[str, list[Row]] = defaultdict(list)
    for row in sorted(rows, key=_order):
        reason = str(row["filter_reason"] or "")
        # Freshness has its own boundary tests; a stale job teaches the golden
        # set nothing about extraction or scoring.
        if not row["jd_text"] or reason.startswith("freshness:"):
            continue
        if row["status"] == "open":
            passed[str(row["source"])].append(row)
        else:
            filtered[f"{row['source']!s}|{reason}"].append(row)
    weight = dict.fromkeys(bd_sources, 2)
    chosen = _round_robin(passed, round(total * open_share), weight)
    return chosen + _round_robin(filtered, total - len(chosen), {})


def draft_job(row: Row, config: Config, seen_at: datetime) -> GoldenJob:
    listing = listing_from_row(row)
    text = redact(listing.description)
    matcher = TitleMatcher(config)
    grounder = Grounder(config.skills, config.llm.extraction.min_evidence_score)
    location = LocationGate(config)(listing)
    match = matcher.match(listing.title)
    years = experience_years(text)
    is_cse = match.primary_domain is not None and match.method != "none"
    in_bd = location.status == "pass" and location.reason == "bangladesh"
    remote = _REMOTE.get(location.reason, "not_remote")
    country = "BD" if in_bd or JobNormalizer(config).city_key(listing) else None
    labels = JobLabels(
        is_cse_role=is_cse,
        primary_domain=match.primary_domain if is_cse else None,
        secondary_domains=list(match.secondary_domains) if is_cse else [],
        min_years=years[0] if years else None,
        remote_eligibility=remote,  # type: ignore[arg-type]
        country=country,
        deadline=listing.deadline,
        skills=grounder.skills_in(text, include_ambiguous=False),
        eligible=is_cse and location.status == "pass",
    )
    golden = GoldenListing(
        source=listing.source,
        source_job_id=listing.source_job_id,
        url=listing.url,
        title=listing.title,
        company=listing.company,
        location=listing.location,
        location_restrictions=list(listing.location_restrictions),
        work_mode=listing.work_mode,
        employment_type=listing.employment_type,
        posted_at=listing.posted_at,
        posted_at_source=listing.posted_at_source,
        deadline=listing.deadline,
        salary=listing.salary,
        tags=list(listing.tags),
        # All-digit values are structured data (Himalayas' expiry timestamp), not
        # contact details.
        extra={k: v if v.isdigit() else redact(v) for k, v in listing.extra.items()},
        description=text,
    )
    return GoldenJob(
        id=f"{listing.source}:{listing.source_job_id}",
        seen_at=seen_at,
        listing=golden,
        labels=labels,
        label_status="silver",
        notes="drafted from rules; check every label",
    )


async def draft_jobs(db: Database, config: Config, total: int) -> list[GoldenJob]:
    rows = await db.query("SELECT * FROM jobs ORDER BY id")
    chosen = pick(rows, total, config.app.location_policy.bangladesh_boards, OPEN_SHARE)
    return [
        draft_job(row, config, datetime.fromisoformat(str(row["first_seen_at"]))) for row in chosen
    ]
