"""The independent auditor (brief §8.4): a second, stronger model re-scores a
sample of last week's matches from the posting and the user's settings alone,
without seeing the system's score. The verdict compares the two scores in
code: within `agree_within` is "justified", otherwise "overrated" or
"underrated"; "unclear" when the auditor says the posting is too thin.

Also the weekly calibration watch (score spread, share of 8+ scores) and the
👍/👎 rates per track. The auditor gets tracks, level, years, skills and
location choices, never a name, username or id.
"""

import hashlib
import json
import statistics
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from jobhunt.config import Config
from jobhunt.config.models import ChainEntry
from jobhunt.db import Database, Row, timestamp
from jobhunt.extract.budget import LlmBudget
from jobhunt.extract.extractor import ModelCaller
from jobhunt.extract.grounding import Grounder
from jobhunt.extract.llm import LlmClient, Prompt
from jobhunt.extract.schema import strict_schema
from jobhunt.normalize.text import truncate

Verdict = Literal["justified", "overrated", "underrated", "unclear"]
PARTS = ("role", "skills", "experience", "education", "preference")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AuditPart(_Strict):
    points: float = Field(ge=0, le=10)
    reason: str
    quote: str | None


class AuditAnswer(_Strict):
    role: AuditPart
    skills: AuditPart
    experience: AuditPart
    education: AuditPart
    preference: AuditPart
    enough_information: bool
    notes: str


@dataclass(frozen=True)
class AuditResult:
    match_id: int
    system_score: Decimal
    auditor_score: Decimal
    verdict: Verdict
    notes: str

    @property
    def delta(self) -> Decimal:
        return self.auditor_score - self.system_score


def auditor_entry(config: Config, environ: Mapping[str, str]) -> ChainEntry:
    """AUDITOR_PROVIDER/AUDITOR_MODEL, else the configured default. Per-minute and
    daily limits come from the chain entry for the same model, if there is one."""
    settings = config.llm.audit
    provider = environ.get("AUDITOR_PROVIDER") or settings.provider
    model = environ.get("AUDITOR_MODEL") or settings.model
    known = config.llm.providers.get(provider)
    if known is None:
        raise ValueError(f"AUDITOR_PROVIDER {provider!r} is not a provider in config/llm.yaml")
    if known.trains_on_inputs:
        raise ValueError(f"{provider} may train on inputs; the auditor sees user settings")
    same = next((e for e in config.llm.chain if e.provider == provider and e.model == model), None)
    if same is not None:
        return same
    return ChainEntry(provider=provider, model=model, rpm=None, tpm=None, rpd=None, tpd=None)


def auditor_environ(config: Config, environ: Mapping[str, str]) -> dict[str, str]:
    """The environment with AUDITOR_API_KEY, if set, standing in for the key."""
    merged = dict(environ)
    entry = auditor_entry(config, environ)
    if key := environ.get("AUDITOR_API_KEY"):
        merged[config.llm.providers[entry.provider].api_key_env] = key
    return merged


def verdict(system: Decimal, auditor: Decimal, enough: bool, agree_within: float) -> Verdict:
    if not enough:
        return "unclear"
    if abs(auditor - system) <= Decimal(str(agree_within)):
        return "justified"
    return "overrated" if system > auditor else "underrated"


_CANDIDATES = """
SELECT m.id, m.score, m.created_at, j.title, j.company, j.location_raw, j.jd_text, j.url,
       p.tracks_json, p.experience_level, p.years_experience, p.locations_json,
       p.job_types_json, p.preferred_cities_json,
       COALESCE(pr.skills_json, '[]') AS skills_json, pr.years_experience AS cv_years
FROM matches m
JOIN jobs j ON j.id = m.job_id
JOIN user_prefs p ON p.user_id = m.user_id
LEFT JOIN user_profiles pr ON pr.user_id = m.user_id
WHERE m.created_at >= ? AND j.jd_text IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM audits a WHERE a.match_id = m.id AND a.auditor_model = ?)
ORDER BY m.score DESC, m.id
"""


def _sampled(match_id: int, share: float) -> bool:
    digest = hashlib.sha256(f"audit:{match_id}".encode()).digest()
    return int.from_bytes(digest[:4], "big") / 2**32 < share


def sample(rows: Sequence[Row], config: Config) -> list[Row]:
    """Every match at or above `audit_all_from`, plus a stable `sample_rest`
    share of the others; at most `max_per_run`, highest scores first."""
    s = config.llm.audit
    chosen = [
        row
        for row in rows
        if float(str(row["score"])) >= s.audit_all_from
        or _sampled(int(str(row["id"])), s.sample_rest)
    ]
    return chosen[: s.max_per_run]


