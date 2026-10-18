"""The notification outbox (brief §6.9): one pending row per new match at or
above the user's minimum score.

`notifications` is unique per (user, job fingerprint), so a job seen on two
boards, or matched again by an overlapping run, is queued once. Seeded jobs
(a source's first run) and users who paused, blocked the bot or deleted their
data get no rows.
"""

from collections import Counter
from collections.abc import Sequence
from datetime import datetime

from jobhunt.db import Database, Statement, timestamp
from jobhunt.score.matching import NewMatch

_QUEUE = """
INSERT INTO notifications (match_id, user_id, job_id, fingerprint, created_at)
SELECT m.id, m.user_id, m.job_id, j.fingerprint, ?
FROM matches m
JOIN jobs j ON j.id = m.job_id
JOIN users u ON u.id = m.user_id
JOIN user_prefs p ON p.user_id = m.user_id
WHERE m.id = ?
  AND u.status = 'active'
  AND m.score >= p.min_score
  AND COALESCE(json_extract(j.gates_json, '$.seeded'), 0) = 0
ON CONFLICT DO NOTHING
RETURNING id
"""


async def enqueue(db: Database, matches: Sequence[NewMatch], now: datetime) -> Counter[str]:
    if not matches:
        return Counter()
    results = await db.batch([Statement(_QUEUE, (timestamp(now), m.match_id)) for m in matches])
    queued = sum(1 for result in results if result.rows)
    return Counter({"queued": queued, "not queued": len(matches) - queued})
