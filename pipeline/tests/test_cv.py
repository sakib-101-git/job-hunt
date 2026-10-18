import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from test_extract import FakeLlm, completion
from test_notify import NOW, Clock, add_user

from jobhunt.config import Config
from jobhunt.cv.parse import CvEducation, CvFacts, CvParser, ground_cv, pdf_text
from jobhunt.db import SqliteBackend, apply_migrations
from jobhunt.extract.grounding import Grounder
from jobhunt.extract.llm import LlmClient
from jobhunt.notify.telegram import TelegramClient

pytestmark = pytest.mark.anyio

CV_LINES = [
    "Curriculum Vitae",
    "Skills: Python, Django, PostgreSQL, Docker, Kubernetes, Figma",
    "Experience",
    "Software Engineer, Acme Ltd, Jan 2024 - Dec 2025",
    "Built REST APIs in Django and Python for a logistics platform.",
    "Education",
    "BSc in Computer Science and Engineering, Chittagong University, 2023",
    "Projects: a chat app with WebSockets and Redis, deployed with Docker.",
]


def make_pdf(lines: list[str]) -> bytes:
    """A one-page PDF with `lines` as real text (what pypdf extracts)."""

    def esc(text: str) -> str:
        return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    stream = "BT /F1 11 Tf 14 TL 50 780 Td " + " ".join(f"({esc(x)}) Tj T*" for x in lines) + " ET"
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R"
        " /Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = b"%PDF-1.4\n"
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n{body}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n".encode()
    out += f"startxref\n{xref}\n%%EOF\n".encode()
    return out


def cv_facts(**changes: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "skills": ["Python", "Django", "PostgreSQL", "Docker", "Figma", "Rust"],
        "years_experience": 2,
        "years_evidence": "Software Engineer, Acme Ltd, Jan 2024 - Dec 2025",
        "education": {
            "level": "bachelor",
            "field": "Computer Science and Engineering",
            "evidence": "BSc in Computer Science and Engineering, Chittagong University, 2023",
        },
        "summary": "Backend engineer with two years of Django and Python work.",
    }
    base.update(changes)
    return base


@pytest.fixture
async def db(repo_root: Path) -> AsyncIterator[SqliteBackend]:
    backend = SqliteBackend(":memory:")
    await apply_migrations(backend, repo_root / "migrations")
    yield backend
    await backend.aclose()


def test_pdf_text() -> None:
    text = pdf_text(make_pdf(CV_LINES), 8)
    assert "Skills: Python, Django" in text
    assert "Chittagong University" in text
    assert pdf_text(b"not a pdf", 8) == ""
    assert pdf_text(make_pdf(CV_LINES), 0) == ""  # max_pages 0 reads nothing


def test_ground_cv(config: Config) -> None:
    grounder = Grounder(config.skills, config.llm.extraction.min_evidence_score)
    text = "\n".join(CV_LINES)
    profile = ground_cv(CvFacts.model_validate(cv_facts()), text, grounder, 300)
    assert "python" in profile.skills
    assert "django" in profile.skills
    assert "Rust" not in profile.skills  # the CV never says Rust
    assert profile.failures == ("skills:Rust",)
    assert profile.years == 2
    assert profile.education.level == "bachelor"

    made_up = cv_facts(
        years_evidence="Senior Engineer, 2015 - 2025",
        education={"level": "master", "field": "AI", "evidence": "MSc in AI, MIT"},
        summary="Contact me at someone@example.com",
    )
    profile = ground_cv(CvFacts.model_validate(made_up), text, grounder, 300)
    assert profile.years is None
    assert profile.education == CvEducation(level="unknown", field=None, evidence=None)
    assert profile.summary is None
    assert {"years_evidence", "education_evidence", "summary"} <= set(profile.failures)


class FakeBot:
    """Telegram's getFile, file download and sendMessage."""

    def __init__(self, pdf: bytes, file_ok: bool = True) -> None:
        self.pdf = pdf
        self.file_ok = file_ok
        self.sent: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/getFile"):
            if not self.file_ok:
                return httpx.Response(400, json={"ok": False, "description": "file not found"})
            result = {"file_id": "F", "file_path": "documents/cv.pdf", "file_size": len(self.pdf)}
            return httpx.Response(200, json={"ok": True, "result": result})
        if "/file/bot" in path:
            return httpx.Response(200, content=self.pdf)
        self.sent.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})


