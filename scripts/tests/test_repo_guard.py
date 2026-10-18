from pathlib import Path

import pytest

import repo_guard


@pytest.mark.parametrize(
    "line",
    [
        "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>",
        "co-authored-by: claude <x@example.com>",
        "Co-authored-by: Someone <noreply@anthropic.com>",
        "🤖 Generated with [Claude Code](https://claude.com/claude-code)",
        "Generated with Claude",
        "Claude-Session: https://claude.ai/code/session_123",
        "  Co-Authored-By: Anthropic Bot <bot@example.com>",
    ],
)
def test_attribution_is_flagged(line: str) -> None:
    assert repo_guard.attribution_violations(f"feat: thing\n\nbody\n\n{line}\n") == [line.strip()]


@pytest.mark.parametrize(
    "text",
    [
        "feat: add taxonomy matcher\n\nUses rapidfuzz for token-set matching.\n",
        "Co-Authored-By: A Friend <friend@example.com>\n",
        "docs: mention that Claude is not a contributor\n",
        "# Co-Authored-By: Claude <noreply@anthropic.com>\n",  # git comment line
        "",
    ],
)
def test_clean_text_passes(text: str) -> None:
    assert repo_guard.attribution_violations(text) == []


@pytest.mark.parametrize(
    "path",
    [
        "CLAUDE.md",
        "bot/CLAUDE.md",
        "AGENTS.md",
        ".claude/settings.json",
        "pipeline/.claude/commands/x.md",
        ".cursor/rules.mdc",
        "NOTES.md",
        "pipeline/README.md",
        "bot/src/notes.MD",
        ".env",
        "bot/.dev.vars",
        ".env.production",
        "profile.yaml",
        "data/jobs.db",
        "jobs.sqlite3",
        "key.pem",
    ],
)
def test_forbidden_tracked_files(path: str) -> None:
    problems = repo_guard.tracked_file_violations([path])
    assert len(problems) == 1
    assert problems[0].startswith(path)


@pytest.mark.parametrize(
    "path",
    [
        "README.md",
        "docs/RUNBOOK.md",
        "prompts/extraction.v1.md",
        ".github/pull_request_template.md",
        ".env.example",
        "pipeline/src/jobhunt/cli.py",
        "config/taxonomy/job_titles.yaml",
        "migrations/0001_init.sql",
        "bot/wrangler.toml",
    ],
)
def test_allowed_tracked_files(path: str) -> None:
    assert repo_guard.tracked_file_violations([path]) == []


def test_main_attribution_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    msg = tmp_path / "COMMIT_EDITMSG"
    msg.write_text("fix: x\n\nCo-Authored-By: Claude <noreply@anthropic.com>\n", encoding="utf-8")
    assert repo_guard.main(["attribution", str(msg)]) == 1
    assert "Remove these lines" in capsys.readouterr().err

    msg.write_text("fix: x\n", encoding="utf-8")
    assert repo_guard.main(["attribution", str(msg)]) == 0


def test_main_attribution_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO("Generated with Claude Code\n"))
    assert repo_guard.main(["attribution"]) == 1


def test_main_tracked_files_uses_git(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(repo_guard, "_git_tracked_files", lambda: ["README.md", "CLAUDE.md"])
    assert repo_guard.main(["tracked-files"]) == 1
    monkeypatch.setattr(repo_guard, "_git_tracked_files", lambda: ["README.md"])
    assert repo_guard.main(["tracked-files"]) == 0


def test_tracked_files_in_this_repo_are_clean() -> None:
    assert repo_guard.tracked_file_violations(repo_guard._git_tracked_files()) == []


@pytest.mark.parametrize("argv", [[], ["nope"], ["attribution", "a", "b"], ["tracked-files", "x"]])
def test_main_usage_errors(argv: list[str]) -> None:
    assert repo_guard.main(argv) == 2
