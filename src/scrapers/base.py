from abc import ABC, abstractmethod


class Scraper(ABC):
    source_name: str = ""

    @abstractmethod
    def scrape(self, keywords: list[str], locations: list[str]) -> list[dict]:
        """Return a list of job dicts with keys:
        source, external_id, title, company, location, url, description, posted_at
        """
        ...