def _parser(
    db: SqliteBackend, config: Config, bot: FakeBot, llm: FakeLlm, environ: dict[str, str]
) -> CvParser:
    clock = Clock()
    telegram = TelegramClient(
        config.app.notifications,
        "T",
        httpx.AsyncClient(transport=httpx.MockTransport(bot)),
        clock,
        clock.sleep,
    )
    client = LlmClient(config.llm, environ, httpx.AsyncClient(transport=httpx.MockTransport(llm)))
    return CvParser(db, config, telegram, client)


async def _with_cv(db: SqliteBackend) -> None:
    await add_user(db, 1)
    await db.execute("UPDATE user_profiles SET cv_file_id = 'F', skills_json = '[\"sql\"]'")


async def test_parse_cv_saves_the_profile(db: SqliteBackend, config: Config) -> None:
    await _with_cv(db)
    bot, llm = FakeBot(make_pdf(CV_LINES)), FakeLlm(completion(cv_facts()))
    environ = {"GEMINI_API_KEY": "g", "GROQ_API_KEY": "q"}
    outcome = await _parser(db, config, bot, llm, environ).parse(1, NOW)
    assert outcome == "parsed"
    # The CV went to Groq only: Gemini's free tier may train on inputs.
    assert llm.requests[0].url.host == "api.groq.com"
    assert "Chittagong University" in llm.bodies[0]["messages"][1]["content"]
    row = (await db.query("SELECT * FROM user_profiles"))[0]
    skills = json.loads(str(row["skills_json"]))
    assert skills[:2] == ["python", "django"]
    assert skills[-1] == "sql"
    assert row["cv_file_id"] is None
    assert row["cv_parsed_at"] == "2026-10-18T06:00:00Z"
    assert row["years_experience"] == 2
    assert json.loads(str(row["education_json"]))["level"] == "bachelor"
    assert row["profile_version"] == 2
    message = bot.sent[0]
    assert "Python" in message["text"]
    assert "2 years" in message["text"]
    codes = config.app.callbacks
    buttons = [b["callback_data"] for b in message["reply_markup"]["inline_keyboard"][0]]
    assert buttons == [codes.cv_ok, codes.cv_edit]


@pytest.mark.parametrize(
    ("environ", "pdf", "file_ok", "answers", "outcome"),
    [
        ({"GEMINI_API_KEY": "g"}, make_pdf(CV_LINES), True, [], "no_model"),
        ({"GROQ_API_KEY": "q"}, make_pdf(CV_LINES), False, [], "download_failed"),
        ({"GROQ_API_KEY": "q"}, make_pdf(["Scan"]), True, [], "no_text"),
        (
            {"GROQ_API_KEY": "q"},
            make_pdf(CV_LINES),
            True,
            [httpx.Response(500)] * 3,
            "model_failed",
        ),
    ],
)
async def test_parse_cv_gives_up_politely(
    db: SqliteBackend,
    config: Config,
    environ: dict[str, str],
    pdf: bytes,
    file_ok: bool,
    answers: list[httpx.Response],
    outcome: str,
) -> None:
    await _with_cv(db)
    bot = FakeBot(pdf, file_ok)
    assert await _parser(db, config, bot, FakeLlm(*answers), environ).parse(1, NOW) == outcome
    row = (await db.query("SELECT cv_file_id, skills_json FROM user_profiles"))[0]
    assert row["cv_file_id"] is None
    assert row["skills_json"] == '["sql"]'
    assert len(bot.sent) == 1
    assert "reply_markup" not in bot.sent[0]


async def test_parse_cv_without_a_file_does_nothing(db: SqliteBackend, config: Config) -> None:
    await add_user(db, 1)
    bot = FakeBot(b"")
    assert await _parser(db, config, bot, FakeLlm(), {}).parse(1, NOW) == "no_cv"
    assert bot.sent == []
