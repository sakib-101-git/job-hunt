import json
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from test_extract import FakeLlm, completion
from test_notify import add_job, add_user

from jobhunt.config import Config
from jobhunt.db import SqliteBackend, apply_migrations
from jobhunt.eval import commands
from jobhunt.eval.audit import (
    Auditor,
    auditor_entry,
    auditor_environ,
    candidates,
    report_markdown,
    sample,
    verdict,
    weekly_report,
)
from jobhunt.eval.dataset import (
    Dataset,
    DatasetError,
    GoldenJob,
    GoldenListing,
    GoldenPair,
    GoldenProfile,
    JobLabels,
    check_dataset,
    load_dataset,
    redact,
    save_dataset,
)
from jobhunt.eval.draft import pick
from jobhunt.eval.evaluate import (
    Oracle,
    PairResult,
    false_positive_rate,
    precision_at_k,
    run_eval,
    spearman,
)
from jobhunt.eval.label import edit_labels, label_jobs, label_pairs
from jobhunt.eval.replay import (
    RecordingLlmTransport,
    ReplayLlmTransport,
    load_answers,
    request_key,
    save_answers,
)
from jobhunt.eval.report import build_report, markdown
from jobhunt.extract.llm import LlmClient

pytestmark = pytest.mark.anyio
SEEN = datetime(2026, 10, 18, 6, 0, tzinfo=UTC)


def golden_job(
    job_id: str = "1",
    *,
    title: str = "Junior Python Developer",
    description: str = "We need a Python developer with 1 year of experience. Django, SQL.",
    location: str | None = "Dhaka",
    source: str = "bdjobs",
    **labels: Any,
) -> GoldenJob:
    base: dict[str, Any] = {
        "is_cse_role": True, "primary_domain": "language-specialists", "min_years": 1,
        "remote_eligibility": "not_remote", "country": "BD", "deadline": None,
        "skills": ["python", "django", "sql"], "eligible": True,
    }  # fmt: skip
    base.update(labels)
    listing = GoldenListing(
        source=source,
        source_job_id=job_id,
        url=f"https://jobs.test/{job_id}",
        title=title,
        company="Acme",
        location=location,
        posted_at=SEEN - timedelta(days=1),
        posted_at_source="api",
        deadline=None,
        description=description,
    )
    return GoldenJob(
        id=f"{source}:{job_id}",
        seen_at=SEEN,
        listing=listing,
        labels=JobLabels.model_validate(base),
        label_status="silver",
    )


def profile(**changes: Any) -> GoldenProfile:
    base: dict[str, Any] = {
        "id": "ai", "description": "AI fresh grad", "tracks": ["ai_ml"], "level": "fresh_grad",
        "years": 0, "locations": ["bangladesh", "remote"], "job_types": ["full_time"],
        "min_score": 5.0, "skills": ["python", "django", "sql"],
    }  # fmt: skip
    base.update(changes)
    return GoldenProfile.model_validate(base)


def dataset() -> Dataset:
    jobs = [
        golden_job("1"),
        golden_job(
            "2",
            title="Civil Engineer",
            description="Site supervision for buildings.",
            is_cse_role=False,
            primary_domain=None,
            min_years=None,
            skills=[],
            eligible=False,
        ),
    ]
    pairs = [
        GoldenPair(profile="ai", job="bdjobs:1", label=2, label_status="silver"),
        GoldenPair(profile="ai", job="bdjobs:2", label=0, label_status="gold"),
    ]
    return Dataset(jobs=jobs, profiles=[profile()], pairs=pairs)


# --- dataset -------------------------------------------------------------------


def test_redact() -> None:
    text = "Send CVs to hr@acme.com.bd or call +880 1711-123456 / 01811 123 456."
    assert redact(text) == "Send CVs to [email] or call [phone] / [phone]."
    kept = "Python 3.12, 5 years, apply by 2026-10-30 23:59:00, 3-5 years, 1,500,000 BDT"
    assert redact(kept) == kept


