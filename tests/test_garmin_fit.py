"""Garmin FIT dives and zip archives of dive logs."""

import io
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

import src.parsers.archive as archive_mod
from src.analysis.feature_engineering import extract_features
from src.api.main import app
from src.parsers import get_parser
from src.parsers.garmin_fit import NotAScubaDiveError, messages_to_frame

FIT = "tests/fixtures/garmin_descent_scuba.fit"
SPARSE = "tests/fixtures/sparse_subsurface_export.ssrf"
T0 = datetime(2026, 5, 1, 9, 0, tzinfo=UTC)


def _messages(sub_sport="single_gas_diving", records=None, **extra):
    """Decoded-FIT-shaped messages for one dive."""
    records = records or [
        {"timestamp": T0 + timedelta(seconds=s), "depth": d, "temperature": 24}
        for s, d in [(0, 0.5), (60, 10.0), (120, 18.0), (600, 18.0), (720, 5.0)]
    ]
    msgs = {
        "sport": [{"sport": "diving", "sub_sport": sub_sport}],
        "session": [{}],
        "record": records,
        "dive_summary": [{"reference_mesg": "session", "dive_number": 42}],
    }
    msgs.update(extra)
    return msgs


# --- The real Descent Mk2 file ----------------------------------------------


def test_real_descent_mk2_dive():
    df = get_parser(FIT).parse(FIT)
    assert df["dive_number"].unique().tolist() == ["529"]
    assert len(df) == 154
    assert df["time"].iloc[0] == 0 and df["time"].is_monotonic_increasing
    assert round(df["depth"].max(), 1) == 2.6
    assert df["temperature"].iloc[0] == 25

    features = extract_features(df).iloc[0]
    # This firmware logs no NDL and has no tank pod: not recorded, not zero.
    assert features["min_ndl"] != features["min_ndl"]  # NaN
    assert features["sac_rate"] != features["sac_rate"]
    assert not features["entered_deco"]


# --- Field mapping (synthetic messages) --------------------------------------


def test_ndl_is_converted_from_seconds_to_minutes():
    msgs = _messages()
    for r, ndl_s in zip(msgs["record"], [5940, 1800, 540, 300, 3000], strict=True):
        r["ndl_time"] = ndl_s
    df = messages_to_frame(msgs)
    assert df["ndl"].tolist() == [99.0, 30.0, 9.0, 5.0, 50.0]


def test_next_stop_depth_marks_deco():
    msgs = _messages()
    msgs["record"][3]["next_stop_depth"] = 6.0
    df = messages_to_frame(msgs)
    assert df["in_deco"].tolist() == [0, 0, 0, 1, 0]
    assert bool(extract_features(df)["entered_deco"].iloc[0])


def test_tank_pressure_follows_updates():
    msgs = _messages(
        tank_update=[
            {"timestamp": T0 + timedelta(seconds=50), "pressure": 200.0},
            {"timestamp": T0 + timedelta(seconds=590), "pressure": 120.0},
        ]
    )
    pressure = messages_to_frame(msgs)["pressure"].tolist()
    assert pressure[0] != pressure[0]  # before any reading: NaN
    assert pressure[1] == 200.0
    assert pressure[2] != pressure[2]  # >60 s since the last reading
    assert pressure[3] == 120.0


def test_sac_gps_and_dive_number():
    msgs = _messages(
        session=[{"start_position_lat": 2**30, "start_position_long": -(2**29)}]
    )
    msgs["dive_summary"][0]["avg_volume_sac"] = 17.5
    df = messages_to_frame(msgs)
    assert df["dive_number"].iloc[0] == "42"
    assert df["sac_rate"].iloc[0] == 17.5
    assert (df["latitude"].iloc[0], df["longitude"].iloc[0]) == (90.0, -45.0)
    assert df["dive_site_name"].iloc[0] == "N/A"


def test_unnumbered_dive_uses_local_start_time():
    msgs = _messages(activity=[{"local_timestamp": datetime(2026, 5, 1, 11, 0, 5)}])
    msgs["dive_summary"] = []
    assert messages_to_frame(msgs)["dive_number"].iloc[0] == "unnum_2026-05-01_110005"