class Auditor:
    def __init__(
        self, db: Database, config: Config, client: LlmClient, environ: Mapping[str, str]
    ) -> None:
        self._db = db
        self._config = config
        self._s = config.llm.audit
        self._client = client
        self._entry = auditor_entry(config, environ)
        self._prompt = Prompt.load(config.root / self._s.prompt)
        self._schema = strict_schema(AuditAnswer, [])
        self._grounder = Grounder(config.skills, config.llm.extraction.min_evidence_score)
        self._weights = config.scoring.weights
        self._tracks = {t.id: t.label for t in config.tracks.tracks}
        self._skills = {s.id: s.name for s in config.skills.all_skills}
        self._cities = {d.id: d.name for d in config.locations.districts}

    @property
    def model(self) -> str:
        return self._entry.model

    def profile_text(self, row: Row) -> str:
        years = row["years_experience"] if row["years_experience"] is not None else row["cv_years"]
        tracks = [self._tracks.get(t, t) for t in json.loads(str(row["tracks_json"]))]
        skills = [self._skills.get(s, s) for s in json.loads(str(row["skills_json"]))]
        cities = [self._cities.get(c, c) for c in json.loads(str(row["preferred_cities_json"]))]
        locations = json.loads(str(row["locations_json"]))
        lines = [
            f"- Tracks: {', '.join(tracks)}",
            f"- Level: {str(row['experience_level']).replace('_', ' ')}"
            + (f", {years!s} years of experience" if years is not None else ""),
            f"- Skills: {', '.join(skills) or 'none given'}",
            "- Can work: "
            + ", ".join(
                "in Bangladesh" if x == "bangladesh" else "remotely (worldwide)" for x in locations
            ),
            f"- Job types: {', '.join(json.loads(str(row['job_types_json'])))}",
            f"- Preferred cities: {', '.join(cities) or 'none'}",
        ]
        return "\n".join(lines)

    async def audit(self, rows: Sequence[Row], now: datetime) -> list[AuditResult]:
        only_auditor = self._config.llm.model_copy(update={"chain": [self._entry]})
        budget = LlmBudget(only_auditor, self._db, now, self._client.available)
        await budget.load()
        caller = ModelCaller(self._client, budget, self._config.llm.extraction.tokens_per_char)
        w = self._weights
        results = []
        for row in rows:
            posting = truncate(str(row["jd_text"]), self._config.app.normalization.jd_max_chars_llm)
            system, user = self._prompt.render(
                role_max=f"{w.role:g}",
                skills_max=f"{w.skills:g}",
                experience_max=f"{w.experience:g}",
                education_max=f"{w.education:g}",
                preference_max=f"{w.preference:g}",
                profile=self.profile_text(row),
                title=str(row["title"]),
                company=str(row["company"] or "unknown"),
                location=str(row["location_raw"] or "unknown"),
                posting=posting,
            )
            answer = await caller.call(
                self._prompt,
                system,
                user,
                "audit",
                self._schema,
                AuditAnswer,
                self._config.llm.request.max_output_tokens,
            )
            if answer is None:
                break  # the auditor's budget is spent; the rest wait for next week
            result = self._result(row, answer[0], posting)
            await self._store(result)
            results.append(result)
        return results

    def _result(self, row: Row, answer: AuditAnswer, posting: str) -> AuditResult:
        caps = {name: Decimal(str(getattr(self._weights, name))) for name in PARTS}
        parts = {name: getattr(answer, name) for name in PARTS}
        total = sum(
            (min(Decimal(str(parts[n].points)), caps[n]) for n in PARTS), Decimal(0)
        ).quantize(Decimal("0.1"), ROUND_HALF_UP)
        system = Decimal(str(row["score"]))
        unquoted = [n for n in PARTS if parts[n].quote and not self._grounder.quoted(
            parts[n].quote, posting)]  # fmt: skip
        notes = answer.notes.strip()
        if unquoted:
            notes = f"{notes} [quotes not in the posting: {', '.join(unquoted)}]".strip()
        return AuditResult(
            match_id=int(str(row["id"])),
            system_score=system,
            auditor_score=total,
            verdict=verdict(system, total, answer.enough_information, self._s.agree_within),
            notes=notes[:1000],
        )

    async def _store(self, result: AuditResult) -> None:
        await self._db.execute(
            "INSERT INTO audits (match_id, auditor_provider, auditor_model, auditor_score, delta,"
            " verdict, notes) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                result.match_id,
                self._entry.provider,
                self._entry.model,
                float(result.auditor_score),
                float(result.delta),
                result.verdict,
                result.notes,
            ),
        )


