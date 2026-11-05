import shutil
from pathlib import Path

import pytest

FIXTURE_DOCS = Path("tests/fixtures/docs")


@pytest.fixture
def fixture_docs(request: pytest.FixtureRequest) -> Path:
    """The read-only fixture documents folder."""
    return request.config.rootpath / FIXTURE_DOCS


@pytest.fixture
def docs_dir(fixture_docs: Path, tmp_path: Path) -> Path:
    """A writable copy of the fixture documents, for tests that change files."""
    target = tmp_path / "docs"
    shutil.copytree(fixture_docs, target)
    return target
