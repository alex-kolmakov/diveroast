"""Regression tests for the P1 findings: values, not shapes.

Each test pins a bug the old suite couldn't see because it asserted column
presence and dtype. The sparse fixture (see fixtures/make_sparse_fixture.py)
has the gaps real logs have: unnumbered, unrated, no pressure/SAC, no NDL.
"""

import logging
import math
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from google.genai import types
from httpx import ASGITransport, AsyncClient

import src.api.dependencies as deps
import src.mcp.server as mcp_server
from src.agent.conversation import DiverRoastAgent
from src.analysis.feature_engineering import extract_features
from src.api.main import app
from src.api.models import DashboardResponse
from src.config import settings
from src.parsers import get_parser
from src.rag.ingestion import RowFloorError, check_row_floor
from src.storage.snapshots import LocalSnapshotStore
from src.tools import dive

SPARSE = "tests/fixtures/sparse_subsurface_export.ssrf"
FULL = "tests/fixtures/anonymized_subsurface_export.ssrf"


@pytest.fixture(scope="module")
def sparse_df() -> pd.DataFrame:
    return get_parser(SPARSE).parse(SPARSE)


@pytest.fixture(scope="module")
def full_df() -> pd.DataFrame:
    return get_parser(FULL).parse(FULL)


def _row(features: pd.DataFrame, dive_number: str) -> pd.Series:
    return features[features["dive_number"].astype(str) == dive_number].iloc[0]


def _unnumbered(df: pd.DataFrame) -> list[str]:
    return sorted(d for d in df["dive_number"].unique() if str(d).startswith("unnum_"))


# --- Step 9 named tests -----------------------------------------------------


def test_missing_pressure_yields_none_sac(sparse_df):
    features = extract_features(sparse_df)
    row = _row(features, "22")
    assert math.isnan(row["sac_rate"])
    assert math.isnan(row["avg_pressure"])
    assert math.isnan(row["max_pressure"])

    summary = dive.get_dive_summary(sparse_df, "22", features)
    assert "SAC Rate: not recorded" in summary
    profile = dive.analyze_dive_profile(sparse_df, "22", features)
    assert "HIGH AIR CONSUMPTION" not in profile
    assert "SAC rate" in profile.split("Not recorded for this dive:")[1]


def test_star_rating_is_ignored(sparse_df):
    """Diver-entered star ratings are not read or reported anywhere.

    Most dive computer exports don't have them; the app only uses signals any
    computer produces.
    """
    assert "rating" not in sparse_df.columns
    features = extract_features(sparse_df)
    assert not {"rating", "adverse_conditions"} & set(features.columns)
    texts = [dive.analyze_all_dives(sparse_df), dive.list_dives(sparse_df)]
    for dn in features["dive_number"].astype(str):
        texts.append(dive.get_dive_summary(sparse_df, dn, features))
        texts.append(dive.analyze_dive_profile(sparse_df, dn, features))
    for text in texts:
        assert not re.search(r"rating|rated|adverse", text, re.IGNORECASE)


@pytest.mark.parametrize("fixture,dive_number", [(FULL, "80"), (SPARSE, "22")])
def test_single_and_aggregate_agree(fixture, dive_number):
    """get_dive_summary, analyze_dive_profile and the whole-log view agree on SAC."""
    df = get_parser(fixture).parse(fixture)
    whole_log = extract_features(df)
    single_dive = extract_features(dive.filter_dive(df, dive_number))

    assert math.isnan(_row(whole_log, dive_number)["sac_rate"])
    assert math.isnan(_row(single_dive, dive_number)["sac_rate"])
    assert "SAC Rate: not recorded" in dive.get_dive_summary(df, dive_number)
    assert (
        "SAC rate"
        in dive.analyze_dive_profile(df, dive_number).split(
            "Not recorded for this dive:"
        )[1]
    )


def test_single_and_aggregate_agree_on_measured_values(sparse_df):
    whole_log = extract_features(sparse_df)
    for dn in whole_log["dive_number"].astype(str):
        single = extract_features(dive.filter_dive(sparse_df, dn))
        pd.testing.assert_series_equal(
            _row(whole_log, dn).drop("dive_number"),
            _row(single, dn).drop("dive_number"),
            check_names=False,
        )


