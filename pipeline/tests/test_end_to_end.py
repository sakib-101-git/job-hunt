"""Three users, the real golden-set jobs, the real pipeline stages: each user
gets alerts for their own tracks only, never the same job twice (brief R2, R4).
Telegram is faked; the LLM answers are the recorded ones."""

import dataclasses
import json
from collections import defaultdict
from pathlib import Path

import httpx
import pytest
from test_automation import _pipeline
from test_notify import FakeTelegram

from jobhunt.config import Config
from jobhunt.db import SqliteBackend, apply_migrations
from jobhunt.eval.commands import replay_environ
from jobhunt.eval.dataset import GoldenProfile, load_dataset
from jobhunt.eval.evaluate import Oracle, _store, _unlimited
from jobhunt.eval.replay import FILE_NAME, ReplayLlmTransport, load_answers
from jobhunt.extract.llm import LlmClient
from jobhunt.jobs.enrich import enrich

pytestmark = pytest.mark.anyio

USERS = {
    # Each user is (tracks, locations, level, skills).
    1: (["cybersecurity"], ["bangladesh", "remote"], "junior", ["linux", "siem", "firewall"]),
    2: (["software_web"], ["bangladesh"], "fresh_grad", ["php", "laravel", "react", "mysql"]),
    3: (["ai_ml"], ["bangladesh", "remote"], "fresh_grad", ["python", "llm", "pytorch"]),
}


async def _users(db: SqliteBackend) -> None:
    for user_id, (tracks, locations, level, skills) in USERS.items():
        await db.execute(
            "INSERT INTO users (id, chat_id, locale) VALUES (?, ?, 'en')", (user_id, user_id * 10)
        )
        await db.execute(
            "INSERT INTO user_prefs (user_id, tracks_json, include_adjacent, experience_level,"
            " locations_json, job_types_json, min_score, delivery_mode, max_per_cycle)"
            ' VALUES (?, ?, 1, ?, ?, \'["full_time", "contract", "internship"]\', 5, \'instant\','
            " 100)",
            (user_id, json.dumps(tracks), level, json.dumps(locations)),
        )
        await db.execute(
            "INSERT INTO user_profiles (user_id, skills_json) VALUES (?, ?)",
            (user_id, json.dumps(skills)),
        )


async def test_three_users_get_their_own_alerts(config: Config, repo_root: Path) -> None:
    golden = repo_root / "eval" / "golden"
    dataset = load_dataset(golden)
    replay_config = dataclasses.replace(config, llm=_unlimited(config.llm))
    db = SqliteBackend(":memory:")
    await apply_migrations(db, repo_root / "migrations")
    ids = await _store(db, config, dataset.jobs)
    as_of = max(job.seen_at for job in dataset.jobs)
    transport = ReplayLlmTransport(load_answers(golden / FILE_NAME))
    client = LlmClient(
        replay_config.llm, replay_environ(config), httpx.AsyncClient(transport=transport)
    )
    await enrich(db, replay_config, client, as_of, limit=len(dataset.jobs))
    await client.aclose()
    # The golden jobs are a source's first sighting for this database: let them
    # count as new (seeded jobs are never sent outside a user's backfill).
    await db.execute("UPDATE jobs SET gates_json = json_set(gates_json, '$.seeded', json('false'))")
    await _users(db)
    # Not each user's first matches, so the regular rules apply, not the backfill cap.
    await db.execute(
        "INSERT INTO matches (user_id, job_id, score, breakdown_json, explanation_json,"
        " gate_results_json, scorer_version) SELECT u.id, ?, 0, '{}', '{}', '{}', 'seed'"
        " FROM users u",
        (ids[dataset.jobs[-1].id],),
    )

    fake = FakeTelegram()
    pipeline = _pipeline(db, config, fake)
    pipeline._clock = lambda: as_of
    matched = await pipeline.match()
    sent = await pipeline.notify()
    assert sent.counts["sent"] > 0, (matched.counts, sent.counts)

    by_chat: dict[int, list[str]] = defaultdict(list)
    for body in fake.sent:
        title = body["text"].split("\n", 1)[0]
        by_chat[int(body["chat_id"])].append(title)
    assert set(by_chat) == {10, 20, 30}, "every user got alerts"

    titles = {job.id: job.listing.title for job in dataset.jobs}
    rows = await db.query(
        "SELECT n.user_id, j.source || ':' || j.source_job_id AS job, d.domain_slug"
        " FROM notifications n JOIN jobs j ON j.id = n.job_id"
        " LEFT JOIN job_domains d ON d.job_id = j.id AND d.is_primary = 1"
        " WHERE n.status = 'sent'"
    )
    delivered: dict[int, set[str]] = defaultdict(set)
    for row in rows:
        user_id, job_id = int(str(row["user_id"])), str(row["job"])
        assert job_id not in delivered[user_id], "never twice"
        delivered[user_id].add(job_id)
    labels = {job.id: job.labels for job in dataset.jobs}
    oracle = Oracle(config)
    profiles = {
        user_id: GoldenProfile(
            id=str(user_id), description="", tracks=tracks, level=level,  # type: ignore[arg-type]
            years=None, locations=locations, job_types=["full_time"],  # type: ignore[arg-type]
            min_score=5, skills=skills,
        )
        for user_id, (tracks, locations, level, skills) in USERS.items()
    }  # fmt: skip
    for user_id, jobs in delivered.items():
        for job_id in jobs:
            assert labels[job_id].eligible, f"user {user_id} got {titles[job_id]}"
            job = next(j for j in dataset.jobs if j.id == job_id)
            # The labelled facts say the job is in one of the user's tracks
            # (domains or include_titles) and in a place they chose.
            broken = set(oracle.violations(profiles[user_id], job)) - {"seniority"}
            assert not broken, (user_id, titles[job_id], broken)
    # The security user never gets a frontend/web job (brief R2).
    security_titles = " ".join(titles[j] for j in delivered[1]).lower()
    assert "frontend" not in security_titles
    assert "react" not in security_titles
    assert delivered[1] != delivered[2]

    # A second run sends nothing again.
    again = await pipeline.notify()
    assert again.counts.get("sent", 0) == 0
    await db.aclose()
