"""Score jobs for users and store the matches that pass every hard gate.

A pair is scored once (`matches` is unique per user and job). Pairs failing a
hard gate are not stored; they are counted by reason in the run summary, which
keeps the table from growing as jobs times users. Jobs whose facts came from the
rules-only fallback are matched too, marked `quick` ("AI review pending").
"""

import json
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from jobhunt.config import Config
from jobhunt.db import Database, Row, timestamp
from jobhunt.extract.rules import PROVIDER as RULES
from jobhunt.extract.schema import JobFacts
from jobhunt.jobs.rows import listing_from_row
from jobhunt.normalize.jobs import JobNormalizer
from jobhunt.score.model import JobView, UserProfile
from jobhunt.score.scorer import Scorer

_ELIGIBLE_JOBS = """
SELECT j.*, f.facts_json, f.provider AS facts_provider
FROM jobs j JOIN job_facts f ON f.job_id = j.id
WHERE j.status = 'open' AND json_extract(j.gates_json, '$.needs_facts') = '[]'
"""

_PROFILES = """
SELECT u.id, p.*, COALESCE(pr.skills_json, '[]') AS skills_json
FROM users u
JOIN user_prefs p ON p.user_id = u.id
LEFT JOIN user_profiles pr ON pr.user_id = u.id
WHERE u.status IN ('active', 'paused')
"""


@dataclass(frozen=True)
class NewMatch:
    match_id: int
    user_id: int
    job_id: int
    score: Decimal


async def load_jobs(
    db: Database, config: Config, job_ids: Sequence[int] | None = None
) -> list[JobView]:
    rows = await db.query(_ELIGIBLE_JOBS)
    wanted = set(job_ids) if job_ids is not None else None
    rows = [r for r in rows if wanted is None or int(str(r["id"])) in wanted]
    if not rows:
        return []
    domains: dict[int, list[Row]] = {}
    for row in await db.query("SELECT * FROM job_domains"):
        domains.setdefault(int(str(row["job_id"])), []).append(row)
    normalizer = JobNormalizer(config)
    return [_job_view(row, domains.get(int(str(row["id"])), []), normalizer) for row in rows]


def _job_view(row: Row, domain_rows: list[Row], normalizer: JobNormalizer) -> JobView:
    listing = listing_from_row(row)
    facts = JobFacts.model_validate_json(str(row["facts_json"]))
    primary = next((str(d["domain_slug"]) for d in domain_rows if d["is_primary"]), None)
    secondary = [str(d["domain_slug"]) for d in domain_rows if not d["is_primary"]]
    secondary += [s for s in facts.secondary_domains if s not in secondary]
    gates = json.loads(str(row["gates_json"]))
    location = gates.get("location", {})
    return JobView(
        job_id=int(str(row["id"])),
        title=listing.title,
        company=listing.company,
        url=listing.url,
        source=listing.source,
        location_raw=listing.location,
        facts=facts,
        primary_domain=primary or facts.primary_domain,
        secondary_domains=tuple(dict.fromkeys(secondary))[:3],
        location_reason=str(location.get("reason", "")),
        location_evidence=location.get("evidence"),
        city=normalizer.city_key(listing) or None,
        posted_at=listing.posted_at,
        deadline=listing.deadline or facts.application_deadline,
        warnings=tuple(location.get("warnings", [])),
        salary_text=listing.salary,
        quick=row["facts_provider"] == RULES,
    )


async def load_profiles(db: Database, user_ids: Sequence[int] | None = None) -> list[UserProfile]:
    rows = await db.query(_PROFILES)
    wanted = set(user_ids) if user_ids is not None else None
    return [_profile(row) for row in rows if wanted is None or int(str(row["id"])) in wanted]


def _profile(row: Row) -> UserProfile:
    overrides = json.loads(str(row["domain_overrides_json"] or "{}"))
    return UserProfile(
        user_id=int(str(row["id"])),
        tracks=tuple(json.loads(str(row["tracks_json"]))),
        level=row["experience_level"],  # type: ignore[arg-type]
        years=float(str(row["years_experience"])) if row["years_experience"] is not None else None,
        locations=frozenset(json.loads(str(row["locations_json"]))),
        job_types=frozenset(json.loads(str(row["job_types_json"]))),
        min_score=Decimal(str(row["min_score"])),
        skills=frozenset(json.loads(str(row["skills_json"]))),
        include_adjacent=bool(row["include_adjacent"]),
        deselected={track: frozenset(slugs) for track, slugs in overrides.items()},
        preferred_cities=tuple(json.loads(str(row["preferred_cities_json"] or "[]"))),
    )


async def match(
    db: Database,
    config: Config,
    now: datetime,
    job_ids: Sequence[int] | None = None,
    user_ids: Sequence[int] | None = None,
) -> tuple[list[NewMatch], Counter[str]]:
    """Score every (user, job) pair not scored yet; store the ones that pass."""
    jobs = await load_jobs(db, config, job_ids)
    users = await load_profiles(db, user_ids)
    existing = {
        (int(str(r["user_id"])), int(str(r["job_id"])))
        for r in await db.query("SELECT user_id, job_id FROM matches")
    }
    scorer = Scorer(config)
    counts: Counter[str] = Counter()
    new: list[NewMatch] = []
    for job in jobs:
        for user in users:
            if (user.user_id, job.job_id) in existing:
                continue
            result = scorer.score(job, user)
            if not result.passed:
                failed = result.failed_gate
                counts[
                    f"not matched: {failed.gate}:{failed.reason}" if failed else "not matched"
                ] += 1
                continue
            rows = await db.query(
                """INSERT INTO matches (user_id, job_id, score, breakdown_json, explanation_json,
                                        gate_results_json, scorer_version, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?) RETURNING id""",
                (
                    user.user_id,
                    job.job_id,
                    float(result.score),
                    json.dumps(result.breakdown() | {"quick": job.quick}),
                    json.dumps(result.explanation(), ensure_ascii=False),
                    json.dumps(result.gate_results(), ensure_ascii=False),
                    result.scorer_version,
                    timestamp(now),
                ),
            )
            new.append(NewMatch(int(str(rows[0]["id"])), user.user_id, job.job_id, result.score))
            counts[f"matched ({result.label})"] += 1
    return new, counts
