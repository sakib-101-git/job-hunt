"""Batched LLM title classification (brief §6.3, step 2), for tech titles the
taxonomy doesn't know. Up to `batch_size` titles per request; the answer is
restricted to the domain slugs or "none"; results are cached per normalized
title in `title_classes`, so each title costs one call ever."""

from collections.abc import Sequence
from datetime import datetime

from jobhunt.config import Config
from jobhunt.db import Database, Statement, timestamp
from jobhunt.extract.extractor import ModelCaller
from jobhunt.extract.llm import Prompt
from jobhunt.extract.schema import TitleLabels, strict_schema

NONE = "none"
# Titles per cache lookup: D1 allows 100 bound parameters per statement.
_LOOKUP_CHUNK = 90


class TitleClassifier:
    def __init__(self, config: Config, caller: ModelCaller, db: Database) -> None:
        settings = config.llm.title_classification
        self._settings = settings
        self._prompt = Prompt.load(config.root / settings.prompt)
        self._slugs = sorted(config.taxonomy.selectable_slugs)
        self._schema = strict_schema(TitleLabels, [*self._slugs, NONE])
        self._max_out = config.llm.extraction.expected_output_tokens
        self._caller = caller
        self._db = db

    async def classify(self, titles: Sequence[str], now: datetime) -> dict[str, str | None]:
        """Slug (or None for "not a computing role") per title; titles a model
        could not be reached for are left out."""
        wanted = list(dict.fromkeys(titles))
        result = await self._cached(wanted)
        todo = [title for title in wanted if title not in result]
        for start in range(0, len(todo), self._settings.batch_size):
            batch = todo[start : start + self._settings.batch_size]
            labels = await self._ask(batch)
            if labels is None:
                break  # no model has room; try again next run
            result.update(labels)
            await self._store(labels, now)
        return result

    async def _cached(self, titles: list[str]) -> dict[str, str | None]:
        found: dict[str, str | None] = {}
        for start in range(0, len(titles), _LOOKUP_CHUNK):
            chunk = titles[start : start + _LOOKUP_CHUNK]
            marks = ", ".join("?" for _ in chunk)
            rows = await self._db.query(
                f"SELECT title, domain_slug FROM title_classes WHERE prompt_version = ?"  # noqa: S608
                f" AND title IN ({marks})",
                (self._settings.prompt_version, *chunk),
            )
            for row in rows:
                found[str(row["title"])] = str(row["domain_slug"]) if row["domain_slug"] else None
        return found

    async def _ask(self, batch: list[str]) -> dict[str, str | None] | None:
        system, user = self._prompt.render(
            domains=", ".join(self._slugs),
            titles="\n".join(f"{i}. {title}" for i, title in enumerate(batch, start=1)),
        )
        answer = await self._caller.call(
            self._prompt, system, user, "title_labels", self._schema, TitleLabels, self._max_out
        )
        if answer is None:
            return None
        labels = {label.number: label.slug for label in answer[0].labels}
        result: dict[str, str | None] = {}
        for i, title in enumerate(batch, start=1):
            slug = labels.get(i)
            if slug is not None:
                result[title] = slug if slug in self._slugs else None
        return result

    async def _store(self, labels: dict[str, str | None], now: datetime) -> None:
        await self._db.batch(
            [
                Statement(
                    "INSERT INTO title_classes (title, prompt_version, domain_slug, created_at)"
                    " VALUES (?, ?, ?, ?) ON CONFLICT (title, prompt_version) DO UPDATE SET"
                    " domain_slug = excluded.domain_slug",
                    (title, self._settings.prompt_version, slug, timestamp(now)),
                )
                for title, slug in labels.items()
            ]
        )
