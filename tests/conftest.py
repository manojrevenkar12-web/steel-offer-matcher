import os
from datetime import date

import pytest

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

from steelmatch.domain.grades import GradeKB  # noqa: E402
from steelmatch.settings import load  # noqa: E402

TODAY = date(2026, 11, 17)


@pytest.fixture(scope="session")
def kb():
    return GradeKB.load()


@pytest.fixture(scope="session")
def settings():
    return load()
