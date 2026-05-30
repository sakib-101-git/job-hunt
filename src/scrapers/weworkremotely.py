"""
We Work Remotely scraper — public RSS feeds for programming roles.
Feeds cover: general programming, back-end, front-end, full-stack.
"""
import logging
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from bs4 import BeautifulSoup
from src.models import ScrapedJob
from src.scrapers.base import BaseScraper
from src.utils import clean_html, parse_relative_date

log = logging.getLogger("jobhunt.scrapers.weworkremotely")

FEEDS = [
    "https://weworkremotely.com/categories/remote-programming-jobs.rss",
    "https://weworkremotely.com/categories/remote-back-end-programming-jobs.rss",
    "https://weworkremotely.com/categories/remote-front-end-programming-jobs.rss",
    "https://weworkremotely.com/categories/remote-full-stack-programming-jobs.rss",
]


class WeWorkRemotelyScraper(BaseScraper):
    name = "weworkremotely"

    def fetch_listings(self) -> list[ScrapedJob]:
        keywords_lower = [kw.lower() for kw in self.config.search_keywords]
        jobs = []
        seen_ids: set[str] = set()
        for feed_url in FEEDS:
            try:
                resp = self._get(feed_url)
                for j in self._parse_feed(resp.text, keywords_lower):
                    if j.source_job_id not in seen_ids:
                        seen_ids.add(j.source_job_id)
                        jobs.append(j)
            except Exception as exc:
                log.warning(f"weworkremotely feed failed ({feed_url}): {exc}")
        log.info(f"weworkremotely: found {len(jobs)} listings")
        return jobs

    def fetch_job_detail(self, url: str) -> str:
        try:
            resp = self._get(url)
            soup = BeautifulSoup(resp.text, "lxml")
            desc = soup.select_one(".job-description, .listing-container, article")
            return clean_html(str(desc)) if desc else clean_html(soup.get_text())
        except Exception as exc:
            log.warning(f"weworkremotely detail fetch failed for {url}: {exc}")
            return ""

    def _parse_feed(self, xml_text: str, keywords_lower: list[str]) -> list[ScrapedJob]:
        root = ET.fromstring(xml_text)
        jobs = []
        for item in root.findall(".//item"):
            try:
                title_el = item.find("title")
                link_el = item.find("link")
                guid_el = item.find("guid")
                desc_el = item.find("description")
                pubdate_el = item.find("pubDate")

                raw_title = title_el.text if title_el is not None else ""
                # WWR title format: "Company: Job Title"
                if ": " in raw_title:
                    company, title = raw_title.split(": ", 1)
                else:
                    company, title = "", raw_title

                title = title.strip()
                company = company.strip()

                if not any(kw in title.lower() for kw in keywords_lower):
                    continue

                link = link_el.text.strip() if link_el is not None else ""
                guid = guid_el.text.strip() if guid_el is not None else link
                job_id = guid.split("/")[-1] or guid[-40:]

                description = clean_html(desc_el.text or "") if desc_el is not None else ""
                posted_date = self._parse_rfc2822(pubdate_el.text if pubdate_el is not None else "")

                jobs.append(ScrapedJob(
                    source=self.name,
                    source_job_id=job_id,
                    title=title,
                    company=company,
                    location="Worldwide",
                    url=link,
                    jd_text=description,
                    job_type="remote",
                    is_remote=True,
                    posted_date=posted_date,
                ))
            except Exception as exc:
                log.debug(f"weworkremotely item parse error: {exc}")
        return jobs

    def _parse_rfc2822(self, text: str):
        """Parse RFC 2822 dates used in RSS (e.g. 'Mon, 30 May 2026 00:00:00 +0000')."""
        if not text:
            return None
        try:
            return parsedate_to_datetime(text.strip())
        except Exception:
            return parse_relative_date(text)
