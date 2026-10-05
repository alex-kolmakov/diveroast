"""Deco judged by whether the stops were kept; technical dives plan theirs."""

import numpy as np
import pandas as pd
import pytest
from defusedxml.ElementTree import fromstring

from src.analysis.feature_engineering import extract_features
from src.tools.dive import deco_flags, dive_issues


def _deco_dive(
    *,
    stop_m: float | None = 3.0,
    hold_at: float = 3.0,
    surface_owing: bool = False,
    helium: float = 0.0,
    o2_spread: float = 0.0,
    mode: str = "OC",
    number: str = "1",
) -> pd.DataFrame:
    """30 m for 20 min (deco owed from minute 15), up to the stop, 5 min at
    ``hold_at`` while a ``stop_m`` stop is owed, then deco clears (unless
    ``surface_owing``) and the diver surfaces. 10 s samples."""
    t = np.arange(0, 33 * 60 + 1, 10.0)
    depth = np.select(
        [t < 60, t < 21 * 60, t < 24 * 60, t < 29 * 60, t < 33 * 60 - 30],
        [
            t / 60 * 30,
            30.0,
            30 - (t - 21 * 60) / 180 * (30 - hold_at),
            hold_at,
            hold_at,
        ],
        default=0.0,
    )
    owed = (t >= 15 * 60) & ((t < 29 * 60) | surface_owing)
    return pd.DataFrame(
        {
            "dive_number": number,
            "time": t,
            "depth": depth,
            "temperature": 25.0,
            "pressure": np.nan,
            "ndl": np.nan,
            "sac_rate": np.nan,
            "in_deco": owed.astype(float),
            "stop_depth": np.where(
                owed, stop_m if stop_m is not None else np.nan, np.nan
            ),
            "max_helium": helium,
            "o2_spread": o2_spread,
            "dive_mode": mode,
        }
    )


def _row(**kwargs) -> pd.Series:
    return extract_features(_deco_dive(**kwargs)).iloc[0]


def test_recreational_deco_with_stops_kept():
    row = _row()
    assert row["deco_minutes"] == pytest.approx(14, abs=0.2)
    assert row["missed_stop_minutes"] == 0 and row["surfaced_owing"] == 0
    flags = deco_flags(row)
    assert flags["recreational_deco"] and flags["stops_kept"]
    assert not flags["missed_stop"]
    issue = next(i for i in dive_issues(row) if "ENTERED DECOMPRESSION" in i)
    assert "stops were kept" in issue


def test_hovering_above_the_stop_is_missed():
    row = _row(stop_m=6.0, hold_at=3.5)  # 2.5 m above a 6 m stop for 5 min
    assert row["missed_stop_minutes"] == pytest.approx(5, abs=0.2)
    assert row["max_above_stop"] == pytest.approx(2.5)
    flags = deco_flags(row)
    assert flags["missed_stop"] and not flags["stops_kept"]
    assert any(i.startswith("MISSED DECO STOP") for i in dive_issues(row))


def test_surfacing_with_deco_owed_is_flagged_but_surface_time_isnt_missed():
    row = _row(surface_owing=True)
    assert row["surfaced_owing"] == 3.0
    assert row["missed_stop_minutes"] == 0  # time logged at the surface doesn't count
    assert any(i.startswith("SURFACED WITH DECO OWED") for i in dive_issues(row))


def test_stop_depth_not_recorded_claims_nothing():
    row = _row(stop_m=None)
    assert np.isnan(row["missed_stop_minutes"]) and np.isnan(row["surfaced_owing"])
    flags = deco_flags(row)
    assert not flags["missed_stop"] and not flags["stops_kept"]
    issue = next(i for i in dive_issues(row) if "ENTERED DECOMPRESSION" in i)
    assert "weren't recorded" in issue


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"helium": 30.0}, "trimix"),
        ({"o2_spread": 29.0}, "deco gas"),
        ({"mode": "CCR"}, "CCR"),
    ],
)
def test_technical_dives_plan_their_deco(kwargs, reason):
    row = _row(**kwargs)
    assert row["technical"] and reason in row["tech_reason"]
    assert not any("DECOMPRESSION" in i for i in dive_issues(row))


def test_a_nitrox_reset_is_not_a_deco_gas():
    """EAN28 -> EAN29 mid-dive (seen in a real recreational log)."""
    assert not _row(o2_spread=1.0)["technical"]


def test_technical_dive_still_owns_a_missed_stop():
    row = _row(helium=30.0, stop_m=6.0, hold_at=3.5)
    assert row["technical"]
    assert any(i.startswith("MISSED DECO STOP") for i in dive_issues(row))


# --- Dashboard ----------------------------------------------------------------


