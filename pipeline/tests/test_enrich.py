import json
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from jobhunt.config import Config
from jobhunt.db import SqliteBackend, apply_migrations
from jobhunt.extract.llm import LlmClient
from jobhunt.jobs.enrich import enrich
from jobhunt.jobs.gating import GateItem, JobGates, apply_gates
from jobhunt.jobs.store import store_jobs
from jobhunt.normalize.jobs import JobNormalizer
from jobhunt.sources.models import RawListing
from jobhunt.taxonomy.matcher import TitleMatcher

pytestmark = pytest.mark.anyio
NOW = datetime(2026, 10, 18, 9, 0, tzinfo=UTC)
PYTHON_JOB = (
    "Junior Python Developer. At least 1 year of experience with Python. "
    "Fully remote, open to candidates worldwide. Work from anywhere."
)


def _facts(**changes: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "is_cse_role": True, "is_cse_role_evidence": "Junior Python Developer",
        "primary_domain": "language-specialists", "secondary_domains": [],
        "normalized_title": "Junior Python Developer", "seniority": "junior",
        "seniority_evidence": "Junior Python Developer", "min_years": 1, "max_years": None,
        "years_evidence": "At least 1 year of experience",
        "education": {"level": "unknown", "fields": [], "required": False},
        "education_evidence": None, "required_skills": ["Python"], "preferred_skills": [],
        "employment_type": "full_time", "work_mode": "remote", "country": None, "city": None,
        "remote_eligibility": "worldwide",
        "remote_eligibility_evidence": "open to candidates worldwide",
        "timezone_requirement": None, "salary": None, "application_deadline": None,
        "deadline_evidence": None, "posted_date_in_text": None, "language_requirements": [],
        "red_flags": [], "summary": "Python work.",
    }  # fmt: skip
    base.update(changes)
    return base


def _llm(facts_for: dict[str, dict[str, Any]], labels: dict[str, str]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        user = body["messages"][1]["content"]
        if body["response_format"]["json_schema"]["name"] == "title_labels":
            answer: Any = {
                "labels": [
                    {"number": int(line.split(".")[0]), "slug": labels[line.split(". ", 1)[1]]}
                    for line in user.splitlines()
                ]
            }
        else:
            title = user.splitlines()[0].removeprefix("Title: ")
            answer = facts_for[title]
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps(answer)}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
        )

    return httpx.MockTransport(handler)


def _listing(job_id: str, title: str, description: str, **fields: Any) -> RawListing:
    base: dict[str, Any] = {
        "source": "remoteok", "source_job_id": job_id, "url": f"https://x/{job_id}",
        "title": title, "description": description, "description_format": "text",
        "company": "Acme", "location": None, "location_restrictions": ("Remote",),
        "work_mode": "remote", "posted_at": NOW - timedelta(days=1), "posted_at_source": "api",
    }  # fmt: skip
    base.update(fields)
    return RawListing(**base)


@pytest.fixture
async def db(repo_root: Path) -> AsyncIterator[SqliteBackend]:
    backend = SqliteBackend(":memory:")
    await apply_migrations(backend, repo_root / "migrations")
    yield backend
    await backend.aclose()


async def _seed(
    db: SqliteBackend, config: Config, matcher: TitleMatcher, listings: list[RawListing]
) -> None:
    normalizer = JobNormalizer(config)
    stored = await store_jobs(db, [normalizer.record(x) for x in listings], NOW, 10)
    items = [GateItem(j.id, x, NOW, False) for j, x in zip(stored, listings, strict=True)]
    await apply_gates(db, JobGates(config, matcher), items, NOW, 10)


async def _jobs(db: SqliteBackend) -> dict[str, dict[str, Any]]:
    return {str(r["source_job_id"]): dict(r) for r in await db.query("SELECT * FROM jobs")}


