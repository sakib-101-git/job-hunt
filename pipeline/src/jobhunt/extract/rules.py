"""Rules-only facts (brief §6.6), used when every LLM is out of budget: regex
for years and degree, skills from the skills.yaml vocabulary, the domain from
the title match, remote eligibility from the location gate (structured fields
only). Jobs with rules-only facts are extracted again once a model is free.
"""

import re

from jobhunt.config import Config
from jobhunt.extract.grounding import Grounder, experience_years
from jobhunt.extract.schema import (
    Education,
    EducationField,
    EducationLevel,
    EmploymentType,
    JobFacts,
    RemoteEligibility,
    SeniorityLevel,
    WorkMode,
)
from jobhunt.gates.base import GateResult, PhraseFinder
from jobhunt.sources.models import RawListing
from jobhunt.taxonomy.matcher import TitleMatch

PROVIDER = "rules"
MODEL = "rules-v1"

_DEGREES: list[tuple[EducationLevel, re.Pattern[str]]] = [
    ("phd", re.compile(r"\b(ph\.?\s?d|doctorate)\b", re.I)),
    ("master", re.compile(r"\b(m\.?\s?sc|masters?|m\.?\s?s\.?|mba)\b", re.I)),
    ("bachelor", re.compile(
        r"\b(b\.?\s?sc|bachelor'?s?|b\.?\s?s\.?|b\.?\s?tech|b\.?\s?e\.?|graduate degree)\b", re.I
    )),
    ("diploma", re.compile(r"\bdiploma\b", re.I)),
]  # fmt: skip
_FIELDS: list[tuple[EducationField, re.Pattern[str]]] = [
    ("cse", re.compile(r"\b(cse|computer science (and|&) engineering)\b", re.I)),
    ("cs", re.compile(r"\bcomputer science\b", re.I)),
    ("it", re.compile(r"\b(information technology|IT)\b")),
    ("software", re.compile(r"\bsoftware engineering\b", re.I)),
    ("eee", re.compile(r"\b(eee|electrical (and|&) electronic)", re.I)),
]
_EMPLOYMENT: list[tuple[EmploymentType, re.Pattern[str]]] = [
    ("internship", re.compile(r"\bintern(ship)?\b", re.I)),
    ("part_time", re.compile(r"\bpart[\s-]?time\b", re.I)),
    ("contract", re.compile(r"\b(contract(ual)?|freelance|temporary)\b", re.I)),
    ("full_time", re.compile(r"\b(full[\s-]?time|permanent)\b", re.I)),
]
_WORK_MODE: list[tuple[WorkMode, re.Pattern[str]]] = [
    ("hybrid", re.compile(r"\bhybrid\b", re.I)),
    ("remote", re.compile(r"\b(remote|home|work from home|wfh)\b", re.I)),
    ("onsite", re.compile(r"\b(office|on[\s-]?site)\b", re.I)),
]
_SENIORITY: dict[str, SeniorityLevel] = {
    "intern": "intern", "entry": "entry", "junior": "junior", "mid": "mid",
    "senior": "senior", "lead_plus": "lead_plus",
}  # fmt: skip


class RulesExtractor:
    def __init__(self, config: Config, grounder: Grounder) -> None:
        self._grounder = grounder
        self._red_flags = PhraseFinder(config.app.red_flags)

    def facts(
        self, listing: RawListing, text: str, match: TitleMatch, location: GateResult
    ) -> JobFacts:
        years = experience_years(text)
        degree = next(((lvl, m) for lvl, p in _DEGREES if (m := p.search(text))), None)
        fields = [name for name, pattern in _FIELDS if pattern.search(text)]
        is_cse = match.primary_domain is not None and match.method != "none"
        return JobFacts(
            is_cse_role=is_cse,
            is_cse_role_evidence=None,
            primary_domain=match.primary_domain if is_cse else None,
            secondary_domains=list(match.secondary_domains)[:3] if is_cse else [],
            normalized_title=listing.title,
            seniority=_SENIORITY.get(match.seniority or "", "unknown"),
            seniority_evidence=None,
            min_years=years[0] if years else None,
            max_years=years[1] if years else None,
            years_evidence=years[2] if years else None,
            education=Education(
                level=degree[0] if degree else "unknown",
                fields=fields,
                required=degree is not None,
            ),
            education_evidence=degree[1][0] if degree else None,
            required_skills=self._grounder.skills_in(text, include_ambiguous=False),
            preferred_skills=[],
            employment_type=_first(_EMPLOYMENT, listing.employment_type, "unknown"),
            work_mode=_first(_WORK_MODE, listing.work_mode, "unknown"),
            country="BD" if location.reason == "bangladesh" else None,
            city=None,
            remote_eligibility=_eligibility(location, listing.work_mode),
            remote_eligibility_evidence=location.evidence if location.status == "pass" else None,
            timezone_requirement=", ".join(location.warnings) or None,
            salary=None,
            application_deadline=listing.deadline,
            deadline_evidence=None,
            posted_date_in_text=None,
            language_requirements=[],
            red_flags=self._red_flags.find_all(text),
            summary=" ".join(text.split()[:25]),
        )


def _first[T](table: list[tuple[T, re.Pattern[str]]], text: str | None, default: T) -> T:
    if text:
        for value, pattern in table:
            if pattern.search(text):
                return value
    return default


def _eligibility(location: GateResult, work_mode: str | None) -> RemoteEligibility:
    remote = (work_mode or "").casefold() in {"remote", "home"}
    if location.status == "pass":
        if location.reason == "worldwide":
            return "worldwide"
        return "bangladesh_included" if remote else "not_remote"
    if location.reason in {"region_restricted", "region_list_without_bangladesh"}:
        return "region_restricted"
    return "unspecified" if remote else "not_remote"
