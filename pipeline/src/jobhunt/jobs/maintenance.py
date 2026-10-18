"""Once-a-day housekeeping, run by the first pipeline run of each local day.

- Open jobs that are no longer fresh (brief §6.8) become `expired`: the deadline
  has passed, or the posting (or, without a posting date, the first sighting)
  is older than `max_age_days`. Send time checks freshness again anyway.
- Retention (brief §7.5): descriptions of jobs not seen for `jd_text_days` are
  cleared; their extracted facts stay.
"""

from collections import Counter
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from jobhunt.config import Config
from jobhunt.db import Database, get_kv, set_kv, timestamp

_EXPIRE = """
UPDATE jobs SET status = 'expired'
WHERE status = 'open'
  AND ((deadline IS NOT NULL AND deadline < ?1)
       OR (posted_at IS NOT NULL AND posted_at < ?2)
       OR (posted_at IS NULL AND first_seen_at < ?2))
"""

_CLEAR_TEXT = "UPDATE jobs SET jd_text = NULL WHERE jd_text IS NOT NULL AND last_seen_at < ?"


async def daily_maintenance(db: Database, config: Config, now: datetime) -> Counter[str] | None:
    """Run the housekeeping if it hasn't run today; None when it already had."""
    today = now.astimezone(ZoneInfo(config.app.timezone)).date().isoformat()
    key = config.app.maintenance.kv_key
    if await get_kv(db, key) == today:
        return None
    max_age = timedelta(days=config.app.freshness.max_age_days)
    retention = timedelta(days=config.app.retention.jd_text_days)
    expired = await db.execute(_EXPIRE, (today, timestamp(now - max_age)))
    cleared = await db.execute(_CLEAR_TEXT, (timestamp(now - retention),))
    await set_kv(db, key, today, now)
    return Counter({"jobs expired": expired.changes, "descriptions cleared": cleared.changes})
