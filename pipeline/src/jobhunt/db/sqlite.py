"""Local SQLite backend for development and tests."""

import sqlite3
from collections.abc import Sequence
from pathlib import Path

from jobhunt.db.base import Param, Result, Row, Statement, error_for


class SqliteBackend:
    """A `Database` on a local SQLite file (or ":memory:").

    Foreign keys are switched on, as D1 always enforces them. sqlite3 calls are
    synchronous; local queries are fast enough that this never matters.
    """

    def __init__(self, path: Path | str) -> None:
        self._conn = sqlite3.connect(path, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")

    def _run(self, sql: str, params: Sequence[Param]) -> Result:
        cursor = self._conn.execute(sql, tuple(params))
        rows: list[Row] = [dict(row) for row in cursor.fetchall()]
        return Result(rows, max(cursor.rowcount, 0), cursor.lastrowid)

    async def query(self, sql: str, params: Sequence[Param] = ()) -> list[Row]:
        return (await self.execute(sql, params)).rows

    async def execute(self, sql: str, params: Sequence[Param] = ()) -> Result:
        try:
            return self._run(sql, params)
        except sqlite3.Error as exc:
            raise error_for(str(exc)) from exc

    async def batch(self, statements: Sequence[Statement]) -> list[Result]:
        try:
            self._conn.execute("BEGIN")
            try:
                results = [self._run(s.sql, s.params) for s in statements]
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")
        except sqlite3.Error as exc:
            raise error_for(str(exc)) from exc
        return results

    async def execute_script(self, sql: str) -> None:
        try:
            self._conn.executescript(f"BEGIN;\n{sql}\nCOMMIT;")
        except sqlite3.Error as exc:
            if self._conn.in_transaction:
                self._conn.execute("ROLLBACK")
            raise error_for(str(exc)) from exc

    async def aclose(self) -> None:
        self._conn.close()
