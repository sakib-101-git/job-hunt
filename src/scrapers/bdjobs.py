import requests
from bs4 import BeautifulSoup
from .base import Scraper


class BDJobsScraper(Scraper):
    source_name = "bdjobs"
    BASE_URL = "https://jobs.bdjobs.com/jobsearch.asp"

    def scrape(self, keywords: list[str], locations: list[str]) -> list[dict]:
        jobs = []
        for keyword in keywords:
            jobs.extend(self._search(keyword))
        return jobs

    def _search(self, keyword: str) -> list[dict]:
        params = {"txtkeyword": keyword, "iPage": 1}
        resp = requests.get(self.BASE_URL, params=params, timeout=15)
        resp.raise_for_status()
        return self._parse(resp.text)

    def _parse(self, html: str) -> list[dict]:
        soup = BeautifulSoup(html, "lxml")
        jobs = []
        for row in soup.select(".job-tittle"):
            a = row.find("a")
            if not a:
                continue
            jobs.append({
                "source": self.source_name,
                "external_id": a.get("href", ""),
                "title": a.get_text(strip=True),
                "company": "",
                "location": "",
                "url": a.get("href", ""),
                "description": "",
                "posted_at": None,
            })
        return jobs
