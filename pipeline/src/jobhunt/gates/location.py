"""Location gate (brief §6.5), strict by default.

A job passes only if it is in Bangladesh, or remote with explicit worldwide
eligibility (or eligibility that names Bangladesh). Each eligibility entry a
source publishes is an alternative ("Home based - EMEA; Home based - Worldwide"),
so entries are judged one by one and any passing entry passes the job.

- Bangladesh boards (policy `bangladesh_boards`): in Bangladesh unless the
  listing names a foreign place or carries an overseas note (defect D5).
- An entry passes if it names a Bangladeshi place, or says worldwide/anywhere
  without naming another country or region.
- Regions that contain Bangladesh without naming it ("APAC") pass only when
  `accept_region_lists_containing_bd` is on.
- Remote with no stated eligibility needs the description: `needs_facts`
  (or a pass, if `accept_unspecified_remote` is on).
- A worldwide entry is not taken at its word when the description states who
  may apply ("authorized to work in the US"), or, on boards listed in
  `mixed_tags_need_facts`, when another entry names a country or region (We
  Work Remotely tags some region-locked jobs "Anywhere in the World" too): the
  job `needs_facts`, so the description decides.
- Time-zone requirements never reject; they come back as warnings.
Every pass carries the quote it was decided on.
"""

import re
from dataclasses import dataclass

from jobhunt.config import Config
from jobhunt.extract.grounding import ELLIPSIS, normalize
from jobhunt.extract.schema import JobFacts
from jobhunt.gates.base import GateResult, PhraseFinder
from jobhunt.sources.models import RawListing

GATE = "location"


@dataclass(frozen=True)
class _Entry:
    status: str  # bangladesh | worldwide | region_with_bd | restricted | remote_only | unknown
    evidence: str


