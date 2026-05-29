import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from src.models import ScrapedJob
from src.filters import hard_filter, score_fit


def _make_config(**overrides):
    cfg = MagicMock()
    cfg.search_keywords = ["python developer", "software engineer", "backend developer"]
    cfg.search_locations = ["dhaka", "remote", "bangladesh"]
    cfg.exclude_keywords = ["principal", "director"]
    cfg.exclude_companies = ["scam corp ltd"]
    cfg.max_age_hours = 72
    cfg.min_fit_score = 6
    cfg.scoring_model = "llama-3.1-8b-instant"
    cfg.max_tokens_scoring = 300
    cfg.groq_api_key = "fake"
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


def _make_job(**overrides):
    defaults = dict(
        source="bdjobs",
        source_job_id="001",
        title="Python Developer",
        company="Good Company",
        location="Dhaka, Bangladesh",
        url="https://example.com/job/001",
        jd_text="We need a Python developer with 2+ years experience in Django.",
        posted_date=datetime.now(timezone.utc) - timedelta(hours=5),
    )
    defaults.update(overrides)
    return ScrapedJob(**defaults)


# ──────────────────────────────────────────────────────────────────
# hard_filter tests

def test_passes_all_criteria():
    assert hard_filter(_make_job(), _make_config()) is True


def test_fails_no_keyword_match():
    job = _make_job(title="HR Manager")
    assert hard_filter(job, _make_config()) is False


def test_fails_excluded_keyword_in_title():
    job = _make_job(title="Principal Software Engineer")
    assert hard_filter(job, _make_config()) is False


def test_fails_excluded_company():
    job = _make_job(company="Scam Corp Ltd")
    assert hard_filter(job, _make_config()) is False


def test_fails_wrong_location():
    job = _make_job(location="New York, USA")
    assert hard_filter(job, _make_config()) is False


def test_passes_empty_location():
    # If location field is empty, don't filter it out
    job = _make_job(location="")
    assert hard_filter(job, _make_config()) is True


def test_fails_too_old():
    job = _make_job(posted_date=datetime.now(timezone.utc) - timedelta(hours=100))
    assert hard_filter(job, _make_config()) is False


def test_passes_no_posted_date():
    job = _make_job(posted_date=None)
    assert hard_filter(job, _make_config()) is True


# ──────────────────────────────────────────────────────────────────
# score_fit tests

@patch("src.filters.Groq")
def test_score_fit_returns_tuple(mock_groq):
    mock_choice = MagicMock()
    mock_choice.choices = [MagicMock(message=MagicMock(content='{"score": 7, "reason": "Good Python match"}'))]
    mock_groq.return_value.chat.completions.create.return_value = mock_choice

    job = _make_job()
    profile = {
        "summary_pool": [{"text": "Python backend engineer", "tags": ["python"]}],
        "skills": {"languages": [{"name": "Python"}], "tools": []},
        "experience": [],
    }
    score, reason = score_fit(job, profile, _make_config())
    assert isinstance(score, int)
    assert 0 <= score <= 10
    assert isinstance(reason, str)


def test_score_fit_short_jd_returns_5():
    job = _make_job(jd_text="short")
    score, reason = score_fit(job, {}, _make_config())
    assert score == 5
    assert "short" in reason.lower()


@patch("src.filters.Groq")
def test_score_fit_api_error_returns_5(mock_groq):
    mock_groq.return_value.chat.completions.create.side_effect = Exception("API down")
    job = _make_job()
    score, reason = score_fit(job, {}, _make_config())
    assert score == 5
