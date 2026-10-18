"""Cloudflare D1 over the REST API, for the pipeline on GitHub Actions.

`POST {base}/accounts/{account}/d1/database/{database}/query` takes either
`{"sql", "params"}` or `{"batch": [{"sql", "params"}, ...]}`; batches run as one
transaction. The API allows 1,200 requests per five minutes per user and
answers 429 with Retry-After when that is exceeded (docs/DECISIONS.md).
"""

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

import httpx

from jobhunt.config.models import DatabaseSettings
from jobhunt.db.base import DatabaseError, Param, Result, Row, Statement, error_for

Sleep = Callable[[float], Awaitable[None]]


class D1HttpBackend:
    def __init__(
        self,
        settings: DatabaseSettings,
        account_id: str,
        database_id: str,
        api_token: str,
        client: httpx.AsyncClient | None = None,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._settings = settings
        base = settings.d1_api_base_url.rstrip("/")
        self._url = f"{base}/accounts/{account_id}/d1/database/{database_id}/query"
        self._headers = {"Authorization": f"Bearer {api_token}"}
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=settings.request_timeout_seconds)
        self._sleep = sleep

    # --- Database -------------------------------------------------------------

    async def query(self, sql: str, params: Sequence[Param] = ()) -> list[Row]:
        return (await self.execute(sql, params)).rows

    async def execute(self, sql: str, params: Sequence[Param] = ()) -> Result:
        results = await self._post(self._statement(Statement(sql, params)))
        return results[0]

    async def batch(self, statements: Sequence[Statement]) -> list[Result]:
        if not statements:
            return []
        body = {"batch": [self._statement(s) for s in statements]}
        results = await self._post(body)
        if len(results) != len(statements):
            raise DatabaseError(f"D1 returned {len(results)} results for {len(statements)}")
        return results

    async def execute_script(self, sql: str) -> None:
        await self._post({"sql": sql})

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    # --- helpers --------------------------------------------------------------

    def _statement(self, statement: Statement) -> dict[str, Any]:
        if len(statement.params) > self._settings.max_params_per_statement:
            raise DatabaseError(
                f"{len(statement.params)} parameters; D1 allows "
                f"{self._settings.max_params_per_statement} per statement"
            )
        if len(statement.sql.encode("utf-8")) > self._settings.max_statement_bytes:
            raise DatabaseError(
                f"statement is over D1's {self._settings.max_statement_bytes}-byte limit"
            )
        return {"sql": statement.sql, "params": [_json_param(p) for p in statement.params]}

    async def _post(self, body: dict[str, Any]) -> list[Result]:
        attempt = 0
        while True:
            try:
                response = await self._client.post(self._url, json=body, headers=self._headers)
            except httpx.TransportError as exc:
                if attempt >= self._settings.max_retries:
                    raise DatabaseError(f"D1 request failed: {exc}") from exc
                await self._sleep(self._backoff(attempt, None))
                attempt += 1
                continue
            if _retryable(response.status_code) and attempt < self._settings.max_retries:
                await self._sleep(self._backoff(attempt, response.headers.get("retry-after")))
                attempt += 1
                continue
            return _parse(response)

    def _backoff(self, attempt: int, retry_after: str | None) -> float:
        if retry_after is not None:
            try:
                return max(float(retry_after), 0.0)
            except ValueError:
                pass
        return float(self._settings.retry_base_delay_seconds * 2**attempt)


def _json_param(value: Param) -> str | int | float | None:
    if isinstance(value, bytes):
        raise DatabaseError("D1's REST API cannot carry BLOB parameters")
    if isinstance(value, bool):
        return int(value)
    return value


def _retryable(status: int) -> bool:
    return status == httpx.codes.TOO_MANY_REQUESTS or status >= httpx.codes.INTERNAL_SERVER_ERROR


def _parse(response: httpx.Response) -> list[Result]:
    try:
        data = response.json()
    except ValueError as exc:
        raise DatabaseError(f"D1 returned HTTP {response.status_code} without JSON") from exc
    if not response.is_success or not data.get("success", False):
        messages = [e.get("message", "") for e in data.get("errors") or []]
        detail = "; ".join(m for m in messages if m) or f"HTTP {response.status_code}"
        raise error_for(detail)
    results = []
    for item in data.get("result") or []:
        meta = item.get("meta") or {}
        results.append(
            Result(
                rows=list(item.get("results") or []),
                changes=int(meta.get("changes") or 0),
                last_row_id=meta.get("last_row_id"),
            )
        )
    if not results:
        raise DatabaseError("D1 returned no results")
    return results
