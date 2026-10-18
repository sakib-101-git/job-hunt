import json
from collections.abc import AsyncIterator, Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from jobhunt.config import Config
from jobhunt.config.models import ChainEntry
from jobhunt.db import SqliteBackend, apply_migrations
from jobhunt.extract.budget import LlmBudget
from jobhunt.extract.extractor import Extractor, ModelCaller
from jobhunt.extract.grounding import Grounder, experience_years
from jobhunt.extract.llm import LlmClient, LlmError, Prompt, RateLimitedError
from jobhunt.extract.rules import RulesExtractor
from jobhunt.extract.schema import JobFacts, TitleLabels, strict_schema
from jobhunt.extract.titles import TitleClassifier
from jobhunt.gates.base import GateResult
from jobhunt.sources.models import RawListing
from jobhunt.taxonomy.matcher import TitleMatcher

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 10, 18, 9, 0, tzinfo=UTC)
KEYS = {"GEMINI_API_KEY": "g-key", "GROQ_API_KEY": "q-key"}
POSTING = (
    "We are hiring a Junior Python Developer in Dhaka. Requirements: BSc in CSE. "
    "At least 1 year of experience with Python and Django. Nice to have: Docker. "
    "Apply by 2026-11-01. This is an on-site role in Dhaka, Bangladesh."
)

Handler = Callable[[httpx.Request], httpx.Response]


def facts(**changes: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "is_cse_role": True,
        "is_cse_role_evidence": "Junior Python Developer",
        "primary_domain": "language-specialists",
        "secondary_domains": ["software-engineering"],
        "normalized_title": "Junior Python Developer",
        "seniority": "junior",
        "seniority_evidence": "Junior Python Developer",
        "min_years": 1,
        "max_years": None,
        "years_evidence": "At least 1 year of experience",
        "education": {"level": "bachelor", "fields": ["cse"], "required": True},
        "education_evidence": "BSc in CSE",
        "required_skills": ["Python", "Django"],
        "preferred_skills": ["Docker"],
        "employment_type": "full_time",
        "work_mode": "onsite",
        "country": "BD",
        "city": "Dhaka",
        "remote_eligibility": "not_remote",
        "remote_eligibility_evidence": "on-site role in Dhaka, Bangladesh",
        "timezone_requirement": None,
        "salary": None,
        "application_deadline": "2026-11-01",
        "deadline_evidence": "Apply by 2026-11-01",
        "posted_date_in_text": None,
        "language_requirements": [],
        "red_flags": [],
        "summary": "Build Django services.",
    }
    base.update(changes)
    return base


def completion(data: Any, tokens: tuple[int, int] = (100, 50)) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": json.dumps(data)}}],
            "usage": {"prompt_tokens": tokens[0], "completion_tokens": tokens[1]},
        },
    )


class FakeLlm:
    """Answers in order; records each request's model and body."""

    def __init__(self, *answers: httpx.Response | Callable[[dict[str, Any]], httpx.Response]):
        self.answers = list(answers)
        self.bodies: list[dict[str, Any]] = []
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.bodies.append(body)
        self.requests.append(request)
        answer = self.answers.pop(0)
        return answer(body) if callable(answer) else answer


@pytest.fixture
async def db(repo_root: Path) -> AsyncIterator[SqliteBackend]:
    backend = SqliteBackend(":memory:")
    await apply_migrations(backend, repo_root / "migrations")
    yield backend
    await backend.aclose()


class Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _client(config: Config, fake: Handler, environ: dict[str, str] | None = None) -> LlmClient:
    transport = httpx.MockTransport(fake)
    return LlmClient(
        config.llm, KEYS if environ is None else environ, httpx.AsyncClient(transport=transport)
    )


async def _caller(
    config: Config, db: SqliteBackend, fake: Handler, environ: dict[str, str] | None = None
) -> tuple[ModelCaller, LlmBudget]:
    client = _client(config, fake, environ)
    clock = Clock()
    budget = LlmBudget(config.llm, db, NOW, client.available, clock=clock, sleep=clock.sleep)
    await budget.load()
    return ModelCaller(client, budget, config.llm.extraction.tokens_per_char), budget


