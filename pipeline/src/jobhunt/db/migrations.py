"""Apply migrations/ to a database the way `wrangler d1 migrations apply` does.

Wrangler records applied files in `d1_migrations` and runs each file together
with the INSERT that records it. Doing the same here means a local SQLite
database and production D1 share one schema history. In production, wrangler
applies migrations (the deploy workflow); this module serves local databases
and tests.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from jobhunt.db.base import Database

MIGRATIONS_TABLE = "d1_migrations"

# Same DDL as wrangler, so either tool can continue the other's history.
_CREATE_TABLE = f"""CREATE TABLE IF NOT EXISTS {MIGRATIONS_TABLE}(
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT UNIQUE,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
)"""

_NAME = re.compile(r"^(\d+)_[a-z0-9_]+\.sql$")


@dataclass(frozen=True)
class Migration:
    number: int
    name: str
    sql: str


def read_migrations(directory: Path) -> list[Migration]:
    """Migration files in number order. Names must look like `0001_initial.sql`."""
    migrations = []
    for path in directory.glob("*.sql"):
        match = _NAME.match(path.name)
        if not match:
            raise ValueError(f"{path}: migration names must look like 0001_name.sql")
        migrations.append(Migration(int(match[1]), path.name, path.read_text(encoding="utf-8")))
    migrations.sort(key=lambda m: m.number)
    numbers = [m.number for m in migrations]
    if numbers != list(range(1, len(numbers) + 1)):
        raise ValueError(f"migration numbers must run 1, 2, 3, ... without gaps: {numbers}")
    return migrations


async def applied_migrations(db: Database) -> list[str]:
    await db.execute(_CREATE_TABLE)
    rows = await db.query(f"SELECT name FROM {MIGRATIONS_TABLE} ORDER BY id")  # noqa: S608 (constant)
    return [str(row["name"]) for row in rows]


async def apply_migrations(db: Database, directory: Path) -> list[str]:
    """Apply every migration not applied yet. Returns the names it applied."""
    done = set(await applied_migrations(db))
    applied = []
    for migration in read_migrations(directory):
        if migration.name in done:
            continue
        name = migration.name.replace("'", "''")
        await db.execute_script(
            f"{migration.sql}\nINSERT INTO {MIGRATIONS_TABLE} (name) VALUES ('{name}');"
        )
        applied.append(migration.name)
    return applied
