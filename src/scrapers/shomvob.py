import requests
from bs4 import BeautifulSoup
from .base import Scraper


class ShomvobScraper(Scraper):
    source_name = "shomvob"
    BASE_URL = "https://shomvob.com/jobs"

    def scrape(self, keywords: list[str], locations: list[str]) -> list[dict]:
        jobs = []
        for keyword in keywords:
            jobs.extend(self._search(keyword))
        return jobs

    def _search(self, keyword: str) -> list[dict]:
        params = {"q": keyword}
        resp = requests.get(self.BASE_URL, params=params, timeout=15)
        resp.raise_for_status()
        return self._parse(resp.text)

    def _parse(self, html: str) -> list[dict]:
        soup = BeautifulSoup(html, "lxml")
        jobs = []
        for card in soup.select(".job-card"):
            title_el = card.select_one(".job-title")
            company_el = card.select_one(".company-name")
            link_el = card.select_one("a")
            if not title_el:
                continue
            jobs.append({
                "source": self.source_name,
                "external_id": link_el.get("href", "") if link_el else "",
                "title": title_el.get_text(strip=True),
                "company": company_el.get_text(strip=True) if company_el else "",
                "location": "",
                "url": link_el.get("href", "") if link_el else "",
                "description": "",
                "posted_at": None,
            })
        return jobs
