from pathlib import Path

import httpx
import pytest

from jobhunt import __version__, shared
from jobhunt.cli import main, run_checks
from jobhunt.config import find_repo_root, load_config
from jobhunt.sources.http import Fetcher


def test_find_repo_root_from_nested_dir(checkout: Path) -> None:
    nested = checkout / "pipeline" / "src"
    nested.mkdir()
    assert find_repo_root(nested) == checkout


def test_find_repo_root_outside_checkout(tmp_path: Path) -> None:
    assert find_repo_root(tmp_path) is None


def test_checks_pass_in_checkout_with_python_312(checkout: Path) -> None:
    checks = run_checks(checkout, (3, 12))
    assert [check.name for check in checks] == ["python", "repo", "config"]
    assert all(check.ok for check in checks)


@pytest.mark.parametrize("version", [(3, 11), (3, 13), (3, 14)])
def test_python_check_fails_on_other_versions(checkout: Path, version: tuple[int, int]) -> None:
    by_name = {check.name: check for check in run_checks(checkout, version)}
    assert not by_name["python"].ok
    assert "need 3.12" in by_name["python"].detail


def test_repo_check_fails_outside_checkout(tmp_path: Path) -> None:
    by_name = {check.name: check for check in run_checks(tmp_path, (3, 12))}
    assert not by_name["repo"].ok
    assert "config" not in by_name


def test_config_check_fails_on_broken_config(checkout: Path) -> None:
    (checkout / "config" / "app.yaml").write_text("version: 1\n", encoding="utf-8")
    by_name = {check.name: check for check in run_checks(checkout, (3, 12))}
    assert not by_name["config"].ok
    assert "title_matching" in by_name["config"].detail


