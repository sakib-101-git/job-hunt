"""Schemas for the files in config/. Every model rejects unknown keys, so a typo
in a YAML file fails loudly instead of being ignored."""

from collections import Counter
from collections.abc import Iterable
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Slug = Annotated[str, StringConstraints(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$")]
Ident = Annotated[str, StringConstraints(pattern=r"^[a-z0-9]+(_[a-z0-9]+)*$")]
Seniority = Literal["intern", "entry", "junior", "mid", "senior", "lead_plus"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _duplicates(values: Iterable[str]) -> list[str]:
    return sorted(value for value, count in Counter(values).items() if count > 1)


def _require_unique(what: str, values: Iterable[str]) -> None:
    if dupes := _duplicates(values):
        raise ValueError(f"duplicate {what}: {', '.join(dupes)}")


# --- taxonomy/job_titles.yaml -------------------------------------------------


class TitleGroup(Strict):
    name: NonEmpty
    titles: list[NonEmpty] = Field(min_length=1)


class Domain(Strict):
    id: int
    slug: Slug
    name: NonEmpty
    # A seniority ladder holds career-stage titles: it feeds seniority detection
    # and is never a selectable domain or proof that a job is a CSE role.
    kind: Literal["seniority_ladder"] | None = None
    groups: list[TitleGroup] = Field(min_length=1)

    @property
    def selectable(self) -> bool:
        return self.kind is None

    @property
    def titles(self) -> list[str]:
        return [title for group in self.groups for title in group.titles]


class Addition(Strict):
    domain: Slug
    titles: list[NonEmpty] = Field(min_length=1)


class Taxonomy(Strict):
    version: Literal[1]
    domains: list[Domain] = Field(min_length=1)
    additions: list[Addition] = []
    non_cse_exclusions: list[NonEmpty] = []

    @model_validator(mode="after")
    def _check(self) -> Self:
        _require_unique("domain ids", (str(d.id) for d in self.domains))
        _require_unique("domain slugs", (d.slug for d in self.domains))
        slugs = self.domain_slugs
        if unknown := sorted({a.domain for a in self.additions} - slugs):
            raise ValueError(f"additions use unknown domains: {', '.join(unknown)}")
        return self

    @property
    def domain_slugs(self) -> set[str]:
        return {d.slug for d in self.domains}

    @property
    def selectable_slugs(self) -> set[str]:
        return {d.slug for d in self.domains if d.selectable}

    def titles_by_domain(self) -> dict[str, list[str]]:
        """Every title per domain slug: the directory titles, then the additions."""
        result = {d.slug: d.titles for d in self.domains}
        for addition in self.additions:
            result[addition.domain] = [*result[addition.domain], *addition.titles]
        return result


# --- taxonomy/tracks.yaml -----------------------------------------------------


class TrackDomains(Strict):
    primary: list[Slug] = Field(min_length=1)
    adjacent: list[Slug] = []

    @model_validator(mode="after")
    def _check(self) -> Self:
        if both := sorted(set(self.primary) & set(self.adjacent)):
            raise ValueError(f"domains both primary and adjacent: {', '.join(both)}")
        return self


class Track(Strict):
    id: Ident
    label: NonEmpty
    description: NonEmpty
    domains: TrackDomains
    include_titles: list[NonEmpty] = []


class Tracks(Strict):
    version: Literal[1]
    tracks: list[Track] = Field(min_length=1)

    @model_validator(mode="after")
    def _check(self) -> Self:
        _require_unique("track ids", (t.id for t in self.tracks))
        return self


# --- taxonomy/title_rules.yaml ------------------------------------------------


class SeniorityRules(Strict):
    prefix_or_suffix: dict[NonEmpty, Seniority] = {}
    prefix_only: dict[NonEmpty, Seniority] = {}
    suffix_only: dict[NonEmpty, Seniority] = {}
    level_words: list[NonEmpty] = []
    level_map: dict[int, Seniority] = {}


class TitleRules(Strict):
    version: Literal[1]
    phrases: dict[NonEmpty, NonEmpty] = {}
    abbreviations: dict[NonEmpty, NonEmpty] = {}
    equivalents: dict[NonEmpty, list[NonEmpty]] = {}
    stopwords: list[NonEmpty] = []
    seniority: SeniorityRules
    noise_words: list[NonEmpty] = []
    dotted_suffixes: list[NonEmpty] = []

    @model_validator(mode="after")
    def _check(self) -> Self:
        variants = [v for vs in self.equivalents.values() for v in vs]
        _require_unique("equivalent words", [*self.equivalents, *variants])
        return self


# --- skills.yaml --------------------------------------------------------------


class Skill(Strict):
    id: Ident
    name: NonEmpty
    aliases: list[NonEmpty] = []

    @property
    def spellings(self) -> list[str]:
        return [self.name, *self.aliases]


class SkillCategory(Strict):
    id: Ident
    name: NonEmpty
    skills: list[Skill] = Field(min_length=1)


class Skills(Strict):
    version: Literal[1]
    categories: list[SkillCategory] = Field(min_length=1)

    @model_validator(mode="after")
    def _check(self) -> Self:
        _require_unique("skill category ids", (c.id for c in self.categories))
        _require_unique("skill ids", (s.id for s in self.all_skills))
        _require_unique(
            "skill names or aliases (case-insensitive)",
            (spelling.casefold() for s in self.all_skills for spelling in s.spellings),
        )
        return self

    @property
    def all_skills(self) -> list[Skill]:
        return [skill for category in self.categories for skill in category.skills]


# --- locations.yaml -----------------------------------------------------------


class District(Strict):
    id: Ident
    name: NonEmpty
    aliases: list[NonEmpty] = []
    areas: list[NonEmpty] = []


class Division(Strict):
    id: Ident
    name: NonEmpty
    aliases: list[NonEmpty] = []
    districts: list[District] = Field(min_length=1)


class Country(Strict):
    code: Annotated[str, StringConstraints(pattern=r"^[A-Z]{2}$")]
    name: NonEmpty
    aliases: list[NonEmpty] = []


class Locations(Strict):
    version: Literal[1]
    country: Country
    divisions: list[Division] = Field(min_length=1)
    preferred_city_options: list[Ident] = []

    @model_validator(mode="after")
    def _check(self) -> Self:
        _require_unique("division ids", (d.id for d in self.divisions))
        district_ids = [district.id for d in self.divisions for district in d.districts]
        _require_unique("district ids", district_ids)
        _require_unique("preferred city options", self.preferred_city_options)
        if unknown := sorted(set(self.preferred_city_options) - set(district_ids)):
            raise ValueError(f"preferred_city_options are not districts: {', '.join(unknown)}")
        return self

    @property
    def districts(self) -> list[District]:
        return [district for division in self.divisions for district in division.districts]

    def place_names(self) -> set[str]:
        """Every spelling of the country, divisions, districts and areas, casefolded."""
        names = {self.country.name, *self.country.aliases}
        for division in self.divisions:
            names.update([division.name, *division.aliases])
            for district in division.districts:
                names.update([district.name, *district.aliases, *district.areas])
        return {name.casefold() for name in names}


# --- app.yaml -----------------------------------------------------------------


class TitleMatching(Strict):
    min_similarity: float = Field(gt=0, le=1)
    known_extra_weight: float = Field(ge=0, le=1)
    secondary_margin: float = Field(ge=0, le=1)
    max_secondary_domains: int = Field(ge=0)
    spelling_min_ratio: float = Field(gt=0, le=100)
    spelling_min_length: int = Field(ge=1)


class DatabaseSettings(Strict):
    d1_api_base_url: Annotated[str, StringConstraints(pattern=r"^https://")]
    request_timeout_seconds: float = Field(gt=0)
    max_retries: int = Field(ge=0)
    retry_base_delay_seconds: float = Field(ge=0)
    max_params_per_statement: int = Field(ge=1)
    max_statement_bytes: int = Field(ge=1)


class App(Strict):
    version: Literal[1]
    title_matching: TitleMatching
    database: DatabaseSettings
