"""The database interface shared by the D1 (production) and SQLite (local) backends.

Both speak the SQLite dialect and run the same migrations, so code above this
layer never knows which one it has.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol

Param = str | int | float | bytes | None
Row = Mapping[str, Param]


class DatabaseError(Exception):
    """A query failed."""


class ConstraintError(DatabaseError):
    """A UNIQUE, FOREIGN KEY, CHECK, NOT NULL or column-type constraint was violated."""


# SQLite (and D1, which is SQLite) words constraint failures like this; the
# second form is a STRICT table's type check.
_CONSTRAINT_MESSAGES = ("constraint failed", "cannot store")


def error_for(message: str) -> DatabaseError:
    if any(text in message.lower() for text in _CONSTRAINT_MESSAGES):
        return ConstraintError(message)
    return DatabaseError(message)


@dataclass(frozen=True)
class Statement:
    sql: str
    params: Sequence[Param] = ()


@dataclass(frozen=True)
class Result:
    rows: list[Row] = field(default_factory=list)
    changes: int = 0
    last_row_id: int | None = None


class Database(Protocol):
    async def query(self, sql: str, params: Sequence[Param] = ()) -> list[Row]:
        """Run one statement and return its rows."""
        ...

    async def execute(self, sql: str, params: Sequence[Param] = ()) -> Result:
        """Run one statement and return its rows and write counts."""
        ...

    async def batch(self, statements: Sequence[Statement]) -> list[Result]:
        """Run statements in order as one transaction: all of them, or none."""
        ...

    async def execute_script(self, sql: str) -> None:
        """Run several `;`-separated statements as one transaction (migrations)."""
        ...

    async def aclose(self) -> None: ...