def test_dashboard_scores_adherence_not_deco_entry():
    from src.api.routes.dashboard import _compute_danger_score, _identify_issues

    kept = _row()
    tech = _row(helium=30.0)
    missed = _row(stop_m=6.0, hold_at=3.5)
    assert _compute_danger_score(tech) < _compute_danger_score(kept)
    assert _compute_danger_score(missed) > _compute_danger_score(kept)
    assert "low NDL" in _identify_issues(kept)
    assert "low NDL" not in _identify_issues(tech)
    assert "missed deco stop" in _identify_issues(missed)


# --- Parsers --------------------------------------------------------------------


def _ssrf_dive(
    cylinders: str, events: str, dc: str = "<divecomputer model='X'>"
) -> object:
    return fromstring(f"<dive number='1'>{cylinders}{dc}{events}</divecomputer></dive>")


def test_subsurface_gases_and_mode():
    from src.parsers.subsurface import gas_profile

    ean = "<cylinder o2='28.0%' /><cylinder o2='29.0%' />"
    reset = (
        "<event time='0:00 min' name='gaschange' cylinder='0' o2='28.0%' />"
        "<event time='21:27 min' name='gaschange' cylinder='1' o2='29.0%' />"
    )
    assert gas_profile(_ssrf_dive(ean, reset))["o2_spread"] == 1.0

    trimix = "<cylinder o2='21.0%' he='35.0%' /><cylinder o2='50.0%' />"
    old_style = (
        "<event time='0:00 min' type='11' value='21' name='gaschange' />"
        "<event time='40:00 min' type='11' value='50' name='gaschange' />"
    )
    gases = gas_profile(_ssrf_dive(trimix, old_style))
    assert gases == {
        "max_helium": 35.0,
        "gas_count": 2,
        "o2_spread": 29.0,
        "dive_mode": "OC",
    }

    ccr = gas_profile(
        _ssrf_dive("<cylinder o2='21.0%' />", "", "<divecomputer dctype='CCR'>")
    )
    assert ccr["dive_mode"] == "CCR"


def test_uddf_gases_mode_and_stop_depth():
    from src.parsers.uddf import parse_uddf_root

    root = fromstring(
        """<uddf xmlns="http://www.streit.cc/uddf/3.2/">
          <gasdefinitions>
            <mix id="tx"><o2>0.21</o2><he>0.35</he></mix>
            <mix id="ean50"><o2>0.50</o2><he>0.0</he></mix>
          </gasdefinitions>
          <profiledata><repetitiongroup><dive id="d1">
            <informationbeforedive><divenumber>7</divenumber></informationbeforedive>
            <samples>
              <waypoint><depth>0</depth><divetime>0</divetime><switchmix ref="tx"/>
                <divemode type="closedcircuit"/></waypoint>
              <waypoint><depth>40</depth><divetime>600</divetime></waypoint>
              <waypoint><depth>21</depth><divetime>1800</divetime><switchmix ref="ean50"/>
                <decostop kind="mandatory" decodepth="6" duration="120"/></waypoint>
              <waypoint><depth>6</depth><divetime>2400</divetime>
                <decostop kind="mandatory" decodepth="6" duration="60"/></waypoint>
              <waypoint><depth>0</depth><divetime>2700</divetime></waypoint>
            </samples>
          </dive></repetitiongroup></profiledata>
        </uddf>"""
    )
    df = parse_uddf_root(root)
    first = df.iloc[0]
    assert first["max_helium"] == 35.0 and first["o2_spread"] == 29.0
    assert first["dive_mode"] == "CCR"
    assert df["stop_depth"].tolist()[2:4] == [6.0, 6.0]


def test_garmin_rebreather_dives_are_accepted():
    """Garmin CCR dives arrive as sub_sport 63 and used to be rejected."""
    from datetime import UTC, datetime, timedelta

    from src.parsers.garmin_fit import messages_to_frame

    start = datetime(2026, 1, 1, tzinfo=UTC)
    messages = {
        "sport": [{"sub_sport": 63}],
        "session": [{}],
        "dive_gas": [
            {
                "message_index": 0,
                "oxygen_content": 18,
                "helium_content": 30,
                "status": "enabled",
            },
            {
                "message_index": 1,
                "oxygen_content": 50,
                "helium_content": 0,
                "status": "enabled",
            },
        ],
        "event": [{"event": "dive_gas_switched", "data": 1}],
        "record": [
            {
                "timestamp": start + timedelta(seconds=s),
                "depth": d,
                "next_stop_depth": n,
            }
            for s, d, n in [
                (0, 0, 0),
                (600, 50, 0),
                (1800, 21, 6),
                (2400, 6, 6),
                (2700, 0, 0),
            ]
        ],
    }
    df = messages_to_frame(messages)
    first = df.iloc[0]
    assert first["dive_mode"] == "CCR" and first["max_helium"] == 30.0
    assert first["o2_spread"] == 32.0
    assert df["stop_depth"].tolist()[2:4] == [6.0, 6.0]
