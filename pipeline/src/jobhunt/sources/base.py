"""The source interface and helpers shared by the source modules."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Protocol

from jobhunt.sources.http import Fetched, Fetcher, Validators
from jobhunt.sources.models import SourceResult


@dataclass
class FetchContext:
    fetcher: Fetcher
    now: datetime
    # The source's first successful run: fetch everything, notify nobody.
    bootstrap: bool
    # Ids of this source's jobs already stored, to skip detail requests.
    known_ids: frozenset[str]
    environ: Mapping[str, str]
    # Source-owned JSON state (HTTP validators, cursors), saved after success.
    state: dict[str, Any] = field(default_factory=dict)

    async def get_if_changed(
        self, url: str, *, params: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Fetched | None:  # fmt: skip
        """GET with stored validators; None when the server answers 304."""
        stored: dict[str, Any] = self.state.setdefault("validators", {})
        previous = Validators.from_dict(stored.get(url, {}))
        response = await self.fetcher.get(url, params=params, headers=headers, validators=previous)
        if response.not_modified:
            return None
        if validators := response.validators.as_dict():
            stored[url] = validators
        else:
            stored.pop(url, None)
        return response


class Source(Protocol):
    name: str

    async def fetch(self, ctx: FetchContext) -> SourceResult: ...


# --- parsing helpers ----------------------------------------------------------


def text_or_none(value: Any) -> str | None:
    """Strip a scalar to text; empty strings and placeholder "None"/"NA" become None."""
    if value is None:
        return None
    text = str(value).strip()
    return text if text and text.casefold() not in {"none", "na", "n/a", "null"} else None


def parse_datetime(value: Any) -> datetime | None:
    """ISO 8601, RFC 2822 or a Unix timestamp, as an aware UTC datetime.

    A timestamp without an offset is taken as UTC, which is how every source
    checked so far publishes it.
    """
    text = text_or_none(value)
    if text is None:
        return None
    parsed: datetime | None
    if text.isdigit():
        parsed = datetime.fromtimestamp(int(text), UTC)
    else:
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            try:
                parsed = parsedate_to_datetime(text)
            except (TypeError, ValueError):
                return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def parse_date(value: Any) -> date | None:
    """The calendar date at the start of an ISO date or datetime string."""
    text = text_or_none(value)
    if text is None:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def string_list(value: Any) -> tuple[str, ...]:
    """A list of strings, or a comma-separated string, as a tuple of non-empty items."""
    if value is None:
        return ()
    items = value if isinstance(value, list) else str(value).split(",")
    return tuple(text for item in items if (text := text_or_none(item)))
