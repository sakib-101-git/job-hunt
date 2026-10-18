"""Schemas for the files in config/. Every model rejects unknown keys, so a typo
in a YAML file fails loudly instead of being ignored."""

import re
from collections import Counter
from collections.abc import Iterable
from typing import Annotated, Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

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
    ambiguous: bool = False  # an ordinary word: the rules-only extractor skips it

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

    def place_names_raw(self) -> set[str]:
        """Every spelling of the country, divisions, districts and areas, as written."""
        names = {self.country.name, *self.country.aliases}
        for division in self.divisions:
            names.update([division.name, *division.aliases])
            for district in division.districts:
                names.update([district.name, *district.aliases, *district.areas])
        return names

    def place_names(self) -> set[str]:
        """`place_names_raw`, casefolded."""
        return {name.casefold() for name in self.place_names_raw()}


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


class NormalizationSettings(Strict):
    jd_max_chars_stored: int = Field(ge=100)
    jd_max_chars_llm: int = Field(ge=100)
    tracking_params: list[NonEmpty] = []
    company_suffixes: list[NonEmpty] = []
    store_batch_size: int = Field(ge=1)

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.jd_max_chars_llm > self.jd_max_chars_stored:
            raise ValueError("jd_max_chars_llm cannot exceed jd_max_chars_stored")
        return self


ExperienceLevel = Literal["student_intern", "fresh_grad", "junior", "mid", "senior"]
EXPERIENCE_LEVELS: tuple[ExperienceLevel, ...] = (
    "student_intern",
    "fresh_grad",
    "junior",
    "mid",
    "senior",
)


class FreshnessSettings(Strict):
    max_age_days: int = Field(ge=1)


class LocationPolicy(Strict):
    bangladesh_boards: list[Ident] = []
    accept_unspecified_remote: bool
    accept_region_lists_containing_bd: bool


class SeniorityGate(Strict):
    max_years_gap: dict[ExperienceLevel, float]
    blocked_title_levels: dict[ExperienceLevel, list[Seniority]]
    default_years: dict[ExperienceLevel, float]

    @model_validator(mode="after")
    def _every_level(self) -> Self:
        for name in ("max_years_gap", "blocked_title_levels", "default_years"):
            if missing := sorted(set(EXPERIENCE_LEVELS) - set(getattr(self, name))):
                raise ValueError(f"{name} is missing levels: {', '.join(missing)}")
        return self


class CseGateSettings(Strict):
    tech_signals: list[NonEmpty] = Field(min_length=1)


class App(Strict):
    version: Literal[1]
    title_matching: TitleMatching
    database: DatabaseSettings
    normalization: NormalizationSettings
    timezone: NonEmpty
    freshness: FreshnessSettings
    location_policy: LocationPolicy
    seniority_gate: SeniorityGate
    cse_gate: CseGateSettings
    red_flags: list[NonEmpty] = []

    @model_validator(mode="after")
    def _known_timezone(self) -> Self:
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown timezone {self.timezone!r}") from exc
        return self


# --- geography.yaml -----------------------------------------------------------


class Region(Strict):
    name: NonEmpty
    aliases: list[NonEmpty] = []
    includes_bangladesh: bool


class OtherCountry(Strict):
    name: NonEmpty
    aliases: list[NonEmpty] = []
    cities: list[NonEmpty] = []


class Geography(Strict):
    version: Literal[1]
    worldwide_phrases: list[NonEmpty] = Field(min_length=1)
    remote_words: list[NonEmpty] = Field(min_length=1)
    timezone_markers: list[NonEmpty] = []
    filler_words: list[NonEmpty] = []
    regions: list[Region] = []
    countries: list[OtherCountry] = []

    @model_validator(mode="after")
    def _check(self) -> Self:
        names = [r.name for r in self.regions] + [c.name for c in self.countries]
        _require_unique("region and country names", (n.casefold() for n in names))
        return self


# --- sources.yaml -------------------------------------------------------------

HttpsUrl = Annotated[str, StringConstraints(pattern=r"^https://\S+$")]


class HostOverride(Strict):
    min_delay_seconds: float | None = Field(default=None, ge=0)
    concurrency: int | None = Field(default=None, ge=1)


class HttpSettings(Strict):
    user_agent: NonEmpty
    timeout_seconds: float = Field(gt=0)
    max_retries: int = Field(ge=0)
    backoff_base_seconds: float = Field(ge=0)
    backoff_max_seconds: float = Field(ge=0)
    per_host_concurrency: int = Field(ge=1)
    per_host_min_delay_seconds: float = Field(ge=0)
    host_overrides: dict[NonEmpty, HostOverride] = {}


