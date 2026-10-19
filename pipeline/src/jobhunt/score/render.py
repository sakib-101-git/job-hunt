"""The job alert (brief §7.3) and the "Why this score?" view, as Telegram HTML.

Every word comes from locales/<locale>.json; the numbers come straight from the
stored breakdown, never recomputed. Values are escaped (`& < >`, all Telegram's
HTML mode needs); the locale's own markup is kept.
"""

import html
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from jobhunt.config import Config
from jobhunt.score.model import JobView, UserProfile
from jobhunt.score.scorer import ScoreResult

TELEGRAM_LIMIT = 4096


class Locale:
    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    @classmethod
    def load(cls, root: Path, name: str = "en") -> "Locale":
        return cls(json.loads((root / "locales" / f"{name}.json").read_text(encoding="utf-8")))

    def text(self, key: str) -> Any:
        """The value at a dotted key. Under "component" the rest is one key,
        since component rules contain dots themselves ("role.primary")."""
        section, _, rest = key.partition(".")
        if section == "component" and rest:
            return self._data["component"][rest]
        node: Any = self._data
        for part in key.split("."):
            node = node[part]
        return node

    def __call__(self, key: str, **values: object) -> "Safe":
        return Safe(str(self.text(key)).format(**{k: _escape(v) for k, v in values.items()}))


class Safe(str):
    """Text already rendered from the locale, with its values escaped."""

    __slots__ = ()


def _escape(value: object) -> str:
    return value if isinstance(value, Safe) else html.escape(str(value), quote=False)


