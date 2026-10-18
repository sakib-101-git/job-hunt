"""The facts extracted from one posting (brief §6.6), and the strict JSON schema
the LLM must answer with.

Strict structured output (Groq, Gemini) needs every property listed as required,
optional values as nullable types and `additionalProperties: false` on every
object; `strict_schema` turns the pydantic schema into that form and pins the
domain slugs to the taxonomy's.
"""

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SeniorityLevel = Literal["intern", "entry", "junior", "mid", "senior", "lead_plus", "unknown"]
EducationLevel = Literal["diploma", "bachelor", "master", "phd", "any", "unknown"]
EducationField = Literal[
    "cse", "cs", "it", "software", "eee", "any_engineering", "any_science", "any"
]
EmploymentType = Literal["full_time", "part_time", "contract", "internship", "unknown"]
WorkMode = Literal["onsite", "hybrid", "remote", "unknown"]
RemoteEligibility = Literal[
    "worldwide", "bangladesh_included", "region_restricted", "unspecified", "not_remote"
]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Education(_Strict):
    level: EducationLevel
    fields: list[EducationField]
    required: bool


class Salary(_Strict):
    min: float | None
    max: float | None
    currency: str | None
    period: str | None


class JobFacts(_Strict):
    is_cse_role: bool
    is_cse_role_evidence: str | None
    primary_domain: str | None
    secondary_domains: list[str] = Field(max_length=3)
    normalized_title: str
    seniority: SeniorityLevel
    seniority_evidence: str | None
    min_years: int | None = Field(ge=0, le=50)
    max_years: int | None = Field(ge=0, le=50)
    years_evidence: str | None
    education: Education
    education_evidence: str | None
    required_skills: list[str]
    preferred_skills: list[str]
    employment_type: EmploymentType
    work_mode: WorkMode
    country: str | None = Field(pattern=r"^[A-Z]{2}$")
    city: str | None
    remote_eligibility: RemoteEligibility
    remote_eligibility_evidence: str | None
    timezone_requirement: str | None
    salary: Salary | None
    application_deadline: date | None
    deadline_evidence: str | None
    posted_date_in_text: date | None
    language_requirements: list[str]
    red_flags: list[str]
    summary: str = Field(max_length=300)


class TitleLabel(_Strict):
    number: int
    slug: str


class TitleLabels(_Strict):
    labels: list[TitleLabel]


# --- strict JSON schema ---------------------------------------------------------

_DROP = {"title", "default", "format", "maxLength", "minLength", "maxItems", "pattern",
         "minimum", "maximum", "description"}  # fmt: skip


def strict_schema(model: type[BaseModel], slugs: list[str]) -> dict[str, Any]:
    """`model`'s JSON schema in strict form; fields named like domains take `slugs`."""
    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})
    cleaned: dict[str, Any] = _clean(_inline(schema, defs), slugs)
    return cleaned


def _inline(node: Any, defs: dict[str, Any]) -> Any:
    if isinstance(node, dict):
        if "$ref" in node:
            return _inline(defs[node["$ref"].split("/")[-1]], defs)
        return {key: _inline(value, defs) for key, value in node.items()}
    if isinstance(node, list):
        return [_inline(item, defs) for item in node]
    return node


def _clean(node: Any, slugs: list[str], name: str = "") -> Any:
    if isinstance(node, list):
        return [_clean(item, slugs, name) for item in node]
    if not isinstance(node, dict):
        return node
    node = {k: v for k, v in node.items() if k not in _DROP}
    if node.get("type") == "object" and "properties" in node:
        node["properties"] = {
            key: _clean(value, slugs, key) for key, value in node["properties"].items()
        }
        node["required"] = list(node["properties"])
        node["additionalProperties"] = False
        return node
    node = {key: _clean(value, slugs, name) for key, value in node.items()}
    if name in {"primary_domain", "secondary_domains", "slug"} and node.get("type") == "string":
        node["enum"] = slugs
    return node
