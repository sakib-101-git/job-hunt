"""OpenAI-compatible chat completions with strict JSON-schema output, which both
Gemini and Groq serve (config/llm.yaml)."""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from jobhunt.config.models import ChainEntry, Llm

_USER_MARKER = "<!-- user -->"
_RETRY_MARKER = "<!-- retry -->"


class LlmError(Exception):
    """The model could not answer; try the next one."""


class RateLimitedError(LlmError):
    """HTTP 429: this model's limit is reached for now."""


@dataclass(frozen=True)
class Prompt:
    """A prompt file: the system message, then `<!-- user -->` and the user
    message template, then `<!-- retry -->` and the note sent after an answer
    that failed validation. `{{name}}` placeholders are filled by `render`."""

    system: str
    user: str
    retry: str

    @classmethod
    def load(cls, path: Path) -> "Prompt":
        text = path.read_text(encoding="utf-8")
        system, _, rest = text.partition(_USER_MARKER)
        user, _, retry = rest.partition(_RETRY_MARKER)
        return cls(system.strip(), user.strip(), retry.strip())

    def render(self, **values: str) -> tuple[str, str]:
        return _fill(self.system, values), _fill(self.user, values)

    def retry_note(self, error: str) -> str:
        return _fill(self.retry, {"error": error})


def _fill(template: str, values: dict[str, str]) -> str:
    for key, value in values.items():
        template = template.replace("{{" + key + "}}", value)
    return template


@dataclass(frozen=True)
class Answer:
    data: Any
    tokens_in: int
    tokens_out: int


class LlmClient:
    def __init__(
        self,
        settings: Llm,
        environ: Mapping[str, str],
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._s = settings
        self._environ = environ
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=settings.request.timeout_seconds)

    def available(self, entry: ChainEntry) -> bool:
        return bool(self._environ.get(self._s.providers[entry.provider].api_key_env))

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def complete_json(
        self, entry: ChainEntry, system: str, user: str, name: str, schema: dict[str, Any]
    ) -> Answer:
        provider = self._s.providers[entry.provider]
        body: dict[str, Any] = {
            "model": entry.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self._s.request.temperature,
            "max_tokens": self._s.request.max_output_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": name, "strict": True, "schema": schema},
            },
            **entry.params,
        }
        headers = {"Authorization": f"Bearer {self._environ[provider.api_key_env]}"}
        try:
            response = await self._client.post(
                f"{provider.base_url.rstrip('/')}/chat/completions", json=body, headers=headers
            )
        except httpx.HTTPError as exc:
            raise LlmError(f"{entry.key}: {type(exc).__name__}: {exc}") from exc
        if response.status_code == httpx.codes.TOO_MANY_REQUESTS:
            raise RateLimitedError(f"{entry.key}: rate limited")
        if not response.is_success:
            raise LlmError(f"{entry.key}: HTTP {response.status_code}: {_error_code(response)}")
        try:
            payload = response.json()
            content = payload["choices"][0]["message"]["content"]
            data = json.loads(content)
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LlmError(f"{entry.key}: unreadable answer: {exc}") from exc
        usage = payload.get("usage") or {}
        return Answer(
            data, int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)
        )


def _error_code(response: httpx.Response) -> str:
    """The provider's error code or type, never its message: a rejected answer
    comes back quoted in the message (Groq's `failed_generation`), and for a CV
    that is personal data headed for public CI logs."""
    try:
        error = response.json().get("error") or {}
    except (ValueError, AttributeError):
        return "no error details"
    if isinstance(error, list):  # Gemini's OpenAI layer wraps it in a list
        error = error[0] if error and isinstance(error[0], dict) else {}
    if not isinstance(error, dict):
        return "no error details"
    return str(error.get("code") or error.get("type") or error.get("status") or "error")[:80]
