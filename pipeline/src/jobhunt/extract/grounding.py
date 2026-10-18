"""Grounding validator (brief §6.6): the anti-hallucination check.

- Every `*_evidence` quote must match a span of the description
  (rapidfuzz `partial_ratio` at least `min_evidence_score`, after normalizing
  case, quotes, dashes and whitespace), and every number in the quote must be in
  the description: fuzzy matching alone would accept "5 years" for "1 year". The
  years quote must also contain the extracted `min_years`. A field whose quote
  fails is reset to null/unknown and the failure is recorded.
- Every skill must appear in the description. Skills known to skills.yaml come
  back as their canonical id ("ReactJS" -> "react"); others as written.
- Years and deadline are cross-checked against what a regex finds; a
  disagreement is a flag for the audit, not a correction.
"""

import re
import unicodedata
from dataclasses import dataclass
from datetime import date

from rapidfuzz import fuzz

from jobhunt.config.models import Skills
from jobhunt.extract.schema import Education, JobFacts
from jobhunt.gates.base import PhraseFinder

_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-"})  # noqa: RUF001
_SPACE = re.compile(r"\s+")
_NUMBER = re.compile(r"\d+")
YEARS = re.compile(
    r"(?<![\d.])(\d{1,2})\s*\+?\s*(?:(?:-|to)\s*(\d{1,2})\s*)?(?:years?|yrs?)\b", re.IGNORECASE
)
# Years with "experience" next to them, before or after: what the rules-only
# extractor trusts ("in business for 25 years" is not a requirement).
_SPAN = r"(?<![\d.])(\d{1,2})\s*\+?\s*(?:(?:-|to)\s*(\d{1,2})\s*)?(?:years?|yrs?)\b"
_EXPERIENCE_YEARS = (
    re.compile(rf"experience[^.\n\d]{{0,40}}?{_SPAN}", re.IGNORECASE),
    re.compile(rf"{_SPAN}[^.\n\d]{{0,25}}?experience", re.IGNORECASE),
)


def experience_years(text: str) -> tuple[int, int | None, str] | None:
    """(min years, max years, the quote) from the first experience requirement."""
    hits = [m for pattern in _EXPERIENCE_YEARS if (m := pattern.search(text))]
    if not hits:
        return None
    first = min(hits, key=lambda m: m.start())
    return int(first[1]), int(first[2]) if first[2] else None, first[0]


MIN_QUOTE = 4


@dataclass(frozen=True)
class Grounded:
    facts: JobFacts
    failures: tuple[str, ...]
    flags: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.failures


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).translate(_QUOTES).casefold()
    return _SPACE.sub(" ", text).strip()


class Grounder:
    def __init__(self, skills: Skills, min_score: float) -> None:
        self._min = min_score
        self._ids: dict[str, str] = {}
        for skill in skills.all_skills:
            for spelling in skill.spellings:
                self._ids[normalize(spelling)] = skill.id
        self._finder = PhraseFinder(s for skill in skills.all_skills for s in skill.spellings)
        self._clear = PhraseFinder(
            s for skill in skills.all_skills if not skill.ambiguous for s in skill.spellings
        )

    def skills_in(self, text: str, *, include_ambiguous: bool = True) -> list[str]:
        """Canonical ids of the known skills the text mentions, in order."""
        finder = self._finder if include_ambiguous else self._clear
        ids = (self._ids.get(normalize(hit)) for hit in finder.find_all(text))
        return list(dict.fromkeys(i for i in ids if i))

    def quoted(self, quote: str | None, text: str) -> bool:
        if quote is None or len(quote.strip()) < MIN_QUOTE:
            return False
        if not set(_NUMBER.findall(quote)) <= set(_NUMBER.findall(text)):
            return False
        return fuzz.partial_ratio(normalize(quote), normalize(text)) >= self._min

    def ground(self, facts: JobFacts, text: str, listing_deadline: date | None) -> Grounded:
        failures: list[str] = []
        changes: dict[str, object] = {}

        def check(field: str, reset: dict[str, object], must_contain: object = None) -> None:
            quote = getattr(facts, field)
            if quote is None:
                return
            contains = must_contain is None or str(must_contain) in _NUMBER.findall(quote)
            if not contains or not self.quoted(quote, text):
                failures.append(field)
                changes.update({field: None, **reset})

        check("is_cse_role_evidence", {})
        check("seniority_evidence", {"seniority": "unknown"})
        check("years_evidence", {"min_years": None, "max_years": None}, facts.min_years)
        check(
            "education_evidence",
            {"education": Education(level="unknown", fields=[], required=False)},
        )
        unspecified = "unspecified" if facts.work_mode == "remote" else facts.remote_eligibility
        check("remote_eligibility_evidence", {"remote_eligibility": unspecified})
        check("deadline_evidence", {"application_deadline": None})

        present = set(self.skills_in(text))
        lowered = normalize(text)
        for field in ("required_skills", "preferred_skills"):
            kept = []
            for skill in getattr(facts, field):
                canonical = self._ids.get(normalize(skill))
                if canonical and canonical in present:
                    kept.append(canonical)
                elif re.search(rf"(?<!\w){re.escape(normalize(skill))}(?!\w)", lowered):
                    kept.append(skill)
                else:
                    failures.append(f"{field}:{skill}")
            changes[field] = list(dict.fromkeys(kept))

        grounded = facts.model_copy(update=changes)
        flags = self._flags(grounded, text, listing_deadline)
        return Grounded(grounded, tuple(failures), tuple(flags))

    @staticmethod
    def _flags(facts: JobFacts, text: str, listing_deadline: date | None) -> list[str]:
        flags = []
        found = {int(m[1]) for m in YEARS.finditer(text)}
        if facts.min_years is not None and found and facts.min_years not in found:
            flags.append("min_years_disagrees_with_text")
        if (
            listing_deadline is not None
            and facts.application_deadline is not None
            and facts.application_deadline != listing_deadline
        ):
            flags.append("deadline_disagrees_with_listing")
        return flags