async def candidates(db: Database, config: Config, model: str, now: datetime) -> list[Row]:
    since = timestamp(now - timedelta(days=config.llm.audit.days))
    return await db.query(_CANDIDATES, (since, model))


# --- weekly report -----------------------------------------------------------------


@dataclass
class WeeklyReport:
    audits: int
    agreement: float | None
    mean_abs_delta: float | None
    verdicts: dict[str, int]
    worst: list[Row]
    matches: int
    histogram: dict[int, int]
    strong_share: float | None
    stdev: float | None
    feedback: dict[str, Counter[str]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


async def weekly_report(db: Database, config: Config, now: datetime) -> WeeklyReport:
    s = config.llm.audit
    since = timestamp(now - timedelta(days=s.days))
    audits = await db.query(
        "SELECT a.match_id, a.auditor_model, a.auditor_score, a.delta, a.verdict, a.notes,"
        " m.score, j.title, j.url FROM audits a JOIN matches m ON m.id = a.match_id"
        " JOIN jobs j ON j.id = m.job_id WHERE a.created_at >= ? ORDER BY ABS(a.delta) DESC",
        (since,),
    )
    deltas = [abs(float(str(a["delta"]))) for a in audits]
    verdicts = Counter(str(a["verdict"]) for a in audits)
    judged = sum(verdicts[v] for v in ("justified", "overrated", "underrated"))
    scores = [
        float(str(r["score"]))
        for r in await db.query("SELECT score FROM matches WHERE created_at >= ?", (since,))
    ]
    histogram = Counter(min(int(x), 10) for x in scores)
    strong = config.scoring.labels[0].min
    report = WeeklyReport(
        audits=len(audits),
        agreement=verdicts["justified"] / judged if judged else None,
        mean_abs_delta=sum(deltas) / len(deltas) if deltas else None,
        verdicts=dict(verdicts),
        worst=list(audits[: s.worst_shown]),
        matches=len(scores),
        histogram={bucket: histogram.get(bucket, 0) for bucket in range(11)},
        strong_share=sum(1 for x in scores if x >= strong) / len(scores) if scores else None,
        stdev=statistics.pstdev(scores) if len(scores) > 1 else None,
    )
    if report.strong_share is not None and report.strong_share > s.max_strong_share:
        report.warnings.append("inflation")
    if report.stdev is not None and report.stdev < s.min_score_stdev:
        report.warnings.append("narrow")
    rows = await db.query(
        "SELECT json_extract(m.gate_results_json, '$.tracks.evidence') AS track, f.kind, f.reason"
        " FROM feedback f JOIN matches m ON m.id = f.match_id"
        " WHERE f.created_at >= ? AND f.kind IN ('thumbs_up', 'thumbs_down')",
        (since,),
    )
    feedback: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        track = str(row["track"] or "unknown")
        feedback[track][str(row["kind"])] += 1
        if row["reason"]:
            feedback[track][f"reason: {row['reason']!s}"] += 1
    report.feedback = dict(feedback)
    return report


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{value:.0%}"


def report_markdown(report: WeeklyReport, model: str) -> str:
    lines = [
        "## Weekly audit",
        "",
        f"- Matches re-scored by `{model}` this week: {report.audits}",
        f"- Agreement (within the configured margin): {_pct(report.agreement)}",
        "- Mean |difference|: "
        + ("-" if report.mean_abs_delta is None else f"{report.mean_abs_delta:.2f}"),
        f"- Verdicts: {', '.join(f'{k} {v}' for k, v in sorted(report.verdicts.items())) or '-'}",
        "",
        "### Largest disagreements",
        "",
    ]
    for row in report.worst:
        lines.append(
            f"- [{row['title']!s}]({row['url']!s}): system {row['score']!s}, "
            f"auditor {row['auditor_score']!s} ({row['verdict']!s}). {row['notes'] or ''!s}"
        )
    lines += [
        "",
        "### Calibration",
        "",
        f"Matches this week: {report.matches}; share scoring 8+: {_pct(report.strong_share)}; "
        "spread (std dev): " + ("-" if report.stdev is None else f"{report.stdev:.2f}"),
        "",
        "| " + " | ".join(str(b) for b in report.histogram) + " |",
        "|" + " ---: |" * len(report.histogram),
        "| " + " | ".join(str(c) for c in report.histogram.values()) + " |",
        "",
    ]
    lines += [f"**Warning: {w}**" for w in report.warnings]
    lines += ["", "### Feedback by track", ""]
    for track, counts in sorted(report.feedback.items()):
        lines.append(f"- {track}: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    return "\n".join(lines) + "\n"
