"""Enrich open jobs: classify unknown tech titles, extract facts, re-run gates.

1. Titles the taxonomy doesn't know (CSE gate `title_not_in_taxonomy`) are
   classified in batches. A title the model calls "not a computing role" is
   filtered without spending an extraction call on it.
2. Up to `max_jobs_per_run` open jobs without current facts are extracted,
   newest first. Facts are current when their input hash (prompt version, title,
   company, location, description) is unchanged and an LLM produced them.
3. The gates run again on the extracted facts. Rules-only facts (no model had
   room) are stored for scoring but never decide a gate: those jobs stay as they
   were and are extracted again on a later run.
"""

import json
import logging
from collections import Counter
from dataclasses import replace
from datetime import datetime

from jobhunt.config import Config
from jobhunt.db import Database, Row, timestamp
from jobhunt.extract.budget import LlmBudget
from jobhunt.extract.extractor import Extractor, ModelCaller
from jobhunt.extract.grounding import Grounder
from jobhunt.extract.llm import LlmClient
from jobhunt.extract.rules import MODEL as RULES_MODEL
from jobhunt.extract.rules import PROVIDER as RULES
from jobhunt.extract.rules import RulesExtractor
from jobhunt.extract.schema import JobFacts
from jobhunt.extract.titles import TitleClassifier
from jobhunt.gates.location import LocationGate
from jobhunt.jobs.gating import GateItem, JobGates, outcome_statements
from jobhunt.jobs.rows import listing_from_row
from jobhunt.taxonomy.matcher import TitleMatcher

log = logging.getLogger(__name__)

_OPEN_JOBS = """
SELECT j.*, f.input_hash AS facts_hash, f.provider AS facts_provider
FROM jobs j LEFT JOIN job_facts f ON f.job_id = j.id
WHERE j.status = 'open'
ORDER BY j.first_seen_at DESC, j.id DESC
"""

_STORE_FACTS = """
INSERT INTO job_facts (job_id, facts_json, provider, model, prompt_version, input_hash,
                       grounding_ok, grounding_failures_json, created_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (job_id) DO UPDATE SET
    facts_json = excluded.facts_json, provider = excluded.provider, model = excluded.model,
    prompt_version = excluded.prompt_version, input_hash = excluded.input_hash,
    grounding_ok = excluded.grounding_ok,
    grounding_failures_json = excluded.grounding_failures_json,
    created_at = excluded.created_at
"""


async def enrich(
    db: Database, config: Config, client: LlmClient, now: datetime, limit: int | None = None
) -> Counter[str]:
    counts: Counter[str] = Counter()
    matcher = TitleMatcher(config)
    gates = JobGates(config, matcher)
    location_gate = LocationGate(config)
    budget = LlmBudget(config.llm, db, now, client.available)
    await budget.load()
    caller = ModelCaller(client, budget, config.llm.extraction.tokens_per_char)
    grounder = Grounder(config.skills, config.llm.extraction.min_evidence_score)
    extractor = Extractor(config, caller, grounder)
    rules = RulesExtractor(config, grounder)
    classifier = TitleClassifier(config, caller, db)

    rows = await db.query(_OPEN_JOBS)
    todo = [row for row in rows if _needs_extraction(row, extractor)]
    todo = todo[: config.llm.extraction.max_jobs_per_run if limit is None else limit]

    # 1. Classify unknown tech titles in batches.
    unknown = {
        int(str(row["id"])): matcher.match(str(row["title"])).normalized
        for row in todo
        if _gate(row, "cse").get("reason") == "title_not_in_taxonomy"
    }
    classes = await classifier.classify(list(unknown.values()), now)
    if classified := sum(1 for title in unknown.values() if title in classes):
        counts["titles classified"] += classified

    # 2 and 3. Extract facts and run the gates again.
    for row in todo:
        job_id = int(str(row["id"]))
        listing = listing_from_row(row)
        item = GateItem(job_id, listing, _parse(row["first_seen_at"]), _gate_seeded(row))
        title_class = classes.get(unknown[job_id]) if job_id in unknown else None
        if job_id in unknown and unknown[job_id] in classes and title_class is None:
            outcome = gates.evaluate(item, now)
            filtered = replace(
                outcome, status="filtered", filter_reason="cse:not_cse_per_classifier"
            )
            await db.batch(outcome_statements(job_id, filtered))
            counts["filtered cse:not_cse_per_classifier"] += 1
            continue

        text = listing.description
        input_hash = extractor.input_hash(listing, text)
        extraction = await extractor.extract(listing, text)
        if extraction is None:
            match = matcher.match(listing.title)
            facts = rules.facts(listing, text, match, location_gate(listing))
            await _store(db, job_id, facts, (RULES, RULES_MODEL), extractor, input_hash, (), now)
            counts["facts by rules (no model had room)"] += 1
            continue
        grounded = extraction.grounded
        source = (extraction.provider, extraction.model)
        await _store(
            db, job_id, grounded.facts, source, extractor, input_hash, grounded.failures, now
        )
        counts[f"facts by {extraction.provider}/{extraction.model}"] += 1
        outcome = gates.evaluate(item, now, grounded.facts, title_class)
        await db.batch(outcome_statements(job_id, outcome, flags=grounded.flags))
        counts[f"after facts: {outcome.status} {outcome.filter_reason or ''}".strip()] += 1
    return counts


def _needs_extraction(row: Row, extractor: Extractor) -> bool:
    if row["facts_hash"] is None or row["facts_provider"] == RULES:
        return True
    return row["facts_hash"] != extractor.input_hash(listing_from_row(row), str(row["jd_text"]))


def _gate(row: Row, name: str) -> dict[str, object]:
    gates = json.loads(str(row["gates_json"] or "{}"))
    value = gates.get(name)
    return value if isinstance(value, dict) else {}


def _gate_seeded(row: Row) -> bool:
    return bool(json.loads(str(row["gates_json"] or "{}")).get("seeded", False))


def _parse(value: object) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


async def _store(
    db: Database,
    job_id: int,
    facts: JobFacts,
    source: tuple[str, str],
    extractor: Extractor,
    input_hash: str,
    failures: tuple[str, ...],
    now: datetime,
) -> None:
    provider, model = source
    await db.execute(
        _STORE_FACTS,
        (
            job_id,
            facts.model_dump_json(),
            provider,
            model,
            extractor.prompt_version,
            input_hash,
            int(not failures),
            json.dumps(failures),
            timestamp(now),
        ),
    )
