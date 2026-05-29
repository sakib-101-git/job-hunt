"""
Shomvob scraper.

Search URL: https://shomvob.com/jobs
Params: keyword=python+developer, location=dhaka

Shomvob may use JS rendering. If the requests+BS4 approach returns empty cards,
set use_playwright=true in the scraper or switch to the playwright fallback below.
Save fresh HTML to tests/fixtures/shomvob_search_sample.html and inspect selectors.
"""
import logging
import re
from pathlib import Path
from bs4 import BeautifulSoup
from src.models import ScrapedJob
from src.scrapers.base import BaseScraper
from src.utils import parse_relative_date, clean_html

log = logging.getLogger("jobhunt.scrapers.shomvob")

SEARCH_URL = "https://shomvob.com/jobs"
FIXTURE_DIR = Path(__file__).parent.parent.parent / "tests" / "fixtures"


class ShomvobScraper(BaseScraper):
    name = "shomvob"

    def fetch_listings(self) -> list[ScrapedJob]:
        jobs = []
        seen_ids: set[str] = set()
        for keyword in self.config.search_keywords[:5]:
            try:
                new = self._search(keyword)
                for j in new:
                    if j.source_job_id not in seen_ids:
                        seen_ids.add(j.source_job_id)
                        jobs.append(j)
            except Exception as exc:
                log.warning(f"shomvob search for '{keyword}' failed: {exc}")
        log.info(f"shomvob: found {len(jobs)} listings")
        return jobs

    def fetch_job_detail(self, url: str) -> str:
        try:
            resp = self._get(url)
            self._save_fixture("shomvob_detail_sample.html", resp.text)
            return self._parse_detail(resp.text)
        except Exception as exc:
            log.warning(f"shomvob detail fetch failed for {url}: {exc}")
            return ""

    # ------------------------------------------------------------------ #

    def _search(self, keyword: str) -> list[ScrapedJob]:
        resp = self._get(SEARCH_URL, params={"keyword": keyword})
        self._save_fixture("shomvob_search_sample.html", resp.text)
        jobs = self._parse_listing(resp.text)

        # If no results, the page may be JS-rendered — try playwright
        if not jobs:
            jobs = self._playwright_search(keyword)

        return jobs

    def _parse_listing(self, html: str) -> list[ScrapedJob]:
        soup = BeautifulSoup(html, "lxml")
        jobs = []

        for card in soup.select(".job-card, .job-listing, [class*='job-item'], article"):
            try:
                title_el = card.select_one("h2, h3, .job-title, [class*='title'] a")
                link_el = card.select_one("a[href]")
                if not title_el or not link_el:
                    continue

                href = link_el.get("href", "")
                if not href.startswith("http"):
                    href = "https://shomvob.com" + href

                company_el = card.select_one(".company, .company-name, [class*='company']")
                location_el = card.select_one(".location, [class*='location']")
                date_el = card.select_one(".date, .posted-date, time")

                job_id = re.sub(r"[^a-z0-9-]", "", href.split("/")[-1]) or href[-20:]

                jobs.append(ScrapedJob(
                    source=self.name,
                    source_job_id=job_id,
                    title=title_el.get_text(strip=True),
                    company=company_el.get_text(strip=True) if company_el else "",
                    location=location_el.get_text(strip=True) if location_el else "",
                    url=href,
                    posted_date=parse_relative_date(date_el.get_text(strip=True) if date_el else ""),
                ))
            except Exception as exc:
                log.debug(f"shomvob card parse error: {exc}")

        return jobs

    def _parse_detail(self, html: str) -> str:
        soup = BeautifulSoup(html, "lxml")
        desc_el = soup.select_one(".job-description, .job-details, [class*='description'], article")
        if desc_el:
            return clean_html(str(desc_el))
        return clean_html(soup.get_text())

    def _playwright_search(self, keyword: str) -> list[ScrapedJob]:
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page(user_agent=self.config.user_agent)
                page.goto(f"{SEARCH_URL}?keyword={keyword}", timeout=20000)
                page.wait_for_selector(".job-card, .job-listing, article", timeout=10000)
                html = page.content()
                browser.close()
            self._save_fixture("shomvob_search_sample.html", html)
            return self._parse_listing(html)
        except Exception as exc:
            log.warning(f"shomvob playwright fallback failed: {exc}")
            return []

    def _save_fixture(self, filename: str, content: str):
        try:
            FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
            path = FIXTURE_DIR / filename
            if not path.exists():
                path.write_text(content, encoding="utf-8")
        except Exception:
            pass
