"""Himalayas search API, filtered to jobs open to Bangladesh (config `params`).

Terms (clause 113): Apply must use the job's applicationLink and Himalayas must
be credited with a visible link, so `url` is the applicationLink.

An empty `locationRestrictions` list is Himalayas' published way of saying the
job has no location restriction; it is recorded as `location_policy:
worldwide`, the structured evidence the location gate needs.
"""

import json
from typing import Any

from jobhunt.config.models import HimalayasSource
from jobhunt.sources.base import FetchContext, parse_datetime, text_or_none
from jobhunt.sources.models import RawListing, SourceResult


class Himalayas:
    def __init__(self, name: str, settings: HimalayasSource) -> None:
        self.name = name
        self._s = settings

    async def fetch(self, ctx: FetchContext) -> SourceResult:
        headers = {}
        if self._s.api_key_env and (key := ctx.environ.get(self._s.api_key_env)):
            headers["Authorization"] = f"Bearer {key}"
        listings: dict[str, RawListing] = {}
        for page in range(1, self._s.max_pages + 1):
            response = await ctx.fetcher.get(
                self._s.url, params={**self._s.params, "page": str(page)}, headers=headers
            )
            jobs = response.json().get("jobs") or []
            for job in jobs:
                if listing := self._parse(job):
                    listings.setdefault(listing.source_job_id, listing)
            if not jobs:
                break
        return SourceResult(list(listings.values()))

    def _parse(self, job: dict[str, Any]) -> RawListing | None:
        guid, title = text_or_none(job.get("guid")), text_or_none(job.get("title"))
        url = text_or_none(job.get("applicationLink")) or guid
        if not (guid and title and url):
            return None
        restrictions = _list(job.get("locationRestrictions"))
        timezones = _list(job.get("timezoneRestrictions"))
        extra = {
            key: value
            for key, value in (
                ("seniority", ", ".join(_list(job.get("seniority")))),
                ("timezones", ", ".join(timezones)),
                ("expires_at", text_or_none(job.get("expiryDate"))),
                ("location_policy", None if restrictions else "worldwide"),
            )
            if value
        }
        return RawListing(
            source=self.name,
            source_job_id=guid,
            url=url,
            title=title,
            description=str(job.get("description") or ""),
            description_format="html",
            company=text_or_none(job.get("companyName")),
            location=", ".join(restrictions) or None,
            location_restrictions=restrictions,
            work_mode="remote",
            employment_type=text_or_none(job.get("employmentType")),
            posted_at=parse_datetime(job.get("pubDate")),
            posted_at_source="api",
            salary=_salary(job),
            tags=_list(job.get("categories")),
            extra=extra,
        )


def _list(value: Any) -> tuple[str, ...]:
    """Lists arrive as JSON arrays; accept a JSON-encoded string too."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            value = [value]
    if not isinstance(value, list):
        return ()
    return tuple(text for item in value if (text := text_or_none(item)))


def _salary(job: dict[str, Any]) -> str | None:
    low, high = text_or_none(job.get("minSalary")), text_or_none(job.get("maxSalary"))
    if not (low or high):
        return None
    currency = text_or_none(job.get("currency")) or ""
    period = text_or_none(job.get("salaryPeriod")) or ""
    return f"{low or '?'}-{high or '?'} {currency} {period}".strip()
