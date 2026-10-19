"""The deterministic, explainable scorer (brief §6.7).

Hard gates first (tracks, the user's location choice, job type, seniority,
scam markers): a job failing one is never sent to that user. Then five
components from config/scoring.yaml, each rounded to 2 decimals
(ROUND_HALF_UP, never float rounding) and the total rounded to 1 decimal, so the
parts shown in a message add up to the score shown. Every component records the
rule it applied, the user's value, the job's value and the posting's quote.
"""

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from jobhunt.config import Config
from jobhunt.config.models import Track
from jobhunt.gates.base import GateResult, PhraseFinder
from jobhunt.gates.seniority import seniority_gate
from jobhunt.normalize.titles import TitleNormalizer
from jobhunt.score.model import JobView, UserProfile

TWO = Decimal("0.01")
ONE = Decimal("0.1")


def d(value: float | int | str) -> Decimal:
    return Decimal(str(value))


def q2(value: Decimal) -> Decimal:
    return value.quantize(TWO, rounding=ROUND_HALF_UP)


def q1(value: Decimal) -> Decimal:
    return value.quantize(ONE, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class Component:
    name: str
    points: Decimal
    max: Decimal
    rule: str  # a locale key naming the rule that applied
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "points": str(self.points),
            "max": str(self.max),
            "rule": self.rule,
            **self.detail,
        }


@dataclass(frozen=True)
class ScoreResult:
    passed: bool
    gates: tuple[GateResult, ...]
    components: tuple[Component, ...] = ()
    score: Decimal = Decimal("0.0")
    label: str = ""
    scorer_version: str = ""

    @property
    def failed_gate(self) -> GateResult | None:
        return next((g for g in self.gates if g.status == "fail"), None)

    def breakdown(self) -> dict[str, Any]:
        return {c.name: str(c.points) for c in self.components} | {"score": str(self.score)}

    def explanation(self) -> dict[str, Any]:
        return {c.name: c.as_dict() for c in self.components}

    def gate_results(self) -> dict[str, Any]:
        return {g.gate: g.as_dict() for g in self.gates}


