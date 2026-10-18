"""Send pending notifications (brief §6.9).

Per user, oldest first: instant users are served by every run, digest users by
the digest run (or on demand); nothing goes out during a user's quiet hours.
Freshness and the deadline are checked again at send time. The first
`max_per_cycle` matches go out as full alerts; the rest are listed in one
message. Every row ends as sent (with Telegram's message id), failed (with the
error and attempt count; retried on later runs up to `max_attempts`) or
skipped (with the reason). A 403 marks the user blocked. Counts are returned
as they happened: a failed send is never reported as success (defect D3).
"""

import json
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo

from jobhunt.config import Config
from jobhunt.config.models import Callbacks
from jobhunt.db import Database, Row, Statement, timestamp
from jobhunt.gates.freshness import FreshnessGate
from jobhunt.notify.telegram import BlockedError, Keyboard, TelegramClient, TelegramError
from jobhunt.score.matching import load_jobs, load_profiles, stored_result
from jobhunt.score.model import JobView
from jobhunt.score.render import Locale, Renderer

_PENDING = """
SELECT n.id AS notification_id, n.attempts, n.match_id, n.job_id, n.created_at,
       u.id AS user_id, u.chat_id, p.delivery_mode, p.quiet_hours_json, p.max_per_cycle,
       m.breakdown_json, m.explanation_json, m.gate_results_json, m.scorer_version,
       j.first_seen_at
FROM notifications n
JOIN users u ON u.id = n.user_id
JOIN user_prefs p ON p.user_id = n.user_id
JOIN matches m ON m.id = n.match_id
JOIN jobs j ON j.id = n.job_id
WHERE n.status IN ('pending', 'failed') AND n.attempts < ? AND u.status = 'active'
ORDER BY n.created_at, n.id
"""


@dataclass(frozen=True)
class SendPlan:
    digest_run: bool = False
    only_users: frozenset[int] | None = None  # /digest: these users, whatever their mode


def keyboard(job: JobView, match_id: int, locale: Locale, codes: Callbacks) -> Keyboard:
    def button(key: str, code: str) -> dict[str, str]:
        return {"text": str(locale.text(f"buttons.{key}")), "callback_data": f"{code}:{match_id}"}

    return [
        [{"text": str(locale.text("buttons.apply")), "url": job.url}],
        [button("why", codes.why)],
        [button("applied", codes.applied), button("save", codes.save), button("hide", codes.hide)],
        [button("thumbs_up", codes.thumbs_up), button("thumbs_down", codes.thumbs_down)],
    ]


def in_quiet_hours(raw: object, now: datetime, default_zone: ZoneInfo) -> bool:
    """`{"start": "22:00", "end": "08:00", "tz": "Asia/Dhaka"}`; a window may wrap midnight."""
    if not raw:
        return False
    hours = json.loads(str(raw))
    zone = ZoneInfo(hours.get("tz") or str(default_zone))
    local = now.astimezone(zone).time()
    start, end = time.fromisoformat(hours["start"]), time.fromisoformat(hours["end"])
    return start <= local < end if start <= end else local >= start or local < end