def _listing(**fields: Any) -> RawListing:
    base: dict[str, Any] = {
        "source": "bdjobs", "source_job_id": "1", "url": "https://x",
        "title": "Junior Python Developer", "description": POSTING, "description_format": "text",
        "company": "Acme", "location": "Dhaka", "deadline": date(2026, 11, 1),
    }  # fmt: skip
    base.update(fields)
    return RawListing(**base)


# --- schema and prompt ---------------------------------------------------------


def test_strict_schema_shape(config: Config) -> None:
    schema = strict_schema(JobFacts, ["ai-ml", "cloud"])
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"]) == set(JobFacts.model_fields)
    assert schema["properties"]["primary_domain"]["anyOf"][0]["enum"] == ["ai-ml", "cloud"]
    assert schema["properties"]["secondary_domains"]["items"]["enum"] == ["ai-ml", "cloud"]
    education = schema["properties"]["education"]
    assert education["additionalProperties"] is False
    assert education["required"] == ["level", "fields", "required"]
    assert "$defs" not in json.dumps(schema)
    labels = strict_schema(TitleLabels, ["x", "none"])
    assert labels["properties"]["labels"]["items"]["properties"]["slug"]["enum"] == ["x", "none"]


def test_prompts_load_and_render(config: Config) -> None:
    prompt = Prompt.load(config.root / config.llm.extraction.prompt)
    system, user = prompt.render(domains="ai-ml", title="T", company="C", location="L", posting="P")
    assert "ai-ml" in system
    assert "{{" not in system + user
    assert user.startswith("Title: T")
    assert "bad field" in prompt.retry_note("bad field")
    titles = Prompt.load(config.root / config.llm.title_classification.prompt)
    assert "{{titles}}" in titles.user


def test_facts_validate(config: Config) -> None:
    assert JobFacts.model_validate(facts()).application_deadline == date(2026, 11, 1)
    with pytest.raises(ValueError, match="country"):
        JobFacts.model_validate(facts(country="Bangladesh"))
    with pytest.raises(ValueError, match="extra"):
        JobFacts.model_validate({**facts(), "score": 9})


# --- client --------------------------------------------------------------------


async def test_client_request_and_answer(config: Config) -> None:
    fake = FakeLlm(completion({"ok": 1}, (120, 30)))
    client = _client(config, fake)
    entry = config.llm.chain[0]
    answer = await client.complete_json(entry, "sys", "user", "job_facts", {"type": "object"})
    assert (answer.data, answer.tokens_in, answer.tokens_out) == ({"ok": 1}, 120, 30)
    request, body = fake.requests[0], fake.bodies[0]
    assert (
        str(request.url)
        == "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
    )
    assert request.headers["authorization"] == "Bearer g-key"
    assert body["model"] == entry.model
    assert body["temperature"] == 0
    assert body["response_format"]["json_schema"]["strict"] is True
    assert body["reasoning_effort"] == "low"  # per-model params pass through


@pytest.mark.parametrize(
    ("response", "error"),
    [
        (httpx.Response(429), RateLimitedError),
        (httpx.Response(400, text="bad schema"), LlmError),
        (httpx.Response(200, json={"choices": []}), LlmError),
        (httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}]}), LlmError),
    ],
)
async def test_client_errors(
    config: Config, response: httpx.Response, error: type[Exception]
) -> None:
    client = _client(config, FakeLlm(response))
    with pytest.raises(error):
        await client.complete_json(config.llm.chain[0], "s", "u", "n", {})


