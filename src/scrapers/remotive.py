"""
Remotive scraper — free public REST API.
https://remotive.com/api/remote-jobs?search=<keyword>&limit=20
All results are remote-first by definition.
"""
import logging
from src.models import ScrapedJob
from src.scrapers.base import BaseScraper
from src.utils import clean_html

log = logging.getLogger("jobhunt.scrapers.remotive")

API_URL = "https://remotive.com/api/remote-jobs"
MAX_PER_KEYWORD = 20


class RemotiveScraper(BaseScraper):
    name = "remotive"

    def fetch_listings(self) -> list[ScrapedJob]:
        jobs = []
        seen_ids: set[str] = set()
        for keyword in self.config.search_keywords[:6]:
            try:
                new = self._search(keyword)
                for j in new:
                    if j.source_job_id not in seen_ids:
                        seen_ids.add(j.source_job_id)
                        jobs.append(j)
            except Exception as exc:
                log.warning(f"remotive search for '{keyword}' failed: {exc}")
        log.info(f"remotive: found {len(jobs)} listings")
        return jobs

    def fetch_job_detail(self, url: str) -> str:
        return ""  # full JD already included in listing response

    def _search(self, keyword: str) -> list[ScrapedJob]:
        resp = self._get(API_URL, params={"search": keyword, "limit": MAX_PER_KEYWORD})
        data = resp.json()
        jobs = []
        for item in data.get("jobs", []):
            try:
                jobs.append(ScrapedJob(
                    source=self.name,
                    source_job_id=str(item["id"]),
                    title=item.get("title", ""),
                    company=item.get("company_name", ""),
                    location=item.get("candidate_required_location") or "Worldwide",
                    url=item.get("url", ""),
                    jd_text=clean_html(item.get("description", "")),
                    job_type=item.get("job_type", "remote"),
                    is_remote=True,
                ))
            except Exception as exc:
                log.debug(f"remotive parse error: {exc}")
        return jobs
