from pathlib import Path

import pytest

from jobhunt.config import Config
from jobhunt.db import D1HttpBackend, DatabaseError, SqliteBackend, open_database

D1 = {"CLOUDFLARE_ACCOUNT_ID": "a", "D1_DATABASE_ID": "d", "CLOUDFLARE_API_TOKEN": "t"}


@pytest.mark.anyio
async def test_d1_when_configured(config: Config) -> None:
    db = open_database(config.app.database, {**D1, "JOBHUNT_SQLITE_PATH": "ignored.db"})
    assert isinstance(db, D1HttpBackend)
    await db.aclose()


def test_sqlite_otherwise(config: Config, tmp_path: Path) -> None:
    db = open_database(config.app.database, {"JOBHUNT_SQLITE_PATH": str(tmp_path / "x.db")})
    assert isinstance(db, SqliteBackend)


def test_partial_d1_config_is_an_error(config: Config) -> None:
    with pytest.raises(DatabaseError, match="missing CLOUDFLARE_API_TOKEN"):
        open_database(config.app.database, {**D1, "CLOUDFLARE_API_TOKEN": ""})


def test_nothing_configured(config: Config) -> None:
    with pytest.raises(DatabaseError, match="no database configured"):
        open_database(config.app.database, {})