@pytest.fixture
def api(tmp_path):
    store = LocalSnapshotStore(str(tmp_path))
    app.dependency_overrides[deps.get_snapshot_store] = lambda: store
    with patch(
        "src.api.routes.dashboard._generate_dive_summaries",
        side_effect=lambda dives: [f"summary {d['dive_number']}" for d in dives],
    ) as summaries:
        yield (
            AsyncClient(transport=ASGITransport(app=app), base_url="http://test"),
            summaries,
            store,
        )
    app.dependency_overrides.clear()


async def _upload(client, path=SPARSE, session_id=None):
    with open(path, "rb") as f:
        data = {"session_id": session_id} if session_id else {}
        resp = await client.post(
            "/api/upload", files={"file": ("export.ssrf", f.read())}, data=data
        )
    assert resp.status_code == 200
    return resp.json()["session_id"]


async def test_share_id_is_read_only(api):
    client, _, _ = api
    async with client:
        sid = await _upload(client)
        dash = (await client.get(f"/api/dashboard/{sid}")).json()
        share_id = dash["share_id"]
        assert share_id and share_id != sid

        # The public link works and doesn't leak the session ID.
        shared = await client.get(f"/api/shared/{share_id}")
        assert shared.status_code == 200
        assert shared.json()["session_id"] is None
        assert sid not in shared.text

        # The share ID grants nothing else.
        chat = await client.post(
            "/api/chat", json={"message": "hi", "session_id": share_id}
        )
        assert chat.status_code == 404
        assert (await client.get(f"/api/dashboard/{share_id}")).status_code == 404
        patch_resp = await client.patch(
            f"/api/sessions/{share_id}/roast", json={"roast": "pwned"}
        )
        assert patch_resp.status_code in (404, 405)
        hijack = await _upload(client, path=FULL, session_id=share_id)
        assert hijack not in (share_id, sid)

        # The owner's session and shared page are unchanged.
        assert deps.get_session(sid).dive_data["dive_number"].nunique() == 7
        again = (await client.get(f"/api/shared/{share_id}")).json()
        assert again["aggregate_stats"]["total_dives"] == 7
        assert again["roast_summary"] is None


async def test_dashboard_summaries_are_generated_once(api):
    client, summaries, _ = api
    async with client:
        sid = await _upload(client)
        first = (await client.get(f"/api/dashboard/{sid}")).json()
        second = (await client.get(f"/api/dashboard/{sid}")).json()
        assert summaries.call_count == 1
        assert first == second
        await client.get(f"/api/shared/{first['share_id']}")
        assert summaries.call_count == 1


async def test_roast_is_saved_server_side(api):
    client, _, store = api
    async with client:
        sid = await _upload(client)
        share_id = (await client.get(f"/api/dashboard/{sid}")).json()["share_id"]

        agent = deps.get_session(sid)

        async def fake_stream(message):
            agent.last_prompt = "sharp-ironic-analyst (v4)"
            agent.last_sources = [{"title": "Ascent", "url": "https://dan.org/a"}]
            yield "Your ascents "
            yield "need work."

        with patch.object(agent, "chat_stream", fake_stream):
            resp = await client.post(
                "/api/chat", json={"message": "roast me", "session_id": sid}
            )
        assert "event: sources" in resp.text
        assert "https://dan.org/a" in resp.text

        shared = (await client.get(f"/api/shared/{share_id}")).json()
        assert shared["roast_summary"] == "Your ascents need work."
        assert shared["roast_prompt"] == "sharp-ironic-analyst (v4)"


async def test_pre_p1_snapshot_still_renders(api, tmp_path):
    """Snapshots saved before this change (session_id inside, no new fields) still load."""
    client, _, store = api
    old = {
        "session_id": "old-session-id",
        "aggregate_stats": {
            "total_dives": 1,
            "avg_max_depth": 20.0,
            "avg_sac_rate": 15.0,
            "avg_max_ascend_speed": 8.0,
            "dives_with_adverse_conditions": 0,
        },
        "metrics": [
            {
                "label": "Max Depth",
                "unit": "m",
                "min_val": 20.0,
                "max_val": 20.0,
                "avg_val": 20.0,
                "worst_val": 20.0,
                "safe_upper": 18.0,
                "warning_upper": 30.0,
                "zone": "warning",
                "per_dive": [{"dive_number": "1", "value": 20.0, "zone": "warning"}],
            }
        ],
        "all_dives": [],
        "top_problematic_dives": [],
        "diver_profile": {
            "water_types": [],
            "regions": [],
            "experience_level": "beginner",
            "dive_sites": [],
            "temp_exposure": {},
        },
        "roast_summary": "old roast",
    }
    (tmp_path / "old-session-id.json").write_text(
        DashboardResponse.model_validate(old).model_dump_json()
    )
    async with client:
        resp = await client.get("/api/shared/old-session-id")
    assert resp.status_code == 200
    assert resp.json()["session_id"] is None
    assert resp.json()["roast_summary"] == "old roast"


