import pytest

from src.agent import usage
from src.api import limits


@pytest.fixture(autouse=True)
def _fresh_rate_limits():
    """Every test starts with empty per-IP counters."""
    for limiter in (limits.upload_limiter, limits.chat_limiter):
        limiter._hits.clear()
    usage.daily_budget._tokens = 0
    yield
