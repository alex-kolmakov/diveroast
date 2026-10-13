from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.dependencies import get_or_create_session
from src.api.main import app
from src.api.routes.dashboard import (
    ISSUE_FAMILY,
    ISSUE_RANK_KEY,
    PICK_REASONS,
    _categories_by_severity,
    _classify_experience,
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


def test_gas_left_gauge_runs_from_plenty_to_empty():
    import pandas as pd

    from src.api.routes.dashboard import _build_metrics

    features = pd.DataFrame(
        {
            "dive_number": ["1", "2", "3", "4"],
            "end_pressure": [80.0, 40.0, 20.0, 10.0],
            "technical": [False, False, False, True],
        }
    )
    gauge = next(m for m in _build_metrics(features) if m.label == "Gas Left on Exit")
    # The technical dive carries gas the log may not show: it isn't judged.
    # Left to right like every other gauge: fine, under the reserve, red.
    assert [(p.dive_number, p.zone) for p in gauge.per_dive] == [
        ("1", "safe"),
        ("2", "warning"),
        ("3", "danger"),
    ]
    assert gauge.worst_val == 20.0 and gauge.zone == "danger"


class TestComputeDangerScore:
    def test_safe_dive(self):
        row = {
            "min_ndl": 20,
            "max_ascend_speed": 5,
            "sac_rate": 10,
            "max_depth": 10,
        }
        assert _compute_danger_score(row) == 0.0

    def test_limits_and_margins(self):
        row = {
            "min_ndl": 2,
            "max_ascend_speed": 15,
            "sac_rate": 25,
            "max_depth": 38,
        }
        score = _compute_danger_score(row)
        # Ascent limit broken: 2*2=4. A low NDL, air consumption and depth on
        # their own are facts: 0.
        assert score == 4.0

    def test_warning_level(self):
        row = {
            "min_ndl": 7,
            "max_ascend_speed": 9.5,
            "sac_rate": 17,
            "max_depth": 25,
        }
        score = _compute_danger_score(row)
        # Ascent near the limit: 2. Nothing else here is a finding.
        assert score == 2.0

    def test_a_deep_dive_inside_its_gas_limits_scores_nothing(self):
        deep = {"max_depth": 38, "max_po2": 1.01, "gas_density": 6.1, "min_ndl": 20}
        assert _compute_danger_score({**deep, "gas_density": 5.0}) == 0.0
        # Dense gas is a thin margin, not a limit.
        assert _compute_danger_score(deep) == 1.0

    def test_oxygen_pressure_is_a_limit(self):
        base = {"max_depth": 36, "min_ndl": 20}
        assert _compute_danger_score({**base, "max_po2": 1.45}) == 3.0
        assert _compute_danger_score({**base, "max_po2": 1.65}) == 6.0

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
        # A low NDL, air consumption and depth are facts, not issues.
        assert _identify_issues(row) == ["rapid ascent"]


class TestPickOrder:
    """Cards follow what is worst in the log, not a fixed ascent-first list."""

    CALM = {
        "max_depth": 15,
        "max_ascend_speed": 6,
        "max_shallow_ascend_speed": 6,
        "min_ndl": 30,
        "end_pressure": 80,
        "safety_stop_minutes": 4,
        "redescents": 0,
        "mid_dive_surfacings": 0,
    }

    def _scored(self, *rows):
        return [
            (str(i), _compute_danger_score(row), row, _identify_issues(row))
            for i, row in enumerate(rows, start=1)
        ]

    def test_gas_and_stop_issues_score(self):
        # Thin margins: 1 point, 2 at the far end. Never as much as a limit.
        assert _compute_danger_score({**self.CALM, "end_pressure": 40}) == 1.0
        assert _compute_danger_score({**self.CALM, "end_pressure": 10}) == 2.0
        assert _compute_danger_score({**self.CALM, "safety_stop_minutes": 2}) == 1.0
        assert _compute_danger_score({**self.CALM, "safety_stop_minutes": 0.5}) == 2.0
        assert _compute_danger_score({**self.CALM, "redescents": 3}) == 2.0

    def test_new_issues_are_identified(self):
        row = {**self.CALM, "end_pressure": 10, "safety_stop_minutes": 0.5}
        assert _identify_issues(row) == ["low gas reserve", "short safety stop"]

    def test_unrecorded_gas_and_stop_score_nothing(self):
        row = {**self.CALM, "end_pressure": None, "safety_stop_minutes": None}
        assert _compute_danger_score(row) == 0.0

    def test_a_broken_limit_outranks_a_thin_margin(self):
        scored = self._scored(
            {**self.CALM, "end_pressure": 10},
            {**self.CALM, "max_ascend_speed": 12},
        )
        assert _categories_by_severity(scored) == ["rapid ascent", "low gas reserve"]

    def test_ties_go_to_the_limit(self):
        scored = self._scored(
            {**self.CALM, "end_pressure": 10},
            {**self.CALM, "max_ascend_speed": 9.5},
        )
        assert _categories_by_severity(scored) == ["rapid ascent", "low gas reserve"]

    def test_depth_and_air_consumption_never_pick_a_dive(self):
        assert "deep dive" not in ISSUE_RANK_KEY
        assert "high air consumption" not in ISSUE_RANK_KEY
        scored = self._scored({**self.CALM, "max_depth": 38, "sac_rate": 30})
        assert _categories_by_severity(scored) == []

    def test_both_ascent_tiers_share_one_family(self):
        assert ISSUE_FAMILY["rapid ascent"] == ISSUE_FAMILY["bolted to surface"]
        assert set(PICK_REASONS) == set(ISSUE_RANK_KEY)


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

    # At most one ascent card: the other slots go to other kinds of problem
    reasons = [d["pick_reason"] for d in data["top_problematic_dives"]]
    assert len(set(reasons)) == len(reasons)
    assert sum("ascent" in r or "bolt" in r for r in reasons) <= 1
    assert not any("Deepest dive with issues" in r or "air" in r for r in reasons)
    for dive in data["top_problematic_dives"]:
        assert dive["pick_class"] in ("limit", "margin")

    # Gauges say what kind of number they show
    kinds = {m["label"]: m["kind"] for m in data["metrics"]}
    assert kinds["Max Depth"] == "descriptive"
    assert kinds["SAC Rate"] == "descriptive"
    # NDL is a margin, not a limit: it has no gauge
    assert "Min NDL" not in kinds
    # Ascent is one gauge: the faster of the sustained rate and the surfacing
    assert list(kinds.values()) == ["descriptive"] * 4 + ["limit"] * 3
    assert "Gas Left on Exit" in kinds
    assert kinds["Fastest Ascent"] == "limit"
    assert kinds["Re-descents"] == "descriptive"
    assert kinds["Oxygen Pressure at Depth"] == "limit"
    depth = next(m for m in data["metrics"] if m["label"] == "Max Depth")
    assert depth["zone"] == "safe"

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
