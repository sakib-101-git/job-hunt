import re
from .base import Scraper


class LinkedInEmailScraper(Scraper):
    """Parse job alert emails forwarded as plain text files in data/linkedin_emails/."""

    source_name = "linkedin_email"

    def scrape(self, keywords: list[str], locations: list[str]) -> list[dict]:
        from pathlib import Path
        from src.utils import ROOT

        email_dir = ROOT / "data" / "linkedin_emails"
        if not email_dir.exists():
            return []

        jobs = []
        for f in email_dir.glob("*.txt"):
            jobs.extend(self._parse_email(f.read_text(encoding="utf-8")))
        return jobs

    def _parse_email(self, text: str) -> list[dict]:
        jobs = []
        blocks = re.split(r"\n{2,}", text.strip())
        for block in blocks:
            lines = [l.strip() for l in block.splitlines() if l.strip()]
            if len(lines) < 2:
                continue
            url_match = re.search(r"https?://\S+", block)
            jobs.append({
                "source": self.source_name,
                "external_id": url_match.group(0) if url_match else lines[0],
                "title": lines[0],
                "company": lines[1] if len(lines) > 1 else "",
                "location": lines[2] if len(lines) > 2 else "",
                "url": url_match.group(0) if url_match else "",
                "description": block,
                "posted_at": None,
            })
        return jobs
