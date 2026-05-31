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
                # Don't retry client errors (4xx) other than rate-limiting — they
                # won't change on retry (e.g. a 403 from a Cloudflare datacenter-IP
                # block). Fail fast so we don't waste time or spam the log.
                if 400 <= resp.status_code < 500 and resp.status_code != 429:
                    resp.raise_for_status()
                resp.raise_for_status()
                return resp
            except requests.RequestException as exc:
                last_exc = exc
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if status is not None and 400 <= status < 500 and status != 429:
                    raise  # client error — no point retrying
                log.warning(f"{self.name}: request failed ({exc}), attempt {attempt + 1}")
                if attempt < self.config.max_retries:
                    time.sleep(wait)
                    wait *= 2
        raise last_exc
