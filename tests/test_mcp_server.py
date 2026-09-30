import pandas as pd
import pytest

import src.mcp.server as mcp_mod
from src.mcp.server import (
    _filter_dive,
    analyze_dive_profile,
    get_dive_summary,
    list_dives,
)


def _make_dive_data():
    """Sample dive data used across tests."""
    return pd.DataFrame(
        {
            "dive_number": ["1"] * 5 + ["2"] * 5,
            "trip_name": ["Trip A"] * 10,
            "dive_site_name": ["Reef Site"] * 10,
            "time": [0, 60, 120, 180, 240] * 2,
            "depth": [5, 10, 15, 10, 5, 10, 20, 30, 20, 10],
            "temperature": [25.0] * 10,
            "pressure": [200, 180, 160, 140, 120, 200, 170, 140, 110, 80],
            "rbt": [60, 50, 40, 30, 20, 60, 40, 20, 10, 5],
            "ndl": [99, 80, 60, 40, 30, 99, 60, 20, 10, 5],
            "sac_rate": [15.0] * 10,
            "rating": [4] * 5 + [2] * 5,
        }
    )


@pytest.fixture(autouse=True)
def _load_dive_data():
    """Load sample data into MCP session state before each test."""
    mcp_mod._dive_data = _make_dive_data()
    yield
    mcp_mod._dive_data = None


def test_filter_dive_string_match():
    df = _make_dive_data()
    result = _filter_dive(df, "1")
    assert len(result) == 5


def test_filter_dive_int_fallback():
    df = _make_dive_data()
    # force int dive_number column
    df["dive_number"] = df["dive_number"].astype(int)
    result = _filter_dive(df, "2")
    assert len(result) == 5


def test_get_dive_summary():
    result = get_dive_summary("1")
    assert "Dive 1" in result
    assert "Reef Site" in result
    assert "15.0m" in result  # max depth


def test_get_dive_summary_not_found():
    result = get_dive_summary("999")
    assert "No data found" in result


def test_analyze_dive_profile_adverse():
    result = analyze_dive_profile("2")
    assert "Dive 2" in result
    assert "adverse conditions" in result


def test_analyze_dive_profile_clean():
    result = analyze_dive_profile("1")
    assert "No major safety issues" in result


def test_list_dives():
    result = list_dives()
    assert "2" in result
    assert "Reef Site" in result
    assert "15.0m" in result


def test_no_dive_data_raises():
    mcp_mod._dive_data = None
    with pytest.raises(ValueError, match="No dive log loaded"):
        list_dives()
