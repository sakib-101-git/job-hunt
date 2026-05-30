"""
Jobicy scraper — free public JSON API for worldwide remote jobs.
https://jobicy.com/api/v2/remote-jobs?count=50&tag=<tag>
Provides jobGeo (location) and jobLevel — both useful for filtering.
"""
import logging
from datetime import datetime, timezone
from src.models import ScrapedJob
from src.scrapers.base import BaseScraper
from src.utils import clean_html

log = logging.getLogger("jobhunt.scrapers.jobicy")

API_URL = "https://jobicy.com/api/v2/remote-jobs"

# Industries/tags relevant to entry-level SWE, data and ML/research roles
QUERY_TAGS = ["python", "javascript", "react", "dev", "data-science", "machine-learning"]
COUNT = 50


class JobicyScraper(BaseScraper):
    name = "jobicy"

    def fetch_listings(self) -> list[ScrapedJob]:
        keywords_lower = [kw.lower() for kw in self.config.search_keywords]
        jobs = []
        seen_ids: set[str] = set()
        for tag in QUERY_TAGS:
            try:
                for item in self._fetch_tag(tag):
                    j = self._parse(item)
                    if not j or j.source_job_id in seen_ids:
                        continue
                    if any(kw in j.title.lower() for kw in keywords_lower):
                        seen_ids.add(j.source_job_id)
                        jobs.append(j)
            except Exception as exc:
                log.warning(f"jobicy tag '{tag}' failed: {exc}")
        log.info(f"jobicy: found {len(jobs)} listings")
        return jobs

    def fetch_job_detail(self, url: str) -> str:
        return ""  # full description already in listing

    def _fetch_tag(self, tag: str) -> list[dict]:
        resp = self._get(API_URL, params={"count": COUNT, "tag": tag})
        return resp.json().get("jobs", [])

    def _parse(self, item: dict) -> ScrapedJob | None:
        try:
            jt = item.get("jobType")
            job_type = jt[0] if isinstance(jt, list) and jt else "remote"
            return ScrapedJob(
                source=self.name,
                source_job_id=str(item["id"]),
                title=item.get("jobTitle", ""),
                company=item.get("companyName", ""),
                location=item.get("jobGeo") or "Anywhere",
                url=item.get("url", ""),
                jd_text=clean_html(item.get("jobDescription") or item.get("jobExcerpt", "")),
                job_type=job_type,
                is_remote=True,
                posted_date=self._parse_date(item.get("pubDate", "")),
            )
        except Exception as exc:
            log.debug(f"jobicy parse error: {exc}")
            return None

    def _parse_date(self, text: str):
        if not text:
            return None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(text.strip(), fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        return None
