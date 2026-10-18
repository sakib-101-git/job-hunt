"""`jobhunt eval label`: the owner confirms or corrects silver labels, one at a
time, which makes them gold (brief §8.3). Progress is saved after every item,
so it can stop and resume at any point.

For a job: Enter accepts the labels shown; `field=value` changes one (repeat
for several, then Enter); `s` skips; `q` quits. For a pair: Enter accepts,
`0`/`1`/`2` sets the label, `s` skips, `q` quits.
"""

import json
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

from jobhunt.eval.dataset import Dataset, GoldenJob, GoldenPair, JobLabels, save_dataset

Ask = Callable[[str], str]
Say = Callable[[str], None]

EXCERPT = 1200
_LISTS = {"skills", "secondary_domains"}
_NULLS = {"", "none", "null", "-"}


def _parse(name: str, raw: str) -> Any:
    value = raw.strip()
    if name in _LISTS:
        return [part.strip() for part in value.split(",") if part.strip()]
    if value.lower() in _NULLS:
        return None
    if name in {"is_cse_role", "eligible"}:
        if value.lower() not in {"true", "false", "yes", "no", "y", "n"}:
            raise ValueError(f"{name} is true or false")
        return value.lower() in {"true", "yes", "y"}
    if name == "min_years":
        return int(value)
    if name == "deadline":
        return date.fromisoformat(value).isoformat()
    if name == "country":
        return value.upper()
    return value


def edit_labels(labels: JobLabels, edits: list[str]) -> JobLabels:
    """`field=value` edits applied to `labels`, validated as a whole."""
    data = labels.model_dump(mode="json")
    for edit in edits:
        name, sep, raw = edit.partition("=")
        name = name.strip()
        if not sep or name not in data:
            raise ValueError(f"not a field=value edit: {edit!r}")
        data[name] = _parse(name, raw)
    return JobLabels.model_validate(data)


def _show_job(job: GoldenJob, say: Say) -> None:
    listing = job.listing
    say("")
    say(f"== {job.id}  [{job.label_status}]")
    say(f"{listing.title} | {listing.company or '-'} | {listing.location or '-'}")
    if listing.location_restrictions:
        say(f"restrictions: {', '.join(listing.location_restrictions)}")
    say(f"posted {listing.posted_at or '-'}, deadline {listing.deadline or '-'}, {listing.url}")
    say(listing.description[:EXCERPT] + ("…" if len(listing.description) > EXCERPT else ""))
    say("labels: " + json.dumps(job.labels.model_dump(mode="json"), ensure_ascii=False))


def label_jobs(dataset: Dataset, directory: Path, ask: Ask, say: Say) -> int:
    """Walk the silver jobs; returns how many became gold."""
    done = 0
    for index, job in enumerate(dataset.jobs):
        if job.label_status == "gold":
            continue
        _show_job(job, say)
        edits: list[str] = []
        while True:
            answer = ask("Enter=accept, field=value, s=skip, q=quit > ").strip()
            if answer == "q":
                return done
            if answer == "s":
                break
            if answer:
                edits.append(answer)
                continue
            try:
                labels = edit_labels(job.labels, edits)
            except ValueError as exc:
                say(f"not saved: {exc}")
                edits = []
                continue
            dataset.jobs[index] = job.model_copy(update={"labels": labels, "label_status": "gold"})
            save_dataset(directory, dataset)
            done += 1
            break
    return done


def label_pairs(dataset: Dataset, directory: Path, ask: Ask, say: Say) -> int:
    done = 0
    profiles = {p.id: p for p in dataset.profiles}
    for index, pair in enumerate(dataset.pairs):
        if pair.label_status == "gold":
            continue
        profile, job = profiles[pair.profile], dataset.job(pair.job)
        say("")
        say(f"== {profile.id}: {profile.description}")
        say(f"   job {job.id}: {job.listing.title} | {job.listing.company or '-'}")
        say(f"   job labels: {json.dumps(job.labels.model_dump(mode='json'))}")
        say(f"   pair label: {pair.label} (0 not relevant, 1 maybe, 2 apply)")
        answer = ask("Enter=accept, 0/1/2, s=skip, q=quit > ").strip()
        if answer == "q":
            return done
        if answer == "s":
            continue
        if answer and answer not in {"0", "1", "2"}:
            say("not saved: answer 0, 1 or 2")
            continue
        label = int(answer) if answer else pair.label
        dataset.pairs[index] = GoldenPair.model_validate(
            pair.model_dump() | {"label": label, "label_status": "gold"}
        )
        save_dataset(directory, dataset)
        done += 1
    return done
