"""Run the golden set through the real pipeline and measure it (brief §8.3).

The jobs go through the same code as production, on an in-memory SQLite
database: store, gates, enrichment (title classes and facts, from recorded or
live LLM answers), gates again, then the scorer for every profile. Nothing here
re-implements a stage; the only independent logic is the hard-gate oracle,
which applies the product's hard rules to the hand-checked labels, so a leak
caused by a wrong extraction shows up as a violation.

The evaluation's clock is the latest `seen_at` in the set: collect a golden set
in one sitting, so its jobs are fresh at that moment.
"""

import dataclasses
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

import httpx

from jobhunt.config import Config
from jobhunt.config.models import ChainEntry, Llm
from jobhunt.db import Row, SqliteBackend, apply_migrations
from jobhunt.eval.dataset import Dataset, GoldenJob, GoldenProfile
from jobhunt.extract.llm import LlmClient
from jobhunt.extract.rules import PROVIDER as RULES
from jobhunt.extract.schema import JobFacts
from jobhunt.jobs.enrich import enrich
from jobhunt.jobs.gating import GateItem, JobGates, apply_gates
from jobhunt.jobs.store import store_jobs
from jobhunt.normalize.jobs import JobNormalizer
from jobhunt.normalize.titles import TitleNormalizer
from jobhunt.score.matching import load_jobs
from jobhunt.score.model import JobView, UserProfile
from jobhunt.score.scorer import Scorer

FIELDS = ("is_cse_role", "primary_domain", "min_years", "remote_eligibility", "country", "deadline")


@dataclass(frozen=True)
class JobResult:
    job: GoldenJob
    status: str  # open | filtered
    filter_reason: str | None
    needs_facts: tuple[str, ...]
    facts: JobFacts | None
    facts_provider: str | None
    grounding_failures: tuple[str, ...]
    system_domain: str | None

    @property
    def by_llm(self) -> bool:
        return self.facts_provider not in (None, RULES)

    @property
    def eligible(self) -> bool:
        """The system would show this job to someone."""
        return self.status == "open" and not self.needs_facts


@dataclass(frozen=True)
class PairResult:
    profile: GoldenProfile
    job: GoldenJob
    score: Decimal | None  # None: not matched (a job or user gate failed)
    reason: str  # why not matched, or the score label
    notified: bool
    label: int | None = None  # the pair's label, when the pair is labelled
    label_status: str | None = None

    @property
    def score_or_zero(self) -> Decimal:
        return self.score if self.score is not None else Decimal(0)


@dataclass
class EvalRun:
    jobs: list[JobResult]
    pairs: list[PairResult]  # every profile x every job
    llm_hits: int = 0
    llm_misses: int = 0
    as_of: datetime | None = None
    notes: list[str] = field(default_factory=list)


def _unlimited(llm: Llm) -> Llm:
    """Recorded answers cost nothing: drop the per-minute and daily limits."""
    chain = [
        ChainEntry.model_validate(entry.model_dump() | {"rpm": None, "tpm": None,
                                                         "rpd": None, "tpd": None})
        for entry in llm.chain
    ]  # fmt: skip
    return llm.model_copy(update={"chain": chain})


def profile_user(index: int, profile: GoldenProfile) -> UserProfile:
    return UserProfile(
        user_id=index,
        tracks=tuple(profile.tracks),
        level=profile.level,
        years=profile.years,
        locations=frozenset(profile.locations),
        job_types=frozenset(profile.job_types),
        min_score=Decimal(str(profile.min_score)),
        skills=frozenset(profile.skills),
        preferred_cities=tuple(profile.preferred_cities),
    )


async def run_eval(
    config: Config,
    dataset: Dataset,
    transport: httpx.AsyncBaseTransport,
    environ: Mapping[str, str],
    *,
    replay: bool,
) -> EvalRun:
    """Every golden job through the pipeline, every profile through the scorer."""
    if not dataset.jobs:
        return EvalRun([], [])
    as_of = max(job.seen_at for job in dataset.jobs)
    if replay:
        config = dataclasses.replace(config, llm=_unlimited(config.llm))
    db = SqliteBackend(":memory:")
    try:
        await apply_migrations(db, config.root / "migrations")
        ids = await _store(db, config, dataset.jobs)
        client = LlmClient(config.llm, environ, httpx.AsyncClient(transport=transport))
        try:
            await enrich(db, config, client, as_of, limit=len(dataset.jobs))
        finally:
            await client.aclose()
        job_results = await _job_results(db, dataset.jobs, ids)
        views = {view.job_id: view for view in await load_jobs(db, config)}
    finally:
        await db.aclose()
    pairs = _score_all(config, dataset, ids, views, job_results)
    return EvalRun(job_results, pairs, as_of=as_of)


