"""
LinkedIn email scraper.

Connects to Gmail via IMAP, finds LinkedIn job alert emails from the last 24 hours,
and parses the HTML email body to extract job listings.

Prerequisites:
- Gmail account set up with a LinkedIn job alert
- Gmail App Password (not your real password) in .env as IMAP_APP_PASSWORD
- IMAP_HOST=imap.gmail.com, IMAP_EMAIL=you@gmail.com in .env
"""
import imaplib
import email
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from email.header import decode_header
from pathlib import Path
from bs4 import BeautifulSoup
from src.models import ScrapedJob
from src.scrapers.base import BaseScraper

log = logging.getLogger("jobhunt.scrapers.linkedin")
FIXTURE_DIR = Path(__file__).parent.parent.parent / "tests" / "fixtures"


class LinkedInEmailScraper(BaseScraper):
    name = "linkedin_email"

    def fetch_listings(self) -> list[ScrapedJob]:
        if not (self.config.imap_host and self.config.imap_email and self.config.imap_app_password):
            log.info("LinkedIn email scraper skipped — IMAP credentials not configured")
            return []

        try:
            jobs = self._fetch_from_imap()
            log.info(f"linkedin_email: found {len(jobs)} listings")
            return jobs
        except Exception as exc:
            log.error(f"linkedin_email IMAP error: {exc}")
            return []

    def fetch_job_detail(self, url: str) -> str:
        # LinkedIn public job pages can be fetched as guest for the JD text
        try:
            resp = self._get(url)
            return self._parse_linkedin_detail(resp.text)
        except Exception as exc:
            log.warning(f"linkedin detail fetch failed for {url}: {exc}")
            return ""

    # ------------------------------------------------------------------ #

    def _fetch_from_imap(self) -> list[ScrapedJob]:
        with imaplib.IMAP4_SSL(self.config.imap_host) as mail:
            mail.login(self.config.imap_email, self.config.imap_app_password)
            mail.select("inbox")

            since_date = (datetime.now(timezone.utc) - timedelta(hours=self.config.max_age_hours))
            imap_date = since_date.strftime("%d-%b-%Y")

            _, msg_ids = mail.search(
                None,
                f'FROM "jobs-noreply@linkedin.com" SINCE "{imap_date}"',
            )

            jobs = []
            for msg_id in (msg_ids[0].split() if msg_ids[0] else []):
                try:
                    _, msg_data = mail.fetch(msg_id, "(RFC822)")
                    raw = msg_data[0][1]
                    msg = email.message_from_bytes(raw)
                    html_body = self._get_html_body(msg)
                    if html_body:
                        jobs.extend(self._parse_email_html(html_body))
                except Exception as exc:
                    log.debug(f"linkedin email parse error: {exc}")

        return jobs

    def _get_html_body(self, msg) -> str | None:
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/html":
                    payload = part.get_payload(decode=True)
                    charset = part.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="replace")
        else:
            if msg.get_content_type() == "text/html":
                payload = msg.get_payload(decode=True)
                charset = msg.get_content_charset() or "utf-8"
                return payload.decode(charset, errors="replace")
        return None

    def _parse_email_html(self, html: str) -> list[ScrapedJob]:
        soup = BeautifulSoup(html, "lxml")
        jobs = []

        # LinkedIn alert emails contain job blocks with title + company + location + link
        for job_el in soup.select("[data-job-id], .job-card-container, table[class*='job']"):
            try:
                title_el = job_el.select_one("a[class*='title'], h3, strong")
                if not title_el:
                    continue
                link_el = job_el.select_one("a[href*='linkedin.com/jobs']")
                if not link_el:
                    continue

                href = link_el.get("href", "")
                job_id = self._extract_linkedin_id(href)
                if not job_id:
                    job_id = href[-20:]

                company_el = job_el.select_one("[class*='company'], [class*='subtitle']")
                location_el = job_el.select_one("[class*='location']")

                jobs.append(ScrapedJob(
                    source=self.name,
                    source_job_id=job_id,
                    title=title_el.get_text(strip=True),
                    company=company_el.get_text(strip=True) if company_el else "",
                    location=location_el.get_text(strip=True) if location_el else "",
                    url=href,
                ))
            except Exception as exc:
                log.debug(f"linkedin email job parse error: {exc}")

        return jobs

    def _parse_linkedin_detail(self, html: str) -> str:
        soup = BeautifulSoup(html, "lxml")
        desc_el = soup.select_one(
            ".description__text, .show-more-less-html__markup, [class*='description']"
        )
        if desc_el:
            from src.utils import clean_html
            return clean_html(str(desc_el))
        return ""

    def _extract_linkedin_id(self, url: str) -> str:
        m = re.search(r"/jobs/view/(\d+)", url)
        if m:
            return m.group(1)
        m = re.search(r"currentJobId=(\d+)", url)
        return m.group(1) if m else ""
