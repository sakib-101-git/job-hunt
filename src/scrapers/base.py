import time
import logging
from abc import ABC, abstractmethod
import requests
from src.models import ScrapedJob

log = logging.getLogger("jobhunt.scrapers")


class BaseScraper(ABC):
    name: str = ""

    def __init__(self, config):
        self.config = config
        self._session = requests.Session()
        self._session.headers["User-Agent"] = config.user_agent

    @abstractmethod
    def fetch_listings(self) -> list[ScrapedJob]:
        """Hit the search/listing pages and return partial ScrapedJob objects."""
        ...

    @abstractmethod
    def fetch_job_detail(self, url: str) -> str:
        """Fetch full JD text from a job detail page."""
        ...

    def _get(self, url: str, params: dict | None = None) -> requests.Response:
        time.sleep(self.config.request_delay)
        last_exc = None
        wait = 2
        for attempt in range(self.config.max_retries + 1):
            try:
                resp = self._session.get(
                    url,
                    params=params,
                    timeout=self.config.timeout_seconds,
                )
                resp.raise_for_status()
                return resp
            except requests.RequestException as exc:
                last_exc = exc
                log.warning(f"{self.name}: request failed ({exc}), attempt {attempt + 1}")
                if attempt < self.config.max_retries:
                    time.sleep(wait)
                    wait *= 2
        raise last_exc
