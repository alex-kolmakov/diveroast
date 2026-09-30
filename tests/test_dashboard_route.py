from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.dependencies import get_or_create_session
from src.api.main import app
from src.api.routes.dashboard import (
    _classify_experience,
    _classify_ndl_zone,
    _classify_region,
    _classify_water_type,
    _classify_zone,
    _compute_danger_score,
    _identify_issues,
)
from src.parsers import get_parser


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def session_with_dives():
    """Create a session with real dive data from the fixture file."""
    parser = get_parser("export.ssrf")
    df = parser.parse("tests/fixtures/anonymized_subsurface_export.ssrf")
    sid, agent = get_or_create_session()
    agent.set_dive_data(df)
    return sid


# --- Unit tests for helper functions ---


class TestClassifyZone:
    def test_safe(self):
        assert _classify_zone(10.0, 18.0, 30.0) == "safe"

    def test_warning(self):
        assert _classify_zone(20.0, 18.0, 30.0) == "warning"

    def test_danger(self):
        assert _classify_zone(35.0, 18.0, 30.0) == "danger"

    def test_boundary_safe(self):
        assert _classify_zone(18.0, 18.0, 30.0) == "safe"

    def test_boundary_warning(self):
        assert _classify_zone(30.0, 18.0, 30.0) == "warning"


class TestClassifyNdlZone:
    def test_safe(self):
        assert _classify_ndl_zone(15.0) == "safe"

    def test_warning(self):
        assert _classify_ndl_zone(7.0) == "warning"

    def test_danger(self):
        assert _classify_ndl_zone(3.0) == "danger"


class TestComputeDangerScore:
    def test_safe_dive(self):
        row = {
            "min_ndl": 20,
            "max_ascend_speed": 5,
            "sac_rate": 10,
            "max_depth": 10,
        }
        assert _compute_danger_score(row) == 0.0

    def test_all_danger(self):
        row = {
            "min_ndl": 2,
            "max_ascend_speed": 15,
            "sac_rate": 25,
            "max_depth": 40,
        }
        score = _compute_danger_score(row)
        # NDL danger: 3*2=6, ascent danger: 2*2=4, SAC danger: 1*2=2, depth danger: 1*2=2
        assert score == 14.0

    def test_warning_level(self):
        row = {
            "min_ndl": 7,
            "max_ascend_speed": 9.5,
            "sac_rate": 17,
            "max_depth": 25,
        }
        score = _compute_danger_score(row)
        # NDL warning: 3, ascent warning: 2, SAC warning: 1, depth warning: 1
        assert score == 7.0

    def test_bolt_to_surface_scores_like_a_sustained_fast_ascent(self):
        calm = {"max_ascend_speed": 6, "max_shallow_ascend_speed": 6, "max_depth": 12}
        bolt = {**calm, "max_shallow_ascend_speed": 12}
        hard_bolt = {**calm, "max_shallow_ascend_speed": 24}
        sustained = {**calm, "max_ascend_speed": 12}
        assert _compute_danger_score(calm) == 0.0
        assert _compute_danger_score(bolt) == 2.0
        assert (
            _compute_danger_score(hard_bolt) == 4.0 == _compute_danger_score(sustained)
        )


class TestIdentifyIssues:
    def test_bolt_to_surface_is_its_own_issue(self):
        row = {"max_ascend_speed": 6, "max_shallow_ascend_speed": 18, "max_depth": 12}
        assert _identify_issues(row) == ["bolted to surface"]

    def test_no_issues(self):
        row = {
            "max_ascend_speed": 5,
            "min_ndl": 20,
            "sac_rate": 10,
            "max_depth": 10,
        }
        assert _identify_issues(row) == []

    def test_multiple_issues(self):
        row = {
            "max_ascend_speed": 12,
            "min_ndl": 3,
            "sac_rate": 22,
            "max_depth": 35,
        }
        issues = _identify_issues(row)
        assert issues == [
            "rapid ascent",
            "low NDL",
            "high air consumption",
            "deep dive",
        ]


