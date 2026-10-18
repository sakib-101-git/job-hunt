"""Read a user's CV: download the PDF the bot was sent, extract its text, ask a
model that doesn't train on inputs for the facts matching needs, ground them,
save them on the profile and message the user what was found.

The CV text exists only in memory during this call: it is never stored or
logged. The Telegram file id is cleared once the CV is read (or can't be).
"""

import io
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pypdf import PdfReader
from pypdf.errors import PyPdfError

from jobhunt.config import Config
from jobhunt.config.models import ChainEntry
from jobhunt.db import Database, timestamp
from jobhunt.extract.budget import LlmBudget
from jobhunt.extract.extractor import ModelCaller
from jobhunt.extract.grounding import Grounder, normalize
from jobhunt.extract.llm import LlmClient, Prompt
from jobhunt.extract.schema import strict_schema
from jobhunt.notify.telegram import TelegramClient, TelegramError
from jobhunt.score.render import Locale

log = logging.getLogger(__name__)

CvEducationLevel = Literal["secondary", "diploma", "bachelor", "master", "phd", "unknown"]
Outcome = Literal["parsed", "no_cv", "no_model", "download_failed", "no_text", "model_failed"]

# Contact details a summary must never carry.
_PERSONAL = re.compile(r"@|https?://|www\.|\+?\d[\d\s()-]{7,}\d")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CvEducation(_Strict):
    level: CvEducationLevel
    field: str | None
    evidence: str | None


class CvFacts(_Strict):
    skills: list[str]
    years_experience: float | None = Field(ge=0, le=50)
    years_evidence: str | None
    education: CvEducation
    summary: str


@dataclass(frozen=True)
class CvProfile:
    skills: list[str]  # canonical skill ids first, then grounded skills as written
    years: float | None
    education: CvEducation
    summary: str | None
    failures: tuple[str, ...]


def pdf_text(data: bytes, max_pages: int) -> str:
    """The text of the first `max_pages` pages; empty when the PDF can't be read."""
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            return ""
        pages = [page.extract_text() or "" for page in reader.pages[:max_pages]]
    except (PyPdfError, ValueError, KeyError, TypeError, OSError) as exc:
        log.info("unreadable PDF: %s", type(exc).__name__)
        return ""
    return re.sub(r"[ \t]+", " ", "\n".join(pages)).strip()


def ground_cv(facts: CvFacts, text: str, grounder: Grounder, max_summary: int) -> CvProfile:
    """Keep only what the CV text supports (same rules as job facts)."""
    failures: list[str] = []
    present = set(grounder.skills_in(text))
    lowered = normalize(text)
    known: list[str] = []
    written: list[str] = []
    for skill in facts.skills:
        canonical = grounder.canonical(skill)
        if canonical and canonical in present:
            known.append(canonical)
        elif re.search(rf"(?<!\w){re.escape(normalize(skill))}(?!\w)", lowered):
            written.append(skill.strip())
        else:
            failures.append(f"skills:{skill}")
    years = facts.years_experience
    if years is not None and not grounder.quoted(facts.years_evidence, text):
        failures.append("years_evidence")
        years = None
    education = facts.education
    if education.level != "unknown" and not grounder.quoted(education.evidence, text):
        failures.append("education_evidence")
        education = CvEducation(level="unknown", field=None, evidence=None)
    summary: str | None = facts.summary.strip()[:max_summary] or None
    if summary and _PERSONAL.search(summary):
        failures.append("summary")
        summary = None
    skills = list(dict.fromkeys(known + [s for s in written if s]))
    return CvProfile(skills, years, education, summary, tuple(failures))


_USER = """
SELECT u.chat_id, pr.cv_file_id, COALESCE(pr.skills_json, '[]') AS skills_json
FROM users u LEFT JOIN user_profiles pr ON pr.user_id = u.id
WHERE u.id = ? AND u.status != 'deleted'
"""

_SAVE = """
UPDATE user_profiles SET
    skills_json = ?2, education_json = ?3, summary = ?4, years_experience = ?5,
    cv_file_id = NULL, cv_parsed_at = ?6, profile_version = profile_version + 1,
    updated_at = ?6
WHERE user_id = ?1
"""


