import shutil
import pytest
from src.models import TailoredCV, TailoredExperience, TailoredProject, TailoredCoverLetter
from src.render import _latex_escape, _make_jinja_env, render_cv, render_cover_letter

SAMPLE_PROFILE = {
    "personal": {
        "name": "Test User",
        "email": "test@example.com",
        "phone": "+880-1XXX",
        "location": "Dhaka, Bangladesh",
        "linkedin": "linkedin.com/in/testuser",
        "github": "github.com/testuser",
    },
    "education": [{"institution": "Test Uni", "degree": "BSc CS", "dates": "2017–2021"}],
}

SAMPLE_CV = TailoredCV(
    headline="Python Backend Engineer | Django & FastAPI | 2 Years",
    selected_summary="Backend engineer with 2 years of Python experience building APIs.",
    experience=[
        TailoredExperience(
            company="Sample & Co",
            role="Software Engineer",
            dates="Jan 2023 – Present",
            location="Dhaka",
            selected_bullets=[
                "Built REST API serving 50k requests/day with 99.9% uptime",
                "Reduced query latency by 40% with PostgreSQL indexing",
            ],
        )
    ],
    projects=[
        TailoredProject(
            name="Open Source Tool",
            url="github.com/test/tool",
            selected_bullets=["Automated data pipeline processing 10k records/day"],
        )
    ],
    selected_skills={
        "languages": ["Python", "JavaScript"],
        "frameworks": ["Django", "FastAPI"],
        "tools": ["Docker", "AWS"],
        "databases": ["PostgreSQL"],
    },
    education=[{"institution": "Test Uni", "degree": "BSc CS", "dates": "2017–2021"}],
)

SAMPLE_CL = TailoredCoverLetter(
    greeting="Dear Hiring Manager,",
    body="I am excited to apply for the Python Developer role. My 2 years of FastAPI experience aligns well with your requirements.",
    closing="Best regards,\nTest User",
)


# ──────────────────────────────────────────────────────────────────
# latex_escape

def test_latex_escape_ampersand():
    assert _latex_escape("A & B") == r"A \& B"


def test_latex_escape_percent():
    assert _latex_escape("100%") == r"100\%"


def test_latex_escape_underscore():
    assert _latex_escape("some_var") == r"some\_var"


def test_latex_escape_multiple():
    result = _latex_escape("$10 & 50% off")
    assert r"\$" in result
    assert r"\&" in result
    assert r"\%" in result


# ──────────────────────────────────────────────────────────────────
# Template rendering

def test_cv_template_renders():
    env = _make_jinja_env()
    tmpl = env.get_template("cv.tex.j2")
    tex = tmpl.render(tailored=SAMPLE_CV, personal=SAMPLE_PROFILE["personal"])
    assert "Test User" in tex
    assert "Python Backend Engineer" in tex
    assert r"Sample \& Co" in tex  # & should be escaped
    assert r"\begin{document}" in tex


def test_cover_letter_template_renders():
    env = _make_jinja_env()
    tmpl = env.get_template("cover_letter.tex.j2")
    tex = tmpl.render(
        letter=SAMPLE_CL,
        personal=SAMPLE_PROFILE["personal"],
        job_title="Python Developer",
        company="Test Company",
    )
    assert "Test User" in tex
    assert "Dear Hiring Manager" in tex
    assert "Python Developer" in tex


# ──────────────────────────────────────────────────────────────────
# PDF generation (only if pdflatex is available)

@pytest.mark.skipif(not shutil.which("pdflatex"), reason="pdflatex not installed")
def test_render_cv_produces_pdf(tmp_path):
    path = render_cv(SAMPLE_CV, SAMPLE_PROFILE, str(tmp_path))
    import os
    assert os.path.exists(path)
    assert os.path.getsize(path) > 0


@pytest.mark.skipif(not shutil.which("pdflatex"), reason="pdflatex not installed")
def test_render_cover_letter_produces_pdf(tmp_path):
    path = render_cover_letter(SAMPLE_CL, SAMPLE_PROFILE, str(tmp_path), "Python Dev", "Test Co")
    import os
    assert os.path.exists(path)
    assert os.path.getsize(path) > 0
