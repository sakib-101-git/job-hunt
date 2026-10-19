"""Score jobs for users and store the matches that pass every hard gate.

A pair is scored once (`matches` is unique per user and job). Pairs failing a
hard gate are not stored; they are counted by reason in the run summary, which
keeps the table from growing as jobs times users. Jobs whose facts came from the
rules-only fallback are matched too, marked `quick` ("AI review pending").

Each run scores only what changed: jobs not yet scored against every user (new,
or re-gated after extraction: `jobs.scored_at` is NULL) against all users, and
users whose settings or profile changed since they were last scored against all
open jobs. Nothing else is read, which keeps D1's daily row reads flat as users
and jobs grow.
"""

import json
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from jobhunt.config import Config
from jobhunt.db import Database, Row, Statement, timestamp
from jobhunt.extract.rules import PROVIDER as RULES
from jobhunt.extract.schema import JobFacts
from jobhunt.gates.base import GateResult
from jobhunt.jobs.rows import listing_from_row
from jobhunt.normalize.jobs import JobNormalizer
from jobhunt.score.model import JobView, UserProfile
from jobhunt.score.render import Renderer
from jobhunt.score.scorer import Component, Scorer, ScoreResult

_ELIGIBLE_JOBS = """
SELECT j.*, f.facts_json, f.provider AS facts_provider
FROM jobs j JOIN job_facts f ON f.job_id = j.id
WHERE j.status = 'open' AND json_extract(j.gates_json, '$.needs_facts') = '[]'
"""

_PROFILES = """
SELECT u.id, p.*, COALESCE(pr.skills_json, '[]') AS skills_json, pr.years_experience AS cv_years,
       (p.scored_at IS NULL OR p.scored_at <= p.updated_at
        OR (pr.updated_at IS NOT NULL AND p.scored_at <= pr.updated_at)) AS needs_scoring
FROM users u
JOIN user_prefs p ON p.user_id = u.id
LEFT JOIN user_profiles pr ON pr.user_id = u.id
WHERE u.status IN ('active', 'paused')
"""
# D1 binds at most 100 parameters per statement.
_ID_CHUNK = 90


@dataclass(frozen=True)
class NewMatch:
    match_id: int
    user_id: int
    job_id: int
    score: Decimal


async def load_jobs(
    db: Database,
    config: Config,
    job_ids: Sequence[int] | None = None,
    *,
    unscored_only: bool = False,
) -> list[JobView]:
    """Open jobs with facts that passed the job gates: all of them, those in
    `job_ids`, or those not yet scored against every user."""
    sql = _ELIGIBLE_JOBS + (" AND j.scored_at IS NULL" if unscored_only else "")
    if job_ids is None:
        rows = await db.query(sql)
    else:
        rows = [
            row
            for chunk in _chunks(sorted(set(job_ids)))
            for row in await db.query(
                f"{sql} AND j.id IN ({', '.join('?' for _ in chunk)})", tuple(chunk)
            )
        ]
    if not rows:
        return []
    domains: dict[int, list[Row]] = {}
    for chunk in _chunks([int(str(row["id"])) for row in rows]):
        marks = ", ".join("?" for _ in chunk)
        for row in await db.query(
            f"SELECT * FROM job_domains WHERE job_id IN ({marks})",  # noqa: S608
            tuple(chunk),
        ):
            domains.setdefault(int(str(row["job_id"])), []).append(row)
    normalizer = JobNormalizer(config)
    return [_job_view(row, domains.get(int(str(row["id"])), []), normalizer) for row in rows]


def _chunks(ids: Sequence[int]) -> list[Sequence[int]]:
    return [ids[i : i + _ID_CHUNK] for i in range(0, len(ids), _ID_CHUNK)]


