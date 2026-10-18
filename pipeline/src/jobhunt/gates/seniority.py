"""Seniority gate (brief §6.7), per user: a job is never sent when its title's
seniority is blocked for the user's level, or when it asks for
`max_years_gap` or more years beyond the user's experience. Unknown years or
seniority never block; the scorer accounts for them."""

from jobhunt.config.models import ExperienceLevel, Seniority, SeniorityGate
from jobhunt.gates.base import GateResult

GATE = "seniority"


def seniority_gate(
    settings: SeniorityGate,
    user_level: ExperienceLevel,
    user_years: float | None,
    title_level: Seniority | None,
    min_years: int | None,
) -> GateResult:
    if title_level is not None and title_level in settings.blocked_title_levels[user_level]:
        return GateResult(GATE, "fail", "title_too_senior", title_level)
    years = settings.default_years[user_level] if user_years is None else user_years
    if min_years is not None and min_years - years >= settings.max_years_gap[user_level]:
        return GateResult(
            GATE, "fail", "too_much_experience", f"asks {min_years} years; user has {years:g}"
        )
    return GateResult(GATE, "pass", "within_tolerance")
