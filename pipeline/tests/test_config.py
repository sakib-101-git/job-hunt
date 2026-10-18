from pathlib import Path

import pytest
import yaml

from jobhunt.config import CONFIG_DIR_ENV, Config, ConfigError, default_config_dir, load_config
from jobhunt.config.loader import check_tracks


def _edit(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text, old
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def test_real_config_loads(config: Config) -> None:
    taxonomy = config.taxonomy
    assert len(taxonomy.domains) == 35
    assert sum(len(d.titles) for d in taxonomy.domains) == 2095
    assert sum(len(a.titles) for a in taxonomy.additions) == 85
    assert taxonomy.domain_slugs - taxonomy.selectable_slugs == {"seniority-ladder"}
    assert len(config.tracks.tracks) == 16
    assert len(config.locations.districts) == 64
    assert len(config.locations.divisions) == 8


def test_tracks_are_consistent_with_taxonomy(config: Config) -> None:
    assert check_tracks(config.tracks, config.taxonomy) == []


def test_ai_track_includes_python_and_r_roles(config: Config) -> None:
    # The owner's own example: AI users must also get Python and R developer roles.
    ai = next(track for track in config.tracks.tracks if track.id == "ai_ml")
    assert {"Python Developer", "R Developer", "R Programmer"} <= set(ai.include_titles)


def test_place_names_cover_common_spellings(config: Config) -> None:
    names = config.locations.place_names()
    assert {"chattogram", "chittagong", "ctg", "dhaka", "gulshan", "comilla", "bangladesh"} <= names


def test_default_config_dir_prefers_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(CONFIG_DIR_ENV, str(tmp_path))
    assert default_config_dir() == tmp_path


def test_default_config_dir_finds_checkout(monkeypatch: pytest.MonkeyPatch, checkout: Path) -> None:
    monkeypatch.delenv(CONFIG_DIR_ENV, raising=False)
    monkeypatch.chdir(checkout / "config")
    assert default_config_dir() == checkout / "config"
    assert isinstance(load_config(), Config)


def test_default_config_dir_outside_checkout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv(CONFIG_DIR_ENV, raising=False)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ConfigError, match=CONFIG_DIR_ENV):
        default_config_dir()


@pytest.mark.parametrize(
    ("file", "old", "new", "message"),
    [
        (
            "taxonomy/tracks.yaml",
            "primary: [cybersecurity]",
            "primary: [cyber-sec]",
            "unknown domain",
        ),
        ("taxonomy/tracks.yaml", '"Python Developer"', '"Python Ninja"', "is not in job_titles"),
        (
            "taxonomy/tracks.yaml",
            "adjacent: [data-science-analytics, robotics]",
            "adjacent: [data-science-analytics, seniority-ladder]",
            "is not selectable",
        ),
        (
            "taxonomy/tracks.yaml",
            "adjacent: [data-science-analytics, robotics]",
            "adjacent: [ai-ml]",
            "both primary and adjacent",
        ),
        ("taxonomy/tracks.yaml", "id: data_science", "id: ai_ml", "duplicate track ids"),
        (
            "taxonomy/job_titles.yaml",
            "slug: mobile-desktop",
            "slug: software-engineering",
            "duplicate domain slugs",
        ),
        (
            "taxonomy/job_titles.yaml",
            "  - domain: fintech",
            "  - domain: banking",
            "unknown domains",
        ),
        ("taxonomy/job_titles.yaml", "version: 1", "version: 2", "version"),
        ("app.yaml", "min_similarity: 0.7", "min_similarity: 1.5", "min_similarity"),
        ("app.yaml", "title_matching:", "title_matching:\n  typo_key: 1", "typo_key"),
        (
            "skills.yaml",
            "{id: java, name: Java}",
            "{id: java, name: Java, aliases: [Python3]}",
            "duplicate skill names",
        ),
        (
            "skills.yaml",
            "{id: java, name: Java}",
            "{id: python, name: Java}",
            "duplicate skill ids",
        ),
        ("locations.yaml", "  - mymensingh\n", "  - atlantis\n", "not districts"),
        (
            "locations.yaml",
            "{id: feni, name: Feni}",
            "{id: noakhali, name: Feni}",
            "duplicate district ids",
        ),
        (
            "taxonomy/title_rules.yaml",
            "  executive: [officer]",
            "  executive: [officer]\n  officer: [exec]",
            "duplicate equivalent words",
        ),
        (
            "sources.yaml",
            "    disabled_reason: US-centric; worldwide-remote roles are rare. Brief §6.1.\n",
            "",
            "needs a disabled_reason",
        ),
        (
            "sources.yaml",
            "    kind: remoteok\n    enabled: true\n",
            "    kind: remoteok\n    enabled: true\n    disabled_reason: x\n",
            "must not have a disabled_reason",
        ),
        (
            "sources.yaml",
            "    kind: disabled\n    enabled: false\n    disabled_reason: US-centric",
            "    kind: disabled\n    enabled: true\n    disabled_reason: US-centric",
            "an enabled source must not have",
        ),
        (
            "sources.yaml",
            "    url: https://remoteok.com/api",
            "    url: http://remoteok.com/api",
            "url",
        ),
        ("sources.yaml", "    kind: remoteok", "    kind: monster", "kind"),
        ("app.yaml", "timezone: Asia/Dhaka", "timezone: Mars/Olympus", "unknown timezone"),
        (
            "app.yaml",
            "  bangladesh_boards: [bdjobs]",
            "  bangladesh_boards: [nope]",
            "unknown source",
        ),
        (
            "app.yaml",
            "  max_years_gap: {student_intern: 2, ",
            "  max_years_gap: {",
            "missing levels",
        ),
        ("app.yaml", "  jd_max_chars_llm: 6000", "  jd_max_chars_llm: 9000", "cannot exceed"),
        (
            "geography.yaml",
            "  - {name: Oman, cities: [Muscat]}",
            "  - {name: Qatar}",
            "duplicate region",
        ),
        (
            "sources.yaml",
            "    kind: disabled\n    enabled: false\n"
            "    disabled_reason: US-centric; worldwide-remote roles are rare. Brief §6.1.\n",
            "    kind: disabled\n    enabled: true\n",
            "cannot be enabled",
        ),
    ],
)
def test_broken_config_is_rejected(
    checkout: Path, file: str, old: str, new: str, message: str
) -> None:
    _edit(checkout / "config" / file, old, new)
    with pytest.raises(ConfigError, match=message):
        load_config(checkout / "config")


def test_missing_file(checkout: Path) -> None:
    (checkout / "config" / "skills.yaml").unlink()
    with pytest.raises(ConfigError, match="file not found"):
        load_config(checkout / "config")


def test_invalid_yaml(checkout: Path) -> None:
    (checkout / "config" / "app.yaml").write_text("version: [1\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="invalid YAML"):
        load_config(checkout / "config")


def test_yaml_booleans_are_not_strings(checkout: Path) -> None:
    # YAML 1.1 reads a bare `on` as true; schemas must reject it, not coerce it.
    path = checkout / "config" / "taxonomy" / "title_rules.yaml"
    _edit(path, '"on", ', "on, ")
    with pytest.raises(ConfigError, match="valid string"):
        load_config(checkout / "config")


def test_taxonomy_file_matches_companion_layout(repo_root: Path) -> None:
    data = yaml.safe_load((repo_root / "config/taxonomy/job_titles.yaml").read_text("utf-8"))
    assert set(data) == {"version", "domains", "additions", "non_cse_exclusions"}
