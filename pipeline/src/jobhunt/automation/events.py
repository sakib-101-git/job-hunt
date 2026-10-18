"""GitHub `repository_dispatch` events (brief §10.5): the bot and the Worker's
cron ask for work by event type, with a small JSON payload.

The workflow passes the event file (`$GITHUB_EVENT_PATH`) instead of payload
values on the command line, so user ids never appear in the public run logs.
"""

import json
from pathlib import Path
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from jobhunt.automation.pipeline import Pipeline, RunSummary

MAX_HTML = 4096  # Telegram's message limit


class EventError(Exception):
    """The event file is missing, unreadable, or not an event this pipeline knows."""


class _Payload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class UserPayload(_Payload):
    user_id: Annotated[int, Field(gt=0)]


class BroadcastPayload(_Payload):
    html: Annotated[str, Field(min_length=1, max_length=MAX_HTML)]
    admin_id: Annotated[int, Field(gt=0)]


class EmptyPayload(_Payload):
    pass


def read_event(path: Path) -> tuple[str, dict[str, Any]]:
    """(event type, client payload) from a repository_dispatch event file."""
    try:
        event = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise EventError(f"cannot read the event file: {exc}") from exc
    action = event.get("action")
    if not isinstance(action, str):
        raise EventError("not a repository_dispatch event (no action)")
    payload = event.get("client_payload") or {}
    if not isinstance(payload, dict):
        raise EventError("client_payload is not an object")
    return action, payload


async def handle_event(pipeline: Pipeline, action: str, payload: dict[str, Any]) -> RunSummary:
    try:
        if action == "run-pipeline":
            EmptyPayload.model_validate(payload)
            return await pipeline.cycle("dispatch")
        if action == "backfill-user":
            return await pipeline.backfill(UserPayload.model_validate(payload).user_id)
        if action == "parse-cv":
            return await pipeline.parse_cv(UserPayload.model_validate(payload).user_id)
        if action == "send-digest":
            return await pipeline.send_digest(UserPayload.model_validate(payload).user_id)
        if action == "broadcast":
            message = BroadcastPayload.model_validate(payload)
            return await pipeline.broadcast(message.html, message.admin_id)
    except ValidationError as exc:
        raise EventError(f"bad payload for {action}: {exc.error_count()} errors") from exc
    raise EventError(f"unknown event type {action!r}")