async def test_enrich_extracts_classifies_and_regates(
    db: SqliteBackend, config: Config, matcher: TitleMatcher
) -> None:
    listings = [
        # Location unstated in the listing: the description says worldwide.
        _listing("py", "Junior Python Developer", PYTHON_JOB),
        # Tech word, unknown title: the classifier says it is not a computing role.
        _listing("tax", "Tax Technology Associate", "Tax filings. Work from anywhere."),
        # Unknown title the classifier accepts; the description is US-only.
        _listing("pipe", "Data Plumber", "Build data pipelines. Remote, US only.",
                 location_restrictions=("Worldwide",)),
    ]  # fmt: skip
    await _seed(db, config, matcher, listings)
    facts_for = {
        "Junior Python Developer": _facts(),
        "Data Plumber": _facts(
            is_cse_role_evidence="Build data pipelines", primary_domain="data-engineering",
            normalized_title="Data Plumber", seniority="unknown", seniority_evidence=None,
            min_years=None, years_evidence=None, required_skills=[],
            remote_eligibility="worldwide", remote_eligibility_evidence="Build data pipelines",
        ),
    }  # fmt: skip
    # Keys are normalized titles: "Associate" is a seniority word and is stripped.
    labels = {"tax technology": "none", "data plumber": "data-engineering"}
    client = LlmClient(
        config.llm, {"GROQ_API_KEY": "k"}, httpx.AsyncClient(transport=_llm(facts_for, labels))
    )

    counts = await enrich(db, config, client, NOW)

    assert counts["titles classified"] == 2
    assert counts["filtered cse:not_cse_per_classifier"] == 1
    # Groq's 8K tokens/minute moves later requests on to the next Groq model.
    assert sum(n for key, n in counts.items() if key.startswith("facts by groq/")) == 2
    jobs = await _jobs(db)
    assert (jobs["py"]["status"], jobs["py"]["remote_eligibility_evidence"]) == (
        "open", "open to candidates worldwide",
    )  # fmt: skip
    assert json.loads(jobs["py"]["gates_json"])["needs_facts"] == []
    assert jobs["tax"]["filter_reason"] == "cse:not_cse_per_classifier"
    assert jobs["pipe"]["status"] == "open"  # listing said Worldwide; facts don't override a pass
    domains = await db.query(
        "SELECT domain_slug, method FROM job_domains WHERE job_id = ?", (jobs["pipe"]["id"],)
    )
    assert domains == [{"domain_slug": "data-engineering", "method": "llm"}]
    stored = await db.query("SELECT provider, grounding_ok, prompt_version FROM job_facts")
    assert {(r["provider"], r["prompt_version"]) for r in stored} == {("groq", "extraction.v2")}

    # A second run finds nothing to do: facts are current.
    again = await enrich(db, config, client, NOW)
    assert not any(key.startswith("facts by") for key in again)


async def test_enrich_without_models_uses_rules_and_retries_later(
    db: SqliteBackend, config: Config, matcher: TitleMatcher
) -> None:
    await _seed(db, config, matcher, [_listing("py", "Junior Python Developer", PYTHON_JOB)])
    no_keys = LlmClient(config.llm, {}, httpx.AsyncClient(transport=_llm({}, {})))
    counts = await enrich(db, config, no_keys, NOW)
    assert counts == {"facts by rules (no model had room)": 1}
    jobs = await _jobs(db)
    assert json.loads(jobs["py"]["gates_json"])["needs_facts"] == ["location"]  # undecided
    # With a model, the rules-only facts are replaced.
    client = LlmClient(
        config.llm, {"GROQ_API_KEY": "k"},
        httpx.AsyncClient(transport=_llm({"Junior Python Developer": _facts()}, {})),
    )  # fmt: skip
    counts = await enrich(db, config, client, NOW)
    assert sum(n for key, n in counts.items() if key.startswith("facts by groq/")) == 1
    assert (await db.query("SELECT provider FROM job_facts"))[0]["provider"] == "groq"


async def test_extracted_region_restriction_filters(
    db: SqliteBackend, config: Config, matcher: TitleMatcher
) -> None:
    await _seed(
        db, config, matcher, [_listing("py", "Junior Python Developer", PYTHON_JOB + " US only.")]
    )
    restricted = _facts(
        remote_eligibility="region_restricted", remote_eligibility_evidence="US only"
    )
    client = LlmClient(
        config.llm, {"GROQ_API_KEY": "k"},
        httpx.AsyncClient(transport=_llm({"Junior Python Developer": restricted}, {})),
    )  # fmt: skip
    counts = await enrich(db, config, client, NOW, limit=5)
    assert counts["after facts: filtered location:region_restricted"] == 1


async def test_extracted_deadline_and_date_feed_freshness(
    db: SqliteBackend, config: Config, matcher: TitleMatcher
) -> None:
    text = PYTHON_JOB + " Apply by 2026-10-10."
    await _seed(
        db, config, matcher, [_listing("py", "Junior Python Developer", text, posted_at=None)]
    )
    expired = _facts(application_deadline="2026-10-10", deadline_evidence="Apply by 2026-10-10")
    client = LlmClient(
        config.llm, {"GROQ_API_KEY": "k"},
        httpx.AsyncClient(transport=_llm({"Junior Python Developer": expired}, {})),
    )  # fmt: skip
    await enrich(db, config, client, NOW)
    assert (await _jobs(db))["py"]["filter_reason"] == "freshness:deadline_passed"
    assert date(2026, 10, 10) < NOW.date()
