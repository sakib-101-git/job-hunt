"""Extract facts from a posting: once per job, user-independent, cached.

The budget picks a model; the answer must match the strict schema and pass
pydantic validation (one retry with the validation error); then the grounding
validator drops anything the posting doesn't support. When no model has room,
`extract` returns None and the caller uses the rules-only facts.
"""

import hashlib
import logging
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from jobhunt.config import Config
from jobhunt.extract.budget import LlmBudget
from jobhunt.extract.grounding import Grounded, Grounder
from jobhunt.extract.llm import LlmClient, LlmError, Prompt, RateLimitedError
from jobhunt.extract.schema import JobFacts, strict_schema
from jobhunt.normalize.text import truncate
from jobhunt.sources.models import RawListing

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Extraction:
    grounded: Grounded
    provider: str
    model: str


class ModelCaller:
    """Shared by fact extraction and title classification: walk the chain, keep
    the budget, validate the answer, retry once on a schema miss."""

    def __init__(self, client: LlmClient, budget: LlmBudget, tokens_per_char: float) -> None:
        self._client = client
        self._budget = budget
        self._tokens_per_char = tokens_per_char

    async def call[M: BaseModel](
        self,
        prompt: Prompt,
        system: str,
        user: str,
        name: str,
        schema: dict[str, Any],
        model: type[M],
        max_output_tokens: int,
    ) -> tuple[M, str, str] | None:
        estimate = int((len(system) + len(user)) * self._tokens_per_char) + max_output_tokens
        skip: set[str] = set()
        while entry := await self._budget.acquire(estimate, skip):
            message = user
            for attempt in range(2):
                try:
                    answer = await self._client.complete_json(entry, system, message, name, schema)
                except RateLimitedError:
                    self._budget.exhaust(entry)
                    break
                except LlmError as exc:
                    log.warning("%s", exc)
                    skip.add(entry.key)
                    break
                await self._budget.record(entry, answer.tokens_in, answer.tokens_out)
                try:
                    return model.model_validate(answer.data), entry.provider, entry.model
                except ValidationError as exc:
                    if attempt == 1:
                        skip.add(entry.key)
                        break
                    message = f"{user}\n\n{prompt.retry_note(str(exc)[:1000])}"
        return None


class Extractor:
    def __init__(self, config: Config, caller: ModelCaller, grounder: Grounder) -> None:
        settings = config.llm.extraction
        self._settings = settings
        self._max_out = config.llm.request.max_output_tokens
        self._llm_chars = config.app.normalization.jd_max_chars_llm
        self._prompt = Prompt.load(config.root / settings.prompt)
        self._slugs = sorted(config.taxonomy.selectable_slugs)
        self._schema = strict_schema(JobFacts, self._slugs)
        self._caller = caller
        self._grounder = grounder

    @property
    def prompt_version(self) -> str:
        return self._settings.prompt_version

    def input_hash(self, listing: RawListing, text: str) -> str:
        parts = [self.prompt_version, listing.title, listing.company or "", listing.location or ""]
        parts.append(truncate(text, self._llm_chars))
        return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()

    async def extract(self, listing: RawListing, text: str) -> Extraction | None:
        system, user = self._prompt.render(
            domains=", ".join(self._slugs),
            title=listing.title,
            company=listing.company or "unknown",
            location=listing.location or ", ".join(listing.location_restrictions) or "unknown",
            posting=truncate(text, self._llm_chars),
        )
        result = await self._caller.call(
            self._prompt, system, user, "job_facts", self._schema, JobFacts, self._max_out
        )
        if result is None:
            return None
        facts, provider, model = result
        if facts.primary_domain is not None and facts.primary_domain not in self._slugs:
            facts = facts.model_copy(update={"primary_domain": None})
        grounded = self._grounder.ground(facts, text, listing.deadline)
        return Extraction(grounded, provider, model)