class TestClassifyWaterType:
    def test_tropical(self):
        assert _classify_water_type(28.0) == "Tropical"

    def test_temperate(self):
        assert _classify_water_type(20.0) == "Temperate"

    def test_cold(self):
        assert _classify_water_type(8.0) == "Cold water"


class TestClassifyRegion:
    def test_red_sea(self):
        assert _classify_region(27.0, 34.0) == "Red Sea"

    def test_caribbean(self):
        assert _classify_region(18.0, -65.0) == "Caribbean"

    def test_unknown(self):
        assert _classify_region(0.0, 0.0) is None


class TestClassifyExperience:
    def test_beginner(self):
        assert _classify_experience(10, 15.0) == "beginner"

    def test_intermediate(self):
        assert _classify_experience(50, 20.0) == "intermediate"

    def test_advanced_by_depth(self):
        assert _classify_experience(10, 45.0) == "advanced"

    def test_advanced_by_count(self):
        assert _classify_experience(150, 15.0) == "advanced"


# --- Route integration tests ---


@pytest.mark.anyio
async def test_dashboard_session_not_found():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/dashboard/nonexistent-session")
    assert response.status_code == 404


@pytest.mark.anyio
async def test_dashboard_no_dive_data():
    sid, _ = get_or_create_session()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/api/dashboard/{sid}")
    assert response.status_code == 400


@pytest.mark.anyio
@patch(
    "src.api.routes.dashboard._generate_dive_summaries",
    return_value=["Summary 1.", "Summary 2.", "Summary 3."],
)
async def test_dashboard_success(mock_summaries, session_with_dives):
    sid = session_with_dives
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/api/dashboard/{sid}")

    assert response.status_code == 200
    data = response.json()

    # Check top-level structure
    assert "session_id" in data
    assert data["session_id"] == sid
    assert "aggregate_stats" in data
    assert "metrics" in data
    assert "all_dives" in data
    assert "top_problematic_dives" in data
    assert "diver_profile" in data

    # Aggregate stats
    stats = data["aggregate_stats"]
    assert stats["total_dives"] > 0
    assert isinstance(stats["avg_max_depth"], float)
    assert isinstance(stats["avg_sac_rate"], float)

    # Metrics have per_dive values and worst_val
    assert len(data["metrics"]) > 0
    for metric in data["metrics"]:
        assert metric["zone"] in ("safe", "warning", "danger")
        assert "label" in metric
        assert "unit" in metric
        assert "per_dive" in metric
        assert "worst_val" in metric
        assert metric["total"] == stats["total_dives"]
        # Only dives that recorded the metric are plotted.
        assert len(metric["per_dive"]) == metric["recorded"] <= metric["total"]
        for pt in metric["per_dive"]:
            assert "dive_number" in pt
            assert "value" in pt
            assert pt["zone"] in ("safe", "warning", "danger")

    # All dives have required fields including new ones
    for dive in data["all_dives"]:
        assert "dive_number" in dive
        assert "max_depth" in dive
        assert "max_ascend_speed" in dive
        assert "dive_site_name" in dive
        assert "trip_name" in dive
        assert "latitude" in dive
        assert "longitude" in dive

    # Top problematic dives are at most 3 and ordered by danger score
    assert len(data["top_problematic_dives"]) <= 3
    if len(data["top_problematic_dives"]) > 1:
        scores = [d["danger_score"] for d in data["top_problematic_dives"]]
        assert scores == sorted(scores, reverse=True)

    # Problematic dives have summary and pick_reason
    for dive in data["top_problematic_dives"]:
        assert "summary" in dive
        assert isinstance(dive["summary"], str)
        assert len(dive["summary"]) > 0
        assert "pick_reason" in dive
        assert isinstance(dive["pick_reason"], str)

    # Diver profile structure
    profile = data["diver_profile"]
    assert "water_types" in profile
    assert "regions" in profile
    assert "experience_level" in profile
    assert "dive_sites" in profile
    assert profile["experience_level"] in ("beginner", "intermediate", "advanced")