@dataclass(frozen=True)
class Renderer:
    locale: Locale
    domain_names: dict[str, str]
    track_labels: dict[str, str]
    attribution: dict[str, str]
    city_names: dict[str, str]
    skill_names: dict[str, str]
    zone: ZoneInfo
    full_mark: float

    @classmethod
    def from_config(cls, config: Config, locale: str = "en") -> "Renderer":
        return cls(
            locale=Locale.load(config.root, locale),
            domain_names={d.slug: d.name for d in config.taxonomy.domains},
            track_labels={t.id: t.label for t in config.tracks.tracks},
            attribution={name: s.attribution for name, s in config.sources.sources.items()},
            city_names={d.id: d.name for d in config.locations.districts},
            skill_names={s.id: s.name for s in config.skills.all_skills},
            zone=ZoneInfo(config.app.timezone),
            full_mark=config.scoring.marks.full,
        )

    # --- alert ----------------------------------------------------------------

    def alert(self, job: JobView, result: ScoreResult, user: UserProfile, now: datetime) -> str:
        t = self.locale
        sep = t.text("alert.separator")
        facts = job.facts
        header = [t("alert.title", title=job.title)]
        line = []
        if job.company:
            line.append(t("alert.company", company=job.company))
        if place := job.location_raw or (job.city and self.city_names.get(job.city)):
            mode = t.text("work_mode")[facts.work_mode]
            key = "alert.location_with_mode" if mode else "alert.location"
            line.append(t(key, location=place, mode=mode))
        if employment := t.text("employment")[facts.employment_type]:
            line.append(t("alert.employment", employment=employment))
        if line:
            header.append(sep.join(line))
        dates = []
        if job.posted_at:
            dates.append(t("alert.posted", age=self._age(job.posted_at, now)))
        if job.deadline:
            dates.append(t("alert.deadline", date=self._date(job.deadline)))
        if dates:
            header.append(sep.join(dates))

        label = t.text("labels")[result.label]
        body: list[str] = [t("alert.score", score=result.score, label=label)]
        body += [self._component_line(c.name, c.points, c.max, c.rule, c.detail, job, user)
                 for c in result.components]  # fmt: skip

        notes: list[str] = [t("alert.warning", text=w) for w in self._warnings(job)]
        if job.quick:
            notes.insert(0, _escape(t.text("alert.quick")))
        if facts.summary:
            notes.append(t("alert.summary", text=facts.summary))
        notes.append(t("alert.via", source=self.attribution.get(job.source, job.source)))
        message = "\n\n".join("\n".join(part) for part in (header, body, notes) if part)
        return message if len(message) <= TELEGRAM_LIMIT else message[: TELEGRAM_LIMIT - 1] + "…"

    def _component_line(
        self,
        name: str,
        points: Decimal,
        maximum: Decimal,
        rule: str,
        detail: dict[str, Any],
        job: JobView,
        user: UserProfile,
    ) -> str:
        t = self.locale
        mark = (
            "full"
            if points >= maximum * Decimal(str(self.full_mark))
            else ("partial" if points > 0 else "none")
        )
        text = self._describe(rule, detail, job, user)
        points_text = t("alert.points", points=_num(points), max=_num(maximum))
        return f"{t.text('marks')[mark]} {text} {points_text}"

    def _describe(self, rule: str, detail: dict[str, Any], job: JobView, user: UserProfile) -> str:
        t = self.locale
        key = f"component.{rule}"
        if rule.startswith("role."):
            domain = self.domain_names.get(detail.get("job_domain") or "", "")
            track = self.track_labels.get(detail.get("track", ""), "")
            return t(key, domain=domain, track=track)
        if rule == "skills.coverage":
            parts: list[str] = []
            if detail["matched"]:
                matched = self._skills(detail["matched"])
                parts.append(t("component.skills.matched_mark", skills=matched))
            else:
                parts.append(_escape(t.text("component.skills.none_matched")))
            if detail["missing"]:
                parts.append(t("component.skills.missing", skills=self._skills(detail["missing"])))
            if few := detail.get("few_listed"):
                parts.append(t("component.skills.few_listed", count=few))
            return t(key, matched=Safe(str(t.text("alert.separator")).join(parts)))
        if rule.startswith("experience."):
            return t(key, asks=self._years(detail), level=t.text("levels")[user.level])
        if rule.startswith("education."):
            fields = (
                ", ".join(detail["fields"]).upper() or t.text("component")["education.not_required"]
            )
            return t(key, fields=fields)
        if rule == "preference.sum":
            return t(key, details=Safe(self._preferences(detail, job)))
        return t(key)

    def _preferences(self, detail: dict[str, Any], job: JobView) -> str:
        t = self.locale
        parts: list[str] = []
        city = self.city_names.get(job.city or "")
        if "city" in detail["hits"]:
            parts.append(t("component.preference.city", city=city))
        elif city and detail["preferred_cities"]:
            preferred = ", ".join(self.city_names.get(c, c) for c in detail["preferred_cities"])
            parts.append(t("component.preference.other_city", city=city, preferred=preferred))
        if "work_mode" in detail["hits"]:
            # A hit with no stated mode is an on-site job in Bangladesh for a user
            # who chose Bangladesh.
            if mode := t.text("work_mode")[job.facts.work_mode]:
                parts.append(t("component.preference.work_mode", mode=mode))
            else:
                parts.append(_escape(t.text("component.preference.in_bangladesh")))
        if "salary" in detail["hits"]:
            parts.append(_escape(t.text("component.preference.salary")))
        separator = str(t.text("alert.separator"))
        return separator.join(parts) or _escape(t.text("component.preference.none"))

    def _skills(self, skills: list[str]) -> str:
        return ", ".join(self.skill_names.get(skill, skill) for skill in skills)

    def _years(self, detail: dict[str, Any]) -> str:
        t = self.locale
        low, high = detail.get("min_years"), detail.get("max_years")
        if low is None:
            return ""
        if high is not None and high != low:
            return t("component.experience.years_range", low=low, high=high)
        if high == low:
            return t("component.experience.years_exact", years=low)
        return t("component.experience.years_min", years=low)

    def _warnings(self, job: JobView) -> list[str]:
        warnings: list[str] = [self.locale("alert.timezone", text=w) for w in job.warnings]
        if job.facts.timezone_requirement:
            warnings.append(self.locale("alert.timezone", text=job.facts.timezone_requirement))
        warnings += job.facts.language_requirements
        return list(dict.fromkeys(warnings))

    def _age(self, posted: datetime, now: datetime) -> str:
        days = (now.astimezone(self.zone).date() - posted.astimezone(self.zone).date()).days
        if days <= 0:
            return str(self.locale.text("alert.age_today"))
        if days == 1:
            return str(self.locale.text("alert.age_yesterday"))
        return self.locale("alert.age_days", days=days)

    def _date(self, value: Any) -> str:
        return f"{value.day} {self.locale.text('months')[value.month - 1]}"

    # --- why ------------------------------------------------------------------

    def why(self, job: JobView, result: ScoreResult, user: UserProfile) -> str:
        t = self.locale
        lines = [t("why.title", score=result.score, title=job.title), t.text("why.intro"), ""]
        names = t.text("why.names")
        for c in result.components:
            text = self._describe(c.rule, c.detail, job, user)
            lines.append(
                t(
                    "why.component",
                    name=names[c.name],
                    points=_num(c.points),
                    max=_num(c.max),
                    line=text,
                )
            )
            if quote := c.detail.get("evidence"):
                lines.append(t("why.quote", text=" ".join(str(quote).split())))
        if job.location_evidence:
            lines.append(t("why.location", text=job.location_evidence))
        passed = ", ".join(g.gate for g in result.gates if g.status == "pass")
        lines += ["", t("why.gates", gates=passed), t("why.version", version=result.scorer_version)]
        return "\n".join(lines)


def _num(value: Decimal) -> str:
    """2.50 -> 2.5, 3.00 -> 3: as short as the value allows."""
    return format(value.normalize(), "f")
