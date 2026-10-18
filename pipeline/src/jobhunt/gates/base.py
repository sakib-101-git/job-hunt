"""Gate results and the whole-word phrase finder the gates share. Gates are pure
functions: no I/O, so every rule is testable in isolation."""

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Literal

GateStatus = Literal["pass", "fail", "needs_facts"]


@dataclass(frozen=True)
class GateResult:
    """`needs_facts`: the listing alone can't decide; the job goes to LLM
    extraction and the gate is evaluated again on the extracted facts."""

    gate: str
    status: GateStatus
    reason: str
    evidence: str | None = None
    warnings: tuple[str, ...] = field(default=())

    def as_dict(self) -> dict[str, object]:
        data: dict[str, object] = {"status": self.status, "reason": self.reason}
        if self.evidence:
            data["evidence"] = self.evidence
        if self.warnings:
            data["warnings"] = list(self.warnings)
        return data


class PhraseFinder:
    """Finds any of a set of phrases as whole words, longest first.

    Phrases of up to three capital letters ("US", "EU") match only in capitals;
    everything else ignores case.
    """

    def __init__(self, phrases: Iterable[str]) -> None:
        exact: list[str] = []
        folded: list[str] = []
        for phrase in sorted({p.strip() for p in phrases if p.strip()}, key=len, reverse=True):
            bare = phrase.replace(".", "")
            (exact if bare.isupper() and len(bare) <= 3 else folded).append(re.escape(phrase))
        self._exact = _compile(exact, 0)
        self._folded = _compile(folded, re.IGNORECASE)

    def find(self, text: str) -> str | None:
        """The first phrase found, as it appears in `text`."""
        hits = [m for p in (self._exact, self._folded) if p and (m := p.search(text))]
        return min(hits, key=lambda m: m.start())[0] if hits else None

    def find_all(self, text: str) -> list[str]:
        """Every phrase found, left to right; of overlapping hits, the longest."""
        hits = [m for p in (self._exact, self._folded) if p for m in p.finditer(text)]
        hits.sort(key=lambda m: (m.start(), -(m.end() - m.start())))
        kept: list[re.Match[str]] = []
        for hit in hits:
            if not kept or hit.start() >= kept[-1].end():
                kept.append(hit)
        return [m[0] for m in kept]


def _compile(alternatives: list[str], flags: int) -> re.Pattern[str] | None:
    if not alternatives:
        return None
    return re.compile(rf"(?<![\w]){'|'.join(alternatives)}(?![\w])", flags)
