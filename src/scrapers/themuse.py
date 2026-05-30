"""
The Muse scraper — free public JSON API with a large job board.
https://www.themuse.com/api/public/jobs?category=...&level=...&page=N
We restrict to Entry Level / Internship levels client-side.
"""
import logging
from datetime import datetime, timezone
from src.models import ScrapedJob
from src.scrapers.base import BaseScraper
from src.utils import clean_html

log = logging.getLogger("jobhunt.scrapers.themuse")

API_URL = "https://www.themuse.com/api/public/jobs"
CATEGORIES = ["Software Engineering", "Data Science", "Data and Analytics", "Computer and IT"]
MAX_PAGES = 3
ACCEPT_LEVELS = {"entry level", "internship"}


class TheMuseScraper(BaseScraper):
    name = "themuse"

    def fetch_listings(self) -> list[ScrapedJob]:
        keywords_lower = [kw.lower() for kw in self.config.search_keywords]
        jobs = []
        seen_ids: set[str] = set()
        for page in range(MAX_PAGES):
            try:
                results = self._fetch_page(page)
                if not results:
                    break
                for item in results:
                    j = self._parse(item)
                    if not j or j.source_job_id in seen_ids:
                        continue
                    if any(kw in j.title.lower() for kw in keywords_lower):
                        seen_ids.add(j.source_job_id)
                        jobs.append(j)
            except Exception as exc:
                log.warning(f"themuse page {page} failed: {exc}")
                break
        log.info(f"themuse: found {len(jobs)} listings")
        return jobs

    def fetch_job_detail(self, url: str) -> str:
        return ""  # description included in listing

    def _fetch_page(self, page: int) -> list[dict]:
        params = [("page", str(page))]
        params += [("category", c) for c in CATEGORIES]
        params += [("level", "Entry Level"), ("level", "Internship")]
        resp = self._get(API_URL, params=params)
        return resp.json().get("results", [])

    def _parse(self, item: dict) -> ScrapedJob | None:
        try:
            levels = [l.get("name", "").lower() for l in item.get("levels", [])]
            if not any(lv in ACCEPT_LEVELS for lv in levels):
                return None  # extra safety: only entry/intern
            locations = ", ".join(l.get("name", "") for l in item.get("locations", []))
            is_remote = "remote" in locations.lower() or "flexible" in locations.lower()
            return ScrapedJob(
                source=self.name,
                source_job_id=str(item["id"]),
                title=item.get("name", ""),
                company=item.get("company", {}).get("name", ""),
                location=locations or "Flexible / Remote",
                url=item.get("refs", {}).get("landing_page", ""),
                jd_text=clean_html(item.get("contents", "")),
                job_type="remote" if is_remote else None,
                is_remote=is_remote,
                posted_date=self._parse_date(item.get("publication_date", "")),
            )
        except Exception as exc:
            log.debug(f"themuse parse error: {exc}")
            return None

    def _parse_date(self, text: str):
        if not text:
            return None
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            return None
