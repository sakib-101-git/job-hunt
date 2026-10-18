from pathlib import Path

import pytest

from jobhunt.db import ConstraintError, SqliteBackend, Statement
from jobhunt.db.migrations import apply_migrations, read_migrations

pytestmark = pytest.mark.anyio


@pytest.fixture
async def db() -> SqliteBackend:
    backend = SqliteBackend(":memory:")
    await backend.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT UNIQUE) STRICT")
    return backend


async def test_execute_reports_changes_and_row_id(db: SqliteBackend) -> None:
    result = await db.execute("INSERT INTO t (name) VALUES (?)", ("a",))
    assert (result.changes, result.last_row_id) == (1, 1)
    assert await db.query("SELECT id, name FROM t") == [{"id": 1, "name": "a"}]


async def test_batch_is_all_or_nothing(db: SqliteBackend) -> None:
    insert = "INSERT INTO t (name) VALUES (?)"
    results = await db.batch([Statement(insert, ("a",)), Statement(insert, ("b",))])
    assert [r.last_row_id for r in results] == [1, 2]

    with pytest.raises(ConstraintError):
        await db.batch([Statement(insert, ("c",)), Statement(insert, ("a",))])
    assert [row["name"] for row in await db.query("SELECT name FROM t ORDER BY id")] == ["a", "b"]


async def test_script_is_all_or_nothing(db: SqliteBackend) -> None:
    with pytest.raises(ConstraintError):
        await db.execute_script(
            "INSERT INTO t (name) VALUES ('x'); INSERT INTO t (name) VALUES ('x');"
        )
    assert await db.query("SELECT name FROM t") == []
    await db.execute_script("INSERT INTO t (name) VALUES ('y');")
    assert await db.query("SELECT name FROM t") == [{"name": "y"}]


async def test_data_persists_in_a_file(tmp_path: Path) -> None:
    path = tmp_path / "local.db"
    first = SqliteBackend(path)
    await first.execute("CREATE TABLE kv (k TEXT PRIMARY KEY, v TEXT)")
    await first.execute("INSERT INTO kv VALUES ('a', 'b')")
    await first.aclose()
    second = SqliteBackend(path)
    assert await second.query("SELECT v FROM kv") == [{"v": "b"}]
    await second.aclose()


@pytest.mark.parametrize(
    ("files", "message"),
    [
        (["0001_a.sql", "0003_c.sql"], "without gaps"),
        (["0001_a.sql", "initial.sql"], "must look like"),
        (["0001_A.sql"], "must look like"),
    ],
)
def test_bad_migration_names(tmp_path: Path, files: list[str], message: str) -> None:
    for name in files:
        (tmp_path / name).write_text("SELECT 1;", encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        read_migrations(tmp_path)


async def test_failed_migration_is_not_recorded(tmp_path: Path) -> None:
    (tmp_path / "0001_ok.sql").write_text("CREATE TABLE a (x INTEGER);", encoding="utf-8")
    (tmp_path / "0002_bad.sql").write_text(
        "CREATE TABLE b (x INTEGER); INSERT INTO missing VALUES (1);", encoding="utf-8"
    )
    db = SqliteBackend(":memory:")
    with pytest.raises(Exception, match="no such table"):
        await apply_migrations(db, tmp_path)
    tables = {str(r["name"]) for r in await db.query("SELECT name FROM sqlite_schema")}
    assert "a" in tables
    assert "b" not in tables
    names = [r["name"] for r in await db.query("SELECT name FROM d1_migrations")]
    assert names == ["0001_ok.sql"]
