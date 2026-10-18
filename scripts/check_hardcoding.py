"""Find values that belong in config/, locales/ or the environment but were
written into code (brief §9): URLs, t.me links, model ids, chat-id-sized
numbers, cron expressions, and decimal numbers in the scoring code.

    check_hardcoding.py [ROOT]

Lines listed in scripts/hardcoding_allowlist.txt ("path: text the line
contains") are accepted. Comments and docstrings are not exempt on purpose:
an example URL in a comment is cheap to allowlist and easy to spot in review.
Exit status is 0 when clean and 1 otherwise.
"""

import re
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

SOURCES = ("pipeline/src", "bot/src", "bot/scripts")
SUFFIXES = (".py", ".ts")
SKIP_PARTS = ("generated",)
SCORING = ("pipeline/src/jobhunt/score/",)
ALLOWLIST = Path("scripts") / "hardcoding_allowlist.txt"


@dataclass(frozen=True)
class Rule:
    name: str
    pattern: re.Pattern[str]
    paths: tuple[str, ...] = ()  # limit to these path prefixes


RULES = (
    Rule("url", re.compile(r"https?://[\w.-]+\.[a-z]{2,}")),
    Rule("telegram link", re.compile(r"\bt\.me/")),
    Rule(
        "model id",
        re.compile(
            r"\b(gpt-[\w.-]+|gemini-[\w.-]+|llama[\w.-]*\d|qwen[\w.-]*\d|claude-[\w.-]+)", re.I
        ),
    ),
    Rule("chat-id-sized number", re.compile(r"(?<![\w.])\d{7,}(?![\w.])")),
    Rule("cron expression", re.compile(r"[\"'](?:[\d*/,-]+\s+){4}[\d*/,-]+[\"']")),
    Rule("decimal in scoring code", re.compile(r"(?<![\w.\"'§])\d+\.\d+(?![\w.])"), SCORING),
)


@dataclass(frozen=True)
class Finding:
    path: str
    line_no: int
    rule: str
    line: str


def load_allowlist(path: Path) -> list[tuple[str, str]]:
    if not path.is_file():
        return []
    entries = []
    for raw in path.read_text(encoding="utf-8").split("\n"):
        line = raw.strip()
        if line and not line.startswith("#"):
            file, _, text = line.partition(": ")
            entries.append((file.strip(), text.strip()))
    return entries


def scan(root: Path, allow: Sequence[tuple[str, str]]) -> list[Finding]:
    findings = []
    for path in _files(root):
        rel = path.relative_to(root).as_posix()
        for line_no, line in enumerate(path.read_text(encoding="utf-8").split("\n"), start=1):
            for rule in RULES:
                if rule.paths and not rel.startswith(rule.paths):
                    continue
                if rule.pattern.search(line) and not _allowed(rel, line, allow):
                    findings.append(Finding(rel, line_no, rule.name, line.strip()))
    return findings


def _files(root: Path) -> Iterable[Path]:
    for source in SOURCES:
        base = root / source
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if path.suffix in SUFFIXES and not set(SKIP_PARTS) & set(path.parts):
                yield path


def _allowed(rel: str, line: str, allow: Sequence[tuple[str, str]]) -> bool:
    return any(rel == file and text in line for file, text in allow)


def main(argv: Sequence[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else Path.cwd()
    findings = scan(root, load_allowlist(root / ALLOWLIST))
    for f in findings:
        sys.stderr.write(f"{f.path}:{f.line_no}: {f.rule}: {f.line}\n")
    if findings:
        sys.stderr.write(
            f"{len(findings)} hard-coded value(s): move them to config/ or locales/, or add the"
            f" line to {ALLOWLIST} with the reason\n"
        )
        return 1
    sys.stdout.write("no hard-coded values found\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