def test_dataset_round_trip_and_checks(tmp_path: Path) -> None:
    data = dataset()
    save_dataset(tmp_path, data)
    # A description with a U+2028 line separator must survive the round trip.
    data.jobs[0] = data.jobs[0].model_copy(
        update={"listing": data.jobs[0].listing.model_copy(update={"description": "a\u2028b"})}
    )
    save_dataset(tmp_path, data)
    assert load_dataset(tmp_path).jobs[0].listing.description == "a\u2028b"

    bad = data.model_copy(deep=True)
    bad.pairs.append(GoldenPair(profile="ai", job="nope:1", label=1, label_status="silver"))
    with pytest.raises(DatasetError, match="unknown ids"):
        check_dataset(bad)
    bad = data.model_copy(deep=True)
    bad.jobs[0] = bad.jobs[0].model_copy(update={"id": "x:1"})
    with pytest.raises(DatasetError, match="<source>:<source_job_id>"):
        check_dataset(bad)
    leaked = golden_job("3", description="mail me at a@b.com")
    with pytest.raises(DatasetError, match="e-mail"):
        check_dataset(Dataset(jobs=[leaked], profiles=[], pairs=[]))
    with pytest.raises(DatasetError, match="duplicate"):
        check_dataset(Dataset(jobs=[golden_job(), golden_job()], profiles=[], pairs=[]))


def test_the_committed_golden_set_is_valid(repo_root: Path) -> None:
    data = load_dataset(repo_root / "eval" / "golden")
    assert len(data.jobs) >= 150
    assert len(data.pairs) >= 300
    assert 8 <= len(data.profiles) <= 10


# --- replay --------------------------------------------------------------------


async def test_record_then_replay(tmp_path: Path) -> None:
    body = {"model": "m", "messages": [{"role": "system", "content": "s"},
                                         {"role": "user", "content": "u"}]}  # fmt: skip
    inner = httpx.MockTransport(lambda _: completion({"answer": 1}))
    recorder = RecordingLlmTransport(inner)
    async with httpx.AsyncClient(transport=recorder) as client:
        response = await client.post("https://llm.test/chat/completions", json=body)
        assert response.json()["choices"][0]["message"]["content"] == '{"answer": 1}'
    path = tmp_path / "answers.jsonl"
    save_answers(path, recorder.answers)
    answers = load_answers(path)
    assert list(answers) == [request_key(body)]

    replay = ReplayLlmTransport(answers)
    async with httpx.AsyncClient(transport=replay) as client:
        hit = await client.post("https://other.test/chat/completions", json=body)
        miss = await client.post("https://other.test/x", json={**body, "messages": []})
    assert json.loads(hit.json()["choices"][0]["message"]["content"]) == {"answer": 1}
    assert miss.status_code == 404
    assert (replay.hits, replay.misses) == (1, 1)
    assert load_answers(tmp_path / "missing.jsonl") == {}


async def test_recorder_reuses_known_answers() -> None:
    body = {"messages": [{"role": "user", "content": "u"}]}
    known = {request_key(body): {"key": request_key(body), "model": "m", "content": "{}"}}

    def unreachable(_: httpx.Request) -> httpx.Response:
        raise AssertionError("a known answer must not be fetched again")

    recorder = RecordingLlmTransport(httpx.MockTransport(unreachable), known)
    async with httpx.AsyncClient(transport=recorder) as client:
        response = await client.post("https://llm.test", json=body)
    assert response.json()["choices"][0]["message"]["content"] == "{}"
    assert (recorder.reused, recorder.new) == (1, 0)


async def test_recorder_passes_failures_through() -> None:
    recorder = RecordingLlmTransport(httpx.MockTransport(lambda _: httpx.Response(429)))
    async with httpx.AsyncClient(transport=recorder) as client:
        response = await client.post("https://llm.test", json={"messages": []})
    assert response.status_code == 429
    assert recorder.answers == {}


# --- metrics -------------------------------------------------------------------


