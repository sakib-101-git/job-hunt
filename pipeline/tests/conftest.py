import shutil
from pathlib import Path

import pytest

from jobhunt.config import Config, find_repo_root, load_config
from jobhunt.taxonomy.matcher import TitleMatcher


@pytest.fixture(scope="session")
def repo_root() -> Path:
    root = find_repo_root(Path(__file__).resolve())
    assert root is not None
    return root


@pytest.fixture(scope="session")
def config(repo_root: Path) -> Config:
    return load_config(repo_root / "config")


@pytest.fixture(scope="session")
def matcher(config: Config) -> TitleMatcher:
    return TitleMatcher(config)


@pytest.fixture
def checkout(tmp_path: Path, repo_root: Path) -> Path:
    """A minimal job-hunt checkout in tmp_path with a copy of the real config/."""
    (tmp_path / "pipeline").mkdir()
    (tmp_path / "pipeline" / "pyproject.toml").write_text("", encoding="utf-8")
    shutil.copytree(repo_root / "config", tmp_path / "config")
    shutil.copytree(repo_root / "prompts", tmp_path / "prompts")
    return tmp_path


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
