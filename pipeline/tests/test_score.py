import json
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from jobhunt.config import Config
from jobhunt.db import SqliteBackend, apply_migrations
from jobhunt.extract.schema import JobFacts
from jobhunt.score.matching import match
from jobhunt.score.model import JobView, UserProfile
from jobhunt.score.render import Renderer
from jobhunt.score.scorer import Scorer, q1

NOW = datetime(2026, 10, 18, 9, 0, tzinfo=UTC)


def _facts(**changes: Any) -> JobFacts:
    base: dict[str, Any] = {
        "is_cse_role": True, "is_cse_role_evidence": "ML Engineer", "primary_domain": "ai-ml",
        "secondary_domains": [], "normalized_title": "ML Engineer", "seniority": "junior",
        "seniority_evidence": None, "min_years": 0, "max_years": 1, "years_evidence": "0-1 years",
        "education": {"level": "bachelor", "fields": ["cse"], "required": True},
        "education_evidence": "BSc in CSE", "required_skills": ["python", "pytorch"],
        "preferred_skills": ["docker"], "employment_type": "full_time", "work_mode": "onsite",
        "country": "BD", "city": "Dhaka", "remote_eligibility": "not_remote",
        "remote_eligibility_evidence": "Dhaka office", "timezone_requirement": None,
        "salary": None, "application_deadline": None, "deadline_evidence": None,
        "posted_date_in_text": None, "language_requirements": [], "red_flags": [],
        "summary": "Builds models.",
    }  # fmt: skip
    base.update(changes)
    return JobFacts.model_validate(base)


def _job(**changes: Any) -> JobView:
    facts = changes.pop("facts", _facts())
    base: dict[str, Any] = {
        "job_id": 1, "title": "Junior Machine Learning Engineer", "company": "Acme",
        "url": "https://x", "source": "bdjobs", "location_raw": "Dhaka", "facts": facts,
        "primary_domain": "ai-ml", "secondary_domains": (), "location_reason": "bangladesh",
        "location_evidence": "Dhaka", "city": "dhaka", "posted_at": NOW - timedelta(days=2),
        "deadline": date(2026, 10, 30),
    }  # fmt: skip
    base.update(changes)
    return JobView(**base)


def _user(**changes: Any) -> UserProfile:
    base: dict[str, Any] = {
        "user_id": 7, "tracks": ("ai_ml",), "level": "fresh_grad", "years": None,
        "locations": frozenset({"bangladesh"}), "job_types": frozenset({"full_time", "internship"}),
        "min_score": Decimal("6.0"), "skills": frozenset({"python", "pytorch", "docker"}),
    }  # fmt: skip
    base.update(changes)
    return UserProfile(**base)


@pytest.fixture(scope="module")
def scorer(config: Config) -> Scorer:
    return Scorer(config)


# --- components ----------------------------------------------------------------


def test_full_marks(scorer: Scorer) -> None:
    result = scorer.score(_job(), _user(preferred_cities=("dhaka",)))
    assert result.passed
    assert result.breakdown() == {
        "role": "3.00", "skills": "3.00", "experience": "2.00", "education": "1.00",
        "preference": "0.80", "score": "9.8",
    }  # fmt: skip
    assert result.label == "strong"
    assert result.scorer_version == "scorer.v2"


@pytest.mark.parametrize(
    ("job", "user", "role", "rule"),
    [
        ({}, {}, "3.00", "role.primary"),
        ({"primary_domain": "language-specialists", "title": "Python Developer"}, {}, "2.25",
         "role.secondary_or_include"),  # the owner's example: AI track includes Python roles
        ({"primary_domain": "software-engineering", "secondary_domains": ("ai-ml",)}, {}, "2.25",
         "role.secondary_or_include"),
        ({"primary_domain": "data-science-analytics"}, {}, "1.50", "role.adjacent"),
    ],
)  # fmt: skip
def test_role_fit(
    scorer: Scorer, job: dict[str, Any], user: dict[str, Any], role: str, rule: str
) -> None:
    result = scorer.score(_job(**job), _user(**user))
    component = next(c for c in result.components if c.name == "role")
    assert (str(component.points), component.rule) == (role, rule)


