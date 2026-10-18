"""Remote job boards with simple JSON feeds: Remote OK, Jobicy, Working Nomads,
Remotive. Each returns its whole current list in one request, so conditional
GETs avoid re-downloading an unchanged feed."""

import re
from typing import Any

from jobhunt.config.models import (
    JobicySource,
    RemoteokSource,
    RemotiveSource,
    UrlSource,
    WorkingNomadsSource,
)
from jobhunt.sources.base import FetchContext, parse_datetime, string_list, text_or_none
from jobhunt.sources.models import RawListing, SourceResult

_WORKINGNOMADS_ID = re.compile(r"/job/go/(\d+)")


class _JsonFeed:
    settings: UrlSource

    def __init__(self, name: str, settings: UrlSource) -> None:
        self.name = name
        self.settings = settings

    async def fetch(self, ctx: FetchContext) -> SourceResult:
        response = await ctx.get_if_changed(self.settings.url, params=self.settings.params or None)
        if response is None:
            return SourceResult([], unchanged=True)
        listings = [
            listing for item in self.items(response.json()) if (listing := self.parse(item))
        ]
        return SourceResult(listings)

    def items(self, data: Any) -> list[dict[str, Any]]:
        raise NotImplementedError

    def parse(self, item: dict[str, Any]) -> RawListing | None:
        raise NotImplementedError


class RemoteOk(_JsonFeed):
    def __init__(self, name: str, settings: RemoteokSource) -> None:
        super().__init__(name, settings)

    def items(self, data: Any) -> list[dict[str, Any]]:
        # The first element is the API's legal notice, not a job.
        return [item for item in data if isinstance(item, dict) and "position" in item]

    def parse(self, item: dict[str, Any]) -> RawListing | None:
        job_id, title, url = (text_or_none(item.get(k)) for k in ("id", "position", "url"))
        if not (job_id and title and url):
            return None
        low, high = (text_or_none(item.get(k)) for k in ("salary_min", "salary_max"))
        salary = f"{low}-{high} USD/year" if low and high and high != "0" else None
        location = text_or_none(item.get("location"))
        return RawListing(
            source=self.name,
            source_job_id=job_id,
            url=url,
            title=title,
            description=str(item.get("description") or ""),
            description_format="html",
            company=text_or_none(item.get("company")),
            location=location,
            location_restrictions=(location,) if location else (),
            work_mode="remote",
            posted_at=parse_datetime(item.get("date") or item.get("epoch")),
            posted_at_source="api",
            salary=salary,
            tags=string_list(item.get("tags")),
        )


class Jobicy(_JsonFeed):
    def __init__(self, name: str, settings: JobicySource) -> None:
        super().__init__(name, settings)

    def items(self, data: Any) -> list[dict[str, Any]]:
        return list(data.get("jobs") or [])

    def parse(self, item: dict[str, Any]) -> RawListing | None:
        job_id, title, url = (text_or_none(item.get(k)) for k in ("id", "jobTitle", "url"))
        if not (job_id and title and url):
            return None
        geo = text_or_none(item.get("jobGeo"))
        return RawListing(
            source=self.name,
            source_job_id=job_id,
            url=url,
            title=title,
            description=str(item.get("jobDescription") or ""),
            description_format="html",
            company=text_or_none(item.get("companyName")),
            location=geo,
            location_restrictions=string_list(geo),
            work_mode="remote",
            employment_type=", ".join(string_list(item.get("jobType"))) or None,
            posted_at=parse_datetime(item.get("pubDate")),
            posted_at_source="api",
            tags=string_list(item.get("jobIndustry")),
            extra={"level": level} if (level := text_or_none(item.get("jobLevel"))) else {},
        )


class WorkingNomads(_JsonFeed):
    def __init__(self, name: str, settings: WorkingNomadsSource) -> None:
        super().__init__(name, settings)

    def items(self, data: Any) -> list[dict[str, Any]]:
        return [item for item in data if isinstance(item, dict)]

    def parse(self, item: dict[str, Any]) -> RawListing | None:
        url, title = text_or_none(item.get("url")), text_or_none(item.get("title"))
        match = _WORKINGNOMADS_ID.search(url or "")
        if not (url and title and match):
            return None
        location = text_or_none(item.get("location"))
        return RawListing(
            source=self.name,
            source_job_id=match[1],
            url=url,
            title=title,
            description=str(item.get("description") or ""),
            description_format="html",
            company=text_or_none(item.get("company_name")),
            location=location,
            location_restrictions=(location,) if location else (),
            work_mode="remote",
            posted_at=parse_datetime(item.get("pub_date")),
            posted_at_source="api",
            tags=string_list(item.get("tags")),
            extra={"category": c} if (c := text_or_none(item.get("category_name"))) else {},
        )


class Remotive(_JsonFeed):
    def __init__(self, name: str, settings: RemotiveSource) -> None:
        super().__init__(name, settings)

    def items(self, data: Any) -> list[dict[str, Any]]:
        return list(data.get("jobs") or [])

    def parse(self, item: dict[str, Any]) -> RawListing | None:
        job_id, title, url = (text_or_none(item.get(k)) for k in ("id", "title", "url"))
        if not (job_id and title and url):
            return None
        location = text_or_none(item.get("candidate_required_location"))
        return RawListing(
            source=self.name,
            source_job_id=job_id,
            url=url,
            title=title,
            description=str(item.get("description") or ""),
            description_format="html",
            company=text_or_none(item.get("company_name")),
            location=location,
            location_restrictions=string_list(location),
            work_mode="remote",
            employment_type=text_or_none(item.get("job_type")),
            posted_at=parse_datetime(item.get("publication_date")),
            posted_at_source="api",
            salary=text_or_none(item.get("salary")),
            tags=string_list(item.get("tags")),
        )