def job_domains(
    domain_rows: Sequence[Row], facts: JobFacts, provider: str | None
) -> tuple[str | None, list[str]]:
    """The job's primary domain and the taxonomy's secondary domains. A
    taxonomy match decides the primary domain. A title class (method "llm") was
    judged from the title alone, so a model that read the whole posting
    overrides it ("Associate Manager, Branch Governance, Distribution Network"
    is not networking). The model's own secondary domains stay in the facts
    and are not used for matching: they are guesses."""
    primary_row = next((d for d in domain_rows if d["is_primary"]), None)
    primary = str(primary_row["domain_slug"]) if primary_row else None
    secondary = [str(d["domain_slug"]) for d in domain_rows if not d["is_primary"]]
    by_model = provider not in (None, RULES) and facts.primary_domain is not None
    if primary_row is not None and primary_row["method"] == "llm" and by_model:
        primary = facts.primary_domain
    primary = primary or facts.primary_domain
    return primary, [s for s in dict.fromkeys(secondary) if s != primary]


def _job_view(row: Row, domain_rows: list[Row], normalizer: JobNormalizer) -> JobView:
    listing = listing_from_row(row)
    facts = JobFacts.model_validate_json(str(row["facts_json"]))
    provider = str(row["facts_provider"]) if row["facts_provider"] else None
    primary, secondary = job_domains(domain_rows, facts, provider)
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
        primary_domain=primary,
        secondary_domains=tuple(secondary)[:3],
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
    return [profile for profile, _ in await _load_profiles(db, user_ids)]


async def _load_profiles(
    db: Database, user_ids: Sequence[int] | None
) -> list[tuple[UserProfile, bool]]:
    """Profiles, each with whether it needs scoring against every open job."""
    rows = await db.query(_PROFILES)
    wanted = set(user_ids) if user_ids is not None else None
    return [
        (_profile(row), bool(row["needs_scoring"]))
        for row in rows
        if wanted is None or int(str(row["id"])) in wanted
    ]


def _profile(row: Row) -> UserProfile:
    overrides = json.loads(str(row["domain_overrides_json"] or "{}"))
    return UserProfile(
        user_id=int(str(row["id"])),
        tracks=tuple(json.loads(str(row["tracks_json"]))),
        level=row["experience_level"],  # type: ignore[arg-type]
        years=_years(row["years_experience"], row["cv_years"]),
        locations=frozenset(json.loads(str(row["locations_json"]))),
        job_types=frozenset(json.loads(str(row["job_types_json"]))),
        min_score=Decimal(str(row["min_score"])),
        skills=frozenset(json.loads(str(row["skills_json"]))),
        include_adjacent=bool(row["include_adjacent"]),
        deselected={track: frozenset(slugs) for track, slugs in overrides.items()},
        preferred_cities=tuple(json.loads(str(row["preferred_cities_json"] or "[]"))),
    )


def _years(*values: object) -> float | None:
    """The first known value: years set in prefs, then years read from a CV."""
    return next((float(str(v)) for v in values if v is not None), None)