def test_security_user_never_gets_frontend(scorer: Scorer) -> None:
    # The owner's example: cross-track leakage is a hard gate.
    frontend = _job(primary_domain="software-engineering", title="Frontend Developer")
    result = scorer.score(frontend, _user(tracks=("cybersecurity",)))
    assert not result.passed
    assert result.failed_gate is not None
    assert (result.failed_gate.gate, result.failed_gate.reason) == (
        "tracks",
        "outside_chosen_tracks",
    )


def test_adjacent_toggle_and_deselected_domains(scorer: Scorer) -> None:
    adjacent = _job(primary_domain="data-science-analytics")
    assert not scorer.score(adjacent, _user(include_adjacent=False)).passed
    deselected = _user(deselected={"ai_ml": frozenset({"ai-ml"})})
    assert not scorer.score(_job(), deselected).passed


@pytest.mark.parametrize(
    ("facts", "skills", "points"),
    [
        ({}, {"python", "pytorch", "docker"}, "3.00"),
        ({}, {"python"}, "1.20"),  # (2*1 + 0) / (2*2 + 1) * 3
        ({}, set(), "0.00"),
        ({"required_skills": [], "preferred_skills": []}, set(), "1.50"),
        # One skill listed (the duplicate counts once): matching it is capped.
        ({"required_skills": ["Python"], "preferred_skills": ["python"]}, {"python"}, "2.25"),
        ({"required_skills": ["python", "sql"], "preferred_skills": []}, {"sql"}, "1.50"),
    ],
)
def test_skills_fit(scorer: Scorer, facts: dict[str, Any], skills: set[str], points: str) -> None:
    result = scorer.score(_job(facts=_facts(**facts)), _user(skills=frozenset(skills)))
    assert str(next(c for c in result.components if c.name == "skills").points) == points


@pytest.mark.parametrize(
    ("facts", "user", "points", "rule"),
    [
        ({"min_years": 0}, {}, "2.00", "experience.met"),
        ({"min_years": 1}, {}, "1.00", "experience.gap"),
        ({"min_years": 2}, {}, "0.50", "experience.gap"),
        ({"min_years": 2}, {"years": 2.0}, "2.00", "experience.met"),
        ({"min_years": None, "years_evidence": None}, {}, "1.50", "experience.unknown"),
        ({"seniority": "senior", "min_years": 0}, {"level": "mid"}, "2.00", "experience.met"),
        ({"seniority": "mid", "min_years": 0}, {}, "1.50", "experience.met"),  # mismatch penalty
    ],
)
def test_experience_fit(
    scorer: Scorer, facts: dict[str, Any], user: dict[str, Any], points: str, rule: str
) -> None:
    result = scorer.score(_job(facts=_facts(**facts)), _user(**user))
    component = next(c for c in result.components if c.name == "experience")
    assert (str(component.points), component.rule) == (points, rule)


@pytest.mark.parametrize(
    ("education", "points"),
    [
        ({"level": "bachelor", "fields": ["cse"], "required": True}, "1.00"),
        ({"level": "unknown", "fields": [], "required": False}, "1.00"),
        ({"level": "bachelor", "fields": ["any_engineering"], "required": True}, "0.75"),
        ({"level": "bachelor", "fields": ["eee"], "required": True}, "0.00"),
    ],
)
def test_education_fit(scorer: Scorer, education: dict[str, Any], points: str) -> None:
    result = scorer.score(_job(facts=_facts(education=education)), _user())
    assert str(next(c for c in result.components if c.name == "education").points) == points


# --- hard gates ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("job", "user", "gate"),
    [
        (
            {"location_reason": "worldwide", "facts": _facts(work_mode="remote")},
            {},
            "user_location",
        ),
        ({}, {"locations": frozenset({"remote"})}, "user_location"),
        ({"facts": _facts(employment_type="contract")}, {}, "job_type"),
        ({"facts": _facts(min_years=5)}, {}, "seniority"),
        ({"facts": _facts(seniority="lead_plus")}, {}, "seniority"),
        ({"facts": _facts(red_flags=["Training fee of 5000 BDT"])}, {}, "red_flags"),
    ],
)
def test_hard_gates(scorer: Scorer, job: dict[str, Any], user: dict[str, Any], gate: str) -> None:
    result = scorer.score(_job(**job), _user(**user))
    assert not result.passed
    assert result.failed_gate is not None
    assert result.failed_gate.gate == gate


