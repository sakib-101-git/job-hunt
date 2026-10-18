from pathlib import Path

import pytest

from jobhunt import __version__
from jobhunt.cli import find_repo_root, main, run_checks


def _make_checkout(tmp_path: Path) -> Path:
    (tmp_path / "pipeline").mkdir()
    (tmp_path / "pipeline" / "pyproject.toml").write_text("", encoding="utf-8")
    return tmp_path


def test_find_repo_root_from_nested_dir(tmp_path: Path) -> None:
    root = _make_checkout(tmp_path)
    nested = root / "pipeline" / "src"
    nested.mkdir()
    assert find_repo_root(nested) == root


def test_find_repo_root_outside_checkout(tmp_path: Path) -> None:
    assert find_repo_root(tmp_path) is None


def test_checks_pass_in_checkout_with_python_312(tmp_path: Path) -> None:
    checks = run_checks(_make_checkout(tmp_path), (3, 12))
    assert all(check.ok for check in checks)


@pytest.mark.parametrize("version", [(3, 11), (3, 13), (3, 14)])
def test_python_check_fails_on_other_versions(tmp_path: Path, version: tuple[int, int]) -> None:
    by_name = {check.name: check for check in run_checks(_make_checkout(tmp_path), version)}
    assert not by_name["python"].ok
    assert "need 3.12" in by_name["python"].detail


def test_repo_check_fails_outside_checkout(tmp_path: Path) -> None:
    by_name = {check.name: check for check in run_checks(tmp_path, (3, 12))}
    assert not by_name["repo"].ok


def test_doctor_exit_codes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(_make_checkout(tmp_path))
    assert main(["doctor"]) == 0
    assert "[ok  ] repo" in capsys.readouterr().out

    monkeypatch.chdir(tmp_path.parent)
    assert main(["doctor"]) == 1


def test_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_command_is_required() -> None:
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2
