import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from jobhunt.config import Config
from jobhunt.db import ConstraintError, D1HttpBackend, DatabaseError, Statement

pytestmark = pytest.mark.anyio

Handler = Callable[[httpx.Request], httpx.Response]


def _ok(*results: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, json={"success": True, "errors": [], "result": list(results)})


def _rows(rows: list[dict[str, Any]], changes: int = 0, last_row_id: int = 0) -> dict[str, Any]:
    return {
        "results": rows,
        "success": True,
        "meta": {"changes": changes, "last_row_id": last_row_id},
    }


class Recorder:
    def __init__(self, *responses: httpx.Response | Exception) -> None:
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def body(self, index: int = 0) -> Any:
        return json.loads(self.requests[index].content)


@pytest.fixture
def sleeps() -> list[float]:
    return []


def _backend(config: Config, handler: Handler, sleeps: list[float]) -> D1HttpBackend:
    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return D1HttpBackend(config.app.database, "acc", "db1", "token", client=client, sleep=sleep)


async def test_query_request_and_rows(config: Config, sleeps: list[float]) -> None:
    api = Recorder(_ok(_rows([{"id": 1, "name": "a"}])))
    db = _backend(config, api, sleeps)
    assert await db.query("SELECT * FROM t WHERE id = ? AND ok = ?", (1, True)) == [
        {"id": 1, "name": "a"}
    ]
    request = api.requests[0]
    assert request.method == "POST"
    assert str(request.url) == (
        "https://api.cloudflare.com/client/v4/accounts/acc/d1/database/db1/query"
    )
    assert request.headers["authorization"] == "Bearer token"
    assert api.body() == {"sql": "SELECT * FROM t WHERE id = ? AND ok = ?", "params": [1, 1]}


async def test_execute_meta(config: Config, sleeps: list[float]) -> None:
    db = _backend(config, Recorder(_ok(_rows([], changes=1, last_row_id=7))), sleeps)
    result = await db.execute("INSERT INTO t (name) VALUES (?)", ("a",))
    assert (result.changes, result.last_row_id, result.rows) == (1, 7, [])


async def test_batch_sends_one_request(config: Config, sleeps: list[float]) -> None:
    api = Recorder(_ok(_rows([], 1, 1), _rows([], 1, 2)))
    db = _backend(config, api, sleeps)
    results = await db.batch([Statement("INSERT 1", ("a",)), Statement("INSERT 2", (None,))])
    assert [r.last_row_id for r in results] == [1, 2]
    assert api.body() == {
        "batch": [{"sql": "INSERT 1", "params": ["a"]}, {"sql": "INSERT 2", "params": [None]}]
    }
    assert await db.batch([]) == []
    assert len(api.requests) == 1


async def test_batch_result_count_must_match(config: Config, sleeps: list[float]) -> None:
    db = _backend(config, Recorder(_ok(_rows([]))), sleeps)
    with pytest.raises(DatabaseError, match="1 results for 2"):
        await db.batch([Statement("A"), Statement("B")])


async def test_script_sends_raw_sql(config: Config, sleeps: list[float]) -> None:
    api = Recorder(_ok(_rows([]), _rows([])))
    await _backend(config, api, sleeps).execute_script("CREATE TABLE a (x); CREATE TABLE b (x);")
    assert api.body() == {"sql": "CREATE TABLE a (x); CREATE TABLE b (x);"}


async def test_constraint_errors_are_typed(config: Config, sleeps: list[float]) -> None:
    error = {"code": 7500, "message": "UNIQUE constraint failed: users.id: SQLITE_CONSTRAINT"}
    response = httpx.Response(400, json={"success": False, "errors": [error], "result": []})
    with pytest.raises(ConstraintError, match="UNIQUE"):
        await _backend(config, Recorder(response), sleeps).execute("INSERT ...")


async def test_other_errors_are_not_retried(config: Config, sleeps: list[float]) -> None:
    response = httpx.Response(403, json={"success": False, "errors": [], "result": []})
    api = Recorder(response)
    with pytest.raises(DatabaseError, match="HTTP 403"):
        await _backend(config, api, sleeps).execute("SELECT 1")
    assert len(api.requests) == 1
    assert sleeps == []


async def test_rate_limit_and_server_errors_are_retried(
    config: Config, sleeps: list[float]
) -> None:
    api = Recorder(
        httpx.Response(429, headers={"retry-after": "7"}, json={"success": False}),
        httpx.Response(503, text="unavailable"),
        httpx.ConnectError("reset"),
        _ok(_rows([{"x": 1}])),
    )
    assert await _backend(config, api, sleeps).query("SELECT 1 AS x") == [{"x": 1}]
    base = config.app.database.retry_base_delay_seconds
    assert sleeps == [7.0, base * 2, base * 4]


async def test_retries_give_up(config: Config, sleeps: list[float]) -> None:
    retries = config.app.database.max_retries
    api = Recorder(*[httpx.Response(502, text="bad gateway") for _ in range(retries + 1)])
    with pytest.raises(DatabaseError, match="without JSON"):
        await _backend(config, api, sleeps).execute("SELECT 1")
    assert len(api.requests) == retries + 1

    api = Recorder(*[httpx.ReadTimeout("slow") for _ in range(retries + 1)])
    with pytest.raises(DatabaseError, match="request failed"):
        await _backend(config, api, sleeps).execute("SELECT 1")


async def test_bad_retry_after_falls_back_to_backoff(config: Config, sleeps: list[float]) -> None:
    api = Recorder(httpx.Response(429, headers={"retry-after": "soon"}), _ok(_rows([])))
    await _backend(config, api, sleeps).execute("SELECT 1")
    assert sleeps == [config.app.database.retry_base_delay_seconds]


async def test_limits_are_checked_before_sending(config: Config, sleeps: list[float]) -> None:
    api = Recorder()
    db = _backend(config, api, sleeps)
    limit = config.app.database.max_params_per_statement
    with pytest.raises(DatabaseError, match="parameters"):
        await db.execute("SELECT ?", tuple(range(limit + 1)))
    with pytest.raises(DatabaseError, match="byte limit"):
        await db.execute("x" * (config.app.database.max_statement_bytes + 1))
    with pytest.raises(DatabaseError, match="BLOB"):
        await db.execute("SELECT ?", (b"\x00",))
    assert api.requests == []


@pytest.mark.parametrize(
    "body",
    [{"success": True, "result": []}, {"success": False, "errors": [{"message": "boom"}]}],
)
async def test_unusable_responses(
    config: Config, sleeps: list[float], body: dict[str, Any]
) -> None:
    with pytest.raises(DatabaseError):
        await _backend(config, Recorder(httpx.Response(200, json=body)), sleeps).execute("x")


async def test_owned_client_is_closed(config: Config) -> None:
    db = D1HttpBackend(config.app.database, "acc", "db1", "token")
    await db.aclose()