def test_remote_job_open_to_bangladesh_suits_either_choice(scorer: Scorer) -> None:
    remote_bd = _job(facts=_facts(work_mode="remote"), location_reason="bangladesh")
    assert scorer.score(remote_bd, _user(locations=frozenset({"remote"}))).passed
    assert scorer.score(remote_bd, _user(locations=frozenset({"bangladesh"}))).passed
    worldwide = _job(facts=_facts(work_mode="remote"), location_reason="worldwide")
    assert scorer.score(worldwide, _user(locations=frozenset({"remote"}))).passed


def test_unknown_employment_type_passes(scorer: Scorer) -> None:
    assert scorer.score(_job(facts=_facts(employment_type="unknown")), _user()).passed


# --- properties (brief §6.7) ---------------------------------------------------

SKILLS = ["python", "pytorch", "sql", "docker", "aws", "react", "java"]


@settings(max_examples=150, deadline=None)
@given(
    required=st.lists(st.sampled_from(SKILLS), max_size=5, unique=True),
    preferred=st.lists(st.sampled_from(SKILLS), max_size=4, unique=True),
    have=st.sets(st.sampled_from(SKILLS)),
    extra=st.sampled_from(SKILLS),
)
def test_adding_a_matching_skill_never_lowers_the_score(
    scorer: Scorer, required: list[str], preferred: list[str], have: set[str], extra: str
) -> None:
    job = _job(facts=_facts(required_skills=required, preferred_skills=preferred))
    before = scorer.score(job, _user(skills=frozenset(have)))
    after = scorer.score(job, _user(skills=frozenset(have | {extra})))
    assert after.score >= before.score


@settings(max_examples=100, deadline=None)
@given(low=st.integers(0, 15), high=st.integers(0, 15), years=st.floats(0, 10))
def test_more_years_asked_never_raises_experience_fit(
    scorer: Scorer, low: int, high: int, years: float
) -> None:
    low, high = sorted((low, high))
    user = _user(level="senior", years=years)  # senior: no seniority gate in the way

    def points(min_years: int) -> Decimal:
        result = scorer.score(_job(facts=_facts(min_years=min_years, max_years=None)), user)
        return next(c.points for c in result.components if c.name == "experience")

    assert points(high) <= points(low)


@settings(max_examples=150, deadline=None)
@given(
    required=st.lists(st.sampled_from(SKILLS), max_size=5, unique=True),
    have=st.sets(st.sampled_from(SKILLS)),
    min_years=st.one_of(st.none(), st.integers(0, 3)),
    cities=st.sets(st.sampled_from(["dhaka", "chattogram", "sylhet"])),
    domain=st.sampled_from(["ai-ml", "language-specialists", "data-science-analytics"]),
)
def test_components_always_add_up_to_the_score(
    scorer: Scorer,
    required: list[str],
    have: set[str],
    min_years: int | None,
    cities: set[str],
    domain: str,
) -> None:
    job = _job(primary_domain=domain, facts=_facts(required_skills=required, min_years=min_years))
    result = scorer.score(job, _user(skills=frozenset(have), preferred_cities=tuple(cities)))
    if result.passed:
        assert result.score == q1(sum((c.points for c in result.components), Decimal(0)))
        assert all(c.points <= c.max for c in result.components)
        assert Decimal(0) <= result.score <= Decimal(10)


# --- rendering -----------------------------------------------------------------


def test_alert_follows_the_brief_layout(config: Config, scorer: Scorer) -> None:
    user = _user(preferred_cities=("chattogram",))
    job = _job(facts=_facts(language_requirements=["Excellent Bangla and English"]))
    text = Renderer.from_config(config).alert(job, scorer.score(job, user), user, NOW)
    lines = text.splitlines()
    assert lines[0] == "🎯 <b>Junior Machine Learning Engineer</b>"
    assert lines[1] == "🏢 Acme · 📍 Dhaka (On-site) · 🕒 Full-time"
    assert lines[2] == "📅 Posted 2 days ago · ⏳ Apply by 30 Oct"
    assert "⭐ <b>9.3/10 · Strong match</b> for you" in text
    assert "✅ Skills: Python, PyTorch ✓ · missing: Docker" not in text  # user has docker
    assert "(3/3)" in text
    partial = "\u2796"  # the "partial" mark
    assert f"{partial} Preferences: Dhaka (you prefer Chattogram) · On-site ✓ (0.3/1)" in text
    assert "⚠️ Excellent Bangla and English" in text
    assert text.endswith("🔗 via BDJobs")


