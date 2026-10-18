"""The `jobhunt eval` and `jobhunt audit` commands."""

import logging
import os
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path

import httpx

from jobhunt.automation.pipeline import admin_ids
from jobhunt.config import Config
from jobhunt.db import open_database
from jobhunt.eval.audit import (
    Auditor,
    WeeklyReport,
    auditor_environ,
    candidates,
    report_markdown,
    sample,
    weekly_report,
)
from jobhunt.eval.dataset import GOLDEN_DIR, Dataset, load_dataset, load_profiles, save_dataset
from jobhunt.eval.draft import draft_jobs
from jobhunt.eval.evaluate import run_eval
from jobhunt.eval.label import label_jobs, label_pairs
from jobhunt.eval.replay import (
    FILE_NAME,
    RecordingLlmTransport,
    ReplayLlmTransport,
    load_answers,
    save_answers,
)
from jobhunt.eval.report import build_report, markdown
from jobhunt.extract.llm import LlmClient
from jobhunt.notify.telegram import TelegramClient, TelegramError
from jobhunt.score.render import Locale

Write = Callable[[str], None]


def _summary(text: str) -> None:
    if path := os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(path).open("a", encoding="utf-8") as out:
            out.write(text)


def replay_environ(config: Config) -> dict[str, str]:
    """A stand-in key for every provider, so the client tries each model in turn
    and the replay transport answers from the recordings."""
    return {provider.api_key_env: "replay" for provider in config.llm.providers.values()}


async def evaluate(
    config: Config,
    directory: Path,
    *,
    live: bool,
    record: bool,
    check: bool,
    report_path: Path | None,
    write: Write,
) -> int:
    dataset = load_dataset(directory)
    answers_path = directory / FILE_NAME
    if live:
        recorder = RecordingLlmTransport() if record else None
        transport: httpx.AsyncBaseTransport = recorder or httpx.AsyncHTTPTransport()
        run = await run_eval(config, dataset, transport, os.environ, replay=False)
        if recorder is not None:
            save_answers(answers_path, recorder.answers)
            write(f"recorded {len(recorder.answers)} answers in {answers_path}")
    else:
        replay = ReplayLlmTransport(load_answers(answers_path))
        # Every miss is a "model failed" warning per model; the count below says it once.
        logging.getLogger("jobhunt.extract").setLevel(logging.ERROR)
        run = await run_eval(config, dataset, replay, replay_environ(config), replay=True)
        write(f"recorded answers used: {replay.hits}, missing: {replay.misses}")
    report = build_report(config, run)
    text = markdown(report, config)
    write(text)
    _summary(text)
    if report_path is not None:
        report_path.write_text(text, encoding="utf-8")
    return 1 if check and not report.ok else 0


def label(directory: Path, pairs: bool, ask: Callable[[str], str], write: Write) -> int:
    dataset = load_dataset(directory)
    done = (label_pairs if pairs else label_jobs)(dataset, directory, ask, write)
    write(f"{done} labels confirmed as gold")
    return 0


async def draft(config: Config, directory: Path, db_path: Path, count: int, write: Write) -> int:
    if (directory / "jobs.jsonl").is_file():
        write(f"{directory / 'jobs.jsonl'} exists; move it away to draft a new set")
        return 1
    db = open_database(config.app.database, {"JOBHUNT_SQLITE_PATH": str(db_path)})
    try:
        jobs = await draft_jobs(db, config, count)
    finally:
        await db.aclose()
    # Pairs name the old jobs, so they go; the profiles stay.
    save_dataset(directory, Dataset(jobs=jobs, profiles=load_profiles(directory), pairs=[]))
    write(f"drafted {len(jobs)} jobs into {directory / 'jobs.jsonl'}")
    return 0


async def audit(config: Config, environ: Mapping[str, str], write: Write) -> int:
    now = datetime.now(UTC)
    db = open_database(config.app.database, environ)
    client = LlmClient(config.llm, auditor_environ(config, environ))
    try:
        auditor = Auditor(db, config, client, environ)
        rows = sample(await candidates(db, config, auditor.model, now), config)
        results = await auditor.audit(rows, now)
        write(f"audited {len(results)} of {len(rows)} sampled matches")
        report = await weekly_report(db, config, now)
    finally:
        await client.aclose()
        await db.aclose()
    text = report_markdown(report, auditor.model)
    write(text)
    _summary(text)
    await _tell_admins(config, environ, report, auditor.model)
    return 0


async def _tell_admins(
    config: Config, environ: Mapping[str, str], report: WeeklyReport, model: str
) -> None:
    token, admins = environ.get("TELEGRAM_BOT_TOKEN"), admin_ids(environ)
    if not token or not admins:
        return
    t = Locale.load(config.root)

    def pct(value: float | None) -> str:
        return "-" if value is None else f"{value:.0%}"

    lines = [
        t(
            "admin.audit_summary",
            count=report.audits,
            model=model,
            agreement=pct(report.agreement),
            delta="-" if report.mean_abs_delta is None else f"{report.mean_abs_delta:.2f}",
        )
    ]
    if "inflation" in report.warnings:
        lines.append(t("admin.audit_inflation", share=pct(report.strong_share)))
    if "narrow" in report.warnings:
        lines.append(t("admin.audit_narrow", stdev=f"{report.stdev or 0:.2f}"))
    for row in report.worst:
        lines.append(
            t(
                "admin.audit_worst",
                url=row["url"],
                title=row["title"],
                system=row["score"],
                auditor=row["auditor_score"],
            )
        )
    telegram = TelegramClient(config.app.notifications, token)
    try:
        for chat_id in admins:
            try:
                await telegram.send_message(chat_id, "\n".join(lines))
            except TelegramError:
                continue
    finally:
        await telegram.aclose()


__all__ = ["GOLDEN_DIR", "audit", "draft", "evaluate", "label"]
