"""Repository guards shared by the commit-msg hook, CI and `make guard`.

    repo_guard.py attribution [FILE]   check commit/PR text (FILE or stdin)
    repo_guard.py tracked-files        check the files tracked by git

Exit status is 0 when clean and 1 when a rule is violated.
"""

import fnmatch
import re
import subprocess
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path

# Commit trailers, PR footers and session links added by AI coding tools.
ATTRIBUTION = re.compile(
    r"co-authored-by:.*(claude|anthropic)"
    r"|generated with.*claude"
    r"|claude-session:"
    r"|noreply@anthropic\.com",
    re.IGNORECASE,
)

# AI-tool instruction and state files never belong in the repository.
FORBIDDEN_NAMES = frozenset(
    {"CLAUDE.md", "CLAUDE.local.md", "AGENTS.md", "GEMINI.md", ".cursorrules", ".windsurfrules"}
)
FORBIDDEN_DIRS = frozenset({".claude", ".cursor", ".aider", ".gemini"})

# Markdown is allowed only in the README and these trees.
MARKDOWN_ALLOWED = ("README.md", "docs/*", "prompts/*", ".github/*")

# Secrets, personal data and databases.
SECRET_PATTERNS = (
    ".env",
    ".env.*",
    ".dev.vars",
    ".dev.vars.*",
    "profile.yaml",
    "*.pem",
    "*.db",
    "*.sqlite",
    "*.sqlite3",
)
SECRET_ALLOWED = frozenset({".env.example"})


def attribution_violations(text: str) -> list[str]:
    """Lines of `text` that carry AI attribution. Git comment lines are ignored."""
    return [
        line.strip()
        for line in text.splitlines()
        if not line.startswith("#") and ATTRIBUTION.search(line)
    ]


def tracked_file_violations(paths: Iterable[str]) -> list[str]:
    problems: list[str] = []
    for path in paths:
        parts = path.split("/")
        name = parts[-1]
        if name in FORBIDDEN_NAMES or FORBIDDEN_DIRS.intersection(parts[:-1]):
            problems.append(f"{path}: AI-tool files must not be committed")
        elif name.lower().endswith(".md") and not any(
            fnmatch.fnmatchcase(path, pattern) for pattern in MARKDOWN_ALLOWED
        ):
            problems.append(f"{path}: Markdown belongs in README.md or docs/")
        elif name not in SECRET_ALLOWED and any(
            fnmatch.fnmatchcase(name, pattern) for pattern in SECRET_PATTERNS
        ):
            problems.append(f"{path}: secrets, personal data and databases must not be committed")
    return problems


def _git_tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z"],  # noqa: S607 (git from PATH is intended)
        check=True,
        capture_output=True,
    ).stdout
    return [p for p in out.decode("utf-8").split("\0") if p]


def _report(title: str, problems: Sequence[str]) -> int:
    if not problems:
        return 0
    sys.stderr.write(f"{title}\n")
    for problem in problems:
        sys.stderr.write(f"  - {problem}\n")
    return 1


def main(argv: Sequence[str]) -> int:
    if len(argv) >= 1 and argv[0] == "attribution" and len(argv) <= 2:
        text = Path(argv[1]).read_text(encoding="utf-8") if len(argv) == 2 else sys.stdin.read()
        return _report(
            "AI attribution is not allowed in commits or pull requests. Remove these lines:",
            attribution_violations(text),
        )
    if list(argv) == ["tracked-files"]:
        return _report(
            "Tracked files break repository rules:", tracked_file_violations(_git_tracked_files())
        )
    sys.stderr.write(__doc__ or "")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