def test_alert_says_when_few_skills_are_listed(config: Config, scorer: Scorer) -> None:
    job = _job(facts=_facts(required_skills=["python"], preferred_skills=[]))
    text = Renderer.from_config(config).alert(job, scorer.score(job, _user()), _user(), NOW)
    assert "Skills: Python ✓ · only 1 listed (2.25/3)" in text


def test_alert_escapes_values_and_marks_quick_matches(config: Config, scorer: Scorer) -> None:
    job = _job(title="Dev <script>", company="A & B", quick=True)
    text = Renderer.from_config(config).alert(job, scorer.score(job, _user()), _user(), NOW)
    assert "Dev &lt;script&gt;" in text
    assert "A &amp; B" in text
    assert "&amp;amp;" not in text
    assert "⚡ Quick match" in text


def test_why_shows_rules_quotes_and_version(config: Config, scorer: Scorer) -> None:
    job = _job()
    text = Renderer.from_config(config).why(job, scorer.score(job, _user()), _user())
    assert "“0-1 years”" in text
    assert "“BSc in CSE”" in text
    assert "Location: Dhaka" in text
    assert "scorer.v2" in text
    assert "&amp;amp;" not in text


def test_unstated_work_mode_and_multiline_quotes_render_cleanly(
    config: Config, scorer: Scorer
) -> None:
    # Found by rendering a golden-set alert: an on-site Bangladesh job with no
    # stated work mode read "Preferences: unknown ✓", and BDJobs quotes kept
    # their line breaks.
    facts = _facts(work_mode="unknown", years_evidence="Experience\n\n• 0-1 years")
    job = _job(facts=facts, city=None)
    renderer = Renderer.from_config(config)
    text = renderer.alert(job, scorer.score(job, _user()), _user(), NOW)
    assert "Preferences: in Bangladesh ✓" in text
    assert "unknown" not in text
    why = renderer.why(job, scorer.score(job, _user()), _user())
    assert "“Experience • 0-1 years”" in why


def test_alert_ages_and_missing_parts(config: Config, scorer: Scorer) -> None:
    renderer = Renderer.from_config(config)
    today = _job(posted_at=NOW, deadline=None, company=None, location_raw=None, city=None)
    text = renderer.alert(today, scorer.score(today, _user()), _user(), NOW)
    assert "📅 Posted today" in text
    yesterday = _job(posted_at=NOW - timedelta(days=1))
    assert "yesterday" in renderer.alert(yesterday, scorer.score(yesterday, _user()), _user(), NOW)


# --- matching end to end -------------------------------------------------------


@pytest.fixture
async def db(repo_root: Path) -> AsyncIterator[SqliteBackend]:
    backend = SqliteBackend(":memory:")
    await apply_migrations(backend, repo_root / "migrations")
    yield backend
    await backend.aclose()


