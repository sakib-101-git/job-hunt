from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml

from jobhunt.config import Config
from jobhunt.config.models import ExperienceLevel, Seniority
from jobhunt.gates.base import PhraseFinder
from jobhunt.gates.cse import CseGate
from jobhunt.gates.freshness import FreshnessGate
from jobhunt.gates.location import LocationGate
from jobhunt.gates.seniority import seniority_gate
from jobhunt.sources.models import RawListing
from jobhunt.taxonomy.matcher import TitleMatcher

NOW = datetime(2026, 10, 18, 9, 0, tzinfo=UTC)


def _listing(**fields: Any) -> RawListing:
    base: dict[str, Any] = {
        "source": "remoteok",
        "source_job_id": "1",
        "url": "https://x",
        "title": "Software Engineer",
        "description": "d",
        "description_format": "text",
    }
    base.update(fields)
    return RawListing(**base)


# --- location -----------------------------------------------------------------

LOCATION_CASES = yaml.safe_load(
    (Path(__file__).parent / "data" / "location_cases.yaml").read_text(encoding="utf-8")
)


def test_enough_location_cases() -> None:
    assert len(LOCATION_CASES) >= 60


@pytest.mark.parametrize(
    ("fields", "expect"),
    [(case["listing"], case["expect"]) for case in LOCATION_CASES],
    ids=[case["case"] for case in LOCATION_CASES],
)
def test_location_gate(config: Config, fields: dict[str, Any], expect: str) -> None:
    status, reason = expect.split()
    if "location_restrictions" in fields:
        fields = {**fields, "location_restrictions": tuple(fields["location_restrictions"])}
    result = LocationGate(config)(_listing(**fields))
    assert (result.status, result.reason) == (status, reason), result
    if status == "pass":
        assert result.evidence


def test_location_evidence_and_timezone_warnings(config: Config) -> None:
    result = LocationGate(config)(_listing(location_restrictions=("REMOTE (anywhere, EST hours)",)))
    assert result.evidence == "REMOTE (anywhere, EST hours)"
    assert result.warnings == ("EST hours",)


def test_location_policies(config: Config) -> None:
    policy = config.app.location_policy.model_copy(
        update={"accept_unspecified_remote": True, "accept_region_lists_containing_bd": True}
    )
    lenient = config.__class__(
        **{**config.__dict__, "app": config.app.model_copy(update={"location_policy": policy})}
    )
    gate = LocationGate(lenient)
    asia = gate(_listing(location_restrictions=("Home Based - APAC",)))
    assert (asia.status, asia.reason) == ("pass", "region_with_bangladesh")
    remote = gate(_listing(location_restrictions=("Remote",)))
    assert (remote.status, remote.reason) == ("pass", "unspecified_remote_accepted")


def test_phrase_finder_case_rules() -> None:
    finder = PhraseFinder(["US", "United States", "anywhere"])
    assert finder.find("Remote - US") == "US"
    assert finder.find("contact us") is None
    assert finder.find("ANYWHERE") == "ANYWHERE"
    assert finder.find_all("US or United States") == ["US", "United States"]
    assert PhraseFinder([]).find("x") is None


def test_phrase_finder_whole_words_for_every_phrase() -> None:
    # With three or more phrases per case class, every one of them, not only the
    # first and last, must match whole words only (found by the golden set:
    # "ITSM" read as "TS", "restore" as "rest").
    finder = PhraseFinder(["TypeScript", "REST API", "REST", "RESTful", "TS", "EU", "US"])
    assert finder.find_all("an ITSM tool; backup and restore; FOCUS on EUR") == []
    assert finder.find_all("REST and TS, EU or US") == ["REST", "TS", "EU", "US"]


def test_phrase_finder_exact_phrases() -> None:
    finder = PhraseFinder(["REST", "Python"], exact=["REST"])
    assert finder.find_all("the rest of the team uses python and REST") == ["python", "REST"]


# --- CSE -----------------------------------------------------------------------


@pytest.fixture(scope="module")
def cse(config: Config) -> CseGate:
    return CseGate(config)


@pytest.mark.parametrize(
    ("title", "status", "reason"),
    [
        ("Software Engineer", "pass", "taxonomy"),
        ("Jr. SQA Engr.", "pass", "taxonomy"),
        ("Senior Civil Engineer", "fail", "non_cse_title"),
        ("Data Entry Operator", "fail", "non_cse_title"),
        ("IT Sales Executive", "fail", "non_cse_title"),
        ("Industrial Engineer", "fail", "non_cse_title"),
        ("Graphic Designer & Video Editor", "fail", "non_cse_title"),
        ("Ubuntu Sales Engineer (Entry-Level)", "needs_facts", "ambiguous_exclusion"),
        ("Software Sales Engineer", "pass", "taxonomy"),
        ("Customer Support Specialist", "fail", "no_tech_signal"),
        ("Head of Technology", "needs_facts", "title_not_in_taxonomy"),
        ("SAP RE FX Consultant", "needs_facts", "title_not_in_taxonomy"),
    ],
)
def test_cse_gate(
    cse: CseGate, matcher: TitleMatcher, title: str, status: str, reason: str
) -> None:
    result = cse(title, matcher.match(title))
    assert (result.status, result.reason) == (status, reason), result


