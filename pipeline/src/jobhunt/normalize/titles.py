"""Job-title normalization, driven by config/taxonomy/title_rules.yaml.

Taxonomy titles and listing titles go through the same steps, so both sides end
up in one canonical form:

1. NFKC + casefold, then split into segments at brackets and separators
   (" - ", "|", ",", ...). Segments made only of noise words ("Remote",
   "Full-time"), place names ("Dhaka") or job codes are dropped.
2. Tokenize, keeping "C++", "C#", "Node.js" and ".NET" intact.
3. Join split spellings ("front end" -> "frontend"), expand abbreviations
   ("engr" -> "engineer"), fold equivalents ("engineer" -> "developer") and drop
   stopwords.
4. Strip seniority words from the start and end into a separate signal.
"""

import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from jobhunt.config.models import Seniority, TitleRules

SENIORITY_RANK: Mapping[Seniority, int] = {
    "intern": 0,
    "entry": 1,
    "junior": 2,
    "mid": 3,
    "senior": 4,
    "lead_plus": 5,
}

SEGMENT_SPLIT = re.compile(r"[()\[\]{}|,;:@–—]|\s-|-\s")  # noqa: RUF001 (en and em dashes are separators)
_APOSTROPHES = re.compile(r"['’`]")  # noqa: RUF001 (curly apostrophe is intended)
_TOKEN = re.compile(r"[\w+#.]+")
_JOB_CODE = re.compile(r"#?[a-z]{0,6}\d{3,}\w*")

Phrase = tuple[str, ...]


@dataclass(frozen=True)
class NormalizedTitle:
    tokens: tuple[str, ...]
    seniority: Seniority | None

    @property
    def text(self) -> str:
        return " ".join(self.tokens)

    @property
    def key(self) -> str:
        """Order-insensitive form: "Executive - IT" and "IT Executive" share a key."""
        return " ".join(sorted(self.tokens))


