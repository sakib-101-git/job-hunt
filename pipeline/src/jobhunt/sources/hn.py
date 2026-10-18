"""Hacker News "Ask HN: Who is hiring?" through the Algolia HN API.

Only the latest monthly thread is read, and only top-level comments that match
the configured patterns (REMOTE plus a worldwide marker). Posters usually start
with "Company | Role | Location | ..."; that first line is split for the title,
company and location, and the whole comment is the description. Segments that
match `not_a_title` (links, contract types, pay) are never taken as the role.
"""

import html
import re
from typing import Any

from jobhunt.config.models import HnSource
from jobhunt.sources.base import FetchContext, parse_datetime, text_or_none
from jobhunt.sources.models import RawListing, SourceResult

_FIRST_LINE = re.compile(r"<p>|<br\s*/?>|\n", re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")
_WORK_MODE = re.compile(r"\b(remote|onsite|on-site|hybrid|in office)\b", re.IGNORECASE)


class HackerNews:
    def __init__(self, name: str, settings: HnSource) -> None:
        self.name = name
        self._s = settings
        self._require = [re.compile(p) for p in settings.require]
        self._require_any = [re.compile(p) for p in settings.require_any]
        self._not_a_title = [re.compile(p) for p in settings.not_a_title]

    async def fetch(self, ctx: FetchContext) -> SourceResult:
        stories = await ctx.fetcher.get(
            self._s.search_url,
            params={"tags": f"story,author_{self._s.thread_author}", "hitsPerPage": "10"},
        )
        thread = next(
            (
                hit
                for hit in stories.json().get("hits") or []
                if str(hit.get("title", "")).startswith(self._s.thread_title_prefix)
            ),
            None,
        )
        if thread is None:
            return SourceResult([], warnings=("no 'Who is hiring?' thread found",))
        story_id = str(thread["objectID"])
        comments = await ctx.fetcher.get(
            self._s.comments_url,
            params={"tags": f"comment,story_{story_id}", "hitsPerPage": "1000"},
        )
        listings = [
            listing
            for hit in comments.json().get("hits") or []
            if str(hit.get("parent_id")) == story_id and (listing := self._parse(hit, thread))
        ]
        return SourceResult(listings)

    def _parse(self, hit: dict[str, Any], thread: dict[str, Any]) -> RawListing | None:
        body = str(hit.get("comment_text") or "")
        if not all(p.search(body) for p in self._require):
            return None
        if self._require_any and not any(p.search(body) for p in self._require_any):
            return None
        first_line = html.unescape(_TAG.sub("", _FIRST_LINE.split(body, maxsplit=1)[0]))
        parts = [part.strip() for part in first_line.split("|") if part.strip()]
        if len(parts) < 2:
            return None
        company, rest = parts[0], parts[1:]
        modes = [part for part in rest if _WORK_MODE.search(part)]
        roles = [
            part
            for part in rest
            if part not in modes and not any(p.search(part) for p in self._not_a_title)
        ]
        # No role segment: keep the poster's own line rather than invent a title.
        title = roles[0] if roles else first_line.strip()
        return RawListing(
            source=self.name,
            source_job_id=str(hit["objectID"]),
            url=self._s.item_url.format(id=hit["objectID"]),
            title=title,
            description=body,
            description_format="html",
            company=company,
            location=" | ".join(modes) or None,
            location_restrictions=tuple(modes),
            work_mode="remote",
            posted_at=parse_datetime(hit.get("created_at")),
            posted_at_source="api",
            extra={"thread": t} if (t := text_or_none(thread.get("title"))) else {},
        )
