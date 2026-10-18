"""From a source's RawListing to the row stored in `jobs` (brief §6.2).

- Canonical URL: lower-cased scheme and host, no fragment, tracking parameters
  dropped, the rest sorted.
- Fingerprint, the cross-source identity of a job: sha1 of the normalized
  company, title (with its seniority) and city. The same job on BDJobs and
  LinkedIn gets one fingerprint, so a user is notified once.
- Nothing is invented: unknown dates and places stay unknown.
"""

import fnmatch
import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from jobhunt.config import Config
from jobhunt.normalize.text import clean_text, html_to_text, truncate
from jobhunt.normalize.titles import TitleNormalizer
from jobhunt.sources.models import PostedAtSource, RawListing

_WORD = re.compile(r"[\w+#]+")


@dataclass(frozen=True)
class JobRecord:
    source: str
    source_job_id: str
    url: str
    canonical_url: str
    fingerprint: str
    title: str
    company: str | None
    location_raw: str | None
    posted_at: datetime | None
    posted_at_source: PostedAtSource
    deadline: date | None
    jd_text: str
    jd_hash: str
    listing_json: str


class JobNormalizer:
    def __init__(self, config: Config) -> None:
        self._s = config.app.normalization
        self._titles = TitleNormalizer(config.title_rules, config.locations.place_names())
        self._tracking = [p.casefold() for p in self._s.tracking_params]
        self._suffixes = {s.casefold() for s in self._s.company_suffixes}
        # Every spelling of a district or its areas, longest first, to its district id.
        spellings: dict[str, str] = {}
        for district in config.locations.districts:
            for name in [district.name, *district.aliases, *district.areas]:
                spellings[self._fold(name)] = district.id
        self._places = sorted(spellings.items(), key=lambda item: -len(item[0]))

    def record(self, listing: RawListing) -> JobRecord:
        if listing.description_format == "html":
            text = html_to_text(listing.description)
        else:
            text = clean_text(listing.description)
        jd_text = truncate(text, self._s.jd_max_chars_stored)
        return JobRecord(
            source=listing.source,
            source_job_id=listing.source_job_id,
            url=listing.url,
            canonical_url=self.canonical_url(listing.url),
            fingerprint=self.fingerprint(listing),
            title=" ".join(listing.title.split()),
            company=listing.company,
            location_raw=listing.location,
            posted_at=listing.posted_at,
            posted_at_source=listing.posted_at_source if listing.posted_at else "unknown",
            deadline=listing.deadline,
            jd_text=jd_text,
            jd_hash=hashlib.sha256(jd_text.encode("utf-8")).hexdigest(),
            listing_json=json.dumps(
                {
                    "location_restrictions": list(listing.location_restrictions),
                    "work_mode": listing.work_mode,
                    "employment_type": listing.employment_type,
                    "salary": listing.salary,
                    "tags": list(listing.tags),
                    "extra": dict(listing.extra),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        )

    def canonical_url(self, url: str) -> str:
        parts = urlsplit(url.strip())
        query = [
            (key, value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if not any(fnmatch.fnmatchcase(key.casefold(), p) for p in self._tracking)
        ]
        return urlunsplit(
            (
                parts.scheme.lower(),
                parts.netloc.lower(),
                parts.path or "/",
                urlencode(sorted(query)),
                "",
            )
        )

    def fingerprint(self, listing: RawListing) -> str:
        title = self._titles.normalize(listing.title)
        key = "|".join(
            (
                self.company_key(listing.company),
                title.key,
                title.seniority or "",
                self.city_key(listing),
            )
        )
        return hashlib.sha1(key.encode("utf-8"), usedforsecurity=False).hexdigest()

    def company_key(self, company: str | None) -> str:
        words = _WORD.findall(self._fold(company or ""))
        while words and words[-1] in self._suffixes:
            words.pop()
        return " ".join(words)

    def city_key(self, listing: RawListing) -> str:
        """A Bangladesh district id when the location names one, else the first
        location part; "remote" for remote jobs with no place."""
        location = self._fold(listing.location or "")
        padded = f" {' '.join(_WORD.findall(location))} "
        for spelling, district in self._places:
            if f" {spelling} " in padded:
                return district
        if first := self._fold((listing.location or "").split(",")[0]):
            return first
        return "remote" if (listing.work_mode or "").casefold() == "remote" else ""

    @staticmethod
    def _fold(text: str) -> str:
        return " ".join(_WORD.findall(unicodedata.normalize("NFKC", text).casefold()))
