"""The notification outbox (brief §6.9): one pending row per new match at or
above the user's minimum score.

`notifications` is unique per (user, job fingerprint), so a job seen on two
boards, or matched again by an overlapping run, is queued once. Seeded jobs
(a source's first run) and users who paused, blocked the bot or deleted their
data get no rows.

A user's first matches are their backfill (brief §6.10): only the best
`backfill_top_n` of them are queued, seeded jobs included, whether the user's
backfill job or a scheduled run scores them first. The rest stay stored as
matches and are never queued, so a new user is not flooded.
"""

from collections import Counter
from collections.abc import Collection, Sequence
from datetime import datetime

from jobhunt.db import Database, Statement, timestamp
from jobhunt.score.matching import NewMatch

_QUEUE = """
INSERT INTO notifications (match_id, user_id, job_id, fingerprint, created_at)
SELECT m.id, m.user_id, m.job_id, j.fingerprint, ?1
FROM matches m
JOIN jobs j ON j.id = m.job_id
JOIN users u ON u.id = m.user_id
JOIN user_prefs p ON p.user_id = m.user_id
WHERE m.id = ?2
  AND u.status = 'active'
  AND m.score >= p.min_score
  AND (?3 OR COALESCE(json_extract(j.gates_json, '$.seeded'), 0) = 0)
ON CONFLICT DO NOTHING
RETURNING id
"""


async def first_time_users(db: Database, user_ids: Collection[int] | None = None) -> set[int]:
    """Users with prefs and no stored match yet: their next matches are a backfill."""
    rows = await db.query(
        "SELECT p.user_id FROM user_prefs p"
        " WHERE NOT EXISTS (SELECT 1 FROM matches m WHERE m.user_id = p.user_id)"
    )
    found = {int(str(row["user_id"])) for row in rows}
    return found if user_ids is None else found & set(user_ids)


def split_backfill(
    matches: Sequence[NewMatch], first_time: Collection[int], top_n: int
) -> tuple[list[NewMatch], list[NewMatch]]:
    """(regular matches, the best `top_n` per first-time user)."""
    regular = [m for m in matches if m.user_id not in first_time]
    backfill: list[NewMatch] = []
    for user_id in sorted(set(first_time)):
        mine = [m for m in matches if m.user_id == user_id]
        backfill += sorted(mine, key=lambda m: (-m.score, m.match_id))[:top_n]
    return regular, backfill


async def enqueue(
    db: Database, matches: Sequence[NewMatch], now: datetime, *, include_seeded: bool = False
) -> Counter[str]:
    if not matches:
        return Counter()
    stamp, seeded = timestamp(now), int(include_seeded)
    results = await db.batch([Statement(_QUEUE, (stamp, m.match_id, seeded)) for m in matches])
    queued = sum(1 for result in results if result.rows)
    return Counter({"queued": queued, "not queued": len(matches) - queued})


async def queue_matches(
    db: Database,
    matches: Sequence[NewMatch],
    first_time: Collection[int],
    top_n: int,
    now: datetime,
) -> Counter[str]:
    """Queue new matches: the regular ones, then each first-time user's best."""
    regular, backfill = split_backfill(matches, first_time, top_n)
    counts = await enqueue(db, regular, now)
    backfilled = await enqueue(db, backfill, now, include_seeded=True)
    counts.update(backfilled)
    if backfilled["queued"]:
        counts["queued (backfill)"] = backfilled["queued"]
    left = len(matches) - len(regular) - len(backfill)
    if left:
        counts["not queued: beyond backfill"] += left
    return counts