def test_full_rebuild_row_floor():
    with pytest.raises(RowFloorError):
        check_row_floor(322, previous_count=17_107)  # the production symptom
    with pytest.raises(RowFloorError):
        check_row_floor(500, previous_count=None)  # below RAG_MIN_ROWS
    check_row_floor(17_000, previous_count=17_107)
    check_row_floor(settings.RAG_MIN_ROWS, previous_count=None)


def test_full_rebuild_resets_dlt_state():
    """A full rebuild must drop dlt's incremental cursor, not just the table."""
    import src.rag.ingestion as ingestion

    table = MagicMock()
    table.count_rows.return_value = 20_000
    db = MagicMock()
    db.table_names.return_value = [settings.LANCEDB_TABLE_NAME]
    db.open_table.return_value = table
    pipeline = MagicMock()
    with (
        patch.object(ingestion.lancedb, "connect", return_value=db),
        patch.object(ingestion.dlt, "pipeline", return_value=pipeline),
        patch.object(ingestion, "wordpress_rest_api_source", MagicMock()),
        patch.object(ingestion, "lancedb_adapter", lambda data, embed: data),
    ):
        ingestion.run_pipeline(full_replace=True)
        assert pipeline.run.call_args.kwargs["refresh"] == "drop_sources"
        ingestion.run_pipeline(full_replace=False)
        assert pipeline.run.call_args.kwargs.get("refresh") is None


# --- Step 5: the unnumbered-dive fix ---------------------------------------


def test_analyze_all_dives_handles_unnumbered_dives(sparse_df):
    unnumbered = _unnumbered(sparse_df)
    assert len(unnumbered) == 2
    # Make an unnumbered dive the fastest ascent so it lands in the offender list.
    df = sparse_df.copy()
    features = extract_features(df)
    features.loc[features["dive_number"] == unnumbered[0], "max_ascend_speed"] = 99.0
    text = dive.analyze_all_dives(df, features)
    assert f"#{unnumbered[0]}" in text
    assert "99.0 m/min" in text


def test_unnumbered_dives_keep_their_trip(sparse_df):
    for dn in _unnumbered(sparse_df):
        trips = dive.filter_dive(sparse_df, dn)["trip_name"].unique()
        assert list(trips) == ["Sparse trip"]


def test_tool_errors_are_logged(caplog):
    agent = DiverRoastAgent()
    fc = MagicMock()
    fc.name = "search_dan_incidents"
    fc.args = {"query": "x"}

    def boom(**kwargs):
        raise RuntimeError("lancedb down")

    with (
        patch("src.agent.conversation.TOOL_FUNCTIONS", {"search_dan_incidents": boom}),
        caplog.at_level(logging.ERROR),
    ):
        result = agent._execute_tool(fc)
    assert "Tool error" in result
    assert "search_dan_incidents failed" in caplog.text


# --- Step 2/4: NDL, deco and coverage --------------------------------------


def test_dive_without_ndl_has_no_ndl_sentence(sparse_df):
    features = extract_features(sparse_df)
    assert math.isnan(_row(features, "24")["min_ndl"])
    text = dive.analyze_dive_profile(sparse_df, "24", features)
    before_coverage = text.split("Data coverage:")[0]
    assert "LOW NDL" not in before_coverage
    assert not re.search(r"NDL[^.\n]*\d", before_coverage)


def test_computer_flagged_deco_is_its_own_signal(sparse_df):
    features = extract_features(sparse_df)
    assert bool(_row(features, "77")["entered_deco"])
    assert features["entered_deco"].sum() == 1
    assert "ENTERED DECOMPRESSION" in dive.analyze_dive_profile(
        sparse_df, "77", features
    )


