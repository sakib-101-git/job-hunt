from pathlib import Path

from check_hardcoding import load_allowlist, main, scan


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_finds_each_kind_of_hard_coded_value(tmp_path: Path) -> None:
    _write(tmp_path, "pipeline/src/jobhunt/x.py", 'URL = "https://api.example.com/v1"\n')
    _write(tmp_path, "bot/src/a.ts", 'const link = "t.me/somebot";\nconst admin = 123456789;\n')
    _write(tmp_path, "bot/src/b.ts", 'const m = "gemini-3.5-flash";\nconst c = "*/10 * * * *";\n')
    _write(tmp_path, "pipeline/src/jobhunt/score/s.py", "BONUS = 0.75\nSECTION = 'brief §6.7'\n")
    rules = sorted(f.rule for f in scan(tmp_path, []))
    assert rules == [
        "chat-id-sized number",
        "cron expression",
        "decimal in scoring code",
        "model id",
        "telegram link",
        "url",
    ]


def test_generated_files_and_allowlisted_lines_pass(tmp_path: Path) -> None:
    _write(tmp_path, "bot/src/generated/config.json.ts", 'x = "https://api.example.com"\n')
    _write(tmp_path, "pipeline/src/jobhunt/y.py", 'DOCS = "https://docs.example.org"  # why\n')
    _write(
        tmp_path,
        "scripts/hardcoding_allowlist.txt",
        "# reason\npipeline/src/jobhunt/y.py: https://docs.example.org\n",
    )
    allow = load_allowlist(tmp_path / "scripts" / "hardcoding_allowlist.txt")
    assert allow == [("pipeline/src/jobhunt/y.py", "https://docs.example.org")]
    assert scan(tmp_path, allow) == []
    assert main(["check_hardcoding.py", str(tmp_path)]) == 0


def test_exit_status(tmp_path: Path) -> None:
    _write(tmp_path, "bot/src/c.ts", 'fetch("https://api.telegram.org/bot")\n')
    assert main(["check_hardcoding.py", str(tmp_path)]) == 1


def test_the_repository_is_clean() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    assert main(["check_hardcoding.py", str(repo_root)]) == 0