async def _seed(db: SqliteBackend) -> None:
    gates = {
        "needs_facts": [],
        "location": {"status": "pass", "reason": "bangladesh", "evidence": "Dhaka"},
    }
    await db.execute(
        "INSERT INTO jobs (id, source, source_job_id, url, canonical_url, fingerprint, title,"
        " company, location_raw, posted_at, posted_at_source, first_seen_at, last_seen_at,"
        " gates_json) VALUES (1, 'bdjobs', '1', 'https://x', 'https://x', 'fp',"
        " 'Junior Machine Learning Engineer', 'Acme', 'Dhaka', '2026-10-17T00:00:00Z', 'api',"
        " 'x', 'x', ?)",
        (json.dumps(gates),),
    )
    await db.execute("INSERT INTO job_domains VALUES (1, 'ai-ml', 1.0, 'exact', 1)")
    await db.execute(
        "INSERT INTO job_facts (job_id, facts_json, provider, model, prompt_version, input_hash,"
        " grounding_ok) VALUES (1, ?, 'groq', 'm', 'extraction.v1', 'h', 1)",
        (_facts().model_dump_json(),),
    )
    for user_id, tracks in ((1, ["ai_ml"]), (2, ["cybersecurity"])):
        await db.execute(
            "INSERT INTO users (id, chat_id, locale) VALUES (?, ?, 'en')", (user_id, user_id)
        )
        await db.execute(
            "INSERT INTO user_prefs (user_id, tracks_json, include_adjacent, experience_level,"
            " locations_json, job_types_json, min_score, delivery_mode, max_per_cycle)"
            " VALUES (?, ?, 1, 'fresh_grad', '[\"bangladesh\"]', '[\"full_time\"]', 6.0,"
            " 'instant', 5)",
            (user_id, json.dumps(tracks)),
        )
    await db.execute("INSERT INTO user_profiles (user_id, skills_json) VALUES (1, '[\"python\"]')")


@pytest.mark.anyio
async def test_match_stores_passing_pairs_once(db: SqliteBackend, config: Config) -> None:
    await _seed(db)
    new, counts = await match(db, config, NOW)
    assert [(m.user_id, m.job_id) for m in new] == [(1, 1)]
    assert counts["not matched: tracks:outside_chosen_tracks"] == 1
    row = (await db.query("SELECT * FROM matches"))[0]
    assert row["scorer_version"] == "scorer.v2"
    breakdown = json.loads(str(row["breakdown_json"]))
    assert breakdown["score"] == str(new[0].score)
    assert breakdown["quick"] is False
    assert json.loads(str(row["explanation_json"]))["experience"]["evidence"] == "0-1 years"
    again, _ = await match(db, config, NOW)
    assert again == []


@pytest.mark.anyio
async def test_match_skips_jobs_waiting_for_facts(db: SqliteBackend, config: Config) -> None:
    await _seed(db)
    await db.execute(
        "UPDATE jobs SET gates_json = json_set(gates_json, '$.needs_facts', json('[\"location\"]'))"
    )
    new, _ = await match(db, config, NOW)
    assert new == []


def test_scoring_config_is_consistent(config: Config) -> None:
    data = config.scoring.model_dump()
    data["weights"]["role"] = 2.0
    with pytest.raises(ValueError, match="sum to 10"):
        type(config.scoring).model_validate(data)
    assert replace(_job(), quick=True).quick


def test_locale_covers_everything_the_renderer_uses(config: Config) -> None:
    from typing import get_args

    from jobhunt.extract.schema import EmploymentType, WorkMode
    from jobhunt.score.render import Locale

    locale = Locale.load(config.root)
    assert {label.key for label in config.scoring.labels} <= set(locale.text("labels"))
    assert set(get_args(EmploymentType)) <= set(locale.text("employment"))
    assert set(get_args(WorkMode)) <= set(locale.text("work_mode"))
    assert set(config.app.seniority_gate.default_years) <= set(locale.text("levels"))
    rules = {
        "role.primary", "role.secondary_or_include", "role.adjacent", "skills.coverage",
        "skills.none_listed", "experience.met", "experience.gap", "experience.unknown",
        "education.accepted", "education.broad", "education.other", "preference.sum",
    }  # fmt: skip
    assert rules <= set(locale.text("component").keys())


def test_each_title_is_normalized_once_per_scorer(
    config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jobhunt.normalize.titles import TitleNormalizer
    from jobhunt.score.bench import bench, synthetic

    views, profiles = synthetic(config, users=20, jobs=15, seed=3)
    scorer = Scorer(config)
    calls: list[str] = []
    real = TitleNormalizer.normalize

    def counting(self: TitleNormalizer, title: str) -> Any:
        calls.append(title)
        return real(self, title)

    monkeypatch.setattr(TitleNormalizer, "normalize", counting)
    for job in views:
        for user in profiles:
            scorer.score(job, user)
    assert len(calls) == len({job.title for job in views})
    assert bench(config, users=4, jobs=5).pairs == 20
