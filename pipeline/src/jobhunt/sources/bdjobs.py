"""BDJobs, the main Bangladesh board.

Search pages through one functional category (config `category`); for jobs not
stored yet, the details API supplies the full description, skills, education
and overseas note. Endpoints were found in bdjobs.com's web bundle; see
docs/RUNBOOK.md for how to re-check them if they change.
"""

import logging
from typing import Any

from jobhunt.config.models import BdjobsSource
from jobhunt.sources.base import FetchContext, parse_date, parse_datetime, text_or_none
from jobhunt.sources.http import FetchError
from jobhunt.sources.models import RawListing, SourceResult

log = logging.getLogger(__name__)

# Detail fields that make up the description, in reading order.
_DESCRIPTION_PARTS = (
    ("JobDescription", "Responsibilities"),
    ("JobKeyPoints", None),
    ("EducationRequirements", "Education"),
    ("experience", "Experience"),
    ("AdditionJobRequirements", "Additional requirements"),
    ("SkillsRequired", "Skills"),
    ("JobOtherBenifits", "Benefits"),
)


class Bdjobs:
    def __init__(self, name: str, settings: BdjobsSource) -> None:
        self.name = name
        self._s = settings

    async def fetch(self, ctx: FetchContext) -> SourceResult:
        items = await self._search(ctx)
        listings = []
        for item in items:
            job_id = text_or_none(item.get("Jobid"))
            if job_id is None or not text_or_none(item.get("jobTitle")):
                continue
            details = None
            if job_id not in ctx.known_ids:
                details = await self._details(ctx, job_id)
            if details and details.get("Closed") == "1":
                continue
            listings.append(self._listing(job_id, item, details))
        return SourceResult(listings)

    async def _search(self, ctx: FetchContext) -> list[dict[str, Any]]:
        params = {
            "category": self._s.category,
            "rpp": str(self._s.results_per_page),
            "isPro": "0",
        }
        if not ctx.bootstrap:
            params["postedWithin"] = str(self._s.posted_within_days)
        items: dict[str, dict[str, Any]] = {}
        for page in range(1, self._s.max_pages + 1):
            response = await ctx.fetcher.get(
                self._s.search_url, params={**params, "pg": str(page)}, headers=self._s.headers
            )
            data = response.json()
            batch = (data.get("data") or []) + (data.get("premiumData") or [])
            for item in batch:
                items.setdefault(str(item.get("Jobid")), item)
            total_pages = int((data.get("common") or {}).get("totalpages") or 0)
            if not batch or page >= total_pages:
                break
        return list(items.values())

    async def _details(self, ctx: FetchContext, job_id: str) -> dict[str, Any] | None:
        try:
            response = await ctx.fetcher.get(
                self._s.details_url, params={"jobId": job_id, "ln": "1"}, headers=self._s.headers
            )
            data = response.json().get("data") or []
        except FetchError as exc:
            # The listing still carries a short description; keep the job.
            log.warning("bdjobs details for %s failed: %s", job_id, exc)
            return None
        details = data[0] if isinstance(data, list) and data else None
        if not isinstance(details, dict) or details.get("JobFound") != "True":
            return None
        return details

    def _listing(
        self, job_id: str, item: dict[str, Any], details: dict[str, Any] | None
    ) -> RawListing:
        d = details or {}
        extra = {
            key: value
            for key, value in (
                ("category_id", text_or_none(item.get("Cat_id"))),
                ("skills", text_or_none(d.get("SkillsRequired"))),
                ("overseas_note", text_or_none(d.get("overseasnote"))),
                ("vacancies", text_or_none(d.get("JobVacancies") or item.get("Vacancies"))),
                ("details", "missing" if details is None else None),
            )
            if value
        }
        return RawListing(
            source=self.name,
            source_job_id=job_id,
            url=self._s.job_url.format(id=job_id),
            title=str(item["jobTitle"]).strip(),
            description=_description(item, details),
            description_format="html",
            company=text_or_none(item.get("companyName")),
            location=text_or_none(d.get("JobLocation") or item.get("location")),
            work_mode=text_or_none(d.get("JobWorkPlace") or item.get("WorkPlace")),
            employment_type=text_or_none(d.get("JobNature") or item.get("JobType")),
            posted_at=parse_datetime(item.get("publishDate")),
            posted_at_source="api",
            deadline=parse_date(d.get("DeadlineDB") or item.get("deadlineDB")),
            salary=text_or_none(d.get("JobSalaryRangeText") or item.get("Salary")),
            extra=extra,
        )


def _description(item: dict[str, Any], details: dict[str, Any] | None) -> str:
    if details is None:
        parts = [item.get("jobContext"), item.get("jobDescription"), item.get("experience")]
        return "\n".join(f"<p>{text}</p>" for p in parts if (text := text_or_none(p)))
    sections = []
    for key, heading in _DESCRIPTION_PARTS:
        if text := text_or_none(details.get(key)):
            sections.append(f"<h3>{heading}</h3>\n{text}" if heading else text)
    return "\n".join(sections)
