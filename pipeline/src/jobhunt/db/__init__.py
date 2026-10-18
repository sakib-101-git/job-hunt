"""Database access: Cloudflare D1 in production, SQLite locally."""

from collections.abc import Mapping

from jobhunt.config.models import DatabaseSettings
from jobhunt.db.base import (
    ConstraintError,
    Database,
    DatabaseError,
    Param,
    Result,
    Row,
    Statement,
    timestamp,
)
from jobhunt.db.d1 import D1HttpBackend
from jobhunt.db.migrations import apply_migrations, read_migrations
from jobhunt.db.sqlite import SqliteBackend

D1_ENV = ("CLOUDFLARE_ACCOUNT_ID", "D1_DATABASE_ID", "CLOUDFLARE_API_TOKEN")
SQLITE_ENV = "JOBHUNT_SQLITE_PATH"


def open_database(settings: DatabaseSettings, environ: Mapping[str, str]) -> Database:
    """D1 when its three variables are set, else the SQLite file in JOBHUNT_SQLITE_PATH."""
    d1 = [environ.get(name, "") for name in D1_ENV]
    if all(d1):
        account_id, database_id, api_token = d1
        return D1HttpBackend(settings, account_id, database_id, api_token)
    if any(d1):
        missing = ", ".join(name for name, value in zip(D1_ENV, d1, strict=True) if not value)
        raise DatabaseError(f"D1 is partly configured; missing {missing}")
    if path := environ.get(SQLITE_ENV):
        return SqliteBackend(path)
    raise DatabaseError(f"no database configured: set {', '.join(D1_ENV)} or {SQLITE_ENV}")


__all__ = [
    "D1_ENV",
    "SQLITE_ENV",
    "ConstraintError",
    "D1HttpBackend",
    "Database",
    "DatabaseError",
    "Param",
    "Result",
    "Row",
    "SqliteBackend",
    "Statement",
    "apply_migrations",
    "open_database",
    "read_migrations",
    "timestamp",
]