class HealthSettings(Strict):
    failures_before_alert: int = Field(ge=1)
    empty_runs_before_alert: int = Field(ge=1)


EnvName = Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Z0-9_]*$")]


class SourceBase(Strict):
    enabled: bool
    disabled_reason: NonEmpty | None = None
    min_interval_minutes: int = Field(ge=1)
    attribution: NonEmpty
    # The source is skipped, not failed, until all of these are set.
    required_env: list[EnvName] = []

    @model_validator(mode="after")
    def _reason_when_disabled(self) -> Self:
        if not self.enabled and not self.disabled_reason:
            raise ValueError("a disabled source needs a disabled_reason")
        if self.enabled and self.disabled_reason:
            raise ValueError("an enabled source must not have a disabled_reason")
        return self


class BdjobsSource(SourceBase):
    kind: Literal["bdjobs"]
    search_url: HttpsUrl
    details_url: HttpsUrl
    job_url: Annotated[str, StringConstraints(pattern=r"^https://\S*\{id\}\S*$")]
    headers: dict[NonEmpty, NonEmpty] = {}
    category: NonEmpty
    results_per_page: int = Field(ge=1)
    max_pages: int = Field(ge=1)
    posted_within_days: int = Field(ge=1)


class UrlSource(SourceBase):
    url: HttpsUrl
    params: dict[NonEmpty, NonEmpty] = {}


class RemoteokSource(UrlSource):
    kind: Literal["remoteok"]


class JobicySource(UrlSource):
    kind: Literal["jobicy"]


class WorkingNomadsSource(UrlSource):
    kind: Literal["workingnomads"]


class RemotiveSource(UrlSource):
    kind: Literal["remotive"]


class HimalayasSource(UrlSource):
    kind: Literal["himalayas"]
    max_pages: int = Field(ge=1)
    api_key_env: EnvName | None = None


class RssSource(SourceBase):
    kind: Literal["rss"]
    feeds: list[HttpsUrl] = Field(min_length=1)


AtsName = Literal["greenhouse", "lever", "ashby", "workable", "smartrecruiters"]
TokenUrl = Annotated[str, StringConstraints(pattern=r"^https://\S*\{token\}\S*$")]


class AtsCompany(Strict):
    name: NonEmpty
    ats: AtsName
    token: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9._-]+$")]
    verified: NonEmpty  # what was checked before adding the company


class AtsSource(SourceBase):
    kind: Literal["ats"]
    boards: dict[AtsName, TokenUrl]
    details: dict[AtsName, TokenUrl] = {}
    job_pages: dict[AtsName, TokenUrl] = {}
    companies: list[AtsCompany] = Field(min_length=1)

    @model_validator(mode="after")
    def _check(self) -> Self:
        _require_unique("companies", (f"{c.ats}:{c.token}" for c in self.companies))
        for company in self.companies:
            if company.ats not in self.boards:
                raise ValueError(f"no board URL for {company.ats} ({company.name})")
        return self


def _valid_regex(pattern: str) -> str:
    try:
        re.compile(pattern)
    except re.error as exc:
        raise ValueError(f"invalid regex {pattern!r}: {exc}") from exc
    return pattern


Regex = Annotated[str, StringConstraints(min_length=1), AfterValidator(_valid_regex)]


class HnSource(SourceBase):
    kind: Literal["hn"]
    search_url: HttpsUrl
    comments_url: HttpsUrl
    item_url: Annotated[str, StringConstraints(pattern=r"^https://\S*\{id\}\S*$")]
    thread_author: NonEmpty
    thread_title_prefix: NonEmpty
    require: list[Regex] = []
    require_any: list[Regex] = []
    not_a_title: list[Regex] = []


class ImapSource(SourceBase):
    kind: Literal["imap"]
    sender: NonEmpty
    lookback_days: int = Field(ge=1)
    job_url: Annotated[str, StringConstraints(pattern=r"^https://\S*\{id\}\S*$")]


class DisabledSource(SourceBase):
    """A source with no implementation, kept to record why it is off."""

    kind: Literal["disabled"]

    @model_validator(mode="after")
    def _never_enabled(self) -> Self:
        if self.enabled:
            raise ValueError("kind 'disabled' has no implementation and cannot be enabled")
        return self


SourceSettings = Annotated[
    BdjobsSource
    | RemoteokSource
    | JobicySource
    | WorkingNomadsSource
    | RemotiveSource
    | HimalayasSource
    | RssSource
    | AtsSource
    | HnSource
    | ImapSource
    | DisabledSource,
    Field(discriminator="kind"),
]