class Scorer:
    def __init__(self, config: Config) -> None:
        self._s = config.scoring
        self._seniority = config.app.seniority_gate
        self._tracks = {track.id: track for track in config.tracks.tracks}
        self._titles = TitleNormalizer(config.title_rules, config.locations.place_names())
        self._include = {
            track.id: {self._titles.normalize(t).key for t in track.include_titles}
            for track in config.tracks.tracks
        }
        self._red_flags = PhraseFinder(config.app.red_flags)
        # Title keys and red-flag hits depend on the job alone; matching scores
        # each job against every user, so they are computed once per job.
        self._title_keys: dict[str, str] = {}
        self._flag_hits: dict[tuple[str, ...], str | None] = {}

    def score(self, job: JobView, user: UserProfile) -> ScoreResult:
        role = self._role(job, user)
        gates = (
            role[1],
            self._location_gate(job, user),
            self._job_type_gate(job, user),
            seniority_gate(
                self._seniority,
                user.level,
                user.years,
                None if job.facts.seniority == "unknown" else job.facts.seniority,
                job.facts.min_years,
            ),
            self._red_flag_gate(job),
        )
        if any(g.status == "fail" for g in gates):
            return ScoreResult(False, gates, scorer_version=self._s.scorer_version)
        components = (
            role[0],
            self._skills(job, user),
            self._experience(job, user),
            self._education(job),
            self._preference(job, user),
        )
        total = q1(sum((c.points for c in components), Decimal(0)))
        label = next(lbl.key for lbl in self._s.labels if total >= d(lbl.min))
        return ScoreResult(True, gates, components, total, label, self._s.scorer_version)

    # --- gates ----------------------------------------------------------------

    def _location_gate(self, job: JobView, user: UserProfile) -> GateResult:
        remote = job.facts.work_mode == "remote"
        if job.location_reason == "worldwide":
            ok, need = "remote" in user.locations, "remote"
        elif remote:  # a remote job open to Bangladesh suits either choice
            ok, need = bool(user.locations), "bangladesh or remote"
        else:
            ok, need = "bangladesh" in user.locations, "bangladesh"
        reason = "matches_user_choice" if ok else "user_wants_other_locations"
        return GateResult("user_location", "pass" if ok else "fail", reason, need)

    def _job_type_gate(self, job: JobView, user: UserProfile) -> GateResult:
        kind = job.facts.employment_type
        if kind == "unknown" or kind in user.job_types:
            return GateResult("job_type", "pass", "accepted", kind)
        return GateResult("job_type", "fail", "user_wants_other_job_types", kind)

    def _red_flag_gate(self, job: JobView) -> GateResult:
        flags = tuple(job.facts.red_flags)
        if flags not in self._flag_hits:
            self._flag_hits[flags] = self._red_flags.find(" | ".join(flags))
        if flag := self._flag_hits[flags]:
            return GateResult("red_flags", "fail", "scam_marker", flag)
        return GateResult("red_flags", "pass", "none")

    def _title_key(self, title: str) -> str:
        key = self._title_keys.get(title)
        if key is None:
            key = self._title_keys[title] = self._titles.normalize(title).key
        return key

    # --- components -----------------------------------------------------------

    def _role(self, job: JobView, user: UserProfile) -> tuple[Component, GateResult]:
        cap = d(self._s.weights.role)
        title_key = self._title_key(job.title)
        best: tuple[Decimal, str, str] | None = None
        for track_id in user.tracks:
            track = self._tracks.get(track_id)
            if track is None:
                continue
            off = user.deselected.get(track_id, frozenset())
            primary = set(track.domains.primary) - off
            adjacent = set(track.domains.adjacent) - off
            hit = self._role_hit(job, primary, adjacent, title_key, track, user.include_adjacent)
            if hit and (best is None or hit[0] > best[0]):
                best = (hit[0], hit[1], track_id)
        if best is None:
            gate = GateResult("tracks", "fail", "outside_chosen_tracks", job.primary_domain)
            return Component("role", Decimal(0), cap, "role.none"), gate
        points, rule, track_id = best
        detail = {"track": track_id, "job_domain": job.primary_domain}
        gate = GateResult("tracks", "pass", rule, track_id)
        return Component("role", q2(points), cap, f"role.{rule}", detail), gate

    def _role_hit(
        self,
        job: JobView,
        primary: set[str],
        adjacent: set[str],
        title_key: str,
        track: Track,
        include_adjacent: bool,
    ) -> tuple[Decimal, str] | None:
        cfg = self._s.role
        if job.primary_domain in primary:
            return d(cfg.primary), "primary"
        if primary & set(job.secondary_domains) or title_key in self._include[track.id]:
            return d(cfg.secondary_or_include), "secondary_or_include"
        domains = {job.primary_domain, *job.secondary_domains}
        if include_adjacent and adjacent & domains:
            return d(cfg.adjacent), "adjacent"
        return None

    def _skills(self, job: JobView, user: UserProfile) -> Component:
        cfg, cap = self._s.skills, d(self._s.weights.skills)
        must = [s.casefold() for s in job.facts.required_skills]
        nice = [s.casefold() for s in job.facts.preferred_skills if s.casefold() not in must]
        if not must and not nice:
            return Component("skills", q2(d(cfg.none_listed)), cap, "skills.none_listed")
        have = {s.casefold() for s in user.skills}
        must_hit = [s for s in must if s in have]
        nice_hit = [s for s in nice if s in have]
        total = cfg.must_weight * len(must) + cfg.nice_weight * len(nice)
        got = cfg.must_weight * len(must_hit) + cfg.nice_weight * len(nice_hit)
        detail: dict[str, Any] = {
            "matched": must_hit + nice_hit,
            "missing": [s for s in must + nice if s not in have],
        }
        points = d(got) / d(total) * cap
        if len(must) + len(nice) < cfg.few_listed:
            detail["few_listed"] = len(must) + len(nice)
            points = min(points, d(cfg.few_listed_cap))
        return Component("skills", q2(points), cap, "skills.coverage", detail)

    def _experience(self, job: JobView, user: UserProfile) -> Component:
        cfg, cap = self._s.experience, d(self._s.weights.experience)
        years = self._seniority.default_years[user.level] if user.years is None else user.years
        facts = job.facts
        detail: dict[str, Any] = {
            "user_level": user.level,
            "user_years": years,
            "min_years": facts.min_years,
            "max_years": facts.max_years,
            "evidence": facts.years_evidence,
        }
        if facts.min_years is None:
            points, rule = d(cfg.unknown), "experience.unknown"
        else:
            gap = d(facts.min_years) - d(years)
            if gap <= 0:
                points, rule = d(cfg.met), "experience.met"
            else:
                steps = sorted(cfg.gap_points.items())
                points = next((d(p) for limit, p in steps if gap <= limit), Decimal(0))
                rule = "experience.gap"
        fitting = cfg.fitting_seniority[user.level]
        if facts.seniority != "unknown" and facts.seniority not in fitting:
            points = max(Decimal(0), points - d(cfg.seniority_mismatch_penalty))
            detail["seniority_mismatch"] = facts.seniority
        return Component("experience", q2(points), cap, rule, detail)

    def _education(self, job: JobView) -> Component:
        cfg, cap = self._s.education, d(self._s.weights.education)
        edu = job.facts.education
        detail = {
            "level": edu.level,
            "fields": edu.fields,
            "evidence": job.facts.education_evidence,
        }
        if not edu.required or not edu.fields or set(edu.fields) & set(cfg.accepted_fields):
            return Component("education", q2(d(cfg.accepted)), cap, "education.accepted", detail)
        if set(edu.fields) & set(cfg.broad_fields):
            return Component("education", q2(d(cfg.broad)), cap, "education.broad", detail)
        return Component("education", q2(d(cfg.other_field)), cap, "education.other", detail)

    def _preference(self, job: JobView, user: UserProfile) -> Component:
        cfg, cap = self._s.preference, d(self._s.weights.preference)
        points = Decimal(0)
        hits: list[str] = []
        if job.city and job.city in user.preferred_cities:
            points += d(cfg.city)
            hits.append("city")
        remote = job.facts.work_mode == "remote"
        if (remote and "remote" in user.locations) or (
            not remote and job.location_reason == "bangladesh" and "bangladesh" in user.locations
        ):
            points += d(cfg.work_mode)
            hits.append("work_mode")
        if job.facts.salary is not None or job.salary_text:
            points += d(cfg.salary_disclosed)
            hits.append("salary")
        detail = {
            "hits": hits,
            "job_city": job.city,
            "preferred_cities": list(user.preferred_cities),
        }
        return Component("preference", q2(min(points, cap)), cap, "preference.sum", detail)
