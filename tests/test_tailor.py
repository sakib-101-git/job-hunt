import json
import pytest
from unittest.mock import MagicMock, patch
from src.models import ScoredJob, TailoredCV, TailoredCoverLetter
from src.tailor import tailor_cv, tailor_cover_letter, _parse_json


SAMPLE_JD = "Looking for a Python backend developer with Django, PostgreSQL, 2+ years."

SAMPLE_PROFILE = {
    "personal": {"name": "Test User", "email": "test@example.com", "phone": "+880"},
    "summary_pool": [
        {"text": "Backend engineer with 2 years Python experience.", "tags": ["python"]},
    ],
    "experience": [
        {
            "company": "Test Co",
            "role": "Software Engineer",
            "dates": "2023 – Present",
            "location": "Dhaka",
            "bullets": [
                {"text": "Built REST API with FastAPI", "tags": ["python", "fastapi"]},
                {"text": "Managed PostgreSQL schema", "tags": ["sql", "postgres"]},
            ],
        }
    ],
    "projects": [
        {
            "name": "Sample App",
            "url": "github.com/test/sample",
            "bullets": [{"text": "Django CRUD app", "tags": ["python", "django"]}],
        }
    ],
    "skills": {
        "languages": [{"name": "Python"}],
        "tools": ["Docker"],
    },
    "education": [{"institution": "Test Uni", "degree": "BSc CS", "dates": "2017–2021"}],
    "certifications": [],
}

CV_RESPONSE = {
    "headline": "Python Backend Engineer | Django | 2 Years",
    "selected_summary": "Backend engineer with 2 years Python experience.",
    "experience": [
        {
            "company": "Test Co",
            "role": "Software Engineer",
            "dates": "2023 – Present",
            "location": "Dhaka",
            "selected_bullets": ["Built REST API with FastAPI"],
        }
    ],
    "projects": [
        {"name": "Sample App", "url": "github.com/test/sample", "selected_bullets": ["Django CRUD app"]}
    ],
    "selected_skills": {"languages": ["Python"], "frameworks": ["Django"], "tools": ["Docker"], "databases": ["PostgreSQL"]},
    "education": [],
    "certifications": [],
}

CL_RESPONSE = {
    "greeting": "Dear Hiring Manager,",
    "body": "I am applying for the Python Developer role. I have 2 years of experience.",
    "closing": "Best regards,\nTest User",
}


def _make_job():
    return ScoredJob(
        source="bdjobs", source_job_id="001", title="Python Developer",
        company="Test Co", location="Dhaka", url="https://example.com",
        jd_text=SAMPLE_JD, fit_score=8, fit_reason="Good match", db_id=1,
    )


def _make_config():
    cfg = MagicMock()
    cfg.groq_api_key = "fake"
    cfg.llm_model = "llama-3.3-70b-versatile"
    cfg.max_tokens_tailoring = 2000
    return cfg


@patch("src.tailor.Groq")
def test_tailor_cv_returns_model(mock_groq):
    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock(message=MagicMock(content=json.dumps(CV_RESPONSE)))]
    mock_groq.return_value.chat.completions.create.return_value = mock_resp

    result = tailor_cv(_make_job(), SAMPLE_PROFILE, _make_config())
    assert isinstance(result, TailoredCV)
    assert result.headline == CV_RESPONSE["headline"]
    assert len(result.experience) == 1
    assert result.experience[0].company == "Test Co"


@patch("src.tailor.Groq")
def test_tailor_cover_letter_returns_model(mock_groq):
    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock(message=MagicMock(content=json.dumps(CL_RESPONSE)))]
    mock_groq.return_value.chat.completions.create.return_value = mock_resp

    result = tailor_cover_letter(_make_job(), SAMPLE_PROFILE, _make_config())
    assert isinstance(result, TailoredCoverLetter)
    assert "applying" in result.body.lower()


def test_parse_json_strips_code_fences():
    raw = '```json\n{"key": "value"}\n```'
    result = _parse_json(raw, MagicMock(), "test-model")
    assert result == {"key": "value"}


def test_parse_json_valid_json():
    raw = '{"score": 7, "reason": "good"}'
    result = _parse_json(raw, MagicMock(), "test-model")
    assert result["score"] == 7


@patch("src.tailor.Groq")
def test_tailor_cv_education_passthrough(mock_groq):
    """If LLM returns empty education, it falls back to profile's education."""
    response = dict(CV_RESPONSE, education=[])
    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock(message=MagicMock(content=json.dumps(response)))]
    mock_groq.return_value.chat.completions.create.return_value = mock_resp

    result = tailor_cv(_make_job(), SAMPLE_PROFILE, _make_config())
    assert len(result.education) > 0
    assert result.education[0]["institution"] == "Test Uni"
