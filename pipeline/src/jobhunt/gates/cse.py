"""CSE gate (brief §6.4): a job passes only if it matched a selectable taxonomy
domain (or the extraction says it is a CSE role, with a quote) and its title is
not on the owner's `non_cse_exclusions` list. A title the taxonomy doesn't know
goes to extraction only if it carries a tech signal (`cse_gate.tech_signals`);
"Video Creator" never costs an LLM call.

Exclusions match as whole words on normalized titles, so "Senior Civil Engineer"
is excluded by "Civil Engineer". An entry with a "(Non-X)" qualifier, such as
"Sales Engineer (Non-Software)", is a "depends": a title that mentions X passes
it, and one that doesn't ("Ubuntu Sales Engineer") goes to extraction to decide.
"""

import re
from dataclasses import dataclass

from jobhunt.config import Config
from jobhunt.gates.base import GateResult, PhraseFinder
from jobhunt.normalize.titles import TitleNormalizer
from jobhunt.taxonomy.matcher import TitleMatch

_NON_QUALIFIER = re.compile(r"^(?P<main>.*?)\s*\(\s*non[\s-]*(?P<unless>[^)]+)\)\s*$", re.I)

GATE = "cse"


@dataclass(frozen=True)
class _Exclusion:
    entry: str
    tokens: tuple[str, ...]
    unless: tuple[str, ...]


class CseGate:
    def __init__(self, config: Config) -> None:
        self._normalizer = TitleNormalizer(config.title_rules, config.locations.place_names())
        self._selectable = config.taxonomy.selectable_slugs
        self._exclusions = [self._exclusion(e) for e in config.taxonomy.non_cse_exclusions]
        self._tech = PhraseFinder(config.app.cse_gate.tech_signals)

    def _exclusion(self, entry: str) -> _Exclusion:
        qualified = _NON_QUALIFIER.match(entry)
        main = qualified["main"] if qualified else entry
        unless = self._normalizer.normalize(qualified["unless"]).tokens if qualified else ()
        return _Exclusion(entry, self._normalizer.normalize(main).tokens, unless)

    def excluded_by(self, title: str) -> tuple[str, bool] | None:
        """The exclusion entry the title hits, and whether it is a qualified one."""
        tokens = self._normalizer.normalize(title).tokens
        for exclusion in self._exclusions:
            if not exclusion.tokens or not _contains(tokens, exclusion.tokens):
                continue
            if exclusion.unless and set(exclusion.unless) <= set(tokens):
                continue
            return exclusion.entry, bool(exclusion.unless)
        return None

    def __call__(
        self,
        title: str,
        match: TitleMatch,
        llm_is_cse: bool | None = None,
        llm_evidence: str | None = None,
    ) -> GateResult:
        if hit := self.excluded_by(title):
            entry, qualified = hit
            if not qualified:
                return GateResult(GATE, "fail", "non_cse_title", entry)
            if llm_is_cse is None:
                return GateResult(GATE, "needs_facts", "ambiguous_exclusion", entry)
        if match.primary_domain in self._selectable and not hit:
            return GateResult(
                GATE, "pass", "taxonomy", f"{match.matched_title} ({match.primary_domain})"
            )
        if llm_is_cse is True and llm_evidence:
            return GateResult(GATE, "pass", "extraction", llm_evidence)
        if llm_is_cse is False:
            return GateResult(GATE, "fail", "not_cse_per_extraction", llm_evidence)
        if hit:
            return GateResult(GATE, "fail", "not_cse_per_extraction", llm_evidence or hit[0])
        if signal := self._tech.find(title):
            return GateResult(GATE, "needs_facts", "title_not_in_taxonomy", signal)
        return GateResult(GATE, "fail", "no_tech_signal", title)


def _contains(tokens: tuple[str, ...], phrase: tuple[str, ...]) -> bool:
    size = len(phrase)
    return any(tokens[i : i + size] == phrase for i in range(len(tokens) - size + 1))