async def _store(db: SqliteBackend, config: Config, jobs: Sequence[GoldenJob]) -> dict[str, int]:
    normalizer = JobNormalizer(config)
    gates = JobGates(config)
    ids: dict[str, int] = {}
    for job in jobs:
        listing = job.listing.raw()
        stored = await store_jobs(db, [normalizer.record(listing)], job.seen_at, 1)
        ids[job.id] = stored[0].id
        item = GateItem(stored[0].id, listing, job.seen_at, bootstrap=False)
        await apply_gates(db, gates, [item], job.seen_at, 1)
    return ids


async def _job_results(
    db: SqliteBackend, jobs: Sequence[GoldenJob], ids: Mapping[str, int]
) -> list[JobResult]:
    rows = {
        int(str(row["id"])): row
        for row in await db.query(
            "SELECT j.id, j.status, j.filter_reason, j.gates_json, f.facts_json, f.provider,"
            " f.grounding_failures_json, d.domain_slug"
            " FROM jobs j LEFT JOIN job_facts f ON f.job_id = j.id"
            " LEFT JOIN job_domains d ON d.job_id = j.id AND d.is_primary = 1"
        )
    }
    results = []
    for job in jobs:
        row: Row = rows[ids[job.id]]
        gates: dict[str, Any] = json.loads(str(row["gates_json"] or "{}"))
        facts = JobFacts.model_validate_json(str(row["facts_json"])) if row["facts_json"] else None
        domain = str(row["domain_slug"]) if row["domain_slug"] else None
        if domain is None and facts is not None:
            domain = facts.primary_domain
        results.append(
            JobResult(
                job=job,
                status=str(row["status"]),
                filter_reason=str(row["filter_reason"]) if row["filter_reason"] else None,
                needs_facts=tuple(gates.get("needs_facts", [])),
                facts=facts,
                facts_provider=str(row["provider"]) if row["provider"] else None,
                grounding_failures=tuple(json.loads(str(row["grounding_failures_json"] or "[]"))),
                system_domain=domain,
            )
        )
    return results


def _score_all(
    config: Config,
    dataset: Dataset,
    ids: Mapping[str, int],
    views: Mapping[int, JobView],
    job_results: Sequence[JobResult],
) -> list[PairResult]:
    scorer = Scorer(config)
    labels = {(p.profile, p.job): p for p in dataset.pairs}
    by_job = {r.job.id: r for r in job_results}
    pairs = []
    for index, profile in enumerate(dataset.profiles, start=1):
        user = profile_user(index, profile)
        for job in dataset.jobs:
            labelled = labels.get((profile.id, job.id))
            label = labelled.label if labelled else None
            status = labelled.label_status if labelled else None
            view = views.get(ids[job.id])
            result = by_job[job.id]
            if view is None:
                reason = result.filter_reason or (
                    f"needs facts: {', '.join(result.needs_facts)}" if result.needs_facts else "-"
                )
                pairs.append(PairResult(profile, job, None, f"job: {reason}", False, label, status))
                continue
            scored = scorer.score(view, user)
            if not scored.passed:
                failed = scored.failed_gate
                reason = f"user: {failed.gate}:{failed.reason}" if failed else "user gate"
                pairs.append(PairResult(profile, job, None, reason, False, label, status))
                continue
            notified = scored.score >= user.min_score
            pairs.append(
                PairResult(profile, job, scored.score, scored.label, notified, label, status)
            )
    return pairs


# --- the hard-gate oracle --------------------------------------------------------


class Oracle:
    """The product's hard rules, applied to the labels instead of extracted facts."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._tracks = {t.id: t for t in config.tracks.tracks}
        titles = TitleNormalizer(config.title_rules, config.locations.place_names())
        self._titles = titles
        self._include = {
            t.id: {titles.normalize(x).key for x in t.include_titles} for t in config.tracks.tracks
        }
        self._gap = config.app.seniority_gate.max_years_gap
        self._default_years = config.app.seniority_gate.default_years

    def violations(self, profile: GoldenProfile, job: GoldenJob) -> list[str]:
        """The hard rules a notification of `job` to `profile` would break."""
        labels = job.labels
        broken = []
        if not labels.is_cse_role:
            broken.append("cse")
        if not labels.eligible:
            broken.append("job_location")
        in_bd = labels.country == "BD"
        remote_ok = labels.remote_eligibility in ("worldwide", "bangladesh_included")
        locations = set(profile.locations)
        if in_bd and not remote_ok and "bangladesh" not in locations:
            broken.append("user_location")
        if not in_bd and remote_ok and "remote" not in locations:
            broken.append("user_location")
        if labels.is_cse_role and not self._in_tracks(profile, job):
            broken.append("tracks")
        years = profile.years if profile.years is not None else self._default_years[profile.level]
        if labels.min_years is not None and labels.min_years - years > self._gap[profile.level]:
            broken.append("seniority")
        return broken

    def _in_tracks(self, profile: GoldenProfile, job: GoldenJob) -> bool:
        domains = {job.labels.primary_domain, *job.labels.secondary_domains}
        title = self._titles.normalize(job.listing.title).key
        for track_id in profile.tracks:
            track = self._tracks[track_id]
            allowed = set(track.domains.primary) | set(track.domains.adjacent)
            if domains & allowed or title in self._include[track_id]:
                return True
        return False


# --- metrics ---------------------------------------------------------------------


def _field_value(result: JobResult, name: str) -> object:
    facts = result.facts
    if name == "is_cse_role" and facts is None:  # decided by the title, before extraction
        return not (result.filter_reason or "").startswith("cse:")
    if name == "primary_domain":
        return result.system_domain
    if facts is None:
        return None
    if name == "deadline":
        return result.job.listing.deadline or facts.application_deadline
    return getattr(facts, name)


def _label_value(job: GoldenJob, name: str) -> object:
    return getattr(job.labels, name)


def _judged(result: JobResult, name: str) -> bool:
    """`is_cse_role` is judged on every job; the domain on CSE jobs (others have
    no domain label); the other fields on jobs that reached extraction."""
    if name == "is_cse_role":
        return True
    if name == "primary_domain":
        return result.job.labels.is_cse_role
    return result.facts is not None


def _correct(result: JobResult, name: str) -> bool:
    """The system's value matches the label. A job can belong to several
    domains, so any labelled domain counts for the system's primary one."""
    value = _field_value(result, name)
    if name == "primary_domain":
        labels = result.job.labels
        return value is not None and value in {labels.primary_domain, *labels.secondary_domains}
    return value == _label_value(result.job, name)


