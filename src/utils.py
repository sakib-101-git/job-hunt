import logging
import re
import time
import functools
import html
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path


class _SafeStreamHandler(logging.StreamHandler):
    """StreamHandler that replaces un-encodable chars instead of crashing on Windows cp1252."""
    def emit(self, record):
        try:
            msg = self.format(record)
            enc = getattr(self.stream, "encoding", None) or "ascii"
            try:
                self.stream.write(msg + self.terminator)
            except UnicodeEncodeError:
                safe = msg.encode(enc, errors="replace").decode(enc)
                self.stream.write(safe + self.terminator)
            self.flush()
        except Exception:
            self.handleError(record)


def setup_logging(log_level: str = "INFO", log_dir: str = "logs") -> logging.Logger:
    Path(log_dir).mkdir(exist_ok=True)
    logger = logging.getLogger("jobhunt")
    logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    if not logger.handlers:
        fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        sh = _SafeStreamHandler()
        sh.setFormatter(fmt)
        logger.addHandler(sh)

        fh = RotatingFileHandler(
            Path(log_dir) / "jobhunt.log",
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
        )
        fh.setFormatter(fmt)
        logger.addHandler(fh)

    return logger


def retry(max_attempts: int = 3, delay: float = 2.0, backoff: float = 2.0):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            wait = delay
            last_exc = None
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except Exception as exc:
                    last_exc = exc
                    if attempt < max_attempts - 1:
                        time.sleep(wait)
                        wait *= backoff
            raise last_exc
        return wrapper
    return decorator


def parse_relative_date(text: str) -> datetime | None:
    if not text:
        return None
    text = text.strip().lower()
    now = datetime.now(timezone.utc)

    patterns = [
        (r"just now|moments? ago", timedelta(minutes=5)),
        (r"(\d+)\s+minute", None),
        (r"(\d+)\s+hour", None),
        (r"(\d+)\s+day", None),
        (r"(\d+)\s+week", None),
        (r"yesterday", timedelta(days=1)),
    ]

    if re.search(r"just now|moments? ago", text):
        return now - timedelta(minutes=5)
    if re.search(r"yesterday", text):
        return now - timedelta(days=1)

    m = re.search(r"(\d+)\s+minute", text)
    if m:
        return now - timedelta(minutes=int(m.group(1)))
    m = re.search(r"(\d+)\s+hour", text)
    if m:
        return now - timedelta(hours=int(m.group(1)))
    m = re.search(r"(\d+)\s+day", text)
    if m:
        return now - timedelta(days=int(m.group(1)))
    m = re.search(r"(\d+)\s+week", text)
    if m:
        return now - timedelta(weeks=int(m.group(1)))

    # Try standard date formats
    for fmt in ("%d %b %Y", "%B %d, %Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue

    return None


def clean_html(raw_html: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", raw_html, flags=re.IGNORECASE)
    text = re.sub(r"</p>|</div>|</li>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def truncate_text(text: str, max_tokens: int) -> str:
    max_chars = max_tokens * 4
    if len(text) <= max_chars:
        return text
    cutoff = text.rfind(" ", 0, max_chars)
    return text[:cutoff] if cutoff > 0 else text[:max_chars]


def sanitize_filename(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s-]+", "_", text)
    return text[:80]
