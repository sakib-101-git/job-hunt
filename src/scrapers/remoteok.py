"""
RemoteOK scraper — free public JSON API.
https://remoteok.com/api?tags=<tag>
Fetches by tag, then filters locally against configured keywords.
"""
import logging
from src.models import ScrapedJob
from src.scrapers.base import BaseScraper
from src.utils import clean_html, parse_relative_date

log = logging.getLogger("jobhunt.scrapers.remoteok")

API_URL = "https://remoteok.com/api"

# Tags to query — covers the main positions in config.yaml keywords
FETCH_TAGS = ["python", "javascript", "typescript", "react", "node", "django", "fullstack"]


class RemoteOKScraper(BaseScraper):
    name = "remoteok"

    def __init__(self, config):
        super().__init__(config)
        # RemoteOK requires a descriptive User-Agent
        self._session.headers["User-Agent"] = "JobHuntBot/1.0 personal-job-tracker"

    def fetch_listings(self) -> list[ScrapedJob]:
        keywords_lower = [kw.lower() for kw in self.config.search_keywords]
        jobs = []
        seen_ids: set[str] = set()
        for tag in FETCH_TAGS:
            try:
                raw = self._fetch_tag(tag)
                for item in raw:
                    if self._is_relevant(item, keywords_lower):
                        j = self._parse(item)
                        if j and j.source_job_id not in seen_ids:
                            seen_ids.add(j.source_job_id)
                            jobs.append(j)
            except Exception as exc:
                log.warning(f"remoteok tag '{tag}' failed: {exc}")
        log.info(f"remoteok: found {len(jobs)} listings")
        return jobs

    def fetch_job_detail(self, url: str) -> str:
        return ""  # JD included in listing response

    def _fetch_tag(self, tag: str) -> list[dict]:
        resp = self._get(API_URL, params={"tags": tag})
        data = resp.json()
        # First element is a metadata object — skip non-job entries
        return [item for item in data if isinstance(item, dict) and "id" in item]

    def _is_relevant(self, item: dict, keywords_lower: list[str]) -> bool:
        title = item.get("position", "").lower()
        tags = " ".join(t.lower() for t in (item.get("tags") or []))
        combined = title + " " + tags
        return any(kw in combined for kw in keywords_lower)

    def _parse(self, item: dict) -> ScrapedJob | None:
        try:
            job_id = str(item["id"])
            url = item.get("url") or f"https://remoteok.com/jobs/{job_id}"
            return ScrapedJob(
                source=self.name,
                source_job_id=job_id,
                title=item.get("position", ""),
                company=item.get("company", ""),
                location=item.get("location") or "Worldwide",
                url=url,
                jd_text=clean_html(item.get("description", "")),
                job_type="remote",
                is_remote=True,
                posted_date=parse_relative_date(item.get("date", "")),
            )
        except Exception as exc:
            log.debug(f"remoteok parse error: {exc}")
            return None