def field_accuracy(results: Sequence[JobResult]) -> dict[str, float | None]:
    """Share of judged jobs whose system value matches the label, per field."""
    accuracy: dict[str, float | None] = {}
    for name in FIELDS:
        judged = [r for r in results if _judged(r, name)]
        hits = sum(1 for r in judged if _correct(r, name))
        accuracy[name] = hits / len(judged) if judged else None
    return accuracy


def skills_scores(results: Sequence[JobResult]) -> dict[str, float | None]:
    """Micro-averaged precision, recall and F1 of the skills the posting asks for."""
    tp = fp = fn = 0
    for r in results:
        if r.facts is None:
            continue
        found = {s.casefold() for s in (r.facts.required_skills + r.facts.preferred_skills)}
        gold = {s.casefold() for s in r.job.labels.skills}
        tp += len(found & gold)
        fp += len(found - gold)
        fn += len(gold - found)
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision is not None and recall is not None and precision + recall
        else None
    )
    return {"precision": precision, "recall": recall, "f1": f1}


def grounding_failure_rate(results: Sequence[JobResult]) -> float | None:
    by_llm = [r for r in results if r.by_llm]
    if not by_llm:
        return None
    return sum(1 for r in by_llm if r.grounding_failures) / len(by_llm)


def gate_confusion(results: Sequence[JobResult]) -> Counter[str]:
    """System eligibility against the `eligible` label."""
    counts: Counter[str] = Counter()
    for r in results:
        system = "shown" if r.eligible else "held back"
        truth = "eligible" if r.job.labels.eligible else "ineligible"
        counts[f"{truth}, {system}"] += 1
    return counts


def _ranks(values: Sequence[float]) -> list[float]:
    """1-based ranks, ties sharing their average rank."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def spearman(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """Spearman's rank correlation (Pearson on average ranks, so ties are fine)."""
    if len(xs) != len(ys) or len(xs) < 3:
        return None
    rx, ry = _ranks(xs), _ranks(ys)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=True))
    vx = sum((a - mx) ** 2 for a in rx)
    vy = sum((b - my) ** 2 for b in ry)
    return cov / (vx * vy) ** 0.5 if vx and vy else None


def precision_at_k(pairs: Sequence[PairResult], k: int) -> float | None:
    """Mean over profiles: of the top-k matched labelled pairs by score, the
    share labelled maybe or apply. Profiles with no matched pair are skipped."""
    by_profile: dict[str, list[PairResult]] = {}
    for p in pairs:
        if p.label is not None and p.score is not None:
            by_profile.setdefault(p.profile.id, []).append(p)
    values = []
    for rows in by_profile.values():
        top = sorted(rows, key=lambda p: p.score_or_zero, reverse=True)[:k]
        values.append(sum(1 for p in top if (p.label or 0) >= 1) / len(top))
    return sum(values) / len(values) if values else None


def false_positive_rate(pairs: Sequence[PairResult]) -> float | None:
    """Of the labelled pairs the system would notify, the share labelled 0."""
    notified = [p for p in pairs if p.label is not None and p.notified]
    if not notified:
        return None
    return sum(1 for p in notified if p.label == 0) / len(notified)


def hard_gate_violations(
    pairs: Sequence[PairResult], oracle: Oracle
) -> list[tuple[PairResult, list[str]]]:
    """Notified pairs (labelled or not) that break a hard rule per the labels."""
    found = []
    for p in pairs:
        if p.notified and (broken := oracle.violations(p.profile, p.job)):
            found.append((p, broken))
    return found


def score_histogram(pairs: Sequence[PairResult]) -> dict[int, int]:
    counts = Counter(int(p.score) for p in pairs if p.score is not None)
    return {bucket: counts.get(bucket, 0) for bucket in range(11)}