class CvParser:
    def __init__(
        self,
        db: Database,
        config: Config,
        telegram: TelegramClient,
        client: LlmClient,
    ) -> None:
        self._db = db
        self._config = config
        self._s = config.llm.cv
        self._telegram = telegram
        self._client = client
        self._prompt = Prompt.load(config.root / self._s.prompt)
        self._schema = strict_schema(CvFacts, [])
        self._grounder = Grounder(config.skills, config.llm.extraction.min_evidence_score)
        self._locale = Locale.load(config.root)
        self._skill_names = {s.id: s.name for s in config.skills.all_skills}

    def _usable(self, entry: ChainEntry) -> bool:
        """Has a key, and its provider's terms rule out training on inputs."""
        provider = self._config.llm.providers[entry.provider]
        return self._client.available(entry) and not provider.trains_on_inputs

    async def parse(self, user_id: int, now: datetime) -> Outcome:
        rows = await self._db.query(_USER, (user_id,))
        if not rows or not rows[0]["cv_file_id"]:
            return "no_cv"  # cancelled, already read, or the user deleted their data
        row = rows[0]
        chat_id = int(str(row["chat_id"]))
        file_id = str(row["cv_file_id"])

        if not any(self._usable(e) for e in self._config.llm.chain):
            return await self._give_up(user_id, chat_id, "no_model", now)
        try:
            data = await self._telegram.download_file(file_id, self._config.app.bot.cv_max_bytes)
        except TelegramError as exc:
            log.warning("CV download failed: %s", exc)
            return await self._give_up(user_id, chat_id, "download_failed", now)
        text = pdf_text(data, self._s.max_pages)[: self._s.max_chars]
        del data
        if len(text) < self._s.min_chars:
            return await self._give_up(user_id, chat_id, "no_text", now)

        budget = LlmBudget(self._config.llm, self._db, now, self._usable)
        await budget.load()
        caller = ModelCaller(self._client, budget, self._config.llm.extraction.tokens_per_char)
        system, user = self._prompt.render(max_summary=str(self._s.max_summary_chars), cv=text)
        result = await caller.call(
            self._prompt,
            system,
            user,
            "cv_facts",
            self._schema,
            CvFacts,
            self._config.llm.extraction.expected_output_tokens,
        )
        if result is None:
            return await self._give_up(user_id, chat_id, "model_failed", now)
        facts, provider, model = result
        profile = ground_cv(facts, text, self._grounder, self._s.max_summary_chars)
        del text
        log.info(
            "CV read by %s/%s: %d skills, %d grounding failures",
            provider,
            model,
            len(profile.skills),
            len(profile.failures),
        )
        existing = json.loads(str(row["skills_json"]))
        skills = list(dict.fromkeys(profile.skills + [s for s in existing if isinstance(s, str)]))
        education = profile.education.model_dump(exclude={"evidence"})
        await self._db.execute(
            _SAVE,
            (
                user_id,
                json.dumps(skills, ensure_ascii=False),
                json.dumps(education | {"prompt_version": self._s.prompt_version}),
                profile.summary,
                profile.years,
                timestamp(now),
            ),
        )
        await self._send(chat_id, self._summary(profile), keyboard=True)
        return "parsed"

    def _summary(self, profile: CvProfile) -> str:
        t = self._locale
        none = t("cv.none")
        skills = ", ".join(self._skill_names.get(s, s) for s in profile.skills)
        years = t("cv.years", years=f"{profile.years:g}") if profile.years is not None else none
        level, field = profile.education.level, profile.education.field
        if level == "unknown":
            education = none
        elif field:
            education = t("cv.education", level=t(f"cv.levels.{level}"), field=field)
        else:
            education = t(f"cv.levels.{level}")
        lines = [
            t("cv.parsed"),
            t("cv.skills", skills=skills or none),
            t("cv.experience", years=years),
            t("cv.degree", education=education),
        ]
        if profile.summary:
            lines.append(t("cv.summary", summary=profile.summary))
        lines.append(t("cv.check"))
        return "\n".join(lines)

    async def _give_up(
        self, user_id: int, chat_id: int, outcome: Outcome, now: datetime
    ) -> Outcome:
        await self._db.execute(
            "UPDATE user_profiles SET cv_file_id = NULL, updated_at = ? WHERE user_id = ?",
            (timestamp(now), user_id),
        )
        await self._send(chat_id, self._locale(f"cv.{outcome}"), keyboard=False)
        return outcome

    async def _send(self, chat_id: int, text: str, *, keyboard: bool) -> None:
        codes = self._config.app.callbacks
        buttons = (
            [
                [
                    {"text": str(self._locale.text("cv.buttons.ok")), "callback_data": codes.cv_ok},
                    {
                        "text": str(self._locale.text("cv.buttons.edit")),
                        "callback_data": codes.cv_edit,
                    },
                ]
            ]
            if keyboard
            else None
        )
        try:
            await self._telegram.send_message(chat_id, text, buttons)
        except TelegramError as exc:
            log.warning("could not message the user about their CV: %s", exc)
