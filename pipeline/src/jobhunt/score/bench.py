"""`jobhunt bench`: time the scorer on synthetic users and jobs (brief §13: 1,000
users x 300 jobs in under 5 s). Matching is pure Python over in-memory facts,
so this is the cost of one pipeline run's matching stage at that scale."""

import random
import time
from dataclasses import dataclass
from decimal import Decimal

from jobhunt.config import Config
from jobhunt.config.models import EXPERIENCE_LEVELS
from jobhunt.extract.schema import JobFacts
from jobhunt.score.model import JobView, UserProfile
from jobhunt.score.scorer import Scorer

SENIORITY = ("entry", "junior", "mid", "senior", "unknown")


@dataclass(frozen=True)
class BenchResult:
    pairs: int
    passed: int
    seconds: float


def synthetic(
    config: Config, users: int, jobs: int, seed: int
) -> tuple[list[JobView], list[UserProfile]]:
    rnd = random.Random(seed)  # noqa: S311 (synthetic test data, not secrets)
    slugs = sorted(config.taxonomy.selectable_slugs)
    titles = config.taxonomy.titles_by_domain()
    skills = [s.id for s in config.skills.all_skills]
    tracks = [t.id for t in config.tracks.tracks]
    views = []
    for job_id in range(jobs):
        domain = rnd.choice(slugs)
        facts = JobFacts.model_validate(
            {
                "is_cse_role": True, "is_cse_role_evidence": None, "primary_domain": domain,
                "secondary_domains": [], "normalized_title": "", "seniority": rnd.choice(SENIORITY),
                "seniority_evidence": None, "min_years": rnd.choice([None, 0, 1, 2, 3, 5]),
                "max_years": None, "years_evidence": None,
                "education": {"level": "bachelor", "fields": ["cse"], "required": True},
                "education_evidence": None, "required_skills": rnd.sample(skills, 5),
                "preferred_skills": rnd.sample(skills, 3), "employment_type": "full_time",
                "work_mode": rnd.choice(["onsite", "remote"]), "country": "BD", "city": None,
                "remote_eligibility": "not_remote", "remote_eligibility_evidence": None,
                "timezone_requirement": None, "salary": None, "application_deadline": None,
                "deadline_evidence": None, "posted_date_in_text": None,
                "language_requirements": [], "red_flags": [], "summary": "",
            }
        )  # fmt: skip
        views.append(
            JobView(
                job_id=job_id,
                title=rnd.choice(titles[domain]),
                company=None,
                url="",
                source="bench",
                location_raw=None,
                facts=facts,
                primary_domain=domain,
                secondary_domains=(),
                location_reason="bangladesh",
                location_evidence=None,
                city=None,
                posted_at=None,
                deadline=None,
            )
        )
    profiles = [
        UserProfile(
            user_id=user_id,
            tracks=tuple(rnd.sample(tracks, 2)),
            level=rnd.choice(EXPERIENCE_LEVELS),
            years=None,
            locations=frozenset({"bangladesh", "remote"}),
            job_types=frozenset({"full_time"}),
            min_score=Decimal(6),
            skills=frozenset(rnd.sample(skills, 8)),
        )
        for user_id in range(users)
    ]
    return views, profiles


def bench(config: Config, users: int, jobs: int, seed: int = 7) -> BenchResult:
    views, profiles = synthetic(config, users, jobs, seed)
    scorer = Scorer(config)
    started = time.perf_counter()
    passed = sum(scorer.score(job, user).passed for job in views for user in profiles)
    return BenchResult(len(views) * len(profiles), passed, time.perf_counter() - started)