@pytest.mark.parametrize("sub_sport", ["apnea_diving", "apnea_hunting", "generic"])
def test_non_scuba_activities_are_rejected(sub_sport):
    with pytest.raises(NotAScubaDiveError, match="not a scuba dive"):
        messages_to_frame(_messages(sub_sport=sub_sport))


def test_dive_without_depth_samples_is_rejected():
    with pytest.raises(NotAScubaDiveError, match="no depth samples"):
        messages_to_frame(_messages(records=[{"timestamp": T0, "depth": None}]))


# --- Zip archives -----------------------------------------------------------


def _zip(tmp_path: Path, members: dict[str, bytes], name="export.zip") -> str:
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as zf:
        for member, data in members.items():
            zf.writestr(member, data)
    return str(path)


def test_zip_combines_fit_and_subsurface_logs(tmp_path):
    fit = Path(FIT).read_bytes()
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as zf:
        zf.writestr("nested/other.fit", fit)  # same dive number: kept apart
    path = _zip(
        tmp_path,
        {
            "a.fit": fit,
            "log.ssrf": Path(SPARSE).read_bytes(),
            "notes.txt": b"not a dive log",
            "hike.fit": b"garbage",
            "part1.zip": inner.getvalue(),
        },
    )
    df = get_parser(path).parse(path)
    dives = set(df["dive_number"].astype(str))
    assert {"529", "529@other"} <= dives
    assert len(dives) == 2 + 7  # two FIT dives + the sparse fixture's seven


def test_zip_without_dives_fails_with_reasons(tmp_path):
    path = _zip(tmp_path, {"hike.fit": b"garbage", "readme.txt": b"x"})
    with pytest.raises(ValueError, match="No dives found.*hike.fit"):
        get_parser(path).parse(path)


def test_zip_member_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(archive_mod, "MAX_MEMBERS", 2)
    path = _zip(tmp_path, {f"{i}.fit": b"x" for i in range(3)})
    with pytest.raises(ValueError, match="too many files"):
        get_parser(path).parse(path)


def test_zip_uncompressed_size_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(archive_mod, "MAX_TOTAL_BYTES", 1000)
    path = _zip(tmp_path, {"big.fit": b"\0" * 5000})  # compresses to ~20 bytes
    with pytest.raises(ValueError, match="too large when uncompressed"):
        get_parser(path).parse(path)


# --- Upload endpoint --------------------------------------------------------


async def test_upload_fit_file():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/upload",
            files={"file": ("dive.fit", Path(FIT).read_bytes())},
        )
    assert resp.status_code == 200
    assert resp.json()["dive_numbers"] == ["529"]


async def test_upload_rejects_freedive_with_reason():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/upload", files={"file": ("dive.fit", b"garbage")}
        )
    assert resp.status_code == 400
    assert "Failed to parse file" in resp.json()["detail"]


# --- Single-dive mode -------------------------------------------------------


async def test_single_dive_upload_gets_single_mode(monkeypatch):
    """A one-dive log returns the dive detail and makes no LLM summary call."""
    import src.api.routes.dashboard as dashboard

    def no_llm(_):
        raise AssertionError("single-dive mode must not call Gemini for summaries")

    monkeypatch.setattr(dashboard, "_generate_dive_summaries", no_llm)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        sid = (
            await client.post(
                "/api/upload", files={"file": ("dive.fit", Path(FIT).read_bytes())}
            )
        ).json()["session_id"]
        data = (await client.get(f"/api/dashboard/{sid}")).json()
    assert data["mode"] == "single"
    assert data["top_problematic_dives"] == []
    single = data["single_dive"]
    assert single["dive_number"] == "529"
    assert len(single["profile"]) == 154
    assert single["duration_min"] > 0


def test_single_dive_profile_is_downsampled_and_ends_at_surface():
    from src.api.routes.dashboard import MAX_PROFILE_POINTS, _build_single_dive

    msgs = _messages(
        records=[
            {"timestamp": T0 + timedelta(seconds=s), "depth": min(s / 60, 20.0)}
            for s in range(0, 3001)
        ]
    )
    df = messages_to_frame(msgs)
    single = _build_single_dive(df, extract_features(df).iloc[0])
    assert len(single.profile) <= MAX_PROFILE_POINTS + 1
    assert single.profile[-1].time_s == 3000
