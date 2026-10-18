"""Assign taxonomy domains to a listing title.

Match order: exact normalized key -> alias -> fuzzy. Aliases are taxonomy titles
without their bracketed or dash-separated qualifier ("NOC Engineer — Network
Operations" -> "NOC Engineer"). Each stage also tries the listing title without
brackets and each multi-word segment of it.

Fuzzy matching repairs typos against the taxonomy vocabulary with rapidfuzz,
then scores candidates by IDF-weighted token overlap. Weighting matters:
"Civil Engineer" shares "engineer" with hundreds of titles, but "civil" appears
in none, so it scores low instead of matching "Software Engineer". Listing words
that the candidate lacks but that appear in the candidate's domains ("Laravel" in
"PHP Laravel Developer") cost only part of their weight. A fuzzy candidate must also share the
listing's role noun, its last word, so "Graphic Designer" never matches
"Graphics Programmer". A one-word title ("Analyst", "Head of Growth" once
seniority is stripped) is too little to go on and is matched exactly or not at all.

Titles left unmatched go to the LLM classifier in a later stage.
"""

import math
import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Literal

from rapidfuzz import fuzz, process

from jobhunt.config import Config
from jobhunt.config.models import Seniority, TitleMatching
from jobhunt.normalize.titles import SEGMENT_SPLIT, NormalizedTitle, TitleNormalizer

# "llm": set by the batched title classifier for titles the taxonomy doesn't know.
Method = Literal["exact", "alias", "fuzzy", "llm", "none"]

_PARENTHETICAL = re.compile(r"\([^)]*\)|\[[^\]]*\]")
_QUALIFIER = re.compile(r"\s[—–]\s.*$")  # noqa: RUF001 (en dash is intended)


@dataclass(frozen=True)
class TitleMatch:
    normalized: str
    seniority: Seniority | None
    method: Method
    primary_domain: str | None = None
    secondary_domains: tuple[str, ...] = ()
    confidence: float = 0.0
    matched_title: str | None = None


@dataclass
class _Entry:
    tokens: Counter[str]
    titles: list[tuple[str, str]] = field(default_factory=list)  # (title, domain slug)

    @property
    def domains(self) -> set[str]:
        return {slug for _, slug in self.titles}


@dataclass(frozen=True)
class _Scored:
    score: float
    shared: int
    entry: _Entry