class Sources(Strict):
    version: Literal[1]
    http: HttpSettings
    health: HealthSettings
    sources: dict[Ident, SourceSettings] = Field(min_length=1)


# --- llm.yaml -----------------------------------------------------------------


class LlmProvider(Strict):
    base_url: HttpsUrl
    api_key_env: EnvName
    trains_on_inputs: bool


class ChainEntry(Strict):
    provider: Ident
    model: NonEmpty
    rpm: int | None = Field(ge=1)
    tpm: int | None = Field(ge=1)
    rpd: int | None = Field(ge=1)
    tpd: int | None = Field(ge=1)
    params: dict[NonEmpty, str | int | float | bool] = {}

    @property
    def key(self) -> str:
        return f"{self.provider}/{self.model}"


class LlmRequest(Strict):
    timeout_seconds: float = Field(gt=0)
    temperature: float = Field(ge=0, le=2)
    max_output_tokens: int = Field(ge=1)
    daily_safety_margin: float = Field(ge=0, lt=1)
    max_wait_seconds: float = Field(ge=0)


PromptPath = Annotated[str, StringConstraints(pattern=r"^prompts/[\w.-]+\.md$")]


class ExtractionSettings(Strict):
    prompt: PromptPath
    prompt_version: NonEmpty
    max_jobs_per_run: int = Field(ge=0)
    min_evidence_score: float = Field(gt=0, le=100)
    tokens_per_char: float = Field(gt=0)


class TitleClassificationSettings(Strict):
    prompt: PromptPath
    prompt_version: NonEmpty
    batch_size: int = Field(ge=1)


class Llm(Strict):
    version: Literal[1]
    providers: dict[Ident, LlmProvider] = Field(min_length=1)
    chain: list[ChainEntry] = Field(min_length=1)
    request: LlmRequest
    extraction: ExtractionSettings
    title_classification: TitleClassificationSettings

    @model_validator(mode="after")
    def _check(self) -> Self:
        _require_unique("chain entries", (entry.key for entry in self.chain))
        if unknown := sorted({e.provider for e in self.chain} - set(self.providers)):
            raise ValueError(f"chain uses unknown providers: {', '.join(unknown)}")
        return self


# --- scoring.yaml -------------------------------------------------------------

Points = Annotated[float, Field(ge=0, le=10)]


class Weights(Strict):
    role: Points
    skills: Points
    experience: Points
    education: Points
    preference: Points


class RoleScoring(Strict):
    primary: Points
    secondary_or_include: Points
    adjacent: Points


class SkillsScoring(Strict):
    must_weight: int = Field(ge=1)
    nice_weight: int = Field(ge=0)
    none_listed: Points


class ExperienceScoring(Strict):
    met: Points
    gap_points: dict[int, Points]
    unknown: Points
    seniority_mismatch_penalty: Points
    fitting_seniority: dict[ExperienceLevel, list[Seniority]]


class EducationScoring(Strict):
    accepted: Points
    broad: Points
    other_field: Points
    accepted_fields: list[NonEmpty]
    broad_fields: list[NonEmpty]


class PreferenceScoring(Strict):
    city: Points
    work_mode: Points
    salary_disclosed: Points


class ScoreLabel(Strict):
    min: Points
    key: Ident


class Marks(Strict):
    full: float = Field(gt=0, le=1)


class Scoring(Strict):
    version: Literal[1]
    scorer_version: NonEmpty
    weights: Weights
    role: RoleScoring
    skills: SkillsScoring
    experience: ExperienceScoring
    education: EducationScoring
    preference: PreferenceScoring
    labels: list[ScoreLabel] = Field(min_length=1)
    marks: Marks

    @model_validator(mode="after")
    def _check(self) -> Self:
        w = self.weights
        if abs(w.role + w.skills + w.experience + w.education + w.preference - 10) > 1e-9:
            raise ValueError("weights must sum to 10")
        caps = {
            "role.primary": (self.role.primary, w.role),
            "experience.met": (self.experience.met, w.experience),
            "education.accepted": (self.education.accepted, w.education),
        }
        for name, (value, cap) in caps.items():
            if abs(value - cap) > 1e-9:
                raise ValueError(f"{name} must equal its weight ({cap})")
        if self.skills.none_listed > w.skills or self.experience.unknown > w.experience:
            raise ValueError("default points cannot exceed the component weight")
        if missing := sorted(set(EXPERIENCE_LEVELS) - set(self.experience.fitting_seniority)):
            raise ValueError(f"fitting_seniority is missing levels: {', '.join(missing)}")
        mins = [label.min for label in self.labels]
        if mins != sorted(mins, reverse=True) or mins[-1] != 0:
            raise ValueError("labels must go from the highest min down to 0")
        return self
