"""RSS feeds in We Work Remotely's format.

Items carry <region>, <country>, <state> and <type> besides the standard fields;
the title reads "Company: Job title". <region> is the eligibility WWR publishes
("Anywhere in the World", "USA Only", ...), so it is kept, never assumed (D4).
Python's bundled expat refuses entity-expansion attacks, so the standard
library parser is safe for these feeds.
"""

import xml.etree.ElementTree as ET

from jobhunt.config.models import RssSource
from jobhunt.sources.base import FetchContext, parse_datetime, string_list, text_or_none
from jobhunt.sources.http import FetchError
from jobhunt.sources.models import RawListing, SourceResult


class RssFeeds:
    def __init__(self, name: str, settings: RssSource) -> None:
        self.name = name
        self._s = settings

    async def fetch(self, ctx: FetchContext) -> SourceResult:
        listings: dict[str, RawListing] = {}
        unchanged = 0
        for url in self._s.feeds:
            response = await ctx.get_if_changed(url)
            if response is None:
                unchanged += 1
                continue
            try:
                root = ET.fromstring(response.content)  # noqa: S314 (see module docstring)
            except ET.ParseError as exc:
                raise FetchError(url, f"invalid RSS: {exc}") from exc
            for item in root.iterfind("./channel/item"):
                listing = self._parse(item)
                if listing:
                    listings.setdefault(listing.source_job_id, listing)
        return SourceResult(list(listings.values()), unchanged=unchanged == len(self._s.feeds))

    def _parse(self, item: ET.Element) -> RawListing | None:
        link = text_or_none(item.findtext("link"))
        guid = text_or_none(item.findtext("guid")) or link
        raw_title = text_or_none(item.findtext("title"))
        if not (link and guid and raw_title):
            return None
        company, _, title = raw_title.partition(": ")
        if not title:
            company, title = "", raw_title
        restrictions = tuple(
            text
            for tag in ("region", "country", "state")
            for text in string_list(item.findtext(tag))
        )
        return RawListing(
            source=self.name,
            source_job_id=guid,
            url=link,
            title=title.strip(),
            description=item.findtext("description") or "",
            description_format="html",
            company=company.strip() or None,
            location=", ".join(restrictions) or None,
            location_restrictions=restrictions,
            work_mode="remote",
            employment_type=text_or_none(item.findtext("type")),
            posted_at=parse_datetime(item.findtext("pubDate")),
            posted_at_source="rss",
            tags=string_list(item.findtext("category")),
            extra={"expires_at": e} if (e := text_or_none(item.findtext("expires_at"))) else {},
        )
