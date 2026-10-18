"""Logging: one JSON object per line on stderr, tagged with the run id (GitHub's
run id in Actions), at LOG_LEVEL. Message text, CV text and tokens are never
logged (brief §12)."""

import json
import logging
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime


class JsonFormatter(logging.Formatter):
    def __init__(self, run_id: str) -> None:
        super().__init__()
        self._run_id = run_id

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "time": datetime.fromtimestamp(record.created, UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "run_id": self._run_id,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            entry["error"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


def configure_logging(environ: Mapping[str, str]) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter(environ.get("GITHUB_RUN_ID") or uuid.uuid4().hex[:12]))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(environ.get("LOG_LEVEL", "INFO").upper())
    # httpx logs every request URL at INFO, and the Telegram URL holds the bot token.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