def test_spearman() -> None:
    assert spearman([1, 2, 3, 4], [1, 2, 3, 4]) == pytest.approx(1.0)
    assert spearman([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)
    assert spearman([1, 1, 2, 2], [0, 0, 1, 1]) == pytest.approx(1.0)  # ties
    assert spearman([1, 2], [1, 2]) is None
    assert spearman([1, 1, 1], [0, 1, 2]) is None  # no variance


def _pair(profile_id: str, score: str | None, label: int, notified: bool = False) -> PairResult:
    job = golden_job(f"{profile_id}{score}{label}")
    return PairResult(
        profile(id=profile_id),
        job,
        Decimal(score) if score else None,
        "x",
        notified,
        label,
    )


def test_precision_at_k_and_false_positives() -> None:
    pairs = [
        _pair("a", "9", 2, True),
        _pair("a", "8", 0, True),
        _pair("a", None, 2),
        _pair("b", "5", 1),
    ]
    assert precision_at_k(pairs, 1) == pytest.approx(1.0)  # a: 2; b: 1
    assert precision_at_k(pairs, 5) == pytest.approx((0.5 + 1.0) / 2)
    assert false_positive_rate(pairs) == pytest.approx(0.5)
    assert false_positive_rate([_pair("a", "5", 1)]) is None


def test_oracle(config: Config) -> None:
    oracle = Oracle(config)
    job = golden_job()
    assert oracle.violations(profile(), job) == []
    assert "user_location" in oracle.violations(profile(locations=["remote"]), job)
    assert "tracks" in oracle.violations(profile(tracks=["cybersecurity"]), job)
    senior = golden_job("9", min_years=6)
    assert "seniority" in oracle.violations(profile(), senior)
    civil = dataset().jobs[1]
    assert {"cse", "job_location"} <= set(oracle.violations(profile(), civil))
    remote = golden_job("r", country=None, remote_eligibility="worldwide")
    assert "user_location" in oracle.violations(profile(locations=["bangladesh"]), remote)


# --- the whole run ---------------------------------------------------------------


async def test_run_eval_and_report(config: Config) -> None:
    data = dataset()
    transport = ReplayLlmTransport({})
    run = await run_eval(config, data, transport, commands.replay_environ(config), replay=True)
    by_id = {r.job.id: r for r in run.jobs}
    assert by_id["bdjobs:1"].eligible
    assert by_id["bdjobs:2"].filter_reason == "cse:non_cse_title"
    assert len(run.pairs) == 2
    matched = next(p for p in run.pairs if p.job.id == "bdjobs:1")
    assert matched.score is not None
    assert matched.notified
    report = build_report(config, run)
    assert report.violations == []
    assert report.metrics["silver"].pairs == 1
    assert report.metrics["gold"].pairs == 1
    text = markdown(report, config)
    assert "## Golden-set evaluation" in text
    assert "Hard-gate violations: 0" in text


async def test_empty_dataset(config: Config) -> None:
    run = await run_eval(
        config, Dataset(jobs=[], profiles=[], pairs=[]), ReplayLlmTransport({}), {}, replay=True
    )
    assert run.jobs == []


async def test_evaluate_command_checks_thresholds(
    config: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    save_dataset(tmp_path, dataset())
    out: list[str] = []
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    report = tmp_path / "report.md"
    code = await commands.evaluate(
        config, tmp_path, live=False, record=False, check=True, report_path=report, write=out.append
    )
    assert out[0].startswith("recorded answers used: 0, missing:")
    assert report.read_text(encoding="utf-8") == summary.read_text(encoding="utf-8")
    assert code in (0, 1)


async def test_live_eval_records_answers(
    config: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    save_dataset(tmp_path, dataset())
    for name in ("GEMINI_API_KEY", "GROQ_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    out: list[str] = []
    await commands.evaluate(
        config, tmp_path, live=True, record=True, check=False, report_path=None, write=out.append
    )
    assert out[0].startswith("recorded 0 new answers")  # no keys: no model was called


# --- labelling -----------------------------------------------------------------


def test_edit_labels() -> None:
    labels = golden_job().labels
    edited = edit_labels(labels, ["min_years=3", "skills=python, go", "country=bd", "deadline="])
    assert (edited.min_years, edited.skills, edited.country) == (3, ["python", "go"], "BD")
    assert edit_labels(labels, ["is_cse_role=no"]).is_cse_role is False
    with pytest.raises(ValueError, match="field=value"):
        edit_labels(labels, ["nonsense"])
    with pytest.raises(ValueError, match="true or false"):
        edit_labels(labels, ["eligible=maybe"])


def _answers(*values: str) -> Iterator[str]:
    yield from values


def test_label_jobs_and_pairs(tmp_path: Path) -> None:
    data = dataset()
    save_dataset(tmp_path, data)
    said: list[str] = []
    answers = _answers("min_years=x", "", "min_years=2", "", "s")
    assert label_jobs(data, tmp_path, lambda _: next(answers), said.append) == 1
    assert "not saved" in " ".join(said)
    saved = load_dataset(tmp_path)
    assert (saved.jobs[0].label_status, saved.jobs[0].labels.min_years) == ("gold", 2)
    assert saved.jobs[1].label_status == "silver"  # skipped

    answers = _answers("1")
    assert label_pairs(saved, tmp_path, lambda _: next(answers), said.append) == 1
    pairs = load_dataset(tmp_path).pairs
    assert (pairs[0].label, pairs[0].label_status) == (1, "gold")
    assert label_jobs(saved, tmp_path, lambda _: "q", said.append) == 0
    assert label_pairs(load_dataset(tmp_path), tmp_path, lambda _: "q", said.append) == 0


# --- draft ---------------------------------------------------------------------


def test_pick_spreads_over_sources_and_outcomes() -> None:
    def row(source: str, n: int, status: str = "open", reason: str | None = None) -> dict[str, Any]:
        return {"source": source, "source_job_id": str(n), "status": status,
                "filter_reason": reason, "jd_text": "text"}  # fmt: skip

    rows = [row("bdjobs", n) for n in range(10)] + [row("remoteok", n) for n in range(10)]
    rows += [row("remoteok", 100 + n, "filtered", "cse:no_tech_signal") for n in range(10)]
    rows += [row("remoteok", 200, "filtered", "freshness:too_old")]
    chosen = pick(rows, 10, ["bdjobs"], 0.6)
    sources = [(r["source"], r["status"]) for r in chosen]
    assert sources.count(("bdjobs", "open")) == 4  # twice per round
    assert sources.count(("remoteok", "open")) == 2
    assert sources.count(("remoteok", "filtered")) == 4
    assert all(r["filter_reason"] != "freshness:too_old" for r in chosen)
    assert pick(rows, 10, ["bdjobs"], 0.6) == chosen  # stable


# --- auditor -------------------------------------------------------------------


@pytest.fixture
async def db(repo_root: Path) -> AsyncIterator[SqliteBackend]:
    backend = SqliteBackend(":memory:")
    await apply_migrations(backend, repo_root / "migrations")
    yield backend
    await backend.aclose()


def audit_answer(points: tuple[float, ...] = (3, 2.5, 2, 1, 0.5), enough: bool = True) -> Any:
    def part(points: float, quote: str | None) -> dict[str, Any]:
        return {"points": points, "reason": "r", "quote": quote}

    names = ("role", "skills", "experience", "education", "preference")
    quotes = ("Machine learning", "python", "0-1 years", None, "Not in the posting at all")
    data = {n: part(p, q) for n, p, q in zip(names, points, quotes, strict=True)}
    return data | {"enough_information": enough, "notes": "fine"}


def test_verdict() -> None:
    assert verdict(Decimal("8.0"), Decimal("7.0"), True, 1.5) == "justified"
    assert verdict(Decimal("8.0"), Decimal("5.0"), True, 1.5) == "overrated"
    assert verdict(Decimal("4.0"), Decimal("7.0"), True, 1.5) == "underrated"
    assert verdict(Decimal("4.0"), Decimal("7.0"), False, 1.5) == "unclear"


def test_auditor_entry(config: Config) -> None:
    assert auditor_entry(config, {}).key == f"{config.llm.audit.provider}/{config.llm.audit.model}"
    custom = auditor_entry(config, {"AUDITOR_PROVIDER": "groq", "AUDITOR_MODEL": "other"})
    assert (custom.key, custom.rpm) == ("groq/other", None)
    with pytest.raises(ValueError, match="not a provider"):
        auditor_entry(config, {"AUDITOR_PROVIDER": "nope"})
    with pytest.raises(ValueError, match="train on inputs"):
        auditor_entry(config, {"AUDITOR_PROVIDER": "gemini"})
    env = auditor_environ(config, {"AUDITOR_API_KEY": "k"})
    assert env[config.llm.providers[config.llm.audit.provider].api_key_env] == "k"


async def _match(db: SqliteBackend, match_id: int, job_id: int, score: float) -> None:
    await db.execute(
        "INSERT INTO matches (id, user_id, job_id, score, breakdown_json, explanation_json,"
        " gate_results_json, scorer_version, created_at) VALUES (?, 1, ?, ?, '{}', '{}',"
        ' \'{"tracks": {"status": "pass", "reason": "primary", "evidence": "ai_ml"}}\','
        " 'v', '2026-10-17T06:00:00Z')",
        (match_id, job_id, score),
    )


async def test_audit_flow(db: SqliteBackend, config: Config) -> None:
    from test_notify import NOW

    await add_user(db, 1)
    for job_id in (1, 2, 3):
        await add_job(db, job_id)
    await db.execute(
        "UPDATE jobs SET jd_text = 'Machine learning engineer. python. 0-1 years of experience.'"
    )
    await _match(db, 1, 1, 9.0)
    await _match(db, 2, 2, 4.0)
    await _match(db, 3, 3, 6.0)
    await db.execute(
        "INSERT INTO feedback (user_id, match_id, kind, reason, created_at)"
        " VALUES (1, 1, 'thumbs_down', 'too_senior', '2026-10-17T07:00:00Z')"
    )
    entry = auditor_entry(config, {})
    rows = await candidates(db, config, entry.model, NOW)
    chosen = sample(rows, config)
    assert int(str(chosen[0]["id"])) == 1  # 9.0 is always audited, highest first

    llm = FakeLlm(completion(audit_answer()), completion(audit_answer(enough=False)))
    client = LlmClient(config.llm, {"GROQ_API_KEY": "k"}, httpx.AsyncClient(
        transport=httpx.MockTransport(llm)))  # fmt: skip
    auditor = Auditor(db, config, client, {})
    assert "Tracks: " in auditor.profile_text(rows[0])
    results = await auditor.audit(rows[:2], NOW)
    assert [r.verdict for r in results] == ["justified", "unclear"]
    assert "quotes not in the posting: preference" in results[0].notes
    user_message = llm.bodies[0]["messages"][1]["content"]
    assert "Alice" not in user_message
    assert "chat_id" not in user_message
    # Audited matches are not picked again by the same model.
    assert len(await candidates(db, config, entry.model, NOW)) == 1

    report = await weekly_report(db, config, NOW)
    assert (report.audits, report.matches) == (2, 3)
    assert report.agreement == pytest.approx(1.0)
    assert report.feedback["ai_ml"]["thumbs_down"] == 1
    text = report_markdown(report, entry.model)
    assert "## Weekly audit" in text
    assert "Feedback by track" in text


async def test_audit_command(
    db: SqliteBackend, config: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[dict[str, Any]] = []

    def telegram(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        "jobhunt.notify.telegram.httpx.AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(telegram), **kw),
    )
    path = tmp_path / "audit.db"
    environ = {
        "JOBHUNT_SQLITE_PATH": str(path),
        "TELEGRAM_BOT_TOKEN": "T",
        "ADMIN_TELEGRAM_IDS": "9001",
    }
    backend = SqliteBackend(path)
    await apply_migrations(backend, config.root / "migrations")
    await backend.aclose()
    out: list[str] = []
    assert await commands.audit(config, environ, out.append) == 0
    assert out[0] == "audited 0 of 0 sampled matches"
    assert sent[0]["chat_id"] == 9001
    assert "Weekly audit" in sent[0]["text"]
