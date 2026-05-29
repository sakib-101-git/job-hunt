import os
import sys
from pathlib import Path
from pydantic import BaseModel
from dotenv import load_dotenv
import yaml

ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env")


class AppConfig(BaseModel):
    groq_api_key: str
    telegram_bot_token: str
    telegram_chat_id: str
    imap_host: str | None = None
    imap_email: str | None = None
    imap_app_password: str | None = None
    log_level: str = "INFO"

    search_keywords: list[str]
    search_locations: list[str]
    exclude_keywords: list[str] = []
    exclude_companies: list[str] = []
    max_age_hours: int = 72
    min_fit_score: int = 6

    request_delay: int = 3
    user_agent: str = "Mozilla/5.0"
    max_retries: int = 2
    timeout_seconds: int = 15

    llm_model: str = "llama-3.3-70b-versatile"
    scoring_model: str = "llama-3.1-8b-instant"
    max_tokens_scoring: int = 300
    max_tokens_tailoring: int = 2000

    telegram_enabled: bool = True

    db_path: str = "data/jobs.db"
    output_dir: str = "output/"

    profile: dict


def load_config() -> AppConfig:
    cfg_path = ROOT / "config.yaml"
    profile_path = ROOT / "profile.yaml"

    with open(cfg_path) as f:
        cfg = yaml.safe_load(f)

    with open(profile_path) as f:
        profile = yaml.safe_load(f)

    search = cfg.get("search", {})
    scraping = cfg.get("scraping", {})
    llm = cfg.get("llm", {})
    notifications = cfg.get("notifications", {})

    groq_key = os.getenv("GROQ_API_KEY", "")
    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN", "")

    missing = []
    if not groq_key:
        missing.append("GROQ_API_KEY")
    if not telegram_token:
        missing.append("TELEGRAM_BOT_TOKEN")
    if missing:
        print(f"ERROR: Missing required env vars: {', '.join(missing)}", file=sys.stderr)
        print("Copy .env.example to .env and fill in your secrets.", file=sys.stderr)
        print("Get a free Groq key at: https://console.groq.com", file=sys.stderr)
        sys.exit(1)

    return AppConfig(
        groq_api_key=groq_key,
        telegram_bot_token=telegram_token,
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
        imap_host=os.getenv("IMAP_HOST"),
        imap_email=os.getenv("IMAP_EMAIL"),
        imap_app_password=os.getenv("IMAP_APP_PASSWORD"),
        log_level=os.getenv("LOG_LEVEL", "INFO"),

        search_keywords=search.get("keywords", []),
        search_locations=search.get("locations", []),
        exclude_keywords=search.get("exclude_keywords", []),
        exclude_companies=search.get("exclude_companies", []),
        max_age_hours=search.get("max_age_hours", 72),
        min_fit_score=search.get("min_fit_score", 6),

        request_delay=scraping.get("request_delay_seconds", 3),
        user_agent=scraping.get("user_agent", "Mozilla/5.0"),
        max_retries=scraping.get("max_retries", 2),
        timeout_seconds=scraping.get("timeout_seconds", 15),

        llm_model=llm.get("model", "llama-3.3-70b-versatile"),
        scoring_model=llm.get("scoring_model", "llama-3.1-8b-instant"),
        max_tokens_scoring=llm.get("max_tokens_scoring", 300),
        max_tokens_tailoring=llm.get("max_tokens_tailoring", 2000),

        telegram_enabled=notifications.get("telegram_enabled", True),

        profile=profile,
    )
