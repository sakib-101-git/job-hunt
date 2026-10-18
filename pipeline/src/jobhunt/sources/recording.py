"""Record live responses to a directory, and replay them in tests.

`jobhunt fetch --record DIR` saves every response a source receives; tests
replay the directory through the same Fetcher, so parsers and pagination are
tested end to end without network access.
"""

import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit, urlunsplit

import httpx

INDEX = "index.json"


def request_key(method: str, url: httpx.URL) -> str:
    """Method and URL with query parameters sorted, so order never matters."""
    parts = urlsplit(str(url))
    query = urlencode(sorted(url.params.multi_items()))
    return f"{method} {urlunsplit((parts.scheme, parts.netloc, parts.path, query, ''))}"


class RecordingTransport(httpx.AsyncBaseTransport):
    def __init__(self, directory: Path, inner: httpx.AsyncBaseTransport | None = None) -> None:
        self._dir = directory
        self._inner = inner or httpx.AsyncHTTPTransport(http2=True)
        self._dir.mkdir(parents=True, exist_ok=True)
        index_path = self._dir / INDEX
        self._index: dict[str, Any] = (
            json.loads(index_path.read_text("utf-8")) if index_path.exists() else {}
        )

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await self._inner.handle_async_request(request)
        body = await response.aread()  # decoded: fixtures stay readable
        key = request_key(request.method, request.url)
        name = hashlib.sha1(key.encode(), usedforsecurity=False).hexdigest()[:12] + ".body"
        (self._dir / name).write_bytes(body)
        self._index[key] = {
            "file": name,
            "status": response.status_code,
            "content_type": response.headers.get("content-type", ""),
        }
        (self._dir / INDEX).write_text(json.dumps(self._index, indent=2, sort_keys=True), "utf-8")
        headers = [
            (header, value)
            for header, value in response.headers.multi_items()
            if header.lower() not in {"content-encoding", "content-length", "transfer-encoding"}
        ]
        return httpx.Response(response.status_code, headers=headers, content=body, request=request)

    async def aclose(self) -> None:
        await self._inner.aclose()


class ReplayTransport(httpx.AsyncBaseTransport):
    """Answers from a recorded directory; an unrecorded request is an error."""

    def __init__(self, directory: Path) -> None:
        self._dir = directory
        self._index: dict[str, Any] = json.loads((directory / INDEX).read_text("utf-8"))
        self.requests: list[str] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        key = request_key(request.method, request.url)
        self.requests.append(key)
        entry = self._index.get(key)
        if entry is None:
            raise httpx.ConnectError(f"no recording for {key}", request=request)
        return httpx.Response(
            entry["status"],
            headers={"content-type": entry["content_type"]},
            content=(self._dir / entry["file"]).read_bytes(),
            request=request,
        )