def test_doctor_exit_codes(
    checkout: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(checkout)
    assert main(["doctor"]) == 0
    assert "[ok  ] config" in capsys.readouterr().out

    monkeypatch.chdir(checkout.parent)
    assert main(["doctor"]) == 1


def test_validate_config(
    checkout: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(checkout)
    assert main(["validate-config"]) == 0
    assert "35 domains" in capsys.readouterr().out

    (checkout / "config" / "skills.yaml").write_text("version: 1\ncategories: []\n", "utf-8")
    assert main(["validate-config"]) == 1
    assert "skills.yaml" in capsys.readouterr().err


def test_build_shared_writes_then_checks(
    checkout: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(checkout)
    assert main(["build-shared", "--check"]) == 1
    assert "out of date" in capsys.readouterr().err

    assert main(["build-shared"]) == 0
    assert "updated" in capsys.readouterr().out
    assert (checkout / shared.GENERATED_PATH).is_file()

    assert main(["build-shared"]) == 0
    assert "already up to date" in capsys.readouterr().out
    assert main(["build-shared", "--check"]) == 0


def test_build_shared_needs_checkout_and_valid_config(
    checkout: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(checkout.parent)
    assert main(["build-shared"]) == 1
    assert "not inside" in capsys.readouterr().err

    monkeypatch.chdir(checkout)
    (checkout / "config" / "app.yaml").unlink()
    assert main(["build-shared"]) == 1
    assert "app.yaml" in capsys.readouterr().err


def test_committed_generated_config_is_current(repo_root: Path) -> None:
    path = repo_root / shared.GENERATED_PATH
    assert path.read_text(encoding="utf-8") == shared.render(load_config(repo_root / "config"))


def test_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_command_is_required() -> None:
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2


def test_sources_lists_every_source(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["sources"]) == 0
    out = capsys.readouterr().out
    assert "bdjobs" in out
    assert "every 10 min" in out
    assert "remotive" in out
    assert "off: Terms forbid" in out


def test_fetch_replays_and_reports(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    from jobhunt import cli
    from jobhunt.sources.recording import ReplayTransport

    fixtures = Path(__file__).parent / "fixtures" / "sources"

    async def no_sleep(_: float) -> None:
        return None

    def replaying(settings: object, client: object = None) -> Fetcher:
        transport = ReplayTransport(fixtures / "jobicy")
        return Fetcher(settings, httpx.AsyncClient(transport=transport), no_sleep)  # type: ignore[arg-type]

    monkeypatch.setattr(cli, "Fetcher", replaying)
    assert main(["fetch", "jobicy", "remotive", "themuse", "--show", "1"]) == 1
    captured = capsys.readouterr()
    assert "jobicy: 5 listings" in captured.out
    assert "Atlassian Administrator" in captured.out
    assert "remotive:" in captured.err  # not recorded: fails like a dead host
    assert "themuse: no such source" in captured.err

    assert main(["fetch", "jobicy", "--record", str(tmp_path / "rec")]) == 0


def _replaying_fetcher(names: list[str]) -> object:
    from jobhunt.sources.recording import ReplayTransport

    fixtures = Path(__file__).parent / "fixtures" / "sources"

    class Multi(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            for name in names:
                try:
                    return await ReplayTransport(fixtures / name).handle_async_request(request)
                except httpx.ConnectError:
                    continue
            raise httpx.ConnectError("unrecorded", request=request)

    async def no_sleep(_: float) -> None:
        return None

    def factory(settings: object, client: object = None) -> Fetcher:
        return Fetcher(settings, httpx.AsyncClient(transport=Multi()), no_sleep)  # type: ignore[arg-type]

    return factory


def test_collect_stores_jobs(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    from jobhunt.automation import pipeline

    monkeypatch.setattr(pipeline, "Fetcher", _replaying_fetcher(["jobicy", "workingnomads"]))
    monkeypatch.setenv("JOBHUNT_SQLITE_PATH", str(tmp_path / "jobs.db"))
    for name in ("CLOUDFLARE_ACCOUNT_ID", "D1_DATABASE_ID", "CLOUDFLARE_API_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    args = ["collect", "--source", "jobicy", "--source", "workingnomads", "--source", "remoteok"]
    assert main(args) == 0
    out = capsys.readouterr().out
    assert "jobicy: ok, 5 listings, 5 new" in out
    assert "remoteok: failed" in out
    assert "gates:" in out

    assert main([*args, "--force"]) == 0
    assert "jobicy: ok, 5 listings, 0 new" in capsys.readouterr().out


def test_collect_without_database(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for name in (
        "JOBHUNT_SQLITE_PATH",
        "CLOUDFLARE_ACCOUNT_ID",
        "D1_DATABASE_ID",
        "CLOUDFLARE_API_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)
    assert main(["collect"]) == 1
    assert "no database configured" in capsys.readouterr().err


def test_collect_fails_when_every_source_fails(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    from jobhunt.automation import pipeline

    monkeypatch.setattr(pipeline, "Fetcher", _replaying_fetcher([]))
    monkeypatch.setenv("JOBHUNT_SQLITE_PATH", str(tmp_path / "jobs.db"))
    assert main(["collect", "--source", "jobicy"]) == 1
    assert "FAILED: every source that ran failed" in capsys.readouterr().out


def test_enrich_without_keys_uses_rules(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.setenv("JOBHUNT_SQLITE_PATH", str(tmp_path / "jobs.db"))
    for name in ("GEMINI_API_KEY", "GROQ_API_KEY", "CLOUDFLARE_ACCOUNT_ID"):
        monkeypatch.delenv(name, raising=False)
    assert main(["enrich", "--limit", "5"]) == 0
    assert "models with a key: none (rules-only facts)" in capsys.readouterr().out


def test_enrich_without_database(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for name in (
        "JOBHUNT_SQLITE_PATH",
        "CLOUDFLARE_ACCOUNT_ID",
        "D1_DATABASE_ID",
        "CLOUDFLARE_API_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)
    assert main(["enrich"]) == 1
    assert "no database configured" in capsys.readouterr().err


def test_run_and_event_write_the_step_summary(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    from jobhunt.automation import pipeline

    monkeypatch.setattr(pipeline, "Fetcher", _replaying_fetcher([]))
    monkeypatch.setenv("JOBHUNT_SQLITE_PATH", str(tmp_path / "jobs.db"))
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    for name in ("CLOUDFLARE_ACCOUNT_ID", "TELEGRAM_BOT_TOKEN", "GEMINI_API_KEY", "GROQ_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    # Offline and without a bot token: the run is recorded as failed.
    assert main(["run", "--trigger", "schedule"]) == 1
    out = capsys.readouterr().out
    assert "== collect" in out
    assert "FAILED: TELEGRAM_BOT_TOKEN is not set" in out
    assert "## Pipeline run: schedule, failed" in summary.read_text(encoding="utf-8")

    event = tmp_path / "event.json"
    event.write_text('{"action": "parse-cv", "client_payload": {"user_id": 1}}', encoding="utf-8")
    assert main(["event", str(event)]) == 1  # no bot token
    event.write_text('{"action": "nope", "client_payload": {}}', encoding="utf-8")
    assert main(["event", str(event)]) == 1
    assert "unknown event type 'nope'" in capsys.readouterr().err
    assert main(["event", str(tmp_path / "missing.json")]) == 1
