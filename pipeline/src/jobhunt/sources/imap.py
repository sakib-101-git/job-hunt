"""The owner's LinkedIn job-alert emails, read over IMAP (read-only).

linkedin.com is never fetched: each job comes from a `/jobs/view/<id>` link in
an alert email, with the link text as the title and the next two text lines of
the card as company and location. LinkedIn changes this HTML from time to time;
keep a redacted real email as the test fixture and update the parser with it.
"""

import asyncio
import contextlib
import email
import imaplib
import re
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.policy import default as default_policy
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Any

from jobhunt.config.models import ImapSource
from jobhunt.sources.base import FetchContext
from jobhunt.sources.http import FetchError
from jobhunt.sources.models import RawListing, SourceResult

_JOB_LINK = re.compile(r"/jobs/view/(\d+)")


# imaplib.IMAP4_SSL, or a fake with the same login/select/search/fetch/logout.
Connect = Callable[[str], Any]


class LinkedInAlerts:
    def __init__(
        self, name: str, settings: ImapSource, connect: Connect = imaplib.IMAP4_SSL
    ) -> None:
        self.name = name
        self._s = settings
        self._connect = connect

    async def fetch(self, ctx: FetchContext) -> SourceResult:
        since = ctx.now - timedelta(days=self._s.lookback_days)
        messages = await asyncio.to_thread(self._read, ctx.environ, since)
        listings: dict[str, RawListing] = {}
        for sent_at, html_body in messages:
            for job in parse_alert(html_body):
                listings.setdefault(job[0], self._listing(job, sent_at))
        return SourceResult(list(listings.values()))

    def _read(self, environ: Mapping[str, str], since: datetime) -> list[tuple[str, str]]:
        try:
            box = self._connect(environ["IMAP_HOST"])
            box.login(environ["IMAP_EMAIL"], environ["IMAP_APP_PASSWORD"])
            box.select("INBOX", readonly=True)
            _, found = box.search(
                None, "FROM", f'"{self._s.sender}"', "SINCE", since.strftime("%d-%b-%Y")
            )
            messages = []
            for number in (found[0] or b"").split():
                _, parts = box.fetch(number.decode(), "(RFC822)")
                raw = next(p[1] for p in parts if isinstance(p, tuple))
                message = email.message_from_bytes(raw, policy=default_policy)
                if isinstance(message, EmailMessage) and (body := _html(message)):
                    messages.append((str(message.get("Date", "")), body))
            box.logout()
        except (imaplib.IMAP4.error, OSError, KeyError, StopIteration) as exc:
            raise FetchError(self.name, f"IMAP failed: {type(exc).__name__}: {exc}") from exc
        return messages

    def _listing(self, job: tuple[str, str, str | None, str | None], sent_at: str) -> RawListing:
        job_id, title, company, location = job
        extra = {}
        with contextlib.suppress(TypeError, ValueError):
            extra["alert_sent_at"] = parsedate_to_datetime(sent_at).isoformat()
        return RawListing(
            source=self.name,
            source_job_id=job_id,
            url=self._s.job_url.format(id=job_id),
            title=title,
            # The alert has no description; extraction falls back to the title.
            description=" · ".join(p for p in (title, company, location) if p),
            description_format="text",
            company=company,
            location=location,
            location_restrictions=(location,) if location else (),
            extra=extra,
        )


def _html(message: EmailMessage) -> str | None:
    part = message.get_body(preferencelist=("html",))
    return part.get_content() if part is not None else None


class _AlertParser(HTMLParser):
    """Collects text chunks with the link they sit in: a job id, "" for any other
    link, or None outside links."""

    def __init__(self) -> None:
        super().__init__()
        self.chunks: list[tuple[str | None, str]] = []
        self._link: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            match = _JOB_LINK.search(dict(attrs).get("href") or "")
            self._link = match[1] if match else ""

    def handle_endtag(self, tag: str) -> None:
        if tag == "a":
            self._link = None

    def handle_data(self, data: str) -> None:
        if text := " ".join(data.split()):
            self.chunks.append((self._link, text))


def parse_alert(html_body: str) -> list[tuple[str, str, str | None, str | None]]:
    """(job id, title, company, location) for each job card, in order."""
    parser = _AlertParser()
    parser.feed(html_body)
    jobs: dict[str, tuple[str, str, str | None, str | None]] = {}
    chunks = parser.chunks
    for i, (link, text) in enumerate(chunks):
        if not link or link in jobs:
            continue
        following: list[str] = []
        for next_link, next_text in chunks[i + 1 :]:
            if next_link is not None or len(following) == 2:
                break  # the card ends at the next link
            following.append(next_text)
        company = following[0] if following else None
        location = following[1] if len(following) > 1 else None
        jobs[link] = (link, text, company, location)
    return list(jobs.values())
