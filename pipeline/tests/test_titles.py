import pytest
from hypothesis import given
from hypothesis import strategies as st

from jobhunt.config import Config
from jobhunt.normalize.titles import NormalizedTitle, TitleNormalizer


@pytest.fixture(scope="module")
def normalizer(config: Config) -> TitleNormalizer:
    return TitleNormalizer(config.title_rules, config.locations.place_names())


@pytest.mark.parametrize(
    ("raw", "text", "seniority"),
    [
        # The owner's messy BD examples.
        ("Executive - IT (Software)", "executive it software", None),
        ("Sr. Officer, MIS", "executive mis", "senior"),
        ("Jr. SQA Engr.", "software quality assurance developer", "junior"),
        # Location, work-mode and job-code segments are dropped.
        ("Senior Software Engineer - Dhaka (Remote, Full-time)", "software developer", "senior"),
        ("Software Engineer | Chattogram", "software developer", None),
        ("Full-Stack Developer (MERN) - REQ-20391", "fullstack developer mern", None),
        ("Backend Engineer [Job ID: 5512]", "backend developer", None),
        # Tech names keep their punctuation.
        ("Node.js Developer", "node.js developer", None),
        ("ASP.NET Core Developer", "asp.net core developer", None),
        (".NET Developer", ".net developer", None),
        ("C++ Engineer", "c++ developer", None),
        ("C# Programmer", "c# developer", None),
        ("ReactJS Developer", "react developer", None),
        # Abbreviations, split spellings, stopwords.
        ("Front-End Dev", "frontend developer", None),
        ("Hardware & Networking Engineer", "hardware networking developer", None),
        ("Lecturer, Dept. of Computer Science", "lecturer cse", None),
        ("Pre-Sales Engineer", "presales developer", None),
        # An abbreviation beside its spelled-out form is not doubled.
        ("Software Development Engineer (SDE)", "software development developer", None),
        # Seniority markers, including numbered levels.
        ("Software Engineer I, II, III", "software developer", "senior"),
        ("Level 2 Support Engineer", "support developer", "mid"),
        ("Developer L3", "developer", "senior"),
        ("Trainee Software Engineer (Intern)", "software developer", "entry"),
        ("Head of Engineering", "engineering", "lead_plus"),
        # A title that is only a seniority word keeps it.
        ("Intern", "intern", None),
        # "Remote" inside a real title is not noise.
        ("Remote Sensing Software Engineer", "remote sensing software developer", None),
    ],
)
def test_normalize(normalizer: TitleNormalizer, raw: str, text: str, seniority: str | None) -> None:
    result = normalizer.normalize(raw)
    assert result.text == text
    assert result.seniority == seniority


def test_key_ignores_word_order(normalizer: TitleNormalizer) -> None:
    assert normalizer.normalize("Executive - IT").key == normalizer.normalize("IT Executive").key


def test_key_keeps_repeated_words() -> None:
    once = NormalizedTitle(("infrastructure", "developer"), None)
    twice = NormalizedTitle(("developer", "infrastructure", "developer"), None)
    assert once.key != twice.key


def test_segment_of_only_noise_is_kept_when_nothing_else_is_left(
    normalizer: TitleNormalizer,
) -> None:
    assert normalizer.normalize("Remote").text == "remote"


def test_fold(normalizer: TitleNormalizer) -> None:
    assert normalizer.fold("engineer") == "developer"
    assert normalizer.fold("python") == "python"
    assert "programmer" in normalizer.variant_words


def test_multi_word_equivalent_is_rejected(config: Config) -> None:
    rules = config.title_rules.model_copy(update={"equivalents": {"developer": ["web dev"]}})
    with pytest.raises(ValueError, match="one word"):
        TitleNormalizer(rules)


@given(st.text(max_size=80))
def test_normalize_never_fails(normalizer: TitleNormalizer, raw: str) -> None:
    result = normalizer.normalize(raw)
    assert all(token and " " not in token for token in result.tokens)