def test_coverage_line_counts_recorded_metrics(sparse_df):
    line = dive.coverage_line(extract_features(sparse_df))
    assert "SAC available for 6/7 dives" in line
    assert "NDL for 6/7" in line
    # Only #77 and one unnumbered dive have pressure samples in the source
    # fixture; the others' SAC comes from cylinder start/end pressures.
    assert "tank pressure for 2/7" in line
    for text in (
        dive.analyze_all_dives(sparse_df),
        dive.list_dives(sparse_df),
        dive.get_dive_summary(sparse_df, "20"),
        dive.analyze_dive_profile(sparse_df, "20"),
    ):
        assert "Data coverage:" in text


def test_context_seed_leaves_out_unrecorded_metrics(sparse_df):
    agent = DiverRoastAgent()
    agent.set_dive_data(sparse_df)
    seed = agent.history[0].parts[0].text
    line_22 = next(line for line in seed.splitlines() if line.startswith("  #22 "))
    assert "SAC" not in line_22
    line_24 = next(line for line in seed.splitlines() if line.startswith("  #24 "))
    assert "NDL" not in line_24
    assert "nan" not in seed.lower()
    assert "[ADVERSE]" not in seed


def test_anomaly_keywords_ignore_unrecorded_ndl():
    features = pd.DataFrame(
        {
            "max_ascend_speed": [5.0, 5.0],
            "min_ndl": [float("nan"), 30.0],
            "entered_deco": [False, False],
            "sac_rate": [float("nan"), 15.0],
            "max_depth": [20.0, 20.0],
        }
    )
    assert dive.build_anomaly_keywords(features) == ""


# --- Step 6: bounded agent loop ---------------------------------------------


def test_agent_loop_is_bounded():
    agent = DiverRoastAgent()
    call = types.FunctionCall(name="list_dives", args={})
    looping = types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(
                    role="model", parts=[types.Part(function_call=call)]
                )
            )
        ]
    )
    client = MagicMock()
    client.models.generate_content.return_value = looping
    agent._client = client

    prompt = MagicMock(prompt="p", label="test", version=0, phoenix_version_id=None)
    agent._run_turn("roast me", prompt)

    calls = client.models.generate_content.call_args_list
    assert len(calls) == settings.AGENT_MAX_STEPS + 1
    last_config = calls[-1].kwargs["config"]
    assert (
        last_config.tool_config.function_calling_config.mode
        == types.FunctionCallingConfigMode.NONE
    )
    assert calls[0].kwargs["config"].temperature == settings.AGENT_TOOL_TEMPERATURE


# --- Step 3/10: sessions and MCP path checks --------------------------------


def test_unknown_session_id_is_not_adopted():
    sid, _ = deps.get_or_create_session("attacker-chosen-id")
    assert sid != "attacker-chosen-id"


def test_sessions_expire(monkeypatch):
    sid, _ = deps.get_or_create_session()
    monkeypatch.setattr(settings, "SESSION_TTL_SECONDS", -1)
    assert deps.get_session(sid) is None


def test_mcp_rejects_sibling_of_allowed_root(tmp_path, monkeypatch):
    allowed = tmp_path / "logs"
    allowed.mkdir()
    sibling = tmp_path / "logs-evil"
    sibling.mkdir()
    (sibling / "x.ssrf").write_text("<divelog/>")
    monkeypatch.setenv("DIVEROAST_MCP_ALLOWED_ROOTS", str(allowed))
    result = mcp_server.parse_dive_log(str(sibling / "x.ssrf"))
    assert result.startswith("Error: file_path must be within")


def test_mcp_parses_file_inside_allowed_root(tmp_path, monkeypatch):
    monkeypatch.setenv("DIVEROAST_MCP_ALLOWED_ROOTS", str(tmp_path))
    target = tmp_path / "log.ssrf"
    target.write_bytes(Path(SPARSE).read_bytes())
    try:
        result = mcp_server.parse_dive_log(str(target))
        assert result.startswith("Parsed 7 dives")
        assert "Data coverage:" in result
    finally:
        mcp_server._dive_data = None


def test_upload_parser_rejects_entity_expansion(tmp_path):
    bomb = tmp_path / "bomb.ssrf"
    bomb.write_text(
        '<?xml version="1.0"?><!DOCTYPE d [<!ENTITY a "aaaa">'
        '<!ENTITY b "&a;&a;&a;&a;">]><divelog><dives>&b;</dives></divelog>'
    )
    with pytest.raises(Exception, match="(?i)entit"):
        get_parser(str(bomb)).parse(str(bomb))
