"""Load and cross-check every file in config/."""

import os
from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import BaseModel, ValidationError

from jobhunt.config.models import (
    App,
    Geography,
    Llm,
    Locations,
    Scoring,
    Skills,
    Sources,
    Taxonomy,
    TitleRules,
    Tracks,
)

CONFIG_DIR_ENV = "JOBHUNT_CONFIG_DIR"


class ConfigError(Exception):
    """A config file is missing, is not valid YAML, or breaks its schema."""


@dataclass(frozen=True)
class Config:
    root: Path  # the checkout: config/, prompts/ and migrations/ live here
    app: App
    taxonomy: Taxonomy
    tracks: Tracks
    title_rules: TitleRules
    skills: Skills
    locations: Locations
    sources: Sources
    geography: Geography
    llm: Llm
    scoring: Scoring


def find_repo_root(start: Path) -> Path | None:
    """Walk up from `start` to the directory that holds `pipeline/pyproject.toml`."""
    for candidate in (start, *start.parents):
        if (candidate / "pipeline" / "pyproject.toml").is_file():
            return candidate
    return None


def default_config_dir() -> Path:
    """`$JOBHUNT_CONFIG_DIR`, else `config/` in the checkout around the working directory."""
    if override := os.environ.get(CONFIG_DIR_ENV):
        return Path(override)
    root = find_repo_root(Path.cwd())
    if root is None:
        raise ConfigError(f"not inside a job-hunt checkout; set {CONFIG_DIR_ENV}")
    return root / "config"


def _load[Model: BaseModel](path: Path, model: type[Model]) -> Model:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"{path}: file not found") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path}: invalid YAML: {exc}") from exc
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"{path}: {exc}") from exc


def check_tracks(tracks: Tracks, taxonomy: Taxonomy) -> list[str]:
    """Problems with tracks that need the taxonomy to spot."""
    slugs = taxonomy.domain_slugs
    selectable = taxonomy.selectable_slugs
    titles = {title for ts in taxonomy.titles_by_domain().values() for title in ts}
    problems: list[str] = []
    for track in tracks.tracks:
        for slug in [*track.domains.primary, *track.domains.adjacent]:
            if slug not in slugs:
                problems.append(f"track {track.id}: unknown domain {slug!r}")
            elif slug not in selectable:
                problems.append(f"track {track.id}: domain {slug!r} is not selectable")
        problems.extend(
            f"track {track.id}: include_titles entry {title!r} is not in job_titles.yaml"
            for title in track.include_titles
            if title not in titles
        )
    return problems


def check_policies(app: App, sources: Sources) -> list[str]:
    return [
        f"location_policy.bangladesh_boards: unknown source {name!r}"
        for name in app.location_policy.bangladesh_boards
        if name not in sources.sources
    ]


def load_config(config_dir: Path | None = None) -> Config:
    directory = config_dir or default_config_dir()
    taxonomy = _load(directory / "taxonomy" / "job_titles.yaml", Taxonomy)
    tracks = _load(directory / "taxonomy" / "tracks.yaml", Tracks)
    if problems := check_tracks(tracks, taxonomy):
        raise ConfigError(f"{directory / 'taxonomy' / 'tracks.yaml'}: " + "; ".join(problems))
    app = _load(directory / "app.yaml", App)
    sources = _load(directory / "sources.yaml", Sources)
    if problems := check_policies(app, sources):
        raise ConfigError(f"{directory / 'app.yaml'}: " + "; ".join(problems))
    llm = _load(directory / "llm.yaml", Llm)
    root = directory.parent
    for prompt in (llm.extraction.prompt, llm.title_classification.prompt):
        if not (root / prompt).is_file():
            raise ConfigError(f"{directory / 'llm.yaml'}: prompt {prompt} not found")
    return Config(
        root=root,
        app=app,
        taxonomy=taxonomy,
        tracks=tracks,
        title_rules=_load(directory / "taxonomy" / "title_rules.yaml", TitleRules),
        skills=_load(directory / "skills.yaml", Skills),
        locations=_load(directory / "locations.yaml", Locations),
        sources=sources,
        geography=_load(directory / "geography.yaml", Geography),
        llm=llm,
        scoring=_load(directory / "scoring.yaml", Scoring),
    )
