from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


@pytest.fixture
def fixture_path():
    return lambda name: FIXTURES / name
