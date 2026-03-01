"""Tests for anomaly keyword building and RAG query augmentation.

Covers both DiverRoastAgent._build_anomaly_keywords() (agent path)
and _build_anomaly_keywords() in the MCP server module.
"""

from unittest.mock import MagicMock, patch

import pandas as pd

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_features(
    max_ascend_speed=5.0,
    min_ndl=10.0,
    sac_rate=15.0,
    max_depth=20.0,
    adverse_conditions=False,
) -> pd.DataFrame:
    """Build a minimal features DataFrame for one dive."""
    return pd.DataFrame(
        [
            {
                "dive_number": 1,
                "max_ascend_speed": max_ascend_speed,
                "min_ndl": min_ndl,
                "sac_rate": sac_rate,
                "max_depth": max_depth,
                "adverse_conditions": int(adverse_conditions),
            }
        ]
    )


# ---------------------------------------------------------------------------
# Agent path — DiverRoastAgent._build_anomaly_keywords()
# ---------------------------------------------------------------------------


def _make_agent(features_df=None):
    """Create a DiverRoastAgent with features pre-set (bypass set_dive_data)."""
    from src.agent.conversation import DiverRoastAgent

    agent = DiverRoastAgent.__new__(DiverRoastAgent)
    agent._client = None
    agent.history = []
    agent.dive_data = None
    agent.features = features_df
    return agent


def test_agent_keywords_no_dive_data():
    agent = _make_agent(features_df=None)
    assert agent._build_anomaly_keywords() == ""


def test_agent_keywords_empty_features():
    agent = _make_agent(features_df=pd.DataFrame())
    assert agent._build_anomaly_keywords() == ""


def test_agent_keywords_rapid_ascent():
    agent = _make_agent(_make_features(max_ascend_speed=15.0))
    kw = agent._build_anomaly_keywords()
    assert "rapid ascent decompression sickness" in kw


def test_agent_keywords_low_ndl():
    agent = _make_agent(_make_features(min_ndl=0.5))
    kw = agent._build_anomaly_keywords()
    assert "close to deco stop NDL almost zero recreational limit" in kw


def test_agent_keywords_high_sac():
    agent = _make_agent(_make_features(sac_rate=25.0))
    kw = agent._build_anomaly_keywords()
    assert "high air consumption SAC rate breathing" in kw


def test_agent_keywords_deep_dive():
    agent = _make_agent(_make_features(max_depth=35.0))
    kw = agent._build_anomaly_keywords()
    assert "deep diving incident" in kw


def test_agent_keywords_adverse_conditions_excluded():
    """Low star rating must NOT inject 'adverse conditions' into RAG — subjective signal."""
    agent = _make_agent(_make_features(adverse_conditions=True))
    kw = agent._build_anomaly_keywords()
    assert "adverse" not in kw.lower()
    assert "conditions" not in kw.lower()


def test_agent_keywords_clean_diver():
    """A diver with no anomalies should produce an empty keyword string."""
    agent = _make_agent(
        _make_features(
            max_ascend_speed=5.0,
            min_ndl=10.0,
            sac_rate=12.0,
            max_depth=20.0,
            adverse_conditions=False,
        )
    )
    assert agent._build_anomaly_keywords() == ""


def test_agent_keywords_multiple_anomalies():
    """Multiple thresholds exceeded → all keywords concatenated."""
    agent = _make_agent(
        _make_features(
            max_ascend_speed=20.0,
            min_ndl=0.3,
            sac_rate=30.0,
            max_depth=40.0,
        )
    )
    kw = agent._build_anomaly_keywords()
    assert "rapid ascent decompression sickness" in kw
    assert "close to deco stop" in kw
    assert "high air consumption SAC rate breathing" in kw
    assert "deep diving incident" in kw


# ---------------------------------------------------------------------------
# Agent path — _execute_tool() query augmentation
# ---------------------------------------------------------------------------


def test_execute_tool_augments_search_query():
    """_execute_tool() must append anomaly keywords to RAG search queries."""

    agent = _make_agent(_make_features(max_ascend_speed=15.0))

    captured_args = {}

    def fake_search_dan_incidents(**kwargs):
        captured_args.update(kwargs)
        return "mocked result"

    fake_fc = MagicMock()
    fake_fc.name = "search_dan_incidents"
    fake_fc.args = {"query": "buoyancy control"}

    with patch(
        "src.agent.conversation.TOOL_FUNCTIONS",
        {"search_dan_incidents": fake_search_dan_incidents},
    ):
        agent._execute_tool(fake_fc)

    assert "buoyancy control" in captured_args["query"]
    assert "rapid ascent decompression sickness" in captured_args["query"]


def test_execute_tool_no_augmentation_without_anomalies():
    """With a clean dive profile, the query must pass through unchanged."""

    agent = _make_agent(
        _make_features(
            max_ascend_speed=5.0, min_ndl=10.0, sac_rate=12.0, max_depth=20.0
        )
    )

    captured_args = {}

    def fake_search(**kwargs):
        captured_args.update(kwargs)
        return "mocked"

    fake_fc = MagicMock()
    fake_fc.name = "search_dan_incidents"
    fake_fc.args = {"query": "buoyancy control"}

    with patch(
        "src.agent.conversation.TOOL_FUNCTIONS",
        {"search_dan_incidents": fake_search},
    ):
        agent._execute_tool(fake_fc)

    assert captured_args["query"] == "buoyancy control"


# ---------------------------------------------------------------------------
# MCP server path — _build_anomaly_keywords()
# ---------------------------------------------------------------------------


def test_mcp_keywords_no_dive_data():
    import src.mcp.server as mcp_server

    with patch.object(mcp_server, "_dive_data", None):
        assert mcp_server._build_anomaly_keywords() == ""


def test_mcp_keywords_rapid_ascent():
    import src.mcp.server as mcp_server

    features_df = _make_features(max_ascend_speed=18.0)
    fake_data = MagicMock()

    with (
        patch.object(mcp_server, "_dive_data", fake_data),
        patch("src.mcp.server.extract_features", return_value=features_df),
    ):
        kw = mcp_server._build_anomaly_keywords()

    assert "rapid ascent decompression sickness" in kw


def test_mcp_keywords_feature_extraction_error():
    """If extract_features raises, _build_anomaly_keywords must return '' silently."""
    import src.mcp.server as mcp_server

    fake_data = MagicMock()

    with (
        patch.object(mcp_server, "_dive_data", fake_data),
        patch("src.mcp.server.extract_features", side_effect=RuntimeError("oops")),
    ):
        assert mcp_server._build_anomaly_keywords() == ""
