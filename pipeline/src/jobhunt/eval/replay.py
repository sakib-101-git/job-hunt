"""Recorded LLM answers, so the evaluation runs in CI without keys and gives
the same result every time (brief §8.3).

An answer is keyed by a hash of the request's system and user messages, so a
changed prompt or posting misses the recording, and the evaluator falls back to
rules-only facts (and the metrics show it). `RecordingLlmTransport` forwards
requests to the real provider and keeps each answer; `ReplayLlmTransport`
serves the kept answers and answers 404 for anything else.
"""

import hashlib
import json
from pathlib import Path
from typing import Any

import httpx

FILE_NAME = "llm_answers.jsonl"


def request_key(body: dict[str, Any]) -> str:
    messages = body.get("messages") or []
    parts = [str(m.get("content", "")) for m in messages if m.get("role") in {"system", "user"}]
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def load_answers(path: Path) -> dict[str, dict[str, Any]]:
    if not path.is_file():
        return {}
    lines = path.read_text(encoding="utf-8").split("\n")  # not splitlines(): see dataset.py
    rows = [json.loads(line) for line in lines if line]
    return {row["key"]: row for row in rows}


def save_answers(path: Path, answers: dict[str, dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        json.dumps(answers[key], ensure_ascii=False, sort_keys=True) for key in sorted(answers)
    ]
    path.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")


def _completion(content: str) -> httpx.Response:
    payload = {
        "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0},
    }
    return httpx.Response(200, json=payload)


class ReplayLlmTransport(httpx.AsyncBaseTransport):
    def __init__(self, answers: dict[str, dict[str, Any]]) -> None:
        self._answers = answers
        self.hits = 0
        self.misses = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        answer = self._answers.get(request_key(json.loads(request.content)))
        if answer is None:
            self.misses += 1
            return httpx.Response(404, json={"error": "no recorded answer"})
        self.hits += 1
        return _completion(answer["content"])


class RecordingLlmTransport(httpx.AsyncBaseTransport):
    """Serves answers recorded before, sends other requests on, and keeps every
    successful answer by request key. Free-tier daily limits allow only part of
    the golden set per day, so each run adds to the recordings instead of
    spending the quota on answers it already has."""

    def __init__(
        self,
        inner: httpx.AsyncBaseTransport | None = None,
        known: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self._inner = inner or httpx.AsyncHTTPTransport()
        self.answers: dict[str, dict[str, Any]] = dict(known or {})
        self.reused = 0
        self.new = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if (known := self.answers.get(request_key(body))) is not None:
            self.reused += 1
            return _completion(known["content"])
        response = await self._inner.handle_async_request(request)
        if response.status_code != httpx.codes.OK:
            return response
        raw = await response.aread()
        try:
            content = json.loads(raw)["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError):
            content = None
        if isinstance(content, str):
            self.new += 1
            self.answers[request_key(body)] = {
                "key": request_key(body),
                "model": body.get("model"),
                "content": content,
            }
        # `raw` is already decoded, so the provider's encoding headers don't apply.
        return httpx.Response(200, headers={"content-type": "application/json"}, content=raw)

    async def aclose(self) -> None:
        await self._inner.aclose()
