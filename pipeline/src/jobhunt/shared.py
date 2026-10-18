"""Build bot/src/generated/config.json: the slice of config/ the Worker needs.

The Worker never reads YAML. CI runs `jobhunt build-shared --check`, so the
committed JSON can't drift from the YAML it comes from.
"""

import json
from pathlib import Path
from typing import Any

from jobhunt.config import Config

GENERATED_PATH = Path("bot") / "src" / "generated" / "config.json"


def shared_data(config: Config) -> dict[str, Any]:
    districts = {district.id: district for district in config.locations.districts}
    return {
        "domains": [
            {"slug": domain.slug, "name": domain.name}
            for domain in config.taxonomy.domains
            if domain.selectable
        ],
        "tracks": [
            {
                "id": track.id,
                "label": track.label,
                "description": track.description,
                "primary_domains": track.domains.primary,
                "adjacent_domains": track.domains.adjacent,
                "include_titles": track.include_titles,
            }
            for track in config.tracks.tracks
        ],
        "skills": [
            {"id": skill.id, "name": skill.name, "aliases": skill.aliases, "category": category.id}
            for category in config.skills.categories
            for skill in category.skills
        ],
        "preferred_cities": [
            {"id": city, "name": districts[city].name}
            for city in config.locations.preferred_city_options
        ],
        "callbacks": config.app.callbacks.model_dump(),
        "bot": config.app.bot.model_dump(),
        "github": config.app.github.model_dump(),
        "timezone": config.app.timezone,
        "max_age_days": config.app.freshness.max_age_days,
        "notifications": {
            "max_attempts": config.app.notifications.max_attempts,
            "telegram_api_base_url": config.app.notifications.telegram_api_base_url,
        },
        "heartbeat": config.app.heartbeat.model_dump(),
    }


def render(config: Config) -> str:
    return json.dumps(shared_data(config), ensure_ascii=False, indent=2) + "\n"


def write(config: Config, path: Path) -> bool:
    """Write the JSON to `path`. Returns True when the file changed."""
    content = render(config)
    if path.is_file() and path.read_text(encoding="utf-8") == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return True