class TitleNormalizer:
    def __init__(self, rules: TitleRules, place_names: Iterable[str] = ()) -> None:
        suffixes = "|".join(re.escape(s.casefold()) for s in rules.dotted_suffixes)
        self._stray_dot = re.compile(rf"\.(?!(?:{suffixes})\b)") if suffixes else re.compile(r"\.")

        self._noise: set[Phrase] = {self._words(w) for w in [*rules.noise_words, *place_names]}
        self._noise.discard(())
        self._phrases = self._phrase_map(rules.phrases)
        self._abbreviations = {
            self._single(key): self._words(value) for key, value in rules.abbreviations.items()
        }
        self._equivalents = {
            self._single(variant): self._single(canonical)
            for canonical, variants in rules.equivalents.items()
            for variant in variants
        }
        self._stopwords = {self._single(word) for word in rules.stopwords}

        seniority = rules.seniority
        self._prefixes = self._phrase_map({**seniority.prefix_or_suffix, **seniority.prefix_only})
        self._suffixes = self._phrase_map({**seniority.prefix_or_suffix, **seniority.suffix_only})
        self._level_words = {self._single(word) for word in seniority.level_words}
        self._level_map = seniority.level_map

    # --- public ---------------------------------------------------------------

    def normalize(self, raw: str) -> NormalizedTitle:
        text = unicodedata.normalize("NFKC", raw).casefold()
        segments = [self._words(part) for part in SEGMENT_SPLIT.split(text)]
        segments = [segment for segment in segments if segment]
        kept = [segment for segment in segments if not self._is_noise(segment)] or segments
        tokens = [token for segment in kept for token in segment]

        tokens = self._replace_phrases(tokens)
        tokens = self._expand_abbreviations(tokens)
        tokens = self._replace_phrases(tokens)
        tokens = [self.fold(token) for token in tokens]
        tokens = [token for token in tokens if token not in self._stopwords] or tokens
        tokens, seniority = self._strip_seniority(tokens)
        return NormalizedTitle(tuple(tokens), seniority)

    def fold(self, token: str) -> str:
        """The canonical form of one already-normalized token ("engineer" -> "developer")."""
        return self._equivalents.get(token, token)

    @property
    def variant_words(self) -> set[str]:
        """Words that `fold` rewrites; typo repair needs them as targets too."""
        return set(self._equivalents)

    # --- helpers --------------------------------------------------------------

    def _words(self, text: str) -> Phrase:
        text = unicodedata.normalize("NFKC", text).casefold().replace("&", " and ")
        text = self._stray_dot.sub(" ", _APOSTROPHES.sub("", text))
        return tuple(_TOKEN.findall(text.replace("_", " ")))

    def _single(self, word: str) -> str:
        words = self._words(word)
        if len(words) != 1:
            raise ValueError(f"expected one word, got {word!r}")
        return words[0]

    def _phrase_map[V](self, mapping: Mapping[str, V]) -> dict[Phrase, V]:
        # Longest phrases first, so "entry level" wins over "level".
        pairs = sorted(((self._words(k), v) for k, v in mapping.items()), key=lambda p: -len(p[0]))
        return dict(pairs)

    def _is_noise(self, segment: Phrase) -> bool:
        i = 0
        while i < len(segment):
            if _JOB_CODE.fullmatch(segment[i]):
                i += 1
                continue
            for phrase in self._noise:
                if segment[i : i + len(phrase)] == phrase:
                    i += len(phrase)
                    break
            else:
                return False
        return True

    def _expand_abbreviations(self, tokens: Sequence[str]) -> list[str]:
        # An abbreviation next to its own spelled-out form, as in
        # "Software Development Engineer (SDE)", is dropped instead of doubled.
        result: list[str] = []
        for i, token in enumerate(tokens):
            expansion = self._abbreviations.get(token)
            if expansion is None:
                result.append(token)
            elif not set(expansion) <= {*tokens[:i], *tokens[i + 1 :]}:
                result.extend(expansion)
        return result

    def _replace_phrases(self, tokens: Sequence[str]) -> list[str]:
        result: list[str] = []
        i = 0
        while i < len(tokens):
            for phrase, replacement in self._phrases.items():
                if tuple(tokens[i : i + len(phrase)]) == phrase:
                    result.extend(self._words(replacement))
                    i += len(phrase)
                    break
            else:
                result.append(tokens[i])
                i += 1
        return result

    def _level(self, window: Sequence[str]) -> Seniority | None:
        """Seniority for a whole "level 2" / "l2" window, else None."""
        for word in self._level_words:
            if len(window) == 1 and window[0].startswith(word):
                number = window[0][len(word) :]
            elif len(window) == 2 and window[0] == word:
                number = window[1]
            else:
                continue
            if number.isdigit() and int(number) in self._level_map:
                return self._level_map[int(number)]
        return None

    def _strip_seniority(self, tokens: list[str]) -> tuple[list[str], Seniority | None]:
        found: list[Seniority] = []
        changed = True
        while changed:
            changed = False
            for phrase, level in self._prefixes.items():
                if len(tokens) > len(phrase) and tuple(tokens[: len(phrase)]) == phrase:
                    tokens, changed = tokens[len(phrase) :], True
                    found.append(level)
                    break
            for phrase, level in self._suffixes.items():
                if len(tokens) > len(phrase) and tuple(tokens[-len(phrase) :]) == phrase:
                    tokens, changed = tokens[: -len(phrase)], True
                    found.append(level)
                    break
            for length in (2, 1):
                if len(tokens) <= length:
                    continue
                if marker := self._level(tokens[:length]):
                    tokens, changed = tokens[length:], True
                    found.append(marker)
                elif marker := self._level(tokens[-length:]):
                    tokens, changed = tokens[:-length], True
                    found.append(marker)
        if not found:
            return tokens, None
        return tokens, max(found, key=lambda level: SENIORITY_RANK[level])
