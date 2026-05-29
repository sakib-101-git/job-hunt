"""
Skill.jobs scraper.

Search URL: https://skill.jobs/jobs
Params: keyword=python+developer

May be JS-rendered. If requests+BS4 returns empty, falls back to playwright.
Save fresh HTML to tests/fixtures/skilljobs_search_sample.html and inspect selectors.
"""
import logging
import re
from pathlib import Path
from bs4 import BeautifulSoup
from src.models import ScrapedJob
from src.scrapers.base import BaseScraper
from src.utils import parse_relative_date, clean_html

log = logging.getLogger("jobhunt.scrapers.skilljobs")

SEARCH_URL = "https://skill.jobs/jobs"
BASE_URL = "https://skill.jobs"
FIXTURE_DIR = Path(__file__).parent.parent.parent / "tests" / "fixtures"


class SkillJobsScraper(BaseScraper):
    name = "skilljobs"

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
                log.warning(f"skilljobs search for '{keyword}' failed: {exc}")
        log.info(f"skilljobs: found {len(jobs)} listings")
        return jobs

    def fetch_job_detail(self, url: str) -> str:
        try:
            resp = self._get(url)
            self._save_fixture("skilljobs_detail_sample.html", resp.text)
            return self._parse_detail(resp.text)
        except Exception as exc:
            log.warning(f"skilljobs detail fetch failed for {url}: {exc}")
            return ""

    # ------------------------------------------------------------------ #

    def _search(self, keyword: str) -> list[ScrapedJob]:
        resp = self._get(SEARCH_URL, params={"keyword": keyword})
        self._save_fixture("skilljobs_search_sample.html", resp.text)
        jobs = self._parse_listing(resp.text)
        if not jobs:
            jobs = self._playwright_search(keyword)
        return jobs

    def _parse_listing(self, html: str) -> list[ScrapedJob]:
        soup = BeautifulSoup(html, "lxml")
        jobs = []

        for card in soup.select(".job-listing, .job-card, [class*='job-item'], article"):
            try:
                title_el = card.select_one("h2, h3, .title, [class*='title']")
                link_el = card.select_one("a[href]")
                if not title_el or not link_el:
                    continue

                href = link_el.get("href", "")
                if not href.startswith("http"):
                    href = BASE_URL + href

                company_el = card.select_one(".company, [class*='company']")
                location_el = card.select_one(".location, [class*='location']")
                date_el = card.select_one(".date, time, [class*='date']")

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
                log.debug(f"skilljobs card parse error: {exc}")

        return jobs

    def _parse_detail(self, html: str) -> str:
        soup = BeautifulSoup(html, "lxml")
        desc_el = soup.select_one(
            ".job-description, .job-details, [class*='description'], [class*='content'], article"
        )
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
                page.wait_for_selector(".job-listing, .job-card, article", timeout=10000)
                html = page.content()
                browser.close()
            self._save_fixture("skilljobs_search_sample.html", html)
            return self._parse_listing(html)
        except Exception as exc:
            log.warning(f"skilljobs playwright fallback failed: {exc}")
            return []

    def _save_fixture(self, filename: str, content: str):
        try:
            FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
            path = FIXTURE_DIR / filename
            if not path.exists():
                path.write_text(content, encoding="utf-8")
        except Exception:
            pass