async def match(
    db: Database,
    config: Config,
    now: datetime,
    user_ids: Sequence[int] | None = None,
) -> tuple[list[NewMatch], Counter[str]]:
    """Score the pairs that changed (see the module docstring); store the ones
    that pass. With `user_ids`, only those users are scored (a backfill), and
    new jobs stay marked unscored for everyone else."""
    stamp = timestamp(now)
    # A /pause with an end date ends here: the user is active again.
    await db.execute(
        "UPDATE users SET status = 'active', paused_until = NULL, updated_at = ?1"
        " WHERE status = 'paused' AND paused_until IS NOT NULL AND paused_until <= ?1",
        (stamp,),
    )
    profiles = await _load_profiles(db, user_ids)
    users = [profile for profile, _ in profiles]
    changed = [profile for profile, needs in profiles if needs]
    new_jobs = await load_jobs(db, config, unscored_only=True)
    all_jobs = await load_jobs(db, config) if changed else []
    counts: Counter[str] = Counter()
    if not users or not (new_jobs or all_jobs):
        await _mark_scored(db, [] if user_ids else new_jobs, changed, stamp)
        return [], counts

    pairs: dict[tuple[int, int], tuple[JobView, UserProfile]] = {}
    for job in new_jobs:
        for user in users:
            pairs[(user.user_id, job.job_id)] = (job, user)
    for user in changed:
        for job in all_jobs:
            pairs[(user.user_id, job.job_id)] = (job, user)
    existing = await _existing_pairs(db, [j.job_id for j in new_jobs], [u.user_id for u in changed])

    scorer = Scorer(config)
    renderer = Renderer.from_config(config)
    new: list[NewMatch] = []
    for key, (job, user) in pairs.items():
        if key in existing:
            continue
        result = scorer.score(job, user)
        if not result.passed:
            failed = result.failed_gate
            counts[f"not matched: {failed.gate}:{failed.reason}" if failed else "not matched"] += 1
            continue
        rows = await db.query(
            """INSERT INTO matches (user_id, job_id, score, breakdown_json, explanation_json,
                                    gate_results_json, scorer_version, why_html, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (user_id, job_id) DO NOTHING
               RETURNING id""",
            (
                user.user_id,
                job.job_id,
                float(result.score),
                json.dumps(result.breakdown() | {"quick": job.quick}),
                json.dumps(result.explanation(), ensure_ascii=False),
                json.dumps(result.gate_results(), ensure_ascii=False),
                result.scorer_version,
                renderer.why(job, result, user),
                stamp,
            ),
        )
        if not rows:  # an overlapping run stored this pair first
            continue
        new.append(NewMatch(int(str(rows[0]["id"])), user.user_id, job.job_id, result.score))
        counts[f"matched ({result.label})"] += 1
    await _mark_scored(db, [] if user_ids else new_jobs, changed, stamp)
    return new, counts


async def _existing_pairs(
    db: Database, job_ids: Sequence[int], user_ids: Sequence[int]
) -> set[tuple[int, int]]:
    found: set[tuple[int, int]] = set()
    for column, ids in (("job_id", job_ids), ("user_id", user_ids)):
        for chunk in _chunks(sorted(set(ids))):
            marks = ", ".join("?" for _ in chunk)
            for row in await db.query(
                f"SELECT user_id, job_id FROM matches WHERE {column} IN ({marks})",  # noqa: S608
                tuple(chunk),
            ):
                found.add((int(str(row["user_id"])), int(str(row["job_id"]))))
    return found


async def _mark_scored(
    db: Database, jobs: Sequence[JobView], users: Sequence[UserProfile], stamp: str | None
) -> None:
    statements = [
        Statement(
            f"UPDATE jobs SET scored_at = ? WHERE id IN ({', '.join('?' for _ in chunk)})",  # noqa: S608
            (stamp, *chunk),
        )
        for chunk in _chunks([job.job_id for job in jobs])
    ] + [
        Statement(
            "UPDATE user_prefs SET scored_at = ?"  # noqa: S608
            f" WHERE user_id IN ({', '.join('?' for _ in chunk)})",
            (stamp, *chunk),
        )
        for chunk in _chunks([user.user_id for user in users])
    ]
    if statements:
        await db.batch(statements)


def stored_result(row: Row, config: Config) -> ScoreResult:
    """The score as stored on a match: messages show these numbers, never a rerun."""
    breakdown = json.loads(str(row["breakdown_json"]))
    explanation: dict[str, dict[str, Any]] = json.loads(str(row["explanation_json"]))
    gates = json.loads(str(row["gate_results_json"]))
    components = tuple(
        Component(
            name,
            Decimal(data["points"]),
            Decimal(data["max"]),
            data["rule"],
            {k: v for k, v in data.items() if k not in {"points", "max", "rule"}},
        )
        for name, data in explanation.items()
    )
    score = Decimal(breakdown["score"])
    label = next(lbl.key for lbl in config.scoring.labels if score >= Decimal(str(lbl.min)))
    return ScoreResult(
        passed=True,
        gates=tuple(
            GateResult(name, g["status"], g["reason"], g.get("evidence"))
            for name, g in gates.items()
        ),
        components=components,
        score=score,
        label=label,
        scorer_version=str(row["scorer_version"]),
    )
