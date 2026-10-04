"""Thermal exposure: cold and warm water, measured from in-water readings."""

import numpy as np
import pandas as pd
import pytest

from src.analysis.feature_engineering import extract_features
from src.tools.dive import anomaly_queries, dive_issues, thermal_flags


def _dive(
    minutes: float,
    bottom_temp: float | None,
    stop_temp: float | None = None,
    surface_temp: float | None = None,
    sparse: bool = False,
    number: str = "1",
) -> pd.DataFrame:
    """A square profile: 1 min down to 20 m, bottom, 1 min up, 3 min at 5 m.

    Temperatures go in like a computer logs them: the surface reading first
    (air or sun), then the bottom, then the stop. ``sparse`` keeps only the
    readings where the temperature changes, as Subsurface does.
    """
    t = np.arange(0, minutes * 60 + 1, 10.0)
    end = t[-1]
    depth = np.select(
        [t < 60, t < end - 240, t < end - 180, t < end - 10],
        [t / 60 * 20, 20.0, 20 - (t - (end - 240)) / 60 * 15, 5.0],
        default=0.0,
    )
    temp = np.full(len(t), np.nan if bottom_temp is None else bottom_temp)
    if surface_temp is not None:
        temp[t < 20] = surface_temp
    if stop_temp is not None:
        temp[t >= end - 180] = stop_temp
    frame = pd.DataFrame(
        {
            "dive_number": number,
            "time": t,
            "depth": depth,
            "temperature": temp,
            "pressure": np.nan,
            "ndl": np.nan,
            "sac_rate": np.nan,
        }
    )
    if sparse:
        changed = frame["temperature"].ne(frame["temperature"].shift())
        frame.loc[~changed, "temperature"] = np.nan
    return frame


def _features(*dives: pd.DataFrame) -> pd.DataFrame:
    return extract_features(pd.concat(dives, ignore_index=True))


def test_cold_minutes_count_in_water_time_below_10c():
    row = _features(_dive(40, bottom_temp=6.0, stop_temp=12.0)).iloc[0]
    assert row["dive_minutes"] == pytest.approx(40, abs=0.2)
    # Below 10 °C from the first in-water reading until the stop phase
    assert row["cold_minutes"] == pytest.approx(40 - 3 - 0.3, abs=1)
    assert row["water_min_temp"] == 6.0
    assert row["stop_temp"] == pytest.approx(12.0)
    flags = thermal_flags(row)
    assert flags == {"prolonged_cold": True, "cold_stops": False, "long_warm": False}


def test_sparse_readings_hold_until_the_next_one():
    dense = _features(_dive(40, bottom_temp=6.0, stop_temp=12.0)).iloc[0]
    sparse = _features(_dive(40, bottom_temp=6.0, stop_temp=12.0, sparse=True)).iloc[0]
    for col in ("cold_minutes", "water_min_temp", "stop_temp"):
        assert sparse[col] == pytest.approx(dense[col])


def test_cold_stops_are_flagged():
    row = _features(_dive(25, bottom_temp=12.0, stop_temp=8.0)).iloc[0]
    assert thermal_flags(row)["cold_stops"]
    assert any("COLD DECOMPRESSION" in issue for issue in dive_issues(row))


def test_surface_and_sun_readings_do_not_count():
    """A 36 °C reading on the boat must not make a 27 °C dive "warm"."""
    row = _features(_dive(70, bottom_temp=27.0, surface_temp=36.0)).iloc[0]
    assert row["water_min_temp"] == 27.0
    assert not thermal_flags(row)["long_warm"]


def test_long_warm_dive_is_flagged_only_when_long():
    long_warm = _features(_dive(70, bottom_temp=30.0, number="1")).iloc[0]
    short_warm = _features(_dive(40, bottom_temp=30.0, number="2")).iloc[0]
    assert thermal_flags(long_warm)["long_warm"]
    assert not thermal_flags(short_warm)["long_warm"]
    assert any("dehydration" in issue for issue in dive_issues(long_warm))


def test_no_temperature_means_no_thermal_claims():
    row = _features(_dive(70, bottom_temp=None)).iloc[0]
    for col in ("water_min_temp", "cold_minutes", "stop_temp"):
        assert np.isnan(row[col])
    assert row["dive_minutes"] == pytest.approx(70, abs=0.2)
    assert not any(thermal_flags(row).values())


def test_thermal_anomalies_search_dan():
    features = _features(
        _dive(40, bottom_temp=6.0, stop_temp=8.0, number="1"),
        _dive(70, bottom_temp=30.0, number="2"),
    )
    queries = " | ".join(anomaly_queries(features))
    assert "hypothermia" in queries
    assert "cold during decompression" in queries
    assert "dehydration" in queries


def test_the_roast_context_carries_the_flags():
    from src.agent.conversation import DiverRoastAgent

    agent = DiverRoastAgent()
    agent.set_dive_data(
        pd.concat(
            [
                _dive(40, bottom_temp=6.0, stop_temp=8.0, number="1"),
                _dive(70, bottom_temp=30.0, number="2"),
            ],
            ignore_index=True,
        )
    )
    seed = agent.history[0].parts[0].text
    assert "PROLONGED COLD" in seed and "COLD STOPS 8.0°C" in seed
    assert "LONG WARM DIVE (water never below 30.0°C)" in seed
    assert (
        "1 dives with prolonged cold, 1 with cold stops, 1 long warm-water dives"
        in seed
    )
