"""Command-line entry point: `jobhunt <command>`.

Commands are added as each pipeline stage lands. `doctor` checks that the
environment can run the pipeline at all.
"""

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from jobhunt import __version__

REQUIRED_PYTHON = (3, 12)


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


def find_repo_root(start: Path) -> Path | None:
    """Walk up from `start` to the directory that holds `pipeline/pyproject.toml`."""
    for candidate in (start, *start.parents):
        if (candidate / "pipeline" / "pyproject.toml").is_file():
            return candidate
    return None


def run_checks(cwd: Path, python_version: tuple[int, int]) -> list[Check]:
    checks = [
        Check(
            "python",
            python_version == REQUIRED_PYTHON,
            f"found {python_version[0]}.{python_version[1]}, "
            f"need {REQUIRED_PYTHON[0]}.{REQUIRED_PYTHON[1]}",
        )
    ]
    root = find_repo_root(cwd)
    checks.append(
        Check("repo", root is not None, str(root) if root else "not inside a job-hunt checkout")
    )
    return checks


def _doctor() -> int:
    checks = run_checks(Path.cwd(), (sys.version_info.major, sys.version_info.minor))
    for check in checks:
        mark = "ok  " if check.ok else "FAIL"
        sys.stdout.write(f"[{mark}] {check.name}: {check.detail}\n")
    return 0 if all(check.ok for check in checks) else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jobhunt")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", help="check that this environment can run the pipeline")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "doctor":
        return _doctor()
    raise AssertionError(f"unhandled command {args.command!r}")  # argparse rejects unknown ones


if __name__ == "__main__":
    sys.exit(main())
