"""Profile shape and gas: re-descents, surfacings, safety stop, reserve, thermocline."""

import numpy as np
import pandas as pd
import pytest

from src.analysis.feature_engineering import count_redescents, extract_features


def _dive(depths, step: float = 10.0, **columns) -> pd.DataFrame:
    """One dive from a depth trace sampled every ``step`` seconds."""
    frame = pd.DataFrame(
        {
            "dive_number": "1",
            "time": np.arange(len(depths)) * step,
            "depth": np.asarray(depths, dtype=float),
            "temperature": np.nan,
            "pressure": np.nan,
            "ndl": np.nan,
            "sac_rate": np.nan,
        }
    )
    for name, values in columns.items():
        frame[name] = values
    return frame


def _square(bottom: float = 20.0, bottom_samples: int = 60, stop_samples: int = 18):
    """Down at 12 m/min, a bottom phase, up at 6 m/min, a stop at 5 m, surface."""
    descent = list(np.arange(0, bottom, 2.0))
    ascent = list(np.arange(bottom, 5.0, -1.0))
    return [
        *descent,
        *[bottom] * bottom_samples,
        *ascent,
        *[5.0] * stop_samples,
        2.5,
        0.0,
    ]


def _row(depths, **columns) -> pd.Series:
    return extract_features(_dive(depths, **columns)).iloc[0]


def test_redescents_count_sawtooth_teeth_not_reef_wobbles():
    assert count_redescents(np.array([0, 10, 20, 20, 12, 5, 0.0])) == 0
    assert count_redescents(np.array([0, 20, 12, 20, 12, 20, 5, 0.0])) == 2
    assert count_redescents(np.array([0, 20, 17, 20, 17, 20, 0.0])) == 0


def test_clean_square_profile_has_nothing_to_flag():
    row = _row(_square())
    assert row["safety_stop_minutes"] == pytest.approx(3.0, abs=0.4)
    assert row["redescents"] == 0
    assert row["mid_dive_surfacings"] == 0
    assert row["max_descent_speed"] == pytest.approx(12.0)


def test_skipped_safety_stop_is_measured_short():
    assert _row(_square(stop_samples=2))["safety_stop_minutes"] < 1


def test_safety_stop_not_measured_when_the_log_ends_underwater():
    truncated = _square()[:-2]  # the trace stops at 5 m
    assert np.isnan(_row(truncated)["safety_stop_minutes"])


def test_no_safety_stop_expected_on_a_shallow_dive():
    row = _row([0, 3, 6, 6, 6, 6, 6, 3, 0])
    assert np.isnan(row["safety_stop_minutes"])


def test_surfacing_and_going_back_down_is_counted():
    row = _row([0, 6, 12, 12, 6, 0, 0, 6, 12, 12, 6, 0])
    assert row["mid_dive_surfacings"] == 1
    assert row["redescents"] == 1


def test_bobbing_at_the_surface_before_the_dive_is_not_a_surfacing():
    row = _row([0, 1.5, 0.5, 1.5, 0.5, 6, 12, 12, 6, 0])
    assert row["mid_dive_surfacings"] == 0


def test_end_pressure_is_the_last_reading_underwater():
    depths = _square()
    pressure = np.full(len(depths), np.nan)
    pressure[0] = 200
    pressure[-4] = 42  # still at the stop
    pressure[-1] = 3  # on the boat, regulator purged
    assert _row(depths, pressure=pressure)["end_pressure"] == 42


def test_no_tank_pod_leaves_end_pressure_unrecorded():
    assert np.isnan(_row(_square())["end_pressure"])


def test_sensor_cooling_from_air_is_not_a_thermocline():
    # 30°C on the boat, settling to 20°C water while the diver sits at depth.
    depths = _square()
    temp = np.full(len(depths), np.nan)
    for i, value in enumerate([30, 28, 26, 24, 22, 21, 20]):
        temp[i * 3] = value
    row = _row(depths, temperature=temp)
    assert row["max_temp"] - row["min_temp"] == 10
    assert row["temp_gradient"] == 0


def test_thermocline_is_the_gap_between_shallows_and_bottom():
    depths = _square(bottom=25.0)
    temp = [18.0 if d >= 20 else 26.0 for d in depths]
    assert _row(depths, temperature=temp)["temp_gradient"] == 8


def test_thermocline_not_measured_without_temperature():
    assert np.isnan(_row(_square())["temp_gradient"])