async def send_pending(
    db: Database,
    config: Config,
    telegram: TelegramClient,
    now: datetime,
    plan: SendPlan = SendPlan(),  # noqa: B008 (frozen, never mutated)
) -> Counter[str]:
    settings = config.app.notifications
    zone = ZoneInfo(config.app.timezone)
    renderer = Renderer.from_config(config)
    freshness = FreshnessGate(config)
    rows = await db.query(_PENDING, (settings.max_attempts,))
    by_user: dict[int, list[Row]] = defaultdict(list)
    for row in rows:
        user_id = int(str(row["user_id"]))
        if plan.only_users is not None:
            if user_id in plan.only_users:
                by_user[user_id].append(row)
            continue
        wants_digest = row["delivery_mode"] == "digest"
        if wants_digest == plan.digest_run and not in_quiet_hours(
            row["quiet_hours_json"], now, zone
        ):
            by_user[user_id].append(row)

    counts: Counter[str] = Counter()
    if not by_user:
        return counts
    jobs = {j.job_id: j for j in await load_jobs(db, config, _job_ids(by_user))}
    users = {u.user_id: u for u in await load_profiles(db, list(by_user))}
    for user_id, user_rows in by_user.items():
        user = users.get(user_id)
        if user is None:
            continue
        sendable = []
        for row in user_rows:
            job = jobs.get(int(str(row["job_id"])))
            if job is None:  # the job was filtered or expired since it was queued
                await _finish(db, row, "skipped", now, error="job no longer open")
                counts["skipped: job no longer open"] += 1
                continue
            first_seen = datetime.fromisoformat(str(row["first_seen_at"]).replace("Z", "+00:00"))
            fresh = freshness(
                now=now,
                posted_at=job.posted_at,
                deadline=job.deadline,
                first_seen_at=first_seen,
                facts_checked=True,
            )
            if fresh.status != "pass":
                await _finish(db, row, "skipped", now, error=f"freshness:{fresh.reason}")
                counts[f"skipped: freshness:{fresh.reason}"] += 1
                continue
            sendable.append((row, job))

        cap = int(str(user_rows[0]["max_per_cycle"]))
        chat_id = int(str(user_rows[0]["chat_id"]))
        done: set[int] = set()
        try:
            for row, job in sendable[:cap]:
                result = stored_result(row, config)
                text = renderer.alert(job, result, user, now)
                buttons = keyboard(
                    job, int(str(row["match_id"])), renderer.locale, config.app.callbacks
                )
                try:
                    message_id = await telegram.send_message(chat_id, text, buttons)
                except BlockedError:
                    raise
                except TelegramError as exc:
                    await _finish(db, row, "failed", now, error=str(exc)[:500])
                    done.add(int(str(row["notification_id"])))
                    counts["failed"] += 1
                    continue
                await _finish(db, row, "sent", now, message_id=message_id)
                done.add(int(str(row["notification_id"])))
                counts["sent"] += 1
            extras = sendable[cap:]
            if extras:
                counts.update(
                    await _send_extras(db, telegram, chat_id, extras, renderer, now, config)
                )
        except BlockedError as exc:
            await db.execute(
                "UPDATE users SET status = 'blocked', updated_at = ? WHERE id = ?",
                (timestamp(now), user_id),
            )
            left = [r for r, _ in sendable if int(str(r["notification_id"])) not in done]
            for row in left:
                await _finish(db, row, "failed", now, error=f"blocked: {exc}"[:500])
            counts["failed: user blocked the bot"] += len(left)
    return counts


async def _send_extras(
    db: Database,
    telegram: TelegramClient,
    chat_id: int,
    extras: Sequence[tuple[Row, JobView]],
    renderer: Renderer,
    now: datetime,
    config: Config,
) -> Counter[str]:
    t = renderer.locale
    listed = extras[: config.app.notifications.max_listed_extras]
    lines = [t("digest.more", count=len(extras))]
    for row, job in listed:
        company = t("digest.company", company=job.company) if job.company else ""
        score = json.loads(str(row["breakdown_json"]))["score"]
        lines.append(t("digest.item", url=job.url, title=job.title, company=company, score=score))
    if len(extras) > len(listed):
        lines.append(t("digest.and_more", count=len(extras) - len(listed)))
    try:
        message_id = await telegram.send_message(chat_id, "\n".join(lines))
    except BlockedError:
        raise
    except TelegramError as exc:
        for row, _ in extras:
            await _finish(db, row, "failed", now, error=str(exc)[:500])
        return Counter({"failed": len(extras)})
    for row, _ in extras:
        await _finish(db, row, "sent", now, message_id=message_id)
    return Counter({"sent in a list": len(extras)})


async def _finish(
    db: Database,
    row: Row,
    status: str,
    now: datetime,
    message_id: int | None = None,
    error: str | None = None,
) -> None:
    attempts = int(str(row["attempts"])) + (1 if status in {"sent", "failed"} else 0)
    await db.batch(
        [
            Statement(
                "UPDATE notifications SET status = ?, telegram_message_id = ?, attempts = ?,"
                " last_error = ?, sent_at = ? WHERE id = ?",
                (
                    status,
                    message_id,
                    attempts,
                    error,
                    timestamp(now) if status == "sent" else None,
                    int(str(row["notification_id"])),
                ),
            )
        ]
    )


def _job_ids(by_user: dict[int, list[Row]]) -> list[int]:
    return sorted({int(str(row["job_id"])) for rows in by_user.values() for row in rows})
