"""
BDJobs scraper.

BDJobs migrated from the old `jobs.bdjobs.com/jobsearch.asp` page (now dead —
it 302-redirects to an empty Angular shell) to a JSON search API:

  GET https://api.bdjobs.com/Jobs/api/JobSearch/GetJobSearch
  params: keyword, pg (1-based page), rpp (results per page), isPro=0
  response: { data: [...], premiumData: [...], common: { totalpages, ... } }

Each job item already carries the JD (jobContext / eduRec / experience), so we
build jd_text from the listing and skip a separate detail fetch. Job detail
pages for the Telegram link live at https://bdjobs.com/h/details/<Jobid>.
"""
import logging
from datetime import datetime
from src.models import ScrapedJob
from src.scrapers.base import BaseScraper
from src.utils import clean_html

log = logging.getLogger("jobhunt.scrapers.bdjobs")

SEARCH_API = "https://api.bdjobs.com/Jobs/api/JobSearch/GetJobSearch"
DETAIL_BASE = "https://bdjobs.com/h/details/"
RESULTS_PER_PAGE = 40
MAX_PAGES = 2          # first 2 pages per keyword to avoid hammering the API
MAX_KEYWORDS = 5       # limit keywords per cycle


class BDJobsScraper(BaseScraper):
    name = "bdjobs"

    def __init__(self, config):
        super().__init__(config)
        # Mimic the real web client so the API returns JSON and we look less
        # like a bare bot (helps when running from datacenter IPs).
        self._session.headers.update({
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://bdjobs.com",
            "Referer": "https://bdjobs.com/",
        })

    def fetch_listings(self) -> list[ScrapedJob]:
        jobs: list[ScrapedJob] = []
        seen_ids: set[str] = set()
        for keyword in self.config.search_keywords[:MAX_KEYWORDS]:
            for page in range(1, MAX_PAGES + 1):
                try:
                    items, total_pages = self._search_page(keyword, page)
                except Exception as exc:
                    log.warning(f"bdjobs '{keyword}' page {page} failed: {exc}")
                    break
                if not items:
                    break
                for item in items:
                    job = self._parse_job(item)
                    if job and job.source_job_id not in seen_ids:
                        seen_ids.add(job.source_job_id)
                        jobs.append(job)
                if page >= total_pages:
                    break
        log.info(f"bdjobs: found {len(jobs)} listings")
        return jobs

    def fetch_job_detail(self, url: str) -> str:
        # JD is already captured from the search API; nothing more to fetch.
        return ""

    # ------------------------------------------------------------------ #

    def _search_page(self, keyword: str, page: int) -> tuple[list[dict], int]:
        resp = self._get(SEARCH_API, params={
            "keyword": keyword,
            "pg": str(page),
            "rpp": str(RESULTS_PER_PAGE),
            "isPro": "0",
        })
        payload = resp.json()
        items = (payload.get("data") or []) + (payload.get("premiumData") or [])
        common = payload.get("common") or {}
        try:
            total_pages = int(common.get("totalpages", 1))
        except (TypeError, ValueError):
            total_pages = 1
        return items, total_pages

    def _parse_job(self, item: dict) -> ScrapedJob | None:
        try:
            job_id = str(item.get("Jobid") or "").strip()
            title = (item.get("jobTitle") or "").strip()
            if not job_id or not title:
                return None
            workplace = (item.get("WorkPlace") or "").strip()
            return ScrapedJob(
                source=self.name,
                source_job_id=job_id,
                title=title,
                company=(item.get("companyName") or "").strip() or "Confidential",
                location=(item.get("location") or "").strip(),
                url=DETAIL_BASE + job_id,
                posted_date=self._parse_date(item.get("publishDate")),
                jd_text=self._build_jd(item),
                salary_range=self._parse_salary(item.get("Salary")),
                job_type=(item.get("JobType") or "").strip() or None,
                is_remote=workplace.lower() in {"home", "remote", "work from home"},
            )
        except Exception as exc:
            log.debug(f"bdjobs card parse error: {exc}")
            return None

    @staticmethod
    def _parse_date(value) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None

    @staticmethod
    def _build_jd(item: dict) -> str:
        parts = []
        for key in ("jobContext", "jobDescription", "eduRec", "experience"):
            val = item.get(key)
            if val:
                val = str(val)
                parts.append(clean_html(val) if "<" in val else val.strip())
        workplace = (item.get("WorkPlace") or "").strip()
        if workplace:
            parts.append(f"Workplace: {workplace}")
        return "\n".join(p for p in parts if p).strip()

    @staticmethod
    def _parse_salary(salary) -> str | None:
        if not isinstance(salary, dict) or salary.get("HideSalary"):
            return None
        if salary.get("SalaryRange"):
            return str(salary["SalaryRange"])
        lo, hi = salary.get("MinSalary") or 0, salary.get("MaxSalary") or 0
        if lo or hi:
            return f"{lo}-{hi}"
        if salary.get("IsNegotiable"):
            return "Negotiable"
        return None


if __name__ == "__main__":
    import json
    from src.config import load_config
    from src.utils import setup_logging
    setup_logging("INFO")
    cfg = load_config()
    scraper = BDJobsScraper(cfg)
    jobs = scraper.fetch_listings()
    print(json.dumps([j.model_dump(mode="json") for j in jobs[:5]], indent=2, default=str))
