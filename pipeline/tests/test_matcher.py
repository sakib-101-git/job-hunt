from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from jobhunt.config import Config
from jobhunt.taxonomy.matcher import TitleMatcher

DATA = Path(__file__).parent / "data"

# R1: at least 95 % of real-world titles get a correct primary domain from
# exact, alias and fuzzy matching alone. The rest fall through to the LLM
# classifier (a later stage); wrong-domain answers must stay rare.
MIN_ACCURACY = 0.95
MAX_WRONG_DOMAIN = 0.02


def _lines(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.startswith("#")]


def _title_cases() -> list[tuple[str, list[str]]]:
    cases = []
    for line in _lines(DATA / "title_cases.txt"):
        title, domains = line.split("||")
        cases.append((title.strip(), domains.split()))
    return cases


TITLE_CASES = _title_cases()
NON_CSE_TITLES = _lines(DATA / "non_cse_titles.txt")


def test_case_files_are_big_enough_and_valid(config: Config) -> None:
    assert len(TITLE_CASES) >= 300
    assert len({title for title, _ in TITLE_CASES}) == len(TITLE_CASES), "duplicate titles"
    for title, domains in TITLE_CASES:
        assert domains, title
        assert set(domains) <= config.taxonomy.domain_slugs, title


def test_title_accuracy(matcher: TitleMatcher) -> None:
    results = [(title, domains, matcher.match(title)) for title, domains in TITLE_CASES]
    wrong = [(t, d, r) for t, d, r in results if r.primary_domain and r.primary_domain not in d]
    unmatched = [t for t, _, r in results if r.primary_domain is None]
    accuracy = 1 - (len(wrong) + len(unmatched)) / len(results)
    report = "\n".join(
        [f"wrong: {t!r} -> {r.primary_domain} (want {d})" for t, d, r in wrong]
        + [f"unmatched: {t!r}" for t in unmatched]
    )
    assert accuracy >= MIN_ACCURACY, f"accuracy {accuracy:.1%}\n{report}"
    assert len(wrong) / len(results) <= MAX_WRONG_DOMAIN, report


def test_non_cse_titles_get_no_domain(matcher: TitleMatcher, config: Config) -> None:
    # A few non-CSE titles look like CSE ones to the matcher; the CSE gate rejects
    # them through non_cse_exclusions, so they must be on that list.
    excluded = {title.casefold() for title in config.taxonomy.non_cse_exclusions}
    leaks = [
        (title, result.primary_domain)
        for title in NON_CSE_TITLES
        if (result := matcher.match(title)).primary_domain and title.casefold() not in excluded
    ]
    assert leaks == []


@pytest.mark.parametrize(
    ("title", "domain"),
    [
        ("Executive - IT (Software)", "it-infrastructure"),
        ("Sr. Officer, MIS", "it-infrastructure"),
        ("Jr. SQA Engr.", "qa-testing"),
    ],
)
def test_owner_examples(matcher: TitleMatcher, title: str, domain: str) -> None:
    assert matcher.match(title).primary_domain == domain


def test_exact_match_reports_title_and_seniority(matcher: TitleMatcher) -> None:
    result = matcher.match("Senior Machine Learning Engineer")
    assert (result.method, result.primary_domain) == ("exact", "ai-ml")
    assert result.matched_title == "Machine Learning Engineer"
    assert result.seniority == "senior"
    assert result.confidence == 1.0


def test_title_in_several_domains_gets_secondaries(matcher: TitleMatcher) -> None:
    result = matcher.match("Flutter Developer")
    assert result.primary_domain == "mobile-desktop"
    assert "language-specialists" in result.secondary_domains


def test_alias_drops_dash_qualifier(matcher: TitleMatcher) -> None:
    result = matcher.match("NOC Engineer")
    assert result.method == "alias"
    assert result.matched_title == "NOC Engineer — Network Operations"


def test_typos_are_repaired(matcher: TitleMatcher) -> None:
    result = matcher.match("Sofware Enginer")
    assert (result.method, result.primary_domain) == ("fuzzy", "software-engineering")


def test_extra_stack_words_still_match(matcher: TitleMatcher) -> None:
    assert matcher.match("Sr. PHP Laravel Developer").primary_domain == "language-specialists"


def test_role_noun_must_match(matcher: TitleMatcher) -> None:
    assert matcher.match("Graphic Designer").method == "none"


def test_unknown_bracket_words_block_the_shorter_title(matcher: TitleMatcher) -> None:
    assert matcher.match("Production Engineer (Garments)").method == "none"
    assert matcher.match("Site Engineer (Civil)").method == "none"


def test_seniority_ladder_is_a_last_resort(matcher: TitleMatcher) -> None:
    # "Mobile Engineer" equals "Lead Mobile Engineer" (a ladder title) once
    # seniority is stripped, but a functional domain wins when one matches.
    assert matcher.match("Mobile Engineer").primary_domain == "mobile-desktop"
    # "Tech Lead" has no functional title to fall back on.
    result = matcher.match("Tech Lead")
    assert (result.primary_domain, result.seniority) == ("seniority-ladder", "lead_plus")
    assert result.secondary_domains == ()


def test_empty_title(matcher: TitleMatcher) -> None:
    result = matcher.match("  -  ")
    assert (result.method, result.primary_domain, result.confidence) == ("none", None, 0.0)


@settings(max_examples=50, deadline=None)
@given(st.text(max_size=60))
def test_match_never_fails(matcher: TitleMatcher, title: str) -> None:
    result = matcher.match(title)
    assert 0.0 <= result.confidence <= 1.0
    assert (result.primary_domain is None) == (result.method == "none")