def test_cse_gate_with_extraction(cse: CseGate, matcher: TitleMatcher) -> None:
    title = "Head of Technology"
    yes = cse(title, matcher.match(title), llm_is_cse=True, llm_evidence="build our platform")
    assert (yes.status, yes.reason, yes.evidence) == ("pass", "extraction", "build our platform")
    no = cse(title, matcher.match(title), llm_is_cse=False, llm_evidence="sell")
    assert (no.status, no.reason) == ("fail", "not_cse_per_extraction")
    ubuntu = "Ubuntu Sales Engineer"
    assert cse(ubuntu, matcher.match(ubuntu), llm_is_cse=True, llm_evidence="q").status == "pass"
    assert cse(ubuntu, matcher.match(ubuntu), llm_is_cse=False).status == "fail"


# --- freshness -----------------------------------------------------------------


@pytest.fixture(scope="module")
def fresh(config: Config) -> FreshnessGate:
    return FreshnessGate(config)


def test_exactly_max_age_is_fresh(fresh: FreshnessGate, config: Config) -> None:
    limit = timedelta(days=config.app.freshness.max_age_days)
    edge = fresh(now=NOW, posted_at=NOW - limit, deadline=None, first_seen_at=NOW)
    assert (edge.status, edge.reason) == ("pass", "posted_recently")
    late = fresh(
        now=NOW, posted_at=NOW - limit - timedelta(seconds=1), deadline=None, first_seen_at=NOW
    )
    assert (late.status, late.reason) == ("fail", "too_old")


def test_deadline_is_judged_in_dhaka_time(fresh: FreshnessGate) -> None:
    # 2026-10-18 19:00 UTC is already 2026-10-19 01:00 in Dhaka.
    evening = datetime(2026, 10, 18, 19, 0, tzinfo=UTC)
    posted = evening - timedelta(days=1)
    result = fresh(now=evening, posted_at=posted, deadline=date(2026, 10, 18), first_seen_at=posted)
    assert (result.status, result.reason) == ("fail", "deadline_passed")
    morning = datetime(2026, 10, 18, 17, 0, tzinfo=UTC)  # 23:00 in Dhaka, same day
    ok = fresh(now=morning, posted_at=posted, deadline=date(2026, 10, 18), first_seen_at=posted)
    assert ok.status == "pass"


def test_no_posting_date(fresh: FreshnessGate) -> None:
    future = date(2026, 11, 1)
    with_deadline = fresh(now=NOW, posted_at=None, deadline=future, first_seen_at=NOW)
    assert (with_deadline.status, with_deadline.reason) == ("pass", "future_deadline")
    seeded = fresh(now=NOW, posted_at=None, deadline=future, first_seen_at=NOW, bootstrap=True)
    assert seeded.status == "needs_facts"
    seeded_checked = fresh(
        now=NOW,
        posted_at=None,
        deadline=future,
        first_seen_at=NOW,
        bootstrap=True,
        facts_checked=True,
    )
    assert (seeded_checked.status, seeded_checked.reason) == ("fail", "bootstrap_without_date")
    old_sighting = fresh(
        now=NOW, posted_at=None, deadline=future, first_seen_at=NOW - timedelta(days=20)
    )
    assert old_sighting.status == "needs_facts"
    nothing = fresh(now=NOW, posted_at=None, deadline=None, first_seen_at=NOW, facts_checked=True)
    assert (nothing.status, nothing.reason) == ("fail", "no_posting_date")


# --- seniority (per user) ------------------------------------------------------


@pytest.mark.parametrize(
    ("level", "years", "title_level", "min_years", "status", "reason"),
    [
        ("fresh_grad", None, None, 1, "pass", "within_tolerance"),
        ("fresh_grad", None, None, 3, "fail", "too_much_experience"),
        ("fresh_grad", 1.0, None, 3, "pass", "within_tolerance"),
        ("fresh_grad", None, "senior", None, "fail", "title_too_senior"),
        ("fresh_grad", None, "junior", None, "pass", "within_tolerance"),
        ("student_intern", None, "mid", None, "fail", "title_too_senior"),
        ("student_intern", None, None, 2, "fail", "too_much_experience"),
        ("junior", None, "senior", 3, "pass", "within_tolerance"),
        ("junior", None, None, 4, "fail", "too_much_experience"),
        ("junior", None, "lead_plus", None, "fail", "title_too_senior"),
        ("senior", None, "lead_plus", 15, "pass", "within_tolerance"),
        ("mid", None, None, None, "pass", "within_tolerance"),
    ],
)
def test_seniority_gate(
    config: Config,
    level: ExperienceLevel,
    years: float | None,
    title_level: Seniority | None,
    min_years: int | None,
    status: str,
    reason: str,
) -> None:
    result = seniority_gate(config.app.seniority_gate, level, years, title_level, min_years)
    assert (result.status, result.reason) == (status, reason)
