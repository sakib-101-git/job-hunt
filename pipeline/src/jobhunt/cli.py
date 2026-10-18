"""Command-line entry point: `jobhunt <command>`.

Commands are added as each pipeline stage lands:

- `doctor` checks that this environment can run the pipeline at all.
- `validate-config` loads and cross-checks every file in config/.
- `build-shared` writes the Worker's generated config (`--check` only compares).
"""

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from jobhunt import __version__, shared
from jobhunt.config import ConfigError, find_repo_root, load_config

REQUIRED_PYTHON = (3, 12)


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


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
    if root is not None:
        try:
            load_config(root / "config")
            checks.append(Check("config", True, str(root / "config")))
        except ConfigError as exc:
            checks.append(Check("config", False, str(exc)))
    return checks


def _write(text: str) -> None:
    sys.stdout.write(f"{text}\n")


def _fail(text: str) -> int:
    sys.stderr.write(f"{text}\n")
    return 1


def _doctor() -> int:
    checks = run_checks(Path.cwd(), (sys.version_info.major, sys.version_info.minor))
    for check in checks:
        mark = "ok  " if check.ok else "FAIL"
        _write(f"[{mark}] {check.name}: {check.detail}")
    return 0 if all(check.ok for check in checks) else 1


def _validate_config() -> int:
    try:
        config = load_config()
    except ConfigError as exc:
        return _fail(str(exc))
    titles = sum(len(t) for t in config.taxonomy.titles_by_domain().values())
    _write(
        f"config ok: {len(config.taxonomy.domains)} domains, {titles} titles, "
        f"{len(config.tracks.tracks)} tracks, {len(config.skills.all_skills)} skills, "
        f"{len(config.locations.districts)} districts"
    )
    return 0


def _build_shared(check: bool) -> int:
    root = find_repo_root(Path.cwd())
    if root is None:
        return _fail("not inside a job-hunt checkout")
    try:
        config = load_config(root / "config")
    except ConfigError as exc:
        return _fail(str(exc))
    path = root / shared.GENERATED_PATH
    if check:
        current = path.read_text(encoding="utf-8") if path.is_file() else None
        if current != shared.render(config):
            return _fail(f"{shared.GENERATED_PATH} is out of date; run `make build-shared`")
        _write(f"{shared.GENERATED_PATH} is up to date")
        return 0
    changed = shared.write(config, path)
    _write(f"{shared.GENERATED_PATH} {'updated' if changed else 'already up to date'}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jobhunt")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", help="check that this environment can run the pipeline")
    commands.add_parser("validate-config", help="load and cross-check every file in config/")
    build = commands.add_parser("build-shared", help="write the Worker's generated config")
    build.add_argument("--check", action="store_true", help="fail if the file is out of date")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "doctor":
        return _doctor()
    if args.command == "validate-config":
        return _validate_config()
    if args.command == "build-shared":
        return _build_shared(args.check)
    raise AssertionError(f"unhandled command {args.command!r}")  # argparse rejects unknown ones


if __name__ == "__main__":
    sys.exit(main())