class LocationGate:
    def __init__(self, config: Config) -> None:
        self._policy = config.app.location_policy
        geo = config.geography
        self._bangladesh = PhraseFinder(config.locations.place_names_raw())
        self._foreign = PhraseFinder(
            name for c in geo.countries for name in [c.name, *c.aliases, *c.cities]
        )
        self._regions_with_bd = PhraseFinder(
            n for r in geo.regions if r.includes_bangladesh for n in [r.name, *r.aliases]
        )
        self._regions_without_bd = PhraseFinder(
            n for r in geo.regions if not r.includes_bangladesh for n in [r.name, *r.aliases]
        )
        self._worldwide = PhraseFinder(geo.worldwide_phrases)
        self._restricting = PhraseFinder(geo.restriction_phrases)
        self._remote = PhraseFinder(geo.remote_words)
        self._timezones = PhraseFinder(geo.timezone_markers)
        self._filler = {word.casefold() for word in geo.filler_words}

    def __call__(self, listing: RawListing) -> GateResult:
        entries = list(listing.location_restrictions) or (
            [listing.location] if listing.location else []
        )
        warnings = tuple(
            dict.fromkeys(tz for entry in entries for tz in self._timezones.find_all(entry))
        )

        if listing.source in self._policy.bangladesh_boards:
            return self._bangladesh_board(listing, entries, warnings)

        judged = [self._entry(entry) for entry in entries]
        if hit := next((e for e in judged if e.status == "bangladesh"), None):
            return GateResult(GATE, "pass", "bangladesh", hit.evidence, warnings)
        if hit := next((e for e in judged if e.status == "worldwide"), None):
            if doubt := self._doubt(listing, judged):
                return GateResult(GATE, "needs_facts", "worldwide_in_doubt", doubt, warnings)
            return GateResult(GATE, "pass", "worldwide", hit.evidence, warnings)
        if self._policy.accept_region_lists_containing_bd and (
            hit := next((e for e in judged if e.status == "region_with_bd"), None)
        ):
            return GateResult(GATE, "pass", "region_with_bangladesh", hit.evidence, warnings)
        if not entries and listing.extra.get("location_policy") == "worldwide":
            if doubt := self._doubt(listing, judged):
                return GateResult(GATE, "needs_facts", "worldwide_in_doubt", doubt, warnings)
            evidence = f"{listing.source}: no location restriction listed"
            return GateResult(GATE, "pass", "worldwide", evidence, warnings)

        if not judged or all(e.status in {"remote_only", "unknown"} for e in judged):
            return self._unspecified(listing, entries, judged, warnings)
        first = next(e for e in judged if e.status in {"restricted", "region_with_bd"})
        reason = (
            "region_list_without_bangladesh"
            if first.status == "region_with_bd"
            else ("region_restricted")
        )
        return GateResult(GATE, "fail", reason, first.evidence, warnings)

    def with_facts(self, listing: RawListing, facts: JobFacts) -> GateResult:
        """The listing's own verdict if it decided; otherwise the extracted
        (grounded) eligibility. An unstated eligibility now fails: the
        description was read and says nothing.

        Two checks on the model's answer. A restriction quoted from the text
        ("100% Remoto LATAM") overrides a listing that only looked worldwide
        (a source with no restriction field). And an answer stands only when its
        quote says it: "worldwide" needs a worldwide phrase ("Remote work is an
        option" is not one), "Bangladesh included" a Bangladeshi place (a model
        once answered it for a list of Asian countries without Bangladesh)."""
        listing_result = self(listing)
        warnings = listing_result.warnings
        evidence = facts.remote_eligibility_evidence
        if listing_result.status != "needs_facts":
            restricted = facts.remote_eligibility == "region_restricted" and evidence
            if listing_result.reason == "worldwide" and restricted:
                return GateResult(GATE, "fail", "region_restricted_in_text", evidence, warnings)
            return listing_result
        if facts.remote_eligibility == "bangladesh_included" and not (
            evidence and self._bangladesh.find(evidence)
        ):
            return GateResult(GATE, "fail", "bangladesh_not_named", evidence, warnings)
        if facts.country == "BD" or facts.remote_eligibility == "bangladesh_included":
            return GateResult(GATE, "pass", "bangladesh", evidence or "country: BD", warnings)
        if facts.remote_eligibility == "worldwide" and evidence:
            if not self._worldwide.find(evidence):
                # "Remote work is an option" says nothing about who may apply.
                return GateResult(GATE, "fail", "worldwide_not_stated", evidence, warnings)
            if listing_result.reason == "worldwide_in_doubt" and self._is_tag(listing, evidence):
                # The model only repeated the tag that was in doubt.
                return GateResult(GATE, "fail", "worldwide_unconfirmed", evidence, warnings)
            return GateResult(GATE, "pass", "worldwide", evidence, warnings)
        if facts.remote_eligibility == "region_restricted":
            return GateResult(GATE, "fail", "region_restricted", evidence, warnings)
        if facts.remote_eligibility == "unspecified":
            if self._policy.accept_unspecified_remote:
                return GateResult(GATE, "pass", "unspecified_remote_accepted", evidence, warnings)
            return GateResult(GATE, "fail", "unspecified_remote", evidence, warnings)
        return GateResult(GATE, "fail", "not_bangladesh", facts.country, warnings)

    # --- helpers --------------------------------------------------------------

    @staticmethod
    def _is_tag(listing: RawListing, evidence: str) -> bool:
        """The quote comes from the listing's location tags, not its text. Each
        piece of a quote joined with an ellipsis ("Anywhere in the World, ...")
        is judged on its own."""
        quote = re.sub(r"^\s*location\s*:\s*", "", evidence, flags=re.IGNORECASE)
        pieces = [p.strip(" ,;.") for p in ELLIPSIS.split(normalize(quote))]
        pieces = [p for p in pieces if p]
        tags = normalize(" ".join([listing.location or "", *listing.location_restrictions]))
        description = normalize(listing.description)
        return bool(pieces) and all(p in tags and p not in description for p in pieces)

    def _doubt(self, listing: RawListing, judged: list[_Entry]) -> str | None:
        """Why a worldwide tag can't be trusted on its own, or None."""
        if listing.source in self._policy.mixed_tags_need_facts:
            places = {"restricted", "region_with_bd"}
            if hit := next((e for e in judged if e.status in places), None):
                return hit.evidence
        return self._restricting.find(listing.description)

    def _bangladesh_board(
        self, listing: RawListing, entries: list[str], warnings: tuple[str, ...]
    ) -> GateResult:
        text = "; ".join(entries)
        if note := listing.extra.get("overseas_note"):
            return GateResult(GATE, "fail", "overseas", note, warnings)
        if foreign := self._foreign.find(text):
            return GateResult(GATE, "fail", "overseas", foreign, warnings)
        evidence = text or f"{listing.source} lists jobs in Bangladesh"
        return GateResult(GATE, "pass", "bangladesh", evidence, warnings)

    def _entry(self, entry: str) -> _Entry:
        if self._bangladesh.find(entry):
            return _Entry("bangladesh", entry)
        foreign = self._foreign.find(entry) or self._regions_without_bd.find(entry)
        if foreign:
            return _Entry("restricted", entry)
        if self._regions_with_bd.find(entry):
            return _Entry("region_with_bd", entry)
        if self._worldwide.find(entry):
            return _Entry("worldwide", entry)
        leftover = entry
        for finder in (self._remote, self._timezones):
            for phrase in finder.find_all(leftover):
                leftover = leftover.replace(phrase, " ")
        words = re.findall(r"[^\W\d_]+", leftover.casefold())
        if all(word in self._filler for word in words):
            return _Entry("remote_only", entry)
        return _Entry("unknown", entry)

    def _unspecified(
        self,
        listing: RawListing,
        entries: list[str],
        judged: list[_Entry],
        warnings: tuple[str, ...],
    ) -> GateResult:
        unknown = next((e.evidence for e in judged if e.status == "unknown"), None)
        if unknown:
            # A place we don't know is not Bangladesh, and not worldwide either.
            return GateResult(GATE, "fail", "not_bangladesh", unknown, warnings)
        remote = (listing.work_mode or "").casefold() == "remote" or any(
            e.status == "remote_only" for e in judged
        )
        if remote and self._policy.accept_unspecified_remote:
            evidence = "; ".join(entries) or "remote, eligibility not stated"
            return GateResult(GATE, "pass", "unspecified_remote_accepted", evidence, warnings)
        reason = "unspecified_remote" if remote else "no_location"
        return GateResult(GATE, "needs_facts", reason, "; ".join(entries) or None, warnings)
