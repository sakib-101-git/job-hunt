"""
BDJobs scraper.

Search URL: https://jobs.bdjobs.com/jobsearch.asp
Params observed from manual search:
  Ession  = URL-encoded keyword (e.g. "python+developer")
  Lcn     = location code (1=Dhaka, 3=Chittagong, 0=All)
  typepost = 0 for all job types
  iPage   = page number (1-based)

If selectors break, save a fresh page to tests/fixtures/bdjobs_search_sample.html
and inspect the structure.
"""
import logging
import re
from pathlib import Path
from bs4 import BeautifulSoup
from src.models import ScrapedJob
from src.scrapers.base import BaseScraper
from src.utils import parse_relative_date, clean_html

log = logging.getLogger("jobhunt.scrapers.bdjobs")

SEARCH_URL = "https://jobs.bdjobs.com/jobsearch.asp"
DETAIL_BASE = "https://jobs.bdjobs.com/"
MAX_PAGES = 3
FIXTURE_DIR = Path(__file__).parent.parent.parent / "tests" / "fixtures"


class BDJobsScraper(BaseScraper):
    name = "bdjobs"

    def fetch_listings(self) -> list[ScrapedJob]:
        jobs = []
        seen_ids: set[str] = set()
        for keyword in self.config.search_keywords[:5]:  # limit API calls
            for page in range(1, MAX_PAGES + 1):
                try:
                    new = self._search_page(keyword, page)
                    if not new:
                        break
                    for j in new:
                        if j.source_job_id not in seen_ids:
                            seen_ids.add(j.source_job_id)
                            jobs.append(j)
                except Exception as exc:
                    log.warning(f"bdjobs page {page} for '{keyword}' failed: {exc}")
                    break
        log.info(f"bdjobs: found {len(jobs)} listings")
        return jobs

    def fetch_job_detail(self, url: str) -> str:
        try:
            resp = self._get(url)
            self._save_fixture("bdjobs_detail_sample.html", resp.text)
            return self._parse_detail(resp.text)
        except Exception as exc:
            log.warning(f"bdjobs detail fetch failed for {url}: {exc}")
            return ""

    # ------------------------------------------------------------------ #

    def _search_page(self, keyword: str, page: int) -> list[ScrapedJob]:
        resp = self._get(SEARCH_URL, params={
            "Ession": keyword,
            "typepost": "0",
            "Lcn": "1",
            "iPage": str(page),
        })
        if page == 1:
            self._save_fixture("bdjobs_search_sample.html", resp.text)
        return self._parse_listing(resp.text)

    def _parse_listing(self, html: str) -> list[ScrapedJob]:
        soup = BeautifulSoup(html, "lxml")
        jobs = []

        # BDJobs job cards — adjust selectors if site HTML changes
        for card in soup.select("div.job-tittle, div.single-job-items, div[class*='job-item']"):
            try:
                title_el = card.select_one("a.job-title-link, h2 a, .job-tittle a")
                if not title_el:
                    continue

                href = title_el.get("href", "")
                if not href.startswith("http"):
                    href = DETAIL_BASE + href.lstrip("/")

                job_id = self._extract_id(href)
                if not job_id:
                    continue

                company_el = card.select_one(".company-name, .comp-name, [class*='company']")
                location_el = card.select_one(".location, [class*='location']")
                date_el = card.select_one(".date, .post-date, [class*='date']")

                jobs.append(ScrapedJob(
                    source=self.name,
                    source_job_id=job_id,
                    title=title_el.get_text(strip=True),
                    company=company_el.get_text(strip=True) if company_el else "Confidential",
                    location=location_el.get_text(strip=True) if location_el else "",
                    url=href,
                    posted_date=parse_relative_date(date_el.get_text(strip=True) if date_el else ""),
                ))
            except Exception as exc:
                log.debug(f"bdjobs card parse error: {exc}")

        return jobs

    def _parse_detail(self, html: str) -> str:
        soup = BeautifulSoup(html, "lxml")
        desc_el = soup.select_one(
            "#JobDescriptionBox, .job-desc, .job-description, [class*='job-detail'], article"
        )
        if desc_el:
            return clean_html(str(desc_el))
        return clean_html(soup.get_text())

    def _extract_id(self, url: str) -> str:
        m = re.search(r"[?&]id=(\d+)", url, re.IGNORECASE)
        return m.group(1) if m else ""

    def _save_fixture(self, filename: str, content: str):
        try:
            FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
            path = FIXTURE_DIR / filename
            if not path.exists():
                path.write_text(content, encoding="utf-8")
        except Exception:
            pass


if __name__ == "__main__":
    import json
    from src.config import load_config
    from src.utils import setup_logging
    setup_logging("INFO")
    cfg = load_config()
    scraper = BDJobsScraper(cfg)
    jobs = scraper.fetch_listings()
    print(json.dumps([j.model_dump(mode="json") for j in jobs[:5]], indent=2, default=str))
