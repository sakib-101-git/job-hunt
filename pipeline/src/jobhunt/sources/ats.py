"""Company career boards through their public ATS APIs: Greenhouse, Lever,
Ashby, Workable and SmartRecruiters. The company list lives in config; one
company's board failing is a warning, not a failed run."""

import html
import logging
from collections.abc import Callable
from typing import Any

from jobhunt.config.models import AtsCompany, AtsSource
from jobhunt.sources.base import (
    FetchContext,
    parse_date,
    parse_datetime,
    string_list,
    text_or_none,
)
from jobhunt.sources.http import FetchError
from jobhunt.sources.models import RawListing, SourceResult

log = logging.getLogger(__name__)

Item = dict[str, Any]


class CompanyBoards:
    def __init__(self, name: str, settings: AtsSource) -> None:
        self.name = name
        self._s = settings

    async def fetch(self, ctx: FetchContext) -> SourceResult:
        listings: list[RawListing] = []
        warnings = []
        for company in self._s.companies:
            try:
                listings.extend(await self._company(ctx, company))
            except FetchError as exc:
                warnings.append(f"{company.name} ({company.ats}): {exc}")
        if warnings and len(warnings) == len(self._s.companies):
            raise FetchError(self.name, "every company board failed: " + "; ".join(warnings))
        return SourceResult(listings, warnings=tuple(warnings))

    async def _company(self, ctx: FetchContext, company: AtsCompany) -> list[RawListing]:
        url = self._s.boards[company.ats].format(token=company.token)
        parse = _PARSERS[company.ats]
        items = _items(company.ats, (await ctx.fetcher.get(url)).json())
        listings = []
        for item in items:
            item_id = text_or_none(item.get("shortcode") or item.get("id"))
            if item_id is None:
                continue
            job_id = f"{company.ats}:{company.token}:{item_id}"
            if company.ats in self._s.details and job_id not in ctx.known_ids:
                item = {**item, **await self._details(ctx, company, item_id)}
            if listing := parse(self, company, job_id, item_id, item):
                listings.append(listing)
        return listings

    async def _details(self, ctx: FetchContext, company: AtsCompany, item_id: str) -> Item:
        url = self._s.details[company.ats].format(token=company.token, id=item_id)
        try:
            data = (await ctx.fetcher.get(url)).json()
        except FetchError as exc:
            log.warning("%s details for %s failed: %s", company.ats, item_id, exc)
            return {}
        return data if isinstance(data, dict) else {}

    def page(self, company: AtsCompany, item_id: str) -> str | None:
        template = self._s.job_pages.get(company.ats)
        return template.format(token=company.token, id=item_id) if template else None

    # --- per-ATS parsing ------------------------------------------------------

    def _greenhouse(
        self, company: AtsCompany, job_id: str, _: str, item: Item
    ) -> RawListing | None:
        title, url = text_or_none(item.get("title")), text_or_none(item.get("absolute_url"))
        if not (title and url):
            return None
        location = text_or_none((item.get("location") or {}).get("name"))
        return RawListing(
            source=self.name,
            source_job_id=job_id,
            url=url,
            title=title,
            description=html.unescape(str(item.get("content") or "")),
            description_format="html",
            company=company.name,
            location=location,
            location_restrictions=_split_locations(location),
            posted_at=parse_datetime(item.get("first_published") or item.get("updated_at")),
            posted_at_source="api",
            deadline=parse_date(item.get("application_deadline")),
        )

    def _lever(self, company: AtsCompany, job_id: str, _: str, item: Item) -> RawListing | None:
        title, url = text_or_none(item.get("text")), text_or_none(item.get("hostedUrl"))
        if not (title and url):
            return None
        categories = item.get("categories") or {}
        locations = string_list(categories.get("allLocations")) or string_list(
            [categories.get("location")]
        )
        sections = [str(item.get("description") or "")]
        sections += [
            f"<h3>{s.get('text', '')}</h3>{s.get('content', '')}" for s in item.get("lists") or []
        ]
        sections.append(str(item.get("additional") or ""))
        return RawListing(
            source=self.name,
            source_job_id=job_id,
            url=url,
            title=title,
            description="\n".join(s for s in sections if s),
            description_format="html",
            company=company.name,
            location=", ".join(locations) or None,
            location_restrictions=locations,
            work_mode=text_or_none(item.get("workplaceType")),
            employment_type=text_or_none(categories.get("commitment")),
            posted_at=parse_datetime(_millis(item.get("createdAt"))),
            posted_at_source="api",
        )

    def _ashby(self, company: AtsCompany, job_id: str, _: str, item: Item) -> RawListing | None:
        title, url = text_or_none(item.get("title")), text_or_none(item.get("jobUrl"))
        if not (title and url) or item.get("isListed") is False:
            return None
        locations = string_list(
            [item.get("location")]
            + [s.get("location") for s in item.get("secondaryLocations") or []]
        )
        work_mode = text_or_none(item.get("workplaceType")) or (
            "remote" if item.get("isRemote") else None
        )
        return RawListing(
            source=self.name,
            source_job_id=job_id,
            url=url,
            title=title,
            description=str(item.get("descriptionHtml") or ""),
            description_format="html",
            company=company.name,
            location=", ".join(locations) or None,
            location_restrictions=locations,
            work_mode=work_mode,
            employment_type=text_or_none(item.get("employmentType")),
            posted_at=parse_datetime(item.get("publishedAt")),
            posted_at_source="api",
            salary=text_or_none((item.get("compensation") or {}).get("compensationTierSummary")),
        )

    def _workable(self, company: AtsCompany, job_id: str, _: str, item: Item) -> RawListing | None:
        title, url = text_or_none(item.get("title")), text_or_none(item.get("url"))
        if not (title and url):
            return None
        places = item.get("locations") or [item]
        locations = tuple(
            text for place in places if (text := _place(place.get("city"), place.get("country")))
        )
        return RawListing(
            source=self.name,
            source_job_id=job_id,
            url=url,
            title=title,
            description=str(item.get("description") or "") or title,
            description_format="html",
            company=company.name,
            location=", ".join(locations) or None,
            location_restrictions=locations,
            work_mode="remote" if item.get("telecommuting") else None,
            employment_type=text_or_none(item.get("employment_type")),
            posted_at=parse_datetime(item.get("published_on")),
            posted_at_source="api",
        )

    def _smartrecruiters(
        self, company: AtsCompany, job_id: str, item_id: str, item: Item
    ) -> RawListing | None:
        title = text_or_none(item.get("name"))
        url = text_or_none(item.get("postingUrl")) or self.page(company, item_id)
        if not (title and url):
            return None
        where = item.get("location") or {}
        location = _place(where.get("city"), str(where.get("country") or "").upper() or None)
        sections = ((item.get("jobAd") or {}).get("sections")) or {}
        description = "\n".join(
            str(sections[key].get("text") or "")
            for key in ("jobDescription", "qualifications", "additionalInformation")
            if isinstance(sections.get(key), dict)
        )
        return RawListing(
            source=self.name,
            source_job_id=job_id,
            url=url,
            title=title,
            description=description or title,
            description_format="html",
            company=company.name,
            location=location,
            location_restrictions=(location,) if location else (),
            work_mode="remote" if where.get("remote") else None,
            employment_type=text_or_none((item.get("typeOfEmployment") or {}).get("label")),
            posted_at=parse_datetime(item.get("releasedDate")),
            posted_at_source="api",
            extra={"level": lv} if (lv := (item.get("experienceLevel") or {}).get("label")) else {},
        )


Parser = Callable[[CompanyBoards, AtsCompany, str, str, Item], RawListing | None]

_PARSERS: dict[str, Parser] = {
    "greenhouse": CompanyBoards._greenhouse,
    "lever": CompanyBoards._lever,
    "ashby": CompanyBoards._ashby,
    "workable": CompanyBoards._workable,
    "smartrecruiters": CompanyBoards._smartrecruiters,
}


def _items(ats: str, data: Any) -> list[Item]:
    if ats == "lever":
        return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []
    key = {"greenhouse": "jobs", "ashby": "jobs", "workable": "jobs", "smartrecruiters": "content"}[
        ats
    ]
    return list(data.get(key) or [])


def _split_locations(text: str | None) -> tuple[str, ...]:
    """Greenhouse joins several offices with ';'."""
    return tuple(part for p in (text or "").split(";") if (part := p.strip()))


def _place(city: Any, country: Any) -> str | None:
    return ", ".join(p for p in (text_or_none(city), text_or_none(country)) if p) or None


def _millis(value: Any) -> str | None:
    text = text_or_none(value)
    return str(int(text) // 1000) if text and text.isdigit() else text