async def test_client_network_error(config: Config) -> None:
    def boom(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    with pytest.raises(LlmError, match="ConnectError"):
        await _client(config, boom).complete_json(config.llm.chain[0], "s", "u", "n", {})


def test_client_available_needs_key(config: Config) -> None:
    client = _client(config, FakeLlm(), {"GROQ_API_KEY": "k"})
    assert [client.available(e) for e in config.llm.chain] == [False, False, True, True, True]


# --- budget --------------------------------------------------------------------


def _chain(config: Config, *entries: ChainEntry) -> Config:
    llm = config.llm.model_copy(update={"chain": list(entries)})
    return config.__class__(**{**config.__dict__, "llm": llm})


async def test_budget_daily_limits_and_record(config: Config, db: SqliteBackend) -> None:
    a = ChainEntry(provider="groq", model="a", rpm=None, tpm=None, rpd=10, tpd=None)
    b = ChainEntry(provider="groq", model="b", rpm=None, tpm=None, rpd=None, tpd=1000)
    cfg = _chain(config, a, b)
    await db.execute(
        "INSERT INTO llm_usage VALUES ('2026-10-18', 'groq', 'a', 9, 0, 0)"
    )  # 9 of 10 used; with the 5 % margin, 9.5 is the cap
    budget = LlmBudget(cfg.llm, db, NOW, lambda e: True)
    await budget.load()
    assert (await budget.acquire(100)) == b
    assert await budget.acquire(2000) is None  # over b's daily tokens
    await budget.record(b, 40, 10)
    await budget.record(b, 40, 10)
    row = (await db.query("SELECT * FROM llm_usage WHERE model = 'b'"))[0]
    assert (row["requests"], row["tokens_in"], row["tokens_out"]) == (2, 80, 20)


async def test_budget_minute_window_waits_or_moves_on(config: Config, db: SqliteBackend) -> None:
    a = ChainEntry(provider="groq", model="a", rpm=2, tpm=None, rpd=None, tpd=None)
    b = ChainEntry(provider="groq", model="b", rpm=None, tpm=500, rpd=None, tpd=None)
    clock = Clock()
    llm = _chain(config, a, b).llm
    llm = llm.model_copy(
        update={"request": llm.request.model_copy(update={"max_wait_seconds": 60})}
    )
    budget = LlmBudget(llm, db, NOW, lambda e: True, clock, clock.sleep)
    assert [await budget.acquire(100) for _ in range(2)] == [a, a]
    assert await budget.acquire(100) == b  # a is full for this minute
    clock.now = 10
    # a is full and b has room for only 400 more tokens: wait for a's window to clear.
    assert await budget.acquire(450) == a
    assert clock.sleeps == [50.0]


async def test_budget_request_over_the_minute_limit_never_fits(
    config: Config, db: SqliteBackend
) -> None:
    b = ChainEntry(provider="groq", model="b", rpm=None, tpm=500, rpd=None, tpd=None)
    budget = LlmBudget(_chain(config, b).llm, db, NOW, lambda e: True)
    assert await budget.acquire(600) is None


async def test_budget_gives_up_after_max_wait(config: Config, db: SqliteBackend) -> None:
    a = ChainEntry(provider="groq", model="a", rpm=1, tpm=None, rpd=None, tpd=None)
    clock = Clock()
    llm = _chain(config, a).llm
    llm = llm.model_copy(update={"request": llm.request.model_copy(update={"max_wait_seconds": 5})})
    budget = LlmBudget(llm, db, NOW, lambda e: True, clock, clock.sleep)
    assert await budget.acquire(10) == a
    assert await budget.acquire(10) is None
    budget.exhaust(a)
    clock.now = 120
    assert await budget.acquire(10) is None
    assert await budget.acquire(10, skip={"groq/a"}) is None


# --- grounding -----------------------------------------------------------------


def test_grounding_keeps_supported_facts(config: Config) -> None:
    grounder = Grounder(config.skills, 90)
    grounded = grounder.ground(JobFacts.model_validate(facts()), POSTING, date(2026, 11, 1))
    assert grounded.ok
    assert grounded.flags == ()
    assert grounded.facts.required_skills == ["python", "django"]
    assert grounded.facts.preferred_skills == ["docker"]


def test_grounding_drops_unsupported_facts(config: Config) -> None:
    grounder = Grounder(config.skills, 90)
    invented = facts(
        years_evidence="At least 5 years of experience",
        min_years=5,
        remote_eligibility="worldwide",
        remote_eligibility_evidence="Work from anywhere in the world",
        work_mode="remote",
        required_skills=["Python", "Kubernetes", "Teamwork"],
        deadline_evidence="Deadline: 30 November",
        application_deadline="2026-11-30",
    )
    grounded = grounder.ground(JobFacts.model_validate(invented), POSTING, date(2026, 11, 1))
    result = grounded.facts
    assert (result.min_years, result.years_evidence) == (None, None)
    assert result.remote_eligibility == "unspecified"
    assert result.application_deadline is None
    assert result.required_skills == ["python"]
    assert set(grounded.failures) == {
        "years_evidence", "remote_eligibility_evidence", "deadline_evidence",
        "required_skills:Kubernetes", "required_skills:Teamwork",
    }  # fmt: skip


def test_grounding_flags(config: Config) -> None:
    grounder = Grounder(config.skills, 90)
    flagged = facts(
        min_years=3, years_evidence=None, application_deadline="2026-11-02", deadline_evidence=None
    )
    grounded = grounder.ground(JobFacts.model_validate(flagged), POSTING, date(2026, 11, 1))
    assert set(grounded.flags) == {
        "min_years_disagrees_with_text",
        "deadline_disagrees_with_listing",
    }


def test_short_quotes_never_ground(config: Config) -> None:
    assert not Grounder(config.skills, 90).quoted("BS", POSTING)


@pytest.mark.parametrize(
    ("text", "years"),
    [
        ("We have been around for 25 years.", None),
        ("At least 2 years of experience in Python", (2, None)),
        ("Experience: 3-5 years", (3, 5)),
        ("Founded 20 years ago; 3 years experience needed", (3, None)),
        ("Freshers welcome", None),
        # BDJobs puts the requirement a few lines below the heading.
        ("Experience\n\n• At least 4 years\n• The applicants should", (4, None)),
        ("Experience\n• Age 25 to 30 years", None),
    ],
)
def test_experience_years(text: str, years: tuple[int, int | None] | None) -> None:
    found = experience_years(text)
    assert (found[:2] if found else None) == years


# --- rules fallback ------------------------------------------------------------


def test_rules_facts(config: Config, matcher: TitleMatcher) -> None:
    rules = RulesExtractor(config, Grounder(config.skills, 90))
    text = POSTING + " Part-time. A refundable security deposit is required. Unity and R are fun."
    listing = _listing(employment_type="Part Time", work_mode="Work at office")
    location = GateResult("location", "pass", "bangladesh", "Dhaka")
    result = rules.facts(listing, text, matcher.match(listing.title), location)
    assert result.primary_domain == "language-specialists"
    assert result.seniority == "junior"
    assert (result.min_years, result.years_evidence) == (1, "1 year of experience")
    assert (result.education.level, result.education.fields) == ("bachelor", ["cse"])
    assert "python" in result.required_skills
    assert "unity" not in result.required_skills  # ambiguous words are skipped
    assert "r" not in result.required_skills
    assert result.employment_type == "part_time"
    assert result.work_mode == "onsite"
    assert result.country == "BD"
    assert result.remote_eligibility == "not_remote"
    assert result.red_flags == ["security deposit"]


@pytest.mark.parametrize(
    ("gate", "work_mode", "eligibility"),
    [
        (GateResult("location", "pass", "worldwide", "Anywhere"), "remote", "worldwide"),
        (
            GateResult("location", "pass", "bangladesh", "Remote: BD"),
            "remote",
            "bangladesh_included",
        ),
        (GateResult("location", "fail", "region_restricted", "US"), "remote", "region_restricted"),
        (GateResult("location", "needs_facts", "unspecified_remote"), "remote", "unspecified"),
        (GateResult("location", "fail", "not_bangladesh", "Berlin"), None, "not_remote"),
    ],
)
def test_rules_eligibility(
    config: Config, matcher: TitleMatcher, gate: GateResult, work_mode: str | None, eligibility: str
) -> None:
    rules = RulesExtractor(config, Grounder(config.skills, 90))
    listing = _listing(work_mode=work_mode)
    assert (
        rules.facts(listing, "x", matcher.match(listing.title), gate).remote_eligibility
        == eligibility
    )


# --- extractor and title classifier --------------------------------------------


async def test_extractor_falls_through_the_chain(config: Config, db: SqliteBackend) -> None:
    fake = FakeLlm(
        httpx.Response(429),  # gemini flash-lite: rate limited, skipped for the run
        httpx.Response(500, text="oops"),  # gemini flash: error, skipped for this job
        completion({"not": "facts"}),  # groq 120b: invalid, retried once...
        completion(facts()),  # ...and valid
    )
    caller, _ = await _caller(config, db, fake)
    extractor = Extractor(config, caller, Grounder(config.skills, 90))
    result = await extractor.extract(_listing(), POSTING)
    assert result is not None
    assert (result.provider, result.model) == ("groq", "openai/gpt-oss-120b")
    assert result.grounded.ok
    assert [b["model"] for b in fake.bodies] == [
        "gemini-3.5-flash-lite", "gemini-3.8-flash", "openai/gpt-oss-120b", "openai/gpt-oss-120b",
    ]  # fmt: skip
    assert "did not match the schema" in fake.bodies[3]["messages"][1]["content"]
    usage = await db.query("SELECT model, requests FROM llm_usage")
    assert {r["model"]: r["requests"] for r in usage} == {"openai/gpt-oss-120b": 2}


async def test_extractor_gives_up(config: Config, db: SqliteBackend) -> None:
    caller, _ = await _caller(config, db, FakeLlm(), environ={})
    extractor = Extractor(config, caller, Grounder(config.skills, 90))
    assert await extractor.extract(_listing(), POSTING) is None
    assert extractor.input_hash(_listing(), POSTING) != extractor.input_hash(
        _listing(title="X"), POSTING
    )


async def test_extractor_twice_invalid_moves_on(config: Config, db: SqliteBackend) -> None:
    fake = FakeLlm(*[completion({"bad": 1}) for _ in range(10)])
    caller, _ = await _caller(config, db, fake, environ={"GROQ_API_KEY": "k"})
    extractor = Extractor(config, caller, Grounder(config.skills, 90))
    assert await extractor.extract(_listing(), POSTING) is None
    assert len(fake.bodies) == 6  # two tries on each of the three Groq models


async def test_title_classifier_batches_and_caches(config: Config, db: SqliteBackend) -> None:
    def label(body: dict[str, Any]) -> httpx.Response:
        lines = body["messages"][1]["content"].splitlines()
        labels = [
            {
                "number": int(line.split(".")[0]),
                "slug": "none" if "tax" in line else "data-engineering",
            }
            for line in lines
        ]
        return completion({"labels": labels})

    fake = FakeLlm(label, label)
    caller, _ = await _caller(config, db, fake, environ={"GROQ_API_KEY": "k"})
    settings = config.llm.title_classification.model_copy(update={"batch_size": 2})
    cfg = config.__class__(
        **{
            **config.__dict__,
            "llm": config.llm.model_copy(update={"title_classification": settings}),
        }
    )
    classifier = TitleClassifier(cfg, caller, db)
    result = await classifier.classify(["data plumber", "tax technical lead", "etl wizard"], NOW)
    assert result == {
        "data plumber": "data-engineering",
        "tax technical lead": None,
        "etl wizard": "data-engineering",
    }
    assert len(fake.bodies) == 2  # 3 titles in batches of 2
    again = await classifier.classify(["etl wizard"], NOW)  # from the cache, no request
    assert again == {"etl wizard": "data-engineering"}
    assert len(fake.bodies) == 2


async def test_title_classifier_without_models(config: Config, db: SqliteBackend) -> None:
    caller, _ = await _caller(config, db, FakeLlm(), environ={})
    classifier = TitleClassifier(config, caller, db)
    assert await classifier.classify(["x"], NOW) == {}
