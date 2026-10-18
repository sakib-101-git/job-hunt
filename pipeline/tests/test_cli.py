from pathlib import Path

import pytest

from jobhunt import __version__, shared
from jobhunt.cli import main, run_checks
from jobhunt.config import find_repo_root, load_config


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
