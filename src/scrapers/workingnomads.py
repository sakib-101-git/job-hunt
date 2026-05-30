"""
Working Nomads scraper — free public JSON feed of worldwide remote jobs.
https://www.workingnomads.com/api/exposed_jobs/
Single large list; filtered locally by title keyword.
"""
import logging
import re
from src.models import ScrapedJob
from src.scrapers.base import BaseScraper
from src.utils import clean_html, parse_relative_date

log = logging.getLogger("jobhunt.scrapers.workingnomads")

API_URL = "https://www.workingnomads.com/api/exposed_jobs/"


class WorkingNomadsScraper(BaseScraper):
    name = "workingnomads"

    def fetch_listings(self) -> list[ScrapedJob]:
        keywords_lower = [kw.lower() for kw in self.config.search_keywords]
        jobs = []
        seen_ids: set[str] = set()
        try:
            resp = self._get(API_URL)
            for item in resp.json():
                title = item.get("title", "")
                if not any(kw in title.lower() for kw in keywords_lower):
                    continue
                j = self._parse(item)
                if j and j.source_job_id not in seen_ids:
                    seen_ids.add(j.source_job_id)
                    jobs.append(j)
        except Exception as exc:
            log.warning(f"workingnomads fetch failed: {exc}")
        log.info(f"workingnomads: found {len(jobs)} listings")
        return jobs

    def fetch_job_detail(self, url: str) -> str:
        return ""  # description included in listing

    def _parse(self, item: dict) -> ScrapedJob | None:
        try:
            url = item.get("url", "")
            slug = re.sub(r"[^a-z0-9-]", "", url.rstrip("/").split("/")[-1].lower()) or url[-40:]
            return ScrapedJob(
                source=self.name,
                source_job_id=slug,
                title=item.get("title", ""),
                company=item.get("company_name", ""),
                location=item.get("location") or "Anywhere",
                url=url,
                jd_text=clean_html(item.get("description", "")),
                job_type="remote",
                is_remote=True,
                posted_date=parse_relative_date(item.get("pub_date", "")),
            )
        except Exception as exc:
            log.debug(f"workingnomads parse error: {exc}")
            return None
