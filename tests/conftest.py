import pytest

from src.api import limits


@pytest.fixture(autouse=True)
def _fresh_rate_limits():
    """Every test starts with empty per-IP counters."""
    for limiter in (limits.upload_limiter, limits.chat_limiter):
        limiter._hits.clear()
    yield
