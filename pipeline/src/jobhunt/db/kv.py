"""The `kv` table: small named values (heartbeat, once-a-day markers)."""

from datetime import datetime

from jobhunt.db.base import Database, timestamp

_SET = """
INSERT INTO kv (key, value, updated_at) VALUES (?1, ?2, ?3)
ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
"""


async def get_kv(db: Database, key: str) -> str | None:
    rows = await db.query("SELECT value FROM kv WHERE key = ?", (key,))
    return str(rows[0]["value"]) if rows else None


async def set_kv(db: Database, key: str, value: str, now: datetime) -> None:
    await db.execute(_SET, (key, value, timestamp(now)))