class TitleMatcher:
    def __init__(self, config: Config) -> None:
        self._settings: TitleMatching = config.app.title_matching
        self._normalizer = TitleNormalizer(config.title_rules, config.locations.place_names())
        self._selectable = config.taxonomy.selectable_slugs

        titles_by_domain = config.taxonomy.titles_by_domain()
        self._domain_size = {slug: len(titles) for slug, titles in titles_by_domain.items()}
        self._exact: dict[str, _Entry] = {}
        self._alias: dict[str, _Entry] = {}
        for slug, titles in titles_by_domain.items():
            for title in titles:
                self._add(self._exact, self._normalizer.normalize(title), title, slug)
                for alias in {_PARENTHETICAL.sub(" ", title), _QUALIFIER.sub("", title)} - {title}:
                    self._add(self._alias, self._normalizer.normalize(alias), title, slug)
        self._alias = {k: v for k, v in self._alias.items() if k not in self._exact}

        document_freq = Counter(token for entry in self._exact.values() for token in entry.tokens)
        total = len(self._exact)
        self._weight = {t: math.log((total + 1) / (df + 1)) + 1 for t, df in document_freq.items()}
        self._unknown_weight = math.log(total + 1) + 1
        self._vocabulary = sorted(set(document_freq) | self._normalizer.variant_words)
        self._token_domains: defaultdict[str, set[str]] = defaultdict(set)
        for entry in self._exact.values():
            for token in entry.tokens:
                self._token_domains[token] |= entry.domains
        self._by_token: defaultdict[str, list[_Entry]] = defaultdict(list)
        for entry in [*self._exact.values(), *self._alias.values()]:
            for token in entry.tokens:
                self._by_token[token].append(entry)

    @staticmethod
    def _add(index: dict[str, _Entry], normalized: NormalizedTitle, title: str, slug: str) -> None:
        if not normalized.tokens:
            return
        entry = index.setdefault(normalized.key, _Entry(Counter(normalized.tokens)))
        if (title, slug) not in entry.titles:
            entry.titles.append((title, slug))

    # --- public ---------------------------------------------------------------

    def match(self, title: str) -> TitleMatch:
        normalized = self._normalizer.normalize(title)
        base = TitleMatch(normalized.text, normalized.seniority, "none")
        if not normalized.tokens:
            return base
        variants = self._variants(title, normalized)

        ladder_only: tuple[Method, _Entry] | None = None
        stages: tuple[tuple[dict[str, _Entry], Method], ...] = (
            (self._exact, "exact"),
            (self._alias, "alias"),
        )
        for index, method in stages:
            for variant in variants:
                entry = index.get(variant.key)
                if entry is None:
                    continue
                if self._has_selectable(entry):
                    return self._result(base, method, 1.0, entry, [])
                ladder_only = ladder_only or (method, entry)

        scored = [s for s in self._fuzzy(variants) if self._has_selectable(s.entry)]
        if scored and scored[0].score >= self._settings.min_similarity:
            best = scored[0]
            margin = best.score - self._settings.secondary_margin
            others = [s.entry for s in scored[1:] if s.score >= margin]
            return self._result(base, "fuzzy", best.score, best.entry, others)
        if ladder_only:
            # Only a career-stage title matched ("Lead Mobile Engineer"): no functional
            # domain is known, but the seniority-ladder hit is still worth reporting.
            return self._result(base, ladder_only[0], 1.0, ladder_only[1], [])
        return base

    # --- helpers --------------------------------------------------------------

    def _variants(self, title: str, full: NormalizedTitle) -> list[NormalizedTitle]:
        """The full title, then without brackets, then each multi-word segment.

        Brackets and trailing segments often carry a stack or team, as in
        "Full-Stack Developer (MERN)" or "Software Engineer - Payments".
        A variant counts only if every word it drops is a taxonomy word, so
        "Site Engineer (Civil)" is never matched as plain "Site Engineer".
        """
        bare = _PARENTHETICAL.sub(" ", title)
        candidates = [self._normalizer.normalize(bare)]
        for segment in SEGMENT_SPLIT.split(bare):
            normalized = self._normalizer.normalize(segment)
            if len(normalized.tokens) >= 2:
                candidates.append(normalized)
        unique = {full.key: full}
        for candidate in candidates:
            dropped = Counter(self._repair(full.tokens)) - Counter(self._repair(candidate.tokens))
            if candidate.tokens and all(token in self._weight for token in dropped):
                unique.setdefault(candidate.key, candidate)
        return list(unique.values())

    def _fuzzy(self, variants: Iterable[NormalizedTitle]) -> list[_Scored]:
        scored: list[_Scored] = []
        for variant in variants:
            if len(variant.tokens) < 2:
                continue
            repaired = self._repair(variant.tokens)
            query, role = Counter(repaired), repaired[-1]
            for entry in self._candidates(query):
                if role not in entry.tokens:
                    continue
                shared = sum((query & entry.tokens).values())
                scored.append(_Scored(self._similarity(query, entry), shared, entry))
        scored.sort(key=lambda s: (-s.score, -s.shared, self._specificity(s.entry)))
        return scored

    def _repair(self, tokens: Iterable[str]) -> list[str]:
        """Swap misspelled tokens ("sofware") for the closest taxonomy word."""
        repaired = []
        for token in tokens:
            if token not in self._weight and len(token) >= self._settings.spelling_min_length:
                found = process.extractOne(
                    token,
                    self._vocabulary,
                    scorer=fuzz.ratio,
                    score_cutoff=self._settings.spelling_min_ratio,
                )
                token = self._normalizer.fold(found[0]) if found else token
            repaired.append(token)
        return repaired

    def _candidates(self, tokens: Counter[str]) -> list[_Entry]:
        seen: dict[int, _Entry] = {}
        for token in tokens:
            for entry in self._by_token.get(token, ()):
                seen.setdefault(id(entry), entry)
        return list(seen.values())

    def _w(self, tokens: Counter[str], cheap_in: set[str] | None = None) -> float:
        """Summed token weight. Tokens seen in any `cheap_in` domain are discounted."""
        total = 0.0
        for token, count in tokens.items():
            weight = self._weight.get(token, self._unknown_weight)
            if cheap_in and self._token_domains[token] & cheap_in:
                weight *= self._settings.known_extra_weight
            total += count * weight
        return total

    def _similarity(self, query: Counter[str], candidate: _Entry) -> float:
        shared = self._w(query & candidate.tokens)
        query_extra = self._w(query - candidate.tokens, cheap_in=candidate.domains)
        candidate_extra = self._w(candidate.tokens - query)
        return 2 * shared / (2 * shared + query_extra + candidate_extra)

    def _specificity(self, entry: _Entry) -> int:
        """Smaller domains are more specific; used to break ties."""
        return min(self._domain_size[slug] for _, slug in entry.titles)

    def _has_selectable(self, entry: _Entry) -> bool:
        return any(slug in self._selectable for _, slug in entry.titles)

    def _ordered_domains(self, entry: _Entry) -> list[str]:
        slugs = dict.fromkeys(slug for _, slug in entry.titles)
        return sorted(slugs, key=lambda s: (s not in self._selectable, self._domain_size[s]))

    def _result(
        self,
        base: TitleMatch,
        method: Method,
        confidence: float,
        best: _Entry,
        others: list[_Entry],
    ) -> TitleMatch:
        domains = self._ordered_domains(best)
        primary = domains[0]
        secondary = [slug for slug in domains[1:] if slug in self._selectable]
        for entry in others:
            secondary.extend(s for s in self._ordered_domains(entry) if s in self._selectable)
        secondary = [slug for slug in dict.fromkeys(secondary) if slug != primary]
        title = next(t for t, slug in best.titles if slug == primary)
        return TitleMatch(
            normalized=base.normalized,
            seniority=base.seniority,
            method=method,
            primary_domain=primary,
            secondary_domains=tuple(secondary[: self._settings.max_secondary_domains]),
            confidence=round(confidence, 3),
            matched_title=title,
        )
